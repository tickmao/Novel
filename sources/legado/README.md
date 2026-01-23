# Legado 书源目录结构

## 三层架构

```
sources/legado/
├── pool/                          # 书源池（原始层）
│   ├── raw.json                  # 原始书源（16,724个）
│   ├── candidates.json           # 候选书源（2,000-3,000个，待生成）
│   └── invalid.json              # 无效书源（存档，待生成）
├── main/                          # 主书源库（主库层）
│   ├── full.json                 # 当前书源（414个 → 目标1,000个）
│   ├── metadata.json             # 元数据（评分、历史，待生成）
│   └── backups/                  # 版本化备份（保留最近10个）
│       └── full_20260123_131410.json
└── temp/                          # 临时文件
    ├── validation_queue.json     # 验证队列（待生成）
    ├── processing.json           # 处理中的书源（待生成）
    └── checkpoints/              # 检查点（待生成）
```

## 数据流

```
pool/raw.json (16,724)
    ↓ 快速过滤
pool/candidates.json (2,000-3,000)
    ↓ 分批验证 + 评分
main/full.json (1,000)
```

## 旧文件（保留用于兼容）

- `full.json` - 旧的主书源文件（已迁移到 main/full.json）
- `yiove_new.json` - 旧的原始书源文件（已迁移到 pool/raw.json）
- `full.backup.json` - 旧的备份文件（1,500个书源）
- `raw.json` - 旧的原始文件（4.7MB）
- `invalid.json` - 旧的无效书源文件（3.6MB）

## 迁移记录

- **迁移时间**: 2026-01-23 13:14:10
- **原始书源**: 16,724 个 → pool/raw.json
- **主书源**: 414 个 → main/full.json
- **初始备份**: main/backups/full_20260123_131410.json

## 下一步

1. 开发 safe_updater.py（数据安全保障）
2. 开发 batch_validator.py（分批验证）
3. 开发 source_selector.py（智能选择）
4. 开发 source_pool_manager.py（池管理）
5. 开发 initialize_sources.py（初始化整合）
6. 开发 daily_maintenance.py（日常维护）
