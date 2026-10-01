#!/usr/bin/env python3
"""
书源筛选脚本
- 只保留小说类型 (bookSourceType = 0)
- 排除漫画、有声书、影视
"""

import json
import argparse
from pathlib import Path

# 需要排除的分组关键词
EXCLUDE_KEYWORDS = ["漫画", "有声", "影视", "视频", "动漫", "听书", "音频"]


def filter_novel_sources(sources: list) -> tuple:
    """
    筛选小说书源

    返回: (小说书源列表, 排除的书源列表)
    """
    novels = []
    excluded = []

    for source in sources:
        # 检查类型
        source_type = source.get("bookSourceType", 0)
        if source_type != 0:
            excluded.append(source)
            continue

        # 检查分组名称
        group = source.get("bookSourceGroup", "")
        if any(kw in group for kw in EXCLUDE_KEYWORDS):
            excluded.append(source)
            continue

        # 检查名称
        name = source.get("bookSourceName", "")
        if any(kw in name for kw in EXCLUDE_KEYWORDS):
            excluded.append(source)
            continue

        novels.append(source)

    return novels, excluded


def main():
    parser = argparse.ArgumentParser(description="书源筛选脚本")
    parser.add_argument("--input", "-i", required=True, help="输入文件路径")
    parser.add_argument("--output", "-o", required=True, help="输出文件路径")
    parser.add_argument("--excluded", "-e", help="排除的书源输出路径（可选）")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"错误：输入文件不存在 {input_path}")
        return 1

    # 读取书源
    with open(input_path, "r", encoding="utf-8") as f:
        sources = json.load(f)

    print(f"读取书源：{len(sources)} 个")

    # 筛选
    novels, excluded = filter_novel_sources(sources)

    print(f"小说书源：{len(novels)} 个")
    print(f"排除书源：{len(excluded)} 个")

    # 输出小说书源
    output_path.parent.mkdir(parents=True, exist_ok=True)
    from validate import assert_report_output
    assert_report_output(Path(output_path))
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(novels, f, ensure_ascii=False, indent=2)

    print(f"输出到：{output_path}")

    # 输出排除的书源（可选）
    if args.excluded and excluded:
        excluded_path = Path(args.excluded)
        excluded_path.parent.mkdir(parents=True, exist_ok=True)
        with open(excluded_path, "w", encoding="utf-8") as f:
            json.dump(excluded, f, ensure_ascii=False, indent=2)
        print(f"排除书源输出到：{excluded_path}")

    # 统计排除原因
    type_excluded = sum(1 for s in excluded if s.get("bookSourceType", 0) != 0)
    keyword_excluded = len(excluded) - type_excluded

    print(f"\n排除原因统计：")
    print(f"  类型不是小说：{type_excluded}")
    print(f"  关键词匹配：{keyword_excluded}")

    return 0


if __name__ == "__main__":
    exit(main())
