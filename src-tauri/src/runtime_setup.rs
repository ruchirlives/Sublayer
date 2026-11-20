use std::path::{Path, PathBuf};

pub fn runtimes_root() -> PathBuf {
    if let Some(local) = std::env::var_os("LOCALAPPDATA") {
        return PathBuf::from(local).join("Sublayer").join("runtimes");
    }
    if let Some(home) = std::env::var_os("USERPROFILE") {
        return PathBuf::from(home)
            .join("AppData")
            .join("Local")
            .join("Sublayer")
            .join("runtimes");
    }
    PathBuf::from("runtimes")
}

fn exe_dir() -> Option<PathBuf> {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf))
}

pub fn find_usd_root() -> Option<PathBuf> {
    if let Ok(var) = std::env::var("ORCHFLOW_USD_ROOT") {
        let p = PathBuf::from(var);
        if p.join("lib/python/pxr/UsdImagingGL").exists() {
            return Some(p);
        }
    }
    let mut candidates = Vec::new();
    if let Some(dir) = exe_dir() {
        candidates.push(dir.join("runtimes").join("openusd-25.08"));
        candidates.push(dir.join("resources").join("openusd"));
        candidates.push(dir.join("openusd"));
    }
    candidates.push(runtimes_root().join("openusd-25.08"));
    candidates.push(PathBuf::from("I:/AI/OpenUSD-runtime/25.08"));
    candidates
        .into_iter()
        .find(|p| p.join("lib/python/pxr/UsdImagingGL").exists())
}

pub fn find_imaging_python() -> Option<PathBuf> {
    if let Ok(var) = std::env::var("ORCHFLOW_PYTHON") {
        let p = PathBuf::from(var);
        if p.is_file() {
            return Some(p);
        }
    }
    let mut candidates = Vec::new();
    if let Some(dir) = exe_dir() {
        candidates.push(dir.join("runtimes/venv-imaging/Scripts/python.exe"));
        candidates.push(dir.join("resources/venv-imaging/Scripts/python.exe"));
        candidates.push(dir.join(".venv-imaging/Scripts/python.exe"));
    }
    candidates.push(runtimes_root().join("venv-imaging/Scripts/python.exe"));
    candidates.push(PathBuf::from(".venv-imaging/Scripts/python.exe"));
    candidates.push(PathBuf::from(
        "e:/CODINGPROJECTS/Rust/Sublayer/.venv-imaging/Scripts/python.exe",
    ));
    candidates.into_iter().find(|p| p.is_file())
}

fn is_valid_aurora_root(p: &Path) -> bool {
    let manifest_path = p.join("runtime.json");
    if let Ok(text) = std::fs::read_to_string(&manifest_path) {
        if let Ok(json) = serde_json::from_str::<serde_json::Value>(&text) {
            let binary_rel = json
                .get("binaryRoot")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
                .unwrap_or("bin");
            let plugin_rel = json
                .get("pluginInfo")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
                .unwrap_or("bin/usd/hdAurora/resources/plugInfo.json");
            return p.join(binary_rel).is_dir() && p.join(plugin_rel).is_file();
        }
        return false;
    }
    p.join("bin/usd/hdAurora/resources/plugInfo.json").is_file()
}

pub fn find_aurora_root() -> Option<PathBuf> {
    if let Ok(var) = std::env::var("ORCHFLOW_AURORA_ROOT") {
        let p = PathBuf::from(var);
        if is_valid_aurora_root(&p) {
            return Some(p);
        }
    }
    let mut candidates = Vec::new();
    if let Some(dir) = exe_dir() {
        candidates.push(dir.join("runtimes/aurora"));
        candidates.push(dir.join("resources/renderers/aurora"));
        candidates.push(dir.join("renderers/aurora"));
    }
    candidates.push(runtimes_root().join("aurora"));
    candidates.push(PathBuf::from("I:/AI/OrchFlow-renderers/aurora"));
    candidates.into_iter().find(|p| is_valid_aurora_root(p))
}

pub fn find_ovrtx_root() -> PathBuf {
    if let Ok(var) = std::env::var("ORCHFLOW_OVRTX_ROOT") {
        let p = PathBuf::from(var);
        if p.join("runtime.json").is_file() {
            return p;
        }
    }
    if let Some(dir) = exe_dir() {
        let prog_root = dir.join("runtimes/ovrtx");
        if prog_root.join("runtime.json").is_file() {
            return prog_root;
        }
    }
    let user_root = runtimes_root().join("ovrtx");
    if user_root.join("runtime.json").is_file() {
        return user_root;
    }
    let dev_root = PathBuf::from("I:/AI/OrchFlow-renderers/ovrtx");
    if dev_root.join("runtime.json").is_file() {
        return dev_root;
    }
    user_root
}

pub fn find_ovrtx_python() -> Option<PathBuf> {
    if let Ok(var) = std::env::var("ORCHFLOW_OVRTX_PYTHON") {
        let p = PathBuf::from(var);
        if p.is_file() {
            return Some(p);
        }
    }
    let mut candidates = Vec::new();
    if let Some(dir) = exe_dir() {
        candidates.push(dir.join("runtimes/venv-ovrtx/Scripts/python.exe"));
        candidates.push(dir.join("resources/venv-ovrtx/Scripts/python.exe"));
        candidates.push(dir.join(".venv-ovrtx/Scripts/python.exe"));
    }
    candidates.push(runtimes_root().join("venv-ovrtx/Scripts/python.exe"));
    candidates.push(PathBuf::from(
        "e:/CODINGPROJECTS/Rust/Sublayer/.venv-ovrtx/Scripts/python.exe",
    ));
    candidates.push(PathBuf::from(
        "e:/CODINGPROJECTS/Rust/OrchFlow/.venv-ovrtx/Scripts/python.exe",
    ));
    candidates.push(PathBuf::from(".venv-ovrtx/Scripts/python.exe"));
    candidates.into_iter().find(|p| p.is_file())
}

pub fn find_worker_dir() -> Option<PathBuf> {
    if let Ok(var) = std::env::var("ORCHFLOW_WORKER_DIR") {
        let p = PathBuf::from(var);
        if p.is_dir() {
            return Some(p);
        }
    }
    let mut candidates = Vec::new();
    if let Some(dir) = exe_dir() {
        candidates.push(dir.join("resources/worker"));
        candidates.push(dir.join("worker"));
    }
    candidates.push(PathBuf::from("worker"));
    candidates.push(PathBuf::from("e:/CODINGPROJECTS/Rust/Sublayer/worker"));
    candidates
        .into_iter()
        .find(|p| p.join("usd_worker.py").is_file())
}

pub fn find_ovrtx_worker_script() -> PathBuf {
    if let Some(dir) = find_worker_dir() {
        let p = dir.join("ovrtx_worker.py");
        if p.is_file() {
            return p;
        }
    }
    PathBuf::from("worker/ovrtx_worker.py")
}

pub fn is_ovrtx_available() -> bool {
    find_ovrtx_root().join("runtime.json").is_file() && find_ovrtx_python().is_some()
}
