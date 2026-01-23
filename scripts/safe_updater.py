#!/usr/bin/env python3
"""
安全更新器 - 数据安全保障
- 数据合理性检查（数量、格式、评分）
- 版本化备份（保留最近10个）
- 原子更新（临时文件 + os.replace）
- 回滚机制
"""

import json
import os
import shutil
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import random


class SafeUpdateError(Exception):
    """安全更新异常"""
    pass


class SafeUpdater:
    """安全更新器"""

    # 数据合理性阈值
    MIN_SOURCES = 800
    MAX_SOURCES = 1200
    MIN_AVG_SCORE = 40
    MAX_BACKUPS = 10

    # 必需字段
    REQUIRED_FIELDS = [
        'bookSourceName',
        'bookSourceUrl',
        'bookSourceType'
    ]

    def __init__(self, base_dir: Path = None):
        """
        初始化安全更新器

        Args:
            base_dir: 基础目录，默认为 sources/legado
        """
        if base_dir is None:
            base_dir = Path(__file__).parent.parent / 'sources' / 'legado'

        self.base_dir = Path(base_dir)
        self.main_dir = self.base_dir / 'main'
        self.backup_dir = self.main_dir / 'backups'
        self.main_file = self.main_dir / 'full.json'

        # 确保目录存在
        self.main_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def validate_sources(self, sources: List[Dict]) -> Tuple[bool, str]:
        """
        验证书源数据合理性

        Args:
            sources: 书源列表

        Returns:
            (是否通过, 错误信息)
        """
        # 检查 1：数量合理性
        count = len(sources)
        if count < self.MIN_SOURCES:
            return False, f'书源数量过少：{count} < {self.MIN_SOURCES}'
        if count > self.MAX_SOURCES:
            return False, f'书源数量过多：{count} > {self.MAX_SOURCES}'

        # 检查 2：不能为空数组
        if count == 0:
            return False, '书源数组为空'

        # 检查 3：格式正确性（抽样检查）
        sample_size = min(10, count)
        samples = random.sample(sources, sample_size)

        for i, source in enumerate(samples):
            if not isinstance(source, dict):
                return False, f'书源格式错误：第 {i} 个不是字典'

            # 检查必需字段
            for field in self.REQUIRED_FIELDS:
                if field not in source:
                    return False, f'书源缺少必需字段：{field}'

            # 检查 URL 格式
            url = source.get('bookSourceUrl', '')
            if not url or not isinstance(url, str):
                return False, f'书源 URL 无效：{url}'

        # 检查 4：评分合理性（如果有评分字段）
        scored_sources = [s for s in sources if 'score' in s]
        if scored_sources:
            avg_score = sum(s.get('score', 0) for s in scored_sources) / len(scored_sources)
            if avg_score < self.MIN_AVG_SCORE:
                return False, f'平均评分过低：{avg_score:.1f} < {self.MIN_AVG_SCORE}'

        return True, 'OK'

    def create_backup(self) -> Optional[Path]:
        """
        创建版本化备份

        Returns:
            备份文件路径，如果主文件不存在则返回 None
        """
        if not self.main_file.exists():
            print('主文件不存在，跳过备份')
            return None

        # 生成带时间戳的备份文件名
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_file = self.backup_dir / f'full_{timestamp}.json'

        # 复制文件
        shutil.copy2(self.main_file, backup_file)
        print(f'✓ 创建备份：{backup_file.name}')

        return backup_file

    def cleanup_old_backups(self):
        """清理旧备份（保留最近10个）"""
        backups = sorted(
            self.backup_dir.glob('full_*.json'),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        if len(backups) > self.MAX_BACKUPS:
            for old_backup in backups[self.MAX_BACKUPS:]:
                old_backup.unlink()
                print(f'✓ 清理旧备份：{old_backup.name}')

    def atomic_write(self, sources: List[Dict]) -> bool:
        """
        原子写入（临时文件 + os.replace）

        Args:
            sources: 书源列表

        Returns:
            是否成功
        """
        # 写入临时文件
        temp_file = self.main_dir / 'full.tmp'

        try:
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(sources, f, ensure_ascii=False, indent=2)

            # 原子替换
            os.replace(temp_file, self.main_file)
            print(f'✓ 原子更新成功：{len(sources)} 个书源')
            return True

        except Exception as e:
            print(f'✗ 原子写入失败：{e}')
            # 清理临时文件
            if temp_file.exists():
                temp_file.unlink()
            return False

    def rollback(self, backup_file: Optional[Path] = None, force: bool = False) -> bool:
        """
        回滚到指定备份

        Args:
            backup_file: 备份文件路径，如果为 None 则回滚到最新备份
            force: 是否强制回滚（跳过验证）

        Returns:
            是否成功
        """
        if backup_file is None:
            # 查找最新备份
            backups = sorted(
                self.backup_dir.glob('full_*.json'),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )

            if not backups:
                print('✗ 没有可用的备份文件')
                return False

            backup_file = backups[0]

        if not backup_file.exists():
            print(f'✗ 备份文件不存在：{backup_file}')
            return False

        try:
            # 读取备份
            with open(backup_file, 'r', encoding='utf-8') as f:
                sources = json.load(f)

            # 验证备份数据（除非强制回滚）
            if not force:
                valid, error = self.validate_sources(sources)
                if not valid:
                    print(f'✗ 备份数据验证失败：{error}')
                    print('提示：使用 --force 强制回滚')
                    return False
            else:
                print('⚠ 强制回滚，跳过验证')

            # 原子写入
            if self.atomic_write(sources):
                print(f'✓ 回滚成功：{backup_file.name}')
                return True
            else:
                return False

        except Exception as e:
            print(f'✗ 回滚失败：{e}')
            return False

    def safe_update(self, new_sources: List[Dict], skip_validation: bool = False) -> bool:
        """
        安全更新主书源文件

        Args:
            new_sources: 新的书源列表
            skip_validation: 是否跳过验证（仅用于测试）

        Returns:
            是否成功
        """
        print(f'\n=== 安全更新开始 ===')
        print(f'新书源数量：{len(new_sources)}')

        # 步骤 1：验证数据
        if not skip_validation:
            valid, error = self.validate_sources(new_sources)
            if not valid:
                print(f'✗ 数据验证失败：{error}')
                raise SafeUpdateError(f'数据验证失败：{error}')
            print('✓ 数据验证通过')
        else:
            print('⚠ 跳过数据验证')

        # 步骤 2：创建备份
        backup_file = self.create_backup()

        # 步骤 3：原子写入
        try:
            if self.atomic_write(new_sources):
                print('✓ 更新成功')

                # 步骤 4：清理旧备份
                self.cleanup_old_backups()

                print('=== 安全更新完成 ===\n')
                return True
            else:
                print('✗ 更新失败')
                return False

        except Exception as e:
            print(f'✗ 更新异常：{e}')

            # 尝试回滚
            if backup_file:
                print('尝试回滚到备份...')
                self.rollback(backup_file)

            raise SafeUpdateError(f'更新失败：{e}')

    def get_current_sources(self) -> List[Dict]:
        """
        获取当前主书源

        Returns:
            书源列表
        """
        if not self.main_file.exists():
            return []

        with open(self.main_file, 'r', encoding='utf-8') as f:
            return json.load(f)

    def list_backups(self) -> List[Tuple[Path, datetime, int]]:
        """
        列出所有备份

        Returns:
            [(备份文件, 创建时间, 书源数量)]
        """
        backups = []

        for backup_file in sorted(
            self.backup_dir.glob('full_*.json'),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        ):
            try:
                with open(backup_file, 'r', encoding='utf-8') as f:
                    sources = json.load(f)

                mtime = datetime.fromtimestamp(backup_file.stat().st_mtime)
                backups.append((backup_file, mtime, len(sources)))

            except Exception as e:
                print(f'✗ 读取备份失败：{backup_file.name} - {e}')

        return backups


def main():
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(description='安全更新器 - 数据安全保障')
    parser.add_argument('--base-dir', type=Path, help='基础目录')
    parser.add_argument('--list-backups', action='store_true', help='列出所有备份')
    parser.add_argument('--rollback', type=str, help='回滚到指定备份（文件名或 latest）')
    parser.add_argument('--force', action='store_true', help='强制回滚（跳过验证）')
    parser.add_argument('--validate', type=Path, help='验证指定文件的书源数据')

    args = parser.parse_args()

    updater = SafeUpdater(base_dir=args.base_dir)

    if args.list_backups:
        print('\n=== 备份列表 ===')
        backups = updater.list_backups()

        if not backups:
            print('没有备份文件')
        else:
            for backup_file, mtime, count in backups:
                print(f'{backup_file.name:30s} {mtime.strftime("%Y-%m-%d %H:%M:%S")} {count:5d} 个书源')

        print(f'\n总计：{len(backups)} 个备份\n')

    elif args.rollback:
        if args.rollback == 'latest':
            print('回滚到最新备份...')
            updater.rollback(force=args.force)
        else:
            backup_file = updater.backup_dir / args.rollback
            print(f'回滚到：{args.rollback}')
            updater.rollback(backup_file, force=args.force)

    elif args.validate:
        print(f'验证文件：{args.validate}')

        with open(args.validate, 'r', encoding='utf-8') as f:
            sources = json.load(f)

        valid, error = updater.validate_sources(sources)

        if valid:
            print(f'✓ 验证通过：{len(sources)} 个书源')
        else:
            print(f'✗ 验证失败：{error}')
            exit(1)

    else:
        # 显示当前状态
        current = updater.get_current_sources()
        backups = updater.list_backups()

        print('\n=== 当前状态 ===')
        print(f'主书源：{len(current)} 个')
        print(f'备份数：{len(backups)} 个')

        if backups:
            latest = backups[0]
            print(f'最新备份：{latest[0].name} ({latest[2]} 个书源)')

        print()


if __name__ == '__main__':
    main()
