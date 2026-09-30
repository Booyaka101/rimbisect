using Verse;

namespace RimbisectSaveFail
{
    // LoadedGame only runs for a save, so a new -quicktest colony never shows this error.
    public class GameComponent_SaveFail : GameComponent
    {
        public GameComponent_SaveFail(Game game)
        {
        }

        public override void LoadedGame()
        {
            Log.Error("rimbisect acceptance: this error only happens in a loaded save");
        }
    }
}
