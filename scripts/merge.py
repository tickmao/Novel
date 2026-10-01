#!/usr/bin/env python3
"""
书源合并脚本
- 合并多个书源文件
- 智能去重：相同 URL 保留质量更高的
- 添加来源元信息
"""

import json
import argparse
from pathlib import Path
from datetime import datetime


def calculate_score(source: dict) -> tuple:
    """
    计算书源质量分数，返回元组用于比较
    分数越高越好

    优先级：
    1. enabled: true 优先
    2. lastUpdateTime 更新的优先
    3. 规则更完整的优先
    4. weight 更高的优先
    5. customOrder 更小的优先
    """
    # 1. 是否启用 (权重最高)
    enabled = 1 if source.get("enabled", True) else 0

    # 2. 更新时间 (越新越好)
    last_update = source.get("lastUpdateTime", 0)

    # 3. 规则完整性
    completeness = 0
    # 有搜索规则
    if source.get("searchUrl"):
        completeness += 2
    # 有正文规则
    rule_content = source.get("ruleContent", {})
    if isinstance(rule_content, dict) and rule_content.get("content"):
        completeness += 2
    # 有目录规则
    rule_toc = source.get("ruleToc", {})
    if isinstance(rule_toc, dict) and rule_toc.get("chapterList"):
        completeness += 1
    # 有书籍信息规则
    rule_book_info = source.get("ruleBookInfo", {})
    if isinstance(rule_book_info, dict) and rule_book_info.get("name"):
        completeness += 1
    # 有搜索规则
    rule_search = source.get("ruleSearch", {})
    if isinstance(rule_search, dict) and rule_search.get("bookList"):
        completeness += 1

    # 4. 权重
    weight = source.get("weight", 0)

    # 5. 排序 (越小越好，取负数)
    order = -source.get("customOrder", 9999)

    return (enabled, last_update, completeness, weight, order)


def smart_merge(*source_lists) -> tuple:
    """
    智能合并书源，相同 URL 保留质量更高的

    返回: (合并后的书源列表, 替换统计)
    """
    url_to_source = {}
    replaced_count = 0

    for sources in source_lists:
        for source in sources:
            url = source.get("bookSourceUrl", "")
            if not url:
                continue

            if url not in url_to_source:
                url_to_source[url] = source
            else:
                # 比较分数，保留更好的
                existing = url_to_source[url]
                existing_score = calculate_score(existing)
                new_score = calculate_score(source)

                if new_score > existing_score:
                    url_to_source[url] = source
                    replaced_count += 1

    return list(url_to_source.values()), replaced_count


def simple_merge(*source_lists) -> list:
    """
    简单合并书源（保留第一个出现的）
    """
    seen = set()
    merged = []

    for sources in source_lists:
        for source in sources:
            url = source.get("bookSourceUrl", "")
            if url and url not in seen:
                seen.add(url)
                merged.append(source)

    return merged


def add_meta(sources: list, meta: dict = None) -> dict:
    """
    添加元信息
    """
    if meta is None:
        meta = {}

    return {
        "_meta": {
            "version": datetime.now().strftime("%Y.%m.%d"),
            "count": len(sources),
            "lastUpdate": datetime.now().isoformat(),
            **meta
        },
        "sources": sources
    }


def main():
    parser = argparse.ArgumentParser(description="书源合并脚本")
    parser.add_argument("--inputs", "-i", nargs="+", required=True, help="输入文件路径（可多个）")
    parser.add_argument("--output", "-o", required=True, help="输出文件路径")
    parser.add_argument("--meta", "-m", help="元信息 JSON 字符串")
    parser.add_argument("--flat", "-f", action="store_true", help="输出扁平数组（不包含元信息）")
    parser.add_argument("--simple", "-s", action="store_true", help="使用简单去重（保留第一个）")
    args = parser.parse_args()

    all_sources = []

    for input_file in args.inputs:
        input_path = Path(input_file)
        if not input_path.exists():
            print(f"警告：文件不存在，跳过 {input_path}")
            continue

        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 支持带元信息的格式
        if isinstance(data, dict) and "sources" in data:
            sources = data["sources"]
        elif isinstance(data, list):
            sources = data
        else:
            print(f"警告：文件格式不正确，跳过 {input_path}")
            continue

        print(f"读取 {input_path.name}：{len(sources)} 个书源")
        all_sources.append(sources)

    if not all_sources:
        print("错误：没有有效的输入文件")
        return 1

    total_before = sum(len(s) for s in all_sources)

    # 合并
    if args.simple:
        merged = simple_merge(*all_sources)
        replaced = 0
        print("\n使用简单去重模式")
    else:
        merged, replaced = smart_merge(*all_sources)
        print("\n使用智能去重模式")

    duplicates = total_before - len(merged)

    print(f"合并前总数：{total_before}")
    print(f"去重后总数：{len(merged)}")
    print(f"重复书源：{duplicates}")
    if not args.simple:
        print(f"智能替换：{replaced} 个（用更优质的版本替换）")

    # 输出
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.flat:
        output_data = merged
    else:
        meta = json.loads(args.meta) if args.meta else {}
        output_data = add_meta(merged, meta)

    from validate import assert_report_output
    assert_report_output(Path(output_path))
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"\n输出到：{output_path}")

    return 0


if __name__ == "__main__":
    exit(main())
