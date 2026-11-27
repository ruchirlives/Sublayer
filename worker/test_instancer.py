import copy
import pathlib
import tempfile
import unittest
from pxr import Gf, Usd, UsdGeom, UsdShade, UsdSkel
from usd_graph import compile_graph, instancer_handles, instancer_root

class InstancerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(pathlib.Path(self.temp.name) / 'source.usda')
        stage = Usd.Stage.CreateNew(self.path)
        world = UsdGeom.Xform.Define(stage, '/World')
        stage.SetDefaultPrim(world.GetPrim())
        UsdGeom.SetStageMetersPerUnit(stage, 1)
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        parent = UsdGeom.Xform.Define(stage, '/World/Parent')
        UsdGeom.XformCommonAPI(parent).SetTranslate(Gf.Vec3d(4, 2, 1))
        UsdGeom.XformCommonAPI(parent).SetRotate(Gf.Vec3f(0, 30, 0))
        mesh = UsdGeom.Cube.Define(stage, '/World/Parent/Cube')
        UsdGeom.XformCommonAPI(mesh).SetTranslate(Gf.Vec3d(1, 0, 0))
        material = UsdShade.Material.Define(stage, '/World/Looks/Red')
        shader = UsdShade.Shader.Define(stage, '/World/Looks/Red/Shader')
        shader.CreateIdAttr('UsdPreviewSurface')
        shader.CreateInput('diffuseColor', __import__('pxr').Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1, 0, 0))
        material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), 'surface')
        UsdShade.MaterialBindingAPI.Apply(parent.GetPrim()).Bind(material)
        UsdGeom.PrimvarsAPI(parent).CreatePrimvar('test', __import__('pxr').Sdf.ValueTypeNames.Float, 'constant').Set(0.7)
        stage.GetRootLayer().Save()
        self.before = pathlib.Path(self.path).read_bytes()
        self.graph = {'targetNodeId': 'manual', 'dependencies': [{'id': 'asset', 'path': self.path}], 'nodes': [
            {'id':'source','operation':'source','inputDependencyId':'asset','instanced':False},
            {'id':'manual','operation':'instancer','instancer':{'instances':[
                {'id':'one','sourcePath':'/Scene/World/Parent/Cube','visible':True,'transform':{'translation':[3,0,0]}},
                {'id':'two','sourcePath':'/Scene/World/Parent/Cube','visible':True,'transform':{'translation':[6,0,0]}},
            ]}},
        ], 'edges':[{'id':'edge','source':'source','target':'manual'}]}

    def compile(self, graph=None):
        return compile_graph(graph or self.graph)

    def test_sharing_materials_world_placement_and_source_preservation(self):
        stage, alive, stats = self.compile()
        handles = instancer_handles(stage)
        self.assertEqual(len(handles), 2)
        first, second = [stage.GetPrimAtPath(h['path']+'/Instance') for h in handles]
        self.assertTrue(first.IsInstance())
        self.assertEqual(first.GetPrototype(), second.GetPrototype())
        cache = UsdGeom.XformCache()
        original = cache.GetLocalToWorldTransform(stage.GetPrimAtPath('/Scene/World/Parent/Cube'))
        cube = stage.GetPrimAtPath(str(first.GetPath())+'/Content')
        actual = cache.GetLocalToWorldTransform(cube)
        expected = original * Gf.Matrix4d().SetTranslate(Gf.Vec3d(3,0,0))
        self.assertTrue(Gf.IsClose(actual, expected, 1e-6))
        material, _ = UsdShade.MaterialBindingAPI(cube).ComputeBoundMaterial()
        self.assertTrue(material)
        self.assertEqual(material.ComputeSurfaceSource()[0].GetIdAttr().Get(), 'UsdPreviewSurface')
        self.assertAlmostEqual(UsdGeom.PrimvarsAPI(cube).GetPrimvar('test').Get(), 0.7, places=5)
        self.assertEqual(pathlib.Path(self.path).read_bytes(), self.before)
        self.assertTrue(stage.GetRootLayer().anonymous)
        self.assertEqual(len(list(pathlib.Path(self.temp.name).iterdir())),1)

    def test_transformed_scene_and_downstream_transform(self):
        graph = copy.deepcopy(self.graph)
        graph['nodes'].insert(1, {'id':'move','operation':'transform','transform':{'translation':[10,5,2],'rotation':[0,20,0],'scale':[2,2,2]}})
        graph['edges'] = [{'id':'a','source':'source','target':'move'},{'id':'b','source':'move','target':'manual'}]
        stage, alive, _ = self.compile(graph)
        cache = UsdGeom.XformCache()
        original = cache.GetLocalToWorldTransform(stage.GetPrimAtPath('/Scene/World/Parent/Cube'))
        content = stage.GetPrimAtPath(instancer_handles(stage)[0]['path']+'/Instance/Content')
        self.assertTrue(Gf.IsClose(cache.GetLocalToWorldTransform(content), original*Gf.Matrix4d().SetTranslate(Gf.Vec3d(3,0,0)), 1e-6))
        graph['nodes'].append({'id':'after','operation':'transform','transform':{'translation':[20,0,0]}})
        graph['edges'].append({'id':'c','source':'manual','target':'after'})
        graph['targetNodeId']='after'
        after, alive2, _ = self.compile(graph)
        self.assertAlmostEqual(instancer_handles(after)[0]['origin'][0]-instancer_handles(stage)[0]['origin'][0],20,places=5)

    def test_crud_ids_proxy_selection_and_validation(self):
        self.graph['nodes'][0]['instanced']=True
        stage, alive, _ = self.compile()
        self.assertEqual(instancer_handles(stage)[0]['sourcePath'],'/Scene/World')
        self.graph['nodes'][1]['instancer']['instances'].pop(0)
        stage, alive, _ = self.compile()
        self.assertEqual([h['id'] for h in instancer_handles(stage)],['two'])
        self.graph['nodes'][1]['instancer']['instances'][0]['sourcePath']='/Scene/Unknown'
        with self.assertRaisesRegex(ValueError,'missing'):
            self.compile()
        self.graph['nodes'][1]['instancer']['instances']=[]
        stage, alive, _ = self.compile()
        self.assertTrue(stage.GetPrimAtPath('/Scene/World'))
        self.assertEqual(instancer_handles(stage),[])

    def test_multi_input_namespaces_and_hidden_placements(self):
        import hashlib
        graph = copy.deepcopy(self.graph)
        graph['nodes'].insert(1, {'id':'other','operation':'source','inputDependencyId':'asset','instanced':False})
        graph['edges'].append({'id':'other-edge','source':'other','target':'manual'})
        prefix = '/Scene/Branch_' + hashlib.sha256(b'edge').hexdigest()[:16]
        for item in graph['nodes'][-1]['instancer']['instances']:
            item['sourcePath'] = prefix + '/World/Parent/Cube'
        graph['nodes'][-1]['instancer']['instances'][0]['visible'] = False
        stage, alive, _ = self.compile(graph)
        self.assertTrue(stage.GetPrimAtPath(prefix+'/World/Parent/Cube'))
        handles = instancer_handles(stage)
        self.assertEqual(len(handles),2)
        self.assertEqual(UsdGeom.Imageable(stage.GetPrimAtPath(handles[0]['path'])).ComputeVisibility(),'invisible')
        self.assertEqual(UsdGeom.Imageable(stage.GetPrimAtPath(prefix+'/World/Parent/Cube')).ComputeVisibility(),'inherited')

    def test_animation_and_skeletal_rejection(self):
        source=Usd.Stage.Open(self.path)
        op=UsdGeom.Xformable(source.GetPrimAtPath('/World/Parent')).GetOrderedXformOps()[0]
        op.Set(Gf.Vec3d(4,2,1),0);op.Set(Gf.Vec3d(8,2,1),10)
        source.GetRootLayer().Save()
        stage, alive, _ = self.compile()
        self.assertAlmostEqual(instancer_handles(stage,10)[0]['origin'][0]-instancer_handles(stage,0)[0]['origin'][0],4,places=5)
        source=Usd.Stage.Open(self.path)
        UsdSkel.Root.Define(source,'/World/Character')
        UsdSkel.Skeleton.Define(source,'/World/Character/Skeleton')
        source.GetRootLayer().Save()
        self.graph['nodes'][1]['instancer']['instances'][0]['sourcePath']='/Scene/World/Character'
        with self.assertRaisesRegex(ValueError,'skeletal'):
            self.compile()

if __name__ == '__main__':
    unittest.main()
