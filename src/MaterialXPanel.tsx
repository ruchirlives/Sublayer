import { OperationProgress } from "./OperationProgress";
import { NumberInput } from "./NumberInput";
import { ProceduralEditor } from "./ProceduralEditor";
import { useEffect, useRef, useState, type PointerEvent } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import {
  defaultMaterialX,
  graphPreview,
  registerFiles,
  type MaterialXRule,
  type MaterialXSettings,
  type ProceduralConfig,
  type ProceduralGraph,
  type StoredProject,
} from "./fileRegistry";

type Matches = {
  id: string;
  matched: number;
  effective: number;
  instanceProxyMatches: number;
  paths: string[];
  proxyPaths?: string[];
};

const textureSlots = [
  { key: "baseColorTextureDependencyId", label: "Base Color Map", hint: "Diffuse / albedo color (sRGB)" },
  { key: "roughnessTextureDependencyId", label: "Roughness Map", hint: "Specular roughness (linear float)" },
  { key: "metallicTextureDependencyId", label: "Metallic Map", hint: "Metalness mask (linear float)" },
  { key: "normalTextureDependencyId", label: "Normal Map", hint: "Tangent-space normal map via ND_normalmap_float + st" },
  { key: "emissionTextureDependencyId", label: "Emission Map", hint: "Emissive radiance pattern (sRGB / linear)" },
  { key: "opacityTextureDependencyId", label: "Opacity Map", hint: "Cutout or transmission opacity mask" },
  { key: "transmissionTextureDependencyId", label: "Transmission Map", hint: "Dielectric transmission mask" },
] as const;

type TextureKey = typeof textureSlots[number]["key"];

const _defaultProceduralColor: ProceduralConfig = {
  type: "noise2d",
  space: "uv",
  scale: [1, 1],
  amplitude: 1,
  octaves: 3,
  color1: [0, 0, 0],
  color2: [1, 1, 1],
};

const _defaultProceduralFloat: ProceduralConfig = {
  type: "noise2d",
  space: "uv",
  scale: [1, 1],
  amplitude: 1,
  octaves: 3,
};

function rgbToHex([r, g, b]: [number, number, number]): string {
  const toHex = (c: number) =>
    Math.max(0, Math.min(255, Math.round(Math.max(0, Math.min(1, c)) * 255)))
      .toString(16)
      .padStart(2, "0");
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

function hexToRgb(hex: string): [number, number, number] {
  const clean = hex.replace("#", "");
  if (clean.length !== 6) return [1, 1, 1];
  const r = parseInt(clean.slice(0, 2), 16) / 255;
  const g = parseInt(clean.slice(2, 4), 16) / 255;
  const b = parseInt(clean.slice(4, 6), 16) / 255;
  return [Number(r.toFixed(3)), Number(g.toFixed(3)), Number(b.toFixed(3))];
}

export function MaterialXPanel({
  project,
  nodeId,
  onProjectChange,
  onClose,
}: {
  project: StoredProject;
  nodeId: string;
  onProjectChange: (project: StoredProject) => void;
  onClose: () => void;
}) {
  const node = project.nodes.find((n) => n.id === nodeId)!;
  const [rows, setRows] = useState<MaterialXRule[]>(() =>
    structuredClone(
      node.data.materialXRules?.map((r) => ({ ...r, material: { ...defaultMaterialX, ...r.material } })) ??
        (node.data.materialX?.bindingTargets || ["*"]).map((query) => ({
          id: crypto.randomUUID(),
          enabled: true,
          searchType: "simple" as const,
          query,
          material: { ...defaultMaterialX, ...(node.data.materialX || {}) },
        })),
    ),
  );
  const [selectedId, setSelectedId] = useState<string>(() => rows[0]?.id || "");
  const [panelView, setPanelView] = useState<"material" | "procedural">("material");
  const [procGraph, setProcGraph] = useState<ProceduralGraph>(() =>
    structuredClone(project.proceduralGraph ?? { nodes: [], edges: [] }),
  );
  const procDraft = useRef<ProceduralGraph>(procGraph);
  procDraft.current = procGraph;
  const [_activeTab, _setActiveTab] = useState<"surface" | "transmission" | "coat" | "textures" | "procedural">("surface");
  const [tableView, setTableView] = useState<"levels" | "pbr" | "coat" | "trans" | "sss" | "textures">("levels");
  const [position, setPosition] = useState(() => ({
    x: Math.max(16, Math.min(60, Math.floor((window.innerWidth - 1000) / 2))),
    y: Math.max(20, Math.min(50, Math.floor((window.innerHeight - 660) / 2))),
  }));
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [message, setMessage] = useState("");
  const [matches, setMatches] = useState<Matches[]>([]);

  const latest = useRef(project);
  latest.current = project;
  const draft = useRef(rows);
  draft.current = rows;

  useEffect(() => {
    if (!dirty && node.data.materialXRules) {
      setRows(
        structuredClone(
          node.data.materialXRules.map((r) => ({
            ...r,
            material: { ...defaultMaterialX, ...r.material },
          })),
        ),
      );
    }
  }, [node.data.materialXRules, dirty]);

  useEffect(() => {
    if (!dirty) {
      setProcGraph(structuredClone(project.proceduralGraph ?? { nodes: [], edges: [] }));
    }
  }, [project.proceduralGraph, dirty]);

  useEffect(() => {
    if (!rows.some((r) => r.id === selectedId) && rows.length > 0) {
      setSelectedId(rows[0].id);
    }
  }, [rows, selectedId]);

  const [dropPrompt, setDropPrompt] = useState<{
    rowId: string;
    primPath: string;
    currentQuery: string;
  } | null>(null);

  function handlePrimDrop(rowId: string, droppedPath: string) {
    const row = draft.current.find((r) => r.id === rowId);
    if (!row) return;
    const current = (row.query || "").trim();
    if (!current || current === "*") {
      patch(rowId, { query: droppedPath });
    } else {
      setDropPrompt({
        rowId,
        primPath: droppedPath,
        currentQuery: current,
      });
    }
  }

  function change(next: MaterialXRule[]) {
    setRows(next);
    setDirty(true);
    setMatches([]);
  }

  function patch(id: string, value: Partial<MaterialXRule>) {
    change(draft.current.map((r) => (r.id === id ? { ...r, ...value } : r)));
  }

  function material(id: string, value: Partial<MaterialXSettings>) {
    const row = draft.current.find((r) => r.id === id);
    if (row) patch(id, { material: { ...row.material, ...value } });
  }

  function candidate(current: StoredProject) {
    const pg = procDraft.current;
    return {
      ...current,
      nodes: current.nodes.map((n) =>
        n.id === nodeId ? { ...n, data: { ...n.data, materialXRules: draft.current } } : n,
      ),
      proceduralGraph: pg.nodes.length > 0 || pg.edges.length > 0 ? pg : undefined,
    };
  }

  async function evaluate(apply: boolean) {
    const snapshot = candidate(latest.current);
    const signature = graphPreview(snapshot, nodeId);
    if (apply) {
      onProjectChange(snapshot);
      setDirty(false);
    }
    setBusy(true);
    setMessage(apply ? "Applied to viewport; updating rule match counts..." : "Evaluating selectors...");
    try {
      const result = await invoke<{ rows: Matches[]; note: string }>("materialx_rule_matches", {
        project: snapshot,
        nodeId,
      });
      if (graphPreview(candidate(latest.current), nodeId) !== signature) {
        if (!apply) throw new Error("Inputs changed. Preview or apply again.");
        return;
      }
      setMatches(result.rows);
      setMessage(result.note);
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function _browse(id: string, key: TextureKey) {
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const path = await open({
        multiple: false,
        filters: [{ name: "Texture", extensions: ["png", "jpg", "jpeg", "exr", "tif", "tiff", "tx", "tga", "bmp"] }],
      });
      if (!path) return;
      const current = latest.current;
      if (!current.nodes.some((n) => n.id === nodeId) || !draft.current.some((r) => r.id === id)) return;
      const files = registerFiles(current.dependencies, [path], "asset");
      if (files.dependencies.find((d) => d.id === files.ids[0])?.kind !== "asset") {
        throw new Error("Link is not a texture asset");
      }
      onProjectChange({ ...current, dependencies: files.dependencies });
      material(id, { [key]: files.ids[0] });
    } catch (e) {
      setMessage(String(e));
    }
  }

  async function exportRow(row: MaterialXRule) {
    setBusy(true);
    try {
      const { save } = await import("@tauri-apps/plugin-dialog");
      const destination = await save({
        defaultPath: row.material.name + ".mtlx",
        filters: [{ name: "MaterialX", extensions: ["mtlx"] }],
      });
      if (!destination) return;
      const current = latest.current;
      const pg = procDraft.current;
      const exportProject = {
        ...current,
        nodes: current.nodes.map((n) =>
          n.id === nodeId ? { ...n, data: { ...n.data, materialXRules: [row], materialX: row.material } } : n,
        ),
        proceduralGraph: pg.nodes.length > 0 || pg.edges.length > 0 ? pg : undefined,
      };
      await invoke("export_materialx", { project: exportProject, nodeId, destination });
      setMessage("Exported material document; textures remain linked.");
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
    }
  }

  const windowRef = useRef<HTMLElement>(null);

  function startDrag(e: PointerEvent<HTMLElement>) {
    if ((e.target as HTMLElement).closest("button, input, select, textarea")) return;
    drag.current = { x: e.clientX, y: e.clientY, left: position.x, top: position.y };
    e.currentTarget.setPointerCapture(e.pointerId);
    e.preventDefault();
  }

  function moveDrag(e: PointerEvent<HTMLElement>) {
    if (drag.current) {
      const winW = windowRef.current ? windowRef.current.offsetWidth : 800;
      const winH = windowRef.current ? windowRef.current.offsetHeight : 500;
      const maxX = Math.max(0, window.innerWidth - winW - 10);
      const maxY = Math.max(0, window.innerHeight - winH - 10);
      setPosition({
        x: Math.max(8, Math.min(maxX, drag.current.left + e.clientX - drag.current.x)),
        y: Math.max(8, Math.min(maxY, drag.current.top + e.clientY - drag.current.y)),
      });
    }
  }

  function move(index: number, offset: number) {
    const next = [...draft.current];
    [next[index], next[index + offset]] = [next[index + offset], next[index]];
    change(next);
  }

  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);

  const activeRow = rows.find((r) => r.id === selectedId) || rows[0];
  void _defaultProceduralColor;
  void _defaultProceduralFloat;
  void _browse;

  return (
    <section
      ref={windowRef}
      className="materialx-window"
      style={{ left: position.x, top: position.y }}
      role="dialog"
      aria-label={`${node.data.label} shading rules`}
    >
      <header
        onPointerDown={startDrag}
        onPointerMove={moveDrag}
        onPointerUp={() => (drag.current = null)}
        onPointerCancel={() => (drag.current = null)}
      >
        <strong>
          {node.data.label} - MaterialX Authoring & Rules{dirty ? " (unapplied edits)" : ""}
        </strong>
        <div className="materialx-mode-switch">
          <button
            type="button"
            className={panelView === "material" ? "primary" : ""}
            onClick={() => setPanelView("material")}
          >
            Material
          </button>
          <button
            type="button"
            className={panelView === "procedural" ? "primary" : ""}
            onClick={() => setPanelView("procedural")}
          >
            Procedural ({procGraph.nodes.length})
          </button>
          <button onClick={onClose}>Close</button>
        </div>
      </header>

      {panelView === "procedural" ? (
        <ProceduralEditor
          project={candidate(latest.current)}
          activeMaterialXNodeId={nodeId}
          activeRuleId={activeRow?.id}
          graph={procGraph}
          onGraphChange={(next) => {
            setProcGraph(next);
            setDirty(true);
          }}
        />
      ) : (
        <>
          <p className="hint">
            Simple search: case-insensitive substring of prim path; * matches all. Regex is case-sensitive. Last matching
            row wins. Select a row to author full surface, transmission, emission, coat, sheen, subsurface, textures, and
            procedurals.
          </p>

          {/* View Switcher */}
          <div style={{ display: "flex", gap: "4px", marginBottom: "6px", flexWrap: "wrap" }}>
            {(
              [
                ["levels", "Levels"],
                ["pbr", "PBR"],
                ["coat", "Coat & Sheen"],
                ["trans", "Trans & Emis"],
                ["sss", "SSS"],
                ["textures", "Textures"],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                className={tableView === key ? "primary" : ""}
                onClick={() => setTableView(key)}
                style={{ fontSize: "11px", padding: "3px 8px" }}
              >
                {label}
              </button>
            ))}
          </div>

          {/* Rules Table */}
          <div className="materialx-grid">
        <table>
          <thead>
            <tr>
              {[
                "On",
                "Search",
                "Prim query",
                "Material name",
                ...(tableView === "levels"
                  ? ["Base color", "Metal", "Rough", "Spec", "Opacity", "Coat", "Coat Rough", "Sheen", "Trans", "Emis", "SSS"]
                  : tableView === "pbr"
                    ? ["Base color", "Metal", "Spec", "Spec Color", "Rough", "IOR", "Opacity"]
                    : tableView === "coat"
                      ? ["Coat", "Coat Color", "Coat Rough", "Sheen", "Sheen Color", "Sheen Rough"]
                      : tableView === "trans"
                        ? ["Trans", "Trans Color", "Emis", "Emis Color"]
                        : tableView === "sss"
                          ? ["SSS", "SSS Scale", "SSS Radius (RGB)"]
                          : ["Base Color Tex", "Metal Tex", "Rough Tex", "Normal Tex"]),
                "Matches",
                "Actions",
              ].map((v) => (
                <th key={v}>{v}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => {
              const rowMat = row.material;
              const stats = matches.find((r) => r.id === row.id);
              const isSelected = row.id === (activeRow?.id || selectedId);

              const badges: string[] = [];
              if ((rowMat.coat ?? 0) > 0) badges.push("Coat");
              if ((rowMat.sheen ?? 0) > 0) badges.push("Sheen");
              if ((rowMat.transmission ?? 0) > 0) badges.push("Trans");
              if ((rowMat.emission ?? 0) > 0) badges.push("Emit");
              if ((rowMat.subsurface ?? 0) > 0) badges.push("SSS");
              if (rowMat.normalTextureDependencyId) badges.push("Norm");
              if (rowMat.proceduralBaseColor?.type && rowMat.proceduralBaseColor.type !== "none") badges.push("ProcCol");
              if (rowMat.proceduralRoughness?.type && rowMat.proceduralRoughness.type !== "none") badges.push("ProcRgh");
              const texCount = textureSlots.filter((slot) => Boolean(rowMat[slot.key])).length;
              if (texCount > 0) badges.push(`${texCount} tex`);

              return (
                <tr
                  key={row.id}
                  className={isSelected ? "materialx-row-selected" : ""}
                  onClick={() => setSelectedId(row.id)}
                >
                  <td>
                    <input
                      aria-label={`Enable row ${index + 1}`}
                      disabled={busy}
                      type="checkbox"
                      checked={row.enabled}
                      onChange={(e) => patch(row.id, { enabled: e.target.checked })}
                    />
                  </td>
                  <td>
                    <select
                      disabled={busy}
                      value={row.searchType}
                      onChange={(e) => patch(row.id, { searchType: e.target.value as "simple" | "regex" })}
                    >
                      <option value="simple">Simple</option>
                      <option value="regex">Regex</option>
                    </select>
                  </td>
                  <td
                    onDragEnter={(e) => {
                      e.preventDefault();
                    }}
                    onDragOver={(e) => {
                      e.preventDefault();
                      e.dataTransfer.dropEffect = "copy";
                    }}
                    onDrop={(e) => {
                      e.preventDefault();
                      const dropped = e.dataTransfer.getData("application/orchflow-prim-path") || e.dataTransfer.getData("text/plain") || e.dataTransfer.getData("text");
                      if (dropped && dropped.startsWith("/")) {
                        handlePrimDrop(row.id, dropped.trim());
                      }
                    }}
                  >
                    <input
                      aria-label={`Prim search row ${index + 1}`}
                      disabled={busy}
                      value={row.query}
                      placeholder="e.g. * or /Mesh, /Body"
                      title="Drag and drop prim from Scene Overview or enter comma-separated terms"
                      onChange={(e) => patch(row.id, { query: e.target.value })}
                      onDragOver={(e) => {
                        e.preventDefault();
                        e.dataTransfer.dropEffect = "copy";
                      }}
                      onDrop={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        const dropped = e.dataTransfer.getData("application/orchflow-prim-path") || e.dataTransfer.getData("text/plain") || e.dataTransfer.getData("text");
                        if (dropped && dropped.startsWith("/")) {
                          handlePrimDrop(row.id, dropped.trim());
                        }
                      }}
                    />
                  </td>
                  <td>
                    <input
                      aria-label={`Material row ${index + 1}`}
                      disabled={busy}
                      value={rowMat.name}
                      onChange={(e) => material(row.id, { name: e.target.value })}
                    />
                  </td>
                  {tableView === "levels" && (
                    <>
                  <td className="materialx-color-cell">
                    <input
                      type="color"
                      disabled={busy}
                      title={`RGB: ${rowMat.baseColor.map((v) => v.toFixed(2)).join(", ")}`}
                      value={rgbToHex(rowMat.baseColor)}
                      onChange={(e) => material(row.id, { baseColor: hexToRgb(e.target.value) })}
                    />
                  </td>
                  <td>
                    <NumberInput
                      aria-label={`Metalness row ${index + 1}`}
                      disabled={busy}
                      min="0"
                      max="1"
                      step="0.05"
                      value={rowMat.metalness}
                      onChange={(e) => material(row.id, { metalness: Number(e.target.value) })}
                    />
                  </td>
                  <td>
                    <NumberInput
                      aria-label={`Roughness row ${index + 1}`}
                      disabled={busy}
                      min="0"
                      max="1"
                      step="0.05"
                      value={rowMat.roughness}
                      onChange={(e) => material(row.id, { roughness: Number(e.target.value) })}
                    />
                  </td>
                  <td>
                    <NumberInput
                      aria-label={`Specular row ${index + 1}`}
                      disabled={busy}
                      min="0"
                      max="1"
                      step="0.05"
                      value={rowMat.specular}
                      onChange={(e) => material(row.id, { specular: Number(e.target.value) })}
                    />
                  </td>
                  <td>
                    <NumberInput
                      aria-label={`Opacity row ${index + 1}`}
                      disabled={busy}
                      min="0"
                      max="1"
                      step="0.05"
                      value={rowMat.opacity}
                      onChange={(e) => material(row.id, { opacity: Number(e.target.value) })}
                    />
                  </td>
                  <td>
                    <NumberInput
                      aria-label={`Coat row ${index + 1}`}
                      disabled={busy}
                      min="0"
                      max="1"
                      step="0.05"
                      value={rowMat.coat ?? 0}
                      onChange={(e) => material(row.id, { coat: Number(e.target.value) })}
                    />
                  </td>
                  <td>
                    <NumberInput
                      aria-label={`Coat Roughness row ${index + 1}`}
                      disabled={busy}
                      min="0"
                      max="1"
                      step="0.05"
                      value={rowMat.coatRoughness ?? 0.1}
                      onChange={(e) => material(row.id, { coatRoughness: Number(e.target.value) })}
                    />
                  </td>
                  <td>
                    <NumberInput
                      aria-label={`Sheen row ${index + 1}`}
                      disabled={busy}
                      min="0"
                      max="1"
                      step="0.05"
                      value={rowMat.sheen ?? 0}
                      onChange={(e) => material(row.id, { sheen: Number(e.target.value) })}
                    />
                  </td>
                  <td>
                    <NumberInput
                      aria-label={`Transmission row ${index + 1}`}
                      disabled={busy}
                      min="0"
                      max="1"
                      step="0.05"
                      value={rowMat.transmission ?? 0}
                      onChange={(e) => material(row.id, { transmission: Number(e.target.value) })}
                    />
                  </td>
                  <td>
                    <NumberInput
                      aria-label={`Emission row ${index + 1}`}
                      disabled={busy}
                      min="0"
                      max="100"
                      step="0.1"
                      value={rowMat.emission ?? 0}
                      onChange={(e) => material(row.id, { emission: Number(e.target.value) })}
                    />
                  </td>
                  <td>
                    <NumberInput
                      aria-label={`Subsurface row ${index + 1}`}
                      disabled={busy}
                      min="0"
                      max="1"
                      step="0.05"
                      value={rowMat.subsurface ?? 0}
                      onChange={(e) => material(row.id, { subsurface: Number(e.target.value) })}
                    />
                  </td>
                    </>
                  )}
                  {tableView === "pbr" && (
                    <>
                      <td className="materialx-color-cell">
                        <input disabled={busy} type="color" value={rgbToHex(rowMat.baseColor)} onChange={(e) => material(row.id, { baseColor: hexToRgb(e.target.value) })} />
                      </td>
                      <td><NumberInput disabled={busy} min="0" max="1" step="0.05" value={rowMat.metalness} onChange={(e) => material(row.id, { metalness: Number(e.target.value) })} /></td>
                      <td><NumberInput disabled={busy} min="0" max="1" step="0.05" value={rowMat.specular} onChange={(e) => material(row.id, { specular: Number(e.target.value) })} /></td>
                      <td className="materialx-color-cell">
                        <input disabled={busy} type="color" value={rgbToHex(rowMat.specularColor ?? [1, 1, 1])} onChange={(e) => material(row.id, { specularColor: hexToRgb(e.target.value) })} />
                      </td>
                      <td><NumberInput disabled={busy} min="0" max="1" step="0.05" value={rowMat.roughness} onChange={(e) => material(row.id, { roughness: Number(e.target.value) })} /></td>
                      <td><NumberInput disabled={busy} min="1" max="5" step="0.05" value={rowMat.specularIor ?? 1.5} onChange={(e) => material(row.id, { specularIor: Number(e.target.value) })} /></td>
                      <td><NumberInput disabled={busy} min="0" max="1" step="0.05" value={rowMat.opacity} onChange={(e) => material(row.id, { opacity: Number(e.target.value) })} /></td>
                    </>
                  )}
                  {tableView === "coat" && (
                    <>
                      <td><NumberInput disabled={busy} min="0" max="1" step="0.05" value={rowMat.coat ?? 0} onChange={(e) => material(row.id, { coat: Number(e.target.value) })} /></td>
                      <td className="materialx-color-cell">
                        <input disabled={busy} type="color" value={rgbToHex(rowMat.coatColor ?? [1, 1, 1])} onChange={(e) => material(row.id, { coatColor: hexToRgb(e.target.value) })} />
                      </td>
                      <td><NumberInput disabled={busy} min="0" max="1" step="0.05" value={rowMat.coatRoughness ?? 0.1} onChange={(e) => material(row.id, { coatRoughness: Number(e.target.value) })} /></td>
                      <td><NumberInput disabled={busy} min="0" max="1" step="0.05" value={rowMat.sheen ?? 0} onChange={(e) => material(row.id, { sheen: Number(e.target.value) })} /></td>
                      <td className="materialx-color-cell">
                        <input disabled={busy} type="color" value={rgbToHex(rowMat.sheenColor ?? [1, 1, 1])} onChange={(e) => material(row.id, { sheenColor: hexToRgb(e.target.value) })} />
                      </td>
                      <td><NumberInput disabled={busy} min="0" max="1" step="0.05" value={rowMat.sheenRoughness ?? 0.3} onChange={(e) => material(row.id, { sheenRoughness: Number(e.target.value) })} /></td>
                    </>
                  )}
                  {tableView === "trans" && (
                    <>
                      <td><NumberInput disabled={busy} min="0" max="1" step="0.05" value={rowMat.transmission ?? 0} onChange={(e) => material(row.id, { transmission: Number(e.target.value) })} /></td>
                      <td className="materialx-color-cell">
                        <input disabled={busy} type="color" value={rgbToHex(rowMat.transmissionColor ?? [1, 1, 1])} onChange={(e) => material(row.id, { transmissionColor: hexToRgb(e.target.value) })} />
                      </td>
                      <td><NumberInput disabled={busy} min="0" max="1000" step="0.1" value={rowMat.emission ?? 0} onChange={(e) => material(row.id, { emission: Number(e.target.value) })} /></td>
                      <td className="materialx-color-cell">
                        <input disabled={busy} type="color" value={rgbToHex(rowMat.emissionColor ?? [1, 1, 1])} onChange={(e) => material(row.id, { emissionColor: hexToRgb(e.target.value) })} />
                      </td>
                    </>
                  )}
                  {tableView === "sss" && (
                    <>
                      <td><NumberInput disabled={busy} min="0" max="1" step="0.05" value={rowMat.subsurface ?? 0} onChange={(e) => material(row.id, { subsurface: Number(e.target.value) })} /></td>
                      <td><NumberInput disabled={busy} min="0" max="100" step="0.05" value={rowMat.subsurfaceScale ?? 1.0} onChange={(e) => material(row.id, { subsurfaceScale: Number(e.target.value) })} /></td>
                      <td>
                        <div className="materialx-rgb">
                          {(rowMat.subsurfaceRadius ?? [1, 1, 1]).map((v, ci) => (
                            <NumberInput
                              key={ci}
                              disabled={busy}
                              min="0"
                              max="10"
                              step="0.05"
                              value={v}
                              onChange={(e) => {
                                const next: [number, number, number] = [...(rowMat.subsurfaceRadius ?? [1, 1, 1])];
                                next[ci] = Number(e.target.value);
                                material(row.id, { subsurfaceRadius: next });
                              }}
                            />
                          ))}
                        </div>
                      </td>
                    </>
                  )}
                  {tableView === "textures" && (
                    <>
                      {(["baseColorTextureDependencyId", "metallicTextureDependencyId", "roughnessTextureDependencyId", "normalTextureDependencyId"] as const).map((slotKey) => {
                        const depId = rowMat[slotKey];
                        const texPath = depId ? (project.dependencies.find((d) => d.id === depId)?.path || "") : "";
                        return (
                          <td key={slotKey}>
                            <div style={{ display: "flex", gap: "4px", alignItems: "center" }}>
                              <button type="button" disabled={busy || !isTauri()} onClick={() => _browse(row.id, slotKey)} style={{ fontSize: "10px", padding: "2px 6px" }} title={texPath || "Browse texture"}>
                                {texPath ? texPath.split(/[\\/]/).pop() : "Browse"}
                              </button>
                              {depId && (
                                <button type="button" disabled={busy} onClick={() => material(row.id, { [slotKey]: undefined })} style={{ fontSize: "10px", padding: "2px 4px" }} title="Clear">
                                  ✕
                                </button>
                              )}
                            </div>
                          </td>
                        );
                      })}
                    </>
                  )}
                  <td
                    title={
                      stats
                        ? `${stats.paths.join("\n")}\n${stats.instanceProxyMatches} instance-proxy matches skipped`
                        : "Preview matches"
                    }
                  >
                    {stats ? `${stats.matched} / ${stats.effective}` : "-"}
                  </td>
                  <td className="materialx-actions">
                    <button
                      disabled={busy}
                      onClick={() => change([...rows, { ...structuredClone(row), id: crypto.randomUUID() }])}
                      title="Duplicate rule"
                    >
                      Dup
                    </button>
                    <button disabled={busy || !index} onClick={() => move(index, -1)} title="Move up">
                      ▲
                    </button>
                    <button
                      disabled={busy || index === rows.length - 1}
                      onClick={() => move(index, 1)}
                      title="Move down"
                    >
                      ▼
                    </button>
                    <button disabled={busy || !isTauri()} onClick={() => exportRow(row)} title="Export to .mtlx file">
                      Export
                    </button>
                    <button
                      disabled={busy || rows.length <= 1}
                      onClick={() => change(rows.filter((r) => r.id !== row.id))}
                      title="Delete rule"
                    >
                      Del
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
        </>
      )}

      {/* Footer Toolbar & Status */}
      <div className="materialx-toolbar">
        {panelView === "material" && (
          <>
            <button
              disabled={busy || rows.length >= 256}
              onClick={() => {
                const newId = crypto.randomUUID();
                const newRow: MaterialXRule = {
                  id: newId,
                  enabled: true,
                  searchType: "simple",
                  query: "",
                  material: { ...structuredClone(defaultMaterialX), name: `Material_${rows.length + 1}` },
                };
                change([...rows, newRow]);
                setSelectedId(newId);
              }}
            >
              Add rule row
            </button>
            <button disabled={busy || !isTauri()} onClick={() => evaluate(false)}>
              Preview matches
            </button>
          </>
        )}
        <button disabled={busy || !isTauri()} className="primary" onClick={() => evaluate(true)}>
          {busy ? "Working..." : "Apply"}
        </button>
      </div>

      {matches.length > 0 && (
        <details
          className="materialx-matched-preview"
          style={{
            marginTop: "8px",
            padding: "8px 12px",
            background: "rgba(10, 20, 28, 0.7)",
            borderRadius: "6px",
            border: "1px solid rgba(100, 180, 240, 0.2)",
            maxHeight: "260px",
            overflowY: "auto",
            flexShrink: 0,
          }}
        >
          <summary
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              cursor: "pointer",
              userSelect: "none",
              listStyle: "none",
            }}
          >
            <strong style={{ color: "#90caf9", fontSize: "12px" }}>▸ Target Geometry Preview</strong>
            <small style={{ opacity: 0.8 }}>
              {matches.reduce((acc, m) => acc + m.matched, 0)} total match(es) across {matches.length} rule(s)
            </small>
          </summary>
          <div style={{ marginTop: "8px", borderTop: "1px solid rgba(255, 255, 255, 0.1)", paddingTop: "8px" }}>
            {matches.map((m, idx) => {
            const rule = rows.find((r) => r.id === m.id);
            return (
              <div key={m.id || idx} style={{ marginBottom: "10px" }}>
                <div style={{ fontSize: "0.85em", color: "#64b5f6", fontWeight: "bold" }}>
                  Rule {idx + 1}: {rule?.material?.name || "Material"} (Query: &ldquo;{rule?.query || "*"}&rdquo;) &mdash; {m.effective} effective / {m.matched} matched
                </div>
                {m.paths.length > 0 ? (
                  <ul style={{ margin: "4px 0 0 16px", padding: 0, fontSize: "0.82em", fontFamily: "monospace", color: "#e0e0e0" }}>
                    {m.paths.map((p) => (
                      <li key={p} style={{ margin: "2px 0" }}>{p}</li>
                    ))}
                  </ul>
                ) : (
                  <p style={{ margin: "2px 0 0 16px", fontSize: "0.8em", color: "#ef9a9a", fontStyle: "italic" }}>
                    No targetable prims. (Searches geometry: Meshes, Subsets, Curves).
                  </p>
                )}
                {m.proxyPaths && m.proxyPaths.length > 0 && (
                  <div style={{ margin: "4px 0 0 16px", fontSize: "0.78em", color: "#ffb74d", background: "rgba(255, 183, 77, 0.08)", padding: "4px 8px", borderRadius: "4px" }}>
                    <strong>⚠ Skipped {m.proxyPaths.length} instance-proxy prim(s) (USD prohibits authoring on instance children):</strong>
                    <ul style={{ margin: "2px 0 0 14px", padding: 0, fontFamily: "monospace" }}>
                      {m.proxyPaths.map((pp) => (
                        <li key={pp}>{pp}</li>
                      ))}
                    </ul>
                    <small style={{ display: "block", marginTop: "2px", opacity: 0.85 }}>
                      To style an instanced asset, target its instance root (e.g. <code>root</code> or <code>/Scene/root</code>).
                    </small>
                  </div>
                )}
              </div>
            );
            })}
          </div>
        </details>
      )}

      {dropPrompt && (
        <div
          className="override-backdrop"
          style={{ zIndex: 9999 }}
          onClick={() => setDropPrompt(null)}
        >
          <div
            className="override-modal"
            style={{ maxWidth: 440, padding: "16px 20px" }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3>Update Prim Query</h3>
            <p style={{ margin: "10px 0", fontSize: "0.9em", color: "#ccc" }}>
              Add <code>{dropPrompt.primPath}</code> to existing query or replace it?
            </p>
            <p style={{ margin: "6px 0 16px", fontSize: "0.85em", color: "#888", fontFamily: "monospace" }}>
              Current: {dropPrompt.currentQuery}
            </p>
            <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end" }}>
              <button
                type="button"
                className="secondary"
                onClick={() => setDropPrompt(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="secondary"
                onClick={() => {
                  patch(dropPrompt.rowId, { query: dropPrompt.primPath });
                  setDropPrompt(null);
                }}
              >
                Replace
              </button>
              <button
                type="button"
                className="primary"
                onClick={() => {
                  const terms = dropPrompt.currentQuery.split(",").map((s) => s.trim()).filter(Boolean);
                  if (!terms.includes(dropPrompt.primPath)) {
                    terms.push(dropPrompt.primPath);
                  }
                  patch(dropPrompt.rowId, { query: terms.join(", ") });
                  setDropPrompt(null);
                }}
              >
                Add (comma-separated)
              </button>
            </div>
          </div>
        </div>
      )}

      {busy && <OperationProgress label="Evaluating or exporting MaterialX rules..." />}
      <p role="status">{message}</p>
      <p className="hint">
        Linear RGB colors. Instance-proxy children are skipped; select their instance root. Closing discards unapplied
        row edits. Full Standard Surface (Base, Coat, Sheen, SSS, Transmission, Emission, Normal map, Procedurals) with
        automatic PreviewSurface fallback.
      </p>
    </section>
  );
}
