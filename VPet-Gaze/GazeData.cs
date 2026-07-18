using System.Text.Json.Serialization;

namespace VPet.Plugin.Gaze;

public sealed class GazeData
{
    [JsonPropertyName("valid")]
    public bool Valid { get; set; }

    [JsonPropertyName("gaze_x")]
    public double GazeX { get; set; }

    [JsonPropertyName("gaze_y")]
    public double GazeY { get; set; }

    [JsonPropertyName("screen_x")]
    public double ScreenX { get; set; }

    [JsonPropertyName("screen_y")]
    public double ScreenY { get; set; }

    [JsonPropertyName("timestamp")]
    public double Timestamp { get; set; }
}
