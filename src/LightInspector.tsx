import { useState, useEffect } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { LightOverride, StoredProject } from "./fileRegistry";
import { NumberInput } from "./NumberInput";

type DiscoveredLight = {
  path: string;
  name: string;
  type: string;
  intensity: number;
  exposure: number;
  color: [number, number, number];
  enableColorTemperature: boolean;
  colorTemperature: number;
};

export function LightInspector({
  project,
  nodeId,
  overrides,
  onChange,
}: {
  project: StoredProject;
  nodeId: string;
  overrides?: LightOverride[];
  onChange: (overrides: LightOverride[]) => void;
}) {
  const [discoveredLights, setDiscoveredLights] = useState<DiscoveredLight[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let active = true;
    async function loadLights() {
      setLoading(true);
      try {
        const res = (await invoke("inspect_stage_lights", {
          project,
          nodeId,
        })) as { lights: DiscoveredLight[] };
        if (active && res?.lights) {
          setDiscoveredLights(res.lights);
        }
      } catch {
        // Stage inspection is non-fatal if inputs are pending
      } finally {
        if (active) setLoading(false);
      }
    }
    loadLights();
    return () => {
      active = false;
    };
  }, [project, nodeId]);

  const list = overrides || [];

  function addLightOverride(light?: DiscoveredLight) {
    const newOverride: LightOverride = {
      id: crypto.randomUUID(),
      lightPath: light?.path || discoveredLights[0]?.path || "/Scene/KeyLight",
      lightType: light ? light.type : "DistantLight",
      enabled: true,
      intensity: light ? light.intensity : 1000.0,
      exposure: light ? light.exposure : 0.0,
      color: light ? light.color : [1.0, 1.0, 1.0],
      enableColorTemperature: light ? light.enableColorTemperature : false,
      colorTemperature: light ? light.colorTemperature : 6500.0,
    };
    onChange([...list, newOverride]);
  }

  function updateOverride(id: string, patch: Partial<LightOverride>) {
    onChange(list.map((o) => (o.id === id ? { ...o, ...patch } : o)));
  }

  function removeOverride(id: string) {
    onChange(list.filter((o) => o.id !== id));
  }

  return (
    <div className="node-options light-inspector" style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <strong>Light Controls</strong>
        <button onClick={() => addLightOverride()} style={{ fontSize: "11px", padding: "4px 8px" }}>
          ＋ Override Light
        </button>
      </div>

      {loading && <small style={{ color: "#9eb6ab" }}>Discovering lights in stage...</small>}

      {discoveredLights.length > 0 && list.length === 0 && (
        <div style={{ background: "#132025", padding: "8px", borderRadius: "6px" }}>
          <p style={{ margin: "0 0 6px", fontSize: "11px", color: "#9eb6ab" }}>
            Discovered {discoveredLights.length} light(s) in upstream USD:
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
            {discoveredLights.map((l) => (
              <button
                key={l.path}
                onClick={() => addLightOverride(l)}
                style={{ fontSize: "10px", padding: "3px 6px" }}
              >
                ＋ {l.name} ({l.type.replace("Light", "")})
              </button>
            ))}
          </div>
        </div>
      )}

      {list.length === 0 ? (
        <p style={{ color: "#8b9c96", fontSize: "11px", margin: "4px 0" }}>
          No lights overridden. Target a light prim to tweak intensity, color, and exposure.
        </p>
      ) : (
        list.map((item) => (
          <div
            key={item.id}
            style={{
              background: "#162329",
              border: "1px solid #283a42",
              borderRadius: "6px",
              padding: "8px",
              display: "flex",
              flexDirection: "column",
              gap: "8px",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <label style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: "6px", margin: 0, cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={item.enabled}
                  onChange={(e) => updateOverride(item.id, { enabled: e.target.checked })}
                />
                <span style={{ fontSize: "11px", fontWeight: "bold" }}>Light On/Off</span>
              </label>
              <button
                className="danger"
                style={{ padding: "2px 6px", fontSize: "10px" }}
                onClick={() => removeOverride(item.id)}
              >
                ✕
              </button>
            </div>

            <div style={{ display: "flex", gap: "8px" }}>
              <label style={{ margin: 0, fontSize: "11px", flex: 2 }}>
                Target Light Prim
                {discoveredLights.length > 0 ? (
                  <select
                    value={item.lightPath}
                    onChange={(e) => {
                      const found = discoveredLights.find((l) => l.path === e.target.value);
                      updateOverride(item.id, {
                        lightPath: e.target.value,
                        ...(found ? { lightType: found.type, intensity: found.intensity, exposure: found.exposure, color: found.color } : {}),
                      });
                    }}
                    style={{ width: "100%", padding: "5px", fontSize: "11px", marginTop: "4px" }}
                  >
                    {discoveredLights.map((l) => (
                      <option key={l.path} value={l.path}>
                        {l.name} ({l.type})
                      </option>
                    ))}
                    {!discoveredLights.some((l) => l.path === item.lightPath) && (
                      <option value={item.lightPath}>{item.lightPath} (Custom)</option>
                    )}
                  </select>
                ) : (
                  <input
                    type="text"
                    value={item.lightPath}
                    placeholder="/Scene/KeyLight"
                    onChange={(e) => updateOverride(item.id, { lightPath: e.target.value })}
                    style={{ width: "100%", padding: "5px", fontSize: "11px", marginTop: "4px" }}
                  />
                )}
              </label>

              {!discoveredLights.some((l) => l.path === item.lightPath) && (
                <label style={{ margin: 0, fontSize: "11px", flex: 1 }}>
                  Type
                  <select
                    value={item.lightType || "DistantLight"}
                    onChange={(e) => updateOverride(item.id, { lightType: e.target.value })}
                    style={{ width: "100%", padding: "5px", fontSize: "11px", marginTop: "4px" }}
                  >
                    <option value="DistantLight">Distant (Sun)</option>
                    <option value="DomeLight">Dome (Ambient)</option>
                    <option value="SphereLight">Sphere (Point)</option>
                  </select>
                </label>
              )}
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" }}>
              <label style={{ margin: 0, fontSize: "11px" }}>
                Intensity
                <NumberInput
                  step="any"
                  value={item.intensity ?? 1.0}
                  onChange={(e) => updateOverride(item.id, { intensity: Number(e.target.value) })}
                  style={{ width: "100%", padding: "4px", fontSize: "11px", marginTop: "4px" }}
                />
              </label>

              <label style={{ margin: 0, fontSize: "11px" }}>
                Exposure (EV)
                <NumberInput
                  step="0.1"
                  value={item.exposure ?? 0.0}
                  onChange={(e) => updateOverride(item.id, { exposure: Number(e.target.value) })}
                  style={{ width: "100%", padding: "4px", fontSize: "11px", marginTop: "4px" }}
                />
              </label>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <label style={{ margin: 0, fontSize: "11px", flex: 1 }}>
                Color (RGB & Picker)
                <div style={{ display: "flex", gap: "6px", alignItems: "center", marginTop: "4px" }}>
                  <input
                    type="color"
                    aria-label="Light Color Picker"
                    value={(() => {
                      const c = item.color || [1, 1, 1];
                      const toHex = (v: number) => Math.max(0, Math.min(255, Math.round(v * 255))).toString(16).padStart(2, "0");
                      return `#${toHex(c[0])}${toHex(c[1])}${toHex(c[2])}`;
                    })()}
                    onChange={(e) => {
                      const hex = e.target.value;
                      const r = parseInt(hex.slice(1, 3), 16) / 255;
                      const g = parseInt(hex.slice(3, 5), 16) / 255;
                      const b = parseInt(hex.slice(5, 7), 16) / 255;
                      updateOverride(item.id, { color: [Number(r.toFixed(3)), Number(g.toFixed(3)), Number(b.toFixed(3))] });
                    }}
                    style={{ width: "28px", height: "24px", padding: 0, border: "none", cursor: "pointer", background: "none" }}
                  />
                  {[0, 1, 2].map((idx) => (
                    <NumberInput
                      key={idx}
                      step="0.05"
                      min="0"
                      max="10"
                      value={(item.color || [1, 1, 1])[idx]}
                      onChange={(e) => {
                        const nextColor = [...(item.color || [1, 1, 1])] as [number, number, number];
                        nextColor[idx] = Number(e.target.value);
                        updateOverride(item.id, { color: nextColor });
                      }}
                      style={{ width: "100%", padding: "4px", fontSize: "11px" }}
                    />
                  ))}
                </div>
              </label>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "4px", marginTop: "2px" }}>
              <label style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: "6px", margin: 0, cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={Boolean(item.enableColorTemperature)}
                  onChange={(e) => updateOverride(item.id, { enableColorTemperature: e.target.checked })}
                />
                <span style={{ fontSize: "11px" }}>Color Temperature (Kelvin)</span>
              </label>
              {item.enableColorTemperature && (
                <NumberInput
                  step="100"
                  min="1000"
                  max="20000"
                  value={item.colorTemperature ?? 6500}
                  onChange={(e) => updateOverride(item.id, { colorTemperature: Number(e.target.value) })}
                  style={{ width: "100%", padding: "4px", fontSize: "11px" }}
                />
              )}
            </div>
          </div>
        ))
      )}
    </div>
  );
}
