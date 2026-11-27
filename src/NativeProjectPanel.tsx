import { InspectorHint } from "./InspectorHint";
import { OperationProgress } from "./OperationProgress";
import { useEffect, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { defaultEditScope } from "./profilePolicy";
import { useApplicationCatalog, findProfile } from "./applicationProfiles";
import { PublicationPanel } from "./PublicationPanel";
import { SubstancePainterPanel } from "./SubstancePainterPanel";
import { CoordinatePanel } from "./CoordinatePanel";
import { prepareQuickstart, recordNativeCreation } from "./fileRegistry";
import type {
  Data,
  PublicationResult,
  NativeCreationResult,
  LiveAdjustmentResult,
  ManualBaselineResult,
  HandoffResult,
  StoredProject,
} from "./fileRegistry";

async function existingTargetPaths(paths: string[]): Promise<string[]> {
  if (!isTauri() || !paths.length) return [];
  try {
    const rows = await invoke<{ id: string; status: string }[]>(
      "dependency_statuses",
      {
        dependencies: paths.map((path, index) => ({
          id: String(index),
          kind: "asset",
          path,
        })),
      },
    );
    return paths.filter((_, index) => rows[index]?.status === "Available");
  } catch {
    return [];
  }
}

export function NativeProjectPanel({
  project,
  nodeId,
  data,
  onChange,
  onHandoff,
  onQuickstartCreated,
  onBaseline,
  onNativeCreated,
  onPublished,
  onLive,
  onStatus,
  onConfigure,
  onMdBridgePulled,
  projectName,
}: {
  project: StoredProject;
  projectName: string;
  nodeId: string;
  data: Data;
  onChange: (patch: Partial<Data>) => void;
  onHandoff: (result: HandoffResult) => StoredProject;
  onQuickstartCreated: (original: StoredProject, prepared: StoredProject) => StoredProject;
  onBaseline: (result: ManualBaselineResult) => void;
  onPublished: (edited: string, result: PublicationResult) => void;
  onLive: (edited: string, result: LiveAdjustmentResult) => void;
  onNativeCreated: (result: NativeCreationResult) => StoredProject;
  onStatus: (message: string) => void;
  onConfigure: () => void;
  onMdBridgePulled?: () => void;
}) {
  const { catalog } = useApplicationCatalog();
  const profile = findProfile(catalog, data);
  const inheritedExecutable = profile?.executable || "";
  const effectiveExecutable = data.appExecutable || inheritedExecutable;
  const [busy, setBusy] = useState(false);
  const [busyScope, setBusyScope] = useState<"md-export" | "native" | null>(null);
  const [busyLabel, setBusyLabel] = useState("");
  const [exportProgress, setExportProgress] = useState<{
    detail?: string;
    progress?: number | null;
  }>({});
  const [creationMessage, setCreationMessage] = useState("");
  const [quickstartMessage, setQuickstartMessage] = useState("");
  const [preparingQuickstart, setPreparingQuickstart] = useState(false);
  const [availability, setAvailability] = useState<Record<string, string>>({});
  const [revision, setRevision] = useState(0);
  const [mdBridge, setMdBridge] = useState<{
    available: boolean;
    bridgeDir: string;
    scriptPath: string;
    pullScriptPath?: string;
    projectPath?: string;
    projectName?: string;
    exportPath?: string;
    exportedAt?: number;
    handoffAvailable?: boolean;
    handoffPath?: string;
    handoffExportedAt?: number;
    handoffAbcAvailable?: boolean;
    handoffAbcPath?: string;
    handoffAbcExportedAt?: number;
  } | null>(null);
  const [bridgeFeedback, setBridgeFeedback] = useState("");
  const [copiedScript, setCopiedScript] = useState<"send" | "pull" | "abc" | null>(null);

  async function copyBridgeScript(kind: "send" | "pull" | "abc", text: string) {
    let copied = false;
    try {
      await navigator.clipboard.writeText(text);
      copied = true;
    } catch {
      try {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        copied = document.execCommand("copy");
        document.body.removeChild(ta);
      } catch {
        copied = false;
      }
    }
    setCopiedScript(kind);
    window.setTimeout(() => setCopiedScript((cur) => (cur === kind ? null : cur)), 2000);
    const msg = copied ? `Copied path: ${text}` : `Script path: ${text}`;
    setBridgeFeedback(msg);
    onStatus(msg);
  }

  async function refreshMdBridge() {
    if (!isTauri() || data.app !== "Marvelous Designer") return;
    try {
      const info = await invoke<{
        available: boolean;
        bridgeDir: string;
        scriptPath: string;
        pullScriptPath?: string;
        projectPath?: string;
        projectName?: string;
        exportPath?: string;
        exportedAt?: number;
        handoffAvailable?: boolean;
        handoffPath?: string;
        handoffExportedAt?: number;
        handoffAbcAvailable?: boolean;
        handoffAbcPath?: string;
        handoffAbcExportedAt?: number;
      }>("md_bridge_status");
      setMdBridge(info);
    } catch {
      /* ignore bridge status errors */
    }
  }

  useEffect(() => {
    void refreshMdBridge();
  }, [data.app, revision]);

  const launchableApps = new Set([
    "Blender",
    "Marvelous Designer",
    "Substance Painter",
    "ZBrush",
  ]);
  const commonReasons = [
    ...(!isTauri() ? ["Use the desktop app."] : []),
    ...(busy ? ["Wait for the current operation to finish."] : []),
    ...(!launchableApps.has(data.app)
      ? [
          data.app
            ? `Native project opening is not implemented for ${data.app}; its profile is currently a template.`
            : "Choose an application profile.",
        ]
      : []),
    ...(!effectiveExecutable ? ["Set the executable in Applications."] : []),
  ];
  const createReasons = [
    ...commonReasons,
    ...(data.app === "Blender" && profile?.adapterVersion !== 2
      ? [
          "In Applications, set this Blender profile's adapter version to 2 and Save. Existing profiles are not upgraded automatically.",
        ]
      : []),
    ...(!data.handoff ? ["Create a handoff first."] : []),
    ...(data.nativeProject ? ["A native project is already linked."] : []),
  ];
  const quickPrepareReasons = [
    ...commonReasons,
    ...(data.app === "Blender" && profile?.adapterVersion !== 2
      ? [
          "In Applications, set this Blender profile's adapter version to 2 and Save.",
        ]
      : []),
    ...(data.handoffSelection != null && !data.handoffSelection.length
      ? ["Choose at least one handoff object."]
      : []),
  ];
  const openReasons = [
    ...commonReasons,
    ...(!data.nativeProject ? ["Create or link a Blender project first."] : []),
  ];

  useEffect(() => {
    setAvailability({});
    if (!isTauri()) return;
    let active = true;
    const timer = setTimeout(() => {
      void invoke<{ id: string; status: string }[]>("dependency_statuses", {
        dependencies: [
          { id: "native", kind: "nativeProject", path: data.nativeProject || "" },
          { id: "exe", kind: "executable", path: effectiveExecutable },
        ],
      })
        .then((rows) => {
          if (active) {
            setAvailability(Object.fromEntries(rows.map((r) => [r.id, r.status])));
          }
        })
        .catch((error) => {
          if (active) onStatus(String(error));
        });
    }, 250);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [data.nativeProject, effectiveExecutable, revision, onStatus]);

  async function run(
    task: () => Promise<void>,
    scope: "md-export" | "native" = "native",
    label = "",
  ) {
    setBusy(true);
    setBusyScope(scope);
    setBusyLabel(label);
    setExportProgress({});
    try {
      await task();
    } catch (error) {
      onStatus(String(error));
    } finally {
      setBusy(false);
      setBusyScope(null);
      setBusyLabel("");
      setExportProgress({});
    }
  }

  useEffect(() => {
    if (!isTauri() || !busy || busyScope !== "md-export") return;
    let unlisten: (() => void) | undefined;
    listen<{
      phase?: string;
      details?: { kind?: string; frame?: number; totalFrames?: number };
    }>("renderer-status", (event) => {
      const details = event.payload?.details;
      if (details?.kind !== "md-export") return;
      const pct =
        typeof details.frame === "number" &&
        typeof details.totalFrames === "number" &&
        details.totalFrames > 0
          ? details.frame / details.totalFrames
          : null;
      setExportProgress({
        detail: event.payload?.phase || undefined,
        progress: pct,
      });
    }).then((fn) => {
      unlisten = fn;
    });
    return () => {
      if (unlisten) unlisten();
    };
  }, [busy, busyScope]);

  async function browse(field: "nativeProject") {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const filters =
      data.app === "Blender"
        ? [{ name: "Blender project", extensions: ["blend"] }]
        : data.app === "Marvelous Designer"
          ? [{ name: "Marvelous Designer project", extensions: ["zprj", "zpac"] }]
          : data.app === "Substance Painter"
            ? [{ name: "Substance Painter project", extensions: ["spp"] }]
            : data.app === "ZBrush"
              ? [{ name: "ZBrush project", extensions: ["zpr", "ztl"] }]
              : undefined;
    const path = await open({
      multiple: false,
      title: "Choose an existing native project",
      filters,
    });
    if (!path) return;
    const existingId = await invoke<string | null>("find_dependency", {
      path,
      dependencies: project.dependencies,
    });
    const existing = project.dependencies.find((d) => d.id === existingId);
    onChange({ [field]: existing?.path || path });
    setRevision((r) => r + 1);
    onStatus(
      existing
        ? "Warning: file already registered. Reused its existing link for this node."
        : "Linked existing file. No import or publication was performed.",
    );
  }

  const canQuickPrepare = data.app === "Blender";
  const rerunQuickstart = !!(data.handoff || data.nativeProject);

  return (
    <div className="native-project-panel compact-native">
      <section className="app-profile">
        <div className="app-section-heading">
          <h3>Application</h3>
          <button className="app-text-action" onClick={onConfigure}>
            Settings
          </button>
        </div>
        <label>
          <span className="field-label">
            Profile
            <InspectorHint text="Choose the application profile. Executable paths and shared capabilities are managed in Applications." />
          </span>
          <select
            value={data.applicationProfileId || ""}
            onChange={(e) => {
              const chosen = catalog.applications.find(
                (p) => p.id === e.target.value,
              );
              onChange({
                app: chosen?.app || "",
                applicationProfileId: chosen?.id || null,
                editScope: defaultEditScope(chosen),
              });
            }}
          >
            <option value="">Choose application</option>
            {catalog.applications.map((p) => (
              <option key={p.id} value={p.id}>
                {p.app} · {p.applicationVersion}
              </option>
            ))}
            {data.applicationProfileId && !profile && (
              <option value={data.applicationProfileId} disabled>
                Profile unavailable
              </option>
            )}
          </select>
        </label>
        {!effectiveExecutable && data.app === "Blender" && (
          <p className="app-notice">Set the executable in Applications.</p>
        )}
        {!!data.appExecutable && (
          <p className="hint">Using a saved node executable override.</p>
        )}
      </section>

      {canQuickPrepare && (
        <section className="coordinate-panel app-workflow-step">
          {preparingQuickstart && (
            <OperationProgress label="Creating handoff, Blender project and baseline…" />
          )}
          <div className="app-section-heading">
            <h3>
              Quick start
              <InspectorHint text="Saves a handoff USD and an unedited .baseline.usdc alongside the chosen .blend before opening Blender." />
            </h3>
          </div>
          <button
            className="primary"
            disabled={!!quickPrepareReasons.length}
            title={
              quickPrepareReasons.join(" ") ||
              "Create handoff USD, save the native project and baseline, and open Blender"
            }
            onClick={() =>
              run(async () => {
                setPreparingQuickstart(true);
                setQuickstartMessage(
                  "Choose where to save the Blender project. Quick start also saves <name>_handoff.usd and <name>.baseline.usdc there.",
                );
                try {
                  const { save, confirm } = await import(
                    "@tauri-apps/plugin-dialog"
                  );
                  const safeBase = (projectName + "_" + data.label)
                    .replace(/[<>:"/\\|?*\x00-\x1f]/g, "_")
                    .replace(/[. ]+$/g, "");
                  const blendPath = await save({
                    title: "Save Blender project with handoff USD and baseline USDC",
                    defaultPath: rerunQuickstart ? `${safeBase}_new.blend` : `${safeBase}.blend`,
                    filters: [{ name: "Blender project", extensions: ["blend"] }],
                  });
                  if (!blendPath) {
                    setQuickstartMessage("Cancelled.");
                    return;
                  }
                  if (!/\.blend$/i.test(blendPath)) {
                    throw new Error("Choose a filename ending in .blend.");
                  }
                  const handoffPath = blendPath.replace(/\.blend$/i, "_handoff.usd");
                  const baselinePath = blendPath.replace(/\.blend$/i, ".baseline.usdc");
                  const targets = [handoffPath, blendPath, baselinePath];
                  const linked = await Promise.all(targets.map(path =>
                    invoke<string | null>("find_dependency", { path, dependencies: project.dependencies }),
                  ));
                  if (linked.some(Boolean)) {
                    throw new Error("A Quick start destination is already registered. Choose a new .blend name or folder.");
                  }
                  const existing = await existingTargetPaths(targets);
                  if (existing.length > 0) {
                    const overwrite = await confirm(
                      `Replace existing file(s)?\n${existing.join("\n")}\nBackups of replaced Blender project and baseline files will be retained. Close the target project in Blender first.`,
                      {
                        title: "Replace existing project files",
                        kind: "warning",
                      },
                    );
                    if (!overwrite) {
                      setQuickstartMessage("Cancelled. Existing files unchanged.");
                      return;
                    }
                  }
                  setQuickstartMessage("Creating handoff USD...");
                  onStatus("Creating handoff USD...");
                  const createdHandoff = await invoke<HandoffResult>(
                    "create_handoff",
                    {
                      project,
                      nodeId,
                      path: handoffPath,
                      quickstartNativePath: blendPath,
                      overwrite: existing.includes(handoffPath),
                    },
                  );
                  const projectAfterHandoff = prepareQuickstart(project, nodeId, createdHandoff);

                  setQuickstartMessage(
                    "Importing the handoff and saving the unedited baseline and Blender project. This may take several minutes.",
                  );
                  onStatus(
                    "Importing the handoff and capturing an unedited baseline in Blender…",
                  );
                  const createdNative = await invoke<NativeCreationResult>(
                    "create_blender_project",
                    {
                      project: projectAfterHandoff,
                      nodeId,
                      path: blendPath,
                      overwrite: existing.includes(blendPath),
                      captureBaseline: true,
                      overwriteBaseline: existing.includes(baselinePath),
                    },
                  );
                  if (!createdNative.baselinePath) {
                    throw new Error("Quick start did not return a validated baseline.");
                  }
                  const prepared = recordNativeCreation(projectAfterHandoff, nodeId, createdNative);
                  const projectAfterNative = onQuickstartCreated(project, prepared);
                  setRevision((r) => r + 1);
                  setQuickstartMessage(
                    "Saved Blender project and unedited baseline. Requesting Blender launch…",
                  );
                  try {
                    await invoke("open_native_project", {
                      project: projectAfterNative,
                      nodeId,
                    });
                    setQuickstartMessage(
                      "Blender launch requested. Use the recorded USD settings when exporting edits. Save the Sublayer project to retain its links.",
                    );
                    onStatus(
                      "Created the handoff, Blender project and unedited baseline. Blender launch requested.",
                    );
                  } catch (error) {
                    const message = `Created ${createdNative.path}, but opening failed: ${error}. Use Open in Blender to retry.`;
                    setQuickstartMessage(message);
                    onStatus(message);
                  }
                } catch (error) {
                  setQuickstartMessage(String(error));
                  throw error;
                } finally {
                  setPreparingQuickstart(false);
                }
              })
            }
          >
            {busy ? "Preparing…" : rerunQuickstart ? "Rerun Quickstart…" : "Prepare & Open in Blender…"}
          </button>
          {rerunQuickstart && (
            <p className="hint">Choose a new destination to prepare a fresh Blender project and baseline. Your current links stay active until preparation succeeds.</p>
          )}
          {quickstartMessage && (
            <p className="hint" role="status">
              {quickstartMessage}
            </p>
          )}
          {!!quickPrepareReasons.length && (
            <p className="hint">{quickPrepareReasons[0]}</p>
          )}
        </section>
      )}

      {data.app === "Marvelous Designer" ? (
        <section className="coordinate-panel app-workflow-step">
          {busy && busyScope === "md-export" && (
            <OperationProgress
              label={busyLabel || "Exporting MD Bridge handoff..."}
              detail={exportProgress.detail}
              progress={exportProgress.progress}
            />
          )}
          <div className="app-section-heading">
            <h3>
              <span className="app-step-number">01</span> Send to MD
              <InspectorHint text="Export your character/avatar in millimetres for Marvelous Designer. Use Pose Ramp (.abc, 30f) to animate from rest pose at frame 0 into the current pose at frame 30, or Static Avatar (.obj) with sublayer_pull_avatar.py." />
            </h3>
            <span
              className={
                "app-state " +
                (mdBridge?.handoffAbcAvailable || mdBridge?.handoffAvailable
                  ? "ready"
                  : "")
              }
            >
              {mdBridge?.handoffAbcAvailable || mdBridge?.handoffAvailable
                ? "Prepared"
                : "Not sent"}
            </span>
          </div>
          {mdBridge?.handoffAbcAvailable && mdBridge.handoffAbcPath && (
            <div className="app-file" title={mdBridge.handoffAbcPath}>
              <span className="app-file-kind">Pose ramp (0→30f)</span>
              <strong>{mdBridge.handoffAbcPath.split(/[\\/]/).pop()}</strong>
              <button
                type="button"
                className="app-text-action"
                onClick={() =>
                  void copyBridgeScript("abc", mdBridge.handoffAbcPath!)
                }
              >
                {copiedScript === "abc" ? "Copied ✓" : "Copy path"}
              </button>
            </div>
          )}
          {mdBridge?.handoffAvailable && mdBridge.handoffPath && (
            <div className="app-file" title={mdBridge.handoffPath}>
              <span className="app-file-kind">Static avatar (.obj)</span>
              <strong>{mdBridge.handoffPath.split(/[\\/]/).pop()}</strong>
            </div>
          )}
          <button
            className="primary"
            type="button"
            disabled={busy || !isTauri()}
            title="Export an Alembic (.abc) that starts in the default rest pose at frame 0 and animates over 30 frames into the current pose"
            onClick={() =>
              run(async () => {
                const res = await invoke<{
                  handoffAbcPath: string;
                  meshCount: number;
                  rampFrames: number;
                }>("send_to_md_bridge", {
                  project,
                  nodeId,
                  baseName: `${projectName}_${data.label}`,
                  format: "abc",
                  rampFrames: 30,
                });
                await refreshMdBridge();
                const msg = `Exported ${res.handoffAbcPath.split(/[\\/]/).pop()} (${res.meshCount} mesh(es), 0→${res.rampFrames}f pose ramp). Import via File → Import → Alembic in MD.`;
                setBridgeFeedback(msg);
                onStatus(msg);
              }, "md-export", "Exporting 30-frame pose ramp (.abc)...")
            }
          >
            Export Pose Ramp (.abc, 30f)
          </button>
          <button
            className="app-secondary"
            type="button"
            disabled={busy || !isTauri()}
            onClick={() =>
              run(async () => {
                const res = await invoke<{
                  handoffPath: string;
                  meshCount: number;
                }>("send_to_md_bridge", {
                  project,
                  nodeId,
                  baseName: `${projectName}_${data.label}`,
                  format: "obj",
                });
                await refreshMdBridge();
                const msg = `Sent ${res.handoffPath.split(/[\\/]/).pop()} (${res.meshCount} mesh(es)) to MD Bridge. Run sublayer_pull_avatar.py in MD to import it.`;
                setBridgeFeedback(msg);
                onStatus(msg);
              }, "md-export", "Exporting static avatar (.obj)...")
            }
          >
            Send Static Avatar (.obj)
          </button>
          {mdBridge?.pullScriptPath && (
            <button
              className="app-secondary"
              type="button"
              onClick={() =>
                void copyBridgeScript("pull", mdBridge.pullScriptPath!)
              }
            >
              {copiedScript === "pull"
                ? "Copied Pull Avatar script ✓"
                : "Copy Pull Avatar script"}
            </button>
          )}
          <button
            className="app-secondary"
            type="button"
            disabled={!isTauri()}
            onClick={() => void invoke("open_md_bridge_folder")}
          >
            Open Bridge Folder
          </button>
          <details className="node-options">
            <summary>Advanced: USD Handoff &amp; coordinates</summary>
            {data.handoff && (
              <div
                className="app-file"
                title={
                  project.dependencies.find(
                    (d) => d.id === data.handoff?.dependencyId,
                  )?.path
                }
              >
                <span className="app-file-kind">USD input</span>
                <strong>
                  {project.dependencies
                    .find((d) => d.id === data.handoff?.dependencyId)
                    ?.path.split(/[\\/]/)
                    .pop()}
                </strong>
              </div>
            )}
            <CoordinatePanel
              projectName={projectName}
              project={project}
              nodeId={nodeId}
              data={data}
              onChange={onChange}
              onHandoff={onHandoff}
              onStatus={onStatus}
            />
          </details>
        </section>
      ) : (
        <section className="coordinate-panel app-workflow-step">
          <div className="app-section-heading">
            <h3>
              <span className="app-step-number">01</span> Handoff
              <InspectorHint text="Create one binary USD handoff, with the selected objects, materials and coordinate conversion. Textures remain linked. Recreate it explicitly after upstream changes; this does not create a native project." />
            </h3>
            <span className={"app-state " + (data.handoff ? "ready" : "")}>
              {data.handoff ? "Prepared" : "Not prepared"}
            </span>
          </div>
          {data.handoff && (
            <div
              className="app-file"
              title={
                project.dependencies.find(
                  (d) => d.id === data.handoff?.dependencyId,
                )?.path
              }
            >
              <span className="app-file-kind">USD input</span>
              <strong>
                {project.dependencies
                  .find((d) => d.id === data.handoff?.dependencyId)
                  ?.path.split(/[\\/]/)
                  .pop()}
              </strong>
            </div>
          )}
          <CoordinatePanel
            projectName={projectName}
            project={project}
            nodeId={nodeId}
            data={data}
            onChange={onChange}
            onHandoff={onHandoff}
            onStatus={onStatus}
          />
        </section>
      )}

      <section className="coordinate-panel app-workflow-step">
        {busy && !preparingQuickstart && busyScope === "native" && (
          <OperationProgress
            label={
              busyLabel ||
              (data.nativeProject
                ? "Opening or linking native project..."
                : "Preparing native project...")
            }
          />
        )}
        <div className="app-section-heading">
          <h3>
            <span className="app-step-number">02</span> Native project
            <InspectorHint text="Keep the editable application project here. Creating a Blender project is separate from handoff export. Existing projects reopen without importing upstream again. Other apps currently support manual project linking." />
          </h3>
          <span className={"app-state " + (data.nativeProject ? "ready" : "")}>
            {data.nativeProject ? "Linked" : "Not linked"}
          </span>
        </div>
        {data.nativeProject ? (
          <>
            <div className="app-file" title={data.nativeProject}>
              <span className="app-file-kind">
                Editable source · {availability.native || "Checking..."}
              </span>
              <strong>{data.nativeProject.split(/[\\/]/).pop()}</strong>
            </div>
            <button
              className="primary"
              disabled={!!openReasons.length}
              title={
                openReasons.join(" ") || "Reopen the linked Blender project"
              }
              onClick={() =>
                run(async () => {
                  const pid = await invoke<number>("open_native_project", {
                    project,
                    nodeId,
                  });
                  onStatus(
                    `Launch requested for the existing native project (process ${pid}). Native save and USD publication remain separate actions.`,
                  );
                })
              }
            >
              Open in {data.app || "App"}
            </button>
            {!!openReasons.length && (
              <p className="app-notice">{openReasons[0]}</p>
            )}
            <button
              className="app-secondary"
              disabled={busy || !isTauri()}
              onClick={() => run(() => browse("nativeProject"))}
            >
              Relink project…
            </button>
          </>
        ) : (
          <>
            {data.app === "Blender" ? (
              <>
                <button
                  className={data.handoff ? "primary" : undefined}
                  disabled={!!createReasons.length}
                  title={
                    createReasons.join(" ") ||
                    "Create a new .blend from the recorded handoff; baseline is exported manually"
                  }
                  onClick={() =>
                    run(async () => {
                      setCreationMessage(
                        "Choose the Blender project destination.",
                      );
                      try {
                        const { save, confirm } = await import(
                          "@tauri-apps/plugin-dialog"
                        );
                        const path = await save({
                          title: "Create new Blender project from handoff",
                          defaultPath: "project.blend",
                          filters: [
                            { name: "Blender project", extensions: ["blend"] },
                          ],
                        });
                        if (!path) {
                          setCreationMessage(
                            "Cancelled. No Blender project or baseline was created.",
                          );
                          return;
                        }
                        const existing = await existingTargetPaths([path]);
                        if (existing.length > 0) {
                          const overwrite = await confirm(
                            "Replace the existing .blend file? This discards its current edits at this path. Backup files will be retained. Close the target project in Blender first.",
                            {
                              title: "Replace existing Blender project",
                              kind: "warning",
                            },
                          );
                          if (!overwrite) {
                            setCreationMessage(
                              "Cancelled. Existing files unchanged.",
                            );
                            return;
                          }
                        }
                        setCreationMessage(
                          "Importing handoff and saving Blender project. This may take several minutes for large scenes...",
                        );
                        onStatus(
                          "Importing the recorded handoff in Blender. This may take several minutes for large scenes…",
                        );
                        const created = await invoke<NativeCreationResult>(
                          "create_blender_project",
                          {
                            project,
                            nodeId,
                            path,
                            overwrite: existing.length > 0,
                          },
                        );
                        const next = onNativeCreated(created);
                        setCreationMessage(
                          "Created native Blender project. Export an unedited baseline manually before making edits.",
                        );
                        setRevision((r) => r + 1);
                        onStatus(
                          "Created and verified the native Blender project. Opening it now…",
                        );
                        try {
                          await invoke("open_native_project", {
                            project: next,
                            nodeId,
                          });
                          onStatus(
                            "Created Blender project and requested opening. Save OrchFlow to retain its native link and import snapshot. Published USD is unchanged.",
                          );
                        } catch (error) {
                          onStatus(
                            `Created and linked ${created.path}, but opening failed: ${error}. Use Open in App to retry.`,
                          );
                        }
                      } catch (error) {
                        setCreationMessage(String(error));
                        throw error;
                      }
                    })
                  }
                >
                  {busy ? "Working…" : "Create Blender project…"}
                </button>
                {creationMessage && (
                  <p className="hint" role="status">
                    {creationMessage}
                  </p>
                )}
                {!!createReasons.length && (
                  <p className="hint">{createReasons[0]}</p>
                )}
              </>
            ) : (
              <p className="hint">
                Link a project created in {data.app || "your application"}.
              </p>
            )}
            <button
              className="app-secondary"
              disabled={busy || !isTauri()}
              onClick={() => run(() => browse("nativeProject"))}
            >
              Link existing project…
            </button>
          </>
        )}
      </section>

      {data.app === "Marvelous Designer" ? (
        <>
          <section className="coordinate-panel app-workflow-step">
            <div className="app-section-heading">
              <h3>
                <span className="app-step-number">03</span> Pull Garment
                <InspectorHint text="Export your simulated garment as garment.abc or garment.obj into the MD Bridge folder (or run sublayer_send_garment.py), then click Pull Garment into Scene." />
              </h3>
              <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <button
                  className="app-text-action"
                  type="button"
                  onClick={() => void refreshMdBridge()}
                >
                  Refresh
                </button>
                <span className={"app-state " + (data.mdBridgePath ? "ready" : "")}>
                  {data.mdBridgePath ? "Active" : mdBridge?.available ? "Ready" : "Waiting"}
                </span>
              </div>
            </div>
            {data.mdBridgePath && (
              <div className="app-file" title={data.mdBridgePath}>
                <span className="app-file-kind">Active MD garment</span>
                <strong>{data.mdBridgePath.split(/[\\/]/).pop()}</strong>
                <button
                  type="button"
                  className="app-text-action"
                  onClick={() => {
                    onChange({ mdBridgePath: "" });
                    setBridgeFeedback("Cleared pulled MD garment.");
                  }}
                >
                  Clear
                </button>
              </div>
            )}
            {mdBridge?.available && mdBridge.exportPath ? (
              <>
                <div className="app-file" title={mdBridge.exportPath}>
                  <span className="app-file-kind">
                    Bridge export · {mdBridge.projectName || "MD"}
                  </span>
                  <strong>{mdBridge.exportPath.split(/[\\/]/).pop()}</strong>
                </div>
                <button
                  className="primary"
                  type="button"
                  disabled={busy || !isTauri()}
                  onClick={() => {
                    const patch: Partial<Data> = {
                      mdBridgePath: mdBridge.exportPath!,
                    };
                    if (
                      !data.nativeProject &&
                      mdBridge.projectPath &&
                      /\.(zprj|zpac)$/i.test(mdBridge.projectPath)
                    ) {
                      patch.nativeProject = mdBridge.projectPath;
                    }
                    onChange(patch);
                    onMdBridgePulled?.();
                    const fileName =
                      mdBridge.exportPath!.split(/[\\/]/).pop() || "garment";
                    const msg = `Pulled ${fileName} into scene output (combined with upstream avatar, mm → m).`;
                    setBridgeFeedback(msg);
                    onStatus(msg);
                  }}
                >
                  Pull Garment into Scene
                </button>
              </>
            ) : (
              <p className="hint">
                No MD bridge export yet. Export <code>garment.abc</code> /{" "}
                <code>garment.obj</code> to the bridge folder, or run{" "}
                <code>sublayer_send_garment.py</code> in MD.
              </p>
            )}
            {mdBridge?.scriptPath && (
              <button
                className="app-secondary"
                type="button"
                onClick={() => void copyBridgeScript("send", mdBridge.scriptPath)}
              >
                {copiedScript === "send"
                  ? "Copied Send Garment script ✓"
                  : "Copy Send Garment script"}
              </button>
            )}
            {bridgeFeedback && (
              <p className="app-notice" role="status">
                {bridgeFeedback}
              </p>
            )}
          </section>

          <details className="node-options">
            <summary>Advanced: Manual baseline diff</summary>
            <PublicationPanel
              key={data.nativeImport?.baselineDependencyId}
              onLive={onLive}
              onBaseline={onBaseline}
              project={project}
              nodeId={nodeId}
              data={data}
              onPublished={onPublished}
              onChange={onChange}
              onStatus={onStatus}
              onConfigure={onConfigure}
            />
          </details>
        </>
      ) : (
        <PublicationPanel
          key={data.nativeImport?.baselineDependencyId}
          onLive={onLive}
          onBaseline={onBaseline}
          project={project}
          nodeId={nodeId}
          data={data}
          onPublished={onPublished}
          onChange={onChange}
          onStatus={onStatus}
          onConfigure={onConfigure}
        />
      )}

      <details className="node-options app-advanced">
        <summary>Advanced</summary>
        {!!data.path && (
          <p className="hint">
            This node already has a direct USD link: {data.path}
            <button onClick={() => onChange({ path: "" })}>
              Remove direct link and use connected inputs
            </button>
          </p>
        )}
        <label>
          <span className="field-label">
            Project path
            <InspectorHint text="Editable application project path. Managed when creating or linking a project." />
          </span>
          <input
            disabled={busy}
            value={data.nativeProject || ""}
            onChange={(e) => onChange({ nativeProject: e.target.value })}
          />
        </label>
        {!!data.appExecutable && (
          <p className="hint">
            This node has a previously saved executable override.{" "}
            <button onClick={() => onChange({ appExecutable: "" })}>
              Use global application default
            </button>
          </p>
        )}
        <button
          disabled={busy || !isTauri()}
          onClick={() => setRevision((r) => r + 1)}
        >
          Check linked files
        </button>
        {data.nativeImport && (
          <p className="hint">
            Prepared with Blender{" "}
            {String(data.nativeImport.report.blenderVersion)}. Original
            conversion and baseline are retained.
          </p>
        )}
      </details>
      {(data.app === "Substance Painter" || data.applicationProfileId === "substance-painter") && (
        <SubstancePainterPanel
          project={project}
          nodeId={nodeId}
          data={data}
          onNativeCreated={onNativeCreated}
          onLive={onLive}
          onStatus={onStatus}
        />
      )}
    </div>
  );
}
