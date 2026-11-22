import { useEffect, useRef, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { open, save } from "@tauri-apps/plugin-dialog";
import type {
  Data,
  LiveAdjustmentResult,
  NativeCreationResult,
  StoredProject,
} from "./fileRegistry";

interface BridgeStatus {
  connected: boolean;
  port?: number;
  version?: string;
  projectOpen?: boolean;
  projectPath?: string;
  textureSets?: string[];
  saveCounter?: number;
  lastSavedAt?: number;
  error?: string;
}

function parentDirectory(filePath: string): string {
  const normalized = filePath.replace(/\\/g, "/");
  const idx = normalized.lastIndexOf("/");
  return idx > 0 ? normalized.slice(0, idx) : "";
}

export function SubstancePainterPanel({
  project,
  nodeId,
  data,
  onNativeCreated,
  onLive,
  onStatus,
}: {
  project: StoredProject;
  nodeId: string;
  data: Data;
  onNativeCreated: (result: NativeCreationResult) => StoredProject;
  onLive: (edited: string, result: LiveAdjustmentResult) => void;
  onStatus: (message: string) => void;
}) {
  const [bridge, setBridge] = useState<BridgeStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [autoSync, setAutoSync] = useState(false);

  const initialExportDir = (() => {
    const depId = data.liveAdjustment?.editedDependencyId;
    if (!depId) return "";
    const dep = project.dependencies.find((d) => d.id === depId);
    return dep?.path ? parentDirectory(dep.path) : "";
  })();
  const [exportDir, setExportDir] = useState<string>(initialExportDir);

  const lastSaveCounterRef = useRef<number | null>(null);
  const busyRef = useRef(false);
  busyRef.current = busy;

  useEffect(() => {
    if (!exportDir && initialExportDir) {
      setExportDir(initialExportDir);
    }
  }, [exportDir, initialExportDir]);

  async function refreshBridge(): Promise<BridgeStatus | null> {
    if (!isTauri()) return null;
    try {
      const status = await invoke<BridgeStatus>("sp_bridge_status");
      setBridge(status);
      if (typeof status.saveCounter === "number" && lastSaveCounterRef.current === null) {
        lastSaveCounterRef.current = status.saveCounter;
      }
      return status;
    } catch (error) {
      const failed: BridgeStatus = { connected: false, error: String(error) };
      setBridge(failed);
      return failed;
    }
  }

  useEffect(() => {
    void refreshBridge();
  }, [nodeId]);

  async function runAction(fn: () => Promise<void>) {
    setBusy(true);
    try {
      await fn();
    } catch (error) {
      const text = String(error);
      setMessage(text);
      onStatus(text);
    } finally {
      setBusy(false);
    }
  }

  async function syncToDir(targetDir: string) {
    setMessage("Exporting PBR textures from Substance Painter and building MaterialX layer...");
    onStatus("Syncing painted materials from Substance Painter...");
    const result = await invoke<
      LiveAdjustmentResult & { manifestPath: string; report?: { textureSetCount?: number; boundPrimCount?: number } }
    >("sync_sp_materials", {
      project,
      nodeId,
      exportDir: targetDir,
    });
    onLive(result.manifestPath, result);
    const count = result.report?.textureSetCount ?? 0;
    const bound = result.report?.boundPrimCount ?? 0;
    const text = `Synced ${count} texture set(s) onto ${bound} prim(s) as MaterialX + UsdPreviewSurface.`;
    setMessage(text);
    onStatus(text);
  }

  // Poll SP bridge when Auto-sync on SP Save is enabled
  useEffect(() => {
    if (!autoSync || !isTauri()) return;
    const timer = window.setInterval(async () => {
      if (busyRef.current) return;
      const status = await refreshBridge();
      if (!status?.connected || typeof status.saveCounter !== "number") return;
      if (lastSaveCounterRef.current === null) {
        lastSaveCounterRef.current = status.saveCounter;
        return;
      }
      if (status.saveCounter > lastSaveCounterRef.current) {
        lastSaveCounterRef.current = status.saveCounter;
        if (exportDir) {
          await runAction(() => syncToDir(exportDir));
        } else {
          setMessage("SP saved! Set an export folder once to enable auto-sync.");
        }
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [autoSync, exportDir, project, nodeId]);

  async function handleInstallPlugin() {
    await runAction(async () => {
      const res = await invoke<{ installed: boolean; path: string; port: number }>(
        "install_sp_plugin",
      );
      const text = `Installed Sublayer Bridge plugin to ${res.path}. Restart Substance Painter (or enable it under Python -> sublayer_bridge).`;
      setMessage(text);
      onStatus(text);
    });
  }

  async function handleCreateProject() {
    await runAction(async () => {
      const sppPath = await save({
        title: "Create Substance Painter Project",
        filters: [{ name: "Substance Painter Project", extensions: ["spp"] }],
      });
      if (!sppPath) return;
      setMessage("Creating Substance Painter project from handoff USD...");
      onStatus("Creating Substance Painter project...");
      const created = await invoke<NativeCreationResult>("create_sp_project", {
        project,
        nodeId,
        path: sppPath,
        overwrite: true,
      });
      onNativeCreated(created);
      await refreshBridge();
      const text = `Created and opened ${created.path} in Substance Painter.`;
      setMessage(text);
      onStatus(text);
    });
  }

  async function handleOpenProject() {
    if (!data.nativeProject) return;
    await runAction(async () => {
      await invoke("open_sp_project", { path: data.nativeProject });
      await refreshBridge();
      const text = `Requested Substance Painter open for ${data.nativeProject}.`;
      setMessage(text);
      onStatus(text);
    });
  }

  async function handleChooseExportFolder(): Promise<string | null> {
    const picked = await open({
      title: "Choose Texture Export Folder for Substance Painter",
      directory: true,
      multiple: false,
    });
    if (!picked || Array.isArray(picked)) return null;
    setExportDir(picked);
    return picked;
  }

  async function handleSyncMaterials() {
    await runAction(async () => {
      const dir = exportDir || (await handleChooseExportFolder());
      if (!dir) return;
      await syncToDir(dir);
    });
  }

  async function handleImportManifest() {
    await runAction(async () => {
      const manifestPath = await open({
        title: "Select sublayer_sp_manifest.json",
        filters: [{ name: "Sublayer SP Manifest", extensions: ["json"] }],
        multiple: false,
      });
      if (!manifestPath || Array.isArray(manifestPath)) return;
      const dir = parentDirectory(manifestPath);
      if (dir) setExportDir(dir);
      setMessage("Building MaterialX + UsdPreviewSurface layer from SP manifest...");
      const result = await invoke<
        LiveAdjustmentResult & { manifestPath: string; report?: { textureSetCount?: number; boundPrimCount?: number } }
      >("sync_sp_materials", {
        project,
        nodeId,
        manifestPath,
      });
      onLive(result.manifestPath, result);
      const count = result.report?.textureSetCount ?? 0;
      const bound = result.report?.boundPrimCount ?? 0;
      const text = `Imported ${count} texture set(s) onto ${bound} prim(s) from manifest.`;
      setMessage(text);
      onStatus(text);
    });
  }

  return (
    <div style={{ display: "grid", gap: 8, marginTop: 8, paddingTop: 8, borderTop: "1px solid var(--border)" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
        <strong style={{ fontSize: 12 }}>Substance Painter 2023 Bridge</strong>
        <button type="button" disabled={busy || !isTauri()} onClick={() => void refreshBridge()}>
          Refresh
        </button>
      </div>

      <div style={{ fontSize: 11, opacity: 0.85 }}>
        {bridge?.connected
          ? `Connected · SP ${bridge.version || "?"}${bridge.projectOpen ? ` · ${bridge.textureSets?.length ?? 0} texture set(s)` : " · No project open"}`
          : `Not connected (127.0.0.1:${bridge?.port ?? 29347})`}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        <button type="button" disabled={busy || !isTauri()} onClick={() => void handleInstallPlugin()}>
          Install SP Plugin
        </button>
        <button
          type="button"
          disabled={busy || !isTauri() || !data.handoff || Boolean(data.nativeProject)}
          title={!data.handoff ? "Create a handoff USD first." : data.nativeProject ? "A .spp project is already linked." : undefined}
          onClick={() => void handleCreateProject()}
        >
          Create .spp in SP
        </button>
        {data.nativeProject && (
          <button type="button" disabled={busy || !isTauri()} onClick={() => void handleOpenProject()}>
            Open in SP
          </button>
        )}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        <button
          type="button"
          disabled={busy || !isTauri() || !bridge?.connected}
          title={!bridge?.connected ? "Connect to Substance Painter bridge first." : exportDir ? `Sync to ${exportDir}` : "Choose export folder and sync"}
          onClick={() => void handleSyncMaterials()}
        >
          Sync Materials from SP
        </button>
        <button
          type="button"
          disabled={busy || !isTauri()}
          title="Change texture export folder"
          onClick={() => void handleChooseExportFolder()}
        >
          Folder…
        </button>
        <button type="button" disabled={busy || !isTauri()} onClick={() => void handleImportManifest()}>
          Import SP Manifest
        </button>
      </div>

      {exportDir && (
        <div style={{ fontSize: 10, opacity: 0.75, wordBreak: "break-all" }}>
          Export folder: {exportDir}
        </div>
      )}

      <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, cursor: "pointer" }}>
        <input
          type="checkbox"
          style={{ width: "auto", margin: 0 }}
          checked={autoSync}
          disabled={!isTauri()}
          onChange={(e) => setAutoSync(e.target.checked)}
        />
        Auto-sync when SP saves (Ctrl+S)
      </label>

      {message && (
        <div style={{ fontSize: 11, opacity: 0.9, wordBreak: "break-word" }}>
          {message}
        </div>
      )}
    </div>
  );
}
