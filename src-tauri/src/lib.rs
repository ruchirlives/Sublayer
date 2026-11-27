mod application_profiles;
use tauri::{Emitter, Manager};
mod application;
pub mod blender_node;
mod dependency_search;
mod desktop_bridge;
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
) -> Result<(), String> {
    let path = catalog_path(&app)?;
    tauri::async_runtime::spawn_blocking(move || application_profiles::save(&path, &catalog))
        .await
        .map_err(|e| e.to_string())?
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

        if !available {
            if let Ok(entries) = std::fs::read_dir(&bridge_dir) {
                let mut newest: Option<(std::time::SystemTime, std::path::PathBuf)> = None;
                for entry in entries.flatten() {
                    let path = entry.path();
                    if !path.is_file() {
                        continue;
                    }
                    let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
                    if name == "garment.obj" || name.ends_with("_garment.obj") {
                        if let Ok(meta) = entry.metadata() {
                            if let Ok(modified) = meta.modified() {
                                if newest.as_ref().is_none_or(|(prev, _)| modified > *prev) {
                                    newest = Some((modified, path));
                                }
                            }
                        }
                    }
                }
                if let Some((modified, path)) = newest {
                    available = true;
                    export_path = path.to_string_lossy().into_owned();
                    exported_at = modified
                        .duration_since(std::time::UNIX_EPOCH)
                        .map(|d| d.as_millis() as u64)
                        .unwrap_or(0);
                }
            }
        }

        let mut handoff_available = false;
        let mut handoff_path = String::new();
        let mut handoff_exported_at = 0u64;
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
        }))
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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let service = std::sync::Arc::new(usd::UsdService::default());
    let shutdown = service.clone();
    let service_setup = service.clone();
    let bridge_holder: BridgeHolder = std::sync::Arc::new(std::sync::Mutex::new(None));
    let bridge_setup = bridge_holder.clone();
    tauri::Builder::default()
        .manage(service)
        .manage(bridge_holder)
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
            find_metahuman_companion_dna
        ])
        .build(tauri::generate_context!())
        .expect("error while building OrchFlow")
        .run(move |_, event| {
            if let tauri::RunEvent::Exit = event {
                desktop_bridge::DesktopBridge::cleanup_file();
                shutdown.shutdown();
            }
        });
}
