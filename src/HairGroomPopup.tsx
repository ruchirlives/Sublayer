import { useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { emitTo, listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { LatestRenderQueue } from "./renderQueue";

type ViewportCamera = { yaw: number; pitch: number; zoom: number; panX: number; panY: number };

type HairGroomStatePayload = {
  path: string;
  graph?: string;
  graphKey?: string;
  ovrtxMode?: "realtime" | "pathTracing";
  materials?: "auto" | "scene" | "solid";
  upAxis?: "Y" | "Z";
  frame?: number;
  activeHairNodeId?: string;
  camera?: ViewportCamera;
  cameraSession?: string;
};

type PopupInputEvent = {
  type: "mousedown" | "mousemove" | "mouseup" | "wheel" | "keydown";
  button?: number;
  buttons?: number;
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  deltaY?: number;
  shiftKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  key?: string;
};

type BrushTool =
  | "comb" | "surfaceComb" | "grab" | "smooth" | "physics"
  | "clump" | "part" | "puff" | "lengthen" | "shorten"
  | "cut" | "add" | "delete" | "pin" | "unpin";

const BRUSH_TOOLS: Array<[BrushTool, string]> = [
  ["comb", "Comb"],
  ["surfaceComb", "Surface Comb"],
  ["grab", "Grab"],
  ["smooth", "Smooth"],
  ["clump", "Clump"],
  ["part", "Part"],
  ["puff", "Puff"],
  ["lengthen", "Lengthen"],
  ["shorten", "Shorten"],
  ["cut", "Cut"],
  ["add", "Add"],
  ["delete", "Delete"],
  ["pin", "Pin"],
  ["unpin", "Unpin"],
];

const textDecoder = new TextDecoder();

export function HairGroomPopup() {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [parentHwnd, setParentHwnd] = useState<number>(0);
  const [rect, setRect] = useState<{ x: number; y: number; width: number; height: number }>({
    x: 0, y: 64, width: 1200, height: 760,
  });
  const [sceneState, setSceneState] = useState<HairGroomStatePayload | null>(null);
  const [camera, setCamera] = useState<ViewportCamera>({ yaw: 35, pitch: 20, zoom: 1, panX: 0, panY: 0 });
  const [frameFit, setFrameFit] = useState(0);
  const [ovrtxMode, setOvrtxMode] = useState<"realtime" | "pathTracing">("realtime");
  const [brushTool, setBrushTool] = useState<BrushTool>("comb");
  const [brushRadius, setBrushRadius] = useState(0.06);
  const [brushRadiusText, setBrushRadiusText] = useState("0.06");
  const [brushStrength, setBrushStrength] = useState(1.0);
  const [brushStrengthText, setBrushStrengthText] = useState("1.0");
  const [livePhysics, setLivePhysics] = useState(false);
  const [showPins, setShowPins] = useState(false);
  const [pinnedCount, setPinnedCount] = useState(0);
  const [interacting, setInteracting] = useState(false);
  const [statusText, setStatusText] = useState("Waiting for scene...");
  const [hairBrush, setHairBrush] = useState<Record<string, unknown> | null>(null);

  const brushSeqRef = useRef(0);
  const strokeIdRef = useRef(0);
  const hairDirtyRef = useRef(false);
  const registeredGraphRef = useRef<string | null>(null);
  const navDragRef = useRef<{ mode: "orbit" | "pan"; x: number; y: number; cam: ViewportCamera } | null>(null);
  const brushDragRef = useRef<{ nx: number; ny: number } | null>(null);
  const lineStartRef = useRef<{ nx: number; ny: number } | null>(null);

  // Measure physical pixel rect of the native D3D11 host area
  useEffect(() => {
    invoke<number>("get_window_hwnd").then(setParentHwnd).catch(() => {});
    const updateRect = () => {
      const el = hostRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      setRect({
        x: Math.round(r.left * dpr),
        y: Math.round(r.top * dpr),
        width: Math.max(64, Math.round(r.width * dpr)),
        height: Math.max(64, Math.round(r.height * dpr)),
      });
    };
    updateRect();
    const ro = new ResizeObserver(updateRect);
    if (hostRef.current) ro.observe(hostRef.current);
    window.addEventListener("resize", updateRect);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", updateRect);
    };
  }, []);

  // Sync scene state from main window
  useEffect(() => {
    let unlistenState: (() => void) | null = null;
    listen<HairGroomStatePayload>("hair-groom-state", (ev) => {
      const p = ev.payload;
      setSceneState(p);
      if (p.camera) setCamera(p.camera);
      if (p.ovrtxMode) setOvrtxMode(p.ovrtxMode);
      registeredGraphRef.current = null;
    }).then((fn) => { unlistenState = fn; });
    emitTo("main", "hair-groom-ready", {}).catch(() => {});
    return () => { unlistenState?.(); };
  }, []);

  // Close native D3D11 child HWND when window closes without blocking close
  useEffect(() => {
    const win = getCurrentWindow();
    let unlistenClose: (() => void) | null = null;
    win.onCloseRequested(() => {
      if (sceneState?.path) {
        invoke("render_viewport_frame", {
          request: {
            operation: "render",
            renderer: "OmniverseRTX",
            ovrtxMode,
            width: 800,
            height: 600,
            path: sceneState.path,
            graphKey: sceneState.graphKey,
            ...camera,
            nativePopup: false,
          },
        }).catch(() => {});
      }
      emitTo("main", "hair-groom-closed", {}).catch(() => {});
    }).then((fn) => { unlistenClose = fn; });
    return () => { unlistenClose?.(); };
  }, [sceneState, camera, ovrtxMode]);

  const queue = useMemo(() => new LatestRenderQueue<Record<string, unknown>, Record<string, unknown>>({
    render: async (req) => {
      const register = Boolean(req.graph && req.graphKey !== registeredGraphRef.current);
      const send = (withGraph: boolean) =>
        invoke<ArrayBuffer>("render_viewport_frame", {
          request: { ...req, ...(withGraph ? {} : { graph: undefined }) },
        });
      let buf: ArrayBuffer;
      try {
        buf = await send(register);
      } catch (err) {
        if (!req.graph || !String(err).includes("Unknown render graph handle")) throw err;
        registeredGraphRef.current = null;
        buf = await send(true);
      }
      if (typeof req.graphKey === "string") {
        registeredGraphRef.current = req.graphKey;
      }
      const view = new DataView(buf);
      const metaLen = view.getUint32(0, true);
      return JSON.parse(textDecoder.decode(new Uint8Array(buf, 4, metaLen))) as Record<string, unknown>;
    },
    present: (res) => {
      const total = typeof res.totalMs === "number" ? res.totalMs.toFixed(1) : "?";
      const sim = typeof res.hairSimMs === "number" ? res.hairSimMs.toFixed(1) : "0.0";
      const ren = typeof res.renderMs === "number" ? res.renderMs.toFixed(1) : "?";
      setStatusText(`Frame: ${total} ms (Sim: ${sim} ms | RTX: ${ren} ms)`);
      if (typeof res.pinnedCount === "number") setPinnedCount(res.pinnedCount);
      if (res.hairGroomNodeId && res.hairGroomDeltas !== undefined) {
        emitTo("main", "hair-groom-commit", {
          nodeId: res.hairGroomNodeId,
          groomDeltas: res.hairGroomDeltas,
        }).catch(() => {});
      }
    },
    error: (err) => setStatusText(`Error: ${String(err)}`),
    busy: () => {},
    schedule: (cb) => window.setTimeout(cb, 0),
    cancel: (id) => window.clearTimeout(id),
  }), []);

  useEffect(() => () => queue.dispose(), [queue]);

  // Trigger render whenever camera, rect, brush, or mode updates
  useEffect(() => {
    if (!sceneState?.path || !parentHwnd) return;
    queue.enqueue(sceneState.cameraSession || "hair-groom-popup", {
      operation: "render",
      renderer: "OmniverseRTX",
      ovrtxMode,
      width: Math.min(1280, Math.max(640, rect.width)),
      height: Math.min(860, Math.max(480, rect.height)),
      path: sceneState.path,
      graph: sceneState.graph,
      graphKey: sceneState.graphKey,
      cameraSession: sceneState.cameraSession || "hair-groom-popup",
      frameFit,
      ...camera,
      interactive: interacting || livePhysics || Boolean(hairBrush),
      materials: sceneState.materials || "auto",
      upAxis: sceneState.upAxis || "Y",
      frame: sceneState.frame ?? 0,
      activeHairNodeId: sceneState.activeHairNodeId,
      hairEditing: true,
      showHairPins: showPins || brushTool === "pin" || brushTool === "unpin",
      hairBrush: hairBrush ? { ...hairBrush, nodeId: sceneState.activeHairNodeId } : undefined,
      nativePopup: true,
      popupSkipImage: true,
      popupParentHwnd: parentHwnd,
      popupRect: rect,
    });
  }, [sceneState, parentHwnd, rect, camera, frameFit, ovrtxMode, interacting, livePhysics, showPins, brushTool, hairBrush, queue]);

  // Live physics loop inside pop-up window
  useEffect(() => {
    if (!livePhysics || !sceneState?.path || !parentHwnd) return;
    const t = window.setTimeout(() => {
      hairDirtyRef.current = true;
      setHairBrush({
        nx: 0.5, ny: 0.5, prevNx: 0.5, prevNy: 0.5,
        radius: brushRadius, strength: brushStrength,
        tool: "physics", livePhysics: true,
        strokeId: strokeIdRef.current, seq: ++brushSeqRef.current,
      });
    }, 16);
    return () => window.clearTimeout(t);
  }, [livePhysics, hairBrush, brushRadius, brushStrength, sceneState, parentHwnd]);

  // Handle Win32 mouse/wheel/key input forwarded from the native D3D11 child HWND
  useEffect(() => {
    let unlisten: (() => void) | null = null;
    listen<PopupInputEvent>("hair-popup-input", (ev) => {
      const e = ev.payload;
      if (e.type === "keydown") {
        if (e.key === "[" || e.key === "]") {
          setBrushRadius((prev) => {
            const next = Math.min(1000, Math.max(0.001, Number((prev * (e.key === "[" ? 0.85 : 1.18)).toFixed(3))));
            setBrushRadiusText(String(next));
            return next;
          });
        } else if (e.key === "Escape") {
          invoke("close_hair_groom_window").catch(() => {});
        }
        return;
      }
      const w = Math.max(1, e.width || rect.width);
      const h = Math.max(1, e.height || rect.height);
      const nx = Math.min(1, Math.max(0, (e.x ?? 0) / w));
      const ny = Math.min(1, Math.max(0, (e.y ?? 0) / h));

      if (e.type === "wheel") {
        const factor = (e.deltaY ?? 0) > 0 ? 0.9 : 1.1;
        setCamera((c) => ({ ...c, zoom: Math.min(50, Math.max(0.02, c.zoom * factor)) }));
        return;
      }
      if (e.type === "mousedown") {
        const btn = e.button ?? 0;
        if (btn === 2 || btn === 1 || e.altKey) {
          const mode = (btn === 1 || (btn === 2 && e.shiftKey)) ? "pan" : "orbit";
          navDragRef.current = { mode, x: e.x ?? 0, y: e.y ?? 0, cam: camera };
          setInteracting(true);
          return;
        }
        if (btn === 0) {
          strokeIdRef.current += 1;
          if (brushTool === "cut" || brushTool === "lengthen") {
            lineStartRef.current = { nx, ny };
          } else {
            brushDragRef.current = { nx, ny };
            hairDirtyRef.current = true;
            setHairBrush({
              nx, ny, prevNx: nx, prevNy: ny,
              radius: brushRadius, strength: brushStrength,
              tool: brushTool, newStroke: true,
              strokeId: strokeIdRef.current, seq: ++brushSeqRef.current,
            });
          }
        }
        return;
      }
      if (e.type === "mousemove") {
        if (navDragRef.current) {
          const d = navDragRef.current;
          const dx = (e.x ?? 0) - d.x;
          const dy = (e.y ?? 0) - d.y;
          if (d.mode === "orbit") {
            setCamera({
              ...d.cam,
              yaw: d.cam.yaw - dx * 0.35,
              pitch: Math.max(-89, Math.min(89, d.cam.pitch + dy * 0.35)),
            });
          } else {
            const scale = 0.002 / Math.max(0.05, d.cam.zoom);
            setCamera({ ...d.cam, panX: d.cam.panX - dx * scale, panY: d.cam.panY + dy * scale });
          }
          return;
        }
        if (brushDragRef.current) {
          const prev = brushDragRef.current;
          brushDragRef.current = { nx, ny };
          hairDirtyRef.current = true;
          setHairBrush({
            nx, ny, prevNx: prev.nx, prevNy: prev.ny,
            radius: brushRadius, strength: brushStrength,
            tool: brushTool,
            strokeId: strokeIdRef.current, seq: ++brushSeqRef.current,
          });
        }
        return;
      }
      if (e.type === "mouseup") {
        if (navDragRef.current) {
          navDragRef.current = null;
          setInteracting(false);
          return;
        }
        if (lineStartRef.current) {
          const start = lineStartRef.current;
          lineStartRef.current = null;
          const payload: Record<string, unknown> = {
            nx, ny, prevNx: start.nx, prevNy: start.ny,
            radius: brushRadius, strength: brushStrength,
            tool: brushTool, newStroke: true, commitUsd: true,
            strokeId: strokeIdRef.current, seq: ++brushSeqRef.current,
          };
          if (brushTool === "cut") payload.cutLine = [start.nx, start.ny, nx, ny];
          if (brushTool === "lengthen") payload.lengthenDrag = [start.nx, start.ny, nx, ny];
          setHairBrush(payload);
          return;
        }
        if (brushDragRef.current) {
          brushDragRef.current = null;
          setHairBrush({
            nx, ny, prevNx: nx, prevNy: ny,
            radius: brushRadius, strength: brushStrength,
            tool: brushTool, settleOnly: true, commitUsd: true,
            strokeId: strokeIdRef.current, seq: ++brushSeqRef.current,
          });
        }
      }
    }).then((fn) => { unlisten = fn; });
    return () => { unlisten?.(); };
  }, [camera, rect, brushTool, brushRadius, brushStrength]);

  return (
    <div style={{ display: "flex", flexDirection: "column", width: "100vw", height: "100vh", background: "#111317", color: "#e6e8ec", overflow: "hidden" }}>
      <div className="viewport-toolbar" style={{ flexWrap: "wrap", gap: 6, padding: "6px 10px", borderBottom: "1px solid #2b2f3a" }}>
        {BRUSH_TOOLS.map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={brushTool === id ? "chosen" : ""}
            onClick={() => setBrushTool(id)}
          >
            {label}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setHairBrush({
            nx: 0.5, ny: 0.5, prevNx: 0.5, prevNy: 0.5,
            radius: brushRadius, strength: brushStrength,
            tool: brushTool, undoStroke: true, commitUsd: true,
            seq: ++brushSeqRef.current,
          })}
        >
          Undo
        </button>
        <button
          type="button"
          className={livePhysics ? "chosen" : ""}
          onClick={() => setLivePhysics((v) => !v)}
        >
          Live Physics
        </button>
        <button
          type="button"
          className={showPins ? "chosen" : ""}
          onClick={() => setShowPins((v) => !v)}
        >
          {pinnedCount > 0 ? `Pins (${pinnedCount})` : "Show Pins"}
        </button>
        {pinnedCount > 0 && (
          <button
            type="button"
            onClick={() => {
              setPinnedCount(0);
              setHairBrush({
                nx: 0.5, ny: 0.5, prevNx: 0.5, prevNy: 0.5,
                radius: brushRadius, strength: brushStrength,
                tool: "pin", clearPins: true, seq: ++brushSeqRef.current,
              });
            }}
          >
            Unpin All
          </button>
        )}
        <label style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12 }}>
          Radius
          <input
            type="range" min={0.01} max={1.0} step={0.01}
            value={Math.min(1, Math.max(0.01, brushRadius))}
            onChange={(e) => {
              const v = Number(e.target.value);
              setBrushRadius(v);
              setBrushRadiusText(String(v));
            }}
            style={{ width: 64 }}
          />
          <input
            type="number" min={0.001} max={1000} step={0.01}
            value={brushRadiusText}
            onChange={(e) => {
              setBrushRadiusText(e.target.value);
              const v = parseFloat(e.target.value);
              if (Number.isFinite(v) && v >= 0.001) setBrushRadius(v);
            }}
            style={{ width: 52 }}
          />
        </label>
        <label style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12 }}>
          Strength
          <input
            type="range" min={0.1} max={3.0} step={0.1}
            value={Math.min(3, Math.max(0.1, brushStrength))}
            onChange={(e) => {
              const v = Number(e.target.value);
              setBrushStrength(v);
              setBrushStrengthText(String(v));
            }}
            style={{ width: 64 }}
          />
          <input
            type="number" min={0.05} max={5} step={0.05}
            value={brushStrengthText}
            onChange={(e) => {
              setBrushStrengthText(e.target.value);
              const v = parseFloat(e.target.value);
              if (Number.isFinite(v) && v >= 0.05) setBrushStrength(v);
            }}
            style={{ width: 46 }}
          />
        </label>
        <button
          type="button"
          className={ovrtxMode === "realtime" ? "chosen" : ""}
          onClick={() => setOvrtxMode((m) => (m === "realtime" ? "pathTracing" : "realtime"))}
          title="Toggle Real-Time vs Path Tracing in pop-up"
        >
          {ovrtxMode === "realtime" ? "Real-Time" : "Path Tracing"}
        </button>
        <button type="button" onClick={() => setFrameFit((n) => n + 1)}>
          Frame
        </button>
        <button
          type="button"
          className="chosen"
          onClick={() => invoke("close_hair_groom_window").catch(() => {})}
        >
          Done
        </button>
      </div>
      <div ref={hostRef} style={{ flex: 1, position: "relative", background: "#0b0d11" }} />
      <div style={{ padding: "4px 10px", fontSize: 12, color: "#9aa2b1", borderTop: "1px solid #2b2f3a", display: "flex", justifyContent: "space-between" }}>
        <span>{statusText}</span>
        <span>Left-drag: Brush • Alt+Left / Right-drag: Orbit • Middle-drag: Pan • Wheel: Zoom • [ / ]: Radius</span>
      </div>
    </div>
  );
}
