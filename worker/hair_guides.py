"""Guide selection and follower binding for interactive grooming."""

from functools import lru_cache

import numpy as np
from hair_contacts import build_contact_kernels
from hair_rods import build_director_transport, build_rod_kernel, build_twist_kernel


def _morton_order(roots):
    minimum = roots.min(axis=0)
    extent = float(np.max(roots.max(axis=0) - minimum))
    if extent == 0.0:
        return np.arange(len(roots), dtype=np.int32)
    grid = np.clip((roots - minimum) * (1023.0 / extent), 0, 1023).astype(np.uint32)
    codes = np.zeros(len(roots), dtype=np.uint32)
    for bit in range(10):
        for axis in range(3):
            codes |= ((grid[:, axis] >> bit) & 1) << (3 * bit + axis)
    return np.argsort(codes, kind="stable").astype(np.int32)


def _arc_sample(points, parameters, starts, counts, fraction):
    lower = starts.copy()
    upper = starts + counts - 1
    while np.any(upper - lower > 1):
        middle = (lower + upper) // 2
        searching = upper - lower > 1
        before = parameters[middle] < fraction
        lower = np.where(searching & before, middle, lower)
        upper = np.where(searching & ~before, middle, upper)
    span = parameters[upper] - parameters[lower]
    t = np.clip((fraction - parameters[lower]) / np.maximum(span, 1e-12), 0.0, 1.0)
    return points[lower] + t[:, None] * (points[upper] - points[lower])


def _sample_guide_ranks(order, roots, quarter, middle, guide_count):
    """Spread the guide budget across roots and distinct nearby curve shapes."""
    count = len(order)
    if guide_count == count:
        return np.arange(count, dtype=np.int64)
    # Each contiguous root neighborhood gets at most six guides. The starts
    # and stops apportion the exact guide budget, including the last group.
    slots = min(6, guide_count)
    first_slot = np.arange(0, guide_count, slots, dtype=np.int64)
    last_slot = np.minimum(first_slot + slots, guide_count)
    start = first_slot * count // guide_count
    stop = last_slot * count // guide_count
    local_rank = np.arange(int(np.max(stop - start)))
    ranks = start[:, None] + local_rank
    valid = ranks < stop[:, None]
    strands = order[np.minimum(ranks, stop[:, None] - 1)]
    features = np.concatenate((roots[strands], quarter[strands], 0.5 * middle[strands]), axis=2)
    center = np.sum(features * valid[..., None], axis=1) / (stop - start)[:, None]
    distance = np.sum((features - center[:, None]) ** 2, axis=2)
    pick = np.argmin(np.where(valid, distance, np.inf), axis=1)
    nearest = np.where(valid, np.inf, -np.inf)
    rows = np.arange(len(start))
    selected = np.empty((len(start), slots), dtype=np.int64)
    for slot in range(slots):
        selected[:, slot] = ranks[rows, pick]
        distance = np.sum((features - features[rows, pick][:, None]) ** 2, axis=2)
        np.minimum(nearest, distance, out=nearest)
        nearest[rows, pick] = -np.inf
        pick = np.argmax(nearest, axis=1)
    return np.sort(selected[np.arange(slots) < (last_slot - first_slot)[:, None]])


def bind_guides(rest_points, counts, guide_fraction=0.10, *, parameters=None, max_guides=1024):
    """Return guide strand indices and compact, weighted follower bindings.

    Input curves must have at least two points. Guide sampling balances root
    location and rest shape. Followers consider bounded candidate windows near
    the root and the quarter-length point.
    """
    count = len(counts)
    if not count:
        raise ValueError("Guide binding requires at least one strand")
    starts = np.cumsum(counts, dtype=np.int32) - counts
    roots = rest_points[starts]
    if parameters is None:
        parameters = strand_parameters(rest_points, counts)
    quarter = _arc_sample(rest_points, parameters, starts, counts, 0.25) - roots
    middle = _arc_sample(rest_points, parameters, starts, counts, 0.50) - roots
    tip = rest_points[starts + counts - 1] - roots
    reach = np.linalg.norm(quarter, axis=1, keepdims=True)
    direction = quarter / np.maximum(reach, 1e-12)
    order = _morton_order(roots)
    guide_count = min(count, max(1, int(np.ceil(count * guide_fraction))))
    if max_guides is not None:
        guide_count = min(guide_count, int(max_guides))
    ranks = _sample_guide_ranks(order, roots, quarter, middle, guide_count)
    guides = order[ranks]

    influence_count = min(4, guide_count)
    window = min(24, guide_count)
    landmark_window = min(12, guide_count)
    if guide_count > window:
        landmark_order = _morton_order(roots + quarter)
        landmark_ranks = np.empty(count, dtype=np.int32)
        landmark_ranks[landmark_order] = np.arange(count, dtype=np.int32)
        guide_landmark_order = np.argsort(landmark_ranks[guides], kind="stable")
        guide_landmark_ranks = landmark_ranks[guides[guide_landmark_order]]
    bindings = np.empty((count, influence_count), dtype=np.int32)
    weights = np.empty((count, influence_count), dtype=np.float32)
    for begin in range(0, count, 4096):
        end = min(begin + 4096, count)
        strands = order[begin:end]
        center = np.searchsorted(ranks, np.arange(begin, end))
        first = np.clip(center - window // 2, 0, guide_count - window)
        candidates = first[:, None] + np.arange(window)
        if guide_count > window:
            landmark_center = np.searchsorted(guide_landmark_ranks, landmark_ranks[strands])
            landmark_first = np.clip(landmark_center - landmark_window // 2, 0, guide_count - landmark_window)
            landmark_candidates = guide_landmark_order[landmark_first[:, None] + np.arange(landmark_window)]
            candidates = np.sort(np.concatenate((candidates, landmark_candidates), axis=1), axis=1)
        candidate_strands = guides[candidates]
        offsets = roots[candidate_strands] - roots[strands, None]
        distance_sq = np.sum(offsets * offsets, axis=2)
        quarter_delta = quarter[candidate_strands] - quarter[strands, None]
        middle_delta = middle[candidate_strands] - middle[strands, None]
        tip_delta = tip[candidate_strands] - tip[strands, None]
        scores = distance_sq + np.sum(quarter_delta * quarter_delta, axis=2)
        scores += 0.25 * np.sum(middle_delta * middle_delta, axis=2)
        scores += 0.25 * np.sum(tip_delta * tip_delta, axis=2)
        scores = np.maximum(scores, 1e-20)
        alignment = np.sum(direction[candidate_strands] * direction[strands, None], axis=2)
        compatible = alignment >= 0.0
        # Keep curves on their side of a part whenever a matching guide exists.
        scores = np.where(compatible | ~compatible.any(axis=1, keepdims=True), scores, np.inf)
        # A guide found by both searches must contribute only once.
        scores[:, 1:] = np.where(candidates[:, 1:] == candidates[:, :-1], np.inf, scores[:, 1:])
        best = np.argsort(scores, axis=1, kind="stable")[:, :influence_count]
        chosen = np.take_along_axis(candidates, best, axis=1)
        influence = 1.0 / np.take_along_axis(scores, best, axis=1)
        bindings[strands] = chosen
        weights[strands] = influence / influence.sum(axis=1, keepdims=True)

    # Even coincident guide roots must reproduce their own simulated strands.
    bindings[guides] = np.arange(guide_count, dtype=np.int32)[:, None]
    weights[guides] = 0.0
    weights[guides, 0] = 1.0
    return guides, bindings, weights


def strand_parameters(rest_points, counts):
    """Return normalized arc lengths packed in the same order as the points."""
    starts = np.cumsum(counts, dtype=np.int32) - counts
    ends = starts + counts - 1
    distances = np.empty(len(rest_points), dtype=np.float64)
    distances[0] = 0.0
    distances[1:] = np.linalg.norm(rest_points[1:] - rest_points[:-1], axis=1)
    distances[starts] = 0.0
    cumulative = np.cumsum(distances)
    offsets = np.repeat(cumulative[starts], counts)
    lengths = np.repeat(cumulative[ends] - cumulative[starts], counts)
    return np.clip((cumulative - offsets) / np.maximum(lengths, 1e-12), 0.0, 1.0).astype(np.float32)


@lru_cache(maxsize=1)
def build_guide_kernels(wp):
    transport_director = build_director_transport(wp)

    @wp.kernel
    def gather_points(source: wp.array(dtype=wp.vec3), indices: wp.array(dtype=int), target: wp.array(dtype=wp.vec3)):
        i = wp.tid()
        target[i] = source[indices[i]]

    @wp.kernel
    def gather_guide_inertia(
        full_arc_lengths: wp.array(dtype=float),
        full_inverse_mass: wp.array(dtype=float),
        indices: wp.array(dtype=int),
        starts: wp.array(dtype=int),
        counts: wp.array(dtype=int),
        arc_lengths: wp.array(dtype=float),
        inverse_mass: wp.array(dtype=float),
        twist_factors: wp.array(dtype=wp.vec4),
        twist_stiffness: float,
    ):
        strand = wp.tid()
        first = starts[strand]
        count = counts[strand]
        full_first = indices[first]
        full_end = full_first + count
        average = full_arc_lengths[full_end - 1] / float(count - 1)
        for offset in range(count):
            point = first + offset
            source = full_first + offset
            arc_lengths[point] = full_arc_lengths[source]
            mobility = full_inverse_mass[source]
            if mobility > 0.0 and average > 1e-10:
                left_idx = wp.max(full_first, source - 1)
                right_idx = wp.min(full_end - 1, source + 1)
                dual_length = 0.5 * (full_arc_lengths[right_idx] - full_arc_lengths[left_idx])
                relative_mass = wp.clamp(dual_length / average, 0.25, 4.0)
                mobility /= relative_mass
            inverse_mass[point] = mobility

        if count <= 2 or full_inverse_mass[full_first + 1] <= 0.0:
            return
        left_span = 0.5 * full_arc_lengths[full_first + 2]
        left = twist_stiffness
        if average > 1e-10:
            left *= wp.clamp(average / wp.max(left_span, 1e-10), 0.25, 4.0)
        elimination = float(0.0)
        for offset in range(1, count - 1):
            source = full_first + offset
            mass = float(1.0)
            if average > 1e-10:
                length = full_arc_lengths[source + 1] - full_arc_lengths[source]
                mass = wp.clamp(length / average, 0.25, 4.0)
            right = float(0.0)
            if offset + 2 < count:
                right = twist_stiffness
                if average > 1e-10:
                    span = 0.5 * (full_arc_lengths[source + 2] - full_arc_lengths[source])
                    right *= wp.clamp(average / wp.max(span, 1e-10), 0.25, 4.0)
            inverse = 1.0 / (mass + left + right - elimination)
            # Store inverse pivot, left and right stiffness, and axial inertia.
            twist_factors[first + offset] = wp.vec4(inverse, left, right, mass)
            elimination = right * right * inverse
            left = right

    @wp.kernel
    def gather_brush_selection(
        guides: wp.array(dtype=int),
        active: wp.array(dtype=int),
        pinned: wp.array(dtype=int),
        hero: wp.array(dtype=int),
        hero_offset: wp.array(dtype=wp.vec3),
        targets: wp.array(dtype=wp.vec3),
        weights: wp.array(dtype=float),
        guide_hero: wp.array(dtype=int),
        guide_hero_offset: wp.array(dtype=wp.vec3),
        guide_targets: wp.array(dtype=wp.vec3),
        guide_weights: wp.array(dtype=float),
        selection_counts: wp.array(dtype=int),
        starts: wp.array(dtype=int),
        counts: wp.array(dtype=int),
        active_guides: wp.array(dtype=int),
        active_edges: wp.array(dtype=int),
    ):
        i = wp.tid()
        strand = guides[i]
        guide_hero[i] = hero[strand]
        guide_hero_offset[i] = hero_offset[strand]
        guide_targets[i] = targets[strand]
        guide_weights[i] = weights[strand]
        if active[strand] != 0 and pinned[strand] == 0:
            slot = wp.atomic_add(selection_counts, 0, 1)
            active_guides[slot] = i
            edge_count = counts[i] - 1
            edge_slot = wp.atomic_add(selection_counts, 3, edge_count)
            first_edge = starts[i] - i
            for local in range(edge_count):
                active_edges[edge_slot + local] = first_edge + local

    @wp.kernel
    def compact_follower_selection(
        starts: wp.array(dtype=int),
        counts: wp.array(dtype=int),
        active: wp.array(dtype=int),
        pinned: wp.array(dtype=int),
        guide_of_strand: wp.array(dtype=int),
        guides: wp.array(dtype=int),
        bindings: wp.array2d(dtype=int),
        weights: wp.array2d(dtype=float),
        selection_counts: wp.array(dtype=int),
        active_points: wp.array(dtype=int),
        active_followers: wp.array(dtype=int),
    ):
        strand = wp.tid()
        count = counts[strand]
        if active[strand] == 0 or pinned[strand] != 0 or count < 2:
            return
        is_follower = guide_of_strand[strand] < 0
        if is_follower:
            has_moving_guide = int(0)
            for influence in range(bindings.shape[1]):
                guide_strand = guides[bindings[strand, influence]]
                if weights[strand, influence] > 0.0 and active[guide_strand] != 0 and pinned[guide_strand] == 0:
                    has_moving_guide = 1
                    break
            if has_moving_guide == 0:
                return
        first_point = wp.atomic_add(selection_counts, 1, count - 1)
        for local in range(1, count):
            active_points[first_point + local - 1] = starts[strand] + local
        if is_follower:
            follower = wp.atomic_add(selection_counts, 2, 1)
            active_followers[follower] = strand

    @wp.kernel
    def transport_guide_frames(
        active_guides: wp.array(dtype=int),
        points: wp.array(dtype=wp.vec3),
        starts: wp.array(dtype=int),
        counts: wp.array(dtype=int),
        root_rest_tangents: wp.array(dtype=wp.vec3),
        root_rest_directors: wp.array(dtype=wp.vec3),
        twist_angles: wp.array(dtype=float),
        use_rest_seed: int,
        tangents: wp.array(dtype=wp.vec3),
        directors: wp.array(dtype=wp.vec3),
        rest_bends: wp.array(dtype=wp.vec3),
        bend_left: wp.array(dtype=float),
        rest_lengths: wp.array(dtype=float),
    ):
        guide = active_guides[wp.tid()]
        start = starts[guide]
        end = start + counts[guide] - 1
        tangent = points[start + 1] - points[start]
        if wp.length_sq(tangent) <= 1e-20:
            tangent = wp.vec3(0.0, 1.0, 0.0)
            if use_rest_seed != 0:
                tangent = root_rest_tangents[start]
        tangent = wp.normalize(tangent)
        basis = wp.vec3(1.0, 0.0, 0.0)
        if wp.abs(tangent[0]) > 0.8:
            basis = wp.vec3(0.0, 1.0, 0.0)
        director = wp.normalize(wp.cross(tangent, basis))
        if use_rest_seed != 0:
            director = transport_director(root_rest_directors[start], root_rest_tangents[start], tangent)
        for point in range(start, end):
            edge = points[point + 1] - points[point]
            length = wp.length(edge)
            if length > 1e-10:
                next_tangent = edge / length
                director = transport_director(director, tangent, next_tangent)
                tangent = next_tangent
            tangents[point] = tangent
            material_director = director
            if use_rest_seed != 0:
                angle = twist_angles[point]
                if wp.abs(angle) > 1e-8:
                    material_director = wp.cos(angle) * director + wp.sin(angle) * wp.cross(tangent, director)
            else:
                rest_lengths[point] = length
                if point + 2 <= end:
                    next_edge = points[point + 2] - points[point + 1]
                    span = length + wp.length(next_edge)
                    left = float(0.5)
                    if span > 1e-10:
                        left = wp.clamp(1.0 - length / span, 0.1, 0.9)
                    bend_left[point] = left
                    turn = (1.0 - left) * next_edge - left * edge
                    rest_bends[point] = wp.vec3(
                        wp.dot(turn, tangent), wp.dot(turn, director),
                        wp.dot(turn, wp.cross(tangent, director)),
                    )
            directors[point] = material_director
        if end >= start:
            tangents[end] = tangent
            directors[end] = material_director

    @wp.func
    def map_frame(
        offset: wp.vec3,
        rest_tangent: wp.vec3,
        rest_director: wp.vec3,
        live_tangent: wp.vec3,
        live_director: wp.vec3,
    ):
        return (
            wp.dot(offset, rest_tangent) * live_tangent
            + wp.dot(offset, rest_director) * live_director
            + wp.dot(offset, wp.cross(rest_tangent, rest_director)) * wp.cross(live_tangent, live_director)
        )

    @wp.kernel
    def bind_follower_samples(
        parameters: wp.array(dtype=float),
        strand_ids: wp.array(dtype=int),
        guide_of_strand: wp.array(dtype=int),
        bindings: wp.array2d(dtype=int),
        guide_starts: wp.array(dtype=int),
        guide_counts: wp.array(dtype=int),
        guide_parameters: wp.array(dtype=float),
        strand_lengths: wp.array(dtype=float),
        guide_lengths: wp.array(dtype=float),
        sample_segments: wp.array2d(dtype=int),
        sample_fractions: wp.array2d(dtype=float),
    ):
        point, influence = wp.tid()
        strand = strand_ids[point]
        if guide_of_strand[strand] >= 0:
            return
        guide = bindings[strand, influence]
        length_ratio = wp.clamp(
            strand_lengths[strand] / wp.max(guide_lengths[guide], 1e-8), 0.0, 1.0,
        )
        parameter = parameters[point] * length_ratio
        lower = guide_starts[guide]
        upper = lower + guide_counts[guide] - 1
        while upper - lower > 1:
            mid = (upper + lower) // 2
            if guide_parameters[mid] <= parameter:
                lower = mid
            else:
                upper = mid
        start_t = guide_parameters[lower]
        segment_t = guide_parameters[upper] - start_t
        sample_segments[point, influence] = lower
        sample_fractions[point, influence] = wp.clamp(
            (parameter - start_t) / wp.max(segment_t, 1e-8), 0.0, 1.0,
        )

    @wp.kernel
    def deform_followers(
        active_points: wp.array(dtype=int),
        rest: wp.array(dtype=wp.vec3),
        parameters: wp.array(dtype=float),
        strand_ids: wp.array(dtype=int),
        strand_starts: wp.array(dtype=int),
        guide_of_strand: wp.array(dtype=int),
        bindings: wp.array2d(dtype=int),
        weights: wp.array2d(dtype=float),
        guide_starts: wp.array(dtype=int),
        guide_inverse_mass: wp.array(dtype=float),
        sample_segments: wp.array2d(dtype=int),
        sample_fractions: wp.array2d(dtype=float),
        guide_rest: wp.array(dtype=wp.vec3),
        guide_live: wp.array(dtype=wp.vec3),
        rest_tangents: wp.array(dtype=wp.vec3),
        rest_directors: wp.array(dtype=wp.vec3),
        live_tangents: wp.array(dtype=wp.vec3),
        live_directors: wp.array(dtype=wp.vec3),
        previous: wp.array(dtype=wp.vec3),
        destination: wp.array(dtype=wp.vec3),
        strand_counts: wp.array(dtype=int),
        vertex_stride: int,
    ):
        point = active_points[wp.tid()]
        strand = strand_ids[point]
        root = strand_starts[strand]
        guide = guide_of_strand[strand]
        if guide >= 0:
            destination[point] = guide_live[guide_starts[guide] + point - root]
            return
        local_k = point - root
        s_count = strand_counts[strand]
        if vertex_stride > 1 and local_k > 0 and local_k < s_count - 1 and (local_k % vertex_stride) != 0:
            previous[point] = destination[point]
            return
        parameter = parameters[point]
        blended = wp.vec3(0.0, 0.0, 0.0)
        for influence in range(bindings.shape[1]):
            weight = weights[strand, influence]
            if weight <= 0.0:
                continue
            guide = bindings[strand, influence]
            guide_root = guide_starts[guide]
            if guide_inverse_mass[guide_root + 1] <= 0.0:
                continue
            lower = sample_segments[point, influence]
            upper = lower + 1
            t = sample_fractions[point, influence]
            rest_segment = guide_rest[upper] - guide_rest[lower]
            live_segment = guide_live[upper] - guide_live[lower]
            center = guide_live[lower] + t * live_segment
            root_offset = rest[root] - guide_rest[guide_root]
            offset = (rest[point] - (guide_rest[lower] + t * rest_segment)) - root_offset
            mapped_lower = map_frame(offset, rest_tangents[lower], rest_directors[lower], live_tangents[lower], live_directors[lower])
            mapped_upper = map_frame(offset, rest_tangents[upper], rest_directors[upper], live_tangents[upper], live_directors[upper])
            mapped = wp.lerp(mapped_lower, mapped_upper, t)
            guide_shift = center - (guide_rest[lower] + t * rest_segment)
            root_shift = guide_live[guide_root] - guide_rest[guide_root]
            rotation_shift = mapped - offset
            # A guide on another layer can have a large rest offset. Carry its
            # motion, but bound the leverage of that offset near partings.
            rotation_limit = wp.length(guide_shift) + 0.25 * wp.length(rest_segment)
            rotation_length = wp.length(rotation_shift)
            if rotation_length > rotation_limit and rotation_length > 1e-8:
                rotation_shift *= rotation_limit / rotation_length
            guide_shift -= (1.0 - parameter) * root_shift
            blended += weight * (guide_shift + wp.min(1.0, 4.0 * parameter) * rotation_shift)
        previous[point] = destination[point]
        # Stationary influences contribute zero displacement but still retain
        # their share of the original weights at the edge of the stroke.
        destination[point] = rest[point] + blended

    @wp.kernel
    def bound_brush_sample(
        previous: wp.array(dtype=wp.vec3),
        stroke_rest: wp.array(dtype=wp.vec3),
        proposed: wp.array(dtype=wp.vec3),
        starts: wp.array(dtype=int),
        counts: wp.array(dtype=int),
        inverse_mass: wp.array(dtype=float),
        arc_lengths: wp.array(dtype=float),
        active_strands: wp.array(dtype=int),
        use_active_strands: int,
        collider: wp.uint64,
        has_collider: int,
        clearance: float,
    ):
        strand = wp.tid()
        if use_active_strands != 0:
            strand = active_strands[strand]
        first = starts[strand]
        end = first + counts[strand]
        if end <= first + 1 or inverse_mass[first + 1] <= 0.0:
            return
        proposed[first] = previous[first]
        fraction = float(1.0)
        max_motion = wp.max(0.25 * arc_lengths[end - 1], 1e-8)
        for i in range(first + 1, end):
            if not wp.isfinite(wp.length_sq(proposed[i])):
                fraction = 0.0
                break
            displacement = proposed[i] - previous[i]
            motion = wp.length(displacement)
            if motion > max_motion:
                fraction = wp.min(fraction, max_motion / motion)
            old_edge = previous[i] - previous[i - 1]
            new_edge = proposed[i] - proposed[i - 1]
            rest_length = wp.length(stroke_rest[i] - stroke_rest[i - 1])
            old_len_sq = wp.length_sq(old_edge)
            limit = wp.max(wp.sqrt(old_len_sq) * (1.0 + 1e-4), 1.15 * rest_length) + 1e-6
            if wp.length_sq(new_edge) > limit * limit:
                # Find the furthest safe point on the line between these
                # samples. Use one fraction for the whole strand.
                change = new_edge - old_edge
                a = wp.length_sq(change)
                b = wp.dot(old_edge, change)
                c = old_len_sq - limit * limit
                discriminant = wp.sqrt(wp.max(0.0, b * b - a * c))
                if a > 1e-20:
                    if b > 0.0 and c < 0.0:
                        safe_t = (-c) / wp.max(discriminant + b, 1e-20)
                    else:
                        safe_t = (discriminant - b) / a
                    fraction = wp.min(fraction, wp.clamp(safe_t, 0.0, 1.0))
            if has_collider != 0 and motion * fraction > 1e-8:
                direction = displacement / motion
                hit = wp.mesh_query_ray(collider, previous[i], direction, motion * fraction)
                if hit.result:
                    normal = wp.mesh_eval_face_normal(collider, hit.face)
                    if wp.dot(direction, normal) < -1e-5:
                        fraction = wp.min(fraction, wp.max(0.0, hit.t - clearance) / motion)
            if fraction <= 0.0:
                break
        if fraction < 1.0:
            for i in range(first + 1, end):
                if fraction <= 0.0:
                    proposed[i] = previous[i]
                else:
                    proposed[i] = wp.lerp(previous[i], proposed[i], fraction)

    return (
        gather_points, gather_guide_inertia, gather_brush_selection, transport_guide_frames,
        bind_follower_samples, deform_followers, bound_brush_sample, compact_follower_selection,
    )


def bound_full_brush(wp, device, session, previous, collider_override=None):
    collider = collider_override if collider_override is not None else session.get("wp_collider")
    wp.launch(
        build_guide_kernels(wp)[6], session["num_strands"],
        inputs=[
            previous, session["anim_rest_wp"], session["pos_wp"],
            session["vert_starts_wp"], session["counts_wp"], session["brush_inv_mass_wp"],
            session["stroke_arc_lengths_wp"], session["counts_wp"], 0,
            collider.id if collider is not None else 0, int(collider is not None),
            1e-5 * session["unit_scale"],
        ],
        device=device,
    )


def create_guide_buffers(wp, device, rest_points, counts, contact_diameter, friction, bend_stiffness):
    parameters = strand_parameters(rest_points, counts)
    guides, bindings, weights = bind_guides(rest_points, counts, parameters=parameters)
    starts = np.cumsum(counts, dtype=np.int32) - counts
    ends = starts + counts - 1
    distances = np.empty(len(rest_points), dtype=np.float64)
    distances[0] = 0.0
    distances[1:] = np.linalg.norm(rest_points[1:] - rest_points[:-1], axis=1)
    distances[starts] = 0.0
    cumulative = np.cumsum(distances)
    strand_lengths = (cumulative[ends] - cumulative[starts]).astype(np.float32)
    guide_lengths = strand_lengths[guides]
    guide_counts = counts[guides]
    guide_starts = np.cumsum(guide_counts, dtype=np.int32) - guide_counts
    guide_ids = np.repeat(np.arange(len(guides), dtype=np.int32), guide_counts)
    local = np.arange(len(guide_ids), dtype=np.int32) - guide_starts[guide_ids]
    indices = starts[guides[guide_ids]] + local
    guide_points = rest_points[indices]
    seg_start = np.flatnonzero(local + 1 < guide_counts[guide_ids]).astype(np.int32)
    bend_start = np.flatnonzero(local + 2 < guide_counts[guide_ids]).astype(np.int32)
    segment_lengths = np.linalg.norm(guide_points[seg_start + 1] - guide_points[seg_start], axis=1)
    positive_lengths = segment_lengths[segment_lengths > 1e-8]
    median_seg_len = float(np.median(positive_lengths)) if positive_lengths.size else contact_diameter
    if positive_lengths.size:
        # Render widths can be deliberately inflated for a groom's look.
        contact_diameter = min(contact_diameter, 0.1 * median_seg_len)
    guide_of_strand = np.full(len(counts), -1, dtype=np.int32)
    guide_of_strand[guides] = np.arange(len(guides), dtype=np.int32)

    def array(values, dtype):
        return wp.array(values, dtype=dtype, device=device)

    def zeros(dtype):
        return wp.zeros(len(indices), dtype=dtype, device=device)

    buffers = {
        "num_strands": len(guides),
        "max_guide_margin": 0.45 * median_seg_len,
        "guides_wp": array(guides, int),
        "indices": array(indices, int),
        "bindings": array(bindings, int),
        "weights": array(weights, float),
        "parameters": array(parameters, float),
        "guide_of_strand": array(guide_of_strand, int),
        "guide_starts": array(guide_starts, int),
        "guide_counts": array(guide_counts, int),
        "guide_parameters": array(parameters[indices], float),
        "strand_lengths_wp": array(strand_lengths, float),
        "guide_lengths_wp": array(guide_lengths, float),
        "sample_segments": wp.empty((len(rest_points), bindings.shape[1]), dtype=int, device=device),
        "sample_fractions": wp.empty((len(rest_points), bindings.shape[1]), dtype=float, device=device),
        "total_verts": len(indices),
        "pos_wp": zeros(wp.vec3),
        "prev_pos_wp": zeros(wp.vec3),
        "vel_wp": zeros(wp.vec3),
        "anim_rest_wp": zeros(wp.vec3),
        "rest_tangents": zeros(wp.vec3),
        "rest_directors": zeros(wp.vec3),
        "rest_bends": zeros(wp.vec3),
        "bend_left": zeros(float),
        "rest_lengths": zeros(float),
        "live_tangents": zeros(wp.vec3),
        "live_directors": zeros(wp.vec3),
        "twist_angles": zeros(float),
        "root_t_wp": array(parameters[indices], float),
        "inv_mass_wp": zeros(float),
        "cum_len_wp": zeros(float),
        "strand_hero_k_wp": wp.empty(len(guides), dtype=int, device=device),
        "strand_hero_offset_wp": wp.empty(len(guides), dtype=wp.vec3, device=device),
        "strand_surface_target_wp": wp.empty(len(guides), dtype=wp.vec3, device=device),
        "strand_brush_w_wp": wp.empty(len(guides), dtype=float, device=device),
        "selection_counts": wp.zeros(4, dtype=int, device=device),
        "active_guides": wp.empty(len(guides), dtype=int, device=device),
        "active_edges": wp.empty(len(seg_start), dtype=int, device=device),
        "active_points": wp.empty(len(rest_points), dtype=int, device=device),
        "active_followers": wp.empty(len(counts), dtype=int, device=device),
        "delta_pos_wp": zeros(wp.vec3),
        "delta_w_wp": zeros(float),
        "n_segs": len(seg_start),
        "seg_v0_wp": array(seg_start, int),
        "seg_v1_wp": array(seg_start + 1, int),
        "seg_len_wp": array(segment_lengths, float),
        "n_bends": len(bend_start),
        "bend_v0_wp": array(bend_start, int),
        "bend_v2_wp": array(bend_start + 2, int),
        "bend_len_wp": array(np.linalg.norm(guide_points[bend_start + 2] - guide_points[bend_start], axis=1), float),
        "stroke_ready": None,
    }
    buffers["vert_starts_wp"] = buffers["guide_starts"]
    buffers["brush_inv_mass_wp"] = buffers["inv_mass_wp"]
    solve = build_rod_kernel(wp)
    buffers["edge_dual"] = zeros(wp.vec3)
    buffers["bend_dual"] = zeros(wp.vec3)
    guide_converged = wp.zeros(len(guides), dtype=int, device=device)
    buffers["rod_factor_state"] = wp.zeros(len(guides), dtype=int, device=device)
    contact_adjacent = zeros(float)
    bend_torque = zeros(float)
    solver_inputs = [
        buffers["active_guides"],
        buffers["pos_wp"], buffers["anim_rest_wp"], buffers["inv_mass_wp"],
        buffers["guide_starts"], buffers["guide_counts"],
        buffers["cum_len_wp"], buffers["strand_hero_k_wp"],
        buffers["strand_surface_target_wp"], buffers["strand_brush_w_wp"],
        zeros(wp.vec3),  # unconstrained positions
        zeros(float), buffers["delta_pos_wp"], buffers["delta_w_wp"], contact_adjacent, buffers["twist_angles"],
        buffers["rest_tangents"], buffers["rest_directors"], buffers["rest_bends"],
        buffers["bend_left"], buffers["rest_lengths"],
        zeros(float),  # per-edge axial weights
        zeros(wp.vec3), buffers["edge_dual"],
        zeros(wp.vec3), buffers["bend_dual"], bend_torque,
        wp.empty((2, len(indices)), dtype=wp.vec3, device=device),
        buffers["rod_factor_state"],
        zeros(wp.vec3),  # forward substitution
        guide_converged,
    ]

    def solve_elastic(stretch, bend, iterations, brush_active, contact_pass):
        stiffness = (stretch, bend)
        if buffers.get("rod_factor_stiffness") != stiffness:
            buffers["rod_factor_state"].zero_()
            buffers["rod_factor_stiffness"] = stiffness
        wp.launch(
            solve, buffers["active_guide_count"],
            inputs=[*solver_inputs, stretch, bend, iterations, brush_active, contact_pass],
            device=device,
        )

    buffers["solve_elastic"] = solve_elastic
    edge_ends = seg_start + 1
    lowers = array(np.minimum(guide_points[seg_start], guide_points[edge_ends]), wp.vec3)
    uppers = array(np.maximum(guide_points[seg_start], guide_points[edge_ends]), wp.vec3)
    bvh = wp.Bvh(lowers, uppers)
    edge_strands = array(guide_ids[seg_start], int)
    segment_bounds, project_contacts, project_body_contacts = build_contact_kernels(wp)
    solve_twist = build_twist_kernel(wp)
    twist_impulses = zeros(float)
    twist_contacts = zeros(float)
    buffers["twist_stiffness"] = 2.0 * bend_stiffness
    buffers["twist_factors"] = zeros(wp.vec4)
    twist_inputs = [
        buffers["active_guides"],
        buffers["twist_angles"], twist_impulses, twist_contacts, bend_torque, buffers["inv_mass_wp"],
        buffers["guide_starts"], buffers["guide_counts"], zeros(float), buffers["twist_factors"],
        guide_converged,
    ]
    contact_inputs = [
        buffers["active_edges"],
        buffers["pos_wp"], buffers["prev_pos_wp"], buffers["anim_rest_wp"], buffers["inv_mass_wp"],
        buffers["seg_v0_wp"], buffers["seg_v1_wp"], edge_strands, lowers, uppers,
        bvh.id, contact_diameter, friction, buffers["delta_pos_wp"], buffers["delta_w_wp"],
        contact_adjacent, buffers["twist_factors"], twist_impulses, twist_contacts,
    ]

    def update_contact_bounds(active_only):
        edge_count = buffers["active_edge_count"] if active_only else len(seg_start)
        wp.launch(
            segment_bounds, edge_count,
            inputs=[
                buffers["active_edges"], int(active_only), buffers["pos_wp"],
                buffers["seg_v0_wp"], buffers["seg_v1_wp"], lowers, uppers,
            ],
            device=device,
        )

    def project_guide_contacts(collider_id=0, margin=0.0, max_query_dist=1.0, self_collision=True):
        if self_collision:
            update_contact_bounds(True)
            bvh.refit()
            contact_adjacent.zero_()
            twist_impulses.zero_()
            twist_contacts.zero_()
            wp.launch(project_contacts, buffers["active_edge_count"], inputs=contact_inputs, device=device)
        if collider_id != 0:
            wp.launch(
                project_body_contacts, buffers["active_edge_count"],
                inputs=[
                    buffers["active_edges"], buffers["seg_v1_wp"],
                    buffers["pos_wp"], buffers["prev_pos_wp"], buffers["inv_mass_wp"],
                    collider_id, margin, friction, max_query_dist,
                    buffers["delta_pos_wp"], buffers["delta_w_wp"],
                ],
                device=device,
            )
        if self_collision and (friction > 0.0 or bend_stiffness > 0.0):
            wp.launch(solve_twist, buffers["active_guide_count"], inputs=twist_inputs, device=device)

    buffers["project_guide_contacts"] = project_guide_contacts
    buffers["update_contact_bounds"] = update_contact_bounds
    return buffers


def step_guide_brush(
    wp, device, session, apply_brush, new_stroke, timing_events=None,
    dynamic_guides=False, self_collision=True, zero_gravity=False, vertex_stride=1,
    bend_scale=1.0, collider_override=None,
):
    """Solve compact guides and reconstruct full-resolution strand points."""
    counts = session["counts_np"]
    if len(counts) < 1024 or counts.min() < 2 or not session.get("grab_ready"):
        return False
    points = session["pos_wp"]
    cache = session.get("guide_cache")
    if cache is None:
        widths = session.get("widths_np")
        diameter = 0.00015 * session["unit_scale"]
        if widths is not None and np.any(widths > 0):
            transform = np.asarray(session["local_to_world"], dtype=np.float32)
            scale = float(np.max(np.linalg.norm(transform[:3, :3], axis=1)))
            diameter = float(np.median(widths[widths > 0])) * scale
        cache = create_guide_buffers(
            wp, device, session["anim_rest_wp"].numpy(), counts, diameter, session["friction"],
            session["bend_stiffness"],
        )
        cache["kernels"] = build_guide_kernels(wp)
        cache["full_rest"] = wp.empty_like(points)
        cache["previous_sample"] = wp.empty_like(points)
        cache["previous_guides"] = wp.empty_like(cache["pos_wp"])
        bind_follower_samples = cache["kernels"][4]
        wp.launch(
            bind_follower_samples, cache["sample_segments"].shape,
            inputs=[
                cache["parameters"], session["strand_ids_wp"], cache["guide_of_strand"],
                cache["bindings"], cache["guide_starts"], cache["guide_counts"],
                cache["guide_parameters"], cache["strand_lengths_wp"], cache["guide_lengths_wp"],
                cache["sample_segments"], cache["sample_fractions"],
            ],
            device=device,
        )
        session["guide_cache"] = cache
    (
        gather_points, gather_inertia, gather_brush_selection, transport_frames, _,
        deform_followers, bound_sample, compact_follower_selection,
    ) = cache["kernels"]
    guide_points = cache["pos_wp"]
    indices = cache["indices"]
    size = cache["total_verts"]

    if new_stroke or cache["stroke_ready"] is None:
        cache["post_solve_cuda_graph"] = None
        cache["selection_counts"].zero_()
        wp.launch(
            gather_brush_selection, cache["num_strands"],
            inputs=[
                cache["guides_wp"], session["active_strand_wp"], session["pinned_strand_wp"],
                session["strand_hero_k_wp"], session["strand_hero_offset_wp"],
                session["strand_surface_target_wp"], session["strand_brush_w_wp"],
                cache["strand_hero_k_wp"], cache["strand_hero_offset_wp"],
                cache["strand_surface_target_wp"], cache["strand_brush_w_wp"],
                cache["selection_counts"], cache["guide_starts"], cache["guide_counts"],
                cache["active_guides"], cache["active_edges"],
            ],
            device=device,
        )
        wp.launch(
            compact_follower_selection, session["num_strands"],
            inputs=[
                session["vert_starts_wp"], session["counts_wp"], session["active_strand_wp"],
                session["pinned_strand_wp"], cache["guide_of_strand"],
                cache["guides_wp"], cache["bindings"], cache["weights"], cache["selection_counts"],
                cache["active_points"], cache["active_followers"],
            ],
            device=device,
        )
        guide_count, point_count, follower_count, edge_count = cache["selection_counts"].numpy()
        cache["stroke_ready"] = bool(guide_count)
        cache["active_guide_count"] = int(guide_count)
        cache["active_edge_count"] = int(edge_count)
        cache["active_point_count"] = int(point_count)
        cache["active_follower_count"] = int(follower_count)
        if cache["stroke_ready"]:
            wp.copy(cache["full_rest"], points)
            # The guard also reads each follower's root, which the
            # reconstruction kernel never changes or snapshots.
            wp.copy(cache["previous_sample"], points)
            wp.launch(gather_points, size, inputs=[points, indices, guide_points], device=device)
            wp.copy(cache["anim_rest_wp"], guide_points)
            cache["update_contact_bounds"](False)
            cache["twist_angles"].zero_()
            cache["edge_dual"].zero_()
            cache["bend_dual"].zero_()
            cache["rod_factor_state"].zero_()
            wp.launch(
                transport_frames, cache["active_guide_count"],
                inputs=[
                    cache["active_guides"],
                    cache["anim_rest_wp"], cache["guide_starts"], cache["guide_counts"],
                    cache["rest_tangents"], cache["rest_directors"], cache["twist_angles"], 0,
                    cache["rest_tangents"], cache["rest_directors"], cache["rest_bends"],
                    cache["bend_left"], cache["rest_lengths"],
                ],
                device=device,
            )
            cache["vel_wp"].zero_()
            wp.launch(
                gather_inertia, cache["num_strands"],
                inputs=[
                    session["stroke_arc_lengths_wp"], session["brush_inv_mass_wp"], indices,
                    cache["guide_starts"], cache["guide_counts"], cache["cum_len_wp"], cache["inv_mass_wp"],
                    cache["twist_factors"], cache["twist_stiffness"],
                ],
                device=device,
            )
    if not cache["stroke_ready"]:
        return False

    events = None
    if timing_events is not None and points.device.is_cuda:
        events = cache.get("timing_events")
        if events is None:
            events = tuple(wp.Event(device=device, enable_timing=True) for _ in range(3))
            cache["timing_events"] = events
        stream = wp.get_stream(device)
        stream.record_event(events[0])

    wp.copy(cache["previous_guides"], guide_points)
    session["step_fn"](
        (lambda: apply_brush(cache)) if apply_brush is not None else None,
        use_volume=False,
        solve_state=cache,
        dynamic_guides=dynamic_guides,
        self_collision=self_collision,
        zero_gravity=zero_gravity,
        bend_scale=bend_scale,
        collider_override=collider_override,
    )
    collider = collider_override if collider_override is not None else session.get("wp_collider")
    coll_id_val = collider.id if collider is not None else 0

    def _run_post_solve():
        wp.launch(
            bound_sample, cache["active_guide_count"],
            inputs=[
                cache["previous_guides"], cache["anim_rest_wp"], guide_points,
                cache["guide_starts"], cache["guide_counts"], cache["inv_mass_wp"], cache["cum_len_wp"],
                cache["active_guides"], 1, coll_id_val, int(collider is not None), 1e-5 * session["unit_scale"],
            ],
            device=device,
        )
        if events is not None:
            stream.record_event(events[1])
        wp.launch(
            transport_frames, cache["active_guide_count"],
            inputs=[
                cache["active_guides"],
                guide_points, cache["guide_starts"], cache["guide_counts"],
                cache["rest_tangents"], cache["rest_directors"], cache["twist_angles"], 1,
                cache["live_tangents"], cache["live_directors"], cache["rest_bends"],
                cache["bend_left"], cache["rest_lengths"],
            ],
            device=device,
        )
        wp.launch(
            deform_followers, cache["active_point_count"],
            inputs=[
                cache["active_points"], cache["full_rest"], cache["parameters"], session["strand_ids_wp"],
                session["vert_starts_wp"], cache["guide_of_strand"],
                cache["bindings"], cache["weights"], cache["guide_starts"], cache["inv_mass_wp"],
                cache["sample_segments"], cache["sample_fractions"], cache["anim_rest_wp"], guide_points,
                cache["rest_tangents"], cache["rest_directors"], cache["live_tangents"], cache["live_directors"],
                cache["previous_sample"], points, session["counts_wp"], int(vertex_stride),
            ],
            device=device,
        )
        if vertex_stride > 1 and cache["active_follower_count"] > 0:
            from hair_kernels import _build_subsample_kernels
            _, interp_kernel = _build_subsample_kernels(wp)
            wp.launch(
                interp_kernel, cache["active_follower_count"],
                inputs=[
                    points, cache["previous_sample"], session["vert_starts_wp"], session["counts_wp"],
                    cache["active_followers"], 1, int(vertex_stride), 0,
                ],
                device=device,
            )
        if cache["active_follower_count"]:
            wp.launch(
                bound_sample, cache["active_follower_count"],
                inputs=[
                    cache["previous_sample"], cache["full_rest"], points,
                    session["vert_starts_wp"], session["counts_wp"], session["brush_inv_mass_wp"], session["stroke_arc_lengths_wp"],
                    cache["active_followers"], 1, collider.id if collider is not None else 0,
                    int(collider is not None), 1e-5 * session["unit_scale"],
                ],
                device=device,
            )

    if points.device.is_cuda and events is None:
        if cache.get("last_vertex_stride") != int(vertex_stride) or cache.get("last_collider_id") != coll_id_val:
            cache["post_solve_cuda_graph"] = None
            cache["last_vertex_stride"] = int(vertex_stride)
            cache["last_collider_id"] = coll_id_val
        post_graph = cache.get("post_solve_cuda_graph")
        if post_graph is None:
            with wp.ScopedDevice(device):
                wp.capture_begin()
                _run_post_solve()
                post_graph = wp.capture_end()
            cache["post_solve_cuda_graph"] = post_graph
        wp.capture_launch(post_graph)
    else:
        _run_post_solve()
    if events is not None:
        stream.record_event(events[2])
        timing_events.append(events)
    return True


def init_sim_guide_cache(
    wp, device, rest_world_np, counts_np, widths_np, unit_scale, local_to_world,
    friction, bend_stiffness, strand_ids_wp, inv_mass_wp, cum_len_wp, full_pos_wp,
):
    """Initialize a persistent ADMM DER + contact guide cache for full simulation."""
    diameter = 0.00015 * unit_scale
    if widths_np is not None and np.any(widths_np > 0):
        transform = np.asarray(local_to_world, dtype=np.float32)
        scale = float(np.max(np.linalg.norm(transform[:3, :3], axis=1)))
        diameter = float(np.median(widths_np[widths_np > 0])) * scale
    cache = create_guide_buffers(wp, device, rest_world_np, counts_np, diameter, friction, bend_stiffness)
    cache["kernels"] = build_guide_kernels(wp)
    from hair_kernels import _build_kernels
    cache["enforce_length_kernel"] = _build_kernels(wp)[27]
    cache["full_counts_wp"] = wp.array(np.asarray(counts_np, dtype=np.int32), dtype=int, device=device)
    cache["full_inv_mass_wp"] = inv_mass_wp
    cache["full_cum_len_wp"] = cum_len_wp
    cache["full_num_strands"] = int(len(counts_np))
    cache["full_rest"] = wp.empty_like(full_pos_wp)
    wp.copy(cache["full_rest"], full_pos_wp)
    cache["previous_sample"] = wp.empty_like(full_pos_wp)
    cache["previous_guides"] = wp.empty_like(cache["pos_wp"])

    gather_points, gather_inertia, _, transport_frames, bind_follower_samples, deform_followers, _, _ = cache["kernels"]
    wp.launch(
        bind_follower_samples, cache["sample_segments"].shape,
        inputs=[
            cache["parameters"], strand_ids_wp, cache["guide_of_strand"],
            cache["bindings"], cache["guide_starts"], cache["guide_counts"],
            cache["guide_parameters"], cache["strand_lengths_wp"], cache["guide_lengths_wp"],
            cache["sample_segments"], cache["sample_fractions"],
        ],
        device=device,
    )
    num_guides = cache["num_strands"]
    n_edges = cache["n_segs"]
    total_full = len(rest_world_np)
    cache["active_guides"].assign(np.arange(num_guides, dtype=np.int32))
    cache["active_edges"].assign(np.arange(n_edges, dtype=np.int32))
    cache["active_points"].assign(np.arange(total_full, dtype=np.int32))
    cache["active_guide_count"] = int(num_guides)
    cache["active_edge_count"] = int(n_edges)
    cache["active_point_count"] = int(total_full)
    cache["active_follower_count"] = 0

    guide_points = cache["pos_wp"]
    wp.launch(gather_points, cache["total_verts"], inputs=[full_pos_wp, cache["indices"], guide_points], device=device)
    wp.copy(cache["anim_rest_wp"], guide_points)
    wp.copy(cache["prev_pos_wp"], guide_points)
    cache["update_contact_bounds"](False)
    cache["twist_angles"].zero_()
    cache["edge_dual"].zero_()
    cache["bend_dual"].zero_()
    cache["rod_factor_state"].zero_()
    cache["strand_hero_k_wp"].fill_(-1)
    cache["strand_brush_w_wp"].zero_()
    wp.launch(
        transport_frames, num_guides,
        inputs=[
            cache["active_guides"],
            cache["anim_rest_wp"], cache["guide_starts"], cache["guide_counts"],
            cache["rest_tangents"], cache["rest_directors"], cache["twist_angles"], 0,
            cache["rest_tangents"], cache["rest_directors"], cache["rest_bends"],
            cache["bend_left"], cache["rest_lengths"],
        ],
        device=device,
    )
    wp.launch(
        gather_inertia, num_guides,
        inputs=[
            cum_len_wp, inv_mass_wp, cache["indices"],
            cache["guide_starts"], cache["guide_counts"], cache["cum_len_wp"], cache["inv_mass_wp"],
            cache["twist_factors"], cache["twist_stiffness"],
        ],
        device=device,
    )
    return cache


def step_sim_guides(wp, device, cache, step_fn, strand_ids_wp, vert_starts_wp, full_pos_wp, dynamic_guides=True, collider_override=None):
    """Advance one frame with ADMM DER + contacts on guides and reconstruct all strands."""
    gather_points, _, _, transport_frames, _, deform_followers, _, _ = cache["kernels"]
    guide_points = cache["pos_wp"]
    coll_id_val = collider_override.id if collider_override is not None else 0
    if cache.get("last_sim_collider_id") != coll_id_val:
        cache["sim_cuda_graph"] = None
        cache["last_sim_collider_id"] = coll_id_val
    if cache.pop("sync_from_full", False):
        wp.launch(gather_points, cache["total_verts"], inputs=[full_pos_wp, cache["indices"], guide_points], device=device)
        wp.copy(cache["prev_pos_wp"], guide_points)
        wp.copy(cache["anim_rest_wp"], guide_points)
        wp.copy(cache["full_rest"], full_pos_wp)
        cache["vel_wp"].zero_()
        wp.launch(
            transport_frames, cache["active_guide_count"],
            inputs=[
                cache["active_guides"],
                cache["anim_rest_wp"], cache["guide_starts"], cache["guide_counts"],
                cache["rest_tangents"], cache["rest_directors"], cache["twist_angles"], 0,
                cache["rest_tangents"], cache["rest_directors"], cache["rest_bends"],
                cache["bend_left"], cache["rest_lengths"],
            ],
            device=device,
        )
        cache["sim_cuda_graph"] = None

    def _run_step():
        step_fn(None, use_volume=False, solve_state=cache, dynamic_guides=dynamic_guides, self_collision=False, collider_override=collider_override)
        wp.launch(
            transport_frames, cache["active_guide_count"],
            inputs=[
                cache["active_guides"],
                guide_points, cache["guide_starts"], cache["guide_counts"],
                cache["rest_tangents"], cache["rest_directors"], cache["twist_angles"], 1,
                cache["live_tangents"], cache["live_directors"], cache["rest_bends"],
                cache["bend_left"], cache["rest_lengths"],
            ],
            device=device,
        )
        wp.launch(
            deform_followers, cache["active_point_count"],
            inputs=[
                cache["active_points"], cache["full_rest"], cache["parameters"], strand_ids_wp,
                vert_starts_wp, cache["guide_of_strand"],
                cache["bindings"], cache["weights"], cache["guide_starts"], cache["inv_mass_wp"],
                cache["sample_segments"], cache["sample_fractions"], cache["anim_rest_wp"], guide_points,
                cache["rest_tangents"], cache["rest_directors"], cache["live_tangents"], cache["live_directors"],
                cache["previous_sample"], full_pos_wp, cache["full_counts_wp"], 1,
            ],
            device=device,
        )
        enforce_len = cache.get("enforce_length_kernel")
        if enforce_len is not None and cache.get("full_num_strands", 0) > 0:
            wp.launch(
                enforce_len,
                dim=cache["full_num_strands"],
                inputs=[
                    full_pos_wp,
                    cache["full_inv_mass_wp"],
                    vert_starts_wp,
                    cache["full_counts_wp"],
                    cache["full_cum_len_wp"],
                    1.01,
                ],
                device=device,
            )

    if full_pos_wp.device.is_cuda:
        sim_graph = cache.get("sim_cuda_graph")
        if sim_graph is None:
            with wp.ScopedDevice(device):
                wp.capture_begin()
                _run_step()
                sim_graph = wp.capture_end()
            cache["sim_cuda_graph"] = sim_graph
        wp.capture_launch(sim_graph)
    else:
        _run_step()
