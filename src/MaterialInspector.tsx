import { useEffect, useRef, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
type Materials = {
  materials: {
    path: string;
    outputs: { context: string; connections: string[] }[];
  }[];
  bindings: { prim: string; materials: Record<string, string> }[];
  shaderIdentifiers: string[];
  missingTextures: { path: string; usedBy: string[] }[];
  limits: { truncated: boolean };
  support: {
    previewSurfaceDefinition: boolean;
    materialXDefinitions: number;
    openPBRDefinitions: string[];
    note: string;
  };
};
export function MaterialInspector({
  path,
  graph,
  onStatus,
}: {
  path: string;
  graph?: string;
  onStatus: (text: string) => void;
}) {
  const [result, setResult] = useState<Materials | null>(null),
    [busy, setBusy] = useState(false);
  const generation = useRef(0);
  useEffect(() => {
    generation.current++;
    setResult(null);
    setBusy(false);
    return () => {
      generation.current++;
    };
  }, [path, graph]);
  async function inspect() {
    const token = ++generation.current;
    setBusy(true);
    try {
      const response = await invoke<{ materialInspection: Materials }>(
        "usd_request",
        { request: { operation: "inspect", path, graph } },
      );
      if (token === generation.current) setResult(response.materialInspection);
    } catch (error) {
      if (token === generation.current) onStatus(String(error));
    } finally {
      if (token === generation.current) setBusy(false);
    }
  }
  return (
    <section className="material-inspector">
      <h3>Materials</h3>
      <button disabled={!path || !isTauri() || busy} onClick={inspect}>
        {busy ? "Inspecting materials…" : "Inspect output materials"}
      </button>
      {result && (
        <>
          <p className="hint">
            {result.materials.length} material(s) · {result.bindings.length}{" "}
            geometry/subset binding(s)
            {result.limits.truncated ? " · results capped" : ""}
          </p>
          <dl>
            <dt>UsdPreviewSurface definition</dt>
            <dd>
              {result.support.previewSurfaceDefinition
                ? "Available"
                : "Unavailable"}
            </dd>
            <dt>MaterialX definitions</dt>
            <dd>{result.support.materialXDefinitions}</dd>
            <dt>OpenPBR definitions</dt>
            <dd>
              {result.support.openPBRDefinitions.length
                ? "Available"
                : "Not found"}
            </dd>
          </dl>
          <p className="hint">{result.support.note}</p>
          {!!result.missingTextures.length && (
            <p className="material-alert">
              {result.missingTextures.length} missing shader asset(s). See
              Dependencies to locate their files.
            </p>
          )}
          <details>
            <summary>Material networks and render contexts</summary>
            {result.materials.length ? (
              result.materials.map((material) => (
                <div className="material-row" key={material.path}>
                  <strong>{material.path}</strong>
                  <span>
                    {material.outputs
                      .map((output) => output.context)
                      .join(", ") || "No surface output"}
                  </span>
                </div>
              ))
            ) : (
              <p className="hint">
                No material definitions found in the inspected stage.
              </p>
            )}
            <p className="hint">
              Shader IDs: {result.shaderIdentifiers.join(", ") || "None"}
            </p>
          </details>
          <details>
            <summary>Preview / full bindings</summary>
            {result.bindings.map((binding) => (
              <div className="material-row" key={binding.prim}>
                <strong>{binding.prim}</strong>
                {Object.entries(binding.materials).map(([purpose, path]) => (
                  <span key={purpose}>
                    {purpose}: {path}
                  </span>
                ))}
              </div>
            ))}
          </details>
          <p className="hint">
            Read-only inspection. Material conversion, baking and application
            adapters remain planned.
          </p>
        </>
      )}
    </section>
  );
}
