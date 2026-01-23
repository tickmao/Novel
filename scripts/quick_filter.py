#!/usr/bin/env python3
"""
候选池快速过滤脚本
从原始书源中快速过滤出符合基本条件的书源
"""

import json
import sys
import argparse
from pathlib import Path

# 添加 scripts 目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from initialize_sources import InitializeSources

def main():
    parser = argparse.ArgumentParser(description='快速过滤书源')
    parser.add_argument('--input', required=True, help='输入文件路径')
    parser.add_argument('--output', required=True, help='输出文件路径')
    args = parser.parse_args()

    try:
        # 初始化
        initializer = InitializeSources()

        # 读取原始书源
        try:
            with open(args.input, 'r', encoding='utf-8') as f:
                raw_sources = json.load(f)
        except FileNotFoundError:
            print(f"错误：文件不存在 {args.input}", file=sys.stderr)
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"错误：JSON 格式错误 {e}", file=sys.stderr)
            sys.exit(1)

        print(f"原始书源: {len(raw_sources)} 个")

        # 快速过滤
        filtered = initializer.quick_filter(raw_sources)

        # 确保输出目录存在
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # 保存过滤后的书源
        try:
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(filtered, f, ensure_ascii=False, indent=2)
        except (OSError, PermissionError) as e:
            print(f"错误：无法写入文件 {args.output}: {e}", file=sys.stderr)
            sys.exit(1)

        print(f"过滤后: {len(filtered)} 个")

        # 避免除零错误
        if len(raw_sources) > 0:
            filter_rate = (1 - len(filtered)/len(raw_sources)) * 100
            print(f"过滤率: {filter_rate:.1f}%")
        else:
            print("过滤率: N/A (原始书源为空)")

    except Exception as e:
        print(f"未预期的错误: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
