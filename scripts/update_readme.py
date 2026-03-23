#!/usr/bin/env python3
"""
书源数量统计和 README 自动更新脚本
- 统计各平台书源数量
- 自动更新 README.md 中的数量信息
- 生成统计报告
"""

import json
import os
import re
from pathlib import Path
from datetime import datetime
from typing import Dict

from legado_paths import primary_source_file

class SourceCounter:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.sources_dir = base_dir / "sources"
        self.readme_file = base_dir / "README.md"

    def count_legado_sources(self) -> int:
        """统计阅读书源数量"""
        legado_file = primary_source_file(self.base_dir)

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
        """统计香色闺阁书源数量 - 与 update_sources.py 保持一致的估算口径"""
        xsreader_file = self.sources_dir / "xsreader/full.xbs"

        if not xsreader_file.exists():
            return 0

        try:
            file_size = os.path.getsize(xsreader_file)
            return file_size // (9 * 1024)
        except Exception as e:
            print(f"读取香色闺阁书源失败: {e}")
            return 0

    def get_all_source_counts(self) -> Dict[str, int]:
        """获取所有平台的书源数量"""
        return {
            "legado": self.count_legado_sources(),
            "xsreader": self.count_xsreader_sources()
        }

    def update_readme(self, counts: Dict[str, int]) -> bool:
        """更新 README.md 文件"""
        if not self.readme_file.exists():
            print(f"README 文件不存在: {self.readme_file}")
            return False

        try:
            with open(self.readme_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # 更新支持平台表格
            legado_count = counts.get("legado", 0)
            xsreader_count = counts.get("xsreader", 0)

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

            # 写回文件
            with open(self.readme_file, 'w', encoding='utf-8') as f:
                f.write(content)

            print("README.md 更新成功")
            return True

        except Exception as e:
            print(f"更新 README 失败: {e}")
            return False

    def run_update(self) -> bool:
        """执行完整的更新流程"""
        print("开始统计书源数量...")

        # 统计各平台书源数量
        counts = self.get_all_source_counts()

        print("书源统计结果:")
        print(f"  阅读 (Legado): {counts['legado']} 个")
        print(f"  香色闺阁: {counts['xsreader']} 个")

        # 更新 README
        return self.update_readme(counts)

def main():
    import argparse

    parser = argparse.ArgumentParser(description="书源数量统计和 README 自动更新")
    parser.add_argument("--base-dir", default=".", help="项目根目录路径")
    parser.add_argument("--dry-run", action="store_true", help="试运行模式（不修改文件）")
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()
    counter = SourceCounter(base_dir)

    if args.dry_run:
        print("试运行模式：只统计数量，不修改文件")
        counts = counter.get_all_source_counts()
        print(f"阅读 (Legado): {counts['legado']} 个")
        print(f"香色闺阁: {counts['xsreader']} 个")
        print(f"总计: {sum(counts.values())} 个书源")
    else:
        success = counter.run_update()
        if success:
            print("✅ 书源数量统计和 README 更新完成")
        else:
            print("❌ 更新过程中出现错误")
            return 1

    return 0

if __name__ == "__main__":
    exit(main())
