"""Sublayer <-> Marvelous Designer Live Bridge: Pull avatar/handoff into open MD.

Load this script in Marvelous Designer (Script -> Python Editor) and click Run
to import the latest handoff *_avatar.obj from %LOCALAPPDATA%/Sublayer/md_bridge/
as an Avatar in the open scene.
"""

import json
import os
import cloApiTypes
import import_api
import utility_api


def _resolve_handoff_path(bridge_dir):
    manifest_path = os.path.join(bridge_dir, "handoff_manifest.json")
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            candidate = data.get("handoffPath") or ""
            if candidate and os.path.isfile(candidate):
                return candidate, data
        except Exception:
            pass
    # Fallback: newest *_avatar.obj or handoff.obj in bridge_dir.
    if os.path.isdir(bridge_dir):
        candidates = [
            os.path.join(bridge_dir, name)
            for name in os.listdir(bridge_dir)
            if name.endswith("_avatar.obj") or name == "handoff.obj"
        ]
        candidates = [p for p in candidates if os.path.isfile(p)]
        if candidates:
            candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            return candidates[0], {}
    return None, {}


def main():
    local_app_data = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    bridge_dir = os.path.join(local_app_data, "Sublayer", "md_bridge")
    handoff_path, meta = _resolve_handoff_path(bridge_dir)
    if not handoff_path:
        msg = f"No Sublayer handoff avatar OBJ found in:\n{bridge_dir}\nClick 'Send Handoff to MD Bridge' in Sublayer first."
        print(msg)
        try:
            utility_api.DisplayMessageBoxW(msg)
        except Exception:
            pass
        return

    opt = cloApiTypes.ImportExportOption()
    opt.bAdd = True
    opt.scale = 1.0  # Sublayer already exports handoff *_avatar.obj in mm (0.001 m/unit).

    # Import as Avatar when the enum/member is exposed by cloApiTypes.
    try:
        enum_cls = getattr(cloApiTypes, "ImportObjectType", None)
        if enum_cls is not None:
            for attr in ("Avatar", "AVATAR", "ImportAvatar", "OBJ_AVATAR"):
                if hasattr(enum_cls, attr):
                    opt.ImportObjectType = getattr(enum_cls, attr)
                    break
    except Exception:
        pass

    ok = import_api.ImportOBJW(handoff_path, opt)
    try:
        utility_api.Refresh3DWindow()
    except Exception:
        pass

    msg = f"Imported Sublayer avatar ({os.path.basename(handoff_path)}) -> {ok}"
    print(msg)
    try:
        utility_api.DisplayMessageBoxW(msg)
    except Exception:
        pass


main()
