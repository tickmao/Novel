#!/usr/bin/env python3
"""
书源自动补充主控制器
- 整合智能恢复、外部收集、增强评分等组件
- 实现完整的自动补充流程
- 维持1000个高质量书源
"""

import json
import asyncio
import argparse
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple, Optional

# 导入其他组件
sys.path.append(str(Path(__file__).parent))

try:
    from smart_recovery import SmartRecovery
    from source_collector import SourceCollector
    from enhanced_scoring import EnhancedScoring
except ImportError as e:
    print(f"导入组件失败: {e}")
    print("请确保所有组件脚本都在同一目录下")
    exit(1)

# 配置常量
TARGET_SOURCES = 1000
MIN_SOURCES_TRIGGER = 800
EMERGENCY_TRIGGER = 700
MIN_SCORE_THRESHOLD = 35
MAX_PER_DOMAIN = 2

class AutoSupplement:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.sources_dir = base_dir / "sources/legado"
        self.scripts_dir = base_dir / "scripts"
        self.temp_dir = base_dir / "temp"
        self.temp_dir.mkdir(exist_ok=True)

        # 文件路径
        self.main_sources_file = self.sources_dir / "full.json"
        self.backup_file = self.sources_dir / "full.backup.json"
        self.invalid_sources_file = self.temp_dir / "invalid_sources.json"
        self.error_report_file = self.temp_dir / "validation_report.json"
        self.history_file = self.temp_dir / "source_history.json"

        # 统计信息
        self.supplement_stats = {
            'initial_count': 0,
            'recovered_count': 0,
            'collected_count': 0,
            'final_count': 0,
            'filtered_adult': 0,
            'filtered_low_score': 0
        }

    def load_current_sources(self) -> List[dict]:
        """加载当前书源"""
        if not self.main_sources_file.exists():
            return []

        with open(self.main_sources_file, 'r', encoding='utf-8') as f:
            return json.load(f)

    def backup_current_sources(self, sources: List[dict]):
        """备份当前书源"""
        with open(self.backup_file, 'w', encoding='utf-8') as f:
            json.dump(sources, f, ensure_ascii=False, indent=2)
        print(f"已备份到: {self.backup_file}")

    async def validate_sources(self, sources: List[dict]) -> Tuple[List[dict], List[dict], Dict]:
        """验证书源有效性"""
        print("验证书源有效性...")

        # 调用现有的validate.py脚本
        temp_input = self.temp_dir / "temp_sources.json"
        temp_valid = self.temp_dir / "temp_valid.json"
        temp_invalid = self.temp_dir / "temp_invalid.json"
        temp_report = self.temp_dir / "temp_report.json"

        # 保存临时文件
        with open(temp_input, 'w', encoding='utf-8') as f:
            json.dump(sources, f, ensure_ascii=False, indent=2)

        # 运行验证脚本
        cmd = [
            sys.executable, str(self.scripts_dir / "validate.py"),
            "--input", str(temp_input),
            "--output", str(temp_valid),
            "--invalid", str(temp_invalid),
            "--report", str(temp_report),
            "--timeout", "10"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"验证脚本执行失败: {result.stderr}")
            return sources, [], {}

        # 读取结果
        valid_sources = []
        invalid_sources = []
        errors = {}

        if temp_valid.exists():
            with open(temp_valid, 'r', encoding='utf-8') as f:
                valid_sources = json.load(f)

        if temp_invalid.exists():
            with open(temp_invalid, 'r', encoding='utf-8') as f:
                invalid_sources = json.load(f)

        if temp_report.exists():
            with open(temp_report, 'r', encoding='utf-8') as f:
                report = json.load(f)
                errors = report.get('errors', {})

        # 清理临时文件
        for temp_file in [temp_input, temp_valid, temp_invalid, temp_report]:
            if temp_file.exists():
                temp_file.unlink()

        return valid_sources, invalid_sources, errors

    async def smart_recovery_process(self, invalid_sources: List[dict], errors: Dict) -> List[dict]:
        """智能恢复失效书源"""
        if not invalid_sources:
            return []

        print(f"\n=== 智能恢复 {len(invalid_sources)} 个失效书源 ===")

        # 保存失效书源和错误报告
        with open(self.invalid_sources_file, 'w', encoding='utf-8') as f:
            json.dump(invalid_sources, f, ensure_ascii=False, indent=2)

        error_report = {
            'timestamp': datetime.now().isoformat(),
            'errors': errors
        }
        with open(self.error_report_file, 'w', encoding='utf-8') as f:
            json.dump(error_report, f, ensure_ascii=False, indent=2)

        # 执行智能恢复
        recovery = SmartRecovery(timeout=15)
        recovered, still_invalid = await recovery.batch_recovery(invalid_sources, errors)

        self.supplement_stats['recovered_count'] = len(recovered)
        recovery.print_recovery_stats()

        return recovered

    async def external_collection_process(self, target_gap: int) -> List[dict]:
        """外部书源收集"""
        if target_gap <= 0:
            return []

        print(f"\n=== 外部收集 {target_gap} 个新书源 ===")

        # 查找大型书源文件
        large_files = []
        yiove_file = self.sources_dir / "yiove_new.json"
        if yiove_file.exists():
            large_files.append(yiove_file)

        # 执行外部收集
        collector = SourceCollector(timeout=30)
        collected_sources = await collector.collect_all_sources(large_files)

        # 限制收集数量（避免处理过多数据）
        if len(collected_sources) > target_gap * 2:
            import random
            collected_sources = random.sample(collected_sources, target_gap * 2)

        self.supplement_stats['collected_count'] = len(collected_sources)
        collector.print_collection_stats()

        return collected_sources

    def clean_and_format_sources(self, sources: List[dict]) -> List[dict]:
        """清理和格式化书源"""
        print("清理和格式化书源...")

        # 调用现有的clean.py脚本
        temp_input = self.temp_dir / "temp_clean_input.json"
        temp_output = self.temp_dir / "temp_clean_output.json"

        with open(temp_input, 'w', encoding='utf-8') as f:
            json.dump(sources, f, ensure_ascii=False, indent=2)

        cmd = [
            sys.executable, str(self.scripts_dir / "clean.py"),
            "--input", str(temp_input),
            "--output", str(temp_output)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"清理脚本执行失败: {result.stderr}")
            return sources

        # 读取清理后的结果
        if temp_output.exists():
            with open(temp_output, 'r', encoding='utf-8') as f:
                cleaned_sources = json.load(f)

            # 清理临时文件
            temp_input.unlink()
            temp_output.unlink()

            return cleaned_sources

        return sources

    def filter_adult_content(self, sources: List[dict]) -> List[dict]:
        """过滤成人内容"""
        print("过滤成人内容...")

        # 调用现有的clean_adult_content.py脚本
        temp_input = self.temp_dir / "temp_adult_input.json"
        temp_output = self.temp_dir / "temp_adult_output.json"

        with open(temp_input, 'w', encoding='utf-8') as f:
            json.dump(sources, f, ensure_ascii=False, indent=2)

        cmd = [
            sys.executable, str(self.scripts_dir / "clean_adult_content.py"),
            "--input", str(temp_input),
            "--output", str(temp_output)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"成人内容过滤脚本执行失败: {result.stderr}")
            return sources

        # 读取过滤后的结果
        if temp_output.exists():
            with open(temp_output, 'r', encoding='utf-8') as f:
                filtered_sources = json.load(f)

            self.supplement_stats['filtered_adult'] = len(sources) - len(filtered_sources)

            # 清理临时文件
            temp_input.unlink()
            temp_output.unlink()

            return filtered_sources

        return sources

    def enhanced_scoring_and_selection(self, sources: List[dict], target_sources: int = 1000) -> List[dict]:
        """增强评分和筛选"""
        print("增强评分和筛选...")

        # 初始化增强评分系统
        scorer = EnhancedScoring(self.history_file)

        # 批量评分
        results = scorer.batch_score_sources(sources)

        # 过滤低分书源
        filtered_results = [(s, score, details) for s, score, details in results
                           if score >= MIN_SCORE_THRESHOLD]

        self.supplement_stats['filtered_low_score'] = len(results) - len(filtered_results)

        # 按评分排序
        filtered_results.sort(key=lambda x: -x[1])

        # 域名去重（每个域名最多保留2个）
        domain_count = {}
        final_sources = []

        for source, score, details in filtered_results:
            url = source.get('bookSourceUrl', '')
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc
            except:
                domain = url

            if domain_count.get(domain, 0) < MAX_PER_DOMAIN:
                final_sources.append(source)
                domain_count[domain] = domain_count.get(domain, 0) + 1

            # 达到目标数量就停止
            if len(final_sources) >= target_sources:
                break

        scorer.print_scoring_stats()
        scorer.save_history()

        return final_sources

    async def auto_supplement_workflow(self, force: bool = False, target_sources: int = 1000) -> bool:
        """自动补充工作流"""
        print("=== 书源自动补充系统 ===")
        print(f"目标书源数量: {target_sources}")
        print(f"触发阈值: {MIN_SOURCES_TRIGGER}")
        print(f"紧急阈值: {EMERGENCY_TRIGGER}")
        print()

        # 1. 加载当前书源
        current_sources = self.load_current_sources()
        self.supplement_stats['initial_count'] = len(current_sources)

        print(f"当前书源数量: {len(current_sources)}")

        # 2. 检查是否需要补充
        if not force and len(current_sources) >= MIN_SOURCES_TRIGGER:
            print("书源数量充足，无需补充")
            return False

        if len(current_sources) < EMERGENCY_TRIGGER:
            print("⚠️  书源数量严重不足，启动紧急补充模式")

        # 3. 备份当前书源
        if current_sources:
            self.backup_current_sources(current_sources)

        # 4. 验证当前书源
        valid_sources, invalid_sources, errors = await self.validate_sources(current_sources)
        print(f"验证结果: 有效 {len(valid_sources)}, 无效 {len(invalid_sources)}")

        # 5. 智能恢复失效书源
        recovered_sources = await self.smart_recovery_process(invalid_sources, errors)

        # 6. 计算还需要多少书源
        current_valid_count = len(valid_sources) + len(recovered_sources)
        target_gap = target_sources - current_valid_count

        print(f"\n当前有效书源: {current_valid_count}")
        print(f"目标缺口: {target_gap}")

        # 7. 外部收集新书源
        collected_sources = await self.external_collection_process(max(target_gap, 200))

        # 8. 合并所有书源
        all_sources = valid_sources + recovered_sources + collected_sources
        print(f"\n合并后总数: {len(all_sources)}")

        # 9. 统一处理流程
        print("\n=== 统一处理流程 ===")

        # 清理和格式化
        cleaned_sources = self.clean_and_format_sources(all_sources)
        print(f"清理后: {len(cleaned_sources)}")

        # 过滤成人内容
        filtered_sources = self.filter_adult_content(cleaned_sources)
        print(f"过滤成人内容后: {len(filtered_sources)}")

        # 增强评分和筛选
        final_sources = self.enhanced_scoring_and_selection(filtered_sources, target_sources)
        self.supplement_stats['final_count'] = len(final_sources)

        print(f"最终筛选: {len(final_sources)}")

        # 10. 更新书源库
        with open(self.main_sources_file, 'w', encoding='utf-8') as f:
            json.dump(final_sources, f, ensure_ascii=False, indent=2)

        print(f"\n✓ 书源库已更新: {self.main_sources_file}")

        return True

    def print_supplement_stats(self):
        """打印补充统计"""
        print("\n=== 补充统计 ===")
        print(f"初始书源: {self.supplement_stats['initial_count']}")
        print(f"智能恢复: {self.supplement_stats['recovered_count']}")
        print(f"外部收集: {self.supplement_stats['collected_count']}")
        print(f"过滤成人内容: {self.supplement_stats['filtered_adult']}")
        print(f"过滤低分书源: {self.supplement_stats['filtered_low_score']}")
        print(f"最终书源: {self.supplement_stats['final_count']}")

        if self.supplement_stats['initial_count'] > 0:
            improvement = self.supplement_stats['final_count'] - self.supplement_stats['initial_count']
            print(f"净增长: {improvement:+d}")

async def main():
    parser = argparse.ArgumentParser(description="书源自动补充主控制器")
    parser.add_argument("--force", "-f", action="store_true", help="强制执行补充（忽略数量检查）")
    parser.add_argument("--dry-run", action="store_true", help="试运行模式（不更新文件）")
    parser.add_argument("--target", "-t", type=int, default=TARGET_SOURCES, help=f"目标书源数量，默认 {TARGET_SOURCES}")
    args = parser.parse_args()

    # 使用参数中的目标数量
    target_sources = args.target

    # 获取项目根目录
    base_dir = Path(__file__).parent.parent

    # 初始化自动补充系统
    supplement = AutoSupplement(base_dir)

    try:
        # 执行自动补充
        success = await supplement.auto_supplement_workflow(force=args.force, target_sources=target_sources)

        # 打印统计
        supplement.print_supplement_stats()

        if success:
            print("\n✓ 自动补充完成")
        else:
            print("\n- 无需补充")

        return 0

    except Exception as e:
        print(f"\n❌ 自动补充失败: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit(asyncio.run(main()))