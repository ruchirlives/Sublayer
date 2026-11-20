import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  type CacheEditFabricPreset,
  type CacheEditFalloff,
  type CacheEditLayer,
  type CacheEditLayerMode,
  type CacheEditSettings,
  type CacheEditSimMode,
  createCacheEditLayer,
  defaultCacheEditSettings,
  graphPreview,
  type StoredProject,
} from "./fileRegistry";
import { NumberInput } from "./NumberInput";

const FABRIC_PRESETS: Record<
  Exclude<CacheEditFabricPreset, "custom">,
  {
    label: string;
    clothStretchStiffness: number;
    clothBendStiffness: number;
    clothDamping: number;
    clothFriction: number;
    simAirDrag: number;
  }
> = {
  silk: {
    label: "Silk / Chiffon",
    clothStretchStiffness: 0.92,
    clothBendStiffness: 0.08,
    clothDamping: 0.04,
    clothFriction: 0.15,
    simAirDrag: 0.18,
  },
  cotton: {
    label: "Cotton / Linen",
    clothStretchStiffness: 0.96,
    clothBendStiffness: 0.3,
    clothDamping: 0.08,
    clothFriction: 0.35,
    simAirDrag: 0.07,
  },
  denim: {
    label: "Denim / Canvas",
    clothStretchStiffness: 0.99,
    clothBendStiffness: 0.65,
    clothDamping: 0.12,
    clothFriction: 0.5,
    simAirDrag: 0.03,
  },
  wool: {
    label: "Wool / Knit",
    clothStretchStiffness: 0.88,
    clothBendStiffness: 0.45,
    clothDamping: 0.14,
    clothFriction: 0.6,
    simAirDrag: 0.06,
  },
  leather: {
    label: "Leather / Thick",
    clothStretchStiffness: 0.99,
    clothBendStiffness: 0.85,
    clothDamping: 0.18,
    clothFriction: 0.55,
    simAirDrag: 0.02,
  },
};

type StageMeshInfo = {
  path: string;
  name: string;
  vertexCount: number;
  timeSampleCount?: number;
  instanceProxy?: boolean;
};

type CacheEditInspectorProps = {
  value?: CacheEditSettings;
  project: StoredProject;
  nodeId: string;
  currentFrame?: number;
  onJumpToFrame?: (frame: number) => void;
  onChange: (next: CacheEditSettings) => void;
};

export function CacheEditInspector({
  value,
  project,
  nodeId,
  currentFrame = 0,
  onJumpToFrame,
  onChange,
}: CacheEditInspectorProps) {
  const settings: CacheEditSettings = {
    ...defaultCacheEditSettings,
    ...(value || {}),
    layers: value?.layers?.length ? value.layers : defaultCacheEditSettings.layers,
  };
  const activeLayerId =
    settings.layers.some((l) => l.id === settings.activeLayerId)
      ? settings.activeLayerId
      : settings.layers[0]?.id || "";

  const [meshes, setMeshes] = useState<StageMeshInfo[]>([]);
  const [stageRange, setStageRange] = useState<[number, number] | null>(null);
  const [scanning, setScanning] = useState(false);

  const scanUpstreamMeshes = () => {
    const graph = graphPreview(project, nodeId, true);
    if (!graph) {
      setMeshes([]);
      return;
    }
    setScanning(true);
    invoke<{
      meshes?: StageMeshInfo[];
      startTimeCode?: number;
      endTimeCode?: number;
    }>("usd_request", {
      request: { operation: "inspect_stage_curves", graph: JSON.parse(graph) },
    })
      .then((res) => {
        setMeshes(res.meshes || []);
        if (
          typeof res.startTimeCode === "number" &&
          typeof res.endTimeCode === "number"
        ) {
          setStageRange([res.startTimeCode, res.endTimeCode]);
        }
      })
      .catch(() => {})
      .finally(() => setScanning(false));
  };

  useEffect(() => {
    scanUpstreamMeshes();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId]);

  const patch = (partial: Partial<CacheEditSettings>) => {
    onChange({ ...settings, ...partial });
  };

  const updateLayer = (layerId: string, partial: Partial<CacheEditLayer>) => {
    patch({
      layers: settings.layers.map((l) =>
        l.id === layerId ? { ...l, ...partial } : l,
      ),
    });
  };

  const addLayer = () => {
    const next = createCacheEditLayer(settings.layers.length + 1, Math.round(currentFrame));
    patch({
      activeLayerId: next.id,
      layers: [...settings.layers, next],
    });
  };

  const removeLayer = (layerId: string) => {
    if (settings.layers.length <= 1) {
      const reset = { ...settings.layers[0], deltas: undefined };
      patch({ layers: [reset], activeLayerId: reset.id });
      return;
    }
    const remaining = settings.layers.filter((l) => l.id !== layerId);
    const nextActive =
      activeLayerId === layerId ? remaining[remaining.length - 1].id : activeLayerId;
    patch({ layers: remaining, activeLayerId: nextActive });
  };

  const moveLayer = (index: number, delta: -1 | 1) => {
    const target = index + delta;
    if (target < 0 || target >= settings.layers.length) return;
    const copy = [...settings.layers];
    const [item] = copy.splice(index, 1);
    copy.splice(target, 0, item);
    patch({ layers: copy });
  };

  const maskCount = Object.keys(settings.maskWeights || {}).length;

  return (
    <div
      className="hair-inspector"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 14,
        padding: "10px 12px 18px",
      }}
    >
      <div
        className="field-row"
        style={{ alignItems: "center", justifyContent: "space-between", gap: 8 }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <strong>Cache Sculptor</strong>
          <span
            style={{
              fontSize: 11,
              padding: "1px 6px",
              borderRadius: 4,
              background: "rgba(59,130,246,0.18)",
              border: "1px solid rgba(96,165,250,0.4)",
            }}
          >
            Frame {Math.round(currentFrame)}
          </span>
        </div>
        <button
          type="button"
          className="mini-btn"
          onClick={scanUpstreamMeshes}
          disabled={scanning}
          title="Rescan upstream USD meshes"
        >
          {scanning ? "Scanning..." : "Scan Meshes"}
        </button>
      </div>
      {stageRange && (
        <p className="hint" style={{ margin: 0, lineHeight: 1.45 }}>
          Stage frames: {stageRange[0]}–{stageRange[1]} · Use <strong>Sculpt / Cloth</strong> in the viewport toolbar to sculpt meshes directly.
        </p>
      )}

      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 10,
          padding: "10px 12px",
          borderRadius: 8,
          border: "1px solid rgba(255,255,255,0.08)",
          background: "rgba(255,255,255,0.02)",
        }}
      >
        <label className="field">
          <span>Target Mesh</span>
          <select
            value={
              meshes.some((m) => m.path === settings.targetQuery)
                ? settings.targetQuery
                : settings.targetQuery
                  ? "__custom__"
                  : ""
            }
            onChange={(e) => {
              if (e.target.value !== "__custom__") {
                patch({ targetQuery: e.target.value });
              }
            }}
          >
            <option value="">All editable meshes (*)</option>
            {meshes.map((m) => (
              <option key={m.path} value={m.path}>
                {m.name} ({m.vertexCount.toLocaleString()} verts
                {m.timeSampleCount ? `, ${m.timeSampleCount} frames` : ", static"})
              </option>
            ))}
            {settings.targetQuery && !meshes.some((m) => m.path === settings.targetQuery) && (
              <option value="__custom__">Custom: {settings.targetQuery}</option>
            )}
          </select>
        </label>

        <label className="field">
          <span>Target Query / Prim Path</span>
          <input
            type="text"
            value={settings.targetQuery}
            placeholder="* or /Scene/Cloth/Dress"
            onChange={(e) => patch({ targetQuery: e.target.value })}
          />
        </label>
      </div>

      {/* Sculpting Layers Stack */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div
          className="field-row"
          style={{ alignItems: "center", justifyContent: "space-between", gap: 8 }}
        >
          <strong>Sculpt Layers ({settings.layers.length})</strong>
          <button type="button" className="mini-btn" onClick={addLayer}>
            + Add Layer (F{Math.round(currentFrame)})
          </button>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {settings.layers.map((layer, idx) => {
            const isActive = layer.id === activeLayerId;
            const sculptedPrims = Object.keys(layer.deltas || {}).length;
            const rangeBadge =
              layer.mode === "allFrames"
                ? "All frames"
                : layer.mode === "singleFrame"
                  ? `F${layer.centerFrame}`
                  : `F${layer.centerFrame - layer.fadeIn}–F${layer.centerFrame + layer.fadeOut} (peak F${layer.centerFrame})`;

            return (
              <div
                key={layer.id}
                onClick={() => {
                  patch({ activeLayerId: layer.id });
                  if (layer.mode !== "allFrames" && onJumpToFrame) {
                    onJumpToFrame(layer.centerFrame);
                  }
                }}
                style={{
                  border: isActive ? "1px solid #60a5fa" : "1px solid rgba(255,255,255,0.12)",
                  background: isActive ? "rgba(59,130,246,0.12)" : "rgba(255,255,255,0.03)",
                  borderRadius: 8,
                  padding: "10px 12px",
                  cursor: "pointer",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <input
                    type="checkbox"
                    checked={layer.enabled}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => updateLayer(layer.id, { enabled: e.target.checked })}
                    title="Enable/mute layer"
                  />
                  <input
                    type="text"
                    value={layer.name}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => updateLayer(layer.id, { name: e.target.value })}
                    style={{ flex: 1, minWidth: 0, fontWeight: isActive ? 600 : 400 }}
                  />
                  <button
                    type="button"
                    className="mini-btn"
                    title="Move up"
                    disabled={idx === 0}
                    onClick={(e) => { e.stopPropagation(); moveLayer(idx, -1); }}
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    className="mini-btn"
                    title="Move down"
                    disabled={idx === settings.layers.length - 1}
                    onClick={(e) => { e.stopPropagation(); moveLayer(idx, 1); }}
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    className="mini-btn"
                    title="Delete layer"
                    onClick={(e) => { e.stopPropagation(); removeLayer(layer.id); }}
                  >
                    ✕
                  </button>
                </div>

                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    fontSize: 11,
                    opacity: 0.78,
                    marginTop: 6,
                  }}
                >
                  <span>{rangeBadge}</span>
                  <span>{sculptedPrims > 0 ? `${sculptedPrims} mesh edited` : "Empty"}</span>
                </div>

                {isActive && (
                  <div
                    onClick={(e) => e.stopPropagation()}
                    style={{
                      marginTop: 10,
                      paddingTop: 10,
                      borderTop: "1px solid rgba(255,255,255,0.08)",
                      display: "flex",
                      flexDirection: "column",
                      gap: 10,
                    }}
                  >
                    <label className="field">
                      <span>Weight ({layer.weight.toFixed(2)})</span>
                      <input
                        type="range"
                        min={-2}
                        max={2}
                        step={0.05}
                        value={layer.weight}
                        onChange={(e) => updateLayer(layer.id, { weight: Number(e.target.value) })}
                      />
                    </label>

                    <div className="field-row" style={{ gap: 6 }}>
                      <label className="field" style={{ flex: 1 }}>
                        <span>Temporal Mode</span>
                        <select
                          value={layer.mode}
                          onChange={(e) =>
                            updateLayer(layer.id, { mode: e.target.value as CacheEditLayerMode })
                          }
                        >
                          <option value="frameRange">Frame Range</option>
                          <option value="singleFrame">Single Frame</option>
                          <option value="allFrames">All Frames</option>
                        </select>
                      </label>
                      {layer.mode === "frameRange" && (
                        <label className="field" style={{ flex: 1 }}>
                          <span>Falloff</span>
                          <select
                            value={layer.falloff}
                            onChange={(e) =>
                              updateLayer(layer.id, { falloff: e.target.value as CacheEditFalloff })
                            }
                          >
                            <option value="smoothstep">Smoothstep</option>
                            <option value="gaussian">Gaussian</option>
                            <option value="linear">Linear</option>
                            <option value="constant">Constant</option>
                          </select>
                        </label>
                      )}
                    </div>

                    {layer.mode !== "allFrames" && (
                      <div className="field-row" style={{ gap: 6, alignItems: "flex-end" }}>
                        <label className="field" style={{ flex: 1 }}>
                          <span>Center Frame</span>
                          <NumberInput
                            value={layer.centerFrame}
                            step={1}
                            onChange={(e) => {
                              const v = Number(e.target.value);
                              if (Number.isFinite(v)) {
                                updateLayer(layer.id, { centerFrame: v });
                                onJumpToFrame?.(v);
                              }
                            }}
                          />
                        </label>
                        <button
                          type="button"
                          className="mini-btn"
                          onClick={() => updateLayer(layer.id, { centerFrame: Math.round(currentFrame) })}
                          title="Set center frame to current timeline frame"
                        >
                          Use F{Math.round(currentFrame)}
                        </button>
                      </div>
                    )}

                    {layer.mode === "frameRange" && (
                      <div className="field-row" style={{ gap: 6 }}>
                        <label className="field" style={{ flex: 1 }}>
                          <span>Fade In (frames)</span>
                          <NumberInput
                            value={layer.fadeIn}
                            min={0}
                            step={1}
                            onChange={(e) => {
                              const v = Number(e.target.value);
                              if (Number.isFinite(v)) updateLayer(layer.id, { fadeIn: Math.max(0, v) });
                            }}
                          />
                        </label>
                        <label className="field" style={{ flex: 1 }}>
                          <span>Fade Out (frames)</span>
                          <NumberInput
                            value={layer.fadeOut}
                            min={0}
                            step={1}
                            onChange={(e) => {
                              const v = Number(e.target.value);
                              if (Number.isFinite(v)) updateLayer(layer.id, { fadeOut: Math.max(0, v) });
                            }}
                          />
                        </label>
                      </div>
                    )}

                    {sculptedPrims > 0 && (
                      <button
                        type="button"
                        className="mini-btn"
                        onClick={() => updateLayer(layer.id, { deltas: undefined })}
                      >
                        Clear Layer Deltas
                      </button>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Full-Garment Cloth Simulation ("Simulate in Sublayer") */}
      <details
        open
        style={{
          padding: "10px 12px",
          borderRadius: 8,
          border: settings.simEnabled
            ? "1px solid rgba(52,211,153,0.45)"
            : "1px solid rgba(255,255,255,0.08)",
          background: settings.simEnabled
            ? "rgba(16,185,129,0.06)"
            : "rgba(255,255,255,0.02)",
        }}
      >
        <summary style={{ cursor: "pointer", fontWeight: 600, fontSize: 12 }}>
          Cloth Simulation (Simulate in Sublayer)
        </summary>
        <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 10 }}>
          <div className="field-row" style={{ alignItems: "center", justifyContent: "space-between", gap: 8 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 12 }}>
              <input
                type="checkbox"
                checked={Boolean(settings.simEnabled)}
                onChange={(e) => patch({ simEnabled: e.target.checked })}
              />
              <span>Enable GPU Cloth Simulation</span>
            </label>
            {settings.simEnabled && (
              <button
                type="button"
                className="mini-btn"
                onClick={() => patch({ simCacheVersion: (settings.simCacheVersion ?? 0) + 1 })}
                title="Clear cached simulation frames and restore base mesh"
              >
                Clear Cache
              </button>
            )}
          </div>
          {settings.simEnabled && (
            <p className="hint" style={{ margin: 0, lineHeight: 1.4 }}>
              Use <strong>Sim</strong> in the viewport timeline to advance cloth simulation from the current frame, and{" "}
              <strong>Play (▶)</strong> to play cached frames without re-simulating.
            </p>
          )}

          {settings.simEnabled && (
            <>
              {(!settings.targetQuery.trim() || settings.targetQuery.trim() === "*") && (
                <p
                  className="hint"
                  style={{
                    margin: 0,
                    padding: "6px 8px",
                    borderRadius: 6,
                    background: "rgba(245,158,11,0.12)",
                    border: "1px solid rgba(245,158,11,0.35)",
                    color: "#fcd34d",
                    lineHeight: 1.4,
                  }}
                >
                  Select a specific <strong>Target Mesh</strong> above so cloth simulation only runs on your garment instead of every mesh in the scene.
                </p>
              )}
              <div className="field-row" style={{ gap: 6 }}>
                <label className="field" style={{ flex: 1 }}>
                  <span>Mode</span>
                  <select
                    value={settings.simMode ?? "animated"}
                    onChange={(e) => patch({ simMode: e.target.value as CacheEditSimMode })}
                  >
                    <option value="animated">Animated Timeline</option>
                    <option value="static">Static Drape</option>
                  </select>
                </label>
                <label className="field" style={{ flex: 1 }}>
                  <span>Fabric Preset</span>
                  <select
                    value={settings.simFabricPreset ?? "custom"}
                    onChange={(e) => {
                      const preset = e.target.value as CacheEditFabricPreset;
                      if (preset === "custom") {
                        patch({ simFabricPreset: "custom" });
                      } else {
                        const p = FABRIC_PRESETS[preset];
                        patch({
                          simFabricPreset: preset,
                          clothStretchStiffness: p.clothStretchStiffness,
                          clothBendStiffness: p.clothBendStiffness,
                          clothDamping: p.clothDamping,
                          clothFriction: p.clothFriction,
                          simAirDrag: p.simAirDrag,
                        });
                      }
                    }}
                  >
                    <option value="custom">Custom</option>
                    {(Object.keys(FABRIC_PRESETS) as Exclude<CacheEditFabricPreset, "custom">[]).map((k) => (
                      <option key={k} value={k}>
                        {FABRIC_PRESETS[k].label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              {(settings.simMode ?? "animated") === "animated" && (
                <>
                  <div className="field-row" style={{ gap: 6, alignItems: "flex-end" }}>
                    <label className="field" style={{ flex: 1 }}>
                      <span>Start</span>
                      <NumberInput
                        value={settings.simStartFrame ?? 0}
                        step={1}
                        onChange={(e) => {
                          const v = Number(e.target.value);
                          if (Number.isFinite(v)) {
                            const end = Math.max(v, settings.simEndFrame ?? 48);
                            patch({ simStartFrame: v, simEndFrame: end });
                          }
                        }}
                      />
                    </label>
                    <label className="field" style={{ flex: 1 }}>
                      <span>End</span>
                      <NumberInput
                        value={settings.simEndFrame ?? 48}
                        step={1}
                        onChange={(e) => {
                          const v = Number(e.target.value);
                          if (Number.isFinite(v)) {
                            patch({ simEndFrame: Math.max(settings.simStartFrame ?? 0, v) });
                          }
                        }}
                      />
                    </label>
                    <label className="field" style={{ width: 64 }}>
                      <span>FPS</span>
                      <NumberInput
                        value={settings.simFps ?? 24}
                        min={1}
                        max={240}
                        step={1}
                        onChange={(e) => {
                          const v = Number(e.target.value);
                          if (Number.isFinite(v) && v > 0) patch({ simFps: Math.min(240, Math.max(1, v)) });
                        }}
                      />
                    </label>
                  </div>
                  {stageRange && (
                    <button
                      type="button"
                      className="mini-btn"
                      onClick={() => patch({ simStartFrame: stageRange[0], simEndFrame: stageRange[1] })}
                      title="Match simulation start/end to upstream USD stage time range"
                    >
                      Use Stage Range ({stageRange[0]}–{stageRange[1]})
                    </button>
                  )}
                </>
              )}

              <div className="field-row" style={{ gap: 6 }}>
                <label className="field" style={{ flex: 1 }}>
                  <span>Settle / Preroll</span>
                  <NumberInput
                    value={settings.simSettleSteps ?? 12}
                    min={0}
                    max={500}
                    step={1}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      if (Number.isFinite(v)) patch({ simSettleSteps: Math.min(500, Math.max(0, Math.round(v))) });
                    }}
                  />
                </label>
                <label className="field" style={{ flex: 1 }}>
                  <span>Air Drag</span>
                  <NumberInput
                    value={settings.simAirDrag ?? 0.05}
                    min={0}
                    max={5}
                    step={0.02}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      if (Number.isFinite(v)) patch({ simAirDrag: Math.min(5, Math.max(0, v)), simFabricPreset: "custom" });
                    }}
                  />
                </label>
              </div>

              <div className="field-row" style={{ gap: 6 }}>
                <label className="field" style={{ flex: 1 }}>
                  <span>Wind X</span>
                  <NumberInput
                    value={(settings.simWind ?? [0, 0, 0])[0]}
                    step={0.5}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      if (Number.isFinite(v)) {
                        const w = settings.simWind ?? [0, 0, 0];
                        patch({ simWind: [v, w[1], w[2]] });
                      }
                    }}
                  />
                </label>
                <label className="field" style={{ flex: 1 }}>
                  <span>Wind Y</span>
                  <NumberInput
                    value={(settings.simWind ?? [0, 0, 0])[1]}
                    step={0.5}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      if (Number.isFinite(v)) {
                        const w = settings.simWind ?? [0, 0, 0];
                        patch({ simWind: [w[0], v, w[2]] });
                      }
                    }}
                  />
                </label>
                <label className="field" style={{ flex: 1 }}>
                  <span>Wind Z</span>
                  <NumberInput
                    value={(settings.simWind ?? [0, 0, 0])[2]}
                    step={0.5}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      if (Number.isFinite(v)) {
                        const w = settings.simWind ?? [0, 0, 0];
                        patch({ simWind: [w[0], w[1], v] });
                      }
                    }}
                  />
                </label>
              </div>

              <div className="field-row" style={{ alignItems: "center", gap: 8 }}>
                <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 12, flex: 1 }}>
                  <input
                    type="checkbox"
                    checked={settings.simSelfCollision ?? true}
                    onChange={(e) => patch({ simSelfCollision: e.target.checked })}
                  />
                  <span>Self-Collision</span>
                </label>
                {(settings.simSelfCollision ?? true) && (
                  <label className="field" style={{ width: 96 }}>
                    <span>Thickness (m)</span>
                    <NumberInput
                      value={settings.simSelfThickness ?? 0.004}
                      min={0}
                      max={1}
                      step={0.001}
                      onChange={(e) => {
                        const v = Number(e.target.value);
                        if (Number.isFinite(v)) patch({ simSelfThickness: Math.min(1, Math.max(0, v)) });
                      }}
                    />
                  </label>
                )}
              </div>

              <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 12 }}>
                <input
                  type="checkbox"
                  checked={settings.simPinToCollider ?? true}
                  onChange={(e) => patch({ simPinToCollider: e.target.checked })}
                />
                <span>Pin Masked Vertices to Animated Body</span>
              </label>

              <p className="hint" style={{ margin: 0, lineHeight: 1.4 }}>
                Paint with the <strong>Mask</strong> brush to pin waistbands, collars, or straps. Sculpt Layers stack non-destructively on top of the simulation.
              </p>
            </>
          )}
        </div>
      </details>

      {/* Cloth Physics & Collider Settings */}
      <details
        open
        style={{
          padding: "10px 12px",
          borderRadius: 8,
          border: "1px solid rgba(255,255,255,0.08)",
          background: "rgba(255,255,255,0.02)",
        }}
      >
        <summary style={{ cursor: "pointer", fontWeight: 600, fontSize: 12 }}>
          Cloth Physics &amp; Collider (GPU XPBD)
        </summary>
        <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 10 }}>
          <label className="field">
            <span>Body / Collider Mesh</span>
            <select
              value={
                settings.colliderQuery === "__none__"
                  ? "__none__"
                  : meshes.some((m) => m.path === settings.colliderQuery)
                    ? settings.colliderQuery
                    : "__custom__"
              }
              onChange={(e) => {
                if (e.target.value !== "__custom__") {
                  patch({ colliderQuery: e.target.value });
                }
              }}
            >
              <option value="__none__">None (No Collision)</option>
              {meshes.map((m) => (
                <option key={m.path} value={m.path}>
                  {m.name} ({m.vertexCount.toLocaleString()} verts)
                </option>
              ))}
              {settings.colliderQuery !== "__none__" &&
                !meshes.some((m) => m.path === settings.colliderQuery) && (
                  <option value="__custom__">Custom: {settings.colliderQuery}</option>
                )}
            </select>
          </label>

          <div className="field-row" style={{ gap: 6 }}>
            <label
              className="field"
              style={{ flex: 1 }}
              title="World scale multiplier for gravity, collision margin, and brush radius (e.g. 1.0 = meters, 100 = cm mesh, 0.01 = 100x oversized)"
            >
              <span>Sim Scale</span>
              <NumberInput
                value={settings.clothSimScale ?? 1.0}
                min={0.001}
                max={1000}
                step={0.1}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (Number.isFinite(v) && v > 0) patch({ clothSimScale: Math.min(1000, Math.max(0.001, v)) });
                }}
              />
            </label>
            <label className="field" style={{ flex: 1 }}>
              <span>Patch Falloff ×R</span>
              <NumberInput
                value={settings.clothDragPinFalloff}
                min={1.1}
                max={5}
                step={0.1}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (Number.isFinite(v)) patch({ clothDragPinFalloff: Math.min(5, Math.max(1.1, v)) });
                }}
              />
            </label>
          </div>

          <div className="field-row" style={{ gap: 6 }}>
            <label className="field" style={{ flex: 1 }}>
              <span>Stretch Stiffness</span>
              <NumberInput
                value={settings.clothStretchStiffness}
                min={0}
                max={1}
                step={0.05}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (Number.isFinite(v)) patch({ clothStretchStiffness: Math.min(1, Math.max(0, v)) });
                }}
              />
            </label>
            <label className="field" style={{ flex: 1 }}>
              <span>Bend Stiffness</span>
              <NumberInput
                value={settings.clothBendStiffness}
                min={0}
                max={1}
                step={0.05}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (Number.isFinite(v)) patch({ clothBendStiffness: Math.min(1, Math.max(0, v)) });
                }}
              />
            </label>
          </div>

          <div className="field-row" style={{ gap: 6 }}>
            <label className="field" style={{ flex: 1 }}>
              <span>Collision Margin (m)</span>
              <NumberInput
                value={settings.clothCollisionMargin}
                min={0}
                max={0.2}
                step={0.001}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (Number.isFinite(v)) patch({ clothCollisionMargin: Math.max(0, v) });
                }}
              />
            </label>
            <label className="field" style={{ flex: 1 }}>
              <span>Friction</span>
              <NumberInput
                value={settings.clothFriction}
                min={0}
                max={1}
                step={0.05}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (Number.isFinite(v)) patch({ clothFriction: Math.min(1, Math.max(0, v)) });
                }}
              />
            </label>
          </div>

          <div className="field-row" style={{ gap: 6 }}>
            <label className="field" style={{ flex: 1 }}>
              <span>Gravity (m/s²)</span>
              <NumberInput
                value={settings.clothGravity}
                step={0.5}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (Number.isFinite(v)) patch({ clothGravity: v });
                }}
              />
            </label>
            <label className="field" style={{ flex: 1 }}>
              <span>Damping</span>
              <NumberInput
                value={settings.clothDamping}
                min={0}
                max={0.95}
                step={0.02}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (Number.isFinite(v)) patch({ clothDamping: Math.min(0.95, Math.max(0, v)) });
                }}
              />
            </label>
          </div>

          <div className="field-row" style={{ gap: 6 }}>
            <label className="field" style={{ flex: 1 }}>
              <span>Substeps / Iters</span>
              <div style={{ display: "flex", gap: 4 }}>
                <NumberInput
                  value={settings.clothSubsteps}
                  min={1}
                  max={16}
                  step={1}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isFinite(v)) patch({ clothSubsteps: Math.min(16, Math.max(1, Math.round(v))) });
                  }}
                />
                <NumberInput
                  value={settings.clothIterations}
                  min={1}
                  max={32}
                  step={1}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isFinite(v)) patch({ clothIterations: Math.min(32, Math.max(1, Math.round(v))) });
                  }}
                />
              </div>
            </label>
          </div>
        </div>
      </details>

      {/* Temporal Filter */}
      <details
        style={{
          padding: "10px 12px",
          borderRadius: 8,
          border: "1px solid rgba(255,255,255,0.08)",
          background: "rgba(255,255,255,0.02)",
        }}
      >
        <summary style={{ cursor: "pointer", fontWeight: 600, fontSize: 12 }}>
          Temporal Smooth Filter
        </summary>
        <div className="field-row" style={{ gap: 10, marginTop: 10 }}>
          <label className="field" style={{ flex: 1 }}>
            <span>Window (±frames)</span>
            <NumberInput
              value={settings.temporalSmoothFrames}
              min={0}
              max={15}
              step={1}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v)) patch({ temporalSmoothFrames: Math.min(15, Math.max(0, Math.round(v))) });
              }}
            />
          </label>
          <label className="field" style={{ flex: 1 }}>
            <span>Strength</span>
            <NumberInput
              value={settings.temporalSmoothStrength}
              min={0}
              max={1}
              step={0.05}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v)) patch({ temporalSmoothStrength: Math.min(1, Math.max(0, v)) });
              }}
            />
          </label>
        </div>
      </details>

      {/* Mask summary */}
      {maskCount > 0 && (
        <div
          className="field-row"
          style={{
            padding: "8px 12px",
            borderRadius: 8,
            border: "1px solid rgba(255,255,255,0.08)",
            background: "rgba(255,255,255,0.02)",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <span className="hint">Vertex mask active on {maskCount} mesh(es)</span>
          <button
            type="button"
            className="mini-btn"
            onClick={() => patch({ maskWeights: undefined })}
          >
            Clear Mask
          </button>
        </div>
      )}
    </div>
  );
}
