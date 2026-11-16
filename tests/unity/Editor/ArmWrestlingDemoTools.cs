// Optional Editor-only scene creation and checks. Do not copy into a VR runtime folder.
using System;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

[InitializeOnLoad]
public static class ArmWrestlingDemoTools
{
    const string ActiveKey = "ArmUdp.CheckActive", CommandKey = "ArmUdp.CommandId", ResultsKey = "ArmUdp.Results";
    static double nextSample;

    static ArmWrestlingDemoTools() { EditorApplication.update += Tick; }

    [MenuItem("Arm Wrestling/Create UDP Demo Scene")]
    public static void CreateDemoScene()
    {
        if (!Application.isBatchMode && !EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo()) return;
        EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        var root = new GameObject("UDP Demo");
        root.AddComponent<ArmWrestlingUdpClient>();
        var demo = root.AddComponent<ArmWrestlingUdpDemo>();
        var pivot = new GameObject("Pivot").transform;
        pivot.position = new Vector3(1.3f, -.7f, 0);
        demo.pivot = pivot;
        Primitive("Arm", PrimitiveType.Cube, pivot, new Vector3(0, .9f, 0), new Vector3(.22f, 1.8f, .22f), new Color(1, .65f, .15f));
        Primitive("Joint", PrimitiveType.Sphere, pivot, Vector3.zero, Vector3.one * .32f, new Color(.25f, .7f, 1));
        Primitive("Table", PrimitiveType.Cube, null, new Vector3(1.3f, -.95f, 0), new Vector3(3, .15f, 1), new Color(.16f, .3f, .45f));
        var camera = new GameObject("Main Camera").AddComponent<Camera>();
        camera.tag = "MainCamera";
        camera.transform.position = new Vector3(0, .5f, -8);
        camera.orthographic = true;
        camera.orthographicSize = 2.5f;
        camera.clearFlags = CameraClearFlags.SolidColor;
        camera.backgroundColor = new Color(.035f, .055f, .09f);
        var light = new GameObject("Light").AddComponent<Light>();
        light.type = LightType.Directional;
        light.transform.rotation = Quaternion.Euler(40, -30, 0);
        RenderSettings.ambientLight = Color.gray;
        Directory.CreateDirectory("Assets/Scenes");
        EditorSceneManager.SaveScene(EditorSceneManager.GetActiveScene(), "Assets/Scenes/UdpDemo.unity");
        Selection.activeGameObject = root;
        Debug.Log("UDP demo scene saved: Assets/Scenes/UdpDemo.unity");
    }

    static void Primitive(string name, PrimitiveType type, Transform parent, Vector3 position, Vector3 scale, Color color)
    {
        var item = GameObject.CreatePrimitive(type);
        item.name = name;
        item.transform.SetParent(parent, false);
        item.transform.localPosition = position;
        item.transform.localScale = scale;
        string folder = "Assets/ArmWrestling/Generated";
        Directory.CreateDirectory(folder);
        var material = AssetDatabase.LoadAssetAtPath<Material>(folder + "/" + name + ".mat");
        if (material == null)
        {
            material = new Material(Shader.Find("Standard"));
            material.color = color;
            AssetDatabase.CreateAsset(material, folder + "/" + name + ".mat");
        }
        item.GetComponent<Renderer>().sharedMaterial = material;
    }

    static void Require(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }

    public static void RunProtocolChecks()
    {
        ArmWrestlingUdpClient.StatusMessage status;
        string prefix = "{\"v\":1,\"type\":\"status\",\"seq\":0,\"mode\":\"stop\",\"reason\":\"telemetry_missing\"";
        Require(ArmWrestlingUdpClient.TryDecode(Encoding.UTF8.GetBytes(prefix + ",\"angle_rad\":null}"), out status)
                && !status.angle_rad.HasValue, "null angle must stay null");
        Require(ArmWrestlingUdpClient.TryDecode(Encoding.UTF8.GetBytes(prefix + ",\"angle_rad\":0}"), out status)
                && status.angle_rad == 0, "numeric zero must be valid");
        Require(ArmWrestlingUdpClient.TryDecode(Encoding.UTF8.GetBytes(prefix + ",\"angle_rad\":-4e-1}"), out status)
                && Math.Abs(status.angle_rad.Value + .4) < 1e-9, "signed scientific number");
        Require(!ArmWrestlingUdpClient.TryDecode(Encoding.UTF8.GetBytes(prefix + "}"), out status), "missing angle must be rejected");
        Require(!ArmWrestlingUdpClient.TryDecode(Encoding.UTF8.GetBytes("not json"), out status), "malformed JSON");
        Require(!ArmWrestlingUdpClient.TryDecode(new byte[8193], out status), "oversize datagram");
        Require(!ArmWrestlingUdpClient.TryDecode(Encoding.UTF8.GetBytes(prefix.Replace("\"v\":1", "\"v\":2") + ",\"angle_rad\":0}"), out status), "wrong version");
        Require(!ArmWrestlingUdpClient.TryDecode(Encoding.UTF8.GetBytes(prefix.Replace("\"seq\":0", "\"seq\":-1") + ",\"angle_rad\":0}"), out status), "negative seq");
        Debug.Log("UDP_PROTOCOL_CHECKS_PASS (8 checks)");
    }

    // CLI opt-in only. SessionState survives the real Play/domain-reload cycle.
    public static void RunIntegration()
    {
        StartCheck(true);
    }

    public static void RunCharacterCheck()
    {
        StartCheck(false);
    }

    static void StartCheck(bool createScene)
    {
        RunProtocolChecks();
        if (createScene) CreateDemoScene();
        else EditorSceneManager.OpenScene("Assets/Scenes/UdpDemo.unity");
        string results = Path.GetFullPath(Path.Combine(Application.dataPath, "../TestResults"));
        string[] args = Environment.GetCommandLineArgs();
        int resultIndex = Array.IndexOf(args, "-armUdpResults");
        if (resultIndex >= 0 && resultIndex + 1 < args.Length)
            results = Path.GetFullPath(args[resultIndex + 1]);
        Directory.CreateDirectory(results);
        SessionState.SetString(ResultsKey, results);
        SessionState.SetInt(CommandKey, 0);
        SessionState.SetBool(ActiveKey, true);
        EditorApplication.EnterPlaymode();
    }

    [Serializable] sealed class Command { public int id; public string action; public string state; public string character_id; }
    [Serializable] sealed class Sample
    {
        public double time;
        public bool playing, fresh, validAngle;
        public int port, sent, received, ignored, commandId;
        public string characterId = "";
        public long seq;
        public string match = "", mode = "", reason = "", statusJson = "", updateJson = "";
        public double angle;
        public float poseDegrees, poseErrorDegrees;
    }

    static void Tick()
    {
        if (!SessionState.GetBool(ActiveKey, false) || EditorApplication.isPlayingOrWillChangePlaymode != EditorApplication.isPlaying
            || EditorApplication.timeSinceStartup < nextSample) return;
        nextSample = EditorApplication.timeSinceStartup + .1;
        string results = SessionState.GetString(ResultsKey, "");
        try
        {
            var client = EditorApplication.isPlaying ? UnityEngine.Object.FindObjectOfType<ArmWrestlingUdpClient>() : null;
            string commandPath = Path.Combine(results, "command.json");
            if (File.Exists(commandPath))
            {
                var command = JsonUtility.FromJson<Command>(File.ReadAllText(commandPath));
                if (command.id > SessionState.GetInt(CommandKey, 0))
                {
                    SessionState.SetInt(CommandKey, command.id);
                    if (command.action == "state" && client != null) client.SetMatchState(command.state);
                    else if (command.action == "character" && client != null) client.characterId = command.character_id;
                    else if (command.action == "stop") EditorApplication.ExitPlaymode();
                    else if (command.action == "play") EditorApplication.EnterPlaymode();
                    else if (command.action == "reload") EditorUtility.RequestScriptReload();
                    else if (command.action == "quit")
                    {
                        SessionState.SetBool(ActiveKey, false);
                        EditorApplication.Exit(0);
                        return;
                    }
                }
            }
            var sample = new Sample { time = EditorApplication.timeSinceStartup, playing = client != null };
            if (client != null)
            {
                sample.fresh = client.HasFreshStatus;
                sample.commandId = SessionState.GetInt(CommandKey, 0);
                sample.characterId = client.characterId;
                sample.validAngle = client.HasValidAngle;
                sample.port = client.LocalPort;
                sample.sent = client.SentCount;
                sample.received = client.ReceivedCount;
                sample.ignored = client.IgnoredCount;
                sample.seq = client.Status == null ? -1 : client.Status.seq;
                sample.match = client.MatchState;
                sample.mode = client.Status == null ? "" : client.Status.mode;
                sample.reason = client.Status == null ? "" : client.Status.reason;
                sample.angle = client.LastAngleRad;
                sample.statusJson = client.LastStatusJson;
                sample.updateJson = client.LastUpdateJson;
                var demo = client.GetComponent<ArmWrestlingUdpDemo>();
                sample.poseDegrees = demo.AppliedDegrees;
                sample.poseErrorDegrees = Quaternion.Angle(demo.pivot.localRotation,
                    demo.ZeroRotation * Quaternion.AngleAxis((float)client.LastAngleRad * Mathf.Rad2Deg * demo.angleSign, demo.localAxis.normalized));
            }
            File.AppendAllText(Path.Combine(results, "samples.jsonl"), JsonUtility.ToJson(sample) + "\n", new UTF8Encoding(false));
        }
        catch (Exception error)
        {
            Debug.LogException(error);
            SessionState.SetBool(ActiveKey, false);
            EditorApplication.Exit(2);
        }
    }
}
