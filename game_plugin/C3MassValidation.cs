// Explicitly armed, isolated-save replay harness. No staging or throttle control.
using System;
using System.Collections;
using System.IO;
using System.Security.Cryptography;
using UnityEngine;

[KSPAddon(KSPAddon.Startup.MainMenu, true)]
public class C3MassValidation : MonoBehaviour
{
    private const string Folder = "C3MassValidation20261010";
    private bool armed;
    private bool focused;
    private int targetIndex = -1;
    private float loadedAt = -1;
    private string dir;
    private bool previousBackground;
    private bool padInspection;
    private bool editorInspection;
    private string expectedVessel = "1T_Test";
    private string craftPath;

    public void Awake() { DontDestroyOnLoad(this); }
    public IEnumerator Start()
    {
        dir = Path.Combine(KSPUtil.ApplicationRootPath, "GameData/C3MassValidation");
        string request = Path.Combine(dir, "replay.arm");
        string padRequest = Path.Combine(dir, "pad.arm");
        string editorRequest = Path.Combine(dir, "editor.arm");
        if (File.Exists(request) && File.Exists(padRequest)) { Status("ambiguous_request"); yield break; }
        padInspection = File.Exists(padRequest);
        editorInspection = File.Exists(editorRequest);
        if (editorInspection && (File.Exists(request) || padInspection)) { Status("ambiguous_request"); yield break; }
        if (editorInspection) { request = editorRequest; expectedVessel = "Neutrons I 2630"; }
        if (padInspection) { request = padRequest; expectedVessel = "Neutrons I 2630"; }
        if (!File.Exists(request)) yield break;
        craftPath = Path.Combine(KSPUtil.ApplicationRootPath, "saves/" + Folder + "/Ships/VAB/Neutrons I 2630.craft");
        string path = (padInspection || editorInspection) ? craftPath : Path.Combine(KSPUtil.ApplicationRootPath, "saves/" + Folder + "/quicksave.sfs");
        if (!File.Exists(path)) { Status("missing_test_save"); yield break; }
        string expected = File.ReadAllText(request).Trim();
        string actual;
        using (SHA256 hash = SHA256.Create())
            actual = BitConverter.ToString(hash.ComputeHash(File.ReadAllBytes(path))).Replace("-", "");
        if (!string.Equals(expected, actual, StringComparison.OrdinalIgnoreCase))
        { Status("test_save_hash_mismatch"); yield break; }
        File.Move(request, Path.Combine(dir, "replay.consumed." + DateTime.UtcNow.ToString("yyyyMMddHHmmssfffffff")));
        armed = true;
        previousBackground = Application.runInBackground;
        Application.runInBackground = true;
        GameEvents.onLevelWasLoadedGUIReady.Add(SceneReady);
        yield return new WaitForSecondsRealtime(3);
        LoadIsolatedGame();
    }

    private void LoadIsolatedGame()
    {
        try
        {
            Game game = GamePersistence.LoadGame("quicksave", Folder, true, true);
            if (game == null) { Status("test_save_load_failed"); return; }
            if (!padInspection && !editorInspection) for (int i = 0; i < game.flightState.protoVessels.Count; i++)
                if (game.flightState.protoVessels[i].vesselName == "1T_Test")
                {
                    if (targetIndex >= 0) { Status("ambiguous_target_vessel"); return; }
                    targetIndex = i;
                }
            if (!padInspection && !editorInspection && targetIndex < 0) { Status("target_vessel_missing"); return; }
            HighLogic.CurrentGame = game;
            HighLogic.SaveFolder = Folder;
            game.startScene = GameScenes.SPACECENTER;
            Status("loading_isolated_spacecenter");
            game.Start();
        }
        catch (Exception error) { Status("load_exception: " + error); }
    }

    private void SceneReady(GameScenes scene)
    {
        if (!armed || HighLogic.SaveFolder != Folder) return;
        if (scene == GameScenes.SPACECENTER && !focused)
        {
            focused = true;
            StartCoroutine(Focus());
        }
    }

    private IEnumerator Focus()
    {
        yield return new WaitForSecondsRealtime(3);
        if (editorInspection)
        {
            Status("loading_baseline_in_editor");
            EditorDriver.StartAndLoadVessel(craftPath, EditorFacility.VAB);
            StartCoroutine(EditorSaveCheck());
        }
        else if (padInspection)
        {
            Status("loading_baseline_on_pad");
            // Even an uncrewed ship requires a non-null manifest: the game
            // unconditionally calls AssignCrewToVessel at IL offset 0x3CB.
            FlightDriver.StartWithNewLaunch(craftPath, "Squad/Flags/default", "LaunchPad", new VesselCrewManifest());
        }
        else
        {
            Status("focusing_existing_vessel");
            FlightDriver.StartAndFocusVessel("quicksave", targetIndex);
        }
    }

    private IEnumerator EditorSaveCheck()
    {
        float start = Time.realtimeSinceStartup;
        while (EditorLogic.fetch == null || EditorLogic.fetch.ship == null || EditorLogic.fetch.ship.Count == 0)
        {
            if (Time.realtimeSinceStartup - start > 90) { Status("editor_load_timeout"); Application.Quit(); yield break; }
            yield return new WaitForSecondsRealtime(1);
        }
        yield return new WaitForSecondsRealtime(12);
        ShipConstruct ship = EditorLogic.fetch.ship;
        if (HighLogic.SaveFolder != Folder || ship.shipName != expectedVessel) { Status("unexpected_editor_ship"); Application.Quit(); yield break; }
        GameEvents.onAboutToSaveShip.Fire(ship);
        ShipConstruction.SaveShip(ship, ship.shipName);
        Status("editor_saved: " + ship.shipName);
        yield return new WaitForSecondsRealtime(8);
        Status("replay_complete");
        armed = false;
        Application.Quit();
    }

    public void Update()
    {
        if (!armed || HighLogic.SaveFolder != Folder || !HighLogic.LoadedSceneIsFlight) return;
        Vessel v = FlightGlobals.ActiveVessel;
        if (v == null || !v.loaded || v.packed) return;
        if (v.vesselName != expectedVessel)
        {
            Status("unexpected_vessel: " + v.vesselName);
            armed = false;
            Application.Quit();
            return;
        }
        if (loadedAt < 0)
        {
            loadedAt = Time.realtimeSinceStartup;
            Status("flight_ready: " + v.vesselName + " mass_t=" + v.GetTotalMass().ToString("R", System.Globalization.CultureInfo.InvariantCulture));
        }
        if (Time.realtimeSinceStartup - loadedAt >= 30)
        {
            Status("replay_complete");
            armed = false;
            Application.Quit();
        }
    }

    private void Status(string value)
    {
        Directory.CreateDirectory(dir);
        File.AppendAllText(Path.Combine(dir, "status.log"), DateTime.UtcNow.ToString("O") + " " + value + Environment.NewLine);
        Debug.Log("[C3MassValidation] " + value);
    }
    public void OnDestroy()
    {
        GameEvents.onLevelWasLoadedGUIReady.Remove(SceneReady);
        if (armed || loadedAt >= 0) Application.runInBackground = previousBackground;
    }
}
