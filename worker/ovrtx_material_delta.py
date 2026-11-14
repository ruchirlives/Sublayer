"""Incremental material delta planning for the NVIDIA ovrtx preview.

The ovstage population keeps the geometry that was opened from a flattened
preview snapshot (the *base*). When a new flattened scene differs from that base
only in material networks or material bindings, the difference is authored as a
small anonymous delta layer. The worker adds it as a USD reference on the live
ovstage (and removes the previous delta), so the renderer, stage, geometry and
camera all stay alive and only the affected material resources are rebuilt.

``plan`` fails closed: it returns ``None`` for anything it cannot prove is
material-only (geometry, transforms, removed specs, stage metadata, ...), and
the caller then performs the existing full rebuild.
"""
from pxr import Sdf

MATERIAL_ROOT_TYPES = ("Material", "NodeGraph")
MATERIAL_TYPES = ("Material", "NodeGraph", "Shader")
ADDED_CONTAINER_TYPES = MATERIAL_TYPES + ("Scope", "")
IGNORED_KEYS = {"primChildren", "properties", "primOrder", "propertyOrder", "customLayerData"}
RENDER_ROOT = Sdf.Path("/Render")  # camera/product/lights are managed separately


def _collect(layer):
    prims, props = set(), set()

    def visit(path):
        if path == Sdf.Path.absoluteRootPath:
            return
        if path.IsPrimPath():
            if not path.HasPrefix(RENDER_ROOT):
                prims.add(path)
        elif path.IsPropertyPath():
            if not path.GetPrimPath().HasPrefix(RENDER_ROOT):
                props.add(path)

    layer.Traverse(Sdf.Path.absoluteRootPath, visit)
    return prims, props


def _infos(spec):
    return {key: spec.GetInfo(key) for key in spec.ListInfoKeys() if key not in IGNORED_KEYS}


def _differs(a, b):
    try:
        return _infos(a) != _infos(b)
    except Exception:
        return True


def _info_keys_changed(a, b):
    ia, ib = _infos(a), _infos(b)
    return {k for k in set(ia) | set(ib) if k not in ia or k not in ib or ia[k] != ib[k]}


def _type(layer, path):
    spec = layer.GetPrimAtPath(path)
    return str(spec.typeName) if spec else ""


def _material_root(layer, path):
    """Topmost Material/NodeGraph at or above ``path`` (or a lone Shader), else None."""
    prim = path if path.IsPrimPath() else path.GetPrimPath()
    chain, current = [], prim
    while current.IsPrimPath():
        chain.append(current)
        current = current.GetParentPath()
    for candidate in reversed(chain):  # outermost first
        if _type(layer, candidate) in MATERIAL_ROOT_TYPES:
            return candidate
    return prim if _type(layer, prim) == "Shader" else None


LIGHT_PROPS = {
    "inputs:intensity", "inputs:exposure", "inputs:color",
    "inputs:enableColorTemperature", "inputs:colorTemperature",
    "intensity", "exposure", "color", "enableColorTemperature", "colorTemperature"
}


def _is_light_property(layer, path):
    if not path.IsPropertyPath():
        return False
    if path.name in LIGHT_PROPS:
        return True
    prim_type = _type(layer, path.GetPrimPath())
    return "Light" in prim_type


def _is_binding(path):
    return path.IsPropertyPath() and path.name.startswith("material:binding")


def _is_transform_prop(path):
    return path.IsPropertyPath() and (path.name == "xformOpOrder" or path.name.startswith("xformOp:"))


def plan(base, new):
    """Return an anonymous delta layer, or None when a full rebuild is required.

    The delta is expressed against ``base`` (what ovstage currently holds as its
    stable population), so applying it repeatedly is idempotent.
    """
    if _differs(base.pseudoRoot, new.pseudoRoot):
        return None
    base_prims, base_props = _collect(base)
    new_prims, new_props = _collect(new)
    removed_prims, removed_props = base_prims - new_prims, base_props - new_props
    # Material prim names carry a content hash, so editing a material replaces the old prim
    # with a new one. A reference cannot delete opinions, but a whole material/scope subtree
    # that is no longer bound simply stays in ovstage as an inert orphan until the next full
    # rebuild. Any other removal (geometry, bindings, single properties) needs a rebuild.
    removed_roots = {p for p in removed_prims if p.GetParentPath() not in removed_prims}
    for root in removed_roots:
        for p in base_prims:
            if p.HasPrefix(root) and _type(base, p) not in ADDED_CONTAINER_TYPES:
                return None
    copy_roots, prop_copies, info_copies = set(), set(), []
    redirect_bindings, reset_xform_prims = [], set()

    def need_material(path):
        root = _material_root(new, path)
        if root is None:
            return False
        copy_roots.add(root)
        return True

    for prop in removed_props:
        prim_path = prop.GetPrimPath()
        if any(prim_path.HasPrefix(root) for root in removed_roots):
            continue
        if need_material(prop):
            continue
        if _is_binding(prop):
            # ovstage.population.add_usd_reference does not reliably rebind existing
            # meshes when material:binding changes on an already-populated stage.
            return None
        if _is_transform_prop(prop):
            if new.GetPrimAtPath(prim_path) is not None:
                if new.GetPropertyAtPath(prim_path.AppendProperty("xformOpOrder")) is None:
                    reset_xform_prims.add(prim_path)
                continue
            return None
        return None

    for path in new_prims - base_prims:
        if (path.GetParentPath() in new_prims - base_prims):
            continue  # covered by its added ancestor
        subtree = [p for p in new_prims if p.HasPrefix(path)]
        for p in subtree:
            spec = new.GetPrimAtPath(p)
            if str(spec.typeName) not in ADDED_CONTAINER_TYPES or spec.specifier != Sdf.SpecifierDef:
                return None
        copy_roots.add(path)

    for path in new_prims & base_prims:
        a, b = base.GetPrimAtPath(path), new.GetPrimAtPath(path)
        if not _differs(a, b):
            continue
        if need_material(path):
            continue
        if _info_keys_changed(a, b) <= {"apiSchemas"}:
            info_copies.append((path, "apiSchemas"))
            continue
        return None

    for path in new_props - base_props:
        if need_material(path):
            continue
        if _is_binding(path):
            return None
        if _is_light_property(new, path) or _is_transform_prop(path):
            prop_copies.add(path)
            continue
        if path.GetPrimPath() in copy_roots or any(path.GetPrimPath().HasPrefix(r) for r in copy_roots):
            continue
        return None

    for path in new_props & base_props:
        if not _differs(base.GetObjectAtPath(path), new.GetObjectAtPath(path)):
            continue
        if need_material(path):
            continue
        if _is_binding(path):
            return None
        if _is_light_property(new, path) or _is_transform_prop(path):
            prop_copies.add(path)
            continue
        return None

    roots = {r for r in copy_roots if not any(o != r and r.HasPrefix(o) for o in copy_roots)}
    delta = Sdf.Layer.CreateAnonymous("ovrtx-material-delta.usda")

    def ensure(path):
        # Ancestors are authored as untyped `def` (not `over`): ovstage's reference merge
        # populates top-level defs, and an untyped def composes with the base prim's type.
        if path != Sdf.Path.absoluteRootPath and not delta.GetPrimAtPath(path):
            ensure(path.GetParentPath())
            Sdf.PrimSpec(delta.GetPrimAtPath(path.GetParentPath()) if path.GetParentPath() != Sdf.Path.absoluteRootPath
                         else delta, path.name, Sdf.SpecifierDef)

    for root in sorted(roots):
        ensure(root.GetParentPath())
        if not Sdf.CopySpec(new, root, delta, root):
            return None
    for path in sorted(prop_copies):
        if any(path.GetPrimPath().HasPrefix(r) for r in roots):
            continue
        ensure(path.GetPrimPath())
        if not Sdf.CopySpec(new, path, delta, path):
            return None
    for path, key in info_copies:
        if any(path.HasPrefix(r) for r in roots):
            continue
        ensure(path)
        delta.GetPrimAtPath(path).SetInfo(key, new.GetPrimAtPath(path).GetInfo(key))
    for target_prop, src_binding_prop in redirect_bindings:
        prim_path = target_prop.GetPrimPath()
        if any(prim_path.HasPrefix(r) for r in roots):
            continue
        ensure(prim_path)
        if not Sdf.CopySpec(new, src_binding_prop, delta, target_prop):
            return None
    for prim_path in sorted(reset_xform_prims):
        if any(prim_path.HasPrefix(r) for r in roots):
            continue
        ensure(prim_path)
        prim_spec = delta.GetPrimAtPath(prim_path)
        if not prim_spec.properties.get("xformOpOrder"):
            attr = Sdf.AttributeSpec(prim_spec, "xformOpOrder", Sdf.ValueTypeNames.TokenArray, False, Sdf.VariabilityUniform)
            attr.default = Sdf.TokenArray([])
    return delta
