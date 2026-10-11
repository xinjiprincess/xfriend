param([Parameter(Mandatory=$true)][string]$WakeWord)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
    Add-Type -AssemblyName System.Speech
    Add-Type -ReferencedAssemblies System.Speech -TypeDefinition @'
using System;
using System.Speech.Recognition;
using System.Threading;
public static class XfriendVoice {
    static readonly object Gate = new object();
    static SpeechRecognitionEngine engine;
    static Grammar wake;
    static DictationGrammar dictation;
    static DateTime until = DateTime.MinValue;
    static string phrase;
    static void Emit(string kind, string text) {
        text = (text ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", " ").Replace("\n", " ");
        Console.WriteLine("{\"kind\":\"" + kind + "\",\"text\":\"" + text + "\"}");
        Console.Out.Flush();
    }
    static void Reset() {
        until = DateTime.MinValue;
        dictation.Enabled = false;
        wake.Enabled = true;
    }
    public static void Run(string word) {
        phrase = word;
        RecognizerInfo info = null;
        foreach (var candidate in SpeechRecognitionEngine.InstalledRecognizers()) {
            if (candidate.Culture.TwoLetterISOLanguageName == "zh") { info = candidate; break; }
        }
        if (info == null) { Emit("error", "missing-recognizer"); return; }
        using (engine = new SpeechRecognitionEngine(info)) {
            var builder = new GrammarBuilder(word); builder.Culture = info.Culture;
            wake = new Grammar(builder);
            dictation = new DictationGrammar(); dictation.Enabled = false;
            engine.LoadGrammar(wake); engine.LoadGrammar(dictation);
            engine.SpeechRecognized += (sender, e) => {
                lock (Gate) {
                    if (e.Result.Confidence < 0.55f) return;
                    if (e.Result.Grammar == wake) {
                        wake.Enabled = false; dictation.Enabled = true;
                        until = DateTime.UtcNow.AddSeconds(12); Emit("wake", phrase);
                    } else if (DateTime.UtcNow < until) {
                        string text = e.Result.Text.Trim();
                        if (text.Length > 0) { Reset(); Emit("command", text); Emit("ready", phrase); }
                    }
                }
            };
            engine.SetInputToDefaultAudioDevice();
            engine.RecognizeAsync(RecognizeMode.Multiple);
            Emit("ready", phrase);
            while (true) {
                Thread.Sleep(200);
                lock (Gate) { if (until != DateTime.MinValue && DateTime.UtcNow >= until) { Reset(); Emit("ready", phrase); } }
            }
        }
    }
}
'@
    [XfriendVoice]::Run($WakeWord)
} catch {
    [Console]::WriteLine('{"kind":"error","text":"microphone-or-recognizer"}')
    exit 1
}
