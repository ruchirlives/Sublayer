import unittest
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

from sp_materials import build_sp_material_layer


class SubstancePainterMaterialsTest(unittest.TestCase):
    def test_build_sp_material_layer_matches_material_and_authors_dual_context(self):
        stage = Usd.Stage.CreateInMemory()
        UsdGeom.Xform.Define(stage, "/Scene")
        mesh = UsdGeom.Mesh.Define(stage, "/Scene/Hero")
        mesh.CreatePointsAttr([Gf.Vec3f(0, 0, 0), Gf.Vec3f(1, 0, 0), Gf.Vec3f(0, 1, 0)])
        mesh.CreateFaceVertexCountsAttr([3])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2])

        old_mat = UsdShade.Material.Define(stage, "/Scene/Looks/Armor")
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(old_mat)

        manifest = {
            "schema": "sublayer.sp.manifest.v1",
            "textureSets": {
                "Armor": {
                    "channels": {
                        "baseColor": "C:/Textures/Armor_BaseColor.<UDIM>.png",
                        "roughness": "C:/Textures/Armor_Roughness.<UDIM>.png",
                        "metallic": "C:/Textures/Armor_Metallic.<UDIM>.png",
                        "normal": "C:/Textures/Armor_Normal.<UDIM>.png",
                    },
                    "udim": True,
                }
            },
        }

        result = build_sp_material_layer(stage, manifest)
        self.assertEqual(result["report"]["textureSetCount"], 1)
        self.assertEqual(result["report"]["boundPrimCount"], 1)
        self.assertEqual(result["report"]["unmatchedTextureSets"], [])

        # Compose the sparse layer over the upstream stage and verify bindings
        edit_layer = Sdf.Layer.CreateAnonymous("sp_edit.usda")
        self.assertTrue(edit_layer.ImportFromString(result["layer"]))
        composed = Usd.Stage.CreateInMemory()
        composed.GetRootLayer().subLayerPaths.append(edit_layer.identifier)
        composed.GetRootLayer().subLayerPaths.append(stage.GetRootLayer().identifier)

        hero = composed.GetPrimAtPath("/Scene/Hero")
        bound_mat, _ = UsdShade.MaterialBindingAPI(hero).ComputeBoundMaterial()
        self.assertEqual(str(bound_mat.GetPath()), "/Scene/Looks/SP_Armor")

        mtlx_surf, _, _ = bound_mat.ComputeSurfaceSource("mtlx")
        self.assertEqual(mtlx_surf.GetIdAttr().Get(), "ND_standard_surface_surfaceshader")
        prev_surf, _, _ = bound_mat.ComputeSurfaceSource()
        self.assertEqual(prev_surf.GetIdAttr().Get(), "UsdPreviewSurface")


if __name__ == "__main__":
    unittest.main()
