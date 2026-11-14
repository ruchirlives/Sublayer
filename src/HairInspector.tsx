import { useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { defaultHairSettings, type HairSettings } from "./fileRegistry";
import { NumberInput } from "./NumberInput";

type CurvePrimInfo = {
  path: string;
  name: string;
  curveCount: number;
  instanceProxy: boolean;
};

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
  const settings: HairSettings = { ...defaultHairSettings, ...(value || {}) };
  const update = (patch: Partial<HairSettings>) => onChange({ ...settings, ...patch });
  const [curves, setCurves] = useState<CurvePrimInfo[] | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState("");

  async function scanCurves() {
    if (!graph || !isTauri()) return;
    setScanning(true);
    setScanError("");
    try {
      const res = await invoke<{ curves?: CurvePrimInfo[] }>("usd_request", {
        request: { operation: "inspect_stage_curves", graph: JSON.parse(graph) },
      });
      setCurves(res?.curves || []);
    } catch (e) {
      setScanError(String(e));
    } finally {
      setScanning(false);
    }
  }

  const toggleCurve = (curvePath: string) => {
    if (!curves) return;
    const editable = curves.filter((c) => !c.instanceProxy);
    const nextSelected = editable
      .filter((c) => {
        const wasSelected = hairQueryMatches(settings.targetQuery, c.path);
        return c.path === curvePath ? !wasSelected : wasSelected;
      })
      .map((c) => c.path);
    if (nextSelected.length === editable.length) {
      update({ targetQuery: "" });
    } else if (nextSelected.length === 0) {
      update({ targetQuery: "__none__" });
    } else {
      update({ targetQuery: nextSelected.join(", ") });
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
          {scanning ? "Scanning..." : curves ? "Rescan curves" : "Scan curves"}
        </button>
        {curves && curves.length > 0 && (
          <>
            <button type="button" onClick={() => update({ targetQuery: "" })}>
              All
            </button>
            <button type="button" onClick={() => update({ targetQuery: "__none__" })}>
              None
            </button>
          </>
        )}
      </div>

      {scanError && <p className="hint" style={{ margin: 0, color: "#f69" }}>{scanError}</p>}

      {curves && (
        <div
          style={{
            maxHeight: "150px",
            overflowY: "auto",
            border: "1px solid rgba(255,255,255,0.12)",
            borderRadius: "4px",
            padding: "6px",
            display: "flex",
            flexDirection: "column",
            gap: "4px",
            fontSize: "12px",
          }}
        >
          {curves.length === 0 ? (
            <span className="hint">No BasisCurves prims found on incoming stage.</span>
          ) : (
            curves.map((c) => {
              const matched = !c.instanceProxy && hairQueryMatches(settings.targetQuery, c.path);
              return (
                <label
                  key={c.path}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "6px",
                    opacity: c.instanceProxy ? 0.55 : 1,
                    cursor: c.instanceProxy ? "not-allowed" : "pointer",
                  }}
                  title={c.path}
                >
                  <input
                    type="checkbox"
                    checked={matched}
                    disabled={c.instanceProxy}
                    onChange={() => toggleCurve(c.path)}
                  />
                  <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {c.name}
                  </span>
                  <small style={{ opacity: 0.7 }}>
                    {c.instanceProxy ? "proxy" : `${c.curveCount.toLocaleString()} curves`}
                  </small>
                </label>
              );
            })
          )}
        </div>
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
    </div>
  );
}
