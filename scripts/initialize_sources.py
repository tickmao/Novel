#!/usr/bin/env python3
"""
初始化整合脚本 - Phase 1 初始化流程
- 整合快速过滤、分批验证、评分排序、智能选择
- 检查点机制（支持断点续传）
- 失败恢复
- 完整的初始化流程：16,724 → 1,000
"""

import json
import asyncio
import argparse
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

# 添加 scripts 目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from batch_validator import BatchValidator
from source_selector import SourceSelector
from safe_updater import SafeUpdater
from source_inventory import SourceInventory


class InitializeSources:
    """初始化整合脚本"""

    def __init__(self, base_dir: Path = None):
        """
        初始化

        Args:
            base_dir: 基础目录，默认为 sources/legado
        """
        if base_dir is None:
            base_dir = Path(__file__).parent.parent / 'sources' / 'legado'

        self.base_dir = Path(base_dir)
        self.pool_dir = self.base_dir / 'pool'
        self.main_dir = self.base_dir / 'main'
        self.temp_dir = self.base_dir / 'temp'

        # 文件路径
        self.raw_file = self.pool_dir / 'raw.json'
        self.candidates_file = self.pool_dir / 'candidates.json'
        self.invalid_file = self.pool_dir / 'invalid.json'
        self.screened_file = self.pool_dir / 'screened.json'
        self.main_file = self.main_dir / 'full.json'
        self.inventory = SourceInventory(self.base_dir)

        # 检查点目录
        self.checkpoint_dir = self.temp_dir / 'checkpoints'
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def quick_filter(self, sources: List[Dict]) -> List[Dict]:
        """
        快速过滤：类型、关键词、字段完整性

        Args:
            sources: 原始书源列表

        Returns:
            过滤后的书源列表
        """
        print('\n=== 步骤 1：快速过滤 ===')
        print(f'原始书源：{len(sources)} 个')

        filtered, report = self.inventory.refresh_screened_pool(sources, save=False)
        print(f'静态筛选剔除：{report["rejected"]} 个')
        print(f'主要原因：{report["reasons"]}')

        with open(self.screened_file, 'w', encoding='utf-8') as f:
            json.dump(filtered, f, ensure_ascii=False, indent=2)
        print(f'✓ screened pool 已保存：{self.screened_file}')

        print(f'过滤后书源：{len(filtered)} 个')
        print(f'过滤率：{(1 - len(filtered)/len(sources))*100:.1f}%')

        return filtered

    async def run_validation(
        self,
        sources: List[Dict],
        batch_size: int = 200,
        concurrency: int = 20,
        timeout: int = 10
    ) -> tuple:
        """
        运行分批验证

        Args:
            sources: 书源列表
            batch_size: 批次大小
            concurrency: 并发数
            timeout: 超时时间

        Returns:
            (有效书源, 无效书源, 统计信息)
        """
        print('\n=== 步骤 2：分批验证 ===')

        validator = BatchValidator(
            batch_size=batch_size,
            concurrency=concurrency,
            timeout=timeout,
            checkpoint_dir=self.checkpoint_dir
        )

        valid, invalid, stats = await validator.validate_all(sources, resume=False)

        return valid, invalid, stats

    def run_selection(
        self,
        sources: List[Dict],
        target: int = 1000,
        max_per_domain: int = 2
    ) -> tuple:
        """
        运行智能选择

        Args:
            sources: 候选书源列表
            target: 目标数量
            max_per_domain: 每域名最多书源数

        Returns:
            (选中书源, 统计信息)
        """
        print('\n=== 步骤 3：智能选择 ===')

        selector = SourceSelector(
            max_per_domain=max_per_domain,
            target_count=target
        )

        selected, stats = selector.select(sources, strategy='domain_diversity')

        return selected, stats

    def safe_update(self, sources: List[Dict], skip_validation: bool = False) -> bool:
        """
        安全更新主书源文件

        Args:
            sources: 新书源列表
            skip_validation: 跳过验证（仅测试）

        Returns:
            是否成功
        """
        print('\n=== 步骤 4：安全更新 ===')

        updater = SafeUpdater(base_dir=self.base_dir)

        try:
            return updater.safe_update(sources, skip_validation=skip_validation)
        except Exception as e:
            print(f'✗ 安全更新失败：{e}')
            return False

    async def initialize(
        self,
        skip_filter: bool = False,
        skip_validation: bool = False,
        target: int = 1000,
        batch_size: int = 200,
        concurrency: int = 20,
        timeout: int = 10,
        max_per_domain: int = 2
    ) -> bool:
        """
        执行完整初始化流程

        Args:
            skip_filter: 跳过快速过滤
            skip_validation: 跳过验证（仅用于测试）
            target: 目标书源数量
            batch_size: 验证批次大小
            concurrency: 验证并发数
            timeout: 验证超时时间
            max_per_domain: 每域名最多书源数

        Returns:
            是否成功
        """
        print('\n' + '='*60)
        print('初始化流程开始')
        print('='*60)
        print(f'时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
        print(f'目标：从 {self.raw_file.name} 筛选出 {target} 个高质量书源')
        print('='*60)

        try:
            # 读取原始书源
            print(f'\n读取原始书源：{self.raw_file}')
            with open(self.raw_file, 'r', encoding='utf-8') as f:
                raw_sources = json.load(f)
            print(f'✓ 读取成功：{len(raw_sources)} 个书源')

            # 步骤 1：快速过滤
            if skip_filter:
                print('\n⚠ 跳过快速过滤')
                filtered_sources = raw_sources
            else:
                filtered_sources = self.quick_filter(raw_sources)

            # 步骤 2：分批验证
            if skip_validation:
                print('\n⚠ 跳过分批验证')
                valid_sources = filtered_sources
                invalid_sources = []
            else:
                valid_sources, invalid_sources, validation_stats = await self.run_validation(
                    filtered_sources,
                    batch_size=batch_size,
                    concurrency=concurrency,
                    timeout=timeout
                )

                # 保存候选书源
                with open(self.candidates_file, 'w', encoding='utf-8') as f:
                    json.dump(valid_sources, f, ensure_ascii=False, indent=2)
                print(f'\n✓ 候选书源已保存：{self.candidates_file}')

                # 保存无效书源
                with open(self.invalid_file, 'w', encoding='utf-8') as f:
                    json.dump(invalid_sources, f, ensure_ascii=False, indent=2)
                print(f'✓ 无效书源已保存：{self.invalid_file}')

            # 步骤 3：智能选择
            selected_sources, selection_stats = self.run_selection(
                valid_sources,
                target=target,
                max_per_domain=max_per_domain
            )

            # 步骤 4：安全更新
            if self.safe_update(selected_sources, skip_validation=skip_validation):
                print('\n' + '='*60)
                print('初始化流程完成')
                print('='*60)
                print(f'原始书源：{len(raw_sources)} 个')
                if not skip_filter:
                    print(f'快速过滤：{len(filtered_sources)} 个')
                if not skip_validation:
                    print(f'有效书源：{len(valid_sources)} 个')
                print(f'最终选择：{len(selected_sources)} 个')
                print(f'唯一域名：{selection_stats["unique_domains"]} 个')
                print(f'平均评分：{selection_stats["avg_score"]:.1f}')
                print('='*60)
                return True
            else:
                print('\n✗ 初始化流程失败：安全更新失败')
                return False

        except Exception as e:
            print(f'\n✗ 初始化流程异常：{e}')
            import traceback
            traceback.print_exc()
            return False


async def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description='初始化整合脚本 - Phase 1 初始化流程')
    parser.add_argument('--base-dir', type=Path, help='基础目录')
    parser.add_argument('--target', type=int, default=1000, help='目标书源数量')
    parser.add_argument('--batch-size', type=int, default=200, help='验证批次大小')
    parser.add_argument('--concurrency', type=int, default=20, help='验证并发数')
    parser.add_argument('--timeout', type=int, default=10, help='验证超时时间（秒）')
    parser.add_argument('--max-per-domain', type=int, default=2, help='每域名最多书源数')
    parser.add_argument('--skip-filter', action='store_true', help='跳过快速过滤')
    parser.add_argument('--skip-validation', action='store_true', help='跳过验证（仅测试）')

    args = parser.parse_args()

    # 创建初始化器
    initializer = InitializeSources(base_dir=args.base_dir)

    # 执行初始化
    success = await initializer.initialize(
        skip_filter=args.skip_filter,
        skip_validation=args.skip_validation,
        target=args.target,
        batch_size=args.batch_size,
        concurrency=args.concurrency,
        timeout=args.timeout,
        max_per_domain=args.max_per_domain
    )

    if success:
        print('\n✓ 初始化成功')
        sys.exit(0)
    else:
        print('\n✗ 初始化失败')
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
