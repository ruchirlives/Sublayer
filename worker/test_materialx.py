import pathlib,tempfile,unittest
import usd_worker
from usd_graph import compile_graph,materialx_document
from pxr import Usd,UsdGeom,UsdShade,Sdf
class MaterialXTests(unittest.TestCase):
 def graph(self):
  sample=pathlib.Path(__file__).resolve().parents[1]/'samples/layered.usda'
  return {'targetNodeId':'material','nodes':[{'id':'a','operation':'source','inputDependencyId':'asset'},{'id':'b','operation':'source','inputDependencyId':'asset'},{'id':'material','operation':'materialx','materialX':{'name':'TestMaterial','baseColor':[.2,.5,.8],'metalness':.1,'roughness':.25,'bindingTargets':['*']}}],'edges':[{'id':'a-m','source':'a','target':'material'},{'id':'b-m','source':'b','target':'material'}],'dependencies':[{'id':'asset','path':str(sample)}]}
 def test_dual_context_authoring_preserves_originals_and_shared_geometry(self):
  graph=self.graph();path=pathlib.Path(graph['dependencies'][0]['path']);before=path.read_bytes();stage,alive,stats=compile_graph(graph)
  self.assertGreater(stats['prototypes'],0)
  mat_prim=next(p for p in stage.GetPrimAtPath('/Scene/_OrchFlowMaterials').GetChildren() if p.GetName().startswith('TestMaterial'))
  material=UsdShade.Material(mat_prim);self.assertTrue(material)
  self.assertEqual(material.ComputeSurfaceSource('mtlx')[0].GetIdAttr().Get(),'ND_standard_surface_surfaceshader');self.assertEqual(material.ComputeSurfaceSource()[0].GetIdAttr().Get(),'UsdPreviewSurface')
  for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
   if prim.IsA(UsdGeom.Gprim):self.assertEqual(UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0].GetPath(),material.GetPath())
  self.assertEqual(path.read_bytes(),before)
 def test_document_reopens_with_real_usd_materialx_plugin(self):
  doc=materialx_document({'materialX':{'name':'TestMaterial'},'dependencies':[]})
  with tempfile.TemporaryDirectory() as folder:
   path=pathlib.Path(folder)/'test.mtlx';path.write_text(doc['document'],encoding='utf-8');stage=Usd.Stage.Open(str(path));self.assertTrue(stage)
   self.assertTrue(any(p.IsA(UsdShade.Material) for p in stage.Traverse()))
 def test_texture_networks_link_assets_and_parameters_are_checked(self):
  graph=self.graph();graph['dependencies'].append({'id':'texture','path':'I:/textures/missing.png'});graph['nodes'][-1]['materialX']['baseColorTextureDependencyId']='texture'
  stage,alive,_=compile_graph(graph);mat_prim=next(p for p in stage.GetPrimAtPath('/Scene/_OrchFlowMaterials').GetChildren() if p.GetName().startswith('TestMaterial'));shader=UsdShade.Shader(mat_prim.GetPrimAtPath('Image_color3'))
  self.assertEqual(shader.GetInput('file').Get().path,'I:/textures/missing.png');self.assertEqual(shader.GetInput('file').GetAttr().GetColorSpace(),'srgb_texture')
  graph['nodes'][-1]['materialX']['roughness']=-1
  with self.assertRaisesRegex(ValueError,'between 0 and 1'):compile_graph(graph)
 def test_missing_binding_target_is_an_error(self):
  graph=self.graph();graph['nodes'][-1]['materialX']['bindingTargets']=['/Missing']
  with self.assertRaisesRegex(ValueError,'binding target'):compile_graph(graph)
 def test_storm_renders_authored_materialx(self):
  caps=usd_worker.dispatch({'operation':'capabilities'})
  if not caps['viewport']:self.skipTest('Imaging runtime unavailable')
  result=usd_worker.dispatch({'operation':'render','graph':self.graph(),'renderer':'HdStormRendererPlugin','width':320,'height':240,'materialMode':'scene'})
  self.assertTrue(result['image'].startswith('data:image/png;base64,'))
 def test_procedural_authoring_and_extended_channels(self):
  graph=self.graph()
  mat=graph['nodes'][-1]['materialX']
  mat['emission']=0.8
  mat['emissionColor']=[1.0,0.5,0.2]
  mat['coat']=0.5
  mat['coatRoughness']=0.05
  mat['transmission']=0.9
  mat['proceduralBaseColor']={'type':'noise2d','space':'uv','amplitude':1.0}
  mat['proceduralRoughness']={'type':'fractal3d','space':'object','amplitude':0.8,'octaves':4}
  stage,alive,stats=compile_graph(graph)
  mat_prim=next(p for p in stage.GetPrimAtPath('/Scene/_OrchFlowMaterials').GetChildren() if p.GetName().startswith('TestMaterial'))
  noise_prim=mat_prim.GetPrimAtPath('Procedural_proceduralBaseColor')
  self.assertTrue(noise_prim)
  self.assertEqual(noise_prim.GetTypeName(),'Shader')
  self.assertEqual(UsdShade.Shader(noise_prim).GetIdAttr().Get(),'ND_noise2d_color3')
  fractal_prim=mat_prim.GetPrimAtPath('Procedural_proceduralRoughness')
  self.assertTrue(fractal_prim)
  self.assertEqual(UsdShade.Shader(fractal_prim).GetIdAttr().Get(),'ND_fractal3d_float')
  self.assertFalse(mat_prim.GetPrimAtPath('MdlProceduralSurface'))
  doc=materialx_document({'materialX':mat,'dependencies':[]})
  self.assertIn('<noise2d name="Procedural_proceduralBaseColor"',doc['document'])
  self.assertIn('<fractal3d name="Procedural_proceduralRoughness"',doc['document'])
  with tempfile.TemporaryDirectory() as folder:
   path=pathlib.Path(folder)/'test_proc.mtlx'
   path.write_text(doc['document'],encoding='utf-8')
   proc_stage=Usd.Stage.Open(str(path))
   self.assertTrue(proc_stage)
 def test_null_color_channels_fallback_cleanly(self):
  graph=self.graph()
  mat=graph['nodes'][-1]['materialX']
  mat['specularColor']=None
  mat['coatColor']=None
  mat['transmissionColor']=None
  mat['emissionColor']=None
  mat['sheenColor']=None
  mat['subsurfaceColor']=None
  stage,alive,stats=compile_graph(graph)
  self.assertTrue(any(p.GetName().startswith('TestMaterial') for p in stage.GetPrimAtPath('/Scene/_OrchFlowMaterials').GetChildren()))
 def test_procedural_graph_multi_output_mapping_and_mtlx_export(self):
  graph=self.graph()
  graph['nodes'][-1]['materialXRules']=[{'id':'rule1','enabled':True,'searchType':'simple','query':'*','material':{'name':'ProcGraphMat','baseColor':[0.8,0.4,0.2],'metalness':0.0,'roughness':0.5,'specular':0.5,'opacity':1.0,'bindingTargets':['*']}}]
  pg={
   'nodes':[
    {'id':'place1','type':'procedural','position':{'x':0,'y':0},'data':{'kind':'place2d','params':{'scale':[3.0,3.0],'rotate':25.0}}},
    {'id':'worley1','type':'procedural','position':{'x':160,'y':0},'data':{'kind':'worley2d','params':{'jitter':0.9}}},
    {'id':'mix1','type':'procedural','position':{'x':320,'y':0},'data':{'kind':'mix','params':{'fg':[0.9,0.2,0.1],'bg':[0.1,0.3,0.8]}}},
    {'id':'frac1','type':'procedural','position':{'x':160,'y':160},'data':{'kind':'fractal3d','params':{'octaves':4,'amplitude':0.7}}},
    {'id':'out_col','type':'procedural','position':{'x':500,'y':0},'data':{'kind':'output','materialXNodeId':'material','ruleId':'rule1','parameter':'base_color','mappingType':'mix','strength':0.8,'enabled':True}},
    {'id':'out_rgh','type':'procedural','position':{'x':500,'y':160},'data':{'kind':'output','materialXNodeId':'material','ruleId':'rule1','parameter':'specular_roughness','mappingType':'multiply','strength':1.0,'enabled':True}},
    {'id':'out_nrm','type':'procedural','position':{'x':500,'y':280},'data':{'kind':'output','materialXNodeId':'material','ruleId':'rule1','parameter':'normal','mappingType':'bump_normal','strength':0.2,'enabled':True}},
   ],
   'edges':[
    {'id':'e1','source':'place1','sourceHandle':'out','target':'worley1','targetHandle':'texcoord'},
    {'id':'e2','source':'worley1','sourceHandle':'out','target':'mix1','targetHandle':'mix'},
    {'id':'e3','source':'mix1','sourceHandle':'out','target':'out_col','targetHandle':'in'},
    {'id':'e4','source':'frac1','sourceHandle':'out','target':'out_rgh','targetHandle':'in'},
    {'id':'e5','source':'worley1','sourceHandle':'out','target':'out_nrm','targetHandle':'in'},
   ],
  }
  graph['proceduralGraph']=pg
  stage,alive,stats=compile_graph(graph)
  mat_prim=next(p for p in stage.GetPrimAtPath('/Scene/_OrchFlowMaterials').GetChildren() if p.GetName().startswith('ProcGraphMat'))
  surf=UsdShade.Shader(mat_prim.GetPrimAtPath('StandardSurface'))
  self.assertTrue(surf.GetInput('base_color').HasConnectedSource())
  self.assertTrue(surf.GetInput('specular_roughness').HasConnectedSource())
  self.assertTrue(surf.GetInput('normal').HasConnectedSource())
  self.assertTrue(mat_prim.GetPrimAtPath('ProcMix_out_col'))
  self.assertTrue(mat_prim.GetPrimAtPath('ProcMul_out_rgh'))
  self.assertTrue(mat_prim.GetPrimAtPath('ProcOutBump_out_nrm'))
  # Verify PreviewSurface stays clean
  prev=UsdShade.Shader(mat_prim.GetPrimAtPath('PreviewSurface'))
  self.assertFalse(prev.GetInput('diffuseColor').HasConnectedSource())
  # Verify .mtlx export with proceduralGraph reopens cleanly
  doc=materialx_document({'materialX':graph['nodes'][-1]['materialXRules'][0]['material'],'dependencies':[],'proceduralGraph':pg,'materialXNodeId':'material','ruleId':'rule1'})
  self.assertIn('<worleynoise2d',doc['document'])
  self.assertIn('<bump',doc['document'])
  with tempfile.TemporaryDirectory() as folder:
   path=pathlib.Path(folder)/'proc_graph.mtlx'
   path.write_text(doc['document'],encoding='utf-8')
   mtlx_stage=Usd.Stage.Open(str(path))
   self.assertTrue(mtlx_stage)
  caps=usd_worker.dispatch({'operation':'capabilities'})
  if caps['viewport']:
   result=usd_worker.dispatch({'operation':'render','graph':graph,'renderer':'HdStormRendererPlugin','width':320,'height':240,'materialMode':'scene'})
   self.assertTrue(result['image'].startswith('data:image/png;base64,'))
 def test_place3d_and_vector3_multiply_scaling(self):
  graph=self.graph();graph['nodes'][-1]={'id':'material','operation':'materialx','materialXRules':[{'id':'rule1','enabled':True,'searchType':'simple','query':'*','material':{'name':'Place3DMat','baseColor':[.5,.5,.5],'metalness':0.,'roughness':.5,'specular':.5,'opacity':1.,'bindingTargets':['*']}}]}
  pg={
   'nodes':[
    {'id':'pos1','type':'procedural','position':{'x':0,'y':0},'data':{'kind':'position','params':{'space':'world'}}},
    {'id':'p3d','type':'procedural','position':{'x':160,'y':0},'data':{'kind':'place3d','params':{'scale':[5.,5.,5.],'rotate':30.,'offset':[0.1,0.2,0.3]}}},
    {'id':'mul3','type':'procedural','position':{'x':320,'y':0},'data':{'kind':'multiply','params':{'in2':2.0}}},
    {'id':'w3d','type':'procedural','position':{'x':480,'y':0},'data':{'kind':'worley3d','params':{'jitter':0.8}}},
    {'id':'out1','type':'procedural','position':{'x':640,'y':0},'data':{'kind':'output','materialXNodeId':'material','ruleId':'rule1','parameter':'base_color','mappingType':'replace','strength':1.0,'enabled':True}},
   ],
   'edges':[
    {'id':'e1','source':'pos1','sourceHandle':'out','target':'p3d','targetHandle':'position'},
    {'id':'e2','source':'p3d','sourceHandle':'out','target':'mul3','targetHandle':'in1'},
    {'id':'e3','source':'mul3','sourceHandle':'out','target':'w3d','targetHandle':'position'},
    {'id':'e4','source':'w3d','sourceHandle':'out','target':'out1','targetHandle':'in'},
   ],
  }
  graph['proceduralGraph']=pg
  stage,alive,_=compile_graph(graph)
  mat_prim=next(p for p in stage.GetPrimAtPath('/Scene/_OrchFlowMaterials').GetChildren() if p.GetName().startswith('Place3DMat'))
  self.assertEqual(UsdShade.Shader(mat_prim.GetPrimAtPath('Proc_p3d_mul')).GetIdAttr().Get(),'ND_multiply_vector3')
  self.assertEqual(UsdShade.Shader(mat_prim.GetPrimAtPath('Proc_p3d_rot')).GetIdAttr().Get(),'ND_rotate3d_vector3')
  self.assertEqual(UsdShade.Shader(mat_prim.GetPrimAtPath('Proc_p3d')).GetIdAttr().Get(),'ND_add_vector3')
  self.assertEqual(UsdShade.Shader(mat_prim.GetPrimAtPath('Proc_mul3')).GetIdAttr().Get(),'ND_multiply_vector3')
  doc=materialx_document({'materialX':graph['nodes'][-1]['materialXRules'][0]['material'],'dependencies':[],'proceduralGraph':pg,'materialXNodeId':'material','ruleId':'rule1'})
  self.assertIn('<rotate3d',doc['document'])
  with tempfile.TemporaryDirectory() as folder:
   path=pathlib.Path(folder)/'place3d.mtlx'
   path.write_text(doc['document'],encoding='utf-8')
   self.assertTrue(Usd.Stage.Open(str(path)))
 def test_colorramp_multi_stop_usd_and_mtlx_export(self):
  graph=self.graph();graph['nodes'][-1]={'id':'material','operation':'materialx','materialXRules':[{'id':'rule1','enabled':True,'searchType':'simple','query':'*','material':{'name':'RampMat','baseColor':[.5,.5,.5],'metalness':0.,'roughness':.5,'specular':.5,'opacity':1.,'bindingTargets':['*']}}]}
  pg={
   'nodes':[
    {'id':'w1','type':'procedural','position':{'x':0,'y':0},'data':{'kind':'worley2d','params':{}}},
    {'id':'cr1','type':'procedural','position':{'x':160,'y':0},'data':{'kind':'colorramp','params':{'interpolation':'linear','stops':[{'pos':0.1,'color':[0.0,0.0,0.2]},{'pos':0.5,'color':[0.9,0.2,0.1]},{'pos':0.9,'color':[1.0,1.0,0.8]}]}}},
    {'id':'out1','type':'procedural','position':{'x':340,'y':0},'data':{'kind':'output','materialXNodeId':'material','ruleId':'rule1','parameter':'base_color','mappingType':'replace','strength':1.0,'enabled':True}},
   ],
   'edges':[
    {'id':'e1','source':'w1','sourceHandle':'out','target':'cr1','targetHandle':'in'},
    {'id':'e2','source':'cr1','sourceHandle':'out','target':'out1','targetHandle':'in'},
   ],
  }
  graph['proceduralGraph']=pg
  stage,_,_=compile_graph(graph)
  mat_prim=next(p for p in stage.GetPrimAtPath('/Scene/_OrchFlowMaterials').GetChildren() if p.GetName().startswith('RampMat'))
  self.assertEqual(UsdShade.Shader(mat_prim.GetPrimAtPath('Proc_cr1_r0')).GetIdAttr().Get(),'ND_remap_float')
  self.assertEqual(UsdShade.Shader(mat_prim.GetPrimAtPath('Proc_cr1_c0')).GetIdAttr().Get(),'ND_clamp_float')
  self.assertEqual(UsdShade.Shader(mat_prim.GetPrimAtPath('Proc_cr1_m0')).GetIdAttr().Get(),'ND_mix_color3')
  self.assertEqual(UsdShade.Shader(mat_prim.GetPrimAtPath('Proc_cr1')).GetIdAttr().Get(),'ND_mix_color3')
  doc=materialx_document({'materialX':graph['nodes'][-1]['materialXRules'][0]['material'],'dependencies':[],'proceduralGraph':pg,'materialXNodeId':'material','ruleId':'rule1'})
  self.assertIn('<remap',doc['document'])
  with tempfile.TemporaryDirectory() as folder:
   path=pathlib.Path(folder)/'ramp.mtlx'
   path.write_text(doc['document'],encoding='utf-8')
   self.assertTrue(Usd.Stage.Open(str(path)))

 def test_blender_cycles_and_octane_procedural_extraction(self):
  import types
  import procedural_mtlx
  from usd_graph import author_materialx

  ns={}
  exec(compile(procedural_mtlx.BLENDER_PROCEDURAL_EXTRACTOR,'blender_proc','exec'),ns)
  extract=ns['_extract_blender_procedural_graph']

  class _Sock:
   def __init__(self,default_value=0.0,link=None):
    self.default_value=default_value
    self.links=[link] if link else []
   @property
   def is_linked(self):
    return bool(self.links)

  class _Link:
   def __init__(self,from_node,from_socket_name=''):
    self.from_node=from_node
    self.from_socket=types.SimpleNamespace(name=from_socket_name,identifier=from_socket_name)

  class _Inputs(dict):
   def __iter__(self):
    return iter(self.values())

  def _node(bid,inputs=None,**attrs):
   return types.SimpleNamespace(bl_idname=bid,inputs=_Inputs(inputs or {}),**attrs)

  tex_coord=_node('ShaderNodeTexCoord')
  mapping=_node('ShaderNodeMapping',{
   'Vector':_Sock(link=_Link(tex_coord,'UV')),
   'Location':_Sock([0.1,0.2,0.0]),
   'Rotation':_Sock([0.0,0.0,0.0]),
   'Scale':_Sock([4.0,4.0,1.0]),
  })
  noise=_node('ShaderNodeTexNoise',{
   'Vector':_Sock(link=_Link(mapping,'Vector')),
   'Scale':_Sock(3.0),
   'Detail':_Sock(4.0),
   'Roughness':_Sock(0.5),
   'Lacunarity':_Sock(2.0),
  },noise_dimensions='2D')
  ramp=_node('ShaderNodeValToRGB',{
   'Fac':_Sock(link=_Link(noise,'Fac')),
  },color_ramp=types.SimpleNamespace(
   interpolation='LINEAR',
   elements=[
    types.SimpleNamespace(position=0.2,color=[0.1,0.2,0.3,1.0]),
    types.SimpleNamespace(position=0.8,color=[0.9,0.8,0.7,1.0]),
   ],
  ))
  bump=_node('ShaderNodeBump',{
   'Height':_Sock(link=_Link(noise,'Fac')),
   'Distance':_Sock(0.25),
   'Strength':_Sock(0.8),
  })

  sub_cycles=extract([
   (_Sock(link=_Link(ramp,'Color')),'base_color','replace'),
   (_Sock(link=_Link(bump,'Normal')),'normal','replace'),
  ])
  self.assertIsNotNone(sub_cycles)
  kinds={n['data']['kind'] for n in sub_cycles['nodes']}
  self.assertTrue({'uv','place2d','unified2d','colorramp','bump'}.issubset(kinds))

  from pxr import UsdGeom
  stage=Usd.Stage.CreateInMemory()
  stage.SetDefaultPrim(UsdGeom.Xform.Define(stage,'/Scene').GetPrim())
  mat=author_materialx(stage,{'name':'CyclesProc','_skipBinding':True},{},procedural_subgraph=sub_cycles)
  self.assertTrue(mat)
  shaders={p.GetName():UsdShade.Shader(p).GetIdAttr().Get() for p in mat.GetPrim().GetChildren() if p.IsA(UsdShade.Shader)}
  self.assertIn('ND_unifiednoise2d_float',shaders.values())
  self.assertIn('ND_bump_vector3',shaders.values())

  oct_tr=_node('Octane3DTransformation',{'Scale':_Sock([0.5,0.5,0.5])})
  oct_noise=_node('OctaneNoiseTexture',{'Transform':_Sock(link=_Link(oct_tr,'Out')),'Octaves':_Sock(3.0),'Omega':_Sock(0.5)})
  oct_grad=_node('OctaneGradient',{'Input':_Sock(link=_Link(oct_noise,'Out')),'Start value':_Sock([0.0,0.0,0.0]),'End value':_Sock([1.0,0.5,0.2])})
  sub_oct=extract([(_Sock(link=_Link(oct_grad,'Out')),'base_color','replace')])
  self.assertIsNotNone(sub_oct)
  oct_kinds={n['data']['kind'] for n in sub_oct['nodes']}
  self.assertTrue({'unified3d','colorramp'}.issubset(oct_kinds))
