#!/usr/bin/env python3
"""
增强书源评分系统
- 扩展原有57分评分到87分
- 新增稳定性、内容质量、用户反馈评分
- 支持动态权重调整
- 历史数据追踪
"""

import json
import time
import argparse
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

# 评分配置
SCORING_CONFIG = {
    # 原有评分维度 (57分)
    'basic_status': {'max': 7, 'enabled': 5, 'explore': 2},
    'response_time': {'max': 15, 'thresholds': [(1000, 15), (3000, 12), (5000, 8), (10000, 4)]},
    'rule_completeness': {'max': 20, 'search_url': 4, 'search_rule': 4, 'toc_rule': 4, 'content_rule': 6, 'explore_url': 2},
    'update_time': {'max': 10, 'thresholds': [(30, 10), (90, 7), (180, 4), (365, 2)]},
    'weight': {'max': 5, 'divisor': 100},

    # 新增评分维度 (30分)
    'stability': {'max': 15, 'availability_rate': 10, 'consecutive_days': 5},
    'content_quality': {'max': 10, 'book_count': 4, 'update_frequency': 3, 'content_completeness': 3},
    'user_feedback': {'max': 5, 'user_rating': 3, 'usage_frequency': 2}
}

# 动态权重配置
WEIGHT_CONFIG = {
    'new_source': 0.5,      # 新书源观察期权重
    'stable_source': 1.0,   # 稳定书源权重
    'premium_source': 1.5,  # 优质书源权重
    'trusted_source': 2.0   # 信任书源权重
}

class EnhancedScoring:
    def __init__(self, history_file: Optional[Path] = None):
        self.history_file = history_file
        self.history_data = self.load_history()
        self.scoring_stats = defaultdict(int)

    def load_history(self) -> Dict:
        """加载历史数据"""
        if not self.history_file or not self.history_file.exists():
            return {}

        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"加载历史数据失败: {e}")
            return {}

    def save_history(self):
        """保存历史数据"""
        if not self.history_file:
            return

        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self.history_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存历史数据失败: {e}")

    def get_source_id(self, source: dict) -> str:
        """生成书源唯一ID"""
        url = source.get('bookSourceUrl', '')
        name = source.get('bookSourceName', '')
        return hashlib.md5(f"{url}:{name}".encode()).hexdigest()

    def update_source_history(self, source: dict, is_valid: bool = True):
        """更新书源历史记录"""
        source_id = self.get_source_id(source)
        current_time = int(time.time())

        if source_id not in self.history_data:
            self.history_data[source_id] = {
                'url': source.get('bookSourceUrl', ''),
                'name': source.get('bookSourceName', ''),
                'first_seen': current_time,
                'last_seen': current_time,
                'check_count': 0,
                'valid_count': 0,
                'consecutive_valid_days': 0,
                'consecutive_invalid_days': 0,
                'last_valid_date': None,
                'quality_scores': [],
                'user_ratings': [],
                'usage_count': 0
            }

        history = self.history_data[source_id]
        history['last_seen'] = current_time
        history['check_count'] += 1

        if is_valid:
            history['valid_count'] += 1
            today = datetime.now().strftime('%Y-%m-%d')

            if history['last_valid_date'] == today:
                # 同一天多次检查，不重复计算
                pass
            else:
                if history['last_valid_date']:
                    last_date = datetime.strptime(history['last_valid_date'], '%Y-%m-%d')
                    if (datetime.now() - last_date).days == 1:
                        history['consecutive_valid_days'] += 1
                    else:
                        history['consecutive_valid_days'] = 1
                else:
                    history['consecutive_valid_days'] = 1

                history['last_valid_date'] = today
                history['consecutive_invalid_days'] = 0
        else:
            history['consecutive_invalid_days'] += 1
            if history['consecutive_invalid_days'] > 7:  # 连续7天无效重置连续有效天数
                history['consecutive_valid_days'] = 0

    def calculate_basic_score(self, source: dict) -> Tuple[int, Dict]:
        """计算基础评分 (原有57分)"""
        details = {}
        total_score = 0

        # 1. 基础状态 (0-7分)
        basic_score = 0
        if source.get('enabled', True):
            basic_score += SCORING_CONFIG['basic_status']['enabled']
        if source.get('enabledExplore'):
            basic_score += SCORING_CONFIG['basic_status']['explore']

        details['basic_status'] = basic_score
        total_score += basic_score

        # 2. 响应时间 (0-15分)
        rt = source.get('respondTime', 99999)
        rt_score = 0
        for threshold, score in SCORING_CONFIG['response_time']['thresholds']:
            if rt < threshold:
                rt_score = score
                break

        details['response_time'] = rt_score
        total_score += rt_score

        # 3. 规则完整性 (0-20分)
        rule_score = 0
        if source.get('searchUrl'):
            rule_score += SCORING_CONFIG['rule_completeness']['search_url']
        if source.get('ruleSearch') or source.get('searchRule'):
            rule_score += SCORING_CONFIG['rule_completeness']['search_rule']
        if source.get('ruleToc') or source.get('tocRule'):
            rule_score += SCORING_CONFIG['rule_completeness']['toc_rule']
        if source.get('ruleContent') or source.get('contentRule'):
            rule_score += SCORING_CONFIG['rule_completeness']['content_rule']
        if source.get('exploreUrl'):
            rule_score += SCORING_CONFIG['rule_completeness']['explore_url']

        details['rule_completeness'] = rule_score
        total_score += rule_score

        # 4. 更新时间 (0-10分)
        last = source.get('lastUpdateTime', 0)
        update_score = 0
        if last:
            days = (time.time() * 1000 - last) / 86400000
            days = max(0, days)
            for threshold, score in SCORING_CONFIG['update_time']['thresholds']:
                if days < threshold:
                    update_score = score
                    break

        details['update_time'] = update_score
        total_score += update_score

        # 5. 权重 (0-5分)
        weight_score = min(source.get('weight', 0) // SCORING_CONFIG['weight']['divisor'],
                          SCORING_CONFIG['weight']['max'])
        details['weight'] = weight_score
        total_score += weight_score

        return total_score, details

    def calculate_stability_score(self, source: dict) -> Tuple[int, Dict]:
        """计算稳定性评分 (15分)"""
        details = {}
        total_score = 0
        source_id = self.get_source_id(source)
        history = self.history_data.get(source_id, {})

        # 1. 历史可用率 (0-10分)
        check_count = history.get('check_count', 0)
        valid_count = history.get('valid_count', 0)

        if check_count >= 10:  # 至少检查10次才计算可用率
            availability_rate = valid_count / check_count
            if availability_rate >= 0.95:
                availability_score = 10
            elif availability_rate >= 0.90:
                availability_score = 8
            elif availability_rate >= 0.80:
                availability_score = 6
            elif availability_rate >= 0.70:
                availability_score = 4
            elif availability_rate >= 0.60:
                availability_score = 2
            else:
                availability_score = 0
        else:
            # 新书源给予中等分数
            availability_score = 5

        details['availability_rate'] = availability_score
        total_score += availability_score

        # 2. 连续可用天数 (0-5分)
        consecutive_days = history.get('consecutive_valid_days', 0)
        if consecutive_days >= 30:
            consecutive_score = 5
        elif consecutive_days >= 14:
            consecutive_score = 4
        elif consecutive_days >= 7:
            consecutive_score = 3
        elif consecutive_days >= 3:
            consecutive_score = 2
        elif consecutive_days >= 1:
            consecutive_score = 1
        else:
            consecutive_score = 0

        details['consecutive_days'] = consecutive_score
        total_score += consecutive_score

        return total_score, details

    def calculate_content_quality_score(self, source: dict) -> Tuple[int, Dict]:
        """计算内容质量评分 (10分)"""
        details = {}
        total_score = 0

        # 1. 书籍数量估算 (0-4分) - 基于规则复杂度
        book_count_score = 0

        # 有探索功能的通常书籍更多
        if source.get('exploreUrl'):
            book_count_score += 2

        # 搜索规则复杂度
        search_rule = source.get('ruleSearch') or source.get('searchRule') or {}
        if isinstance(search_rule, dict):
            if len(str(search_rule)) > 200:  # 复杂搜索规则
                book_count_score += 1

        # 内容规则复杂度
        content_rule = source.get('ruleContent') or source.get('contentRule') or {}
        if isinstance(content_rule, dict):
            if len(str(content_rule)) > 300:  # 复杂内容规则
                book_count_score += 1

        book_count_score = min(book_count_score, 4)
        details['book_count'] = book_count_score
        total_score += book_count_score

        # 2. 更新频率 (0-3分) - 基于最后更新时间
        last_update = source.get('lastUpdateTime', 0)
        if last_update:
            days_since_update = (time.time() * 1000 - last_update) / 86400000
            if days_since_update < 7:
                update_freq_score = 3
            elif days_since_update < 30:
                update_freq_score = 2
            elif days_since_update < 90:
                update_freq_score = 1
            else:
                update_freq_score = 0
        else:
            update_freq_score = 0

        details['update_frequency'] = update_freq_score
        total_score += update_freq_score

        # 3. 内容完整性 (0-3分) - 基于规则完整性
        completeness_score = 0

        # 有目录规则
        if source.get('ruleToc') or source.get('tocRule'):
            completeness_score += 1

        # 有内容规则
        if source.get('ruleContent') or source.get('contentRule'):
            completeness_score += 1

        # 有书籍信息规则
        if source.get('ruleBookInfo') or source.get('bookInfoRule'):
            completeness_score += 1

        details['content_completeness'] = completeness_score
        total_score += completeness_score

        return total_score, details

    def calculate_user_feedback_score(self, source: dict) -> Tuple[int, Dict]:
        """计算用户反馈评分 (5分)"""
        details = {}
        total_score = 0
        source_id = self.get_source_id(source)
        history = self.history_data.get(source_id, {})

        # 1. 用户评价 (0-3分) - 基于历史评分
        user_ratings = history.get('user_ratings', [])
        if user_ratings:
            avg_rating = sum(user_ratings) / len(user_ratings)
            if avg_rating >= 4.5:
                rating_score = 3
            elif avg_rating >= 4.0:
                rating_score = 2
            elif avg_rating >= 3.5:
                rating_score = 1
            else:
                rating_score = 0
        else:
            # 新书源给予中等分数
            rating_score = 1

        details['user_rating'] = rating_score
        total_score += rating_score

        # 2. 使用频率 (0-2分) - 基于历史使用次数
        usage_count = history.get('usage_count', 0)
        if usage_count >= 100:
            usage_score = 2
        elif usage_count >= 10:
            usage_score = 1
        else:
            usage_score = 0

        details['usage_frequency'] = usage_score
        total_score += usage_score

        return total_score, details

    def get_dynamic_weight(self, source: dict) -> float:
        """获取动态权重"""
        source_id = self.get_source_id(source)
        history = self.history_data.get(source_id, {})

        # 新书源（首次见到不超过7天）
        first_seen = history.get('first_seen', time.time())
        days_since_first = (time.time() - first_seen) / 86400

        if days_since_first < 7:
            return WEIGHT_CONFIG['new_source']

        # 根据稳定性和质量确定权重
        check_count = history.get('check_count', 0)
        valid_count = history.get('valid_count', 0)
        consecutive_days = history.get('consecutive_valid_days', 0)

        if check_count >= 30 and valid_count / check_count >= 0.95 and consecutive_days >= 30:
            return WEIGHT_CONFIG['trusted_source']
        elif check_count >= 20 and valid_count / check_count >= 0.90 and consecutive_days >= 14:
            return WEIGHT_CONFIG['premium_source']
        elif check_count >= 10 and valid_count / check_count >= 0.80:
            return WEIGHT_CONFIG['stable_source']
        else:
            return WEIGHT_CONFIG['new_source']

    def calculate_enhanced_score(self, source: dict, bonus: int = 0) -> Tuple[int, Dict]:
        """计算增强评分 (总分87分 + bonus)"""
        all_details = {}
        total_score = bonus

        # 1. 基础评分 (57分)
        basic_score, basic_details = self.calculate_basic_score(source)
        all_details['basic'] = basic_details
        total_score += basic_score

        # 2. 稳定性评分 (15分)
        stability_score, stability_details = self.calculate_stability_score(source)
        all_details['stability'] = stability_details
        total_score += stability_score

        # 3. 内容质量评分 (10分)
        content_score, content_details = self.calculate_content_quality_score(source)
        all_details['content_quality'] = content_details
        total_score += content_score

        # 4. 用户反馈评分 (5分)
        feedback_score, feedback_details = self.calculate_user_feedback_score(source)
        all_details['user_feedback'] = feedback_details
        total_score += feedback_score

        # 5. 应用动态权重
        weight = self.get_dynamic_weight(source)
        weighted_score = int(total_score * weight)
        all_details['dynamic_weight'] = weight
        all_details['weighted_score'] = weighted_score

        # 更新统计
        self.scoring_stats['total_scored'] += 1
        if weighted_score >= 70:
            self.scoring_stats['premium'] += 1
        elif weighted_score >= 50:
            self.scoring_stats['good'] += 1
        elif weighted_score >= 35:
            self.scoring_stats['acceptable'] += 1
        else:
            self.scoring_stats['poor'] += 1

        return weighted_score, all_details

    def batch_score_sources(self, sources: List[dict], bonus_map: Dict = None) -> List[Tuple[dict, int, Dict]]:
        """批量评分书源"""
        results = []
        bonus_map = bonus_map or {}

        print(f"开始增强评分 {len(sources)} 个书源...")

        for i, source in enumerate(sources):
            source_id = self.get_source_id(source)
            bonus = bonus_map.get(source_id, 0)

            score, details = self.calculate_enhanced_score(source, bonus)
            results.append((source, score, details))

            # 进度显示
            if (i + 1) % 100 == 0 or (i + 1) == len(sources):
                print(f"  进度: {i + 1}/{len(sources)} ({(i + 1) * 100 // len(sources)}%)")

        return results

    def print_scoring_stats(self):
        """打印评分统计"""
        print("\n=== 增强评分统计 ===")
        total = self.scoring_stats['total_scored']
        if total > 0:
            print(f"总计评分: {total} 个")
            print(f"优质书源 (≥70分): {self.scoring_stats['premium']} 个 ({self.scoring_stats['premium'] * 100 // total}%)")
            print(f"良好书源 (50-69分): {self.scoring_stats['good']} 个 ({self.scoring_stats['good'] * 100 // total}%)")
            print(f"可接受书源 (35-49分): {self.scoring_stats['acceptable']} 个 ({self.scoring_stats['acceptable'] * 100 // total}%)")
            print(f"较差书源 (<35分): {self.scoring_stats['poor']} 个 ({self.scoring_stats['poor'] * 100 // total}%)")

def main():
    parser = argparse.ArgumentParser(description="增强书源评分系统")
    parser.add_argument("--input", "-i", required=True, help="输入书源文件路径")
    parser.add_argument("--output", "-o", help="输出评分结果文件路径")
    parser.add_argument("--history", help="历史数据文件路径")
    parser.add_argument("--update-history", action="store_true", help="更新历史数据（假设所有书源有效）")
    parser.add_argument("--validate", action="store_true", help="验证评分系统准确性")
    parser.add_argument("--min-score", type=int, default=35, help="最低评分阈值")
    args = parser.parse_args()

    # 读取书源
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"错误：输入文件不存在 {input_path}")
        return 1

    with open(input_path, "r", encoding="utf-8") as f:
        sources = json.load(f)

    print(f"读取书源：{len(sources)} 个")

    # 初始化评分系统
    history_file = Path(args.history) if args.history else None
    scorer = EnhancedScoring(history_file)

    # 更新历史数据
    if args.update_history:
        print("更新历史数据...")
        for source in sources:
            scorer.update_source_history(source, is_valid=True)
        scorer.save_history()

    # 批量评分
    results = scorer.batch_score_sources(sources)

    # 按评分排序
    results.sort(key=lambda x: -x[1])

    # 过滤低分书源
    filtered_results = [(s, score, details) for s, score, details in results if score >= args.min_score]

    print(f"\n评分完成：")
    print(f"  总计：{len(results)} 个")
    print(f"  ≥{args.min_score}分：{len(filtered_results)} 个")
    print(f"  过滤：{len(results) - len(filtered_results)} 个")

    scorer.print_scoring_stats()

    # 输出结果
    if args.output:
        output_data = {
            'timestamp': datetime.now().isoformat(),
            'total_sources': len(sources),
            'scored_sources': len(results),
            'filtered_sources': len(filtered_results),
            'min_score': args.min_score,
            'sources': [
                {
                    'source': source,
                    'score': score,
                    'details': details
                }
                for source, score, details in filtered_results
            ]
        }

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

        print(f"\n评分结果已保存到：{output_path}")

    # 验证模式
    if args.validate:
        print("\n=== 评分验证 ===")
        # 显示评分分布
        scores = [score for _, score, _ in results]
        print(f"最高分：{max(scores) if scores else 0}")
        print(f"最低分：{min(scores) if scores else 0}")
        print(f"平均分：{sum(scores) // len(scores) if scores else 0}")

        # 显示前10名详细信息
        print("\n前10名书源详细评分：")
        for i, (source, score, details) in enumerate(results[:10]):
            print(f"{i+1:2d}. {source.get('bookSourceName', 'Unknown'):30s} {score:3d}分")
            print(f"    基础: {sum(details['basic'].values()):2d}, 稳定: {sum(details['stability'].values()):2d}, "
                  f"质量: {sum(details['content_quality'].values()):2d}, 反馈: {sum(details['user_feedback'].values()):2d}, "
                  f"权重: {details['dynamic_weight']:.1f}")

    return 0

if __name__ == "__main__":
    exit(main())