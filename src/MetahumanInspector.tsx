import { invoke, isTauri } from "@tauri-apps/api/core";
import type { Data } from "./fileRegistry";
import { NumberInput } from "./NumberInput";

type CompanionResult = {
  role: "head" | "body" | null;
  companionPath: string | null;
};

const leafName = (p: string) => p.split(/[\\/]/).filter(Boolean).pop() || p;

export function MetahumanInspector({
  path,
  bodyPath,
  lod,
  materials,
  onChange,
  onStatus,
}: {
  path: string;
  bodyPath: string;
  lod: number;
  materials: boolean;
  onChange: (patch: Partial<Data>) => void;
  onStatus: (message: string) => void;
}) {
  async function browse(slot: "head" | "body") {
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const file = await open({
        multiple: false,
        filters: [{ name: "MetaHuman DNA", extensions: ["dna"] }],
      });
      if (typeof file !== "string") return;

      const patch: Partial<Data> =
        slot === "head" ? { path: file } : { metahumanBodyPath: file };

      const otherEmpty =
        slot === "head" ? !bodyPath.trim() : !path.trim();

      if (otherEmpty && isTauri()) {
        try {
          const info = await invoke<CompanionResult>(
            "find_metahuman_companion_dna",
            { path: file },
          );
          if (info?.companionPath) {
            if (slot === "head" && info.role === "body" && !path.trim()) {
              patch.path = info.companionPath;
              patch.metahumanBodyPath = file;
              onStatus(
                `Linked Body DNA (${leafName(file)}) and auto-loaded companion Head DNA (${leafName(info.companionPath)}).`,
              );
            } else if (slot === "body" && info.role === "head" && !bodyPath.trim()) {
              patch.path = file;
              patch.metahumanBodyPath = info.companionPath;
              onStatus(
                `Linked Head DNA (${leafName(file)}) and auto-loaded companion Body DNA (${leafName(info.companionPath)}).`,
              );
            } else if (slot === "head") {
              patch.metahumanBodyPath = info.companionPath;
              onStatus(
                `Linked Head DNA and auto-loaded companion Body DNA (${leafName(info.companionPath)}).`,
              );
            } else {
              patch.path = info.companionPath;
              onStatus(
                `Linked Body DNA and auto-loaded companion Head DNA (${leafName(info.companionPath)}).`,
              );
            }
          }
        } catch {
          // Companion lookup is best-effort; keep the selected file.
        }
      }

      onChange(patch);
    } catch (error) {
      onStatus(`DNA browse failed: ${String(error)}`);
    }
  }

  return (
    <>
      <label>
        Head DNA
        <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
          <input
            style={{ flex: 1, minWidth: 0 }}
            value={path}
            placeholder="C:\characters\head.dna"
            onChange={(e) => onChange({ path: e.target.value })}
          />
          <button
            type="button"
            disabled={!isTauri()}
            onClick={() => void browse("head")}
            title="Select or change Head .dna source"
          >
            Browse…
          </button>
          {path && (
            <button
              type="button"
              className="danger"
              style={{ padding: "2px 6px" }}
              title="Clear Head DNA"
              aria-label="Clear Head DNA"
              onClick={() => onChange({ path: "" })}
            >
              ×
            </button>
          )}
        </div>
      </label>

      <label>
        Body DNA
        <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
          <input
            style={{ flex: 1, minWidth: 0 }}
            value={bodyPath}
            placeholder="C:\characters\body.dna (auto-detected if present)"
            onChange={(e) => onChange({ metahumanBodyPath: e.target.value })}
          />
          <button
            type="button"
            disabled={!isTauri()}
            onClick={() => void browse("body")}
            title="Select or change Body .dna source"
          >
            Browse…
          </button>
          {bodyPath && (
            <button
              type="button"
              className="danger"
              style={{ padding: "2px 6px" }}
              title="Clear Body DNA"
              aria-label="Clear Body DNA"
              onClick={() => onChange({ metahumanBodyPath: "" })}
            >
              ×
            </button>
          )}
        </div>
      </label>

      <label>
        LOD (0 is highest detail)
        <NumberInput
          min="0"
          max="31"
          step="1"
          value={lod}
          onChange={(e) => {
            const value = Number(e.target.value);
            if (Number.isInteger(value) && value >= 0 && value <= 31)
              onChange({ metahumanLod: value });
          }}
        />
      </label>
      <label className="checkbox-row">
        <input
          type="checkbox"
          checked={materials}
          onChange={(e) => onChange({ metahumanMaterials: e.target.checked })}
        />
        Auto-build MaterialX materials from sibling textures
      </label>
      <p className="hint">
        Selecting either Head or Body DNA automatically brings in its companion
        DNA from the same or sibling folder when the other slot is empty. You
        can browse or clear either slot independently to swap sources.
      </p>
      <p className="hint">
        When both Head and Body DNA share joint names (e.g. spine_04 / neck /
        head), their rigs are stitched onto one unified USD skeleton, and Body
        RigLogic (RBF + Swing-Twist corrective joints) evaluates automatically
        when posed in a downstream Pose node.
      </p>
      <p className="hint">
        Auto-build MaterialX scans sibling texture folders (read-only) and binds
        dual-context MaterialX Standard Surface + UsdPreviewSurface materials
        for skin, eyes, cornea shell, eyelashes, teeth, saliva, and cartilage.
      </p>
    </>
  );
}
