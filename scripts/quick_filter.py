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

    # 初始化
    initializer = InitializeSources()

    # 读取原始书源
    with open(args.input, 'r', encoding='utf-8') as f:
        raw_sources = json.load(f)

    print(f"原始书源: {len(raw_sources)} 个")

    # 快速过滤
    filtered = initializer.quick_filter(raw_sources)

    # 保存过滤后的书源
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)

    print(f"过滤后: {len(filtered)} 个")
    print(f"过滤率: {(1 - len(filtered)/len(raw_sources))*100:.1f}%")

if __name__ == "__main__":
    main()
