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


def clear_graph_caches():
    _SOURCE_STAGE_CACHE.clear()
    _MULTI_ROOT_CACHE.clear()
    _BLENDER_FULL_LAYER_CACHE.clear()
    _BLENDER_STAGE_CACHE.clear()


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


def apply_transform(stage, node, default_target=None):
    from pxr import UsdGeom
    matrix = _build_transform_matrix(node.get('transform'))
    label = 'node_' + hashlib.sha256(str(node.get('id', '')).encode()).hexdigest()[:16]
    targets = node.get('transformTargets')
    if not targets:
        if default_target and default_target.IsValid():
            prepend_root_transform(default_target, matrix, label)
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
        prepend_root_transform(prim, matrix, label)


def prepend_root_transform(prim,matrix,label):
    """Apply an outer transform on an existing scene root without a parent prim."""
    from pxr import UsdGeom,Sdf,Gf
    if matrix==Gf.Matrix4d(1):return None
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
        for cand_name in ('Color', 'Image', 'Input', 'Texture', 'A', 'B', 'Color1', 'Color2', 'Normal', 'Height'):
            s = src_node.inputs.get(cand_name) if hasattr(src_node, 'inputs') else None
            if s and s.is_linked:
                tex, val = _find_texture_or_color(s, visited, group_stack)
                if tex or val is not None:
                    return tex, val
        for s in getattr(src_node, 'inputs', []):
            if s.is_linked:
                tex, val = _find_texture_or_color(s, visited, group_stack)
                if tex or val is not None:
                    return tex, val
        return None, getattr(sock, 'default_value', None)

    def _unwrap_shader(node, visited=None, group_stack=()):
        if node is None:
            return None, 1.0, group_stack
        if visited is None:
            visited = set()
        if node in visited:
            return None, 1.0, group_stack
        visited.add(node)
        bid = node.bl_idname
        if bid == 'ShaderNodeGroup' and getattr(node, 'node_tree', None):
            out_n = next((n for n in node.node_tree.nodes if n.bl_idname == 'NodeGroupOutput'), None)
            if out_n:
                next_stack = group_stack + (node,)
                for s in out_n.inputs:
                    if s.is_linked and (s.type == 'SHADER' or s.name in ('Surface', 'Shader', 'BSDF', 'Material')):
                        return _unwrap_shader(s.links[0].from_node, visited, next_stack)
                for s in out_n.inputs:
                    if s.is_linked:
                        return _unwrap_shader(s.links[0].from_node, visited, next_stack)
        if bid == 'ShaderNodeMixShader':
            fac = _to_float(getattr(node.inputs[0], 'default_value', 0.5), 0.5) if len(node.inputs) > 0 else 0.5
            s1 = node.inputs[1].links[0].from_node if len(node.inputs) > 1 and node.inputs[1].is_linked else None
            s2 = node.inputs[2].links[0].from_node if len(node.inputs) > 2 and node.inputs[2].is_linked else None
            if s1 and s1.bl_idname == 'ShaderNodeBsdfTransparent' and s2:
                sub, op, gs = _unwrap_shader(s2, visited, group_stack)
                return sub, min(op, _clamp01(fac)), gs
            if s2 and s2.bl_idname == 'ShaderNodeBsdfTransparent' and s1:
                sub, op, gs = _unwrap_shader(s1, visited, group_stack)
                return sub, min(op, _clamp01(1.0 - fac)), gs
            pick = s2 if (s2 and (fac >= 0.5 or not s1)) else s1
            return _unwrap_shader(pick, visited, group_stack)
        if bid == 'OctaneMixMaterial':
            amt = _to_float(getattr(node.inputs.get('Amount'), 'default_value', 0.5), 0.5)
            s1 = node.inputs.get('First material').links[0].from_node if node.inputs.get('First material') and node.inputs['First material'].is_linked else None
            s2 = node.inputs.get('Second material').links[0].from_node if node.inputs.get('Second material') and node.inputs['Second material'].is_linked else None
            pick = s2 if (s2 and (amt >= 0.5 or not s1)) else s1
            return _unwrap_shader(pick, visited, group_stack)
        return node, 1.0, group_stack

    def _pick_output(nt):
        outs = [n for n in nt.nodes if n.bl_idname == 'ShaderNodeOutputMaterial' and n.inputs.get('Surface') and n.inputs['Surface'].is_linked]
        if not outs:
            return None
        prefer_octane = (material_mode == 'octane') or (material_mode == 'auto' and 'octane' in engine)
        def _score(o):
            tgt = getattr(o, 'target', 'ALL') or 'ALL'
            src, _, _ = _unwrap_shader(o.inputs['Surface'].links[0].from_node)
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
        shader, mix_opacity, group_stack = _unwrap_shader(out_node.inputs['Surface'].links[0].from_node)
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


def _blender_disk_cache_paths(norm_path, mtime, size):
    import hashlib, tempfile
    cache_dir = os.path.join(tempfile.gettempdir(), 'sublayer_blender_cache')
    key = hashlib.sha256(f"{norm_path}|{mtime}|{size}|v5".encode('utf-8')).hexdigest()[:24]
    return cache_dir, os.path.join(cache_dir, f"{key}.usdc"), os.path.join(cache_dir, f"{key}.json")


def _get_or_extract_full_blender_layer(blend_path, mtime, selected_objects=None, reload=False, material_mode='off', est_seconds=None, convert_procedurals=False):
    import os, tempfile, subprocess, json
    from pxr import Sdf, UsdUtils
    material_mode = material_mode if material_mode in ('off', 'auto', 'octane', 'cycles') else 'off'
    norm_path = os.path.normcase(os.path.abspath(blend_path))
    file_size = os.path.getsize(blend_path) if os.path.isfile(blend_path) else 0
    cache_dir, disk_usdc, disk_json = _blender_disk_cache_paths(norm_path, mtime, file_size)
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

    matching_keys = [k for k in _BLENDER_FULL_LAYER_CACHE if k[0] == norm_path and k[1] == mtime]
    if matching_keys:
        if not selected_objects:
            k = matching_keys[-1]
            raw_entry = _BLENDER_FULL_LAYER_CACHE.pop(k)
            if material_mode == 'off' or raw_entry.get("has_materials"):
                _BLENDER_FULL_LAYER_CACHE[k] = raw_entry
                return _materialize_mode(raw_entry)
        else:
            sel_set = set(str(x) for x in selected_objects)
            best_k = max(matching_keys, key=lambda k: len(sel_set & _BLENDER_FULL_LAYER_CACHE[k]["scene_objects"]))
            if sel_set.issubset(_BLENDER_FULL_LAYER_CACHE[best_k]["scene_objects"]):
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
            sel_ok = (not selected_objects) or set(str(x) for x in selected_objects).issubset(cached_objs)
            if (sel_ok or prev_entry is None) and (material_mode == 'off' or has_mats):
                disk_layer = Sdf.Layer.FindOrOpen(disk_usdc)
                if disk_layer:
                    raw_layer = Sdf.Layer.CreateAnonymous('blender_full.usdc')
                    raw_layer.TransferContent(disk_layer)
                    del disk_layer
                    raw_entry = {
                        "raw_layer": raw_layer,
                        "mat_bundles": cached.get("matBundles") or {},
                        "has_materials": has_mats,
                        "scene": str(cached.get("scene") or ""),
                        "scene_objects": cached_objs,
                        "default_visible": list(cached.get("defaultVisible") or []),
                    }
                    if sel_ok:
                        report_status(f"Blender: Loading cached scene layer for {os.path.basename(blend_path)}...")
                        full_key = (norm_path, mtime, raw_entry["scene"])
                        while len(_BLENDER_FULL_LAYER_CACHE) >= _MAX_BLENDER_FULL_CACHE:
                            _BLENDER_FULL_LAYER_CACHE.pop(next(iter(_BLENDER_FULL_LAYER_CACHE)))
                        _BLENDER_FULL_LAYER_CACHE[full_key] = raw_entry
                        return _materialize_mode(raw_entry)
                    prev_entry = raw_entry
        except Exception:
            pass

    extract_objects = selected_objects
    if prev_entry is not None and selected_objects:
        missing_objs = sorted(set(str(x) for x in selected_objects) - prev_entry["scene_objects"])
        if missing_objs:
            extract_objects = missing_objs

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

scenes = list(bpy.data.scenes)
win_scene = bpy.context.window.scene if bpy.context.window and bpy.context.window.scene else bpy.context.scene
if isinstance(selected, list) and len(selected) > 0:
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
            abs_fp = os.path.abspath(bpy.path.abspath(fp))
            if abs_fp != fp:
                img.filepath = abs_fp
        except Exception:
            pass

for obj in bpy.context.view_layer.objects:
    obj.hide_viewport = False
    obj.hide_render = False
    if obj.hide_get():
        obj.hide_set(False)

for obj in bpy.data.objects:
    obj["orchflow_blender_object"] = obj.name

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
            for m in getattr(obj, 'modifiers', ()):
                if getattr(m, 'type', None) == 'ARMATURE' and getattr(m, 'object', None) is not None:
                    arm = m.object
                    while arm is not None:
                        to_select.add(arm)
                        arm = arm.parent
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
    'export_armatures': False,
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
meta = {
    'scene': target_scene.name,
    'sceneObjects': sorted(exported_names),
    'defaultVisible': default_visible,
}
print('\nORCHFLOW_BLENDER_META:' + json.dumps(meta), flush=True)
if mat_bundles:
    print('\nORCHFLOW_BLENDER_MATERIALS:' + json.dumps(mat_bundles), flush=True)
print('\nORCHFLOW_BLENDER_EXPORT:SUCCESS', flush=True)
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
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=0x08000000 if os.name == 'nt' else 0,
            text=True
        )
        payload = json.dumps({"usdPath": temp_usd_path, "selectedObjects": extract_objects, "materialMode": material_mode})
        try:
            stdout, stderr = proc.communicate(input=payload + "\n")
        finally:
            report_status("")
        if proc.returncode != 0 or 'ORCHFLOW_BLENDER_EXPORT:SUCCESS' not in stdout:
            raise RuntimeError(f"Blender headless extraction failed: {stderr} {stdout}")
        if not os.path.isfile(temp_usd_path) or os.path.getsize(temp_usd_path) == 0:
            raise RuntimeError(f"Blender failed to produce USD data for {blend_path}")
        scene_name = ""
        scene_objects = set()
        default_visible = []
        mat_bundles = {}
        for line in stdout.splitlines():
            if line.startswith("ORCHFLOW_BLENDER_META:"):
                try:
                    meta = json.loads(line[len("ORCHFLOW_BLENDER_META:"):].strip())
                    scene_name = str(meta.get("scene") or "")
                    scene_objects = set(meta.get("sceneObjects") or [])
                    default_visible = list(meta.get("defaultVisible") or [])
                except Exception:
                    pass
            elif line.startswith("ORCHFLOW_BLENDER_MATERIALS:"):
                try:
                    mat_bundles, _ = json.JSONDecoder().raw_decode(line[len("ORCHFLOW_BLENDER_MATERIALS:"):].lstrip())
                except Exception:
                    pass
        temp_layer = Sdf.Layer.FindOrOpen(temp_usd_path)
        if not temp_layer:
            raise RuntimeError("Failed to read exported USD from Blender")
        report_status(f"Blender: Ingesting extracted scene layer into memory...")
        blend_dir = os.path.dirname(os.path.abspath(blend_path))

        def _anchor_asset_path(p):
            if not p or os.path.isabs(p) or p.startswith('anon:'):
                return p
            cand = os.path.abspath(os.path.join(blend_dir, p))
            return cand if os.path.exists(cand) else p

        UsdUtils.ModifyAssetPaths(temp_layer, _anchor_asset_path)
        if prev_entry is not None and extract_objects != selected_objects:
            raw_layer = Sdf.Layer.CreateAnonymous('blender_full.usda')
            raw_layer.TransferContent(prev_entry["raw_layer"])
            src_root = temp_layer.GetPrimAtPath('/root')
            if src_root:
                if not raw_layer.GetPrimAtPath('/root'):
                    Sdf.CreatePrimInLayer(raw_layer, Sdf.Path('/root'))
                for child_name in src_root.nameChildren.keys():
                    child_path = Sdf.Path(f'/root/{child_name}')
                    if not raw_layer.GetPrimAtPath(child_path):
                        Sdf.CreatePrimInLayer(raw_layer, child_path)
                        Sdf.CopySpec(temp_layer, child_path, raw_layer, child_path)
                    elif child_name in ('_materials', 'prototypes'):
                        sub = temp_layer.GetPrimAtPath(child_path)
                        if sub:
                            for sub_name in sub.nameChildren.keys():
                                sub_path = child_path.AppendChild(sub_name)
                                if not raw_layer.GetPrimAtPath(sub_path):
                                    Sdf.CreatePrimInLayer(raw_layer, sub_path)
                                    Sdf.CopySpec(temp_layer, sub_path, raw_layer, sub_path)
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
                tmp_u = disk_usdc + '.tmp'
                tmp_j = disk_json + '.tmp'
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
        full_key = (norm_path, mtime, scene_name)
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
    from pxr import Sdf, Usd, UsdGeom, UsdShade, UsdSkel, UsdVol

    def _get_blender_obj_name(prim):
        for attr_name in ('userProperties:orchflow_blender_object', 'blenderName:object', 'userProperties:blender:object_name'):
            attr = prim.GetAttribute(attr_name)
            if attr and attr.HasAuthoredValue():
                val = attr.Get()
                if isinstance(val, str) and val:
                    return val
        return None

    KEEP_CURVE_PRIMVARS = {
        'primvars:displayColor',
        'primvars:displayOpacity',
        'primvars:st',
        'primvars:uv',
        'primvars:normals',
        'primvars:widths',
    }
    to_remove = []
    to_clean_curves = []
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Subset):
            continue
        if prim.IsA(UsdGeom.Gprim) or prim.IsA(UsdVol.Volume):
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
                keep = any(name in sel_set for name in ancestor_names)
            else:
                keep = any(a.GetName() in sel_set for a in ancestors)
            if keep and prim.IsA(UsdGeom.BasisCurves):
                bc = UsdGeom.BasisCurves(prim)
                pts = bc.GetPointsAttr().Get()
                widths = bc.GetWidthsAttr().Get()
                if not pts or len(pts) == 0:
                    keep = False
                elif widths and min(widths) >= 1.0:
                    keep = False
                else:
                    to_clean_curves.append(prim)
            elif keep and prim.IsA(UsdVol.Volume):
                has_valid_field = any(
                    r.GetName().startswith('field:')
                    and any(stage.GetPrimAtPath(t).IsValid() for t in r.GetTargets())
                    for r in prim.GetAuthoredRelationships()
                )
                if not has_valid_field and not prim.GetChildren():
                    keep = False
            if not keep:
                to_remove.append(prim.GetPath())
        elif prim.GetTypeName() in ('SphereLight', 'DistantLight', 'RectLight', 'DiskLight', 'CylinderLight', 'DomeLight', 'Camera'):
            to_remove.append(prim.GetPath())

    for prim in to_clean_curves:
        for prop_name in prim.GetPropertyNames():
            if prop_name.startswith('primvars:') and prop_name not in KEEP_CURVE_PRIMVARS:
                prim.RemoveProperty(prop_name)

    for path in to_remove:
        if stage.GetPrimAtPath(path):
            stage.RemovePrim(path)

    def _has_geom_descendant(prim):
        if prim.IsA(UsdGeom.Gprim) or prim.IsA(UsdVol.Volume) or prim.IsA(UsdVol.FieldBase):
            return True
        return any(_has_geom_descendant(child) for child in prim.GetAllChildren())

    def _prune_empty_ancestors(prim):
        for child in list(prim.GetAllChildren()):
            if (
                child.GetName() in ('_materials', '_OrchFlowMaterials')
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


def get_or_extract_blender_memory_stage(blend_path, selections=None, reload=False, material_mode='off', est_seconds=None, convert_procedurals=False):
    import os
    from pxr import Sdf, Usd, UsdGeom
    material_mode = material_mode if material_mode in ('off', 'auto', 'octane', 'cycles') else 'off'
    convert_procedurals = bool(convert_procedurals)
    if not os.path.isfile(blend_path):
        raise ValueError(f"Blender file does not exist: {blend_path}")
    mtime = os.path.getmtime(blend_path)
    norm_path = os.path.normcase(os.path.abspath(blend_path))
    sel_key = None if selections is None else tuple(sorted(str(x) for x in selections))
    cache_key = (norm_path, mtime, sel_key, material_mode, convert_procedurals)
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

    entry = _get_or_extract_full_blender_layer(blend_path, mtime, selected_objects=selections, reload=reload, material_mode=material_mode, convert_procedurals=convert_procedurals)
    full_layer = entry["layer"]
    mem_layer = Sdf.Layer.CreateAnonymous('blender_stage.usdc')
    mem_layer.TransferContent(full_layer)
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
    while len(_BLENDER_STAGE_CACHE) >= _MAX_BLENDER_STAGE_CACHE:
        _BLENDER_STAGE_CACHE.pop(next(iter(_BLENDER_STAGE_CACHE)))
    _BLENDER_STAGE_CACHE[cache_key] = stage
    return stage


def compile_graph(graph, reload=False):
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdUtils
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

    def empty_stage():
        stage = Usd.Stage.CreateInMemory()
        root = UsdGeom.Xform.Define(stage, '/Scene')
        stage.SetDefaultPrim(root.GetPrim())
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(stage, 1)
        stage.SetTimeCodesPerSecond(24);stage.SetFramesPerSecond(24)
        keepalive.append(stage)
        return stage, root

    def _source_up_rotation(axis_token, source_stage):
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
        return rot

    def wrap_source_stage(source, instanced=True, norm_path=None, mtime=0.0, axis='preserve', node_scale=1.0):
        roots = list(source.GetPseudoRoot().GetChildren())
        if not roots:
            raise ValueError(f'USD input has no root prims')
        if len(roots) == 1:
            reference_layer, reference_prim = source.GetRootLayer(), roots[0].GetPath()
        else:
            multi_entry = _MULTI_ROOT_CACHE.get(norm_path) if norm_path else None
            if multi_entry and multi_entry[0] == mtime:
                reference_layer, reference_prim = multi_entry[1], multi_entry[2]
                keepalive.append(reference_layer)
            else:
                # A USD reference cannot target the pseudo-root. Normalize a private
                # layer-stack snapshot once per distinct multi-root asset so sibling
                # material bindings stay in the same namespace. References/payloads/
                # variants stay composition arcs; no external layer is edited.
                layer = UsdUtils.FlattenLayerStack(source)
                prepared = Usd.Stage.Open(layer)
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
                if norm_path:
                    while len(_MULTI_ROOT_CACHE) >= _MAX_MULTI_ROOT_CACHE:
                        _MULTI_ROOT_CACHE.pop(next(iter(_MULTI_ROOT_CACHE)), None)
                    _MULTI_ROOT_CACHE[norm_path] = (mtime, reference_layer, reference_prim)
                keepalive.extend([layer, prepared])
        stage, _ = empty_stage()
        ratio = stage.GetTimeCodesPerSecond() / source.GetTimeCodesPerSecond()
        stage.SetStartTimeCode(source.GetStartTimeCode() * ratio)
        stage.SetEndTimeCode(source.GetEndTimeCode() * ratio)
        conversion = UsdGeom.Xformable(stage.GetDefaultPrim())
        import math
        if isinstance(node_scale, bool) or not isinstance(node_scale, (int, float)) or not math.isfinite(node_scale) or node_scale <= 0:
            raise ValueError(f'Invalid source scale: {node_scale}')
        scale = Gf.Matrix4d().SetScale(UsdGeom.GetStageMetersPerUnit(source) * float(node_scale))
        rotation = _source_up_rotation(axis, source)
        if scale * rotation != Gf.Matrix4d(1):
            conversion.AddTransformOp(opSuffix='sourceUnits').Set(scale * rotation)
        prim = stage.OverridePrim(Sdf.Path('/Scene').AppendChild(reference_prim.name))
        prim.GetReferences().AddReference(reference_layer.identifier, reference_prim)
        mark_reference_instanceable(prim, source, enable=instanced)
        return stage

    def source_stage(dependency_id, instanced=True, axis='preserve', node_scale=1.0):
        if not dependency_id or dependency_id not in files:
            raise ValueError('Source USD or published USD file link is missing')
        path = files[dependency_id]
        norm_path = os.path.normcase(os.path.realpath(path))
        mtime = os.path.getmtime(norm_path) if os.path.exists(norm_path) else 0.0
        key = (norm_path, bool(instanced), str(axis or 'preserve'), float(node_scale))
        if key in assets:
            return assets[key]

        source = None
        if reload:
            _SOURCE_STAGE_CACHE.pop(norm_path, None)
            _MULTI_ROOT_CACHE.pop(norm_path, None)
        elif norm_path in _SOURCE_STAGE_CACHE:
            cached_mtime, cached_source = _SOURCE_STAGE_CACHE[norm_path]
            if cached_mtime == mtime:
                source = cached_source

        if source is None:
            source = _open_source_stage(path, reload=reload)
            while len(_SOURCE_STAGE_CACHE) >= _MAX_SOURCE_STAGE_CACHE:
                _SOURCE_STAGE_CACHE.pop(next(iter(_SOURCE_STAGE_CACHE)), None)
            _SOURCE_STAGE_CACHE[norm_path] = (mtime, source)
        elif reload:
            if os.path.splitext(path)[1].lower() in ('.abc', '.obj', '.vdb', '.goz'):
                source = _open_source_stage(path, reload=True)
                _SOURCE_STAGE_CACHE[norm_path] = (mtime, source)
            else:
                source.Reload()

        keepalive.append(source)
        stage = wrap_source_stage(
            source,
            instanced=instanced,
            norm_path=norm_path,
            mtime=mtime,
            axis=axis,
            node_scale=node_scale,
        )
        assets[key] = stage
        return stage

    for id in order:
        if id not in needed:
            continue
        node = lookup[id]
        operation = node['operation']
        if operation == 'application' and node.get('liveAdjustment'):
            branch=dict(graph);branch['targetNodeId']=id
            branch['nodes']=[{**n,'operation':('source' if n.get('inputDependencyId') else 'output'),'liveAdjustment':None,'publication':None,'publishedDependencyId':None} if n['id']==id else n for n in graph['nodes']]
            compose=globals().get('compose_live_adjustment')
            if compose is None:
                from usd_publish import compose_live_adjustment as compose
            results[id],alive=compose(branch,node['liveAdjustment'])
            keepalive.extend(alive);continue
        if operation == 'application':
            raise ValueError(f'Application node {id} has no saved live adjustment. Review edited USD, Apply reviewed edits, then save OrchFlow. Disk publications are not used.')
        inputs = [(edge['id'], results[edge['source']]) for edge in incoming[id]]
        if operation == 'source' and node.get('inputDependencyId'):
            instanced = node.get('instanced', True)
            inputs.insert(
                0,
                (
                    'own-source',
                    source_stage(
                        node['inputDependencyId'],
                        instanced=instanced,
                        axis=node.get('axis', 'preserve'),
                        node_scale=node.get('scale', 1.0),
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
                    continue
            elif blend_path:
                if not os.path.isfile(blend_path):
                    raise ValueError(f'Blender file does not exist: {blend_path}')
                mtime = os.path.getmtime(blend_path)
                sel_key = None if selections is None else tuple(sorted(str(x) for x in selections))
                material_mode = node.get('blenderMaterialMode') or 'off'
                convert_procedurals = bool(node.get('blenderConvertProcedurals', False))
                asset_key_tuple = ('blender', os.path.normcase(os.path.abspath(blend_path)), mtime, sel_key, instanced, material_mode, convert_procedurals)
                if asset_key_tuple in assets:
                    wrapped = assets[asset_key_tuple]
                else:
                    est_secs = node.get('lastExtractionSeconds')
                    b_stage = get_or_extract_blender_memory_stage(blend_path, selections, reload=reload, material_mode=material_mode, est_seconds=est_secs, convert_procedurals=convert_procedurals)
                    keepalive.append(b_stage)
                    wrapped = wrap_source_stage(b_stage, instanced=instanced)
                    assets[asset_key_tuple] = wrapped
                inputs.insert(0, ('own-source', wrapped))
            elif usd_dep and usd_dep in files:
                inputs.insert(0, ('own-source', source_stage(usd_dep, instanced=instanced)))
            elif not inputs:
                raise ValueError(f'Blender node "{node.get("label", id)}" needs a linked .blend file.')
        elif operation not in ('source', 'blender', 'transform', 'output', 'assetOverride', 'materialx', 'material_bind', 'light', 'hdri', 'hair', 'camera', 'delete'):
            raise ValueError(f'Unsupported USD graph operation: {operation}')
        if not inputs:
            raise ValueError(f'{operation.title()} node needs a linked USD file or incoming USD connection')
        if operation == 'assetOverride':
            inputs = [(edge, override_stage(stage, node.get('assetOverrides', []), files, keepalive, override_cache)) for edge, stage in inputs]
        if operation == 'material_bind':
            stage, alive_bind = compose_material_binding_node(inputs, node)
            keepalive.extend(alive_bind)
            results[id] = stage
            continue
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
            continue
        if operation == 'camera' and len(inputs) == 1:
            results[id] = inputs[0][1]
            continue
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
            continue
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
            continue
        if operation == 'materialx' and len(inputs)==1:
            upstream=inputs[0][1]
            overlay=Sdf.Layer.CreateAnonymous('materialx.usda')
            overlay.subLayerPaths=[upstream.GetRootLayer().identifier]
            stage=Usd.Stage.Open(overlay)
            inherit_stage_metrics(stage,upstream)
            stage.SetDefaultPrim(stage.GetPrimAtPath(upstream.GetDefaultPrim().GetPath()))
            author_materialx_rules(stage,node,files,procedural_graph=graph.get('proceduralGraph'))
            keepalive.extend([upstream,overlay,stage])
            results[id]=stage
            continue
        if operation == 'delete' and len(inputs)==1:
            upstream=inputs[0][1]
            overlay=Sdf.Layer.CreateAnonymous('delete.usda');overlay.subLayerPaths=[upstream.GetRootLayer().identifier]
            stage=Usd.Stage.Open(overlay);keepalive.extend([overlay,stage,upstream])
            inherit_stage_metrics(stage,upstream)
            stage.SetDefaultPrim(stage.GetPrimAtPath(upstream.GetDefaultPrim().GetPath()))
            apply_delete(stage,node.get('deletePaths',[]))
            results[id]=stage;continue
        if operation=='transform' and len(inputs)==1:
            upstream=inputs[0][1]
            overlay=Sdf.Layer.CreateAnonymous('transform.usda');overlay.subLayerPaths=[upstream.GetRootLayer().identifier]
            stage=Usd.Stage.Open(overlay);inherit_stage_metrics(stage,upstream)
            stage.SetDefaultPrim(stage.GetPrimAtPath(upstream.GetDefaultPrim().GetPath()))
            apply_transform(stage,node,stage.GetDefaultPrim())
            keepalive.extend([upstream,overlay,stage]);results[id]=stage;continue
        if operation in ('source', 'blender', 'assetOverride', 'output') and len(inputs) == 1:
            results[id] = inputs[0][1]
            continue
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
        if operation == 'transform':apply_transform(stage,node,root)
        if operation == 'delete':apply_delete(stage,node.get('deletePaths',[]))
        if operation == 'materialx':author_materialx_rules(stage,node,files,procedural_graph=graph.get('proceduralGraph'))
        if operation == 'light':apply_light_overrides(stage,node.get('lightOverrides',[]))
        if operation == 'hdri':apply_hdri(stage,node.get('hdri'),files)
        if operation == 'hair':apply_hair(stage,node.get('hair'))
        results[id] = stage
    result = results[target]
    stats = {'evaluatedNodes': len(results), 'uniqueSourceAssets': len(assets),
             'prototypes': len(result.GetPrototypes()),
             'instances': sum(1 for prim in result.Traverse() if prim.IsInstance())}
    return result, keepalive, stats


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
    stage, alive, stats = compile_graph(graph, reload=True)
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
    if flattened.GetCompositionAssetDependencies():raise ValueError('Single-file snapshot still has external USD composition dependencies')
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
    if not isinstance(value['bindingTargets'],list) or not value['bindingTargets'] or len(value['bindingTargets'])>1000 or any(not isinstance(p,str) or not p for p in value['bindingTargets']):raise ValueError('Choose explicit MaterialX binding targets or *')
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
    # A changed network receives a new Hydra material identity. Delegates may retain
    # properties/textures when a material is replaced at an existing scene path.
    import hashlib,json
    proc_hash=procedural_mtlx.subgraph_hash(procedural_subgraph)
    identity_payload={**m, **({'_proceduralGraphHash': proc_hash} if proc_hash else {})}
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
    surface.CreateInput('subsurface_radius',Sdf.ValueTypeNames.Color3f).Set(safe_vec3f(m.get('subsurfaceRadius')))
    surface.CreateInput('subsurface_scale',Sdf.ValueTypeNames.Float).Set(val('subsurfaceScale',1.0))
    surface.CreateInput('opacity',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(opacity))

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
        uv=UsdShade.Shader.Define(stage,path.AppendChild('UV_st'));uv.CreateIdAttr('ND_geompropvalue_vector2');uv.CreateInput('geomprop',Sdf.ValueTypeNames.String).Set('st');uv.CreateOutput('out',Sdf.ValueTypeNames.Float2)
        puv=UsdShade.Shader.Define(stage,path.AppendChild('PreviewUV_st'));puv.CreateIdAttr('UsdPrimvarReader_float2');puv.CreateInput('varname',Sdf.ValueTypeNames.Token).Set('st');puv.CreateOutput('result',Sdf.ValueTypeNames.Float2)
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


def author_materialx_rules(stage,node,files,procedural_graph=None):
    procedural_mtlx = _procedural_mtlx()
    rules=node.get('materialXRules')
    if rules is None:
        sub=procedural_mtlx.collect_rule_subgraph(procedural_graph,node.get('id'),None)
        return author_materialx(stage,node.get('materialX') or {},files,procedural_subgraph=sub)
    selected,stats=materialx_rule_selection(stage,rules)
    for index,rule in enumerate(rules):
        targets=[path for path,owner in selected.items() if owner==index]
        if targets:
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
    for rule in rules:materialx_settings(rule['material'],files)
    _,rows=materialx_rule_selection(stage,rules)
    return {'rows':rows,'note':'Last matching row wins. Instance-proxy descendants are skipped; target their instance root.'}


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


def apply_light_overrides(stage, overrides):
    from pxr import Gf, Sdf, Usd, UsdLux
    if not isinstance(overrides, list):
        return
    for item in overrides:
        if not isinstance(item, dict):
            continue
        path_str = item.get('lightPath', '').strip()
        if not path_str:
            continue

        prim = stage.GetPrimAtPath(Sdf.Path(path_str))
        if not prim or not prim.IsValid():
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

        if not prim or not prim.IsValid():
            target_path = path_str if path_str.startswith('/') else ('/Scene/' + path_str)
            sdf_path = Sdf.Path(target_path)
            light_type = item.get('lightType')
            if not light_type:
                lower = target_path.lower()
                if 'dome' in lower or 'env' in lower or 'ambient' in lower:
                    light_type = 'DomeLight'
                elif 'sphere' in lower or 'point' in lower:
                    light_type = 'SphereLight'
                else:
                    light_type = 'DistantLight'
            define_cls = getattr(UsdLux, light_type, UsdLux.DistantLight)
            light_schema = define_cls.Define(stage, sdf_path)
            prim = light_schema.GetPrim()

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
    from pxr import Usd, UsdLux
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

            lights.append({
                'path': str(prim.GetPath()),
                'name': prim.GetName(),
                'type': type_name,
                'intensity': float(intensity),
                'exposure': float(exposure),
                'color': color,
                'enableColorTemperature': temp_enabled,
                'colorTemperature': temp,
            })
    return {'lights': lights}


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
            pts = mesh.GetPointsAttr().Get()
            meshes.append({
                'path': str(prim.GetPath()),
                'name': prim.GetName(),
                'vertexCount': int(len(pts)) if pts else 0,
                'instanceProxy': bool(prim.IsInstanceProxy()),
            })
    return {'curves': curves, 'meshes': meshes}


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

        n_curves = len(counts)
        total_verts = sum(int(c) for c in counts)
        n_orig = len(orig_widths) if orig_widths is not None else 0
        new_widths = []
        v_offset = 0
        var_offset = 0
        if width_mode == 'multiply' and n_orig > 0 and interp == UsdGeom.Tokens.varying and n_orig != total_verts:
            # Estimate per-curve varying sample count when BasisCurves uses segment-based varying widths.
            approx_per_curve = n_orig / float(max(n_curves, 1))
        else:
            approx_per_curve = 0.0

        for c_idx, raw_c in enumerate(counts):
            n_pts = max(0, int(raw_c))
            if width_mode == 'multiply' and n_orig > 0 and interp == UsdGeom.Tokens.varying and approx_per_curve > 0:
                c_var_count = max(1, int(round(approx_per_curve)))
            else:
                c_var_count = n_pts

            for i in range(n_pts):
                t = float(i) / float(n_pts - 1) if n_pts > 1 else 0.0
                factor = root_scale * (1.0 - t) + (1.0 - taper) * t
                if width_mode == 'set':
                    base_w = width
                else:
                    if n_orig == 0:
                        base_w = 1.0
                    elif interp == UsdGeom.Tokens.constant or n_orig == 1:
                        base_w = float(orig_widths[0])
                    elif interp == UsdGeom.Tokens.uniform and c_idx < n_orig:
                        base_w = float(orig_widths[c_idx])
                    elif interp == UsdGeom.Tokens.vertex and (v_offset + i) < n_orig:
                        base_w = float(orig_widths[v_offset + i])
                    elif interp == UsdGeom.Tokens.varying:
                        if n_orig == total_verts and (v_offset + i) < n_orig:
                            base_w = float(orig_widths[v_offset + i])
                        else:
                            local_idx = min(c_var_count - 1, int(round(t * max(0, c_var_count - 1))))
                            src_idx = min(n_orig - 1, var_offset + local_idx)
                            base_w = float(orig_widths[src_idx])
                    else:
                        base_w = float(orig_widths[min(v_offset + i, n_orig - 1)])
                    base_w *= width
                new_widths.append(max(0.0, float(base_w * factor)))
            v_offset += n_pts
            var_offset += c_var_count

        curves.CreateWidthsAttr().Set(Vt.FloatArray(new_widths))
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
    key = hashlib.sha256(f'{norm_path}|{st.st_mtime_ns}|{st.st_size}'.encode('utf-8')).hexdigest()[:24]
    cache_dir = os.path.join(tempfile.gettempdir(), 'sublayer_alembic_cache')
    os.makedirs(cache_dir, exist_ok=True)
    cache_usdc = os.path.join(cache_dir, f'{key}.usdc')

    if reload or not os.path.isfile(cache_usdc):
        tmp_usdc = os.path.join(cache_dir, f'{key}.{os.getpid()}.tmp.usdc')
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
        finally:
            if os.path.exists(tmp_usdc):
                try:
                    os.remove(tmp_usdc)
                except OSError:
                    pass

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


def _open_source_stage(path, reload=False):
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
    source = Usd.Stage.Open(path)
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
