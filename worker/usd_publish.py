"""Read-only three-way USD review and explicit sparse-layer publication."""
import hashlib
import json
import math
import os
from pathlib import Path

if 'inherit_stage_metrics' not in globals():
    from usd_graph import inherit_stage_metrics, prepend_root_transform

if 'open_geometry_export' not in globals():
    from geometry_exports import open_geometry_export, export_format


def file_digest(path):
    digest=hashlib.sha256()
    with open(path,'rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): digest.update(block)
    return digest.hexdigest()


def validate_revisions(records):
    for record in records:
        try:
            metadata=os.stat(record['path'])
            current=(metadata.st_size,str(metadata.st_mtime_ns))
        except OSError: current=(None,None)
        if current!=(record['size'],record['modifiedNs']):
            raise ValueError('Preserved input changed since native preparation; reconciliation required: '+record['path'])


def close_value(a,b):
    if a is None or b is None: return a is b
    if type(a)!=type(b): return False
    if type(a).__module__.startswith(('pxr.Vt','pxr.Gf')) and a==b: return True
    if isinstance(a,(float,int)):
        return math.isclose(a,b,rel_tol=1e-7,abs_tol=1e-8)
    if isinstance(a,(str,bool)): return a==b
    if isinstance(a,dict):return a.keys()==b.keys() and all(close_value(a[k],b[k]) for k in a)
    try:
        if len(a)!=len(b): return False
        return all(close_value(x,y) for x,y in zip(a,b))
    except TypeError: return a==b


def property_category(prim,name):
    from pxr import UsdShade,UsdSkel,UsdGeom
    if name.startswith('xformOp:') or name=='xformOpOrder': return 'transforms'
    if prim.IsA(UsdShade.Material) or prim.IsA(UsdShade.Shader) or prim.IsA(UsdShade.NodeGraph) or name.startswith('material:binding') or prim.IsA(UsdGeom.Subset): return 'materials'
    if prim.IsA(UsdSkel.Animation) or name=='skel:animationSource': return 'animation'
    if (prim.IsA(UsdSkel.Skeleton) or prim.IsA(UsdSkel.BlendShape)) or name.startswith(('primvars:skel:','skel:')): return 'skeletons'
    if name.startswith(('primvars:st','primvars:uv')): return 'uvs'
    if name in {'faceVertexCounts','faceVertexIndices','holeIndices','subdivisionScheme','interpolateBoundary','faceVaryingLinearInterpolation','triangleSubdivisionRule','creaseIndices','creaseLengths','creaseSharpnesses','cornerIndices','cornerSharpnesses','curveVertexCounts','basis','type','wrap'}: return 'topology'
    if name in {'points','normals','velocities','accelerations','extent','widths'}: return 'points'
    return None


def finite_value(value):
    if isinstance(value,(float,int)):return math.isfinite(value)
    if isinstance(value,(str,bytes)) or value is None:return True
    if isinstance(value,dict):return all(finite_value(v) for v in value.values())
    if hasattr(value,'GetReal') and hasattr(value,'GetImaginary'):return finite_value(value.GetReal()) and finite_value(value.GetImaginary())
    try:return all(finite_value(v) for v in value)
    except TypeError:return True


def attribute_data_changed(a,b):
    if bool(a)!=bool(b): return True
    if not a: return False
    if a.GetTypeName()!=b.GetTypeName() or a.GetTimeSamples()!=b.GetTimeSamples(): return True
    from pxr import Usd
    return any(not close_value(a.Get(Usd.TimeCode.Default() if t is None else t),b.Get(Usd.TimeCode.Default() if t is None else t)) for t in [None,*a.GetTimeSamples()])


def attribute_changed(a,b):
    return attribute_data_changed(a,b) or (a and b and (a.GetConnections()!=b.GetConnections() or not close_value(a.GetAllAuthoredMetadata(),b.GetAllAuthoredMetadata())))


def checked_inverse(matrix):
    from pxr import Gf
    if any(not math.isfinite(v) for row in matrix for v in row):raise ValueError('Non-finite coordinate/transform matrix')
    inverse=matrix.GetInverse();product=matrix*inverse
    if any(not math.isfinite(product[i][j]) or abs(product[i][j]-(1 if i==j else 0))>1e-6 for i in range(4) for j in range(4)):raise ValueError('Singular/ill-conditioned transform; explicit reconciliation required')
    return inverse


def manual_selection(stage,source,allow_new=False,mapping=None,edited=False):
    from pxr import UsdGeom,UsdLux,Usd
    known={identity(p) for p in source.Traverse(Usd.TraverseInstanceProxies()) if identity(p)}
    selected=[]
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if not (prim.IsA(UsdGeom.Gprim) or prim.HasAPI(UsdLux.LightAPI)):continue
        key=identity(prim)
        explicit=next((m for m in (mapping or []) if m.get('editedPath' if edited else 'baselinePath')==str(prim.GetPath())),None)
        if explicit:
            original=source.GetPrimAtPath(explicit.get('inputPath',''))
            if not original or original.GetTypeName()!=prim.GetTypeName() or not identity(original):raise ValueError('Explicit baseline correspondence has incompatible/missing input: '+str(prim.GetPath()))
            if key and key!=identity(original):raise ValueError('Explicit mapping conflicts with exported identity')
            key=identity(original)
        if (not key or key not in known) and allow_new:continue
        if not key or key not in known:
            raise ValueError('Selected export contains geometry/light without a matching OrchFlow ID: '+str(prim.GetPath()))
        selected.append((key,prim.GetTypeName()))
    if not selected:raise ValueError('Baseline selection has no identifiable geometry or lights')
    if len(set(selected))!=len(selected):raise ValueError('Selection has ambiguous duplicate geometry identities')
    return sorted(selected)


def fresh_publication_stages(paths,conversion=None):
    """Refresh the process-wide Sdf registry after external saves/replacements."""
    from pxr import Sdf,Usd
    layers=[]
    converted=[]
    for path in paths:
        kind=export_format(path)
        converted.append(open_geometry_export(path,conversion) if kind!='usd' else None)
        if converted[-1] is not None:
            layers.append(converted[-1].GetRootLayer());continue
        layer=Sdf.Layer.FindOrOpen(path)
        if not layer:raise ValueError('Unable to open USD: '+str(path))
        if not layer.Reload(True):raise ValueError('Unable to reload USD from disk: '+str(path))
        layers.append(layer)
    stages=[converted[i] if converted[i] is not None else Usd.Stage.Open(layer) for i,layer in enumerate(layers)]
    refreshed={layer.identifier for layer in layers}
    for stage in stages:
        if not stage:raise ValueError('Unable to open publication USD stage')
        for layer in stage.GetUsedLayers():
            if layer.anonymous or layer.identifier in refreshed:continue
            if not layer.Reload(True):raise ValueError('Unable to reload referenced USD: '+layer.identifier)
            refreshed.add(layer.identifier)
    return stages


def review_correspondence(request):
    mappings={m['baselinePath']:m for m in (request.get('baselineCorrespondence') or [])}
    mappings.update({m['baselinePath']:m for m in request.get('correspondence',[])})
    return list(mappings.values())


def publication_context(request):
    from pxr import Usd,UsdGeom
    digest=globals().get('graph_digest')
    if digest is None:
        from usd_graph import graph_digest as digest
    graph_revision=digest(request['currentGraph'])
    if not request.get('liveReview') and graph_revision!=request['conversion']['graphRevision']:
        raise ValueError('Upstream graph changed since native preparation; reconcile explicitly or prepare a new round trip. Native edits were preserved.')
    if not request.get('liveReview'):validate_revisions(request['sourceRevisions'])
    if file_digest(request['baseline'])!=request['baselineDigest']: raise ValueError('Round-trip baseline was modified; recreate native preparation')
    kind=export_format(request['baseline'])
    if kind!=export_format(request['edited']):raise ValueError('Baseline and edited export must use the same format (USD, OBJ or Alembic)')
    if kind!='usd' and not request.get('manualBaseline'):raise ValueError('OBJ/Alembic require an explicitly mapped manual baseline')
    stages=fresh_publication_stages([request[key] for key in ['handoff','baseline','edited']],request['conversion'])
    if not all(stages): raise ValueError('Unable to open handoff, baseline or edited export')
    source,baseline,edited=stages
    if source.GetRootLayer().customLayerData.get('orchflowNamespaceVersion')!=2:raise ValueError('This handoff uses the previous scene hierarchy. Create a new handoff and capture a matching baseline before reviewing edits. Existing files were preserved.')
    source_identity=stable_custom_string(source.GetDefaultPrim(),'userProperties:orchflowRoundTripId') if source.GetDefaultPrim() else None
    if request.get('manualBaseline'):
        # Validate baseline ownership here. Edited paths can change when an
        # exporter derives its root from the filename; surface these as review
        # conflicts with inventory so the user can explicitly map the edited path.
        for entry in review_correspondence(request):
            old=baseline.GetPrimAtPath(entry.get('baselinePath',''))
            original=source.GetPrimAtPath(entry.get('inputPath',''))
            if not old or not original or original.GetTypeName()!=old.GetTypeName() or not identity(original):
                raise ValueError('Baseline correspondence needs remapping: '+entry.get('baselinePath','')+'. Confirm its counterpart in the current handoff; no name-based match was assumed.')
        manual_selection(baseline,source,mapping=review_correspondence(request))
    else:
        for name,stage in [('baseline',baseline),('edited export',edited)]:
            if not source_identity or not any(stable_custom_string(p,'userProperties:orchflowRoundTripId')==source_identity for p in stage.Traverse(Usd.TraverseInstanceProxies())):
                raise ValueError(name+' has no matching round-trip identity. Export the complete prepared scene with custom properties enabled.')

    if not source.GetDefaultPrim(): raise ValueError('Preserved handoff needs a default prim')
    if UsdGeom.GetStageUpAxis(baseline)!=UsdGeom.GetStageUpAxis(edited) or not math.isclose(UsdGeom.GetStageMetersPerUnit(baseline),UsdGeom.GetStageMetersPerUnit(edited),rel_tol=1e-10) or baseline.GetTimeCodesPerSecond()!=edited.GetTimeCodesPerSecond():
        raise ValueError('Export axis, units or time rate differs from the baseline. Use the recorded exporter settings.')
    allowed=set(request['policy']['editScope'])
    for scope in allowed:
        if request['policy']['profile']['capabilities'].get(scope,{}).get('status')!='supported': raise ValueError('Unsupported scope: '+scope)
    if kind!='usd':allowed &= {'points','topology','uvs','transforms','animation'}
    return source,baseline,edited,allowed


def prim_index(stage):
    from pxr import Usd
    return {str(p.GetPath()):p for p in stage.Traverse(Usd.TraverseInstanceProxies())}


def stable_custom_string(prim,name):
    attr=prim.GetAttribute(name)
    if not attr:return None
    values=[v for v in [attr.Get(),*[attr.Get(t) for t in attr.GetTimeSamples()]] if v is not None]
    return values[0] if values and isinstance(values[0],str) and all(v==values[0] for v in values) else None


def identity(prim):
    return stable_custom_string(prim,'userProperties:orchflowId')


def build_mapping(source,baseline,edited,explicit):
    from pxr import Sdf
    originals,base,candidates=map(prim_index,(source,baseline,edited))
    by_id={}
    for path,prim in originals.items():
        key=identity(prim)
        if key: by_id.setdefault(key,[]).append(path)
    edited_ids={}
    for path,prim in candidates.items():
        key=identity(prim)
        if key: edited_ids.setdefault(key,[]).append(path)
    mappings={}
    for path,prim in base.items():
        matches=by_id.get(identity(prim),[]) if identity(prim) else []
        target=matches[0] if len(matches)==1 else path if path in originals and originals[path].GetTypeName()==prim.GetTypeName() else None
        edits=edited_ids.get(identity(prim),[]) if identity(prim) else []
        candidate=edits[0] if len(edits)==1 else path
        if target: mappings[path]={'baselinePath':path,'inputPath':target,'editedPath':candidate,'method':'identity' if matches else 'exact path'}
    for entry in explicit:
        path=entry['baselinePath']
        if path not in base or entry['inputPath'] not in originals: raise ValueError('Correspondence names a missing baseline/input prim')
        target=originals[entry['inputPath']]
        if target.GetTypeName()!=base[path].GetTypeName():
            from pxr import UsdGeom
            if not (target.IsA(UsdGeom.Xformable) and base[path].IsA(UsdGeom.Xformable)): raise ValueError('Correspondence prim types are incompatible')
        mappings[path]={**entry,'method':'explicit'}
    groups={}
    for path,entry in mappings.items():groups.setdefault(entry['inputPath'],[]).append(path)
    from pxr import UsdGeom
    for paths in groups.values():
        if len(paths)<2:continue
        roles=['xform' if base[p].IsA(UsdGeom.Xform) else 'geometry' if base[p].IsA(UsdGeom.Gprim) else base[p].GetTypeName() for p in paths]
        if len(paths)==2 and len(set(roles))==2 and 'xform' in roles:
            parent=paths[roles.index('xform')];child=paths[1-roles.index('xform')]
            if Sdf.Path(child).HasPrefix(Sdf.Path(parent)):continue
        deliberate=[p for p in paths if mappings[p]['method']=='explicit']
        if len(deliberate)>1:raise ValueError('Multiple explicit correspondences target the same input prim')
        for path in paths:
            if not deliberate or path!=deliberate[0]:del mappings[path]
    return originals,base,candidates,mappings


def share_identical_edits(output,source,report):
    """Share equal edited instance subtrees without copying unchanged geometry."""
    from pxr import Sdf,UsdGeom,Usd
    layer=output.GetRootLayer();groups={}
    paths=[Sdf.Path(p) for p in set(report['deinstanced'])]
    leaves=[p for p in paths if not any(q!=p and q.HasPrefix(p) for q in paths)]
    for path in leaves:
        original=source.GetPrimAtPath(path);current=output.GetPrimAtPath(path)
        if not original or not original.IsInstance() or not current.IsA(UsdGeom.Xform) or current.GetAppliedSchemas():continue
        if any(r.GetTargets() for r in current.GetRelationships()):continue
        spec=layer.GetPrimAtPath(path)
        if not spec:continue
        if any(attr.HasAuthoredValueOpinion() and not attr.GetName().startswith('xformOp:') and attr.GetName() not in {'xformOpOrder','purpose','visibility','userProperties:orchflowId','userProperties:orchflowRoundTripId'} for attr in current.GetAttributes()):continue
        external_links=[]
        def check_links(spec_path):
            obj=layer.GetObjectAtPath(spec_path)
            if isinstance(obj,Sdf.RelationshipSpec) or (isinstance(obj,Sdf.AttributeSpec) and obj.connectionPathList.GetAppliedItems()):external_links.append(spec_path)
        layer.Traverse(path,check_links)
        if external_links:continue

        reference_spec=next((s for s in original.GetPrimStack() if not s.layer.anonymous and s.referenceList.GetAppliedItems() and not s.path.ContainsPrimVariantSelection()),None)
        if not reference_spec:continue
        temporary=Sdf.Layer.CreateAnonymous();Sdf.CopySpec(layer,path,temporary,Sdf.Path('/Asset'))
        signature=(str(original.GetPrototype().GetPath()),temporary.ExportToString())
        groups.setdefault(signature,[]).append((path,reference_spec))
    shared=0
    for entries in groups.values():
        if len(entries)<2:continue
        container=Sdf.Path('/_OrchFlowEditedPrototypes')
        while source.GetPrimAtPath(container):container=Sdf.Path(str(container)+'_')
        parent=Sdf.CreatePrimInLayer(layer,container);parent.specifier=Sdf.SpecifierClass
        prototype=container.AppendChild('P_'+str(shared))
        exemplar,reference_spec=entries[0]
        Sdf.CopySpec(layer,exemplar,layer,prototype)
        layer.GetPrimAtPath(prototype).specifier=Sdf.SpecifierDef
        proto=output.GetPrimAtPath(prototype)
        proto.GetReferences().SetReferences([Sdf.Reference(reference_spec.layer.identifier,reference_spec.path)])
        proto.SetInstanceable(False)
        passthrough={};authored_roots={}
        for path,_ in entries:
            authored=set(layer.GetPrimAtPath(path).attributes.keys());authored_roots[str(path)]=json.dumps(sorted(authored))
            old=output.GetPrimAtPath(path)
            passthrough[str(path)]=[attr.GetName() for attr in old.GetAttributes() if attr.GetName() not in authored]
            attributes=[]
            for attr in old.GetAttributes():
                values=[(Usd.TimeCode.Default(),attr.Get())]+[(Usd.TimeCode(t),attr.Get(t)) for t in attr.GetTimeSamples()]
                attributes.append((attr.GetName(),attr.GetTypeName(),attr.IsCustom(),attr.GetVariability(),values))
            output.RemovePrim(path)
            instance=output.OverridePrim(path)
            instance.GetReferences().SetReferences([Sdf.Reference('',prototype)])
            instance.SetInstanceable(True)
            for name,kind,custom,variability,values in attributes:
                attr=instance.CreateAttribute(name,kind,custom,variability)
                for time,value in values:
                    if value is not None:attr.Set(value,time)
        shared+=1
        metadata=dict(layer.customLayerData)
        origins=dict(metadata.get('orchflowPrototypeSources',{}));origins[str(prototype)]=str(exemplar)
        metadata['orchflowPrototypeSources']=origins
        forwarding=dict(metadata.get('orchflowInstancePassthrough',{}));forwarding.update({path:json.dumps(names) for path,names in passthrough.items()})
        metadata['orchflowInstancePassthrough']=forwarding
        edited_roots=dict(metadata.get('orchflowInstanceEdits',{}));edited_roots.update(authored_roots);metadata['orchflowInstanceEdits']=edited_roots
        layer.customLayerData=metadata
    report['sharedEditPrototypes']=shared


def prune_redundant_properties(output,source):
    """Remove redundant sparse opinions, never flatten/copy upstream data.

    USD attributes (including point arrays) are atomic opinions. Compare full
    values/sample sets and metadata; equal individual vertices are not sparsely
    addressable by an ordinary USD points attribute.
    """
    from pxr import Sdf,Usd
    layer=output.GetRootLayer();removed=0
    def equal_data(a,b):
        return a.GetTypeName()==b.GetTypeName() and a.GetTimeSamples()==b.GetTimeSamples() and all(a.Get(t)==b.Get(t) for t in [Usd.TimeCode.Default(),*[Usd.TimeCode(t) for t in a.GetTimeSamples()]])
    paths=[]
    layer.Traverse(Sdf.Path.absoluteRootPath,lambda path:paths.append(path) if path.IsPropertyPath() else None)
    for path in paths:
        old=source.GetPrimAtPath(path.GetPrimPath());current=output.GetPrimAtPath(path.GetPrimPath())
        if not old or not current:continue
        name=path.name
        a,b=old.GetAttribute(name),current.GetAttribute(name)
        if a and b:
            same=equal_data(a,b) and a.GetConnections()==b.GetConnections() and a.GetAllAuthoredMetadata()==b.GetAllAuthoredMetadata()
        else:
            a,b=old.GetRelationship(name),current.GetRelationship(name)
            same=bool(a and b and a.GetTargets()==b.GetTargets() and a.GetAllAuthoredMetadata()==b.GetAllAuthoredMetadata())
        if same and current.RemoveProperty(name):
            removed+=1;continue
        # Keep only differing fields on a changed property. For example, an
        # edited shader connection need not freeze an identical texture value
        # or interpolation metadata from the incoming layer.
        spec=layer.GetPropertyAtPath(path)
        if not spec or not a or not b:continue
        for key in list(spec.ListInfoKeys()):
            if key in {'typeName','custom','variability'}:continue
            if key in {'default','timeSamples'}:
                equal=isinstance(spec,Sdf.AttributeSpec) and equal_data(a,b)
            elif key=='connectionPaths':equal=a.GetConnections()==b.GetConnections()
            elif key=='targetPaths':equal=a.GetTargets()==b.GetTargets()
            else:equal=a.GetMetadata(key)==b.GetMetadata(key)
            if equal:
                if key=='connectionPaths':spec.connectionPathList.ClearEdits()
                elif key=='targetPaths':spec.targetPathList.ClearEdits()
                else:spec.ClearInfo(key)
                removed+=1
    return removed


def verified_translation_alignment(old,incoming):
    from pxr import Gf
    if not old or len(old)!=len(incoming):return None
    offset=Gf.Vec3d(0)
    for a,b in zip(old,incoming):offset+=Gf.Vec3d(b)-Gf.Vec3d(a)
    offset/=len(old)
    magnitude=max((abs(c) for points in (old,incoming) for p in points for c in p),default=0)
    tolerance=max(1e-12,8*(2**-23)*magnitude)
    if any(not math.isfinite(a[i]+offset[i]) or abs(a[i]+offset[i]-b[i])>tolerance for a,b in zip(old,incoming) for i in range(3)):return None
    return Gf.Matrix4d().SetTranslate(offset)


def review_publication(request,return_layers=False):
    from pxr import Gf,Sdf,Usd,UsdGeom,UsdShade,UsdSkel,Vt
    edited_digest=file_digest(request['edited'])
    source,baseline,edited,allowed=publication_context(request)
    originals,base,candidates,mappings=build_mapping(source,baseline,edited,review_correspondence(request))
    if max(map(len,(originals,base,candidates)))>100000: raise ValueError('Publication exceeds 100,000 prim review limit')
    layer=Sdf.Layer.CreateAnonymous('edits.usda')
    layer.customLayerData={'orchflowNamespaceVersion':2}
    layer.subLayerPaths=[source.GetRootLayer().identifier]
    output=Usd.Stage.Open(layer)
    inherit_stage_metrics(output,source)
    root=output.GetPrimAtPath(source.GetDefaultPrim().GetPath());output.SetDefaultPrim(root)
    new_networks=[]
    if 'materials' in allowed:
        container=source.GetDefaultPrim().GetPath().AppendChild('_OrchFlowMaterials')
        while source.GetPrimAtPath(container):container=container.GetParentPath().AppendChild(container.name+'_')
        for path,prim in candidates.items():
            if prim.IsA(UsdShade.Material) and path not in base and not any(m['editedPath']==path for m in mappings.values()):
                new_networks.append({'editedPath':path,'inputPath':str(container.AppendChild('M_'+hashlib.sha256(path.encode()).hexdigest()[:16]))})

    report={'applied':[],'preserved':[],'conflicts':[],'deinstanced':[]}
    K=Gf.Matrix4d(1)
    ratio=UsdGeom.GetStageMetersPerUnit(baseline)/UsdGeom.GetStageMetersPerUnit(source)
    K.SetScale(ratio)
    if UsdGeom.GetStageUpAxis(baseline)!=UsdGeom.GetStageUpAxis(source):
        rotation=Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(1,0,0),90 if UsdGeom.GetStageUpAxis(baseline)=='Y' else -90))
        K=K*rotation
    def item(bucket,path,prop,reason):
        if sum(len(report[x]) for x in ['applied','preserved','conflicts'])>=20000: raise ValueError('Publication report exceeds review limit; split the workflow')
        report[bucket].append({'prim':path,'property':prop,'reason':reason})
    if request.get('manualBaseline'):
        from pxr import UsdLux
        owners={}
        for path,prim in base.items():
            if not (prim.IsA(UsdGeom.Gprim) or prim.HasAPI(UsdLux.LightAPI)):continue
            entry=mappings.get(path)
            ep=candidates.get(entry['editedPath']) if entry else None
            if not entry or ep is None:
                item('conflicts',path,'correspondence',"Choose this baseline object's edited-export path in Prim correspondence; the exporter may have renamed the root")
                continue
            key=entry['editedPath']
            if key in owners and owners[key]!=path:item('conflicts',path,'correspondence','Two baseline objects map to one edited object')
            owners[key]=path
            original=originals.get(entry['inputPath'])
            if ep.GetTypeName()!=prim.GetTypeName() or (identity(ep) and original and identity(ep)!=identity(original)):
                item('conflicts',path,'correspondence','Edited object schema or identity does not match the chosen input')
    writes={}
    def claim(path,field,owner):
        key=(str(path),field)
        if key in writes and writes[key]!=owner:raise ValueError('Multiple baseline prims modify the same input property; resolve correspondence: '+str(path)+'.'+field)
        writes[key]=owner
    def editable(path):
        original=source.GetPrimAtPath(path)
        if original and any(spec.path.ContainsPrimVariantSelection() for spec in original.GetPrimStack()):raise ValueError('Variant-contributed edits require explicit variant-aware reconciliation')
        prim=output.GetPrimAtPath(path)
        while prim and prim.IsInstanceProxy():
            parent=prim.GetParent()
            while parent and (not parent.IsInstance() or parent.IsInstanceProxy()): parent=parent.GetParent()
            if not parent: raise ValueError('Could not locate instance owner')
            output.OverridePrim(parent.GetPath()).SetInstanceable(False)
            report['deinstanced'].append(str(parent.GetPath()));prim=output.GetPrimAtPath(path)
        return output.OverridePrim(path)
    def remap(path):
        for entry in sorted([*mappings.values(),*new_networks],key=lambda m:len(m['editedPath']),reverse=True):
            prefix=Sdf.Path(entry['editedPath'])
            if path.HasPrefix(prefix): return path.ReplacePrefix(prefix,Sdf.Path(entry['inputPath']))
        if source.GetPrimAtPath(path.GetPrimPath()): return path
        raise ValueError('Unmapped relationship/connection target: '+str(path))
    def timecode(t):return Usd.TimeCode.Default() if t is None else Usd.TimeCode(t)
    def source_time(t):return None if t is None else t*source.GetTimeCodesPerSecond()/baseline.GetTimeCodesPerSecond()
    def frame_map(bp,ip,t):
        b=UsdGeom.XformCache(timecode(t)).GetLocalToWorldTransform(bp)
        i=UsdGeom.XformCache(timecode(source_time(t))).GetLocalToWorldTransform(ip)
        return b*K*checked_inverse(i)
    def point_frame(bp,ip,t,entry):
        values=entry.get('pointTransform')
        if values is None:return frame_map(bp,ip,t)
        if not isinstance(values,list) or len(values)!=4 or any(not isinstance(row,list) or len(row)!=4 for row in values):raise ValueError('Invalid point-coordinate alignment')
        matrix=Gf.Matrix4d(*[v for row in values for v in row]);checked_inverse(matrix)
        expected=verified_translation_alignment(bp.GetAttribute('points').Get(timecode(t)),ip.GetAttribute('points').Get(timecode(source_time(t))))
        if expected is None or not close_value(expected,matrix):raise ValueError('Approved coordinate alignment no longer matches the baseline/input vertices')
        for field in ('faceVertexCounts','faceVertexIndices'):
            if bp.GetAttribute(field) and ip.GetAttribute(field) and bp.GetAttribute(field).Get()!=ip.GetAttribute(field).Get():raise ValueError('Coordinate alignment cannot repair changed vertex topology')
        return matrix
    def asset_value(attr,value,t):
        stack=attr.GetPropertyStack(timecode(t))
        owner=next((spec.layer for spec in stack if spec.HasInfo('default') or spec.HasInfo('timeSamples')),edited.GetRootLayer())
        if isinstance(value,Sdf.AssetPath):return Sdf.AssetPath(Sdf.ComputeAssetPathRelativeToLayer(owner,value.path))
        if isinstance(value,Sdf.AssetPathArray):return Sdf.AssetPathArray([Sdf.AssetPath(Sdf.ComputeAssetPathRelativeToLayer(owner,v.path)) for v in value])
        return value
    new_subsets=[]
    if 'materials' in allowed:
        for path,prim in candidates.items():
            if not prim.IsA(UsdGeom.Subset) or path in base:continue
            parent=str(prim.GetParent().GetPath())
            entry=next((m for m in mappings.values() if m['editedPath']==parent),None)
            if not entry:
                item('conflicts',path,'face subset','New face assignment requires mapped input mesh');continue
            original=originals[entry['inputPath']];old_parent=base[entry['baselinePath']]
            if not original.IsA(UsdGeom.Mesh) or any(not close_value(original.GetAttribute(name).Get(),old_parent.GetAttribute(name).Get()) or not close_value(old_parent.GetAttribute(name).Get(),prim.GetParent().GetAttribute(name).Get()) for name in ['faceVertexCounts','faceVertexIndices']):
                item('conflicts',path,'face subset','Face correspondence differs; explicit face remapping required');continue
            target=original.GetPath().AppendChild(prim.GetName())
            while source.GetPrimAtPath(target):target=target.GetParentPath().AppendChild(target.name+'_')
            new_subsets.append({'editedPath':path,'inputPath':str(target)})
    new_networks.extend(new_subsets)
    def copy_metadata(old,new,target,relationship=False):
        excluded={'typeName','custom','variability','default','timeSamples','connectionPaths','targetPaths'}
        keys=set(old.GetAllAuthoredMetadata() if old else {})|set(new.GetAllAuthoredMetadata())
        for key in keys-excluded:
            value=new.GetMetadata(key)
            if value is None:
                if relationship and key=='bindMaterialAs':value=UsdShade.Tokens.weakerThanDescendants
                elif old and old.HasAuthoredMetadata(key):raise ValueError('Metadata removal needs explicit reconciliation: '+key)
                else:continue
            target.SetMetadata(key,value)
    for network in new_networks:
        for path,prim in candidates.items():
            if not prim.GetPath().HasPrefix(Sdf.Path(network['editedPath'])):continue
            try:
                target=output.DefinePrim(remap(prim.GetPath()),prim.GetTypeName())
                if prim.IsA(UsdGeom.Subset):UsdShade.MaterialBindingAPI.Apply(target)
                for attr in prim.GetAttributes():
                    if attr.GetTimeSamples() and 'animation' not in allowed and any(not close_value(attr.Get(t),attr.Get(attr.GetTimeSamples()[0])) for t in attr.GetTimeSamples()):raise ValueError('New animated material requires animation scope')
                    result_attr=target.CreateAttribute(attr.GetName(),attr.GetTypeName(),attr.IsCustom(),attr.GetVariability())
                    for t in [None,*attr.GetTimeSamples()]:
                        value=attr.Get(timecode(t))
                        if value is not None:result_attr.Set(asset_value(attr,value,t),timecode(source_time(t)))
                    result_attr.SetConnections([remap(p) for p in attr.GetConnections()]);copy_metadata(None,attr,result_attr)
                for relation in prim.GetRelationships():
                    rel=target.CreateRelationship(relation.GetName(),relation.IsCustom());rel.SetTargets([remap(p) for p in relation.GetTargets()]);copy_metadata(None,relation,rel,True)
                item('applied',path,'material network','New explicitly permitted material network/face assignment')
            except Exception as error:item('conflicts',path,'material network',str(error))
    deleted_roots=[]
    for path,bp in base.items():
        entry=mappings.get(path); ep=candidates.get(entry['editedPath'] if entry else path)
        if entry and any(Sdf.Path(entry['inputPath']).HasPrefix(root) for root in deleted_roots):continue
        if ep is None:
            item('preserved',path,'prim','Absent from application export; use a Delete node to remove upstream prims');continue
        ip=originals.get(entry['inputPath']) if entry else None
        if bp.GetTypeName()!=ep.GetTypeName():
            item('conflicts' if allowed else 'preserved',path,'prim type','Export prim schema changed; explicit scene reconciliation is required');continue
        bm,em=bp.GetAllAuthoredMetadata(),ep.GetAllAuthoredMetadata()
        for key in set(bm)|set(em):
            if key not in {'typeName','specifier','active'} and not close_value(bm.get(key),em.get(key)):item('preserved',path,'metadata:'+key,'Prim metadata/composition change outside the edit-property scope')
        changed_names=[]
        for name in set(bp.GetPropertyNames())|set(ep.GetPropertyNames()):
            if name.startswith('userProperties:'): continue
            ba,ea=bp.GetAttribute(name),ep.GetAttribute(name)
            br,er=bp.GetRelationship(name),ep.GetRelationship(name)
            changed=attribute_changed(ba,ea) if ba or ea else bool(br)!=bool(er) or (br and er and (br.GetTargets()!=er.GetTargets() or not close_value(br.GetAllAuthoredMetadata(),er.GetAllAuthoredMetadata())))
            if changed: changed_names.append(name)
        xchanged=any(name.startswith('xformOp:') or name=='xformOpOrder' for name in changed_names)
        if xchanged:
            bx,ex=UsdGeom.Xformable(bp),UsdGeom.Xformable(ep)
            if bx and ex and bx.GetResetXformStack()==ex.GetResetXformStack() and bx.GetTimeSamples()==ex.GetTimeSamples() and all(close_value(bx.GetLocalTransformation(timecode(t)),ex.GetLocalTransformation(timecode(t))) for t in [None,*bx.GetTimeSamples()]):xchanged=False
        if xchanged:
            if 'transforms' not in allowed:item('preserved',path,'xform','Transforms outside scope')
            elif not ip:item('conflicts',path,'xform','Map this baseline prim to an input prim')
            else:
                try:
                    bx,ex,ix=UsdGeom.Xformable(bp),UsdGeom.Xformable(ep),UsdGeom.Xformable(ip)
                    if bx.GetResetXformStack()!=ex.GetResetXformStack():raise ValueError('Reset-stack/reparent changes require explicit hierarchy reconciliation')
                    bt,et=bx.GetTimeSamples(),ex.GetTimeSamples()
                    codes=sorted(set(bt+et)) or [None]
                    right=[checked_inverse(bx.GetLocalTransformation(timecode(t)))*ex.GetLocalTransformation(timecode(t)) for t in codes]
                    left=[ex.GetLocalTransformation(timecode(t))*checked_inverse(bx.GetLocalTransformation(timecode(t))) for t in codes]
                    right_static=all(close_value(value,right[0]) for value in right);left_static=all(close_value(value,left[0]) for value in left)
                    local_mode=left_static and not right_static
                    if not (right_static or left_static) and 'animation' not in allowed:raise ValueError('Time-varying transform delta requires animation scope')
                    claim(entry['inputPath'],'xform',path)
                    out=UsdGeom.Xformable(editable(entry['inputPath']))
                    op_name='xformOp:transform:orchflowEdit'
                    while ip.GetAttribute(op_name):op_name+='_' 
                    attr=out.GetPrim().CreateAttribute(op_name,Sdf.ValueTypeNames.Matrix4d)
                    original_order=[str(op) for op in (ix.GetXformOpOrderAttr().Get() or []) if str(op)!='!resetXformStack!']
                    order=original_order+[op_name] if local_mode else [op_name]+original_order
                    out.GetXformOpOrderAttr().Set((['!resetXformStack!'] if ix.GetResetXformStack() else [])+order)
                    matrix_pair=all(len(form.GetOrderedXformOps())==1 and form.GetOrderedXformOps()[0].GetOpType()==UsdGeom.XformOp.TypeTransform for form in [bx,ex])
                    def delta_at(t):
                        bmat,emat=bx.GetLocalTransformation(timecode(t)),ex.GetLocalTransformation(timecode(t))
                        delta=right[0] if t is not None and matrix_pair and right_static and not local_mode else emat*checked_inverse(bmat) if local_mode else checked_inverse(bmat)*emat
                        mapping=frame_map(bp,ip,t) if local_mode else frame_map(baseline.GetPseudoRoot() if bx.GetResetXformStack() else bp.GetParent(),source.GetPseudoRoot() if ix.GetResetXformStack() else ip.GetParent(),t)
                        value=checked_inverse(mapping)*delta*mapping
                        if not finite_value(value):raise ValueError('Non-finite transform delta')
                        return value
                    times=set(bt+et+[t*baseline.GetTimeCodesPerSecond()/source.GetTimeCodesPerSecond() for t in ix.GetTimeSamples()])
                    for parent,rate in [(bp.GetParent(),1),(ip.GetParent(),baseline.GetTimeCodesPerSecond()/source.GetTimeCodesPerSecond())]:
                        while parent and not parent.IsPseudoRoot():
                            form=UsdGeom.Xformable(parent)
                            if form:times.update(t*rate for t in form.GetTimeSamples())
                            parent=parent.GetParent()
                    if len(times)>4096:raise ValueError('Transform delta exceeds the 4096-sample publication budget')
                    values={t:delta_at(t) for t in sorted(times)}
                    def refine(start,end,depth=0):
                        if end-start<1e-8:return
                        mid=(start+end)/2;actual=delta_at(mid);linear=(values[start]+values[end])*.5
                        if close_value(actual,linear):return
                        if depth>=12 or len(values)>=4096:raise ValueError('Transform delta cannot be interpolated safely within sample budget')
                        values[mid]=actual;refine(start,mid,depth+1);refine(mid,end,depth+1)
                    initial=sorted(values)
                    for start,end in zip(initial,initial[1:]):refine(start,end)
                    attr.Set(delta_at(None))
                    for t,matrix in sorted(values.items()):attr.Set(matrix,timecode(source_time(t)))
                    item('applied',path,'xform','Relative transform delta; original xform operations and interpolation retained')
                except Exception as error:item('conflicts',path,'xform',str(error))
        topology_changed=any(property_category(bp,n)=='topology' for n in changed_names)
        point_count_changed=bool(bp.GetAttribute('points') and ep.GetAttribute('points') and len(bp.GetAttribute('points').Get() or [])!=len(ep.GetAttribute('points').Get() or []))
        unsafe_topology=None
        if topology_changed or point_count_changed:
            if not {'points','topology'}.issubset(allowed):unsafe_topology='Topology changes require both geometry points and topology scope'
            elif ip:
                if any(n.startswith('primvars:st') or n.startswith('primvars:uv') for n in ip.GetPropertyNames()) and 'uvs' not in allowed:unsafe_topology='Topology would invalidate preserved UVs'
                if any(child.IsA(UsdGeom.Subset) for child in ip.GetChildren()):unsafe_topology='Topology change requires explicit remapping of retained face subsets'
                if (ip.GetRelationship('skel:skeleton') or ip.HasAPI(UsdSkel.BindingAPI)):unsafe_topology='Topology changes on bound geometry require explicit skin/UV remapping; automatic rig remapping is unsafe'
        for name in changed_names:
            category=property_category(bp,name) or property_category(ep,name)
            if category=='transforms':continue
            if not category or category not in allowed:
                item('preserved',path,name,'Outside node scope or unsupported USD property');continue
            if not ip:item('conflicts',path,name,'Map this baseline prim to an input prim');continue
            try:
                if name=='extent' and 'points' in changed_names:
                    item('preserved',path,name,'Extent derived from resulting input-relative points');continue
                if unsafe_topology and category in {'points','topology','uvs','skeletons'}:raise ValueError(unsafe_topology)
                claim(entry['inputPath'],name,path)
                ba,ea=bp.GetAttribute(name),ep.GetAttribute(name)
                if ba or ea:
                    samples=sorted(set((ba.GetTimeSamples() if ba else [])+(ea.GetTimeSamples() if ea else [])))
                    value_changed=attribute_data_changed(ba,ea)
                    if samples and value_changed and 'animation' not in allowed:
                        if not ea or not ba:raise ValueError('Removing/adding sampled properties requires animation scope')
                        if category=='points' and name=='points':
                            if ba.GetTimeSamples()!=ea.GetTimeSamples():
                                raise ValueError('Point sample times differ from baseline; export the recorded animation range or explicitly enable verified animation edits')
                            first_delta=None
                            for t in samples:
                                old,new=ba.Get(t),ea.Get(t)
                                if old is None or new is None or len(old)!=len(new):raise ValueError('Animated point correspondence differs')
                                delta=[Gf.Vec3d(new[i]-old[i]) for i in range(len(new))]
                                if first_delta is None:first_delta=delta
                                elif not close_value(delta,first_delta):raise ValueError('Time-varying point edits require animation scope')
                        else:
                            for attr in (ba,ea):
                                first_value=attr.Get(samples[0])
                                if any(not close_value(attr.Get(t),first_value) for t in samples[1:]):
                                    raise ValueError('Time-sampled edits require animation scope')
                            if ip.GetAttribute(name) and ip.GetAttribute(name).GetTimeSamples():
                                raise ValueError('Time-sampled edits require animation scope')
                    if not ea:
                        target=editable(entry['inputPath']).CreateAttribute(name,ba.GetTypeName(),ba.IsCustom(),ba.GetVariability())
                        target.Block();target.SetConnections([])
                        item('applied',path,name,'Explicit allowed attribute removal');continue
                    if category=='skeletons' and bp.IsA(UsdSkel.Skeleton) and (not close_value(bp.GetAttribute('joints').Get(),ip.GetAttribute('joints').Get()) or not close_value(bp.GetAttribute('joints').Get(),ep.GetAttribute('joints').Get())):raise ValueError('Joint hierarchy/order changed; explicit skinning correspondence is required')
                    if category=='skeletons' and not close_value(frame_map(bp,ip,None),Gf.Matrix4d(1)):raise ValueError('Skeleton bind-space differs; explicit rig correspondence required')
                    if category=='points' and name=='points' and not ip.IsA(UsdGeom.PointBased):raise ValueError('Points must map to an input PointBased prim')
                    if category=='skeletons' and bp.GetTypeName()!=ip.GetTypeName():raise ValueError('Skeleton properties require matching schema types')
                    if category=='points' and name in {'extent','widths'} and not close_value(frame_map(bp,ip,None),Gf.Matrix4d(1)):raise ValueError('Extent/width edits require matching local frames')
                    if category=='skeletons' and name.startswith(('skel:','primvars:skel:')):UsdSkel.BindingAPI.Apply(editable(entry['inputPath']))
                    target=editable(entry['inputPath']).CreateAttribute(name,ea.GetTypeName(),ea.IsCustom(),ea.GetVariability())
                    write_samples=samples
                    if name=='points' and ip.GetAttribute(name):write_samples=sorted(set(samples+[t*baseline.GetTimeCodesPerSecond()/source.GetTimeCodesPerSecond() for t in ip.GetAttribute(name).GetTimeSamples()]))
                    for t in ([None,*write_samples] if value_changed else []):
                        value=ea.Get(timecode(t))
                        if value is None:continue
                        if not finite_value(value):raise ValueError('Non-finite edited attribute value')
                        if category=='points' and name=='points':
                            old=ba.Get(timecode(t)) if ba else None;incoming=ip.GetAttribute(name).Get(timecode(source_time(t))) if ip.GetAttribute(name) else None
                            A=point_frame(bp,ip,t,entry)
                            if not topology_changed and not point_count_changed and old is not None and incoming is not None:
                                if ip.IsA(UsdGeom.Mesh) and bp.IsA(UsdGeom.Mesh) and (not close_value(ip.GetAttribute('faceVertexCounts').Get(),bp.GetAttribute('faceVertexCounts').Get()) or not close_value(ip.GetAttribute('faceVertexIndices').Get(),bp.GetAttribute('faceVertexIndices').Get())):raise ValueError('Baseline topology does not correspond to input; vertex correspondence is ambiguous')
                                if len(value)!=len(old) or len(old)!=len(incoming):raise ValueError('Point correspondence differs from baseline/input')
                                mapped=[A.Transform(Gf.Vec3d(point)) for point in old]
                                # Float32 points and exported transforms accumulate rounding,
                                # particularly around zero. Bound correspondence error by the
                                # mesh coordinate magnitude rather than a fixed scalar epsilon.
                                magnitude=max((abs(c) for points in (incoming,mapped) for point in points for c in point),default=0.0)
                                tolerance=max(1e-12,8*(2**-23)*magnitude)
                                if any(abs(mapped[i][axis]-incoming[i][axis])>tolerance for i in range(len(old)) for axis in range(3)):
                                    alignment=verified_translation_alignment(old,incoming) if t is None and not ba.GetTimeSamples() else None
                                    if alignment is not None:
                                        item('conflicts',path,name,'The unedited baseline uses a different local origin. Confirm the verified translation alignment, then analyze again')
                                        report['conflicts'][-1]['pointTransform']=[list(row) for row in alignment]
                                        continue
                                    raise ValueError('Baseline vertices do not correspond to preserved input; explicit vertex remapping is required')
                                value=Vt.Vec3fArray([Gf.Vec3f(Gf.Vec3d(incoming[i])+A.TransformDir(Gf.Vec3d(value[i]-old[i]))) for i in range(len(value))])
                            else:value=Vt.Vec3fArray([Gf.Vec3f(A.Transform(Gf.Vec3d(point))) for point in value])
                        if category=='points' and name in {'normals','velocities','accelerations'}:
                            A=point_frame(bp,ip,t,entry);transform=A.GetInverse().GetTranspose() if name=='normals' else A
                            mapped=[transform.TransformDir(Gf.Vec3d(v)) for v in value]
                            if name=='normals':mapped=[v.GetNormalized() for v in mapped]
                            value=Vt.Vec3fArray([Gf.Vec3f(v) for v in mapped])
                        value=asset_value(ea,value,t)
                        target.Set(value,timecode(source_time(t)))
                        if name=='points' and len(value):
                            extent=Gf.Range3d()
                            for point in value:extent.UnionWith(Gf.Vec3d(point))
                            editable(entry['inputPath']).CreateAttribute('extent',Sdf.ValueTypeNames.Float3Array).Set(Vt.Vec3fArray([Gf.Vec3f(extent.GetMin()),Gf.Vec3f(extent.GetMax())]),timecode(source_time(t)))
                    copy_metadata(ba,ea,target)
                    if value_changed or ba.GetConnections()!=ea.GetConnections():target.SetConnections([remap(p) for p in ea.GetConnections()])
                else:
                    relation=ep.GetRelationship(name)
                    if name.startswith('material:binding'):UsdShade.MaterialBindingAPI.Apply(editable(entry['inputPath']))
                    rel=editable(entry['inputPath']).CreateRelationship(name,relation.IsCustom() if relation else br.IsCustom());rel.SetTargets([remap(p) for p in relation.GetTargets()] if relation else [])
                    if relation:copy_metadata(br,relation,rel,True)
                item('applied',path,name,'Permitted baseline-relative edit')
            except Exception as error:item('conflicts',path,name,str(error))
    accepted_additions=[]
    selected_additions=request.get('newPrimPaths',[])
    if not isinstance(selected_additions,list) or len(selected_additions)>100:raise ValueError('Too many new prim roots')
    for value in selected_additions:
        try:
            path=Sdf.Path(value)
            if not path.IsAbsolutePath() or not path.IsPrimPath():raise ValueError('Addition needs an absolute prim path')
            ep=edited.GetPrimAtPath(path)
            if not ep or value in {m['editedPath'] for m in mappings.values()}:raise ValueError('Choose a genuinely new prim')
            if any(path.HasPrefix(Sdf.Path(p)) or Sdf.Path(p).HasPrefix(path) for p in accepted_additions):raise ValueError('Addition roots overlap')
            existing_ids={identity(p) for p in originals.values() if identity(p)}
            for prim in Usd.PrimRange(ep,Usd.TraverseInstanceProxies()):
                if identity(prim) in existing_ids:raise ValueError('Addition contains an existing input object; map it instead')
                if prim.GetTypeName() not in ('Xform','Scope','Mesh'):raise ValueError('New prim schema is not implemented for selective additions: '+prim.GetTypeName())
                required={'transforms'} if prim.IsA(UsdGeom.Xformable) else set()
                if prim.IsA(UsdGeom.Mesh):
                    required.update(['points','topology'])
                    mesh=UsdGeom.Mesh(prim);pts=mesh.GetPointsAttr().Get() or [];counts=mesh.GetFaceVertexCountsAttr().Get() or [];indices=mesh.GetFaceVertexIndicesAttr().Get() or []
                    if not pts or sum(counts)!=len(indices) or any(c<0 for c in counts) or any(i<0 or i>=len(pts) for i in indices) or not finite_value(pts):raise ValueError('New mesh topology or point values are invalid')
                if not required.issubset(allowed):raise ValueError('New prim requires allowed scopes: '+', '.join(sorted(required)))
                for attr in prim.GetAttributes():
                    category=property_category(prim,attr.GetName())
                    if attr.GetTimeSamples() and 'animation' not in allowed:raise ValueError('New animated prim requires animation scope')
                    if category and category not in allowed and attr.HasAuthoredValueOpinion():raise ValueError('New prim property outside allowed scope: '+attr.GetName())
                    if attr.GetConnections():raise ValueError('New shader-connected prim needs explicit material reconciliation')
                for rel in prim.GetRelationships():
                    if rel.GetTargets():raise ValueError('New prim relationships need explicit reconciliation')
            parent=ep.GetParent()
            while parent and not parent.IsPseudoRoot():
                form=UsdGeom.Xformable(parent)
                if form and form.GetTimeSamples():raise ValueError('Animated addition parents require explicit reconciliation')
                parent=parent.GetParent()
            container=source.GetDefaultPrim().GetPath().AppendChild('OrchFlowAdded_'+hashlib.sha256(value.encode()).hexdigest()[:16])
            if source.GetPrimAtPath(container):raise ValueError('New prim destination already exists')
            wrapper=UsdGeom.Xform.Define(output,container)
            matrix=UsdGeom.XformCache().GetLocalToWorldTransform(ep.GetParent())*K*checked_inverse(UsdGeom.XformCache().GetLocalToWorldTransform(source.GetDefaultPrim()))
            wrapper.AddTransformOp().Set(matrix)
            output.OverridePrim(container.AppendChild('Content')).GetReferences().AddReference(request['edited'],path)
            accepted_additions.append(value);item('applied',value,'new prim','Explicitly approved addition referencing the edited export')
        except Exception as error:item('conflicts',value,'new prim',str(error))
    represented={m['editedPath'] for m in mappings.values()}|set(base)
    represented.update(path for path,p in candidates.items() if any(p.GetPath().HasPrefix(Sdf.Path(m['editedPath'])) for m in new_networks))
    represented.update(path for path in candidates if any(Sdf.Path(path).HasPrefix(Sdf.Path(root)) for root in accepted_additions))
    for path,prim in candidates.items():
        if path not in represented:
            categories={property_category(prim,n) for n in prim.GetPropertyNames()}
            if categories & allowed:item('conflicts',path,'prim','New/renamed prim has no baseline correspondence. Map a rename explicitly; additions require deliberate scene composition.')
            else:item('preserved',path,'prim','New prim outside allowed scope')
    written_subsets=set()
    for row in report['applied']:
        if row['property']=='indices' or row['property']=='material network':
            mapped=next((m for m in [*mappings.values(),*new_subsets] if m['editedPath']==row['prim'] or m.get('baselinePath')==row['prim']),None)
            if mapped and output.GetPrimAtPath(mapped['inputPath']).IsA(UsdGeom.Subset):written_subsets.add(mapped['inputPath'])
    for subset_path in written_subsets:
        prim=output.GetPrimAtPath(subset_path);subset=UsdGeom.Subset(prim)
        if subset.GetFamilyNameAttr().Get()!='materialBind' or subset.GetElementTypeAttr().Get()!='face':
            item('conflicts',subset_path,'face subset','Only materialBind face subsets can be reconciled automatically');continue
        assigned=set(subset.GetIndicesAttr().Get() or [])
        mesh=UsdGeom.Mesh(prim.GetParent());faces=len(mesh.GetFaceVertexCountsAttr().Get() or []) if mesh else 0
        if any(index<0 or index>=faces for index in assigned):item('conflicts',subset_path,'face subset','Face assignment index is outside the mesh');continue
        for sibling in prim.GetParent().GetChildren():
            if str(sibling.GetPath())==subset_path or not sibling.IsA(UsdGeom.Subset):continue
            other=UsdGeom.Subset(sibling)
            if other.GetFamilyNameAttr().Get()!='materialBind':continue
            existing=list(other.GetIndicesAttr().Get() or []);overlap=assigned.intersection(existing)
            if not overlap:continue
            if str(sibling.GetPath()) in written_subsets:item('conflicts',subset_path,'face subset','Edited face assignments overlap each other');continue
            editable(sibling.GetPath()).CreateAttribute('indices',Sdf.ValueTypeNames.IntArray).Set([i for i in existing if i not in assigned])
            item('applied',str(sibling.GetPath()),'indices','Explicit face reassignment removes overlapping faces from preserved subsets')
    # Check the edited mesh's structural correspondence before promoting any
    # layer, including UV cardinality and topology indices.
    for entry in mappings.values():
        ep=candidates.get(entry['editedPath'])
        if not ep or not ep.IsA(UsdGeom.Mesh):continue
        mesh=UsdGeom.Mesh(ep);points=mesh.GetPointsAttr().Get() or [];counts=mesh.GetFaceVertexCountsAttr().Get() or [];indices=mesh.GetFaceVertexIndicesAttr().Get() or []
        if any(c<0 for c in counts) or sum(counts)!=len(indices) or any(i<0 or i>=len(points) for i in indices) or any(not math.isfinite(v) for point in points for v in point):item('conflicts',entry['baselinePath'],'topology','Edited mesh topology is invalid')
    for entry in mappings.values():
        if not any(row['prim']==entry['baselinePath'] and (row['property'] in {'points','faceVertexIndices','faceVertexCounts','curveVertexCounts','normals'} or row['property'].startswith(('primvars:st','primvars:uv'))) for row in report['applied']):continue
        prim=output.GetPrimAtPath(entry['inputPath'])
        if prim.IsA(UsdGeom.Curves):
            curves=UsdGeom.Curves(prim);points=curves.GetPointsAttr().Get() or [];counts=curves.GetCurveVertexCountsAttr().Get() or []
            if sum(counts)!=len(points) or any(n<0 for n in counts):item('conflicts',entry['baselinePath'],'curve topology','Curve counts no longer match control points')
            if prim.IsA(UsdGeom.NurbsCurves) and any(row['prim']==entry['baselinePath'] and row['property']=='curveVertexCounts' for row in report['applied']):item('conflicts',entry['baselinePath'],'curve topology','NURBS knot/order remapping requires explicit reconciliation')
        if not prim.IsA(UsdGeom.Mesh):continue
        mesh=UsdGeom.Mesh(prim)
        codes=[Usd.TimeCode.Default(),*[Usd.TimeCode(t) for t in sorted(set(mesh.GetPointsAttr().GetTimeSamples()+mesh.GetFaceVertexCountsAttr().GetTimeSamples()+mesh.GetFaceVertexIndicesAttr().GetTimeSamples()))]]
        for code in codes:
            points=mesh.GetPointsAttr().Get(code) or [];counts=mesh.GetFaceVertexCountsAttr().Get(code) or [];indices=mesh.GetFaceVertexIndicesAttr().Get(code) or []
            sizes={'constant':1,'uniform':len(counts),'vertex':len(points),'varying':len(points),'faceVarying':len(indices)}
            normals=mesh.GetNormalsAttr().Get(code)
            if normals and len(normals)!=sizes.get(str(mesh.GetNormalsInterpolation()),len(normals)):item('conflicts',entry['baselinePath'],'normals','Resulting topology invalidates retained normals')
            for pv in UsdGeom.PrimvarsAPI(prim).GetPrimvars():
                value=pv.Get(code)
                if value is None or not pv.GetTypeName().isArray:continue
                expected=sizes.get(str(pv.GetInterpolation()))
                index=pv.GetIndices(code) if pv.IsIndexed() else None
                actual=len(index) if index is not None else len(value)//max(pv.GetElementSize(),1)
                if expected is not None and (actual!=expected or (index is not None and any(i<0 or i>=len(value) for i in index))):item('conflicts',entry['baselinePath'],pv.GetName(),'Resulting topology invalidates a retained primvar; explicit remapping required')
    for entry in mappings.values():
        if not any(row['prim']==entry['baselinePath'] and (row['property'].startswith(('skel:','primvars:skel:')) or row['property'] in {'joints','bindTransforms','restTransforms'}) for row in report['applied']):continue
        prim=output.GetPrimAtPath(entry['inputPath']);binding=UsdSkel.BindingAPI(prim)
        indices,weights=binding.GetJointIndicesPrimvar(),binding.GetJointWeightsPrimvar()
        if (indices and indices.GetAttr().HasAuthoredValueOpinion()) or (weights and weights.GetAttr().HasAuthoredValueOpinion()):
            iv=indices.Get() if indices else None;wv=weights.Get() if weights else None
            size=indices.GetElementSize() if indices else 0;skel=binding.GetInheritedSkeleton();joints=skel.GetJointsAttr().Get() if skel else []
            point_count=len(prim.GetAttribute('points').Get() or []) if prim.GetAttribute('points') else 0
            expected=size if indices and str(indices.GetInterpolation())=='constant' else point_count*size
            if iv is None or wv is None or size<=0 or len(iv)!=len(wv) or len(iv)!=expected or any(i<0 or i>=len(joints) for i in iv) or any(not math.isfinite(w) or w<0 for w in wv):item('conflicts',entry['baselinePath'],'skinning','Resulting skin influences are incompatible with preserved geometry/skeleton')
    if 'animation' in allowed and (baseline.GetStartTimeCode()!=edited.GetStartTimeCode() or baseline.GetEndTimeCode()!=edited.GetEndTimeCode()):
        output.SetStartTimeCode(min(source.GetStartTimeCode(),source_time(edited.GetStartTimeCode())));output.SetEndTimeCode(max(source.GetEndTimeCode(),source_time(edited.GetEndTimeCode())))
        item('applied','/','time range','Extended animation range while preserving the incoming range')
    if not report['conflicts']:
        report['redundantOpinionsPruned']=prune_redundant_properties(output,source)
        share_identical_edits(output,source,report)
    root=output.GetRootLayer()
    # Fingerprint review inputs so Publish cannot use a stale review result.
    if not request.get('liveReview'):validate_revisions(request['sourceRevisions'])
    if file_digest(request['edited'])!=edited_digest or file_digest(request['baseline'])!=request['baselineDigest']:raise ValueError('Export/baseline changed during review; retry')
    token=hashlib.sha256(json.dumps({'edited':edited_digest,'baseline':request['baselineDigest'],'scope':request['policy'],'map':review_correspondence(request),'deletions':request.get('confirmedDeletions',[]),'input':request['sourceRevisions'],'conversion':request['conversion'],'graph':request['currentGraph'],'handoff':request['handoff'],'newPrimPaths':request.get('newPrimPaths',[]),'baselinePath':request['baseline']},sort_keys=True).encode()).hexdigest()
    result={'editedDigest':edited_digest,'report':report,'token':token,'correspondence':list(mappings.values()),'inputPrims':[{'path':p,'type':v.GetTypeName()} for p,v in originals.items()],'baselinePrims':[{'path':p,'type':v.GetTypeName()} for p,v in base.items()],'editedPrims':[{'path':p,'type':v.GetTypeName()} for p,v in candidates.items()]}
    return (result,root,output,[source,baseline,edited]) if return_layers else result


def publish_reviewed(request):
    from pxr import Sdf,Usd,UsdGeom,Gf
    result,layer,output,alive=review_publication(request,True)
    if result['token']!=request.get('reviewToken'):raise ValueError('Review is stale. Analyze again before publishing')
    if result['report']['conflicts']:raise ValueError('Resolve publication conflicts before publishing')
    if not result['report']['applied']:raise ValueError('No permitted edits would be published. Check node edit scope and verified application capabilities, authorize the scope for this baseline, and analyze again. No output files were written.')
    return write_publication(request,result,layer,output)


def write_publication(request,result,layer,output):
    from pxr import Sdf,Usd,UsdGeom,Gf
    import tempfile,uuid
    path=Path(request['destination']);edit_path=path.with_name(path.stem+'.edits.usda')
    if not path.is_absolute() or path.suffix.lower()!='.usda' or not path.parent.is_dir():raise ValueError('Choose an absolute .usda publication path')
    targets=(edit_path,path)
    replacing=any(os.path.lexists(p) for p in targets)
    if replacing and not request.get('overwrite'):raise ValueError('Publication exists; explicit replacement confirmation is required')
    if any(p.is_symlink() for p in targets):raise ValueError('Cannot replace symlink publication destinations')
    protected=[request.get(k) for k in ('handoff','baseline','edited')]+[r['path'] for r in request.get('sourceRevisions',[])]
    for target in targets:
        for original in protected:
            if original and (os.path.normcase(os.path.abspath(target))==os.path.normcase(os.path.abspath(original)) or (target.exists() and os.path.exists(original) and os.path.samefile(target,original))):
                raise ValueError('Publication cannot replace a source, baseline or edited export')
    if replacing:
        if not path.is_file() or not edit_path.is_file():raise ValueError('Existing publication must have both its root and edit layer')
        previous=fresh_publication_stages([str(path)])[0]
        child=previous.GetPrimAtPath('/Scene/PreservedInput')
        refs=child.GetMetadata('references') if child else None
        if not refs or not any(os.path.normcase(os.path.abspath(Sdf.ComputeAssetPathRelativeToLayer(previous.GetRootLayer(),r.assetPath)))==os.path.normcase(os.path.abspath(edit_path)) for r in refs.GetAddedOrExplicitItems()):
            raise ValueError('Only an OrchFlow publication pair can be replaced')
    def stamp(target):
        if not os.path.lexists(target):return None
        value=os.stat(target);return value.st_size,value.st_mtime_ns,value.st_ino
    before={target:stamp(target) for target in targets}
    root=Usd.Stage.CreateInMemory();scene=UsdGeom.Xform.Define(root,'/Scene');root.SetDefaultPrim(scene.GetPrim());UsdGeom.SetStageUpAxis(root,'Y');UsdGeom.SetStageMetersPerUnit(root,1)
    root.GetRootLayer().customLayerData={'orchflowPublication':True}
    root.SetTimeCodesPerSecond(output.GetTimeCodesPerSecond());root.SetFramesPerSecond(output.GetFramesPerSecond());root.SetStartTimeCode(output.GetStartTimeCode());root.SetEndTimeCode(output.GetEndTimeCode())
    scene.AddTransformOp().Set(Gf.Matrix4d(*[x for row in request['conversion']['inverse'] for x in row]))
    child=root.OverridePrim('/Scene/PreservedInput')
    temporary=[];backups={};installed=[]
    try:
        for target in targets:
            fd,name=tempfile.mkstemp(prefix='.orchflow-publish-',suffix='.usda',dir=path.parent);os.close(fd);temporary.append(Path(name))
        temp_edit,temp_root=temporary
        if not layer.Export(str(temp_edit)):raise ValueError('Edit layer export failed')
        child.GetReferences().AddReference(str(temp_edit),output.GetDefaultPrim().GetPath())
        if not root.GetRootLayer().Export(str(temp_root)):raise ValueError('Publication root export failed')
        staged=fresh_publication_stages([str(temp_root)])[0]
        if not staged.GetDefaultPrim():raise ValueError('Staged publication could not be reopened')
        child.GetReferences().ClearReferences();child.GetReferences().AddReference(str(edit_path),output.GetDefaultPrim().GetPath())
        if not root.GetRootLayer().Export(str(temp_root)):raise ValueError('Publication root export failed')
        validate_revisions(request['sourceRevisions'])
        if any(stamp(target)!=value for target,value in before.items()):raise ValueError('Publication changed while preparing replacement; retry')
        for target in targets:
            if before[target] is not None:
                backup=target.with_name(target.name+'.orchflow-backup-'+uuid.uuid4().hex)
                os.link(target,backup);backups[target]=backup
        for temp,target in zip(temporary,targets):
            if target in backups:os.replace(temp,target)
            else:os.link(temp,target)
            installed.append(target)
        reopened=fresh_publication_stages([str(path)])[0]
        if not reopened.GetDefaultPrim():raise ValueError('Published composition could not be reopened')
        validate_revisions(request['sourceRevisions'])
        result['correspondence']=[{key:entry[key] for key in ['baselinePath','inputPath','editedPath']} for entry in request.get('correspondence',[])]
        result.update(publicationRevision=uuid.uuid4().hex,path=str(path),editLayerPath=str(edit_path),sourceRevisions=request['sourceRevisions'],policy=request['policy'],conversion=request['conversion'],baselineDependencyId=request.get('baselineDependencyId'),handoffDependencyId=request.get('handoffDependencyId'),replacementBackups=[str(p) for p in backups.values()])
        return result
    except Exception:
        for target in reversed(installed):
            if target in backups:os.replace(backups.pop(target),target)
            else:target.unlink()
        raise
    finally:
        for temp in temporary:
            if temp.exists():temp.unlink()


def reconcile_upstream(request):
    """Explicit USD strong-over-weak composition, without Blender or conflict review."""
    from pxr import Sdf,Usd,UsdUtils
    preflight=globals().get('coordinate_preflight');export=globals().get('export_handoff')
    if preflight is None:
        from usd_graph import coordinate_preflight as preflight,export_handoff as export
    destination=Path(request['destination'])
    if not destination.is_absolute() or destination.suffix.lower()!='.usda':raise ValueError('Choose an absolute new .usda publication path')
    edit_path=destination.with_name(destination.stem+'.edits.usda')
    input_path=destination.with_name(destination.stem+'.input.usda')
    if any(os.path.lexists(p) for p in [destination,edit_path,input_path,input_path.with_suffix('.layers')]):raise ValueError('Reconciliation destination exists; choose a new filename')
    previous=Sdf.Layer.FindOrOpen(request['adjustmentLayer'])
    if not previous:raise ValueError('Published adjustment layer is unavailable')
    previous.Reload()
    layer=Sdf.Layer.CreateAnonymous('reconciled.edits.usda');layer.TransferContent(previous)
    # Preserve external shader/asset links relative to their original authoring
    # layer when the user chooses a different publication directory.
    UsdUtils.ModifyAssetPaths(layer,lambda asset:Sdf.ComputeAssetPathRelativeToLayer(previous,asset) if asset else asset)
    conversion=request['conversion']
    preparation={'graph':request['currentGraph'],'app':conversion['app'],'upAxis':conversion['upAxis'],'metersPerUnit':conversion['metersPerUnit'],'scale':conversion['scale'],'policy':request['policy']}
    prepared=preflight({**preparation,'measure':False},include_stage=True)
    before=prepared[0]
    created=None
    try:
        created=export({**preparation,'destination':str(input_path)},prepared=prepared)
        revision_fn=globals().get('graph_input_revision')
        if revision_fn is None:
            from usd_graph import graph_input_revision as revision_fn
        if before['draft']['inputRevision']!=revision_fn(request['currentGraph'],prepared[2]):raise ValueError('Upstream files changed during reconciliation; retry after saving them')
        layer.subLayerPaths=[str(input_path)]
        output=Usd.Stage.Open(layer)
        # Equal edits may have been factored into shared prototypes. Repoint
        # their preserved-input reference too, rather than retaining old input.
        current=Usd.Stage.Open(str(input_path))
        origins=dict(layer.customLayerData.get('orchflowPrototypeSources',{}))
        for prototype,source_path in origins.items():
            prim=output.GetPrimAtPath(prototype)
            if not prim:raise ValueError('Adjustment prototype is missing: '+prototype)
            prim.GetReferences().SetReferences([Sdf.Reference(str(input_path),Sdf.Path(source_path))])
        for path,names in dict(layer.customLayerData.get('orchflowInstancePassthrough',{})).items():
            original=current.GetPrimAtPath(path);instance=output.GetPrimAtPath(path)
            if not original or not instance:continue
            authored=set(json.loads(dict(layer.customLayerData.get('orchflowInstanceEdits',{})).get(path,'[]')))
            for name in set(json.loads(names))|{attr.GetName() for attr in original.GetAttributes() if attr.GetName() not in authored}:
                instance.RemoveProperty(name)
                attr=original.GetAttribute(name)
                if attr:
                    target=instance.CreateAttribute(name,attr.GetTypeName(),attr.IsCustom(),attr.GetVariability())
                    for time in [Usd.TimeCode.Default(),*[Usd.TimeCode(t) for t in attr.GetTimeSamples()]]:
                        value=attr.Get(time)
                        if value is not None:target.Set(value,time)
        if not output.GetDefaultPrim():raise ValueError('Adjustment layer has no default prim')
        # Stage time metadata follows current upstream unless the adjustment
        # explicitly authored a stronger opinion, as with every other field.
        layers,assets,missing=UsdUtils.ComputeAllDependencies(str(input_path))
        paths=sorted(set([str(input_path),*[l.realPath for l in layers if l.realPath],*assets,*missing]))
        records=[]
        for path in paths:
            try:
                stat=os.stat(path);records.append({'path':path,'size':stat.st_size,'modifiedNs':str(stat.st_mtime_ns)})
            except OSError:records.append({'path':path,'size':None,'modifiedNs':None})
        report={**request['report'],'reconciled':True,'reconciliationNote':'Existing adjustment opinions composed over current upstream. Stronger node opinions win; no conflict review or native reimport.'}
        result={'report':report,'correspondence':request['correspondence']}
        result=write_publication({**request,'sourceRevisions':records},result,layer,output)
        result['replacesPublicationDependencyId']=request['publicationDependencyId']
        result['reconciliation']={'inputPath':str(input_path),'layerPaths':created['layerPaths'],'graphRevision':before['draft']['graphRevision']}
        return result
    except Exception:
        if created:
            for path in [input_path,*map(Path,created['layerPaths'])]:
                if path.exists():path.unlink()
            input_path.with_suffix('.layers').rmdir()
        raise


def validate_manual_baseline(request):
    """Validate links/identity, not whether the user's export is actually unedited."""
    from pxr import Usd, UsdUtils, UsdGeom
    digest=globals().get('graph_digest')
    if digest is None:
        from usd_graph import graph_digest as digest
    if digest(request['currentGraph'])!=request['conversion']['graphRevision']:
        raise ValueError('Upstream graph differs from the handoff; reconcile first')
    if request['conversion'].get('inputRevision'):
        revision_fn=globals().get('graph_input_revision')
        compiler=globals().get('compile_graph')
        if revision_fn is None:
            from usd_graph import graph_input_revision as revision_fn,compile_graph as compiler
        input_stage,alive,_=compiler(request['currentGraph'],reload=True)
        if revision_fn(request['currentGraph'],alive)!=request['conversion']['inputRevision']:
            raise ValueError('Upstream files changed since handoff preparation; reconcile first')
    paths=[request['baseline'],request['handoff']]
    if os.path.samefile(*paths):raise ValueError('Baseline must be separate from handoff')
    stamps={path:(os.stat(path).st_size,os.stat(path).st_mtime_ns,os.stat(path).st_ino) for path in paths}
    source,baseline=fresh_publication_stages(paths[::-1],request['conversion'])
    if not source or not baseline:raise ValueError('Unable to open baseline or handoff')
    if source.GetRootLayer().customLayerData.get('orchflowNamespaceVersion')!=2:raise ValueError('Create a handoff with the current simplified scene hierarchy before linking a baseline')
    handoff_identity=stable_custom_string(source.GetDefaultPrim(),'userProperties:orchflowRoundTripId') if source.GetDefaultPrim() else None
    baseline_digest=file_digest(request['baseline'])
    if request.get('expectedDigest') and request['expectedDigest']!=baseline_digest:raise ValueError('Baseline changed while choosing correspondence; select it again')
    mapping=request.get('correspondence',[])
    for entry in mapping:
        if not baseline.GetPrimAtPath(entry.get('baselinePath','')):raise ValueError('Mapped baseline prim does not exist')
    try:selection=manual_selection(baseline,source,mapping=mapping)
    except ValueError as error:
        if not any(text in str(error) for text in ('matching OrchFlow ID','Explicit baseline correspondence has incompatible/missing input')):raise
        from pxr import UsdLux
        source_ids={identity(p) for p in source.Traverse(Usd.TraverseInstanceProxies()) if identity(p)}
        def inventory(stage):return [{'path':str(p.GetPath()),'type':p.GetTypeName()} for p in stage.Traverse(Usd.TraverseInstanceProxies()) if p.IsA(UsdGeom.Gprim) or p.HasAPI(UsdLux.LightAPI)]
        return {'needsMapping':True,'baselinePath':request['baseline'],'digest':baseline_digest,'baselinePrims':[p for p in inventory(baseline) if not identity(baseline.GetPrimAtPath(p['path'])) or identity(baseline.GetPrimAtPath(p['path'])) not in source_ids or any(m.get('baselinePath')==p['path'] for m in mapping)],'inputPrims':inventory(source),'message':'Map baseline geometry to the same logical handoff objects. No automatic name matching is performed.'}
    if not handoff_identity:raise ValueError('Handoff identity missing')
    layers,assets,missing=UsdUtils.ComputeAllDependencies(request['handoff'])
    records=[]
    for path in sorted(set([l.realPath for l in layers if l.realPath]+list(assets)+list(missing))):
        try:
            stat=os.stat(path);records.append({'path':path,'size':stat.st_size,'modifiedNs':str(stat.st_mtime_ns)})
        except OSError:records.append({'path':path,'size':None,'modifiedNs':None})
    report={'format':export_format(request['baseline']),'origin':'manual','baselineDigest':baseline_digest,'baselineCorrespondence':mapping,'sourceRevisions':records,
            'roundTripId':handoff_identity,'exporterOptions':None,
            'note':'User attests this is an unedited export. Exporter settings and application version are not verified.',
            'selection':selection,'baselineUpAxis':str(UsdGeom.GetStageUpAxis(baseline)),
            'baselineMetersPerUnit':UsdGeom.GetStageMetersPerUnit(baseline)}
    validate_revisions(records)
    if any((os.stat(path).st_size,os.stat(path).st_mtime_ns,os.stat(path).st_ino)!=stamp for path,stamp in stamps.items()):
        raise ValueError('Baseline or handoff changed during validation; select the baseline again')
    return {'baselinePath':request['baseline'],'nativeDependencyId':request['nativeDependencyId'],
            'handoffDependencyId':request['handoffDependencyId'],'policy':request['policy'],
            'conversion':request['conversion'],'report':report}


_live_layers={}

def mesh_topology_guard(prim):
    digest=hashlib.sha256()
    for name in ('faceVertexCounts','faceVertexIndices'):
        attr=prim.GetAttribute(name)
        digest.update(str(attr.Get() if attr else None).encode())
    points=prim.GetAttribute('points')
    return {'pointCount':len(points.Get() or []) if points else 0,'topology':digest.hexdigest()}

def create_live_adjustment(request):
    import uuid
    from pxr import Sdf
    result,layer,output,alive=review_publication(request,True)
    if result['token']!=request.get('reviewToken'):raise ValueError('Review is stale; analyze again')
    if result['report']['conflicts']:raise ValueError('Resolve conflicts before applying edits')
    if not result['report']['applied']:raise ValueError('No permitted edits to apply')
    guards={};paths=[]
    layer.Traverse(Sdf.Path.absoluteRootPath,lambda p:paths.append(p) if p.IsPropertyPath() and p.name=='points' else None)
    for path in paths:
        prim=alive[0].GetPrimAtPath(path.GetPrimPath())
        if prim:guards[str(prim.GetPath())]=mesh_topology_guard(prim)
    for source_path in dict(layer.customLayerData.get('orchflowPrototypeSources',{})).values():
        prim=alive[0].GetPrimAtPath(source_path)
        if prim and prim.GetAttribute('points'):guards[source_path]=mesh_topology_guard(prim)
    # Freeze only reviewed opinions; the old upstream layer is never persisted
    # as part of the adjustment. The single project JSON keeps these opinions
    # across restarts without generated USD files or unreviewed recomputation.
    layer.subLayerPaths=[]
    file_revisions=[]
    for path in (request['baseline'],request['edited']):
        stat=os.stat(path);file_revisions.append({'path':path,'size':stat.st_size,'modifiedNs':str(stat.st_mtime_ns)})
    return {'revision':uuid.uuid4().hex,'correspondence':review_correspondence(request),'layer':layer.ExportToString(),'guards':guards,'fileRevisions':file_revisions,
            'report':result['report'],'policy':request['policy'],'conversion':request['conversion'],
            'baselineDependencyId':request['baselineDependencyId'],'handoffDependencyId':request['handoffDependencyId']}

def compose_live_adjustment(graph,adjustment):
    from pxr import Sdf,Usd,UsdGeom,Gf
    preflight=globals().get('coordinate_preflight')
    if preflight is None:
        from usd_graph import coordinate_preflight as preflight
    validate_revisions(adjustment.get('fileRevisions',[]))
    conversion=adjustment['conversion']
    _,current,alive=preflight({'graph':graph,'app':conversion['app'],'upAxis':conversion['upAxis'],'metersPerUnit':conversion['metersPerUnit'],'scale':conversion['scale'],'measure':False},include_stage=True)
    for path,guard in adjustment.get('guards',{}).items():
        prim=current.GetPrimAtPath(path)
        if not prim or mesh_topology_guard(prim)!=guard:raise ValueError('Input paths or topology changed. Recreate the handoff and review application edits using the current scene paths. The saved adjustment and external files were preserved: '+path)
    signature=hashlib.sha256(adjustment['layer'].encode()).hexdigest()
    base=_live_layers.get(signature)
    if base is None:
        base=Sdf.Layer.CreateAnonymous('reviewed-adjustment.usda')
        if not base.ImportFromString(adjustment['layer']):raise ValueError('Invalid reviewed adjustment')
        if base.customLayerData.get('orchflowNamespaceVersion')!=2:raise ValueError('Saved edits use the previous scene hierarchy. Create a new handoff and matching baseline, then review edits again. Saved adjustment/native/export files were preserved.')
        if len(_live_layers)>=16:_live_layers.clear()
        _live_layers[signature]=base
    layer=Sdf.Layer.CreateAnonymous('live-adjustment.usda');layer.TransferContent(base)
    layer.subLayerPaths=[current.GetRootLayer().identifier]
    output=Usd.Stage.Open(layer)
    inherit_stage_metrics(output,current)
    for prototype,source_path in dict(layer.customLayerData.get('orchflowPrototypeSources',{})).items():
        prim=output.GetPrimAtPath(prototype)
        if prim:prim.GetReferences().SetReferences([Sdf.Reference(current.GetRootLayer().identifier,Sdf.Path(source_path))])
    for path,names in dict(layer.customLayerData.get('orchflowInstancePassthrough',{})).items():
        original=current.GetPrimAtPath(path);instance=output.GetPrimAtPath(path)
        if not original or not instance:raise ValueError('Instance correspondence changed: '+path)
        authored=set(json.loads(dict(layer.customLayerData.get('orchflowInstanceEdits',{})).get(path,'[]')))
        for name in set(json.loads(names))|{attr.GetName() for attr in original.GetAttributes() if attr.GetName() not in authored}:
            instance.RemoveProperty(name);attr=original.GetAttribute(name)
            if attr:
                target=instance.CreateAttribute(name,attr.GetTypeName(),attr.IsCustom(),attr.GetVariability())
                for time in [Usd.TimeCode.Default(),*[Usd.TimeCode(t) for t in attr.GetTimeSamples()]]:
                    value=attr.Get(time)
                    if value is not None:target.Set(value,time)
    root=Usd.Stage.CreateInMemory();scene=UsdGeom.Xform.Define(root,'/Scene');root.SetDefaultPrim(scene.GetPrim())
    UsdGeom.SetStageUpAxis(root,'Y');UsdGeom.SetStageMetersPerUnit(root,1)
    for setter,getter in [('SetStartTimeCode','GetStartTimeCode'),('SetEndTimeCode','GetEndTimeCode'),('SetTimeCodesPerSecond','GetTimeCodesPerSecond'),('SetFramesPerSecond','GetFramesPerSecond')]:getattr(root,setter)(getattr(output,getter)())
    scene.GetPrim().GetReferences().AddReference(layer.identifier,output.GetDefaultPrim().GetPath())
    prepend_root_transform(scene.GetPrim(),Gf.Matrix4d(*[v for row in conversion['inverse'] for v in row]),'returnCoordinates')
    return root,[*alive,current,base,layer,output,root]
