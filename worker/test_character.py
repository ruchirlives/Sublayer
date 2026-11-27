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

    def _create_head_character(self, path):
        stage = Usd.Stage.CreateNew(path)
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        sr = UsdSkel.Root.Define(stage, "/CharRoot")
        stage.SetDefaultPrim(sr.GetPrim())

        skel = UsdSkel.Skeleton.Define(stage, "/CharRoot/Skeleton")
        skel.CreateJointsAttr(["root", "root/neck_01", "root/neck_01/head"])
        skel.CreateBindTransformsAttr([
            Gf.Matrix4d(1.0),
            Gf.Matrix4d(1.0).SetTranslate(Gf.Vec3d(0.0, 1.4, 0.0)),
            Gf.Matrix4d(1.0).SetTranslate(Gf.Vec3d(0.0, 1.6, 0.0)),
        ])
        skel.CreateRestTransformsAttr([
            Gf.Matrix4d(1.0),
            Gf.Matrix4d(1.0).SetTranslate(Gf.Vec3d(0.0, 1.4, 0.0)),
            Gf.Matrix4d(1.0).SetTranslate(Gf.Vec3d(0.0, 0.2, 0.0)),
        ])

        anim = UsdSkel.Animation.Define(stage, "/CharRoot/HeadTurnAnim")
        anim.CreateJointsAttr(["root", "root/neck_01", "root/neck_01/head"])
        # Frame 1: rest pose
        anim.CreateTranslationsAttr().Set([Gf.Vec3f(0, 0, 0), Gf.Vec3f(0, 1.4, 0), Gf.Vec3f(0, 0.2, 0)], 1.0)
        anim.CreateRotationsAttr().Set([Gf.Quatf(1, 0, 0, 0), Gf.Quatf(1, 0, 0, 0), Gf.Quatf(1, 0, 0, 0)], 1.0)
        anim.CreateScalesAttr().Set([Gf.Vec3h(1, 1, 1), Gf.Vec3h(1, 1, 1), Gf.Vec3h(1, 1, 1)], 1.0)
        # Frame 2: head rotated +90 deg around Y (local X -> -Z, local Z -> +X) and shifted +0.1 in X
        anim.CreateTranslationsAttr().Set([Gf.Vec3f(0, 0, 0), Gf.Vec3f(0, 1.4, 0), Gf.Vec3f(0.1, 0.2, 0)], 2.0)
        anim.CreateRotationsAttr().Set(
            [Gf.Quatf(1, 0, 0, 0), Gf.Quatf(1, 0, 0, 0), Gf.Quatf(0.70710677, 0.0, 0.70710677, 0.0)], 2.0
        )
        anim.CreateScalesAttr().Set([Gf.Vec3h(1, 1, 1), Gf.Vec3h(1, 1, 1), Gf.Vec3h(1, 1, 1)], 2.0)
        UsdSkel.BindingAPI.Apply(skel.GetPrim()).CreateAnimationSourceRel().SetTargets([anim.GetPath()])

        # Head scalp triangle at y=1.75 in rest pose, bound to joint 2 ("head")
        head_mesh = UsdGeom.Mesh.Define(stage, "/CharRoot/head_lod0_mesh")
        head_mesh.CreatePointsAttr([
            Gf.Vec3f(-0.1, 1.75, 0.0),
            Gf.Vec3f(0.1, 1.75, 0.0),
            Gf.Vec3f(0.0, 1.75, 0.2),
        ])
        head_mesh.CreateFaceVertexCountsAttr([3])
        head_mesh.CreateFaceVertexIndicesAttr([0, 1, 2])
        b = UsdSkel.BindingAPI.Apply(head_mesh.GetPrim())
        b.CreateSkeletonRel().SetTargets([skel.GetPath()])
        b.CreateJointIndicesPrimvar(False, 1).Set([2, 2, 2])
        b.CreateJointWeightsPrimvar(False, 1).Set([1.0, 1.0, 1.0])

        stage.SetStartTimeCode(1.0)
        stage.SetEndTimeCode(2.0)
        stage.GetRootLayer().Save()

    def _create_groom(self, path, root_name="/GroomRoot", curve_name="HairCurves"):
        stage = Usd.Stage.CreateNew(path)
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        root = UsdGeom.Xform.Define(stage, root_name)
        stage.SetDefaultPrim(root.GetPrim())
        curves = UsdGeom.BasisCurves.Define(stage, f"{root_name}/{curve_name}")
        curves.CreateTypeAttr(UsdGeom.Tokens.linear)
        curves.CreateCurveVertexCountsAttr([2])
        # Authored in character rest space: root sits on scalp at (0.0, 1.75, 0.05), tip extends +0.1 in X
        curves.CreatePointsAttr([
            Gf.Vec3f(0.0, 1.75, 0.05),
            Gf.Vec3f(0.1, 1.75, 0.05),
        ])
        curves.CreateWidthsAttr([0.002, 0.001])
        stage.GetRootLayer().Save()

    def test_character_head_grooms_joint_and_surface_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            char_usd = os.path.join(tmp, "head_char.usda")
            ext_groom_usd = os.path.join(tmp, "ext_hair.usda")
            up_groom_usd = os.path.join(tmp, "up_eyebrows.usda")
            self._create_head_character(char_usd)
            self._create_groom(ext_groom_usd, "/ExtGroom", "HairCurves")
            self._create_groom(up_groom_usd, "/UpGroom", "EyebrowCurves")

            graph = {
                "dependencies": [
                    {"id": "d-char", "kind": "usd", "path": char_usd},
                    {"id": "d-ext-groom", "kind": "usd", "path": ext_groom_usd},
                    {"id": "d-up-groom", "kind": "usd", "path": up_groom_usd},
                ],
                "nodes": [
                    {"id": "n-char", "operation": "source", "inputDependencyId": "d-char"},
                    {"id": "n-up-groom", "operation": "source", "inputDependencyId": "d-up-groom"},
                    {
                        "id": "n-assemble",
                        "operation": "character",
                        "character": {
                            "baseSkeletonPath": "",
                            "animationOverrides": [],
                            "skeletonBindings": [],
                            "jointAttachments": [],
                            "clothCaches": [],
                            "headGrooms": [
                                {
                                    "id": "hg-joint",
                                    "enabled": True,
                                    "sourceMode": "file",
                                    "dependencyId": "d-ext-groom",
                                    "targetQuery": "",
                                    "attachMode": "joint",
                                    "skeletonPath": "",
                                    "headJointName": "",
                                    "preserveRestPosition": True,
                                    "headMeshPath": "",
                                    "axis": "preserve",
                                    "frontAxis": "preserve",
                                    "scale": 1.0,
                                    "offsetTranslation": [0.0, 0.0, 0.0],
                                    "offsetRotation": [0.0, 0.0, 0.0],
                                    "offsetScale": [1.0, 1.0, 1.0],
                                },
                                {
                                    "id": "hg-surface",
                                    "enabled": True,
                                    "sourceMode": "upstream",
                                    "dependencyId": None,
                                    "targetQuery": "*EyebrowCurves*",
                                    "attachMode": "surface",
                                    "skeletonPath": "",
                                    "headJointName": "",
                                    "preserveRestPosition": True,
                                    "headMeshPath": "",
                                    "axis": "preserve",
                                    "frontAxis": "preserve",
                                    "scale": 1.0,
                                    "offsetTranslation": [0.0, 0.0, 0.0],
                                    "offsetRotation": [0.0, 0.0, 0.0],
                                    "offsetScale": [1.0, 1.0, 1.0],
                                },
                            ],
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "n-char", "target": "n-assemble"},
                    {"id": "e2", "source": "n-up-groom", "target": "n-assemble"},
                ],
                "targetNodeId": "n-assemble",
            }

            stage, alive, _ = compile_graph(graph)

            # 1. Rigid head-joint groom (external file, auto-detected 'head', preserveRestPosition=True)
            hair_prim = next(p for p in stage.Traverse() if p.GetName() == "HairCurves")
            xc1 = UsdGeom.XformCache(1.0)
            xc2 = UsdGeom.XformCache(2.0)
            pts_local = UsdGeom.BasisCurves(hair_prim).GetPointsAttr().Get()
            # Frame 1 (rest pose): root stays at (0.0, 1.75, 0.05) - NO double translation!
            r1 = xc1.GetLocalToWorldTransform(hair_prim).Transform(Gf.Vec3d(pts_local[0]))
            self.assertAlmostEqual(r1[0], 0.0, places=4)
            self.assertAlmostEqual(r1[1], 1.75, places=4)
            self.assertAlmostEqual(r1[2], 0.05, places=4)
            # Frame 2: head at (0.1, 1.6, 0.0) rotated +90 deg Y.
            # Offset from head joint (0, 1.6, 0) was (0, 0.15, 0.05) -> rotated +90 deg Y -> (0.05, 0.15, 0.0)
            # Added to head pos (0.1, 1.6, 0.0) -> (0.15, 1.75, 0.0)
            r2 = xc2.GetLocalToWorldTransform(hair_prim).Transform(Gf.Vec3d(pts_local[0]))
            self.assertAlmostEqual(r2[0], 0.15, places=4)
            self.assertAlmostEqual(r2[1], 1.75, places=4)
            self.assertAlmostEqual(r2[2], 0.0, places=4)
            # Tip was at (0.1, 1.75, 0.05) -> offset (0.1, 0.15, 0.05) -> rot +90Y -> (0.05, 0.15, -0.1)
            # Added to (0.1, 1.6, 0) -> (0.15, 1.75, -0.1)
            t2 = xc2.GetLocalToWorldTransform(hair_prim).Transform(Gf.Vec3d(pts_local[1]))
            self.assertAlmostEqual(t2[0], 0.15, places=4)
            self.assertAlmostEqual(t2[1], 1.75, places=4)
            self.assertAlmostEqual(t2[2], -0.1, places=4)

            # 2. Surface-bound groom (upstream EyebrowCurves bound to auto-detected head_lod0_mesh)
            brow_prim = next(p for p in stage.Traverse() if p.GetName() == "EyebrowCurves")
            brow_pts_1 = UsdGeom.BasisCurves(brow_prim).GetPointsAttr().Get(1.0)
            brow_pts_2 = UsdGeom.BasisCurves(brow_prim).GetPointsAttr().Get(2.0)
            b_r1 = xc1.GetLocalToWorldTransform(brow_prim).Transform(Gf.Vec3d(brow_pts_1[0]))
            self.assertAlmostEqual(b_r1[0], 0.0, places=4)
            self.assertAlmostEqual(b_r1[1], 1.75, places=4)
            self.assertAlmostEqual(b_r1[2], 0.05, places=4)
            b_r2 = xc2.GetLocalToWorldTransform(brow_prim).Transform(Gf.Vec3d(brow_pts_2[0]))
            self.assertAlmostEqual(b_r2[0], 0.15, places=4)
            self.assertAlmostEqual(b_r2[1], 1.75, places=4)
            self.assertAlmostEqual(b_r2[2], 0.0, places=4)
            b_t2 = xc2.GetLocalToWorldTransform(brow_prim).Transform(Gf.Vec3d(brow_pts_2[1]))
            self.assertAlmostEqual(b_t2[0], 0.15, places=4)
            self.assertAlmostEqual(b_t2[1], 1.75, places=4)
            self.assertAlmostEqual(b_t2[2], -0.1, places=4)

            # 3. inspect_stage_skeletons reports isCurves=True on upstream curves
            insp = inspect_stage_skeletons({"graph": graph})
            curves_prims = [p for p in insp["prims"] if p.get("isCurves")]
            self.assertTrue(any("EyebrowCurves" in p["path"] for p in curves_prims))

            # 4. export_character_body strips headGrooms cleanly
            exported_body = os.path.join(tmp, "body_no_grooms.usda")
            exp_res = export_character_body({"graph": graph, "outputPath": exported_body})
            self.assertGreaterEqual(exp_res["meshCount"], 1)
            exp_stage = Usd.Stage.Open(exported_body)
            self.assertFalse(any(p.IsA(UsdGeom.BasisCurves) for p in exp_stage.Traverse()))

    def test_upstream_groom_unbranched_path_isolation_and_scale(self):
        with tempfile.TemporaryDirectory() as tmp:
            char_usd = os.path.join(tmp, "head_char.usda")
            multi_groom_usd = os.path.join(tmp, "multi_root_groom.usda")
            self._create_head_character(char_usd)

            # Multi-root groom file (like an Alembic with dummy head sphere + hair curves,
            # defaulting to metersPerUnit=0.01 cm while points are in meters)
            gs = Usd.Stage.CreateNew(multi_groom_usd)
            UsdGeom.SetStageUpAxis(gs, UsdGeom.Tokens.y)
            UsdGeom.SetStageMetersPerUnit(gs, 0.01)
            dummy_head = UsdGeom.Mesh.Define(gs, "/DummyHeadSphere")
            dummy_head.CreatePointsAttr([Gf.Vec3f(0, 0, 0), Gf.Vec3f(0.1, 0, 0), Gf.Vec3f(0, 0.1, 0)])
            dummy_head.CreateFaceVertexCountsAttr([3])
            dummy_head.CreateFaceVertexIndicesAttr([0, 1, 2])
            # Nested non-matched parent + child curves (reproduces expired child prim on parent deactivation)
            eyebrow_group = UsdGeom.Xform.Define(gs, "/Eyebrow__Curve_")
            eyebrow_child = UsdGeom.BasisCurves.Define(gs, "/Eyebrow__Curve_/BezierCurve_002")
            eyebrow_child.CreateTypeAttr(UsdGeom.Tokens.linear)
            eyebrow_child.CreateCurveVertexCountsAttr([2])
            eyebrow_child.CreatePointsAttr([Gf.Vec3f(0.0, 0.0, 0.0), Gf.Vec3f(0.05, 0.0, 0.0)])
            hair = UsdGeom.BasisCurves.Define(gs, "/Hair_Line__Hair_01")
            hair.CreateTypeAttr(UsdGeom.Tokens.linear)
            hair.CreateCurveVertexCountsAttr([2])
            hair.CreatePointsAttr([Gf.Vec3f(0.0, 0.0, 0.0), Gf.Vec3f(0.0, 0.2, 0.0)])
            hair.CreateWidthsAttr([0.002, 0.001])
            gs.GetRootLayer().Save()

            graph = {
                "dependencies": [
                    {"id": "d-char", "kind": "usd", "path": char_usd},
                    {"id": "d-groom", "kind": "usd", "path": multi_groom_usd},
                ],
                "nodes": [
                    {"id": "n-char", "operation": "source", "inputDependencyId": "d-char"},
                    {"id": "n-groom", "operation": "source", "inputDependencyId": "d-groom"},
                    {
                        "id": "n-assemble",
                        "operation": "character",
                        "character": {
                            "baseSkeletonPath": "",
                            "animationOverrides": [],
                            "skeletonBindings": [],
                            "jointAttachments": [],
                            "clothCaches": [],
                            "headGrooms": [
                                {
                                    "id": "hg-iso",
                                    "enabled": True,
                                    "sourceMode": "upstream",
                                    "dependencyId": None,
                                    "targetQuery": "/Scene/_OrchFlowAsset/Hair_Line__Hair_01",
                                    "attachMode": "joint",
                                    "skeletonPath": "",
                                    "headJointName": "",
                                    "preserveRestPosition": False,
                                    "headMeshPath": "",
                                    "axis": "preserve",
                                    "frontAxis": "preserve",
                                    "scale": 100.0,
                                    "offsetTranslation": [0.0, 0.0, 0.0],
                                    "offsetRotation": [0.0, 0.0, 0.0],
                                    "offsetScale": [1.0, 1.0, 1.0],
                                },
                            ],
                        },
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "n-char", "target": "n-assemble"},
                    {"id": "e2", "source": "n-groom", "target": "n-assemble"},
                ],
                "targetNodeId": "n-assemble",
            }

            stage, _, _ = compile_graph(graph)

            # DummyHeadSphere in the groom branch must be deactivated
            dummy_prims = [p for p in stage.TraverseAll() if p.GetName() == "DummyHeadSphere"]
            self.assertEqual(len(dummy_prims), 1)
            self.assertFalse(dummy_prims[0].IsActive())

            # Character head mesh must still be active
            head_meshes = [p for p in stage.Traverse() if p.GetName() == "head_lod0_mesh"]
            self.assertEqual(len(head_meshes), 1)
            self.assertTrue(head_meshes[0].IsActive())

            # Hair prim must match, snap to head joint at (0, 1.6, 0), and have scale 0.01 * 100 = 1.0
            hair_prim = next(p for p in stage.Traverse() if p.GetName() == "Hair_Line__Hair_01")
            pts = UsdGeom.BasisCurves(hair_prim).GetPointsAttr().Get()
            xc1 = UsdGeom.XformCache(1.0)
            p0 = xc1.GetLocalToWorldTransform(hair_prim).Transform(Gf.Vec3d(pts[0]))
            p1 = xc1.GetLocalToWorldTransform(hair_prim).Transform(Gf.Vec3d(pts[1]))
            self.assertAlmostEqual(p0[0], 0.0, places=4)
            self.assertAlmostEqual(p0[1], 1.6, places=4)
            self.assertAlmostEqual(p0[2], 0.0, places=4)
            self.assertAlmostEqual(p1[1], 1.8, places=4)

            # With no rules targeting the connected groom input, pass-through is disabled
            # (base character stays active, unreferenced groom branch is inactive),
            # while inspect_stage_skeletons still discovers its curves.
            graph_no_rules = dict(graph)
            graph_no_rules["nodes"] = [
                {**n, "character": {**n["character"], "headGrooms": []}}
                if n["id"] == "n-assemble"
                else n
                for n in graph["nodes"]
            ]
            stage_no_rules, _, _ = compile_graph(graph_no_rules)
            self.assertFalse(any(p.GetName() == "Hair_Line__Hair_01" for p in stage_no_rules.Traverse()))
            self.assertFalse(any(p.GetName() == "DummyHeadSphere" for p in stage_no_rules.Traverse()))
            self.assertTrue(any(p.GetName() == "head_lod0_mesh" for p in stage_no_rules.Traverse()))
            insp = inspect_stage_skeletons({"graph": graph_no_rules})
            self.assertTrue(any("Hair_Line__Hair_01" in p["path"] for p in insp["prims"] if p.get("isCurves")))

            # Fast delta groom transform update + gizmo translation resolution
            from usd_graph import apply_live_groom_xforms, groom_xform_only_changes
            from viewport_bounds import transform_gizmo_translation
            import copy

            live_grooms = {}
            stage_live, _, _ = compile_graph(graph, live_grooms=live_grooms)
            self.assertIn(("n-assemble", "hg-iso"), live_grooms)
            next_graph = copy.deepcopy(graph)
            next_graph["nodes"][2]["character"]["headGrooms"][0]["offsetTranslation"] = [0.0, 0.25, 0.0]
            changed = groom_xform_only_changes(graph, next_graph, live_grooms)
            self.assertIsNotNone(changed)
            updates = apply_live_groom_xforms(changed, live_grooms)
            self.assertEqual(len(updates), 1)
            hair_live = next(p for p in stage_live.Traverse() if p.GetName() == "Hair_Line__Hair_01")
            pts_live = UsdGeom.BasisCurves(hair_live).GetPointsAttr().Get()
            p0_moved = UsdGeom.XformCache(1.0).GetLocalToWorldTransform(hair_live).Transform(Gf.Vec3d(pts_live[0]))
            self.assertAlmostEqual(p0_moved[1], 1.85, places=4)
            gt, gv = transform_gizmo_translation(next_graph, "groom:n-assemble:hg-iso")
            self.assertEqual(gt, "groom:n-assemble:hg-iso")
            self.assertEqual(gv, [0.0, 0.25, 0.0])


if __name__ == "__main__":
    unittest.main()
