#!/usr/bin/env python3
"""
候选池、工作库存与对外导出的统一管理器。
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from legado_paths import (
    candidate_pool_file,
    candidate_report_file,
    canonical_source_file,
    metadata_file,
    raw_pool_file,
    resolve_legado_dir,
    screened_pool_file,
    screened_report_file,
    working_source_file,
)
from safe_updater import SafeUpdater
from source_policy import SourcePolicy
from source_selector import SourceSelector
from update_sources import SourceUpdater


def _load_json(path: Path, default):
    if not path.exists():
        return deepcopy(default)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class SourceInventory:
    """维护 screened/candidates/working/export 四层数据。"""

    def __init__(self, base_dir: Path | str | None = None):
        self.base_dir = resolve_legado_dir(base_dir)
        self.project_root = self.base_dir.parent.parent

        self.policy = SourcePolicy(self.project_root)
        self.updater = SafeUpdater(self.base_dir)

        self.raw_file = raw_pool_file(self.base_dir)
        self.screened_file = screened_pool_file(self.base_dir)
        self.screened_report = screened_report_file(self.base_dir)
        self.candidate_file = candidate_pool_file(self.base_dir)
        self.candidate_report = candidate_report_file(self.base_dir)
        self.working_file = working_source_file(self.base_dir)
        self.export_file = canonical_source_file(self.base_dir)
        self.metadata_file = metadata_file(self.base_dir)

        config = _load_json(self.project_root / "config" / "supplement_config.json", {})
        inventory_cfg = config.get("inventory", {})
        supplement_cfg = config.get("supplement", {})

        self.export_target = int(inventory_cfg.get("export_target", supplement_cfg.get("target_sources", 1000)))
        self.working_target = int(inventory_cfg.get("working_target", self.export_target + 30))
        self.min_working_sources = int(inventory_cfg.get("min_working_sources", 950))
        self.max_working_sources = int(inventory_cfg.get("max_working_sources", 1050))
        self.min_candidate_sources = int(inventory_cfg.get("min_candidate_sources", 1800))
        self.screened_validation_batch = int(inventory_cfg.get("screened_validation_batch", 360))
        self.validation_oversample_factor = int(inventory_cfg.get("validation_oversample_factor", 3))
        self.max_per_domain = int(supplement_cfg.get("max_per_domain", 2))

    def load_raw_sources(self) -> List[Dict]:
        return _load_json(self.raw_file, [])

    def load_screened_sources(self) -> List[Dict]:
        return _load_json(self.screened_file, [])

    def load_candidate_sources(self) -> List[Dict]:
        return _load_json(self.candidate_file, [])

    def load_working_sources(self) -> List[Dict]:
        if self.working_file.exists():
            return _load_json(self.working_file, [])
        return _load_json(self.export_file, [])

    def _write_json(self, path: Path, payload) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _stage_json(self, path: Path, payload) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return temp_path

    def _commit_staged_json(self, temp_path: Path, final_path: Path) -> None:
        os.replace(temp_path, final_path)

    def _discard_staged_json(self, temp_path: Optional[Path]) -> None:
        if temp_path and temp_path.exists():
            temp_path.unlink()

    def _dedupe_by_url(self, sources: List[Dict]) -> List[Dict]:
        best_by_url: Dict[str, Dict] = {}
        for source in sources:
            url = str(source.get("bookSourceUrl", "")).strip()
            if not url:
                continue
            score = float(source.get("selectionScore") or source.get("score") or 0)
            existing = best_by_url.get(url)
            existing_score = float(existing.get("selectionScore") or existing.get("score") or 0) if existing else -1
            if not existing or score >= existing_score:
                best_by_url[url] = source
        return list(best_by_url.values())

    def _sort_sources(self, sources: List[Dict]) -> List[Dict]:
        return sorted(
            sources,
            key=lambda item: (
                -(float(item.get("selectionScore") or item.get("score") or 0)),
                item.get("bookSourceName", ""),
            ),
        )

    def refresh_screened_pool(self, sources: Optional[List[Dict]] = None, save: bool = True) -> Tuple[List[Dict], Dict]:
        source_list = deepcopy(sources if sources is not None else self.load_raw_sources())
        accepted, rejected, stats = self.policy.screen_sources(source_list)
        screened_sources = self._sort_sources(self._dedupe_by_url(accepted))

        report = {
            "timestamp": datetime.now().isoformat(),
            "input": len(source_list),
            "screened": len(screened_sources),
            "rejected": len(rejected),
            "reasons": stats.get("reasons", {}),
        }

        if save:
            self._write_json(self.screened_file, screened_sources)
            self._write_json(self.screened_report, report)

        return screened_sources, report

    def refresh_candidate_pool(self, sources: Optional[List[Dict]] = None, save: bool = True) -> Tuple[List[Dict], Dict]:
        source_list = deepcopy(sources if sources is not None else self.load_candidate_sources())
        accepted, rejected, stats = self.policy.screen_sources(source_list)
        candidates = self._sort_sources(self._dedupe_by_url(accepted))

        report = {
            "timestamp": datetime.now().isoformat(),
            "input": len(source_list),
            "candidates": len(candidates),
            "rejected": len(rejected),
            "reasons": stats.get("reasons", {}),
        }

        if save:
            self._write_json(self.candidate_file, candidates)
            self._write_json(self.candidate_report, report)

        return candidates, report

    def merge_validated_candidates(
        self,
        valid_sources: List[Dict],
        invalid_sources: Optional[List[Dict]] = None,
        *,
        save: bool = True,
    ) -> Tuple[List[Dict], Dict]:
        existing = self.load_candidate_sources()
        invalid_urls = {str(item.get("bookSourceUrl", "")).strip() for item in (invalid_sources or [])}

        merged = [source for source in existing if str(source.get("bookSourceUrl", "")).strip() not in invalid_urls]
        now = datetime.now().isoformat()
        for source in valid_sources:
            candidate = deepcopy(source)
            candidate["_validation_status"] = "valid"
            candidate["_last_validated_at"] = now
            merged.append(candidate)

        return self.refresh_candidate_pool(merged, save=save)

    def select_screened_validation_batch(
        self,
        existing_urls: Optional[set] = None,
        target_gap: int = 0,
        screened_sources: Optional[List[Dict]] = None,
    ) -> List[Dict]:
        existing_urls = existing_urls or set()
        screened = screened_sources or self.load_screened_sources()

        batch_size = max(self.screened_validation_batch, target_gap * self.validation_oversample_factor)
        available = [
            deepcopy(source)
            for source in screened
            if str(source.get("bookSourceUrl", "")).strip() not in existing_urls
        ]
        return self._sort_sources(available)[:batch_size]

    def _apply_existing_bonus(self, current_sources: List[Dict], candidate_sources: List[Dict]) -> List[Dict]:
        current_urls = {str(item.get("bookSourceUrl", "")).strip() for item in current_sources}
        boosted: List[Dict] = []
        for source in candidate_sources:
            entry = deepcopy(source)
            if str(entry.get("bookSourceUrl", "")).strip() in current_urls:
                base = float(entry.get("selectionScore") or entry.get("score") or 0)
                entry["selectionScore"] = round(base + 5, 2)
            boosted.append(entry)
        return boosted

    def build_inventory(
        self,
        current_sources: Optional[List[Dict]] = None,
        candidate_sources: Optional[List[Dict]] = None,
        *,
        save: bool = True,
    ) -> Tuple[List[Dict], List[Dict], Dict]:
        current = deepcopy(current_sources if current_sources is not None else self.load_working_sources())
        candidates = deepcopy(candidate_sources if candidate_sources is not None else self.load_candidate_sources())

        screened_current, _, _ = self.policy.screen_sources(current)
        screened_candidates, _ = self.refresh_candidate_pool(candidates, save=False)

        merged = self._dedupe_by_url(screened_current + screened_candidates)
        boosted = self._apply_existing_bonus(screened_current, merged)

        working_selector = SourceSelector(
            max_per_domain=self.max_per_domain,
            target_count=min(self.working_target, self.max_working_sources),
        )
        working_sources, working_stats = working_selector.select(boosted, strategy="domain_diversity")

        export_selector = SourceSelector(
            max_per_domain=self.max_per_domain,
            target_count=self.export_target,
        )
        export_sources, export_stats = export_selector.select(working_sources, strategy="domain_diversity")

        report = {
            "timestamp": datetime.now().isoformat(),
            "current_input": len(current),
            "candidate_input": len(candidates),
            "working_count": len(working_sources),
            "export_count": len(export_sources),
            "working_stats": working_stats,
            "export_stats": export_stats,
            "needs_replenishment": len(working_sources) < self.min_working_sources,
        }

        if save:
            working_temp: Optional[Path] = None
            metadata_temp: Optional[Path] = None
            try:
                working_temp = self._stage_json(self.working_file, working_sources)

                export_success = self.updater.safe_update(
                    export_sources,
                    skip_validation=len(export_sources) < self.updater.MIN_SOURCES
                )
                if not export_success:
                    raise RuntimeError("导出书源更新失败")

                self._commit_staged_json(working_temp, self.working_file)
                working_temp = None

                stats_sync = self.sync_public_stats()
                report["stats_sync"] = stats_sync
                metadata_payload = self.prepare_metadata(report, stats_sync)
                metadata_temp = self._stage_json(self.metadata_file, metadata_payload)
                self._commit_staged_json(metadata_temp, self.metadata_file)
                metadata_temp = None

                if not stats_sync["success"]:
                    raise RuntimeError(stats_sync["error"] or "README/站点统计同步失败")

            finally:
                self._discard_staged_json(working_temp)
                self._discard_staged_json(metadata_temp)

        return working_sources, export_sources, report

    def prepare_metadata(self, inventory_report: Dict, stats_sync: Optional[Dict] = None) -> Dict:
        metadata = _load_json(self.metadata_file, {})
        metadata["inventory"] = inventory_report
        now = datetime.now().isoformat()
        metadata["last_maintenance"] = now
        metadata["last_inventory_rebuild"] = inventory_report.get("timestamp", now)
        if stats_sync is not None:
            metadata["last_stats_sync"] = stats_sync.get("updated_at")
            metadata["last_stats_sync_status"] = stats_sync.get("status")
            metadata["last_stats_sync_error"] = stats_sync.get("error")
        return metadata

    def update_metadata(self, inventory_report: Dict, stats_sync: Optional[Dict] = None) -> Dict:
        metadata = self.prepare_metadata(inventory_report, stats_sync)
        self._write_json(self.metadata_file, metadata)
        return metadata

    def sync_public_stats(self) -> Dict[str, Optional[str]]:
        updated_at = datetime.now().isoformat()
        try:
            success = SourceUpdater(self.project_root).run_update()
            if success:
                return {
                    "success": True,
                    "status": "success",
                    "updated_at": updated_at,
                    "error": None,
                }
            return {
                "success": False,
                "status": "failed",
                "updated_at": updated_at,
                "error": "README 或站点统计同步失败",
            }
        except Exception as e:
            return {
                "success": False,
                "status": "failed",
                "updated_at": updated_at,
                "error": str(e),
            }

    def inventory_status(self) -> Dict:
        working = self.load_working_sources()
        candidates = self.load_candidate_sources()
        screened = self.load_screened_sources()

        return {
            "working_count": len(working),
            "candidate_count": len(candidates),
            "screened_count": len(screened),
            "min_working_sources": self.min_working_sources,
            "working_target": self.working_target,
            "export_target": self.export_target,
            "candidate_buffer_ok": len(candidates) >= self.min_candidate_sources,
        }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="候选池与工作库存管理器")
    parser.add_argument("--base-dir", type=Path, help="项目根目录或 sources/legado 目录")
    parser.add_argument(
        "action",
        choices=["status", "refresh-candidates", "refresh-screened", "rebuild"],
        help="执行动作",
    )
    args = parser.parse_args()

    inventory = SourceInventory(args.base_dir)

    if args.action == "status":
        print(json.dumps(inventory.inventory_status(), ensure_ascii=False, indent=2))
        return 0

    if args.action == "refresh-screened":
        _, report = inventory.refresh_screened_pool(save=True)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.action == "refresh-candidates":
        _, report = inventory.refresh_candidate_pool(save=True)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    _, _, report = inventory.build_inventory(save=True)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
