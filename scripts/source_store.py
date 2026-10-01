"""Versioned raw sources and durable maintenance state."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value)
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result
    except (ValueError, TypeError):
        return None


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()


def digest(value) -> str:
    return hashlib.sha256(json_bytes(value)).hexdigest()


def read_json(path: Path, default=None):
    return json.loads(path.read_bytes()) if path.exists() else deepcopy(default)


def atomic_bundle(files: dict[Path, bytes]) -> None:
    """Restore the entire bundle if any replacement fails."""
    staged, previous, changed = {}, {}, []
    try:
        for path, payload in files.items():
            previous[path] = path.read_bytes() if path.exists() else None
            if previous[path] == payload:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
            staged[path] = Path(name)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        for path, temp in staged.items():
            os.replace(temp, path)
            changed.append(path)
    except BaseException:
        for path in reversed(changed):
            if previous[path] is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(previous[path])
        raise
    finally:
        for temp in staged.values():
            temp.unlink(missing_ok=True)


INTERNAL_FIELDS = {
    'score', 'selectionScore', 'originalName', 'normalizedName',
    'validationStatus', 'validationTime',
}


def source_payload(source: dict) -> dict:
    return {k: deepcopy(v) for k, v in source.items() if not k.startswith('_') and k not in INTERNAL_FIELDS}


def source_id(source: dict) -> str:
    # Fragments can identify distinct rules in Legado. Preserve them here.
    url = str(source.get('bookSourceUrl', '')).strip()
    return digest(url if url else source)


def rule_fingerprint(source: dict) -> str:
    from urllib.parse import urlsplit, urlunsplit
    split = urlsplit(str(source.get('bookSourceUrl', '')))
    url = urlunsplit((split.scheme.lower(), split.netloc.lower(), split.path or '/', split.query, ''))
    rules = {k: v for k, v in source.items() if k.startswith('rule') or k in (
        'searchUrl', 'header', 'loginUrl', 'jsLib', 'bookUrlPattern', 'bookSourceType',
    )}
    return digest({'url': url, 'rules': rules})


class SourceStore:
    SCHEMA_VERSION = 2

    def __init__(self, root: Path):
        self.root = Path(root)
        self.manifest_path = self.root / 'manifest.json'
        self.manifest = read_json(self.manifest_path, {'schema_version': self.SCHEMA_VERSION, 'shards': {}, 'count': 0})
        if self.manifest.get('schema_version') != self.SCHEMA_VERSION:
            raise ValueError('Unsupported raw store schema')
        self.shards: dict[str, dict] = {}
        self.dirty: set[str] = set()
        self.state = read_json(self.root / 'state.json', {'schema_version': 2, 'providers': {}, 'runs': []})

    @property
    def exists(self) -> bool:
        return self.manifest_path.exists()

    def _load(self, prefix: str) -> dict:
        if prefix not in self.shards:
            entry = self.manifest['shards'].get(prefix)
            path = self.root / 'records' / f'{prefix}.json'
            if entry:
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() != entry['sha256']:
                    raise ValueError(f'Raw shard checksum mismatch: {prefix}')
                self.shards[prefix] = json.loads(data)
            else:
                self.shards[prefix] = {}
        return self.shards[prefix]

    def get(self, key: str) -> dict:
        return self._load(key[:2])[key]

    def touch(self, key: str) -> None:
        self.dirty.add(key[:2])

    def records(self):
        for prefix in sorted(set(self.manifest['shards']) | set(self.shards)):
            yield from self._load(prefix).values()

    def ingest(self, source: dict, provenance: dict, now: str | None = None) -> tuple[str, str]:
        if not isinstance(source, dict):
            raise ValueError('A raw source must be an object')
        now = now or utcnow()
        payload = deepcopy(source)
        key, revision = source_id(payload), digest(payload)
        shard = self._load(key[:2])
        record = shard.setdefault(key, {'source_id': key, 'first_seen_at': now, 'versions': {}})
        version = record['versions'].setdefault(revision, {
            'source': payload, 'first_seen_at': now, 'provenance': [],
            'audit': {}, 'validation': {'status': 'pending', 'consecutive_failures': 0},
        })
        if provenance not in version['provenance']:
            version['provenance'].append(deepcopy(provenance))
        version['last_seen_at'] = now
        record['last_seen_at'] = now
        record['latest_revision'] = revision
        self.touch(key)
        return key, revision

    def save(self) -> None:
        manifest = deepcopy(self.manifest)
        files = {}
        for prefix in sorted(self.dirty):
            data = json_bytes(self.shards[prefix])
            if len(data) >= 90 * 1024 * 1024:
                raise ValueError(f'Raw shard exceeds size limit: {prefix}')
            files[self.root / 'records' / f'{prefix}.json'] = data
            manifest['shards'][prefix] = {'count': len(self.shards[prefix]), 'sha256': hashlib.sha256(data).hexdigest()}
        manifest['count'] = sum(item['count'] for item in manifest['shards'].values())
        files[self.root / 'state.json'] = json_bytes(self.state)
        files[self.manifest_path] = json_bytes(manifest)
        atomic_bundle(files)
        self.manifest = manifest
        self.dirty.clear()

    def migrate(self, inputs: list[Path]) -> dict:
        counts = {}
        for path in inputs:
            if not path.exists():
                continue
            items = read_json(path)
            if not isinstance(items, list):
                raise ValueError(f'Expected an array: {path}')
            counts[str(path)] = len(items)
            for item in items:
                self.ingest(item, {'provider': 'legacy', 'file': str(path.name)})
        self.state['migration'] = {'completed_at': utcnow(), 'inputs': counts}
        self.save()
        verified = SourceStore(self.root)
        if sum(1 for _ in verified.records()) != self.manifest['count']:
            raise ValueError('Raw migration count mismatch')
        return {'inputs': counts, 'records': self.manifest['count']}
