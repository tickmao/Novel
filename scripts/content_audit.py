"""Field-aware content review. Genre labels are not adult evidence."""

from __future__ import annotations

import html
import json
import re
import unicodedata
from urllib.parse import unquote, urlsplit

from source_store import digest, source_id, source_payload, utcnow


def normalize(text) -> str:
    value = unicodedata.normalize('NFKC', html.unescape(str(text or '')))
    return re.sub(r'[\u200b-\u200f\ufeff]', '', value)


def category_text(value) -> str:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    value = normalize(value)
    if '@js:' in value or '<js>' in value:
        # Only explicit category titles are evidence; never scan executable code.
        return ' '.join(re.findall(r'''["'](?:title|name)["']\s*:\s*["']([^"']+)["']''', value))
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return ' '.join(str(item.get('title', item.get('name', ''))) for item in parsed if isinstance(item, dict))
    except (ValueError, TypeError):
        pass
    return '\n'.join(line.split('::', 1)[0] for line in value.splitlines())


class ContentAudit:
    def __init__(self, config: dict, reviews=None):
        self.config = config
        self.version = digest({'config': config, 'implementation': 'context-v3'})
        self.reviews = reviews or []

    def resolve_review(self, audit, source):
        payload_hash = source.get('_review_payload_hash', digest(source_payload(source)))
        review = next((item for item in reversed(self.reviews)
                       if item.get('source_id') == source_id(source)
                       and item.get('payload_sha256') == payload_hash
                       and item.get('policy_version') == self.version
                       and item.get('evidence_hash') == audit.get('evidence_hash')
                       and item.get('reason') and item.get('reviewer')), None)
        if review and audit.get('decision') == 'review' and review.get('decision') in ('pass', 'block'):
            return {**audit, 'decision': review['decision'], 'review': review}
        return audit

    def check(self, source: dict, pages: list[dict] | None = None) -> dict:
        evidence = []
        fields = {
            key: normalize(source.get(key)) for key in (
                'originalName', 'bookSourceName', 'bookSourceGroup', 'bookSourceComment', '_yckceo_title',
            )
        }
        fields['categories'] = category_text(source.get('exploreUrl', ''))
        urls = [str(source.get('bookSourceUrl', ''))]
        field_urls = {}
        for index, page in enumerate(pages or []):
            urls.append(page.get('url', ''))
            field = 'page:' + page.get('kind', 'unknown') + ':' + str(index)
            fields[field] = normalize(page.get('text', ''))[:200000]
            field_urls[field] = page.get('url', '')

        for value in urls:
            host = (urlsplit(value).hostname or '').lower().rstrip('.')
            for denied in self.config.get('block_domains', []):
                if host == denied or host.endswith('.' + denied):
                    evidence.append({'rule': 'blocked_domain', 'field': 'hostname', 'match': host, 'decision': 'block'})
            for pattern in self.config.get('domain_label_patterns', []):
                if any(re.fullmatch(pattern, label, re.I) for label in host.split('.')):
                    evidence.append({'rule': 'adult_domain_label', 'field': 'hostname', 'match': host, 'decision': 'block'})

        disclaimer = re.compile(r'(?:严禁|禁止|拒绝|杜绝|抵制|不含|已去除|已删除)(?:发布|上传|传播|提供|收录)?\s*(?:色情|情色|成人(?:内容|小说|专区))(?:内容|小说)?')
        for field in fields:
            if field not in ('bookSourceName', 'originalName', '_yckceo_title', 'categories'):
                fields[field] = disclaimer.sub(lambda match: ' ' * len(match.group()), fields[field])
        for rule in self.config.get('text_rules', []):
            pattern = re.compile(rule['pattern'], re.I)
            for field, value in fields.items():
                if field.startswith('page:content') and not rule.get('body', False):
                    continue
                match = pattern.search(value)
                if match:
                    decision = rule.get('body_decision', rule['decision']) if field.startswith('page:content:') else rule['decision']
                    if (match.group(0) in ('色情', '情色', '肉文', '肉漫')
                            and field not in ('bookSourceName', 'originalName', 'categories')
                            and not field.startswith('page:categories:')):
                        decision = 'review'
                    evidence.append({'rule': rule['id'], 'field': field, 'match': match.group(0)[:100],
                                     'decision': decision, 'url': field_urls.get(field),
                                     'context': value[max(0, match.start() - 100):match.end() + 100],
                                     'field_sha256': digest(value)})
        # Ambiguous explicit words in prose require review, not a site-wide ban.
        for field, value in fields.items():
            if not field.startswith('page:content:'):
                continue
            hits = [word for word in self.config.get('body_review_terms', []) if word in value]
            if len(hits) >= self.config.get('body_review_threshold', 2):
                position = value.index(hits[0])
                evidence.append({'rule': 'explicit_body_terms', 'field': field, 'match': ', '.join(hits),
                                 'decision': 'review', 'url': field_urls.get(field),
                                 'context': value[max(0, position - 100):position + 200], 'field_sha256': digest(value)})

        decisions = {item['decision'] for item in evidence}
        decision = 'block' if 'block' in decisions else 'review' if 'review' in decisions else 'pass'
        evidence_hash = digest({'policy_version': self.version, 'evidence': evidence})
        kinds = {page.get('kind') for page in pages or []}
        return self.resolve_review({
            'decision': decision, 'policy_version': self.version, 'checked_at': utcnow(),
            'complete': {'search', 'book', 'toc', 'content'} <= kinds,
            'evidence': evidence,
            'evidence_hash': evidence_hash,
            'payload_sha256': source.get('_review_payload_hash', digest(source_payload(source))),
        }, source)
