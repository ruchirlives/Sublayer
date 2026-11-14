use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, mpsc};
use std::time::Duration;

#[derive(Serialize, Deserialize)]
struct BridgeInfo {
    port: u16,
    pid: u32,
    token: String,
}

pub fn bridge_file_path() -> PathBuf {
    std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .map(|p| p.join("com.orchflow.desktop/desktop-bridge.json"))
        .unwrap_or_else(|| std::env::temp_dir().join("orchflow-desktop-bridge.json"))
}

fn generate_token() -> String {
    use std::collections::hash_map::RandomState;
    use std::hash::{BuildHasher, Hasher};
    let mut parts = [0u64; 2];
    for slot in &mut parts {
        let mut h = RandomState::new().build_hasher();
        h.write_u32(std::process::id());
        if let Ok(d) = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH) {
            h.write_u128(d.as_nanos());
        }
        *slot = h.finish();
    }
    format!("{:016x}{:016x}", parts[0], parts[1])
}

pub type PendingMap = Arc<Mutex<HashMap<String, mpsc::Sender<Result<Value, String>>>>>;

pub struct DesktopBridge {
    pub pending: PendingMap,
    token: String,
    counter: AtomicU64,
}

impl DesktopBridge {
    pub fn start<F>(emit: F) -> Result<Arc<Self>, String>
    where
        F: Fn(Value) -> Result<(), String> + Send + Sync + 'static,
    {
        let listener = TcpListener::bind("127.0.0.1:0")
            .map_err(|e| format!("Cannot bind desktop MCP bridge: {e}"))?;
        let port = listener
            .local_addr()
            .map_err(|e| format!("Cannot inspect bridge address: {e}"))?
            .port();
        let token = generate_token();
        let path = bridge_file_path();
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let info = BridgeInfo {
            port,
            pid: std::process::id(),
            token: token.clone(),
        };
        let encoded = serde_json::to_string(&info).map_err(|e| e.to_string())?;
        std::fs::write(&path, encoded)
            .map_err(|e| format!("Cannot write desktop bridge info: {e}"))?;

        let bridge = Arc::new(Self {
            pending: Arc::new(Mutex::new(HashMap::new())),
            token,
            counter: AtomicU64::new(1),
        });
        let bridge_thread = bridge.clone();
        let emit = Arc::new(emit);
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(stream) = stream else { break };
                let bridge_conn = bridge_thread.clone();
                let emit_conn = emit.clone();
                std::thread::spawn(move || {
                    bridge_conn.handle_connection(stream, &*emit_conn);
                });
            }
        });
        Ok(bridge)
    }

    fn handle_connection<F>(&self, mut stream: TcpStream, emit: &F)
    where
        F: Fn(Value) -> Result<(), String>,
    {
        let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
        let _ = stream.set_write_timeout(Some(Duration::from_secs(5)));
        let mut reader = match stream.try_clone() {
            Ok(s) => BufReader::new(s),
            Err(_) => return,
        };
        let mut line = String::new();
        if reader.read_line(&mut line).is_err() || line.is_empty() {
            return;
        }
        let response = match serde_json::from_str::<Value>(&line) {
            Err(e) => json!({"ok": false, "error": format!("Invalid bridge JSON: {e}")}),
            Ok(req) => {
                if req.get("token").and_then(Value::as_str) != Some(self.token.as_str()) {
                    json!({"ok": false, "error": "Unauthorized desktop bridge token"})
                } else {
                    let action = req
                        .get("action")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string();
                    let args = req.get("args").cloned().unwrap_or_else(|| json!({}));
                    let req_id = format!("mcp-{}", self.counter.fetch_add(1, Ordering::Relaxed));
                    let (tx, rx) = mpsc::channel();
                    if let Ok(mut map) = self.pending.lock() {
                        map.insert(req_id.clone(), tx);
                    }
                    let payload = json!({
                        "requestId": req_id,
                        "action": action,
                        "args": args,
                    });
                    if let Err(e) = emit(payload) {
                        if let Ok(mut map) = self.pending.lock() {
                            map.remove(&req_id);
                        }
                        json!({"ok": false, "error": e})
                    } else {
                        match rx.recv_timeout(Duration::from_secs(30)) {
                            Ok(Ok(val)) => json!({"ok": true, "result": val}),
                            Ok(Err(err)) => json!({"ok": false, "error": err}),
                            Err(_) => {
                                if let Ok(mut map) = self.pending.lock() {
                                    map.remove(&req_id);
                                }
                                json!({"ok": false, "error": "Desktop window timed out responding"})
                            }
                        }
                    }
                }
            }
        };
        let _ = writeln!(stream, "{response}");
        let _ = stream.flush();
    }

    pub fn resolve(
        &self,
        request_id: &str,
        ok: bool,
        result: Option<Value>,
        error: Option<String>,
    ) {
        if let Ok(mut map) = self.pending.lock() {
            if let Some(tx) = map.remove(request_id) {
                let res = if ok {
                    Ok(result.unwrap_or(Value::Null))
                } else {
                    Err(error.unwrap_or_else(|| "Desktop action failed".into()))
                };
                let _ = tx.send(res);
            }
        }
    }

    pub fn cleanup_file() {
        let path = bridge_file_path();
        if let Ok(raw) = std::fs::read_to_string(&path) {
            if let Ok(info) = serde_json::from_str::<BridgeInfo>(&raw) {
                if info.pid == std::process::id() {
                    let _ = std::fs::remove_file(&path);
                }
            }
        }
    }
}

pub fn send_command(action: &str, args: Value) -> Result<Value, String> {
    let path = bridge_file_path();
    let raw = std::fs::read_to_string(&path).map_err(|_| {
        "No active OrchFlow desktop window found. Launch OrchFlow first.".to_string()
    })?;
    let info: BridgeInfo =
        serde_json::from_str(&raw).map_err(|e| format!("Corrupt desktop bridge info: {e}"))?;
    let addr: SocketAddr = format!("127.0.0.1:{}", info.port)
        .parse()
        .map_err(|e| format!("Invalid bridge address: {e}"))?;
    let mut stream = TcpStream::connect_timeout(&addr, Duration::from_secs(2)).map_err(|_| {
        "OrchFlow desktop window is not reachable (it may have closed).".to_string()
    })?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(30)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(5)));
    let payload = json!({
        "token": info.token,
        "action": action,
        "args": args,
    });
    writeln!(stream, "{payload}").map_err(|e| format!("Failed sending to desktop: {e}"))?;
    stream
        .flush()
        .map_err(|e| format!("Failed flushing desktop command: {e}"))?;
    let mut reader = BufReader::new(stream);
    let mut line = String::new();
    reader
        .read_line(&mut line)
        .map_err(|e| format!("Failed reading desktop response: {e}"))?;
    let resp: Value =
        serde_json::from_str(&line).map_err(|e| format!("Invalid desktop response: {e}"))?;
    if resp.get("ok").and_then(Value::as_bool) == Some(true) {
        Ok(resp.get("result").cloned().unwrap_or(Value::Null))
    } else {
        Err(resp
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or("Desktop command failed")
            .to_string())
    }
}
