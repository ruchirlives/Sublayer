import { useEffect, useMemo, useRef, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";

export type MegascansConfig = {
  libraryRoot: string;
  downloadsDirs: string[];
  socketEnabled: boolean;
  socketPort: number;
};

export type MegascansAssetSummary = {
  id: string;
  listingUid?: string;
  name: string;
  assetType: string;
  folderPath: string;
  primaryPath: string;
  previewImagePath?: string;
  tags: string[];
  availableLods: number[];
  availableResolutions: string[];
  meshFormats: string[];
  pbrMaps: Record<string, string>;
};

export type FabDropOutcome = {
  status: "ready" | "watching";
  dropId: string;
  title: string;
  url: string;
  asset?: MegascansAssetSummary;
  message: string;
};

const thumbCache = new Map<string, string>();

function AssetCardThumb({ path, name, assetType }: { path?: string; name: string; assetType: string }) {
  const [src, setSrc] = useState<string>(() => (path ? thumbCache.get(path) || "" : ""));

  useEffect(() => {
    if (!path || !isTauri()) return;
    const cached = thumbCache.get(path);
    if (cached) {
      setSrc(cached);
      return;
    }
    let cancelled = false;
    invoke<string>("read_megascans_thumbnail", { path })
      .then((uri) => {
        thumbCache.set(path, uri);
        if (!cancelled) setSrc(uri);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [path]);

  if (src) {
    return <img className="megascans-card-img" src={src} alt={name} loading="lazy" draggable={false} />;
  }
  const icon = assetType === "3dplant" ? "🌿" : assetType === "surface" || assetType === "atlas" ? "◉" : "◈";
  return (
    <div className="megascans-card-placeholder" aria-hidden="true">
      <span>{icon}</span>
    </div>
  );
}

export function MegascansPanel({
  onAddAsset,
  onDragStartCanvasMode,
  onDragEndCanvas,
  onStatus,
  onClose,
  onSettings,
}: {
  onAddAsset: (asset: MegascansAssetSummary) => void;
  onDragStartCanvasMode: () => void;
  onDragEndCanvas: () => void;
  onStatus: (message: string) => void;
  onClose: () => void;
  onSettings: () => void;
}) {
  const [config, setConfig] = useState<MegascansConfig>({
    libraryRoot: "",
    downloadsDirs: [],
    socketEnabled: true,
    socketPort: 24981,
  });
  const [assets, setAssets] = useState<MegascansAssetSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<string>("all");
  const [visibleLimit, setVisibleLimit] = useState(60);


  const [pos, setPos] = useState({ x: 96, y: 76 });
  const dragRef = useRef<{ startX: number; startY: number; origX: number; origY: number } | null>(null);

  async function loadAll(overrideRoot?: string) {
    if (!isTauri()) return;
    setLoading(true);
    try {
      const cfg = await invoke<MegascansConfig>("load_megascans_config");
      setConfig(cfg);
      const rootToScan = overrideRoot ?? cfg.libraryRoot;
      if (rootToScan.trim()) {
        const scanned = await invoke<MegascansAssetSummary[]>("scan_megascans_library", {
          libraryRoot: rootToScan,
        });
        setAssets(scanned);
      } else {
        setAssets([]);
        onStatus("Set your library folder in Applications.");
      }
    } catch (e) {
      onStatus(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadAll();
  }, []);

  useEffect(() => {
    setVisibleLimit(60);
  }, [query, category]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: assets.length, "3d": 0, "3dplant": 0, surface: 0, atlas: 0, fab: 0 };
    for (const a of assets) {
      c[a.assetType] = (c[a.assetType] || 0) + 1;
    }
    return c;
  }, [assets]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return assets.filter((a) => {
      if (category !== "all" && a.assetType !== category) return false;
      if (!q) return true;
      return (
        a.name.toLowerCase().includes(q) ||
        a.id.toLowerCase().includes(q) ||
        a.tags.some((t) => t.toLowerCase().includes(q)) ||
        a.meshFormats.some((f) => f.toLowerCase().includes(q))
      );
    });
  }, [assets, query, category]);

  async function importArchiveOrFile() {
    if (!isTauri()) return;
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const chosen = await open({
        multiple: false,
        filters: [
          {
            name: "Fab / Megascans Asset or ZIP",
            extensions: ["zip", "glb", "gltf", "fbx", "usdz", "usd", "usda", "usdc", "abc", "obj", "json"],
          },
        ],
      });
      if (typeof chosen === "string" && chosen.trim()) {
        setLoading(true);
        const summary = await invoke<MegascansAssetSummary>("inspect_megascans_path", { path: chosen });
        onAddAsset(summary);
        await loadAll();
      }
    } catch (e) {
      onStatus(String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      className="megascans-panel"
      style={{ left: `${pos.x}px`, top: `${pos.y}px` }}
      role="dialog"
      aria-label="Megascans and Fab Library"
    >
      <div
        className="megascans-panel-header"
        onPointerDown={(e) => {
          if ((e.target as HTMLElement).closest("button,input,select")) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          dragRef.current = { startX: e.clientX, startY: e.clientY, origX: pos.x, origY: pos.y };
        }}
        onPointerMove={(e) => {
          if (!dragRef.current) return;
          setPos({
            x: Math.max(8, dragRef.current.origX + (e.clientX - dragRef.current.startX)),
            y: Math.max(44, dragRef.current.origY + (e.clientY - dragRef.current.startY)),
          });
        }}
        onPointerUp={() => {
          dragRef.current = null;
        }}
      >
        <div>
          <strong>Megascans &amp; Fab Library</strong>
          <small className="megascans-subtitle">
            Drag cards or <code>fab.com</code> browser links onto the canvas — files stay in{" "}
            <code>{config.libraryRoot || "central folder"}</code>
          </small>
        </div>
        <div className="megascans-header-actions">
          <button type="button" disabled={loading || !isTauri()} onClick={() => void importArchiveOrFile()}>
            Import ZIP / GLB / FBX…
          </button>
          <button type="button" onClick={onSettings}>
            Settings
          </button>
          <button type="button" disabled={loading || !isTauri()} onClick={() => void loadAll()}>
            Rescan
          </button>
          <button type="button" onClick={onClose} aria-label="Close Megascans panel">
            ✕
          </button>
        </div>
      </div>

      <div className="megascans-toolbar">
        <input
          type="search"
          className="megascans-search"
          placeholder="Search rocks, plants, surfaces, IDs, tags, FBX / ABC / GLB…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="megascans-tabs" role="tablist">
          {(
            [
              ["all", "All"],
              ["3d", "3D"],
              ["3dplant", "3D Plants"],
              ["surface", "Surfaces"],
              ["atlas", "Atlases"],
              ["fab", "Fab"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={category === key ? "chosen" : undefined}
              onClick={() => setCategory(key)}
            >
              {label} ({counts[key] || 0})
            </button>
          ))}
        </div>
      </div>

      <div className="megascans-grid">
        {loading && <p className="hint">Scanning central library…</p>}
        {!loading && filtered.length === 0 && (
          <p className="hint">
            {!config.libraryRoot ? "Set your library folder in Applications." : <>No matching assets found in <code>{config.libraryRoot}</code>.</>} Drag any listing card from{" "}
            <code>fab.com</code> onto the Sublayer canvas and click Download in your browser to add it automatically.
          </p>
        )}
        {filtered.slice(0, visibleLimit).map((asset) => {
          const topRes = asset.availableResolutions[0];
          const fmtBadge = asset.meshFormats[0]?.toUpperCase() || "PBR";
          const lodBadge =
            asset.availableLods.length > 1
              ? `LOD0–${asset.availableLods[asset.availableLods.length - 1]}`
              : asset.availableLods.length === 1
                ? `LOD${asset.availableLods[0]}`
                : "";
          return (
            <div
              key={`${asset.folderPath}:${asset.id}`}
              className="megascans-card"
              draggable
              title={`${asset.name}\n${asset.folderPath}\nDrag onto the canvas (or onto an edge/node) or click + Add`}
              onDragStart={(e) => {
                onDragStartCanvasMode();
                e.dataTransfer.setData("application/orchflow-megascans", JSON.stringify(asset));
                e.dataTransfer.effectAllowed = "copy";
              }}
              onDragEnd={onDragEndCanvas}
            >
              <AssetCardThumb path={asset.previewImagePath} name={asset.name} assetType={asset.assetType} />
              <div className="megascans-card-body">
                <div className="megascans-card-title">{asset.name}</div>
                <div className="megascans-card-badges">
                  <span>{asset.assetType === "3dplant" ? "Plant" : asset.assetType}</span>
                  <span>{fmtBadge}</span>
                  {topRes && <span>{topRes}</span>}
                  {lodBadge && <span>{lodBadge}</span>}
                </div>
                <button type="button" className="megascans-add-btn" onClick={() => onAddAsset(asset)}>
                  ＋ Add
                </button>
              </div>
            </div>
          );
        })}
        {filtered.length > visibleLimit && (
          <button
            type="button"
            className="megascans-load-more"
            onClick={() => setVisibleLimit((n) => n + 60)}
          >
            Show more ({filtered.length - visibleLimit} remaining)
          </button>
        )}
      </div>
    </div>
  );
}
