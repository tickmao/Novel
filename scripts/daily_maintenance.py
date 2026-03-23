#!/usr/bin/env python3
"""
日常维护整合脚本 - Phase 2 日常维护流程
- 滚动验证（每天验证 100 个书源，10 天一轮）
- 自动替换（连续 3 次失败或评分 <30）
- 质量监控（生成健康报告）
- 告警检查（<900 警告，<800 严重）
"""

import json
import asyncio
import argparse
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple

# 添加 scripts 目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from batch_validator import BatchValidator
from safe_updater import SafeUpdater
from source_inventory import SourceInventory


class DailyMaintenance:
    """日常维护整合脚本"""

    # 阈值配置
    MIN_SOURCES_WARNING = 950
    MIN_SOURCES_CRITICAL = 900
    MIN_SCORE_THRESHOLD = 30
    MAX_CONSECUTIVE_FAILURES = 3

    def __init__(self, base_dir: Path = None):
        """
        初始化

        Args:
            base_dir: 基础目录，默认为 sources/legado
        """
        if base_dir is None:
            base_dir = Path(__file__).parent.parent / 'sources' / 'legado'

        self.inventory = SourceInventory(base_dir)
        self.base_dir = self.inventory.base_dir
        self.pool_dir = self.base_dir / 'pool'
        self.main_dir = self.base_dir / 'main'
        self.temp_dir = self.base_dir / 'temp'

        # 文件路径
        self.main_file = self.inventory.working_file
        self.metadata_file = self.main_dir / 'metadata.json'
        self.candidates_file = self.inventory.candidate_file

        # 检查点目录
        self.checkpoint_dir = self.temp_dir / 'checkpoints'
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def load_metadata(self) -> Dict:
        """
        加载元数据

        Returns:
            元数据字典
        """
        if not self.metadata_file.exists():
            return {
                'last_validation_index': 0,
                'validation_history': {},
                'failure_counts': {},
                'last_maintenance': None
            }

        with open(self.metadata_file, 'r', encoding='utf-8') as f:
            return json.load(f)

    def save_metadata(self, metadata: Dict):
        """
        保存元数据

        Args:
            metadata: 元数据字典
        """
        with open(self.metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

    def select_sources_for_validation(
        self,
        sources: List[Dict],
        metadata: Dict,
        batch_size: int = 100
    ) -> Tuple[List[Dict], List[int]]:
        """
        选择需要验证的书源（滚动验证）

        Args:
            sources: 所有书源
            metadata: 元数据
            batch_size: 批次大小

        Returns:
            (选中的书源, 选中的索引列表)
        """
        last_index = metadata.get('last_validation_index', 0)
        total = len(sources)

        # 计算本次验证的索引范围
        start_index = last_index
        end_index = min(start_index + batch_size, total)

        # 如果到达末尾，从头开始
        if end_index >= total:
            end_index = batch_size - (total - start_index)
            selected_indices = list(range(start_index, total)) + list(range(0, end_index))
            next_index = end_index
        else:
            selected_indices = list(range(start_index, end_index))
            next_index = end_index

        selected_sources = [sources[i] for i in selected_indices]

        print(f'滚动验证：索引 {start_index} → {next_index}（共 {len(selected_sources)} 个）')

        return selected_sources, selected_indices

    async def validate_sources(
        self,
        sources: List[Dict],
        concurrency: int = 20,
        timeout: int = 10
    ) -> Tuple[List[Dict], List[Dict], Dict]:
        """
        验证书源

        Args:
            sources: 书源列表
            concurrency: 并发数
            timeout: 超时时间

        Returns:
            (有效书源, 无效书源, 统计信息)
        """
        print('\n=== 步骤 1：滚动验证 ===')

        validator = BatchValidator(
            batch_size=len(sources),  # 一次性验证所有选中的书源
            concurrency=concurrency,
            timeout=timeout,
            checkpoint_dir=None  # 日常维护不需要检查点
        )

        valid, invalid, stats = await validator.validate_batch(
            sources,
            batch_num=1,
            total_batches=1
        )

        return valid, invalid, stats

    def identify_failed_sources(
        self,
        sources: List[Dict],
        selected_indices: List[int],
        invalid_sources: List[Dict],
        metadata: Dict
    ) -> List[int]:
        """
        识别需要替换的失效书源

        Args:
            sources: 所有书源
            selected_indices: 本次验证的索引
            invalid_sources: 无效书源
            metadata: 元数据

        Returns:
            需要替换的书源索引列表
        """
        print('\n=== 步骤 2：识别失效书源 ===')

        failure_counts = metadata.get('failure_counts', {})
        to_replace = []

        # 更新失败计数
        for i, source in enumerate(sources):
            url = source.get('bookSourceUrl', '')

            if i in selected_indices:
                # 本次验证的书源
                if source in invalid_sources:
                    # 失败
                    failure_counts[url] = failure_counts.get(url, 0) + 1
                else:
                    # 成功，重置计数
                    failure_counts[url] = 0

            # 检查是否需要替换
            if failure_counts.get(url, 0) >= self.MAX_CONSECUTIVE_FAILURES:
                to_replace.append(i)
                print(f'  - {source.get("bookSourceName", "未知")} (连续 {failure_counts[url]} 次失败)')

            # 检查评分
            score = source.get('score', 0)
            if score > 0 and score < self.MIN_SCORE_THRESHOLD:
                if i not in to_replace:
                    to_replace.append(i)
                    print(f'  - {source.get("bookSourceName", "未知")} (评分过低: {score})')

        metadata['failure_counts'] = failure_counts

        print(f'\n需要替换：{len(to_replace)} 个书源')

        return to_replace

    def replace_failed_sources(
        self,
        sources: List[Dict],
        to_replace: List[int],
        candidates: List[Dict]
    ) -> List[Dict]:
        """
        替换失效书源

        Args:
            sources: 当前书源列表
            to_replace: 需要替换的索引列表
            candidates: 候选书源列表

        Returns:
            替换后的书源列表
        """
        print('\n=== 步骤 3：替换失效书源 ===')

        if not to_replace:
            print('无需替换')
            return sources

        if not candidates:
            print('⚠ 候选池为空，无法替换')
            return sources

        # 复制书源列表
        new_sources = sources.copy()

        # 获取当前所有 URL（用于去重）
        current_urls = {s.get('bookSourceUrl', '') for s in sources}

        # 按评分排序候选书源
        candidates_sorted = sorted(
            candidates,
            key=lambda s: s.get('score', 0),
            reverse=True
        )

        replaced_count = 0

        for idx in to_replace:
            # 查找候选书源（不在当前列表中）
            replacement = None
            for candidate in candidates_sorted:
                url = candidate.get('bookSourceUrl', '')
                if url not in current_urls:
                    replacement = candidate
                    current_urls.add(url)
                    break

            if replacement:
                old_source = new_sources[idx]
                new_sources[idx] = replacement
                replaced_count += 1

                print(f'  替换：{old_source.get("bookSourceName", "未知")} → {replacement.get("bookSourceName", "未知")}')
            else:
                print(f'  ⚠ 无可用候选书源替换索引 {idx}')

        print(f'\n✓ 已替换：{replaced_count} 个书源')

        return new_sources

    def generate_health_report(
        self,
        sources: List[Dict],
        validation_stats: Dict,
        replaced_count: int
    ) -> Dict:
        """
        生成健康报告

        Args:
            sources: 当前书源列表
            validation_stats: 验证统计
            replaced_count: 替换数量

        Returns:
            健康报告
        """
        print('\n=== 步骤 4：生成健康报告 ===')

        total = len(sources)

        # 计算平均评分
        scores = [s.get('score', 0) for s in sources if s.get('score', 0) > 0]
        avg_score = sum(scores) / len(scores) if scores else 0

        # 统计域名多样性
        domains = set()
        for source in sources:
            from urllib.parse import urlparse
            url = source.get('bookSourceUrl', '')
            try:
                domain = urlparse(url).netloc
                if domain.startswith('www.'):
                    domain = domain[4:]
                domains.add(domain)
            except:
                pass

        # 健康状态
        if total < self.MIN_SOURCES_CRITICAL:
            health_status = 'critical'
            health_message = f'严重：书源数量 {total} < {self.MIN_SOURCES_CRITICAL}'
        elif total < self.MIN_SOURCES_WARNING:
            health_status = 'warning'
            health_message = f'警告：书源数量 {total} < {self.MIN_SOURCES_WARNING}'
        else:
            health_status = 'good'
            health_message = '良好'

        report = {
            'timestamp': datetime.now().isoformat(),
            'health_status': health_status,
            'health_message': health_message,
            'total_sources': total,
            'avg_score': round(avg_score, 1),
            'unique_domains': len(domains),
            'validation_stats': validation_stats,
            'replaced_count': replaced_count
        }

        print(f'健康状态：{health_status} - {health_message}')
        print(f'书源总数：{total}')
        print(f'平均评分：{avg_score:.1f}')
        print(f'唯一域名：{len(domains)}')
        print(f'本次替换：{replaced_count}')

        return report

    async def maintain(
        self,
        batch_size: int = 100,
        concurrency: int = 20,
        timeout: int = 10,
        dry_run: bool = False
    ) -> bool:
        """
        执行日常维护

        Args:
            batch_size: 验证批次大小
            concurrency: 验证并发数
            timeout: 验证超时时间
            dry_run: 仅模拟，不实际更新

        Returns:
            是否成功
        """
        print('\n' + '='*60)
        print('日常维护流程开始')
        print('='*60)
        print(f'时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
        print(f'验证批次：{batch_size} 个书源')
        print('='*60)

        try:
            # 读取当前书源
            sources = self.inventory.load_working_sources()
            print(f'\n当前书源：{len(sources)} 个')

            # 读取元数据
            metadata = self.load_metadata()

            # 选择需要验证的书源
            selected_sources, selected_indices = self.select_sources_for_validation(
                sources,
                metadata,
                batch_size
            )

            # 验证书源
            valid_sources, invalid_sources, validation_stats = await self.validate_sources(
                selected_sources,
                concurrency=concurrency,
                timeout=timeout
            )

            # 识别失效书源
            to_replace = self.identify_failed_sources(
                sources,
                selected_indices,
                invalid_sources,
                metadata
            )

            # 替换失效书源
            replaced_count = 0
            if to_replace:
                # 读取候选池
                candidates = self.inventory.load_candidate_sources()

                new_sources = self.replace_failed_sources(sources, to_replace, candidates)
                replaced_count = len([i for i in to_replace if new_sources[i] != sources[i]])

                sources = new_sources

            # 替换后重建库存并重新导出 1000 条
            candidates = self.inventory.load_candidate_sources()
            working_sources, export_sources, inventory_report = self.inventory.build_inventory(
                sources,
                candidates,
                save=not dry_run,
            )
            sources = working_sources

            # 生成健康报告
            report = self.generate_health_report(sources, validation_stats, replaced_count)
            report['inventory_report'] = inventory_report

            # 更新元数据
            if not dry_run:
                metadata = self.load_metadata()
            metadata['last_validation_index'] = (metadata.get('last_validation_index', 0) + batch_size) % len(sources)
            metadata['last_maintenance'] = datetime.now().isoformat()
            metadata['last_report'] = report

            if not dry_run:
                self.save_metadata(metadata)
                print(f'\n✓ 元数据已保存：{self.metadata_file}')

            print('\n' + '='*60)
            print('日常维护流程完成')
            print('='*60)

            # 告警检查
            if report['health_status'] == 'critical':
                print('\n🚨 严重告警：书源数量过少，需要立即补充！')
                return False
            elif report['health_status'] == 'warning':
                print('\n⚠️  警告：书源数量偏少，建议尽快补充')

            return True

        except Exception as e:
            print(f'\n✗ 日常维护异常：{e}')
            import traceback
            traceback.print_exc()
            return False


async def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description='日常维护整合脚本 - Phase 2 日常维护流程')
    parser.add_argument('--base-dir', type=Path, help='基础目录')
    parser.add_argument('--batch-size', type=int, default=100, help='验证批次大小')
    parser.add_argument('--concurrency', type=int, default=20, help='验证并发数')
    parser.add_argument('--timeout', type=int, default=10, help='验证超时时间（秒）')
    parser.add_argument('--dry-run', action='store_true', help='仅模拟，不实际更新')

    args = parser.parse_args()

    # 创建维护器
    maintainer = DailyMaintenance(base_dir=args.base_dir)

    # 执行维护
    success = await maintainer.maintain(
        batch_size=args.batch_size,
        concurrency=args.concurrency,
        timeout=args.timeout,
        dry_run=args.dry_run
    )

    if success:
        print('\n✓ 维护成功')
        sys.exit(0)
    else:
        print('\n✗ 维护失败')
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
