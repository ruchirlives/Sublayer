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
                <th style={{ ...thStyle, width: 28 }}></th>
              </tr>
            </thead>
            <tbody>
              {prototypes.length === 0 ? (
                <tr>
                  <td
                    colSpan={3}
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
            Max slope (°)
            <NumberInput
              value={s.maxSlopeDeg ?? 90}
              min={0}
              max={180}
              step={5}
              onChange={(e) => {
                const deg = Number(e.target.value);
                if (Number.isFinite(deg))
                  patchScatter({ maxSlopeDeg: Math.max(0, Math.min(180, deg)) });
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
        </>
      ) : (
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
      )}
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
      <label className="checkbox-row">
        <input
          type="checkbox"
          checked={s.randomYaw}
          onChange={(e) => patchScatter({ randomYaw: e.target.checked })}
        />
        Random yaw rotation
      </label>
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
