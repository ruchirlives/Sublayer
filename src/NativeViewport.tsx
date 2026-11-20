import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent, type ReactNode } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { LatestRenderQueue, previewSize } from "./renderQueue";
import { NumberInput } from "./NumberInput";
import type { CacheEditDelta, CacheEditMask, HairGroomDelta, Transform, ViewportCamera, ViewportSettings } from "./fileRegistry";
import { TransformGizmo } from "./TransformGizmo";

type Delegate = { id: string; name: string; support: string };
type OvrtxMode = "realtime" | "pathTracing";
type CameraAnchor = { session: string; fit: number; center: [number, number, number]; radius: number; unitScale?: number; translation?: [number, number, number]; transformTarget?: string | null };
type PickResult = { prim: string; material?: string | null; point: Frame["pickedPoint"] };
type Capabilities = {
  version: string;
  viewport: boolean;
  renderers: Delegate[];
  gpu?: string;
  viewportError?: string;
};
type Frame = {
  image: string;
  imageBitmap?: ImageBitmap;
  viewportImageId?: string;
  reusedViewportImage?: boolean;
  width: number;
  height: number;
  renderer: string;
  gpu: string;
  converged: boolean;
  cameraAnchor?: CameraAnchor;
  raySamples?:{current:number;target:number};
  frame: number;
  renderMs: number;
  hairSimMs?: number | null;
  hairUploadMs?: number | null;
  groomEncodeMs?: number | null;
  hairSolveStats?: {
    totalStrands: number;
    guideStrands: number;
    fullSolveStrands: number;
    guideSolveGpuMs?: number;
    followerGpuMs?: number;
  } | null;
  captureMs: number;
  totalMs: number;
  roundTripMs: number;
  pngBytes: number;
  interactive?: boolean;
  pickedPrim?: string | null;
  pickedMaterial?: string | null;
  pickedPoint?: [number, number, number] | null;
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
  updateKind?: "cache-hit" | "material-delta" | "material-delta-fast" | "full-rebuild" | "hair-delta" | "hair-brush" | "groom-commit";
  sceneMs?: number;
  stepMs?: number;
  hairGroomDeltas?: Record<string, HairGroomDelta>;
  hairGroomNodeId?: string;
  hairGroomGraphHandle?: string | null;
  hairPinnedCount?: number;
  cacheEditNodeId?: string;
  cacheEditLayerId?: string;
  cacheEditDeltas?: Record<string, CacheEditDelta>;
  cacheEditMasks?: Record<string, CacheEditMask>;
  cacheSimMs?: number;
  cameraSync?: { panX: number; panY: number; zoom: number } | null;
  sentCamera?: { panX: number; panY: number; zoom: number; frameFit: number; orbitPivotSeq?: number };
  presentedCamera?: ViewportCamera;
  pickId?: number;
  nativePopupOpen?: boolean;
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
  selectedNodeId,
  cameraKey,
  comparison,
  viewportSettings,
  transformGizmo,
  onRuntime,
  onMode,
  onSelectPrim,
  onAddToMaterialX,
  onViewportSettingsChange,
  onViewportError,
  onCommitHairGroom,
  onCommitCacheEdit,
  onTimeChange,
  seekFrameRequest,
}: {
  path: string;
  graph?: string;
  emptyTitle?: string;
  emptyMessage?: string;
  previewControls?: ReactNode;
  graphModeRequest?: number;
  selectedPrim?:string;
  selectedNodeId?: string | null;
  cameraKey?:string;
  comparison?:string;
  viewportSettings?: ViewportSettings;
  transformGizmo?: {
    nodeId: string;
    transform: Transform;
    onChange: (next: Transform) => void;
    onCommit?: () => void;
  } | null;
  onRuntime: (text: string) => void;
  onMode: (scene: boolean) => void;
  onSelectPrim?: (path: string) => void;
  onAddToMaterialX?: (primPath: string) => void;
  onViewportSettingsChange?: (patch: Partial<ViewportSettings>) => void;
  onViewportError?: (error: string) => void;
  onCommitHairGroom?: (nodeId: string, groomDeltas: Record<string, HairGroomDelta>) => void;
  onCommitCacheEdit?: (
    nodeId: string,
    layerId: string,
    deltas: Record<string, CacheEditDelta>,
    masks: Record<string, CacheEditMask>,
  ) => void;
  onTimeChange?: (frame: number) => void;
  seekFrameRequest?: { frame: number; seq: number } | null;
}) {
  const host = useRef<HTMLDivElement>(null);
  const onCommitHairGroomRef = useRef(onCommitHairGroom);
  onCommitHairGroomRef.current = onCommitHairGroom;
  const onCommitCacheEditRef = useRef(onCommitCacheEdit);
  onCommitCacheEditRef.current = onCommitCacheEdit;
  const graphTargetNodeIdRef = useRef<string | null>(null);
  try {
    graphTargetNodeIdRef.current = graph ? (JSON.parse(graph) as { targetNodeId?: string }).targetNodeId ?? null : null;
  } catch {
    graphTargetNodeIdRef.current = null;
  }
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [renderer, setRenderer] = useState(viewportSettings?.renderer || "");
  const [ovrtxMode, setOvrtxMode] = useState<OvrtxMode>(viewportSettings?.ovrtxMode || "pathTracing");
  const [camera, setCamera] = useState<ViewportCamera>(viewportSettings?.camera ?? home);
  const [cameraSession] = useState(() => crypto.randomUUID());
  const [frameFit, setFrameFit] = useState(0);
  const [frameTarget, setFrameTarget] = useState<string | undefined>(undefined);
  const [orbitPivot, setOrbitPivot] = useState<{ target: string; point?: [number, number, number] | null; seq: number } | null>(null);
  const orbitPivotSeqRef = useRef(0);
  const nextPickId = useRef(0);
  const pendingPick = useRef<{
    id: number;
    context: string;
    coords: [number, number];
    resolve: (result: PickResult | null) => void;
  } | null>(null);
  const [isolatedPrim, setIsolatedPrim] = useState<string | null>(null);
  const [hiddenPrims, setHiddenPrims] = useState<string[]>([]);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; prim: string | null; material?: string | null; point?: [number, number, number] | null; picking: boolean } | null>(null);
  const [upAxis, setUpAxis] = useState<"Y" | "Z">(viewportSettings?.upAxis || "Y");
  const [purpose, setPurpose] = useState<"render" | "proxy" | "both">("render");
  const [complexity, setComplexity] = useState<"low" | "medium" | "high">("medium");
  const [drawMode, setDrawMode] = useState<"shaded" | "solid" | "wireframe" | "points" | "bounds">("shaded");
  const [size, setSize] = useState({ width: 960, height: 640 });
  const [frame, setFrame] = useState<Frame | null>(null);
  const [lastHairFrame, setLastHairFrame] = useState<
    (Pick<Frame, "hairSimMs" | "hairUploadMs" | "groomEncodeMs" | "hairSolveStats"> & { groomCommitMs?: number }) | null
  >(null);
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
  const [simulating, setSimulating] = useState(false);
  const [simSession, setSimSession] = useState<{ id: number; originFrame: number } | null>(null);
  const [playStart, setPlayStart] = useState(1);
  const [playEnd, setPlayEnd] = useState(48);
  const hasCacheEditInGraph = useMemo(() => {
    if (!graph) return false;
    return graph.includes('"operation":"cache_edit"');
  }, [graph]);
  const [playLoop, setPlayLoop] = useState(true);
  useEffect(() => {
    onTimeChange?.(time);
  }, [time, onTimeChange]);
  useEffect(() => {
    if (seekFrameRequest && Number.isFinite(seekFrameRequest.frame)) {
      setTime(seekFrameRequest.frame);
    }
  }, [seekFrameRequest]);
  const [combMode, setCombMode] = useState(false);
  const [cacheSculptMode, setCacheSculptMode] = useState(false);
  const [cacheBrushTool, setCacheBrushTool] = useState<
    "clothDrag" | "clothRelax" | "clothInflate" | "grab" | "smooth" | "inflate" | "pinch" | "flatten" | "mask" | "unmask"
  >("clothDrag");
  const [cacheBrushRadius, setCacheBrushRadius] = useState(0.08);
  const [cacheBrushRadiusText, setCacheBrushRadiusText] = useState("0.08");
  const [cacheBrushStrength, setCacheBrushStrength] = useState(0.75);
  const [cacheBrushStrengthText, setCacheBrushStrengthText] = useState("0.75");
  const [cacheBrushInvert, setCacheBrushInvert] = useState(false);
  const [showCacheMask, setShowCacheMask] = useState(false);
  const [cacheBrush, setCacheBrush] = useState<{
    nx: number;
    ny: number;
    prevNx: number;
    prevNy: number;
    radius: number;
    screenRadiusFrac?: number;
    strength: number;
    tool: string;
    invert?: boolean;
    newStroke?: boolean;
    commitUsd?: boolean;
    commitOnly?: boolean;
    undoStroke?: boolean;
    clearMask?: boolean;
    invertMask?: boolean;
    strokeId?: number;
    seq: number;
  } | null>(null);
  const cacheDragRef = useRef<{ nx: number; ny: number; strokeId: number } | null>(null);
  const lastCacheCursor = useRef<{ strokeId: number; nx: number; ny: number } | null>(null);
  const cacheStrokeIdRef = useRef(0);
  const cacheSeqRef = useRef(0);
  const lastDispatchedCacheSeq = useRef(0);
  const [hairPopupOpen, setHairPopupOpen] = useState(false);
  const [popupCamera, setPopupCamera] = useState<ViewportCamera>(home);
  const [popupSize, setPopupSize] = useState<{ width: number; height: number }>({ width: 1180, height: 722 });
  const popupNavRef = useRef<{ mode: "orbit" | "pan" | "dolly"; x: number; y: number; cam: ViewportCamera } | null>(null);
  const popupBrushRef = useRef<{ nx: number; ny: number } | null>(null);
  const popupLineRef = useRef<{ nx: number; ny: number } | null>(null);
  const popupResizeTimerRef = useRef<number | null>(null);
  const [popupHoverSeq, setPopupHoverSeq] = useState(0);
  const popupHoverPendingRef = useRef(false);
  const [livePhysics, setLivePhysics] = useState(false);
  const [nativePopup, setNativePopup] = useState(false);
  const rgbaCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const [brushTool, setBrushTool] = useState<"comb" | "surfaceComb" | "grab" | "smooth" | "physics" | "clump" | "part" | "puff" | "lengthen" | "shorten" | "cut" | "add" | "delete" | "pin" | "unpin">("comb");
  const [brushCursor, setBrushCursor] = useState<{ x: number; y: number } | null>(null);
  const [brushRadius, setBrushRadius] = useState(0.06);
  const [brushRadiusText, setBrushRadiusText] = useState("0.06");
  const [brushStrength, setBrushStrength] = useState(1.0);
  const [brushStrengthText, setBrushStrengthText] = useState("1.0");
  const [brushVertexPct, setBrushVertexPct] = useState<100 | 50 | 25>(100);
  const [hairBrush, setHairBrushState] = useState<{
    nx: number;
    ny: number;
    prevNx: number;
    prevNy: number;
    radius: number;
    screenRadiusFrac?: number;
    strength: number;
    vertexPct?: 100 | 50 | 25;
    tool?: "comb" | "surfaceComb" | "grab" | "smooth" | "physics" | "clump" | "part" | "puff" | "lengthen" | "shorten" | "cut" | "add" | "delete" | "pin" | "unpin";
    cutLine?: [number, number, number, number];
    lengthenDrag?: [number, number, number, number];
    undoStroke?: boolean;
    clearPins?: boolean;
    newStroke?: boolean;
    strokeId?: number;
    livePhysics?: boolean;
    settleOnly?: boolean;
    commitUsd?: boolean;
    seq: number;
  } | null>(null);
  const latestHairBrush = useRef<typeof hairBrush>(null);
  function setHairBrush(brush: typeof hairBrush) {
    latestHairBrush.current = brush;
    setHairBrushState(brush);
  }
  const [brushingActive, setBrushingActive] = useState(false);
  const [showPins, setShowPins] = useState(false);
  const [pinnedCount, setPinnedCount] = useState(0);
  const [cutLine, setCutLine] = useState<{
    x1: number;
    y1: number;
    x2: number;
    y2: number;
    nx1: number;
    ny1: number;
    nx2: number;
    ny2: number;
  } | null>(null);
  const cutLineRef = useRef<typeof cutLine>(null);
  const [lengthenDrag, setLengthenDrag] = useState<typeof cutLine>(null);
  const lengthenDragRef = useRef<typeof cutLine>(null);
  const brushSeqRef = useRef(0);
  const lastDispatchedHairSeq = useRef(0);
  const lastCombCursor = useRef<{ strokeId: number; nx: number; ny: number } | null>(null);
  const brushStrokeIdRef = useRef(0);
  const brushNewStrokeRef = useRef(false);
  const brushDragRef = useRef<{ nx: number; ny: number } | null>(null);
  const lastTopoBrushMsRef = useRef(0);
  const hairGroomDirtyRef = useRef(false);
  const materials = "auto";
  const [interacting, setInteracting] = useState(false);
  const [gizmoDragging, setGizmoDragging] = useState(false);
  const gizmoDraggingRef = useRef(false);
  const onGizmoInteractingChange = useCallback((active: boolean) => {
    gizmoDraggingRef.current = active;
    setGizmoDragging(active);
    setInteracting(active);
    if (active) setActiveStatus(null);
  }, []);

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
      setSimulating(false);
    }
  }, [error, path, capabilities?.viewport]);

  useEffect(() => {
    if (!brushingActive || busy || brushTool === "comb" || brushTool === "surfaceComb") return;
    const delayMs = brushTool === "add" || brushTool === "delete" ? 60 : 16;
    const timer = window.setTimeout(() => {
      const cur = brushDragRef.current;
      if (!cur) return;
      if (brushTool === "add" || brushTool === "delete") {
        lastTopoBrushMsRef.current = performance.now();
      }
      const isNewStroke = brushNewStrokeRef.current;
      brushNewStrokeRef.current = false;
      if (brushTool !== "pin" && brushTool !== "unpin") {
        hairGroomDirtyRef.current = true;
      }
      setHairBrush({
        nx: cur.nx,
        ny: cur.ny,
        prevNx: cur.nx,
        prevNy: cur.ny,
        radius: brushRadius,
        strength: brushStrength,
        tool: brushTool,
        newStroke: isNewStroke,
        strokeId: brushStrokeIdRef.current,
        seq: ++brushSeqRef.current,
      });
    }, delayMs);
    return () => window.clearTimeout(timer);
  }, [brushingActive, busy, hairBrush, brushRadius, brushStrength, brushTool]);

  useEffect(() => {
    if (!playing || busy || error || !path || !capabilities?.viewport) return;
    const delayMs = renderer === "OmniverseRTX" ? 16 : 16;
    const timer = window.setTimeout(() => {
      setTime((cur) => {
        if (cur < playStart) return playStart;
        if (cur >= playEnd) {
          if (!playLoop) {
            setPlaying(false);
      setSimulating(false);
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
    if (!simulating || busy || error || !path || !capabilities?.viewport) return;
    const delayMs = renderer === "OmniverseRTX" ? 30 : 20;
    const timer = window.setTimeout(() => {
      setTime((cur) => {
        if (cur >= playEnd) {
          setSimulating(false);
          return playEnd;
        }
        return cur + 1;
      });
    }, delayMs);
    return () => window.clearTimeout(timer);
  }, [simulating, busy, error, path, capabilities?.viewport, renderer, playEnd, time]);

  useEffect(() => {
    const registeredGraphs=new Set<string>();
    const activeObjectUrl = { current: "" };
    let presentedImage: { id: string; url: string; bitmap?: ImageBitmap } | null = null;
    let nextImageId = 0;
    let committedRenderGraph: { graph: string; handle: string } | null = null;
    const textDecoder = new TextDecoder();
    const revokePreviousUrl = (nextUrl = "") => {
      if (activeObjectUrl.current && activeObjectUrl.current !== nextUrl) {
        URL.revokeObjectURL(activeObjectUrl.current);
      }
      activeObjectUrl.current = nextUrl;
    };
    const scheduler = new LatestRenderQueue<Record<string, unknown>, Frame>({
      render: async (request) => {
        const {graph,...cameraRequest}=request;
        const graphKey=typeof request.graphKey==="string" ? request.graphKey : "";
        const registrationKey=String(request.renderer)+":"+graphKey;
        const commitHandle = request.renderer === "OmniverseRTX" && committedRenderGraph && committedRenderGraph.graph === graph
          ? committedRenderGraph.handle : undefined;
        const anchor = cameraAnchor.current;
        const retainedAnchor = request.renderer === "OmniverseRTX" &&
          anchor?.session === request.cameraSession && anchor?.fit === request.frameFit ? anchor : undefined;
        const isExplicitReload = typeof request.reloadTrigger === "number" && request.reloadTrigger > 0 && request.reloadTrigger !== previousReload.current;
        if (typeof request.reloadTrigger === "number") previousReload.current = request.reloadTrigger;
        let hb = cameraRequest.hairBrush as (NonNullable<typeof hairBrush> & { nodeId?: string }) | undefined;
        if (hb && hb.seq <= lastDispatchedHairSeq.current) {
          delete cameraRequest.hairBrush;
          hb = undefined;
        }
        if (hb) lastDispatchedHairSeq.current = hb.seq;
        let cb = cameraRequest.cacheBrush as (NonNullable<typeof cacheBrush> & { nodeId?: string }) | undefined;
        if (cb && cb.seq <= lastDispatchedCacheSeq.current) {
          delete cameraRequest.cacheBrush;
          cb = undefined;
        }
        if (cb) {
          lastDispatchedCacheSeq.current = cb.seq;
          if (cb.strokeId !== undefined && !cb.commitOnly && !cb.undoStroke) {
            const sid = cb.strokeId;
            const prev = lastCacheCursor.current;
            if (prev?.strokeId === sid) {
              cb = { ...cb, prevNx: prev.nx, prevNy: prev.ny };
              cameraRequest.cacheBrush = cb;
            }
            lastCacheCursor.current = { strokeId: sid, nx: cb.nx, ny: cb.ny };
          }
        }
        if (hb?.strokeId !== undefined && (hb.tool === "comb" || hb.tool === "surfaceComb")) {
          const strokeId = hb.strokeId;
          const previous = lastCombCursor.current;
          if (previous?.strokeId === strokeId) {
            hb = { ...hb, prevNx: previous.nx, prevNy: previous.ny };
            cameraRequest.hairBrush = hb;
          }
          lastCombCursor.current = { strokeId, nx: hb.nx, ny: hb.ny };
        }
        if (hb?.strokeId === brushStrokeIdRef.current &&
          hb?.nodeId === activeHairNodeIdRef.current && brushDragRef.current) {
          brushDragRef.current = { nx: hb.nx, ny: hb.ny };
        }
        const pick = !hb && pendingPick.current?.context === latestContextKeyRef.current ? pendingPick.current : null;
        if (request.renderer === "OmniverseRTX") {
          cameraRequest.viewportImageId = `${cameraSession}:${++nextImageId}`;
          cameraRequest.presentedViewportImageId = presentedImage?.id;
        }
        const render=async(register:boolean, handle?: string)=>{
          const buf = await invoke<ArrayBuffer>("render_viewport_frame",{request:{...cameraRequest,pick:pick?.coords,cameraAnchor:retainedAnchor,...(handle?{graphKey:handle}:{}),...(register?{graph}:{}),reload:isExplicitReload}});
          const view = new DataView(buf);
          const metaLen = view.getUint32(0, true);
          const meta = JSON.parse(textDecoder.decode(new Uint8Array(buf, 4, metaLen))) as Frame;
          if (!meta.image && buf.byteLength > 4 + metaLen) {
            if (meta.encoding === "rgba" && buf.byteLength >= 4 + metaLen + meta.width * meta.height * 4) {
              const rgba = new Uint8ClampedArray(buf, 4 + metaLen, meta.width * meta.height * 4);
              meta.imageBitmap = await createImageBitmap(new ImageData(rgba, meta.width, meta.height));
              meta.image = "";
            } else {
              const mime = meta.encoding === "jpeg" ? "image/jpeg" : "image/png";
              const blob = new Blob([new Uint8Array(buf, 4 + metaLen)], { type: mime });
              meta.image = URL.createObjectURL(blob);
            }
          }
          return meta;
        };
        let result:Frame;
        let usedCommitHandle = Boolean(commitHandle);
        try{result=await render(!commitHandle && !!graph && (!graphKey || !registeredGraphs.has(registrationKey)), commitHandle);}
        catch(error){
          if(!graph || !String(error).includes("Unknown render graph handle"))throw error;
          if (usedCommitHandle) committedRenderGraph = null;
          usedCommitHandle = false;
          result=await render(true);
        }
        if(graphKey && !usedCommitHandle){if(registeredGraphs.size>=32)registeredGraphs.clear();registeredGraphs.add(registrationKey);}
        // Groom data belongs to the dispatched edit even if the image is stale.
        if (hb?.commitUsd && queue.current === scheduler) {
          if (hb.seq >= brushSeqRef.current) {
            hairGroomDirtyRef.current = false;
          }
          setActiveStatus(null);
          if (hb.nodeId && result.hairGroomNodeId === hb.nodeId && result.hairGroomDeltas) {
            const groomEncodeMs = result.groomEncodeMs;
            if (typeof groomEncodeMs === "number") {
              setLastHairFrame((previous) => ({ ...previous, groomEncodeMs, groomCommitMs: result.roundTripMs }));
            }
            if (typeof graph === "string") {
              const committedGraph = JSON.parse(graph);
              const hairNode = committedGraph.nodes?.find(
                (node: { id: string; operation: string }) => node.id === hb.nodeId && node.operation === "hair",
              );
              if (hairNode?.hair) {
                if (Object.keys(result.hairGroomDeltas).length) {
                  hairNode.hair.groomDeltas = result.hairGroomDeltas;
                } else {
                  delete hairNode.hair.groomDeltas;
                }
                const updatedGraph = JSON.stringify(committedGraph);
                const updatedGraphKey = hashContent(updatedGraph);
                if (result.hairGroomGraphHandle) {
                  committedRenderGraph = { graph: updatedGraph, handle: result.hairGroomGraphHandle };
                }
                // A second stroke can already be queued before React publishes
                // this commit. Dispatch it against the geometry just saved.
                scheduler.updateQueued((queued) => queued.graph === graph &&
                  queued.renderer === request.renderer
                  ? { ...queued, graph: updatedGraph, graphKey: updatedGraphKey }
                  : queued);
                const previous = previousViewportRequest.current;
                if (previous?.request.graph === graph && previous.request.renderer === request.renderer) {
                  previousViewportRequest.current = {
                    ...previous,
                    request: { ...previous.request, graph: updatedGraph, graphKey: updatedGraphKey },
                  };
                }
              }
            }
            onCommitHairGroomRef.current?.(hb.nodeId, result.hairGroomDeltas);
          }
        }
        if (
          result.cacheEditNodeId &&
          result.cacheEditLayerId &&
          result.cacheEditDeltas !== undefined
        ) {
          onCommitCacheEditRef.current?.(
            result.cacheEditNodeId,
            result.cacheEditLayerId,
            result.cacheEditDeltas,
            result.cacheEditMasks || {},
          );
        }
        // Keep the old image visible until its replacement is decoded, and apply
        // backpressure to decoding as well as rendering/transport.
        if (result.encoding === "popup") {
          result.image = presentedImage?.url ?? "";
          result.imageBitmap = presentedImage?.bitmap;
          if (request.nativePopup && result.nativePopupOpen === false) {
            setNativePopup(false);
          }
        } else if (result.reusedViewportImage && presentedImage && result.viewportImageId === presentedImage.id) {
          result.image = presentedImage.url;
          result.imageBitmap = presentedImage.bitmap;
        } else if (result.imageBitmap) {
          // Raw RGBA ImageBitmap is already decoded on the worker/compositor.
        } else {
          if (!result.image) throw new Error("Renderer returned a frame without image data");
          const image = new Image();
          image.src = result.image;
          try {
            await image.decode();
          } catch (decodeError) {
            if (result.image.startsWith("blob:")) URL.revokeObjectURL(result.image);
            throw decodeError;
          }
        }
        result.sentCamera = {
          panX: Number(request.panX ?? 0), panY: Number(request.panY ?? 0), zoom: Number(request.zoom ?? 1),
          frameFit: Number(request.frameFit ?? 0),
          orbitPivotSeq: typeof request.orbitPivotSeq === "number" ? request.orbitPivotSeq : undefined,
        };
        result.presentedCamera = {
          yaw: Number(request.yaw), pitch: Number(request.pitch),
          fov: typeof request.fov === "number" ? request.fov : undefined,
          panX: result.cameraSync?.panX ?? result.sentCamera.panX,
          panY: result.cameraSync?.panY ?? result.sentCamera.panY,
          zoom: result.cameraSync?.zoom ?? result.sentCamera.zoom,
        };
        result.pickId = pick?.id;
        return result;
      },
      present: (result) => {
        if (presentedImage?.bitmap && presentedImage.bitmap !== result.imageBitmap) {
          presentedImage.bitmap.close();
        }
        presentedImage = result.viewportImageId ? { id: result.viewportImageId, url: result.image, bitmap: result.imageBitmap } : null;
        if (result.image && result.image.startsWith("blob:")) {
          revokePreviousUrl(result.image);
        } else {
          revokePreviousUrl("");
        }
        if (result.cameraAnchor) cameraAnchor.current = result.cameraAnchor;
        if (result.cameraSync && result.sentCamera) {
          const sync = result.cameraSync;
          const sent = result.sentCamera;
          const rebase = (c: typeof sync) => ({
            panX: c.panX + (sync.panX - sent.panX),
            panY: c.panY + (sync.panY - sent.panY),
            zoom: c.zoom * (sync.zoom / sent.zoom),
          });
          scheduler.updatePending((pending) => pending.frameFit === sent.frameFit
            ? { ...pending, ...rebase({ panX: Number(pending.panX), panY: Number(pending.panY), zoom: Number(pending.zoom) }) }
            : pending);
          if (drag.current) drag.current.camera = { ...drag.current.camera, ...rebase(drag.current.camera) };
          setCamera((c) => ({ ...c, ...rebase(c) }));
          setOrbitPivot((pivot) => pivot?.seq === sent.orbitPivotSeq ? null : pivot);
        }
        if (typeof result.hairPinnedCount === "number") {
          setPinnedCount(result.hairPinnedCount);
        }
        const canvas = rgbaCanvasRef.current;
        if (canvas && result.imageBitmap) {
          if (canvas.width !== result.width) canvas.width = result.width;
          if (canvas.height !== result.height) canvas.height = result.height;
          const ctx = canvas.getContext("2d");
          if (ctx) ctx.drawImage(result.imageBitmap, 0, 0);
        }
        setFrame(result);
        if (result.hairSolveStats?.totalStrands) {
          setLastHairFrame((previous) => ({
            ...previous,
            hairSimMs: result.hairSimMs,
            hairUploadMs: result.hairUploadMs,
            hairSolveStats: result.hairSolveStats,
          }));
        }
        const pick = pendingPick.current;
        if (pick && pick.id === result.pickId) {
          pendingPick.current = null;
          pick.resolve(result.pickedPrim ? { prim: result.pickedPrim, material: result.pickedMaterial, point: result.pickedPoint } : null);
        }
        setPresentedContext(latestContextKeyRef.current);
        setActiveStatus(null);
        setError("");
      },
      discard: (result) => {
        if (result.image.startsWith("blob:") && result.image !== activeObjectUrl.current) {
          URL.revokeObjectURL(result.image);
        }
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
          pendingPick.current?.resolve(null);
          pendingPick.current = null;
          setActiveStatus(null);
        }
      },
      schedule: (callback) => window.requestAnimationFrame(callback),
      cancel: (id) => window.cancelAnimationFrame(id),
    });
    queue.current = scheduler;
    return () => {
      scheduler.dispose();
      pendingPick.current?.resolve(null);
      pendingPick.current = null;
      revokePreviousUrl("");
      if (queue.current === scheduler) queue.current = null;
      if (wheelEnd.current !== null) clearTimeout(wheelEnd.current);
    };
  }, []);

  useEffect(() => {
    if (!isTauri()) return;
    let unlisten: (() => void) | undefined;
    listen<{ phase?: string; details?: string }>("renderer-status", (event) => {
      if (busyRef.current && !gizmoDraggingRef.current && event.payload?.phase) {
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
  const contextGraphKey = useMemo(() => {
    if (!graph) return undefined;
    try {
      const parsed = JSON.parse(graph);
      if (Array.isArray(parsed?.nodes)) {
        parsed.nodes = parsed.nodes.map((n: Record<string, unknown>) => {
          if (n?.operation === "transform") return { ...n, transform: null };
          if (n?.operation === "cache_edit" && n.cacheEdit && typeof n.cacheEdit === "object") {
            const ce = n.cacheEdit as Record<string, unknown>;
            return {
              ...n,
              cacheEdit: {
                targetQuery: ce.targetQuery ?? "",
                simEnabled: Boolean(ce.simEnabled),
                simCacheVersion: ce.simCacheVersion ?? 0,
                layers: ce.layers,
                temporalSmoothFrames: ce.temporalSmoothFrames ?? 0,
                temporalSmoothStrength: ce.temporalSmoothStrength ?? 0,
                maskWeights: ce.maskWeights,
              },
            };
          }
          return n;
        });
      }
      return hashContent(JSON.stringify(parsed));
    } catch {
      return graphKey;
    }
  }, [graph, graphKey]);
  const gizmoNodeId = useMemo(() => {
    if (!graph || !transformGizmo?.nodeId) return undefined;
    try {
      const spec = JSON.parse(graph);
      return spec.nodes?.some((node: { id: string; operation: string }) =>
        node.id === transformGizmo.nodeId && node.operation === "transform",
      ) ? transformGizmo.nodeId : undefined;
    } catch {
      return undefined;
    }
  }, [graph, transformGizmo?.nodeId]);
  const activeHairNodeId = useMemo(() => {
    if (!graph) return null;
    try {
      const g = JSON.parse(graph) as {
        targetNodeId?: string;
        nodes?: Array<{ id?: string; operation?: string; hair?: { simEnabled?: boolean } }>;
      };
      const candidateId = selectedNodeId || g?.targetNodeId;
      const cand = candidateId ? g?.nodes?.find((n) => n.id === candidateId) : undefined;
      if (cand?.operation === "hair" && cand?.hair?.simEnabled) {
        return cand.id ?? null;
      }
      const target = g?.targetNodeId ? g?.nodes?.find((n) => n.id === g.targetNodeId) : undefined;
      if (target?.operation === "hair" && target?.hair?.simEnabled) {
        return target.id ?? null;
      }
      return null;
    } catch {
      return null;
    }
  }, [graph, selectedNodeId]);
  const hasSimulatedHair = Boolean(activeHairNodeId);
  const activeHairNodeIdRef = useRef<string | null>(null);
  activeHairNodeIdRef.current = activeHairNodeId;
  const activeCacheEditNodeId = useMemo(() => {
    if (!graph) return null;
    try {
      const g = JSON.parse(graph) as {
        targetNodeId?: string;
        nodes?: Array<{ id?: string; operation?: string }>;
      };
      const candidateId = selectedNodeId || g?.targetNodeId;
      const cand = candidateId ? g?.nodes?.find((n) => n.id === candidateId) : undefined;
      if (cand?.operation === "cache_edit") return cand.id ?? null;
      const target = g?.targetNodeId ? g?.nodes?.find((n) => n.id === g.targetNodeId) : undefined;
      if (target?.operation === "cache_edit") return target.id ?? null;
      return null;
    } catch {
      return null;
    }
  }, [graph, selectedNodeId]);
  const hasCacheEdit = Boolean(activeCacheEditNodeId);
  useEffect(() => {
    if (!hasCacheEdit && cacheSculptMode) setCacheSculptMode(false);
  }, [hasCacheEdit, cacheSculptMode]);
  useEffect(() => {
    if (!cacheSculptMode || !hasCacheEdit) return;
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "[" || e.key === "]") {
        setCacheBrushRadius((prev) => {
          const next = Math.min(100, Math.max(0.002, Number((prev * (e.key === "[" ? 0.85 : 1.18)).toFixed(3))));
          setCacheBrushRadiusText(String(next));
          return next;
        });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cacheSculptMode, hasCacheEdit]);
  useEffect(() => {
    if (!hasSimulatedHair && combMode) setCombMode(false);
    if (!hasSimulatedHair && livePhysics) setLivePhysics(false);
  }, [hasSimulatedHair, combMode, livePhysics]);
  const popupStateRef = useRef({
    popupCamera,
    camera,
    brushTool,
    brushRadius,
    brushStrength,
    ovrtxMode,
    popupSize,
    livePhysics,
  });
  popupStateRef.current = {
    popupCamera,
    camera,
    brushTool,
    brushRadius,
    brushStrength,
    ovrtxMode,
    popupSize,
    livePhysics,
  };

  useEffect(() => {
    if (!hairPopupOpen) return;
    let unlisten: (() => void) | null = null;
    listen<{
      type: string;
      action?: string;
      command?: string;
      button?: number;
      x?: number;
      y?: number;
      width?: number;
      height?: number;
      deltaY?: number;
      shiftKey?: boolean;
      altKey?: boolean;
      key?: string;
      vertexPct?: number;
    }>("hair-popup-input", (ev) => {
      const e = ev.payload;
      const st = popupStateRef.current;
      if (e.type === "resize") {
        const rw = Math.max(64, Math.round(e.width || 1180));
        const rh = Math.max(64, Math.round(e.height || 722));
        if (popupResizeTimerRef.current !== null) window.clearTimeout(popupResizeTimerRef.current);
        popupResizeTimerRef.current = window.setTimeout(() => {
          setPopupSize({ width: rw, height: rh });
        }, 100);
        return;
      }
      if (e.type === "toolbar") {
        if (e.action === "closed") {
          if (hairGroomDirtyRef.current) finishHairStroke();
          setHairPopupOpen(false);
          return;
        }
        const cmd = e.command || "";
        if (cmd.startsWith("tool:")) {
          setBrushTool(cmd.slice(5) as typeof brushTool);
        } else if (cmd === "toggle:livePhysics") {
          setLivePhysics((prev) => {
            const next = !prev;
            if (!next && hairGroomDirtyRef.current) finishHairStroke();
            return next;
          });
        } else if (cmd === "toggle:showPins") {
          setShowPins((v) => !v);
        } else if (cmd === "toggle:realtime") {
          onViewportSettingsChange?.({ ovrtxMode: st.ovrtxMode === "realtime" ? "pathTracing" : "realtime" });
        } else if (cmd === "action:undo") {
          commitHairBrush({
            nx: 0.5, ny: 0.5, prevNx: 0.5, prevNy: 0.5,
            radius: st.brushRadius, strength: st.brushStrength,
            tool: st.brushTool, undoStroke: true, seq: ++brushSeqRef.current,
          });
        } else if (cmd === "action:radiusDown" || cmd === "action:radiusUp") {
          setBrushRadius((prev) => {
            const next = Math.min(1000, Math.max(0.001, Number((prev * (cmd === "action:radiusDown" ? 0.85 : 1.18)).toFixed(3))));
            setBrushRadiusText(String(next));
            return next;
          });
        } else if (cmd === "action:strengthDown" || cmd === "action:strengthUp") {
          setBrushStrength((prev) => {
            const next = Math.min(5, Math.max(0.1, Number((prev * (cmd === "action:strengthDown" ? 0.85 : 1.18)).toFixed(2))));
            setBrushStrengthText(String(next));
            return next;
          });
        } else if (cmd === "set:vertexPct") {
          const pct = e.vertexPct === 25 ? 25 : e.vertexPct === 50 ? 50 : 100;
          setBrushVertexPct(pct);
        } else if (cmd === "action:frame") {
          setPopupCamera({ ...st.camera });
        }
        return;
      }
      if (e.type === "keydown") {
        if (e.key === "[" || e.key === "]") {
          setBrushRadius((prev) => {
            const next = Math.min(1000, Math.max(0.001, Number((prev * (e.key === "[" ? 0.85 : 1.18)).toFixed(3))));
            setBrushRadiusText(String(next));
            return next;
          });
        } else if (e.key === "Escape") {
          if (hairGroomDirtyRef.current) finishHairStroke();
          setHairPopupOpen(false);
        }
        return;
      }
      const w = Math.max(1, e.width || st.popupSize.width);
      const h = Math.max(1, e.height || st.popupSize.height);
      const nx = Math.min(1, Math.max(0, (e.x ?? 0) / w));
      const ny = Math.min(1, Math.max(0, (e.y ?? 0) / h));
      if (e.type === "wheel") {
        const factor = (e.deltaY ?? 0) < 0 ? 0.9 : 1.1;
        setPopupCamera((c) => ({ ...c, zoom: Math.min(100, Math.max(0.002, c.zoom * factor)) }));
        return;
      }
      if (e.type === "mousedown") {
        const btn = e.button ?? 0;
        if (btn === 2 || btn === 1 || e.altKey || e.shiftKey) {
          const isDolly = Boolean(e.altKey && btn === 2);
          const mode: "orbit" | "pan" | "dolly" = isDolly ? "dolly" : (btn === 2 || e.shiftKey ? "pan" : "orbit");
          popupNavRef.current = { mode, x: e.x ?? 0, y: e.y ?? 0, cam: st.popupCamera };
          return;
        }
        if (btn === 0) {
          brushStrokeIdRef.current += 1;
          if (st.brushTool === "cut" || st.brushTool === "lengthen") {
            popupLineRef.current = { nx, ny };
          } else {
            popupBrushRef.current = { nx, ny };
            hairGroomDirtyRef.current = true;
            setHairBrush({
              nx, ny, prevNx: nx, prevNy: ny,
              radius: st.brushRadius, strength: st.brushStrength,
              tool: st.brushTool, newStroke: true,
              strokeId: brushStrokeIdRef.current, seq: ++brushSeqRef.current,
            });
          }
        }
        return;
      }
      if (e.type === "mousemove") {
        if (popupNavRef.current) {
          const d = popupNavRef.current;
          const dx = (e.x ?? 0) - d.x;
          const dy = (e.y ?? 0) - d.y;
          if (d.mode === "dolly") {
            setPopupCamera({
              ...d.cam,
              zoom: Math.max(0.002, Math.min(100, d.cam.zoom * Math.exp((dy - dx) * 0.005))),
            });
          } else if (d.mode === "orbit") {
            setPopupCamera({
              ...d.cam,
              yaw: d.cam.yaw - dx * 0.4,
              pitch: Math.max(-89, Math.min(89, d.cam.pitch + dy * 0.4)),
            });
          } else {
            setPopupCamera({
              ...d.cam,
              panX: d.cam.panX - (dx / 250) * d.cam.zoom,
              panY: d.cam.panY + (dy / 250) * d.cam.zoom,
            });
          }
          return;
        }
        if (popupBrushRef.current) {
          const prev = popupBrushRef.current;
          popupBrushRef.current = { nx, ny };
          hairGroomDirtyRef.current = true;
          setHairBrush({
            nx, ny, prevNx: prev.nx, prevNy: prev.ny,
            radius: st.brushRadius, strength: st.brushStrength,
            tool: st.brushTool,
            strokeId: brushStrokeIdRef.current, seq: ++brushSeqRef.current,
          });
        } else if (!st.livePhysics) {
          popupHoverPendingRef.current = true;
          setPopupHoverSeq((v) => v + 1);
        }
        return;
      }
      if (e.type === "mouseup") {
        if (popupNavRef.current) {
          popupNavRef.current = null;
          return;
        }
        if (popupLineRef.current) {
          const start = popupLineRef.current;
          popupLineRef.current = null;
          const payload: NonNullable<typeof hairBrush> = {
            nx, ny, prevNx: start.nx, prevNy: start.ny,
            radius: st.brushRadius, strength: st.brushStrength,
            tool: st.brushTool, newStroke: true,
            strokeId: brushStrokeIdRef.current, seq: ++brushSeqRef.current,
          };
          if (st.brushTool === "cut") payload.cutLine = [start.nx, start.ny, nx, ny];
          if (st.brushTool === "lengthen") payload.lengthenDrag = [start.nx, start.ny, nx, ny];
          hairGroomDirtyRef.current = true;
          setHairBrush(payload);
          return;
        }
        if (popupBrushRef.current) {
          popupBrushRef.current = null;
          setHairBrush(null);
        }
      }
    }).then((fn) => { unlisten = fn; });
    return () => { unlisten?.(); };
  }, [hairPopupOpen]);

  useEffect(() => {
    const canvas = rgbaCanvasRef.current;
    if (!canvas || !frame?.imageBitmap) return;
    if (canvas.width !== frame.width) canvas.width = frame.width;
    if (canvas.height !== frame.height) canvas.height = frame.height;
    const ctx = canvas.getContext("2d");
    if (ctx) ctx.drawImage(frame.imageBitmap, 0, 0);
  }, [frame]);
  useEffect(() => {
    if (!livePhysics || !hasSimulatedHair || brushingActive || busy || error || !path || !capabilities?.viewport) return;
    hairGroomDirtyRef.current = true;
    setHairBrush({
      nx: 0.5,
      ny: 0.5,
      prevNx: 0.5,
      prevNy: 0.5,
      radius: brushRadius,
      strength: brushStrength,
      tool: "physics",
      livePhysics: true,
      strokeId: brushStrokeIdRef.current,
      seq: ++brushSeqRef.current,
    });
  }, [livePhysics, hasSimulatedHair, brushingActive, busy, error, path, capabilities?.viewport, hairBrush, brushRadius, brushStrength]);
  useEffect(() => {
    if (!combMode || !hasSimulatedHair) return;
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable)
      ) {
        return;
      }
      if (event.key === "[" || event.key === "]") {
        event.preventDefault();
        setBrushRadius((prev) => {
          const factor = event.key === "[" ? 0.85 : 1.18;
          const next = Math.min(1000.0, Math.max(0.001, Number((prev * factor).toFixed(3))));
          setBrushRadiusText(String(next));
          return next;
        });
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [combMode, hasSimulatedHair]);
  const effectiveShowCacheMask = cacheSculptMode && (showCacheMask || cacheBrushTool === "mask" || cacheBrushTool === "unmask");
  const request = useMemo(
    () => {
      const popupHoverOnly = popupHoverPendingRef.current;
      popupHoverPendingRef.current = false;
      return {
      operation: "render",
      popupHoverOnly,
      comparison,
      cameraSession,
      frameFit,
      frameTarget,
      orbitPivotTarget: orbitPivot?.target,
      orbitPivotPoint: orbitPivot?.point ?? undefined,
      orbitPivotSeq: orbitPivot?.seq,
      isolatedPrim: isolatedPrim || undefined,
      hiddenPrims: hiddenPrims.length ? hiddenPrims : undefined,
      upAxis,
      purpose,
      complexity,
      drawMode,
      selection:selectedPrim?[selectedPrim]:[],
      path,
      graph,
      graphKey,
      renderer,
      ovrtxMode: renderer === "OmniverseRTX" ? ovrtxMode : undefined,
      ...(hairPopupOpen
        ? { width: popupSize.width, height: popupSize.height }
        : previewSize(size.width, size.height, renderer === "OmniverseRTX" ? false : interacting,
            (combMode || livePhysics) && Boolean(activeHairNodeId))),
      ...(hairPopupOpen ? popupCamera : camera),
      interactive: interacting || playing || simulating || livePhysics || hairPopupOpen || Boolean(cacheBrush),
      transformGizmoNodeId: gizmoNodeId,
      frame: time,
      materials,
      activeHairNodeId: activeHairNodeId || undefined,
      activeCacheEditNodeId: activeCacheEditNodeId || undefined,
      hairEditing: (combMode || livePhysics || hairPopupOpen) && Boolean(activeHairNodeId),
      nativePopup: renderer === "OmniverseRTX" && (nativePopup || hairPopupOpen),
      closeNativePopup: renderer === "OmniverseRTX" && !nativePopup && !hairPopupOpen,
      popupSkipImage: renderer === "OmniverseRTX" && (nativePopup || hairPopupOpen),
      popupStatus: hairPopupOpen ? `Rad: ${brushRadius.toFixed(3)} | Str: ${brushStrength.toFixed(2)} | ${brushVertexPct}% | ${brushTool}` : undefined,
      popupBrushRadiusPx: hairPopupOpen ? Math.max(8, Math.min(240, (brushRadius * popupSize.height * 0.45) / Math.max(0.02, popupCamera.zoom))) : undefined,
      hairBrush: hairBrush
        ? { ...hairBrush, vertexPct: brushVertexPct, nodeId: activeHairNodeId || undefined }
        : undefined,
      cacheBrush: cacheBrush
        ? { ...cacheBrush, nodeId: activeCacheEditNodeId || undefined }
        : undefined,
      clothSimStep:
        simulating && simSession
          ? { active: true, session: simSession.id, originFrame: simSession.originFrame }
          : undefined,
      showHairPins: (combMode || hairPopupOpen) && (showPins || brushTool === "pin" || brushTool === "unpin"),
      showCacheMask: effectiveShowCacheMask,
      revision,
      reloadTrigger,
      };
    },
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
      activeHairNodeId,
      activeCacheEditNodeId,
      hairBrush,
      cacheBrush,
      playing,
      simulating,
      simSession,
      combMode,
      effectiveShowCacheMask,
      hairPopupOpen,
      popupCamera,
      popupSize,
      popupHoverSeq,
      brushRadius,
      brushStrength,
      brushVertexPct,
      livePhysics,
      nativePopup,
      showPins,
      brushTool,
      revision,
      reloadTrigger,
      interacting,
      gizmoNodeId,
      selectedPrim,
      isolatedPrim,
      hiddenPrims,
      comparison,
      cameraSession,
      frameFit,
      frameTarget,
      orbitPivot,
      upAxis,
      purpose,
      complexity,
      drawMode,
    ],
  );
  const context = JSON.stringify([comparison,selectedPrim,isolatedPrim,hiddenPrims,
    path,
    contextGraphKey,
    renderer,
    request.ovrtxMode,
    time,
    materials,
    revision,
    frameFit,
    upAxis,
    purpose,
    complexity,
    drawMode,
  ]);
  const currentContextKey = `${context}:${reloadTrigger}`;
  const previousViewportRequest = useRef<{ context: string; request: typeof request } | null>(null);

  function commitHairBrush(
    brush: NonNullable<typeof hairBrush>,
    source = previousViewportRequest.current,
  ) {
    const nodeId = source?.request.activeHairNodeId;
    if (!source || !nodeId || !queue.current) return;
    const original = source.request;
    const lastSample = latestHairBrush.current;
    const unsentSample = lastSample && lastSample.seq > lastDispatchedHairSeq.current ? lastSample : null;
    if (unsentSample && !brush.settleOnly) {
      queue.current.enqueueRequired(source.context, {
        ...original, hairBrush: { ...unsentSample, nodeId },
      });
    }
    const finalBrush = brush.settleOnly && unsentSample ? { ...unsentSample, seq: brush.seq } : brush;
    queue.current.enqueueRequired(
      source.context,
      {
        ...original,
        nativePopup: renderer === "OmniverseRTX" && (nativePopup || hairPopupOpen),
        closeNativePopup: false,
        hairBrush: { ...finalBrush, nodeId, commitUsd: true },
      },
    );
    hairGroomDirtyRef.current = false;
    setHairBrush(null);
    setInteracting(false);
  }

  function commitCacheBrush(
    brush: NonNullable<typeof cacheBrush>,
    source = previousViewportRequest.current,
  ) {
    const nodeId = source?.request.activeCacheEditNodeId || activeCacheEditNodeId;
    if (!source || !nodeId || !queue.current) {
      setCacheBrush(null);
      setInteracting(false);
      return;
    }
    queue.current.enqueueRequired(source.context, {
      ...source.request,
      cacheBrush: { ...brush, nodeId, commitUsd: true },
    });
    setCacheBrush(null);
    setInteracting(false);
  }

  function finishHairStroke(source = previousViewportRequest.current) {
    commitHairBrush({
      nx: 0.5,
      ny: 0.5,
      prevNx: 0.5,
      prevNy: 0.5,
      radius: brushRadius,
      strength: brushStrength,
      tool: brushTool,
      settleOnly: true,
      seq: ++brushSeqRef.current,
    }, source);
  }

  latestContextKeyRef.current = currentContextKey;
  const showBusyStatus =
    busy && !gizmoDragging && !hairBrush && !playing && !simulating && (!frame || presentedContext !== currentContextKey || activeStatus !== null);
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
    const previous = previousViewportRequest.current;
    const changedHairOwner = previous?.request.activeHairNodeId && (
      previous.request.activeHairNodeId !== request.activeHairNodeId ||
      previous.request.graphKey !== request.graphKey ||
      previous.request.renderer !== request.renderer ||
      previous.request.ovrtxMode !== request.ovrtxMode
    );
    if (changedHairOwner) {
      if (hairGroomDirtyRef.current) finishHairStroke(previous);
      if (brushDragRef.current || cutLineRef.current || lengthenDragRef.current) {
        setInteracting(false);
      }
      brushDragRef.current = null;
      cutLineRef.current = null;
      lengthenDragRef.current = null;
      setCutLine(null);
      setLengthenDrag(null);
      setBrushingActive(false);
      setHairBrush(null);
    }
    if (!isTauri() || !capabilities?.viewport || !path || !renderer) {
      previousViewportRequest.current = null;
      queue.current?.clear();
      if (!path) setPresentedContext("");
      return;
    }
    const next = changedHairOwner ? { ...request, hairBrush: undefined } : request;
    previousViewportRequest.current = { context, request: next };
    queue.current?.enqueue(context, next);
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
    async (clientX: number, clientY: number, select = true, pickCamera?: ViewportCamera): Promise<PickResult | null> => {
      if (!host.current || !queue.current || !capabilities?.viewport || !path || !renderer) return null;
      const rect = host.current.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return null;
      const u = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      const v = Math.max(0, Math.min(1, (clientY - rect.top) / rect.height));
      const scheduler = queue.current;
      const picked = await new Promise<PickResult | null>((resolve) => {
        pendingPick.current?.resolve(null);
        pendingPick.current = { id: ++nextPickId.current, context: currentContextKey, coords: [u, v], resolve };
        scheduler.enqueue(context, { ...request, ...pickCamera, interactive: false });
      });
      if (picked && select) onSelectPrim?.(picked.prim);
      return picked;
    },
    [request, onSelectPrim, context, currentContextKey, capabilities, path, renderer],
  );
  const switchToGraphMode = useCallback(() => {
    setSceneMode(false);
    setContextMenu(null);
    drag.current = null;
    if (hairGroomDirtyRef.current) finishHairStroke();
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

  function getCacheBrushPxRad(viewWidth: number, viewHeight: number): number {
    const anchorRadius = Math.max(0.05, cameraAnchor.current?.radius ?? 0.5);
    const aspectMin = Math.min(viewWidth / Math.max(1, viewHeight), 1);
    const baseFovRad = Math.atan(Math.tan((22.5 * Math.PI) / 180) * aspectMin);
    const camDist = (anchorRadius / Math.sin(baseFovRad)) * 1.15 * Math.max(0.002, camera.zoom);
    const tanHalfFov = Math.tan((((camera.fov ?? 45) * 0.5) * Math.PI) / 180);
    // Scale brush radius relative to scene anchor so cursor is always intuitive regardless of asset units
    const worldRad = cacheBrushRadius * Math.max(0.1, anchorRadius);
    return Math.max(
      8,
      Math.min(viewHeight * 0.45, (worldRad / Math.max(1e-4, camDist * tanHalfFov)) * (viewHeight * 0.5)),
    );
  }

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
    if (cacheSculptMode && hasCacheEdit && event.button === 0 && !event.altKey && !isDolly) {
      const rect = host.current?.getBoundingClientRect();
      if (rect && rect.width > 0 && rect.height > 0) {
        const nx = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
        const ny = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
        const strokeId = ++cacheStrokeIdRef.current;
        cacheDragRef.current = { nx, ny, strokeId };
        lastCacheCursor.current = { strokeId, nx, ny };
        const pxRad = getCacheBrushPxRad(rect.width, rect.height);
        const screenRadiusFrac = pxRad / Math.max(1, rect.height * 0.5);
        setInteracting(true);
        setCacheBrush({
          nx,
          ny,
          prevNx: nx,
          prevNy: ny,
          radius: cacheBrushRadius,
          screenRadiusFrac,
          strength: cacheBrushStrength,
          tool: cacheBrushTool,
          invert: cacheBrushInvert || event.shiftKey,
          newStroke: true,
          strokeId,
          seq: ++cacheSeqRef.current,
        });
        event.currentTarget.setPointerCapture(event.pointerId);
        return;
      }
    }
    if (combMode && hasSimulatedHair && event.button === 0 && !event.shiftKey && !isDolly) {
      const rect = host.current?.getBoundingClientRect();
      if (rect && rect.width > 0 && rect.height > 0) {
        const nx = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
        const ny = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
        if (brushTool === "cut") {
          const x = event.clientX - rect.left;
          const y = event.clientY - rect.top;
          const nextLine = { x1: x, y1: y, x2: x, y2: y, nx1: nx, ny1: ny, nx2: nx, ny2: ny };
          cutLineRef.current = nextLine;
          setCutLine(nextLine);
          setInteracting(true);
          event.currentTarget.setPointerCapture(event.pointerId);
          return;
        }
        if (brushTool === "lengthen") {
          const x = event.clientX - rect.left;
          const y = event.clientY - rect.top;
          const nextLine = { x1: x, y1: y, x2: x, y2: y, nx1: nx, ny1: ny, nx2: nx, ny2: ny };
          lengthenDragRef.current = nextLine;
          setLengthenDrag(nextLine);
          setInteracting(true);
          event.currentTarget.setPointerCapture(event.pointerId);
          return;
        }
        brushDragRef.current = { nx, ny };
        brushNewStrokeRef.current = false;
        const nextStrokeId = ++brushStrokeIdRef.current;
        lastCombCursor.current = { strokeId: nextStrokeId, nx, ny };
        if (brushTool !== "pin" && brushTool !== "unpin") {
          hairGroomDirtyRef.current = true;
        }
        setBrushingActive(true);
        setInteracting(true);
        setHairBrush({
          nx,
          ny,
          prevNx: nx,
          prevNy: ny,
          radius: brushRadius,
          strength: brushStrength,
          tool: brushTool,
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
      pan: !isDolly && (event.shiftKey || event.button === 2),
      dolly: isDolly,
      camera,
    };
  }
  function endInteraction() {
    drag.current = null;
    brushDragRef.current = null;
    cutLineRef.current = null;
    setCutLine(null);
    lengthenDragRef.current = null;
    setLengthenDrag(null);
    setBrushingActive(false);
    if (hairGroomDirtyRef.current) finishHairStroke();
    setHairBrush(null);
    setInteracting(false);
  }
  async function handlePointerUp(event: PointerEvent<HTMLDivElement>) {
    const cs = clickStart.current;
    clickStart.current = null;
    const dist = cs ? Math.hypot(event.clientX - cs.x, event.clientY - cs.y) : Infinity;
    if (cutLineRef.current) {
      const line = cutLineRef.current;
      cutLineRef.current = null;
      setCutLine(null);
      setInteracting(false);
      if (Math.hypot(line.x2 - line.x1, line.y2 - line.y1) > 4) {
        commitHairBrush({
          nx: line.nx2,
          ny: line.ny2,
          prevNx: line.nx1,
          prevNy: line.ny1,
          radius: brushRadius,
          strength: brushStrength,
          tool: "cut",
          cutLine: [line.nx1, line.ny1, line.nx2, line.ny2],
          newStroke: true,
          seq: ++brushSeqRef.current,
        });
      }
      return;
    }
    if (lengthenDragRef.current) {
      const line = lengthenDragRef.current;
      lengthenDragRef.current = null;
      setLengthenDrag(null);
      setInteracting(false);
      if (Math.hypot(line.x2 - line.x1, line.y2 - line.y1) > 4) {
        commitHairBrush({
          nx: line.nx2,
          ny: line.ny2,
          prevNx: line.nx1,
          prevNy: line.ny1,
          radius: brushRadius,
          strength: brushStrength,
          tool: "lengthen",
          lengthenDrag: [line.nx1, line.ny1, line.nx2, line.ny2],
          newStroke: true,
          seq: ++brushSeqRef.current,
        });
      }
      return;
    }
    if (cacheDragRef.current) {
      const prev = cacheDragRef.current;
      cacheDragRef.current = null;
      const rect = host.current?.getBoundingClientRect();
      const nx = rect && rect.width > 0 ? Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)) : prev.nx;
      const ny = rect && rect.height > 0 ? Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)) : prev.ny;
      commitCacheBrush({
        nx,
        ny,
        prevNx: prev.nx,
        prevNy: prev.ny,
        radius: cacheBrushRadius,
        strength: cacheBrushStrength,
        tool: cacheBrushTool,
        invert: cacheBrushInvert || event.shiftKey,
        commitUsd: true,
        commitOnly: true,
        strokeId: prev.strokeId,
        seq: ++cacheSeqRef.current,
      });
      return;
    }
    if (brushDragRef.current) {
      brushDragRef.current = null;
      setBrushingActive(false);
      if (livePhysics || combMode) {
        hairGroomDirtyRef.current = true;
        setHairBrush(null);
        setInteracting(false);
      } else {
        finishHairStroke();
      }
      return;
    }
    if (drag.current) {
      const preClickCamera = drag.current.camera;
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
        const y = rect ? Math.min(Math.max(8, event.clientY - rect.top), Math.max(8, rect.height - 330)) : event.clientY;
        const menu = { x, y, prim: null, material: null, point: null, picking: true };
        setContextMenu(menu);
        const picked = await pickPrimAtClientPoint(event.clientX, event.clientY, false, preClickCamera);
        setContextMenu((cur) => cur === menu ? { ...cur, prim: picked?.prim ?? null, material: picked?.material ?? null, point: picked?.point, picking: false } : cur);
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
    if ((combMode && hasSimulatedHair) || (cacheSculptMode && hasCacheEdit)) {
      const rect = host.current?.getBoundingClientRect();
      if (rect && rect.width > 0 && rect.height > 0) {
        setBrushCursor({ x: event.clientX - rect.left, y: event.clientY - rect.top });
      }
    }
    if (cacheDragRef.current) {
      const rect = host.current?.getBoundingClientRect();
      if (!rect || rect.width <= 0 || rect.height <= 0) return;
      const nx = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
      const ny = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
      const prev = cacheDragRef.current;
      cacheDragRef.current = { nx, ny, strokeId: prev.strokeId };
      const pxRad = getCacheBrushPxRad(rect.width, rect.height);
      const screenRadiusFrac = pxRad / Math.max(1, rect.height * 0.5);
      setCacheBrush({
        nx,
        ny,
        prevNx: prev.nx,
        prevNy: prev.ny,
        radius: cacheBrushRadius,
        screenRadiusFrac,
        strength: cacheBrushStrength,
        tool: cacheBrushTool,
        invert: cacheBrushInvert || event.shiftKey,
        strokeId: prev.strokeId,
        seq: ++cacheSeqRef.current,
      });
      return;
    }
    if (cutLineRef.current) {
      const curLine = cutLineRef.current;
      const rect = host.current?.getBoundingClientRect();
      if (!rect || rect.width <= 0 || rect.height <= 0) return;
      const nx = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
      const ny = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
      const nextLine = {
        ...curLine,
        x2: event.clientX - rect.left,
        y2: event.clientY - rect.top,
        nx2: nx,
        ny2: ny,
      };
      cutLineRef.current = nextLine;
      setCutLine(nextLine);
      return;
    }
    if (lengthenDragRef.current) {
      const curLine = lengthenDragRef.current;
      const rect = host.current?.getBoundingClientRect();
      if (!rect || rect.width <= 0 || rect.height <= 0) return;
      const nx = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
      const ny = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
      const nextLine = {
        ...curLine,
        x2: event.clientX - rect.left,
        y2: event.clientY - rect.top,
        nx2: nx,
        ny2: ny,
      };
      lengthenDragRef.current = nextLine;
      setLengthenDrag(nextLine);
      return;
    }
    if (brushDragRef.current) {
      const rect = host.current?.getBoundingClientRect();
      if (!rect || rect.width <= 0 || rect.height <= 0) return;
      const nx = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
      const ny = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
      const prev = brushDragRef.current;
      brushDragRef.current = { nx, ny };
      if (brushTool === "add" || brushTool === "delete") {
        const now = performance.now();
        if (now - lastTopoBrushMsRef.current < 60) return;
        lastTopoBrushMsRef.current = now;
      }
      const isNewStroke = brushNewStrokeRef.current;
      brushNewStrokeRef.current = false;
      if (brushTool !== "pin" && brushTool !== "unpin") {
        hairGroomDirtyRef.current = true;
      }
      setHairBrush({
        nx,
        ny,
        prevNx: prev.nx,
        prevNy: prev.ny,
        radius: brushRadius,
        strength: brushStrength,
        tool: brushTool,
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
          <>
            <canvas
              ref={rgbaCanvasRef}
              className="hydra-frame"
              style={{ display: frame.imageBitmap ? "block" : "none" }}
            />
            {!frame.imageBitmap && (
              <img
                className="hydra-frame"
                src={frame.image}
                alt={`USD stage rendered by ${renderer} on ${frame.gpu}`}
                draggable={false}
              />
            )}
          </>
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
          onPointerLeave={() => setBrushCursor(null)}
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
        {combMode && hasSimulatedHair && lengthenDrag && (
          <svg
            style={{
              position: "absolute",
              inset: 0,
              width: "100%",
              height: "100%",
              pointerEvents: "none",
              zIndex: 6,
            }}
          >
            <line
              x1={lengthenDrag.x1}
              y1={lengthenDrag.y1}
              x2={lengthenDrag.x2}
              y2={lengthenDrag.y2}
              stroke="rgba(56, 189, 248, 0.95)"
              strokeWidth={2}
              strokeDasharray="6 4"
            />
            <circle cx={lengthenDrag.x2} cy={lengthenDrag.y2} r={4} fill="rgba(56, 189, 248, 0.95)" />
          </svg>
        )}
        {combMode && hasSimulatedHair && cutLine && (
          <svg
            style={{
              position: "absolute",
              inset: 0,
              width: "100%",
              height: "100%",
              pointerEvents: "none",
              zIndex: 6,
            }}
          >
            <line
              x1={cutLine.x1}
              y1={cutLine.y1}
              x2={cutLine.x2}
              y2={cutLine.y2}
              stroke="rgba(239, 68, 68, 0.95)"
              strokeWidth={2}
              strokeDasharray="6 4"
            />
          </svg>
        )}
        {cacheSculptMode && hasCacheEdit && brushCursor && (() => {
          let patchFalloff = 1.4;
          if (graph && activeCacheEditNodeId) {
            try {
              const g = JSON.parse(graph) as {
                nodes?: Array<{ id?: string; cacheEdit?: { clothDragPinFalloff?: number } }>;
              };
              const ce = g.nodes?.find((n) => n.id === activeCacheEditNodeId)?.cacheEdit;
              if (typeof ce?.clothDragPinFalloff === "number" && ce.clothDragPinFalloff >= 1.05) {
                patchFalloff = ce.clothDragPinFalloff;
              }
            } catch {
              // ignore
            }
          }
          const pxRad = getCacheBrushPxRad(size.width, size.height);
          const isCloth = cacheBrushTool.startsWith("cloth");
          const outerPxRad = Math.min(size.height * 0.65, pxRad * patchFalloff);
          const rgb =
            cacheBrushTool === "mask"
              ? "249, 115, 22"
              : cacheBrushTool === "unmask"
                ? "16, 185, 129"
                : isCloth
                  ? "56, 189, 248"
                  : "168, 85, 247";
          return (
            <>
              {isCloth && (
                <div
                  style={{
                    position: "absolute",
                    left: brushCursor.x - outerPxRad,
                    top: brushCursor.y - outerPxRad,
                    width: outerPxRad * 2,
                    height: outerPxRad * 2,
                    borderRadius: "50%",
                    border: `1.5px dashed rgba(${rgb}, 0.62)`,
                    boxShadow: "0 0 0 1px rgba(0, 0, 0, 0.35)",
                    pointerEvents: "none",
                    zIndex: 7,
                  }}
                />
              )}
              <div
                style={{
                  position: "absolute",
                  left: brushCursor.x - pxRad,
                  top: brushCursor.y - pxRad,
                  width: pxRad * 2,
                  height: pxRad * 2,
                  borderRadius: "50%",
                  border: `1.5px solid rgba(${rgb}, 0.95)`,
                  background: `radial-gradient(circle, rgba(${rgb}, 0.22) 0%, rgba(${rgb}, 0.08) 55%, rgba(${rgb}, 0) 100%)`,
                  boxShadow: "0 0 0 1px rgba(0, 0, 0, 0.5)",
                  pointerEvents: "none",
                  zIndex: 8,
                }}
              />
              <div
                style={{
                  position: "absolute",
                  left: brushCursor.x - 2,
                  top: brushCursor.y - 2,
                  width: 4,
                  height: 4,
                  borderRadius: "50%",
                  background: `rgba(${rgb}, 0.95)`,
                  boxShadow: "0 0 0 1px rgba(0, 0, 0, 0.7)",
                  pointerEvents: "none",
                  zIndex: 9,
                }}
              />
            </>
          );
        })()}
        {combMode && hasSimulatedHair && brushTool !== "cut" && brushCursor && (() => {
          const anchorRadius = Math.max(0.05, cameraAnchor.current?.radius ?? 0.5);
          const unitScale = Math.max(1e-4, cameraAnchor.current?.unitScale ?? 1.0);
          const aspectMin = Math.min(size.width / Math.max(1, size.height), 1);
          const baseFovRad = Math.atan(Math.tan((22.5 * Math.PI) / 180) * aspectMin);
          const camDist = (anchorRadius / Math.sin(baseFovRad)) * 1.15 * Math.max(0.002, camera.zoom);
          const tanHalfFov = Math.tan((((camera.fov ?? 45) * 0.5) * Math.PI) / 180);
          const pxRad = Math.max(
            6,
            Math.min(size.height * 0.45, ((brushRadius * unitScale) / Math.max(1e-4, camDist * tanHalfFov)) * (size.height * 0.5)),
          );
          return (
            <div
              style={{
                position: "absolute",
                left: brushCursor.x - pxRad,
                top: brushCursor.y - pxRad,
                width: pxRad * 2,
                height: pxRad * 2,
                borderRadius: "50%",
                border: `1.5px solid ${
                  brushTool === "pin"
                    ? "rgba(249, 115, 22, 0.92)"
                    : brushTool === "unpin"
                    ? "rgba(16, 185, 129, 0.92)"
                    : brushTool === "delete"
                    ? "rgba(239, 68, 68, 0.92)"
                    : brushTool === "add"
                    ? "rgba(34, 197, 94, 0.92)"
                    : brushTool === "lengthen" || brushTool === "shorten"
                    ? "rgba(56, 189, 248, 0.92)"
                    : "rgba(255, 220, 120, 0.85)"
                }`,
                boxShadow: "0 0 0 1px rgba(0,0,0,0.45)",
                pointerEvents: "none",
                zIndex: 6,
              }}
            />
          );
        })()}
        {transformGizmo && gizmoNodeId && frame?.cameraAnchor?.transformTarget === gizmoNodeId && (
          <TransformGizmo
            transform={transformGizmo.transform}
            camera={frame.presentedCamera ?? camera}
            upAxis={upAxis}
            width={size.width}
            height={size.height}
            radius={frame.cameraAnchor?.radius ?? cameraAnchor.current?.radius ?? 1}
            anchorTranslation={frame.cameraAnchor?.translation ?? cameraAnchor.current?.translation}
            interactive={sceneMode}
            onChange={transformGizmo.onChange}
            onCommit={transformGizmo.onCommit}
            onInteractingChange={onGizmoInteractingChange}
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
              disabled={contextMenu.picking || !contextMenu.material || !onSelectPrim}
              title={contextMenu.material || "No bound material on this prim"}
              onClick={() => {
                const target = contextMenu.material;
                setContextMenu(null);
                if (target && onSelectPrim) onSelectPrim(target);
              }}
            >
              Select material
            </button>
            <button
              type="button"
              disabled={contextMenu.picking || !contextMenu.prim || !onAddToMaterialX}
              title={onAddToMaterialX ? (contextMenu.prim ? `Add ${contextMenu.prim} to selected MaterialX row` : undefined) : "Open the MaterialX editor to add to the selected row"}
              onClick={() => {
                const target = contextMenu.prim;
                setContextMenu(null);
                if (target && onAddToMaterialX) onAddToMaterialX(target);
              }}
            >
              Add mesh to MaterialX row
            </button>
            <button
              type="button"
              disabled={contextMenu.picking || !contextMenu.material || !onAddToMaterialX}
              title={onAddToMaterialX ? (contextMenu.material || "No bound material on this prim") : "Open the MaterialX editor to add to the selected row"}
              onClick={() => {
                const target = contextMenu.material;
                setContextMenu(null);
                if (target && onAddToMaterialX) onAddToMaterialX(target);
              }}
            >
              Add material to MaterialX row
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
                setOrbitPivot({ target, point: contextMenu.point ?? null, seq: ++orbitPivotSeqRef.current });
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
            title="Re-read the USD stage and re-render the viewport"
            onClick={() => {
              setReloadTrigger((r) => r + 1);
              setRevision((r) => r + 1);
            }}
          >
            Reload stage
          </button>
          <button
            disabled={!isTauri()}
            title="Flush cached GPU scenes, geometry, and shaders from VRAM"
            onClick={async () => {
              if (!isTauri()) return;
              const { confirm } = await import("@tauri-apps/plugin-dialog");
              const confirmed = await confirm(
                "Flush cached GPU scenes, geometry, and shaders from VRAM and re-render? Your node graph and project files will not be affected.",
                { title: "Flush GPU cache", kind: "warning" },
              );
              if (!confirmed) return;
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
            Flush GPU
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
          {hasCacheEdit && (
            <div className="viewport-control-group">
              <button
                type="button"
                className={cacheSculptMode ? "chosen" : ""}
                title="Toggle Cache Sculptor Mesh Sculpting & Cloth Physics Brushes"
                onClick={() => setCacheSculptMode((prev) => !prev)}
              >
                {cacheSculptMode ? "Sculpt / Cloth (Active)" : "Sculpt / Cloth"}
              </button>
              {cacheSculptMode && (
                <>
                  {([
                    ["clothDrag", "Cloth Drag", "GPU XPBD Cloth Physics Drag Brush (preserves cloth length, folds & body collision)"],
                    ["clothRelax", "Cloth Relax", "GPU XPBD Cloth Drape / Relax Brush (settles wrinkles & fixes poke-through)"],
                    ["clothInflate", "Cloth Push", "GPU XPBD Cloth Push / Un-poke Brush (normal displacement with cloth physics)"],
                    ["grab", "Grab", "Screen-plane sculpt grab brush"],
                    ["smooth", "Smooth", "Volume-preserving Taubin surface smooth brush"],
                    ["inflate", "Inflate", "Displace along surface normals (Shift to push in)"],
                    ["pinch", "Pinch", "Sharpen folds and creases toward cursor"],
                    ["flatten", "Flatten", "Flatten surface toward local average plane"],
                    ["mask", "Mask", "Paint vertex mask / pin weights to lock cloth or mesh regions"],
                    ["unmask", "Unmask", "Erase vertex mask / pin weights"],
                  ] as const).map(([id, label, title]) => (
                    <button
                      key={id}
                      type="button"
                      className={cacheBrushTool === id ? "chosen" : ""}
                      onClick={() => setCacheBrushTool(id)}
                      title={title}
                    >
                      {label}
                    </button>
                  ))}
                  <label title="Brush radius (m) — hotkeys [ and ]">
                    Rad
                    <input
                      type="text"
                      inputMode="decimal"
                      value={cacheBrushRadiusText}
                      onChange={(e) => {
                        const raw = e.target.value;
                        setCacheBrushRadiusText(raw);
                        const v = Number(raw);
                        if (raw.trim() !== "" && Number.isFinite(v) && v >= 0.002 && v <= 100) {
                          setCacheBrushRadius(v);
                        }
                      }}
                      onBlur={() => {
                        const v = Number(cacheBrushRadiusText);
                        const clamped = Number.isFinite(v) ? Math.min(100, Math.max(0.002, v)) : cacheBrushRadius;
                        setCacheBrushRadius(clamped);
                        setCacheBrushRadiusText(String(Number(clamped.toFixed(3))));
                      }}
                      style={{ width: "48px" }}
                    />
                  </label>
                  <label title="Brush strength">
                    Str
                    <input
                      type="text"
                      inputMode="decimal"
                      value={cacheBrushStrengthText}
                      onChange={(e) => {
                        const raw = e.target.value;
                        setCacheBrushStrengthText(raw);
                        const v = Number(raw);
                        if (raw.trim() !== "" && Number.isFinite(v) && v >= 0.05 && v <= 4.0) {
                          setCacheBrushStrength(v);
                        }
                      }}
                      onBlur={() => {
                        const v = Number(cacheBrushStrengthText);
                        const clamped = Number.isFinite(v) ? Math.min(4.0, Math.max(0.05, v)) : cacheBrushStrength;
                        setCacheBrushStrength(clamped);
                        setCacheBrushStrengthText(String(Number(clamped.toFixed(2))));
                      }}
                      style={{ width: "42px" }}
                    />
                  </label>
                  <button
                    type="button"
                    className={cacheBrushInvert ? "chosen" : ""}
                    onClick={() => setCacheBrushInvert((v) => !v)}
                    title="Invert brush direction (or hold Shift while dragging)"
                  >
                    Invert
                  </button>
                  <button
                    type="button"
                    className={showCacheMask || cacheBrushTool === "mask" || cacheBrushTool === "unmask" ? "chosen" : ""}
                    onClick={() => setShowCacheMask((v) => !v)}
                    title="Visualize vertex mask / pin weights as smooth vertex shading (auto-enabled for Mask/Unmask brushes)"
                  >
                    Show Mask
                  </button>
                  <button
                    type="button"
                    title="Undo last sculpt / cloth brush stroke"
                    onClick={() => {
                      commitCacheBrush({
                        nx: 0.5, ny: 0.5, prevNx: 0.5, prevNy: 0.5,
                        radius: cacheBrushRadius, strength: cacheBrushStrength,
                        tool: cacheBrushTool, undoStroke: true, commitUsd: true,
                        seq: ++cacheSeqRef.current,
                      });
                    }}
                  >
                    Undo Stroke
                  </button>
                </>
              )}
            </div>
          )}
          {hasSimulatedHair && (
            <div className="viewport-control-group">
              <button
                type="button"
                className={combMode || hairPopupOpen ? "chosen" : ""}
                title="Open the dedicated Native Hair Grooming & Live Physics pop-up window"
                onClick={() => {
                  if (renderer === "OmniverseRTX") {
                    setHairPopupOpen((prev) => {
                      const next = !prev;
                      if (next) {
                        setPopupCamera({ ...camera });
                      } else if (hairGroomDirtyRef.current) {
                        finishHairStroke();
                      }
                      return next;
                    });
                    return;
                  }
                  setCombMode((prev) => {
                    const next = !prev;
                    if (!next && hairGroomDirtyRef.current) {
                      finishHairStroke();
                    }
                    return next;
                  });
                }}
              >
                {combMode || hairPopupOpen ? "Comb (Active)" : "Comb Hair"}
              </button>
              {combMode && (
                <>
                  {(["comb", "surfaceComb", "grab", "smooth", "physics", "clump", "part", "puff", "lengthen", "shorten", "cut", "add", "delete", "pin", "unpin"] as const).map((t) => (
                    <button
                      key={t}
                      type="button"
                      className={brushTool === t ? "chosen" : ""}
                      onClick={() => setBrushTool(t)}
                      title={t === "surfaceComb" ? "Surface Comb (arc along scalp surface)" : `Hair ${t} brush`}
                    >
                      {t === "surfaceComb" ? "Surface" : t[0].toUpperCase() + t.slice(1)}
                    </button>
                  ))}
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
                        if (raw.trim() !== "" && Number.isFinite(v) && v >= 0.001 && v <= 1000.0) {
                          setBrushRadius(v);
                        }
                      }}
                      onBlur={() => {
                        const v = Number(brushRadiusText);
                        const clamped = Number.isFinite(v)
                          ? Math.min(1000.0, Math.max(0.001, v))
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
                  <label className="viewport-comb-field" title="Percentage of strand vertices solved directly by brushes (in-between vertices interpolated)">
                    <span>Verts</span>
                    <select
                      value={brushVertexPct}
                      onChange={(e) => {
                        const v = Number(e.target.value);
                        setBrushVertexPct(v === 25 ? 25 : v === 50 ? 50 : 100);
                      }}
                    >
                      <option value={100}>100%</option>
                      <option value={50}>50%</option>
                      <option value={25}>25%</option>
                    </select>
                  </label>
                  <button
                    type="button"
                    title="Undo last hair brush stroke"
                    onClick={() => {
                      setBrushingActive(false);
                      commitHairBrush({
                        nx: 0.5,
                        ny: 0.5,
                        prevNx: 0.5,
                        prevNy: 0.5,
                        radius: brushRadius,
                        strength: brushStrength,
                        tool: brushTool,
                        undoStroke: true,
                        seq: ++brushSeqRef.current,
                      });
                    }}
                  >
                    Undo
                  </button>
                  <button
                    type="button"
                    className={livePhysics ? "chosen" : ""}
                    onClick={() => {
                      setLivePhysics((prev) => {
                        const next = !prev;
                        if (!next && hairGroomDirtyRef.current) {
                          finishHairStroke();
                        }
                        return next;
                      });
                    }}
                    title="Continuously run ADMM hair physics in the viewport"
                  >
                    Live Physics
                  </button>
                  {renderer === "OmniverseRTX" && (
                    <button
                      type="button"
                      className={nativePopup ? "chosen" : ""}
                      onClick={() => setNativePopup((v) => !v)}
                      title="Present Omniverse RTX frames directly in a zero-copy native D3D11 CUDA pop-up window"
                    >
                      Native Pop-up
                    </button>
                  )}
                  <button
                    type="button"
                    className={showPins || brushTool === "pin" || brushTool === "unpin" ? "chosen" : ""}
                    onClick={() => setShowPins((v) => !v)}
                    title="Show or hide pinned strand markers (always shown while Pin or Unpin is selected)"
                  >
                    {pinnedCount > 0 ? `Pins (${pinnedCount})` : "Show Pins"}
                  </button>
                  {pinnedCount > 0 && (
                    <button
                      type="button"
                      title="Unpin all strands"
                      onClick={() => {
                        setBrushingActive(false);
                        setPinnedCount(0);
                        setHairBrush({
                          nx: 0.5,
                          ny: 0.5,
                          prevNx: 0.5,
                          prevNy: 0.5,
                          radius: brushRadius,
                          strength: brushStrength,
                          tool: brushTool,
                          clearPins: true,
                          seq: ++brushSeqRef.current,
                        });
                      }}
                    >
                      Clear Pins
                    </button>
                  )}
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
                  const resetNid = activeHairNodeIdRef.current || graphTargetNodeIdRef.current;
                  if (resetNid && onCommitHairGroomRef.current) {
                    onCommitHairGroomRef.current(resetNid, {});
                  }
                  setRevision((r) => r + 1);
                }}
              >
                Reset hair
              </button>
            </div>
          )}
          <div className="viewport-control-group">
            <button
              type="button"
              disabled={!path || !capabilities?.viewport}
              title="Jump to start frame"
              onClick={() => {
                setPlaying(false);
      setSimulating(false);
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
      setSimulating(false);
                setTime((t) => t - 1);
              }}
            >
              ◀
            </button>
            <button
              type="button"
              disabled={!path || !capabilities?.viewport}
              className={playing ? "chosen" : ""}
              title={playing ? "Pause timeline preview" : "Play cached timeline preview (does not run simulation)"}
              onClick={() => {
                setSimulating(false);
                if (!playing && (time < playStart || time > playEnd)) {
                  setTime(playStart);
                }
                setPlaying((p) => !p);
              }}
            >
              {playing ? "⏸" : "▶"}
            </button>
            {hasCacheEditInGraph && (
              <button
                type="button"
                disabled={!path || !capabilities?.viewport}
                className={simulating ? "chosen" : ""}
                style={
                  simulating
                    ? {
                        background: "rgba(16,185,129,0.28)",
                        borderColor: "rgba(52,211,153,0.7)",
                        color: "#6ee7b7",
                      }
                    : undefined
                }
                title={
                  simulating
                    ? "Stop cloth simulation"
                    : "Simulate cloth forward from the current frame (caches each frame for Play)"
                }
                onClick={() => {
                  if (simulating) {
                    setSimulating(false);
                    return;
                  }
                  setPlaying(false);
                  setSimSession((prev) => ({
                    id: (prev?.id ?? 0) + 1,
                    originFrame: time,
                  }));
                  setSimulating(true);
                }}
              >
                {simulating ? "⏹ Sim" : "Sim"}
              </button>
            )}
            <button
              type="button"
              disabled={!path || !capabilities?.viewport}
              title="Next frame"
              onClick={() => {
                setPlaying(false);
      setSimulating(false);
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
      setSimulating(false);
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
      setSimulating(false);
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
              if (next === renderer) return;
              setRenderer(next);
              setRevision((r) => r + 1);
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
                if (next === ovrtxMode) return;
                setOvrtxMode(next);
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
          {renderer !== "OmniverseRTX" && (
            <>
              <select
                aria-label="USD geometry purpose"
                value={purpose}
                onChange={(e) => setPurpose(e.target.value as "render" | "proxy" | "both")}
                title="USD purpose to display in Hydra viewport (Proxy is fastest for large scenes)"
              >
                <option value="render">Purpose: Render</option>
                <option value="proxy">Purpose: Proxy</option>
                <option value="both">Purpose: Both</option>
              </select>
              <select
                aria-label="Subdivision complexity"
                value={complexity}
                onChange={(e) => setComplexity(e.target.value as "low" | "medium" | "high")}
                title="Hydra subdivision refinement complexity"
              >
                <option value="low">Detail: Low</option>
                <option value="medium">Detail: Med</option>
                <option value="high">Detail: High</option>
              </select>
              <select
                aria-label="Viewport draw mode"
                value={drawMode}
                onChange={(e) => setDrawMode(e.target.value as "shaded" | "solid" | "wireframe" | "points" | "bounds")}
                title="Viewport draw mode (Solid or Bounds are fastest for heavy scenes)"
              >
                <option value="shaded">Draw: Shaded</option>
                <option value="solid">Draw: Solid</option>
                <option value="wireframe">Draw: Wireframe</option>
                <option value="points">Draw: Points</option>
                <option value="bounds">Draw: Bounds</option>
              </select>
            </>
          )}
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
        {frame && !gizmoDragging ? (
          <span>
            {frame.updateKind ? `${frame.renderer === "OmniverseRTX" ? "RTX" : frame.renderer} (${frame.updateKind}) · ` : ""}
            {frame.width}×{frame.height} · {frame.upAxis} up ·{" "}
            {frame.renderMs.toFixed(0)} ms render · {frame.captureMs.toFixed(0)}{" "}
            ms capture · {frame.roundTripMs.toFixed(0)} ms round trip
            {combMode && lastHairFrame?.hairSolveStats ? ` · last groom: ${lastHairFrame.hairSolveStats.guideStrands} guides / ${lastHairFrame.hairSolveStats.totalStrands} strands${lastHairFrame.hairSolveStats.fullSolveStrands ? ` · full solve on ${lastHairFrame.hairSolveStats.fullSolveStrands}` : ""}` : ""}
            {combMode && typeof lastHairFrame?.hairSimMs === "number" ? ` · ${lastHairFrame.hairSimMs.toFixed(0)} ms groom solve and deformation` : ""}
            {combMode && typeof lastHairFrame?.hairUploadMs === "number" ? ` · ${lastHairFrame.hairUploadMs.toFixed(1)} ms RTX hair point upload` : ""}
            {combMode && typeof lastHairFrame?.hairSolveStats?.guideSolveGpuMs === "number" ? ` · ${lastHairFrame.hairSolveStats.guideSolveGpuMs.toFixed(1)} ms GPU guide solve` : ""}
            {combMode && typeof lastHairFrame?.hairSolveStats?.followerGpuMs === "number" ? ` · ${lastHairFrame.hairSolveStats.followerGpuMs.toFixed(1)} ms GPU follower reconstruction` : ""}
            {combMode && typeof lastHairFrame?.groomEncodeMs === "number" ? ` · ${lastHairFrame.groomEncodeMs.toFixed(0)} ms last groom encoding` : ""}
            {combMode && typeof lastHairFrame?.groomCommitMs === "number" ? ` · ${lastHairFrame.groomCommitMs.toFixed(0)} ms last groom commit round trip` : ""}
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
