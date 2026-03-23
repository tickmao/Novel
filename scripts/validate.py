#!/usr/bin/env python3
"""
书源有效性校验脚本
- 异步并发检测书源 URL 可访问性
- 标记失效书源
- 输出校验报告
"""

import json
import asyncio
import argparse
from pathlib import Path
from datetime import datetime

try:
    import aiohttp
except ImportError:
    print("请先安装 aiohttp: pip install aiohttp")
    exit(1)


# 默认超时时间（秒）
DEFAULT_TIMEOUT = 10

# 并发数量
CONCURRENCY = 20


async def check_source(session: aiohttp.ClientSession, source: dict, timeout: int) -> tuple:
    """
    检查单个书源是否有效

    返回: (书源, 是否有效, 错误信息)
    """
    url = source.get("bookSourceUrl", "")
    if not url:
        return source, False, "URL 为空"

    try:
        async with session.head(url, timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=True) as resp:
            if resp.status < 400:
                return source, True, None
            else:
                return source, False, f"HTTP {resp.status}"
    except asyncio.TimeoutError:
        return source, False, "超时"
    except aiohttp.ClientError as e:
        return source, False, str(e)[:50]
    except Exception as e:
        return source, False, str(e)[:50]


async def validate_sources(sources: list, timeout: int = DEFAULT_TIMEOUT, sample: int = None) -> tuple:
    """
    批量校验书源

    返回: (有效书源列表, 无效书源列表)
    """
    # 采样模式
    if sample and sample < len(sources):
        import random
        sources = random.sample(sources, sample)
        print(f"采样模式：随机选取 {sample} 个书源进行校验")

    valid = []
    invalid = []
    errors = {}

    # 创建信号量控制并发
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def check_with_semaphore(session, source):
        async with semaphore:
            return await check_source(session, source, timeout)

    connector = aiohttp.TCPConnector(limit=CONCURRENCY, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [check_with_semaphore(session, s) for s in sources]

        total = len(tasks)
        completed = 0

        for coro in asyncio.as_completed(tasks):
            source, is_valid, error = await coro
            completed += 1

            if is_valid:
                source["_validation_status"] = "valid"
                source["_last_validated_at"] = datetime.now().isoformat()
                valid.append(source)
            else:
                source["_validation_status"] = "invalid"
                source["_last_validated_at"] = datetime.now().isoformat()
                source["_validation_error"] = error
                invalid.append(source)
                errors[source.get("bookSourceUrl", "")] = error

            # 进度显示
            if completed % 10 == 0 or completed == total:
                print(f"\r进度：{completed}/{total} ({completed*100//total}%)", end="", flush=True)

    print()  # 换行

    return valid, invalid, errors


def main():
    parser = argparse.ArgumentParser(description="书源有效性校验脚本")
    parser.add_argument("--input", "-i", required=True, help="输入文件路径")
    parser.add_argument("--output", "-o", help="有效书源输出路径")
    parser.add_argument("--invalid", help="无效书源输出路径")
    parser.add_argument("--timeout", "-t", type=int, default=DEFAULT_TIMEOUT, help=f"超时时间（秒），默认 {DEFAULT_TIMEOUT}")
    parser.add_argument("--sample", "-s", type=int, help="采样数量（用于测试）")
    parser.add_argument("--report", "-r", help="校验报告输出路径")
    args = parser.parse_args()

    input_path = Path(args.input)

    if not input_path.exists():
        print(f"错误：输入文件不存在 {input_path}")
        return 1

    # 读取书源
    with open(input_path, "r", encoding="utf-8") as f:
        sources = json.load(f)

    print(f"读取书源：{len(sources)} 个")
    print(f"超时设置：{args.timeout} 秒")
    print(f"并发数量：{CONCURRENCY}")
    print()

    # 校验
    valid, invalid, errors = asyncio.run(validate_sources(sources, args.timeout, args.sample))

    print(f"\n校验结果：")
    print(f"  有效：{len(valid)} 个")
    print(f"  无效：{len(invalid)} 个")
    print(f"  有效率：{len(valid)*100//(len(valid)+len(invalid)) if valid or invalid else 0}%")

    # 输出有效书源
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(valid, f, ensure_ascii=False, indent=2)
        print(f"\n有效书源输出到：{output_path}")

    # 输出无效书源
    if args.invalid and invalid:
        invalid_path = Path(args.invalid)
        invalid_path.parent.mkdir(parents=True, exist_ok=True)
        with open(invalid_path, "w", encoding="utf-8") as f:
            json.dump(invalid, f, ensure_ascii=False, indent=2)
        print(f"无效书源输出到：{invalid_path}")

    # 输出校验报告
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)

        report = {
            "timestamp": datetime.now().isoformat(),
            "total": len(sources),
            "valid": len(valid),
            "invalid": len(invalid),
            "sample": args.sample,
            "timeout": args.timeout,
            "errors": errors
        }

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"校验报告输出到：{report_path}")

    return 0


if __name__ == "__main__":
    exit(main())
