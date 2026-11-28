"""Real USD composition tests; no DCC execution or external-file writes."""
import copy
import pathlib
import tempfile
import unittest
from pxr import Sdf, Usd, UsdGeom, UsdShade, UsdVol
from usd_graph import compile_graph
import fog

class FogTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = pathlib.Path(tmp.name) / 'vol.usda'
        self.vdb = pathlib.Path(tmp.name) / 'density.vdb'
        self.vdb.write_bytes(b'fixture: composition only')
        stage = Usd.Stage.CreateNew(str(self.path))
        root = UsdGeom.Xform.Define(stage, '/Asset')
        stage.SetDefaultPrim(root.GetPrim())
        UsdGeom.SetStageUpAxis(stage, 'Y')
        UsdGeom.SetStageMetersPerUnit(stage, 1)
        UsdGeom.Cube.Define(stage, '/Asset/Cube')
        vol = UsdVol.Volume.Define(stage, '/Asset/Smoke')
        field = UsdVol.OpenVDBAsset.Define(stage, '/Asset/Smoke/density')
        field.CreateFieldNameAttr('density')
        field.CreateFilePathAttr(Sdf.AssetPath('./density.vdb'))
        vol.CreateFieldRelationship('density', field.GetPath())
        stage.GetRootLayer().Save()
        self.before = self.path.read_bytes()
        self.graph = {'targetNodeId':'fog', 'dependencies':[{'id':'src','path':str(self.path)}],
            'nodes':[{'id':'src','operation':'source','inputDependencyId':'src','instanced':False},
                     {'id':'fog','operation':'fog'}],
            'edges':[{'id':'e','source':'src','target':'fog'}]}

    def test_private_binding_relative_texture_source_preservation_and_refresh(self):
        stage, alive, _ = compile_graph(self.graph)
        vol = stage.GetPrimAtPath('/Scene/Asset/Smoke')
        mat, _ = UsdShade.MaterialBindingAPI(vol).ComputeBoundMaterial()
        self.assertTrue(mat)
        shader = UsdShade.Shader(stage.GetPrimAtPath(mat.GetPath().AppendChild('Shader')))
        self.assertEqual(shader.GetInput('volume_density_scale').Get(), 1)
        self.assertEqual(pathlib.Path(shader.GetInput('volume_density_texture').Get().path), self.vdb)
        self.assertFalse(UsdShade.MaterialBindingAPI(stage.GetPrimAtPath('/Scene/Asset/Cube')).ComputeBoundMaterial()[0])
        self.graph['nodes'][1]['fog'] = {'densityScale':3}
        updated, keep, _ = compile_graph(self.graph)
        self.assertEqual(UsdShade.Shader(updated.GetPrimAtPath(shader.GetPath())).GetInput('volume_density_scale').Get(), 3)
        self.assertEqual(shader.GetInput('volume_density_scale').Get(), 1)
        self.assertTrue(stage.GetRootLayer().anonymous)
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(self.vdb.read_bytes(), b'fixture: composition only')

    def test_bypass_proxy_rejection_and_explicit_target_validation(self):
        self.graph['nodes'][0]['instanced'] = True
        with self.assertRaisesRegex(ValueError, 'instance-proxy'):
            compile_graph(self.graph)
        self.graph['nodes'][1]['fog'] = {'enabled':False}
        stage, alive, _ = compile_graph(self.graph)
        self.assertTrue(stage.GetPrimAtPath('/Scene/Asset').IsInstance())
        self.graph['nodes'][0]['instanced'] = False
        self.graph['nodes'][1]['fog'] = {'volumePaths':['/Scene/Asset/Cube']}
        with self.assertRaisesRegex(ValueError, 'existing USD Volume'):
            compile_graph(self.graph)

    def test_multiple_inputs_keep_editable_volume_roots_and_original_instances(self):
        self.graph['dependencies'].append({'id':'vdb','path':str(self.vdb)})
        self.graph['nodes'].append({'id':'vdb','operation':'source','inputDependencyId':'vdb'})
        self.graph['edges'].append({'id':'other','source':'vdb','target':'fog'})
        stage, alive, stats = compile_graph(self.graph)
        volumes = [p for p in Usd.PrimRange.Stage(stage,Usd.TraverseInstanceProxies()) if p.IsA(UsdVol.Volume)]
        self.assertEqual(len(volumes),2)
        self.assertTrue(any(p.IsInstance() for p in volumes))
        for prim in volumes:
            self.assertFalse(prim.IsInstanceProxy())
            self.assertTrue(UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0])
        self.assertEqual(self.path.read_bytes(),self.before)

    def test_renderer_boundary_is_private_and_keeps_volume_transforms_and_visibility(self):
        self.graph['nodes'][1]['fog']={'volumeAutoBounds':False,'volumeSize':[4,6,8],'volumeCenter':[1,2,3]}
        stage, alive, _ = compile_graph(self.graph)
        before = stage.GetRootLayer().ExportToString()
        library = self.path.parent/'mdl'
        (library/'Volume').mkdir(parents=True)
        (library/'Volume'/'OmniVolumeDensity.mdl').write_text('fixture')
        preview, keep = fog.prepare_rtx_preview(stage,library)
        boundary = next(p for p in preview.GetPseudoRoot().GetChildren() if p.GetName().startswith('__FogRTX_'))
        self.assertEqual(list(UsdGeom.Mesh(boundary).GetExtentAttr().Get()[0]),[-1,-1,-1])
        self.assertEqual(list(UsdGeom.Mesh(boundary).GetExtentAttr().Get()[1]),[3,5,7])
        self.assertTrue(preview.GetPrimAtPath('/Scene/Asset/Smoke').IsA(UsdVol.Volume))
        self.assertEqual(fog.source_pick(preview,str(boundary.GetPath())),'/Scene/Asset/Smoke')
        UsdGeom.Imageable(preview.GetPrimAtPath('/Scene/Asset/Smoke')).MakeInvisible()
        fog.sync_preview_visibility(preview)
        self.assertEqual(UsdGeom.Imageable(boundary).ComputeVisibility(),'invisible')
        self.assertEqual(stage.GetRootLayer().ExportToString(),before)

    def test_atmosphere_branch_scope_downstream_precedence_and_disabled_bypass(self):
        graph = copy.deepcopy(self.graph)
        graph['nodes'] += [{'id':'later','operation':'fog','fog':{'intensity':4}}, {'id':'unconnected','operation':'fog','fog':{'intensity':8}}]
        graph['edges'].append({'id':'next','source':'fog','target':'later'})
        self.assertEqual(fog.atmosphere(graph)['intensity'],1)
        graph['targetNodeId']='later'
        self.assertEqual(fog.atmosphere(graph)['intensity'],4)
        graph['nodes'][2]['fog']['enabled']=False
        self.assertEqual(fog.atmosphere(graph)['intensity'],1)
        graph['nodes'][1]['fog']={'atmosphereEnabled':False}
        self.assertFalse(fog.atmosphere(graph)['atmosphereEnabled'])
        self.assertTrue(fog.warnings(graph,'HdStormRendererPlugin'))

    def test_atmosphere_attributes_reset_and_axis(self):
        stage = Usd.Stage.CreateInMemory()
        prim = stage.DefinePrim('/Product','RenderProduct')
        fog.apply_atmosphere(prim, fog.DEFAULTS, 'Z')
        self.assertTrue(prim.GetAttribute('omni:rtx:fog:enabled').Get())
        self.assertTrue(prim.GetAttribute('omni:rtx:fog:zUp:enabled').Get())
        fog.apply_atmosphere(prim,None,'Y')
        self.assertFalse(prim.GetAttribute('omni:rtx:fog:enabled').Get())
        self.assertFalse(prim.GetAttribute('omni:rtx:fog:zUp:enabled').Get())
        for patch in ({'densityScale':-1},{'intensity':float('nan')},{'color':[2,0,0]},{'endDistance':0},{'anisotropy':1},{'volumePaths':['/A.attr']}):
            with self.assertRaises(ValueError): fog.settings(patch)

if __name__ == '__main__': unittest.main()
