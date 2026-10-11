// Observes the user's normal save action; never saves or edits their craft.
// GetShipMass is the game's editor mass getter. Installed IL confirms that
// it settles Part.mass through UpdateMass before adding actual resources.
using System;
using System.Collections;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using UnityEngine;

[KSPAddon(KSPAddon.Startup.EditorAny, false)]
public class C3EditorExporter : MonoBehaviour
{
    private int revision;
    private int request;
    private string cacheHash;
    private static string F(double x) { return x.ToString("R", CultureInfo.InvariantCulture); }
    private static string Hash(byte[] bytes)
    {
        using (SHA256 hash = SHA256.Create())
            return BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
    }
    public void Start()
    {
        string cache = Path.Combine(KSPUtil.ApplicationRootPath, "GameData/ModuleManager.ConfigCache");
        cacheHash = File.Exists(cache) ? Hash(File.ReadAllBytes(cache)) : "unavailable";
        GameEvents.onEditorShipModified.Add(Modified);
        GameEvents.onAboutToSaveShip.Add(Saving);
        Debug.Log("[C3EditorExporter] awaiting user save; schema=1");
    }
    private void Modified(ShipConstruct ship) { revision++; }
    private void Saving(ShipConstruct ship)
    {
        Debug.Log("[C3EditorExporter] save observed: " + ship.shipName);
        request++;
        StartCoroutine(AfterSave(ship, request, revision, DateTime.UtcNow));
    }
    private IEnumerator AfterSave(ShipConstruct ship, int ticket, int savedRevision, DateTime saveTime)
    {
        yield return new WaitForSecondsRealtime(1);
        if (ticket != request || savedRevision != revision || EditorLogic.fetch == null
            || !System.Object.ReferenceEquals(EditorLogic.fetch.ship, ship)) yield break;
        string path = ShipConstruction.GetSavePath(ship.shipName);
        byte[] bytes;
        try
        {
            if (!File.Exists(path) || File.GetLastWriteTimeUtc(path) < saveTime.AddSeconds(-2))
            { Debug.LogWarning("[C3EditorExporter] saved file not updated: " + path); yield break; }
            bytes = File.ReadAllBytes(path);
        }
        catch (Exception error) { Debug.LogWarning("[C3EditorExporter] save not readable: " + error.Message); yield break; }
        string digest = Hash(bytes);
        float dry1, resource1;
        ship.GetShipMass(out dry1, out resource1);
        yield return new WaitForSecondsRealtime(0.5f);
        if (ticket != request || savedRevision != revision || EditorLogic.fetch == null
            || !System.Object.ReferenceEquals(EditorLogic.fetch.ship, ship)) yield break;
        try
        {
            if (Hash(File.ReadAllBytes(path)) != digest) yield break;
            float dry2, resource2;
            float total = ship.GetShipMass(out dry2, out resource2);
            if (Math.Abs(dry1 - dry2) > 0.000001 || Math.Abs(resource1 - resource2) > 0.000001)
            { Debug.LogWarning("[C3EditorExporter] mass still changing; snapshot withheld"); yield break; }
            Export(ship, path, digest, dry2, resource2, total);
        }
        catch (Exception error) { Debug.LogError("[C3EditorExporter] export failed: " + error); }
    }
    private void Export(ShipConstruct ship, string path, string digest, float dry, float resource, float total)
    {
        ConfigNode root = new ConfigNode("C3_EDITOR_SNAPSHOT");
        root.AddValue("schema_version", "1");
        root.AddValue("craft_sha256", digest);
        root.AddValue("craft_path", Path.GetFullPath(path));
        root.AddValue("ship_name", ship.shipName);
        root.AddValue("config_cache_sha256", cacheHash);
        root.AddValue("captured_utc", DateTime.UtcNow.ToString("o"));
        root.AddValue("game_mass_method", "ShipConstruct.GetShipMass(out dry, out resource); no crew manifest");
        root.AddValue("editor_dry_mass_t", F(dry));
        root.AddValue("editor_resource_mass_t", F(resource));
        root.AddValue("editor_total_mass_t", F(total));
        root.AddValue("infinite_propellant", CheatOptions.InfinitePropellant.ToString());
        Type rfSettings = Type.GetType("RealFuels.RFSettings, RealFuels");
        if (rfSettings != null)
        {
            PropertyInfo instanceProperty = rfSettings.GetProperty("Instance", BindingFlags.Public | BindingFlags.Static);
            FieldInfo ignitionSetting = rfSettings.GetField("limitedIgnitions", BindingFlags.Public | BindingFlags.Instance);
            object instance = instanceProperty == null ? null : instanceProperty.GetValue(null, null);
            if (instance != null && ignitionSetting != null)
                root.AddValue("rf_limited_ignitions", ignitionSetting.GetValue(instance).ToString());
        }
        foreach (string file in Directory.GetFiles(Path.Combine(KSPUtil.ApplicationRootPath, "GameData"), "*.dll", SearchOption.AllDirectories))
        {
            ConfigNode source = root.AddNode("PLUGIN_SOURCE");
            source.AddValue("path", file.Substring(KSPUtil.ApplicationRootPath.Length).Replace('\\', '/'));
            source.AddValue("sha256", Hash(File.ReadAllBytes(file)));
        }
        foreach (Part p in ship.Parts)
        {
            ConfigNode part = root.AddNode("PART");
            part.AddValue("name", p.partInfo.name);
            part.AddValue("craft_id", p.craftID.ToString());
            part.AddValue("parent_craft_id", p.parent == null ? "none" : p.parent.craftID.ToString());
            part.AddValue("inverse_stage", p.inverseStage.ToString());
            part.AddValue("fuel_cross_feed", p.fuelCrossFeed.ToString());
            part.AddValue("prefab_mass_t", F(p.prefabMass));
            part.AddValue("settled_dry_mass_t", F(p.mass));
            part.AddValue("resource_mass_t", F(p.GetResourceMass()));
            part.AddValue("current_wet_mass_t", F(p.mass + p.GetResourceMass()));
            foreach (AttachNode node in p.attachNodes)
            {
                if (node.attachedPart == null) continue;
                ConfigNode attachment = part.AddNode("ATTACHMENT");
                attachment.AddValue("node_id", node.id);
                attachment.AddValue("attached_craft_id", node.attachedPart.craftID.ToString());
            }
            foreach (PartResource r in p.Resources)
            {
                ConfigNode res = part.AddNode("RESOURCE");
                res.AddValue("name", r.resourceName);
                res.AddValue("amount", F(r.amount));
                res.AddValue("max_amount", F(r.maxAmount));
                res.AddValue("density_t_per_unit", F(r.info.density));
                res.AddValue("flow_state", r.flowState.ToString());
            }
            foreach (PartModule module in p.Modules)
            {
                ConfigNode mod = part.AddNode("MODULE_STATE");
                mod.AddValue("name", module.moduleName);
                ReadFields(module, mod);
                ModuleDecouple decoupler = module as ModuleDecouple;
                if (decoupler != null)
                {
                    mod.AddValue("explosive_node_id", decoupler.explosiveNodeID);
                    mod.AddValue("omni_decoupler", decoupler.isOmniDecoupler.ToString());
                }
                ModuleEngines engine = module as ModuleEngines;
                if (engine == null) continue;
                ConfigNode en = part.AddNode("ENGINE");
                en.AddValue("module", engine.moduleName);
                en.AddValue("engine_id", engine.engineID);
                en.AddValue("enabled", engine.enabled.ToString());
                en.AddValue("selected", engine.isEnabled.ToString());
                en.AddValue("max_thrust_kN", F(engine.maxThrust));
                en.AddValue("min_thrust_kN", F(engine.minThrust));
                en.AddValue("vacuum_isp_s", F(engine.atmosphereCurve.Evaluate(0)));
                en.AddValue("sea_level_isp_s", F(engine.atmosphereCurve.Evaluate(1)));
                en.AddValue("thrust_limiter_percent", F(engine.thrustPercentage));
                foreach (Propellant propellant in engine.propellants)
                {
                    ConfigNode prop = en.AddNode("PROPELLANT");
                    prop.AddValue("name", propellant.name);
                    prop.AddValue("ratio", F(propellant.ratio));
                    prop.AddValue("ignore_for_isp", propellant.ignoreForIsp.ToString());
                    prop.AddValue("flow_mode", propellant.GetFlowMode().ToString());
                }
            }
        }
        string directory = Path.Combine(KSPUtil.ApplicationRootPath, "PluginData/C3MassTelemetry/EditorSnapshots");
        Directory.CreateDirectory(directory);
        string target = Path.Combine(directory, digest + ".c3snapshot");
        string temporary = target + ".tmp";
        root.Save(temporary);
        if (File.Exists(target)) File.Replace(temporary, target, null);
        else File.Move(temporary, target);
        Debug.Log("[C3EditorExporter] saved snapshot " + digest + "; parts=" + ship.Count + "; mass=" + F(total));
    }
    private static void ReadFields(PartModule module, ConfigNode node)
    {
        // Read primitive instance fields only. Do not call mod methods or
        // infer units from a generic name such as volume or maxAmount.
        string[] names = { "volume", "totalVolume", "usableVolume", "utilization", "tankageMass",
            "mass", "massIsAdditive", "basemass", "origMass", "massMult", "MassMult", "engineMass",
            "configuration", "techLevel", "type", "currentSubtypeName", "subtypeName",
            "ignitions", "ignitionsRemained", "ullage", "pressureFed", "ignitionThreshold",
            "currentEngineLayoutName", "adjustMass", "subtractMass", "engineCount" };
        foreach (string name in names)
        {
            FieldInfo field = module.GetType().GetField(name, BindingFlags.Public | BindingFlags.Instance);
            if (field == null) continue;
            object value = field.GetValue(module);
            if (value == null || !(value is string || value is bool || value is IConvertible)) continue;
            ConfigNode item = node.AddNode("FIELD");
            item.AddValue("name", name);
            item.AddValue("value", Convert.ToString(value, CultureInfo.InvariantCulture));
            item.AddValue("declared_type", field.FieldType.FullName);
        }
    }
    public void OnDestroy()
    {
        GameEvents.onEditorShipModified.Remove(Modified);
        GameEvents.onAboutToSaveShip.Remove(Saving);
    }
}
