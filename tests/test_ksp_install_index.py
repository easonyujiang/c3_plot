import json
import tempfile
import unittest
from pathlib import Path

from server.ksp_confignode import CraftParseError, ParseOptions, parse
from server.ksp_install_index import (
    CACHE_PARSE_OPTIONS,
    SCHEMA_VERSION,
    build_ksp_index,
    load_ksp_index,
    load_or_build_ksp_index,
)

FIXTURE = """patchedNodeCount = 6

UrlConfig
{
    parentUrl = /Squad/Parts/Engine/stockEngine.cfg
    PART
    {
        name = stockEngine
        title = Stock Engine
        manufacturer = Stock
        description = A test engine
        category = Engine
        mass = 0.5
        cost = 100
        MODULE
        {
            name = ModuleEnginesFX
            engineID = main
            maxThrust = 200
            minThrust = 0
            PROPELLANT
            {
                name = LiquidFuel
                ratio = 0.9
            }
            PROPELLANT
            {
                name = Oxidizer
                ratio = 1.1
            }
            atmosphereCurve
            {
                key = 0 300
                key = 1 250
            }
        }
        MODULE
        {
            name = ModuleFuelTanks
            volume = 100
            TANK
            {
                name = LiquidFuel
                amount = 80
                maxAmount = 80
            }
        }
        RESOURCE
        {
            name = MonoPropellant
            amount = 5
            maxAmount = 5
        }
        {
            name = FXModuleLookAtConstraint
            CONSTRAINLOOKFX
            {
                targetName = A
            }
        }
    }
}

UrlConfig
{
    parentUrl = /Squad/Parts/Engine/stockEnginePatch.cfg
    PART
    {
        name = stockEngine
        title = Patched Stock Engine
        mass = 0.75
    }
}

UrlConfig
{
    parentUrl = /Broken/broken.cfg
    PART
    {
        title = No Name
    }
}

UrlConfig
{
    parentUrl = Squad/Resources/ResourcesGeneric.cfg
    RESOURCE_DEFINITION
    {
        name = LiquidFuel
        density = 0.005
        unitCost = 0.8
        flowMode = STACK_PRIORITY_SEARCH
        transfer = PUMP
    }
}

UrlConfig
{
    parentUrl = Squad/Resources/ResourcesCustom.cfg
    RESOURCE_DEFINITION
    {
        name = WeirdFuel
        density = not-a-number
        unitCost = 1.5
        flowMode = NO_FLOW
    }
}

UrlConfig
{
    parentUrl = SSTU/Data/EngineLayouts/Layout-02-Double.cfg
    SSTU_ENGINELAYOUT
    {
        name = Double
        mountSizeMult = 1
        mountPositions = 0,1
        rotateEngines = False
        POSITION
        {
            x = -0.5
            z = 0
        }
        POSITION
        {
            x = 0.5
            z = 0
        }
    }
}

UrlConfig
{
    parentUrl = /RealFuels/engines/rfEngine.cfg
    PART
    {
        name = rfEngine
        mass = 1.0
        MODULE
        {
            name = ModuleEnginesRF
            engineID = rf
            maxThrust = 350
        }
    }
}
"""


def make_ksp_root(directory):
    """Create a fake KSP root with a synthetic ConfigCache."""
    game_data = Path(directory) / 'GameData'
    game_data.mkdir(parents=True, exist_ok=True)
    cache = game_data / 'ModuleManager.ConfigCache'
    cache.write_text(FIXTURE, encoding='utf-8')
    return Path(directory)


class KspIndexBuildTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ksp_root = make_ksp_root(self._tmp.name)
        self.index = build_ksp_index(self.ksp_root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_url_config_parent_url(self):
        stock = self.index.primary_part('stockEngine')
        self.assertEqual(stock['parent_url'],
                         '/Squad/Parts/Engine/stockEngine.cfg')

    def test_three_record_kinds_extracted(self):
        self.assertEqual(self.index.status, 'ok')
        # 3 named parts + 1 unnamed (invalid) part record
        self.assertEqual(self.index.metadata['record_counts']['parts'], 4)
        self.assertEqual(self.index.metadata['record_counts']['resources'], 2)
        self.assertEqual(self.index.metadata['record_counts']['sstu_layouts'], 1)
        self.assertIsNotNone(self.index.find_resource('LiquidFuel'))
        self.assertIsNotNone(self.index.find_sstu_layout('Double'))

    def test_anonymous_nodes_allowed_in_index_mode(self):
        counts = self.index.metadata['record_counts']
        self.assertEqual(counts['anonymous_nodes'], 1)
        codes = {d['code'] for d in self.index.diagnostics}
        self.assertIn('anonymous_node', codes)
        # the anonymous node must not masquerade as a MODULE
        stock = self.index.primary_part('stockEngine')
        self.assertNotIn('FXModuleLookAtConstraint', stock['modules'])

    def test_craft_strict_mode_rejects_anonymous(self):
        with self.assertRaises(CraftParseError) as ctx:
            parse('PART\n{\n\t{\n\t\tname = X\n\t}\n}\n')
        self.assertEqual(ctx.exception.code, 'missing_node_name')

    def test_cache_parse_options_limits(self):
        self.assertTrue(CACHE_PARSE_OPTIONS.allow_anonymous_nodes)
        self.assertGreaterEqual(CACHE_PARSE_OPTIONS.max_input_chars,
                                64 * 1024 * 1024)
        self.assertGreaterEqual(CACHE_PARSE_OPTIONS.max_nodes, 150000)
        with self.assertRaises(CraftParseError) as ctx:
            parse('A { } B { } C { } D { }',
                  options=ParseOptions(max_nodes=3,
                                       allow_anonymous_nodes=True))
        self.assertEqual(ctx.exception.code, 'node_limit_exceeded')

    def test_duplicate_part_not_silently_overwritten(self):
        candidates = self.index.find_parts('stockEngine')
        self.assertEqual(len(candidates), 2)
        self.assertEqual({c['parent_url'] for c in candidates}, {
            '/Squad/Parts/Engine/stockEngine.cfg',
            '/Squad/Parts/Engine/stockEnginePatch.cfg'})
        primary_count = sum(1 for c in candidates if c['primary'])
        self.assertEqual(primary_count, 1)
        codes = [d['code'] for d in self.index.diagnostics]
        self.assertIn('duplicate_definition', codes)
        self.assertEqual(
            self.index.metadata['record_counts']['duplicate_definitions'], 1)

    def test_raw_and_parsed_numeric_preserved(self):
        stock = self.index.primary_part('stockEngine')
        self.assertEqual(stock['mass_raw'], '0.5')
        self.assertEqual(stock['mass_t'], 0.5)
        self.assertEqual(stock['cost_raw'], '100')
        self.assertEqual(stock['cost'], 100.0)
        fuel = self.index.find_resource('LiquidFuel')
        self.assertEqual(fuel['density_raw'], '0.005')
        self.assertEqual(fuel['density_t_per_unit'], 0.005)
        self.assertEqual(fuel['flow_mode'], 'STACK_PRIORITY_SEARCH')
        self.assertEqual(fuel['transfer'], 'PUMP')

    def test_unparseable_numeric_keeps_raw_with_diagnostic(self):
        weird = self.index.find_resource('WeirdFuel')
        self.assertEqual(weird['density_raw'], 'not-a-number')
        self.assertIsNone(weird['density_t_per_unit'])
        self.assertIn('numeric_parse_failed',
                      [d['code'] for d in weird['diagnostics']])
        self.assertIn('numeric_parse_failed',
                      [d['code'] for d in self.index.diagnostics])

    def test_sstu_position_count(self):
        layout = self.index.find_sstu_layout('Double')
        self.assertEqual(layout['position_count'], 2)
        self.assertEqual(len(layout['positions']), 2)
        self.assertEqual(layout['positions'][0]['values'][0],
                         ['x', '-0.5'])
        self.assertEqual(layout['mount_size_mult'], 1.0)
        self.assertEqual(layout['mount_positions'], '0,1')
        self.assertEqual(layout['rotate_engines'], 'False')

    def test_engine_evidence_fx_and_rf(self):
        stock = self.index.primary_part('stockEngine')
        engine = stock['engines'][0]
        self.assertEqual(engine['module'], 'ModuleEnginesFX')
        self.assertEqual(engine['engine_id'], 'main')
        self.assertEqual(engine['max_thrust'], 200.0)
        self.assertEqual(engine['min_thrust'], 0.0)
        self.assertEqual(len(engine['propellants']), 2)
        self.assertEqual(engine['propellants'][0]['name'], 'LiquidFuel')
        rf = self.index.primary_part('rfEngine')['engines'][0]
        self.assertEqual(rf['module'], 'ModuleEnginesRF')
        self.assertEqual(rf['engine_id'], 'rf')
        self.assertEqual(rf['max_thrust'], 350.0)

    def test_atmosphere_curve_duplicate_keys_preserved(self):
        stock = self.index.primary_part('stockEngine')
        keys = stock['engines'][0]['atmosphere_curve_keys']
        self.assertEqual(keys, ['0 300', '1 250'])

    def test_dynamic_mass_modules_recorded(self):
        stock = self.index.primary_part('stockEngine')
        self.assertIn('ModuleFuelTanks', stock['dynamic_mass_modules'])
        tanks = stock['fuel_tanks'][0]
        self.assertEqual(tanks['volume'], '100')
        self.assertEqual(tanks['tanks'][0]['name'], 'LiquidFuel')
        self.assertEqual(stock['resources'][0]['name'], 'MonoPropellant')

    def test_invalid_record_reported(self):
        invalid = [p for p in self.index.parts if p['name'] == '']
        self.assertEqual(len(invalid), 1)
        self.assertIn('missing_required_field',
                      [d['code'] for d in invalid[0]['diagnostics']])
        self.assertEqual(self.index.lookup_status('part', ''), 'invalid_record')

    def test_queries_and_lookup_status(self):
        self.assertEqual(self.index.lookup_status('part', 'stockEngine'),
                         'ambiguous')
        self.assertEqual(self.index.lookup_status('part', 'rfEngine'),
                         'found')
        self.assertEqual(self.index.lookup_status('part', 'nope'),
                         'missing')
        self.assertEqual(self.index.lookup_status('sstu_layout', 'Double'),
                         'found')
        self.assertEqual(self.index.find_parts('nope'), [])
        self.assertIsNone(self.index.primary_part('nope'))

    def test_metadata_complete(self):
        meta = self.index.metadata
        self.assertEqual(meta['schema_version'], SCHEMA_VERSION)
        self.assertEqual(meta['source_kind'], 'module_manager_config_cache')
        self.assertEqual(meta['patched_node_count'], 6)
        self.assertEqual(meta['config_cache_path'],
                         str(self.ksp_root / 'GameData'
                             / 'ModuleManager.ConfigCache'))
        self.assertGreater(meta['file_size'], 0)
        self.assertGreater(meta['mtime_ns'], 0)
        self.assertEqual(len(meta['sha256']), 64)
        self.assertIn('built_at', meta)

    def test_deterministic_and_json_serializable(self):
        first = build_ksp_index(self.ksp_root)
        second = build_ksp_index(self.ksp_root)
        a = first.to_dict()
        b = second.to_dict()
        a['metadata'].pop('built_at')
        b['metadata'].pop('built_at')
        self.assertEqual(a, b)
        json.dumps(a, ensure_ascii=False)


class KspIndexCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ksp_root = make_ksp_root(self._tmp.name)
        self.cache_path = Path(self._tmp.name) / 'cache' / 'ksp_index.json'

    def tearDown(self):
        self._tmp.cleanup()

    def test_cache_path_none_writes_nothing(self):
        before = {p for p in Path(self._tmp.name).rglob('*') if p.is_file()}
        build_ksp_index(self.ksp_root)
        after = {p for p in Path(self._tmp.name).rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_atomic_write_and_no_temp_left(self):
        index = build_ksp_index(self.ksp_root, cache_path=self.cache_path)
        self.assertTrue(self.cache_path.is_file())
        leftovers = list(Path(self._tmp.name).rglob('*.tmp'))
        self.assertEqual(leftovers, [])
        payload = json.loads(self.cache_path.read_text(encoding='utf-8'))
        self.assertEqual(payload['schema_version'], SCHEMA_VERSION)
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(index.to_dict()['parts'], payload['parts'])

    def test_load_or_build_hits_cache(self):
        first = load_or_build_ksp_index(self.ksp_root,
                                        cache_path=self.cache_path)
        self.assertEqual(first.status, 'ok')
        second = load_or_build_ksp_index(self.ksp_root,
                                         cache_path=self.cache_path)
        payload = json.loads(self.cache_path.read_text(encoding='utf-8'))
        self.assertEqual(second.metadata['built_at'],
                         payload['metadata']['built_at'])

    def test_fingerprint_invalidated_by_content_change(self):
        first = load_or_build_ksp_index(self.ksp_root,
                                        cache_path=self.cache_path)
        first_built_at = first.metadata['built_at']
        cache_file = self.ksp_root / 'GameData' / 'ModuleManager.ConfigCache'
        cache_file.write_text(cache_file.read_text(encoding='utf-8')
                              + '\n', encoding='utf-8')
        second = load_or_build_ksp_index(self.ksp_root,
                                         cache_path=self.cache_path)
        self.assertNotEqual(second.metadata['built_at'], first_built_at)
        self.assertEqual(second.metadata['file_size'],
                         cache_file.stat().st_size)

    def test_corrupt_cache_rebuilt(self):
        self.cache_path.parent.mkdir(parents=True)
        self.cache_path.write_text('{ not json', encoding='utf-8')
        loaded = load_ksp_index(self.cache_path)
        self.assertEqual(loaded.status, 'cache_invalid')
        rebuilt = load_or_build_ksp_index(self.ksp_root,
                                          cache_path=self.cache_path)
        self.assertEqual(rebuilt.status, 'ok')
        self.assertEqual(json.loads(self.cache_path.read_text(
            encoding='utf-8'))['status'], 'ok')

    def test_cache_missing_source_status(self):
        empty = Path(self._tmp.name) / 'no_ksp'
        index = build_ksp_index(empty)
        self.assertEqual(index.status, 'cache_missing')
        codes = [d['code'] for d in index.diagnostics]
        self.assertIn('cache_missing', codes)
        self.assertEqual(index.to_dict()['parts'], [])


@unittest.skipUnless(
    Path(r'D:\SteamLibrary\steamapps\common\Kerbal Space Program\GameData'
         r'\ModuleManager.ConfigCache').is_file(),
    'real KSP install not present on this machine')
class RealInstallSmokeTests(unittest.TestCase):
    """Read-only smoke build against the local KSP install."""

    def test_real_install_builds(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = build_ksp_index(
                r'D:\SteamLibrary\steamapps\common\Kerbal Space Program',
                cache_path=Path(tmp) / 'cache.json')
            self.assertEqual(index.status, 'ok')
            counts = index.metadata['record_counts']
            self.assertGreater(counts['parts'], 1000)
            self.assertGreater(counts['resources'], 100)
            self.assertGreater(counts['sstu_layouts'], 10)
            layout = index.find_sstu_layout('Double')
            self.assertEqual(layout['position_count'], 2)
            layout = index.find_sstu_layout('Triple')
            self.assertEqual(layout['position_count'], 3)
            layout = index.find_sstu_layout('SixRadial')
            self.assertEqual(layout['position_count'], 6)
            # no temp files and a loadable cache
            self.assertFalse(list(Path(tmp).rglob('*.tmp')))
            cached = load_ksp_index(Path(tmp) / 'cache.json')
            self.assertEqual(cached.status, 'ok')


if __name__ == '__main__':
    unittest.main()
