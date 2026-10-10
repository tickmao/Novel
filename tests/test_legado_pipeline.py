#!/usr/bin/env python3

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from clean import normalize_source_name  # noqa: E402
from source_health import apply_result
from test_inventory_v2 import ready_result
from safe_updater import SafeUpdater  # noqa: E402
from publication_fixture import enable_test_publication


class CleanNameTests(unittest.TestCase):
    def test_normalize_source_name_keeps_core_name(self):
        cases = {
            "💵 起点自用": "起点",
            "🍁 大魔兔📱 #Haxc1107": "大魔兔",
            "㊣黑岩阅读 #破冰1101": "黑岩阅读",
            " QQ浏览器🇨🇳": "QQ浏览器",
            "㊣ UC小说 #一程1101": "UC小说",
            "⭐番茄小说(番茄Web共享API)": "番茄小说",
            "R 圣墟小说网🐶": "圣墟小说网",
            "翻阅小说/凤凰网书城app": "翻阅小说",
            "大灰狼_api.doubi.tk": "大灰狼",
            "海棠书屋2": "海棠书屋",
        }

        for original, expected in cases.items():
            with self.subTest(original=original):
                self.assertEqual(normalize_source_name(original), expected)

    def test_normalize_source_name_never_returns_empty_for_noisy_but_valid_names(self):
        samples = [
            "💵 铁血读书",
            "📖 布咕阅读",
            "💵 红薯",
            "📡 小说阅读",
            "🍅最新版",
        ]

        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(normalize_source_name(sample))


class SafeUpdaterTests(unittest.TestCase):
    def test_safe_update_syncs_compatibility_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            enable_test_publication(base_dir)
            legado_dir = base_dir / "sources" / "legado"
            updater = SafeUpdater(legado_dir)

            seeds = [
                {
                    "bookSourceName": "💵 起点自用",
                    "bookSourceUrl": "https://example.com/qidian",
                    "bookSourceType": 0,
                    "bookSourceGroup": "🎉 精选",
                    "ruleSearch": {"name": "x"},
                    "ruleToc": {"list": "x"},
                    "ruleContent": {"content": "x"},
                },
                {
                    "bookSourceName": "🍁 大魔兔📱 #Haxc1107",
                    "bookSourceUrl": "https://example.com/damotu",
                    "bookSourceType": 0,
                    "bookSourceGroup": "🎉 精选",
                    "ruleSearch": {"name": "x"},
                    "ruleToc": {"list": "x"},
                    "ruleContent": {"content": "x"},
                },
            ]
            sources = []
            digit_map = str.maketrans("0123456789", "零一二三四五六七八九")
            for idx in range(1000):
                item = dict(seeds[idx % 2])
                item["bookSourceUrl"] = f"https://example{idx}.com"
                item["score"] = 60
                item["selectionScore"] = 60
                if idx >= 2:
                    item["bookSourceName"] = f"测试书站{str(idx).translate(digit_map)}"
                version = {'validation': {}, 'audit': {}}
                apply_result(version, ready_result(updater.policy))
                item['_audit'] = version['audit']
                item['_health'] = version['validation']
                sources.append(item)

            self.assertTrue(updater.safe_update(sources))

            main_file = legado_dir / "main" / "full.json"
            compatibility_file = legado_dir / "full.json"

            main_sources = json.loads(main_file.read_text(encoding="utf-8"))
            compatibility_sources = json.loads(compatibility_file.read_text(encoding="utf-8"))

            self.assertEqual(main_sources, compatibility_sources)
            self.assertEqual(main_sources[0]["bookSourceName"], "起点")
            self.assertEqual(main_sources[1]["bookSourceName"], "大魔兔")


if __name__ == "__main__":
    unittest.main()
