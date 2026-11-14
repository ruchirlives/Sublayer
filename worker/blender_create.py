"""Fixed Blender background adapter; request data arrives on stdin, never as code."""
import bpy
import json
import hashlib
import fnmatch
import os
import sys
from pxr import Usd, UsdGeom, UsdUtils, Gf


def revision(paths):
    result = []
    for path in sorted(set(paths)):
        try:
            value = os.stat(path)
            result.append({'path':path,'size':value.st_size,'modifiedNs':str(value.st_mtime_ns)})
        except OSError:
            result.append({'path':path,'size':None,'modifiedNs':None})
    return result


def publish_pair(pairs, overwrite, expected):
    """Publish validated native/baseline outputs; retain backups on replacement."""
    import uuid
    if revision([target for _, target in pairs]) != expected:
        raise RuntimeError('Destination changed during import; existing files were preserved. Retry.')
    backups, installed = {}, []
    try:
        for temporary, target in pairs:
            if os.path.islink(target):
                raise RuntimeError('Cannot replace a symlink destination')
            if os.path.lexists(target):
                if not overwrite:
                    raise RuntimeError('Destination already exists; replacement requires confirmation')
                backup = target + '.orchflow-backup-' + uuid.uuid4().hex
                os.link(target, backup)
                backups[target] = backup
        for temporary, target in pairs:
            if target in backups:
                os.replace(temporary, target)
            else:
                os.link(temporary, target)
            installed.append(target)
    except Exception:
        for target in reversed(installed):
            if target in backups:
                os.replace(backups.pop(target), target)
            else:
                os.unlink(target)
        raise
    return list(backups.values())


def create(config):
    source, destination, temporary = config['handoff'], config['destination'], config['temporary']
    overwrite = config.get("overwrite", False)
    capture_baseline=config.get("captureBaseline", True)
    targets = [destination, config["baseline"]] if capture_baseline else [destination]
    expected_outputs = revision(targets)
    if os.path.lexists(destination) and not overwrite:
        raise RuntimeError('Native destination exists. Choose a new filename; existing edits are never overwritten.')
    expected=config['policy']['profile']['applicationVersion']
    actual='.'.join(map(str,bpy.app.version))
    if not fnmatch.fnmatchcase(actual,expected.replace('x','*')):
        raise RuntimeError(f'Configured application version {expected} does not match Blender {actual}. Verify and select the correct application profile.')
    conversion = config['conversion']
    stage = Usd.Stage.Open(source)
    if not stage:
        raise RuntimeError('Handoff USD could not be opened')
    axis, unit = str(UsdGeom.GetStageUpAxis(stage)), UsdGeom.GetStageMetersPerUnit(stage)
    if axis != conversion['upAxis'] or abs(unit-conversion['metersPerUnit']) > 1e-10*unit:
        raise RuntimeError('Handoff metadata differs from its recorded conversion. Recreate the handoff first.')
    layers, assets, unresolved = UsdUtils.ComputeAllDependencies(source)
    paths = [layer.realPath for layer in layers if layer.realPath] + list(assets) + list(unresolved)
    before = revision(paths)
    if len(json.dumps(before))>4000000: raise RuntimeError("Input dependency revision record is too large; split the preparation workflow")
    options = dict(filepath=source, relative_path=False, scale=1.0,
        apply_unit_conversion_scale=False, support_scene_instancing=True,
        import_textures_mode='IMPORT_NONE', import_materials=True,
        import_skeletons=True, import_blendshapes=True, read_mesh_uvs=True,
        import_visible_only=False, import_defined_only=True, set_frame_range=True,
        merge_parent_xform=False, property_import_mode="USER")
    properties = bpy.ops.wm.usd_import.get_rna_type().properties
    missing = [key for key in options if key not in properties]
    if missing:
        raise RuntimeError('Installed Blender lacks required USD import options: ' + ', '.join(missing))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system = 'METRIC'
    bpy.context.scene.unit_settings.scale_length = unit
    status = bpy.ops.wm.usd_import(**options)
    if 'FINISHED' not in status or not bpy.context.scene.objects:
        raise RuntimeError('USD import did not create an editable scene')
    if revision(paths) != before:
        raise RuntimeError('Handoff or referenced assets changed during import. Retry after saving them.')
    baseline, baseline_temp=config['baseline'], config['baselineTemporary']
    round_trip=stage.GetDefaultPrim().GetAttribute('userProperties:orchflowRoundTripId').Get()
    baseline_digest=None
    export_options={}
    if capture_baseline:
        if os.path.lexists(baseline) and not overwrite: raise RuntimeError('Baseline destination already exists')
        export_options=dict(filepath=baseline_temp,check_existing=False,export_animation=True,
            export_hair=True,export_uvmaps=True,rename_uvmaps=False,export_normals=True,
            export_materials=True,export_armatures=True,export_shapekeys=True,use_instancing=True,
            convert_orientation=False,export_textures_mode='KEEP',relative_paths=False,
            xform_op_mode='MAT',root_prim_path='',export_custom_properties=True,
            custom_properties_namespace='userProperties',convert_scene_units='CUSTOM',meters_per_unit=unit,
            merge_parent_xform=False)
        props=bpy.ops.wm.usd_export.get_rna_type().properties
        if any(key not in props for key in export_options): raise RuntimeError('Blender lacks required round-trip baseline export options')
        if 'KEEP' not in [value.identifier for value in props['export_textures_mode'].enum_items]: raise RuntimeError('Blender cannot export without copying textures')
        if 'FINISHED' not in bpy.ops.wm.usd_export(**export_options): raise RuntimeError('Initial round-trip baseline export failed')
        baseline_stage=Usd.Stage.Open(baseline_temp)
        if not baseline_stage: raise RuntimeError('Baseline could not be reopened')
        if any(layer.anonymous and layer != baseline_stage.GetSessionLayer() for layer in baseline_stage.GetUsedLayers()): raise RuntimeError('Baseline contains transient dependencies')
        round_trip=stage.GetDefaultPrim().GetAttribute('userProperties:orchflowRoundTripId').Get()
        if not round_trip or not any(p.GetAttribute('userProperties:orchflowRoundTripId') and p.GetAttribute('userProperties:orchflowRoundTripId').Get()==round_trip for p in baseline_stage.Traverse(Usd.TraverseInstanceProxies())):
            raise RuntimeError('Blender did not retain handoff identity. Cannot capture a safe baseline; enable user/custom properties.')
        digest=hashlib.sha256()
        with open(baseline_temp,'rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
        baseline_digest=digest.hexdigest()
    bpy.context.preferences.filepaths.save_version = 0
    if 'FINISHED' not in bpy.ops.wm.save_as_mainfile(filepath=temporary, check_existing=False, relative_remap=False):
        raise RuntimeError('Blender did not save the native project')
    if 'FINISHED' not in bpy.ops.wm.open_mainfile(filepath=temporary, load_ui=False, use_scripts=False):
        raise RuntimeError('Created native project could not be reopened')
    if not bpy.context.scene.objects:
        raise RuntimeError('Created native project has no imported objects after reopening')
    # Blender always uses Z-up; its importer rotates Y-up USD automatically.
    importer_matrix = Gf.Matrix4d(1)
    if axis == 'Y':
        importer_matrix.SetRotate(Gf.Rotation(Gf.Vec3d(1,0,0),90))
    forward = Gf.Matrix4d(*[value for row in conversion['forward'] for value in row]) * importer_matrix
    report = {'adapterVersion':2,'roundTripId':round_trip,'blenderVersion':bpy.app.version_string,
        'importerOptions':{key:value for key,value in options.items() if key != 'filepath'},
        'nativeUpAxis':'Z','nativeMetersPerUnit':unit,
        'nativeForward':[list(row) for row in forward],
        'nativeInverse':[list(row) for row in forward.GetInverse()],
        'sourceRevisionDigest':hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest(),
        'sourceRevisions':before,'baselineDigest':baseline_digest,
        'exporterOptions':{key:value for key,value in export_options.items() if key!='filepath'},
        'sourceRevisionCount':len(before),'missingAssets':list(unresolved)[:100], 'missingAssetCount':len(unresolved),
        'objectCount':len(bpy.context.scene.objects),
        'meshCount':sum(obj.type == 'MESH' for obj in bpy.context.scene.objects),
        'armatureCount':sum(obj.type == 'ARMATURE' for obj in bpy.context.scene.objects)}
    # Exclusive publication: no existing destination can be replaced, including
    # a file created by another process while Blender was importing.
    if revision(paths)!=before:raise RuntimeError('Preserved inputs changed during preparation; retry after saving them.')
    report["replacementBackups"] = publish_pair(([(baseline_temp,baseline)] if capture_baseline else [])+[(temporary,destination)], overwrite, expected_outputs)
    report['baselineCaptured']=capture_baseline
    return report


config = json.loads(sys.stdin.readline())
try:
    report = create(config)
    print('ORCHFLOW_CREATE:' + json.dumps(report), flush=True)
finally:
    for key in ['temporary','baselineTemporary']:
        if os.path.isfile(config[key]): os.unlink(config[key])
