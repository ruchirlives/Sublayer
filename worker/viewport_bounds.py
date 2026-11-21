"""Shared USD viewport geometry bounds and selection framing helpers."""

import math


def transform_gizmo_translation(graph, node_id=None):
    """Resolve the active gizmo in the evaluated graph, including pinned previews."""
    if not isinstance(graph, dict):
        return None, None
    target_id = node_id if node_id is not None else graph.get("targetNodeId")
    if not target_id:
        return None, None
    for node in graph.get("nodes") or []:
        if isinstance(node, dict) and node.get("id") == target_id and node.get("operation") == "transform":
            translation = (node.get("transform") or {}).get("translation")
            if isinstance(translation, list) and len(translation) == 3 and all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                for v in translation
            ):
                return target_id, [float(v) for v in translation]
            return target_id, [0.0, 0.0, 0.0]
    return None, None

def compute_visible_geometry_bounds(stage, time_code):
    """Compute aligned bounding box for all visible renderable geometry.
    
    Excludes synthetic helper prims under /Render (cameras, render products,
    lights) so framing focuses strictly on actual user assets.
    """
    from pxr import Gf, Sdf, Usd, UsdGeom
    bbox_cache = UsdGeom.BBoxCache(
        time_code,
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy]
    )
    total_range = Gf.Range3d()
    found_geometry = False
    render_path = Sdf.Path("/Render")
    for prim in stage.Traverse():
        if prim.GetPath().HasPrefix(render_path):
            continue
        if prim.IsA(UsdGeom.Gprim) or prim.IsA(UsdGeom.PointInstancer):
            img = UsdGeom.Imageable(prim)
            if img.ComputeVisibility(time_code) != UsdGeom.Tokens.invisible:
                prim_range = bbox_cache.ComputeWorldBound(prim).ComputeAlignedRange()
                if not prim_range.IsEmpty():
                    total_range.UnionWith(prim_range)
                    found_geometry = True
    if not found_geometry:
        for child in stage.GetPseudoRoot().GetChildren():
            if child.GetName() != "Render":
                r = bbox_cache.ComputeWorldBound(child).ComputeAlignedRange()
                if not r.IsEmpty():
                    total_range.UnionWith(r)
    return total_range

def compute_selection_bounds(stage, selection_paths, time_code):
    """Compute aligned bounding box for the specified selected prim paths."""
    if not selection_paths:
        return None
    from pxr import Gf, Sdf, Usd, UsdGeom
    bbox_cache = UsdGeom.BBoxCache(
        time_code,
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy]
    )
    total_range = Gf.Range3d()
    found = False
    for p in selection_paths:
        if not p:
            continue
        prim = stage.GetPrimAtPath(Sdf.Path(str(p)))
        if prim:
            r = bbox_cache.ComputeWorldBound(prim).ComputeAlignedRange()
            if not r.IsEmpty():
                total_range.UnionWith(r)
                found = True
    return total_range if found else None


def apply_viewport_visibility(stage, isolated_path_str, hidden_paths=None):
    """Apply viewport-only isolation and hidden-prim visibility on the session layer."""
    from pxr import Sdf, Usd, UsdGeom

    isolate_prim_in_stage(stage, isolated_path_str)
    if not hidden_paths:
        return
    session = stage.GetSessionLayer()
    with Usd.EditContext(stage, session):
        for raw_path in hidden_paths:
            try:
                path = Sdf.Path(str(raw_path))
            except Exception:
                continue
            if not path.IsPrimPath():
                continue
            prim = stage.GetPrimAtPath(path)
            if not prim or not prim.IsValid():
                continue
            if prim.IsInstanceProxy():
                parent = path.GetParentPath()
                while parent != Sdf.Path.absoluteRootPath:
                    parent_prim = stage.GetPrimAtPath(parent)
                    if parent_prim and not parent_prim.IsInstanceProxy():
                        prim = parent_prim
                        break
                    parent = parent.GetParentPath()
            if prim and prim.IsValid() and not prim.IsInstanceProxy() and prim.IsA(UsdGeom.Imageable):
                UsdGeom.Imageable(prim).CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)


def isolate_prim_in_stage(stage, isolated_path_str):
    """Isolate the given prim path in the stage, keeping it, its descendants,
    all lights, and /Render visible, while setting other sibling branches to invisible.
    Authors on the session layer so underlying stage opinions and external files are preserved.
    If isolated_path_str is None or empty, clears the session layer.
    """
    from pxr import Sdf, Usd, UsdGeom, UsdLux

    session = stage.GetSessionLayer()

    def _clear_vis(prim_spec):
        if 'visibility' in prim_spec.properties:
            prim_spec.RemoveProperty(prim_spec.properties['visibility'])
        for child in list(prim_spec.nameChildren):
            _clear_vis(child)

    for root_spec in list(session.rootPrims):
        _clear_vis(root_spec)

    if not isolated_path_str:
        return

    try:
        isolated_path = Sdf.Path(str(isolated_path_str))
    except Exception:
        return
    if not isolated_path.IsPrimPath():
        return
    prim = stage.GetPrimAtPath(isolated_path)
    if not prim or not prim.IsValid():
        return

    # If the user selected an instance proxy, map to the nearest instance root
    if prim.IsInstanceProxy():
        p = isolated_path.GetParentPath()
        while p != Sdf.Path.absoluteRootPath:
            parent_prim = stage.GetPrimAtPath(p)
            if parent_prim and not parent_prim.IsInstanceProxy():
                isolated_path = p
                break
            p = p.GetParentPath()

    keep_ancestors = set()
    render_root = Sdf.Path('/Render')

    curr = isolated_path
    while curr != Sdf.Path.absoluteRootPath:
        keep_ancestors.add(curr)
        curr = curr.GetParentPath()

    for p in stage.Traverse(Usd.TraverseInstanceProxies()):
        path = p.GetPath()
        if path.HasPrefix(render_root):
            curr = path
            while curr != Sdf.Path.absoluteRootPath:
                keep_ancestors.add(curr)
                curr = curr.GetParentPath()
            continue

        type_name = p.GetTypeName()
        is_light = (
            p.HasAPI(UsdLux.LightAPI) or
            type_name.endswith('Light') or
            'Light' in type_name
        )
        if is_light:
            curr = path
            while curr != Sdf.Path.absoluteRootPath:
                keep_ancestors.add(curr)
                curr = curr.GetParentPath()

    with Usd.EditContext(stage, session):
        def _apply(item):
            if item.IsInstanceProxy():
                return
            path = item.GetPath()
            if path in keep_ancestors:
                if item.IsA(UsdGeom.Imageable):
                    UsdGeom.Imageable(item).CreateVisibilityAttr().Set(UsdGeom.Tokens.inherited)
                if path == isolated_path:
                    return
                for child in item.GetFilteredChildren(Usd.TraverseInstanceProxies()):
                    _apply(child)
            else:
                if item.IsA(UsdGeom.Imageable):
                    UsdGeom.Imageable(item).CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)
                else:
                    for child in item.GetFilteredChildren(Usd.TraverseInstanceProxies()):
                        _apply(child)

        for child in stage.GetPseudoRoot().GetChildren():
            _apply(child)


def raycast_prim_hit(stage, prim_path, ray_origin, ray_dir, time_code):
    """Return closest hit dict {point, meshPath, faceIndex} on prim_path (or descendants), or None."""
    if not stage or not prim_path:
        return None
    import numpy as np
    from pxr import Gf, Sdf, Usd, UsdGeom

    root_prim = stage.GetPrimAtPath(Sdf.Path(str(prim_path)))
    if not root_prim or not root_prim.IsValid():
        return None

    ro_w = Gf.Vec3d(float(ray_origin[0]), float(ray_origin[1]), float(ray_origin[2]))
    rd_w = Gf.Vec3d(float(ray_dir[0]), float(ray_dir[1]), float(ray_dir[2])).GetNormalized()
    xform_cache = UsdGeom.XformCache(time_code)

    best_t = float("inf")
    best_hit = None

    for p in Usd.PrimRange(root_prim, Usd.TraverseInstanceProxies()):
        if p.IsA(UsdGeom.Mesh):
            mesh = UsdGeom.Mesh(p)
            pts_vt = mesh.GetPointsAttr().Get(time_code)
            counts_vt = mesh.GetFaceVertexCountsAttr().Get(time_code)
            indices_vt = mesh.GetFaceVertexIndicesAttr().Get(time_code)
            if not pts_vt or not counts_vt or not indices_vt:
                continue
            pts = np.asarray(pts_vt, dtype=np.float64)
            counts = np.asarray(counts_vt, dtype=np.int32)
            indices = np.asarray(indices_vt, dtype=np.int32)
            # Fan-triangulate faces while tracking original face index
            tris = []
            tri_faces = []
            offset = 0
            for face_idx, c in enumerate(counts):
                if c >= 3 and offset + c <= len(indices):
                    i0 = indices[offset]
                    for k in range(1, c - 1):
                        tris.append((i0, indices[offset + k], indices[offset + k + 1]))
                        tri_faces.append(face_idx)
                offset += int(c)
            if not tris:
                continue
            tri_arr = np.asarray(tris, dtype=np.int32)

            l2w = xform_cache.GetLocalToWorldTransform(p)
            w2l = l2w.GetInverse()
            ro_l = w2l.Transform(ro_w)
            rd_l = w2l.TransformDir(rd_w)
            ro = np.array([ro_l[0], ro_l[1], ro_l[2]], dtype=np.float64)
            rd = np.array([rd_l[0], rd_l[1], rd_l[2]], dtype=np.float64)

            v0 = pts[tri_arr[:, 0]]
            v1 = pts[tri_arr[:, 1]]
            v2 = pts[tri_arr[:, 2]]
            e1 = v1 - v0
            e2 = v2 - v0
            h = np.cross(rd[None, :], e2)
            a = np.einsum("ij,ij->i", e1, h)
            valid = np.abs(a) > 1e-10
            if not np.any(valid):
                continue
            f = np.zeros_like(a)
            f[valid] = 1.0 / a[valid]
            s = ro[None, :] - v0
            u = f * np.einsum("ij,ij->i", s, h)
            valid &= (u >= -1e-5) & (u <= 1.0 + 1e-5)
            if not np.any(valid):
                continue
            q = np.cross(s, e1)
            v = f * np.einsum("ij,ij->i", rd[None, :], q)
            valid &= (v >= -1e-5) & (u + v <= 1.0 + 1e-5)
            if not np.any(valid):
                continue
            t = f * np.einsum("ij,ij->i", e2, q)
            valid &= t > 1e-5
            if not np.any(valid):
                continue
            valid_indices = np.flatnonzero(valid)
            best_local = int(valid_indices[int(np.argmin(t[valid_indices]))])
            t_min = float(t[best_local])
            hit_l = ro + rd * t_min
            hit_w = l2w.Transform(Gf.Vec3d(float(hit_l[0]), float(hit_l[1]), float(hit_l[2])))
            t_world = float(Gf.Dot(hit_w - ro_w, rd_w))
            if t_world > 1e-5 and t_world < best_t:
                best_t = t_world
                best_hit = {
                    "point": [float(hit_w[0]), float(hit_w[1]), float(hit_w[2])],
                    "meshPath": str(p.GetPath()),
                    "faceIndex": int(tri_faces[best_local]),
                }

        elif p.IsA(UsdGeom.BasisCurves):
            curves = UsdGeom.BasisCurves(p)
            pts_vt = curves.GetPointsAttr().Get(time_code)
            if not pts_vt:
                continue
            pts = np.asarray(pts_vt, dtype=np.float64)
            l2w = xform_cache.GetLocalToWorldTransform(p)
            w2l = l2w.GetInverse()
            ro_l = w2l.Transform(ro_w)
            rd_l = w2l.TransformDir(rd_w)
            rd_l_len = rd_l.GetLength()
            if rd_l_len <= 1e-8:
                continue
            rd_l_n = rd_l / rd_l_len
            ro = np.array([ro_l[0], ro_l[1], ro_l[2]], dtype=np.float64)
            rd = np.array([rd_l_n[0], rd_l_n[1], rd_l_n[2]], dtype=np.float64)
            rel = pts - ro[None, :]
            proj = rel @ rd
            mask = proj > 1e-5
            if not np.any(mask):
                continue
            perp = rel[mask] - proj[mask, None] * rd[None, :]
            dist2 = np.einsum("ij,ij->i", perp, perp)
            idx = int(np.argmin(dist2))
            hit_l = pts[mask][idx]
            hit_w = l2w.Transform(Gf.Vec3d(float(hit_l[0]), float(hit_l[1]), float(hit_l[2])))
            t_world = float(Gf.Dot(hit_w - ro_w, rd_w))
            if t_world > 1e-5 and t_world < best_t:
                best_t = t_world
                best_hit = {
                    "point": [float(hit_w[0]), float(hit_w[1]), float(hit_w[2])],
                    "meshPath": str(p.GetPath()),
                    "faceIndex": None,
                }

    return best_hit


def raycast_prim_world_point(stage, prim_path, ray_origin, ray_dir, time_code):
    """Return closest [x, y, z] world hit on prim_path (or its descendants) along ray, or None."""
    hit = raycast_prim_hit(stage, prim_path, ray_origin, ray_dir, time_code)
    return hit["point"] if hit else None



