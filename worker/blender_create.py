"""Fixed Blender background adapter; request data arrives on stdin, never as code."""
import json
import hashlib
import math
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


def publish_pair(pairs, allowed_replacements, expected):
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
                if target not in allowed_replacements:
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


def stable_identity(prim, name):
    attr = prim.GetAttribute('userProperties:' + name)
    if not attr:
        return None
    values = [attr.Get(), *(attr.Get(t) for t in attr.GetTimeSamples())]
    authored = [value for value in values if value is not None]
    if authored and isinstance(authored[0], str) and all(v == authored[0] for v in authored):
        return authored[0]
    return None


def validate_baseline(source, baseline, round_trip, unit):
    if not baseline:
        raise RuntimeError('Baseline could not be reopened')
    root = baseline.GetRootLayer()
    if any(root.GetCompositionAssetDependencies()) or any(
            layer not in (root, baseline.GetSessionLayer()) for layer in baseline.GetUsedLayers()):
        raise RuntimeError('Baseline must contain its scene in one USD layer')
    if str(UsdGeom.GetStageUpAxis(baseline)) != 'Z' or not math.isclose(
            UsdGeom.GetStageMetersPerUnit(baseline), unit, rel_tol=1e-6):
        raise RuntimeError('Baseline axis or units differ from the prepared Blender scene')
    prims = list(baseline.Traverse(Usd.TraverseInstanceProxies()))
    identities = {value for prim in prims if (value := stable_identity(prim, 'orchflowRoundTripId'))}
    if identities != {round_trip}:
        raise RuntimeError('Blender did not preserve the handoff round-trip identity')
    source_ids = {value for prim in source.Traverse(Usd.TraverseInstanceProxies())
                  if (value := stable_identity(prim, 'orchflowId'))}
    root_id = stable_identity(source.GetDefaultPrim(), 'orchflowId')
    exported_ids = {value for prim in prims if (value := stable_identity(prim, 'orchflowId'))}
    if not (source_ids & exported_ids) - {root_id} or exported_ids - source_ids:
        raise RuntimeError('Blender did not preserve usable handoff object identities')


def create(config):
    import bpy

    source, destination, temporary = config['handoff'], config['destination'], config['temporary']
    overwrite = config.get("overwrite", False)
    capture_baseline=config.get("captureBaseline", False)
    targets = [destination, config["baseline"]] if capture_baseline else [destination]
    expected_outputs = revision(targets)
    allowed_replacements = {destination} if overwrite else set()
    if capture_baseline and config.get("overwriteBaseline", False):
        allowed_replacements.add(config["baseline"])
    for target in targets:
        if os.path.islink(target) or (os.path.lexists(target) and
                (not os.path.isfile(target) or target not in allowed_replacements)):
            raise RuntimeError('Cannot replace output without an eligible file and confirmation: ' + target)
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
    root = stage.GetDefaultPrim()
    if not root:
        raise RuntimeError('Handoff has no default prim')
    round_trip = stable_identity(root, 'orchflowRoundTripId')
    if capture_baseline and (not round_trip or stage.GetRootLayer().customLayerData.get('orchflowNamespaceVersion') != 2):
        raise RuntimeError('Create a fresh tagged handoff before capturing a baseline')
    layers, assets, unresolved = UsdUtils.ComputeAllDependencies(source)
    paths = [source] + [layer.realPath for layer in layers if layer.realPath] + list(assets) + list(unresolved)
    input_paths = {os.path.normcase(os.path.realpath(path)) for path in paths}
    if any(os.path.normcase(os.path.realpath(path)) in input_paths for path in targets):
        raise RuntimeError('An output destination overlaps a handoff dependency')
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
    baseline_digest=None
    export_options={}
    if capture_baseline:
        export_options=dict(filepath=baseline_temp,check_existing=False,selected_objects_only=False,
            collection='',export_animation=True,
            export_hair=True,export_uvmaps=True,rename_uvmaps=False,export_normals=True,
            export_materials=True,export_armatures=True,export_shapekeys=True,use_instancing=True,
            convert_orientation=False,export_textures_mode='KEEP',relative_paths=False,
            xform_op_mode='MAT',root_prim_path='',export_custom_properties=True,
            custom_properties_namespace='userProperties',convert_scene_units='CUSTOM',meters_per_unit=unit,
            merge_parent_xform=False)
        props=bpy.ops.wm.usd_export.get_rna_type().properties
        for key, value in export_options.items():
            if key not in props or (props[key].type == 'ENUM' and value not in props[key].enum_items.keys()):
                raise RuntimeError(f'Blender lacks the required USD export setting {key}={value}')
        if 'FINISHED' not in bpy.ops.wm.usd_export(**export_options): raise RuntimeError('Initial round-trip baseline export failed')
        baseline_stage=Usd.Stage.Open(baseline_temp)
        validate_baseline(stage, baseline_stage, round_trip, unit)
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
    report["replacementBackups"] = publish_pair(([(baseline_temp,baseline)] if capture_baseline else [])+[(temporary,destination)], allowed_replacements, expected_outputs)
    report['baselineCaptured']=capture_baseline
    if capture_baseline:
        report['baselineOrigin']='quickstart'
    return report


if __name__ == '__main__':
    config = json.loads(sys.stdin.readline())
    try:
        report = create(config)
        print('ORCHFLOW_CREATE:' + json.dumps(report), flush=True)
    finally:
        for key in ['temporary','baselineTemporary']:
            if os.path.isfile(config[key]): os.unlink(config[key])
