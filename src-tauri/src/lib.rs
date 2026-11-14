mod application_profiles;
use tauri::{Emitter, Manager};
mod application;
pub mod blender_node;
mod dependency_search;
mod desktop_bridge;
mod project;
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
    app: tauri::AppHandle,
) -> Result<serde_json::Value, String> {
    let config = catalog_path(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        application::create_blender_project(
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
    service: tauri::State<'_, std::sync::Arc<usd::UsdService>>,
) -> Result<serde_json::Value, String> {
    let config = catalog_path(&app)?;
    let service = service.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
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
                let _ = handle.emit("renderer-status", payload);
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
            inspect_blender_file,
            export_blender_to_usd,
            create_handoff,
            reuse_handoff,
            open_native_project,
            save_project,
            load_project,
            find_dependency,
            dependency_statuses,
            find_missing_dependencies,
            usd_request,
            mcp_bridge_reply
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
