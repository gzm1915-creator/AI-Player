// Synthetic game surface for state-machine tests; no game DLLs or input events.
using System;
using System.Collections.Generic;
namespace UnityEngine {
 public class Object { static int next; int id=++next; public static object? SceneObject; public static T? FindObjectOfType<T>() where T:class => SceneObject as T; public int GetInstanceID()=>id; public T? TryCast<T>() where T:class => this as T; }
 public class GameObject { public bool activeInHierarchy=true; public int layer; }
 public class Transform { public Vector3 localPosition; public Transform parent=>this; public Vector3 TransformPoint(Vector3 p)=>p; }
 public class Component:Object { public Transform transform=new(); public GameObject gameObject=new(); }
 public struct Vector3 { public float x,y,z; public static Vector3 Lerp(Vector3 a,Vector3 b,float t)=>a; }
 public struct Vector2 { public float x,y; public Vector2(float x,float y){this.x=x;this.y=y;} public static float Distance(Vector2 a,Vector2 b)=>MathF.Sqrt((a.x-b.x)*(a.x-b.x)+(a.y-b.y)*(a.y-b.y)); }
 public struct Color { public float r,g,b; }
 public struct Bounds { public Vector3 center; }
 public class Collider2D:Component { public Bounds bounds; }
 public class SpriteRenderer:Component { public Color color; }
 public class Camera { public static Camera? main; public static Camera[] allCameras=Array.Empty<Camera>(); public string name="synthetic"; public bool enabled=true; public int cullingMask=-1; public float nearClipPlane=0.1f,farClipPlane=1000,depth; public float zOffset,xOffset,yOffset; public object? targetTexture; public int pixelWidth=1920,pixelHeight=1080; public Vector3 WorldToViewportPoint(Vector3 p)=>new Vector3 { x=p.x+xOffset,y=p.y+yOffset,z=p.z+zOffset }; public Vector3 WorldToScreenPoint(Vector3 p)=>new Vector3 { x=p.x,y=p.y,z=p.z+zOffset }; }
 public static class Mathf { public static float Clamp01(float v)=>Math.Clamp(v,0,1); }
 public static class Screen { public static int width=1920,height=1080; }
}
public class PlayerTask:UnityEngine.Object { public int TaskStep; public bool IsComplete; public uint Id; public string TaskType="Synthetic"; public PlayerControl? Owner; }
public class NormalPlayerTask:PlayerTask { public byte[] Data=new byte[2]; public float TaskTimer; public void Initialize(){} public void Complete(){} public void NextStep(){} }
public class SampleMinigame:Minigame {
 public enum States:byte { PrepareSample=0, Complete=16, AwaitingStart=32, Selection=64, Processing=128 }
 public States State; public int StartCalls,SelectCalls,LastSelected=-1;
 public void NextStep(){StartCalls++;State=States.Processing;}
 public void SelectTube(int i){SelectCalls++;LastSelected=i;}
}
public class Minigame:UnityEngine.Component { public static Minigame? Instance; public PlayerTask? MyTask; public bool amOpening; public int CloseCalls,ForceCloseCalls; public void Close()=>CloseCalls++; public void ForceClose()=>ForceCloseCalls++; }
public class HudManager { public static HudManager? Instance; public UnityEngine.Camera? UICamera; public DialogueBox? Dialogue; }
public class TextLabel { public string text=""; }
public class DialogueBox:UnityEngine.Component { public TextLabel target=new(); public int HideCount; public void Hide(){HideCount++;gameObject.activeInHierarchy=false;} }
public class TutorialManager:UnityEngine.Component { }
public class MeetingHud { public static MeetingHud? Instance; }
public class NativeTaskRecord { public uint Id; public bool Complete; }
public class PlayerData { public bool IsDead; public List<NativeTaskRecord> Tasks=new(); public NativeTaskRecord? FindTaskById(uint id)=>Tasks.Find(t=>t.Id==id); }
public class PlayerControl { public static PlayerControl? LocalPlayer=new(); public PlayerData? Data=new(); public byte PlayerId; public List<PlayerTask>? myTasks=new(); public UnityEngine.Vector2 GetTruePosition()=>new(1,2); public void CompleteTask(uint id){} public void ClearTasks(){} }
public class GameManager { public static GameManager? Instance=new(); public bool GameHasStarted=true; }
public class ShieldMinigame:Minigame { public UnityEngine.SpriteRenderer[]? Shields; public UnityEngine.Color OnColor,OffColor; public int Toggles; public void ToggleShield(int i){Shields![i].color=OnColor;Toggles++;} }
public class AcceptDivertPowerGame:Minigame { public bool done; public int Clicks; public void DoSwitch(){done=true;Clicks++;} }
public class UploadDataGame:Minigame { public bool running; public int Clicks; public void Click(){running=true;Clicks++;} }
public class FloatRange { public float min,max; }
public class EmptyGarbageMinigame:Minigame { public UnityEngine.Collider2D? Handle; public FloatRange HandleRange=new(); }
public class DivertPowerTask:PlayerTask { public int TargetSystem; }
public class DivertPowerMinigame:Minigame { public UnityEngine.Collider2D[]? Sliders; public int[]? SliderOrder; public FloatRange SliderY=new(); }
public class Wire:UnityEngine.Component { public int WireId; public UnityEngine.Collider2D hitbox=new(); }
public class WireNode:UnityEngine.Component { public int WireId; public UnityEngine.Collider2D hitbox=new(); }
public class WireMinigame:Minigame { public Wire[]? LeftNodes; public WireNode[]? RightNodes; }
public class UnlockManifoldsMinigame:Minigame { public bool animating; public int buttonCounter; public void HitButton(int n)=>buttonCounter++; }
public class VentDirt:UnityEngine.Object { }
public class DirtPool { public List<VentDirt> activeChildren=new(); }
public class VentCleaningMinigame:Minigame { public bool ventOpen; public DirtPool? dirtPool; public void OpenVent()=>ventOpen=true; public void CleanUp(VentDirt d)=>dirtPool!.activeChildren.Remove(d); }
namespace AUTelemetryProbe {
 internal sealed class InternalNavigationCommand { public string CommandId="neko-test",Action="start_inspect_sample"; public uint? InteractTaskId; public int? ExpectedMinigameInstanceId; public int TargetNumber=-1; public string ExpectedSessionId="test-session"; public long ExpectedMatchId=1; public DateTimeOffset? IssuedAtUtc=DateTimeOffset.UtcNow; }
 internal static class TelemetryCapture { public static string CurrentSessionId="test-session";
 public static OwnTaskInfo DescribeOwnTask(PlayerTask task)=>new(){TaskId=task.Id,TaskType=task.TaskType,TaskInstanceId=TaskCompletionMemory.InstanceId(task),Complete=task.IsComplete,Step=task.TaskStep}; }
 internal sealed class OwnTaskInfo { public uint TaskId; public string TaskType="",TaskInstanceId="",StartAt=""; public bool Complete; public int Step,MaxStep; public double Confidence; public List<PositionInfo> Locations=new(); }
 internal sealed class PositionInfo { public string Space=""; public float X,Y; }
 internal static class RuntimeControl { public static bool MinigameControlEnabled,AutoNavigationEnabled; }
 internal static class TaskStartSignalPatch { public static long SignalVersion=1; }
 internal sealed class Log { public void LogWarning(string s){} }
 internal static class TelemetryProbePlugin { public static Log? LogSource; }
}
namespace HarmonyLib {
 [AttributeUsage(AttributeTargets.Class)] public sealed class HarmonyPatch:Attribute { public HarmonyPatch(Type type,string method){} }
 public class Harmony { public List<Type> Patched=new(); public string? FailType; public void PatchAll(Type type){Patched.Add(type);if(type.Name==FailType)throw new Exception("synthetic hook failure");} }
}
