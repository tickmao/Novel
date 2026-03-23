#!/usr/bin/env python3
"""
对原始书源池执行静态准入筛选，产出 screened pool。
"""

import argparse
import json
from pathlib import Path

from legado_paths import raw_pool_file, resolve_legado_dir, screened_pool_file, screened_report_file
from source_inventory import SourceInventory


def main():
    parser = argparse.ArgumentParser(description="静态筛选原始书源池")
    parser.add_argument("--input", "-i", type=Path, help="输入文件，默认使用 pool/raw.json")
    parser.add_argument("--output", "-o", type=Path, help="输出文件，默认使用 pool/screened.json")
    parser.add_argument("--report", "-r", type=Path, help="报告文件，默认使用 pool/screened_report.json")
    args = parser.parse_args()

    legado_dir = resolve_legado_dir(Path(__file__).parent.parent)
    inventory = SourceInventory(legado_dir)

    input_file = args.input or raw_pool_file(legado_dir)
    output_file = args.output or screened_pool_file(legado_dir)
    report_file = args.report or screened_report_file(legado_dir)

    with open(input_file, "r", encoding="utf-8") as f:
        sources = json.load(f)

    screened, report = inventory.refresh_screened_pool(sources, save=False)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(screened, f, ensure_ascii=False, indent=2)

    report_file.parent.mkdir(parents=True, exist_ok=True)
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("静态筛选完成")
    print(f"输入数量：{report['input']}")
    print(f"通过数量：{report['screened']}")
    print(f"剔除数量：{report['rejected']}")
    print(f"输出文件：{output_file}")
    print(f"报告文件：{report_file}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
