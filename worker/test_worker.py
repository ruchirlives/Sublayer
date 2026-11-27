import pathlib
import unittest
from usd_worker import dispatch


class WorkerTests(unittest.TestCase):
    def test_real_stage_metadata(self):
        path = pathlib.Path(__file__).resolve().parents[1] / 'samples' / 'cube.usda'
        before = path.read_bytes()
        result = dispatch({'operation': 'inspect', 'path': str(path)})
        self.assertEqual(result['upAxis'], 'Y')
        self.assertEqual(result['metersPerUnit'], 1)
        self.assertEqual(result['rootPrims'][0]['path'], '/World')
        self.assertFalse(result['payloadsLoaded'])
        self.assertEqual(path.read_bytes(), before)

    def test_capabilities_describe_actual_runtime(self):
        result = dispatch({'operation': 'capabilities'})
        self.assertIn('viewport', result)
        if not result['viewport']:
            self.assertEqual(result['renderers'], [])
            self.assertIn('viewportError', result)
        else:
            self.assertIn('gpu', result)

    def test_unknown_operation(self):
        with self.assertRaises(ValueError):
            dispatch({'operation': 'flatten'})

    def test_scatter_point_instancer(self):
        from pxr import UsdGeom
        from usd_graph import compile_graph
        path = str(pathlib.Path(__file__).resolve().parents[1] / 'samples' / 'cube.usda')
        graph = {
            'targetNodeId': 'scatter1',
            'dependencies': [{'id': 'cube', 'path': path}],
            'nodes': [
                {'id': 'src', 'operation': 'source', 'inputDependencyId': 'cube'},
                {
                    'id': 'scatter1',
                    'operation': 'scatter',
                    'scatter': {
                        'count': 10000,
                        'seed': 7,
                        'distribution': 'grid',
                        'areaSize': [50, 0, 50],
                        'scaleMin': 0.5,
                        'scaleMax': 1.5,
                        'randomYaw': True,
                    },
                },
            ],
            'edges': [{'id': 'e1', 'source': 'src', 'target': 'scatter1'}],
        }
        stage, alive, stats = compile_graph(graph)
        instancer = UsdGeom.PointInstancer(stage.GetPrimAtPath('/Scene/Scatter'))
        self.assertTrue(instancer.GetPrim().IsValid())
        self.assertEqual(len(instancer.GetProtoIndicesAttr().Get()), 10000)
        self.assertEqual(len(instancer.GetPositionsAttr().Get()), 10000)
        self.assertEqual(len(instancer.GetOrientationsAttr().Get()), 10000)
        self.assertEqual(len(instancer.GetScalesAttr().Get()), 10000)
        self.assertGreaterEqual(stats['instances'], 10000)
        self.assertGreaterEqual(stats['prototypes'], 1)

        # Verify identity orientation has real=1, imag=(0,0,0) (not 180-deg X flip).
        graph['nodes'][1]['scatter']['randomYaw'] = False
        stage_id, _, _ = compile_graph(graph)
        inst_id = UsdGeom.PointInstancer(stage_id.GetPrimAtPath('/Scene/Scatter'))
        q0 = inst_id.GetOrientationsAttr().Get()[0]
        self.assertAlmostEqual(q0.GetReal(), 1.0, places=2)
        self.assertAlmostEqual(q0.GetImaginary()[0], 0.0, places=2)
        scene_children_off = [p.GetName() for p in stage_id.GetPrimAtPath('/Scene').GetChildren()]
        self.assertEqual(scene_children_off, ['Scatter'])

        # Verify passThrough keeps the input USD prims alongside /Scene/Scatter.
        graph['nodes'][1]['scatter']['passThrough'] = True
        stage_pt, _, _ = compile_graph(graph)
        scene_children_on = [p.GetName() for p in stage_pt.GetPrimAtPath('/Scene').GetChildren()]
        self.assertIn('Scatter', scene_children_on)
        self.assertGreater(len(scene_children_on), 1)

    def test_scatter_prototype_table_selection(self):
        import tempfile
        from pxr import Usd, UsdGeom
        from usd_graph import compile_graph

        with tempfile.TemporaryDirectory() as tmp:
            props_path = str(pathlib.Path(tmp) / 'props.usda')
            p_stage = Usd.Stage.CreateNew(props_path)
            UsdGeom.SetStageUpAxis(p_stage, UsdGeom.Tokens.y)
            UsdGeom.SetStageMetersPerUnit(p_stage, 1.0)
            root = UsdGeom.Xform.Define(p_stage, '/Props')
            p_stage.SetDefaultPrim(root.GetPrim())
            UsdGeom.Cube.Define(p_stage, '/Props/RockA')
            UsdGeom.Sphere.Define(p_stage, '/Props/RockB')
            UsdGeom.Cone.Define(p_stage, '/Props/IgnoredCone')
            p_stage.GetRootLayer().Save()

            graph = {
                'dependencies': [{'id': 'dep1', 'path': props_path}],
                'targetNodeId': 'scatter1',
                'nodes': [
                    {'id': 'src', 'operation': 'source', 'inputDependencyId': 'dep1', 'instanced': False},
                    {
                        'id': 'scatter1',
                        'operation': 'scatter',
                        'scatter': {
                            'count': 40,
                            'seed': 7,
                            'distribution': 'random',
                            'areaSize': [10.0, 0.0, 10.0],
                            'scaleMin': 1.0,
                            'scaleMax': 1.0,
                            'randomYaw': True,
                            'prototypes': [
                                {'id': 'r1', 'enabled': True, 'primPath': '/Scene/Props/RockA'},
                                {'id': 'r2', 'enabled': False, 'primPath': '/Scene/Props/IgnoredCone'},
                                {'id': 'r3', 'enabled': True, 'primPath': '/Scene/Props/RockB'},
                            ],
                        },
                    },
                ],
                'edges': [{'id': 'e1', 'source': 'src', 'target': 'scatter1'}],
            }
            stage, _, _ = compile_graph(graph)
            instancer = UsdGeom.PointInstancer(stage.GetPrimAtPath('/Scene/Scatter'))
            targets = instancer.GetPrototypesRel().GetTargets()
            self.assertEqual(len(targets), 2)
            self.assertEqual(stage.GetPrimAtPath('/Scene/Scatter/Prototypes/Proto_0').GetTypeName(), 'Cube')
            self.assertEqual(stage.GetPrimAtPath('/Scene/Scatter/Prototypes/Proto_1').GetTypeName(), 'Sphere')
            indices = set(int(i) for i in instancer.GetProtoIndicesAttr().Get())
            self.assertEqual(indices, {0, 1})

            # Missing prim path should fail closed.
            graph['nodes'][1]['scatter']['prototypes'].append({'id': 'r4', 'enabled': True, 'primPath': '/Scene/DoesNotExist'})
            with self.assertRaises(ValueError):
                compile_graph(graph)

            # All disabled should fail closed.
            graph['nodes'][1]['scatter']['prototypes'] = [{'id': 'r2', 'enabled': False, 'primPath': '/Scene/Props/RockA'}]
            with self.assertRaises(ValueError):
                compile_graph(graph)

            # Instance-proxy child should fail closed.
            graph['nodes'][0]['instanced'] = True
            graph['nodes'][1]['scatter']['prototypes'] = [{'id': 'r1', 'enabled': True, 'primPath': '/Scene/Props/RockA'}]
            with self.assertRaises(ValueError):
                compile_graph(graph)

    def test_group_node_xform_and_scatter(self):
        import tempfile
        from pxr import Gf, Usd, UsdGeom
        from usd_graph import compile_graph

        with tempfile.TemporaryDirectory() as tmp:
            a_path = str(pathlib.Path(tmp) / 'rock.usda')
            a_stage = Usd.Stage.CreateNew(a_path)
            UsdGeom.SetStageUpAxis(a_stage, UsdGeom.Tokens.y)
            UsdGeom.SetStageMetersPerUnit(a_stage, 1.0)
            a_root = UsdGeom.Xform.Define(a_stage, '/Rock')
            a_stage.SetDefaultPrim(a_root.GetPrim())
            UsdGeom.Cube.Define(a_stage, '/Rock/Mesh')
            a_stage.GetRootLayer().Save()

            b_path = str(pathlib.Path(tmp) / 'moss.usda')
            b_stage = Usd.Stage.CreateNew(b_path)
            UsdGeom.SetStageUpAxis(b_stage, UsdGeom.Tokens.y)
            UsdGeom.SetStageMetersPerUnit(b_stage, 1.0)
            b_root = UsdGeom.Xform.Define(b_stage, '/Moss')
            b_stage.SetDefaultPrim(b_root.GetPrim())
            UsdGeom.Sphere.Define(b_stage, '/Moss/Mesh')
            b_stage.GetRootLayer().Save()

            graph = {
                'dependencies': [
                    {'id': 'd1', 'path': a_path},
                    {'id': 'd2', 'path': b_path},
                ],
                'targetNodeId': 'scatter1',
                'nodes': [
                    {'id': 'src1', 'operation': 'source', 'inputDependencyId': 'd1', 'instanced': False},
                    {'id': 'src2', 'operation': 'source', 'inputDependencyId': 'd2', 'instanced': False},
                    {
                        'id': 'grp1',
                        'operation': 'group',
                        'groupName': 'Rock Cluster',
                        'transform': {'translation': [1.0, 2.0, 3.0], 'rotation': [0.0, 0.0, 0.0], 'scale': [2.0, 2.0, 2.0]},
                    },
                    {
                        'id': 'scatter1',
                        'operation': 'scatter',
                        'scatter': {
                            'count': 10,
                            'seed': 5,
                            'distribution': 'random',
                            'areaSize': [10.0, 0.0, 10.0],
                            'scaleMin': 1.0,
                            'scaleMax': 1.0,
                            'randomYaw': False,
                            'prototypes': [{'id': 'p1', 'enabled': True, 'primPath': '/Scene/Rock_Cluster'}],
                        },
                    },
                ],
                'edges': [
                    {'id': 'e1', 'source': 'src1', 'target': 'grp1'},
                    {'id': 'e2', 'source': 'src2', 'target': 'grp1'},
                    {'id': 'e3', 'source': 'grp1', 'target': 'scatter1'},
                ],
            }

            # Verify Group stage directly first.
            grp_graph = {**graph, 'targetNodeId': 'grp1'}
            grp_stage, _, _ = compile_graph(grp_graph)
            grp_prim = grp_stage.GetPrimAtPath('/Scene/Rock_Cluster')
            self.assertTrue(grp_prim.IsValid())
            self.assertEqual(grp_prim.GetTypeName(), 'Xform')
            self.assertEqual(len(grp_prim.GetChildren()), 2)
            local_mat = UsdGeom.Xformable(grp_prim).GetLocalTransformation()
            self.assertTrue(Gf.IsClose(local_mat.ExtractTranslation(), Gf.Vec3d(1.0, 2.0, 3.0), 1e-4))

            # Verify Scatter can select and instance /Scene/Rock_Cluster.
            sc_stage, _, _ = compile_graph(graph)
            instancer = UsdGeom.PointInstancer(sc_stage.GetPrimAtPath('/Scene/Scatter'))
            targets = instancer.GetPrototypesRel().GetTargets()
            self.assertEqual(len(targets), 1)
            proto0 = sc_stage.GetPrimAtPath('/Scene/Scatter/Prototypes/Proto_0')
            self.assertEqual(proto0.GetTypeName(), 'Xform')
            self.assertEqual(len(proto0.GetChildren()), 2)

    def test_scatter_surface_normal_slope_and_spacing(self):
        import math
        import tempfile
        from pxr import Gf, Usd, UsdGeom
        from usd_graph import compile_graph

        cube_path = str(pathlib.Path(__file__).resolve().parents[1] / 'samples' / 'cube.usda')
        with tempfile.TemporaryDirectory() as tmp:
            terrain_path = str(pathlib.Path(tmp) / 'terrain.usda')
            t_stage = Usd.Stage.CreateNew(terrain_path)
            UsdGeom.SetStageUpAxis(t_stage, UsdGeom.Tokens.y)
            UsdGeom.SetStageMetersPerUnit(t_stage, 1.0)
            root = UsdGeom.Xform.Define(t_stage, '/Terrain')
            t_stage.SetDefaultPrim(root.GetPrim())
            # Flat quad at y=2.5 (CCW winding -> +Y normal, slope 0 deg) and vertical wall quad (slope 90 deg).
            flat = UsdGeom.Mesh.Define(t_stage, '/Terrain/Flat')
            flat.CreatePointsAttr([(-10, 2.5, 10), (10, 2.5, 10), (10, 2.5, -10), (-10, 2.5, -10)])
            flat.CreateFaceVertexCountsAttr([4])
            flat.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
            wall = UsdGeom.Mesh.Define(t_stage, '/Terrain/Wall')
            wall.CreatePointsAttr([(-10, 0, 10), (10, 0, 10), (10, 10, 10), (-10, 10, 10)])
            wall.CreateFaceVertexCountsAttr([4])
            wall.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
            t_stage.GetRootLayer().Save()

            graph = {
                'targetNodeId': 'scatter1',
                'dependencies': [
                    {'id': 'cube', 'path': cube_path},
                    {'id': 'terrain', 'path': terrain_path},
                ],
                'nodes': [
                    {'id': 'src_proto', 'operation': 'source', 'inputDependencyId': 'cube'},
                    {'id': 'src_surf', 'operation': 'source', 'inputDependencyId': 'terrain'},
                    {
                        'id': 'scatter1',
                        'operation': 'scatter',
                        'scatter': {
                            'count': 80,
                            'seed': 123,
                            'distribution': 'surface',
                            'areaSize': [20, 0, 20],
                            'scaleMin': 1.0,
                            'scaleMax': 1.0,
                            'randomYaw': False,
                            'passThrough': True,
                            'surfacePrimPath': '',
                            'alignToNormal': True,
                            'maxSlopeDeg': 30.0,
                            'minSpacing': 1.5,
                        },
                    },
                ],
                'edges': [
                    {'id': 'e1', 'source': 'src_proto', 'target': 'scatter1'},
                    {'id': 'e2', 'source': 'src_surf', 'target': 'scatter1'},
                ],
            }
            stage, _, _ = compile_graph(graph)
            instancer = UsdGeom.PointInstancer(stage.GetPrimAtPath('/Scene/Scatter'))
            positions = list(instancer.GetPositionsAttr().Get())
            self.assertGreater(len(positions), 5)
            for p in positions:
                self.assertAlmostEqual(p[1], 2.5, places=3)
            # Verify pairwise distance >= minSpacing (1.5)
            for i in range(len(positions)):
                for j in range(i + 1, len(positions)):
                    dist = (Gf.Vec3f(positions[i]) - Gf.Vec3f(positions[j])).GetLength()
                    self.assertGreaterEqual(dist, 1.49)

    def test_scatter_enhanced_rotation_scale_weights_and_filters(self):
        import math
        import tempfile
        from pxr import Gf, Usd, UsdGeom
        from usd_graph import compile_graph

        cube_path = str(pathlib.Path(__file__).resolve().parents[1] / 'samples' / 'cube.usda')
        with tempfile.TemporaryDirectory() as tmp:
            props_path = str(pathlib.Path(tmp) / 'props.usda')
            p_stage = Usd.Stage.CreateNew(props_path)
            UsdGeom.SetStageUpAxis(p_stage, UsdGeom.Tokens.y)
            UsdGeom.SetStageMetersPerUnit(p_stage, 1.0)
            p_root = UsdGeom.Xform.Define(p_stage, '/Props')
            p_stage.SetDefaultPrim(p_root.GetPrim())
            UsdGeom.Cube.Define(p_stage, '/Props/A')
            UsdGeom.Sphere.Define(p_stage, '/Props/B')
            p_stage.GetRootLayer().Save()

            # 1. Rotation ranges, non-uniform scale, weighted prototypes, circle annulus + offset
            graph = {
                'targetNodeId': 'scatter1',
                'dependencies': [{'id': 'props', 'path': props_path}],
                'nodes': [
                    {'id': 'src', 'operation': 'source', 'inputDependencyId': 'props', 'instanced': False},
                    {
                        'id': 'scatter1',
                        'operation': 'scatter',
                        'scatter': {
                            'count': 200,
                            'seed': 7,
                            'distribution': 'circle',
                            'areaSize': [20.0, 0.0, 20.0],
                            'areaOffset': [10.0, 2.0, -5.0],
                            'innerRadiusRatio': 0.5,
                            'scaleUniform': False,
                            'scaleMin': 1.0,
                            'scaleMax': 1.0,
                            'scaleMinVec': [0.5, 2.0, 0.25],
                            'scaleMaxVec': [0.7, 3.0, 0.5],
                            'rotationOffset': [10.0, 0.0, 0.0],
                            'rotationMin': [-5.0, 30.0, 0.0],
                            'rotationMax': [5.0, 60.0, 0.0],
                            'prototypes': [
                                {'id': 'p0', 'enabled': True, 'primPath': '/Scene/Props/A', 'weight': 9.0},
                                {'id': 'p1', 'enabled': True, 'primPath': '/Scene/Props/B', 'weight': 1.0},
                            ],
                        },
                    },
                ],
                'edges': [{'id': 'e1', 'source': 'src', 'target': 'scatter1'}],
            }
            stage, _, _ = compile_graph(graph)
            instancer = UsdGeom.PointInstancer(stage.GetPrimAtPath('/Scene/Scatter'))
            positions = list(instancer.GetPositionsAttr().Get())
            scales = list(instancer.GetScalesAttr().Get())
            orientations = list(instancer.GetOrientationsAttr().Get())
            proto_indices = list(instancer.GetProtoIndicesAttr().Get())

            self.assertEqual(len(positions), 200)
            # Check annulus radii (5.0..10.0 around offset [10, 2, -5])
            for pos in positions:
                self.assertAlmostEqual(pos[1], 2.0, places=3)
                r = math.hypot(pos[0] - 10.0, pos[2] + 5.0)
                self.assertGreaterEqual(r, 4.99)
                self.assertLessEqual(r, 10.01)
            # Check non-uniform scale bounds
            for sc in scales:
                self.assertTrue(0.49 <= sc[0] <= 0.71)
                self.assertTrue(1.99 <= sc[1] <= 3.01)
                self.assertTrue(0.24 <= sc[2] <= 0.51)
            # Check weighted prototype ratio (~90% proto 0)
            p0_count = sum(1 for i in proto_indices if i == 0)
            self.assertGreater(p0_count, 150)
            self.assertLess(p0_count, 200)
            # Check rotation bounds via forward vector / quaternion angle
            for q in orientations:
                rot = Gf.Rotation(Gf.Quatd(q.GetReal(), Gf.Vec3d(q.GetImaginary())))
                # Local +Y rotated by X in [5, 15] deg should tilt between 5 and 15 deg from +Y
                up_rot = rot.TransformDir(Gf.Vec3d(0.0, 1.0, 0.0))
                tilt_deg = math.degrees(math.acos(max(-1.0, min(1.0, up_rot[1]))))
                self.assertTrue(4.5 <= tilt_deg <= 15.5, f'Unexpected tilt {tilt_deg}')

            # 2. Surface heightMin/heightMax, minSlopeDeg, surfaceOffset, and alignStrength
            terrain_path = str(pathlib.Path(tmp) / 'stepped.usda')
            t_stage = Usd.Stage.CreateNew(terrain_path)
            UsdGeom.SetStageUpAxis(t_stage, UsdGeom.Tokens.y)
            UsdGeom.SetStageMetersPerUnit(t_stage, 1.0)
            t_root = UsdGeom.Xform.Define(t_stage, '/Terrain')
            t_stage.SetDefaultPrim(t_root.GetPrim())
            low = UsdGeom.Mesh.Define(t_stage, '/Terrain/Low')
            low.CreatePointsAttr([(-10, 0.0, 10), (10, 0.0, 10), (10, 0.0, -10), (-10, 0.0, -10)])
            low.CreateFaceVertexCountsAttr([4])
            low.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
            high = UsdGeom.Mesh.Define(t_stage, '/Terrain/High')
            high.CreatePointsAttr([(-10, 5.0, 10), (10, 5.0, 10), (10, 5.0, -10), (-10, 5.0, -10)])
            high.CreateFaceVertexCountsAttr([4])
            high.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
            t_stage.GetRootLayer().Save()

            surf_graph = {
                'targetNodeId': 'scatter2',
                'dependencies': [
                    {'id': 'cube', 'path': cube_path},
                    {'id': 'terrain', 'path': terrain_path},
                ],
                'nodes': [
                    {'id': 'src_proto', 'operation': 'source', 'inputDependencyId': 'cube'},
                    {'id': 'src_surf', 'operation': 'source', 'inputDependencyId': 'terrain'},
                    {
                        'id': 'scatter2',
                        'operation': 'scatter',
                        'scatter': {
                            'count': 40,
                            'seed': 11,
                            'distribution': 'surface',
                            'heightMin': 3.0,
                            'heightMax': 6.0,
                            'surfaceOffset': -0.25,
                            'alignToNormal': True,
                            'alignStrength': 0.5,
                            'minSlopeDeg': 0.0,
                            'maxSlopeDeg': 10.0,
                            'rotationMin': [0.0, 0.0, 0.0],
                            'rotationMax': [0.0, 0.0, 0.0],
                        },
                    },
                ],
                'edges': [
                    {'id': 'e1', 'source': 'src_proto', 'target': 'scatter2'},
                    {'id': 'e2', 'source': 'src_surf', 'target': 'scatter2'},
                ],
            }
            s_stage, _, _ = compile_graph(surf_graph)
            s_inst = UsdGeom.PointInstancer(s_stage.GetPrimAtPath('/Scene/Scatter'))
            s_pos = list(s_inst.GetPositionsAttr().Get())
            self.assertEqual(len(s_pos), 40)
            for pos in s_pos:
                # High quad is at y=5.0, surfaceOffset=-0.25 along +Y normal -> y=4.75
                self.assertAlmostEqual(pos[1], 4.75, places=3)

            # 3. Circle perimeter (innerRadiusRatio=1.0), even spacing, and outward/inward/tangent alignment
            for mode, expected_dir_fn in [
                ('outward', lambda pos: Gf.Vec3d(pos[0], 0.0, pos[2]).GetNormalized()),
                ('inward', lambda pos: -Gf.Vec3d(pos[0], 0.0, pos[2]).GetNormalized()),
                ('tangent', lambda pos: Gf.Vec3d(-pos[2], 0.0, pos[0]).GetNormalized()),
            ]:
                c_graph = {
                    'targetNodeId': 'scatter_c',
                    'dependencies': [{'id': 'cube', 'path': cube_path}],
                    'nodes': [
                        {'id': 'src', 'operation': 'source', 'inputDependencyId': 'cube'},
                        {
                            'id': 'scatter_c',
                            'operation': 'scatter',
                            'scatter': {
                                'count': 4,
                                'seed': 1,
                                'distribution': 'circle',
                                'areaSize': [20.0, 0.0, 20.0],
                                'innerRadiusRatio': 1.0,
                                'circleAlign': mode,
                                'circleEvenSpacing': True,
                                'rotationMin': [0.0, 0.0, 0.0],
                                'rotationMax': [0.0, 0.0, 0.0],
                            },
                        },
                    ],
                    'edges': [{'id': 'e1', 'source': 'src', 'target': 'scatter_c'}],
                }
                c_stage, _, _ = compile_graph(c_graph)
                c_inst = UsdGeom.PointInstancer(c_stage.GetPrimAtPath('/Scene/Scatter'))
                c_pos = list(c_inst.GetPositionsAttr().Get())
                c_ori = list(c_inst.GetOrientationsAttr().Get())
                self.assertEqual(len(c_pos), 4)
                for pos, q in zip(c_pos, c_ori):
                    r = math.hypot(pos[0], pos[2])
                    self.assertAlmostEqual(r, 10.0, places=3)
                    rot = Gf.Rotation(Gf.Quatd(q.GetReal(), Gf.Vec3d(q.GetImaginary())))
                    fwd = rot.TransformDir(Gf.Vec3d(0.0, 0.0, 1.0))
                    expected = expected_dir_fn(pos)
                    self.assertGreater(Gf.Dot(fwd, expected), 0.99, f'{mode}: got {fwd}, expected {expected}')

            # 4. Per-instance color variation primvars (displayColor & scatterRandom) + MaterialX tint injection
            mtlx_graph = {
                'targetNodeId': 'scatter_col',
                'dependencies': [{'id': 'cube', 'path': cube_path}],
                'nodes': [
                    {'id': 'src', 'operation': 'source', 'inputDependencyId': 'cube'},
                    {
                        'id': 'mtlx',
                        'operation': 'materialx',
                        'materialX': {
                            'name': 'LeafMat',
                            'baseColor': [0.4, 0.8, 0.2],
                            'roughness': 0.5,
                            'metalness': 0.0,
                        },
                    },
                    {
                        'id': 'scatter_col',
                        'operation': 'scatter',
                        'scatter': {
                            'count': 25,
                            'seed': 9,
                            'distribution': 'random',
                            'passThrough': True,
                            'colorVariationEnabled': True,
                            'colorVariationMode': 'ramp',
                            'colorRampA': [0.2, 0.8, 0.1],
                            'colorRampB': [0.9, 0.3, 0.1],
                            'colorApplyToMaterials': True,
                        },
                    },
                ],
                'edges': [
                    {'id': 'e1', 'source': 'src', 'target': 'mtlx'},
                    {'id': 'e2', 'source': 'mtlx', 'target': 'scatter_col'},
                ],
            }
            col_stage, _, _ = compile_graph(mtlx_graph, reload=True)
            col_inst = UsdGeom.PointInstancer(col_stage.GetPrimAtPath('/Scene/Scatter'))
            pv_api = UsdGeom.PrimvarsAPI(col_inst.GetPrim())
            sr = pv_api.GetPrimvar('scatterRandom')
            dc = pv_api.GetPrimvar('displayColor')
            self.assertTrue(sr and sr.IsDefined())
            self.assertTrue(dc and dc.IsDefined())
            self.assertEqual(sr.GetInterpolation(), UsdGeom.Tokens.vertex)
            self.assertEqual(dc.GetInterpolation(), UsdGeom.Tokens.vertex)
            sr_vals = list(sr.Get())
            dc_vals = list(dc.Get())
            self.assertEqual(len(sr_vals), 25)
            self.assertEqual(len(dc_vals), 25)
            for t, c in zip(sr_vals, dc_vals):
                self.assertAlmostEqual(c[0], (1.0 - t) * 0.2 + t * 0.9, places=4)
                self.assertAlmostEqual(c[1], (1.0 - t) * 0.8 + t * 0.3, places=4)
            # Verify MaterialX tint reader + multiply shader injected inside Scatter prototype material
            from pxr import UsdShade
            proto_mesh = None
            for p in col_stage.Traverse(Usd.TraverseInstanceProxies()):
                if str(p.GetPath()).startswith('/Scene/Scatter/Prototypes/Proto_0') and p.IsA(UsdGeom.Gprim):
                    proto_mesh = p
                    break
            self.assertIsNotNone(proto_mesh)
            bound_mat, _ = UsdShade.MaterialBindingAPI(proto_mesh).ComputeBoundMaterial()
            self.assertTrue(bound_mat and bound_mat.GetPrim().IsValid())
            mul_prim = col_stage.GetPrimAtPath(bound_mat.GetPath().AppendChild('ScatterTintMul'))
            rdr_prim = col_stage.GetPrimAtPath(bound_mat.GetPath().AppendChild('ScatterDisplayColor'))
            self.assertTrue(mul_prim and mul_prim.IsValid())
            self.assertTrue(rdr_prim and rdr_prim.IsValid())
            self.assertEqual(UsdShade.Shader(rdr_prim).GetIdAttr().Get(), 'ND_geompropvalue_color3')

    def test_source_load_payloads_toggle(self):
        import tempfile
        from pxr import Usd, UsdGeom
        from usd_graph import compile_graph

        with tempfile.TemporaryDirectory() as tmp:
            payload_path = str(pathlib.Path(tmp) / 'heavy.usda')
            p_stage = Usd.Stage.CreateNew(payload_path)
            p_root = UsdGeom.Xform.Define(p_stage, '/Heavy')
            UsdGeom.Cube.Define(p_stage, '/Heavy/DenseMesh')
            p_stage.SetDefaultPrim(p_root.GetPrim())
            p_stage.GetRootLayer().Save()

            asm_path = str(pathlib.Path(tmp) / 'assembly.usda')
            a_stage = Usd.Stage.CreateNew(asm_path)
            a_root = UsdGeom.Xform.Define(a_stage, '/Assembly')
            a_stage.SetDefaultPrim(a_root.GetPrim())
            holder = UsdGeom.Xform.Define(a_stage, '/Assembly/Prop')
            holder.GetPrim().GetPayloads().AddPayload(payload_path)
            a_stage.GetRootLayer().Save()

            graph_unloaded = {
                'targetNodeId': 'src',
                'dependencies': [{'id': 'asm', 'path': asm_path}],
                'nodes': [{'id': 'src', 'operation': 'source', 'inputDependencyId': 'asm', 'loadPayloads': False}],
                'edges': [],
            }
            st_unloaded, _, _ = compile_graph(graph_unloaded)
            self.assertFalse(st_unloaded.GetPrimAtPath('/Scene/Assembly/Prop/DenseMesh').IsValid())

            graph_loaded = {
                'targetNodeId': 'src',
                'dependencies': [{'id': 'asm', 'path': asm_path}],
                'nodes': [{'id': 'src', 'operation': 'source', 'inputDependencyId': 'asm', 'loadPayloads': True}],
                'edges': [],
            }
            st_loaded, _, _ = compile_graph(graph_loaded)
            self.assertTrue(st_loaded.GetPrimAtPath('/Scene/Assembly/Prop/DenseMesh').IsValid())

    def test_source_use_as_payload(self):
        from usd_graph import compile_graph

        cube_path = str(pathlib.Path(__file__).resolve().parents[1] / 'samples' / 'cube.usda')
        graph_unloaded = {
            'targetNodeId': 'src',
            'dependencies': [{'id': 'cube', 'path': cube_path}],
            'nodes': [{'id': 'src', 'operation': 'source', 'inputDependencyId': 'cube', 'useAsPayload': True, 'loadPayloads': False}],
            'edges': [],
        }
        st_unloaded, _, _ = compile_graph(graph_unloaded)
        world_unloaded = st_unloaded.GetPrimAtPath('/Scene/World')
        self.assertTrue(world_unloaded.IsValid())
        self.assertTrue(world_unloaded.HasAuthoredPayloads())
        self.assertFalse(world_unloaded.IsLoaded())
        self.assertFalse(st_unloaded.GetPrimAtPath('/Scene/World/Cube').IsValid())

        graph_loaded = {
            'targetNodeId': 'src',
            'dependencies': [{'id': 'cube', 'path': cube_path}],
            'nodes': [{'id': 'src', 'operation': 'source', 'inputDependencyId': 'cube', 'useAsPayload': True, 'loadPayloads': True}],
            'edges': [],
        }
        st_loaded, _, _ = compile_graph(graph_loaded)
        world_loaded = st_loaded.GetPrimAtPath('/Scene/World')
        self.assertTrue(world_loaded.HasAuthoredPayloads())
        self.assertTrue(world_loaded.IsLoaded())
        self.assertTrue(st_loaded.GetPrimAtPath('/Scene/World/Cube').IsValid())

    def test_compiled_graph_disk_cache_restore(self):
        from pxr import UsdGeom
        from usd_graph import compile_graph, clear_graph_caches

        cube_path = str(pathlib.Path(__file__).resolve().parents[1] / 'samples' / 'cube.usda')
        graph = {
            'targetNodeId': 'scatter_disk_test',
            'dependencies': [{'id': 'cube', 'path': cube_path}],
            'nodes': [
                {'id': 'src', 'operation': 'source', 'inputDependencyId': 'cube'},
                {
                    'id': 'scatter_disk_test',
                    'operation': 'scatter',
                    'scatter': {
                        'count': 250,
                        'seed': 99,
                        'distribution': 'circle',
                        'areaSize': [30, 0, 30],
                        'scaleMin': 0.8,
                        'scaleMax': 1.2,
                        'randomYaw': True,
                    },
                },
            ],
            'edges': [{'id': 'e1', 'source': 'src', 'target': 'scatter_disk_test'}],
        }
        stage1, _, stats1 = compile_graph(graph, reload=True, _disk_cache_threshold=0.0)
        self.assertGreaterEqual(stats1['instances'], 250)
        clear_graph_caches()
        stage2, alive2, stats2 = compile_graph(graph, reload=False)
        self.assertEqual(len(alive2), 1)
        self.assertEqual(stats2, stats1)
        inst = UsdGeom.PointInstancer(stage2.GetPrimAtPath('/Scene/Scatter'))
        self.assertEqual(len(inst.GetPositionsAttr().Get()), 250)

    def test_storm_draw_modes(self):
        caps = dispatch({'operation': 'capabilities'})
        if not caps.get('viewport'):
            self.skipTest('Hydra viewport runtime unavailable')
        path = str(pathlib.Path(__file__).resolve().parents[1] / 'samples' / 'cube.usda')
        for mode in ('shaded', 'solid', 'wireframe', 'points', 'bounds'):
            res = dispatch({
                'operation': 'render',
                'path': path,
                'renderer': 'HdStormRendererPlugin',
                'width': 128,
                'height': 128,
                'drawMode': mode,
                'camera': {'eye': [3, 3, 3], 'target': [0, 0, 0], 'up': [0, 1, 0], 'fov': 45},
            })
            self.assertIn('image', res)
            self.assertTrue(res['image'].startswith('data:image/png;base64,'))
            self.assertGreater(len(res['image']), 100)

    def test_compiled_graph_disk_cache(self):
        import os
        from usd_graph import compile_graph, clear_graph_caches, _compiled_graph_cache_paths

        cube_path = str(pathlib.Path(__file__).resolve().parents[1] / 'samples' / 'cube.usda')
        graph = {
            'targetNodeId': 'scatter1',
            'dependencies': [{'id': 'cube', 'path': cube_path}],
            'nodes': [
                {'id': 'src', 'operation': 'source', 'inputDependencyId': 'cube'},
                {
                    'id': 'scatter1',
                    'operation': 'scatter',
                    'scatter': {
                        'count': 50,
                        'seed': 99,
                        'distribution': 'random',
                        'areaSize': [10, 0, 10],
                        'scaleMin': 1.0,
                        'scaleMax': 1.0,
                        'randomYaw': False,
                    },
                },
            ],
            'edges': [{'id': 'e1', 'source': 'src', 'target': 'scatter1'}],
        }
        stage1, _, stats1 = compile_graph(graph, reload=True, _disk_cache_threshold=0.0)
        self.assertTrue(stage1.GetPrimAtPath('/Scene/Scatter').IsValid())
        clear_graph_caches()
        stage2, _, stats2 = compile_graph(graph, reload=False)
        self.assertTrue(stage2.GetPrimAtPath('/Scene/Scatter').IsValid())
        self.assertTrue(stage2.GetRootLayer().anonymous)
        self.assertEqual(stats2, stats1)

    def test_storm_light_normalization(self):
        from pxr import Usd, UsdGeom, UsdLux
        from usd_worker import _normalize_storm_lights

        stage = Usd.Stage.CreateInMemory()
        UsdGeom.Xform.Define(stage, '/Scene')
        light = UsdLux.DistantLight.Define(stage, '/Scene/Sun')
        light.CreateIntensityAttr().Set(3000.0)
        _normalize_storm_lights(stage, stage.GetSessionLayer(), 0.0)
        self.assertAlmostEqual(light.GetIntensityAttr().Get(0.0), 2.0, places=3)
        self.assertAlmostEqual(stage.GetRootLayer().GetAttributeAtPath('/Scene/Sun.inputs:intensity').default, 3000.0, places=3)


if __name__ == '__main__':
    unittest.main()
