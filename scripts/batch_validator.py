#!/usr/bin/env python3
"""
分批验证器 - 避免超时
- 分批验证书源（避免一次性验证过多导致超时）
- 并发控制（20个并发）
- 结果记录和统计
- 检查点机制（支持断点续传）
"""

import json
import asyncio
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple, Optional

try:
    import aiohttp
except ImportError:
    print("请先安装 aiohttp: pip install aiohttp")
    exit(1)


class BatchValidator:
    """分批验证器"""

    # 默认配置
    DEFAULT_BATCH_SIZE = 200
    DEFAULT_CONCURRENCY = 20
    DEFAULT_TIMEOUT = 10

    def __init__(
        self,
        batch_size: int = DEFAULT_BATCH_SIZE,
        concurrency: int = DEFAULT_CONCURRENCY,
        timeout: int = DEFAULT_TIMEOUT,
        checkpoint_dir: Optional[Path] = None
    ):
        """
        初始化分批验证器

        Args:
            batch_size: 每批验证的书源数量
            concurrency: 并发数量
            timeout: 超时时间（秒）
            checkpoint_dir: 检查点目录
        """
        self.batch_size = batch_size
        self.concurrency = concurrency
        self.timeout = timeout
        self.checkpoint_dir = checkpoint_dir

        if checkpoint_dir:
            checkpoint_dir.mkdir(parents=True, exist_ok=True)

    async def check_source(
        self,
        session: aiohttp.ClientSession,
        source: Dict
    ) -> Tuple[Dict, bool, Optional[str], Optional[int]]:
        """
        检查单个书源是否有效

        Args:
            session: aiohttp 会话
            source: 书源字典

        Returns:
            (书源, 是否有效, 错误信息, 响应时间ms)
        """
        url = source.get('bookSourceUrl', '')
        if not url:
            return source, False, 'URL 为空', None

        start_time = asyncio.get_event_loop().time()

        try:
            async with session.head(
                url,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                allow_redirects=True
            ) as resp:
                elapsed = int((asyncio.get_event_loop().time() - start_time) * 1000)

                if resp.status < 400:
                    return source, True, None, elapsed
                else:
                    return source, False, f'HTTP {resp.status}', elapsed

        except asyncio.TimeoutError:
            return source, False, '超时', None
        except aiohttp.ClientError as e:
            return source, False, str(e)[:50], None
        except Exception as e:
            return source, False, str(e)[:50], None

    async def validate_batch(
        self,
        sources: List[Dict],
        batch_num: int,
        total_batches: int
    ) -> Tuple[List[Dict], List[Dict], Dict]:
        """
        验证一批书源

        Args:
            sources: 书源列表
            batch_num: 当前批次号（从1开始）
            total_batches: 总批次数

        Returns:
            (有效书源, 无效书源, 统计信息)
        """
        valid = []
        invalid = []
        errors = {}
        response_times = []

        # 创建信号量控制并发
        semaphore = asyncio.Semaphore(self.concurrency)

        async def check_with_semaphore(session, source):
            async with semaphore:
                return await self.check_source(session, source)

        connector = aiohttp.TCPConnector(limit=self.concurrency, ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [check_with_semaphore(session, s) for s in sources]

            total = len(tasks)
            completed = 0

            for coro in asyncio.as_completed(tasks):
                source, is_valid, error, response_time = await coro
                completed += 1

                if is_valid:
                    valid.append(source)
                    if response_time:
                        response_times.append(response_time)
                        # 记录响应时间到书源
                        source['_response_time'] = response_time
                else:
                    invalid.append(source)
                    errors[source.get('bookSourceUrl', '')] = error

                # 进度显示
                if completed % 10 == 0 or completed == total:
                    print(
                        f'\r批次 {batch_num}/{total_batches} - '
                        f'进度：{completed}/{total} ({completed*100//total}%)',
                        end='',
                        flush=True
                    )

        print()  # 换行

        # 统计信息
        stats = {
            'batch_num': batch_num,
            'total': len(sources),
            'valid': len(valid),
            'invalid': len(invalid),
            'valid_rate': len(valid) / len(sources) if sources else 0,
            'avg_response_time': sum(response_times) / len(response_times) if response_times else None
        }

        return valid, invalid, stats

    def save_checkpoint(
        self,
        batch_num: int,
        valid_sources: List[Dict],
        invalid_sources: List[Dict],
        all_stats: List[Dict]
    ):
        """
        保存检查点

        Args:
            batch_num: 当前批次号
            valid_sources: 累计有效书源
            invalid_sources: 累计无效书源
            all_stats: 所有批次统计
        """
        if not self.checkpoint_dir:
            return

        checkpoint_file = self.checkpoint_dir / f'checkpoint_batch_{batch_num}.json'

        checkpoint_data = {
            'batch_num': batch_num,
            'timestamp': datetime.now().isoformat(),
            'valid_count': len(valid_sources),
            'invalid_count': len(invalid_sources),
            'stats': all_stats
        }

        with open(checkpoint_file, 'w', encoding='utf-8') as f:
            json.dump(checkpoint_data, f, ensure_ascii=False, indent=2)

        print(f'✓ 检查点已保存：{checkpoint_file.name}')

    def load_checkpoint(self) -> Optional[Dict]:
        """
        加载最新检查点

        Returns:
            检查点数据，如果没有则返回 None
        """
        if not self.checkpoint_dir or not self.checkpoint_dir.exists():
            return None

        checkpoints = sorted(
            self.checkpoint_dir.glob('checkpoint_batch_*.json'),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        if not checkpoints:
            return None

        with open(checkpoints[0], 'r', encoding='utf-8') as f:
            return json.load(f)

    async def validate_all(
        self,
        sources: List[Dict],
        resume: bool = False
    ) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        """
        分批验证所有书源

        Args:
            sources: 书源列表
            resume: 是否从检查点恢复

        Returns:
            (有效书源, 无效书源, 统计信息列表)
        """
        print(f'\n=== 分批验证开始 ===')
        print(f'总书源数：{len(sources)}')
        print(f'批次大小：{self.batch_size}')
        print(f'并发数量：{self.concurrency}')
        print(f'超时时间：{self.timeout}秒')

        # 计算批次数
        total_batches = (len(sources) + self.batch_size - 1) // self.batch_size
        print(f'总批次数：{total_batches}\n')

        all_valid = []
        all_invalid = []
        all_stats = []

        start_batch = 1

        # 从检查点恢复
        if resume:
            checkpoint = self.load_checkpoint()
            if checkpoint:
                start_batch = checkpoint['batch_num'] + 1
                print(f'从检查点恢复：批次 {checkpoint["batch_num"]}')
                print(f'已验证：{checkpoint["valid_count"]} 有效，{checkpoint["invalid_count"]} 无效\n')

        # 分批验证
        for i in range(start_batch - 1, total_batches):
            batch_num = i + 1
            start_idx = i * self.batch_size
            end_idx = min((i + 1) * self.batch_size, len(sources))
            batch_sources = sources[start_idx:end_idx]

            print(f'批次 {batch_num}/{total_batches}：验证 {len(batch_sources)} 个书源')

            valid, invalid, stats = await self.validate_batch(
                batch_sources,
                batch_num,
                total_batches
            )

            all_valid.extend(valid)
            all_invalid.extend(invalid)
            all_stats.append(stats)

            # 显示批次统计
            print(f'  有效：{stats["valid"]} 个 ({stats["valid_rate"]*100:.1f}%)')
            if stats['avg_response_time']:
                print(f'  平均响应：{stats["avg_response_time"]:.0f}ms')

            # 保存检查点
            if self.checkpoint_dir:
                self.save_checkpoint(batch_num, all_valid, all_invalid, all_stats)

            print()

        print('=== 分批验证完成 ===\n')

        return all_valid, all_invalid, all_stats

    def print_summary(self, stats_list: List[Dict]):
        """
        打印验证摘要

        Args:
            stats_list: 统计信息列表
        """
        total = sum(s['total'] for s in stats_list)
        valid = sum(s['valid'] for s in stats_list)
        invalid = sum(s['invalid'] for s in stats_list)

        print('=== 验证摘要 ===')
        print(f'总书源数：{total}')
        print(f'有效书源：{valid} ({valid*100/total:.1f}%)')
        print(f'无效书源：{invalid} ({invalid*100/total:.1f}%)')

        # 平均响应时间
        response_times = [s['avg_response_time'] for s in stats_list if s['avg_response_time']]
        if response_times:
            avg_response = sum(response_times) / len(response_times)
            print(f'平均响应：{avg_response:.0f}ms')

        print()


async def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description='分批验证器 - 避免超时')
    parser.add_argument('--input', type=Path, required=True, help='输入文件')
    parser.add_argument('--output-valid', type=Path, help='有效书源输出文件')
    parser.add_argument('--output-invalid', type=Path, help='无效书源输出文件')
    parser.add_argument('--batch-size', type=int, default=200, help='每批验证数量')
    parser.add_argument('--concurrency', type=int, default=20, help='并发数量')
    parser.add_argument('--timeout', type=int, default=10, help='超时时间（秒）')
    parser.add_argument('--checkpoint-dir', type=Path, help='检查点目录')
    parser.add_argument('--resume', action='store_true', help='从检查点恢复')
    parser.add_argument('--report', type=Path, help='统计报告输出文件')

    args = parser.parse_args()

    # 读取书源
    with open(args.input, 'r', encoding='utf-8') as f:
        sources = json.load(f)

    # 创建验证器
    validator = BatchValidator(
        batch_size=args.batch_size,
        concurrency=args.concurrency,
        timeout=args.timeout,
        checkpoint_dir=args.checkpoint_dir
    )

    # 执行验证
    valid, invalid, stats = await validator.validate_all(sources, resume=args.resume)

    # 打印摘要
    validator.print_summary(stats)

    # 保存结果
    if args.output_valid:
        with open(args.output_valid, 'w', encoding='utf-8') as f:
            json.dump(valid, f, ensure_ascii=False, indent=2)
        print(f'✓ 有效书源已保存：{args.output_valid}')

    if args.output_invalid:
        with open(args.output_invalid, 'w', encoding='utf-8') as f:
            json.dump(invalid, f, ensure_ascii=False, indent=2)
        print(f'✓ 无效书源已保存：{args.output_invalid}')

    # 保存统计报告
    if args.report:
        report = {
            'timestamp': datetime.now().isoformat(),
            'input_file': str(args.input),
            'total_sources': len(sources),
            'valid_sources': len(valid),
            'invalid_sources': len(invalid),
            'valid_rate': len(valid) / len(sources) if sources else 0,
            'batch_stats': stats
        }

        with open(args.report, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f'✓ 统计报告已保存：{args.report}')


if __name__ == '__main__':
    asyncio.run(main())
