import { useState } from "react";
import { InspectorHint } from "./InspectorHint";
import { NumberInput } from "./NumberInput";
import { identityTransform, type Transform, type TransformTargetRule } from "./fileRegistry";

const thStyle: React.CSSProperties = {
  textAlign: "left",
  padding: "6px 8px",
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
  padding: "4px 6px",
  fontSize: "11px",
  fontFamily: "monospace",
};

export function TransformInspector({
  value,
  targets,
  selectedPrim,
  onTransformChange,
  onTargetsChange,
}: {
  value?: Transform;
  targets?: TransformTargetRule[];
  selectedPrim?: string;
  onTransformChange: (next: Transform) => void;
  onTargetsChange: (next: TransformTargetRule[]) => void;
}) {
  const [draftPattern, setDraftPattern] = useState("");
  const current = value || identityTransform;
  const rows = targets || [];

  function setAxis(field: keyof Transform, index: number, axisValue: number) {
    const vector: [number, number, number] = [...current[field]];
    vector[index] = Number.isFinite(axisValue) ? axisValue : field === "scale" ? 1 : 0;
    onTransformChange({ ...current, [field]: vector });
  }

  function addPatternRow(rawPattern: string) {
    const pattern = rawPattern.trim() || "*";
    onTargetsChange([
      ...rows,
      { id: crypto.randomUUID(), enabled: true, pattern },
    ]);
  }

  function addSelectedPrim() {
    const prim = (selectedPrim || "").trim();
    if (!prim) return;
    const existing = rows.find((r) => r.pattern.trim() === prim);
    if (existing) {
      if (!existing.enabled) {
        onTargetsChange(rows.map((r) => (r.id === existing.id ? { ...r, enabled: true } : r)));
      }
      return;
    }
    onTargetsChange([
      ...rows,
      { id: crypto.randomUUID(), enabled: true, pattern: prim },
    ]);
  }

  function updateRow(id: string, patch: Partial<TransformTargetRule>) {
    onTargetsChange(rows.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }

  function removeRow(id: string) {
    onTargetsChange(rows.filter((r) => r.id !== id));
  }

  return (
    <div className="transform-settings">
      <h3 className="field-label">
        Transform
        <InspectorHint text="Applied after merging incoming stages. Translation is in metres; rotation uses XYZ order in degrees." />
      </h3>
      {(["translation", "rotation", "scale"] as const).map((field) => (
        <div key={field}>
          <span className="vector-label">
            {field === "translation"
              ? "Translation · m"
              : field === "rotation"
                ? "Rotation · °"
                : "Scale"}
          </span>
          <div className="vector-input">
            {["X", "Y", "Z"].map((axis, index) => (
              <label key={axis}>
                {axis}
                <NumberInput
                  aria-label={`${field} ${axis}`}
                  step={field === "rotation" ? 1 : 0.1}
                  value={current[field][index]}
                  onChange={(e) => setAxis(field, index, Number(e.target.value))}
                />
              </label>
            ))}
          </div>
        </div>
      ))}
      <button onClick={() => onTransformChange(structuredClone(identityTransform))}>
        Reset transform
      </button>

      <div style={{ display: "grid", gap: 6, marginTop: 8 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
          <h3 className="field-label" style={{ margin: 0 }}>
            Prim Filter
            <InspectorHint text="Optional target prim filter. Leave empty to transform the stage root. Supports exact paths (/Scene/Mesh), wildcards (*Mesh*, /Scene/Char*), comma-separated patterns, and !exclude." />
          </h3>
          <button
            type="button"
            disabled={!selectedPrim}
            title={selectedPrim ? `Add ${selectedPrim} to selection` : "Select a prim in the viewport or scene overview first"}
            onClick={addSelectedPrim}
          >
            Add to selection
          </button>
        </div>

        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input
            type="text"
            style={inputStyle}
            placeholder="/Scene/* or *Mesh*"
            value={draftPattern}
            onChange={(e) => setDraftPattern(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addPatternRow(draftPattern);
                setDraftPattern("");
              }
            }}
          />
          <button
            type="button"
            style={{ whiteSpace: "nowrap" }}
            onClick={() => {
              addPatternRow(draftPattern);
              setDraftPattern("");
            }}
          >
            Add row
          </button>
        </div>

        <div style={{ border: "1px solid #28363e", borderRadius: 4, overflowX: "auto", background: "#0e181c" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
            <thead>
              <tr style={{ background: "#162329" }}>
                <th style={{ ...thStyle, width: 32, textAlign: "center" }}>On</th>
                <th style={thStyle}>Prim / Pattern</th>
                <th style={{ ...thStyle, width: 28 }}></th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={3} style={{ padding: 10, color: "#687b84", textAlign: "center", fontSize: 11 }}>
                    No filter rows — transforms the stage root.
                  </td>
                </tr>
              ) : (
                rows.map((row) => (
                  <tr key={row.id} style={{ opacity: row.enabled ? 1 : 0.55 }}>
                    <td style={{ ...tdStyle, textAlign: "center" }}>
                      <input
                        type="checkbox"
                        checked={row.enabled}
                        onChange={(e) => updateRow(row.id, { enabled: e.target.checked })}
                      />
                    </td>
                    <td style={tdStyle}>
                      <input
                        type="text"
                        style={inputStyle}
                        placeholder="/Scene/* or *Mesh*"
                        value={row.pattern}
                        onChange={(e) => updateRow(row.id, { pattern: e.target.value })}
                      />
                    </td>
                    <td style={{ ...tdStyle, textAlign: "center" }}>
                      <button
                        type="button"
                        className="danger"
                        style={{ padding: "2px 6px", fontSize: 10 }}
                        title="Remove row"
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
    </div>
  );
}
