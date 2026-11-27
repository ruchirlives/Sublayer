"""NVIDIA Warp GPU kernel definitions and lazy initialization for the Hair node.

Kernels are compiled once per Warp module (`wp._orchflow_hair_kernels`) and cover:
- XPBD strand simulation: `predict_kernel`, `stretch_constraint_kernel`,
  `bend_constraint_kernel`, `shape_bend_constraint_kernel`, `apply_accum_kernel`,
  `update_vel_kernel`, and `enforce_strand_length_kernel`.
- Mesh & volume collision: `collide_mesh_kernel`, `collide_segments_kernel`,
  `vol_grid_accum_kernel`, `vol_grid_repel_kernel`, `snap_root_to_mesh_kernel`,
  and `ray_mesh_hit_kernel`.
- Interactive grooming brushes: `mask_backfacing_roots_kernel`,
  `brush_min_depth_kernel`, `init_grab_clump_kernel`, `grab_clump_kernel`,
  `strand_ray_dist_kernel`, `comb_sweep_kernel`, `surface_comb_kernel`,
  `smooth_brush_kernel`, `part_brush_kernel`, `puff_brush_kernel`,
  `clump_brush_kernel`, `pin_brush_kernel`, `mark_active_brush_strands_kernel`,
  `sync_active_inv_mass_kernel`, `world_to_local_kernel`, and `measure_stroke_lengths`.
"""

import os
import sys

_WARP_READY = False


def _ensure_warp():
    global _WARP_READY
    sim_pkg_dir = os.environ.get("ORCHFLOW_SIM_PACKAGES", r"I:\AI\OrchFlow-packages\py312")
    if sim_pkg_dir and os.path.isdir(sim_pkg_dir) and sim_pkg_dir not in sys.path:
        sys.path.insert(0, sim_pkg_dir)

    try:
        import numpy as np
        import warp as wp
    except ImportError as exc:
        raise RuntimeError(
            "GPU hair simulation requires 'warp-lang' and 'numpy'. "
            f"Install them into {sim_pkg_dir} or the active Python environment. ({exc})"
        ) from exc

    if not _WARP_READY:
        wp.config.quiet = True
        old_stdout = sys.stdout
        saved_fd = None
        try:
            sys.stdout = sys.stderr
            try:
                saved_fd = os.dup(1)
                os.dup2(2, 1)
            except OSError:
                saved_fd = None
            wp.init()
        finally:
            if saved_fd is not None:
                try:
                    os.dup2(saved_fd, 1)
                    os.close(saved_fd)
                except OSError:
                    pass
            sys.stdout = old_stdout
        _WARP_READY = True

    device = "cuda:0" if wp.is_cuda_available() else "cpu"
    return wp, np, device


def _build_kernels(wp):
    if getattr(wp, "_orchflow_hair_kernels", None) is not None:
        return wp._orchflow_hair_kernels

    @wp.kernel
    def predict_kernel(
        pos: wp.array(dtype=wp.vec3),
        prev_pos: wp.array(dtype=wp.vec3),
        vel: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        anim_rest: wp.array(dtype=wp.vec3),
        ext_accel: wp.vec3,
        damping: float,
        dt: float,
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0:
            p = anim_rest[i]
            pos[i] = p
            prev_pos[i] = p
            vel[i] = wp.vec3(0.0, 0.0, 0.0)
            return

        p = pos[i]
        prev_pos[i] = p
        damp_factor = wp.max(0.0, 1.0 - damping)
        v = vel[i] * damp_factor + ext_accel * dt
        v_speed = wp.length(v)
        if v_speed > 1e-6:
            v = v / (1.0 + 0.04 * v_speed * dt)
        pos[i] = p + v * dt

    @wp.kernel
    def zero_accum_kernel(
        delta_pos: wp.array(dtype=wp.vec3),
        delta_w: wp.array(dtype=float),
    ):
        i = wp.tid()
        delta_pos[i] = wp.vec3(0.0, 0.0, 0.0)
        delta_w[i] = 0.0

    @wp.kernel
    def stretch_constraint_kernel(
        pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        seg_v0: wp.array(dtype=int),
        seg_v1: wp.array(dtype=int),
        seg_rest_len: wp.array(dtype=float),
        stiffness: float,
        delta_pos: wp.array(dtype=wp.vec3),
        delta_w: wp.array(dtype=float),
    ):
        s = wp.tid()
        i0 = seg_v0[s]
        i1 = seg_v1[s]
        w0 = inv_mass[i0]
        w1 = inv_mass[i1]
        w_sum = w0 + w1
        if w_sum <= 1e-8:
            return

        p0 = pos[i0]
        p1 = pos[i1]
        diff = p1 - p0
        dist = wp.length(diff)
        if dist <= 1e-8:
            return

        rest = seg_rest_len[s]
        err = dist - rest
        corr = (diff / dist) * (err * stiffness / w_sum)

        if w0 > 0.0:
            wp.atomic_add(delta_pos, i0, corr * w0)
            wp.atomic_add(delta_w, i0, 1.0)
        if w1 > 0.0:
            wp.atomic_add(delta_pos, i1, -corr * w1)
            wp.atomic_add(delta_w, i1, 1.0)

    @wp.kernel
    def bend_constraint_kernel(
        pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        bend_v0: wp.array(dtype=int),
        bend_v2: wp.array(dtype=int),
        bend_rest_len: wp.array(dtype=float),
        bend_stiffness: float,
        delta_pos: wp.array(dtype=wp.vec3),
        delta_w: wp.array(dtype=float),
    ):
        b = wp.tid()
        if bend_stiffness <= 1e-6:
            return
        i0 = bend_v0[b]
        i2 = bend_v2[b]
        w0 = inv_mass[i0]
        w2 = inv_mass[i2]
        w_sum = w0 + w2
        if w_sum <= 1e-8:
            return

        p0 = pos[i0]
        p2 = pos[i2]
        diff = p2 - p0
        dist = wp.length(diff)
        if dist <= 1e-8:
            return

        rest = bend_rest_len[b]
        err = dist - rest
        corr = (diff / dist) * (err * bend_stiffness / w_sum)

        if w0 > 0.0:
            wp.atomic_add(delta_pos, i0, corr * w0)
            wp.atomic_add(delta_w, i0, 1.0)
        if w2 > 0.0:
            wp.atomic_add(delta_pos, i2, -corr * w2)
            wp.atomic_add(delta_w, i2, 1.0)

    @wp.kernel
    def apply_accum_kernel(
        pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        delta_pos: wp.array(dtype=wp.vec3),
        delta_w: wp.array(dtype=float),
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0:
            return
        w = delta_w[i]
        if w > 0.0:
            pos[i] = pos[i] + delta_pos[i] / w

    @wp.kernel
    def collide_mesh_kernel(
        pos: wp.array(dtype=wp.vec3),
        prev_pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        mesh_id: wp.uint64,
        margin: float,
        friction: float,
        max_query_dist: float,
        apply_friction: int,
        active_edges: wp.array(dtype=int),
        edge_ends: wp.array(dtype=int),
        compact: int,
    ):
        i = wp.tid()
        if compact != 0:
            i = edge_ends[active_edges[i]]
        if inv_mass[i] <= 0.0:
            return

        p = pos[i]
        prev_p = prev_pos[i]
        if apply_friction == 2 and wp.length_sq(p - prev_p) <= 1e-12:
            return

        # Continuous collision detection on final substep pass: catch fast motion or open shells
        if apply_friction != 0:
            move = p - prev_p
            move_len = wp.length(move)
            if move_len > 1e-6:
                move_dir = move / move_len
                ray_q = wp.mesh_query_ray(mesh_id, prev_p, move_dir, move_len + margin)
                if ray_q.result and ray_q.t <= move_len + margin:
                    hit_n = wp.mesh_eval_face_normal(mesh_id, ray_q.face)
                    if wp.dot(hit_n, move_dir) > 0.0:
                        hit_n = -hit_n
                    hit_p = prev_p + move_dir * ray_q.t
                    pen = wp.dot((hit_p + hit_n * margin) - p, hit_n)
                    if pen > 0.0:
                        p = p + hit_n * pen

        query = wp.mesh_query_point_sign_normal(mesh_id, p, max_query_dist)
        if query.result:
            closest = wp.mesh_eval_position(mesh_id, query.face, query.u, query.v)
            diff = p - closest
            dist = wp.length(diff)
            signed_dist = dist * query.sign
            if signed_dist < margin:
                face_n = wp.mesh_eval_face_normal(mesh_id, query.face)
                normal = face_n
                if dist > 1e-6:
                    normal = diff / dist
                    if query.sign < 0.0:
                        normal = -normal
                        if wp.dot(normal, face_n) < 0.0:
                            normal = face_n

                new_p = closest + normal * margin
                pos[i] = new_p
                if apply_friction != 0:
                    disp = new_p - prev_p
                    n_comp = wp.dot(disp, normal)
                    t_disp = disp - normal * n_comp
                    prev_pos[i] = new_p - normal * n_comp - t_disp * (1.0 - friction)
                return

        pos[i] = p

    @wp.kernel
    def collide_segments_kernel(
        pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        seg_v0: wp.array(dtype=int),
        seg_v1: wp.array(dtype=int),
        mesh_id: wp.uint64,
        margin: float,
        max_query_dist: float,
        delta_pos: wp.array(dtype=wp.vec3),
        delta_w: wp.array(dtype=float),
        active_edges: wp.array(dtype=int),
        compact: int,
    ):
        s = wp.tid()
        if compact != 0:
            s = active_edges[s]
        i0 = seg_v0[s]
        i1 = seg_v1[s]
        w0 = inv_mass[i0]
        w1 = inv_mass[i1]
        w_sum = w0 + w1
        if w_sum <= 1e-8:
            return

        mid = (pos[i0] + pos[i1]) * 0.5
        query = wp.mesh_query_point_sign_normal(mesh_id, mid, max_query_dist)
        if query.result:
            closest = wp.mesh_eval_position(mesh_id, query.face, query.u, query.v)
            diff = mid - closest
            dist = wp.length(diff)
            signed_dist = dist * query.sign
            if signed_dist < margin:
                face_n = wp.mesh_eval_face_normal(mesh_id, query.face)
                normal = face_n
                if dist > 1e-6:
                    normal = diff / dist
                    if query.sign < 0.0:
                        normal = -normal
                        if wp.dot(normal, face_n) < 0.0:
                            normal = face_n
                push = (closest + normal * margin) - mid
                if w0 > 0.0:
                    wp.atomic_add(delta_pos, i0, push * (w0 / w_sum))
                    wp.atomic_add(delta_w, i0, 1.0)
                if w1 > 0.0:
                    wp.atomic_add(delta_pos, i1, push * (w1 / w_sum))
                    wp.atomic_add(delta_w, i1, 1.0)

    @wp.kernel
    def update_vel_kernel(
        pos: wp.array(dtype=wp.vec3),
        prev_pos: wp.array(dtype=wp.vec3),
        vel: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        inv_dt: float,
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0:
            vel[i] = wp.vec3(0.0, 0.0, 0.0)
            return
        vel[i] = (pos[i] - prev_pos[i]) * inv_dt

    @wp.kernel
    def mask_backfacing_roots_kernel(
        pos: wp.array(dtype=wp.vec3),
        vert_starts: wp.array(dtype=int),
        vert_counts: wp.array(dtype=int),
        eye: wp.vec3,
        collider_id: wp.uint64,
        has_collider: int,
        max_query_dist: float,
        backfacing: wp.array(dtype=int),
    ):
        s = wp.tid()
        backfacing[s] = 0
        if vert_counts[s] < 2:
            return
        start = vert_starts[s]
        root = pos[start]
        normal = wp.vec3(0.0, 0.0, 0.0)
        if has_collider != 0:
            query = wp.mesh_query_point_sign_normal(collider_id, root, max_query_dist)
            if query.result:
                normal = wp.mesh_eval_face_normal(collider_id, query.face)
        if wp.length_sq(normal) <= 1e-12:
            for k in range(1, vert_counts[s]):
                normal = pos[start + k] - root
                if wp.length_sq(normal) > 1e-12:
                    break
        if wp.dot(normal, eye - root) < 0.0:
            backfacing[s] = 1

    @wp.kernel
    def brush_min_depth_kernel(
        pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        strand_ids: wp.array(dtype=int),
        backfacing: wp.array(dtype=int),
        mask_backfaces: int,
        ray_origin: wp.vec3,
        ray_dir: wp.vec3,
        radius: float,
        min_depth: wp.array(dtype=float),
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0:
            return
        if mask_backfaces != 0 and backfacing[strand_ids[i]] != 0:
            return
        to_v = pos[i] - ray_origin
        t_along = wp.dot(to_v, ray_dir)
        if t_along <= 0.0:
            return
        closest = ray_origin + ray_dir * t_along
        dist = wp.length(pos[i] - closest)
        if dist < radius:
            wp.atomic_min(min_depth, 0, t_along)

    @wp.kernel
    def init_grab_clump_kernel(
        pos: wp.array(dtype=wp.vec3),
        vert_starts: wp.array(dtype=int),
        vert_counts: wp.array(dtype=int),
        strand_dist: wp.array(dtype=float),
        hero_pos0: wp.vec3,
        radius: float,
        strand_hero_k: wp.array(dtype=int),
        strand_hero_offset: wp.array(dtype=wp.vec3),
        strand_surface_target: wp.array(dtype=wp.vec3),
        strand_brush_w: wp.array(dtype=float),
        active_strand: wp.array(dtype=int),
    ):
        s = wp.tid()
        d = strand_dist[s]
        if d >= radius:
            strand_hero_k[s] = -1
            strand_brush_w[s] = 0.0
            return
        s0 = vert_starts[s]
        ln = vert_counts[s]
        if ln <= 1:
            strand_hero_k[s] = -1
            strand_brush_w[s] = 0.0
            return
        best_k = int(1)
        best_d = float(1e9)
        for k in range(1, ln):
            kd = wp.length(pos[s0 + k] - hero_pos0)
            if kd < best_d:
                best_d = kd
                best_k = k
        strand_hero_k[s] = best_k
        strand_hero_offset[s] = pos[s0 + best_k] - hero_pos0
        strand_surface_target[s] = pos[s0 + best_k]
        u = d / radius
        strand_brush_w[s] = 1.0 - u * u
        active_strand[s] = 1

    @wp.kernel
    def grab_clump_kernel(
        pos: wp.array(dtype=wp.vec3),
        prev_pos: wp.array(dtype=wp.vec3),
        vel: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        vert_starts: wp.array(dtype=int),
        vert_counts: wp.array(dtype=int),
        cum_len: wp.array(dtype=float),
        strand_hero_k: wp.array(dtype=int),
        strand_hero_offset: wp.array(dtype=wp.vec3),
        strand_brush_w: wp.array(dtype=float),
        collider_id: wp.uint64,
        has_collider: int,
        margin: float,
        max_query_dist: float,
        cursor_target: wp.vec3,
        strength: float,
        inv_dt: float,
    ):
        s = wp.tid()
        k = strand_hero_k[s]
        if k < 0:
            return
        bw = strand_brush_w[s]
        if bw <= 0.0:
            return
        s0 = vert_starts[s]
        ln = vert_counts[s]
        target = cursor_target + strand_hero_offset[s]
        root_p = pos[s0]
        to_target = target - root_p
        dist_root = wp.length(to_target)
        while k < ln and dist_root > cum_len[s0 + k]:
            k = k + 1
        if k >= ln:
            k = ln - 1
            max_r = cum_len[s0 + k]
            if dist_root > max_r and dist_root > 1e-6:
                target = root_p + to_target * (max_r / dist_root)
        strand_hero_k[s] = k
        if has_collider != 0:
            q = wp.mesh_query_point_sign_normal(collider_id, target, max_query_dist)
            if q.result:
                closest = wp.mesh_eval_position(collider_id, q.face, q.u, q.v)
                diff = target - closest
                dist = wp.length(diff)
                if dist * q.sign < margin:
                    face_n = wp.mesh_eval_face_normal(collider_id, q.face)
                    nrm = face_n
                    if dist > 1e-6:
                        nrm = diff / dist
                        if q.sign < 0.0:
                            nrm = -nrm
                            if wp.dot(nrm, face_n) < 0.0:
                                nrm = face_n
                    target = closest + nrm * margin
        v_idx = s0 + k
        if inv_mass[v_idx] <= 0.0:
            return
        w = wp.min(1.0, strength * bw)
        delta = (target - pos[v_idx]) * w
        d_len = wp.length(delta)
        if has_collider != 0 and d_len > 1e-6:
            d_dir = delta / d_len
            rq = wp.mesh_query_ray(collider_id, pos[v_idx], d_dir, d_len + margin)
            if rq.result and rq.t <= d_len + margin:
                hit_n = wp.mesh_eval_face_normal(collider_id, rq.face)
                if wp.dot(hit_n, d_dir) > 0.0:
                    hit_n = -hit_n
                vn = wp.dot(delta, hit_n)
                if vn < 0.0:
                    delta = delta - hit_n * vn
        pos[v_idx] = pos[v_idx] + delta
        prev_pos[v_idx] = prev_pos[v_idx] + delta
        vel[v_idx] = vel[v_idx] * 0.1
        if k - 1 > 0 and inv_mass[v_idx - 1] > 0.0:
            d_prev = delta * 0.55
            if has_collider != 0 and wp.length(d_prev) > 1e-6:
                dp_len = wp.length(d_prev)
                dp_dir = d_prev / dp_len
                rqp = wp.mesh_query_ray(collider_id, pos[v_idx - 1], dp_dir, dp_len + margin)
                if rqp.result and rqp.t <= dp_len + margin:
                    hn = wp.mesh_eval_face_normal(collider_id, rqp.face)
                    if wp.dot(hn, dp_dir) > 0.0:
                        hn = -hn
                    vn = wp.dot(d_prev, hn)
                    if vn < 0.0:
                        d_prev = d_prev - hn * vn
            pos[v_idx - 1] = pos[v_idx - 1] + d_prev
            prev_pos[v_idx - 1] = prev_pos[v_idx - 1] + d_prev
            vel[v_idx - 1] = vel[v_idx - 1] * 0.1
        if k + 1 < ln and inv_mass[v_idx + 1] > 0.0:
            d_next = delta * 0.55
            if has_collider != 0 and wp.length(d_next) > 1e-6:
                dn_len = wp.length(d_next)
                dn_dir = d_next / dn_len
                rqn = wp.mesh_query_ray(collider_id, pos[v_idx + 1], dn_dir, dn_len + margin)
                if rqn.result and rqn.t <= dn_len + margin:
                    hn = wp.mesh_eval_face_normal(collider_id, rqn.face)
                    if wp.dot(hn, dn_dir) > 0.0:
                        hn = -hn
                    vn = wp.dot(d_next, hn)
                    if vn < 0.0:
                        d_next = d_next - hn * vn
            pos[v_idx + 1] = pos[v_idx + 1] + d_next
            prev_pos[v_idx + 1] = prev_pos[v_idx + 1] + d_next
            vel[v_idx + 1] = vel[v_idx + 1] * 0.1
        # Pull intermediate vertices taut as the grabbed point approaches full reach
        k_len = wp.max(cum_len[s0 + k], 1e-6)
        reach_ratio = wp.min(1.0, wp.length(target - root_p) / k_len)
        taut_w = wp.max(0.0, (reach_ratio - 0.75) * 4.0) * bw
        if taut_w > 0.0 and k > 1:
            dir_r = wp.normalize(target - root_p)
            tw_clamped = wp.min(1.0, taut_w)
            for j in range(1, k):
                if inv_mass[s0 + j] > 0.0:
                    line_p = root_p + dir_r * cum_len[s0 + j]
                    d_taut = (line_p - pos[s0 + j]) * tw_clamped
                    dt_len = wp.length(d_taut)
                    if has_collider != 0 and dt_len > 1e-6:
                        dt_dir = d_taut / dt_len
                        rqt = wp.mesh_query_ray(collider_id, pos[s0 + j], dt_dir, dt_len + margin)
                        if rqt.result and rqt.t <= dt_len + margin:
                            hn = wp.mesh_eval_face_normal(collider_id, rqt.face)
                            if wp.dot(hn, dt_dir) > 0.0:
                                hn = -hn
                            vn = wp.dot(d_taut, hn)
                            if vn < 0.0:
                                d_taut = d_taut - hn * vn
                    pos[s0 + j] = pos[s0 + j] + d_taut
                    vel[s0 + j] = vel[s0 + j] * 0.1

    @wp.kernel
    def strand_ray_dist_kernel(
        pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        strand_ids: wp.array(dtype=int),
        backfacing: wp.array(dtype=int),
        mask_backfaces: int,
        ray_origin: wp.vec3,
        ray_dir: wp.vec3,
        radius: float,
        min_depth: wp.array(dtype=float),
        strand_dist: wp.array(dtype=float),
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0:
            return
        if mask_backfaces != 0 and backfacing[strand_ids[i]] != 0:
            return
        to_v = pos[i] - ray_origin
        t_along = wp.dot(to_v, ray_dir)
        if t_along <= 0.0:
            return
        closest = ray_origin + ray_dir * t_along
        dist = wp.length(pos[i] - closest)
        wp.atomic_min(strand_dist, strand_ids[i], dist)

    @wp.func
    def limit_comb_target(root: wp.vec3, current: wp.vec3, target: wp.vec3, rest_length: float):
        offset = target - root
        distance = wp.length(offset)
        reach = wp.max(rest_length, wp.length(current - root))
        if distance > reach and distance > 1e-8:
            return root + offset * (reach / distance)
        return target

    @wp.kernel
    def comb_sweep_kernel(
        active_guides: wp.array(dtype=int),
        pos: wp.array(dtype=wp.vec3),
        vel: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        vert_starts: wp.array(dtype=int),
        strand_hero_k: wp.array(dtype=int),
        strand_surface_target: wp.array(dtype=wp.vec3),
        strand_brush_w: wp.array(dtype=float),
        cum_len: wp.array(dtype=float),
        collider_id: wp.uint64,
        has_collider: int,
        max_query_dist: float,
        drag_delta: wp.vec3,
        radius: float,
        strength: float,
        drive_position: int,
        vert_counts: wp.array(dtype=int),
        margin: float,
    ):
        s = wp.tid()
        if drive_position == 0:
            s = active_guides[s]
        k = strand_hero_k[s]
        w = strand_brush_w[s]
        if k <= 0 or w <= 1e-6:
            return
        v0 = vert_starts[s]
        c = vert_counts[s]
        hero_idx = v0 + k
        if inv_mass[hero_idx] <= 0.0:
            return
        p = pos[hero_idx]
        t_p = strand_surface_target[s]

        drag_dir = wp.vec3(0.0, 0.0, 0.0)
        d_step = float(0.0)
        if wp.length(drag_delta) > 1e-8:
            adj_delta = drag_delta
            if has_collider != 0:
                q = wp.mesh_query_point_sign_normal(collider_id, t_p, max_query_dist)
                if q.result:
                    surf_p = wp.mesh_eval_position(collider_id, q.face, q.u, q.v)
                    d_vec = t_p - surf_p
                    d_len = wp.length(d_vec)
                    if d_len > 1e-6 and d_len * q.sign <= margin * 2.5:
                        n = d_vec / d_len
                        if q.sign < 0.0:
                            n = -n
                        dn = wp.dot(drag_delta, n)
                        if dn < 0.0:
                            adj_delta = drag_delta - n * dn
            adj_len = wp.length(adj_delta)
            if adj_len > 1e-8:
                drag_dir = adj_delta / adj_len
            delta = adj_delta * (w * wp.min(1.5, strength))
            d_step = wp.length(delta)
            max_step = radius * 0.25
            if d_step > max_step and d_step > 1e-6:
                delta = delta * (max_step / d_step)
                d_step = max_step
            t_p = t_p + delta
            strand_surface_target[s] = t_p

        t_p = limit_comb_target(pos[v0], p, t_p, cum_len[hero_idx])
        strand_surface_target[s] = t_p

        # Disentangle & straighten vertices along the comb drag path while
        # preserving exact segment lengths and collider clearance.
        if d_step > 1e-6 and wp.length_sq(drag_dir) > 0.5 and c >= 3:
            drag_factor = wp.min(1.0, d_step / wp.max(radius * 0.04, 1e-5))
            align_w = wp.clamp(w * wp.min(1.5, strength) * 0.45 * drag_factor, 0.0, 0.75)
            for j in range(1, c):
                idx = v0 + j
                if inv_mass[idx] <= 0.0:
                    continue
                prev_p = pos[idx - 1]
                seg_len = wp.max(cum_len[idx] - cum_len[idx - 1], 1e-6)
                cur_seg = pos[idx] - prev_p
                cur_len = wp.length(cur_seg)
                cur_dir = cur_seg / cur_len if cur_len > 1e-8 else drag_dir
                if j < k:
                    to_target = t_p - prev_p
                    to_len = wp.length(to_target)
                    target_dir = to_target / to_len if to_len > 1e-8 else drag_dir
                    pre_w = align_w * 0.55
                    blended = cur_dir * (1.0 - pre_w) + target_dir * pre_w
                    b_len = wp.length(blended)
                    new_dir = blended / b_len if b_len > 1e-8 else target_dir
                    cand = prev_p + new_dir * seg_len
                elif j == k and drive_position != 0:
                    raw_cand = p + (t_p - p) * 0.85
                    to_raw = raw_cand - prev_p
                    raw_len = wp.length(to_raw)
                    new_dir = to_raw / raw_len if raw_len > 1e-8 else drag_dir
                    cand = prev_p + new_dir * seg_len
                else:
                    blended = cur_dir * (1.0 - align_w) + drag_dir * align_w
                    b_len = wp.length(blended)
                    new_dir = blended / b_len if b_len > 1e-8 else drag_dir
                    cand = prev_p + new_dir * seg_len
                if has_collider != 0:
                    old_v = pos[idx]
                    cmove = cand - old_v
                    cmove_len = wp.length(cmove)
                    if cmove_len > 1e-6:
                        cmove_dir = cmove / cmove_len
                        crq = wp.mesh_query_ray(collider_id, old_v, cmove_dir, cmove_len + margin)
                        if crq.result and crq.t <= cmove_len + margin:
                            cand = old_v + cmove_dir * wp.max(0.0, crq.t - margin)
                    # Also prevent segment (prev_p -> cand) from cutting across curved/open collider.
                    # For j == 1 (root segment), ignore hits right at the root so roots emerging
                    # from the scalp are never pushed back inside.
                    svec = cand - prev_p
                    slen = wp.length(svec)
                    if slen > 1e-6:
                        sdir = svec / slen
                        srq = wp.mesh_query_ray(collider_id, prev_p, sdir, slen)
                        if srq.result and srq.t <= slen and (j > 1 or srq.t > slen * 0.15):
                            hit_p = wp.mesh_eval_position(collider_id, srq.face, srq.u, srq.v)
                            hit_n = wp.mesh_eval_face_normal(collider_id, srq.face)
                            if wp.dot(hit_n, sdir) > 0.0:
                                hit_n = -hit_n
                            cand = hit_p + hit_n * margin
                    qc = wp.mesh_query_point_sign_normal(collider_id, cand, max_query_dist)
                    if qc.result:
                        closest = wp.mesh_eval_position(collider_id, qc.face, qc.u, qc.v)
                        diff = cand - closest
                        dist = wp.length(diff)
                        if dist * qc.sign < margin:
                            face_n = wp.mesh_eval_face_normal(collider_id, qc.face)
                            nrm = face_n
                            if dist > 1e-6:
                                nrm = diff / dist
                                if qc.sign < 0.0:
                                    nrm = -nrm
                                    if wp.dot(nrm, face_n) < 0.0:
                                        nrm = face_n
                            cand = closest + nrm * margin
                pos[idx] = cand
                vel[idx] = vel[idx] * 0.25
        elif drive_position != 0:
            pos[hero_idx] = p + (t_p - p) * 0.85
            vel[hero_idx] = wp.vec3(0.0, 0.0, 0.0)

    @wp.kernel
    def surface_comb_kernel(
        active_guides: wp.array(dtype=int),
        pos: wp.array(dtype=wp.vec3),
        vel: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        vert_starts: wp.array(dtype=int),
        strand_hero_k: wp.array(dtype=int),
        strand_surface_target: wp.array(dtype=wp.vec3),
        strand_brush_w: wp.array(dtype=float),
        cum_len: wp.array(dtype=float),
        collider_id: wp.uint64,
        has_collider: int,
        max_query_dist: float,
        drag_delta: wp.vec3,
        radius: float,
        strength: float,
        drive_position: int,
        vert_counts: wp.array(dtype=int),
        margin: float,
    ):
        s = wp.tid()
        if drive_position == 0:
            s = active_guides[s]
        k = strand_hero_k[s]
        w = strand_brush_w[s]
        if k <= 0 or w <= 1e-6:
            return
        hero_idx = vert_starts[s] + k
        if inv_mass[hero_idx] <= 0.0:
            return
        p = pos[hero_idx]
        t_p = strand_surface_target[s]

        if wp.length(drag_delta) > 1e-8:
            root_p = pos[vert_starts[s]]
            r_vec = t_p - root_p
            r_len = wp.length(r_vec)
            if r_len > 1e-6:
                # Determine local scalp normal and surface height from collider
                n = r_vec / r_len
                d_surf = float(0.0)
                has_surf = int(0)
                if has_collider != 0:
                    q = wp.mesh_query_point_sign_normal(collider_id, t_p, max_query_dist)
                    if q.result:
                        surf_p = wp.mesh_eval_position(collider_id, q.face, q.u, q.v)
                        d_vec = t_p - surf_p
                        d_len = wp.length(d_vec)
                        if d_len > 1e-6:
                            n = d_vec * (q.sign / d_len)
                            d_surf = wp.max(0.0, q.sign * d_len)
                            has_surf = 1

                # Project screen-space movement onto scalp tangent plane
                adj_delta = drag_delta - n * wp.dot(drag_delta, n)
                travel = adj_delta * (w * wp.min(1.5, strength))
                max_step = radius * 0.25
                travel *= wp.min(1.0, max_step / wp.max(wp.length(travel), 1e-8))
                p_moved = t_p + travel

                if has_surf != 0:
                    q2 = wp.mesh_query_point_sign_normal(collider_id, p_moved, max_query_dist)
                    if q2.result:
                        surf_p2 = wp.mesh_eval_position(collider_id, q2.face, q2.u, q2.v)
                        d_vec2 = p_moved - surf_p2
                        d_len2 = wp.length(d_vec2)
                        if d_len2 > 1e-6:
                            p_arc = surf_p2 + (d_vec2 / d_len2) * (q2.sign * d_surf)
                        else:
                            p_arc = surf_p2 + wp.mesh_eval_face_normal(collider_id, q2.face) * d_surf
                    else:
                        p_arc = p_moved
                else:
                    new_r = p_moved - root_p
                    new_r_len = wp.length(new_r)
                    if new_r_len > 1e-6:
                        p_arc = root_p + (new_r / new_r_len) * r_len
                    else:
                        p_arc = p_moved

                delta = p_arc - t_p
                d_step = wp.length(delta)
                if d_step > max_step and d_step > 1e-6:
                    delta = delta * (max_step / d_step)
                t_p = t_p + delta
                strand_surface_target[s] = t_p

        t_p = limit_comb_target(pos[vert_starts[s]], p, t_p, cum_len[hero_idx])
        strand_surface_target[s] = t_p
        if drive_position != 0:
            pos[hero_idx] = p + (t_p - p) * 0.85
            vel[hero_idx] = wp.vec3(0.0, 0.0, 0.0)

    @wp.kernel
    def smooth_brush_kernel(
        pos: wp.array(dtype=wp.vec3),
        source: wp.array(dtype=wp.vec3),
        vel: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        vert_starts: wp.array(dtype=int),
        vert_counts: wp.array(dtype=int),
        strand_ids: wp.array(dtype=int),
        local_idx: wp.array(dtype=int),
        arc_lengths: wp.array(dtype=float),
        ray_origin: wp.vec3,
        ray_dir: wp.vec3,
        min_depth: wp.array(dtype=float),
        brush_center: wp.vec3,
        radius: float,
        strength: float,
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0:
            return
        s_id = strand_ids[i]
        l_idx = local_idx[i]
        s_len = vert_counts[s_id]
        if l_idx <= 0 or l_idx >= s_len - 1:
            return
        p = source[i]
        t_along = wp.dot(p - ray_origin, ray_dir)
        if t_along <= 0.0:
            return
        closest = ray_origin + ray_dir * t_along
        dist = wp.length(p - closest)
        if dist >= radius:
            return
        u = dist / radius
        w = (1.0 - u * u) * wp.min(0.45, strength * 0.25)
        arc_span = arc_lengths[i + 1] - arc_lengths[i - 1]
        t = wp.clamp((arc_lengths[i] - arc_lengths[i - 1]) / wp.max(arc_span, 1e-8), 0.0, 1.0)
        avg = wp.lerp(source[i - 1], source[i + 1], t)
        pos[i] = p + (avg - p) * w
        vel[i] = vel[i] * (1.0 - w * 0.5)

    @wp.kernel
    def part_brush_kernel(
        pos: wp.array(dtype=wp.vec3),
        vel: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        ray_origin: wp.vec3,
        ray_dir: wp.vec3,
        min_depth: wp.array(dtype=float),
        radius: float,
        push_step: float,
        inv_dt: float,
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0:
            return
        to_v = pos[i] - ray_origin
        t_along = wp.dot(to_v, ray_dir)
        if t_along <= 0.0:
            return
        closest = ray_origin + ray_dir * t_along
        radial = pos[i] - closest
        dist = wp.length(radial)
        if dist >= radius or dist < 1e-6:
            return
        u = dist / radius
        w = 1.0 - u * u
        delta = (radial / dist) * (push_step * w)
        pos[i] = pos[i] + delta
        vel[i] = vel[i] * 0.25

    @wp.kernel
    def puff_brush_kernel(
        pos: wp.array(dtype=wp.vec3),
        vel: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        collider_id: wp.uint64,
        has_collider: int,
        ray_origin: wp.vec3,
        ray_dir: wp.vec3,
        min_depth: wp.array(dtype=float),
        brush_center: wp.vec3,
        radius: float,
        push_step: float,
        max_query_dist: float,
        inv_dt: float,
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0:
            return
        t_along = wp.dot(pos[i] - ray_origin, ray_dir)
        if t_along <= 0.0:
            return
        closest = ray_origin + ray_dir * t_along
        dist = wp.length(pos[i] - closest)
        if dist >= radius:
            return
        u = dist / radius
        w = 1.0 - u * u
        out_dir = -ray_dir
        if has_collider != 0:
            q = wp.mesh_query_point_sign_normal(collider_id, pos[i], max_query_dist)
            if q.result:
                surf_p = wp.mesh_eval_position(collider_id, q.face, q.u, q.v)
                d_vec = pos[i] - surf_p
                d_len = wp.length(d_vec)
                if d_len > 1e-6:
                    out_dir = d_vec / d_len
        delta = out_dir * (push_step * w)
        pos[i] = pos[i] + delta
        vel[i] = vel[i] * 0.25

    @wp.kernel
    def clump_brush_kernel(
        pos: wp.array(dtype=wp.vec3),
        source: wp.array(dtype=wp.vec3),
        vel: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        vert_counts: wp.array(dtype=int),
        strand_ids: wp.array(dtype=int),
        local_idx: wp.array(dtype=int),
        arc_lengths: wp.array(dtype=float),
        clump_s0: int,
        clump_ln: int,
        ray_origin: wp.vec3,
        ray_dir: wp.vec3,
        min_depth: wp.array(dtype=float),
        brush_center: wp.vec3,
        radius: float,
        strength: float,
        inv_dt: float,
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0 or clump_ln <= 1:
            return
        if i >= clump_s0 and i < clump_s0 + clump_ln:
            return
        p = source[i]
        t_along = wp.dot(p - ray_origin, ray_dir)
        if t_along <= 0.0:
            return
        closest = ray_origin + ray_dir * t_along
        dist = wp.length(p - closest)
        if dist >= radius:
            return
        s_id = strand_ids[i]
        l_idx = local_idx[i]
        s_len = vert_counts[s_id]
        strand_length = arc_lengths[i - l_idx + s_len - 1]
        reference_length = arc_lengths[clump_s0 + clump_ln - 1]
        if strand_length <= 1e-8 or reference_length <= 1e-8:
            return
        t_tip = wp.clamp(arc_lengths[i] / strand_length, 0.0, 1.0)
        reference_arc = t_tip * reference_length
        lower = clump_s0
        upper = clump_s0 + clump_ln - 1
        while upper - lower > 1:
            middle = (lower + upper) // 2
            if arc_lengths[middle] <= reference_arc:
                lower = middle
            else:
                upper = middle
        arc_span = arc_lengths[upper] - arc_lengths[lower]
        t = wp.clamp((reference_arc - arc_lengths[lower]) / wp.max(arc_span, 1e-8), 0.0, 1.0)
        target = wp.lerp(source[lower], source[upper], t)
        u = dist / radius
        w = (1.0 - u * u) * (0.15 + 0.85 * t_tip) * wp.min(0.45, strength * 0.25)
        delta = (target - p) * w
        delta *= wp.min(1.0, (radius * 0.15) / wp.max(wp.length(delta), 1e-8))
        pos[i] = p + delta
        vel[i] = vel[i] * 0.25

    @wp.kernel
    def mark_active_brush_strands_kernel(
        pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        strand_ids: wp.array(dtype=int),
        ray_origin: wp.vec3,
        ray_dir: wp.vec3,
        min_depth: wp.array(dtype=float),
        brush_center: wp.vec3,
        radius: float,
        use_ray_dist: int,
        active_strand: wp.array(dtype=int),
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0:
            return
        to_v = pos[i] - ray_origin
        t_along = wp.dot(to_v, ray_dir)
        if t_along <= 0.0:
            return
        closest = ray_origin + ray_dir * t_along
        dist = wp.length(pos[i] - closest)
        if dist < radius:
            active_strand[strand_ids[i]] = 1

    @wp.kernel
    def sync_active_inv_mass_kernel(
        inv_mass: wp.array(dtype=float),
        strand_ids: wp.array(dtype=int),
        active_strand: wp.array(dtype=int),
        pinned_strand: wp.array(dtype=int),
        brush_inv_mass: wp.array(dtype=float),
    ):
        i = wp.tid()
        sid = strand_ids[i]
        if active_strand[sid] != 0 and pinned_strand[sid] == 0:
            brush_inv_mass[i] = inv_mass[i]
        else:
            brush_inv_mass[i] = 0.0

    @wp.kernel
    def pin_brush_kernel(
        pos: wp.array(dtype=wp.vec3),
        vert_starts: wp.array(dtype=int),
        counts: wp.array(dtype=int),
        ray_origin: wp.vec3,
        ray_dir: wp.vec3,
        min_depth: wp.array(dtype=float),
        brush_center: wp.vec3,
        radius: float,
        use_ray_dist: int,
        pin_value: int,
        pinned_strand: wp.array(dtype=int),
    ):
        s = wp.tid()
        start = vert_starts[s]
        cnt = counts[s]
        for k in range(cnt):
            p = pos[start + k]
            to_v = p - ray_origin
            t_along = wp.dot(to_v, ray_dir)
            if t_along > 0.0:
                closest = ray_origin + ray_dir * t_along
                d = wp.length(p - closest)
                if d < radius:
                    pinned_strand[s] = pin_value
                    return

    @wp.kernel
    def snap_root_to_mesh_kernel(
        mesh: wp.uint64,
        pts: wp.array(dtype=wp.vec3),
        max_d: float,
        out_pts: wp.array(dtype=wp.vec3),
        out_hit: wp.array(dtype=int),
    ):
        tid = wp.tid()
        q = wp.mesh_query_point_no_sign(mesh, pts[tid], max_d)
        if q.result:
            out_pts[tid] = wp.mesh_eval_position(mesh, q.face, q.u, q.v)
            out_hit[tid] = 1

    @wp.kernel
    def ray_mesh_hit_kernel(
        mesh: wp.uint64,
        ro: wp.array(dtype=wp.vec3),
        rd: wp.array(dtype=wp.vec3),
        max_d: float,
        out_t_arr: wp.array(dtype=float),
    ):
        h = wp.mesh_query_ray(mesh, ro[0], rd[0], max_d)
        if h.result:
            out_t_arr[0] = h.t

    @wp.kernel
    def enforce_strand_length_kernel(
        pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        vert_starts: wp.array(dtype=int),
        counts: wp.array(dtype=int),
        cum_len: wp.array(dtype=float),
        max_stretch: float,
    ):
        s = wp.tid()
        s0 = vert_starts[s]
        ln = counts[s]
        if ln <= 1:
            return
        prev_raw = pos[s0]
        prev_out = prev_raw
        for k in range(1, ln):
            idx = s0 + k
            cur_raw = pos[idx]
            if inv_mass[idx] <= 0.0:
                prev_raw = cur_raw
                prev_out = cur_raw
                continue
            rest_seg = (cum_len[idx] - cum_len[idx - 1]) * max_stretch
            diff = cur_raw - prev_raw
            cur_len = wp.length(diff)
            if cur_len > rest_seg and cur_len > 1e-7:
                diff = diff * (rest_seg / cur_len)
            prev_out = prev_out + diff
            pos[idx] = prev_out
            prev_raw = cur_raw

    @wp.kernel
    def shape_bend_constraint_kernel(
        pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        anim_rest: wp.array(dtype=wp.vec3),
        seg_v0: wp.array(dtype=int),
        seg_v1: wp.array(dtype=int),
        curl_stiffness: float,
        delta_pos: wp.array(dtype=wp.vec3),
        delta_w: wp.array(dtype=float),
    ):
        s = wp.tid()
        if curl_stiffness <= 1e-6:
            return
        i0 = seg_v0[s]
        i1 = seg_v1[s]
        w0 = inv_mass[i0]
        w1 = inv_mass[i1]
        w_sum = w0 + w1
        if w_sum <= 1e-8:
            return
        rest_vec = anim_rest[i1] - anim_rest[i0]
        cur_vec = pos[i1] - pos[i0]
        err = rest_vec - cur_vec
        corr = err * (curl_stiffness / w_sum)
        if w0 > 0.0:
            wp.atomic_add(delta_pos, i0, -corr * w0)
            wp.atomic_add(delta_w, i0, 1.0)
        if w1 > 0.0:
            wp.atomic_add(delta_pos, i1, corr * w1)
            wp.atomic_add(delta_w, i1, 1.0)

    @wp.kernel
    def vol_grid_accum_kernel(
        pos: wp.array(dtype=wp.vec3),
        inv_cell: float,
        table_mask: int,
        grid_pos: wp.array(dtype=wp.vec3),
        grid_count: wp.array(dtype=int),
    ):
        i = wp.tid()
        p = pos[i]
        ix = int(wp.floor(p[0] * inv_cell))
        iy = int(wp.floor(p[1] * inv_cell))
        iz = int(wp.floor(p[2] * inv_cell))
        h = ((ix * 73856093) ^ (iy * 19349663) ^ (iz * 83492791)) & table_mask
        wp.atomic_add(grid_pos, h, p)
        wp.atomic_add(grid_count, h, 1)

    @wp.kernel
    def vol_grid_repel_kernel(
        pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        root_t: wp.array(dtype=float),
        inv_cell: float,
        cell_size: float,
        table_mask: int,
        vol_strength: float,
        grid_pos: wp.array(dtype=wp.vec3),
        grid_count: wp.array(dtype=int),
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0:
            return
        p = pos[i]
        ix = int(wp.floor(p[0] * inv_cell))
        iy = int(wp.floor(p[1] * inv_cell))
        iz = int(wp.floor(p[2] * inv_cell))
        h = ((ix * 73856093) ^ (iy * 19349663) ^ (iz * 83492791)) & table_mask
        cnt = grid_count[h]
        if cnt <= 6:
            return
        center = grid_pos[h] / float(cnt)
        diff = p - center
        dist = wp.length(diff)
        if dist <= 1e-6 or dist > cell_size:
            return
        excess = wp.min(1.0, float(cnt - 6) / 18.0)
        push = vol_strength * excess * cell_size * 0.02 * root_t[i]
        pos[i] = p + (diff / dist) * push

    @wp.kernel
    def world_to_local_kernel(
        world_pos: wp.array(dtype=wp.vec3),
        reference_world: wp.array(dtype=wp.vec3),
        reference_local: wp.array(dtype=wp.vec3),
        c0: wp.vec3,
        c1: wp.vec3,
        c2: wp.vec3,
        local_pos: wp.array(dtype=wp.vec3),
    ):
        i = wp.tid()
        # Convert displacement from an exact USD/world pair so untouched
        # points don't acquire deltas from the inverse transform's rounding.
        p = world_pos[i] - reference_world[i]
        local_pos[i] = reference_local[i] + c0 * p[0] + c1 * p[1] + c2 * p[2]

    @wp.kernel
    def measure_stroke_lengths(
        points: wp.array(dtype=wp.vec3),
        starts: wp.array(dtype=int),
        counts: wp.array(dtype=int),
        lengths: wp.array(dtype=float),
    ):
        strand = wp.tid()
        first = starts[strand]
        end = first + counts[strand]
        if first < end:
            lengths[first] = 0.0
            length = float(0.0)
            for point in range(first + 1, end):
                length += wp.length(points[point] - points[point - 1])
                lengths[point] = length

    @wp.kernel
    def grab_target_kernel(
        active_guides: wp.array(dtype=int),
        pos: wp.array(dtype=wp.vec3),
        vert_starts: wp.array(dtype=int),
        vert_counts: wp.array(dtype=int),
        cum_len: wp.array(dtype=float),
        strand_hero_k: wp.array(dtype=int),
        strand_hero_offset: wp.array(dtype=wp.vec3),
        strand_surface_target: wp.array(dtype=wp.vec3),
        strand_brush_w: wp.array(dtype=float),
        collider_id: wp.uint64,
        has_collider: int,
        margin: float,
        max_query_dist: float,
        cursor_target: wp.vec3,
        is_direct: int,
    ):
        s = wp.tid()
        if is_direct == 0:
            s = active_guides[s]
        k = strand_hero_k[s]
        if k <= 0 or strand_brush_w[s] <= 0.0:
            return
        s0 = vert_starts[s]
        ln = vert_counts[s]
        target = cursor_target + strand_hero_offset[s]
        root_p = pos[s0]
        to_target = target - root_p
        dist_root = wp.length(to_target)
        while k < ln and dist_root > cum_len[s0 + k]:
            k = k + 1
        if k >= ln:
            k = ln - 1
            max_r = cum_len[s0 + k]
            if dist_root > max_r and dist_root > 1e-6:
                target = root_p + to_target * (max_r / dist_root)
        strand_hero_k[s] = k
        if has_collider != 0:
            q = wp.mesh_query_point_sign_normal(collider_id, target, max_query_dist)
            if q.result:
                closest = wp.mesh_eval_position(collider_id, q.face, q.u, q.v)
                diff = target - closest
                dist = wp.length(diff)
                if dist * q.sign < margin:
                    face_n = wp.mesh_eval_face_normal(collider_id, q.face)
                    nrm = face_n
                    if dist > 1e-6:
                        nrm = diff / dist
                        if q.sign < 0.0:
                            nrm = -nrm
                            if wp.dot(nrm, face_n) < 0.0:
                                nrm = face_n
                    target = closest + nrm * margin
        strand_surface_target[s] = target
        bw = wp.min(1.0, strand_brush_w[s] * 1.5)
        delta = (target - pos[s0 + k]) * bw
        pos[s0 + k] = pos[s0 + k] + delta
        if k > 1:
            pos[s0 + k - 1] = pos[s0 + k - 1] + delta * 0.5
        if k + 1 < ln:
            pos[s0 + k + 1] = pos[s0 + k + 1] + delta * 0.5

    kernels = (
        predict_kernel,
        zero_accum_kernel,
        stretch_constraint_kernel,
        bend_constraint_kernel,
        apply_accum_kernel,
        collide_mesh_kernel,
        update_vel_kernel,
        brush_min_depth_kernel,
        init_grab_clump_kernel,
        grab_clump_kernel,
        strand_ray_dist_kernel,
        comb_sweep_kernel,
        smooth_brush_kernel,
        part_brush_kernel,
        puff_brush_kernel,
        clump_brush_kernel,
        mark_active_brush_strands_kernel,
        sync_active_inv_mass_kernel,
        world_to_local_kernel,
        collide_segments_kernel,
        pin_brush_kernel,
        shape_bend_constraint_kernel,
        vol_grid_accum_kernel,
        vol_grid_repel_kernel,
        surface_comb_kernel,
        snap_root_to_mesh_kernel,
        ray_mesh_hit_kernel,
        enforce_strand_length_kernel,
        mask_backfacing_roots_kernel,
        measure_stroke_lengths,
        grab_target_kernel,
    )
    wp._orchflow_hair_kernels = kernels
    return kernels


def _build_rgba_quantize_kernel(wp):
    cached = getattr(wp, "_orchflow_rgba_quantize_kernel", None)
    if cached is not None:
        return cached

    @wp.kernel
    def float_to_rgba_u8_kernel(
        src: wp.array3d(dtype=float),
        channels: int,
        dst: wp.array3d(dtype=wp.uint8),
    ):
        y, x = wp.tid()
        r = wp.clamp(src[y, x, 0] * 255.0 + 0.5, 0.0, 255.0)
        g = wp.clamp(src[y, x, 1] * 255.0 + 0.5, 0.0, 255.0)
        b = wp.clamp(src[y, x, 2] * 255.0 + 0.5, 0.0, 255.0)
        a = 255.0
        if channels >= 4:
            a = wp.clamp(src[y, x, 3] * 255.0 + 0.5, 0.0, 255.0)
        dst[y, x, 0] = wp.uint8(int(r))
        dst[y, x, 1] = wp.uint8(int(g))
        dst[y, x, 2] = wp.uint8(int(b))
        dst[y, x, 3] = wp.uint8(int(a))

    wp._orchflow_rgba_quantize_kernel = float_to_rgba_u8_kernel
    return float_to_rgba_u8_kernel


def _build_subsample_kernels(wp):
    cached = getattr(wp, "_orchflow_subsample_kernels", None)
    if cached is not None:
        return cached

    @wp.kernel
    def mask_subsampled_inv_mass_kernel(
        inv_mass: wp.array(dtype=float),
        local_idx: wp.array(dtype=int),
        strand_ids: wp.array(dtype=int),
        counts: wp.array(dtype=int),
        stride: int,
    ):
        i = wp.tid()
        if stride <= 1 or inv_mass[i] <= 0.0:
            return
        k = local_idx[i]
        c = counts[strand_ids[i]]
        if k > 0 and k < c - 1 and (k % stride) != 0:
            inv_mass[i] = 0.0

    @wp.kernel
    def interpolate_subsampled_strands_kernel(
        pos: wp.array(dtype=wp.vec3),
        prev_pos: wp.array(dtype=wp.vec3),
        starts: wp.array(dtype=int),
        counts: wp.array(dtype=int),
        active_strands: wp.array(dtype=int),
        active_only: int,
        stride: int,
        blend_mode: int,
    ):
        idx = wp.tid()
        s = active_strands[idx] if active_only != 0 else idx
        if stride <= 1:
            return
        v0 = starts[s]
        c = counts[s]
        if c <= 2:
            return
        for k in range(1, c - 1):
            rem = k % stride
            if rem == 0:
                continue
            k0 = k - rem
            k1 = wp.min(k0 + stride, c - 1)
            span = wp.max(float(k1 - k0), 1.0)
            t = float(k - k0) / span
            if blend_mode == 1:
                pos[v0 + k] = pos[v0 + k0] * (1.0 - t) + pos[v0 + k1] * t
            else:
                d0 = pos[v0 + k0] - prev_pos[v0 + k0]
                d1 = pos[v0 + k1] - prev_pos[v0 + k1]
                pos[v0 + k] = prev_pos[v0 + k] + d0 * (1.0 - t) + d1 * t

    cached = (mask_subsampled_inv_mass_kernel, interpolate_subsampled_strands_kernel)
    wp._orchflow_subsample_kernels = cached
    return cached


def _build_smooth_all_kernel(wp):
    from hair_draw_kernels import _build_smooth_all_kernel as _fn
    return _fn(wp)


def _build_brush_collider_kernel(wp):
    from hair_draw_kernels import _build_brush_collider_kernel as _fn
    return _fn(wp)


def _build_brush_circle_kernel(wp):
    from hair_draw_kernels import _build_brush_circle_kernel as _fn
    return _fn(wp)


def _build_draw_lock_kernels(wp):
    from hair_draw_kernels import _build_draw_lock_kernels as _fn
    return _fn(wp)


def _build_polyline_kernel(wp):
    from hair_draw_kernels import _build_polyline_kernel as _fn
    return _fn(wp)


def draw_polyline_cuda(dev_u8, h, w, pts_norm):
    if not pts_norm or len(pts_norm) < 2:
        return
    wp, np, device = _ensure_warp()
    arr = np.asarray(pts_norm, dtype=np.float32).reshape(-1, 2)
    pts_px = np.empty_like(arr)
    pts_px[:, 0] = arr[:, 0] * float(w)
    pts_px[:, 1] = arr[:, 1] * float(h)
    pts_wp = wp.array(pts_px, dtype=wp.vec2, device=device)
    kernel = _build_polyline_kernel(wp)
    wp.launch(kernel, dim=len(pts_px) - 1, inputs=[dev_u8, int(h), int(w), pts_wp, int(len(pts_px))], device=device)


def draw_brush_circle_cuda(dev_u8, h, w, nx, ny, radius_px):
    wp, _np, device = _ensure_warp()
    if radius_px <= 1.0:
        return
    kernel = _build_brush_circle_kernel(wp)
    wp.launch(
        kernel,
        dim=(h, w),
        inputs=[dev_u8, int(h), int(w), float(nx) * float(w), float(ny) * float(h), float(radius_px)],
        device=device,
    )


def render_var_to_cuda_u8(render_var, ovrtx_mod, cache_dict):
    """Map an ovrtx RenderVar on CUDA and quantize to (H, W, 4) uint8 on GPU without host readback."""
    wp, _np, device = _ensure_warp()
    with render_var.map(device=ovrtx_mod.Device.CUDA) as rv:
        rv.wait()
        w_arr = wp.from_dlpack(rv)
        shape = w_arr.shape
        if len(shape) != 3 or shape[2] not in (3, 4):
            raise RuntimeError(f"Unexpected CUDA render_var shape: {shape}")
        h, w, c = int(shape[0]), int(shape[1]), int(shape[2])
        target_shape = (h, w, 4)
        dev_u8 = cache_dict.get("dev_u8")
        if dev_u8 is None or dev_u8.shape != target_shape:
            dev_u8 = wp.empty(target_shape, dtype=wp.uint8, device=device)
            cache_dict["dev_u8"] = dev_u8
        if w_arr.dtype == wp.uint8 and c == 4:
            wp.copy(dev_u8, w_arr)
        elif w_arr.dtype == wp.float32:
            kernel = _build_rgba_quantize_kernel(wp)
            wp.launch(kernel, dim=(h, w), inputs=[w_arr, c, dev_u8], device=device)
        else:
            return None
        stream_ptr = int(wp.get_stream(device).cuda_stream)
        return dev_u8, h, w, stream_ptr


def read_render_var_rgba_cuda(render_var, ovrtx_mod, cache_dict):
    """Map an ovrtx RenderVar on CUDA, quantize float->uint8 on GPU if needed, and DMA into pinned host RAM."""
    wp, np, device = _ensure_warp()
    with render_var.map(device=ovrtx_mod.Device.CUDA) as rv:
        rv.wait()
        w_arr = wp.from_dlpack(rv)
        shape = w_arr.shape
        if len(shape) != 3 or shape[2] not in (3, 4):
            raise RuntimeError(f"Unexpected CUDA render_var shape: {shape}")
        h, w, c = int(shape[0]), int(shape[1]), int(shape[2])
        target_shape = (h, w, 4)
        pinned = cache_dict.get("pinned_host")
        if pinned is None or pinned.shape != target_shape:
            pinned = wp.empty(target_shape, dtype=wp.uint8, device="cpu", pinned=True)
            cache_dict["pinned_host"] = pinned
        if w_arr.dtype == wp.uint8 and c == 4:
            wp.copy(pinned, w_arr)
        elif w_arr.dtype == wp.float32:
            dev_u8 = cache_dict.get("dev_u8")
            if dev_u8 is None or dev_u8.shape != target_shape:
                dev_u8 = wp.empty(target_shape, dtype=wp.uint8, device=device)
                cache_dict["dev_u8"] = dev_u8
            kernel = _build_rgba_quantize_kernel(wp)
            wp.launch(kernel, dim=(h, w), inputs=[w_arr, c, dev_u8], device=device)
            wp.copy(pinned, dev_u8)
        else:
            return None
        wp.synchronize_device(device)
        return pinned.numpy()


