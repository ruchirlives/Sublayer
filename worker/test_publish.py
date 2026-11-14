import pathlib,tempfile,unittest,hashlib,os
import usd_worker
from pxr import Usd,UsdGeom,UsdShade,Sdf,Gf
from usd_publish import review_publication,publish_reviewed,file_digest

class PublishTests(unittest.TestCase):
 def fixture(self,folder,scope):
  root=pathlib.Path(folder);source=root/'handoff.usda'
  s=Usd.Stage.CreateNew(str(source));x=UsdGeom.Xform.Define(s,'/Handoff');s.SetDefaultPrim(x.GetPrim());x.GetPrim().CreateAttribute('userProperties:orchflowRoundTripId',Sdf.ValueTypeNames.String).Set('test-round-trip');x.AddScaleOp().Set((2,2,2));UsdGeom.SetStageUpAxis(s,'Y');UsdGeom.SetStageMetersPerUnit(s,1)
  m=UsdGeom.Mesh.Define(s,'/Handoff/Mesh');m.CreatePointsAttr([(0,0,0),(1,0,0),(0,1,0)]);m.CreateFaceVertexCountsAttr([3]);m.CreateFaceVertexIndicesAttr([0,1,2]);m.CreateSubdivisionSchemeAttr('none')
  m.GetPrim().CreateAttribute('unsupported:detail',Sdf.ValueTypeNames.String).Set('preserve me')
  UsdGeom.Camera.Define(s,'/Handoff/UnsupportedCamera');s.GetRootLayer().Save()
  baseline=root/'baseline.usda';s.GetRootLayer().Export(str(baseline));b=Usd.Stage.Open(str(baseline));b.RemovePrim('/Handoff/UnsupportedCamera');b.GetPrimAtPath('/Handoff/Mesh').RemoveProperty('unsupported:detail');b.GetRootLayer().Save()
  edited=root/'edited.usda';b.GetRootLayer().Export(str(edited));e=Usd.Stage.Open(str(edited))
  meta=os.stat(source)
  request={'handoff':str(source),'baseline':str(baseline),'edited':str(edited),'baselineDigest':file_digest(baseline),'sourceRevisions':[{'path':str(source),'size':meta.st_size,'modifiedNs':str(meta.st_mtime_ns)}], 'policy':{'editScope':scope,'profile':{'capabilities':{key:{'status':'supported'} for key in scope}}},'currentGraph':{},'conversion':{'graphRevision':hashlib.sha256(b'{}').hexdigest(),'inverse':[[.5,0,0,0],[0,.5,0,0],[0,0,.5,0],[0,0,0,1]]},'correspondence':[],'confirmedDeletions':[]}
  return request,s,b,e
 def test_selective_points_preserve_dropped_input_and_normalize_output(self):
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,['points']);before=pathlib.Path(r['handoff']).read_bytes()
   e.GetPrimAtPath('/Handoff/Mesh').GetAttribute('points').Set([(0,0,0),(2,0,0),(0,1,0)]);e.GetRootLayer().Save()
   review=review_publication(r);self.assertEqual(review['report']['conflicts'],[])
   r.update(reviewToken=review['token'],destination=str(pathlib.Path(folder)/'published.usda'))
   result=publish_reviewed(r);stage=Usd.Stage.Open(result['path']);p=stage.GetPrimAtPath('/Scene/PreservedInput/Mesh')
   self.assertEqual(p.GetAttribute('unsupported:detail').Get(),'preserve me');self.assertTrue(stage.GetPrimAtPath('/Scene/PreservedInput/UnsupportedCamera'))
   self.assertEqual(p.GetAttribute('points').Get()[1][0],2)
   self.assertEqual(UsdGeom.GetStageUpAxis(stage),'Y');self.assertEqual(UsdGeom.GetStageMetersPerUnit(stage),1)
   self.assertAlmostEqual(UsdGeom.XformCache().GetLocalToWorldTransform(p).ExtractRotationMatrix()[0][0],1)
   self.assertEqual(pathlib.Path(r['handoff']).read_bytes(),before)
   with self.assertRaisesRegex(ValueError,'exists'):publish_reviewed(r)
 def test_out_of_scope_and_unrepresented_deletions_are_preserved(self):
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,[]);e.RemovePrim('/Handoff/Mesh');e.GetRootLayer().Save()
   result=review_publication(r);self.assertFalse(result['report']['applied']);self.assertFalse(result['report']['conflicts']);self.assertTrue(result['report']['preserved'])
 def test_application_deletion_is_preserved_even_with_old_scope_and_confirmation(self):
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,['deletions']);e.RemovePrim('/Handoff/Mesh');e.GetRootLayer().Save()
   result=review_publication(r);self.assertFalse(result['report']['conflicts'])
   r['confirmedDeletions']=['/Handoff/Mesh'];result=review_publication(r);self.assertFalse(result['report']['conflicts']);self.assertFalse(result['report']['applied']);self.assertTrue(result['report']['preserved'])
 def test_changed_upstream_graph_requires_reconciliation(self):
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,['points']);r['currentGraph']={'changedInput':'new.usda'}
   with self.assertRaisesRegex(ValueError,'Upstream graph changed'):review_publication(r)
 def test_revision_and_stale_review_rejection(self):
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,['points']);result=review_publication(r)
   r.update(reviewToken=result['token'],destination=str(pathlib.Path(folder)/'pub.usda'))
   e.GetPrimAtPath('/Handoff/Mesh').GetAttribute('points').Set([(0,0,0),(3,0,0),(0,1,0)]);e.GetRootLayer().Save()
   with self.assertRaisesRegex(ValueError,'stale'):publish_reviewed(r)
   s.GetPrimAtPath('/Handoff/Mesh').GetAttribute('unsupported:detail').Set('changed');s.GetRootLayer().Save()
   with self.assertRaisesRegex(ValueError,'changed'):review_publication(r)
 def test_topology_conflicts_with_preserved_uvs(self):
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,['points','topology'])
   # Add a UV dependency to the preserved input and refresh its recorded revision.
   UsdGeom.PrimvarsAPI(s.GetPrimAtPath('/Handoff/Mesh')).CreatePrimvar('st',Sdf.ValueTypeNames.TexCoord2fArray,UsdGeom.Tokens.faceVarying).Set([(0,0),(1,0),(0,1)]);s.GetRootLayer().Save();meta=os.stat(r['handoff']);r['sourceRevisions'][0].update(size=meta.st_size,modifiedNs=str(meta.st_mtime_ns))
   e.GetPrimAtPath('/Handoff/Mesh').GetAttribute('faceVertexIndices').Set([0,2,1]);e.GetRootLayer().Save()
   self.assertTrue(review_publication(r)['report']['conflicts'])

 def test_transform_delta_is_applied_once_and_out_of_scope_points_stay_original(self):
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,['transforms'])
   UsdGeom.Xformable(e.GetPrimAtPath('/Handoff/Mesh')).AddTranslateOp().Set((3,0,0))
   e.GetPrimAtPath('/Handoff/Mesh').GetAttribute('points').Set([(0,0,0),(5,0,0),(0,1,0)]);e.GetRootLayer().Save()
   result=review_publication(r);self.assertFalse(result['report']['conflicts'])
   r.update(reviewToken=result['token'],destination=str(pathlib.Path(folder)/'pub.usda'));published=publish_reviewed(r)
   stage=Usd.Stage.Open(published['path']);p=stage.GetPrimAtPath('/Scene/PreservedInput/Mesh')
   self.assertAlmostEqual(UsdGeom.XformCache().GetLocalToWorldTransform(p).ExtractTranslation()[0],3)
   self.assertEqual(p.GetAttribute('points').Get()[1][0],1)
 def test_material_addition_rebinds_without_losing_original_networks(self):
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,['materials'])
   material=UsdShade.Material.Define(e,'/Handoff/NewMaterial');shader=UsdShade.Shader.Define(e,'/Handoff/NewMaterial/Surface');shader.CreateIdAttr('UsdPreviewSurface');shader.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set((1,0,0));material.CreateSurfaceOutput().ConnectToSource(shader.CreateOutput('surface',Sdf.ValueTypeNames.Token))
   UsdShade.MaterialBindingAPI.Apply(e.GetPrimAtPath('/Handoff/Mesh')).Bind(material);e.GetRootLayer().Save()
   result=review_publication(r);self.assertFalse(result['report']['conflicts'],result['report']['conflicts'])
   r.update(reviewToken=result['token'],destination=str(pathlib.Path(folder)/'pub.usda'));published=publish_reviewed(r)
   stage=Usd.Stage.Open(published['path']);bound=UsdShade.MaterialBindingAPI(stage.GetPrimAtPath('/Scene/PreservedInput/Mesh')).ComputeBoundMaterial()[0]
   self.assertTrue(bound);self.assertIn('_OrchFlowMaterials',str(bound.GetPath()))
 def test_renamed_mesh_requires_explicit_correspondence(self):
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,['points'])
   editor=Usd.NamespaceEditor(e);editor.MovePrimAtPath('/Handoff/Mesh','/Handoff/Renamed');self.assertTrue(editor.ApplyEdits())
   e.GetPrimAtPath('/Handoff/Renamed').GetAttribute('points').Set([(0,0,0),(2,0,0),(0,1,0)]);e.GetRootLayer().Save()
   self.assertTrue(review_publication(r)['report']['conflicts'])
   r['correspondence']=[{'baselinePath':'/Handoff/Mesh','inputPath':'/Handoff/Mesh','editedPath':'/Handoff/Renamed'}]
   result=review_publication(r);self.assertFalse(result['report']['conflicts'],result['report']['conflicts'])
 def test_animation_requires_both_property_scope_and_animation_permission(self):
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,['transforms'])
   op=UsdGeom.Xformable(e.GetPrimAtPath('/Handoff/Mesh')).AddTranslateOp();op.Set((0,0,0),1);op.Set((4,0,0),2);e.GetRootLayer().Save()
   self.assertTrue(review_publication(r)['report']['conflicts'])
   r['policy']['editScope'].append('animation');r['policy']['profile']['capabilities']['animation']={'status':'supported'}
   self.assertFalse(review_publication(r)['report']['conflicts'])

 def test_identical_edited_branches_reuse_one_geometry_prototype(self):
  from test_graph import GraphTests,graph
  from usd_graph import export_handoff
  with tempfile.TemporaryDirectory() as folder:
   path,asset=GraphTests().fixture(folder)
   mesh=UsdGeom.Mesh.Define(asset,'/Asset/Large');mesh.CreatePointsAttr([(i/1000.,0,0) for i in range(20000)]);mesh.CreateFaceVertexCountsAttr([]);mesh.CreateFaceVertexIndicesAttr([]);asset.GetRootLayer().Save()
   handoff=pathlib.Path(folder)/'handoff.usda';prepared=export_handoff({'graph':graph(path),'app':'Blender','upAxis':'Y','metersPerUnit':1,'destination':str(handoff)})
   source=Usd.Stage.Open(str(handoff));baseline=pathlib.Path(folder)/'baseline.usda';source.GetRootLayer().Export(str(baseline));edited=pathlib.Path(folder)/'edited.usda';source.GetRootLayer().Export(str(edited));stage=Usd.Stage.Open(str(edited))
   paths=[str(p.GetPath()) for p in stage.Traverse(Usd.TraverseInstanceProxies()) if p.IsA(UsdGeom.Mesh)]
   self.assertEqual(len(paths),2)
   for path in paths:
    prim=stage.GetPrimAtPath(path)
    while prim.IsInstanceProxy():
     parent=prim.GetParent()
     while parent and (not parent.IsInstance() or parent.IsInstanceProxy()):parent=parent.GetParent()
     stage.OverridePrim(parent.GetPath()).SetInstanceable(False);prim=stage.GetPrimAtPath(path)
    values=list(prim.GetAttribute('points').Get());values[0]=Gf.Vec3f(1,1,1);prim.GetAttribute('points').Set(values)
   stage.GetRootLayer().Save();records=[]
   for layer in source.GetUsedLayers():
    if layer.realPath:
     meta=os.stat(layer.realPath);records.append({'path':layer.realPath,'size':meta.st_size,'modifiedNs':str(meta.st_mtime_ns)})
   request={'handoff':str(handoff),'baseline':str(baseline),'edited':str(edited),'baselineDigest':file_digest(baseline),'sourceRevisions':records,'policy':{'editScope':['points'],'profile':{'capabilities':{'points':{'status':'supported'}}}},'currentGraph':{},'conversion':{'graphRevision':hashlib.sha256(b'{}').hexdigest(),'inverse':[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]},'correspondence':[]}
   reviewed=review_publication(request);self.assertFalse(reviewed['report']['conflicts'],reviewed['report']['conflicts']);self.assertGreater(reviewed['report']['sharedEditPrototypes'],0)
   request.update(reviewToken=reviewed['token'],destination=str(pathlib.Path(folder)/'published.usda'));result=publish_reviewed(request);published=Usd.Stage.Open(result['path'])
   meshes=[p for prototype in published.GetPrototypes() for p in Usd.PrimRange(prototype) if p.IsA(UsdGeom.Mesh)]
   self.assertEqual(len(meshes),1)
   self.assertEqual(len(meshes[0].GetAttribute('points').Get()),20000)
   from usd_publish import reconcile_upstream
   replacement=pathlib.Path(folder)/'replacement.usda';asset.GetRootLayer().Export(str(replacement));updated=Usd.Stage.Open(str(replacement));updated.GetPrimAtPath('/Asset/Large').CreateAttribute('upstream:tag',Sdf.ValueTypeNames.String).Set('updated');updated.GetRootLayer().Save()
   changed=graph(replacement)
   reconciled=reconcile_upstream({'adjustmentLayer':result['editLayerPath'],'publicationDependencyId':'old','currentGraph':changed,'conversion':prepared['draft'],'policy':request['policy'],'report':result['report'],'correspondence':[],'baselineDependencyId':'baseline','handoffDependencyId':'handoff','destination':str(pathlib.Path(folder)/'reconciled.usda')})
   rebased=Usd.Stage.Open(reconciled['path']);meshes=[p for prototype in rebased.GetPrototypes() for p in Usd.PrimRange(prototype) if p.IsA(UsdGeom.Mesh)]
   self.assertEqual(len(meshes),1);self.assertEqual(meshes[0].GetAttribute('upstream:tag').Get(),'updated')
   self.assertEqual(meshes[0].GetAttribute('points').Get()[0],Gf.Vec3f(1,1,1))


 def test_uv_values_and_interpolation_are_applied_and_invalid_cardinality_is_blocked(self):
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,['uvs'])
   pv=UsdGeom.PrimvarsAPI(e.GetPrimAtPath('/Handoff/Mesh')).CreatePrimvar('st',Sdf.ValueTypeNames.TexCoord2fArray,UsdGeom.Tokens.faceVarying);pv.Set([(0,0),(0.5,0),(0,1)]);e.GetRootLayer().Save()
   result=review_publication(r);self.assertFalse(result['report']['conflicts'],result['report']['conflicts'])
   pv.Set([(0,0),(1,0)]);e.GetRootLayer().Save()
   self.assertTrue(review_publication(r)['report']['conflicts'])
 def test_skeleton_rest_edits_preserve_skinning_and_invalid_weights_are_blocked(self):
  from pxr import UsdSkel
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,['skeletons'])
   for stage in [s,b,e]:
    stage.GetPrimAtPath('/Handoff').SetTypeName('SkelRoot')
    skeleton=UsdSkel.Skeleton.Define(stage,'/Handoff/Skeleton');skeleton.CreateJointsAttr(['root']);skeleton.CreateBindTransformsAttr([Gf.Matrix4d(1)]);skeleton.CreateRestTransformsAttr([Gf.Matrix4d(1)])
    binding=UsdSkel.BindingAPI.Apply(stage.GetPrimAtPath('/Handoff/Mesh'));binding.CreateSkeletonRel().SetTargets([skeleton.GetPath()]);binding.CreateGeomBindTransformAttr(Gf.Matrix4d(1));binding.CreateJointIndicesPrimvar(False,1).Set([0,0,0]);binding.CreateJointWeightsPrimvar(False,1).Set([1,1,1]);stage.GetRootLayer().Save()
   meta=os.stat(r['handoff']);r['sourceRevisions'][0].update(size=meta.st_size,modifiedNs=str(meta.st_mtime_ns));r['baselineDigest']=file_digest(r['baseline'])
   e.GetPrimAtPath('/Handoff/Skeleton').GetAttribute('restTransforms').Set([Gf.Matrix4d().SetTranslate((1,0,0))]);e.GetRootLayer().Save()
   result=review_publication(r);self.assertFalse(result['report']['conflicts'],result['report']['conflicts'])
   UsdSkel.BindingAPI(e.GetPrimAtPath('/Handoff/Mesh')).GetJointWeightsPrimvar().Set([1,1]);e.GetRootLayer().Save();self.assertTrue(review_publication(r)['report']['conflicts'])

 def test_new_face_material_assignment_preserves_other_networks_and_reconciles_subsets(self):
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,['materials'])
   old=UsdShade.Material.Define(s,'/Handoff/OriginalMaterial');subset=UsdGeom.Subset.Define(s,'/Handoff/Mesh/OriginalFaces');subset.CreateElementTypeAttr('face');subset.CreateFamilyNameAttr('materialBind');subset.CreateIndicesAttr([0]);UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(old);s.GetRootLayer().Save();meta=os.stat(r['handoff']);r['sourceRevisions'][0].update(size=meta.st_size,modifiedNs=str(meta.st_mtime_ns))
   new=UsdShade.Material.Define(e,'/Handoff/NewMaterial');assigned=UsdGeom.Subset.Define(e,'/Handoff/Mesh/NewFaces');assigned.CreateElementTypeAttr('face');assigned.CreateFamilyNameAttr('materialBind');assigned.CreateIndicesAttr([0]);UsdShade.MaterialBindingAPI.Apply(assigned.GetPrim()).Bind(new);e.GetRootLayer().Save()
   result=review_publication(r);self.assertFalse(result['report']['conflicts'],result['report']['conflicts'])
   r.update(reviewToken=result['token'],destination=str(pathlib.Path(folder)/'pub.usda'));published=publish_reviewed(r);stage=Usd.Stage.Open(published['path'])
   self.assertTrue(stage.GetPrimAtPath('/Scene/PreservedInput/OriginalMaterial'))
   self.assertEqual(list(stage.GetPrimAtPath('/Scene/PreservedInput/Mesh/OriginalFaces').GetAttribute('indices').Get()),[])
   self.assertEqual(list(stage.GetPrimAtPath('/Scene/PreservedInput/Mesh/NewFaces').GetAttribute('indices').Get()),[0])
 def test_exporter_unit_normalization_does_not_double_convert_returned_edits(self):
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,['points'])
   F=Gf.Matrix4d().SetScale(100)*Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(1,0,0),90))
   for stage,matrix,unit in [(s,F,0.01),(b,Gf.Matrix4d().SetScale(0.01)*F,1),(e,Gf.Matrix4d().SetScale(0.01)*F,1)]:
    x=UsdGeom.Xformable(stage.GetPrimAtPath('/Handoff'));x.ClearXformOpOrder();x.AddTransformOp().Set(matrix);UsdGeom.SetStageUpAxis(stage,'Z');UsdGeom.SetStageMetersPerUnit(stage,unit);stage.GetRootLayer().Save()
   meta=os.stat(r['handoff']);r['sourceRevisions'][0].update(size=meta.st_size,modifiedNs=str(meta.st_mtime_ns));r['baselineDigest']=file_digest(r['baseline']);r['conversion']['inverse']=[list(row) for row in F.GetInverse()]
   e.GetPrimAtPath('/Handoff/Mesh').GetAttribute('points').Set([(0,0,0),(2,0,0),(0,1,0)]);e.GetRootLayer().Save()
   result=review_publication(r);self.assertFalse(result['report']['conflicts'],result['report']['conflicts']);r.update(reviewToken=result['token'],destination=str(pathlib.Path(folder)/'pub.usda'));published=publish_reviewed(r);stage=Usd.Stage.Open(published['path']);prim=stage.GetPrimAtPath('/Scene/PreservedInput/Mesh')
   world=UsdGeom.XformCache().GetLocalToWorldTransform(prim);self.assertAlmostEqual(world.Transform(Gf.Vec3d(prim.GetAttribute('points').Get()[1]))[0],2)

 def test_static_transform_delta_preserves_animation_without_keyframe_permission(self):
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,['transforms'])
   for stage in [s,b,e]:
    op=UsdGeom.Xformable(stage.GetPrimAtPath('/Handoff/Mesh')).AddTranslateOp();op.Set((1,0,0),1);op.Set((2,0,0),2);stage.GetRootLayer().Save()
   meta=os.stat(r['handoff']);r['sourceRevisions'][0].update(size=meta.st_size,modifiedNs=str(meta.st_mtime_ns));r['baselineDigest']=file_digest(r['baseline'])
   op=UsdGeom.Xformable(e.GetPrimAtPath('/Handoff/Mesh')).GetOrderedXformOps()[0];op.Set((4,0,0),1);op.Set((5,0,0),2);e.GetRootLayer().Save()
   result=review_publication(r);self.assertFalse(result['report']['conflicts'],result['report']['conflicts']);r.update(reviewToken=result['token'],destination=str(pathlib.Path(folder)/'pub.usda'));published=publish_reviewed(r);stage=Usd.Stage.Open(published['path']);prim=stage.GetPrimAtPath('/Scene/PreservedInput/Mesh')
   self.assertAlmostEqual(UsdGeom.XformCache(1).GetLocalToWorldTransform(prim).ExtractTranslation()[0],4);self.assertAlmostEqual(UsdGeom.XformCache(2).GetLocalToWorldTransform(prim).ExtractTranslation()[0],5)
 def test_material_metadata_edit_does_not_replace_original_animation_values(self):
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,['materials'])
   for stage in [s,b,e]:
    mat=UsdShade.Material.Define(stage,'/Handoff/Mat');shader=UsdShade.Shader.Define(stage,'/Handoff/Mat/Shader');attr=shader.CreateInput('file',Sdf.ValueTypeNames.Asset).GetAttr();attr.Set(Sdf.AssetPath('./a.png'),0);attr.Set(Sdf.AssetPath('./b.png'),1);attr.SetMetadata('colorSpace','sRGB');stage.GetRootLayer().Save()
   meta=os.stat(r['handoff']);r['sourceRevisions'][0].update(size=meta.st_size,modifiedNs=str(meta.st_mtime_ns));r['baselineDigest']=file_digest(r['baseline'])
   e.GetPrimAtPath('/Handoff/Mat/Shader').GetAttribute('inputs:file').SetMetadata('colorSpace','raw');e.GetRootLayer().Save()
   result=review_publication(r);self.assertFalse(result['report']['conflicts'],result['report']['conflicts']);r.update(reviewToken=result['token'],destination=str(pathlib.Path(folder)/'pub.usda'));published=publish_reviewed(r);stage=Usd.Stage.Open(published['path']);attr=stage.GetPrimAtPath('/Scene/PreservedInput/Mat/Shader').GetAttribute('inputs:file')
   self.assertEqual(attr.GetMetadata('colorSpace'),'raw');self.assertEqual(attr.GetTimeSamples(),[0,1]);self.assertEqual(attr.Get(1).path,'./b.png')

 def test_transform_overlay_preserves_original_rotation_interpolation(self):
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,['transforms'])
   rotate=UsdGeom.Xformable(s.GetPrimAtPath('/Handoff/Mesh')).AddRotateZOp();rotate.Set(0,1);rotate.Set(180,3);s.GetRootLayer().Save()
   for stage,offset in [(b,0),(e,3)]:
    op=UsdGeom.Xformable(stage.GetPrimAtPath('/Handoff/Mesh')).AddTransformOp()
    for time,angle in [(1,0),(3,180)]:op.Set(Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0,0,1),angle))*Gf.Matrix4d().SetTranslate((offset,0,0)),time)
    stage.GetRootLayer().Save()
   meta=os.stat(r['handoff']);r['sourceRevisions'][0].update(size=meta.st_size,modifiedNs=str(meta.st_mtime_ns));r['baselineDigest']=file_digest(r['baseline'])
   result=review_publication(r);self.assertFalse(result['report']['conflicts'],result['report']['conflicts']);r.update(reviewToken=result['token'],destination=str(pathlib.Path(folder)/'pub.usda'));published=publish_reviewed(r);stage=Usd.Stage.Open(published['path']);prim=stage.GetPrimAtPath('/Scene/PreservedInput/Mesh');matrix=UsdGeom.XformCache(2).GetLocalToWorldTransform(prim)
   self.assertAlmostEqual(matrix[0][1],1);self.assertAlmostEqual(matrix.ExtractTranslation()[0],3)
   self.assertIn('xformOp:rotateZ',list(prim.GetAttribute('xformOpOrder').Get()))
 def test_blender_object_and_mesh_data_can_map_to_the_same_input_prim_by_role(self):
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,['transforms','points'])
   s.GetPrimAtPath('/Handoff/Mesh').CreateAttribute('userProperties:orchflowId',Sdf.ValueTypeNames.String).Set('mesh-id');s.GetRootLayer().Save()
   editor=Usd.NamespaceEditor(b);editor.MovePrimAtPath('/Handoff/Mesh','/Handoff/TemporaryData');self.assertTrue(editor.ApplyEdits());UsdGeom.Xform.Define(b,'/Handoff/Mesh');editor=Usd.NamespaceEditor(b);editor.MovePrimAtPath('/Handoff/TemporaryData','/Handoff/Mesh/Data');self.assertTrue(editor.ApplyEdits())
   b.GetPrimAtPath('/Handoff/Mesh').CreateAttribute('userProperties:orchflowId',Sdf.ValueTypeNames.String).Set('mesh-id');b.GetRootLayer().Save();e.GetRootLayer().TransferContent(b.GetRootLayer())
   UsdGeom.Xformable(e.GetPrimAtPath('/Handoff/Mesh')).AddTranslateOp().Set((3,0,0));e.GetPrimAtPath('/Handoff/Mesh/Data').GetAttribute('points').Set([(0,0,0),(2,0,0),(0,1,0)]);e.GetRootLayer().Save()
   meta=os.stat(r['handoff']);r['sourceRevisions'][0].update(size=meta.st_size,modifiedNs=str(meta.st_mtime_ns));r['baselineDigest']=file_digest(r['baseline'])
   r['correspondence']=[{'baselinePath':'/Handoff/Mesh/Data','inputPath':'/Handoff/Mesh','editedPath':'/Handoff/Mesh/Data'}]
   result=review_publication(r);self.assertFalse(result['report']['conflicts'],result['report']['conflicts']);r.update(reviewToken=result['token'],destination=str(pathlib.Path(folder)/'pub.usda'));published=publish_reviewed(r);stage=Usd.Stage.Open(published['path']);prim=stage.GetPrimAtPath('/Scene/PreservedInput/Mesh')
   self.assertAlmostEqual(UsdGeom.XformCache().GetLocalToWorldTransform(prim).Transform(Gf.Vec3d(prim.GetAttribute('points').Get()[1]))[0],5)

 def test_wrong_round_trip_is_rejected_before_any_publication(self):
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,['points']);e.GetDefaultPrim().GetAttribute('userProperties:orchflowRoundTripId').Set('different-native-project');e.GetRootLayer().Save()
   with self.assertRaisesRegex(ValueError,'round-trip identity'):review_publication(r)

 def test_embedded_worker_dispatches_review_and_publication(self):
  # Match the packaged worker: concatenate sources rather than import modules.
  namespace={'__name__':'orchflow_embedded_test'}
  scripts=['usd_graph.py','usd_publish.py','usd_worker.py']
  exec('\n'.join((pathlib.Path(__file__).parent/name).read_text() for name in scripts),namespace)
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,['points'])
   e.GetPrimAtPath('/Handoff/Mesh').GetAttribute('points').Set([(0,0,0),(3,0,0),(0,1,0)]);e.GetRootLayer().Save()
   review=namespace['dispatch']({**r,'operation':'review_publication'})
   self.assertFalse(review['report']['conflicts'])
   result=namespace['dispatch']({**r,'operation':'publish_reviewed','reviewToken':review['token'],'destination':str(pathlib.Path(folder)/'embedded.usda')})
   self.assertTrue(Usd.Stage.Open(result['path']))
 def test_graph_identity_ignores_json_integer_float_spelling(self):
  from usd_graph import graph_digest
  self.assertEqual(graph_digest({'transform':[0,1,2]}),graph_digest({'transform':[0.0,1.0,2.0]}))

 def test_redundant_opinions_are_pruned_against_upstream(self):
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,['uvs'])
   for stage,value in [(s,[(0,0),(1,0),(0,1)]),(b,[(.1,0),(1,0),(0,1)]),(e,[(0,0),(1,0),(0,1)])]:
    pv=UsdGeom.PrimvarsAPI(stage.GetPrimAtPath('/Handoff/Mesh')).CreatePrimvar('st',Sdf.ValueTypeNames.TexCoord2fArray,UsdGeom.Tokens.vertex);pv.Set(value);stage.GetRootLayer().Save()
   meta=os.stat(r['handoff']);r['sourceRevisions'][0].update(size=meta.st_size,modifiedNs=str(meta.st_mtime_ns));r['baselineDigest']=file_digest(r['baseline'])
   review,layer,output,alive=review_publication(r,True)
   self.assertFalse(review['report']['conflicts']);self.assertGreater(review['report']['redundantOpinionsPruned'],0)
   self.assertFalse(layer.GetPropertyAtPath('/Handoff/Mesh.primvars:st'))
   self.assertTrue(output.GetPrimAtPath('/Handoff/Mesh').GetAttribute('primvars:st'))

 def test_reconciliation_layers_existing_points_over_current_upstream_without_export(self):
  from usd_graph import export_handoff
  from usd_publish import reconcile_upstream
  with tempfile.TemporaryDirectory() as folder:
   root=pathlib.Path(folder);asset=root/'asset.usda';stage=Usd.Stage.CreateNew(str(asset));x=UsdGeom.Xform.Define(stage,'/Asset');stage.SetDefaultPrim(x.GetPrim());UsdGeom.SetStageUpAxis(stage,'Y');UsdGeom.SetStageMetersPerUnit(stage,1)
   mesh=UsdGeom.Mesh.Define(stage,'/Asset/Mesh');mesh.CreatePointsAttr([(0,0,0),(1,0,0),(0,1,0)]);mesh.CreateFaceVertexCountsAttr([3]);mesh.CreateFaceVertexIndicesAttr([0,1,2]);mesh.CreateSubdivisionSchemeAttr('none');mesh.GetPrim().CreateAttribute('upstream:tag',Sdf.ValueTypeNames.String).Set('before');stage.GetRootLayer().Save()
   graph={'targetNodeId':'app','nodes':[{'id':'src','operation':'source','inputDependencyId':'asset'},{'id':'move','operation':'transform','transform':{'translation':[0,0,0]}},{'id':'app','operation':'output'}],'edges':[{'id':'a','source':'src','target':'move'},{'id':'b','source':'move','target':'app'}],'dependencies':[{'id':'asset','path':str(asset)}]}
   prepared=export_handoff({'graph':graph,'app':'Blender','upAxis':'Z','metersPerUnit':.01,'scale':2,'destination':str(root/'handoff.usda')})
   source=Usd.Stage.Open(prepared['path']);baseline=root/'baseline.usda';source.Flatten().Export(str(baseline));edited=root/'edited.usda';source.Flatten().Export(str(edited));edit=Usd.Stage.Open(str(edited))
   prim=next(p for p in edit.Traverse(Usd.TraverseInstanceProxies()) if p.IsA(UsdGeom.Mesh))
   while prim.IsInstanceProxy():
    parent=prim.GetParent()
    while parent and (not parent.IsInstance() or parent.IsInstanceProxy()):parent=parent.GetParent()
    edit.OverridePrim(parent.GetPath()).SetInstanceable(False);prim=edit.GetPrimAtPath(prim.GetPath())
   prim.GetAttribute('points').Set([(0,0,0),(2,0,0),(0,1,0)]);edit.GetRootLayer().Save()
   records=[]
   for layer in source.GetUsedLayers():
    if layer.realPath:
     stat=os.stat(layer.realPath);records.append({'path':layer.realPath,'size':stat.st_size,'modifiedNs':str(stat.st_mtime_ns)})
   policy={'editScope':['points'],'profile':{'capabilities':{'points':{'status':'supported'}}}}
   r={'handoff':prepared['path'],'baseline':str(baseline),'edited':str(edited),'baselineDigest':file_digest(baseline),'sourceRevisions':records,'policy':policy,'conversion':prepared['draft'],'currentGraph':graph,'correspondence':[],'baselineDependencyId':'baseline','handoffDependencyId':'handoff'}
   reviewed=review_publication(r);self.assertFalse(reviewed['report']['conflicts'],reviewed['report']['conflicts']);r.update(reviewToken=reviewed['token'],destination=str(root/'published.usda'));published=publish_reviewed(r)
   original_adjustments=pathlib.Path(published['editLayerPath']).read_bytes();original_export=edited.read_bytes()
   mesh.GetPrim().GetAttribute('upstream:tag').Set('after');mesh.GetPointsAttr().Set([(0,0,0),(9,0,0),(0,1,0)]);UsdGeom.Camera.Define(stage,'/Asset/AddedCamera');stage.GetRootLayer().Save();graph['nodes'][1]['transform']['translation']=[3,0,0]
   request={'adjustmentLayer':published['editLayerPath'],'publicationDependencyId':'publication','currentGraph':graph,'conversion':prepared['draft'],'policy':policy,'report':published['report'],'correspondence':[],'baselineDependencyId':'baseline','handoffDependencyId':'handoff','destination':str(root/'reconciled.usda')}
   result=reconcile_upstream(request);output=Usd.Stage.Open(result['path']);prims=list(output.Traverse(Usd.TraverseInstanceProxies()));out=next(p for p in prims if p.IsA(UsdGeom.Mesh))
   self.assertEqual(out.GetAttribute('upstream:tag').Get(),'after');self.assertEqual(out.GetAttribute('points').Get()[1][0],2);self.assertTrue(any(p.IsA(UsdGeom.Camera) for p in prims))
   self.assertAlmostEqual(UsdGeom.XformCache().GetLocalToWorldTransform(out).Transform(Gf.Vec3d(0))[0],3)
   self.assertEqual(pathlib.Path(published['editLayerPath']).read_bytes(),original_adjustments);self.assertEqual(edited.read_bytes(),original_export)
   self.assertEqual(result['replacesPublicationDependencyId'],'publication')
   with self.assertRaisesRegex(ValueError,'exists'):reconcile_upstream(request)
   # A second reconciliation keeps the same opinion strength and original native convention.
   request.update(adjustmentLayer=result['editLayerPath'],destination=str(root/'again.usda'))
   second=reconcile_upstream(request);self.assertTrue(Usd.Stage.Open(second['path']))

 def test_changed_uvs_do_not_freeze_identical_interpolation_metadata(self):
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,['uvs'])
   for stage in [s,b,e]:
    UsdGeom.PrimvarsAPI(stage.GetPrimAtPath('/Handoff/Mesh')).CreatePrimvar('st',Sdf.ValueTypeNames.TexCoord2fArray,UsdGeom.Tokens.vertex).Set([(0,0),(1,0),(0,1)]);stage.GetRootLayer().Save()
   e.GetPrimAtPath('/Handoff/Mesh').GetAttribute('primvars:st').Set([(.5,0),(1,0),(0,1)]);e.GetRootLayer().Save()
   stat=os.stat(r['handoff']);r['sourceRevisions'][0].update(size=stat.st_size,modifiedNs=str(stat.st_mtime_ns));r['baselineDigest']=file_digest(r['baseline'])
   result,layer,output,alive=review_publication(r,True);self.assertFalse(result['report']['conflicts'])
   spec=layer.GetPropertyAtPath('/Handoff/Mesh.primvars:st');self.assertTrue(spec.HasInfo('default'));self.assertFalse(spec.HasInfo('interpolation'))
   self.assertEqual(UsdGeom.PrimvarsAPI(output.GetPrimAtPath('/Handoff/Mesh')).GetPrimvar('st').GetInterpolation(),'vertex')

 def test_constant_sampled_ids_match_renamed_export_root(self):
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,['points'])
   s.GetPrimAtPath('/Handoff/Mesh').CreateAttribute('userProperties:orchflowId',Sdf.ValueTypeNames.String).Set('mesh-id');s.GetRootLayer().Save()
   attr=b.GetPrimAtPath('/Handoff/Mesh').CreateAttribute('userProperties:orchflowId',Sdf.ValueTypeNames.String);attr.Set('mesh-id',1);attr.Set('mesh-id',2);b.GetRootLayer().Save()
   e.GetPrimAtPath('/Handoff/Mesh').CreateAttribute('userProperties:orchflowId',Sdf.ValueTypeNames.String).Set('mesh-id');e.GetPrimAtPath('/Handoff/Mesh').GetAttribute('points').Set([(0,0,0),(3,0,0),(0,1,0)])
   UsdGeom.Xform.Define(e,'/root');editor=Usd.NamespaceEditor(e);editor.MovePrimAtPath('/Handoff','/root/Handoff');self.assertTrue(editor.ApplyEdits());e.GetRootLayer().Save()
   stat=os.stat(r['handoff']);r['sourceRevisions'][0].update(size=stat.st_size,modifiedNs=str(stat.st_mtime_ns));r['baselineDigest']=file_digest(r['baseline'])
   result=review_publication(r);self.assertFalse(result['report']['conflicts'],result['report']['conflicts']);self.assertTrue(any(x['property']=='points' for x in result['report']['applied']))
 def test_empty_scope_does_not_silently_publish_an_unchanged_scene(self):
  with tempfile.TemporaryDirectory() as folder:
   r,s,b,e=self.fixture(folder,[]);e.GetPrimAtPath('/Handoff/Mesh').GetAttribute('points').Set([(0,0,0),(3,0,0),(0,1,0)]);e.GetRootLayer().Save()
   reviewed=review_publication(r);r.update(reviewToken=reviewed['token'],destination=str(pathlib.Path(folder)/'no-edits.usda'))
   with self.assertRaisesRegex(ValueError,'No permitted edits'):publish_reviewed(r)
   self.assertFalse(pathlib.Path(r['destination']).exists())

class SampleMismatchTests(unittest.TestCase):
 def test_point_sample_mismatch_fails_without_reading_all_frames(self):
  fixture=PublishTests()
  with tempfile.TemporaryDirectory() as folder:
   request,source,baseline,edited=fixture.fixture(folder,['points'])
   bp=baseline.GetPrimAtPath('/Handoff/Mesh').GetAttribute('points')
   for t in range(1,202):bp.Set([(0,0,0),(1+t,0,0),(0,1,0)],t)
   baseline.GetRootLayer().Save()
   request['baselineDigest']=file_digest(request['baseline'])
   ep=edited.GetPrimAtPath('/Handoff/Mesh').GetAttribute('points')
   ep.Set([(0,0,0),(2,0,0),(0,1,0)])
   edited.GetRootLayer().Save()
   result=review_publication(request)
   self.assertTrue(any('Point sample times differ' in c['reason'] for c in result['report']['conflicts']))

class ManualSelectionTests(unittest.TestCase):
 def fixture(self,folder):
  request,source,baseline,edited=PublishTests().fixture(folder,['points','transforms','topology'])
  source.GetPrimAtPath('/Handoff/Mesh').CreateAttribute('userProperties:orchflowId',Sdf.ValueTypeNames.String).Set('mesh-id');source.GetRootLayer().Save()
  for stage in (baseline,edited):
   saved=Sdf.Layer.CreateAnonymous();Sdf.CopySpec(stage.GetRootLayer(),'/Handoff/Mesh',saved,'/SelectedMesh')
   stage.GetRootLayer().Clear();Sdf.CopySpec(saved,'/SelectedMesh',stage.GetRootLayer(),'/SelectedMesh')
   prim=stage.GetPrimAtPath('/SelectedMesh');prim.CreateAttribute('userProperties:orchflowId',Sdf.ValueTypeNames.String).Set('mesh-id')
   UsdGeom.Xformable(prim).AddScaleOp().Set((2,2,2));UsdGeom.SetStageUpAxis(stage,'Y');UsdGeom.SetStageMetersPerUnit(stage,1);stage.GetRootLayer().Save()
  meta=os.stat(request['handoff']);request['sourceRevisions'][0].update(size=meta.st_size,modifiedNs=str(meta.st_mtime_ns));request['baselineDigest']=file_digest(request['baseline']);request['manualBaseline']=True
  return request,source,baseline,edited
 def test_rootless_selection_matches_identity_and_preserves_outside_selection(self):
  with tempfile.TemporaryDirectory() as folder:
   request,source,baseline,edited=self.fixture(folder)
   result=review_publication(request)
   self.assertFalse(result['report']['applied']);self.assertFalse(result['report']['conflicts'])
   edited.GetPrimAtPath('/SelectedMesh').GetAttribute('points').Set([(0,0,0),(2,0,0),(0,1,0)]);edited.GetRootLayer().Save()
   result=review_publication(request);self.assertFalse(result['report']['conflicts']);self.assertTrue(any(c['property']=='points' for c in result['report']['applied']))
   self.assertTrue(source.GetPrimAtPath('/Handoff/UnsupportedCamera'))
 def test_missing_ids_are_blocked(self):
  with tempfile.TemporaryDirectory() as folder:
   request,source,baseline,edited=self.fixture(folder)
   baseline.GetPrimAtPath('/SelectedMesh').RemoveProperty('userProperties:orchflowId');baseline.GetRootLayer().Save();request['baselineDigest']=file_digest(request['baseline'])
   with self.assertRaisesRegex(ValueError,'matching OrchFlow ID'):review_publication(request)
 def test_manual_baseline_validation_is_read_only_and_hashed(self):
  from usd_publish import validate_manual_baseline
  with tempfile.TemporaryDirectory() as folder:
   request,source,baseline,edited=self.fixture(folder)
   request.update(nativeDependencyId='native',handoffDependencyId='handoff')
   before=pathlib.Path(request['baseline']).read_bytes()
   result=validate_manual_baseline(request)
   self.assertEqual(result['report']['origin'],'manual');self.assertEqual(result['report']['baselineDigest'],file_digest(request['baseline']));self.assertEqual(pathlib.Path(request['baseline']).read_bytes(),before)
 def test_additions_are_explicit_and_scope_gated(self):
  with tempfile.TemporaryDirectory() as folder:
   request,source,baseline,edited=self.fixture(folder)
   mesh=UsdGeom.Mesh.Define(edited,'/NewMesh');mesh.CreatePointsAttr([(0,0,0),(1,0,0),(0,1,0)]);mesh.CreateFaceVertexCountsAttr([3]);mesh.CreateFaceVertexIndicesAttr([0,1,2]);edited.GetRootLayer().Save()
   self.assertTrue(review_publication(request)['report']['conflicts'])
   request['newPrimPaths']=['/NewMesh'];result=review_publication(request)
   self.assertFalse(result['report']['conflicts']);self.assertTrue(any(c['property']=='new prim' for c in result['report']['applied']))
   request['policy']['editScope'].remove('topology');self.assertTrue(review_publication(request)['report']['conflicts'])
