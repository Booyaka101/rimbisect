using Verse;

namespace RimbisectFrameFail
{
    // Map.MapUpdate calls the sky manager without a try/catch of its own, so from the next
    // frame on every frame throws before the game components update.
    public class MapComponent_FrameFail : MapComponent
    {
        public MapComponent_FrameFail(Map map) : base(map)
        {
        }

        public override void MapComponentUpdate()
        {
            map.skyManager = null;
        }
    }
}
