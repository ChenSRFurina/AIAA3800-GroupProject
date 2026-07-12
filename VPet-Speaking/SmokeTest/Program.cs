using VPet.Plugin.Speaking;

var text = GetMessage.get_message(GetMessage.TestSample);
Console.WriteLine($"Synthesizing: {text}");

var tts = XunfeiTtsClient.FromConfigNearAssembly();
var audio = await tts.SynthesizeAsync(text);
var outPath = Path.Combine(AppContext.BaseDirectory, "test_speak.mp3");
await File.WriteAllBytesAsync(outPath, audio);
Console.WriteLine($"OK: {audio.Length} bytes -> {outPath}");
