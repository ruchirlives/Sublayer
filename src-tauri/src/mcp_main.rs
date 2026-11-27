//! Local stdio MCP server. Reuses desktop domain validation and persistent USD worker.
#![allow(dead_code)]
mod application;
mod application_profiles;
mod dependency_search;
mod desktop_bridge;
mod megascans;
mod project;
mod runtime_setup;
mod usd;
use serde_json::{Value, json};
use std::io::{BufRead, Write};
fn string<'a>(a: &'a Value, key: &str) -> Result<&'a str, String> {
    a.get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("Missing string: {key}"))
}
fn model(a: &Value) -> Result<project::Project, String> {
    project::Project::from_value(a.get("project").cloned().ok_or("Missing project")?)
}
fn revision(path: &str) -> Result<String, String> {
    use std::hash::{Hash, Hasher};
    let mut h = std::collections::hash_map::DefaultHasher::new();
    std::fs::read(path).map_err(|e| e.to_string())?.hash(&mut h);
    Ok(format!("{:016x}", h.finish()))
}
fn catalog(a: &Value) -> Result<application_profiles::Catalog, String> {
    let path = a
        .get("catalogPath")
        .and_then(Value::as_str)
        .map(std::path::PathBuf::from)
        .or_else(|| {
            std::env::var_os("APPDATA")
                .map(|p| std::path::PathBuf::from(p).join("com.orchflow.desktop/applications.json"))
        });
    path.map(|p| application_profiles::load(&p))
        .unwrap_or_else(|| Ok(application_profiles::Catalog::defaults()))
}
fn definitions() -> Vec<Value> {
    let s = json!({"type":"string"});
    let p = json!({"type":"object"});
    let entries = vec![
        (
            "scene_children",
            "Read lazy USD scene hierarchy children, including instance proxies.",
            json!({"project":p,"nodeId":s,"path":s,"parent":s,"offset":{"type":"integer"}}),
            vec![],
        ),
        (
            "export_materialx",
            "Explicitly export a material-only .mtlx to a new destination without copying textures.",
            json!({"project":p,"nodeId":s,"destination":s}),
            vec!["project", "nodeId", "destination"],
        ),
        (
            "materialx_document",
            "Generate the authored MaterialX node document without writing a file.",
            json!({"project":p,"nodeId":s}),
            vec!["project", "nodeId"],
        ),
        (
            "project_new",
            "Create an unsaved test graph referencing an external USD source.",
            json!({"sourcePath":s}),
            vec![],
        ),
        (
            "project_validate",
            "Validate a full OrchFlow JSON project without writes.",
            json!({"project":p}),
            vec!["project"],
        ),
        (
            "project_load",
            "Read a project with a revision token for safe save.",
            json!({"path":s}),
            vec!["path"],
        ),
        (
            "project_save",
            "Explicitly save only OrchFlow JSON. Existing files require expectedRevision from project_load. Does not modify USD/native/assets.",
            json!({"path":s,"project":p,"expectedRevision":s}),
            vec!["path", "project"],
        ),
        (
            "application_profiles",
            "Read configured profiles and bundled templates. No application launching.",
            json!({"catalogPath":s}),
            vec![],
        ),
        (
            "usd_capabilities",
            "Discover real upstream USD, installed Hydra renderers and GPU.",
            json!({}),
            vec![],
        ),
        (
            "usd_inspect",
            "Inspect a stage without editing it.",
            json!({"path":s}),
            vec!["path"],
        ),
        (
            "usd_dependencies",
            "Discover USD dependencies including unresolved assets.",
            json!({"path":s}),
            vec!["path"],
        ),
        (
            "usd_render",
            "Render a validated project node or external path; returns an MCP image plus metadata. Does not control the desktop UI.",
            json!({
                "project":p,"nodeId":s,"path":s,"renderer":s,
                "width":{"type":"integer"},"height":{"type":"integer"},
                "yaw":{"type":"number"},"pitch":{"type":"number"},
                "panX":{"type":"number"},"panY":{"type":"number"},"zoom":{"type":"number"},
                "projection":s,"fov":{"type":"number"},"upAxis":s,
                "cameraSession":s,"frameFit":{"type":"integer"},
                "interactive":{"type":"boolean"},"transformGizmoNodeId":s,
                "pick":{"type":"array","items":{"type":"number"},"minItems":2,"maxItems":2},
                "orbitPivotSeq":{"type":"integer"},"orbitPivotTarget":s,
                "orbitPivotPoint":{"type":"array","items":{"type":"number"},"minItems":3,"maxItems":3}
            }),
            vec![],
        ),
        (
            "create_handoff",
            "Explicitly write a single-file USD handoff snapshot with external texture links and no supporting layer folder. Returns artifact links; does not modify project JSON automatically.",
            json!({"project":p,"nodeId":s,"destination":s,"catalogPath":s}),
            vec!["project", "nodeId", "destination"],
        ),
        (
            "validate_manual_baseline",
            "Read-only validation of an unedited USD/OBJ/Alembic baseline, including explicit object correspondence. Does not modify project JSON or run Blender.",
            json!({"project":p,"nodeId":s,"baseline":s,"catalogPath":s,"correspondence":{"type":"array","items":{"type":"object"}},"expectedDigest":s}),
            vec!["project", "nodeId", "baseline"],
        ),
        (
            "review_publication",
            "Read-only baseline/edit review using recorded policy. No Blender execution.",
            json!({"project":p,"nodeId":s,"edited":s,"correspondence":{"type":"array","items":{"type":"object"}},"confirmedDeletions":{"type":"array","items":{"type":"string"}},"newPrimPaths":{"type":"array","items":{"type":"string"}}}),
            vec!["project", "nodeId", "edited"],
        ),
        (
            "publish_reviewed",
            "Explicitly write new publication root and sparse edit layer using a valid reviewToken. No project auto-save.",
            json!({"project":p,"nodeId":s,"edited":s,"destination":s,"reviewToken":s,"correspondence":{"type":"array","items":{"type":"object"}},"confirmedDeletions":{"type":"array","items":{"type":"string"}},"newPrimPaths":{"type":"array","items":{"type":"string"}}}),
            vec!["project", "nodeId", "edited", "destination", "reviewToken"],
        ),
        (
            "reconcile_upstream",
            "Explicitly compose existing adjustments over current input into new root/edit/input layers. No Blender execution or conflict review.",
            json!({"project":p,"nodeId":s,"destination":s}),
            vec!["project", "nodeId", "destination"],
        ),
        (
            "desktop_state",
            "Inspect the open OrchFlow desktop window state (project path, nodes, selected/pinned node, active tab, viewport settings).",
            json!({}),
            vec![],
        ),
        (
            "desktop_open_project",
            "Open an existing .orchflow project file in the running OrchFlow desktop window.",
            json!({"path":s}),
            vec!["path"],
        ),
        (
            "desktop_select_node",
            "Select a node in the open OrchFlow desktop window.",
            json!({"nodeId":s}),
            vec!["nodeId"],
        ),
        (
            "desktop_pin_node",
            "Pin or unpin a node preview in the open OrchFlow desktop window.",
            json!({"nodeId":json!({"type":["string","null"]})}),
            vec![],
        ),
        (
            "desktop_set_viewport",
            "Update viewport settings (renderer, ovrtxMode, upAxis, materials, activeTab) in the open OrchFlow desktop window.",
            json!({"renderer":s,"ovrtxMode":s,"upAxis":s,"materials":s,"activeTab":s}),
            vec![],
        ),
        (
            "desktop_apply_project",
            "Validate and apply a project JSON object into the open OrchFlow desktop window without saving it to disk.",
            json!({"project":p,"label":s,"selectNodeId":s}),
            vec!["project"],
        ),
        (
            "inspect_stage_skeletons",
            "Discover UsdSkel Skeletons (with joints), Animations (with time ranges), and Xformable prims for a Character node.",
            json!({"project":p,"nodeId":s}),
            vec!["project", "nodeId"],
        ),
        (
            "export_character_body",
            "Explicitly bake and export the animated/deformed character body meshes (excluding clothCaches) to a user-selected .usd/.usda/.usdc path.",
            json!({"project":p,"nodeId":s,"destination":s}),
            vec!["project", "nodeId", "destination"],
        ),
    ];
    entries.into_iter().map(|(name,description,properties,required)|{let write=["project_save","export_materialx","export_character_body","create_handoff","publish_reviewed","reconcile_upstream","desktop_open_project","desktop_select_node","desktop_pin_node","desktop_set_viewport","desktop_apply_project"].contains(&name);json!({"name":name,"description":description,"inputSchema":{"type":"object","properties":properties,"required":required,"additionalProperties":false},"annotations":{"readOnlyHint":!write,"destructiveHint":name=="project_save","idempotentHint":!write,"openWorldHint":false}})}).collect()
}
fn call(name: &str, a: &Value, service: &usd::UsdService) -> Result<Value, String> {
    match name {
 "project_new"=>{let source=a.get("sourcePath").and_then(Value::as_str);project::Project::from_value(json!({"version":2,"dependencies":source.map(|path|vec![json!({"id":"usd-input","kind":"usd","path":path})]).unwrap_or_default(),"nodes":[{"id":"source","type":"stage","position":{"x":80,"y":140},"data":{"label":"Source stage","operation":"source","app":"","axis":"preserve","scale":1,"inputDependencyId":source.map(|_|"usd-input")}}, {"id":"transform","type":"stage","position":{"x":440,"y":140},"data":{"label":"Transform","operation":"transform","app":"","axis":"preserve","scale":1}}, {"id":"output","type":"stage","position":{"x":800,"y":140},"data":{"label":"Output","operation":"output","app":"","axis":"preserve","scale":1}}],"edges":[{"id":"source-transform","source":"source","target":"transform"},{"id":"transform-output","source":"transform","target":"output"}]})).and_then(|p|serde_json::to_value(p).map_err(|e|e.to_string()))},
 "project_validate"=>{let p=model(a)?;Ok(json!({"valid":true,"nodes":p.nodes.len(),"edges":p.edges.len(),"dependencies":p.dependencies.len()}))},
 "project_load"=>{let path=string(a,"path")?;let p=project::load(path)?;Ok(json!({"project":p,"revision":revision(path)?}))},
 "project_save"=>{let path=string(a,"path")?;let p=model(a)?;if std::path::Path::new(path).exists() && a.get("expectedRevision").and_then(Value::as_str)!=Some(&revision(path)?){return Err("Project changed or no expectedRevision supplied; load it before replacing existing JSON".into());}project::save(path,&p)?;Ok(json!({"path":path,"revision":revision(path)?}))},
 "export_materialx"=>application::export_materialx(&model(a)?,string(a,"nodeId")?,string(a,"destination")?,service),
 "materialx_document"=>{let project=model(a)?;let id=string(a,"nodeId")?;let node=project.nodes.iter().find(|n|n.id==id && n.data.operation=="materialx").ok_or("Choose a MaterialX node")?;service.request(json!({"operation":"materialx_document","materialX":node.data.material_x,"dependencies":project.dependencies}))},
 "inspect_stage_skeletons"=>{let project=model(a)?;let id=string(a,"nodeId")?;service.request(json!({"operation":"inspect_stage_skeletons","graph":application::evaluation_graph(&project,id,false)?}))},
 "export_character_body"=>{let project=model(a)?;let id=string(a,"nodeId")?;if !project.nodes.iter().any(|n|n.id==id && n.data.operation=="character"){return Err("Choose a Character node".into());}service.request(json!({"operation":"export_character_body","graph":application::evaluation_graph(&project,id,false)?,"outputPath":string(a,"destination")?}))},
 "application_profiles"=>Ok(json!({"configured":catalog(a)?,"bundled":application_profiles::Catalog::defaults()})),
 "usd_capabilities"=>service.request(json!({"operation":"capabilities"})),
 "usd_inspect"|"usd_dependencies"=>service.request(json!({"operation":if name=="usd_inspect"{"inspect"}else{"dependencies"},"path":string(a,"path")?})),
 "usd_render"|"scene_children"=>{let mut request=a.clone();request["operation"]=if name=="usd_render"{"render"}else{"scene_children"}.into();if a.get("project").is_some(){request["graph"]=application::evaluation_graph(&model(a)?,string(a,"nodeId")?,false)?;}else{string(a,"path")?;}service.request(request)},
 "create_handoff"=>service.request(application::handoff_request(&model(a)?,string(a,"nodeId")?,string(a,"destination")?,&catalog(a)?)?),
 "validate_manual_baseline"=>{let mut request=application::manual_baseline_request(&model(a)?,string(a,"nodeId")?,string(a,"baseline")?,&catalog(a)?)?;request["correspondence"]=a.get("correspondence").cloned().unwrap_or(json!([]));request["expectedDigest"]=a.get("expectedDigest").cloned().unwrap_or(Value::Null);service.request(request)},
 "review_publication"|"publish_reviewed"=>{let mapping=serde_json::from_value(a.get("correspondence").cloned().unwrap_or(json!([]))).map_err(|e|e.to_string())?;let deletes=serde_json::from_value(a.get("confirmedDeletions").cloned().unwrap_or(json!([]))).map_err(|e|e.to_string())?;let mut request=application::publication_request(&model(a)?,string(a,"nodeId")?,string(a,"edited")?,mapping,deletes,if name=="publish_reviewed"{Some(string(a,"destination")?.into())}else{None},if name=="publish_reviewed"{Some(string(a,"reviewToken")?.into())}else{None})?;request["newPrimPaths"]=a.get("newPrimPaths").cloned().unwrap_or(json!([]));service.request(request)},
 "reconcile_upstream"=>service.request(application::reconciliation_request(&model(a)?,string(a,"nodeId")?,string(a,"destination")?)?),
 "desktop_state"=>{
     let mut state = desktop_bridge::send_command("get_state", json!({}))?;
     if let Some(obj) = state.as_object_mut() {
         let log_path = std::env::var_os("LOCALAPPDATA")
             .map(std::path::PathBuf::from)
             .unwrap_or_else(|| std::path::PathBuf::from("."))
             .join("OrchFlow")
             .join("worker-errors.log");
         let tail: Vec<String> = std::fs::read_to_string(&log_path)
             .map(|text| {
                 let lines: Vec<&str> = text.lines().collect();
                 let start = lines.len().saturating_sub(80);
                 lines[start..].iter().map(|s| (*s).to_string()).collect()
             })
             .unwrap_or_default();
         obj.insert("recentWorkerErrors".into(), json!(tail));
     }
     Ok(state)
 },
 "desktop_open_project"=>desktop_bridge::send_command("open_project", json!({"path": string(a,"path")?})),
 "desktop_select_node"=>desktop_bridge::send_command("select_node", json!({"nodeId": string(a,"nodeId")?})),
 "desktop_pin_node"=>desktop_bridge::send_command("pin_node", json!({"nodeId": a.get("nodeId").cloned().unwrap_or(Value::Null)})),
 "desktop_set_viewport"=>desktop_bridge::send_command("set_viewport", a.clone()),
 "desktop_apply_project"=>{
     let project = model(a)?;
     let mut payload = json!({"project": project});
     if let Some(label) = a.get("label") {
         payload["label"] = label.clone();
     }
     if let Some(select_id) = a.get("selectNodeId") {
         payload["selectNodeId"] = select_id.clone();
     }
     desktop_bridge::send_command("apply_project", payload)
 }
 _=>Err("Unknown OrchFlow tool".into())
 }
}
fn tool_result(result: Result<Value, String>) -> Value {
    match result {
        Err(e) => json!({"isError":true,"content":[{"type":"text","text":e}]}),
        Ok(mut data) => {
            let mut content = vec![];
            if let Some(image) = data.get("image").and_then(Value::as_str).map(str::to_owned) {
                if let Some((header, encoded)) = image.split_once(",") {
                    content.push(json!({"type":"image","mimeType":header.trim_start_matches("data:").trim_end_matches(";base64"),"data":encoded}));
                    data.as_object_mut().unwrap().remove("image");
                }
            }
            content.push(json!({"type":"text","text":serde_json::to_string(&data).unwrap()}));
            json!({"isError":false,"content":content})
        }
    }
}
fn main() {
    let service = usd::UsdService::default();
    let input = std::io::stdin();
    let mut output = std::io::stdout().lock();
    let mut initialized = false;
    for line in input.lock().lines() {
        let Ok(line) = line else { break };
        if line.len() > 24 * 1024 * 1024 {
            eprintln!("MCP request exceeds 24MB");
            break;
        }
        let request: Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(_) => {
                let _ = writeln!(
                    output,
                    "{}",
                    json!({"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":"Parse error"}})
                );
                let _ = output.flush();
                continue;
            }
        };
        let Some(id) = request.get("id") else {
            continue;
        };
        let method = request.get("method").and_then(Value::as_str).unwrap_or("");
        let params = &request["params"];
        let response = match method {
            "initialize" => {
                initialized = true;
                let asked = params["protocolVersion"].as_str().unwrap_or("");
                let version =
                    if ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"].contains(&asked) {
                        asked
                    } else {
                        "2025-11-25"
                    };
                json!({"result":{"protocolVersion":version,"capabilities":{"tools":{"listChanged":false}},"serverInfo":{"name":"orchflow","version":"0.1.0"},"instructions":"Headless OrchFlow tools use real Rust validation and upstream OpenUSD. Never launch Blender tests unless requested. Project saves write only JSON; artifact tools explicitly write new user-selected USD destinations. When an OrchFlow desktop window is open, use the desktop_* tools (desktop_state, desktop_open_project, desktop_select_node, desktop_pin_node, desktop_set_viewport, desktop_apply_project) to inspect and control the live UI session."}})
            }
            "ping" => json!({"result":{}}),
            _ if !initialized => json!({"error":{"code":-32002,"message":"Initialize first"}}),
            "tools/list" => json!({"result":{"tools":definitions()}}),
            "tools/call" => {
                let name = params["name"].as_str().unwrap_or("");
                let args = params.get("arguments").cloned().unwrap_or(json!({}));
                let result = definitions()
                    .iter()
                    .find(|t| t["name"] == name)
                    .ok_or("Unknown tool")
                    .map_err(str::to_owned)
                    .and_then(|tool| {
                        let properties = tool["inputSchema"]["properties"].as_object().unwrap();
                        let obj = args.as_object().ok_or("Arguments must be an object")?;
                        if obj.keys().any(|k| !properties.contains_key(k)) {
                            return Err("Unknown argument".into());
                        }
                        call(name, &args, &service)
                    });
                json!({"result":tool_result(result)})
            }
            _ => json!({"error":{"code":-32601,"message":"Method not found"}}),
        };
        let mut response = response;
        response["jsonrpc"] = "2.0".into();
        response["id"] = id.clone();
        if writeln!(output, "{response}")
            .and_then(|_| output.flush())
            .is_err()
        {
            break;
        }
    }
    service.shutdown();
}
