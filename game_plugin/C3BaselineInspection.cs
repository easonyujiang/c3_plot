// One-shot, hash-bound isolated inspection; flight requires an explicit request and pad gates.
using System;
using System.Collections;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using UnityEngine;

[KSPAddon(KSPAddon.Startup.MainMenu, true)]
public class C3BaselineInspection : MonoBehaviour
{
    private string dir, folder, craftName, craftPath;
    private bool active, loading, rebuild, fly, flying;
    private object masterCore, ascent;
    private StreamWriter flightCsv;
    private double launchedUT, lastRecord = -1, stableSince = -1;
    private float launchWall;
    private double measuredPayload;
    private double targetPayload = 1.0;
    private bool payloadShielded;
    private int payloadCount;
    private bool ballastAdjusted;
    private float ballastAdjustedAt;
    private float started, readyAt = -1;
    private bool oldBackground;
    private static string F(double x) { return x.ToString("R", CultureInfo.InvariantCulture); }
    private static string Hash(string p)
    {
        using (SHA256 h = SHA256.Create())
            return BitConverter.ToString(h.ComputeHash(File.ReadAllBytes(p))).Replace("-", "");
    }
    public void Awake() { DontDestroyOnLoad(this); }
    public IEnumerator Start()
    {
        dir = Path.Combine(KSPUtil.ApplicationRootPath, "PluginData/C3Baseline");
        string request = Path.Combine(dir, "inspect.request");
        if (!File.Exists(request)) yield break;
        // Do not compete with an old replay request or another loaded save.
        string legacy = Path.Combine(KSPUtil.ApplicationRootPath, "GameData/C3MassValidation");
        foreach (string mode in new string[] { "replay", "pad", "editor" })
            if (File.Exists(Path.Combine(legacy, mode + ".arm"))) { Status("blocked_legacy_request"); yield break; }
        try
        {
            ConfigNode root = ConfigNode.Load(request);
            ConfigNode plan = root.GetNode("C3_BASELINE_REQUEST");
            if (plan == null) throw new Exception("missing request node");
            rebuild = plan.GetValue("editor_rebuild") == "True";
            fly = plan.GetValue("mode") == "flight";
            string payloadText = plan.GetValue("payload_t");
            if (!string.IsNullOrEmpty(payloadText) &&
                (!double.TryParse(payloadText, NumberStyles.Float, CultureInfo.InvariantCulture, out targetPayload) ||
                 targetPayload <= 0))
                throw new Exception("invalid payload target");
            folder = plan.GetValue("folder"); craftName = plan.GetValue("craft");
            if (string.IsNullOrEmpty(folder) || !System.Text.RegularExpressions.Regex.IsMatch(folder, "^C3Baseline[0-9A-Za-z_-]+$"))
                throw new Exception("invalid isolated folder");
            if (craftName != "C3 Neutrons I 2630 Payload Baseline") throw new Exception("unexpected craft");
            string save = Path.Combine(KSPUtil.ApplicationRootPath, "saves/" + folder + "/persistent.sfs");
            craftPath = Path.Combine(KSPUtil.ApplicationRootPath, "saves/" + folder + "/Ships/VAB/" + craftName + ".craft");
            if (!string.Equals(Hash(save), plan.GetValue("save_sha256"), StringComparison.OrdinalIgnoreCase) ||
                !string.Equals(Hash(craftPath), plan.GetValue("craft_sha256"), StringComparison.OrdinalIgnoreCase))
                throw new Exception("input hash mismatch");
            File.Move(request, Path.Combine(dir, "inspect.consumed." + DateTime.UtcNow.ToString("yyyyMMddHHmmssfffffff")));
            active = true; started = Time.realtimeSinceStartup;
            oldBackground = Application.runInBackground; Application.runInBackground = true;
            GameEvents.onLevelWasLoadedGUIReady.Add(SceneReady);
            Status("armed_pad_inspection " + folder);
        }
        catch (Exception e) { Status("request_rejected " + e); yield break; }
        yield return new WaitForSecondsRealtime(3);
        try
        {
            Game game = GamePersistence.LoadGame("persistent", folder, true, true);
            if (game == null) throw new Exception("load returned null");
            HighLogic.CurrentGame = game; HighLogic.SaveFolder = folder;
            game.startScene = GameScenes.SPACECENTER;
            game.Start();
        }
        catch (Exception e) { Finish("load_failed " + e); }
    }
    private void SceneReady(GameScenes scene)
    {
        if (active && !loading && scene == GameScenes.SPACECENTER && HighLogic.SaveFolder == folder)
        { loading = true; StartCoroutine(LoadPad()); }
    }
    private IEnumerator LoadPad()
    {
        yield return new WaitForSecondsRealtime(5);
        if (!active || HighLogic.SaveFolder != folder) yield break;
        if (rebuild)
        {
            Status("rebuilding_fairings_in_editor");
            EditorDriver.StartAndLoadVessel(craftPath, EditorFacility.VAB);
            float waitStart = Time.realtimeSinceStartup;
            while (EditorLogic.fetch == null || EditorLogic.fetch.ship == null || EditorLogic.fetch.ship.Count == 0)
            {
                if (!active || Time.realtimeSinceStartup - waitStart > 60) { Finish("editor_timeout"); yield break; }
                yield return new WaitForSecondsRealtime(1);
            }
            yield return new WaitForSecondsRealtime(20);
            if (!active || HighLogic.SaveFolder != folder || EditorLogic.fetch.ship.shipName != craftName)
            { Finish("unexpected_editor_ship"); yield break; }
            GameEvents.onAboutToSaveShip.Fire(EditorLogic.fetch.ship);
            ShipConstruction.SaveShip(EditorLogic.fetch.ship, craftName);
            Status("editor_rebuild_saved sha256=" + Hash(craftPath));
            yield return new WaitForSecondsRealtime(3);
        }
        try
        {
            Status("loading_pad");
            FlightDriver.StartWithNewLaunch(craftPath, "Squad/Flags/default", "LaunchPad", new VesselCrewManifest());
        }
        catch (Exception e) { Finish("pad_load_failed " + e); }
    }
    public void Update()
    {
        if (!active) return;
        if (!flying && Time.realtimeSinceStartup - started > 180) { Finish("inspection_timeout"); return; }
        if (!HighLogic.LoadedSceneIsFlight || HighLogic.SaveFolder != folder) return;
        Vessel v = FlightGlobals.ActiveVessel;
        if (v == null || !v.loaded || v.packed)
        {
            if (flying && Time.realtimeSinceStartup - launchWall > 1800) Finish("flight_timeout_missing_vessel");
            return;
        }
        if (flying)
        {
            try { MonitorFlight(v); } catch (Exception e) { Finish("flight_monitor_failed " + e); }
            return;
        }
        if (v.vesselName != craftName) { Finish("unexpected_vessel"); return; }
        if (readyAt < 0) { readyAt = Time.realtimeSinceStartup; Status("pad_ready"); }
        if (Time.realtimeSinceStartup - readyAt < 15) return;
        try
        {
            if (fly && !ballastAdjusted)
            {
                Capture(v, "_before_ballast");
                int stoppedPumps = 0;
                foreach (Part part in v.parts)
                    foreach (PartModule module in part.Modules)
                        if (module.moduleName == "RefuelingPump")
                        {
                            Set(module, "enablePump", false);
                            module.enabled = false;
                            stoppedPumps++;
                        }
                if (stoppedPumps != 4) throw new Exception("unexpected refueling pump count");
                Status("refueling_pumps_disabled count=" + stoppedPumps);
                PartResource ballast = null;
                foreach (Part part in v.parts)
                    if (part.partInfo.name == "proceduralTankRealFuels")
                        foreach (PartResource resource in part.Resources)
                            if (resource.resourceName == "LeadBallast")
                            {
                                if (ballast != null) throw new Exception("ambiguous ballast");
                                ballast = resource;
                            }
                if (ballast == null || ballast.info.density <= 0 || payloadCount != 11)
                    throw new Exception("missing or invalid ballast");
                double amount = ballast.amount + (targetPayload-measuredPayload)/ballast.info.density;
                if (amount < 0 || amount > ballast.maxAmount) throw new Exception("ballast capacity exceeded");
                Status("ballast_adjustment target=" + F(targetPayload) + " old=" + F(ballast.amount) + " new=" + F(amount) + " density=" + F(ballast.info.density));
                ballast.amount = amount;
                ballastAdjusted = true; ballastAdjustedAt = Time.realtimeSinceStartup;
                return;
            }
            if (fly && Time.realtimeSinceStartup - ballastAdjustedAt < 3) return;
            Capture(v);
            if (fly) BeginFlight(v); else Finish("inspection_complete");
        }
        catch (Exception e) { Finish("capture_failed " + e); }
    }
    private void Capture(Vessel v, string suffix = "")
    {
        ConfigNode report = new ConfigNode("C3_BASELINE_INSPECTION");
        report.AddValue("schema", "1"); report.AddValue("utc", DateTime.UtcNow.ToString("O"));
        report.AddValue("save", folder); report.AddValue("craft_sha256", Hash(craftPath));
        report.AddValue("vessel", v.vesselName); report.AddValue("mass_t", F(v.GetTotalMass()));
        report.AddValue("body", v.mainBody.bodyName); report.AddValue("body_radius_m", F(v.mainBody.Radius));
        report.AddValue("body_mu", F(v.mainBody.gravParameter)); report.AddValue("latitude_deg", F(v.latitude));
        report.AddValue("longitude_deg", F(v.longitude)); report.AddValue("altitude_m", F(v.altitude));
        report.AddValue("situation", v.situation); report.AddValue("controllable", v.IsControllable);
        report.AddValue("ut_s", F(Planetarium.GetUniversalTime()));
        object master = null;
        double payloadMass = 0; payloadShielded = true; payloadCount = 0;
        foreach (Part p in v.parts)
        {
            ConfigNode part = report.AddNode("PART");
            part.AddValue("name", p.partInfo.name); part.AddValue("flight_id", p.flightID);
            part.AddValue("persistent_id", p.persistentId);
            part.AddValue("parent_flight_id", p.parent == null ? "none" : p.parent.flightID.ToString());
            part.AddValue("stage", p.inverseStage); part.AddValue("mass_t", F(p.mass + p.GetResourceMass()));
            part.AddValue("dry_mass_t", F(p.mass)); part.AddValue("shielded", p.ShieldedFromAirstream);
            // This exact payload donor consists of these part types only.
            bool payload = p.partInfo.name == "probeCoreOcto.v2" || p.partInfo.name == "probeCoreOcto_v2" ||
                p.partInfo.name == "batteryPack" || p.partInfo.name == "longAntenna" || p.partInfo.name == "proceduralTankRealFuels";
            part.AddValue("payload", payload); if (payload) { payloadMass += p.mass + p.GetResourceMass(); payloadCount++; payloadShielded &= p.ShieldedFromAirstream; }
            foreach (PartResource r in p.Resources)
            {
                ConfigNode resource = part.AddNode("RESOURCE");
                resource.AddValue("name", r.resourceName); resource.AddValue("amount", F(r.amount));
                resource.AddValue("capacity", F(r.maxAmount)); resource.AddValue("density", F(r.info.density));
            }
            foreach (PartModule m in p.Modules)
            {
                ModuleEngines engine = m as ModuleEngines;
                if (engine != null)
                {
                    ConfigNode e = part.AddNode("ENGINE");
                    e.AddValue("max_thrust_kN", F(engine.maxThrust)); e.AddValue("min_thrust_kN", F(engine.minThrust));
                    e.AddValue("ignited", engine.EngineIgnited); e.AddValue("operational", engine.isOperational);
                }
                if (m.GetType().FullName == "MuMech.MechJebCore")
                {
                    object candidate = m.GetType().GetProperty("MasterMechJeb").GetValue(m, null);
                    if (master != null && candidate != null && !object.ReferenceEquals(master, candidate))
                        throw new Exception("ambiguous master MechJeb");
                    if (candidate != null) master = candidate;
                }
            }
        }
        measuredPayload = payloadMass; masterCore = master;
        report.AddValue("payload_mass_t", F(payloadMass));
        report.AddValue("target_payload_t", F(targetPayload));
        report.AddValue("payload_shielded", payloadShielded);
        report.AddValue("mechjeb_master_found", master != null);
        if (master != null)
        {
            MethodInfo get = master.GetType().GetMethod("GetComputerModule", new Type[] { typeof(string) });
            foreach (string name in new string[] { "MechJebModuleAscentAutopilot", "MechJebModuleAscentPVG", "MechJebModuleAscentGuidance", "MechJebModuleStagingController", "MechJebModuleThrustController" })
            {
                object module = get.Invoke(master, new object[] { name });
                ConfigNode node = report.AddNode(name);
                if (module == null) { node.AddValue("missing", true); continue; }
                foreach (FieldInfo field in module.GetType().GetFields(BindingFlags.Public | BindingFlags.Instance))
                {
                    object val = field.GetValue(module);
                    string scalar = Scalar(val);
                    if (scalar == null && val != null && val.GetType().Name.StartsWith("Editable"))
                    {
                        PropertyInfo prop = val.GetType().GetProperty("val");
                        if (prop != null) scalar = Scalar(prop.GetValue(val, null));
                    }
                    if (scalar != null) node.AddValue(field.Name, scalar);
                }
                PropertyInfo path = module.GetType().GetProperty("ascentPathIdxPublic");
                if (path != null) node.AddValue("ascentPathIdxPublic", path.GetValue(module, null));
            }
        }
        report.Save(Path.Combine(dir, folder + suffix + ".c3inspection"));
        Status("measured payload_t=" + F(payloadMass) + " vessel_t=" + F(v.GetTotalMass()) + " controllable=" + v.IsControllable + " mj=" + (master != null));
    }

    private object Module(string name)
    {
        object m = masterCore.GetType().GetMethod("GetComputerModule", new Type[] { typeof(string) }).Invoke(masterCore, new object[] { name });
        if (m == null) throw new Exception("missing " + name);
        return m;
    }
    private static void Set(object target, string name, object value)
    {
        FieldInfo field = target.GetType().GetField(name, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
        if (field != null) { field.SetValue(target, value); return; }
        PropertyInfo prop = target.GetType().GetProperty(name);
        if (prop == null) throw new MissingMemberException(target.GetType().FullName, name);
        prop.SetValue(target, value, null);
    }
    private static void Editable(object module, string name, double number)
    {
        object obj = module.GetType().GetField(name).GetValue(module);
        PropertyInfo prop = obj.GetType().GetProperty("val");
        if (prop != null) prop.SetValue(obj, Convert.ChangeType(number, prop.PropertyType), null);
        else { FieldInfo f = obj.GetType().GetField("val"); f.SetValue(obj, Convert.ChangeType(number, f.FieldType)); }
    }
    private void BeginFlight(Vessel v)
    {
        if (v.situation != Vessel.Situations.PRELAUNCH || !v.IsControllable || masterCore == null ||
            payloadCount != 11 || Math.Abs(measuredPayload - targetPayload) > 0.001 || !payloadShielded || v.parts.Count != 24 ||
            Math.Abs(v.latitude - 19.6137) > 0.02 || Math.Abs(v.longitude - 110.9553) > 0.02)
            throw new Exception("pad gates failed: payload mass/count/shielding, control, location or part count");
        int clamps = 0, fairings = 0, engines = 0;
        double charge = 0;
        foreach (Part p in v.parts)
        {
            foreach (PartResource r in p.Resources) if (r.resourceName == "ElectricCharge") charge += r.amount;
            if (p.partInfo.name == "launchClamp1") { if (p.inverseStage != 3) throw new Exception("clamp stage"); clamps++; }
            if (p.partInfo.name == "KzProcFairingSide1") { if (p.inverseStage != 2) throw new Exception("fairing stage"); fairings++; }
            if (p.partInfo.name == "SSTU-SC-ENG-Merlin-1B") { if (p.inverseStage != 4) throw new Exception("booster stage"); engines++; }
            if (p.partInfo.name == "SSTU-SC-ENG-LR81-8096") { if (p.inverseStage != 1) throw new Exception("upper stage"); engines++; }
        }
        if (clamps != 4 || fairings != 2 || engines != 2 || charge < 750) throw new Exception("staging/electric gate");
        ascent = Module("MechJebModuleAscentAutopilot");
        PropertyInfo path = ascent.GetType().GetProperty("ascentPathIdxPublic");
        path.SetValue(ascent, Enum.Parse(path.PropertyType, "PVG"), null);
        Editable(ascent, "desiredOrbitAltitude", 220000); Set(ascent, "desiredInclination", 20.0);
        Editable(Module("MechJebModuleAscentGuidance"), "desiredInclination", 20);
        Set(ascent, "autoThrottle", true); Set(ascent, "autostage", true); Set(ascent, "skipCircularization", true);
        object pvg = Module("MechJebModuleAscentPVG");
        Editable(pvg, "DesiredApoapsis", 220000); Editable(pvg, "PitchStartVelocity", 50);
        Editable(pvg, "PitchRate", 0.5); Editable(pvg, "DynamicPressureTrigger", 10000);
        Set(pvg, "FixedCoast", true); Editable(pvg, "FixedCoastLength", 0);
        Set(pvg, "AttachAltFlag", false); Set(pvg, "StagingTriggerFlag", false);
        Editable(Module("MechJebModuleStagingController"), "autostageLimit", 1);
        Capture(v, "_configured");
        flightCsv = new StreamWriter(Path.Combine(dir, folder + "_flight.csv"), false, new System.Text.UTF8Encoding(false));
        flightCsv.WriteLine("ut_s,elapsed_s,stage,altitude_m,periapsis_m,apoapsis_m,inclination_deg,mass_t,throttle,thrust_kN,electric_charge,status");
        object users = ascent.GetType().GetField("users").GetValue(ascent);
        users.GetType().GetMethod("Add").Invoke(users, new object[] { this });
        launchedUT = Planetarium.GetUniversalTime(); launchWall = Time.realtimeSinceStartup; flying = true;
        ascent.GetType().GetMethod("StartCountdown").Invoke(ascent, new object[] { launchedUT + 5 });
        Status("flight_countdown target=220000x220000 inclination=20 payload_t=" + F(measuredPayload) + " target_payload_t=" + F(targetPayload));
    }
    private void MonitorFlight(Vessel v)
    {
        if (v.vesselName != craftName) { Finish("flight_failed_unexpected_vessel"); return; }
        double ut = Planetarium.GetUniversalTime(), elapsed = ut - launchedUT;
        if (Time.realtimeSinceStartup - launchWall > 1800 || elapsed > 1000) { Capture(v, "_final"); Finish("flight_timeout"); return; }
        if (ut - lastRecord < 1) return;
        lastRecord = ut;
        double thrust = 0, ec = 0; int payload = 0;
        foreach (Part p in v.parts)
        {
            if (p.partInfo.name == "probeCoreOcto.v2" || p.partInfo.name == "probeCoreOcto_v2" || p.partInfo.name == "batteryPack" || p.partInfo.name == "longAntenna" || p.partInfo.name == "proceduralTankRealFuels") payload++;
            foreach (PartResource r in p.Resources) if (r.resourceName == "ElectricCharge") ec += r.amount;
            foreach (PartModule m in p.Modules) { ModuleEngines e = m as ModuleEngines; if (e != null) thrust += e.finalThrust; }
        }
        string status = Convert.ToString(ascent.GetType().GetField("status").GetValue(ascent));
        flightCsv.WriteLine(string.Join(",", new string[] { F(ut), F(elapsed), v.currentStage.ToString(), F(v.altitude), F(v.orbit.PeA), F(v.orbit.ApA), F(v.orbit.inclination), F(v.GetTotalMass()), F(v.ctrlState.mainThrottle), F(thrust), F(ec), "\"" + status.Replace("\"", "\"\"") + "\"" }));
        flightCsv.Flush();
        if (payload != 11 || !v.IsControllable || ec <= 0) { Capture(v, "_final"); Finish("flight_failed_payload_or_control"); return; }
        if (elapsed > 60 && (v.situation == Vessel.Situations.PRELAUNCH || v.Landed || v.Splashed))
        { Capture(v, "_final"); Finish("flight_failed_no_liftoff_or_landed"); return; }
        bool reached = Math.Abs(v.orbit.PeA-220000) <= 5000 && Math.Abs(v.orbit.ApA-220000) <= 5000 && Math.Abs(v.orbit.inclination-20) <= 0.2 && thrust < 0.01;
        if (reached)
        {
            if (stableSince < 0) stableSince = ut;
            if (ut-stableSince >= 30) { Capture(v, "_final"); Finish("flight_success_orbit_held_30s"); }
        }
        else stableSince = -1;
        if (elapsed > 30 && !(bool)ascent.GetType().GetProperty("enabled").GetValue(ascent, null) && !reached)
        { Capture(v, "_final"); Finish("flight_failed_guidance_stopped_outside_target"); }
    }

    private static string Scalar(object x)
    {
        if (x == null) return null;
        Type t = x.GetType();
        return t.IsPrimitive || t.IsEnum || x is string ? Convert.ToString(x, CultureInfo.InvariantCulture) : null;
    }
    private void Status(string s)
    {
        Directory.CreateDirectory(dir);
        File.AppendAllText(Path.Combine(dir, "status.log"), DateTime.UtcNow.ToString("O") + " " + s + Environment.NewLine);
        Debug.Log("[C3Baseline] " + s);
    }
    private void Finish(string s)
    {
        if (flightCsv != null) { flightCsv.Flush(); flightCsv.Close(); flightCsv = null; }
        if (flying && ascent != null)
        {
            try
            {
                object users = ascent.GetType().GetField("users").GetValue(ascent);
                users.GetType().GetMethod("Remove").Invoke(users, new object[] { this });
                FlightInputHandler.state.mainThrottle = 0;
            }
            catch (Exception e) { Status("shutdown_error " + e.Message); }
        }
        Status(s); active = false; Application.Quit();
    }
    public void OnDestroy()
    {
        GameEvents.onLevelWasLoadedGUIReady.Remove(SceneReady);
        if (loading) Application.runInBackground = oldBackground;
    }
}
