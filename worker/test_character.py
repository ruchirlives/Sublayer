import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pxr import Gf, Usd, UsdGeom, UsdSkel

from usd_graph import compile_graph, export_character_body, inspect_stage_skeletons
from hair_sim import _build_skel_context, _triangulate_mesh


class CharacterNodeTests(unittest.TestCase):
    def _create_base_character(self, path):
        stage = Usd.Stage.CreateNew(path)
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        sr = UsdSkel.Root.Define(stage, "/CharRoot")
        stage.SetDefaultPrim(sr.GetPrim())

        skel = UsdSkel.Skeleton.Define(stage, "/CharRoot/Skeleton")
        skel.CreateJointsAttr(["root", "arm"])
        skel.CreateBindTransformsAttr([
            Gf.Matrix4d(1.0),
            Gf.Matrix4d(1.0).SetTranslate(Gf.Vec3d(0.0, 1.0, 0.0)),
        ])
        skel.CreateRestTransformsAttr([
            Gf.Matrix4d(1.0),
            Gf.Matrix4d(1.0).SetTranslate(Gf.Vec3d(0.0, 1.0, 0.0)),
        ])

        anim = UsdSkel.Animation.Define(stage, "/CharRoot/WalkAnim")
        anim.CreateJointsAttr(["root", "arm"])
        anim.CreateTranslationsAttr().Set([Gf.Vec3f(0, 0, 0), Gf.Vec3f(0, 1, 0)], 1.0)
        anim.CreateRotationsAttr().Set([Gf.Quatf(1, 0, 0, 0), Gf.Quatf(1, 0, 0, 0)], 1.0)
        anim.CreateScalesAttr().Set([Gf.Vec3h(1, 1, 1), Gf.Vec3h(1, 1, 1)], 1.0)

        # Frame 2: rotate arm 90 deg around Z
        anim.CreateTranslationsAttr().Set([Gf.Vec3f(0, 0, 0), Gf.Vec3f(0, 1, 0)], 2.0)
        anim.CreateRotationsAttr().Set(
            [Gf.Quatf(1, 0, 0, 0), Gf.Quatf(0.70710677, 0.0, 0.0, 0.70710677)], 2.0
        )
        anim.CreateScalesAttr().Set([Gf.Vec3h(1, 1, 1), Gf.Vec3h(1, 1, 1)], 2.0)
        UsdSkel.BindingAPI.Apply(skel.GetPrim()).CreateAnimationSourceRel().SetTargets([anim.GetPath()])

        mesh = UsdGeom.Mesh.Define(stage, "/CharRoot/BodyMesh")
        mesh.CreatePointsAttr([
            Gf.Vec3f(0, 1, 0),
            Gf.Vec3f(1, 1, 0),
            Gf.Vec3f(1, 2, 0),
        ])
        mesh.CreateFaceVertexCountsAttr([3])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2])
        b = UsdSkel.BindingAPI.Apply(mesh.GetPrim())
        b.CreateSkeletonRel().SetTargets([skel.GetPath()])
        b.CreateJointIndicesPrimvar(False, 1).Set([1, 1, 1])
        b.CreateJointWeightsPrimvar(False, 1).Set([1.0, 1.0, 1.0])

        stage.SetStartTimeCode(1.0)
        stage.SetEndTimeCode(2.0)
        stage.GetRootLayer().Save()

    def _create_clothing(self, path):
        stage = Usd.Stage.CreateNew(path)
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        sr = UsdSkel.Root.Define(stage, "/ClothRoot")
        stage.SetDefaultPrim(sr.GetPrim())

        dup_skel = UsdSkel.Skeleton.Define(stage, "/ClothRoot/DupSkeleton")
        dup_skel.CreateJointsAttr(["root", "arm"])
        dup_skel.CreateBindTransformsAttr([
            Gf.Matrix4d(1.0),
            Gf.Matrix4d(1.0).SetTranslate(Gf.Vec3d(0.0, 1.0, 0.0)),
        ])
        dup_skel.CreateRestTransformsAttr([
            Gf.Matrix4d(1.0),
            Gf.Matrix4d(1.0).SetTranslate(Gf.Vec3d(0.0, 1.0, 0.0)),
        ])

        shirt = UsdGeom.Mesh.Define(stage, "/ClothRoot/ShirtMesh")
        shirt.CreatePointsAttr([Gf.Vec3f(1, 1, 0), Gf.Vec3f(2, 1, 0), Gf.Vec3f(1, 2, 0)])
        shirt.CreateFaceVertexCountsAttr([3])
        shirt.CreateFaceVertexIndicesAttr([0, 1, 2])
        b = UsdSkel.BindingAPI.Apply(shirt.GetPrim())
        b.CreateSkeletonRel().SetTargets([dup_skel.GetPath()])
        b.CreateJointIndicesPrimvar(False, 1).Set([1, 1, 1])
        b.CreateJointWeightsPrimvar(False, 1).Set([1.0, 1.0, 1.0])
        stage.GetRootLayer().Save()

    def _create_sword(self, path):
        stage = Usd.Stage.CreateNew(path)
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        root = UsdGeom.Xform.Define(stage, "/PropRoot")
        stage.SetDefaultPrim(root.GetPrim())
        sword = UsdGeom.Xform.Define(stage, "/PropRoot/Sword")
        blade = UsdGeom.Mesh.Define(stage, "/PropRoot/Sword/Blade")
        blade.CreatePointsAttr([Gf.Vec3f(0, 0, 0), Gf.Vec3f(0, 1, 0), Gf.Vec3f(0.1, 0, 0)])
        blade.CreateFaceVertexCountsAttr([3])
        blade.CreateFaceVertexIndicesAttr([0, 1, 2])
        stage.GetRootLayer().Save()

    def test_character_pose_skeleton_binding_joint_attach_and_skinned_collider(self):
        with tempfile.TemporaryDirectory() as tmp:
            char_usd = os.path.join(tmp, "char.usda")
            cloth_usd = os.path.join(tmp, "cloth.usda")
            sword_usd = os.path.join(tmp, "sword.usda")
            self._create_base_character(char_usd)
            self._create_clothing(cloth_usd)
            self._create_sword(sword_usd)

            graph = {
                "dependencies": [
                    {"id": "d-char", "kind": "usd", "path": char_usd},
                    {"id": "d-cloth", "kind": "usd", "path": cloth_usd},
                    {"id": "d-sword", "kind": "usd", "path": sword_usd},
                ],
                "nodes": [
                    {"id": "n-char", "operation": "source", "inputDependencyId": "d-char"},
                    {"id": "n-cloth", "operation": "source", "inputDependencyId": "d-cloth"},
                    {"id": "n-sword", "operation": "source", "inputDependencyId": "d-sword"},
                    {
                        "id": "n-assemble",
                        "operation": "character",
                        "character": {
                            "baseSkeletonPath": "",
                            "animationOverrides": [
                                {
                                    "id": "anim-1",
                                    "enabled": True,
                                    "skeletonPath": "",
                                    "animationPath": "WalkAnim",
                                    "mode": "pose",
                                    "poseTime": 2.0,
                                }
                            ],
                            "skeletonBindings": [
                                {
                                    "id": "skel-1",
                                    "enabled": True,
                                    "targetQuery": "*Cloth*, *Shirt*",
                                    "skeletonPath": "",
                                    "deactivateSourceSkeletons": True,
                                }
                            ],
                            "jointAttachments": [
                                {
                                    "id": "joint-1",
                                    "enabled": True,
                                    "targetQuery": "*Sword*",
                                    "skeletonPath": "",
                                    "jointName": "arm",
                                    "offsetTranslation": [0.5, 0.0, 0.0],
                                    "offsetRotation": [0.0, 0.0, 0.0],
                                    "offsetScale": [1.0, 1.0, 1.0],
                                }
                            ],
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "n-char", "target": "n-assemble"},
                    {"id": "e2", "source": "n-cloth", "target": "n-assemble"},
                    {"id": "e3", "source": "n-sword", "target": "n-assemble"},
                ],
                "targetNodeId": "n-assemble",
            }

            insp = inspect_stage_skeletons({"graph": graph})
            self.assertGreaterEqual(len(insp["skeletons"]), 1)
            self.assertGreaterEqual(len(insp["animations"]), 1)

            stage, alive, _ = compile_graph(graph)
            scene = stage.GetPrimAtPath("/Scene")
            self.assertTrue(scene.IsA(UsdSkel.Root))

            # Duplicate skeleton should be deactivated
            dup_skels = [
                p for p in stage.TraverseAll()
                if p.GetName() == "DupSkeleton"
            ]
            self.assertEqual(len(dup_skels), 1)
            self.assertFalse(dup_skels[0].IsActive())

            # ShirtMesh should be bound to CharRoot/Skeleton
            shirt_prim = next(p for p in stage.Traverse() if p.GetName() == "ShirtMesh")
            shirt_targets = UsdSkel.BindingAPI(shirt_prim).GetSkeletonRel().GetTargets()
            self.assertEqual(len(shirt_targets), 1)
            self.assertTrue(str(shirt_targets[0]).endswith("/Skeleton"))

            # Sword Xform should be attached to arm (at (0,1,0) rotated +90 deg Z) with offset (0.5,0,0) -> (0, 1.5, 0)
            sword_prim = next(p for p in stage.Traverse() if p.GetName() == "Sword")
            xc = UsdGeom.XformCache(1.0)
            sword_pos = xc.GetLocalToWorldTransform(sword_prim).Transform(Gf.Vec3d(0, 0, 0))
            self.assertAlmostEqual(sword_pos[0], 0.0, places=4)
            self.assertAlmostEqual(sword_pos[1], 1.5, places=4)
            self.assertAlmostEqual(sword_pos[2], 0.0, places=4)

            # Hair collider skinning helper should evaluate posed/skinned points at frame 1
            import numpy as np
            skel_ctx = _build_skel_context(stage)
            body_prim = next(p for p in stage.Traverse() if p.GetName() == "BodyMesh")
            pts_np, tris_np = _triangulate_mesh(UsdGeom.Mesh(body_prim), 1.0, xc, np, skel_ctx=skel_ctx)
            # Point (1, 1, 0) rotated +90 deg around Z at pivot (0, 1, 0) -> (0, 2, 0)
            self.assertAlmostEqual(float(pts_np[1][0]), 0.0, places=4)
            self.assertAlmostEqual(float(pts_np[1][1]), 2.0, places=4)

            # Export animated body USD and round-trip a simulated cloth cache back
            exported_body = os.path.join(tmp, "exported_body.usda")
            exp_res = export_character_body({"graph": graph, "outputPath": exported_body})
            self.assertTrue(os.path.isfile(exported_body))
            self.assertGreaterEqual(exp_res["meshCount"], 1)
            exp_stage = Usd.Stage.Open(exported_body)
            exp_body_mesh = next(p for p in exp_stage.Traverse() if p.IsA(UsdGeom.Mesh) and "BodyMesh" in p.GetName())
            exp_pts = UsdGeom.Mesh(exp_body_mesh).GetPointsAttr().Get(1.0)
            self.assertAlmostEqual(float(exp_pts[1][0]), 0.0, places=4)
            self.assertAlmostEqual(float(exp_pts[1][1]), 2.0, places=4)

            # Create a Z-up cm cloth cache and attach it to character root
            cloth_cache_usd = os.path.join(tmp, "md_cloth_cache.usda")
            cs = Usd.Stage.CreateNew(cloth_cache_usd)
            UsdGeom.SetStageUpAxis(cs, UsdGeom.Tokens.z)
            UsdGeom.SetStageMetersPerUnit(cs, 1.0)
            cr = UsdGeom.Xform.Define(cs, "/MDCloth")
            cs.SetDefaultPrim(cr.GetPrim())
            cm = UsdGeom.Mesh.Define(cs, "/MDCloth/Dress")
            cm.CreatePointsAttr().Set([Gf.Vec3f(0, 0, 100), Gf.Vec3f(10, 0, 100), Gf.Vec3f(0, 10, 100)], 1.0)
            cm.CreateFaceVertexCountsAttr([3])
            cm.CreateFaceVertexIndicesAttr([0, 1, 2])
            cs.SetStartTimeCode(1.0)
            cs.SetEndTimeCode(2.0)
            cs.GetRootLayer().Save()

            graph["dependencies"].append({"id": "d-md-cache", "kind": "usd", "path": cloth_cache_usd})
            graph["nodes"][3]["character"]["clothCaches"] = [
                {
                    "id": "cc-1",
                    "enabled": True,
                    "dependencyId": "d-md-cache",
                    "attachToRoot": True,
                    "axis": "Z",
                    "frontAxis": "preserve",
                    "scale": 0.01,
                    "timeOffset": 0.0,
                }
            ]
            stage_with_cache, _, _ = compile_graph(graph)
            dress_prim = next(p for p in stage_with_cache.Traverse() if p.GetName() == "Dress")
            self.assertIn("CharRoot/ClothCache_0", str(dress_prim.GetPath()))
            dress_world = UsdGeom.XformCache(1.0).GetLocalToWorldTransform(dress_prim).Transform(Gf.Vec3d(0, 0, 100))
            self.assertAlmostEqual(dress_world[1], 1.0, places=4)


if __name__ == "__main__":
    unittest.main()
