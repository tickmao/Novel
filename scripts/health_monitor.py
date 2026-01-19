#!/usr/bin/env python3
"""
书源健康监控系统
- 监控书源数量和质量
- 生成健康报告
- 检测异常情况并预警
- 跟踪系统运行状态
"""

import json
import asyncio
import argparse
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

# 健康阈值配置
HEALTH_THRESHOLDS = {
    'source_count': {
        'excellent': 1000,
        'good': 900,
        'warning': 800,
        'critical': 700
    },
    'availability_rate': {
        'excellent': 0.95,
        'good': 0.90,
        'warning': 0.80,
        'critical': 0.70
    },
    'average_score': {
        'excellent': 50,
        'good': 45,
        'warning': 40,
        'critical': 35
    },
    'domain_diversity': {
        'excellent': 500,
        'good': 400,
        'warning': 300,
        'critical': 200
    }
}

class HealthMonitor:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.sources_dir = base_dir / "sources/legado"
        self.temp_dir = base_dir / "temp"
        self.reports_dir = base_dir / "reports"
        self.reports_dir.mkdir(exist_ok=True)

        # 文件路径
        self.main_sources_file = self.sources_dir / "full.json"
        self.history_file = self.temp_dir / "source_history.json"
        self.health_history_file = self.reports_dir / "health_history.json"

        # 监控数据
        self.current_health = {}
        self.health_history = self.load_health_history()

    def load_health_history(self) -> List[Dict]:
        """加载健康历史记录"""
        if not self.health_history_file.exists():
            return []

        try:
            with open(self.health_history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"加载健康历史失败: {e}")
            return []

    def save_health_history(self):
        """保存健康历史记录"""
        try:
            with open(self.health_history_file, 'w', encoding='utf-8') as f:
                json.dump(self.health_history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存健康历史失败: {e}")

    def load_sources(self) -> List[Dict]:
        """加载书源数据"""
        if not self.main_sources_file.exists():
            return []

        try:
            with open(self.main_sources_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"加载书源失败: {e}")
            return []

    def load_source_history(self) -> Dict:
        """加载书源历史数据"""
        if not self.history_file.exists():
            return {}

        try:
            with open(self.history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"加载书源历史失败: {e}")
            return {}

    def calculate_basic_score(self, source: dict) -> int:
        """计算基础评分（简化版）"""
        score = 0

        # 基础状态
        if source.get('enabled', True):
            score += 5
        if source.get('enabledExplore'):
            score += 2

        # 响应时间
        rt = source.get('respondTime', 99999)
        if rt < 1000:
            score += 15
        elif rt < 3000:
            score += 12
        elif rt < 5000:
            score += 8
        elif rt < 10000:
            score += 4

        # 规则完整性
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

        # 更新时间
        last = source.get('lastUpdateTime', 0)
        if last:
            days = (time.time() * 1000 - last) / 86400000
            days = max(0, days)
            if days < 30:
                score += 10
            elif days < 90:
                score += 7
            elif days < 180:
                score += 4
            elif days < 365:
                score += 2

        # 权重
        score += min(source.get('weight', 0) // 100, 5)

        return score

    def analyze_source_quality(self, sources: List[Dict]) -> Dict:
        """分析书源质量"""
        if not sources:
            return {
                'total_count': 0,
                'average_score': 0,
                'score_distribution': {},
                'domain_count': 0,
                'domain_distribution': {},
                'enabled_count': 0,
                'explore_enabled_count': 0
            }

        scores = []
        domains = defaultdict(int)
        enabled_count = 0
        explore_enabled_count = 0

        for source in sources:
            # 计算评分
            score = self.calculate_basic_score(source)
            scores.append(score)

            # 统计域名
            url = source.get('bookSourceUrl', '')
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc
                domains[domain] += 1
            except:
                pass

            # 统计启用状态
            if source.get('enabled', True):
                enabled_count += 1
            if source.get('enabledExplore'):
                explore_enabled_count += 1

        # 评分分布
        score_distribution = {
            '50+': sum(1 for s in scores if s >= 50),
            '40-49': sum(1 for s in scores if 40 <= s < 50),
            '30-39': sum(1 for s in scores if 30 <= s < 40),
            '20-29': sum(1 for s in scores if 20 <= s < 30),
            '<20': sum(1 for s in scores if s < 20)
        }

        # 域名分布（前10）
        top_domains = dict(sorted(domains.items(), key=lambda x: -x[1])[:10])

        return {
            'total_count': len(sources),
            'average_score': sum(scores) / len(scores) if scores else 0,
            'score_distribution': score_distribution,
            'domain_count': len(domains),
            'domain_distribution': top_domains,
            'enabled_count': enabled_count,
            'explore_enabled_count': explore_enabled_count
        }

    def calculate_availability_rate(self, sources: List[Dict]) -> float:
        """计算可用率（基于历史数据）"""
        source_history = self.load_source_history()
        if not source_history:
            return 0.85  # 默认估算值

        total_checks = 0
        total_valid = 0

        for source in sources:
            source_id = self.get_source_id(source)
            history = source_history.get(source_id, {})

            check_count = history.get('check_count', 0)
            valid_count = history.get('valid_count', 0)

            if check_count > 0:
                total_checks += check_count
                total_valid += valid_count

        return total_valid / total_checks if total_checks > 0 else 0.85

    def get_source_id(self, source: dict) -> str:
        """生成书源ID"""
        import hashlib
        url = source.get('bookSourceUrl', '')
        name = source.get('bookSourceName', '')
        return hashlib.md5(f"{url}:{name}".encode()).hexdigest()

    def get_health_level(self, metric: str, value: float) -> str:
        """获取健康等级"""
        thresholds = HEALTH_THRESHOLDS.get(metric, {})

        if value >= thresholds.get('excellent', float('inf')):
            return 'excellent'
        elif value >= thresholds.get('good', float('inf')):
            return 'good'
        elif value >= thresholds.get('warning', float('inf')):
            return 'warning'
        else:
            return 'critical'

    def generate_health_report(self) -> Dict:
        """生成健康报告"""
        print("生成健康报告...")

        # 加载数据
        sources = self.load_sources()
        quality_analysis = self.analyze_source_quality(sources)
        availability_rate = self.calculate_availability_rate(sources)

        # 计算健康指标
        source_count = quality_analysis['total_count']
        average_score = quality_analysis['average_score']
        domain_count = quality_analysis['domain_count']

        # 健康等级
        health_levels = {
            'source_count': self.get_health_level('source_count', source_count),
            'availability_rate': self.get_health_level('availability_rate', availability_rate),
            'average_score': self.get_health_level('average_score', average_score),
            'domain_diversity': self.get_health_level('domain_diversity', domain_count)
        }

        # 总体健康等级
        level_scores = {'excellent': 4, 'good': 3, 'warning': 2, 'critical': 1}
        avg_level_score = sum(level_scores[level] for level in health_levels.values()) / len(health_levels)

        if avg_level_score >= 3.5:
            overall_health = 'excellent'
        elif avg_level_score >= 2.5:
            overall_health = 'good'
        elif avg_level_score >= 1.5:
            overall_health = 'warning'
        else:
            overall_health = 'critical'

        # 生成报告
        report = {
            'timestamp': datetime.now().isoformat(),
            'overall_health': overall_health,
            'metrics': {
                'source_count': {
                    'value': source_count,
                    'level': health_levels['source_count'],
                    'target': HEALTH_THRESHOLDS['source_count']['excellent']
                },
                'availability_rate': {
                    'value': availability_rate,
                    'level': health_levels['availability_rate'],
                    'target': HEALTH_THRESHOLDS['availability_rate']['excellent']
                },
                'average_score': {
                    'value': average_score,
                    'level': health_levels['average_score'],
                    'target': HEALTH_THRESHOLDS['average_score']['excellent']
                },
                'domain_diversity': {
                    'value': domain_count,
                    'level': health_levels['domain_diversity'],
                    'target': HEALTH_THRESHOLDS['domain_diversity']['excellent']
                }
            },
            'quality_analysis': quality_analysis,
            'recommendations': self.generate_recommendations(health_levels, quality_analysis)
        }

        self.current_health = report
        return report

    def generate_recommendations(self, health_levels: Dict, quality_analysis: Dict) -> List[str]:
        """生成改进建议"""
        recommendations = []

        # 书源数量建议
        if health_levels['source_count'] in ['warning', 'critical']:
            gap = HEALTH_THRESHOLDS['source_count']['excellent'] - quality_analysis['total_count']
            recommendations.append(f"书源数量不足，建议补充 {gap} 个高质量书源")

        # 可用率建议
        if health_levels['availability_rate'] in ['warning', 'critical']:
            recommendations.append("可用率偏低，建议执行智能恢复流程")

        # 评分建议
        if health_levels['average_score'] in ['warning', 'critical']:
            low_score_count = quality_analysis['score_distribution'].get('<20', 0) + \
                            quality_analysis['score_distribution'].get('20-29', 0)
            if low_score_count > 0:
                recommendations.append(f"发现 {low_score_count} 个低分书源，建议清理或优化")

        # 域名多样性建议
        if health_levels['domain_diversity'] in ['warning', 'critical']:
            recommendations.append("域名多样性不足，建议收集更多不同域名的书源")

        # 通用建议
        if not recommendations:
            recommendations.append("系统运行良好，建议继续保持定期监控")

        return recommendations

    def detect_anomalies(self) -> List[Dict]:
        """检测异常情况"""
        anomalies = []

        if len(self.health_history) < 2:
            return anomalies

        current = self.current_health
        previous = self.health_history[-1]

        # 检测书源数量急剧下降
        current_count = current['metrics']['source_count']['value']
        previous_count = previous['metrics']['source_count']['value']

        if current_count < previous_count * 0.9:  # 下降超过10%
            anomalies.append({
                'type': 'source_count_drop',
                'severity': 'high',
                'message': f"书源数量急剧下降：{previous_count} → {current_count}",
                'change': current_count - previous_count
            })

        # 检测可用率急剧下降
        current_rate = current['metrics']['availability_rate']['value']
        previous_rate = previous['metrics']['availability_rate']['value']

        if current_rate < previous_rate - 0.1:  # 下降超过10%
            anomalies.append({
                'type': 'availability_drop',
                'severity': 'high',
                'message': f"可用率急剧下降：{previous_rate:.2%} → {current_rate:.2%}",
                'change': current_rate - previous_rate
            })

        # 检测平均评分下降
        current_score = current['metrics']['average_score']['value']
        previous_score = previous['metrics']['average_score']['value']

        if current_score < previous_score - 5:  # 下降超过5分
            anomalies.append({
                'type': 'score_drop',
                'severity': 'medium',
                'message': f"平均评分下降：{previous_score:.1f} → {current_score:.1f}",
                'change': current_score - previous_score
            })

        return anomalies

    def should_trigger_supplement(self) -> Tuple[bool, str]:
        """判断是否应该触发自动补充"""
        if not self.current_health:
            return False, "无健康数据"

        metrics = self.current_health['metrics']

        # 检查书源数量
        source_count = metrics['source_count']['value']
        if source_count < HEALTH_THRESHOLDS['source_count']['critical']:
            return True, f"书源数量严重不足 ({source_count})"

        if source_count < HEALTH_THRESHOLDS['source_count']['warning']:
            return True, f"书源数量不足 ({source_count})"

        # 检查可用率
        availability = metrics['availability_rate']['value']
        if availability < HEALTH_THRESHOLDS['availability_rate']['warning']:
            return True, f"可用率过低 ({availability:.2%})"

        return False, "健康状况良好"

    def print_health_report(self, report: Dict):
        """打印健康报告"""
        print("\n" + "="*50)
        print("📊 书源健康报告")
        print("="*50)

        # 总体健康状况
        health_emoji = {
            'excellent': '🟢',
            'good': '🟡',
            'warning': '🟠',
            'critical': '🔴'
        }

        overall = report['overall_health']
        print(f"\n总体健康状况: {health_emoji[overall]} {overall.upper()}")

        # 详细指标
        print(f"\n📈 详细指标:")
        metrics = report['metrics']

        for metric_name, metric_data in metrics.items():
            value = metric_data['value']
            level = metric_data['level']
            target = metric_data['target']

            if metric_name == 'source_count':
                print(f"  书源数量: {value:,} / {target:,} {health_emoji[level]}")
            elif metric_name == 'availability_rate':
                print(f"  可用率: {value:.2%} / {target:.2%} {health_emoji[level]}")
            elif metric_name == 'average_score':
                print(f"  平均评分: {value:.1f} / {target} {health_emoji[level]}")
            elif metric_name == 'domain_diversity':
                print(f"  域名多样性: {value:,} / {target:,} {health_emoji[level]}")

        # 质量分析
        quality = report['quality_analysis']
        print(f"\n📊 质量分析:")
        print(f"  启用书源: {quality['enabled_count']:,} / {quality['total_count']:,}")
        print(f"  探索功能: {quality['explore_enabled_count']:,}")

        print(f"\n  评分分布:")
        for score_range, count in quality['score_distribution'].items():
            percentage = count * 100 // quality['total_count'] if quality['total_count'] > 0 else 0
            print(f"    {score_range:>6}: {count:>4} ({percentage:>2}%)")

        # 改进建议
        recommendations = report['recommendations']
        if recommendations:
            print(f"\n💡 改进建议:")
            for i, rec in enumerate(recommendations, 1):
                print(f"  {i}. {rec}")

    async def daily_health_check(self) -> Dict:
        """每日健康检查"""
        print("执行每日健康检查...")

        # 生成健康报告
        report = self.generate_health_report()

        # 检测异常
        anomalies = self.detect_anomalies()

        # 添加异常信息到报告
        report['anomalies'] = anomalies

        # 保存到历史记录
        self.health_history.append(report)

        # 只保留最近30天的记录
        cutoff_date = datetime.now() - timedelta(days=30)
        self.health_history = [
            h for h in self.health_history
            if datetime.fromisoformat(h['timestamp']) > cutoff_date
        ]

        self.save_health_history()

        # 打印报告
        self.print_health_report(report)

        # 打印异常
        if anomalies:
            print(f"\n⚠️  检测到 {len(anomalies)} 个异常:")
            for anomaly in anomalies:
                severity_emoji = {'high': '🔴', 'medium': '🟠', 'low': '🟡'}
                print(f"  {severity_emoji[anomaly['severity']]} {anomaly['message']}")

        # 检查是否需要触发补充
        should_supplement, reason = self.should_trigger_supplement()
        if should_supplement:
            print(f"\n🚨 建议触发自动补充: {reason}")
            report['supplement_recommended'] = True
            report['supplement_reason'] = reason
        else:
            print(f"\n✅ 无需补充: {reason}")
            report['supplement_recommended'] = False

        return report

async def main():
    parser = argparse.ArgumentParser(description="书源健康监控系统")
    parser.add_argument("--report", "-r", help="生成报告并保存到指定文件")
    parser.add_argument("--check-supplement", action="store_true", help="检查是否需要触发自动补充")
    parser.add_argument("--history", action="store_true", help="显示健康历史趋势")
    args = parser.parse_args()

    # 获取项目根目录
    base_dir = Path(__file__).parent.parent

    # 初始化健康监控
    monitor = HealthMonitor(base_dir)

    try:
        # 执行健康检查
        report = await monitor.daily_health_check()

        # 保存报告
        if args.report:
            report_path = Path(args.report)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print(f"\n报告已保存到: {report_path}")

        # 检查补充建议
        if args.check_supplement:
            if report.get('supplement_recommended'):
                print(f"\n退出码: 1 (需要补充)")
                return 1
            else:
                print(f"\n退出码: 0 (无需补充)")
                return 0

        # 显示历史趋势
        if args.history and len(monitor.health_history) > 1:
            print(f"\n📈 健康趋势 (最近7天):")
            recent_history = monitor.health_history[-7:]
            for h in recent_history:
                date = datetime.fromisoformat(h['timestamp']).strftime('%m-%d')
                count = h['metrics']['source_count']['value']
                rate = h['metrics']['availability_rate']['value']
                score = h['metrics']['average_score']['value']
                health = h['overall_health']
                print(f"  {date}: {count:4d}源 {rate:.1%}可用 {score:4.1f}分 {health}")

        return 0

    except Exception as e:
        print(f"\n❌ 健康检查失败: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit(asyncio.run(main()))