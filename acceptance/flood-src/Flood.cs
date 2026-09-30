using Verse;

namespace RimbisectFlood
{
    // Reaches RimWorld's 10,000 message limit while mods load, before the probe does. The
    // game logs nothing after that, so a later error is only seen if the probe turns
    // logging back on.
    public class FloodMod : Mod
    {
        public FloodMod(ModContentPack content) : base(content)
        {
            for (int i = 0; i < 10001; i++)
            {
                Log.Warning("rimbisect acceptance flood " + i);
            }
        }
    }

    // A second flood once the probe is running, then an error of its own. It is only seen if
    // the game's message count is kept from reaching the limit while the flood happens.
    [StaticConstructorOnStartup]
    public static class LateFlood
    {
        static LateFlood()
        {
            for (int i = 0; i < 10001; i++)
            {
                Log.Warning("rimbisect acceptance late flood " + i);
            }
            Log.Error("rimbisectLateFloodError");
        }
    }
}
