"""NVIDIA Warp GPU kernels for Cache Sculptor mesh cache sculpting and cloth physics brushes.

Provides:
- Lazy Warp/CUDA initialization (reusing OrchFlow's package directory).
- Live vertex normal recomputation on GPU.
- Sculpt brushes: Grab, Smooth (Taubin/Laplacian), Inflate/Push, Pinch, Flatten, Mask/Unmask.
- Interactive XPBD Cloth Physics Brush kernels:
  - Soft brush spring pull / gravity drape / normal push
  - Structural edge-length stretch constraints (Jacobi XPBD)
  - Interior shared-edge isometric bending constraints (crisp cloth folds)
  - `wp.Mesh` BVH collider push-out with Coulomb tangential friction
  - World-to-USD-local GPU transform for zero-copy Omniverse RTX uploads.
"""

from __future__ import annotations

import os
import sys

_WP = None
_WP_INITIALIZED = False
_WP_DEVICE = None
_KERNELS_BUILT = False


def get_warp():
    """Lazily import and initialize NVIDIA Warp, returning `(wp, device)` or `(None, None)`."""
    global _WP, _WP_INITIALIZED, _WP_DEVICE
    if _WP_INITIALIZED:
        return _WP, _WP_DEVICE
    _WP_INITIALIZED = True
    for pkg_dir in (
        os.environ.get("ORCHFLOW_PACKAGES_DIR"),
        r"I:\AI\OrchFlow-packages\py312",
    ):
        if pkg_dir and os.path.isdir(pkg_dir) and pkg_dir not in sys.path:
            sys.path.append(pkg_dir)
    try:
        import warp as wp

        wp.config.quiet = True
        wp.init()
        dev = "cuda:0" if wp.is_cuda_available() else "cpu"
        _WP = wp
        _WP_DEVICE = dev
        _build_kernels(wp)
        return _WP, _WP_DEVICE
    except Exception:
        _WP = None
        _WP_DEVICE = None
        return None, None


# Kernel handles populated by _build_kernels(wp)
accum_face_normals_kernel = None
normalize_normals_kernel = None
brush_grab_kernel = None
brush_inflate_kernel = None
brush_pinch_kernel = None
brush_flatten_kernel = None
brush_smooth_step_kernel = None
brush_mask_kernel = None
cloth_prepare_patch_kernel = None
cloth_predict_kernel = None
cloth_stretch_jacobi_kernel = None
cloth_bend_jacobi_kernel = None
cloth_apply_jacobi_kernel = None
cloth_collider_kernel = None
cloth_update_vel_kernel = None
cloth_reconstruct_fine_kernel = None
world_to_local_kernel = None
cloth_sim_predict_kernel = None
cloth_sim_update_vel_kernel = None
cloth_lra_tether_kernel = None
cloth_self_collision_kernel = None
cloth_bind_collider_pins_kernel = None
cloth_eval_collider_pins_kernel = None
cloth_lerp_collider_pts_kernel = None


def _build_kernels(wp):
    global _KERNELS_BUILT
    global accum_face_normals_kernel, normalize_normals_kernel
    global brush_grab_kernel, brush_inflate_kernel, brush_pinch_kernel
    global brush_flatten_kernel, brush_smooth_step_kernel, brush_mask_kernel
    global cloth_prepare_patch_kernel, cloth_predict_kernel
    global cloth_stretch_jacobi_kernel, cloth_bend_jacobi_kernel
    global cloth_apply_jacobi_kernel, cloth_collider_kernel
    global cloth_update_vel_kernel, cloth_reconstruct_fine_kernel, world_to_local_kernel
    global cloth_sim_predict_kernel, cloth_sim_update_vel_kernel, cloth_lra_tether_kernel, cloth_self_collision_kernel
    global cloth_bind_collider_pins_kernel, cloth_eval_collider_pins_kernel
    global cloth_lerp_collider_pts_kernel

    if _KERNELS_BUILT:
        return
    _KERNELS_BUILT = True

    @wp.func
    def smooth_falloff(dist: float, radius: float) -> float:
        if dist >= radius or radius <= 1.0e-8:
            return 0.0
        t = dist / radius
        u = 1.0 - t * t
        return u * u

    @wp.kernel
    def _accum_face_normals(
        pos: wp.array(dtype=wp.vec3),
        tris: wp.array(dtype=int),
        normals: wp.array(dtype=wp.vec3),
    ):
        tid = wp.tid()
        i0 = tris[tid * 3 + 0]
        i1 = tris[tid * 3 + 1]
        i2 = tris[tid * 3 + 2]
        p0 = pos[i0]
        p1 = pos[i1]
        p2 = pos[i2]
        fn = wp.cross(p1 - p0, p2 - p0)
        wp.atomic_add(normals, i0, fn)
        wp.atomic_add(normals, i1, fn)
        wp.atomic_add(normals, i2, fn)

    @wp.kernel
    def _normalize_normals(normals: wp.array(dtype=wp.vec3)):
        i = wp.tid()
        n = normals[i]
        l = wp.length(n)
        if l > 1.0e-8:
            normals[i] = n / l
        else:
            normals[i] = wp.vec3(0.0, 1.0, 0.0)

    @wp.kernel
    def _brush_grab(
        pos: wp.array(dtype=wp.vec3),
        stroke_start_pos: wp.array(dtype=wp.vec3),
        mask: wp.array(dtype=float),
        brush_weights: wp.array(dtype=float),
        total_delta: wp.vec3,
    ):
        i = wp.tid()
        w = brush_weights[i]
        if w <= 0.0:
            return
        m = wp.clamp(1.0 - mask[i], 0.0, 1.0)
        if m <= 0.0:
            return
        pos[i] = stroke_start_pos[i] + total_delta * (w * m)

    @wp.kernel
    def _brush_inflate(
        pos: wp.array(dtype=wp.vec3),
        normals: wp.array(dtype=wp.vec3),
        mask: wp.array(dtype=float),
        hit_pos: wp.vec3,
        cam_fwd: wp.vec3,
        radius: float,
        amount: float,
        backface_mask: int,
    ):
        i = wp.tid()
        p = pos[i]
        d = wp.length(p - hit_pos)
        w = smooth_falloff(d, radius)
        if w <= 0.0:
            return
        m = wp.clamp(1.0 - mask[i], 0.0, 1.0)
        if m <= 0.0:
            return
        n = normals[i]
        if backface_mask != 0 and wp.dot(n, cam_fwd) > 0.25:
            return
        pos[i] = p + n * (amount * w * m)

    @wp.kernel
    def _brush_pinch(
        pos: wp.array(dtype=wp.vec3),
        normals: wp.array(dtype=wp.vec3),
        mask: wp.array(dtype=float),
        hit_pos: wp.vec3,
        cam_fwd: wp.vec3,
        radius: float,
        strength: float,
        backface_mask: int,
    ):
        i = wp.tid()
        p = pos[i]
        diff = hit_pos - p
        d = wp.length(diff)
        w = smooth_falloff(d, radius)
        if w <= 0.0:
            return
        m = wp.clamp(1.0 - mask[i], 0.0, 1.0)
        if m <= 0.0:
            return
        if backface_mask != 0 and wp.dot(normals[i], cam_fwd) > 0.25:
            return
        step = wp.clamp(strength * w * m * 0.22, -0.45, 0.45)
        pos[i] = p + diff * step

    @wp.kernel
    def _brush_flatten(
        pos: wp.array(dtype=wp.vec3),
        normals: wp.array(dtype=wp.vec3),
        mask: wp.array(dtype=float),
        hit_pos: wp.vec3,
        plane_origin: wp.vec3,
        plane_normal: wp.vec3,
        cam_fwd: wp.vec3,
        radius: float,
        strength: float,
        backface_mask: int,
    ):
        i = wp.tid()
        p = pos[i]
        d = wp.length(p - hit_pos)
        w = smooth_falloff(d, radius)
        if w <= 0.0:
            return
        m = wp.clamp(1.0 - mask[i], 0.0, 1.0)
        if m <= 0.0:
            return
        if backface_mask != 0 and wp.dot(normals[i], cam_fwd) > 0.25:
            return
        signed_dist = wp.dot(p - plane_origin, plane_normal)
        blend = wp.clamp(strength * w * m * 0.45, 0.0, 0.95)
        pos[i] = p - plane_normal * (signed_dist * blend)

    @wp.kernel
    def _brush_smooth_step(
        pos_in: wp.array(dtype=wp.vec3),
        pos_out: wp.array(dtype=wp.vec3),
        normals: wp.array(dtype=wp.vec3),
        mask: wp.array(dtype=float),
        adj_offsets: wp.array(dtype=int),
        adj_neighbors: wp.array(dtype=int),
        hit_pos: wp.vec3,
        cam_fwd: wp.vec3,
        radius: float,
        factor: float,
        backface_mask: int,
    ):
        i = wp.tid()
        p = pos_in[i]
        d = wp.length(p - hit_pos)
        w = smooth_falloff(d, radius)
        if w <= 0.0:
            pos_out[i] = p
            return
        m = wp.clamp(1.0 - mask[i], 0.0, 1.0)
        if m <= 0.0:
            pos_out[i] = p
            return
        if backface_mask != 0 and wp.dot(normals[i], cam_fwd) > 0.25:
            pos_out[i] = p
            return
        start = adj_offsets[i]
        end = adj_offsets[i + 1]
        count = end - start
        if count <= 0:
            pos_out[i] = p
            return
        acc = wp.vec3(0.0, 0.0, 0.0)
        for k in range(start, end):
            acc = acc + pos_in[adj_neighbors[k]]
        avg = acc / float(count)
        pos_out[i] = p + (avg - p) * (factor * w * m)

    @wp.kernel
    def _brush_mask(
        pos: wp.array(dtype=wp.vec3),
        normals: wp.array(dtype=wp.vec3),
        mask: wp.array(dtype=float),
        hit_pos: wp.vec3,
        cam_fwd: wp.vec3,
        radius: float,
        target_val: float,
        strength: float,
        backface_mask: int,
    ):
        i = wp.tid()
        p = pos[i]
        d = wp.length(p - hit_pos)
        w = smooth_falloff(d, radius)
        if w <= 0.0:
            return
        if backface_mask != 0 and wp.dot(normals[i], cam_fwd) > 0.25:
            return
        cur = mask[i]
        blend = wp.clamp(strength * w * 0.5, 0.0, 1.0)
        mask[i] = wp.clamp(cur + (target_val - cur) * blend, 0.0, 1.0)

    # ------------------------------------------------------------------
    # Cloth Physics Brush XPBD Kernels
    # ------------------------------------------------------------------

    @wp.kernel
    def _cloth_prepare_patch(
        pos: wp.array(dtype=wp.vec3),
        mask: wp.array(dtype=float),
        inv_mass: wp.array(dtype=float),
        brush_weight: wp.array(dtype=float),
        hit_pos: wp.vec3,
        brush_radius: float,
        patch_radius: float,
    ):
        i = wp.tid()
        p = pos[i]
        d = wp.length(p - hit_pos)
        unmasked = wp.clamp(1.0 - mask[i], 0.0, 1.0)
        if unmasked <= 1.0e-4 or d >= patch_radius:
            inv_mass[i] = 0.0
            brush_weight[i] = 0.0
            return
        # Smooth mobility fade near outer patch boundary so folds anchor naturally
        patch_w = smooth_falloff(d, patch_radius)
        # Keep interior of patch fully mobile; fade smoothly in outer 35%
        if d <= patch_radius * 0.65:
            mob = unmasked
        else:
            mob = unmasked * wp.clamp(patch_w * 4.0, 0.0, 1.0)
        inv_mass[i] = mob
        brush_weight[i] = smooth_falloff(d, brush_radius) * unmasked

    @wp.kernel
    def _cloth_predict(
        pos: wp.array(dtype=wp.vec3),
        prev_pos: wp.array(dtype=wp.vec3),
        stroke_start_pos: wp.array(dtype=wp.vec3),
        vel: wp.array(dtype=wp.vec3),
        normals: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        brush_weight: wp.array(dtype=float),
        total_delta: wp.vec3,
        gravity_vec: wp.vec3,
        inflate_amount: float,
        drag_spring: float,
        dt: float,
        damping: float,
        mode: int,  # 0 = clothDrag, 1 = clothRelax, 2 = clothInflate
    ):
        i = wp.tid()
        im = inv_mass[i]
        p = pos[i]
        prev_pos[i] = p
        if im <= 0.0:
            vel[i] = wp.vec3(0.0, 0.0, 0.0)
            return
        bw = brush_weight[i]
        if mode == 0:
            # Quasistatic Cloth Drag: compliant spring toward cursor target
            # with strong velocity damping so cloth never overshoots or stretches like rubber
            v = vel[i] * 0.2
            target = stroke_start_pos[i] + total_delta
            pull = (target - p) * wp.clamp(bw * drag_spring, 0.0, 0.65)
            pos[i] = p + v * dt + pull
        elif mode == 1:
            # Cloth Relax / Drape: gravity + elastic settling inside brush
            v = vel[i] * wp.clamp(1.0 - damping, 0.0, 1.0)
            v = v + gravity_vec * (dt * bw * im)
            pos[i] = p + v * dt
        else:
            # Cloth Inflate / Un-poke: outward normal push + cloth constraints
            v = vel[i] * 0.25
            n = normals[i]
            pos[i] = p + v * dt + n * (inflate_amount * bw)

    @wp.kernel
    def _cloth_stretch_jacobi(
        pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        edge_v0: wp.array(dtype=int),
        edge_v1: wp.array(dtype=int),
        edge_rest: wp.array(dtype=float),
        corr_sum: wp.array(dtype=wp.vec3),
        corr_count: wp.array(dtype=int),
        stiffness: float,
    ):
        e = wp.tid()
        i0 = edge_v0[e]
        i1 = edge_v1[e]
        w0 = inv_mass[i0]
        w1 = inv_mass[i1]
        w_sum = w0 + w1
        if w_sum <= 1.0e-8:
            return
        p0 = pos[i0]
        p1 = pos[i1]
        diff = p1 - p0
        dist = wp.length(diff)
        if dist <= 1.0e-8:
            return
        rest = edge_rest[e]
        err = dist - rest
        n = diff / dist
        scale = stiffness * err / w_sum
        c0 = n * (scale * w0)
        c1 = n * (-scale * w1)
        if w0 > 0.0:
            wp.atomic_add(corr_sum, i0, c0)
            wp.atomic_add(corr_count, i0, 1)
        if w1 > 0.0:
            wp.atomic_add(corr_sum, i1, c1)
            wp.atomic_add(corr_count, i1, 1)

    @wp.kernel
    def _cloth_bend_jacobi(
        pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        bend_v0: wp.array(dtype=int),
        bend_v1: wp.array(dtype=int),
        bend_rest: wp.array(dtype=float),
        corr_sum: wp.array(dtype=wp.vec3),
        corr_count: wp.array(dtype=int),
        bend_stiffness: float,
    ):
        b = wp.tid()
        i0 = bend_v0[b]
        i1 = bend_v1[b]
        w0 = inv_mass[i0]
        w1 = inv_mass[i1]
        w_sum = w0 + w1
        if w_sum <= 1.0e-8:
            return
        p0 = pos[i0]
        p1 = pos[i1]
        diff = p1 - p0
        dist = wp.length(diff)
        if dist <= 1.0e-8:
            return
        rest = bend_rest[b]
        err = dist - rest
        n = diff / dist
        scale = bend_stiffness * err / w_sum
        c0 = n * (scale * w0)
        c1 = n * (-scale * w1)
        if w0 > 0.0:
            wp.atomic_add(corr_sum, i0, c0)
            wp.atomic_add(corr_count, i0, 1)
        if w1 > 0.0:
            wp.atomic_add(corr_sum, i1, c1)
            wp.atomic_add(corr_count, i1, 1)

    @wp.kernel
    def _cloth_apply_jacobi(
        pos: wp.array(dtype=wp.vec3),
        corr_sum: wp.array(dtype=wp.vec3),
        corr_count: wp.array(dtype=int),
        omega: float,
        max_step: float,
    ):
        i = wp.tid()
        cnt = corr_count[i]
        if cnt <= 0:
            return
        delta = (corr_sum[i] / float(cnt)) * omega
        d_len = wp.length(delta)
        if d_len > max_step and d_len > 1.0e-8:
            delta = delta * (max_step / d_len)
        pos[i] = pos[i] + delta
        corr_sum[i] = wp.vec3(0.0, 0.0, 0.0)
        corr_count[i] = 0

    @wp.kernel
    def _cloth_collider(
        pos: wp.array(dtype=wp.vec3),
        prev_pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        collider_mesh: wp.uint64,
        margin: float,
        friction: float,
        max_query_dist: float,
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0:
            return
        p = pos[i]
        q = wp.mesh_query_point_sign_normal(collider_mesh, p, max_query_dist)
        if not q.result:
            return
        cp = wp.mesh_eval_position(collider_mesh, q.face, q.u, q.v)
        fn = wp.mesh_eval_face_normal(collider_mesh, q.face)
        diff = p - cp
        dist = wp.length(diff)
        signed_d = q.sign * dist
        if signed_d >= margin:
            return
        # Ignore deep spurious inside-sign hits from open/non-watertight body meshes
        if q.sign < 0.0 and dist > wp.max(margin * 8.0, max_query_dist * 0.25):
            return
        # Push outside collider surface along face normal
        push_n = fn
        if dist > 1.0e-7 and q.sign > 0.0:
            push_n = diff / dist
        raw_push = margin - signed_d
        max_push = wp.max(margin * 4.0, max_query_dist * 0.08)
        if raw_push > max_push:
            target = p + push_n * max_push
        else:
            target = cp + push_n * margin
        # Apply Coulomb tangential friction relative to previous substep
        move = target - prev_pos[i]
        n_comp = wp.dot(move, push_n)
        tang = move - push_n * n_comp
        tang_damp = wp.clamp(1.0 - friction, 0.0, 1.0)
        pos[i] = prev_pos[i] + push_n * n_comp + tang * tang_damp

    @wp.kernel
    def _cloth_update_vel(
        pos: wp.array(dtype=wp.vec3),
        prev_pos: wp.array(dtype=wp.vec3),
        vel: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        inv_dt: float,
        damping: float,
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0:
            vel[i] = wp.vec3(0.0, 0.0, 0.0)
            return
        v = (pos[i] - prev_pos[i]) * inv_dt
        vel[i] = v * wp.clamp(1.0 - damping, 0.0, 1.0)

    @wp.kernel
    def _cloth_reconstruct_fine(
        fine_start_pos: wp.array(dtype=wp.vec3),
        fine_out_pos: wp.array(dtype=wp.vec3),
        fine_mask: wp.array(dtype=float),
        proxy_start_pos: wp.array(dtype=wp.vec3),
        proxy_cur_pos: wp.array(dtype=wp.vec3),
        bind_idx: wp.array(dtype=int),
        bind_w: wp.array(dtype=float),
    ):
        i = wp.tid()
        m = wp.clamp(1.0 - fine_mask[i], 0.0, 1.0)
        if m <= 0.0:
            fine_out_pos[i] = fine_start_pos[i]
            return
        base = i * 4
        disp = wp.vec3(0.0, 0.0, 0.0)
        for k in range(4):
            w = bind_w[base + k]
            if w > 0.0:
                ci = bind_idx[base + k]
                disp = disp + (proxy_cur_pos[ci] - proxy_start_pos[ci]) * w
        fine_out_pos[i] = fine_start_pos[i] + disp * m

    @wp.kernel
    def _world_to_local(
        world_pos: wp.array(dtype=wp.vec3),
        local_pos: wp.array(dtype=wp.vec3),
        m0: wp.vec3,
        m1: wp.vec3,
        m2: wp.vec3,
        t: wp.vec3,
    ):
        i = wp.tid()
        p = world_pos[i]
        # Row-major 4x4 transform: local = p * M[:3, :3] + M[3, :3]
        lx = p[0] * m0[0] + p[1] * m1[0] + p[2] * m2[0] + t[0]
        ly = p[0] * m0[1] + p[1] * m1[1] + p[2] * m2[1] + t[1]
        lz = p[0] * m0[2] + p[1] * m1[2] + p[2] * m2[2] + t[2]
        local_pos[i] = wp.vec3(lx, ly, lz)

    # ------------------------------------------------------------------
    # Full-Garment Temporal Cloth Simulation Kernels
    # ------------------------------------------------------------------

    @wp.kernel
    def _cloth_sim_predict(
        pos: wp.array(dtype=wp.vec3),
        prev_pos: wp.array(dtype=wp.vec3),
        vel: wp.array(dtype=wp.vec3),
        normals: wp.array(dtype=wp.vec3),
        pin_weight: wp.array(dtype=float),
        pin_target: wp.array(dtype=wp.vec3),
        gravity_vec: wp.vec3,
        wind_vec: wp.vec3,
        air_drag: float,
        dt: float,
        damping: float,
        max_speed: float,
    ):
        i = wp.tid()
        p = pos[i]
        prev_pos[i] = p
        pw = wp.clamp(pin_weight[i], 0.0, 1.0)
        if pw >= 0.999:
            pos[i] = pin_target[i]
            vel[i] = wp.vec3(0.0, 0.0, 0.0)
            return
        v = vel[i] * wp.clamp(1.0 - damping, 0.0, 1.0)
        n = normals[i]
        v_rel = wind_vec - v
        v_n = wp.dot(v_rel, n)
        aero = n * (v_n * air_drag) + v_rel * (air_drag * 0.25)
        v = v + (gravity_vec + aero) * dt
        spd = wp.length(v)
        if spd > max_speed and spd > 1.0e-8:
            v = v * (max_speed / spd)
        p_next = p + v * dt
        if pw > 1.0e-4:
            p_next = p_next + (pin_target[i] - p_next) * (pw * 0.5)
        pos[i] = p_next

    @wp.kernel
    def _cloth_sim_update_vel(
        pos: wp.array(dtype=wp.vec3),
        prev_pos: wp.array(dtype=wp.vec3),
        vel: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        inv_dt: float,
        damping: float,
        max_speed: float,
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0:
            vel[i] = wp.vec3(0.0, 0.0, 0.0)
            return
        v = (pos[i] - prev_pos[i]) * inv_dt
        v = v * wp.clamp(1.0 - damping, 0.0, 1.0)
        spd = wp.length(v)
        if spd > max_speed and spd > 1.0e-8:
            v = v * (max_speed / spd)
        vel[i] = v

    @wp.kernel
    def _cloth_lra_tether(
        pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        tether_anchor: wp.array(dtype=int),
        tether_dist: wp.array(dtype=float),
        stretch_allowance: float,
    ):
        i = wp.tid()
        if inv_mass[i] <= 0.0:
            return
        anchor = tether_anchor[i]
        if anchor < 0:
            return
        max_len = tether_dist[i] * (1.0 + stretch_allowance)
        if max_len <= 1.0e-7:
            return
        p = pos[i]
        pa = pos[anchor]
        diff = p - pa
        dist = wp.length(diff)
        if dist > max_len and dist > 1.0e-7:
            pos[i] = pa + diff * (max_len / dist)

    @wp.kernel
    def _cloth_self_collision(
        grid: wp.uint64,
        pos: wp.array(dtype=wp.vec3),
        rest_pos: wp.array(dtype=wp.vec3),
        inv_mass: wp.array(dtype=float),
        adj_offsets: wp.array(dtype=int),
        adj_neighbors: wp.array(dtype=int),
        corr_sum: wp.array(dtype=wp.vec3),
        corr_count: wp.array(dtype=int),
        thickness: float,
    ):
        i = wp.tid()
        w_i = inv_mass[i]
        if w_i <= 0.0 or thickness <= 1.0e-7:
            return
        p_i = pos[i]
        rp_i = rest_pos[i]
        rest_skip = thickness * 1.5
        start = adj_offsets[i]
        end = adj_offsets[i + 1]
        query = wp.hash_grid_query(grid, p_i, thickness)
        j = int(0)
        acc = wp.vec3(0.0, 0.0, 0.0)
        hits = int(0)
        while wp.hash_grid_query_next(query, j):
            if j == i:
                continue
            w_j = inv_mass[j]
            w_sum = w_i + w_j
            if w_sum <= 1.0e-8:
                continue
            # Skip vertices that started close in the rest garment (1-ring/2-ring, seams, thickness shells)
            if wp.length(rp_i - rest_pos[j]) < rest_skip:
                continue
            is_adj = int(0)
            for k in range(start, end):
                if adj_neighbors[k] == j:
                    is_adj = 1
                    break
            if is_adj != 0:
                continue
            diff = p_i - pos[j]
            dist = wp.length(diff)
            if dist >= thickness or dist <= 1.0e-7:
                continue
            pen = wp.min(thickness - dist, thickness * 0.5)
            push = (diff / dist) * (pen * (w_i / w_sum))
            acc = acc + push
            hits = hits + 1
        if hits > 0:
            wp.atomic_add(corr_sum, i, acc / float(hits))
            wp.atomic_add(corr_count, i, 1)

    @wp.kernel
    def _cloth_bind_collider_pins(
        pos: wp.array(dtype=wp.vec3),
        pin_weight: wp.array(dtype=float),
        collider_mesh: wp.uint64,
        margin: float,
        max_query_dist: float,
        pin_face: wp.array(dtype=int),
        pin_uv: wp.array(dtype=wp.vec2),
        pin_offset: wp.array(dtype=float),
    ):
        i = wp.tid()
        if pin_weight[i] <= 1.0e-4:
            pin_face[i] = -1
            pin_uv[i] = wp.vec2(0.0, 0.0)
            pin_offset[i] = 0.0
            return
        p = pos[i]
        q = wp.mesh_query_point_sign_normal(collider_mesh, p, max_query_dist)
        if not q.result:
            pin_face[i] = -1
            pin_uv[i] = wp.vec2(0.0, 0.0)
            pin_offset[i] = 0.0
            return
        cp = wp.mesh_eval_position(collider_mesh, q.face, q.u, q.v)
        dist = wp.length(p - cp)
        signed_d = q.sign * dist
        pin_face[i] = q.face
        pin_uv[i] = wp.vec2(q.u, q.v)
        pin_offset[i] = wp.max(margin, signed_d)

    @wp.kernel
    def _cloth_eval_collider_pins(
        pin_weight: wp.array(dtype=float),
        pin_face: wp.array(dtype=int),
        pin_uv: wp.array(dtype=wp.vec2),
        pin_offset: wp.array(dtype=float),
        collider_mesh: wp.uint64,
        pin_target: wp.array(dtype=wp.vec3),
    ):
        i = wp.tid()
        if pin_weight[i] <= 1.0e-4:
            return
        face = pin_face[i]
        if face < 0:
            return
        uv = pin_uv[i]
        cp = wp.mesh_eval_position(collider_mesh, face, uv[0], uv[1])
        fn = wp.mesh_eval_face_normal(collider_mesh, face)
        pin_target[i] = cp + fn * pin_offset[i]

    @wp.kernel
    def _cloth_lerp_collider_pts(
        pts_a: wp.array(dtype=wp.vec3),
        pts_b: wp.array(dtype=wp.vec3),
        pts_out: wp.array(dtype=wp.vec3),
        alpha: float,
    ):
        i = wp.tid()
        a = pts_a[i]
        b = pts_b[i]
        pts_out[i] = a + (b - a) * alpha

    accum_face_normals_kernel = _accum_face_normals
    normalize_normals_kernel = _normalize_normals
    brush_grab_kernel = _brush_grab
    brush_inflate_kernel = _brush_inflate
    brush_pinch_kernel = _brush_pinch
    brush_flatten_kernel = _brush_flatten
    brush_smooth_step_kernel = _brush_smooth_step
    brush_mask_kernel = _brush_mask
    cloth_prepare_patch_kernel = _cloth_prepare_patch
    cloth_predict_kernel = _cloth_predict
    cloth_stretch_jacobi_kernel = _cloth_stretch_jacobi
    cloth_bend_jacobi_kernel = _cloth_bend_jacobi
    cloth_apply_jacobi_kernel = _cloth_apply_jacobi
    cloth_collider_kernel = _cloth_collider
    cloth_update_vel_kernel = _cloth_update_vel
    cloth_reconstruct_fine_kernel = _cloth_reconstruct_fine
    world_to_local_kernel = _world_to_local
    cloth_sim_predict_kernel = _cloth_sim_predict
    cloth_sim_update_vel_kernel = _cloth_sim_update_vel
    cloth_lra_tether_kernel = _cloth_lra_tether
    cloth_self_collision_kernel = _cloth_self_collision
    cloth_bind_collider_pins_kernel = _cloth_bind_collider_pins
    cloth_eval_collider_pins_kernel = _cloth_eval_collider_pins
    cloth_lerp_collider_pts_kernel = _cloth_lerp_collider_pts
