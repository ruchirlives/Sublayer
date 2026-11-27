"""Sublayer <-> Marvelous Designer Live Bridge: Send open garment to Sublayer.

Load this script in Marvelous Designer (Script -> Python Editor) and click Run
to export the currently open garment (without the avatar) to Sublayer's bridge
folder (%LOCALAPPDATA%/Sublayer/md_bridge/<project_name>_garment.obj).
"""

import json
import os
import re
import time
import cloApiTypes
import export_api
import utility_api


def _safe_slug(name, fallback="md_scene"):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f\s]+', "_", str(name or "")).strip("._")
    return cleaned or fallback


def main():
    local_app_data = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    bridge_dir = os.path.join(local_app_data, "Sublayer", "md_bridge")
    os.makedirs(bridge_dir, exist_ok=True)

    project_path = ""
    project_name = "Untitled"
    try:
        project_path = utility_api.GetProjectFilePathW() or ""
    except Exception:
        pass
    try:
        project_name = utility_api.GetProjectNameW() or project_name
    except Exception:
        pass

    base_name = project_name
    if (not base_name or base_name == "Untitled") and project_path:
        base_name = os.path.splitext(os.path.basename(project_path))[0]
    if not base_name or base_name == "Untitled":
        handoff_manifest = os.path.join(bridge_dir, "handoff_manifest.json")
        if os.path.isfile(handoff_manifest):
            try:
                with open(handoff_manifest, "r", encoding="utf-8") as hf:
                    base_name = json.load(hf).get("baseName") or base_name
            except Exception:
                pass
    slug = _safe_slug(base_name, fallback="md_garment")

    export_path = os.path.join(bridge_dir, f"{slug}_garment.obj")
    manifest_path = os.path.join(bridge_dir, "manifest.json")
    tmp_manifest = manifest_path + ".tmp"

    opt = cloApiTypes.ImportExportOption()
    opt.bExportGarment = True
    opt.bExportAvatar = False
    opt.bIncludeHiddenObject = False
    opt.bSaveInZip = False
    opt.bSingleObject = False
    opt.bThin = True
    opt.bCreateUnifiedTexture = True
    opt.bUnifiedDiffuseMap = True
    opt.bUnifiedNormalMap = True
    opt.bUnifiedRoughnessMap = True
    opt.bUnifiedMetalnessMap = True
    opt.bUnifiedOpacityMap = True
    opt.scale = 1.0  # MD exports in mm; Sublayer MD profile converts 0.001 m/unit.

    was_avatar_shown = True
    try:
        was_avatar_shown = bool(utility_api.IsShowAvatar())
        if was_avatar_shown:
            utility_api.SetShowHideAvatar(False)
    except Exception:
        pass

    try:
        exported = export_api.ExportOBJ(export_path, opt)
    finally:
        try:
            if was_avatar_shown:
                utility_api.SetShowHideAvatar(True)
        except Exception:
            pass
    if not exported:
        print("Export cancelled.")
        return
    actual_obj = export_path if os.path.isfile(export_path) else next(
        (p for p in exported if str(p).lower().endswith(".obj") and os.path.isfile(p)),
        "",
    )
    if not actual_obj:
        raise RuntimeError(
            f"ExportOBJ did not produce an .obj file (returned: {exported!r})"
        )

    manifest = {
        "projectPath": project_path,
        "projectName": project_name,
        "exportPath": actual_obj,
        "exportedFiles": list(exported) if exported else [actual_obj],
        "exportedAt": int(time.time() * 1000),
        "garmentOnly": True,
        "scale": 1.0,
    }
    with open(tmp_manifest, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    os.replace(tmp_manifest, manifest_path)

    msg = f"Sent garment to Sublayer bridge:\n{export_path}"
    print(msg)
    try:
        utility_api.DisplayMessageBoxW(msg)
    except Exception:
        pass


main()
