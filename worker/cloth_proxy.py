"""Under-the-hood coarse spatial-clustering cloth proxy for dense meshes.

When a sculpted garment has > `PROXY_VERTEX_THRESHOLD` vertices (e.g., 95k-vert
subdivided or solidified dress), direct per-vertex Jacobi XPBD converges slowly
because tension only travels 1 edge ring per iteration.

This module automatically builds a compact (~3k–5k node) proxy graph from the
fine mesh via surface-area-adaptive 3D voxel clustering, solves cloth XPBD on
the coarse proxy, and smoothly reconstructs all fine vertices using 4-neighbor
inverse-distance displacement interpolation (preserving fine wrinkles, seams,
and solidified shell thickness with zero visible double meshes).
"""

from __future__ import annotations

import numpy as np

PROXY_VERTEX_THRESHOLD = 8000
TARGET_PROXY_NODES = 4000


def build_cloth_proxy(
    verts_world: np.ndarray,
    tris: np.ndarray,
    target_nodes: int = TARGET_PROXY_NODES,
):
    """Build a coarse cloth proxy graph and 4-neighbor fine-to-proxy binding.

    Returns a dict with:
    - `n_proxy`: int
    - `proxy_pos`: `(P, 3)` float32
    - `proxy_normals`: `(P, 3)` float32
    - `vert_to_cluster`: `(N,)` int32
    - `cluster_counts`: `(P,)` int32
    - `edge_v0`, `edge_v1`, `edge_rest`: structural proxy edges
    - `bend_v0`, `bend_v1`, `bend_rest`: interior proxy bending pairs
    - `bind_idx`: `(N * 4,)` int32 — 4 bound proxy node indices per fine vertex
    - `bind_w`: `(N * 4,)` float32 — normalized weights summing to 1.0
    """
    pos = np.ascontiguousarray(verts_world, dtype=np.float32)
    n_verts = int(pos.shape[0])
    t2d = np.ascontiguousarray(tris, dtype=np.int32).reshape(-1, 3)

    # 1. Surface-area-adaptive voxel cell size
    v0 = pos[t2d[:, 0]]
    v1 = pos[t2d[:, 1]]
    v2 = pos[t2d[:, 2]]
    face_cross = np.cross(v1 - v0, v2 - v0)
    face_areas = 0.5 * np.linalg.norm(face_cross, axis=1)
    total_area = float(np.sum(face_areas))
    bbox_min = pos.min(axis=0)
    bbox_max = pos.max(axis=0)
    bbox_diag = float(np.linalg.norm(bbox_max - bbox_min))

    if total_area > 1.0e-8:
        cell = float(np.sqrt(total_area / max(200, int(target_nodes))))
    else:
        cell = max(1.0e-4, bbox_diag / 64.0)
    cell = max(cell, bbox_diag * 1.0e-4, 1.0e-5)

    # 2. Vectorized 3D voxel clustering
    ijk = np.floor((pos - bbox_min[None, :]) / cell).astype(np.int64)
    # Pack (ix, iy, iz) into a single int64 key (20 bits each -> up to 1M voxels/axis)
    keys = (ijk[:, 0] << 40) | ((ijk[:, 1] & 0xFFFFF) << 20) | (ijk[:, 2] & 0xFFFFF)
    _uniq, vert_to_cluster, cluster_counts = np.unique(
        keys, return_inverse=True, return_counts=True
    )
    vert_to_cluster = vert_to_cluster.astype(np.int32)
    cluster_counts = cluster_counts.astype(np.int32)
    n_proxy = int(cluster_counts.size)

    # 3. Proxy node positions & normals (vectorized centroid + averaged normals)
    proxy_pos = np.zeros((n_proxy, 3), dtype=np.float32)
    np.add.at(proxy_pos, vert_to_cluster, pos)
    proxy_pos /= np.maximum(1, cluster_counts)[:, None].astype(np.float32)

    fine_normals = np.zeros_like(pos, dtype=np.float32)
    np.add.at(fine_normals, t2d[:, 0], face_cross)
    np.add.at(fine_normals, t2d[:, 1], face_cross)
    np.add.at(fine_normals, t2d[:, 2], face_cross)
    proxy_normals = np.zeros((n_proxy, 3), dtype=np.float32)
    np.add.at(proxy_normals, vert_to_cluster, fine_normals)
    pn_len = np.linalg.norm(proxy_normals, axis=1, keepdims=True)
    valid_n = pn_len[:, 0] > 1.0e-8
    proxy_normals[valid_n] /= pn_len[valid_n]
    proxy_normals[~valid_n] = np.array([0.0, 1.0, 0.0], dtype=np.float32)

    # 4. Vectorized structural proxy edges from fine triangles
    ct = vert_to_cluster[t2d]  # (F, 3)
    e_a = np.concatenate([ct[:, 0], ct[:, 1], ct[:, 2]])
    e_b = np.concatenate([ct[:, 1], ct[:, 2], ct[:, 0]])
    diff_mask = e_a != e_b
    e_a = e_a[diff_mask]
    e_b = e_b[diff_mask]
    lo = np.minimum(e_a, e_b).astype(np.int64)
    hi = np.maximum(e_a, e_b).astype(np.int64)
    edge_keys = (lo << 32) | hi
    uniq_edges = np.unique(edge_keys)
    edge_v0 = (uniq_edges >> 32).astype(np.int32)
    edge_v1 = (uniq_edges & 0xFFFFFFFF).astype(np.int32)
    if edge_v0.size > 0:
        edge_rest = np.linalg.norm(proxy_pos[edge_v1] - proxy_pos[edge_v0], axis=1).astype(np.float32)
        edge_rest = np.maximum(edge_rest, 1.0e-5)
    else:
        edge_rest = np.zeros((0,), dtype=np.float32)

    # 5. Proxy bending pairs from non-degenerate proxy triangles
    valid_tri = (ct[:, 0] != ct[:, 1]) & (ct[:, 1] != ct[:, 2]) & (ct[:, 2] != ct[:, 0])
    ptris = ct[valid_tri]
    bend_v0_list = []
    bend_v1_list = []
    if ptris.shape[0] > 0:
        # Deduplicate proxy triangles by sorted vertex triple
        ptris_sorted = np.sort(ptris, axis=1)
        tri_keys = (
            (ptris_sorted[:, 0].astype(np.int64) << 40)
            | (ptris_sorted[:, 1].astype(np.int64) << 20)
            | ptris_sorted[:, 2].astype(np.int64)
        )
        _, uniq_ti = np.unique(tri_keys, return_index=True)
        ptris = ptris[uniq_ti]
        edge_to_opp: dict[tuple[int, int], int] = {}
        for c0, c1, c2 in ptris.tolist():
            for a, b, opp in ((c0, c1, c2), (c1, c2, c0), (c2, c0, c1)):
                k = (a, b) if a < b else (b, a)
                prev = edge_to_opp.get(k)
                if prev is None:
                    edge_to_opp[k] = opp
                elif prev >= 0 and prev != opp:
                    bend_v0_list.append(prev)
                    bend_v1_list.append(opp)
                    edge_to_opp[k] = -1  # already paired
    proxy_tris = np.ascontiguousarray(ptris.reshape(-1), dtype=np.int32) if ptris.shape[0] > 0 else np.zeros((0,), dtype=np.int32)
    bend_v0 = np.asarray(bend_v0_list, dtype=np.int32)
    bend_v1 = np.asarray(bend_v1_list, dtype=np.int32)
    if bend_v0.size > 0:
        bend_rest = np.linalg.norm(proxy_pos[bend_v1] - proxy_pos[bend_v0], axis=1).astype(np.float32)
        bend_rest = np.maximum(bend_rest, 1.0e-5)
    else:
        bend_rest = np.zeros((0,), dtype=np.float32)

    # 6. Proxy 1-ring CSR adjacency + up to 8 candidate neighbor clusters per proxy node
    max_cand = 8
    cand_table = np.repeat(np.arange(n_proxy, dtype=np.int32)[:, None], max_cand, axis=1)
    cand_fill = np.ones((n_proxy,), dtype=np.int32)
    adj_lists: list[list[int]] = [[] for _ in range(n_proxy)]
    for a, b in zip(edge_v0.tolist(), edge_v1.tolist()):
        adj_lists[a].append(b)
        adj_lists[b].append(a)
        fa = int(cand_fill[a])
        if fa < max_cand:
            cand_table[a, fa] = b
            cand_fill[a] = fa + 1
        fb = int(cand_fill[b])
        if fb < max_cand:
            cand_table[b, fb] = a
            cand_fill[b] = fb + 1
    adj_offsets = np.zeros((n_proxy + 1,), dtype=np.int32)
    adj_neighbors_list: list[int] = []
    for i, nbrs in enumerate(adj_lists):
        adj_neighbors_list.extend(nbrs)
        adj_offsets[i + 1] = len(adj_neighbors_list)
    adj_neighbors = np.asarray(adj_neighbors_list, dtype=np.int32)

    # 7. Vectorized 4-nearest smooth IDW binding per fine vertex
    # Gather the 8 candidate proxy clusters for every fine vertex: shape (N, 8)
    v_cands = cand_table[vert_to_cluster]  # (N, 8)
    cand_pts = proxy_pos[v_cands]  # (N, 8, 3)
    d2 = np.sum((cand_pts - pos[:, None, :]) ** 2, axis=2)  # (N, 8)

    # Mask duplicate padded entries beyond cand_fill[c]
    col_idx = np.arange(max_cand, dtype=np.int32)[None, :]
    valid_col = col_idx < cand_fill[vert_to_cluster][:, None]
    d2 = np.where(valid_col, d2, np.float32(1.0e30))

    # Pick 4 nearest candidates per fine vertex
    top4 = np.argpartition(d2, kth=3, axis=1)[:, :4]
    rows = np.arange(n_verts, dtype=np.int32)[:, None]
    bind_idx_2d = v_cands[rows, top4].astype(np.int32)
    bind_d2 = d2[rows, top4]
    valid_4 = bind_d2 < np.float32(1.0e29)

    eps2 = np.float32((cell * 0.25) ** 2 + 1.0e-10)
    raw_w = np.where(valid_4, np.float32(1.0) / (bind_d2 + eps2), np.float32(0.0))
    w_sum = np.sum(raw_w, axis=1, keepdims=True)
    # Safety: if somehow all 0, fall back to weight 1.0 on own cluster
    bad = w_sum[:, 0] <= 1.0e-12
    if np.any(bad):
        raw_w[bad, 0] = 1.0
        bind_idx_2d[bad, 0] = vert_to_cluster[bad]
        w_sum = np.sum(raw_w, axis=1, keepdims=True)
    bind_w_2d = (raw_w / w_sum).astype(np.float32)

    return {
        "n_proxy": n_proxy,
        "proxy_pos": np.ascontiguousarray(proxy_pos, dtype=np.float32),
        "proxy_normals": np.ascontiguousarray(proxy_normals, dtype=np.float32),
        "vert_to_cluster": vert_to_cluster,
        "cluster_counts": cluster_counts,
        "tris": proxy_tris,
        "edge_v0": edge_v0,
        "edge_v1": edge_v1,
        "edge_rest": edge_rest,
        "bend_v0": bend_v0,
        "bend_v1": bend_v1,
        "bend_rest": bend_rest,
        "adj_offsets": adj_offsets,
        "adj_neighbors": adj_neighbors,
        "bind_idx": np.ascontiguousarray(bind_idx_2d.reshape(-1), dtype=np.int32),
        "bind_w": np.ascontiguousarray(bind_w_2d.reshape(-1), dtype=np.float32),
    }


def aggregate_proxy_mask(fine_mask: np.ndarray, vert_to_cluster: np.ndarray, n_proxy: int) -> np.ndarray:
    """Aggregate fine-vertex mask weights `[0, 1]` onto proxy nodes (max pin per cluster)."""
    pm = np.zeros((n_proxy,), dtype=np.float32)
    if fine_mask.size > 0 and n_proxy > 0:
        np.maximum.at(pm, vert_to_cluster, np.clip(fine_mask, 0.0, 1.0))
    return pm


def recompute_proxy_positions(
    fine_pos: np.ndarray,
    vert_to_cluster: np.ndarray,
    cluster_counts: np.ndarray,
    n_proxy: int,
) -> np.ndarray:
    """Recompute proxy node centroids from current fine vertex positions (e.g. after sculpt strokes)."""
    pp = np.zeros((n_proxy, 3), dtype=np.float32)
    np.add.at(pp, vert_to_cluster, fine_pos)
    pp /= np.maximum(1, cluster_counts)[:, None].astype(np.float32)
    return pp
