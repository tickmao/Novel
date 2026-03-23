#!/usr/bin/env python3
"""
书源自动补充主控制器。

优先使用内部候选池补齐库存；不足时再从 screened pool 做增量验证，
最后才触发外部收集。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Dict, List, Tuple

from batch_validator import BatchValidator
from source_collector import SourceCollector
from source_inventory import SourceInventory


class AutoSupplement:
    def __init__(self, base_dir: Path, export_target: int | None = None):
        self.base_dir = base_dir
        self.inventory = SourceInventory(base_dir / "sources" / "legado")
        self.policy = self.inventory.policy

        config = json.loads((base_dir / "config" / "supplement_config.json").read_text(encoding="utf-8"))
        supplement_cfg = config.get("supplement", {})
        validation_cfg = config.get("validation", {})
        collection_cfg = config.get("collection", {})

        self.export_target = export_target or self.inventory.export_target
        self.working_target = max(self.inventory.working_target, self.export_target + 30)
        self.min_working_sources = self.inventory.min_working_sources
        self.inventory.export_target = self.export_target
        self.inventory.working_target = self.working_target
        self.validation_timeout = int(validation_cfg.get("timeout", 10))
        self.validation_concurrency = int(validation_cfg.get("concurrency", 20))
        self.collection_timeout = int(collection_cfg.get("timeout", 30))

        self.stats = {
            "initial_working": 0,
            "initial_candidates": 0,
            "screened_batch": 0,
            "screened_valid": 0,
            "external_collected": 0,
            "external_large_files": 0,
            "external_valid": 0,
            "final_working": 0,
            "final_export": 0,
        }

    async def validate_sources(self, sources: List[Dict]) -> Tuple[List[Dict], List[Dict], Dict]:
        if not sources:
            return [], [], {"valid": 0, "invalid": 0}

        validator = BatchValidator(
            batch_size=len(sources),
            concurrency=self.validation_concurrency,
            timeout=self.validation_timeout,
            checkpoint_dir=None,
        )
        return await validator.validate_batch(sources, batch_num=1, total_batches=1)

    async def replenish_from_screened(
        self,
        current_urls: set,
        target_gap: int,
        *,
        save: bool = True,
    ) -> Tuple[List[Dict], List[Dict], Dict]:
        screened_sources = self.inventory.load_screened_sources()
        if not screened_sources:
            screened_sources, _ = self.inventory.refresh_screened_pool(save=save)

        batch = self.inventory.select_screened_validation_batch(
            existing_urls=current_urls,
            target_gap=target_gap,
            screened_sources=screened_sources,
        )
        self.stats["screened_batch"] = len(batch)

        if not batch:
            return [], [], {"valid": 0, "invalid": 0}

        valid, invalid, stats = await self.validate_sources(batch)
        self.stats["screened_valid"] = len(valid)
        return valid, invalid, stats

    async def replenish_from_external(self, current_urls: set, target_gap: int) -> Tuple[List[Dict], List[Dict], Dict]:
        collector = SourceCollector(timeout=self.collection_timeout)
        large_files = []
        yiove_file = self.inventory.base_dir / "yiove_new.json"
        if yiove_file.exists():
            large_files.append(yiove_file)

        collected = await collector.collect_all_sources(large_files)
        self.stats["external_collected"] = len(collected)
        self.stats["external_large_files"] = len(large_files)

        accepted, _, _ = self.policy.screen_sources(collected)
        filtered = [
            source for source in accepted
            if str(source.get("bookSourceUrl", "")).strip() not in current_urls
        ][: max(target_gap * 2, 200)]

        valid, invalid, stats = await self.validate_sources(filtered)
        self.stats["external_valid"] = len(valid)
        return valid, invalid, stats

    async def auto_supplement_workflow(self, force: bool = False, dry_run: bool = False) -> bool:
        working_sources = self.inventory.load_working_sources()
        candidate_sources, _ = self.inventory.refresh_candidate_pool(save=not dry_run)

        self.stats["initial_working"] = len(working_sources)
        self.stats["initial_candidates"] = len(candidate_sources)

        print("=== 书源自动补充 ===")
        print(f"当前工作库存：{len(working_sources)}")
        print(f"当前候选池：{len(candidate_sources)}")
        print(f"目标工作库存：{self.working_target}")
        print(f"对外导出数量：{self.export_target}")

        rebuilt_working, rebuilt_export, rebuilt_report = self.inventory.build_inventory(
            working_sources,
            candidate_sources,
            save=False,
        )
        print(f"内部整理后工作库存：{len(rebuilt_working)}")

        if not force and len(rebuilt_working) >= self.min_working_sources:
            print("库存充足，无需补源")
            if not dry_run and (
                len(working_sources) != len(rebuilt_working)
                or len(rebuilt_export) != self.export_target
            ):
                self.inventory.build_inventory(working_sources, candidate_sources, save=True)
            self.stats["final_working"] = len(rebuilt_working)
            self.stats["final_export"] = len(rebuilt_export)
            return False

        current_urls = {
            str(source.get("bookSourceUrl", "")).strip()
            for source in rebuilt_working + candidate_sources
        }
        target_gap = max(self.working_target - len(rebuilt_working), 0)

        print(f"当前库存低于阈值，需补充约 {target_gap} 条")

        screened_valid, screened_invalid, _ = await self.replenish_from_screened(
            current_urls,
            target_gap,
            save=not dry_run,
        )
        if screened_valid:
            candidate_sources, _ = self.inventory.merge_validated_candidates(
                screened_valid,
                screened_invalid,
                save=not dry_run,
            )

        rebuilt_working, rebuilt_export, _ = self.inventory.build_inventory(
            rebuilt_working,
            candidate_sources,
            save=False,
        )

        if len(rebuilt_working) < self.min_working_sources:
            current_urls = {
                str(source.get("bookSourceUrl", "")).strip()
                for source in rebuilt_working + candidate_sources
            }
            target_gap = max(self.working_target - len(rebuilt_working), 0)
            print(f"内部候选池不足，启动外部补源，缺口 {target_gap}")
            external_valid, external_invalid, _ = await self.replenish_from_external(current_urls, target_gap)
            if external_valid:
                candidate_sources, _ = self.inventory.merge_validated_candidates(
                    external_valid,
                    external_invalid,
                    save=not dry_run,
                )
                rebuilt_working, rebuilt_export, _ = self.inventory.build_inventory(
                    rebuilt_working,
                    candidate_sources,
                    save=False,
                )

        self.stats["final_working"] = len(rebuilt_working)
        self.stats["final_export"] = len(rebuilt_export)

        if dry_run:
            print("试运行模式：未写入文件")
            return len(rebuilt_working) >= self.min_working_sources

        self.inventory.build_inventory(rebuilt_working, candidate_sources, save=True)
        return True

    def print_stats(self):
        print("\n=== 补源统计 ===")
        for key, value in self.stats.items():
            print(f"{key}: {value}")


async def main():
    parser = argparse.ArgumentParser(description="书源自动补充主控制器")
    parser.add_argument("--force", "-f", action="store_true", help="强制执行补源")
    parser.add_argument("--dry-run", action="store_true", help="试运行，不写入文件")
    parser.add_argument("--target", "-t", type=int, help="对外导出目标数量，默认读取配置")
    args = parser.parse_args()

    base_dir = Path(__file__).parent.parent
    supplement = AutoSupplement(base_dir, export_target=args.target)

    success = await supplement.auto_supplement_workflow(force=args.force, dry_run=args.dry_run)
    supplement.print_stats()

    if success:
        print("\n✓ 自动补充完成")
        return 0

    print("\n- 无需补充")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
