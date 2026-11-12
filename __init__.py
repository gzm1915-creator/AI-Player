"""Native N.E.K.O. extension. Uses the host's configured model and voice."""

import hashlib
import secrets
import time
from pathlib import Path

from plugin.sdk.plugin import NekoPluginBase, Ok, lifecycle, llm_tool, neko_plugin, plugin_entry, ui

from .bridge import Bridge, Monitor, atomic, load
from .spatial import model_state

EMPTY = {"type": "object", "properties": {}}
MEETING = {
    "session_id": {"type": "string", "description": "最新状态中的sessionId"},
    "meeting_id": {"type": "integer", "description": "最新状态中的meetingId"},
}
HISTORY_OPERATIONS = ("history_pause", "history_resume", "history_clear_current", "history_clear_all")


@neko_plugin
class AmongUsNekoPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.bridge = None
        self.monitor = None
        self.settings = {}
        self.ui_token = secrets.token_urlsafe(32)

    def _settings_path(self):
        return Path(self.data_path()) / "connection.json"

    def _connect(self, game_dir):
        if self.monitor:
            if self.monitor.pilot.enabled:
                self.bridge.halt()
            self.monitor.close()
            if self.monitor.thread and self.monitor.thread.is_alive():
                raise ValueError("旧监听线程仍在退出，请重载插件以恢复，避免重复监听。")
        key = hashlib.sha256(str(Path(game_dir).resolve()).casefold().encode()).hexdigest()[:16] if game_dir else ""
        self.bridge = (
            Bridge(
                game_dir,
                history_dir=Path(self.data_path()) / "history" / key,
                history_enabled=self.settings.get("history_enabled", True),
            )
            if game_dir
            else None
        )
        self.monitor = Monitor(self.bridge, self.push_message, self.settings) if self.bridge else None
        if self.monitor:
            self.monitor.auto_reply = self.settings.get("auto_reply", True)
            self.monitor.start()

    @lifecycle(id="startup")
    async def startup(self, **_):
        config = await self.config.dump(timeout=5.0)
        section = config.get("among_us", {}) if isinstance(config, dict) else {}
        self.settings = load(self._settings_path())
        game_dir = self.settings.get("game_dir") or section.get("game_dir", "")
        self.settings["game_dir"] = game_dir
        if not game_dir:
            return Ok({"summary": "请在面板填写游戏目录并保存连接；无需重启。"})
        self._connect(game_dir)
        return Ok({"summary": "Among Us观测已启动；正在等待游戏遥测。"})

    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        if self.monitor:
            if self.monitor.pilot.enabled:
                self.bridge.halt()
            self.monitor.close()
        return Ok({"summary": "Among Us观测已停止。"})

    def _call(self, method, *args):
        if not self.bridge:
            return {"error": "尚未设置game_dir，配置后请重启插件。"}
        try:
            return getattr(self.bridge, method)(*args)
        except (OSError, ValueError, TypeError) as exc:
            return {"error": str(exc)}

    @plugin_entry(id="status", name="Among Us连接状态", description="查看游戏连接及可见状态。", input_schema=EMPTY)
    async def status(self, **_):
        return Ok(
            {**model_state(self._call("read")), "observer": self.monitor.health() if self.monitor else {"alive": False}}
        )

    @plugin_entry(
        id="map",
        name="读取当前位置与路线",
        description="实时读取Among Us游戏遥测的当前连接、坐标、中文位置、附近玩家及已走路线。以本次summary为准，不能沿用聊天记忆中的断线结论。无需打开本地文件。",
        input_schema=EMPTY,
    )
    async def map(self, **_):
        return Ok(self._call("map_context"))

    @plugin_entry(
        id="history",
        name="读取行动记录与目击经过",
        description="查询本局按时间排序的路线、任务阶段变化和玩家目击/离开视野。包含对局identity及任务批次taskBatch；用于详细复述做过什么、遇见谁，历史不能当作当前位置。",
        input_schema={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["all", "route", "tasks", "encounters"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
        },
    )
    async def history(self, kind="all", limit=30, **_):
        result = self._call("history", kind, limit)
        result.pop("logDirectory", None)
        return Ok(result)

    @plugin_entry(
        id="meeting_guide",
        name="读取会议策略与玩家术语",
        description="只读会议发言策略，区分船员和内鬼、本人证据与他人声称；term可选，如金水、银水、狼坑、11、秒报、跳管。完整策略不等于当前游戏事实，不会发言或投票。",
        input_schema={"type": "object", "properties": {"term": {"type": "string", "maxLength": 40}}},
    )
    async def meeting_guide(self, term=None, **_):
        return Ok(await self.among_us_meeting_guide(term))

    @plugin_entry(
        id="say",
        name="发送会议发言",
        description="AI在Among Us会议里说话使用此入口，text填写要发的1至100字。自动读取当前会议，无需ui_token；普通语音不会转成游戏文字。不要调用面板control入口。",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 100}},
            "required": ["text"],
        },
    )
    async def send_meeting(self, text=None, **_):
        return Ok(await self.among_us_say(text=text))

    @plugin_entry(
        id="vote",
        name="选择会议投票或跳过",
        description="在会议可投票且本人未投时必须决定投某玩家或跳过，并实际调用本入口。player_id须来自最新voting.candidates的合法玩家，-1为跳过；证据不足优先跳过。session_id与meeting_id必须取最新状态。无需ui_token，submitted不是投票成功，读取voting.hasVoted确认游戏已记录。",
        input_schema={
            "type": "object",
            "properties": {"player_id": {"type": "integer", "minimum": -1, "maximum": 254}, **MEETING},
            "required": ["player_id", "session_id", "meeting_id"],
        },
    )
    async def vote(self, player_id=None, session_id=None, meeting_id=None, **_):
        return Ok(await self.among_us_vote(player_id, session_id, meeting_id))

    @plugin_entry(
        id="sabotage",
        name="内鬼主动制造破坏",
        description="普通内鬼主动破坏设施。target为lights照明/comms通讯/reactor反应堆/oxygen氧气，默认lights。无需移动到控制台，无需ui_token；冷却、会议、角色限制会返回原因。这是制造破坏，不是船员修复。不要调用面板control入口。",
        input_schema={
            "type": "object",
            "properties": {"target": {"type": "string", "enum": ["lights", "comms", "reactor", "oxygen"]}},
        },
    )
    async def sabotage(self, target="lights", **_):
        return Ok(await self.among_us_sabotage(target=target))

    @plugin_entry(
        id="kill",
        name="内鬼击杀当前可见目标",
        description="普通内鬼通过原生按钮击杀当前选中的可见非同伴目标。player_id可省略自动读取原生目标，冷却、目击者、距离等仍限制；需先开始自动游玩或开启导航执行器。无需ui_token，不使用面板control。",
        input_schema={"type": "object", "properties": {"player_id": {"type": "integer", "minimum": 0, "maximum": 254}}},
    )
    async def kill(self, player_id=None, **_):
        return Ok(await self.among_us_kill(player_id=player_id))

    @plugin_entry(
        id="vent",
        name="管道传送与退出",
        description="普通内鬼沿真实管道网络移动。action=status读取中文入口、出口编号和连接；enter进管并留在内部，move传target_vent_id在管内移动但不出管；approach传entry_vent_id沿已知可通行路线接近入口；travel传target_vent_id，可选entry_vent_id；需先走到入口旁并在面板开启导航执行器及允许管道技能。自动进管、逐段移动、到站出管；exit用于取消并退出。无需ui_token，submitted不算到达。",
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["status", "approach", "enter", "move", "travel", "exit"]},
                "target_vent_id": {"type": "integer", "minimum": 0},
                "entry_vent_id": {"type": "integer", "minimum": 0},
            },
        },
    )
    async def vent(self, action="status", target_vent_id=None, entry_vent_id=None, **_):
        return Ok(await self.among_us_vent(action=action, target_vent_id=target_vent_id, entry_vent_id=entry_vent_id))

    @plugin_entry(
        id="manual_vent",
        name="手动管道命令",
        description="执行明确的手动命令：钻管道 [入口编号]、管道移动 出口编号、出管道、管道传送 出口编号。钻入及管内移动后留在管道中；已提交时暂停自动游玩，防止抢控制。需管道许可和导航执行器，无需面板令牌。",
        input_schema={
            "type": "object",
            "properties": {"command": {"type": "string", "maxLength": 80}},
            "required": ["command"],
        },
    )
    async def manual_vent(self, command="钻管道", **_):
        return Ok(await self.among_us_manual_vent(command))

    @plugin_entry(
        id="escape_policy",
        name="设置击杀后管道撤离",
        description="enabled=true启用击杀确认后优先管道撤离：入口周围无人时自动前往可达管道，到出口连续观察随机2至15秒且无人再出管。无入口、有人或入口路径失败则地面撤离。需开始自动游玩并允许管道技能；设置成功不代表已钻管。",
        input_schema={"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
    )
    async def escape_policy(self, enabled=True, **_):
        return Ok(await self.among_us_escape_policy(enabled))

    @ui.context(id="dashboard", title="Among Us 控制面板")
    async def dashboard(self):
        return {
            "game": self._call("read"),
            "gameDir": self.settings.get("game_dir", ""),
            "anchors": self.bridge.anchors() if self.bridge else [],
            "observer": self.monitor.health() if self.monitor else {"alive": False},
            "uiToken": self.ui_token,
        }

    @ui.action(id="control", label="游戏控制", group="dashboard", refresh_context=True)
    @plugin_entry(
        id="control",
        name="面板专用设置（AI勿调用）",
        description="仅供插件面板持有ui_token时设置连接、历史和开关。AI发言请用say，投票或跳过用vote，内鬼破坏用sabotage，击杀用kill，查询用map/history/status；此入口不供AI游戏动作使用。",
        input_schema={
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [
                        "connect",
                        "pause",
                        "resume",
                        "reply_on",
                        "reply_off",
                        "vote_on",
                        "vote_off",
                        "say",
                        "stop",
                        "pilot_on",
                        "pilot_off",
                        "runtime",
                        "navigate",
                        "start_all",
                        "studybot_config",
                        "history_pause",
                        "history_resume",
                        "history_clear_current",
                        "history_clear_all",
                        "vent_status",
                        "vent_travel",
                        "vent_exit",
                        "manual_vent",
                    ],
                },
                "game_dir": {"type": "string"},
                "studybot_root": {"type": "string"},
                "studybot_python": {"type": "string"},
                "text": {"type": "string", "maxLength": 100},
                "flag": {"type": "string"},
                "enabled": {"type": "boolean"},
                "anchor_id": {"type": "string"},
                "target_vent_id": {"type": "integer", "minimum": 0},
                "entry_vent_id": {"type": "integer", "minimum": 0},
                "ui_token": {"type": "string"},
                "history_record_id": {"type": "string"},
            },
            "required": ["operation", "ui_token"],
        },
    )
    async def control(
        self,
        operation="",
        game_dir="",
        text="",
        ui_token="",
        flag="",
        enabled=False,
        anchor_id="",
        target_vent_id=None,
        entry_vent_id=None,
        **_,
    ):
        if operation == "studybot_config":
            if not isinstance(ui_token, str) or not secrets.compare_digest(ui_token, self.ui_token):
                raise ValueError("请从插件控制面板操作。")
            if not self.monitor:
                raise ValueError("请先连接游戏目录。")
            with self.bridge.lock:
                self.monitor.solver.set_enabled(False)
                self.monitor.pilot.set_enabled(False, source="panel.studybot_config")
                self.bridge.halt()
                self.settings.update(
                    studybot_root=str(_.get("studybot_root", "")).strip().strip('"'),
                    studybot_python=str(_.get("studybot_python", "")).strip().strip('"'),
                )
                atomic(self._settings_path(), self.settings)
                self.monitor.solver.settings = self.settings
                return Ok(self.monitor.solver.preflight())
        if not isinstance(ui_token, str) or not secrets.compare_digest(ui_token, self.ui_token):
            raise ValueError("请从插件控制面板操作。")
        if self.bridge and operation in HISTORY_OPERATIONS:
            history_lock = self.monitor.delivery_lock if self.monitor else self.bridge.lock
            with history_lock:
                return await self._control(
                    operation,
                    game_dir,
                    text,
                    flag,
                    enabled,
                    anchor_id,
                    _.get("history_record_id"),
                    target_vent_id,
                    entry_vent_id,
                )
        if self.bridge and operation not in ("connect", "resume"):
            with self.bridge.lock:
                return await self._control(
                    operation,
                    game_dir,
                    text,
                    flag,
                    enabled,
                    anchor_id,
                    _.get("history_record_id"),
                    target_vent_id,
                    entry_vent_id,
                )
        return await self._control(
            operation,
            game_dir,
            text,
            flag,
            enabled,
            anchor_id,
            _.get("history_record_id"),
            target_vent_id,
            entry_vent_id,
        )

    async def _control(
        self,
        operation,
        game_dir,
        text,
        flag,
        enabled,
        anchor_id,
        history_record_id=None,
        target_vent_id=None,
        entry_vent_id=None,
    ):
        if operation == "connect":
            candidate = Path(game_dir.strip().strip('"')).expanduser().resolve()
            if not (candidate / "Among Us.exe").is_file():
                raise ValueError("目录内找不到 Among Us.exe，请选择游戏根目录。")
            if not (candidate / "BepInEx/plugins/AUTelemetryProbe/AUTelemetryProbe.dll").is_file():
                raise ValueError("游戏端组件未安装，请先运行适配包 install.ps1。")
            settings = {**self.settings, "game_dir": str(candidate)}
            atomic(self._settings_path(), settings)
            self.settings = settings
            self._connect(str(candidate))
        elif operation in ("pause", "resume"):
            if not self.monitor:
                raise ValueError("请先保存连接。")
            if operation == "resume":
                self.monitor.start()
            self.monitor.paused = operation == "pause"
            if operation == "pause" and self.bridge:
                self.monitor.solver.set_enabled(False)
                self.monitor.pilot.set_enabled(False, source="panel.pause")
                self.bridge.halt()
        elif operation in HISTORY_OPERATIONS:
            if not self.bridge:
                raise ValueError("请先保存游戏连接。")
            with self.bridge.lock:
                if operation in ("history_pause", "history_resume"):
                    enabled = operation == "history_resume"
                    settings = {**self.settings, "history_enabled": enabled}
                    atomic(self._settings_path(), settings)
                    self.settings = settings
                    self.bridge.journal.set_enabled(enabled)
                    result = {
                        "recordingEnabled": enabled,
                        "summary": "已恢复行动记录和历史读取。"
                        if enabled
                        else "已暂停行动记录和历史读取；已有文件保留，AI 只读取当前实时状态。",
                    }
                else:
                    self.bridge.read()
                    if operation == "history_clear_current" and history_record_id != self.bridge.journal.doc.get(
                        "recordId"
                    ):
                        raise ValueError("当前显示的对局记录已变化，请刷新面板后重新确认清理。")
                    result = self.bridge.journal.clear(all_matches=operation == "history_clear_all")
            if self.monitor:
                self.monitor.history_changed(operation)
            return Ok(result)
        elif operation in ("pilot_on", "pilot_off", "start_all"):
            if not self.monitor:
                raise ValueError("请先保存连接。")
            on = operation != "pilot_off"
            if on:
                self.monitor.solver.set_enabled(True)
            else:
                self.monitor.solver.set_enabled(False)
            self.monitor.pilot.set_enabled(on, source="panel." + operation)
            if on:
                self.monitor.paused = False
                self.monitor.start()
                self.bridge.set_runtime("autoNavigation", True)
            else:
                self.bridge.halt()
            if operation == "start_all":
                self.monitor.auto_reply = True
                self.settings["auto_reply"] = True
                atomic(self._settings_path(), self.settings)
                role = self.bridge.read().get("ownRole") or {}
                self.bridge.set_runtime("autoReportBodies", not role.get("isImpostor", False))
                self.bridge.set_runtime("meetingControl", True)
                # Keep the user's explicit vent permission. Fresh installs
                # default off; starting autoplay must not undo their choice.
                self.bridge.set_runtime("minigameControl", False)
        elif operation == "runtime":
            if not self.bridge:
                raise ValueError("请先保存连接。")
            if flag == "autoNavigation" and not enabled and self.monitor:
                self.monitor.solver.set_enabled(False)
                self.monitor.pilot.set_enabled(False, source="panel.navigation_permission_off")
            if flag == "minigameControl":
                self.monitor.solver.set_enabled(enabled)
                self.bridge.set_runtime("minigameControl", False)
            else:
                self.bridge.set_runtime(flag, enabled)
        elif operation == "navigate":
            if self.monitor:
                self.monitor.solver.set_enabled(False)
                self.monitor.pilot.set_enabled(False, source="panel.manual_navigation")
            result = self._call("navigate", anchor_id)
            if "error" in result:
                raise ValueError(result["error"])
            return Ok(result)
        elif operation == "manual_vent":
            result = await self.among_us_manual_vent(text)
            if "error" in result:
                raise ValueError(result["error"])
            return Ok(result)
        elif operation in ("vent_status", "vent_travel", "vent_exit"):
            action = {"vent_status": "status", "vent_travel": "travel", "vent_exit": "exit"}[operation]
            result = await self.among_us_vent(action, target_vent_id, entry_vent_id)
            if "error" in result:
                raise ValueError(result["error"])
            return Ok(result)
        elif operation in ("reply_on", "reply_off"):
            self.settings["auto_reply"] = operation == "reply_on"
            atomic(self._settings_path(), self.settings)
            if self.monitor:
                self.monitor.auto_reply = self.settings["auto_reply"]
        elif operation in ("vote_on", "vote_off"):
            self.settings["auto_vote"] = operation == "vote_on"
            atomic(self._settings_path(), self.settings)
            if self.monitor:
                self.monitor.auto_vote = self.settings["auto_vote"]
        elif operation == "say":
            state = self._call("read")
            result = self._call("say", text, state.get("sessionId"), state.get("meetingId"))
            if "error" in result:
                raise ValueError(result["error"])
            return Ok(result)
        elif operation == "stop":
            if self.monitor:
                self.monitor.solver.set_enabled(False)
                self.monitor.pilot.set_enabled(False, source="panel.stop")
            result = self._call("halt")
            if "error" in result:
                raise ValueError(result["error"])
            return Ok(result)
        else:
            raise ValueError("未知操作。")
        return Ok({"summary": "设置已应用。"})

    @llm_tool(
        name="among_us_state",
        description="读取Among Us当前可见状态、附近玩家位置、会议ID和地图移动锚点。过期状态connected为false，不得用于执行动作。",
        parameters=EMPTY,
    )
    async def among_us_state(self, **_):
        state = model_state(self._call("read"))
        if self.bridge:
            state["anchors"] = self.bridge.anchors()
        return state

    @llm_tool(
        name="among_us_history",
        description="详细读取本局行动经过：actionRecord按时间给出中文任务进度、进入区域、目击玩家/移动/离开视野。按identity和taskBatch区分不同对局与任务刷新；last_seen_only为历史，不能当作当前角色位置。kind为all/route/tasks/encounters，limit为最近1至200条。",
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["all", "route", "tasks", "encounters"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
        },
    )
    async def among_us_history(self, kind="all", limit=30, **_):
        result = self._call("history", kind, limit)
        result.pop("logDirectory", None)
        return result

    @llm_tool(
        name="among_us_map",
        description="直接读取自身当前位置、按时间排列的已走路线摘要、计划中的后续路径点和最后目击玩家。结果为可读文本与JSON，不需要访问本地文件；计划路线不代表已经走过。",
        parameters=EMPTY,
    )
    async def among_us_map(self, **_):
        return self._call("map_context")

    @llm_tool(
        name="among_us_meeting_guide",
        description="只读Among Us会议策略和52项术语。term可选，精确查金水/银水/11/互保/狼坑/秒报/板子等；不传返回角色策略、证据规则、话术示例及全术语。词义不是身份判定，不发送消息或投票。",
        parameters={"type": "object", "properties": {"term": {"type": "string", "maxLength": 40}}},
    )
    async def among_us_meeting_guide(self, term=None, **_):
        from .meeting_strategy import meeting_guide

        return meeting_guide(term)

    @llm_tool(
        name="among_us_say",
        description="把text中1至100字发送到当前Among Us会议，text必填，ID可省略由接口实时绑定；显式ID必须为最新。无需ui_token。语音回复不会自动发送。submitted仅为提交。",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 100}, **MEETING},
            "required": ["text"],
        },
    )
    async def among_us_say(self, text=None, session_id=None, meeting_id=None, **_):
        return self._call("say", text, session_id, meeting_id)

    @llm_tool(
        name="among_us_sabotage",
        description="普通内鬼主动制造破坏，target选lights/comms/reactor/oxygen。需自动控制已启用；会议/冷却中不能执行。无需ui_token，submitted不代表已经破坏，实际结果见sabotage.lastAction。",
        parameters={
            "type": "object",
            "properties": {"target": {"type": "string", "enum": ["lights", "comms", "reactor", "oxygen"]}},
        },
    )
    async def among_us_sabotage(self, target="lights", **_):
        if not self.bridge:
            return {"error": "请先连接游戏。"}
        with self.bridge.lock:
            result = self._call("sabotage", target)
            if result.get("status") == "submitted" and self.monitor and self.monitor.pilot.enabled:
                controller = self.monitor.pilot.impostor
                controller.sabotage_command = result["commandId"]
                controller.sabotage_submitted = time.monotonic()
                controller.next_sabotage = controller.sabotage_submitted + 35
            return result

    @llm_tool(
        name="among_us_kill",
        description="普通内鬼击杀原生按钮当前选中的可见非同伴目标，player_id可省略。需自动控制已启用，冷却、目击者和墙体校验仍有效；submitted不是击杀成功。无需ui_token。",
        parameters={"type": "object", "properties": {"player_id": {"type": "integer", "minimum": 0, "maximum": 254}}},
    )
    async def among_us_kill(self, player_id=None, **_):
        if not self.bridge:
            return {"error": "请先连接游戏。"}
        with self.bridge.lock:
            state = self.bridge.read()
            target = player_id if player_id is not None else (state.get("ownRole") or {}).get("killTargetId")
            if target is None:
                return {"error": "原生击杀按钮尚未选中目标，请先靠近可见的非同伴玩家。"}
            result = self._call("kill", target, state["sessionId"], state["matchId"])
            if result.get("status") == "submitted" and self.monitor and self.monitor.pilot.enabled:
                controller = self.monitor.pilot.impostor
                controller.release()
                controller.kill_command, controller.target_player = result["commandId"], target
                controller.submitted = time.monotonic()
            return result

    @llm_tool(
        name="among_us_vent",
        description="普通内鬼管道技能。status查询真实管道编号/中文位置/连通出口；enter钻入管道并保持隐藏，move传target_vent_id管内移动不出管；approach传entry_vent_id走到入口，完成不等于钻管；travel提供target_vent_id，站在入口旁后自动进管、沿相连管道移动并出管；entry_vent_id可选。需面板允许管道技能并开启导航执行器，exit可取消退出。不能跨网络或从远处传送。读取ventTravel确认，submitted不代表完成。",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["status", "approach", "enter", "move", "travel", "exit"]},
                "target_vent_id": {"type": "integer", "minimum": 0},
                "entry_vent_id": {"type": "integer", "minimum": 0},
            },
        },
    )
    async def among_us_vent(self, action="status", target_vent_id=None, entry_vent_id=None, **_):
        if not self.bridge:
            return {"error": "请先连接游戏。"}
        with self.bridge.lock:
            result = self._call("vent", action, target_vent_id, entry_vent_id)
            if result.get("status") == "submitted" and self.monitor:
                pilot = self.monitor.pilot
                if pilot.pending:
                    self.bridge.release_navigation(pilot.pending)
                pilot.pending = pilot.target_task = None
                pilot.impostor.release()
            return result

    @llm_tool(
        name="among_us_manual_vent",
        description="人为接管管道：command可为钻管道 [入口编号]、管道移动 出口编号、出管道、管道传送 出口编号。进管或管内移动后仍保持隐藏，须另发出管道。成功提交后暂停自动游玩，游戏回执才证明执行；需管道许可与导航执行器。",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string", "maxLength": 80}},
            "required": ["command"],
        },
    )
    async def among_us_manual_vent(self, command="钻管道", **_):
        if not self.bridge:
            return {"error": "请先连接游戏。"}
        with self.bridge.lock:
            result = self._call("manual_vent", command)
            if result.get("status") == "submitted" and self.monitor:
                self.monitor.pilot.set_enabled(False, source="manual_vent")
                self.monitor.solver.set_enabled(False)
                self.monitor.pilot.impostor.release()
                self.monitor.pilot.pending = self.monitor.pilot.target_task = None
                result["autopilotPaused"] = True
            return result

    @llm_tool(
        name="among_us_escape_policy",
        description="设置击杀后自动撤离规则，enabled=true优先管道，false只走地面；需开启自动游玩和管道许可。出口观察2至15秒，无人再出管。只修改策略，不能声称已完成进管或撤离。",
        parameters={"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
    )
    async def among_us_escape_policy(self, enabled=True, **_):
        result = self._call("set_runtime", "impostorVentEscape", enabled)
        if "error" in result:
            return result
        return {
            "status": "configured",
            "completed": False,
            "enabled": enabled,
            "summary": "已设置击杀后优先管道撤离；需自动游玩和管道许可已开启。此回执仅证明规则已保存，没有执行进管。"
            if enabled
            else "已设置击杀后地面撤离，没有执行进管。",
        }

    @llm_tool(
        name="among_us_vote",
        description="实际执行会议投票；meetingDecision要求决策时须选择合法玩家或跳过，不能只说已经投票。player_id取最新voting.candidates合法玩家ID，-1为跳过，证据不足优先跳过。必须指定最新sessionId和meetingId，并满足voting.canVote及voteGate.ready。submitted或pending不表示成功，voting.hasVoted才表示游戏已记录；已投不会重复提交。",
        parameters={
            "type": "object",
            "properties": {"player_id": {"type": "integer", "minimum": -1, "maximum": 254}, **MEETING},
            "required": ["player_id", "session_id", "meeting_id"],
        },
    )
    async def among_us_vote(self, player_id=None, session_id=None, meeting_id=None, **_):
        return self._call("vote", player_id, session_id, meeting_id)

    @llm_tool(
        name="among_us_navigate",
        description="任务阶段在The Skeld地图移动到among_us_state列出的真实anchorId。只负责寻路，不会自动完成所有小游戏。",
        parameters={"type": "object", "properties": {"anchor_id": {"type": "string"}}, "required": ["anchor_id"]},
    )
    async def among_us_navigate(self, anchor_id=None, **_):
        if self.monitor and self.monitor.pilot.enabled:
            return {"error": "面板自动任务导航正在控制移动；请使用面板关闭后再手动指定目标，避免控制器争用。"}
        return self._call("navigate", anchor_id)

    @llm_tool(name="among_us_stop", description="停止Among Us当前自动移动。", parameters=EMPTY)
    async def among_us_stop(self, **_):
        if self.bridge:
            with self.bridge.lock:
                if self.monitor:
                    self.monitor.solver.set_enabled(False)
                    self.monitor.pilot.set_enabled(False, source="tool.among_us_stop")
                return self._call("halt")
        return {"error": "请先连接游戏。"}
