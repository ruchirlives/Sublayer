"""DNA adapter checks with real upstream USD; Epic binary fixture is opt-in."""
import hashlib
import os
import tempfile
import types
import unittest
from unittest.mock import patch

from pxr import Gf, Usd, UsdGeom, UsdSkel
from metahuman import load_dna, stage_from_reader

SDK = types.SimpleNamespace(Direction_left=0, Direction_up=2, Direction_front=4,
                            TranslationUnit_cm=0, TranslationUnit_m=1,
                            RotationUnit_degrees=0, RotationUnit_radians=1)


class Reader:
    """Child precedes its parent; split UV seam shares the position index."""
    def getLODCount(self): return 1
    def getCoordinateSystem(self): return types.SimpleNamespace(xAxis=0, yAxis=2, zAxis=4)
    def getTranslationUnit(self): return 0
    def getRotationUnit(self): return 0
    def getName(self): return 'Fixture'
    def getJointCount(self): return 2
    def getJointParentIndex(self, i): return 1
    def getJointName(self, i): return ['child', 'root'][i]
    def getNeutralJointTranslation(self, i): return [(0, 2, 0), (1, 0, 0)][i]
    def getNeutralJointRotation(self, i): return (0, 0, 90) if i == 1 else (0, 0, 0)
    def getBlendShapeChannelCount(self): return 1
    def getBlendShapeChannelName(self, i): return 'smile'
    def getMeshIndicesForLOD(self, lod): return [0]
    def getMeshCount(self): return 1
    def getMeshName(self, i): return 'head'
    def getVertexPositionCount(self, m): return 3
    def getVertexPosition(self, m, i): return [(0, 0, 0), (1, 0, 0), (0, 1, 0)][i]
    def getVertexLayoutCount(self, m): return 4
    def getVertexLayoutPositionIndices(self, m): return [0, 1, 2, 0]
    def getFaceCount(self, m): return 2
    def getFaceVertexLayoutIndices(self, m, i): return [[0, 1, 2], [3, 2, 1]][i]
    def getVertexTextureCoordinateCount(self, m): return 4
    def getVertexLayoutTextureCoordinateIndices(self, m): return [0, 1, 2, 3]
    def getVertexTextureCoordinate(self, m, i): return [(0, 0), (1, 0), (0, 1), (1, 1)][i]
    def getVertexNormalCount(self, m): return 1
    def getVertexLayoutNormalIndices(self, m): return [0] * 4
    def getVertexNormal(self, m, i): return (0, 0, 1)
    def getSkinWeightsCount(self, m): return 3
    def getSkinWeightsJointIndices(self, m, i): return [0, 1] if i == 0 else [1]
    def getSkinWeightsValues(self, m, i): return [0.25, 0.75] if i == 0 else [1.0]
    def getBlendShapeTargetCount(self, m): return 1
    def getBlendShapeChannelIndex(self, m, t): return 0
    def getBlendShapeTargetVertexIndices(self, m, t): return [2, 0]
    def getBlendShapeTargetDeltaCount(self, m, t): return 2
    def getBlendShapeTargetDelta(self, m, t, i): return [(0, 0, 2), (0, 0, 1)][i]


class MetaHumanTests(unittest.TestCase):
    def test_neutral_rig_topology_seams_weights_and_sparse_shapes(self):
        stage = stage_from_reader(Reader(), 0, SDK)
        self.assertTrue(stage.GetRootLayer().anonymous)
        self.assertEqual(UsdGeom.GetStageMetersPerUnit(stage), 0.01)
        skel = UsdSkel.Skeleton.Get(stage, '/MetaHuman/Skeleton')
        self.assertEqual(list(skel.GetJointsAttr().Get()), ['root', 'root/child'])
        self.assertTrue(UsdSkel.Topology(skel.GetJointsAttr().Get()).Validate()[0])
        self.assertLess((skel.GetBindTransformsAttr().Get()[1].ExtractTranslation() - Gf.Vec3d(-1, 0, 0)).GetLength(), 1e-6)
        mesh = UsdGeom.Mesh.Get(stage, '/MetaHuman/head_0')
        self.assertEqual(list(mesh.GetFaceVertexIndicesAttr().Get()), [0, 1, 2, 0, 2, 1])
        self.assertEqual(list(UsdGeom.PrimvarsAPI(mesh).GetPrimvar('st').GetIndices()), [0, 1, 2, 3, 2, 1])
        binding = UsdSkel.BindingAPI(mesh)
        self.assertEqual(list(binding.GetJointIndicesPrimvar().Get()), [1, 0, 0, 0, 0, 0])
        self.assertEqual(list(binding.GetBlendShapesAttr().Get()), ['smile'])
        shape = UsdSkel.BlendShape.Get(stage, '/MetaHuman/head_0/BlendShape_0')
        self.assertEqual(list(shape.GetPointIndicesAttr().Get()), [0, 2])
        self.assertEqual(list(shape.GetOffsetsAttr().Get()), [Gf.Vec3f(0, 0, 1), Gf.Vec3f(0, 0, 2)])
        cache = UsdSkel.Cache()
        cache.Populate(UsdSkel.Root(stage.GetDefaultPrim()), Usd.PrimDefaultPredicate)
        query = cache.GetSkelQuery(skel)
        self.assertTrue(query)
        # Skinning transforms at rest are identity, including rotated parents.
        for matrix in query.ComputeSkinningTransforms(0):
            self.assertTrue(Gf.IsClose(matrix, Gf.Matrix4d(1), 1e-6))

    def test_bad_dna_data_fails_closed(self):
        cases = [('getJointParentIndex', lambda i: 99),
                 ('getJointParentIndex', lambda i: 1-i),
                 ('getSkinWeightsValues', lambda m, i: [0.2]),
                 ('getFaceVertexLayoutIndices', lambda m, i: [0, 1, 99]),
                 ('getVertexLayoutTextureCoordinateIndices', lambda m: [0]),
                 ('getBlendShapeTargetVertexIndices', lambda m, t: [0, 0]),
                 ('getVertexPosition', lambda m, i: (float('nan'), 0, 0)),
                 ('getCoordinateSystem', lambda: types.SimpleNamespace(xAxis=1, yAxis=2, zAxis=4))]
        for method, value in cases:
            with self.subTest(method=method):
                reader = Reader()
                setattr(reader, method, value)
                with self.assertRaises(ValueError): stage_from_reader(reader, 0, SDK)
        for lod in (-1, 1, True, 0.5):
            with self.assertRaises(ValueError): stage_from_reader(Reader(), lod, SDK)

    def test_graph_composition_and_file_preservation(self):
        from usd_graph import compile_graph
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'source.dna')
            with open(path, 'wb') as f: f.write(b'fixture')
            graph = {'targetNodeId':'dna', 'dependencies':[{'id':'file', 'path':path}],
                     'nodes':[{'id':'dna', 'operation':'metahuman', 'inputDependencyId':'file', 'metahumanLod':0}], 'edges':[]}
            with patch('metahuman.load_dna', side_effect=lambda *args: stage_from_reader(Reader(), 0, SDK)) as load:
                stage, *alive = compile_graph(graph, reload=True)
                self.assertTrue(stage.GetPrimAtPath('/Scene/MetaHuman/Skeleton'))
                load.assert_called_once_with(path, 0)
            with open(path, 'rb') as f: self.assertEqual(f.read(), b'fixture')

    def test_missing_reader_and_rejected_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'bad.dna')
            with open(path, 'wb') as f: f.write(b'bad')
            with patch('metahuman.importlib.import_module', side_effect=ImportError()):
                with self.assertRaisesRegex(ValueError, 'Python.*ORCHFLOW_DNA_ROOT'): load_dna(path)
            sdk = types.SimpleNamespace(FileStream=lambda *args: None, BinaryStreamReader=lambda *args: types.SimpleNamespace(read=lambda: None),
                DataLayer_All=0, Status=types.SimpleNamespace(isOk=lambda:False, get=lambda:types.SimpleNamespace(message='version mismatch')))
            sdk.FileStream.AccessMode_Read=0; sdk.FileStream.OpenMode_Binary=0
            with patch('metahuman._sdk', return_value=sdk):
                with self.assertRaisesRegex(ValueError, 'version mismatch'): load_dna(path)

    def test_head_and_body_unified_skeleton_and_graph(self):
        from metahuman import compose_dna_pair
        from usd_graph import compile_graph

        class BodyReader(Reader):
            def getName(self): return 'Body'
            def getJointCount(self): return 3
            def getJointParentIndex(self, i): return [0, 0, 1][i]
            def getJointName(self, i): return ['root', 'spine_04', 'neck_01'][i]
            def getNeutralJointTranslation(self, i): return [(0, 0, 0), (0, 140, 0), (0, 15, 0)][i]
            def getNeutralJointRotation(self, i): return (0, 0, 0)
            def getMeshName(self, i): return 'body_mesh'
            def getSkinWeightsJointIndices(self, m, i): return [1]
            def getSkinWeightsValues(self, m, i): return [1.0]
            def getBlendShapeChannelCount(self): return 0
            def getBlendShapeTargetCount(self, m): return 0

        class HeadReader(Reader):
            def getName(self): return 'Head'
            def getJointCount(self): return 3
            def getJointParentIndex(self, i): return [0, 0, 1][i]
            def getJointName(self, i): return ['spine_04', 'neck_01', 'FACIAL_C_Jaw'][i]
            # Simulate a slightly different character height (135 vs body's 140)
            def getNeutralJointTranslation(self, i): return [(0, 135, 0), (0, 15, 0), (0, 5, 3)][i]
            def getNeutralJointRotation(self, i): return (0, 0, 0)
            def getMeshName(self, i): return 'head_mesh'
            def getSkinWeightsJointIndices(self, m, i): return [2] if i == 0 else [1]
            def getSkinWeightsValues(self, m, i): return [1.0]

        body_stage = stage_from_reader(BodyReader(), 0, SDK)
        head_stage = stage_from_reader(HeadReader(), 0, SDK)
        combined = compose_dna_pair(head_stage, body_stage)

        skel = UsdSkel.Skeleton.Get(combined, '/MetaHuman/Skeleton')
        self.assertTrue(skel)
        joints = list(skel.GetJointsAttr().Get())
        self.assertEqual(joints, ['root', 'root/spine_04', 'root/spine_04/neck_01', 'root/spine_04/neck_01/FACIAL_C_Jaw'])
        self.assertTrue(UsdSkel.Topology(joints).Validate()[0])

        # Verify head mesh skin indices remapped from [2, 1, 1] -> [3, 2, 2] and geomBindTransform shifts Y by +5
        head_mesh = UsdGeom.Mesh.Get(combined, '/MetaHuman/Head/head_mesh_0')
        self.assertTrue(head_mesh)
        head_binding = UsdSkel.BindingAPI(head_mesh.GetPrim())
        self.assertEqual(list(head_binding.GetJointIndicesPrimvar().Get()), [3, 2, 2])
        gbt = head_binding.GetGeomBindTransformAttr().Get()
        self.assertAlmostEqual(gbt.ExtractTranslation()[1], 5.0, places=4)
        self.assertEqual(list(head_binding.GetBlendShapeTargetsRel().GetTargets()), [head_mesh.GetPath().AppendChild('BlendShape_0')])

        cache = UsdSkel.Cache()
        cache.Populate(UsdSkel.Root(combined.GetDefaultPrim()), Usd.PrimDefaultPredicate)
        for matrix in cache.GetSkelQuery(skel).ComputeSkinningTransforms(0):
            self.assertTrue(Gf.IsClose(matrix, Gf.Matrix4d(1), 1e-6))

        # Verify graph compilation with both inputDependencyId and metahumanBodyDependencyId
        with tempfile.TemporaryDirectory() as tmp:
            h_path = os.path.join(tmp, 'head.dna')
            b_path = os.path.join(tmp, 'body.dna')
            open(h_path, 'wb').write(b'h')
            open(b_path, 'wb').write(b'b')
            graph = {
                'targetNodeId': 'mh',
                'dependencies': [{'id': 'h', 'path': h_path}, {'id': 'b', 'path': b_path}],
                'nodes': [{'id': 'mh', 'operation': 'metahuman', 'inputDependencyId': 'h', 'metahumanBodyDependencyId': 'b', 'metahumanLod': 0}],
                'edges': [],
            }
            def _fake_load(p, lod=0):
                return head_stage if os.path.abspath(p) == os.path.abspath(h_path) else body_stage
            with patch('metahuman.load_dna', side_effect=_fake_load):
                stage, *alive = compile_graph(graph, reload=True)
                self.assertTrue(stage.GetPrimAtPath('/Scene/MetaHuman/Skeleton').IsValid())
                self.assertTrue(stage.GetPrimAtPath('/Scene/MetaHuman/Head/head_mesh_0').IsValid())
                self.assertTrue(stage.GetPrimAtPath('/Scene/MetaHuman/Body/body_mesh_0').IsValid())

    def test_body_riglogic_rbf_and_twist_swing_evaluation(self):
        import math
        from metahuman_riglogic import BodyRigLogicSolver

        rl_sdk = types.SimpleNamespace(
            RotationUnit_degrees=0, RotationUnit_radians=1,
            RBFDistanceMethod_Euclidean=0, RBFDistanceMethod_Quaternion=1,
            RBFDistanceMethod_SwingAngle=2, RBFDistanceMethod_TwistAngle=3,
            RBFFunctionType_Gaussian=0, RBFFunctionType_Exponential=1,
            RBFFunctionType_Linear=2, RBFFunctionType_Cubic=3, RBFFunctionType_Quintic=4,
            RBFNormalizeMethod_OnlyNormalizeAboveOne=0, RBFNormalizeMethod_AlwaysNormalize=1,
            RBFSolverType_Additive=0, RBFSolverType_Interpolative=1,
            TwistAxis_X=0, TwistAxis_Y=1, TwistAxis_Z=2,
        )

        # Joint 0: upperarm_r (driver), Joint 1: clavicle_pec_r (RBF driven), Joint 2: upperarm_twist_01_r (twist driven)
        q90_z = (0.0, 0.0, math.sin(math.radians(45.0)), math.cos(math.radians(45.0)))

        class RLReader:
            def getJointCount(self): return 3
            def getJointName(self, i): return ['upperarm_r', 'clavicle_pec_r', 'upperarm_twist_01_r'][i]
            # 4 raw controls; RBF pose output control lives at extended index 4 (>= rawControlCount)
            def getRawControlCount(self): return 4
            def getRawControlName(self, i): return ['upperarm_r.qx', 'upperarm_r.qy', 'upperarm_r.qz', 'upperarm_r.qw'][i]
            def getRotationUnit(self): return 0
            def getNeutralJointTranslation(self, i): return [(0, 0, 0), (5, 10, 0), (0, 0, 0)][i]
            def getNeutralJointRotation(self, i): return (0, 0, 0)
            def getRBFSolverCount(self): return 1
            def getRBFSolverRawControlIndices(self, s): return [0, 1, 2, 3]
            def getRBFSolverPoseIndices(self, s): return [0, 1]
            def getRBFSolverRawControlValues(self, s): return [0.0, 0.0, 0.0, 1.0, *q90_z]
            def getRBFPoseScale(self, p): return 1.0
            def getRBFPoseInputControlIndices(self, p): return []
            def getRBFPoseOutputControlIndices(self, p): return [] if p == 0 else [4]
            def getRBFPoseOutputControlWeights(self, p): return [] if p == 0 else [1.0]
            def getRBFSolverRadius(self, s): return 1.5
            def getRBFSolverAutomaticRadius(self, s): return True
            def getRBFSolverWeightThreshold(self, s): return 0.001
            def getRBFSolverDistanceMethod(self, s): return 1
            def getRBFSolverFunctionType(self, s): return 0
            def getRBFSolverNormalizeMethod(self, s): return 0
            def getRBFSolverTwistAxis(self, s): return 0
            def getRBFSolverType(self, s): return 1  # Interpolative
            def getSwingCount(self): return 0
            def getTwistCount(self): return 1
            def getTwistInputControlIndices(self, t): return [0, 1, 2, 3]
            def getTwistOutputJointIndices(self, t): return [2]
            def getTwistBlendWeights(self, t): return [0.5]
            def getTwistSetupTwistAxis(self, t): return 2  # Z axis
            def getJointGroupCount(self): return 1
            def getJointGroupInputIndices(self, g): return [4]
            def getJointGroupOutputIndices(self, g): return [1 * 9 + 1]  # clavicle_pec_r ty
            def getJointGroupValues(self, g): return [3.0]
            def getJointGroupJointIndices(self, g): return [1]

        solver = BodyRigLogicSolver(RLReader(), rl_sdk)
        names = ['root/upperarm_r', 'root/clavicle_pec_r', 'root/upperarm_twist_01_r']
        # At rest pose: clavicle_pec_r stays at neutral Y=10
        rest_mats = [Gf.Matrix4d(1), Gf.Matrix4d(1).SetTranslate(Gf.Vec3d(5, 10, 0)), Gf.Matrix4d(1)]
        rest_upd = solver.evaluate(names, rest_mats)
        self.assertAlmostEqual(rest_upd[1].ExtractTranslation()[1], 10.0, places=3)
        self.assertNotIn(0, rest_upd)  # Driver joint is never overwritten

        # At 90 deg Z rotation: clavicle_pec_r lifts by +3 (Y=13) and twist_01_r gets half twist (45 deg Z)
        posed_mats = [Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d.ZAxis(), 90.0)), rest_mats[1], Gf.Matrix4d(1)]
        posed_upd = solver.evaluate(names, posed_mats)
        self.assertAlmostEqual(posed_upd[1].ExtractTranslation()[1], 13.0, places=2)
        tw_rot = posed_upd[2].ExtractRotation()
        self.assertAlmostEqual(abs(tw_rot.GetAngle()), 45.0, places=1)

    def test_metahuman_auto_materials_discover_sibling_textures(self):
        from pxr import UsdShade
        from metahuman import apply_metahuman_materials

        stage = stage_from_reader(Reader(), 0, SDK)
        with tempfile.TemporaryDirectory() as tmp:
            dna_path = os.path.join(tmp, 'head.dna')
            tex_dir = os.path.join(tmp, 'Textures')
            os.makedirs(tex_dir)
            bc_path = os.path.join(tex_dir, 'head_basecolor.png')
            nrm_path = os.path.join(tex_dir, 'head_normal.png')
            with open(dna_path, 'wb') as f: f.write(b'dna')
            with open(bc_path, 'wb') as f: f.write(b'png')
            with open(nrm_path, 'wb') as f: f.write(b'png')

            apply_metahuman_materials(stage, [dna_path])
            mesh = stage.GetPrimAtPath('/MetaHuman/head_0')
            mat, _ = UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()
            self.assertTrue(mat and mat.GetPrim().IsValid())
            std = UsdShade.Shader(stage.GetPrimAtPath(mat.GetPath().AppendChild('StandardSurface')))
            self.assertEqual(std.GetIdAttr().Get(), 'ND_standard_surface_surfaceshader')
            bc_img = UsdShade.Shader(stage.GetPrimAtPath(mat.GetPath().AppendChild('MtlxBaseColor')))
            self.assertEqual(os.path.normcase(bc_img.GetInput('file').Get().path), os.path.normcase(os.path.abspath(bc_path)))
            nrm_img = UsdShade.Shader(stage.GetPrimAtPath(mat.GetPath().AppendChild('MtlxNormalTex')))
            self.assertEqual(os.path.normcase(nrm_img.GetInput('file').Get().path), os.path.normcase(os.path.abspath(nrm_path)))

    def test_metahuman_eyelashes_rgba_alpha_extraction(self):
        import struct
        from pxr import UsdShade
        from metahuman import apply_metahuman_materials

        class LashReader(Reader):
            def getMeshName(self, i): return 'eyelashes_lod0_mesh'

        stage = stage_from_reader(LashReader(), 0, SDK)
        with tempfile.TemporaryDirectory() as tmp:
            dna_path = os.path.join(tmp, 'head.dna')
            tex_dir = os.path.join(tmp, 'Textures')
            os.makedirs(tex_dir)
            lash_path = os.path.join(tex_dir, 'eyelashes_color.png')
            with open(dna_path, 'wb') as f: f.write(b'dna')
            # Minimal valid PNG signature + IHDR with color_type=6 (RGBA)
            ihdr = struct.pack('>IIBBBBB', 2, 2, 8, 6, 0, 0, 0)
            with open(lash_path, 'wb') as f:
                f.write(b'\x89PNG\r\n\x1a\n' + struct.pack('>I', len(ihdr)) + b'IHDR' + ihdr + b'\x00\x00\x00\x00')

            apply_metahuman_materials(stage, [dna_path])
            mesh = stage.GetPrimAtPath('/MetaHuman/eyelashes_lod0_mesh_0')
            mat, _ = UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()
            self.assertTrue(mat and mat.GetPrim().IsValid())
            rgba = UsdShade.Shader(stage.GetPrimAtPath(mat.GetPath().AppendChild('MtlxBaseColorRGBA')))
            self.assertEqual(rgba.GetIdAttr().Get(), 'ND_image_color4')
            ext = UsdShade.Shader(stage.GetPrimAtPath(mat.GetPath().AppendChild('MtlxAlphaExtract')))
            self.assertEqual(ext.GetIdAttr().Get(), 'ND_extract_color4')
            self.assertEqual(ext.GetInput('index').Get(), 3)
            std = UsdShade.Shader(stage.GetPrimAtPath(mat.GetPath().AppendChild('StandardSurface')))
            self.assertTrue(std.GetInput('opacity').HasConnectedSource())
            prev = UsdShade.Shader(stage.GetPrimAtPath(mat.GetPath().AppendChild('PreviewSurface')))
            src_api, src_name, _ = prev.GetInput('opacity').GetConnectedSource()
            self.assertEqual(src_name, 'a')
            self.assertAlmostEqual(prev.GetInput('opacityThreshold').Get(), 0.05, places=4)

    @unittest.skipUnless(os.environ.get('ORCHFLOW_TEST_DNA'), 'No real Epic DNA fixture selected')
    def test_real_epic_dna_read_only(self):
        path = os.environ['ORCHFLOW_TEST_DNA']
        with open(path, 'rb') as f: before = hashlib.sha256(f.read()).digest()
        stage = load_dna(path, 0)
        self.assertTrue(UsdSkel.Skeleton.Get(stage, '/MetaHuman/Skeleton'))
        self.assertGreater(sum(p.IsA(UsdGeom.Mesh) for p in stage.Traverse()), 0)
        from usd_graph import compile_graph
        graph = {'namespaceVersion':2, 'targetNodeId':'dna',
                 'nodes':[{'id':'dna', 'operation':'metahuman', 'inputDependencyId':'file'}],
                 'edges':[], 'dependencies':[{'id':'file', 'path':path}]}
        composed, alive, stats = compile_graph(graph, reload=True)
        self.assertTrue(composed.GetPrimAtPath('/Scene/MetaHuman/Skeleton'))
        self.assertEqual(UsdGeom.GetStageMetersPerUnit(composed), 1)
        source_point = next(UsdGeom.Mesh(p) for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)).GetPointsAttr().Get()[0]
        composed_mesh = next(UsdGeom.Mesh(p) for p in composed.Traverse() if p.IsA(UsdGeom.Mesh))
        world = UsdGeom.XformCache().GetLocalToWorldTransform(composed_mesh.GetPrim()).Transform(Gf.Vec3d(source_point))
        self.assertLess((world - Gf.Vec3d(source_point) * UsdGeom.GetStageMetersPerUnit(stage)).GetLength(), 1e-5)
        with open(path, 'rb') as f: self.assertEqual(hashlib.sha256(f.read()).digest(), before)

    @unittest.skipUnless(os.environ.get('ORCHFLOW_TEST_DNA_DIR'), 'No DNA fixture directory selected')
    def test_real_dna_directory_all_lods_and_source_integrity(self):
        from pathlib import Path
        from metahuman import _sdk
        dna = _sdk()
        paths = sorted(Path(os.environ['ORCHFLOW_TEST_DNA_DIR']).rglob('*.dna'))
        self.assertTrue(paths)
        for path in paths:
            before = hashlib.sha256(path.read_bytes()).digest()
            stream = dna.FileStream(str(path), dna.FileStream.AccessMode_Read, dna.FileStream.OpenMode_Binary)
            reader = dna.BinaryStreamReader(stream, dna.DataLayer_All)
            reader.read()
            self.assertTrue(dna.Status.isOk())
            for lod in range(reader.getLODCount()):
                with self.subTest(path=str(path), lod=lod):
                    stage = stage_from_reader(reader, lod, dna)
                    skel = UsdSkel.Skeleton.Get(stage, '/MetaHuman/Skeleton')
                    self.assertTrue(UsdSkel.Topology(skel.GetJointsAttr().Get()).Validate()[0])
                    cache = UsdSkel.Cache()
                    cache.Populate(UsdSkel.Root(stage.GetDefaultPrim()), Usd.PrimDefaultPredicate)
                    for matrix in cache.GetSkelQuery(skel).ComputeSkinningTransforms(0):
                        self.assertTrue(Gf.IsClose(matrix, Gf.Matrix4d(1), 1e-4))
                    meshes = [UsdGeom.Mesh(p) for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
                    ids = list(reader.getMeshIndicesForLOD(lod))
                    self.assertEqual(len(meshes), len(ids))
                    for mesh, i in zip(meshes, ids):
                        self.assertEqual(len(mesh.GetPointsAttr().Get()), reader.getVertexPositionCount(i))
                        self.assertEqual(len(mesh.GetFaceVertexCountsAttr().Get()), reader.getFaceCount(i))
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)
            print(f'Verified all {reader.getLODCount()} LODs: {path}', flush=True)


if __name__ == '__main__': unittest.main()
