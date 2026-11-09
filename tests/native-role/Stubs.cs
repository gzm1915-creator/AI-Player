// Synthetic interfaces: these tests never load game DLLs or send inputs.
using System;
using System.Collections.Generic;
namespace BepInEx { public static class Paths {public static string PluginPath=System.IO.Path.GetTempPath();} }
namespace HarmonyLib {
 [AttributeUsage(AttributeTargets.Class)] public sealed class HarmonyPatch:Attribute { public HarmonyPatch(Type t,string m){} }
}
namespace UnityEngine {
 public class Object {
   private static int next; private int id=++next; public int GetInstanceID()=>id;
   public static PlayerControl[] ScenePlayers=Array.Empty<PlayerControl>();
   public static T[] FindObjectsOfType<T>() where T:class => typeof(T)==typeof(Vent) ? (T[])(object)Vent.All : typeof(T)==typeof(PlayerControl) ? (T[])(object)ScenePlayers : Array.Empty<T>();
   public T? TryCast<T>() where T:class => this as T;
   public static implicit operator bool(Object? o)=>o!=null;
   public static bool operator !(Object? o)=>o==null;
 }
 public struct Vector3 {public float x,y,z;public static implicit operator Vector2(Vector3 v)=>new(v.x,v.y);}
 public struct Vector2 { public float x,y;public Vector2(float a,float b){x=a;y=b;}public static float Distance(Vector2 a,Vector2 b)=>MathF.Sqrt((a.x-b.x)*(a.x-b.x)+(a.y-b.y)*(a.y-b.y)); }
 public class GameObject {public bool activeInHierarchy=true;public int layer;}
 public class Transform {public Vector3 position;}
 public class Component:Object {public string name="synthetic";public GameObject gameObject=new();public Transform transform=new();}
 public class Camera {
   public static Camera[] allCameras={new()};public bool enabled=true;public int cullingMask=-1,pixelWidth=100,pixelHeight=100;
   public object? targetTexture;public string name="synthetic";public float nearClipPlane=-100,farClipPlane=100;
   public Vector3 WorldToViewportPoint(Vector3 p)=>new(){x=.5f+p.x/20,y=.5f+p.y/20,z=p.z};
 }
}
public enum RoleTypes {Crewmate,Impostor,Shapeshifter}
public class RoleBehaviour {public bool IsImpostor,CanUseKillButton,CanVent;}
public class PlayerData {public RoleTypes RoleType;public RoleBehaviour? Role=new();public bool IsDead,Disconnected;public string PlayerName="test";}
public class PlayerControl:UnityEngine.Component {
 public static PlayerControl? LocalPlayer;public static List<PlayerControl> AllPlayerControls=new();
 public PlayerData? Data=new();public byte PlayerId;public float killTimer;public bool CanMove=true,inVent,walkingToVent,isKilling;
 public PlayerPhysics? MyPhysics=new();
 public UnityEngine.Vector2 GetTruePosition()=>new(transform.position.x,transform.position.y);
 public void MurderPlayer(PlayerControl target){}
}
public class PlayerPhysics {public UnityEngine.Vector2 Velocity;public UnityEngine.Vector2 GetVelocity()=>Velocity;public int Exits;public void RpcExitVent(int id)=>Exits++;}
public class Vent:UnityEngine.Component {
 public static Vent[] All=Array.Empty<Vent>();public static Vent? currentVent;
 public int Id,Enters,Moves;public Vent? Left,Right,Center;public float UsableDistance=1;public bool Allowed=true,MoveAllowed=true;
 public float CanUse(PlayerData? p,out bool can,out bool could) {can=could=Allowed;return UnityEngine.Vector2.Distance(PlayerControl.LocalPlayer!.GetTruePosition(),transform.position);}
 public void Use()=>Enters++;
 public bool TryMoveToVent(Vent v,out string error) {Moves++;error=MoveAllowed?"":"blocked";return MoveAllowed;}
}
public class KillButton:UnityEngine.Component {public PlayerControl? currentTarget;public bool canInteract=true;public int Clicks;public void DoClick()=>Clicks++;}
public class HudManager {public static HudManager? Instance;public KillButton? KillButton;}
public class MeetingHud {public static MeetingHud? Instance;}
public class ExileController {public static ExileController? Instance;}
public class GameManager {public static GameManager? Instance=new();public bool GameHasStarted=true;}
public class PlayerTask {public uint Id;public string TaskType="FixWiring";}
public class Minigame:UnityEngine.Object {public static Minigame? Instance;public PlayerTask? MyTask;}
public class SwitchMinigame:Minigame {}
public class KeypadGame:Minigame {}
public class TuneRadioMinigame:Minigame {}
public class ReactorMinigame:Minigame {}
public enum SystemTypes {Electrical,Reactor,LifeSupp,Comms,Sabotage}
public class ShipStatus:UnityEngine.Object {public static ShipStatus? Instance;public string Type="Ship";public Dictionary<SystemTypes,UnityEngine.Object> Systems=new();public int RpcCalls;public void RpcUpdateSystem(SystemTypes system,byte value)=>RpcCalls++;}
public class SabotageSystemType:UnityEngine.Object {public float Timer;public bool AnyActive;}
public class SwitchSystem:UnityEngine.Object {public bool IsActive;}
public class ReactorSystemType:UnityEngine.Object {public bool IsActive;}
public class LifeSuppSystemType:UnityEngine.Object {public bool IsActive;}
public class HudOverrideSystemType:UnityEngine.Object {public bool IsActive;}
public static class Constants {public const int ShipAndObjectsMask=1,ShipOnlyMask=2;}
public static class PhysicsHelpers {public static bool Blocked;public static Func<UnityEngine.Vector2,UnityEngine.Vector2,bool>? Occludes;public static bool AnythingBetween(UnityEngine.Vector2 a,UnityEngine.Vector2 b,int mask,bool flag)=>Blocked || (Occludes?.Invoke(a,b) ?? false);}
namespace AUTelemetryProbe {
 internal static class RuntimeControl {public static bool AutoNavigationEnabled=true,UseVentsEnabled=true;}
 internal static class TelemetryProbePlugin {public static FakeLog? LogSource=new();}
 internal class FakeLog {public void LogInfo(string text){} }
 internal static class TelemetryCapture {public static string CurrentSessionId="current-session";}
 internal static class TaskStartSignalPatch {public static long SignalVersion=1;}
 internal class InternalNavigationCommand {public string CommandId="test",ExpectedSessionId="current-session";public long ExpectedMatchId=1;public DateTimeOffset? IssuedAtUtc=DateTimeOffset.UtcNow;public int TargetNumber=1;public int MaxDurationMs=45000;}
 internal sealed class PositionInfo {public string Space="";public float X,Y;}
}
