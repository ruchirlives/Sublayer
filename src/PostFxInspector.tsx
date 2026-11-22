import { useState } from "react";
import { defaultPostFx, type PostFxSettings } from "./fileRegistry";
import { NumberInput } from "./NumberInput";

const TONEMAP_OPERATORS: { value: PostFxSettings["tonemapOperator"]; label: string }[] = [
  { value: "acesApproximation", label: "ACES (Approx)" },
  { value: "hableUc2", label: "Hable (Filmic)" },
  { value: "hejiHableAlu", label: "Heji-Hable ALU" },
  { value: "reinhard", label: "Reinhard" },
  { value: "modifiedReinhard", label: "Modified Reinhard" },
  { value: "iray", label: "Iray" },
  { value: "none", label: "None (Clamp)" },
  { value: "raw", label: "Raw" },
];

const compactInput = { width: "100%", padding: "2px 4px", fontSize: "10px" };
const compactLabel = { margin: 0, fontSize: "10px", color: "#9eb6ab" };

function RgbControl({
  label,
  value,
  step = "0.05",
  min,
  max,
  onChange,
}: {
  label: string;
  value: [number, number, number];
  step?: string;
  min?: string;
  max?: string;
  onChange: (next: [number, number, number]) => void;
}) {
  const [splitRgb, setSplitRgb] = useState(
    () => Math.abs(value[0] - value[1]) > 1e-4 || Math.abs(value[1] - value[2]) > 1e-4,
  );
  const avg = Number(((value[0] + value[1] + value[2]) / 3).toFixed(4));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
        <span style={{ fontSize: "10px", color: "#9eb6ab", flex: 1 }}>{label}</span>
        {!splitRgb && (
          <NumberInput
            step={step}
            min={min}
            max={max}
            value={avg}
            onChange={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v)) onChange([v, v, v]);
            }}
            style={{ ...compactInput, width: "68px" }}
          />
        )}
        <button
          type="button"
          title={splitRgb ? "Switch to uniform scalar" : "Edit R / G / B channels separately"}
          onClick={() => setSplitRgb((s) => !s)}
          style={{
            fontSize: "9px",
            padding: "1px 4px",
            color: splitRgb ? "#c084fc" : "#8b9c96",
            borderColor: splitRgb ? "#7e57c2" : undefined,
          }}
        >
          RGB
        </button>
      </div>
      {splitRgb && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "3px" }}>
          {(["R", "G", "B"] as const).map((ch, idx) => (
            <label key={ch} style={{ ...compactLabel, display: "flex", alignItems: "center", gap: "2px" }}>
              <span style={{ fontSize: "9px", color: idx === 0 ? "#f87171" : idx === 1 ? "#4ade80" : "#60a5fa" }}>{ch}</span>
              <NumberInput
                step={step}
                min={min}
                max={max}
                value={value[idx]}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (!Number.isFinite(v)) return;
                  const next: [number, number, number] = [...value];
                  next[idx] = v;
                  onChange(next);
                }}
                style={compactInput}
              />
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

function Section({
  title,
  open,
  onToggleOpen,
  enabled,
  onToggleEnabled,
  children,
}: {
  title: string;
  open: boolean;
  onToggleOpen: () => void;
  enabled?: boolean;
  onToggleEnabled?: (checked: boolean) => void;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        background: "#162329",
        border: "1px solid #263840",
        borderRadius: "5px",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "5px",
          padding: "4px 6px",
          background: open ? "#1a2a31" : "transparent",
        }}
      >
        <button
          type="button"
          onClick={onToggleOpen}
          style={{
            background: "none",
            border: "none",
            padding: "0 2px",
            fontSize: "10px",
            color: "#9eb6ab",
            cursor: "pointer",
            minWidth: "12px",
          }}
        >
          {open ? "▾" : "▸"}
        </button>
        {onToggleEnabled !== undefined && (
          <input
            type="checkbox"
            checked={Boolean(enabled)}
            onChange={(e) => {
              onToggleEnabled(e.target.checked);
              if (e.target.checked && !open) onToggleOpen();
            }}
            style={{ margin: 0, cursor: "pointer" }}
          />
        )}
        <button
          type="button"
          onClick={onToggleOpen}
          style={{
            flex: 1,
            textAlign: "left",
            background: "none",
            border: "none",
            padding: 0,
            fontSize: "11px",
            fontWeight: 500,
            color: enabled === false ? "#7e908a" : "#e4f0ec",
            cursor: "pointer",
          }}
        >
          {title}
        </button>
      </div>
      {open && (
        <div
          style={{
            padding: "6px",
            borderTop: "1px solid #22333b",
            display: "flex",
            flexDirection: "column",
            gap: "5px",
          }}
        >
          {children}
        </div>
      )}
    </div>
  );
}

export function PostFxInspector({
  value,
  onChange,
}: {
  value?: PostFxSettings;
  onChange: (next: PostFxSettings) => void;
}) {
  const cfg: PostFxSettings = { ...defaultPostFx, ...(value || {}) };
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({
    tonemap: true,
    grade: Boolean(cfg.gradeEnabled),
    autoExposure: Boolean(cfg.autoExposureEnabled),
    bloom: Boolean(cfg.bloomEnabled),
    chromab: Boolean(cfg.chromabEnabled),
    vignetteGrain: Boolean(cfg.vignetteEnabled || cfg.filmGrainEnabled),
  });

  const patch = (partial: Partial<PostFxSettings>) => onChange({ ...cfg, ...partial });
  const toggle = (key: string) => setOpenSections((prev) => ({ ...prev, [key]: !prev[key] }));

  return (
    <div className="node-options postfx-inspector" style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "6px" }}>
        <span style={{ fontSize: "10px", color: "#9eb6ab" }}>Omniverse RTX PostFX</span>
        <button
          type="button"
          onClick={() => onChange({ ...defaultPostFx })}
          style={{ fontSize: "10px", padding: "2px 6px" }}
          title="Reset all PostFX settings to RTX defaults"
        >
          Reset
        </button>
      </div>

      {/* 1. Tone Mapping */}
      <Section title="Tone Mapping" open={Boolean(openSections.tonemap)} onToggleOpen={() => toggle("tonemap")}>
        <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr", gap: "4px" }}>
          <label style={compactLabel}>
            Operator
            <select
              aria-label="Tone Map Operator"
              value={cfg.tonemapOperator}
              onChange={(e) => patch({ tonemapOperator: e.target.value as PostFxSettings["tonemapOperator"] })}
              style={{ ...compactInput, marginTop: "2px" }}
            >
              {TONEMAP_OPERATORS.map((op) => (
                <option key={op.value} value={op.value}>
                  {op.label}
                </option>
              ))}
            </select>
          </label>
          <label style={compactLabel}>
            Color Space
            <select
              aria-label="Tone Map Color Space"
              value={cfg.tonemapColorMode}
              onChange={(e) => patch({ tonemapColorMode: e.target.value as PostFxSettings["tonemapColorMode"] })}
              style={{ ...compactInput, marginTop: "2px" }}
            >
              <option value="sRGBLinear">sRGB Linear</option>
              <option value="ACEScg">ACEScg</option>
            </select>
          </label>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "4px" }}>
          <label style={compactLabel}>
            Exposure Key
            <NumberInput
              step="0.05"
              min="0.001"
              value={cfg.exposureKey}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v > 0) patch({ exposureKey: v });
              }}
              style={{ ...compactInput, marginTop: "2px" }}
            />
          </label>
          <label style={compactLabel}>
            White Scale
            <NumberInput
              step="1"
              min="0.01"
              value={cfg.whiteScale}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v > 0) patch({ whiteScale: v });
              }}
              style={{ ...compactInput, marginTop: "2px" }}
            />
          </label>
          <label style={compactLabel}>
            Max White
            <NumberInput
              step="0.5"
              min="0.01"
              value={cfg.maxWhiteLuminance}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v > 0) patch({ maxWhiteLuminance: v });
              }}
              style={{ ...compactInput, marginTop: "2px" }}
            />
          </label>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px" }}>
          <label style={compactLabel}>
            Sharpness (AA)
            <NumberInput
              step="0.05"
              min="0"
              max="2"
              value={cfg.sharpness}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v >= 0) patch({ sharpness: v });
              }}
              style={{ ...compactInput, marginTop: "2px" }}
            />
          </label>
          <label style={compactLabel}>
            Dither
            <NumberInput
              step="0.05"
              min="0"
              max="1"
              value={cfg.dither}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v >= 0) patch({ dither: v });
              }}
              style={{ ...compactInput, marginTop: "2px" }}
            />
          </label>
        </div>

        <RgbControl
          label="Whitepoint"
          value={cfg.whitepoint}
          step="0.05"
          min="0.01"
          onChange={(whitepoint) => patch({ whitepoint })}
        />

        {cfg.tonemapOperator === "iray" && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "4px", borderTop: "1px solid #22333b", paddingTop: "4px" }}>
            <label style={compactLabel}>
              Burn High
              <NumberInput
                step="0.05"
                min="0"
                max="1"
                value={cfg.irayBurnHighlights}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (Number.isFinite(v)) patch({ irayBurnHighlights: v });
                }}
                style={{ ...compactInput, marginTop: "2px" }}
              />
            </label>
            <label style={compactLabel}>
              Crush Black
              <NumberInput
                step="0.05"
                min="0"
                max="1"
                value={cfg.irayCrushBlacks}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (Number.isFinite(v)) patch({ irayCrushBlacks: v });
                }}
                style={{ ...compactInput, marginTop: "2px" }}
              />
            </label>
            <label style={compactLabel}>
              Iray Sat
              <NumberInput
                step="0.05"
                min="0"
                max="3"
                value={cfg.iraySaturation}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (Number.isFinite(v)) patch({ iraySaturation: v });
                }}
                style={{ ...compactInput, marginTop: "2px" }}
              />
            </label>
          </div>
        )}
      </Section>

      {/* 2. Color Grading */}
      <Section
        title="Color Grading"
        open={Boolean(openSections.grade)}
        onToggleOpen={() => toggle("grade")}
        enabled={cfg.gradeEnabled}
        onToggleEnabled={(gradeEnabled) => patch({ gradeEnabled })}
      >
        <RgbControl label="Saturation" value={cfg.gradeSaturation} step="0.05" min="0" onChange={(gradeSaturation) => patch({ gradeSaturation })} />
        <RgbControl label="Contrast" value={cfg.gradeContrast} step="0.05" min="0" onChange={(gradeContrast) => patch({ gradeContrast })} />
        <RgbControl label="Gamma" value={cfg.gradeGamma} step="0.05" min="0.05" onChange={(gradeGamma) => patch({ gradeGamma })} />
        <RgbControl label="Gain" value={cfg.gradeGain} step="0.05" onChange={(gradeGain) => patch({ gradeGain })} />
        <RgbControl label="Lift" value={cfg.gradeLift} step="0.02" onChange={(gradeLift) => patch({ gradeLift })} />
        <RgbControl label="Offset" value={cfg.gradeOffset} step="0.02" onChange={(gradeOffset) => patch({ gradeOffset })} />
        <RgbControl label="Multiply" value={cfg.gradeMultiply} step="0.05" onChange={(gradeMultiply) => patch({ gradeMultiply })} />
        <RgbControl label="Black Point" value={cfg.gradeBlackPoint} step="0.02" onChange={(gradeBlackPoint) => patch({ gradeBlackPoint })} />
        <RgbControl label="White Point" value={cfg.gradeWhitePoint} step="0.05" onChange={(gradeWhitePoint) => patch({ gradeWhitePoint })} />
      </Section>

      {/* 3. Auto Exposure */}
      <Section
        title="Auto Exposure"
        open={Boolean(openSections.autoExposure)}
        onToggleOpen={() => toggle("autoExposure")}
        enabled={cfg.autoExposureEnabled}
        onToggleEnabled={(autoExposureEnabled) => patch({ autoExposureEnabled })}
      >
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px" }}>
          <label style={compactLabel}>
            White Point Scale
            <NumberInput
              step="0.5"
              min="0.01"
              value={cfg.autoExposureWhitePointScale}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v > 0) patch({ autoExposureWhitePointScale: v });
              }}
              style={{ ...compactInput, marginTop: "2px" }}
            />
          </label>
          <label style={compactLabel}>
            Adaptation Speed
            <NumberInput
              step="0.5"
              min="0.01"
              value={cfg.autoExposureAdaptationSpeed}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v > 0) patch({ autoExposureAdaptationSpeed: v });
              }}
              style={{ ...compactInput, marginTop: "2px" }}
            />
          </label>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "4px" }}>
          <label style={compactLabel}>
            Filter
            <select
              value={cfg.autoExposureHistogramFilter}
              onChange={(e) => patch({ autoExposureHistogramFilter: e.target.value as "median" | "average" })}
              style={{ ...compactInput, marginTop: "2px" }}
            >
              <option value="median">Median</option>
              <option value="average">Average</option>
            </select>
          </label>
          <label style={compactLabel}>
            Min Lum
            <NumberInput
              step="10"
              min="0"
              value={cfg.autoExposureMinLuminance}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v >= 0) patch({ autoExposureMinLuminance: v });
              }}
              style={{ ...compactInput, marginTop: "2px" }}
            />
          </label>
          <label style={compactLabel}>
            Max Lum
            <NumberInput
              step="1000"
              min="1"
              value={cfg.autoExposureMaxLuminance}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v > 0) patch({ autoExposureMaxLuminance: v });
              }}
              style={{ ...compactInput, marginTop: "2px" }}
            />
          </label>
        </div>
      </Section>

      {/* 4. Bloom & Lens Flares */}
      <Section
        title="Bloom & Lens Flares"
        open={Boolean(openSections.bloom)}
        onToggleOpen={() => toggle("bloom")}
        enabled={cfg.bloomEnabled}
        onToggleEnabled={(bloomEnabled) => patch({ bloomEnabled })}
      >
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "4px" }}>
          <label style={compactLabel}>
            Scale
            <NumberInput
              step="0.1"
              min="0"
              value={cfg.bloomScale}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v >= 0) patch({ bloomScale: v });
              }}
              style={{ ...compactInput, marginTop: "2px" }}
            />
          </label>
          <label style={compactLabel}>
            Cutoff
            <NumberInput
              step="0.1"
              min="0"
              value={cfg.bloomCutoff}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v >= 0) patch({ bloomCutoff: v });
              }}
              style={{ ...compactInput, marginTop: "2px" }}
            />
          </label>
          <label style={compactLabel}>
            Fuzziness
            <NumberInput
              step="0.05"
              min="0"
              max="1"
              value={cfg.bloomCutoffFuzziness}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v >= 0) patch({ bloomCutoffFuzziness: v });
              }}
              style={{ ...compactInput, marginTop: "2px" }}
            />
          </label>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: "4px" }}>
          <label style={compactLabel}>
            f-Stop
            <NumberInput
              step="0.5"
              min="0.5"
              value={cfg.bloomFStop}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v > 0) patch({ bloomFStop: v });
              }}
              style={{ ...compactInput, marginTop: "2px" }}
            />
          </label>
          <label style={compactLabel}>
            Focal
            <NumberInput
              step="5"
              min="1"
              value={cfg.bloomFocalLength}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v > 0) patch({ bloomFocalLength: v });
              }}
              style={{ ...compactInput, marginTop: "2px" }}
            />
          </label>
          <label style={compactLabel}>
            Blades
            <NumberInput
              step="1"
              min="3"
              max="16"
              value={cfg.bloomBlades}
              onChange={(e) => {
                const v = Math.round(Number(e.target.value));
                if (Number.isFinite(v) && v >= 3 && v <= 16) patch({ bloomBlades: v });
              }}
              style={{ ...compactInput, marginTop: "2px" }}
            />
          </label>
          <label style={compactLabel}>
            Rot°
            <NumberInput
              step="5"
              value={cfg.bloomApertureRotation}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v)) patch({ bloomApertureRotation: v });
              }}
              style={{ ...compactInput, marginTop: "2px" }}
            />
          </label>
        </div>
        <div style={{ display: "flex", gap: "10px", fontSize: "10px" }}>
          <label style={{ display: "flex", alignItems: "center", gap: "4px", margin: 0, cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={cfg.bloomApertureCircular}
              onChange={(e) => patch({ bloomApertureCircular: e.target.checked })}
              style={{ margin: 0 }}
            />
            Circular
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: "4px", margin: 0, cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={cfg.bloomEnergyConservingBlend}
              onChange={(e) => patch({ bloomEnergyConservingBlend: e.target.checked })}
              style={{ margin: 0 }}
            />
            Energy Conserving
          </label>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "4px" }}>
          <label style={compactLabel}>
            Aniso Flare
            <NumberInput
              step="0.05"
              min="0"
              value={cfg.bloomAnisoFlareWeight}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v >= 0) patch({ bloomAnisoFlareWeight: v });
              }}
              style={{ ...compactInput, marginTop: "2px" }}
            />
          </label>
          <label style={compactLabel}>
            Halo Flare
            <NumberInput
              step="0.01"
              min="0"
              value={cfg.bloomHaloFlareWeight}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v >= 0) patch({ bloomHaloFlareWeight: v });
              }}
              style={{ ...compactInput, marginTop: "2px" }}
            />
          </label>
          <label style={compactLabel}>
            Iso Flare
            <NumberInput
              step="0.05"
              min="0"
              value={cfg.bloomIsotropicFlareWeight}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v >= 0) patch({ bloomIsotropicFlareWeight: v });
              }}
              style={{ ...compactInput, marginTop: "2px" }}
            />
          </label>
        </div>
      </Section>

      {/* 5. Chromatic Aberration */}
      <Section
        title="Chromatic Aberration"
        open={Boolean(openSections.chromab)}
        onToggleOpen={() => toggle("chromab")}
        enabled={cfg.chromabEnabled}
        onToggleEnabled={(chromabEnabled) => patch({ chromabEnabled })}
      >
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "4px" }}>
          <label style={compactLabel}>
            Red
            <NumberInput
              step="0.005"
              value={cfg.chromabStrengthR}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v)) patch({ chromabStrengthR: v });
              }}
              style={{ ...compactInput, marginTop: "2px" }}
            />
          </label>
          <label style={compactLabel}>
            Green
            <NumberInput
              step="0.005"
              value={cfg.chromabStrengthG}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v)) patch({ chromabStrengthG: v });
              }}
              style={{ ...compactInput, marginTop: "2px" }}
            />
          </label>
          <label style={compactLabel}>
            Blue
            <NumberInput
              step="0.005"
              value={cfg.chromabStrengthB}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v)) patch({ chromabStrengthB: v });
              }}
              style={{ ...compactInput, marginTop: "2px" }}
            />
          </label>
        </div>
        <label style={{ display: "flex", alignItems: "center", gap: "4px", margin: 0, fontSize: "10px", cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={cfg.chromabUseLanczos}
            onChange={(e) => patch({ chromabUseLanczos: e.target.checked })}
            style={{ margin: 0 }}
          />
          Lanczos Sampler (Higher Quality)
        </label>
      </Section>

      {/* 6. Vignette & Film Grain */}
      <Section
        title="Vignette & Film Grain"
        open={Boolean(openSections.vignetteGrain)}
        onToggleOpen={() => toggle("vignetteGrain")}
        enabled={cfg.vignetteEnabled || cfg.filmGrainEnabled}
        onToggleEnabled={(on) => patch({ vignetteEnabled: on, filmGrainEnabled: on ? cfg.filmGrainEnabled : false })}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
          <label style={{ display: "flex", alignItems: "center", gap: "5px", margin: 0, fontSize: "10px", cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={cfg.vignetteEnabled}
              onChange={(e) => patch({ vignetteEnabled: e.target.checked })}
              style={{ margin: 0 }}
            />
            Vignette
          </label>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px" }}>
            <label style={compactLabel}>
              Strength
              <NumberInput
                step="0.05"
                min="0"
                value={cfg.vignetteStrength}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (Number.isFinite(v) && v >= 0) patch({ vignetteStrength: v });
                }}
                style={{ ...compactInput, marginTop: "2px" }}
              />
            </label>
            <label style={compactLabel}>
              Size
              <NumberInput
                step="5"
                min="1"
                value={cfg.vignetteSize}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (Number.isFinite(v) && v > 0) patch({ vignetteSize: v });
                }}
                style={{ ...compactInput, marginTop: "2px" }}
              />
            </label>
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "4px", borderTop: "1px solid #22333b", paddingTop: "4px" }}>
          <label style={{ display: "flex", alignItems: "center", gap: "5px", margin: 0, fontSize: "10px", cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={cfg.filmGrainEnabled}
              onChange={(e) => patch({ filmGrainEnabled: e.target.checked })}
              style={{ margin: 0 }}
            />
            Film Grain
          </label>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px" }}>
            <label style={compactLabel}>
              Amount
              <NumberInput
                step="0.01"
                min="0"
                value={cfg.filmGrainAmount}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (Number.isFinite(v) && v >= 0) patch({ filmGrainAmount: v });
                }}
                style={{ ...compactInput, marginTop: "2px" }}
              />
            </label>
            <label style={compactLabel}>
              Size
              <NumberInput
                step="0.1"
                min="0.1"
                value={cfg.filmGrainSize}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (Number.isFinite(v) && v > 0) patch({ filmGrainSize: v });
                }}
                style={{ ...compactInput, marginTop: "2px" }}
              />
            </label>
          </div>
        </div>
      </Section>
    </div>
  );
}
