#!/usr/bin/env python3
"""
测试 safe_updater.py 的功能
"""

import json
import sys
from pathlib import Path

# 添加 scripts 目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from safe_updater import SafeUpdater, SafeUpdateError


def test_safe_updater():
    """测试安全更新器"""
    print('=== 测试 SafeUpdater ===\n')

    # 初始化
    base_dir = Path(__file__).parent.parent / 'sources' / 'legado'
    updater = SafeUpdater(base_dir=base_dir)

    # 测试 1：获取当前书源
    print('测试 1：获取当前书源')
    current = updater.get_current_sources()
    print(f'✓ 当前书源数量：{len(current)}')

    # 测试 2：验证数据（应该失败，因为数量不足）
    print('\n测试 2：验证当前数据（预期失败）')
    valid, error = updater.validate_sources(current)
    if not valid:
        print(f'✓ 验证失败（预期）：{error}')
    else:
        print('✗ 验证通过（不符合预期）')

    # 测试 3：验证格式（使用小样本）
    print('\n测试 3：验证数据格式')
    sample = current[:10]
    for i, source in enumerate(sample):
        required_fields = ['bookSourceName', 'bookSourceUrl', 'bookSourceType']
        missing = [f for f in required_fields if f not in source]
        if missing:
            print(f'✗ 书源 {i} 缺少字段：{missing}')
        else:
            print(f'✓ 书源 {i} 格式正确')

    # 测试 4：列出备份
    print('\n测试 4：列出备份')
    backups = updater.list_backups()
    print(f'✓ 备份数量：{len(backups)}')
    for backup_file, mtime, count in backups:
        print(f'  - {backup_file.name}: {count} 个书源, {mtime.strftime("%Y-%m-%d %H:%M:%S")}')

    # 测试 5：模拟安全更新（使用当前数据，跳过验证）
    print('\n测试 5：模拟安全更新（跳过验证）')
    try:
        # 创建一个测试数据（复制当前数据）
        test_sources = current.copy()

        # 跳过验证进行更新
        success = updater.safe_update(test_sources, skip_validation=True)

        if success:
            print('✓ 安全更新成功')

            # 验证备份数量增加
            new_backups = updater.list_backups()
            print(f'✓ 新备份数量：{len(new_backups)}')
        else:
            print('✗ 安全更新失败')

    except SafeUpdateError as e:
        print(f'✗ 安全更新异常：{e}')

    # 测试 6：测试回滚
    print('\n测试 6：测试回滚到最新备份')
    if updater.rollback():
        print('✓ 回滚成功')
    else:
        print('✗ 回滚失败')

    # 测试 7：验证空数组（应该失败）
    print('\n测试 7：验证空数组（预期失败）')
    valid, error = updater.validate_sources([])
    if not valid:
        print(f'✓ 验证失败（预期）：{error}')
    else:
        print('✗ 验证通过（不符合预期）')

    # 测试 8：验证格式错误（应该失败）
    print('\n测试 8：验证格式错误（预期失败）')
    invalid_sources = [
        {'bookSourceName': '测试', 'bookSourceUrl': 'http://test.com'},  # 缺少 bookSourceType
    ] * 900  # 数量足够
    valid, error = updater.validate_sources(invalid_sources)
    if not valid:
        print(f'✓ 验证失败（预期）：{error}')
    else:
        print('✗ 验证通过（不符合预期）')

    print('\n=== 测试完成 ===')


if __name__ == '__main__':
    test_safe_updater()
