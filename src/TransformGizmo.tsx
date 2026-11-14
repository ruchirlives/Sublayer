import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import type { Transform, ViewportCamera } from "./fileRegistry";

export type GizmoMode = "translate" | "rotate" | "scale";

type Vec3 = [number, number, number];
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

export function TransformGizmo({
  transform,
  camera,
  upAxis,
  width,
  height,
  radius,
  interactive,
  onChange,
  onCommit,
}: {
  transform: Transform;
  camera: ViewportCamera;
  upAxis: "Y" | "Z";
  width: number;
  height: number;
  radius: number;
  interactive: boolean;
  onChange: (next: Transform) => void;
  onCommit?: () => void;
}) {
  const [mode, setMode] = useState<GizmoMode>("translate");
  const [hoverAxis, setHoverAxis] = useState<AxisIdx | "all" | null>(null);
  const [activeAxis, setActiveAxis] = useState<AxisIdx | "all" | null>(null);

  const dragRef = useRef<{
    mode: GizmoMode;
    axis: AxisIdx | "all";
    startX: number;
    startY: number;
    startTransform: Transform;
    dir2d: [number, number];
    pixelsPerUnit: number;
    worldUnitsPerPx: number;
    startAngle: number;
    rotSign: number;
    right: Vec3;
    cameraUp: Vec3;
  } | null>(null);

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
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const key = e.key.toLowerCase();
      if (key === "w") setMode("translate");
      else if (key === "e") setMode("rotate");
      else if (key === "r") setMode("scale");
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [interactive]);

  const basis = useMemo(() => {
    const w = Math.max(1, width);
    const h = Math.max(1, height);
    const yaw = (camera.yaw * Math.PI) / 180;
    const pitch = (camera.pitch * Math.PI) / 180;
    const fov = Math.max(5, Math.min(170, camera.fov ?? 45));
    const aspect = w / h;

    const up: Vec3 = upAxis === "Z" ? [0, 0, 1] : [0, 1, 0];
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
    const right = normalize(cross(up, forward));
    const cameraUp = normalize(cross(forward, right));

    const safeRadius = Math.max(0.001, radius || 1);
    const halfFov = (22.5 * Math.PI) / 180;
    const fitDistance =
      (safeRadius / Math.sin(Math.atan(Math.tan(halfFov) * Math.min(aspect, 1)))) *
      1.15;
    const distance = fitDistance * Math.max(0.002, camera.zoom || 1);
    const tanHalf = Math.tan(((fov * Math.PI) / 180) * 0.5);
    const worldUnitsPerPx = (distance * tanHalf) / (h * 0.5);
    const axisWorldLen = worldUnitsPerPx * 82;

    const projectOffset = (offset: Vec3): [number, number] => {
      const vx = dot(offset, right);
      const vy = dot(offset, cameraUp);
      const vz = Math.max(1e-4, distance - dot(offset, forward));
      const ndcX = vx / (vz * tanHalf * aspect);
      const ndcY = vy / (vz * tanHalf);
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
  }, [camera, upAxis, width, height, radius]);

  const axisTips = useMemo(
    () =>
      AXES.map((a) => {
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
    [basis],
  );

  const rotationRings = useMemo(() => {
    const r = basis.axisWorldLen * 0.85;
    const steps = 48;
    return AXES.map((a) => {
      const u: Vec3 = a.idx === 0 ? [0, 1, 0] : a.idx === 1 ? [0, 0, 1] : [1, 0, 0];
      const v: Vec3 = a.idx === 0 ? [0, 0, 1] : a.idx === 1 ? [1, 0, 0] : [0, 1, 0];
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
  }, [basis]);

  function startAxisDrag(e: PointerEvent<SVGElement>, axis: AxisIdx | "all") {
    if (!interactive || e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    (e.currentTarget as Element).setPointerCapture(e.pointerId);

    const tipInfo = axis === "all" ? null : axisTips[axis];
    const startAngle = Math.atan2(
      e.clientY - (e.currentTarget.ownerSVGElement?.getBoundingClientRect().top ?? 0) - basis.center[1],
      e.clientX - (e.currentTarget.ownerSVGElement?.getBoundingClientRect().left ?? 0) - basis.center[0],
    );
    const rotSign =
      axis === "all"
        ? 1
        : dot(AXES[axis].dir, basis.forward) >= 0
          ? -1
          : 1;

    setActiveAxis(axis);
    dragRef.current = {
      mode,
      axis,
      startX: e.clientX,
      startY: e.clientY,
      startTransform: structuredClone(transform),
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
        next.translation[d.axis] = round4(d.startTransform.translation[d.axis] + delta);
      }
    } else if (d.mode === "rotate" && d.axis !== "all") {
      const rect = e.currentTarget.ownerSVGElement?.getBoundingClientRect();
      const px = e.clientX - (rect?.left ?? 0);
      const py = e.clientY - (rect?.top ?? 0);
      const angle = Math.atan2(py - basis.center[1], px - basis.center[0]);
      let deltaRad = angle - d.startAngle;
      while (deltaRad > Math.PI) deltaRad -= Math.PI * 2;
      while (deltaRad < -Math.PI) deltaRad += Math.PI * 2;
      const deltaDeg = (deltaRad * 180) / Math.PI * d.rotSign;
      next.rotation[d.axis] = round4(d.startTransform.rotation[d.axis] + deltaDeg);
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

    onChange(next);
  }

  function endAxisDrag(e: PointerEvent<SVGElement>) {
    if (!dragRef.current) return;
    e.stopPropagation();
    dragRef.current = null;
    setActiveAxis(null);
    onCommit?.();
  }

  const [cx, cy] = basis.center;
  const pointerStyle = interactive ? "auto" : "none";

  return (
    <>
      <div
        className="gizmo-mode-bar"
        style={{
          position: "absolute",
          top: 12,
          left: "50%",
          transform: "translateX(-50%)",
          zIndex: 6,
          display: "flex",
          gap: 4,
          padding: "3px 4px",
          borderRadius: 6,
          background: "rgba(11, 20, 24, 0.86)",
          border: "1px solid rgba(152, 172, 182, 0.25)",
          pointerEvents: "auto",
        }}
        onPointerDown={(e) => e.stopPropagation()}
        onDoubleClick={(e) => e.stopPropagation()}
      >
        {(
          [
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
        {mode === "rotate" &&
          rotationRings.map((ring) => {
            const highlighted = activeAxis === ring.idx || hoverAxis === ring.idx;
            return (
              <g key={ring.label}>
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
                  onPointerEnter={() => setHoverAxis(ring.idx)}
                  onPointerLeave={() => setHoverAxis((cur) => (cur === ring.idx ? null : cur))}
                  onPointerDown={(e) => startAxisDrag(e, ring.idx)}
                  onPointerMove={moveAxisDrag}
                  onPointerUp={endAxisDrag}
                  onPointerCancel={endAxisDrag}
                  onDoubleClick={(e) => e.stopPropagation()}
                />
              </g>
            );
          })}

        {(mode === "translate" || mode === "scale") &&
          axisTips.map((a) => {
            const highlighted = activeAxis === a.idx || hoverAxis === a.idx;
            const color = highlighted ? "#ffffff" : a.color;
            const [tx, ty] = a.tip;
            const [ux, uy] = a.dir2d;
            const px = -uy;
            const py = ux;
            const arrowPts = `${tx.toFixed(1)},${ty.toFixed(1)} ${(tx - ux * 12 + px * 5).toFixed(1)},${(ty - uy * 12 + py * 5).toFixed(1)} ${(tx - ux * 12 - px * 5).toFixed(1)},${(ty - uy * 12 - py * 5).toFixed(1)}`;
            return (
              <g key={a.label}>
                <line
                  x1={cx}
                  y1={cy}
                  x2={tx}
                  y2={ty}
                  stroke={color}
                  strokeWidth={highlighted ? 3.5 : 2.4}
                />
                {mode === "translate" ? (
                  <polygon points={arrowPts} fill={color} />
                ) : (
                  <rect
                    x={tx - 5}
                    y={ty - 5}
                    width={10}
                    height={10}
                    rx={1.5}
                    fill={color}
                  />
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
                  onPointerEnter={() => setHoverAxis(a.idx)}
                  onPointerLeave={() => setHoverAxis((cur) => (cur === a.idx ? null : cur))}
                  onPointerDown={(e) => startAxisDrag(e, a.idx)}
                  onPointerMove={moveAxisDrag}
                  onPointerUp={endAxisDrag}
                  onPointerCancel={endAxisDrag}
                  onDoubleClick={(e) => e.stopPropagation()}
                />
              </g>
            );
          })}

        {mode === "scale" ? (
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
            onPointerEnter={() => setHoverAxis("all")}
            onPointerLeave={() => setHoverAxis((cur) => (cur === "all" ? null : cur))}
            onPointerDown={(e) => startAxisDrag(e, "all")}
            onPointerMove={moveAxisDrag}
            onPointerUp={endAxisDrag}
            onPointerCancel={endAxisDrag}
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
              pointerEvents: mode === "translate" ? pointerStyle : "none",
              cursor: mode === "translate" ? "move" : "default",
            }}
            onPointerEnter={() => mode === "translate" && setHoverAxis("all")}
            onPointerLeave={() => setHoverAxis((cur) => (cur === "all" ? null : cur))}
            onPointerDown={(e) => mode === "translate" && startAxisDrag(e, "all")}
            onPointerMove={moveAxisDrag}
            onPointerUp={endAxisDrag}
            onPointerCancel={endAxisDrag}
            onDoubleClick={(e) => e.stopPropagation()}
          />
        )}
      </svg>
    </>
  );
}
