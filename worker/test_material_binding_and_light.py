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

    def test_postfx_node_passthrough_and_rtx_render_product_attrs(self):
        from pxr import UsdRender
        import ovrtx_worker

        sample = pathlib.Path(__file__).resolve().parents[1] / 'samples/layered.usda'
        graph = {
            'targetNodeId': 'pfx2',
            'nodes': [
                {'id': 'geo', 'operation': 'source', 'inputDependencyId': 'asset'},
                {
                    'id': 'pfx1',
                    'operation': 'postfx',
                    'postFx': {
                        'tonemapOperator': 'hableUc2',
                        'bloomEnabled': True,
                        'bloomScale': 2.5,
                        'vignetteEnabled': True,
                        'vignetteStrength': 0.85,
                    },
                },
                {
                    'id': 'pfx2',
                    'operation': 'postfx',
                    'postFx': {
                        'tonemapOperator': 'iray',
                        'gradeEnabled': True,
                        'gradeSaturation': [1.2, 1.2, 1.2],
                        'chromabEnabled': True,
                        'autoExposureEnabled': True,
                    },
                },
            ],
            'edges': [
                {'id': 'e1', 'source': 'geo', 'target': 'pfx1'},
                {'id': 'e2', 'source': 'pfx1', 'target': 'pfx2'},
            ],
            'dependencies': [{'id': 'asset', 'path': str(sample)}],
        }
        stage, alive, stats = compile_graph(graph)
        self.assertTrue(stage)

        resolved = ovrtx_worker.resolve_graph_postfx(graph)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved['tonemapOperator'], 'iray')
        self.assertTrue(resolved['bloomEnabled'])
        self.assertAlmostEqual(resolved['bloomScale'], 2.5, places=4)
        self.assertTrue(resolved['gradeEnabled'])
        self.assertTrue(resolved['chromabEnabled'])
        self.assertTrue(resolved['autoExposureEnabled'])
        self.assertTrue(resolved['vignetteEnabled'])

        test_stage = Usd.Stage.CreateInMemory()
        prod = UsdRender.Product.Define(test_stage, '/Render/RenderProduct_Viewport')
        ovrtx_worker.apply_postfx_to_render_product(prod.GetPrim(), resolved)
        prim = prod.GetPrim()
        self.assertEqual(prim.GetAttribute('omni:rtx:post:tonemap:operator').Get(), 'iray')
        self.assertIs(prim.GetAttribute('omni:rtx:post:bloom:enabled').Get(), True)
        self.assertAlmostEqual(prim.GetAttribute('omni:rtx:post:bloom:scale').Get(), 2.5, places=4)
        self.assertIs(prim.GetAttribute('omni:rtx:post:grade:enabled').Get(), True)
        self.assertIs(prim.GetAttribute('omni:rtx:post:chromab:enabled').Get(), True)
        self.assertIs(prim.GetAttribute('omni:rtx:autoExposure:enabled').Get(), True)
        self.assertIs(prim.GetAttribute('omni:rtx:post:tvNoise:enabled').Get(), True)
        self.assertIs(prim.GetAttribute('omni:rtx:post:tvNoise:vignetting:enabled').Get(), True)
        self.assertIs(prim.GetAttribute('omni:rtx:post:tvNoise:filmGrain:enabled').Get(), False)

    def test_light_global_scale_multiplies_positions_sizes_and_intensities(self):
        sample = pathlib.Path(__file__).resolve().parents[1] / 'samples/layered.usda'
        graph = {
            'targetNodeId': 'light_node',
            'nodes': [
                {'id': 'geo', 'operation': 'source', 'inputDependencyId': 'asset'},
                {
                    'id': 'light_node',
                    'operation': 'light',
                    'scale': 100.0,
                    'lightIntensityScale': 250.0,
                    'lightOverrides': [
                        {
                            'id': 'k1',
                            'mode': 'create',
                            'lightPath': '/Scene/Lights/Key',
                            'lightType': 'RectLight',
                            'enabled': True,
                            'intensity': 12.0,
                            'translate': [3.2, 3.0, 3.2],
                            'rotate': [-35.0, 45.0, 0.0],
                            'width': 2.0,
                            'height': 1.5,
                        },
                        {
                            'id': 'sun1',
                            'mode': 'create',
                            'lightPath': '/Scene/Lights/Sun',
                            'lightType': 'DistantLight',
                            'enabled': True,
                            'intensity': 3.5,
                            'angle': 0.53,
                        },
                        {
                            'id': 'off1',
                            'mode': 'create',
                            'lightPath': '/Scene/Lights/Disabled',
                            'lightType': 'RectLight',
                            'enabled': False,
                            'intensity': 10.0,
                        },
                    ],
                },
            ],
            'edges': [{'id': 'e1', 'source': 'geo', 'target': 'light_node'}],
            'dependencies': [{'id': 'asset', 'path': str(sample)}],
        }
        stage, _, _ = compile_graph(graph)
        key = stage.GetPrimAtPath('/Scene/Lights/Key')
        sun = stage.GetPrimAtPath('/Scene/Lights/Sun')
        off = stage.GetPrimAtPath('/Scene/Lights/Disabled')
        self.assertAlmostEqual(key.GetAttribute('inputs:intensity').Get(), 3000.0, places=3)
        self.assertAlmostEqual(key.GetAttribute('inputs:width').Get(), 200.0, places=3)
        self.assertAlmostEqual(key.GetAttribute('inputs:height').Get(), 150.0, places=3)
        pos = UsdGeom.Xformable(key).ComputeLocalToWorldTransform(Usd.TimeCode.Default()).ExtractTranslation()
        self.assertAlmostEqual(pos[0], 320.0, places=3)
        self.assertAlmostEqual(pos[1], 300.0, places=3)
        self.assertAlmostEqual(pos[2], 320.0, places=3)
        self.assertAlmostEqual(sun.GetAttribute('inputs:intensity').Get(), 875.0, places=3)
        self.assertAlmostEqual(sun.GetAttribute('inputs:angle').Get(), 0.53, places=4)
        self.assertAlmostEqual(off.GetAttribute('inputs:intensity').Get(), 0.0, places=3)

if __name__ == '__main__':
    unittest.main()
