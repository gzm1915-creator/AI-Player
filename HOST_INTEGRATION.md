# 其他 AI 宿主接入

游戏组件通过本地 JSON 文件交换状态与命令，不依赖 N.E.K.O. 进程或指定模型服务。本包目前只附 N.E.K.O. 宿主适配，没有通用 HTTP/MCP 服务或其他宿主一键启动器；仅启动游戏 DLL 不会自动启动完整 AI 游玩。

推荐复用 `plugin/among_us_neko/bridge.py` 的 Bridge、Monitor 和策略模块，另写宿主工具注册、消息推送、面板与生命周期层。`__init__.py` 是 N.E.K.O. 专用注册层；其他宿主应避免直接执行它。核心模块使用标准库，小游戏另需 README 所列依赖。可参考 `tests/test_bridge.py` 的独立模块加载方式。

## 独立读取示例

在解压目录运行，下例只读取，不启用控制，也不导入 N.E.K.O. SDK：

```python
import importlib
import sys
import types
from pathlib import Path

source = Path("plugin/among_us_neko").resolve()
package = types.ModuleType("among_us_core")
package.__path__ = [str(source)]
sys.modules["among_us_core"] = package
core = importlib.import_module("among_us_core.bridge")
bridge = core.Bridge("F:/Games/Among Us", history_dir="./among-us-history")
print(bridge.map_context())
```

| 用途 | 核心入口 |
| --- | --- |
| 状态、中文位置、路线与目标 | `read()`、`map_context()`、`anchors()` |
| 会议正文 | `say(text)`，可绑定当前会议，或显式传最新会话/会议标识 |
| 投票或弃票 | `vote(player_id, session, meeting)`，参数约定见现有宿主注册层 |
| 导航、停止 | `navigate(anchor_id)`、`halt()` |
| 破坏、管道 | `sabotage(target)`、`vent(...)`、`manual_vent(text)` |
| 后台观测与策略调度 | `Monitor(bridge, push, settings)`，宿主需显式管理策略开关和停止 |

`push` 回调和游玩开关应参照现有 Monitor、宿主注册层实现。坐标变化通常只更新上下文，不强制语音输出；构造对象不等于启用自动游玩。

## 文件协议

默认目录：游戏的 `BepInEx/plugins/AUTelemetryProbe/`。

- `latest.json`：最新遥测。
- `internal-navigation-command.json` / `internal-navigation-status.json`：动作命令及状态。
- `meeting-control-status.json`：会议、聊天与投票状态。
- `runtime-control.json` / `runtime-control-status.json`：许可开关及确认。
- `sabotage-command.json`、`vent-command.json`：独立动作通道。

路径可由 `BepInEx/config/com.dsh.autelprobe.cfg` 覆盖；命令字段与校验以 `bridge.py` 和 `game-source` 为准，本版未发布独立的稳定网络协议规范。

接入时保留会话、对局、会议标识与有效期校验，使用原子写入，遥测过期停用旧坐标，避免多个控制器同时写命令。`submitted` 只是提交，必须核对匹配的游戏原生结果才能确认完成。历史或预测不能冒充当前目击。模型、语音、人格、会话记忆与宿主面板由接入者提供，其他宿主尚未完成联调验证。
