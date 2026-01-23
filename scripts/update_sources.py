#!/usr/bin/env python3
"""
书源数量统计和站点/README自动更新脚本
- 准确统计各平台书源数量
- 自动更新站点 docs/index.html
- 自动更新 README.md
- 生成统计报告
"""

import json
import re
import os
from pathlib import Path
from datetime import datetime
from typing import Dict

class SourceUpdater:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.sources_dir = base_dir / "sources"
        self.docs_dir = base_dir / "docs"
        self.readme_file = base_dir / "README.md"
        self.index_file = self.docs_dir / "index.html"

    def count_legado_sources(self) -> int:
        """统计阅读书源数量"""
        # Novel 2.0: 使用新的三层架构路径
        legado_file = self.sources_dir / "legado/main/full.json"

        if not legado_file.exists():
            # 兼容旧路径
            legado_file = self.sources_dir / "legado/full.json"
            if not legado_file.exists():
                return 0

        try:
            with open(legado_file, 'r', encoding='utf-8') as f:
                sources = json.load(f)
                return len(sources) if isinstance(sources, list) else 0
        except Exception as e:
            print(f"读取阅读书源失败: {e}")
            return 0

    def count_xsreader_sources(self) -> int:
        """统计香色闺阁书源数量 - 基于文件大小估算"""
        xsreader_file = self.sources_dir / "xsreader/full.xbs"

        if not xsreader_file.exists():
            return 0

        try:
            # 香色闺阁是二进制格式，根据文件大小估算
            file_size = os.path.getsize(xsreader_file)
            # 根据经验，每个书源约8-10KB，这里用9KB估算
            estimated_count = file_size // (9 * 1024)
            return estimated_count
        except Exception as e:
            print(f"读取香色闺阁书源失败: {e}")
            return 0

    def count_ifreetime_sources(self) -> int:
        """统计爱阅书香书源数量"""
        ifreetime_dir = self.sources_dir / "ifreetime"

        if not ifreetime_dir.exists():
            return 0

        # 目前为空，返回0
        return 0

    def get_all_source_counts(self) -> Dict[str, int]:
        """获取所有平台的书源数量"""
        counts = {
            "legado": self.count_legado_sources(),
            "xsreader": self.count_xsreader_sources(),
            "ifreetime": self.count_ifreetime_sources()
        }

        print(f"统计结果:")
        print(f"  阅读 (Legado): {counts['legado']} 个")
        print(f"  香色闺阁: {counts['xsreader']} 个")
        print(f"  爱阅书香: {counts['ifreetime']} 个")
        print(f"  总计: {sum(counts.values())} 个")

        return counts

    def update_website(self, counts: Dict[str, int]) -> bool:
        """更新站点 docs/index.html"""
        if not self.index_file.exists():
            print(f"站点文件不存在: {self.index_file}")
            return False

        try:
            with open(self.index_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # 更新书源总数
            total_sources = counts['legado']  # 主要展示阅读书源数量
            content = re.sub(
                r'<span class="stat-value">\d+ 个书源</span>',
                f'<span class="stat-value">{total_sources} 个书源</span>',
                content
            )

            # 更新meta描述中的数量
            content = re.sub(
                r'聚合 \d+\+ 纯小说书源',
                f'聚合 {total_sources}+ 纯小说书源',
                content
            )

            # 更新Open Graph描述
            content = re.sub(
                r'聚合 \d+\+ 纯小说书源',
                f'聚合 {total_sources}+ 纯小说书源',
                content
            )

            with open(self.index_file, 'w', encoding='utf-8') as f:
                f.write(content)

            print("站点 index.html 更新成功")
            return True

        except Exception as e:
            print(f"更新站点失败: {e}")
            return False

    def update_readme(self, counts: Dict[str, int]) -> bool:
        """更新 README.md"""
        if not self.readme_file.exists():
            print(f"README 文件不存在: {self.readme_file}")
            return False

        try:
            with open(self.readme_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # 更新支持平台表格
            legado_count = counts['legado']
            xsreader_count = counts['xsreader']
            ifreetime_count = counts['ifreetime']

            # 更新表格中的数量
            content = re.sub(
                r'(\| Android / iOS \| \[阅读 \(Legado\)\].*?\| )\d+( \|)',
                f'\\g<1>{legado_count}\\g<2>',
                content
            )

            content = re.sub(
                r'(\| iOS \| \[香色闺阁\].*?\| )\d+( \|)',
                f'\\g<1>{xsreader_count}\\g<2>',
                content
            )

            if ifreetime_count > 0:
                content = re.sub(
                    r'(\| iOS \| \[爱阅书香\].*?\| )待更新( \|)',
                    f'\\g<1>{ifreetime_count}\\g<2>',
                    content
                )

            # 更新书源导入部分的数量
            content = re.sub(
                r'\*\*全量书源 \(\d+ 个\)\*\*',
                f'**全量书源 ({legado_count} 个)**',
                content
            )

            content = re.sub(
                r'\*\*全量书源 \(\d+ 个站点\)\*\*',
                f'**全量书源 ({xsreader_count} 个站点)**',
                content
            )

            with open(self.readme_file, 'w', encoding='utf-8') as f:
                f.write(content)

            print("README.md 更新成功")
            return True

        except Exception as e:
            print(f"更新 README 失败: {e}")
            return False

    def generate_stats_report(self, counts: Dict[str, int]) -> str:
        """生成统计报告"""
        total = sum(counts.values())

        report = f"""# 书源统计报告

**更新时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 各平台书源数量

| 平台 | 应用 | 数量 | 状态 |
|------|------|------|------|
| Android/iOS | 阅读 (Legado) | {counts['legado']} | ✅ 主要维护 |
| iOS | 香色闺阁 | {counts['xsreader']} | 📊 估算值 |
| iOS | 爱阅书香 | {counts['ifreetime']} | ⏳ 待更新 |

**总计**: {total} 个书源

## 统计说明

- **阅读书源**: 精确统计，JSON格式文件
- **香色闺阁**: 基于文件大小估算（二进制格式）
- **爱阅书香**: 目前无书源文件

## 更新内容

- ✅ 站点首页书源数量
- ✅ README.md 支持平台表格
- ✅ README.md 书源导入说明
- ✅ 元数据描述信息
"""
        return report

    def run_update(self) -> bool:
        """执行完整的更新流程"""
        print("开始统计书源数量并更新站点和README...")

        # 统计各平台书源数量
        counts = self.get_all_source_counts()

        # 更新站点
        website_success = self.update_website(counts)

        # 更新 README
        readme_success = self.update_readme(counts)

        # 生成统计报告
        report = self.generate_stats_report(counts)
        report_file = self.base_dir / "STATS.md"

        try:
            with open(report_file, 'w', encoding='utf-8') as f:
                f.write(report)
            print(f"统计报告已保存: {report_file}")
        except Exception as e:
            print(f"保存统计报告失败: {e}")

        return website_success and readme_success

def main():
    import argparse

    parser = argparse.ArgumentParser(description="书源数量统计和站点/README自动更新")
    parser.add_argument("--base-dir", default=".", help="项目根目录路径")
    parser.add_argument("--dry-run", action="store_true", help="试运行模式（不修改文件）")
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()
    updater = SourceUpdater(base_dir)

    if args.dry_run:
        print("试运行模式：只统计数量，不修改文件")
        counts = updater.get_all_source_counts()
        return 0
    else:
        success = updater.run_update()
        if success:
            print("\n✅ 书源数量统计和站点/README更新完成")
        else:
            print("\n❌ 更新过程中出现错误")
            return 1

    return 0

if __name__ == "__main__":
    exit(main())