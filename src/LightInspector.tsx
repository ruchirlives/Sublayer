import { useState, useEffect } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { CameraWorldTransform, LightOverride, StoredProject } from "./fileRegistry";
import {
  DEFAULT_METERS_PER_UNIT,
  effectiveLightScale,
  formatStageUnitLabel,
  linearInputStep,
  normalizeMetersPerUnit,
  presetScaleFactor,
  roundUnitValue,
  scaleLightItemForStage,
  type SceneBounds,
} from "./lightUnits";
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
  translate?: [number, number, number];
  rotate?: [number, number, number];
};

type LightPresetItem = Omit<LightOverride, "id">;

type LightPreset = {
  id: string;
  name: string;
  builtin?: boolean;
  metersPerUnit?: number;
  lights: LightPresetItem[];
};

const CUSTOM_PRESETS_KEY = "sublayer.lightPresets.v1";

const BUILTIN_PRESETS: LightPreset[] = [
  {
    id: "builtin-3point",
    name: "3-Point Studio",
    builtin: true,
    lights: [
      {
        mode: "create",
        lightPath: "/Scene/Lights/KeyLight",
        lightType: "RectLight",
        enabled: true,
        intensity: 12,
        exposure: 0,
        color: [1, 0.96, 0.9],
        enableColorTemperature: true,
        colorTemperature: 5600,
        translate: [3.2, 3.0, 3.2],
        rotate: [-35, 45, 0],
        width: 2.0,
        height: 2.0,
      },
      {
        mode: "create",
        lightPath: "/Scene/Lights/FillLight",
        lightType: "RectLight",
        enabled: true,
        intensity: 4.5,
        exposure: 0,
        color: [0.85, 0.92, 1],
        enableColorTemperature: true,
        colorTemperature: 7200,
        translate: [-3.5, 2.0, 2.5],
        rotate: [-20, -55, 0],
        width: 3.0,
        height: 2.5,
      },
      {
        mode: "create",
        lightPath: "/Scene/Lights/RimLight",
        lightType: "RectLight",
        enabled: true,
        intensity: 16,
        exposure: 0,
        color: [1, 1, 1],
        enableColorTemperature: false,
        colorTemperature: 6500,
        translate: [-1.8, 3.5, -3.5],
        rotate: [-40, -155, 0],
        width: 1.5,
        height: 1.0,
      },
    ],
  },
  {
    id: "builtin-daylight",
    name: "Daylight Sun",
    builtin: true,
    lights: [
      {
        mode: "create",
        lightPath: "/Scene/Lights/Sun",
        lightType: "DistantLight",
        enabled: true,
        intensity: 3.5,
        exposure: 0,
        color: [1, 0.95, 0.88],
        enableColorTemperature: true,
        colorTemperature: 5500,
        translate: [0, 5, 0],
        rotate: [-48, 38, 0],
        angle: 0.53,
      },
      {
        mode: "create",
        lightPath: "/Scene/Lights/SkyFill",
        lightType: "DomeLight",
        enabled: true,
        intensity: 0.45,
        exposure: 0,
        color: [0.68, 0.82, 1],
        enableColorTemperature: false,
        colorTemperature: 6500,
      },
    ],
  },
  {
    id: "builtin-dramatic",
    name: "Dramatic Rim",
    builtin: true,
    lights: [
      {
        mode: "create",
        lightPath: "/Scene/Lights/DramaticKey",
        lightType: "DiskLight",
        enabled: true,
        intensity: 18,
        exposure: 0,
        color: [1, 0.92, 0.82],
        enableColorTemperature: true,
        colorTemperature: 4800,
        translate: [2.8, 2.6, 1.4],
        rotate: [-32, 62, 0],
        radius: 0.8,
      },
      {
        mode: "create",
        lightPath: "/Scene/Lights/EdgeRim",
        lightType: "RectLight",
        enabled: true,
        intensity: 24,
        exposure: 0,
        color: [0.75, 0.88, 1],
        enableColorTemperature: true,
        colorTemperature: 8000,
        translate: [-2.6, 2.8, -2.8],
        rotate: [-30, -135, 0],
        width: 1.0,
        height: 2.2,
      },
    ],
  },
  {
    id: "builtin-softbox",
    name: "Product Softbox",
    builtin: true,
    lights: [
      {
        mode: "create",
        lightPath: "/Scene/Lights/TopSoftbox",
        lightType: "RectLight",
        enabled: true,
        intensity: 10,
        exposure: 0,
        color: [1, 1, 1],
        enableColorTemperature: false,
        colorTemperature: 6500,
        translate: [0, 4.0, 0],
        rotate: [-90, 0, 0],
        width: 3.5,
        height: 3.5,
      },
      {
        mode: "create",
        lightPath: "/Scene/Lights/LeftStrip",
        lightType: "RectLight",
        enabled: true,
        intensity: 7,
        exposure: 0,
        color: [1, 0.98, 0.96],
        enableColorTemperature: false,
        colorTemperature: 6500,
        translate: [-3.5, 1.5, 1.5],
        rotate: [-12, -68, 0],
        width: 1.2,
        height: 3.0,
      },
      {
        mode: "create",
        lightPath: "/Scene/Lights/RightStrip",
        lightType: "RectLight",
        enabled: true,
        intensity: 6,
        exposure: 0,
        color: [0.94, 0.97, 1],
        enableColorTemperature: false,
        colorTemperature: 6500,
        translate: [3.5, 1.5, 1.5],
        rotate: [-12, 68, 0],
        width: 1.2,
        height: 3.0,
      },
    ],
  },
];

const LIGHT_TYPES = [
  { value: "RectLight", label: "Rect", short: "Rect" },
  { value: "DistantLight", label: "Distant", short: "Dist" },
  { value: "DiskLight", label: "Disk", short: "Disk" },
  { value: "SphereLight", label: "Sphere", short: "Sph" },
  { value: "CylinderLight", label: "Cylinder", short: "Cyl" },
  { value: "DomeLight", label: "Dome", short: "Dome" },
] as const;

function loadCustomPresets(): LightPreset[] {
  try {
    const raw = localStorage.getItem(CUSTOM_PRESETS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((p) => p && typeof p.id === "string" && typeof p.name === "string" && Array.isArray(p.lights));
  } catch {
    return [];
  }
}

function saveCustomPresets(presets: LightPreset[]) {
  try {
    localStorage.setItem(CUSTOM_PRESETS_KEY, JSON.stringify(presets));
  } catch {
    // Ignore storage quota errors
  }
}

function nextUniqueLightPath(list: LightOverride[], discovered: DiscoveredLight[], base = "/Scene/Lights/Light"): string {
  const used = new Set<string>([
    ...list.map((l) => l.lightPath),
    ...discovered.map((l) => l.path),
  ]);
  for (let i = 1; i < 1000; i++) {
    const candidate = `${base}${i}`;
    if (!used.has(candidate)) return candidate;
  }
  return `${base}${Date.now()}`;
}

function rgbToHex(c?: [number, number, number] | null): string {
  const col = c || [1, 1, 1];
  const toHex = (v: number) => Math.max(0, Math.min(255, Math.round(v * 255))).toString(16).padStart(2, "0");
  return `#${toHex(col[0])}${toHex(col[1])}${toHex(col[2])}`;
}

function shortTypeLabel(t?: string): string {
  const found = LIGHT_TYPES.find((lt) => lt.value === t);
  return found ? found.short : (t || "Light").replace("Light", "");
}

export function LightInspector({
  project,
  nodeId,
  overrides,
  globalScale = 1,
  intensityScale = 1,
  onScalesChange,
  getViewportCameraTransform,
  onStatus,
  onChange,
}: {
  project: StoredProject;
  nodeId: string;
  overrides?: LightOverride[];
  globalScale?: number;
  intensityScale?: number;
  onScalesChange?: (patch: { scale?: number; lightIntensityScale?: number }) => void;
  getViewportCameraTransform?: () => CameraWorldTransform | null;
  onStatus?: (message: string) => void;
  onChange: (overrides: LightOverride[]) => void;
}) {
  const [discoveredLights, setDiscoveredLights] = useState<DiscoveredLight[]>([]);
  const [stageMetersPerUnit, setStageMetersPerUnit] = useState<number>(DEFAULT_METERS_PER_UNIT);
  const [stageUpAxis, setStageUpAxis] = useState<string>("Y");
  const [sceneBounds, setSceneBounds] = useState<SceneBounds | null>(null);
  const [loading, setLoading] = useState(false);
  const [expandedIds, setExpandedIds] = useState<Record<string, boolean>>({});
  const [customPresets, setCustomPresets] = useState<LightPreset[]>(() => loadCustomPresets());
  const [selectedPresetId, setSelectedPresetId] = useState<string>(BUILTIN_PRESETS[0].id);
  const [savingPreset, setSavingPreset] = useState(false);
  const [presetNameDraft, setPresetNameDraft] = useState("");

  useEffect(() => {
    let active = true;
    async function loadLights() {
      setLoading(true);
      try {
        const res = (await invoke("inspect_stage_lights", {
          project,
          nodeId,
        })) as { lights: DiscoveredLight[]; metersPerUnit?: number; upAxis?: string; sceneBounds?: SceneBounds | null };
        if (active && res) {
          if (res.lights) setDiscoveredLights(res.lights);
          if (typeof res.metersPerUnit === "number" && Number.isFinite(res.metersPerUnit) && res.metersPerUnit > 0) {
            setStageMetersPerUnit(res.metersPerUnit);
          }
          if (typeof res.upAxis === "string" && res.upAxis) {
            setStageUpAxis(res.upAxis);
          }
          setSceneBounds(res.sceneBounds ?? null);
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

  const unitScale = effectiveLightScale(stageMetersPerUnit, sceneBounds);
  const linearStep = linearInputStep(stageMetersPerUnit, sceneBounds);
  const unitLabel = formatStageUnitLabel(stageMetersPerUnit);

  const list = overrides || [];
  const allPresets = [...BUILTIN_PRESETS, ...customPresets];
  const activePreset = allPresets.find((p) => p.id === selectedPresetId) || allPresets[0];

  function toggleExpanded(id: string) {
    setExpandedIds((prev) => ({ ...prev, [id]: !prev[id] }));
  }

  function addLightOverride(light?: DiscoveredLight) {
    const id = crypto.randomUUID();
    const newOverride: LightOverride = {
      id,
      mode: "override",
      lightPath: light?.path || discoveredLights[0]?.path || "/Scene/KeyLight",
      lightType: light ? light.type : "DistantLight",
      enabled: true,
      intensity: light ? light.intensity : 1.0,
      exposure: light ? light.exposure : 0.0,
      color: light ? light.color : [1.0, 1.0, 1.0],
      enableColorTemperature: light ? light.enableColorTemperature : false,
      colorTemperature: light ? light.colorTemperature : 6500.0,
    };
    setExpandedIds((prev) => ({ ...prev, [id]: true }));
    onChange([...list, newOverride]);
  }

  function createNewLight(fromView = false) {
    let translate: [number, number, number] = sceneBounds
      ? [
          roundUnitValue(sceneBounds.center[0]),
          roundUnitValue(sceneBounds.center[1] + 1.0 * unitScale),
          roundUnitValue(sceneBounds.center[2] + 2.0 * unitScale),
        ]
      : [0, roundUnitValue(2 * unitScale), roundUnitValue(2 * unitScale)];
    let rotate: [number, number, number] = [-35, 0, 0];
    const activeGlobalScale = typeof globalScale === "number" && Number.isFinite(globalScale) && globalScale > 0 ? globalScale : 1;
    if (fromView) {
      const cam = getViewportCameraTransform?.();
      if (!cam) {
        onStatus?.("Render a viewport frame first to create a light from the camera view.");
        return;
      }
      translate = [
        roundUnitValue(cam.translate[0] / activeGlobalScale),
        roundUnitValue(cam.translate[1] / activeGlobalScale),
        roundUnitValue(cam.translate[2] / activeGlobalScale),
      ];
      rotate = [...cam.rotate];
    }
    const id = crypto.randomUUID();
    const path = nextUniqueLightPath(list, discoveredLights, fromView ? "/Scene/Lights/ViewLight" : "/Scene/Lights/Light");
    const defaultSize = roundUnitValue(1.0 * unitScale);
    const created: LightOverride = {
      id,
      mode: "create",
      lightPath: path,
      lightType: "RectLight",
      enabled: true,
      intensity: 10.0,
      exposure: 0.0,
      color: [1.0, 1.0, 1.0],
      enableColorTemperature: false,
      colorTemperature: 6500.0,
      translate,
      rotate,
      width: defaultSize,
      height: defaultSize,
    };
    setExpandedIds((prev) => ({ ...prev, [id]: true }));
    onChange([...list, created]);
    if (fromView) {
      onStatus?.(`Created ${path.split("/").pop()} from viewport camera (${unitLabel})`);
    }
  }

  function duplicateItem(item: LightOverride) {
    const id = crypto.randomUUID();
    const base = item.lightPath.replace(/\d+$/, "") || "/Scene/Lights/Light";
    const copy: LightOverride = {
      ...item,
      id,
      mode: "create",
      lightPath: nextUniqueLightPath(list, discoveredLights, base),
      color: item.color ? [...item.color] : [1, 1, 1],
      translate: item.translate ? [...item.translate] : undefined,
      rotate: item.rotate ? [...item.rotate] : undefined,
      scale: item.scale ? [...item.scale] : undefined,
    };
    setExpandedIds((prev) => ({ ...prev, [id]: true }));
    onChange([...list, copy]);
  }

  function matchItemToView(item: LightOverride) {
    const cam = getViewportCameraTransform?.();
    if (!cam) {
      onStatus?.("Render a viewport frame first to match the camera view.");
      return;
    }
    const activeGlobalScale = typeof globalScale === "number" && Number.isFinite(globalScale) && globalScale > 0 ? globalScale : 1;
    updateOverride(item.id, {
      translate: [
        roundUnitValue(cam.translate[0] / activeGlobalScale),
        roundUnitValue(cam.translate[1] / activeGlobalScale),
        roundUnitValue(cam.translate[2] / activeGlobalScale),
      ],
      rotate: [...cam.rotate],
    });
    onStatus?.(`Matched ${item.lightPath.split("/").pop() || "light"} to viewport camera`);
  }

  function updateOverride(id: string, patch: Partial<LightOverride>) {
    onChange(list.map((o) => (o.id === id ? { ...o, ...patch } : o)));
  }

  function removeOverride(id: string) {
    onChange(list.filter((o) => o.id !== id));
  }

  function applyPreset(mode: "replace" | "append") {
    if (!activePreset) return;
    const baseList = mode === "append" ? [...list] : [];
    const factor = presetScaleFactor(activePreset.metersPerUnit, stageMetersPerUnit, activePreset.builtin, sceneBounds);
    const added: LightOverride[] = [];
    for (const rawItem of activePreset.lights) {
      const item = scaleLightItemForStage(rawItem, factor, sceneBounds, Boolean(activePreset.builtin));
      const path =
        mode === "append" && baseList.some((l) => l.lightPath === item.lightPath)
          ? nextUniqueLightPath([...baseList, ...added], discoveredLights, item.lightPath)
          : item.lightPath;
      added.push({
        ...item,
        id: crypto.randomUUID(),
        lightPath: path,
        color: item.color ? ([...item.color] as [number, number, number]) : [1, 1, 1],
        translate: item.translate ? ([...item.translate] as [number, number, number]) : undefined,
        rotate: item.rotate ? ([...item.rotate] as [number, number, number]) : undefined,
        scale: item.scale ? ([...item.scale] as [number, number, number]) : undefined,
      });
    }
    onChange([...baseList, ...added]);
    onStatus?.(`${mode === "append" ? "Appended" : "Applied"} "${activePreset.name}" preset (${added.length} lights, ${unitLabel})`);
  }

  function commitSavePreset() {
    const trimmed = presetNameDraft.trim();
    if (!trimmed || list.length === 0) return;
    const newPreset: LightPreset = {
      id: `custom-${crypto.randomUUID()}`,
      name: trimmed,
      metersPerUnit: normalizeMetersPerUnit(stageMetersPerUnit),
      lights: list.map(({ id: _id, ...rest }) => rest),
    };
    const next = [...customPresets, newPreset];
    setCustomPresets(next);
    saveCustomPresets(next);
    setSelectedPresetId(newPreset.id);
    setPresetNameDraft("");
    setSavingPreset(false);
    onStatus?.(`Saved light preset "${trimmed}"`);
  }

  function deleteSelectedPreset() {
    if (!activePreset || activePreset.builtin) return;
    const next = customPresets.filter((p) => p.id !== activePreset.id);
    setCustomPresets(next);
    saveCustomPresets(next);
    setSelectedPresetId(BUILTIN_PRESETS[0].id);
    onStatus?.(`Deleted light preset "${activePreset.name}"`);
  }

  const compactInputStyle = { width: "100%", padding: "2px 4px", fontSize: "10px" };
  const compactLabelStyle = { margin: 0, fontSize: "10px", color: "#9eb6ab" };

  return (
    <div className="node-options light-inspector" style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
      {/* Action bar */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "4px", alignItems: "center" }}>
        <button type="button" onClick={() => createNewLight(false)} style={{ fontSize: "10px", padding: "3px 6px" }}>
          ＋ New
        </button>
        <button
          type="button"
          onClick={() => createNewLight(true)}
          title="Create a new light at the current viewport camera position, shining forward"
          style={{ fontSize: "10px", padding: "3px 6px" }}
        >
          From View
        </button>
        <button type="button" onClick={() => addLightOverride()} style={{ fontSize: "10px", padding: "3px 6px" }}>
          ＋ Override
        </button>
        <span
          title={
            sceneBounds
              ? `Stage: ${stageMetersPerUnit} m/unit (${stageUpAxis}-up) · Scene radius: ${Number(sceneBounds.radius.toPrecision(3))} ${unitLabel}. New lights and built-in presets scale automatically.`
              : `Stage: ${stageMetersPerUnit} m/unit (${stageUpAxis}-up). New lights and presets scale automatically.`
          }
          style={{
            marginLeft: "auto",
            fontSize: "9px",
            padding: "2px 5px",
            borderRadius: "3px",
            background: "#16262c",
            border: "1px solid #2a3e46",
            color: "#9eb6ab",
          }}
        >
          {sceneBounds ? `${unitLabel} · r=${Number(sceneBounds.radius.toPrecision(2))}` : unitLabel}
        </span>
      </div>

      {/* Global Spatial & Intensity Scale (Storm / RTX switcher) */}
      {onScalesChange && (() => {
        const currentPosScale = typeof globalScale === "number" && Number.isFinite(globalScale) && globalScale > 0 ? globalScale : 1;
        const currentIntScale = typeof intensityScale === "number" && Number.isFinite(intensityScale) && intensityScale > 0 ? intensityScale : 1;
        const presets = [
          { label: "Storm", pos: 1, intensity: 1, title: "Storm defaults (Pos/Size 1x, Intensity 1x)" },
          { label: "RTX", pos: 100, intensity: 250, title: "Omniverse RTX defaults (Pos/Size 100x, Intensity 250x)" },
        ];
        return (
          <div
            style={{
              background: "#121d22",
              border: "1px solid #22333b",
              borderRadius: "5px",
              padding: "5px 6px",
              display: "flex",
              flexDirection: "column",
              gap: "4px",
            }}
            title="Independent non-destructive multipliers for light position/size and lumen intensity. Use Storm or RTX presets when switching renderers."
          >
            <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
              <span style={{ fontSize: "10px", color: "#9eb6ab", flex: 1 }}>Global Scale</span>
              {presets.map((p) => {
                const active =
                  Math.abs(currentPosScale - p.pos) < 1e-6 &&
                  Math.abs(currentIntScale - p.intensity) < 1e-6;
                return (
                  <button
                    key={p.label}
                    type="button"
                    title={p.title}
                    onClick={() => {
                      onScalesChange({ scale: p.pos, lightIntensityScale: p.intensity });
                      onStatus?.(`Light scale set to ${p.label} (Pos ${p.pos}x, Int ${p.intensity}x)`);
                    }}
                    style={{
                      fontSize: "10px",
                      padding: "2px 6px",
                      ...(active
                        ? {
                            background: "#1f4a3c",
                            borderColor: "#9fe2b6",
                            color: "#d9f7e6",
                          }
                        : {}),
                    }}
                  >
                    {p.label}
                  </button>
                );
              })}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px" }}>
              <label style={{ margin: 0, fontSize: "9px", color: "#8b9c96" }}>
                Pos / Size ×
                <NumberInput
                  aria-label="Global Light Position Scale"
                  min="0.0001"
                  step="any"
                  value={currentPosScale}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isFinite(v) && v > 0) onScalesChange({ scale: v });
                  }}
                  style={{ width: "100%", padding: "2px 4px", fontSize: "10px", marginTop: "2px" }}
                />
              </label>
              <label style={{ margin: 0, fontSize: "9px", color: "#8b9c96" }}>
                Intensity ×
                <NumberInput
                  aria-label="Global Light Intensity Scale"
                  min="0.0001"
                  step="any"
                  value={currentIntScale}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isFinite(v) && v > 0) onScalesChange({ lightIntensityScale: v });
                  }}
                  style={{ width: "100%", padding: "2px 4px", fontSize: "10px", marginTop: "2px" }}
                />
              </label>
            </div>
          </div>
        );
      })()}

      {/* Preset bar */}
      <div
        style={{
          background: "#121d22",
          border: "1px solid #22333b",
          borderRadius: "5px",
          padding: "5px 6px",
          display: "flex",
          flexDirection: "column",
          gap: "4px",
        }}
      >
        <div style={{ display: "flex", gap: "4px", alignItems: "center" }}>
          <select
            aria-label="Light Preset"
            value={activePreset?.id || ""}
            onChange={(e) => setSelectedPresetId(e.target.value)}
            style={{ flex: 1, padding: "2px 4px", fontSize: "10px", minWidth: 0 }}
          >
            <optgroup label="Built-in Presets">
              {BUILTIN_PRESETS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.lights.length})
                </option>
              ))}
            </optgroup>
            {customPresets.length > 0 && (
              <optgroup label="Custom Presets">
                {customPresets.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.lights.length})
                  </option>
                ))}
              </optgroup>
            )}
          </select>
          <button
            type="button"
            title="Replace current lights with preset"
            onClick={() => applyPreset("replace")}
            style={{ fontSize: "10px", padding: "2px 6px" }}
          >
            Apply
          </button>
          <button
            type="button"
            title="Append preset lights to current list"
            onClick={() => applyPreset("append")}
            style={{ fontSize: "10px", padding: "2px 5px" }}
          >
            ＋
          </button>
          {activePreset && !activePreset.builtin && (
            <button
              type="button"
              className="danger"
              title="Delete custom preset"
              onClick={deleteSelectedPreset}
              style={{ fontSize: "10px", padding: "2px 5px" }}
            >
              ✕
            </button>
          )}
          <button
            type="button"
            disabled={list.length === 0}
            title="Save current lights as a custom preset"
            onClick={() => {
              setSavingPreset((v) => !v);
              setPresetNameDraft("");
            }}
            style={{ fontSize: "10px", padding: "2px 5px" }}
          >
            Save
          </button>
        </div>

        {savingPreset && (
          <div style={{ display: "flex", gap: "4px", alignItems: "center" }}>
            <input
              type="text"
              placeholder="Preset name..."
              value={presetNameDraft}
              onChange={(e) => setPresetNameDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") commitSavePreset();
                if (e.key === "Escape") setSavingPreset(false);
              }}
              style={{ flex: 1, padding: "2px 5px", fontSize: "10px" }}
              autoFocus
            />
            <button
              type="button"
              disabled={!presetNameDraft.trim()}
              onClick={commitSavePreset}
              style={{ fontSize: "10px", padding: "2px 6px" }}
            >
              OK
            </button>
          </div>
        )}
      </div>

      {loading && <small style={{ color: "#9eb6ab", fontSize: "10px" }}>Discovering lights...</small>}

      {discoveredLights.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "4px", alignItems: "center" }}>
          <span style={{ fontSize: "10px", color: "#8b9c96" }}>Upstream:</span>
          {discoveredLights.map((l) => (
            <button
              key={l.path}
              type="button"
              onClick={() => addLightOverride(l)}
              style={{ fontSize: "10px", padding: "1px 5px" }}
            >
              ＋ {l.name}
            </button>
          ))}
        </div>
      )}

      {list.length === 0 ? (
        <p style={{ color: "#8b9c96", fontSize: "10px", margin: "2px 0" }}>
          No lights configured. Apply a preset, create a light, or override an upstream light.
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
          {list.map((item) => {
            const isCreated =
              item.mode === "create" ||
              (!item.mode && !discoveredLights.some((l) => l.path === item.lightPath));
            const lightType = item.lightType || "DistantLight";
            const hasTransform = Boolean(item.translate || item.rotate);
            const isExpanded = Boolean(expandedIds[item.id]);
            const shortName = item.lightPath.split("/").filter(Boolean).pop() || item.lightPath;

            return (
              <div
                key={item.id}
                style={{
                  background: "#162329",
                  border: "1px solid #263840",
                  borderRadius: "5px",
                  overflow: "hidden",
                }}
              >
                {/* Compact single-row header */}
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "4px",
                    padding: "4px 6px",
                    background: isExpanded ? "#1a2a31" : "transparent",
                  }}
                >
                  <button
                    type="button"
                    onClick={() => toggleExpanded(item.id)}
                    title={isExpanded ? "Collapse" : "Expand"}
                    style={{
                      background: "none",
                      border: "none",
                      padding: "0 2px",
                      fontSize: "10px",
                      color: "#9eb6ab",
                      cursor: "pointer",
                      minWidth: "14px",
                    }}
                  >
                    {isExpanded ? "▾" : "▸"}
                  </button>
                  <input
                    type="checkbox"
                    checked={item.enabled}
                    title="Enable / Disable light"
                    onChange={(e) => updateOverride(item.id, { enabled: e.target.checked })}
                    style={{ margin: 0, cursor: "pointer" }}
                  />
                  <span
                    style={{
                      width: "9px",
                      height: "9px",
                      borderRadius: "50%",
                      background: rgbToHex(item.color),
                      border: "1px solid rgba(255,255,255,0.25)",
                      flexShrink: 0,
                    }}
                  />
                  <button
                    type="button"
                    onClick={() => toggleExpanded(item.id)}
                    style={{
                      flex: 1,
                      textAlign: "left",
                      background: "none",
                      border: "none",
                      padding: 0,
                      fontSize: "11px",
                      color: item.enabled ? "#e4f0ec" : "#7e908a",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      cursor: "pointer",
                    }}
                    title={item.lightPath}
                  >
                    {shortName}
                  </button>
                  <span
                    style={{
                      fontSize: "9px",
                      padding: "0 4px",
                      borderRadius: "3px",
                      background: isCreated ? "#1f4a3c" : "#2b3b4e",
                      color: isCreated ? "#9ef0d0" : "#b8d4f4",
                    }}
                  >
                    {shortTypeLabel(lightType)}
                  </span>
                  <button
                    type="button"
                    title="Match viewport camera"
                    style={{ padding: "1px 4px", fontSize: "9px" }}
                    onClick={() => matchItemToView(item)}
                  >
                    View
                  </button>
                  <button
                    type="button"
                    title="Duplicate light"
                    style={{ padding: "1px 4px", fontSize: "9px" }}
                    onClick={() => duplicateItem(item)}
                  >
                    ⧉
                  </button>
                  <button
                    type="button"
                    className="danger"
                    title="Delete light"
                    style={{ padding: "1px 4px", fontSize: "9px" }}
                    onClick={() => removeOverride(item.id)}
                  >
                    ✕
                  </button>
                </div>

                {/* Compact expanded body */}
                {isExpanded && (
                  <div
                    style={{
                      padding: "6px",
                      borderTop: "1px solid #22333b",
                      display: "flex",
                      flexDirection: "column",
                      gap: "5px",
                    }}
                  >
                    {/* Path & Type */}
                    <div style={{ display: "flex", gap: "5px" }}>
                      <label style={{ ...compactLabelStyle, flex: 2 }}>
                        {isCreated ? "Path" : "Target"}
                        {!isCreated && discoveredLights.length > 0 ? (
                          <select
                            value={item.lightPath}
                            onChange={(e) => {
                              const found = discoveredLights.find((l) => l.path === e.target.value);
                              updateOverride(item.id, {
                                lightPath: e.target.value,
                                ...(found
                                  ? {
                                      lightType: found.type,
                                      intensity: found.intensity,
                                      exposure: found.exposure,
                                      color: found.color,
                                    }
                                  : {}),
                              });
                            }}
                            style={{ ...compactInputStyle, marginTop: "2px" }}
                          >
                            {discoveredLights.map((l) => (
                              <option key={l.path} value={l.path}>
                                {l.name} ({l.type})
                              </option>
                            ))}
                            {!discoveredLights.some((l) => l.path === item.lightPath) && (
                              <option value={item.lightPath}>{item.lightPath}</option>
                            )}
                          </select>
                        ) : (
                          <input
                            type="text"
                            value={item.lightPath}
                            placeholder="/Scene/Lights/KeyLight"
                            onChange={(e) => updateOverride(item.id, { lightPath: e.target.value })}
                            style={{ ...compactInputStyle, marginTop: "2px" }}
                          />
                        )}
                      </label>

                      {isCreated && (
                        <label style={{ ...compactLabelStyle, flex: 1 }}>
                          Type
                          <select
                            value={lightType}
                            onChange={(e) => {
                              const nextType = e.target.value;
                              const patch: Partial<LightOverride> = { lightType: nextType };
                              if (nextType === "RectLight") {
                                if (item.width === undefined) patch.width = roundUnitValue(1.0 * unitScale);
                                if (item.height === undefined) patch.height = roundUnitValue(1.0 * unitScale);
                              } else if (nextType === "SphereLight" || nextType === "DiskLight" || nextType === "CylinderLight") {
                                if (item.radius === undefined) patch.radius = roundUnitValue(0.5 * unitScale);
                              } else if (nextType === "DistantLight") {
                                if (item.angle === undefined) patch.angle = 0.53;
                              }
                              updateOverride(item.id, patch);
                            }}
                            style={{ ...compactInputStyle, marginTop: "2px" }}
                          >
                            {LIGHT_TYPES.map((t) => (
                              <option key={t.value} value={t.value}>
                                {t.label}
                              </option>
                            ))}
                          </select>
                        </label>
                      )}
                    </div>

                    {/* Intensity, Exposure, and Shape in one compact row */}
                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns:
                          lightType === "RectLight"
                            ? "1fr 1fr 1fr 1fr"
                            : lightType === "DomeLight"
                              ? "1fr 1fr"
                              : "1fr 1fr 1fr",
                        gap: "4px",
                      }}
                    >
                      <label style={compactLabelStyle}>
                        Int
                        <NumberInput
                          step="any"
                          value={item.intensity ?? 1.0}
                          onChange={(e) => updateOverride(item.id, { intensity: Number(e.target.value) })}
                          style={{ ...compactInputStyle, marginTop: "2px" }}
                        />
                      </label>
                      <label style={compactLabelStyle}>
                        EV
                        <NumberInput
                          step="0.1"
                          value={item.exposure ?? 0.0}
                          onChange={(e) => updateOverride(item.id, { exposure: Number(e.target.value) })}
                          style={{ ...compactInputStyle, marginTop: "2px" }}
                        />
                      </label>
                      {lightType === "RectLight" && (
                        <>
                          <label style={compactLabelStyle}>
                            W ({unitLabel})
                            <NumberInput
                              step={linearStep}
                              min="0.001"
                              value={item.width ?? roundUnitValue(1.0 * unitScale)}
                              onChange={(e) => updateOverride(item.id, { width: Number(e.target.value) })}
                              style={{ ...compactInputStyle, marginTop: "2px" }}
                            />
                          </label>
                          <label style={compactLabelStyle}>
                            H ({unitLabel})
                            <NumberInput
                              step={linearStep}
                              min="0.001"
                              value={item.height ?? roundUnitValue(1.0 * unitScale)}
                              onChange={(e) => updateOverride(item.id, { height: Number(e.target.value) })}
                              style={{ ...compactInputStyle, marginTop: "2px" }}
                            />
                          </label>
                        </>
                      )}
                      {(lightType === "SphereLight" || lightType === "DiskLight" || lightType === "CylinderLight") && (
                        <label style={compactLabelStyle}>
                          Rad ({unitLabel})
                          <NumberInput
                            step={linearStep}
                            min="0.001"
                            value={item.radius ?? roundUnitValue(0.5 * unitScale)}
                            onChange={(e) => updateOverride(item.id, { radius: Number(e.target.value) })}
                            style={{ ...compactInputStyle, marginTop: "2px" }}
                          />
                        </label>
                      )}
                      {lightType === "DistantLight" && (
                        <label style={compactLabelStyle}>
                          Ang
                          <NumberInput
                            step="0.1"
                            min="0"
                            value={item.angle ?? 0.53}
                            onChange={(e) => updateOverride(item.id, { angle: Number(e.target.value) })}
                            style={{ ...compactInputStyle, marginTop: "2px" }}
                          />
                        </label>
                      )}
                    </div>

                    {/* Color + Kelvin inline */}
                    <div style={{ display: "flex", gap: "4px", alignItems: "center" }}>
                      <input
                        type="color"
                        aria-label="Light Color Picker"
                        value={rgbToHex(item.color)}
                        onChange={(e) => {
                          const hex = e.target.value;
                          const r = parseInt(hex.slice(1, 3), 16) / 255;
                          const g = parseInt(hex.slice(3, 5), 16) / 255;
                          const b = parseInt(hex.slice(5, 7), 16) / 255;
                          updateOverride(item.id, {
                            color: [Number(r.toFixed(3)), Number(g.toFixed(3)), Number(b.toFixed(3))],
                          });
                        }}
                        style={{ width: "22px", height: "20px", padding: 0, border: "none", cursor: "pointer", background: "none", flexShrink: 0 }}
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
                          style={{ ...compactInputStyle, width: "44px" }}
                        />
                      ))}
                      <label style={{ display: "flex", alignItems: "center", gap: "3px", margin: 0, fontSize: "10px", cursor: "pointer", whiteSpace: "nowrap" }}>
                        <input
                          type="checkbox"
                          checked={Boolean(item.enableColorTemperature)}
                          onChange={(e) => updateOverride(item.id, { enableColorTemperature: e.target.checked })}
                          style={{ margin: 0 }}
                        />
                        K
                      </label>
                      {item.enableColorTemperature && (
                        <NumberInput
                          step="100"
                          min="1000"
                          max="20000"
                          value={item.colorTemperature ?? 6500}
                          onChange={(e) => updateOverride(item.id, { colorTemperature: Number(e.target.value) })}
                          style={{ ...compactInputStyle, width: "54px" }}
                        />
                      )}
                    </div>

                    {/* Transform */}
                    {isCreated || hasTransform ? (
                      <div style={{ display: "flex", flexDirection: "column", gap: "3px", borderTop: "1px solid #22333b", paddingTop: "4px" }}>
                        <div style={{ display: "grid", gridTemplateColumns: "14px 1fr 1fr 1fr", gap: "3px", alignItems: "center" }}>
                          <span style={{ fontSize: "9px", color: "#8b9c96" }}>T</span>
                          {[0, 1, 2].map((idx) => (
                            <NumberInput
                              key={idx}
                              step={linearStep}
                              value={(item.translate || [0, 0, 0])[idx]}
                              onChange={(e) => {
                                const next = [...(item.translate || [0, 0, 0])] as [number, number, number];
                                next[idx] = Number(e.target.value);
                                updateOverride(item.id, { translate: next });
                              }}
                              style={compactInputStyle}
                            />
                          ))}
                        </div>
                        <div style={{ display: "grid", gridTemplateColumns: "14px 1fr 1fr 1fr", gap: "3px", alignItems: "center" }}>
                          <span style={{ fontSize: "9px", color: "#8b9c96" }}>R</span>
                          {[0, 1, 2].map((idx) => (
                            <NumberInput
                              key={idx}
                              step="1"
                              value={(item.rotate || [0, 0, 0])[idx]}
                              onChange={(e) => {
                                const next = [...(item.rotate || [0, 0, 0])] as [number, number, number];
                                next[idx] = Number(e.target.value);
                                updateOverride(item.id, { rotate: next });
                              }}
                              style={compactInputStyle}
                            />
                          ))}
                        </div>
                        {!isCreated && (
                          <button
                            type="button"
                            style={{ alignSelf: "flex-end", fontSize: "9px", padding: "1px 5px" }}
                            onClick={() => updateOverride(item.id, { translate: undefined, rotate: undefined, scale: undefined })}
                          >
                            Reset Transform
                          </button>
                        )}
                      </div>
                    ) : (
                      <div style={{ display: "flex", justifyContent: "flex-end", borderTop: "1px solid #22333b", paddingTop: "4px" }}>
                        <button
                          type="button"
                          style={{ fontSize: "9px", padding: "1px 5px" }}
                          onClick={() => {
                            const found = discoveredLights.find((l) => l.path === item.lightPath);
                            updateOverride(item.id, {
                              translate: found?.translate ? [...found.translate] : [0, 0, 0],
                              rotate: found?.rotate ? [...found.rotate] : [0, 0, 0],
                            });
                          }}
                        >
                          Override Transform
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
