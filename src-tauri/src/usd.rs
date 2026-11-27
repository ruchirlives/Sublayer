//! Serialized, persistent OpenUSD worker service shared by desktop and future MCP.
use serde_json::Value;
use std::{
    io::{BufRead, BufReader, Read, Write},
    process::{Child, ChildStdin, ChildStdout, Command, Stdio},
    sync::{
        Arc, Mutex,
        mpsc::{self, Receiver},
    },
    time::{Duration, Instant},
};

// A venv launcher can spawn a separate base-Python process on Windows.
// Closing this job terminates descendants as well as the launcher.
#[cfg(windows)]
struct WorkerJob(isize);
#[cfg(windows)]
impl WorkerJob {
    fn attach(child: &Child) -> Result<Self, String> {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::{Foundation::CloseHandle, System::JobObjects::*};
        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if handle.is_null() {
                return Err(std::io::Error::last_os_error().to_string());
            }
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as *const _,
                std::mem::size_of_val(&limits) as u32,
            ) == 0
                || AssignProcessToJobObject(handle, child.as_raw_handle()) == 0
            {
                let error = std::io::Error::last_os_error().to_string();
                CloseHandle(handle);
                return Err(format!("Cannot contain USD worker process tree: {error}"));
            }
            Ok(Self(handle as isize))
        }
    }
    fn terminate(&self) {
        unsafe {
            windows_sys::Win32::System::JobObjects::TerminateJobObject(self.0 as _, 1);
        }
    }
}
#[cfg(windows)]
impl Drop for WorkerJob {
    fn drop(&mut self) {
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(self.0 as _);
        }
    }
}

pub type StatusListener = Arc<dyn Fn(Value) + Send + Sync>;

type WorkerResponse = Result<(Value, Option<Vec<u8>>), String>;

fn spawn_stdout_reader(
    output: ChildStdout,
    status_listener: Option<StatusListener>,
    log_prefix: &'static str,
    invalid_msg: &'static str,
) -> Receiver<WorkerResponse> {
    let (sender, responses) = mpsc::sync_channel(1);
    std::thread::spawn(move || {
        let mut reader = BufReader::new(output);
        let mut line_buf = Vec::new();
        loop {
            line_buf.clear();
            match reader.read_until(b'\n', &mut line_buf) {
                Ok(0) => break,
                Ok(_) => {}
                Err(e) => {
                    let _ = sender.send(Err(e.to_string()));
                    break;
                }
            }
            let line = String::from_utf8_lossy(&line_buf);
            let trimmed = line.trim_end_matches(&['\r', '\n'][..]);
            if let Some(payload) = trimmed.strip_prefix("ORCHFLOW_STATUS:") {
                if let Some(ref cb) = status_listener {
                    if let Ok(v) = serde_json::from_str::<Value>(payload) {
                        cb(v);
                    }
                }
            } else if let Some(payload) = trimmed.strip_prefix("ORCHFLOW:") {
                let parsed: Result<Value, String> =
                    serde_json::from_str(payload).map_err(|e| format!("{invalid_msg}: {e}"));
                let msg = match parsed {
                    Ok(value) => {
                        let bin_len = value
                            .get("binaryBytes")
                            .and_then(Value::as_u64)
                            .unwrap_or(0) as usize;
                        if bin_len > 0 {
                            let mut bin_buf = vec![0u8; bin_len];
                            if let Err(e) = reader.read_exact(&mut bin_buf) {
                                Err(format!("Failed to read worker binary frame: {e}"))
                            } else {
                                Ok((value, Some(bin_buf)))
                            }
                        } else {
                            Ok((value, None))
                        }
                    }
                    Err(e) => Err(e),
                };
                if sender.send(msg).is_err() {
                    break;
                }
            } else {
                eprintln!("{log_prefix}: {trimmed}");
            }
        }
    });
    responses
}

fn ensure_worker_script_dir() -> Result<std::path::PathBuf, String> {
    if let Some(dir) = crate::runtime_setup::find_worker_dir() {
        if dir.join("usd_worker.py").is_file() {
            return Ok(dir);
        }
    }
    let files: [(&str, &str); 14] = [
        ("metahuman.py", include_str!("../../worker/metahuman.py")),
        (
            "metahuman_riglogic.py",
            include_str!("../../worker/metahuman_riglogic.py"),
        ),
        (
            "geometry_exports.py",
            include_str!("../../worker/geometry_exports.py"),
        ),
        (
            "procedural_mtlx.py",
            include_str!("../../worker/procedural_mtlx.py"),
        ),
        (
            "hair_colliders.py",
            include_str!("../../worker/hair_colliders.py"),
        ),
        (
            "cloth_kernels.py",
            include_str!("../../worker/cloth_kernels.py"),
        ),
        (
            "cloth_proxy.py",
            include_str!("../../worker/cloth_proxy.py"),
        ),
        ("cache_edit.py", include_str!("../../worker/cache_edit.py")),
        ("usd_graph.py", include_str!("../../worker/usd_graph.py")),
        ("pose_node.py", include_str!("../../worker/pose_node.py")),
        (
            "usd_publish.py",
            include_str!("../../worker/usd_publish.py"),
        ),
        (
            "material_preview.py",
            include_str!("../../worker/material_preview.py"),
        ),
        (
            "viewport_bounds.py",
            include_str!("../../worker/viewport_bounds.py"),
        ),
        ("usd_worker.py", include_str!("../../worker/usd_worker.py")),
    ];
    let mut hash: u64 = 0xcbf29ce484222325;
    for (name, content) in &files {
        for &b in name.as_bytes().iter().chain(content.as_bytes().iter()) {
            hash ^= u64::from(b);
            hash = hash.wrapping_mul(0x100000001b3);
        }
    }
    let base = crate::runtime_setup::runtimes_root()
        .parent()
        .map(|p| p.to_path_buf())
        .unwrap_or_else(std::env::temp_dir);
    let cache_dir = base.join("worker_cache").join(format!("{hash:016x}"));
    if !cache_dir.join("usd_worker.py").is_file() {
        std::fs::create_dir_all(&cache_dir).map_err(|e| e.to_string())?;
        for (name, content) in &files {
            std::fs::write(cache_dir.join(name), content).map_err(|e| e.to_string())?;
        }
    }
    Ok(cache_dir)
}

struct Worker {
    child: Child,
    _scratch: Option<tempfile::TempDir>,
    #[cfg(windows)]
    job: WorkerJob,
    input: ChildStdin,
    responses: Receiver<WorkerResponse>,
}
impl Worker {
    fn start(status_listener: Option<StatusListener>) -> Result<Self, String> {
        let python = crate::runtime_setup::find_imaging_python()
            .unwrap_or_else(|| std::path::PathBuf::from("python"));
        let script_dir = ensure_worker_script_dir()?;
        let worker_script = script_dir.join("usd_worker.py");
        let mut command = Command::new(python);
        command.env("ORCHFLOW_WORKER_DIR", &script_dir);
        if std::env::var_os("ORCHFLOW_USD_ROOT").is_none() {
            if let Some(usd_root) = crate::runtime_setup::find_usd_root() {
                command.env("ORCHFLOW_USD_ROOT", usd_root);
            }
        }
        if std::env::var_os("ORCHFLOW_AURORA_ROOT").is_none() {
            if let Some(aurora_root) = crate::runtime_setup::find_aurora_root() {
                command.env("ORCHFLOW_AURORA_ROOT", aurora_root);
            }
        }
        command
            .arg("-u")
            .arg(&worker_script)
            .arg("--serve")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let mut child = command
            .spawn()
            .map_err(|e| format!("Cannot start USD worker: {e}. Configure ORCHFLOW_PYTHON."))?;
        #[cfg(windows)]
        let job = WorkerJob::attach(&child).inspect_err(|_| {
            let _ = child.kill();
            let _ = child.wait();
        })?;
        let input = child.stdin.take().ok_or("Worker stdin unavailable")?;
        let output = child.stdout.take().ok_or("Worker stdout unavailable")?;
        let responses =
            spawn_stdout_reader(output, status_listener, "USD", "Invalid worker response");
        Ok(Self {
            child,
            _scratch: None,
            #[cfg(windows)]
            job,
            input,
            responses,
        })
    }
    fn start_ovrtx(status_listener: Option<StatusListener>) -> Result<Self, String> {
        let python = find_ovrtx_python().ok_or_else(|| {
            "Cannot find Omniverse RTX Python executable. Run Sublayer Runtime Setup or scripts/setup-ovrtx.ps1."
                .to_string()
        })?;
        let script = find_ovrtx_worker_script();
        let runtime = crate::runtime_setup::find_ovrtx_root();
        let scratch_root = runtime.join("scratch");
        let scratch = std::fs::create_dir_all(&scratch_root)
            .and_then(|_| {
                tempfile::Builder::new()
                    .prefix("preview-")
                    .tempdir_in(&scratch_root)
            })
            .map_err(|e| {
                format!(
                    "Cannot create NVIDIA preview folder in {}: {e}",
                    scratch_root.display()
                )
            })?;
        let mut command = Command::new(python);
        if let Some(parent) = script.parent().and_then(|p| p.parent()) {
            if parent.exists() {
                command.current_dir(parent);
            }
        }
        command
            .env("ORCHFLOW_OVRTX_ROOT", &runtime)
            .env("ORCHFLOW_OVRTX_SCRATCH", scratch.path())
            .arg("-u")
            .arg(script)
            .arg("--serve")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let mut child = command.spawn().map_err(|e| {
            format!("Cannot start Omniverse RTX worker: {e}. Ensure .venv-ovrtx is created.")
        })?;
        #[cfg(windows)]
        let job = WorkerJob::attach(&child).inspect_err(|_| {
            let _ = child.kill();
            let _ = child.wait();
        })?;
        let input = child.stdin.take().ok_or("Worker stdin unavailable")?;
        let output = child.stdout.take().ok_or("Worker stdout unavailable")?;
        let responses = spawn_stdout_reader(
            output,
            status_listener,
            "OVRTX",
            "Invalid ovrtx worker response",
        );
        Ok(Self {
            child,
            _scratch: Some(scratch),
            #[cfg(windows)]
            job,
            input,
            responses,
        })
    }
    fn request(&mut self, request: &Value) -> WorkerResponse {
        serde_json::to_writer(&mut self.input, request).map_err(|e| e.to_string())?;
        self.input
            .write_all(b"\n")
            .and_then(|_| self.input.flush())
            .map_err(|e| e.to_string())?;
        self.responses
            .recv_timeout(Duration::from_secs(
                if matches!(
                    request.get("operation").and_then(Value::as_str),
                    Some(
                        "review_publication"
                            | "publish_reviewed"
                            | "reconcile_upstream"
                            | "validate_manual_baseline"
                    )
                ) {
                    900
                } else if matches!(
                    request.get("operation").and_then(Value::as_str),
                    Some("render" | "scene_children")
                ) {
                    300 // First-time MaterialX/RTX shader compilation or cold Blender extraction may exceed 60s.
                } else {
                    300
                },
            ))
            .map_err(|e| {
                match self.child.try_wait() {
                    Ok(Some(status)) => format!("USD worker exited ({status}); the next request will restart it. See backend stderr for native crash diagnostics."),
                    _ => format!("USD worker stopped or timed out ({e}); the next request will restart it"),
                }
            })?
    }
}
impl Drop for Worker {
    fn drop(&mut self) {
        #[cfg(windows)]
        self.job.terminate();
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn find_ovrtx_python() -> Option<std::path::PathBuf> {
    crate::runtime_setup::find_ovrtx_python()
}

fn find_ovrtx_worker_script() -> std::path::PathBuf {
    crate::runtime_setup::find_ovrtx_worker_script()
}

fn is_ovrtx_available() -> bool {
    crate::runtime_setup::is_ovrtx_available()
}

struct RtxWorker {
    worker: Worker,
    scene_key: Value,
}

#[derive(Default)]
pub struct UsdService {
    inspection_worker: Mutex<Option<Worker>>,
    worker: Mutex<Option<Worker>>,
    ovrtx_worker: Mutex<Option<RtxWorker>>,
    status_listener: Mutex<Option<StatusListener>>,
}
impl UsdService {
    pub fn set_status_listener<F>(&self, listener: F)
    where
        F: Fn(Value) + Send + Sync + 'static,
    {
        if let Ok(mut slot) = self.status_listener.lock() {
            *slot = Some(Arc::new(listener));
        }
    }
    pub fn warm(&self) -> Result<(), String> {
        let mut slot = self.inspection_worker.lock().map_err(|e| e.to_string())?;
        if slot.is_none() {
            let listener = self.status_listener.lock().ok().and_then(|s| s.clone());
            *slot = Some(Worker::start(listener)?);
        }
        Ok(())
    }
    pub fn request(&self, request: Value) -> Result<Value, String> {
        let (value, _) = self.request_inner(request)?;
        Ok(value)
    }
    pub fn request_binary(&self, mut request: Value) -> Result<(Value, Vec<u8>), String> {
        if let Some(obj) = request.as_object_mut() {
            obj.insert("binaryFrame".into(), Value::Bool(true));
        }
        let (value, bin) = self.request_inner(request)?;
        Ok((value, bin.unwrap_or_default()))
    }
    fn send_to_slot(
        slot: &Mutex<Option<Worker>>,
        status_listener: &Mutex<Option<StatusListener>>,
        request: &Value,
        is_capabilities: bool,
    ) -> WorkerResponse {
        let mut guard = slot.lock().map_err(|e| e.to_string())?;
        if guard.is_none() {
            let listener = status_listener.lock().ok().and_then(|s| s.clone());
            *guard = Some(Worker::start(listener)?);
        }
        let started = Instant::now();
        let response = guard.as_mut().unwrap().request(request);
        match response {
            Ok((mut value, bin)) => {
                if let Some(error) = value.get("error").and_then(Value::as_str) {
                    return Err(error.to_string());
                }
                if is_capabilities && is_ovrtx_available() {
                    if let Some(obj) = value.as_object_mut() {
                        if let Some(renderers) =
                            obj.get_mut("renderers").and_then(Value::as_array_mut)
                        {
                            renderers.push(serde_json::json!({
                                "id": "OmniverseRTX",
                                "name": "Omniverse RTX (NVIDIA ovrtx)"
                            }));
                        }
                        obj.insert(
                            "ovrtxRuntime".into(),
                            serde_json::json!({
                                "installed": true,
                                "available": true,
                                "message": "NVIDIA Omniverse RTX standalone viewport path tracing active"
                            }),
                        );
                    }
                }
                if let Some(object) = value.as_object_mut() {
                    object.insert(
                        "roundTripMs".into(),
                        Value::from(started.elapsed().as_secs_f64() * 1000.0),
                    );
                }
                Ok((value, bin))
            }
            Err(e) => {
                *guard = None;
                Err(e)
            }
        }
    }
    fn request_inner(&self, request: Value) -> WorkerResponse {
        let op = request.get("operation").and_then(Value::as_str);
        if ![
            Some("capabilities"),
            Some("inspect"),
            Some("scene_children"),
            Some("render"),
            Some("clear_scene"),
            Some("dependencies"),
            Some("coordinate_preflight"),
            Some("handoff_export"),
            Some("reuse_handoff"),
            Some("review_publication"),
            Some("apply_live_adjustment"),
            Some("export_live_output"),
            Some("validate_manual_baseline"),
            Some("publish_reviewed"),
            Some("reconcile_upstream"),
            Some("materialx_document"),
            Some("materialx_rule_matches"),
            Some("asset_override_inputs"),
            Some("inspect_stage_materials"),
            Some("inspect_stage_lights"),
            Some("inspect_stage_curves"),
            Some("inspect_stage_skeletons"),
            Some("export_character_body"),
            Some("export_md_bridge_handoff"),
            Some("reset_hair"),
            Some("convert_source_to_usd"),
            Some("sp_build_materials"),
        ]
        .contains(&op)
        {
            return Err("Unsupported USD operation".into());
        }

        let is_render = op == Some("render");
        let is_scene_children = op == Some("scene_children");
        let is_reset_hair = op == Some("reset_hair");
        if is_reset_hair {
            if let Ok(mut hydra) = self.worker.lock() {
                if let Some(w) = hydra.as_mut() {
                    let _ = w.request(&request);
                }
            }
            if let Ok(mut rtx) = self.ovrtx_worker.lock() {
                if let Some(w) = rtx.as_mut() {
                    if let Ok((resp, _)) = w.worker.request(&request) {
                        return Ok((resp, None));
                    }
                }
            }
            return Ok((serde_json::json!({"reset": true}), None));
        }

        let is_clear = op == Some("clear_scene");
        if is_clear {
            if let Ok(mut insp) = self.inspection_worker.lock() {
                if let Some(w) = insp.as_mut() {
                    if w.request(&request).is_err() {
                        *insp = None;
                    }
                }
            }
            {
                let mut hydra = self.worker.lock().map_err(|e| e.to_string())?;
                *hydra = None;
            }
            let mut rtx = self.ovrtx_worker.lock().map_err(|e| e.to_string())?;
            if let Some(w) = rtx.as_mut() {
                if let Ok((resp, _)) = w.worker.request(&request) {
                    return Ok((resp, None));
                }
            }
            *rtx = None;
            return Ok((
                serde_json::json!({"cleared": true, "cachedScenes": 0}),
                None,
            ));
        }

        if is_scene_children {
            let mut rtx = self.ovrtx_worker.lock().map_err(|e| e.to_string())?;
            if let Some(active) = rtx.as_mut() {
                let started = Instant::now();
                return match active.worker.request(&request) {
                    Ok((mut value, bin)) => {
                        if let Some(error) = value.get("error").and_then(Value::as_str) {
                            return Err(error.to_string());
                        }
                        if let Some(object) = value.as_object_mut() {
                            object.insert(
                                "roundTripMs".into(),
                                Value::from(started.elapsed().as_secs_f64() * 1000.0),
                            );
                        }
                        Ok((value, bin))
                    }
                    Err(e) => {
                        *rtx = None;
                        Err(e)
                    }
                };
            }
        }

        let is_ovrtx =
            is_render && request.get("renderer").and_then(Value::as_str) == Some("OmniverseRTX");

        if is_ovrtx {
            // Release the isolated Hydra/Storm renderer before starting Omniverse RTX.
            // Keeping its OpenGL FBO, textures and buffers alive in GPU VRAM competes
            // with RTX path tracing on large scenes. Keep `inspection_worker` alive.
            {
                let mut hydra = self.worker.lock().map_err(|e| e.to_string())?;
                *hydra = None;
            }

            let mut slot = self.ovrtx_worker.lock().map_err(|e| e.to_string())?;
            let scene_key = serde_json::json!([
                request.get("graphKey"),
                request.get("path"),
                request.get("revision"),
                request.get("frame"),
                request.get("materials"),
                request.get("ovrtxMode"),
                request.get("isolatedPrim"),
                request.get("hiddenPrims"),
                if request.get("graphKey").is_none() {
                    request.get("graph")
                } else {
                    None
                }
            ]);
            let started = Instant::now();
            if slot.is_none() {
                let listener = self.status_listener.lock().ok().and_then(|s| s.clone());
                *slot = Some(RtxWorker {
                    worker: Worker::start_ovrtx(listener)?,
                    scene_key,
                });
            } else if let Some(active) = slot.as_mut() {
                active.scene_key = scene_key;
            }
            let response = slot.as_mut().unwrap().worker.request(&request);
            return match response {
                Ok((mut value, bin)) => {
                    if let Some(error) = value.get("error").and_then(Value::as_str) {
                        let message = error.to_string();
                        if !message.contains("Unknown render graph handle") {
                            eprintln!("OVRTX error: {message}");
                        }
                        if message.contains("stalled") || message.contains("CUDA") {
                            *slot = None;
                        }
                        return Err(message);
                    }
                    if let Some(object) = value.as_object_mut() {
                        object.insert(
                            "roundTripMs".into(),
                            Value::from(started.elapsed().as_secs_f64() * 1000.0),
                        );
                    }
                    Ok((value, bin))
                }
                Err(e) => {
                    eprintln!("OVRTX worker IPC error (restarting): {e}");
                    *slot = None;
                    Err(e)
                }
            };
        }

        // Non-GL USD inspection, dependency discovery, MaterialX & publication
        // operations run on `inspection_worker` so they never block behind renders
        // or get killed when switching to Omniverse RTX.
        let is_inspection_op = !matches!(op, Some("render" | "capabilities"));
        if is_inspection_op {
            return Self::send_to_slot(
                &self.inspection_worker,
                &self.status_listener,
                &request,
                false,
            );
        }

        // Release the isolated RTX renderer before returning to Hydra. Keeping
        // its GPU allocations alive competes with Storm on large scenes.
        if is_render {
            let mut rtx = self.ovrtx_worker.lock().map_err(|e| e.to_string())?;
            *rtx = None;
        }
        Self::send_to_slot(
            &self.worker,
            &self.status_listener,
            &request,
            op == Some("capabilities"),
        )
    }
    pub fn shutdown(&self) {
        if let Ok(mut insp) = self.inspection_worker.lock() {
            *insp = None;
        }
        if let Ok(mut worker) = self.worker.lock() {
            *worker = None;
        }
        if let Ok(mut ovrtx) = self.ovrtx_worker.lock() {
            *ovrtx = None;
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn ovrtx_discovery_finds_runtime_and_venv() {
        assert!(is_ovrtx_available());
        assert!(find_ovrtx_python().is_some());
        assert!(find_ovrtx_worker_script().exists());
    }

    #[test]
    fn rejects_unknown_operation_without_starting_process() {
        let service = UsdService::default();
        assert!(
            service
                .request(serde_json::json!({"operation":"execute"}))
                .is_err()
        );
        assert!(service.worker.lock().unwrap().is_none());
    }

    #[test]
    #[ignore = "requires ORCHFLOW_PYTHON and ORCHFLOW_USD_ROOT with a real GPU"]
    fn real_worker_renders_persistent_frames() {
        let service = UsdService::default();
        let caps = service
            .request(serde_json::json!({"operation":"capabilities"}))
            .unwrap();
        assert_eq!(caps["viewport"], true, "{caps}");
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../samples/layered.usda");
        let discovered = service
            .request(serde_json::json!({"operation":"dependencies","path":path}))
            .unwrap();
        assert!(
            discovered["files"]
                .as_array()
                .unwrap()
                .iter()
                .any(|file| file["path"].as_str().unwrap().ends_with("cube.usda"))
        );
        let material_report = service
            .request(serde_json::json!({"operation":"inspect","path":path}))
            .unwrap();
        assert!(material_report.get("materialInspection").is_some());
        let mut request = serde_json::json!({"operation":"render","path":path,"renderer":"HdStormRendererPlugin","width":800,"height":600});
        let first = service.request(request.clone()).unwrap();
        assert!(
            first["image"]
                .as_str()
                .unwrap()
                .starts_with("data:image/png;base64,")
        );
        request["yaw"] = 80.into();
        let warm = service.request(request.clone()).unwrap();
        assert_ne!(first["image"], warm["image"]);
        println!(
            "GPU: {}; warm render={}ms capture={}ms worker={}ms Rust roundtrip={}ms PNG={} bytes",
            warm["gpu"],
            warm["renderMs"],
            warm["captureMs"],
            warm["totalMs"],
            warm["roundTripMs"],
            warm["pngBytes"]
        );
        request["renderer"] = "missing-renderer".into();
        assert!(
            service
                .request(request.clone())
                .unwrap_err()
                .contains("no fallback")
        );
        request["renderer"] = "HdStormRendererPlugin".into();
        assert!(service.request(request).is_ok());
        let graph = serde_json::json!({
            "targetNodeId":"out",
            "dependencies":[{"id":"file","path":path}],
            "nodes":[{"id":"source","operation":"source","inputDependencyId":"file"},
                     {"id":"transform","operation":"transform","transform":{"translation":[3,0,0]}},
                     {"id":"out","operation":"output"}],
            "edges":[{"id":"source-transform","source":"source","target":"transform"},
                     {"id":"source-out","source":"source","target":"out"},
                     {"id":"transform-out","source":"transform","target":"out"}]
        });
        let prepared = service.request(serde_json::json!({"operation":"coordinate_preflight","graph":graph.clone(),"app":"Blender","upAxis":"Z","metersPerUnit":0.01,"scale":2})).unwrap();
        assert_eq!(prepared["draft"]["factor"], 200.0);
        assert_eq!(
            prepared["draft"]["graphRevision"].as_str().unwrap().len(),
            64
        );
        let combined=service.request(serde_json::json!({"operation":"render","graph":graph,"renderer":"HdStormRendererPlugin","width":800,"height":600})).unwrap();
        assert_eq!(combined["graphStats"]["uniqueSourceAssets"], 1);
        assert!(combined["graphStats"]["prototypes"].as_u64().unwrap() > 0);
        assert!(
            combined["image"]
                .as_str()
                .unwrap()
                .starts_with("data:image/png;base64,")
        );
        service.shutdown();
        assert!(service.worker.lock().unwrap().is_none());
        assert!(service.inspection_worker.lock().unwrap().is_none());
    }

    #[test]
    #[ignore = "requires ORCHFLOW_PYTHON and ORCHFLOW_USD_ROOT with a real GPU"]
    fn real_worker_binary_frame_transport() {
        let service = UsdService::default();
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../samples/layered.usda");
        let (png_meta, png_bytes) = service
            .request_binary(serde_json::json!({
                "operation": "render",
                "path": path,
                "renderer": "HdStormRendererPlugin",
                "width": 320,
                "height": 240,
                "interactive": false
            }))
            .unwrap();
        assert!(png_meta.get("image").is_none());
        assert_eq!(png_meta["encoding"], "png");
        assert_eq!(png_meta["binaryBytes"], png_bytes.len());
        assert!(png_bytes.starts_with(b"\x89PNG"));

        let (jpg_meta, jpg_bytes) = service
            .request_binary(serde_json::json!({
                "operation": "render",
                "path": path,
                "renderer": "HdStormRendererPlugin",
                "width": 320,
                "height": 240,
                "interactive": true
            }))
            .unwrap();
        assert!(jpg_meta.get("image").is_none());
        assert_eq!(jpg_meta["encoding"], "jpeg");
        assert_eq!(jpg_meta["binaryBytes"], jpg_bytes.len());
        assert!(jpg_bytes.starts_with(b"\xff\xd8\xff"));
        service.shutdown();
    }

    #[test]
    #[ignore = "real GPU navigation benchmark; optionally set ORCHFLOW_BENCH_STAGE"]
    fn measure_interactive_navigation() {
        let service = UsdService::default();
        let path = std::env::var("ORCHFLOW_BENCH_STAGE").unwrap_or_else(|_| {
            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../samples/layered.usda")
                .to_string_lossy()
                .into_owned()
        });
        let mut request = serde_json::json!({"operation":"render","path":path,"renderer":"HdStormRendererPlugin","width":1130,"height":788});
        service.request(request.clone()).unwrap();
        for interactive in [false, true] {
            request["interactive"] = interactive.into();
            request["width"] = (if interactive { 800 } else { 1130 }).into();
            request["height"] = (if interactive { 558 } else { 788 }).into();
            service.request(request.clone()).unwrap(); // warm size-dependent GPU resources
            let mut timings = Vec::new();
            let mut captures = Vec::new();
            for frame in 0..30 {
                request["yaw"] = (35.0 + f64::from(frame) * 0.7).into();
                request["panX"] = (f64::from(frame) * 0.002).into();
                let result = service.request(request.clone()).unwrap();
                assert_eq!(result["encoding"], if interactive { "jpeg" } else { "png" });
                assert_eq!(result["interactive"], interactive);
                timings.push(result["roundTripMs"].as_f64().unwrap());
                captures.push(result["captureMs"].as_f64().unwrap());
            }
            timings.sort_by(f64::total_cmp);
            captures.sort_by(f64::total_cmp);
            println!(
                "{}: 30 frames, Rust roundtrip median={:.2}ms p95={:.2}ms, capture median={:.2}ms",
                if interactive {
                    "interactive 800x558 JPEG"
                } else {
                    "full 1130x788 PNG"
                },
                timings[15],
                timings[28],
                captures[15]
            );
        }
        request["interactive"] = false.into();
        request["width"] = 1130.into();
        request["height"] = 788.into();
        let final_frame = service.request(request).unwrap();
        assert_eq!(final_frame["encoding"], "png");
        assert_eq!(final_frame["width"], 1130);
        service.shutdown();
    }
}

#[cfg(all(test, windows))]
mod worker_job_tests {
    use super::*;
    #[test]
    #[ignore = "requires installed NVIDIA GPU runtime and explicit render-test authorization"]
    fn rtx_scene_replacement_uses_fresh_workers() {
        let service = UsdService::default();
        for key in ["scene-a", "scene-b", "scene-a"] {
            let response = service.request(serde_json::json!({
                "operation":"render", "renderer":"OmniverseRTX", "graphKey":key,
                "graph": {"namespaceVersion":2,"targetNodeId":"source",
                    "nodes":[{"id":"source","operation":"source","inputDependencyId":"asset"}],
                    "edges":[],"dependencies":[{"id":"asset","kind":"usd","path":"I:/AI/OrchFlow-build/ovrtx-navigation/plane-cubes.usda"}]},
                "width":320,"height":180,"yaw":35,"pitch":25,"zoom":1,"interactive":true
            })).unwrap();
            assert_eq!(response["width"], 320);
            assert!(
                response["image"]
                    .as_str()
                    .unwrap()
                    .starts_with("data:image/")
            );
            println!("Rendered replacement {key}");
        }
        service.shutdown();
    }

    #[test]
    fn dropping_job_terminates_venv_python_descendant() {
        use windows_sys::Win32::{
            Foundation::CloseHandle,
            System::Threading::{OpenProcess, WaitForSingleObject},
        };
        let python = find_ovrtx_python().expect("NVIDIA test Python environment");
        let mut child = Command::new(python).args(["-u", "-c",
            "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(p.pid,flush=True); time.sleep(60)"])
            .stdout(Stdio::piped()).spawn().unwrap();
        let job = WorkerJob::attach(&child).unwrap();
        let mut line = String::new();
        BufReader::new(child.stdout.take().unwrap())
            .read_line(&mut line)
            .unwrap();
        let pid: u32 = line.trim().parse().unwrap();
        unsafe {
            let descendant = OpenProcess(0x00100000, 0, pid);
            assert!(!descendant.is_null());
            drop(job);
            let outcome = WaitForSingleObject(descendant, 5000);
            CloseHandle(descendant);
            assert_eq!(outcome, 0, "base-Python descendant survived job closure");
        }
        let _ = child.wait();
    }
}
