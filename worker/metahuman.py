"""Read-only Epic DNA -> anonymous OpenUSD neutral rig. No RigLogic evaluation."""
import importlib
import math
import os
import re
import sys
from pathlib import Path

from pxr import Gf, Sdf, Tf, Usd, UsdGeom, UsdShade, UsdSkel, Vt


def _sdk():
    # Must be built for this worker's Python ABI, not an arbitrary Maya Python.
    version = f'py{sys.version_info.major}.{sys.version_info.minor}'
    root = os.environ.get('ORCHFLOW_DNA_ROOT')
    if root:
        # Accept either a direct binding directory or the runtime base directory.
        for candidate in (Path(root) / version / 'lib', Path(root) / version, Path(root) / 'lib'):
            if (candidate / 'dna.py').is_file():
                root = str(candidate)
                break
    else:
        candidates = [Path(os.environ.get('LOCALAPPDATA', '')) / 'Sublayer' / 'runtimes' / 'dna' / version / 'lib',
                      Path('I:/AI/OpenRigLogic-runtime') / version / 'lib']
        root = next((str(p) for p in candidates if (p / 'dna.py').is_file()), None)
    if root:
        if not os.path.isdir(root):
            raise ValueError(f'ORCHFLOW_DNA_ROOT is not a directory: {root}')
        if root not in sys.path:
            sys.path.insert(0, root)
        if os.name == 'nt' and hasattr(os, 'add_dll_directory'):
            # Keep the DLL directory handle alive for delayed dependencies.
            if not hasattr(_sdk, 'dll_dir'):
                _sdk.dll_dir = os.add_dll_directory(root)
    try:
        return importlib.import_module('dna')
    except (ImportError, OSError) as exc:
        raise ValueError(
            'MetaHuman requires Epic DNA reader Python bindings built for Python '
            f'{sys.version_info.major}.{sys.version_info.minor}. Build Epic OpenRigLogic '
            'with scripts/setup-metahuman.ps1, or set ORCHFLOW_DNA_ROOT '
            'to the directory containing dna.py, its extension and DLLs. '
            'OpenRigLogic supports legacy and UE 5.6+ DNA. '
            'Old DNA Calibration bindings only support legacy DNA. No fallback was used.'
        ) from exc


def load_dna(path, lod=0):
    if not path or not os.path.isfile(path):
        raise ValueError(f'MetaHuman DNA file is missing: {path or "no file linked"}')
    if os.path.splitext(path)[1].lower() != '.dna':
        raise ValueError('MetaHuman requires a .dna file (not .mhb, FBX or USD)')
    dna = _sdk()
    stream = dna.FileStream(path, dna.FileStream.AccessMode_Read, dna.FileStream.OpenMode_Binary)
    reader = dna.BinaryStreamReader(stream, dna.DataLayer_All)
    reader.read()
    if not dna.Status.isOk():
        status = dna.Status.get()
        raise ValueError(f'Epic DNA reader rejected this file: {status.message}. '
                         'Check corruption and reader/DNA version compatibility; '
                         'public DNA Calibration does not support UE 5.6+ DNA.')
    stage = stage_from_reader(reader, lod, dna)
    abs_path = os.path.abspath(path)
    stage.GetDefaultPrim().SetCustomDataByKey('sublayer:dnaSource', abs_path)
    skel = stage.GetPrimAtPath('/MetaHuman/Skeleton')
    if skel and skel.IsValid():
        skel.SetCustomDataByKey('sublayer:dnaSource', abs_path)
    return stage


def compose_dna_pair(head_stage, body_stage):
    """Compose Head and Body DNA neutral rigs under one /MetaHuman root.

    When Head and Body skeletons share joint names (standard MetaHuman DNA,
    e.g. spine_04 / neck_01 / head), stitch them into a single unified
    /MetaHuman/Skeleton so posing the body moves the head rig together.
    Otherwise fall back to separate /MetaHuman/Head and /MetaHuman/Body sub-rigs.
    """
    body_skel = UsdSkel.Skeleton.Get(body_stage, '/MetaHuman/Skeleton')
    head_skel = UsdSkel.Skeleton.Get(head_stage, '/MetaHuman/Skeleton')

    def _joint_names(skel):
        paths = list(skel.GetJointsAttr().Get() or [])
        names = list(skel.GetJointNamesAttr().Get() or [])
        if len(names) != len(paths):
            names = [p.rsplit('/', 1)[-1] for p in paths]
        return paths, names

    if body_skel and head_skel:
        body_paths, body_jnames = _joint_names(body_skel)
        head_paths, head_jnames = _joint_names(head_skel)
        body_name_to_idx = {name: i for i, name in enumerate(body_jnames)}
        anchor_h = next((i for i, name in enumerate(head_jnames) if name in body_name_to_idx), None)
        if anchor_h is not None:
            return _compose_unified_dna_pair(
                head_stage, body_stage, head_skel, body_skel,
                head_paths, head_jnames, body_paths, body_jnames,
                body_name_to_idx, anchor_h,
            )

    stage = Usd.Stage.CreateInMemory()
    root = UsdGeom.Xform.Define(stage, '/MetaHuman')
    stage.SetDefaultPrim(root.GetPrim())
    head_mpu = UsdGeom.GetStageMetersPerUnit(head_stage) or 0.01
    body_mpu = UsdGeom.GetStageMetersPerUnit(body_stage) or 0.01
    UsdGeom.SetStageUpAxis(stage, UsdGeom.GetStageUpAxis(head_stage) or 'Y')
    UsdGeom.SetStageMetersPerUnit(stage, head_mpu)

    head_prim = stage.OverridePrim('/MetaHuman/Head')
    head_prim.GetReferences().AddReference(
        head_stage.GetRootLayer().identifier,
        head_stage.GetDefaultPrim().GetPath(),
    )
    body_prim = stage.OverridePrim('/MetaHuman/Body')
    body_prim.GetReferences().AddReference(
        body_stage.GetRootLayer().identifier,
        body_stage.GetDefaultPrim().GetPath(),
    )
    if abs(body_mpu - head_mpu) > 1e-9:
        scale_factor = body_mpu / head_mpu
        UsdGeom.XformCommonAPI(body_prim).SetScale(Gf.Vec3f(scale_factor, scale_factor, scale_factor))
    return stage


def _compose_unified_dna_pair(
    head_stage, body_stage, head_skel, body_skel,
    head_paths, head_jnames, body_paths, body_jnames,
    body_name_to_idx, anchor_h,
):
    body_mpu = UsdGeom.GetStageMetersPerUnit(body_stage) or 0.01
    head_mpu = UsdGeom.GetStageMetersPerUnit(head_stage) or 0.01
    unit_scale = head_mpu / body_mpu

    body_rests = list(body_skel.GetRestTransformsAttr().Get() or [])
    body_binds = list(body_skel.GetBindTransformsAttr().Get() or [])
    head_rests = list(head_skel.GetRestTransformsAttr().Get() or [])
    head_binds = list(head_skel.GetBindTransformsAttr().Get() or [])

    if abs(unit_scale - 1.0) > 1e-9:
        def _scale_mat_t(m, s):
            out = Gf.Matrix4d(m)
            t = out.ExtractTranslation()
            out.SetTranslateOnly(Gf.Vec3d(t[0] * s, t[1] * s, t[2] * s))
            return out
        head_rests = [_scale_mat_t(m, unit_scale) for m in head_rests]
        head_binds = [_scale_mat_t(m, unit_scale) for m in head_binds]

    anchor_b = body_name_to_idx[head_jnames[anchor_h]]
    # Rigidly align head's neutral anchor joint onto body's neutral anchor joint.
    geom_align = head_binds[anchor_h].GetInverse() * body_binds[anchor_b]
    if abs(unit_scale - 1.0) > 1e-9:
        geom_align = Gf.Matrix4d().SetScale(Gf.Vec3d(unit_scale, unit_scale, unit_scale)) * geom_align

    uni_paths = list(body_paths)
    uni_jnames = list(body_jnames)
    uni_rests = list(body_rests)
    uni_binds = list(body_binds)
    uni_path_set = set(uni_paths)
    head_path_to_idx = {p: i for i, p in enumerate(head_paths)}
    head_to_uni = {}

    for hi, hpath in enumerate(head_paths):
        hname = head_jnames[hi]
        if hname in body_name_to_idx:
            head_to_uni[hi] = body_name_to_idx[hname]
            continue
        if '/' in hpath:
            parent_hpath, leaf = hpath.rsplit('/', 1)
            parent_ui = head_to_uni[head_path_to_idx[parent_hpath]]
            new_path = f'{uni_paths[parent_ui]}/{leaf}'
            new_rest = head_rests[hi]
            new_bind = new_rest * uni_binds[parent_ui]
        else:
            new_path = hpath
            new_rest = head_rests[hi] * geom_align
            new_bind = new_rest
        if new_path in uni_path_set:
            new_path = f'{new_path}_head_{hi}'
        ui = len(uni_paths)
        uni_paths.append(new_path)
        uni_jnames.append(hname)
        uni_rests.append(new_rest)
        uni_binds.append(new_bind)
        uni_path_set.add(new_path)
        head_to_uni[hi] = ui

    stage = Usd.Stage.CreateInMemory()
    root = UsdSkel.Root.Define(stage, '/MetaHuman')
    stage.SetDefaultPrim(root.GetPrim())
    UsdGeom.SetStageUpAxis(stage, UsdGeom.GetStageUpAxis(body_stage) or 'Y')
    UsdGeom.SetStageMetersPerUnit(stage, body_mpu)

    skel = UsdSkel.Skeleton.Define(stage, '/MetaHuman/Skeleton')
    skel.CreateJointsAttr(uni_paths)
    skel.CreateJointNamesAttr(uni_jnames)
    skel.CreateRestTransformsAttr(uni_rests)
    skel.CreateBindTransformsAttr(uni_binds)
    body_src = body_stage.GetDefaultPrim().GetCustomDataByKey('sublayer:dnaSource')
    head_src = head_stage.GetDefaultPrim().GetCustomDataByKey('sublayer:dnaSource')
    if body_src:
        skel.GetPrim().SetCustomDataByKey('sublayer:bodyDnaSource', str(body_src))
        root.GetPrim().SetCustomDataByKey('sublayer:bodyDnaSource', str(body_src))
    if head_src:
        skel.GetPrim().SetCustomDataByKey('sublayer:headDnaSource', str(head_src))
        root.GetPrim().SetCustomDataByKey('sublayer:headDnaSource', str(head_src))

    # Combine blend shape channels from body and head animations
    channels = []
    seen_ch = set()
    for src_stage in (body_stage, head_stage):
        anim_prim = src_stage.GetPrimAtPath('/MetaHuman/BlendShapeWeights')
        if anim_prim and anim_prim.IsValid():
            for ch in list(UsdSkel.Animation(anim_prim).GetBlendShapesAttr().Get() or []):
                if ch not in seen_ch:
                    seen_ch.add(ch)
                    channels.append(ch)
    animation = UsdSkel.Animation.Define(stage, '/MetaHuman/BlendShapeWeights')
    UsdSkel.BindingAPI.Apply(skel.GetPrim()).CreateAnimationSourceRel().SetTargets([animation.GetPath()])
    animation.CreateBlendShapesAttr(channels)
    animation.CreateBlendShapeWeightsAttr([0.0] * len(channels))

    dst_layer = stage.GetRootLayer()
    UsdGeom.Scope.Define(stage, '/MetaHuman/Body')
    UsdGeom.Scope.Define(stage, '/MetaHuman/Head')

    def _copy_meshes(src_stage, dst_scope, remap_indices=None, geom_bind=None):
        src_layer = src_stage.GetRootLayer()
        src_root = src_stage.GetDefaultPrim()
        for child in src_root.GetChildren():
            if not child.IsA(UsdGeom.Mesh):
                continue
            dst_path = Sdf.Path(f'{dst_scope}/{child.GetName()}')
            Sdf.CopySpec(src_layer, child.GetPath(), dst_layer, dst_path)
            mesh_prim = stage.GetPrimAtPath(dst_path)
            binding = UsdSkel.BindingAPI.Apply(mesh_prim)
            binding.CreateSkeletonRel().SetTargets([skel.GetPath()])
            if geom_bind is not None:
                binding.CreateGeomBindTransformAttr(geom_bind)
            if remap_indices is not None:
                ji_pv = binding.GetJointIndicesPrimvar()
                if ji_pv and ji_pv.IsDefined():
                    old_vals = list(ji_pv.Get() or [])
                    ji_pv.Set([remap_indices[j] for j in old_vals])
            bs_rel = binding.GetBlendShapeTargetsRel()
            if bs_rel and bs_rel.HasAuthoredTargets():
                new_targets = [
                    dst_path.AppendChild(t.name)
                    for t in bs_rel.GetTargets()
                ]
                bs_rel.SetTargets(new_targets)

    _copy_meshes(body_stage, '/MetaHuman/Body')
    _copy_meshes(head_stage, '/MetaHuman/Head', remap_indices=head_to_uni, geom_bind=geom_align)
    return stage


def _vec(value):
    # Epic Python wrappers expose vectors as tuples.
    result = tuple(float(v) for v in value)
    if len(result) != 3 or not all(math.isfinite(v) for v in result):
        raise ValueError('DNA contains a non-finite or invalid vector')
    return result


def _indices(values, size, what):
    values = list(values)
    if any(not isinstance(v, int) or v < 0 or v >= size for v in values):
        raise ValueError(f'DNA contains invalid {what} indices')
    return values


def stage_from_reader(reader, lod, dna):
    """Retain DNA vertex order; UV/normal indices are per-corner, never welded."""
    if isinstance(lod, bool) or not isinstance(lod, int) or not 0 <= lod < reader.getLODCount():
        raise ValueError(f'DNA LOD {lod} is unavailable (count: {reader.getLODCount()})')
    coordinate = reader.getCoordinateSystem()
    def component(value, axis):
        return getattr(value, axis + 'Axis') if hasattr(value, axis + 'Axis') else getattr(value, axis)
    # Fail closed instead of silently mirroring uncommon coordinate conventions.
    if tuple(component(coordinate, a) for a in 'xyz') != (
            dna.Direction_left, dna.Direction_up, dna.Direction_front):
        raise ValueError('Unsupported DNA coordinates: expected left/up/front (Maya Y-up). No conversion guessed.')
    unit = reader.getTranslationUnit()
    if unit not in (dna.TranslationUnit_cm, dna.TranslationUnit_m):
        raise ValueError('Unsupported DNA translation unit')
    rotation_unit = reader.getRotationUnit()
    if rotation_unit not in (dna.RotationUnit_degrees, dna.RotationUnit_radians):
        raise ValueError('Unsupported DNA rotation unit')
    sequence, signs, orientation = 'xyz', (1, 1, 1), 'rightHanded'
    if hasattr(reader, 'getRotationSequence'):
        sequences = {getattr(dna, 'RotationSequence_' + s): s for s in ('xyz', 'xzy', 'yxz', 'yzx', 'zxy', 'zyx')}
        sequence = sequences.get(reader.getRotationSequence())
        sign = reader.getRotationSign()
        signs = tuple(component(sign, a) for a in 'xyz')
        if not sequence or any(s not in (-1, 1) for s in signs):
            raise ValueError('Unsupported DNA rotation sequence/sign')
        winding = reader.getFaceWindingOrder()
        if winding not in (dna.FaceWindingOrder_ccw, dna.FaceWindingOrder_cw):
            raise ValueError('Unsupported DNA face winding order')
        orientation = 'rightHanded' if winding == dna.FaceWindingOrder_ccw else 'leftHanded'
    stage = Usd.Stage.CreateInMemory()
    root = UsdSkel.Root.Define(stage, '/MetaHuman')
    stage.SetDefaultPrim(root.GetPrim())
    UsdGeom.SetStageUpAxis(stage, 'Y')
    UsdGeom.SetStageMetersPerUnit(stage, 0.01 if unit == dna.TranslationUnit_cm else 1.0)
    root.GetPrim().SetCustomDataByKey('sublayer:dnaName', reader.getName())
    root.GetPrim().SetCustomDataByKey('sublayer:dnaLOD', lod)
    if hasattr(reader, 'getFileFormatGeneration'):
        root.GetPrim().SetCustomDataByKey('sublayer:dnaVersion',
            f'{reader.getFileFormatGeneration()}.{reader.getFileFormatVersion()}')
    root.GetPrim().SetCustomDataByKey('sublayer:dnaRigLogic', 'not evaluated; neutral rig and blend shapes only')
    count = reader.getJointCount()
    if not count:
        raise ValueError('DNA has no joints; a complete definition/geometry data layer is required')
    names, rests, binds = [], [], []
    # DNA indices need not be topological. Emit parent-first USD joint order and
    # explicitly remap every skin influence from original DNA indices.
    remap, visiting = {}, set()

    def joint(i):
        if i in remap:
            return remap[i]
        if i in visiting:
            raise ValueError('DNA joint hierarchy contains a cycle')
        visiting.add(i)
        parent = reader.getJointParentIndex(i)
        if not 0 <= parent < count:
            raise ValueError('DNA joint parent is out of range')
        p = None if parent == i else joint(parent)
        name = Tf.MakeValidIdentifier(reader.getJointName(i)) or f'Joint_{i}'
        path = f'{names[p]}/{name}' if p is not None else name
        if path in names:
            path += f'_{i}'
        names.append(path)
        translation = _vec(reader.getNeutralJointTranslation(i))
        angles = _vec(reader.getNeutralJointRotation(i))
        if rotation_unit == dna.RotationUnit_radians:
            angles = tuple(math.degrees(v) for v in angles)
        rotation = Gf.Matrix4d(1)
        axes = (Gf.Vec3d.XAxis(), Gf.Vec3d.YAxis(), Gf.Vec3d.ZAxis())
        for token in sequence:
            j = 'xyz'.index(token)
            rotation = rotation * Gf.Matrix4d().SetRotate(Gf.Rotation(axes[j], angles[j] * signs[j]))
        rotation.SetTranslateOnly(Gf.Vec3d(*translation))
        index = len(rests)
        rests.append(rotation)
        binds.append(rotation * binds[p] if p is not None else rotation)
        remap[i] = index
        visiting.remove(i)
        return index

    for i in range(count):
        joint(i)
    skeleton = UsdSkel.Skeleton.Define(stage, '/MetaHuman/Skeleton')
    skeleton.CreateJointsAttr(names)
    skeleton.CreateJointNamesAttr([reader.getJointName(i) for i in sorted(remap, key=remap.get)])
    skeleton.CreateRestTransformsAttr(rests)
    skeleton.CreateBindTransformsAttr(binds)
    animation = UsdSkel.Animation.Define(stage, '/MetaHuman/BlendShapeWeights')
    UsdSkel.BindingAPI.Apply(skeleton.GetPrim()).CreateAnimationSourceRel().SetTargets([animation.GetPath()])
    channel_names = [str(reader.getBlendShapeChannelName(i))
                     for i in range(reader.getBlendShapeChannelCount())]
    if any(not name for name in channel_names) or len(set(channel_names)) != len(channel_names):
        raise ValueError('DNA blend shape channel names are empty or duplicated')
    animation.CreateBlendShapesAttr(channel_names)
    animation.CreateBlendShapeWeightsAttr([0.0] * len(channel_names))
    mesh_ids = _indices(reader.getMeshIndicesForLOD(lod), reader.getMeshCount(), 'mesh')
    if not mesh_ids:
        raise ValueError('Selected DNA LOD contains no meshes')
    for mesh_id in mesh_ids:
        name = f'{Tf.MakeValidIdentifier(reader.getMeshName(mesh_id))}_{mesh_id}'
        mesh = UsdGeom.Mesh.Define(stage, f'/MetaHuman/{name}')
        mesh.GetPrim().SetCustomDataByKey('sublayer:dnaMeshIndex', mesh_id)
        n = reader.getVertexPositionCount(mesh_id)
        points = [_vec(reader.getVertexPosition(mesh_id, i)) for i in range(n)]
        mesh.CreatePointsAttr(points)
        layout_count = reader.getVertexLayoutCount(mesh_id)
        positions = _indices(reader.getVertexLayoutPositionIndices(mesh_id), n, 'vertex layout')
        if len(positions) != layout_count:
            raise ValueError('DNA vertex layout size mismatch')
        corners, counts = [], []
        for i in range(reader.getFaceCount(mesh_id)):
            face = _indices(reader.getFaceVertexLayoutIndices(mesh_id, i), layout_count, 'face layout')
            if len(face) < 3:
                raise ValueError('DNA contains a non-polygon face')
            counts.append(len(face))
            corners.extend(face)
        mesh.CreateFaceVertexCountsAttr(counts)
        mesh.CreateFaceVertexIndicesAttr([positions[i] for i in corners])
        mesh.CreateSubdivisionSchemeAttr('none')
        mesh.CreateOrientationAttr(orientation)
        uv_count = reader.getVertexTextureCoordinateCount(mesh_id)
        if uv_count:
            uv_ids = _indices(reader.getVertexLayoutTextureCoordinateIndices(mesh_id), uv_count, 'UV')
            if len(uv_ids) != layout_count:
                raise ValueError('DNA UV layout size mismatch')
            uvs = [reader.getVertexTextureCoordinate(mesh_id, i) for i in range(uv_count)]
            if any(len(v) != 2 or not all(math.isfinite(x) for x in v) for v in uvs):
                raise ValueError('DNA contains invalid UV values')
            st = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st', Sdf.ValueTypeNames.TexCoord2fArray, 'faceVarying')
            st.Set(uvs)
            st.SetIndices([uv_ids[i] for i in corners])
        normal_count = reader.getVertexNormalCount(mesh_id)
        if normal_count:
            normal_ids = _indices(reader.getVertexLayoutNormalIndices(mesh_id), normal_count, 'normal')
            if len(normal_ids) != layout_count:
                raise ValueError('DNA normal layout size mismatch')
            normals = [_vec(reader.getVertexNormal(mesh_id, i)) for i in range(normal_count)]
            mesh.CreateNormalsAttr([normals[normal_ids[i]] for i in corners])
            mesh.SetNormalsInterpolation('faceVarying')
        if reader.getSkinWeightsCount(mesh_id) != n:
            raise ValueError('DNA skin weights do not match vertex count')
        rows = []
        for i in range(n):
            ids = _indices(reader.getSkinWeightsJointIndices(mesh_id, i), count, 'skin joint')
            weights = list(reader.getSkinWeightsValues(mesh_id, i))
            if (len(ids) != len(weights) or not weights or
                    any(not math.isfinite(w) or w < 0 for w in weights) or
                    abs(sum(weights) - 1.0) > 0.001):
                raise ValueError('DNA skin weights are invalid or not normalized')
            rows.append([(remap[j], w / sum(weights)) for j, w in zip(ids, weights)])
        width = max(map(len, rows), default=1)
        padded = [row + [(0, 0.0)] * (width-len(row)) for row in rows]
        binding = UsdSkel.BindingAPI.Apply(mesh.GetPrim())
        binding.CreateSkeletonRel().SetTargets([skeleton.GetPath()])
        binding.CreateGeomBindTransformAttr(Gf.Matrix4d(1))
        binding.CreateJointIndicesPrimvar(False, width).Set([j for row in padded for j, _ in row])
        binding.CreateJointWeightsPrimvar(False, width).Set([w for row in padded for _, w in row])
        targets, channels = [], []
        for t in range(reader.getBlendShapeTargetCount(mesh_id)):
            channel = reader.getBlendShapeChannelIndex(mesh_id, t)
            _indices([channel], len(channel_names), 'blend shape channel')
            indices = _indices(reader.getBlendShapeTargetVertexIndices(mesh_id, t), n, 'blend shape vertex')
            offsets = [_vec(reader.getBlendShapeTargetDelta(mesh_id, t, i)) for i in range(reader.getBlendShapeTargetDeltaCount(mesh_id, t))]
            if len(indices) != len(offsets) or len(set(indices)) != len(indices):
                raise ValueError('DNA blend shape indices/deltas mismatch')
            # USD sparse point indices must be ascending; reorder offsets together.
            pairs = sorted(zip(indices, offsets))
            shape = UsdSkel.BlendShape.Define(stage, f'{mesh.GetPath()}/BlendShape_{t}')
            shape.CreatePointIndicesAttr([i for i, _ in pairs])
            shape.CreateOffsetsAttr(Vt.Vec3fArray([v for _, v in pairs]))
            targets.append(shape.GetPath())
            channels.append(channel_names[channel])
        if targets:
            binding.CreateBlendShapesAttr(channels)
            binding.CreateBlendShapeTargetsRel().SetTargets(targets)
    return stage


_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.exr', '.tif', '.tiff', '.tga', '.bmp'}

_SLOT_DEFAULTS = {
    'head': {'baseColor': (0.72, 0.56, 0.48), 'specular': 0.35, 'roughness': 0.45, 'subsurface': 0.5, 'sssRadius': (1.0, 0.25, 0.2), 'sssScale': 0.012},
    'body': {'baseColor': (0.72, 0.56, 0.48), 'specular': 0.35, 'roughness': 0.48, 'subsurface': 0.5, 'sssRadius': (1.0, 0.25, 0.2), 'sssScale': 0.012},
    'teeth': {'baseColor': (0.86, 0.84, 0.78), 'specular': 0.5, 'roughness': 0.2},
    'saliva': {'baseColor': (0.92, 0.92, 0.92), 'specular': 0.4, 'roughness': 0.05, 'opacity': 0.15},
    'eye': {'baseColor': (0.88, 0.86, 0.84), 'specular': 0.6, 'roughness': 0.06, 'coat': 0.85, 'coatRoughness': 0.03},
    # MetaHuman eyeshell is a UE translucent eye-occlusion shell; without UE's custom
    # shadow shader, refractive transmission creates a milky veil and chrome highlight over the iris.
    'eyeshell': {'baseColor': (0.05, 0.04, 0.04), 'specular': 0.0, 'roughness': 1.0, 'opacity': 0.0},
    'eyelashes': {'baseColor': (0.03, 0.025, 0.02), 'specular': 0.15, 'roughness': 0.48},
    # Thin single-sided tearline strip: soft translucent sheen without solid-glass refraction.
    'eyeedge': {'baseColor': (0.75, 0.62, 0.60), 'specular': 0.35, 'roughness': 0.08, 'opacity': 0.18},
    'cartilage': {'baseColor': (0.65, 0.32, 0.28), 'specular': 0.45, 'roughness': 0.20},
}


def _classify_mesh_slot(mesh_name: str, prim_path: str) -> str:
    low = mesh_name.lower()
    if 'eyeshell' in low or 'eye_shell' in low or 'cornea' in low:
        return 'eyeshell'
    if 'eyeedge' in low or 'eye_edge' in low or 'tearline' in low:
        return 'eyeedge'
    if 'eyelash' in low or 'lash' in low:
        return 'eyelashes'
    if 'cartilage' in low or 'lacrimal' in low or 'caruncle' in low:
        return 'cartilage'
    if 'saliva' in low:
        return 'saliva'
    if 'teeth' in low or 'tooth' in low:
        return 'teeth'
    if 'eye' in low:
        return 'eye'
    if 'body' in low or '/body/' in prim_path.lower():
        return 'body'
    if 'head' in low or 'face' in low or '/head/' in prim_path.lower():
        return 'head'
    return 'head'


def _classify_texture(path: Path):
    """Return (slot, channel, priority) or None for a candidate MetaHuman texture."""
    stem = path.stem.lower()
    parent = path.parent.name.lower()
    combined = f'{parent}/{stem}'

    # Skip wrinkle / animated blend maps in favor of main neutral textures
    if re.search(r'(?:^|[_\-])wm[0-9]|wrinkle|animated', stem):
        return None

    # Channel classification
    channel = None
    priority = 0
    if re.search(r'(?:^|[_\-])(basecolor|base_color|albedo|diffuse|color|col|bc|main_color)(?:$|[_\-0-9])', stem) or stem.endswith(('_d', '_c')):
        channel = 'baseColor'
        priority = 2 if ('basecolor' in stem or 'albedo' in stem or 'diffuse' in stem) else 1
    elif re.search(r'(?:^|[_\-])(normal|nrm|norm)(?:$|[_\-0-9])', stem) or stem.endswith('_n'):
        channel = 'normal'
        priority = 2 if 'normal' in stem else 1
    elif re.search(r'(?:^|[_\-])(roughness|rough)(?:$|[_\-0-9])', stem) or stem.endswith('_r'):
        channel = 'roughness'
        priority = 2 if 'roughness' in stem else 1
    elif re.search(r'(?:^|[_\-])(metallic|metalness|metal)(?:$|[_\-0-9])', stem) or stem.endswith('_m'):
        channel = 'metallic'
        priority = 2 if 'metallic' in stem else 1
    elif re.search(r'(?:^|[_\-])(opacity|alpha|mask|transp)(?:$|[_\-0-9])', stem) or stem.endswith('_a'):
        channel = 'opacity'
        priority = 2 if ('opacity' in stem or 'alpha' in stem or 'mask' in stem) else 1
    elif re.search(r'eyelash|lash', stem) and not re.search(r'(?:^|[_\-])(cavity|ao|spec|sss|subsurface|gloss|disp|height|bump)', stem):
        channel = 'baseColor'
        priority = 0
    if not channel:
        return None

    # Slot classification
    if 'eyelash' in combined or 'lash' in combined:
        slot = 'eyelashes'
    elif 'teeth' in combined:
        slot = 'teeth'
    elif 'saliva' in combined:
        slot = 'saliva'
    elif 'cartilage' in combined or 'lacrimal' in combined:
        slot = 'cartilage'
    elif 'eyeshell' in combined:
        slot = 'eyeshell'
    elif 'eyeedge' in combined:
        slot = 'eyeedge'
    elif 'eye' in combined or 'iris' in combined or 'sclera' in combined:
        slot = 'eye'
    elif 'body' in combined:
        slot = 'body'
    elif 'head' in combined or 'face' in combined or 'skin' in combined:
        slot = 'head'
    else:
        return None

    return slot, channel, priority


def _discover_metahuman_textures(dna_paths) -> dict[str, dict[str, str]]:
    """Read-only bounded scan for MetaHuman PBR textures alongside linked .dna files."""
    search_dirs: list[Path] = []
    seen_dirs: set[str] = set()

    def _add_dir(d: Path):
        try:
            if not d.is_dir() or d.is_symlink():
                return
            key = os.path.normcase(str(d.resolve()))
            if key not in seen_dirs:
                seen_dirs.add(key)
                search_dirs.append(d)
        except OSError:
            pass

    for raw in dna_paths:
        if not raw:
            continue
        p = Path(raw)
        base = p.parent
        _add_dir(base)
        for sub in ('Textures', 'Maps', 'SourceAssets', 'textures', 'maps'):
            _add_dir(base / sub)
        parent = base.parent
        if parent and parent != base:
            for sub in ('Textures', 'Maps', 'SourceAssets', 'Head', 'Face', 'Body'):
                _add_dir(parent / sub)
                _add_dir(parent / sub / 'Textures')
                _add_dir(parent / sub / 'Maps')

    found: dict[str, dict[str, tuple[int, str]]] = {}
    scanned = 0
    for d in search_dirs:
        if scanned >= 500:
            break
        try:
            for entry in os.scandir(d):
                if scanned >= 500:
                    break
                if entry.is_symlink() or not entry.is_file():
                    continue
                ep = Path(entry.path)
                if ep.suffix.lower() not in _IMAGE_EXTS:
                    continue
                scanned += 1
                res = _classify_texture(ep)
                if not res:
                    continue
                slot, channel, prio = res
                slot_map = found.setdefault(slot, {})
                prev = slot_map.get(channel)
                if prev is None or prio > prev[0]:
                    slot_map[channel] = (prio, os.path.abspath(entry.path))
        except OSError:
            continue

    return {slot: {ch: path for ch, (_, path) in chs.items()} for slot, chs in found.items()}


def _image_has_transparency(path: str) -> bool:
    """Zero-dependency read-only check for whether an image carries an alpha channel."""
    ext = os.path.splitext(path)[1].lower()
    try:
        with open(path, 'rb') as f:
            head = f.read(4096)
        if ext == '.png' and len(head) >= 26 and head[:8] == b'\x89PNG\r\n\x1a\n':
            # IHDR color type at byte 25: 4 = Grayscale+Alpha, 6 = RGBA; or tRNS chunk for indexed/RGB.
            color_type = head[25]
            return color_type in (4, 6) or (b'tRNS' in head)
        if ext == '.tga' and len(head) >= 18:
            pixel_depth = head[16]
            alpha_bits = head[17] & 0x0F
            return pixel_depth == 32 or alpha_bits > 0
        if ext == '.exr' and head[:4] == b'\x76\x2f\x31\x01':
            return b'\x00A\x00' in head or b'channels\x00' in head and b'\x00a\x00' in head.lower()
    except OSError:
        return False
    try:
        from PIL import Image
        with Image.open(path) as img:
            if img.mode in ('RGBA', 'LA', 'PA'):
                alpha_extrema = img.getextrema()[-1]
                return isinstance(alpha_extrema, tuple) and alpha_extrema[0] < 255
            if img.mode == 'P' and 'transparency' in img.info:
                return True
    except Exception:
        return False
    return False


def _author_metahuman_material(stage: Usd.Stage, mat_path: Sdf.Path, slot: str, channels: dict[str, str]) -> UsdShade.Material:
    """Author a dual-context MaterialX (Standard Surface) + UsdPreviewSurface material."""
    defaults = _SLOT_DEFAULTS.get(slot, _SLOT_DEFAULTS['head'])
    material = UsdShade.Material.Define(stage, mat_path)

    mtlx = UsdShade.Shader.Define(stage, mat_path.AppendChild('StandardSurface'))
    mtlx.CreateIdAttr('ND_standard_surface_surfaceshader')
    mtlx.CreateInput('base', Sdf.ValueTypeNames.Float).Set(1.0)
    mtlx.CreateInput('specular', Sdf.ValueTypeNames.Float).Set(float(defaults.get('specular', 0.5)))
    mtlx_out = mtlx.CreateOutput('out', Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput('mtlx').ConnectToSource(mtlx_out)

    preview = UsdShade.Shader.Define(stage, mat_path.AppendChild('PreviewSurface'))
    preview.CreateIdAttr('UsdPreviewSurface')
    preview_out = preview.CreateOutput('surface', Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput().ConnectToSource(preview_out)

    uv_mtlx_out = None
    uv_prev_out = None
    if channels:
        uv_mtlx = UsdShade.Shader.Define(stage, mat_path.AppendChild('MtlxUV'))
        uv_mtlx.CreateIdAttr('ND_geompropvalue_vector2')
        uv_mtlx.CreateInput('geomprop', Sdf.ValueTypeNames.String).Set('st')
        uv_mtlx_out = uv_mtlx.CreateOutput('out', Sdf.ValueTypeNames.Float2)

        uv_prev = UsdShade.Shader.Define(stage, mat_path.AppendChild('PreviewUV'))
        uv_prev.CreateIdAttr('UsdPrimvarReader_float2')
        uv_prev.CreateInput('varname', Sdf.ValueTypeNames.Token).Set('st')
        uv_prev_out = uv_prev.CreateOutput('result', Sdf.ValueTypeNames.Float2)

    def _mtlx_img(child: str, path: str, is_color: bool, cs: str):
        img = UsdShade.Shader.Define(stage, mat_path.AppendChild(child))
        img.CreateIdAttr('ND_image_color3' if is_color else 'ND_image_float')
        f_in = img.CreateInput('file', Sdf.ValueTypeNames.Asset)
        f_in.Set(Sdf.AssetPath(path))
        f_in.GetAttr().SetColorSpace(cs)
        img.CreateInput('texcoord', Sdf.ValueTypeNames.Float2).ConnectToSource(uv_mtlx_out)
        return img.CreateOutput('out', Sdf.ValueTypeNames.Color3f if is_color else Sdf.ValueTypeNames.Float)

    def _prev_img(child: str, path: str, cs: str):
        tex = UsdShade.Shader.Define(stage, mat_path.AppendChild(child))
        tex.CreateIdAttr('UsdUVTexture')
        tex.CreateInput('file', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(path))
        tex.CreateInput('sourceColorSpace', Sdf.ValueTypeNames.Token).Set(cs)
        tex.CreateInput('st', Sdf.ValueTypeNames.Float2).ConnectToSource(uv_prev_out)
        return tex

    base_rgb = defaults.get('baseColor', (0.72, 0.56, 0.48))
    base_color_mtlx_out = None
    # Only translucent/card slots may use alpha/opacity cutouts. Skin (head/body),
    # eyes, teeth, and cartilage are always opaque; any alpha channel or *_mask
    # texture on those slots in MetaHuman exports is a region/clothing/cavity mask,
    # NOT transparency.
    allows_opacity_map = slot in ('eyelashes', 'eyeshell', 'eyeedge', 'saliva')
    use_rgba_alpha = (
        slot == 'eyelashes'
        and not channels.get('opacity')
        and bool(channels.get('baseColor'))
        and _image_has_transparency(channels['baseColor'])
    )
    # MetaHuman eyelash textures are white-on-black/transparent strand masks meant to be
    # tinted by dark hair melanin rather than used as white diffuse color.
    eyelash_mask_from_color = slot == 'eyelashes' and not channels.get('opacity') and bool(channels.get('baseColor'))

    if eyelash_mask_from_color:
        p = channels['baseColor']
        c = Gf.Vec3f(*base_rgb)
        mtlx.CreateInput('base_color', Sdf.ValueTypeNames.Color3f).Set(c)
        preview.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(c)
        pt = _prev_img('PreviewBaseColor', p, 'raw')
        if use_rgba_alpha:
            rgba_img = UsdShade.Shader.Define(stage, mat_path.AppendChild('MtlxBaseColorRGBA'))
            rgba_img.CreateIdAttr('ND_image_color4')
            f_in = rgba_img.CreateInput('file', Sdf.ValueTypeNames.Asset)
            f_in.Set(Sdf.AssetPath(p))
            f_in.GetAttr().SetColorSpace('raw')
            rgba_img.CreateInput('texcoord', Sdf.ValueTypeNames.Float2).ConnectToSource(uv_mtlx_out)
            rgba_out = rgba_img.CreateOutput('out', Sdf.ValueTypeNames.Color4f)

            alpha_ext = UsdShade.Shader.Define(stage, mat_path.AppendChild('MtlxAlphaExtract'))
            alpha_ext.CreateIdAttr('ND_extract_color4')
            alpha_ext.CreateInput('in', Sdf.ValueTypeNames.Color4f).ConnectToSource(rgba_out)
            alpha_ext.CreateInput('index', Sdf.ValueTypeNames.Int).Set(3)
            alpha_out = alpha_ext.CreateOutput('out', Sdf.ValueTypeNames.Float)

            op_c = UsdShade.Shader.Define(stage, mat_path.AppendChild('MtlxOpacityColor'))
            op_c.CreateIdAttr('ND_convert_float_color3')
            op_c.CreateInput('in', Sdf.ValueTypeNames.Float).ConnectToSource(alpha_out)
            mtlx.CreateInput('opacity', Sdf.ValueTypeNames.Color3f).ConnectToSource(
                op_c.CreateOutput('out', Sdf.ValueTypeNames.Color3f)
            )
            preview.CreateInput('opacity', Sdf.ValueTypeNames.Float).ConnectToSource(pt.CreateOutput('a', Sdf.ValueTypeNames.Float))
        else:
            op_out = _mtlx_img('MtlxOpacity', p, False, 'raw')
            op_c = UsdShade.Shader.Define(stage, mat_path.AppendChild('MtlxOpacityColor'))
            op_c.CreateIdAttr('ND_convert_float_color3')
            op_c.CreateInput('in', Sdf.ValueTypeNames.Float).ConnectToSource(op_out)
            mtlx.CreateInput('opacity', Sdf.ValueTypeNames.Color3f).ConnectToSource(
                op_c.CreateOutput('out', Sdf.ValueTypeNames.Color3f)
            )
            preview.CreateInput('opacity', Sdf.ValueTypeNames.Float).ConnectToSource(pt.CreateOutput('r', Sdf.ValueTypeNames.Float))
        preview.CreateInput('opacityThreshold', Sdf.ValueTypeNames.Float).Set(0.05)
    elif channels.get('baseColor'):
        p = channels['baseColor']
        pt = _prev_img('PreviewBaseColor', p, 'sRGB')
        preview.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).ConnectToSource(pt.CreateOutput('rgb', Sdf.ValueTypeNames.Float3))
        if use_rgba_alpha:
            rgba_img = UsdShade.Shader.Define(stage, mat_path.AppendChild('MtlxBaseColorRGBA'))
            rgba_img.CreateIdAttr('ND_image_color4')
            f_in = rgba_img.CreateInput('file', Sdf.ValueTypeNames.Asset)
            f_in.Set(Sdf.AssetPath(p))
            f_in.GetAttr().SetColorSpace('srgb_texture')
            rgba_img.CreateInput('texcoord', Sdf.ValueTypeNames.Float2).ConnectToSource(uv_mtlx_out)
            rgba_out = rgba_img.CreateOutput('out', Sdf.ValueTypeNames.Color4f)

            rgb_conv = UsdShade.Shader.Define(stage, mat_path.AppendChild('MtlxBaseColorRGB'))
            rgb_conv.CreateIdAttr('ND_convert_color4_color3')
            rgb_conv.CreateInput('in', Sdf.ValueTypeNames.Color4f).ConnectToSource(rgba_out)
            base_color_mtlx_out = rgb_conv.CreateOutput('out', Sdf.ValueTypeNames.Color3f)
            mtlx.CreateInput('base_color', Sdf.ValueTypeNames.Color3f).ConnectToSource(base_color_mtlx_out)

            alpha_ext = UsdShade.Shader.Define(stage, mat_path.AppendChild('MtlxAlphaExtract'))
            alpha_ext.CreateIdAttr('ND_extract_color4')
            alpha_ext.CreateInput('in', Sdf.ValueTypeNames.Color4f).ConnectToSource(rgba_out)
            alpha_ext.CreateInput('index', Sdf.ValueTypeNames.Int).Set(3)
            alpha_out = alpha_ext.CreateOutput('out', Sdf.ValueTypeNames.Float)

            op_c = UsdShade.Shader.Define(stage, mat_path.AppendChild('MtlxOpacityColor'))
            op_c.CreateIdAttr('ND_convert_float_color3')
            op_c.CreateInput('in', Sdf.ValueTypeNames.Float).ConnectToSource(alpha_out)
            mtlx.CreateInput('opacity', Sdf.ValueTypeNames.Color3f).ConnectToSource(
                op_c.CreateOutput('out', Sdf.ValueTypeNames.Color3f)
            )
            preview.CreateInput('opacity', Sdf.ValueTypeNames.Float).ConnectToSource(pt.CreateOutput('a', Sdf.ValueTypeNames.Float))
        else:
            base_color_mtlx_out = _mtlx_img('MtlxBaseColor', p, True, 'srgb_texture')
            mtlx.CreateInput('base_color', Sdf.ValueTypeNames.Color3f).ConnectToSource(base_color_mtlx_out)
    else:
        c = Gf.Vec3f(*base_rgb)
        mtlx.CreateInput('base_color', Sdf.ValueTypeNames.Color3f).Set(c)
        preview.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(c)

    if channels.get('roughness'):
        p = channels['roughness']
        mtlx.CreateInput('specular_roughness', Sdf.ValueTypeNames.Float).ConnectToSource(_mtlx_img('MtlxRoughness', p, False, 'raw'))
        pt = _prev_img('PreviewRoughness', p, 'raw')
        preview.CreateInput('roughness', Sdf.ValueTypeNames.Float).ConnectToSource(pt.CreateOutput('r', Sdf.ValueTypeNames.Float))
    else:
        r = float(defaults.get('roughness', 0.45))
        mtlx.CreateInput('specular_roughness', Sdf.ValueTypeNames.Float).Set(r)
        preview.CreateInput('roughness', Sdf.ValueTypeNames.Float).Set(r)

    if channels.get('metallic'):
        p = channels['metallic']
        mtlx.CreateInput('metalness', Sdf.ValueTypeNames.Float).ConnectToSource(_mtlx_img('MtlxMetallic', p, False, 'raw'))
        pt = _prev_img('PreviewMetallic', p, 'raw')
        preview.CreateInput('metallic', Sdf.ValueTypeNames.Float).ConnectToSource(pt.CreateOutput('r', Sdf.ValueTypeNames.Float))
    else:
        mtlx.CreateInput('metalness', Sdf.ValueTypeNames.Float).Set(0.0)
        preview.CreateInput('metallic', Sdf.ValueTypeNames.Float).Set(0.0)

    if channels.get('normal'):
        p = channels['normal']
        n_img = UsdShade.Shader.Define(stage, mat_path.AppendChild('MtlxNormalTex'))
        n_img.CreateIdAttr('ND_image_vector3')
        nf = n_img.CreateInput('file', Sdf.ValueTypeNames.Asset)
        nf.Set(Sdf.AssetPath(p))
        nf.GetAttr().SetColorSpace('raw')
        n_img.CreateInput('texcoord', Sdf.ValueTypeNames.Float2).ConnectToSource(uv_mtlx_out)
        n_map = UsdShade.Shader.Define(stage, mat_path.AppendChild('MtlxNormalMap'))
        n_map.CreateIdAttr('ND_normalmap')
        n_map.CreateInput('in', Sdf.ValueTypeNames.Vector3f).ConnectToSource(n_img.CreateOutput('out', Sdf.ValueTypeNames.Vector3f))
        mtlx.CreateInput('normal', Sdf.ValueTypeNames.Vector3f).ConnectToSource(n_map.CreateOutput('out', Sdf.ValueTypeNames.Vector3f))
        pt = _prev_img('PreviewNormal', p, 'raw')
        pt.CreateInput('scale', Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(2.0, 2.0, 2.0, 1.0))
        pt.CreateInput('bias', Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(-1.0, -1.0, -1.0, 0.0))
        preview.CreateInput('normal', Sdf.ValueTypeNames.Normal3f).ConnectToSource(pt.CreateOutput('rgb', Sdf.ValueTypeNames.Float3))

    if allows_opacity_map and channels.get('opacity'):
        p = channels['opacity']
        op_out = _mtlx_img('MtlxOpacity', p, False, 'raw')
        op_c = UsdShade.Shader.Define(stage, mat_path.AppendChild('MtlxOpacityColor'))
        op_c.CreateIdAttr('ND_convert_float_color3')
        op_c.CreateInput('in', Sdf.ValueTypeNames.Float).ConnectToSource(op_out)
        mtlx.CreateInput('opacity', Sdf.ValueTypeNames.Color3f).ConnectToSource(op_c.CreateOutput('out', Sdf.ValueTypeNames.Color3f))
        pt = _prev_img('PreviewOpacity', p, 'raw')
        preview.CreateInput('opacity', Sdf.ValueTypeNames.Float).ConnectToSource(pt.CreateOutput('r', Sdf.ValueTypeNames.Float))
        if slot == 'eyelashes':
            preview.CreateInput('opacityThreshold', Sdf.ValueTypeNames.Float).Set(0.05)
    elif not use_rgba_alpha and not eyelash_mask_from_color and 'opacity' in defaults:
        op = float(defaults['opacity'])
        mtlx.CreateInput('opacity', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(op, op, op))
        preview.CreateInput('opacity', Sdf.ValueTypeNames.Float).Set(op)

    if 'transmission' in defaults:
        mtlx.CreateInput('transmission', Sdf.ValueTypeNames.Float).Set(float(defaults['transmission']))
        mtlx.CreateInput('specular_IOR', Sdf.ValueTypeNames.Float).Set(float(defaults.get('ior', 1.38)))
    if 'coat' in defaults:
        mtlx.CreateInput('coat', Sdf.ValueTypeNames.Float).Set(float(defaults['coat']))
        mtlx.CreateInput('coat_roughness', Sdf.ValueTypeNames.Float).Set(float(defaults.get('coatRoughness', 0.05)))
        preview.CreateInput('clearcoat', Sdf.ValueTypeNames.Float).Set(float(defaults['coat']))
        preview.CreateInput('clearcoatRoughness', Sdf.ValueTypeNames.Float).Set(float(defaults.get('coatRoughness', 0.05)))
    if 'subsurface' in defaults:
        mtlx.CreateInput('subsurface', Sdf.ValueTypeNames.Float).Set(float(defaults['subsurface']))
        sss_in = mtlx.CreateInput('subsurface_color', Sdf.ValueTypeNames.Color3f)
        if base_color_mtlx_out is not None:
            sss_in.ConnectToSource(base_color_mtlx_out)
        else:
            sss_in.Set(Gf.Vec3f(*base_rgb))
        mtlx.CreateInput('subsurface_radius', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*defaults['sssRadius']))
        mtlx.CreateInput('subsurface_scale', Sdf.ValueTypeNames.Float).Set(float(defaults['sssScale']))

    return material


def apply_metahuman_materials(stage: Usd.Stage, dna_paths) -> None:
    """Discover sibling PBR textures and bind dual-context MaterialX + UsdPreviewSurface materials."""
    root = stage.GetDefaultPrim()
    if not root or not root.IsValid():
        return
    meshes = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
    if not meshes:
        return
    textures_by_slot = _discover_metahuman_textures(dna_paths)
    mat_scope_path = root.GetPath().AppendChild('_Materials')
    UsdGeom.Scope.Define(stage, mat_scope_path)
    authored: dict[str, UsdShade.Material] = {}
    for mesh_prim in meshes:
        slot = _classify_mesh_slot(mesh_prim.GetName(), str(mesh_prim.GetPath()))
        if slot not in authored:
            mat_path = mat_scope_path.AppendChild(f'MH_{Tf.MakeValidIdentifier(slot)}')
            authored[slot] = _author_metahuman_material(stage, mat_path, slot, textures_by_slot.get(slot, {}))
        UsdShade.MaterialBindingAPI.Apply(mesh_prim).Bind(authored[slot])
