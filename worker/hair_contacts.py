"""Discrete segment contacts for the compact guide solve."""


def build_contact_kernels(wp):
    @wp.kernel
    def segment_bounds(
        active_edges: wp.array(dtype=int),
        active_only: int,
        points: wp.array(dtype=wp.vec3),
        starts: wp.array(dtype=int),
        ends: wp.array(dtype=int),
        lowers: wp.array(dtype=wp.vec3),
        uppers: wp.array(dtype=wp.vec3),
    ):
        edge = wp.tid()
        if active_only != 0:
            edge = active_edges[edge]
        p = points[starts[edge]]
        q = points[ends[edge]]
        lowers[edge] = wp.min(p, q)
        uppers[edge] = wp.max(p, q)

    @wp.func
    def accumulate_witness(
        first: int,
        second: int,
        t: float,
        first_mass: float,
        second_mass: float,
        impulse: wp.vec3,
        rhs: wp.array(dtype=wp.vec3),
        diagonal: wp.array(dtype=float),
        adjacent: wp.array(dtype=float),
    ):
        a = 1.0 - t
        b = t
        # Project a target at the point of contact. Its two endpoints share
        # one constraint, so the rod's normal equations include a cross term.
        displacement = (a * a * first_mass + b * b * second_mass) * impulse
        free_a = a if first_mass > 0.0 else 0.0
        free_b = b if second_mass > 0.0 else 0.0
        if free_a > 0.0:
            wp.atomic_add(rhs, first, free_a * displacement)
            wp.atomic_add(diagonal, first, free_a * free_a)
        if free_b > 0.0:
            wp.atomic_add(rhs, second, free_b * displacement)
            wp.atomic_add(diagonal, second, free_b * free_b)
        if free_a > 0.0 and free_b > 0.0:
            wp.atomic_add(adjacent, second, free_a * free_b)

    @wp.func
    def solve_tangent_impulse(
        a: float, b: float, c: float, determinant: float,
        slip: float, limit: float,
    ):
        if limit <= 1e-8:
            return wp.vec2(0.0)
        sticking = wp.vec2(-c, b) * (slip / determinant)
        if wp.length_sq(sticking) <= limit * limit:
            return sticking
        if wp.abs(b) <= 1e-6 * wp.max(a, c):
            return wp.vec2(-limit, 0.0)

        # Limit a sliding impulse in the contact's effective-mass metric.
        # Adding a scalar to the diagonal yields the constrained minimizer
        # when the impulse reaches the boundary of the Coulomb disk.
        lower = float(0.0)
        upper = slip / limit
        sliding = sticking
        for iteration in range(8):
            shift = 0.5 * (lower + upper)
            shifted_det = determinant + shift * (a + c + shift)
            sliding = wp.vec2(-(c + shift), b) * (slip / shifted_det)
            if wp.length_sq(sliding) > limit * limit:
                lower = shift
            else:
                upper = shift
        return sliding * (limit / wp.max(wp.length(sliding), 1e-8))

    @wp.kernel
    def project_contacts(
        active_edges: wp.array(dtype=int),
        points: wp.array(dtype=wp.vec3),
        previous: wp.array(dtype=wp.vec3),
        stroke_rest: wp.array(dtype=wp.vec3),
        inverse_mass: wp.array(dtype=float),
        starts: wp.array(dtype=int),
        ends: wp.array(dtype=int),
        strand_ids: wp.array(dtype=int),
        lowers: wp.array(dtype=wp.vec3),
        uppers: wp.array(dtype=wp.vec3),
        bvh: wp.uint64,
        diameter: float,
        friction: float,
        contact_rhs: wp.array(dtype=wp.vec3),
        contact_diagonal: wp.array(dtype=float),
        contact_adjacent: wp.array(dtype=float),
        twist_factors: wp.array(dtype=wp.vec4),
        twist_impulses: wp.array(dtype=float),
        twist_contacts: wp.array(dtype=float),
    ):
        edge = active_edges[wp.tid()]
        i0 = starts[edge]
        i1 = ends[edge]
        if inverse_mass[i0] + inverse_mass[i1] <= 0.0:
            return
        radius = wp.vec3(diameter)
        query = wp.bvh_query_aabb(bvh, lowers[edge] - radius, uppers[edge] + radius)
        other = int(0)
        while wp.bvh_query_next(query, other):
            if strand_ids[other] == strand_ids[edge]:
                continue
            j0 = starts[other]
            j1 = ends[other]
            if other < edge and inverse_mass[j0] + inverse_mass[j1] > 0.0:
                continue
            closest = wp.closest_point_edge_edge(points[i0], points[i1], points[j0], points[j1], 1e-14)
            if closest[2] >= diameter:
                continue
            previous_closest = wp.closest_point_edge_edge(
                previous[i0], previous[i1], previous[j0], previous[j1], 1e-14,
            )
            # Don't ratchet the target smaller when the last solve left
            # residual compression. Allow overlaps already present at stroke start.
            separation = wp.min(diameter, previous_closest[2])
            if separation < diameter:
                rest_closest = wp.closest_point_edge_edge(
                    stroke_rest[i0], stroke_rest[i1], stroke_rest[j0], stroke_rest[j1], 1e-14,
                )
                separation = wp.min(diameter, wp.max(separation, rest_closest[2]))
            s = closest[0]
            t = closest[1]
            relative = wp.lerp(points[i0], points[i1], s) - wp.lerp(points[j0], points[j1], t)
            old_relative = wp.lerp(previous[i0], previous[i1], s) - wp.lerp(previous[j0], previous[j1], t)
            previous_normal = (
                wp.lerp(previous[i0], previous[i1], previous_closest[0])
                - wp.lerp(previous[j0], previous[j1], previous_closest[1])
            )
            normal = relative
            if wp.length_sq(normal) <= 1e-16:
                normal = previous_normal
                if wp.length_sq(normal) <= 1e-16:
                    tangent = points[i1] - points[i0]
                    normal = wp.cross(tangent, points[j1] - points[j0])
                    if wp.length_sq(normal) <= 1e-16:
                        normal = wp.cross(tangent, wp.vec3(1.0, 0.0, 0.0))
                        if wp.length_sq(normal) <= 1e-16:
                            normal = wp.vec3(0.0, 1.0, 0.0)
            normal = wp.normalize(normal)
            signed_distance = closest[2]
            # Interior edge witnesses on both samples identify a near crossing.
            # Keep the previous side only when the separation direction has
            # actually reversed. Endpoint contacts can slide around each other.
            if (
                s > 1e-3 and s < 0.999 and t > 1e-3 and t < 0.999
                and previous_closest[0] > 1e-3 and previous_closest[0] < 0.999
                and previous_closest[1] > 1e-3 and previous_closest[1] < 0.999
                and wp.dot(normal, previous_normal) < -0.5 * previous_closest[2]
            ):
                normal = -normal
                signed_distance = -signed_distance
            penetration = separation - signed_distance
            if penetration <= 0.0:
                continue
            slip = relative - old_relative
            slip -= wp.dot(slip, normal) * normal
            slip_length = wp.length(slip)
            i0_share = (1.0 - s) * inverse_mass[i0]
            i1_share = s * inverse_mass[i1]
            j0_share = (1.0 - t) * inverse_mass[j0]
            j1_share = t * inverse_mass[j1]
            diagonal = (1.0 - s) * i0_share + s * i1_share + (1.0 - t) * j0_share + t * j1_share
            if diagonal > 1e-8:
                normal_impulse = penetration / diagonal
                tangent_impulse = wp.vec3(0.0)
                i_axis = wp.vec3(0.0)
                j_axis = wp.vec3(0.0)
                i_mobility = float(0.0)
                j_mobility = float(0.0)
                if friction > 0.0 and slip_length > 1e-8:
                    # The contact moves through both centerline translation and
                    # axial roll. Solve in the two-dimensional tangent plane.
                    i_edge = points[i1] - points[i0]
                    j_edge = points[j1] - points[j0]
                    if inverse_mass[i0] > 0.0 and wp.length_sq(i_edge) > 1e-20:
                        i_axis = wp.cross(wp.normalize(i_edge), -normal)
                        # The radius cancels in r²/I for an axial cylinder.
                        i_mobility = 2.0 / twist_factors[i0][3]
                    if inverse_mass[j0] > 0.0 and wp.length_sq(j_edge) > 1e-20:
                        j_axis = wp.cross(wp.normalize(j_edge), -normal)
                        j_mobility = 2.0 / twist_factors[j0][3]
                    u = slip / slip_length
                    v = wp.cross(normal, u)
                    iu = wp.dot(i_axis, u)
                    iv = wp.dot(i_axis, v)
                    ju = wp.dot(j_axis, u)
                    jv = wp.dot(j_axis, v)
                    a = diagonal + i_mobility * iu * iu + j_mobility * ju * ju
                    b = i_mobility * iu * iv + j_mobility * ju * jv
                    c = diagonal + i_mobility * iv * iv + j_mobility * jv * jv
                    # Use the positive expanded determinant, avoiding cancellation
                    # when the two rotational contributions are nearly parallel.
                    cross_axes = iu * jv - iv * ju
                    determinant = diagonal * (a + c - diagonal) + i_mobility * j_mobility * cross_axes * cross_axes
                    tangent_2d = solve_tangent_impulse(a, b, c, determinant, slip_length, friction * normal_impulse)
                    tangent_impulse = tangent_2d[0] * u + tangent_2d[1] * v
                correction = normal_impulse * normal + tangent_impulse
                largest_share = wp.max(1.0, wp.max(wp.max(i0_share, i1_share), wp.max(j0_share, j1_share)))
                correction_length = wp.length(correction) * largest_share
                contact_scale = wp.min(1.0, diameter / wp.max(correction_length, 1e-8))
                correction *= contact_scale
                accumulate_witness(
                    i0, i1, s, inverse_mass[i0], inverse_mass[i1], correction,
                    contact_rhs, contact_diagonal, contact_adjacent,
                )
                accumulate_witness(
                    j0, j1, t, inverse_mass[j0], inverse_mass[j1], -correction,
                    contact_rhs, contact_diagonal, contact_adjacent,
                )
                if diameter > 1e-8 and wp.length_sq(tangent_impulse) > 0.0:
                    rotation_scale = 2.0 * contact_scale / diameter
                    if i_mobility > 0.0:
                        wp.atomic_add(twist_impulses, i0, wp.dot(i_axis, tangent_impulse) * i_mobility * rotation_scale)
                        wp.atomic_add(twist_contacts, i0, 1.0)
                    if j_mobility > 0.0:
                        wp.atomic_add(twist_impulses, j0, wp.dot(j_axis, tangent_impulse) * j_mobility * rotation_scale)
                        wp.atomic_add(twist_contacts, j0, 1.0)

    @wp.kernel
    def project_body_contacts(
        active_edges: wp.array(dtype=int),
        edge_ends: wp.array(dtype=int),
        points: wp.array(dtype=wp.vec3),
        previous_points: wp.array(dtype=wp.vec3),
        inverse_mass: wp.array(dtype=float),
        mesh_id: wp.uint64,
        margin: float,
        friction: float,
        max_query_dist: float,
        contact_rhs: wp.array(dtype=wp.vec3),
        contact_diagonal: wp.array(dtype=float),
    ):
        edge = active_edges[wp.tid()]
        i = edge_ends[edge]
        if inverse_mass[i] <= 0.0:
            return
        p = points[i]
        query = wp.mesh_query_point_sign_normal(mesh_id, p, max_query_dist)
        if not query.result:
            return
        closest = wp.mesh_eval_position(mesh_id, query.face, query.u, query.v)
        diff = p - closest
        dist = wp.length(diff)
        signed_dist = dist * query.sign
        if signed_dist >= margin:
            return
        face_n = wp.mesh_eval_face_normal(mesh_id, query.face)
        normal = face_n
        if dist > 1e-6:
            normal = diff / dist
            if query.sign < 0.0:
                normal = -normal
                if wp.dot(normal, face_n) < 0.0:
                    normal = face_n
        penetration = margin - signed_dist
        push = (closest + normal * margin) - p
        if friction > 0.0:
            slip = p - previous_points[i]
            t_slip = slip - normal * wp.dot(slip, normal)
            t_len = wp.length(t_slip)
            if t_len > 1e-8:
                push -= t_slip * wp.min(1.0, friction * penetration / t_len)
        wp.atomic_add(contact_rhs, i, push)
        wp.atomic_add(contact_diagonal, i, 1.0)

    return segment_bounds, project_contacts, project_body_contacts
