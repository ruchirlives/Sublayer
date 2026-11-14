"""Shared USD viewport geometry bounds and selection framing helpers."""

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
    session.Clear()
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


