import pathlib,tempfile,unittest,base64
import usd_worker
from usd_worker import missing_shader_assets,selective_material_preview
from pxr import Usd,UsdGeom,UsdShade,Sdf
class SelectiveMaterialTests(unittest.TestCase):
 def fixture(self,path):
  stage=Usd.Stage.CreateNew(str(path));root=UsdGeom.Xform.Define(stage,'/Asset');stage.SetDefaultPrim(root.GetPrim())
  good=UsdShade.Material.Define(stage,'/Asset/Good');shader=UsdShade.Shader.Define(stage,'/Asset/Good/Surface');shader.CreateIdAttr('UsdPreviewSurface');shader.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set((1,.01,.01));shader.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(.8);good.CreateSurfaceOutput().ConnectToSource(shader.CreateOutput('surface',Sdf.ValueTypeNames.Token))
  bad=UsdShade.Material.Define(stage,'/Asset/Bad');surface=UsdShade.Shader.Define(stage,'/Asset/Bad/Surface');surface.CreateIdAttr('UsdPreviewSurface');texture=UsdShade.Shader.Define(stage,'/Asset/Bad/Texture');texture.CreateIdAttr('UsdUVTexture');texture.CreateInput('file',Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('./missing.png'));surface.CreateInput('opacity',Sdf.ValueTypeNames.Float).ConnectToSource(texture.CreateOutput('r',Sdf.ValueTypeNames.Float));bad.CreateSurfaceOutput().ConnectToSource(surface.CreateOutput('surface',Sdf.ValueTypeNames.Token))
  for name,x,material in [('Red',-1.4,good),('Gray',1.4,bad)]:
   sphere=UsdGeom.Sphere.Define(stage,'/Asset/'+name);sphere.AddTranslateOp().Set((x,0,0));UsdShade.MaterialBindingAPI.Apply(sphere.GetPrim()).Bind(material)
  # A missing asset in an unconnected shader must not spoil a good material.
  unused=UsdShade.Shader.Define(stage,'/Asset/Good/Unused');unused.CreateIdAttr('UsdUVTexture');unused.CreateInput('file',Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath('./unused.png'))
  unused_uv=UsdShade.Shader.Define(stage,'/Asset/Good/UnusedUV');unused_uv.CreateIdAttr('UsdPrimvarReader_float2');unused_uv.CreateInput('varname',Sdf.ValueTypeNames.Token).Set('_')
  stage.GetRootLayer().Save();return stage
 def test_only_connected_bad_material_is_replaced_without_editing_sources(self):
  with tempfile.TemporaryDirectory() as folder:
   path=pathlib.Path(folder)/'asset.usda';stage=self.fixture(path);before=path.read_bytes();original=stage.GetRootLayer().ExportToString()
   preview,alive,affected=selective_material_preview(stage,missing_shader_assets(stage,0),0)
   self.assertEqual(affected,['/Asset/Bad']);self.assertEqual(usd_worker.invalid_shader_inputs(stage,0),[])
   good=UsdShade.Material(preview.GetPrimAtPath('/Asset/Good')).ComputeSurfaceSource()[0];self.assertEqual(good.GetPath(),Sdf.Path('/Asset/Good/Surface'));self.assertEqual(good.GetInput('diffuseColor').Get()[0],1)
   fallback=UsdShade.Material(preview.GetPrimAtPath('/Asset/Bad')).ComputeSurfaceSource()[0];self.assertEqual(fallback.GetPath(),Sdf.Path('/Asset/Bad/Solid'));self.assertEqual(fallback.GetInput('opacity').Get(),1)
   self.assertEqual(UsdShade.MaterialBindingAPI(preview.GetPrimAtPath('/Asset/Gray')).ComputeBoundMaterial()[0].GetPath(),Sdf.Path('/Asset/Bad'))
   self.assertEqual(stage.GetRootLayer().ExportToString(),original);self.assertEqual(path.read_bytes(),before)
 def test_instance_embedded_materials_keep_shared_prototypes_and_face_subsets(self):
  with tempfile.TemporaryDirectory() as folder:
   path=pathlib.Path(folder)/'asset.usda';asset=self.fixture(path)
   mesh=UsdGeom.Mesh.Define(asset,'/Asset/Mesh');mesh.CreatePointsAttr([(0,0,0),(1,0,0),(0,1,0)]);mesh.CreateFaceVertexCountsAttr([3]);mesh.CreateFaceVertexIndicesAttr([0,1,2]);subset=UsdGeom.Subset.Define(asset,'/Asset/Mesh/BadFaces');subset.CreateElementTypeAttr('face');subset.CreateFamilyNameAttr('materialBind');subset.CreateIndicesAttr([0]);UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(UsdShade.Material(asset.GetPrimAtPath('/Asset/Bad')));asset.GetRootLayer().Save()
   stage=Usd.Stage.CreateInMemory()
   for name in ['One','Two']:
    prim=stage.DefinePrim('/'+name);prim.GetReferences().AddReference(str(path));prim.SetInstanceable(True)
   count=len(stage.GetPrototypes());preview,alive,affected=selective_material_preview(stage,missing_shader_assets(stage,0),0)
   self.assertEqual(len(preview.GetPrototypes()),count);self.assertEqual(len(preview.GetPrototypes()),1)
   self.assertEqual(set(affected),{'/One/Bad','/Two/Bad'})
   for name in ['One','Two']:
    material=UsdShade.MaterialBindingAPI(preview.GetPrimAtPath('/'+name+'/Mesh/BadFaces')).ComputeBoundMaterial()[0]
    self.assertTrue(material.ComputeSurfaceSource()[0].GetPath().name=='Solid')
    self.assertTrue(preview.GetPrimAtPath('/'+name).IsInstance())
 def test_auto_image_retains_red_and_scene_mode_restores_original_network(self):
  if not usd_worker.dispatch({'operation':'capabilities'})['viewport']:self.skipTest('Requires imaging')
  from PySide6.QtGui import QImage
  with tempfile.TemporaryDirectory() as folder:
   path=pathlib.Path(folder)/'asset.usda';stage=self.fixture(path);before=path.read_bytes()
   request={'operation':'render','path':str(path),'renderer':'HdStormRendererPlugin','width':480,'height':320,'yaw':0,'pitch':0}
   auto=usd_worker.dispatch({**request,'materials':'auto','reload':True});self.assertEqual(auto['materialMode'],'mixed');self.assertEqual(auto['fallbackMaterials'],['/Asset/Bad'])
   image=QImage.fromData(base64.b64decode(auto['image'].split(',')[1]));reds=sum(1 for y in range(image.height()) for x in range(image.width()) if (lambda c:c.red()>c.green()*1.6 and c.red()>c.blue()*1.6 and c.red()>60)(image.pixelColor(x,y)))
   self.assertGreater(reds,100,'Healthy red material should stay colored in Auto')
   solid=usd_worker.dispatch({**request,'materials':'solid'});self.assertEqual(solid['materialMode'],'solid');self.assertNotEqual(auto['image'],solid['image'])
   scene=usd_worker.dispatch({**request,'materials':'scene'});self.assertEqual(scene['materialMode'],'scene');self.assertEqual(scene['fallbackMaterials'],[])
   again=usd_worker.dispatch({**request,'materials':'auto'});self.assertEqual(again['image'],auto['image']);self.assertEqual(path.read_bytes(),before)

 def test_missing_shader_implementation_asset_uses_material_fallback(self):
  stage=Usd.Stage.CreateInMemory();mat=UsdShade.Material.Define(stage,'/Material');shader=UsdShade.Shader.Define(stage,'/Material/Surface');shader.SetSourceAsset(Sdf.AssetPath('missing-shader.glslfx'),'glslfx');mat.CreateSurfaceOutput().ConnectToSource(shader.CreateOutput('surface',Sdf.ValueTypeNames.Token))
  missing=missing_shader_assets(stage,0);self.assertEqual(len(missing),1)
  preview,alive,affected=selective_material_preview(stage,missing,0);self.assertEqual(affected,['/Material']);self.assertEqual(UsdShade.Material(preview.GetPrimAtPath('/Material')).ComputeSurfaceSource()[0].GetIdAttr().Get(),'UsdPreviewSurface')

 def test_invalid_uv_reader_is_preview_fallback_without_guessing_a_uv_set(self):
  with tempfile.TemporaryDirectory() as folder:
   path=pathlib.Path(folder)/'invalid-uv.usda';stage=self.fixture(path)
   shader=UsdShade.Shader.Define(stage,'/Asset/Good/UV');shader.CreateIdAttr('UsdPrimvarReader_float2');shader.CreateInput('varname',Sdf.ValueTypeNames.Token).Set('_');shader.CreateOutput('result',Sdf.ValueTypeNames.Float2)
   texture=UsdShade.Shader.Define(stage,'/Asset/Good/Tex');texture.CreateIdAttr('UsdUVTexture');texture.CreateInput('st',Sdf.ValueTypeNames.Float2).ConnectToSource(shader.ConnectableAPI(),'result');texture.CreateOutput('rgb',Sdf.ValueTypeNames.Float3)
   UsdShade.Shader(stage.GetPrimAtPath('/Asset/Good/Surface')).GetInput('diffuseColor').ConnectToSource(texture.ConnectableAPI(),'rgb');stage.GetRootLayer().Save();before=path.read_bytes()
   invalid=usd_worker.invalid_shader_inputs(stage,0);self.assertEqual(len(invalid),1)
   preview,alive,affected=selective_material_preview(stage,missing_shader_assets(stage,0),0,invalid);self.assertIn('/Asset/Good',affected)
   self.assertEqual(UsdShade.Shader(stage.GetPrimAtPath('/Asset/Good/UV')).GetInput('varname').Get(),'_');self.assertEqual(path.read_bytes(),before)
   if usd_worker.dispatch({'operation':'capabilities'})['viewport']:
    request={'operation':'render','path':str(path),'renderer':'HdStormRendererPlugin','width':320,'height':240}
    result=usd_worker.dispatch({**request,'materials':'auto'});self.assertEqual(result['materialMode'],'mixed');self.assertEqual(len(result['invalidShaderInputs']),1)
    with self.assertRaisesRegex(ValueError,'no|Cannot render source materials'):usd_worker.dispatch({**request,'materials':'scene'})
    self.assertEqual(usd_worker.dispatch({**request,'materials':'solid'})['materialMode'],'solid')
