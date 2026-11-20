"""Local–global elastic solve for compact, root-pinned hair guides."""

from functools import lru_cache


@lru_cache(maxsize=1)
def build_director_transport(wp):
    @wp.func
    def transport_director(director: wp.vec3, previous: wp.vec3, tangent: wp.vec3):
        cosine = wp.clamp(wp.dot(previous, tangent), -1.0, 1.0)
        rotated = director
        if cosine > -0.99999:
            axis = wp.cross(previous, tangent)
            first = wp.cross(axis, director)
            rotated += first + wp.cross(axis, first) / (1.0 + cosine)
        # At a half turn the material director supplies a stable rotation axis.
        return wp.normalize(rotated - tangent * wp.dot(rotated, tangent))

    return transport_director


def build_rod_kernel(wp):
    transport_director = build_director_transport(wp)

    @wp.func
    def retain_dual(dual: wp.vec3, limit: float):
        length = wp.length(dual)
        if not wp.isfinite(length) or length <= 1e-12:
            return wp.vec3(0.0)
        return dual * wp.min(0.65, limit / length)

    @wp.kernel
    def solve_guides(
        active_guides: wp.array(dtype=int),
        points: wp.array(dtype=wp.vec3),
        rest: wp.array(dtype=wp.vec3),
        inverse_mass: wp.array(dtype=float),
        starts: wp.array(dtype=int),
        counts: wp.array(dtype=int),
        arc_lengths: wp.array(dtype=float),
        brush_points: wp.array(dtype=int),
        brush_targets: wp.array(dtype=wp.vec3),
        brush_weights: wp.array(dtype=float),
        target: wp.array(dtype=wp.vec3),
        attachment_weights: wp.array(dtype=float),
        contact_rhs: wp.array(dtype=wp.vec3),
        contact_diagonal: wp.array(dtype=float),
        contact_adjacent: wp.array(dtype=float),
        twist_angles: wp.array(dtype=float),
        rest_tangents: wp.array(dtype=wp.vec3),
        rest_directors: wp.array(dtype=wp.vec3),
        rest_bends: wp.array(dtype=wp.vec3),
        bend_left: wp.array(dtype=float),
        rest_lengths: wp.array(dtype=float),
        stretch_weights: wp.array(dtype=float),
        edge_z: wp.array(dtype=wp.vec3),
        edge_dual: wp.array(dtype=wp.vec3),
        bend_z: wp.array(dtype=wp.vec3),
        bend_dual: wp.array(dtype=wp.vec3),
        bend_torque: wp.array(dtype=float),
        factors: wp.array2d(dtype=wp.vec3),
        factor_state: wp.array(dtype=int),
        forward: wp.array(dtype=wp.vec3),
        converged: wp.array(dtype=int),
        stretch: float,
        bend: float,
        iterations: int,
        brush_active: int,
        contact_pass: int,
    ):
        strand = active_guides[wp.tid()]
        first = starts[strand]
        count = counts[strand]
        if count < 2 or inverse_mass[first + 1] <= 0.0:
            return
        end = first + count
        root = points[first]
        average_length = arc_lengths[end - 1] / float(count - 1)
        tolerance = wp.max(0.002 * average_length, 1e-8)
        tolerance_sq = tolerance * tolerance
        n_segs = float(wp.max(count - 1, 1))
        res_scale = wp.clamp(n_segs / 12.0, 1.0, 8.0)
        stretch_penalty = 64.0 * stretch * res_scale * res_scale
        bend_penalty = 8.0 * bend * res_scale
        stretch_blend = 1.0 if brush_active == 0 else (stretch / (stretch + 0.1))
        bend_blend = bend / (bend + 0.25)
        torque_scale = 0.25 * bend / wp.max(average_length * average_length, 1e-16)
        hero_offset = brush_points[strand]
        spring_weight = float(0.0)
        spring_shift = wp.vec3(0.0)
        hero_arc = float(0.0)
        span = float(1.0)
        if contact_pass == 0 and brush_active != 0 and hero_offset > 0 and hero_offset < count:
            hero = first + hero_offset
            if inverse_mass[hero] > 0.0:
                hero_arc = arc_lengths[hero]
                total_length = arc_lengths[end - 1]
                span = wp.max(0.16 * total_length, 1.5 * total_length / float(count - 1))
                span = wp.max(span, 1e-8)
                spring_weight = 32.0 * brush_weights[strand]
                spring_shift = brush_targets[strand] - points[hero]

        # The brush attaches at the same arc positions throughout a stroke.
        # Keep that factor separate so transient contacts can't overwrite it.
        factor_bank = int(0)
        refactor = factor_state[strand] != 1 + brush_active
        update_targets = contact_pass == 0
        contact_weight = float(0.0)
        if contact_pass == 0:
            converged[strand] = 0
            if refactor:
                # The same axial strain takes less displacement on a shorter
                # segment. Scale the spring energy by inverse rest length.
                for edge in range(first, end - 1):
                    scale = float(1.0)
                    if average_length > 1e-10:
                        scale = wp.clamp(average_length / wp.max(rest_lengths[edge], 1e-10), 0.25, 4.0)
                    stretch_weights[edge] = stretch_penalty * scale
                    bend_torque[edge] = 0.0
        else:
            largest_contact = float(0.0)
            for i in range(first + 1, end):
                largest_contact = wp.max(largest_contact, contact_diagonal[i])
            update_targets = largest_contact > 0.0
            refactor = update_targets
            if update_targets:
                factor_bank = 1
            # Scale the entire strand's contact block to preserve symmetry
            # and avoid arbitrarily stiff targets in very dense clumps.
            contact_weight = 8.0 / wp.max(1.0, largest_contact)
            if not update_targets and converged[strand] != 0:
                return
        for i in range(first + 1, end if update_targets else first + 1):
            mass = 1.0 / inverse_mass[i]
            attachment = attachment_weights[i]
            edge_weight = stretch_weights[i - 1]
            adjacent = -edge_weight
            if contact_pass == 0:
                if refactor:
                    falloff = wp.max(0.0, 1.0 - wp.abs(arc_lengths[i] - hero_arc) / span)
                    attachment = mass * spring_weight * falloff * falloff
                    if brush_active == 0 and i == first + 1:
                        attachment += 0.25 * bend_penalty
                    attachment_weights[i] = attachment
                relative = points[i] - root
                if brush_active == 0 and i == first + 1:
                    target[i] = mass * relative + attachment * (rest[i] - root)
                else:
                    target[i] = mass * relative + attachment * (relative + spring_shift)
                if brush_active != 0:
                    edge_dual[i] = retain_dual(edge_dual[i], 0.2 * average_length)
                    bend_dual[i] = retain_dual(bend_dual[i], 0.4 * average_length)
                else:
                    edge_dual[i] = wp.vec3(0.0)
                    bend_dual[i] = wp.vec3(0.0)
            elif contact_diagonal[i] > 0.0:
                # Apply the contact block to the input pose and add the
                # projected witness displacement to form the absolute target.
                own = contact_diagonal[i]
                left = contact_adjacent[i]
                contact_target = contact_rhs[i] + own * (points[i] - root)
                if i > first + 1:
                    contact_target += left * (points[i - 1] - root)
                if i + 1 < end:
                    contact_target += contact_adjacent[i + 1] * (points[i + 1] - root)
                target[i] += contact_weight * contact_target
                attachment += contact_weight * own
                adjacent += contact_weight * left
            if not refactor:
                continue
            d = mass + attachment + edge_weight
            if i + 1 < end:
                d += stretch_weights[i]
            # Each bend row has three coefficients: left, -1, 1-left.
            # Add its contributions to the diagonal and the two lower bands.
            two_away = float(0.0)
            if i - 2 >= first:
                left = bend_left[i - 2]
                right = 1.0 - left
                d += bend_penalty * right * right
                adjacent -= bend_penalty * right
                two_away = bend_penalty * right * left
            if i + 1 < end:
                d += bend_penalty
                adjacent -= bend_penalty * bend_left[i - 1]
            if i + 2 < end:
                left = bend_left[i]
                d += bend_penalty * left * left
            l2 = float(0.0)
            l1 = float(0.0)
            if i - 2 > first:
                l2 = two_away / factors[factor_bank, i - 2][0]
            if i - 1 > first:
                correction = float(0.0)
                previous = factors[factor_bank, i - 1]
                if i - 2 > first:
                    previous_diagonal = factors[factor_bank, i - 2][0]
                    correction = l2 * previous_diagonal * previous[1]
                    d -= l2 * l2 * previous_diagonal
                l1 = (adjacent - correction) / previous[0]
                d -= l1 * l1 * previous[0]
            factors[factor_bank, i] = wp.vec3(d, l1, l2)
        if contact_pass == 0 and refactor:
            factor_state[strand] = 1 + brush_active

        for iteration in range(iterations):
            tangent = rest_tangents[first]
            director = rest_directors[first]
            for i in range(first + 1, end):
                if stretch > 0.0:
                    edge_vec = points[i] - points[i - 1] + edge_dual[i]
                    length = wp.length(edge_vec)
                    if length > 1e-8:
                        edge_z[i] = edge_vec * (1.0 + stretch_blend * (rest_lengths[i - 1] / length - 1.0))
                    else:
                        edge_z[i] = stretch_blend * (rest[i] - rest[i - 1])
                if i - 2 >= first and bend > 0.0:
                    edge_vec = points[i - 1] - points[i - 2]
                    if wp.length_sq(edge_vec) > 1e-20:
                        next_tangent = wp.normalize(edge_vec)
                        director = transport_director(director, tangent, next_tangent)
                        tangent = next_tangent
                    rest_bend = rest_bends[i - 2]
                    cross_direction = wp.cross(tangent, director)
                    across = rest_bend[1] * director + rest_bend[2] * cross_direction
                    twist = twist_angles[i - 2]
                    if wp.abs(twist) > 1e-8:
                        across = wp.cos(twist) * across + wp.sin(twist) * wp.cross(tangent, across)
                    desired = rest_bend[0] * tangent + across
                    left = bend_left[i - 2]
                    turn = (1.0 - left) * (points[i] - points[i - 1]) - left * edge_vec
                    if contact_pass == 0 and i - 2 > first:
                        # This derivative is the change in the material bend
                        # vector per unit of axial twist. Feed its elastic
                        # torque into the same chain as the friction impulse.
                        gradient = wp.dot(turn, wp.cross(tangent, across))
                        torque = wp.clamp(torque_scale * gradient, -0.15, 0.15)
                        bend_torque[i - 2] = torque if wp.abs(torque) > 1e-5 else 0.0
                    u_arc = arc_lengths[i - 1] / wp.max(arc_lengths[end - 1], 1e-8)
                    local_bend_blend = wp.clamp(bend_blend * (1.45 - 0.85 * u_arc), 0.0, 1.0)
                    bend_z[i] = wp.lerp(turn + bend_dual[i], desired, local_bend_blend)
            for i in range(first + 1, end):
                rhs = target[i] + stretch_weights[i - 1] * (edge_z[i] - edge_dual[i])
                if i + 1 < end:
                    rhs -= stretch_weights[i] * (edge_z[i + 1] - edge_dual[i + 1])
                if i - 2 >= first:
                    rhs += bend_penalty * (1.0 - bend_left[i - 2]) * (bend_z[i] - bend_dual[i])
                if i + 1 < end:
                    rhs -= bend_penalty * (bend_z[i + 1] - bend_dual[i + 1])
                if i + 2 < end:
                    rhs += bend_penalty * bend_left[i] * (bend_z[i + 2] - bend_dual[i + 2])
                factor = factors[factor_bank, i]
                if i > first + 1:
                    rhs -= factor[1] * forward[i - 1]
                if i - 2 > first:
                    rhs -= factor[2] * forward[i - 2]
                forward[i] = rhs
            max_motion_sq = float(0.0)
            for i in range(end - 1, first, -1):
                value = forward[i] / factors[factor_bank, i][0]
                if i + 1 < end:
                    value -= factors[factor_bank, i + 1][1] * (points[i + 1] - root)
                if i + 2 < end:
                    value -= factors[factor_bank, i + 2][2] * (points[i + 2] - root)
                max_motion_sq = wp.max(max_motion_sq, wp.length_sq(value - (points[i] - root)))
                points[i] = root + value
            max_residual_sq = float(0.0)
            for i in range(first + 1, end):
                if stretch > 0.0:
                    edge_residual = points[i] - points[i - 1] - edge_z[i]
                    edge_dual[i] += edge_residual
                    max_residual_sq = wp.max(max_residual_sq, wp.length_sq(edge_residual))
                if i - 2 >= first and bend > 0.0:
                    left = bend_left[i - 2]
                    incoming = points[i - 1] - points[i - 2]
                    outgoing = points[i] - points[i - 1]
                    bend_residual = (1.0 - left) * outgoing - left * incoming - bend_z[i]
                    bend_dual[i] += bend_residual
                    max_residual_sq = wp.max(max_residual_sq, wp.length_sq(bend_residual))
            if iteration >= 1 and max_motion_sq <= tolerance_sq and max_residual_sq <= tolerance_sq:
                if contact_pass == 0:
                    converged[strand] = 1
                break

    return solve_guides


def build_twist_kernel(wp):
    @wp.kernel
    def solve_twist(
        active_guides: wp.array(dtype=int),
        angles: wp.array(dtype=float),
        impulses: wp.array(dtype=float),
        contacts: wp.array(dtype=float),
        bend_torque: wp.array(dtype=float),
        inverse_mass: wp.array(dtype=float),
        starts: wp.array(dtype=int),
        counts: wp.array(dtype=int),
        forward: wp.array(dtype=float),
        factors: wp.array(dtype=wp.vec4),
        converged: wp.array(dtype=int),
    ):
        strand = active_guides[wp.tid()]
        root_edge = starts[strand]
        end = root_edge + counts[strand] - 1
        if end <= root_edge + 1 or inverse_mass[root_edge + 1] <= 0.0:
            return

        has_twist = int(0)
        for edge in range(root_edge + 1, end):
            if angles[edge] != 0.0 or impulses[edge] != 0.0 or bend_torque[edge] != 0.0:
                has_twist = 1
                break
        if has_twist == 0:
            return

        # Implicit smoothing of relative rotations, with the root frame fixed.
        # Imported curves have no authored twist, so the reference gradient is zero.
        for edge in range(root_edge + 1, end):
            kick = wp.clamp(impulses[edge] / wp.max(contacts[edge], 1.0), -0.35, 0.35)
            factor = factors[edge]
            rhs = factor[3] * (0.92 * angles[edge] + kick + bend_torque[edge])
            if edge > root_edge + 1:
                rhs += factor[1] * forward[edge - 1]
            forward[edge] = rhs * factor[0]
        for edge in range(end - 1, root_edge, -1):
            angle = forward[edge]
            if edge + 1 < end:
                factor = factors[edge]
                angle += factor[2] * factor[0] * angles[edge + 1]
            if angle != angles[edge]:
                converged[strand] = 0
            angles[edge] = angle

    return solve_twist
