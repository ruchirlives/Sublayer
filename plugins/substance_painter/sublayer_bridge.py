"""Sublayer Bridge plugin for Adobe Substance 3D Painter 2023+.

Exposes a local loopback HTTP/JSON bridge (127.0.0.1:29347 by default,
overridable via SUBLAYER_SP_PORT) so Sublayer's Application node can:
  - Check Substance Painter status and open project / texture sets
  - Create and save a new .spp project from a Sublayer handoff USD
  - Open an existing .spp project
  - Export PBR Metallic-Roughness textures + a sublayer_sp_manifest.json
    for live MaterialX + UsdPreviewSurface authoring in Sublayer.

Install location (Windows):
  %USERPROFILE%\\Documents\\Adobe\\Adobe Substance 3D Painter\\python\\plugins\\sublayer_bridge.py
Or load automatically by setting SUBSTANCE_PAINTER_PLUGINS_PATH to this directory.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

try:
    import substance_painter.application as sp_app
    import substance_painter.event as sp_event
    import substance_painter.export as sp_export
    import substance_painter.logging as sp_log
    import substance_painter.project as sp_project
    import substance_painter.textureset as sp_textureset
    import substance_painter.ui as sp_ui
except ImportError:  # Allows importing outside SP for unit tests
    sp_app = None
    sp_event = None
    sp_export = None
    sp_log = None
    sp_project = None
    sp_textureset = None
    sp_ui = None

try:
    from PySide2 import QtCore, QtWidgets
except ImportError:
    try:
        from PySide6 import QtCore, QtWidgets  # SP 2024+ fallback
    except ImportError:
        QtCore = None
        QtWidgets = None


DEFAULT_PORT = int(os.environ.get("SUBLAYER_SP_PORT", "29347"))
UDIM_RE = re.compile(r"([._])(1\d{3})(\.[^.]+)$")

_server: ThreadingHTTPServer | None = None
_server_thread: threading.Thread | None = None
_plugin_widgets: list[Any] = []
_dispatcher: Any = None
_save_counter: int = 0
_last_saved_at: float = 0.0


def _on_project_saved(_event: Any) -> None:
    global _save_counter, _last_saved_at
    _save_counter += 1
    _last_saved_at = time.time()


if QtCore is not None:
    class _MainThreadDispatcher(QtCore.QObject):
        _invoke = QtCore.Signal(object)

        def __init__(self) -> None:
            super().__init__()
            self._invoke.connect(self._execute, QtCore.Qt.QueuedConnection)

        @QtCore.Slot(object)
        def _execute(self, fn: Callable[[], None]) -> None:
            fn()

        def dispatch(self, fn: Callable[[], None]) -> None:
            self._invoke.emit(fn)
else:
    _MainThreadDispatcher = None


def _log(message: str, warning: bool = False) -> None:
    if sp_log is not None:
        severity = sp_log.WARNING if warning else sp_log.INFO
        sp_log.log(severity, "SublayerBridge", message)
    else:
        print(f"[SublayerBridge] {message}")


def _run_on_main_thread(fn: Callable[[], Any], timeout: float = 300.0) -> Any:
    """Marshal a callable onto Substance Painter's Qt main thread."""
    if _dispatcher is None or QtWidgets is None or QtWidgets.QApplication.instance() is None:
        return fn()

    done = threading.Event()
    box: dict[str, Any] = {}

    def _wrapped() -> None:
        try:
            box["result"] = fn()
        except Exception as exc:  # noqa: BLE001
            box["error"] = f"{exc}\n{traceback.format_exc()}"
        finally:
            done.set()

    _dispatcher.dispatch(_wrapped)
    if not done.wait(timeout):
        raise TimeoutError(f"Timed out waiting for Substance Painter main thread ({timeout}s)")
    if "error" in box:
        raise RuntimeError(box["error"])
    return box.get("result")


def _sp_version() -> str:
    if sp_app is None:
        return "unknown"
    try:
        return str(sp_app.version())
    except Exception:
        return "unknown"


def _list_texture_sets() -> list[str]:
    if sp_project is None or not sp_project.is_open():
        return []
    return [ts.name() for ts in sp_textureset.all_texture_sets()]


def _normalize_udim_path(paths: list[str]) -> tuple[str, bool]:
    """Collapse UDIM tile paths into a single <UDIM> pattern when applicable."""
    if not paths:
        return "", False
    normalized = [os.path.abspath(p).replace("\\", "/") for p in sorted(paths)]
    if len(normalized) == 1:
        m = UDIM_RE.search(normalized[0])
        if m:
            return UDIM_RE.sub(r"\1<UDIM>\3", normalized[0]), True
        return normalized[0], False
    udim_patterns = set()
    for p in normalized:
        if UDIM_RE.search(p):
            udim_patterns.add(UDIM_RE.sub(r"\1<UDIM>\3", p))
    if len(udim_patterns) == 1:
        return next(iter(udim_patterns)), True
    return normalized[0], False


def _classify_channel(file_path: str) -> str | None:
    stem = Path(file_path).stem
    # Strip trailing .1001 or _1001 UDIM suffix before matching channel token
    stem = re.sub(r"[._]1\d{3}$", "", stem)
    lower = stem.lower()
    for token, channel in (
        ("_basecolor", "baseColor"),
        ("_roughness", "roughness"),
        ("_metallic", "metallic"),
        ("_normal", "normal"),
        ("_opacity", "opacity"),
        ("_emissive", "emissive"),
        ("_height", "height"),
    ):
        if lower.endswith(token):
            return channel
    return None


def _build_export_config(
    export_dir: str,
    file_format: str = "png",
    bit_depth: str = "8",
    size_log2: int | None = None,
    padding: str = "infinite",
    rev_tag: str = "",
) -> dict[str, Any]:
    """Build a standard PBR Metallic-Roughness (OpenGL Normal) export config."""
    preset_name = "Sublayer_PBR_MetalRough"
    prefix = f"$textureSet_{rev_tag}_" if rev_tag else "$textureSet_"
    maps = [
        {
            "fileName": f"{prefix}BaseColor(.$udim)",
            "channels": [
                {"destChannel": "R", "srcChannel": "R", "srcMapType": "documentMap", "srcMapName": "baseColor"},
                {"destChannel": "G", "srcChannel": "G", "srcMapType": "documentMap", "srcMapName": "baseColor"},
                {"destChannel": "B", "srcChannel": "B", "srcMapType": "documentMap", "srcMapName": "baseColor"},
            ],
            "parameters": {"fileFormat": file_format, "bitDepth": bit_depth, "dithering": True, "paddingAlgorithm": padding},
        },
        {
            "fileName": f"{prefix}Roughness(.$udim)",
            "channels": [
                {"destChannel": "L", "srcChannel": "L", "srcMapType": "documentMap", "srcMapName": "roughness"},
            ],
            "parameters": {"fileFormat": file_format, "bitDepth": bit_depth, "dithering": False, "paddingAlgorithm": padding},
        },
        {
            "fileName": f"{prefix}Metallic(.$udim)",
            "channels": [
                {"destChannel": "L", "srcChannel": "L", "srcMapType": "documentMap", "srcMapName": "metallic"},
            ],
            "parameters": {"fileFormat": file_format, "bitDepth": bit_depth, "dithering": False, "paddingAlgorithm": padding},
        },
        {
            "fileName": f"{prefix}Normal(.$udim)",
            "channels": [
                {"destChannel": "R", "srcChannel": "R", "srcMapType": "virtualMap", "srcMapName": "Normal_OpenGL"},
                {"destChannel": "G", "srcChannel": "G", "srcMapType": "virtualMap", "srcMapName": "Normal_OpenGL"},
                {"destChannel": "B", "srcChannel": "B", "srcMapType": "virtualMap", "srcMapName": "Normal_OpenGL"},
            ],
            "parameters": {"fileFormat": file_format, "bitDepth": "16" if file_format == "png" else bit_depth, "dithering": False, "paddingAlgorithm": padding},
        },
    ]
    has_opacity = False
    has_emissive = False
    if sp_textureset is not None and sp_project is not None and sp_project.is_open():
        for ts in sp_textureset.all_texture_sets():
            stack = ts.get_stack()
            if stack.has_channel(sp_textureset.ChannelType.Opacity):
                has_opacity = True
            if stack.has_channel(sp_textureset.ChannelType.Emissive):
                has_emissive = True

    if has_opacity:
        maps.append(
            {
                "fileName": f"{prefix}Opacity(.$udim)",
                "channels": [
                    {"destChannel": "L", "srcChannel": "L", "srcMapType": "documentMap", "srcMapName": "opacity"},
                ],
                "parameters": {"fileFormat": file_format, "bitDepth": bit_depth, "dithering": False, "paddingAlgorithm": padding},
            }
        )
    if has_emissive:
        maps.append(
            {
                "fileName": f"{prefix}Emissive(.$udim)",
                "channels": [
                    {"destChannel": "R", "srcChannel": "R", "srcMapType": "documentMap", "srcMapName": "emissive"},
                    {"destChannel": "G", "srcChannel": "G", "srcMapType": "documentMap", "srcMapName": "emissive"},
                    {"destChannel": "B", "srcChannel": "B", "srcMapType": "documentMap", "srcMapName": "emissive"},
                ],
                "parameters": {"fileFormat": file_format, "bitDepth": bit_depth, "dithering": False, "paddingAlgorithm": padding},
            }
        )

    export_list = [{"rootPath": ts} for ts in _list_texture_sets()]
    default_params: dict[str, Any] = {
        "fileFormat": file_format,
        "bitDepth": bit_depth,
        "dithering": False,
        "paddingAlgorithm": padding,
    }
    if size_log2 is not None:
        default_params["sizeLog2"] = int(size_log2)

    return {
        "exportShaderParams": False,
        "exportPath": export_dir,
        "defaultExportPreset": preset_name,
        "exportPresets": [{"name": preset_name, "maps": maps}],
        "exportList": export_list,
        "exportParameters": [{"parameters": default_params}],
    }


def _handle_health() -> dict[str, Any]:
    is_open = bool(sp_project and sp_project.is_open())
    project_path = ""
    if is_open:
        try:
            project_path = sp_project.file_path() or ""
        except Exception:
            project_path = ""
    return {
        "ok": True,
        "app": "Substance 3D Painter",
        "version": _sp_version(),
        "projectOpen": is_open,
        "projectPath": project_path,
        "textureSets": _list_texture_sets(),
        "saveCounter": _save_counter,
        "lastSavedAt": _last_saved_at,
    }


def _handle_project_create_start(payload: dict[str, Any]) -> None:
    mesh_path = str(payload.get("meshPath") or "").strip()
    spp_path = str(payload.get("sppPath") or "").strip()
    if not mesh_path or not os.path.isfile(mesh_path):
        raise ValueError(f"Handoff mesh file not found: {mesh_path}")
    if not spp_path:
        raise ValueError("sppPath is required")

    if sp_project.is_open():
        if sp_project.needs_saving():
            raise RuntimeError(
                "Substance Painter has unsaved changes in the currently open project. "
                "Please save or close it before creating a new project from Sublayer."
            )
        sp_project.close()

    normal_format = str(payload.get("normalMapFormat") or "OpenGL").lower()
    sp_normal = (
        sp_project.NormalMapFormat.DirectX
        if normal_format == "directx"
        else sp_project.NormalMapFormat.OpenGL
    )
    settings = sp_project.Settings(
        normal_map_format=sp_normal,
        import_cameras=bool(payload.get("importCameras", False)),
    )
    if payload.get("templatePath"):
        template = str(payload["templatePath"])
        if os.path.isfile(template):
            sp_project.create(mesh_file_path=mesh_path, template_file_path=template, settings=settings)
        else:
            sp_project.create(mesh_file_path=mesh_path, settings=settings)
    else:
        sp_project.create(mesh_file_path=mesh_path, settings=settings)


def _handle_project_create_save(spp_path: str) -> dict[str, Any]:
    os.makedirs(os.path.dirname(os.path.abspath(spp_path)), exist_ok=True)
    sp_project.save_as(spp_path)
    return {
        "ok": True,
        "sppPath": os.path.abspath(spp_path),
        "version": _sp_version(),
        "textureSets": _list_texture_sets(),
    }


def _wait_until_not_busy(timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        busy = _run_on_main_thread(lambda: bool(sp_project.is_busy()))
        if not busy:
            return
        time.sleep(0.25)
    raise TimeoutError("Timed out waiting for Substance Painter to finish loading the project.")


def _handle_project_create(payload: dict[str, Any]) -> dict[str, Any]:
    spp_path = str(payload.get("sppPath") or "").strip()
    _run_on_main_thread(lambda: _handle_project_create_start(payload))
    _wait_until_not_busy(120.0)
    return _run_on_main_thread(lambda: _handle_project_create_save(spp_path))


def _handle_project_open(payload: dict[str, Any]) -> dict[str, Any]:
    spp_path = str(payload.get("sppPath") or "").strip()
    if not spp_path or not os.path.isfile(spp_path):
        raise ValueError(f"Substance Painter project not found: {spp_path}")

    if sp_project.is_open():
        current = sp_project.file_path() or ""
        if current and os.path.normcase(os.path.abspath(current)) == os.path.normcase(os.path.abspath(spp_path)):
            return {
                "ok": True,
                "sppPath": os.path.abspath(spp_path),
                "version": _sp_version(),
                "textureSets": _list_texture_sets(),
                "alreadyOpen": True,
            }
        if sp_project.needs_saving():
            raise RuntimeError(
                "Substance Painter has unsaved changes in the currently open project. "
                "Please save or close it before opening another project."
            )
        sp_project.close()

    sp_project.open(spp_path)
    return {
        "ok": True,
        "sppPath": os.path.abspath(spp_path),
        "version": _sp_version(),
        "textureSets": _list_texture_sets(),
        "alreadyOpen": False,
    }


def _handle_export_textures(payload: dict[str, Any]) -> dict[str, Any]:
    if not sp_project.is_open():
        raise RuntimeError("No Substance Painter project is currently open.")

    export_dir = str(payload.get("exportDir") or "").strip()
    if not export_dir:
        raise ValueError("exportDir is required")
    os.makedirs(export_dir, exist_ok=True)

    manifest_path = os.path.join(export_dir, "sublayer_sp_manifest.json")
    prev_files: set[str] = set()
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as prev_handle:
                prev_manifest = json.load(prev_handle)
            for fp in prev_manifest.get("exportedFiles") or []:
                prev_files.add(os.path.normcase(os.path.abspath(str(fp))))
        except Exception:
            pass

    rev_tag = f"r{int(time.time())}"
    config = _build_export_config(
        export_dir=export_dir,
        file_format=str(payload.get("fileFormat") or "png"),
        bit_depth=str(payload.get("bitDepth") or "8"),
        size_log2=payload.get("sizeLog2"),
        padding=str(payload.get("padding") or "infinite"),
        rev_tag=rev_tag,
    )
    result = sp_export.export_project_textures(config)
    if result.status != sp_export.ExportStatus.Success:
        raise RuntimeError(f"Substance Painter texture export failed: {result.message}")

    texture_sets: dict[str, dict[str, Any]] = {}
    all_files: list[str] = []
    for (ts_name, _stack_name), file_paths in result.textures.items():
        grouped: dict[str, list[str]] = {}
        for fp in file_paths:
            abs_fp = os.path.abspath(fp).replace("\\", "/")
            all_files.append(abs_fp)
            ch = _classify_channel(abs_fp)
            if ch:
                grouped.setdefault(ch, []).append(abs_fp)

        channels: dict[str, str] = {}
        is_udim = False
        for ch, fps in grouped.items():
            pattern, udim = _normalize_udim_path(fps)
            channels[ch] = pattern
            is_udim = is_udim or udim

        texture_sets[ts_name] = {
            "channels": channels,
            "udim": is_udim,
            "files": sorted(set(os.path.abspath(fp).replace("\\", "/") for fp in file_paths)),
        }

    manifest = {
        "schema": "sublayer.sp.manifest.v1",
        "app": "Substance 3D Painter",
        "version": _sp_version(),
        "projectPath": (sp_project.file_path() or "").replace("\\", "/"),
        "exportDir": os.path.abspath(export_dir).replace("\\", "/"),
        "textureSets": texture_sets,
        "exportedFiles": sorted(set(all_files)),
    }
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)

    # Clean up superseded PNGs from the previous sync so the folder stays tidy
    new_files = {os.path.normcase(os.path.abspath(fp)) for fp in all_files}
    for old_fp in prev_files - new_files:
        try:
            if os.path.isfile(old_fp):
                os.remove(old_fp)
        except Exception:
            pass

    return {
        "ok": True,
        "manifestPath": os.path.abspath(manifest_path).replace("\\", "/"),
        "manifest": manifest,
    }


class _BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _send_json(self, code: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        data = self.rfile.read(length)
        return json.loads(data.decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            try:
                self._send_json(200, _run_on_main_thread(_handle_health))
            except Exception as exc:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": str(exc)})
            return
        self._send_json(404, {"ok": False, "error": f"Unknown endpoint: {self.path}"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            payload = self._read_json()
            if self.path == "/project/create":
                res = _handle_project_create(payload)
            elif self.path == "/project/open":
                res = _run_on_main_thread(lambda: _handle_project_open(payload))
            elif self.path == "/export/textures":
                _wait_until_not_busy(120.0)
                res = _run_on_main_thread(lambda: _handle_export_textures(payload))
            else:
                self._send_json(404, {"ok": False, "error": f"Unknown endpoint: {self.path}"})
                return
            self._send_json(200, res)
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": str(exc)})


def _export_from_menu() -> None:
    if QtWidgets is None:
        return
    if not sp_project.is_open():
        QtWidgets.QMessageBox.warning(None, "Sublayer Bridge", "Open a Substance Painter project first.")
        return
    export_dir = QtWidgets.QFileDialog.getExistingDirectory(None, "Choose Texture Export Folder for Sublayer")
    if not export_dir:
        return
    try:
        res = _handle_export_textures({"exportDir": export_dir})
        QtWidgets.QMessageBox.information(
            None,
            "Sublayer Bridge",
            f"Exported textures and manifest to:\n{res['manifestPath']}\n\n"
            "Click 'Sync Materials' or 'Import SP Manifest' in Sublayer.",
        )
    except Exception as exc:  # noqa: BLE001
        QtWidgets.QMessageBox.critical(None, "Sublayer Bridge", f"Export failed:\n{exc}")


def start_plugin() -> None:
    global _server, _server_thread, _dispatcher
    if _MainThreadDispatcher is not None:
        _dispatcher = _MainThreadDispatcher()
    try:
        _server = ThreadingHTTPServer(("127.0.0.1", DEFAULT_PORT), _BridgeHandler)
        _server_thread = threading.Thread(target=_server.serve_forever, name="SublayerBridgeHTTP", daemon=True)
        _server_thread.start()
        _log(f"Listening on http://127.0.0.1:{DEFAULT_PORT}")
    except OSError as exc:
        _log(f"Could not bind port {DEFAULT_PORT}: {exc}", warning=True)

    if sp_event is not None:
        try:
            sp_event.DISPATCHER.connect(sp_event.ProjectSaved, _on_project_saved)
        except Exception:
            pass
    if sp_ui is not None and QtWidgets is not None:
        action = QtWidgets.QAction("Sublayer: Export Textures...", None)
        action.triggered.connect(_export_from_menu)
        sp_ui.add_action(sp_ui.ApplicationMenu.File, action)
        _plugin_widgets.append(action)


def close_plugin() -> None:
    global _server, _server_thread, _dispatcher
    _dispatcher = None
    if _server is not None:
        try:
            _server.shutdown()
            _server.server_close()
        except Exception:
            pass
        _server = None
        _server_thread = None
    if sp_event is not None:
        try:
            sp_event.DISPATCHER.disconnect(sp_event.ProjectSaved, _on_project_saved)
        except Exception:
            pass
    if sp_ui is not None:
        for widget in _plugin_widgets:
            try:
                sp_ui.delete_ui_element(widget)
            except Exception:
                pass
    _plugin_widgets.clear()
    _log("Sublayer Bridge stopped.")
