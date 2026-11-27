"""Sublayer <-> Marvelous Designer Live Bridge: Pull avatar/pose-ramp into open MD.

Load this script in Marvelous Designer (Script -> Python Editor) and click Run
to import the newest handoff (*_avatar_pose.abc or *_avatar.obj) from
%LOCALAPPDATA%/Sublayer/md_bridge/ as an Avatar in the open scene.
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
                data = json.load(f) or {}
            candidates = []
            abc_path = data.get("handoffAbcPath") or ""
            if abc_path and os.path.isfile(abc_path):
                abc_ts = int(data.get("handoffAbcExportedAt") or os.path.getmtime(abc_path) * 1000)
                candidates.append((abc_ts, abc_path, data))
            obj_path = data.get("handoffPath") or ""
            if obj_path and os.path.isfile(obj_path):
                obj_ts = int(data.get("exportedAt") or os.path.getmtime(obj_path) * 1000)
                candidates.append((obj_ts, obj_path, data))
            if candidates:
                candidates.sort(key=lambda item: item[0], reverse=True)
                return candidates[0][1], candidates[0][2]
        except Exception:
            pass

    if os.path.isdir(bridge_dir):
        candidates = [
            os.path.join(bridge_dir, name)
            for name in os.listdir(bridge_dir)
            if name.endswith("_avatar_pose.abc")
            or name.endswith("_avatar.obj")
            or name == "handoff.obj"
        ]
        candidates = [p for p in candidates if os.path.isfile(p)]
        if candidates:
            candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            return candidates[0], {}
    return None, {}


def main():
    local_app_data = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    bridge_dir = os.path.join(local_app_data, "Sublayer", "md_bridge")
    handoff_path, _meta = _resolve_handoff_path(bridge_dir)
    if not handoff_path:
        msg = (
            f"No Sublayer handoff avatar (.abc or .obj) found in:\n{bridge_dir}\n"
            "Click 'Export Pose Ramp (.abc, 30f)' or 'Send Static Avatar (.obj)' in Sublayer first."
        )
        print(msg)
        try:
            utility_api.DisplayMessageBoxW(msg)
        except Exception:
            pass
        return

    opt = cloApiTypes.ImportExportOption()
    opt.bAdd = True
    opt.scale = 1.0  # Sublayer exports handoff .abc/.obj in mm (0.001 m/unit).

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

    ext = os.path.splitext(handoff_path)[1].lower()
    if ext == ".abc":
        ok = import_api.ImportFileW(handoff_path, opt)
    else:
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
