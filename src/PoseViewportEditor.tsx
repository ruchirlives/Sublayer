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
type BoneVisMode = "primary" | "chain" | "all";

const GIZMO_AXES: { idx: 0 | 1 | 2; label: "X" | "Y" | "Z"; color: string }[] = [
  { idx: 0, label: "X", color: "#ef4444" },
  { idx: 1, label: "Y", color: "#22c55e" },
  { idx: 2, label: "Z", color: "#3b82f6" },
];
const ARC_STEPS = 18;
const ARC_START = (12 * Math.PI) / 180;
const ARC_END = (78 * Math.PI) / 180;
const GUIDE_STEPS = 36;

const HELPER_JOINT_RE =
  /(?:^|\/)(?:FACIAL_|.*(?:corrective|twist|bulge|_half|_side_|_inn_|_out_|_fwd_|_bck_|_pec_|latissimus|_scap_|_dip_|_pip_|_mcp_|_palm|eyeEdge|eyeshell|cartilage|saliva))/i;

function isHelperJoint(jointPath: string): boolean {
  return HELPER_JOINT_RE.test(jointPath);
}

function jointSideColor(jointPath: string): { stroke: string; fill: string } {
  const leaf = (jointPath.split("/").pop() || jointPath).toLowerCase();
  if (leaf.endsWith("_l") || leaf.endsWith(".l") || leaf.includes("_left") || leaf.startsWith("l_")) {
    return { stroke: "#38bdf8", fill: "rgba(56, 189, 248, 0.28)" };
  }
  if (leaf.endsWith("_r") || leaf.endsWith(".r") || leaf.includes("_right") || leaf.startsWith("r_")) {
    return { stroke: "#fb7185", fill: "rgba(251, 113, 133, 0.28)" };
  }
  return { stroke: "#e2e8f0", fill: "rgba(226, 232, 240, 0.25)" };
}

function kitePolygon(x0: number, y0: number, x1: number, y1: number): string | null {
  const dx = x1 - x0;
  const dy = y1 - y0;
  const len = Math.hypot(dx, dy);
  if (len < 3) return null;
  const ux = dx / len;
  const uy = dy / len;
  const px = -uy;
  const py = ux;
  const w = Math.min(7.5, Math.max(2, len * 0.11));
  const bx = x0 + ux * (len * 0.2);
  const by = y0 + uy * (len * 0.2);
  return `${x0.toFixed(1)},${y0.toFixed(1)} ${(bx + px * w).toFixed(1)},${(by + py * w).toFixed(1)} ${x1.toFixed(1)},${y1.toFixed(1)} ${(bx - px * w).toFixed(1)},${(by - py * w).toFixed(1)}`;
}

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
  const [visMode, setVisMode] = useState<BoneVisMode>("primary");
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

  function pointDepth(p: number[] | undefined): number {
    if (!p || !matrix) return 1;
    return p[0] * matrix[0][3] + p[1] * matrix[1][3] + p[2] * matrix[2][3] + matrix[3][3];
  }

  const activeWorldPoints = activeAxis !== null && liveBones?.worldPoints ? liveBones.worldPoints : bones?.worldPoints;
  const activeWorldAxes = activeAxis !== null && liveBones?.worldAxes ? liveBones.worldAxes : bones?.worldAxes;

  const points = activeWorldPoints?.map(project) || [];
  const depths = activeWorldPoints?.map(pointDepth) || [];
  const selected = editor.jointIndex;
  const chain = new Set<number>([selected]);
  let parent = bones?.parents[selected] ?? -1;
  while (parent >= 0 && !chain.has(parent)) {
    chain.add(parent);
    parent = bones?.parents[parent] ?? -1;
  }
  bones?.parents.forEach((p, i) => {
    if (p === selected && !isHelperJoint(bones?.joints[i] || "")) chain.add(i);
  });

  const effectiveMode: BoneVisMode = activeAxis !== null ? "chain" : visMode;
  const isJointVisible = (i: number): boolean => {
    if (i === selected) return true;
    const jPath = bones?.joints[i] || "";
    if (effectiveMode === "chain") return chain.has(i);
    if (effectiveMode === "primary") return !isHelperJoint(jPath);
    return true;
  };

  let minDepth = Infinity;
  let maxDepth = -Infinity;
  for (let i = 0; i < depths.length; i++) {
    if (!points[i] || !isJointVisible(i)) continue;
    const d = depths[i];
    if (d < minDepth) minDepth = d;
    if (d > maxDepth) maxDepth = d;
  }
  const depthSpan = Math.max(1e-4, maxDepth - minDepth);
  const depthOpacity = (i: number): number => {
    if (!Number.isFinite(minDepth) || i === selected) return 0.96;
    const t = Math.max(0, Math.min(1, (depths[i] - minDepth) / depthSpan));
    return 0.92 - t * 0.48;
  };

  // Exact constant-screen-radius 3D arc handles at the selected joint.
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
    const clipW = pointDepth(selWorld);
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
          const pp = par >= 0 ? points[par] : null;
          if (!p || !pp || !isJointVisible(i) || !isJointVisible(par)) return null;
          // In DCC convention, the bone segment from `par -> i` swings when `par` rotates.
          const isSelectedBone = par === selected;
          const jPath = bones?.joints[par] || bones?.joints[i] || "";
          const colors = isSelectedBone
            ? { stroke: "#ffd37d", fill: "rgba(255, 211, 125, 0.38)" }
            : jointSideColor(jPath);
          const poly = kitePolygon(pp[0], pp[1], p[0], p[1]);
          const op = isSelectedBone ? 0.98 : depthOpacity(par);
          const parName = (bones?.joints[par] || "").split("/").pop() || "";
          const childName = (bones?.joints[i] || "").split("/").pop() || "";
          if (poly) {
            return (
              <polygon
                key={`bone-${i}`}
                points={poly}
                fill={colors.fill}
                stroke={colors.stroke}
                strokeWidth={isSelectedBone ? 2 : 1.25}
                strokeLinejoin="round"
                opacity={op}
                style={{ pointerEvents: interactive ? "all" : "none", cursor: "pointer" }}
                onPointerDown={(e) => {
                  e.stopPropagation();
                  e.preventDefault();
                  editor.onSelectJoint(par);
                }}
              >
                <title>{`${parName} → ${childName} (click to select ${parName})`}</title>
              </polygon>
            );
          }
          return (
            <line
              key={`bone-${i}`}
              x1={pp[0]}
              y1={pp[1]}
              x2={p[0]}
              y2={p[1]}
              stroke={colors.stroke}
              strokeWidth={isSelectedBone ? 3.5 : 1.75}
              opacity={op}
              style={{ pointerEvents: interactive ? "stroke" : "none", cursor: "pointer" }}
              onPointerDown={(e) => {
                e.stopPropagation();
                e.preventDefault();
                editor.onSelectJoint(par);
              }}
            >
              <title>{`${parName} → ${childName} (click to select ${parName})`}</title>
            </line>
          );
        })}
        {points.map((p, i) => {
          if (!p || !isJointVisible(i)) return null;
          const isSel = i === selected;
          const jPath = bones?.joints[i] || "";
          const colors = isSel ? { stroke: "#ffd37d", fill: "#ffcb73" } : jointSideColor(jPath);
          return (
            <circle
              key={`joint-${i}`}
              cx={p[0]}
              cy={p[1]}
              r={isSel ? 5.5 : 3.2}
              fill={isSel ? "#ffcb73" : colors.stroke}
              stroke="#0f172a"
              strokeWidth={1.25}
              opacity={isSel ? 1 : depthOpacity(i)}
              style={{ pointerEvents: interactive ? "all" : "none", cursor: "pointer" }}
              onPointerDown={(e) => {
                e.stopPropagation();
                e.preventDefault();
                editor.onSelectJoint(i);
              }}
            >
              <title>{`${jPath.split("/").pop() || jPath} — click pivot to select`}</title>
            </circle>
          );
        })}
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
        <div style={{ display: "flex", gap: 4 }}>
          {(["primary", "chain", "all"] as BoneVisMode[]).map((m) => (
            <button
              key={m}
              aria-pressed={visMode === m}
              onClick={() => setVisMode(m)}
              style={{
                padding: "3px 8px",
                borderColor: visMode === m ? "#f2ca80" : undefined,
                textTransform: "capitalize",
              }}
            >
              {m === "primary" ? "Primary bones" : m === "chain" ? "Selected chain" : "All joints"}
            </button>
          ))}
        </div>
        <div style={{ marginTop: 5, color: "#b0c2cd" }}>
          Click a bone or joint pivot; drag an X/Y/Z arc handle along its arc to rotate.
        </div>
        {(!matrix || error) && <div role="status">{error || "Waiting for viewport camera…"}</div>}
      </div>
    </>
  );
}
