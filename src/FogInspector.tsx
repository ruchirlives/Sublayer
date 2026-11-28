import { defaultFog, type FogSettings } from "./fileRegistry";

export function FogInspector({ value, onChange }: { value?: FogSettings; onChange: (value: FogSettings) => void }) {
  const fog = { ...defaultFog, ...value };
  const update = (patch: Partial<FogSettings>) => onChange({ ...fog, ...patch });
  const number = (key: keyof FogSettings, label: string, min?: number, max?: number) => (
    <label>{label}<input type="number" step="any" min={min} max={max} value={fog[key] as number}
      onChange={e => { if (e.target.value && Number.isFinite(e.target.valueAsNumber)) update({ [key]: e.target.valueAsNumber }); }} /></label>
  );
  const color = (key: "color" | "volumeColor", label: string) => (
    <fieldset><legend>{label} (linear RGB)</legend>{fog[key].map((component, i) => (
      <input key={i} aria-label={`${label} ${["R", "G", "B"][i]}`} type="number" min={0} max={1} step={0.01} value={component}
        onChange={e => { if (!e.target.value || !Number.isFinite(e.target.valueAsNumber)) return; const next = [...fog[key]] as [number, number, number]; next[i] = e.target.valueAsNumber; update({ [key]: next }); }} />
    ))}</fieldset>
  );
  return <div className="node-options">
    <p>RTX fog controls. Storm and Aurora do not preview these effects. Original USD and VDB files stay linked and unchanged.</p>
    <label><input type="checkbox" checked={fog.enabled} onChange={e => update({ enabled: e.target.checked })} />Enable Fog node</label>
    <details open><summary>Atmospheric haze</summary>
      <p>Fast exponential distance/height haze; no light scattering. Downstream Fog nodes win for the active preview.</p>
      <label><input type="checkbox" checked={fog.atmosphereEnabled} onChange={e => update({ atmosphereEnabled: e.target.checked })} />Enable atmosphere</label>
      {color("color", "Fog color")}
      {number("intensity", "Intensity", 0)}
      {number("distanceDensity", "Distance density", 0)}
      {number("startDistance", "Start distance (m)", 0)}
      {number("endDistance", "End distance (m)", 0)}
      {number("height", "Plane height (m)")}
      {number("heightDensity", "Height density", 0)}
      {number("heightFalloff", "Height falloff", 0)}
    </details>
    <details open><summary>Imported VDB shading</summary>
      <p>Connect an existing VDB Source or USD Volume. Overrides density-volume materials only; surface materials pass through. Use RTX Path Tracing. Animated fields and volume instance proxies require a separate editable input.</p>
      <label><input type="checkbox" checked={fog.volumesEnabled} onChange={e => update({ volumesEnabled: e.target.checked })} />Apply volume shading</label>
      <label>Volume prim paths (one per line; empty means all editable volumes)<textarea value={fog.volumePaths.join("\n")} onChange={e => update({ volumePaths: e.target.value.split(/\r?\n/).map(s => s.trim()).filter(Boolean) })} /></label>
      <p>Automatic bounds read the VDB density grid's position and size. If unavailable, turn this off and enter the grid's local center and size. Use a Transform node to move or scale the volume.</p>
      <label><input type="checkbox" checked={fog.volumeAutoBounds} onChange={e => update({ volumeAutoBounds: e.target.checked })} />Automatic VDB bounds</label>
      {!fog.volumeAutoBounds && (["volumeCenter", "volumeSize"] as const).map(key => <fieldset key={key}><legend>{key === "volumeCenter" ? "Boundary center" : "Boundary size"}</legend>{fog[key].map((v, i) => <input key={i} aria-label={`${key} ${["X", "Y", "Z"][i]}`} type="number" step="any" min={key === "volumeSize" ? 0.001 : undefined} value={v} onChange={e => { if (!e.target.value || !Number.isFinite(e.target.valueAsNumber)) return; const next = [...fog[key]] as [number, number, number]; next[i] = e.target.valueAsNumber; update({ [key]: next }); }} />)}</fieldset>)}
      {number("densityScale", "Density multiplier", 0)}
      {color("volumeColor", "Scattering albedo")}
      {number("anisotropy", "Scattering direction (-1 back / 1 forward)", -0.99, 0.99)}
    </details>
  </div>;
}
