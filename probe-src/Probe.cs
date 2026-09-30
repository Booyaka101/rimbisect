using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading;
using UnityEngine;
using UnityEngine.SceneManagement;
using Verse;

namespace RimbisectProbe
{
    // Inert unless rimbisect launched the game: it sets RIMBISECT_EVENTS to the file the
    // trial watches, and everything is written there as JSON lines.
    public static class Probe
    {
        public static readonly string EventsPath = Environment.GetEnvironmentVariable("RIMBISECT_EVENTS");
        public static bool Active => !string.IsNullOrEmpty(EventsPath);

        public static readonly float Settle = ReadSettle();

        private static float ReadSettle()
        {
            float value;
            string raw = Environment.GetEnvironmentVariable("RIMBISECT_SETTLE");
            return float.TryParse(raw, NumberStyles.Float, CultureInfo.InvariantCulture, out value) ? value : 20f;
        }

        private static readonly object Gate = new object();
        private static bool started;
        private static bool sawPlayScene;
        private static bool gaveUp;
        private static Timer watchdog;

        private static StreamWriter events;
        private static int messagesSeen;

        private static readonly FieldInfo LogCapped =
            typeof(Log).GetField("reachedMaxMessagesLimit", BindingFlags.NonPublic | BindingFlags.Static);
        private static readonly FieldInfo LogCount =
            typeof(Log).GetField("messageCount", BindingFlags.NonPublic | BindingFlags.Static);

        // Titles of the dialogs the game shows when it abandons map generation or loading.
        private static readonly string[] GiveUpTitleKeys =
        {
            "ErrorWhileGeneratingMapTitle", "ErrorWhileLoadingMapTitle",
            "ErrorWhileLoadingAssetsTitle", "RecoveredFromErrorsDialogTitle",
        };

        public static void Start()
        {
            if (!Active || started)
            {
                return;
            }
            started = true;
            Application.logMessageReceivedThreaded += OnLog;
            SceneManager.sceneLoaded += OnSceneLoaded;
            watchdog = new Timer(_ => Watch(), null, 500, 500);
            // Errors logged before this mod's constructor ran (assembly loading, earlier mod
            // constructors) are only in Verse's own queue, which has no lock to take.
            try
            {
                foreach (LogMessage message in Log.Messages.ToList())
                {
                    if (message.type == LogMessageType.Error)
                    {
                        Emit("error", message.text, message.repeats);
                    }
                }
            }
            catch (Exception)
            {
                Emit("error", "rimbisect probe: could not read the errors logged before it loaded", 1);
            }
            Emit("started", string.Join("\n", LoadedModManager.RunningModsListForReading.Select(m => m.PackageId)), 1);
            HoldOffLogCap();
            KeepLogging();
        }

        private static void Watch()
        {
            KeepLogging();
            CheckErrorDialogs();
        }

        // After 10,000 messages RimWorld stops logging until someone clears the debug log, so
        // an error that comes after a flood of unrelated ones would never be seen. Its counter
        // is set back every 5,000 messages so it never gets there; KeepLogging switches
        // logging back on if it did anyway.
        private static void HoldOffLogCap()
        {
            try
            {
                LogCount?.SetValue(null, 0);
            }
            catch (Exception)
            {
                // KeepLogging still catches the cap, half a second late.
            }
        }

        private static void KeepLogging()
        {
            try
            {
                if (LogCapped != null && (bool)LogCapped.GetValue(null))
                {
                    Log.ResetMessageCount();
                    Emit("log_reset", null, 1);
                }
            }
            catch (Exception)
            {
                // Logging stays off; the trial still ends on done or the timeout.
            }
        }

        // When map generation throws, or loading fails and the game falls back to Core alone,
        // the game opens an error dialog and waits for a click that never comes; -quicktest
        // does not try again. Polled from a timer thread because loading can fail before any
        // main-thread hook of this mod exists.
        private static void CheckErrorDialogs()
        {
            try
            {
                WindowStack windows = Current.Root?.uiRoot?.windows;
                if (gaveUp || windows == null)
                {
                    return;
                }
                foreach (Window window in windows.Windows)
                {
                    if (window is Dialog_MessageBox box && GiveUpTitleKeys.Any(key => box.title == key.Translate().RawText))
                    {
                        GiveUp(box.title + ": " + box.text.RawText);
                        return;
                    }
                }
            }
            catch (Exception)
            {
                // The main thread changed the window list mid-read; the next tick looks again.
            }
        }

        private static void OnSceneLoaded(Scene scene, LoadSceneMode mode)
        {
            if (scene.name == "Play")
            {
                sawPlayScene = true;
            }
            else if (scene.name == "Entry" && sawPlayScene)
            {
                GiveUp("the game went back to the main menu");
            }
        }

        private static void GiveUp(string text)
        {
            lock (Gate)
            {
                if (gaveUp)
                {
                    return;
                }
                gaveUp = true;
            }
            watchdog?.Dispose();
            Emit("gave_up", text, 1);
        }

        private static void OnLog(string condition, string stackTrace, LogType type)
        {
            if (Interlocked.Increment(ref messagesSeen) % 5000 == 0)
            {
                HoldOffLogCap();
            }
            if (type == LogType.Exception)
            {
                Emit("error", condition + "\n" + stackTrace, 1);
            }
            else if (type == LogType.Error || type == LogType.Assert)
            {
                Emit("error", condition, 1);
            }
        }

        public static void Emit(string kind, string text, int repeats)
        {
            if (!Active)
            {
                return;
            }
            var line = new StringBuilder("{\"event\":\"").Append(kind).Append('"');
            if (text != null)
            {
                line.Append(",\"text\":");
                AppendJsonString(line, text);
            }
            if (repeats > 1)
            {
                line.Append(",\"repeats\":").Append(repeats);
            }
            line.Append("}\n");
            lock (Gate)
            {
                try
                {
                    if (events == null)
                    {
                        var stream = new FileStream(EventsPath, FileMode.Append, FileAccess.Write,
                            FileShare.ReadWrite | FileShare.Delete);
                        events = new StreamWriter(stream, new UTF8Encoding(false)) { AutoFlush = true };
                    }
                    events.Write(line.ToString());
                }
                catch (Exception)
                {
                    // Nowhere left to report this; rimbisect notices the missing events.
                    events = null;
                }
            }
        }

        private static void AppendJsonString(StringBuilder sb, string value)
        {
            sb.Append('"');
            foreach (char c in value)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (c < ' ')
                        {
                            sb.Append("\\u").Append(((int)c).ToString("x4"));
                        }
                        else
                        {
                            sb.Append(c);
                        }
                        break;
                }
            }
            sb.Append('"');
        }
    }

    public class ProbeMod : Mod
    {
        public ProbeMod(ModContentPack content) : base(content)
        {
            Probe.Start();
        }
    }

    [StaticConstructorOnStartup]
    public static class ProbeLoaded
    {
        static ProbeLoaded()
        {
            Probe.Emit("loaded", null, 1);
        }
    }

    public class ProbeMapComponent : MapComponent
    {
        private float readyAt = -1f;
        private int ticks;
        private bool done;
        private readonly HashSet<string> closed = new HashSet<string>();

        public ProbeMapComponent(Map map) : base(map)
        {
        }

        public override void MapComponentTick()
        {
            if (readyAt >= 0f)
            {
                ticks++;
            }
        }

        // Settle is game time, since that is what mods' tick errors need; a game too slow to
        // get there still ends after three times as long in real time.
        public override void MapComponentUpdate()
        {
            if (!Probe.Active || done)
            {
                return;
            }
            if (readyAt < 0f)
            {
                readyAt = Time.realtimeSinceStartup;
                Log.Message("RIMBISECT_MAP_READY");
                Probe.Emit("map_ready", null, 1);
                return;
            }
            if (ticks < Probe.Settle * 60f && Time.realtimeSinceStartup - readyAt < Math.Max(60f, Probe.Settle * 3f))
            {
                KeepPlaying();
                return;
            }
            done = true;
            Log.Message("RIMBISECT_DONE");
            Probe.Emit("done", ticks.ToString(CultureInfo.InvariantCulture), 1);
            Root.Shutdown();
        }

        // Some mods open a window that pauses the game on a new colony, or start it paused,
        // and nobody is there to close it. Settle would then pass with no game time at all.
        // Superfast only gets the settle ticks done sooner; a slow list runs as fast as it can.
        private void KeepPlaying()
        {
            TickManager time = Find.TickManager;
            if (time.Paused)
            {
                foreach (Window window in Find.WindowStack.Windows.Where(w => w.forcePause).ToList())
                {
                    string name = window.GetType().FullName;
                    if (Find.WindowStack.TryRemove(window, false) && closed.Add(name))
                    {
                        Log.Message("RIMBISECT_CLOSED " + name);
                        Probe.Emit("closed", name, 1);
                    }
                }
            }
            if (time.CurTimeSpeed != TimeSpeed.Superfast)
            {
                time.CurTimeSpeed = TimeSpeed.Superfast;
            }
        }
    }
}
