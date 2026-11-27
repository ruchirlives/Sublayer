import { useEffect, useRef, useState, type RefObject } from "react";
import { TransformGizmo } from "./TransformGizmo";
import {
  identityTransform,
  type InstancerInstance,
  type InstancerSettings,
  type ViewportCamera,
  type Transform,
} from "./fileRegistry";
import {
  constrainTransform,
  movementLocks,
  placementDelta,
  type InstancerEditor,
  type InstancerHandle,
  type Vec3,
} from "./instancer";
type Pick = {
  prim: string;
  point?: Vec3 | null;
  sourcePrim?: string;
  origin?: Vec3;
  error?: string;
};
type Props = {
  editor: InstancerEditor;
  host: RefObject<HTMLDivElement | null>;
  active: boolean;
  camera: ViewportCamera;
  upAxis: "Y" | "Z";
  width: number;
  height: number;
  center: Vec3;
  radius: number;
  matrix?: number[][];
  handles: InstancerHandle[];
  pick: (x: number, y: number) => Promise<Pick | null>;
  onPreview: (settings: InstancerSettings | null) => void;
  onError: (error: string) => void;
  onNavigate: (dx: number, dy: number, done: boolean) => void;
};
type Gesture = {
  start: [number, number];
  last: [number, number];
  released: boolean;
  cancelled: boolean;
  pick: Pick | null;
  ready: boolean;
  before: InstancerSettings;
  editor: InstancerEditor;
  matrix: number[][];
  width: number;
  height: number;
  locks: boolean[];
  handles: InstancerHandle[];
  navigate: Props["onNavigate"];
  item?: InstancerInstance;
  preview?: InstancerSettings;
};
export function InstancerViewport(props: Props) {
  const live = useRef(props);
  live.current = props;
  const gesture = useRef<Gesture | null>(null);
  const gizmoDraft = useRef<InstancerSettings | null>(null);
  const [draft, setDraft] = useState<InstancerSettings | null>(null);
  const [gizmoEpoch, setGizmoEpoch] = useState(0);
  function preview(settings: InstancerSettings | null) {
    setDraft(settings);
    live.current.onPreview(settings);
  }
  function cancel() {
    const g = gesture.current;
    if (g) {
      g.cancelled = true;
      if (g.ready && !g.pick) g.navigate(0, 0, true);
    }
    gesture.current = null;
    gizmoDraft.current = null;
    setGizmoEpoch((v) => v + 1);
    preview(null);
  }
  function update(g: Gesture) {
    if (g.cancelled || !g.ready) return;
    if (!g.pick) {
      g.navigate(g.last[0] - g.start[0], g.last[1] - g.start[1], g.released);
      return;
    }
    if (Math.hypot(g.last[0] - g.start[0], g.last[1] - g.start[1]) < 6) return;
    if (!g.item) {
      const handle = g.handles
        .filter(
          (h) =>
            h.nodeId === g.editor.nodeId &&
            (g.pick!.prim === h.path || g.pick!.prim.startsWith(h.path + "/")),
        )
        .sort((a, b) => b.path.length - a.path.length)[0];
      const original =
        handle && g.before.instances.find((i) => i.id === handle.id);
      g.item = original
        ? { ...structuredClone(original), id: crypto.randomUUID() }
        : {
            id: crypto.randomUUID(),
            sourcePath: g.pick.sourcePrim || g.pick.prim,
            visible: true,
            transform: structuredClone(identityTransform),
          };
      g.editor.onSelect(g.item.id);
    }
    const origin = g.pick.origin || g.pick.point;
    if (!origin) return;
    const rect = live.current.host.current?.getBoundingClientRect();
    if (!rect) return;
    const delta = placementDelta(
      g.matrix,
      g.width,
      g.height,
      origin,
      [g.start[0] - rect.left, g.start[1] - rect.top],
      [g.last[0] - rect.left, g.last[1] - rect.top],
      g.locks,
    );
    const transform = {
      ...g.item.transform,
      translation: g.item.transform.translation.map(
        (v, i) => v + delta[i],
      ) as Vec3,
    };
    g.preview = {
      ...g.before,
      instances: [...g.before.instances, { ...g.item, transform }],
    };
    preview(g.preview);
  }
  function finish(g: Gesture) {
    if (g.cancelled) return;
    update(g);
    if (g.preview) {
      g.editor.onChange(g.preview);
      g.editor.onCommit();
    }
    gesture.current = null;
    preview(null);
  }
  useEffect(() => {
    const host = live.current.host.current;
    if (!host) return;
    let click: {
      x: number;
      y: number;
      editor: InstancerEditor;
    } | null = null;
    const down = (e: globalThis.PointerEvent) => {
      const p = live.current;
      if (
        !p.active ||
        e.button !== 0 ||
        e.ctrlKey ||
        e.metaKey ||
        e.shiftKey ||
        !(e.target instanceof Element) ||
        !e.target.closest(".scene-input")
      )
        return;
      if (!e.altKey) {
        click = { x: e.clientX, y: e.clientY, editor: p.editor };
        return;
      }
      if (!p.matrix) {
        p.onError("Wait for the viewport frame before placing instances.");
        return;
      }
      e.preventDefault();
      e.stopPropagation();
      click = null;
      if (gesture.current) cancel();
      const g: Gesture = {
        start: [e.clientX, e.clientY],
        last: [e.clientX, e.clientY],
        released: false,
        cancelled: false,
        pick: null,
        ready: false,
        before: structuredClone(p.editor.settings),
        editor: p.editor,
        matrix: p.matrix,
        width: p.width,
        height: p.height,
        locks: movementLocks(p.editor.settings, p.upAxis),
        handles: p.handles,
        navigate: p.onNavigate,
      };
      gesture.current = g;
      (e.target as HTMLElement).setPointerCapture(e.pointerId);
      void p
        .pick(e.clientX, e.clientY)
        .then((result) => {
          if (g.cancelled || gesture.current !== g) return;
          g.ready = true;
          g.pick = result;
          if (result?.error) {
            cancel();
            p.onError(result.error);
            return;
          }
          if (result && !result.origin && !result.point) {
            cancel();
            p.onError("No world-space pivot is available for this prim.");
            return;
          }
          if (g.released) finish(g);
          else update(g);
        })
        .catch((error) => {
          if (!g.cancelled) {
            cancel();
            p.onError(String(error));
          }
        });
    };
    const move = (e: globalThis.PointerEvent) => {
      const g = gesture.current;
      if (!g || g.released) return;
      e.preventDefault();
      e.stopPropagation();
      g.last = [e.clientX, e.clientY];
      update(g);
    };
    const up = (e: globalThis.PointerEvent) => {
      const g = gesture.current;
      if (g) {
        e.preventDefault();
        e.stopPropagation();
        g.last = [e.clientX, e.clientY];
        g.released = true;
        if (g.ready) finish(g);
        return;
      }
      const c = click;
      click = null;
      const p = live.current;
      if (
        c &&
        p.active &&
        p.editor.nodeId === c.editor.nodeId &&
        Math.hypot(e.clientX - c.x, e.clientY - c.y) < 6
      ) {
        void p
          .pick(c.x, c.y)
          .then((result) => {
            if (
              !live.current.active ||
              live.current.editor.nodeId !== c.editor.nodeId
            )
              return;
            const h =
              result &&
              live.current.handles.find(
                (h) =>
                  h.nodeId === c.editor.nodeId &&
                  (result.prim === h.path ||
                    result.prim.startsWith(h.path + "/")),
              );
            c.editor.onSelect(h?.id || null);
          })
          .catch((error) => p.onError(String(error)));
      }
    };
    const key = (e: KeyboardEvent) => {
      const p = live.current;
      if (
        !p.active ||
        e
          .composedPath()
          .some(
            (t) =>
              t instanceof HTMLElement &&
              (t.matches("input,textarea,select") || t.isContentEditable),
          )
      )
        return;
      if (
        (e.ctrlKey || e.metaKey) &&
        ["z", "y"].includes(e.key.toLowerCase()) &&
        (gesture.current || gizmoDraft.current)
      ) {
        e.preventDefault();
        e.stopImmediatePropagation();
        cancel();
        return;
      }
      if (e.key === "Escape" && gesture.current) {
        e.preventDefault();
        e.stopImmediatePropagation();
        cancel();
      }
      if (
        (e.key === "Delete" || e.key === "Backspace") &&
        p.editor.selectedId &&
        !e.ctrlKey &&
        !e.metaKey &&
        !gesture.current
      ) {
        e.preventDefault();
        e.stopImmediatePropagation();
        if (gizmoDraft.current) cancel();
        p.editor.onChange({
          ...p.editor.settings,
          instances: p.editor.settings.instances.filter(
            (i) => i.id !== p.editor.selectedId,
          ),
        });
        p.editor.onSelect(null);
      }
    };
    const abort = () => {
      click = null;
      if (gesture.current || gizmoDraft.current) cancel();
    };
    host.addEventListener("pointerdown", down, true);
    window.addEventListener("pointermove", move, true);
    window.addEventListener("pointerup", up, true);
    window.addEventListener("pointercancel", abort, true);
    window.addEventListener("blur", abort);
    window.addEventListener("keydown", key, true);
    return () => {
      host.removeEventListener("pointerdown", down, true);
      window.removeEventListener("pointermove", move, true);
      window.removeEventListener("pointerup", up, true);
      window.removeEventListener("pointercancel", abort, true);
      window.removeEventListener("blur", abort);
      window.removeEventListener("keydown", key, true);
      if (gesture.current) gesture.current.cancelled = true;
      live.current.onPreview(null);
    };
  }, []);
  useEffect(() => {
    if (!props.active) cancel();
  }, [props.active]);
  const current = draft || props.editor.settings;
  const selected = current.instances.find(
    (i) => i.id === props.editor.selectedId,
  );
  const handle = props.handles.find(
    (h) => h.nodeId === props.editor.nodeId && h.id === selected?.id,
  );
  return (
    <>
      {props.active && (
        <div
          style={{
            position: "absolute",
            bottom: 58,
            left: 16,
            pointerEvents: "none",
            zIndex: 6,
            color: "#b9e5d3",
            background: "#13241edb",
            padding: "5px 9px",
            borderRadius: 4,
          }}
        >
          Instancer · Alt-drag: create · Click: select · Delete: remove · Esc:
          cancel
        </div>
      )}
      {props.active && selected && handle && (
        <TransformGizmo
          key={`${selected.id}:${gizmoEpoch}`}
          transform={selected.transform}
          camera={props.camera}
          upAxis={props.upAxis}
          width={props.width}
          height={props.height}
          radius={props.radius}
          anchorTranslation={handle.translation}
          originOffset={
            handle.origin.map((v, i) => v - props.center[i]) as Vec3
          }
          interactive
          worldOnly
          translationLocks={movementLocks(current, props.upAxis)}
          onChange={(next: Transform) => {
            const start = props.editor.settings.instances.find(
              (i) => i.id === selected.id,
            );
            if (!start) return;
            const settings = {
              ...props.editor.settings,
              instances: props.editor.settings.instances.map((i) =>
                i.id === selected.id
                  ? {
                      ...i,
                      transform: constrainTransform(
                        next,
                        start.transform,
                        movementLocks(current, props.upAxis),
                      ),
                    }
                  : i,
              ),
            };
            gizmoDraft.current = settings;
            preview(settings);
          }}
          onCancel={cancel}
          onCommit={() => {
            if (gizmoDraft.current) {
              props.editor.onChange(gizmoDraft.current);
              props.editor.onCommit();
              gizmoDraft.current = null;
            }
            preview(null);
          }}
        />
      )}
    </>
  );
}
