using System;
using System.Collections.Generic;
using AUTelemetryProbe;

static class VentTests
{
 static void Check(bool ok,string why) {if(!ok)throw new Exception("vent: "+why);}
 static DateTimeOffset now;
 static PlayerControl p=null!;
 static Vent a=null!,b=null!,c=null!,d=null!;
 static VentTravelCommand Cmd(string action="travel",int target=2,bool guarded=false,double wait=2)=>new(){CommandId=Guid.NewGuid().ToString(),Action=action,GuardedExit=guarded,ObserveSeconds=wait,
   TargetVentId=target,EntryVentId=0,ExpectedSessionId=TelemetryCapture.CurrentSessionId,ExpectedMatchId=TaskStartSignalPatch.SignalVersion,IssuedAtUtc=now};
 static void Reset() {
   TaskStartSignalPatch.SignalVersion++;now=DateTimeOffset.UtcNow;
   p=new(){Data=new(){RoleType=RoleTypes.Impostor,Role=new(){CanVent=true}}};PlayerControl.LocalPlayer=p;
   ShipStatus.Instance=new();GameManager.Instance=new();MeetingHud.Instance=null;ExileController.Instance=null;Minigame.Instance=null;
   RuntimeControl.AutoNavigationEnabled=RuntimeControl.UseVentsEnabled=true;
   a=new(){Id=0};b=new(){Id=1};c=new(){Id=2};d=new(){Id=3};a.Left=b;b.Left=a;b.Right=c;c.Left=b;
   Vent.All=new[]{a,b,c,d};Vent.currentVent=null;VentTransport.Capture();
   PlayerControl.AllPlayerControls=new(){p};UnityEngine.Object.ScenePlayers=Array.Empty<PlayerControl>();
   UnityEngine.Camera.allCameras=new[]{new UnityEngine.Camera()};PhysicsHelpers.Blocked=false;
 }
 static void Inside(Vent v) {p.inVent=true;p.CanMove=false;Vent.currentVent=v;}
 static void Outside() {p.inVent=false;p.walkingToVent=false;p.CanMove=true;}
 static void ObserveFor(double from,double to) {for(double t=from;t<to;t+=.1) VentTransport.Advance(now.AddSeconds(t));VentTransport.Advance(now.AddSeconds(to));}
 public static void Run() {
   Reset();var graph=new Dictionary<int,Vent>{{0,a},{1,b},{2,c},{3,d}};
   Check(string.Join(",",VentTransport.FindPath(graph,0,2))=="0,1,2","BFS multi-hop/cycle");
   Check(VentTransport.FindPath(graph,0,3).Count==0,"disconnected");
   Check(VentTransport.Submit(Cmd(),now)==null && a.Enters==1,"native entry request");
   VentTransport.Advance(now.AddSeconds(.5));Check(VentTransport.Capture().Stage=="entering","entry not falsely confirmed");
   Inside(a);VentTransport.Advance(now.AddSeconds(1));VentTransport.Advance(now.AddSeconds(1.7));
   Check(a.Moves==1 && VentTransport.Capture().Stage=="awaiting_hop","first adjacent hop");
   VentTransport.Advance(now.AddSeconds(2));Check(a.Moves==1,"no repeat without observed hop");
   Inside(b);VentTransport.Advance(now.AddSeconds(2.1));VentTransport.Advance(now.AddSeconds(2.8));Check(b.Moves==1,"second hop");
   Inside(c);VentTransport.Advance(now.AddSeconds(3));Check(VentTransport.Capture().ReachedTarget,"observed target");
   VentTransport.Advance(now.AddSeconds(3.1));Check(p.MyPhysics!.Exits==1 && VentTransport.Busy,"exit RPC is not completion");
   Outside();VentTransport.Advance(now.AddSeconds(3.2));Check(VentTransport.Capture().State=="completed" && VentTransport.Capture().SafeExitConfirmed,"confirmed safe exit");
   Reset();Check(VentTransport.Submit(Cmd(target:3),now)!=null && a.Enters==0,"reject disconnected before entry");
   a.Allowed=false;Check(VentTransport.Submit(Cmd(),now)!=null && a.Enters==0,"native CanUse guard");a.Allowed=true;
   p.transform.position=new(){x=5};Check(VentTransport.Submit(Cmd(),now)!=null && a.Enters==0,"range guard");
   Reset();p.Data!.RoleType=RoleTypes.Crewmate;Check(VentTransport.Submit(Cmd(),now)!=null,"role guard");
   Reset();RuntimeControl.UseVentsEnabled=false;Check(VentTransport.Submit(Cmd(),now)!=null,"permission guard");
   Inside(a);Check(VentTransport.Submit(Cmd("exit"),now)==null,"exit works with permission off");VentTransport.Advance(now);
   Outside();VentTransport.Advance(now.AddSeconds(.2));Check(VentTransport.Capture().State=="completed","emergency exit");
   Reset();Check(VentTransport.Submit(Cmd(),now)==null,"entry accepted");
   Check(VentTransport.Submit(Cmd("exit"),now.AddSeconds(.1))==null,"cancel pending entry");
   VentTransport.Advance(now.AddSeconds(.2));Check(VentTransport.Busy,"must not finish before pending entry coroutine");
   Inside(a);VentTransport.Advance(now.AddSeconds(.3));Check(p.MyPhysics!.Exits==1,"cancel exits delayed entry");
   Outside();VentTransport.Advance(now.AddSeconds(.4));Check(VentTransport.Capture().SafeExitConfirmed,"cancel completed");
   Reset();
   var enterOnly=Cmd("enter");
   Check(VentTransport.Submit(enterOnly,now)==null,"manual enter accepted");
   VentTransport.Advance(now.AddSeconds(.1));
   Check(VentTransport.Capture().State!="completed","enter needs native confirmation");
   Inside(a);VentTransport.Advance(now.AddSeconds(.2));
   Check(VentTransport.Capture().State=="completed" && VentTransport.Capture().HeldInside && !VentTransport.Capture().SafeExitConfirmed && p.MyPhysics!.Exits==0,"manual enter holds inside without claiming exit");
   Check(VentTransport.Submit(Cmd("enter"),now)!=null,"no duplicate enter while inside");
   Check(VentTransport.Submit(Cmd("move",1),now)==null,"manual inside move accepted");
   VentTransport.Advance(now.AddSeconds(.7));Inside(b);VentTransport.Advance(now.AddSeconds(.9));
   Check(VentTransport.Capture().State=="completed" && VentTransport.Capture().HeldInside && p.MyPhysics!.Exits==0,"manual move stays inside");
   Check(VentTransport.Submit(Cmd("exit"),now)==null,"manual exit accepted after holding");
   VentTransport.Advance(now.AddSeconds(1));Outside();VentTransport.Advance(now.AddSeconds(1.2));
   Check(VentTransport.Capture().State=="completed" && VentTransport.Capture().SafeExitConfirmed,"manual exit confirmed");
   Reset();Check(VentTransport.Submit(Cmd("move",1),now)!=null,"pipe movement requires inside");
   Reset();VentTransport.Submit(Cmd(),now);Inside(a);VentTransport.Advance(now.AddSeconds(.1));
   RuntimeControl.AutoNavigationEnabled=false;VentTransport.Advance(now.AddSeconds(.2));VentTransport.Advance(now.AddSeconds(.3));
   Check(p.MyPhysics!.Exits==1,"stop attempts cleanup exit");Outside();VentTransport.Advance(now.AddSeconds(.4));
   Check(VentTransport.Capture().State=="failed" && VentTransport.Capture().SafeExitConfirmed,"cancel is failed travel even after safe exit");
   Reset();VentTransport.Submit(Cmd(),now);Inside(a);VentTransport.Advance(now.AddSeconds(.1));VentTransport.Advance(now.AddSeconds(.8));
   VentTransport.Advance(now.AddSeconds(5));Check(VentTransport.Capture().Stage=="cleanup_exit","hop timeout");
   VentTransport.Advance(now.AddSeconds(5.1));VentTransport.Advance(now.AddSeconds(11));
   Check(VentTransport.Capture().State=="failed" && !VentTransport.Capture().SafeExitConfirmed,"unconfirmed exit never completed");
   Reset();VentTransport.Submit(Cmd(),now);MeetingHud.Instance=new();VentTransport.Advance(now.AddSeconds(.1));
   Check(!VentTransport.Busy && p.MyPhysics!.Exits==0,"meeting cancels without inappropriate RPC");
   Reset();var old=Cmd();TaskStartSignalPatch.SignalVersion++;Check(VentTransport.Submit(old,now)!=null,"cross-match guard");
   Check(VentTransport.Submit(Cmd(),now.AddSeconds(3))!=null,"expiry guard");
   Reset();Inside(a);Check(VentControl.Move(2)!=null && a.Moves==0,"low-level nonadjacent rejected");
   Reset();VentTransport.Submit(Cmd(target:0,guarded:true),now);Inside(a);
   VentTransport.Advance(now.AddSeconds(.1));Check(VentTransport.Capture().Stage=="observing_exit","guarded exit must observe");
   ObserveFor(.2,2.1);
   Check(p.MyPhysics!.Exits==0,"minimum continuous clear period");
   var other=new PlayerControl{PlayerId=4};other.transform.position=new(){x=1};PlayerControl.AllPlayerControls.Add(other);
   VentTransport.Advance(now.AddSeconds(2.2));Check(VentTransport.Capture().ClearSeconds==0,"witness resets clear clock");
   PlayerControl.AllPlayerControls.Remove(other);VentTransport.Advance(now.AddSeconds(2.3));
   ObserveFor(2.4,4.2);Check(p.MyPhysics.Exits==0,"clear clock restarted");
   VentTransport.Advance(now.AddSeconds(4.4));VentTransport.Advance(now.AddSeconds(4.5));
   Check(p.MyPhysics.Exits==1 && VentTransport.Capture().State!="completed","clear observation requests native exit only");
   Outside();VentTransport.Advance(now.AddSeconds(4.6));Check(VentTransport.Capture().SafeExitConfirmed,"guarded confirmed exit");
   Reset();VentTransport.Submit(Cmd(target:0,guarded:true,wait:15),now);Inside(a);VentTransport.Advance(now.AddSeconds(.1));
   ObserveFor(.2,14);Check(p.MyPhysics!.Exits==0,"15 second observation is respected");
   ObserveFor(14.1,15.3);Check(VentTransport.Capture().Stage=="exiting" || p.MyPhysics.Exits>0,"15 second clear observation finishes");
   Reset();VentTransport.Submit(Cmd(target:0,guarded:true),now);Inside(a);VentTransport.Advance(now.AddSeconds(.1));
   VentTransport.Advance(now.AddSeconds(.2));VentTransport.Advance(now.AddSeconds(4));
   Check(p.MyPhysics!.Exits==0 && VentTransport.Capture().ClearSeconds==0,"paused scan time does not count as continuous observation");
   Reset();VentTransport.Submit(Cmd(target:0,guarded:true),now);Inside(a);VentTransport.Advance(now.AddSeconds(.1));
   UnityEngine.Camera.allCameras=Array.Empty<UnityEngine.Camera>();VentTransport.Advance(now.AddSeconds(3));
   Check(p.MyPhysics!.Exits==0 && VentTransport.Capture().ExitObservation!.Status=="camera_unavailable","unknown detection keeps player hidden");
   VentTransport.Advance(now.AddSeconds(46));Check(VentTransport.Capture().State=="failed" && p.inVent && p.MyPhysics.Exits==0,"blocked observation timeout never forces exit");
   Reset();UnityEngine.Object.ScenePlayers=new[]{new PlayerControl {PlayerId=9}};
   Check(VentTransport.Submit(Cmd(guarded:true),now)!=null && a.Enters==0,"practice scene witness blocks entry");
   Reset();Check(VentTransport.Submit(Cmd(guarded:true,wait:1),now)!=null,"observation bounds");
   Reset();VentTransport.Submit(Cmd(guarded:true),now);RuntimeControl.UseVentsEnabled=false;
   VentTransport.Advance(now.AddSeconds(.1));VentTransport.Advance(now.AddSeconds(.2));
   Check(VentTransport.Busy,"guarded cleanup waits for pending native entry coroutine");
   Inside(a);ObserveFor(.3,2.6);VentTransport.Advance(now.AddSeconds(2.7));
   Check(p.MyPhysics!.Exits==1,"guarded cancellation exits only after clear observation");
   Outside();VentTransport.Advance(now.AddSeconds(2.8));Check(VentTransport.Capture().State=="failed" && VentTransport.Capture().SafeExitConfirmed,"guarded cancelled travel not reported as completed");
   Reset();Inside(a);
   var hidden=new PlayerControl{PlayerId=7};hidden.transform.position=new(){x=1};
   UnityEngine.Object.ScenePlayers=new[]{hidden};PhysicsHelpers.Blocked=true;
   Check(VentExitObservation.Scan().NearbyCount==0,"wall occlusion prevents hidden player information");
   PhysicsHelpers.Blocked=false;Check(VentExitObservation.Scan().NearbyCount==1,"visible nearby scene dummy counted");
   hidden.inVent=true;Check(VentExitObservation.Scan().NearbyCount==0,"other hidden vent occupants not revealed");
   Console.WriteLine("PASS native vents: multi-hop, observed entry/hop/exit, pending-entry cancellation, native range, disconnect, role/gates, timeouts, meeting and replay guards (synthetic)");
   Console.WriteLine("PASS guarded vent exit: 2-15 second continuous clear view, witness reset, scene dummies, camera failure, hidden timeout and no through-wall/vent-player disclosure (synthetic)");
 }
}
