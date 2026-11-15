using UnityEngine;

// Test-only UI and angle mapping; the VR team can replace this entire component.
[RequireComponent(typeof(ArmWrestlingUdpClient))]
[DefaultExecutionOrder(100)]
public sealed class ArmWrestlingUdpDemo : MonoBehaviour
{
    public Transform pivot;
    public Vector3 localAxis = Vector3.forward;
    public float angleSign = 1;
    public float AppliedDegrees { get; private set; }
    public Quaternion ZeroRotation { get; private set; }
    ArmWrestlingUdpClient client;

    void Awake()
    {
        client = GetComponent<ArmWrestlingUdpClient>();
        if (pivot != null) ZeroRotation = pivot.localRotation;
    }

    void LateUpdate()
    {
        if (pivot == null || localAxis.sqrMagnitude < .0001f || !client.HasValidAngle) return;
        AppliedDegrees = (float)client.LastAngleRad * Mathf.Rad2Deg * angleSign;
        pivot.localRotation = ZeroRotation * Quaternion.AngleAxis(AppliedDegrees, localAxis.normalized);
    }

    void OnGUI()
    {
        GUILayout.BeginArea(new Rect(16, 16, 420, 300), GUI.skin.box);
        GUILayout.Label("Arm wrestling / UDP V0.1");
        GUILayout.Label("PC " + client.host + ":" + client.port + " | local port " + client.LocalPort);
        GUILayout.Label("Sending: " + client.characterId + " / " + client.MatchState);
        GUILayout.BeginHorizontal();
        foreach (string state in new[] { "waiting", "running", "paused", "finished" })
            if (GUILayout.Button(state)) client.SetMatchState(state);
        GUILayout.EndHorizontal();
        GUILayout.Label(client.HasFreshStatus ? "PC status: connected" : "PC status: waiting / timeout");
        GUILayout.Label(client.HasValidAngle
            ? "Angle: " + client.LastAngleRad.ToString("F4") + " rad / " + AppliedDegrees.ToString("F2") + " deg"
            : "Angle: invalid / holding last pose (" + AppliedDegrees.ToString("F2") + " deg)");
        if (client.Status != null)
            GUILayout.Label("mode=" + client.Status.mode + " | reason=" + client.Status.reason
                + (client.HasFreshStatus ? "" : " (stale)"));
        GUILayout.Label("Sent " + client.SentCount + " / received " + client.ReceivedCount + " / ignored " + client.IgnoredCount);
        if (!string.IsNullOrEmpty(client.NetworkError)) GUILayout.Label(client.NetworkError);
        GUILayout.Label("Negative rad = winning direction; enabled = PC calculation state.");
        GUILayout.EndArea();
    }
}
