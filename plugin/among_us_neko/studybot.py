"""Run the preserved StudyBot adapter in an owned, cancellable subprocess."""
import ctypes
import json
import os
import subprocess
import time
from pathlib import Path
from .task_batch import TaskBatch
from .sample_task import sample_phase, sample_waiting
from .task_receipts import receipt_for

REPAIR_TASKS = {"FixLights", "RestoreOxy", "FixComms", "ResetReactor"}


class StudyBot:
    def __init__(self, bridge, settings=None):
        self.bridge = bridge
        self.settings = settings or {}
        self.enabled = False
        self.ready = False
        self.proc = None
        self.log = None
        self.active = None
        self.identity = None
        self.task_batches = TaskBatch()
        self.seen = None
        self.attempts = {}
        self.emergencies = set()
        self.deferred_tasks = []
        self.result = {"engine": "StudyBot", "state": "disabled", "reason": "尚未检查旧技能包"}

    def status(self):
        return {**self.result, "enabled": self.enabled, "ready": self.ready, "deferredTasks": self.deferred_tasks,
                "busy": self.proc is not None,
                "root": self.settings.get("studybot_root", ""),
                "python": self.settings.get("studybot_python", "")}

    def environment(self):
        env = os.environ.copy()
        state_dir = self.bridge.journal.root / "studybot"
        state_dir.mkdir(parents=True, exist_ok=True)
        env.update(AU_PLUGIN_ROOT=str(self.bridge.root), AU_NAV_COMMAND=str(self.bridge.command_path),
                   AU_NAV_STATUS=str(self.bridge.nav_path), AU_STUDYBOT_STATE_DIR=str(state_dir),
                   PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        return env

    def command(self):
        python = Path(self.settings.get("studybot_python", ""))
        root = Path(self.settings.get("studybot_root", ""))
        if not python.is_file() or not (root / "src/tasks.py").is_file():
            raise ValueError("请先设置旧 StudyBot 技能目录和 Python 路径，并检查加载")
        return [str(python), str(Path(__file__).parent / "studybot_runner/studybot_minigame_adapter.py"),
                "--root", str(root)]

    def preflight(self):
        self.ready = False
        try:
            result = subprocess.run(self.command() + ["--preflight"], env=self.environment(),
                                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                raise ValueError((result.stderr or result.stdout)[-2500:])
            lines = result.stdout.strip().splitlines()
            details = json.loads(lines[-1]) if lines else None
            if not isinstance(details, dict) or details.get("engine") != "StudyBot" or not details.get("skills"):
                raise ValueError("旧技能包检查没有返回有效的技能清单，请检查Python和原技能目录。")
            self.ready = True
            self.result = {"engine": "StudyBot", "state": "ready", "reason": "原技能包导入成功，等待任务窗口", **details}
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            self.result = {"engine": "StudyBot", "state": "blocked", "reason": str(exc)}
            raise ValueError(str(exc)) from exc
        return self.status()

    def set_enabled(self, enabled):
        if enabled:
            if self.enabled:
                return
            if self.proc is not None and not self.cancel():
                raise ValueError("旧技能进程尚未停止，暂不启动新的小游戏操作。")
            self.preflight()
            self.bridge.set_runtime("minigameControl", False)
            self.enabled = True
            self.attempts.clear()
            self.seen = None
        else:
            self.enabled = False
            if self.cancel():
                self.result.update(state="disabled", reason="旧技能已停止")

    def close_window(self):
        # Called only after our child exits, avoiding two writers to the command file.
        self.bridge.command("close_minigame")

    def cancel(self):
        owned = self.proc is not None
        if self.proc is not None:
            try:
                if self.proc.poll() is None:
                    self.proc.terminate()
                    try:
                        self.proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        self.proc.kill()
                        self.proc.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired) as exc:
                if self.proc.poll() is None:
                    self.result.update(state="blocked", reason="旧技能进程尚未停止，将继续重试取消：" + str(exc))
                    return False
            self.proc = None
        if self.log:
            self.log.close()
            self.log = None
        if owned:
            self.release_mouse()
        self.active = None
        return True

    @staticmethod
    def release_mouse():
        if os.name == "nt":
            ctypes.windll.user32.mouse_event(0x0004 | 0x0010, 0, 0, 0, 0)

    def tick(self, state):
        self.deferred_tasks = [{"taskId": t.get("taskId"), "taskType": "InspectSample",
                                "remainingSeconds": t["sample"].get("remainingSeconds"),
                                "reason": "培养中，可离开做其他任务；游戏确认就绪后再返回"}
                               for t in state.get("tasks", []) if state.get("connected") and sample_waiting(t)]
        if not self.enabled:
            if self.proc is not None:
                self.cancel()
            return
        if not state.get("connected") or state.get("meetingOpen") or state.get("phase") != "TASKS":
            self.cancel()
            self.seen = None
            return
        identity = self.task_batches.observe(state)
        if identity != self.identity:
            completed = receipt_for(state, self.active) if self.active else None
            previous = self.active
            if not self.cancel():
                return
            self.identity, self.seen, self.attempts = identity, None, {}
            self.emergencies = set()
            self.result.update(state="ready", reason="当前任务批次已更新，等待任务窗口", taskId=None,
                               taskType=None, initialStep=0, attempts=0, verifiedStep=None,
                               navigationCommandId=None)
            if completed:
                self.result.update(state="completed", reason="游戏原生回执确认上一批任务完成；新任务批次独立处理",
                                   taskId=previous["taskId"], taskType=previous["taskType"],
                                   initialStep=previous["step"], verifiedStep=completed["task"].get("step"),
                                   completionReceiptId=completed["receiptId"])
                return
        observed = state.get("activeEmergencies")
        if isinstance(observed, list):
            current = set(observed)
            for cleared_type in self.emergencies - current:
                self.attempts.pop(("repair:" + cleared_type, 0), None)
            self.emergencies = current
        if (state.get("ownRole") or {}).get("isImpostor"):
            if self.cancel():
                self.result.update(state="waiting", reason="内鬼使用假任务掩护，不执行真实任务或修复技能")
            return
        if state.get("runtimeControl", {}).get("minigameControl"):
            self.bridge.set_runtime("minigameControl", False)
            self.result.update(state="waiting", reason="正在关闭替代执行器，避免输入争用")
            return
        now = time.monotonic()
        if self.active:
            task = next((t for t in state.get("tasks", []) if t.get("taskId") == self.active["taskId"]), None)
            completed = receipt_for(state, self.active)
            if completed:
                task = completed["task"]
            repair = self.active.get("repairType")
            cleared = repair is not None and isinstance(state.get("activeEmergencies"), list) and repair not in state["activeEmergencies"]
            progressed = task is not None and (task.get("complete") or task.get("step", 0) > self.active["step"])
            finished = task is not None and (task.get("complete") or
                        (task.get("maxStep", 0) > 0 and task.get("step", 0) >= task["maxStep"]))
            # A hit/round is progress, not permission to interrupt a live skill.
            # Multi-location tasks may finish one stage when the skill exits;
            # asteroids must finish the entire counter, even if the skill exits.
            verified = cleared if repair else progressed and (task.get("taskType") != "ClearAsteroids" or finished)
            if task and task.get("taskType") == "InspectSample":
                verified = bool(task.get("complete"))
                finished = verified
            window_changed = ((state.get("openMinigame") or {}).get("instanceId") != self.active["instanceId"]
                              or not state.get("minigameOpen"))
            if window_changed and not verified:
                if self.cancel():
                    self.seen = None
                    self.result.update(state="cancelled", reason="任务窗口已关闭或切换，停止旧技能，保留当前新窗口")
                return
            if task and sample_waiting(task):
                if not self.cancel():
                    return
                self.result.update(state="deferred", reason="游戏确认样本培养已启动，关闭面板离开；就绪后再返回", taskId=task["taskId"])
                self.close_window()
                try:
                    self.bridge.journal.record_action(state, "task_culture_started", task=task,
                        evidence="native_own_task_timer", remainingSeconds=task["sample"].get("remainingSeconds"))
                except (OSError, ValueError):
                    pass
                return
            if repair:
                finished = cleared
            exited = self.proc.poll() is not None
            timed_out = now - self.active["started"] > self.active["timeout"]
            self.result.update(verifiedStep=task.get("step") if task else None)
            if finished or exited or timed_out or window_changed:
                if not cleared and not progressed and not timed_out and now - self.active["started"] < 2:
                    return
                task_id, step = self.active["taskId"], self.active["step"]
                if not self.cancel():
                    return
                self.result.update(state="completed" if verified else "failed",
                                   reason=("游戏确认破坏已修复" if repair else "游戏任务完成或当前阶段已结束") if verified else "旧技能结束或超时，任务尚未完成；已关闭窗口等待重试",
                                   taskId=task_id, initialStep=step,
                                   verifiedStep=task.get("step") if task else None)
                if repair and verified:
                    self.bridge.journal.record_action(state, "repair_completed", repairType=repair,
                                                      evidence="native_system_cleared")
                if state.get("minigameOpen") and not window_changed:
                    self.close_window()
            return
        if not state.get("minigameOpen"):
            self.seen = None
            return
        opened = state.get("openMinigame", {})
        instance = opened.get("instanceId")
        repair_type = opened.get("taskType") if opened.get("taskType") in REPAIR_TASKS else None
        if instance is None or (opened.get("taskId") is None and not repair_type):
            self.result.update(state="blocked", reason="需要游戏端 0.6.70 的当前任务窗口标识，拒绝猜测技能")
            return
        if self.seen == instance:
            return
        task = next((t for t in state.get("tasks", []) if t.get("taskId") == opened.get("taskId")), None)
        if repair_type:
            if not isinstance(state.get("activeEmergencies"), list) or repair_type not in state["activeEmergencies"]:
                self.result.update(state="blocked", reason="尚未确认此破坏正在发生，拒绝猜测修复结果")
                return
            # Window type is stronger evidence than a reused/missing task ID.
            task = {"taskId": opened.get("taskId"), "taskType": repair_type, "step": 0}
        elif task and task.get("taskType") in REPAIR_TASKS:
            repair_type = task["taskType"]
            if not isinstance(state.get("activeEmergencies"), list) or repair_type not in state["activeEmergencies"]:
                self.result.update(state="blocked", reason="需要新版游戏端确认破坏状态")
                return
        if not task or task.get("complete"):
            return
        self.seen = instance
        phase = sample_phase(task)
        if task.get("taskType") == "InspectSample":
            if phase not in ("not_started", "ready"):
                self.result.update(state="deferred" if phase == "processing" else "unsupported",
                    reason="样本仍在培养，关闭面板并继续其他任务" if phase == "processing" else "需加载0.6.87以读取样本培养阶段；不会原地等候",
                    taskId=task["taskId"], taskType="InspectSample", attempts=0)
                self.close_window()
                return
        key = ("repair:" + repair_type, 0) if repair_type else (task["taskId"], task.get("step", 0))
        if task.get("taskType") == "InspectSample":
            key = (*key, phase)  # Starting culture and selecting the result have independent retry limits.
        attempt = self.attempts.get(key, 0) + 1
        self.attempts[key] = attempt
        self.result.update(taskId=key[0], taskType=task.get("taskType"), initialStep=key[1], attempts=attempt)
        if attempt > 3:
            self.result.update(state="failed", reason="同一任务阶段已失败三次，停止该任务")
            self.close_window()
            return
        cmd = self.command() + ["--task-type", task["taskType"], "--step", str(key[1]),
                                "--start-at", str(task.get("startAt", ""))]
        if task["taskId"] is not None:
            cmd += ["--task-id", str(task["taskId"])]
        log_path = self.bridge.journal.root / "studybot/adapter-child.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log = log_path.open("a", encoding="utf-8")
        try:
            self.proc = subprocess.Popen(cmd, env=self.environment(), stdout=self.log, stderr=subprocess.STDOUT,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            self.cancel()
            self.result.update(state="failed", reason=str(exc))
            self.close_window()
            return
        timeout = {"InspectSample": 8 if phase == "not_started" else 12, "StartReactor": 75, "UnlockManifolds": 65, "ResetReactor": 60,
                   "SubmitScan": 30, "ClearAsteroids": 45}.get(task["taskType"], 35)
        self.active = {"taskId": task["taskId"], "step": key[1], "started": now, "timeout": timeout, "repairType": repair_type,
                       "instanceId": instance, "taskInstanceId": task.get("taskInstanceId"), "taskType": task["taskType"]}
        self.result.update(state="running", reason="原 StudyBot 技能执行中；等待实际任务进度", logPath=str(log_path),
                           navigationCommandId=state.get("navigation", {}).get("commandId"))
