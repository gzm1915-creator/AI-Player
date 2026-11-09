# 引用、参考与发行边界

本发行：**Among us 虚拟玩家 0.1.35**，作者：**JHcys**。署名指本插件的整合与开发，不替代参考项目原作者的权利。

| 项目 | 来源与使用范围 |
| --- | --- |
| StudyBot / bot-among-us | https://github.com/iribarnesy/bot-among-us ；外部小游戏技能链、TaskManager 和修复任务方法。用户另行安装，不分发完整上游源码与提取素材。 |
| Among Us DeepBot | https://github.com/shimiaoshui/among-us-deepbot ；参考普通内鬼掩护、巡场、机会、目击风险与撤离设计，未复制 DeepBot/TOR 源码或二进制，不宣称兼容。 |
| N.E.K.O. | https://github.com/Project-N-E-K-O/N.E.K.O ；当前宿主 SDK、工具、UI 与消息接口适配，不分发或修改本体，不是通用游戏接口的强制依赖。 |
| BepInEx | https://builds.bepinex.dev/projects/bepinex_be ；游戏端 IL2CPP 加载框架，用户自行安装，不随包分发。 |

用户提供的 StudyBot 压缩包顶层未发现明确 LICENSE 文件，本说明不擅自认定其许可证或授予上游再分发权；包内保留此前项目的适配器、输入兼容层与反应堆技能，获取上游代码/素材需核对其当前授权。参考的 DeepBot 压缩包含 MIT License，署名 `Copyright (c) 2026 shimiaoshui`；本包对其为设计参考。

Python、NumPy、Pillow、OpenCV、Tesseract 等依赖由用户另行安装，各自遵循原许可。此说明不为所有文件额外授予统一开源许可。

## 原有发行边界记录

# Distribution boundaries

This package contains the Among Us to N.E.K.O. adapter source, AU Telemetry
Probe 0.6.91 binary/source with portable paths and native action controls, route geometry,
tests and installation scripts.
It does not redistribute Among Us, game-generated interop assemblies,
BepInEx, Harmony, Il2CppInterop, Unity, N.E.K.O., Python, .NET, speech models,
or API credentials. Install those dependencies through their official channels.

The StudyBot task adapter, input compatibility modules and authored reactor
skill are preserved from the user's prior local project. Task solving
algorithms are retained; paths and process ownership were adapted for the
N.E.K.O. plugin. The upstream StudyBot repository
(https://github.com/iribarnesy/bot-among-us) remains an external dependency;
its source and extracted game assets are not included in this distribution.

The four emergency repair entries call the externally installed StudyBot
methods; those algorithms are not copied into this package. Oxygen repair
additionally uses the user's OpenCV and Tesseract OCR installations.

The basic ordinary-Impostor controller was authored for this adapter after
reviewing the user-provided among-us-deepbot archive: cover activity,
visible opportunity selection, witness risk and post-kill escape informed
the design. DeepBot/TOR source, binaries, virtual-player and model clients
are not included or required. This is an independent local-role integration,
not a claim of DeepBot/TOR compatibility.

N.E.K.O. plugin interfaces were examined in the user-provided 0.9.1-rc4
distribution. Existing N.E.K.O. plugin implementations and assets are not
copied into this package.

Among Us belongs to Innersloth. This adapter is not affiliated with or
endorsed by Innersloth or the N.E.K.O. maintainers. No plugin-marketplace
publication or vendor compatibility certification has been performed.
