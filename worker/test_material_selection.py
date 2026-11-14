"""Headless checks for selection using real upstream USD binding resolution."""
import unittest
import usd_worker
from usd_worker import ViewportSelection
from pxr import Sdf,Usd,UsdGeom,UsdShade


class MaterialSelectionTests(unittest.TestCase):
    def setUp(self):
        self.stage=Usd.Stage.CreateInMemory()
        self.world=UsdGeom.Xform.Define(self.stage,'/World')
        self.red=UsdShade.Material.Define(self.stage,'/Looks/Red')
        self.blue=UsdShade.Material.Define(self.stage,'/Looks/Blue')
        self.unused=UsdShade.Material.Define(self.stage,'/Looks/Unused')
        self.a=UsdGeom.Mesh.Define(self.stage,'/World/A')
        self.b=UsdGeom.Mesh.Define(self.stage,'/World/B')
        UsdShade.MaterialBindingAPI.Apply(self.world.GetPrim()).Bind(self.red)
        self.selection=ViewportSelection(self.stage)
        self.addCleanup(self.selection.close)

    def selected(self,material):
        return self.selection.resolve([str(material.GetPath())])[0]

    def test_inheritance_override_and_no_source_changes(self):
        UsdShade.MaterialBindingAPI.Apply(self.a.GetPrim()).Bind(self.blue)
        before=self.stage.GetRootLayer().ExportToString()
        self.assertEqual(self.selected(self.red),['/World/B'])
        self.assertEqual(self.selected(self.blue),['/World/A'])
        self.assertEqual(self.selected(self.unused),[])
        self.assertEqual(self.selection.resolve(['/World/A'])[0],['/World/A'])
        self.assertEqual(self.stage.GetRootLayer().ExportToString(),before)

    def test_collection_binding_and_face_subset_select_whole_mesh(self):
        collection=Usd.CollectionAPI.Apply(self.world.GetPrim(),'blueObjects')
        collection.CreateIncludesRel().SetTargets([self.a.GetPath()])
        UsdShade.MaterialBindingAPI.Apply(self.world.GetPrim()).Bind(collection,self.blue,'blueObjects')
        subset=UsdGeom.Subset.Define(self.stage,'/World/B/BlueFaces')
        subset.CreateElementTypeAttr(UsdGeom.Tokens.face)
        subset.CreateFamilyNameAttr('materialBind');subset.CreateIndicesAttr([0])
        UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(self.blue)
        selected,info=self.selection.resolve([str(self.blue.GetPath())])
        self.assertEqual(selected,['/World/A','/World/B'])
        self.assertEqual(info,{'materialCount':1,'meshCount':2})

    def test_instance_proxies_keep_instance_sharing(self):
        source=self.stage.CreateClassPrim('/Library')
        mesh=UsdGeom.Mesh.Define(self.stage,'/Library/Mesh')
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(self.blue)
        for name in ('One','Two'):
            instance=UsdGeom.Xform.Define(self.stage,'/'+name).GetPrim()
            instance.GetReferences().AddInternalReference(source.GetPath())
            instance.SetInstanceable(True)
        before=self.stage.GetRootLayer().ExportToString()
        self.assertEqual(self.selected(self.blue),['/One/Mesh','/Two/Mesh'])
        self.assertTrue(self.stage.GetPrimAtPath('/One/Mesh').IsInstanceProxy())
        self.assertEqual(self.stage.GetPrimAtPath('/One').GetPrototype(),self.stage.GetPrimAtPath('/Two').GetPrototype())
        self.assertEqual(self.stage.GetRootLayer().ExportToString(),before)

    def test_purpose_bindings_and_cached_results_invalidate_on_edit(self):
        api=UsdShade.MaterialBindingAPI.Apply(self.a.GetPrim())
        api.Bind(self.blue,materialPurpose=UsdShade.Tokens.preview)
        self.assertEqual(self.selected(self.blue),['/World/A'])
        index=self.selection.material_users
        self.assertEqual(self.selected(self.blue),['/World/A'])
        self.assertIs(self.selection.material_users,index)
        api.Bind(self.unused,materialPurpose=UsdShade.Tokens.preview)
        self.assertIsNone(self.selection.material_users)
        self.assertEqual(self.selected(self.blue),[])
        self.assertEqual(self.selected(self.unused),['/World/A'])
        with self.assertRaises(ValueError):self.selection.resolve(['/World/A.points'])


if __name__=='__main__':unittest.main()
