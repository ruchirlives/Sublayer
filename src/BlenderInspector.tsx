import { useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  registerFiles,
  type BlenderObjectInfo,
  type StoredProject,
} from "./fileRegistry";

interface BlenderInspectorProps {
  project: StoredProject;
  nodeId: string;
  onProjectChange: (project: StoredProject) => void;
  onStatus: (message: string) => void;
}

interface SceneStatRow {
  name: string;
  isActiveScene: boolean;
  totalCols: number;
  visibleCols: number;
  selectedCols: number;
  allNames: string[];
  visibleNames: string[];
  selectedCount: number;
  totalCount: number;
}

interface CollectionStatRow {
  key: string;
  scene: string;
  name: string;
  sceneVisible: boolean;
  totalObjects: number;
  selectedObjects: number;
  vertices: number;
  polygons: number;
  objectNames: string[];
  visibleObjectNames: string[];
}

function getDefaultSceneVisibleSelections(objs: BlenderObjectInfo[]): string[] {
  const hasActiveFlag = objs.some((o) => o.activeScene === true);
  const pool = hasActiveFlag ? objs.filter((o) => o.activeScene === true) : objs;
  const visible = pool.filter((o) => o.sceneVisible !== false).map((o) => o.name);
  return visible.length > 0
    ? Array.from(new Set(visible))
    : Array.from(new Set(pool.map((o) => o.name)));
}

function getPreferredSceneName(objs: BlenderObjectInfo[]): string | null {
  const active = objs.find((o) => o.activeScene === true && o.scene);
  if (active?.scene) return active.scene;
  const first = objs.find((o) => o.scene);
  return first?.scene ?? null;
}

export function BlenderInspector({
  project,
  nodeId,
  onProjectChange,
  onStatus,
}: BlenderInspectorProps) {
  const node = project.nodes.find((n) => n.id === nodeId);
  const objects: BlenderObjectInfo[] = node?.data.blenderObjects || [];

  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [customUsdPath, setCustomUsdPath] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [sceneFilter, setSceneFilter] = useState<string | null>(() =>
    getPreferredSceneName(objects)
  );

  const [pendingSelections, setPendingSelections] = useState<string[] | null>(null);
  const debounceRef = useRef<number | null>(null);
  const pendingSelectionsRef = useRef<string[] | null>(null);
  const projectRef = useRef(project);
  projectRef.current = project;
  const onProjectChangeRef = useRef(onProjectChange);
  onProjectChangeRef.current = onProjectChange;
  const nodeIdRef = useRef(nodeId);
  const autoUpgradedNodesRef = useRef(new Set<string>());

  const blendDep = project.dependencies.find(
    (d) => d.id === node?.data.nativeProjectDependencyId
  );
  const blendPath = blendDep?.path || "";

  function commitSelections(targetNodeId: string, next: string[]): StoredProject {
    const currentProject = projectRef.current;
    const updatedProject: StoredProject = {
      ...currentProject,
      nodes: currentProject.nodes.map((n) =>
        n.id !== targetNodeId
          ? n
          : {
              ...n,
              data: {
                ...n.data,
                blenderSelections: next,
              },
            }
      ),
    };
    projectRef.current = updatedProject;
    pendingSelectionsRef.current = null;
    onProjectChangeRef.current(updatedProject);
    return updatedProject;
  }

  function flushPendingSelections(): StoredProject {
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
      debounceRef.current = null;
      if (pendingSelectionsRef.current !== null) {
        return commitSelections(nodeIdRef.current, pendingSelectionsRef.current);
      }
    }
    return projectRef.current;
  }

  useEffect(() => {
    if (nodeIdRef.current !== nodeId) {
      flushPendingSelections();
      nodeIdRef.current = nodeId;
      setSceneFilter(getPreferredSceneName(objects));
    }
    setPendingSelections(null);
    pendingSelectionsRef.current = null;
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
  }, [nodeId, node?.data.blenderSelections]);

  useEffect(() => {
    return () => {
      flushPendingSelections();
    };
  }, []);

  useEffect(() => {
    if (
      blendPath &&
      !loading &&
      objects.length > 0 &&
      objects.every((o) => !o.scene) &&
      !autoUpgradedNodesRef.current.has(nodeId)
    ) {
      autoUpgradedNodesRef.current.add(nodeId);
      void rescanBlend(true);
    }
  }, [nodeId, blendPath, loading, objects]);

  const selections: string[] = pendingSelections ?? (node?.data.blenderSelections || []);
  const selectedSet = useMemo(() => new Set(selections), [selections]);

  const sceneStats: SceneStatRow[] = useMemo(() => {
    const map = new Map<
      string,
      {
        name: string;
        isActiveScene: boolean;
        collections: Set<string>;
        visibleCollections: Set<string>;
        selectedCollections: Set<string>;
        allNamesSet: Set<string>;
        visibleNamesSet: Set<string>;
      }
    >();
    for (const o of objects) {
      const sName = o.scene || "Scene";
      let entry = map.get(sName);
      if (!entry) {
        entry = {
          name: sName,
          isActiveScene: o.activeScene === true,
          collections: new Set(),
          visibleCollections: new Set(),
          selectedCollections: new Set(),
          allNamesSet: new Set(),
          visibleNamesSet: new Set(),
        };
        map.set(sName, entry);
      } else if (o.activeScene === true) {
        entry.isActiveScene = true;
      }
      entry.allNamesSet.add(o.name);
      if (o.sceneVisible !== false) entry.visibleNamesSet.add(o.name);
      const cols = o.collections && o.collections.length > 0 ? o.collections : ["Scene Collection"];
      const isSelected = selectedSet.has(o.name);
      for (const colName of cols) {
        entry.collections.add(colName);
        if (o.sceneVisible !== false) entry.visibleCollections.add(colName);
        if (isSelected) entry.selectedCollections.add(colName);
      }
    }
    return Array.from(map.values()).map((entry) => {
      const allNames = Array.from(entry.allNamesSet);
      const visibleNames = Array.from(entry.visibleNamesSet);
      const selectedCount = allNames.filter((n) => selectedSet.has(n)).length;
      return {
        name: entry.name,
        isActiveScene: entry.isActiveScene,
        totalCols: entry.collections.size,
        visibleCols: entry.visibleCollections.size,
        selectedCols: entry.selectedCollections.size,
        allNames,
        visibleNames,
        selectedCount,
        totalCount: allNames.length,
      };
    });
  }, [objects, selectedSet]);

  const effectiveSceneFilter =
    sceneFilter && sceneStats.some((s) => s.name === sceneFilter) ? sceneFilter : null;

  const collectionStats: CollectionStatRow[] = useMemo(() => {
    const map = new Map<
      string,
      {
        key: string;
        scene: string;
        name: string;
        sceneVisible: boolean;
        seenObjects: Set<string>;
        selectedObjects: Set<string>;
        visibleObjectNames: Set<string>;
        vertices: number;
        polygons: number;
      }
    >();
    for (const o of objects) {
      const sName = o.scene || "Scene";
      if (effectiveSceneFilter && sName !== effectiveSceneFilter) continue;
      const cols = o.collections && o.collections.length > 0 ? o.collections : ["Scene Collection"];
      const isSelected = selectedSet.has(o.name);
      const isVisible = o.sceneVisible !== false;
      for (const colName of cols) {
        const key = `${sName}::${colName}`;
        let entry = map.get(key);
        if (!entry) {
          entry = {
            key,
            scene: sName,
            name: colName,
            sceneVisible: isVisible,
            seenObjects: new Set(),
            selectedObjects: new Set(),
            visibleObjectNames: new Set(),
            vertices: 0,
            polygons: 0,
          };
          map.set(key, entry);
        }
        if (isVisible) {
          entry.sceneVisible = true;
          entry.visibleObjectNames.add(o.name);
        }
        if (!entry.seenObjects.has(o.name)) {
          entry.seenObjects.add(o.name);
          entry.vertices += o.vertices || 0;
          entry.polygons += o.polygons || 0;
          if (isSelected) entry.selectedObjects.add(o.name);
        }
      }
    }
    return Array.from(map.values())
      .map((entry) => ({
        key: entry.key,
        scene: entry.scene,
        name: entry.name,
        sceneVisible: entry.sceneVisible,
        totalObjects: entry.seenObjects.size,
        selectedObjects: entry.selectedObjects.size,
        vertices: entry.vertices,
        polygons: entry.polygons,
        objectNames: Array.from(entry.seenObjects),
        visibleObjectNames: Array.from(entry.visibleObjectNames),
      }))
      .sort((a, b) => {
        if (a.sceneVisible !== b.sceneVisible) return a.sceneVisible ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
  }, [objects, effectiveSceneFilter, selectedSet]);

  const filteredCollections = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return collectionStats;
    return collectionStats.filter(
      (c) => c.name.toLowerCase().includes(q) || c.scene.toLowerCase().includes(q)
    );
  }, [collectionStats, searchQuery]);

  if (!node) return null;

  const usdDep = project.dependencies.find(
    (d) => d.id === node.data.publishedDependencyId
  );
  const existingUsdPath = usdDep?.path || "";

  // Propose default USD path next to the .blend file if not yet set
  const defaultUsdPath = blendPath
    ? blendPath.replace(/\.blend$/i, ".usda")
    : "";
  const targetUsdPath = customUsdPath || existingUsdPath || defaultUsdPath;

  const selectedCollectionsCount = collectionStats.filter((c) => c.selectedObjects > 0).length;
  const allFilteredColsSelected =
    filteredCollections.length > 0 &&
    filteredCollections.every((c) => c.selectedObjects === c.totalObjects && c.totalObjects > 0);
  const someFilteredColsSelected =
    !allFilteredColsSelected && filteredCollections.some((c) => c.selectedObjects > 0);

  function updateSelections(next: string[]) {
    pendingSelectionsRef.current = next;
    setPendingSelections(next);
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
    }
    debounceRef.current = window.setTimeout(() => {
      debounceRef.current = null;
      commitSelections(nodeIdRef.current, next);
    }, 150);
  }

  function handleSelectScene(sceneRow: SceneStatRow) {
    setSceneFilter(sceneRow.name);
    const sceneNameSet = new Set(sceneRow.allNames);
    const hasOtherSceneSelections = selections.some((n) => !sceneNameSet.has(n));
    if (sceneRow.selectedCount === 0 || hasOtherSceneSelections) {
      const toSelect =
        sceneRow.visibleNames.length > 0 ? sceneRow.visibleNames : sceneRow.allNames;
      updateSelections(toSelect);
    }
  }

  function handleToggleScene(sceneRow: SceneStatRow) {
    setSceneFilter(sceneRow.name);
    const sceneNameSet = new Set(sceneRow.allNames);
    const hasOtherSceneSelections = selections.some((n) => !sceneNameSet.has(n));
    if (sceneRow.selectedCount > 0 && !hasOtherSceneSelections) {
      updateSelections([]);
    } else {
      const toSelect =
        sceneRow.visibleNames.length > 0 ? sceneRow.visibleNames : sceneRow.allNames;
      updateSelections(toSelect);
    }
  }

  function handleToggleCollection(colRow: CollectionStatRow) {
    const allChecked =
      colRow.selectedObjects === colRow.totalObjects && colRow.totalObjects > 0;
    const next = new Set(selections);
    if (allChecked) {
      for (const name of colRow.objectNames) next.delete(name);
    } else {
      for (const name of colRow.objectNames) next.add(name);
    }
    updateSelections(Array.from(next));
  }

  function handleMasterCollectionToggle() {
    if (allFilteredColsSelected) {
      handleSelectNone();
    } else {
      handleSelectAll();
    }
  }

  function handleSelectSceneVisible() {
    const next = new Set<string>();
    const activeSceneRow = sceneStats.find((s) => s.isActiveScene);
    const targetScene = effectiveSceneFilter ?? activeSceneRow?.name ?? null;
    for (const col of collectionStats) {
      if (targetScene && col.scene !== targetScene) continue;
      if (!col.sceneVisible) continue;
      for (const name of col.visibleObjectNames) next.add(name);
    }
    updateSelections(Array.from(next));
  }

  function handleSelectAll() {
    const next = new Set(selections);
    for (const col of filteredCollections) {
      for (const name of col.objectNames) next.add(name);
    }
    updateSelections(Array.from(next));
  }

  function handleSelectNone() {
    const next = new Set(selections);
    for (const col of filteredCollections) {
      for (const name of col.objectNames) next.delete(name);
    }
    updateSelections(Array.from(next));
  }

  function handleInvertSelection() {
    const scopedNames = new Set<string>();
    for (const col of filteredCollections) {
      for (const name of col.objectNames) scopedNames.add(name);
    }
    const next = new Set(selections);
    for (const name of scopedNames) {
      if (next.has(name)) next.delete(name);
      else next.add(name);
    }
    updateSelections(Array.from(next));
  }

  async function handleBrowseBlend() {
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const selected = await open({
        multiple: false,
        directory: false,
        filters: [{ name: "Blender File", extensions: ["blend"] }],
      });
      if (typeof selected !== "string" || !selected) return;

      setLoading(true);
      onStatus(`Inspecting Blender file: ${selected}...`);

      const { dependencies, ids } = registerFiles(
        project.dependencies,
        [selected],
        "nativeProject"
      );

      // Run inspect via background Blender
      const discovered = await invoke<BlenderObjectInfo[]>(
        "inspect_blender_file",
        { blendPath: selected }
      );

      const defaultSelected = getDefaultSceneVisibleSelections(discovered);
      const preferredScene = getPreferredSceneName(discovered);
      setSceneFilter(preferredScene);

      const updatedProject: StoredProject = {
        ...project,
        dependencies,
        nodes: project.nodes.map((n) =>
          n.id !== nodeId
            ? n
            : {
                ...n,
                data: {
                  ...n.data,
                  nativeProjectDependencyId: ids[0] || null,
                  blenderObjects: discovered,
                  blenderSelections: defaultSelected,
                },
              }
        ),
      };

      onProjectChange(updatedProject);
      const sceneCount = new Set(discovered.map((o) => o.scene || "Scene")).size;
      onStatus(
        `Discovered ${discovered.length} object(s) across ${sceneCount} scene(s) (${defaultSelected.length} visible selected)`
      );
    } catch (err) {
      onStatus(`Error inspecting .blend file: ${String(err)}`);
    } finally {
      setLoading(false);
    }
  }

  async function rescanBlend(resetToSceneVisible = false) {
    if (!blendPath) return;
    setLoading(true);
    onStatus(`Rescanning ${blendPath}...`);
    try {
      const discovered = await invoke<BlenderObjectInfo[]>(
        "inspect_blender_file",
        { blendPath }
      );

      const currentProject = projectRef.current;
      const currentNode = currentProject.nodes.find((n) => n.id === nodeId);
      const currentSelections =
        pendingSelectionsRef.current ?? (currentNode?.data.blenderSelections || []);
      const hasExistingConfig = currentNode?.data.blenderSelections !== undefined;
      const existingSet = new Set(currentSelections);
      const validExisting = discovered
        .filter((o) => existingSet.has(o.name))
        .map((o) => o.name);
      const updatedSelections =
        !resetToSceneVisible && hasExistingConfig && validExisting.length > 0
          ? Array.from(new Set(validExisting))
          : getDefaultSceneVisibleSelections(discovered);

      if (resetToSceneVisible || !effectiveSceneFilter) {
        setSceneFilter(getPreferredSceneName(discovered));
      }

      const updatedProject: StoredProject = {
        ...currentProject,
        nodes: currentProject.nodes.map((n) =>
          n.id !== nodeId
            ? n
            : {
                ...n,
                data: {
                  ...n.data,
                  blenderObjects: discovered,
                  blenderSelections: updatedSelections,
                },
              }
        ),
      };

      onProjectChangeRef.current(updatedProject);
      onStatus(`Found ${discovered.length} object(s) in .blend file.`);
    } catch (err) {
      onStatus(`Rescan failed: ${String(err)}`);
    } finally {
      setLoading(false);
    }
  }

  function handleRescan() {
    void rescanBlend(false);
  }

  async function handleBrowseUsdDestination() {
    try {
      const { save } = await import("@tauri-apps/plugin-dialog");
      const selected = await save({
        filters: [{ name: "Universal Scene Description", extensions: ["usda", "usd", "usdc"] }],
        defaultPath: targetUsdPath || defaultUsdPath,
      });
      if (typeof selected === "string" && selected) {
        setCustomUsdPath(selected);
      }
    } catch (err) {
      onStatus(String(err));
    }
  }

  async function handleExportUsd() {
    if (!blendPath) {
      onStatus("Please select a .blend file first.");
      return;
    }
    const finalUsdPath = targetUsdPath || defaultUsdPath;
    if (!finalUsdPath) {
      onStatus("Please choose a destination USD filepath.");
      return;
    }

    const activeSelections = pendingSelectionsRef.current ?? selections;
    const baseProject = flushPendingSelections();

    setExporting(true);
    onStatus(`Exporting ${activeSelections.length} object(s) to USD via Blender...`);
    try {
      await invoke("export_blender_to_usd", {
        blendPath,
        usdPath: finalUsdPath,
        selectedObjects: activeSelections,
      });

      // Register the exported USD in dependencies
      const { dependencies, ids } = registerFiles(
        baseProject.dependencies,
        [finalUsdPath],
        "usd"
      );

      const updatedProject: StoredProject = {
        ...baseProject,
        dependencies,
        nodes: baseProject.nodes.map((n) =>
          n.id !== nodeId
            ? n
            : {
                ...n,
                data: {
                  ...n.data,
                  blenderSelections: activeSelections,
                  publishedDependencyId: ids[0] || null,
                },
              }
        ),
      };

      onProjectChange(updatedProject);
      onStatus(
        `Exported ${activeSelections.length} object(s) to ${finalUsdPath.split(/[\\/]/).pop()} successfully!`
      );
    } catch (err) {
      onStatus(`USD export failed: ${String(err)}`);
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="node-options blender-inspector" style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
      <div>
        <strong style={{ fontSize: "12px" }}>Blender Importer</strong>
        <p style={{ margin: "2px 0 6px", fontSize: "11px", color: "#8b9c96" }}>
          Import geometry from a .blend file and export selected objects to a USD stage.
        </p>
      </div>

      {/* 1. Blender File Picker */}
      <div style={{ background: "#162329", border: "1px solid #283a42", borderRadius: "6px", padding: "8px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
          <span style={{ fontSize: "11px", fontWeight: "bold" }}>Blender File (.blend)</span>
          {blendPath && (
            <button
              onClick={handleRescan}
              disabled={loading}
              style={{ fontSize: "10px", padding: "2px 6px" }}
              title="Rescan objects from .blend"
            >
              {loading ? "Scanning…" : "↻ Rescan"}
            </button>
          )}
        </div>
        <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
          <input
            type="text"
            readOnly
            value={blendPath}
            placeholder="No .blend file selected"
            style={{ flex: 1, fontSize: "11px", padding: "4px 6px", background: "#0e181c", color: "#d8e2dc" }}
          />
          <button
            onClick={handleBrowseBlend}
            disabled={loading}
            style={{ fontSize: "11px", padding: "4px 8px", whiteSpace: "nowrap" }}
          >
            {blendPath ? "Change…" : "Browse…"}
          </button>
        </div>
        {blendPath && (
          <label style={{ margin: "8px 0 0", gap: "4px", fontSize: "11px" }}>
            Material conversion
            <select
              aria-label="Blender material conversion"
              value={node?.data.blenderMaterialMode || "off"}
              onChange={(e) => {
                const mode = e.target.value as "off" | "auto" | "octane" | "cycles";
                const base = flushPendingSelections();
                onProjectChange({
                  ...base,
                  nodes: base.nodes.map((n) =>
                    n.id !== nodeId ? n : { ...n, data: { ...n.data, blenderMaterialMode: mode } }
                  ),
                });
              }}
              style={{ fontSize: "11px", padding: "4px 6px" }}
            >
              <option value="off">Preserve Blender USD export</option>
              <option value="auto">Convert Cycles / Octane → MaterialX (Auto)</option>
              <option value="octane">Convert → MaterialX (Prefer Octane)</option>
              <option value="cycles">Convert → MaterialX (Prefer Cycles)</option>
            </select>
          </label>
        )}
        {blendPath && (node?.data.blenderMaterialMode || "off") !== "off" && (
          <label
            style={{
              margin: "6px 0 0",
              display: "flex",
              flexDirection: "row",
              alignItems: "center",
              gap: "6px",
              fontSize: "11px",
              cursor: "pointer",
            }}
          >
            <input
              type="checkbox"
              aria-label="Convert Blender procedurals to MaterialX"
              checked={Boolean(node?.data.blenderConvertProcedurals)}
              onChange={(e) => {
                const checked = e.target.checked;
                const base = flushPendingSelections();
                onProjectChange({
                  ...base,
                  nodes: base.nodes.map((n) =>
                    n.id !== nodeId ? n : { ...n, data: { ...n.data, blenderConvertProcedurals: checked } }
                  ),
                });
              }}
            />
            <span>Convert procedurals (Cycles / Octane → MaterialX)</span>
          </label>
        )}
        {blendPath && (
          <label
            style={{
              margin: "6px 0 0",
              display: "flex",
              alignItems: "center",
              gap: "6px",
              fontSize: "11px",
              cursor: "pointer",
            }}
          >
            <input
              type="checkbox"
              aria-label="Export skeletons (UsdSkel)"
              checked={Boolean(node?.data.blenderExportSkeletons)}
              onChange={(e) => {
                const checked = e.target.checked;
                const base = flushPendingSelections();
                onProjectChange({
                  ...base,
                  nodes: base.nodes.map((n) =>
                    n.id !== nodeId ? n : { ...n, data: { ...n.data, blenderExportSkeletons: checked } }
                  ),
                });
              }}
            />
            <span>Export skeletons (UsdSkel)</span>
          </label>
        )}
      </div>

      {/* 2. Scene & Collection Selection Tables */}
      {blendPath && (
        <div style={{ background: "#162329", border: "1px solid #283a42", borderRadius: "6px", padding: "8px" }}>
          {sceneStats.length > 0 && (
            <div style={{ marginBottom: "8px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
                <span style={{ fontSize: "11px", fontWeight: "bold" }}>
                  Scenes ({sceneStats.length})
                </span>
                {effectiveSceneFilter && sceneStats.length > 1 && (
                  <button
                    onClick={() => setSceneFilter(null)}
                    style={{ fontSize: "10px", padding: "1px 6px" }}
                    title="Show collections from all scenes"
                  >
                    All scenes
                  </button>
                )}
              </div>
              <div
                style={{
                  maxHeight: "110px",
                  overflowY: "auto",
                  border: "1px solid #1f323a",
                  borderRadius: "4px",
                  background: "#0c1519",
                }}
              >
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "11px" }}>
                  <thead>
                    <tr style={{ background: "#111f25", color: "#8b9c96", textAlign: "left", borderBottom: "1px solid #1f323a" }}>
                      <th style={{ padding: "4px 6px", width: "20px" }}></th>
                      <th style={{ padding: "4px 6px" }}>Scene</th>
                      <th style={{ padding: "4px 6px", textAlign: "right" }}>Collections</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sceneStats.map((s) => {
                      const targetCount = s.visibleNames.length > 0 ? s.visibleNames.length : s.allNames.length;
                      const allSceneChecked = s.selectedCount > 0 && s.selectedCount >= targetCount;
                      const sceneIndeterminate = s.selectedCount > 0 && s.selectedCount < targetCount;
                      const isFiltering = effectiveSceneFilter === s.name;
                      return (
                        <tr
                          key={s.name}
                          style={{
                            borderBottom: "1px solid #132127",
                            background: isFiltering ? "#1c333d" : "transparent",
                          }}
                        >
                          <td style={{ padding: "3px 6px", textAlign: "center" }}>
                            <input
                              type="checkbox"
                              ref={(el) => {
                                if (el) el.indeterminate = sceneIndeterminate;
                              }}
                              checked={allSceneChecked}
                              onChange={() => handleToggleScene(s)}
                              title={`Toggle visible collections in ${s.name}`}
                              style={{ margin: 0, cursor: "pointer", accentColor: "#9fe2b6" }}
                            />
                          </td>
                          <td
                            onClick={() => handleSelectScene(s)}
                            style={{
                              padding: "3px 6px",
                              cursor: "pointer",
                              color: isFiltering ? "#7cc6fe" : "#d8e2dc",
                              fontWeight: isFiltering ? "bold" : "normal",
                            }}
                            title={`Switch to scene ${s.name} and filter its visible collections`}
                          >
                            {s.name}
                            {s.isActiveScene && (
                              <span
                                style={{
                                  marginLeft: "6px",
                                  fontSize: "9px",
                                  color: "#9fe2b6",
                                  background: "#152b22",
                                  border: "1px solid #284c3c",
                                  borderRadius: "3px",
                                  padding: "0px 4px",
                                }}
                              >
                                active
                              </span>
                            )}
                          </td>
                          <td style={{ padding: "3px 6px", textAlign: "right", color: "#8b9c96", fontVariantNumeric: "tabular-nums" }}>
                            {s.selectedCols}/{s.totalCols} ({s.visibleCols} visible)
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px", gap: "4px", flexWrap: "wrap" }}>
            <span style={{ fontSize: "11px", fontWeight: "bold" }}>
              Collections ({selectedCollectionsCount} / {collectionStats.length} selected)
            </span>
            <div style={{ display: "flex", gap: "4px" }}>
              <button
                onClick={handleSelectSceneVisible}
                style={{ fontSize: "10px", padding: "2px 5px" }}
                title="Select only collections visible in the scene"
              >
                Scene Visible
              </button>
              <button
                onClick={handleSelectAll}
                style={{ fontSize: "10px", padding: "2px 5px" }}
                title="Select all listed collections"
              >
                All
              </button>
              <button
                onClick={handleSelectNone}
                style={{ fontSize: "10px", padding: "2px 5px" }}
                title="Deselect listed collections"
              >
                None
              </button>
              <button
                onClick={handleInvertSelection}
                style={{ fontSize: "10px", padding: "2px 5px" }}
                title="Invert listed collection selection"
              >
                Invert
              </button>
            </div>
          </div>

          {collectionStats.length > 3 && (
            <div style={{ display: "flex", gap: "4px", alignItems: "center", marginBottom: "6px" }}>
              <input
                type="text"
                placeholder="Filter collections…"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                style={{
                  flex: 1,
                  fontSize: "11px",
                  padding: "3px 6px",
                  background: "#0c1519",
                  color: "#d8e2dc",
                  border: "1px solid #1f323a",
                  borderRadius: "4px",
                }}
              />
              {searchQuery && (
                <button
                  onClick={() => setSearchQuery("")}
                  style={{ fontSize: "10px", padding: "2px 6px" }}
                  title="Clear collection search"
                >
                  ✕
                </button>
              )}
            </div>
          )}

          {collectionStats.length === 0 ? (
            <p style={{ margin: "4px 0", fontSize: "11px", color: "#8b9c96" }}>
              {loading ? "Discovering scenes and collections in .blend..." : "No exportable collections found. Click Rescan."}
            </p>
          ) : (
            <div
              style={{
                maxHeight: "220px",
                overflowY: "auto",
                background: "#0c1519",
                borderRadius: "4px",
                border: "1px solid #1f323a",
              }}
            >
              <table
                style={{
                  width: "100%",
                  borderCollapse: "collapse",
                  fontSize: "11px",
                  tableLayout: "fixed",
                }}
              >
                <thead
                  style={{
                    position: "sticky",
                    top: 0,
                    background: "#16252b",
                    zIndex: 1,
                    borderBottom: "1px solid #283a42",
                  }}
                >
                  <tr>
                    <th style={{ width: "26px", padding: "4px 4px 4px 6px", textAlign: "center" }}>
                      <input
                        type="checkbox"
                        ref={(el) => {
                          if (el) el.indeterminate = someFilteredColsSelected && !allFilteredColsSelected;
                        }}
                        checked={allFilteredColsSelected}
                        onChange={handleMasterCollectionToggle}
                        style={{ cursor: "pointer", accentColor: "#9fe2b6" }}
                        title={allFilteredColsSelected ? "Deselect all" : "Select all"}
                      />
                    </th>
                    <th style={{ padding: "4px 6px", textAlign: "left", color: "#a5b8b0", fontWeight: 600 }}>
                      Collection
                    </th>
                    <th style={{ width: "62px", padding: "4px 4px", textAlign: "left", color: "#a5b8b0", fontWeight: 600 }}>
                      Scene State
                    </th>
                    <th style={{ width: "42px", padding: "4px 4px", textAlign: "right", color: "#a5b8b0", fontWeight: 600 }}>
                      Objs
                    </th>
                    <th style={{ width: "52px", padding: "4px 6px 4px 4px", textAlign: "right", color: "#a5b8b0", fontWeight: 600 }}>
                      Faces
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {filteredCollections.length === 0 ? (
                    <tr>
                      <td colSpan={5} style={{ padding: "12px", textAlign: "center", color: "#6e847c" }}>
                        No collections match "{searchQuery}"
                      </td>
                    </tr>
                  ) : (
                    filteredCollections.map((col) => {
                      const checked = col.selectedObjects === col.totalObjects && col.totalObjects > 0;
                      const partial = col.selectedObjects > 0 && col.selectedObjects < col.totalObjects;
                      return (
                        <tr
                          key={col.key}
                          onClick={() => handleToggleCollection(col)}
                          style={{
                            cursor: "pointer",
                            background: col.selectedObjects > 0 ? "#152a28" : "transparent",
                            borderBottom: "1px solid #142227",
                          }}
                          onMouseEnter={(e) => {
                            if (col.selectedObjects === 0) e.currentTarget.style.background = "#101d22";
                          }}
                          onMouseLeave={(e) => {
                            if (col.selectedObjects === 0) e.currentTarget.style.background = "transparent";
                          }}
                        >
                          <td
                            style={{ padding: "4px 4px 4px 6px", textAlign: "center" }}
                            onClick={(e) => e.stopPropagation()}
                          >
                            <input
                              type="checkbox"
                              ref={(el) => {
                                if (el) el.indeterminate = partial;
                              }}
                              checked={checked}
                              onChange={() => handleToggleCollection(col)}
                              style={{ cursor: "pointer", accentColor: "#9fe2b6" }}
                            />
                          </td>
                          <td
                            style={{
                              padding: "4px 6px",
                              color: col.selectedObjects > 0 ? "#ffffff" : "#c0d0c9",
                              fontWeight: col.selectedObjects > 0 ? 600 : 400,
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            }}
                            title={`${col.name} (${col.scene})`}
                          >
                            {col.name}
                            {!effectiveSceneFilter && sceneStats.length > 1 && (
                              <span style={{ marginLeft: "5px", fontSize: "9px", color: "#7a9187" }}>
                                {col.scene}
                              </span>
                            )}
                          </td>
                          <td style={{ padding: "4px 4px" }}>
                            <span
                              style={{
                                fontSize: "9px",
                                color: col.sceneVisible ? "#9fe2b6" : "#7a9187",
                                background: col.sceneVisible ? "#152b22" : "#16252a",
                                padding: "1px 4px",
                                borderRadius: "3px",
                                border: col.sceneVisible ? "1px solid #284c3c" : "1px solid #23373f",
                                display: "inline-block",
                              }}
                            >
                              {col.sceneVisible ? "Visible" : "Hidden"}
                            </span>
                          </td>
                          <td
                            style={{
                              padding: "4px 4px",
                              textAlign: "right",
                              color: "#7a9187",
                              fontVariantNumeric: "tabular-nums",
                            }}
                          >
                            {col.totalObjects}
                          </td>
                          <td
                            style={{
                              padding: "4px 6px 4px 4px",
                              textAlign: "right",
                              color: "#7a9187",
                              fontVariantNumeric: "tabular-nums",
                            }}
                          >
                            {col.polygons > 0 ? col.polygons.toLocaleString() : "—"}
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* In-Memory USD Status */}
      {blendPath && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "8px",
            background: "#122621",
            border: "1px solid #1f3f35",
            borderRadius: "6px",
            padding: "7px 10px",
          }}
        >
          <span
            style={{
              width: "8px",
              height: "8px",
              borderRadius: "50%",
              background: "#48c774",
              display: "inline-block",
              boxShadow: "0 0 6px #48c77488",
            }}
          />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: "11px", fontWeight: "600", color: "#b9ebd2" }}>
              In-Memory Stage Active
            </div>
            <div style={{ fontSize: "10px", color: "#749b8a" }}>
              Geometry extracted directly from .blend into memory
            </div>
          </div>
        </div>
      )}

      {/* Optional: Save Standalone USD to disk */}
      {blendPath && (
        <details
          style={{
            background: "#162329",
            border: "1px solid #283a42",
            borderRadius: "6px",
            padding: "8px",
          }}
        >
          <summary
            style={{
              fontSize: "11px",
              fontWeight: "600",
              cursor: "pointer",
              color: "#8b9c96",
            }}
          >
            Export Standalone USD to Disk (Optional)
          </summary>
          <div style={{ marginTop: "8px" }}>
            <div style={{ display: "flex", gap: "6px", alignItems: "center", marginBottom: "8px" }}>
              <input
                type="text"
                readOnly
                value={targetUsdPath}
                placeholder="Destination USD file"
                style={{ flex: 1, fontSize: "11px", padding: "4px 6px", background: "#0e181c", color: "#d8e2dc" }}
              />
              <button
                onClick={handleBrowseUsdDestination}
                style={{ fontSize: "11px", padding: "4px 8px", whiteSpace: "nowrap" }}
              >
                Browse…
              </button>
            </div>
            <button
              onClick={handleExportUsd}
              disabled={exporting || selections.length === 0}
              style={{
                width: "100%",
                padding: "5px 10px",
                fontSize: "11px",
                fontWeight: "600",
                background: "#1c323b",
                border: "1px solid #2e4c57",
                color: "#d8e2dc",
                cursor: exporting || selections.length === 0 ? "not-allowed" : "pointer",
              }}
            >
              {exporting ? "Exporting USD…" : `Export USD File (${selections.length} selected)`}
            </button>
          </div>
        </details>
      )}
    </div>
  );
}
