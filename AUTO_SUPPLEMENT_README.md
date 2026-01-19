# 书源自动补充与维护系统

## 概述

这是一个完整的书源自动补充与维护系统，能够智能维持1000个高质量书源，通过多层次机制确保书源数量和质量的持续稳定提升。

## 系统架构

```
书源自动补充系统
├── 智能恢复系统 (smart_recovery.py)
├── 外部书源收集器 (source_collector.py)
├── 增强评分系统 (enhanced_scoring.py)
├── 自动补充主控制器 (auto_supplement.py)
├── 健康监控系统 (health_monitor.py)
├── 配置文件 (config/)
└── GitHub Actions 自动化 (.github/workflows/)
```

## 核心功能

### 1. 智能恢复系统
- **分类恢复机制**：根据错误类型采用不同策略
- **重试机制**：超时、403、404、503/502错误的专门处理
- **反爬虫处理**：User-Agent轮换、请求头优化
- **URL修复**：根域名检测、重定向跟踪、路径修复

### 2. 外部书源收集器
- **GitHub仓库监控**：自动获取知名仓库的最新书源
- **书源网站爬取**：从分享网站收集书源
- **大文件处理**：智能处理大型书源数据文件
- **格式标准化**：统一书源格式和去重

### 3. 增强评分系统
- **87分评分体系**：
  - 原有评分：57分（基础状态、响应时间、规则完整性、更新时间、权重）
  - 稳定性评分：15分（历史可用率、连续可用天数）
  - 内容质量评分：10分（书籍数量、更新频率、内容完整性）
  - 用户反馈评分：5分（用户评价、使用频率）
- **动态权重调整**：新书源、稳定书源、优质书源、信任书源

### 4. 健康监控系统
- **实时监控**：书源数量、可用率、平均评分、域名多样性
- **异常检测**：自动识别数量下降、可用率降低等异常
- **预警机制**：自动判断是否需要触发补充流程
- **历史追踪**：30天健康数据历史记录

### 5. 自动补充主控制器
- **智能触发**：当书源数量 < 800时自动触发
- **完整流程**：恢复 → 收集 → 净化 → 去18禁 → 评分 → 筛选
- **质量保证**：确保最终保留1000个最优书源

## 安装和配置

### 1. 环境要求
```bash
Python 3.8+
pip install aiohttp aiofiles
```

### 2. 目录结构
```
Novel/
├── scripts/              # 核心脚本
├── config/               # 配置文件
├── sources/legado/       # 书源数据
├── temp/                 # 临时文件
├── reports/              # 报告文件
└── .github/workflows/    # 自动化工作流
```

### 3. 配置文件
- `config/supplement_config.json` - 补充系统配置
- `config/scoring_weights.json` - 评分权重配置
- `config/source_channels.json` - 书源渠道配置

## 使用方法

### 手动执行

#### 1. 健康检查
```bash
cd scripts
python health_monitor.py --report ../reports/health.json
```

#### 2. 智能恢复
```bash
python smart_recovery.py \
  --invalid invalid_sources.json \
  --errors error_report.json \
  --output recovered_sources.json
```

#### 3. 外部收集
```bash
python source_collector.py \
  --output collected_sources.json \
  --large-files ../sources/legado/yiove_new.json
```

#### 4. 增强评分
```bash
python enhanced_scoring.py \
  --input sources.json \
  --output scored_sources.json \
  --history source_history.json
```

#### 5. 自动补充
```bash
python auto_supplement.py --force --target 1000
```

#### 6. 系统测试
```bash
python test_system.py
```

### 自动化执行

系统通过GitHub Actions实现全自动化：

- **每日健康检查**：每天凌晨2点UTC
- **每周完整补充**：每周日凌晨3点UTC
- **按需触发**：当书源数量不足时自动执行

#### 手动触发GitHub Actions
1. 进入仓库的Actions页面
2. 选择"书源自动补充与维护"工作流
3. 点击"Run workflow"
4. 设置参数（可选）：
   - 强制执行补充
   - 目标书源数量

## 监控和报告

### 健康报告示例
```
📊 书源健康报告
==========================================

总体健康状况: 🟢 EXCELLENT

📈 详细指标:
  书源数量: 1,000 / 1,000 🟢
  可用率: 92% / 95% 🟡
  平均评分: 48.5 / 50 🟢
  域名多样性: 520 / 500 🟢

💡 改进建议:
  1. 可用率偏低，建议执行智能恢复流程
```

### 补充统计示例
```
=== 补充统计 ===
初始书源: 750
智能恢复: 120
外部收集: 200
过滤成人内容: 15
过滤低分书源: 55
最终书源: 1000
净增长: +250
```

## 配置说明

### 补充系统配置
```json
{
  "supplement": {
    "target_sources": 1000,        // 目标书源数量
    "min_sources_trigger": 800,    // 触发补充的最小数量
    "emergency_trigger": 700,      // 紧急补充触发数量
    "min_score_threshold": 35,     // 最低评分阈值
    "max_per_domain": 2           // 每域名最大书源数
  }
}
```

### 评分权重配置
```json
{
  "dynamic_weights": {
    "new_source": 0.5,      // 新书源权重
    "stable_source": 1.0,   // 稳定书源权重
    "premium_source": 1.5,  // 优质书源权重
    "trusted_source": 2.0   // 信任书源权重
  }
}
```

## 故障排除

### 常见问题

1. **依赖安装失败**
   ```bash
   pip install --upgrade pip
   pip install aiohttp aiofiles
   ```

2. **权限错误**
   ```bash
   chmod +x scripts/*.py
   ```

3. **网络超时**
   - 调整配置文件中的timeout设置
   - 检查网络连接和防火墙设置

4. **GitHub Actions失败**
   - 检查仓库权限设置
   - 确认GITHUB_TOKEN有足够权限

### 日志和调试

- 健康报告：`reports/health_report.json`
- 验证报告：`reports/validation_report.json`
- 临时文件：`temp/` 目录
- GitHub Actions日志：仓库Actions页面

## 性能优化

### 建议配置
- **并发数量**：根据网络带宽调整（默认10-20）
- **超时时间**：根据网络质量调整（默认10-30秒）
- **采样数量**：大文件处理时限制采样数量

### 监控指标
- 书源数量变化趋势
- 平均评分变化
- 可用率统计
- 域名多样性

## 贡献指南

1. Fork 仓库
2. 创建功能分支
3. 提交更改
4. 创建Pull Request

### 开发环境设置
```bash
git clone <repository>
cd Novel
pip install -r requirements.txt
python scripts/test_system.py
```

## 许可证

本项目采用MIT许可证，详见LICENSE文件。

## 更新日志

### v1.0.0 (2024-01-18)
- ✨ 实现智能恢复系统
- ✨ 实现外部书源收集器
- ✨ 实现增强评分系统（87分）
- ✨ 实现健康监控系统
- ✨ 实现自动补充主控制器
- ✨ 添加GitHub Actions自动化
- ✨ 完整的测试验证系统

## 联系方式

如有问题或建议，请通过GitHub Issues联系。