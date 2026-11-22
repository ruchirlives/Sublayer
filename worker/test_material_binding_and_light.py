import pathlib, unittest
import usd_worker
from usd_graph import compile_graph, decompose_camera_transform, inspect_stage_lights
from pxr import Gf, Usd, UsdGeom, UsdShade, UsdLux, Sdf

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

    def test_light_create_from_view_and_shape(self):
        sample = pathlib.Path(__file__).resolve().parents[1] / 'samples/layered.usda'
        eye = Gf.Vec3d(3.0, 4.0, 5.0)
        center = Gf.Vec3d(0.0, 1.0, 0.0)
        up = Gf.Vec3d(0.0, 1.0, 0.0)
        cam_world = Gf.Matrix4d().SetLookAt(eye, center, up).GetInverse()
        cam_xform = decompose_camera_transform(cam_world)

        graph = {
            'targetNodeId': 'light_node',
            'nodes': [
                {'id': 'geo', 'operation': 'source', 'inputDependencyId': 'asset'},
                {
                    'id': 'light_node',
                    'operation': 'light',
                    'lightOverrides': [
                        {
                            'id': 'view_light',
                            'mode': 'create',
                            'lightPath': '/Scene/Lights/ViewLight1',
                            'lightType': 'RectLight',
                            'enabled': True,
                            'intensity': 12.5,
                            'exposure': 0.5,
                            'color': [1.0, 0.9, 0.8],
                            'translate': cam_xform['translate'],
                            'rotate': cam_xform['rotate'],
                            'width': 2.0,
                            'height': 1.5,
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
        prim = stage.GetPrimAtPath('/Scene/Lights/ViewLight1')
        self.assertTrue(prim and prim.IsValid())
        self.assertEqual(prim.GetTypeName(), 'RectLight')
        self.assertTrue(prim.HasAPI(UsdLux.ShadowAPI))
        self.assertIs(prim.GetAttribute('inputs:shadow:enable').Get(), True)
        self.assertAlmostEqual(prim.GetAttribute('inputs:width').Get(), 2.0, places=4)
        self.assertAlmostEqual(prim.GetAttribute('inputs:height').Get(), 1.5, places=4)

        mat = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        pos = mat.ExtractTranslation()
        for i in range(3):
            self.assertAlmostEqual(pos[i], eye[i], places=3)
        fwd = mat.TransformDir(Gf.Vec3d(0, 0, -1)).GetNormalized()
        expected_fwd = (center - eye).GetNormalized()
        for i in range(3):
            self.assertAlmostEqual(fwd[i], expected_fwd[i], places=3)

    def test_created_lights_and_hdri_enable_shadows_by_default(self):
        sample = pathlib.Path(__file__).resolve().parents[1] / 'samples/layered.usda'
        light_types = ['DistantLight', 'RectLight', 'DiskLight', 'SphereLight', 'CylinderLight', 'DomeLight']
        overrides = [
            {
                'id': f'l_{lt}',
                'mode': 'create',
                'lightPath': f'/Scene/Lights/{lt}_Test',
                'lightType': lt,
                'enabled': True,
                'intensity': 10.0,
            }
            for lt in light_types
        ]
        graph = {
            'targetNodeId': 'hdri_node',
            'nodes': [
                {'id': 'geo', 'operation': 'source', 'inputDependencyId': 'asset'},
                {'id': 'light_node', 'operation': 'light', 'lightOverrides': overrides},
                {'id': 'hdri_node', 'operation': 'hdri', 'hdri': {'intensity': 1.0}},
            ],
            'edges': [
                {'id': 'e1', 'source': 'geo', 'target': 'light_node'},
                {'id': 'e2', 'source': 'light_node', 'target': 'hdri_node'},
            ],
            'dependencies': [{'id': 'asset', 'path': str(sample)}]
        }
        stage, alive, stats = compile_graph(graph)
        for lt in light_types:
            prim = stage.GetPrimAtPath(f'/Scene/Lights/{lt}_Test')
            self.assertTrue(prim and prim.IsValid(), lt)
            self.assertTrue(prim.HasAPI(UsdLux.ShadowAPI), lt)
            self.assertIs(prim.GetAttribute('inputs:shadow:enable').Get(), True, lt)
        hdri_prim = stage.GetPrimAtPath('/OrchFlowHDRI')
        self.assertTrue(hdri_prim and hdri_prim.IsValid())
        self.assertTrue(hdri_prim.HasAPI(UsdLux.ShadowAPI))
        self.assertIs(hdri_prim.GetAttribute('inputs:shadow:enable').Get(), True)

    def test_inspect_stage_lights_reports_stage_units_and_bounds(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            src_path = pathlib.Path(td) / 'cube.usda'
            src_stage = Usd.Stage.CreateNew(str(src_path))
            UsdGeom.SetStageMetersPerUnit(src_stage, 1.0)
            UsdGeom.SetStageUpAxis(src_stage, UsdGeom.Tokens.y)
            cube = UsdGeom.Cube.Define(src_stage, '/Cube')
            cube.CreateSizeAttr().Set(2.0)
            src_stage.GetRootLayer().Save()

            base_graph = {
                'targetNodeId': 'light_node',
                'nodes': [
                    {'id': 'geo', 'operation': 'source', 'inputDependencyId': 'asset'},
                    {'id': 'light_node', 'operation': 'light', 'lightOverrides': []},
                ],
                'edges': [{'id': 'e1', 'source': 'geo', 'target': 'light_node'}],
                'dependencies': [{'id': 'asset', 'path': str(src_path)}],
            }
            report = inspect_stage_lights({'graph': base_graph})
            self.assertAlmostEqual(report['metersPerUnit'], 1.0, places=6)
            self.assertEqual(report['upAxis'], 'Y')
            self.assertIsNotNone(report['sceneBounds'])
            # 2x2x2 cube has half-diagonal radius sqrt(3) ~= 1.732
            self.assertAlmostEqual(report['sceneBounds']['radius'], 3.0 ** 0.5, places=3)

            scaled_graph = {
                'targetNodeId': 'light_node',
                'nodes': [
                    {'id': 'geo', 'operation': 'source', 'inputDependencyId': 'asset'},
                    {'id': 'xform', 'operation': 'transform', 'transform': {'scale': [100.0, 100.0, 100.0]}},
                    {'id': 'light_node', 'operation': 'light', 'lightOverrides': []},
                ],
                'edges': [
                    {'id': 'e1', 'source': 'geo', 'target': 'xform'},
                    {'id': 'e2', 'source': 'xform', 'target': 'light_node'},
                ],
                'dependencies': [{'id': 'asset', 'path': str(src_path)}],
            }
            scaled_report = inspect_stage_lights({'graph': scaled_graph})
            self.assertAlmostEqual(scaled_report['metersPerUnit'], 1.0, places=6)
            self.assertIsNotNone(scaled_report['sceneBounds'])
            self.assertAlmostEqual(scaled_report['sceneBounds']['radius'], 100.0 * (3.0 ** 0.5), places=2)

if __name__ == '__main__':
    unittest.main()
