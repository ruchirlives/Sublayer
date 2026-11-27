mod application_profiles;
use tauri::{Emitter, Manager};
mod application;
pub mod blender_node;
mod dependency_search;
mod desktop_bridge;
pub mod megascans;
mod project;
mod runtime_setup;
pub mod substance_painter;
mod usd;
use project::Project;

fn catalog_path(app: &tauri::AppHandle) -> Result<std::path::PathBuf, String> {
    app.path()
        .app_config_dir()
        .map(|p| p.join("applications.json"))
        .map_err(|e| e.to_string())
}
#[tauri::command]
async fn authorize_publication_scope(
    project: Project,
    node_id: String,
    app: tauri::AppHandle,
    service: tauri::State<'_, std::sync::Arc<usd::UsdService>>,
) -> Result<application_profiles::PolicySnapshot, String> {
    let path = catalog_path(&app)?;
    let service = service.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        let policy = application::proposed_publication_policy(
            &project,
            &node_id,
            &application_profiles::load(&path)?,
        )?;
        let node = project.nodes.iter().find(|n| n.id == node_id).unwrap();
        let native = node
            .data
            .manual_baseline
            .as_ref()
            .or(node.data.native_import.as_ref())
            .ok_or("Select a baseline first")?;
        let baseline = project
            .dependencies
            .iter()
            .find(|d| d.id == native.baseline_dependency_id)
            .ok_or("Baseline link missing")?
            .path
            .clone();
        // Build a read-only baseline check with the proposed scope without altering
        // the immutable native-import record or any application files.
        let mut clone =
            Project::from_value(serde_json::to_value(&project).map_err(|e| e.to_string())?)?;
        clone
            .nodes
            .iter_mut()
            .find(|n| n.id == node_id)
            .unwrap()
            .data
            .publication_policy = Some(policy.clone());
        service.request(application::publication_request(
            &clone,
            &node_id,
            &baseline,
            vec![],
            vec![],
            None,
            None,
        )?)?;
        Ok(policy)
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn materialx_rule_matches(
    project: Project,
    node_id: String,
    service: tauri::State<'_, std::sync::Arc<usd::UsdService>>,
) -> Result<serde_json::Value, String> {
    let graph = application::evaluation_graph(&project, &node_id, false)?;
    let service = service.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        service.request(serde_json::json!({"operation":"materialx_rule_matches","graph":graph}))
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn inspect_stage_materials(
    project: Project,
    node_id: String,
    service: tauri::State<'_, std::sync::Arc<usd::UsdService>>,
) -> Result<serde_json::Value, String> {
    let graph = application::evaluation_graph(&project, &node_id, false)?;
    let service = service.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        service.request(serde_json::json!({"operation":"inspect_stage_materials","graph":graph}))
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn inspect_stage_lights(
    project: Project,
    node_id: String,
    service: tauri::State<'_, std::sync::Arc<usd::UsdService>>,
) -> Result<serde_json::Value, String> {
    let graph = application::evaluation_graph(&project, &node_id, false)?;
    let service = service.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        service.request(serde_json::json!({"operation":"inspect_stage_lights","graph":graph}))
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn export_materialx(
    project: Project,
    node_id: String,
    destination: String,
    service: tauri::State<'_, std::sync::Arc<usd::UsdService>>,
) -> Result<serde_json::Value, String> {
    let service = service.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        application::export_materialx(&project, &node_id, &destination, &service)
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn reconcile_application_upstream(
    project: Project,
    node_id: String,
    destination: String,
    service: tauri::State<'_, std::sync::Arc<usd::UsdService>>,
) -> Result<serde_json::Value, String> {
    let service = service.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        service.request(application::reconciliation_request(
            &project,
            &node_id,
            &destination,
        )?)
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn validate_manual_baseline(
    app: tauri::AppHandle,
    project: Project,
    node_id: String,
    baseline: String,
    correspondence: Option<Vec<serde_json::Value>>,
    expected_digest: Option<String>,
    service: tauri::State<'_, std::sync::Arc<usd::UsdService>>,
) -> Result<serde_json::Value, String> {
    let config = catalog_path(&app)?;
    let service = service.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        let mut request = application::manual_baseline_request(
            &project,
            &node_id,
            &baseline,
            &application_profiles::load(&config)?,
        )?;
        request["correspondence"] = serde_json::json!(correspondence.unwrap_or_default());
        request["expectedDigest"] = serde_json::json!(expected_digest);
        service.request(request)
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn apply_live_adjustment(
    project: Project,
    node_id: String,
    edited: String,
    correspondence: Vec<serde_json::Value>,
    new_prim_paths: Vec<String>,
    review_token: String,
    service: tauri::State<'_, std::sync::Arc<usd::UsdService>>,
) -> Result<serde_json::Value, String> {
    let service = service.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        let mut request = application::publication_request(
            &project,
            &node_id,
            &edited,
            correspondence,
            vec![],
            None,
            Some(review_token.clone()),
        )?;
        request["operation"] = "apply_live_adjustment".into();
        request["liveReview"] = true.into();
        request["reviewToken"] = review_token.into();
        request["newPrimPaths"] = serde_json::json!(new_prim_paths);
        service.request(request)
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn export_live_output(
    project: Project,
    node_id: String,
    destination: String,
    service: tauri::State<'_, std::sync::Arc<usd::UsdService>>,
) -> Result<serde_json::Value, String> {
    let graph = application::evaluation_graph(&project, &node_id, false)?;
    let service = service.inner().clone();
    tauri::async_runtime::spawn_blocking(move || service.request(serde_json::json!({"operation":"export_live_output","graph":graph,"destination":destination}))).await.map_err(|e|e.to_string())?
}
#[tauri::command]
async fn review_application_publication(
    project: Project,
    node_id: String,
    edited: String,
    correspondence: Vec<serde_json::Value>,
    confirmed_deletions: Vec<String>,
    destination: Option<String>,
    review_token: Option<String>,
    new_prim_paths: Option<Vec<String>>,
    overwrite: Option<bool>,
    service: tauri::State<'_, std::sync::Arc<usd::UsdService>>,
) -> Result<serde_json::Value, String> {
    let service = service.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        let mut request = application::publication_request(
            &project,
            &node_id,
            &edited,
            correspondence,
            confirmed_deletions,
            destination,
            review_token,
        )?;
        request["newPrimPaths"] = serde_json::json!(new_prim_paths.unwrap_or_default());
        request["liveReview"] = true.into();
        request["overwrite"] = serde_json::json!(overwrite.unwrap_or(false));
        service.request(request)
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn load_application_profiles(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    let path = catalog_path(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        Ok(serde_json::json!({"path":path,"catalog":application_profiles::load(&path)?}))
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn save_application_profiles(
    app: tauri::AppHandle,
    catalog: application_profiles::Catalog,
    socket_holder: tauri::State<'_, MegascansSocketHolder>,
) -> Result<(), String> {
    let path = catalog_path(&app)?;
    let cfg = catalog.megascans_library.clone();
    tauri::async_runtime::spawn_blocking(move || application_profiles::save(&path, &catalog))
        .await
        .map_err(|e| e.to_string())??;
    restart_megascans_socket(&app, socket_holder.inner(), &cfg);
    Ok(())
}
#[tauri::command]
async fn inspect_blender_file(
    app: tauri::AppHandle,
    blend_path: String,
) -> Result<Vec<project::BlenderObjectInfo>, String> {
    let _ = app.emit(
        "renderer-status",
        serde_json::json!({
            "phase": format!(
                "Blender: Inspecting {}",
                std::path::Path::new(&blend_path)
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
            )
        }),
    );
    let path = catalog_path(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        let catalog = application_profiles::load(&path)?;
        let executable = blender_node::find_blender_executable(&catalog)?;
        blender_node::inspect_blender_file(&executable, std::path::Path::new(&blend_path))
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn export_blender_to_usd(
    app: tauri::AppHandle,
    blend_path: String,
    usd_path: String,
    selected_objects: Option<Vec<String>>,
    scene_name: Option<String>,
) -> Result<(), String> {
    let _ = app.emit(
        "renderer-status",
        serde_json::json!({
            "phase": format!(
                "Blender: Exporting to {}",
                std::path::Path::new(&usd_path)
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
            )
        }),
    );
    let path = catalog_path(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        let catalog = application_profiles::load(&path)?;
        let executable = blender_node::find_blender_executable(&catalog)?;
        blender_node::export_blender_to_usd(
            &executable,
            std::path::Path::new(&blend_path),
            std::path::Path::new(&usd_path),
            selected_objects.as_deref(),
            scene_name.as_deref(),
        )
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn sp_bridge_status() -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(substance_painter::bridge_status)
        .await
        .map_err(|e| e.to_string())
}

#[tauri::command]
async fn install_sp_plugin(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    let config = catalog_path(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        substance_painter::install_sp_plugin(&application_profiles::load(&config)?)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn create_sp_project(
    project: Project,
    node_id: String,
    path: String,
    overwrite: Option<bool>,
    app: tauri::AppHandle,
) -> Result<serde_json::Value, String> {
    let config = catalog_path(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        substance_painter::create_sp_project(
            &project,
            &node_id,
            &path,
            overwrite.unwrap_or(false),
            &application_profiles::load(&config)?,
        )
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn open_sp_project(path: String, app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    let config = catalog_path(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        substance_painter::open_sp_project(&path, &application_profiles::load(&config)?)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn sync_sp_materials(
    project: Project,
    node_id: String,
    export_dir: Option<String>,
    manifest_path: Option<String>,
    app: tauri::AppHandle,
    service: tauri::State<'_, std::sync::Arc<usd::UsdService>>,
) -> Result<serde_json::Value, String> {
    let config = catalog_path(&app)?;
    let service = service.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        substance_painter::sync_sp_materials(
            &project,
            &node_id,
            export_dir.as_deref(),
            manifest_path.as_deref(),
            &application_profiles::load(&config)?,
            &service,
        )
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn create_blender_project(
    project: Project,
    node_id: String,
    path: String,
    overwrite: Option<bool>,
    capture_baseline: Option<bool>,
    overwrite_baseline: Option<bool>,
    app: tauri::AppHandle,
) -> Result<serde_json::Value, String> {
    let config = catalog_path(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        application::create_blender_project(
            &project,
            &node_id,
            &path,
            overwrite.unwrap_or(false),
            capture_baseline.unwrap_or(false),
            overwrite_baseline.unwrap_or(false),
            &application_profiles::load(&config)?,
        )
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn reuse_handoff(
    app: tauri::AppHandle,
    project: Project,
    node_id: String,
    path: String,
    service: tauri::State<'_, std::sync::Arc<usd::UsdService>>,
) -> Result<serde_json::Value, String> {
    let config = catalog_path(&app)?;
    let service = service.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        service.request(application::reuse_handoff_request(
            &project,
            &node_id,
            &path,
            &application_profiles::load(&config)?,
        )?)
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn create_handoff(
    app: tauri::AppHandle,
    project: Project,
    node_id: String,
    path: String,
    overwrite: Option<bool>,
    quickstart_native_path: Option<String>,
    service: tauri::State<'_, std::sync::Arc<usd::UsdService>>,
) -> Result<serde_json::Value, String> {
    let config = catalog_path(&app)?;
    let service = service.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        if let Some(native_path) = quickstart_native_path {
            application::validate_quickstart_paths(&project, &native_path, &path)?;
        }
        let mut request = application::handoff_request(
            &project,
            &node_id,
            &path,
            &application_profiles::load(&config)?,
        )?;
        request["overwrite"] = serde_json::json!(overwrite.unwrap_or(false));
        service.request(request)
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn open_native_project(
    app: tauri::AppHandle,
    project: Project,
    node_id: String,
) -> Result<u32, String> {
    let config = catalog_path(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        application::open(&project, &node_id, &application_profiles::load(&config)?)
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn md_bridge_status() -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let base = std::env::var_os("LOCALAPPDATA")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(std::env::temp_dir);
        let bridge_dir = base.join("Sublayer").join("md_bridge");
        let manifest_path = bridge_dir.join("manifest.json");
        let handoff_manifest_path = bridge_dir.join("handoff_manifest.json");
        let scripts_root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .map(|p| p.join("scripts").join("md_bridge"));
        let script_path = scripts_root
            .as_ref()
            .map(|p| p.join("sublayer_send_garment.py"))
            .unwrap_or_else(|| {
                std::path::PathBuf::from("scripts/md_bridge/sublayer_send_garment.py")
            });
        let pull_script_path = scripts_root
            .as_ref()
            .map(|p| p.join("sublayer_pull_avatar.py"))
            .unwrap_or_else(|| {
                std::path::PathBuf::from("scripts/md_bridge/sublayer_pull_avatar.py")
            });

        let mut available = false;
        let mut project_path = String::new();
        let mut project_name = "Untitled".to_string();
        let mut export_path = String::new();
        let mut exported_at = 0u64;
        if let Ok(raw) = std::fs::read_to_string(&manifest_path) {
            if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&raw) {
                export_path = parsed
                    .get("exportPath")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                available = !export_path.is_empty() && std::path::Path::new(&export_path).is_file();
                project_path = parsed
                    .get("projectPath")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                project_name = parsed
                    .get("projectName")
                    .and_then(|v| v.as_str())
                    .unwrap_or("Untitled")
                    .to_string();
                exported_at = parsed
                    .get("exportedAt")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0);
            }
        }

        let mut newest: Option<(std::time::SystemTime, std::path::PathBuf)> = None;
        if let Ok(entries) = std::fs::read_dir(&bridge_dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                if !path.is_file() {
                    continue;
                }
                let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
                let lower = name.to_ascii_lowercase();
                let is_garment = [
                    "garment.abc",
                    "garment.obj",
                    "garment.usd",
                    "garment.usdc",
                    "garment.usda",
                ]
                .contains(&lower.as_str())
                    || lower.ends_with("_garment.abc")
                    || lower.ends_with("_garment.obj")
                    || lower.ends_with("_garment.usd")
                    || lower.ends_with("_garment.usdc")
                    || lower.ends_with("_garment.usda");
                if is_garment {
                    if let Ok(meta) = entry.metadata() {
                        if let Ok(modified) = meta.modified() {
                            if newest.as_ref().is_none_or(|(prev, _)| modified > *prev) {
                                newest = Some((modified, path));
                            }
                        }
                    }
                }
            }
        }
        if let Some((modified, path)) = newest {
            let newest_ms = modified
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_millis() as u64)
                .unwrap_or(0);
            if !available || newest_ms > exported_at {
                available = true;
                export_path = path.to_string_lossy().into_owned();
                exported_at = newest_ms;
            }
        }

        let mut handoff_available = false;
        let mut handoff_path = String::new();
        let mut handoff_exported_at = 0u64;
        let mut handoff_abc_available = false;
        let mut handoff_abc_path = String::new();
        let mut handoff_abc_exported_at = 0u64;
        if let Ok(raw) = std::fs::read_to_string(&handoff_manifest_path) {
            if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&raw) {
                handoff_path = parsed
                    .get("handoffPath")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                handoff_available =
                    !handoff_path.is_empty() && std::path::Path::new(&handoff_path).is_file();
                handoff_exported_at = parsed
                    .get("exportedAt")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0);
                handoff_abc_path = parsed
                    .get("handoffAbcPath")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                handoff_abc_available = !handoff_abc_path.is_empty()
                    && std::path::Path::new(&handoff_abc_path).is_file();
                handoff_abc_exported_at = parsed
                    .get("handoffAbcExportedAt")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0);
            }
        }

        Ok(serde_json::json!({
            "available": available,
            "bridgeDir": bridge_dir,
            "scriptPath": script_path,
            "pullScriptPath": pull_script_path,
            "projectPath": project_path,
            "projectName": project_name,
            "exportPath": export_path,
            "exportedAt": exported_at,
            "handoffAvailable": handoff_available,
            "handoffPath": handoff_path,
            "handoffExportedAt": handoff_exported_at,
            "handoffAbcAvailable": handoff_abc_available,
            "handoffAbcPath": handoff_abc_path,
            "handoffAbcExportedAt": handoff_abc_exported_at,
        }))
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn open_md_bridge_folder() -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let base = std::env::var_os("LOCALAPPDATA")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(std::env::temp_dir);
        let bridge_dir = base.join("Sublayer").join("md_bridge");
        std::fs::create_dir_all(&bridge_dir).map_err(|e| e.to_string())?;
        #[cfg(windows)]
        {
            std::process::Command::new("explorer.exe")
                .arg(&bridge_dir)
                .spawn()
                .map_err(|e| format!("Failed to open MD Bridge folder: {e}"))?;
        }
        Ok(())
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn send_to_md_bridge(
    app: tauri::AppHandle,
    service: tauri::State<'_, std::sync::Arc<usd::UsdService>>,
    project: Project,
    node_id: String,
    base_name: Option<String>,
    format: Option<String>,
    ramp_frames: Option<u32>,
) -> Result<serde_json::Value, String> {
    let config = catalog_path(&app)?;
    let service = service.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        project.validate()?;
        let node = project
            .nodes
            .iter()
            .find(|n| n.id == node_id)
            .ok_or("Application node no longer exists")?;
        let catalog = application_profiles::load(&config)?;
        let policy = catalog.policy(&node.data)?;
        let up_axis = if node.data.axis == "preserve" {
            policy.profile.up_axis.clone()
        } else {
            node.data.axis.clone()
        };
        let meters_per_unit = node
            .data
            .application_meters_per_unit
            .unwrap_or(policy.profile.meters_per_unit);
        let scale = node.data.scale * policy.profile.scale;
        let graph = application::evaluation_graph(&project, &node_id, true)?;
        let req = serde_json::json!({
            "operation": "export_md_bridge_handoff",
            "graph": graph,
            "upAxis": up_axis,
            "metersPerUnit": meters_per_unit,
            "scale": scale,
            "selection": node.data.handoff_selection,
            "baseName": base_name.unwrap_or_else(|| node.data.label.clone()),
            "format": format.unwrap_or_else(|| "obj".to_string()),
            "rampFrames": ramp_frames.unwrap_or(30),
        });
        service.request(req)
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn save_project(path: String, project: Project) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || project::save(&path, &project))
        .await
        .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn find_missing_dependencies(
    folder: String,
    dependencies: Vec<project::Dependency>,
) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        dependency_search::find_missing(&folder, dependencies)
    })
    .await
    .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn find_dependency(
    path: String,
    dependencies: Vec<project::Dependency>,
) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let key = project::file_key(&path);
        dependencies
            .into_iter()
            .find(|dependency| project::file_key(&dependency.path) == key)
            .map(|d| d.id)
    })
    .await
    .map_err(|e| e.to_string())
}
#[tauri::command]
async fn dependency_statuses(
    dependencies: Vec<project::Dependency>,
) -> Result<Vec<serde_json::Value>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        dependencies
            .into_iter()
            .map(|dependency| {
                let status = match std::fs::metadata(&dependency.path) {
                    Ok(meta) if meta.is_file() => "Available",
                    Ok(meta) if meta.is_dir() => "Directory",
                    Ok(_) => "Other",
                    Err(e) if e.kind() == std::io::ErrorKind::NotFound => "Missing",
                    Err(_) => "Unavailable",
                };
                serde_json::json!({"id":dependency.id,"status":status})
            })
            .collect()
    })
    .await
    .map_err(|e| e.to_string())
}
#[tauri::command]
async fn load_project(path: String) -> Result<Project, String> {
    tauri::async_runtime::spawn_blocking(move || project::load(&path))
        .await
        .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn usd_request(
    service: tauri::State<'_, std::sync::Arc<usd::UsdService>>,
    request: serde_json::Value,
) -> Result<serde_json::Value, String> {
    if request
        .get("operation")
        .and_then(serde_json::Value::as_str)
        .is_some_and(|op| {
            [
                "handoff_export",
                "review_publication",
                "publish_reviewed",
                "reconcile_upstream",
            ]
            .contains(&op)
        })
    {
        return Err("Use the dedicated handoff or reviewed-publication action".into());
    }
    let service = service.inner().clone();
    tauri::async_runtime::spawn_blocking(move || service.request(request))
        .await
        .map_err(|e| e.to_string())?
}
#[tauri::command]
async fn render_viewport_frame(
    service: tauri::State<'_, std::sync::Arc<usd::UsdService>>,
    request: serde_json::Value,
) -> Result<tauri::ipc::Response, String> {
    let service = service.inner().clone();
    let (meta, image_bytes) =
        tauri::async_runtime::spawn_blocking(move || service.request_binary(request))
            .await
            .map_err(|e| e.to_string())??;
    let meta_bytes = serde_json::to_vec(&meta).map_err(|e| e.to_string())?;
    let meta_len = u32::try_from(meta_bytes.len())
        .map_err(|_| "Viewport frame metadata too large".to_string())?;
    let mut out = Vec::with_capacity(4 + meta_bytes.len() + image_bytes.len());
    out.extend_from_slice(&meta_len.to_le_bytes());
    out.extend_from_slice(&meta_bytes);
    out.extend_from_slice(&image_bytes);
    Ok(tauri::ipc::Response::new(out))
}
type BridgeHolder =
    std::sync::Arc<std::sync::Mutex<Option<std::sync::Arc<desktop_bridge::DesktopBridge>>>>;

#[tauri::command]
fn mcp_bridge_reply(
    request_id: String,
    ok: bool,
    result: Option<serde_json::Value>,
    error: Option<String>,
    bridge: tauri::State<'_, BridgeHolder>,
) -> Result<(), String> {
    let guard = bridge
        .lock()
        .map_err(|_| "Desktop bridge lock poisoned".to_string())?;
    if let Some(active) = guard.as_ref() {
        active.resolve(&request_id, ok, result, error);
    }
    Ok(())
}

#[tauri::command]
fn open_hair_groom_window(app: tauri::AppHandle) -> Result<(), String> {
    let handle = app.clone();
    app.run_on_main_thread(move || {
        if let Some(win) = handle.get_webview_window("hair-groom") {
            let _ = win.show();
            let _ = win.set_focus();
            return;
        }
        let _ = tauri::WebviewWindowBuilder::new(
            &handle,
            "hair-groom",
            tauri::WebviewUrl::App("index.html".into()),
        )
        .title("OrchFlow Hair Grooming & Live Physics")
        .inner_size(1280.0, 860.0)
        .min_inner_size(720.0, 480.0)
        .background_color(tauri::window::Color(17, 19, 23, 255))
        .additional_browser_args("--disable-gpu --disable-gpu-compositing")
        .initialization_script("window.__ORCHFLOW_HAIR_GROOM__ = true;")
        .build();
    })
    .map_err(|e| e.to_string())
}

#[tauri::command]
async fn close_hair_groom_window(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_webview_window("hair-groom") {
        let _ = win.close();
    }
    Ok(())
}

#[tauri::command]
fn get_window_hwnd(window: tauri::WebviewWindow) -> Result<isize, String> {
    #[cfg(target_os = "windows")]
    {
        window
            .hwnd()
            .map(|h| h.0 as isize)
            .map_err(|e| e.to_string())
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = window;
        Ok(0)
    }
}

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct MetahumanCompanionResult {
    role: Option<String>,
    companion_path: Option<String>,
}

fn find_dna_case_insensitive(dir: &std::path::Path, target_lower: &str) -> Option<String> {
    let entries = std::fs::read_dir(dir).ok()?;
    for entry in entries.flatten() {
        let p = entry.path();
        if p.is_file() {
            if let Some(name) = p.file_name().and_then(|n| n.to_str()) {
                if name.to_ascii_lowercase() == target_lower {
                    return Some(p.to_string_lossy().to_string());
                }
            }
        }
    }
    None
}

fn find_unique_dna_in_dir(dir: &std::path::Path) -> Option<String> {
    let entries = std::fs::read_dir(dir).ok()?;
    let mut found = None;
    for entry in entries.flatten() {
        let p = entry.path();
        if p.is_file() {
            if p.extension()
                .and_then(|e| e.to_str())
                .map(|e| e.eq_ignore_ascii_case("dna"))
                .unwrap_or(false)
            {
                if found.is_some() {
                    return None;
                }
                found = Some(p.to_string_lossy().to_string());
            }
        }
    }
    found
}

#[tauri::command]
fn find_metahuman_companion_dna(path: String) -> MetahumanCompanionResult {
    let p = std::path::Path::new(path.trim());
    let Some(parent) = p.parent() else {
        return MetahumanCompanionResult {
            role: None,
            companion_path: None,
        };
    };
    let stem = p
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_string();
    let stem_lower = stem.to_ascii_lowercase();
    let parent_lower = parent
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();

    let is_head = stem_lower == "head"
        || stem_lower == "face"
        || stem_lower.contains("head")
        || stem_lower.contains("face")
        || (!stem_lower.contains("body") && (parent_lower == "head" || parent_lower == "face"));
    let is_body =
        !is_head && (stem_lower == "body" || stem_lower.contains("body") || parent_lower == "body");

    if !is_head && !is_body {
        return MetahumanCompanionResult {
            role: None,
            companion_path: None,
        };
    }

    let role = if is_head { "head" } else { "body" };
    let mut candidates: Vec<String> = Vec::new();
    if is_head {
        if stem_lower.contains("head") {
            candidates.push(format!("{}.dna", stem_lower.replacen("head", "body", 1)));
        }
        if stem_lower.contains("face") {
            candidates.push(format!("{}.dna", stem_lower.replacen("face", "body", 1)));
        }
        candidates.push("body.dna".to_string());
    } else {
        if stem_lower.contains("body") {
            candidates.push(format!("{}.dna", stem_lower.replacen("body", "head", 1)));
            candidates.push(format!("{}.dna", stem_lower.replacen("body", "face", 1)));
        }
        candidates.push("head.dna".to_string());
        candidates.push("face.dna".to_string());
    }

    // 1. Check same directory
    for cand in &candidates {
        if let Some(found) = find_dna_case_insensitive(parent, cand) {
            if !found.eq_ignore_ascii_case(path.trim()) {
                return MetahumanCompanionResult {
                    role: Some(role.to_string()),
                    companion_path: Some(found),
                };
            }
        }
    }

    // 2. Check sibling folder (e.g. ../Body/body.dna <-> ../Head/head.dna or ../Face/head.dna)
    if let Some(grandparent) = parent.parent() {
        let sibling_dirs: &[&str] = if is_head {
            &["body"]
        } else {
            &["head", "face"]
        };
        if let Ok(entries) = std::fs::read_dir(grandparent) {
            for entry in entries.flatten() {
                let sp = entry.path();
                if !sp.is_dir() {
                    continue;
                }
                let sname = sp
                    .file_name()
                    .and_then(|n| n.to_str())
                    .unwrap_or("")
                    .to_ascii_lowercase();
                if sibling_dirs.contains(&sname.as_str()) {
                    for cand in &candidates {
                        if let Some(found) = find_dna_case_insensitive(&sp, cand) {
                            return MetahumanCompanionResult {
                                role: Some(role.to_string()),
                                companion_path: Some(found),
                            };
                        }
                    }
                    if let Some(found) = find_unique_dna_in_dir(&sp) {
                        return MetahumanCompanionResult {
                            role: Some(role.to_string()),
                            companion_path: Some(found),
                        };
                    }
                }
            }
        }
    }

    MetahumanCompanionResult {
        role: Some(role.to_string()),
        companion_path: None,
    }
}

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct StartupLaunchOptions {
    open_last_project: bool,
    project_path: Option<String>,
}

#[tauri::command]
fn startup_launch_options() -> StartupLaunchOptions {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut open_last_project = false;
    let mut project_path = None;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--last-project" | "-l" => open_last_project = true,
            "--project" | "-p" => {
                if let Some(next) = args.get(i + 1) {
                    project_path = Some(next.clone());
                    i += 1;
                }
            }
            other if !other.starts_with('-') && project_path.is_none() => {
                project_path = Some(other.to_string());
            }
            _ => {}
        }
        i += 1;
    }
    StartupLaunchOptions {
        open_last_project,
        project_path,
    }
}

type MegascansSocketHolder =
    std::sync::Arc<std::sync::Mutex<Option<megascans::MegascansSocketServer>>>;

fn megascans_config_path(app: &tauri::AppHandle) -> Result<std::path::PathBuf, String> {
    app.path()
        .app_config_dir()
        .map(|p| p.join("applications.json"))
        .map_err(|e| e.to_string())
}

fn restart_megascans_socket(
    app: &tauri::AppHandle,
    holder: &MegascansSocketHolder,
    cfg: &megascans::MegascansConfig,
) {
    if let Ok(mut guard) = holder.lock() {
        if let Some(old) = guard.take() {
            old.stop();
        }
        if cfg.socket_enabled {
            let emit_handle = app.clone();
            if let Ok(srv) =
                megascans::MegascansSocketServer::start(cfg.socket_port, move |assets| {
                    let _ = emit_handle.emit("megascans-socket-import", assets);
                })
            {
                *guard = Some(srv);
            }
        }
    }
}

#[tauri::command]
fn load_megascans_config(app: tauri::AppHandle) -> Result<megascans::MegascansConfig, String> {
    let path = megascans_config_path(&app)?;
    megascans::load_config(&path)
}

#[tauri::command]
async fn open_fab_browser(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("fab-browser") {
        window.show().map_err(|e| e.to_string())?;
        return window.set_focus().map_err(|e| e.to_string());
    }
    let cfg = megascans::load_config(&megascans_config_path(&app)?)?;
    cfg.validate()?;
    if cfg.library_root.is_empty() {
        return Err("Set your library folder in Applications".into());
    }
    let profile = app
        .path()
        .app_local_data_dir()
        .map_err(|e| e.to_string())?
        .join("fab-webview");
    std::fs::create_dir_all(&profile).map_err(|e| e.to_string())?;
    let folder_label = serde_json::to_string(&format!("Downloads save to {}", cfg.library_root))
        .map_err(|e| e.to_string())?;
    let script = format!(
        r#"
(() => {{
  if (window.top !== window || !/(^|\.)fab\.com$/i.test(location.hostname)) return;
  const mount = () => {{
    if (!document.body || document.getElementById('sublayer-fab-bar')) return;
    const bar = document.createElement('div'); bar.id = 'sublayer-fab-bar';
    Object.assign(bar.style, {{position:'fixed',bottom:'12px',left:'12px',zIndex:'2147483647',
      background:'#142126',color:'#e5f3ed',padding:'8px',borderRadius:'8px',display:'flex',gap:'8px',font:'12px sans-serif',maxWidth:'90vw',flexWrap:'wrap'}});
    for (const [label, action] of [['Back',()=>history.back()],['Forward',()=>history.forward()],['Fab Home',()=>location.assign('https://www.fab.com/')]]) {{
      const button=document.createElement('button'); button.textContent=label;
      button.onclick=action; bar.append(button);
    }}
    const status=document.createElement('span'); status.textContent={folder_label}; bar.append(status);
    document.body.append(bar);
  }};
  if (document.readyState==='loading') document.addEventListener('DOMContentLoaded',mount); else mount();
}})();
"#
    );
    let download_app = app.clone();
    let downloads = std::sync::Arc::new(std::sync::Mutex::new(std::collections::HashMap::<
        std::path::PathBuf,
        Option<String>,
    >::new()));
    let download_context = downloads.clone();
    let builder = tauri::WebviewWindowBuilder::new(
        &app,
        "fab-browser",
        tauri::WebviewUrl::External("https://www.fab.com/".parse().unwrap()),
    )
    .title("Fab Browser")
    .inner_size(1200., 850.)
    .data_directory(profile)
    .initialization_script(script)
    .on_download(move |webview, event| {
        use tauri::webview::DownloadEvent;
        match event {
            DownloadEvent::Requested { destination, .. } => {
                let result = (|| -> Result<(), String> {
                    let cfg = megascans::load_config(&megascans_config_path(&download_app)?)?;
                    cfg.validate()?;
                    if cfg.library_root.is_empty() {
                        return Err("Set your library folder in Applications".into());
                    }
                    let incoming = megascans::library_subdirectory(
                        std::path::Path::new(&cfg.library_root),
                        "Downloaded/Fab/_incoming",
                    )?;
                    let name = destination
                        .file_name()
                        .ok_or("Download filename missing")?
                        .to_string_lossy();
                    *destination = incoming.join(format!("{}_{}", uuid::Uuid::new_v4(), name));
                    let listing = webview
                        .url()
                        .ok()
                        .and_then(|url| megascans::parse_fab_or_asset_url(url.as_str(), "").0);
                    download_context
                        .lock()
                        .map_err(|e| e.to_string())?
                        .insert(destination.clone(), listing);
                    Ok(())
                })();
                if let Err(error) = result {
                    let _ =
                        download_app.emit("fab-download-error", serde_json::json!({"error":error}));
                    return false;
                }
            }
            DownloadEvent::Finished { path, success, .. } => {
                let handle = download_app.clone();
                let listing = path
                    .as_ref()
                    .and_then(|p| download_context.lock().ok()?.remove(p))
                    .flatten();
                std::thread::spawn(move || {
                    let drop_id = uuid::Uuid::new_v4().to_string();
                    let result = (|| -> Result<megascans::MegascansAssetSummary, String> {
                        if !success {
                            return Err("Fab download did not complete".into());
                        }
                        let path = path.ok_or("Fab download completed without a local path")?;
                        let root = path
                            .parent()
                            .and_then(|p| p.parent())
                            .and_then(|p| p.parent())
                            .and_then(|p| p.parent())
                            .ok_or("Invalid incoming download path")?;
                        let name = path
                            .file_stem()
                            .and_then(|v| v.to_str())
                            .unwrap_or("Fab Asset");
                        let title = name.get(37..).unwrap_or(name);
                        let mut asset = megascans::ingest_downloaded_file_into_central_library(
                            &path,
                            root,
                            listing.as_deref(),
                            None,
                            title,
                        )?;
                        if asset.listing_uid.is_none() {
                            asset.listing_uid = listing;
                        }
                        std::fs::remove_file(path).map_err(|e| e.to_string())?;
                        Ok(asset)
                    })();
                    match result {
                        Ok(asset) => {
                            let _ = handle.emit(
                                "fab-download-ready",
                                serde_json::json!({"dropId":drop_id,"asset":asset}),
                            );
                            if let Some(main) = handle.get_webview_window("main") {
                                let _ = main.show();
                                let _ = main.set_focus();
                            }
                        }
                        Err(error) => {
                            let _ = handle.emit(
                                "fab-download-error",
                                serde_json::json!({"dropId":drop_id,"error":error}),
                            );
                        }
                    }
                });
            }
            _ => {}
        }
        true
    });
    builder.build().map_err(|e| e.to_string())?;
    let session_app = app.clone();
    let emit_app = app.clone();
    let watches = app
        .state::<std::sync::Arc<megascans::FabWatchRegistry>>()
        .inner()
        .clone();
    megascans::watch_browser_session(
        cfg,
        move || session_app.get_webview_window("fab-browser").is_some(),
        move || !watches.has_pending(),
        move |result| {
            let drop_id = uuid::Uuid::new_v4().to_string();
            match result {
                Ok(asset) => {
                    let _ = emit_app.emit(
                        "fab-download-ready",
                        serde_json::json!({"dropId":drop_id,"asset":asset}),
                    );
                    if let Some(main) = emit_app.get_webview_window("main") {
                        let _ = main.show();
                        let _ = main.set_focus();
                    }
                }
                Err(error) => {
                    let _ = emit_app.emit(
                        "fab-download-error",
                        serde_json::json!({"dropId":drop_id,"error":error}),
                    );
                }
            }
        },
    );
    Ok(())
}

#[tauri::command]
async fn scan_megascans_library(
    library_root: Option<String>,
    app: tauri::AppHandle,
) -> Result<Vec<megascans::MegascansAssetSummary>, String> {
    let cfg_path = megascans_config_path(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        let root = match library_root.filter(|s| !s.trim().is_empty()) {
            Some(r) => std::path::PathBuf::from(r),
            None => std::path::PathBuf::from(megascans::load_config(&cfg_path)?.library_root),
        };
        megascans::scan_library(&root)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn inspect_megascans_path(
    path: String,
    app: tauri::AppHandle,
) -> Result<megascans::MegascansAssetSummary, String> {
    let cfg_path = megascans_config_path(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        let p = std::path::PathBuf::from(&path);
        let ext = p
            .extension()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        if ext == "zip" {
            let cfg = megascans::load_config(&cfg_path)?;
            let lib_root = std::path::PathBuf::from(&cfg.library_root);
            let stem = p.file_stem().and_then(|s| s.to_str()).unwrap_or("Asset");
            return megascans::ingest_downloaded_file_into_central_library(
                &p, &lib_root, None, None, stem,
            );
        }
        megascans::inspect_asset_dir_or_file(&p, None)
            .ok_or_else(|| format!("No supported Megascans / Fab mesh or PBR maps found at {path}"))
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn read_megascans_thumbnail(path: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || {
        megascans::read_thumbnail_data_uri(std::path::Path::new(&path))
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn resolve_or_watch_fab_drop(
    url: String,
    title_hint: String,
    drop_id: String,
    app: tauri::AppHandle,
    watches: tauri::State<'_, std::sync::Arc<megascans::FabWatchRegistry>>,
) -> Result<megascans::FabDropOutcome, String> {
    let cfg_path = megascans_config_path(&app)?;
    let watches = watches.inner().clone();
    let app_handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let cfg = megascans::load_config(&cfg_path)?;
        let lib_root = std::path::PathBuf::from(&cfg.library_root);
        if !lib_root.is_dir() {
            return Err(
                "Configure a valid central Megascans/Fab folder first (e.g. I:\\EpicGames\\Megascans)."
                    .into(),
            );
        }

        let (listing_uid, direct_ext, title) = megascans::parse_fab_or_asset_url(&url, &title_hint);

        // 1. Instant hit inside the central library
        if let Some(existing) =
            megascans::match_existing_library_asset(&lib_root, listing_uid.as_deref(), &title)
        {
            return Ok(megascans::FabDropOutcome {
                status: "ready".into(),
                drop_id,
                title: existing.name.clone(),
                url,
                message: format!("Loaded \"{}\" from central library.", existing.name),
                asset: Some(existing),
            });
        }

        // 2. Direct downloadable file URL (.glb, .usdz, .hdr, .zip) -> fetch directly into central library
        let is_fab_page = url.to_ascii_lowercase().contains("fab.com/listings/");
        if !is_fab_page {
            if let Some(ext) = direct_ext {
                let downloaded =
                    megascans::download_direct_asset_url(&url, &ext, &lib_root, &title)?;
                return Ok(megascans::FabDropOutcome {
                    status: "ready".into(),
                    drop_id,
                    title: downloaded.name.clone(),
                    url,
                    message: format!("Downloaded \"{}\" into central library.", downloaded.name),
                    asset: Some(downloaded),
                });
            }
        }

        // 3. Fab listing webpage drop -> watch browser Downloads and auto-extract into central library
        let mut dl_dirs: Vec<_> = cfg.downloads_dirs.iter().filter(|s| !s.is_empty()).map(std::path::PathBuf::from).collect();
        dl_dirs.push(lib_root.clone());
        if !dl_dirs.iter().any(|p| p.is_dir()) {
            return Err(
                "Browser Downloads folder could not be found. Set your Downloads path in the Megascans / Fab browser."
                    .into(),
            );
        }

        let did_emit = drop_id.clone();
        watches.start_watch(
            drop_id.clone(),
            dl_dirs,
            lib_root.clone(),
            listing_uid,
            url.clone(),
            title.clone(),
            move |res| match res {
                Ok(asset) => {
                    let _ = app_handle.emit(
                        "fab-download-ready",
                        serde_json::json!({ "dropId": did_emit, "asset": asset }),
                    );
                }
                Err(err) => {
                    let _ = app_handle.emit(
                        "fab-download-error",
                        serde_json::json!({ "dropId": did_emit, "error": err }),
                    );
                }
            },
        );

        Ok(megascans::FabDropOutcome {
            status: "watching".into(),
            drop_id,
            title: title.clone(),
            url,
            asset: None,
            message: format!(
                "Waiting for \"{title}\" — click Download in your browser tab; Sublayer will unpack it into {} automatically.",
                lib_root.display()
            ),
        })
    })
    .await
    .map_err(|e| e.to_string())?
}

#[tauri::command]
fn cancel_fab_drop_watch(
    drop_id: String,
    watches: tauri::State<'_, std::sync::Arc<megascans::FabWatchRegistry>>,
) {
    watches.cancel(&drop_id);
}

#[tauri::command]
async fn ingest_dropped_local_file(
    file_name: String,
    file_size: u64,
    project_dir: Option<String>,
    app: tauri::AppHandle,
) -> Result<megascans::MegascansAssetSummary, String> {
    let cfg_path = megascans_config_path(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        let cfg = megascans::load_config(&cfg_path)?;
        megascans::resolve_dropped_local_file(&file_name, file_size, &cfg, project_dir.as_deref())
    })
    .await
    .map_err(|e| e.to_string())?
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let service = std::sync::Arc::new(usd::UsdService::default());
    let shutdown = service.clone();
    let service_setup = service.clone();
    let bridge_holder: BridgeHolder = std::sync::Arc::new(std::sync::Mutex::new(None));
    let bridge_setup = bridge_holder.clone();
    let fab_watches = std::sync::Arc::new(megascans::FabWatchRegistry::default());
    let ms_socket_holder: MegascansSocketHolder = std::sync::Arc::new(std::sync::Mutex::new(None));
    let ms_socket_setup = ms_socket_holder.clone();
    let ms_socket_shutdown = ms_socket_holder.clone();
    tauri::Builder::default()
        .manage(service)
        .manage(bridge_holder)
        .manage(fab_watches)
        .manage(ms_socket_holder)
        .plugin(tauri_plugin_dialog::init())
        .setup(move |app| {
            let handle = app.handle().clone();
            service_setup.set_status_listener(move |payload| {
                if let Some(popup_evt) = payload.get("popupInput") {
                    let _ = handle.emit("hair-popup-input", popup_evt.clone());
                } else {
                    let _ = handle.emit("renderer-status", payload);
                }
            });
            let warm_service = service_setup.clone();
            std::thread::spawn(move || {
                let _ = warm_service.warm();
            });
            let bridge_handle = app.handle().clone();
            if let Ok(bridge) = desktop_bridge::DesktopBridge::start(move |payload| {
                bridge_handle
                    .emit("orchflow://mcp-command", payload)
                    .map_err(|e| e.to_string())
            }) {
                if let Ok(mut guard) = bridge_setup.lock() {
                    *guard = Some(bridge);
                }
            }
            if let Ok(cfg_path) = megascans_config_path(app.handle()) {
                if let Ok(cfg) = megascans::load_config(&cfg_path) {
                    restart_megascans_socket(app.handle(), &ms_socket_setup, &cfg);
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            export_materialx,
            materialx_rule_matches,
            inspect_stage_materials,
            inspect_stage_lights,
            reconcile_application_upstream,
            authorize_publication_scope,
            review_application_publication,
            apply_live_adjustment,
            export_live_output,
            validate_manual_baseline,
            load_application_profiles,
            save_application_profiles,
            create_blender_project,
            sp_bridge_status,
            install_sp_plugin,
            create_sp_project,
            open_sp_project,
            sync_sp_materials,
            inspect_blender_file,
            export_blender_to_usd,
            create_handoff,
            reuse_handoff,
            open_native_project,
            md_bridge_status,
            open_md_bridge_folder,
            send_to_md_bridge,
            save_project,
            load_project,
            find_dependency,
            dependency_statuses,
            find_missing_dependencies,
            usd_request,
            render_viewport_frame,
            mcp_bridge_reply,
            open_hair_groom_window,
            close_hair_groom_window,
            get_window_hwnd,
            startup_launch_options,
            find_metahuman_companion_dna,
            load_megascans_config,
            open_fab_browser,
            scan_megascans_library,
            inspect_megascans_path,
            read_megascans_thumbnail,
            resolve_or_watch_fab_drop,
            cancel_fab_drop_watch,
            ingest_dropped_local_file
        ])
        .build(tauri::generate_context!())
        .expect("error while building OrchFlow")
        .run(move |_, event| {
            if let tauri::RunEvent::Exit = event {
                if let Ok(mut guard) = ms_socket_shutdown.lock() {
                    if let Some(srv) = guard.take() {
                        srv.stop();
                    }
                }
                desktop_bridge::DesktopBridge::cleanup_file();
                shutdown.shutdown();
            }
        });
}
