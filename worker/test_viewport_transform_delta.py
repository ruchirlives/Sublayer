"""Transform edits must not enter the RTX material reference shortcut."""
import unittest
import usd_worker  # Configure the upstream USD runtime before importing pxr.
from pxr import Usd, UsdGeom, UsdShade, Gf, Sdf
from ovrtx_material_delta import plan


class TransformDeltaTests(unittest.TestCase):
    def fixture(self):
        base = Usd.Stage.CreateInMemory()
        base.SetDefaultPrim(UsdGeom.Xform.Define(base, "/Scene").GetPrim())
        shape = UsdGeom.Xform.Define(base, "/Scene/Object")
        shape.AddTranslateOp().Set(Gf.Vec3d(0))
        shader = UsdShade.Shader.Define(base, "/Scene/Material/Shader")
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.5)
        layer = base.Flatten()
        new_layer = Sdf.Layer.CreateAnonymous()
        new_layer.TransferContent(layer)
        return layer, Usd.Stage.Open(new_layer)

    def test_changed_transform_fails_closed(self):
        base, edited = self.fixture()
        edited.GetPrimAtPath("/Scene/Object").GetAttribute("xformOp:translate").Set(Gf.Vec3d(1,0,0))
        self.assertIsNone(plan(base, edited.GetRootLayer()))

    def test_added_transform_fails_closed(self):
        base, edited = self.fixture()
        UsdGeom.Xformable(edited.GetPrimAtPath("/Scene/Object")).AddScaleOp().Set(Gf.Vec3f(2))
        self.assertIsNone(plan(base, edited.GetRootLayer()))

    def test_removed_transform_fails_closed(self):
        base, edited = self.fixture()
        prim = edited.GetPrimAtPath("/Scene/Object")
        prim.RemoveProperty("xformOp:translate")
        prim.RemoveProperty("xformOpOrder")
        self.assertIsNone(plan(base, edited.GetRootLayer()))

    def test_material_changes_keep_incremental_path_when_transforms_are_unchanged(self):
        base, edited = self.fixture()
        edited.GetPrimAtPath("/Scene/Material/Shader").GetAttribute("inputs:roughness").Set(0.9)
        delta = plan(base, edited.GetRootLayer())
        self.assertIsNotNone(delta)
        self.assertIsNone(delta.GetAttributeAtPath("/Scene/Object.xformOp:translate"))
