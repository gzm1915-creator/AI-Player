"""Bounded, self-contained spatial evidence for models without filesystem access."""
from __future__ import annotations
from datetime import datetime, timezone

ROOMS = {"Cafeteria": "餐厅", "Weapons": "武器室", "Navigation": "导航室", "O2": "氧气室",
         "Shields": "护盾室", "Communications": "通讯室", "Storage": "仓库", "Admin": "管理室",
         "Electrical": "电力室", "Security": "保安室", "MedBay": "医疗室", "Reactor": "反应堆",
         "UpperEngine": "上引擎室", "LowerEngine": "下引擎室",
         "Hallway_BigY": "东侧三岔走廊", "Hallway_Cross": "西侧十字走廊", "Hallway_South": "南侧走廊"}

TASK_NAMES = {"upload-data": "传输数据", "data": "传输数据", "swipe-card": "刷卡", "med-bay": "检查样本",
              "asteroids": "清除小行星", "clear-asteroids": "清除小行星",
              "med-scanner": "提交扫描", "unlock-manifolds": "解锁歧管", "garbage": "清空垃圾槽",
              "clean-filter": "清理氧气过滤器", "divert-power": "电力转接", "stabilize-steering": "稳定航向",
              "chart-course": "规划航线", "shield": "启动护盾", "gas-can": "装填燃料",
              "fix-wiring": "修复线路", "calibrate": "校准分配器", "fuel-engine": "为引擎加燃料",
              "align-engine": "校准引擎", "start-reactor": "启动反应堆", "vent-cleaning": "清理通风口"}
ANCHOR_NAMES = {"cafeteria.default_start": "餐厅·出生点", "cafeteria.east_departure": "餐厅·东侧出口",
                "cafeteria.southeast_bypass_west": "餐厅·东南绕行西侧", "cafeteria.southeast_bypass_east": "餐厅·东南绕行东侧",
                "cafeteria.returned_default": "餐厅·返回点", "cafeteria.native_runner_return": "餐厅·返回点",
                "cafeteria.left": "餐厅·西侧通道", "cafeteria.right": "餐厅·东侧通道", "cafeteria.bottom": "餐厅·南侧通道",
                "cafeteria.right.tasks": "餐厅·东侧任务区", "cafeteria.right.data_start": "餐厅·数据终端入口",
                "weapons.arrival_default_path": "武器室·入口", "weapons.smooth_arrival": "武器室·入口过渡点",
                "weapons.guided_start": "武器室·通道起点", "weapons.returned_from_o2": "武器室·氧气室方向通道",
                "o2.guided_arrival": "氧气室·入口", "navigation.guided_arrival": "导航室·入口",
                "shields.guided_arrival": "护盾室·入口", "communications.guided_arrival": "通讯室·入口",
                "junction.center": "中央走廊·交叉口", "junction.left.01": "西北走廊·交叉口",
                "junction.left.02": "西侧走廊·交叉口", "junction.left.03": "西南走廊·交叉口",
                "junction.right.01": "东侧走廊·北段交叉口", "junction.right.02": "东侧走廊·中段交叉口",
                "junction.right.03": "南侧走廊·东段交叉口", "engine.lower": "下引擎室·入口",
                "engine.upper": "上引擎室·入口", "reactor.entry": "反应堆·入口", "security.entry": "保安室·入口",
                "medical.entry": "医疗室·入口", "admin.anchor.top": "管理室·北侧通道",
                "admin.anchor.bottom": "管理室·南侧通道", "electrical.anchor.top": "电力室·北侧通道",
                "electrical.anchor.bottom": "电力室·南侧入口", "storage.orbit.right": "仓库·东侧通道",
                "storage.orbit.left": "仓库·西侧通道", "storage.orbit.top": "仓库·北侧通道",
                "storage.orbit.bottom": "仓库·南侧通道", "hallway_big_y.east_turn": "东侧三岔走廊·东侧转角",
                "hallway_big_y.shields_south_turn": "东侧三岔走廊·护盾方向南侧转角",
                "hallway_big_y.shields_west_turn": "东侧三岔走廊·护盾方向西侧转角"}
ANCHOR_PREFIX_ROOMS = {"admin": "Admin", "cafeteria": "Cafeteria", "weapons": "Weapons", "cockpit": "Navigation",
                       "life-support": "O2", "shields": "Shields", "comms": "Communications", "storage": "Storage",
                       "electrical": "Electrical", "left-engine": "UpperEngine", "right-engine": "LowerEngine",
                       "medical": "MedBay", "security": "Security", "reactor": "Reactor", "big-y": "Hallway_BigY"}


def anchor_label(anchor_id, room=None):
    aid = str(anchor_id or "")
    if aid in ANCHOR_NAMES:
        return ANCHOR_NAMES[aid]
    parts = aid.split(".")
    if len(parts) >= 3 and parts[0] == "skeld":
        room_name = ROOMS.get(room or ANCHOR_PREFIX_ROOMS.get(parts[1]), "地图")
        task = TASK_NAMES.get(parts[2], "通风口" if parts[2] == "vent" else "参考点")
        suffix = {"lower": "下侧", "upper": "上侧", "north": "北侧", "south": "南侧", "approach": "接近点"}.get(parts[-1])
        return room_name + "·" + task + ("·" + suffix if suffix else "")
    return ROOMS.get(room, room) or aid or "未标注位置"


TASK_TYPES = {"FixWiring": "修复线路", "SubmitScan": "提交扫描", "InspectSample": "检查样本",
              "UploadData": "传输数据", "DownloadData": "下载数据", "DivertPower": "转接电力",
              "AcceptPower": "接收电力", "SwipeCard": "刷卡", "FuelEngines": "为引擎加燃料",
              "AlignEngineOutput": "校准引擎", "ChartCourse": "规划航线", "StabilizeSteering": "稳定航向",
              "ClearAsteroids": "清除小行星", "EmptyGarbage": "清空垃圾槽", "EmptyChute": "清空垃圾槽",
              "CleanO2Filter": "清理氧气过滤器", "CalibrateDistributor": "校准分配器",
              "PrimeShields": "启动护盾", "UnlockManifolds": "解锁歧管", "StartReactor": "启动反应堆",
              "VentCleaning": "清理通风口"}
DIRECTIONS = {"same_position": "身边", "+X": "右侧", "-X": "左侧", "+Y": "上方", "-Y": "下方",
              "+X,+Y": "右上方", "+X,-Y": "右下方", "-X,+Y": "左上方", "-X,-Y": "左下方"}


def navigation_options(anchors):
    """Human destinations, stable executable IDs, no English fallback in UI."""
    result, counts = [], {}
    for a in anchors:
        aid, room = a.get("anchorId"), a.get("roomId")
        if not aid:
            continue
        name = anchor_label(aid, room)
        if name == aid or not any("\u4e00" <= c <= "\u9fff" for c in name):
            name = "地图参考点"
        category = "task" if a.get("kind") == "task" else "vent" if a.get("kind") == "vent" else "waypoint"
        prefix = {"task": "做任务：", "waypoint": "去这里：", "vent": "通风口："}[category]
        if "divert-power" in aid:
            name = name.replace("电力转接", "分配电力" if room == "Electrical" else "接收电力")
        label = prefix + name.replace("·", " — ")
        counts[label] = counts.get(label, 0) + 1
        if counts[label] > 1:
            label += f"（位置 {counts[label]}）"
        result.append({"anchorId": aid, "label": label, "kind": a.get("kind"), "category": category,
                       "room": room, "roomName": ROOMS.get(room)})
    return sorted(result, key=lambda a: ({"task": 0, "waypoint": 1, "vent": 2}[a["category"]], a["roomName"] or "", a["label"]))


def utc_clock(stamp):
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%H:%M:%S UTC")
    except (ValueError, TypeError):
        return str(stamp or "时间未知")


def position_text(position):
    if isinstance(position, dict) and isinstance(position.get("x"), (int, float)) and isinstance(position.get("y"), (int, float)):
        return f"({position['x']:.2f}, {position['y']:.2f})"
    return "坐标未记录"


def describe_event(event):
    kind, task = event.get("event"), event.get("task") or {}
    name = TASK_TYPES.get(task.get("taskType"), task.get("taskType") or "任务")
    player = event.get("player") or {}
    who = str(player.get("colorName") or "") + str(player.get("playerName") or "玩家")
    location = (event.get("location") or {}).get("label")
    where = ("，在" + location + "附近") if location else ""
    if kind == "task_completed":
        text = f"完成{name}（任务 {task.get('taskId')}，进度 {task.get('step')}/{task.get('maxStep', '?')}）"
    elif kind == "task_progress":
        text = f"{name}进度 {event.get('previousStep', '?')}→{task.get('step')}/{task.get('maxStep', '?')}，尚未确认整个任务完成"
    elif kind == "task_first_observed":
        text = f"首次读取{name}：{task.get('step', 0)}/{task.get('maxStep', '?')}（不是刚完成的证据）"
    elif kind == "task_reset":
        text = f"{name}进度重置为 {task.get('step', 0)}/{task.get('maxStep', '?')}"
    elif kind == "task_batch_started":
        text = f"任务已刷新，开始第 {event.get('taskBatch')} 批；此前完成记录属于旧任务批次"
    elif kind == "task_culture_started":
        text = "已启动医务室样本培养，离开做其他任务；培养结束后还需返回选择异常样本，尚未完成任务"
    elif kind == "player_encounter":
        text = f"看到{who}，对方坐标{position_text(player)}"
        relative = player.get("relativeToSelf") or {}
        if relative.get("distance") is not None:
            text += f"，在自身{relative.get('directionName') or DIRECTIONS.get(relative.get('direction'), '附近')}约 {relative['distance']:.2f} 单位"
    elif kind == "player_moved":
        text = f"{who}从{position_text(event.get('previousPosition'))}移动到{position_text(player)}"
    elif kind == "player_left_view":
        text = f"{who}已离开视野；最后看到在{position_text(player)}，当前去向未知"
    elif kind == "area_changed":
        text = "进入" + (location or "未标注区域") + "附近（按参考点估计）"
        where = ""
    elif kind == "observation_boundary":
        text = "开始新的观测段：" + {"meeting": "会议切换", "gap": "遥测中断或监听重启", "vent": "管道位移前后"}.get(event.get("reason"), "观测恢复") + "；两段之间不计作已走路线"
    elif kind in ("vent_completed", "vent_failed"):
        route = "→".join("管道#" + str(v) for v in (event.get("route") or [])) or "未记录路线"
        text = ("管道传送已完成" if kind == "vent_completed" else "管道传送失败") + f"（{route}）"
        if event.get("heldInside"):
            text = ("已确认钻入管道" if event.get("action") == "enter" else "已确认管内移动到站") + "，仍在管道内隐藏，尚未出管"
        if event.get("safeExitConfirmed"):
            text += "，已确认安全出管"
        if event.get("reason"):
            text += "；原因：" + str(event["reason"])[:180]
        text += "（内鬼私密记录）"
    elif kind == "repair_completed":
        repair = {"FixLights": "照明", "RestoreOxy": "氧气", "FixComms": "通讯", "ResetReactor": "反应堆"}.get(event.get("repairType"), "破坏")
        text = f"修复{repair}技能结束，游戏系统确认破坏已解除"
    elif kind == "impostor_intent":
        intent = {"cover": "前往假任务掩护点", "patrol": "巡查其他房间内部", "hunt": "接近当前可见目标", "search": "按短时预测寻找失联目标（非实时目击）", "escape": "离开击杀现场"}.get(event.get("intent"), "内鬼行动")
        text = "计划" + intent + "：" + anchor_label(event.get("targetAnchorId")) + "（仅为行动意图，未确认到达）"
    elif kind == "impostor_cover":
        text = "到达" + anchor_label(event.get("targetAnchorId")) + "，做假任务掩护；未完成真实任务"
        if event.get("dwellSeconds") is not None:
            text += f"；计划停留 {event['dwellSeconds']} 秒（依据本局任务栏）"
    elif kind == "impostor_patrol_arrived":
        text = "游戏导航确认到达巡场点" + anchor_label(event.get("targetAnchorId")) + "，重新观察机会（内鬼私密记录）"
    elif kind == "crew_group_observed":
        text = "当前可见团体至少包含两名其他玩家：" + "、".join(str(p) for p in event.get("playerIds", [])) + "；不代表确认阵营或持续互保"
    elif kind == "autopilot_recovery":
        text = "自动游玩恢复处理：" + str(event.get("reason") or "释放失效路线后重新规划")
    elif kind == "autopilot_control":
        text = ("开启" if event.get("enabled") else "关闭") + "自动游玩；来源：" + str(event.get("source") or "用户操作")
    elif kind == "impostor_kill_confirmed":
        text = f"游戏确认自身击杀玩家 {event.get('targetPlayerId')}，开始撤离（内鬼私密记录）"
    elif kind == "impostor_traffic":
        text = (f"选择随移动人群短时同行，计划{event.get('durationSeconds')}秒；路线仍须游戏确认"
                if event.get("stage") == "begin" else "结束随人流同行：" + str(event.get("reason") or "恢复假任务与巡视"))
    elif kind == "impostor_vent_intent":
        text = f"击杀后已请求管道撤离：入口#{event.get('entry')}→出口#{event.get('exit')}，计划连续观察{event.get('observeSeconds')}秒再出管；尚未完成（内鬼私密记录）"
    elif kind == "impostor_sabotage_confirmed":
        target = {"lights": "照明", "comms": "通讯", "reactor": "反应堆", "oxygen": "氧气"}.get(event.get("target"), "设施")
        text = "提交破坏请求后，游戏系统确认" + target + "破坏已发生（内鬼私密记录）"
    elif kind.startswith("navigation_"):
        target = anchor_label(event.get("targetAnchorId"))
        text = {"navigation_started": "开始前往", "navigation_completed": "已到达", "navigation_failed": "前往失败："}.get(kind, "导航：") + target
        if event.get("error"):
            text += "，原因：" + str(event["error"])[:180]
    else:
        text = str(kind or "观测")
    if event.get("position"):
        text += "；自身" + position_text(event["position"])
    return utc_clock(event.get("at")) + " " + text + where


def action_record(history, limit=12):
    events = history.get("timeline", [])[-limit:]
    return {"scope": history.get("scope", "historical"), "identity": history.get("identity"),
            "recordingEnabled": history.get("recordingEnabled", True), "clearedAt": history.get("clearedAt"),
            "recordId": history.get("recordId"),
            "taskBatch": history.get("taskBatch", 1), "order": "从早到晚", "timeZone": "UTC",
            "events": [{**e, "description": describe_event(e)} for e in events],
            "text": "\n".join(describe_event(e) for e in events) or ("用户已暂停行动记录和历史读取。" if history.get("recordingEnabled") is False else "尚无详细事件。"),
            "notice": "同一任务ID可在新批次复用；按identity、taskBatch、时间区分。路过任务点不等于执行任务。历史目击不等于当前可见。"}


def model_history(history, limit=12):
    result = {k: history[k] for k in ("identity", "scope", "taskBatch", "recordId", "recordingEnabled", "clearedAt", "startedAt", "updatedAt", "routeSummary", "distance", "routePointCount", "notice") if k in history}
    result["actionRecord"] = action_record(history, limit)
    result["lastSeenPlayers"] = history.get("lastSeenPlayers", [])[:12]
    result["taskEvents"] = [{k: e[k] for k in ("eventId", "event", "at", "taskBatch", "task", "position", "previousStep", "location") if k in e}
                            for e in history.get("taskEvents", [])[-12:]]
    return result


def current_summary(state):
    """Lead with this sample's facts, before general rules or old history."""
    stamp = state.get("telemetryAt") or "未知"
    history = state.get("history") or {}
    memory_notice = ""
    if history.get("clearedAt"):
        memory_notice += f"行动记录已于{history['clearedAt']}清理，不能引用清理前的旧记录。"
    if history.get("recordingEnabled") is False:
        memory_notice += "用户已暂停行动记录和历史读取，不要沿用旧推送复述行动。"
    if not state.get("connected"):
        return f"本次游戏遥测连接已断开（connected=false），当前位置和附近玩家不可用；最后采样：{stamp}。" + memory_notice
    location = state.get("location") or {}
    position = (state.get("self") or {}).get("position") or {}
    x, y = location.get("x", position.get("x")), location.get("y", position.get("y"))
    text = "本次游戏遥测连接正常（connected=true）"
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        text += f"；自身坐标（{x:.2f}, {y:.2f}）"
    estimate = location.get("areaEstimate") or {}
    if estimate.get("room"):
        text += "，约在" + ROOMS.get(estimate["room"], estimate["room"])
    detection = state.get("playerDetection") or {}
    players = state.get("nearbyPlayers") or []
    if players:
        names = [str(p.get("playerName") or p.get("name") or p.get("playerId")) for p in players[:8]]
        text += "；当前可见玩家：" + "、".join(names)
    elif detection.get("status") in ("telemetry_stale", "error", "unavailable", "legacy_probe"):
        text += "；玩家检测当前不可用"
    else:
        text += "；当前采样未发现可见玩家"
    recent = state.get("recentPlayers") or []
    if recent:
        text += "；近期离开检测范围：" + "、".join(str(p.get("playerName") or p.get("playerId")) + f"（{p.get('ageSeconds', '?')}秒前）" for p in recent[:6])
        text += "，仅有最后目击位置，去向不确定，仍需考虑目击风险"
    scenes = state.get("crimeScenes") or []
    if scenes:
        text += "；本局自身击杀现场（自动路线避让）：" + "、".join(f"({s['x']:.1f},{s['y']:.1f})" for s in scenes[-5:])
    vent = state.get("ventTravel") or {}
    if vent.get("state") == "completed" and vent.get("reachedTarget") and vent.get("safeExitConfirmed"):
        text += "；游戏确认最近一次管道传送到站并安全出管"
    elif vent.get("busy"):
        text += "；管道动作尚未完成：" + str(vent.get("reason") or "等待游戏确认")
    elif vent.get("state") == "failed":
        text += "；最近管道动作失败，不能声称已完成"
    return text + f"；采样时间：{stamp}。本次观测取代此前的连接和位置状态。" + memory_notice


def route_summary(points, limit=12):
    visits = []
    for point in points:
        anchor = point.get("nearestAnchor") or {}
        position = point.get("position") or {}
        aid = anchor.get("anchorId") if anchor.get("distance", 99) <= 1.5 else None
        room = anchor.get("room")
        label = anchor_label(aid, room) if aid else "未标注位置"
        key = aid or label
        if visits and visits[-1]["anchorId"] == key and not point.get("segmentStart"):
            visits[-1]["lastAt"] = point.get("at")
            visits[-1]["position"] = position
            continue
        visits.append({"anchorId": key, "label": label, "firstAt": point.get("at"), "firstPosition": position,
                       "lastAt": point.get("at"), "position": position,
                       "segmentStart": bool(point.get("segmentStart"))})
    visits = visits[-limit:]
    text = ""
    for index, visit in enumerate(visits):
        if index:
            text += " | 新观测段：" if visit["segmentStart"] else " → "
        p = visit["position"]
        text += visit["label"] + "附近"
        if "x" in p and "y" in p:
            text += f"({p['x']:.2f},{p['y']:.2f})"
        text += "[" + utc_clock(visit["firstAt"]) + "–" + utc_clock(visit["lastAt"]) + "]"
    return {"order": "从早到晚", "visits": visits, "text": text or "尚无行走记录",
            "notice": "这是带时间的已走参考点附近位置；路过任务锚点不等于做过任务，须查任务进度证据。新观测段不能当作连续步行。"}


def model_state(state):
    # Keep live gameplay facts and exact command/session IDs, but don't make
    # another model open private files or consume the full diagnostic stream.
    data = {"summary": current_summary(state),
            **{k: v for k, v in state.items() if k not in ("runtimeStatus", "runtimeControl", "history")}}
    history = state.get("history") or {}
    data["history"] = model_history(history)
    navigation = dict(data.get("navigation") or {})
    points = navigation.pop("plannedRoute", []) or []
    index = navigation.get("waypointIndex") or 0
    navigation["nextWaypoints"] = points[index:index + 8]
    if navigation.get("action") not in ("navigate", "pursue_player", "navigate_vent") or navigation.get("state") not in ("running", "paused", "holding", "seeking_task_button"):
        navigation["nextWaypoints"] = []
    navigation["targetLabel"] = anchor_label(navigation.get("targetAnchorId")) if navigation.get("targetAnchorId") else None
    data["navigation"] = navigation
    detection = data.get("playerDetection") or {}
    data["playerDetection"] = {k: v for k, v in detection.items() if k != "cameraDiagnostics"}
    data["dataAccess"] = "所需观测已包含在JSON中；不需要打开本地文件。among_us_map读取路线摘要，among_us_history查询历史证据。"
    return data
