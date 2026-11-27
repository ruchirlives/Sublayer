"""Compact NVIDIA Warp kernels for Draw Lock, Smooth All, brush collider enforcement, and viewport overlays.

Kept in its own module separate from `hair_kernels.py` and initialized in a single pass
so Warp compiles this small module once without invalidating the main solver's module hash.
"""


def _init_draw_kernels(wp):
    cached = getattr(wp, "_orchflow_draw_kernel_bundle", None)
    if cached is not None:
        return cached

    @wp.kernel
    def smooth_all_parabolic_kernel(
        pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        pinned: wp.array(dtype=int),
        vert_starts: wp.array(dtype=int),
        counts: wp.array(dtype=int),
        arc_lengths: wp.array(dtype=float),
        collider_id: wp.uint64,
        has_collider: int,
        margin: float,
        max_query_dist: float,
        strength: float,
    ):
        s = wp.tid()
        if pinned[s] != 0:
            return
        v0 = vert_starts[s]
        c = counts[s]
        if c < 3:
            return
        p0 = pos[v0]
        p1 = pos[v0 + c - 1]
        a0 = arc_lengths[v0]
        total_arc = arc_lengths[v0 + c - 1] - a0
        inv_c = 1.0 / float(c - 1)

        num = wp.vec3(0.0, 0.0, 0.0)
        den = float(0.0)
        for k in range(1, c - 1):
            idx = v0 + k
            t = (arc_lengths[idx] - a0) / total_arc if total_arc > 1e-8 else float(k) * inv_c
            t = wp.clamp(t, 0.0, 1.0)
            b = t * (1.0 - t)
            chord = p0 * (1.0 - t) + p1 * t
            num += (pos[idx] - chord) * b
            den += b * b

        coeff = num / den if den > 1e-12 else wp.vec3(0.0, 0.0, 0.0)
        w = wp.clamp(strength * 0.65, 0.1, 1.0)
        any_collided = int(0)

        for k in range(1, c - 1):
            idx = v0 + k
            if inv_mass[idx] <= 0.0:
                continue
            old_p = pos[idx]
            t = (arc_lengths[idx] - a0) / total_arc if total_arc > 1e-8 else float(k) * inv_c
            t = wp.clamp(t, 0.0, 1.0)
            target = p0 * (1.0 - t) + p1 * t + coeff * (t * (1.0 - t))
            new_p = old_p + (target - old_p) * w

            if has_collider != 0:
                move = new_p - old_p
                move_len = wp.length(move)
                if move_len > 1e-6:
                    move_dir = move / move_len
                    rq = wp.mesh_query_ray(collider_id, old_p, move_dir, move_len + margin)
                    if rq.result and rq.t <= move_len + margin:
                        safe_t = wp.max(0.0, rq.t - margin)
                        new_p = old_p + move_dir * safe_t
                        any_collided = 1
                q = wp.mesh_query_point_sign_normal(collider_id, new_p, max_query_dist)
                if q.result:
                    closest = wp.mesh_eval_position(collider_id, q.face, q.u, q.v)
                    diff = new_p - closest
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
                        new_p = closest + nrm * margin
                        any_collided = 1

            pos[idx] = new_p

        if any_collided != 0 and c >= 4:
            for _pass in range(2):
                prev_pt = pos[v0]
                for k in range(1, c - 1):
                    idx = v0 + k
                    cur_pt = pos[idx]
                    next_pt = pos[idx + 1]
                    if inv_mass[idx] > 0.0:
                        span = arc_lengths[idx + 1] - arc_lengths[idx - 1]
                        f = wp.clamp((arc_lengths[idx] - arc_lengths[idx - 1]) / wp.max(span, 1e-8), 0.0, 1.0)
                        avg = prev_pt * (1.0 - f) + next_pt * f
                        relaxed = cur_pt + (avg - cur_pt) * 0.45
                        if has_collider != 0:
                            rmove = relaxed - cur_pt
                            rmove_len = wp.length(rmove)
                            if rmove_len > 1e-6:
                                rmove_dir = rmove / rmove_len
                                rrq = wp.mesh_query_ray(collider_id, cur_pt, rmove_dir, rmove_len + margin)
                                if rrq.result and rrq.t <= rmove_len + margin:
                                    relaxed = cur_pt + rmove_dir * wp.max(0.0, rrq.t - margin)
                            q = wp.mesh_query_point_sign_normal(collider_id, relaxed, max_query_dist)
                            if q.result:
                                closest = wp.mesh_eval_position(collider_id, q.face, q.u, q.v)
                                diff = relaxed - closest
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
                                    relaxed = closest + nrm * margin
                        pos[idx] = relaxed
                        prev_pt = relaxed
                    else:
                        prev_pt = cur_pt

    @wp.kernel
    def enforce_brush_collider_kernel(
        prev_pos: wp.array(dtype=wp.vec3),
        pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        vert_starts: wp.array(dtype=int),
        counts: wp.array(dtype=int),
        cum_len: wp.array(dtype=float),
        collider_id: wp.uint64,
        margin: float,
        max_query_dist: float,
    ):
        s = wp.tid()
        v0 = vert_starts[s]
        c = counts[s]
        if c < 2:
            return
        pos[v0] = prev_pos[v0]
        for k in range(1, c):
            idx = v0 + k
            if inv_mass[idx] <= 0.0:
                continue
            old_p = prev_pos[idx]
            cur_p = pos[idx]
            move = cur_p - old_p
            move_len = wp.length(move)
            if move_len > 1e-6:
                move_dir = move / move_len
                rq = wp.mesh_query_ray(collider_id, old_p, move_dir, move_len + margin)
                if rq.result and rq.t <= move_len + margin:
                    cur_p = old_p + move_dir * wp.max(0.0, rq.t - margin)
            prev_p = pos[idx - 1]
            seg = cur_p - prev_p
            seg_len = wp.length(seg)
            if seg_len > 1e-6:
                seg_dir = seg / seg_len
                srq = wp.mesh_query_ray(collider_id, prev_p, seg_dir, seg_len)
                if srq.result and srq.t < seg_len and (k > 1 or srq.t > seg_len * 0.15):
                    hit_p = wp.mesh_eval_position(collider_id, srq.face, srq.u, srq.v)
                    hit_n = wp.mesh_eval_face_normal(collider_id, srq.face)
                    if wp.dot(seg_dir, hit_n) > 0.0:
                        hit_n = -hit_n
                    cur_p = hit_p + hit_n * margin
            q = wp.mesh_query_point_sign_normal(collider_id, cur_p, max_query_dist)
            if q.result:
                closest = wp.mesh_eval_position(collider_id, q.face, q.u, q.v)
                diff = cur_p - closest
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
                    cur_p = closest + nrm * margin
            rest_seg = wp.max(cum_len[idx] - cum_len[idx - 1], 1e-6)
            max_seg = rest_seg * 1.04
            seg2 = cur_p - prev_p
            seg2_len = wp.length(seg2)
            if seg2_len > max_seg:
                cur_p = prev_p + (seg2 / seg2_len) * max_seg
                q2 = wp.mesh_query_point_sign_normal(collider_id, cur_p, max_query_dist)
                if q2.result:
                    c2 = wp.mesh_eval_position(collider_id, q2.face, q2.u, q2.v)
                    d2 = cur_p - c2
                    dl2 = wp.length(d2)
                    if dl2 * q2.sign < margin:
                        fn2 = wp.mesh_eval_face_normal(collider_id, q2.face)
                        n2 = fn2
                        if dl2 > 1e-6:
                            n2 = d2 / dl2
                            if q2.sign < 0.0:
                                n2 = -n2
                                if wp.dot(n2, fn2) < 0.0:
                                    n2 = fn2
                        cur_p = c2 + n2 * margin
            pos[idx] = cur_p

    @wp.kernel
    def draw_brush_circle_kernel(
        img: wp.array3d(dtype=wp.uint8),
        h: int,
        w: int,
        cx: float,
        cy: float,
        r_px: float,
    ):
        y, x = wp.tid()
        if x >= w or y >= h:
            return
        dx = float(x) - cx
        dy = float(y) - cy
        d = wp.sqrt(dx * dx + dy * dy)
        if wp.abs(d - r_px) <= 1.25:
            img[y, x, 0] = wp.uint8(60)
            img[y, x, 1] = wp.uint8(220)
            img[y, x, 2] = wp.uint8(255)
            img[y, x, 3] = wp.uint8(255)

    @wp.kernel
    def raycast_curve_to_mesh_kernel(
        mesh: wp.uint64,
        ro: wp.vec3,
        rd_arr: wp.array(dtype=wp.vec3),
        max_d: float,
        out_pos: wp.array(dtype=wp.vec3),
        out_nrm: wp.array(dtype=wp.vec3),
        out_t: wp.array(dtype=float),
        out_hit: wp.array(dtype=int),
    ):
        tid = wp.tid()
        rd = rd_arr[tid]
        h = wp.mesh_query_ray(mesh, ro, rd, max_d)
        if h.result:
            out_pos[tid] = wp.mesh_eval_position(mesh, h.face, h.u, h.v)
            n = wp.mesh_eval_face_normal(mesh, h.face)
            if wp.dot(n, rd) > 0.0:
                n = -n
            n_len = wp.length(n)
            if n_len > 1e-6:
                n = n / n_len
            out_nrm[tid] = n
            out_t[tid] = h.t
            out_hit[tid] = 1

    @wp.kernel
    def project_points_outside_mesh_kernel(
        mesh: wp.uint64,
        pts: wp.array(dtype=wp.vec3),
        max_d: float,
        margin: float,
    ):
        tid = wp.tid()
        p = pts[tid]
        q = wp.mesh_query_point_sign_normal(mesh, p, max_d)
        if q.result:
            closest = wp.mesh_eval_position(mesh, q.face, q.u, q.v)
            diff = p - closest
            dist = wp.length(diff)
            if dist * q.sign < margin:
                face_n = wp.mesh_eval_face_normal(mesh, q.face)
                nrm = face_n
                if dist > 1e-6:
                    nrm = diff / dist
                    if q.sign < 0.0:
                        nrm = -nrm
                        if wp.dot(nrm, face_n) < 0.0:
                            nrm = face_n
                pts[tid] = closest + nrm * margin

    @wp.kernel
    def drape_points_to_mesh_kernel(
        mesh: wp.uint64,
        pts: wp.array(dtype=wp.vec3),
        nrms: wp.array(dtype=wp.vec3),
        offsets: wp.array(dtype=float),
        lift: float,
        k_verts: int,
        max_snap_dist: float,
    ):
        tid = wp.tid()
        p = pts[tid]
        n = nrms[tid]
        n_len = wp.length(n)
        if n_len < 1e-6:
            return
        n = n / n_len
        ro = p + n * lift
        rd = -n
        h = wp.mesh_query_ray(mesh, ro, rd, lift * 2.5)
        if h.result:
            surf_p = wp.mesh_eval_position(mesh, h.face, h.u, h.v)
            surf_n = wp.mesh_eval_face_normal(mesh, h.face)
            if wp.dot(surf_n, n) < 0.0:
                surf_n = -surf_n
            sn_len = wp.length(surf_n)
            if sn_len > 1e-6:
                surf_n = surf_n / sn_len
            else:
                surf_n = n
            pts[tid] = surf_p + surf_n * offsets[tid]
        elif k_verts > 0 and (tid % k_verts) == 0:
            q = wp.mesh_query_point_sign_normal(mesh, p, max_snap_dist)
            if q.result:
                closest = wp.mesh_eval_position(mesh, q.face, q.u, q.v)
                surf_n = wp.mesh_eval_face_normal(mesh, q.face)
                if wp.dot(surf_n, n) < 0.0:
                    surf_n = -surf_n
                sn_len = wp.length(surf_n)
                if sn_len > 1e-6:
                    surf_n = surf_n / sn_len
                else:
                    surf_n = n
                pts[tid] = closest + surf_n * offsets[tid]

    @wp.kernel
    def draw_polyline_kernel(
        img: wp.array(dtype=wp.uint8, ndim=3),
        h: int,
        w: int,
        pts_px: wp.array(dtype=wp.vec2),
        n_pts: int,
    ):
        seg_i = wp.tid()
        if seg_i >= n_pts - 1:
            return
        p0 = pts_px[seg_i]
        p1 = pts_px[seg_i + 1]
        dx = p1[0] - p0[0]
        dy = p1[1] - p0[1]
        steps = int(wp.ceil(wp.max(wp.abs(dx), wp.abs(dy)))) + 1
        if steps < 1:
            steps = 1
        if steps > 4096:
            steps = 4096
        for k in range(steps + 1):
            t = float(k) / float(steps)
            cx = int(wp.round(p0[0] + dx * t))
            cy = int(wp.round(p0[1] + dy * t))
            for oy in range(-1, 2):
                yy = cy + oy
                if yy < 0 or yy >= h:
                    continue
                for ox in range(-1, 2):
                    xx = cx + ox
                    if xx < 0 or xx >= w:
                        continue
                    img[yy, xx, 0] = wp.uint8(52)
                    img[yy, xx, 1] = wp.uint8(211)
                    img[yy, xx, 2] = wp.uint8(153)
                    img[yy, xx, 3] = wp.uint8(255)

    bundle = {
        "smooth_all": smooth_all_parabolic_kernel,
        "enforce_collider": enforce_brush_collider_kernel,
        "brush_circle": draw_brush_circle_kernel,
        "draw_lock": (
            raycast_curve_to_mesh_kernel,
            project_points_outside_mesh_kernel,
            drape_points_to_mesh_kernel,
        ),
        "polyline": draw_polyline_kernel,
    }
    wp._orchflow_draw_kernel_bundle = bundle
    return bundle


def _build_smooth_all_kernel(wp):
    return _init_draw_kernels(wp)["smooth_all"]


def _build_brush_collider_kernel(wp):
    return _init_draw_kernels(wp)["enforce_collider"]


def _build_brush_circle_kernel(wp):
    return _init_draw_kernels(wp)["brush_circle"]


def _build_draw_lock_kernels(wp):
    return _init_draw_kernels(wp)["draw_lock"]


def _build_polyline_kernel(wp):
    return _init_draw_kernels(wp)["polyline"]
