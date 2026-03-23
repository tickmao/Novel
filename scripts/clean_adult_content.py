#!/usr/bin/env python3
"""
基于统一准入策略清理成人向与耽美/BL 相关书源。
"""

import argparse
import json
from pathlib import Path

from legado_paths import primary_source_file, resolve_legado_dir
from source_policy import SourcePolicy


def clean_adult_sources(input_file: Path, output_file: Path | None = None) -> tuple[int, int, list]:
    if output_file is None:
        output_file = input_file

    with open(input_file, "r", encoding="utf-8") as f:
        sources = json.load(f)

    policy = SourcePolicy(Path(__file__).parent.parent)
    kept = []
    removed = []

    for source in sources:
        risks = policy.detect_adult_risks(source)
        if risks:
            removed.append({
                "name": source.get("bookSourceName", ""),
                "url": source.get("bookSourceUrl", ""),
                "reasons": risks,
            })
        else:
            kept.append(source)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(kept, f, ensure_ascii=False, indent=2)

    return len(sources), len(kept), removed


def main():
    parser = argparse.ArgumentParser(description="清理成人向与耽美/BL 相关书源")
    parser.add_argument("--input", "-i", type=Path, help="输入文件路径")
    parser.add_argument("--output", "-o", type=Path, help="输出文件路径")
    parser.add_argument("--removed", "-r", type=Path, help="移除报告输出路径")
    args = parser.parse_args()

    legado_dir = resolve_legado_dir(Path(__file__).parent.parent)
    input_file = args.input or primary_source_file(legado_dir)
    output_file = args.output or input_file
    removed_file = args.removed or legado_dir / "removed_adult_sources.json"

    if not input_file.exists():
        print(f"错误：找不到文件 {input_file}")
        return 1

    original_count, final_count, removed_sources = clean_adult_sources(input_file, output_file)

    print("成人向清理完成")
    print(f"原始书源数量：{original_count}")
    print(f"清理后数量：{final_count}")
    print(f"移除数量：{len(removed_sources)}")

    removed_file.parent.mkdir(parents=True, exist_ok=True)
    with open(removed_file, "w", encoding="utf-8") as f:
        json.dump(removed_sources, f, ensure_ascii=False, indent=2)
    print(f"移除报告已保存到：{removed_file}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
