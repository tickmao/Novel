#!/usr/bin/env python3
"""
统一的书源命名规范、成人向审核与静态准入策略。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

from clean import calculate_quality_score, normalize_group, normalize_source_name


CN_ONLY_RE = re.compile(r"^[\u4e00-\u9fff·]{2,16}$")
HAS_CN_RE = re.compile(r"[\u4e00-\u9fff]")
HAS_ASCII_OR_DIGIT_RE = re.compile(r"[A-Za-z0-9]")
MEDIA_KEYWORDS = ("漫画", "有声", "视频", "影视", "音频", "听书", "动漫", "漫客", "咚漫", "画涯", "韩漫", "禁嫚")
CHINESE_DIGITS = str.maketrans({
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


def _load_json(path: Path, default: Dict) -> Dict:
    if not path.exists():
        return deepcopy(default)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class SourcePolicy:
    """书源准入策略。"""

    DEFAULT_NAME_CONFIG = {
        "require_pure_chinese": True,
        "min_length": 2,
        "max_length": 16,
        "drop_ascii_suffixes": [],
        "token_replacements": {},
        "domain_to_canonical": {},
        "alias_to_canonical": {},
        "generic_blacklist": [],
    }
    DEFAULT_AUDIT_CONFIG = {
        "text_patterns": [],
        "url_patterns": [],
        "allow_patterns": [],
    }

    def __init__(self, base_dir: Path | str | None = None):
        root = Path(base_dir).resolve() if base_dir else Path(__file__).resolve().parent.parent
        self.project_root = root
        if not (self.project_root / "config").exists():
            for parent in [root] + list(root.parents):
                if (parent / "config").exists():
                    self.project_root = parent
                    break

        config_dir = self.project_root / "config"
        self.name_config = _load_json(config_dir / "name_normalization.json", self.DEFAULT_NAME_CONFIG)
        self.audit_config = _load_json(config_dir / "content_audit.json", self.DEFAULT_AUDIT_CONFIG)

        self.require_pure_chinese = bool(self.name_config.get("require_pure_chinese", True))
        self.min_length = int(self.name_config.get("min_length", 2))
        self.max_length = int(self.name_config.get("max_length", 16))
        self.generic_blacklist = set(self.name_config.get("generic_blacklist", []))
        self.reject_name_patterns = [
            re.compile(pattern) for pattern in self.name_config.get("reject_patterns", [])
        ]
        self.drop_ascii_suffixes = tuple(self.name_config.get("drop_ascii_suffixes", []))
        self.token_replacements = self.name_config.get("token_replacements", {})
        self.domain_to_canonical = {
            self._normalize_domain(k): v for k, v in self.name_config.get("domain_to_canonical", {}).items()
        }
        self.alias_to_canonical = {}
        for alias, canonical in self.name_config.get("alias_to_canonical", {}).items():
            self.alias_to_canonical[normalize_source_name(alias)] = canonical
            self.alias_to_canonical[alias.strip()] = canonical

        self.text_patterns = [
            (re.compile(item["pattern"]), item["reason"])
            for item in self.audit_config.get("text_patterns", [])
        ]
        self.url_patterns = [
            (re.compile(item["pattern"]), item["reason"])
            for item in self.audit_config.get("url_patterns", [])
        ]
        self.allow_patterns = tuple(self.audit_config.get("allow_patterns", []))

    def _normalize_domain(self, domain: str) -> str:
        cleaned = domain.lower().strip()
        if cleaned.startswith("www."):
            cleaned = cleaned[4:]
        return cleaned

    def extract_domain(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            domain = parsed.netloc or parsed.path.split("/")[0]
            return self._normalize_domain(domain)
        except Exception:
            return ""

    def _apply_token_replacements(self, text: str) -> str:
        value = text
        for token, replacement in self.token_replacements.items():
            value = re.sub(re.escape(token), replacement, value, flags=re.IGNORECASE)
        return value

    def _strip_ascii_noise(self, text: str) -> str:
        value = text

        for suffix in self.drop_ascii_suffixes:
            value = re.sub(rf"{re.escape(suffix)}$", "", value, flags=re.IGNORECASE)

        value = re.sub(r"(?<=[\u4e00-\u9fff])[A-Za-z]+$", "", value)
        value = re.sub(r"^[A-Za-z0-9]+(?=[\u4e00-\u9fff])", "", value)
        value = re.sub(r"(?<=[\u4e00-\u9fff])[A-Za-z]+(?=[\u4e00-\u9fff])", "", value)
        value = re.sub(r"[A-Za-z]+(?=[零一二三四五六七八九十百千万两〇]\u4e00-\u9fff)", "", value)
        value = re.sub(r"[A-Za-z]+", "", value) if HAS_CN_RE.search(value) else value
        value = re.sub(r"\s+", "", value)
        return normalize_source_name(value)

    def _canonical_from_domain(self, domain: str) -> Optional[str]:
        if not domain:
            return None
        if domain in self.domain_to_canonical:
            return self.domain_to_canonical[domain]

        parts = domain.split(".")
        if len(parts) > 2:
            collapsed = ".".join(parts[-2:])
            return self.domain_to_canonical.get(collapsed)
        return None

    def canonicalize_name(self, name: str, url: str = "") -> Tuple[str, str, List[str]]:
        """
        产出最终展示名称。

        返回 `(final_name, audit_status, reasons)`。
        """
        original = normalize_source_name(name)
        if not original:
            return "", "rejected", ["名称为空"]

        domain = self.extract_domain(url)
        mapped = self._canonical_from_domain(domain)

        if not mapped:
            mapped = self.alias_to_canonical.get(original)

        candidate = normalize_source_name(mapped or original)
        candidate = self._apply_token_replacements(candidate)
        candidate = candidate.translate(CHINESE_DIGITS)
        candidate = self._strip_ascii_noise(candidate)
        candidate = candidate.replace("·", "")

        reasons: List[str] = []

        if candidate in self.generic_blacklist:
            reasons.append("名称过于泛化")

        for pattern in self.reject_name_patterns:
            if pattern.search(candidate):
                reasons.append("名称命中低质量模式")
                break

        if len(candidate) < self.min_length:
            reasons.append("名称过短")
        if len(candidate) > self.max_length:
            reasons.append("名称过长")

        if self.require_pure_chinese and not CN_ONLY_RE.match(candidate):
            reasons.append("名称不是纯中文")

        if HAS_ASCII_OR_DIGIT_RE.search(candidate):
            reasons.append("名称仍含英文或数字")

        if not HAS_CN_RE.search(candidate):
            reasons.append("名称不含中文主体")

        if reasons:
            return candidate, "rejected", reasons

        return candidate, "pure_chinese", []

    def detect_adult_risks(self, source: Dict) -> List[str]:
        """
        从名称、分组、备注、URL 多字段检查成人向风险。
        """
        text_fields = [
            str(source.get("originalName", "")),
            str(source.get("bookSourceName", "")),
            str(source.get("bookSourceGroup", "")),
            str(source.get("bookSourceComment", "")),
        ]
        text_blob = " ".join(filter(None, text_fields))
        for allow in self.allow_patterns:
            text_blob = text_blob.replace(allow, "")

        risks: List[str] = []

        for pattern, reason in self.text_patterns:
            if pattern.search(text_blob):
                risks.append(reason)

        url_blob = " ".join([
            str(source.get("bookSourceUrl", "")),
            str(source.get("searchUrl", "")),
            str(source.get("exploreUrl", "")),
        ])
        for pattern, reason in self.url_patterns:
            if pattern.search(url_blob):
                risks.append(reason)

        # 去重并保序
        deduped: List[str] = []
        seen = set()
        for risk in risks:
            if risk not in seen:
                deduped.append(risk)
                seen.add(risk)
        return deduped

    def _rule_completeness(self, source: Dict) -> int:
        return sum(
            1 for field in ("ruleSearch", "ruleToc", "ruleContent")
            if source.get(field)
        )

    def enrich_source(self, source: Dict) -> Dict:
        enriched = deepcopy(source)
        original_name = str(source.get("originalName") or source.get("bookSourceName", "")).strip()
        final_name, audit_status, name_reasons = self.canonicalize_name(
            original_name,
            str(source.get("bookSourceUrl", "")),
        )

        adult_risks = self.detect_adult_risks(source)
        domain = self.extract_domain(str(source.get("bookSourceUrl", "")))

        enriched["originalName"] = original_name
        enriched["normalizedName"] = final_name
        if final_name:
            enriched["bookSourceName"] = final_name
        if "bookSourceGroup" in enriched:
            enriched["bookSourceGroup"] = normalize_group(str(enriched.get("bookSourceGroup", "")))
        enriched["_domain"] = domain
        enriched["_name_audit_status"] = audit_status
        enriched["_name_audit_reasons"] = name_reasons
        enriched["_adult_hit_reasons"] = adult_risks
        enriched["_name_quality_score"] = 10 if audit_status == "pure_chinese" else 0

        base_score = float(source.get("selectionScore") or source.get("score") or calculate_quality_score(source))
        validation_bonus = 3 if source.get("_validation_status") == "valid" else 0
        enriched["selectionScore"] = round(base_score + enriched["_name_quality_score"] + validation_bonus, 2)

        return enriched

    def screen_source(self, source: Dict) -> Tuple[Optional[Dict], Optional[Dict]]:
        """
        返回 `(accepted_source, rejected_record)`。
        """
        record = self.enrich_source(source)
        reject_reasons: List[str] = []

        url = str(record.get("bookSourceUrl", "")).strip()
        if not url.startswith(("http://", "https://")):
            reject_reasons.append("URL 无效")

        if int(record.get("bookSourceType", 0)) != 0:
            reject_reasons.append("不是小说源")

        if self._rule_completeness(record) < 2:
            reject_reasons.append("规则不完整")

        media_text = " ".join([
            str(record.get("bookSourceName", "")),
            str(record.get("bookSourceGroup", "")),
        ])
        if any(keyword in media_text for keyword in MEDIA_KEYWORDS):
            reject_reasons.append("非纯小说内容")

        reject_reasons.extend(record.get("_name_audit_reasons", []))
        reject_reasons.extend(record.get("_adult_hit_reasons", []))

        if reject_reasons:
            rejected = {
                "originalName": record.get("originalName", ""),
                "normalizedName": record.get("normalizedName", ""),
                "bookSourceUrl": record.get("bookSourceUrl", ""),
                "reasons": list(dict.fromkeys(reject_reasons)),
                "domain": record.get("_domain", ""),
            }
            return None, rejected

        return record, None

    def screen_sources(self, sources: Iterable[Dict]) -> Tuple[List[Dict], List[Dict], Dict]:
        source_list = list(sources)
        accepted: List[Dict] = []
        rejected: List[Dict] = []
        reason_counter: Counter = Counter()

        for source in source_list:
            valid, invalid = self.screen_source(source)
            if valid:
                accepted.append(valid)
            elif invalid:
                rejected.append(invalid)
                reason_counter.update(invalid.get("reasons", []))

        stats = {
            "input": len(source_list),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "reasons": dict(reason_counter.most_common()),
        }
        return accepted, rejected, stats
