import { useState, useEffect } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { MaterialBindingRule, StoredProject } from "./fileRegistry";

export function MaterialBindingInspector({
  project,
  nodeId,
  bindings,
  onChange,
}: {
  project: StoredProject;
  nodeId: string;
  bindings?: MaterialBindingRule[];
  onChange: (bindings: MaterialBindingRule[]) => void;
}) {
  const [availableMaterials, setAvailableMaterials] = useState<{ path: string; name: string }[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let active = true;
    async function loadMaterials() {
      setLoading(true);
      try {
        const res = (await invoke("inspect_stage_materials", {
          project,
          nodeId,
        })) as { materials: { path: string; name: string }[] };
        if (active && res?.materials) {
          setAvailableMaterials(res.materials);
        }
      } catch {
        // Stage inspection is non-fatal if inputs are pending
      } finally {
        if (active) setLoading(false);
      }
    }
    loadMaterials();
    return () => {
      active = false;
    };
  }, [project, nodeId]);

  const list = bindings || [];

  function addRule() {
    const newRule: MaterialBindingRule = {
      id: crypto.randomUUID(),
      enabled: true,
      targetQuery: "*",
      materialPath: availableMaterials[0]?.path || "/Materials/Material",
      strength: "strongerThanDescendants",
    };
    onChange([...list, newRule]);
  }

  function updateRule(id: string, patch: Partial<MaterialBindingRule>) {
    onChange(list.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }

  function removeRule(id: string) {
    onChange(list.filter((r) => r.id !== id));
  }

  return (
    <div className="node-options material-binding-inspector" style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <strong>Material Bindings</strong>
        <button onClick={addRule} style={{ fontSize: "11px", padding: "4px 8px" }}>
          ＋ Add Binding
        </button>
      </div>

      {loading && <small style={{ color: "#9eb6ab" }}>Inspecting materials in connected stage...</small>}

      {list.length === 0 ? (
        <p style={{ color: "#8b9c96", fontSize: "11px", margin: "4px 0" }}>
          No bindings authored. Click "+ Add Binding" to target geometry prims.
        </p>
      ) : (
        <div style={{ overflowX: "auto", border: "1px solid #283a42", borderRadius: "6px", background: "#0e181c" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "11px" }}>
            <thead>
              <tr style={{ background: "#162329", color: "#8b9c96", textAlign: "left", borderBottom: "1px solid #283a42" }}>
                <th style={{ padding: "4px 6px", width: "24px" }}>On</th>
                <th style={{ padding: "4px 6px" }}>Target Prim / Pattern</th>
                <th style={{ padding: "4px 6px" }}>Material</th>
                <th style={{ padding: "4px 6px" }}>Strength</th>
                <th style={{ padding: "4px 6px", width: "28px" }}></th>
              </tr>
            </thead>
            <tbody>
              {list.map((rule) => (
                <tr key={rule.id} style={{ borderBottom: "1px solid #162329" }}>
                  <td style={{ padding: "4px 6px", textAlign: "center" }}>
                    <input
                      type="checkbox"
                      checked={rule.enabled}
                      onChange={(e) => updateRule(rule.id, { enabled: e.target.checked })}
                      title="Enable binding"
                    />
                  </td>
                  <td style={{ padding: "4px 6px" }}>
                    <input
                      type="text"
                      value={rule.targetQuery}
                      placeholder="e.g. *Mesh* or /Scene/Body"
                      onChange={(e) => updateRule(rule.id, { targetQuery: e.target.value })}
                      style={{ width: "100%", minWidth: "110px", padding: "3px 5px", fontSize: "11px" }}
                    />
                  </td>
                  <td style={{ padding: "4px 6px" }}>
                    {availableMaterials.length > 0 ? (
                      <select
                        value={rule.materialPath}
                        onChange={(e) => updateRule(rule.id, { materialPath: e.target.value })}
                        style={{ width: "100%", minWidth: "110px", padding: "3px 5px", fontSize: "11px" }}
                      >
                        {availableMaterials.map((m) => (
                          <option key={m.path} value={m.path}>
                            {m.name} ({m.path})
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type="text"
                        value={rule.materialPath}
                        placeholder="/Materials/MyMaterial"
                        onChange={(e) => updateRule(rule.id, { materialPath: e.target.value })}
                        style={{ width: "100%", minWidth: "110px", padding: "3px 5px", fontSize: "11px" }}
                      />
                    )}
                  </td>
                  <td style={{ padding: "4px 6px" }}>
                    <select
                      value={rule.strength}
                      onChange={(e) => updateRule(rule.id, { strength: e.target.value as any })}
                      style={{ width: "100%", minWidth: "90px", padding: "3px 5px", fontSize: "11px" }}
                    >
                      <option value="strongerThanDescendants">Stronger</option>
                      <option value="weakerThanDescendants">Weaker</option>
                    </select>
                  </td>
                  <td style={{ padding: "4px 6px", textAlign: "center" }}>
                    <button
                      className="danger"
                      style={{ padding: "2px 6px", fontSize: "10px" }}
                      onClick={() => removeRule(rule.id)}
                      title="Remove binding"
                    >
                      ✕
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
