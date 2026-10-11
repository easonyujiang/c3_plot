export type Sample={payload_t:number;orbit_alt_km:number;dv_rem_m_s:number;benchmark?:string};
export type RocketConfig={rocket:{name:string};stages:{wet_mass_t:number;dry_mass_t:number;multi_ignition:boolean;runtime_performance?:{thrust:number;isp_vac:number;isp_sl:number};max_ignitions?:number|null;tank_states?:{part_id:string;module:string;fields:Record<string,string>}[];[key:string]:unknown}[];calibration:Sample[];[key:string]:unknown};
export type Craft={id:string;sha256:string;name:string;filename:string;save:string};
export type Rocket={slug:string;name:string};
export type CurveData={c3_mass_curve:{c3:number;payload:number}[];calibration_support?:{measured_payload_min_t:number;measured_payload_max_t:number}};
