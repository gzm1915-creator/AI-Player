using System;
using AUTelemetryProbe;
using UnityEngine;

static void Check(bool ok,string message) {if(!ok)throw new Exception(message);}
static PlayerControl Player(byte id,float x=0) {
 var p=new PlayerControl {PlayerId=id};p.transform.position=new Vector3{x=x};return p;
}
var self=Player(0);self.Data!.RoleType=RoleTypes.Impostor;self.Data.Role!.IsImpostor=true;self.Data.Role.CanUseKillButton=true;
var victim=Player(1,1);var witness=Player(2,2);
PlayerControl.LocalPlayer=self;PlayerControl.AllPlayerControls=new(){self,victim};
ShipStatus.Instance=new();HudManager.Instance=new(){KillButton=new(){currentTarget=victim}};
var button=HudManager.Instance.KillButton!;
var command=new InternalNavigationCommand();
var killAt = DateTimeOffset.UtcNow.AddSeconds(6);
for (int i=0;i<=60;i++) PlayerAwareness.Scan(true,killAt.AddSeconds(-6+i*.1));
command.IssuedAtUtc=killAt;
Check(ImpostorControl.Kill(command,killAt)==null && button.Clicks==1,"legal native button click");
Check(ImpostorControl.LastAction.State=="submitted","submitted is not success");
ImpostorControl.Confirm(self,victim);
Check(ImpostorControl.LastAction.State=="submitted","live target cannot confirm");
victim.Data!.IsDead=true;ImpostorControl.Confirm(witness,victim);
Check(ImpostorControl.LastAction.State=="submitted","other killer cannot confirm");
ImpostorControl.Confirm(self,victim);
Check(ImpostorControl.LastAction.State=="completed" && ImpostorControl.LastAction.Position!.X==1,"local death confirmation");
victim.Data.IsDead=false;
var checks=0;
void Rejected(Action before,Action after,string label) {
 PlayerAwareness.Reset();before();command.IssuedAtUtc=DateTimeOffset.UtcNow;
 var error=ImpostorControl.Kill(command);after();
 Check(error!=null && button.Clicks==1,label);checks++;
}
Rejected(()=>command.ExpectedSessionId="old",()=>command.ExpectedSessionId="current-session","cross-session");
Rejected(()=>command.ExpectedMatchId=0,()=>command.ExpectedMatchId=1,"cross-match");
command.IssuedAtUtc=DateTimeOffset.UtcNow.AddSeconds(-3);
Check(ImpostorControl.Kill(command)!=null,"expiry");checks++;command.IssuedAtUtc=DateTimeOffset.UtcNow;
Rejected(()=>self.Data.RoleType=RoleTypes.Shapeshifter,()=>self.Data.RoleType=RoleTypes.Impostor,"special role");
Rejected(()=>self.killTimer=3,()=>self.killTimer=0,"cooldown");
Rejected(()=>MeetingHud.Instance=new(),()=>MeetingHud.Instance=null,"meeting");
Rejected(()=>Minigame.Instance=new(),()=>Minigame.Instance=null,"minigame");
Rejected(()=>self.Data.IsDead=true,()=>self.Data.IsDead=false,"death");
Rejected(()=>GameManager.Instance!.GameHasStarted=false,()=>GameManager.Instance!.GameHasStarted=true,"game ended");
Rejected(()=>RuntimeControl.AutoNavigationEnabled=false,()=>RuntimeControl.AutoNavigationEnabled=true,"runtime disabled");
Rejected(()=>button.currentTarget=witness,()=>button.currentTarget=victim,"wrong native target");
Rejected(()=>PhysicsHelpers.Blocked=true,()=>PhysicsHelpers.Blocked=false,"wall");
Rejected(()=>PlayerControl.AllPlayerControls.Add(witness),()=>PlayerControl.AllPlayerControls.Remove(witness),"visible witness");
Rejected(()=>victim.transform.position=new Vector3{x=25},()=>victim.transform.position=new Vector3{x=1},"offscreen");
Rejected(()=>victim.Data.Role!.IsImpostor=true,()=>victim.Data.Role!.IsImpostor=false,"teammate");
Check(EmergencyObservation.WindowTaskType(new SwitchMinigame())=="FixLights","lights identity");
Check(EmergencyObservation.WindowTaskType(new KeypadGame())=="RestoreOxy","oxygen identity");
Check(EmergencyObservation.WindowTaskType(new TuneRadioMinigame())=="FixComms","comms identity");
Check(EmergencyObservation.WindowTaskType(new ReactorMinigame())=="ResetReactor","reactor identity");
Check(EmergencyObservation.WindowTaskType(new Minigame())==null,"unknown window refused");
Check(EmergencyObservation.Active()!.Count==0,"available clear systems");
ShipStatus.Instance.Systems[SystemTypes.Electrical]=new SwitchSystem{IsActive=true};
Check(EmergencyObservation.Active()!.Contains("FixLights"),"native emergency");
ShipStatus.Instance.Type="HQ";Check(EmergencyObservation.Active()==null,"unsupported map unavailable");
ShipStatus.Instance=null;Check(EmergencyObservation.Active()==null,"missing ship unavailable");
Console.WriteLine($"PASS native role: {checks} rejection cases, actual-click/confirmation boundary, 4 exact repair windows and emergency observation (synthetic)");
ShipStatus.Instance=new();var sab=new SabotageSystemType();
ShipStatus.Instance.Systems[SystemTypes.Sabotage]=sab;
var sabotage=new SabotageCommand{CommandId="sabotage-test",Target="lights",ExpectedSessionId="current-session",ExpectedMatchId=1,IssuedAtUtc=DateTimeOffset.UtcNow};
Check(SabotageControl.Execute(sabotage)==null && ShipStatus.Instance.RpcCalls==1,"native sabotage RPC");
Check(SabotageControl.Capture().LastAction.State=="submitted","RPC return does not prove sabotage");
ShipStatus.Instance.Systems[SystemTypes.Electrical]=new SwitchSystem{IsActive=true};
Check(SabotageControl.Capture().LastAction.State=="completed","active native system confirms sabotage");
sab.Timer=10;Check(SabotageControl.Execute(sabotage)!=null && ShipStatus.Instance.RpcCalls==1,"sabotage cooldown");sab.Timer=0;
sab.AnyActive=true;Check(SabotageControl.Execute(sabotage)!=null,"existing sabotage");sab.AnyActive=false;
MeetingHud.Instance=new();Check(SabotageControl.Execute(sabotage)!=null,"meeting sabotage refused");MeetingHud.Instance=null;
self.Data.RoleType=RoleTypes.Crewmate;Check(SabotageControl.Execute(sabotage)!=null,"crew sabotage refused");self.Data.RoleType=RoleTypes.Impostor;
Check(SabotageControl.Execute(new SabotageCommand{Target="lights",IssuedAtUtc=DateTimeOffset.UtcNow.AddSeconds(-3)})!=null,"stale sabotage");
Console.WriteLine("PASS native sabotage: cooldown, active system, meeting, role, replay guards and submitted/completed boundary (synthetic)");
VentTests.Run();

AwarenessTests.Run();

SafetyCrimeTests.Run();
BlackoutSafetyTests.Run();
