import { InspectorHint } from "./InspectorHint";
import { NumberInput } from "./NumberInput";
import { HandoffSelection } from "./HandoffSelection";
import { OperationProgress } from "./OperationProgress";
import { useApplicationCatalog, findProfile } from "./applicationProfiles";
import { useEffect, useMemo, useRef, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import {
  graphPreview,
  type HandoffResult,
  type CoordinateDraft,
  type Data,
  type StoredProject,
} from "./fileRegistry";

type Preflight = {
  draft: CoordinateDraft;
  inputDimensions: number[];
  handoffDimensions: number[];
  note: string;
};

async function targetFileExists(path: string): Promise<boolean> {
  if (!isTauri() || !path) return false;
  try {
    const rows = await invoke<{ id: string; status: string }[]>(
      "dependency_statuses",
      { dependencies: [{ id: "target", kind: "usd", path }] },
    );
    return rows[0]?.status === "Available";
  } catch {
    return false;
  }
}

export function CoordinatePanel({
  project,
  nodeId,
  data,
  onChange,
  onHandoff,
  onStatus,
  projectName = "Untitled",
}: {
  projectName?: string;
  project: StoredProject;
  nodeId: string;
  data: Data;
  onChange: (patch: Partial<Data>) => void;
  onHandoff: (result: HandoffResult) => void;
  onStatus: (message: string) => void;
}) {
  const { catalog } = useApplicationCatalog();
  const profile = findProfile(catalog, data);
  const axis = data.axis === "preserve" ? profile?.upAxis : data.axis;
  const unit = data.applicationMetersPerUnit ?? profile?.metersPerUnit;
  const effectiveScale = data.scale * (profile?.scale || 1);
  const graph = graphPreview(project, nodeId, true);
  const signature = JSON.stringify([
    graph,
    data.app,
    axis,
    unit,
    effectiveScale,
    profile,
    data.editScope,
    data.handoffSelection,
  ]);
  const latest = useRef(signature);
  latest.current = signature;
  const [result, setResult] = useState<Preflight | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setResult(null), [signature]);
  const [handoffMessage, setHandoffMessage] = useState("");

  const handoffOutdated = useMemo(() => {
    if (!data.handoff) return false;
    const conv = data.handoff.conversion;
    if (
      axis !== conv.upAxis ||
      unit !== conv.metersPerUnit ||
      Math.abs(effectiveScale - conv.scale) > 1e-9
    ) {
      return true;
    }
    if (
      JSON.stringify(data.handoffSelection ?? null) !==
      JSON.stringify(data.handoff.selection ?? null)
    ) {
      return true;
    }
    try {
      const parsed = JSON.parse(graph) as {
        edges?: Array<{ id?: string; source?: string; target?: string }>;
      };
      const currentIncoming = (parsed.edges || [])
        .filter((e) => e.target === nodeId)
        .map((e) => `${e.id || ""}:${e.source || ""}`)
        .sort();
      const recordedIncoming = Object.entries(data.handoff.inputEdges || {})
        .map(([source, id]) => `${id}:${source}`)
        .sort();
      if (JSON.stringify(currentIncoming) !== JSON.stringify(recordedIncoming)) {
        return true;
      }
    } catch {
      /* ignore parse errors */
    }
    return false;
  }, [data.handoff, data.handoffSelection, axis, unit, effectiveScale, graph, nodeId]);

  const canCreateHandoff =
    !busy &&
    isTauri() &&
    !!profile &&
    !!data.app &&
    !!axis &&
    !!unit &&
    (data.handoffSelection == null || data.handoffSelection.length > 0);

  return (
    <div className="preparation-input">
      {busy && <OperationProgress label="Preparing or validating handoff USD..." />}

      {handoffOutdated && (
        <p className="app-notice" role="status">
          Inputs or coordinate settings changed since this handoff was created. Recreate handoff to update.
        </p>
      )}

      <HandoffSelection
        graph={graph}
        selection={data.handoffSelection}
        onChange={(value) => onChange({ handoffSelection: value })}
        disabled={busy}
      />

      <button
        className={data.handoff && !handoffOutdated ? undefined : "primary"}
        disabled={!canCreateHandoff}
        onClick={async () => {
          setBusy(true);
          setHandoffMessage("Choose a destination for the handoff USD.");
          try {
            const { save, confirm } = await import("@tauri-apps/plugin-dialog");
            const safeBase = (projectName + "_" + data.label)
              .replace(/[<>:"/\\|?*\x00-\x1f]/g, "_")
              .replace(/[. ]+$/g, "");
            const path = await save({
              title: "Create single-file handoff USD",
              defaultPath: `${safeBase}_handoff.usd`,
              filters: [
                { name: "Binary USD handoff", extensions: ["usd", "usdc"] },
                { name: "ASCII USD (debugging)", extensions: ["usda"] },
              ],
            });
            if (!path) {
              setHandoffMessage("Cancelled. No new handoff was created.");
              return;
            }
            if (latest.current !== signature) {
              const message =
                "Settings changed while choosing a destination. Create the handoff again.";
              setHandoffMessage(message);
              onStatus(message);
              return;
            }
            const exists = await targetFileExists(path);
            if (exists) {
              const overwrite = await confirm(
                "Replace this existing file with a new OrchFlow handoff? Source files cannot be replaced. Texture files stay linked.",
                { title: "Replace existing handoff", kind: "warning" },
              );
              if (!overwrite) {
                setHandoffMessage("Cancelled. Existing handoff unchanged.");
                return;
              }
            }
            setHandoffMessage("Creating handoff USD...");
            const created = await invoke<HandoffResult>("create_handoff", {
              project,
              nodeId,
              path,
              overwrite: exists,
            });
            onHandoff(created);
            const size =
              created.fileSizeBytes == null
                ? ""
                : ` · ${(created.fileSizeBytes / 1048576).toFixed(1)} MB`;
            const scope =
              created.selection == null
                ? "whole scene"
                : `${created.selection.length} selected root(s)`;
            const message = `Created ${created.path.split(/[\\/]/).pop()}${size} · ${scope}. Save OrchFlow to retain its link.`;
            setHandoffMessage(message);
            onStatus(message);
          } catch (error) {
            const message = String(error);
            setHandoffMessage(message);
            onStatus(message);
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy
          ? "Preparing handoff..."
          : data.handoff
            ? "Recreate handoff…"
            : "Create handoff…"}
      </button>

      {handoffMessage && (
        <p className="hint" role="status">
          {handoffMessage}
        </p>
      )}

      <details className="node-options">
        <summary>Coordinates &amp; scale</summary>
        <label>
          <span className="field-label">
            Up axis
            <InspectorHint text="Overrides the application default for the next handoff. The source graph is Y-up in metres. Existing native projects keep their recorded conversion." />
          </span>
          <select
            value={data.axis}
            onChange={(e) => onChange({ axis: e.target.value })}
          >
            <option value="preserve">Use application profile</option>
            <option>Y</option>
            <option>Z</option>
          </select>
        </label>
        <label>
          <span className="field-label">
            Scale
            <InspectorHint text="Additional multiplier, applied with unit conversion on handoff and reversed on return. Defaults to 1." />
          </span>
          <NumberInput
            min="0.000001"
            step="0.1"
            value={data.scale}
            onChange={(e) => {
              const value = Number(e.target.value);
              if (Number.isFinite(value) && value > 0) onChange({ scale: value });
            }}
          />
        </label>
        <label>
          <span className="field-label">
            Metres per unit
            <InspectorHint text="Overrides the application unit size for the next handoff. Leave empty to use its profile default." />
          </span>
          <NumberInput
            min="0.000001"
            step="0.001"
            placeholder="Use application profile"
            value={data.applicationMetersPerUnit ?? ""}
            onChange={(e) => {
              const value = Number(e.target.value);
              if (!e.target.value) onChange({ applicationMetersPerUnit: null });
              else if (Number.isFinite(value) && value > 0)
                onChange({ applicationMetersPerUnit: value });
            }}
          />
        </label>
        <p className="hint">
          Target: {axis || "choose axis"}-up · {unit ?? "choose units"} m/unit ·
          additional scale {data.scale}. Linear factor:{" "}
          {unit ? effectiveScale / unit : "—"}.
          <InspectorHint
            text={
              profile
                ? `Profile defaults: ${profile.upAxis}-up · ${profile.metersPerUnit} m/unit · scale ${profile.scale}`
                : "Select an application to see profile defaults."
            }
          />
        </p>
        <button
          disabled={!canCreateHandoff}
          onClick={async () => {
            setBusy(true);
            try {
              const checked = await invoke<Preflight>("usd_request", {
                request: {
                  operation: "coordinate_preflight",
                  graph: JSON.parse(graph),
                  app: data.app,
                  upAxis: axis,
                  metersPerUnit: unit,
                  scale: effectiveScale,
                  selection: data.handoffSelection,
                },
              });
              if (latest.current !== signature) {
                onStatus(
                  "Settings or inputs changed during preparation. Run the check again.",
                );
                return;
              }
              setResult(checked);
              onStatus(
                "Checked the composed input and reversible coordinate matrices. No native project or external USD was written.",
              );
            } catch (error) {
              onStatus(String(error));
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "Checking…" : "Preview conversion"}
        </button>
        {data.handoff && (
          <p className="hint">
            Handoff:{" "}
            {
              project.dependencies.find(
                (d) => d.id === data.handoff?.dependencyId,
              )?.path
            }
            <br />
            Recorded: {data.handoff.conversion.upAxis}-up ·{" "}
            {data.handoff.conversion.metersPerUnit} m/unit · scale{" "}
            {data.handoff.conversion.scale}.
          </p>
        )}
        {result && (
          <>
            <p>
              Input dimensions (m):{" "}
              {result.inputDimensions.map((v) => v.toPrecision(5)).join(" × ")}
              <br />
              Handoff dimensions (target units):{" "}
              {result.handoffDimensions.map((v) => v.toPrecision(5)).join(" × ")}
            </p>
            <details>
              <summary>Forward and return matrices</summary>
              <pre>
                {JSON.stringify(
                  { forward: result.draft.forward, inverse: result.draft.inverse },
                  null,
                  2,
                )}
              </pre>
            </details>
            <p className="hint">{result.note}</p>
          </>
        )}
        {data.handoff &&
          result &&
          JSON.stringify(data.handoff.conversion) !==
            JSON.stringify(result.draft) && (
            <p className="hint">
              Current inputs or settings differ from the recorded handoff. Create
              a new handoff before importing these changes.
            </p>
          )}
        </details>
        <button
          className="app-secondary"
          disabled={busy || !isTauri() || !profile}
          onClick={async () => {
            setBusy(true);
            try {
              const { open } = await import("@tauri-apps/plugin-dialog");
              const path = await open({
                title: "Relink existing handoff USD",
                multiple: false,
                filters: [
                  { name: "USD handoff", extensions: ["usd", "usdc", "usda"] },
                ],
              });
              if (!path || Array.isArray(path)) return;
              try {
                const reused = await invoke<HandoffResult>("reuse_handoff", {
                  project,
                  nodeId,
                  path,
                });
                if (latest.current !== signature) {
                  setHandoffMessage(
                    "Inputs changed; select the handoff again.",
                  );
                  return;
                }
                onHandoff(reused);
                setHandoffMessage(
                  "Existing handoff linked unchanged. Save Sublayer to retain it.",
                );
              } catch {
                // Previous-session handoff (no donor node in current project):
                // link the existing USD file directly using the current preflight draft.
                if (!profile) return;
                const upAxis = data.axis === "preserve" ? profile.upAxis : data.axis;
                const meters = data.applicationMetersPerUnit ?? profile.metersPerUnit;
                let draft = result?.draft;
                if (!draft && graph) {
                  const checked = await invoke<Preflight>("usd_request", {
                    request: {
                      operation: "coordinate_preflight",
                      graph: JSON.parse(graph),
                      app: profile.app,
                      upAxis,
                      metersPerUnit: meters,
                      scale: effectiveScale,
                      selection: data.handoffSelection,
                      measure: false,
                    },
                  });
                  draft = checked.draft;
                }
                if (!draft) {
                  throw new Error("Connect an upstream input before relinking a handoff.");
                }
                onHandoff({
                  path,
                  layerPaths: [],
                  policy: {
                    profile,
                    editScope: data.editScope || {},
                  } as HandoffResult["policy"],
                  draft: draft as HandoffResult["draft"],
                  selection: data.handoffSelection ?? null,
                  inputEdges: {},
                });
                setHandoffMessage(
                  "Previous-session handoff linked. Save Sublayer to retain it.",
                );
              }
            } catch (error) {
              setHandoffMessage(String(error));
            } finally {
              setBusy(false);
            }
          }}
        >
          Relink handoff…
        </button>
    </div>
  );
}
