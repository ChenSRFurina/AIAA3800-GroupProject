using VPet.Plugin.Speaking;

var text = GetMessage.get_message(GetMessage.TestSample);
Console.WriteLine($"Synthesizing: {text}");

var f5 = F5TtsClient.FromConfigNearAssembly();
Console.WriteLine($"F5 endpoint: {f5.Host}:{f5.Port}, nfe={f5.NfeStep}");

if (!await f5.PingAsync())
{
    Console.WriteLine("ERROR: F5 server not reachable.");
    Console.WriteLine("Start it first:");
    Console.WriteLine("  python Local_model/Fast_generating/start_server.py");
    return 1;
}

var sw = System.Diagnostics.Stopwatch.StartNew();
var audio = await f5.SynthesizeAsync(text);
sw.Stop();

var outPath = Path.Combine(AppContext.BaseDirectory, "test_speak_f5.wav");
await File.WriteAllBytesAsync(outPath, audio);
Console.WriteLine($"OK: {audio.Length} bytes in {sw.ElapsedMilliseconds} ms -> {outPath}");
return 0;
