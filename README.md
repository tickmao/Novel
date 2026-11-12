# Novel

给阅读类 App 用的书源合集，导入即可用。定期维护，失效的源会换掉。

## 导入

| 平台 | 书源数量 |
|------|---------|
| Android / iOS · [阅读 (Legado)](https://gedoor.github.io/) | 319 |
| iOS · [香色闺阁](https://apps.apple.com/app/id1521205149) | 1274 |
| iOS · [爱阅书香](https://apps.apple.com/app/id1137819437) | 待更新 |

### 阅读 (Legado)

打开 App → 我的 → 书源管理 → 右上角菜单 → 网络导入 → 粘贴链接

主推这一版：每一条都通过实际搜索、打开目录、读取正文的校验，并按周期复验。

```
https://cdn.jsdelivr.net/gh/tickmao/Novel@master/sources/legado/full.json
```

需要更多站点时可以加上这一版（条目更多，但含尚未校验的源，不保证每一条都能读）：

```
https://cdn.jsdelivr.net/gh/tickmao/Novel@master/sources/legado/full-extended.json
```

**净化规则**（去广告、去水印）

```
https://cdn.jsdelivr.net/gh/tickmao/Novel@master/rules/legado/purify.json
```

**TTS 配置**（在线语音）

```
https://cdn.jsdelivr.net/gh/tickmao/Novel@master/rules/legado/tts.json
```

### 香色闺阁

打开 App → 站点 → 导入 → 粘贴链接

```
https://cdn.jsdelivr.net/gh/tickmao/Novel@master/sources/xsreader/full.xbs
```

### 爱阅书香

打开 App → 同步 → 粘贴链接（第三方维护）

```
https://github.com/shidahuilang/shuyuan/raw/shuyuan/aiyueshuxiang.ibs
```

## 配套

**字体**：[霞鹜文楷](https://github.com/lxgw/LxgwWenKai)

```
https://cdn.jsdelivr.net/gh/tickmao/Novel@master/fonts/LXGWWenKai.ttf
```

![霞鹜文楷字形演示](https://raw.githubusercontent.com/lxgw/LxgwWenKai/main/documentation/wenkai-1.png)

**主题**

- 阅读：`themes/legado/tickmao.json`
- 爱阅书香：`themes/ifreetime/tickmao.itcs`

## 声明

- 本仓库仅作为书源的聚合与分享，不拥有任何书源的版权
- 所有书源版权归原创作者所有，详见 [CREDITS.md](CREDITS.md)
- 仅供学习交流使用，请勿用于商业用途
- 如有侵权，请 [提交 Issue](https://github.com/tickmao/Novel/issues) 联系删除
- 请支持正版阅读

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=tickmao/Novel&type=Date)](https://star-history.com/#tickmao/Novel&Date)

## Author

Novel by [Tickmao](https://blog.tickmao.com/), Released under the MIT License.

- Blog: [@Tickmao](https://blog.tickmao.com/)
- GitHub: [@Tickmao](https://github.com/tickmao)
- Twitter: [@Tickmao](https://twitter.com/tickmao)
