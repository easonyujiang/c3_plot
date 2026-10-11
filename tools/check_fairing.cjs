const fs = require('fs');
const assert = require('assert/strict');
const path = require('path');
(async () => {
  const source = fs.readFileSync(path.join(__dirname, '../dashboard/js/fairing.js'), 'utf8');
  const { withFairingAllowance, netCapacity } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
  const data = { m_pl_max: 5, c3_0_payload: 3,
    c3_mass_curve: [{ c3: -10, payload: 5 }, { c3: 0, payload: 3 }, { c3: 10, payload: 1 }, { c3: 20, payload: 0 }],
    dv_curve: [{ m: 0, dv: 5000 }, { m: 2, dv: 3000 }, { m: 4, dv: 1000 }],
    missions: { test: { payload: 3 }, unknown: { payload: null } } };
  const before = JSON.stringify(data);
  assert.equal(withFairingAllowance(data, 0), data);
  const result = withFairingAllowance(data, 2);
  assert.deepEqual(result.c3_mass_curve, [{ c3: -10, payload: 3 }, { c3: 0, payload: 1 }, { c3: 5, payload: 0 }]);
  assert.deepEqual(result.dv_curve, [{ m: 0, dv: 3000 }, { m: 2, dv: 1000 }]);
  assert.equal(result.missions.test.payload, 1);
  assert.equal(result.missions.unknown.payload, null);
  assert.equal(JSON.stringify(data), before);
  assert.equal(withFairingAllowance(data, 6).c3_mass_curve.length, 0);
  assert.equal(netCapacity(1, 2), 0);
  assert.throws(() => withFairingAllowance(data, -1));
  assert.throws(() => withFairingAllowance(data, Infinity));
  console.log('Fairing allowance: analytical values, zero-capacity endpoint and source preservation passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
