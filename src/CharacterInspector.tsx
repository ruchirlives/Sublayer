import { useEffect, useRef, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import {
  defaultCharacterSettings,
  registerFiles,
  type CharacterAnimationOverride,
  type CharacterClothCache,
  type CharacterHeadGroom,
  type CharacterJointAttachment,
  type CharacterSettings,
  type CharacterSkeletonBinding,
  type Dependency,
} from "./fileRegistry";
import { NumberInput } from "./NumberInput";

type SkeletonInfo = {
  path: string;
  name: string;
  joints: string[];
  instanceProxy: boolean;
};

type AnimationInfo = {
  path: string;
  name: string;
  jointCount: number;
  startTime: number;
  endTime: number;
  timeSampleCount: number;
  instanceProxy: boolean;
};

type StagePrimInfo = {
  path: string;
  name: string;
  typeName: string;
  isMesh: boolean;
  isCurves?: boolean;
};

function makeRuleId(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
}

const GROOM_SCALE_PRESETS = [
  { label: "×0.01", value: 0.01 },
  { label: "×0.1", value: 0.1 },
  { label: "×1", value: 1 },
  { label: "×10", value: 10 },
  { label: "×100", value: 100 },
];

function hairQueryMatches(rawQuery: string, primPath: string): boolean {
  const terms = rawQuery
    .split(",")
    .map((t) => t.trim().toLowerCase())
    .filter(Boolean);
  const includes = terms.filter((t) => !t.startsWith("!"));
  const excludes = terms
    .filter((t) => t.startsWith("!"))
    .map((t) => t.slice(1).trim())
    .filter(Boolean);
  const low = primPath.toLowerCase();
  const nameLow = (primPath.split("/").pop() || "").toLowerCase();
  const hit = (term: string): boolean => {
    if (term === "*") return true;
    if (/[*?[]/.test(term)) {
      const escaped = term
        .replace(/[.+^${}()|[\]\\]/g, "\\$&")
        .replace(/\*/g, ".*")
        .replace(/\?/g, ".");
      try {
        const re = new RegExp(`^${escaped}$`);
        return re.test(low) || re.test(nameLow);
      } catch {
        return false;
      }
    }
    if (term.startsWith("/")) {
      const trimmed = term.replace(/\/+$/, "");
      return low === trimmed || low.startsWith(`${trimmed}/`);
    }
    return low.includes(term);
  };
  if (excludes.some(hit)) return false;
  if (includes.length === 0 || includes.includes("*")) return true;
  return includes.some(hit);
}

function characterQueryMatches(rawQuery: string, primPath: string): boolean {
  if (hairQueryMatches(rawQuery, primPath)) return true;
  const parts = primPath.split("/");
  if (parts.length >= 4 && parts[1] === "Scene" && parts[2].startsWith("Branch_")) {
    const unbranched = "/Scene/" + parts.slice(3).join("/");
    return hairQueryMatches(rawQuery, unbranched);
  }
  return false;
}

function filterOutermostStagePrims(prims: StagePrimInfo[]): StagePrimInfo[] {
  const sorted = [...prims].sort((a, b) => a.path.localeCompare(b.path));
  const out: StagePrimInfo[] = [];
  const prefixes: string[] = [];
  for (const p of sorted) {
    if (prefixes.some((pref) => p.path === pref || p.path.startsWith(`${pref}/`))) continue;
    out.push(p);
    prefixes.push(p.path);
  }
  return out;
}

export function CharacterInspector({
  value,
  graph,
  dependencies,
  onChange,
  onStatus,
  activeGroomGizmoId,
  onToggleGroomGizmo,
}: {
  value: CharacterSettings | null | undefined;
  graph: string | null;
  dependencies: Dependency[];
  onChange: (next: CharacterSettings, nextDependencies?: Dependency[]) => void;
  onStatus?: (msg: string) => void;
  activeGroomGizmoId?: string | null;
  onToggleGroomGizmo?: (groomId: string) => void;
}) {
  const cfg: CharacterSettings = {
    ...defaultCharacterSettings,
    ...(value || {}),
    animationOverrides: value?.animationOverrides || [],
    jointAttachments: value?.jointAttachments || [],
    skeletonBindings: value?.skeletonBindings || [],
    clothCaches: value?.clothCaches || [],
    headGrooms: value?.headGrooms || [],
  };

  const [draft, setDraft] = useState<CharacterSettings>(cfg);
  const debounceRef = useRef<number | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    setDraft(cfg);
  }, [value]);

  useEffect(() => {
    return () => {
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
      }
    };
  }, []);

  const commitNow = (patch: Partial<CharacterSettings>, nextDeps?: Dependency[]) => {
    if (debounceRef.current !== null) {
      window.clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
    const next = { ...draft, ...patch };
    setDraft(next);
    onChangeRef.current(next, nextDeps);
  };

  const commitDebounced = (patch: Partial<CharacterSettings>) => {
    const next = { ...draft, ...patch };
    setDraft(next);
    if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      debounceRef.current = null;
      onChangeRef.current(next);
    }, 250);
  };

  const [skeletons, setSkeletons] = useState<SkeletonInfo[] | null>(null);
  const [animations, setAnimations] = useState<AnimationInfo[] | null>(null);
  const [stagePrims, setStagePrims] = useState<StagePrimInfo[] | null>(null);
  const [scanning, setScanning] = useState(false);
  const [exportingBody, setExportingBody] = useState(false);
  const [scanError, setScanError] = useState("");

  async function scanSkeletons() {
    if (!graph || !isTauri()) return;
    setScanning(true);
    setScanError("");
    try {
      const res = await invoke<{
        skeletons?: SkeletonInfo[];
        animations?: AnimationInfo[];
        prims?: StagePrimInfo[];
      }>("usd_request", {
        request: { operation: "inspect_stage_skeletons", graph: JSON.parse(graph) },
      });
      setSkeletons(res?.skeletons || []);
      setAnimations(res?.animations || []);
      setStagePrims(res?.prims || []);
    } catch (e) {
      setScanError(String(e));
    } finally {
      setScanning(false);
    }
  }

  const resolveJointsForSkeleton = (skelPath: string): string[] => {
    if (!skeletons || skeletons.length === 0) return [];
    const wanted = (skelPath || draft.baseSkeletonPath || "").trim();
    const match =
      (wanted && skeletons.find((s) => s.path === wanted || s.path.endsWith(wanted))) ||
      skeletons[0];
    return match?.joints || [];
  };

  const addAnimationOverride = () => {
    const firstAnim = animations?.[0]?.path || "";
    const nextRule: CharacterAnimationOverride = {
      id: makeRuleId("anim"),
      enabled: true,
      skeletonPath: draft.baseSkeletonPath || "",
      animationPath: firstAnim,
      mode: "pose",
      poseTime: animations?.[0]?.startTime ?? 0,
    };
    commitNow({ animationOverrides: [...draft.animationOverrides, nextRule] });
  };

  const updateAnimationOverride = (
    id: string,
    patch: Partial<CharacterAnimationOverride>,
    immediate = false
  ) => {
    const next = draft.animationOverrides.map((r) => (r.id === id ? { ...r, ...patch } : r));
    if (immediate) commitNow({ animationOverrides: next });
    else commitDebounced({ animationOverrides: next });
  };

  const removeAnimationOverride = (id: string) => {
    commitNow({ animationOverrides: draft.animationOverrides.filter((r) => r.id !== id) });
  };

  const addSkeletonBinding = () => {
    const nextRule: CharacterSkeletonBinding = {
      id: makeRuleId("skel"),
      enabled: true,
      targetQuery: "",
      skeletonPath: draft.baseSkeletonPath || "",
      deactivateSourceSkeletons: true,
    };
    commitNow({ skeletonBindings: [...draft.skeletonBindings, nextRule] });
  };

  const updateSkeletonBinding = (
    id: string,
    patch: Partial<CharacterSkeletonBinding>,
    immediate = false
  ) => {
    const next = draft.skeletonBindings.map((r) => (r.id === id ? { ...r, ...patch } : r));
    if (immediate) commitNow({ skeletonBindings: next });
    else commitDebounced({ skeletonBindings: next });
  };

  const removeSkeletonBinding = (id: string) => {
    commitNow({ skeletonBindings: draft.skeletonBindings.filter((r) => r.id !== id) });
  };

  const addJointAttachment = () => {
    const joints = resolveJointsForSkeleton("");
    const nextRule: CharacterJointAttachment = {
      id: makeRuleId("joint"),
      enabled: true,
      targetQuery: "",
      skeletonPath: draft.baseSkeletonPath || "",
      jointName: joints[0] || "",
      offsetTranslation: [0, 0, 0],
      offsetRotation: [0, 0, 0],
      offsetScale: [1, 1, 1],
    };
    commitNow({ jointAttachments: [...draft.jointAttachments, nextRule] });
  };

  const updateJointAttachment = (
    id: string,
    patch: Partial<CharacterJointAttachment>,
    immediate = false
  ) => {
    const next = draft.jointAttachments.map((r) => (r.id === id ? { ...r, ...patch } : r));
    if (immediate) commitNow({ jointAttachments: next });
    else commitDebounced({ jointAttachments: next });
  };

  const removeJointAttachment = (id: string) => {
    commitNow({ jointAttachments: draft.jointAttachments.filter((r) => r.id !== id) });
  };

  const addClothCache = () => {
    const nextRule: CharacterClothCache = {
      id: makeRuleId("cloth"),
      enabled: true,
      dependencyId: null,
      attachToRoot: true,
      axis: "preserve",
      frontAxis: "preserve",
      scale: 1.0,
      timeOffset: 0.0,
    };
    commitNow({ clothCaches: [...draft.clothCaches, nextRule] });
  };

  const updateClothCache = (
    id: string,
    patch: Partial<CharacterClothCache>,
    immediate = false,
    nextDeps?: Dependency[]
  ) => {
    const next = draft.clothCaches.map((r) => (r.id === id ? { ...r, ...patch } : r));
    if (immediate) commitNow({ clothCaches: next }, nextDeps);
    else commitDebounced({ clothCaches: next });
  };

  const removeClothCache = (id: string) => {
    commitNow({ clothCaches: draft.clothCaches.filter((r) => r.id !== id) });
  };

  const addHeadGroom = () => {
    const nextRule: CharacterHeadGroom = {
      id: makeRuleId("groom"),
      enabled: true,
      sourceMode: "file",
      dependencyId: null,
      targetQuery: "",
      attachMode: "joint",
      skeletonPath: draft.baseSkeletonPath || "",
      headJointName: "",
      preserveRestPosition: true,
      headMeshPath: "",
      axis: "preserve",
      frontAxis: "preserve",
      scale: 1.0,
      offsetTranslation: [0, 0, 0],
      offsetRotation: [0, 0, 0],
      offsetScale: [1, 1, 1],
    };
    commitNow({ headGrooms: [...draft.headGrooms, nextRule] });
  };

  const updateHeadGroom = (
    id: string,
    patch: Partial<CharacterHeadGroom>,
    immediate = false,
    nextDeps?: Dependency[]
  ) => {
    const next = draft.headGrooms.map((r) => (r.id === id ? { ...r, ...patch } : r));
    if (immediate) commitNow({ headGrooms: next }, nextDeps);
    else commitDebounced({ headGrooms: next });
  };

  const removeHeadGroom = (id: string) => {
    commitNow({ headGrooms: draft.headGrooms.filter((r) => r.id !== id) });
  };

  async function browseHeadGroom(id: string) {
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const picked = await open({
        multiple: false,
        filters: [
          {
            name: "Hair Groom (Alembic / USD)",
            extensions: ["abc", "usd", "usda", "usdc"],
          },
        ],
      });
      if (!picked || typeof picked !== "string") return;
      const { dependencies: nextDeps, ids } = registerFiles(
        dependencies,
        [picked],
        "usd"
      );
      updateHeadGroom(id, { dependencyId: ids[0], sourceMode: "file" }, true, nextDeps);
      onStatus?.(`Linked head groom: ${picked.split(/[\\/]/).pop()}`);
    } catch (e) {
      onStatus?.(`Head groom browse failed: ${String(e)}`);
    }
  }

  async function browseClothCache(id: string) {
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const picked = await open({
        multiple: false,
        filters: [
          {
            name: "Simulated Cloth Cache",
            extensions: ["usd", "usda", "usdc", "abc"],
          },
        ],
      });
      if (!picked || typeof picked !== "string") return;
      const { dependencies: nextDeps, ids } = registerFiles(
        dependencies,
        [picked],
        "usd"
      );
      updateClothCache(id, { dependencyId: ids[0] }, true, nextDeps);
      onStatus?.(`Linked cloth cache: ${picked.split(/[\\/]/).pop()}`);
    } catch (e) {
      onStatus?.(`Cloth cache browse failed: ${String(e)}`);
    }
  }

  async function exportAnimatedBody() {
    if (!graph || !isTauri()) return;
    try {
      const { save } = await import("@tauri-apps/plugin-dialog");
      const outputPath = await save({
        defaultPath: "character_body_anim.usd",
        filters: [{ name: "USD Stage", extensions: ["usd", "usda", "usdc"] }],
      });
      if (!outputPath) return;
      setExportingBody(true);
      setScanError("");
      const res = await invoke<{
        path?: string;
        meshCount?: number;
        frameCount?: number;
      }>("usd_request", {
        request: {
          operation: "export_character_body",
          graph: JSON.parse(graph),
          outputPath,
        },
      });
      onStatus?.(
        `Exported animated body USD (${res?.meshCount ?? 0} mesh(es), ${res?.frameCount ?? 1} frame(s)) to ${res?.path || outputPath}`
      );
    } catch (e) {
      setScanError(String(e));
      onStatus?.(`Export animated body failed: ${String(e)}`);
    } finally {
      setExportingBody(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "6px" }}>
        <span className="field-label">Character Rig &amp; Assembly</span>
        <div style={{ display: "flex", gap: "4px" }}>
          <button type="button" onClick={scanSkeletons} disabled={!graph || scanning} style={{ padding: "3px 8px", fontSize: "11px" }}>
            {scanning ? "Scanning..." : "Scan Skeletons & Anims"}
          </button>
          <button type="button" onClick={exportAnimatedBody} disabled={!graph || exportingBody} style={{ padding: "3px 8px", fontSize: "11px" }} title="Bake deformed/animated body meshes to USD for Marvelous Designer">
            {exportingBody ? "Exporting..." : "Export Body USD..."}
          </button>
        </div>
      </div>
      {scanError && <p className="hint" style={{ color: "#f87171", margin: 0 }}>{scanError}</p>}
      {skeletons !== null && (
        <small style={{ opacity: 0.75, fontSize: "11px" }}>
          Found {skeletons.length} skeleton(s), {animations?.length || 0} animation(s).
        </small>
      )}

      <label>
        <span className="field-label">Default Base Skeleton</span>
        {skeletons && skeletons.length > 0 ? (
          <select
            value={draft.baseSkeletonPath}
            onChange={(e) => commitNow({ baseSkeletonPath: e.target.value })}
          >
            <option value="">Auto-detect first skeleton</option>
            {skeletons.map((s) => (
              <option key={s.path} value={s.path}>
                {s.name} ({s.joints.length} joints) — {s.path}
              </option>
            ))}
          </select>
        ) : (
          <input
            type="text"
            value={draft.baseSkeletonPath}
            onChange={(e) => commitDebounced({ baseSkeletonPath: e.target.value })}
            placeholder="Auto-detect (or /Scene/Branch_.../Skeleton)"
          />
        )}
      </label>

      {/* Section 1: Animation & Pose */}
      <div style={{ borderTop: "1px solid rgba(255,255,255,0.12)", paddingTop: "8px", display: "flex", flexDirection: "column", gap: "6px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span className="field-label">Animation &amp; Pose</span>
          <button type="button" onClick={addAnimationOverride} style={{ padding: "2px 7px", fontSize: "11px" }}>
            + Add Pose / Anim
          </button>
        </div>
        {draft.animationOverrides.length === 0 && (
          <p className="hint" style={{ margin: 0 }}>No animation/pose overrides; uses source skeleton defaults.</p>
        )}
        {draft.animationOverrides.map((rule) => (
          <div
            key={rule.id}
            style={{
              padding: "6px",
              borderRadius: "4px",
              border: "1px solid rgba(255,255,255,0.12)",
              background: "rgba(0,0,0,0.16)",
              display: "flex",
              flexDirection: "column",
              gap: "5px",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "6px" }}>
              <label style={{ display: "flex", alignItems: "center", gap: "5px", fontSize: "11px", margin: 0 }}>
                <input
                  type="checkbox"
                  checked={rule.enabled}
                  onChange={(e) => updateAnimationOverride(rule.id, { enabled: e.target.checked }, true)}
                />
                Enabled
              </label>
              <button
                type="button"
                onClick={() => removeAnimationOverride(rule.id)}
                style={{ padding: "1px 6px", fontSize: "11px" }}
              >
                Remove
              </button>
            </div>
            <label>
              <span className="field-label">Target Skeleton</span>
              {skeletons && skeletons.length > 0 ? (
                <select
                  value={rule.skeletonPath}
                  onChange={(e) => updateAnimationOverride(rule.id, { skeletonPath: e.target.value }, true)}
                >
                  <option value="">Default Base Skeleton</option>
                  {skeletons.map((s) => (
                    <option key={s.path} value={s.path}>{s.path}</option>
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  value={rule.skeletonPath}
                  onChange={(e) => updateAnimationOverride(rule.id, { skeletonPath: e.target.value })}
                  placeholder="Default Base Skeleton"
                />
              )}
            </label>
            <label>
              <span className="field-label">Animation Prim</span>
              {animations && animations.length > 0 ? (
                <select
                  value={rule.animationPath}
                  onChange={(e) => updateAnimationOverride(rule.id, { animationPath: e.target.value }, true)}
                >
                  <option value="">Select animation...</option>
                  {animations.map((a) => (
                    <option key={a.path} value={a.path}>
                      {a.name} ({a.startTime}..{a.endTime}) — {a.path}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  value={rule.animationPath}
                  onChange={(e) => updateAnimationOverride(rule.id, { animationPath: e.target.value })}
                  placeholder="/Scene/Branch_.../Animation"
                />
              )}
            </label>
            <div style={{ display: "flex", gap: "6px", alignItems: "flex-end" }}>
              <label style={{ flex: 1 }}>
                <span className="field-label">Mode</span>
                <select
                  value={rule.mode}
                  onChange={(e) =>
                    updateAnimationOverride(rule.id, { mode: e.target.value as "pose" | "clip" }, true)
                  }
                >
                  <option value="pose">Pose at Time</option>
                  <option value="clip">Play Clip</option>
                </select>
              </label>
              {rule.mode === "pose" && (
                <label style={{ flex: 1 }}>
                  <span className="field-label">Pose Time</span>
                  <NumberInput
                    value={rule.poseTime}
                    step={1}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      if (Number.isFinite(v)) updateAnimationOverride(rule.id, { poseTime: v });
                    }}
                  />
                </label>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Section 2: Wildcard -> Skeleton Bindings */}
      <div style={{ borderTop: "1px solid rgba(255,255,255,0.12)", paddingTop: "8px", display: "flex", flexDirection: "column", gap: "6px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span className="field-label">Skeleton Bindings (Wildcard → Skel)</span>
          <button type="button" onClick={addSkeletonBinding} style={{ padding: "2px 7px", fontSize: "11px" }}>
            + Add Binding
          </button>
        </div>
        {draft.skeletonBindings.length === 0 && (
          <p className="hint" style={{ margin: 0 }}>Assign clothing/mesh wildcards to bind to a target skeleton.</p>
        )}
        {draft.skeletonBindings.map((rule) => (
          <div
            key={rule.id}
            style={{
              padding: "6px",
              borderRadius: "4px",
              border: "1px solid rgba(255,255,255,0.12)",
              background: "rgba(0,0,0,0.16)",
              display: "flex",
              flexDirection: "column",
              gap: "5px",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "6px" }}>
              <label style={{ display: "flex", alignItems: "center", gap: "5px", fontSize: "11px", margin: 0 }}>
                <input
                  type="checkbox"
                  checked={rule.enabled}
                  onChange={(e) => updateSkeletonBinding(rule.id, { enabled: e.target.checked }, true)}
                />
                Enabled
              </label>
              <button
                type="button"
                onClick={() => removeSkeletonBinding(rule.id)}
                style={{ padding: "1px 6px", fontSize: "11px" }}
              >
                Remove
              </button>
            </div>
            <label>
              <span className="field-label">Target Wildcard</span>
              <input
                type="text"
                value={rule.targetQuery}
                onChange={(e) => updateSkeletonBinding(rule.id, { targetQuery: e.target.value })}
                placeholder="*Shirt*, *Pants*, /Scene/Branch_..."
              />
            </label>
            <label>
              <span className="field-label">Target Skeleton</span>
              {skeletons && skeletons.length > 0 ? (
                <select
                  value={rule.skeletonPath}
                  onChange={(e) => updateSkeletonBinding(rule.id, { skeletonPath: e.target.value }, true)}
                >
                  <option value="">Default Base Skeleton</option>
                  {skeletons.map((s) => (
                    <option key={s.path} value={s.path}>{s.path}</option>
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  value={rule.skeletonPath}
                  onChange={(e) => updateSkeletonBinding(rule.id, { skeletonPath: e.target.value })}
                  placeholder="Default Base Skeleton"
                />
              )}
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "11px", margin: 0 }}>
              <input
                type="checkbox"
                checked={rule.deactivateSourceSkeletons}
                onChange={(e) =>
                  updateSkeletonBinding(rule.id, { deactivateSourceSkeletons: e.target.checked }, true)
                }
              />
              Deactivate duplicate source skeletons/anims
            </label>
          </div>
        ))}
      </div>

      {/* Section 3: Wildcard -> Joint Attachments */}
      <div style={{ borderTop: "1px solid rgba(255,255,255,0.12)", paddingTop: "8px", display: "flex", flexDirection: "column", gap: "6px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span className="field-label">Joint Attachments (Wildcard → Joint)</span>
          <button type="button" onClick={addJointAttachment} style={{ padding: "2px 7px", fontSize: "11px" }}>
            + Add Attachment
          </button>
        </div>
        {draft.jointAttachments.length === 0 && (
          <p className="hint" style={{ margin: 0 }}>Attach rigid props, Xforms, lights, or grooms to skeleton joints.</p>
        )}
        {draft.jointAttachments.map((rule) => {
          const joints = resolveJointsForSkeleton(rule.skeletonPath);
          return (
            <div
              key={rule.id}
              style={{
                padding: "6px",
                borderRadius: "4px",
                border: "1px solid rgba(255,255,255,0.12)",
                background: "rgba(0,0,0,0.16)",
                display: "flex",
                flexDirection: "column",
                gap: "5px",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "6px" }}>
                <label style={{ display: "flex", alignItems: "center", gap: "5px", fontSize: "11px", margin: 0 }}>
                  <input
                    type="checkbox"
                    checked={rule.enabled}
                    onChange={(e) => updateJointAttachment(rule.id, { enabled: e.target.checked }, true)}
                  />
                  Enabled
                </label>
                <button
                  type="button"
                  onClick={() => removeJointAttachment(rule.id)}
                  style={{ padding: "1px 6px", fontSize: "11px" }}
                >
                  Remove
                </button>
              </div>
              <label>
                <span className="field-label">Prim / Xform Wildcard</span>
                <input
                  type="text"
                  value={rule.targetQuery}
                  onChange={(e) => updateJointAttachment(rule.id, { targetQuery: e.target.value })}
                  placeholder="*Helmet*, *Sword*, /Scene/Branch_.../Prop"
                />
              </label>
              <label>
                <span className="field-label">Skeleton</span>
                {skeletons && skeletons.length > 0 ? (
                  <select
                    value={rule.skeletonPath}
                    onChange={(e) => updateJointAttachment(rule.id, { skeletonPath: e.target.value }, true)}
                  >
                    <option value="">Default Base Skeleton</option>
                    {skeletons.map((s) => (
                      <option key={s.path} value={s.path}>{s.path}</option>
                    ))}
                  </select>
                ) : (
                  <input
                    type="text"
                    value={rule.skeletonPath}
                    onChange={(e) => updateJointAttachment(rule.id, { skeletonPath: e.target.value })}
                    placeholder="Default Base Skeleton"
                  />
                )}
              </label>
              <label>
                <span className="field-label">Joint</span>
                {joints.length > 0 ? (
                  <select
                    value={rule.jointName}
                    onChange={(e) => updateJointAttachment(rule.id, { jointName: e.target.value }, true)}
                  >
                    <option value="">Select joint...</option>
                    {joints.map((j) => (
                      <option key={j} value={j}>{j}</option>
                    ))}
                  </select>
                ) : (
                  <input
                    type="text"
                    value={rule.jointName}
                    onChange={(e) => updateJointAttachment(rule.id, { jointName: e.target.value })}
                    placeholder="head, hand_r, spine_03..."
                  />
                )}
              </label>
              <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                <span className="field-label">Offset Translate (m)</span>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "4px" }}>
                  {[0, 1, 2].map((idx) => (
                    <NumberInput
                      key={idx}
                      value={rule.offsetTranslation[idx]}
                      step={0.01}
                      onChange={(e) => {
                        const v = Number(e.target.value);
                        if (!Number.isFinite(v)) return;
                        const next: [number, number, number] = [...rule.offsetTranslation] as [number, number, number];
                        next[idx] = v;
                        updateJointAttachment(rule.id, { offsetTranslation: next });
                      }}
                    />
                  ))}
                </div>
                <span className="field-label">Offset Rotate (deg)</span>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "4px" }}>
                  {[0, 1, 2].map((idx) => (
                    <NumberInput
                      key={idx}
                      value={rule.offsetRotation[idx]}
                      step={1}
                      onChange={(e) => {
                        const v = Number(e.target.value);
                        if (!Number.isFinite(v)) return;
                        const next: [number, number, number] = [...rule.offsetRotation] as [number, number, number];
                        next[idx] = v;
                        updateJointAttachment(rule.id, { offsetRotation: next });
                      }}
                    />
                  ))}
                </div>
                <span className="field-label">Offset Scale</span>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "4px" }}>
                  {[0, 1, 2].map((idx) => (
                    <NumberInput
                      key={idx}
                      value={rule.offsetScale[idx]}
                      step={0.05}
                      onChange={(e) => {
                        const v = Number(e.target.value);
                        if (!Number.isFinite(v) || v === 0) return;
                        const next: [number, number, number] = [...rule.offsetScale] as [number, number, number];
                        next[idx] = v;
                        updateJointAttachment(rule.id, { offsetScale: next });
                      }}
                    />
                  ))}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Section 4: Animated Cloth Caches */}
      <div style={{ borderTop: "1px solid rgba(255,255,255,0.12)", paddingTop: "8px", display: "flex", flexDirection: "column", gap: "6px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span className="field-label">Animated Cloth Caches (MD / Sim)</span>
          <button type="button" onClick={addClothCache} style={{ padding: "2px 7px", fontSize: "11px" }}>
            + Add Cloth Cache
          </button>
        </div>
        {draft.clothCaches.length === 0 && (
          <p className="hint" style={{ margin: 0 }}>
            Attach simulated .usd/.abc cloth caches to the character root with axis &amp; scale overrides.
          </p>
        )}
        {draft.clothCaches.map((rule) => {
          const dep = rule.dependencyId
            ? dependencies.find((d) => d.id === rule.dependencyId)
            : undefined;
          return (
            <div
              key={rule.id}
              style={{
                padding: "6px",
                borderRadius: "4px",
                border: "1px solid rgba(255,255,255,0.12)",
                background: "rgba(0,0,0,0.16)",
                display: "flex",
                flexDirection: "column",
                gap: "5px",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "6px" }}>
                <label style={{ display: "flex", alignItems: "center", gap: "5px", fontSize: "11px", margin: 0 }}>
                  <input
                    type="checkbox"
                    checked={rule.enabled}
                    onChange={(e) => updateClothCache(rule.id, { enabled: e.target.checked }, true)}
                  />
                  Enabled
                </label>
                <button
                  type="button"
                  onClick={() => removeClothCache(rule.id)}
                  style={{ padding: "1px 6px", fontSize: "11px" }}
                >
                  Remove
                </button>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <button type="button" onClick={() => browseClothCache(rule.id)} style={{ padding: "3px 8px", fontSize: "11px" }}>
                  Browse Cache...
                </button>
                <span style={{ fontSize: "11px", opacity: 0.85, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }} title={dep?.path || ""}>
                  {dep ? dep.path.split(/[\\/]/).pop() : "No cache file linked"}
                </span>
              </div>
              <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "11px", margin: 0 }}>
                <input
                  type="checkbox"
                  checked={rule.attachToRoot}
                  onChange={(e) => updateClothCache(rule.id, { attachToRoot: e.target.checked }, true)}
                />
                Attach to character root
              </label>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px" }}>
                <label>
                  <span className="field-label">Up Axis</span>
                  <select
                    value={rule.axis}
                    onChange={(e) => updateClothCache(rule.id, { axis: e.target.value }, true)}
                  >
                    {["preserve", "Y", "-Y", "Z", "-Z", "X", "-X"].map((ax) => (
                      <option key={ax} value={ax}>{ax}</option>
                    ))}
                  </select>
                </label>
                <label>
                  <span className="field-label">Front Axis</span>
                  <select
                    value={rule.frontAxis}
                    onChange={(e) => updateClothCache(rule.id, { frontAxis: e.target.value }, true)}
                  >
                    {["preserve", "Y", "-Y", "Z", "-Z", "X", "-X"].map((ax) => (
                      <option key={ax} value={ax}>{ax}</option>
                    ))}
                  </select>
                </label>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px" }}>
                <label>
                  <span className="field-label">Uniform Scale</span>
                  <NumberInput
                    value={rule.scale}
                    min={0.0001}
                    step={0.01}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      if (Number.isFinite(v) && v > 0) updateClothCache(rule.id, { scale: v });
                    }}
                  />
                </label>
                <label>
                  <span className="field-label">Time Offset</span>
                  <NumberInput
                    value={rule.timeOffset}
                    step={1}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      if (Number.isFinite(v)) updateClothCache(rule.id, { timeOffset: v });
                    }}
                  />
                </label>
              </div>
            </div>
          );
        })}
      </div>

      {/* Section 5: Head Grooms */}
      <div style={{ borderTop: "1px solid rgba(255,255,255,0.12)", paddingTop: "8px", display: "flex", flexDirection: "column", gap: "6px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span className="field-label">Head Grooms</span>
          <button type="button" onClick={addHeadGroom} style={{ padding: "2px 7px", fontSize: "11px" }}>
            + Add Groom
          </button>
        </div>
        {draft.headGrooms.length === 0 && (
          <p className="hint" style={{ margin: 0 }}>
            Attach external .abc/.usd grooms or connected Hair/Source curves to the head joint or head mesh surface.
          </p>
        )}
        {draft.headGrooms.map((rule) => {
          const dep = rule.dependencyId
            ? dependencies.find((d) => d.id === rule.dependencyId)
            : undefined;
          const joints = resolveJointsForSkeleton(rule.skeletonPath);
          const scannedMeshes = (stagePrims || []).filter((p) => p.isMesh);
          const scannedCurves = (stagePrims || []).filter((p) => p.isCurves);
          const matchedGroomPrims =
            rule.sourceMode === "upstream" && stagePrims && rule.targetQuery.trim()
              ? filterOutermostStagePrims(
                  stagePrims.filter((p) => characterQueryMatches(rule.targetQuery, p.path))
                )
              : null;
          return (
            <div
              key={rule.id}
              style={{
                padding: "6px",
                borderRadius: "4px",
                border: "1px solid rgba(255,255,255,0.12)",
                background: "rgba(0,0,0,0.16)",
                display: "flex",
                flexDirection: "column",
                gap: "5px",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "6px" }}>
                <label style={{ display: "flex", alignItems: "center", gap: "5px", fontSize: "11px", margin: 0 }}>
                  <input
                    type="checkbox"
                    checked={rule.enabled}
                    onChange={(e) => updateHeadGroom(rule.id, { enabled: e.target.checked }, true)}
                  />
                  Enabled
                </label>
                <div style={{ display: "flex", gap: "4px", alignItems: "center" }}>
                  {rule.attachMode === "joint" && onToggleGroomGizmo && (
                    <button
                      type="button"
                      disabled={!rule.enabled}
                      onClick={() => onToggleGroomGizmo(rule.id)}
                      title="Toggle 3D viewport gizmo (Q/W/E/R) to position this groom"
                      style={{
                        padding: "1px 6px",
                        fontSize: "11px",
                        ...(activeGroomGizmoId === rule.id
                          ? {
                              background: "rgba(99, 102, 241, 0.45)",
                              borderColor: "rgba(129, 140, 248, 0.9)",
                            }
                          : {}),
                      }}
                    >
                      {activeGroomGizmoId === rule.id ? "Gizmo On" : "Gizmo"}
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => removeHeadGroom(rule.id)}
                    style={{ padding: "1px 6px", fontSize: "11px" }}
                  >
                    Remove
                  </button>
                </div>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px" }}>
                <label>
                  <span className="field-label">Source</span>
                  <select
                    value={rule.sourceMode}
                    onChange={(e) =>
                      updateHeadGroom(rule.id, { sourceMode: e.target.value as "file" | "upstream" }, true)
                    }
                  >
                    <option value="file">External File</option>
                    <option value="upstream">Connected Input</option>
                  </select>
                </label>
                <label>
                  <span className="field-label">Attach Mode</span>
                  <select
                    value={rule.attachMode}
                    onChange={(e) =>
                      updateHeadGroom(rule.id, { attachMode: e.target.value as "joint" | "surface" }, true)
                    }
                  >
                    <option value="joint">Head Joint (Rigid)</option>
                    <option value="surface">Head Mesh (Surface)</option>
                  </select>
                </label>
              </div>

              {rule.sourceMode === "file" ? (
                <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                  <button
                    type="button"
                    onClick={() => browseHeadGroom(rule.id)}
                    style={{ padding: "3px 8px", fontSize: "11px" }}
                  >
                    Browse Groom...
                  </button>
                  <span
                    style={{
                      fontSize: "11px",
                      opacity: 0.85,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      flex: 1,
                    }}
                    title={dep?.path || ""}
                  >
                    {dep ? dep.path.split(/[\\/]/).pop() : "No groom file linked"}
                  </span>
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                  <label>
                    <span className="field-label">Groom Prim / Wildcard</span>
                    <input
                      type="text"
                      list={`groom-curves-${rule.id}`}
                      value={rule.targetQuery}
                      onChange={(e) => updateHeadGroom(rule.id, { targetQuery: e.target.value })}
                      placeholder="*Hair*, *Groom*, *Eyebrows*, /Scene/..."
                    />
                    {scannedCurves.length > 0 && (
                      <datalist id={`groom-curves-${rule.id}`}>
                        {scannedCurves.map((c) => (
                          <option key={c.path} value={c.path}>{c.name}</option>
                        ))}
                      </datalist>
                    )}
                  </label>
                  {rule.targetQuery.trim() && (
                    stagePrims === null ? (
                      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "6px", fontSize: "11px", opacity: 0.75 }}>
                        <span>Scan to preview matched prims</span>
                        <button
                          type="button"
                          onClick={scanSkeletons}
                          disabled={!graph || scanning}
                          style={{ padding: "1px 6px", fontSize: "10px" }}
                        >
                          {scanning ? "Scanning..." : "Scan"}
                        </button>
                      </div>
                    ) : matchedGroomPrims && matchedGroomPrims.length > 0 ? (
                      <div style={{ fontSize: "11px", display: "flex", flexWrap: "wrap", gap: "4px", alignItems: "center" }}>
                        <span style={{ opacity: 0.75 }}>Matched ({matchedGroomPrims.length}):</span>
                        {matchedGroomPrims.slice(0, 6).map((mp) => (
                          <span
                            key={mp.path}
                            title={mp.path}
                            style={{
                              padding: "1px 5px",
                              borderRadius: "3px",
                              background: "rgba(99, 102, 241, 0.22)",
                              border: "1px solid rgba(129, 140, 248, 0.4)",
                              fontSize: "10px",
                              maxWidth: "100%",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            }}
                          >
                            {mp.name}
                          </span>
                        ))}
                        {matchedGroomPrims.length > 6 && (
                          <span style={{ opacity: 0.7, fontSize: "10px" }}>
                            +{matchedGroomPrims.length - 6} more
                          </span>
                        )}
                      </div>
                    ) : (
                      <span style={{ fontSize: "11px", color: "#fbbf24" }}>
                        No prims matched by query
                      </span>
                    )
                  )}
                </div>
              )}

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px" }}>
                <label>
                  <span className="field-label">Up Axis</span>
                  <select
                    value={rule.axis}
                    onChange={(e) => updateHeadGroom(rule.id, { axis: e.target.value }, true)}
                  >
                    {["preserve", "Y", "-Y", "Z", "-Z", "X", "-X"].map((ax) => (
                      <option key={ax} value={ax}>{ax}</option>
                    ))}
                  </select>
                </label>
                <label>
                  <span className="field-label">Front Axis</span>
                  <select
                    value={rule.frontAxis}
                    onChange={(e) => updateHeadGroom(rule.id, { frontAxis: e.target.value }, true)}
                  >
                    {["preserve", "Y", "-Y", "Z", "-Z", "X", "-X"].map((ax) => (
                      <option key={ax} value={ax}>{ax}</option>
                    ))}
                  </select>
                </label>
              </div>

              <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                <label>
                  <span className="field-label">Uniform Scale</span>
                  <NumberInput
                    value={rule.scale}
                    min={0.0001}
                    step={0.01}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      if (Number.isFinite(v) && v > 0) updateHeadGroom(rule.id, { scale: v });
                    }}
                  />
                </label>
                <div style={{ display: "flex", gap: "4px", flexWrap: "wrap" }}>
                  {GROOM_SCALE_PRESETS.map((preset) => {
                    const active = Math.abs(rule.scale - preset.value) < 1e-6;
                    return (
                      <button
                        key={preset.label}
                        type="button"
                        onClick={() => updateHeadGroom(rule.id, { scale: preset.value }, true)}
                        style={{
                          padding: "2px 6px",
                          fontSize: "10px",
                          flex: "1 1 auto",
                          ...(active
                            ? {
                                background: "rgba(99, 102, 241, 0.35)",
                                borderColor: "rgba(129, 140, 248, 0.8)",
                              }
                            : {}),
                        }}
                      >
                        {preset.label}
                      </button>
                    );
                  })}
                </div>
              </div>

              {rule.attachMode === "joint" ? (
                <>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px" }}>
                    <label>
                      <span className="field-label">Skeleton</span>
                      {skeletons && skeletons.length > 0 ? (
                        <select
                          value={rule.skeletonPath}
                          onChange={(e) => updateHeadGroom(rule.id, { skeletonPath: e.target.value }, true)}
                        >
                          <option value="">Default Base Skeleton</option>
                          {skeletons.map((s) => (
                            <option key={s.path} value={s.path}>{s.path}</option>
                          ))}
                        </select>
                      ) : (
                        <input
                          type="text"
                          value={rule.skeletonPath}
                          onChange={(e) => updateHeadGroom(rule.id, { skeletonPath: e.target.value })}
                          placeholder="Default Base Skeleton"
                        />
                      )}
                    </label>
                    <label>
                      <span className="field-label">Head Joint</span>
                      {joints.length > 0 ? (
                        <select
                          value={rule.headJointName}
                          onChange={(e) => updateHeadGroom(rule.id, { headJointName: e.target.value }, true)}
                        >
                          <option value="">Auto-detect head joint</option>
                          {joints.map((j) => (
                            <option key={j} value={j}>{j}</option>
                          ))}
                        </select>
                      ) : (
                        <input
                          type="text"
                          value={rule.headJointName}
                          onChange={(e) => updateHeadGroom(rule.id, { headJointName: e.target.value })}
                          placeholder="Auto-detect (head)"
                        />
                      )}
                    </label>
                  </div>
                  <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "11px", margin: 0 }}>
                    <input
                      type="checkbox"
                      checked={rule.preserveRestPosition}
                      onChange={(e) =>
                        updateHeadGroom(rule.id, { preserveRestPosition: e.target.checked }, true)
                      }
                    />
                    Preserve rest position relative to head
                  </label>
                </>
              ) : (
                <label>
                  <span className="field-label">Head Mesh (Surface Target)</span>
                  {scannedMeshes.length > 0 ? (
                    <select
                      value={rule.headMeshPath}
                      onChange={(e) => updateHeadGroom(rule.id, { headMeshPath: e.target.value }, true)}
                    >
                      <option value="">Auto-detect head mesh</option>
                      {scannedMeshes.map((m) => (
                        <option key={m.path} value={m.path}>{m.name} — {m.path}</option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type="text"
                      value={rule.headMeshPath}
                      onChange={(e) => updateHeadGroom(rule.id, { headMeshPath: e.target.value })}
                      placeholder="Auto-detect (or /Scene/.../head_lod0_mesh)"
                    />
                  )}
                </label>
              )}

              <details style={{ fontSize: "11px" }}>
                <summary style={{ cursor: "pointer", opacity: 0.8 }}>Offset Transform</summary>
                <div style={{ display: "flex", flexDirection: "column", gap: "3px", marginTop: "4px" }}>
                  <span className="field-label">Offset Translate (m)</span>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "4px" }}>
                    {[0, 1, 2].map((idx) => (
                      <NumberInput
                        key={idx}
                        value={rule.offsetTranslation[idx]}
                        step={0.005}
                        onChange={(e) => {
                          const v = Number(e.target.value);
                          if (!Number.isFinite(v)) return;
                          const next: [number, number, number] = [...rule.offsetTranslation] as [number, number, number];
                          next[idx] = v;
                          updateHeadGroom(rule.id, { offsetTranslation: next });
                        }}
                      />
                    ))}
                  </div>
                  <span className="field-label">Offset Rotate (deg)</span>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "4px" }}>
                    {[0, 1, 2].map((idx) => (
                      <NumberInput
                        key={idx}
                        value={rule.offsetRotation[idx]}
                        step={1}
                        onChange={(e) => {
                          const v = Number(e.target.value);
                          if (!Number.isFinite(v)) return;
                          const next: [number, number, number] = [...rule.offsetRotation] as [number, number, number];
                          next[idx] = v;
                          updateHeadGroom(rule.id, { offsetRotation: next });
                        }}
                      />
                    ))}
                  </div>
                  <span className="field-label">Uniform Offset Scale</span>
                  <NumberInput
                    value={rule.offsetScale[0]}
                    min={0.0001}
                    step={0.05}
                    onChange={(e) => {
                      const v = Number(e.target.value);
                      if (!Number.isFinite(v) || v <= 0) return;
                      updateHeadGroom(rule.id, { offsetScale: [v, v, v] });
                    }}
                  />
                </div>
              </details>
            </div>
          );
        })}
      </div>
    </div>
  );
}
