#!/usr/bin/env python3
"""
Legado 书源路径与兼容文件同步工具。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List


def resolve_legado_dir(base_dir: Path | str | None = None) -> Path:
    """
    解析 Legado 数据目录。

    支持传入项目根目录或 `sources/legado` 目录。
    """
    if base_dir is None:
        base = Path(__file__).resolve().parent.parent
    else:
        base = Path(base_dir).resolve()

    if (base / "main").is_dir() and (base / "pool").is_dir():
        return base

    legado_dir = base / "sources" / "legado"
    if legado_dir.is_dir():
        return legado_dir

    return base


def canonical_source_file(base_dir: Path | str | None = None) -> Path:
    """主库文件：`sources/legado/main/full.json`。"""
    return resolve_legado_dir(base_dir) / "main" / "full.json"


def working_source_file(base_dir: Path | str | None = None) -> Path:
    """内部工作库存：`sources/legado/main/working.json`。"""
    return resolve_legado_dir(base_dir) / "main" / "working.json"


def compatibility_source_file(base_dir: Path | str | None = None) -> Path:
    """兼容文件：`sources/legado/full.json`。"""
    return resolve_legado_dir(base_dir) / "full.json"


def metadata_file(base_dir: Path | str | None = None) -> Path:
    """主库元数据文件。"""
    return resolve_legado_dir(base_dir) / "main" / "metadata.json"


def screened_pool_file(base_dir: Path | str | None = None) -> Path:
    """静态筛选后的候选输入池。"""
    return resolve_legado_dir(base_dir) / "pool" / "screened.json"


def screened_report_file(base_dir: Path | str | None = None) -> Path:
    """静态筛选报告。"""
    return resolve_legado_dir(base_dir) / "pool" / "screened_report.json"


def candidate_pool_file(base_dir: Path | str | None = None) -> Path:
    """已验证候选池。"""
    return resolve_legado_dir(base_dir) / "pool" / "candidates.json"


def candidate_report_file(base_dir: Path | str | None = None) -> Path:
    """候选池维护报告。"""
    return resolve_legado_dir(base_dir) / "pool" / "candidate_report.json"


def raw_pool_file(base_dir: Path | str | None = None) -> Path:
    """原始书源池。"""
    return resolve_legado_dir(base_dir) / "pool" / "raw.json"


def primary_source_file(base_dir: Path | str | None = None) -> Path:
    """
    当前应优先读取的书源文件。

    优先主库，主库不存在时回退到兼容文件。
    """
    canonical = canonical_source_file(base_dir)
    compatibility = compatibility_source_file(base_dir)

    if canonical.exists() or not compatibility.exists():
        return canonical

    return compatibility


def mirror_source_files(base_dir: Path | str | None = None) -> List[Path]:
    """需要同步写入的 Legado 书源文件列表。"""
    canonical = canonical_source_file(base_dir)
    compatibility = compatibility_source_file(base_dir)
    return [canonical, compatibility]


def write_source_mirror(
    sources: list,
    base_dir: Path | str | None = None,
    *,
    indent: int = 2
) -> List[Path]:
    """
    同步写入主库文件和兼容文件。
    """
    payload = json.dumps(sources, ensure_ascii=False, indent=indent)
    written: List[Path] = []

    for path in mirror_source_files(base_dir):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        written.append(path)

    return written
