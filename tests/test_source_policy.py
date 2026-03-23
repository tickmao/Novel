#!/usr/bin/env python3

import json
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from auto_supplement import AutoSupplement  # noqa: E402
from daily_maintenance import DailyMaintenance  # noqa: E402
from source_inventory import SourceInventory  # noqa: E402
from source_policy import SourcePolicy  # noqa: E402


class SourcePolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = SourcePolicy(ROOT)

    def test_canonicalize_name_maps_common_mixed_names_to_chinese(self):
        cases = [
            ("QQ阅读", "https://book.qq.com", "腾讯阅读"),
            ("QQ浏览器", "https://novel.html5.qq.com", "腾讯阅读"),
            ("56书库", "https://www.56shuku.example", "五六书库"),
            ("SF轻小说", "https://book.sfacg.com", "菠萝包轻小说"),
        ]

        for name, url, expected in cases:
            with self.subTest(name=name):
                normalized, status, reasons = self.policy.canonicalize_name(name, url)
                self.assertEqual(status, "pure_chinese")
                self.assertEqual(normalized, expected)
                self.assertEqual(reasons, [])

    def test_detect_adult_risks_blocks_adult_and_boys_love_sources(self):
        adult_sources = [
            {"bookSourceName": "PO18文学", "bookSourceUrl": "https://po18.example"},
            {"bookSourceName": "BL文库", "bookSourceUrl": "https://safe.example"},
            {"bookSourceName": "海棠书屋", "bookSourceUrl": "https://haitang.example"},
            {"bookSourceName": "正常小说", "bookSourceComment": "耽美佳作", "bookSourceUrl": "https://safe.example"},
        ]

        for source in adult_sources:
            with self.subTest(name=source.get("bookSourceName")):
                self.assertTrue(self.policy.detect_adult_risks(source))

    def test_screen_source_rejects_non_chinese_name(self):
        accepted, rejected = self.policy.screen_source(
            {
                "bookSourceName": "Noveldl",
                "bookSourceUrl": "https://noveldl.example",
                "bookSourceType": 0,
                "ruleSearch": {"name": "x"},
                "ruleToc": {"list": "x"},
            }
        )
        self.assertIsNone(accepted)
        self.assertIsNotNone(rejected)


class SourceInventoryTests(unittest.TestCase):
    def create_temp_project(self) -> Path:
        project_root = Path(tempfile.mkdtemp())
        (project_root / "config").mkdir()
        for name in ("name_normalization.json", "content_audit.json", "supplement_config.json"):
            (project_root / "config" / name).write_text((ROOT / "config" / name).read_text(encoding="utf-8"), encoding="utf-8")
        (project_root / "sources" / "legado" / "pool").mkdir(parents=True)
        (project_root / "sources" / "legado" / "main").mkdir(parents=True)
        return project_root

    def test_build_inventory_prefers_chinese_and_exports_exact_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / "config").mkdir()
            for name in ("name_normalization.json", "content_audit.json", "supplement_config.json"):
                (project_root / "config" / name).write_text((ROOT / "config" / name).read_text(encoding="utf-8"), encoding="utf-8")

            legado_dir = project_root / "sources" / "legado"
            (legado_dir / "pool").mkdir(parents=True)
            (legado_dir / "main").mkdir(parents=True)

            inventory = SourceInventory(legado_dir)
            current = [
                {
                    "bookSourceName": "QQ阅读",
                    "bookSourceUrl": "https://book.qq.com",
                    "bookSourceType": 0,
                    "ruleSearch": {"name": "x"},
                    "ruleToc": {"list": "x"},
                    "ruleContent": {"content": "x"},
                    "score": 80,
                }
            ]
            digit_map = str.maketrans({
                "0": "零",
                "1": "一",
                "2": "二",
                "3": "三",
                "4": "四",
                "5": "五",
                "6": "六",
                "7": "七",
                "8": "八",
                "9": "九",
            })
            candidates = []
            for idx in range(1100):
                label = str(idx).translate(digit_map)
                candidates.append(
                    {
                        "bookSourceName": f"测试书站{label}",
                        "bookSourceUrl": f"https://example{idx}.com",
                        "bookSourceType": 0,
                        "ruleSearch": {"name": "x"},
                        "ruleToc": {"list": "x"},
                        "ruleContent": {"content": "x"},
                        "score": 60 + (idx % 10),
                    }
                )

            working, export, report = inventory.build_inventory(current, candidates, save=False)
            self.assertGreaterEqual(len(working), inventory.min_working_sources)
            self.assertEqual(len(export), inventory.export_target)
            self.assertTrue(all("bookSourceName" in item for item in export))
            self.assertFalse(any(any(ch.isascii() and ch.isalnum() for ch in item["bookSourceName"]) for item in export))

    def test_build_inventory_does_not_write_working_when_export_update_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / "config").mkdir()
            for name in ("name_normalization.json", "content_audit.json", "supplement_config.json"):
                (project_root / "config" / name).write_text((ROOT / "config" / name).read_text(encoding="utf-8"), encoding="utf-8")

            legado_dir = project_root / "sources" / "legado"
            (legado_dir / "pool").mkdir(parents=True)
            (legado_dir / "main").mkdir(parents=True)
            inventory = SourceInventory(legado_dir)

            candidates = []
            for idx in range(1100):
                candidates.append(
                    {
                        "bookSourceName": f"测试书站{str(idx).translate(str.maketrans('0123456789', '零一二三四五六七八九'))}",
                        "bookSourceUrl": f"https://example{idx}.com",
                        "bookSourceType": 0,
                        "ruleSearch": {"name": "x"},
                        "ruleToc": {"list": "x"},
                        "ruleContent": {"content": "x"},
                        "score": 60,
                    }
                )

            with patch.object(inventory.updater, "safe_update", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    inventory.build_inventory([], candidates, save=True)

            self.assertFalse(inventory.working_file.exists())
            self.assertFalse(inventory.metadata_file.exists())

    def test_update_metadata_refreshes_last_maintenance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / "config").mkdir()
            for name in ("name_normalization.json", "content_audit.json", "supplement_config.json"):
                (project_root / "config" / name).write_text((ROOT / "config" / name).read_text(encoding="utf-8"), encoding="utf-8")

            legado_dir = project_root / "sources" / "legado"
            (legado_dir / "pool").mkdir(parents=True)
            (legado_dir / "main").mkdir(parents=True)
            inventory = SourceInventory(legado_dir)

            inventory.update_metadata({"timestamp": "one"}, {"updated_at": "a", "status": "success", "error": None})
            first = json.loads(inventory.metadata_file.read_text(encoding="utf-8"))["last_maintenance"]
            time.sleep(0.01)
            inventory.update_metadata({"timestamp": "two"}, {"updated_at": "b", "status": "success", "error": None})
            second = json.loads(inventory.metadata_file.read_text(encoding="utf-8"))["last_maintenance"]

            self.assertNotEqual(first, second)

    def test_build_inventory_records_stats_sync_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / "config").mkdir()
            for name in ("name_normalization.json", "content_audit.json", "supplement_config.json"):
                (project_root / "config" / name).write_text((ROOT / "config" / name).read_text(encoding="utf-8"), encoding="utf-8")

            legado_dir = project_root / "sources" / "legado"
            (legado_dir / "pool").mkdir(parents=True)
            (legado_dir / "main").mkdir(parents=True)
            inventory = SourceInventory(legado_dir)

            candidates = []
            for idx in range(1100):
                candidates.append(
                    {
                        "bookSourceName": f"测试书站{str(idx).translate(str.maketrans('0123456789', '零一二三四五六七八九'))}",
                        "bookSourceUrl": f"https://example{idx}.com",
                        "bookSourceType": 0,
                        "ruleSearch": {"name": "x"},
                        "ruleToc": {"list": "x"},
                        "ruleContent": {"content": "x"},
                        "score": 60,
                    }
                )

            with patch.object(inventory.updater, "safe_update", return_value=True):
                with patch("source_inventory.SourceUpdater") as mock_updater_cls:
                    mock_updater_cls.return_value.run_update.return_value = False
                    with self.assertRaises(RuntimeError):
                        inventory.build_inventory([], candidates, save=True)

            metadata = json.loads(inventory.metadata_file.read_text(encoding="utf-8"))
            self.assertEqual(metadata["last_stats_sync_status"], "failed")
            self.assertTrue(metadata["last_stats_sync_error"])


class AutoSupplementTests(unittest.IsolatedAsyncioTestCase):
    async def test_replenish_from_external_includes_local_large_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / "config").mkdir()
            for name in ("name_normalization.json", "content_audit.json", "supplement_config.json"):
                (project_root / "config" / name).write_text((ROOT / "config" / name).read_text(encoding="utf-8"), encoding="utf-8")

            legado_dir = project_root / "sources" / "legado"
            (legado_dir / "pool").mkdir(parents=True)
            (legado_dir / "main").mkdir(parents=True)
            yiove = legado_dir / "yiove_new.json"
            yiove.write_text("[]", encoding="utf-8")

            supplement = AutoSupplement(project_root)

            with patch("auto_supplement.SourceCollector") as mock_collector_cls:
                collector = mock_collector_cls.return_value
                collector.collect_all_sources = AsyncMock(return_value=[])
                await supplement.replenish_from_external(set(), 10)

            collector.collect_all_sources.assert_awaited()
            passed_files = collector.collect_all_sources.await_args.args[0]
            self.assertEqual([path.resolve() for path in passed_files], [yiove.resolve()])

    async def test_dry_run_passes_save_false_to_merge_validated_candidates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / "config").mkdir()
            for name in ("name_normalization.json", "content_audit.json", "supplement_config.json"):
                (project_root / "config" / name).write_text((ROOT / "config" / name).read_text(encoding="utf-8"), encoding="utf-8")

            legado_dir = project_root / "sources" / "legado"
            (legado_dir / "pool").mkdir(parents=True)
            (legado_dir / "main").mkdir(parents=True)

            supplement = AutoSupplement(project_root)
            valid_source = {
                "bookSourceName": "测试书站",
                "bookSourceUrl": "https://example.com",
                "bookSourceType": 0,
                "ruleSearch": {"name": "x"},
                "ruleToc": {"list": "x"},
                "ruleContent": {"content": "x"},
            }

            with patch.object(supplement.inventory, "load_working_sources", return_value=[]), \
                 patch.object(supplement.inventory, "refresh_candidate_pool", return_value=([], {})), \
                 patch.object(supplement.inventory, "build_inventory", side_effect=[([], [], {}), ([], [], {})]), \
                 patch.object(supplement, "replenish_from_screened", AsyncMock(return_value=([valid_source], [], {}))), \
                 patch.object(supplement.inventory, "merge_validated_candidates", return_value=([], {})) as merge_mock:
                await supplement.auto_supplement_workflow(force=True, dry_run=True)

            merge_mock.assert_called_once()
            self.assertFalse(merge_mock.call_args.kwargs["save"])


class DailyMaintenanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_maintain_preserves_inventory_metadata_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            (project_root / "config").mkdir()
            for name in ("name_normalization.json", "content_audit.json", "supplement_config.json"):
                (project_root / "config" / name).write_text((ROOT / "config" / name).read_text(encoding="utf-8"), encoding="utf-8")

            legado_dir = project_root / "sources" / "legado"
            (legado_dir / "pool").mkdir(parents=True)
            (legado_dir / "main").mkdir(parents=True)

            maintenance = DailyMaintenance(legado_dir)
            sources = [
                {
                    "bookSourceName": "测试书站",
                    "bookSourceUrl": "https://example.com",
                    "bookSourceType": 0,
                    "ruleSearch": {"name": "x"},
                    "ruleToc": {"list": "x"},
                    "ruleContent": {"content": "x"},
                    "score": 60,
                }
            ]
            maintenance.save_metadata({"last_validation_index": 0, "failure_counts": {}})

            def fake_build_inventory(current, candidates, save=True):
                maintenance.inventory.update_metadata(
                    {"timestamp": "inventory-ts"},
                    {"updated_at": "sync-ts", "status": "success", "error": None},
                )
                return sources, sources, {"timestamp": "inventory-ts"}

            with patch.object(maintenance.inventory, "load_working_sources", return_value=sources), \
                 patch.object(maintenance, "validate_sources", AsyncMock(return_value=(sources, [], {"valid": 1, "invalid": 0}))), \
                 patch.object(maintenance, "identify_failed_sources", return_value=[]), \
                 patch.object(maintenance.inventory, "load_candidate_sources", return_value=[]), \
                 patch.object(maintenance, "generate_health_report", return_value={"health_status": "good"}), \
                 patch.object(maintenance.inventory, "build_inventory", side_effect=fake_build_inventory):
                success = await maintenance.maintain(batch_size=1, dry_run=False)

            self.assertTrue(success)
            metadata = json.loads(maintenance.metadata_file.read_text(encoding="utf-8"))
            self.assertEqual(metadata["last_stats_sync_status"], "success")
            self.assertEqual(metadata["last_stats_sync"], "sync-ts")


if __name__ == "__main__":
    unittest.main()
