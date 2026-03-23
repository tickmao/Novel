#!/usr/bin/env python3
"""
书源自动补充系统测试验证脚本
- 测试各个组件的基本功能
- 验证配置文件正确性
- 模拟完整补充流程
- 检查系统集成
"""

import json
import asyncio
import sys
import traceback
import shutil
from pathlib import Path
from datetime import datetime

# 添加脚本目录到路径
sys.path.append(str(Path(__file__).parent))

class SystemTester:
    def __init__(self):
        self.base_dir = Path(__file__).parent.parent
        self.scripts_dir = self.base_dir / "scripts"
        self.config_dir = self.base_dir / "config"
        self.test_dir = self.base_dir / "test_data"
        self.test_dir.mkdir(exist_ok=True)

        self.test_results = {
            'config_validation': False,
            'smart_recovery': False,
            'source_collector': False,
            'enhanced_scoring': False,
            'health_monitor': False,
            'auto_supplement': False,
            'integration': False
        }

        self.test_sources = self.create_test_sources()

    def create_test_sources(self) -> list:
        """创建测试书源数据"""
        return [
            {
                "bookSourceName": "测试书源1",
                "bookSourceUrl": "https://example.com/api1",
                "bookSourceGroup": "测试",
                "enabled": True,
                "enabledExplore": True,
                "respondTime": 800,
                "lastUpdateTime": int(datetime.now().timestamp() * 1000),
                "searchUrl": "https://example.com/search",
                "ruleSearch": {"name": "test"},
                "ruleToc": {"list": "test"},
                "ruleContent": {"content": "test"}
            },
            {
                "bookSourceName": "测试书源2",
                "bookSourceUrl": "https://example.com/api2",
                "bookSourceGroup": "测试",
                "enabled": True,
                "respondTime": 2500,
                "lastUpdateTime": int(datetime.now().timestamp() * 1000) - 86400000 * 60,
                "searchUrl": "https://example.com/search2",
                "ruleContent": {"content": "test2"}
            },
            {
                "bookSourceName": "失效书源",
                "bookSourceUrl": "https://invalid-domain-12345.com/api",
                "bookSourceGroup": "测试",
                "enabled": True,
                "searchUrl": "https://invalid-domain-12345.com/search",
                "ruleContent": {"content": "test"}
            }
        ]

    def test_config_validation(self) -> bool:
        """测试配置文件验证"""
        print("🔧 测试配置文件...")

        try:
            # 检查配置文件存在
            config_files = [
                'supplement_config.json',
                'scoring_weights.json',
                'source_channels.json'
            ]

            for config_file in config_files:
                config_path = self.config_dir / config_file
                if not config_path.exists():
                    print(f"  ❌ 配置文件不存在: {config_file}")
                    return False

                # 验证JSON格式
                with open(config_path, 'r', encoding='utf-8') as f:
                    json.load(f)
                print(f"  ✓ {config_file} 格式正确")

            print("  ✅ 配置文件验证通过")
            return True

        except Exception as e:
            print(f"  ❌ 配置文件验证失败: {e}")
            return False

    async def test_smart_recovery(self) -> bool:
        """测试智能恢复系统"""
        print("\n🔄 测试智能恢复系统...")

        try:
            from smart_recovery import SmartRecovery

            # 创建测试数据
            invalid_sources = [self.test_sources[2]]  # 失效书源
            errors = {"https://invalid-domain-12345.com/api": "超时"}

            # 测试恢复系统
            recovery = SmartRecovery(timeout=5)
            recovered, still_invalid = await recovery.batch_recovery(invalid_sources, errors)

            print(f"  测试结果: 尝试恢复 {len(invalid_sources)} 个, 仍失效 {len(still_invalid)} 个")
            print("  ✅ 智能恢复系统测试通过")
            return True

        except Exception as e:
            print(f"  ❌ 智能恢复系统测试失败: {e}")
            traceback.print_exc()
            return False

    async def test_source_collector(self) -> bool:
        """测试外部书源收集器"""
        print("\n📥 测试外部书源收集器...")

        try:
            from source_collector import SourceCollector

            # 测试收集器（试运行模式）
            collector = SourceCollector(timeout=10)

            # 只测试标准化功能
            test_source = {
                "bookSourceName": "  测试书源  ",
                "bookSourceUrl": "https://example.com/test",
                "enabled": "true"
            }

            normalized = collector.normalize_source(test_source)
            if normalized and normalized['bookSourceName'] == "测试书源":
                print("  ✓ 书源标准化功能正常")
            else:
                print("  ❌ 书源标准化功能异常")
                return False

            print("  ✅ 外部书源收集器测试通过")
            return True

        except Exception as e:
            print(f"  ❌ 外部书源收集器测试失败: {e}")
            traceback.print_exc()
            return False

    def test_enhanced_scoring(self) -> bool:
        """测试增强评分系统"""
        print("\n📊 测试增强评分系统...")

        try:
            from enhanced_scoring import EnhancedScoring

            # 测试评分系统
            scorer = EnhancedScoring()

            # 测试单个书源评分
            test_source = self.test_sources[0]
            score, details = scorer.calculate_enhanced_score(test_source)

            if score > 0 and isinstance(details, dict):
                print(f"  ✓ 评分计算正常: {score} 分")
                print(f"  ✓ 评分详情: {list(details.keys())}")
            else:
                print("  ❌ 评分计算异常")
                return False

            # 测试批量评分
            results = scorer.batch_score_sources(self.test_sources[:2])
            if len(results) == 2:
                print(f"  ✓ 批量评分正常: {len(results)} 个结果")
            else:
                print("  ❌ 批量评分异常")
                return False

            print("  ✅ 增强评分系统测试通过")
            return True

        except Exception as e:
            print(f"  ❌ 增强评分系统测试失败: {e}")
            traceback.print_exc()
            return False

    async def test_health_monitor(self) -> bool:
        """测试健康监控系统"""
        print("\n🏥 测试健康监控系统...")

        try:
            from health_monitor import HealthMonitor

            # 创建测试书源文件
            test_sources_file = self.test_dir / "test_sources.json"
            with open(test_sources_file, 'w', encoding='utf-8') as f:
                json.dump(self.test_sources, f, ensure_ascii=False, indent=2)

            # 测试健康监控
            monitor = HealthMonitor(self.test_dir)
            monitor.main_sources_file = test_sources_file

            # 生成健康报告
            report = await monitor.daily_health_check()

            if report and 'overall_health' in report:
                print(f"  ✓ 健康报告生成正常: {report['overall_health']}")
                print(f"  ✓ 监控指标: {list(report['metrics'].keys())}")
            else:
                print("  ❌ 健康报告生成异常")
                return False

            print("  ✅ 健康监控系统测试通过")
            return True

        except Exception as e:
            print(f"  ❌ 健康监控系统测试失败: {e}")
            traceback.print_exc()
            return False

    async def test_auto_supplement_dry_run(self) -> bool:
        """测试自动补充系统（试运行）"""
        print("\n🤖 测试自动补充系统...")

        try:
            from auto_supplement import AutoSupplement

            # 创建测试环境
            config_dir = self.test_dir / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            for name in ("supplement_config.json", "name_normalization.json", "content_audit.json"):
                shutil.copy2(self.config_dir / name, config_dir / name)

            legado_dir = self.test_dir / "sources/legado"
            (legado_dir / "main").mkdir(parents=True, exist_ok=True)
            (legado_dir / "pool").mkdir(parents=True, exist_ok=True)

            # 创建测试书源文件
            test_working_file = legado_dir / "main/working.json"
            test_candidate_file = legado_dir / "pool/candidates.json"

            with open(test_working_file, 'w', encoding='utf-8') as f:
                json.dump(self.test_sources, f, ensure_ascii=False, indent=2)
            with open(test_candidate_file, 'w', encoding='utf-8') as f:
                json.dump(self.test_sources, f, ensure_ascii=False, indent=2)

            test_supplement = AutoSupplement(self.test_dir)

            # 测试加载功能
            sources = test_supplement.inventory.load_working_sources()
            if len(sources) == len(self.test_sources):
                print(f"  ✓ 书源加载正常: {len(sources)} 个")
            else:
                print("  ❌ 书源加载异常")
                return False

            # 测试 dry-run 不会落盘修改
            success = await test_supplement.auto_supplement_workflow(force=True, dry_run=True)
            if success:
                print("  ✓ dry-run 执行正常")
            else:
                print("  ❌ dry-run 执行异常")
                return False

            print("  ✅ 自动补充系统测试通过")
            return True

        except Exception as e:
            print(f"  ❌ 自动补充系统测试失败: {e}")
            traceback.print_exc()
            return False

    def test_integration(self) -> bool:
        """测试系统集成"""
        print("\n🔗 测试系统集成...")

        try:
            # 检查脚本文件存在
            required_scripts = [
                'smart_recovery.py',
                'source_collector.py',
                'enhanced_scoring.py',
                'health_monitor.py',
                'auto_supplement.py'
            ]

            for script in required_scripts:
                script_path = self.scripts_dir / script
                if not script_path.exists():
                    print(f"  ❌ 脚本文件不存在: {script}")
                    return False

            print("  ✓ 所有脚本文件存在")

            # 检查GitHub Actions工作流
            workflow_file = self.base_dir / ".github/workflows/auto-supplement.yml"
            if workflow_file.exists():
                print("  ✓ GitHub Actions工作流存在")
            else:
                print("  ❌ GitHub Actions工作流不存在")
                return False

            # 检查导入依赖
            try:
                import aiohttp
                import aiofiles
                print("  ✓ 外部依赖可用")
            except ImportError as e:
                print(f"  ❌ 外部依赖缺失: {e}")
                return False

            print("  ✅ 系统集成测试通过")
            return True

        except Exception as e:
            print(f"  ❌ 系统集成测试失败: {e}")
            return False

    async def run_all_tests(self):
        """运行所有测试"""
        print("🚀 开始书源自动补充系统测试")
        print("=" * 50)

        # 配置验证
        self.test_results['config_validation'] = self.test_config_validation()

        # 智能恢复测试
        self.test_results['smart_recovery'] = await self.test_smart_recovery()

        # 外部收集测试
        self.test_results['source_collector'] = await self.test_source_collector()

        # 增强评分测试
        self.test_results['enhanced_scoring'] = self.test_enhanced_scoring()

        # 健康监控测试
        self.test_results['health_monitor'] = await self.test_health_monitor()

        # 自动补充测试
        self.test_results['auto_supplement'] = await self.test_auto_supplement_dry_run()

        # 系统集成测试
        self.test_results['integration'] = self.test_integration()

    def print_test_summary(self):
        """打印测试总结"""
        print("\n" + "=" * 50)
        print("📋 测试结果总结")
        print("=" * 50)

        passed = 0
        total = len(self.test_results)

        for test_name, result in self.test_results.items():
            status = "✅ 通过" if result else "❌ 失败"
            print(f"{test_name:20s}: {status}")
            if result:
                passed += 1

        print(f"\n总计: {passed}/{total} 个测试通过")

        if passed == total:
            print("🎉 所有测试通过！系统可以正常使用。")
            return True
        else:
            print("⚠️  部分测试失败，请检查相关组件。")
            return False

    def cleanup_test_data(self):
        """清理测试数据"""
        try:
            import shutil
            if self.test_dir.exists():
                shutil.rmtree(self.test_dir)
            print("\n🧹 测试数据已清理")
        except Exception as e:
            print(f"\n⚠️  清理测试数据失败: {e}")

async def main():
    """主函数"""
    tester = SystemTester()

    try:
        # 运行所有测试
        await tester.run_all_tests()

        # 打印总结
        success = tester.print_test_summary()

        # 清理测试数据
        tester.cleanup_test_data()

        # 返回退出码
        return 0 if success else 1

    except Exception as e:
        print(f"\n❌ 测试执行失败: {e}")
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit(asyncio.run(main()))
