"""Pure-NumPy Epic MetaHuman Body RigLogic (RBF + TwistSwing + JointGroup) evaluator.

Reads RBFBehavior, TwistSwingBehavior, and JointGroups directly from Epic's
`dna.BinaryStreamReader` (no separate py3riglogic SWIG build required) and
evaluates driven corrective helper joints (e.g. clavicle_pec, scapula,
upperarm_twist, bicep/tricep, elbow/knee correctives) from posed driver joints.
"""
from __future__ import annotations

import math
import os
import re
from typing import Any

import numpy as np
from pxr import Gf

_SOLVER_CACHE: dict[tuple[str, int, int], "BodyRigLogicSolver | None"] = {}


def _mat4_to_quat_xyzw(mat: Gf.Matrix4d) -> tuple[float, float, float, float]:
    q = mat.ExtractRotationQuat()
    im = q.GetImaginary()
    w = float(q.GetReal())
    x, y, z = float(im[0]), float(im[1]), float(im[2])
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    return (x / n, y / n, z / n, w / n)


def _quat_mul(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def _quat_inv(q: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    return (-q[0], -q[1], -q[2], q[3])


def _quat_slerp(a: tuple[float, float, float, float], b: tuple[float, float, float, float], t: float) -> tuple[float, float, float, float]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    dot = ax * bx + ay * by + az * bz + aw * bw
    if dot < 0.0:
        bx, by, bz, bw = -bx, -by, -bz, -bw
        dot = -dot
    if dot > 0.9995:
        rx = ax + t * (bx - ax)
        ry = ay + t * (by - ay)
        rz = az + t * (bz - az)
        rw = aw + t * (bw - aw)
        n = math.sqrt(rx * rx + ry * ry + rz * rz + rw * rw)
        return (rx / n, ry / n, rz / n, rw / n) if n > 1e-12 else (0.0, 0.0, 0.0, 1.0)
    theta0 = math.acos(max(-1.0, min(1.0, dot)))
    sin0 = math.sin(theta0)
    theta = theta0 * t
    s0 = math.sin(theta0 - theta) / sin0
    s1 = math.sin(theta) / sin0
    return (
        s0 * ax + s1 * bx,
        s0 * ay + s1 * by,
        s0 * az + s1 * bz,
        s0 * aw + s1 * bw,
    )


def _separate_twist(q: tuple[float, float, float, float], axis: int) -> tuple[float, float, float, float]:
    x, y, z, w = q
    if axis == 0:
        n = math.sqrt(x * x + w * w)
        return (x / n, 0.0, 0.0, w / n) if n > 1e-12 else (0.0, 0.0, 0.0, 1.0)
    if axis == 1:
        n = math.sqrt(y * y + w * w)
        return (0.0, y / n, 0.0, w / n) if n > 1e-12 else (0.0, 0.0, 0.0, 1.0)
    n = math.sqrt(z * z + w * w)
    return (0.0, 0.0, z / n, w / n) if n > 1e-12 else (0.0, 0.0, 0.0, 1.0)


def _get_swing_arr(q: np.ndarray, axis: int) -> np.ndarray:
    """Matches Epic RBFSolver.cpp getSwing<TwistAxis>."""
    out = np.zeros_like(q)
    x, y, z, w = q[..., 0], q[..., 1], q[..., 2], q[..., 3]
    if axis == 0:
        denom = -np.sqrt(np.maximum(x * x + w * w, 1e-12))
        out[..., 0] = 0.0
        out[..., 1] = (w * y - x * z) / denom
        out[..., 2] = (w * z + x * y) / denom
        out[..., 3] = denom
    elif axis == 1:
        denom = -np.sqrt(np.maximum(y * y + w * w, 1e-12))
        out[..., 0] = (w * x + y * z) / denom
        out[..., 1] = 0.0
        out[..., 2] = (w * z - y * x) / denom
        out[..., 3] = denom
    else:
        denom = -np.sqrt(np.maximum(z * z + w * w, 1e-12))
        out[..., 0] = (w * x - z * y) / denom
        out[..., 1] = (w * y + z * x) / denom
        out[..., 2] = 0.0
        out[..., 3] = denom
    return out


def _get_twist_arr(q: np.ndarray, axis: int) -> np.ndarray:
    """Matches Epic RBFSolver.cpp getTwist<TwistAxis>."""
    out = np.zeros_like(q)
    comp = q[..., axis]
    w = q[..., 3]
    mag = np.sqrt(comp * comp + w * w)
    zero = mag < 1e-12
    safe = np.where(zero, 1.0, mag)
    out[..., axis] = np.where(zero, 0.0, comp / safe)
    out[..., 3] = np.where(zero, 1.0, w / safe)
    return out


def _convert_rbf_input(vec: np.ndarray, dist_method: int, twist_axis: int) -> np.ndarray:
    """Convert raw control vector(s) of shape (..., D) for SwingAngle/TwistAngle."""
    # 0=Euclidean, 1=Quaternion, 2=SwingAngle, 3=TwistAngle
    if dist_method not in (2, 3) or vec.shape[-1] % 4 != 0:
        return vec.copy()
    reshaped = vec.reshape(*vec.shape[:-1], -1, 4)
    if dist_method == 2:
        conv = _get_swing_arr(reshaped, twist_axis)
    else:
        conv = _get_twist_arr(reshaped, twist_axis)
    return conv.reshape(vec.shape)


def _rbf_distances(targets: np.ndarray, inp: np.ndarray, dist_method: int) -> np.ndarray:
    """Compute distance from inp (D,) to targets (T, D). Matches Epic RBFSolver.cpp."""
    if dist_method == 0:  # Euclidean
        diff = targets - inp[None, :]
        return np.sqrt(np.sum(diff * diff, axis=1))
    if dist_method == 3:  # TwistAngle
        t_q = targets.reshape(targets.shape[0], -1, 4)
        i_q = inp.reshape(1, -1, 4)
        ang_t = 2.0 * np.arctan2(t_q[..., 0] + t_q[..., 1] + t_q[..., 2], t_q[..., 3])
        ang_t = np.where(ang_t > math.pi, ang_t - 2.0 * math.pi, np.where(ang_t < -math.pi, ang_t + 2.0 * math.pi, ang_t))
        ang_i = 2.0 * np.arctan2(i_q[..., 0] + i_q[..., 1] + i_q[..., 2], i_q[..., 3])
        ang_i = np.where(ang_i > math.pi, ang_i - 2.0 * math.pi, np.where(ang_i < -math.pi, ang_i + 2.0 * math.pi, ang_i))
        return np.sqrt(np.sum((ang_t - ang_i) ** 2, axis=1))
    # Quaternion (1) and SwingAngle (2) share arc-length metric
    t_q = targets.reshape(targets.shape[0], -1, 4)
    i_q = inp.reshape(1, -1, 4)
    dots = np.sum(t_q * i_q, axis=-1)
    dot_sqr = np.clip(dots * dots, 0.0, 1.0)
    arc = np.arccos(np.clip(2.0 * dot_sqr - 1.0, -1.0, 1.0))
    return np.sqrt(np.sum(arc * arc, axis=1))


def _rbf_kernel(dists: np.ndarray, radius: float, func_type: int) -> np.ndarray:
    """Matches Epic RBFSolver.cpp WeightMethodFunctor (0=Gaussian,1=Exponential,2=Linear,3=Cubic,4=Quintic)."""
    r = max(float(radius), 1e-6)
    if func_type == 0:  # Gaussian
        return np.exp(-dists / (r * r))
    if func_type == 1:  # Exponential
        return np.exp(-2.0 * dists / r)
    if func_type == 2:  # Linear
        return np.maximum(0.0, 1.0 - (dists / r))
    if func_type == 3:  # Cubic
        return np.maximum(0.0, 1.0 - (dists / r) ** 3)
    if func_type == 4:  # Quintic
        return np.maximum(0.0, 1.0 - (dists / r) ** 5)
    return np.exp(-dists / (r * r))


def _parse_control_name(name: str) -> tuple[str, str] | None:
    """Parse DNA raw control name like 'upperarm_r.qx' or 'upperarm_r_q.x'."""
    m = re.match(r'^(.+?)\.([a-zA-Z0-9_]+)$', name.strip())
    if m:
        return m.group(1), m.group(2).lower()
    return None


class _CompiledRBFSolver:
    __slots__ = (
        'raw_in_indices', 'pose_indices', 'pose_out_controls', 'pose_out_weights',
        'pose_in_controls', 'targets', 'target_scales', 'radius', 'weight_threshold',
        'dist_method', 'func_type', 'norm_method', 'twist_axis', 'is_interpolative',
        'inv_coeffs',
    )

    def __init__(
        self,
        raw_in_indices,
        pose_indices,
        pose_out_controls,
        pose_out_weights,
        pose_in_controls,
        raw_target_values,
        target_scales,
        radius,
        automatic_radius,
        weight_threshold,
        dist_method,
        func_type,
        norm_method,
        twist_axis,
        is_interpolative,
    ):
        self.raw_in_indices = np.asarray(raw_in_indices, dtype=np.int32)
        self.pose_indices = pose_indices
        self.pose_out_controls = pose_out_controls
        self.pose_out_weights = pose_out_weights
        self.pose_in_controls = pose_in_controls
        self.target_scales = np.asarray(target_scales, dtype=np.float64)
        self.weight_threshold = float(weight_threshold)
        self.dist_method = int(dist_method)
        self.func_type = int(func_type)
        self.norm_method = int(norm_method)
        self.twist_axis = int(twist_axis)
        self.is_interpolative = bool(is_interpolative)

        dim = len(self.raw_in_indices)
        t_count = len(raw_target_values) // dim if dim > 0 else 0
        raw_mat = np.asarray(raw_target_values, dtype=np.float64).reshape(t_count, dim) if t_count > 0 else np.zeros((0, dim), dtype=np.float64)
        self.targets = _convert_rbf_input(raw_mat, self.dist_method, self.twist_axis) if t_count > 0 else raw_mat

        if automatic_radius and t_count >= 2:
            total = 0.0
            for i in range(t_count - 1):
                total += float(np.sum(_rbf_distances(self.targets[i + 1:], self.targets[i], self.dist_method)))
            self.radius = max(total / (t_count * (t_count - 1) * 0.5), 1e-6)
        else:
            self.radius = max(float(radius), 1e-6)

        self.inv_coeffs = None
        if self.is_interpolative and t_count > 0:
            mat = np.zeros((t_count, t_count), dtype=np.float64)
            for i in range(t_count):
                w = _rbf_kernel(_rbf_distances(self.targets[i:], self.targets[i], self.dist_method), self.radius, self.func_type)
                mat[i, i:] = w
                mat[i:, i] = w
            try:
                self.inv_coeffs = np.linalg.inv(mat)
            except np.linalg.LinAlgError:
                self.inv_coeffs = np.linalg.pinv(mat)

    def solve(self, raw_controls: np.ndarray) -> None:
        t_count = len(self.targets)
        if t_count == 0 or len(self.raw_in_indices) == 0:
            return
        inp = _convert_rbf_input(raw_controls[self.raw_in_indices], self.dist_method, self.twist_axis)
        dists = _rbf_distances(self.targets, inp, self.dist_method)
        weights = _rbf_kernel(dists, self.radius, self.func_type)
        if self.is_interpolative and self.inv_coeffs is not None:
            weights = np.clip(self.inv_coeffs @ weights, 0.0, 1.0)

        sum_w = float(np.sum(weights))
        if sum_w > 1e-12 and (sum_w > 1.0 or self.norm_method == 1):  # 1 = AlwaysNormalize
            weights = weights / sum_w
        weights = weights * self.target_scales
        weights = np.where(weights > self.weight_threshold, weights, 0.0)

        for i in range(t_count):
            w = float(weights[i])
            if w <= 0.0:
                continue
            in_ctrls = self.pose_in_controls[i]
            if in_ctrls:
                for ci in in_ctrls:
                    w *= float(raw_controls[ci])
                if w <= 0.0:
                    continue
            out_ctrls = self.pose_out_controls[i]
            out_w = self.pose_out_weights[i]
            for oc, ow in zip(out_ctrls, out_w):
                raw_controls[oc] += w * ow


class BodyRigLogicSolver:
    """Evaluates MetaHuman Body DNA RBF solvers, TwistSwing setups, and JointGroups."""

    def __init__(self, reader: Any, dna: Any):
        self.joint_count = int(reader.getJointCount())
        self.joint_names = [str(reader.getJointName(i)) for i in range(self.joint_count)]
        self.name_to_idx = {n: i for i, n in enumerate(self.joint_names)}
        self.raw_control_count = int(reader.getRawControlCount())
        self.raw_control_names = [str(reader.getRawControlName(i)) for i in range(self.raw_control_count)]

        # Map joint_name -> {attr: raw_control_index} for driver input population
        self.driver_controls: dict[str, dict[str, int]] = {}
        for idx, cname in enumerate(self.raw_control_names):
            parsed = _parse_control_name(cname)
            if parsed:
                jname, attr = parsed
                if jname in self.name_to_idx:
                    self.driver_controls.setdefault(jname, {})[attr] = idx

        # Neutral joint transforms in DNA space (translation, rotation euler deg, quat xyzw)
        rot_unit = reader.getRotationUnit()
        is_rad = rot_unit == dna.RotationUnit_radians
        self.rot_is_rad = is_rad
        self.sequence = 'xyz'
        self.signs = (1, 1, 1)
        if hasattr(reader, 'getRotationSequence'):
            seq_map = {getattr(dna, 'RotationSequence_' + s): s for s in ('xyz', 'xzy', 'yxz', 'yzx', 'zxy', 'zyx')}
            self.sequence = seq_map.get(reader.getRotationSequence(), 'xyz')
            sign = reader.getRotationSign()
            comp = lambda v, a: getattr(v, a + 'Axis') if hasattr(v, a + 'Axis') else getattr(v, a)
            self.signs = tuple(int(comp(sign, a)) for a in 'xyz')

        self.neutral_t = np.zeros((self.joint_count, 3), dtype=np.float64)
        self.neutral_r_deg = np.zeros((self.joint_count, 3), dtype=np.float64)
        self.neutral_q = [(0.0, 0.0, 0.0, 1.0)] * self.joint_count
        axes = (Gf.Vec3d.XAxis(), Gf.Vec3d.YAxis(), Gf.Vec3d.ZAxis())
        for i in range(self.joint_count):
            t = reader.getNeutralJointTranslation(i)
            r = reader.getNeutralJointRotation(i)
            self.neutral_t[i] = (float(t[0]), float(t[1]), float(t[2]))
            angles = tuple(math.degrees(float(v)) if is_rad else float(v) for v in r)
            self.neutral_r_deg[i] = angles
            rot = Gf.Matrix4d(1)
            for token in self.sequence:
                j = 'xyz'.index(token)
                rot = rot * Gf.Matrix4d().SetRotate(Gf.Rotation(axes[j], angles[j] * self.signs[j]))
            self.neutral_q[i] = _mat4_to_quat_xyzw(rot)

        # Enum mapping helpers
        dist_map = {
            dna.RBFDistanceMethod_Euclidean: 0,
            dna.RBFDistanceMethod_Quaternion: 1,
            dna.RBFDistanceMethod_SwingAngle: 2,
            dna.RBFDistanceMethod_TwistAngle: 3,
        }
        func_map = {
            dna.RBFFunctionType_Gaussian: 0,
            dna.RBFFunctionType_Exponential: 1,
            dna.RBFFunctionType_Linear: 2,
            dna.RBFFunctionType_Cubic: 3,
            dna.RBFFunctionType_Quintic: 4,
        }
        norm_map = {
            dna.RBFNormalizeMethod_OnlyNormalizeAboveOne: 0,
            dna.RBFNormalizeMethod_AlwaysNormalize: 1,
        }
        axis_map = {
            dna.TwistAxis_X: 0,
            dna.TwistAxis_Y: 1,
            dna.TwistAxis_Z: 2,
        }

        # Compile RBF solvers
        self.rbf_solvers: list[_CompiledRBFSolver] = []
        self.rbf_output_controls: set[int] = set()
        solver_count = int(reader.getRBFSolverCount()) if hasattr(reader, 'getRBFSolverCount') else 0
        for si in range(solver_count):
            raw_in = list(reader.getRBFSolverRawControlIndices(si))
            pose_ids = list(reader.getRBFSolverPoseIndices(si))
            raw_vals = list(reader.getRBFSolverRawControlValues(si))
            pose_scales = [float(reader.getRBFPoseScale(pi)) for pi in pose_ids]
            pose_in = [list(reader.getRBFPoseInputControlIndices(pi)) for pi in pose_ids]
            pose_out_c = [list(reader.getRBFPoseOutputControlIndices(pi)) for pi in pose_ids]
            pose_out_w = [list(reader.getRBFPoseOutputControlWeights(pi)) for pi in pose_ids]
            for oc_list in pose_out_c:
                self.rbf_output_controls.update(oc_list)
            self.rbf_solvers.append(
                _CompiledRBFSolver(
                    raw_in_indices=raw_in,
                    pose_indices=pose_ids,
                    pose_out_controls=pose_out_c,
                    pose_out_weights=pose_out_w,
                    pose_in_controls=pose_in,
                    raw_target_values=raw_vals,
                    target_scales=pose_scales,
                    radius=float(reader.getRBFSolverRadius(si)),
                    automatic_radius=bool(reader.getRBFSolverAutomaticRadius(si)),
                    weight_threshold=float(reader.getRBFSolverWeightThreshold(si)),
                    dist_method=dist_map.get(reader.getRBFSolverDistanceMethod(si), 1),
                    func_type=func_map.get(reader.getRBFSolverFunctionType(si), 0),
                    norm_method=norm_map.get(reader.getRBFSolverNormalizeMethod(si), 0),
                    twist_axis=axis_map.get(reader.getRBFSolverTwistAxis(si), 0),
                    is_interpolative=(reader.getRBFSolverType(si) == dna.RBFSolverType_Interpolative),
                )
            )

        # Compile Twist and Swing setups
        self.swings: list[tuple[list[int], list[int], list[float], int]] = []
        self.twists: list[tuple[list[int], list[int], list[float], int]] = []
        self.driven_joints: set[int] = set()
        swing_count = int(reader.getSwingCount()) if hasattr(reader, 'getSwingCount') else 0
        for si in range(swing_count):
            in_c = list(reader.getSwingInputControlIndices(si))
            out_j = list(reader.getSwingOutputJointIndices(si))
            bw = [float(w) for w in reader.getSwingBlendWeights(si)]
            ax = axis_map.get(reader.getSwingSetupTwistAxis(si), 0)
            if len(in_c) == 4 and out_j:
                self.swings.append((in_c, out_j, bw, ax))
                self.driven_joints.update(out_j)

        twist_count = int(reader.getTwistCount()) if hasattr(reader, 'getTwistCount') else 0
        for ti in range(twist_count):
            in_c = list(reader.getTwistInputControlIndices(ti))
            out_j = list(reader.getTwistOutputJointIndices(ti))
            bw = [float(w) for w in reader.getTwistBlendWeights(ti)]
            ax = axis_map.get(reader.getTwistSetupTwistAxis(ti), 0)
            if len(in_c) == 4 and out_j:
                self.twists.append((in_c, out_j, bw, ax))
                self.driven_joints.update(out_j)

        # Compile JointGroups (linear map from raw_controls -> joint attribute deltas)
        self.joint_attr_stride = 9
        self.joint_groups: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        jg_count = int(reader.getJointGroupCount()) if hasattr(reader, 'getJointGroupCount') else 0
        max_out_idx = 0
        for gi in range(jg_count):
            in_idx = np.asarray(list(reader.getJointGroupInputIndices(gi)), dtype=np.int32)
            out_idx = np.asarray(list(reader.getJointGroupOutputIndices(gi)), dtype=np.int32)
            vals = np.asarray(list(reader.getJointGroupValues(gi)), dtype=np.float64)
            if len(in_idx) == 0 or len(out_idx) == 0:
                continue
            mat = vals.reshape(len(out_idx), len(in_idx))
            self.joint_groups.append((in_idx, out_idx, mat))
            max_out_idx = max(max_out_idx, int(np.max(out_idx)))
            for j in reader.getJointGroupJointIndices(gi):
                self.driven_joints.add(int(j))

        # Detect 9 (tx,ty,tz,rx,ry,rz,sx,sy,sz) vs 10 (tx,ty,tz,qx,qy,qz,qw,sx,sy,sz) stride
        if self.joint_count > 0 and max_out_idx >= self.joint_count * 9:
            self.joint_attr_stride = 10
        self.joint_deltas_len = max(self.joint_count * self.joint_attr_stride, max_out_idx + 1)

        # Compute full control buffer length: DNA places PSD and RBF pose output controls
        # at indices >= raw_control_count, and JointGroups read from those extended indices.
        max_ctrl = self.raw_control_count - 1
        for s in self.rbf_solvers:
            if len(s.raw_in_indices):
                max_ctrl = max(max_ctrl, int(np.max(s.raw_in_indices)))
            for lst in s.pose_in_controls:
                if lst:
                    max_ctrl = max(max_ctrl, max(lst))
            for lst in s.pose_out_controls:
                if lst:
                    max_ctrl = max(max_ctrl, max(lst))
        for in_c, _, _, _ in self.swings:
            if in_c:
                max_ctrl = max(max_ctrl, max(in_c))
        for in_c, _, _, _ in self.twists:
            if in_c:
                max_ctrl = max(max_ctrl, max(in_c))
        for in_idx, _, _ in self.joint_groups:
            if len(in_idx):
                max_ctrl = max(max_ctrl, int(np.max(in_idx)))
        self.total_control_count = max(self.raw_control_count, max_ctrl + 1)

        self.has_behavior = bool(self.rbf_solvers or self.swings or self.twists or self.joint_groups)

    def evaluate(
        self,
        target_joint_names: list[str],
        local_matrices: list[Gf.Matrix4d],
    ) -> dict[int, Gf.Matrix4d]:
        """Compute updated local matrices for driven corrective joints.

        `target_joint_names` and `local_matrices` correspond to the USD skeleton
        (which may be a unified Head + Body skeleton). Returns `{usd_joint_idx: mat}`.
        """
        if not self.has_behavior or not target_joint_names:
            return {}

        raw_controls = np.zeros(self.total_control_count, dtype=np.float64)
        # Initialize quaternion w components to 1.0 (identity at rest)
        for jname, attr_map in self.driver_controls.items():
            if 'qw' in attr_map:
                raw_controls[attr_map['qw']] = 1.0

        # Populate driver raw controls from posed USD local transforms relative to neutral bind
        usd_name_to_idx = {n.rsplit('/', 1)[-1]: i for i, n in enumerate(target_joint_names)}
        for jname, attr_map in self.driver_controls.items():
            usd_i = usd_name_to_idx.get(jname)
            dna_i = self.name_to_idx.get(jname)
            if usd_i is None or dna_i is None or usd_i >= len(local_matrices):
                continue
            mat = local_matrices[usd_i]
            q_pose = _mat4_to_quat_xyzw(mat)
            q_rel = _quat_mul(_quat_inv(self.neutral_q[dna_i]), q_pose)
            if q_rel[3] < 0.0:
                q_rel = (-q_rel[0], -q_rel[1], -q_rel[2], -q_rel[3])
            if 'qx' in attr_map:
                raw_controls[attr_map['qx']] = q_rel[0]
            if 'qy' in attr_map:
                raw_controls[attr_map['qy']] = q_rel[1]
            if 'qz' in attr_map:
                raw_controls[attr_map['qz']] = q_rel[2]
            if 'qw' in attr_map:
                raw_controls[attr_map['qw']] = q_rel[3]
            if any(k in attr_map for k in ('tx', 'ty', 'tz')):
                t = mat.ExtractTranslation()
                nt = self.neutral_t[dna_i]
                if 'tx' in attr_map:
                    raw_controls[attr_map['tx']] = float(t[0]) - nt[0]
                if 'ty' in attr_map:
                    raw_controls[attr_map['ty']] = float(t[1]) - nt[1]
                if 'tz' in attr_map:
                    raw_controls[attr_map['tz']] = float(t[2]) - nt[2]

        # 1. Evaluate RBF solvers -> writes pose weights into raw_controls
        for oc in self.rbf_output_controls:
            raw_controls[oc] = 0.0
        for solver in self.rbf_solvers:
            solver.solve(raw_controls)

        # 2. Evaluate JointGroups -> sparse additive deltas per joint attribute
        joint_deltas = np.zeros(self.joint_deltas_len, dtype=np.float64)
        for in_idx, out_idx, mat in self.joint_groups:
            joint_deltas[out_idx] += mat @ raw_controls[in_idx]

        # 3. Evaluate Swing and Twist setups -> quaternion overrides/multipliers on driven joints
        ts_quats: dict[int, tuple[float, float, float, float]] = {}
        identity = (0.0, 0.0, 0.0, 1.0)
        for in_c, out_j, bw, ax in self.swings:
            q_in = (float(raw_controls[in_c[0]]), float(raw_controls[in_c[1]]), float(raw_controls[in_c[2]]), float(raw_controls[in_c[3]]))
            twist = _separate_twist(q_in, ax)
            inv_twist = _quat_inv(twist)
            swing = _quat_mul(q_in, inv_twist)
            inv_swing = _quat_inv(swing)
            for ji, w in zip(out_j, bw):
                inv_swing_frac = _quat_slerp(inv_swing, identity, w)
                ts_quats[ji] = _quat_mul(inv_twist, inv_swing_frac)

        for in_c, out_j, bw, ax in self.twists:
            q_in = (float(raw_controls[in_c[0]]), float(raw_controls[in_c[1]]), float(raw_controls[in_c[2]]), float(raw_controls[in_c[3]]))
            twist = _separate_twist(q_in, ax)
            for ji, w in zip(out_j, bw):
                q_tw = _quat_slerp(twist, identity, w)
                if ji in ts_quats:
                    ts_quats[ji] = _quat_mul(ts_quats[ji], q_tw)
                else:
                    ts_quats[ji] = q_tw

        # 4. Assemble updated local matrices ONLY for driven corrective joints
        updates: dict[int, Gf.Matrix4d] = {}
        stride = self.joint_attr_stride
        axes = (Gf.Vec3d.XAxis(), Gf.Vec3d.YAxis(), Gf.Vec3d.ZAxis())
        for dna_i in self.driven_joints:
            jname = self.joint_names[dna_i]
            # Never clobber a primary driver joint
            if jname in self.driver_controls:
                continue
            usd_i = usd_name_to_idx.get(jname)
            if usd_i is None or usd_i >= len(local_matrices):
                continue

            base_off = dna_i * stride
            dt = joint_deltas[base_off:base_off + 3]
            if stride == 9:
                dr = joint_deltas[base_off + 3:base_off + 6]
                ds = joint_deltas[base_off + 6:base_off + 9]
                if self.rot_is_rad:
                    dr = np.degrees(dr)
                angles = self.neutral_r_deg[dna_i] + dr
                rot = Gf.Matrix4d(1)
                for token in self.sequence:
                    j = 'xyz'.index(token)
                    rot = rot * Gf.Matrix4d().SetRotate(Gf.Rotation(axes[j], float(angles[j]) * self.signs[j]))
                q_final = _mat4_to_quat_xyzw(rot)
            else:
                dq = joint_deltas[base_off + 3:base_off + 7]
                ds = joint_deltas[base_off + 7:base_off + 10]
                q_delta = (float(dq[0]), float(dq[1]), float(dq[2]), float(dq[3]) + 1.0)
                qn = math.sqrt(sum(c * c for c in q_delta))
                q_delta = tuple(c / qn for c in q_delta) if qn > 1e-12 else identity
                q_final = _quat_mul(self.neutral_q[dna_i], q_delta)

            if dna_i in ts_quats:
                q_final = _quat_mul(q_final, ts_quats[dna_i])

            # Build USD Gf.Matrix4d (S * R * T in row-vector convention)
            sx = max(1e-4, 1.0 + float(ds[0]))
            sy = max(1e-4, 1.0 + float(ds[1]))
            sz = max(1e-4, 1.0 + float(ds[2]))
            t_final = self.neutral_t[dna_i] + dt
            out_mat = Gf.Matrix4d().SetScale(Gf.Vec3d(sx, sy, sz)) * Gf.Matrix4d().SetRotate(
                Gf.Quatd(q_final[3], Gf.Vec3d(q_final[0], q_final[1], q_final[2]))
            )
            out_mat.SetTranslateOnly(Gf.Vec3d(float(t_final[0]), float(t_final[1]), float(t_final[2])))
            updates[usd_i] = out_mat

        return updates


def get_body_riglogic_solver(dna_path: str) -> BodyRigLogicSolver | None:
    """Load and cache a BodyRigLogicSolver for a .dna file, or None if unavailable."""
    if not dna_path or not os.path.isfile(dna_path):
        return None
    try:
        st = os.stat(dna_path)
        key = (os.path.normcase(os.path.abspath(dna_path)), st.st_size, st.st_mtime_ns)
    except OSError:
        return None
    if key in _SOLVER_CACHE:
        return _SOLVER_CACHE[key]
    try:
        from metahuman import _sdk
        dna = _sdk()
        stream = dna.FileStream(dna_path, dna.FileStream.AccessMode_Read, dna.FileStream.OpenMode_Binary)
        reader = dna.BinaryStreamReader(stream, dna.DataLayer_All)
        reader.read()
        if not dna.Status.isOk():
            _SOLVER_CACHE[key] = None
            return None
        solver = BodyRigLogicSolver(reader, dna)
        result = solver if solver.has_behavior else None
        _SOLVER_CACHE[key] = result
        return result
    except Exception:
        _SOLVER_CACHE[key] = None
        return None
