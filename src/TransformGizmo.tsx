import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import type { Transform, ViewportCamera } from "./fileRegistry";

export type GizmoMode = "all" | "translate" | "rotate" | "scale";
export type GizmoSpace = "world" | "local";
type DragMode = "translate" | "rotate" | "scale";

type Vec3 = [number, number, number];
type Mat3 = [Vec3, Vec3, Vec3];
type AxisIdx = 0 | 1 | 2;

const AXES: { idx: AxisIdx; label: "X" | "Y" | "Z"; dir: Vec3; color: string }[] = [
  { idx: 0, label: "X", dir: [1, 0, 0], color: "#ef4444" },
  { idx: 1, label: "Y", dir: [0, 1, 0], color: "#22c55e" },
  { idx: 2, label: "Z", dir: [0, 0, 1], color: "#3b82f6" },
];

function dot(a: Vec3, b: Vec3): number {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function cross(a: Vec3, b: Vec3): Vec3 {
  return [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ];
}

function normalize(v: Vec3): Vec3 {
  const len = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / len, v[1] / len, v[2] / len];
}

function round4(n: number): number {
  return Math.round(n * 10000) / 10000;
}

const DEG2RAD = Math.PI / 180;
const RAD2DEG = 180 / Math.PI;

function mat3FromEulerXYZ(rotDeg: Vec3): Mat3 {
  const rx = rotDeg[0] * DEG2RAD;
  const ry = rotDeg[1] * DEG2RAD;
  const rz = rotDeg[2] * DEG2RAD;
  const cx = Math.cos(rx), sx = Math.sin(rx);
  const cy = Math.cos(ry), sy = Math.sin(ry);
  const cz = Math.cos(rz), sz = Math.sin(rz);
  // R = Rz(rz) * Ry(ry) * Rx(rx) (matches UsdGeom.XformCommonAPI.RotationOrderXYZ)
  return [
    [cy * cz, sx * sy * cz - cx * sz, cx * sy * cz + sx * sz],
    [cy * sz, sx * sy * sz + cx * cz, cx * sy * sz - sx * cz],
    [-sy, sx * cy, cx * cy],
  ];
}

function mulMat3(a: Mat3, b: Mat3): Mat3 {
  const out: Mat3 = [
    [0, 0, 0],
    [0, 0, 0],
    [0, 0, 0],
  ];
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 3; c++) {
      out[r][c] = a[r][0] * b[0][c] + a[r][1] * b[1][c] + a[r][2] * b[2][c];
    }
  }
  return out;
}

function mat3AxisAngle(axis: AxisIdx, rad: number): Mat3 {
  const c = Math.cos(rad);
  const s = Math.sin(rad);
  if (axis === 0) return [[1, 0, 0], [0, c, -s], [0, s, c]];
  if (axis === 1) return [[c, 0, s], [0, 1, 0], [-s, 0, c]];
  return [[c, -s, 0], [s, c, 0], [0, 0, 1]];
}

function mat3Col(m: Mat3, col: AxisIdx): Vec3 {
  return [m[0][col], m[1][col], m[2][col]];
}

function extractEulerXYZ(m: Mat3): Vec3 {
  const sy = Math.max(-1, Math.min(1, -m[2][0]));
  const ry = Math.asin(sy);
  let rx = 0;
  let rz = 0;
  if (Math.abs(Math.cos(ry)) > 1e-6) {
    rx = Math.atan2(m[2][1], m[2][2]);
    rz = Math.atan2(m[1][0], m[0][0]);
  } else {
    rx = Math.atan2(-m[1][2], m[1][1]);
    rz = 0;
  }
  return [round4(rx * RAD2DEG), round4(ry * RAD2DEG), round4(rz * RAD2DEG)];
}

export function TransformGizmo({
  transform,
  camera,
  upAxis,
  width,
  height,
  radius,
  anchorTranslation,
  originOffset,
  interactive,
  onChange,
  onCommit,
  onCancel,
  translationLocks,
  worldOnly = false,
  onInteractingChange,
}: {
  transform: Transform;
  camera: ViewportCamera;
  upAxis: "Y" | "Z";
  width: number;
  height: number;
  radius: number;
  anchorTranslation?: [number, number, number];
  originOffset?: [number, number, number];
  interactive: boolean;
  onChange: (next: Transform) => void;
  onCommit?: () => void;
  onCancel?: () => void;
  translationLocks?: [boolean, boolean, boolean];
  worldOnly?: boolean;
  onInteractingChange?: (interacting: boolean) => void;
}) {
  const [mode, setMode] = useState<GizmoMode>("all");
  const [space, setSpace] = useState<GizmoSpace>("world");
  const [hoverAxis, setHoverAxis] = useState<AxisIdx | "all" | null>(null);
  const [hoverKind, setHoverKind] = useState<DragMode | null>(null);
  const [activeAxis, setActiveAxis] = useState<AxisIdx | "all" | null>(null);
  const [activeKind, setActiveKind] = useState<DragMode | null>(null);

  const dragRef = useRef<{
    mode: DragMode;
    space: GizmoSpace;
    axis: AxisIdx | "all";
    axisDir: Vec3;
    startX: number;
    startY: number;
    startTransform: Transform;
    startRotMat: Mat3;
    dir2d: [number, number];
    pixelsPerUnit: number;
    worldUnitsPerPx: number;
    startAngle: number;
    rotSign: number;
    right: Vec3;
    cameraUp: Vec3;
  } | null>(null);

  useEffect(() => {
    return () => {
      if (dragRef.current) {
        dragRef.current = null;
        onInteractingChange?.(false);
      }
    };
  }, [onInteractingChange]);

  useEffect(() => {
    if (!interactive) return;
    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable)
      ) {
        return;
      }
      if (e.key === "Escape" && dragRef.current && onCancel) {
        dragRef.current = null; setActiveAxis(null); setActiveKind(null); onInteractingChange?.(false); onCancel(); return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const key = e.key.toLowerCase();
      if (key === "q") setMode("all");
      else if (key === "w") setMode("translate");
      else if (key === "e") setMode("rotate");
      else if (key === "r") setMode("scale");
      else if (key === "x" && !worldOnly) setSpace((s) => (s === "world" ? "local" : "world"));
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [interactive, onCancel, onInteractingChange, worldOnly]);

  const basis = useMemo(() => {
    const w = Math.max(1, width);
    const h = Math.max(1, height);
    const yaw = (camera.yaw * Math.PI) / 180;
    const pitch = (Math.max(-90, Math.min(90, camera.pitch)) * Math.PI) / 180;
    const isOrtho = camera.projection === "orthographic";
    const fov = Math.max(5, Math.min(170, camera.fov ?? 45));
    const aspect = w / h;

    const forward: Vec3 =
      upAxis === "Z"
        ? [
            Math.sin(yaw) * Math.cos(pitch),
            -Math.cos(yaw) * Math.cos(pitch),
            Math.sin(pitch),
          ]
        : [
            Math.sin(yaw) * Math.cos(pitch),
            Math.sin(pitch),
            Math.cos(yaw) * Math.cos(pitch),
          ];
    const right: Vec3 =
      upAxis === "Z"
        ? [Math.cos(yaw), Math.sin(yaw), 0]
        : [Math.cos(yaw), 0, -Math.sin(yaw)];
    const cameraUp = normalize(cross(forward, right));

    const safeRadius = Math.max(0.001, radius || 1);
    const halfFov = (22.5 * Math.PI) / 180;
    const fitDistance =
      (safeRadius / Math.sin(Math.atan(Math.tan(halfFov) * Math.min(aspect, 1)))) *
      1.15;
    const distance = fitDistance * Math.max(0.002, camera.zoom || 1);
    const orthoHalfHeight = Math.max(1e-5, distance * Math.tan(halfFov));
    const tanHalf = Math.tan(((fov * Math.PI) / 180) * 0.5);
    const worldUnitsPerPx = (isOrtho ? orthoHalfHeight : distance * tanHalf) / (h * 0.5);
    const axisWorldLen = worldUnitsPerPx * 82;

    // The displayed frame can lag behind both pointer release and the next drag.
    const baseT = anchorTranslation ?? transform.translation;
    const deltaT: Vec3 = [
      transform.translation[0] - baseT[0],
      transform.translation[1] - baseT[1],
      transform.translation[2] - baseT[2],
    ];

    const panOffsetX = -(camera.panX || 0) * safeRadius;
    const panOffsetY = -(camera.panY || 0) * safeRadius;

    const projectOffset = (offset: Vec3): [number, number] => {
      const worldOffset: Vec3 = [
        offset[0] + deltaT[0] + (originOffset?.[0] ?? 0),
        offset[1] + deltaT[1] + (originOffset?.[1] ?? 0),
        offset[2] + deltaT[2] + (originOffset?.[2] ?? 0),
      ];
      const vx = dot(worldOffset, right) + panOffsetX;
      const vy = dot(worldOffset, cameraUp) + panOffsetY;
      let ndcX: number;
      let ndcY: number;
      if (isOrtho) {
        ndcX = vx / (orthoHalfHeight * aspect);
        ndcY = vy / orthoHalfHeight;
      } else {
        const vz = Math.max(1e-4, distance - dot(worldOffset, forward));
        ndcX = vx / (vz * tanHalf * aspect);
        ndcY = vy / (vz * tanHalf);
      }
      return [(ndcX * 0.5 + 0.5) * w, (0.5 - ndcY * 0.5) * h];
    };

    const center = projectOffset([0, 0, 0]);
    return {
      center,
      forward,
      right,
      cameraUp,
      axisWorldLen,
      worldUnitsPerPx,
      projectOffset,
    };
  }, [camera, upAxis, width, height, radius, transform.translation, anchorTranslation, originOffset]);

  const rotMat = useMemo(
    () => mat3FromEulerXYZ(transform.rotation as Vec3),
    [transform.rotation],
  );

  const spaceAxes = useMemo(
    () =>
      AXES.map((a) => ({
        ...a,
        dir: space === "local" ? normalize(mat3Col(rotMat, a.idx)) : a.dir,
      })),
    [space, rotMat],
  );

  const axisTips = useMemo(
    () =>
      spaceAxes.map((a) => {
        const tip = basis.projectOffset([
          a.dir[0] * basis.axisWorldLen,
          a.dir[1] * basis.axisWorldLen,
          a.dir[2] * basis.axisWorldLen,
        ]);
        const dx = tip[0] - basis.center[0];
        const dy = tip[1] - basis.center[1];
        const lenPx = Math.hypot(dx, dy);
        return {
          ...a,
          tip,
          dx,
          dy,
          lenPx,
          dir2d: lenPx > 1e-3 ? ([dx / lenPx, dy / lenPx] as [number, number]) : ([1, 0] as [number, number]),
          pixelsPerUnit: lenPx / Math.max(1e-6, basis.axisWorldLen),
        };
      }),
    [basis, spaceAxes],
  );

  const rotationRings = useMemo(() => {
    const r = basis.axisWorldLen * (mode === "all" ? 0.72 : 0.85);
    const steps = 48;
    return spaceAxes.map((a) => {
      const u = spaceAxes[(a.idx + 1) % 3].dir;
      const v = spaceAxes[(a.idx + 2) % 3].dir;
      const pts: string[] = [];
      for (let i = 0; i <= steps; i++) {
        const theta = (i / steps) * Math.PI * 2;
        const c = Math.cos(theta) * r;
        const s = Math.sin(theta) * r;
        const [sx, sy] = basis.projectOffset([
          u[0] * c + v[0] * s,
          u[1] * c + v[1] * s,
          u[2] * c + v[2] * s,
        ]);
        pts.push(`${sx.toFixed(1)},${sy.toFixed(1)}`);
      }
      return { ...a, points: pts.join(" ") };
    });
  }, [basis, mode, spaceAxes]);

  function startAxisDrag(e: PointerEvent<SVGElement>, axis: AxisIdx | "all", dragMode: DragMode) {
    if (!interactive || e.button !== 0 || (dragMode === "translate" && axis !== "all" && space === "world" && translationLocks?.[axis])) return;
    e.stopPropagation();
    e.preventDefault();
    (e.currentTarget as Element).setPointerCapture(e.pointerId);

    const tipInfo = axis === "all" ? null : axisTips[axis];
    const axisDir: Vec3 = axis === "all" ? [0, 0, 0] : spaceAxes[axis].dir;
    const startAngle = Math.atan2(
      e.clientY - (e.currentTarget.ownerSVGElement?.getBoundingClientRect().top ?? 0) - basis.center[1],
      e.clientX - (e.currentTarget.ownerSVGElement?.getBoundingClientRect().left ?? 0) - basis.center[0],
    );
    const rotSign =
      axis === "all"
        ? 1
        : dot(axisDir, basis.forward) >= 0
          ? -1
          : 1;

    setActiveAxis(axis);
    setActiveKind(dragMode);
    onInteractingChange?.(true);
    dragRef.current = {
      mode: dragMode,
      space,
      axis,
      axisDir,
      startX: e.clientX,
      startY: e.clientY,
      startTransform: structuredClone(transform),
      startRotMat: rotMat,
      dir2d: tipInfo ? tipInfo.dir2d : [1, 0],
      pixelsPerUnit: tipInfo ? Math.max(8, tipInfo.pixelsPerUnit) : 1 / Math.max(1e-6, basis.worldUnitsPerPx),
      worldUnitsPerPx: basis.worldUnitsPerPx,
      startAngle,
      rotSign,
      right: basis.right,
      cameraUp: basis.cameraUp,
    };
  }

  function moveAxisDrag(e: PointerEvent<SVGElement>) {
    const d = dragRef.current;
    if (!d) return;
    e.stopPropagation();

    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    const next = structuredClone(d.startTransform);

    if (d.mode === "translate") {
      if (d.axis === "all") {
        const wx = dx * d.worldUnitsPerPx;
        const wy = -dy * d.worldUnitsPerPx;
        for (let i = 0; i < 3; i++) {
          next.translation[i] = round4(
            d.startTransform.translation[i] + d.right[i] * wx + d.cameraUp[i] * wy,
          );
        }
      } else {
        const projPx = dx * d.dir2d[0] + dy * d.dir2d[1];
        const delta = projPx / d.pixelsPerUnit;
        for (let i = 0; i < 3; i++) {
          next.translation[i] = round4(
            d.startTransform.translation[i] + d.axisDir[i] * delta,
          );
        }
      }
    } else if (d.mode === "rotate" && d.axis !== "all") {
      const rect = e.currentTarget.ownerSVGElement?.getBoundingClientRect();
      const px = e.clientX - (rect?.left ?? 0);
      const py = e.clientY - (rect?.top ?? 0);
      const angle = Math.atan2(py - basis.center[1], px - basis.center[0]);
      let deltaRad = angle - d.startAngle;
      while (deltaRad > Math.PI) deltaRad -= Math.PI * 2;
      while (deltaRad < -Math.PI) deltaRad += Math.PI * 2;
      const signedRad = deltaRad * d.rotSign;
      const deltaMat = mat3AxisAngle(d.axis, signedRad);
      const nextMat =
        d.space === "local"
          ? mulMat3(d.startRotMat, deltaMat)
          : mulMat3(deltaMat, d.startRotMat);
      next.rotation = extractEulerXYZ(nextMat);
    } else if (d.mode === "scale") {
      if (d.axis === "all") {
        const factor = Math.max(0.01, 1 + (dx - dy) / 110);
        for (let i = 0; i < 3; i++) {
          next.scale[i] = round4(d.startTransform.scale[i] * factor);
        }
      } else {
        const projPx = dx * d.dir2d[0] + dy * d.dir2d[1];
        const factor = Math.max(0.01, 1 + projPx / 82);
        next.scale[d.axis] = round4(d.startTransform.scale[d.axis] * factor);
      }
    }

    if (d.mode === "translate" && translationLocks) next.translation = next.translation.map((v,i) => translationLocks[i] ? d.startTransform.translation[i] : v) as Vec3;
    onChange(next);
  }

  function endAxisDrag(e: PointerEvent<SVGElement>) {
    if (!dragRef.current) return;
    e.stopPropagation();
    dragRef.current = null;
    setActiveAxis(null);
    setActiveKind(null);
    onInteractingChange?.(false);
    onCommit?.();
  }

  const [cx, cy] = basis.center;
  const pointerStyle = interactive ? "auto" : "none";
  const showRotate = mode === "all" || mode === "rotate";
  const showTranslate = mode === "all" || mode === "translate";
  const showScale = mode === "scale";

  return (
    <>
      <div
        className="gizmo-mode-bar"
        style={{
          position: "absolute",
          bottom: 18,
          left: "50%",
          transform: "translateX(-50%)",
          zIndex: 8,
          display: "flex",
          gap: 4,
          padding: "3px 4px",
          borderRadius: 6,
          background: "rgba(11, 20, 24, 0.88)",
          border: "1px solid rgba(152, 172, 182, 0.25)",
          pointerEvents: interactive ? "auto" : "none",
          opacity: interactive ? 1 : 0.55,
        }}
        onPointerDown={(e) => e.stopPropagation()}
        onDoubleClick={(e) => e.stopPropagation()}
      >
        {(
          [
            ["all", "All (Q)"],
            ["translate", "Move (W)"],
            ["rotate", "Rotate (E)"],
            ["scale", "Scale (R)"],
          ] as const
        ).map(([m, label]) => (
          <button
            key={m}
            type="button"
            onClick={() => setMode(m)}
            style={{
              padding: "3px 8px",
              fontSize: 11,
              borderRadius: 4,
              border: "none",
              cursor: "pointer",
              background: mode === m ? "#2f6b58" : "transparent",
              color: mode === m ? "#eefaf5" : "#9fb2bc",
              fontWeight: mode === m ? 600 : 400,
            }}
          >
            {label}
          </button>
        ))}
        <span
          aria-hidden="true"
          style={{
            width: 1,
            margin: "2px 2px",
            background: "rgba(152, 172, 182, 0.25)",
          }}
        />
        {(
          [
            ["world", "World"],
            ["local", "Local"],
          ] as const
        ).map(([s, label]) => (
          <button
            key={s}
            type="button"
            disabled={worldOnly && s === "local"}
            title={worldOnly ? "Instance placement uses world axes" : "Toggle gizmo alignment (X)"}
            onClick={() => setSpace(s)}
            style={{
              padding: "3px 8px",
              fontSize: 11,
              borderRadius: 4,
              border: "none",
              cursor: "pointer",
              background: space === s ? "#2b4f66" : "transparent",
              color: space === s ? "#e8f4fc" : "#9fb2bc",
              fontWeight: space === s ? 600 : 400,
            }}
          >
            {label}
          </button>
        ))}
      </div>

      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          pointerEvents: "none",
          zIndex: 5,
          overflow: "visible",
        }}
      >
        {showRotate &&
          rotationRings.map((ring) => {
            const highlighted =
              (activeAxis === ring.idx && activeKind === "rotate") ||
              (hoverAxis === ring.idx && hoverKind === "rotate");
            return (
              <g key={`rot-${ring.label}`}>
                <polyline
                  points={ring.points}
                  fill="none"
                  stroke={highlighted ? "#ffffff" : ring.color}
                  strokeWidth={highlighted ? 3.5 : 2.2}
                  strokeOpacity={highlighted ? 1 : 0.85}
                />
                <polyline
                  points={ring.points}
                  fill="none"
                  stroke="transparent"
                  strokeWidth={14}
                  style={{ pointerEvents: pointerStyle, cursor: "grab" }}
                  onPointerEnter={() => {
                    setHoverAxis(ring.idx);
                    setHoverKind("rotate");
                  }}
                  onPointerLeave={() => {
                    setHoverAxis((cur) => (cur === ring.idx ? null : cur));
                    setHoverKind((cur) => (cur === "rotate" ? null : cur));
                  }}
                  onPointerDown={(e) => startAxisDrag(e, ring.idx, "rotate")}
                  onPointerMove={moveAxisDrag}
                  onPointerUp={endAxisDrag}
                  onPointerCancel={(e) => { if (onCancel) { dragRef.current=null;setActiveAxis(null);setActiveKind(null);onInteractingChange?.(false);onCancel(); } else endAxisDrag(e); }}
                  onLostPointerCapture={endAxisDrag}
                  onDoubleClick={(e) => e.stopPropagation()}
                />
              </g>
            );
          })}

        {(showTranslate || showScale) &&
          axisTips.map((a) => {
            if (a.lenPx < 6 || (showTranslate && !showScale && space === "world" && translationLocks?.[a.idx])) return null;
            const axisKind: DragMode = showScale ? "scale" : "translate";
            const highlighted =
              (activeAxis === a.idx && activeKind === axisKind) ||
              (hoverAxis === a.idx && hoverKind === axisKind);
            const color = highlighted ? "#ffffff" : a.color;
            const [tx, ty] = a.tip;
            const [ux, uy] = a.dir2d;
            const px = -uy;
            const py = ux;
            const arrowPts = `${tx.toFixed(1)},${ty.toFixed(1)} ${(tx - ux * 12 + px * 5).toFixed(1)},${(ty - uy * 12 + py * 5).toFixed(1)} ${(tx - ux * 12 - px * 5).toFixed(1)},${(ty - uy * 12 - py * 5).toFixed(1)}`;
            return (
              <g key={`axis-${a.label}`}>
                <line
                  x1={cx}
                  y1={cy}
                  x2={tx}
                  y2={ty}
                  stroke={color}
                  strokeWidth={highlighted ? 3.5 : 2.4}
                />
                {showScale ? (
                  <rect
                    x={tx - 5}
                    y={ty - 5}
                    width={10}
                    height={10}
                    rx={1.5}
                    fill={color}
                  />
                ) : (
                  <polygon points={arrowPts} fill={color} />
                )}
                <text
                  x={tx + ux * 14}
                  y={ty + uy * 14 + 3}
                  fill={color}
                  fontSize="11"
                  fontWeight="700"
                  textAnchor="middle"
                  style={{ userSelect: "none", textShadow: "0 1px 3px rgba(0,0,0,0.85)" }}
                >
                  {a.label}
                </text>
                <line
                  x1={cx}
                  y1={cy}
                  x2={tx + ux * 8}
                  y2={ty + uy * 8}
                  stroke="transparent"
                  strokeWidth={16}
                  style={{ pointerEvents: pointerStyle, cursor: "grab" }}
                  onPointerEnter={() => {
                    setHoverAxis(a.idx);
                    setHoverKind(axisKind);
                  }}
                  onPointerLeave={() => {
                    setHoverAxis((cur) => (cur === a.idx ? null : cur));
                    setHoverKind((cur) => (cur === axisKind ? null : cur));
                  }}
                  onPointerDown={(e) => startAxisDrag(e, a.idx, axisKind)}
                  onPointerMove={moveAxisDrag}
                  onPointerUp={endAxisDrag}
                  onPointerCancel={(e) => { if (onCancel) { dragRef.current=null;setActiveAxis(null);setActiveKind(null);onInteractingChange?.(false);onCancel(); } else endAxisDrag(e); }}
                  onLostPointerCapture={endAxisDrag}
                  onDoubleClick={(e) => e.stopPropagation()}
                />
              </g>
            );
          })}

        {showScale ? (
          <rect
            x={cx - 6}
            y={cy - 6}
            width={12}
            height={12}
            rx={2}
            fill={activeAxis === "all" || hoverAxis === "all" ? "#ffffff" : "#e2e8f0"}
            stroke="#0b1418"
            strokeWidth={1.5}
            style={{ pointerEvents: pointerStyle, cursor: "nwse-resize" }}
            onPointerEnter={() => {
              setHoverAxis("all");
              setHoverKind("scale");
            }}
            onPointerLeave={() => {
              setHoverAxis((cur) => (cur === "all" ? null : cur));
              setHoverKind((cur) => (cur === "scale" ? null : cur));
            }}
            onPointerDown={(e) => startAxisDrag(e, "all", "scale")}
            onPointerMove={moveAxisDrag}
            onPointerUp={endAxisDrag}
            onPointerCancel={(e) => { if (onCancel) { dragRef.current=null;setActiveAxis(null);setActiveKind(null);onInteractingChange?.(false);onCancel(); } else endAxisDrag(e); }}
            onLostPointerCapture={endAxisDrag}
            onDoubleClick={(e) => e.stopPropagation()}
          />
        ) : (
          <circle
            cx={cx}
            cy={cy}
            r={6}
            fill={activeAxis === "all" || hoverAxis === "all" ? "#ffffff" : "#e2e8f0"}
            stroke="#0b1418"
            strokeWidth={1.5}
            style={{
              pointerEvents: showTranslate ? pointerStyle : "none",
              cursor: showTranslate ? "move" : "default",
            }}
            onPointerEnter={() => {
              if (showTranslate) {
                setHoverAxis("all");
                setHoverKind("translate");
              }
            }}
            onPointerLeave={() => {
              setHoverAxis((cur) => (cur === "all" ? null : cur));
              setHoverKind((cur) => (cur === "translate" ? null : cur));
            }}
            onPointerDown={(e) => showTranslate && startAxisDrag(e, "all", "translate")}
            onPointerMove={moveAxisDrag}
            onPointerUp={endAxisDrag}
            onPointerCancel={(e) => { if (onCancel) { dragRef.current=null;setActiveAxis(null);setActiveKind(null);onInteractingChange?.(false);onCancel(); } else endAxisDrag(e); }}
            onLostPointerCapture={endAxisDrag}
            onDoubleClick={(e) => e.stopPropagation()}
          />
        )}
      </svg>
    </>
  );
}
