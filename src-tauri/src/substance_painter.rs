//! Substance 3D Painter 2023+ bridge client, plugin installer, project creation,
//! and material-only live adjustment sync.

use crate::application;
use crate::application_profiles::Catalog;
use crate::project::Project;
use crate::usd::UsdService;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

const SP_PLUGIN_SOURCE: &str = include_str!("../../plugins/substance_painter/sublayer_bridge.py");
const DEFAULT_SP_PORT: u16 = 29347;

pub fn bridge_port() -> u16 {
    std::env::var("SUBLAYER_SP_PORT")
        .ok()
        .and_then(|v| v.parse::<u16>().ok())
        .unwrap_or(DEFAULT_SP_PORT)
}

pub fn find_substance_painter_executable(catalog: &Catalog) -> Result<PathBuf, String> {
    if let Some(profile) = catalog
        .applications
        .iter()
        .find(|p| p.id == "substance-painter" || p.app.eq_ignore_ascii_case("Substance Painter"))
    {
        if !profile.executable.trim().is_empty() {
            let configured = PathBuf::from(&profile.executable);
            if configured.is_file() {
                return Ok(configured);
            }
        }
    }

    let candidates = [
        r"C:\Program Files\Adobe\Adobe Substance 3D Painter\Adobe Substance 3D Painter.exe",
        r"C:\Program Files\Steam\steamapps\common\Substance 3D Painter 2023\Adobe Substance 3D Painter.exe",
        r"C:\Program Files (x86)\Steam\steamapps\common\Substance 3D Painter 2023\Adobe Substance 3D Painter.exe",
        r"C:\Program Files\Steam\steamapps\common\Substance 3D Painter\Adobe Substance 3D Painter.exe",
        r"C:\Program Files (x86)\Steam\steamapps\common\Substance 3D Painter\Adobe Substance 3D Painter.exe",
    ];
    for candidate in candidates {
        let p = PathBuf::from(candidate);
        if p.is_file() {
            return Ok(p);
        }
    }

    Err(
        "Substance 3D Painter executable not found. Configure its path in Applications -> Substance Painter."
            .into(),
    )
}

pub fn user_sp_plugins_dir() -> Result<PathBuf, String> {
    let home = std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .map_err(|_| "Unable to determine user home directory for Substance Painter plugins")?;
    Ok(PathBuf::from(home)
        .join("Documents")
        .join("Adobe")
        .join("Adobe Substance 3D Painter")
        .join("python")
        .join("plugins"))
}

pub fn sp_plugins_dir_for_executable(exe: &Path) -> Result<PathBuf, String> {
    if let Some(install_dir) = exe.parent() {
        let bundled_plugins = install_dir.join("resources").join("python").join("plugins");
        if bundled_plugins.is_dir() {
            return Ok(bundled_plugins);
        }
    }
    user_sp_plugins_dir()
}

pub fn install_sp_plugin(catalog: &Catalog) -> Result<serde_json::Value, String> {
    let exe = find_substance_painter_executable(catalog)?;
    let preferred_dir = sp_plugins_dir_for_executable(&exe)?;
    let target = preferred_dir.join("sublayer_bridge.py");

    let (installed_path, used_fallback) = match std::fs::create_dir_all(&preferred_dir)
        .and_then(|_| std::fs::write(&target, SP_PLUGIN_SOURCE))
    {
        Ok(()) => (target, false),
        Err(primary_err) => {
            let fallback_dir = user_sp_plugins_dir()?;
            if fallback_dir == preferred_dir {
                return Err(format!(
                    "Failed to write SP plugin {}: {primary_err}",
                    target.display()
                ));
            }
            std::fs::create_dir_all(&fallback_dir).map_err(|e| {
                format!(
                    "Failed to write SP plugin to {} ({primary_err}) and failed to create fallback {}: {e}",
                    target.display(),
                    fallback_dir.display()
                )
            })?;
            let fallback_target = fallback_dir.join("sublayer_bridge.py");
            std::fs::write(&fallback_target, SP_PLUGIN_SOURCE).map_err(|e| {
                format!(
                    "Failed to write SP plugin to {} ({primary_err}) and fallback {}: {e}",
                    target.display(),
                    fallback_target.display()
                )
            })?;
            (fallback_target, true)
        }
    };

    Ok(serde_json::json!({
        "installed": true,
        "path": installed_path.to_string_lossy().to_string(),
        "executable": exe.to_string_lossy().to_string(),
        "usedFallback": used_fallback,
        "port": bridge_port(),
    }))
}

fn parse_http_json_response(raw: &[u8]) -> Result<(u16, serde_json::Value), String> {
    let text = String::from_utf8_lossy(raw);
    let (header_block, body) = text
        .split_once("\r\n\r\n")
        .or_else(|| text.split_once("\n\n"))
        .ok_or("Malformed HTTP response from Substance Painter bridge")?;
    let status_line = header_block.lines().next().unwrap_or("");
    let status_code = status_line
        .split_whitespace()
        .nth(1)
        .and_then(|s| s.parse::<u16>().ok())
        .ok_or_else(|| format!("Invalid HTTP status line from SP bridge: {status_line}"))?;
    let parsed: serde_json::Value = serde_json::from_str(body.trim())
        .map_err(|e| format!("Invalid JSON from SP bridge (HTTP {status_code}): {e}"))?;
    Ok((status_code, parsed))
}

fn bridge_request(
    method: &str,
    path: &str,
    body: Option<&serde_json::Value>,
    timeout_secs: u64,
) -> Result<serde_json::Value, String> {
    let port = bridge_port();
    let addr: SocketAddr = format!("127.0.0.1:{port}")
        .parse()
        .map_err(|e| format!("Invalid SP bridge address: {e}"))?;
    let mut stream = TcpStream::connect_timeout(&addr, Duration::from_secs(2)).map_err(|e| {
        format!(
            "Could not connect to Substance Painter bridge on 127.0.0.1:{port} ({e}). \
             Make sure Substance Painter is running with the Sublayer Bridge plugin installed."
        )
    })?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(timeout_secs)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(10)));

    let payload = match body {
        Some(v) => serde_json::to_string(v).map_err(|e| e.to_string())?,
        None => String::new(),
    };
    let req = format!(
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{payload}",
        payload.len()
    );
    stream
        .write_all(req.as_bytes())
        .map_err(|e| format!("Failed sending request to SP bridge: {e}"))?;

    let mut response = Vec::new();
    stream
        .read_to_end(&mut response)
        .map_err(|e| format!("Failed reading response from SP bridge: {e}"))?;

    let (status, json) = parse_http_json_response(&response)?;
    if status >= 400 || json.get("ok").and_then(|v| v.as_bool()) == Some(false) {
        let msg = json
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Substance Painter bridge returned an error");
        return Err(msg.to_string());
    }
    Ok(json)
}

pub fn bridge_status() -> serde_json::Value {
    let port = bridge_port();
    match bridge_request("GET", "/health", None, 3) {
        Ok(mut val) => {
            val["connected"] = serde_json::Value::Bool(true);
            val["port"] = serde_json::json!(port);
            val
        }
        Err(err) => serde_json::json!({
            "connected": false,
            "port": port,
            "error": err,
        }),
    }
}

fn ensure_bridge_running(catalog: &Catalog) -> Result<serde_json::Value, String> {
    if let Ok(health) = bridge_request("GET", "/health", None, 3) {
        return Ok(health);
    }

    // Ensure the plugin is installed so SP loads it on launch
    let exe = find_substance_painter_executable(catalog)?;
    let _ = install_sp_plugin(catalog);
    let plugin_dir = sp_plugins_dir_for_executable(&exe)?;

    let mut cmd = Command::new(&exe);
    cmd.env("SUBSTANCE_PAINTER_PLUGINS_PATH", &plugin_dir)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    cmd.spawn().map_err(|e| {
        format!(
            "Failed to launch Substance Painter ({}): {e}",
            exe.display()
        )
    })?;

    let deadline = Instant::now() + Duration::from_secs(30);
    while Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(750));
        if let Ok(health) = bridge_request("GET", "/health", None, 3) {
            return Ok(health);
        }
    }

    Err(
        "Substance Painter was launched, but the Sublayer Bridge plugin did not respond within 30 seconds. \
         Verify the plugin is enabled in Substance Painter's Python menu and try again."
            .into(),
    )
}

pub fn create_sp_project(
    project: &Project,
    node_id: &str,
    spp_path: &str,
    overwrite: bool,
    catalog: &Catalog,
) -> Result<serde_json::Value, String> {
    project.validate()?;
    let node = project
        .nodes
        .iter()
        .find(|n| n.id == node_id)
        .ok_or("Application node no longer exists")?;
    if node.data.operation != "application" {
        return Err("Target node must be an Application node".into());
    }
    let handoff = node
        .data
        .handoff
        .as_ref()
        .ok_or("Create a handoff USD first before creating a Substance Painter project.")?;
    let handoff_dep = project
        .dependencies
        .iter()
        .find(|d| d.id == handoff.dependency_id)
        .ok_or("Handoff USD dependency not found in project registry.")?;
    let handoff_path = Path::new(&handoff_dep.path);
    if !handoff_path.is_file() {
        return Err(format!(
            "Handoff USD file is missing on disk: {}",
            handoff_path.display()
        ));
    }

    let target = Path::new(spp_path);
    if !target.is_absolute() {
        return Err("Substance Painter project path must be absolute.".into());
    }
    if target.exists() && !overwrite {
        return Err(format!(
            "Substance Painter project already exists at {}. Confirm replacement or choose a new path.",
            target.display()
        ));
    }

    let _ = ensure_bridge_running(catalog)?;
    let created = bridge_request(
        "POST",
        "/project/create",
        Some(&serde_json::json!({
            "meshPath": handoff_dep.path,
            "sppPath": spp_path,
            "normalMapFormat": "OpenGL",
            "importCameras": false,
        })),
        120,
    )?;

    let policy = catalog.policy(&node.data)?;
    Ok(serde_json::json!({
        "path": created.get("sppPath").and_then(|v| v.as_str()).unwrap_or(spp_path),
        "handoffDependencyId": handoff.dependency_id,
        "policy": policy,
        "conversion": handoff.conversion,
        "report": {
            "adapterVersion": 1,
            "spVersion": created.get("version").and_then(|v| v.as_str()).unwrap_or("unknown"),
            "textureSets": created.get("textureSets").cloned().unwrap_or_else(|| serde_json::json!([])),
        }
    }))
}

pub fn open_sp_project(spp_path: &str, catalog: &Catalog) -> Result<serde_json::Value, String> {
    let target = Path::new(spp_path);
    if !target.is_file() {
        return Err(format!("Substance Painter project not found: {spp_path}"));
    }
    if let Ok(res) = bridge_request(
        "POST",
        "/project/open",
        Some(&serde_json::json!({ "sppPath": spp_path })),
        60,
    ) {
        return Ok(res);
    }

    let exe = find_substance_painter_executable(catalog)?;
    let _ = install_sp_plugin(catalog);
    let plugin_dir = sp_plugins_dir_for_executable(&exe)?;
    let child = Command::new(&exe)
        .arg(target)
        .env("SUBSTANCE_PAINTER_PLUGINS_PATH", &plugin_dir)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("Failed to launch Substance Painter: {e}"))?;
    Ok(serde_json::json!({
        "ok": true,
        "launched": true,
        "pid": child.id(),
        "sppPath": spp_path,
    }))
}

pub fn sync_sp_materials(
    project: &Project,
    node_id: &str,
    export_dir: Option<&str>,
    manifest_path: Option<&str>,
    catalog: &Catalog,
    usd_service: &UsdService,
) -> Result<serde_json::Value, String> {
    project.validate()?;
    let node = project
        .nodes
        .iter()
        .find(|n| n.id == node_id)
        .ok_or("Application node no longer exists")?;
    if node.data.operation != "application" {
        return Err("Target node must be an Application node".into());
    }

    let (resolved_manifest_path, manifest): (String, serde_json::Value) =
        if let Some(mp) = manifest_path.filter(|s| !s.trim().is_empty()) {
            let raw = std::fs::read_to_string(mp)
                .map_err(|e| format!("Failed to read SP manifest {mp}: {e}"))?;
            let parsed: serde_json::Value =
                serde_json::from_str(&raw).map_err(|e| format!("Invalid SP manifest JSON: {e}"))?;
            (mp.to_string(), parsed)
        } else {
            let dir = export_dir
                .filter(|s| !s.trim().is_empty())
                .ok_or("Choose an export folder for Substance Painter textures.")?;
            let exported = bridge_request(
                "POST",
                "/export/textures",
                Some(&serde_json::json!({ "exportDir": dir })),
                300,
            )?;
            let mp = exported
                .get("manifestPath")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let mf = exported
                .get("manifest")
                .cloned()
                .ok_or("Substance Painter bridge did not return a manifest.")?;
            (mp, mf)
        };

    let graph = application::evaluation_graph(project, node_id, true)?;
    let built = usd_service.request(serde_json::json!({
        "operation": "sp_build_materials",
        "graph": graph,
        "manifest": manifest,
    }))?;
    if let Some(err) = built.get("error").and_then(|v| v.as_str()) {
        return Err(err.to_string());
    }

    let layer = built
        .get("layer")
        .and_then(|v| v.as_str())
        .ok_or("USD worker did not return a sparse material layer.")?;
    let report = built
        .get("report")
        .cloned()
        .unwrap_or_else(|| serde_json::json!({}));

    let policy = catalog.policy(&node.data)?;
    let conversion = node
        .data
        .handoff
        .as_ref()
        .map(|h| serde_json::to_value(&h.conversion).unwrap_or(serde_json::Value::Null))
        .unwrap_or(serde_json::Value::Null);
    let handoff_dep_id = node
        .data
        .handoff
        .as_ref()
        .map(|h| h.dependency_id.clone())
        .unwrap_or_default();
    let exported_files = manifest
        .get("exportedFiles")
        .cloned()
        .unwrap_or_else(|| serde_json::json!([]));

    let revision = format!(
        "sp-{}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis())
            .unwrap_or(0)
    );

    Ok(serde_json::json!({
        "manifestPath": resolved_manifest_path,
        "exportedFiles": exported_files,
        "layer": layer,
        "report": report,
        "policy": policy,
        "conversion": conversion,
        "handoffDependencyId": handoff_dep_id,
        "baselineDependencyId": handoff_dep_id,
        "revision": revision,
        "correspondence": [],
        "guards": {},
        "fileRevisions": [],
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_http_json_response() {
        let raw = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"ok\":true,\"version\":\"8.3.0\"}";
        let (status, json) = parse_http_json_response(raw).unwrap();
        assert_eq!(status, 200);
        assert_eq!(json["version"], "8.3.0");
    }

    #[test]
    fn user_plugins_dir_ends_with_plugins() {
        let dir = user_sp_plugins_dir().unwrap();
        assert!(dir.ends_with("plugins"));
    }
}
