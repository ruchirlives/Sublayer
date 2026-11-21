"""Branch-local UsdSkel animation/pose mappings; never bakes mesh points."""
import hashlib
import math
from pxr import Gf, Sdf, Usd, UsdSkel, Vt


def _write_pose_trs(output, authored):
    translations = []
    rotations = []
    scales = []
    for j in authored:
        q = j['rotation']
        ro = j['rotationOffset']
        if ro[0] or ro[1] or ro[2]:
            base = Gf.Rotation(Gf.Quatd(q[0], Gf.Vec3d(*q[1:])))
            offset = (
                Gf.Rotation(Gf.Vec3d(1, 0, 0), ro[0])
                * Gf.Rotation(Gf.Vec3d(0, 1, 0), ro[1])
                * Gf.Rotation(Gf.Vec3d(0, 0, 1), ro[2])
            )
            qd = (offset * base).GetQuat()
            im = qd.GetImaginary()
            rotations.append(Gf.Quatf(float(qd.GetReal()), float(im[0]), float(im[1]), float(im[2])))
        else:
            rotations.append(Gf.Quatf(float(q[0]), float(q[1]), float(q[2]), float(q[3])))
        t = j['translation']
        to = j['translationOffset']
        translations.append(Gf.Vec3f(float(t[0] + to[0]), float(t[1] + to[1]), float(t[2] + to[2])))
        s = j['scale']
        scales.append(Gf.Vec3h(float(s[0]), float(s[1]), float(s[2])))
    output.CreateTranslationsAttr().Set(Vt.Vec3fArray(translations))
    rot_attr = output.CreateRotationsAttr()
    rot_arr = Vt.QuatfArray(rotations)
    rot_attr.Clear()
    rot_attr.Set(rot_arr)
    rot_attr.Set(rot_arr, Usd.TimeCode(0.0))
    rot_attr.Set(rot_arr, Usd.TimeCode(1e-4))
    output.CreateScalesAttr().Set(Vt.Vec3hArray(scales))
    return True


def _match_authored_joints(authored, target_names):
    target_set = set(target_names)
    leaf_to_target = {}
    for tn in target_names:
        leaf = tn.rsplit('/', 1)[-1]
        if leaf not in leaf_to_target:
            leaf_to_target[leaf] = tn
    matched_names = []
    matched_joints = []
    used = set()
    for j in authored:
        name = j['name']
        target_name = None
        if name in target_set:
            target_name = name
        else:
            leaf = name.rsplit('/', 1)[-1]
            cand = leaf_to_target.get(leaf)
            if cand is not None:
                target_name = cand
        if target_name is not None and target_name not in used:
            used.add(target_name)
            matched_names.append(target_name)
            matched_joints.append(j)
    return matched_names, matched_joints


def update_live_pose(node, targets):
    cfg = node.get('pose') or {}
    entries = {e['id']: e for e in cfg.get('entries', [])}
    active = [
        (row['id'], row.get('entryId'), row.get('skeletonPath'))
        for row in cfg.get('mappings', [])
        if row.get('enabled', True) and row.get('entryId')
    ]
    if len(active) != len(targets):
        return False
    for (row_id, entry_id, skel_path), (t_row_id, t_entry_id, t_skel_path, output) in zip(active, targets):
        if row_id != t_row_id or entry_id != t_entry_id or skel_path != t_skel_path:
            return False
        entry = entries.get(entry_id)
        authored = entry.get('joints') if entry else None
        if not authored:
            return False
        skel_prim = output.GetPrim().GetStage().GetPrimAtPath(t_skel_path)
        target_names = list(UsdSkel.Skeleton(skel_prim).GetJointsAttr().Get() or []) if skel_prim and skel_prim.IsValid() else [j['name'] for j in authored]
        matched_names, matched_joints = _match_authored_joints(authored, target_names)
        if not matched_joints:
            return False
        output.CreateJointsAttr(matched_names)
        if not _write_pose_trs(output, matched_joints):
            return False
        if skel_prim and skel_prim.IsValid():
            rel = UsdSkel.BindingAPI(skel_prim).GetAnimationSourceRel()
            if rel:
                rel.ClearTargets(False)
                rel.SetTargets([output.GetPath()])
    return True


def apply_pose(stage, node, files, keepalive, reload=False, live_poses=None):
    cfg = node.get('pose') or {}
    entries = {e['id']: e for e in cfg.get('entries', [])}
    seen = set()
    for row in cfg.get('mappings', []):
        if not row.get('enabled', True) or not row.get('entryId'):
            continue
        entry = entries.get(row['entryId'])
        if not entry:
            raise ValueError('Pose mapping refers to a missing library entry')
        target = stage.GetPrimAtPath(row['skeletonPath'])
        if not target or not target.IsA(UsdSkel.Skeleton) or target.IsInstanceProxy():
            raise ValueError('Pose target must be an existing, editable Skeleton: ' + row['skeletonPath'])
        if row['skeletonPath'] in seen:
            raise ValueError('Pose contains multiple active mappings for the same skeleton')
        seen.add(row['skeletonPath'])
        authored = entry.get('joints')
        if authored:
            target_names = list(UsdSkel.Skeleton(target).GetJointsAttr().Get() or [])
            matched_names, matched_joints = _match_authored_joints(authored, target_names)
            if not matched_joints:
                raise ValueError('Authored pose joint order changed or has no matching joint names for skeleton: ' + row['skeletonPath'])
            path = str(target.GetPath()) + '/_OrchFlowPose_' + hashlib.sha256((node['id'] + ':' + row['id']).encode()).hexdigest()[:20]
            output = UsdSkel.Animation.Define(stage, path)
            output.CreateJointsAttr(matched_names)
            if entry.get('blendShapes'):
                output.CreateBlendShapesAttr(entry['blendShapes'])
                output.CreateBlendShapeWeightsAttr(entry['blendShapeWeights'])
            if not _write_pose_trs(output, matched_joints):
                raise ValueError('Cannot author pose transforms')
            UsdSkel.BindingAPI.Apply(target).CreateAnimationSourceRel().SetTargets([output.GetPath()])
            if live_poses is not None:
                live_poses.setdefault(node['id'], []).append((row['id'], row.get('entryId'), row.get('skeletonPath'), output))
            continue
        source = stage
        if entry.get('dependencyId'):
            path = files.get(entry['dependencyId'])
            if not path:
                raise ValueError('Pose animation dependency is missing')
            source = Usd.Stage.Open(path)
            if not source:
                raise ValueError('Cannot open Pose animation asset: ' + path)
            source.Reload()
            keepalive.append(source)
        anim = UsdSkel.Animation(source.GetPrimAtPath(entry['animationPath']))
        if not anim:
            raise ValueError('Pose source must be a SkelAnimation: ' + entry['animationPath'])
        joints = list(anim.GetJointsAttr().Get() or [])
        target_joints = set(UsdSkel.Skeleton(target).GetJointsAttr().Get() or [])
        if not joints or len(set(joints)) != len(joints) or not set(joints).issubset(target_joints):
            raise ValueError('Pose joint names do not match the target skeleton; retargeting is required')
        mode = entry.get('mode')
        frame = float(entry.get('poseTime', 0))
        if mode not in ('clip', 'pose') or not math.isfinite(frame):
            raise ValueError('Invalid Pose mode or sample frame')
        name = hashlib.sha256((node['id'] + ':' + row['id']).encode()).hexdigest()[:20]
        path = str(target.GetPath()) + '/_OrchFlowPose_' + name
        if stage.GetPrimAtPath(path):
            raise ValueError('Pose generated namespace collides with an existing prim')
        output = UsdSkel.Animation.Define(stage, path)
        if mode == 'clip':
            scale = stage.GetTimeCodesPerSecond() / source.GetTimeCodesPerSecond()
            if source == stage:
                # Internal references avoid a root-layer self-reference cycle.
                output.GetPrim().GetReferences().AddInternalReference(anim.GetPath())
            else:
                output.GetPrim().GetReferences().AddReference(source.GetRootLayer().identifier, anim.GetPath(), Sdf.LayerOffset(0, scale))
                stage.SetStartTimeCode(min(stage.GetStartTimeCode(), source.GetStartTimeCode() * scale))
                stage.SetEndTimeCode(max(stage.GetEndTimeCode(), source.GetEndTimeCode() * scale))
        else:
            output.CreateJointsAttr(joints)
            for attr_name in ('translations', 'rotations', 'scales', 'blendShapes', 'blendShapeWeights'):
                attr = anim.GetPrim().GetAttribute(attr_name)
                value = attr.Get(Usd.TimeCode(frame)) if attr else None
                if value is not None:
                    output.GetPrim().CreateAttribute(attr_name, attr.GetTypeName(), custom=False).Set(value)
        UsdSkel.BindingAPI.Apply(target).CreateAnimationSourceRel().SetTargets([output.GetPath()])
