import { useEffect, useRef, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { defaultHairSettings, type HairSettings } from "./fileRegistry";
import { NumberInput } from "./NumberInput";

type CurvePrimInfo = {
  path: string;
  name: string;
  curveCount: number;
  instanceProxy: boolean;
};

type MeshPrimInfo = {
  path: string;
  name: string;
  vertexCount: number;
  instanceProxy: boolean;
};

type PrimTableRow = {
  path: string;
  name: string;
  countLabel: string;
  instanceProxy: boolean;
};

function PrimTableList({
  rows,
  query,
  emptyText,
  countHeader,
  filterPlaceholder,
  onToggle,
  onSelectAll,
  onSelectNone,
}: {
  rows: PrimTableRow[];
  query: string;
  emptyText: string;
  countHeader: string;
  filterPlaceholder: string;
  onToggle: (path: string) => void;
  onSelectAll: () => void;
  onSelectNone: () => void;
}) {
  const [filter, setFilter] = useState("");
  const q = filter.trim().toLowerCase();
  const visible = q
    ? rows.filter((r) => r.name.toLowerCase().includes(q) || r.path.toLowerCase().includes(q))
    : rows;
  const editableCount = rows.filter((r) => !r.instanceProxy).length;
  const selectedCount = rows.filter((r) => !r.instanceProxy && hairQueryMatches(query, r.path)).length;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
      {rows.length > 0 && (
        <div style={{ display: "flex", gap: "4px", alignItems: "center" }}>
          <input
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={filterPlaceholder}
            style={{ flex: 1, fontSize: "11px", padding: "3px 6px" }}
          />
          <button type="button" onClick={onSelectAll} style={{ padding: "2px 6px", fontSize: "11px" }}>
            All
          </button>
          <button type="button" onClick={onSelectNone} style={{ padding: "2px 6px", fontSize: "11px" }}>
            None
          </button>
          <small style={{ opacity: 0.7, fontSize: "11px", whiteSpace: "nowrap" }}>
            {selectedCount}/{editableCount}
          </small>
        </div>
      )}
      <div
        style={{
          maxHeight: "155px",
          overflowY: "auto",
          border: "1px solid rgba(255,255,255,0.12)",
          borderRadius: "4px",
          background: "rgba(0,0,0,0.16)",
        }}
      >
        {rows.length === 0 ? (
          <p className="hint" style={{ margin: "6px 8px" }}>{emptyText}</p>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "11px", tableLayout: "fixed" }}>
            <thead>
              <tr
                style={{
                  position: "sticky",
                  top: 0,
                  background: "#132024",
                  borderBottom: "1px solid rgba(255,255,255,0.12)",
                  textAlign: "left",
                }}
              >
                <th style={{ width: "24px", padding: "4px 6px" }} />
                <th style={{ width: "36%", padding: "4px 4px", fontWeight: 600 }}>Prim</th>
                <th style={{ padding: "4px 4px", fontWeight: 600 }}>Path</th>
                <th style={{ width: "68px", padding: "4px 6px", textAlign: "right", fontWeight: 600 }}>
                  {countHeader}
                </th>
              </tr>
            </thead>
            <tbody>
              {visible.length === 0 ? (
                <tr>
                  <td colSpan={4} style={{ padding: "6px 8px", opacity: 0.65 }}>
                    No matches for "{filter}"
                  </td>
                </tr>
              ) : (
                visible.map((r) => {
                  const matched = !r.instanceProxy && hairQueryMatches(query, r.path);
                  const parentPath = r.path.includes("/") ? r.path.slice(0, r.path.lastIndexOf("/")) || "/" : "/";
                  return (
                    <tr
                      key={r.path}
                      title={r.path}
                      onClick={() => {
                        if (!r.instanceProxy) onToggle(r.path);
                      }}
                      style={{
                        borderBottom: "1px solid rgba(255,255,255,0.06)",
                        background: matched ? "rgba(159, 226, 182, 0.08)" : "transparent",
                        opacity: r.instanceProxy ? 0.5 : 1,
                        cursor: r.instanceProxy ? "not-allowed" : "pointer",
                      }}
                    >
                      <td style={{ padding: "3px 6px", textAlign: "center" }}>
                        <input
                          type="checkbox"
                          checked={matched}
                          disabled={r.instanceProxy}
                          onChange={() => onToggle(r.path)}
                          onClick={(e) => e.stopPropagation()}
                        />
                      </td>
                      <td
                        style={{
                          padding: "3px 4px",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {r.name}
                      </td>
                      <td
                        style={{
                          padding: "3px 4px",
                          opacity: 0.65,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {parentPath}
                      </td>
                      <td style={{ padding: "3px 6px", textAlign: "right", opacity: 0.75, whiteSpace: "nowrap" }}>
                        {r.instanceProxy ? "proxy" : r.countLabel}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

type Props = {
  value?: HairSettings;
  graph?: string;
  onChange: (next: HairSettings) => void;
};

function globToRegex(pattern: string): RegExp {
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".");
  return new RegExp(`^${escaped}$`, "i");
}

function hairQueryMatches(rawQuery: string, primPath: string): boolean {
  const terms = rawQuery
    .split(",")
    .map((t) => t.trim().toLowerCase())
    .filter(Boolean);
  const includes = terms.filter((t) => !t.startsWith("!"));
  const excludes = terms
    .filter((t) => t.startsWith("!"))
    .map((t) => t.slice(1).trim())
    .filter(Boolean);

  const low = primPath.toLowerCase();
  const nameLow = primPath.split("/").pop()?.toLowerCase() ?? low;

  const hit = (term: string) => {
    if (term === "*") return true;
    if (/[*?[]/.test(term)) {
      const rx = globToRegex(term);
      return rx.test(low) || rx.test(nameLow);
    }
    if (term.startsWith("/")) {
      const prefix = term.replace(/\/+$/, "");
      return low === prefix || low.startsWith(`${prefix}/`);
    }
    return low.includes(term);
  };

  if (excludes.some(hit)) return false;
  if (includes.length === 0 || includes.includes("*")) return true;
  return includes.some(hit);
}

export function HairInspector({ value, graph, onChange }: Props) {
  const committed: HairSettings = { ...defaultHairSettings, ...(value || {}) };
  const [draft, setDraft] = useState<HairSettings>(committed);
  const debounceRef = useRef<number | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    setDraft({ ...defaultHairSettings, ...(value || {}) });
  }, [value]);

  useEffect(() => {
    return () => {
      if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    };
  }, []);

  const settings = draft;
  const updateImmediate = (patch: Partial<HairSettings>) => {
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
    const next = { ...draft, ...patch };
    setDraft(next);
    onChangeRef.current(next);
  };
  const update = (patch: Partial<HairSettings>) => {
    const next = { ...draft, ...patch };
    setDraft(next);
    if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      debounceRef.current = null;
      onChangeRef.current(next);
    }, 300);
  };
  const [curves, setCurves] = useState<CurvePrimInfo[] | null>(null);
  const [meshes, setMeshes] = useState<MeshPrimInfo[] | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState("");

  async function scanCurves() {
    if (!graph || !isTauri()) return;
    setScanning(true);
    setScanError("");
    try {
      const res = await invoke<{ curves?: CurvePrimInfo[]; meshes?: MeshPrimInfo[] }>("usd_request", {
        request: { operation: "inspect_stage_curves", graph: JSON.parse(graph) },
      });
      setCurves(res?.curves || []);
      setMeshes(res?.meshes || []);
    } catch (e) {
      setScanError(String(e));
    } finally {
      setScanning(false);
    }
  }

  const toggleQueryPrim = (
    list: Array<{ path: string; instanceProxy: boolean }> | null,
    currentQuery: string,
    primPath: string,
    key: "targetQuery" | "colliderQuery"
  ) => {
    if (!list) return;
    const editable = list.filter((c) => !c.instanceProxy);
    const nextSelected = editable
      .filter((c) => {
        const wasSelected = hairQueryMatches(currentQuery, c.path);
        return c.path === primPath ? !wasSelected : wasSelected;
      })
      .map((c) => c.path);
    if (nextSelected.length === editable.length) {
      update({ [key]: key === "colliderQuery" ? "*" : "" } as Partial<HairSettings>);
    } else if (nextSelected.length === 0) {
      update({ [key]: "__none__" } as Partial<HairSettings>);
    } else {
      update({ [key]: nextSelected.join(", ") } as Partial<HairSettings>);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
      <label>
        <span className="field-label">Target curves</span>
        <input
          type="text"
          placeholder="* (all BasisCurves), names, or !exclusions"
          value={settings.targetQuery}
          onChange={(e) => update({ targetQuery: e.target.value })}
          onDragOver={(e) => {
            if (e.dataTransfer.types.includes("application/orchflow-prim-path")) {
              e.preventDefault();
            }
          }}
          onDrop={(e) => {
            const primPath = e.dataTransfer.getData("application/orchflow-prim-path");
            if (!primPath) return;
            e.preventDefault();
            const current = settings.targetQuery.trim();
            update({
              targetQuery:
                current && current !== "*" && current !== "__none__"
                  ? `${current}, ${primPath}`
                  : primPath,
            });
          }}
        />
      </label>

      <div style={{ display: "flex", gap: "6px", alignItems: "center", flexWrap: "wrap" }}>
        <button
          type="button"
          disabled={!graph || !isTauri() || scanning}
          onClick={scanCurves}
        >
          {scanning ? "Scanning..." : curves ? "Rescan stage" : "Scan curves & colliders"}
        </button>
      </div>

      {scanError && <p className="hint" style={{ margin: 0, color: "#f69" }}>{scanError}</p>}

      {curves && (
        <PrimTableList
          rows={curves.map((c) => ({
            path: c.path,
            name: c.name,
            countLabel: `${c.curveCount.toLocaleString()}`,
            instanceProxy: c.instanceProxy,
          }))}
          query={settings.targetQuery}
          emptyText="No BasisCurves prims found on incoming stage."
          countHeader="Curves"
          filterPlaceholder="Filter curves..."
          onToggle={(path) => toggleQueryPrim(curves, settings.targetQuery, path, "targetQuery")}
          onSelectAll={() => update({ targetQuery: "" })}
          onSelectNone={() => update({ targetQuery: "__none__" })}
        />
      )}

      <label>
        <span className="field-label">Width mode</span>
        <select
          value={settings.widthMode}
          onChange={(e) => {
            const mode = e.target.value as "multiply" | "set";
            update({
              widthMode: mode,
              width: mode === "set" && settings.width === 1.0 ? 0.001 : mode === "multiply" && settings.width < 0.01 ? 1.0 : settings.width,
            });
          }}
        >
          <option value="multiply">Scale existing widths</option>
          <option value="set">Set base width</option>
        </select>
      </label>

      <label>
        <span className="field-label">
          {settings.widthMode === "multiply" ? "Width scale" : "Base width (m)"}
        </span>
        <NumberInput
          min={settings.widthMode === "multiply" ? 0.01 : 0.00001}
          step={settings.widthMode === "multiply" ? 0.05 : 0.0002}
          value={settings.width}
          onChange={(e) => {
            const v = Number(e.target.value);
            if (Number.isFinite(v) && v > 0) update({ width: v });
          }}
        />
      </label>

      <label>
        <span className="field-label">Root scale ({settings.rootScale.toFixed(2)}×)</span>
        <NumberInput
          min={0.05}
          max={10}
          step={0.05}
          value={settings.rootScale}
          onChange={(e) => {
            const v = Number(e.target.value);
            if (Number.isFinite(v) && v > 0 && v <= 10) update({ rootScale: v });
          }}
        />
      </label>

      <label>
        <span className="field-label">Tip taper ({Math.round(settings.taper * 100)}%)</span>
        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          <input
            type="range"
            min={0}
            max={1}
            step={0.02}
            value={settings.taper}
            onChange={(e) => update({ taper: Number(e.target.value) })}
            style={{ flex: 1 }}
          />
          <NumberInput
            min={0}
            max={1}
            step={0.05}
            value={settings.taper}
            onChange={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v)) update({ taper: Math.max(0, Math.min(1, v)) });
            }}
            style={{ width: "64px" }}
          />
        </div>
      </label>

      <p className="hint" style={{ margin: 0 }}>
        Comma-separated names, paths, wildcards (*), or <code>!exclude</code>.
        {settings.taper > 0 || settings.rootScale !== 1
          ? ` Root ${settings.rootScale.toFixed(2)}× → Tip ${(1 - settings.taper).toFixed(2)}×.`
          : ""}
      </p>

      <details open={settings.simEnabled}>
        <summary style={{ cursor: "pointer", fontWeight: 600, fontSize: "12px", padding: "4px 0" }}>
          GPU Physics Simulation (Warp)
        </summary>
        <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginTop: "6px" }}>
          <label style={{ display: "flex", alignItems: "center", gap: "8px", cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={settings.simEnabled}
              onChange={(e) => updateImmediate({ simEnabled: e.target.checked })}
            />
            <span className="field-label" style={{ margin: 0 }}>
              Enable GPU strand simulation
            </span>
          </label>

          {settings.simEnabled && (
            <>
              <label>
                <span className="field-label">Simulation mode</span>
                <select
                  value={settings.simMode}
                  onChange={(e) => updateImmediate({ simMode: e.target.value as "static" | "animated" })}
                >
                  <option value="static">Static style / settle (Time 0)</option>
                  <option value="animated">Animated timeline (start–end)</option>
                </select>
              </label>

              {settings.simMode === "static" ? (
                <label>
                  <span className="field-label">Settle steps</span>
                  <NumberInput
                    min={1}
                    max={500}
                    step={4}
                    value={settings.settleSteps}
                    onChange={(e) => {
                      const v = Math.round(Number(e.target.value));
                      if (Number.isFinite(v) && v >= 1 && v <= 500) update({ settleSteps: v });
                    }}
                  />
                </label>
              ) : (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px" }}>
                  <label>
                    <span className="field-label">Start frame</span>
                    <NumberInput
                      step={1}
                      value={settings.startFrame}
                      onChange={(e) => {
                        const v = Number(e.target.value);
                        if (Number.isFinite(v)) {
                          update({ startFrame: v, endFrame: Math.max(v, settings.endFrame) });
                        }
                      }}
                    />
                  </label>
                  <label>
                    <span className="field-label">End frame</span>
                    <NumberInput
                      step={1}
                      min={settings.startFrame}
                      value={settings.endFrame}
                      onChange={(e) => {
                        const v = Number(e.target.value);
                        if (Number.isFinite(v) && v >= settings.startFrame && v - settings.startFrame <= 10000) {
                          update({ endFrame: v });
                        }
                      }}
                    />
                  </label>
                </div>
              )}

              <label>
                <span className="field-label">Collider meshes</span>
                <input
                  type="text"
                  placeholder="* (all meshes), __none__, or !exclude"
                  value={settings.colliderQuery}
                  onChange={(e) => update({ colliderQuery: e.target.value })}
                  onDragOver={(e) => {
                    if (e.dataTransfer.types.includes("application/orchflow-prim-path")) {
                      e.preventDefault();
                    }
                  }}
                  onDrop={(e) => {
                    const primPath = e.dataTransfer.getData("application/orchflow-prim-path");
                    if (!primPath) return;
                    e.preventDefault();
                    const current = settings.colliderQuery.trim();
                    update({
                      colliderQuery:
                        current && current !== "*" && current !== "__none__"
                          ? `${current}, ${primPath}`
                          : primPath,
                    });
                  }}
                />
              </label>

              {meshes && (
                <PrimTableList
                  rows={meshes.map((m) => ({
                    path: m.path,
                    name: m.name,
                    countLabel: `${m.vertexCount.toLocaleString()}`,
                    instanceProxy: m.instanceProxy,
                  }))}
                  query={settings.colliderQuery}
                  emptyText="No Mesh prims found on incoming stage."
                  countHeader="Verts"
                  filterPlaceholder="Filter colliders..."
                  onToggle={(path) => toggleQueryPrim(meshes, settings.colliderQuery, path, "colliderQuery")}
                  onSelectAll={() => update({ colliderQuery: "*" })}
                  onSelectNone={() => update({ colliderQuery: "__none__" })}
                />
              )}

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px" }}>
                <label>
                  <span className="field-label">Stretch stiffness</span>
                  <NumberInput
                    min={0}
                    max={1}
                    step={0.05}
                    value={settings.stiffness}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      if (Number.isFinite(v)) update({ stiffness: Math.max(0, Math.min(1, v)) });
                    }}
                  />
                </label>
                <label>
                  <span className="field-label">Bend / curl stiffness</span>
                  <NumberInput
                    min={0}
                    max={1}
                    step={0.05}
                    value={settings.bendStiffness}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      if (Number.isFinite(v)) update({ bendStiffness: Math.max(0, Math.min(1, v)) });
                    }}
                  />
                </label>
                <label>
                  <span className="field-label">Damping</span>
                  <NumberInput
                    min={0}
                    max={1}
                    step={0.01}
                    value={settings.damping}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      if (Number.isFinite(v)) update({ damping: Math.max(0, Math.min(1, v)) });
                    }}
                  />
                </label>
                <label>
                  <span className="field-label">Gravity (m/s²)</span>
                  <NumberInput
                    min={-100}
                    max={100}
                    step={0.5}
                    value={settings.gravity}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      if (Number.isFinite(v)) update({ gravity: Math.max(-100, Math.min(100, v)) });
                    }}
                  />
                </label>
                <label>
                  <span className="field-label">Collision margin (m)</span>
                  <NumberInput
                    min={0}
                    max={1}
                    step={0.001}
                    value={settings.collisionMargin}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      if (Number.isFinite(v)) update({ collisionMargin: Math.max(0, Math.min(1, v)) });
                    }}
                  />
                </label>
                <label>
                  <span className="field-label">Friction</span>
                  <NumberInput
                    min={0}
                    max={1}
                    step={0.05}
                    value={settings.friction}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      if (Number.isFinite(v)) update({ friction: Math.max(0, Math.min(1, v)) });
                    }}
                  />
                </label>
                <label>
                  <span className="field-label">Substeps</span>
                  <NumberInput
                    min={1}
                    max={64}
                    step={1}
                    value={settings.substeps}
                    onChange={(e) => {
                      const v = Math.round(Number(e.target.value));
                      if (Number.isFinite(v) && v >= 1 && v <= 64) update({ substeps: v });
                    }}
                  />
                </label>
                <label>
                  <span className="field-label">Solver iterations</span>
                  <NumberInput
                    min={1}
                    max={128}
                    step={1}
                    value={settings.iterations}
                    onChange={(e) => {
                      const v = Math.round(Number(e.target.value));
                      if (Number.isFinite(v) && v >= 1 && v <= 128) update({ iterations: v });
                    }}
                  />
                </label>
              </div>

              <div>
                <span className="field-label">Wind (m/s² X / Y / Z)</span>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "6px" }}>
                  {[0, 1, 2].map((axis) => (
                    <NumberInput
                      key={axis}
                      min={-200}
                      max={200}
                      step={0.5}
                      value={settings.wind[axis]}
                      onChange={(e) => {
                        const v = Number(e.target.value);
                        if (!Number.isFinite(v)) return;
                        const nextWind: [number, number, number] = [...settings.wind] as [number, number, number];
                        nextWind[axis] = Math.max(-200, Math.min(200, v));
                        update({ wind: nextWind });
                      }}
                    />
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      </details>
    </div>
  );
}
