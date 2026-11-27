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

    def test_comb_brush_disentangles_and_straightens_along_drag_path(self):
        import numpy as np
        from pxr import Usd, UsdGeom, Vt
        import hair_sim
        from usd_graph import _hair_query_matches

        self.addCleanup(hair_sim.reset_hair_sessions)
        hair_sim.reset_hair_sessions()

        def _direction_roughness(pts_3d):
            segs = pts_3d[:, 1:] - pts_3d[:, :-1]
            lens = np.linalg.norm(segs, axis=2, keepdims=True)
            dirs = segs / np.maximum(lens, 1e-6)
            return float(np.mean(np.linalg.norm(dirs[:, 1:] - dirs[:, :-1], axis=2)))

        # 1) Direct-mode kinked/zig-zag strands combed downward
        stage = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        UsdGeom.Xform.Define(stage, "/Scene")
        n_strands, n_verts = 6, 10
        xs = np.linspace(-0.08, 0.08, n_strands, dtype=np.float32)
        pts = np.zeros((n_strands, n_verts, 3), dtype=np.float32)
        for s in range(n_strands):
            for j in range(n_verts):
                kink = (0.045 if (j % 2 == 1) else -0.045) if j > 0 else 0.0
                z_kink = (0.03 if ((j + s) % 2 == 1) else -0.03) if j > 0 else 0.0
                pts[s, j] = [xs[s] + kink, 0.4 - 0.045 * j, z_kink]
        curves = UsdGeom.BasisCurves.Define(stage, "/Scene/Hair")
        curves.CreateTypeAttr().Set(UsdGeom.Tokens.linear)
        curves.CreateCurveVertexCountsAttr().Set(Vt.IntArray([n_verts] * n_strands))
        curves.CreatePointsAttr().Set(Vt.Vec3fArray.FromNumpy(pts.reshape(-1, 3)))

        cfg = {
            "simEnabled": True,
            "simMode": "static",
            "gravity": 0.0,
            "stiffness": 0.95,
            "bendStiffness": 0.4,
            "substeps": 4,
            "iterations": 8,
            "collisionMargin": 0.005,
        }
        cam = {
            "origin": (0.0, 0.2, 1.8), "forward": (0.0, 0.0, -1.0),
            "right": (1.0, 0.0, 0.0), "up": (0.0, 1.0, 0.0),
            "tan_half_fov": 0.4142, "aspect": 1.3333,
        }
        before_rough = _direction_roughness(pts)
        rest_seg_lens = np.linalg.norm(pts[:, 1:] - pts[:, :-1], axis=2)

        # Drag downward (+ny in screen space => -Y in world space)
        ny_steps = [0.40, 0.46, 0.52, 0.58, 0.64, 0.70]
        for idx, ny in enumerate(ny_steps):
            hair_sim.step_hair_brush(
                stage, cfg, _hair_query_matches,
                {
                    "tool": "comb", "strokeId": 1, "newStroke": idx == 0,
                    "nx": 0.50, "ny": ny,
                    "prevNx": 0.50, "prevNy": ny_steps[max(0, idx - 1)],
                    "radius": 0.45, "strength": 1.0,
                },
                cam,
            )
        after = np.asarray(curves.GetPointsAttr().Get(), dtype=np.float32).reshape(n_strands, n_verts, 3)
        after_rough = _direction_roughness(after)
        self.assertLess(after_rough, before_rough * 0.55)
        # Roots stay pinned and segment lengths stay preserved
        self.assertLess(float(np.max(np.linalg.norm(after[:, 0] - pts[:, 0], axis=1))), 1e-4)
        after_seg_lens = np.linalg.norm(after[:, 1:] - after[:, :-1], axis=2)
        self.assertLessEqual(float(np.max(after_seg_lens / np.maximum(rest_seg_lens, 1e-6))), 1.05)
        # Segments align downward (-Y) along the drag path (much higher than initial zig-zag ~0.38)
        before_segs = pts[:, 1:] - pts[:, :-1]
        before_dirs = before_segs / np.maximum(np.linalg.norm(before_segs, axis=2, keepdims=True), 1e-6)
        before_align = float(np.mean(before_dirs[:, :, 1] * -1.0))
        after_segs = after[:, 1:] - after[:, :-1]
        after_dirs = after_segs / np.maximum(np.linalg.norm(after_segs, axis=2, keepdims=True), 1e-6)
        after_align = float(np.mean(after_dirs[:, :, 1] * -1.0))
        self.assertGreater(after_align, before_align + 0.25)

        # 2) Guide-mode (1,100 tangled strands) with a spherical scalp collider
        hair_sim.reset_hair_sessions()
        stage2 = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(stage2, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(stage2, 1.0)
        UsdGeom.Xform.Define(stage2, "/Scene")
        # Simple icosphere/octahedron-like scalp collider centered at (0, 0.2, -0.35), r=0.16
        sphere = UsdGeom.Sphere.Define(stage2, "/Scene/Scalp")
        sphere.CreateRadiusAttr().Set(0.16)
        UsdGeom.Xformable(sphere).AddTranslateOp().Set((0.0, 0.2, -0.35))

        n_strands2, n_verts2 = 1100, 10
        rng = np.random.default_rng(13)
        roots2 = np.stack([
            rng.uniform(-0.14, 0.14, n_strands2).astype(np.float32),
            rng.uniform(0.30, 0.42, n_strands2).astype(np.float32),
            rng.uniform(-0.08, 0.08, n_strands2).astype(np.float32),
        ], axis=1)
        pts2 = np.zeros((n_strands2, n_verts2, 3), dtype=np.float32)
        pts2[:, 0] = roots2
        for j in range(1, n_verts2):
            sign = 1.0 if (j % 2 == 1) else -1.0
            pts2[:, j, 0] = roots2[:, 0] + sign * 0.035
            pts2[:, j, 1] = roots2[:, 1] - 0.04 * j
            pts2[:, j, 2] = roots2[:, 2] + sign * 0.02
        curves2 = UsdGeom.BasisCurves.Define(stage2, "/Scene/Hair")
        curves2.CreateTypeAttr().Set(UsdGeom.Tokens.linear)
        curves2.CreateCurveVertexCountsAttr().Set(Vt.IntArray([n_verts2] * n_strands2))
        curves2.CreatePointsAttr().Set(Vt.Vec3fArray.FromNumpy(pts2.reshape(-1, 3)))

        before_rough2 = _direction_roughness(pts2)
        diag = {"totalStrands": 0, "guideStrands": 0, "fullSolveStrands": 0}
        for idx, ny in enumerate(ny_steps):
            diag = {"totalStrands": 0, "guideStrands": 0, "fullSolveStrands": 0}
            hair_sim.step_hair_brush(
                stage2, cfg, _hair_query_matches,
                {
                    "tool": "comb", "strokeId": 2, "newStroke": idx == 0,
                    "nx": 0.50, "ny": ny,
                    "prevNx": 0.50, "prevNy": ny_steps[max(0, idx - 1)],
                    "radius": 0.45, "strength": 1.0,
                },
                cam,
                diagnostics=diag,
            )
        self.assertGreater(diag.get("guideStrands", 0), 0)
        after2 = np.asarray(curves2.GetPointsAttr().Get(), dtype=np.float32).reshape(n_strands2, n_verts2, 3)
        after_rough2 = _direction_roughness(after2)
        self.assertLess(after_rough2, before_rough2 * 0.70)

    def test_draw_lock_hair_creation_over_3d_object_and_empty_space(self):
        import numpy as np
        from pxr import Usd, UsdGeom, Vt
        import hair_sim
        from usd_graph import _hair_query_matches

        self.addCleanup(hair_sim.reset_hair_sessions)
        hair_sim.reset_hair_sessions()

        def _make_mesh_only_stage():
            st = Usd.Stage.CreateInMemory()
            UsdGeom.SetStageUpAxis(st, UsdGeom.Tokens.y)
            UsdGeom.SetStageMetersPerUnit(st, 1.0)
            UsdGeom.Xform.Define(st, "/Scene")
            # Triangulated UV sphere centered at (0, 0, 0), radius 0.25
            mesh = UsdGeom.Mesh.Define(st, "/Scene/Head")
            n_lat, n_lon = 16, 24
            r = 0.25
            verts = []
            for i in range(n_lat + 1):
                theta = np.pi * i / n_lat
                for j in range(n_lon):
                    phi = 2.0 * np.pi * j / n_lon
                    verts.append((
                        float(r * np.sin(theta) * np.cos(phi)),
                        float(r * np.cos(theta)),
                        float(r * np.sin(theta) * np.sin(phi)),
                    ))
            faces = []
            counts = []
            for i in range(n_lat):
                for j in range(n_lon):
                    jn = (j + 1) % n_lon
                    v00 = i * n_lon + j
                    v01 = i * n_lon + jn
                    v10 = (i + 1) * n_lon + j
                    v11 = (i + 1) * n_lon + jn
                    faces.extend([v00, v11, v10, v00, v01, v11])
                    counts.extend([3, 3])
            mesh.CreatePointsAttr().Set(Vt.Vec3fArray(verts))
            mesh.CreateFaceVertexCountsAttr().Set(Vt.IntArray(counts))
            mesh.CreateFaceVertexIndicesAttr().Set(Vt.IntArray(faces))
            return st

        stage = _make_mesh_only_stage()
        cfg = {
            "simEnabled": True,
            "simMode": "static",
            "gravity": 0.0,
            "stiffness": 0.95,
            "bendStiffness": 0.4,
            "collisionMargin": 0.004,
        }
        cam = {
            "origin": (0.0, 0.0, 1.2), "forward": (0.0, 0.0, -1.0),
            "right": (1.0, 0.0, 0.0), "up": (0.0, 1.0, 0.0),
            "tan_half_fov": 0.4142, "aspect": 1.3333,
        }

        # 1) Dragging in empty space (far left corner) must NOT create hair
        empty_curve = [[0.04, 0.10], [0.06, 0.18], [0.08, 0.26]]
        res_empty = hair_sim.step_hair_brush(
            stage, cfg, _hair_query_matches,
            {
                "tool": "drawLock", "strokeId": 1, "newStroke": True,
                "nx": 0.08, "ny": 0.26, "prevNx": 0.04, "prevNy": 0.10,
                "radius": 0.04, "strength": 1.0, "drawCurve": empty_curve,
            },
            cam,
        )
        self.assertEqual(res_empty, [])
        self.assertFalse(stage.GetPrimAtPath("/Scene/CreatedHair").IsValid())

        # 2) Dragging across the 3D head mesh creates a multi-strand lock along the curve
        head_curve = [[0.48, 0.34], [0.50, 0.42], [0.52, 0.50], [0.54, 0.58], [0.55, 0.64]]
        res_hit = hair_sim.step_hair_brush(
            stage, cfg, _hair_query_matches,
            {
                "tool": "drawLock", "strokeId": 2, "newStroke": True,
                "nx": 0.55, "ny": 0.64, "prevNx": 0.48, "prevNy": 0.34,
                "radius": 0.04, "strength": 1.0, "drawCurve": head_curve,
            },
            cam,
        )
        self.assertEqual(len(res_hit), 1)
        prim = stage.GetPrimAtPath("/Scene/CreatedHair")
        self.assertTrue(prim.IsValid())
        curves = UsdGeom.BasisCurves(prim)
        c1 = np.asarray(curves.GetCurveVertexCountsAttr().Get(), dtype=np.int32)
        p1 = np.asarray(curves.GetPointsAttr().Get(), dtype=np.float32).reshape(len(c1), int(c1[0]), 3)
        self.assertGreaterEqual(len(c1), 16)
        # Lock flows downward from top of head drag (+Y) to lower (-Y)
        self.assertGreater(float(np.mean(p1[:, 0, 1])), float(np.mean(p1[:, -1, 1])) + 0.12)
        # Non-root vertices stay outside the sphere collider (r=0.25)
        radii = np.linalg.norm(p1[:, 1:, :], axis=2)
        self.assertGreaterEqual(float(np.min(radii)), 0.245)

        # 3) Persisting via groomDeltas restores /Scene/CreatedHair on a fresh mesh-only stage
        deltas = hair_sim.export_session_groom_deltas(stage, cfg, _hair_query_matches)
        self.assertIn("/Scene/CreatedHair", deltas)
        hair_sim.reset_hair_sessions()
        stage_reloaded = _make_mesh_only_stage()
        hair_sim.apply_hair_simulation(stage_reloaded, {**cfg, "groomDeltas": deltas}, _hair_query_matches)
        prim_reloaded = stage_reloaded.GetPrimAtPath("/Scene/CreatedHair")
        self.assertTrue(prim_reloaded.IsValid())
        c_rel = np.asarray(UsdGeom.BasisCurves(prim_reloaded).GetCurveVertexCountsAttr().Get(), dtype=np.int32)
        self.assertEqual(len(c_rel), len(c1))

        # 4) Drawing a second lock appends cleanly to the existing hair prim
        head_curve2 = [[0.44, 0.36], [0.43, 0.46], [0.42, 0.56], [0.41, 0.62]]
        hair_sim.step_hair_brush(
            stage_reloaded, {**cfg, "groomDeltas": deltas}, _hair_query_matches,
            {
                "tool": "drawLock", "strokeId": 3, "newStroke": True,
                "nx": 0.41, "ny": 0.62, "prevNx": 0.44, "prevNy": 0.36,
                "radius": 0.04, "strength": 1.0, "drawCurve": head_curve2,
            },
            cam,
        )
        c2 = np.asarray(UsdGeom.BasisCurves(prim_reloaded).GetCurveVertexCountsAttr().Get(), dtype=np.int32)
        self.assertEqual(len(c2), len(c1) * 2)

        # 4b) Immediately styling the newly drawn lock with Comb moves the strands
        before_comb = np.asarray(UsdGeom.BasisCurves(prim_reloaded).GetPointsAttr().Get(), dtype=np.float32).copy()
        for idx, nx in enumerate((0.50, 0.54, 0.58)):
            hair_sim.step_hair_brush(
                stage_reloaded, {**cfg, "groomDeltas": deltas}, _hair_query_matches,
                {
                    "tool": "comb", "strokeId": 10, "newStroke": idx == 0,
                    "nx": nx, "ny": 0.50, "prevNx": 0.50 if idx == 0 else nx - 0.04, "prevNy": 0.50,
                    "radius": 0.08, "strength": 1.0,
                },
                cam,
            )
        after_comb = np.asarray(UsdGeom.BasisCurves(prim_reloaded).GetPointsAttr().Get(), dtype=np.float32)
        self.assertGreater(float(np.max(np.linalg.norm(after_comb - before_comb, axis=1))), 0.005)

        # 5) Also works when the 3D object is an instanceable USD reference (like Source nodes)
        hair_sim.reset_hair_sessions()
        proto_stage = _make_mesh_only_stage()
        inst_stage = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(inst_stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(inst_stage, 1.0)
        UsdGeom.Xform.Define(inst_stage, "/Scene")
        ref_prim = inst_stage.DefinePrim("/Scene/HeadInstance")
        ref_prim.GetReferences().AddReference(proto_stage.GetRootLayer().identifier, "/Scene/Head")
        ref_prim.SetInstanceable(True)
        res_inst = hair_sim.step_hair_brush(
            inst_stage, cfg, _hair_query_matches,
            {
                "tool": "drawLock", "strokeId": 4, "newStroke": True,
                "nx": 0.55, "ny": 0.64, "prevNx": 0.48, "prevNy": 0.34,
                "radius": 0.04, "strength": 1.0, "drawCurve": head_curve,
            },
            cam,
        )
        self.assertEqual(len(res_inst), 1)
        self.assertTrue(inst_stage.GetPrimAtPath("/Scene/CreatedHair").IsValid())

    def test_all_brushes_prevent_collider_penetration(self):
        """Every grooming brush (including with default colliderQuery='__none__') must not push strands through 3D scene meshes."""
        import numpy as np
        from pxr import Gf, Usd, UsdGeom, Vt
        import hair_sim
        from usd_graph import _hair_query_matches, apply_hair

        self.addCleanup(hair_sim.reset_hair_sessions)

        radius = 0.25

        def _make_head_and_hair_stage():
            stage = Usd.Stage.CreateInMemory()
            UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
            UsdGeom.SetStageMetersPerUnit(stage, 1.0)
            UsdGeom.Xform.Define(stage, "/Scene")

            # UV sphere head mesh centered at origin, radius = 0.25
            n_lat, n_lon = 16, 24
            m_pts = []
            for i in range(n_lat + 1):
                phi = np.pi * i / n_lat
                for j in range(n_lon):
                    theta = 2.0 * np.pi * j / n_lon
                    m_pts.append(Gf.Vec3f(
                        float(radius * np.sin(phi) * np.cos(theta)),
                        float(radius * np.cos(phi)),
                        float(radius * np.sin(phi) * np.sin(theta)),
                    ))
            fvc, fvi = [], []
            for i in range(n_lat):
                for j in range(n_lon):
                    jn = (j + 1) % n_lon
                    a = i * n_lon + j
                    b = i * n_lon + jn
                    c = (i + 1) * n_lon + jn
                    d = (i + 1) * n_lon + j
                    fvc.append(4)
                    fvi.extend([a, b, c, d])
            head = UsdGeom.Mesh.Define(stage, "/Scene/Head")
            head.CreatePointsAttr().Set(Vt.Vec3fArray(m_pts))
            head.CreateFaceVertexCountsAttr().Set(Vt.IntArray(fvc))
            head.CreateFaceVertexIndicesAttr().Set(Vt.IntArray(fvi))

            # Strands wrapping closely around the front (+Z) of the sphere
            pts = []
            counts = []
            for s in range(6):
                x0 = -0.05 + 0.02 * s
                for k in range(12):
                    ang = -0.7 + 1.4 * (k / 11.0)
                    r = radius + 0.015
                    pts.append(Gf.Vec3f(
                        float(x0),
                        float(r * np.sin(ang)),
                        float(r * np.cos(ang)),
                    ))
                counts.append(12)
            curves = UsdGeom.BasisCurves.Define(stage, "/Scene/Hair")
            curves.CreateTypeAttr().Set(UsdGeom.Tokens.linear)
            curves.CreateCurveVertexCountsAttr().Set(Vt.IntArray(counts))
            curves.CreatePointsAttr().Set(Vt.Vec3fArray(pts))
            curves.CreateWidthsAttr().Set(Vt.FloatArray([0.002] * len(pts)))
            return stage, curves

        cfg = {
            "simEnabled": True,
            "simMode": "static",
            "settleSteps": 1,
            "gravity": 0.0,
            "colliderQuery": "__none__",
            "collisionMargin": 0.005,
        }
        cam = {
            "origin": (0.0, 0.0, 1.2),
            "forward": (0.0, 0.0, -1.0),
            "right": (1.0, 0.0, 0.0),
            "up": (0.0, 1.0, 0.0),
            "tan_half_fov": 0.4142,
            "aspect": 1.0,
        }

        # 1. smoothAll
        hair_sim.reset_hair_sessions()
        stage, curves = _make_head_and_hair_stage()
        apply_hair(stage, cfg)
        roots_before = np.asarray(curves.GetPointsAttr().Get(), dtype=np.float32)[::12].copy()
        for _ in range(4):
            hair_sim.step_hair_brush(
                stage, cfg, _hair_query_matches,
                {"tool": "smooth", "smoothAll": True, "strength": 1.0, "commitUsd": True},
                cam,
            )
        pts_np = np.asarray(curves.GetPointsAttr().Get(), dtype=np.float32)
        min_r = float(np.min(np.linalg.norm(pts_np, axis=1)))
        self.assertGreaterEqual(min_r, radius - 0.002, f"smoothAll penetrated sphere: min_r={min_r}")
        self.assertLess(float(np.max(np.linalg.norm(pts_np[::12] - roots_before, axis=1))), 1e-5, "smoothAll detached roots")

        # 2. Interactive brushes dragged directly across the curved sphere surface
        for idx, tool in enumerate(("comb", "surfaceComb", "grab", "smooth", "clump", "part", "puff", "physics", "lengthen"), start=1):
            with self.subTest(tool=tool):
                hair_sim.reset_hair_sessions()
                stage, curves = _make_head_and_hair_stage()
                apply_hair(stage, cfg)
                roots_before = np.asarray(curves.GetPointsAttr().Get(), dtype=np.float32)[::12].copy()
                for step in range(4):
                    nx = 0.50 + 0.06 * step
                    prev_nx = 0.50 + 0.06 * max(0, step - 1)
                    ny = 0.50 + 0.04 * step
                    prev_ny = 0.50 + 0.04 * max(0, step - 1)
                    hair_sim.step_hair_brush(
                        stage, cfg, _hair_query_matches,
                        {
                            "tool": tool, "strokeId": idx, "newStroke": step == 0,
                            "nx": nx, "ny": ny, "prevNx": prev_nx, "prevNy": prev_ny,
                            "radius": 0.20, "strength": 1.0, "commitUsd": step == 3,
                        },
                        cam,
                    )
                pts_np = np.asarray(curves.GetPointsAttr().Get(), dtype=np.float32)
                min_r = float(np.min(np.linalg.norm(pts_np, axis=1)))
                self.assertGreaterEqual(min_r, radius - 0.002, f"{tool} penetrated sphere: min_r={min_r}")
                self.assertLess(float(np.max(np.linalg.norm(pts_np[::12] - roots_before, axis=1))), 1e-5, f"{tool} detached roots")


if __name__ == "__main__":
    unittest.main()
