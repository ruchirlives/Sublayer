import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { InspectorHint } from "./InspectorHint";
import { NumberInput } from "./NumberInput";
import {
  defaultScatter,
  graphPreview,
  type ScatterPrototypeRule,
  type ScatterSettings,
  type StoredProject,
} from "./fileRegistry";

type Prim = {
  path: string;
  name: string;
  type: string;
  instance: boolean;
  instanceProxy: boolean;
  hasChildren: boolean;
};

type Children = {
  children: Prim[];
  nextOffset: number | null;
};

const thStyle: React.CSSProperties = {
  textAlign: "left",
  padding: "5px 6px",
  borderBottom: "1px solid #2d3c44",
  fontSize: "10px",
  textTransform: "uppercase",
  color: "#98acb6",
  letterSpacing: "0.04em",
};

const tdStyle: React.CSSProperties = {
  padding: "4px 6px",
  borderBottom: "1px solid #1d2b32",
  verticalAlign: "middle",
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  background: "#0b1418",
  color: "#dce6eb",
  border: "1px solid #2b3d46",
  borderRadius: "3px",
  padding: "3px 6px",
  fontSize: "11px",
  fontFamily: "monospace",
};

function rgbToHex(rgb: [number, number, number]): string {
  return (
    "#" +
    rgb
      .map((c) =>
        Math.max(0, Math.min(255, Math.round(c * 255)))
          .toString(16)
          .padStart(2, "0"),
      )
      .join("")
  );
}

function hexToRgb(hex: string): [number, number, number] {
  const clean = hex.replace(/^#/, "");
  if (clean.length !== 6) return [1, 1, 1];
  const r = parseInt(clean.slice(0, 2), 16);
  const g = parseInt(clean.slice(2, 4), 16);
  const b = parseInt(clean.slice(4, 6), 16);
  if (!Number.isFinite(r) || !Number.isFinite(g) || !Number.isFinite(b)) return [1, 1, 1];
  return [r / 255, g / 255, b / 255];
}

export function ScatterInspector({
  project,
  nodeId,
  value,
  onChange,
}: {
  project: StoredProject;
  nodeId: string;
  value?: ScatterSettings | null;
  onChange: (next: ScatterSettings) => void;
}) {
  const s = value || defaultScatter;
  const prototypes = s.prototypes || [];
  const hasInputs = project.edges.some((e) => e.target === nodeId);

  const areaOffset: [number, number, number] = s.areaOffset ?? [0, 0, 0];
  const scaleUniform = s.scaleUniform ?? true;
  const scaleMinVec: [number, number, number] = s.scaleMinVec ?? [s.scaleMin, s.scaleMin, s.scaleMin];
  const scaleMaxVec: [number, number, number] = s.scaleMaxVec ?? [s.scaleMax, s.scaleMax, s.scaleMax];
  const rotationOffset: [number, number, number] = s.rotationOffset ?? [0, 0, 0];
  const rotationMin: [number, number, number] =
    s.rotationMin ?? [0, 0, 0];
  const rotationMax: [number, number, number] =
    s.rotationMax ?? (s.randomYaw ? [0, 360, 0] : [0, 0, 0]);
  const minSlopeDeg = s.minSlopeDeg ?? 0;
  const maxSlopeDeg = s.maxSlopeDeg ?? 90;
  const alignStrength = s.alignStrength ?? 1;
  const surfaceOffset = s.surfaceOffset ?? 0;
  const gridJitter = s.gridJitter ?? 0;
  const innerRadiusRatio = s.innerRadiusRatio ?? 0;
  const circleAlign = s.circleAlign ?? "none";
  const circleEvenSpacing = Boolean(s.circleEvenSpacing);
  const colorVariationEnabled = Boolean(s.colorVariationEnabled);
  const colorVariationMode = s.colorVariationMode ?? "hsv";
  const colorTintBase: [number, number, number] = s.colorTintBase ?? [1, 1, 1];
  const colorHueJitter = s.colorHueJitter ?? 15;
  const colorSatJitter = s.colorSatJitter ?? 0.15;
  const colorValJitter = s.colorValJitter ?? 0.15;
  const colorRampA: [number, number, number] = s.colorRampA ?? [0.85, 0.95, 0.75];
  const colorRampB: [number, number, number] = s.colorRampB ?? [0.95, 0.75, 0.55];
  const colorApplyToMaterials = s.colorApplyToMaterials ?? true;

  const [pickerOpen, setPickerOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [rows, setRows] = useState<Record<string, Children>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set(["/", "/Scene"]));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const titleId = useId();

  function patchScatter(patch: Partial<ScatterSettings>) {
    onChange({ ...s, ...patch });
  }

  function addRow(primPath = "") {
    const next: ScatterPrototypeRule = {
      id: crypto.randomUUID(),
      enabled: true,
      primPath,
      weight: 1,
    };
    patchScatter({ prototypes: [...prototypes, next] });
  }

  function updateRow(id: string, patch: Partial<ScatterPrototypeRule>) {
    patchScatter({
      prototypes: prototypes.map((r) => (r.id === id ? { ...r, ...patch } : r)),
    });
  }

  function removeRow(id: string) {
    patchScatter({ prototypes: prototypes.filter((r) => r.id !== id) });
  }

  function togglePrimFromPicker(path: string) {
    const existing = prototypes.some((r) => r.primPath.trim() === path);
    if (existing) {
      patchScatter({
        prototypes: prototypes.filter((r) => r.primPath.trim() !== path),
      });
    } else {
      addRow(path);
    }
  }

  async function load(parent: string, offset = 0, token = generation.current) {
    if (!hasInputs) return;
    setBusy(true);
    try {
      const graph = graphPreview(project, nodeId, true);
      const result = await invoke<Children>("usd_request", {
        request: { operation: "scene_children", graph, parent, offset, search },
      });
      if (token !== generation.current) return;
      setRows((old) => ({
        ...old,
        [parent]: {
          ...result,
          children: offset
            ? [...(old[parent]?.children || []), ...result.children]
            : result.children,
        },
      }));
      setError("");
      if (!search && parent === "/" && offset === 0) {
        const sceneChild = result.children.find((c) => c.path === "/Scene" && c.hasChildren);
        if (sceneChild) {
          void load("/Scene", 0, token);
        }
      }
    } catch (e) {
      if (token === generation.current) setError(String(e));
    } finally {
      if (token === generation.current) setBusy(false);
    }
  }

  useEffect(() => {
    generation.current++;
    setRows({});
    setExpanded(new Set(["/", "/Scene"]));
    setError("");
    if (!pickerOpen || !hasInputs) {
      setBusy(false);
      return;
    }
    const token = generation.current;
    const timer = setTimeout(() => void load("/", 0, token), search ? 300 : 0);
    return () => {
      clearTimeout(timer);
      generation.current++;
    };
  }, [pickerOpen, search, nodeId, hasInputs]);

  function renderTree(parent: string, depth: number): ReactNode {
    const list = rows[parent]?.children;
    if (!list) return null;
    return (
      <ul className="handoff-tree-list">
        {list.map((prim) => {
          const isSelected = prototypes.some((r) => r.primPath.trim() === prim.path);
          const isExpanded = expanded.has(prim.path);
          const detail = prim.instanceProxy
            ? "Select its containing instance"
            : prim.instance
              ? "Whole instance"
              : "";
          return (
            <li key={prim.path}>
              <div
                className={"handoff-object-row " + (isSelected ? "included" : "")}
                style={{ paddingLeft: search ? 8 : 8 + depth * 18 }}
              >
                {!search && prim.hasChildren ? (
                  <button
                    className="handoff-expand"
                    type="button"
                    aria-label={`${isExpanded ? "Collapse" : "Expand"} ${prim.name}`}
                    aria-expanded={isExpanded}
                    disabled={busy}
                    onClick={() => {
                      setExpanded((old) => {
                        const next = new Set(old);
                        next.has(prim.path) ? next.delete(prim.path) : next.add(prim.path);
                        return next;
                      });
                      if (!isExpanded && !rows[prim.path]) void load(prim.path);
                    }}
                  >
                    {isExpanded ? "▾" : "▸"}
                  </button>
                ) : (
                  <span className="handoff-expand-space" aria-hidden="true" />
                )}
                <label
                  className="handoff-object-label"
                  title={
                    prim.instanceProxy
                      ? `${prim.path} · Select the containing instance instead.`
                      : prim.path
                  }
                >
                  <input
                    type="checkbox"
                    checked={isSelected}
                    disabled={prim.instanceProxy}
                    onChange={() => togglePrimFromPicker(prim.path)}
                  />
                  <span className="handoff-object-text">
                    <span className="handoff-object-name">{prim.name}</span>
                    {search && <small className="handoff-object-path">{prim.path}</small>}
                  </span>
                  <span className="handoff-object-type">{prim.type}</span>
                </label>
                {detail && <small className="handoff-object-state">{detail}</small>}
              </div>
              {!search && isExpanded && renderTree(prim.path, depth + 1)}
            </li>
          );
        })}
        {rows[parent]?.nextOffset != null && (
          <li>
            <button
              className="handoff-more"
              type="button"
              disabled={busy}
              onClick={() => void load(parent, rows[parent].nextOffset!)}
            >
              Show more prims…
            </button>
          </li>
        )}
      </ul>
    );
  }

  return (
    <div className="scatter-inspector">
      <p className="field-label">
        PointInstancer Scatter
        <InspectorHint text="Generates a USD PointInstancer from connected upstream prototype stages using GPU instancing." />
      </p>

      <div style={{ display: "grid", gap: 6, marginBottom: 4 }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 6,
            flexWrap: "wrap",
          }}
        >
          <span className="field-label" style={{ margin: 0 }}>
            Prototypes
            <InspectorHint text="Select prims from the connected input to scatter. A random prototype from this list is picked for each instance. Leave empty to scatter whole connected inputs." />
          </span>
          <div style={{ display: "flex", gap: 4 }}>
            <button
              type="button"
              disabled={!hasInputs || !isTauri()}
              title={
                hasInputs
                  ? "Browse and select prims from the connected input stage"
                  : "Connect an upstream node to Scatter first"
              }
              onClick={() => setPickerOpen(true)}
            >
              Select from input…
            </button>
            <button type="button" onClick={() => addRow("")}>
              + Add
            </button>
          </div>
        </div>

        <div
          style={{
            border: "1px solid #28363e",
            borderRadius: 4,
            overflowX: "auto",
            background: "#0e181c",
          }}
        >
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
            <thead>
              <tr style={{ background: "#162329" }}>
                <th style={{ ...thStyle, width: 30, textAlign: "center" }}>On</th>
                <th style={thStyle}>Prim Path</th>
                <th style={{ ...thStyle, width: 54 }} title="Relative spawn probability weight">Wt</th>
                <th style={{ ...thStyle, width: 28 }}></th>
              </tr>
            </thead>
            <tbody>
              {prototypes.length === 0 ? (
                <tr>
                  <td
                    colSpan={4}
                    style={{
                      padding: 8,
                      color: "#687b84",
                      textAlign: "center",
                      fontSize: 11,
                    }}
                  >
                    No prims selected — scatters whole connected inputs.
                  </td>
                </tr>
              ) : (
                prototypes.map((row) => (
                  <tr key={row.id} style={{ opacity: row.enabled ? 1 : 0.55 }}>
                    <td style={{ ...tdStyle, textAlign: "center" }}>
                      <input
                        type="checkbox"
                        aria-label={`Enable prototype ${row.primPath || "row"}`}
                        checked={row.enabled}
                        onChange={(e) =>
                          updateRow(row.id, { enabled: e.target.checked })
                        }
                      />
                    </td>
                    <td style={tdStyle}>
                      <input
                        type="text"
                        style={inputStyle}
                        placeholder="/Scene/Prop"
                        value={row.primPath}
                        onChange={(e) =>
                          updateRow(row.id, { primPath: e.target.value })
                        }
                      />
                    </td>
                    <td style={tdStyle}>
                      <NumberInput
                        style={{ ...inputStyle, textAlign: "right", padding: "3px 4px" }}
                        aria-label={`Weight for ${row.primPath || "prototype"}`}
                        value={row.weight ?? 1}
                        min={0}
                        step={0.1}
                        onChange={(e) => {
                          const w = Number(e.target.value);
                          if (Number.isFinite(w))
                            updateRow(row.id, { weight: Math.max(0, w) });
                        }}
                      />
                    </td>
                    <td style={{ ...tdStyle, textAlign: "center" }}>
                      <button
                        type="button"
                        className="danger"
                        style={{ padding: "2px 6px", fontSize: 10 }}
                        title="Remove prototype"
                        aria-label={`Remove ${row.primPath || "prototype"}`}
                        onClick={() => removeRow(row.id)}
                      >
                        ✕
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      <label>
        Count
        <NumberInput
          value={s.count}
          min={1}
          max={1000000}
          step={10}
          onChange={(e) => {
            const count = Number(e.target.value);
            if (Number.isFinite(count))
              patchScatter({ count: Math.max(1, Math.min(1000000, Math.round(count))) });
          }}
        />
      </label>
      <label>
        Seed
        <NumberInput
          value={s.seed}
          min={0}
          max={999999}
          step={1}
          onChange={(e) => {
            const seed = Number(e.target.value);
            if (Number.isFinite(seed))
              patchScatter({ seed: Math.max(0, Math.round(seed)) });
          }}
        />
      </label>
      <label>
        Distribution
        <select
          value={s.distribution}
          onChange={(e) =>
            patchScatter({
              distribution: e.target.value as "random" | "grid" | "circle" | "surface",
            })
          }
        >
          <option value="random">Random</option>
          <option value="grid">Grid</option>
          <option value="circle">Circle</option>
          <option value="surface">Surface</option>
        </select>
      </label>
      {s.distribution === "surface" ? (
        <>
          <label>
            <span className="field-label">
              Surface prim path
              <InspectorHint text="Optional USD prim path of the target mesh (e.g. /Scene/Terrain). If blank with 2+ inputs, the last connected input is used as the surface." />
            </span>
            <input
              type="text"
              placeholder="/Scene/Terrain (optional)"
              value={s.surfacePrimPath ?? ""}
              onChange={(e) => patchScatter({ surfacePrimPath: e.target.value })}
            />
          </label>
          <label>
            Slope Min / Max (°)
            <div className="vector-inputs">
              <NumberInput
                value={minSlopeDeg}
                min={0}
                max={180}
                step={5}
                onChange={(e) => {
                  const deg = Number(e.target.value);
                  if (Number.isFinite(deg)) {
                    const clamped = Math.max(0, Math.min(180, deg));
                    patchScatter({
                      minSlopeDeg: clamped,
                      maxSlopeDeg: Math.max(clamped, maxSlopeDeg),
                    });
                  }
                }}
              />
              <NumberInput
                value={maxSlopeDeg}
                min={0}
                max={180}
                step={5}
                onChange={(e) => {
                  const deg = Number(e.target.value);
                  if (Number.isFinite(deg)) {
                    const clamped = Math.max(minSlopeDeg, Math.min(180, deg));
                    patchScatter({ maxSlopeDeg: clamped });
                  }
                }}
              />
            </div>
          </label>
          <label>
            <span className="field-label">
              Altitude Min / Max (m)
              <InspectorHint text="Optional world elevation range along the stage up-axis. Leave blank for no limit." />
            </span>
            <div className="vector-inputs">
              <input
                type="number"
                step={0.5}
                placeholder="No min"
                value={s.heightMin ?? ""}
                onChange={(e) => {
                  const raw = e.target.value.trim();
                  if (!raw) {
                    patchScatter({ heightMin: null });
                    return;
                  }
                  const v = Number(raw);
                  if (Number.isFinite(v)) {
                    patchScatter({
                      heightMin: v,
                      heightMax:
                        s.heightMax != null && s.heightMax < v ? v : s.heightMax ?? null,
                    });
                  }
                }}
              />
              <input
                type="number"
                step={0.5}
                placeholder="No max"
                value={s.heightMax ?? ""}
                onChange={(e) => {
                  const raw = e.target.value.trim();
                  if (!raw) {
                    patchScatter({ heightMax: null });
                    return;
                  }
                  const v = Number(raw);
                  if (Number.isFinite(v)) {
                    patchScatter({
                      heightMax: s.heightMin != null && v < s.heightMin ? s.heightMin : v,
                    });
                  }
                }}
              />
            </div>
          </label>
          <label>
            <span className="field-label">
              Surface offset (m)
              <InspectorHint text="Offset instances along the surface normal. Negative values sink instances into the terrain; positive values lift them." />
            </span>
            <NumberInput
              value={surfaceOffset}
              step={0.05}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v)) patchScatter({ surfaceOffset: v });
              }}
            />
          </label>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={Boolean(s.alignToNormal)}
              onChange={(e) => patchScatter({ alignToNormal: e.target.checked })}
            />
            Align to surface normal
          </label>
          {s.alignToNormal && (
            <label>
              Align strength (0–1)
              <NumberInput
                value={alignStrength}
                min={0}
                max={1}
                step={0.05}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (Number.isFinite(v))
                    patchScatter({ alignStrength: Math.max(0, Math.min(1, v)) });
                }}
              />
            </label>
          )}
        </>
      ) : (
        <>
          <label>
            Area X / Y / Z
            <div className="vector-inputs">
              <NumberInput
                value={s.areaSize[0]}
                min={0}
                step={1}
                onChange={(e) => {
                  const x = Number(e.target.value);
                  if (Number.isFinite(x))
                    patchScatter({
                      areaSize: [Math.max(0, x), s.areaSize[1], s.areaSize[2]],
                    });
                }}
              />
              <NumberInput
                value={s.areaSize[1]}
                min={0}
                step={1}
                onChange={(e) => {
                  const y = Number(e.target.value);
                  if (Number.isFinite(y))
                    patchScatter({
                      areaSize: [s.areaSize[0], Math.max(0, y), s.areaSize[2]],
                    });
                }}
              />
              <NumberInput
                value={s.areaSize[2]}
                min={0}
                step={1}
                onChange={(e) => {
                  const z = Number(e.target.value);
                  if (Number.isFinite(z))
                    patchScatter({
                      areaSize: [s.areaSize[0], s.areaSize[1], Math.max(0, z)],
                    });
                }}
              />
            </div>
          </label>
          <label>
            Center offset X / Y / Z
            <div className="vector-inputs">
              {[0, 1, 2].map((axis) => (
                <NumberInput
                  key={axis}
                  value={areaOffset[axis]}
                  step={0.5}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isFinite(v)) {
                      const next: [number, number, number] = [...areaOffset];
                      next[axis] = v;
                      patchScatter({ areaOffset: next });
                    }
                  }}
                />
              ))}
            </div>
          </label>
          {s.distribution === "grid" && (
            <label>
              <span className="field-label">
                Grid jitter (0–1)
                <InspectorHint text="Adds random horizontal offset within each grid cell for more organic placement." />
              </span>
              <NumberInput
                value={gridJitter}
                min={0}
                max={1}
                step={0.05}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (Number.isFinite(v))
                    patchScatter({ gridJitter: Math.max(0, Math.min(1, v)) });
                }}
              />
            </label>
          )}
          {s.distribution === "circle" && (
            <>
              <label>
                <span className="field-label">
                  Inner radius ratio (0–1)
                  <InspectorHint text="Creates a ring/annulus clearing in the center. Set to 1.0 to place instances directly on the circle perimeter." />
                </span>
                <NumberInput
                  value={innerRadiusRatio}
                  min={0}
                  max={1}
                  step={0.05}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isFinite(v))
                      patchScatter({ innerRadiusRatio: Math.max(0, Math.min(1, v)) });
                  }}
                />
              </label>
              <label>
                <span className="field-label">
                  Arc alignment
                  <InspectorHint text="Orients each instance relative to the circle or ellipse arc. Combines with Base rotation and Random min/max below." />
                </span>
                <select
                  value={circleAlign}
                  onChange={(e) => {
                    const next = e.target.value as "none" | "outward" | "inward" | "tangent";
                    const isDefaultFullYaw =
                      circleAlign === "none" &&
                      next !== "none" &&
                      rotationMin[0] === 0 &&
                      rotationMin[1] === 0 &&
                      rotationMin[2] === 0 &&
                      rotationMax[0] === 0 &&
                      rotationMax[1] === 360 &&
                      rotationMax[2] === 0;
                    patchScatter({
                      circleAlign: next,
                      ...(isDefaultFullYaw
                        ? {
                            randomYaw: false,
                            rotationMin: [0, 0, 0],
                            rotationMax: [0, 0, 0],
                          }
                        : {}),
                    });
                  }}
                >
                  <option value="none">None</option>
                  <option value="outward">Perpendicular (Outward)</option>
                  <option value="inward">Perpendicular (Inward)</option>
                  <option value="tangent">Tangent (Along arc)</option>
                </select>
              </label>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={circleEvenSpacing}
                  onChange={(e) => patchScatter({ circleEvenSpacing: e.target.checked })}
                />
                Evenly spaced around circle
              </label>
            </>
          )}
        </>
      )}
      {s.distribution !== "grid" && (
        <label>
          Min spacing
          <NumberInput
            value={s.minSpacing ?? 0}
            min={0}
            step={0.5}
            onChange={(e) => {
              const sp = Number(e.target.value);
              if (Number.isFinite(sp)) patchScatter({ minSpacing: Math.max(0, sp) });
            }}
          />
        </label>
      )}

      <label className="checkbox-row">
        <input
          type="checkbox"
          checked={scaleUniform}
          onChange={(e) => patchScatter({ scaleUniform: e.target.checked })}
        />
        Uniform scale
      </label>
      {scaleUniform ? (
        <label>
          Scale Min / Max
          <div className="vector-inputs">
            <NumberInput
              value={s.scaleMin}
              min={0.01}
              step={0.05}
              onChange={(e) => {
                const scaleMin = Number(e.target.value);
                if (Number.isFinite(scaleMin)) {
                  const clamped = Math.max(0.01, scaleMin);
                  patchScatter({
                    scaleMin: clamped,
                    scaleMax: Math.max(clamped, s.scaleMax),
                  });
                }
              }}
            />
            <NumberInput
              value={s.scaleMax}
              min={0.01}
              step={0.05}
              onChange={(e) => {
                const scaleMax = Number(e.target.value);
                if (Number.isFinite(scaleMax))
                  patchScatter({ scaleMax: Math.max(s.scaleMin, scaleMax) });
              }}
            />
          </div>
        </label>
      ) : (
        <>
          <label>
            Scale Min X / Y / Z
            <div className="vector-inputs">
              {[0, 1, 2].map((axis) => (
                <NumberInput
                  key={axis}
                  value={scaleMinVec[axis]}
                  min={0.01}
                  step={0.05}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isFinite(v)) {
                      const clamped = Math.max(0.01, v);
                      const nextMin: [number, number, number] = [...scaleMinVec];
                      const nextMax: [number, number, number] = [...scaleMaxVec];
                      nextMin[axis] = clamped;
                      nextMax[axis] = Math.max(clamped, nextMax[axis]);
                      patchScatter({ scaleMinVec: nextMin, scaleMaxVec: nextMax });
                    }
                  }}
                />
              ))}
            </div>
          </label>
          <label>
            Scale Max X / Y / Z
            <div className="vector-inputs">
              {[0, 1, 2].map((axis) => (
                <NumberInput
                  key={axis}
                  value={scaleMaxVec[axis]}
                  min={0.01}
                  step={0.05}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isFinite(v)) {
                      const nextMax: [number, number, number] = [...scaleMaxVec];
                      nextMax[axis] = Math.max(scaleMinVec[axis], v);
                      patchScatter({ scaleMaxVec: nextMax });
                    }
                  }}
                />
              ))}
            </div>
          </label>
        </>
      )}

      <details open style={{ marginTop: 4 }}>
        <summary className="field-label" style={{ cursor: "pointer", userSelect: "none" }}>
          Rotation (°)
        </summary>
        <div style={{ display: "grid", gap: 6, marginTop: 6 }}>
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
            <button
              type="button"
              style={{ fontSize: 10, padding: "2px 6px" }}
              onClick={() =>
                patchScatter({
                  randomYaw: true,
                  rotationMin: [0, 0, 0],
                  rotationMax: [0, 360, 0],
                })
              }
            >
              Yaw 360°
            </button>
            <button
              type="button"
              style={{ fontSize: 10, padding: "2px 6px" }}
              onClick={() =>
                patchScatter({
                  randomYaw: true,
                  rotationMin: [-15, 0, -15],
                  rotationMax: [15, 360, 15],
                })
              }
            >
              Tilt ±15°
            </button>
            <button
              type="button"
              style={{ fontSize: 10, padding: "2px 6px" }}
              onClick={() =>
                patchScatter({
                  randomYaw: true,
                  rotationMin: [0, 0, 0],
                  rotationMax: [360, 360, 360],
                })
              }
            >
              Full 3D
            </button>
            <button
              type="button"
              style={{ fontSize: 10, padding: "2px 6px" }}
              onClick={() =>
                patchScatter({
                  randomYaw: false,
                  rotationOffset: [0, 0, 0],
                  rotationMin: [0, 0, 0],
                  rotationMax: [0, 0, 0],
                })
              }
            >
              Reset
            </button>
          </div>
          <label>
            Base rotation X / Y / Z
            <div className="vector-inputs">
              {[0, 1, 2].map((axis) => (
                <NumberInput
                  key={axis}
                  value={rotationOffset[axis]}
                  step={5}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isFinite(v)) {
                      const next: [number, number, number] = [...rotationOffset];
                      next[axis] = v;
                      patchScatter({ rotationOffset: next });
                    }
                  }}
                />
              ))}
            </div>
          </label>
          <label>
            Random min X / Y / Z
            <div className="vector-inputs">
              {[0, 1, 2].map((axis) => (
                <NumberInput
                  key={axis}
                  value={rotationMin[axis]}
                  step={5}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isFinite(v)) {
                      const nextMin: [number, number, number] = [...rotationMin];
                      const nextMax: [number, number, number] = [...rotationMax];
                      nextMin[axis] = v;
                      nextMax[axis] = Math.max(v, nextMax[axis]);
                      patchScatter({
                        rotationMin: nextMin,
                        rotationMax: nextMax,
                        randomYaw: nextMax[1] > nextMin[1],
                      });
                    }
                  }}
                />
              ))}
            </div>
          </label>
          <label>
            Random max X / Y / Z
            <div className="vector-inputs">
              {[0, 1, 2].map((axis) => (
                <NumberInput
                  key={axis}
                  value={rotationMax[axis]}
                  step={5}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isFinite(v)) {
                      const nextMin: [number, number, number] = [...rotationMin];
                      const nextMax: [number, number, number] = [...rotationMax];
                      nextMax[axis] = v;
                      nextMin[axis] = Math.min(nextMin[axis], v);
                      patchScatter({
                        rotationMin: nextMin,
                        rotationMax: nextMax,
                        randomYaw: nextMax[1] > nextMin[1],
                      });
                    }
                  }}
                />
              ))}
            </div>
          </label>
        </div>
      </details>

      <details style={{ marginTop: 4 }} open={colorVariationEnabled}>
        <summary className="field-label" style={{ cursor: "pointer", userSelect: "none" }}>
          Color variation (Primvar)
          <InspectorHint text="Authors per-instance primvars:displayColor and primvars:scatterRandom on the PointInstancer. Supported in Hydra Storm and Omniverse RTX." />
        </summary>
        <div style={{ display: "grid", gap: 6, marginTop: 6 }}>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={colorVariationEnabled}
              onChange={(e) =>
                patchScatter({ colorVariationEnabled: e.target.checked })
              }
            />
            Enable per-instance color variation
          </label>
          {colorVariationEnabled && (
            <>
              <label>
                Mode
                <select
                  value={colorVariationMode}
                  onChange={(e) =>
                    patchScatter({
                      colorVariationMode: e.target.value as "hsv" | "ramp",
                    })
                  }
                >
                  <option value="hsv">HSV tint jitter</option>
                  <option value="ramp">Color A → B ramp</option>
                </select>
              </label>
              {colorVariationMode === "hsv" ? (
                <>
                  <label>
                    Base tint
                    <input
                      type="color"
                      value={rgbToHex(colorTintBase)}
                      onChange={(e) =>
                        patchScatter({ colorTintBase: hexToRgb(e.target.value) })
                      }
                    />
                  </label>
                  <label>
                    Hue ± (°) / Sat ± / Val ±
                    <div className="vector-inputs">
                      <NumberInput
                        value={colorHueJitter}
                        min={0}
                        max={180}
                        step={5}
                        onChange={(e) => {
                          const v = Number(e.target.value);
                          if (Number.isFinite(v))
                            patchScatter({
                              colorHueJitter: Math.max(0, Math.min(180, v)),
                            });
                        }}
                      />
                      <NumberInput
                        value={colorSatJitter}
                        min={0}
                        max={1}
                        step={0.05}
                        onChange={(e) => {
                          const v = Number(e.target.value);
                          if (Number.isFinite(v))
                            patchScatter({
                              colorSatJitter: Math.max(0, Math.min(1, v)),
                            });
                        }}
                      />
                      <NumberInput
                        value={colorValJitter}
                        min={0}
                        max={1}
                        step={0.05}
                        onChange={(e) => {
                          const v = Number(e.target.value);
                          if (Number.isFinite(v))
                            patchScatter({
                              colorValJitter: Math.max(0, Math.min(1, v)),
                            });
                        }}
                      />
                    </div>
                  </label>
                </>
              ) : (
                <label>
                  Color A / Color B
                  <div className="vector-inputs">
                    <input
                      type="color"
                      aria-label="Color ramp start"
                      value={rgbToHex(colorRampA)}
                      onChange={(e) =>
                        patchScatter({ colorRampA: hexToRgb(e.target.value) })
                      }
                    />
                    <input
                      type="color"
                      aria-label="Color ramp end"
                      value={rgbToHex(colorRampB)}
                      onChange={(e) =>
                        patchScatter({ colorRampB: hexToRgb(e.target.value) })
                      }
                    />
                  </div>
                </label>
              )}
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={colorApplyToMaterials}
                  onChange={(e) =>
                    patchScatter({ colorApplyToMaterials: e.target.checked })
                  }
                />
                <span>
                  Tint prototype materials
                  <InspectorHint text="Automatically multiplies bound MaterialX base_color (and connects untextured UsdPreviewSurface diffuseColor) with primvars:displayColor inside the scatter prototypes." />
                </span>
              </label>
            </>
          )}
        </div>
      </details>

      <label className="checkbox-row">
        <input
          type="checkbox"
          checked={Boolean(s.passThrough)}
          onChange={(e) => patchScatter({ passThrough: e.target.checked })}
        />
        Pass through input USD
      </label>

      {pickerOpen && (
        <div
          className="override-backdrop"
          onKeyDown={(event) => {
            if (event.key === "Escape") setPickerOpen(false);
          }}
        >
          <section
            className="override-modal handoff-picker"
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
          >
            <div className="handoff-picker-heading">
              <h2 id={titleId}>Select scatter prototype prims</h2>
              <button
                type="button"
                onClick={() => setPickerOpen(false)}
                aria-label="Close prim picker"
              >
                ×
              </button>
            </div>
            <p className="hint">
              Tick prims from the connected input stage to add them to the prototype
              table. Each scatter instance picks one at random.
            </p>
            <div className="handoff-picker-body">
              <section className="handoff-object-browser" aria-label="Input prims">
                <div className="handoff-search">
                  <input
                    autoFocus
                    aria-label="Search input prims"
                    placeholder="Search prims by name or path…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                  {search && (
                    <button type="button" onClick={() => setSearch("")}>
                      Clear search
                    </button>
                  )}
                </div>
                <div className="handoff-tree-heading">
                  <strong>{search ? "Search results" : "Input hierarchy"}</strong>
                  {!search && (
                    <button
                      type="button"
                      onClick={() => setExpanded(new Set(["/"]))}
                    >
                      Collapse all
                    </button>
                  )}
                </div>
                <div className="handoff-object-tree" aria-busy={busy}>
                  {renderTree("/", 0)}
                  {busy && (
                    <p className="hint" role="status">
                      Loading input prims…
                    </p>
                  )}
                  {!busy && rows["/"] && !rows["/"].children.length && (
                    <p className="hint">
                      {search
                        ? "No matching prims. Try another name."
                        : "No prims available in input."}
                    </p>
                  )}
                </div>
              </section>
              <section className="handoff-selected" aria-label="Selected prototypes">
                <div className="handoff-selected-heading">
                  <strong>Selected ({prototypes.length})</strong>
                  <button
                    type="button"
                    disabled={!prototypes.length}
                    onClick={() => patchScatter({ prototypes: [] })}
                  >
                    Clear all
                  </button>
                </div>
                <p className="hint">Prims listed here will be scattered randomly.</p>
                {!prototypes.length ? (
                  <p className="handoff-selection-empty">
                    No prims selected yet.
                    <br />
                    Tick prims in the input list.
                  </p>
                ) : (
                  <ul className="handoff-selected-list">
                    {prototypes.map((row) => (
                      <li key={row.id}>
                        <div>
                          <strong>
                            {row.primPath.split("/").filter(Boolean).pop() || "(empty)"}
                          </strong>
                          <small>{row.primPath || "(no path)"}</small>
                        </div>
                        <button
                          type="button"
                          aria-label={`Remove ${row.primPath}`}
                          onClick={() => removeRow(row.id)}
                        >
                          ×
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>
            {error && <p role="alert">{error}</p>}
            <div className="handoff-picker-footer">
              <span>
                {prototypes.length
                  ? `${prototypes.length} prototype${prototypes.length === 1 ? "" : "s"} selected`
                  : "No prims selected (scatters whole inputs)"}
              </span>
              <button
                className="primary"
                type="button"
                onClick={() => setPickerOpen(false)}
              >
                Done
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
