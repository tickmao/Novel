#!/usr/bin/env python3
"""
书源智能整合脚本
- 多维度质量评分
- 智能去重（URL/域名）
- 可选有效性校验
"""

import json
import re
import time
import argparse
import urllib.request
import ssl
from pathlib import Path
from urllib.parse import urlparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from legado_paths import primary_source_file, write_source_mirror

# 配置
MAX_SOURCES = 1500
MAX_RESPOND_TIME = 10000
MIN_SCORE = 25
MAX_PER_DOMAIN = 2
CONCURRENCY = 30
EXISTING_BONUS = 5  # 现有书源信任加分

# Emoji 正则
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U00002600-\U000027BF"
    "\U0001FA00-\U0001FAFF"
    "\U00002300-\U000023FF"
    "\U00002B50-\U00002B55"
    "\U0000FE00-\U0000FE0F"
    "\U0000200D"
    "]+",
    flags=re.UNICODE
)

# SSL 上下文
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def calculate_quality_score(source: dict, bonus: int = 0) -> int:
    """多维度质量评分（满分 60 + bonus）"""
    score = bonus

    # 基础状态 (0-7)
    if source.get('enabled', True):
        score += 5
    if source.get('enabledExplore'):
        score += 2

    # 响应时间 (0-15)
    rt = source.get('respondTime', 99999)
    if rt < 1000:
        score += 15
    elif rt < 3000:
        score += 12
    elif rt < 5000:
        score += 8
    elif rt < 10000:
        score += 4

    # 规则完整性 (0-20)
    if source.get('searchUrl'):
        score += 4
    if source.get('ruleSearch') or source.get('searchRule'):
        score += 4
    if source.get('ruleToc') or source.get('tocRule'):
        score += 4
    if source.get('ruleContent') or source.get('contentRule'):
        score += 6
    if source.get('exploreUrl'):
        score += 2

    # 更新时间 (0-10)
    last = source.get('lastUpdateTime', 0)
    if last:
        days = (time.time() * 1000 - last) / 86400000
        days = max(0, days)  # 防止未来时间导致负数
        if days < 30:
            score += 10
        elif days < 90:
            score += 7
        elif days < 180:
            score += 4
        elif days < 365:
            score += 2

    # 权重 (0-5)
    score += min(source.get('weight', 0) // 100, 5)

    return score


def normalize_url(url: str) -> str:
    """URL 规范化"""
    url = re.sub(r'#[^\s]*$', '', url)
    url = url.rstrip('/')
    url = url.replace('http://', 'https://')
    return url


def get_domain(url: str) -> str:
    """提取域名"""
    try:
        return urlparse(url).netloc
    except Exception:
        return url


def clean_source(source: dict) -> dict:
    """清洗书源"""
    if 'bookSourceName' in source:
        source['bookSourceName'] = EMOJI_PATTERN.sub('', source['bookSourceName']).strip()
    if 'bookSourceGroup' in source:
        source['bookSourceGroup'] = EMOJI_PATTERN.sub('', source['bookSourceGroup']).strip()
    return source


def filter_sources(sources: list, check_respond_time: bool = True) -> list:
    """筛选书源"""
    filtered = []
    for s in sources:
        # 类型筛选
        if s.get('bookSourceType', 0) != 0:
            continue
        # 响应时间筛选
        if check_respond_time and s.get('respondTime', 99999) > MAX_RESPOND_TIME:
            continue
        # 基础规则筛选
        if not s.get('searchUrl'):
            continue
        if not (s.get('ruleContent') or s.get('contentRule')):
            continue
        # 评分筛选
        if calculate_quality_score(s) < MIN_SCORE:
            continue
        filtered.append(s)
    return filtered


def smart_dedupe(sources: list, score_cache: dict, target_domains: int = 1000) -> list:
    """智能去重（优先保证域名多样性）"""
    # 1. URL 去重（保留高分）
    url_map = {}
    for s in sources:
        url = normalize_url(s.get('bookSourceUrl', ''))
        if not url:
            continue
        score = score_cache.get(id(s), calculate_quality_score(s))
        if url not in url_map or score > url_map[url][1]:
            url_map[url] = (s, score)

    sources = [v[0] for v in url_map.values()]
    print(f"    URL 去重后: {len(sources)}")

    # 2. 按域名分组，每个域名按评分排序
    domain_map = defaultdict(list)
    for s in sources:
        url = s.get('bookSourceUrl', '')
        domain = get_domain(url)
        score = score_cache.get(id(s), calculate_quality_score(s))
        domain_map[domain].append((s, score))

    for domain in domain_map:
        domain_map[domain].sort(key=lambda x: -x[1])

    # 3. 优先保证域名多样性
    # 第一轮：每个域名取最高分的 1 个
    result = []
    domains_used = set()

    # 按域名最高分排序
    sorted_domains = sorted(domain_map.keys(),
                           key=lambda d: -domain_map[d][0][1] if domain_map[d] else 0)

    for domain in sorted_domains:
        if len(domains_used) >= target_domains:
            break
        items = domain_map[domain]
        if items:
            result.append(items[0][0])
            domains_used.add(domain)

    print(f"    第一轮（每域名1个）: {len(result)} 个, {len(domains_used)} 个域名")

    # 第二轮：补充第二个书源（如果还有配额）
    remaining = MAX_SOURCES - len(result)
    if remaining > 0:
        second_sources = []
        for domain in sorted_domains:
            items = domain_map[domain]
            if len(items) > 1:
                second_sources.append((items[1][0], items[1][1]))

        # 按评分排序，取剩余配额
        second_sources.sort(key=lambda x: -x[1])
        for s, _ in second_sources[:remaining]:
            result.append(s)

    print(f"    最终去重后: {len(result)} 个, {len(domains_used)} 个域名")
    return result


def head_check(url: str) -> bool:
    """HEAD 请求校验"""
    try:
        req = urllib.request.Request(url, method='HEAD', headers={
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36'
        })
        with urllib.request.urlopen(req, timeout=5, context=SSL_CTX) as resp:
            return resp.status < 400
    except Exception:
        return False


def validate_sources(sources: list) -> list:
    """批量校验"""
    valid = []
    total = len(sources)

    print(f"校验 {total} 个书源...")

    def check_one(s):
        url = s.get('bookSourceUrl', '')
        if url and head_check(url):
            return s
        return None

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        futures = {executor.submit(check_one, s): s for s in sources}
        done = 0
        for future in as_completed(futures):
            done += 1
            result = future.result()
            if result:
                valid.append(result)
            if done % 100 == 0:
                print(f"  进度: {done}/{total}, 有效: {len(valid)}")

    return valid


def main():
    parser = argparse.ArgumentParser(description="书源智能整合脚本")
    parser.add_argument("--validate", "-v", action="store_true", help="启用网络校验")
    parser.add_argument("--max", "-m", type=int, default=MAX_SOURCES, help="最大书源数量")
    parser.add_argument("--domains", "-d", type=int, default=1000, help="目标域名数量")
    args = parser.parse_args()

    base_dir = Path(__file__).parent.parent
    existing_path = primary_source_file(base_dir)
    new_path = base_dir / "sources/legado/yiove_new.json"
    output_path = existing_path
    backup_path = output_path.parent / "full.backup.json"

    # 读取现有书源
    print("读取现有书源...")
    with open(existing_path, 'r', encoding='utf-8') as f:
        existing = json.load(f)
    print(f"  现有: {len(existing)} 个")

    # 读取新书源
    print("读取新书源...")
    with open(new_path, 'r', encoding='utf-8') as f:
        new_sources = json.load(f)
    print(f"  新增: {len(new_sources)} 个")

    # 筛选新书源（严格筛选）
    print("\n筛选新书源...")
    new_filtered = filter_sources(new_sources, check_respond_time=True)
    print(f"  筛选后: {len(new_filtered)} 个")

    # 筛选现有书源（宽松筛选，不检查响应时间）
    print("筛选现有书源...")
    existing_filtered = filter_sources(existing, check_respond_time=False)
    print(f"  筛选后: {len(existing_filtered)} 个")

    # 清洗
    print("清洗书源...")
    new_filtered = [clean_source(s) for s in new_filtered]
    existing_filtered = [clean_source(s) for s in existing_filtered]

    # 预计算评分并缓存（现有书源有信任加分）
    print("\n计算评分...")
    score_cache = {}
    for s in existing_filtered:
        score_cache[id(s)] = calculate_quality_score(s, bonus=EXISTING_BONUS)
    for s in new_filtered:
        score_cache[id(s)] = calculate_quality_score(s, bonus=0)

    # 合并
    print("\n合并书源...")
    all_sources = existing_filtered + new_filtered
    print(f"  合并前: {len(all_sources)} 个")

    # 去重
    print("智能去重...")
    deduped = smart_dedupe(all_sources, score_cache, target_domains=args.domains)
    print(f"  去重后: {len(deduped)} 个")

    # 可选：网络校验
    if args.validate:
        print("\n有效性校验...")
        deduped = validate_sources(deduped)
        print(f"  有效: {len(deduped)} 个")

    # 排序取 top
    max_count = args.max
    print(f"\n按评分排序，取 top {max_count}...")
    deduped.sort(key=lambda x: -score_cache.get(id(x), calculate_quality_score(x)))
    final = deduped[:max_count]
    print(f"  最终: {len(final)} 个")

    # 备份
    if existing_path.exists():
        import shutil
        shutil.copy(existing_path, backup_path)
        print(f"\n已备份到: {backup_path}")

    # 输出
    written_paths = write_source_mirror(final, base_dir)
    print(f"输出到: {written_paths[0]}")

    # 统计
    print("\n=== 统计 ===")
    print(f"原有书源: {len(existing)} -> 筛选后 {len(existing_filtered)}")
    print(f"新增书源: {len(new_sources)} -> 筛选后 {len(new_filtered)}")
    print(f"合并去重: {len(deduped)}")
    print(f"最终输出: {len(final)}")

    # 评分分布
    scores = [score_cache.get(id(s), calculate_quality_score(s)) for s in final]
    print(f"\n评分分布:")
    print(f"  50-65: {sum(1 for s in scores if s >= 50)}")
    print(f"  40-49: {sum(1 for s in scores if 40 <= s < 50)}")
    print(f"  30-39: {sum(1 for s in scores if 30 <= s < 40)}")
    print(f"  25-29: {sum(1 for s in scores if 25 <= s < 30)}")


if __name__ == "__main__":
    main()
