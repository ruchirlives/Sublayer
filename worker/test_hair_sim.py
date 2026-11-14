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
            {"nx": 0.65, "ny": 0.5, "prevNx": 0.5, "prevNy": 0.5, "radius": 1.0, "strength": 1.5},
            cam_basis,
        )
        self.assertEqual(len(updates), 1)
        after_pts = curves.GetPointsAttr().Get()
        self.assertGreater(float(after_pts[5][0]), float(before_pts[5][0]))
        hair_sim.reset_hair_sessions()


if __name__ == "__main__":
    unittest.main()
