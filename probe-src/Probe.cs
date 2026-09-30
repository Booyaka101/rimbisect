using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using UnityEngine;
using UnityEngine.SceneManagement;
using Verse;

namespace RimbisectProbe
{
    // Inert unless rimbisect launched the game: it sets RIMBISECT_EVENTS to the file the
    // trial watches. Everything is written there as JSON lines, because RimWorld switches
    // the Unity log off after 10,000 messages and a big modlist can get there.
    public static class Probe
    {
        public static readonly string EventsPath = Environment.GetEnvironmentVariable("RIMBISECT_EVENTS");
        public static bool Active => !string.IsNullOrEmpty(EventsPath);

        public static float Settle
        {
            get
            {
                float value;
                string raw = Environment.GetEnvironmentVariable("RIMBISECT_SETTLE");
                return float.TryParse(raw, System.Globalization.NumberStyles.Float,
                    System.Globalization.CultureInfo.InvariantCulture, out value) ? value : 20f;
            }
        }

        private static readonly object Gate = new object();
        private static bool started;
        private static bool sawPlayScene;
        private static bool gaveUp;
        private static Timer watchdog;

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
            watchdog = new Timer(_ => CheckErrorDialogs(), null, 500, 500);
            // Errors logged before this mod's constructor ran (assembly loading, earlier mod
            // constructors) are only in Verse's own queue.
            foreach (LogMessage message in Log.Messages.ToList())
            {
                if (message.type == LogMessageType.Error)
                {
                    Emit("error", message.text, message.repeats);
                }
            }
            Emit("started", null, 1);
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
                    File.AppendAllText(EventsPath, line.ToString(), new UTF8Encoding(false));
                }
                catch (Exception)
                {
                    // Nowhere left to report this; rimbisect notices the missing events.
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
        private bool done;

        public ProbeMapComponent(Map map) : base(map)
        {
        }

        public override void FinalizeInit()
        {
            if (!Probe.Active || readyAt >= 0f)
            {
                return;
            }
            readyAt = Time.realtimeSinceStartup;
            Log.Message("RIMBISECT_MAP_READY");
            Probe.Emit("map_ready", null, 1);
        }

        public override void MapComponentUpdate()
        {
            if (readyAt < 0f || done || Time.realtimeSinceStartup - readyAt < Probe.Settle)
            {
                return;
            }
            done = true;
            Log.Message("RIMBISECT_DONE");
            Probe.Emit("done", null, 1);
            Root.Shutdown();
        }
    }
}
