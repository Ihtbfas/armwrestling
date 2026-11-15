using System;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Text;
using System.Xml;
using UnityEngine;

// Reusable V0.1 transport. No scene, UI, XR or control-policy dependencies.
public sealed class ArmWrestlingUdpClient : MonoBehaviour
{
    public string host = "127.0.0.1";
    public int port = 9102;
    public string characterId = "demo";
    [SerializeField] string matchState = "waiting";
    const double HeartbeatSeconds = .2, StatusTimeoutSeconds = 1.0;
    const long MaxSequence = 9007199254740991L;
    static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);
    static readonly DataContractJsonSerializer StatusSerializer =
        new DataContractJsonSerializer(typeof(StatusMessage));

    [Serializable]
    sealed class UpdateMessage
    {
        public int v = 1;
        public string type = "vr_update";
        public long seq;
        public string character_id;
        public string match_state;
    }

    // Required nullable double: JSON null, missing angle, and numeric zero differ.
    // System.Runtime.Serialization.Json is part of .NET, not a Unity package.
    [DataContract]
    public sealed class StatusMessage
    {
        [DataMember(IsRequired = true)] public int v;
        [DataMember(IsRequired = true)] public string type;
        [DataMember(IsRequired = true)] public long seq;
        [DataMember(IsRequired = true)] public double? angle_rad;
        [DataMember(IsRequired = true)] public string mode;
        [DataMember(IsRequired = true)] public string reason;
    }

    UdpClient socket;
    long nextSequence, lastSequence = -1;
    double nextSend, reopenAt, lastReceive = double.NegativeInfinity;
    public StatusMessage Status { get; private set; }
    public string MatchState { get { return matchState; } }
    public double LastAngleRad { get; private set; }
    public string NetworkError { get; private set; }
    public string LastStatusJson { get; private set; }
    public string LastUpdateJson { get; private set; }
    public int ReceivedCount { get; private set; }
    public int SentCount { get; private set; }
    public int IgnoredCount { get; private set; }
    public int LocalPort { get { return socket == null ? 0 : ((IPEndPoint)socket.Client.LocalEndPoint).Port; } }
    public bool HasFreshStatus
    {
        get { return socket != null && Status != null && Time.realtimeSinceStartupAsDouble - lastReceive < StatusTimeoutSeconds; }
    }
    public bool HasValidAngle { get { return HasFreshStatus && Status.angle_rad.HasValue; } }

    public static bool TryDecode(byte[] packet, out StatusMessage status)
    {
        status = null;
        if (packet == null || packet.Length == 0 || packet.Length > 8192) return false;
        try
        {
            // Reading through the end rejects trailing JSON/garbage as well.
            using (var reader = JsonReaderWriterFactory.CreateJsonReader(packet, XmlDictionaryReaderQuotas.Max))
            {
                var parsed = (StatusMessage)StatusSerializer.ReadObject(reader);
                if (reader.Read() || parsed == null || parsed.v != 1 || parsed.type != "status"
                    || parsed.seq < 0 || parsed.seq > MaxSequence
                    || (parsed.mode != "enabled" && parsed.mode != "stop")
                    || string.IsNullOrEmpty(parsed.reason)) return false;
                if (parsed.angle_rad.HasValue && (double.IsNaN(parsed.angle_rad.Value)
                    || double.IsInfinity(parsed.angle_rad.Value) || Math.Abs(parsed.angle_rad.Value) > 100)) return false;
                status = parsed;
                return true;
            }
        }
        catch (Exception error) when (error is SerializationException || error is XmlException
            || error is ArgumentException || error is FormatException || error is OverflowException)
        {
            return false;
        }
    }

    public void SetMatchState(string value)
    {
        if (value != "waiting" && value != "running" && value != "paused" && value != "finished")
            throw new ArgumentException("Use waiting, running, paused or finished.");
        matchState = value;
        nextSend = 0; // A button changes the full state; heartbeats continue afterwards.
    }

    void OnEnable()
    {
        Status = null;
        LastAngleRad = 0;
        ReceivedCount = SentCount = IgnoredCount = 0;
        LastStatusJson = LastUpdateJson = "";
        OpenSocket();
    }

    void OpenSocket()
    {
        CloseSocket();
        try
        {
            var address = IPAddress.Parse(host);
            if (address.AddressFamily != AddressFamily.InterNetwork) throw new ArgumentException("IPv4 host required.");
            if (string.IsNullOrEmpty(characterId) || characterId.Length > 64)
                throw new ArgumentException("characterId must contain 1..64 characters.");
            Utf8.GetBytes(characterId); // Reject malformed UTF-16 before any packets are sent.
            SetMatchState(matchState);
            socket = new UdpClient(new IPEndPoint(IPAddress.Loopback, 0));
            socket.Connect(new IPEndPoint(address, port)); // Also filters packets from other endpoints.
            socket.Client.Blocking = false;
            nextSequence = 0;
            lastSequence = -1;
            lastReceive = double.NegativeInfinity;
            nextSend = 0;
            NetworkError = "";
            Debug.Log("UDP open 127.0.0.1:" + LocalPort + " -> " + host + ":" + port);
        }
        catch (Exception error) when (error is SocketException || error is ArgumentException || error is FormatException)
        {
            CloseSocket();
            NetworkError = error.Message;
            reopenAt = Time.realtimeSinceStartupAsDouble + 2;
        }
    }

    void Update()
    {
        double now = Time.realtimeSinceStartupAsDouble;
        if (socket == null)
        {
            if (now >= reopenAt) OpenSocket();
            return;
        }
        // A new socket after silence permits a PC seq reset without ever treating
        // a duplicate/old packet in the current session as a fresh heartbeat.
        if (Status != null && now - lastReceive >= StatusTimeoutSeconds && lastSequence >= 0)
        {
            CloseSocket();
            reopenAt = now + 1.1; // Give the PC's old VR endpoint lock time to expire.
            NetworkError = "Status timeout; reconnecting";
            Debug.Log("UDP status timeout; holding last pose");
            return;
        }
        if (now >= nextSend)
        {
            nextSend = now + HeartbeatSeconds;
            try
            {
                string json = JsonUtility.ToJson(new UpdateMessage {
                    seq = nextSequence, character_id = characterId, match_state = matchState });
                byte[] bytes = Utf8.GetBytes(json);
                if (socket.Send(bytes, bytes.Length) == bytes.Length)
                {
                    nextSequence++;
                    SentCount++;
                    LastUpdateJson = json;
                }
            }
            catch (SocketException error) { NetworkError = error.SocketErrorCode.ToString(); }
        }
        // Bounded, nonblocking work on the main thread; no waiting or worker threads.
        for (int i = 0; i < 64; i++)
        {
            try
            {
                if (!socket.Client.Poll(0, SelectMode.SelectRead)) break;
                IPEndPoint source = null;
                byte[] packet = socket.Receive(ref source);
                StatusMessage message;
                if (!TryDecode(packet, out message) || message.seq <= lastSequence)
                {
                    IgnoredCount++;
                    continue;
                }
                bool changed = Status == null || Status.mode != message.mode || Status.reason != message.reason
                    || Status.angle_rad.HasValue != message.angle_rad.HasValue;
                Status = message;
                lastSequence = message.seq;
                lastReceive = now;
                ReceivedCount++;
                NetworkError = "";
                LastStatusJson = Utf8.GetString(packet);
                if (message.angle_rad.HasValue) LastAngleRad = message.angle_rad.Value;
                if (changed) Debug.Log("UDP status " + LastStatusJson);
            }
            catch (SocketException error)
            {
                // Windows may report an ICMP port-unreachable while PC is down.
                if (error.SocketErrorCode != SocketError.WouldBlock)
                    NetworkError = error.SocketErrorCode.ToString();
                break;
            }
        }
    }

    void CloseSocket()
    {
        if (socket != null) socket.Close();
        socket = null;
    }

    void OnDisable() { CloseSocket(); }
    void OnDestroy() { CloseSocket(); }
    void OnApplicationQuit() { CloseSocket(); }
}
