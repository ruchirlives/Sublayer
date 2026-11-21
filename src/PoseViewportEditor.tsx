import { projectPosePoint } from "./poseProjection";
import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { PoseJoint } from "./fileRegistry";

export type PoseEditing = { nodeId: string; entryId: string; skeletonPath: string; jointIndex: number };
export type LivePoseOverride = {
  nodeId: string;
  entryId: string;
  skeletonPath: string;
  jointIndex: number;
  baseRotation: [number, number, number, number];
  rotationOffset: [number, number, number];
};
export type PoseViewportProps = PoseEditing & {
  joints: PoseJoint[];
  onSelectJoint: (index: number) => void;
  onRotateJoint: (index: number, axis: number, value: number) => void;
  onCommit: () => void;
};
type Vec3 = [number, number, number];
type Bones = { path: string; worldPoints: number[][]; worldAxes?: number[][][]; parents: number[]; joints: string[] };

const GIZMO_AXES: { idx: 0 | 1 | 2; label: "X" | "Y" | "Z"; color: string }[] = [
  { idx: 0, label: "X", color: "#ef4444" },
  { idx: 1, label: "Y", color: "#22c55e" },
  { idx: 2, label: "Z", color: "#3b82f6" },
];
const ARC_STEPS = 18;
const ARC_START = (12 * Math.PI) / 180;
const ARC_END = (78 * Math.PI) / 180;
const GUIDE_STEPS = 36;

function add3(a: Vec3, b: Vec3, scale = 1): Vec3 {
  return [a[0] + b[0] * scale, a[1] + b[1] * scale, a[2] + b[2] * scale];
}

function cross3(a: Vec3, b: Vec3): Vec3 {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

function norm3(v: Vec3): Vec3 {
  const len = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / len, v[1] / len, v[2] / len];
}

export function PoseViewportEditor({
  editor,
  graph,
  frame,
  matrix,
  width,
  height,
  interactive,
  onInteractingChange,
  onLiveRotateJoint,
  liveBones,
}: {
  editor: PoseViewportProps;
  graph: string;
  frame: number;
  matrix?: number[][];
  width: number;
  height: number;
  interactive: boolean;
  onInteractingChange?: (interacting: boolean) => void;
  onLiveRotateJoint?: (override: LivePoseOverride | null) => void;
  liveBones?: { worldPoints: number[][]; worldAxes: number[][][] } | null;
}) {
  const [bones, setBones] = useState<Bones | null>(null);
  const [error, setError] = useState("");
  const [all, setAll] = useState(true);
  const [hoverAxis, setHoverAxis] = useState<number | null>(null);
  const [activeAxis, setActiveAxis] = useState<number | null>(null);
  const [liveDeg, setLiveDeg] = useState<number | null>(null);
  const drag = useRef<{
    index: number;
    pivotX: number;
    pivotY: number;
    startAngle: number;
    prevAngle: number;
    accumRad: number;
    sign: number;
    value: number;
    latestValue: number;
    axis: number;
    baseRotation: [number, number, number, number];
    startOffset: [number, number, number];
  } | null>(null);
  const callbacks = useRef(editor);
  callbacks.current = editor;
  const interactingCb = useRef(onInteractingChange);
  interactingCb.current = onInteractingChange;
  const liveRotateCb = useRef(onLiveRotateJoint);
  liveRotateCb.current = onLiveRotateJoint;

  const skelInFlight = useRef(false);
  const skelQueued = useRef<{ graph: string; frame: number; skeletonPath: string } | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (drag.current) {
        const d = drag.current;
        drag.current = null;
        liveRotateCb.current?.(null);
        interactingCb.current?.(false);
        callbacks.current.onRotateJoint(d.index, d.axis, d.latestValue);
        callbacks.current.onCommit();
      }
    };
  }, []);

  useEffect(() => {
    const req = { graph, frame, skeletonPath: editor.skeletonPath };
    if (skelInFlight.current) {
      skelQueued.current = req;
      return;
    }
    const run = (current: { graph: string; frame: number; skeletonPath: string }) => {
      skelInFlight.current = true;
      invoke<{ skeletons: Bones[] }>("usd_request", {
        request: { operation: "inspect_stage_skeletons", graph: JSON.parse(current.graph), frame: current.frame },
      })
        .then((result) => {
          if (mounted.current) {
            setBones(result.skeletons.find((s) => s.path === current.skeletonPath) || null);
            setError("");
          }
        })
        .catch((e) => {
          if (mounted.current) setError(String(e));
        })
        .finally(() => {
          skelInFlight.current = false;
          const next = skelQueued.current;
          skelQueued.current = null;
          if (next && mounted.current) run(next);
        });
    };
    run(req);
  }, [graph, frame, editor.skeletonPath]);

  function project(p: number[]) {
    return matrix ? projectPosePoint(p, matrix, width, height) : null;
  }

  const activeWorldPoints = activeAxis !== null && liveBones?.worldPoints ? liveBones.worldPoints : bones?.worldPoints;
  const activeWorldAxes = activeAxis !== null && liveBones?.worldAxes ? liveBones.worldAxes : bones?.worldAxes;

  const points = activeWorldPoints?.map(project) || [];
  const selected = editor.jointIndex;
  const chain = new Set<number>([selected]);
  let parent = bones?.parents[selected] ?? -1;
  while (parent >= 0 && !chain.has(parent)) {
    chain.add(parent);
    parent = bones?.parents[parent] ?? -1;
  }
  bones?.parents.forEach((p, i) => {
    if (p === selected) chain.add(i);
  });

  // During active drag on dense rigs, show only the selected chain to avoid reconciling ~1,750 SVG nodes per frame.
  const showAllNow = all && activeAxis === null;

  // Exact constant-screen-radius 3D rotation gizmo at the selected joint.
  const selWorld = activeWorldPoints?.[selected] as Vec3 | undefined;
  const selScreen = points[selected];
  const selAxes: [Vec3, Vec3, Vec3] = (activeWorldAxes?.[selected] as [Vec3, Vec3, Vec3] | undefined) ?? [
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, 1],
  ];
  let worldRingRadius = 0.12;
  if (selWorld && selScreen && matrix) {
    const p11 = Math.hypot(matrix[0][1], matrix[1][1], matrix[2][1]);
    const clipW =
      selWorld[0] * matrix[0][3] +
      selWorld[1] * matrix[1][3] +
      selWorld[2] * matrix[2][3] +
      matrix[3][3];
    if (p11 > 1e-6 && clipW > 1e-6 && height > 0) {
      worldRingRadius = (44 * clipW) / (p11 * height * 0.5);
    }
  }

  const buildPath = (pts: ([number, number] | null)[]) => {
    const segments: string[] = [];
    let current = "";
    for (const pt of pts) {
      if (!pt) {
        if (current) {
          segments.push(current);
          current = "";
        }
      } else {
        current += (current ? " L " : "M ") + `${pt[0].toFixed(1)} ${pt[1].toFixed(1)}`;
      }
    }
    if (current) segments.push(current);
    return segments.join(" ");
  };

  const arcHandles =
    selWorld && selScreen && matrix
      ? GIZMO_AXES.map(({ idx, label, color }) => {
          const u = selAxes[(idx + 1) % 3];
          const v = selAxes[(idx + 2) % 3];
          const axisNormal = selAxes[idx];
          const arc3D: Vec3[] = [];
          const arc2D: ([number, number] | null)[] = [];
          for (let k = 0; k <= ARC_STEPS; k++) {
            const a = ARC_START + (k / ARC_STEPS) * (ARC_END - ARC_START);
            const pt = add3(add3(selWorld, u, Math.cos(a) * worldRingRadius), v, Math.sin(a) * worldRingRadius);
            arc3D.push(pt);
            arc2D.push(project(pt));
          }
          const midK = Math.floor(ARC_STEPS / 2);
          const midPt2D = arc2D[midK];
          const midPt3D = arc3D[midK];
          let guideD = "";
          if (activeAxis === idx) {
            const guide2D: ([number, number] | null)[] = [];
            for (let k = 0; k <= GUIDE_STEPS; k++) {
              const a = (k / GUIDE_STEPS) * Math.PI * 2;
              const pt = add3(add3(selWorld, u, Math.cos(a) * worldRingRadius), v, Math.sin(a) * worldRingRadius);
              guide2D.push(project(pt));
            }
            guideD = buildPath(guide2D);
          }
          return { idx, label, color, axisNormal, arc3D, arc2D, midPt2D, midPt3D, d: buildPath(arc2D), guideD };
        })
      : [];

  return (
    <>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        style={{ position: "absolute", inset: 0, width: "100%", height: "100%", zIndex: 8, pointerEvents: "none" }}
        aria-label="Pose skeleton bones"
      >
        {points.map((p, i) => {
          const par = bones?.parents[i] ?? -1;
          const pp = points[par];
          if (!p || !pp || (!showAllNow && !chain.has(i))) return null;
          return (
            <line
              key={`bone-${i}`}
              x1={pp[0]}
              y1={pp[1]}
              x2={p[0]}
              y2={p[1]}
              stroke={i === selected ? "#ffd37d" : "#8bdfed"}
              strokeWidth={i === selected ? 4 : 2}
              opacity={0.85}
              style={{ pointerEvents: interactive ? "stroke" : "none", cursor: "pointer" }}
              onPointerDown={(e) => {
                e.stopPropagation();
                e.preventDefault();
                editor.onSelectJoint(i);
              }}
            />
          );
        })}
        {points.map((p, i) =>
          !p || (!showAllNow && !chain.has(i)) ? null : (
            <circle
              key={`joint-${i}`}
              cx={p[0]}
              cy={p[1]}
              r={i === selected ? 5 : 3}
              fill={i === selected ? "#ffcb73" : "#b5f2f6"}
              stroke="#173a45"
              strokeWidth={1}
              style={{ pointerEvents: interactive ? "all" : "none", cursor: "pointer" }}
              onPointerDown={(e) => {
                e.stopPropagation();
                e.preventDefault();
                editor.onSelectJoint(i);
              }}
            >
              <title>{bones?.joints[i]} — click to select</title>
            </circle>
          ),
        )}
        {selScreen &&
          arcHandles.map((arc) => {
            if (!arc.d) return null;
            const isHot = activeAxis === arc.idx || hoverAxis === arc.idx;
            const startDrag = (e: React.PointerEvent<SVGElement>) => {
              const joint = editor.joints[selected];
              if (!joint || !selWorld || !selScreen) return;
              e.stopPropagation();
              e.preventDefault();
              const svg = e.currentTarget.ownerSVGElement;
              const rect = svg?.getBoundingClientRect();
              const px = ((e.clientX - (rect?.left ?? 0)) / Math.max(1, rect?.width ?? width)) * width;
              const py = ((e.clientY - (rect?.top ?? 0)) / Math.max(1, rect?.height ?? height)) * height;
              const p3 = arc.midPt3D;
              const radial = norm3([p3[0] - selWorld[0], p3[1] - selWorld[1], p3[2] - selWorld[2]]);
              const tan3 = norm3(cross3(arc.axisNormal, radial));
              const p2a = project(p3);
              const p2b = project(add3(p3, tan3, worldRingRadius * 0.15));
              let sign = 1;
              if (p2a && p2b) {
                const rx = p2a[0] - selScreen[0];
                const ry = p2a[1] - selScreen[1];
                const tx = p2b[0] - p2a[0];
                const ty = p2b[1] - p2a[1];
                const cross2d = rx * ty - ry * tx;
                if (Math.abs(cross2d) > 1e-4) sign = cross2d >= 0 ? 1 : -1;
              }
              const startAngle = Math.atan2(py - selScreen[1], px - selScreen[0]);
              e.currentTarget.setPointerCapture(e.pointerId);
              setActiveAxis(arc.idx);
              setLiveDeg(joint.rotationOffset[arc.idx]);
              interactingCb.current?.(true);
              drag.current = {
                index: selected,
                pivotX: selScreen[0],
                pivotY: selScreen[1],
                startAngle,
                prevAngle: startAngle,
                accumRad: 0,
                sign,
                value: joint.rotationOffset[arc.idx],
                latestValue: joint.rotationOffset[arc.idx],
                axis: arc.idx,
                baseRotation: [...joint.rotation] as [number, number, number, number],
                startOffset: [...joint.rotationOffset] as [number, number, number],
              };
            };
            const moveDrag = (e: React.PointerEvent<SVGElement>) => {
              const d = drag.current;
              if (!d) return;
              e.stopPropagation();
              const svg = e.currentTarget.ownerSVGElement;
              const rect = svg?.getBoundingClientRect();
              const px = ((e.clientX - (rect?.left ?? 0)) / Math.max(1, rect?.width ?? width)) * width;
              const py = ((e.clientY - (rect?.top ?? 0)) / Math.max(1, rect?.height ?? height)) * height;
              const angle = Math.atan2(py - d.pivotY, px - d.pivotX);
              let da = angle - d.prevAngle;
              if (da > Math.PI) da -= Math.PI * 2;
              if (da < -Math.PI) da += Math.PI * 2;
              d.prevAngle = angle;
              d.accumRad += da * d.sign;
              const nextVal = d.value + (d.accumRad * 180) / Math.PI;
              d.latestValue = nextVal;
              setLiveDeg(nextVal);
              if (liveRotateCb.current) {
                const rotationOffset: [number, number, number] = [...d.startOffset];
                rotationOffset[d.axis] = nextVal;
                liveRotateCb.current({
                  nodeId: callbacks.current.nodeId,
                  entryId: callbacks.current.entryId,
                  skeletonPath: callbacks.current.skeletonPath,
                  jointIndex: d.index,
                  baseRotation: d.baseRotation,
                  rotationOffset,
                });
              } else {
                callbacks.current.onRotateJoint(d.index, d.axis, nextVal);
              }
            };
            const endDrag = (e: React.PointerEvent<SVGElement>, isCancel = false) => {
              const d = drag.current;
              if (!d) return;
              if (!isCancel) e.stopPropagation();
              drag.current = null;
              setActiveAxis(null);
              setLiveDeg(null);
              liveRotateCb.current?.(null);
              interactingCb.current?.(false);
              callbacks.current.onRotateJoint(d.index, d.axis, d.latestValue);
              callbacks.current.onCommit();
            };
            return (
              <g key={`arc-handle-${arc.label}`}>
                {arc.guideD && (
                  <path
                    d={arc.guideD}
                    fill="none"
                    stroke={arc.color}
                    strokeWidth={1.25}
                    strokeDasharray="4 4"
                    opacity={0.45}
                    style={{ pointerEvents: "none" }}
                  />
                )}
                <path
                  d={arc.d}
                  fill="none"
                  stroke={arc.color}
                  strokeWidth={isHot ? 4 : 2.5}
                  strokeLinecap="round"
                  opacity={activeAxis !== null && activeAxis !== arc.idx ? 0.25 : 0.95}
                  style={{ pointerEvents: "none" }}
                />
                <path
                  d={arc.d}
                  fill="none"
                  stroke="transparent"
                  strokeWidth={16}
                  style={{ pointerEvents: interactive ? "stroke" : "none", cursor: "grab" }}
                  onPointerEnter={() => setHoverAxis(arc.idx)}
                  onPointerLeave={() => setHoverAxis((a) => (a === arc.idx ? null : a))}
                  onPointerDown={startDrag}
                  onPointerMove={moveDrag}
                  onPointerUp={(e) => endDrag(e)}
                  onPointerCancel={(e) => endDrag(e, true)}
                >
                  <title>Drag along arc to rotate local {arc.label}</title>
                </path>
                {arc.midPt2D && (
                  <g
                    style={{ pointerEvents: interactive ? "all" : "none", cursor: "grab" }}
                    onPointerEnter={() => setHoverAxis(arc.idx)}
                    onPointerLeave={() => setHoverAxis((a) => (a === arc.idx ? null : a))}
                    onPointerDown={startDrag}
                    onPointerMove={moveDrag}
                    onPointerUp={(e) => endDrag(e)}
                    onPointerCancel={(e) => endDrag(e, true)}
                  >
                    <circle
                      cx={arc.midPt2D[0]}
                      cy={arc.midPt2D[1]}
                      r={isHot ? 7 : 5.5}
                      fill={arc.color}
                      stroke="#0f172a"
                      strokeWidth={1.5}
                      opacity={activeAxis !== null && activeAxis !== arc.idx ? 0.25 : 1}
                    />
                    <text
                      x={arc.midPt2D[0]}
                      y={arc.midPt2D[1] + 3}
                      textAnchor="middle"
                      fontSize={8.5}
                      fontWeight={700}
                      fill="#ffffff"
                      style={{ pointerEvents: "none", userSelect: "none" }}
                    >
                      {arc.label}
                    </text>
                  </g>
                )}
              </g>
            );
          })}
      </svg>
      <div
        style={{
          position: "absolute",
          bottom: 52,
          left: 16,
          zIndex: 9,
          padding: "9px 12px",
          border: "1px solid #527078",
          borderRadius: 8,
          background: "#17202beF",
          maxWidth: "calc(100% - 32px)",
          fontSize: 11,
        }}
        onPointerDown={(e) => e.stopPropagation()}
      >
        <strong>Pose bones · X-ray</strong>
        <div style={{ margin: "5px 0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {editor.joints[selected]?.name.split("/").pop() || "Select a joint"}
          {activeAxis !== null && liveDeg !== null ? ` · ${"XYZ"[activeAxis]}: ${liveDeg.toFixed(1)}°` : ""}
        </div>
        <button onClick={() => setAll(!all)} style={{ padding: "3px 8px" }}>
          {all ? "Selected chain" : "All bones"}
        </button>
        <div style={{ marginTop: 5, color: "#b0c2cd" }}>Click any bone or joint; drag an X/Y/Z arc handle along its arc to rotate.</div>
        {(!matrix || error) && <div role="status">{error || "Waiting for viewport camera…"}</div>}
      </div>
    </>
  );
}
