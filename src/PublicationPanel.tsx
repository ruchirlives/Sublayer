import { InspectorHint } from "./InspectorHint";
import { OperationProgress } from "./OperationProgress";
import { NodeEditScope, type PolicySnapshot } from "./applicationProfiles";
import { useEffect, useRef, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import type {
  LiveAdjustmentResult,
  ManualBaselineResult,
  Data,
  StoredProject,
  PublicationResult,
  Correspondence,
} from "./fileRegistry";

type Change = {
  prim: string;
  property: string;
  reason: string;
  pointTransform?: number[][];
};

type Review = {
  editedDigest: string;
  token: string;
  report: {
    applied: Change[];
    preserved: Change[];
    conflicts: Change[];
    deinstanced: string[];
  };
  correspondence: (Correspondence & { method?: string })[];
  inputPrims: { path: string; type: string }[];
  baselinePrims: { path: string; type: string }[];
  editedPrims: { path: string; type: string }[];
};

type BaselineMapping = {
  needsMapping: true;
  baselinePath: string;
  digest: string;
  baselinePrims: { path: string; type: string }[];
  inputPrims: { path: string; type: string }[];
  message: string;
};

export function PublicationPanel({
  project,
  nodeId,
  data,
  onPublished: _onPublished,
  onChange,
  onStatus,
  onBaseline,
  onLive,
  onConfigure,
}: {
  onLive: (edited: string, result: LiveAdjustmentResult) => void;
  onBaseline: (result: ManualBaselineResult) => void;
  project: StoredProject;
  nodeId: string;
  data: Data;
  onPublished: (edited: string, result: PublicationResult) => void;
  onChange: (patch: Partial<Data>) => void;
  onStatus: (message: string) => void;
  onConfigure: () => void;
}) {
  const node = project.nodes.find((n) => n.id === nodeId);
  const native = node?.data.manualBaseline || node?.data.nativeImport;
  const quickstartExporter = native?.report.baselineOrigin === "quickstart"
    ? native.report.exporterOptions as Record<string, unknown> | undefined
    : undefined;
  const live = node?.data.liveAdjustment;
  const baselinePath = project.dependencies.find(
    (d) => d.id === native?.baselineDependencyId,
  )?.path;
  const editedPath = project.dependencies.find(
    (d) => d.id === live?.editedDependencyId,
  )?.path;

  const [baselineMapping, setBaselineMapping] =
    useState<BaselineMapping | null>(null);
  const [baselineChoices, setBaselineChoices] = useState<
    Record<string, string>
  >({});
  const [correspondenceOpen, setCorrespondenceOpen] = useState(false);
  const [progress, setProgress] = useState("Working...");
  const [analysisRequest, setAnalysisRequest] = useState(0);
  const handledAnalysis = useRef(0);
  const analysisAlignment = useRef(false);
  const [newPrimPaths, setNewPrimPaths] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const liveForBaseline = live && live.baselineDependencyId === native?.baselineDependencyId &&
    live.handoffDependencyId === native?.handoffDependencyId;
  const editedForBaseline = liveForBaseline ? editedPath : undefined;
  const [edited, setEdited] = useState(() => editedForBaseline || "");
  const [mapping, setMapping] = useState<Correspondence[]>([]);
  const [deletions, setDeletions] = useState<string[]>([]);
  const [review, setReview] = useState<Review | null>(null);
  const [inventory, setInventory] = useState<Review | null>(null);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (editedForBaseline) {
      setEdited(editedForBaseline);
    }
  }, [editedForBaseline]);

  const key = JSON.stringify([
    edited,
    mapping,
    deletions,
    newPrimPaths,
    project,
    native,
    node?.data.editScope,
    node?.data.applicationProfileId,
    node?.data.publicationPolicy,
  ]);
  const latest = useRef(key);
  latest.current = key;

  useEffect(() => setReview(null), [key]);

  // Wait for committed file/mapping state before starting the requested review.
  useEffect(() => {
    if (!analysisRequest || handledAnalysis.current === analysisRequest) return;
    handledAnalysis.current = analysisRequest;
    void analyze(analysisAlignment.current);
  }, [analysisRequest]);

  async function analyze(alignment = false) {
    setReview(null);
    setMessage(
      alignment
        ? "Coordinate alignment selected. Reanalysing changes…"
        : "",
    );
    setProgress(
      alignment
        ? "Checking coordinate alignment and reanalysing changes…"
        : "Comparing baseline and edited export...",
    );
    setBusy(true);
    try {
      const result = await invoke<Review>("review_application_publication", {
        project,
        nodeId,
        edited,
        newPrimPaths,
        correspondence: mapping,
        confirmedDeletions: deletions,
        destination: null,
        reviewToken: null,
      });
      if (latest.current !== key) {
        setMessage("Inputs changed during review. Analyze again.");
        return;
      }
      if (
        inventory &&
        inventory.editedDigest !== result.editedDigest &&
        deletions.length
      ) {
        setDeletions([]);
        setReview(null);
        setInventory(result);
        setMessage(
          "Export contents changed. Deletion confirmations were cleared; analyze and confirm again.",
        );
        return;
      }
      const unresolved = result.report.conflicts.find(
        (c) => c.property === "correspondence",
      );
      if (unresolved) {
        setCorrespondenceOpen(true);
        setQuery(unresolved.prim);
        setPage(0);
      }
      setReview(result);
      setInventory(result);
      setMessage(
        result.report.conflicts.length
          ? `Resolve ${result.report.conflicts.length} issue(s) below, then analyse again.`
          : result.report.applied.length
            ? `Ready to apply ${result.report.applied.length} change(s).`
            : "No allowed changes found. Check the node’s Allowed edits if you expected changes.",
      );
    } catch (error) {
      setReview(null);
      if (
        String(error).includes("Baseline correspondence needs remapping") &&
        baselinePath
      ) {
        setOpen(false);
        await linkBaseline(
          baselinePath,
          [],
          native?.report.baselineDigest as string | undefined,
        );
      } else {
        setMessage(String(error));
      }
    } finally {
      setBusy(false);
    }
  }

  async function linkBaseline(
    baseline: string,
    correspondence: Correspondence[] = [],
    expectedDigest?: string,
  ) {
    setProgress("Validating baseline correspondence...");
    setBusy(true);
    try {
      const result = await invoke<ManualBaselineResult | BaselineMapping>(
        "validate_manual_baseline",
        { project, nodeId, baseline, correspondence, expectedDigest },
      );
      if (latest.current !== key) {
        setMessage("Project changed during validation; select baseline again.");
        return;
      }
      if ("needsMapping" in result) {
        setBaselineMapping(result);
        setBaselineChoices({});
        setMessage(result.message);
        return;
      }
      onBaseline(result);
      setBaselineMapping(null);
      setReview(null);
      setInventory(null);
      setMapping([]);
      setDeletions([]);
      setMessage(
        "Baseline linked with reviewed correspondence. Save OrchFlow to retain it.",
      );
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
    }
  }

  function openAnalysis() {
    const baselineMappings =
      (native?.report.baselineCorrespondence as Correspondence[] | undefined) ||
      [];
    const previous = node?.data.liveAdjustment || node?.data.publication;
    const inherited =
      previous?.conversion?.graphRevision === native?.conversion?.graphRevision
        ? previous?.correspondence || []
        : [];
    const seeded = new Map(baselineMappings.map((m) => [m.baselinePath, m]));
    for (const m of inherited) {
      const current = seeded.get(m.baselinePath);
      if (!current || current.inputPath === m.inputPath) {
        seeded.set(m.baselinePath, m);
      }
    }
    setNewPrimPaths([]);
    setMapping([...seeded.values()]);
    setDeletions([]);
    setReview(null);
    setInventory(null);
    setMessage("");
    setQuery("");
    setPage(0);
    setCorrespondenceOpen(false);
    setOpen(true);
    analysisAlignment.current = false;
    setAnalysisRequest((value) => value + 1);
  }

  async function browseEdited() {
    try {
      const { open: openDialog } = await import("@tauri-apps/plugin-dialog");
      const path = await openDialog({
        multiple: false,
        title: "Choose edited application export",
        filters: [
          {
            name: "Application export (binary USD preferred)",
            extensions: ["usd", "usdc", "usda", "obj", "abc"],
          },
        ],
      });
      if (path) {
        setEdited(path);
        setDeletions([]);
        setReview(null);
        setInventory(null);
        setMessage("");
        if (native) {
          openAnalysis();
        }
      }
    } catch (error) {
      setMessage(String(error));
    }
  }

  function changeMapping(path: string, patch: Partial<Correspondence>) {
    const automatic = inventory?.correspondence.find(
      (m) => m.baselinePath === path,
    );
    const current = mapping.find((m) => m.baselinePath === path);
    const next = {
      baselinePath: path,
      inputPath: "",
      editedPath: path,
      ...automatic,
      ...current,
      ...patch,
    };
    setMapping((previous) => [
      ...previous.filter((m) => m.baselinePath !== path),
      next,
    ]);
  }

  const rows = (inventory?.baselinePrims || []).filter((p) =>
    p.path.toLowerCase().includes(query.toLowerCase()),
  );
  const permissionsChanged =
    !!native &&
    (JSON.stringify((node?.data.publicationPolicy || native.policy).editScope) !==
      JSON.stringify(node?.data.editScope || []) ||
      (node?.data.publicationPolicy || native.policy).profile.id !==
        node?.data.applicationProfileId);

  return (
    <section className="coordinate-panel app-workflow-step app-live-output">
      <div className="app-section-heading">
        <h3>
          <span className="app-step-number">03</span> Adjustment
          <InspectorHint text="Compare an unedited baseline with the edited export, using the same selection, format and export settings. USD, OBJ and Alembic are supported; binary USD is best for large files. Exports must match the handoff coordinates. Reviewed edits live in OrchFlow over the current upstream scene; save the project to retain them." />
        </h3>
        <span className={"app-state " + (liveForBaseline ? "ready" : "")}>
          {liveForBaseline ? "Applied" : native ? "Ready to review" : "Needs baseline"}
        </span>
      </div>

      {busy && !open && <OperationProgress label={progress} />}

      {node?.data.publication && !node.data.liveAdjustment && (
        <p className="app-notice" role="status">
          Review and apply the edited export to enable live output.
        </p>
      )}

      <NodeEditScope
        data={data}
        onChange={onChange}
        onConfigure={onConfigure}
      />

      {permissionsChanged && (
        <>
          <button
            disabled={busy || !isTauri()}
            onClick={async () => {
              setProgress("Validating edit permissions...");
              setBusy(true);
              try {
                const policy = await invoke<PolicySnapshot>(
                  "authorize_publication_scope",
                  { project, nodeId },
                );
                if (latest.current !== key) {
                  onStatus(
                    "Node/input changed during authorization; apply permissions again.",
                  );
                  return;
                }
                onChange({ publicationPolicy: policy });
                onStatus(
                  "Applied changed edit permissions to this baseline. Native files were not changed.",
                );
              } catch (e) {
                onStatus(String(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            Apply changed edit permissions
          </button>
          <p className="hint">Updated edit scope needs approval.</p>
        </>
      )}

      <div className="app-file" title={baselinePath}>
        <span className="app-file-kind">Unedited baseline</span>
        <strong>
          {baselinePath?.split(/[\\/]/).pop() || "No baseline linked"}
        </strong>
      </div>
      <button
        className={node?.data.handoff && !native ? "primary" : undefined}
        disabled={busy || !isTauri() || !node?.data.handoff}
        onClick={async () => {
          const { open: openDialog, confirm } = await import(
            "@tauri-apps/plugin-dialog"
          );
          const baseline = await openDialog({
            multiple: false,
            title: "Choose manually exported unedited baseline",
            filters: [
              {
                name: "Application baseline (binary USD preferred)",
                extensions: ["usd", "usdc", "usda", "obj", "abc"],
              },
            ],
          });
          if (!baseline) return;
          if (
            !(await confirm(
              `Confirm this file was exported before your edits, from this node's handoff. Use the same format, selection, vertex order and export settings for the edited file. OBJ/Alembic coordinates must use the recorded handoff convention: ${node?.data.handoff?.conversion.upAxis}-up, ${node?.data.handoff?.conversion.metersPerUnit} metres/unit. OBJ materials are preserved from upstream; MTL files are not imported. OrchFlow cannot prove the baseline is unedited.`,
              { title: "Use manual baseline", kind: "warning" },
            ))
          )
            return;
          await linkBaseline(baseline);
        }}
      >
        {native ? "Change baseline…" : "Browse baseline…"}
      </button>

      {!node?.data.handoff && (
        <p className="hint">Create a handoff first.</p>
      )}
      {!open && message && (
        <p className="app-notice" role="status">
          {message}
        </p>
      )}

      {quickstartExporter && (
        <details className="review-advanced">
          <summary>Export edits with the Quick start settings</summary>
          <p className="hint">
            Use Blender {String(native?.report.blenderVersion)}. Run this command
            in Blender’s Python Console and choose a new .usdc file for the edited
            scene. Keep the export settings as shown.
          </p>
          <textarea
            aria-label="Blender Python command for the edited USD export"
            readOnly
            rows={5}
            value={`import json; bpy.ops.wm.usd_export("INVOKE_DEFAULT", **json.loads(${JSON.stringify(JSON.stringify({ ...quickstartExporter, check_existing: true }))}))`}
          />
        </details>
      )}

      <div className="app-file" title={edited || editedPath}>
        <span className="app-file-kind">Edited export</span>
        <strong>
          {edited.split(/[\\/]/).pop() || "No edited export selected"}
        </strong>
      </div>
      {live && editedPath && editedPath !== edited && (
        <p className="hint" title={editedPath}>
          Currently applied: {editedPath.split(/[\\/]/).pop()}
        </p>
      )}

      <button
        className={native && !edited.trim() ? "primary" : undefined}
        disabled={busy || !isTauri()}
        onClick={() => void browseEdited()}
      >
        Browse edited export…
      </button>

      <button
        className={native && edited.trim() ? "primary" : undefined}
        disabled={busy || !native || !edited.trim() || !isTauri()}
        onClick={openAnalysis}
      >
        {live && editedPath === edited ? "Re-analyse export" : "Analyse changes"}
      </button>

      {live && (
        <details className="node-options app-export">
          <summary>Export USD</summary>
          <button
            disabled={busy || !isTauri()}
            onClick={async () => {
              setProgress("Composing and exporting final USD...");
              setBusy(true);
              try {
                const { save } = await import("@tauri-apps/plugin-dialog");
                const destination = await save({
                  title: "Export single-file binary USD scene",
                  defaultPath: "final.usd",
                  filters: [
                    { name: "Binary USD scene", extensions: ["usd", "usdc"] },
                    { name: "ASCII USD (debugging)", extensions: ["usda"] },
                  ],
                });
                if (!destination) return;
                const result = await invoke<{ path: string }>(
                  "export_live_output",
                  { project, nodeId, destination },
                );
                setMessage(`Exported ${result.path}`);
              } catch (e) {
                setMessage(String(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            Export final USD...
          </button>
        </details>
      )}

      {!native && (
        <p className="hint">Link an unedited baseline to analyse changes.</p>
      )}

      {baselineMapping && (
        <div className="override-backdrop">
          <section
            className="override-modal publication-review"
            role="dialog"
            aria-modal="true"
            aria-label="Map baseline objects"
          >
            <h2>Match baseline objects to handoff</h2>
            <p>{baselineMapping.message}</p>
            <p>
              Choose each object's handoff counterpart explicitly. Equal names
              are not proof of correspondence. Geometry and coordinate checks
              still run when reviewing edits.
            </p>
            <div className="dependency-grid">
              <table>
                <thead>
                  <tr>
                    <th>Baseline object</th>
                    <th>Type</th>
                    <th>Handoff object</th>
                  </tr>
                </thead>
                <tbody>
                  {baselineMapping.baselinePrims.map((prim) => (
                    <tr key={prim.path}>
                      <td>{prim.path}</td>
                      <td>{prim.type}</td>
                      <td>
                        <select
                          disabled={busy}
                          value={baselineChoices[prim.path] || ""}
                          onChange={(e) =>
                            setBaselineChoices((previous) => ({
                              ...previous,
                              [prim.path]: e.target.value,
                            }))
                          }
                        >
                          <option value="">
                            Choose the matching handoff object
                          </option>
                          {baselineMapping.inputPrims
                            .filter((p) => p.type === prim.type)
                            .map((p) => (
                              <option key={p.path} value={p.path}>
                                {p.path}
                              </option>
                            ))}
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {busy && <OperationProgress label={progress} />}
            <p role="status">{message}</p>
            <button
              disabled={
                busy ||
                baselineMapping.baselinePrims.some(
                  (p) => !baselineChoices[p.path],
                )
              }
              onClick={() =>
                linkBaseline(
                  baselineMapping.baselinePath,
                  baselineMapping.baselinePrims.map((p) => ({
                    baselinePath: p.path,
                    inputPath: baselineChoices[p.path],
                    editedPath: p.path,
                  })),
                  baselineMapping.digest,
                )
              }
            >
              Confirm correspondence and link baseline
            </button>
            <button disabled={busy} onClick={() => setBaselineMapping(null)}>
              Cancel
            </button>
          </section>
        </div>
      )}

      {open && (
        <div
          className="override-backdrop"
          onKeyDown={(e) => {
            if (e.key === "Escape" && !busy) setOpen(false);
          }}
        >
          <section
            className="override-modal publication-review application-change-review"
            role="dialog"
            aria-modal="true"
            aria-label="Review changes"
          >
            <h2>Review changes</h2>
            <p className="hint" title={edited}>
              {edited.split(/[\\/]/).pop()} · Resolve any matches below, then
              apply.
            </p>
            {busy && <OperationProgress label={progress} />}
            <p role="status" aria-live="polite">
              {!busy && inventory && !review
                ? "Matches or options changed. Analyse again to refresh the results."
                : message}
            </p>
            {inventory && (
              <div className="review-summary">
                <span>{inventory.report.applied.length} changes</span>
                <span>{inventory.report.conflicts.length} need attention</span>
                <span>{inventory.report.preserved.length} preserved</span>
              </div>
            )}
            {inventory && (
              <>
                <details
                  open={correspondenceOpen}
                  onToggle={(e) =>
                    setCorrespondenceOpen(e.currentTarget.open)
                  }
                >
                  <summary>Object matches</summary>
                  <p className="hint">
                    Match each exported object to its input and edited
                    counterpart. Full paths identify objects with the same
                    name.
                  </p>
                  <input
                    aria-label="Filter baseline prims"
                    placeholder="Filter baseline paths…"
                    value={query}
                    onChange={(e) => {
                      setQuery(e.target.value);
                      setPage(0);
                    }}
                  />
                  <div className="dependency-grid">
                    <table>
                      <thead>
                        <tr>
                          <th>Baseline prim</th>
                          <th>Preserved input prim</th>
                          <th>Edited export prim</th>
                          <th>Match</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.slice(page * 50, (page + 1) * 50).map((p) => {
                          const automatic = inventory.correspondence.find(
                            (m) => m.baselinePath === p.path,
                          );
                          const explicit = mapping.find(
                            (m) => m.baselinePath === p.path,
                          );
                          const value = explicit || automatic;
                          return (
                            <tr key={p.path}>
                              <td title={p.path}>
                                {p.path.split("/").pop()}
                                <small> · {p.type}</small>
                              </td>
                              <td>
                                <select
                                  aria-label={`Input for ${p.path}`}
                                  disabled={busy}
                                  value={value?.inputPath || ""}
                                  onChange={(e) =>
                                    changeMapping(p.path, {
                                      inputPath: e.target.value,
                                      pointTransform: undefined,
                                    })
                                  }
                                >
                                  <option value="">Choose input prim</option>
                                  {value?.inputPath &&
                                    !inventory.inputPrims.some(
                                      (q) => q.path === value.inputPath,
                                    ) && (
                                      <option value={value.inputPath}>
                                        Missing: {value.inputPath}
                                      </option>
                                    )}
                                  {inventory.inputPrims
                                    .filter((q) => q.type === p.type)
                                    .map((q) => (
                                      <option key={q.path} value={q.path}>
                                        {q.path}
                                      </option>
                                    ))}
                                </select>
                              </td>
                              <td>
                                <select
                                  aria-label={`Edited for ${p.path}`}
                                  disabled={busy}
                                  value={value?.editedPath || p.path}
                                  onChange={(e) =>
                                    changeMapping(p.path, {
                                      editedPath: e.target.value,
                                      pointTransform: undefined,
                                    })
                                  }
                                >
                                  {!inventory.editedPrims.some(
                                    (q) =>
                                      q.path === (value?.editedPath || p.path),
                                  ) && (
                                    <option
                                      value={value?.editedPath || p.path}
                                    >
                                      Choose edited prim (current path missing)
                                    </option>
                                  )}
                                  {inventory.editedPrims
                                    .filter((q) => q.type === p.type)
                                    .map((q) => (
                                      <option key={q.path} value={q.path}>
                                        {q.path}
                                      </option>
                                    ))}
                                </select>
                              </td>
                              <td>
                                {explicit
                                  ? "explicit"
                                  : automatic?.method || "unresolved"}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <button disabled={!page} onClick={() => setPage((p) => p - 1)}>
                    Previous
                  </button>
                  <span>
                    {" "}
                    {Math.min(page * 50 + 1, rows.length)}–
                    {Math.min((page + 1) * 50, rows.length)} of {rows.length}{" "}
                  </span>
                  <button
                    disabled={(page + 1) * 50 >= rows.length}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    Next
                  </button>
                  <button
                    disabled={busy || !mapping.length}
                    onClick={() => setMapping([])}
                  >
                    Reset explicit mappings
                  </button>
                </details>

                {(["conflicts", "applied", "preserved"] as const).map((kind) => (
                  <details
                    className="review-results"
                    key={kind}
                    open={
                      kind === "conflicts" &&
                      !!(review || inventory).report.conflicts.length
                    }
                  >
                    <summary>
                      {
                        {
                          conflicts: "Needs attention",
                          applied: "Changes",
                          preserved: "Preserved",
                        }[kind]
                      }{" "}
                      ({(review || inventory).report[kind].length})
                    </summary>
                    <div className="dependency-grid">
                      <table>
                        <thead>
                          <tr>
                            <th>Object</th>
                            <th>Edit</th>
                            <th>Details</th>
                            <th>Action</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(review || inventory).report[kind]
                            .filter((change) =>
                              change.prim
                                .toLowerCase()
                                .includes(query.toLowerCase()),
                            )
                            .slice(0, 500)
                            .map((change, index) => (
                              <tr key={index}>
                                <td title={change.prim}>
                                  {change.prim.split("/").pop()}
                                </td>
                                <td>{change.property}</td>
                                <td>{change.reason}</td>
                                <td>
                                  {change.pointTransform && (
                                    <button
                                      disabled={busy}
                                      title={JSON.stringify(
                                        change.pointTransform,
                                      )}
                                      onClick={() => {
                                        changeMapping(change.prim, {
                                          pointTransform: change.pointTransform,
                                        });
                                        analysisAlignment.current = true;
                                        setAnalysisRequest((value) => value + 1);
                                      }}
                                    >
                                      Use alignment and analyse
                                    </button>
                                  )}
                                  {(change.reason.includes(
                                    "Confirm this deletion",
                                  ) ||
                                    change.reason === "Explicit deletion") && (
                                    <label>
                                      <input
                                        type="checkbox"
                                        checked={deletions.includes(change.prim)}
                                        onChange={(e) =>
                                          setDeletions((previous) =>
                                            e.target.checked
                                              ? [...previous, change.prim]
                                              : previous.filter(
                                                  (p) => p !== change.prim,
                                                ),
                                          )
                                        }
                                      />
                                      Delete this input prim
                                    </label>
                                  )}
                                </td>
                              </tr>
                            ))}
                        </tbody>
                      </table>
                    </div>
                  </details>
                ))}

                {!!inventory.report.deinstanced.length && (
                  <p className="hint">
                    Explicit edits require separate evaluation of{" "}
                    {inventory.report.deinstanced.length} instance ancestor(s).
                    Original reference arcs and untouched instances remain
                    preserved.
                  </p>
                )}
              </>
            )}

            <details className="review-advanced">
              <summary>Advanced options</summary>
              <details>
                <summary>Export settings and coordinates</summary>
                <pre>
                  {JSON.stringify(
                    {
                      exporter:
                        native?.report.exporterOptions ||
                        "Manual export: use identical settings for both files",
                      conversion: native?.conversion,
                      policy: native?.policy,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
              {inventory && (
                <details>
                  <summary>Approve new prims</summary>
                  <p>
                    Enter one new exported prim root per line. Existing objects
                    cannot be added again. New meshes require geometry points,
                    topology and transforms; unsupported schemas and external
                    bindings are blocked.
                  </p>
                  <textarea
                    aria-label="New prim roots"
                    disabled={busy}
                    value={newPrimPaths.join("\n")}
                    onChange={(e) =>
                      setNewPrimPaths(
                        e.target.value
                          .split("\n")
                          .map((p) => p.trim())
                          .filter(Boolean),
                      )
                    }
                  />
                </details>
              )}
              <p className="hint">
                Tables show up to 500 rows. Filter Object matches to inspect a
                specific path. Original files stay unchanged.
              </p>
            </details>

            <div className="review-actions">
              <button
                disabled={busy || !edited.trim()}
                onClick={() => void analyze()}
              >
                Analyse again
              </button>
              <button
                className="primary"
                disabled={
                  busy ||
                  !review ||
                  !review.report.applied.length ||
                  !!review.report.conflicts.length
                }
                onClick={async () => {
                  if (!review) return;
                  setProgress("Building reviewed live adjustment...");
                  setBusy(true);
                  try {
                    if (latest.current !== key) {
                      setMessage("Inputs changed. Analyze again.");
                      return;
                    }
                    const result = await invoke<LiveAdjustmentResult>(
                      "apply_live_adjustment",
                      {
                        project,
                        nodeId,
                        edited,
                        newPrimPaths,
                        correspondence: mapping,
                        reviewToken: review.token,
                      },
                    );
                    if (latest.current !== key) {
                      setReview(null);
                      setMessage(
                        "Project changed while applying edits. Analyze again.",
                      );
                      return;
                    }
                    onLive(edited, result);
                    setMessage(
                      "Live adjustment applied. Save OrchFlow to retain it.",
                    );
                    onStatus(
                      "Applied reviewed edits in memory over current upstream. Save OrchFlow to retain the adjustment. No USD files written.",
                    );
                    setOpen(false);
                  } catch (error) {
                    setMessage(String(error));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Apply reviewed edits
              </button>
              <button disabled={busy} onClick={() => setOpen(false)}>
                Close
              </button>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}
