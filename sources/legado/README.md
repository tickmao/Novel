# Legado 书源数据

## 目录结构

```
sources/legado/
├── full.json                  # 交付①：仅本轮验证通过的源（用户导入）
├── full-extended.json         # 交付②：验证源 + 分层未验证候选，补到 extended_target
├── manifest.json              # 每条源的层级、修订与最近检查时间
├── main/
│   ├── full.json              # full.json 的镜像（必须字节一致）
│   ├── full-extended.json     # full-extended.json 的镜像（必须字节一致）
│   ├── publication.json       # 两个包的 sha256、分层计数、来源修订引用
│   ├── shadow.json            # 影子库存与两档发布闸门状态
│   ├── working.json           # 交付源与合格备用源的内部视图
│   ├── maintenance_report.json
│   ├── metadata.json
│   └── backups/               # 版本化发布备份（保留最近 10 个）
└── pool/
    ├── raw/                   # 分片原始库：修订、出处、审核与验证状态
    │   ├── manifest.json
    │   ├── state.json
    │   └── records/*.json
    └── candidates.json        # 合格备用源，不能当作用户交付文件
```

## 分层交付

- `full.json` 只包含 `eligibility == 'ready'` 的源：内容审核 pass、当前引擎指纹、两本书各两章正文证据、且证据在有效期内。数量不足目标时如实少于 1000，不用未验证源补齐。
- `full-extended.json` 前段与 `full.json` 完全一致，后段按 `verified → grace → stale_verified → unverified → never_checked` 顺序补足。它承载数量，不构成可用性证明。
- 内容审核 `block`/`review`、禁用源、非小说源、`invalid_url`、`webView`/`startBrowser` 与 `content_locked` 永不进入任何交付包。
- 更新时请先备份个人自建源，再删除旧合集导入新版，避免残留远端已移除的源。

## 维护

初始化、回填、日常维护与故障处理见 [维护文档](../../AUTO_SUPPLEMENT_README.md)。本轮决策可单独查看：

```bash
.venv/bin/python -B scripts/maintenance_plan.py --explain
```

原始记录中的旧验证标记不构成当前可用性证明；没有完成搜索、目录、正文验证和内容审核的版本不会进入 `full.json`。
