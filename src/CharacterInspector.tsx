import { useEffect, useRef, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import {
  defaultCharacterSettings,
  registerFiles,
  type CharacterAnimationOverride,
  type CharacterClothCache,
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

function makeRuleId(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
}

export function CharacterInspector({
  value,
  graph,
  dependencies,
  onChange,
  onStatus,
}: {
  value: CharacterSettings | null | undefined;
  graph: string | null;
  dependencies: Dependency[];
  onChange: (next: CharacterSettings, nextDependencies?: Dependency[]) => void;
  onStatus?: (msg: string) => void;
}) {
  const cfg: CharacterSettings = {
    ...defaultCharacterSettings,
    ...(value || {}),
    animationOverrides: value?.animationOverrides || [],
    jointAttachments: value?.jointAttachments || [],
    skeletonBindings: value?.skeletonBindings || [],
    clothCaches: value?.clothCaches || [],
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
      }>("usd_request", {
        request: { operation: "inspect_stage_skeletons", graph: JSON.parse(graph) },
      });
      setSkeletons(res?.skeletons || []);
      setAnimations(res?.animations || []);
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
    </div>
  );
}
