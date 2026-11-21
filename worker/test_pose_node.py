import pathlib
import tempfile
import unittest
import usd_worker
from usd_graph import compile_graph, clear_graph_caches
from test_graph import skeletal_fixture
from pxr import Usd, UsdSkel, UsdGeom


class PoseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = skeletal_fixture(self.tmp.name)
        self.external = pathlib.Path(self.tmp.name) / 'clip.usda'
        source = Usd.Stage.Open(str(self.path))
        source.GetRootLayer().Export(str(self.external))
        self.before = pathlib.Path(self.path).read_bytes(), self.external.read_bytes()
        self.graph = {'targetNodeId': 'pose', 'dependencies': [
            {'id': 'rig', 'path': str(self.path)}, {'id': 'clip', 'path': str(self.external)}],
            'nodes': [{'id': 'source', 'operation': 'source', 'inputDependencyId': 'rig'},
                      {'id': 'pose', 'operation': 'pose', 'pose': {
                          'entries': [{'id': 'a', 'name': 'Sample', 'dependencyId': 'clip',
                                       'animationPath': '/Rig/Animation', 'mode': 'pose', 'poseTime': 1}],
                          'mappings': [{'id': 'r', 'skeletonPath': '/Scene/Rig/Skeleton', 'entryId': 'a'}]}}],
            'edges': [{'id': 'edge', 'source': 'source', 'target': 'pose'}]}

    def tearDown(self):
        clear_graph_caches()
        self.tmp.cleanup()

    def query(self):
        stage, alive, _ = compile_graph(self.graph)
        cache = UsdSkel.Cache()
        for prim in stage.Traverse():
            if prim.IsA(UsdSkel.Root):
                cache.Populate(UsdSkel.Root(prim), Usd.PrimDefaultPredicate)
        return stage, alive, cache.GetSkelQuery(UsdSkel.Skeleton(stage.GetPrimAtPath('/Scene/Rig/Skeleton')))

    def test_pose_clip_switch_and_upstream_restore_preserve_geometry(self):
        stage, alive, query = self.query()
        self.assertAlmostEqual(query.ComputeJointLocalTransforms(0)[0][3][0], 1)
        self.assertAlmostEqual(query.ComputeJointLocalTransforms(1)[0][3][0], 1)
        points = UsdGeom.Mesh(stage.GetPrimAtPath('/Scene/Rig/Mesh')).GetPointsAttr().Get()
        self.graph['nodes'][1]['pose']['entries'][0]['mode'] = 'clip'
        stage, alive, query = self.query()
        self.assertAlmostEqual(query.ComputeJointLocalTransforms(0)[0][3][0], 0)
        self.assertAlmostEqual(query.ComputeJointLocalTransforms(1)[0][3][0], 1)
        self.assertEqual(UsdGeom.Mesh(stage.GetPrimAtPath('/Scene/Rig/Mesh')).GetPointsAttr().Get(), points)
        self.graph['nodes'][1]['pose']['mappings'][0]['entryId'] = None
        stage, alive, query = self.query()
        self.assertEqual(str(query.GetAnimQuery().GetPrim().GetPath()), '/Scene/Rig/Animation')
        self.assertEqual(self.before, (pathlib.Path(self.path).read_bytes(), self.external.read_bytes()))

    def test_reject_incompatible_joints_and_missing_target(self):
        self.graph['nodes'][1]['pose']['mappings'][0]['skeletonPath'] = '/Missing'
        with self.assertRaisesRegex(ValueError, 'existing, editable Skeleton'):
            self.query()
        self.graph['nodes'][1]['pose']['mappings'][0]['skeletonPath'] = '/Scene/Rig/Skeleton'
        source = Usd.Stage.Open(str(self.external))
        UsdSkel.Animation(source.GetPrimAtPath('/Rig/Animation')).GetJointsAttr().Set(['wrongJoint'])
        source.GetRootLayer().Save()
        with self.assertRaisesRegex(ValueError, 'retargeting'):
            self.query()

    def test_upstream_animation_reference(self):
        entry = self.graph['nodes'][1]['pose']['entries'][0]
        entry.pop('dependencyId')
        entry['animationPath'] = '/Scene/Rig/Animation'
        entry['mode'] = 'clip'
        stage, alive, query = self.query()
        self.assertAlmostEqual(query.ComputeJointLocalTransforms(1)[0][3][0], 1)

    def test_authored_pose_capture_edit_preserves_points_and_blend_weights(self):
        from usd_graph import inspect_stage_skeletons
        import copy
        info = inspect_stage_skeletons({'graph':self.graph, 'frame':1})['skeletons'][0]
        self.assertEqual(info['parents'], [-1])
        self.assertEqual(info['worldPoints'], [[1, 0, 0]])
        cfg = self.graph['nodes'][1]['pose']
        cfg['entries'] = [{'id':'a', 'name':'Authored', 'mode':'pose', 'poseTime':1,
                           'animationPath':'', 'joints':copy.deepcopy(info['poseJoints']),
                           'blendShapes':['Smile'], 'blendShapeWeights':[0.7]}]
        stage, alive, query = self.query()
        self.assertAlmostEqual(query.ComputeJointLocalTransforms(0)[0][3][0], 1)
        weights = query.GetAnimQuery().ComputeBlendShapeWeights(0)
        self.assertAlmostEqual(weights[0], 0.7)
        points = UsdGeom.Mesh(stage.GetPrimAtPath('/Scene/Rig/Mesh')).GetPointsAttr().Get()
        joint = cfg['entries'][0]['joints'][0]
        joint['rotationOffset'] = [0, 0, 90]
        joint['translationOffset'] = [2, 0, 0]
        stage, alive, query = self.query()
        matrix = query.ComputeJointLocalTransforms(0)[0]
        self.assertAlmostEqual(matrix[3][0], 3)
        self.assertAlmostEqual(abs(matrix[0][1]), 1, places=5)
        self.assertEqual(UsdGeom.Mesh(stage.GetPrimAtPath('/Scene/Rig/Mesh')).GetPointsAttr().Get(), points)
        self.assertEqual(self.before, (pathlib.Path(self.path).read_bytes(), self.external.read_bytes()))
        cfg['entries'][0]['joints'][0]['name'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'joint order changed'):
            self.query()

    def test_external_revision_refresh_and_row_bypass(self):
        stage, alive, query = self.query()
        self.assertAlmostEqual(query.ComputeJointLocalTransforms(0)[0][3][0], 1)
        external = Usd.Stage.Open(str(self.external))
        anim = UsdSkel.Animation(external.GetPrimAtPath('/Rig/Animation'))
        anim.GetTranslationsAttr().Set([(3, 0, 0)], 1)
        external.GetRootLayer().Save()
        stage, alive, query = self.query()
        self.assertAlmostEqual(query.ComputeJointLocalTransforms(0)[0][3][0], 3)
        self.graph['nodes'][1]['pose']['mappings'][0]['enabled'] = False
        stage, alive, query = self.query()
        self.assertEqual(str(query.GetAnimQuery().GetPrim().GetPath()), '/Scene/Rig/Animation')

    def test_independent_skeleton_rows_and_downstream_composition(self):
        from pxr import Gf
        source = Usd.Stage.Open(str(self.path))
        head = UsdSkel.Skeleton.Define(source, '/Rig/Head')
        head.CreateJointsAttr(['root'])
        head.CreateBindTransformsAttr([Gf.Matrix4d(1)])
        head.CreateRestTransformsAttr([Gf.Matrix4d(1)])
        source.GetRootLayer().Save()
        cfg = self.graph['nodes'][1]['pose']
        cfg['entries'].append({**cfg['entries'][0], 'id': 'b', 'poseTime': 0})
        cfg['mappings'].append({'id': 'head', 'skeletonPath': '/Scene/Rig/Head', 'entryId': 'b'})
        self.graph['nodes'].append({'id': 'out', 'operation': 'transform', 'transform': {'translation': [0, 0, 0]}})
        self.graph['edges'].append({'id': 'po', 'source': 'pose', 'target': 'out'})
        self.graph['targetNodeId'] = 'out'
        stage, alive, query = self.query()
        self.assertAlmostEqual(query.ComputeJointLocalTransforms(0)[0][3][0], 1)
        cache = UsdSkel.Cache()
        cache.Populate(UsdSkel.Root(stage.GetPrimAtPath('/Scene/Rig')), Usd.PrimDefaultPredicate)
        head_query = cache.GetSkelQuery(UsdSkel.Skeleton(stage.GetPrimAtPath('/Scene/Rig/Head')))
        self.assertAlmostEqual(head_query.ComputeJointLocalTransforms(0)[0][3][0], 0)


if __name__ == '__main__':
    unittest.main()
