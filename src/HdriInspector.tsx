import { NumberInput } from "./NumberInput";
import {
  registerFiles,
  type Dependency,
  type HdriSettings,
} from "./fileRegistry";

export const defaultHdri: HdriSettings = {
  textureDependencyId: null,
  intensity: 1.0,
  exposure: 0.0,
  rotation: 0.0,
  color: [1, 1, 1],
  textureFormat: "latlong",
};

function rgbToHex(c: [number, number, number]): string {
  const toHex = (v: number) =>
    Math.max(0, Math.min(255, Math.round(v * 255)))
      .toString(16)
      .padStart(2, "0");
  return `#${toHex(c[0])}${toHex(c[1])}${toHex(c[2])}`;
}

function hexToRgb(hex: string): [number, number, number] {
  const r = parseInt(hex.slice(1, 3), 16) / 255;
  const g = parseInt(hex.slice(3, 5), 16) / 255;
  const b = parseInt(hex.slice(5, 7), 16) / 255;
  return [
    Number(r.toFixed(3)),
    Number(g.toFixed(3)),
    Number(b.toFixed(3)),
  ];
}

export function HdriInspector({
  value,
  dependencies,
  onChange,
  onStatus,
}: {
  value?: HdriSettings;
  dependencies: Dependency[];
  onChange: (hdri: HdriSettings, nextDependencies?: Dependency[]) => void;
  onStatus: (msg: string) => void;
}) {
  const hdri: HdriSettings = { ...defaultHdri, ...(value || {}) };
  const dep = hdri.textureDependencyId
    ? dependencies.find((d) => d.id === hdri.textureDependencyId)
    : undefined;

  async function browseHdri() {
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const picked = await open({
        multiple: false,
        filters: [
          {
            name: "HDRI Environment",
            extensions: ["hdr", "exr", "png", "jpg", "jpeg", "tif", "tiff"],
          },
        ],
      });
      if (!picked || typeof picked !== "string") return;
      const { dependencies: nextDeps, ids } = registerFiles(
        dependencies,
        [picked],
        "asset",
      );
      if (ids[0]) {
        onChange({ ...hdri, textureDependencyId: ids[0] }, nextDeps);
        onStatus(`Linked HDRI: ${picked.split(/[\\/]/).pop()}`);
      }
    } catch (err) {
      onStatus(String(err));
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginBottom: "12px" }}>
      <h3 className="field-label" style={{ margin: 0 }}>
        HDRI Dome Light
      </h3>
      <div
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
        <div>
          <span style={{ fontSize: "11px", color: "#93aaa9", display: "block", marginBottom: "4px" }}>
            Environment Map (.hdr / .exr)
          </span>
          <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
            <input
              type="text"
              readOnly
              value={dep?.path || ""}
              placeholder="No HDRI selected (flat white dome)"
              style={{ flex: 1, fontSize: "11px", padding: "4px 6px", background: "#0e181c" }}
            />
            <button
              onClick={browseHdri}
              style={{ fontSize: "11px", padding: "4px 8px", whiteSpace: "nowrap" }}
            >
              Browse…
            </button>
            {hdri.textureDependencyId && (
              <button
                onClick={() => onChange({ ...hdri, textureDependencyId: null })}
                style={{ fontSize: "11px", padding: "4px 6px" }}
                title="Clear HDRI texture"
              >
                ×
              </button>
            )}
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" }}>
          <label style={{ margin: 0, fontSize: "11px" }}>
            Intensity
            <NumberInput
              min="0"
              step="0.1"
              value={hdri.intensity}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v >= 0) onChange({ ...hdri, intensity: v });
              }}
              style={{ width: "100%", padding: "4px", fontSize: "11px" }}
            />
          </label>
          <label style={{ margin: 0, fontSize: "11px" }}>
            Exposure (EV)
            <NumberInput
              step="0.1"
              value={hdri.exposure}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v)) onChange({ ...hdri, exposure: v });
              }}
              style={{ width: "100%", padding: "4px", fontSize: "11px" }}
            />
          </label>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" }}>
          <label style={{ margin: 0, fontSize: "11px" }}>
            Rotation (°)
            <NumberInput
              step="1"
              value={hdri.rotation}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v)) onChange({ ...hdri, rotation: v });
              }}
              style={{ width: "100%", padding: "4px", fontSize: "11px" }}
            />
          </label>
          <label style={{ margin: 0, fontSize: "11px" }}>
            Format
            <select
              value={hdri.textureFormat}
              onChange={(e) =>
                onChange({
                  ...hdri,
                  textureFormat: e.target.value as HdriSettings["textureFormat"],
                })
              }
              style={{ width: "100%", padding: "4px", fontSize: "11px" }}
            >
              <option value="latlong">LatLong</option>
              <option value="automatic">Automatic</option>
              <option value="angular">Angular</option>
              <option value="mirroredBall">Mirrored Ball</option>
            </select>
          </label>
        </div>

        <label style={{ margin: 0, fontSize: "11px" }}>
          Tint Color
          <div style={{ display: "flex", gap: "6px", alignItems: "center", marginTop: "4px" }}>
            <input
              type="color"
              aria-label="HDRI Tint Picker"
              value={rgbToHex(hdri.color)}
              onChange={(e) => onChange({ ...hdri, color: hexToRgb(e.target.value) })}
              style={{ width: "28px", height: "24px", padding: 0, border: "none", cursor: "pointer", background: "none" }}
            />
            {[0, 1, 2].map((idx) => (
              <NumberInput
                key={idx}
                step="0.05"
                min="0"
                max="10"
                value={hdri.color[idx]}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (!Number.isFinite(v) || v < 0) return;
                  const next: [number, number, number] = [...hdri.color];
                  next[idx] = v;
                  onChange({ ...hdri, color: next });
                }}
                style={{ width: "100%", padding: "4px", fontSize: "11px" }}
              />
            ))}
          </div>
        </label>
      </div>
    </div>
  );
}
