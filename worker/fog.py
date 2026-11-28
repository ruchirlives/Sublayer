"""Non-destructive density-volume shading and standalone RTX atmospheric haze."""
import hashlib
import math

DEFAULTS = dict(enabled=True, atmosphereEnabled=True, volumesEnabled=True,
                color=[.75, .75, .75], intensity=1., distanceDensity=.1,
                startDistance=0., endDistance=100., height=0., heightDensity=.1, heightFalloff=1.,
                volumePaths=[], volumeAutoBounds=True, volumeCenter=[0., 0., 0.], volumeSize=[2., 2., 2.], densityScale=1., volumeColor=[.9, .9, .9], anisotropy=0.)


def settings(raw=None):
    from pxr import Sdf
    if raw is not None and not isinstance(raw, dict):
        raise ValueError('Fog settings must be an object')
    c = dict(DEFAULTS, **(raw or {}))
    for key in ('enabled', 'atmosphereEnabled', 'volumesEnabled', 'volumeAutoBounds'):
        if not isinstance(c[key], bool):
            raise ValueError('Fog enable flags must be booleans')
    for key in ('intensity', 'distanceDensity', 'startDistance', 'endDistance', 'heightDensity', 'heightFalloff', 'densityScale', 'height', 'anisotropy'):
        v = c[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > 1e12:
            raise ValueError('Fog numbers must be finite and bounded')
        if key not in ('height', 'anisotropy') and v < 0:
            raise ValueError('Fog density and distances must be nonnegative')
    if c['endDistance'] <= c['startDistance'] or abs(c['anisotropy']) > .99:
        raise ValueError('Fog end distance must exceed start; anisotropy must be between -0.99 and 0.99')
    for key in ('color', 'volumeColor'):
        if not isinstance(c[key], (list, tuple)) or len(c[key]) != 3 or any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or not 0 <= v <= 1 for v in c[key]):
            raise ValueError('Fog colors must be three finite linear values between zero and one')
    for key in ('volumeCenter', 'volumeSize'):
        if not isinstance(c[key], (list, tuple)) or len(c[key]) != 3 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > 1e12 or (key == 'volumeSize' and v <= 0) for v in c[key]):
            raise ValueError('Fog volume bounds require finite centers and positive sizes')
    paths = c['volumePaths']
    if not isinstance(paths, list) or len(paths) > 10000:
        raise ValueError('Invalid Fog volume paths')
    for p in paths:
        if not isinstance(p, str) or not Sdf.Path.IsValidPathString(p) or not Sdf.Path(p).IsAbsolutePath() or not Sdf.Path(p).IsPrimPath() or p == '/':
            raise ValueError('Fog targets must be absolute volume prim paths')
    return c


def active_nodes(graph):
    if not isinstance(graph, dict):
        return []
    nodes = {n['id']: n for n in graph.get('nodes', [])}
    incoming = {}
    for e in graph.get('edges', []):
        incoming.setdefault(e['target'], []).append(e['source'])
    seen, ordered = set(), []
    def visit(nid):
        if nid in seen or nid not in nodes:
            return
        seen.add(nid)
        for source in incoming.get(nid, []):
            visit(source)
        if nodes[nid].get('operation') == 'fog':
            ordered.append(nodes[nid])
    visit(graph.get('targetNodeId'))
    return ordered


def atmosphere(graph):
    result = None
    for node in active_nodes(graph):
        c = settings(node.get('fog'))
        if c['enabled']:
            result = c
    return result


def apply_atmosphere(product, cfg, up_axis, volumes_enabled=None):
    """Names/types verified against the standalone SDK's rtx_settings schema."""
    from pxr import Gf, Sdf
    c = settings(cfg)
    specs = {
        'enabled': (Sdf.ValueTypeNames.Bool, cfg is not None and c['enabled'] and c['atmosphereEnabled']),
        'color': (Sdf.ValueTypeNames.Color3f, Gf.Vec3f(*c['color'])),
        'colorIntensity': (Sdf.ValueTypeNames.Float, c['intensity']),
        'distanceDensity': (Sdf.ValueTypeNames.Float, c['distanceDensity']),
        'start:distance': (Sdf.ValueTypeNames.Float, c['startDistance']),
        'endDist': (Sdf.ValueTypeNames.Float, c['endDistance']),
        'start:height': (Sdf.ValueTypeNames.Float, c['height']),
        'height:density': (Sdf.ValueTypeNames.Float, c['heightDensity']),
        'height:falloff': (Sdf.ValueTypeNames.Float, c['heightFalloff']),
        'zUp:enabled': (Sdf.ValueTypeNames.Bool, str(up_axis).upper() == 'Z'),
    }
    for suffix, (kind, value) in specs.items():
        product.CreateAttribute('omni:rtx:fog:' + suffix, kind, custom=False).Set(value)
    render_volumes = volumes_enabled if volumes_enabled is not None else (cfg is not None and c['enabled'] and c['volumesEnabled'])
    if render_volumes:
        product.CreateAttribute('omni:rtx:pt:ptvol:enabled', Sdf.ValueTypeNames.Bool, custom=False).Set(True)


def apply_volumes(stage, node):
    from pxr import Gf, Sdf, Usd, UsdShade, UsdVol
    c = settings(node.get('fog'))
    if not c['enabled'] or not c['volumesEnabled']:
        return
    if c['volumePaths']:
        volumes = [stage.GetPrimAtPath(p) for p in dict.fromkeys(c['volumePaths'])]
    else:
        volumes = [p for p in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()) if p.IsA(UsdVol.Volume)]
    # Validate all targets before authoring any opinions. Never de-instance assets.
    targets = []
    for prim in volumes:
        if not prim or not prim.IsA(UsdVol.Volume):
            raise ValueError('Fog target must be an existing USD Volume')
        if prim.IsInstanceProxy():
            raise ValueError(f'Fog cannot shade instance-proxy volume {prim.GetPath()}; connect an editable non-instanced volume input')
        volume = UsdVol.Volume(prim)
        field_path = volume.GetFieldPath('density')
        field = UsdVol.OpenVDBAsset(stage.GetPrimAtPath(field_path)) if field_path else None
        if not field or field.GetFieldNameAttr().Get() != 'density':
            raise ValueError(f'Fog requires an OpenVDB density field on {prim.GetPath()}; level sets and other grids are not density fog')
        attr = field.GetFilePathAttr()
        if attr.GetNumTimeSamples():
            raise ValueError('Fog VDB shading currently requires a static density field')
        asset = attr.Get()
        if not asset or not (asset.resolvedPath or asset.path):
            raise ValueError('Fog density field has no VDB file')
        # Resolve relative paths in the field layer, including referenced stages.
        path = asset.resolvedPath
        if not path:
            for spec in attr.GetPropertyStack():
                if spec.HasDefaultValue():
                    path = Sdf.ComputeAssetPathRelativeToLayer(spec.layer, asset.path)
                    break
        targets.append((prim, path or asset.path))
    for prim, path in targets:
        ident = hashlib.sha256((node['id'] + str(prim.GetPath())).encode()).hexdigest()[:20]
        root = stage.GetDefaultPrim().GetPath()
        mat_path = root.AppendChild('__Fog_' + ident)
        if stage.GetPrimAtPath(mat_path):
            raise ValueError(f'Fog material namespace collision: {mat_path}')
        material = UsdShade.Material.Define(stage, mat_path)
        shader = UsdShade.Shader.Define(stage, mat_path.AppendChild('Shader'))
        shader.SetSourceAsset(Sdf.AssetPath('Volume/OmniVolumeDensity.mdl'), 'mdl')
        shader.SetSourceAssetSubIdentifier('OmniVolumeDensity', 'mdl')
        shader.CreateInput('volume_density_texture', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(path))
        shader.CreateInput('volume_density_scale', Sdf.ValueTypeNames.Float).Set(c['densityScale'])
        shader.CreateInput('volume_albedo', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*c['volumeColor']))
        shader.CreateInput('directional_bias', Sdf.ValueTypeNames.Float).Set(c['anisotropy'])
        output = shader.CreateOutput('out', Sdf.ValueTypeNames.Token)
        material.CreateVolumeOutput('mdl').ConnectToSource(output)
        material.CreateSurfaceOutput('mdl').ConnectToSource(output)
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(material, UsdShade.Tokens.strongerThanDescendants)
        prim.CreateAttribute('sublayer:fog:autoBounds', Sdf.ValueTypeNames.Bool).Set(c['volumeAutoBounds'])
        prim.CreateAttribute('sublayer:fog:boundaryCenter', Sdf.ValueTypeNames.Float3).Set(Gf.Vec3f(*c['volumeCenter']))
        prim.CreateAttribute('sublayer:fog:boundarySize', Sdf.ValueTypeNames.Float3).Set(Gf.Vec3f(*c['volumeSize']))


def warnings(graph, renderer):
    configs = [settings(n.get('fog')) for n in active_nodes(graph)]
    if not any(c['enabled'] for c in configs):
        return []
    if renderer != 'OmniverseRTX':
        return ['Fog atmosphere and MDL VDB shading require NVIDIA RTX; this renderer does not preview Fog effects.']
    if any(c['enabled'] and c['volumesEnabled'] for c in configs):
        return ['Fog VDB shading requires RTX Path Tracing and a static density grid.']
    return []


def prepare_rtx_preview(stage, mdl_library, frame=0):
    """Resolve our portable SDK module in a private preview overlay only."""
    from pxr import Sdf, Usd
    attrs = [p.GetAttribute('info:mdl:sourceAsset') for p in stage.Traverse()]
    attrs = [a for a in attrs if a and a.Get() == Sdf.AssetPath('Volume/OmniVolumeDensity.mdl')]
    if not attrs:
        return stage, []
    module = mdl_library / 'Volume' / 'OmniVolumeDensity.mdl'
    if not module.is_file():
        raise ValueError('RTX runtime is missing Volume/OmniVolumeDensity.mdl required by Fog')
    overlay = Sdf.Layer.CreateAnonymous('fog-rtx-preview.usda')
    overlay.subLayerPaths = [stage.GetRootLayer().identifier]
    preview = Usd.Stage.Open(overlay)
    preview.SetDefaultPrim(preview.GetPrimAtPath(stage.GetDefaultPrim().GetPath()))
    for attr in attrs:
        preview.GetAttributeAtPath(attr.GetPath()).Set(Sdf.AssetPath(module.as_posix()))
    from pxr import Gf, UsdGeom, UsdShade, UsdVol
    for prim in list(preview.Traverse()):
        if not prim.IsA(UsdVol.Volume) or prim.IsInstanceProxy():
            continue
        mat, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
        if not mat or not str(mat.GetPath().name).startswith('__Fog_'):
            continue
        if UsdGeom.Imageable(prim).ComputeVisibility(Usd.TimeCode(frame)) == UsdGeom.Tokens.invisible:
            continue
        center = prim.GetAttribute('sublayer:fog:boundaryCenter').Get()
        size = prim.GetAttribute('sublayer:fog:boundarySize').Get()
        if prim.GetAttribute('sublayer:fog:autoBounds').Get():
            from vdb_metadata import density_bounds
            shader = UsdShade.Shader(preview.GetPrimAtPath(mat.GetPath().AppendChild('Shader')))
            asset = shader.GetInput('volume_density_texture').Get()
            center, size = density_bounds(asset.resolvedPath or asset.path)
        if center is None or size is None:
            continue
        boundary_path = '/__FogRTX_' + hashlib.sha256(str(prim.GetPath()).encode()).hexdigest()[:20]
        if preview.GetPrimAtPath(boundary_path):
            raise ValueError('Fog preview boundary would overwrite an existing prim')
        cube = UsdGeom.Mesh.Define(preview, boundary_path)
        points = [Gf.Vec3f(*[center[i] + sign[i] * size[i] * .5 for i in range(3)]) for sign in ((-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),(-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1))]
        cube.CreatePointsAttr(points)
        cube.CreateExtentAttr([Gf.Vec3f(*[center[i]-size[i]*.5 for i in range(3)]), Gf.Vec3f(*[center[i]+size[i]*.5 for i in range(3)])])
        cube.CreateFaceVertexCountsAttr([4]*6)
        cube.CreateFaceVertexIndicesAttr([0,3,2,1,4,5,6,7,0,1,5,4,1,2,6,5,2,3,7,6,3,0,4,7])
        cube.CreateSubdivisionSchemeAttr('none')
        cube.AddTransformOp().Set(UsdGeom.XformCache(Usd.TimeCode(frame)).GetLocalToWorldTransform(prim))
        cube.GetPrim().CreateAttribute('primvars:isVolume', Sdf.ValueTypeNames.Bool).Set(True)
        cube.GetPrim().CreateAttribute('sublayer:fog:sourceVolume', Sdf.ValueTypeNames.String).Set(str(prim.GetPath()))
        UsdShade.MaterialBindingAPI.Apply(cube.GetPrim()).Bind(mat)
    return preview, [stage, overlay]


def sync_preview_visibility(stage, frame=0):
    from pxr import Usd, UsdGeom
    for prim in stage.GetPseudoRoot().GetChildren():
        source = prim.GetAttribute('sublayer:fog:sourceVolume')
        if source and source.Get():
            volume = stage.GetPrimAtPath(source.Get())
            visibility = UsdGeom.Imageable(volume).ComputeVisibility(Usd.TimeCode(frame)) if volume else UsdGeom.Tokens.invisible
            UsdGeom.Imageable(prim).CreateVisibilityAttr().Set(visibility)


def source_pick(stage, path):
    prim = stage.GetPrimAtPath(path) if path and stage else None
    source = prim.GetAttribute('sublayer:fog:sourceVolume') if prim else None
    return source.Get() if source and source.Get() else path
