"""Composition, transforms and instancing using real upstream USD; no renderer needed."""
import pathlib
import tempfile
import unittest
from usd_worker import compile_graph, dispatch
from pxr import Usd, UsdGeom, UsdShade, Sdf


def graph(path):
    return {'targetNodeId':'out','dependencies':[{'id':'asset','path':str(path)}],
            'nodes':[{'id':'a','operation':'source','inputDependencyId':'asset'},
                     {'id':'b','operation':'source','inputDependencyId':'asset'},
                     {'id':'left','operation':'transform','transform':{'translation':[-3,0,0]}},
                     {'id':'right','operation':'transform','transform':{'translation':[3,0,0]}},
                     {'id':'out','operation':'output'}],
            'edges':[{'id':'al','source':'a','target':'left'},{'id':'br','source':'b','target':'right'},
                     {'id':'lo','source':'left','target':'out'},{'id':'ro','source':'right','target':'out'}]}


def skeletal_fixture(folder):
    from pxr import UsdSkel, Gf
    path=pathlib.Path(folder)/'rig.usda'
    stage=Usd.Stage.CreateNew(str(path))
    root=UsdSkel.Root.Define(stage,'/Rig')
    stage.SetDefaultPrim(root.GetPrim())
    UsdGeom.SetStageUpAxis(stage,'Y'); UsdGeom.SetStageMetersPerUnit(stage,1)
    skeleton=UsdSkel.Skeleton.Define(stage,'/Rig/Skeleton')
    skeleton.CreateJointsAttr(['root'])
    skeleton.CreateBindTransformsAttr([Gf.Matrix4d(1)])
    skeleton.CreateRestTransformsAttr([Gf.Matrix4d(1)])
    mesh=UsdGeom.Mesh.Define(stage,'/Rig/Mesh')
    mesh.CreatePointsAttr([(-1,0,0),(1,0,0),(0,2,0)])
    mesh.CreateFaceVertexCountsAttr([3]); mesh.CreateFaceVertexIndicesAttr([0,1,2])
    mesh.CreateSubdivisionSchemeAttr('none')
    binding=UsdSkel.BindingAPI.Apply(mesh.GetPrim())
    binding.CreateSkeletonRel().SetTargets([skeleton.GetPath()])
    binding.CreateGeomBindTransformAttr(Gf.Matrix4d(1))
    binding.CreateJointIndicesPrimvar(False,1).Set([0,0,0])
    binding.CreateJointWeightsPrimvar(False,1).Set([1,1,1])
    animation=UsdSkel.Animation.Define(stage,'/Rig/Animation')
    animation.CreateJointsAttr(['root'])
    animation.CreateTranslationsAttr().Set([(0,0,0)],0)
    animation.GetTranslationsAttr().Set([(1,0,0)],1)
    animation.CreateRotationsAttr([Gf.Quatf(1)])
    animation.CreateScalesAttr([Gf.Vec3h(1)])
    UsdSkel.BindingAPI.Apply(skeleton.GetPrim()).CreateAnimationSourceRel().SetTargets([animation.GetPath()])
    stage.GetRootLayer().Save()
    return path


class GraphTests(unittest.TestCase):
    def fixture(self, folder):
        path=pathlib.Path(folder)/'asset.usda'
        source=Usd.Stage.CreateNew(str(path))
        UsdGeom.SetStageMetersPerUnit(source,1)
        UsdGeom.SetStageUpAxis(source,UsdGeom.Tokens.y)
        root=UsdGeom.Xform.Define(source,'/Asset')
        source.SetDefaultPrim(root.GetPrim())
        UsdGeom.Cube.Define(source,'/Asset/Cube').CreateSizeAttr(2)
        source.GetRootLayer().Save()
        return path,source

    def test_multiple_branches_share_geometry_and_original_remains_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            path,source=self.fixture(folder)
            before=path.read_bytes()
            stage,keepalive,stats=compile_graph(graph(path))
            bounds=UsdGeom.BBoxCache(0,['default']).ComputeWorldBound(stage.GetPseudoRoot()).ComputeAlignedRange()
            self.assertAlmostEqual(bounds.GetMin()[0],-4)
            self.assertAlmostEqual(bounds.GetMax()[0],4)
            self.assertEqual(stats['uniqueSourceAssets'],1)
            self.assertGreater(stats['instances'],0)
            self.assertGreater(stats['prototypes'],0)
            geometry=sum(1 for prototype in stage.GetPrototypes() for p in Usd.PrimRange(prototype) if p.IsA(UsdGeom.Cube))
            self.assertEqual(geometry,1,'repeated branches must share one cube prototype')
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual([p.GetName() for p in source.GetPseudoRoot().GetChildren()],['Asset'])

    def test_asset_override_branch_isolation_chaining_and_sharing(self):
        with tempfile.TemporaryDirectory() as folder:
            path, source = self.fixture(folder)
            original = str(pathlib.Path(folder)/'missing.png')
            replacement = str(pathlib.Path(folder)/'replacement.png')
            final = str(pathlib.Path(folder)/'final.png')
            shader = UsdShade.Shader.Define(source, '/Asset/Texture')
            shader.CreateInput('file', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(original))
            source.GetRootLayer().Save()
            before = path.read_bytes()
            spec = graph(path)
            spec['dependencies'] += [{'id':'texture','path':replacement}, {'id':'final','path':final}]
            for node in spec['nodes'][2:4]:
                node['operation'] = 'assetOverride'
                node['assetOverrides'] = [{'originalPath':original, 'replacementDependencyId':'texture'}]
            stage, alive, _ = compile_graph(spec)
            def paths(stage):
                return [str(p.GetAttribute('inputs:file').Get().path) for p in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()) if p.GetAttribute('inputs:file')]
            self.assertEqual(paths(stage), [replacement, replacement])
            geometry = sum(1 for prototype in stage.GetPrototypes() for p in Usd.PrimRange(prototype) if p.IsA(UsdGeom.Cube))
            self.assertEqual(geometry, 1)
            spec['nodes'][3]['assetOverrides'] = []
            stage, alive, _ = compile_graph(spec)
            self.assertCountEqual(paths(stage), [replacement, original])
            spec['nodes'].append({'id':'chain','operation':'assetOverride','assetOverrides':[{'originalPath':original,'replacementDependencyId':'final'}]})
            spec['edges'].append({'id':'chain-edge','source':'left','target':'chain'})
            spec['targetNodeId'] = 'chain'
            stage, alive, _ = compile_graph(spec)
            self.assertEqual(paths(stage), [final])
            self.assertEqual(path.read_bytes(), before)

    def test_override_asset_arrays_samples_and_distinct_same_names(self):
        with tempfile.TemporaryDirectory() as folder:
            path, source = self.fixture(folder)
            first = str(pathlib.Path(folder)/'a'/'texture.png')
            second = str(pathlib.Path(folder)/'b'/'texture.png')
            replacement = str(pathlib.Path(folder)/'replacement.png')
            prim = source.GetPrimAtPath('/Asset')
            prim.CreateAttribute('assets', Sdf.ValueTypeNames.AssetArray).Set([Sdf.AssetPath(first), Sdf.AssetPath(second)])
            prim.CreateAttribute('optionalTexture', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath())
            prim.CreateAttribute('animated', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(first), 10)
            source.GetRootLayer().Save()
            spec = graph(path)
            spec['dependencies'].append({'id':'replacement','path':replacement})
            spec['targetNodeId'] = 'left'
            spec['nodes'][2].update(operation='assetOverride',assetOverrides=[{'originalPath':first,'replacementDependencyId':'replacement'}])
            stage, alive, _ = compile_graph(spec)
            prims = [p for p in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()) if p.GetAttribute('assets')]
            self.assertEqual(len(prims), 1)
            self.assertEqual([pathlib.Path(a.path) for a in prims[0].GetAttribute('assets').Get()], [pathlib.Path(replacement),pathlib.Path(second)])
            self.assertEqual(prims[0].GetAttribute('animated').Get(10).path, replacement)
            self.assertEqual(prims[0].GetAttribute('optionalTexture').Get().path, '')

    def test_application_conversion_units_axis_scale_inverse_and_input_revision(self):
        from pxr import Gf
        with tempfile.TemporaryDirectory() as folder:
            path, source = self.fixture(folder)
            before = path.read_bytes()
            spec = graph(path)
            first_revision = None
            for axis in ['Y', 'Z']:
                for unit in [1, 0.01, 0.001]:
                    for scale in [1, 2.5]:
                        result = dispatch({'operation':'coordinate_preflight','graph':spec,'app':'Blender','upAxis':axis,'metersPerUnit':unit,'scale':scale})
                        draft = result['draft']
                        self.assertEqual(draft['factor'], scale/unit)
                        forward, inverse = Gf.Matrix4d(*[v for row in draft['forward'] for v in row]), Gf.Matrix4d(*[v for row in draft['inverse'] for v in row])
                        point = Gf.Vec3d(1, 2, 3)
                        restored = inverse.Transform(forward.Transform(point))
                        for a,b in zip(point,restored): self.assertAlmostEqual(a,b)
                        up = forward.TransformDir(Gf.Vec3d(0,1,0))
                        self.assertAlmostEqual(up[1 if axis == 'Y' else 2],scale/unit)
                        self.assertAlmostEqual(result['handoffDimensions'][0],result['inputDimensions'][0]*scale/unit)
                        self.assertEqual(result['graphStats']['uniqueSourceAssets'],1)
                        first_revision = draft['inputRevision']
            self.assertEqual(path.read_bytes(), before)
            UsdGeom.Cube(source.GetPrimAtPath('/Asset/Cube')).GetSizeAttr().Set(5)
            source.GetRootLayer().Save()
            result = dispatch({'operation':'coordinate_preflight','graph':spec,'app':'Blender','upAxis':'Z','metersPerUnit':1})
            self.assertNotEqual(result['draft']['inputRevision'],first_revision)
            for value in [0, -1, float('inf'), True]:
                with self.assertRaises(ValueError): dispatch({'operation':'coordinate_preflight','graph':spec,'upAxis':'Z','metersPerUnit':value})

    def test_handoff_export_reopens_without_anonymous_dependencies_and_preserves_instances(self):
        from usd_graph import export_handoff
        with tempfile.TemporaryDirectory() as folder:
            path, source = self.fixture(folder)
            before = path.read_bytes()
            destination = pathlib.Path(folder)/'handoff.usda'
            result = export_handoff({'graph':graph(path),'app':'Blender','upAxis':'Z','metersPerUnit':0.01,'scale':2,'destination':str(destination)})
            stage = Usd.Stage.Open(str(destination))
            self.assertEqual(UsdGeom.GetStageUpAxis(stage), 'Z')
            self.assertEqual(UsdGeom.GetStageMetersPerUnit(stage),0.01)
            self.assertEqual(stage.GetDefaultPrim().GetName(),'Handoff')
            self.assertTrue(result['layerPaths'])
            for layer in stage.GetUsedLayers():
                if layer != stage.GetSessionLayer(): self.assertFalse(layer.anonymous)
            self.assertNotIn('anon:',destination.read_text())
            geometry=sum(1 for prototype in stage.GetPrototypes() for p in Usd.PrimRange(prototype) if p.IsA(UsdGeom.Cube))
            self.assertEqual(geometry,1)
            extent=UsdGeom.BBoxCache(0,['default']).ComputeWorldBound(stage.GetPseudoRoot()).ComputeAlignedRange()
            self.assertAlmostEqual(extent.GetSize()[0],1600)
            self.assertEqual(path.read_bytes(),before)
            request={'graph':graph(path),'app':'Blender','upAxis':'Z','metersPerUnit':1,'destination':str(destination)}
            output_before=destination.read_bytes()
            with self.assertRaisesRegex(ValueError,'already exists'): export_handoff(request)
            self.assertEqual(destination.read_bytes(),output_before)
            request['overwrite']=True
            replacement=export_handoff(request)
            self.assertNotEqual(destination.read_bytes(),output_before)
            self.assertTrue(all(pathlib.Path(p).exists() for p in result['layerPaths']))
            self.assertTrue(Usd.Stage.Open(str(destination)))
            request['overwrite']=False
            request['destination']=str(path)
            with self.assertRaisesRegex(ValueError,'already exists'): export_handoff(request)
            self.assertEqual(path.read_bytes(),before)

    def test_handoff_export_persists_branch_asset_overrides(self):
        from usd_graph import export_handoff
        with tempfile.TemporaryDirectory() as folder:
            path,source=self.fixture(folder)
            original=str(pathlib.Path(folder)/'missing.png')
            replacement=str(pathlib.Path(folder)/'replacement.png')
            UsdShade.Shader.Define(source,'/Asset/Shader').CreateInput('file',Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(original))
            source.GetRootLayer().Save()
            before=path.read_bytes()
            spec=graph(path)
            spec['dependencies'].append({'id':'texture','path':replacement})
            spec['nodes'][2].update(operation='assetOverride',assetOverrides=[{'originalPath':original,'replacementDependencyId':'texture'}])
            destination=pathlib.Path(folder)/'override.usda'
            export_handoff({'graph':spec,'app':'Blender','upAxis':'Y','metersPerUnit':1,'destination':str(destination)})
            stage=Usd.Stage.Open(str(destination))
            assets=[p.GetAttribute('inputs:file').Get().path for p in Usd.PrimRange.Stage(stage,Usd.TraverseInstanceProxies()) if p.GetAttribute('inputs:file')]
            self.assertCountEqual([pathlib.Path(a) for a in assets],[pathlib.Path(original),pathlib.Path(replacement)])
            self.assertEqual(path.read_bytes(),before)

    def test_skeletal_branches_keep_reference_sharing_without_forced_prototypes(self):
        from pxr import UsdSkel
        from usd_graph import export_handoff
        with tempfile.TemporaryDirectory() as folder:
            path=skeletal_fixture(folder)
            before=path.read_bytes()
            stage,alive,stats=compile_graph(graph(path))
            skeletons=[p for p in stage.Traverse() if p.IsA(UsdSkel.Skeleton)]
            self.assertEqual(len(skeletons),2)
            self.assertTrue(all(not p.IsInPrototype() for p in skeletons))
            self.assertEqual(stats['uniqueSourceAssets'],1)
            for root in [p for p in stage.Traverse() if p.IsA(UsdSkel.Root)]:
                cache=UsdSkel.Cache();cache.Populate(UsdSkel.Root(root),Usd.PrimDefaultPredicate)
                skel=next(p for p in Usd.PrimRange(root) if p.IsA(UsdSkel.Skeleton))
                query=cache.GetSkelQuery(UsdSkel.Skeleton(skel))
                self.assertAlmostEqual(query.ComputeSkinningTransforms(1)[0].ExtractTranslation()[0],1)
            destination=pathlib.Path(folder)/'handoff.usda'
            export_handoff({'graph':graph(path),'app':'Blender','upAxis':'Z','metersPerUnit':1,'destination':str(destination)})
            reopened=Usd.Stage.Open(str(destination))
            self.assertEqual(sum(p.IsA(UsdSkel.Skeleton) for p in reopened.Traverse()),2)
            self.assertEqual(path.read_bytes(),before)

    def test_handoff_preserves_time_range_and_seconds_across_source_rates(self):
        from usd_graph import export_handoff
        with tempfile.TemporaryDirectory() as folder:
            path,stage=self.fixture(folder)
            stage.SetTimeCodesPerSecond(30);stage.SetFramesPerSecond(30);stage.SetStartTimeCode(30);stage.SetEndTimeCode(90)
            cube=UsdGeom.Cube(stage.GetPrimAtPath('/Asset/Cube'));cube.GetSizeAttr().Set(1,30);cube.GetSizeAttr().Set(3,90);stage.GetRootLayer().Save()
            output=pathlib.Path(folder)/'handoff.usda'
            spec=graph(path);spec['targetNodeId']='left'
            export_handoff({'graph':spec,'app':'Blender','upAxis':'Z','metersPerUnit':1,'destination':str(output)})
            handoff=Usd.Stage.Open(str(output))
            self.assertEqual((handoff.GetStartTimeCode(),handoff.GetEndTimeCode(),handoff.GetTimeCodesPerSecond()),(24,72,24))
            geometry=next(p for p in handoff.Traverse(Usd.TraverseInstanceProxies()) if p.IsA(UsdGeom.Cube))
            self.assertEqual(geometry.GetAttribute('size').Get(24),1);self.assertEqual(geometry.GetAttribute('size').Get(72),3)

    def test_rotation_scale_translation_are_usd_xform_ops(self):
        with tempfile.TemporaryDirectory() as folder:
            path,_=self.fixture(folder)
            spec=graph(path);spec['targetNodeId']='left'
            spec['nodes'][2]['transform']={'translation':[5,0,0],'rotation':[0,0,90],'scale':[2,1,1]}
            stage,keepalive,_=compile_graph(spec)
            bounds=UsdGeom.BBoxCache(0,['default']).ComputeWorldBound(stage.GetPseudoRoot()).ComputeAlignedRange()
            self.assertAlmostEqual(bounds.GetMin()[0],4)
            self.assertAlmostEqual(bounds.GetMax()[0],6)
            self.assertAlmostEqual(bounds.GetMin()[1],-2)
            self.assertAlmostEqual(bounds.GetMax()[1],2)
            self.assertEqual(list(UsdGeom.Xformable(stage.GetDefaultPrim()).GetXformOpOrderAttr().Get()),['xformOp:translate','xformOp:rotateXYZ','xformOp:scale'])

    def test_large_mesh_is_shared_across_branches_not_authored_twice(self):
        with tempfile.TemporaryDirectory() as folder:
            path,source=self.fixture(folder)
            mesh=UsdGeom.Mesh.Define(source,'/Asset/LargeMesh')
            mesh.CreatePointsAttr([(float(i%100)/1000,float(i//100)/1000,0) for i in range(20000)])
            mesh.CreateFaceVertexCountsAttr([3]*19998)
            mesh.CreateFaceVertexIndicesAttr([index for i in range(19998) for index in (i,i+1,i+2)])
            source.GetRootLayer().Save()
            stage,keepalive,stats=compile_graph(graph(path))
            meshes=[UsdGeom.Mesh(p) for prototype in stage.GetPrototypes() for p in Usd.PrimRange(prototype) if p.IsA(UsdGeom.Mesh)]
            self.assertEqual(len(meshes),1)
            self.assertEqual(len(meshes[0].GetPointsAttr().Get()),20000)
            self.assertEqual(stats['uniqueSourceAssets'],1)

    def test_different_units_and_up_axis_are_composed_in_meters_y_up(self):
        with tempfile.TemporaryDirectory() as folder:
            path,source=self.fixture(folder)
            UsdGeom.SetStageMetersPerUnit(source,0.01)
            UsdGeom.SetStageUpAxis(source,UsdGeom.Tokens.z)
            UsdGeom.XformCommonAPI(source.GetDefaultPrim()).SetTranslate((0,0,100))
            source.GetRootLayer().Save()
            spec=graph(path);spec['targetNodeId']='a'
            stage,keepalive,_=compile_graph(spec)
            bounds=UsdGeom.BBoxCache(0,['default']).ComputeWorldBound(stage.GetPseudoRoot()).ComputeAlignedRange()
            self.assertAlmostEqual(bounds.GetMidpoint()[1],1)
            self.assertAlmostEqual(UsdGeom.GetStageMetersPerUnit(stage),1)
            self.assertEqual(UsdGeom.GetStageUpAxis(stage),'Y')

    def test_multi_root_material_bindings_survive_namespacing(self):
        with tempfile.TemporaryDirectory() as folder:
            path=pathlib.Path(folder)/'multiple.usda'
            source=Usd.Stage.CreateNew(str(path));UsdGeom.SetStageMetersPerUnit(source,1)
            cube=UsdGeom.Cube.Define(source,'/Geometry/Cube')
            material=UsdShade.Material.Define(source,'/Looks/Material')
            shader=UsdShade.Shader.Define(source,'/Looks/Material/Shader');shader.CreateIdAttr('UsdPreviewSurface')
            material.CreateSurfaceOutput().ConnectToSource(shader.CreateOutput('surface',Sdf.ValueTypeNames.Token))
            UsdShade.MaterialBindingAPI.Apply(cube.GetPrim()).Bind(material)
            source.GetRootLayer().Save();before=path.read_bytes()
            stage,keepalive,_=compile_graph(graph(path))
            cubes=[p for p in stage.Traverse(Usd.TraverseInstanceProxies()) if p.IsA(UsdGeom.Cube)]
            self.assertEqual(len(cubes),2)
            for p in cubes:
                bound,_=UsdShade.MaterialBindingAPI(p).ComputeBoundMaterial()
                self.assertTrue(bound,'sibling material references must remain bound')
            self.assertEqual(path.read_bytes(),before)

    def test_reject_cycle_dangling_edges_empty_inputs_and_invalid_transform(self):
        with tempfile.TemporaryDirectory() as folder:
            path,_=self.fixture(folder)
            spec=graph(path);spec['edges'].append({'id':'cycle','source':'out','target':'a'})
            with self.assertRaisesRegex(ValueError,'cycle'):compile_graph(spec)
            spec=graph(path);spec['edges'][0]['source']='missing'
            with self.assertRaisesRegex(ValueError,'Dangling'):compile_graph(spec)
            spec=graph(path);spec['edges']=[]
            with self.assertRaisesRegex(ValueError,'needs'):compile_graph(spec)
            spec=graph(path);spec['nodes'][2]['transform']={'scale':[1,float('nan'),1]}
            with self.assertRaisesRegex(ValueError,'Invalid'):compile_graph(spec)

class DeleteNodeTests(unittest.TestCase):
    def test_delete_is_branch_local_and_does_not_change_source(self):
        with tempfile.TemporaryDirectory() as folder:
            path=pathlib.Path(folder)/'source.usda'
            source=Usd.Stage.CreateNew(str(path));root=UsdGeom.Xform.Define(source,'/World');source.SetDefaultPrim(root.GetPrim());UsdGeom.Cube.Define(source,'/World/A');UsdGeom.Cube.Define(source,'/World/B');source.GetRootLayer().Save();before=path.read_bytes()
            from usd_graph import apply_delete
            layer=Sdf.Layer.CreateAnonymous();layer.subLayerPaths=[str(path)];stage=Usd.Stage.Open(layer)
            apply_delete(stage,['/World/A']);self.assertFalse(stage.GetPrimAtPath('/World/A').IsActive());self.assertTrue(stage.GetPrimAtPath('/World/B').IsActive());self.assertTrue(source.GetPrimAtPath('/World/A').IsActive());self.assertEqual(path.read_bytes(),before)
            with self.assertRaises(ValueError):apply_delete(stage,['/World/Missing'])
            with self.assertRaises(ValueError):apply_delete(stage,['/'])
