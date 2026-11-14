import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type NodeProps,
} from "@xyflow/react";
import type {
  ProceduralGraph,
  ProceduralGraphEdge,
  ProceduralGraphNode,
  ProceduralGraphNodeData,
  ProceduralMappingType,
  ProceduralNodeKind,
  StoredProject,
} from "./fileRegistry";

export type SocketType = "float" | "vector2" | "vector3" | "color3" | "dynamic" | "string" | "integer" | "boolean";

export interface ProceduralPortSpec {
  name: string;
  label: string;
  type: SocketType;
  defaultValue?: number | number[] | string | boolean;
  connectable?: boolean;
  options?: { value: string | number; label: string }[];
  min?: number;
  max?: number;
  step?: number;
}

export interface ProceduralNodeSpec {
  kind: ProceduralNodeKind;
  label: string;
  category: "Coordinates" | "Generators" | "Math & Color" | "Output";
  outType?: SocketType;
  inputs: ProceduralPortSpec[];
}

export const PROCEDURAL_NODE_SPECS: Record<Exclude<ProceduralNodeKind, "triplanar">, ProceduralNodeSpec> = {
  uv: {
    kind: "uv",
    label: "UV Coords",
    category: "Coordinates",
    outType: "vector2",
    inputs: [{ name: "geomprop", label: "Attr", type: "string", defaultValue: "st", connectable: false }],
  },
  place2d: {
    kind: "place2d",
    label: "Place 2D",
    category: "Coordinates",
    outType: "vector2",
    inputs: [
      { name: "texcoord", label: "UV", type: "vector2" },
      { name: "scale", label: "Scale", type: "vector2", defaultValue: [1, 1], step: 0.1 },
      { name: "rotate", label: "Rotate", type: "float", defaultValue: 0, step: 1 },
      { name: "offset", label: "Offset", type: "vector2", defaultValue: [0, 0], step: 0.05 },
      { name: "pivot", label: "Pivot", type: "vector2", defaultValue: [0, 0], step: 0.05 },
    ],
  },
  position: {
    kind: "position",
    label: "Position 3D",
    category: "Coordinates",
    outType: "vector3",
    inputs: [
      {
        name: "space",
        label: "Space",
        type: "string",
        defaultValue: "object",
        connectable: false,
        options: [
          { value: "object", label: "Object" },
          { value: "world", label: "World" },
          { value: "model", label: "Model" },
        ],
      },
    ],
  },
  place3d: {
    kind: "place3d",
    label: "Place 3D",
    category: "Coordinates",
    outType: "vector3",
    inputs: [
      { name: "position", label: "Pos", type: "vector3" },
      { name: "scale", label: "Scale", type: "vector3", defaultValue: [1, 1, 1], step: 0.1 },
      { name: "rotate", label: "Rotate Y", type: "float", defaultValue: 0, step: 1 },
      { name: "offset", label: "Offset", type: "vector3", defaultValue: [0, 0, 0], step: 0.05 },
    ],
  },
  noise2d: {
    kind: "noise2d",
    label: "Noise 2D",
    category: "Generators",
    outType: "float",
    inputs: [
      { name: "texcoord", label: "UV", type: "vector2" },
      { name: "amplitude", label: "Amp", type: "float", defaultValue: 1, step: 0.05 },
      { name: "pivot", label: "Pivot", type: "float", defaultValue: 0, step: 0.05 },
    ],
  },
  noise3d: {
    kind: "noise3d",
    label: "Noise 3D",
    category: "Generators",
    outType: "float",
    inputs: [
      { name: "position", label: "Pos", type: "vector3" },
      { name: "amplitude", label: "Amp", type: "float", defaultValue: 1, step: 0.05 },
      { name: "pivot", label: "Pivot", type: "float", defaultValue: 0, step: 0.05 },
    ],
  },
  fractal3d: {
    kind: "fractal3d",
    label: "Fractal 3D",
    category: "Generators",
    outType: "float",
    inputs: [
      { name: "position", label: "Pos", type: "vector3" },
      { name: "amplitude", label: "Amp", type: "float", defaultValue: 1, step: 0.05 },
      { name: "octaves", label: "Octaves", type: "integer", defaultValue: 3, min: 1, max: 8, step: 1 },
      { name: "lacunarity", label: "Lacunarity", type: "float", defaultValue: 2, min: 1, max: 4, step: 0.1 },
      { name: "diminish", label: "Diminish", type: "float", defaultValue: 0.5, min: 0, max: 1, step: 0.05 },
    ],
  },
  worley2d: {
    kind: "worley2d",
    label: "Worley 2D",
    category: "Generators",
    outType: "float",
    inputs: [
      { name: "texcoord", label: "UV", type: "vector2" },
      { name: "jitter", label: "Jitter", type: "float", defaultValue: 1, min: 0, max: 1, step: 0.05 },
    ],
  },
  worley3d: {
    kind: "worley3d",
    label: "Worley 3D",
    category: "Generators",
    outType: "float",
    inputs: [
      { name: "position", label: "Pos", type: "vector3" },
      { name: "jitter", label: "Jitter", type: "float", defaultValue: 1, min: 0, max: 1, step: 0.05 },
    ],
  },
  cell2d: {
    kind: "cell2d",
    label: "Cell 2D",
    category: "Generators",
    outType: "float",
    inputs: [{ name: "texcoord", label: "UV", type: "vector2" }],
  },
  cell3d: {
    kind: "cell3d",
    label: "Cell 3D",
    category: "Generators",
    outType: "float",
    inputs: [{ name: "position", label: "Pos", type: "vector3" }],
  },
  unified2d: {
    kind: "unified2d",
    label: "Unified 2D",
    category: "Generators",
    outType: "float",
    inputs: [
      { name: "texcoord", label: "UV", type: "vector2" },
      {
        name: "type",
        label: "Type",
        type: "integer",
        defaultValue: 0,
        connectable: false,
        options: [
          { value: 0, label: "Perlin" },
          { value: 1, label: "Cell" },
          { value: 2, label: "Worley" },
          { value: 3, label: "Fractal" },
        ],
      },
      { name: "freq", label: "Freq", type: "vector2", defaultValue: [1, 1], step: 0.1 },
      { name: "jitter", label: "Jitter", type: "float", defaultValue: 1, min: 0, max: 1, step: 0.05 },
      { name: "octaves", label: "Octaves", type: "integer", defaultValue: 3, min: 1, max: 8, step: 1 },
      { name: "clampoutput", label: "Clamp", type: "boolean", defaultValue: true, connectable: false },
    ],
  },
  unified3d: {
    kind: "unified3d",
    label: "Unified 3D",
    category: "Generators",
    outType: "float",
    inputs: [
      { name: "position", label: "Pos", type: "vector3" },
      {
        name: "type",
        label: "Type",
        type: "integer",
        defaultValue: 0,
        connectable: false,
        options: [
          { value: 0, label: "Perlin" },
          { value: 1, label: "Cell" },
          { value: 2, label: "Worley" },
          { value: 3, label: "Fractal" },
        ],
      },
      { name: "freq", label: "Freq", type: "vector3", defaultValue: [1, 1, 1], step: 0.1 },
      { name: "jitter", label: "Jitter", type: "float", defaultValue: 1, min: 0, max: 1, step: 0.05 },
      { name: "octaves", label: "Octaves", type: "integer", defaultValue: 3, min: 1, max: 8, step: 1 },
      { name: "clampoutput", label: "Clamp", type: "boolean", defaultValue: true, connectable: false },
    ],
  },
  checkerboard: {
    kind: "checkerboard",
    label: "Checkerboard",
    category: "Generators",
    outType: "color3",
    inputs: [
      { name: "texcoord", label: "UV", type: "vector2" },
      { name: "color1", label: "Color 1", type: "color3", defaultValue: [0.05, 0.05, 0.05] },
      { name: "color2", label: "Color 2", type: "color3", defaultValue: [0.95, 0.95, 0.95] },
      { name: "uvtiling", label: "Tiling", type: "vector2", defaultValue: [4, 4], step: 0.5 },
      { name: "uvoffset", label: "Offset", type: "vector2", defaultValue: [0, 0], step: 0.05 },
    ],
  },
  grid: {
    kind: "grid",
    label: "Grid",
    category: "Generators",
    outType: "color3",
    inputs: [
      { name: "texcoord", label: "UV", type: "vector2" },
      { name: "uvtiling", label: "Tiling", type: "vector2", defaultValue: [4, 4], step: 0.5 },
      { name: "thickness", label: "Thick", type: "float", defaultValue: 0.05, min: 0, max: 1, step: 0.01 },
      { name: "staggered", label: "Stagger", type: "boolean", defaultValue: false, connectable: false },
    ],
  },
  tiledcircles: {
    kind: "tiledcircles",
    label: "Tiled Circles",
    category: "Generators",
    outType: "color3",
    inputs: [
      { name: "texcoord", label: "UV", type: "vector2" },
      { name: "uvtiling", label: "Tiling", type: "vector2", defaultValue: [4, 4], step: 0.5 },
      { name: "size", label: "Size", type: "float", defaultValue: 0.5, min: 0, max: 1, step: 0.02 },
      { name: "staggered", label: "Stagger", type: "boolean", defaultValue: false, connectable: false },
    ],
  },
  tiledhexagons: {
    kind: "tiledhexagons",
    label: "Tiled Hex",
    category: "Generators",
    outType: "color3",
    inputs: [
      { name: "texcoord", label: "UV", type: "vector2" },
      { name: "uvtiling", label: "Tiling", type: "vector2", defaultValue: [4, 4], step: 0.5 },
      { name: "size", label: "Size", type: "float", defaultValue: 0.5, min: 0, max: 1, step: 0.02 },
      { name: "staggered", label: "Stagger", type: "boolean", defaultValue: false, connectable: false },
    ],
  },
  ramplr: {
    kind: "ramplr",
    label: "Ramp L→R",
    category: "Generators",
    outType: "color3",
    inputs: [
      { name: "texcoord", label: "UV", type: "vector2" },
      { name: "valuel", label: "Left", type: "color3", defaultValue: [0, 0, 0] },
      { name: "valuer", label: "Right", type: "color3", defaultValue: [1, 1, 1] },
    ],
  },
  ramptb: {
    kind: "ramptb",
    label: "Ramp T→B",
    category: "Generators",
    outType: "color3",
    inputs: [
      { name: "texcoord", label: "UV", type: "vector2" },
      { name: "valuet", label: "Top", type: "color3", defaultValue: [1, 1, 1] },
      { name: "valueb", label: "Bottom", type: "color3", defaultValue: [0, 0, 0] },
    ],
  },
  mix: {
    kind: "mix",
    label: "Mix",
    category: "Math & Color",
    outType: "dynamic",
    inputs: [
      { name: "fg", label: "FG", type: "dynamic", defaultValue: [1, 1, 1] },
      { name: "bg", label: "BG", type: "dynamic", defaultValue: [0, 0, 0] },
      { name: "mix", label: "Mix", type: "float", defaultValue: 0.5, min: 0, max: 1, step: 0.02 },
    ],
  },
  multiply: {
    kind: "multiply",
    label: "Multiply",
    category: "Math & Color",
    outType: "dynamic",
    inputs: [
      { name: "in1", label: "In 1", type: "dynamic", defaultValue: 1 },
      { name: "in2", label: "In 2", type: "dynamic", defaultValue: 1 },
    ],
  },
  add: {
    kind: "add",
    label: "Add",
    category: "Math & Color",
    outType: "dynamic",
    inputs: [
      { name: "in1", label: "In 1", type: "dynamic", defaultValue: 0 },
      { name: "in2", label: "In 2", type: "dynamic", defaultValue: 0 },
    ],
  },
  remap: {
    kind: "remap",
    label: "Remap",
    category: "Math & Color",
    outType: "dynamic",
    inputs: [
      { name: "in", label: "In", type: "dynamic", defaultValue: 0 },
      { name: "inlow", label: "In Low", type: "float", defaultValue: 0, step: 0.05 },
      { name: "inhigh", label: "In High", type: "float", defaultValue: 1, step: 0.05 },
      { name: "outlow", label: "Out Low", type: "float", defaultValue: 0, step: 0.05 },
      { name: "outhigh", label: "Out High", type: "float", defaultValue: 1, step: 0.05 },
    ],
  },
  clamp: {
    kind: "clamp",
    label: "Clamp",
    category: "Math & Color",
    outType: "dynamic",
    inputs: [
      { name: "in", label: "In", type: "dynamic", defaultValue: 0 },
      { name: "low", label: "Low", type: "float", defaultValue: 0, step: 0.05 },
      { name: "high", label: "High", type: "float", defaultValue: 1, step: 0.05 },
    ],
  },
  contrast: {
    kind: "contrast",
    label: "Contrast",
    category: "Math & Color",
    outType: "dynamic",
    inputs: [
      { name: "in", label: "In", type: "dynamic", defaultValue: 0 },
      { name: "amount", label: "Amount", type: "float", defaultValue: 1, step: 0.05 },
      { name: "pivot", label: "Pivot", type: "float", defaultValue: 0.5, step: 0.05 },
    ],
  },
  saturate: {
    kind: "saturate",
    label: "Saturate",
    category: "Math & Color",
    outType: "color3",
    inputs: [
      { name: "in", label: "In", type: "color3", defaultValue: [0.5, 0.5, 0.5] },
      { name: "amount", label: "Amount", type: "float", defaultValue: 1, min: 0, max: 3, step: 0.05 },
    ],
  },
  hsvadjust: {
    kind: "hsvadjust",
    label: "HSV Adjust",
    category: "Math & Color",
    outType: "color3",
    inputs: [
      { name: "in", label: "In", type: "color3", defaultValue: [0.5, 0.5, 0.5] },
      { name: "amount", label: "H/S/V", type: "vector3", defaultValue: [0, 1, 1], step: 0.05 },
    ],
  },
  bump: {
    kind: "bump",
    label: "Bump",
    category: "Math & Color",
    outType: "vector3",
    inputs: [
      { name: "height", label: "Height", type: "float", defaultValue: 0 },
      { name: "scale", label: "Scale", type: "float", defaultValue: 0.1, min: -2, max: 2, step: 0.02 },
    ],
  },
  colorramp: {
    kind: "colorramp",
    label: "Color Ramp",
    category: "Math & Color",
    outType: "color3",
    inputs: [{ name: "in", label: "Fac", type: "float", defaultValue: 0.5, min: 0, max: 1, step: 0.02 }],
  },
  output: {
    kind: "output",
    label: "MaterialX Output",
    category: "Output",
    inputs: [{ name: "in", label: "Value", type: "dynamic" }],
  },
};

export const STANDARD_SURFACE_PARAMETER_GROUPS: {
  group: string;
  items: { value: string; label: string }[];
}[] = [
  {
    group: "Base",
    items: [
      { value: "base_color", label: "Base Color" },
      { value: "base", label: "Base Weight" },
      { value: "metalness", label: "Metalness" },
    ],
  },
  {
    group: "Specular",
    items: [
      { value: "specular", label: "Specular Weight" },
      { value: "specular_color", label: "Specular Color" },
      { value: "specular_roughness", label: "Specular Roughness" },
      { value: "specular_IOR", label: "Specular IOR" },
    ],
  },
  {
    group: "Coat",
    items: [
      { value: "coat", label: "Coat Weight" },
      { value: "coat_roughness", label: "Coat Roughness" },
      { value: "coat_color", label: "Coat Color" },
    ],
  },
  {
    group: "Sheen",
    items: [
      { value: "sheen", label: "Sheen Weight" },
      { value: "sheen_roughness", label: "Sheen Roughness" },
      { value: "sheen_color", label: "Sheen Color" },
    ],
  },
  {
    group: "Transmission & Emission",
    items: [
      { value: "transmission", label: "Transmission" },
      { value: "transmission_color", label: "Transmission Color" },
      { value: "emission", label: "Emission" },
      { value: "emission_color", label: "Emission Color" },
    ],
  },
  {
    group: "Subsurface & Geometry",
    items: [
      { value: "subsurface", label: "Subsurface" },
      { value: "subsurface_color", label: "Subsurface Color" },
      { value: "subsurface_scale", label: "Subsurface Scale" },
      { value: "subsurface_radius", label: "Subsurface Radius" },
      { value: "opacity", label: "Opacity" },
      { value: "normal", label: "Normal" },
    ],
  },
];

export const MAPPING_TYPES: { value: ProceduralMappingType; label: string }[] = [
  { value: "replace", label: "Replace" },
  { value: "multiply", label: "Multiply" },
  { value: "add", label: "Add" },
  { value: "mix", label: "Mix" },
  { value: "bump_normal", label: "Bump → Normal" },
];

const SOCKET_COLORS: Record<SocketType, string> = {
  float: "#68d391",
  vector2: "#63b3ed",
  vector3: "#b794f4",
  color3: "#f6ad55",
  dynamic: "#cbd5e0",
  string: "#a0aec0",
  integer: "#68d391",
  boolean: "#81e6d9",
};

const CATEGORY_COLORS: Record<ProceduralNodeSpec["category"], string> = {
  Coordinates: "#2c5282",
  Generators: "#276749",
  "Math & Color": "#744210",
  Output: "#702459",
};

const PALETTE_GROUPS: { category: ProceduralNodeSpec["category"]; kinds: (Exclude<ProceduralNodeKind, "triplanar">)[] }[] = [
  { category: "Coordinates", kinds: ["uv", "place2d", "position", "place3d"] },
  {
    category: "Generators",
    kinds: [
      "noise2d",
      "noise3d",
      "fractal3d",
      "worley2d",
      "worley3d",
      "cell2d",
      "cell3d",
      "unified2d",
      "unified3d",
      "checkerboard",
      "grid",
      "tiledcircles",
      "tiledhexagons",
      "ramplr",
      "ramptb",
    ],
  },
  {
    category: "Math & Color",
    kinds: ["colorramp", "mix", "multiply", "add", "remap", "clamp", "contrast", "saturate", "hsvadjust", "bump"],
  },
  { category: "Output", kinds: ["output"] },
];

export function wouldCreateProceduralCycle(
  edges: { source: string; target: string }[],
  source: string,
  target: string,
): boolean {
  if (source === target) return true;
  const visited = new Set<string>();
  const queue = [target];
  while (queue.length > 0) {
    const curr = queue.shift()!;
    if (curr === source) return true;
    if (visited.has(curr)) continue;
    visited.add(curr);
    for (const edge of edges) {
      if (edge.source === curr && !visited.has(edge.target)) {
        queue.push(edge.target);
      }
    }
  }
  return false;
}

export function isSocketCompatible(sourceType: SocketType | undefined, targetType: SocketType | undefined): boolean {
  if (!sourceType || !targetType) return false;
  if (sourceType === targetType) return true;
  if (sourceType === "vector2" || targetType === "vector2") return false;
  if (targetType === "dynamic" || sourceType === "dynamic") return true;
  if (sourceType === "float" && targetType === "vector3") return true;
  const scalarOrColor = new Set<SocketType>(["float", "color3", "integer"]);
  return scalarOrColor.has(sourceType) && scalarOrColor.has(targetType);
}

function rgbToHex(rgb: unknown): string {
  const arr = Array.isArray(rgb) ? rgb : [0.5, 0.5, 0.5];
  const byte = (v: number) => Math.max(0, Math.min(255, Math.round((Number(v) || 0) * 255))).toString(16).padStart(2, "0");
  return `#${byte(arr[0])}${byte(arr[1])}${byte(arr[2])}`;
}

function hexToRgb(hex: string): [number, number, number] {
  const clean = hex.replace("#", "");
  const n = parseInt(clean.length === 3 ? clean.replace(/(.)/g, "$1$1") : clean, 16);
  if (Number.isNaN(n)) return [0.5, 0.5, 0.5];
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

export interface RuleTargetOption {
  materialXNodeId: string;
  ruleId: string;
  label: string;
}

export function collectRuleTargetOptions(project: StoredProject): RuleTargetOption[] {
  const options: RuleTargetOption[] = [];
  for (const node of project.nodes) {
    if (node.data.operation !== "materialx") continue;
    const nodeLabel = node.data.label || node.id;
    const rules = node.data.materialXRules;
    if (rules && rules.length > 0) {
      for (const rule of rules) {
        options.push({
          materialXNodeId: node.id,
          ruleId: rule.id,
          label: `${nodeLabel} — ${rule.material?.name || "Material"} (${rule.query || "*"})`,
        });
      }
    } else {
      options.push({
        materialXNodeId: node.id,
        ruleId: "",
        label: `${nodeLabel} — ${node.data.materialX?.name || "Material"} (*)`,
      });
    }
  }
  return options;
}

type FlowNodeData = ProceduralGraphNodeData & {
  connectedPorts?: string[];
  ruleOptions?: RuleTargetOption[];
  onUpdateData?: (nodeId: string, patch: Partial<ProceduralGraphNodeData>) => void;
  onDeleteNode?: (nodeId: string) => void;
};

function ProceduralNodeCard({ id, data }: NodeProps<Node<FlowNodeData>>) {
  const kind = (data.kind === "triplanar" ? "noise3d" : data.kind) as Exclude<ProceduralNodeKind, "triplanar">;
  const spec = PROCEDURAL_NODE_SPECS[kind];
  const connected = new Set(data.connectedPorts ?? []);
  const params = data.params ?? {};

  function setParam(key: string, value: unknown) {
    data.onUpdateData?.(id, { params: { ...params, [key]: value } });
  }

  function renderInputControl(port: ProceduralPortSpec) {
    const val = params[port.name] ?? port.defaultValue;
    if (port.options) {
      return (
        <select
          className="nodrag nopan proc-input"
          value={String(val ?? port.defaultValue ?? "")}
          onChange={(e) => setParam(port.name, port.type === "integer" ? Number(e.target.value) : e.target.value)}
        >
          {port.options.map((opt) => (
            <option key={String(opt.value)} value={String(opt.value)}>
              {opt.label}
            </option>
          ))}
        </select>
      );
    }
    if (port.type === "boolean") {
      return (
        <input
          type="checkbox"
          className="nodrag nopan"
          checked={Boolean(val)}
          onChange={(e) => setParam(port.name, e.target.checked)}
        />
      );
    }
    if (port.type === "color3" || (port.type === "dynamic" && Array.isArray(val))) {
      return (
        <input
          type="color"
          className="nodrag nopan proc-color"
          value={rgbToHex(val)}
          onChange={(e) => setParam(port.name, hexToRgb(e.target.value))}
        />
      );
    }
    if (port.type === "vector2") {
      const arr = Array.isArray(val) ? val : [0, 0];
      return (
        <div className="proc-vec">
          {[0, 1].map((idx) => (
            <input
              key={idx}
              type="number"
              className="nodrag nopan proc-num"
              step={port.step ?? 0.1}
              value={Number(arr[idx] ?? 0)}
              onChange={(e) => {
                const next = [Number(arr[0] ?? 0), Number(arr[1] ?? 0)];
                next[idx] = Number(e.target.value) || 0;
                setParam(port.name, next);
              }}
            />
          ))}
        </div>
      );
    }
    if (port.type === "vector3") {
      const arr = Array.isArray(val) ? val : [0, 0, 0];
      return (
        <div className="proc-vec">
          {[0, 1, 2].map((idx) => (
            <input
              key={idx}
              type="number"
              className="nodrag nopan proc-num"
              step={port.step ?? 0.1}
              value={Number(arr[idx] ?? 0)}
              onChange={(e) => {
                const next = [Number(arr[0] ?? 0), Number(arr[1] ?? 0), Number(arr[2] ?? 0)];
                next[idx] = Number(e.target.value) || 0;
                setParam(port.name, next);
              }}
            />
          ))}
        </div>
      );
    }
    if (port.type === "string") {
      return (
        <input
          type="text"
          className="nodrag nopan proc-input"
          value={String(val ?? "")}
          onChange={(e) => setParam(port.name, e.target.value)}
        />
      );
    }
    return (
      <input
        type="number"
        className="nodrag nopan proc-num"
        min={port.min}
        max={port.max}
        step={port.step ?? 0.05}
        value={Number(val ?? 0)}
        onChange={(e) => setParam(port.name, Number(e.target.value) || 0)}
      />
    );
  }

  const ruleOptions = data.ruleOptions ?? [];
  const selectedRuleKey = `${data.materialXNodeId ?? ""}::${data.ruleId ?? ""}`;
  const mappingType = data.mappingType ?? "replace";
  const needsStrength = mappingType === "mix" || mappingType === "add" || mappingType === "bump_normal";

  const rampStops: { pos: number; color: [number, number, number] }[] = Array.isArray(params.stops)
    ? (params.stops as { pos: number; color: [number, number, number] }[])
    : [
        { pos: 0, color: [0, 0, 0] },
        { pos: 1, color: [1, 1, 1] },
      ];
  const rampInterp = params.interpolation === "constant" ? "constant" : "linear";
  const sortedStops = [...rampStops].map((s, idx) => ({ ...s, idx })).sort((a, b) => a.pos - b.pos);
  const gradientCss = (() => {
    if (sortedStops.length === 0) return "#000";
    if (sortedStops.length === 1) return rgbToHex(sortedStops[0].color);
    if (rampInterp === "constant") {
      const parts: string[] = [];
      for (let i = 0; i < sortedStops.length; i++) {
        const cur = sortedStops[i];
        const nextPos = i < sortedStops.length - 1 ? sortedStops[i + 1].pos * 100 : 100;
        parts.push(`${rgbToHex(cur.color)} ${(cur.pos * 100).toFixed(1)}% ${nextPos.toFixed(1)}%`);
      }
      return `linear-gradient(90deg, ${parts.join(", ")})`;
    }
    return `linear-gradient(90deg, ${sortedStops.map((s) => `${rgbToHex(s.color)} ${(s.pos * 100).toFixed(1)}%`).join(", ")})`;
  })();

  return (
    <div className={`proc-node ${kind === "output" ? "proc-node-output" : ""}`}>
      <div className="proc-node-header" style={{ background: CATEGORY_COLORS[spec.category] }}>
        <span>{spec.label}</span>
        <button
          type="button"
          className="nodrag nopan proc-del-btn"
          title="Delete node"
          onClick={() => data.onDeleteNode?.(id)}
        >
          ×
        </button>
      </div>
      <div className="proc-node-body">
        {spec.outType && (
          <div className="proc-out-row">
            <span className="proc-port-label">out ({spec.outType})</span>
            <Handle
              type="source"
              position={Position.Right}
              id="out"
              className="proc-handle"
              style={{ background: SOCKET_COLORS[spec.outType] }}
            />
          </div>
        )}

        {spec.inputs.map((port) => {
          const isConnectable = port.connectable !== false;
          const isLinked = connected.has(port.name);
          return (
            <div key={port.name} className="proc-port-row">
              {isConnectable && (
                <Handle
                  type="target"
                  position={Position.Left}
                  id={port.name}
                  className="proc-handle"
                  style={{ background: SOCKET_COLORS[port.type] }}
                />
              )}
              <span className="proc-port-label">{port.label}</span>
              {!isLinked && port.defaultValue !== undefined && (
                <div className="proc-port-ctrl">{renderInputControl(port)}</div>
              )}
              {isLinked && <span className="proc-linked-tag">linked</span>}
            </div>
          );
        })}

        {kind === "colorramp" && (
          <div className="proc-ramp-box nodrag nopan">
            <div className="proc-ramp-bar" style={{ background: gradientCss }} />
            <div className="proc-ramp-toolbar">
              <select
                className="proc-input"
                value={rampInterp}
                onChange={(e) => setParam("interpolation", e.target.value)}
              >
                <option value="linear">Linear</option>
                <option value="constant">Constant</option>
              </select>
              <button
                type="button"
                className="proc-ramp-add"
                onClick={() => {
                  const midPos = 0.5;
                  setParam("stops", [...rampStops, { pos: midPos, color: [0.5, 0.5, 0.5] }]);
                }}
              >
                + Stop
              </button>
            </div>
            <div className="proc-ramp-stops">
              {sortedStops.map((stop) => (
                <div key={stop.idx} className="proc-ramp-stop-row">
                  <input
                    type="color"
                    className="proc-color"
                    value={rgbToHex(stop.color)}
                    onChange={(e) => {
                      const next = rampStops.map((s, i) => (i === stop.idx ? { ...s, color: hexToRgb(e.target.value) } : s));
                      setParam("stops", next);
                    }}
                  />
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.01}
                    value={stop.pos}
                    onChange={(e) => {
                      const next = rampStops.map((s, i) =>
                        i === stop.idx ? { ...s, pos: Math.max(0, Math.min(1, Number(e.target.value))) } : s,
                      );
                      setParam("stops", next);
                    }}
                  />
                  <input
                    type="number"
                    className="proc-num"
                    min={0}
                    max={1}
                    step={0.02}
                    value={Number(stop.pos.toFixed(2))}
                    onChange={(e) => {
                      const next = rampStops.map((s, i) =>
                        i === stop.idx ? { ...s, pos: Math.max(0, Math.min(1, Number(e.target.value) || 0)) } : s,
                      );
                      setParam("stops", next);
                    }}
                  />
                  {rampStops.length > 2 && (
                    <button
                      type="button"
                      className="proc-del-btn"
                      title="Remove stop"
                      onClick={() => setParam("stops", rampStops.filter((_, i) => i !== stop.idx))}
                    >
                      ×
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {kind === "output" && (
          <div className="proc-output-controls nodrag nopan">
            <label>
              Target Rule
              <select
                className="proc-input"
                value={selectedRuleKey}
                onChange={(e) => {
                  const [mxId, rId] = e.target.value.split("::");
                  data.onUpdateData?.(id, {
                    materialXNodeId: mxId || undefined,
                    ruleId: rId || undefined,
                  });
                }}
              >
                <option value="::">Select MaterialX Rule…</option>
                {ruleOptions.map((opt) => (
                  <option key={`${opt.materialXNodeId}::${opt.ruleId}`} value={`${opt.materialXNodeId}::${opt.ruleId}`}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Parameter
              <select
                className="proc-input"
                value={data.parameter ?? "base_color"}
                onChange={(e) => data.onUpdateData?.(id, { parameter: e.target.value })}
              >
                {STANDARD_SURFACE_PARAMETER_GROUPS.map((grp) => (
                  <optgroup key={grp.group} label={grp.group}>
                    {grp.items.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </label>
            <label>
              Mapping
              <select
                className="proc-input"
                value={mappingType}
                onChange={(e) =>
                  data.onUpdateData?.(id, { mappingType: e.target.value as ProceduralMappingType })
                }
              >
                {MAPPING_TYPES.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            </label>
            {needsStrength && (
              <label>
                {mappingType === "bump_normal" ? "Bump Scale" : "Strength"}
                <div className="proc-vec">
                  <input
                    type="range"
                    min={0}
                    max={mappingType === "bump_normal" ? 2 : 1}
                    step={0.02}
                    value={Number(data.strength ?? (mappingType === "bump_normal" ? 0.2 : 1))}
                    onChange={(e) => data.onUpdateData?.(id, { strength: Number(e.target.value) })}
                  />
                  <input
                    type="number"
                    className="proc-num"
                    step={0.05}
                    value={Number(data.strength ?? (mappingType === "bump_normal" ? 0.2 : 1))}
                    onChange={(e) => data.onUpdateData?.(id, { strength: Number(e.target.value) || 0 })}
                  />
                </div>
              </label>
            )}
            <label className="proc-enabled-row">
              <input
                type="checkbox"
                checked={data.enabled !== false}
                onChange={(e) => data.onUpdateData?.(id, { enabled: e.target.checked })}
              />
              <span>Enabled</span>
            </label>
          </div>
        )}
      </div>
    </div>
  );
}

const NODE_TYPES = { procedural: ProceduralNodeCard };

function ProceduralEditorInner({
  project,
  activeMaterialXNodeId,
  activeRuleId,
  graph,
  onGraphChange,
  focusNodeId,
  onFocusHandled,
  onInspectOutput,
}: {
  project: StoredProject;
  activeMaterialXNodeId: string;
  activeRuleId?: string;
  graph: ProceduralGraph;
  onGraphChange: (next: ProceduralGraph) => void;
  focusNodeId?: string | null;
  onFocusHandled?: () => void;
  onInspectOutput?: (node: ProceduralGraphNode) => void;
}) {
  const [statusMessage, setStatusMessage] = useState("");
  const { screenToFlowPosition, setCenter } = useReactFlow();
  const ruleOptions = useMemo(() => collectRuleTargetOptions(project), [project]);
  const graphRef = useRef(graph);
  graphRef.current = graph;

  const toFlowEdges = useCallback(
    (edges: ProceduralGraphEdge[]): Edge[] =>
      edges.map((e) => ({
        id: e.id,
        source: e.source,
        sourceHandle: e.sourceHandle ?? "out",
        target: e.target,
        targetHandle: e.targetHandle ?? "in",
      })),
    [],
  );

  const updateNodeData = useCallback(
    (nodeId: string, patch: Partial<ProceduralGraphNodeData>) => {
      const curr = graphRef.current;
      onGraphChange({
        ...curr,
        nodes: curr.nodes.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, ...patch } } : n)),
      });
    },
    [onGraphChange],
  );

  const deleteNode = useCallback(
    (nodeId: string) => {
      const curr = graphRef.current;
      onGraphChange({
        nodes: curr.nodes.filter((n) => n.id !== nodeId),
        edges: curr.edges.filter((e) => e.source !== nodeId && e.target !== nodeId),
      });
    },
    [onGraphChange],
  );

  const toFlowNodes = useCallback(
    (nodes: ProceduralGraphNode[], edges: ProceduralGraphEdge[], prevNodes: Node<FlowNodeData>[] = []): Node<FlowNodeData>[] => {
      const prevMap = new Map(prevNodes.map((n) => [n.id, n]));
      return nodes.map((n) => {
        const prev = prevMap.get(n.id);
        return {
          ...(prev ?? {}),
          id: n.id,
          type: "procedural",
          position: prev?.dragging ? prev.position : n.position,
          data: {
            ...n.data,
            connectedPorts: edges.filter((e) => e.target === n.id).map((e) => e.targetHandle || "in"),
            ruleOptions,
            onUpdateData: updateNodeData,
            onDeleteNode: deleteNode,
          },
        };
      });
    },
    [ruleOptions, updateNodeData, deleteNode],
  );

  const [rfNodes, setRfNodes, onRfNodesChange] = useNodesState<Node<FlowNodeData>>(
    toFlowNodes(graph.nodes, graph.edges),
  );
  const [rfEdges, setRfEdges, onRfEdgesChange] = useEdgesState<Edge>(toFlowEdges(graph.edges));

  useEffect(() => {
    setRfNodes((prev) => toFlowNodes(graph.nodes, graph.edges, prev));
  }, [graph.nodes, graph.edges, toFlowNodes, setRfNodes]);

  useEffect(() => {
    setRfEdges(toFlowEdges(graph.edges));
  }, [graph.edges, toFlowEdges, setRfEdges]);

  useEffect(() => {
    if (!focusNodeId) return;
    const target = graphRef.current.nodes.find((n) => n.id === focusNodeId);
    if (!target) {
      onFocusHandled?.();
      return;
    }
    setRfNodes((prev) => prev.map((n) => ({ ...n, selected: n.id === focusNodeId })));
    const timer = window.setTimeout(() => {
      void setCenter(target.position.x + 115, target.position.y + 80, { zoom: 1, duration: 250 });
      onFocusHandled?.();
    }, 30);
    return () => window.clearTimeout(timer);
  }, [focusNodeId, setRfNodes, setCenter, onFocusHandled]);

  const handleNodesChange = useCallback(
    (changes: NodeChange<Node<FlowNodeData>>[]) => {
      onRfNodesChange(changes);
      const needsCommit = changes.some(
        (c) => c.type === "remove" || (c.type === "position" && c.dragging === false),
      );
      if (!needsCommit) return;
      const curr = graphRef.current;
      const removedIds = new Set(changes.filter((c) => c.type === "remove").map((c) => c.id));
      const posUpdates = new Map(
        changes
          .filter((c): c is Extract<NodeChange<Node<FlowNodeData>>, { type: "position" }> => c.type === "position" && Boolean(c.position))
          .map((c) => [c.id, c.position!]),
      );
      const nextNodes = curr.nodes
        .filter((n) => !removedIds.has(n.id))
        .map((n) => (posUpdates.has(n.id) ? { ...n, position: posUpdates.get(n.id)! } : n));
      const nextEdges = curr.edges.filter((e) => !removedIds.has(e.source) && !removedIds.has(e.target));
      onGraphChange({ nodes: nextNodes, edges: nextEdges });
    },
    [onRfNodesChange, onGraphChange],
  );

  const handleEdgesChange = useCallback(
    (changes: EdgeChange<Edge>[]) => {
      onRfEdgesChange(changes);
      const removedIds = new Set(changes.filter((c) => c.type === "remove").map((c) => c.id));
      if (removedIds.size === 0) return;
      const curr = graphRef.current;
      onGraphChange({ ...curr, edges: curr.edges.filter((e) => !removedIds.has(e.id)) });
    },
    [onRfEdgesChange, onGraphChange],
  );

  const handleConnect = useCallback(
    (connection: Connection) => {
      if (!connection.source || !connection.target) return;
      const curr = graphRef.current;
      if (wouldCreateProceduralCycle(curr.edges, connection.source, connection.target)) {
        setStatusMessage("Rejected connection: cycles are not allowed in MaterialX procedural graphs.");
        return;
      }
      const srcNode = curr.nodes.find((n) => n.id === connection.source);
      const dstNode = curr.nodes.find((n) => n.id === connection.target);
      if (!srcNode || !dstNode) return;
      const srcSpec = PROCEDURAL_NODE_SPECS[srcNode.data.kind as Exclude<ProceduralNodeKind, "triplanar">];
      const dstSpec = PROCEDURAL_NODE_SPECS[dstNode.data.kind as Exclude<ProceduralNodeKind, "triplanar">];
      const targetHandle = connection.targetHandle || "in";
      const dstPort = dstSpec?.inputs.find((p) => p.name === targetHandle);
      if (!isSocketCompatible(srcSpec?.outType, dstPort?.type)) {
        setStatusMessage(
          `Incompatible sockets: ${srcSpec?.outType ?? "unknown"} cannot connect to ${dstPort?.type ?? "unknown"}.`,
        );
        return;
      }
      setStatusMessage("");
      const filtered = curr.edges.filter(
        (e) => !(e.target === connection.target && (e.targetHandle || "in") === targetHandle),
      );
      const newEdge: ProceduralGraphEdge = {
        id: `pe-${crypto.randomUUID().slice(0, 8)}`,
        source: connection.source,
        sourceHandle: connection.sourceHandle || "out",
        target: connection.target,
        targetHandle,
      };
      onGraphChange({ ...curr, edges: [...filtered, newEdge] });
    },
    [onGraphChange],
  );

  function addNode(kind: Exclude<ProceduralNodeKind, "triplanar">, position?: { x: number; y: number }) {
    const curr = graphRef.current;
    const spec = PROCEDURAL_NODE_SPECS[kind];
    const defaults: Record<string, unknown> = {};
    for (const p of spec.inputs) {
      if (p.defaultValue !== undefined) defaults[p.name] = p.defaultValue;
    }
    if (kind === "colorramp") {
      defaults.interpolation = "linear";
      defaults.stops = [
        { pos: 0, color: [0, 0, 0] },
        { pos: 1, color: [1, 1, 1] },
      ];
    }
    const count = curr.nodes.length;
    const pos = position ?? {
      x: 60 + (count % 4) * 210,
      y: 40 + Math.floor(count / 4) * 180,
    };
    const data: ProceduralGraphNodeData =
      kind === "output"
        ? {
            kind,
            materialXNodeId: activeMaterialXNodeId,
            ruleId: activeRuleId || ruleOptions.find((r) => r.materialXNodeId === activeMaterialXNodeId)?.ruleId,
            parameter: "base_color",
            mappingType: "replace",
            strength: 1,
            enabled: true,
          }
        : { kind, params: defaults };
    const newNode: ProceduralGraphNode = {
      id: `pn-${crypto.randomUUID().slice(0, 8)}`,
      type: "procedural",
      position: pos,
      data,
    };
    onGraphChange({ ...curr, nodes: [...curr.nodes, newNode] });
    setStatusMessage("");
  }

  function handlePaletteDragStart(e: DragEvent<HTMLButtonElement>, kind: Exclude<ProceduralNodeKind, "triplanar">) {
    e.dataTransfer.setData("application/orchflow-proc-kind", kind);
    e.dataTransfer.effectAllowed = "copy";
  }

  function handleCanvasDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    const kind = e.dataTransfer.getData("application/orchflow-proc-kind") as Exclude<ProceduralNodeKind, "triplanar">;
    if (!kind || !PROCEDURAL_NODE_SPECS[kind]) return;
    const position = screenToFlowPosition({ x: e.clientX, y: e.clientY });
    addNode(kind, position);
  }

  return (
    <div className="proc-editor-shell">
      <div className="proc-palette">
        {PALETTE_GROUPS.map((grp) => (
          <div key={grp.category} className="proc-palette-group">
            <span className="proc-palette-title">{grp.category}</span>
            <div className="proc-palette-buttons">
              {grp.kinds.map((k) => (
                <button
                  key={k}
                  type="button"
                  draggable
                  onDragStart={(e) => handlePaletteDragStart(e, k)}
                  onClick={() => addNode(k)}
                >
                  + {PROCEDURAL_NODE_SPECS[k].label}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
      {statusMessage && <div className="proc-status-warn">{statusMessage}</div>}
      <div
        className="proc-canvas-wrap"
        onDragOver={(e) => {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }}
        onDrop={handleCanvasDrop}
      >
        <ReactFlow
          nodes={rfNodes}
          edges={rfEdges}
          nodeTypes={NODE_TYPES}
          onNodesChange={handleNodesChange}
          onEdgesChange={handleEdgesChange}
          onConnect={handleConnect}
          onNodeDoubleClick={(_evt, rfNode) => {
            if (rfNode.data?.kind !== "output" || !onInspectOutput) return;
            const procNode = graphRef.current.nodes.find((n) => n.id === rfNode.id);
            if (procNode) onInspectOutput(procNode);
          }}
          fitView={!focusNodeId}
          deleteKeyCode={["Delete", "Backspace"]}
        >
          <Background color="#294448" gap={16} />
          <Controls />
        </ReactFlow>
      </div>
    </div>
  );
}

export function ProceduralEditor(props: {
  project: StoredProject;
  activeMaterialXNodeId: string;
  activeRuleId?: string;
  graph: ProceduralGraph;
  onGraphChange: (next: ProceduralGraph) => void;
  focusNodeId?: string | null;
  onFocusHandled?: () => void;
  onInspectOutput?: (node: ProceduralGraphNode) => void;
}) {
  return (
    <ReactFlowProvider>
      <ProceduralEditorInner {...props} />
    </ReactFlowProvider>
  );
}
