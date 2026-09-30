using System;
using RimWorld;
using Verse;

namespace RimbisectMapFail
{
    // Scenario parts are the one map-generation hook the game does not wrap in try/catch,
    // so this reaches ErrorWhileGeneratingMap and the game goes back to the main menu.
    public class ScenPart_FailMap : ScenPart
    {
        public override void PostMapGenerate(Map map)
        {
            throw new InvalidOperationException("rimbisect acceptance: map generation fails on purpose");
        }
    }
}
