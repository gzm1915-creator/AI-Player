using System;
using AUTelemetryProbe;
using UnityEngine;

static class AwarenessTests
{
    static void Check(bool ok, string why) { if (!ok) throw new Exception("awareness: " + why); }
    static PlayerControl Player(byte id, float x) { var p = new PlayerControl { PlayerId = id }; p.transform.position = new Vector3 { x = x }; return p; }
    public static void Run()
    {
        var self = Player(0, 0); self.Data!.RoleType = RoleTypes.Impostor; self.Data.Role!.IsImpostor = true;
        var target = Player(1, 1); var witness = Player(3, 2);
        PlayerControl.LocalPlayer = self; PlayerControl.AllPlayerControls = new() { self, target, witness };
        UnityEngine.Object.ScenePlayers = Array.Empty<PlayerControl>();
        MeetingHud.Instance = null; ExileController.Instance = null; Minigame.Instance = null;
        ShipStatus.Instance = new(); GameManager.Instance = new(); RuntimeControl.AutoNavigationEnabled = true;
        Camera.allCameras = new[] { new Camera() }; PhysicsHelpers.Blocked = false; PhysicsHelpers.Occludes = null;
        PlayerAwareness.Reset(); var now = DateTimeOffset.UtcNow;
        var frame = PlayerAwareness.Scan(true, now);
        Check(frame.Players.Count == 2 && PlayerAwareness.HasWitness(frame, 1, target.GetTruePosition()), "initial witness seen");
        PhysicsHelpers.Occludes = (a, b) => b.x > 1.5f;
        witness.transform.position = new Vector3 { x = 3 };
        frame = PlayerAwareness.Scan(true, now.AddSeconds(1));
        Check(frame.Players.Count == 1 && frame.RecentPlayers.Count == 1, "wall occludes current player but retains memory");
        Check(frame.RecentPlayers[0].X == 2 && frame.RecentPlayers[0].AgeSeconds == 1, "hidden live coordinate never refreshes last sighting");
        Check(PlayerAwareness.HasWitness(frame, 1, target.GetTruePosition()), "recent corner witness blocks kill");
        HudManager.Instance = new() { KillButton = new() { currentTarget = target } };
        var command = new InternalNavigationCommand { ExpectedMatchId = TaskStartSignalPatch.SignalVersion };
        Check(ImpostorControl.Kill(command) != null && HudManager.Instance.KillButton.Clicks == 0, "direct native kill cannot bypass memory guard");
        var pursuit = new InternalNavigationCommand { ExpectedMatchId = TaskStartSignalPatch.SignalVersion };
        Check(PlayerPursuit.Validate(pursuit, true, out _) != null, "pursuit refuses recent witness");
        frame = PlayerAwareness.Scan(true, now.AddSeconds(10.1));
        Check(frame.RecentPlayers.Count == 0 && !PlayerAwareness.HasWitness(frame, 1, target.GetTruePosition()), "memory expires after ten seconds");
        PhysicsHelpers.Occludes = null;
        frame = PlayerAwareness.Scan(true, now.AddSeconds(11));
        Check(frame.RecentPlayers.Count == 0 && frame.Players.Count == 2, "reacquisition replaces historical location");
        witness.transform.position = new Vector3 { x = 100 };
        TaskStartSignalPatch.SignalVersion++;
        frame = PlayerAwareness.Scan(true, now.AddSeconds(11.1));
        Check(frame.RecentPlayers.Count == 0, "new match clears old sightings");

        // The strict native camera remains strict; only observation has an 8% margin.
        var marginal = Player(4, 10.5f); UnityEngine.Object.ScenePlayers = new[] { marginal };
        PlayerAwareness.Reset(); frame = PlayerAwareness.Scan(true);
        var edge = frame.Players.Find(p => p.Player.PlayerId == 4);
        Check(edge != null && !edge.InCameraView, "scene dummy just beyond camera is detected in margin");
        Check(!PlayerView.Contains(Camera.allCameras[0], marginal.transform.position), "native kill camera requirement unchanged");
        marginal.transform.position = new Vector3 { x = 11.5f };
        frame = PlayerAwareness.Scan(true);
        Check(!frame.Players.Exists(p => p.Player.PlayerId == 4), "beyond radial/margin limit rejected");
        PhysicsHelpers.Blocked = true; frame = PlayerAwareness.Scan(true);
        Check(frame.Players.Count == 0, "expanded viewport cannot see through wall"); PhysicsHelpers.Blocked = false;
        Camera.allCameras = Array.Empty<Camera>(); frame = PlayerAwareness.Scan(true);
        Check(frame.Diagnostics.Status == "camera_unavailable" && PlayerAwareness.HasWitness(frame, 1, target.GetTruePosition()), "camera failure is never clear area");
        Camera.allCameras = new[] { new Camera() }; UnityEngine.Object.ScenePlayers = Array.Empty<PlayerControl>();
        PlayerControl.AllPlayerControls = new() { self, target }; PlayerAwareness.Reset();
        pursuit.ExpectedMatchId = TaskStartSignalPatch.SignalVersion;
        Check(PlayerPursuit.Validate(pursuit, true, out var observed) == null && observed != null, "visible pursuit target allowed");
        pursuit.IssuedAtUtc = DateTimeOffset.UtcNow.AddSeconds(-3);
        Check(PlayerPursuit.Validate(pursuit, true, out _) != null && PlayerPursuit.Validate(pursuit, false, out _) == null, "initial expiry does not expire active chase");
        pursuit.IssuedAtUtc = DateTimeOffset.UtcNow;
        PhysicsHelpers.Blocked = true; PlayerAwareness.Scan(true);
        Check(PlayerPursuit.Validate(pursuit, false, out _) != null, "occluded target cannot be pursued using memory");
        PhysicsHelpers.Blocked = false;
        var progress = new PlayerPursuit(pursuit, 0);
        observed = new ObservedPlayer { Player = target, Position = new Vector2(1, 0), Velocity = new Vector2(2, 0) };
        Check(progress.NeedsPlan(observed, 0), "first dynamic plan"); progress.Planned(observed, 0);
        observed.Position = new Vector2(2, 0);
        Check(!progress.NeedsPlan(observed, 100) && progress.NeedsPlan(observed, 300), "moving endpoint replans at bounded cadence");
        progress.Planned(observed, 300); observed.Position = new Vector2(2.1f, 0); observed.Velocity = new Vector2(.1f, 0);
        Check(progress.NeedsPlan(observed, 450) && progress.Info.TargetSlowed, "slowdown triggers earlier opportunity refresh");
        Check(progress.Expired(45001), "chase deadline cannot be extended by replans");
        frame = PlayerAwareness.Scan(true, DateTimeOffset.UtcNow.AddSeconds(1));
        frame.RecentPlayers.Add(new RecentPlayerInfo { PlayerId=3, X=2, Y=0, AgeSeconds=4, UncertaintyRadius=2 });
        KillSafetyObservation.Current.Clear();
        KillSafetyObservation.Current.Add(new KillSafetyInfo {TargetPlayerId=1, RequiredSeconds=3.5, Ready=true});
        Check(PlayerPursuit.Validate(pursuit,false,out _) == null,
            "pursuit must use target's 3-5s witness window instead of silently waiting a fixed five seconds");
        frame.RecentPlayers[0]=new RecentPlayerInfo {PlayerId=3,X=2,Y=0,AgeSeconds=3,UncertaintyRadius=2};
        Check(PlayerPursuit.Validate(pursuit,false,out _) != null,"witness inside the target's window still prevents pursuit");
        PlayerAwareness.Reset();
        Console.WriteLine("PASS awareness/pursuit: camera margin, real wall occlusion, dummy discovery, ten-second memory/expiry, no hidden refresh, native kill guard, session reset, loss cancellation, moving/slow endpoint cadence and absolute deadline (synthetic)");
    }
}
