import pathlib,tempfile,unittest
import usd_worker
from pxr import Usd,UsdGeom
class SceneOverviewTests(unittest.TestCase):
 def test_hierarchy_children_include_instance_proxies_without_source_edits(self):
  with tempfile.TemporaryDirectory() as folder:
   asset=pathlib.Path(folder)/'asset.usda';s=Usd.Stage.CreateNew(str(asset));x=UsdGeom.Xform.Define(s,'/Asset');s.SetDefaultPrim(x.GetPrim());UsdGeom.Sphere.Define(s,'/Asset/Sphere');s.GetRootLayer().Save()
   root=pathlib.Path(folder)/'scene.usda';stage=Usd.Stage.CreateNew(str(root));p=stage.DefinePrim('/Instance');p.GetReferences().AddReference(str(asset));p.SetInstanceable(True);stage.GetRootLayer().Save();before=root.read_bytes()
   result=usd_worker.dispatch({'operation':'scene_children','path':str(root),'reload':True});self.assertTrue(result['children'][0]['instance'])
   child=usd_worker.dispatch({'operation':'scene_children','path':str(root),'parent':'/Instance'})['children'][0];self.assertTrue(child['instanceProxy']);self.assertEqual(child['path'],'/Instance/Sphere');self.assertEqual(root.read_bytes(),before)
   with self.assertRaises(ValueError):usd_worker.dispatch({'operation':'scene_children','path':str(root),'parent':'relative'})
 def test_graph_hierarchy_uses_same_composed_paths_as_preview(self):
  path=pathlib.Path(__file__).resolve().parents[1]/'samples/layered.usda'
  graph={'targetNodeId':'out','nodes':[{'id':'source','operation':'source','inputDependencyId':'usd'},{'id':'out','operation':'output'}],'edges':[{'id':'edge','source':'source','target':'out'}],'dependencies':[{'id':'usd','path':str(path)}]}
  result=usd_worker.dispatch({'operation':'scene_children','graph':graph});self.assertEqual(result['children'][0]['path'],'/Scene')
 def test_storm_selection_highlights_and_clears_without_mutating_stage(self):
  if not usd_worker.dispatch({'operation':'capabilities'})['viewport']:self.skipTest('Requires imaging')
  path=pathlib.Path(__file__).resolve().parents[1]/'samples/layered.usda';before=path.read_bytes();r={'operation':'render','path':str(path),'renderer':'HdStormRendererPlugin','width':320,'height':240}
  plain=usd_worker.dispatch(r)['image'];highlight=usd_worker.dispatch({**r,'selection':['/World']})['image'];self.assertNotEqual(plain,highlight);self.assertEqual(plain,usd_worker.dispatch({**r,'selection':[]})['image']);self.assertEqual(path.read_bytes(),before)
