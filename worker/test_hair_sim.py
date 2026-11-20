import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usd_worker  # noqa: E402, F401


class HairSimTests(unittest.TestCase):
    def _make_stage(self):
        from pxr import Gf, Sdf, Usd, UsdGeom, Vt

        layer = Sdf.Layer.CreateAnonymous("hair_sim_test.usda")
        stage = Usd.Stage.Open(layer)
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        root = UsdGeom.Xform.Define(stage, "/Scene")
        stage.SetDefaultPrim(root.GetPrim())

        # Create 2 horizontal hair strands above a sphere collider centered at (0, -0.5, 0)
        curves = UsdGeom.BasisCurves.Define(stage, "/Scene/Hair")
        curves.CreateTypeAttr(UsdGeom.Tokens.linear)
        curves.CreateCurveVertexCountsAttr(Vt.IntArray([6, 6]))
        pts = []
        for z in (-0.1, 0.1):
            for i in range(6):
                pts.append(Gf.Vec3f(float(i) * 0.15, 0.2, z))
        curves.CreatePointsAttr(Vt.Vec3fArray(pts))
        curves.CreateWidthsAttr(Vt.FloatArray([0.002]))
        curves.SetWidthsInterpolation(UsdGeom.Tokens.constant)

        # Create a box/octahedron-like sphere collider mesh below the strands
        mesh = UsdGeom.Mesh.Define(stage, "/Scene/HeadCollider")
        # Simple cube spanning [-1, 1] in X/Z and [-1.5, 0.0] in Y
        m_pts = [
            Gf.Vec3f(-1.0, -1.5, -1.0),
            Gf.Vec3f(1.0, -1.5, -1.0),
            Gf.Vec3f(1.0, 0.0, -1.0),
            Gf.Vec3f(-1.0, 0.0, -1.0),
            Gf.Vec3f(-1.0, -1.5, 1.0),
            Gf.Vec3f(1.0, -1.5, 1.0),
            Gf.Vec3f(1.0, 0.0, 1.0),
            Gf.Vec3f(-1.0, 0.0, 1.0),
        ]
        mesh.CreatePointsAttr(Vt.Vec3fArray(m_pts))
        mesh.CreateFaceVertexCountsAttr(Vt.IntArray([4, 4, 4, 4, 4, 4]))
        mesh.CreateFaceVertexIndicesAttr(
            Vt.IntArray([
                0, 3, 2, 1,
                4, 5, 6, 7,
                0, 1, 5, 4,
                2, 3, 7, 6,
                1, 2, 6, 5,
                0, 4, 7, 3,
            ])
        )
        return stage, curves

    def test_static_hair_node_untouched_points(self):
        from pxr import Usd
        from usd_graph import apply_hair

        stage, curves = self._make_stage()
        apply_hair(stage, {"widthMode": "multiply", "width": 2.0, "simEnabled": False})
        self.assertEqual(curves.GetPointsAttr().GetNumTimeSamples(), 0)
        self.assertAlmostEqual(float(curves.GetWidthsAttr().Get()[0]), 0.004, places=6)

    def test_gpu_warp_hair_simulation_and_collision(self):
        from pxr import Usd
        from usd_graph import apply_hair

        stage, curves = self._make_stage()
        cfg = {
            "targetQuery": "",
            "widthMode": "multiply",
            "width": 1.0,
            "taper": 0.5,
            "rootScale": 1.0,
            "simEnabled": True,
            "simMode": "animated",
            "colliderQuery": "/Scene/HeadCollider",
            "startFrame": 1,
            "endFrame": 12,
            "substeps": 4,
            "iterations": 8,
            "stiffness": 0.95,
            "bendStiffness": 0.2,
            "damping": 0.04,
            "gravity": 9.81,
            "wind": [0.0, 0.0, 0.0],
            "collisionMargin": 0.02,
            "friction": 0.25,
        }
        apply_hair(stage, cfg)

        pts_attr = curves.GetPointsAttr()
        self.assertEqual(pts_attr.GetNumTimeSamples(), 12)

        pts_f1 = pts_attr.Get(Usd.TimeCode(1.0))
        pts_f12 = pts_attr.Get(Usd.TimeCode(12.0))
        self.assertEqual(len(pts_f12), 12)

        # Roots stay pinned at x=0, y=0.2
        self.assertAlmostEqual(float(pts_f12[0][0]), 0.0, places=5)
        self.assertAlmostEqual(float(pts_f12[0][1]), 0.2, places=5)
        self.assertAlmostEqual(float(pts_f12[6][0]), 0.0, places=5)
        self.assertAlmostEqual(float(pts_f12[6][1]), 0.2, places=5)

        # Tips should have drooped under gravity, but stopped at or above collider top (y >= 0.0)
        self.assertLess(float(pts_f12[5][1]), float(pts_f1[5][1]))
        for p in pts_f12:
            self.assertGreaterEqual(float(p[1]), -0.01)

        # Repeat call should hit cache cleanly
        apply_hair(stage, cfg)
        self.assertEqual(pts_attr.GetNumTimeSamples(), 12)

    def test_large_groom_performance_under_5s(self):
        import time
        from pxr import Gf, UsdGeom, Vt
        from usd_graph import apply_hair

        stage, _ = self._make_stage()
        dense = UsdGeom.BasisCurves.Define(stage, "/Scene/DenseHair")
        dense.CreateTypeAttr(UsdGeom.Tokens.linear)
        n_strands = 5000
        pts_per = 12
        dense.CreateCurveVertexCountsAttr(Vt.IntArray([pts_per] * n_strands))
        pts = []
        for s in range(n_strands):
            x0 = ((s % 100) - 50) * 0.01
            z0 = ((s // 100) - 25) * 0.01
            for k in range(pts_per):
                pts.append(Gf.Vec3f(x0 + k * 0.02, 0.25, z0))
        dense.CreatePointsAttr(Vt.Vec3fArray(pts))

        cfg = {
            "targetQuery": "/Scene/DenseHair",
            "widthMode": "multiply",
            "width": 1.0,
            "taper": 0.0,
            "rootScale": 1.0,
            "simEnabled": True,
            "simMode": "animated",
            "colliderQuery": "/Scene/HeadCollider",
            "startFrame": 1,
            "endFrame": 48,
            "substeps": 4,
            "iterations": 8,
            "stiffness": 0.95,
            "bendStiffness": 0.4,
            "damping": 0.04,
            "gravity": 9.81,
            "wind": [0.0, 0.0, 0.0],
            "collisionMargin": 0.01,
            "friction": 0.25,
        }
        t0 = time.perf_counter()
        apply_hair(stage, cfg)
        elapsed = time.perf_counter() - t0
        self.assertEqual(dense.GetPointsAttr().GetNumTimeSamples(), 48)
        self.assertLess(elapsed, 10.0, f"60k-vertex 48-frame sim took too long: {elapsed:.2f}s")

    def test_static_settle_mode_writes_default_points(self):
        from usd_graph import apply_hair

        stage, curves = self._make_stage()
        orig_pts = curves.GetPointsAttr().Get()
        cfg = {
            "targetQuery": "",
            "widthMode": "multiply",
            "width": 1.0,
            "taper": 0.0,
            "rootScale": 1.0,
            "simEnabled": True,
            "simMode": "static",
            "settleSteps": 24,
            "colliderQuery": "/Scene/HeadCollider",
            "substeps": 4,
            "iterations": 8,
            "stiffness": 0.95,
            "bendStiffness": 0.2,
            "damping": 0.04,
            "gravity": 9.81,
            "wind": [0.0, 0.0, 0.0],
            "collisionMargin": 0.02,
            "friction": 0.25,
        }
        apply_hair(stage, cfg)
        pts_attr = curves.GetPointsAttr()
        self.assertEqual(pts_attr.GetNumTimeSamples(), 0)
        settled_pts = pts_attr.Get()
        self.assertLess(float(settled_pts[5][1]), float(orig_pts[5][1]))
        for p in settled_pts:
            self.assertGreaterEqual(float(p[1]), -0.01)

    def test_interactive_hair_brush_and_reset(self):
        import hair_sim
        from usd_graph import _hair_query_matches, apply_hair

        hair_sim.reset_hair_sessions()
        stage, curves = self._make_stage()
        cfg = {
            "targetQuery": "",
            "widthMode": "multiply",
            "width": 1.0,
            "taper": 0.0,
            "rootScale": 1.0,
            "simEnabled": True,
            "simMode": "static",
            "settleSteps": 6,
            "colliderQuery": "/Scene/HeadCollider",
            "gravity": 9.81,
        }
        apply_hair(stage, cfg)
        before_pts = curves.GetPointsAttr().Get()

        cam_basis = {
            "origin": (0.4, 0.2, 2.0),
            "forward": (0.0, 0.0, -1.0),
            "right": (1.0, 0.0, 0.0),
            "up": (0.0, 1.0, 0.0),
            "tan_half_fov": 0.4142,
            "aspect": 1.3333,
        }
        updates = hair_sim.step_hair_brush(
            stage,
            cfg,
            _hair_query_matches,
            {"nx": 0.35, "ny": 0.5, "prevNx": 0.5, "prevNy": 0.5, "radius": 1.0, "strength": 1.5},
            cam_basis,
        )
        self.assertEqual(len(updates), 1)
        after_pts = curves.GetPointsAttr().Get()
        self.assertLess(float(after_pts[5][0]), float(before_pts[5][0]))
        hair_sim.reset_hair_sessions()

    def test_comb_brushes_mask_backfacing_roots_before_picking(self):
        from pxr import Gf, UsdGeom, Vt
        import hair_sim
        from usd_graph import _hair_query_matches, apply_hair

        self.addCleanup(hair_sim.reset_hair_sessions)
        camera = {
            "origin": (0.3, 0.0, 2.0),
            "forward": (0.0, 0.0, -1.0),
            "right": (1.0, 0.0, 0.0),
            "up": (0.0, 1.0, 0.0),
        }
        for tool in ("comb", "surfaceComb"):
            for use_collider in (True, False):
                with self.subTest(tool=tool, use_collider=use_collider):
                    hair_sim.reset_hair_sessions()
                    stage, curves = self._make_stage()
                    cfg = {
                        "targetQuery": "/Scene/Hair",
                        "simEnabled": True,
                        "simMode": "static",
                        "settleSteps": 1,
                        "gravity": 0.0,
                        "collisionMargin": 0.001,
                        "colliderQuery": "/Scene/Scalp" if use_collider else "",
                    }
                    scalp = UsdGeom.Mesh.Define(stage, "/Scene/Scalp")
                    scalp.CreatePointsAttr(Vt.Vec3fArray([
                        Gf.Vec3f(x, y, z)
                        for z in (0.6, -0.6)
                        for x, y in ((-2, -2), (2, -2), (2, 2), (-2, 2))
                    ]))
                    scalp.CreateFaceVertexCountsAttr(Vt.IntArray([4, 4]))
                    scalp.CreateFaceVertexIndicesAttr(Vt.IntArray([3, 2, 1, 0, 4, 5, 6, 7]))
                    # With a collider, point the root segments against its normals.
                    # Without one, point the root segments away from/toward the eye.
                    segment_sign = 1 if use_collider else -1
                    curves.GetPointsAttr().Set(Vt.Vec3fArray([
                        Gf.Vec3f(max(0, k - 1) * 0.12, 0.0, side * (0.55 + segment_sign * min(k, 1) * 0.02))
                        for side in (1, -1)
                        for k in range(6)
                    ]))
                    apply_hair(stage, cfg)
                    attr = curves.GetPointsAttr()
                    before = attr.Get()

                    for prev_x, x in ((0.5, 0.6), (0.6, 0.7)):
                        hair_sim.step_hair_brush(
                            stage, cfg, _hair_query_matches,
                            {"tool": tool, "strokeId": 1, "nx": x, "prevNx": prev_x, "radius": 1.0},
                            camera,
                        )
                    sess = hair_sim._ACTIVE_SIM_SESSIONS["/Scene/Hair"]
                    self.assertLess(sess["grab_center"][2], 0.0)
                    self.assertEqual(list(sess["active_strand_wp"].numpy()), [0, 1])
                    after = attr.Get()
                    self.assertEqual(list(after[:6]), list(before[:6]))
                    self.assertTrue(any(
                        (after[k] - before[k]).GetLength() > 1e-4 for k in range(7, 12)
                    ))

    def test_committed_groom_recomposes_after_a_preview_target_change(self):
        import tempfile
        from pxr import UsdGeom
        import hair_sim
        from usd_graph import (
            _hair_query_matches, compile_graph, editable_hair_node_id,
            graph_after_groom_commit, open_hair_preview_stage,
        )

        self.addCleanup(hair_sim.reset_hair_sessions)
        hair_sim.reset_hair_sessions()
        with tempfile.TemporaryDirectory() as directory:
            source_path = str(Path(directory) / "hair.usda")
            source, _ = self._make_stage()
            source.GetRootLayer().Export(source_path)
            cfg = {"simEnabled": True, "simMode": "static", "settleSteps": 1, "gravity": 0.0}
            graph = {
                "targetNodeId": "hair",
                "dependencies": [{"id": "asset", "path": source_path}],
                "nodes": [
                    {"id": "source", "operation": "source", "inputDependencyId": "asset", "instanced": False},
                    {"id": "hair", "operation": "hair", "hair": cfg},
                    {"id": "output", "operation": "output"},
                ],
                "edges": [
                    {"id": "input", "source": "source", "target": "hair"},
                    {"id": "result", "source": "hair", "target": "output"},
                ],
            }
            composed, keepalive, _ = compile_graph(graph, interactive_hair=True)
            preview = open_hair_preview_stage(composed, graph)
            curves = next(UsdGeom.BasisCurves(prim) for prim in preview.Traverse() if prim.IsA(UsdGeom.BasisCurves))
            prim_path = curves.GetPath()
            before = curves.GetPointsAttr().Get()
            camera = {
                "origin": (0.4, 0.2, 2.0), "forward": (0.0, 0.0, -1.0),
                "right": (1.0, 0.0, 0.0), "up": (0.0, 1.0, 0.0),
            }
            updates = hair_sim.step_hair_brush(
                preview, cfg, _hair_query_matches,
                {"tool": "comb", "ny": 0.65, "prevNy": 0.5, "radius": 1.0, "newStroke": True, "commitUsd": True},
                camera,
            )
            after = curves.GetPointsAttr().Get()
            self.assertNotEqual(after, before)
            baseline = UsdGeom.BasisCurves(composed.GetPrimAtPath(prim_path)).GetPointsAttr().Get()
            self.assertEqual(baseline, before)
            deltas = hair_sim.export_session_groom_deltas(
                preview, cfg, _hair_query_matches, local_updates=updates, acknowledge=True,
            )
            self.assertIn(str(prim_path), deltas)
            committed_graph = graph_after_groom_commit(graph, "hair", deltas)
            self.assertEqual(committed_graph["nodes"][1]["hair"]["groomDeltas"], deltas)
            self.assertNotIn("groomDeltas", cfg)
            output_graph = {**graph, "targetNodeId": "output"}
            expected_output = graph_after_groom_commit(output_graph, "hair", deltas)
            self.assertEqual(expected_output["nodes"], committed_graph["nodes"])
            self.assertIsNone(editable_hair_node_id(output_graph, None))
            self.assertEqual(editable_hair_node_id(output_graph, "hair"), "hair")
            merge_graph = {**output_graph, "edges": [
                *graph["edges"], {"id": "second", "source": "source", "target": "output"},
            ]}
            self.assertIsNone(graph_after_groom_commit(merge_graph, "hair", deltas))

            hair_sim.reset_hair_sessions()
            upstream, keepalive, _ = compile_graph({**graph, "targetNodeId": "source"}, reload=True)
            self.assertEqual(UsdGeom.BasisCurves(upstream.GetPrimAtPath(prim_path)).GetPointsAttr().Get(), before)
            for node_id in ("output", "hair"):
                rebuilt, keepalive, _ = compile_graph({**committed_graph, "targetNodeId": node_id}, interactive_hair=True)
                actual = UsdGeom.BasisCurves(rebuilt.GetPrimAtPath(prim_path)).GetPointsAttr().Get()
                self.assertEqual(len(actual), len(after))
                self.assertTrue(all((a - b).GetLength() < 1e-6 for a, b in zip(actual, after)))

    def test_guide_comb_and_surface_comb_brushes(self):
        import numpy as np
        from pxr import Gf, Sdf, Usd, UsdGeom, Vt
        import hair_sim
        from usd_graph import _hair_query_matches, apply_hair

        self.addCleanup(hair_sim.reset_hair_sessions)
        hair_sim.reset_hair_sessions()
        layer = Sdf.Layer.CreateAnonymous("guide_comb_test.usda")
        stage = Usd.Stage.Open(layer)
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.Xform.Define(stage, "/Scene")
        scalp = UsdGeom.Mesh.Define(stage, "/Scene/Scalp")
        scalp.CreatePointsAttr(Vt.Vec3fArray([
            Gf.Vec3f(-2, 0.0, -2), Gf.Vec3f(2, 0.0, -2), Gf.Vec3f(2, 0.0, 2), Gf.Vec3f(-2, 0.0, 2),
            Gf.Vec3f(-2, -0.5, -2), Gf.Vec3f(2, -0.5, -2), Gf.Vec3f(2, -0.5, 2), Gf.Vec3f(-2, -0.5, 2),
        ]))
        scalp.CreateFaceVertexCountsAttr(Vt.IntArray([4, 4, 4, 4, 4, 4]))
        scalp.CreateFaceVertexIndicesAttr(Vt.IntArray([
            0, 3, 2, 1, 4, 5, 6, 7, 0, 1, 5, 4, 2, 3, 7, 6, 1, 2, 6, 5, 3, 0, 4, 7,
        ]))

        n_strands = 1100
        n_verts = 6
        pts = [
            Gf.Vec3f(-0.5 + (s % 35) * 0.025, 0.01 + k * 0.1, -0.5 + (s // 35) * 0.025)
            for s in range(n_strands)
            for k in range(n_verts)
        ]
        curves = UsdGeom.BasisCurves.Define(stage, "/Scene/Hair")
        curves.CreateTypeAttr().Set(UsdGeom.Tokens.linear)
        curves.CreateCurveVertexCountsAttr().Set(Vt.IntArray([n_verts] * n_strands))
        curves.CreatePointsAttr().Set(Vt.Vec3fArray(pts))
        curves.CreateWidthsAttr().Set(Vt.FloatArray([0.005] * (n_strands * n_verts)))

        cfg = {
            "simEnabled": True,
            "simMode": "static",
            "settleSteps": 2,
            "gravity": 0.0,
            "colliderQuery": "/Scene/Scalp",
            "collisionMargin": 0.002,
        }
        apply_hair(stage, cfg)
        cam = {
            "origin": (0.0, 0.5, 2.0), "forward": (0.0, 0.0, -1.0),
            "right": (1.0, 0.0, 0.0), "up": (0.0, 1.0, 0.0),
            "tan_half_fov": 0.4142, "aspect": 1.3333,
        }
        for idx, tool in enumerate(("comb", "surfaceComb"), start=1):
            with self.subTest(tool=tool):
                hair_sim.step_hair_brush(
                    stage, cfg, _hair_query_matches,
                    {"tool": tool, "strokeId": idx, "newStroke": True, "nx": 0.5, "ny": 0.5, "prevNx": 0.5, "prevNy": 0.5, "radius": 1.0, "strength": 1.5},
                    cam,
                )
                b0 = np.asarray(curves.GetPointsAttr().Get(), dtype=np.float32)
                hair_sim.step_hair_brush(
                    stage, cfg, _hair_query_matches,
                    {"tool": tool, "strokeId": idx, "newStroke": False, "nx": 0.65, "ny": 0.5, "prevNx": 0.5, "prevNy": 0.5, "radius": 1.0, "strength": 1.5},
                    cam,
                )
                b1 = np.asarray(curves.GetPointsAttr().Get(), dtype=np.float32)
                self.assertGreater(float(np.max(np.abs(b1 - b0))), 0.05)

    def test_grab_brush_on_cached_slack_strands_no_blowup(self):
        import numpy as np
        from pxr import Gf, Sdf, Usd, UsdGeom, Vt
        import hair_sim
        from usd_graph import _hair_query_matches, apply_hair

        self.addCleanup(hair_sim.reset_hair_sessions)
        hair_sim.reset_hair_sessions()
        layer = Sdf.Layer.CreateAnonymous("grab_slack_test.usda")
        stage = Usd.Stage.Open(layer)
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.Xform.Define(stage, "/Scene")

        n_strands, n_verts = 8, 8
        pts = [
            Gf.Vec3f(
                -0.08 + s * 0.02 + 0.25 * float(np.sin((k / (n_verts - 1)) * np.pi)),
                0.5 - 0.3 * (k / (n_verts - 1)),
                0.0,
            )
            for s in range(n_strands)
            for k in range(n_verts)
        ]
        curves = UsdGeom.BasisCurves.Define(stage, "/Scene/Hair")
        curves.CreateTypeAttr().Set(UsdGeom.Tokens.linear)
        curves.CreateCurveVertexCountsAttr().Set(Vt.IntArray([n_verts] * n_strands))
        curves.CreatePointsAttr().Set(Vt.Vec3fArray(pts))

        cfg = {
            "simEnabled": True,
            "simMode": "static",
            "settleSteps": 2,
            "gravity": 9.81,
            "stiffness": 0.98,
            "bendStiffness": 0.3,
            "substeps": 8,
            "iterations": 6,
        }
        # Run on a first stage to populate the cache, then on a fresh stage so
        # apply_hair exercises the cached settled-pose path.
        apply_hair(stage, cfg)
        hair_sim.reset_hair_sessions()
        stage2 = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(stage2, UsdGeom.Tokens.y)
        UsdGeom.Xform.Define(stage2, "/Scene")
        curves2 = UsdGeom.BasisCurves.Define(stage2, "/Scene/Hair")
        curves2.CreateTypeAttr().Set(UsdGeom.Tokens.linear)
        curves2.CreateCurveVertexCountsAttr().Set(Vt.IntArray([n_verts] * n_strands))
        curves2.CreatePointsAttr().Set(Vt.Vec3fArray(pts))
        apply_hair(stage2, cfg)
        settled = np.asarray(curves2.GetPointsAttr().Get(), dtype=np.float32).reshape(n_strands, n_verts, 3)
        settled_seg_lens = np.linalg.norm(settled[:, 1:] - settled[:, :-1], axis=2)

        cam = {
            "origin": (0.0, 0.3, 1.5), "forward": (0.0, 0.0, -1.0),
            "right": (1.0, 0.0, 0.0), "up": (0.0, 1.0, 0.0),
            "tan_half_fov": 0.4142, "aspect": 1.3333,
        }
        hair_sim.step_hair_brush(
            stage2, cfg, _hair_query_matches,
            {"tool": "grab", "strokeId": 1, "newStroke": True, "nx": 0.5, "ny": 0.5, "prevNx": 0.5, "prevNy": 0.5, "radius": 0.5, "strength": 1.0},
            cam,
        )
        for step, nx in enumerate((0.40, 0.30)):
            hair_sim.step_hair_brush(
                stage2, cfg, _hair_query_matches,
                {"tool": "grab", "strokeId": 1, "newStroke": False, "nx": nx, "ny": 0.5, "prevNx": 0.5 - step * 0.1, "prevNy": 0.5, "radius": 0.5, "strength": 1.0},
                cam,
            )
        after = np.asarray(curves2.GetPointsAttr().Get(), dtype=np.float32).reshape(n_strands, n_verts, 3)
        after_seg_lens = np.linalg.norm(after[:, 1:] - after[:, :-1], axis=2)
        self.assertLessEqual(float(np.max(after_seg_lens / np.maximum(settled_seg_lens, 1e-6))), 1.03)

    def test_live_physics_preserves_follower_strand_lengths(self):
        import numpy as np
        from pxr import Usd, UsdGeom, Vt
        import hair_sim
        from usd_graph import _hair_query_matches, apply_hair

        self.addCleanup(hair_sim.reset_hair_sessions)
        hair_sim.reset_hair_sessions()
        stage = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.Xform.Define(stage, "/Scene")
        n_strands, n_verts = 1088, 12
        rng = np.random.default_rng(7)
        s_idx = np.arange(n_strands, dtype=np.float32)
        angles = (s_idx / n_strands) * 2.0 * np.pi
        roots = np.stack([
            0.18 * np.cos(angles) + rng.uniform(-0.01, 0.01, n_strands).astype(np.float32),
            np.full(n_strands, 0.4, dtype=np.float32),
            0.18 * np.sin(angles) + rng.uniform(-0.01, 0.01, n_strands).astype(np.float32),
        ], axis=1)
        t = np.linspace(0.0, 1.0, n_verts, dtype=np.float32)[None, :, None]
        # Non-uniform segments + slight initial curl so followers rotate relative to guides
        offsets = np.zeros((n_strands, n_verts, 3), dtype=np.float32)
        offsets[:, :, 0] = 0.08 * np.sin(t[:, :, 0] * np.pi + angles[:, None])
        offsets[:, :, 1] = -0.35 * (t[:, :, 0] ** 1.15)
        offsets[:, :, 2] = 0.04 * np.cos(t[:, :, 0] * np.pi)
        pts_np = (roots[:, None, :] + offsets).reshape(-1, 3).astype(np.float32)

        curves = UsdGeom.BasisCurves.Define(stage, "/Scene/Hair")
        curves.CreateTypeAttr().Set(UsdGeom.Tokens.linear)
        curves.CreateCurveVertexCountsAttr().Set(Vt.IntArray([n_verts] * n_strands))
        curves.CreatePointsAttr().Set(Vt.Vec3fArray.FromNumpy(pts_np))

        cfg = {
            "simEnabled": True,
            "simMode": "static",
            "settleSteps": 4,
            "gravity": 9.81,
            "wind": [3.0, 0.0, 0.0],
            "stiffness": 0.95,
            "bendStiffness": 0.4,
            "substeps": 4,
            "iterations": 8,
        }
        apply_hair(stage, cfg)
        rest_seg_lens = np.linalg.norm(
            pts_np.reshape(n_strands, n_verts, 3)[:, 1:] - pts_np.reshape(n_strands, n_verts, 3)[:, :-1],
            axis=2,
        )
        cam = {
            "origin": (0.0, 0.2, 2.0), "forward": (0.0, 0.0, -1.0),
            "right": (1.0, 0.0, 0.0), "up": (0.0, 1.0, 0.0),
            "tan_half_fov": 0.4142, "aspect": 1.3333,
        }
        brush = {
            "tool": "comb", "livePhysics": True, "settleOnly": True, "commitUsd": False,
            "nx": 0.5, "ny": 0.5, "prevNx": 0.5, "prevNy": 0.5,
        }
        diag = {"totalStrands": 0, "guideStrands": 0, "fullSolveStrands": 0}
        for _ in range(16):
            diag = {"totalStrands": 0, "guideStrands": 0, "fullSolveStrands": 0}
            hair_sim.step_hair_brush(stage, cfg, _hair_query_matches, brush, cam, write_usd=False, diagnostics=diag)
        self.assertGreater(diag["guideStrands"], 0)
        self.assertEqual(diag["fullSolveStrands"], 0)
        sess = next(iter(hair_sim._ACTIVE_SIM_SESSIONS.values()))
        after = sess["pos_wp"].numpy().reshape(n_strands, n_verts, 3)
        after_seg_lens = np.linalg.norm(after[:, 1:] - after[:, :-1], axis=2)
        self.assertLessEqual(float(np.max(after_seg_lens / np.maximum(rest_seg_lens, 1e-6))), 1.03)


if __name__ == "__main__":
    unittest.main()
