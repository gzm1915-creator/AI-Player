# 市场检查与发布

作者：JHcys。插件 ID 保持 `among_us_neko`，本次版本为 0.1.36。

源码根目录含 plugin.toml、__init__.py、config.example.toml、pyproject.toml、tests 与标准 GitHub 工作流。游戏 DLL 和旧 .neko-plugin 不提交到市场源码树；游戏端独立下载，历史原包仍可从 README 中固定的原提交获取。

本次仅整理发布，不新增游玩逻辑。行为测试随源码移到根目录；原游戏安装脚本测试保留在原始游戏发行中，本市场包只测 AI 适配层。路线 JSON 是测试数据，不会随市场安装包部署。

在 N.E.K.O. 源码开发环境运行（插件目录参数可用本仓库的绝对路径）：

```bash
uv run neko-plugin check /path/to/AI-Player --strict
uv run neko-plugin precheck /path/to/AI-Player
```

推送后等待 GitHub Actions 的 Verify N.E.K.O Plugin 成功。市场投稿：仓库地址 `https://github.com/gzm1915-creator/AI-Player`，插件目录留空，读取仓库信息后应得到 `among_us_neko`。仓库名推荐 n.e.k.o_plugin_<ID>，当前官方 CLI 对其他公开仓库名只给建议，不必为此更改现有地址。

先完成首次审核，再从 N.E.K.O. 源码根目录运行 `uv run neko-plugin publish /path/to/AI-Player`。该命令负责推送标签、等待标准 Release、通知市场；每次发布必须使用未曾发布过的新版本。不要在审核前把工作流已通过说成市场已上架。

参考：https://project-neko.online/zh-CN/plugins/cli
