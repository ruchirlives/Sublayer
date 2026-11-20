"""Topology-modifying and drag-line hair brushes (`lengthen`, `shorten`, `cut`, `add`, `delete`).

These brushes run on CPU/NumPy + Warp utility kernels and rebuild or update
USD BasisCurves topology (`curveVertexCounts`, `points`, `widths`) along with
the active GPU simulation session.
"""

from hair_kernels import _build_kernels


def _build_length_gpu_kernels(wp):
    cached = getattr(wp, "_orchflow_length_gpu_kernels", None)
    if cached is not None:
        return cached

    @wp.kernel
    def lengthen_drag_gpu_kernel(
        pos: wp.array(dtype=wp.vec3),
        out_pos: wp.array(dtype=wp.vec3),
        starts: wp.array(dtype=int),
        counts: wp.array(dtype=int),
        pinned: wp.array(dtype=int),
        eye: wp.vec3,
        fwd: wp.vec3,
        r1: wp.vec3,
        drag_unnorm: wp.vec3,
        r2: float,
        changed_flag: wp.array(dtype=int),
    ):
        s = wp.tid()
        if pinned[s] != 0:
            return
        v0 = starts[s]
        c = counts[s]
        if c < 2:
            return
        hit = int(0)
        for k in range(c):
            to_v = pos[v0 + k] - eye
            t_along = wp.dot(to_v, r1)
            if t_along > 0.0:
                closest = eye + r1 * t_along
                d2 = wp.length_sq(pos[v0 + k] - closest)
                if d2 <= r2:
                    hit = 1
                    break
        if hit == 0:
            return
        p_tip = pos[v0 + c - 1]
        z_tip = wp.dot(p_tip - eye, fwd)
        if z_tip <= 1e-5:
            return
        p_end = p_tip + drag_unnorm * z_tip
        ext_seg = wp.length(p_end - p_tip)
        if ext_seg <= 1e-6:
            return
        total_len = float(0.0)
        for k in range(c - 1):
            total_len += wp.length(pos[v0 + k + 1] - pos[v0 + k])
        new_total = total_len + ext_seg
        out_pos[v0] = pos[v0]
        seg_idx = int(0)
        seg_start_s = float(0.0)
        seg_end_s = wp.length(pos[v0 + 1] - pos[v0]) if c > 1 else ext_seg
        for k in range(1, c):
            s_target = new_total * (float(k) / float(c - 1))
            while seg_idx < c - 1 and s_target > seg_end_s + 1e-7:
                seg_idx += 1
                seg_start_s = seg_end_s
                if seg_idx < c - 1:
                    seg_end_s += wp.length(pos[v0 + seg_idx + 1] - pos[v0 + seg_idx])
                else:
                    seg_end_s += ext_seg
            p_a = pos[v0 + seg_idx]
            p_b = pos[v0 + seg_idx + 1] if seg_idx < c - 1 else p_end
            span = wp.max(seg_end_s - seg_start_s, 1e-8)
            frac = wp.clamp((s_target - seg_start_s) / span, 0.0, 1.0)
            out_pos[v0 + k] = p_a + (p_b - p_a) * frac
        wp.atomic_add(changed_flag, 0, 1)

    @wp.kernel
    def length_adjust_gpu_kernel(
        pos: wp.array(dtype=wp.vec3),
        out_pos: wp.array(dtype=wp.vec3),
        starts: wp.array(dtype=int),
        counts: wp.array(dtype=int),
        pinned: wp.array(dtype=int),
        ro: wp.vec3,
        rd: wp.vec3,
        center: wp.vec3,
        use_ray: int,
        r2: float,
        scaled_radius: float,
        strength: float,
        is_lengthen: int,
        min_strand_len: float,
        changed_flag: wp.array(dtype=int),
    ):
        s = wp.tid()
        if pinned[s] != 0:
            return
        v0 = starts[s]
        c = counts[s]
        if c < 2:
            return
        best_d2 = float(1e30)
        for k in range(c):
            p = pos[v0 + k]
            if use_ray != 0:
                t_proj = wp.max(0.0, wp.dot(p - ro, rd))
                closest = ro + rd * t_proj
                d2 = wp.length_sq(p - closest)
            else:
                d2 = wp.length_sq(p - center)
            if d2 < best_d2:
                best_d2 = d2
        if best_d2 > r2:
            return
        total_len = float(0.0)
        for k in range(c - 1):
            total_len += wp.length(pos[v0 + k + 1] - pos[v0 + k])
        if total_len <= 1e-7:
            return
        w = wp.max(0.0, 1.0 - wp.sqrt(best_d2) / wp.max(scaled_radius, 1e-6))
        if w <= 0.0:
            return
        rate = wp.min(0.35, 0.08 * wp.max(0.1, strength) * w)
        if is_lengthen != 0:
            add_len = total_len * rate
            tip_vec = pos[v0 + c - 1] - pos[v0 + c - 2]
            tip_n = wp.length(tip_vec)
            if tip_n <= 1e-8:
                tip_vec = pos[v0 + c - 1] - pos[v0]
                tip_n = wp.length(tip_vec)
            if tip_n <= 1e-8:
                return
            p_end = pos[v0 + c - 1] + (tip_vec / tip_n) * add_len
            new_total = total_len + add_len
            out_pos[v0] = pos[v0]
            seg_idx = int(0)
            seg_start_s = float(0.0)
            seg_end_s = wp.length(pos[v0 + 1] - pos[v0])
            for k in range(1, c):
                s_target = new_total * (float(k) / float(c - 1))
                while seg_idx < c - 1 and s_target > seg_end_s + 1e-7:
                    seg_idx += 1
                    seg_start_s = seg_end_s
                    if seg_idx < c - 1:
                        seg_end_s += wp.length(pos[v0 + seg_idx + 1] - pos[v0 + seg_idx])
                    else:
                        seg_end_s += add_len
                p_a = pos[v0 + seg_idx]
                p_b = pos[v0 + seg_idx + 1] if seg_idx < c - 1 else p_end
                span = wp.max(seg_end_s - seg_start_s, 1e-8)
                frac = wp.clamp((s_target - seg_start_s) / span, 0.0, 1.0)
                out_pos[v0 + k] = p_a + (p_b - p_a) * frac
        else:
            new_len = wp.max(min_strand_len, total_len * wp.max(0.4, 1.0 - rate))
            if new_len >= total_len - 1e-6:
                return
            out_pos[v0] = pos[v0]
            seg_idx = int(0)
            seg_start_s = float(0.0)
            seg_end_s = wp.length(pos[v0 + 1] - pos[v0])
            for k in range(1, c):
                s_target = new_len * (float(k) / float(c - 1))
                while seg_idx < c - 2 and s_target > seg_end_s + 1e-7:
                    seg_idx += 1
                    seg_start_s = seg_end_s
                    seg_end_s += wp.length(pos[v0 + seg_idx + 1] - pos[v0 + seg_idx])
                p_a = pos[v0 + seg_idx]
                p_b = pos[v0 + seg_idx + 1]
                span = wp.max(seg_end_s - seg_start_s, 1e-8)
                frac = wp.clamp((s_target - seg_start_s) / span, 0.0, 1.0)
                out_pos[v0 + k] = p_a + (p_b - p_a) * frac
        wp.atomic_add(changed_flag, 0, 1)

    @wp.kernel
    def recompute_seg_lens_kernel(
        pos: wp.array(dtype=wp.vec3),
        v0_arr: wp.array(dtype=int),
        v1_arr: wp.array(dtype=int),
        out_lens: wp.array(dtype=float),
    ):
        i = wp.tid()
        out_lens[i] = wp.length(pos[v1_arr[i]] - pos[v0_arr[i]])

    @wp.kernel
    def recompute_cum_len_kernel(
        pos: wp.array(dtype=wp.vec3),
        starts: wp.array(dtype=int),
        counts: wp.array(dtype=int),
        cum_len: wp.array(dtype=float),
    ):
        s = wp.tid()
        v0 = starts[s]
        c = counts[s]
        if c <= 0:
            return
        acc = float(0.0)
        cum_len[v0] = 0.0
        for k in range(1, c):
            acc += wp.length(pos[v0 + k] - pos[v0 + k - 1])
            cum_len[v0 + k] = acc

    cached = (lengthen_drag_gpu_kernel, length_adjust_gpu_kernel, recompute_seg_lens_kernel, recompute_cum_len_kernel)
    wp._orchflow_length_gpu_kernels = cached
    return cached


def _sync_rest_lengths_gpu(sess, wp, device):
    _, _, recompute_seg_lens_kernel, recompute_cum_len_kernel = _build_length_gpu_kernels(wp)
    pos_wp = sess["pos_wp"]
    if sess.get("seg_v0_wp") is not None and sess.get("seg_v1_wp") is not None and sess.get("seg_len_wp") is not None:
        n_segs = len(sess["seg_len_wp"])
        if n_segs > 0:
            wp.launch(
                recompute_seg_lens_kernel,
                dim=n_segs,
                inputs=[pos_wp, sess["seg_v0_wp"], sess["seg_v1_wp"], sess["seg_len_wp"]],
                device=device,
            )
    if sess.get("bend_v0_wp") is not None and sess.get("bend_v2_wp") is not None and sess.get("bend_len_wp") is not None:
        n_bends = len(sess["bend_len_wp"])
        if n_bends > 0:
            wp.launch(
                recompute_seg_lens_kernel,
                dim=n_bends,
                inputs=[pos_wp, sess["bend_v0_wp"], sess["bend_v2_wp"], sess["bend_len_wp"]],
                device=device,
            )
    if sess.get("cum_len_wp") is not None and sess.get("vert_starts_wp") is not None and sess.get("counts_wp") is not None:
        n_strands = int(sess.get("num_strands", 0))
        if n_strands > 0:
            wp.launch(
                recompute_cum_len_kernel,
                dim=n_strands,
                inputs=[pos_wp, sess["vert_starts_wp"], sess["counts_wp"], sess["cum_len_wp"]],
                device=device,
            )


def _apply_length_or_cut_brush(sess, tool, brush_center, scaled_radius, strength, wp, np, device, ray_origin=None, ray_dir=None):
    """Apply lengthen or shorten to unpinned strands hit by the brush."""
    total_verts = int(sess["total_verts"])
    num_strands = int(sess["num_strands"])
    if total_verts <= 0 or num_strands <= 0:
        return False
    if tool in ("lengthen", "shorten") and sess.get("vert_starts_wp") is not None and sess.get("counts_wp") is not None:
        _, length_adjust_gpu_kernel, _, _ = _build_length_gpu_kernels(wp)
        pos_wp = sess["pos_wp"]
        out_wp = sess["prev_pos_wp"]
        wp.copy(out_wp, pos_wp)
        changed_flag = wp.zeros(1, dtype=int, device=device)
        use_ray = 1 if (ray_origin is not None and ray_dir is not None) else 0
        if use_ray:
            rd_arr = np.array([float(ray_dir[0]), float(ray_dir[1]), float(ray_dir[2])], dtype=np.float32)
            rd_len = float(np.linalg.norm(rd_arr))
            if rd_len > 1e-8:
                rd_arr /= rd_len
            ro_vec = wp.vec3(float(ray_origin[0]), float(ray_origin[1]), float(ray_origin[2]))
            rd_vec = wp.vec3(float(rd_arr[0]), float(rd_arr[1]), float(rd_arr[2]))
        else:
            ro_vec = wp.vec3(0.0, 0.0, 0.0)
            rd_vec = wp.vec3(0.0, 0.0, -1.0)
        center_vec = wp.vec3(float(brush_center[0]), float(brush_center[1]), float(brush_center[2]))
        min_strand_len = max(1e-4, 0.005 * float(sess["unit_scale"]))
        wp.launch(
            length_adjust_gpu_kernel,
            dim=num_strands,
            inputs=[
                pos_wp, out_wp, sess["vert_starts_wp"], sess["counts_wp"], sess["pinned_strand_wp"],
                ro_vec, rd_vec, center_vec, use_ray,
                float(scaled_radius * scaled_radius), float(scaled_radius), float(strength),
                1 if tool == "lengthen" else 0, float(min_strand_len), changed_flag,
            ],
            device=device,
        )
        if int(changed_flag.numpy()[0]) == 0:
            return False
        wp.copy(pos_wp, out_wp)
        wp.copy(sess["anim_rest_wp"], pos_wp)
        sess["vel_wp"].zero_()
        _sync_rest_lengths_gpu(sess, wp, device)
        return True
    pos_np = sess["pos_wp"].numpy().copy()
    counts_np = sess["counts_np"]
    starts_np = sess["starts_np"]
    pinned_np = sess["pinned_strand_wp"].numpy()
    cx, cy, cz = float(brush_center[0]), float(brush_center[1]), float(brush_center[2])
    center_np = np.array([cx, cy, cz], dtype=np.float32)
    r2 = float(scaled_radius * scaled_radius)
    unit_scale = float(sess["unit_scale"])
    min_strand_len = max(1e-4, 0.005 * unit_scale)

    if ray_origin is not None and ray_dir is not None:
        ro = np.array([float(ray_origin[0]), float(ray_origin[1]), float(ray_origin[2])], dtype=np.float32)
        rd = np.array([float(ray_dir[0]), float(ray_dir[1]), float(ray_dir[2])], dtype=np.float32)
        rd_len = float(np.linalg.norm(rd))
        if rd_len > 1e-8:
            rd /= rd_len
        to_pts = pos_np - ro[None, :]
        t_proj = np.maximum(0.0, to_pts @ rd)
        closest_on_ray = ro[None, :] + t_proj[:, None] * rd[None, :]
        dists_sq = np.sum((pos_np - closest_on_ray) ** 2, axis=1)
    else:
        dists_sq = np.sum((pos_np - center_np[None, :]) ** 2, axis=1)
    changed = False
    for s in range(num_strands):
        if pinned_np[s] != 0:
            continue
        v0 = int(starts_np[s])
        c = int(counts_np[s])
        if c < 2:
            continue
        strand_d2 = dists_sq[v0 : v0 + c]
        k_best = int(np.argmin(strand_d2))
        if strand_d2[k_best] > r2:
            continue
        pts = pos_np[v0 : v0 + c]
        diffs = pts[1:] - pts[:-1]
        seg_lens = np.linalg.norm(diffs, axis=1)
        total_len = float(seg_lens.sum())
        if total_len <= 1e-7:
            continue
        if tool in ("lengthen", "shorten"):
            w = max(0.0, 1.0 - float(np.sqrt(strand_d2[k_best])) / max(scaled_radius, 1e-6))
            if w <= 0.0:
                continue
            rate = min(0.35, 0.08 * max(0.1, strength) * w)
            cum = np.concatenate(([0.0], np.cumsum(seg_lens)))
            if tool == "lengthen":
                add_len = total_len * rate
                tip_vec = pts[-1] - pts[-2]
                tip_n = float(np.linalg.norm(tip_vec))
                if tip_n <= 1e-8:
                    tip_vec = pts[-1] - pts[0]
                    tip_n = float(np.linalg.norm(tip_vec))
                if tip_n <= 1e-8:
                    continue
                tip_dir = tip_vec / tip_n
                ext_pt = pts[-1] + tip_dir * add_len
                ext_pts = np.vstack([pts, ext_pt.reshape(1, 3)])
                ext_cum = np.concatenate((cum, [total_len + add_len]))
                sample_s = np.linspace(0.0, total_len + add_len, c, dtype=np.float32)
                for ax in range(3):
                    pos_np[v0 : v0 + c, ax] = np.interp(sample_s, ext_cum, ext_pts[:, ax])
            else:
                new_len = max(min_strand_len, total_len * max(0.4, 1.0 - rate))
                if new_len >= total_len - 1e-6:
                    continue
                sample_s = np.linspace(0.0, new_len, c, dtype=np.float32)
                for ax in range(3):
                    pos_np[v0 : v0 + c, ax] = np.interp(sample_s, cum, pts[:, ax])
            changed = True
        elif tool == "cut":
            cum = np.concatenate(([0.0], np.cumsum(seg_lens)))
            # Find closest point along all segments of this strand to brush_center
            best_arc = float(cum[k_best])
            best_d2 = float(strand_d2[k_best])
            for seg_i in range(c - 1):
                a = pts[seg_i]
                b = pts[seg_i + 1]
                ab = b - a
                ab_len2 = float(np.dot(ab, ab))
                if ab_len2 > 1e-12:
                    t = max(0.0, min(1.0, float(np.dot(center_np - a, ab) / ab_len2)))
                    proj = a + ab * t
                    d2 = float(np.sum((proj - center_np) ** 2))
                    if d2 <= best_d2:
                        best_d2 = d2
                        best_arc = float(cum[seg_i] + t * seg_lens[seg_i])
            cut_len = max(min_strand_len, best_arc)
            # If the brush hit right at the tip, trim by the brush overlap so tip-cutting works
            if cut_len >= total_len - 1e-5:
                tip_trim = min(scaled_radius * 0.5, total_len * 0.25)
                cut_len = max(min_strand_len, total_len - tip_trim)
            if cut_len >= total_len - 1e-5:
                continue
            sample_s = np.linspace(0.0, cut_len, c, dtype=np.float32)
            for ax in range(3):
                pos_np[v0 : v0 + c, ax] = np.interp(sample_s, cum, pts[:, ax])
            changed = True

    if not changed:
        return False

    new_world_wp = wp.array(np.ascontiguousarray(pos_np, dtype=np.float32), dtype=wp.vec3, device=device)
    wp.copy(sess["pos_wp"], new_world_wp)
    wp.copy(sess["prev_pos_wp"], new_world_wp)
    wp.copy(sess["anim_rest_wp"], new_world_wp)
    sess["vel_wp"].zero_()
    s_v0 = sess.get("seg_v0_np")
    s_v1 = sess.get("seg_v1_np")
    if s_v0 is not None and s_v1 is not None and len(s_v0) > 0 and sess.get("seg_len_wp") is not None:
        new_slen = np.linalg.norm(pos_np[s_v1] - pos_np[s_v0], axis=1).astype(np.float32)
        wp.copy(sess["seg_len_wp"], wp.array(new_slen, dtype=float, device=device))
    b_v0 = sess.get("bend_v0_np")
    b_v2 = sess.get("bend_v2_np")
    if b_v0 is not None and b_v2 is not None and len(b_v0) > 0 and sess.get("bend_len_wp") is not None:
        new_blen = np.linalg.norm(pos_np[b_v2] - pos_np[b_v0], axis=1).astype(np.float32)
        wp.copy(sess["bend_len_wp"], wp.array(new_blen, dtype=float, device=device))
    return True


def _rebuild_session_topology(stage, p_str, sess, new_counts_np, new_world_pos_np, new_pinned_np, new_widths_np, wp, np, device, Vt, UsdGeom):
    """Rebuild GPU session buffers and USD BasisCurves after Add or Delete strands."""
    new_counts_np = np.ascontiguousarray(new_counts_np, dtype=np.int32)
    new_world_pos_np = np.ascontiguousarray(new_world_pos_np, dtype=np.float32)
    num_strands = int(len(new_counts_np))
    total_verts = int(new_counts_np.sum()) if num_strands > 0 else 0

    m = np.asarray(sess["world_to_local"], dtype=np.float32)
    local_np = (
        np.ascontiguousarray(new_world_pos_np @ m[:3, :3] + m[3, :3], dtype=np.float32)
        if total_verts > 0
        else np.zeros((0, 3), dtype=np.float32)
    )

    prim = stage.GetPrimAtPath(p_str)
    if prim and prim.IsValid():
        curves = UsdGeom.BasisCurves(prim)
        curves.CreateCurveVertexCountsAttr().Set(Vt.IntArray.FromNumpy(new_counts_np))
        curves.CreatePointsAttr().Set(Vt.Vec3fArray.FromNumpy(local_np))
        if new_widths_np is not None:
            curves.CreateWidthsAttr().Set(Vt.FloatArray.FromNumpy(np.ascontiguousarray(new_widths_np, dtype=np.float32)))

    if total_verts == 0:
        sess.update({
            "total_verts": 0,
            "num_strands": 0,
            "counts_np": new_counts_np,
            "starts_np": np.zeros(0, dtype=np.int32),
            "widths_np": new_widths_np,
            "topology_dirty": True,
            "dirty_groom": True,
            "needs_stage_repopulate": True,
        })
        return

    ends = np.cumsum(new_counts_np)
    starts = ends - new_counts_np
    valid_strands = new_counts_np > 0
    inv_mass_np = np.ones(total_verts, dtype=np.float32)
    inv_mass_np[starts[valid_strands]] = 0.0

    vert_indices = np.arange(total_verts, dtype=np.int32)
    strand_ids = np.repeat(np.arange(num_strands, dtype=np.int32), new_counts_np)
    local_idx = vert_indices - starts[strand_ids]
    denom = np.maximum(1, new_counts_np[strand_ids] - 1).astype(np.float32)
    root_t_np = (local_idx.astype(np.float32) / denom).astype(np.float32)

    is_seg = local_idx + 1 < new_counts_np[strand_ids]
    seg_v0_np = vert_indices[is_seg]
    seg_v1_np = seg_v0_np + 1
    seg_len_np = np.linalg.norm(new_world_pos_np[seg_v1_np] - new_world_pos_np[seg_v0_np], axis=1).astype(np.float32)

    vert_seg_len = np.zeros(total_verts, dtype=np.float64)
    if len(seg_v1_np) > 0:
        vert_seg_len[seg_v1_np] = seg_len_np
    global_cum = np.cumsum(vert_seg_len, dtype=np.float64)
    cum_len_np = (global_cum - global_cum[starts[strand_ids]]).astype(np.float32)

    is_bend = local_idx + 2 < new_counts_np[strand_ids]
    bend_v0_np = vert_indices[is_bend]
    bend_v2_np = bend_v0_np + 2
    bend_len_np = np.linalg.norm(new_world_pos_np[bend_v2_np] - new_world_pos_np[bend_v0_np], axis=1).astype(np.float32)

    n_segs = len(seg_v0_np)
    n_bends = len(bend_v0_np)
    pos_wp = wp.array(new_world_pos_np, dtype=wp.vec3, device=device)
    sess.update({
        "total_verts": total_verts,
        "num_strands": num_strands,
        "counts_np": new_counts_np,
        "starts_np": starts,
        "seg_v0_np": seg_v0_np,
        "seg_v1_np": seg_v1_np,
        "bend_v0_np": bend_v0_np,
        "bend_v2_np": bend_v2_np,
        "pos_wp": pos_wp,
        "prev_pos_wp": wp.array(new_world_pos_np, dtype=wp.vec3, device=device),
        "anim_rest_wp": wp.array(new_world_pos_np, dtype=wp.vec3, device=device),
        "vel_wp": wp.zeros(total_verts, dtype=wp.vec3, device=device),
        "inv_mass_wp": wp.array(inv_mass_np, dtype=float, device=device),
        "root_t_wp": wp.array(root_t_np, dtype=float, device=device),
        "delta_pos_wp": wp.zeros(total_verts, dtype=wp.vec3, device=device),
        "delta_w_wp": wp.zeros(total_verts, dtype=float, device=device),
        "local_wp": wp.zeros(total_verts, dtype=wp.vec3, device=device),
        "reference_world_wp": wp.array(new_world_pos_np, dtype=wp.vec3, device=device),
        "reference_local_wp": wp.array(local_np, dtype=wp.vec3, device=device),
        "n_segs": n_segs,
        "seg_v0_wp": wp.array(seg_v0_np, dtype=int, device=device) if n_segs > 0 else None,
        "seg_v1_wp": wp.array(seg_v1_np, dtype=int, device=device) if n_segs > 0 else None,
        "seg_len_wp": wp.array(seg_len_np, dtype=float, device=device) if n_segs > 0 else None,
        "n_bends": n_bends,
        "bend_v0_wp": wp.array(bend_v0_np, dtype=int, device=device) if n_bends > 0 else None,
        "bend_v2_wp": wp.array(bend_v2_np, dtype=int, device=device) if n_bends > 0 else None,
        "bend_len_wp": wp.array(bend_len_np, dtype=float, device=device) if n_bends > 0 else None,
        "strand_ids_wp": wp.array(strand_ids, dtype=int, device=device),
        "local_idx_wp": wp.array(local_idx, dtype=int, device=device),
        "vert_starts_wp": wp.array(starts, dtype=int, device=device),
        "counts_wp": wp.array(new_counts_np, dtype=int, device=device),
        "cum_len_wp": wp.array(cum_len_np, dtype=float, device=device),
        "strand_hero_k_wp": wp.full(num_strands, -1, dtype=int, device=device),
        "strand_hero_offset_wp": wp.zeros(num_strands, dtype=wp.vec3, device=device),
        "strand_surface_target_wp": wp.zeros(num_strands, dtype=wp.vec3, device=device),
        "strand_brush_w_wp": wp.zeros(num_strands, dtype=float, device=device),
        "active_strand_wp": wp.zeros(num_strands, dtype=int, device=device),
        "pinned_strand_wp": wp.array(np.ascontiguousarray(new_pinned_np, dtype=np.int32), dtype=int, device=device),
        "brush_inv_mass_wp": wp.zeros(total_verts, dtype=float, device=device),
        "undo_stack": [],
        "guide_cache": None,
        "widths_np": new_widths_np,
        "topology_dirty": True,
        "dirty_groom": True,
        "needs_stage_repopulate": True,
    })


def _apply_add_or_delete_brush(stage, p_str, sess, tool, brush_center, scaled_radius, strength, ray_origin, ray_dir, is_new_stroke, wp, np, device, Vt, UsdGeom):
    """Add new strands near brush_center or delete unpinned strands hit by the brush ray."""
    num_strands = int(sess["num_strands"])
    total_verts = int(sess["total_verts"])
    counts_np = sess["counts_np"]
    starts_np = sess["starts_np"]
    pos_np = sess["pos_wp"].numpy() if total_verts > 0 else np.zeros((0, 3), dtype=np.float32)
    pinned_np = sess["pinned_strand_wp"].numpy() if num_strands > 0 else np.zeros(0, dtype=np.int32)
    widths_np = sess.get("widths_np")
    widths_interp = str(sess.get("widths_interp") or "vertex")
    cx, cy, cz = float(brush_center[0]), float(brush_center[1]), float(brush_center[2])
    center_np = np.array([cx, cy, cz], dtype=np.float32)

    if tool == "delete":
        if num_strands == 0 or total_verts == 0:
            return False
        r2 = float(scaled_radius * scaled_radius)
        ro = np.array([float(ray_origin[0]), float(ray_origin[1]), float(ray_origin[2])], dtype=np.float32)
        rd = np.array([float(ray_dir[0]), float(ray_dir[1]), float(ray_dir[2])], dtype=np.float32)
        rd_len = float(np.linalg.norm(rd))
        if rd_len > 1e-8:
            rd /= rd_len
        to_pts = pos_np - ro[None, :]
        t_proj = np.maximum(0.0, to_pts @ rd)
        closest_on_ray = ro[None, :] + t_proj[:, None] * rd[None, :]
        ray_dists_sq = np.sum((pos_np - closest_on_ray) ** 2, axis=1)
        keep_strand = np.ones(num_strands, dtype=bool)
        for s in range(num_strands):
            if pinned_np[s] != 0:
                continue
            v0 = int(starts_np[s])
            c = int(counts_np[s])
            if np.min(ray_dists_sq[v0 : v0 + c]) <= r2:
                keep_strand[s] = False
        if np.all(keep_strand):
            return False
        keep_vert = np.repeat(keep_strand, counts_np)
        new_counts = counts_np[keep_strand]
        new_pos = pos_np[keep_vert]
        new_pinned = pinned_np[keep_strand]
        new_widths = None
        if widths_np is not None:
            if widths_interp == "uniform" and len(widths_np) == num_strands:
                new_widths = widths_np[keep_strand]
            elif len(widths_np) == total_verts:
                new_widths = widths_np[keep_vert]
            else:
                new_widths = widths_np
        _rebuild_session_topology(stage, p_str, sess, new_counts, new_pos, new_pinned, new_widths, wp, np, device, Vt, UsdGeom)
        return True

    if tool == "add":
        last_ac = sess.get("last_add_center")
        if not is_new_stroke and last_ac is not None:
            d_ac = float(np.linalg.norm(center_np - np.asarray(last_ac, dtype=np.float32)))
            if d_ac < scaled_radius * 0.2:
                return False
        sess["last_add_center"] = (cx, cy, cz)

        n_spawn = max(1, min(8, int(round(2.0 * max(0.25, strength)))))
        k_verts = int(np.median(counts_np)) if len(counts_np) > 0 else 8
        k_verts = max(2, k_verts)
        unit_scale = float(sess["unit_scale"])
        rng = np.random.default_rng(int(abs(cx * 1000.0 + cy * 317.0 + cz * 97.0 + num_strands)) & 0xFFFFFFFF)

        # Build tangent frame perpendicular to ray_dir for brush disk jitter
        rd = np.array([float(ray_dir[0]), float(ray_dir[1]), float(ray_dir[2])], dtype=np.float32)
        rd_norm = float(np.linalg.norm(rd))
        if rd_norm > 1e-6:
            rd /= rd_norm
        else:
            rd = np.array([0.0, 0.0, -1.0], dtype=np.float32)
        up_ref = np.array([0.0, 1.0, 0.0], dtype=np.float32) if abs(rd[1]) < 0.9 else np.array([1.0, 0.0, 0.0], dtype=np.float32)
        t1 = np.cross(up_ref, rd)
        t1 /= max(float(np.linalg.norm(t1)), 1e-6)
        t2 = np.cross(rd, t1)

        wp_collider = sess.get("wp_collider")
        roots = pos_np[starts_np] if num_strands > 0 else np.zeros((0, 3), dtype=np.float32)

        added_counts = []
        added_pts = []
        added_widths = []
        for _ in range(n_spawn):
            angle = float(rng.uniform(0.0, 2.0 * np.pi))
            rad = float(rng.uniform(0.0, scaled_radius * 0.7))
            cand = center_np + (t1 * np.cos(angle) + t2 * np.sin(angle)) * rad

            # Snap root to collider surface if available
            root_pt = cand
            if wp_collider is not None:
                snap_root_to_mesh_kernel = _build_kernels(wp)[25]
                q_pt = wp.array(cand.reshape(1, 3), dtype=wp.vec3, device=device)
                q_out = wp.zeros(1, dtype=wp.vec3, device=device)
                q_hit = wp.zeros(1, dtype=int, device=device)
                wp.launch(snap_root_to_mesh_kernel, dim=1, inputs=[wp_collider.id, q_pt, float(sess["max_query_dist"]), q_out, q_hit], device=device)
                if int(q_hit.numpy()[0]) == 1:
                    root_pt = q_out.numpy()[0]

            if len(roots) > 0:
                nearest_s = int(np.argmin(np.sum((roots - root_pt[None, :]) ** 2, axis=1)))
                sv0 = int(starts_np[nearest_s])
                sc = int(counts_np[nearest_s])
                tmpl = pos_np[sv0 : sv0 + sc] - pos_np[sv0 : sv0 + 1]
                if sc != k_verts:
                    t_old = np.linspace(0.0, 1.0, sc, dtype=np.float32)
                    t_new = np.linspace(0.0, 1.0, k_verts, dtype=np.float32)
                    tmpl_res = np.stack([np.interp(t_new, t_old, tmpl[:, ax]) for ax in range(3)], axis=1)
                else:
                    tmpl_res = tmpl
                strand_pts = root_pt[None, :] + tmpl_res
                if widths_np is not None:
                    if widths_interp == "uniform" and len(widths_np) == num_strands:
                        added_widths.append(np.array([widths_np[nearest_s]], dtype=np.float32))
                    elif len(widths_np) == total_verts:
                        sw = widths_np[sv0 : sv0 + sc]
                        if sc != k_verts:
                            sw = np.interp(np.linspace(0.0, 1.0, k_verts), np.linspace(0.0, 1.0, sc), sw).astype(np.float32)
                        added_widths.append(sw)
            else:
                step_dir = np.array([0.0, -0.02 * unit_scale, 0.0], dtype=np.float32)
                strand_pts = np.stack([root_pt + step_dir * float(k) for k in range(k_verts)], axis=0)
                if widths_np is not None:
                    w_val = float(np.median(widths_np)) if len(widths_np) > 0 else 0.002 * unit_scale
                    if widths_interp == "uniform":
                        added_widths.append(np.array([w_val], dtype=np.float32))
                    else:
                        added_widths.append(np.full(k_verts, w_val, dtype=np.float32))

            added_counts.append(k_verts)
            added_pts.append(strand_pts.astype(np.float32))

        new_counts = np.concatenate([counts_np, np.array(added_counts, dtype=np.int32)])
        new_pos = np.concatenate([pos_np] + added_pts, axis=0) if total_verts > 0 else np.concatenate(added_pts, axis=0)
        new_pinned = np.concatenate([pinned_np, np.zeros(n_spawn, dtype=np.int32)])
        new_widths = None
        if widths_np is not None:
            if added_widths:
                new_widths = np.concatenate([widths_np] + added_widths)
            else:
                new_widths = widths_np
        _rebuild_session_topology(stage, p_str, sess, new_counts, new_pos, new_pinned, new_widths, wp, np, device, Vt, UsdGeom)
        return True

    return False


def _apply_lengthen_drag_brush(sess, lengthen_drag, scaled_radius, cam_basis, wp, np, device):
    """Select all unpinned strands within radius of click ray (all depths) and extend them to end at let-go screen point."""
    num_strands = int(sess["num_strands"])
    total_verts = int(sess["total_verts"])
    if num_strands <= 0 or total_verts <= 0:
        return False

    nx1, ny1, nx2, ny2 = (float(lengthen_drag[0]), float(lengthen_drag[1]), float(lengthen_drag[2]), float(lengthen_drag[3]))
    eye = np.asarray(cam_basis["origin"], dtype=np.float64)
    fwd = np.asarray(cam_basis["forward"], dtype=np.float64)
    right = np.asarray(cam_basis["right"], dtype=np.float64)
    up = np.asarray(cam_basis["up"], dtype=np.float64)
    tan_half_fov = float(cam_basis.get("tan_half_fov", 0.4142))
    aspect = float(cam_basis.get("aspect", 1.3333))

    sx1 = (nx1 * 2.0 - 1.0) * tan_half_fov * aspect
    sy1 = (1.0 - ny1 * 2.0) * tan_half_fov
    r1_unnorm = fwd + right * sx1 + up * sy1
    r1 = r1_unnorm / max(float(np.linalg.norm(r1_unnorm)), 1e-12)

    sx2 = (nx2 * 2.0 - 1.0) * tan_half_fov * aspect
    sy2 = (1.0 - ny2 * 2.0) * tan_half_fov
    r2_unnorm = fwd + right * sx2 + up * sy2
    drag_unnorm = r2_unnorm - r1_unnorm
    r2 = float(scaled_radius * scaled_radius)

    if sess.get("vert_starts_wp") is not None and sess.get("counts_wp") is not None:
        lengthen_drag_gpu_kernel, _, _, _ = _build_length_gpu_kernels(wp)
        pos_wp = sess["pos_wp"]
        out_wp = sess["prev_pos_wp"]
        wp.copy(out_wp, pos_wp)
        changed_flag = wp.zeros(1, dtype=int, device=device)
        wp.launch(
            lengthen_drag_gpu_kernel,
            dim=num_strands,
            inputs=[
                pos_wp, out_wp, sess["vert_starts_wp"], sess["counts_wp"], sess["pinned_strand_wp"],
                wp.vec3(float(eye[0]), float(eye[1]), float(eye[2])),
                wp.vec3(float(fwd[0]), float(fwd[1]), float(fwd[2])),
                wp.vec3(float(r1[0]), float(r1[1]), float(r1[2])),
                wp.vec3(float(drag_unnorm[0]), float(drag_unnorm[1]), float(drag_unnorm[2])),
                r2, changed_flag,
            ],
            device=device,
        )
        if int(changed_flag.numpy()[0]) == 0:
            return False
        wp.copy(pos_wp, out_wp)
        wp.copy(sess["anim_rest_wp"], pos_wp)
        sess["vel_wp"].zero_()
        _sync_rest_lengths_gpu(sess, wp, device)
        return True

    pos_np = sess["pos_wp"].numpy().copy()
    counts_np = sess["counts_np"]
    starts_np = sess["starts_np"]
    pinned_np = sess["pinned_strand_wp"].numpy()

    to_v = pos_np.astype(np.float64) - eye[None, :]
    t_along = to_v @ r1
    closest = eye[None, :] + t_along[:, None] * r1[None, :]
    perp_d2 = np.sum((pos_np.astype(np.float64) - closest) ** 2, axis=1)

    hit_vert = (t_along > 0.0) & (perp_d2 <= r2)
    if not np.any(hit_vert):
        return False
    strand_ids_np = np.repeat(np.arange(num_strands, dtype=np.int32), counts_np)
    if len(strand_ids_np) != len(hit_vert):
        return False
    hit_strands = np.flatnonzero(
        (pinned_np == 0) & (counts_np >= 2) & (np.bincount(strand_ids_np, weights=hit_vert, minlength=num_strands) > 0)
    )
    if len(hit_strands) == 0:
        return False

    changed = False
    for s in hit_strands:
        v0 = int(starts_np[s])
        c = int(counts_np[s])
        pts = pos_np[v0 : v0 + c]
        p_tip = pts[-1].astype(np.float64)
        z_tip = float(np.dot(p_tip - eye, fwd))
        if z_tip <= 1e-5:
            continue
        p_end = (p_tip + drag_unnorm * z_tip).astype(np.float32)
        ext_seg = float(np.linalg.norm(p_end - pts[-1]))
        if ext_seg <= 1e-6:
            continue
        diffs = pts[1:] - pts[:-1]
        seg_lens = np.linalg.norm(diffs, axis=1)
        total_len = float(seg_lens.sum())
        cum = np.concatenate(([0.0], np.cumsum(seg_lens), [total_len + ext_seg]))
        ext_pts = np.vstack([pts, p_end.reshape(1, 3)])
        sample_s = np.linspace(0.0, total_len + ext_seg, c, dtype=np.float32)
        for ax in range(3):
            pos_np[v0 : v0 + c, ax] = np.interp(sample_s, cum, ext_pts[:, ax])
        changed = True

    if not changed:
        return False

    new_world_wp = wp.array(np.ascontiguousarray(pos_np, dtype=np.float32), dtype=wp.vec3, device=device)
    wp.copy(sess["pos_wp"], new_world_wp)
    wp.copy(sess["prev_pos_wp"], new_world_wp)
    wp.copy(sess["anim_rest_wp"], new_world_wp)
    sess["vel_wp"].zero_()
    s_v0 = sess.get("seg_v0_np")
    s_v1 = sess.get("seg_v1_np")
    if s_v0 is not None and s_v1 is not None and len(s_v0) > 0 and sess.get("seg_len_wp") is not None:
        new_slen = np.linalg.norm(pos_np[s_v1] - pos_np[s_v0], axis=1).astype(np.float32)
        wp.copy(sess["seg_len_wp"], wp.array(new_slen, dtype=float, device=device))
    b_v0 = sess.get("bend_v0_np")
    b_v2 = sess.get("bend_v2_np")
    if b_v0 is not None and b_v2 is not None and len(b_v0) > 0 and sess.get("bend_len_wp") is not None:
        new_blen = np.linalg.norm(pos_np[b_v2] - pos_np[b_v0], axis=1).astype(np.float32)
        wp.copy(sess["bend_len_wp"], wp.array(new_blen, dtype=float, device=device))
    return True


def _apply_cut_line_brush(stage, p_str, sess, cut_line, cam_basis, wp, np, device, Vt, UsdGeom):
    """Cut all unpinned strands crossed by the projected screen-space line and insert a point at the cut."""
    num_strands = int(sess["num_strands"])
    total_verts = int(sess["total_verts"])
    if num_strands <= 0 or total_verts <= 0:
        return False
    nx1, ny1, nx2, ny2 = (float(cut_line[0]), float(cut_line[1]), float(cut_line[2]), float(cut_line[3]))
    dx_2d = nx2 - nx1
    dy_2d = ny2 - ny1
    seg_len2_2d = dx_2d * dx_2d + dy_2d * dy_2d
    if seg_len2_2d < 1e-8:
        return False

    eye = np.asarray(cam_basis["origin"], dtype=np.float64)
    fwd = np.asarray(cam_basis["forward"], dtype=np.float64)
    right = np.asarray(cam_basis["right"], dtype=np.float64)
    up = np.asarray(cam_basis["up"], dtype=np.float64)
    tan_half_fov = float(cam_basis.get("tan_half_fov", 0.4142))
    aspect = float(cam_basis.get("aspect", 1.3333))

    def _ray_for(nx, ny):
        sx = (nx * 2.0 - 1.0) * tan_half_fov * aspect
        sy = (1.0 - ny * 2.0) * tan_half_fov
        r = fwd + right * sx + up * sy
        nrm = float(np.linalg.norm(r))
        return r / max(nrm, 1e-12)

    r1 = _ray_for(nx1, ny1)
    r2 = _ray_for(nx2, ny2)
    plane_n = np.cross(r1, r2)
    pn_len = float(np.linalg.norm(plane_n))
    if pn_len < 1e-10:
        return False
    plane_n /= pn_len

    pos_np = sess["pos_wp"].numpy()
    counts_np = sess["counts_np"]
    starts_np = sess["starts_np"]
    pinned_np = sess["pinned_strand_wp"].numpy()
    widths_np = sess.get("widths_np")
    widths_interp = str(sess.get("widths_interp") or "vertex")

    signed_d = (pos_np.astype(np.float64) - eye[None, :]) @ plane_n

    new_counts = []
    new_pts_list = []
    new_widths_list = []
    any_cut = False

    for s in range(num_strands):
        v0 = int(starts_np[s])
        c = int(counts_np[s])
        pts_s = pos_np[v0 : v0 + c]
        w_s = None
        if widths_np is not None:
            if widths_interp == "uniform" and len(widths_np) == num_strands:
                w_s = widths_np[s : s + 1]
            elif len(widths_np) == total_verts:
                w_s = widths_np[v0 : v0 + c]

        cut_done = False
        if pinned_np[s] == 0 and c >= 2:
            sd_s = signed_d[v0 : v0 + c]
            for k in range(c - 1):
                d0 = float(sd_s[k])
                d1 = float(sd_s[k + 1])
                if (d0 <= 0.0 <= d1) or (d1 <= 0.0 <= d0):
                    denom = d0 - d1
                    if abs(denom) < 1e-12:
                        continue
                    alpha = max(0.0, min(1.0, d0 / denom))
                    p_cut = (pts_s[k].astype(np.float64) * (1.0 - alpha) + pts_s[k + 1].astype(np.float64) * alpha)
                    to_p = p_cut - eye
                    z_cam = float(np.dot(to_p, fwd))
                    if z_cam <= 1e-5:
                        continue
                    sx_p = float(np.dot(to_p, right)) / (z_cam * tan_half_fov * aspect)
                    sy_p = float(np.dot(to_p, up)) / (z_cam * tan_half_fov)
                    nx_p = (sx_p + 1.0) * 0.5
                    ny_p = (1.0 - sy_p) * 0.5
                    t_seg = ((nx_p - nx1) * dx_2d + (ny_p - ny1) * dy_2d) / seg_len2_2d
                    if 0.0 <= t_seg <= 1.0:
                        kept = pts_s[: k + 1]
                        p_cut_f32 = p_cut.astype(np.float32).reshape(1, 3)
                        strand_new = np.concatenate([kept, p_cut_f32], axis=0)
                        new_counts.append(int(strand_new.shape[0]))
                        new_pts_list.append(strand_new)
                        if w_s is not None:
                            if widths_interp == "uniform":
                                new_widths_list.append(w_s)
                            else:
                                w_cut = np.array([float(w_s[k] * (1.0 - alpha) + w_s[k + 1] * alpha)], dtype=np.float32)
                                new_widths_list.append(np.concatenate([w_s[: k + 1], w_cut]))
                        any_cut = True
                        cut_done = True
                        break
        if not cut_done:
            new_counts.append(c)
            new_pts_list.append(pts_s)
            if w_s is not None:
                new_widths_list.append(w_s)

    if not any_cut:
        return False

    out_counts = np.array(new_counts, dtype=np.int32)
    out_pts = np.concatenate(new_pts_list, axis=0)
    out_widths = np.concatenate(new_widths_list, axis=0) if new_widths_list else None
    _rebuild_session_topology(stage, p_str, sess, out_counts, out_pts, pinned_np, out_widths, wp, np, device, Vt, UsdGeom)
    return True


