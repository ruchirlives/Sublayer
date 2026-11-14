import { useEffect, useMemo, useRef, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import {
  referencedBy,
  overrideDependency,
  overrideInputs,
  type StoredProject,
} from "./fileRegistry";

export function DependencyTable({
  project,
  onChange,
  onStatus,
  onRescan,
  nodeId,
}: {
  project: StoredProject;
  nodeId: string;
  onChange: (project: StoredProject) => void;
  onStatus: (text: string) => void;
  onRescan: () => Promise<void>;
}) {
  const latest = useRef(project);
  latest.current = project;
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [candidates, setCandidates] = useState<Record<string, string[]>>({});
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [statuses, setStatuses] = useState<Record<string, string>>({});
  const [refresh, setRefresh] = useState(0);
  const effectiveDependencies = overrideInputs(project, nodeId).map(d => {
    const rule = project.nodes.find(n => n.id === nodeId)?.data.assetOverrides?.find(o => o.originalPath === d.path);
    const replacement = project.dependencies.find(r => r.id === rule?.replacementDependencyId);
    return {...d, path: replacement?.path || d.path};
  });
  const signature = JSON.stringify(effectiveDependencies);
  useEffect(() => {
    if (!isTauri()) return;
    let active = true;
    setStatuses({});
    invoke<{ id: string; status: string }[]>("dependency_statuses", {
      dependencies: effectiveDependencies,
    })
      .then((rows) => {
        if (active)
          setStatuses(
            Object.fromEntries(rows.map((row) => [row.id, row.status])),
          );
      })
      .catch((error) => {
        if (active) onStatus(String(error));
      });
    return () => {
      active = false;
    };
  }, [signature, refresh, onStatus]);
  async function run(task: () => Promise<void>) {
    setBusy(true);
    try {
      await task();
    } catch (error) {
      onStatus(String(error));
    } finally {
      setBusy(false);
    }
  }
  async function relink(id: string) {
    const dependency = project.dependencies.find((d) => d.id === id);
    if (!dependency) return;
    const { open } = await import("@tauri-apps/plugin-dialog");
    const path = await open({
      multiple: false,
      title: "Relink to an original file",
      defaultPath: dependency.path,
    });
    if (!path) return;
    const existingId = await invoke<string | null>("find_dependency", {
      path,
      dependencies: project.dependencies.filter((d) => d.id !== id),
    });
    const replacement =
      project.dependencies.find((d) => d.id === existingId)?.path || path;
    const next = overrideDependency(project, nodeId, id, replacement);
    onChange(next);
    await onRescan();
    onStatus(
      existingId
        ? `Warning: file is already registered. References merged into ${existingId}; this branch reuses that file link.`
        : "Replacement applied to this branch. External files were not changed.",
    );
  }
  useEffect(() => {
    if (isTauri()) void run(onRescan);
  }, []);
  async function useMatch(id: string, path: string) {
    const current = latest.current;
    const existingId = await invoke<string | null>("find_dependency", {
      path,
      dependencies: current.dependencies.filter((d) => d.id !== id),
    });
    const replacement =
      current.dependencies.find((d) => d.id === existingId)?.path || path;
    if (!latest.current.dependencies.some((d) => d.id === id)) return;
    onChange(overrideDependency(latest.current, nodeId, id, replacement));
    setCandidates((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
    await onRescan();
    onStatus(
      existingId
        ? "Warning: matched an already registered file; the branch reuses its file link."
        : "Relinked to the chosen file. Originals were not modified.",
    );
  }
  async function findMissing() {
    const missing = effectiveDependencies.filter(
      (d) =>
        selected.has(d.id) &&
        !["Available", "Directory"].includes(statuses[d.id]),
    );
    if (!missing.length) {
      onStatus("No missing files among the selected rows.");
      return;
    }
    const { open } = await import("@tauri-apps/plugin-dialog");
    const folder = await open({
      directory: true,
      multiple: false,
      title: "Search this folder and its subfolders for missing files",
    });
    if (!folder) return;
    const result = await invoke<{
      matches: { id: string; candidates: string[] }[];
      scannedEntries: number;
      skippedFolders: number;
      truncated: boolean;
    }>("find_missing_dependencies", { folder, dependencies: missing });
    const pending: Record<string, string[]> = {};
    const replacements: { id: string; path: string }[] = [];
    const partial = result.truncated || result.skippedFolders > 0;
    let unmatched = 0;
    for (const match of result.matches) {
      if (match.candidates.length === 1 && !partial) {
        const existingId = await invoke<string | null>("find_dependency", {
          path: match.candidates[0],
          dependencies: latest.current.dependencies.filter(
            (d) => d.id !== match.id,
          ),
        });
        replacements.push({
          id: match.id,
          path:
            latest.current.dependencies.find((d) => d.id === existingId)
              ?.path || match.candidates[0],
        });
      } else if (match.candidates.length) {
        pending[match.id] = match.candidates;
      } else unmatched++;
    }
    let next = latest.current;
    for (const replacement of replacements)
      if (next.dependencies.some((d) => d.id === replacement.id))
        next = overrideDependency(next, nodeId, replacement.id, replacement.path);
    if (replacements.length) onChange(next);
    setCandidates(pending);
    setChoices({});
    if (replacements.length) await onRescan();
    setRefresh((v) => v + 1);
    onStatus(
      `${partial ? "Warning: search was incomplete. " : ""}Relinked ${replacements.length} unique match(es); ${Object.keys(pending).length} need your choice; ${unmatched} not found. Searched ${result.scannedEntries} entries; skipped ${result.skippedFolders} folder(s).`,
    );
  }
  const rows = useMemo(
    () =>
      overrideInputs(project, nodeId)
        .map((dependency) => {
          const name = dependency.path.split(/[\\/]/).pop() || dependency.path;
          const extension = name.includes(".")
            ? name.split(".").pop()!.toUpperCase()
            : "—";
          const nodes = referencedBy(project, dependency.id);
          return { dependency, name, extension, nodes };
        })
        .filter((row) =>
          [
            row.name,
            row.dependency.path,
            row.dependency.kind,
            ...row.nodes.map((n) => n.data.label),
          ]
            .join(" ")
            .toLowerCase()
            .includes(query.toLowerCase()),
        ),
    [project, query, nodeId],
  );
  return (
    <section className="dependencies-tab">
      <div className="dependency-tools">
        <div>
          <h1>Asset overrides</h1>
          <p>
            Replacements affect this node and its downstream outputs. Original files stay unchanged.
          </p>
        </div>
        <input
          aria-label="Search dependencies"
          placeholder="Search files, paths or nodes…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button
          disabled={busy || !isTauri()}
          onClick={() => setRefresh((v) => v + 1)}
        >
          Check files
        </button>
        <button disabled={busy || !isTauri()} onClick={() => run(onRescan)}>
          Rescan USD
        </button>
        <button
          disabled={busy || !isTauri() || !selected.size}
          onClick={() => run(findMissing)}
        >
          Find Missing ({selected.size})
        </button>
      </div>
      <div className="dependency-grid">
        <table>
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  aria-label="Select all visible dependencies"
                  disabled={busy || !rows.length}
                  checked={
                    rows.length > 0 &&
                    rows.every((row) => selected.has(row.dependency.id))
                  }
                  onChange={(e) =>
                    setSelected((prev) => {
                      const next = new Set(prev);
                      for (const row of rows)
                        e.target.checked
                          ? next.add(row.dependency.id)
                          : next.delete(row.dependency.id);
                      return next;
                    })
                  }
                />
              </th>
              <th>Filename</th>
              <th>File type</th>
              <th>Category</th>
              <th>Original location</th>
              <th>Replacement</th>
              <th>Referenced by node(s)</th>
              <th>Availability</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ dependency, name, extension, nodes }) => (
              <tr key={dependency.id}>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`Select ${name}`}
                    disabled={busy}
                    checked={selected.has(dependency.id)}
                    onChange={(e) =>
                      setSelected((prev) => {
                        const next = new Set(prev);
                        e.target.checked
                          ? next.add(dependency.id)
                          : next.delete(dependency.id);
                        return next;
                      })
                    }
                  />
                </td>
                <td title={dependency.id}>{name}</td>
                <td>{extension}</td>
                <td>
                  {dependency.kind === "nativeProject"
                    ? "Native project"
                    : dependency.kind === "usd"
                      ? "USD"
                      : "Asset"}
                </td>
                <td className="file-location" title={dependency.path}>
                  {dependency.path}
                </td>
                <td className="file-location">{project.dependencies.find(d => d.id === project.nodes.find(n => n.id === nodeId)?.data.assetOverrides?.find(o => o.originalPath === dependency.path)?.replacementDependencyId)?.path || "Original"}
                  <button onClick={() => onChange({...project, nodes: project.nodes.map(n => n.id !== nodeId ? n : {...n, data: {...n.data, assetOverrides: (n.data.assetOverrides || []).filter(o => o.originalPath !== dependency.path)}})})}>Reset</button>
                </td>
                <td>
                  <div>
                    {nodes.length
                      ? nodes.map((n) => n.data.label).join(", ")
                      : "Project only"}
                  </div>
                </td>
                <td
                  className={
                    statuses[dependency.id] === "Missing" ? "missing-file" : ""
                  }
                >
                  {statuses[dependency.id] ||
                    (isTauri() ? "Checking…" : "Desktop required")}
                </td>
                <td>
                  <button
                    disabled={busy || !isTauri()}
                    onClick={() => run(() => relink(dependency.id))}
                  >
                    Relink…
                  </button>
                  {!!candidates[dependency.id]?.length && (
                    <div className="candidate-match">
                      <select
                        aria-label={`Choose located file for ${name}`}
                        value={choices[dependency.id] || ""}
                        onChange={(e) =>
                          setChoices((prev) => ({
                            ...prev,
                            [dependency.id]: e.target.value,
                          }))
                        }
                      >
                        <option value="">
                          Choose match ({candidates[dependency.id].length})…
                        </option>
                        {candidates[dependency.id].map((path) => (
                          <option key={path} value={path}>
                            {path}
                          </option>
                        ))}
                      </select>
                      <button
                        disabled={busy || !choices[dependency.id]}
                        onClick={() =>
                          run(() =>
                            useMatch(dependency.id, choices[dependency.id]),
                          )
                        }
                      >
                        Use match
                      </button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!rows.length && (
          <p className="dependency-empty">
            {project.dependencies.length
              ? "No matching dependencies."
              : "No upstream dependencies. Connect a source and rescan USD."}
          </p>
        )}
      </div>
      <p className="dependency-note">
        {rows.length} of {project.dependencies.length} files · Relinking updates
        only this node's downstream branch. It does not rewrite references
        embedded inside external USD or application files.
      </p>
    </section>
  );
}
