// Passive observer: never changes throttle, staging, craft or save files.
using System;
using System.Globalization;
using System.IO;
using UnityEngine;

[KSPAddon(KSPAddon.Startup.MainMenu, true)]
public class C3MassTelemetryLoadCheck : MonoBehaviour
{
    public void Start() { Debug.Log("[C3MassTelemetry] schema=2 loaded; passive flight observer ready"); }
}

[KSPAddon(KSPAddon.Startup.Flight, false)]
public class C3MassTelemetry : MonoBehaviour
{
    private StreamWriter csv;
    private Guid vesselId;
    private string prefix;
    private double lastSample = double.NaN;
    private double thrustDv;
    private int lastStage = int.MinValue;
    private double lastSnapshot = double.NegativeInfinity;
    private static string F(double value) { return value.ToString("R", CultureInfo.InvariantCulture); }
    private static string Csv(string value) { return "\"" + value.Replace("\"", "\"\"") + "\""; }

    public void FixedUpdate()
    {
        try
        {
            Vessel v = FlightGlobals.ActiveVessel;
            if (v == null || !v.loaded || v.packed) return;
            double ut = Planetarium.GetUniversalTime();
            if (csv == null || v.id != vesselId)
            {
                Close();
                // Never put ConfigNode snapshots in GameData: KSP treats .cfg
                // PART blocks there as additional game definitions.
                string dir = Path.Combine(KSPUtil.ApplicationRootPath, "PluginData/C3MassTelemetry/Flight");
                Directory.CreateDirectory(dir);
                prefix = Path.Combine(dir, DateTime.UtcNow.ToString("yyyyMMdd-HHmmss-fffffff") + "_" + v.id.ToString("N"));
                csv = new StreamWriter(prefix + ".csv", false, new System.Text.UTF8Encoding(false));
                csv.WriteLine("ut_s,vessel_name,body,situation,stage,mass_t,altitude_m,periapsis_m,apoapsis_m,eccentricity,inclination_deg,c3_km2_s2,throttle,thrust_kN,thrust_dv_integral_m_s,latitude_deg,longitude_deg,orbit_speed_m_s");
                vesselId = v.id; lastSample = double.NaN; thrustDv = 0;
                lastStage = int.MinValue; lastSnapshot = double.NegativeInfinity;
            }
            double dt = double.IsNaN(lastSample) ? 0 : ut - lastSample;
            if (dt >= 0 && dt < 0.5 && !double.IsNaN(lastSample)) return;
            double thrust = 0;
            foreach (Part part in v.parts)
                foreach (PartModule module in part.Modules)
                {
                    ModuleEngines engine = module as ModuleEngines;
                    if (engine != null && engine.EngineIgnited) thrust += engine.finalThrust;
                }
            double mass = v.GetTotalMass();
            // Scalar delivered thrust integral, not orbital delta-v; skip
            // large UT gaps so time-warp does not fabricate impulse.
            if (dt > 0 && dt <= 2 && mass > 0) thrustDv += thrust / mass * dt;
            double radius = v.mainBody.Radius + v.altitude;
            double c3 = (v.orbit.vel.sqrMagnitude - 2 * v.mainBody.gravParameter / radius) / 1e6;
            csv.WriteLine(string.Join(",", new string[] {
                F(ut), Csv(v.vesselName), Csv(v.mainBody.bodyName), Csv(v.situation.ToString()),
                v.currentStage.ToString(), F(mass), F(v.altitude), F(v.orbit.PeA), F(v.orbit.ApA),
                F(v.orbit.eccentricity), F(v.orbit.inclination), F(c3), F(v.ctrlState.mainThrottle),
                F(thrust), F(thrustDv), F(v.latitude), F(v.longitude), F(v.orbit.vel.magnitude)
            }));
            csv.Flush();
            lastSample = ut;
            if (v.currentStage != lastStage || ut - lastSnapshot >= 10)
            {
                Snapshot(v, ut);
                lastStage = v.currentStage; lastSnapshot = ut;
            }
        }
        catch (Exception error)
        {
            Debug.LogError("[C3MassTelemetry] Observer stopped: " + error);
            Close(); enabled = false;
        }
    }

    private void Snapshot(Vessel v, double ut)
    {
        ConfigNode root = new ConfigNode("C3_RUNTIME_SNAPSHOT");
        root.AddValue("schema_version", "2");
        root.AddValue("ut_s", F(ut));
        root.AddValue("vessel_id", v.id.ToString());
        root.AddValue("vessel_name", v.vesselName);
        root.AddValue("vessel_mass_t", F(v.GetTotalMass()));
        root.AddValue("body", v.mainBody.bodyName);
        root.AddValue("body_radius_m", F(v.mainBody.Radius));
        root.AddValue("body_mu_m3_s2", F(v.mainBody.gravParameter));
        root.AddValue("body_rotation_period_s", F(v.mainBody.rotationPeriod));
        root.AddValue("stage", v.currentStage.ToString());
        foreach (Part p in v.parts)
        {
            ConfigNode part = root.AddNode("PART");
            part.AddValue("name", p.partInfo.name);
            part.AddValue("flight_id", p.flightID.ToString());
            part.AddValue("parent_flight_id", p.parent == null ? "none" : p.parent.flightID.ToString());
            part.AddValue("fuel_cross_feed", p.fuelCrossFeed.ToString());
            part.AddValue("inverse_stage", p.inverseStage.ToString());
            // Raw mass is explicitly labelled; do not assert it includes
            // every dynamic module contribution or reinterpret saved modMass.
            part.AddValue("raw_part_mass_field_t", F(p.mass));
            part.AddValue("resource_mass_t", F(p.GetResourceMass()));
            // Exact expression found in this installation's Vessel.GetTotalMass
            // IL: Part.mass + Part.GetResourceMass(), before vessel summation.
            part.AddValue("runtime_part_mass_t", F(p.mass + p.GetResourceMass()));
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
                ConfigNode tag = part.AddNode("MODULE_PRESENT");
                tag.AddValue("name", module.moduleName);
                ModuleEngines engine = module as ModuleEngines;
                if (engine == null) continue;
                ConfigNode en = part.AddNode("ENGINE");
                en.AddValue("module", engine.moduleName);
                en.AddValue("engine_id", engine.engineID);
                en.AddValue("ignited", engine.EngineIgnited.ToString());
                en.AddValue("max_thrust_kN", F(engine.maxThrust));
                en.AddValue("actual_thrust_kN", F(engine.finalThrust));
                en.AddValue("vacuum_isp_curve_s", F(engine.atmosphereCurve.Evaluate(0)));
                en.AddValue("sea_level_isp_curve_s", F(engine.atmosphereCurve.Evaluate(1)));
                en.AddValue("actual_isp_s", F(engine.realIsp));
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
        root.Save(prefix + "_" + ut.ToString("F3", CultureInfo.InvariantCulture).Replace('.', '_') + ".c3snapshot");
    }

    private void Close() { if (csv != null) { csv.Dispose(); csv = null; } }
    public void OnDestroy() { Close(); }
}
