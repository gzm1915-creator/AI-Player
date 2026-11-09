using System;
using AUTelemetryProbe;
SampleTaskTests.Run();
NavigationProgressTests.Run();
TaskCompletionTests.Run();

static void Check(bool condition,string message) { if(!condition) throw new Exception(message); }
static void Setup(Minigame game) {
 RuntimeControl.MinigameControlEnabled=false; MinigameAutomation.Tick(0);
 MeetingHud.Instance=null; Minigame.Instance=game;
 game.MyTask ??= new PlayerTask();
 RuntimeControl.MinigameControlEnabled=true;
 MinigameAutomation.Tick(100);
}

var upload=new UploadDataGame(); Setup(upload);
MinigameAutomation.Tick(1000);
Check(upload.Clicks==1,"upload must start via its control");
Check(MinigameAutomation.Status.State!="completed","button click is not task completion");
MinigameAutomation.Tick(1500);
Check(upload.Clicks==1,"running upload must not restart");
upload.MyTask!.TaskStep=1; MinigameAutomation.Tick(2000);
Check(MinigameAutomation.Status.State=="completed","real step increase must verify progress");
MinigameAutomation.Tick(2700); MinigameAutomation.Tick(2800);
Check(upload.CloseCalls==1,"close animation must be dispatched only once");

var cancelled=new UploadDataGame(); Setup(cancelled);
MinigameAutomation.Tick(1000); Minigame.Instance=null; MinigameAutomation.Tick(1500);
Check(MinigameAutomation.Status.State=="closed_without_progress","closing a window is not completion");

var timeout=new UploadDataGame(); Setup(timeout);
MinigameAutomation.Tick(1000); MinigameAutomation.Tick(31_000);
Check(MinigameAutomation.Status.State=="failed","unverified puzzle must time out");
MinigameAutomation.Tick(31_100); MinigameAutomation.Tick(32_000);
Check(timeout.CloseCalls==1,"failed puzzle must close for retry");
MinigameAutomation.Tick(34_000);
Check(timeout.ForceCloseCalls==1,"stuck closing animation must have a bounded fallback");

var unsupported=new Minigame(); Setup(unsupported); MinigameAutomation.Tick(1000);
Check(MinigameAutomation.Status.State=="unsupported","unsupported puzzle must be explicit");

var disabled=new UploadDataGame(); Setup(disabled);
RuntimeControl.MinigameControlEnabled=false; MinigameAutomation.Tick(1000);
Check(disabled.Clicks==0,"disabled solver must not act");

var interrupted=new UploadDataGame(); Setup(interrupted);
MeetingHud.Instance=new MeetingHud(); MinigameAutomation.Tick(1000);
Check(interrupted.Clicks==0,"meeting must interrupt puzzle actions");

var old=new UploadDataGame { MyTask=new PlayerTask { IsComplete=true } }; Setup(old);
MinigameAutomation.Tick(1000);
Check(old.Clicks==0 && MinigameAutomation.Status.State=="unsupported","already complete task is not new completion");

var shields=new ShieldMinigame { OnColor=new UnityEngine.Color {r=1}, OffColor=new UnityEngine.Color {b=1},
 Shields=new[] {new UnityEngine.SpriteRenderer {color=new UnityEngine.Color {r=1}},new UnityEngine.SpriteRenderer {color=new UnityEngine.Color {b=1}}} };
Setup(shields); MinigameAutomation.Tick(1000); MinigameAutomation.Tick(1500);
Check(shields.Toggles==1,"only inactive shields must be toggled");
Check(!shields.MyTask!.IsComplete,"solver must not write task completion");

Setup(new Minigame()); MinigameAutomation.Tick(1000); MinigameAutomation.Tick(1100); MinigameAutomation.Tick(2000);
Check(Minigame.Instance!.CloseCalls==1,"unsupported puzzle must also release the UI");
UnityEngine.Camera.allCameras=new[] {
 new UnityEngine.Camera { name="UI Camera", zOffset=-40,depth=10 },
 new UnityEngine.Camera { name="task camera", zOffset=50,depth=0 }
};
Check(MinigameAutomation.TryProjectControl(new UnityEngine.Vector3 {x=595,y=808,z=0},0,out var projected),"negative-depth UI camera must fall back to a camera that sees the puzzle");
Check(projected.z==50 && MinigameAutomation.Status.InputDiagnostics!.Contains("selected=task camera"),"select rendering camera instead of merely preferring UI camera");
UnityEngine.Camera.allCameras[1].cullingMask=0;
Check(!MinigameAutomation.TryProjectControl(new UnityEngine.Vector3 {x=595,y=808,z=0},0,out _),"camera that excludes the control layer cannot project inputs");
Console.WriteLine("12 native state-machine and camera scenarios passed; all input surfaces were synthetic.");

var observer=new UnityEngine.Vector3 {x=.5f,y=.5f};
var uiCamera=new UnityEngine.Camera {name="UI",zOffset=-40};
var worldCamera=new UnityEngine.Camera {name="World",zOffset=50};
UnityEngine.Camera.main=uiCamera;
Check(PlayerView.Select(new[]{uiCamera,worldCamera},observer,0,out var viewDiagnostics)==worldCamera,
 "player view must select a world camera that sees the local player, even when main is UI");
Check(viewDiagnostics.Contains("UI:local=") && viewDiagnostics.Contains("valid=False"),"rejected camera must have diagnostic evidence");
foreach(var invalid in new[]{
 new UnityEngine.Camera {zOffset=50,xOffset=2}, new UnityEngine.Camera {zOffset=50,cullingMask=0},
 new UnityEngine.Camera {zOffset=50,targetTexture=new object()},new UnityEngine.Camera {zOffset=50,enabled=false},
 new UnityEngine.Camera {zOffset=50,pixelWidth=0},new UnityEngine.Camera {zOffset=-1}})
 Check(PlayerView.Select(new[]{invalid},observer,0,out _)==null,"invalid player-view camera must not imply an empty visible scene");
Check(PlayerView.Contains(worldCamera,new UnityEngine.Vector3 {x=0,y=1}),"viewport edge is visible");
foreach(var point in new[]{new UnityEngine.Vector3 {x=1.01f,y=.5f},new UnityEngine.Vector3 {x=.5f,y=-.01f},
 new UnityEngine.Vector3 {x=.5f,y=.5f,z=-51},new UnityEngine.Vector3 {x=float.NaN,y=.5f},
 new UnityEngine.Vector3 {x=.5f,y=.5f,z=1001}})
 Check(!PlayerView.Contains(worldCamera,point),"offscreen or invalid target must stay excluded");
Console.WriteLine("Player detection camera selection, unavailable-camera diagnostics and viewport boundary scenarios passed.");
var orthoWorld=new UnityEngine.Camera {name="Main Camera",nearClipPlane=-100,farClipPlane=100,zOffset=-.01f};
Check(PlayerView.Select(new[]{uiCamera,orthoWorld},observer,0,out _)==orthoWorld,
 "a rendered observer at negative depth within the game's clip planes must select its world camera");
Check(PlayerView.Contains(orthoWorld,observer),"visible negative-depth player must not be rejected");
Check(!PlayerView.Contains(orthoWorld,new UnityEngine.Vector3 {x=.5f,y=.5f,z=-101}),"point behind the actual near plane remains hidden");

var routeFixture=System.IO.Path.GetTempFileName();
try {
 System.IO.File.WriteAllText(routeFixture,"""
 {"anchors":[{"anchorId":"a","position":{"x":0,"y":0}},{"anchorId":"b","position":{"x":0,"y":2}},{"anchorId":"c","position":{"x":2,"y":2}},{"anchorId":"d","position":{"x":2,"y":0}}],
 "routeSegments":[{"segmentId":"recorded","fromAnchorId":"a","toAnchorId":"d","status":"observed_success","path":[{"x":0,"y":0},{"x":0,"y":2},{"x":2,"y":2},{"x":2,"y":0}]},
 {"segmentId":"bad-shortcut","fromAnchorId":"a","toAnchorId":"d","status":"generated_validated","path":[{"x":0,"y":0},{"x":2,"y":0}]}]}
 """);
 var graph=NavigationGraph.Load(routeFixture,.08);
 bool Clear(UnityEngine.Vector2 a,UnityEngine.Vector2 b) {
   if((a.x<1 && b.x>1)||(a.x>1 && b.x<1)) {
     var atWall=a.y+(b.y-a.y)*(1-a.x)/(b.x-a.x);
     return atWall>1.5;
   }
   return true;
 }
 var directions=new System.Collections.Generic.HashSet<(float,float,float,float)>();
 var plan=graph.FindRoute(new UnityEngine.Vector2(-.1f,0),"d",3,(from,to)=>{directions.Add((from.x,from.y,to.x,to.y));return Clear(from,to);});
 Check(directions.Contains((0,0,0,2)) && directions.Contains((0,2,0,0)),"directed traversal must check both directions for outward-only crime scene rules");
 Check(System.Linq.Enumerable.Any(plan.Points,p=>p.y>1.5f),"route must go around the wall even if the target is within snap radius");
 for(var i=1;i<plan.Points.Count;i++) Check(Clear(plan.Points[i-1],plan.Points[i]),"every executed graph leg must be the collision-checked leg");
 bool noEntry=false;
 try {graph.FindRoute(new UnityEngine.Vector2(-.1f,0),"d",3,(a,b)=>false);} catch(InvalidOperationException){noEntry=true;}
 Check(noEntry,"no reachable entry must fail explicitly instead of attaching through a wall");
 var pursuitPlan=PursuitPlanner.Plan(graph,new UnityEngine.Vector2(-.1f,0),new UnityEngine.Vector2(2,0),"moving-player",Clear);
 Check(System.Linq.Enumerable.Any(pursuitPlan.Points,p=>p.y>1.5f),"pursuit around wall must use recorded network");
 for(var i=1;i<pursuitPlan.Points.Count;i++) Check(Clear(pursuitPlan.Points[i-1],pursuitPlan.Points[i]),"every pursuit leg checked");
 var closePursuit=PursuitPlanner.Plan(graph,new UnityEngine.Vector2(0,0),new UnityEngine.Vector2(.05f,0),"close-player",Clear);
 Check(closePursuit.Points.Count==2,"near zero distance pursuit holds without already-at-node failure");
 bool pursuitBlocked=false;
 try {PursuitPlanner.Plan(graph,new UnityEngine.Vector2(0,0),new UnityEngine.Vector2(2,0),"blocked",(a,b)=>false);} catch(InvalidOperationException){pursuitBlocked=true;}
 Check(pursuitBlocked,"blocked pursuit fails instead of walking through wall");
 // Regression: a visible target in Reactor stood about three units away,
 // 1.845 from a recorded node. Walking approach must not fail the 1.75 snap.
 var reactorStart=new UnityEngine.Vector2(-21.323f,-5.725f);
 var reactorTarget=new UnityEngine.Vector2(-21.323f,-2.725f);
 var localApproach=PursuitPlanner.Plan(graph,reactorStart,reactorTarget,"reactor-player",(a,b)=>true);
 Check(localApproach.Points.Count==2 && Math.Abs(localApproach.PlannedDistance-3)<.01,
   "visible room approach within 4.5 must not require target to stand on recorded graph");
 bool sceneBlocked=false;
 try {PursuitPlanner.Plan(graph,new UnityEngine.Vector2(0,0),new UnityEngine.Vector2(2,0),"scene",(a,b)=>CrimeSceneGeometry.Allows(a,b,new UnityEngine.Vector2(1,0),3));}
 catch(InvalidOperationException){sceneBlocked=true;}
 Check(sceneBlocked,"local pursuit cannot bypass crime-scene direction rules");
 // At more than 4.5, the 2.5 attachment is still collision-checked.
 var offsetApproach=PursuitPlanner.Plan(graph,new UnityEngine.Vector2(-.5f,0),new UnityEngine.Vector2(3.845f,2),"offset-player",(a,b)=>true);
 Check(offsetApproach.Points.Count>=3,"target 1.845 off graph can attach on a clear leg");
 bool tooFarFromGraph=false;
 try {PursuitPlanner.Plan(graph,new UnityEngine.Vector2(0,0),new UnityEngine.Vector2(5,2),"remote-player",(a,b)=>true);}
 catch(InvalidOperationException){tooFarFromGraph=true;}
 Check(tooFarFromGraph,"distant target beyond 2.5 graph attachment remains rejected");
 var ventPlan=VentApproachPlanner.Plan(graph,new UnityEngine.Vector2(-.1f,0),new UnityEngine.Vector2(2,0),.75f,"vent",Clear);
 Check(System.Linq.Enumerable.Any(ventPlan.Points,p=>p.y>1.5f),"vent approach must preserve collision checked route around wall");
 Check(UnityEngine.Vector2.Distance(ventPlan.Points[^1],new UnityEngine.Vector2(2,0))<.65,"vent standing point remains within usable distance");
 bool ventBlocked=false;
 try {VentApproachPlanner.Plan(graph,new UnityEngine.Vector2(0,0),new UnityEngine.Vector2(2,0),.75f,"vent",(a,b)=>false);} catch(InvalidOperationException){ventBlocked=true;}
 Check(ventBlocked,"no walkable pipe standing point must fail instead of using direct input");
 var postKillStart=new UnityEngine.Vector2(0,-1.845f);
 bool oldEscapeFailed=false;
 try {graph.FindRoute(postKillStart,"c",1.25,(a,b)=>true);} catch(InvalidOperationException){oldEscapeFailed=true;}
 Check(oldEscapeFailed,"regression fixture must reproduce ordinary 1.25 start attachment failure");
 var escapePlan=EscapePlanner.Plan(graph,postKillStart,"c",(a,b)=>CrimeSceneGeometry.Allows(a,b,postKillStart,3));
 Check(escapePlan.Points.Count==2,"post-kill escape can attach from a clear off-graph position without modifying crew routes");
 var wallEscape=EscapePlanner.Plan(graph,new UnityEngine.Vector2(-.1f,0),"d",Clear);
 Check(System.Linq.Enumerable.Any(wallEscape.Points,p=>p.y>1.5f),"escape still goes around a wall");
 bool oldSceneBlocked=false;
 try {EscapePlanner.Plan(graph,new UnityEngine.Vector2(0,0),"d",(a,b)=>CrimeSceneGeometry.Allows(a,b,new UnityEngine.Vector2(2,0),3));}
 catch(InvalidOperationException){oldSceneBlocked=true;}
 Check(oldSceneBlocked,"escape cannot reenter another crime scene");
 var localVent=VentApproachPlanner.Plan(graph,reactorStart,reactorTarget,.75f,"reactor-vent",(a,b)=>true);
 Check(localVent.Points.Count==2,"a nearby clear vent can be approached from an off-graph murder position");
}
finally {System.IO.File.Delete(routeFixture);}
var longRouteFixture=System.IO.Path.GetTempFileName();
try {
 System.IO.File.WriteAllText(longRouteFixture,"""
 {"anchors":[{"anchorId":"a","position":{"x":0,"y":0}},{"anchorId":"b","position":{"x":0,"y":8}},{"anchorId":"c","position":{"x":10,"y":8}},{"anchorId":"d","position":{"x":10,"y":0}}],
 "routeSegments":[{"segmentId":"detour","fromAnchorId":"a","toAnchorId":"d","status":"observed_success","path":[{"x":0,"y":0},{"x":0,"y":8},{"x":10,"y":8},{"x":10,"y":0}]}]}
 """);
 var longGraph=NavigationGraph.Load(longRouteFixture,.08);
 bool longRejected=false;
 try {PursuitPlanner.Plan(longGraph,new UnityEngine.Vector2(0,0),new UnityEngine.Vector2(10,0),"long",(a,b)=>true);}
 catch(InvalidOperationException e){longRejected=e.Message.Contains("过远");}
 Check(longRejected,"longer than 18 units recorded detour must not become unbounded pursuit");
}
finally {System.IO.File.Delete(longRouteFixture);}
Console.WriteLine("Recorded navigation graph: wall-separated snap, invalid recorded shortcut and blocked-entry scenarios passed.");
EdgeAttachmentTests.Run();

var dialogue=new DialogueBox();
dialogue.target.text="正常来说，任务计量条已满，但在练习模式中，我们会改为发布新任务。";
HudManager.Instance=new HudManager {Dialogue=dialogue};
Minigame.Instance=null; MeetingHud.Instance=null;
UnityEngine.Object.SceneObject=new TutorialManager();
RuntimeControl.AutoNavigationEnabled=false;
Check(!PracticeDialog.Tick() && dialogue.HideCount==0,"disabled autoplay must not dismiss dialogs");
RuntimeControl.AutoNavigationEnabled=true;
dialogue.target.text="网络连接错误，请重试";
Check(!PracticeDialog.Tick(),"unrelated errors must not be dismissed");
dialogue.target.text="Normally the crew wins, but in practice mode we will assign new tasks.";
UnityEngine.Object.SceneObject=null;
Check(!PracticeDialog.Tick(),"completion text alone outside practice is insufficient");
UnityEngine.Object.SceneObject=new TutorialManager();
Check(PracticeDialog.Tick() && dialogue.HideCount==1,"only the verified practice task-refresh dialog should release autoplay");
Check(!PracticeDialog.Tick(),"a closed dialog must not be repeatedly clicked");
Console.WriteLine("Practice completion dialog: autoplay gate, practice-only scope, unrelated-error retention and single dismissal passed.");

var scene=new UnityEngine.Vector2(0,0);
Check(CrimeSceneGeometry.Allows(scene,new UnityEngine.Vector2(4,0),scene,3),"escape from body center allowed");
Check(CrimeSceneGeometry.Allows(new UnityEngine.Vector2(1,0),new UnityEngine.Vector2(4,0),scene,3),"outward escape allowed");
Check(!CrimeSceneGeometry.Allows(new UnityEngine.Vector2(1,0),new UnityEngine.Vector2(-4,0),scene,3),"inside cannot move toward body then away");
Check(!CrimeSceneGeometry.Allows(new UnityEngine.Vector2(4,0),new UnityEngine.Vector2(-4,0),scene,3),"outside endpoints cannot cut through crime scene");
Check(!CrimeSceneGeometry.Allows(new UnityEngine.Vector2(4,0),new UnityEngine.Vector2(2,0),scene,3),"no returning to body area");
Check(CrimeSceneGeometry.Allows(new UnityEngine.Vector2(4,0),new UnityEngine.Vector2(4,4),scene,3),"route outside crime area allowed");
Console.WriteLine("Crime scene path geometry: outward escape, no reentry or cross-through, clear detour passed.");
