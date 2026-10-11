/** Presentation-only allowance: never refit calibration or change physics. */
export function netCapacity(gross, fairing) {
  return gross == null ? null : Math.max(0, gross - fairing);
}

export function withFairingAllowance(data, mass) {
  if (!Number.isFinite(mass) || mass < 0) throw new Error('整流罩质量必须为非负有限数值');
  if (mass === 0) return data;
  const c3 = data.c3_mass_curve || [];
  const curve = [];
  for (let i = 0; i < c3.length; i += 1) {
    const point = c3[i];
    if (point.payload >= mass) curve.push({ ...point, payload: netCapacity(point.payload, mass) });
    else if (i > 0 && c3[i - 1].payload > mass) {
      const previous = c3[i - 1];
      const fraction = (previous.payload - mass) / (previous.payload - point.payload);
      curve.push({ c3: previous.c3 + fraction * (point.c3 - previous.c3), payload: 0 });
    }
  }
  const dv = data.dv_curve || [];
  const dvCurve = [];
  for (let i = 0; i < dv.length; i += 1) {
    const point = dv[i];
    if (point.m >= mass) {
      if (i > 0 && point.m > mass && dv[i - 1].m < mass) {
        const previous = dv[i - 1];
        const fraction = (mass - previous.m) / (point.m - previous.m);
        dvCurve.push({ m: 0, dv: previous.dv + fraction * (point.dv - previous.dv) });
      }
      dvCurve.push({ ...point, m: point.m - mass });
    }
  }
  const missions = Object.fromEntries(Object.entries(data.missions || {}).map(([key, mission]) =>
    [key, { ...mission, payload: netCapacity(mission.payload, mass) }]));
  return { ...data, c3_mass_curve: curve, dv_curve: dvCurve, missions,
    m_pl_max: netCapacity(data.m_pl_max, mass),
    c3_0_payload: netCapacity(data.c3_0_payload, mass),
    fairing_allowance_t: mass };
}
