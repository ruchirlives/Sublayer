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
    ckey = (col.name, getattr(getattr(col, 'library', None), 'filepath', '') or '')
    if ckey in inst_stats_cache:
        return inst_stats_cache[ckey]
    if ckey in visited:
        return (0, 0)
    visited = visited | {ckey}
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
    inst_stats_cache[ckey] = (v_total, p_total)
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
        try:
            lib = getattr(img, 'library', None)
            abs_fp = os.path.abspath(bpy.path.abspath(fp, library=lib))
            if abs_fp != fp:
                img.filepath = abs_fp
        except Exception:
            pass

def _unhide_collection_tree(col, visited=None):
    if visited is None:
        visited = set()
    if col is None:
        return
    cid = (col.name, getattr(getattr(col, 'library', None), 'filepath', '') or '')
    if cid in visited:
        return
    visited.add(cid)
    for attr in ('hide_viewport', 'hide_render'):
        try:
            if getattr(col, attr, False):
                setattr(col, attr, False)
        except Exception:
            pass
    for o in getattr(col, 'all_objects', ()):
        for attr in ('hide_viewport', 'hide_render'):
            try:
                if getattr(o, attr, False):
                    setattr(o, attr, False)
            except Exception:
                pass
        if o.type == 'EMPTY' and getattr(o, 'instance_type', None) == 'COLLECTION' and getattr(o, 'instance_collection', None) is not None:
            _unhide_collection_tree(o.instance_collection, visited)

for obj in bpy.data.objects:
    try:
        obj["orchflow_blender_object"] = obj.name
    except Exception:
        pass

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
    for attr in ('hide_viewport', 'hide_render'):
        try:
            if getattr(obj, attr, False):
                setattr(obj, attr, False)
        except Exception:
            pass
    try:
        if obj.hide_get():
            obj.hide_set(False)
    except Exception:
        pass
    if obj.type == 'EMPTY' and getattr(obj, 'instance_type', None) == 'COLLECTION' and getattr(obj, 'instance_collection', None) is not None:
        _unhide_collection_tree(obj.instance_collection)

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
            elif mod.type == 'PARTICLE_SYSTEM':
                ps = getattr(mod, 'particle_system', None)
                pset = getattr(ps, 'settings', None) if ps is not None else None
                if pset is not None:
                    io = getattr(pset, 'instance_object', None)
                    ic = getattr(pset, 'instance_collection', None)
                    if io is not None:
                        for attr in ('hide_viewport', 'hide_render'):
                            try: setattr(io, attr, False)
                            except Exception: pass
                    if ic is not None:
                        _unhide_collection_tree(ic)
            elif mod.type == 'NODES':
                try:
                    for k in mod.keys():
                        val = mod[k]
                        if isinstance(val, bpy.types.Collection):
                            _unhide_collection_tree(val)
                        elif isinstance(val, bpy.types.Object):
                            for attr in ('hide_viewport', 'hide_render'):
                                try: setattr(val, attr, False)
                                except Exception: pass
                except Exception:
                    pass
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    exportable_inst_types = {'MESH', 'CURVE', 'CURVES', 'SURFACE', 'META', 'FONT', 'VOLUME', 'HAIR', 'POINTCLOUD'}
    materialized_parents = set()
    proto_data_cache = {}
    inst_counter = 0
    for inst in depsgraph.object_instances:
        if not getattr(inst, 'is_instance', False):
            continue
        parent_ev = getattr(inst, 'parent', None)
        if parent_ev is None:
            continue
        parent_orig = getattr(parent_ev, 'original', parent_ev)
        if parent_orig.name not in expanded or parent_orig.type == 'EMPTY':
            continue
        inst_obj = getattr(inst, 'object', None)
        if inst_obj is None or inst_obj.type not in exportable_inst_types:
            continue
        eval_data = getattr(inst_obj, 'data', None)
        if eval_data is None:
            continue
        if inst_obj.type == 'MESH' and len(eval_data.polygons) == 0:
            continue
        data_key = (inst_obj.type, eval_data.as_pointer())
        proto_data = proto_data_cache.get(data_key)
        if proto_data is None:
            if inst_obj.type == 'MESH':
                try:
                    cand = eval_data.copy()
                    if len(cand.vertices) > 0 and len(cand.polygons) > 0:
                        proto_data = cand
                    else:
                        bpy.data.meshes.remove(cand)
                except Exception:
                    proto_data = None
                if proto_data is None:
                    try:
                        import bmesh
                        bm = bmesh.new()
                        bm.from_mesh(eval_data)
                        proto_data = bpy.data.meshes.new(f"OrchFlowInstProto_{len(proto_data_cache)}")
                        bm.to_mesh(proto_data)
                        bm.free()
                        for mat in getattr(eval_data, 'materials', ()):
                            proto_data.materials.append(mat)
                        if len(proto_data.vertices) == 0 or len(proto_data.polygons) == 0:
                            bpy.data.meshes.remove(proto_data)
                            proto_data = None
                    except Exception:
                        proto_data = None
                if proto_data is None:
                    continue
            else:
                try:
                    proto_data = eval_data.copy()
                except Exception:
                    continue
            proto_data_cache[data_key] = proto_data
        inst_counter += 1
        dup = bpy.data.objects.new(f"{parent_orig.name}__inst_{inst_counter}", proto_data)
        dup.matrix_world = inst.matrix_world.copy()
        try:
            dup["orchflow_blender_object"] = parent_orig.name
        except Exception:
            pass
        bpy.context.scene.collection.objects.link(dup)
        expanded.add(dup.name)
        materialized_parents.add(parent_orig)
    for p_orig in materialized_parents:
        try:
            if getattr(p_orig, 'instance_type', 'NONE') in ('VERTS', 'FACES'):
                p_orig.instance_type = 'NONE'
        except Exception:
            pass
        has_gn = any(getattr(m, 'type', None) == 'NODES' for m in getattr(p_orig, 'modifiers', ()))
        hide_emitter = (
            has_gn
            or not getattr(p_orig, 'show_instancer_for_viewport', True)
            or not getattr(p_orig, 'show_instancer_for_render', True)
            or (p_orig.type == 'MESH' and getattr(p_orig, 'data', None) is not None and len(p_orig.data.polygons) == 0)
        )
        if hide_emitter:
            expanded.discard(p_orig.name)
    if materialized_parents:
        bpy.context.view_layer.update()
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
