using System;
using AUTelemetryProbe;
using UnityEngine;

static class BlackoutSafetyTests
{
    static void Check(bool ok, string message) { if (!ok) throw new Exception(message); }
    public static void Run()
    {
        TaskStartSignalPatch.SignalVersion++;
        var self = new PlayerControl { PlayerId=0 };
        self.Data!.RoleType=RoleTypes.Impostor; self.Data.Role!.IsImpostor=true; self.Data.Role.CanUseKillButton=true;
        var target = new PlayerControl { PlayerId=1 }; target.transform.position=new Vector3 {x=1};
        PlayerControl.LocalPlayer=self; PlayerControl.AllPlayerControls=new() {self,target};
        Camera.allCameras=new[] {new Camera()}; UnityEngine.Object.ScenePlayers=Array.Empty<PlayerControl>();
        MeetingHud.Instance=null; ExileController.Instance=null; Minigame.Instance=null;
        GameManager.Instance=new(); ShipStatus.Instance=new(); RuntimeControl.AutoNavigationEnabled=true;
        PhysicsHelpers.Blocked=false; PhysicsHelpers.Occludes=null; PlayerAwareness.Reset();
        var lights = new SwitchSystem {IsActive=false};
        ShipStatus.Instance.Systems[SystemTypes.Electrical]=lights;
        ShipStatus.Instance.Systems[SystemTypes.Comms]=new HudOverrideSystemType {IsActive=true};
        HudManager.Instance=new() {KillButton=new() {currentTarget=target}};
        var start=DateTimeOffset.UtcNow;
        var command=new InternalNavigationCommand {ExpectedMatchId=TaskStartSignalPatch.SignalVersion,IssuedAtUtc=start};
        PlayerAwareness.Scan(true,start);
        Check(!KillSafetyObservation.Current[0].Ready && !KillSafetyObservation.Current[0].LightsWaitBypassed,
            "comms or a merely submitted sabotage must not remove the normal countdown");
        lights.IsActive=true; PlayerAwareness.Scan(true,start.AddSeconds(.1));
        var safety=KillSafetyObservation.Current[0];
        Check(safety.Ready && safety.LightsWaitBypassed && safety.RequiredSeconds==0 && safety.WitnessMemorySeconds>=3,
            "confirmed active lights bypasses only the added countdown");
        Check(ImpostorControl.Kill(command,start.AddSeconds(.11))==null && HudManager.Instance.KillButton.Clicks==1,
            "native kill can act during blackout without waiting 3-5 seconds");
        lights.IsActive=false;
        Check(ImpostorControl.Kill(command,start.AddSeconds(.12))!=null && HudManager.Instance.KillButton.Clicks==1,
            "repair between Python decision and native click invalidates the bypass");
        lights.IsActive=true;
        var witness=new PlayerControl {PlayerId=3}; witness.transform.position=new Vector3 {x=2};
        PlayerControl.AllPlayerControls.Add(witness); PlayerAwareness.Scan(true,start.AddSeconds(.2));
        Check(!KillSafetyObservation.Current[0].Ready && ImpostorControl.Kill(command,start.AddSeconds(.21))!=null,
            "a current witness still blocks native blackout kill");
        PhysicsHelpers.Occludes=(a,b)=>b.x>1.5f;
        PlayerAwareness.Scan(true,start.AddSeconds(.3));
        Check(!KillSafetyObservation.Current[0].Ready && PlayerAwareness.Current.RecentPlayers.Count==1,
            "zero countdown must not erase the independent recent-witness window");
        for(int i=4;i<=65;i++) PlayerAwareness.Scan(true,start.AddSeconds(i*.1));
        Check(KillSafetyObservation.Current[0].Ready,"blackout opportunity resumes once the witness risk expires");
        lights.IsActive=false; PlayerAwareness.Scan(true,start.AddSeconds(6.6));
        Check(!KillSafetyObservation.Current[0].Ready && KillSafetyObservation.Current[0].RequiredSeconds>=3,
            "repair starts a fresh ordinary observation period instead of counting blackout time");
        lights.IsActive=true; ShipStatus.Instance.Type="HQ";
        PlayerAwareness.Scan(true,start.AddSeconds(6.7));
        Check(!KillSafetyObservation.Current[0].LightsWaitBypassed,"unknown map never implies active Skeld lights");
        ShipStatus.Instance.Type="Ship"; lights.IsActive=false; PhysicsHelpers.Occludes=null;
        PlayerAwareness.Reset();
        Console.WriteLine("PASS blackout safety: actual active system, immediate native range check, repair race, current/recent witness protection and fresh countdown after repair (synthetic)");
    }
}
