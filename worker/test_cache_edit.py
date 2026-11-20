"""Automated tests for Cache Sculptor USD Cache Editor & GPU Cloth Physics Brush."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest

import numpy as np

WORKER_DIR = os.path.dirname(os.path.abspath(__file__))
if WORKER_DIR not in sys.path:
    sys.path.insert(0, WORKER_DIR)

import cache_edit as ce
import cloth_kernels as ck


def _make_grid_mesh(nx: int = 17, ny: int = 17, size: float = 1.0, z_height: float = 0.0):
    xs = np.linspace(-size * 0.5, size * 0.5, nx, dtype=np.float32)
    ys = np.linspace(-size * 0.5, size * 0.5, ny, dtype=np.float32)
    pts = []
    for y in ys:
        for x in xs:
            pts.append((float(x), float(z_height), float(y)))
    faces = []
    counts = []
    for j in range(ny - 1):
        for i in range(nx - 1):
            v0 = j * nx + i
            v1 = v0 + 1
            v2 = v0 + nx + 1
            v3 = v0 + nx
            faces.extend([v0, v2, v1, v0, v3, v2])
            counts.extend([3, 3])
    return np.asarray(pts, dtype=np.float32), np.asarray(counts, dtype=np.int32), np.asarray(faces, dtype=np.int32)


def _make_sphere_mesh(center=(0.0, -0.18, 0.0), radius: float = 0.16, stacks: int = 12, slices: int = 16):
    pts = []
    cx, cy, cz = center
    for i in range(stacks + 1):
        phi = np.pi * i / stacks
        for j in range(slices):
            theta = 2.0 * np.pi * j / slices
            x = cx + radius * np.sin(phi) * np.cos(theta)
            y = cy + radius * np.cos(phi)
            z = cz + radius * np.sin(phi) * np.sin(theta)
            pts.append((float(x), float(y), float(z)))
    faces = []
    counts = []
    for i in range(stacks):
        for j in range(slices):
            jn = (j + 1) % slices
            v0 = i * slices + j
            v1 = (i + 1) * slices + j
            v2 = (i + 1) * slices + jn
            v3 = i * slices + jn
            faces.extend([v0, v2, v1, v0, v3, v2])
            counts.extend([3, 3])
    return np.asarray(pts, dtype=np.float32), np.asarray(counts, dtype=np.int32), np.asarray(faces, dtype=np.int32)


class TestCacheEdit(unittest.TestCase):
    def test_temporal_weights(self):
        layer = {"mode": "frameRange", "centerFrame": 10.0, "fadeIn": 4.0, "fadeOut": 4.0, "falloff": "smoothstep"}
        self.assertAlmostEqual(ce.compute_temporal_weight(10.0, layer), 1.0, places=5)
        self.assertAlmostEqual(ce.compute_temporal_weight(6.0, layer), 0.0, places=5)
        self.assertAlmostEqual(ce.compute_temporal_weight(14.0, layer), 0.0, places=5)
        self.assertAlmostEqual(ce.compute_temporal_weight(8.0, layer), 0.5, places=5)
        self.assertAlmostEqual(ce.compute_temporal_weight(12.0, layer), 0.5, places=5)

        lin = {**layer, "falloff": "linear"}
        self.assertAlmostEqual(ce.compute_temporal_weight(9.0, lin), 0.75, places=5)

        gauss = {**layer, "falloff": "gaussian"}
        self.assertAlmostEqual(ce.compute_temporal_weight(10.0, gauss), 1.0, places=5)
        self.assertAlmostEqual(ce.compute_temporal_weight(6.0, gauss), 0.0, places=5)
        self.assertGreater(ce.compute_temporal_weight(8.0, gauss), 0.2)

        single = {"mode": "singleFrame", "centerFrame": 5.0}
        self.assertEqual(ce.compute_temporal_weight(5.0, single), 1.0)
        self.assertEqual(ce.compute_temporal_weight(6.0, single), 0.0)

        all_f = {"mode": "allFrames"}
        self.assertEqual(ce.compute_temporal_weight(999.0, all_f), 1.0)

    def test_sparse_delta_and_mask_codec(self):
        delta = np.zeros((200, 3), dtype=np.float32)
        delta[10] = [0.1, -0.2, 0.3]
        delta[42] = [0.0, 0.5, -0.1]
        enc = ce.encode_mesh_delta(delta)
        self.assertIsNotNone(enc)
        self.assertIn("indicesZB64", enc)
        dec = ce.decode_mesh_delta(enc, 200)
        self.assertIsNotNone(dec)
        np.testing.assert_allclose(dec, delta, atol=1e-6)

        mask = np.zeros((200,), dtype=np.float32)
        mask[5:20] = 1.0
        mask[25] = 0.5
        m_enc = ce.encode_mesh_mask(mask)
        self.assertIsNotNone(m_enc)
        m_dec = ce.decode_mesh_mask(m_enc, 200)
        np.testing.assert_allclose(m_dec, mask, atol=1.0 / 255.0 + 1e-5)

    def test_animated_usd_layers_and_source_preservation(self):
        from pxr import Sdf, Usd, UsdGeom, Vt

        pts, counts, faces = _make_grid_mesh(9, 9, size=1.0)
        with tempfile.TemporaryDirectory() as tmp:
            src_path = os.path.join(tmp, "cloth_cache.usda")
            src_stage = Usd.Stage.CreateNew(src_path)
            UsdGeom.SetStageUpAxis(src_stage, UsdGeom.Tokens.y)
            UsdGeom.SetStageMetersPerUnit(src_stage, 1.0)
            src_stage.SetStartTimeCode(1)
            src_stage.SetEndTimeCode(15)
            mesh = UsdGeom.Mesh.Define(src_stage, "/Cloth")
            mesh.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(counts))
            mesh.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(faces))
            pa = mesh.CreatePointsAttr()
            for f in range(1, 16):
                p = pts.copy()
                p[:, 1] += 0.01 * f
                pa.Set(Vt.Vec3fArray.FromNumpy(p), float(f))
            src_stage.GetRootLayer().Save()

            with open(src_path, "rb") as fh:
                sha_before = hashlib.sha256(fh.read()).hexdigest()

            # Compose over source without editing source
            overlay = Sdf.Layer.CreateAnonymous("test_root.usda")
            overlay.subLayerPaths = [src_path]
            stage = Usd.Stage.Open(overlay)

            d1 = np.zeros_like(pts)
            d1[:, 1] = 0.5
            enc1 = ce.encode_mesh_delta(d1)

            cfg = {
                "targetQuery": "/Cloth",
                "layers": [
                    {
                        "id": "l1",
                        "name": "Fix Poke",
                        "enabled": True,
                        "weight": 1.0,
                        "mode": "frameRange",
                        "centerFrame": 8.0,
                        "fadeIn": 4.0,
                        "fadeOut": 4.0,
                        "falloff": "linear",
                        "deltas": {"/Cloth": enc1},
                    }
                ],
                "temporalSmoothFrames": 0,
                "temporalSmoothStrength": 0.0,
            }
            ce.apply_cache_edit(stage, cfg)

            out_mesh = UsdGeom.Mesh(stage.GetPrimAtPath("/Cloth"))
            p_f1 = np.asarray(out_mesh.GetPointsAttr().Get(1.0), dtype=np.float32)
            p_f6 = np.asarray(out_mesh.GetPointsAttr().Get(6.0), dtype=np.float32)
            p_f8 = np.asarray(out_mesh.GetPointsAttr().Get(8.0), dtype=np.float32)

            # Frame 1 is outside [4, 12] -> untouched (y = 0.01)
            self.assertAlmostEqual(float(p_f1[0, 1]), 0.01, places=5)
            # Frame 6 is halfway in fadeIn -> +0.25 (y = 0.06 + 0.25 = 0.31)
            self.assertAlmostEqual(float(p_f6[0, 1]), 0.31, places=5)
            # Frame 8 is peak -> +0.50 (y = 0.08 + 0.50 = 0.58)
            self.assertAlmostEqual(float(p_f8[0, 1]), 0.58, places=5)

            with open(src_path, "rb") as fh:
                sha_after = hashlib.sha256(fh.read()).hexdigest()
            self.assertEqual(sha_before, sha_after)

    def test_cloth_physics_brush_and_sculpt_brushes(self):
        from pxr import Usd, UsdGeom, Vt

        ce.reset_cache_sessions()
        stage = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)

        cloth_pts, cloth_cnt, cloth_idx = _make_grid_mesh(17, 17, size=1.0, z_height=0.0)
        cloth = UsdGeom.Mesh.Define(stage, "/Scene/Cloth")
        cloth.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(cloth_pts))
        cloth.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(cloth_cnt))
        cloth.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(cloth_idx))

        sph_pts, sph_cnt, sph_idx = _make_sphere_mesh(center=(0.0, -0.17, 0.0), radius=0.16)
        body = UsdGeom.Mesh.Define(stage, "/Scene/Body")
        body.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(sph_pts))
        body.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(sph_cnt))
        body.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(sph_idx))

        # Top-down camera looking straight at origin (0, 0, 0) from (0, 2, 0)
        cam_basis = {
            "origin": (0.0, 2.0, 0.0),
            "forward": (0.0, -1.0, 0.0),
            "right": (1.0, 0.0, 0.0),
            "up": (0.0, 0.0, -1.0),
            "tan_half_fov": 0.41421356,
            "aspect": 1.0,
        }
        cfg = {
            "targetQuery": "/Scene/Cloth",
            "colliderQuery": "/Scene/Body",
            "activeLayerId": "layer-1",
            "clothSubsteps": 4,
            "clothIterations": 10,
            "clothStretchStiffness": 0.95,
            "clothBendStiffness": 0.35,
            "clothDamping": 0.08,
            "clothGravity": 9.81,
            "clothCollisionMargin": 0.01,
            "clothFriction": 0.3,
            "clothDragPinFalloff": 1.8,
            "layers": [{"id": "layer-1", "name": "Layer 1", "enabled": True, "weight": 1.0, "mode": "allFrames"}],
        }

        # 1. Cloth Drag brush: pull center sideways
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            ce.step_cache_brush(
                stage, cfg,
                {"tool": "clothDrag", "nx": 0.5, "ny": 0.5, "prevNx": 0.5, "prevNy": 0.5,
                 "radius": 0.22, "strength": 1.0, "newStroke": True, "strokeId": 1},
                cam_basis, frame=0.0,
            )
            updates = ce.step_cache_brush(
                stage, cfg,
                {"tool": "clothDrag", "nx": 0.58, "ny": 0.5, "prevNx": 0.5, "prevNy": 0.5,
                 "radius": 0.22, "strength": 1.0, "newStroke": False, "strokeId": 1},
                cam_basis, frame=0.0,
            )
        self.assertEqual(len(updates), 1)
        pts_after_drag = np.asarray(cloth.GetPointsAttr().Get(), dtype=np.float32)
        center_idx = (17 // 2) * 17 + (17 // 2)
        # Center vertex should have moved in +X
        self.assertGreater(float(pts_after_drag[center_idx, 0]), 0.01)
        # Corner vertex outside patch must stay anchored at (-0.5, 0, -0.5)
        np.testing.assert_allclose(pts_after_drag[0], cloth_pts[0], atol=1e-5)
        # Verify woven cloth inextensibility: structural edge stretch stays tight (< 6%)
        topo = ce._extract_mesh_topology(cloth_pts, cloth_idx)
        cur_lens = np.linalg.norm(pts_after_drag[topo["edge_v1"]] - pts_after_drag[topo["edge_v0"]], axis=1)
        max_rel_stretch = float(np.max(np.abs(cur_lens - topo["edge_rest"]) / topo["edge_rest"]))
        self.assertLess(max_rel_stretch, 0.065)

        # 2. Cloth Relax brush over spherical body collider
        for step_i in range(6):
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                ce.step_cache_brush(
                    stage, cfg,
                    {"tool": "clothRelax", "nx": 0.5, "ny": 0.5, "prevNx": 0.5, "prevNy": 0.5,
                     "radius": 0.3, "strength": 1.0, "newStroke": (step_i == 0), "strokeId": 2},
                    cam_basis, frame=0.0,
                )
        pts_after_relax = np.asarray(cloth.GetPointsAttr().Get(), dtype=np.float32)
        # Center should drape downward toward the sphere but NOT penetrate inside radius 0.16
        dist_from_sphere_center = np.linalg.norm(
            pts_after_relax - np.array([[0.0, -0.17, 0.0]], dtype=np.float32), axis=1
        )
        wp, dev = ck.get_warp()
        if wp is not None:
            self.assertGreaterEqual(float(dist_from_sphere_center.min()), 0.155)

        # 3. Export sparse deltas & verify non-empty
        layer_id, deltas, masks = ce.export_session_cache_deltas(stage, cfg, frame=0.0)
        self.assertEqual(layer_id, "layer-1")
        self.assertIn("/Scene/Cloth", deltas)
        self.assertEqual(masks, {})

    def test_compile_graph_evaluates_cache_edit_node(self):
        from pxr import Usd, UsdGeom, Vt
        import usd_graph

        pts, counts, faces = _make_grid_mesh(9, 9, size=1.0)
        with tempfile.TemporaryDirectory() as tmp:
            src_path = os.path.join(tmp, "src.usda")
            src_stage = Usd.Stage.CreateNew(src_path)
            UsdGeom.SetStageUpAxis(src_stage, UsdGeom.Tokens.y)
            UsdGeom.SetStageMetersPerUnit(src_stage, 1.0)
            m = UsdGeom.Mesh.Define(src_stage, "/Cloth")
            m.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(pts))
            m.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(counts))
            m.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(faces))
            src_stage.SetDefaultPrim(m.GetPrim())
            src_stage.GetRootLayer().Save()

            graph = {
                "targetNodeId": "ce-1",
                "dependencies": [{"id": "dep-1", "kind": "usd", "path": src_path}],
                "nodes": [
                    {
                        "id": "src-1",
                        "operation": "source",
                        "inputDependencyId": "dep-1",
                        "axis": "preserve",
                        "frontAxis": "preserve",
                        "scale": 1.0,
                        "transform": {"translation": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
                    },
                    {
                        "id": "ce-1",
                        "operation": "cache_edit",
                        "axis": "preserve",
                        "frontAxis": "preserve",
                        "scale": 1.0,
                        "transform": {"translation": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
                        "cacheEdit": {
                            "targetQuery": "",
                            "colliderQuery": "__none__",
                            "activeLayerId": "layer-1",
                            "layers": [
                                {"id": "layer-1", "name": "Layer 1", "enabled": True, "weight": 1.0, "mode": "allFrames"}
                            ],
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "src-1", "target": "ce-1"}],
            }
            out_stage, _alive, _stats = usd_graph.compile_graph(graph)
            self.assertIsNotNone(out_stage)
            meshes = [p for p in out_stage.Traverse() if p.IsA(UsdGeom.Mesh)]
            self.assertEqual(len(meshes), 1)
            # Even when Source is instanced, Cache Sculptor de-instances target meshes in its overlay
            self.assertFalse(meshes[0].IsInstanceProxy())

            # Verify a live brush stroke deforms the compiled stage mesh
            ce.reset_cache_sessions()
            cam_basis = {
                "origin": (0.0, 2.0, 0.0),
                "forward": (0.0, -1.0, 0.0),
                "right": (1.0, 0.0, 0.0),
                "up": (0.0, 0.0, -1.0),
                "tan_half_fov": 0.41421356,
                "aspect": 1.0,
            }
            cache_cfg = graph["nodes"][1]["cacheEdit"]
            before = np.asarray(UsdGeom.Mesh(meshes[0]).GetPointsAttr().Get(), dtype=np.float32).copy()
            with Usd.EditContext(out_stage, out_stage.GetSessionLayer()):
                ce.step_cache_brush(
                    out_stage, cache_cfg,
                    {"tool": "grab", "nx": 0.5, "ny": 0.5, "prevNx": 0.5, "prevNy": 0.5,
                     "radius": 0.3, "strength": 1.0, "newStroke": True, "strokeId": 10},
                    cam_basis, frame=0.0,
                )
                updates = ce.step_cache_brush(
                    out_stage, cache_cfg,
                    {"tool": "grab", "nx": 0.6, "ny": 0.5, "prevNx": 0.5, "prevNy": 0.5,
                     "radius": 0.3, "strength": 1.0, "newStroke": False, "strokeId": 10},
                    cam_basis, frame=0.0,
                )
            self.assertEqual(len(updates), 1)
            after = np.asarray(UsdGeom.Mesh(meshes[0]).GetPointsAttr().Get(), dtype=np.float32)
            self.assertGreater(float(np.max(np.abs(after - before))), 0.01)

    def test_static_mesh_animated_by_temporal_sculpt_layer(self):
        from pxr import Sdf, Usd, UsdGeom, Vt

        pts, counts, faces = _make_grid_mesh(5, 5, size=1.0)
        src_layer = Sdf.Layer.CreateAnonymous("static_src.usda")
        src_stage = Usd.Stage.Open(src_layer)
        UsdGeom.SetStageUpAxis(src_stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(src_stage, 1.0)
        m = UsdGeom.Mesh.Define(src_stage, "/StaticCloth")
        m.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(pts))
        m.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(counts))
        m.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(faces))

        overlay = Sdf.Layer.CreateAnonymous("anim_root.usda")
        overlay.subLayerPaths = [src_layer.identifier]
        stage = Usd.Stage.Open(overlay)

        delta = np.zeros_like(pts)
        delta[:, 1] = 0.4
        cfg = {
            "targetQuery": "/StaticCloth",
            "layers": [
                {
                    "id": "l1",
                    "name": "Morph Peak F10",
                    "enabled": True,
                    "weight": 1.0,
                    "mode": "frameRange",
                    "centerFrame": 10.0,
                    "fadeIn": 4.0,
                    "fadeOut": 4.0,
                    "falloff": "linear",
                    "deltas": {"/StaticCloth": ce.encode_mesh_delta(delta)},
                }
            ],
        }
        ce.apply_cache_edit(stage, cfg)
        out_mesh = UsdGeom.Mesh(stage.GetPrimAtPath("/StaticCloth"))
        samples = out_mesh.GetPointsAttr().GetTimeSamples()
        self.assertIn(6.0, samples)
        self.assertIn(10.0, samples)
        self.assertIn(14.0, samples)
        # Frame 6 (outside fadeIn) = 0.0, Frame 8 (halfway) = 0.2, Frame 10 (peak) = 0.4, Frame 14 = 0.0
        self.assertAlmostEqual(float(np.asarray(out_mesh.GetPointsAttr().Get(6.0))[0, 1]), 0.0, places=5)
        self.assertAlmostEqual(float(np.asarray(out_mesh.GetPointsAttr().Get(8.0))[0, 1]), 0.2, places=5)
        self.assertAlmostEqual(float(np.asarray(out_mesh.GetPointsAttr().Get(10.0))[0, 1]), 0.4, places=5)
        self.assertAlmostEqual(float(np.asarray(out_mesh.GetPointsAttr().Get(14.0))[0, 1]), 0.0, places=5)

    def test_dense_96k_cloth_proxy_solver(self):
        """Verify under-the-hood coarse proxy activates on a 96k-vert dress-scale mesh."""
        import time
        from pxr import Usd, UsdGeom, Vt

        ce.reset_cache_sessions()
        stage = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)

        # 310x310 = 96,100 vertices (~191k tris, matching cloth_parent's 95,903 verts)
        cloth_pts, cloth_cnt, cloth_idx = _make_grid_mesh(310, 310, size=1.0, z_height=0.0)
        cloth = UsdGeom.Mesh.Define(stage, "/Scene/DenseDress")
        cloth.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(cloth_pts))
        cloth.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(cloth_cnt))
        cloth.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(cloth_idx))

        cam_basis = {
            "origin": (0.0, 2.0, 0.0),
            "forward": (0.0, -1.0, 0.0),
            "right": (1.0, 0.0, 0.0),
            "up": (0.0, 0.0, -1.0),
            "tan_half_fov": 0.41421356,
            "aspect": 1.0,
        }
        cfg = {
            "targetQuery": "/Scene/DenseDress",
            "colliderQuery": "__none__",
            "activeLayerId": "layer-1",
            "clothSubsteps": 4,
            "clothIterations": 8,
            "clothStretchStiffness": 0.95,
            "clothBendStiffness": 0.35,
            "clothDamping": 0.08,
            "clothGravity": 9.81,
            "clothCollisionMargin": 0.005,
            "clothFriction": 0.3,
            "clothDragPinFalloff": 1.4,
            "layers": [{"id": "layer-1", "name": "Layer 1", "enabled": True, "weight": 1.0, "mode": "allFrames"}],
        }

        t0 = time.perf_counter()
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            ce.step_cache_brush(
                stage, cfg,
                {"tool": "clothDrag", "nx": 0.5, "ny": 0.5, "prevNx": 0.5, "prevNy": 0.5,
                 "radius": 0.2, "strength": 1.0, "newStroke": True, "strokeId": 100},
                cam_basis, frame=0.0,
            )
        t_init_ms = (time.perf_counter() - t0) * 1000.0

        # Confirm proxy activated automatically under the hood
        sess = next(iter(ce._CACHE_SESSIONS.values()))
        self.assertTrue(sess.use_proxy)
        self.assertLess(sess.n_sim, 10000)

        t1 = time.perf_counter()
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            updates = ce.step_cache_brush(
                stage, cfg,
                {"tool": "clothDrag", "nx": 0.56, "ny": 0.5, "prevNx": 0.5, "prevNy": 0.5,
                 "radius": 0.2, "strength": 1.0, "newStroke": False, "strokeId": 100},
                cam_basis, frame=0.0,
            )
        t_step_ms = (time.perf_counter() - t1) * 1000.0
        print(f"[Dense 96k proxy] n_verts={sess.n_verts}, n_proxy={sess.n_sim}, init={t_init_ms:.1f}ms, step={t_step_ms:.1f}ms")

        self.assertEqual(len(updates), 1)
        pts_after = np.asarray(cloth.GetPointsAttr().Get(), dtype=np.float32)
        self.assertTrue(np.all(np.isfinite(pts_after)))
        # Corner outside patch remains anchored; center deforms
        np.testing.assert_allclose(pts_after[0], cloth_pts[0], atol=1e-5)
        self.assertGreater(float(np.max(np.abs(pts_after - cloth_pts))), 0.005)

    def test_skip_simulation_does_not_poison_node_result_cache(self):
        """Regression: scene_children (_SKIP_SIMULATION=True) must never poison _NODE_RESULT_CACHE."""
        from pxr import Usd, UsdGeom, Vt
        import usd_graph

        pts, counts, faces = _make_grid_mesh(9, 9, size=1.0, z_height=0.5)
        with tempfile.TemporaryDirectory() as tmp:
            src_path = os.path.join(tmp, "sim_src.usda")
            src_stage = Usd.Stage.CreateNew(src_path)
            UsdGeom.SetStageUpAxis(src_stage, UsdGeom.Tokens.y)
            UsdGeom.SetStageMetersPerUnit(src_stage, 1.0)
            m = UsdGeom.Mesh.Define(src_stage, "/Cloth")
            m.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(pts))
            m.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(counts))
            m.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(faces))
            src_stage.SetDefaultPrim(m.GetPrim())
            src_stage.GetRootLayer().Save()

            graph = {
                "targetNodeId": "ce-sim",
                "dependencies": [{"id": "dep-1", "kind": "usd", "path": src_path}],
                "nodes": [
                    {
                        "id": "src-1",
                        "operation": "source",
                        "inputDependencyId": "dep-1",
                        "axis": "preserve",
                        "frontAxis": "preserve",
                        "scale": 1.0,
                        "transform": {"translation": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
                    },
                    {
                        "id": "ce-sim",
                        "operation": "cache_edit",
                        "axis": "preserve",
                        "frontAxis": "preserve",
                        "scale": 1.0,
                        "transform": {"translation": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]},
                        "cacheEdit": {
                            "targetQuery": "",
                            "colliderQuery": "__none__",
                            "activeLayerId": "layer-1",
                            "simEnabled": True,
                            "simMode": "animated",
                            "simStartFrame": 0,
                            "simEndFrame": 6,
                            "simSettleSteps": 2,
                            "layers": [
                                {"id": "layer-1", "name": "Layer 1", "enabled": True, "weight": 1.0, "mode": "allFrames"}
                            ],
                        },
                    },
                ],
                "edges": [{"id": "e1", "source": "src-1", "target": "ce-sim"}],
            }

            # 1. Simulate scene_children calling compile_graph with _SKIP_SIMULATION=True
            usd_graph._SKIP_SIMULATION = True
            try:
                skipped_stage, _, _ = usd_graph.compile_graph(graph)
            finally:
                usd_graph._SKIP_SIMULATION = False
            m_skip = [p for p in skipped_stage.Traverse() if p.IsA(UsdGeom.Mesh)][0]
            self.assertEqual(len(UsdGeom.Mesh(m_skip).GetPointsAttr().GetTimeSamples()), 0)

            # 2. Immediately call compile_graph for rendering (_SKIP_SIMULATION=False)
            sim_stage, _, _ = usd_graph.compile_graph(graph)
            m_sim = [p for p in sim_stage.Traverse() if p.IsA(UsdGeom.Mesh)][0]
            self.assertGreaterEqual(len(UsdGeom.Mesh(m_sim).GetPointsAttr().GetTimeSamples()), 7)

    def test_simulate_in_sublayer_animated_cloth_with_collider_pins_and_sculpt_layer(self):
        from pxr import Sdf, Usd, UsdGeom, Vt

        ce.reset_cache_sessions()
        cloth_pts, cloth_cnt, cloth_idx = _make_grid_mesh(13, 13, size=0.8, z_height=0.0)
        sph_pts, sph_cnt, sph_idx = _make_sphere_mesh(center=(0.0, -0.18, 0.0), radius=0.16)

        with tempfile.TemporaryDirectory() as tmp:
            src_path = os.path.join(tmp, "garment_and_body.usda")
            src_stage = Usd.Stage.CreateNew(src_path)
            UsdGeom.SetStageUpAxis(src_stage, UsdGeom.Tokens.y)
            UsdGeom.SetStageMetersPerUnit(src_stage, 1.0)
            src_stage.SetStartTimeCode(0)
            src_stage.SetEndTimeCode(10)

            cloth = UsdGeom.Mesh.Define(src_stage, "/Scene/Cloth")
            cloth.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(cloth_pts))
            cloth.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(cloth_cnt))
            cloth.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(cloth_idx))

            body = UsdGeom.Mesh.Define(src_stage, "/Scene/Body")
            body.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(sph_cnt))
            body.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(sph_idx))
            b_pts_attr = body.CreatePointsAttr()
            for f in range(0, 11):
                shifted = sph_pts.copy()
                shifted[:, 0] += 0.02 * f  # Body translates +0.20m in +X over frames 0..10
                b_pts_attr.Set(Vt.Vec3fArray.FromNumpy(shifted), float(f))
            src_stage.GetRootLayer().Save()

            with open(src_path, "rb") as fh:
                sha_before = hashlib.sha256(fh.read()).hexdigest()

            root_layer = Sdf.Layer.CreateAnonymous("sim_root.usda")
            root_layer.subLayerPaths = [src_path]
            stage = Usd.Stage.Open(root_layer)

            # Pin the center vertex (right above the sphere top) via maskWeights
            center_idx = (13 // 2) * 13 + (13 // 2)
            mask = np.zeros((cloth_pts.shape[0],), dtype=np.float32)
            mask[center_idx] = 1.0
            mask_entry = ce.encode_mesh_mask(mask)

            # Add a Sculpt Layer stacking on top of the simulation at frame 10
            sculpt_delta = np.zeros_like(cloth_pts)
            sculpt_delta[0] = [0.0, 0.15, 0.0]

            cfg = {
                "targetQuery": "/Scene/Cloth",
                "colliderQuery": "/Scene/Body",
                "simEnabled": True,
                "simMode": "animated",
                "simStartFrame": 0.0,
                "simEndFrame": 10.0,
                "simFps": 24.0,
                "simSettleSteps": 6,
                "simFabricPreset": "cotton",
                "simAirDrag": 0.08,
                "simWind": [0.5, 0.0, 0.0],
                "simSelfCollision": True,
                "simSelfThickness": 0.005,
                "simPinToCollider": True,
                "clothSubsteps": 4,
                "clothIterations": 8,
                "clothStretchStiffness": 0.96,
                "clothBendStiffness": 0.3,
                "clothDamping": 0.08,
                "clothGravity": 9.81,
                "clothCollisionMargin": 0.008,
                "clothFriction": 0.35,
                "clothSimScale": 1.0,
                "maskWeights": {"/Scene/Cloth": mask_entry},
                "layers": [
                    {
                        "id": "l-fix",
                        "name": "Corner Lift",
                        "enabled": True,
                        "weight": 1.0,
                        "mode": "singleFrame",
                        "centerFrame": 10.0,
                        "fadeIn": 0.0,
                        "fadeOut": 0.0,
                        "falloff": "constant",
                        "deltas": {"/Scene/Cloth": ce.encode_mesh_delta(sculpt_delta)},
                    }
                ],
            }

            ce.apply_cache_edit(stage, cfg)
            out_cloth = UsdGeom.Mesh(stage.GetPrimAtPath("/Scene/Cloth"))
            samples = out_cloth.GetPointsAttr().GetTimeSamples()
            self.assertEqual(len(samples), 11)
            self.assertEqual(samples[0], 0.0)
            self.assertEqual(samples[-1], 10.0)

            p0 = np.asarray(out_cloth.GetPointsAttr().Get(0.0), dtype=np.float32)
            p10 = np.asarray(out_cloth.GetPointsAttr().Get(10.0), dtype=np.float32)

            # Free edges should have draped downward under gravity
            self.assertLess(float(p10[0, 1]) - 0.15, float(cloth_pts[0, 1]) - 0.02)

            wp, _dev = ck.get_warp()
            if wp is not None:
                # Pinned center vertex should have tracked the moving sphere in +X (~+0.20m)
                self.assertGreater(float(p10[center_idx, 0] - p0[center_idx, 0]), 0.14)
                # No vertex penetrates the sphere at frame 10 (center at (0.20, -0.18, 0.0), r=0.16)
                sphere_c10 = np.array([[0.20, -0.18, 0.0]], dtype=np.float32)
                min_dist = float(np.min(np.linalg.norm(p10 - sphere_c10, axis=1)))
                self.assertGreaterEqual(min_dist, 0.155)

            # Verify LRU cache hit on identical re-evaluation without sculpt layer
            cfg_no_layer = {**cfg, "layers": []}
            root2 = Sdf.Layer.CreateAnonymous("sim_root2.usda")
            root2.subLayerPaths = [src_path]
            stage2 = Usd.Stage.Open(root2)
            cache_size_before = len(ce._CLOTH_SIM_CACHE)
            ce.apply_cache_edit(stage2, cfg_no_layer)
            self.assertEqual(len(ce._CLOTH_SIM_CACHE), cache_size_before)
            p10_raw = np.asarray(UsdGeom.Mesh(stage2.GetPrimAtPath("/Scene/Cloth")).GetPointsAttr().Get(10.0), dtype=np.float32)
            # Sculpt layer lifted vertex 0 by exactly +0.15 on Y
            self.assertAlmostEqual(float(p10[0, 1] - p10_raw[0, 1]), 0.15, places=4)

            with open(src_path, "rb") as fh:
                sha_after = hashlib.sha256(fh.read()).hexdigest()
            self.assertEqual(sha_before, sha_after)

    def test_simulate_in_sublayer_static_drape(self):
        from pxr import Sdf, Usd, UsdGeom, Vt

        ce.reset_cache_sessions()
        cloth_pts, cloth_cnt, cloth_idx = _make_grid_mesh(11, 11, size=0.8, z_height=0.0)
        sph_pts, sph_cnt, sph_idx = _make_sphere_mesh(center=(0.0, -0.18, 0.0), radius=0.16)

        src_layer = Sdf.Layer.CreateAnonymous("static_drape_src.usda")
        src_stage = Usd.Stage.Open(src_layer)
        UsdGeom.SetStageUpAxis(src_stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(src_stage, 1.0)

        cloth = UsdGeom.Mesh.Define(src_stage, "/Cloth")
        cloth.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(cloth_pts))
        cloth.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(cloth_cnt))
        cloth.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(cloth_idx))

        body = UsdGeom.Mesh.Define(src_stage, "/Body")
        body.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(sph_pts))
        body.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(sph_cnt))
        body.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(sph_idx))

        root = Sdf.Layer.CreateAnonymous("static_drape_root.usda")
        root.subLayerPaths = [src_layer.identifier]
        stage = Usd.Stage.Open(root)

        cfg = {
            "targetQuery": "/Cloth",
            "colliderQuery": "/Body",
            "simEnabled": True,
            "simMode": "static",
            "simSettleSteps": 16,
            "clothGravity": 9.81,
            "clothCollisionMargin": 0.008,
            "simSelfCollision": True,
            "simSelfThickness": 0.005,
        }
        ce.apply_cache_edit(stage, cfg)
        draped = np.asarray(UsdGeom.Mesh(stage.GetPrimAtPath("/Cloth")).GetPointsAttr().Get(), dtype=np.float32)
        # Corners fell under gravity
        self.assertLess(float(draped[0, 1]), -0.02)
        wp, _dev = ck.get_warp()
        if wp is not None:
            min_dist = float(np.min(np.linalg.norm(draped - np.array([[0.0, -0.18, 0.0]], dtype=np.float32), axis=1)))
            self.assertGreaterEqual(min_dist, 0.155)

    def test_simulate_in_sublayer_woven_cloth_inextensibility(self):
        from pxr import Sdf, Usd, UsdGeom, Vt

        ce.reset_cache_sessions()
        # Vertical 1.0m x 1.0m hanging cloth panel (17x17) pinned along its top row (y = 1.0)
        xs = np.linspace(-0.5, 0.5, 17, dtype=np.float32)
        ys = np.linspace(1.0, 0.0, 17, dtype=np.float32)
        pts = np.array([(float(x), float(y), 0.0) for y in ys for x in xs], dtype=np.float32)
        faces, counts = [], []
        for j in range(16):
            for i in range(16):
                v0 = j * 17 + i
                v1 = v0 + 1
                v2 = v0 + 18
                v3 = v0 + 17
                faces.extend([v0, v2, v1, v0, v3, v2])
                counts.extend([3, 3])
        counts = np.asarray(counts, dtype=np.int32)
        faces = np.asarray(faces, dtype=np.int32)

        stage = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        cloth = UsdGeom.Mesh.Define(stage, "/Curtain")
        cloth.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(pts))
        cloth.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(counts))
        cloth.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(faces))

        mask = np.zeros((pts.shape[0],), dtype=np.float32)
        mask[:17] = 1.0  # Pin entire top row

        cfg = {
            "targetQuery": "/Curtain",
            "colliderQuery": "__none__",
            "simEnabled": True,
            "simMode": "animated",
            "simStartFrame": 0.0,
            "simEndFrame": 24.0,
            "simFps": 24.0,
            "simSettleSteps": 8,
            "clothSubsteps": 4,
            "clothIterations": 8,
            "clothStretchStiffness": 0.96,
            "clothBendStiffness": 0.30,
            "clothGravity": 9.81,
            "simSelfCollision": False,
            "maskWeights": {"/Curtain": ce.encode_mesh_mask(mask)},
        }
        ce.apply_cache_edit(stage, cfg)
        p24 = np.asarray(cloth.GetPointsAttr().Get(24.0), dtype=np.float32)
        # Rest height is 1.0m (top y=1.0, bottom y=0.0). Woven cloth must not sag past ~2.5% stretch.
        bottom_y = float(p24[-17:, 1].min())
        total_height = 1.0 - bottom_y
        self.assertLess(total_height, 1.025)

    def test_manual_sim_mode_and_step_cloth_simulation_from_current_frame(self):
        """Interactive manual mode must not auto-simulate on compile; step_cloth_simulation advances from current frame and caches for Play."""
        from pxr import Sdf, Usd, UsdGeom, Vt
        import usd_graph

        ce.clear_cloth_sim_cache()
        cloth_pts, cloth_cnt, cloth_idx = _make_grid_mesh(9, 9, size=0.6, z_height=0.0)
        stage = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        cloth = UsdGeom.Mesh.Define(stage, "/Scene/Drape")
        cloth.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(cloth_pts))
        cloth.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(cloth_cnt))
        cloth.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(cloth_idx))

        cfg = {
            "targetQuery": "/Scene/Drape",
            "colliderQuery": "__none__",
            "simEnabled": True,
            "simMode": "animated",
            "simStartFrame": 0,
            "simEndFrame": 12,
            "clothSubsteps": 4,
            "clothIterations": 8,
            "clothGravity": 9.81,
            "simCacheVersion": 1,
        }

        # 1. Interactive manual mode: apply_cache_edit must NOT auto-simulate when cache is empty
        prev_manual = getattr(usd_graph, "_INTERACTIVE_MANUAL_SIM", False)
        usd_graph._INTERACTIVE_MANUAL_SIM = True
        try:
            ce.apply_cache_edit(stage, cfg)
            self.assertEqual(len(cloth.GetPointsAttr().GetTimeSamples()), 0)

            # 2. Simulate button pressed at frame 5 -> steps 5, 6, 7, 8
            for f in (5, 6, 7, 8):
                ups = ce.step_cloth_simulation(stage, cfg, f, origin_frame=5, session_id=1)
                self.assertEqual(len(ups), 1)
                self.assertEqual(ups[0][0], "/Scene/Drape")

            ts = list(cloth.GetPointsAttr().GetTimeSamples())
            self.assertEqual(ts, [5.0, 6.0, 7.0, 8.0])
            p5 = np.asarray(cloth.GetPointsAttr().Get(5.0), dtype=np.float32)
            p8 = np.asarray(cloth.GetPointsAttr().Get(8.0), dtype=np.float32)
            # Cloth must fall under gravity between frame 5 and frame 8
            self.assertLess(float(p8[:, 1].mean()), float(p5[:, 1].mean()) - 0.02)

            # 3. Pressing Simulate again from frame 6 replaces the cache from frame 6 onwards
            #    (preserves frame 5, drops stale frames 7..8 until re-simulated)
            ups_replace = ce.step_cloth_simulation(stage, cfg, 6, origin_frame=6, session_id=2)
            self.assertEqual(len(ups_replace), 1)
            self.assertEqual(list(cloth.GetPointsAttr().GetTimeSamples()), [5.0, 6.0])
            # Advance to frame 7 in session 2
            ce.step_cloth_simulation(stage, cfg, 7, origin_frame=6, session_id=2)
            self.assertEqual(list(cloth.GetPointsAttr().GetTimeSamples()), [5.0, 6.0, 7.0])
            p7_new = np.asarray(cloth.GetPointsAttr().Get(7.0), dtype=np.float32)

            # 4. Re-evaluating apply_cache_edit in manual mode replays cached frames without re-simulating
            src_stage2 = Usd.Stage.CreateInMemory()
            UsdGeom.SetStageUpAxis(src_stage2, UsdGeom.Tokens.y)
            UsdGeom.SetStageMetersPerUnit(src_stage2, 1.0)
            cloth_src2 = UsdGeom.Mesh.Define(src_stage2, "/Scene/Drape")
            cloth_src2.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(cloth_pts))
            cloth_src2.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(cloth_cnt))
            cloth_src2.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(cloth_idx))
            wrap_layer2 = Sdf.Layer.CreateAnonymous("wrap2.usda")
            wrap_layer2.subLayerPaths = [src_stage2.GetRootLayer().identifier]
            stage2 = Usd.Stage.Open(wrap_layer2)
            ce.apply_cache_edit(stage2, cfg)
            cloth2 = UsdGeom.Mesh(stage2.GetPrimAtPath("/Scene/Drape"))
            self.assertEqual(list(cloth2.GetPointsAttr().GetTimeSamples()), [5.0, 6.0, 7.0])
            np.testing.assert_allclose(
                np.asarray(cloth2.GetPointsAttr().Get(7.0), dtype=np.float32),
                p7_new,
                atol=1e-5,
            )
        finally:
            usd_graph._INTERACTIVE_MANUAL_SIM = prev_manual
            ce.clear_cloth_sim_cache()


if __name__ == "__main__":
    unittest.main()
