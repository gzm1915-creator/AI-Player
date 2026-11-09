# Among us 虚拟玩家

**作者：JHcys｜版本：0.1.35｜游戏组件：AU Telemetry Probe 0.6.91**

这是一个能够让你的AI伙伴以玩家身份陪伴游玩的插件。

游戏接口不绑定 N.E.K.O.；N.E.K.O. 只是当前首先适配的 AI 宿主。其他 AI 可通过本地接口另做适配，本发行尚未提供其他宿主的现成面板或通用一键启动器。仅安装游戏 DLL 不会自动启动完整 AI 游玩。

## 功能与适用范围

船员任务导航、原 StudyBot 小游戏技能、样本培养后离开再返回；附近玩家观测、中文位置、路线和目击记录；会议读取、工具发言与投票/弃票；普通内鬼假任务、巡场、机会追踪、击杀、主动破坏及管道撤离。面板可停止自动控制、暂停记录或清理插件历史。

主要适配 Windows x64、Steam IL2CPP、The Skeld,目前技能组只适用于普通船员与普通内鬼。游戏编译参考 Steam build **25134893**；其他版本、地图、特殊角色及模组组合未充分验证。真人多人环境的效果不能由离线测试保证。

## 安装教程

### 一、通用游戏端

1. 准备合法安装的 Among Us，在 Steam“管理 → 浏览本地文件”找到包含 `Among Us.exe` 的游戏目录。
2. 安装 **BepInEx 6 Unity IL2CPP Windows x64**：[官方下载](https://builds.bepinex.dev/projects/bepinex_be)、[官方安装教程](https://docs.bepinex.dev/master/articles/user_guide/installation/unity_il2cpp.html)。启动游戏一次再退出，确认生成 `BepInEx/core/BepInEx.Unity.IL2CPP.dll`。额外 .NET 环境按官方文档安装。
3. 解压发布包，退出游戏，在解压目录打开 PowerShell，运行以下命令并替换游戏目录：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -GameDir "F:\Games\Among Us"
```

脚本校验文件并安装游戏组件与路线，不修改 AI 宿主。已有同名组件或配置会停止并保留；升级时退出游戏后改用 `update-game.ps1`，参数相同，会备份旧 DLL 并保留设置与历史。更换 DLL 必须重启游戏；已有 0.6.91 无需为此次文档发布重复替换。

### 二、选择 AI 宿主

**N.E.K.O.：**

1. 正常配置 [N.E.K.O.](https://github.com/Project-N-E-K-O/N.E.K.O) 的模型，模型须支持工具调用。文字可用，语音为可选。本适配依据 0.9.1-rc4（目录名0.9.1.0）/ SDK 0.1 编写，其他版本需核对兼容性。
2. 在插件管理器导入 `among_us_neko-0.1.35.neko-plugin`，不要导入整个 ZIP。
3. 启动 **Among us 虚拟玩家**，打开“游戏控制”面板，在“Among Us 游戏目录”输入路径并“保存并重新连接”。不需要修改 N.E.K.O. 本体，也无需配置第二份模型密钥。
4. 若版本没有导入按钮，按该版本官方文档将 `plugin/among_us_neko` 放入**用户插件目录**并刷新，不要修改程序安装目录。

**其他 AI：**游戏端安装相同，按 [HOST_INTEGRATION.md](HOST_INTEGRATION.md) 编写宿主适配。后续准备专门做一个面板,填写供应商API key即可游玩.

### 三、小游戏技能（自动做任务时配置）

1. 安装 Windows [Python](https://www.python.org/downloads/windows/) 3.10+，执行 `python -m pip install numpy Pillow`。
2. 单独获取 [StudyBot / bot-among-us](https://github.com/iribarnesy/bot-among-us)，准备含 `src/tasks.py` 的原技能目录。包内不含完整上游源码或游戏提取素材，上游版本可能与此前验证版本不同。
3. 面板“原 StudyBot 技能包”填写 `python.exe` 和技能目录绝对路径，保存并检查加载。导入成功不代表所有任务已实机通过。
4. 氧气修复另需 `python -m pip install opencv-python`，以及含英文数据的 [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki)，放入 PATH 或设置 `TESSERACT_CMD`。包内兼容层不提供完整 OpenCV/OCR。

操作小游戏时保持游戏前台并移开遮挡游戏的桌宠浮窗。不要同时运行旧会议桥、旧自动游玩脚本或其他输入控制器。

## 开始使用

先进入 The Skeld 练习局，确认面板连接、坐标和目击记录随实际移动更新，再点“开始自动游玩”。“停止”可终止自动控制并人工接管。

可对 AI 说“读取当前位置与路线”“查看最近目击记录”“把这句文字发到会议”“根据证据选择投票或者弃票”。会议发言需实际调用 `say` / `among_us_say`；普通语音或文字不会自动转发。`submitted` 只代表请求提交，不代表动作完成。

位置与玩家变化静默更新上下文，不强制重复播报坐标。模型思考、回应和工具调用受宿主及网络延迟影响。清理插件记录不会清理 AI 宿主自身已保存的聊天记忆。

## 可能出现的 Bug 与解决方案

| 现象 | 处理方法 |
| --- | --- |
| 未连接或坐标不更新 | 检查目录、游戏进程、`BepInEx/LogOutput.log` 和组件目录的 `latest.json` 是否更新；换 DLL 后重启。 |
| 无输入栏、无面板、导入失败 | 导入 `.neko-plugin` 后启动插件并打开“游戏控制”；刷新列表，检查宿主 SDK 兼容性。 |
| AI 答应却没动作或虚报完成 | 要求调用动作工具并看面板回执；检查角色、开关、距离、冷却与会议状态。提交不等于完成。 |
| 小游戏不动、点击偏移、提前关窗 | 保持游戏前台、移开浮窗，检查 Python、技能目录和依赖，避免鼠标争用。失败可能触发关闭重试，以真实进度为准。 |
| 撞墙、原地抖动、任务后导航失败 | 停止并人工移到开阔处再启动；仅用 The Skeld，核对路线和组件版本；保留房间、目标和错误供反馈。 |
| 没有新目击 | 检查遥测与检测诊断，靠近玩家复测。可见、近期目击和预测不同，隔墙或超范围可能排除；空列表不代表全图无人。 |
| 记忆混乱或复读坐标 | 调用“读取当前位置与路线”获取最新采样；暂停或清理插件记录，另清宿主旧记忆，检查旧脚本重复推送。 |
| 会议不能发言/投票 | 检查会议控制、讨论门限、是否已投票及房间聊天设置；过期会议会拒绝。只发正文，不附用户命令。 |
| 破坏/管道不可用 | 检查普通内鬼角色、运行许可、冷却、入口距离与网络连通；其他角色或地图不保证支持。 |
| 破坏修复不能自动导航 | 专用控制台路线仍待补全；手动靠近打开面板测试。反应堆双人、氧气两处仍遵守游戏规则。 |
| 巡场重复、偶发停止或策略不自然 | 策略仍有局限；看面板错误后停止再启动，记录日志反馈，无法保证每种多人局势。 |

反馈请附游戏版本、地图/角色、插件版本、错误和复现步骤，分享日志前移除私人聊天与路径。

## 引用、更新与卸载

引用 StudyBot 小游戏技能链，参考 Among Us DeepBot 普通内鬼策略设计，并使用 BepInEx 与 N.E.K.O. SDK。具体范围与授权核对见 [THIRD_PARTY.md](THIRD_PARTY.md)。本包不含游戏、AI 宿主、个人历史或模型密钥。

历史功能、实机验证边界见 [CHANGELOG.md](CHANGELOG.md)、[SELF_CHECK.md](SELF_CHECK.md)，会议策略见 [MEETING_STRATEGY.md](MEETING_STRATEGY.md)。历史文档保留当时状态，当前安装步骤以本文为准。0.1.35 仅整理发布信息与说明，沿用 0.1.34 的游玩逻辑。

卸载前停止插件并退出游戏，备份记录后通过宿主卸载适配器，移除 `BepInEx/plugins/AUTelemetryProbe` 及独立配置 `BepInEx/config/com.dsh.autelprobe.cfg`。BepInEx 按官方说明卸载，保留其他模组需要的文件。

**本模组由于真人环境测试样本较少，仍然存在部分缺陷，敬请谅解。**
