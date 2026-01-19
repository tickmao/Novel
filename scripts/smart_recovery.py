#!/usr/bin/env python3
"""
智能书源恢复系统
- 分析失效书源的错误类型
- 采用不同策略尝试恢复
- 支持多轮重试和URL修复
"""

import json
import asyncio
import argparse
import random
import time
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urlparse, urljoin
from typing import List, Dict, Tuple, Optional

try:
    import aiohttp
except ImportError:
    print("请先安装 aiohttp: pip install aiohttp")
    exit(1)

# 配置常量
DEFAULT_TIMEOUT = 15
CONCURRENCY = 10
MAX_RETRIES = 3

# User-Agent 池
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
]

class SmartRecovery:
    def __init__(self, timeout: int = DEFAULT_TIMEOUT):
        self.timeout = timeout
        self.recovery_stats = {
            'timeout': {'attempted': 0, 'recovered': 0},
            '403': {'attempted': 0, 'recovered': 0},
            '404': {'attempted': 0, 'recovered': 0},
            '503/502': {'attempted': 0, 'recovered': 0},
            'other': {'attempted': 0, 'recovered': 0}
        }

    def classify_error(self, error: str) -> str:
        """分类错误类型"""
        if "超时" in error or "timeout" in error.lower():
            return "timeout"
        elif "403" in error:
            return "403"
        elif "404" in error:
            return "404"
        elif "503" in error or "502" in error:
            return "503/502"
        else:
            return "other"

    async def handle_timeout_errors(self, session: aiohttp.ClientSession, source: dict) -> Tuple[bool, str]:
        """处理超时错误：增加超时时间，多次重试"""
        url = source.get("bookSourceUrl", "")

        # 逐步增加超时时间：15s -> 30s -> 60s
        timeouts = [15, 30, 60]

        for i, timeout in enumerate(timeouts):
            try:
                await asyncio.sleep(random.uniform(1, 3))  # 随机延迟

                async with session.head(
                    url,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    allow_redirects=True
                ) as resp:
                    if resp.status < 400:
                        return True, f"超时恢复成功(第{i+1}次尝试,{timeout}s)"

            except asyncio.TimeoutError:
                if i == len(timeouts) - 1:
                    return False, f"超时恢复失败(已尝试{len(timeouts)}次)"
                continue
            except Exception as e:
                return False, f"超时恢复异常: {str(e)[:50]}"

        return False, "超时恢复失败"

    async def handle_anti_crawler(self, session: aiohttp.ClientSession, source: dict) -> Tuple[bool, str]:
        """处理反爬虫(403)：轮换User-Agent和请求头"""
        url = source.get("bookSourceUrl", "")

        for i in range(3):
            try:
                headers = {
                    'User-Agent': random.choice(USER_AGENTS),
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                    'Accept-Encoding': 'gzip, deflate',
                    'DNT': '1',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                }

                await asyncio.sleep(random.uniform(2, 5))  # 随机延迟

                async with session.head(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                    allow_redirects=True
                ) as resp:
                    if resp.status < 400:
                        return True, f"反爬虫恢复成功(第{i+1}次尝试)"

            except Exception as e:
                if i == 2:
                    return False, f"反爬虫恢复失败: {str(e)[:50]}"
                continue

        return False, "反爬虫恢复失败"

    async def handle_not_found(self, session: aiohttp.ClientSession, source: dict) -> Tuple[bool, str]:
        """处理404错误：尝试根域名和路径修复"""
        url = source.get("bookSourceUrl", "")
        parsed = urlparse(url)

        # 尝试的URL变体
        url_variants = []

        # 1. 根域名
        root_url = f"{parsed.scheme}://{parsed.netloc}"
        url_variants.append(root_url)

        # 2. 常见路径
        common_paths = ['/', '/index.html', '/index.php', '/api', '/book']
        for path in common_paths:
            url_variants.append(f"{root_url}{path}")

        # 3. HTTPS/HTTP 切换
        if parsed.scheme == 'http':
            https_url = url.replace('http://', 'https://', 1)
            url_variants.append(https_url)
        elif parsed.scheme == 'https':
            http_url = url.replace('https://', 'http://', 1)
            url_variants.append(http_url)

        for i, test_url in enumerate(url_variants):
            try:
                await asyncio.sleep(random.uniform(1, 2))

                async with session.head(
                    test_url,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                    allow_redirects=True
                ) as resp:
                    if resp.status < 400:
                        # 更新书源URL
                        source["bookSourceUrl"] = test_url
                        return True, f"URL修复成功: {test_url}"

            except Exception:
                continue

        return False, "URL修复失败"

    async def handle_server_errors(self, session: aiohttp.ClientSession, source: dict) -> Tuple[bool, str]:
        """处理服务器错误(503/502)：延迟重试"""
        url = source.get("bookSourceUrl", "")

        # 延迟重试：5分钟、30分钟、2小时
        delays = [300, 1800, 7200]  # 秒

        for i, delay in enumerate(delays):
            if i > 0:  # 第一次不延迟
                print(f"等待 {delay//60} 分钟后重试...")
                await asyncio.sleep(min(delay, 60))  # 测试时最多等待1分钟

            try:
                async with session.head(
                    url,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                    allow_redirects=True
                ) as resp:
                    if resp.status < 400:
                        return True, f"服务器错误恢复成功(第{i+1}次尝试)"

            except Exception as e:
                if i == len(delays) - 1:
                    return False, f"服务器错误恢复失败: {str(e)[:50]}"
                continue

        return False, "服务器错误恢复失败"

    async def recover_source(self, session: aiohttp.ClientSession, source: dict, error: str) -> Tuple[dict, bool, str]:
        """恢复单个书源"""
        error_type = self.classify_error(error)
        self.recovery_stats[error_type]['attempted'] += 1

        recovery_strategies = {
            'timeout': self.handle_timeout_errors,
            '403': self.handle_anti_crawler,
            '404': self.handle_not_found,
            '503/502': self.handle_server_errors,
        }

        strategy = recovery_strategies.get(error_type)
        if not strategy:
            return source, False, f"未知错误类型: {error}"

        try:
            success, message = await strategy(session, source)
            if success:
                self.recovery_stats[error_type]['recovered'] += 1
                return source, True, message
            else:
                return source, False, message
        except Exception as e:
            return source, False, f"恢复异常: {str(e)[:50]}"

    async def batch_recovery(self, invalid_sources: List[dict], errors: Dict[str, str]) -> Tuple[List[dict], List[dict]]:
        """批量恢复失效书源"""
        recovered = []
        still_invalid = []

        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def recover_with_semaphore(session, source, error):
            async with semaphore:
                return await self.recover_source(session, source, error)

        connector = aiohttp.TCPConnector(limit=CONCURRENCY, ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = []

            for source in invalid_sources:
                url = source.get("bookSourceUrl", "")
                error = errors.get(url, "未知错误")
                tasks.append(recover_with_semaphore(session, source, error))

            total = len(tasks)
            completed = 0

            for coro in asyncio.as_completed(tasks):
                source, success, message = await coro
                completed += 1

                if success:
                    recovered.append(source)
                    print(f"✓ 恢复成功: {source.get('bookSourceName', 'Unknown')} - {message}")
                else:
                    still_invalid.append(source)

                # 进度显示
                if completed % 5 == 0 or completed == total:
                    print(f"\r恢复进度：{completed}/{total} ({completed*100//total}%)", end="", flush=True)

        print()
        return recovered, still_invalid

    def print_recovery_stats(self):
        """打印恢复统计"""
        print("\n=== 恢复统计 ===")
        total_attempted = sum(stats['attempted'] for stats in self.recovery_stats.values())
        total_recovered = sum(stats['recovered'] for stats in self.recovery_stats.values())

        for error_type, stats in self.recovery_stats.items():
            if stats['attempted'] > 0:
                success_rate = stats['recovered'] * 100 // stats['attempted']
                print(f"{error_type:>8}: {stats['recovered']:>3}/{stats['attempted']:>3} ({success_rate:>2}%)")

        if total_attempted > 0:
            overall_rate = total_recovered * 100 // total_attempted
            print(f"{'总计':>8}: {total_recovered:>3}/{total_attempted:>3} ({overall_rate:>2}%)")

async def main():
    parser = argparse.ArgumentParser(description="智能书源恢复系统")
    parser.add_argument("--invalid", "-i", required=True, help="失效书源文件路径")
    parser.add_argument("--errors", "-e", required=True, help="错误报告文件路径")
    parser.add_argument("--output", "-o", help="恢复书源输出路径")
    parser.add_argument("--still-invalid", help="仍然失效的书源输出路径")
    parser.add_argument("--timeout", "-t", type=int, default=DEFAULT_TIMEOUT, help=f"超时时间（秒），默认 {DEFAULT_TIMEOUT}")
    parser.add_argument("--test-mode", action="store_true", help="测试模式：只处理前10个书源")
    args = parser.parse_args()

    # 读取失效书源
    invalid_path = Path(args.invalid)
    if not invalid_path.exists():
        print(f"错误：失效书源文件不存在 {invalid_path}")
        return 1

    with open(invalid_path, "r", encoding="utf-8") as f:
        invalid_sources = json.load(f)

    # 读取错误报告
    errors_path = Path(args.errors)
    if not errors_path.exists():
        print(f"错误：错误报告文件不存在 {errors_path}")
        return 1

    with open(errors_path, "r", encoding="utf-8") as f:
        error_report = json.load(f)
        errors = error_report.get("errors", {})

    # 测试模式
    if args.test_mode:
        invalid_sources = invalid_sources[:10]
        print(f"测试模式：只处理前 {len(invalid_sources)} 个书源")

    print(f"读取失效书源：{len(invalid_sources)} 个")
    print(f"开始智能恢复...")
    print()

    # 执行恢复
    recovery = SmartRecovery(args.timeout)
    recovered, still_invalid = await recovery.batch_recovery(invalid_sources, errors)

    # 输出结果
    print(f"\n恢复结果：")
    print(f"  恢复成功：{len(recovered)} 个")
    print(f"  仍然失效：{len(still_invalid)} 个")
    print(f"  恢复率：{len(recovered)*100//(len(recovered)+len(still_invalid)) if recovered or still_invalid else 0}%")

    recovery.print_recovery_stats()

    # 保存恢复的书源
    if args.output and recovered:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(recovered, f, ensure_ascii=False, indent=2)
        print(f"\n恢复书源输出到：{output_path}")

    # 保存仍然失效的书源
    if args.still_invalid and still_invalid:
        still_invalid_path = Path(args.still_invalid)
        still_invalid_path.parent.mkdir(parents=True, exist_ok=True)
        with open(still_invalid_path, "w", encoding="utf-8") as f:
            json.dump(still_invalid, f, ensure_ascii=False, indent=2)
        print(f"仍然失效书源输出到：{still_invalid_path}")

    return 0

if __name__ == "__main__":
    exit(asyncio.run(main()))