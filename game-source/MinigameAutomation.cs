using System;
using System.Diagnostics;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using UnityEngine;

namespace AUTelemetryProbe;

// Operates the open puzzle's controls. Task steps are only read for verification.
internal static class MinigameAutomation
{
    internal static MinigameAutomationStatus Status { get; private set; } = new();
    private static PlayerTask? _task;
    private static int _instance;
    private static int _initialStep;
    private static bool _initialComplete;
    private static long _openedAt;
    private static long _nextAction;
    private static int _actions;
    private static int _wireIndex;
    private static bool _held;
    private static long _dragStarted;
    private static int _dragMs;
    private static int _holdMs;
    private static Vector3 _dragFrom;
    private static Vector3 _dragTo;
    private static long _focusPausedAt;
    private static long _completedAt;
    private static bool _closeSent;
    private static long _failedAt;
    private static bool _forceCloseSent;
    private static long _round;
    private static string _inputError = "无法定位游戏内的拖动控件";
    private static readonly Dictionary<(uint, int), int> Attempts = new();

    internal static void Tick(long now)
    {
        try
        {
            if (!RuntimeControl.MinigameControlEnabled)
            {
                ReleaseMouse();
                _instance = 0;
                Attempts.Clear();
                Set("disabled", "小游戏操作未开启");
                return;
            }
            if (MeetingHud.Instance != null || PlayerControl.LocalPlayer?.Data == null ||
                PlayerControl.LocalPlayer.Data.IsDead || GameManager.Instance?.GameHasStarted != true)
            {
                ReleaseMouse();
                _instance = 0;
                Set("waiting", "等待任务阶段的本地角色");
                return;
            }
            var game = Minigame.Instance;
            if (_round != TaskStartSignalPatch.SignalVersion)
            {
                _round = TaskStartSignalPatch.SignalVersion;
                Attempts.Clear();
            }
            if (game == null)
            {
                ReleaseMouse();
                if (_instance != 0 && Status.State is not ("completed" or "failed" or "unsupported"))
                    Set(Progressed() ? "completed" : "closed_without_progress", "窗口已关闭；以任务进度确认结果");
                _instance = 0;
                return;
            }
            if (_instance != game.GetInstanceID())
            {
                ReleaseMouse();
                _instance = game.GetInstanceID();
                _task = game.MyTask;
                _initialStep = _task?.TaskStep ?? -1;
                _initialComplete = _task?.IsComplete ?? false;
                _openedAt = now;
                _nextAction = now + 850;
                _focusPausedAt = 0;
                _actions = _wireIndex = 0;
                _completedAt = 0;
                _closeSent = false;
                _forceCloseSent = false;
                _failedAt = 0;
                var key = (_task?.Id ?? uint.MaxValue, _initialStep);
                var attempts = Attempts.TryGetValue(key, out var previousAttempts) ? previousAttempts + 1 : 1;
                Attempts[key] = attempts;
                Status = new MinigameAutomationStatus { State = "opening", Reason = "等待任务窗口展开",
                    TaskId = _task?.Id, TaskType = _task?.TaskType.ToString(), InitialStep = _initialStep, Attempts = attempts };
                if (attempts > 3) Set("failed", "同一任务阶段已重试3次，停止该任务的自动操作");
            }
            if (Progressed())
            {
                ReleaseMouse();
                if (_completedAt == 0) _completedAt = now;
                Status.VerifiedStep = _task?.TaskStep;
                Set("completed", "已观测到任务进度增加或完成");
                if (!_closeSent && now - _completedAt > 600) { game.Close(); _closeSent = true; }
                return;
            }
            if (Status.State is "failed" or "unsupported")
            {
                ReleaseMouse();
                if (_failedAt == 0) _failedAt = now;
                if (!_closeSent && now - _failedAt >= 700)
                { _closeSent = true; game.Close(); }
                if (!_forceCloseSent && now - _failedAt >= 2500)
                { _forceCloseSent = true; game.ForceClose(); }
                return;
            }
            if (_task == null || _initialComplete)
            {
                Set("unsupported", "当前窗口不是可验证的未完成本地任务");
                return;
            }
            // Pointer gestures must never affect another foreground application.
            if (_focusPausedAt != 0)
            {
                if (!GameIsForeground()) return;
                _openedAt += now - _focusPausedAt;
                _focusPausedAt = 0;
                _nextAction = now + 250;
            }
            if (now - _openedAt > 30_000)
            {
                ReleaseMouse();
                Set("failed", "小游戏操作超时，未确认进度；请手动处理或关闭窗口");
                return;
            }
            if (_held)
            {
                if (!GameIsForeground()) { PauseForFocus(now); return; }
                var t = Mathf.Clamp01((float)(now - _dragStarted) / _dragMs);
                if (!MoveMouse(Vector3.Lerp(_dragFrom, _dragTo, t)))
                {
                    ReleaseMouse();
                    Set("failed", _inputError);
                    return;
                }
                if (now - _dragStarted >= _dragMs + _holdMs)
                {
                    ReleaseMouse();
                    _nextAction = now + 500;
                }
                return;
            }
            if (now < _nextAction || game.amOpening) return;
            Set("solving", "正在操作任务控件并等待真实进度");

            var shields = game.TryCast<ShieldMinigame>();
            if (shields != null)
            {
                var buttons = shields.Shields;
                for (var i = 0; buttons != null && i < buttons.Length; i++)
                {
                    var button = buttons[i];
                    if (button != null && ColorDistance(button.color, shields.OffColor) < ColorDistance(button.color, shields.OnColor))
                    { shields.ToggleShield(i); Acted(now); return; }
                }
                _nextAction = now + 250;
                return;
            }
            var accept = game.TryCast<AcceptDivertPowerGame>();
            if (accept != null)
            {
                if (!accept.done && _actions == 0) { accept.DoSwitch(); Acted(now); }
                return;
            }
            var upload = game.TryCast<UploadDataGame>();
            if (upload != null)
            {
                if (!upload.running && _actions == 0) { upload.Click(); Acted(now); }
                return;
            }
            var garbage = game.TryCast<EmptyGarbageMinigame>();
            if (garbage != null && garbage.Handle != null)
            {
                var handle = garbage.Handle.transform;
                var target = handle.localPosition;
                target.y = garbage.HandleRange.min;
                BeginDrag(garbage.Handle.bounds.center, handle.parent.TransformPoint(target), 450, 4000, now);
                return;
            }
            var power = game.TryCast<DivertPowerMinigame>();
            if (power != null)
            {
                var task = _task.TryCast<DivertPowerTask>();
                var sliders = power.Sliders;
                var order = power.SliderOrder;
                for (var i = 0; task != null && sliders != null && order != null && i < Math.Min(sliders.Length, order.Length); i++)
                {
                    if (order[i] != task.TargetSystem || sliders[i] == null) continue;
                    var slider = sliders[i];
                    var target = slider.transform.localPosition;
                    target.y = power.SliderY.max;
                    BeginDrag(slider.bounds.center, slider.transform.parent.TransformPoint(target), 650, 350, now);
                    return;
                }
                Set("failed", "无法匹配电力任务对应的滑杆");
                return;
            }
            var wires = game.TryCast<WireMinigame>();
            if (wires != null)
            {
                var left = wires.LeftNodes;
                var right = wires.RightNodes;
                if (left == null || right == null || left.Length == 0) { Set("failed", "连线控件不可用"); return; }
                var wire = left[_wireIndex % left.Length];
                for (var i = 0; wire != null && i < right.Length; i++)
                {
                    if (right[i] == null || right[i].WireId != wire.WireId) continue;
                    // IDs label the visible colours; never write ExpectedWires/ActualWires.
                    if (BeginDrag(wire.hitbox.bounds.center, right[i].hitbox.bounds.center, 550, 150, now)) _wireIndex++;
                    return;
                }
                Set("failed", "无法匹配相同颜色的接线端点");
                return;
            }
            var unlock = game.TryCast<UnlockManifoldsMinigame>();
            if (unlock != null)
            {
                if (!unlock.animating && unlock.buttonCounter < 10)
                { unlock.HitButton(unlock.buttonCounter); Acted(now); }
                return;
            }
            var vent = game.TryCast<VentCleaningMinigame>();
            if (vent != null)
            {
                if (!vent.ventOpen) { vent.OpenVent(); Acted(now); return; }
                var dirt = vent.dirtPool?.activeChildren;
                if (dirt != null && dirt.Count > 0)
                {
                    var item = dirt[0]?.TryCast<VentDirt>();
                    if (item != null) { vent.CleanUp(item); Acted(now); }
                }
                return;
            }
            Set("unsupported", "当前小游戏尚未支持自动操作，可手动完成；不会伪造任务完成");
        }
        catch (Exception exc)
        {
            ReleaseMouse();
            Set("failed", exc.Message);
            TelemetryProbePlugin.LogSource?.LogWarning($"Minigame automation: {exc.Message}");
        }
    }

    private static bool Progressed() => _task != null &&
        (_task.TaskStep > _initialStep || (!_initialComplete && _task.IsComplete));
    private static float ColorDistance(Color a, Color b) =>
        (a.r-b.r)*(a.r-b.r) + (a.g-b.g)*(a.g-b.g) + (a.b-b.b)*(a.b-b.b);
    private static void Set(string state, string reason) { Status.State = state; Status.Reason = reason; }
    private static void Acted(long now) { Status.Actions = ++_actions; _nextAction = now + 350; }
    private static void PauseForFocus(long now)
    {
        ReleaseMouse();
        _focusPausedAt = now;
        Set("waiting_for_focus", "拖动操作需要游戏保持前台，请切回 Among Us");
    }
    private static bool BeginDrag(Vector3 from, Vector3 to, int durationMs, int holdMs, long now)
    {
        if (!GameIsForeground()) { PauseForFocus(now); return false; }
        if (!MoveMouse(from)) { Set("failed", _inputError); return false; }
        mouse_event(0x0002, 0, 0, 0, UIntPtr.Zero);
        _held = true;
        _dragFrom = from;
        _dragTo = to;
        _dragStarted = now;
        _dragMs = durationMs;
        _holdMs = holdMs;
        Acted(now);
        return true;
    }
    internal static void ReleaseMouse()
    {
        if (_held) mouse_event(0x0004, 0, 0, 0, UIntPtr.Zero);
        _held = false;
    }
    private static bool GameIsForeground()
    {
        GetWindowThreadProcessId(GetForegroundWindow(), out var pid);
        return pid == (uint)Process.GetCurrentProcess().Id;
    }
    private static bool MoveMouse(Vector3 world)
    {
        var window = GetForegroundWindow();
        _inputError = "游戏不在前台或窗口坐标不可用";
        if (!GameIsForeground() || !GetClientRect(window, out var rect)) return false;
        var layer = Minigame.Instance?.gameObject.layer ?? 0;
        if (!TryProjectControl(world, layer, out var screen))
        { _inputError = "没有能看见任务控件的相机；详见输入诊断"; return false; }
        var point = new Point { X = (int)(screen.x / Screen.width * (rect.Right-rect.Left)),
            Y = (int)((1-screen.y / Screen.height) * (rect.Bottom-rect.Top)) };
        if (!ClientToScreen(window, ref point)) return false;
        // A topmost companion can cover the mouse target without changing the
        // foreground window. Test the actual hit window before sending input.
        GetWindowThreadProcessId(WindowFromPoint(point), out var hitPid);
        if (hitPid != (uint)Process.GetCurrentProcess().Id)
        {
            _inputError = "任务控件被其它窗口遮挡，请隐藏 N.E.K.O. 浮窗或开启鼠标穿透后重试";
            Status.InputDiagnostics += $"; hitWindowProcessId={hitPid}";
            return false;
        }
        return SetCursorPos(point.X, point.Y);
    }

    internal static bool TryProjectControl(Vector3 world, int layer, out Vector3 screen)
    {
        screen = default;
        Camera? selected = null;
        var diagnostics = new List<string>();
        foreach (var camera in Camera.allCameras)
        {
            if (camera == null || !camera.enabled) continue;
            var projected = camera.WorldToScreenPoint(world);
            var rendersLayer = (camera.cullingMask & (1 << layer)) != 0;
            diagnostics.Add($"{camera.name}:({projected.x:F1},{projected.y:F1},{projected.z:F1}),layer={rendersLayer}");
            if (!rendersLayer || projected.z < camera.nearClipPlane || projected.z > camera.farClipPlane ||
                projected.x < 0 || projected.x >= Screen.width || projected.y < 0 || projected.y >= Screen.height)
                continue;
            if (selected != null && selected.depth >= camera.depth) continue;
            selected = camera;
            screen = projected;
        }
        Status.InputDiagnostics = $"selected={selected?.name ?? "none"}; layer={layer}; " +
            $"surface={Screen.width}x{Screen.height}; " + string.Join("; ", diagnostics);
        return selected != null;
    }
    [StructLayout(LayoutKind.Sequential)] private struct Point { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)] private struct Rect { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern IntPtr WindowFromPoint(Point point);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
    [DllImport("user32.dll")] private static extern bool GetClientRect(IntPtr window, out Rect rect);
    [DllImport("user32.dll")] private static extern bool ClientToScreen(IntPtr window, ref Point point);
    [DllImport("user32.dll")] private static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] private static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);
}

internal sealed class MinigameAutomationStatus
{
    public string State { get; set; } = "disabled";
    public string Reason { get; set; } = "小游戏操作未开启";
    public uint? TaskId { get; init; }
    public string? TaskType { get; init; }
    public int InitialStep { get; init; }
    public int? VerifiedStep { get; set; }
    public int Actions { get; set; }
    public int Attempts { get; init; }
    public string? InputDiagnostics { get; set; }
}
