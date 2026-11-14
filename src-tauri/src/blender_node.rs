//! Headless Blender inspection and USD export adapter for Blender nodes.
use crate::application_profiles::Catalog;
use crate::project::BlenderObjectInfo;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

pub fn find_blender_executable(catalog: &Catalog) -> Result<PathBuf, String> {
    if let Some(profile) = catalog
        .applications
        .iter()
        .find(|p| p.app.eq_ignore_ascii_case("Blender") || p.id == "blender")
    {
        let p = PathBuf::from(&profile.executable);
        if p.is_file() {
            return Ok(p);
        }
    }

    let candidates = [
        r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender\blender.exe",
    ];
    for candidate in candidates {
        let p = PathBuf::from(candidate);
        if p.is_file() {
            return Ok(p);
        }
    }

    if let Ok(path_var) = std::env::var("PATH") {
        for dir in std::env::split_paths(&path_var) {
            let candidate = dir.join("blender.exe");
            if candidate.is_file() {
                return Ok(candidate);
            }
        }
    }

    Err("Blender executable not found. Please install Blender or configure its executable path in Applications settings.".into())
}

pub fn inspect_blender_file(
    executable: &Path,
    blend_path: &Path,
) -> Result<Vec<BlenderObjectInfo>, String> {
    if !blend_path.is_file() {
        return Err(format!(
            "Blender file does not exist: {}",
            blend_path.display()
        ));
    }

    let script = r#"
import bpy, json

EXPORTABLE_TYPES = {'MESH', 'CURVE', 'CURVES', 'SURFACE', 'META', 'FONT', 'VOLUME', 'HAIR', 'POINTCLOUD', 'GPENCIL', 'GREASEPENCIL'}

def is_exportable(obj):
    if obj.type == 'CURVE':
        data = getattr(obj, 'data', None)
        has_geo = bool(
            data is not None and (
                getattr(data, 'bevel_depth', 0.0) > 0.0
                or getattr(data, 'extrude', 0.0) > 0.0
                or getattr(data, 'bevel_object', None) is not None
            )
        )
        has_mesh_mod = any(
            getattr(m, 'type', None) in {'SCREW', 'SKIN', 'SOLIDIFY', 'REMESH'}
            for m in getattr(obj, 'modifiers', ())
        )
        return has_geo or has_mesh_mod
    if obj.type in EXPORTABLE_TYPES:
        return True
    if obj.type == 'EMPTY' and getattr(obj, 'instance_type', None) == 'COLLECTION' and getattr(obj, 'instance_collection', None) is not None:
        return True
    return False

def get_collection_visibility(scene):
    vis_map = {}
    ancestors_map = {}
    def walk_lc(lc, parent_vis, ancestors, is_root=False):
        if is_root:
            vis = True
        else:
            vis = (
                parent_vis
                and not getattr(lc, 'exclude', False)
                and not getattr(lc, 'hide_viewport', False)
                and not getattr(lc.collection, 'hide_viewport', False)
                and not getattr(lc.collection, 'hide_render', False)
            )
        cname = lc.collection.name
        vis_map[cname] = vis_map.get(cname, False) or bool(vis)
        if cname not in ancestors_map:
            ancestors_map[cname] = set()
        ancestors_map[cname].update(ancestors)
        next_ancestors = ancestors if is_root else (ancestors | {cname})
        for child in lc.children:
            walk_lc(child, vis, next_ancestors, False)
    if scene.view_layers:
        walk_lc(scene.view_layers[0].layer_collection, True, set(), True)
    return vis_map, ancestors_map

win_scene = bpy.context.window.scene if bpy.context.window and bpy.context.window.scene else bpy.context.scene
scenes = list(bpy.data.scenes)
default_scene = win_scene
if default_scene is None or not any(is_exportable(o) for o in default_scene.objects):
    best = max(scenes, key=lambda s: sum(1 for o in s.objects if is_exportable(o)), default=None)
    if best is not None:
        default_scene = best

inst_stats_cache = {}
def get_inst_stats(col, visited=None):
    if visited is None:
        visited = set()
    if col.name in inst_stats_cache:
        return inst_stats_cache[col.name]
    if col.name in visited:
        return (0, 0)
    visited = visited | {col.name}
    v_total = 0
    p_total = 0
    for o in col.all_objects:
        if o.type == 'MESH' and getattr(o, 'data', None) is not None:
            v_total += len(o.data.vertices)
            p_total += len(o.data.polygons)
        elif o.type == 'EMPTY' and getattr(o, 'instance_type', None) == 'COLLECTION' and getattr(o, 'instance_collection', None) is not None:
            iv, ip = get_inst_stats(o.instance_collection, visited)
            v_total += iv
            p_total += ip
    inst_stats_cache[col.name] = (v_total, p_total)
    return (v_total, p_total)

items = []
for s in scenes:
    col_vis, ancestors_map = get_collection_visibility(s)
    is_active_scene = (default_scene is not None and s.name == default_scene.name)
    vl = s.view_layers[0] if s.view_layers else None
    for obj in sorted(s.objects, key=lambda o: o.name):
        if not is_exportable(obj):
            continue
        v = 0
        p = 0
        obj_type = obj.type
        if obj.type == 'MESH' and getattr(obj, 'data', None) is not None:
            v = len(obj.data.vertices)
            p = len(obj.data.polygons)
        elif obj.type == 'EMPTY' and getattr(obj, 'instance_type', None) == 'COLLECTION' and getattr(obj, 'instance_collection', None) is not None:
            obj_type = 'INSTANCE'
            v, p = get_inst_stats(obj.instance_collection)
        direct_cols = [c.name for c in obj.users_collection if c.name in col_vis]
        if not direct_cols:
            direct_cols = [c.name for c in obj.users_collection]
        if not direct_cols and obj.name in s.collection.objects:
            direct_cols = [s.collection.name]
        is_hidden_vl = False
        if vl is not None:
            try:
                is_hidden_vl = bool(obj.hide_get(view_layer=vl))
            except Exception:
                is_hidden_vl = False
        scene_visible = (
            any(col_vis.get(c, True) for c in direct_cols)
            and not getattr(obj, 'hide_viewport', False)
            and not getattr(obj, 'hide_render', False)
            and not is_hidden_vl
        )
        if obj_type == 'INSTANCE' and getattr(obj, 'instance_collection', None) is not None:
            inst_col = obj.instance_collection
            if getattr(inst_col, 'hide_viewport', False) or getattr(inst_col, 'hide_render', False):
                scene_visible = False
            if not direct_cols or direct_cols == [s.collection.name]:
                cols = [inst_col.name]
            else:
                cols = list(direct_cols)
        else:
            cols = list(direct_cols)
        for c in list(direct_cols):
            for anc in sorted(ancestors_map.get(c, ())):
                anc_col = bpy.data.collections.get(anc)
                if anc_col is not None and len(anc_col.objects) == 0 and bool(col_vis.get(anc, True)) == bool(scene_visible):
                    if anc not in cols:
                        cols.append(anc)
        items.append({
            'name': obj.name,
            'type': obj_type,
            'vertices': v,
            'polygons': p,
            'collections': cols,
            'scene': s.name,
            'sceneVisible': bool(scene_visible),
            'activeScene': bool(is_active_scene),
        })

print('ORCHFLOW_BLENDER_INSPECT:' + json.dumps(items))
"#;

    let mut cmd = Command::new(executable);
    cmd.args([
        "--background",
        "--factory-startup",
        blend_path.to_str().ok_or("Invalid blend path encoding")?,
        "--python-exit-code",
        "1",
        "--python-expr",
        script,
    ])
    .stdin(Stdio::null())
    .stdout(Stdio::piped())
    .stderr(Stdio::piped());

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }

    let output = cmd
        .output()
        .map_err(|e| format!("Failed to run Blender: {e}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout);

    for line in stdout.lines() {
        if let Some(rest) = line.strip_prefix("ORCHFLOW_BLENDER_INSPECT:") {
            let objects: Vec<BlenderObjectInfo> = serde_json::from_str(rest.trim())
                .map_err(|e| format!("Invalid object data returned from Blender: {e}"))?;
            return Ok(objects);
        }
    }

    let stderr = String::from_utf8_lossy(&output.stderr);
    Err(format!(
        "Blender did not return object inspection data. {stderr} {stdout}"
    ))
}

static BLENDER_EXPORT_MUTEX: std::sync::Mutex<()> = std::sync::Mutex::new(());

pub fn export_blender_to_usd(
    executable: &Path,
    blend_path: &Path,
    usd_path: &Path,
    selected_objects: Option<&[String]>,
) -> Result<(), String> {
    let _export_guard = BLENDER_EXPORT_MUTEX
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if !blend_path.is_file() {
        return Err(format!(
            "Blender file does not exist: {}",
            blend_path.display()
        ));
    }

    if let Some(parent) = usd_path.parent() {
        if !parent.is_dir() {
            std::fs::create_dir_all(parent)
                .map_err(|e| format!("Cannot create output USD directory: {e}"))?;
        }
    }

    let script = r#"
import bpy, json, sys, os
line = sys.stdin.readline()
if not line:
    sys.exit(1)
config = json.loads(line)
usd_path = config['usdPath']
selected = config.get('selectedObjects')

if isinstance(selected, list) and len(selected) > 0 and len(bpy.data.scenes) > 1:
    sel_set_init = set(selected)
    win_scene = bpy.context.window.scene if bpy.context.window and bpy.context.window.scene else bpy.context.scene
    best = max(bpy.data.scenes, key=lambda s: (sum(1 for o in s.objects if o.name in sel_set_init), 1 if (win_scene and s.name == win_scene.name) else 0))
    if best is not None:
        bpy.context.window.scene = best
elif len(bpy.context.view_layer.objects) == 0 and len(bpy.data.scenes) > 1:
    bpy.context.window.scene = max(bpy.data.scenes, key=lambda s: len(s.objects))

def _enable_collections(lc):
    lc.exclude = False
    lc.hide_viewport = False
    for child in lc.children:
        _enable_collections(child)
_enable_collections(bpy.context.view_layer.layer_collection)

for img in bpy.data.images:
    fp = getattr(img, 'filepath', '') or ''
    if fp:
        abs_fp = os.path.abspath(bpy.path.abspath(fp))
        if abs_fp != fp:
            img.filepath = abs_fp

for obj in bpy.data.objects:
    obj["orchflow_blender_object"] = obj.name

props = bpy.ops.wm.usd_export.get_rna_type().properties
kwargs = {
    'filepath': usd_path,
    'root_prim_path': '/root',
    'export_meshes': True,
    'export_materials': True,
    'export_lights': False,
    'export_cameras': False,
    'export_curves': True,
    'export_volumes': False,
    'export_hair': True,
    'export_points': True,
    'export_armatures': True,
    'export_normals': True,
    'export_uvmaps': True,
    'export_mesh_colors': True,
    'generate_preview_surface': True,
    'export_textures_mode': 'KEEP',
    'relative_paths': False,
    'convert_world_material': False,
    'use_instancing': True,
    'evaluation_mode': 'VIEWPORT',
    'export_subdivision': 'BEST_MATCH',
    'export_custom_properties': True,
    'author_blender_name': True,
}
if 'convert_orientation' in props:
    kwargs['convert_orientation'] = True

for obj in bpy.context.view_layer.objects:
    obj.hide_viewport = False
    obj.hide_render = False
    if obj.hide_get():
        obj.hide_set(False)

if isinstance(selected, list):
    sel_set = set(selected)
    expanded = set()
    by_name = {o.name: o for o in bpy.context.view_layer.objects}
    for name in sel_set:
        obj = by_name.get(name)
        if obj is None:
            continue
        expanded.add(obj.name)
        curr = obj.parent
        while curr is not None:
            expanded.add(curr.name)
            curr = curr.parent
        for mod in getattr(obj, 'modifiers', []):
            if mod.type == 'ARMATURE' and getattr(mod, 'object', None):
                arm = mod.object
                expanded.add(arm.name)
                curr = arm.parent
                while curr is not None:
                    expanded.add(curr.name)
                    curr = curr.parent
    for obj in bpy.context.view_layer.objects:
        obj.select_set(obj.name in expanded)
    kwargs['selected_objects_only'] = True
else:
    kwargs['selected_objects_only'] = False

for k in list(kwargs.keys()):
    if k not in props:
        del kwargs[k]

res = bpy.ops.wm.usd_export(**kwargs)
if 'FINISHED' not in res:
    raise RuntimeError(f'bpy.ops.wm.usd_export returned {res}')

if not os.path.isfile(usd_path) or os.path.getsize(usd_path) == 0:
    raise RuntimeError('Blender failed to export USD file to: ' + usd_path)
print('ORCHFLOW_BLENDER_EXPORT:SUCCESS')
"#;

    let mut cmd = Command::new(executable);
    cmd.args([
        "--background",
        "--factory-startup",
        blend_path.to_str().ok_or("Invalid blend path encoding")?,
        "--python-exit-code",
        "1",
        "--python-expr",
        script,
    ])
    .stdin(Stdio::piped())
    .stdout(Stdio::piped())
    .stderr(Stdio::piped());

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("Cannot start Blender: {e}"))?;
    let config = serde_json::json!({
        "usdPath": usd_path.to_string_lossy(),
        "selectedObjects": selected_objects
    });

    if let Some(mut stdin) = child.stdin.take() {
        stdin
            .write_all(format!("{config}\n").as_bytes())
            .map_err(|e| format!("Cannot send configuration to Blender: {e}"))?;
    }

    let output = child
        .wait_with_output()
        .map_err(|e| format!("Blender failed during export: {e}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout);

    if !output.status.success() || !stdout.contains("ORCHFLOW_BLENDER_EXPORT:SUCCESS") {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("Blender USD export failed. {stderr} {stdout}"));
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_find_blender_executable_finds_system_blender() {
        let catalog = Catalog::defaults();
        let found = find_blender_executable(&catalog);
        assert!(
            found.is_ok(),
            "Expected to locate installed blender executable: {:?}",
            found
        );
        let path = found.unwrap();
        assert!(path.is_file());
    }

    #[test]
    fn test_blender_node_project_validation_and_round_trip() {
        let json_val = serde_json::json!({
            "version": 2,
            "nodes": [
                {
                    "id": "b1",
                    "type": "stage",
                    "position": { "x": 100, "y": 100 },
                    "data": {
                        "label": "Blender",
                        "operation": "blender",
                        "app": "Blender",
                        "axis": "Z",
                        "scale": 1.0,
                        "nativeProjectDependencyId": "dep_blend",
                        "publishedDependencyId": "dep_usd",
                        "blenderSelections": ["Suzanne", "Cube"],
                        "blenderObjects": [
                            { "name": "Suzanne", "type": "MESH", "vertices": 507, "polygons": 500 },
                            { "name": "Cube", "type": "MESH", "vertices": 8, "polygons": 6 }
                        ]
                    }
                }
            ],
            "edges": [],
            "dependencies": [
                { "id": "dep_blend", "kind": "nativeProject", "path": "I:/models/test.blend" },
                { "id": "dep_usd", "kind": "usd", "path": "I:/models/test.usda" }
            ]
        });

        let project = crate::project::Project::from_value(json_val).expect("Valid project");
        project
            .validate()
            .expect("Project validation must succeed for blender node");
        let node = &project.nodes[0];
        assert_eq!(node.data.operation, "blender");
        assert_eq!(
            node.data.blender_selections.as_ref().unwrap(),
            &vec!["Suzanne", "Cube"]
        );
        assert_eq!(node.data.blender_objects.as_ref().unwrap().len(), 2);
    }

    #[test]
    fn test_blender_object_info_deserialization() {
        let payload = r#"[
            {"name": "Head.001", "type": "MESH", "vertices": 1200, "polygons": 1100, "collections": ["Scene Collection"]},
            {"name": "Curves.001", "type": "CURVES", "vertices": 0, "polygons": 0, "collections": ["Hair"]}
        ]"#;
        let parsed: Vec<BlenderObjectInfo> =
            serde_json::from_str(payload).expect("Valid BlenderObjectInfo JSON");
        assert_eq!(parsed.len(), 2);
        assert_eq!(parsed[0].name, "Head.001");
        assert_eq!(parsed[0].collections, vec!["Scene Collection"]);
        assert_eq!(parsed[1].r#type, "CURVES");
    }
}
