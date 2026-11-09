using System;
using System.Collections.Generic;
using AUTelemetryProbe;
using UnityEngine;

static class SafetyCrimeTests
{
    static void Check(bool ok, string reason) { if (!ok) throw new Exception(reason); }
    public static void Run()
    {
        TaskStartSignalPatch.SignalVersion++;
        var local = new PlayerControl { PlayerId = 0 };
        local.Data!.RoleType = RoleTypes.Impostor; local.Data.Role!.IsImpostor = true;
        var target = new PlayerControl { PlayerId = 1 }; target.transform.position = new Vector3 { x = 1 };
        PlayerControl.LocalPlayer = local; PlayerControl.AllPlayerControls = new() { local, target };
        Camera.allCameras = new[] { new Camera() }; UnityEngine.Object.ScenePlayers = Array.Empty<PlayerControl>();
        MeetingHud.Instance = null; ExileController.Instance = null; Minigame.Instance = null;
        GameManager.Instance = new(); ShipStatus.Instance = new(); PhysicsHelpers.Blocked = false; PhysicsHelpers.Occludes = null;
        RuntimeControl.AutoNavigationEnabled = true; PlayerAwareness.Reset();
        var start = DateTimeOffset.UtcNow;
        PlayerAwareness.Scan(true, start);
        HudManager.Instance = new() { KillButton = new() { currentTarget = target } };
        var command = new InternalNavigationCommand { ExpectedMatchId = TaskStartSignalPatch.SignalVersion, IssuedAtUtc = start };
        Check(ImpostorControl.Kill(command, start) != null && HudManager.Instance.KillButton.Clicks == 0, "native direct kill cannot bypass observation timer");
        var required = KillSafetyObservation.Current[0].RequiredSeconds;
        Check(required >= 3 && required <= 5 && !KillSafetyObservation.Current[0].Ready, "3-5 second random safety window, never immediately ready");
        for (int i = 1; i <= 52; i++) PlayerAwareness.Scan(true, start.AddSeconds(i * .1));
        Check(KillSafetyObservation.Current[0].Ready, "ready within five seconds of continuous clear observations");
        var witness = new PlayerControl { PlayerId = 3 }; witness.transform.position = new Vector3 { x = 2 };
        PlayerControl.AllPlayerControls.Add(witness); PlayerAwareness.Scan(true, start.AddSeconds(5.3));
        Check(!KillSafetyObservation.Current[0].Ready, "new witness resets safe observation");
        PhysicsHelpers.Occludes = (a, b) => b.x > 1.5;
        for (int i = 54; i <= 105; i++) PlayerAwareness.Scan(true, start.AddSeconds(i * .1));
        Check(KillSafetyObservation.Current[0].Ready && PlayerAwareness.Current.RecentPlayers.Count == 1,
            "corner witness risk ends in 3-5 seconds while ten-second historical memory remains");
        PlayerAwareness.Scan(true, start.AddSeconds(11.5));
        Check(!KillSafetyObservation.Current[0].Ready, "observation gap invalidates timer");
        Camera.allCameras = Array.Empty<Camera>(); PlayerAwareness.Scan(true, start.AddSeconds(11.6));
        Check(KillSafetyObservation.Current.Count == 0, "missing camera clears readiness");
        Camera.allCameras = new[] { new Camera() }; PhysicsHelpers.Occludes = null;

        Check(CrimeSceneMemory.Capture().Count == 0, "new match crime memory empty");
        CrimeSceneMemory.Remember(local, target);
        Check(CrimeSceneMemory.Capture().Count == 0, "live target is never a confirmed crime");
        target.Data!.IsDead = true; CrimeSceneMemory.Remember(witness, target);
        Check(CrimeSceneMemory.Capture().Count == 0, "other player's kill is not self history");
        CrimeSceneMemory.Remember(local, target); CrimeSceneMemory.Remember(local, target);
        Check(CrimeSceneMemory.Capture().Count == 1, "local kill remembered once");
        MeetingHud.Instance = new(); PlayerAwareness.Reset();
        Check(CrimeSceneMemory.Capture().Count == 1, "meeting or observation reset retains crime scene");
        MeetingHud.Instance = null;
        Check(CrimeSceneMemory.Allows(new Vector2(1,0),new Vector2(5,0)), "outward escape permitted");
        Check(!CrimeSceneMemory.Allows(new Vector2(5,0),new Vector2(1,0)), "automatic return forbidden");
        var second = new PlayerControl { PlayerId = 4 }; second.Data!.IsDead = true; second.transform.position = new Vector3 { x = 10 };
        CrimeSceneMemory.Remember(local, second);
        Check(CrimeSceneMemory.Capture().Count == 2, "multiple own kill locations remembered");
        Check(!CrimeSceneMemory.Allows(new Vector2(5,0),new Vector2(14,0)), "crossing a previous scene is forbidden even with safe endpoint");
        TaskStartSignalPatch.SignalVersion++;
        Check(CrimeSceneMemory.Capture().Count == 0, "new match clears crime scene memory");
        Console.WriteLine("PASS 3-5s continuous safety observation/reset/gap/corner memory and persistent per-match own-kill avoidance (synthetic)");
    }
}
