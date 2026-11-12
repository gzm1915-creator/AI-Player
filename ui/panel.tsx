import { Page, Card, Stack, Inline, Text, Tip, Alert, StatusBadge, Field, Input, Select, Button, CodeBlock, useState, useEffect, useToast } from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"

export default function AmongUsPanel(props: PluginSurfaceProps<any>) {
  const state = props.state || {}
  const game = state.game || {}
  const observer = state.observer || {}
  const autopilot = observer.autopilot || game.autopilot || {}
  const minigame = observer.studybot || {}
  const impostor = autopilot.impostor || {}
  const social = autopilot.social || {}
  const members = social.members || []
  const visibleMembers = members.filter((p: any) => p.visibleNow).length
  const predictedMembers = members.filter((p: any) => !p.visibleNow).length
  const taskStages = {early: "前期任务", late: "剩余少量任务", done: "自身任务已完成", unknown: "等待任务列表"}
  const ownRole = game.ownRole || {}
  const roomSweep = impostor.roomSweep || {}
  const patrolRange = impostor.patrolRange || {}
  const traffic = impostor.trafficFollow || {}
  const roomNames = {Cafeteria:"餐厅", Weapons:"武器室", Navigation:"导航室", O2:"氧气室", Shields:"护盾室", Communications:"通讯室", Storage:"仓库", Admin:"管理室", Electrical:"电力室", Security:"保安室", MedBay:"医疗室", Reactor:"反应堆", UpperEngine:"上引擎室", LowerEngine:"下引擎室"}
  const recoveries = (autopilot.recoveryCount || 0) + (impostor.recoveryCount || 0)
  const pursuit = game.navigation?.pursuit || {}
  const phaseLabel = { cover: "假任务掩护", patrol: "主动巡场", flow: "随移动人流同行", hunt: "持续追踪可见目标", search: "短时预测寻找", escape: "击杀后撤离" }
  const [skillRoot, setSkillRoot] = props.useLocalState("studybotRoot", "")
  const [skillPython, setSkillPython] = props.useLocalState("studybotPython", "")
  const [path, setPath] = props.useLocalState("gamePath", "")
  const [text, setText] = props.useLocalState("chatText", "接口测试：你好，我是 N.E.K.O。")
  const [busy, setBusy] = useState(false)
  const [clearScope, setClearScope] = useState("")
  const [clearRecord, setClearRecord] = useState("")
  const [anchor, setAnchor] = props.useLocalState("manualAnchor", "")
  const [targetCategory, setTargetCategory] = props.useLocalState("manualTargetCategory", "task")
  const destinations = (state.anchors || []).filter(a => targetCategory === "all" || a.category === targetCategory)
  const targetLabel = (id: string) => (state.anchors || []).find(a => a.anchorId === id)?.label || "未选择目标"
  const vents = game.vents || []
  const ventTravel = game.ventTravel || {}
  const ventRole = game.ventRole || {}
  const ventStages = {idle:"等待指令",submitted:"等待游戏接收",entering:"正在进管",moving:"准备沿管道移动",awaiting_hop:"等待抵达下一段",exiting:"正在出管",exit_requested:"取消并退出",cleanup_exit:"失败后尝试退出",observing_exit:"出口观察中",cleanup_observing:"等待安全退出",finished:"已结束",failed:"执行失败",rejected:"条件不满足",unconfirmed:"未收到游戏确认"}
  const ventStates = {idle:"尚无请求",submitted:"已提交",running:"执行中",completed:"已完成",failed:"失败"}
  const ventLabel = (id: number) => vents.find((v: any) => v.id === id)?.label || `管道 #${id}`
  const [ventTarget, setVentTarget] = props.useLocalState("ventTarget", "")
  const [manualVentCommand, setManualVentCommand] = props.useLocalState("manualVentCommand", "钻管道")
  const [ventEntry, setVentEntry] = props.useLocalState("ventEntry", "")
  const entryOptions = vents.filter((v: any) => v.entryInRange || v.id === ventRole.currentVentId)
  const entry = ventRole.inVent ? vents.find((v: any) => v.id === ventRole.currentVentId)
    : ventEntry ? entryOptions.find((v: any) => v.id === Number(ventEntry))
    : [...entryOptions].sort((a: any, b: any) => (a.distance ?? Infinity) - (b.distance ?? Infinity))[0]
  const exitOptions = entry ? vents.filter((v: any) => (entry.connectedVentIds || []).includes(v.id)) : []
  const record = game.history?.actionRecord || {}
  const toast = useToast()
  const canControl = !!state.uiToken && (props.actions || []).some(a => a.id === "control" || a.entry_id === "control")
  useEffect(() => {
    let refreshing = false
    const timer = setInterval(async () => {
      if (refreshing) return
      refreshing = true
      try { await props.api.refresh() } catch (_) {} finally { refreshing = false }
    }, 2000)
    return () => clearInterval(timer)
  }, [props.api])
  async function control(operation: string, extra = {}) {
    setBusy(true)
    try {
      await props.api.call("control", { operation, ui_token: state.uiToken, ...extra })
      await props.api.refresh()
      toast.success(operation === "say" ? "已提交游戏发送请求。" : "操作已应用。")
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally { setBusy(false) }
  }
  async function runtime(flag: string, enabled: boolean) {
    return control("runtime", { flag, enabled })
  }
  return <Page title="Among Us 控制面板" subtitle="连接、自动任务导航、会议与观测">
    <Stack>
      {!canControl && <Alert tone="warning">请先点击页面右上角“启动”，再刷新面板。</Alert>}
      <Card title="游戏连接"><Stack>
        <Inline><StatusBadge tone={game.connected ? "success" : "warning"} label={game.connected ? "游戏已连接" : "等待游戏遥测"} />
          <StatusBadge tone={observer.alive && !observer.paused ? "success" : "warning"} label={observer.paused ? "监听已暂停" : observer.alive ? "监听运行中" : "监听未启动"} /></Inline>
        <Text>当前目录：{state.gameDir || "未设置"}</Text>
        <Field label="Among Us 游戏目录" help="Steam → Among Us → 管理 → 浏览本地文件，复制含 Among Us.exe 的目录。">
          <Input value={path} onChange={setPath} placeholder="在这里输入完整游戏目录" /></Field>
        <Inline><Button disabled={busy || !canControl || !(path.trim() || state.gameDir)} onClick={() => control("connect", { game_dir: path.trim() || state.gameDir })}>保存并重新连接</Button>
          <Button disabled={busy} onClick={() => props.api.refresh()}>刷新状态</Button></Inline>
        <Tip>路径保存在此插件的私有数据目录，立即生效，无需修改 N.E.K.O. 文件。</Tip>
      </Stack></Card>
      <Card title="记录管理"><Stack>
        <StatusBadge tone={game.history?.recordingEnabled === false ? "warning" : "success"} label={game.history?.recordingEnabled === false ? "记录与历史读取已暂停" : "正在记录并提供历史"} />
        <Inline><Button disabled={busy || !canControl || !state.gameDir} onClick={() => control(game.history?.recordingEnabled === false ? "history_resume" : "history_pause")}>{game.history?.recordingEnabled === false ? "恢复记录与历史读取" : "暂停记录与历史读取"}</Button>
          <Button disabled={busy || !canControl || !game.history?.identity || !game.history?.recordId} onClick={() => {setClearRecord(game.history.recordId); setClearScope("current")}}>清空当前对局记录</Button>
          <Button disabled={busy || !canControl || !state.gameDir} onClick={() => setClearScope("all")}>清空全部游戏记录</Button></Inline>
        {clearScope && <Alert tone="warning"><Stack>
          <Text>{clearScope === "all" ? "将永久删除此插件为当前游戏目录保存的所有对局路线、任务变化和玩家目击。" : "将永久删除面板所显示对局的路线、任务变化和玩家目击；其他对局保留。"}当前对局已保存 {game.history?.storedEventCount ?? game.history?.eventCount ?? 0} 条观测。记录开启时，清理后会从当前状态重新积累。</Text>
          <Inline><Button disabled={busy} tone="danger" onClick={async () => {await control(clearScope === "all" ? "history_clear_all" : "history_clear_current", {history_record_id: clearRecord}); setClearScope("")}}>确认清理</Button>
            <Button disabled={busy} onClick={() => setClearScope("")}>取消</Button></Inline>
        </Stack></Alert>}
        <Tip>暂停后保留文件，但停止采集和向 AI 提供历史；实时位置与当前玩家检测继续工作。清理只影响此插件的游戏记录，N.E.K.O. 自己保存的聊天记忆请在它的记忆管理中清理。</Tip>
        {game.history?.clearedAt && <Text>上次手动清理：{game.history.clearedAt} · AI 收到的接口数据会标记此次清理边界。</Text>}
      </Stack></Card>
      <Card title="原 StudyBot 技能包"><Stack>
        <Text>当前技能目录：{minigame.root || "未配置"}</Text>
        <Text>Python：{minigame.python || "未配置"}</Text>
        <Field label="StudyBot 目录（包含 src/tasks.py）"><Input value={skillRoot} onChange={setSkillRoot} placeholder="选择之前验证过的 StudyBot 源码目录" /></Field>
        <Field label="Python 可执行文件"><Input value={skillPython} onChange={setSkillPython} placeholder="完整 python.exe 路径；需安装 numpy、Pillow" /></Field>
        <Button disabled={busy || !canControl} onClick={() => control("studybot_config", {studybot_root: skillRoot.trim() || minigame.root, studybot_python: skillPython.trim() || minigame.python})}>保存并检查旧技能加载</Button>
        <Text>已载入技能：{(minigame.skills || []).join("、") || "尚未检查"}</Text>
        <Tip>复用原技能及反应堆技能。新增修灯、氧气、通讯、反应堆修复；破坏点导航待办，需手动到点打开面板，可单独点“开启小游戏操作”。氧气修复另需在此 Python 安装 opencv-python 和系统 Tesseract OCR；反应堆需要另一名玩家配合。游戏保持前台，浮窗避开操作区域。</Tip>
      </Stack></Card>
      <Card title="自动游玩"><Stack>
        <Alert tone="info">“开始自动游玩”按自身角色选择行为：船员随任务进度调整抱团，剩余一两个任务时优先跟团、接近任务点再脱团；普通内鬼交替做假任务与巡场，途中判断机会，确认击杀后撤离。使用原 StudyBot 技能组，需要游戏保持前台。</Alert>
        <Text>自身角色：{ownRole.roleType === "Impostor" ? "普通内鬼" : ownRole.isImpostor ? `特殊内鬼（${ownRole.roleType}，待适配）` : ownRole.roleType || "等待角色观测"} · 行为模式：{autopilot.rolePolicy === "basic_impostor" ? "基础内鬼" : "船员任务"}</Text>
        {autopilot.rolePolicy !== "basic_impostor" && <Stack>
          <Text>抱团与任务：{social.reason || "寻找至少两名其他玩家的移动团体"}</Text>
          <Text>任务阶段：{taskStages[social.taskStage] || "等待任务列表"} · 剩余任务：{social.remainingTasks ?? "未知"} · {social.taskPriority ? "先完成已选择的任务阶段" : "结合团体调整路线"}</Text>
          <Text>跟随团体：实时可见 {visibleMembers} 人 · 短时预测 {predictedMembers} 人 · 不代表已验证身份</Text>
          <Text>本轮抱团：{social.followElapsedSeconds ?? 0} / {social.followLimitSeconds ?? 90} 秒（适用于剩余一两个任务） · 脱团任务距离：{social.taskDetachDistance ?? 10} 单位</Text>
          <Text>疑似任务停留：{(social.motionEstimates || []).filter((p: any) => p.suspectedTaskStop).length} 人 · 根据持续静止与任务点距离推测</Text>
          <Tip>前期兼顾任务与开局40秒的沿途抱团；剩余一两个任务时，进入待办任务点10单位内即脱团，最多抱团90秒再去做任务。任务全完成后跟团观察；别人疑似在做任务时继续自己的任务，或移开一点观察。</Tip>
          <Tip>预测最多延续3秒，不能当作真实目击或击杀依据。团体每0.25秒复核；路线重规划至少间隔1.2秒且团体移动2单位。只沿已有通道行走，不打断已打开的小游戏。</Tip>
        </Stack>}
        {autopilot.rolePolicy === "basic_impostor" && <Stack>
          <Text>内鬼阶段：{phaseLabel[impostor.phase] || impostor.phase || "等待"} · 击杀冷却：{ownRole.killCooldown == null ? "未知" : `${Math.max(0, ownRole.killCooldown).toFixed(1)} 秒`} · 目标玩家：{impostor.targetPlayerId ?? "无"}</Text>
          <Text>{impostor.reason || "等待基础内鬼判断"}</Text>
          {impostor.motionEstimate && <Text>失联预测：玩家 {impostor.motionEstimate.playerId} · 最后目击已过 {impostor.motionEstimate.predictionAgeSeconds} 秒 · 只用于短时寻找，不能用于击杀</Text>}
          <Text>假任务依据：{impostor.fakeTask?.source === "own_task_bar" ? `本局任务栏 · ${impostor.fakeTask.taskType}` : "等待任务栏"} · 到点停留：{impostor.coverSeconds == null ? "随机5–15秒" : `${impostor.coverSeconds}秒`}</Text>
          <Text>主动巡场：已完成 {impostor.patrolCount || 0} 段 · 房间：{roomNames[impostor.patrolRoom] || "待选择"} · 下次优先：{impostor.nextActivity === "patrol" ? "巡查房间内部" : "假任务掩护"}</Text>
          <Text>房间缓冲：巡视至少 {roomSweep.requiredOtherRooms ?? 4} 个不同的其他房间后才允许回访；假任务房同样计入。</Text>
          <Text>最近到达：{(roomSweep.recentRooms || []).map((r: string) => roomNames[r] || r).join(" → ") || "暂无"} · 下个假任务：{roomSweep.nextFakeTaskAnchorId ? targetLabel(roomSweep.nextFakeTaskAnchorId) : "待选择"}</Text>
          <Text>区域轮换：{patrolRange.areaName || "等待位置"} · 连续停留 {patrolRange.elapsedSeconds ?? 0}/{patrolRange.limitSeconds ?? 65} 秒 · {patrolRange.exitDue ? "优先转往其他片区" : "结合假任务与人流调整路线"}</Text>
          <Text>随人流同行：{traffic.active ? `进行中 ${traffic.elapsedSeconds ?? 0}/${traffic.durationSeconds} 秒` : "等待合适的移动人群"} · 实时可见 {(traffic.members || []).filter((p: any) => p.visibleNow).length} 人 · 短时预测 {(traffic.members || []).filter((p: any) => !p.visibleNow).length} 人</Text>
          <Text>{traffic.reason || "遇到至少两名移动玩家后，短时同行，再恢复假任务与巡视"}</Text>
          <Tip>同一片区约65秒后优先换区；片区按自身位置估计，越界连续3秒才确认。每轮随人流12–24秒，间隔30–50秒；人群停下、分散或失联时结束，不在别人的任务点旁闲站。四房缓冲与击杀现场避让始终保留。</Tip>
          <Text>巡场只在房间内短停0.6–2秒；同房间冷却90秒，同落点冷却180秒，有其他内部点时换点。</Text>
          <Tip>假任务后巡视其他房间，再去下个任务点；周围房间有自己的假任务时直接做任务，提前选择后续目标。入口和通道不安排停留；路线转弯或安全检查仍可能短暂减速。假任务停留为5–15秒。</Tip>
          {(impostor.killDecisions || []).map((p: any) => <Text key={`decision-${p.playerId}`}>未出手判断 · 玩家 {p.playerId} · 距离 {p.distance} 单位 · 潜在目击者：{p.witnessIds?.length ? p.witnessIds.join("、") : "无"} · {p.reason}</Text>)}
          {impostor.lastPursuitFailure && <Alert tone="warning">最近接近失败 · 玩家 {impostor.lastPursuitFailure.playerId}：{impostor.lastPursuitFailure.error}</Alert>}
          <Text>击杀后撤离：{impostor.escapeReason || "优先考虑管道，条件不满足则地面撤离"}</Text>
          <Text>原生击杀回执：{game.impostorAction?.state || "尚无"} · {game.impostorAction?.reason || "尚未提交击杀"}</Text>
          <Text>主动破坏：{game.sabotage?.reason || "需游戏组件0.6.77"} · 冷却：{game.sabotage?.cooldown == null ? "未知" : `${Math.max(0, game.sabotage.cooldown).toFixed(1)} 秒`}</Text>
          <Text>破坏回执：{game.sabotage?.lastAction?.state || "尚无"} · {game.sabotage?.lastAction?.reason || "尚未提交"}</Text>
          <Tip>先掩护，再选择可见落单目标；刚失联时可沿通道预测寻找最多3秒，重新看见后才恢复原生追踪和击杀判断。自动按可见机会使用照明或通讯破坏。假任务不算任务完成。复杂埋伏战术、特殊角色与修复点导航待办。</Tip>
        </Stack>}
        <Inline><Button disabled={busy || !canControl || !game.connected} tone="success" onClick={() => control("start_all")}>开始自动游玩</Button>
          <Button disabled={busy || !canControl} tone="danger" onClick={() => control("stop")}>停止全部自动移动</Button>
          <Button disabled={busy || !canControl || !observer.alive} onClick={() => control("pause")}>暂停监听</Button>
          <Button disabled={busy || !canControl || !state.gameDir} onClick={() => control("resume")}>恢复监听</Button></Inline>
        <StatusBadge tone={autopilot.enabled ? autopilot.state === "error" ? "danger" : "success" : "warning"} label={autopilot.enabled ? `自动导航：${autopilot.state}` : "自动导航未启用"} />
        <Text>{autopilot.reason || "开启自动游玩后会自动挑选未完成任务目标。"}{autopilot.target ? ` · 目标 ${targetLabel(autopilot.target)}` : ""}</Text>
        {autopilot.lastError && <Alert tone="danger">{autopilot.lastError}</Alert>}
        <Text>自动恢复：{recoveries} 次 · 最近原因：{(autopilot.rolePolicy === "basic_impostor" ? impostor.lastRecovery : autopilot.lastRecovery)?.reason || "暂无"}</Text>
        <Text>上次开关操作：{autopilot.lastControl?.reason || "尚无"} · 来源：{autopilot.lastControl?.source || "未知"}</Text>
        <Inline><Button disabled={busy || !canControl || !game.connected || autopilot.enabled} onClick={() => runtime("autoNavigation", !game.runtimeControl?.autoNavigation)}>{game.runtimeControl?.autoNavigation ? "关闭导航执行器" : "开启导航执行器"}</Button>
          <Button disabled={busy || !canControl || !game.connected} onClick={() => runtime("autoReportBodies", !game.runtimeControl?.autoReportBodies)}>{game.runtimeControl?.autoReportBodies ? "关闭自动报告尸体" : "开启自动报告尸体"}</Button>
          <Button disabled={busy || !canControl || !game.connected} onClick={() => runtime("useVents", !game.runtimeControl?.useVents)}>{game.runtimeControl?.useVents ? "关闭管道技能" : "允许管道技能"}</Button></Inline>
        <Inline><Button disabled={busy || !canControl || !game.connected} onClick={() => runtime("visualCapture", !game.runtimeControl?.visualCapture)}>{game.runtimeControl?.visualCapture ? "关闭视觉记录" : "开启视觉记录"}</Button>
          <Button disabled={busy || !canControl || !game.connected} onClick={() => runtime("meetingControl", !game.runtimeControl?.meetingControl)}>{game.runtimeControl?.meetingControl ? "关闭会议控制" : "开启会议控制"}</Button>
          <Button disabled={busy || !canControl} onClick={() => control(observer.autoReply === false ? "reply_on" : "reply_off")}>{observer.autoReply === false ? "开启会议聊天自动回应" : "关闭会议聊天自动回应"}</Button></Inline>
        <Text>游戏阶段：{game.connected ? game.phase : "遥测已断开，等待游戏"} · 会议：{game.meetingOpen ? "进行中" : "未开启"} · 推送次数：{observer.pushCount || 0}</Text>
        {game.connected && !ownRole.killSafetySupported && <Alert tone="warning">3至5秒安全判断和现场避让需要重启游戏加载0.6.81。</Alert>}
        {game.connected && !ownRole.localPursuitApproachSupported && <Alert tone="warning">接近玩家的路线修正尚未加载，请重启游戏加载0.6.82。</Alert>}
        {game.connected && (!ownRole.roomSweepEscapeSupported || !ownRole.lightsKillWaitSupported) && <Alert tone="warning">撤离路线和照明免读秒修正尚未加载，请重启游戏加载0.6.83。</Alert>}
        {game.connected && !ownRole.midEdgeNavigationSupported && <Alert tone="warning">路线中段重新接路修正尚未加载，请重启游戏加载0.6.84。</Alert>}
        <Text>出手前安全判断：正常照明下连续随机3–5秒；游戏确认照明破坏生效时免除额外读秒，修好灯后恢复观察。潜在目击者、原生击杀距离与冷却仍需满足。</Text>
        {(ownRole.killSafety || []).map(s => <Text key={`safety-${s.targetPlayerId}`}>玩家 {s.targetPlayerId}：{s.lightsWaitBypassed ? "照明破坏生效，免除额外读秒" : `安全观察 ${s.clearSeconds}/${Number(s.requiredSeconds).toFixed(1)} 秒`} · {s.ready ? "安全条件满足，等待原生距离判定" : s.reason}</Text>)}
        <Text>本局击杀现场避让：{(game.crimeScenes || []).length} 处 · 保留到本局结束</Text>
        {(game.crimeScenes || []).map(s => <Text key={`scene-${s.targetPlayerId}`}>自身击杀玩家 {s.targetPlayerId} · X {s.x}，Y {s.y} · 避让半径 {s.avoidRadius ?? 3} 单位</Text>)}
        <Tip>自动目标和行走路线均避开已确认的自身击杀现场；允许向外撤离。没有安全路线时会暂停。此避让记忆独立于可清理的聊天历史。</Tip>
        <Text>持续追踪：{!game.connected ? "等待游戏连接" : ownRole.dynamicPursuitSupported ? pursuit.active ? `目标玩家 ${pursuit.targetPlayerId} · 已更新路线 ${pursuit.replans || 0} 次 · 速度 ${pursuit.targetSpeed ?? "—"}${pursuit.targetSlowed ? " · 已降速" : ""}` : "等待可见落单目标" : "需重启游戏加载 0.6.80"}</Text>
        <Text>导航状态：{game.navigation?.state || "未知"} · 目标：{game.navigation?.targetAnchorId ? targetLabel(game.navigation.targetAnchorId) : "无"} · 导航错误：{game.navigation?.error || "无"}</Text>
        <Text>监听心跳距今：{observer.heartbeatAge == null ? "尚无" : `${observer.heartbeatAge} 秒`} · 遥测时间：{game.telemetryAt || "尚无"}</Text>
        {(observer.lastError || game.error) && <Alert tone="danger">{observer.lastError || game.error}</Alert>}
        <Tip>完成情况以游戏任务进度为准；未完成目标冷却后可重试。小游戏开启时等待执行器，不会同时移动。普通语音不会自动转成游戏聊天。</Tip>
        <Field label="目的地类型"><Select value={targetCategory} onChange={value => {setTargetCategory(value); setAnchor("")}} options={[{value: "task", label: "任务地点"}, {value: "waypoint", label: "房间入口与通道"}, {value: "all", label: "全部地点（含通风口）"}]} /></Field>
        <Field label="手动导航目标" help="按中文房间和用途选择；靠近任务点不代表已经完成任务。"><Select value={destinations.some(a => a.anchorId === anchor) ? anchor : ""} onChange={setAnchor} options={[{value: "", label: "请选择想去的地点"}, ...destinations.map(a => ({value: a.anchorId, label: a.label}))]} /></Field>
        <Button disabled={busy || !canControl || !game.connected || !destinations.some(a => a.anchorId === anchor)} onClick={() => control("navigate", {anchor_id: anchor})}>前往这个地点</Button>
        <Button disabled={busy || !canControl || !game.connected || !minigame.ready} onClick={() => runtime("minigameControl", !minigame.enabled)}>{minigame.enabled ? "关闭小游戏操作" : "开启小游戏操作"}</Button>
        <Text>小游戏执行器：StudyBot · {minigame.state || "尚未加载"} · 任务：{minigame.taskType || "等待任务窗口"}</Text>
        <Text>{minigame.reason || "请先检查旧技能包加载；游戏端建议使用 0.6.80。"} · 已尝试 {minigame.attempts || 0}/3 次</Text>
        {(minigame.deferredTasks || []).map((t: any) => <Text key={`sample-${t.taskId}`}>检查样本：培养中，剩余约 {Math.ceil(t.remainingSeconds ?? 0)} 秒；已安排离开，准备好后返回。</Text>)}
        <Tip>检查样本分两次：启动培养后离开做其他任务，按游戏倒计时判断何时返回；等待不消耗选择样本的重试次数，也不算任务完成。</Tip>
        <Text>当前破坏：{game.activeEmergencies == null ? "游戏组件尚未提供或当前地图不支持" : game.activeEmergencies.length ? game.activeEmergencies.map(t => ({FixLights: "照明", RestoreOxy: "氧气", FixComms: "通讯", ResetReactor: "反应堆"}[t] || t)).join("、") : "未发生"} · 修复点导航：待办</Text>
        <Tip>原技能运行结束后以任务进度确认完成；修复技能须收到游戏系统解除证据。同一阶段或同一轮破坏最多尝试三次；失败后关闭窗口，破坏修复需手动重新打开。</Tip>
      </Stack></Card>
      <Card title="管道技能"><Stack>
        <Text>手动命令会直接交给游戏执行，提交成功后暂停自动游玩。</Text>
        <Field label="手动管道命令" help="钻管道 [入口编号] / 管道移动 出口编号 / 出管道 / 管道传送 出口编号"><Input value={manualVentCommand} onChange={setManualVentCommand} placeholder="例如：钻管道，或 管道移动 3" /></Field>
        <Inline><Button disabled={busy || !canControl || !game.connected || !manualVentCommand.trim()} onClick={() => control("manual_vent", {text: manualVentCommand.trim()})}>执行管道命令</Button>
          <Button disabled={busy || !canControl || !game.connected || !ventTravel.manualControlSupported || ventRole.inVent || ventTravel.busy} onClick={() => control("manual_vent", {text: "钻管道"})}>手动钻入附近管道</Button>
          <Button disabled={busy || !canControl || !game.connected || (!ventRole.inVent && !ventTravel.busy)} onClick={() => control("manual_vent", {text: "出管道"})}>手动出管道</Button></Inline>
        <Tip>钻入后保持在管道内；“管道移动 编号”到站后仍不出管。先开启导航执行器与管道许可，并站在入口旁；此命令不会从远处进管。</Tip>
        <Text>仅适用于存活的普通内鬼。管道编号来自游戏原生网络；必须先走到入口旁、开启“允许管道技能”和导航执行器。</Text>
        <StatusBadge tone={ventTravel.busy ? "success" : game.capabilities?.vent?.available ? "success" : "warning"} label={ventTravel.busy ? `管道执行中：${ventStages[ventTravel.stage] || "处理中"}` : game.capabilities?.vent?.available ? "管道技能可用" : (game.capabilities?.vent?.reason || "管道技能暂不可用")} />
        <Text>当前角色：{ventRole.inVent ? `在${ventLabel(ventRole.currentVentId)}内` : "在管道外"} · 实际当前管道：{ventRole.currentVentId >= 0 ? ventLabel(ventRole.currentVentId) : "无"}</Text>
        <Text>出口观察：{ventTravel.clearSeconds == null ? "尚未开始" : `${ventTravel.clearSeconds.toFixed(1)} / ${ventTravel.observeSeconds || 2} 秒`} · 附近玩家：{ventTravel.exitObservation?.status === "ok" ? ventTravel.exitObservation.nearbyCount : "尚未可靠检测"}</Text>
        <Text>回执：{ventStates[ventTravel.state] || "尚无请求"} · 阶段：{ventStages[ventTravel.stage] || "等待指令"} · {ventTravel.reason || "尚无管道请求"}</Text>
        <Inline><Button disabled={busy || !canControl || !game.connected} onClick={() => control("vent_status")}>读取管道状态</Button>
          <Button disabled={busy || !canControl || !(ventTravel.busy || ventRole.inVent || ventRole.walkingToVent) || !game.connected} tone="danger" onClick={() => control("vent_exit")}>退出管道/取消</Button></Inline>
        <Button disabled={busy || !canControl} onClick={() => runtime("impostorVentEscape", game.runtimeControl?.impostorVentEscape === false)}>{game.runtimeControl?.impostorVentEscape === false ? "开启击杀后优先管道撤离" : "关闭击杀后优先管道撤离"}</Button>
        <Tip>自动游玩确认击杀后，入口附近无人则优先走可达管道；到出口后连续观察随机2–15秒，无人才出管。没有可达入口或途中有人则改用地面撤离；检测异常时保持隐藏。</Tip>
        <Field label="进入/当前管道"><Select value={entryOptions.some((v: any) => String(v.id) === ventEntry) ? ventEntry : ""} onChange={value => {setVentEntry(value); setVentTarget("")}} options={[{value: "", label: "自动选择附近入口"}, ...entryOptions.map((v: any) => ({value: String(v.id), label: `${v.label}（距自身 ${v.distance ?? "—"}）`}))]} /></Field>
        <Field label="目标出口"><Select value={exitOptions.some((v: any) => String(v.id) === ventTarget) ? ventTarget : ""} onChange={setVentTarget} options={[{value: "", label: entry ? "请选择相连出口" : "请先走到可用入口旁"}, ...exitOptions.map((v: any) => ({value: String(v.id), label: v.label}))]} /></Field>
        <Button disabled={busy || !canControl || !game.connected || !game.capabilities?.vent?.available || !exitOptions.some((v: any) => String(v.id) === ventTarget) || ventTravel.busy} onClick={() => control("vent_travel", {target_vent_id: Number(ventTarget), ...(entry ? {entry_vent_id: entry.id} : {})})}>开始管道传送</Button>
        <Tip>“已提交”只代表发送请求；游戏确认到达目标并安全出管后才显示“已完成”。管道位移会单独记录，不计入步行距离；失败时插件会尝试退出，仍失败请手动接管。</Tip>
      </Stack></Card>
      <Card title="会议发言与投票"><Stack>
        <Inline><Button disabled={busy || !canControl} onClick={() => control(observer.autoVote === false ? "vote_on" : "vote_off")}>{observer.autoVote === false ? "开启自动投票决策" : "关闭自动投票决策"}</Button></Inline>
        <Text>投票决策：{observer.paused ? "监听已暂停" : observer.meetingDecision?.summary || "等待会议"} · 提醒 {observer.meetingDecision?.attempts || 0}/3 次</Text>
        <Text>本人投票：{game.voting?.hasVoted ? "游戏已记录投票" : game.voting?.state === "submitted" ? "已提交，等待游戏记录" : game.voting?.state === "failed" ? "提交失败，可重新决策" : game.voting?.state === "unconfirmed" ? "尚未获得游戏确认" : "尚未投票"}</Text>
        {game.connected && game.meetingOpen && !game.voting?.supported && <Alert tone="warning">需重启游戏加载0.6.86以读取投票阶段与名单。</Alert>}
        <Tip>进入可投票阶段并满足讨论等待条件后，提醒AI选择玩家或跳过；剩余不超过10秒时优先决策。证据不足优先跳过。每次会议最多提醒三次，不会替AI随意选人，也不会反复要求它发言。关闭聊天自动回应不影响投票决策；关闭会议控制或暂停监听可停止自动决策提醒。</Tip>
        <Text>会议策略已加载：52项玩家术语 · 区分本人目击、玩家声称与推断 · 按船员/内鬼选择话术</Text>
        <Tip>金水、银水、互保、对跳、11和跳管均需结合条件。发言保持简短，不自动复读；普通聊天不会自动发送到游戏。</Tip>
        {(game.meetingStrategy?.mentionedTerms || []).map(t => <Text key={t.term}>{t.term}：{t.meaning}</Text>)}
        {game.meetingStrategy && <details><summary>查看本次会议话术策略</summary><CodeBlock>{JSON.stringify(game.meetingStrategy, null, 2)}</CodeBlock></details>}
        <Text>AI发言入口：say / among_us_say · {game.capabilities?.meetingChat?.reason || "等待游戏观测"}</Text>
        <Tip>对AI说“使用发送会议发言，把……发到游戏会议”。AI无需面板令牌；control入口只供此面板使用。</Tip>
        <Field label="发送内容（1–100 字）"><Input value={text} onChange={setText} /></Field>
        <Button disabled={busy || !canControl || !game.meetingOpen || !text.trim() || text.length > 100} onClick={() => control("say", { text })}>发送到当前会议</Button>
        <Tip>请在会议开始前连接。按钮直接提交文字，便于区分游戏通路和模型响应问题。</Tip>
      </Stack></Card>
      <Card title="实时位置与主动观察"><Stack>
        <Text>自身坐标：{game.connected && game.location?.source === "unity_world" ? `X ${game.location.x}，Y ${game.location.y}` : "位置不可用"}</Text>
        <Text>近似区域：{game.connected && game.location?.areaEstimate ? `${game.location.areaEstimate.roomName || game.location.areaEstimate.room}（距参考点 ${game.location.areaEstimate.distance} 单位，仅为估计）` : "无法可靠判断房间"}</Text>
        <Text>最近推送给 AI：{observer.lastPushedObservation?.summary || "等待首次推送"}</Text>
        <Text>静默观测：{observer.alive && !observer.paused ? "持续更新" : "已暂停"} · 会议自动回应：{observer.autoReply === false ? "已关闭" : "已开启"} · 上次请求回复：{observer.lastReviewAge == null ? "尚无" : `${observer.lastReviewAge} 秒前`}</Text>
        <Tip>位置、可见玩家和路线变化静默更新，不强制播报坐标。会议中其他玩家发言才请求自动回复；也可随时主动询问 AI。</Tip>
      </Stack></Card>
      <Card title="玩家检测"><Stack>
        <Text>当前检测到：{(game.nearbyPlayers || []).length} 人 · 检测状态：{game.playerDetection?.status || "未知"}</Text>
        <Text>候选范围：{game.playerDetection?.detectionRadius ?? "等待遥测"} 单位 · 扫描间隔：{game.playerDetection?.scanIntervalMs ?? "等待遥测"} 毫秒 · 插件每秒采集约 10 次</Text>
        <Text>观测相机：{game.playerDetection?.cameraName || "不可用"} · 遥测：{game.connected ? "已连接" : "已过期，当前位置不可用"}</Text>
        {game.connected && !game.playerDetection?.awarenessSupported && <Alert tone="warning">近期目击保护尚未连接；请重启 Among Us 加载 0.6.80 组件。</Alert>}
        <Text>镜头每侧扩展约 {Math.round((game.playerDetection?.viewportMargin ?? 0.08) * 100)}% · 近期目击保留 {game.playerDetection?.recentMemorySeconds ?? 10} 秒 · 仍检查墙体遮挡</Text>
        <Text>近期离开检测范围：{(game.recentPlayers || []).length} 人 · 这些记录仅表示最后目击位置，不能当作隔墙实时坐标</Text>
        {(game.recentPlayers || []).map(p => <Text key={`recent-${p.playerId}`}>{p.playerName || `玩家 ${p.playerId}`} · {p.ageSeconds} 秒前看见 · 当时 X {p.x}，Y {p.y} · 位置不确定范围约 {p.uncertaintyRadius} 单位</Text>)}
        <Tip>当前或近期玩家可能位于自身或目标附近时暂缓击杀。刚转入墙后的记录保留10秒；近期目击按本次3至5秒窗口判断，照明破坏期间也保留；重新看见后以新位置为准。</Tip>
        <Text>扫描 {game.playerDetection?.scanned ?? "—"} · 超出距离 {game.playerDetection?.outsideRadius ?? "—"} · 画面外 {game.playerDetection?.outsideView ?? "—"} · 遮挡 {game.playerDetection?.occluded ?? "—"} · 无效角色 {game.playerDetection?.inactive ?? "—"} · 错误 {game.playerDetection?.errors ?? "—"}</Text>
        {(game.nearbyPlayers || []).map(p => <Text key={p.playerId}>{p.playerName || `玩家 ${p.playerId}`} · {p.colorName || p.colorKey || "颜色未知"} · 自身{p.relativeToSelf?.directionName || "附近"} · 距离 {p.relativeToSelf?.distance ?? p.distance ?? "—"} · {p.observationSource === "camera_margin" ? "镜头边缘扩展范围" : "画面内"} · X {p.x}，Y {p.y} · {p.location?.label ? `${p.location.label}附近` : "区域未标注"}</Text>)}
        {game.playerDetection?.status === "camera_unavailable" && <Alert tone="warning">没有找到能看到自身的游戏相机，本次检测不可用。</Alert>}
        <Tip>检测范围内、画面或镜头边缘扩展范围且无遮挡的真人或练习假人。名字、颜色、位置属于观测信息；阵营不会从游戏内部读取。旧探针需要更新游戏端组件后才有诊断数据。</Tip>
        <details><summary>查看玩家检测原始数据</summary><CodeBlock>{JSON.stringify({ currentPlayers: game.nearbyPlayers, recentPlayers: game.recentPlayers, diagnostics: game.playerDetection }, null, 2)}</CodeBlock></details>
      </Stack></Card>
      <Card title="本局行动记录"><Stack>
        <Text>路线记录点：{game.history?.routePointCount || 0} · 累计行走距离：{game.history?.distance || 0} · 已目击玩家：{(game.history?.lastSeenPlayers || []).length}</Text>
        <Text>记录时间：{game.history?.startedAt || "等待首次观测"} → {game.history?.updatedAt || "—"}</Text>
        <Text>记录范围：{game.history?.scope === "current_match" ? "本局" : game.history?.scope === "previous_match" ? "上一局历史，不能当成本局事实" : "尚无记录"} · 当前任务批次：{game.history?.taskBatch || 1}</Text>
        {(record.events || []).slice(-12).map(e => <Text key={e.eventId}>{e.description}</Text>)}
        <Text>本地记录目录：{game.history?.logDirectory || "尚未生成"}</Text>
        <Text>已走路线（从早到晚）：{game.history?.routeSummary?.text || "等待路线记录"}</Text>
        {(game.history?.lastSeenPlayers || []).map(item => <Text key={item.player?.playerId}>{item.player?.playerName || `玩家 ${item.player?.playerId}`} · {item.isCurrent ? "本次仍可见" : "历史目击，去向未知"} · 最后目击 {item.lastSeenAt ? new Date(item.lastSeenAt).toLocaleTimeString() : "—"}（约 {item.ageSeconds ?? "—"} 秒前）· X {item.player?.x}，Y {item.player?.y} · 累计相遇 {item.encounterCount || 1} 次</Text>)}
        {game.history?.error && <Alert tone="danger">记录失败：{game.history.error}</Alert>}
        <Tip>每局与任务批次分别标记，跨会议和插件重启保留。可对 AI 说“使用读取行动记录与目击经过，按时间详细说说做了什么、遇见谁”。时间线使用 UTC，目击行显示本机时间。路过任务点不能当作做过任务；历史方位只对应当时自身的位置。</Tip>
        <details><summary>查看行动记录原始数据</summary><CodeBlock>{JSON.stringify({ recentRoute: game.history?.route, taskProgress: game.history?.taskEvents, encounters: game.history?.encounters, lastSeenPlayers: game.history?.lastSeenPlayers }, null, 2)}</CodeBlock></details>
      </Stack></Card>
      <Card title="可见状态"><details><summary>查看当前位置、导航与聊天原始状态</summary><CodeBlock>{JSON.stringify({ self: game.self, navigation: game.navigation, chat: game.chat }, null, 2)}</CodeBlock></details></Card>
    </Stack>
  </Page>
}
