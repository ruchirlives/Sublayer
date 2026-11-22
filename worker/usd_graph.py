def graph_digest(graph):
    # Rust serializes integral f64 values as 0.0 while JavaScript uses 0.
    # Graph identity must depend on values rather than JSON number spelling.
    import hashlib,json
    def canonical(value):
        if isinstance(value,float) and value.is_integer():return int(value)
        if isinstance(value,dict):return {k:canonical(v) for k,v in value.items()}
        if isinstance(value,list):return [canonical(v) for v in value]
        return value
    return hashlib.sha256(json.dumps(canonical(graph),sort_keys=True,separators=(',',':')).encode()).hexdigest()


"""Read-only USD graph evaluation. All generated layers are anonymous/in memory."""
import hashlib
import json
import os
import sys


def report_status(phase, details=None):
    try:
        payload = {"phase": phase}
        if details:
            payload["details"] = details
        sys.stdout.write("ORCHFLOW_STATUS:" + json.dumps(payload) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


_SOURCE_STAGE_CACHE = {}
_MAX_SOURCE_STAGE_CACHE = 16
_MULTI_ROOT_CACHE = {}
_MAX_MULTI_ROOT_CACHE = 16
_NODE_RESULT_CACHE = {}
_MAX_NODE_RESULT_CACHE = 32


def clear_graph_caches():
    _SOURCE_STAGE_CACHE.clear()
    _MULTI_ROOT_CACHE.clear()
    _NODE_RESULT_CACHE.clear()
    _BLENDER_FULL_LAYER_CACHE.clear()
    _BLENDER_STAGE_CACHE.clear()


def _disk_cache_dirs():
    import tempfile

    tmp = tempfile.gettempdir()
    return (
        os.path.join(tmp, 'sublayer_alembic_cache'),
        os.path.join(tmp, 'sublayer_blender_cache'),
        os.path.join(tmp, 'sublayer_project_cache'),
    )


def _touch_cache_file(path):
    try:
        os.utime(path, None)
    except OSError:
        pass


def _cleanup_stale_source_cache(cache_dir, path_hash, keep_names):
    """Remove older cached versions of the same source file."""
    prefix = f'{path_hash}_'
    try:
        entries = os.listdir(cache_dir)
    except OSError:
        return
    for entry in entries:
        if (
            entry.startswith(prefix)
            and entry not in keep_names
            and '.tmp' not in entry
            and entry.endswith(('.usdc', '.json'))
        ):
            try:
                os.remove(os.path.join(cache_dir, entry))
            except OSError:
                pass


def _prune_disk_caches(keep_paths=(), max_bytes=None, max_age_days=30):
    """Evict expired (>max_age_days) and oldest-used disk cache entries above max_bytes."""
    import time

    if max_bytes is None:
        try:
            max_mb = float(os.environ.get('ORCHFLOW_DISK_CACHE_MAX_MB', '4096'))
        except ValueError:
            max_mb = 4096.0
        max_bytes = max(0, int(max_mb * 1024 * 1024))

    keep = {os.path.normcase(os.path.abspath(p)) for p in keep_paths if p}
    cutoff = time.time() - (max_age_days * 86400) if max_age_days and max_age_days > 0 else None
    entries = []
    total_size = 0

    for d in _disk_cache_dirs():
        if not os.path.isdir(d):
            continue
        for root_dir, _dirs, files in os.walk(d):
            for name in files:
                if '.tmp' in name:
                    continue
                full = os.path.join(root_dir, name)
                norm = os.path.normcase(os.path.abspath(full))
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                if cutoff is not None and st.st_mtime < cutoff and norm not in keep:
                    try:
                        os.remove(full)
                    except OSError:
                        pass
                    continue
                total_size += st.st_size
                entries.append((st.st_mtime, st.st_size, full, norm))

    if total_size <= max_bytes:
        return

    entries.sort(key=lambda item: item[0])
    for _mtime, size, full, norm in entries:
        if total_size <= max_bytes:
            break
        if norm in keep:
            continue
        try:
            os.remove(full)
            total_size -= size
        except OSError:
            pass
        if full.endswith('.usdc'):
            paired_json = full[:-5] + '.json'
            if os.path.normcase(os.path.abspath(paired_json)) not in keep and os.path.isfile(paired_json):
                try:
                    j_size = os.path.getsize(paired_json)
                    os.remove(paired_json)
                    total_size -= j_size
                except OSError:
                    pass


def resolve_prim_material(stage, prim_path, hit_mesh_path=None, face_index=None):
    """Return the USD path of the material bound to ``prim_path`` (or hit face subset), or None."""
    if not stage or not prim_path:
        return None
    from pxr import Sdf, UsdGeom, UsdShade
    target_path = hit_mesh_path or prim_path
    prim = stage.GetPrimAtPath(Sdf.Path(target_path))
    if not prim or not prim.IsValid():
        prim = stage.GetPrimAtPath(Sdf.Path(prim_path))
    if not prim or not prim.IsValid():
        return None
    purposes = (UsdShade.Tokens.allPurpose, UsdShade.Tokens.preview, UsdShade.Tokens.full)
    if face_index is not None and prim.IsA(UsdGeom.Imageable):
        for subset in UsdGeom.Subset.GetAllGeomSubsets(UsdGeom.Imageable(prim)):
            elem_type = subset.GetElementTypeAttr().Get()
            if elem_type and elem_type != UsdGeom.Tokens.face:
                continue
            indices_vt = subset.GetIndicesAttr().Get()
            if indices_vt and int(face_index) in indices_vt:
                for purpose in purposes:
                    mat, _ = UsdShade.MaterialBindingAPI(subset.GetPrim()).ComputeBoundMaterial(purpose)
                    if mat and mat.GetPrim().IsValid():
                        return str(mat.GetPath())
    for purpose in purposes:
        mat, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial(purpose)
        if mat and mat.GetPrim().IsValid():
            return str(mat.GetPath())
    if prim.IsA(UsdGeom.Imageable):
        for subset in UsdGeom.Subset.GetAllGeomSubsets(UsdGeom.Imageable(prim)):
            for purpose in purposes:
                mat, _ = UsdShade.MaterialBindingAPI(subset.GetPrim()).ComputeBoundMaterial(purpose)
                if mat and mat.GetPrim().IsValid():
                    return str(mat.GetPath())
    return None


def compute_material_users(stage):
    from pxr import Usd, UsdGeom, UsdShade
    prims = []
    for count, prim in enumerate(stage.Traverse(Usd.TraverseInstanceProxies())):
        if count >= 100000:
            raise ValueError('Material selection exceeds the 100,000-prim limit; narrow the preview scene')
        if prim.IsA(UsdGeom.Mesh) or (prim.IsA(UsdGeom.Subset) and prim.GetParent().IsA(UsdGeom.Mesh)):
            prims.append(prim)
    users = {}
    for purpose in (UsdShade.Tokens.allPurpose, UsdShade.Tokens.preview, UsdShade.Tokens.full):
        materials, _ = UsdShade.MaterialBindingAPI.ComputeBoundMaterials(prims, purpose)
        for prim, material in zip(prims, materials):
            if not material:
                continue
            mesh = prim.GetParent() if prim.IsA(UsdGeom.Subset) else prim
            users.setdefault(str(material.GetPath()), set()).add(str(mesh.GetPath()))
    return users


def query_scene_children(stage, request):
    from pxr import Sdf, Usd
    path = Sdf.Path(request.get('parent', '/'))
    if not path.IsAbsolutePath() or not path.IsPrimPath() and path != Sdf.Path.absoluteRootPath:
        raise ValueError('Hierarchy parent must be an absolute prim path')
    parent = stage.GetPseudoRoot() if path == Sdf.Path.absoluteRootPath else stage.GetPrimAtPath(path)
    if not parent:
        raise ValueError('Scene element no longer exists')
    offset = int(request.get('offset', 0))
    if offset < 0:
        raise ValueError('Invalid hierarchy offset')
    search = request.get('search', '')
    if not isinstance(search, str) or len(search) > 512:
        raise ValueError('Invalid object search')
    if search:
        children = []
        needle = search.casefold()
        for count, prim in enumerate(stage.Traverse(Usd.TraverseInstanceProxies())):
            if count >= 100000:
                raise ValueError('Object search exceeds the 100,000-prim budget; narrow the scene')
            if needle not in str(prim.GetPath()).casefold():
                continue
            children.append(prim)
            if len(children) > offset + 500:
                break
    else:
        children = parent.GetFilteredChildren(Usd.TraverseInstanceProxies())
    rows = []
    for prim in children[offset:offset + 500]:
        rows.append({
            'path': str(prim.GetPath()),
            'name': prim.GetName(),
            'type': prim.GetTypeName() or 'Scope',
            'instance': prim.IsInstance(),
            'instanceProxy': prim.IsInstanceProxy(),
            'hasChildren': bool(prim.GetFilteredChildren(Usd.TraverseInstanceProxies())),
        })
    return {'children': rows, 'nextOffset': offset + 500 if offset + 500 < len(children) else None}


def inherit_stage_metrics(stage, upstream):
    """Layer-stack metadata is not inherited like prim opinions; author it on
    each private overlay root so graph outputs keep canonical axis/units/time."""
    from pxr import UsdGeom
    if not stage.GetRootLayer().anonymous:raise ValueError('Stage metrics may only be authored on private generated layers')
    UsdGeom.SetStageUpAxis(stage,UsdGeom.GetStageUpAxis(upstream))
    UsdGeom.SetStageMetersPerUnit(stage,UsdGeom.GetStageMetersPerUnit(upstream))
    for setter,getter in [('SetStartTimeCode','GetStartTimeCode'),('SetEndTimeCode','GetEndTimeCode'),('SetTimeCodesPerSecond','GetTimeCodesPerSecond'),('SetFramesPerSecond','GetFramesPerSecond')]:
        getattr(stage,setter)(getattr(upstream,getter)())


def _build_transform_matrix(transform):
    import math
    from pxr import Gf, Usd, UsdGeom
    transform = transform or {}
    values = {}
    for field, default in [('translation', [0, 0, 0]), ('rotation', [0, 0, 0]), ('scale', [1, 1, 1])]:
        value = transform.get(field, default)
        if not isinstance(value, list) or len(value) != 3 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
            raise ValueError('Invalid transform ' + field)
        values[field] = value
    temporary = Usd.Stage.CreateInMemory()
    form = UsdGeom.XformCommonAPI(UsdGeom.Xform.Define(temporary, '/Transform'))
    form.SetTranslate(Gf.Vec3d(*values['translation']))
    form.SetRotate(Gf.Vec3f(*values['rotation']), UsdGeom.XformCommonAPI.RotationOrderXYZ)
    form.SetScale(Gf.Vec3f(*values['scale']))
    prim = temporary.GetDefaultPrim() if temporary.GetDefaultPrim() else temporary.GetPrimAtPath('/Transform')
    return UsdGeom.Xformable(prim).GetLocalTransformation()


def apply_transform(stage, node, default_target=None, live_ops=None):
    from pxr import UsdGeom
    matrix = _build_transform_matrix(node.get('transform'))
    label = 'node_' + hashlib.sha256(str(node.get('id', '')).encode()).hexdigest()[:16]
    targets = node.get('transformTargets')
    def author(prim):
        attr = prepend_root_transform(prim, matrix, label, keep_identity=live_ops is not None)
        if live_ops is not None:
            live_ops.append(attr)

    if not targets:
        if default_target is not None and hasattr(default_target, 'GetPrim'):
            default_target = default_target.GetPrim()
        if default_target and default_target.IsValid():
            author(default_target)
        return
    patterns = [
        str(row.get('pattern') or '').strip()
        for row in targets
        if isinstance(row, dict) and row.get('enabled', True) and str(row.get('pattern') or '').strip()
    ]
    if not patterns:
        return
    matched_paths = []
    matched_prims = []
    for prim in stage.Traverse():
        if prim.IsInstanceProxy() or not UsdGeom.Xformable(prim):
            continue
        path = prim.GetPath()
        path_str = str(path)
        if any(_hair_query_matches(pattern, path_str) for pattern in patterns):
            matched_paths.append(path)
            matched_prims.append(prim)
    matched_set = set(matched_paths)
    for path, prim in zip(matched_paths, matched_prims):
        if any(parent in matched_set for parent in path.GetPrefixes()[:-1]):
            continue
        author(prim)


def prepend_root_transform(prim,matrix,label,*,keep_identity=False):
    """Apply an outer transform on an existing scene root without a parent prim."""
    from pxr import UsdGeom,Sdf,Gf
    if not keep_identity and matrix==Gf.Matrix4d(1):return None
    form=UsdGeom.Xformable(prim)
    order=[str(value) for value in (form.GetXformOpOrderAttr().Get() or [])]
    name='xformOp:transform:'+label
    while prim.GetAttribute(name):name+='_' 
    attr=prim.CreateAttribute(name,Sdf.ValueTypeNames.Matrix4d,custom=False);attr.Set(matrix)
    reset=['!resetXformStack!'] if '!resetXformStack!' in order else []
    form.GetXformOpOrderAttr().Set(reset+[name]+[value for value in order if value!='!resetXformStack!'])
    return attr


def asset_key(path):
    return os.path.normcase(os.path.realpath(path)) if not path.startswith('anon:') else path


def asset_override_probe(layer):
    """Read asset/composition opinions without copying numeric geometry arrays.

    Sparse reference replacement is supported for file-backed layers without
    sublayers/variant contributions. Complex stacks retain the existing path.
    """
    from pxr import Sdf
    probe=Sdf.Layer.CreateAnonymous('asset-opinions.usda')
    if layer.anonymous or layer.subLayerPaths:
        probe.TransferContent(layer)
        return probe,False
    paths=[]
    layer.Traverse(Sdf.Path.absoluteRootPath,lambda path:paths.append(path))
    if any(path.ContainsPrimVariantSelection() for path in paths):
        probe.TransferContent(layer)
        return probe,False
    for key in layer.pseudoRoot.ListInfoKeys():
        if key not in ('subLayers','subLayerOffsets'):
            probe.pseudoRoot.SetInfo(key,layer.pseudoRoot.GetInfo(key))
    for path in paths:
        if path.IsPropertyPath():
            attr=layer.GetAttributeAtPath(path)
            if attr and attr.typeName in (Sdf.ValueTypeNames.Asset,Sdf.ValueTypeNames.AssetArray):
                Sdf.CreatePrimInLayer(probe,path.GetPrimPath())
                if not Sdf.CopySpec(layer,path,probe,path):raise ValueError('Unable to copy asset opinion')
        elif path.IsPrimPath():
            prim=layer.GetPrimAtPath(path)
            if not prim:continue
            for field in ('references','payload'):
                if prim.HasInfo(field):
                    target=Sdf.CreatePrimInLayer(probe,path)
                    items=prim.GetInfo(field).ApplyOperations([])
                    if field=='references':target.referenceList.explicitItems=items
                    else:target.payloadList.explicitItems=items
    return probe,True


def override_stage(upstream, rules, files, keepalive, cache):
    """Copy affected layer specs only; keep composition arcs and instanceability.

    Anonymous replacement layers are shared for identical input/rule signatures.
    No source layer is edited, saved, or flattened by this operation.
    """
    from pxr import Sdf, Usd, UsdUtils
    if not rules:
        return upstream
    signature = (upstream.GetRootLayer().identifier, json.dumps(rules, sort_keys=True))
    if signature in cache:
        return cache[signature]
    replacements = {}
    for rule in rules:
        target = files.get(rule.get('replacementDependencyId'))
        if not target:
            raise ValueError('Asset Override replacement file link is missing')
        replacements[asset_key(rule['originalPath'])] = os.path.abspath(target)
    root = upstream.GetRootLayer()
    layers = {layer.identifier: layer for layer in upstream.GetUsedLayers()}
    pending = list(layers.values())
    while pending:
        owner = pending.pop()
        for path in owner.GetCompositionAssetDependencies():
            if not path:
                continue
            child = Sdf.Layer.FindOrOpen(Sdf.ComputeAssetPathRelativeToLayer(owner, path))
            if child and child.identifier not in layers:
                layers[child.identifier] = child
                pending.append(child)
    changes, parents, probes, sparse_layers = {}, {}, {}, set()
    for identifier, layer in layers.items():
        origins = json.loads(layer.customLayerData.get('orchflowAssetOrigins', '{}'))
        edits = {}
        # ModifyAssetPaths also visits asset arrays, samples, references and payloads.
        probe,sparse=asset_override_probe(layer)
        probes[identifier]=probe
        if sparse:sparse_layers.add(identifier)
        def inspect(path):
            if not path:
                return path
            anchored = Sdf.ComputeAssetPathRelativeToLayer(layer, path)
            original = origins.get(asset_key(anchored), anchored)
            if asset_key(original) in replacements:
                edits[path] = (replacements[asset_key(original)], original)
            return path
        UsdUtils.ModifyAssetPaths(probe, inspect)
        if edits:
            changes[identifier] = edits
        for path in layer.GetCompositionAssetDependencies():
            if not path:
                continue
            anchored = Sdf.ComputeAssetPathRelativeToLayer(layer, path)
            child = Sdf.Layer.Find(anchored)
            if child:
                parents.setdefault(child.identifier, set()).add(identifier)
    affected = set(changes)
    queue = list(affected)
    while queue:
        for parent in parents.get(queue.pop(), []):
            if parent not in affected:
                affected.add(parent)
                queue.append(parent)
    if root.identifier not in affected:
        return upstream
    clones = {identifier: Sdf.Layer.CreateAnonymous('asset-override.usda') for identifier in affected}
    for identifier, clone in clones.items():
        layer = layers[identifier]
        clone.TransferContent(probes[identifier])
        origins = json.loads(layer.customLayerData.get('orchflowAssetOrigins', '{}'))
        def replace(path):
            if not path:
                return path
            anchored = Sdf.ComputeAssetPathRelativeToLayer(layer, path)
            if path in changes.get(identifier, {}):
                target, original = changes[identifier][path]
                origins[asset_key(target)] = original
                return target
            child = Sdf.Layer.Find(anchored)
            if child and child.identifier in clones:
                return clones[child.identifier].identifier
            return anchored
        UsdUtils.ModifyAssetPaths(clone, replace)
        if identifier in sparse_layers:clone.subLayerPaths=[layer.identifier]
        metadata = dict(clone.customLayerData)
        metadata['orchflowAssetOrigins'] = json.dumps(origins)
        clone.customLayerData = metadata
    result = Usd.Stage.Open(clones[root.identifier])
    inherit_stage_metrics(result,upstream)
    keepalive.extend([*clones.values(), result])
    cache[signature] = result
    return result


def has_skeleton(stage):
    from pxr import Usd, UsdSkel
    return any(p.IsA(UsdSkel.Root) or p.IsA(UsdSkel.Skeleton)
               for p in stage.Traverse(Usd.TraverseInstanceProxies()))


def mark_reference_instanceable(prim, upstream, enable=True):
    # USD 25.08's legacy skeleton adapter cannot populate a SkelRoot nested
    # inside native instance prototypes. Keep shared reference arcs/specs but
    # allow separate skeleton evaluation. Ordinary mesh assets stay instanced
    # unless instancing is explicitly disabled (e.g. for hero assets/component authoring).
    prim.SetInstanceable(bool(enable) and not has_skeleton(upstream))


def find_blender_executable():
    import os, shutil
    appdata = os.environ.get('APPDATA')
    if appdata:
        cfg = os.path.join(appdata, 'com.orchflow.desktop', 'applications.json')
        if os.path.isfile(cfg):
            try:
                import json
                with open(cfg, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for app in data.get('applications', []):
                    if app.get('id') == 'blender' and app.get('executable'):
                        exe = app['executable']
                        if os.path.isfile(exe):
                            return exe
            except Exception:
                pass
    candidates = [
        r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender\blender.exe",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    which = shutil.which("blender")
    if which:
        return which
    return None


_BLENDER_FULL_LAYER_CACHE = {}
_BLENDER_STAGE_CACHE = {}
_MAX_BLENDER_FULL_CACHE = 8
_MAX_BLENDER_STAGE_CACHE = 24


def _evict_blender_caches(norm_path):
    for k in [k for k in _BLENDER_FULL_LAYER_CACHE if k[0] == norm_path]:
        del _BLENDER_FULL_LAYER_CACHE[k]
    for k in [k for k in _BLENDER_STAGE_CACHE if k[0] == norm_path]:
        del _BLENDER_STAGE_CACHE[k]


def _apply_blender_materialx_to_layer(layer, extracted_materials, convert_procedurals=False):
    if not extracted_materials:
        return
    from pxr import Usd, UsdGeom, UsdShade, Tf
    stage = Usd.Stage.Open(layer)
    if not stage:
        return
    if not stage.GetDefaultPrim():
        root_prim = stage.GetPrimAtPath('/root') or next(iter(stage.GetPseudoRoot().GetChildren()), None)
        if root_prim:
            stage.SetDefaultPrim(root_prim)

    by_key = {}
    for mat_name, spec in extracted_materials.items():
        by_key[mat_name] = spec
        by_key[Tf.MakeValidIdentifier(mat_name)] = spec

    old_to_new = {}
    files = {}
    tex_idx = 0
    tex_fields = (
        ('baseColorTexture', 'baseColorTextureDependencyId'),
        ('roughnessTexture', 'roughnessTextureDependencyId'),
        ('metallicTexture', 'metallicTextureDependencyId'),
        ('normalTexture', 'normalTextureDependencyId'),
        ('bumpTexture', 'bumpTextureDependencyId'),
        ('emissionTexture', 'emissionTextureDependencyId'),
        ('opacityTexture', 'opacityTextureDependencyId'),
        ('transmissionTexture', 'transmissionTextureDependencyId'),
    )
    for prim in list(stage.Traverse()):
        if not prim.IsA(UsdShade.Material):
            continue
        spec = by_key.get(prim.GetName())
        if not spec:
            continue
        settings = dict(spec)
        settings['name'] = prim.GetName()
        settings['_skipBinding'] = True
        for src_key, dep_key in tex_fields:
            fp = settings.pop(src_key, None)
            if fp and os.path.isfile(fp):
                dep_id = f'__bltex_{tex_idx}'
                tex_idx += 1
                files[dep_id] = fp
                settings[dep_key] = dep_id
        proc_sub = settings.pop('_proceduralSubgraph', None) if convert_procedurals else None
        try:
            new_mat = author_materialx(stage, settings, files, procedural_subgraph=proc_sub)
            old_to_new[prim.GetPath()] = new_mat
        except Exception as exc:
            print(f"[orchflow] MaterialX conversion skipped for {prim.GetName()}: {exc}", file=sys.stderr)

    if not old_to_new:
        return
    for prim in stage.Traverse():
        if not prim.HasAPI(UsdShade.MaterialBindingAPI):
            continue
        binding = UsdShade.MaterialBindingAPI(prim)
        for purpose in (UsdShade.Tokens.allPurpose, UsdShade.Tokens.preview, UsdShade.Tokens.full):
            direct = binding.GetDirectBinding(materialPurpose=purpose)
            target_mat = direct.GetMaterial()
            if target_mat and target_mat.GetPath() in old_to_new:
                binding.Bind(old_to_new[target_mat.GetPath()], materialPurpose=purpose)


_BLENDER_MATERIALX_EXTRACTOR = r"""
def _extract_blender_materials(target_scene, material_mode):
    if material_mode == 'off':
        return {}
    engine = (getattr(target_scene.render, 'engine', '') or '').lower()

    def _is_octane_node(n):
        return bool(n and (n.bl_idname.startswith(('Octane', 'ShaderNodeOct')) or n.type == 'CUSTOM' and any(
            k in n.bl_idname for k in ('Octane',)
        )))

    def _clamp01(v):
        return max(0.0, min(1.0, float(v)))

    def _to_rgb(val, default=(0.8, 0.8, 0.8)):
        try:
            if hasattr(val, '__len__') and not isinstance(val, str):
                if len(val) >= 3:
                    return [float(val[0]), float(val[1]), float(val[2])]
                if len(val) == 1:
                    x = float(val[0])
                    return [x, x, x]
            x = float(val)
            return [x, x, x]
        except Exception:
            return list(default)

    def _to_float(val, default=0.0):
        try:
            if hasattr(val, '__len__') and not isinstance(val, str):
                vals = [float(x) for x in val[:3]]
                return sum(vals) / max(len(vals), 1)
            return float(val)
        except Exception:
            return float(default)

    def _resolve_img_path(node):
        img = getattr(node, 'image', None)
        if not img:
            return None
        fp = getattr(img, 'filepath', '') or ''
        if fp:
            try:
                abs_fp = os.path.abspath(bpy.path.abspath(fp, library=getattr(img, 'library', None)))
                if os.path.isfile(abs_fp):
                    return abs_fp
            except Exception:
                pass
        packed = getattr(img, 'packed_file', None)
        if packed is not None:
            try:
                import hashlib, tempfile
                data = packed.data
                if data:
                    tex_dir = os.path.join(tempfile.gettempdir(), 'sublayer_blender_cache', 'packed_tex')
                    os.makedirs(tex_dir, exist_ok=True)
                    fmt = (getattr(img, 'file_format', '') or 'PNG').lower()
                    ext = '.jpg' if fmt in ('jpeg', 'jpg') else ('.exr' if fmt == 'open_exr' else f'.{fmt}' if fmt.isalnum() else '.png')
                    digest = hashlib.sha256(data).hexdigest()[:20]
                    out_fp = os.path.join(tex_dir, f"{digest}{ext}")
                    if not os.path.isfile(out_fp):
                        with open(out_fp, 'wb') as f:
                            f.write(data)
                    return out_fp
            except Exception:
                pass
        return None

    def _find_texture_or_color(sock, visited=None, group_stack=()):
        if sock is None:
            return None, None
        if visited is None:
            visited = set()
        if not sock.is_linked:
            dv = getattr(sock, 'default_value', None)
            return None, dv
        link = sock.links[0]
        src_node = link.from_node
        if src_node.bl_idname == 'NodeGroupInput' and group_stack:
            outer = group_stack[-1]
            outer_sock = outer.inputs.get(link.from_socket.name)
            if outer_sock is not None:
                return _find_texture_or_color(outer_sock, visited, group_stack[:-1])
        visit_key = (src_node, getattr(link.from_socket, 'name', ''))
        if visit_key in visited:
            return None, None
        visited.add(visit_key)
        bid = src_node.bl_idname
        if bid == 'ShaderNodeGroup' and getattr(src_node, 'node_tree', None):
            out_n = next((n for n in src_node.node_tree.nodes if n.bl_idname == 'NodeGroupOutput'), None)
            if out_n:
                sock_name = getattr(link.from_socket, 'name', '')
                out_sock = out_n.inputs.get(sock_name) if sock_name else None
                if out_sock is None:
                    sock_id = getattr(link.from_socket, 'identifier', '')
                    out_sock = next((s for s in out_n.inputs if getattr(s, 'identifier', '') == sock_id), None)
                if out_sock is not None and out_sock.is_linked:
                    return _find_texture_or_color(out_sock, visited, group_stack + (src_node,))
        if bid in ('ShaderNodeTexImage', 'OctaneRGBImage', 'OctaneAlphaImage'):
            return _resolve_img_path(src_node), None
        if bid in ('OctaneRGBColor', 'OctaneRGBAColor'):
            return None, getattr(src_node, 'a_value', None) or (getattr(src_node.inputs[0], 'default_value', None) if src_node.inputs else None)
        if bid == 'OctaneGreyscaleColor':
            return None, getattr(src_node, 'a_value', None)
        fallback_val = None
        for cand_name in ('Color', 'Image', 'Input', 'Texture', 'A', 'B', 'Color1', 'Color2', 'Normal', 'Height'):
            for s in getattr(src_node, 'inputs', []):
                if s.name == cand_name and s.is_linked:
                    tex, val = _find_texture_or_color(s, visited, group_stack)
                    if tex:
                        return tex, val
                    if fallback_val is None and val is not None:
                        fallback_val = val
        for s in getattr(src_node, 'inputs', []):
            if s.is_linked and getattr(s, 'name', '') not in ('Fac', 'Factor'):
                tex, val = _find_texture_or_color(s, visited, group_stack)
                if tex:
                    return tex, val
                if fallback_val is None and val is not None:
                    fallback_val = val
        if fallback_val is not None:
            return None, fallback_val
        return None, getattr(sock, 'default_value', None)

    def _resolve_mix_factor(sock, group_stack=()):
        if sock is None:
            return 0.5
        if not sock.is_linked:
            return _to_float(getattr(sock, 'default_value', 0.5), 0.5)
        link = sock.links[0]
        src_node = link.from_node
        if src_node.bl_idname == 'NodeGroupInput' and group_stack:
            outer = group_stack[-1]
            outer_sock = outer.inputs.get(link.from_socket.name)
            if outer_sock is not None:
                return _resolve_mix_factor(outer_sock, group_stack[:-1])
        # When Factor is driven by an internal mask/texture/ramp network,
        # inputs[1] (s1) is the base shader and inputs[2] (s2) is the masked decal/overlay.
        return 0.0

    def _unwrap_shader(node, visited=None, group_stack=(), via_socket=None):
        if node is None:
            return None, 1.0, group_stack
        if visited is None:
            visited = set()
        visit_key = (node, getattr(via_socket, 'name', '') if via_socket is not None else '')
        if visit_key in visited:
            return None, 1.0, group_stack
        visited.add(visit_key)
        bid = node.bl_idname
        if bid == 'NodeGroupInput' and group_stack and via_socket is not None:
            outer = group_stack[-1]
            outer_sock = outer.inputs.get(via_socket.name)
            if outer_sock is not None and outer_sock.is_linked:
                l0 = outer_sock.links[0]
                return _unwrap_shader(l0.from_node, visited, group_stack[:-1], l0.from_socket)
            return None, 1.0, group_stack
        if bid == 'ShaderNodeGroup' and getattr(node, 'node_tree', None):
            out_n = next((n for n in node.node_tree.nodes if n.bl_idname == 'NodeGroupOutput'), None)
            if out_n:
                next_stack = group_stack + (node,)
                for s in out_n.inputs:
                    if s.is_linked and (s.type == 'SHADER' or s.name in ('Surface', 'Shader', 'BSDF', 'Material')):
                        l0 = s.links[0]
                        return _unwrap_shader(l0.from_node, visited, next_stack, l0.from_socket)
                for s in out_n.inputs:
                    if s.is_linked:
                        l0 = s.links[0]
                        return _unwrap_shader(l0.from_node, visited, next_stack, l0.from_socket)
        if bid == 'ShaderNodeAddShader':
            l1 = node.inputs[0].links[0] if len(node.inputs) > 0 and node.inputs[0].is_linked else None
            l2 = node.inputs[1].links[0] if len(node.inputs) > 1 and node.inputs[1].is_linked else None
            for cand_l in (l1, l2):
                if cand_l is not None:
                    sub, op, gs = _unwrap_shader(cand_l.from_node, set(visited), group_stack, cand_l.from_socket)
                    if sub is not None and getattr(sub, 'bl_idname', '') != 'NodeUndefined':
                        return sub, op, gs
            return None, 1.0, group_stack
        if bid == 'ShaderNodeMixShader':
            fac = _resolve_mix_factor(node.inputs[0] if len(node.inputs) > 0 else None, group_stack)
            l1 = node.inputs[1].links[0] if len(node.inputs) > 1 and node.inputs[1].is_linked else None
            l2 = node.inputs[2].links[0] if len(node.inputs) > 2 and node.inputs[2].is_linked else None
            s1 = l1.from_node if l1 else None
            s2 = l2.from_node if l2 else None
            if s1 and s1.bl_idname == 'ShaderNodeBsdfTransparent' and l2:
                sub, op, gs = _unwrap_shader(s2, visited, group_stack, l2.from_socket)
                return sub, min(op, _clamp01(fac)), gs
            if s2 and s2.bl_idname == 'ShaderNodeBsdfTransparent' and l1:
                sub, op, gs = _unwrap_shader(s1, visited, group_stack, l1.from_socket)
                return sub, min(op, _clamp01(1.0 - fac)), gs
            pick_l = l2 if (l2 and (fac >= 0.5 or not l1)) else l1
            if not pick_l:
                return None, 1.0, group_stack
            return _unwrap_shader(pick_l.from_node, visited, group_stack, pick_l.from_socket)
        if bid == 'OctaneMixMaterial':
            amt = _resolve_mix_factor(node.inputs.get('Amount'), group_stack)
            l1 = node.inputs['First material'].links[0] if node.inputs.get('First material') and node.inputs['First material'].is_linked else None
            l2 = node.inputs['Second material'].links[0] if node.inputs.get('Second material') and node.inputs['Second material'].is_linked else None
            pick_l = l2 if (l2 and (amt >= 0.5 or not l1)) else l1
            if not pick_l:
                return None, 1.0, group_stack
            return _unwrap_shader(pick_l.from_node, visited, group_stack, pick_l.from_socket)
        return node, 1.0, group_stack

    def _pick_output(nt):
        outs = [n for n in nt.nodes if n.bl_idname == 'ShaderNodeOutputMaterial' and n.inputs.get('Surface') and n.inputs['Surface'].is_linked]
        if not outs:
            return None
        prefer_octane = (material_mode == 'octane') or (material_mode == 'auto' and 'octane' in engine)
        def _score(o):
            tgt = getattr(o, 'target', 'ALL') or 'ALL'
            l0 = o.inputs['Surface'].links[0]
            src, _, _ = _unwrap_shader(l0.from_node, None, (), l0.from_socket)
            if src is None or getattr(src, 'bl_idname', '') == 'NodeUndefined':
                return (99, 0 if getattr(o, 'is_active_output', False) else 1)
            is_oct = _is_octane_node(src)
            if prefer_octane:
                if tgt == 'OCTANE' or is_oct:
                    return (0 if tgt == 'OCTANE' else 1, 0 if getattr(o, 'is_active_output', False) else 1)
                return (3, 0 if getattr(o, 'is_active_output', False) else 1)
            else:
                if not is_oct and tgt in ('CYCLES', 'EEVEE', 'ALL'):
                    return (0 if tgt == 'CYCLES' else (1 if tgt == 'EEVEE' else 2), 0 if getattr(o, 'is_active_output', False) else 1)
                return (3, 0 if getattr(o, 'is_active_output', False) else 1)
        outs.sort(key=_score)
        return outs[0]

    def _extract_octane_medium(med_sock, group_stack=()):
        if not med_sock or not med_sock.is_linked:
            return {}
        mnode = med_sock.links[0].from_node
        mbid = mnode.bl_idname
        if mbid not in ('OctaneRandomWalk', 'OctaneScattering', 'OctaneAbsorption'):
            return {}
        out = {'subsurface': 0.65, 'subsurfaceScale': 0.02}
        albedo_s = mnode.inputs.get('Albedo') or mnode.inputs.get('Scattering') or mnode.inputs.get('Absorption')
        if albedo_s:
            tex, val = _find_texture_or_color(albedo_s, None, group_stack)
            if tex:
                out['baseColorTexture'] = tex
                out['baseColor'] = [0.85, 0.72, 0.65]
                out['subsurfaceColor'] = [0.85, 0.72, 0.65]
            elif val is not None:
                out['subsurfaceColor'] = _to_rgb(val, (1.0, 0.75, 0.65))
        rad_s = mnode.inputs.get('Radius')
        if rad_s and not rad_s.is_linked and getattr(rad_s, 'default_value', None) is not None:
            out['subsurfaceRadius'] = _to_rgb(rad_s.default_value, (1.0, 0.35, 0.2))
        dens_s = mnode.inputs.get('Density')
        if dens_s and not dens_s.is_linked and getattr(dens_s, 'default_value', None) is not None:
            d = _to_float(dens_s.default_value, 100.0)
            if d > 0:
                out['subsurfaceScale'] = round(max(0.001, min(0.05, 1.0 / max(d, 1.0))), 4)
        return out

    results = {}
    for mat in bpy.data.materials:
        if mat.name in results and getattr(mat, 'library', None) is not None:
            continue
        if not mat.use_nodes or not mat.node_tree:
            continue
        out_node = _pick_output(mat.node_tree)
        if not out_node:
            continue
        l0_main = out_node.inputs['Surface'].links[0]
        shader, mix_opacity, group_stack = _unwrap_shader(l0_main.from_node, None, (), l0_main.from_socket)
        if not shader:
            continue
        bid = shader.bl_idname
        inps = shader.inputs
        d = {'name': mat.name, 'opacity': mix_opacity}

        def _set_color_or_tex(sock_name, color_key, tex_key, default=(0.8, 0.8, 0.8)):
            s = inps.get(sock_name)
            if not s:
                return
            tex, val = _find_texture_or_color(s, None, group_stack)
            if tex:
                d[tex_key] = tex
            if val is not None:
                d[color_key] = _to_rgb(val, default)

        def _set_float_or_tex(sock_name, val_key, tex_key=None, default=0.0, scale=1.0):
            s = inps.get(sock_name)
            if not s:
                return
            tex, val = _find_texture_or_color(s, None, group_stack)
            if tex and tex_key:
                d[tex_key] = tex
            if val is not None:
                d[val_key] = _clamp01(_to_float(val, default) * scale) if scale == 1.0 and val_key in ('metalness', 'roughness', 'coat', 'coatRoughness', 'sheen', 'sheenRoughness', 'transmission', 'subsurface', 'opacity') else float(_to_float(val, default)) * scale

        if bid == 'ShaderNodeBsdfPrincipled':
            _set_color_or_tex('Base Color', 'baseColor', 'baseColorTexture', (0.8, 0.8, 0.8))
            _set_float_or_tex('Metallic', 'metalness', 'metallicTexture', 0.0)
            _set_float_or_tex('Roughness', 'roughness', 'roughnessTexture', 0.5)
            _set_float_or_tex('Specular IOR Level', 'specular', None, 0.5)
            _set_color_or_tex('Specular Tint', 'specularColor', None, (1.0, 1.0, 1.0))
            _set_float_or_tex('IOR', 'specularIor', None, 1.45)
            _set_float_or_tex('Alpha', 'opacity', 'opacityTexture', mix_opacity)
            _set_float_or_tex('Coat Weight', 'coat', None, 0.0)
            _set_float_or_tex('Coat Roughness', 'coatRoughness', None, 0.1)
            _set_color_or_tex('Coat Tint', 'coatColor', None, (1.0, 1.0, 1.0))
            _set_float_or_tex('Sheen Weight', 'sheen', None, 0.0)
            _set_float_or_tex('Sheen Roughness', 'sheenRoughness', None, 0.5)
            _set_color_or_tex('Sheen Tint', 'sheenColor', None, (1.0, 1.0, 1.0))
            _set_float_or_tex('Transmission Weight', 'transmission', 'transmissionTexture', 0.0)
            if 'transmission' not in d:
                _set_float_or_tex('Transmission', 'transmission', 'transmissionTexture', 0.0)
            if d.get('transmission', 0.0) > 0.5 and (d.get('specularIor', 1.45) < 1.15 or any(k in mat.name.lower() for k in ('eye', 'tear', 'cornea', 'shell', 'cartilage', 'saliva', 'lacrimal'))):
                d['thinWalled'] = True
            _set_float_or_tex('Subsurface Weight', 'subsurface', None, 0.0)
            _set_float_or_tex('Subsurface Scale', 'subsurfaceScale', None, 0.05)
            if inps.get('Subsurface Radius') and not inps['Subsurface Radius'].is_linked:
                d['subsurfaceRadius'] = _to_rgb(inps['Subsurface Radius'].default_value, (1.0, 0.2, 0.1))
            if 'baseColor' in d:
                d['subsurfaceColor'] = list(d['baseColor'])
            em_str = _to_float(getattr(inps.get('Emission Strength'), 'default_value', 0.0), 0.0) if inps.get('Emission Strength') else 0.0
            _set_color_or_tex('Emission Color', 'emissionColor', 'emissionTexture', (0.0, 0.0, 0.0))
            em_col = d.get('emissionColor', [0.0, 0.0, 0.0])
            if em_str > 0 and (max(em_col) > 0 or 'emissionTexture' in d):
                d['emission'] = em_str
            if 'baseColorTexture' not in d and 'emissionTexture' in d and (not inps.get('Base Color') or not inps['Base Color'].is_linked):
                d['baseColorTexture'] = d['emissionTexture']
                d['baseColor'] = [1.0, 1.0, 1.0]
            _set_float_or_tex('Normal', '__unused_norm', 'normalTexture', 0.0)
            d.pop('__unused_norm', None)
            proc_sub = _extract_blender_procedural_graph([
                (inps.get('Base Color'), 'base_color', 'replace'),
                (inps.get('Metallic'), 'metalness', 'replace'),
                (inps.get('Roughness'), 'specular_roughness', 'replace'),
                (inps.get('Specular IOR Level'), 'specular', 'replace'),
                (inps.get('Coat Weight'), 'coat', 'replace'),
                (inps.get('Coat Roughness'), 'coat_roughness', 'replace'),
                (inps.get('Emission Color'), 'emission_color', 'replace'),
                (inps.get('Normal'), 'normal', 'replace'),
            ], group_stack)
            if proc_sub:
                d['_proceduralSubgraph'] = proc_sub
            results[mat.name] = d
        elif bid == 'ShaderNodeBsdfHairPrincipled':
            mel = _to_float(getattr(inps.get('Melanin'), 'default_value', 0.8), 0.8) if inps.get('Melanin') else 0.8
            _set_color_or_tex('Color', 'baseColor', 'baseColorTexture', (0.04, 0.03, 0.025))
            if 'baseColor' not in d or d['baseColor'] == [0.0, 0.0, 0.0]:
                v = max(0.015, (1.0 - mel) ** 2 * 0.45)
                d['baseColor'] = [v, v * 0.78, v * 0.65]
            _set_float_or_tex('Roughness', 'roughness', None, 0.35)
            _set_float_or_tex('IOR', 'specularIor', None, 1.55)
            _set_float_or_tex('Coat', 'coat', None, 0.6)
            d['coatRoughness'] = 0.12
            d['subsurface'] = 0.15
            d['subsurfaceScale'] = 0.005
            d['subsurfaceColor'] = list(d.get('baseColor', [0.1, 0.08, 0.06]))
            results[mat.name] = d
        elif bid == 'OctaneUniversalMaterial':
            _set_color_or_tex('Albedo', 'baseColor', 'baseColorTexture', (0.8, 0.8, 0.8))
            _set_float_or_tex('Metallic', 'metalness', 'metallicTexture', 0.0)
            _set_float_or_tex('Roughness', 'roughness', 'roughnessTexture', 0.4)
            _set_float_or_tex('Specular', 'specular', None, 0.5)
            _set_float_or_tex('Dielectric IOR', 'specularIor', None, 1.5)
            _set_float_or_tex('Opacity', 'opacity', 'opacityTexture', mix_opacity)
            _set_color_or_tex('Coating', 'coatColor', None, (0.0, 0.0, 0.0))
            if 'coatColor' in d:
                d['coat'] = _clamp01(max(d['coatColor']))
            _set_float_or_tex('Coating roughness', 'coatRoughness', None, 0.1)
            _set_color_or_tex('Sheen', 'sheenColor', None, (0.0, 0.0, 0.0))
            if 'sheenColor' in d:
                d['sheen'] = _clamp01(max(d['sheenColor']))
            _set_float_or_tex('Sheen roughness', 'sheenRoughness', None, 0.3)
            if inps.get('Transmission') and (inps['Transmission'].is_linked or getattr(inps['Transmission'], 'default_value', None) is not None):
                _set_color_or_tex('Transmission', 'transmissionColor', 'transmissionTexture', (1.0, 1.0, 1.0))
                tc = d.get('transmissionColor', [0.0, 0.0, 0.0])
                d['transmission'] = 1.0 if 'transmissionTexture' in d else _clamp01(max(tc))
                thin_sock = inps.get('Thin wall') or inps.get('Fake shadows')
                if d['transmission'] > 0.5 and (
                    (thin_sock is not None and bool(getattr(thin_sock, 'default_value', False)))
                    or d.get('specularIor', 1.5) < 1.15
                    or any(k in mat.name.lower() for k in ('eye', 'tear', 'cornea', 'shell', 'cartilage', 'saliva', 'lacrimal'))
                ):
                    d['thinWalled'] = True
            med = _extract_octane_medium(inps.get('Medium'), group_stack)
            if 'baseColorTexture' in d and 'baseColorTexture' in med:
                med.pop('baseColorTexture', None)
            d.update(med)
            if med.get('subsurface'):
                d['transmission'] = 0.0
                d.pop('transmissionTexture', None)
            if 'baseColorTexture' in d and d.get('baseColor') == [0.0, 0.0, 0.0]:
                d['baseColor'] = [0.8, 0.8, 0.8]
            if 'subsurface' in d and 'baseColor' in d:
                d['subsurfaceColor'] = list(d['baseColor'])
            if inps.get('Normal') and inps['Normal'].is_linked:
                tex, _ = _find_texture_or_color(inps['Normal'], None, group_stack)
                if tex:
                    d['normalTexture'] = tex
            elif inps.get('Bump') and inps['Bump'].is_linked:
                tex, _ = _find_texture_or_color(inps['Bump'], None, group_stack)
                if tex:
                    d['bumpTexture'] = tex
            proc_sub = _extract_blender_procedural_graph([
                (inps.get('Albedo'), 'base_color', 'replace'),
                (inps.get('Metallic'), 'metalness', 'replace'),
                (inps.get('Roughness'), 'specular_roughness', 'replace'),
                (inps.get('Specular'), 'specular', 'replace'),
                (inps.get('Coating roughness'), 'coat_roughness', 'replace'),
                (inps.get('Bump'), 'normal', 'bump_normal'),
                (inps.get('Normal'), 'normal', 'replace'),
            ], group_stack)
            if proc_sub:
                d['_proceduralSubgraph'] = proc_sub
            results[mat.name] = d
        elif bid == 'OctaneGlossyMaterial':
            _set_color_or_tex('Diffuse', 'baseColor', 'baseColorTexture', (0.7, 0.7, 0.7))
            _set_float_or_tex('Specular', 'specular', None, 0.8)
            _set_float_or_tex('Roughness', 'roughness', 'roughnessTexture', 0.1)
            _set_float_or_tex('Index of refraction', 'specularIor', None, 1.45)
            _set_float_or_tex('Opacity', 'opacity', 'opacityTexture', mix_opacity)
            _set_color_or_tex('Sheen', 'sheenColor', None, (0.0, 0.0, 0.0))
            if 'sheenColor' in d:
                d['sheen'] = _clamp01(max(d['sheenColor']))
            _set_float_or_tex('Sheen Roughness', 'sheenRoughness', None, 0.2)
            if inps.get('Normal') and inps['Normal'].is_linked:
                tex, _ = _find_texture_or_color(inps['Normal'])
                if tex:
                    d['normalTexture'] = tex
            proc_sub = _extract_blender_procedural_graph([
                (inps.get('Diffuse'), 'base_color', 'replace'),
                (inps.get('Roughness'), 'specular_roughness', 'replace'),
                (inps.get('Specular'), 'specular', 'replace'),
                (inps.get('Bump'), 'normal', 'bump_normal'),
                (inps.get('Normal'), 'normal', 'replace'),
            ], group_stack)
            if proc_sub:
                d['_proceduralSubgraph'] = proc_sub
            results[mat.name] = d
        elif bid == 'OctaneDiffuseMaterial':
            _set_color_or_tex('Diffuse', 'baseColor', 'baseColorTexture', (0.7, 0.7, 0.7))
            _set_float_or_tex('Roughness', 'roughness', 'roughnessTexture', 0.8)
            d['specular'] = 0.05
            _set_float_or_tex('Opacity', 'opacity', 'opacityTexture', mix_opacity)
            d.update(_extract_octane_medium(inps.get('Medium'), group_stack))
            proc_sub = _extract_blender_procedural_graph([
                (inps.get('Diffuse'), 'base_color', 'replace'),
                (inps.get('Roughness'), 'specular_roughness', 'replace'),
                (inps.get('Bump'), 'normal', 'bump_normal'),
                (inps.get('Normal'), 'normal', 'replace'),
            ], group_stack)
            if proc_sub:
                d['_proceduralSubgraph'] = proc_sub
            results[mat.name] = d
        elif bid == 'OctaneSpecularMaterial':
            _set_color_or_tex('Reflection', 'specularColor', None, (1.0, 1.0, 1.0))
            _set_color_or_tex('Transmission', 'transmissionColor', None, (1.0, 1.0, 1.0))
            d['transmission'] = 1.0
            d['baseColor'] = [1.0, 1.0, 1.0]
            _set_float_or_tex('Roughness', 'roughness', 'roughnessTexture', 0.02)
            _set_float_or_tex('Index of refraction', 'specularIor', None, 1.45)
            _set_float_or_tex('Opacity', 'opacity', 'opacityTexture', mix_opacity)
            d.update(_extract_octane_medium(inps.get('Medium'), group_stack))
            d['subsurface'] = 0.0
            thin_sock = inps.get('Thin wall') or inps.get('Fake shadows')
            if (
                (thin_sock is not None and bool(getattr(thin_sock, 'default_value', False)))
                or d.get('specularIor', 1.45) < 1.15
                or any(k in mat.name.lower() for k in ('eye', 'tear', 'cornea', 'shell', 'cartilage', 'saliva', 'lacrimal'))
            ):
                d['thinWalled'] = True
            results[mat.name] = d
        elif bid == 'ShaderNodeBsdfGlass':
            _set_color_or_tex('Color', 'transmissionColor', 'transmissionTexture', (1.0, 1.0, 1.0))
            d['transmission'] = 1.0
            d['baseColor'] = list(d.get('transmissionColor', [1.0, 1.0, 1.0]))
            _set_float_or_tex('Roughness', 'roughness', 'roughnessTexture', 0.0)
            _set_float_or_tex('IOR', 'specularIor', None, 1.45)
            d['opacity'] = mix_opacity
            if d.get('specularIor', 1.45) < 1.15 or any(k in mat.name.lower() for k in ('eye', 'tear', 'cornea', 'shell', 'cartilage', 'saliva', 'lacrimal')):
                d['thinWalled'] = True
            results[mat.name] = d
        elif bid == 'ShaderNodeBsdfTransparent':
            _set_color_or_tex('Color', 'transmissionColor', 'transmissionTexture', (1.0, 1.0, 1.0))
            d['transmission'] = 1.0
            d['baseColor'] = [1.0, 1.0, 1.0]
            d['roughness'] = 0.0
            d['specular'] = 0.0
            d['specularIor'] = 1.0
            d['opacity'] = 0.0
            d['thinWalled'] = True
            results[mat.name] = d
        elif bid == 'OctaneHairMaterial':
            mel = _to_float(getattr(inps.get('Melanin'), 'default_value', 0.8), 0.8) if inps.get('Melanin') else 0.8
            _set_color_or_tex('Albedo', 'baseColor', 'baseColorTexture', (0.04, 0.03, 0.025))
            if 'baseColor' not in d or d['baseColor'] == [1.0, 1.0, 1.0]:
                v = max(0.015, (1.0 - mel) ** 2 * 0.45)
                d['baseColor'] = [v, v * 0.78, v * 0.65]
            _set_float_or_tex('Longitudinal roughness', 'roughness', None, 0.2)
            _set_float_or_tex('Index of refraction', 'specularIor', None, 1.55)
            _set_float_or_tex('Opacity', 'opacity', 'opacityTexture', mix_opacity)
            d['coat'] = 0.55
            d['coatRoughness'] = 0.12
            d['subsurface'] = 0.15
            d['subsurfaceScale'] = 0.005
            d['subsurfaceColor'] = list(d['baseColor'])
            results[mat.name] = d
    return results
"""


def _blender_disk_cache_paths(norm_path, mtime, size, export_skeletons=False, scene_name=None):
    import hashlib, tempfile
    cache_dir = os.path.join(tempfile.gettempdir(), 'sublayer_blender_cache')
    path_hash = hashlib.sha256(norm_path.encode('utf-8')).hexdigest()[:12]
    skel_tag = 'skel1' if export_skeletons else 'skel0'
    key = hashlib.sha256(f"{norm_path}|{mtime}|{size}|v11|{skel_tag}|{scene_name!r}".encode('utf-8')).hexdigest()[:24]
    return cache_dir, os.path.join(cache_dir, f"{path_hash}_{key}.usdc"), os.path.join(cache_dir, f"{path_hash}_{key}.json")


def _run_blender_extract_process(cmd, payload, timeout_seconds=240.0):
    import subprocess
    import threading
    import time

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=0x08000000 if os.name == 'nt' else 0,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    stdout_lines = []
    stderr_lines = []
    success_event = threading.Event()

    def _read_stdout():
        try:
            for line in iter(proc.stdout.readline, ''):
                stdout_lines.append(line)
                if 'ORCHFLOW_BLENDER_EXPORT:SUCCESS' in line:
                    success_event.set()
        except Exception:
            pass

    def _read_stderr():
        try:
            for line in iter(proc.stderr.readline, ''):
                stderr_lines.append(line)
        except Exception:
            pass

    stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    try:
        if proc.stdin is not None:
            try:
                proc.stdin.write(payload + "\n")
                proc.stdin.flush()
            except OSError:
                pass
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass

        deadline = time.monotonic() + float(timeout_seconds)
        while True:
            if success_event.wait(timeout=0.05):
                try:
                    proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                        proc.wait(timeout=2.0)
                    except Exception:
                        pass
                stdout_thread.join(timeout=0.5)
                stderr_thread.join(timeout=0.2)
                return 0, "".join(stdout_lines), "".join(stderr_lines)

            if proc.poll() is not None:
                stdout_thread.join(timeout=2.0)
                stderr_thread.join(timeout=1.0)
                rc = 0 if success_event.is_set() else proc.returncode
                return rc, "".join(stdout_lines), "".join(stderr_lines)

            if time.monotonic() > deadline:
                try:
                    proc.kill()
                    proc.wait(timeout=2.0)
                except Exception:
                    pass
                stdout_thread.join(timeout=0.5)
                stderr_thread.join(timeout=0.5)
                raise RuntimeError(
                    f"Blender headless extraction timed out after {int(timeout_seconds)}s: "
                    f"{''.join(stderr_lines)[-1000:]} {''.join(stdout_lines)[-1000:]}"
                )
    finally:
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass


def _get_or_extract_full_blender_layer(blend_path, mtime, selected_objects=None, reload=False, material_mode='off', est_seconds=None, convert_procedurals=False, export_skeletons=False, scene_name=None):
    import os, tempfile, subprocess, json
    from pxr import Sdf, UsdUtils
    material_mode = material_mode if material_mode in ('off', 'auto', 'octane', 'cycles') else 'off'
    export_skeletons = bool(export_skeletons)
    norm_path = os.path.normcase(os.path.abspath(blend_path))
    file_size = os.path.getsize(blend_path) if os.path.isfile(blend_path) else 0
    cache_dir, disk_usdc, disk_json = _blender_disk_cache_paths(norm_path, mtime, file_size, export_skeletons=export_skeletons, scene_name=scene_name)
    if reload:
        _evict_blender_caches(norm_path)
        for p in (disk_usdc, disk_json):
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
    else:
        for k in [k for k in _BLENDER_FULL_LAYER_CACHE if k[0] == norm_path and k[1] != mtime]:
            del _BLENDER_FULL_LAYER_CACHE[k]
        for k in [k for k in _BLENDER_STAGE_CACHE if k[0] == norm_path and k[1] != mtime]:
            del _BLENDER_STAGE_CACHE[k]

    def _materialize_mode(raw_entry):
        raw_layer = raw_entry["raw_layer"]
        mat_bundles = raw_entry.get("mat_bundles") or {}
        if material_mode == 'off' or not mat_bundles.get(material_mode):
            layer = raw_layer
        else:
            layer = Sdf.Layer.CreateAnonymous('blender_mtlx.usdc')
            layer.TransferContent(raw_layer)
            extracted_materials = mat_bundles.get(material_mode) or {}
            if extracted_materials:
                report_status(f"Blender: Converting {len(extracted_materials)} material(s) to MaterialX...")
                _apply_blender_materialx_to_layer(layer, extracted_materials, convert_procedurals=convert_procedurals)
        return {
            "layer": layer,
            "scene": raw_entry["scene"],
            "scene_objects": raw_entry["scene_objects"],
            "default_visible": raw_entry["default_visible"],
        }

    def _obj_present(name: str, obj_set: set[str]) -> bool:
        if name in obj_set:
            return True
        inst_prefix = f"{name}__inst_"
        return any(o.startswith(inst_prefix) for o in obj_set)

    # Key includes both the resolved scene and the requested scene (None means
    # automatic selection). Never reuse a different scene's shared objects.
    matching_keys = [k for k in _BLENDER_FULL_LAYER_CACHE if k[0] == norm_path and k[1] == mtime and k[3] == export_skeletons and k[4] == scene_name]
    if matching_keys:
        if not selected_objects:
            k = matching_keys[-1]
            raw_entry = _BLENDER_FULL_LAYER_CACHE.pop(k)
            if material_mode == 'off' or raw_entry.get("has_materials"):
                _BLENDER_FULL_LAYER_CACHE[k] = raw_entry
                return _materialize_mode(raw_entry)
        else:
            sel_set = set(str(x) for x in selected_objects)
            best_k = max(matching_keys, key=lambda k: sum(1 for x in sel_set if _obj_present(x, _BLENDER_FULL_LAYER_CACHE[k]["scene_objects"])))
            if all(_obj_present(x, _BLENDER_FULL_LAYER_CACHE[best_k]["scene_objects"]) for x in sel_set):
                raw_entry = _BLENDER_FULL_LAYER_CACHE.pop(best_k)
                if material_mode == 'off' or raw_entry.get("has_materials"):
                    _BLENDER_FULL_LAYER_CACHE[best_k] = raw_entry
                    return _materialize_mode(raw_entry)

    prev_entry = None
    if not reload and matching_keys:
        k = matching_keys[-1]
        cand = _BLENDER_FULL_LAYER_CACHE.get(k)
        if cand and (material_mode == 'off' or cand.get("has_materials")):
            prev_entry = cand

    if not reload and os.path.isfile(disk_usdc) and os.path.isfile(disk_json):
        try:
            with open(disk_json, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            cached_objs = set(cached.get("sceneObjects") or [])
            has_mats = bool(cached.get("hasMaterials"))
            if scene_name and cached.get('scene') != scene_name:
                raise ValueError('Cached Blender layer belongs to a different scene')
            sel_ok = (not selected_objects) or all(_obj_present(str(x), cached_objs) for x in selected_objects)
            if (sel_ok or prev_entry is None) and (material_mode == 'off' or has_mats):
                disk_layer = Sdf.Layer.FindOrOpen(disk_usdc)
                if disk_layer:
                    raw_entry = {
                        "raw_layer": disk_layer,
                        "mat_bundles": cached.get("matBundles") or {},
                        "has_materials": has_mats,
                        "scene": str(cached.get("scene") or ""),
                        "scene_objects": cached_objs,
                        "default_visible": list(cached.get("defaultVisible") or []),
                    }
                    if sel_ok:
                        report_status(f"Blender: Loading cached scene layer for {os.path.basename(blend_path)}...")
                        full_key = (norm_path, mtime, raw_entry["scene"], export_skeletons, scene_name)
                        while len(_BLENDER_FULL_LAYER_CACHE) >= _MAX_BLENDER_FULL_CACHE:
                            _BLENDER_FULL_LAYER_CACHE.pop(next(iter(_BLENDER_FULL_LAYER_CACHE)))
                        _BLENDER_FULL_LAYER_CACHE[full_key] = raw_entry
                        return _materialize_mode(raw_entry)
                    prev_entry = raw_entry
        except Exception:
            pass

    extract_objects = selected_objects
    if prev_entry is not None and selected_objects:
        missing_objs = sorted(x for x in set(str(x) for x in selected_objects) if not _obj_present(x, prev_entry["scene_objects"]))
        if missing_objs:
            extract_objects = missing_objs

    requested_scene = scene_name
    blender_exe = find_blender_executable()
    if not blender_exe:
        raise ValueError("Blender executable not found. Please configure Blender executable in Applications settings.")
    est_suffix = f" (est. ~{round(est_seconds)}s)" if est_seconds and est_seconds > 0 else ""
    report_status(f"Blender: Extracting scene geometry & materials from {os.path.basename(blend_path)}{est_suffix}...")
    with tempfile.NamedTemporaryFile(suffix='.usdc', delete=False) as tmp:
        temp_usd_path = tmp.name
    py_script = _procedural_mtlx().BLENDER_PROCEDURAL_EXTRACTOR + "\n" + _BLENDER_MATERIALX_EXTRACTOR + r"""
import bpy, json, sys, os
line = sys.stdin.readline()
if not line:
    sys.exit(1)
config = json.loads(line)
usd_path = config['usdPath']
selected = config.get('selectedObjects')
material_mode = config.get('materialMode', 'off')
export_skeletons = bool(config.get('exportSkeletons', False))

EXPORTABLE_TYPES = {'MESH', 'CURVE', 'CURVES', 'SURFACE', 'META', 'FONT', 'VOLUME', 'HAIR', 'POINTCLOUD', 'GPENCIL', 'GREASEPENCIL'}
def is_exportable(obj):
    if obj.type == 'CURVE':
        data = getattr(obj, 'data', None)
        has_geo = bool(
            data is not None and (
                getattr(data, 'bevel_depth', 0.0) > 0.0
                or getattr(data, 'extrude', 0.0) > 0.0
                or getattr(data, 'bevel_object', None) is not None
            )
        )
        has_mesh_mod = any(
            getattr(m, 'type', None) in {'SCREW', 'SKIN', 'SOLIDIFY', 'REMESH'}
            for m in getattr(obj, 'modifiers', ())
        )
        return has_geo or has_mesh_mod
    if obj.type in EXPORTABLE_TYPES:
        return True
    if obj.type == 'EMPTY' and getattr(obj, 'instance_type', None) == 'COLLECTION' and getattr(obj, 'instance_collection', None) is not None:
        return True
    return False

scene_name = config.get('sceneName')
scenes = list(bpy.data.scenes)
win_scene = bpy.context.window.scene if bpy.context.window and bpy.context.window.scene else bpy.context.scene
if scene_name:
    best = bpy.data.scenes.get(scene_name)
    if best is None:
        raise RuntimeError(f'Blender scene {scene_name!r} no longer exists; rescan and select a scene')
    bpy.context.window.scene = best
elif isinstance(selected, list) and len(selected) > 0:
    sel_set = set(selected)
    best = max(scenes, key=lambda s: (sum(1 for o in s.objects if o.name in sel_set), 1 if (win_scene and s.name == win_scene.name) else 0))
    if best is not None:
        bpy.context.window.scene = best
elif win_scene is None or not any(is_exportable(o) for o in win_scene.objects):
    best = max(scenes, key=lambda s: sum(1 for o in s.objects if is_exportable(o)), default=None)
    if best is not None:
        bpy.context.window.scene = best

target_scene = bpy.context.window.scene

def _get_col_vis(scene):
    vis_map = {}
    def walk(lc, parent_vis, is_root=False):
        vis = True if is_root else (parent_vis and not getattr(lc, 'exclude', False) and not getattr(lc, 'hide_viewport', False) and not getattr(lc.collection, 'hide_viewport', False) and not getattr(lc.collection, 'hide_render', False))
        cname = lc.collection.name
        vis_map[cname] = vis_map.get(cname, False) or bool(vis)
        for child in lc.children:
            walk(child, vis, False)
    if scene.view_layers:
        walk(scene.view_layers[0].layer_collection, True, True)
    return vis_map

col_vis = _get_col_vis(target_scene)
vl = target_scene.view_layers[0] if target_scene.view_layers else None
default_visible = []
for obj in target_scene.objects:
    if not is_exportable(obj):
        continue
    is_hidden_vl = False
    if vl is not None:
        try:
            is_hidden_vl = bool(obj.hide_get(view_layer=vl))
        except Exception:
            is_hidden_vl = False
    cols = [c.name for c in obj.users_collection if c.name in col_vis] or [c.name for c in obj.users_collection]
    if (
        any(col_vis.get(c, True) for c in cols)
        and not getattr(obj, 'hide_viewport', False)
        and not getattr(obj, 'hide_render', False)
        and not is_hidden_vl
    ):
        default_visible.append(obj.name)

def _enable_collections(lc):
    lc.exclude = False
    lc.hide_viewport = False
    for child in lc.children:
        _enable_collections(child)
_enable_collections(bpy.context.view_layer.layer_collection)

for img in bpy.data.images:
    fp = getattr(img, 'filepath', '') or ''
    if fp:
        try:
            lib = getattr(img, 'library', None)
            abs_fp = os.path.abspath(bpy.path.abspath(fp, library=lib))
            if abs_fp != fp:
                img.filepath = abs_fp
        except Exception:
            pass

def _unhide_collection_tree(col, visited=None):
    if visited is None:
        visited = set()
    if col is None:
        return
    cid = (col.name, getattr(getattr(col, 'library', None), 'filepath', '') or '')
    if cid in visited:
        return
    visited.add(cid)
    for attr in ('hide_viewport', 'hide_render'):
        try:
            if getattr(col, attr, False):
                setattr(col, attr, False)
        except Exception:
            pass
    for o in getattr(col, 'all_objects', ()):
        for attr in ('hide_viewport', 'hide_render'):
            try:
                if getattr(o, attr, False):
                    setattr(o, attr, False)
            except Exception:
                pass
        if o.type == 'EMPTY' and getattr(o, 'instance_type', None) == 'COLLECTION' and getattr(o, 'instance_collection', None) is not None:
            _unhide_collection_tree(o.instance_collection, visited)

for obj in bpy.context.view_layer.objects:
    for attr in ('hide_viewport', 'hide_render'):
        try:
            if getattr(obj, attr, False):
                setattr(obj, attr, False)
        except Exception:
            pass
    try:
        if obj.hide_get():
            obj.hide_set(False)
    except Exception:
        pass
    if obj.type == 'EMPTY' and getattr(obj, 'instance_type', None) == 'COLLECTION' and getattr(obj, 'instance_collection', None) is not None:
        _unhide_collection_tree(obj.instance_collection)

for obj in bpy.data.objects:
    try:
        obj["orchflow_blender_object"] = obj.name
    except Exception:
        pass

# Realize collection instances and apply topology-generating modifiers (e.g. Mirror/Array/Solidify)
# on instanced/modifier-driven meshes so Blender's USD exporter does not fall back to
# the un-evaluated base mesh datablock, while preserving Armature modifiers when export_skeletons=True.
inst_empties = [
    o for o in list(bpy.context.view_layer.objects)
    if o.type == 'EMPTY'
    and getattr(o, 'instance_type', None) == 'COLLECTION'
    and getattr(o, 'instance_collection', None) is not None
]
if inst_empties:
    if bpy.context.mode != 'OBJECT':
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass
    for ie in inst_empties:
        try:
            bpy.ops.object.select_all(action='DESELECT')
            ie.select_set(True)
            bpy.context.view_layer.objects.active = ie
            bpy.ops.object.duplicates_make_real(use_base_parent=True, use_hierarchy=True)
            ie.instance_type = 'NONE'
        except Exception:
            pass
try:
    bpy.context.view_layer.update()
    TOPO_MODS = {'MIRROR', 'ARRAY', 'SOLIDIFY'}
    for obj in list(bpy.context.view_layer.objects):
        if obj.type != 'MESH' or not obj.modifiers:
            continue
        topo_names = [m.name for m in obj.modifiers if m.type in TOPO_MODS and m.show_viewport]
        if not topo_names:
            continue
        if obj.data and obj.data.users > 1:
            obj.data = obj.data.copy()
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        for mname in topo_names:
            try:
                bpy.ops.object.modifier_apply(modifier=mname)
            except Exception:
                pass
except Exception:
    pass

linked_mat_names = {m.name for m in bpy.data.materials if getattr(m, 'library', None) is not None}
used_mat_names = {m.name for m in bpy.data.materials}
for m in bpy.data.materials:
    if getattr(m, 'library', None) is None and m.name in linked_mat_names:
        cand = f"{m.name}__local"
        idx = 1
        while cand in used_mat_names:
            cand = f"{m.name}__local_{idx}"
            idx += 1
        m.name = cand
        used_mat_names.add(cand)

export_targets = set(str(x) for x in selected) if isinstance(selected, list) and len(selected) > 0 else set(default_visible)
selected_only = False
exported_names = set()
if export_targets:
    bpy.context.view_layer.update()
    to_select = set()
    for obj in target_scene.objects:
        if obj.name in export_targets:
            curr = obj
            while curr is not None:
                to_select.add(curr)
                curr = curr.parent
            if obj.type == 'EMPTY':
                for child in getattr(obj, 'children_recursive', ()):
                    to_select.add(child)
            for m in getattr(obj, 'modifiers', ()):
                if getattr(m, 'type', None) == 'ARMATURE' and getattr(m, 'object', None) is not None:
                    arm = m.object
                    while arm is not None:
                        to_select.add(arm)
                        arm = arm.parent
                elif getattr(m, 'type', None) == 'PARTICLE_SYSTEM':
                    ps = getattr(m, 'particle_system', None)
                    pset = getattr(ps, 'settings', None) if ps is not None else None
                    if pset is not None:
                        io = getattr(pset, 'instance_object', None)
                        ic = getattr(pset, 'instance_collection', None)
                        if io is not None:
                            for attr in ('hide_viewport', 'hide_render'):
                                try: setattr(io, attr, False)
                                except Exception: pass
                        if ic is not None:
                            _unhide_collection_tree(ic)
                elif getattr(m, 'type', None) == 'NODES':
                    try:
                        for k in m.keys():
                            val = m[k]
                            if isinstance(val, bpy.types.Collection):
                                _unhide_collection_tree(val)
                            elif isinstance(val, bpy.types.Object):
                                for attr in ('hide_viewport', 'hide_render'):
                                    try: setattr(val, attr, False)
                                    except Exception: pass
                    except Exception:
                        pass
    # Materialize non-EMPTY depsgraph instances (Particle OBJECT/COLLECTION, VERTS/FACES, Geometry Nodes)
    # as shared-data objects using the EVALUATED instance geometry (cached per unique mesh pointer)
    # so Blender's usd_export(use_instancing=True) emits true lightweight USD instances.
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    materialized_parents = set()
    proto_data_cache = {}
    inst_counter = 0
    for inst in depsgraph.object_instances:
        if not getattr(inst, 'is_instance', False):
            continue
        parent_ev = getattr(inst, 'parent', None)
        if parent_ev is None:
            continue
        parent_orig = getattr(parent_ev, 'original', parent_ev)
        if parent_orig not in to_select or parent_orig.type == 'EMPTY':
            continue
        inst_obj = getattr(inst, 'object', None)
        if inst_obj is None or inst_obj.type not in EXPORTABLE_TYPES:
            continue
        eval_data = getattr(inst_obj, 'data', None)
        if eval_data is None:
            continue
        if inst_obj.type == 'MESH' and len(eval_data.polygons) == 0:
            continue
        data_key = (inst_obj.type, eval_data.as_pointer())
        proto_data = proto_data_cache.get(data_key)
        if proto_data is None:
            if inst_obj.type == 'MESH':
                try:
                    cand = eval_data.copy()
                    if len(cand.vertices) > 0 and len(cand.polygons) > 0:
                        proto_data = cand
                    else:
                        bpy.data.meshes.remove(cand)
                except Exception:
                    proto_data = None
                if proto_data is None:
                    try:
                        import bmesh
                        bm = bmesh.new()
                        bm.from_mesh(eval_data)
                        proto_data = bpy.data.meshes.new(f"OrchFlowInstProto_{len(proto_data_cache)}")
                        bm.to_mesh(proto_data)
                        bm.free()
                        for mat in getattr(eval_data, 'materials', ()):
                            proto_data.materials.append(mat)
                        if len(proto_data.vertices) == 0 or len(proto_data.polygons) == 0:
                            bpy.data.meshes.remove(proto_data)
                            proto_data = None
                    except Exception:
                        proto_data = None
                if proto_data is None:
                    continue
            else:
                try:
                    proto_data = eval_data.copy()
                except Exception:
                    continue
            proto_data_cache[data_key] = proto_data
        inst_counter += 1
        dup = bpy.data.objects.new(f"{parent_orig.name}__inst_{inst_counter}", proto_data)
        dup.matrix_world = inst.matrix_world.copy()
        try:
            dup["orchflow_blender_object"] = parent_orig.name
        except Exception:
            pass
        target_scene.collection.objects.link(dup)
        to_select.add(dup)
        materialized_parents.add(parent_orig)
    for p_orig in materialized_parents:
        try:
            if getattr(p_orig, 'instance_type', 'NONE') in ('VERTS', 'FACES'):
                p_orig.instance_type = 'NONE'
        except Exception:
            pass
        has_gn = any(getattr(m, 'type', None) == 'NODES' for m in getattr(p_orig, 'modifiers', ()))
        hide_emitter = (
            has_gn
            or not getattr(p_orig, 'show_instancer_for_viewport', True)
            or not getattr(p_orig, 'show_instancer_for_render', True)
            or (p_orig.type == 'MESH' and getattr(p_orig, 'data', None) is not None and len(p_orig.data.polygons) == 0)
        )
        if hide_emitter and p_orig in to_select:
            to_select.discard(p_orig)
    if materialized_parents:
        bpy.context.view_layer.update()
    for obj in bpy.context.view_layer.objects:
        try:
            obj.select_set(obj in to_select)
            if obj in to_select:
                exported_names.add(obj.name)
        except Exception:
            pass
    if exported_names:
        selected_only = True
if not exported_names:
    exported_names = {o.name for o in target_scene.objects if is_exportable(o)}

props = bpy.ops.wm.usd_export.get_rna_type().properties
kwargs = {
    'filepath': usd_path,
    'root_prim_path': '/root',
    'export_meshes': True,
    'export_materials': True,
    'export_lights': False,
    'export_cameras': False,
    'export_curves': True,
    'export_volumes': False,
    'export_hair': True,
    'export_points': True,
    'export_armatures': export_skeletons,
    'export_shapekeys': export_skeletons,
    'export_animation': export_skeletons,
    'only_deform_bones': True,
    'export_normals': True,
    'export_uvmaps': True,
    'export_mesh_colors': True,
    'generate_preview_surface': True,
    'export_textures_mode': 'KEEP',
    'relative_paths': False,
    'convert_world_material': False,
    'use_instancing': True,
    'evaluation_mode': 'VIEWPORT',
    'export_subdivision': 'BEST_MATCH',
    'export_custom_properties': True,
    'author_blender_name': True,
    'selected_objects_only': selected_only,
}
if 'convert_orientation' in props:
    kwargs['convert_orientation'] = True

for k in list(kwargs.keys()):
    if k not in props:
        del kwargs[k]

# A single-frame scene is a posed snapshot. Animation export is required for
# UsdSkel, but must not replace its saved pose with another render-range frame.
export_scene = bpy.context.scene
if (kwargs.get('export_armatures') and kwargs.get('export_animation')
        and export_scene.frame_start == export_scene.frame_end):
    pose_frame = export_scene.frame_current
    export_scene.frame_start = pose_frame
    export_scene.frame_end = pose_frame

res = bpy.ops.wm.usd_export(**kwargs)
if 'FINISHED' not in res:
    raise RuntimeError(f'bpy.ops.wm.usd_export returned {res}')
mat_bundles = {}
if material_mode != 'off':
    mat_bundles = {
        'auto': _extract_blender_materials(target_scene, 'auto'),
        'octane': _extract_blender_materials(target_scene, 'octane'),
        'cycles': _extract_blender_materials(target_scene, 'cycles'),
    }
library_dirs = []
for lib in getattr(bpy.data, 'libraries', ()):
    lfp = getattr(lib, 'filepath', '') or ''
    if lfp:
        try:
            ldir = os.path.dirname(os.path.abspath(bpy.path.abspath(lfp)))
            if ldir and ldir not in library_dirs:
                library_dirs.append(ldir)
        except Exception:
            pass
meta = {
    'scene': target_scene.name,
    'sceneObjects': sorted(exported_names),
    'defaultVisible': default_visible,
    'libraryDirs': library_dirs,
}
print('\nORCHFLOW_BLENDER_META:' + json.dumps(meta), flush=True)
if mat_bundles:
    print('\nORCHFLOW_BLENDER_MATERIALS:' + json.dumps(mat_bundles), flush=True)
print('\nORCHFLOW_BLENDER_EXPORT:SUCCESS', flush=True)
sys.stdout.flush()
sys.stderr.flush()
os._exit(0)
"""
    temp_py_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', encoding='utf-8', delete=False) as py_tmp:
            py_tmp.write(py_script)
            temp_py_path = py_tmp.name
        cmd = [blender_exe, "--background"]
        if material_mode == 'off':
            cmd.append("--factory-startup")
        cmd.extend([blend_path, "--python-exit-code", "1", "--python", temp_py_path])
        payload = json.dumps({"usdPath": temp_usd_path, "selectedObjects": extract_objects, "materialMode": material_mode, "exportSkeletons": export_skeletons, "sceneName": requested_scene})
        try:
            returncode, stdout, stderr = _run_blender_extract_process(cmd, payload)
        finally:
            report_status("")
        if returncode != 0 or 'ORCHFLOW_BLENDER_EXPORT:SUCCESS' not in stdout:
            raise RuntimeError(f"Blender headless extraction failed: {stderr} {stdout}")
        if not os.path.isfile(temp_usd_path) or os.path.getsize(temp_usd_path) == 0:
            raise RuntimeError(f"Blender failed to produce USD data for {blend_path}")
        scene_name = ""
        scene_objects = set()
        default_visible = []
        library_dirs = []
        mat_bundles = {}
        for line in stdout.splitlines():
            if line.startswith("ORCHFLOW_BLENDER_META:"):
                try:
                    meta = json.loads(line[len("ORCHFLOW_BLENDER_META:"):].strip())
                    scene_name = str(meta.get("scene") or "")
                    scene_objects = set(meta.get("sceneObjects") or [])
                    default_visible = list(meta.get("defaultVisible") or [])
                    library_dirs = [str(d) for d in (meta.get("libraryDirs") or []) if d]
                except Exception:
                    pass
            elif line.startswith("ORCHFLOW_BLENDER_MATERIALS:"):
                try:
                    mat_bundles, _ = json.JSONDecoder().raw_decode(line[len("ORCHFLOW_BLENDER_MATERIALS:"):].lstrip())
                except Exception:
                    pass
        if requested_scene and scene_name != requested_scene:
            raise RuntimeError(f'Blender did not confirm export of requested scene {requested_scene!r}')
        temp_layer = Sdf.Layer.FindOrOpen(temp_usd_path)
        if not temp_layer:
            raise RuntimeError("Failed to read exported USD from Blender")
        report_status(f"Blender: Ingesting extracted scene layer into memory...")
        blend_dir = os.path.dirname(os.path.abspath(blend_path))
        search_dirs = [blend_dir] + [d for d in library_dirs if d != blend_dir]

        def _anchor_asset_path(p):
            if not p or os.path.isabs(p) or p.startswith('anon:'):
                return p
            for base_d in search_dirs:
                cand = os.path.abspath(os.path.join(base_d, p))
                if os.path.exists(cand):
                    return cand
            return p

        UsdUtils.ModifyAssetPaths(temp_layer, _anchor_asset_path)
        if prev_entry is not None and extract_objects != selected_objects:
            raw_layer = Sdf.Layer.CreateAnonymous('blender_full.usdc')
            raw_layer.TransferContent(prev_entry["raw_layer"])

            def _merge_prim_tree(src_spec, dst_path):
                dst_spec = raw_layer.GetPrimAtPath(dst_path)
                if not dst_spec:
                    Sdf.CreatePrimInLayer(raw_layer, dst_path)
                    Sdf.CopySpec(temp_layer, src_spec.path, raw_layer, dst_path)
                    return
                for child_name, child_spec in src_spec.nameChildren.items():
                    _merge_prim_tree(child_spec, dst_path.AppendChild(child_name))

            src_root = temp_layer.GetPrimAtPath('/root')
            if src_root:
                _merge_prim_tree(src_root, Sdf.Path('/root'))
            scene_objects = set(prev_entry["scene_objects"]) | scene_objects
            merged_bundles = {}
            for mode_k in set(prev_entry.get("mat_bundles", {}).keys()) | set(mat_bundles.keys()):
                merged_bundles[mode_k] = {
                    **(prev_entry.get("mat_bundles") or {}).get(mode_k, {}),
                    **mat_bundles.get(mode_k, {}),
                }
            mat_bundles = merged_bundles
        else:
            raw_layer = Sdf.Layer.CreateAnonymous('blender_full.usdc')
            raw_layer.TransferContent(temp_layer)
        del temp_layer
        has_materials = bool(mat_bundles)
        try:
            should_write_disk = True
            if os.path.isfile(disk_json) and os.path.isfile(disk_usdc):
                try:
                    with open(disk_json, 'r', encoding='utf-8') as f:
                        prev_cached = json.load(f)
                    prev_objs = set(prev_cached.get('sceneObjects') or [])
                    prev_has_mats = bool(prev_cached.get('hasMaterials'))
                    if scene_objects.issubset(prev_objs) and len(prev_objs) > len(scene_objects) and (not has_materials or prev_has_mats):
                        should_write_disk = False
                except Exception:
                    pass
            if should_write_disk:
                os.makedirs(cache_dir, exist_ok=True)
                tmp_u = f"{disk_usdc[:-5]}.tmp.{os.getpid()}.usdc"
                tmp_j = f"{disk_json[:-5]}.tmp.{os.getpid()}.json"
                raw_layer.Export(tmp_u)
                with open(tmp_j, 'w', encoding='utf-8') as f:
                    json.dump({
                        'scene': scene_name,
                        'sceneObjects': sorted(scene_objects),
                        'defaultVisible': default_visible,
                        'hasMaterials': has_materials,
                        'matBundles': mat_bundles,
                    }, f)
                os.replace(tmp_u, disk_usdc)
                os.replace(tmp_j, disk_json)
                _prune_disk_caches(keep_paths=(disk_usdc, disk_json))
        except Exception:
            pass
        raw_entry = {
            "raw_layer": raw_layer,
            "mat_bundles": mat_bundles,
            "has_materials": has_materials,
            "scene": scene_name,
            "scene_objects": scene_objects,
            "default_visible": default_visible,
        }
        full_key = (norm_path, mtime, scene_name, export_skeletons, requested_scene)
        while len(_BLENDER_FULL_LAYER_CACHE) >= _MAX_BLENDER_FULL_CACHE:
            _BLENDER_FULL_LAYER_CACHE.pop(next(iter(_BLENDER_FULL_LAYER_CACHE)))
        _BLENDER_FULL_LAYER_CACHE[full_key] = raw_entry
        return _materialize_mode(raw_entry)
    finally:
        for tmp_path in (temp_usd_path, temp_py_path):
            if tmp_path and os.path.isfile(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass


def _filter_blender_stage_in_memory(stage, sel_set):
    import re
    from pxr import Sdf, Usd, UsdGeom, UsdShade, UsdSkel, UsdVol

    sanitized_sel = {re.sub(r'[^A-Za-z0-9_]', '_', str(s)) for s in sel_set}

    def _name_selected(name):
        return name in sel_set or name in sanitized_sel

    def _get_blender_obj_name(prim):
        for attr_name in ('userProperties:orchflow_blender_object', 'blenderName:object', 'userProperties:blender:object_name'):
            attr = prim.GetAttribute(attr_name)
            if attr and attr.HasAuthoredValue():
                val = attr.Get()
                if isinstance(val, str) and val:
                    return val
        return None

    def _is_under_prototypes(path):
        parts = str(path).split('/')
        return 'prototypes' in parts

    def _is_prim_selected(prim):
        curr = prim
        ancestor_names = []
        ancestors = []
        while curr and curr.GetPath() != Sdf.Path.absoluteRootPath:
            ancestors.append(curr)
            name = _get_blender_obj_name(curr)
            if name:
                ancestor_names.append(name)
            curr = curr.GetParent()
        if ancestor_names:
            return any(_name_selected(name) for name in ancestor_names)
        return any(_name_selected(a.GetName()) for a in ancestors)

    def _collect_referenced_proto_paths(prim, out_paths, visited=None):
        if visited is None:
            visited = set()
        p_path = prim.GetPath()
        if p_path in visited:
            return
        visited.add(p_path)
        for spec in prim.GetPrimStack():
            if not spec.hasReferences:
                continue
            for ref in spec.referenceList.GetAddedOrExplicitItems():
                if not ref.assetPath and ref.primPath and not ref.primPath.isEmpty:
                    target = stage.GetPrimAtPath(ref.primPath)
                    out_paths.add(ref.primPath)
                    if target and target.IsValid():
                        for desc in Usd.PrimRange(target):
                            _collect_referenced_proto_paths(desc, out_paths, visited)
        if prim.IsInstance():
            proto = prim.GetPrototype()
            if proto and proto.IsValid():
                for desc in Usd.PrimRange(proto):
                    if desc.IsInstance():
                        _collect_referenced_proto_paths(desc, out_paths, visited)

    KEEP_CURVE_PRIMVARS = {
        'primvars:displayColor',
        'primvars:displayOpacity',
        'primvars:st',
        'primvars:uv',
        'primvars:normals',
        'primvars:widths',
    }
    to_remove = []
    to_hide = []
    to_clean_curves = []
    referenced_proto_paths = set()
    kept_instance_paths = set()

    for prim in stage.Traverse():
        if _is_under_prototypes(prim.GetPath()):
            continue
        if prim.IsA(UsdGeom.Subset):
            continue
        if prim.IsInstance():
            kept_instance_paths.add(prim.GetPath())
            _collect_referenced_proto_paths(prim, referenced_proto_paths)
            if not _is_prim_selected(prim):
                to_hide.append(prim.GetPath())
            continue
        if prim.IsA(UsdGeom.Gprim) or prim.IsA(UsdVol.Volume):
            keep = _is_prim_selected(prim)
            if prim.IsA(UsdGeom.BasisCurves):
                bc = UsdGeom.BasisCurves(prim)
                pts = bc.GetPointsAttr().Get()
                widths = bc.GetWidthsAttr().Get()
                if not pts or len(pts) == 0:
                    to_remove.append(prim.GetPath())
                    continue
                elif widths and min(widths) >= 1.0:
                    to_remove.append(prim.GetPath())
                    continue
                elif keep:
                    to_clean_curves.append(prim)
            elif prim.IsA(UsdVol.Volume):
                has_valid_field = any(
                    r.GetName().startswith('field:')
                    and any(stage.GetPrimAtPath(t).IsValid() for t in r.GetTargets())
                    for r in prim.GetAuthoredRelationships()
                )
                if not has_valid_field and not prim.GetChildren():
                    to_remove.append(prim.GetPath())
                    continue
            if not keep:
                to_hide.append(prim.GetPath())
        elif prim.GetTypeName() in ('SphereLight', 'DistantLight', 'RectLight', 'DiskLight', 'CylinderLight', 'DomeLight', 'Camera'):
            to_remove.append(prim.GetPath())

    for prim in to_clean_curves:
        for prop_name in prim.GetPropertyNames():
            if prop_name.startswith('primvars:') and prop_name not in KEEP_CURVE_PRIMVARS:
                prim.RemoveProperty(prop_name)

    for path in to_hide:
        p = stage.GetPrimAtPath(path)
        if p and p.IsValid() and p.IsA(UsdGeom.Imageable):
            UsdGeom.Imageable(p).MakeInvisible()

    has_sublayer_overlay = bool(stage.GetRootLayer().subLayerPaths)
    for path in to_remove:
        p = stage.GetPrimAtPath(path)
        if p and p.IsValid():
            if has_sublayer_overlay:
                p.SetActive(False)
            else:
                stage.RemovePrim(path)

    if has_sublayer_overlay:
        return

    def _has_geom_descendant(prim):
        if prim.IsInstance() and prim.GetPath() in kept_instance_paths:
            return True
        if prim.IsA(UsdGeom.Gprim) or prim.IsA(UsdVol.Volume) or prim.IsA(UsdVol.FieldBase):
            return True
        return any(_has_geom_descendant(child) for child in prim.GetAllChildren())

    def _prune_empty_ancestors(prim):
        for child in list(prim.GetAllChildren()):
            if (
                child.GetName() in ('_materials', '_OrchFlowMaterials', 'prototypes')
                or child.IsInstance()
                or child.IsA(UsdShade.Material)
                or child.IsA(UsdShade.Shader)
                or child.IsA(UsdSkel.Skeleton)
                or child.IsA(UsdSkel.BlendShape)
                or child.IsA(UsdVol.FieldBase)
            ):
                continue
            _prune_empty_ancestors(child)
            if not _has_geom_descendant(child):
                stage.RemovePrim(child.GetPath())

    root_prim = stage.GetDefaultPrim() or stage.GetPrimAtPath('/root') or stage.GetPseudoRoot()
    _prune_empty_ancestors(root_prim)

    # Prune unused prototype definitions under /root/prototypes while keeping every referenced prototype intact
    protos_scope = root_prim.GetChild('prototypes') if root_prim != stage.GetPseudoRoot() else stage.GetPrimAtPath('/prototypes')
    if protos_scope:
        for proto_child in list(protos_scope.GetAllChildren()):
            pp = proto_child.GetPath()
            if not any(rp == pp or rp.HasPrefix(pp) or pp.HasPrefix(rp) for rp in referenced_proto_paths):
                stage.RemovePrim(pp)
        if not list(protos_scope.GetAllChildren()):
            stage.RemovePrim(protos_scope.GetPath())

    used_materials = set()
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        for rel in prim.GetRelationships():
            if rel.GetName() == 'material:binding' or rel.GetName().startswith('material:binding:'):
                for target in rel.GetTargets():
                    used_materials.add(target.GetPrimPath())

    mats_scope = root_prim.GetChild('_materials') if root_prim != stage.GetPseudoRoot() else stage.GetPrimAtPath('/_materials')
    if mats_scope:
        for mat_prim in list(mats_scope.GetAllChildren()):
            if mat_prim.IsA(UsdShade.Material) and mat_prim.GetPath() not in used_materials:
                stage.RemovePrim(mat_prim.GetPath())


def get_or_extract_blender_memory_stage(blend_path, selections=None, reload=False, material_mode='off', est_seconds=None, convert_procedurals=False, export_skeletons=False, scene_name=None):
    import os
    from pxr import Sdf, Usd, UsdGeom
    material_mode = material_mode if material_mode in ('off', 'auto', 'octane', 'cycles') else 'off'
    convert_procedurals = bool(convert_procedurals)
    export_skeletons = bool(export_skeletons)
    if not os.path.isfile(blend_path):
        raise ValueError(f"Blender file does not exist: {blend_path}")
    mtime = os.path.getmtime(blend_path)
    norm_path = os.path.normcase(os.path.abspath(blend_path))
    sel_key = None if selections is None else tuple(sorted(str(x) for x in selections))
    cache_key = (norm_path, mtime, sel_key, material_mode, convert_procedurals, export_skeletons, scene_name)
    if not reload and cache_key in _BLENDER_STAGE_CACHE:
        stage = _BLENDER_STAGE_CACHE.pop(cache_key)
        _BLENDER_STAGE_CACHE[cache_key] = stage
        return stage

    if sel_key == ():
        mem_layer = Sdf.Layer.CreateAnonymous('blender_empty.usdc')
        stage = Usd.Stage.Open(mem_layer)
        root = UsdGeom.Xform.Define(stage, '/root')
        stage.SetDefaultPrim(root.GetPrim())
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        while len(_BLENDER_STAGE_CACHE) >= _MAX_BLENDER_STAGE_CACHE:
            _BLENDER_STAGE_CACHE.pop(next(iter(_BLENDER_STAGE_CACHE)))
        _BLENDER_STAGE_CACHE[cache_key] = stage
        return stage

    entry = _get_or_extract_full_blender_layer(blend_path, mtime, selected_objects=selections, reload=reload, material_mode=material_mode, convert_procedurals=convert_procedurals, export_skeletons=export_skeletons, scene_name=scene_name)
    full_layer = entry["layer"]
    mem_layer = Sdf.Layer.CreateAnonymous('blender_stage.usdc')
    mem_layer.subLayerPaths = [full_layer.identifier]
    if full_layer.defaultPrim:
        mem_layer.defaultPrim = full_layer.defaultPrim
    stage = Usd.Stage.Open(mem_layer)
    if not stage:
        raise RuntimeError("Failed to open in-memory stage from Blender layer")
    if not stage.GetDefaultPrim():
        root_prim = stage.GetPrimAtPath('/root') or next(iter(stage.GetPseudoRoot().GetChildren()), None)
        if root_prim:
            stage.SetDefaultPrim(root_prim)
    filter_set = set(sel_key) if sel_key is not None else set(entry.get("default_visible") or [])
    if sel_key is not None or filter_set:
        report_status(f"Blender: Filtering {len(filter_set)} selected object(s)...")
        _filter_blender_stage_in_memory(stage, filter_set)
    # Keep full_layer alive alongside the cached stage.
    stage._orchflow_full_layer = full_layer
    while len(_BLENDER_STAGE_CACHE) >= _MAX_BLENDER_STAGE_CACHE:
        _BLENDER_STAGE_CACHE.pop(next(iter(_BLENDER_STAGE_CACHE)))
    _BLENDER_STAGE_CACHE[cache_key] = stage
    return stage


def _compiled_graph_cache_paths(graph, needed, lookup, files, target):
    import hashlib, json, tempfile
    cache_dir = os.path.join(tempfile.gettempdir(), 'sublayer_graph_cache')
    dep_stamps = []
    for nid in sorted(needed):
        n = lookup[nid]
        dep_stamps.extend(_pose_dependency_stamps(n, files))
        for key_name in ('inputDependencyId', 'publishedDependencyId', 'nativeProjectDependencyId'):
            did = n.get(key_name)
            if did and did in files:
                p = files[did]
                np_path = os.path.normcase(os.path.realpath(p))
                if os.path.isfile(np_path):
                    ext = os.path.splitext(np_path)[1].lower()
                    mt = _sublayer_project_stamp(np_path) if ext in ('.sublayer', '.orchflow') else os.path.getmtime(np_path)
                    sz = os.path.getsize(np_path)
                else:
                    mt, sz = 0.0, -1
                dep_stamps.append((did, np_path, mt, sz))
        for ov in (n.get('assetOverrides') or []):
            did = ov.get('replacementDependencyId') if isinstance(ov, dict) else None
            if did and did in files:
                p = files[did]
                np_path = os.path.normcase(os.path.realpath(p))
                mt = os.path.getmtime(np_path) if os.path.isfile(np_path) else 0.0
                sz = os.path.getsize(np_path) if os.path.isfile(np_path) else -1
                dep_stamps.append((did, np_path, mt, sz))
    payload = {
        'v': 1,
        'target': target,
        'nodes': [lookup[nid] for nid in sorted(needed)],
        'edges': sorted(
            [e for e in (graph.get('edges') or []) if e.get('source') in needed and e.get('target') in needed],
            key=lambda e: str(e.get('id') or ''),
        ),
        'deps': sorted(set(dep_stamps)),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode('utf-8')).hexdigest()[:32]
    return cache_dir, os.path.join(cache_dir, f"{digest}.usdc"), os.path.join(cache_dir, f"{digest}.json")


def _load_compiled_graph_cache(cache_usdc, cache_json):
    import json
    from pxr import Sdf, Usd
    if not (os.path.isfile(cache_usdc) and os.path.isfile(cache_json)):
        return None, None, None
    try:
        with open(cache_json, 'r', encoding='utf-8') as f:
            meta = json.load(f)
        if isinstance(meta, dict) and 'stats' in meta:
            stats = meta['stats']
            mx_sel = meta.get('mxSelections') or {}
        else:
            stats = meta
            mx_sel = {}
        disk_layer = Sdf.Layer.FindOrOpen(cache_usdc)
        if not disk_layer:
            return None, None, None
        mem_layer = Sdf.Layer.CreateAnonymous('compiled_graph_cache.usdc')
        mem_layer.TransferContent(disk_layer)
        del disk_layer
        stage = Usd.Stage.Open(mem_layer)
        if stage is None:
            return None, None, None
        return stage, stats, mx_sel
    except Exception:
        return None, None, None


def _save_compiled_graph_cache(cache_dir, cache_usdc, cache_json, stage, stats, mx_selections=None):
    import json
    tmp_usdc = f"{cache_usdc}.tmp.{os.getpid()}.usdc"
    tmp_json = f"{cache_json}.tmp.{os.getpid()}.json"
    try:
        os.makedirs(cache_dir, exist_ok=True)
        flat = stage.Flatten()
        if not flat or not flat.Export(tmp_usdc):
            raise RuntimeError('Export failed')
        meta = {'stats': stats, 'mxSelections': mx_selections or {}}
        with open(tmp_json, 'w', encoding='utf-8') as f:
            json.dump(meta, f)
        os.replace(tmp_usdc, cache_usdc)
        os.replace(tmp_json, cache_json)
        usdc_files = [
            os.path.join(cache_dir, name)
            for name in os.listdir(cache_dir)
            if name.endswith('.usdc') and '.tmp.' not in name
        ]
        if len(usdc_files) > 12:
            usdc_files.sort(key=lambda p: os.path.getmtime(p))
            for old_usdc in usdc_files[:-12]:
                old_json = old_usdc[:-5] + '.json'
                for p in (old_usdc, old_json):
                    if os.path.isfile(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
    except Exception:
        for p in (tmp_usdc, tmp_json):
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


def _compiled_graph_cache_paths(graph, needed, lookup, files, target):
    import hashlib, json, tempfile
    cache_dir = os.path.join(tempfile.gettempdir(), 'sublayer_graph_cache')
    dep_stamps = []
    for nid in sorted(needed):
        n = lookup[nid]
        dep_stamps.extend(_pose_dependency_stamps(n, files))
        for key_name in ('inputDependencyId', 'publishedDependencyId', 'nativeProjectDependencyId'):
            did = n.get(key_name)
            if did and did in files:
                p = files[did]
                np_path = os.path.normcase(os.path.realpath(p))
                if os.path.isfile(np_path):
                    ext = os.path.splitext(np_path)[1].lower()
                    mt = _sublayer_project_stamp(np_path) if ext in ('.sublayer', '.orchflow') else os.path.getmtime(np_path)
                    sz = os.path.getsize(np_path)
                else:
                    mt, sz = 0.0, -1
                dep_stamps.append((did, np_path, mt, sz))
        for ov in (n.get('assetOverrides') or []):
            did = ov.get('replacementDependencyId') if isinstance(ov, dict) else None
            if did and did in files:
                p = files[did]
                np_path = os.path.normcase(os.path.realpath(p))
                mt = os.path.getmtime(np_path) if os.path.isfile(np_path) else 0.0
                sz = os.path.getsize(np_path) if os.path.isfile(np_path) else -1
                dep_stamps.append((did, np_path, mt, sz))
    payload = {
        'v': 1,
        'target': target,
        'nodes': [lookup[nid] for nid in sorted(needed)],
        'edges': sorted(
            [e for e in (graph.get('edges') or []) if e.get('source') in needed and e.get('target') in needed],
            key=lambda e: str(e.get('id') or ''),
        ),
        'deps': sorted(set(dep_stamps)),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode('utf-8')).hexdigest()[:32]
    return cache_dir, os.path.join(cache_dir, f"{digest}.usdc"), os.path.join(cache_dir, f"{digest}.json")


def _load_compiled_graph_cache(cache_usdc, cache_json):
    import json
    from pxr import Sdf, Usd
    if not (os.path.isfile(cache_usdc) and os.path.isfile(cache_json)):
        return None, None, None
    try:
        with open(cache_json, 'r', encoding='utf-8') as f:
            meta = json.load(f)
        if isinstance(meta, dict) and 'stats' in meta:
            stats = meta['stats']
            mx_sel = meta.get('mxSelections') or {}
        else:
            stats = meta
            mx_sel = {}
        disk_layer = Sdf.Layer.FindOrOpen(cache_usdc)
        if not disk_layer:
            return None, None, None
        mem_layer = Sdf.Layer.CreateAnonymous('compiled_graph_cache.usdc')
        mem_layer.TransferContent(disk_layer)
        del disk_layer
        stage = Usd.Stage.Open(mem_layer)
        if stage is None:
            return None, None, None
        return stage, stats, mx_sel
    except Exception:
        return None, None, None


def _save_compiled_graph_cache(cache_dir, cache_usdc, cache_json, stage, stats, mx_selections=None):
    import json
    tmp_usdc = f"{cache_usdc}.tmp.{os.getpid()}.usdc"
    tmp_json = f"{cache_json}.tmp.{os.getpid()}.json"
    try:
        os.makedirs(cache_dir, exist_ok=True)
        flat = stage.Flatten()
        if not flat or not flat.Export(tmp_usdc):
            raise RuntimeError('Export failed')
        meta = {'stats': stats, 'mxSelections': mx_selections or {}}
        with open(tmp_json, 'w', encoding='utf-8') as f:
            json.dump(meta, f)
        os.replace(tmp_usdc, cache_usdc)
        os.replace(tmp_json, cache_json)
        usdc_files = [
            os.path.join(cache_dir, name)
            for name in os.listdir(cache_dir)
            if name.endswith('.usdc') and '.tmp.' not in name
        ]
        if len(usdc_files) > 12:
            usdc_files.sort(key=lambda p: os.path.getmtime(p))
            for old_usdc in usdc_files[:-12]:
                old_json = old_usdc[:-5] + '.json'
                for p in (old_usdc, old_json):
                    if os.path.isfile(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
    except Exception:
        for p in (tmp_usdc, tmp_json):
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


def is_live_hair_node(node):
    hair = node.get('hair')
    return (
        node.get('operation') == 'hair' and isinstance(hair, dict)
        and bool(hair.get('simEnabled')) and hair.get('simMode', 'static') == 'static'
    )


def editable_hair_node_id(graph, selected_id):
    if not graph:
        return None
    node_id = selected_id or graph.get('targetNodeId')
    return next((n['id'] for n in graph.get('nodes', [])
                 if n.get('id') == node_id and is_live_hair_node(n)), None)


def groom_frame_view(request):
    """View settings that must match before reusing a committed brush frame."""
    transient = (
        'graph', 'graphKey', 'hairBrush', 'cacheBrush', 'interactive', 'cameraAnchor', 'pick', 'reload',
        'viewportImageId', 'presentedViewportImageId',
    )
    return {name: value for name, value in request.items() if name not in transient}


def graph_after_groom_commit(graph, node_id, deltas):
    """Expected project graph after accepting a stroke on the previewed Hair node."""
    nodes = graph.get('nodes', [])
    lookup = {node['id']: node for node in nodes}
    target = graph.get('targetNodeId')
    visited = set()
    # Single-input material overlays preserve the curve prim paths, geometry
    # and transforms. Live point edits already reach those same prims.
    geometry_passthrough = ('output', 'camera', 'materialx', 'material_bind')
    while target != node_id:
        if target in visited or lookup.get(target, {}).get('operation') not in geometry_passthrough:
            return None
        visited.add(target)
        inputs = [edge['source'] for edge in graph.get('edges', []) if edge['target'] == target]
        if len(inputs) != 1:
            return None
        target = inputs[0]
    for index, node in enumerate(nodes):
        if node.get('id') == node_id and is_live_hair_node(node):
            hair = dict(node['hair'])
            if deltas:
                hair['groomDeltas'] = deltas
            else:
                hair.pop('groomDeltas', None)
            return {**graph, 'nodes': [*nodes[:index], {**node, 'hair': hair}, *nodes[index + 1:]]}
    return None


def open_hair_preview_stage(stage, graph):
    """Keep interactive opinions out of compiler caches and shared root layers."""
    if not graph or not any(is_live_hair_node(n) for n in graph.get('nodes', [])):
        return stage
    from pxr import Sdf, Usd

    session = Sdf.Layer.CreateAnonymous('hair-preview.usda')
    session.subLayerPaths = [stage.GetSessionLayer().identifier]
    preview = Usd.Stage.OpenMasked(
        stage.GetRootLayer(), session, stage.GetPathResolverContext(),
        stage.GetPopulationMask(), Usd.Stage.LoadNone,
    )
    preview.SetLoadRules(stage.GetLoadRules())
    preview.SetEditTarget(session)
    return preview


def compile_graph(graph, reload=False, *, live_transforms=None, live_poses=None, mx_selections_out=None, interactive_hair=False, _disk_cache_threshold=0.35):
    import time
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdUtils
    compile_start = time.perf_counter()
    if reload:
        _NODE_RESULT_CACHE.clear()
    report_status("OpenUSD: Compiling scene graph...")
    if not isinstance(graph, dict):
        raise ValueError('USD graph must be an object')
    nodes, edges, dependencies = graph.get('nodes', []), graph.get('edges', []), graph.get('dependencies', [])
    if len(nodes) > 10000 or len(edges) > 50000 or len(dependencies) > 100000:
        raise ValueError('USD graph exceeds limits')
    lookup = {n['id']: n for n in nodes}
    files = {d['id']: d['path'] for d in dependencies}
    if len(lookup) != len(nodes) or len(files) != len(dependencies):
        raise ValueError('Duplicate graph node/dependency IDs')
    target = graph.get('targetNodeId')
    if target not in lookup:
        raise ValueError('Select a graph node to preview')
    incoming = {id: [] for id in lookup}
    outgoing = {id: [] for id in lookup}
    degree = {id: 0 for id in lookup}
    edge_ids = set()
    for edge in edges:
        source, destination = edge['source'], edge['target']
        if source not in lookup or destination not in lookup or edge['id'] in edge_ids:
            raise ValueError('Dangling or duplicate USD graph edge')
        edge_ids.add(edge['id'])
        incoming[destination].append(edge)
        outgoing[source].append(destination)
        degree[destination] += 1
    queue = [id for id in lookup if degree[id] == 0]
    order = []
    while queue:
        id = queue.pop()
        order.append(id)
        for destination in outgoing[id]:
            degree[destination] -= 1
            if degree[destination] == 0:
                queue.append(destination)
    if len(order) != len(nodes):
        raise ValueError('USD graph contains a cycle')
    live_node_id = None
    if live_transforms is not None:
        candidate = target
        # Single-input Output and Camera nodes pass through exactly the same stage.
        # Other downstream operations can snapshot or derive data from their inputs.
        while lookup[candidate]['operation'] in ('output', 'camera') and len(incoming[candidate]) == 1:
            candidate = incoming[candidate][0]['source']
        if lookup[candidate]['operation'] == 'transform':
            live_node_id = candidate
            live_transforms[candidate] = []
    needed, stack = set(), [target]
    while stack:
        id = stack.pop()
        if id in needed:
            continue
        needed.add(id)
        # Published application output is an existing external publication, not
        # an adapter execution. Its upstream graph is not executed again here.
        if lookup[id]['operation'] != 'application' or lookup[id].get('liveAdjustment'):
            stack.extend(edge['source'] for edge in incoming[id])
    keepalive, assets, results = [], {}, {}
    override_cache = {}
    live_hair_nodes = {nid for nid in needed if interactive_hair and is_live_hair_node(lookup[nid])}
    can_disk_cache = (
        not live_hair_nodes
        and all(lookup[nid].get('loadPayloads', True) is not False for nid in needed)
        and not any(lookup[nid].get('useAsPayload') for nid in needed)
        and not any(lookup[nid].get('operation') == 'blender' for nid in needed)
        and not any(lookup[nid].get('operation') == 'application' and lookup[nid].get('liveAdjustment') for nid in needed)
    )
    graph_cache_paths = _compiled_graph_cache_paths(graph, needed, lookup, files, target) if can_disk_cache else None
    if reload and graph_cache_paths is not None:
        for p in (graph_cache_paths[1], graph_cache_paths[2]):
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
    elif graph_cache_paths is not None:
        cached_stage, cached_stats, cached_mx = _load_compiled_graph_cache(graph_cache_paths[1], graph_cache_paths[2])
        if cached_stage is not None and cached_stats is not None:
            if live_transforms is not None:
                live_transforms.clear()
            if mx_selections_out is not None and cached_mx:
                mx_selections_out.update(cached_mx)
            return cached_stage, [cached_stage], cached_stats

    def empty_stage(load=Usd.Stage.LoadAll):
        stage = Usd.Stage.CreateInMemory(load=load)
        root = UsdGeom.Xform.Define(stage, '/Scene')
        stage.SetDefaultPrim(root.GetPrim())
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(stage, 1)
        stage.SetTimeCodesPerSecond(24);stage.SetFramesPerSecond(24)
        keepalive.append(stage)
        return stage, root

    def _source_up_rotation(axis_token, source_stage, front_token='preserve'):
        import math
        resolved = axis_token if axis_token and axis_token != 'preserve' else str(UsdGeom.GetStageUpAxis(source_stage))
        rot = Gf.Matrix4d(1)
        if resolved == 'Z':
            rot.SetRotate(Gf.Rotation(Gf.Vec3d(1, 0, 0), -90))
        elif resolved == '-Z':
            rot.SetRotate(Gf.Rotation(Gf.Vec3d(1, 0, 0), 90))
        elif resolved == '-Y':
            rot.SetRotate(Gf.Rotation(Gf.Vec3d(1, 0, 0), 180))
        elif resolved == 'X':
            rot.SetRotate(Gf.Rotation(Gf.Vec3d(0, 0, 1), 90))
        elif resolved == '-X':
            rot.SetRotate(Gf.Rotation(Gf.Vec3d(0, 0, 1), -90))
        elif resolved not in ('Y', 'preserve'):
            raise ValueError(f'Unsupported source up-axis: {resolved}')
        if front_token and front_token != 'preserve':
            axis_dirs = {
                'X': Gf.Vec3d(1, 0, 0), '-X': Gf.Vec3d(-1, 0, 0),
                'Y': Gf.Vec3d(0, 1, 0), '-Y': Gf.Vec3d(0, -1, 0),
                'Z': Gf.Vec3d(0, 0, 1), '-Z': Gf.Vec3d(0, 0, -1),
            }
            if front_token not in axis_dirs:
                raise ValueError(f'Unsupported source front-axis: {front_token}')
            v_up = rot.TransformDir(axis_dirs[front_token])
            if abs(v_up[0]) < 1e-6 and abs(v_up[2]) < 1e-6:
                raise ValueError('Source front axis cannot be parallel to up axis')
            yaw_deg = -math.degrees(math.atan2(v_up[0], v_up[2]))
            front_rot = Gf.Matrix4d(1)
            front_rot.SetRotate(Gf.Rotation(Gf.Vec3d(0, 1, 0), yaw_deg))
            rot = rot * front_rot
        return rot

    def wrap_source_stage(source, instanced=True, norm_path=None, mtime=0.0, axis='preserve', front_axis='preserve', node_scale=1.0, load_payloads=True, use_as_payload=False):
        stage_load = Usd.Stage.LoadAll if load_payloads else Usd.Stage.LoadNone
        roots = list(source.GetPseudoRoot().GetChildren())
        if not roots:
            raise ValueError(f'USD input has no root prims')
        if len(roots) == 1:
            reference_layer, reference_prim = source.GetRootLayer(), roots[0].GetPath()
        else:
            multi_key = (norm_path, bool(load_payloads)) if norm_path else None
            multi_entry = _MULTI_ROOT_CACHE.get(multi_key) if multi_key else None
            if multi_entry and multi_entry[0] == mtime:
                reference_layer, reference_prim = multi_entry[1], multi_entry[2]
                keepalive.append(reference_layer)
            else:
                # A USD reference cannot target the pseudo-root. Normalize a private
                # layer-stack snapshot once per distinct multi-root asset so sibling
                # material bindings stay in the same namespace. References/payloads/
                # variants stay composition arcs; no external layer is edited.
                layer = UsdUtils.FlattenLayerStack(source)
                prepared = Usd.Stage.Open(layer, load=stage_load)
                if any(not local.anonymous for local in prepared.GetLayerStack()):
                    raise ValueError('Multi-root normalization requires private anonymous layers')
                name = '_OrchFlowAsset'
                while prepared.GetPrimAtPath('/' + name):
                    name += '_'
                container = UsdGeom.Xform.Define(prepared, '/' + name)
                for root in roots:
                    editor = Usd.NamespaceEditor(prepared)
                    editor.MovePrimAtPath(root.GetPath(), Sdf.Path('/' + name).AppendChild(root.GetName()))
                    if not editor.CanApplyEdits() or not editor.ApplyEdits():
                        raise ValueError('This multi-root USD cannot be safely namespaced for preview')
                prepared.SetDefaultPrim(container.GetPrim())
                reference_layer, reference_prim = layer, container.GetPath()
                if multi_key:
                    while len(_MULTI_ROOT_CACHE) >= _MAX_MULTI_ROOT_CACHE:
                        _MULTI_ROOT_CACHE.pop(next(iter(_MULTI_ROOT_CACHE)), None)
                    _MULTI_ROOT_CACHE[multi_key] = (mtime, reference_layer, reference_prim)
                keepalive.extend([layer, prepared])
        stage, _ = empty_stage(load=stage_load)
        ratio = stage.GetTimeCodesPerSecond() / source.GetTimeCodesPerSecond()
        stage.SetStartTimeCode(source.GetStartTimeCode() * ratio)
        stage.SetEndTimeCode(source.GetEndTimeCode() * ratio)
        conversion = UsdGeom.Xformable(stage.GetDefaultPrim())
        import math
        if isinstance(node_scale, bool) or not isinstance(node_scale, (int, float)) or not math.isfinite(node_scale) or node_scale <= 0:
            raise ValueError(f'Invalid source scale: {node_scale}')
        scale = Gf.Matrix4d().SetScale(UsdGeom.GetStageMetersPerUnit(source) * float(node_scale))
        rotation = _source_up_rotation(axis, source, front_axis)
        if scale * rotation != Gf.Matrix4d(1):
            conversion.AddTransformOp(opSuffix='sourceUnits').Set(scale * rotation)
        prim = stage.OverridePrim(Sdf.Path('/Scene').AppendChild(reference_prim.name))
        if use_as_payload:
            prim.GetPayloads().AddPayload(reference_layer.identifier, reference_prim)
        else:
            prim.GetReferences().AddReference(reference_layer.identifier, reference_prim)
        mark_reference_instanceable(prim, source, enable=instanced)
        return stage

    def source_stage(dependency_id, instanced=True, axis='preserve', front_axis='preserve', node_scale=1.0, load_payloads=True, use_as_payload=False):
        if not dependency_id or dependency_id not in files:
            raise ValueError('Source USD or published USD file link is missing')
        path = files[dependency_id]
        norm_path = os.path.normcase(os.path.realpath(path))
        ext = os.path.splitext(norm_path)[1].lower()
        if ext in ('.sublayer', '.orchflow'):
            mtime = _sublayer_project_stamp(norm_path) if os.path.exists(norm_path) else 0.0
        else:
            mtime = os.path.getmtime(norm_path) if os.path.exists(norm_path) else 0.0
        load_flag = bool(load_payloads)
        payload_flag = bool(use_as_payload)
        key = (norm_path, bool(instanced), str(axis or 'preserve'), str(front_axis or 'preserve'), float(node_scale), load_flag, payload_flag)
        if key in assets:
            return assets[key]

        cache_key = (norm_path, load_flag)
        source = None
        if reload:
            _SOURCE_STAGE_CACHE.pop(cache_key, None)
            _MULTI_ROOT_CACHE.pop(cache_key, None)
        elif cache_key in _SOURCE_STAGE_CACHE:
            cached_mtime, cached_source = _SOURCE_STAGE_CACHE[cache_key]
            if cached_mtime == mtime:
                source = cached_source

        if source is None:
            source = _open_source_stage(path, reload=reload, load_payloads=load_flag)
            while len(_SOURCE_STAGE_CACHE) >= _MAX_SOURCE_STAGE_CACHE:
                _SOURCE_STAGE_CACHE.pop(next(iter(_SOURCE_STAGE_CACHE)), None)
            _SOURCE_STAGE_CACHE[cache_key] = (mtime, source)
        elif reload:
            if ext in ('.abc', '.obj', '.vdb', '.goz', '.sublayer', '.orchflow'):
                source = _open_source_stage(path, reload=True, load_payloads=load_flag)
                _SOURCE_STAGE_CACHE[cache_key] = (mtime, source)
            else:
                source.Reload()

        keepalive.append(source)
        stage = wrap_source_stage(
            source,
            instanced=instanced,
            norm_path=norm_path,
            mtime=mtime,
            axis=axis,
            front_axis=front_axis,
            node_scale=node_scale,
            load_payloads=load_flag,
            use_as_payload=payload_flag,
        )
        assets[key] = stage
        return stage

    node_hashes = {}

    def _node_cache_sig(nid):
        import json as _json
        n = lookup[nid]
        dep_stamps = _pose_dependency_stamps(n, files)
        for k_name in ('inputDependencyId', 'publishedDependencyId', 'nativeProjectDependencyId'):
            did = n.get(k_name)
            if did and did in files:
                p = files[did]
                np_p = os.path.normcase(os.path.realpath(p))
                if os.path.isfile(np_p):
                    ext = os.path.splitext(np_p)[1].lower()
                    mt = _sublayer_project_stamp(np_p) if ext in ('.sublayer', '.orchflow') else os.path.getmtime(np_p)
                    sz = os.path.getsize(np_p)
                else:
                    mt, sz = 0.0, -1
                dep_stamps.append((did, np_p, mt, sz))
        in_sigs = [(edge['id'], node_hashes.get(edge['source'], edge['source'])) for edge in incoming[nid]]
        extra = graph.get('proceduralGraph') if n.get('operation') == 'materialx' else None
        return hashlib.sha256(_json.dumps({'n': n, 'in': in_sigs, 'deps': dep_stamps, 'pg': extra}, sort_keys=True).encode('utf-8')).hexdigest()

    def _eval_node(id):
        node = lookup[id]
        operation = node['operation']
        if operation == 'application' and node.get('liveAdjustment'):
            branch=dict(graph);branch['targetNodeId']=id
            branch['nodes']=[{**n,'operation':('source' if n.get('inputDependencyId') else 'output'),'liveAdjustment':None,'publication':None,'publishedDependencyId':None} if n['id']==id else n for n in graph['nodes']]
            compose=globals().get('compose_live_adjustment')
            if compose is None:
                from usd_publish import compose_live_adjustment as compose
            results[id],alive=compose(branch,node['liveAdjustment'])
            keepalive.extend(alive);return
        if operation == 'application':
            raise ValueError(f'Application node {id} has no saved live adjustment. Review edited USD, Apply reviewed edits, then save OrchFlow. Disk publications are not used.')
        inputs = [(edge['id'], results[edge['source']]) for edge in incoming[id]]
        if operation == 'source' and node.get('inputDependencyId'):
            instanced = node.get('instanced', True)
            load_payloads = bool(node.get('loadPayloads', True))
            use_as_payload = bool(node.get('useAsPayload', False))
            inputs.insert(
                0,
                (
                    'own-source',
                    source_stage(
                        node['inputDependencyId'],
                        instanced=instanced,
                        axis=node.get('axis', 'preserve'),
                        front_axis=node.get('frontAxis', 'preserve'),
                        node_scale=node.get('scale', 1.0),
                        load_payloads=load_payloads,
                        use_as_payload=use_as_payload,
                    ),
                ),
            )
        elif operation == 'blender':
            blend_dep = node.get('nativeProjectDependencyId')
            usd_dep = node.get('publishedDependencyId') or node.get('inputDependencyId')
            instanced = bool(node.get('instanced', False))
            blend_path = (files.get(blend_dep) if blend_dep else None) or (node.get('nativePath') or '').strip()
            selections = node.get('blenderSelections')
            if isinstance(selections, list) and len(selections) == 0:
                if not inputs:
                    stage, _ = empty_stage()
                    results[id] = stage
                    return
            elif blend_path:
                if not os.path.isfile(blend_path):
                    raise ValueError(f'Blender file does not exist: {blend_path}')
                mtime = os.path.getmtime(blend_path)
                sel_key = None if selections is None else tuple(sorted(str(x) for x in selections))
                material_mode = node.get('blenderMaterialMode') or 'off'
                convert_procedurals = bool(node.get('blenderConvertProcedurals', False))
                export_skeletons = bool(node.get('blenderExportSkeletons', False))
                scene_name = node.get('blenderScene') or None
                node_scale = node.get('scale', 1.0)
                asset_key_tuple = ('blender', os.path.normcase(os.path.abspath(blend_path)), mtime, sel_key, instanced, material_mode, convert_procedurals, export_skeletons, scene_name, float(node_scale))
                if asset_key_tuple in assets:
                    wrapped = assets[asset_key_tuple]
                else:
                    est_secs = node.get('lastExtractionSeconds')
                    b_stage = get_or_extract_blender_memory_stage(blend_path, selections, reload=reload, material_mode=material_mode, est_seconds=est_secs, convert_procedurals=convert_procedurals, export_skeletons=export_skeletons, scene_name=scene_name)
                    keepalive.append(b_stage)
                    wrapped = wrap_source_stage(b_stage, instanced=instanced, node_scale=node_scale)
                    assets[asset_key_tuple] = wrapped
                inputs.insert(0, ('own-source', wrapped))
            elif usd_dep and usd_dep in files:
                inputs.insert(0, ('own-source', source_stage(usd_dep, instanced=instanced, node_scale=node.get('scale', 1.0))))
            elif not inputs:
                raise ValueError(f'Blender node "{node.get("label", id)}" needs a linked .blend file.')
        elif operation not in ('source', 'blender', 'transform', 'group', 'output', 'assetOverride', 'materialx', 'material_bind', 'light', 'hdri', 'hair', 'cache_edit', 'camera', 'delete', 'scatter', 'character', 'pose'):
            raise ValueError(f'Unsupported USD graph operation: {operation}')
        if not inputs:
            raise ValueError(f'{operation.title()} node needs a linked USD file or incoming USD connection')
        if operation == 'pose':
            if len(inputs) != 1:
                raise ValueError('Pose needs one upstream stage; combine inputs with Output first')
            upstream = inputs[0][1]
            overlay = Sdf.Layer.CreateAnonymous('pose.usda')
            overlay.subLayerPaths = [upstream.GetRootLayer().identifier]
            stage = Usd.Stage.Open(overlay)
            inherit_stage_metrics(stage, upstream)
            stage.SetDefaultPrim(stage.GetPrimAtPath(upstream.GetDefaultPrim().GetPath()))
            keepalive.extend([upstream, overlay, stage])
            from pose_node import apply_pose
            apply_pose(stage, node, files, keepalive, reload, live_poses=live_poses)
            results[id] = stage
            return
        if operation == 'character':
            stage, alive_char = compose_character_node(inputs, node, files=files, reload=reload, wrap_fn=wrap_source_stage)
            keepalive.extend(alive_char)
            results[id] = stage
            return
        if operation == 'assetOverride':
            inputs = [(edge, override_stage(stage, node.get('assetOverrides', []), files, keepalive, override_cache)) for edge, stage in inputs]
        if operation == 'material_bind':
            stage, alive_bind = compose_material_binding_node(inputs, node)
            keepalive.extend(alive_bind)
            results[id] = stage
            return
        if operation == 'light' and len(inputs)==1:
            upstream = inputs[0][1]
            overlay = Sdf.Layer.CreateAnonymous('light.usda')
            overlay.subLayerPaths = [upstream.GetRootLayer().identifier]
            stage = Usd.Stage.Open(overlay)
            inherit_stage_metrics(stage, upstream)
            stage.SetDefaultPrim(stage.GetPrimAtPath(upstream.GetDefaultPrim().GetPath()))
            apply_light_overrides(stage, node.get('lightOverrides', []))
            keepalive.extend([upstream, overlay, stage])
            results[id] = stage
            return
        if operation == 'camera' and len(inputs) == 1:
            results[id] = inputs[0][1]
            return
        if operation == 'hdri' and len(inputs)==1:
            upstream = inputs[0][1]
            overlay = Sdf.Layer.CreateAnonymous('hdri.usda')
            overlay.subLayerPaths = [upstream.GetRootLayer().identifier]
            stage = Usd.Stage.Open(overlay)
            inherit_stage_metrics(stage, upstream)
            stage.SetDefaultPrim(stage.GetPrimAtPath(upstream.GetDefaultPrim().GetPath()))
            apply_hdri(stage, node.get('hdri'), files)
            keepalive.extend([upstream, overlay, stage])
            results[id] = stage
            return
        if operation == 'hair' and len(inputs) == 1:
            upstream = inputs[0][1]
            overlay = Sdf.Layer.CreateAnonymous('hair.usda')
            overlay.subLayerPaths = [upstream.GetRootLayer().identifier]
            stage = Usd.Stage.Open(overlay)
            inherit_stage_metrics(stage, upstream)
            stage.SetDefaultPrim(stage.GetPrimAtPath(upstream.GetDefaultPrim().GetPath()))
            apply_hair(stage, node.get('hair'))
            keepalive.extend([upstream, overlay, stage])
            results[id] = stage
            return
        if operation == 'cache_edit' and len(inputs) == 1:
            upstream = inputs[0][1]
            overlay = Sdf.Layer.CreateAnonymous('cache_edit_root.usda')
            overlay.subLayerPaths = [upstream.GetRootLayer().identifier]
            stage = Usd.Stage.Open(overlay)
            inherit_stage_metrics(stage, upstream)
            stage.SetDefaultPrim(stage.GetPrimAtPath(upstream.GetDefaultPrim().GetPath()))
            apply_cache_edit(stage, node.get('cacheEdit'))
            keepalive.extend([upstream, overlay, stage])
            results[id] = stage
            return
        if operation == 'materialx' and len(inputs)==1:
            upstream=inputs[0][1]
            overlay=Sdf.Layer.CreateAnonymous('materialx.usda')
            overlay.subLayerPaths=[upstream.GetRootLayer().identifier]
            stage=Usd.Stage.Open(overlay)
            inherit_stage_metrics(stage,upstream)
            stage.SetDefaultPrim(stage.GetPrimAtPath(upstream.GetDefaultPrim().GetPath()))
            sel_out = {} if mx_selections_out is not None else None
            author_materialx_rules(stage,node,files,procedural_graph=graph.get('proceduralGraph'),selected_out=sel_out)
            if mx_selections_out is not None and node.get('materialXRules') is not None:
                mx_selections_out[id] = sel_out
            keepalive.extend([upstream,overlay,stage])
            results[id]=stage
            return
        if operation == 'delete' and len(inputs)==1:
            upstream=inputs[0][1]
            overlay=Sdf.Layer.CreateAnonymous('delete.usda');overlay.subLayerPaths=[upstream.GetRootLayer().identifier]
            stage=Usd.Stage.Open(overlay);keepalive.extend([overlay,stage,upstream])
            inherit_stage_metrics(stage,upstream)
            stage.SetDefaultPrim(stage.GetPrimAtPath(upstream.GetDefaultPrim().GetPath()))
            apply_delete(stage,node.get('deletePaths',[]))
            results[id]=stage;return
        if operation=='transform' and len(inputs)==1:
            upstream=inputs[0][1]
            overlay=Sdf.Layer.CreateAnonymous('transform.usda');overlay.subLayerPaths=[upstream.GetRootLayer().identifier]
            stage=Usd.Stage.Open(overlay);inherit_stage_metrics(stage,upstream)
            stage.SetDefaultPrim(stage.GetPrimAtPath(upstream.GetDefaultPrim().GetPath()))
            apply_transform(stage,node,stage.GetDefaultPrim(),live_transforms[id] if id == live_node_id else None)
            keepalive.extend([upstream,overlay,stage]);results[id]=stage;return
        if operation in ('source', 'blender', 'assetOverride', 'output') and len(inputs) == 1:
            results[id] = inputs[0][1]
            return
        if operation == 'group':
            import re
            raw_name = str(node.get('groupName') or '').strip()
            sanitized = re.sub(r'[^A-Za-z0-9_]', '_', raw_name) if raw_name else 'Group'
            if not sanitized or not (sanitized[0].isalpha() or sanitized[0] == '_'):
                sanitized = '_' + sanitized
            stage, root = empty_stage()
            stage.SetStartTimeCode(min(value.GetStartTimeCode() for _, value in inputs))
            stage.SetEndTimeCode(max(value.GetEndTimeCode() for _, value in inputs))
            group_path = f'/Scene/{sanitized}'
            group_xform = UsdGeom.Xform.Define(stage, group_path)
            for idx, (edge_id, upstream) in enumerate(inputs):
                child_name = 'Input' if len(inputs) == 1 else 'Branch_' + hashlib.sha256(edge_id.encode()).hexdigest()[:16]
                child_prim = stage.OverridePrim(f'{group_path}/{child_name}')
                default_prim = upstream.GetDefaultPrim()
                target_path = default_prim.GetPath() if (default_prim and default_prim.IsValid()) else Sdf.Path('/Scene')
                child_prim.GetReferences().AddReference(upstream.GetRootLayer().identifier, target_path)
                has_instances = any(p.IsInstanceable() or p.IsInstance() for p in upstream.Traverse())
                mark_reference_instanceable(child_prim, upstream, enable=node.get('instanced', True) and has_instances)
            apply_transform(stage, node, group_xform.GetPrim(), live_transforms[id] if id == live_node_id else None)
            keepalive.append(stage)
            keepalive.extend(u for _, u in inputs)
            results[id] = stage
            return
        if operation == 'scatter':
            if not inputs:
                raise ValueError('Connect at least one upstream stage to Scatter')
            pass_through = bool((node.get('scatter') or {}).get('passThrough', False))
            if pass_through and len(inputs) == 1:
                upstream = inputs[0][1]
                overlay = Sdf.Layer.CreateAnonymous('scatter.usda')
                overlay.subLayerPaths = [upstream.GetRootLayer().identifier]
                stage = Usd.Stage.Open(overlay)
                inherit_stage_metrics(stage, upstream)
                default_prim = upstream.GetDefaultPrim()
                if default_prim and default_prim.IsValid():
                    stage.SetDefaultPrim(stage.GetPrimAtPath(default_prim.GetPath()))
                keepalive.extend([overlay, stage, upstream])
            else:
                stage, root = empty_stage()
                stage.SetStartTimeCode(min(value.GetStartTimeCode() for _, value in inputs))
                stage.SetEndTimeCode(max(value.GetEndTimeCode() for _, value in inputs))
                if pass_through:
                    for edge_id, upstream in inputs:
                        name = 'Branch_' + hashlib.sha256(edge_id.encode()).hexdigest()[:16]
                        prim = stage.OverridePrim('/Scene/' + name)
                        prim.GetReferences().AddReference(upstream.GetRootLayer().identifier, upstream.GetDefaultPrim().GetPath())
                        has_instances = any(p.IsInstanceable() or p.IsInstance() for p in upstream.Traverse())
                        mark_reference_instanceable(prim, upstream, enable=node.get('instanced', True) and has_instances)
                keepalive.append(stage)
            apply_scatter(stage, node, inputs)
            results[id] = stage
            return
        stage, root = empty_stage()
        stage.SetStartTimeCode(min(value.GetStartTimeCode() for _,value in inputs));stage.SetEndTimeCode(max(value.GetEndTimeCode() for _,value in inputs))
        for edge_id, upstream in inputs:
            name = 'Branch_' + hashlib.sha256(edge_id.encode()).hexdigest()[:16]
            prim = stage.OverridePrim('/Scene/' + name)
            prim.GetReferences().AddReference(upstream.GetRootLayer().identifier, upstream.GetDefaultPrim().GetPath())
            # Transform ops live on the enclosing Scene prim. Descendants remain
            # read-only instance proxies unless upstream has uninstanced prims or node disabled instancing.
            has_instances = any(p.IsInstanceable() or p.IsInstance() for p in upstream.Traverse())
            mark_reference_instanceable(prim, upstream, enable=node.get('instanced', True) and has_instances)
        if operation == 'transform':
            apply_transform(stage,node,root,live_transforms[id] if id == live_node_id else None)
        if operation == 'delete':apply_delete(stage,node.get('deletePaths',[]))
        if operation == 'materialx':
            sel_out = {} if mx_selections_out is not None else None
            author_materialx_rules(stage,node,files,procedural_graph=graph.get('proceduralGraph'),selected_out=sel_out)
            if mx_selections_out is not None and node.get('materialXRules') is not None:
                mx_selections_out[id] = sel_out
        if operation == 'light':apply_light_overrides(stage,node.get('lightOverrides',[]))
        if operation == 'hdri':apply_hdri(stage,node.get('hdri'),files)
        if operation == 'hair':apply_hair(stage,node.get('hair'))
        if operation == 'cache_edit':apply_cache_edit(stage,node.get('cacheEdit'))
        results[id] = stage

    for id in order:
        if id not in needed:
            continue
        node = lookup[id]
        op = node.get('operation')
        skip_sim_active = bool(globals().get('_SKIP_SIMULATION', False))
        can_node_cache = (
            id != live_node_id
            and id not in live_hair_nodes
            and op not in ('application', 'assetOverride', 'light', 'hdri')
            and not (live_poses is not None and op == 'pose')
            and not skip_sim_active
            and not (mx_selections_out is not None and node.get('materialXRules') is not None)
        )
        sig = _node_cache_sig(id)
        node_hashes[id] = sig
        if can_node_cache and not reload and sig in _NODE_RESULT_CACHE:
            c_stage, c_keep = _NODE_RESULT_CACHE.pop(sig)
            _NODE_RESULT_CACHE[sig] = (c_stage, c_keep)
            results[id] = c_stage
            keepalive.extend(c_keep)
            continue
        before_keep = len(keepalive)
        _eval_node(id)
        if can_node_cache:
            while len(_NODE_RESULT_CACHE) >= _MAX_NODE_RESULT_CACHE:
                _NODE_RESULT_CACHE.pop(next(iter(_NODE_RESULT_CACHE)), None)
            _NODE_RESULT_CACHE[sig] = (results[id], keepalive[before_keep:])
    result = results[target]
    point_instance_count = 0
    scene_instances = 0
    point_proto_count = 0
    for prim in result.Traverse():
        if prim.IsInstance():
            scene_instances += 1
        elif prim.IsA(UsdGeom.PointInstancer):
            inst_schema = UsdGeom.PointInstancer(prim)
            indices = inst_schema.GetProtoIndicesAttr().Get()
            if indices:
                point_instance_count += len(indices)
            point_proto_count += len(inst_schema.GetPrototypesRel().GetTargets())
    stats = {'evaluatedNodes': len(results), 'uniqueSourceAssets': len(assets),
             'prototypes': len(result.GetPrototypes()) + point_proto_count,
             'instances': scene_instances + point_instance_count}
    if graph_cache_paths is not None and (time.perf_counter() - compile_start) >= _disk_cache_threshold:
        _save_compiled_graph_cache(graph_cache_paths[0], graph_cache_paths[1], graph_cache_paths[2], result, stats, mx_selections_out)
    return result, keepalive, stats


def _filter_min_spacing(positions, normals, min_spacing, max_count):
    import math
    import numpy as np

    if min_spacing <= 0.0 or len(positions) == 0:
        if len(positions) > max_count:
            positions = positions[:max_count]
            if normals is not None:
                normals = normals[:max_count]
        return positions, normals

    inv_cell = 1.0 / min_spacing
    min_sq = min_spacing * min_spacing
    grid = {}
    kept_idx = []
    for i, pos in enumerate(positions):
        px, py, pz = float(pos[0]), float(pos[1]), float(pos[2])
        cx = math.floor(px * inv_cell)
        cy = math.floor(py * inv_cell)
        cz = math.floor(pz * inv_cell)
        too_close = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    bucket = grid.get((cx + dx, cy + dy, cz + dz))
                    if not bucket:
                        continue
                    for ox, oy, oz in bucket:
                        ddx = px - ox
                        ddy = py - oy
                        ddz = pz - oz
                        if ddx * ddx + ddy * ddy + ddz * ddz < min_sq:
                            too_close = True
                            break
                    if too_close:
                        break
                if too_close:
                    break
            if too_close:
                break
        if not too_close:
            grid.setdefault((cx, cy, cz), []).append((px, py, pz))
            kept_idx.append(i)
            if len(kept_idx) >= max_count:
                break

    idx_arr = np.asarray(kept_idx, dtype=np.int64)
    return positions[idx_arr], (normals[idx_arr] if normals is not None else None)


def _sample_surface_points(surface_stages, surface_prim_path, max_slope_deg, up_vec, sample_count, rng):
    import numpy as np
    from pxr import Sdf, Usd, UsdGeom

    target_path = None
    match_name = None
    if surface_prim_path:
        if surface_prim_path.startswith('/') and Sdf.Path.IsValidPathString(surface_prim_path):
            target_path = Sdf.Path(surface_prim_path)
        else:
            match_name = surface_prim_path

    all_v0 = []
    all_v1 = []
    all_v2 = []

    for s_stage in surface_stages:
        xf_cache = UsdGeom.XformCache()
        for prim in s_stage.Traverse(Usd.TraverseInstanceProxies()):
            if not prim.IsA(UsdGeom.Mesh):
                continue
            if target_path is not None:
                p_path = prim.GetPath()
                if p_path != target_path and not p_path.HasPrefix(target_path):
                    continue
            elif match_name is not None and prim.GetName() != match_name:
                continue

            mesh = UsdGeom.Mesh(prim)
            pts_vt = mesh.GetPointsAttr().Get()
            counts_vt = mesh.GetFaceVertexCountsAttr().Get()
            indices_vt = mesh.GetFaceVertexIndicesAttr().Get()
            if not pts_vt or not counts_vt or not indices_vt:
                continue

            pts = np.asarray(pts_vt, dtype=np.float64)
            if pts.ndim != 2 or pts.shape[0] == 0:
                continue
            mat = np.asarray(xf_cache.GetLocalToWorldTransform(prim), dtype=np.float64)
            pts_w = pts @ mat[:3, :3] + mat[3, :3]

            counts = np.asarray(counts_vt, dtype=np.int64)
            indices = np.asarray(indices_vt, dtype=np.int64)
            if np.all(counts == 3):
                tris = indices.reshape(-1, 3)
            elif np.all(counts == 4):
                quads = indices.reshape(-1, 4)
                tris = np.vstack((quads[:, [0, 1, 2]], quads[:, [0, 2, 3]]))
            else:
                tri_list = []
                offset = 0
                for c in counts:
                    if c >= 3:
                        i0 = indices[offset]
                        for k in range(1, c - 1):
                            tri_list.append((i0, indices[offset + k], indices[offset + k + 1]))
                    offset += c
                if not tri_list:
                    continue
                tris = np.asarray(tri_list, dtype=np.int64)

            if mesh.GetOrientationAttr().Get() == UsdGeom.Tokens.leftHanded:
                tris = tris[:, [0, 2, 1]]

            all_v0.append(pts_w[tris[:, 0]])
            all_v1.append(pts_w[tris[:, 1]])
            all_v2.append(pts_w[tris[:, 2]])

    if not all_v0:
        return np.zeros((0, 3), dtype=np.float32), np.zeros((0, 3), dtype=np.float32)

    v0 = np.vstack(all_v0)
    v1 = np.vstack(all_v1)
    v2 = np.vstack(all_v2)

    e1 = v1 - v0
    e2 = v2 - v0
    cross = np.cross(e1, e2)
    area2 = np.linalg.norm(cross, axis=1)
    valid = area2 > 1e-12
    if not np.any(valid):
        return np.zeros((0, 3), dtype=np.float32), np.zeros((0, 3), dtype=np.float32)

    v0 = v0[valid]
    v1 = v1[valid]
    v2 = v2[valid]
    area2 = area2[valid]
    normals = cross[valid] / area2[:, None]

    cos_angle = np.clip(normals @ up_vec, -1.0, 1.0)
    slope_deg = np.degrees(np.arccos(cos_angle))
    slope_mask = slope_deg <= max_slope_deg
    if not np.any(slope_mask):
        return np.zeros((0, 3), dtype=np.float32), np.zeros((0, 3), dtype=np.float32)

    v0 = v0[slope_mask]
    v1 = v1[slope_mask]
    v2 = v2[slope_mask]
    normals = normals[slope_mask]
    weights = area2[slope_mask]

    cdf = np.cumsum(weights)
    cdf /= cdf[-1]
    u = rng.uniform(0.0, 1.0, size=sample_count)
    tri_idx = np.searchsorted(cdf, u, side='right')
    tri_idx = np.minimum(tri_idx, len(v0) - 1)

    r1 = np.sqrt(rng.uniform(0.0, 1.0, size=sample_count))
    r2 = rng.uniform(0.0, 1.0, size=sample_count)
    b0 = (1.0 - r1)[:, None]
    b1 = (r1 * (1.0 - r2))[:, None]
    b2 = (r1 * r2)[:, None]

    pos = (b0 * v0[tri_idx] + b1 * v1[tri_idx] + b2 * v2[tri_idx]).astype(np.float32)
    nrm = normals[tri_idx].astype(np.float32)
    return pos, nrm


def _resolve_scatter_prototype_prim(search_inputs, prim_path_str):
    from pxr import Sdf
    try:
        sdf_path = Sdf.Path(prim_path_str)
    except Exception as exc:
        raise ValueError(f'Invalid scatter prototype prim path: {prim_path_str}') from exc
    if not sdf_path.IsAbsolutePath() or not sdf_path.IsPrimPath() or sdf_path == Sdf.Path.absoluteRootPath:
        raise ValueError(f'Scatter prototype must be an absolute prim path: {prim_path_str}')

    for _, upstream in search_inputs:
        prim = upstream.GetPrimAtPath(sdf_path)
        if prim and prim.IsValid() and prim.IsActive():
            if prim.IsInstanceProxy():
                raise ValueError(
                    f'Cannot scatter instance-proxy prim directly: {prim_path_str}. Select its containing instance instead.'
                )
            return upstream, prim

    for edge_id, upstream in search_inputs:
        branch_prefix = '/Scene/Branch_' + hashlib.sha256(edge_id.encode()).hexdigest()[:16]
        if prim_path_str == branch_prefix or prim_path_str.startswith(branch_prefix + '/'):
            default_prim = upstream.GetDefaultPrim()
            base_path = str(default_prim.GetPath()) if (default_prim and default_prim.IsValid()) else ''
            rest = prim_path_str[len(branch_prefix):]
            mapped_str = (base_path + rest) if base_path else (rest or '/')
            mapped_path = Sdf.Path(mapped_str)
            prim = upstream.GetPrimAtPath(mapped_path)
            if prim and prim.IsValid() and prim.IsActive():
                if prim.IsInstanceProxy():
                    raise ValueError(
                        f'Cannot scatter instance-proxy prim directly: {prim_path_str}. Select its containing instance instead.'
                    )
                return upstream, prim

    raise ValueError(f'Scatter prototype prim not found in input: {prim_path_str}')


def apply_scatter(stage, node, inputs):
    import math
    import numpy as np
    from pxr import Gf, Sdf, UsdGeom, Vt

    cfg = node.get('scatter') or {}
    count = max(1, min(1_000_000, int(cfg.get('count', 100))))
    seed = max(0, int(cfg.get('seed', 42)))
    distribution = cfg.get('distribution', 'random')
    area = cfg.get('areaSize') or [20.0, 0.0, 20.0]
    ax = max(0.0, float(area[0])) if len(area) > 0 else 20.0
    ay = max(0.0, float(area[1])) if len(area) > 1 else 0.0
    az = max(0.0, float(area[2])) if len(area) > 2 else 20.0
    scale_min = max(0.01, float(cfg.get('scaleMin', 0.8)))
    scale_max = max(scale_min, float(cfg.get('scaleMax', 1.2)))
    random_yaw = bool(cfg.get('randomYaw', True))
    surface_prim_path = str(cfg.get('surfacePrimPath') or '').strip()
    align_to_normal = bool(cfg.get('alignToNormal', False))
    max_slope_deg = max(0.0, min(180.0, float(cfg.get('maxSlopeDeg', 90.0))))
    min_spacing = max(0.0, float(cfg.get('minSpacing', 0.0)))

    if distribution == 'surface' and not surface_prim_path and len(inputs) >= 2:
        proto_inputs = inputs[:-1]
        surface_stages = [inputs[-1][1]]
    else:
        proto_inputs = inputs
        surface_stages = [u for _, u in inputs]

    raw_prototypes = cfg.get('prototypes') or []
    instancer = UsdGeom.PointInstancer.Define(stage, '/Scene/Scatter')
    UsdGeom.Scope.Define(stage, '/Scene/Scatter/Prototypes')
    proto_paths = []
    if raw_prototypes:
        enabled_paths = [
            str(item.get('primPath') or '').strip()
            for item in raw_prototypes
            if isinstance(item, dict) and item.get('enabled', True) and str(item.get('primPath') or '').strip()
        ]
        if not enabled_paths:
            raise ValueError('Enable at least one prototype prim in Scatter, or clear the prototype table')
        search_inputs = proto_inputs + [item for item in inputs if item not in proto_inputs]
        for idx, prim_path_str in enumerate(enabled_paths):
            target_upstream, target_prim = _resolve_scatter_prototype_prim(search_inputs, prim_path_str)
            proto_path = Sdf.Path(f'/Scene/Scatter/Prototypes/Proto_{idx}')
            proto_prim = stage.OverridePrim(proto_path)
            proto_prim.GetReferences().AddReference(target_upstream.GetRootLayer().identifier, target_prim.GetPath())
            proto_paths.append(proto_path)
    else:
        for idx, (_, upstream) in enumerate(proto_inputs):
            proto_path = Sdf.Path(f'/Scene/Scatter/Prototypes/Proto_{idx}')
            proto_prim = stage.OverridePrim(proto_path)
            default_prim = upstream.GetDefaultPrim()
            if default_prim and default_prim.IsValid():
                proto_prim.GetReferences().AddReference(upstream.GetRootLayer().identifier, default_prim.GetPath())
            else:
                children = list(upstream.GetPseudoRoot().GetChildren())
                if children:
                    proto_prim.GetReferences().AddReference(upstream.GetRootLayer().identifier, children[0].GetPath())
            proto_paths.append(proto_path)
    instancer.CreatePrototypesRel().SetTargets(proto_paths)

    up_is_z = UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
    up_vec = np.array([0.0, 0.0, 1.0], dtype=np.float64) if up_is_z else np.array([0.0, 1.0, 0.0], dtype=np.float64)

    rng = np.random.default_rng(seed)
    normals = None
    if distribution == 'surface':
        sample_n = min(count * 4, 250_000) if min_spacing > 0.0 else count
        positions, normals = _sample_surface_points(
            surface_stages, surface_prim_path, max_slope_deg, up_vec, sample_n, rng
        )
        if min_spacing > 0.0:
            positions, normals = _filter_min_spacing(positions, normals, min_spacing, count)
    elif distribution == 'grid':
        cols = max(1, int(math.ceil(math.sqrt(count))))
        rows = max(1, int(math.ceil(count / cols)))
        gx = np.linspace(-ax * 0.5, ax * 0.5, cols, dtype=np.float32) if cols > 1 else np.zeros(1, dtype=np.float32)
        gz = np.linspace(-az * 0.5, az * 0.5, rows, dtype=np.float32) if rows > 1 else np.zeros(1, dtype=np.float32)
        xx, zz = np.meshgrid(gx, gz)
        px = xx.ravel()[:count]
        pz = zz.ravel()[:count]
        py = rng.uniform(-ay * 0.5, ay * 0.5, size=count).astype(np.float32) if ay > 0 else np.zeros(count, dtype=np.float32)
        positions = np.column_stack((px, py, pz))
    elif distribution == 'circle':
        radii = np.sqrt(rng.uniform(0.0, 1.0, size=count)).astype(np.float32)
        angles = rng.uniform(0.0, 2.0 * math.pi, size=count).astype(np.float32)
        px = radii * np.cos(angles) * (ax * 0.5)
        pz = radii * np.sin(angles) * (az * 0.5)
        py = rng.uniform(-ay * 0.5, ay * 0.5, size=count).astype(np.float32) if ay > 0 else np.zeros(count, dtype=np.float32)
        positions = np.column_stack((px, py, pz))
    else:
        sample_n = min(count * 4, 250_000) if min_spacing > 0.0 else count
        px = rng.uniform(-ax * 0.5, ax * 0.5, size=sample_n).astype(np.float32)
        py = rng.uniform(-ay * 0.5, ay * 0.5, size=sample_n).astype(np.float32) if ay > 0 else np.zeros(sample_n, dtype=np.float32)
        pz = rng.uniform(-az * 0.5, az * 0.5, size=sample_n).astype(np.float32)
        positions = np.column_stack((px, py, pz))
        if min_spacing > 0.0:
            positions, _ = _filter_min_spacing(positions, None, min_spacing, count)

    actual_count = len(positions)
    proto_indices = rng.integers(0, len(proto_paths), size=actual_count, dtype=np.int32)
    uniform_scales = rng.uniform(scale_min, scale_max, size=actual_count).astype(np.float32)
    scales = np.column_stack((uniform_scales, uniform_scales, uniform_scales)) if actual_count > 0 else np.zeros((0, 3), dtype=np.float32)

    if random_yaw and actual_count > 0:
        half_angles = rng.uniform(0.0, math.pi, size=actual_count).astype(np.float32)
        qw_y = np.cos(half_angles)
        q_sin_y = np.sin(half_angles)
    else:
        qw_y = np.ones(actual_count, dtype=np.float32)
        q_sin_y = np.zeros(actual_count, dtype=np.float32)

    zeros = np.zeros(actual_count, dtype=np.float32)
    qx_y, qy_y, qz_y = (zeros, zeros, q_sin_y) if up_is_z else (zeros, q_sin_y, zeros)

    if align_to_normal and normals is not None and actual_count > 0:
        n64 = normals.astype(np.float64)
        dots = np.clip(n64 @ up_vec, -1.0, 1.0)
        axes = np.cross(up_vec, n64)
        qw_a = 1.0 + dots
        qx_a, qy_a, qz_a = axes[:, 0], axes[:, 1], axes[:, 2]
        anti = dots < -0.9999
        if np.any(anti):
            qx_a[anti] = 1.0
            qy_a[anti] = 0.0
            qz_a[anti] = 0.0
            qw_a[anti] = 0.0
        norm_a = np.sqrt(qx_a * qx_a + qy_a * qy_a + qz_a * qz_a + qw_a * qw_a)
        norm_a = np.maximum(norm_a, 1e-12)
        qx_a /= norm_a
        qy_a /= norm_a
        qz_a /= norm_a
        qw_a /= norm_a

        # Hamilton product q_align * q_yaw
        qx = qw_a * qx_y + qx_a * qw_y + qy_a * qz_y - qz_a * qy_y
        qy = qw_a * qy_y - qx_a * qz_y + qy_a * qw_y + qz_a * qx_y
        qz = qw_a * qz_y + qx_a * qy_y - qy_a * qx_y + qz_a * qw_y
        qw = qw_a * qw_y - qx_a * qx_y - qy_a * qy_y - qz_a * qz_y
    else:
        qx, qy, qz, qw = qx_y, qy_y, qz_y, qw_y

    # Gf.Quath memory layout for FromNumpy is (imag_x, imag_y, imag_z, real_w).
    orientations_np = np.column_stack((qx, qy, qz, qw)).astype(np.float16) if actual_count > 0 else np.zeros((0, 4), dtype=np.float16)

    instancer.CreateProtoIndicesAttr().Set(Vt.IntArray.FromNumpy(proto_indices))
    instancer.CreatePositionsAttr().Set(Vt.Vec3fArray.FromNumpy(positions))
    instancer.CreateScalesAttr().Set(Vt.Vec3fArray.FromNumpy(scales))
    if hasattr(Vt.QuathArray, 'FromNumpy'):
        try:
            instancer.CreateOrientationsAttr().Set(Vt.QuathArray.FromNumpy(orientations_np))
        except Exception:
            instancer.CreateOrientationsAttr().Set(Vt.QuathArray([Gf.Quath(float(w), float(x), float(y), float(z)) for x, y, z, w in zip(qx, qy, qz, qw)]))
    else:
        instancer.CreateOrientationsAttr().Set(Vt.QuathArray([Gf.Quath(float(w), float(x), float(y), float(z)) for x, y, z, w in zip(qx, qy, qz, qw)]))


def normalize_handoff_selection(selection):
    from pxr import Sdf
    if selection is None:return None
    if not isinstance(selection,list) or not selection or len(selection)>1000:raise ValueError('Select between 1 and 1000 handoff object roots')
    paths=[]
    for value in selection:
        if not isinstance(value,str):raise ValueError('Handoff selection must contain prim paths')
        path=Sdf.Path(value)
        if not path.IsAbsolutePath() or not path.IsPrimPath() or path==Sdf.Path.absoluteRootPath:raise ValueError('Handoff selection needs absolute object paths, not the scene pseudo-root')
        paths.append(path)
    roots=[]
    for path in sorted(set(paths),key=lambda p:(str(p).count('/'),str(p))):
        if not any(path.HasPrefix(parent) for parent in roots):roots.append(path)
    return [str(path) for path in roots]


def selected_handoff_input(upstream,selection):
    """Prune export-only composition; keep namespaces, ancestors and required
    material/rig/connection targets. The live graph and upstream stay intact."""
    from pxr import Sdf,Usd,UsdGeom,UsdShade
    roots={Sdf.Path(p) for p in normalize_handoff_selection(selection)}
    for path in roots:
        prim=upstream.GetPrimAtPath(path)
        if not prim or not prim.IsActive():raise ValueError('Handoff object no longer exists: '+str(path))
        if prim.IsInstanceProxy():raise ValueError('Select the containing instance root instead of its proxy child: '+str(path))
    all_prims=list(upstream.Traverse(Usd.TraverseInstanceProxies()))
    if len(all_prims)>100000:raise ValueError('Handoff selection exceeds the 100,000-prim dependency budget')
    inspected=set()
    def covered(path):return any(path.HasPrefix(root) for root in roots)
    def dependency(path):
        prim=upstream.GetPrimAtPath(path.GetPrimPath())
        if not prim:return
        while prim.IsInstanceProxy():prim=prim.GetParent()
        target=prim.GetPath()
        if not covered(target):roots.add(target)
    while True:
        pending=[p for p in all_prims if covered(p.GetPath()) and p.GetPath() not in inspected]
        if not pending:break
        for prim in pending:
            inspected.add(prim.GetPath())
            if prim.IsA(UsdGeom.Gprim) or prim.IsA(UsdGeom.Subset):
                api=UsdShade.MaterialBindingAPI(prim)
                for purpose in ('','preview','full'):
                    material,_=api.ComputeBoundMaterial(purpose)
                    if material:dependency(material.GetPath())
            for relation in prim.GetRelationships():
                name=relation.GetName()
                if name.startswith(('material:binding','collection:')):continue
                for target in relation.GetForwardedTargets():dependency(target)
            for attr in prim.GetAttributes():
                for target in attr.GetConnections():dependency(target)
            # Rig bindings may be authored on the selected object's ancestors.
            parent=prim.GetParent()
            while parent and not parent.IsPseudoRoot():
                for relation in parent.GetRelationships():
                    if relation.GetName().startswith('skel:'):
                        for target in relation.GetForwardedTargets():dependency(target)
                parent=parent.GetParent()
    layer=Sdf.Layer.CreateAnonymous('selected-handoff.usda');layer.subLayerPaths=[upstream.GetRootLayer().identifier]
    result=Usd.Stage.Open(layer);inherit_stage_metrics(result,upstream)
    result.SetDefaultPrim(result.GetPrimAtPath(upstream.GetDefaultPrim().GetPath()))
    def retained(path):return any(path.HasPrefix(root) or root.HasPrefix(path) for root in roots)
    for prim in upstream.Traverse():
        path=prim.GetPath()
        if not retained(path) and (prim.GetParent().IsPseudoRoot() or retained(path.GetParentPath())):
            result.OverridePrim(path).SetActive(False)
    return result,[upstream,layer,result]


def coordinate_preflight(request, include_stage=False):
    """Prospective conversion only: never imports, exports or binds a native file."""
    from pxr import Gf, Usd, UsdGeom, UsdUtils
    import math
    axis = request.get('upAxis')
    unit, scale = request.get('metersPerUnit'), request.get('scale', 1)
    if axis not in ('Y', 'Z') or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in (unit, scale)):
        raise ValueError('Choose an explicit application up-axis and positive finite units/scale')
    factor = scale / unit
    if not math.isfinite(factor) or factor <= 1e-100 or factor >= 1e100:
        raise ValueError('Application conversion factor is outside the supported numeric range')
    graph = request.get('graph')
    if not isinstance(graph, dict) or any(n.get('id') == graph.get('targetNodeId') and n.get('operation') == 'application' for n in graph.get('nodes', [])):
        raise ValueError('Coordinate preparation requires the application INPUT graph, not its publication')
    stage, alive, stats = compile_graph(graph, reload=bool(request.get('reload', True)))
    selection=normalize_handoff_selection(request.get('selection'))
    if selection is not None:
        stage,selected_alive=selected_handoff_input(stage,selection);alive.extend(selected_alive)
    rotation = Gf.Matrix4d(1)
    if axis == 'Z':
        rotation.SetRotate(Gf.Rotation(Gf.Vec3d(1, 0, 0), 90))
    forward = Gf.Matrix4d().SetScale(factor) * rotation
    inverse = forward.GetInverse()
    handoff = Usd.Stage.CreateInMemory()
    root = UsdGeom.Xform.Define(handoff, '/Handoff')
    handoff.SetDefaultPrim(root.GetPrim())
    UsdGeom.SetStageUpAxis(handoff, axis)
    UsdGeom.SetStageMetersPerUnit(handoff, unit)
    handoff.SetTimeCodesPerSecond(stage.GetTimeCodesPerSecond());handoff.SetFramesPerSecond(stage.GetFramesPerSecond());handoff.SetStartTimeCode(stage.GetStartTimeCode());handoff.SetEndTimeCode(stage.GetEndTimeCode())
    root.GetPrim().GetReferences().AddReference(stage.GetRootLayer().identifier,stage.GetDefaultPrim().GetPath())
    prepend_root_transform(root.GetPrim(),forward,'handoffCoordinates')
    # Metadata revisions cover external layers and their referenced assets. They
    # are preflight fingerprints, not content hashes or a live filesystem watcher.
    input_revision=graph_input_revision(graph,alive)
    def bounds(value):
        extent = UsdGeom.BBoxCache(Usd.TimeCode(value.GetStartTimeCode()), ['default', 'render']).ComputeWorldBound(value.GetPseudoRoot()).ComputeAlignedRange()
        return [0, 0, 0] if extent.IsEmpty() else list(extent.GetSize())
    report = {'selection':selection,'draft': {'version':1, 'app':request.get('app', ''), 'upAxis':axis,
            'metersPerUnit':unit, 'scale':scale, 'factor':factor,
            'forward':[list(row) for row in forward], 'inverse':[list(row) for row in inverse],
            'graphRevision':graph_digest(graph), 'inputRevision':input_revision,
            'usdVersion':'.'.join(map(str, Usd.GetVersion()))},
            'inputDimensions':bounds(stage) if request.get('measure',True) else [], 'handoffDimensions':bounds(handoff) if request.get('measure',True) else [], 'graphStats':stats,
            'note':'Draft only. No native project was created or imported. Return inverse applies only if the exporter retains the complete handoff convention.'}

    return (report, handoff, [*alive, stage, handoff]) if include_stage else report


def write_single_file_snapshot(stage, destination, replacing=False):
    """Explicit snapshot write only. Keep texture links; bake USD composition
    into one file using upstream flattening, which preserves scene instancing."""
    from pxr import Sdf,Usd,UsdGeom
    import pathlib,tempfile
    destination=pathlib.Path(destination)
    extension=destination.suffix.lower()
    if extension not in ('.usd','.usdc','.usda'):
        raise ValueError('Choose a .usd or .usdc binary snapshot, or .usda text snapshot')
    binary=extension in ('.usd','.usdc')
    def stamp():
        if not destination.exists():return None
        value=destination.stat();return (value.st_size,value.st_mtime_ns,value.st_ino)
    before=stamp()
    flattened=stage.Flatten(False)
    if not flattened:raise ValueError('Unable to compose single-file USD snapshot')
    flattened.customLayerData={**dict(stage.GetRootLayer().customLayerData),**dict(flattened.customLayerData),'orchflowSingleFile':True}
    # OpenUSD 25.08 includes an empty asset path for internal prototype references.
    if any(flattened.GetCompositionAssetDependencies()):
        raise ValueError('Single-file snapshot still has external USD composition dependencies')
    # Explicit temporary encoding overrides USD_DEFAULT_FILE_FORMAT, including
    # when the public destination uses the encoding-neutral .usd extension.
    handle,temporary=tempfile.mkstemp(prefix='.orchflow-snapshot-',suffix=('.usdc' if binary else '.usda'),dir=destination.parent)
    os.close(handle);temporary=pathlib.Path(temporary)
    installed=False
    try:
        if not flattened.Export(str(temporary)):raise ValueError('Single-file USD export failed')
        reopened=Usd.Stage.Open(str(temporary))
        if not reopened or not reopened.GetDefaultPrim() or reopened.GetDefaultPrim().GetPath()!=stage.GetDefaultPrim().GetPath():raise ValueError('Single-file USD could not be reopened with its original default prim')
        if binary and reopened.GetRootLayer().GetFileFormat().formatId!='usdc':raise ValueError('Snapshot was not written in binary USD format')
        if any(layer!=reopened.GetRootLayer() and layer!=reopened.GetSessionLayer() for layer in reopened.GetUsedLayers()):raise ValueError('Single-file USD still requires supporting USD layers')
        if UsdGeom.GetStageUpAxis(reopened)!=UsdGeom.GetStageUpAxis(stage) or UsdGeom.GetStageMetersPerUnit(reopened)!=UsdGeom.GetStageMetersPerUnit(stage):raise ValueError('Snapshot changed stage coordinate metadata')
        # Release file-backed layers before replacement on Windows.
        reopened=None
        if replacing:
            if destination.is_symlink() or stamp()!=before:raise ValueError('Handoff changed during export; existing file was preserved')
            os.replace(temporary,destination)
        else:
            import shutil
            try:
                with destination.open('xb') as target:
                    installed=True
                    with temporary.open('rb') as source:shutil.copyfileobj(source,target,1024*1024)
            except Exception:
                if installed:destination.unlink()
                raise
    finally:
        if temporary.exists():temporary.unlink()


def export_handoff(request, prepared=None, tag_identity=True):
    """Explicit single-file handoff snapshot; never write original USD/assets."""
    from pxr import Sdf,Usd
    import pathlib,uuid
    destination=pathlib.Path(request.get('destination',''))
    if not destination.is_absolute() or destination.suffix.lower() not in ('.usd','.usdc','.usda'):
        raise ValueError('Choose an absolute .usd or .usdc binary destination, or .usda text destination')
    if destination.is_symlink():raise ValueError('Cannot replace a symlink handoff')
    replacing=destination.exists()
    if replacing:
        if not request.get('overwrite'):raise ValueError('Handoff destination already exists; explicit replacement is required')
        existing=Sdf.Layer.FindOrOpen(str(destination))
        handoff_prim=existing.GetPrimAtPath('/Handoff') if existing else None
        if not existing or not (existing.customLayerData.get('orchflowHandoff') or (handoff_prim and 'userProperties:orchflowRoundTripId' in handoff_prim.properties)):
            raise ValueError('Only an OrchFlow-generated handoff can be replaced; source files are protected')
    if not destination.parent.is_dir():raise ValueError('Handoff parent directory does not exist')
    report,stage,alive=prepared if prepared is not None else coordinate_preflight(request,include_stage=True)
    for value in alive:
        if isinstance(value,Usd.Stage):
            for source_layer in value.GetUsedLayers():
                if source_layer.realPath and os.path.normcase(os.path.realpath(source_layer.realPath))==os.path.normcase(os.path.realpath(destination)):
                    raise ValueError('Handoff destination is an upstream USD input; choose another filename')
    root=stage.GetRootLayer();root.customLayerData={**root.customLayerData,'orchflowHandoff':True,'orchflowNamespaceVersion':2,'orchflowHandoffSelection':json.dumps(report.get('selection'))}
    round_trip=uuid.uuid4().hex
    if tag_identity:
        stage.GetDefaultPrim().CreateAttribute('userProperties:orchflowRoundTripId',Sdf.ValueTypeNames.String,custom=True).Set(round_trip)
        for prim in list(stage.Traverse()):
            if prim.IsInstanceProxy():continue
            prim.CreateAttribute('userProperties:orchflowId',Sdf.ValueTypeNames.String,custom=True).Set(hashlib.sha256((round_trip+str(prim.GetPath())).encode()).hexdigest())
    write_single_file_snapshot(stage,destination,replacing)
    return {**report,'policy':request.get('policy'),'path':str(destination),'layerPaths':[],'fileSizeBytes':destination.stat().st_size}


def materialx_settings(settings,files):
    import math,re
    defaults={
        'name':'OrchFlowMaterial',
        'baseColor':[.7,.35,.15],'metalness':0.0,'roughness':.4,'specular':.5,'opacity':1.0,
        'baseWeight':1.0,'specularColor':[1.0,1.0,1.0],'specularIor':1.5,
        'transmission':0.0,'transmissionColor':[1.0,1.0,1.0],
        'emission':0.0,'emissionColor':[1.0,1.0,1.0],
        'coat':0.0,'coatRoughness':0.1,'coatColor':[1.0,1.0,1.0],
        'sheen':0.0,'sheenRoughness':0.3,'sheenColor':[1.0,1.0,1.0],
        'subsurface':0.0,'subsurfaceColor':[1.0,1.0,1.0],'subsurfaceScale':1.0,'subsurfaceRadius':[1.0,1.0,1.0],
        'bindingTargets':['*'],
        'baseColorTextureDependencyId':None,
        'roughnessTextureDependencyId':None,
        'metallicTextureDependencyId':None,
        'emissionTextureDependencyId':None,
        'normalTextureDependencyId':None,
        'bumpTextureDependencyId':None,
        'opacityTextureDependencyId':None,
        'transmissionTextureDependencyId':None,
        'proceduralBaseColor':None,
        'proceduralRoughness':None,
    }
    clean_settings={k:v for k,v in (settings or {}).items() if v is not None}
    value={**defaults,**clean_settings}
    if not re.fullmatch('[A-Za-z_][A-Za-z_0-9]{0,99}',value['name']):raise ValueError('MaterialX name must be a USD identifier')
    color_defaults={
        'baseColor':[.7,.35,.15],
        'specularColor':[1.0,1.0,1.0],
        'transmissionColor':[1.0,1.0,1.0],
        'emissionColor':[1.0,1.0,1.0],
        'coatColor':[1.0,1.0,1.0],
        'sheenColor':[1.0,1.0,1.0],
        'subsurfaceColor':[1.0,1.0,1.0],
    }
    for color_key,default_c in color_defaults.items():
        c=value.get(color_key)
        if c is None:
            value[color_key]=default_c
            c=default_c
        if not isinstance(c,list) or len(c)!=3:raise ValueError(f'MaterialX {color_key} requires three channels')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1 for v in c):
            raise ValueError(f'MaterialX {color_key} channels must be finite values between 0 and 1')
    for float_key in ['metalness','roughness','specular','opacity','baseWeight','transmission','emission','coat','coatRoughness','sheen','sheenRoughness','subsurface']:
        val=value.get(float_key)
        if val is not None and (isinstance(val,bool) or not isinstance(val,(int,float)) or not math.isfinite(val) or val<0 or (float_key!='emission' and val>1)):
            raise ValueError(f'MaterialX parameter {float_key} must be a finite value between 0 and 1')
    scale=value.get('subsurfaceScale')
    if scale is None:
        value['subsurfaceScale'] = 1.0
        scale = 1.0
    radius=value.get('subsurfaceRadius')
    if radius is None:
        value['subsurfaceRadius'] = [1.0, 1.0, 1.0]
        radius = [1.0, 1.0, 1.0]
    if isinstance(scale,bool) or not isinstance(scale,(int,float)) or not math.isfinite(scale) or scale<0:
        raise ValueError('Subsurface scale must be finite and non-negative')
    if not isinstance(radius,list) or len(radius)!=3 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for v in radius):
        raise ValueError('Subsurface radius requires three finite non-negative values')
    ior=value.get('specularIor')
    if ior is not None and (isinstance(ior,bool) or not isinstance(ior,(int,float)) or not math.isfinite(ior) or ior<1):
        raise ValueError('MaterialX specular IOR must be >= 1.0')
    for key in ['baseColorTextureDependencyId','roughnessTextureDependencyId','metallicTextureDependencyId','emissionTextureDependencyId','normalTextureDependencyId','bumpTextureDependencyId','opacityTextureDependencyId','transmissionTextureDependencyId']:
        if value.get(key) and value[key] not in files:raise ValueError('MaterialX texture dependency is missing from the registry: '+str(value[key]))
    if not isinstance(value['bindingTargets'],list) or not value['bindingTargets'] or len(value['bindingTargets'])>100000 or any(not isinstance(p,str) or not p for p in value['bindingTargets']):raise ValueError('Choose explicit MaterialX binding targets or *')
    return value


def generate_procedural_mdl(sub_id, color_proc=None, rough_proc=None, base_c=(0.7, 0.35, 0.15), roughness=0.4, metalness=0.0, specular_ior=1.5, coat=0.0, opacity=1.0):
    lines = [
        "mdl 1.6;",
        "",
        "import ::materialx::noise::*;",
        "import ::state::*;",
        "import ::math::*;",
        "import ::UsdPreviewSurface::*;",
        "",
        f"export material {sub_id}(",
        f"    float3 diffuseColor = float3({base_c[0]:.4f}, {base_c[1]:.4f}, {base_c[2]:.4f}),",
        f"    float metallic = {metalness:.4f},",
        f"    float roughness = {roughness:.4f},",
        f"    uniform float ior = {specular_ior:.4f},",
        f"    float clearcoat = {coat:.4f},",
        f"    float opacity = {opacity:.4f}",
        ") = let {",
        "    float3 coord = ::state::texture_coordinate(0);",
        "    float2 uv = float2(coord.x, coord.y);",
        "    float3 obj_p = ::state::transform_point(::state::coordinate_internal, ::state::coordinate_object, ::state::position());",
    ]

    # Color modulation
    if color_proc:
        ptype = color_proc.get('type', 'noise2d')
        space = color_proc.get('space', 'uv')
        scale = color_proc.get('scale', [1.0, 1.0])
        sx = scale[0] if isinstance(scale, (list, tuple)) else scale
        sy = scale[1] if isinstance(scale, (list, tuple)) and len(scale) > 1 else sx
        amp = float(color_proc.get('amplitude', 1.0))
        octaves = int(color_proc.get('octaves', 3))

        if ptype == 'noise2d':
            lines.append(f"    float2 p_col = uv * float2({sx}, {sy});")
            lines.append(f"    float3 n_col = ::materialx::noise::mx_perlin_noise_float3(p_col) * {amp};")
        elif ptype == 'noise3d':
            lines.append(f"    float3 p_col = obj_p * {sx};")
            lines.append(f"    float3 n_col = ::materialx::noise::mx_perlin_noise_float3(p_col) * {amp};")
        elif ptype == 'fractal3d':
            pos_src = f"obj_p * {sx}" if space == 'object' else f"float3(uv.x * {sx}, uv.y * {sy}, 0.0)"
            lines.append(f"    float3 p_col = {pos_src};")
            lines.append(f"    float3 n_col = ::materialx::noise::mx_fractal_noise_float3(p_col, {octaves}, 2.0, 0.5) * {amp};")
        elif ptype == 'worley2d':
            lines.append(f"    float2 p_col = uv * float2({sx}, {sy});")
            lines.append(f"    float3 n_col = ::materialx::noise::mx_worley_noise_float3(p_col, 1.0, 0) * {amp};")
        lines.append("    float3 final_diffuse = diffuseColor * math::abs(n_col);")
    else:
        lines.append("    float3 final_diffuse = diffuseColor;")

    # Roughness modulation
    if rough_proc:
        ptype = rough_proc.get('type', 'fractal3d')
        space = rough_proc.get('space', 'object')
        scale = rough_proc.get('scale', [1.0, 1.0])
        sx = scale[0] if isinstance(scale, (list, tuple)) else scale
        sy = scale[1] if isinstance(scale, (list, tuple)) and len(scale) > 1 else sx
        amp = float(rough_proc.get('amplitude', 0.5))
        octaves = int(rough_proc.get('octaves', 3))

        if ptype == 'noise2d':
            lines.append(f"    float2 p_rg = uv * float2({sx}, {sy});")
            lines.append(f"    float n_rg = ::materialx::noise::mx_perlin_noise_float(p_rg) * {amp};")
        elif ptype == 'fractal3d':
            pos_src = f"obj_p * {sx}" if space == 'object' else f"float3(uv.x * {sx}, uv.y * {sy}, 0.0)"
            lines.append(f"    float3 p_rg = {pos_src};")
            lines.append(f"    float n_rg = ::materialx::noise::mx_fractal_noise_float(p_rg, {octaves}, 2.0, 0.5) * {amp};")
        lines.append("    float final_roughness = math::clamp(roughness + n_rg, 0.001, 1.0);")
    else:
        lines.append("    float final_roughness = roughness;")

    lines.extend([
        "} in ::UsdPreviewSurface::UsdPreviewSurface(",
        "    diffuseColor: final_diffuse,",
        "    metallic: metallic,",
        "    roughness: final_roughness,",
        "    ior: ior,",
        "    clearcoat: clearcoat,",
        "    opacity: opacity",
        ");",
        ""
    ])
    return "\n".join(lines)


def _procedural_mtlx():
    try:
        import procedural_mtlx
        return procedural_mtlx
    except ModuleNotFoundError:
        import sys
        return sys.modules[__name__]


def author_materialx(stage,settings,files,procedural_subgraph=None):
    from pxr import UsdShade,UsdGeom,Sdf,Gf,Sdr
    procedural_mtlx = _procedural_mtlx()
    m=materialx_settings(settings,files)
    required_ids=['ND_standard_surface_surfaceshader','ND_image_color3','ND_image_float','ND_geompropvalue_vector2']
    if m.get('normalTextureDependencyId'):required_ids.append('ND_normalmap_float')
    if m.get('bumpTextureDependencyId') and not m.get('normalTextureDependencyId'):required_ids.append('ND_bump_vector3')
    if not os.environ.get('ORCHFLOW_SKIP_SDR_CHECK'):
        for identifier in required_ids:
            if not Sdr.Registry().GetShaderNodeByIdentifier(identifier):raise ValueError('MaterialX shader definition unavailable: '+identifier)
    # Resolve UV primvar: prefer standard 'st', but auto-detect groom root UVs on BasisCurves
    # when 'st' is absent so fur/hair textures sample root UVs rather than along-strand coords.
    uv_primvar='st'
    uv_mode=str(m.get('uvMode') or 'auto').strip().lower()
    if uv_mode not in ('auto','root','strand'):
        uv_mode='auto'
    proc_strand_attrs=set()
    if isinstance(procedural_subgraph,dict):
        for pn in (procedural_subgraph.get('nodes') or []):
            pd=(pn.get('data') or {})
            if pd.get('kind')=='uv':
                gp=str(((pd.get('params') or {}).get('geomprop') or '')).strip()
                if gp in ('orchflow:strand_uv','strand_uv'):
                    proc_strand_attrs.add(gp)
    target_paths=m.get('bindingTargets') or []
    if target_paths and stage.GetDefaultPrim():
        from pxr import Usd,Vt
        def _iter_target_prims():
            for t in target_paths:
                if t=='*':
                    for p in Usd.PrimRange(stage.GetDefaultPrim(),Usd.TraverseInstanceProxies()):
                        yield p
                elif isinstance(t,str) and t.startswith('/'):
                    tp=stage.GetPrimAtPath(Sdf.Path(t))
                    if tp and tp.IsValid():
                        for p in Usd.PrimRange(tp,Usd.TraverseInstanceProxies()):
                            yield p

        def _ensure_strand_uv(attr_name):
            authored=False
            for p in _iter_target_prims():
                if not p.IsA(UsdGeom.BasisCurves) or p.IsInstanceProxy():
                    continue
                pv=UsdGeom.PrimvarsAPI(p)
                if not pv.HasPrimvar(attr_name):
                    counts=UsdGeom.BasisCurves(p).GetCurveVertexCountsAttr().Get()
                    if counts:
                        uvs=[]
                        for c_idx,n in enumerate(counts):
                            n_int=int(n)
                            if n_int<=0:
                                continue
                            u=((c_idx*2654435761)&0xFFFF)/65535.0
                            if n_int==1:
                                uvs.append(Gf.Vec2f(u,0.0))
                            else:
                                denom=float(n_int-1)
                                for i in range(n_int):
                                    uvs.append(Gf.Vec2f(u,float(i)/denom))
                        if uvs:
                            pv.CreatePrimvar(attr_name,Sdf.ValueTypeNames.TexCoord2fArray,UsdGeom.Tokens.vertex).Set(Vt.Vec2fArray(uvs))
                            authored=True
                else:
                    authored=True
            return authored

        for gp in proc_strand_attrs:
            _ensure_strand_uv(gp)

        if uv_mode=='strand':
            if _ensure_strand_uv('orchflow:strand_uv'):
                uv_primvar='orchflow:strand_uv'
        else:
            has_st=False
            root_uv_candidate=None
            for p in _iter_target_prims():
                pv=UsdGeom.PrimvarsAPI(p)
                if not has_st and (pv.HasPrimvar('st') or pv.HasPrimvar('uv')):
                    has_st=True
                if root_uv_candidate is None and p.IsA(UsdGeom.BasisCurves):
                    for cand in ('groom_root_uv','root_uv','uv_root'):
                        if pv.HasPrimvar(cand):
                            root_uv_candidate=cand
                            break
                if uv_mode=='auto' and has_st:
                    break
                if uv_mode=='root' and root_uv_candidate:
                    break
            if uv_mode=='root':
                if root_uv_candidate:
                    uv_primvar=root_uv_candidate
            elif not has_st and root_uv_candidate:
                uv_primvar=root_uv_candidate

    # A changed network receives a new Hydra material identity. Delegates may retain
    # properties/textures when a material is replaced at an existing scene path.
    import hashlib,json
    proc_hash=procedural_mtlx.subgraph_hash(procedural_subgraph)
    identity_payload={**m, **({'_proceduralGraphHash': proc_hash} if proc_hash else {}), **({'_uvPrimvar': uv_primvar} if uv_primvar!='st' else {})}
    identity=hashlib.sha256(json.dumps(identity_payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:16]
    path=stage.GetDefaultPrim().GetPath().AppendChild('_OrchFlowMaterials').AppendChild(m['name']+'_'+identity)
    while stage.GetPrimAtPath(path):path=path.GetParentPath().AppendChild(path.name+'_')
    material=UsdShade.Material.Define(stage,path)
    surface=UsdShade.Shader.Define(stage,path.AppendChild('StandardSurface'));surface.CreateIdAttr('ND_standard_surface_surfaceshader')
    surface.CreateOutput('out',Sdf.ValueTypeNames.Token);material.CreateSurfaceOutput('mtlx').ConnectToSource(surface.ConnectableAPI(),'out')
    preview=UsdShade.Shader.Define(stage,path.AppendChild('PreviewSurface'));preview.CreateIdAttr('UsdPreviewSurface');preview.CreateOutput('surface',Sdf.ValueTypeNames.Token);material.CreateSurfaceOutput().ConnectToSource(preview.ConnectableAPI(),'surface')

    # Physical parameters with safe defaults (distinguish 0.0 from None)
    def val(key,default):
        v=m.get(key)
        return default if v is None else float(v)

    def safe_vec3f(val, default=(1.0, 1.0, 1.0)):
        if val is None:
            return Gf.Vec3f(*default)
        try:
            return Gf.Vec3f(*(float(x) for x in val))
        except Exception:
            return Gf.Vec3f(*default)

    base_weight=val('baseWeight',1.0)
    base_c=m.get('baseColor') or [.7,.35,.15]
    metalness=val('metalness',0.0)
    roughness=val('roughness',0.4)
    specular=val('specular',0.5)
    specular_ior=val('specularIor',1.5)
    spec_c=m.get('specularColor') or [1.0,1.0,1.0]

    transmission=val('transmission',0.0)
    trans_c=m.get('transmissionColor') or [1.0,1.0,1.0]
    emission=val('emission',0.0)
    emit_c=m.get('emissionColor') or [1.0,1.0,1.0]
    coat=val('coat',0.0)
    coat_roughness=val('coatRoughness',0.1)
    coat_c=m.get('coatColor') or [1.0,1.0,1.0]
    sheen=val('sheen',0.0)
    sheen_roughness=val('sheenRoughness',0.3)
    sheen_c=m.get('sheenColor') or [1.0,1.0,1.0]
    subsurface=val('subsurface',0.0)
    sub_c=m.get('subsurfaceColor') or [1.0,1.0,1.0]
    opacity=val('opacity',1.0)

    # Standard Surface inputs
    surface.CreateInput('base',Sdf.ValueTypeNames.Float).Set(base_weight)
    surface.CreateInput('base_color',Sdf.ValueTypeNames.Color3f).Set(safe_vec3f(base_c, (.7, .35, .15)))
    surface.CreateInput('metalness',Sdf.ValueTypeNames.Float).Set(metalness)
    surface.CreateInput('specular',Sdf.ValueTypeNames.Float).Set(specular)
    surface.CreateInput('specular_color',Sdf.ValueTypeNames.Color3f).Set(safe_vec3f(spec_c))
    surface.CreateInput('specular_roughness',Sdf.ValueTypeNames.Float).Set(roughness)
    surface.CreateInput('specular_IOR',Sdf.ValueTypeNames.Float).Set(specular_ior)

    surface.CreateInput('transmission',Sdf.ValueTypeNames.Float).Set(transmission)
    surface.CreateInput('transmission_color',Sdf.ValueTypeNames.Color3f).Set(safe_vec3f(trans_c))
    surface.CreateInput('emission',Sdf.ValueTypeNames.Float).Set(emission)
    surface.CreateInput('emission_color',Sdf.ValueTypeNames.Color3f).Set(safe_vec3f(emit_c))
    surface.CreateInput('coat',Sdf.ValueTypeNames.Float).Set(coat)
    surface.CreateInput('coat_roughness',Sdf.ValueTypeNames.Float).Set(coat_roughness)
    surface.CreateInput('coat_color',Sdf.ValueTypeNames.Color3f).Set(safe_vec3f(coat_c))
    surface.CreateInput('sheen',Sdf.ValueTypeNames.Float).Set(sheen)
    surface.CreateInput('sheen_roughness',Sdf.ValueTypeNames.Float).Set(sheen_roughness)
    surface.CreateInput('sheen_color',Sdf.ValueTypeNames.Color3f).Set(safe_vec3f(sheen_c))
    surface.CreateInput('subsurface',Sdf.ValueTypeNames.Float).Set(subsurface)
    surface.CreateInput('subsurface_color',Sdf.ValueTypeNames.Color3f).Set(safe_vec3f(sub_c))
    sss_rad=safe_vec3f(m.get('subsurfaceRadius'))
    surface.CreateInput('subsurface_radius',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(max(0.005, float(sss_rad[0])), max(0.005, float(sss_rad[1])), max(0.005, float(sss_rad[2]))))
    surface.CreateInput('subsurface_scale',Sdf.ValueTypeNames.Float).Set(max(1e-4, float(val('subsurfaceScale',1.0))))
    _thin_name = str(m.get('name') or '').lower()
    _is_eye_film = transmission > 0.5 and any(
        k in _thin_name for k in ('eye', 'tear', 'cornea', 'shell', 'cartilage', 'saliva', 'lacrimal', 'wet')
    )
    _auto_thin = transmission > 0.5 and (specular_ior < 1.15 or _is_eye_film)
    # Thin eye/tear/cornea shells sit micrometers in front of the eyeball; in RTX Path Tracing,
    # pure transmission (opacity=1.0) spawns a secondary ray whose self-intersection epsilon can
    # skip past the iris behind it. Fractional cutout opacity passes primary rays cleanly via any-hit
    # while keeping wet corneal specular highlights.
    if _is_eye_film and opacity >= 0.99 and not m.get('opacityTexture') and not m.get('opacityTextureDependencyId'):
        opacity = 0.12
    surface.CreateInput('opacity',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(opacity))
    if bool(m.get('thinWalled')) or _auto_thin:
        surface.CreateInput('thin_walled',Sdf.ValueTypeNames.Bool).Set(True)

    # Dual-context UsdPreviewSurface fallback (weighted diffuse/subsurface for rasterizers)
    diffuse_mult=base_weight*(1.0-subsurface)*(1.0-metalness)*(1.0-transmission)
    subsurface_mult=subsurface*(1.0-metalness)*(1.0-transmission)
    preview_diffuse=[diffuse_mult*base_c[i]+subsurface_mult*sub_c[i] for i in range(3)]
    preview.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(safe_vec3f(preview_diffuse, (.7, .35, .15)))
    preview.CreateInput('metallic',Sdf.ValueTypeNames.Float).Set(metalness)
    preview.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(roughness)
    preview.CreateInput('ior',Sdf.ValueTypeNames.Float).Set(specular_ior)
    preview.CreateInput('useSpecularWorkflow',Sdf.ValueTypeNames.Int).Set(0)
    preview.CreateInput('emissiveColor',Sdf.ValueTypeNames.Color3f).Set(safe_vec3f([c*emission for c in emit_c], (0, 0, 0)))
    preview.CreateInput('clearcoat',Sdf.ValueTypeNames.Float).Set(coat)
    preview.CreateInput('clearcoatRoughness',Sdf.ValueTypeNames.Float).Set(coat_roughness)
    preview.CreateInput('opacity',Sdf.ValueTypeNames.Float).Set(opacity)

    # Helper: shared UV and PreviewUV coordinates
    uv_nodes={}
    def get_uv_coord(space='uv'):
        if space in uv_nodes:return uv_nodes[space]
        if space=='object':
            pos=UsdShade.Shader.Define(stage,path.AppendChild('Pos_object'));pos.CreateIdAttr('ND_position_vector3');pos.CreateInput('space',Sdf.ValueTypeNames.String).Set('object');pos.CreateOutput('out',Sdf.ValueTypeNames.Float3)
            uv_nodes[space]=(pos,None)
            return uv_nodes[space]
        uv=UsdShade.Shader.Define(stage,path.AppendChild('UV_st'));uv.CreateIdAttr('ND_geompropvalue_vector2');uv.CreateInput('geomprop',Sdf.ValueTypeNames.String).Set(uv_primvar);uv.CreateOutput('out',Sdf.ValueTypeNames.Float2)
        puv=UsdShade.Shader.Define(stage,path.AppendChild('PreviewUV_st'));puv.CreateIdAttr('UsdPrimvarReader_float2');puv.CreateInput('varname',Sdf.ValueTypeNames.Token).Set(uv_primvar);puv.CreateOutput('result',Sdf.ValueTypeNames.Float2)
        uv_nodes[space]=(uv,puv)
        return uv_nodes[space]

    # Texture maps
    texture_mappings=[
        ('baseColorTextureDependencyId','base_color','diffuseColor','color3','srgb_texture'),
        ('roughnessTextureDependencyId','specular_roughness','roughness','float','raw'),
        ('metallicTextureDependencyId','metalness','metallic','float','raw'),
        ('emissionTextureDependencyId','emission_color','emissiveColor','color3','srgb_texture'),
        ('opacityTextureDependencyId','opacity','opacity','color3','raw'),
        ('transmissionTextureDependencyId','transmission',None,'float','raw'),
    ]
    for key,port,preview_port,kind,cspace in texture_mappings:
        if not m.get(key):continue
        asset=files[m[key]]
        uv,puv=get_uv_coord('uv')
        name_suffix='_'+kind if kind in ('color3','float') and port=='base_color' else '_'+port
        image=UsdShade.Shader.Define(stage,path.AppendChild('Image'+name_suffix));image.CreateIdAttr('ND_image_'+kind)
        file=image.CreateInput('file',Sdf.ValueTypeNames.Asset);file.Set(Sdf.AssetPath(asset));file.GetAttr().SetColorSpace(cspace)
        image.CreateInput('texcoord',Sdf.ValueTypeNames.Float2).ConnectToSource(uv.ConnectableAPI(),'out')
        image.CreateOutput('out',Sdf.ValueTypeNames.Color3f if kind=='color3' else Sdf.ValueTypeNames.Float)
        surface.GetInput(port).ConnectToSource(image.ConnectableAPI(),'out')
        if port=='base_color' and subsurface>0.0:
            surface.GetInput('subsurface_color').ConnectToSource(image.ConnectableAPI(),'out')
        if preview_port and puv:
            texture=UsdShade.Shader.Define(stage,path.AppendChild('PreviewTexture'+name_suffix));texture.CreateIdAttr('UsdUVTexture')
            texture.CreateInput('file',Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(asset))
            texture.CreateInput('sourceColorSpace',Sdf.ValueTypeNames.Token).Set('sRGB' if cspace=='srgb_texture' else 'raw')
            texture.CreateInput('st',Sdf.ValueTypeNames.Float2).ConnectToSource(puv.ConnectableAPI(),'result')
            output='rgb' if kind=='color3' else 'r'
            texture.CreateOutput(output,Sdf.ValueTypeNames.Float3 if kind=='color3' else Sdf.ValueTypeNames.Float)
            preview.GetInput(preview_port).ConnectToSource(texture.ConnectableAPI(),output)

    # Normal map
    if m.get('normalTextureDependencyId'):
        asset=files[m['normalTextureDependencyId']]
        uv,puv=get_uv_coord('uv')
        n_img=UsdShade.Shader.Define(stage,path.AppendChild('Image_normal'));n_img.CreateIdAttr('ND_image_vector3')
        n_file=n_img.CreateInput('file',Sdf.ValueTypeNames.Asset);n_file.Set(Sdf.AssetPath(asset));n_file.GetAttr().SetColorSpace('raw')
        n_img.CreateInput('texcoord',Sdf.ValueTypeNames.Float2).ConnectToSource(uv.ConnectableAPI(),'out')
        n_img.CreateOutput('out',Sdf.ValueTypeNames.Float3)
        normalmap=UsdShade.Shader.Define(stage,path.AppendChild('NormalMap'));normalmap.CreateIdAttr('ND_normalmap_float')
        normalmap.CreateInput('in',Sdf.ValueTypeNames.Float3).ConnectToSource(n_img.ConnectableAPI(),'out')
        normalmap.CreateInput('scale',Sdf.ValueTypeNames.Float).Set(1.0)
        normalmap.CreateOutput('out',Sdf.ValueTypeNames.Float3)
        surface.CreateInput('normal',Sdf.ValueTypeNames.Float3).ConnectToSource(normalmap.ConnectableAPI(),'out')
        if puv:
            p_ntex=UsdShade.Shader.Define(stage,path.AppendChild('PreviewNormalTexture'));p_ntex.CreateIdAttr('UsdUVTexture')
            p_ntex.CreateInput('file',Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(asset))
            p_ntex.CreateInput('sourceColorSpace',Sdf.ValueTypeNames.Token).Set('raw')
            p_ntex.CreateInput('st',Sdf.ValueTypeNames.Float2).ConnectToSource(puv.ConnectableAPI(),'result')
            p_ntex.CreateOutput('rgb',Sdf.ValueTypeNames.Float3)
            preview.CreateInput('normal',Sdf.ValueTypeNames.Float3).ConnectToSource(p_ntex.ConnectableAPI(),'rgb')
    elif m.get('bumpTextureDependencyId'):
        asset=files[m['bumpTextureDependencyId']]
        uv,puv=get_uv_coord('uv')
        b_img=UsdShade.Shader.Define(stage,path.AppendChild('Image_bump'));b_img.CreateIdAttr('ND_image_float')
        b_file=b_img.CreateInput('file',Sdf.ValueTypeNames.Asset);b_file.Set(Sdf.AssetPath(asset));b_file.GetAttr().SetColorSpace('raw')
        b_img.CreateInput('texcoord',Sdf.ValueTypeNames.Float2).ConnectToSource(uv.ConnectableAPI(),'out')
        b_img.CreateOutput('out',Sdf.ValueTypeNames.Float)
        bump=UsdShade.Shader.Define(stage,path.AppendChild('Bump'));bump.CreateIdAttr('ND_bump_vector3')
        bump.CreateInput('height',Sdf.ValueTypeNames.Float).ConnectToSource(b_img.ConnectableAPI(),'out')
        bump.CreateInput('scale',Sdf.ValueTypeNames.Float).Set(float(m.get('bumpScale',0.002)))
        bump.CreateOutput('out',Sdf.ValueTypeNames.Float3)
        surface.CreateInput('normal',Sdf.ValueTypeNames.Float3).ConnectToSource(bump.ConnectableAPI(),'out')

    # Procedural generators (legacy scalar/noise config; keep UsdPreviewSurface clean)
    def author_procedural(slot_name, target_input, is_color):
        proc=m.get(slot_name)
        if not proc or not isinstance(proc,dict) or proc.get('type') in (None,'none'):return
        ptype=proc['type']
        space=proc.get('space','uv')
        coord_node,_=get_uv_coord('object' if space=='object' or '3d' in ptype else 'uv')
        kind='color3' if is_color else 'float'
        node_name=f'Procedural_{slot_name}'
        amp=proc.get('amplitude',1.0)
        if ptype in ('noise2d','noise3d','fractal3d'):
            proc_id=f'ND_{ptype}_{kind}'
            if Sdr.Registry().GetShaderNodeByIdentifier(proc_id) or os.environ.get('ORCHFLOW_SKIP_SDR_CHECK'):
                shader=UsdShade.Shader.Define(stage,path.AppendChild(node_name));shader.CreateIdAttr(proc_id)
                if '2d' in ptype and coord_node:
                    shader.CreateInput('texcoord',Sdf.ValueTypeNames.Float2).ConnectToSource(coord_node.ConnectableAPI(),'out')
                elif '3d' in ptype and coord_node:
                    shader.CreateInput('position',Sdf.ValueTypeNames.Float3).ConnectToSource(coord_node.ConnectableAPI(),'out')
                if is_color:
                    shader.CreateInput('amplitude',Sdf.ValueTypeNames.Float3).Set(Gf.Vec3f(amp,amp,amp))
                else:
                    shader.CreateInput('amplitude',Sdf.ValueTypeNames.Float).Set(amp)
                if ptype=='fractal3d':
                    shader.CreateInput('octaves',Sdf.ValueTypeNames.Int).Set(int(proc.get('octaves',3)))
                out_type=Sdf.ValueTypeNames.Color3f if is_color else Sdf.ValueTypeNames.Float
                shader.CreateOutput('out',out_type)
                target_input.ConnectToSource(shader.ConnectableAPI(),'out')
        elif ptype == 'worley2d':
            proc_id='ND_worleynoise2d_float'
            if Sdr.Registry().GetShaderNodeByIdentifier(proc_id) or os.environ.get('ORCHFLOW_SKIP_SDR_CHECK'):
                shader=UsdShade.Shader.Define(stage,path.AppendChild(node_name));shader.CreateIdAttr(proc_id)
                if coord_node:
                    shader.CreateInput('texcoord',Sdf.ValueTypeNames.Float2).ConnectToSource(coord_node.ConnectableAPI(),'out')
                shader.CreateOutput('out',Sdf.ValueTypeNames.Float)
                if is_color:
                    conv=UsdShade.Shader.Define(stage,path.AppendChild(node_name+'_conv'));conv.CreateIdAttr('ND_convert_float_color3')
                    conv.CreateInput('in',Sdf.ValueTypeNames.Float).ConnectToSource(shader.ConnectableAPI(),'out')
                    conv.CreateOutput('out',Sdf.ValueTypeNames.Color3f)
                    target_input.ConnectToSource(conv.ConnectableAPI(),'out')
                else:
                    target_input.ConnectToSource(shader.ConnectableAPI(),'out')

    author_procedural('proceduralBaseColor',surface.GetInput('base_color'),True)
    author_procedural('proceduralRoughness',surface.GetInput('specular_roughness'),False)

    if procedural_subgraph:
        base_values={
            'base': base_weight,
            'base_color': base_c,
            'metalness': metalness,
            'specular': specular,
            'specular_color': spec_c,
            'specular_roughness': roughness,
            'specular_IOR': specular_ior,
            'transmission': transmission,
            'transmission_color': trans_c,
            'emission': emission,
            'emission_color': emit_c,
            'coat': coat,
            'coat_roughness': coat_roughness,
            'coat_color': coat_c,
            'sheen': sheen,
            'sheen_roughness': sheen_roughness,
            'sheen_color': sheen_c,
            'subsurface': subsurface,
            'subsurface_color': sub_c,
            'subsurface_radius': m.get('subsurfaceRadius') or [1.0, 1.0, 1.0],
            'subsurface_scale': val('subsurfaceScale', 1.0),
            'opacity': [opacity, opacity, opacity],
        }
        procedural_mtlx.author_procedural_graph_usd(
            stage, path, surface, procedural_subgraph, get_uv_coord, base_values
        )

    if not settings.get('_skipBinding'):
        for target in m['bindingTargets']:
            prim=stage.GetDefaultPrim() if target=='*' else stage.GetPrimAtPath(target)
            if not prim or prim.IsPseudoRoot() or prim.IsInstanceProxy():raise ValueError('MaterialX binding target is missing or inside an instance. Select the instance root to preserve sharing: '+target)
            UsdShade.MaterialBindingAPI.Apply(prim).Bind(material,bindingStrength=UsdShade.Tokens.strongerThanDescendants)
            # Purpose-specific incoming bindings otherwise take precedence over allPurpose.
            for purpose in (UsdShade.Tokens.full,UsdShade.Tokens.preview):
                UsdShade.MaterialBindingAPI(prim).Bind(material,bindingStrength=UsdShade.Tokens.strongerThanDescendants,materialPurpose=purpose)
    return material


def materialx_document(request):
    """Portable standard_surface document; output is returned until explicitly saved."""
    import xml.etree.ElementTree as ET
    files={d['id']:d['path'] for d in request.get('dependencies',[])}
    m=materialx_settings(request.get('materialX') or {},files)
    root=ET.Element('materialx',version='1.38')
    surface=ET.SubElement(root,'standard_surface',name='Surface',type='surfaceshader')
    def input(name,kind,value):return ET.SubElement(surface,'input',name=name,type=kind,value=str(value))
    input('base','float',m['baseWeight']);input('base_color','color3',','.join(map(str,m['baseColor'])))
    input('metalness','float',m['metalness']);input('specular','float',m['specular'])
    input('specular_color','color3',','.join(map(str,m.get('specularColor',[1.0,1.0,1.0]))))
    input('specular_roughness','float',m['roughness'])
    input('specular_IOR','float',m['specularIor'])
    input('transmission','float',m['transmission']);input('transmission_color','color3',','.join(map(str,m['transmissionColor'])))
    input('emission','float',m['emission']);input('emission_color','color3',','.join(map(str,m['emissionColor'])))
    input('coat','float',m['coat']);input('coat_roughness','float',m['coatRoughness']);input('coat_color','color3',','.join(map(str,m['coatColor'])))
    input('sheen','float',m['sheen']);input('sheen_roughness','float',m['sheenRoughness']);input('sheen_color','color3',','.join(map(str,m['sheenColor'])))
    input('subsurface','float',m['subsurface']);input('subsurface_color','color3',','.join(map(str,m['subsurfaceColor'])))
    input('subsurface_radius','color3',','.join(map(str,m['subsurfaceRadius'])))
    input('subsurface_scale','float',m['subsurfaceScale'])
    input('opacity','color3',','.join(map(str,m['opacity'] if isinstance(m['opacity'],list) else [m['opacity']]*3)))

    uv_element=None
    def get_uv_doc():
        nonlocal uv_element
        if uv_element is None:
            uv_element=ET.SubElement(root,'geompropvalue',name='UV_st',type='vector2')
            ET.SubElement(uv_element,'input',name='geomprop',type='string',value='st')
        return uv_element

    texture_slots=[
        ('baseColorTextureDependencyId','base_color','color3','srgb_texture'),
        ('roughnessTextureDependencyId','specular_roughness','float','lin_rec709'),
        ('metallicTextureDependencyId','metalness','float','lin_rec709'),
        ('emissionTextureDependencyId','emission_color','color3','srgb_texture'),
        ('opacityTextureDependencyId','opacity','color3','lin_rec709'),
        ('transmissionTextureDependencyId','transmission','float','lin_rec709'),
    ]
    for key,port,kind,cspace in texture_slots:
        if not m.get(key):continue
        get_uv_doc()
        name='Image_'+port
        image=ET.SubElement(root,'image',name=name,type=kind)
        ET.SubElement(image,'input',name='file',type='filename',value=files[m[key]],colorspace=cspace)
        ET.SubElement(image,'input',name='texcoord',type='vector2',nodename='UV_st')
        existing=surface.find(f"input[@name='{port}']")
        if existing is not None:surface.remove(existing)
        ET.SubElement(surface,'input',name=port,type=kind,nodename=name)

    if m.get('normalTextureDependencyId'):
        get_uv_doc()
        n_img=ET.SubElement(root,'image',name='Image_normal',type='vector3')
        ET.SubElement(n_img,'input',name='file',type='filename',value=files[m['normalTextureDependencyId']],colorspace='lin_rec709')
        ET.SubElement(n_img,'input',name='texcoord',type='vector2',nodename='UV_st')
        normalmap=ET.SubElement(root,'normalmap',name='NormalMap',type='vector3')
        ET.SubElement(normalmap,'input',name='in',type='vector3',nodename='Image_normal')
        ET.SubElement(surface,'input',name='normal',type='vector3',nodename='NormalMap')

    # Procedural nodes in Document
    def add_proc_doc(slot_name, port, is_color):
        proc=m.get(slot_name)
        if not proc or not isinstance(proc,dict) or proc.get('type') in (None,'none'):return
        ptype=proc['type'];kind='color3' if is_color else 'float'
        node_name=f'Procedural_{slot_name}'
        proc_node=ET.SubElement(root,ptype,name=node_name,type=kind)
        amp=proc.get('amplitude',1.0)
        if is_color:
            ET.SubElement(proc_node,'input',name='amplitude',type='vector3',value=f'{amp}, {amp}, {amp}')
        else:
            ET.SubElement(proc_node,'input',name='amplitude',type='float',value=str(amp))
        if '2d' in ptype:
            get_uv_doc()
            ET.SubElement(proc_node,'input',name='texcoord',type='vector2',nodename='UV_st')
        elif '3d' in ptype:
            pos_el=root.find("position[@name='Pos_object']")
            if pos_el is None:
                pos_el=ET.SubElement(root,'position',name='Pos_object',type='vector3')
                ET.SubElement(pos_el,'input',name='space',type='string',value='object')
            ET.SubElement(proc_node,'input',name='position',type='vector3',nodename='Pos_object')
        if ptype=='fractal3d':
            ET.SubElement(proc_node,'input',name='octaves',type='integer',value=str(int(proc.get('octaves',3))))
        existing=surface.find(f"input[@name='{port}']")
        if existing is not None:surface.remove(existing)
        ET.SubElement(surface,'input',name=port,type=kind,nodename=node_name)

    add_proc_doc('proceduralBaseColor','base_color',True)
    add_proc_doc('proceduralRoughness','specular_roughness',False)

    if request.get('proceduralGraph') and request.get('materialXNodeId'):
        procedural_mtlx = _procedural_mtlx()
        sub=procedural_mtlx.collect_rule_subgraph(request['proceduralGraph'],request['materialXNodeId'],request.get('ruleId'))
        if sub:
            def get_pos_doc():
                pos_el=root.find("position[@name='Pos_object']")
                if pos_el is None:
                    pos_el=ET.SubElement(root,'position',name='Pos_object',type='vector3')
                    ET.SubElement(pos_el,'input',name='space',type='string',value='object')
                return 'Pos_object'
            base_values={
                'base': m['baseWeight'], 'base_color': m['baseColor'], 'metalness': m['metalness'],
                'specular': m['specular'], 'specular_color': m.get('specularColor',[1.0,1.0,1.0]),
                'specular_roughness': m['roughness'], 'specular_IOR': m['specularIor'],
                'transmission': m['transmission'], 'transmission_color': m['transmissionColor'],
                'emission': m['emission'], 'emission_color': m['emissionColor'],
                'coat': m['coat'], 'coat_roughness': m['coatRoughness'], 'coat_color': m['coatColor'],
                'sheen': m['sheen'], 'sheen_roughness': m['sheenRoughness'], 'sheen_color': m['sheenColor'],
                'subsurface': m['subsurface'], 'subsurface_color': m['subsurfaceColor'],
                'subsurface_radius': m['subsurfaceRadius'], 'subsurface_scale': m['subsurfaceScale'],
                'opacity': [m['opacity']]*3 if not isinstance(m['opacity'],list) else m['opacity'],
            }
            procedural_mtlx.append_procedural_graph_xml(root,surface,sub,get_uv_doc,get_pos_doc,base_values)

    material=ET.SubElement(root,'surfacematerial',name=m['name'],type='material');ET.SubElement(material,'input',name='surfaceshader',type='surfaceshader',nodename='Surface')
    ET.indent(root);return {'document':ET.tostring(root,encoding='unicode'),'name':m['name'],'note':'Material only; USD branch bindings and PreviewSurface fallback remain in the evaluated USD graph.'}


def apply_delete(stage,paths):
    from pxr import Sdf
    if not isinstance(paths,list) or len(paths)>1000:raise ValueError('Invalid Delete prim paths')
    for value in paths:
        path=Sdf.Path(value)
        if not path.IsAbsolutePath() or not path.IsPrimPath():raise ValueError('Delete needs absolute prim paths')
        prim=stage.GetPrimAtPath(path)
        if not prim:raise ValueError('Delete target not found: '+value)
        if prim.IsInstanceProxy():raise ValueError('Cannot delete an instance-proxy child; select the instance root: '+value)
    for value in sorted(set(paths),key=lambda p:(p.count('/'),p)):
        stage.OverridePrim(value).SetActive(False)


def graph_input_revision(graph,alive):
    from pxr import Usd,UsdUtils
    paths = {d['path'] for d in graph.get('dependencies', [])}
    for value in alive:
        if not isinstance(value, Usd.Stage):
            continue
        for layer in value.GetUsedLayers():
            if layer.realPath:
                paths.add(layer.realPath)
    for path in list(paths):
        if os.path.isfile(path) and path.lower().endswith(('.usd', '.usda', '.usdc', '.usdz')):
            layers, assets, missing = UsdUtils.ComputeAllDependencies(path)
            paths.update(layer.realPath for layer in layers if layer.realPath)
            paths.update(assets)
            paths.update(missing)
    revisions = []
    for path in sorted(paths):
        try:
            metadata = os.stat(path)
            revision = [asset_key(path), metadata.st_size, metadata.st_mtime_ns]
        except OSError:
            revision = [asset_key(path), 'missing']
        revisions.append(revision)
    digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return digest(revisions)


def materialx_rule_selection(stage,rules):
    from pxr import Usd,UsdGeom,UsdShade
    import re
    if not isinstance(rules,list) or len(rules)>256:raise ValueError('MaterialX supports up to 256 rules')
    candidates=[p for p in stage.Traverse(Usd.TraverseInstanceProxies()) if p.IsInstance() or p.IsA(UsdGeom.Gprim) or p.IsA(UsdGeom.Subset)]
    selected={};stats=[]
    for index,rule in enumerate(rules):
        query=rule.get('query','');mode=rule.get('searchType')
        if not isinstance(query,str) or len(query)>512 or mode not in ('simple','regex'):raise ValueError('Invalid MaterialX search rule')
        pattern=None
        if mode=='regex':
            try:pattern=re.compile(query)
            except re.error as error:raise ValueError('MaterialX row '+str(index+1)+' regex: '+str(error))
        paths=[];proxies=0;proxy_paths=[]
        if rule.get('enabled',True) and query:
            for prim in candidates:
                path=str(prim.GetPath())
                bound=UsdShade.MaterialBindingAPI(prim).GetDirectBinding().GetMaterial()
                mname=bound.GetPrim().GetName() if bound else ''
                mpath=str(bound.GetPath()) if bound else ''
                if mode=='regex':
                    matched=bool(pattern.search(path) or (mname and pattern.search(mname)) or (mpath and pattern.search(mpath)))
                else:
                    terms=[t.strip().casefold() for t in query.split(',') if t.strip()]
                    matched=False
                    matching_q=''
                    for q in terms:
                        if q=='*' or q in path.casefold() or (mname and q in mname.casefold()) or (mpath and q in mpath.casefold()):
                            matched=True
                            matching_q=q
                            break
                if not matched:continue
                if prim.IsInstanceProxy():
                    proxies+=1
                    proxy_paths.append(path)
                    continue
                display_path=f"{path} (material: {mname})" if (mode!='regex' and matching_q and mname and matching_q in mname.casefold() and matching_q not in path.casefold()) else path
                paths.append(display_path);selected[path]=index
        stats.append({'id':rule.get('id'),'matched':len(paths),'instanceProxyMatches':proxies,'paths':paths[:200],'proxyPaths':proxy_paths[:50]})
    for index,row in enumerate(stats):row['effective']=sum(owner==index for owner in selected.values())
    return selected,stats


def _apply_material_param_overrides(stage, targets, mat_settings, override_params):
    if not override_params:
        return
    from pxr import Gf, Sdf, Usd, UsdShade
    # Map MaterialXSettings keys -> (MaterialX input, UsdPreviewSurface input, Sdf.ValueTypeName, is_color)
    PARAM_MAP = {
        'baseColor': ('base_color', 'diffuseColor', Sdf.ValueTypeNames.Color3f, True),
        'roughness': ('specular_roughness', 'roughness', Sdf.ValueTypeNames.Float, False),
        'metalness': ('metalness', 'metallic', Sdf.ValueTypeNames.Float, False),
        'specular': ('specular', None, Sdf.ValueTypeNames.Float, False),
        'specularColor': ('specular_color', 'specularColor', Sdf.ValueTypeNames.Color3f, True),
        'specularIor': ('specular_IOR', 'ior', Sdf.ValueTypeNames.Float, False),
        'ior': ('specular_IOR', 'ior', Sdf.ValueTypeNames.Float, False),
        'opacity': ('opacity', 'opacity', Sdf.ValueTypeNames.Float, False),
        'coat': ('coat', 'clearcoat', Sdf.ValueTypeNames.Float, False),
        'coatColor': ('coat_color', None, Sdf.ValueTypeNames.Color3f, True),
        'coatRoughness': ('coat_roughness', 'clearcoatRoughness', Sdf.ValueTypeNames.Float, False),
        'sheen': ('sheen', None, Sdf.ValueTypeNames.Float, False),
        'sheenColor': ('sheen_color', None, Sdf.ValueTypeNames.Color3f, True),
        'sheenRoughness': ('sheen_roughness', None, Sdf.ValueTypeNames.Float, False),
        'transmission': ('transmission', None, Sdf.ValueTypeNames.Float, False),
        'transmissionColor': ('transmission_color', None, Sdf.ValueTypeNames.Color3f, True),
        'subsurface': ('subsurface', None, Sdf.ValueTypeNames.Float, False),
        'subsurfaceColor': ('subsurface_color', None, Sdf.ValueTypeNames.Color3f, True),
        'subsurfaceScale': ('subsurface_scale', None, Sdf.ValueTypeNames.Float, False),
        'subsurfaceRadius': ('subsurface_radius', None, Sdf.ValueTypeNames.Color3f, True),
        'emission': ('emission', None, Sdf.ValueTypeNames.Float, False),
        'emissionColor': ('emission_color', 'emissiveColor', Sdf.ValueTypeNames.Color3f, True),
    }
    seen_shaders = set()
    for path in targets:
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsValid():
            continue
        binding = UsdShade.MaterialBindingAPI(prim)
        for purpose in (UsdShade.Tokens.allPurpose, UsdShade.Tokens.full, UsdShade.Tokens.preview):
            mat, _ = binding.ComputeBoundMaterial(materialPurpose=purpose)
            if not mat:
                continue
            for p in Usd.PrimRange(mat.GetPrim()):
                if not p.IsA(UsdShade.Shader) or p.GetPath() in seen_shaders:
                    continue
                shader = UsdShade.Shader(p)
                sid = str(shader.GetIdAttr().Get() or '')
                is_preview = sid == 'UsdPreviewSurface'
                is_mtlx = sid == 'ND_standard_surface_surfaceshader' or 'standard_surface' in sid
                if not is_preview and not is_mtlx:
                    continue
                seen_shaders.add(p.GetPath())
                for key in override_params:
                    spec = PARAM_MAP.get(key)
                    if not spec or key not in mat_settings:
                        continue
                    mtlx_name, prev_name, vtype, is_color = spec
                    inp_name = prev_name if is_preview else mtlx_name
                    if not inp_name:
                        continue
                    val = mat_settings[key]
                    existing = shader.GetInput(inp_name)
                    target_type = existing.GetTypeName() if existing else vtype
                    inp = existing if existing else shader.CreateInput(inp_name, vtype)
                    if inp.HasConnectedSource():
                        inp.DisconnectSource()
                    if target_type == Sdf.ValueTypeNames.Color3f:
                        if isinstance(val, (list, tuple)) and len(val) >= 3:
                            c0, c1, c2 = float(val[0]), float(val[1]), float(val[2])
                            if inp_name == 'subsurface_radius':
                                c0, c1, c2 = max(0.005, c0), max(0.005, c1), max(0.005, c2)
                            inp.Set(Gf.Vec3f(c0, c1, c2))
                        else:
                            fv = float(val)
                            if inp_name == 'subsurface_radius':
                                fv = max(0.005, fv)
                            inp.Set(Gf.Vec3f(fv, fv, fv))
                    else:
                        fv = float(val[0]) if isinstance(val, (list, tuple)) else float(val)
                        if inp_name == 'subsurface_scale':
                            fv = max(1e-4, fv)
                        inp.Set(fv)


def author_materialx_rules(stage,node,files,procedural_graph=None,selected_override=None,selected_out=None):
    procedural_mtlx = _procedural_mtlx()
    rules=node.get('materialXRules')
    if rules is None:
        sub=procedural_mtlx.collect_rule_subgraph(procedural_graph,node.get('id'),None)
        return author_materialx(stage,node.get('materialX') or {},files,procedural_subgraph=sub)
    if selected_override is not None:
        selected,stats=selected_override,[]
    else:
        selected,stats=materialx_rule_selection(stage,rules)
    if selected_out is not None:
        selected_out.clear()
        selected_out.update(selected)
    for index,rule in enumerate(rules):
        targets=[path for path,owner in selected.items() if owner==index]
        if targets:
            if rule.get('mode') == 'override':
                _apply_material_param_overrides(stage, targets, rule.get('material') or {}, rule.get('overrideParams') or [])
                continue
            sub=procedural_mtlx.collect_rule_subgraph(procedural_graph,node.get('id'),rule.get('id'))
            material=author_materialx(stage,{**rule['material'],'bindingTargets':targets},files,procedural_subgraph=sub)
            from pxr import Sdf,UsdShade
            for path in targets:
                if any(other!=path and Sdf.Path(other).HasPrefix(Sdf.Path(path)) for other in selected):
                    for purpose in (UsdShade.Tokens.allPurpose,UsdShade.Tokens.full,UsdShade.Tokens.preview):
                        UsdShade.MaterialBindingAPI(stage.GetPrimAtPath(path)).Bind(material,bindingStrength=UsdShade.Tokens.weakerThanDescendants,materialPurpose=purpose)
    return stats


def materialx_rule_matches(request):
    graph=request['graph'];target=graph['targetNodeId']
    node=next(n for n in graph['nodes'] if n['id']==target)
    if node['operation']!='materialx':raise ValueError('Choose a MaterialX node')
    input_graph={**graph,'nodes':[{**n,'operation':'assetOverride','assetOverrides':[]} if n['id']==target else n for n in graph['nodes']]}
    stage,alive,_=compile_graph(input_graph,reload=bool(request.get('reload',False)))
    rules=node.get('materialXRules',[])
    files={d['id']:d['path'] for d in graph['dependencies']}
    for rule in rules:materialx_settings({**rule['material'],'bindingTargets':rule['material'].get('bindingTargets') or ['*']},files)
    _,rows=materialx_rule_selection(stage,rules)
    return {'rows':rows,'note':'Last matching row wins. Instance-proxy descendants are skipped; target their instance root.'}


def asset_override_inputs(request):
    """Discover external USD and asset paths arriving at an Asset Override node."""
    from pxr import Sdf, UsdUtils
    graph = request['graph']
    if isinstance(graph, str):
        graph = json.loads(graph)
    target = graph['targetNodeId']
    node = next((n for n in graph.get('nodes', []) if n['id'] == target), None)
    if not node or node.get('operation') != 'assetOverride':
        raise ValueError('Choose an Asset Override node')
    if not any(e.get('target') == target for e in graph.get('edges', [])):
        return {'files': []}
    input_graph = {
        **graph,
        'nodes': [
            {**n, 'assetOverrides': []} if n['id'] == target else n
            for n in graph.get('nodes', [])
        ],
    }
    stage, alive, _ = compile_graph(input_graph, reload=bool(request.get('reload', False)))
    layers = {layer.identifier: layer for layer in stage.GetUsedLayers()}
    pending = list(layers.values())
    while pending:
        owner = pending.pop()
        for path in owner.GetCompositionAssetDependencies():
            if not path or path.startswith('anon:'):
                continue
            child = Sdf.Layer.FindOrOpen(Sdf.ComputeAssetPathRelativeToLayer(owner, path))
            if child and child.identifier not in layers:
                layers[child.identifier] = child
                pending.append(child)
    discovered = {}
    for layer in layers.values():
        origins = json.loads(layer.customLayerData.get('orchflowAssetOrigins', '{}'))
        probe, _ = asset_override_probe(layer)
        def inspect(path):
            if not path or path.startswith('anon:'):
                return path
            anchored = Sdf.ComputeAssetPathRelativeToLayer(layer, path)
            if not anchored or anchored.startswith('anon:'):
                return path
            original = origins.get(asset_key(anchored), anchored)
            if not original or original.startswith('anon:'):
                return path
            key = asset_key(original)
            if key not in discovered:
                kind = 'usd' if os.path.splitext(original)[1].lower() in ('.usd', '.usda', '.usdc', '.usdz') else 'asset'
                discovered[key] = {'path': original, 'kind': kind}
            return path
        UsdUtils.ModifyAssetPaths(probe, inspect)
    return {'files': list(discovered.values())}


def compose_material_binding_node(inputs, node):
    from pxr import Sdf, Usd, UsdGeom, UsdShade
    keepalive = []
    if len(inputs) == 1:
        upstream = inputs[0][1]
        overlay = Sdf.Layer.CreateAnonymous('material_bind.usda')
        overlay.subLayerPaths = [upstream.GetRootLayer().identifier]
        stage = Usd.Stage.Open(overlay)
        inherit_stage_metrics(stage, upstream)
        stage.SetDefaultPrim(stage.GetPrimAtPath(upstream.GetDefaultPrim().GetPath()))
        keepalive.extend([upstream, overlay, stage])
    else:
        stage = Usd.Stage.CreateInMemory()
        root = UsdGeom.Xform.Define(stage, '/Scene')
        stage.SetDefaultPrim(root.GetPrim())
        inherit_stage_metrics(stage, inputs[0][1])
        stage.SetStartTimeCode(min(s.GetStartTimeCode() for _, s in inputs))
        stage.SetEndTimeCode(max(s.GetEndTimeCode() for _, s in inputs))
        keepalive.append(stage)

        geo_inputs = [inp for inp in inputs if not inp[0].endswith('_materials')]
        mat_inputs = [inp for inp in inputs if inp[0].endswith('_materials')]
        if not geo_inputs:
            geo_inputs = [inputs[0]]
            mat_inputs = inputs[1:]
        elif not mat_inputs and len(inputs) > 1:
            mat_inputs = [inputs[1]]

        geo_stage = geo_inputs[0][1]
        root_prim = stage.GetDefaultPrim()
        root_prim.GetReferences().AddReference(geo_stage.GetRootLayer().identifier, geo_stage.GetDefaultPrim().GetPath())
        keepalive.append(geo_stage)

        for edge_id, mat_stage in mat_inputs:
            name = 'MatLib_' + hashlib.sha256(edge_id.encode()).hexdigest()[:8]
            mat_container = stage.OverridePrim('/Materials/' + name)
            mat_container.GetReferences().AddReference(mat_stage.GetRootLayer().identifier, mat_stage.GetDefaultPrim().GetPath())
            keepalive.append(mat_stage)

    apply_material_bindings(stage, node.get('materialBindings', []))
    return stage, keepalive


def apply_material_bindings(stage, bindings):
    from pxr import Sdf, Usd, UsdGeom, UsdShade
    import re
    if not isinstance(bindings, list):
        return
    candidates = [p for p in stage.Traverse(Usd.TraverseInstanceProxies()) if p.IsInstance() or p.IsA(UsdGeom.Gprim) or p.IsA(UsdGeom.Subset)]
    for binding in bindings:
        if not binding.get('enabled', True):
            continue
        target_query = binding.get('targetQuery', '').strip()
        mat_path_str = binding.get('materialPath', '').strip()
        strength_str = binding.get('strength', 'strongerThanDescendants')
        if not target_query or not mat_path_str:
            continue

        mat_prim = None
        for p in stage.Traverse():
            if p.IsA(UsdShade.Material):
                if str(p.GetPath()) == mat_path_str or str(p.GetPath()).endswith(mat_path_str) or p.GetName() == mat_path_str.lstrip('/'):
                    mat_prim = p
                    break
        if not mat_prim:
            mat_prim = stage.GetPrimAtPath(Sdf.Path(mat_path_str))
        if not mat_prim or not mat_prim.IsValid():
            continue
        material = UsdShade.Material(mat_prim)

        strength = UsdShade.Tokens.strongerThanDescendants if strength_str == 'strongerThanDescendants' else UsdShade.Tokens.weakerThanDescendants

        is_regex = target_query.startswith('^') or target_query.endswith('$')
        pattern = re.compile(target_query) if is_regex else None

        for prim in candidates:
            if prim.IsInstanceProxy():
                continue
            path_str = str(prim.GetPath())
            matched = False
            if pattern:
                matched = bool(pattern.search(path_str))
            elif '*' in target_query:
                regex_pat = '^' + re.escape(target_query).replace('\\*', '.*') + '$'
                matched = bool(re.match(regex_pat, path_str))
            else:
                matched = (path_str == target_query) or (target_query.lower() in path_str.lower())

            if matched:
                binding_api = UsdShade.MaterialBindingAPI.Apply(prim)
                binding_api.Bind(material, bindingStrength=strength)


def decompose_camera_transform(matrix, stage=None, time_code=None):
    from pxr import Gf, Usd, UsdGeom
    local_matrix = matrix
    if stage is not None:
        scene_prim = stage.GetPrimAtPath('/Scene')
        if scene_prim and scene_prim.IsValid():
            tc = time_code if time_code is not None else Usd.TimeCode.Default()
            scene_xf = UsdGeom.XformCache(tc).GetLocalToWorldTransform(scene_prim)
            if abs(scene_xf.GetDeterminant()) > 1e-18:
                local_matrix = matrix * scene_xf.GetInverse()
    t = local_matrix.ExtractTranslation()
    rot = local_matrix.RemoveScaleShear().ExtractRotation()
    rz, ry, rx = rot.Decompose(Gf.Vec3d(0, 0, 1), Gf.Vec3d(0, 1, 0), Gf.Vec3d(1, 0, 0))
    return {
        'translate': [round(float(t[0]), 4), round(float(t[1]), 4), round(float(t[2]), 4)],
        'rotate': [round(float(rx), 3), round(float(ry), 3), round(float(rz), 3)],
    }


def apply_light_overrides(stage, overrides):
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux
    if not isinstance(overrides, list):
        return
    valid_types = {
        'DistantLight', 'RectLight', 'DiskLight', 'SphereLight', 'CylinderLight', 'DomeLight'
    }
    for item in overrides:
        if not isinstance(item, dict):
            continue
        path_str = item.get('lightPath', '').strip()
        if not path_str:
            continue

        mode = item.get('mode')
        prim = stage.GetPrimAtPath(Sdf.Path(path_str))
        if mode != 'create' and (not prim or not prim.IsValid()):
            alt_path = Sdf.Path('/Scene' + ('' if path_str.startswith('/') else '/') + path_str)
            alt_prim = stage.GetPrimAtPath(alt_path)
            if alt_prim and alt_prim.IsValid():
                prim = alt_prim
                path_str = str(prim.GetPath())
            else:
                for p in stage.Traverse(Usd.TraverseInstanceProxies()):
                    p_path = str(p.GetPath())
                    if ('Light' in p.GetTypeName() or (hasattr(UsdLux, 'LightAPI') and p.HasAPI(UsdLux.LightAPI))):
                        if p_path.endswith(path_str) or p.GetName() == path_str.lstrip('/'):
                            prim = p
                            path_str = p_path
                            break

        light_type = item.get('lightType')
        if light_type not in valid_types:
            light_type = None

        if mode == 'create' or not prim or not prim.IsValid():
            target_path = path_str if path_str.startswith('/') else ('/Scene/' + path_str)
            sdf_path = Sdf.Path(target_path)
            # Ensure intermediate parent prims exist as Xforms so created lights are valid in Hydra
            parent_path = sdf_path.GetParentPath()
            ancestors = []
            while parent_path and parent_path != Sdf.Path.absoluteRootPath:
                ancestors.append(parent_path)
                parent_path = parent_path.GetParentPath()
            for anc in reversed(ancestors):
                anc_prim = stage.GetPrimAtPath(anc)
                if not anc_prim or not anc_prim.IsValid():
                    UsdGeom.Xform.Define(stage, anc)
            if not light_type:
                lower = target_path.lower()
                if 'dome' in lower or 'env' in lower or 'ambient' in lower:
                    light_type = 'DomeLight'
                elif 'sphere' in lower or 'point' in lower:
                    light_type = 'SphereLight'
                elif 'rect' in lower or 'area' in lower:
                    light_type = 'RectLight'
                elif 'disk' in lower:
                    light_type = 'DiskLight'
                elif 'cylinder' in lower:
                    light_type = 'CylinderLight'
                else:
                    light_type = 'DistantLight'
            define_cls = getattr(UsdLux, light_type, UsdLux.DistantLight)
            light_schema = define_cls.Define(stage, sdf_path)
            prim = light_schema.GetPrim()
            if hasattr(UsdLux, 'ShadowAPI'):
                shadow_api = UsdLux.ShadowAPI.Apply(prim)
                shadow_api.CreateShadowEnableAttr().Set(True)
            path_str = str(prim.GetPath())

        if prim.IsInstanceProxy():
            curr = prim
            while curr and not curr.IsPseudoRoot():
                if curr.IsInstance():
                    curr.SetInstanceable(False)
                    break
                curr = curr.GetParent()
            prim = stage.GetPrimAtPath(Sdf.Path(path_str))
            if not prim or not prim.IsValid():
                continue

        if hasattr(UsdLux, 'LightAPI') and not prim.HasAPI(UsdLux.LightAPI):
            UsdLux.LightAPI.Apply(prim)

        # Apply transform if specified
        t_val = item.get('translate')
        r_val = item.get('rotate')
        s_val = item.get('scale')
        if t_val is not None or r_val is not None or s_val is not None:
            xformable = UsdGeom.Xformable(prim)
            xformable.ClearXformOpOrder()
            xform_api = UsdGeom.XformCommonAPI(prim)
            if isinstance(t_val, list) and len(t_val) == 3:
                xform_api.SetTranslate(Gf.Vec3d(float(t_val[0]), float(t_val[1]), float(t_val[2])))
            if isinstance(r_val, list) and len(r_val) == 3:
                xform_api.SetRotate(
                    Gf.Vec3f(float(r_val[0]), float(r_val[1]), float(r_val[2])),
                    UsdGeom.XformCommonAPI.RotationOrderXYZ,
                )
            if isinstance(s_val, list) and len(s_val) == 3:
                xform_api.SetScale(Gf.Vec3f(float(s_val[0]), float(s_val[1]), float(s_val[2])))

        enabled = item.get('enabled', True)
        if not enabled:
            if prim.HasAttribute('inputs:intensity'):
                prim.GetAttribute('inputs:intensity').Set(0.0)
            if prim.HasAttribute('intensity'):
                prim.GetAttribute('intensity').Set(0.0)
            if not prim.HasAttribute('inputs:intensity') and not prim.HasAttribute('intensity'):
                prim.CreateAttribute('inputs:intensity', Sdf.ValueTypeNames.Float).Set(0.0)
            continue

        if item.get('intensity') is not None:
            val = float(item['intensity'])
            attr = prim.GetAttribute('inputs:intensity') if prim.HasAttribute('inputs:intensity') else prim.CreateAttribute('inputs:intensity', Sdf.ValueTypeNames.Float)
            attr.Set(val)
            if prim.HasAttribute('intensity'):
                prim.GetAttribute('intensity').Set(val)

        if item.get('exposure') is not None:
            val = float(item['exposure'])
            attr = prim.GetAttribute('inputs:exposure') if prim.HasAttribute('inputs:exposure') else prim.CreateAttribute('inputs:exposure', Sdf.ValueTypeNames.Float)
            attr.Set(val)
            if prim.HasAttribute('exposure'):
                prim.GetAttribute('exposure').Set(val)

        if item.get('color') is not None:
            c = item['color']
            if isinstance(c, list) and len(c) == 3:
                vec = Gf.Vec3f(float(c[0]), float(c[1]), float(c[2]))
                attr = prim.GetAttribute('inputs:color') if prim.HasAttribute('inputs:color') else prim.CreateAttribute('inputs:color', Sdf.ValueTypeNames.Color3f)
                attr.Set(vec)
                if prim.HasAttribute('color'):
                    prim.GetAttribute('color').Set(vec)

        if item.get('enableColorTemperature') is not None:
            b_val = bool(item['enableColorTemperature'])
            attr = prim.GetAttribute('inputs:enableColorTemperature') if prim.HasAttribute('inputs:enableColorTemperature') else prim.CreateAttribute('inputs:enableColorTemperature', Sdf.ValueTypeNames.Bool)
            attr.Set(b_val)
            if prim.HasAttribute('enableColorTemperature'):
                prim.GetAttribute('enableColorTemperature').Set(b_val)

        if item.get('colorTemperature') is not None:
            val = float(item['colorTemperature'])
            attr = prim.GetAttribute('inputs:colorTemperature') if prim.HasAttribute('inputs:colorTemperature') else prim.CreateAttribute('inputs:colorTemperature', Sdf.ValueTypeNames.Float)
            attr.Set(val)
            if prim.HasAttribute('colorTemperature'):
                prim.GetAttribute('colorTemperature').Set(val)

        for field, attr_name in (
            ('width', 'inputs:width'),
            ('height', 'inputs:height'),
            ('radius', 'inputs:radius'),
            ('angle', 'inputs:angle'),
        ):
            if item.get(field) is not None:
                val = float(item[field])
                attr = prim.GetAttribute(attr_name) if prim.HasAttribute(attr_name) else prim.CreateAttribute(attr_name, Sdf.ValueTypeNames.Float)
                attr.Set(val)


def inspect_stage_materials(request):
    from pxr import Usd, UsdShade
    graph = request['graph']
    stage, alive, _ = compile_graph(graph, reload=bool(request.get('reload', False)))
    materials = []
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if prim.IsA(UsdShade.Material):
            materials.append({
                'path': str(prim.GetPath()),
                'name': prim.GetName(),
            })
    return {'materials': materials}


def inspect_stage_lights(request):
    from pxr import Usd, UsdGeom, UsdLux
    graph = request['graph']
    stage, alive, _ = compile_graph(graph, reload=bool(request.get('reload', False)))
    lights = []
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        type_name = prim.GetTypeName()
        is_light = 'Light' in type_name or prim.HasAPI(UsdLux.LightAPI) if hasattr(UsdLux, 'LightAPI') else ('Light' in type_name)
        if is_light:
            intensity = 1.0
            if prim.HasAttribute('inputs:intensity'):
                intensity = prim.GetAttribute('inputs:intensity').Get() or 1.0
            elif prim.HasAttribute('intensity'):
                intensity = prim.GetAttribute('intensity').Get() or 1.0

            exposure = 0.0
            if prim.HasAttribute('inputs:exposure'):
                exposure = prim.GetAttribute('inputs:exposure').Get() or 0.0
            elif prim.HasAttribute('exposure'):
                exposure = prim.GetAttribute('exposure').Get() or 0.0

            color = [1.0, 1.0, 1.0]
            if prim.HasAttribute('inputs:color'):
                val = prim.GetAttribute('inputs:color').Get()
                if val:
                    color = [float(val[0]), float(val[1]), float(val[2])]
            elif prim.HasAttribute('color'):
                val = prim.GetAttribute('color').Get()
                if val:
                    color = [float(val[0]), float(val[1]), float(val[2])]

            temp_enabled = False
            if prim.HasAttribute('inputs:enableColorTemperature'):
                temp_enabled = bool(prim.GetAttribute('inputs:enableColorTemperature').Get())
            elif prim.HasAttribute('enableColorTemperature'):
                temp_enabled = bool(prim.GetAttribute('enableColorTemperature').Get())

            temp = 6500.0
            if prim.HasAttribute('inputs:colorTemperature'):
                temp = float(prim.GetAttribute('inputs:colorTemperature').Get() or 6500.0)
            elif prim.HasAttribute('colorTemperature'):
                temp = float(prim.GetAttribute('colorTemperature').Get() or 6500.0)

            xform_data = {'translate': [0.0, 0.0, 0.0], 'rotate': [0.0, 0.0, 0.0]}
            try:
                local_mat = UsdGeom.Xformable(prim).GetLocalTransformation(Usd.TimeCode.Default())
                xform_data = decompose_camera_transform(local_mat)
            except Exception:
                pass

            lights.append({
                'path': str(prim.GetPath()),
                'name': prim.GetName(),
                'type': type_name,
                'intensity': float(intensity),
                'exposure': float(exposure),
                'color': color,
                'enableColorTemperature': temp_enabled,
                'colorTemperature': temp,
                'translate': xform_data['translate'],
                'rotate': xform_data['rotate'],
            })
    meters_per_unit = float(UsdGeom.GetStageMetersPerUnit(stage) or 1.0)
    up_axis = str(UsdGeom.GetStageUpAxis(stage) or 'Y')
    scene_bounds = None
    try:
        from viewport_bounds import compute_visible_geometry_bounds
        bbox_range = compute_visible_geometry_bounds(stage, Usd.TimeCode.Default())
        if bbox_range and not bbox_range.IsEmpty():
            bmin = bbox_range.GetMin()
            bmax = bbox_range.GetMax()
            size = bmax - bmin
            center = (bmin + bmax) * 0.5
            radius = float(size.GetLength()) * 0.5
            if radius > 1e-6:
                scene_bounds = {
                    'center': [float(center[0]), float(center[1]), float(center[2])],
                    'size': [float(size[0]), float(size[1]), float(size[2])],
                    'radius': radius,
                }
    except Exception:
        pass
    return {
        'lights': lights,
        'metersPerUnit': meters_per_unit,
        'upAxis': up_axis,
        'sceneBounds': scene_bounds,
    }


def reuse_handoff(request):
    import copy
    from pxr import Usd,UsdGeom
    donor=request['donorGraph'];graph=request['graph']
    def semantic(value):
        value=copy.deepcopy(value);target=value['targetNodeId'];value['targetNodeId']='@target'
        for node in value['nodes']:
            if node['id']==target:node['id']='@target'
        for edge in value['edges']:
            if edge['target']==target:edge['target']='@target';edge['id']='@input:'+edge['source']
        value['nodes'].sort(key=lambda n:n['id']);value['edges'].sort(key=lambda e:e['id']);value['dependencies'].sort(key=lambda d:d['id'])
        return graph_digest(value)
    if semantic(graph)!=semantic(donor):raise ValueError('Shared handoff inputs differ; create a separate handoff')
    checked=coordinate_preflight({'graph':donor,'app':request['policy']['profile']['app'],'upAxis':request['conversion']['upAxis'],'metersPerUnit':request['conversion']['metersPerUnit'],'scale':request['conversion']['scale'],'measure':False})
    if checked['draft']['inputRevision']!=request['conversion']['inputRevision'] or graph_digest(donor)!=request['conversion']['graphRevision']:raise ValueError('Original handoff preparation is out of date; create a new handoff')
    layer=Usd.Stage.Open(request['path'])
    if not layer:raise ValueError('Unable to open shared handoff')
    layer.Reload()
    if layer.GetRootLayer().customLayerData.get('orchflowNamespaceVersion')!=2:raise ValueError('Recreate this handoff using the current simplified scene hierarchy before reusing it')
    root=layer.GetDefaultPrim();identity=root.GetAttribute('userProperties:orchflowRoundTripId').Get() if root else None
    if not identity or (request.get('roundTripId') and identity!=request['roundTripId']):raise ValueError('Shared handoff round-trip identity changed')
    if str(UsdGeom.GetStageUpAxis(layer))!=checked['draft']['upAxis'] or UsdGeom.GetStageMetersPerUnit(layer)!=checked['draft']['metersPerUnit']:raise ValueError('Shared handoff metadata differs from preparation')
    edges={e['source']:e['id'] for e in donor['edges'] if e['target']==donor['targetNodeId']}
    for edge in graph['edges']:
        if edge['target']==graph['targetNodeId']:edge['id']=edges[edge['source']]
    checked['draft']['graphRevision']=graph_digest(graph)
    checked['draft']['app']=request['policy']['profile']['app']
    return {'path':request['path'],'layerPaths':request['layerPaths'],'policy':request['policy'],'draft':checked['draft'],'inputEdges':edges,'selection':request.get('selection')}


def apply_hdri(stage, hdri_cfg, files):
    from pxr import Gf, Sdf, UsdGeom, UsdLux
    cfg = hdri_cfg if isinstance(hdri_cfg, dict) else {}
    dome_path = Sdf.Path('/OrchFlowHDRI')
    dome = UsdLux.DomeLight.Define(stage, dome_path)
    if hasattr(UsdLux, 'ShadowAPI'):
        UsdLux.ShadowAPI.Apply(dome.GetPrim()).CreateShadowEnableAttr().Set(True)
    dome.CreateIntensityAttr().Set(float(cfg.get('intensity', 1.0)))
    dome.CreateExposureAttr().Set(float(cfg.get('exposure', 0.0)))
    col = cfg.get('color') or [1.0, 1.0, 1.0]
    if isinstance(col, list) and len(col) >= 3:
        dome.CreateColorAttr().Set(Gf.Vec3f(float(col[0]), float(col[1]), float(col[2])))
    fmt = str(cfg.get('textureFormat') or 'latlong')
    if fmt not in ('latlong', 'automatic', 'angular', 'mirroredBall'):
        fmt = 'latlong'
    dome.CreateTextureFormatAttr().Set(fmt)
    dep_id = cfg.get('textureDependencyId')
    if dep_id and dep_id in files:
        dome.CreateTextureFileAttr().Set(Sdf.AssetPath(files[dep_id]))
    dome.ClearXformOpOrder()
    dome.OrientToStageUpAxis()
    rot = float(cfg.get('rotation', 0.0))
    if abs(rot) > 1e-6:
        dome.AddRotateYOp().Set(rot)


def _hair_query_matches(raw_query, prim_path_str):
    import fnmatch

    terms = [t.strip().lower() for t in str(raw_query or '').split(',') if t.strip()]
    includes = [t for t in terms if not t.startswith('!')]
    excludes = [t[1:].strip() for t in terms if t.startswith('!') and t[1:].strip()]

    low = prim_path_str.lower()
    name_low = prim_path_str.rsplit('/', 1)[-1].lower()

    def _hit(term):
        if term == '*':
            return True
        if any(ch in term for ch in ('*', '?', '[')):
            return fnmatch.fnmatchcase(low, term) or fnmatch.fnmatchcase(name_low, term)
        if term.startswith('/'):
            return low == term or low.startswith(term.rstrip('/') + '/')
        return term in low

    if excludes and any(_hit(t) for t in excludes):
        return False
    if not includes or '*' in includes:
        return True
    return any(_hit(t) for t in includes)


_SKELETON_INSPECT_CACHE = None


def pose_only_node(prev_graph, next_graph, live_poses):
    if not isinstance(prev_graph, dict) or not isinstance(next_graph, dict) or not live_poses:
        return None
    target_id = next_graph.get('targetNodeId')
    if not target_id or prev_graph.get('targetNodeId') != target_id:
        return None
    if any(
        prev_graph.get(k) != next_graph.get(k)
        for k in set(prev_graph.keys()) | set(next_graph.keys())
        if k != 'nodes'
    ):
        return None
    prev_nodes = prev_graph.get('nodes')
    next_nodes = next_graph.get('nodes')
    if not isinstance(prev_nodes, list) or not isinstance(next_nodes, list) or len(prev_nodes) != len(next_nodes):
        return None
    changed_node = None
    for pn, nn in zip(prev_nodes, next_nodes):
        if pn == nn:
            continue
        if (
            not isinstance(pn, dict)
            or not isinstance(nn, dict)
            or pn.get('id') != nn.get('id')
            or nn.get('id') not in live_poses
            or nn.get('operation') != 'pose'
        ):
            return None
        if {k: v for k, v in pn.items() if k != 'pose'} != {k: v for k, v in nn.items() if k != 'pose'}:
            return None
        if changed_node is not None:
            return None
        changed_node = nn
    return changed_node


def inspect_stage_skeletons(request):
    global _SKELETON_INSPECT_CACHE
    from pxr import Gf, Usd, UsdGeom, UsdSkel

    if request.get('path'):
        stage = _open_source_stage(request['path'], reload=bool(request.get('reload', False)))
        alive = [stage]
    else:
        import copy
        graph = request['graph']
        reload_flag = bool(request.get('reload', False))
        stage = None
        if not reload_flag and _SKELETON_INSPECT_CACHE is not None:
            c_graph, c_stage, c_alive, c_poses = _SKELETON_INSPECT_CACHE
            if graph == c_graph:
                stage, alive = c_stage, c_alive
            else:
                changed = pose_only_node(c_graph, graph, c_poses)
                if changed is not None:
                    from pose_node import update_live_pose
                    if update_live_pose(changed, c_poses[changed['id']]):
                        _SKELETON_INSPECT_CACHE = (graph, c_stage, c_alive, c_poses)
                        stage, alive = c_stage, c_alive
        if stage is None:
            live_poses = {}
            stage, alive, _ = compile_graph(graph, reload_flag, live_poses=live_poses)
            _SKELETON_INSPECT_CACHE = (graph, stage, alive, live_poses)
    cache = UsdSkel.Cache()
    for p in stage.Traverse():
        if p.IsA(UsdSkel.Root):
            cache.Populate(UsdSkel.Root(p), Usd.PrimDefaultPredicate)
    sample_time = float(request.get('frame', stage.GetStartTimeCode()))
    xforms = UsdGeom.XformCache(sample_time)
    skeletons = []
    animations = []
    prims = []
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if prim.IsA(UsdSkel.Skeleton):
            skel = UsdSkel.Skeleton(prim)
            joints = skel.GetJointsAttr().Get()
            query = cache.GetSkelQuery(skel)
            anim_query = query.GetAnimQuery() if query else None
            blend_shapes = list(anim_query.GetBlendShapeOrder()) if anim_query else []
            blend_weights = list(anim_query.ComputeBlendShapeWeights(sample_time) or []) if anim_query else []
            local = query.ComputeJointLocalTransforms(sample_time) if query else skel.GetRestTransformsAttr().Get()
            joint_data = []
            world_points = []
            world_axes = []
            if local:
                translations, rotations, scales = UsdSkel.DecomposeTransforms(local)
                joint_data = [{'name':str(name), 'translation':list(t), 'rotation':[q.GetReal(), *q.GetImaginary()], 'scale':list(scale), 'rotationOffset':[0,0,0], 'translationOffset':[0,0,0]} for name,t,q,scale in zip(joints, translations, rotations, scales)]
                world = xforms.GetLocalToWorldTransform(prim)
                skel_transforms = query.ComputeJointSkelTransforms(sample_time) if query else []
                world_mats = [m * world for m in skel_transforms]
                world_points = [list(wm.ExtractTranslation()) for wm in world_mats]
                world_axes = [
                    [
                        list(wm.TransformDir(Gf.Vec3d(1, 0, 0)).GetNormalized()),
                        list(wm.TransformDir(Gf.Vec3d(0, 1, 0)).GetNormalized()),
                        list(wm.TransformDir(Gf.Vec3d(0, 0, 1)).GetNormalized()),
                    ]
                    for wm in world_mats
                ]
            skeletons.append({
                'path': str(prim.GetPath()),
                'name': prim.GetName(),
                'joints': [str(j) for j in joints] if joints else [],
                'blendShapes':blend_shapes, 'blendShapeWeights':blend_weights,
                'poseJoints': joint_data, 'worldPoints':world_points, 'worldAxes': world_axes, 'parents':list(UsdSkel.Topology(joints).GetParentIndices()),
                'instanceProxy': bool(prim.IsInstanceProxy()),
            })
        elif prim.IsA(UsdSkel.Animation):
            anim = UsdSkel.Animation(prim)
            joints = anim.GetJointsAttr().Get()
            samples = set()
            for attr in (anim.GetTranslationsAttr(), anim.GetRotationsAttr(), anim.GetScalesAttr()):
                if attr:
                    samples.update(attr.GetTimeSamples())
            sorted_samples = sorted(samples)
            animations.append({
                'path': str(prim.GetPath()),
                'name': prim.GetName(),
                'jointCount': int(len(joints)) if joints else 0,
                'startTime': float(sorted_samples[0]) if sorted_samples else float(stage.GetStartTimeCode()),
                'endTime': float(sorted_samples[-1]) if sorted_samples else float(stage.GetEndTimeCode()),
                'timeSampleCount': len(sorted_samples),
                'instanceProxy': bool(prim.IsInstanceProxy()),
            })
        elif prim.IsA(UsdGeom.Xformable) and str(prim.GetPath()) != '/Scene':
            prims.append({
                'path': str(prim.GetPath()),
                'name': prim.GetName(),
                'typeName': prim.GetTypeName(),
                'isMesh': bool(prim.IsA(UsdGeom.Mesh)),
            })
    return {'skeletons': skeletons, 'animations': animations, 'prims': prims}


def _build_offset_matrix(translation, rotation_deg, scale):
    from pxr import Gf

    tx, ty, tz = [float(v) for v in (translation or [0.0, 0.0, 0.0])]
    rx, ry, rz = [float(v) for v in (rotation_deg or [0.0, 0.0, 0.0])]
    sx, sy, sz = [float(v) for v in (scale or [1.0, 1.0, 1.0])]
    mat = Gf.Matrix4d(1.0)
    mat.SetScale(Gf.Vec3d(sx, sy, sz))
    rot = (
        Gf.Rotation(Gf.Vec3d(1, 0, 0), rx)
        * Gf.Rotation(Gf.Vec3d(0, 1, 0), ry)
        * Gf.Rotation(Gf.Vec3d(0, 0, 1), rz)
    )
    rot_mat = Gf.Matrix4d(1.0)
    rot_mat.SetRotate(rot)
    mat = mat * rot_mat
    mat.SetTranslateOnly(Gf.Vec3d(tx, ty, tz))
    return mat


def _filter_outermost_prims(prims):
    paths = sorted((str(p.GetPath()), p) for p in prims)
    outermost = []
    accepted_prefixes = []
    for path_str, prim in paths:
        if any(path_str == pref or path_str.startswith(pref + '/') for pref in accepted_prefixes):
            continue
        outermost.append(prim)
        accepted_prefixes.append(path_str)
    return outermost


def _find_joint_index(joint_order, wanted_name):
    wanted = str(wanted_name or '').strip()
    if not wanted:
        return None
    for idx, j in enumerate(joint_order):
        if str(j) == wanted:
            return idx
    wanted_low = wanted.lower()
    wanted_leaf = wanted_low.rsplit('/', 1)[-1].split(':')[-1]
    for idx, j in enumerate(joint_order):
        j_low = str(j).lower()
        j_leaf = j_low.rsplit('/', 1)[-1].split(':')[-1]
        if j_low == wanted_low or j_leaf == wanted_leaf:
            return idx
    return None


def compose_character_node(inputs, node, files=None, reload=False, wrap_fn=None):
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdSkel

    stage = Usd.Stage.CreateInMemory()
    root = UsdSkel.Root.Define(stage, '/Scene').GetPrim()
    stage.SetDefaultPrim(root)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    stage.SetStartTimeCode(min(s.GetStartTimeCode() for _, s in inputs))
    stage.SetEndTimeCode(max(s.GetEndTimeCode() for _, s in inputs))
    keepalive = [stage]

    for edge_id, upstream in inputs:
        name = 'Branch_' + hashlib.sha256(edge_id.encode()).hexdigest()[:16]
        prim = stage.OverridePrim('/Scene/' + name)
        prim.GetReferences().AddReference(
            upstream.GetRootLayer().identifier,
            upstream.GetDefaultPrim().GetPath(),
        )
        mark_reference_instanceable(prim, upstream, enable=False)
        keepalive.append(upstream)

    # Consolidate everything under one UsdSkel.Root at /Scene so UsdSkel.Cache
    # and Hydra skinning evaluate bound clothing and accessories uniformly.
    # Also clear instanceable on branch/source wrappers so accessories and clothing
    # can be edited (transforms, skeleton bindings, deactivation) without proxy errors.
    root.SetTypeName('SkelRoot')
    while True:
        instances = [p for p in stage.Traverse() if p.IsInstance()]
        if not instances:
            break
        for inst in instances:
            inst.SetInstanceable(False)
    for prim in list(stage.Traverse()):
        if prim.GetPath() != root.GetPath() and prim.GetTypeName() == 'SkelRoot':
            prim.SetTypeName('Xform')

    cfg = node.get('character') or {}
    base_skel_path = str(cfg.get('baseSkeletonPath') or '').strip()

    def _resolve_skeleton(path_str):
        cand = str(path_str or '').strip() or base_skel_path
        if cand:
            p = stage.GetPrimAtPath(cand)
            if p and p.IsValid() and p.IsA(UsdSkel.Skeleton):
                return p
            for sk in stage.Traverse():
                if sk.IsA(UsdSkel.Skeleton) and str(sk.GetPath()).endswith(cand):
                    return sk
        for sk in stage.Traverse():
            if sk.IsA(UsdSkel.Skeleton):
                return sk
        return None

    # 0. Animation / Pose overrides (applied before skeleton bindings and joint attachments)
    anim_overrides = [
        r for r in (cfg.get('animationOverrides') or [])
        if r.get('enabled', True) and str(r.get('animationPath') or '').strip()
    ]
    if anim_overrides:
        pose_cache = UsdSkel.Cache()
        for idx, rule in enumerate(anim_overrides):
            target_skel = _resolve_skeleton(rule.get('skeletonPath'))
            if not target_skel:
                continue
            anim_path_str = str(rule.get('animationPath') or '').strip()
            anim_prim = stage.GetPrimAtPath(anim_path_str)
            if not anim_prim or not anim_prim.IsValid() or not anim_prim.IsA(UsdSkel.Animation):
                for p in stage.Traverse():
                    if p.IsA(UsdSkel.Animation) and str(p.GetPath()).endswith(anim_path_str):
                        anim_prim = p
                        break
            if not anim_prim or not anim_prim.IsValid() or not anim_prim.IsA(UsdSkel.Animation):
                continue
            # Ensure the animation prim is active in case an earlier rule or source deactivated it
            if not anim_prim.IsActive():
                anim_prim.SetActive(True)
            mode = str(rule.get('mode') or 'pose')
            skel_binding = UsdSkel.BindingAPI.Apply(target_skel)
            if mode == 'clip':
                skel_binding.CreateAnimationSourceRel().SetTargets([anim_prim.GetPath()])
            else:
                pose_time = float(rule.get('poseTime', 0.0))
                src_anim = UsdSkel.Animation(anim_prim)
                pose_anim_path = f'/Scene/_OrchFlowPoseAnim_{idx}'
                pose_anim = UsdSkel.Animation.Define(stage, pose_anim_path)
                src_joints = src_anim.GetJointsAttr().Get()
                if src_joints:
                    pose_anim.CreateJointsAttr().Set(src_joints)
                anim_query = pose_cache.GetAnimQuery(src_anim)
                if anim_query:
                    local_xforms = anim_query.ComputeJointLocalTransforms(pose_time)
                    if local_xforms:
                        pose_anim.SetTransforms(local_xforms, Usd.TimeCode.Default())
                skel_binding.CreateAnimationSourceRel().SetTargets([pose_anim.GetPath()])

    # 1. Skeleton bindings (assign wildcard entries to skeletons)
    for rule in cfg.get('skeletonBindings') or []:
        if not rule.get('enabled', True):
            continue
        query = str(rule.get('targetQuery') or '').strip()
        if not query:
            continue
        target_skel = _resolve_skeleton(rule.get('skeletonPath'))
        if not target_skel:
            continue
        deactivate_src = bool(rule.get('deactivateSourceSkeletons', True))
        target_binding = UsdSkel.BindingAPI(target_skel)
        target_anims = set(
            str(t) for t in (target_binding.GetAnimationSourceRel().GetTargets() if target_binding else [])
        )
        matched = [
            p for p in stage.Traverse()
            if str(p.GetPath()) != '/Scene'
            and not p.IsA(UsdSkel.Skeleton)
            and not p.IsA(UsdSkel.Animation)
            and _hair_query_matches(query, str(p.GetPath()))
        ]
        for m_prim in _filter_outermost_prims(matched):
            for sub in Usd.PrimRange(m_prim):
                if deactivate_src and (sub.IsA(UsdSkel.Skeleton) or sub.IsA(UsdSkel.Animation)):
                    if sub.GetPath() != target_skel.GetPath() and str(sub.GetPath()) not in target_anims:
                        sub.SetActive(False)
                elif (
                    sub.IsA(UsdGeom.Mesh)
                    or sub.HasAPI(UsdSkel.BindingAPI)
                    or sub.GetAttribute('primvars:skel:jointIndices').HasAuthoredValue()
                ):
                    binding = UsdSkel.BindingAPI.Apply(sub)
                    binding.CreateSkeletonRel().SetTargets([target_skel.GetPath()])

    # 2. Joint attachments (assign wildcard prims/Xforms to follow joints via UsdSkel.Cache)
    joint_rules = [
        r for r in (cfg.get('jointAttachments') or [])
        if r.get('enabled', True) and str(r.get('targetQuery') or '').strip() and str(r.get('jointName') or '').strip()
    ]
    if joint_rules:
        skel_cache = UsdSkel.Cache()
        skel_cache.Populate(UsdSkel.Root(root), Usd.TraverseInstanceProxies())
        xform_cache = UsdGeom.XformCache()
        start_tc = stage.GetStartTimeCode()
        end_tc = stage.GetEndTimeCode()

        for rule in joint_rules:
            target_skel = _resolve_skeleton(rule.get('skeletonPath'))
            if not target_skel:
                continue
            skel_query = skel_cache.GetSkelQuery(UsdSkel.Skeleton(target_skel))
            if not skel_query:
                continue
            joint_idx = _find_joint_index(skel_query.GetJointOrder(), rule.get('jointName'))
            if joint_idx is None:
                continue

            query = str(rule.get('targetQuery') or '').strip()
            matched = [
                p for p in stage.Traverse()
                if str(p.GetPath()) != '/Scene'
                and p.IsA(UsdGeom.Xformable)
                and not p.IsA(UsdSkel.Skeleton)
                and not p.IsA(UsdSkel.Animation)
                and _hair_query_matches(query, str(p.GetPath()))
            ]
            outermost = _filter_outermost_prims(matched)
            if not outermost:
                continue

            anim_query = skel_query.GetAnimQuery()
            anim_samples = anim_query.GetJointTransformTimeSamples() if anim_query else []
            if len(anim_samples) > 1:
                if end_tc > start_tc and (end_tc - start_tc) <= 1000:
                    frames = [float(f) for f in range(int(round(start_tc)), int(round(end_tc)) + 1)]
                    timecodes = sorted(set(float(t) for t in anim_samples) | set(frames))
                else:
                    timecodes = sorted(float(t) for t in anim_samples)
            elif end_tc > start_tc and (end_tc - start_tc) <= 1000 and anim_query and anim_query.JointTransformsMightBeTimeVarying():
                timecodes = [float(f) for f in range(int(round(start_tc)), int(round(end_tc)) + 1)]
            else:
                timecodes = [Usd.TimeCode.Default()]

            offset_mat = _build_offset_matrix(
                rule.get('offsetTranslation'),
                rule.get('offsetRotation'),
                rule.get('offsetScale'),
            )

            for prim in outermost:
                if prim.HasAPI(UsdSkel.BindingAPI):
                    rel = prim.GetRelationship('skel:skeleton')
                    if rel and rel.HasAuthoredTargets():
                        rel.ClearTargets(True)
                xf = UsdGeom.Xformable(prim)
                xf.ClearXformOpOrder()
                mat_op = xf.AddTransformOp()
                for tc in timecodes:
                    xform_cache.SetTime(tc)
                    joint_skel_xforms = skel_query.ComputeJointSkelTransforms(tc)
                    if not joint_skel_xforms or joint_idx >= len(joint_skel_xforms):
                        continue
                    skel_world = xform_cache.GetLocalToWorldTransform(target_skel)
                    joint_world = joint_skel_xforms[joint_idx] * skel_world
                    parent = prim.GetParent()
                    parent_world = (
                        xform_cache.GetLocalToWorldTransform(parent)
                        if parent and parent.GetPath() != Sdf.Path.absoluteRootPath
                        else Gf.Matrix4d(1.0)
                    )
                    local_mat = offset_mat * joint_world * parent_world.GetInverse()
                    mat_op.Set(local_mat, tc)

    # 3. Animated Cloth Caches (attached to character root with scale/up/front overrides)
    cloth_caches = [
        r for r in (cfg.get('clothCaches') or [])
        if r.get('enabled', True) and str(r.get('dependencyId') or '').strip()
    ]
    if cloth_caches and files:
        base_skel = _resolve_skeleton('')
        char_root = base_skel.GetParent() if base_skel and base_skel.GetParent() else root
        if not char_root or char_root.GetPath() == Sdf.Path.absoluteRootPath:
            char_root = root
        for idx, cc in enumerate(cloth_caches):
            dep_id = str(cc.get('dependencyId') or '').strip()
            cache_path = files.get(dep_id)
            if not cache_path:
                continue
            if not os.path.isfile(cache_path):
                raise ValueError(f'Cloth cache file does not exist: {cache_path}')
            src_stage = _open_source_stage(cache_path, reload=reload)
            keepalive.append(src_stage)
            axis = str(cc.get('axis') or 'preserve')
            front_axis = str(cc.get('frontAxis') or 'preserve')
            scale = float(cc.get('scale', 1.0))
            time_offset = float(cc.get('timeOffset', 0.0))
            if wrap_fn is not None:
                norm_path = os.path.normcase(os.path.abspath(cache_path))
                mtime = os.path.getmtime(cache_path)
                wrapped = wrap_fn(
                    src_stage,
                    instanced=False,
                    norm_path=norm_path,
                    mtime=mtime,
                    axis=axis,
                    front_axis=front_axis,
                    node_scale=scale,
                )
            else:
                wrapped = src_stage
            keepalive.append(wrapped)

            attach_parent = char_root if cc.get('attachToRoot', True) else root
            cache_prim_path = attach_parent.GetPath().AppendChild(f'ClothCache_{idx}')
            cache_prim = stage.OverridePrim(cache_prim_path)
            default_prim = wrapped.GetDefaultPrim()
            target_prim_path = default_prim.GetPath() if default_prim and default_prim.IsValid() else wrapped.GetPseudoRoot().GetChildren()[0].GetPath()
            if abs(time_offset) > 1e-9:
                cache_prim.GetReferences().AddReference(
                    wrapped.GetRootLayer().identifier,
                    target_prim_path,
                    Sdf.LayerOffset(offset=time_offset),
                )
            else:
                cache_prim.GetReferences().AddReference(
                    wrapped.GetRootLayer().identifier,
                    target_prim_path,
                )
            mark_reference_instanceable(cache_prim, wrapped, enable=False)

            c_start = float(wrapped.GetStartTimeCode()) + time_offset
            c_end = float(wrapped.GetEndTimeCode()) + time_offset
            if c_end > c_start:
                if stage.GetEndTimeCode() <= stage.GetStartTimeCode():
                    stage.SetStartTimeCode(c_start)
                    stage.SetEndTimeCode(c_end)
                else:
                    stage.SetStartTimeCode(min(stage.GetStartTimeCode(), c_start))
                    stage.SetEndTimeCode(max(stage.GetEndTimeCode(), c_end))

    return stage, keepalive


def export_character_body(request):
    import os
    import shutil
    import tempfile
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdSkel, Vt

    output_path = str(request.get('outputPath') or '').strip()
    if not output_path:
        raise ValueError('Select a destination .usd/.usda/.usdc path for animated body export')
    ext = os.path.splitext(output_path)[1].lower()
    if ext not in ('.usd', '.usda', '.usdc'):
        raise ValueError('Export path must end in .usd, .usda, or .usdc')

    graph = dict(request['graph'])
    target_id = graph.get('targetNodeId')
    # Strip clothCaches from the target Character node so we export only the body/props,
    # not previously simulated cloth caches.
    graph['nodes'] = [
        {
            **n,
            'character': {**(n.get('character') or {}), 'clothCaches': []},
        }
        if n.get('id') == target_id and n.get('operation') == 'character'
        else n
        for n in graph.get('nodes', [])
    ]

    stage, alive, _ = compile_graph(graph, reload=bool(request.get('reload', False)))
    start_tc = float(stage.GetStartTimeCode())
    end_tc = float(stage.GetEndTimeCode())
    if end_tc > start_tc and (end_tc - start_tc) <= 2000:
        timecodes = [float(f) for f in range(int(round(start_tc)), int(round(end_tc)) + 1)]
    else:
        timecodes = [Usd.TimeCode.Default()]

    skel_cache = UsdSkel.Cache()
    skin_bindings = {}
    root = stage.GetDefaultPrim()
    if root and root.IsValid() and root.IsA(UsdSkel.Root):
        skel_cache.Populate(UsdSkel.Root(root), Usd.TraverseInstanceProxies())
    else:
        for p in stage.Traverse(Usd.TraverseInstanceProxies()):
            if p.IsA(UsdSkel.Root):
                skel_cache.Populate(UsdSkel.Root(p), Usd.TraverseInstanceProxies())

    for p in stage.Traverse(Usd.TraverseInstanceProxies()):
        if p.HasAPI(UsdSkel.BindingAPI):
            sq_skin = skel_cache.GetSkinningQuery(p)
            if sq_skin:
                rel = UsdSkel.BindingAPI(p).GetSkeletonRel()
                targets = rel.GetTargets() if rel else []
                skel_prim = stage.GetPrimAtPath(targets[0]) if targets else None
                if skel_prim and skel_prim.IsValid() and skel_prim.IsA(UsdSkel.Skeleton):
                    sq = skel_cache.GetSkelQuery(UsdSkel.Skeleton(skel_prim))
                    if sq:
                        skin_bindings[str(p.GetPath())] = (sq_skin, sq)

    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix='orchflow_body_', suffix=ext, dir=out_dir)
    os.close(fd)
    try:
        os.remove(tmp_path)
    except OSError:
        pass

    try:
        out_stage = Usd.Stage.CreateNew(tmp_path)
        out_root = UsdGeom.Xform.Define(out_stage, '/Body')
        out_stage.SetDefaultPrim(out_root.GetPrim())
        UsdGeom.SetStageUpAxis(out_stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(out_stage, 1.0)
        if len(timecodes) > 1:
            out_stage.SetStartTimeCode(timecodes[0])
            out_stage.SetEndTimeCode(timecodes[-1])

        xform_cache = UsdGeom.XformCache()
        mesh_count = 0
        for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
            if not prim.IsActive() or not prim.IsA(UsdGeom.Mesh):
                continue
            src_mesh = UsdGeom.Mesh(prim)
            counts = src_mesh.GetFaceVertexCountsAttr().Get()
            indices = src_mesh.GetFaceVertexIndicesAttr().Get()
            if not counts or not indices:
                continue

            mesh_name = f'{prim.GetName()}_{mesh_count}' if out_stage.GetPrimAtPath(f'/Body/{prim.GetName()}') else prim.GetName()
            dst_mesh = UsdGeom.Mesh.Define(out_stage, f'/Body/{mesh_name}')
            dst_mesh.CreateFaceVertexCountsAttr().Set(counts)
            dst_mesh.CreateFaceVertexIndicesAttr().Set(indices)
            orient = src_mesh.GetOrientationAttr().Get()
            if orient:
                dst_mesh.CreateOrientationAttr().Set(orient)
            subdiv = src_mesh.GetSubdivisionSchemeAttr().Get()
            if subdiv:
                dst_mesh.CreateSubdivisionSchemeAttr().Set(subdiv)

            pts_attr = dst_mesh.CreatePointsAttr()
            skin_entry = skin_bindings.get(str(prim.GetPath()))
            for tc in timecodes:
                xform_cache.SetTime(tc)
                pts = src_mesh.GetPointsAttr().Get(tc)
                if not pts:
                    pts = src_mesh.GetPointsAttr().Get()
                if not pts:
                    continue
                if skin_entry is not None:
                    sq_skin, sq = skin_entry
                    xforms = sq.ComputeSkinningTransforms(tc)
                    if xforms:
                        mapper = sq_skin.GetJointMapper()
                        if mapper:
                            xforms = mapper.RemapTransforms(xforms)
                        pts = Vt.Vec3fArray(pts)
                        sq_skin.ComputeSkinnedPoints(xforms, pts, tc)
                        world_mat = xform_cache.GetLocalToWorldTransform(sq.GetPrim())
                    else:
                        world_mat = xform_cache.GetLocalToWorldTransform(prim)
                else:
                    world_mat = xform_cache.GetLocalToWorldTransform(prim)
                baked = Vt.Vec3fArray([Gf.Vec3f(world_mat.Transform(Gf.Vec3d(p))) for p in pts])
                pts_attr.Set(baked, tc)
            mesh_count += 1

        out_stage.GetRootLayer().Save()
        del out_stage
        shutil.move(tmp_path, output_path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise

    return {
        'path': output_path,
        'meshCount': mesh_count,
        'frameCount': len(timecodes),
        'startTimeCode': timecodes[0] if len(timecodes) > 1 else 0.0,
        'endTimeCode': timecodes[-1] if len(timecodes) > 1 else 0.0,
    }


def inspect_stage_curves(request):
    from pxr import Usd, UsdGeom

    graph = request['graph']
    stage, alive, _ = compile_graph(graph, reload=bool(request.get('reload', False)))
    curves = []
    meshes = []
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if prim.IsA(UsdGeom.BasisCurves):
            bc = UsdGeom.BasisCurves(prim)
            counts = bc.GetCurveVertexCountsAttr().Get()
            curves.append({
                'path': str(prim.GetPath()),
                'name': prim.GetName(),
                'curveCount': int(len(counts)) if counts else 0,
                'instanceProxy': bool(prim.IsInstanceProxy()),
            })
        elif prim.IsA(UsdGeom.Mesh):
            mesh = UsdGeom.Mesh(prim)
            pts_attr = mesh.GetPointsAttr()
            samples = pts_attr.GetTimeSamples() if pts_attr else []
            pts = pts_attr.Get(samples[0] if samples else Usd.TimeCode.Default()) if pts_attr else None
            meshes.append({
                'path': str(prim.GetPath()),
                'name': prim.GetName(),
                'vertexCount': int(len(pts)) if pts else 0,
                'timeSampleCount': int(len(samples)),
                'instanceProxy': bool(prim.IsInstanceProxy()),
            })
    return {
        'curves': curves,
        'meshes': meshes,
        'startTimeCode': float(stage.GetStartTimeCode()),
        'endTimeCode': float(stage.GetEndTimeCode()),
    }


def apply_cache_edit(stage, cache_cfg):
    if not isinstance(cache_cfg, dict):
        return
    import os as _os, sys as _sys
    _here = globals().get('__file__')
    for _cand in (
        _os.environ.get('ORCHFLOW_WORKER_DIR'),
        _os.path.dirname(_os.path.abspath(_here)) if _here else None,
        _os.path.join(_os.getcwd(), 'worker'),
        r'e:\CODINGPROJECTS\Rust\Sublayer\worker',
    ):
        if _cand and _os.path.isdir(_cand) and _cand not in _sys.path:
            _sys.path.insert(0, _cand)
            break
    from cache_edit import apply_cache_edit as _apply_cache_edit
    _apply_cache_edit(stage, cache_cfg)


def apply_hair(stage, hair_cfg):
    import math
    from pxr import UsdGeom, Vt

    cfg = hair_cfg if isinstance(hair_cfg, dict) else {}
    width_mode = str(cfg.get('widthMode') or 'multiply')
    if width_mode not in ('multiply', 'set'):
        raise ValueError('Hair width mode must be multiply or set')
    width = float(cfg.get('width', 1.0))
    taper = float(cfg.get('taper', 0.0))
    root_scale = float(cfg.get('rootScale', 1.0))
    if not math.isfinite(width) or width <= 0.0:
        raise ValueError('Hair width must be a finite positive number')
    if not math.isfinite(taper) or taper < 0.0 or taper > 1.0:
        raise ValueError('Hair taper must be between 0 and 1')
    if not math.isfinite(root_scale) or root_scale <= 0.0 or root_scale > 10.0:
        raise ValueError('Hair root scale must be between 0 and 10')

    sim_enabled = bool(cfg.get('simEnabled', False))
    width_is_identity = (
        width_mode == 'multiply'
        and abs(width - 1.0) < 1e-9
        and abs(taper) < 1e-9
        and abs(root_scale - 1.0) < 1e-9
    )
    if width_is_identity and not sim_enabled:
        return

    raw_query = str(cfg.get('targetQuery') or '').strip()

    if not width_is_identity:
        _apply_hair_widths(stage, raw_query, width_mode, width, taper, root_scale)

    if sim_enabled:
        import os
        import sys
        _here = globals().get('__file__')
        for _cand in (
            os.environ.get('ORCHFLOW_WORKER_DIR'),
            os.path.dirname(os.path.abspath(_here)) if _here else None,
            os.path.join(os.getcwd(), 'worker'),
            r'e:\CODINGPROJECTS\Rust\Sublayer\worker',
        ):
            if _cand and os.path.isdir(_cand) and _cand not in sys.path:
                sys.path.insert(0, _cand)
                break
        from hair_sim import apply_hair_simulation
        apply_hair_simulation(stage, cfg, _hair_query_matches)


def _apply_hair_widths(stage, raw_query, width_mode, width, taper, root_scale):
    from pxr import UsdGeom, Vt

    has_profile = abs(taper) >= 1e-9 or abs(root_scale - 1.0) >= 1e-9
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.BasisCurves) or prim.IsInstanceProxy():
            continue
        if not _hair_query_matches(raw_query, str(prim.GetPath())):
            continue
        curves = UsdGeom.BasisCurves(prim)
        widths_attr = curves.GetWidthsAttr()
        orig_widths = widths_attr.Get() if widths_attr else None
        interp = curves.GetWidthsInterpolation()

        if not has_profile:
            if width_mode == 'set':
                curves.CreateWidthsAttr().Set(Vt.FloatArray([float(width)]))
                curves.SetWidthsInterpolation(UsdGeom.Tokens.constant)
            else:
                if orig_widths is not None and len(orig_widths) > 0:
                    curves.CreateWidthsAttr().Set(Vt.FloatArray([float(w) * width for w in orig_widths]))
                else:
                    curves.CreateWidthsAttr().Set(Vt.FloatArray([float(width)]))
                    curves.SetWidthsInterpolation(UsdGeom.Tokens.constant)
            continue

        counts = curves.GetCurveVertexCountsAttr().Get()
        if not counts or len(counts) == 0:
            if width_mode == 'set':
                curves.CreateWidthsAttr().Set(Vt.FloatArray([float(width * root_scale)]))
                curves.SetWidthsInterpolation(UsdGeom.Tokens.constant)
            elif orig_widths is not None and len(orig_widths) > 0:
                curves.CreateWidthsAttr().Set(Vt.FloatArray([float(w) * width * root_scale for w in orig_widths]))
            continue

        import numpy as np

        counts_np = np.asarray(counts, dtype=np.int32)
        n_curves = len(counts_np)
        total_verts = int(counts_np.sum())
        if total_verts == 0:
            continue
        orig_np = np.asarray(orig_widths, dtype=np.float32) if orig_widths is not None else np.empty(0, dtype=np.float32)
        n_orig = len(orig_np)

        starts_np = np.zeros(n_curves, dtype=np.int32)
        if n_curves > 1:
            starts_np[1:] = np.cumsum(counts_np[:-1], dtype=np.int32)
        rep_counts = np.repeat(counts_np, counts_np)
        rep_starts = np.repeat(starts_np, counts_np)
        local_idx = np.arange(total_verts, dtype=np.int32) - rep_starts
        denom = np.maximum(rep_counts - 1, 1).astype(np.float32)
        t = np.where(rep_counts > 1, local_idx.astype(np.float32) / denom, np.float32(0.0))
        t_curve = np.power(t, np.float32(1.35))
        factor = np.float32(root_scale) * (np.float32(1.0) - t_curve) + np.float32(1.0 - taper) * t_curve

        if width_mode == 'set':
            base_w = np.full(total_verts, width, dtype=np.float32)
        else:
            if n_orig == 0:
                base_w = np.ones(total_verts, dtype=np.float32)
            elif interp == UsdGeom.Tokens.constant or n_orig == 1:
                base_w = np.full(total_verts, float(orig_np[0]), dtype=np.float32)
            elif interp == UsdGeom.Tokens.uniform and n_orig >= n_curves:
                base_w = np.repeat(orig_np[:n_curves], counts_np)
            elif (interp == UsdGeom.Tokens.vertex or (interp == UsdGeom.Tokens.varying and n_orig == total_verts)) and n_orig >= total_verts:
                base_w = orig_np[:total_verts]
            elif interp == UsdGeom.Tokens.varying:
                c_var_count = max(1, int(round(n_orig / float(max(n_curves, 1)))))
                curve_ids = np.repeat(np.arange(n_curves, dtype=np.int32), counts_np)
                local_var = np.minimum(c_var_count - 1, np.rint(t * max(0, c_var_count - 1)).astype(np.int32))
                src_idx = np.minimum(n_orig - 1, curve_ids * c_var_count + local_var)
                base_w = orig_np[src_idx]
            else:
                src_idx = np.minimum(np.arange(total_verts, dtype=np.int32), n_orig - 1)
                base_w = orig_np[src_idx]
            base_w = base_w * np.float32(width)

        new_widths_np = np.maximum(np.float32(0.0), base_w * factor).astype(np.float32)
        curves.CreateWidthsAttr().Set(Vt.FloatArray.FromNumpy(new_widths_np))
        curves.SetWidthsInterpolation(UsdGeom.Tokens.vertex)


def _read_vdb_grid_names(path):
    """Extract grid names from an OpenVDB file header without requiring pyopenvdb."""
    import struct
    names = []
    try:
        with open(path, 'rb') as f:
            magic = f.read(8)
            if magic != b' VDB\x00\x00\x00\x00':
                return ['density']
            file_ver, lib_major, lib_minor, has_grid_offsets = struct.unpack('<III?', f.read(13))
            f.read(16)  # 16-byte UUID
            if file_ver >= 224:
                pass
            # Read file-level metadata count
            meta_count = struct.unpack('<I', f.read(4))[0]
            for _ in range(min(meta_count, 256)):
                nlen = struct.unpack('<I', f.read(4))[0]
                f.read(nlen)
                tlen = struct.unpack('<I', f.read(4))[0]
                f.read(tlen)
                vlen = struct.unpack('<I', f.read(4))[0]
                f.read(vlen)
            grid_count = struct.unpack('<I', f.read(4))[0]
            for _ in range(min(grid_count, 64)):
                glen = struct.unpack('<I', f.read(4))[0]
                if glen == 0 or glen > 1024:
                    break
                gname = f.read(glen).decode('utf-8', errors='replace')
                names.append(gname)
                tlen = struct.unpack('<I', f.read(4))[0]
                f.read(tlen)  # grid type
                if file_ver >= 222:
                    ilen = struct.unpack('<I', f.read(4))[0]
                    f.read(ilen)  # instance parent
                # Grid stream offsets: gridPos (8), blockPos (8), endPos (8)
                grid_pos, block_pos, end_pos = struct.unpack('<qqq', f.read(24))
                if end_pos > f.tell():
                    f.seek(end_pos)
                else:
                    break
    except Exception:
        pass
    return names or ['density']


def _open_vdb_source_stage(path):
    from pxr import Sdf, Tf, Usd, UsdGeom, UsdVol

    abs_path = os.path.abspath(path)
    if not os.path.isfile(abs_path):
        raise ValueError(f'VDB file does not exist: {path}')
    stage = Usd.Stage.CreateInMemory('source-vdb.usda')
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    vol = UsdVol.Volume.Define(stage, '/Volume')
    stage.SetDefaultPrim(vol.GetPrim())

    grid_names = _read_vdb_grid_names(abs_path)
    used = set()
    for gname in grid_names:
        ident = Tf.MakeValidIdentifier(gname or 'density')
        if ident in used:
            ident += f'_{len(used)}'
        used.add(ident)
        field = UsdVol.OpenVDBAsset.Define(stage, f'/Volume/{ident}')
        field.CreateFieldNameAttr().Set(gname or 'density')
        field.CreateFilePathAttr().Set(Sdf.AssetPath(abs_path))
        rel_name = Tf.MakeValidIdentifier(gname or 'density')
        vol.CreateFieldRelationship(rel_name, field.GetPath())
    return stage


def _parse_mtl_file(mtl_path):
    materials = {}
    if not os.path.isfile(mtl_path):
        return materials
    base_dir = os.path.dirname(mtl_path)
    current = None
    with open(mtl_path, encoding='utf-8-sig', errors='replace') as stream:
        for raw_line in stream:
            line = raw_line.split('#', 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            tok = parts[0]
            vals = parts[1:]
            if tok == 'newmtl' and vals:
                name = ' '.join(vals)
                current = materials.setdefault(name, {})
            elif current is None:
                continue
            elif tok in ('Kd', 'Ks', 'Ke') and len(vals) >= 3:
                try:
                    current[tok] = tuple(float(x) for x in vals[:3])
                except ValueError:
                    pass
            elif tok in ('d', 'Ns', 'Ni', 'Pr', 'Pm') and vals:
                try:
                    current[tok] = float(vals[0])
                except ValueError:
                    pass
            elif tok == 'Tr' and vals:
                try:
                    current['d'] = max(0.0, min(1.0, 1.0 - float(vals[0])))
                except ValueError:
                    pass
            elif tok in ('map_Kd', 'map_Ks', 'map_Ke', 'map_d', 'map_Bump', 'bump', 'map_Pr', 'map_Roughness', 'map_Pm', 'map_Metallic') and vals:
                # Handle optional MTL texture flags like -bm 1.0 before the filename
                tex_rel = vals[-1]
                tex_abs = os.path.abspath(os.path.join(base_dir, tex_rel))
                if os.path.isfile(tex_abs):
                    key = 'map_Bump' if tok == 'bump' else ('map_Pr' if tok == 'map_Roughness' else ('map_Pm' if tok == 'map_Metallic' else tok))
                    current[key] = tex_abs
    return materials


def _open_obj_source_stage(path):
    import hashlib
    import math
    from pxr import Gf, Sdf, Tf, Usd, UsdGeom, UsdShade, Vt

    abs_path = os.path.abspath(path)
    if not os.path.isfile(abs_path):
        raise ValueError(f'OBJ file does not exist: {path}')
    base_dir = os.path.dirname(abs_path)

    points = []
    vcolors = []
    has_vcolors = False
    uvs = []
    normals = []
    objects = {}
    obj_name = 'Mesh'
    cur_mtl = None
    declared = {}
    mtl_defs = {}

    def idx(value, size):
        n = int(value)
        r = n - 1 if n > 0 else size + n
        if n == 0 or r < 0 or r >= size:
            raise ValueError('OBJ index outside declared array')
        return r

    def nums(tokens, count):
        res = [float(v) for v in tokens]
        if len(res) < count or not all(math.isfinite(v) for v in res):
            raise ValueError('Invalid OBJ coordinates')
        return res

    with open(abs_path, encoding='utf-8-sig', errors='replace') as stream:
        pending = ''
        for line_no, raw_line in enumerate(stream, 1):
            line = raw_line.split('#', 1)[0].strip()
            if line.endswith('\\'):
                pending += line[:-1] + ' '
                continue
            line = pending + line
            pending = ''
            if not line:
                continue
            token, *values = line.split()
            try:
                if token == 'v':
                    v = nums(values, 3)
                    if len(v) == 4 and v[3] != 0:
                        v = [c / v[3] for c in v[:3]]
                    declared.setdefault(obj_name, []).append(len(points))
                    points.append((v[0], v[1], v[2]))
                    if len(v) >= 6:
                        has_vcolors = True
                        vcolors.append((v[3], v[4], v[5]))
                    else:
                        vcolors.append((1.0, 1.0, 1.0))
                elif token == 'vt':
                    v = nums(values, 1)
                    uvs.append((v[0], v[1] if len(v) > 1 else 0.0))
                elif token == 'vn':
                    v = nums(values, 3)
                    normals.append((v[0], v[1], v[2]))
                elif token in ('o', 'g') and values:
                    if token == 'o' or not objects:
                        obj_name = ' '.join(values) or 'Mesh'
                elif token == 'mtllib' and values:
                    mtl_path = os.path.abspath(os.path.join(base_dir, ' '.join(values)))
                    mtl_defs.update(_parse_mtl_file(mtl_path))
                elif token == 'usemtl' and values:
                    cur_mtl = ' '.join(values)
                elif token == 'f':
                    if len(values) < 3:
                        continue
                    face = []
                    for corner in values:
                        fields = corner.split('/')
                        if not fields[0]:
                            raise ValueError('Invalid OBJ face corner')
                        face.append((
                            idx(fields[0], len(points)),
                            idx(fields[1], len(uvs)) if len(fields) > 1 and fields[1] else None,
                            idx(fields[2], len(normals)) if len(fields) > 2 and fields[2] else None,
                        ))
                    objects.setdefault(obj_name, []).append((face, cur_mtl))
            except (ValueError, OverflowError) as error:
                raise ValueError(f'OBJ line {line_no}: {error}') from error

    if not objects:
        raise ValueError('OBJ contains no polygon meshes')

    stage = Usd.Stage.CreateInMemory('source-obj.usda')
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, '/OBJ')
    stage.SetDefaultPrim(root.GetPrim())

    # Author UsdPreviewSurface materials from MTL definitions
    usd_materials = {}
    if mtl_defs:
        UsdGeom.Scope.Define(stage, '/OBJ/_Materials')
        used_mat_names = set()
        for mname, mdata in mtl_defs.items():
            mid = Tf.MakeValidIdentifier(mname)
            if mid in used_mat_names:
                mid += '_' + hashlib.sha256(mname.encode()).hexdigest()[:8]
            used_mat_names.add(mid)
            mat_path = f'/OBJ/_Materials/{mid}'
            mat = UsdShade.Material.Define(stage, mat_path)
            sh = UsdShade.Shader.Define(stage, f'{mat_path}/PreviewSurface')
            sh.CreateIdAttr('UsdPreviewSurface')
            sh.CreateOutput('surface', Sdf.ValueTypeNames.Token)
            mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), 'surface')

            kd = mdata.get('Kd', (0.8, 0.8, 0.8))
            sh.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*kd))
            if 'Pr' in mdata:
                rough = max(0.02, min(1.0, mdata['Pr']))
            elif 'Ns' in mdata:
                rough = max(0.04, min(1.0, math.sqrt(2.0 / (max(1.0, mdata['Ns']) + 2.0))))
            else:
                rough = 0.5
            sh.CreateInput('roughness', Sdf.ValueTypeNames.Float).Set(float(rough))
            if 'Pm' in mdata:
                sh.CreateInput('metallic', Sdf.ValueTypeNames.Float).Set(max(0.0, min(1.0, mdata['Pm'])))
            if 'd' in mdata:
                sh.CreateInput('opacity', Sdf.ValueTypeNames.Float).Set(max(0.0, min(1.0, mdata['d'])))
            if 'Ni' in mdata:
                sh.CreateInput('ior', Sdf.ValueTypeNames.Float).Set(max(1.0, mdata['Ni']))
            if 'Ke' in mdata:
                sh.CreateInput('emissiveColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*mdata['Ke']))

            def _bind_tex(slot_key, input_name, value_type, out_name, colorspace='sRGB'):
                tex_fp = mdata.get(slot_key)
                if not tex_fp:
                    return
                st_reader = stage.GetPrimAtPath(f'{mat_path}/PrimvarSt')
                if not st_reader:
                    reader = UsdShade.Shader.Define(stage, f'{mat_path}/PrimvarSt')
                    reader.CreateIdAttr('UsdPrimvarReader_float2')
                    reader.CreateInput('varname', Sdf.ValueTypeNames.Token).Set('st')
                    reader.CreateOutput('result', Sdf.ValueTypeNames.Float2)
                else:
                    reader = UsdShade.Shader(st_reader)
                tex = UsdShade.Shader.Define(stage, f'{mat_path}/Tex_{input_name}')
                tex.CreateIdAttr('UsdUVTexture')
                tex.CreateInput('file', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(tex_fp))
                tex.CreateInput('sourceColorSpace', Sdf.ValueTypeNames.Token).Set(colorspace)
                tex.CreateInput('st', Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(), 'result')
                tex.CreateOutput(out_name, value_type)
                sh.CreateInput(input_name, value_type).ConnectToSource(tex.ConnectableAPI(), out_name)

            _bind_tex('map_Kd', 'diffuseColor', Sdf.ValueTypeNames.Color3f, 'rgb', 'sRGB')
            _bind_tex('map_Pr', 'roughness', Sdf.ValueTypeNames.Float, 'r', 'raw')
            _bind_tex('map_Pm', 'metallic', Sdf.ValueTypeNames.Float, 'r', 'raw')
            _bind_tex('map_d', 'opacity', Sdf.ValueTypeNames.Float, 'r', 'raw')
            _bind_tex('map_Ke', 'emissiveColor', Sdf.ValueTypeNames.Color3f, 'rgb', 'sRGB')
            _bind_tex('map_Bump', 'normal', Sdf.ValueTypeNames.Normal3f, 'rgb', 'raw')
            usd_materials[mname] = mat

    used_names = set()
    for raw, face_entries in objects.items():
        ident = Tf.MakeValidIdentifier(raw)
        if ident in used_names:
            ident += '_' + hashlib.sha256(raw.encode()).hexdigest()[:12]
        used_names.add(ident)
        faces = [f for f, _ in face_entries]
        verts = sorted({c[0] for f in faces for c in f} | set(declared.get(raw, []))) if len(objects) > 1 else list(range(len(points)))
        remap = {v: i for i, v in enumerate(verts)}
        corners = [c for f in faces for c in f]
        mesh = UsdGeom.Mesh.Define(stage, f'/OBJ/{ident}')
        mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*points[v]) for v in verts]))
        mesh.CreateFaceVertexCountsAttr([len(f) for f in faces])
        mesh.CreateFaceVertexIndicesAttr([remap[c[0]] for c in corners])
        mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        mesh.CreateDoubleSidedAttr(True)
        if has_vcolors:
            dc = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('displayColor', Sdf.ValueTypeNames.Color3fArray, UsdGeom.Tokens.vertex)
            dc.Set(Vt.Vec3fArray([Gf.Vec3f(*vcolors[v]) for v in verts]))
        if all(c[1] is not None for c in corners) and corners:
            uv = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st', Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)
            uv.Set(Vt.Vec2fArray([Gf.Vec2f(*uvs[c[1]]) for c in corners]))
        if all(c[2] is not None for c in corners) and corners:
            mesh.CreateNormalsAttr(Vt.Vec3fArray([Gf.Vec3f(*normals[c[2]]) for c in corners]))
            mesh.SetNormalsInterpolation(UsdGeom.Tokens.faceVarying)

        mtls_used = {m for _, m in face_entries if m in usd_materials}
        if len(mtls_used) == 1 and all(m in usd_materials for _, m in face_entries):
            only_mtl = next(iter(mtls_used))
            UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(usd_materials[only_mtl])
        elif mtls_used:
            by_mtl = {}
            for fi, (_, m) in enumerate(face_entries):
                if m in usd_materials:
                    by_mtl.setdefault(m, []).append(fi)
            UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim())
            for mname, face_indices in by_mtl.items():
                sub = UsdGeom.Subset.Define(stage, f'/OBJ/{ident}/mat_{Tf.MakeValidIdentifier(mname)}')
                sub.CreateElementTypeAttr(UsdGeom.Tokens.face)
                sub.CreateFamilyNameAttr(UsdShade.Tokens.materialBind)
                sub.CreateIndicesAttr(Vt.IntArray(face_indices))
                UsdShade.MaterialBindingAPI.Apply(sub.GetPrim()).Bind(usd_materials[mname])
    return stage


def _open_alembic_source_stage(path, reload=False):
    import hashlib
    import subprocess
    import tempfile
    from pxr import Sdf, Usd

    st = os.stat(path)
    norm_path = os.path.normcase(os.path.abspath(path))
    path_hash = hashlib.sha256(norm_path.encode('utf-8')).hexdigest()[:12]
    key = hashlib.sha256(f'{norm_path}|{st.st_mtime_ns}|{st.st_size}'.encode('utf-8')).hexdigest()[:24]
    cache_dir = os.path.join(tempfile.gettempdir(), 'sublayer_alembic_cache')
    os.makedirs(cache_dir, exist_ok=True)
    cache_usdc = os.path.join(cache_dir, f'{path_hash}_{key}.usdc')

    if reload or not os.path.isfile(cache_usdc):
        tmp_usdc = os.path.join(cache_dir, f'{path_hash}_{key}.{os.getpid()}.tmp.usdc')
        try:
            if Sdf.FileFormat.FindByExtension('abc'):
                abc_stage = Usd.Stage.Open(path)
                if abc_stage is None:
                    raise ValueError(f'Unable to open Alembic input with usdAbc: {path}')
                if not abc_stage.GetRootLayer().Export(tmp_usdc):
                    raise ValueError(f'Unable to export Alembic cache USD: {cache_usdc}')
            else:
                repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                imaging_python = os.environ.get('ORCHFLOW_IMAGING_PYTHON') or os.path.join(
                    repo_root, '.venv-imaging', 'Scripts', 'python.exe'
                )
                if not os.path.isfile(imaging_python):
                    raise ValueError(
                        'Alembic support requires the upstream OpenUSD usdAbc plugin; it is missing from this runtime'
                    )
                usd_root = os.environ.get('ORCHFLOW_USD_ROOT') or r'I:\AI\OpenUSD-runtime\25.08'
                env = os.environ.copy()
                py_path = os.path.join(usd_root, 'lib', 'python')
                if os.path.isdir(py_path):
                    env['PYTHONPATH'] = py_path + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
                extra_paths = [p for p in (os.path.join(usd_root, 'lib'), os.path.join(usd_root, 'bin')) if os.path.isdir(p)]
                if extra_paths:
                    env['PATH'] = os.pathsep.join(extra_paths) + (os.pathsep + env['PATH'] if env.get('PATH') else '')
                script = (
                    "import os, sys\n"
                    "usd_root = sys.argv[3]\n"
                    "if hasattr(os, 'add_dll_directory'):\n"
                    "    for d in (os.path.join(usd_root, 'lib'), os.path.join(usd_root, 'bin')):\n"
                    "        if os.path.isdir(d):\n"
                    "            os.add_dll_directory(d)\n"
                    "from pxr import Sdf, Usd\n"
                    "if not Sdf.FileFormat.FindByExtension('abc'):\n"
                    "    raise RuntimeError('usdAbc plugin missing in imaging runtime')\n"
                    "s = Usd.Stage.Open(sys.argv[1])\n"
                    "if s is None:\n"
                    "    raise RuntimeError(f'Unable to open Alembic input: {sys.argv[1]}')\n"
                    "if not s.GetRootLayer().Export(sys.argv[2]):\n"
                    "    raise RuntimeError(f'Unable to export Alembic cache: {sys.argv[2]}')\n"
                )
                proc = subprocess.run(
                    [imaging_python, '-c', script, path, tmp_usdc, usd_root],
                    capture_output=True,
                    text=True,
                    env=env,
                    check=False,
                )
                if proc.returncode != 0 or not os.path.isfile(tmp_usdc):
                    detail = (proc.stderr or proc.stdout or '').strip()
                    raise ValueError(
                        f'Unable to convert Alembic input to cached USD ({path}): {detail or "usdAbc conversion failed"}'
                    )
            os.replace(tmp_usdc, cache_usdc)
            _cleanup_stale_source_cache(cache_dir, path_hash, {os.path.basename(cache_usdc)})
            _prune_disk_caches(keep_paths=(cache_usdc,))
        finally:
            if os.path.exists(tmp_usdc):
                try:
                    os.remove(tmp_usdc)
                except OSError:
                    pass
    else:
        _touch_cache_file(cache_usdc)

    source = Usd.Stage.Open(cache_usdc)
    if source is None:
        raise ValueError(f'Unable to open cached Alembic USD: {cache_usdc}')
    if reload:
        source.Reload()
    return source


def _parse_goz_file(path):
    import struct

    if not os.path.isfile(path):
        raise ValueError(f'GoZ file does not exist: {path}')

    meshes = []
    current = None

    def _ensure_mesh():
        nonlocal current
        if current is None:
            current = {
                'name': 'Mesh',
                'points': [],
                'faces': [],
                'uvs': [],
                'polypaint': [],
                'mask': [],
                'polygroups': [],
                'diffuse_map': '',
                'displacement_map': '',
                'normal_map': '',
            }

    def _flush_mesh():
        nonlocal current
        if current is not None and (current['points'] or current['faces']):
            meshes.append(current)
        current = None

    with open(path, 'rb') as stream:
        header = stream.read(32)
        if len(header) < 32 or not header.startswith(b'GoZb'):
            raise ValueError(f'Not a valid GoZ binary file: {path}')

        while True:
            hdr = stream.read(16)
            if not hdr or len(hdr) < 16:
                break
            tag, length, count = struct.unpack('<IIQ', hdr)
            if tag == 0:
                _flush_mesh()
                continue
            if length < 16:
                raise ValueError(f'Invalid GoZ block length {length} for tag {tag:#x}')
            payload_len = length - 16
            payload = stream.read(payload_len) if payload_len > 0 else b''
            if len(payload) < payload_len:
                raise ValueError(f'Unexpected end of GoZ file in block {tag:#x}')

            if tag == 1:
                if current is not None and (current['points'] or current['faces']):
                    _flush_mesh()
                _ensure_mesh()
                raw_name = payload.split(b'\x00', 1)[0].decode('utf-8', errors='replace').strip()
                if raw_name.startswith('GoZMesh_'):
                    raw_name = raw_name[len('GoZMesh_'):]
                current['name'] = raw_name or 'Mesh'
            elif tag == 10001:
                _ensure_mesh()
                expected = count * 12
                if len(payload) < expected:
                    raise ValueError('Truncated GoZ vertex block')
                current['points'] = list(struct.iter_unpack('<3f', memoryview(payload)[:expected]))
            elif tag == 20001:
                _ensure_mesh()
                expected = count * 16
                if len(payload) < expected:
                    raise ValueError('Truncated GoZ face block')
                current['faces'] = [
                    (v0, v1, v2) if v3 < 0 else (v0, v1, v2, v3)
                    for v0, v1, v2, v3 in struct.iter_unpack('<4i', memoryview(payload)[:expected])
                ]
            elif tag == 25001:
                _ensure_mesh()
                expected = count * 32
                if len(payload) >= expected:
                    current['uvs'] = list(struct.iter_unpack('<8f', memoryview(payload)[:expected]))
            elif tag == 30001:
                _ensure_mesh()
                expected = count * 4
                if len(payload) >= expected:
                    current['polypaint'] = [
                        (r / 255.0, g / 255.0, b / 255.0)
                        for b, g, r, _ in struct.iter_unpack('<4B', memoryview(payload)[:expected])
                    ]
            elif tag == 40001:
                _ensure_mesh()
                if count > 0:
                    stride = len(payload) // count
                    if stride == 2:
                        current['mask'] = [
                            v[0] / 65535.0
                            for v in struct.iter_unpack('<H', memoryview(payload)[: count * 2])
                        ]
                    elif stride == 4:
                        current['mask'] = [
                            max(0.0, min(1.0, v[0]))
                            for v in struct.iter_unpack('<f', memoryview(payload)[: count * 4])
                        ]
                    elif stride == 1:
                        current['mask'] = [
                            v[0] / 255.0
                            for v in struct.iter_unpack('<B', memoryview(payload)[:count])
                        ]
            elif tag == 50001:
                _ensure_mesh()
                if count > 0:
                    stride = len(payload) // count
                    if stride == 2:
                        current['polygroups'] = [
                            v[0]
                            for v in struct.iter_unpack('<H', memoryview(payload)[: count * 2])
                        ]
                    elif stride == 4:
                        current['polygroups'] = [
                            v[0]
                            for v in struct.iter_unpack('<I', memoryview(payload)[: count * 4])
                        ]
            elif tag == 60001:
                _ensure_mesh()
                current['diffuse_map'] = payload.split(b'\x00', 1)[0].decode('utf-8', errors='replace').strip()
            elif tag == 70001:
                _ensure_mesh()
                current['displacement_map'] = payload.split(b'\x00', 1)[0].decode('utf-8', errors='replace').strip()
            elif tag == 80001:
                _ensure_mesh()
                current['normal_map'] = payload.split(b'\x00', 1)[0].decode('utf-8', errors='replace').strip()

    _flush_mesh()
    if not meshes:
        raise ValueError('GoZ file contains no polygon meshes')
    return meshes


def _open_goz_source_stage(path):
    import hashlib
    from pxr import Gf, Sdf, Tf, Usd, UsdGeom, UsdShade, Vt

    meshes = _parse_goz_file(path)
    base_dir = os.path.dirname(os.path.abspath(path))

    stage = Usd.Stage.CreateInMemory('source-goz.usda')
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, '/GoZ')
    stage.SetDefaultPrim(root.GetPrim())

    def _resolve_tex(tex_path):
        if not tex_path:
            return ''
        if os.path.isabs(tex_path):
            return os.path.abspath(tex_path)
        return os.path.abspath(os.path.join(base_dir, tex_path))

    used_names = set()
    mat_scope_created = False

    for entry in meshes:
        raw_name = entry['name'] or 'Mesh'
        ident = Tf.MakeValidIdentifier(raw_name)
        if ident in used_names:
            ident += '_' + hashlib.sha256(f'{raw_name}:{len(used_names)}'.encode()).hexdigest()[:8]
        used_names.add(ident)

        points = entry['points']
        faces = entry['faces']
        point_count = len(points)
        for face in faces:
            for idx in face:
                if idx < 0 or idx >= point_count:
                    raise ValueError(f'GoZ face index {idx} out of range for {point_count} vertices')

        mesh = UsdGeom.Mesh.Define(stage, f'/GoZ/{ident}')
        mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*p) for p in points]))
        mesh.CreateFaceVertexCountsAttr([len(f) for f in faces])
        mesh.CreateFaceVertexIndicesAttr([idx for f in faces for idx in f])
        mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        mesh.CreateDoubleSidedAttr(True)
        if points:
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            zs = [p[2] for p in points]
            mesh.CreateExtentAttr(
                Vt.Vec3fArray([Gf.Vec3f(min(xs), min(ys), min(zs)), Gf.Vec3f(max(xs), max(ys), max(zs))])
            )

        if entry['uvs'] and len(entry['uvs']) == len(faces) and faces:
            st_vals = []
            for fi, face in enumerate(faces):
                uv8 = entry['uvs'][fi]
                for k in range(len(face)):
                    st_vals.append(Gf.Vec2f(uv8[2 * k], 1.0 - uv8[2 * k + 1]))
            uv = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
                'st', Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying
            )
            uv.Set(Vt.Vec2fArray(st_vals))

        has_polypaint = bool(entry['polypaint']) and len(entry['polypaint']) == point_count
        if has_polypaint:
            dc = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
                'displayColor', Sdf.ValueTypeNames.Color3fArray, UsdGeom.Tokens.vertex
            )
            dc.Set(Vt.Vec3fArray([Gf.Vec3f(*c) for c in entry['polypaint']]))

        if entry['mask'] and len(entry['mask']) == point_count:
            mk = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
                'zbrush_mask', Sdf.ValueTypeNames.FloatArray, UsdGeom.Tokens.vertex
            )
            mk.Set(Vt.FloatArray(entry['mask']))

        if entry['polygroups'] and len(entry['polygroups']) == len(faces):
            pg_primvar = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
                'zbrush_polygroup', Sdf.ValueTypeNames.IntArray, UsdGeom.Tokens.uniform
            )
            pg_primvar.Set(Vt.IntArray(entry['polygroups']))
            by_pg = {}
            for fi, pg_id in enumerate(entry['polygroups']):
                by_pg.setdefault(pg_id, []).append(fi)
            if len(by_pg) > 1 or (len(by_pg) == 1 and next(iter(by_pg)) != 0):
                for pg_id, face_indices in sorted(by_pg.items()):
                    sub = UsdGeom.Subset.Define(stage, f'/GoZ/{ident}/polygroup_{pg_id}')
                    sub.CreateElementTypeAttr(UsdGeom.Tokens.face)
                    sub.CreateFamilyNameAttr('polygroup')
                    sub.CreateIndicesAttr(Vt.IntArray(face_indices))

        diff_path = _resolve_tex(entry['diffuse_map'])
        norm_path = _resolve_tex(entry['normal_map'])
        disp_path = _resolve_tex(entry['displacement_map'])
        if diff_path or norm_path or disp_path:
            if not mat_scope_created:
                UsdGeom.Scope.Define(stage, '/GoZ/_Materials')
                mat_scope_created = True
            mat_path = f'/GoZ/_Materials/{ident}_Mat'
            mat = UsdShade.Material.Define(stage, mat_path)
            sh = UsdShade.Shader.Define(stage, f'{mat_path}/PreviewSurface')
            sh.CreateIdAttr('UsdPreviewSurface')
            sh.CreateInput('roughness', Sdf.ValueTypeNames.Float).Set(0.5)
            sh.CreateOutput('surface', Sdf.ValueTypeNames.Token)
            mat.CreateSurfaceOutput().ConnectToSource(sh.ConnectableAPI(), 'surface')

            reader = UsdShade.Shader.Define(stage, f'{mat_path}/PrimvarSt')
            reader.CreateIdAttr('UsdPrimvarReader_float2')
            reader.CreateInput('varname', Sdf.ValueTypeNames.Token).Set('st')
            reader.CreateOutput('result', Sdf.ValueTypeNames.Float2)

            def _bind_goz_tex(tex_fp, tex_name, input_name, value_type, out_name, colorspace):
                tex = UsdShade.Shader.Define(stage, f'{mat_path}/{tex_name}')
                tex.CreateIdAttr('UsdUVTexture')
                tex.CreateInput('file', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(tex_fp))
                tex.CreateInput('sourceColorSpace', Sdf.ValueTypeNames.Token).Set(colorspace)
                tex.CreateInput('st', Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(), 'result')
                tex.CreateOutput(out_name, value_type)
                sh.CreateInput(input_name, value_type).ConnectToSource(tex.ConnectableAPI(), out_name)

            if diff_path:
                _bind_goz_tex(diff_path, 'Tex_diffuseColor', 'diffuseColor', Sdf.ValueTypeNames.Color3f, 'rgb', 'sRGB')
            elif has_polypaint:
                dc_reader = UsdShade.Shader.Define(stage, f'{mat_path}/PrimvarDisplayColor')
                dc_reader.CreateIdAttr('UsdPrimvarReader_float3')
                dc_reader.CreateInput('varname', Sdf.ValueTypeNames.Token).Set('displayColor')
                dc_reader.CreateOutput('result', Sdf.ValueTypeNames.Float3)
                sh.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).ConnectToSource(
                    dc_reader.ConnectableAPI(), 'result'
                )

            if norm_path:
                _bind_goz_tex(norm_path, 'Tex_normal', 'normal', Sdf.ValueTypeNames.Normal3f, 'rgb', 'raw')
            if disp_path:
                _bind_goz_tex(disp_path, 'Tex_displacement', 'displacement', Sdf.ValueTypeNames.Float, 'r', 'raw')

            UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat)

    return stage


_ACTIVE_SUBLAYER_PROJECTS = set()


def _load_sublayer_project_graph(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except Exception as e:
        raise ValueError(f'Unable to read Sublayer project ({path}): {e}') from e
    if not isinstance(raw, dict):
        raise ValueError(f'Sublayer project must be a JSON object: {path}')
    nodes = raw.get('nodes')
    edges = raw.get('edges') or []
    dependencies = list(raw.get('dependencies') or [])
    if not isinstance(nodes, list) or not nodes:
        raise ValueError(f'Sublayer project has no nodes: {path}')
    if not isinstance(edges, list):
        raise ValueError(f'Sublayer project has invalid edges: {path}')

    # Support legacy v1 inline node paths if dependencies are absent.
    dep_ids = {d.get('id') for d in dependencies if isinstance(d, dict)}
    for n in nodes:
        data = n.get('data') if isinstance(n, dict) else None
        if not isinstance(data, dict):
            continue
        inline_path = data.get('path')
        if inline_path and not data.get('inputDependencyId'):
            syn_id = f'__inline_{n.get("id", len(dependencies))}'
            if syn_id not in dep_ids:
                dependencies.append({'id': syn_id, 'kind': 'usd', 'path': inline_path})
                dep_ids.add(syn_id)
            data['inputDependencyId'] = syn_id

    node_ids = {n.get('id') for n in nodes if isinstance(n, dict) and n.get('id')}
    incoming_counts = {nid: 0 for nid in node_ids}
    outgoing_counts = {nid: 0 for nid in node_ids}
    for e in edges:
        if not isinstance(e, dict):
            continue
        src, dst = e.get('source'), e.get('target')
        if src in outgoing_counts:
            outgoing_counts[src] += 1
        if dst in incoming_counts:
            incoming_counts[dst] += 1

    output_nodes = [
        n.get('id')
        for n in nodes
        if isinstance(n, dict) and n.get('data', {}).get('operation') == 'output' and n.get('id')
    ]
    connected_outputs = [nid for nid in output_nodes if incoming_counts.get(nid, 0) > 0]
    pinned_id = (raw.get('viewport') or {}).get('pinnedNodeId')
    leaf_nodes = [nid for nid in node_ids if outgoing_counts.get(nid, 0) == 0]

    if connected_outputs:
        target_id = connected_outputs[0]
    elif output_nodes:
        target_id = output_nodes[0]
    elif pinned_id in node_ids:
        target_id = pinned_id
    elif leaf_nodes:
        target_id = leaf_nodes[0]
    else:
        target_id = nodes[-1].get('id')

    if target_id not in node_ids:
        raise ValueError(f'Sublayer project has no valid target node: {path}')

    compiled_nodes = []
    for n in nodes:
        if not isinstance(n, dict) or not n.get('id'):
            continue
        data = n.get('data') if isinstance(n.get('data'), dict) else n
        compiled_nodes.append({'id': n['id'], **data})

    return {
        'nodes': compiled_nodes,
        'edges': edges,
        'dependencies': dependencies,
        'targetNodeId': target_id,
    }


def _sublayer_project_stamp(path, _seen=None):
    import hashlib
    norm_path = os.path.normcase(os.path.realpath(path))
    seen = set(_seen or ())
    if norm_path in seen:
        return f'cycle:{norm_path}'
    seen.add(norm_path)
    st = os.stat(norm_path)
    parts = [norm_path, str(st.st_mtime_ns), str(st.st_size)]
    try:
        with open(norm_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        deps = raw.get('dependencies') or []
        dep_entries = []
        for d in deps:
            if not isinstance(d, dict):
                continue
            dp = d.get('path')
            if not isinstance(dp, str) or not dp.strip():
                continue
            ndp = os.path.normcase(os.path.realpath(dp))
            if os.path.exists(ndp):
                if os.path.splitext(ndp)[1].lower() in ('.sublayer', '.orchflow'):
                    dep_entries.append(f'{ndp}|{_sublayer_project_stamp(ndp, seen)}')
                else:
                    dst = os.stat(ndp)
                    dep_entries.append(f'{ndp}|{dst.st_mtime_ns}|{dst.st_size}')
            else:
                dep_entries.append(f'{ndp}|missing')
        parts.extend(sorted(dep_entries))
    except Exception:
        pass
    return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()[:24]


def _open_sublayer_source_stage(path, reload=False):
    import hashlib
    import tempfile
    from pxr import Usd

    if not os.path.isfile(path):
        raise ValueError(f'Sublayer project file is missing: {path}')
    norm_path = os.path.normcase(os.path.realpath(path))
    if norm_path in _ACTIVE_SUBLAYER_PROJECTS:
        raise ValueError(f'Circular Sublayer project reference detected: {path}')

    path_hash = hashlib.sha256(norm_path.encode('utf-8')).hexdigest()[:12]
    stamp = _sublayer_project_stamp(norm_path)
    cache_dir = os.path.join(tempfile.gettempdir(), 'sublayer_project_cache')
    os.makedirs(cache_dir, exist_ok=True)
    cache_usdc = os.path.join(cache_dir, f'{path_hash}_{stamp}.usdc')

    if reload or not os.path.isfile(cache_usdc):
        _ACTIVE_SUBLAYER_PROJECTS.add(norm_path)
        tmp_usdc = os.path.join(cache_dir, f'{path_hash}_{stamp}.{os.getpid()}.tmp.usdc')
        try:
            sub_graph = _load_sublayer_project_graph(path)
            compiled_stage, _keepalive, _ = compile_graph(sub_graph, reload=reload)
            flattened = compiled_stage.Flatten(False)
            if not flattened or not flattened.Export(tmp_usdc):
                raise ValueError(f'Unable to export Sublayer project cache: {cache_usdc}')
            os.replace(tmp_usdc, cache_usdc)
            _cleanup_stale_source_cache(cache_dir, path_hash, {os.path.basename(cache_usdc)})
            _prune_disk_caches(keep_paths=(cache_usdc,))
        finally:
            _ACTIVE_SUBLAYER_PROJECTS.discard(norm_path)
            if os.path.exists(tmp_usdc):
                try:
                    os.remove(tmp_usdc)
                except OSError:
                    pass
    else:
        _touch_cache_file(cache_usdc)

    source = Usd.Stage.Open(cache_usdc)
    if source is None:
        raise ValueError(f'Unable to open cached Sublayer project USD: {cache_usdc}')
    if reload:
        source.Reload()
    return source


def _open_source_stage(path, reload=False, load_payloads=True):
    from pxr import Usd

    ext = os.path.splitext(path)[1].lower()
    if ext == '.abc':
        return _open_alembic_source_stage(path, reload=reload)
    if ext == '.obj':
        return _open_obj_source_stage(path)
    if ext == '.vdb':
        return _open_vdb_source_stage(path)
    if ext == '.goz':
        return _open_goz_source_stage(path)
    if ext in ('.sublayer', '.orchflow'):
        return _open_sublayer_source_stage(path, reload=reload)
    stage_load = Usd.Stage.LoadAll if load_payloads else Usd.Stage.LoadNone
    source = Usd.Stage.Open(path, load=stage_load)
    if source is None:
        raise ValueError(f'Unable to open USD input: {path}')
    return source


def convert_source_to_usd(request):
    path = request.get('path')
    destination = request.get('destination')
    if not isinstance(path, str) or not path.strip():
        raise ValueError('A source file path is required')
    if not isinstance(destination, str) or not destination.strip():
        raise ValueError('A destination USD path is required')
    ext = os.path.splitext(destination)[1].lower()
    if ext not in ('.usd', '.usda', '.usdc'):
        raise ValueError('Destination must be a .usd, .usda, or .usdc file')
    stage = _open_source_stage(path)
    out_dir = os.path.dirname(os.path.abspath(destination))
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    if not stage.Export(destination):
        raise ValueError(f'Failed exporting converted USD to {destination}')
    abs_dest = os.path.abspath(destination)
    return {
        'exported': abs_dest,
        'destination': abs_dest,
        'primCount': sum(1 for _ in stage.Traverse()),
        'sourceFormat': os.path.splitext(path)[1].lstrip('.').lower(),
    }


def _pose_dependency_stamps(node, files):
    result = []
    for entry in (node.get('pose') or {}).get('entries', []):
        did = entry.get('dependencyId')
        if did:
            path = os.path.normcase(os.path.realpath(files.get(did, '')))
            stat = os.stat(path) if os.path.isfile(path) else None
            result.append((did, path, stat.st_mtime_ns if stat else 0, stat.st_size if stat else 0))
    return result
