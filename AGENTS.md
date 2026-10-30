# Novel — 书源维护自动化

Python 脚本 + GitHub Actions 生产书源数据，`docs/` 是发布站点。运行时用 `.venv`（Python 3.14），依赖见 `scripts/requirements.txt`。

## 目录三层

| 层 | 路径 | 说明 |
|---|---|---|
| pool | `sources/legado/pool/` | 原始池：`raw/records/*.json` 分片、`raw.json`、`invalid.json`、`candidates.json` |
| main | `sources/legado/main/` | 生产层：`full.json`、`publication.json`、`manifest.json`、`shadow.json`、`maintenance_report.json`、`backups/` |
| 兼容导出 | `sources/legado/full.json`、`full-extended.json` | 对外分发包，由 `update_sources.py` 同步 |

路径解析统一走 `scripts/legado_paths.py`，新脚本不要硬编码路径。

## 常用命令

```bash
.venv/bin/python -B -m unittest discover -s tests -v                  # 回归测试，CI 同款
.venv/bin/python -B scripts/check_publication.py --allow-bootstrap    # 发布门禁自检
git status --short                                                     # 动手前先看工作区
```

不要跑全量网络验证（数千站点、常以小时计），除非明确要求。

## 数据纪律

- **禁止整文件读大 JSON**：`sources/legado/pool/raw/`（约 230MB）、`pool/raw.json`（约 100MB）、`pool/invalid.json`（约 45MB）。统计需求用 `python`/`jq` 聚合，只把结论带进上下文。
- 改数据先备份；只有验证通过才允许合并进 `main/full.json`，`scripts/safe_updater.py` 负责数量与分层校验。
- 数量、分层、评分以 `sources/legado/manifest.json` 与 `main/publication.json` 为准；文档里的历史数字会过期。
- 常量权威位置：`scripts/source_selector.py`（`target_count=1000`、`max_per_domain=2`）、`scripts/integrate.py`（`MIN_SCORE=25`）。
- 证据身份只看读取语义：`scripts/runtime_config.py` 的 `SEMANTIC_FILES`（`NovelProbe.kt` + 兼容补丁）、`ENGINE_COMMIT`、`PROBE_PROTOCOL`。改 `runtime_validator.py`、`probe_runtime.py`、`Dockerfile` **不得**提升 `VALIDATOR_VERSION` 或 `PROBE_PROTOCOL`；提升会作废全库证据，需要数天重验。只有准入阈值（书数/章节数/字数）、探针协议或 JVM 基础镜像变化时才提升。
- 公开发布不会因证据身份变化而收缩：旧身份的条目按 `legacy` 层保留并优先重验，只有「当期身份明确拒绝」的条目才会被移除（`source_inventory.currently_refused` + `safe_updater` 的收缩保护）。

## CI 已知坑（历史事故，勿重犯）

- `github.event.schedule` 只在 `schedule` 触发时存在；`workflow_dispatch` 下为空，所以定时分支的条件必须写成 `if [ "$EVENT_SCHEDULE" = '<cron>' ]` 并给出默认值。
- 工作流调用脚本前核对 argparse 参数名（曾因 `--output` 与 `--output-valid` 不一致导致退出码 2）。
- `.gitignore` 排除的路径在 CI 里不存在。文件末尾的 pi 快照块只放静态大文件，**不要**扩到 `pool/raw/`、`main/backups/`，否则 CI 的 `git add` 会静默漏提交。
- 单步 `$GITHUB_STEP_SUMMARY` 上限 1 MiB。曾把 918 KB 的 `maintenance_report.json` 用 `indent=2` 写进摘要（约 1.24 MiB），导致 `Add run summary` 每次失败；摘要一律走 `check_publication.py --summary`，它有 64 KiB 硬上限。
- `Persist completed work` 是 `if: always()`，红灯的 run 同样会提交，所以「chore: maintain source inventory and publication」的提交历史**不能**当作成功证据。
- 提交清单是显式文件列表，不要改回 `git add <目录>`：目录级 `git add` 会把 `main/backups/`（7 MB/次）重新吃进历史。

## 代码风格

Python：4 空格、单引号、`snake_case` / `PascalCase` / `UPPER_SNAKE_CASE`、`from __future__ import annotations` + typing 类型提示、asyncio/aiohttp。模块头 `#!/usr/bin/env python3` 加中文 docstring，与现有文件保持一致；注释与 docstring 用中文，标识符用 English。

新增脚本要补 `tests/` 下的 unittest；接入 workflow 时同步核对参数与产物路径。

## 文档

`.claude/CLAUDE.md`、`.claude/memory.md` 是 Claude Code 侧资料（未纳入 git），部分内容已过期。与本文件或代码冲突时，以代码为准。
