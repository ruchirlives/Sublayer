import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent, type ReactNode } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { LatestRenderQueue, previewSize } from "./renderQueue";
import { NumberInput } from "./NumberInput";
import type { Transform, ViewportCamera, ViewportSettings } from "./fileRegistry";
import { TransformGizmo } from "./TransformGizmo";

type Delegate = { id: string; name: string; support: string };
type OvrtxMode = "realtime" | "pathTracing";
type CameraAnchor = { session: string; fit: number; center: [number, number, number]; radius: number };
type Capabilities = {
  version: string;
  viewport: boolean;
  renderers: Delegate[];
  gpu?: string;
  viewportError?: string;
};
type Frame = {
  image: string;
  width: number;
  height: number;
  renderer: string;
  gpu: string;
  converged: boolean;
  cameraAnchor?: CameraAnchor;
  raySamples?:{current:number;target:number};
  frame: number;
  renderMs: number;
  captureMs: number;
  totalMs: number;
  roundTripMs: number;
  pngBytes: number;
  interactive?: boolean;
  pickedPrim?: string | null;
  encoding?: string;
  upAxis: string;
  materialMode?: "scene" | "solid" | "mixed";
  selectionInfo?: { materialCount: number; meshCount: number };
  missingAssets?: { path: string; usedBy: string[] }[];
  warnings?: string[];
  graphStats?: {
    uniqueSourceAssets: number;
    prototypes: number;
    instances: number;
  };
  vramUsedMb?: number | null;
  vramTotalMb?: number | null;
  cachedScenes?: number;
  updateKind?: "cache-hit" | "material-delta" | "full-rebuild";
  sceneMs?: number;
  stepMs?: number;
};
const home = { yaw: 35, pitch: 20, zoom: 1, panX: 0, panY: 0 };
function hashContent(value: string): string {
  let h1 = 0xdeadbeef, h2 = 0x41c64e6d;
  for (let i = 0; i < value.length; i++) {
    const ch = value.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36);
}

export function NativeViewport({
  path,
  graph,
  emptyTitle,
  emptyMessage,
  previewControls,
  graphModeRequest,
  selectedPrim,
  cameraKey,
  comparison,
  viewportSettings,
  transformGizmo,
  onRuntime,
  onMode,
  onSelectPrim,
  onViewportSettingsChange,
  onViewportError,
}: {
  path: string;
  graph?: string;
  emptyTitle?: string;
  emptyMessage?: string;
  previewControls?: ReactNode;
  graphModeRequest?: number;
  selectedPrim?:string;
  cameraKey?:string;
  comparison?:string;
  viewportSettings?: ViewportSettings;
  transformGizmo?: {
    transform: Transform;
    onChange: (next: Transform) => void;
    onCommit?: () => void;
  } | null;
  onRuntime: (text: string) => void;
  onMode: (scene: boolean) => void;
  onSelectPrim?: (path: string) => void;
  onViewportSettingsChange?: (patch: Partial<ViewportSettings>) => void;
  onViewportError?: (error: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [renderer, setRenderer] = useState(viewportSettings?.renderer || "");
  const [ovrtxMode, setOvrtxMode] = useState<OvrtxMode>(viewportSettings?.ovrtxMode || "pathTracing");
  const [camera, setCamera] = useState<ViewportCamera>(viewportSettings?.camera ?? home);
  const [cameraSession] = useState(() => crypto.randomUUID());
  const [frameFit, setFrameFit] = useState(0);
  const [frameTarget, setFrameTarget] = useState<string | undefined>(undefined);
  const [isolatedPrim, setIsolatedPrim] = useState<string | null>(null);
  const [hiddenPrims, setHiddenPrims] = useState<string[]>([]);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; prim: string | null; picking: boolean } | null>(null);
  const [upAxis, setUpAxis] = useState<"Y" | "Z">(viewportSettings?.upAxis || "Y");
  const [size, setSize] = useState({ width: 960, height: 640 });
  const [frame, setFrame] = useState<Frame | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    onViewportError?.(error);
  }, [error, onViewportError]);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [activeStatus, setActiveStatus] = useState<string | null>(null);
  const [presentedContext, setPresentedContext] = useState("");
  const latestContextKeyRef = useRef("");
  const [sceneMode, setSceneMode] = useState(false);
  const [revision, setRevision] = useState(0);
  const [reloadTrigger, setReloadTrigger] = useState(0);
  const previousReload = useRef(0);
  const [retry, setRetry] = useState(0);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [playStart, setPlayStart] = useState(1);
  const [playEnd, setPlayEnd] = useState(48);
  const [playLoop, setPlayLoop] = useState(true);
  const [combMode, setCombMode] = useState(false);
  const [brushRadius, setBrushRadius] = useState(0.06);
  const [brushRadiusText, setBrushRadiusText] = useState("0.06");
  const [brushStrength, setBrushStrength] = useState(1.0);
  const [brushStrengthText, setBrushStrengthText] = useState("1.0");
  const [hairBrush, setHairBrush] = useState<{
    nx: number;
    ny: number;
    prevNx: number;
    prevNy: number;
    radius: number;
    strength: number;
    newStroke?: boolean;
    strokeId?: number;
    settleOnly?: boolean;
    commitUsd?: boolean;
    seq: number;
  } | null>(null);
  const [hairSettleTicks, setHairSettleTicks] = useState(0);
  const [brushingActive, setBrushingActive] = useState(false);
  const brushSeqRef = useRef(0);
  const brushStrokeIdRef = useRef(0);
  const brushNewStrokeRef = useRef(false);
  const brushDragRef = useRef<{ nx: number; ny: number } | null>(null);
  const materials = "auto";
  const [interacting, setInteracting] = useState(false);

  const sameCamera = (
    a: typeof camera | null | undefined,
    b: typeof camera | null | undefined,
  ) =>
    Boolean(a && b) &&
    a!.yaw === b!.yaw &&
    a!.pitch === b!.pitch &&
    a!.zoom === b!.zoom &&
    a!.panX === b!.panX &&
    a!.panY === b!.panY &&
    a!.fov === b!.fov &&
    a!.dofEnabled === b!.dofEnabled &&
    a!.focusDistance === b!.focusDistance &&
    a!.fStop === b!.fStop;

  useEffect(() => {
    if (interacting || !viewportSettings?.camera) return;
    if (!sameCamera(viewportSettings.camera, camera)) {
      setCamera(viewportSettings.camera);
    }
  }, [viewportSettings?.camera]);

  useEffect(() => {
    if (interacting) return;
    if (sameCamera(viewportSettings?.camera, camera)) {
      return;
    }
    onViewportSettingsChange?.({ camera });
  }, [camera, interacting]);

  useEffect(() => {
    if (viewportSettings?.renderer) setRenderer(viewportSettings.renderer);
    if (viewportSettings?.ovrtxMode) setOvrtxMode(viewportSettings.ovrtxMode);
    if (viewportSettings?.upAxis) setUpAxis(viewportSettings.upAxis);
  }, [
    viewportSettings?.renderer,
    viewportSettings?.ovrtxMode,
    viewportSettings?.upAxis,
  ]);
  const wheelEnd = useRef<ReturnType<typeof setTimeout> | null>(null);
  const queue = useRef<LatestRenderQueue<
    Record<string, unknown>,
    Frame
  > | null>(null);
  const drag = useRef<{
    x: number;
    y: number;
    pan: boolean;
    dolly: boolean;
    camera: typeof home;
  } | null>(null);
  const cameraAnchor = useRef<CameraAnchor | undefined>(undefined);
  useEffect(()=>{
    setSceneMode(false);setInteracting(false);drag.current=null;
    if(wheelEnd.current !== null)clearTimeout(wheelEnd.current);
    onMode(false);
  },[graphModeRequest,onMode]);


  useEffect(() => {
    if (error || !path || !capabilities?.viewport) {
      setPlaying(false);
    }
  }, [error, path, capabilities?.viewport]);

  useEffect(() => {
    if (hairSettleTicks <= 0 || busy || brushDragRef.current) return;
    const timer = window.setTimeout(() => {
      const next = hairSettleTicks - 1;
      setHairSettleTicks(next);
      if (next > 0) {
        setHairBrush({
          nx: 0.5,
          ny: 0.5,
          prevNx: 0.5,
          prevNy: 0.5,
          radius: brushRadius,
          strength: brushStrength,
          settleOnly: true,
          commitUsd: next === 1,
          seq: ++brushSeqRef.current,
        });
      } else {
        setHairBrush(null);
        setInteracting(false);
      }
    }, 16);
    return () => window.clearTimeout(timer);
  }, [hairSettleTicks, busy, brushRadius, brushStrength]);

  useEffect(() => {
    if (!brushingActive || busy) return;
    const timer = window.setTimeout(() => {
      const cur = brushDragRef.current;
      if (!cur) return;
      const isNewStroke = brushNewStrokeRef.current;
      brushNewStrokeRef.current = false;
      setHairBrush({
        nx: cur.nx,
        ny: cur.ny,
        prevNx: cur.nx,
        prevNy: cur.ny,
        radius: brushRadius,
        strength: brushStrength,
        newStroke: isNewStroke,
        strokeId: brushStrokeIdRef.current,
        seq: ++brushSeqRef.current,
      });
    }, 16);
    return () => window.clearTimeout(timer);
  }, [brushingActive, busy, hairBrush, brushRadius, brushStrength]);

  useEffect(() => {
    if (!playing || busy || error || !path || !capabilities?.viewport) return;
    const delayMs = renderer === "OmniverseRTX" ? 80 : 35;
    const timer = window.setTimeout(() => {
      setTime((cur) => {
        if (cur < playStart) return playStart;
        if (cur >= playEnd) {
          if (!playLoop) {
            setPlaying(false);
            return playEnd;
          }
          return playStart;
        }
        return cur + 1;
      });
    }, delayMs);
    return () => window.clearTimeout(timer);
  }, [playing, busy, error, path, capabilities?.viewport, renderer, playStart, playEnd, playLoop, time]);

  useEffect(() => {
    const registeredGraphs=new Set<string>();
    const scheduler = new LatestRenderQueue<Record<string, unknown>, Frame>({
      render: async (request) => {
        const {graph,...cameraRequest}=request;
        const graphKey=typeof request.graphKey==="string" ? request.graphKey : "";
        const registrationKey=String(request.renderer)+":"+graphKey;
        const anchor = cameraAnchor.current;
        const retainedAnchor = request.renderer === "OmniverseRTX" &&
          anchor?.session === request.cameraSession && anchor?.fit === request.frameFit ? anchor : undefined;
        const isExplicitReload = typeof request.reloadTrigger === "number" && request.reloadTrigger > 0 && request.reloadTrigger !== previousReload.current;
        if (typeof request.reloadTrigger === "number") previousReload.current = request.reloadTrigger;
        const hb = cameraRequest.hairBrush as { nx?: number; ny?: number } | undefined;
        if (hb && typeof hb.nx === "number" && typeof hb.ny === "number" && brushDragRef.current) {
          brushDragRef.current = { nx: hb.nx, ny: hb.ny };
        }
        const render=async(register:boolean)=>invoke<Frame>("usd_request",{request:{...cameraRequest,cameraAnchor:retainedAnchor,...(register?{graph}:{}),reload:isExplicitReload}});
        let result:Frame;
        try{result=await render(!!graph&&(!graphKey||!registeredGraphs.has(registrationKey)));}
        catch(error){
          if(!graph || !String(error).includes("Unknown render graph handle"))throw error;
          result=await render(true);
        }
        if(graphKey){if(registeredGraphs.size>=32)registeredGraphs.clear();registeredGraphs.add(registrationKey);}
        // Keep the old image visible until its replacement is decoded, and apply
        // backpressure to decoding as well as rendering/transport.
        const image = new Image();
        image.src = result.image;
        await image.decode();
        return result;
      },
      present: (result) => {
        if (result.cameraAnchor) cameraAnchor.current = result.cameraAnchor;
        setFrame(result);
        setPresentedContext(latestContextKeyRef.current);
        setActiveStatus(null);
        setError("");
      },
      error: (error) => {
        setError(String(error));
        setActiveStatus(null);
      },
      busy: (b) => {
        busyRef.current = b;
        setBusy(b);
        if (b) {
          setError("");
        } else {
          setActiveStatus(null);
        }
      },
      schedule: (callback) => window.requestAnimationFrame(callback),
      cancel: (id) => window.cancelAnimationFrame(id),
    });
    queue.current = scheduler;
    return () => {
      scheduler.dispose();
      if (queue.current === scheduler) queue.current = null;
      if (wheelEnd.current !== null) clearTimeout(wheelEnd.current);
    };
  }, []);

  useEffect(() => {
    if (!isTauri()) return;
    let unlisten: (() => void) | undefined;
    listen<{ phase?: string; details?: string }>("renderer-status", (event) => {
      if (busyRef.current && event.payload?.phase) {
        setActiveStatus(event.payload.phase);
      }
    }).then((fn) => {
      unlisten = fn;
    });
    return () => {
      if (unlisten) unlisten();
    };
  }, []);
  useEffect(() => {
    if (!isTauri()) {
      onRuntime("Browser mode · launch desktop for native Hydra rendering");
      return;
    }
    let active = true;
    invoke<Capabilities>("usd_request", {
      request: { operation: "capabilities" },
    })
      .then((c) => {
        if (!active) return;
        setCapabilities(c);
        setError(
          c.viewport ? "" : c.viewportError || "Imaging runtime unavailable",
        );
        setRenderer((current) =>
          c.renderers.some((r) => r.id === current)
            ? current
            : c.renderers.find((r) => r.name === "Storm")?.id ||
              c.renderers[0]?.id ||
              "",
        );
        onRuntime(
          `OpenUSD ${c.version} · ${c.viewport ? c.gpu : "inspection only; imaging unavailable"}`,
        );
      })
      .catch((e) => {
        if (active) {
          setError(String(e));
          onRuntime(String(e));
        }
      });
    return () => {
      active = false;
    };
  }, [onRuntime, retry]);
  useEffect(() => {
    const observer = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      if (width < 1 || height < 1) return;
      // Retain full display size; interaction requests use a cheaper preview.
      const factor = Math.min(1, 1440 / width, 1080 / height);
      setSize({
        width: Math.max(64, Math.round(width * factor)),
        height: Math.max(64, Math.round(height * factor)),
      });
    });
    if (host.current) observer.observe(host.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    setIsolatedPrim(null);
    setHiddenPrims([]);
    setContextMenu(null);
    setError("");
    drag.current = null;
    setInteracting(false);
    if (wheelEnd.current !== null) clearTimeout(wheelEnd.current);
  }, [cameraKey || path]);
  const graphKey=useMemo(()=>graph?hashContent(graph):undefined,[graph]);
  const hasSimulatedHair = useMemo(() => {
    if (!graph) return false;
    try {
      const g = JSON.parse(graph);
      const t = g?.nodes?.find((n: { id?: string }) => n.id === g.targetNodeId);
      return t?.operation === "hair" && Boolean(t?.hair?.simEnabled);
    } catch {
      return false;
    }
  }, [graph]);
  useEffect(() => {
    if (!hasSimulatedHair && combMode) setCombMode(false);
  }, [hasSimulatedHair, combMode]);
  const request = useMemo(
    () => ({
      operation: "render",
      comparison,
      cameraSession,
      frameFit,
      frameTarget,
      isolatedPrim: isolatedPrim || undefined,
      hiddenPrims: hiddenPrims.length ? hiddenPrims : undefined,
      upAxis,
      selection:selectedPrim?[selectedPrim]:[],
      path,
      graph,
      graphKey,
      renderer,
      ovrtxMode: renderer === "OmniverseRTX" ? ovrtxMode : undefined,
      ...previewSize(size.width, size.height, renderer === "OmniverseRTX" ? false : interacting),
      ...camera,
      interactive: interacting,
      frame: time,
      materials,
      hairBrush: hairBrush || undefined,
      revision,
      reloadTrigger,
    }),
    [
      path,
      graph,
      graphKey,
      renderer,
      ovrtxMode,
      size,
      camera,
      time,
      materials,
      hairBrush,
      revision,
      reloadTrigger,
      interacting,
      selectedPrim,
      isolatedPrim,
      hiddenPrims,
      comparison,
      cameraSession,
      frameFit,
      frameTarget,
      upAxis,
    ],
  );
  const context = JSON.stringify([comparison,selectedPrim,isolatedPrim,hiddenPrims,
    path,
    graphKey,
    renderer,
    request.ovrtxMode,
    time,
    materials,
    revision,
    upAxis,
  ]);
  const currentContextKey = `${context}:${reloadTrigger}`;
  latestContextKeyRef.current = currentContextKey;
  const showBusyStatus =
    busy && !hairBrush && !playing && (!frame || presentedContext !== currentContextKey || activeStatus !== null);
  const [busyElapsedSec, setBusyElapsedSec] = useState(0);
  useEffect(() => {
    if (!showBusyStatus) {
      setBusyElapsedSec(0);
      return;
    }
    const started = Date.now();
    const id = window.setInterval(() => {
      setBusyElapsedSec(Math.floor((Date.now() - started) / 1000));
    }, 1000);
    return () => window.clearInterval(id);
  }, [showBusyStatus, activeStatus]);
  useEffect(() => {
    if (!isTauri() || !capabilities?.viewport || !path || !renderer) {
      queue.current?.clear();
      if (!path) setPresentedContext("");
      return;
    }
    queue.current?.enqueue(context, request);
  }, [request, context, capabilities, path, renderer]);
  useEffect(() => {
    setFrame(null);
  }, [renderer, request.ovrtxMode, isolatedPrim, hiddenPrims]);
  useEffect(() => {
    if (!contextMenu) return;
    const onKeyDown = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") setContextMenu(null);
    };
    const onWindowPointerDown = (e: globalThis.PointerEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && target.closest(".scene-context-menu")) return;
      setContextMenu(null);
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("pointerdown", onWindowPointerDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("pointerdown", onWindowPointerDown);
    };
  }, [contextMenu]);
  useEffect(()=>{
    if(renderer !== "OmniverseRTX"||interacting||busy||error||!frame||frame.renderer!==renderer||frame.converged)return;
    const timer=setTimeout(()=>queue.current?.enqueue(context,request),40);
    return ()=>clearTimeout(timer);
  },[renderer,interacting,busy,error,frame,context,request]);

  const clickStart = useRef<{ x: number; y: number; ctrl: boolean; button: number; dolly?: boolean } | null>(null);
  const pickPrimAtClientPoint = useCallback(
    async (clientX: number, clientY: number, select = true): Promise<string | null> => {
      if (!host.current) return null;
      const rect = host.current.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return null;
      const u = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      const v = Math.max(0, Math.min(1, (clientY - rect.top) / rect.height));
      try {
        const res = await invoke<Frame>("usd_request", {
          request: {
            ...request,
            pick: [u, v],
            interactive: false,
          },
        });
        const picked = res?.pickedPrim ?? null;
        if (picked && select && onSelectPrim) {
          onSelectPrim(picked);
        }
        return picked;
      } catch (err) {
        console.warn("Pick query failed:", err);
        return null;
      }
    },
    [request, onSelectPrim],
  );
  const switchToGraphMode = useCallback(() => {
    setSceneMode(false);
    setContextMenu(null);
    drag.current = null;
    setInteracting(false);
    if (wheelEnd.current !== null) clearTimeout(wheelEnd.current);
    onMode(false);
  }, [onMode]);
  const switchToSceneMode = useCallback(() => {
    setSceneMode(true);
    onMode(true);
  }, [onMode]);
  const graphPickRef = useRef({ sceneMode, pickPrimAtClientPoint, switchToSceneMode });
  graphPickRef.current = { sceneMode, pickPrimAtClientPoint, switchToSceneMode };
  useEffect(() => {
    let pending: { x: number; y: number } | null = null;
    const onDown = (event: globalThis.PointerEvent) => {
      if (graphPickRef.current.sceneMode) return;
      if (event.button !== 0 || !(event.ctrlKey || event.metaKey)) return;
      if (!host.current) return;
      if (
        event.target instanceof Element &&
        event.target.closest(".viewport-bar, .viewport-caption, .override-backdrop, button, input, select, textarea, a")
      ) {
        return;
      }
      const rect = host.current.getBoundingClientRect();
      if (
        event.clientX < rect.left ||
        event.clientX > rect.right ||
        event.clientY < rect.top ||
        event.clientY > rect.bottom
      ) {
        return;
      }
      pending = { x: event.clientX, y: event.clientY };
    };
    const onUp = (event: globalThis.PointerEvent) => {
      const start = pending;
      pending = null;
      if (!start || graphPickRef.current.sceneMode) return;
      if (!(event.ctrlKey || event.metaKey)) return;
      if (Math.hypot(event.clientX - start.x, event.clientY - start.y) < 6) {
        void graphPickRef.current.pickPrimAtClientPoint(event.clientX, event.clientY);
      }
    };
    const onCancel = () => {
      pending = null;
    };
    const onDblClick = (event: globalThis.MouseEvent) => {
      if (graphPickRef.current.sceneMode) return;
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      if (!host.current) return;
      if (
        !(event.target instanceof Element) ||
        !event.target.closest(".react-flow__pane") ||
        event.target.closest(
          ".react-flow__node, .react-flow__edge, .react-flow__controls, .react-flow__minimap, .viewport-controls, .viewport-bar, .viewport-caption, .override-backdrop, .floating-inspector, button, input, select, textarea, a",
        )
      ) {
        return;
      }
      const rect = host.current.getBoundingClientRect();
      if (
        event.clientX < rect.left ||
        event.clientX > rect.right ||
        event.clientY < rect.top ||
        event.clientY > rect.bottom
      ) {
        return;
      }
      event.preventDefault();
      window.getSelection()?.removeAllRanges();
      graphPickRef.current.switchToSceneMode();
    };
    const onMouseDown = (event: globalThis.MouseEvent) => {
      if (graphPickRef.current.sceneMode || event.detail < 2) return;
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      if (
        !(event.target instanceof Element) ||
        !event.target.closest(".react-flow__pane") ||
        event.target.closest(
          ".react-flow__node, .react-flow__edge, .react-flow__controls, .react-flow__minimap, .viewport-controls, .viewport-bar, .viewport-caption, .override-backdrop, .floating-inspector, button, input, select, textarea, a",
        )
      ) {
        return;
      }
      event.preventDefault();
    };
    window.addEventListener("pointerdown", onDown, true);
    window.addEventListener("pointerup", onUp, true);
    window.addEventListener("pointercancel", onCancel, true);
    window.addEventListener("mousedown", onMouseDown, true);
    window.addEventListener("dblclick", onDblClick, true);
    return () => {
      window.removeEventListener("pointerdown", onDown, true);
      window.removeEventListener("pointerup", onUp, true);
      window.removeEventListener("pointercancel", onCancel, true);
      window.removeEventListener("mousedown", onMouseDown, true);
      window.removeEventListener("dblclick", onDblClick, true);
    };
  }, []);

  function pointerDown(event: PointerEvent<HTMLDivElement>) {
    if (!sceneMode) return;
    if (contextMenu) setContextMenu(null);
    if (wheelEnd.current !== null) clearTimeout(wheelEnd.current);
    const isCtrl = event.ctrlKey || event.metaKey;
    const isDolly = isCtrl && event.button === 2;
    clickStart.current = {
      x: event.clientX,
      y: event.clientY,
      ctrl: isCtrl && event.button === 0,
      button: event.button,
      dolly: isDolly,
    };
    if (isCtrl && !isDolly) {
      // Don't start orbit/pan drag when Ctrl-clicking for selection
      return;
    }
    if (combMode && hasSimulatedHair && event.button === 0 && !event.shiftKey && !isDolly) {
      const rect = host.current?.getBoundingClientRect();
      if (rect && rect.width > 0 && rect.height > 0) {
        const nx = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
        const ny = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
        brushDragRef.current = { nx, ny };
        brushNewStrokeRef.current = false;
        const nextStrokeId = ++brushStrokeIdRef.current;
        setHairSettleTicks(0);
        setBrushingActive(true);
        setInteracting(true);
        setHairBrush({
          nx,
          ny,
          prevNx: nx,
          prevNy: ny,
          radius: brushRadius,
          strength: brushStrength,
          newStroke: true,
          strokeId: nextStrokeId,
          seq: ++brushSeqRef.current,
        });
        event.currentTarget.setPointerCapture(event.pointerId);
        return;
      }
    }
    setInteracting(true);
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = {
      x: event.clientX,
      y: event.clientY,
      pan: !isDolly && (event.shiftKey || event.button === 1 || event.button === 2),
      dolly: isDolly,
      camera,
    };
  }
  function endInteraction() {
    drag.current = null;
    brushDragRef.current = null;
    setBrushingActive(false);
    setHairSettleTicks(0);
    setHairBrush(null);
    setInteracting(false);
  }
  async function handlePointerUp(event: PointerEvent<HTMLDivElement>) {
    const cs = clickStart.current;
    clickStart.current = null;
    const dist = cs ? Math.hypot(event.clientX - cs.x, event.clientY - cs.y) : Infinity;
    if (brushDragRef.current) {
      brushDragRef.current = null;
      setBrushingActive(false);
      setHairSettleTicks(30);
      return;
    }
    if (drag.current) {
      if (dist >= 6) {
        pointerMove(event);
      } else {
        // Restore pre-jitter camera if it was a right-click rather than a drag
        setCamera(drag.current.camera);
      }
      endInteraction();
      if (cs && cs.button === 2 && !cs.dolly && dist < 6) {
        const rect = host.current?.getBoundingClientRect();
        const x = rect ? Math.min(Math.max(8, event.clientX - rect.left), Math.max(8, rect.width - 196)) : event.clientX;
        const y = rect ? Math.min(Math.max(8, event.clientY - rect.top), Math.max(8, rect.height - 230)) : event.clientY;
        setContextMenu({ x, y, prim: null, picking: true });
        const picked = await pickPrimAtClientPoint(event.clientX, event.clientY, false);
        setContextMenu((cur) => (cur ? { ...cur, prim: picked, picking: false } : null));
      }
      return;
    }
    endInteraction();
    if (cs && cs.ctrl) {
      if (dist < 6) {
        await pickPrimAtClientPoint(event.clientX, event.clientY);
      }
    }
  }
  function pointerMove(event: PointerEvent<HTMLDivElement>) {
    if (brushDragRef.current) {
      const rect = host.current?.getBoundingClientRect();
      if (!rect || rect.width <= 0 || rect.height <= 0) return;
      const nx = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
      const ny = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
      const prev = brushDragRef.current;
      brushDragRef.current = { nx, ny };
      const isNewStroke = brushNewStrokeRef.current;
      brushNewStrokeRef.current = false;
      setHairBrush({
        nx,
        ny,
        prevNx: prev.nx,
        prevNy: prev.ny,
        radius: brushRadius,
        strength: brushStrength,
        newStroke: isNewStroke,
        strokeId: brushStrokeIdRef.current,
        seq: ++brushSeqRef.current,
      });
      return;
    }
    const start = drag.current;
    if (!start) return;
    const dx = event.clientX - start.x,
      dy = event.clientY - start.y;
    setCamera(
      start.dolly
        ? {
            ...start.camera,
            zoom: Math.max(
              0.002,
              Math.min(100, start.camera.zoom * Math.exp((dy - dx) * 0.005)),
            ),
          }
        : start.pan
          ? {
              ...start.camera,
              panX: start.camera.panX - (dx / 250) * start.camera.zoom,
              panY: start.camera.panY + (dy / 250) * start.camera.zoom,
            }
          : {
              ...start.camera,
              yaw: start.camera.yaw - dx * 0.4,
              pitch: Math.max(-89, Math.min(89, start.camera.pitch + dy * 0.4)),
            },
    );
  }
  return (
    <>
      <div ref={host} className="native-viewport">
        {frame ? (
          <img
            className="hydra-frame"
            src={frame.image}
            alt={`USD stage rendered by ${renderer} on ${frame.gpu}`}
            draggable={false}
          />
        ) : (
          <div className="placeholder">
            <div>◈</div>
            <h1>{!path && emptyTitle ? emptyTitle : busy ? "Rendering scene…" : error ? "Unable to preview scene" : path ? "Scene preview" : "Your stage, connected."}</h1>
            <p>OPENUSD / HYDRA</p>
            <small>
              {!isTauri()
                ? "Native preview requires the desktop app."
                : !path
                  ? emptyMessage || "Assign a USD root layer or published output to preview."
                  : busy
                    ? activeStatus || "Rendering…"
                    : error || "Choose an available renderer."}
            </small>
          </div>
        )}
        <div
          className={"scene-input " + (sceneMode ? "enabled" : "")}
          onContextMenu={(e) => e.preventDefault()}
          onMouseDown={(e) => {
            if (e.detail > 1) e.preventDefault();
          }}
          onPointerDown={pointerDown}
          onPointerMove={pointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={endInteraction}
          onLostPointerCapture={endInteraction}
          onDoubleClick={(e) => {
            if (!sceneMode || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
            e.preventDefault();
            window.getSelection()?.removeAllRanges();
            switchToGraphMode();
          }}
          onWheel={(e) => {
            if (sceneMode) {
              setInteracting(true);
              if (wheelEnd.current !== null) clearTimeout(wheelEnd.current);
              // Only the final refinement waits for wheel input to settle.
              wheelEnd.current = setTimeout(() => {
                if (!drag.current) setInteracting(false);
              }, 140);
              setCamera((c) => ({
                ...c,
                zoom: Math.max(
                  0.002,
                  Math.min(100, c.zoom * Math.exp(e.deltaY * 0.001)),
                ),
              }));
            }
          }}
        />
        {transformGizmo && frame && (
          <TransformGizmo
            transform={transformGizmo.transform}
            camera={camera}
            upAxis={upAxis}
            width={size.width}
            height={size.height}
            radius={frame.cameraAnchor?.radius ?? cameraAnchor.current?.radius ?? 1}
            interactive={sceneMode}
            onChange={transformGizmo.onChange}
            onCommit={transformGizmo.onCommit}
          />
        )}
        {sceneMode && contextMenu && (
          <div
            className="scene-context-menu"
            style={{ left: contextMenu.x, top: contextMenu.y }}
            onContextMenu={(e) => e.preventDefault()}
            onPointerDown={(e) => e.stopPropagation()}
          >
            <div className="scene-context-header">
              {contextMenu.picking
                ? "Picking prim…"
                : contextMenu.prim || "No prim under cursor"}
            </div>
            <button
              type="button"
              disabled={contextMenu.picking || !contextMenu.prim || !onSelectPrim}
              onClick={() => {
                const target = contextMenu.prim;
                setContextMenu(null);
                if (target && onSelectPrim) onSelectPrim(target);
              }}
            >
              Select
            </button>
            <button
              type="button"
              disabled={!selectedPrim || !onSelectPrim}
              onClick={() => {
                setContextMenu(null);
                if (onSelectPrim) onSelectPrim("");
              }}
            >
              Deselect
            </button>
            <button
              type="button"
              disabled={contextMenu.picking || !contextMenu.prim}
              onClick={() => {
                const target = contextMenu.prim;
                setContextMenu(null);
                if (!target) return;
                setHiddenPrims((prev) => (prev.includes(target) ? prev : [...prev, target]));
              }}
            >
              Hide prim
            </button>
            <button
              type="button"
              disabled={contextMenu.picking || !contextMenu.prim}
              onClick={() => {
                const target = contextMenu.prim;
                setContextMenu(null);
                if (!target) return;
                setIsolatedPrim((cur) => (cur === target ? null : target));
              }}
            >
              {isolatedPrim && contextMenu.prim === isolatedPrim ? "Unisolate prim" : "Isolate prim"}
            </button>
            <button
              type="button"
              disabled={hiddenPrims.length === 0 && !isolatedPrim}
              onClick={() => {
                setContextMenu(null);
                setHiddenPrims([]);
                setIsolatedPrim(null);
              }}
            >
              Show all
            </button>
            <button
              type="button"
              disabled={contextMenu.picking || !contextMenu.prim}
              onClick={() => {
                const target = contextMenu.prim;
                setContextMenu(null);
                if (!target) return;
                setCamera((c) => ({ ...c, panX: 0, panY: 0 }));
                setFrameTarget(target);
                setFrameFit((v) => v + 1);
              }}
            >
              Set orbit pivot
            </button>
          </div>
        )}
        {showBusyStatus && (
          <div className="viewport-busy-hud" role="status" aria-live="polite" style={{ display: "grid", gap: 4 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span className="viewport-busy-dot" />
              <span className="viewport-busy-text">
                {activeStatus || "Preparing scene…"}
                {busyElapsedSec >= 2 ? ` (${busyElapsedSec}s)` : ""}
              </span>
            </div>
            <div
              style={{
                width: "100%",
                height: 2,
                borderRadius: 2,
                background: "rgba(255,255,255,0.12)",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  width: "35%",
                  height: "100%",
                  borderRadius: 2,
                  background: "#a0dfb5",
                  animation: "operation-progress 1.4s ease-in-out infinite",
                }}
              />
            </div>
          </div>
        )}
      </div>
      {previewControls}
      <div className="toolbar viewport-controls">
        <div className="mode-buttons">
          <button
            className={!sceneMode ? "chosen" : ""}
            onClick={switchToGraphMode}
          >
            Graph
          </button>
          <button
            className={sceneMode ? "chosen" : ""}
            onClick={switchToSceneMode}
          >
            Scene
          </button>
          <button onClick={() => { setCamera(home); setFrameTarget(undefined); setFrameFit(value => value + 1); }}>Frame all</button>
          <button
            disabled={!selectedPrim}
            title={selectedPrim ? `Frame selected prim: ${selectedPrim}` : "Select a prim to frame"}
            onClick={() => {
              if (selectedPrim) {
                setCamera(home);
                setFrameTarget(selectedPrim);
                setFrameFit(value => value + 1);
              }
            }}
          >
            Frame selection
          </button>
          <button
            disabled={!selectedPrim && !isolatedPrim}
            className={isolatedPrim ? "chosen" : ""}
            title={
              isolatedPrim
                ? `Isolate active for: ${isolatedPrim} (click to restore)`
                : selectedPrim
                ? `Isolate ${selectedPrim}`
                : "Select a prim to isolate"
            }
            onClick={() => {
              setIsolatedPrim((current) => (current ? null : selectedPrim || null));
            }}
          >
            {isolatedPrim ? "Isolated" : "Isolate"}
          </button>
          <button
            disabled={!path || !capabilities?.viewport}
            onClick={() => {
              setReloadTrigger((r) => r + 1);
              setRevision((r) => r + 1);
            }}
          >
            Reload
          </button>
          <button
            disabled={!isTauri()}
            title="Flush cached GPU scenes, geometry, and shaders from VRAM"
            onClick={async () => {
              if (!isTauri()) return;
              try {
                await invoke("usd_request", { request: { operation: "clear_scene" } });
              } catch {
                // ignore
              }
              setFrame(null);
              setReloadTrigger((r) => r + 1);
              setRevision((r) => r + 1);
            }}
          >
            Clear scene
          </button>
          {frame?.vramUsedMb != null && frame?.vramTotalMb != null && (
            <span
              title={`${frame.cachedScenes ?? 1} scene(s) resident in GPU memory`}
              style={{
                fontSize: "11px",
                padding: "3px 7px",
                borderRadius: "4px",
                background: "#0e181c",
                border:
                  frame.vramUsedMb / frame.vramTotalMb > 0.85
                    ? "1px solid #d97757"
                    : "1px solid #283a42",
                color:
                  frame.vramUsedMb / frame.vramTotalMb > 0.85
                    ? "#f4a261"
                    : "#9eb6ab",
                whiteSpace: "nowrap",
              }}
            >
              VRAM {(frame.vramUsedMb / 1024).toFixed(1)} / {(frame.vramTotalMb / 1024).toFixed(1)} GB
              {frame.cachedScenes ? ` (${frame.cachedScenes})` : ""}
            </span>
          )}
          {hasSimulatedHair && (
            <div style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
              <button
                type="button"
                className={combMode ? "chosen" : ""}
                title="Toggle mouse hair comb / grab brush in Scene mode"
                onClick={() => setCombMode((v) => !v)}
              >
                {combMode ? "Comb (Active)" : "Comb Hair"}
              </button>
              {combMode && (
                <>
                  <label title="Brush radius (m)">
                    Rad
                    <input
                      type="text"
                      inputMode="decimal"
                      value={brushRadiusText}
                      onChange={(e) => {
                        const raw = e.target.value;
                        setBrushRadiusText(raw);
                        const v = Number(raw);
                        if (raw.trim() !== "" && Number.isFinite(v) && v >= 0.005 && v <= 2.0) {
                          setBrushRadius(v);
                        }
                      }}
                      onBlur={() => {
                        const v = Number(brushRadiusText);
                        const clamped = Number.isFinite(v)
                          ? Math.min(2.0, Math.max(0.005, v))
                          : brushRadius;
                        setBrushRadius(clamped);
                        setBrushRadiusText(String(Number(clamped.toFixed(3))));
                      }}
                      style={{ width: "50px" }}
                    />
                  </label>
                  <label title="Brush strength">
                    Str
                    <input
                      type="text"
                      inputMode="decimal"
                      value={brushStrengthText}
                      onChange={(e) => {
                        const raw = e.target.value;
                        setBrushStrengthText(raw);
                        const v = Number(raw);
                        if (raw.trim() !== "" && Number.isFinite(v) && v >= 0.1 && v <= 5.0) {
                          setBrushStrength(v);
                        }
                      }}
                      onBlur={() => {
                        const v = Number(brushStrengthText);
                        const clamped = Number.isFinite(v)
                          ? Math.min(5.0, Math.max(0.1, v))
                          : brushStrength;
                        setBrushStrength(clamped);
                        setBrushStrengthText(String(Number(clamped.toFixed(2))));
                      }}
                      style={{ width: "44px" }}
                    />
                  </label>
                </>
              )}
              <button
                type="button"
                disabled={!isTauri()}
                title="Reset interactive hair pose back to simulated rest state"
                onClick={async () => {
                  if (!isTauri()) return;
                  try {
                    await invoke("usd_request", { request: { operation: "reset_hair" } });
                  } catch {
                    // ignore
                  }
                  setRevision((r) => r + 1);
                }}
              >
                Reset hair
              </button>
            </div>
          )}
          <div style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
            <button
              type="button"
              disabled={!path || !capabilities?.viewport}
              title="Jump to start frame"
              onClick={() => {
                setPlaying(false);
                setTime(playStart);
              }}
            >
              ⏮
            </button>
            <button
              type="button"
              disabled={!path || !capabilities?.viewport}
              title="Previous frame"
              onClick={() => {
                setPlaying(false);
                setTime((t) => t - 1);
              }}
            >
              ◀
            </button>
            <button
              type="button"
              disabled={!path || !capabilities?.viewport}
              className={playing ? "chosen" : ""}
              title={playing ? "Pause timeline preview" : "Play timeline preview"}
              onClick={() => {
                if (!playing && (time < playStart || time > playEnd)) {
                  setTime(playStart);
                }
                setPlaying((p) => !p);
              }}
            >
              {playing ? "⏸" : "▶"}
            </button>
            <button
              type="button"
              disabled={!path || !capabilities?.viewport}
              title="Next frame"
              onClick={() => {
                setPlaying(false);
                setTime((t) => t + 1);
              }}
            >
              ▶|
            </button>
            <input
              type="range"
              aria-label="Timeline scrubber"
              disabled={!path || !capabilities?.viewport}
              min={playStart}
              max={Math.max(playStart, playEnd)}
              step={1}
              value={Math.max(playStart, Math.min(playEnd, time))}
              onChange={(e) => {
                setPlaying(false);
                setTime(Number(e.target.value));
              }}
              style={{ width: "88px" }}
            />
            <label title="Current USD time code">
              Time
              <NumberInput
                aria-label="USD time code"
                step={1}
                value={time}
                onChange={(e) => {
                  const value = Number(e.target.value);
                  if (Number.isFinite(value)) {
                    setPlaying(false);
                    setTime(value);
                  }
                }}
              />
            </label>
            <NumberInput
              aria-label="Playback start frame"
              title="Playback start frame"
              step={1}
              value={playStart}
              onChange={(e) => {
                const v = Math.round(Number(e.target.value));
                if (Number.isFinite(v)) {
                  setPlayStart(v);
                  if (v > playEnd) setPlayEnd(v);
                }
              }}
              style={{ width: "44px" }}
            />
            <span style={{ opacity: 0.5, fontSize: "11px" }}>–</span>
            <NumberInput
              aria-label="Playback end frame"
              title="Playback end frame"
              step={1}
              value={playEnd}
              onChange={(e) => {
                const v = Math.round(Number(e.target.value));
                if (Number.isFinite(v) && v >= playStart) setPlayEnd(v);
              }}
              style={{ width: "44px" }}
            />
            <button
              type="button"
              className={playLoop ? "chosen" : ""}
              title={playLoop ? "Loop playback enabled" : "Loop playback disabled"}
              onClick={() => setPlayLoop((l) => !l)}
            >
              Loop
            </button>
          </div>
          <select
            aria-label="Renderer"
            disabled={!capabilities?.viewport}
            value={renderer}
            onChange={(e) => {
              const next = e.target.value;
              setRenderer(next);
              setReloadTrigger((r) => r + 1);
              onViewportSettingsChange?.({ renderer: next });
            }}
          >
            <option value="" disabled>
              {capabilities?.viewport
                ? "Select renderer"
                : "Imaging unavailable"}
            </option>
            {capabilities?.renderers.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
          {renderer === "OmniverseRTX" && (
            <select
              aria-label="NVIDIA render mode"
              value={ovrtxMode}
              onChange={(event) => {
                const next = event.target.value as OvrtxMode;
                setOvrtxMode(next);
                setReloadTrigger((r) => r + 1);
                onViewportSettingsChange?.({ ovrtxMode: next });
              }}
            >
              <option value="realtime">Real-Time Path Tracing</option>
              <option value="pathTracing">Path Tracing</option>
            </select>
          )}
          <button
            disabled={!isTauri()}
            title="Re-probe OpenUSD/GPU capabilities, refresh available renderers, and reconnect if a render worker stopped"
            onClick={() => {
              setRetry((r) => r + 1);
              setRevision((r) => r + 1);
            }}
          >
            Refresh renderers
          </button>
        </div>
        <div className="render-options">
          <select
            aria-label="View up axis"
            value={upAxis}
            onChange={(e) => {
              const next = e.target.value as "Y" | "Z";
              setUpAxis(next);
              onViewportSettingsChange?.({ upAxis: next });
            }}
            title="Viewport display Up axis (does not modify source USD)"
          >
            <option value="Y">Up: Y</option>
            <option value="Z">Up: Z</option>
          </select>
        </div>
      </div>
      <div className="viewport-caption">
        <span>
          {sceneMode
            ? "Drag to orbit · Shift/right-drag to pan · Right-click for menu · Double-click for Graph · Ctrl+right-drag or scroll to dolly · Ctrl-click to pick"
            : "Graph mode · Double-click canvas for Scene · Ctrl-click to pick"}
        </span>
        {showBusyStatus ? (
          <span className="viewport-status-active">
            ● {activeStatus || "Preparing scene…"}
          </span>
        ) : null}
        {frame ? (
          <span>
            {frame.updateKind ? `${frame.renderer === "OmniverseRTX" ? "RTX" : frame.renderer} (${frame.updateKind}) · ` : ""}
            {frame.width}×{frame.height} · {frame.upAxis} up ·{" "}
            {frame.renderMs.toFixed(0)} ms render · {frame.captureMs.toFixed(0)}{" "}
            ms capture · {frame.roundTripMs.toFixed(0)} ms round trip
            {frame.raySamples?` · ${frame.raySamples.current}/${frame.raySamples.target} samples`:""}{!frame.converged ? " · refining" : ""}
            {frame.materialMode ? ` · ${frame.materialMode} shading` : ""}
            {frame.interactive ? " · interactive preview" : ""}
            {frame.graphStats
              ? ` · ${frame.graphStats.uniqueSourceAssets} source asset(s) · ${frame.graphStats.prototypes} USD prototype(s)`
              : ""}
          </span>
        ) : null}
        {!!frame?.selectionInfo?.materialCount && <span role="status">{frame.selectionInfo.meshCount ? `${frame.selectionInfo.meshCount} mesh${frame.selectionInfo.meshCount === 1 ? "" : "es"} highlighted for the selected material` : "No meshes use this material"}</span>}
        {frame?.warnings?.map((warning) => (
          <span className="viewport-warning" role="status" key={warning}>
            {warning}
          </span>
        ))}
        {!!frame?.missingAssets?.length && (
          <details className="asset-warnings">
            <summary>Missing assets ({frame.missingAssets.length})</summary>
            <ul>
              {frame.missingAssets.map((asset) => (
                <li key={asset.path}>{asset.path}</li>
              ))}
            </ul>
            <p>
              Restore the referenced files, then Reload. No paths were changed.
            </p>
          </details>
        )}
        {error && (
          <span className="viewport-error" role="alert">
            {error}
          </span>
        )}
      </div>
    </>
  );
}
