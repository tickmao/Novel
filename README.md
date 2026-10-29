# Novel

小说书源维护仓库，为阅读类 App 提供一站式书源服务。

## 支持平台

| 平台 | App | 书源数量 |
|------|-----|---------|
| Android / iOS | [阅读 (Legado)](https://gedoor.github.io/) | 1000 |
| iOS | [香色闺阁](https://apps.apple.com/app/id1521205149) | 1274 |
| iOS | [爱阅书香](https://apps.apple.com/app/id1137819437) | 待更新 |

<!-- publication-status -->
当前发布：严格验证 **140** 条 / 扩展 **1000** 条（未验证 772 条，验证占比 14%）；状态：`degraded`；更新时间：2026-10-29T18:03:29.576341+00:00。
<!-- /publication-status -->

## 书源导入

### 阅读 (Legado)

打开 App -> 我的 -> 书源管理 -> 右上角菜单 -> 网络导入 -> 粘贴链接

**严格验证书源 (140 个)**
```
https://cdn.jsdelivr.net/gh/tickmao/Novel@master/sources/legado/full.json
```

每一条都通过固定版本 Legado 引擎的完整阅读验证（搜索、书籍、目录、两本书各两章正文）和内容审核，且证据在有效期内。

**扩展书源 (1000 个，其中未验证 772 个)**
```
https://cdn.jsdelivr.net/gh/tickmao/Novel@master/sources/legado/full-extended.json
```

扩展包前段与严格验证文件完全一致，后段补充尚未完成完整阅读验证的候选源。未验证源可能返回空章节或已失效，逐条状态见 `sources/legado/manifest.json`。**建议优先使用严格验证文件。**

**净化规则**
```
https://cdn.jsdelivr.net/gh/tickmao/Novel@master/rules/legado/purify.json
```

**TTS 配置**
```
https://cdn.jsdelivr.net/gh/tickmao/Novel@master/rules/legado/tts.json
```

### 香色闺阁

打开 App -> 站点 -> 导入 -> 粘贴链接

**全量书源 (1274 个站点)**
```
https://cdn.jsdelivr.net/gh/tickmao/Novel@master/sources/xsreader/full.xbs
```

### 爱阅书香

打开 App -> 同步 -> 粘贴链接

**书源地址**（第三方）
```
https://github.com/shidahuilang/shuyuan/raw/shuyuan/aiyueshuxiang.ibs
```

## 其他资源

### 字体

推荐字体：[霞鹜文楷](https://github.com/lxgw/LxgwWenKai)

```
https://cdn.jsdelivr.net/gh/tickmao/Novel@master/fonts/LXGWWenKai.ttf
```

### 主题

- Legado 主题：`themes/legado/tickmao.json`
- 爱阅书香主题：`themes/ifreetime/tickmao.itcs`

## 目录结构

```
Novel/
├── sources/          # 书源文件
│   ├── legado/       # 阅读书源
│   ├── xsreader/     # 香色闺阁书源
│   └── ifreetime/    # 爱阅书香书源
├── rules/            # 规则配置
│   └── legado/       # 净化规则、TTS、字典等
├── themes/           # 主题配置
├── fonts/            # 字体文件
├── scripts/          # 工具脚本
└── docs/             # 网页文件
```

## 声明

- 本仓库仅作为书源的聚合与分享，不拥有任何书源的版权
- 所有书源版权归原创作者所有，详见 [CREDITS.md](CREDITS.md)
- 仅供学习交流使用，请勿用于商业用途
- 如有侵权，请 [提交 Issue](https://github.com/tickmao/Novel/issues) 联系删除
- 请支持正版阅读

## 更新日志

查看 [CHANGELOG.md](CHANGELOG.md)

## Star History

[![Star History Chart](https://star-history.dera.page/svg?repos=tickmao/Novel&type=Date)](https://star-history.dera.page/#tickmao/Novel&Date)

## Author

Novel by [Tickmao](https://blog.tickmao.com/), Released under the MIT License.

- Blog: [@Tickmao](https://blog.tickmao.com/)
- GitHub: [@Tickmao](https://github.com/tickmao)
- Twitter: [@Tickmao](https://twitter.com/tickmao)
