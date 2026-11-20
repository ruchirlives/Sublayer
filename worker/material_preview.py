"""Shared read-only diagnostics and private selective material fallbacks."""
import os


def iter_scene_and_prototype_prims(stage):
    """Yield non-instance-proxy prims on the stage plus each prototype's prims once."""
    from pxr import Usd
    for prim in stage.Traverse():
        yield prim
    for proto in stage.GetPrototypes():
        for prim in Usd.PrimRange(proto):
            yield prim


def missing_shader_assets(stage, frame, mdl_search_paths=()):
    """Audit composed shader input assets without changing layers or guessing paths."""
    from pathlib import PurePosixPath
    from pxr import Ar, Sdf, Usd, UsdShade
    missing = {}
    with Ar.ResolverContextBinder(stage.GetPathResolverContext()):
        for prim in iter_scene_and_prototype_prims(stage):
            if not prim.IsA(UsdShade.Shader):
                continue
            for attr in prim.GetAttributes():
                if not (attr.GetName().startswith('inputs:') or (attr.GetName().startswith('info:') and attr.GetName().endswith('sourceAsset'))):
                    continue
                if attr.GetTypeName() not in (Sdf.ValueTypeNames.Asset, Sdf.ValueTypeNames.AssetArray):
                    continue
                value = attr.Get(Usd.TimeCode(frame))
                assets = [value] if isinstance(value, Sdf.AssetPath) else value or []
                for asset in assets:
                    if not asset.path or asset.resolvedPath or (os.path.isabs(asset.path) and os.path.isfile(asset.path)):
                        continue
                    if mdl_search_paths and attr.GetName() == 'info:mdl:sourceAsset':
                        module = PurePosixPath(asset.path)
                        if (module.as_posix() == asset.path and module.suffix == '.mdl' and
                            not module.is_absolute() and '..' not in module.parts and ':' not in asset.path and
                            any((folder / module).is_file() for folder in mdl_search_paths)):
                            continue
                    if UsdShade.UdimUtils.IsUdimIdentifier(asset.path):
                        # Resolve tile patterns relative to the layer that authored the value.
                        stack = attr.GetPropertyStack(Usd.TimeCode(frame))
                        layer = next((spec.layer for spec in stack if spec.HasInfo('default') or spec.HasInfo('timeSamples')), stage.GetRootLayer())
                        if UsdShade.UdimUtils.ResolveUdimTilePaths(asset.path, layer):
                            continue
                    missing.setdefault(asset.path, []).append(str(attr.GetPath()))
    return [{'path': path, 'usedBy': properties} for path, properties in sorted(missing.items())]


def invalid_shader_inputs(stage,frame):
    """Known Storm-invalid primvar readers; never guess another UV set."""
    from pxr import Usd,UsdShade
    issues=[]
    for prim in iter_scene_and_prototype_prims(stage):
        if not prim.IsA(UsdShade.Shader):continue
        shader=UsdShade.Shader(prim);identifier=shader.GetIdAttr().Get() or ''
        if not identifier.startswith('UsdPrimvarReader_'):continue
        attr=prim.GetAttribute('inputs:varname')
        if not attr or attr.GetConnections():continue
        name=attr.Get(Usd.TimeCode(frame))
        if name is None or str(name) in ('','_'):
            issues.append({'path':str(attr.GetPath()),'reason':'UV/primvar reader uses an empty or Storm-invalid name: '+repr(name)})
    if not issues:return issues
    connected=set()
    def visit(attr):
        path=str(attr.GetPath())
        if path in connected:return
        connected.add(path)
        for target in attr.GetConnections():
            other=stage.GetAttributeAtPath(target)
            if other:visit(other)
        prim=attr.GetPrim()
        if attr.GetName().startswith('outputs:') and prim.IsA(UsdShade.Shader):
            for other in prim.GetAttributes():
                if other.GetName().startswith('inputs:'):visit(other)
    for prim in iter_scene_and_prototype_prims(stage):
        if prim.IsA(UsdShade.Material):
            for output in UsdShade.Material(prim).GetOutputs():visit(output.GetAttr())
    return [issue for issue in issues if issue['path'] in connected]


def shader_depends_on(stage, attr, broken, seen):
    from pxr import UsdShade
    path = str(attr.GetPath())
    if path in seen:
        return False
    if path in broken:
        return True
    seen.add(path)
    for connection in attr.GetConnections():
        source = stage.GetAttributeAtPath(connection)
        if source and shader_depends_on(stage, source, broken, seen):
            return True
    prim = attr.GetPrim()
    if attr.GetName().startswith('outputs:') and prim.IsA(UsdShade.Shader):
        return any(
            shader_depends_on(stage, item, broken, seen)
            for item in prim.GetAttributes()
            if item.GetName().startswith('inputs:') or
                (item.GetName().startswith('info:') and item.GetName().endswith('sourceAsset'))
        )
    return False


def preserve_mdl_preview(stage, missing, invalid):
    """Disconnect broken alternate outputs on a private flattened NVIDIA stage."""
    from pxr import Sdf, Usd, UsdShade
    layer = stage.GetRootLayer()
    if not layer.anonymous:
        raise ValueError('MDL preview requires a private flattened stage')
    broken_assets = {path for item in missing for path in item['usedBy']}
    broken = broken_assets | {item['path'] for item in invalid}
    disconnected = {}
    for prim in iter_scene_and_prototype_prims(stage):
        if not prim.IsA(UsdShade.Material):
            continue
        material = UsdShade.Material(prim)
        mdl = material.GetSurfaceOutput('mdl')
        if not mdl or not mdl.HasConnectedSource() or shader_depends_on(stage, mdl.GetAttr(), broken, set()):
            continue
        for output in material.GetOutputs():
            attr = output.GetAttr()
            if shader_depends_on(stage, attr, broken, set()):
                for spec in attr.GetPropertyStack():
                    if spec.layer == layer:
                        disconnected[spec.path] = spec
    for spec in disconnected.values():
        spec.connectionPathList.explicitItems = []
    if disconnected:
        # NVIDIA population can resolve assets even on disconnected shaders.
        # Keep the original diagnostics for fallback selection and the UI.
        blocked = {}
        for path in broken_assets:
            attr = stage.GetAttributeAtPath(path)
            if attr:
                for spec in attr.GetPropertyStack():
                    if spec.layer == layer:
                        blocked[spec.path] = spec
        for spec in blocked.values():
            spec.default = Sdf.ValueBlock()
            for time in layer.ListTimeSamplesForPath(spec.path):
                layer.EraseTimeSample(spec.path, time)


def selective_material_preview(stage,missing,frame,invalid=None):
    """Private material-network replacements; preserve geometry, bindings and instances."""
    from pxr import Usd,UsdShade,Sdf,UsdUtils,Gf,Sdr
    broken={prop for item in missing for prop in item['usedBy']}|{item['path'] for item in invalid or []}
    affected=[]
    for prim in iter_scene_and_prototype_prims(stage):
        if prim.IsA(UsdShade.Material) and any(shader_depends_on(stage, output.GetAttr(),broken,set()) for output in UsdShade.Material(prim).GetOutputs()):affected.append(prim)
    if not affected:return stage,[],[]
    # One replacement template per render context set, not one geometry copy
    # per binding/face/instance. Copy only affected source layers and ancestors,
    # memoizing each layer to retain shared reference/prototype identity.
    layers={l.identifier:l for l in stage.GetUsedLayers()};pending=list(layers.values())
    while pending:
        owner=pending.pop()
        for asset in owner.GetCompositionAssetDependencies():
            if not asset:
                continue
            child=Sdf.Layer.FindOrOpen(Sdf.ComputeAssetPathRelativeToLayer(owner,asset))
            if child and child.identifier not in layers:layers[child.identifier]=child;pending.append(child)
    edits={}
    for prim in affected:
        spec=next((spec for spec in prim.GetPrimStack() if spec.layer.identifier in layers),None)
        if spec is None:raise ValueError('Unable to locate material definition for preview: '+str(prim.GetPath()))
        edits.setdefault(spec.layer.identifier,{})[str(spec.path)]=[a.GetFullName() for a in UsdShade.Material(prim).GetOutputs()]
    parents={}
    for identifier,owner in layers.items():
        for asset in owner.GetCompositionAssetDependencies():
            if not asset:
                continue
            child=Sdf.Layer.Find(Sdf.ComputeAssetPathRelativeToLayer(owner,asset))
            if child:parents.setdefault(child.identifier,set()).add(identifier)
    changed=set(edits);pending=list(changed)
    while pending:
        for parent in parents.get(pending.pop(),[]):
            if parent not in changed:changed.add(parent);pending.append(parent)
    root=stage.GetRootLayer();changed.add(root.identifier)
    clones={key:Sdf.Layer.CreateAnonymous('auto-material-preview.usda') for key in changed}
    keepalive=[]
    for key,clone in clones.items():
        owner=layers[key];clone.TransferContent(owner)
        def anchor(asset):
            if not asset:
                return asset
            anchored=Sdf.ComputeAssetPathRelativeToLayer(owner,asset);child=Sdf.Layer.Find(anchored)
            return clones[child.identifier].identifier if child and child.identifier in clones else anchored
        UsdUtils.ModifyAssetPaths(clone,anchor)
        for path,outputs in edits.get(key,{}).items():
            template=Usd.Stage.CreateInMemory();mat=UsdShade.Material.Define(template,'/Fallback')
            shader=UsdShade.Shader.Define(template,'/Fallback/Solid');shader.CreateIdAttr('UsdPreviewSurface');shader.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(.55));shader.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(.7);shader.CreateInput('opacity',Sdf.ValueTypeNames.Float).Set(1)
            shader.CreateOutput('surface',Sdf.ValueTypeNames.Token)
            mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(),'surface')
            for name in outputs:
                if name.endswith(':surface'):
                    context=name[len('outputs:'):-len(':surface')] if name!='outputs:surface' else ''
                    target=shader;port='surface'
                    if context=='mtlx' and Sdr.Registry().GetShaderNodeByIdentifier('ND_standard_surface_surfaceshader'):
                        target=UsdShade.Shader.Define(template,'/Fallback/SolidMaterialX');target.CreateIdAttr('ND_standard_surface_surfaceshader');target.CreateInput('base_color',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(.55));target.CreateInput('specular_roughness',Sdf.ValueTypeNames.Float).Set(.7);target.CreateOutput('out',Sdf.ValueTypeNames.Token);port='out'
                    mat.CreateSurfaceOutput(context).ConnectToSource(target.ConnectableAPI(),port)
                else:mat.GetPrim().CreateAttribute(name,Sdf.ValueTypeNames.Token).SetConnections([])
            Sdf.CopySpec(template.GetRootLayer(),'/Fallback',clone,Sdf.Path(path))
            keepalive.append(template)
    session=stage.GetSessionLayer();session=clones.get(session.identifier,session)
    result=Usd.Stage.Open(clones[root.identifier],session,stage.GetPathResolverContext())
    if result is None:raise ValueError('Unable to compose selective material preview')
    keepalive.extend([*clones.values(),result])
    affected_paths = []
    protos = list(stage.GetPrototypes())
    for p in affected:
        if p.IsInPrototype():
            owner_proto = next((pr for pr in protos if p.GetPath().HasPrefix(pr.GetPath())), None)
            if owner_proto is not None:
                rel = p.GetPath().MakeRelativePath(owner_proto.GetPath())
                for inst in owner_proto.GetInstances():
                    affected_paths.append(str(inst.GetPath().AppendPath(rel)))
                continue
        affected_paths.append(str(p.GetPath()))
    return result,keepalive,affected_paths

