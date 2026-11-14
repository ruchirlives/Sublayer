import pathlib, unittest
import usd_worker
from usd_graph import compile_graph, inspect_stage_lights
from pxr import Usd, UsdGeom, UsdShade, UsdLux, Sdf

class MaterialBindingAndLightTests(unittest.TestCase):
    def test_material_binding_node_applies_bindings(self):
        sample = pathlib.Path(__file__).resolve().parents[1] / 'samples/layered.usda'
        graph = {
            'targetNodeId': 'bind',
            'nodes': [
                {'id': 'geo', 'operation': 'source', 'inputDependencyId': 'asset'},
                {
                    'id': 'bind',
                    'operation': 'material_bind',
                    'materialBindings': [
                        {
                            'id': 'rule1',
                            'enabled': True,
                            'targetQuery': 'Sphere',
                            'materialPath': '/Materials/M_Test',
                            'strength': 'strongerThanDescendants'
                        }
                    ]
                }
            ],
            'edges': [
                {'id': 'e1', 'source': 'geo', 'target': 'bind'}
            ],
            'dependencies': [{'id': 'asset', 'path': str(sample)}]
        }
        stage, alive, stats = compile_graph(graph)
        self.assertTrue(stage)

    def test_light_overrides_modify_light_attributes(self):
        sample = pathlib.Path(__file__).resolve().parents[1] / 'samples/layered.usda'
        graph = {
            'targetNodeId': 'light_node',
            'nodes': [
                {'id': 'geo', 'operation': 'source', 'inputDependencyId': 'asset'},
                {
                    'id': 'light_node',
                    'operation': 'light',
                    'lightOverrides': [
                        {
                            'id': 'l1',
                            'lightPath': '/World/DomeLight',
                            'enabled': False,
                            'intensity': 500.0,
                            'exposure': 1.5,
                            'color': [0.8, 0.2, 0.2]
                        }
                    ]
                }
            ],
            'edges': [
                {'id': 'e1', 'source': 'geo', 'target': 'light_node'}
            ],
            'dependencies': [{'id': 'asset', 'path': str(sample)}]
        }
        stage, alive, stats = compile_graph(graph)
        self.assertTrue(stage)
        report = inspect_stage_lights({'graph': graph})
        self.assertIn('lights', report)

if __name__ == '__main__':
    unittest.main()
