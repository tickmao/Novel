#!/usr/bin/env python3
"""
智能选择器 - 从候选池选择高质量书源
- 评分排序
- 域名分散算法（每域名最多2个）
- Top N 选择
- 支持多种选择策略
"""

import json
import argparse
from pathlib import Path
from typing import List, Dict, Tuple
from urllib.parse import urlparse
from collections import defaultdict


class SourceSelector:
    """智能选择器"""

    # 默认配置
    DEFAULT_MAX_PER_DOMAIN = 2
    DEFAULT_TARGET_COUNT = 1000

    def __init__(
        self,
        max_per_domain: int = DEFAULT_MAX_PER_DOMAIN,
        target_count: int = DEFAULT_TARGET_COUNT
    ):
        """
        初始化智能选择器

        Args:
            max_per_domain: 每个域名最多选择的书源数
            target_count: 目标书源数量
        """
        self.max_per_domain = max_per_domain
        self.target_count = target_count

    def extract_domain(self, url: str) -> str:
        """
        提取域名

        Args:
            url: URL 字符串

        Returns:
            域名
        """
        try:
            parsed = urlparse(url)
            domain = parsed.netloc or parsed.path.split('/')[0]
            # 移除 www. 前缀
            if domain.startswith('www.'):
                domain = domain[4:]
            return domain
        except Exception:
            return url

    def calculate_score(self, source: Dict) -> float:
        """
        计算书源评分

        Args:
            source: 书源字典

        Returns:
            评分（0-100）
        """
        # 如果已有选择分，优先使用
        if 'selectionScore' in source:
            return float(source['selectionScore'])

        # 其次使用已有评分
        if 'score' in source:
            return float(source['score'])

        # 否则使用简单评分
        score = 0.0

        # 基础分：有效性（10分）
        if source.get('_is_valid', True):
            score += 10

        # 响应时间（15分）
        response_time = source.get('_response_time')
        if response_time:
            if response_time < 1000:
                score += 15
            elif response_time < 3000:
                score += 12
            elif response_time < 5000:
                score += 8
            else:
                score += 4

        # 规则完整性（20分）
        rule_fields = ['ruleSearch', 'ruleToc', 'ruleContent']
        complete_rules = sum(1 for field in rule_fields if source.get(field))
        score += (complete_rules / len(rule_fields)) * 20

        # 启用状态（5分）
        if source.get('enabled', True):
            score += 5

        # 权重（5分）
        weight = source.get('weight', 0)
        score += min(weight / 100, 1) * 5

        return score

    def sort_by_score(self, sources: List[Dict]) -> List[Dict]:
        """
        按评分排序

        Args:
            sources: 书源列表

        Returns:
            排序后的书源列表
        """
        # 计算评分
        for source in sources:
            if 'score' not in source:
                source['score'] = self.calculate_score(source)

        # 按评分降序排序
        return sorted(sources, key=lambda s: s.get('score', 0), reverse=True)

    def select_with_domain_diversity(
        self,
        sources: List[Dict]
    ) -> Tuple[List[Dict], Dict]:
        """
        使用域名分散算法选择书源

        Args:
            sources: 已排序的书源列表

        Returns:
            (选中的书源, 统计信息)
        """
        selected = []
        domain_count = defaultdict(int)
        skipped_by_domain = 0
        total_domains = set()

        for source in sources:
            # 达到目标数量
            if len(selected) >= self.target_count:
                break

            url = source.get('bookSourceUrl', '')
            domain = self.extract_domain(url)
            total_domains.add(domain)

            # 检查域名限制
            if domain_count[domain] >= self.max_per_domain:
                skipped_by_domain += 1
                continue

            selected.append(source)
            domain_count[domain] += 1

        # 统计信息
        stats = {
            'total_candidates': len(sources),
            'selected': len(selected),
            'target': self.target_count,
            'unique_domains': len(domain_count),
            'total_domains': len(total_domains),
            'skipped_by_domain': skipped_by_domain,
            'avg_score': sum(s.get('score', 0) for s in selected) / len(selected) if selected else 0,
            'min_score': min((s.get('score', 0) for s in selected), default=0),
            'max_score': max((s.get('score', 0) for s in selected), default=0)
        }

        return selected, stats

    def select_top_n(self, sources: List[Dict]) -> Tuple[List[Dict], Dict]:
        """
        简单选择 Top N

        Args:
            sources: 已排序的书源列表

        Returns:
            (选中的书源, 统计信息)
        """
        selected = sources[:self.target_count]

        # 统计域名
        domains = set()
        for source in selected:
            url = source.get('bookSourceUrl', '')
            domain = self.extract_domain(url)
            domains.add(domain)

        stats = {
            'total_candidates': len(sources),
            'selected': len(selected),
            'target': self.target_count,
            'unique_domains': len(domains),
            'avg_score': sum(s.get('score', 0) for s in selected) / len(selected) if selected else 0,
            'min_score': min((s.get('score', 0) for s in selected), default=0),
            'max_score': max((s.get('score', 0) for s in selected), default=0)
        }

        return selected, stats

    def select(
        self,
        sources: List[Dict],
        strategy: str = 'domain_diversity'
    ) -> Tuple[List[Dict], Dict]:
        """
        选择书源

        Args:
            sources: 书源列表
            strategy: 选择策略（domain_diversity 或 top_n）

        Returns:
            (选中的书源, 统计信息)
        """
        print(f'\n=== 智能选择开始 ===')
        print(f'候选书源：{len(sources)} 个')
        print(f'目标数量：{self.target_count} 个')
        print(f'选择策略：{strategy}')
        print(f'域名限制：每域名最多 {self.max_per_domain} 个\n')

        # 步骤 1：按评分排序
        print('步骤 1：按评分排序...')
        sorted_sources = self.sort_by_score(sources)
        print(f'✓ 排序完成')

        # 步骤 2：选择
        print(f'\n步骤 2：使用 {strategy} 策略选择...')
        if strategy == 'domain_diversity':
            selected, stats = self.select_with_domain_diversity(sorted_sources)
        elif strategy == 'top_n':
            selected, stats = self.select_top_n(sorted_sources)
        else:
            raise ValueError(f'未知策略：{strategy}')

        print(f'✓ 选择完成：{len(selected)} 个书源')

        # 步骤 3：打印统计
        print('\n=== 选择统计 ===')
        print(f'候选书源：{stats["total_candidates"]} 个')
        print(f'选中书源：{stats["selected"]} 个')
        print(f'唯一域名：{stats["unique_domains"]} 个')
        print(f'平均评分：{stats["avg_score"]:.1f}')
        print(f'评分范围：{stats["min_score"]:.1f} - {stats["max_score"]:.1f}')

        if strategy == 'domain_diversity':
            print(f'总域名数：{stats["total_domains"]} 个')
            print(f'域名限制跳过：{stats["skipped_by_domain"]} 个')

        print('\n=== 智能选择完成 ===\n')

        return selected, stats


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description='智能选择器 - 从候选池选择高质量书源')
    parser.add_argument('--input', type=Path, required=True, help='输入文件（候选书源）')
    parser.add_argument('--output', type=Path, required=True, help='输出文件（选中书源）')
    parser.add_argument('--target', type=int, default=1000, help='目标书源数量')
    parser.add_argument('--max-per-domain', type=int, default=2, help='每域名最多书源数')
    parser.add_argument(
        '--strategy',
        choices=['domain_diversity', 'top_n'],
        default='domain_diversity',
        help='选择策略'
    )
    parser.add_argument('--report', type=Path, help='统计报告输出文件')

    args = parser.parse_args()

    # 读取候选书源
    with open(args.input, 'r', encoding='utf-8') as f:
        sources = json.load(f)

    # 创建选择器
    selector = SourceSelector(
        max_per_domain=args.max_per_domain,
        target_count=args.target
    )

    # 执行选择
    selected, stats = selector.select(sources, strategy=args.strategy)

    # 保存结果
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(selected, f, ensure_ascii=False, indent=2)
    print(f'✓ 选中书源已保存：{args.output}')

    # 保存统计报告
    if args.report:
        from datetime import datetime

        report = {
            'timestamp': datetime.now().isoformat(),
            'input_file': str(args.input),
            'output_file': str(args.output),
            'strategy': args.strategy,
            'max_per_domain': args.max_per_domain,
            'stats': stats
        }

        with open(args.report, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f'✓ 统计报告已保存：{args.report}')


if __name__ == '__main__':
    main()
