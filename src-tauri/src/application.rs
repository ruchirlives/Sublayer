//! Application launch, preparation and validated selective-publication requests.
use crate::project::Project;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

pub struct LaunchSpec {
    executable: PathBuf,
    native_project: PathBuf,
}

fn existing_file(path: &str, role: &str) -> Result<PathBuf, String> {
    let path = Path::new(path);
    if !path.is_absolute() {
        return Err(format!(
            "{role} must have an absolute file path. Browse to relink it."
        ));
    }
    let resolved = path.canonicalize().map_err(|e| {
        format!(
            "{role} is missing or unavailable: {} ({e}). Browse to relink it.",
            path.display()
        )
    })?;
    if !resolved.is_file() {
        return Err(format!("{role} must be a file, not a directory"));
    }
    // Keep the ordinary absolute spelling for Blender/its bundled USD resolver,
    // rather than passing Windows verbatim \\?\ canonical prefixes.
    Ok(path.to_path_buf())
}

fn existing_export(path: &str, role: &str) -> Result<PathBuf, String> {
    let path = existing_file(path, role)?;
    let extension = path
        .extension()
        .and_then(|v| v.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if !["usd", "usda", "usdc", "obj", "abc"].contains(&extension.as_str()) {
        return Err(format!("{role} must be USD, OBJ or Alembic (.abc)"));
    }
    Ok(path)
}

pub fn proposed_publication_policy(
    project: &Project,
    node_id: &str,
    catalog: &crate::application_profiles::Catalog,
) -> Result<crate::application_profiles::PolicySnapshot, String> {
    project.validate()?;
    let node = project
        .nodes
        .iter()
        .find(|n| n.id == node_id)
        .ok_or("Application node no longer exists")?;
    let native = node
        .data
        .manual_baseline
        .as_ref()
        .or(node.data.native_import.as_ref())
        .ok_or("Native preparation with baseline is required")?;
    let proposed = catalog.policy(&node.data)?;
    let old = &native.policy.profile;
    if proposed.profile.app != old.app
        || proposed.profile.adapter_version != old.adapter_version
        || proposed.profile.application_version != old.application_version
    {
        return Err("Application/adapter version changed; a new round trip is required".into());
    }
    let axis = if node.data.axis == "preserve" {
        &proposed.profile.up_axis
    } else {
        &node.data.axis
    };
    let unit = node
        .data
        .application_meters_per_unit
        .unwrap_or(proposed.profile.meters_per_unit);
    if axis != &native.conversion.up_axis
        || (unit - native.conversion.meters_per_unit).abs() > 1e-10 * unit
        || (node.data.scale * proposed.profile.scale - native.conversion.scale).abs()
            > 1e-10 * native.conversion.scale
    {
        return Err("Coordinate settings changed; migrate/reconcile the native project before changing publication permissions".into());
    }
    Ok(proposed)
}

pub fn publication_request(
    project: &Project,
    node_id: &str,
    edited: &str,
    correspondence: Vec<serde_json::Value>,
    confirmed_deletions: Vec<String>,
    destination: Option<String>,
    token: Option<String>,
) -> Result<serde_json::Value, String> {
    project.validate()?;
    let node = project
        .nodes
        .iter()
        .find(|n| n.id == node_id)
        .ok_or("Application node no longer exists")?;
    if node.data.operation != "application" {
        return Err("Publication requires an Application node".into());
    }
    let native = node
        .data
        .manual_baseline
        .as_ref()
        .or(node.data.native_import.as_ref())
        .ok_or("First import with baseline capture is required before selective publication")?;
    if node.data.manual_baseline.is_none()
        && node.data.native_project_dependency_id.as_ref() != native.native_dependency_id.as_ref()
    {
        return Err(
            "Native project differs from preparation snapshot; reconcile explicitly".into(),
        );
    }
    let policy = node
        .data
        .publication_policy
        .as_ref()
        .unwrap_or(&native.policy);
    if node.data.application_profile_id.as_ref() != Some(&policy.profile.id)
        || node.data.edit_scope != policy.edit_scope
    {
        return Err(
            "Node application/scope changed since preparation; reconcile before publication".into(),
        );
    }
    policy.validate()?;
    let path = |id: &str| -> Result<String, String> {
        let dep = project
            .dependencies
            .iter()
            .find(|d| d.id == id)
            .ok_or("Publication dependency is missing")?;
        Ok(existing_file(&dep.path, "publication input")?
            .to_string_lossy()
            .into())
    };
    if let Some(path) = &destination {
        let root = Path::new(path);
        let edit = root.with_file_name(format!(
            "{}.edits.usda",
            root.file_stem().and_then(|s| s.to_str()).unwrap_or("")
        ));
        if project.dependencies.iter().any(|d| {
            [root.to_path_buf(), edit.clone()].iter().any(|p| {
                crate::project::file_key(&d.path) == crate::project::file_key(&p.to_string_lossy())
                    && !node.data.publication.as_ref().is_some_and(|publication| {
                        d.id == publication.dependency_id
                            || d.id == publication.edit_layer_dependency_id
                    })
            })
        }) {
            return Err(
                "Publication destination conflicts with another registered input; choose a different filename"
                    .into(),
            );
        }
    }
    let edited = existing_export(edited, "edited application export")?;
    let report = &native.report;
    let digest = report
        .get("baselineDigest")
        .and_then(|v| v.as_str())
        .ok_or("Native preparation has no baseline digest")?;
    let revisions = report
        .get("sourceRevisions")
        .and_then(|v| v.as_array())
        .ok_or("Native preparation has no preserved input revisions")?;
    Ok(
        serde_json::json!({"baselineCorrespondence":native.report.get("baselineCorrespondence"),"manualBaseline":node.data.manual_baseline.is_some(),"operation":if destination.is_some(){"publish_reviewed"}else{"review_publication"},"handoff":path(&native.handoff_dependency_id)?,"baseline":path(&native.baseline_dependency_id)?,"edited":edited,"baselineDependencyId":native.baseline_dependency_id,"handoffDependencyId":native.handoff_dependency_id,"baselineDigest":digest,"currentGraph":application_input_graph(project,node_id)?,"sourceRevisions":revisions,"policy":policy,"conversion":native.conversion,"correspondence":correspondence,"confirmedDeletions":confirmed_deletions,"destination":destination,"reviewToken":token}),
    )
}

pub fn reconciliation_request(
    project: &Project,
    node_id: &str,
    destination: &str,
) -> Result<serde_json::Value, String> {
    project.validate()?;
    let node = project
        .nodes
        .iter()
        .find(|n| n.id == node_id)
        .ok_or("Application node no longer exists")?;
    if node.data.operation != "application" {
        return Err("Reconciliation requires an Application node".into());
    }
    let publication = node
        .data
        .publication
        .as_ref()
        .ok_or("Publish node adjustments before reconciling upstream")?;
    let path = Path::new(destination);
    if !path.is_absolute() || path.extension().and_then(|s| s.to_str()) != Some("usda") {
        return Err("Choose an absolute new .usda publication destination".into());
    }
    let stem = path
        .file_stem()
        .and_then(|s| s.to_str())
        .ok_or("Invalid destination")?;
    let targets = [
        path.to_path_buf(),
        path.with_file_name(format!("{stem}.edits.usda")),
        path.with_file_name(format!("{stem}.input.usda")),
    ];
    if targets.iter().any(|p| {
        p.symlink_metadata().is_ok()
            || project.dependencies.iter().any(|d| {
                crate::project::file_key(&d.path) == crate::project::file_key(&p.to_string_lossy())
            })
    }) {
        return Err(
            "Reconciliation destination exists or is registered; choose a new filename".into(),
        );
    }
    let adjustment = project
        .dependencies
        .iter()
        .find(|d| d.id == publication.edit_layer_dependency_id)
        .ok_or("Adjustment layer link missing")?;
    let adjustment = existing_file(&adjustment.path, "published adjustment layer")?;
    Ok(
        serde_json::json!({"operation":"reconcile_upstream","publicationDependencyId":publication.dependency_id,"adjustmentLayer":adjustment,"destination":destination,"currentGraph":application_input_graph(project,node_id)?,"policy":publication.policy,"conversion":publication.conversion,"baselineDependencyId":publication.baseline_dependency_id,"handoffDependencyId":publication.handoff_dependency_id,"report":publication.report,"correspondence":publication.correspondence}),
    )
}

#[cfg(test)]
pub fn prepare(project: &Project, node_id: &str) -> Result<LaunchSpec, String> {
    prepare_with_catalog(
        project,
        node_id,
        &crate::application_profiles::Catalog::defaults(),
    )
}
fn executable_for(
    project: &Project,
    node: &crate::project::Node,
    catalog: &crate::application_profiles::Catalog,
) -> Result<PathBuf, String> {
    if let Some(id) = &node.data.app_executable_dependency_id {
        let file = project
            .dependencies
            .iter()
            .find(|d| &d.id == id)
            .ok_or("Executable link is missing")?;
        return existing_file(&file.path, "application executable");
    }
    let profile = catalog
        .applications
        .iter()
        .find(|p| {
            node.data
                .application_profile_id
                .as_ref()
                .is_some_and(|id| &p.id == id)
        })
        .ok_or("Application profile is unavailable")?;
    existing_file(&profile.executable, "application executable")
}
pub fn prepare_with_catalog(
    project: &Project,
    node_id: &str,
    catalog: &crate::application_profiles::Catalog,
) -> Result<LaunchSpec, String> {
    project.validate()?;
    let node = project
        .nodes
        .iter()
        .find(|n| n.id == node_id)
        .ok_or("Application node no longer exists")?;
    if node.data.operation != "application" {
        return Err("Open in App requires an Application node".into());
    }
    let allowed_extensions: &[&str] = match node.data.app.as_str() {
        "Blender" => &["blend"],
        "Marvelous Designer" => &["zprj", "zpac"],
        "Substance Painter" => &["spp"],
        "ZBrush" => &["zpr", "ztl"],
        _ => {
            return Err("Reopening is currently supported for Blender, Marvelous Designer, Substance Painter, and ZBrush; this application's launch adapter is not implemented".into());
        }
    };
    let resolve = |id: &Option<String>, role: &str| -> Result<PathBuf, String> {
        let dependency = project
            .dependencies
            .iter()
            .find(|d| Some(&d.id) == id.as_ref())
            .ok_or_else(|| format!("Choose the {role} first"))?;
        existing_file(&dependency.path, role)
    };
    let executable = executable_for(project, node, catalog)?;
    let native_project = resolve(&node.data.native_project_dependency_id, "native project")?;
    if !executable
        .extension()
        .is_some_and(|s| s.eq_ignore_ascii_case("exe"))
    {
        return Err(
            "Choose the application's .exe executable; shell scripts and shortcuts are not supported"
                .into(),
        );
    }
    if !native_project
        .extension()
        .and_then(|s| s.to_str())
        .is_some_and(|ext| {
            allowed_extensions
                .iter()
                .any(|allowed| ext.eq_ignore_ascii_case(allowed))
        })
    {
        return Err(format!(
            "{} reopening requires an existing {} native project",
            node.data.app,
            allowed_extensions
                .iter()
                .map(|e| format!(".{e}"))
                .collect::<Vec<_>>()
                .join("/")
        ));
    }
    Ok(LaunchSpec {
        executable,
        native_project,
    })
}

impl LaunchSpec {
    fn command(&self) -> Command {
        let mut command = Command::new(&self.executable);
        command
            .arg(&self.native_project)
            .current_dir(
                self.native_project
                    .parent()
                    .expect("absolute file has parent"),
            )
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        // Blender's GUI remains visible; suppress only the Windows console.
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000); // CREATE_NO_WINDOW
        }
        command
    }
}

fn application_input_graph(project: &Project, node_id: &str) -> Result<serde_json::Value, String> {
    evaluation_graph(project, node_id, true)
}

pub fn evaluation_graph(
    project: &Project,
    node_id: &str,
    application_input: bool,
) -> Result<serde_json::Value, String> {
    use std::collections::HashSet;
    project.validate()?;
    if !project.nodes.iter().any(|n| n.id == node_id) {
        return Err("Application node no longer exists".into());
    }
    let mut needed = HashSet::new();
    let mut pending = vec![node_id];
    while let Some(id) = pending.pop() {
        if !needed.insert(id) {
            continue;
        }
        let item = project.nodes.iter().find(|n| n.id == id).unwrap();
        if (id == node_id && application_input)
            || item.data.operation != "application"
            || item.data.live_adjustment.is_some()
        {
            pending.extend(
                project
                    .edges
                    .iter()
                    .filter(|e| e.target == id)
                    .map(|e| e.source.as_str()),
            );
        }
    }
    let mut used = HashSet::new();
    let nodes: Vec<_> = project.nodes.iter().filter(|n|needed.contains(n.id.as_str())).map(|n| {
        let operation = if n.id == node_id && application_input {if n.data.input_dependency_id.is_some() {"source"} else {"output"}} else {&n.data.operation};
        let published = if n.id == node_id && application_input {None} else {n.data.published_dependency_id.as_ref()};
        if n.data.operation=="materialx"{used.extend(n.data.material_x.texture_dependencies());if let Some(rules)=&n.data.material_x_rules {for r in rules{used.extend(r.material.texture_dependencies());}}}
        used.extend(n.data.input_dependency_id.iter()); used.extend(published);
        if !(n.id == node_id && application_input) {
            used.extend(n.data.native_project_dependency_id.iter());
        }
        used.extend(n.data.asset_overrides.iter().map(|o| &o.replacement_dependency_id));
        if let Some(h) = &n.data.hdri { if let Some(tid) = &h.texture_dependency_id { used.insert(tid); } }
        if let Some(pose) = &n.data.pose { for entry in &pose.entries { used.extend(entry.dependency_id.iter()); } }
        if let Some(ch) = &n.data.character {
            for cc in &ch.cloth_caches {
                if let Some(did) = &cc.dependency_id {
                    if !did.is_empty() {
                        used.insert(did);
                    }
                }
            }
            for hg in &ch.head_grooms {
                if hg.source_mode == "file" {
                    if let Some(did) = &hg.dependency_id {
                        if !did.is_empty() {
                            used.insert(did);
                        }
                    }
                }
            }
        }
        if !(n.id==node_id && application_input) {if let Some(live)=&n.data.live_adjustment {used.insert(&live.edited_dependency_id);used.insert(&live.baseline_dependency_id);used.insert(&live.handoff_dependency_id);}}
        if let Some(id) = &n.data.metahuman_body_dependency_id { used.insert(id); }
        let mut value=serde_json::json!({"id":n.id,"operation":operation,"inputDependencyId":n.data.input_dependency_id,"publishedDependencyId":published,"nativeProjectDependencyId":if n.id == node_id && application_input { None } else { n.data.native_project_dependency_id.as_ref() },"instanced":n.data.instanced.unwrap_or(false),"axis":n.data.axis,"frontAxis":n.data.front_axis,"scale":n.data.scale,"transform":n.data.transform,"assetOverrides":n.data.asset_overrides,"publication":if n.id == node_id && application_input {None} else {n.data.publication.as_ref().map(|p|serde_json::json!({"sourceRevisions":p.source_revisions}))}});
        if n.data.operation == "metahuman" {
            value["metahumanLod"] = serde_json::json!(n.data.metahuman_lod);
            value["metahumanMaterials"] = serde_json::json!(n.data.metahuman_materials);
            if let Some(body_id) = &n.data.metahuman_body_dependency_id {
                value["metahumanBodyDependencyId"] = serde_json::json!(body_id);
            }
        }
        if n.data.operation=="blender"{if let Some(scene)=&n.data.blender_scene{value["blenderScene"]=serde_json::to_value(scene).unwrap();}if let Some(sel)=&n.data.blender_selections{value["blenderSelections"]=serde_json::to_value(sel).unwrap();}if let Some(mode)=&n.data.blender_material_mode{value["blenderMaterialMode"]=serde_json::to_value(mode).unwrap();}if let Some(cp)=n.data.blender_convert_procedurals{value["blenderConvertProcedurals"]=serde_json::to_value(cp).unwrap();}if let Some(es)=n.data.blender_export_skeletons{value["blenderExportSkeletons"]=serde_json::to_value(es).unwrap();}if let Some(secs)=n.data.last_extraction_seconds{value["lastExtractionSeconds"]=serde_json::to_value(secs).unwrap();}}
if n.data.operation=="camera"{if let Some(cam)=&n.data.camera{value["camera"]=serde_json::to_value(cam).unwrap();}if let Some(views)=&n.data.camera_views{value["cameraViews"]=serde_json::to_value(views).unwrap();}}
        if !(n.id==node_id && application_input) {if let Some(live)=&n.data.live_adjustment {value["liveAdjustment"]=serde_json::to_value(live).unwrap();value["publishedDependencyId"]=serde_json::Value::Null;value["publication"]=serde_json::Value::Null;}}
        if n.data.operation=="delete" {value["deletePaths"]=serde_json::to_value(&n.data.delete_paths).unwrap();}
        if n.data.operation=="group"{if let Some(gn)=&n.data.group_name {value["groupName"]=serde_json::to_value(gn).unwrap();}}
        if n.data.operation=="materialx"{value["materialX"]=serde_json::to_value(&n.data.material_x).unwrap();if let Some(rules)=&n.data.material_x_rules {value["materialXRules"]=serde_json::to_value(rules).unwrap();}}
        if n.data.operation=="material_bind"{if let Some(bindings)=&n.data.material_bindings {value["materialBindings"]=serde_json::to_value(bindings).unwrap();}}
        if n.data.operation=="light"{if let Some(overrides)=&n.data.light_overrides {value["lightOverrides"]=serde_json::to_value(overrides).unwrap();}if let Some(ls)=n.data.light_intensity_scale {value["lightIntensityScale"]=serde_json::to_value(ls).unwrap();}}
        if n.data.operation=="hdri"{if let Some(hdri)=&n.data.hdri {value["hdri"]=serde_json::to_value(hdri).unwrap();}}
        if n.data.operation=="postfx"{if let Some(post_fx)=&n.data.post_fx {value["postFx"]=serde_json::to_value(post_fx).unwrap();}}
        if n.data.operation=="hair"{if let Some(hair)=&n.data.hair {value["hair"]=serde_json::to_value(hair).unwrap();}}
        if n.data.operation=="cache_edit"{if let Some(ce)=&n.data.cache_edit {value["cacheEdit"]=serde_json::to_value(ce).unwrap();}}
        if n.data.operation=="pose" { value["pose"]=serde_json::to_value(&n.data.pose).unwrap(); }
        if n.data.operation=="character"{if let Some(ch)=&n.data.character {value["character"]=serde_json::to_value(ch).unwrap();}}
        if !(n.id == node_id && application_input) {
            if let Some(publication)=&n.data.publication {if !publication.revision.is_empty(){value["publication"]["revision"]=publication.revision.clone().into();}}
        }
        value
    }).collect();
    let edges: Vec<_> = project
        .edges
        .iter()
        .filter(|e| needed.contains(e.source.as_str()) && needed.contains(e.target.as_str()))
        .map(|e| {
            let id = project
                .nodes
                .iter()
                .find(|n| n.id == e.target)
                .and_then(|n| n.data.handoff.as_ref())
                .and_then(|h| h.input_edges.get(&e.source))
                .unwrap_or(&e.id);
            serde_json::json!({"id":id,"source":e.source,"target":e.target})
        })
        .collect();
    let dependencies: Vec<_> = project
        .dependencies
        .iter()
        .filter(|d| used.contains(&d.id))
        .map(|d| serde_json::json!({"id":d.id,"path":d.path}))
        .collect();
    Ok(
        serde_json::json!({"namespaceVersion":2,"targetNodeId":node_id,"nodes":nodes,"edges":edges,"dependencies":dependencies}),
    )
}

/// Build the input graph from validated project data, never from a client-supplied output graph.
pub fn validate_quickstart_paths(
    project: &Project,
    native: &str,
    handoff: &str,
) -> Result<(), String> {
    let native = Path::new(native);
    if !native.is_absolute()
        || !native
            .extension()
            .is_some_and(|ext| ext.eq_ignore_ascii_case("blend"))
    {
        return Err("Choose an absolute .blend destination".into());
    }
    let baseline = native.with_extension("baseline.usdc");
    let [native, baseline, handoff] = [
        native.to_string_lossy(),
        baseline.to_string_lossy(),
        handoff.into(),
    ]
    .map(|path| crate::project::file_key(&path));
    if native == baseline || native == handoff || baseline == handoff {
        return Err("Quick start requires distinct output files".into());
    }
    if project.dependencies.iter().any(|dependency| {
        [&native, &baseline, &handoff].contains(&&crate::project::file_key(&dependency.path))
    }) {
        return Err(
            "A Quick start destination is already registered. Choose a new .blend name or folder"
                .into(),
        );
    }
    Ok(())
}

pub fn handoff_request(
    project: &Project,
    node_id: &str,
    destination: &str,
    catalog: &crate::application_profiles::Catalog,
) -> Result<serde_json::Value, String> {
    project.validate()?;
    let node = project
        .nodes
        .iter()
        .find(|n| n.id == node_id)
        .ok_or("Application node no longer exists")?;
    if node.data.operation != "application" || node.data.app.is_empty() {
        return Err("Choose an application on an Application node first".into());
    }
    if project.nodes.iter().filter(|n| n.id != node_id).any(|n| {
        n.data.handoff.as_ref().is_some_and(|h| {
            project.dependencies.iter().any(|d| {
                d.id == h.dependency_id
                    && crate::project::file_key(&d.path) == crate::project::file_key(destination)
            })
        })
    }) {
        return Err(
            "This handoff is used by another node. Reuse it unchanged, or choose a new destination"
                .into(),
        );
    }
    if project.dependencies.iter().any(|d| {
        crate::project::file_key(&d.path) == crate::project::file_key(destination)
            && node.data.handoff.as_ref().map(|h| h.dependency_id.as_str()) != Some(d.id.as_str())
            && !([
                node.data
                    .manual_baseline
                    .as_ref()
                    .map(|v| v.handoff_dependency_id.as_str()),
                node.data
                    .native_import
                    .as_ref()
                    .map(|v| v.handoff_dependency_id.as_str()),
                node.data
                    .native_preparation
                    .as_ref()
                    .map(|v| v.handoff_dependency_id.as_str()),
                node.data
                    .live_adjustment
                    .as_ref()
                    .map(|v| v.handoff_dependency_id.as_str()),
            ]
            .into_iter()
            .flatten()
            .any(|id| id == d.id))
    }) {
        return Err(
            "Handoff destination is already a registered dependency. Choose a new filename".into(),
        );
    }
    if project
        .nodes
        .iter()
        .filter_map(|n| n.data.input_dependency_id.as_ref())
        .any(|id| {
            project.dependencies.iter().any(|d| {
                &d.id == id
                    && crate::project::file_key(&d.path) == crate::project::file_key(destination)
            })
        })
    {
        return Err("Handoff destination is used as a scene input; choose another filename".into());
    }
    let policy = catalog.policy(&node.data)?;
    let axis = if node.data.axis == "preserve" {
        policy.profile.up_axis.as_str()
    } else {
        node.data.axis.as_str()
    };
    let units = node
        .data
        .application_meters_per_unit
        .unwrap_or(policy.profile.meters_per_unit);

    Ok(
        serde_json::json!({"selection":node.data.handoff_selection,"policy":policy,"operation":"handoff_export","destination":destination,"graph":application_input_graph(project,node_id)?,"app":node.data.app,"upAxis":axis,"metersPerUnit":units,"scale":node.data.scale * policy.profile.scale}),
    )
}

pub fn open(
    project: &Project,
    node_id: &str,
    catalog: &crate::application_profiles::Catalog,
) -> Result<u32, String> {
    let spec = prepare_with_catalog(project, node_id, catalog)?;
    let mut child = spec
        .command()
        .spawn()
        .map_err(|e| format!("Unable to launch application: {e}"))?;
    let pid = child.id();
    // Reap the launched process without blocking Tauri or treating exit as publish.
    std::thread::spawn(move || {
        let _ = child.wait();
    });
    Ok(pid)
}

fn capture_tail(
    mut stream: impl std::io::Read + Send + 'static,
) -> std::thread::JoinHandle<String> {
    std::thread::spawn(move || {
        let mut tail = Vec::new();
        let mut buffer = [0u8; 8192];
        while let Ok(count) = stream.read(&mut buffer) {
            if count == 0 {
                break;
            }
            tail.extend_from_slice(&buffer[..count]);
            if tail.len() > 20 * 1024 * 1024 {
                tail.drain(..tail.len() - 20 * 1024 * 1024);
            }
        }
        String::from_utf8_lossy(&tail).into_owned()
    })
}

pub fn create_blender_project(
    project: &Project,
    node_id: &str,
    destination: &str,
    overwrite: bool,
    capture_baseline: bool,
    overwrite_baseline: bool,
    catalog: &crate::application_profiles::Catalog,
) -> Result<serde_json::Value, String> {
    use std::io::Write;
    project.validate()?;
    let node = project
        .nodes
        .iter()
        .find(|n| n.id == node_id)
        .ok_or("Application node no longer exists")?;
    if node.data.operation != "application" || node.data.app != "Blender" {
        return Err(
            "First import is currently implemented for Blender Application nodes only".into(),
        );
    }
    if node.data.native_project_dependency_id.is_some() {
        return Err("This node already links a native project. Reopen or relink it; automatic reimport is not allowed".into());
    }
    let handoff = node
        .data
        .handoff
        .as_ref()
        .ok_or("Create a handoff USD first")?;
    let file = |id: &str, role: &str| -> Result<PathBuf, String> {
        let entry = project
            .dependencies
            .iter()
            .find(|d| d.id == id)
            .ok_or_else(|| format!("Missing {role} link"))?;
        existing_file(&entry.path, role)
    };
    let source = file(&handoff.dependency_id, "handoff USD")?;
    let policy = catalog.policy(&node.data)?;
    if policy.profile.adapter_version != 2 {
        return Err("This build supports Blender adapter version 2 only".into());
    }
    if !handoff.policy.same_preparation_contract(&policy) {
        return Err("Application capabilities or node scope differ from the handoff snapshot. Recreate the handoff before first import".into());
    }
    let executable = executable_for(project, node, catalog)?;
    if !executable
        .extension()
        .is_some_and(|s| s.eq_ignore_ascii_case("exe"))
    {
        return Err("Choose Blender's .exe executable".into());
    }
    let destination = Path::new(destination);
    if !destination.is_absolute()
        || !destination
            .extension()
            .is_some_and(|s| s.eq_ignore_ascii_case("blend"))
    {
        return Err("Choose an absolute .blend destination".into());
    }
    if destination.symlink_metadata().is_ok() && !overwrite {
        return Err("Native destination already exists. Choose a new filename".into());
    }
    let parent = destination
        .parent()
        .ok_or("Destination directory is required")?;
    if !parent.is_dir() {
        return Err("Destination directory does not exist".into());
    }
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_nanos();
    let temporary = parent.join(format!(
        ".orchflow-create-{}-{stamp}.blend",
        std::process::id()
    ));
    let baseline = destination.with_extension("baseline.usdc");
    if capture_baseline && !overwrite_baseline && baseline.symlink_metadata().is_ok() {
        return Err(
            "Baseline destination already exists. Confirm replacement or choose a new filename"
                .into(),
        );
    }
    let targets =
        std::iter::once(destination).chain(capture_baseline.then_some(baseline.as_path()));
    if targets
        .map(|path| crate::project::file_key(&path.to_string_lossy()))
        .any(|target| {
            project
                .dependencies
                .iter()
                .any(|d| crate::project::file_key(&d.path) == target)
        })
    {
        return Err(
            "Native/baseline destination is already a registered dependency; choose a new filename"
                .into(),
        );
    }
    let baseline_temporary = parent.join(format!(
        ".orchflow-baseline-{}-{stamp}.usdc",
        std::process::id()
    ));
    let reserved = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)
        .map_err(|e| format!("Cannot reserve native project output: {e}"))?;
    drop(reserved);
    let result = (|| {
        let mut command = Command::new(&executable);
        command
            .args([
                "--background",
                "--factory-startup",
                "--disable-autoexec",
                "--python-exit-code",
                "11",
                "--python-expr",
                include_str!("../../worker/blender_create.py"),
            ])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .current_dir(parent);
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let mut child = command
            .spawn()
            .map_err(|e| format!("Cannot start Blender: {e}"))?;
        let stdout = capture_tail(child.stdout.take().unwrap());
        let stderr = capture_tail(child.stderr.take().unwrap());
        let config = serde_json::json!({"handoff":source,"destination":destination,"temporary":temporary,"baseline":baseline,"baselineTemporary":baseline_temporary,"captureBaseline":capture_baseline,"overwrite":overwrite,"overwriteBaseline":overwrite_baseline,"conversion":handoff.conversion,"policy":policy});
        let sent = child
            .stdin
            .take()
            .unwrap()
            .write_all(format!("{}\n", config).as_bytes());
        if sent.is_err() {
            let _ = child.kill();
            let _ = child.wait();
            return Err("Could not send first-import request to Blender".into());
        }
        let started = std::time::Instant::now();
        let status = loop {
            if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
                break status;
            }
            if started.elapsed() > std::time::Duration::from_secs(900) {
                let _ = child.kill();
                let _ = child.wait();
                return Err(
                    "Blender import timed out after 15 minutes; no native link was changed".into(),
                );
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        };
        let output = stdout.join().map_err(|_| "Blender output reader failed")?;
        let errors = stderr.join().map_err(|_| "Blender error reader failed")?;
        if !status.success() {
            return Err(format!("Blender import failed. {errors} {output}"));
        }
        let report = output
            .lines()
            .find_map(|line| line.strip_prefix("ORCHFLOW_CREATE:"))
            .ok_or_else(|| {
                format!("Blender returned no completed import report. {output} {errors}")
            })?;
        let mut report: serde_json::Value =
            serde_json::from_str(report).map_err(|e| e.to_string())?;
        let metadata = std::fs::metadata(destination)
            .map_err(|e| format!("Blender did not create the native project: {e}"))?;
        if metadata.len() < 12 {
            return Err("Created native project is empty".into());
        }
        if report["baselineCaptured"].as_bool() != Some(capture_baseline)
            || capture_baseline
                && (report["baselineDigest"].as_str().is_none_or(|digest| {
                    digest.len() != 64 || !digest.bytes().all(|b| b.is_ascii_hexdigit())
                }) || !baseline.is_file())
        {
            return Err("Blender did not return the requested validated outputs".into());
        }
        report["nativeFileSize"] = metadata.len().into();
        report["executablePath"] = executable.to_string_lossy().as_ref().into();
        Ok(
            serde_json::json!({"path":destination,"baselinePath":capture_baseline.then_some(&baseline),"handoffDependencyId":handoff.dependency_id,"conversion":handoff.conversion,"policy":policy,"report":report}),
        )
    })();
    if baseline_temporary.exists() {
        let _ = std::fs::remove_file(&baseline_temporary);
    }
    if temporary.exists() {
        let _ = std::fs::remove_file(&temporary);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scope_authorization_is_explicit_and_publication_uses_frozen_coordinates() {
        let dir = std::env::temp_dir().join(format!(
            "orchflow-publish-policy-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir(&dir).unwrap();
        for name in [
            "native.blend",
            "handoff.usda",
            "baseline.usdc",
            "edited.usda",
        ] {
            std::fs::write(dir.join(name), b"fixture; never executed").unwrap();
        }
        let catalog = crate::application_profiles::Catalog::defaults();
        let policy = serde_json::json!({"profile":catalog.applications[0],"editScope":[]});
        let identity = [
            [1., 0., 0., 0.],
            [0., 1., 0., 0.],
            [0., 0., 1., 0.],
            [0., 0., 0., 1.],
        ];
        let conversion = serde_json::json!({"version":1,"app":"Blender","upAxis":"Y","metersPerUnit":1,"scale":1,"factor":1,"forward":identity,"inverse":identity,"inputRevision":"a".repeat(64),"graphRevision":"b".repeat(64),"usdVersion":"25.8"});
        let value = serde_json::json!({"version":2,"nodes":[{"id":"app","type":"stage","position":{"x":0,"y":0},"data":{"label":"Blender","operation":"application","app":"Blender","applicationProfileId":"blender","axis":"Y","scale":1,"nativeProjectDependencyId":"native","nativeImport":{"nativeDependencyId":"native","baselineDependencyId":"baseline","handoffDependencyId":"handoff","policy":policy,"conversion":conversion,"report":{"adapterVersion":2,"blenderVersion":"5.2.1","baselineDigest":"0".repeat(64),"sourceRevisions":[]}}}}],"edges":[],"dependencies":[{"id":"native","kind":"nativeProject","path":dir.join("native.blend")},{"id":"baseline","kind":"usd","path":dir.join("baseline.usdc")},{"id":"handoff","kind":"usd","path":dir.join("handoff.usda")}]});
        let mut project = Project::from_value(value).unwrap();
        let mut updated = catalog.clone();
        project.nodes[0].data.edit_scope = vec!["points".into()];
        assert!(proposed_publication_policy(&project, "app", &updated).is_err());
        updated.applications[0]
            .capabilities
            .get_mut("points")
            .unwrap()
            .status = "supported".into();
        updated.applications[0].revision += 1;
        let proposed = proposed_publication_policy(&project, "app", &updated).unwrap();
        assert!(
            publication_request(
                &project,
                "app",
                dir.join("edited.usda").to_str().unwrap(),
                vec![],
                vec![],
                None,
                None
            )
            .is_err()
        );
        project.nodes[0].data.publication_policy = Some(proposed);
        let request = publication_request(
            &project,
            "app",
            dir.join("edited.usda").to_str().unwrap(),
            vec![],
            vec![],
            None,
            None,
        )
        .unwrap();
        assert_eq!(request["operation"], "review_publication");
        assert_eq!(request["policy"]["editScope"][0], "points");
        assert_eq!(request["conversion"]["upAxis"], "Y");
        assert!(
            project.nodes[0]
                .data
                .native_import
                .as_ref()
                .unwrap()
                .policy
                .edit_scope
                .is_empty()
        );
        project.nodes[0].data.publication=Some(serde_json::from_value(serde_json::json!({"dependencyId":"baseline","editLayerDependencyId":"handoff","editedDependencyId":"baseline","baselineDependencyId":"baseline","handoffDependencyId":"handoff","policy":request["policy"],"conversion":request["conversion"],"report":{},"sourceRevisions":[],"correspondence":[]})).unwrap());
        project.nodes[0].data.published_dependency_id = Some("baseline".into());
        project.nodes[0].data.axis = "Z".into();
        let reconciled = reconciliation_request(
            &project,
            "app",
            dir.join("reconciled.usda").to_str().unwrap(),
        )
        .unwrap();
        assert_eq!(reconciled["operation"], "reconcile_upstream");
        assert_eq!(reconciled["conversion"]["upAxis"], "Y");
        assert_eq!(
            reconciled["currentGraph"]["nodes"][0]["publication"],
            serde_json::Value::Null
        );
        assert!(
            reconciliation_request(&project, "app", dir.join("baseline.usdc").to_str().unwrap())
                .is_err()
        );
        project.nodes[0].data.scale = 2.;
        assert!(proposed_publication_policy(&project, "app", &updated).is_err());
        for name in [
            "native.blend",
            "handoff.usda",
            "baseline.usdc",
            "edited.usda",
        ] {
            std::fs::remove_file(dir.join(name)).unwrap();
        }
        std::fs::remove_dir(dir).unwrap();
    }
    #[test]
    fn direct_arguments_preserve_spaces_and_shell_metacharacters() {
        let spec = LaunchSpec {
            executable: PathBuf::from("C:/Program Files/Blender/blender.exe"),
            native_project: PathBuf::from("I:/art & projects/artist's $(model).blend"),
        };
        let command = spec.command();
        assert_eq!(command.get_program(), spec.executable.as_os_str());
        assert_eq!(
            command.get_args().collect::<Vec<_>>(),
            vec![spec.native_project.as_os_str()]
        );
    }

    #[test]
    fn quickstart_rejects_registered_destinations_but_accepts_fresh_paths() {
        let dir = std::env::temp_dir();
        let native = dir.join("quickstart-old.blend");
        let baseline = dir.join("quickstart-old.baseline.usdc");
        let handoff = dir.join("quickstart-old_handoff.usd");
        let project = Project::from_value(serde_json::json!({
            "version": 2, "nodes": [], "edges": [],
            "dependencies": [
                {"id": "native", "kind": "nativeProject", "path": native},
                {"id": "baseline", "kind": "usd", "path": baseline},
                {"id": "handoff", "kind": "usd", "path": handoff},
            ]
        }))
        .unwrap();
        let new_native = dir.join("quickstart-fresh.blend");
        let new_handoff = dir.join("quickstart-fresh_handoff.usd");
        assert!(
            validate_quickstart_paths(
                &project,
                new_native.to_str().unwrap(),
                new_handoff.to_str().unwrap()
            )
            .is_ok()
        );
        assert!(
            validate_quickstart_paths(
                &project,
                native.to_str().unwrap(),
                new_handoff.to_str().unwrap()
            )
            .is_err()
        );
        assert!(
            validate_quickstart_paths(
                &project,
                new_native.to_str().unwrap(),
                handoff.to_str().unwrap()
            )
            .is_err()
        );
        let mut baseline_only = project;
        baseline_only
            .dependencies
            .retain(|dependency| dependency.id == "baseline");
        assert!(
            validate_quickstart_paths(
                &baseline_only,
                native.to_str().unwrap(),
                new_handoff.to_str().unwrap()
            )
            .is_err()
        );
    }

    #[test]
    fn validate_reopen_without_writing_or_launching() {
        let dir = std::env::temp_dir().join(format!(
            "orchflow-launch-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir(&dir).unwrap();
        let executable = dir.join("blender.exe");
        let native = dir.join("native & model.blend");
        std::fs::write(&executable, b"fixture, never launched").unwrap();
        std::fs::write(&native, b"native edits").unwrap();
        let value = serde_json::json!({"version":2,"nodes":[{"id":"app","type":"stage","position":{"x":0,"y":0},"data":{
            "label":"Blender","operation":"application","app":"Blender","applicationProfileId":"blender","axis":"Z","scale":2,
            "nativeProjectDependencyId":"native","appExecutableDependencyId":"exe"}}],"edges":[],
            "dependencies":[{"id":"exe","kind":"executable","path":executable},{"id":"native","kind":"nativeProject","path":native}]});
        let mut project = Project::from_value(value).unwrap();
        assert!(prepare(&project, "app").is_ok());
        assert!(
            create_blender_project(
                &project,
                "app",
                "I:/never-created.blend",
                false,
                true,
                false,
                &crate::application_profiles::Catalog::defaults()
            )
            .unwrap_err()
            .contains("already links")
        );
        let handoff = handoff_request(
            &project,
            "app",
            "I:/handoff.usda",
            &crate::application_profiles::Catalog::defaults(),
        )
        .unwrap();
        assert_eq!(handoff["graph"]["nodes"][0]["operation"], "output");
        assert!(handoff["graph"]["nodes"][0]["publishedDependencyId"].is_null());
        assert_eq!(handoff["scale"], 2.0);
        assert_eq!(handoff["upAxis"], "Z");
        assert_eq!(
            handoff["graph"]["dependencies"].as_array().unwrap().len(),
            0
        );

        assert_eq!(std::fs::read(&native).unwrap(), b"native edits");
        let saved = serde_json::to_value(&project).unwrap();
        let restored = Project::from_value(saved).unwrap();
        assert_eq!(
            restored.nodes[0]
                .data
                .app_executable_dependency_id
                .as_deref(),
            Some("exe")
        );
        project.dependencies[0].path = dir.to_string_lossy().into();
        assert!(
            prepare(&project, "app")
                .err()
                .unwrap()
                .contains("directory")
        );
        project.dependencies[0].path = executable.to_string_lossy().into();
        project.dependencies[1].path = "relative.blend".into();
        assert!(prepare(&project, "app").err().unwrap().contains("absolute"));
        project.dependencies[1].path = executable.to_string_lossy().into();
        assert!(prepare(&project, "app").err().unwrap().contains(".blend"));
        project.dependencies[1].path = native.to_string_lossy().into();
        project.nodes[0].data.operation = "source".into();
        assert!(prepare(&project, "app").is_err());
        project.nodes[0].data.operation = "application".into();
        project.nodes[0].data.app = "Maya".into();
        assert!(
            prepare(&project, "app")
                .err()
                .unwrap()
                .contains("launch adapter is not implemented")
        );
        project.nodes[0].data.app = "Blender".into();
        std::fs::remove_file(&native).unwrap();
        assert!(prepare(&project, "app").is_err());
        assert!(
            !native.exists(),
            "missing native projects must never be recreated"
        );
        std::fs::remove_file(executable).unwrap();
        std::fs::remove_dir(dir).unwrap();
    }

    #[test]
    fn validate_reopen_marvelous_designer_without_launching() {
        let dir = std::env::temp_dir().join(format!(
            "orchflow-md-launch-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir(&dir).unwrap();
        let executable = dir.join("MarvelousDesigner.exe");
        let native = dir.join("garment.zprj");
        std::fs::write(&executable, b"fixture, never launched").unwrap();
        std::fs::write(&native, b"zprj fixture").unwrap();
        let value = serde_json::json!({
            "version": 2,
            "nodes": [{
                "id": "md",
                "type": "stage",
                "position": {"x": 0, "y": 0},
                "data": {
                    "label": "Marvelous Designer",
                    "operation": "application",
                    "app": "Marvelous Designer",
                    "applicationProfileId": "marvelous-designer",
                    "axis": "Y",
                    "scale": 1,
                    "nativeProjectDependencyId": "native",
                    "appExecutableDependencyId": "exe"
                }
            }],
            "edges": [],
            "dependencies": [
                {"id": "exe", "kind": "executable", "path": executable},
                {"id": "native", "kind": "nativeProject", "path": native}
            ]
        });
        let project = Project::from_value(value).unwrap();
        let spec = prepare(&project, "md").unwrap();
        assert_eq!(spec.executable, executable);
        assert_eq!(spec.native_project, native);
        let _ = std::fs::remove_dir_all(dir);
    }
}

pub fn export_materialx(
    project: &Project,
    node_id: &str,
    destination: &str,
    service: &crate::usd::UsdService,
) -> Result<serde_json::Value, String> {
    project.validate()?;
    let node = project
        .nodes
        .iter()
        .find(|n| n.id == node_id && n.data.operation == "materialx")
        .ok_or("Choose a MaterialX node")?;
    let path = std::path::Path::new(&destination);
    if !path.is_absolute()
        || path.extension().and_then(|v| v.to_str()) != Some("mtlx")
        || project
            .dependencies
            .iter()
            .any(|d| crate::project::file_key(&d.path) == crate::project::file_key(&destination))
    {
        return Err("Choose a new .mtlx destination outside existing dependencies".into());
    }
    let rule_id = node
        .data
        .material_x_rules
        .as_ref()
        .and_then(|rules| rules.first())
        .map(|r| r.id.as_str());
    let doc = service.request(serde_json::json!({
        "operation": "materialx_document",
        "materialX": node.data.material_x,
        "dependencies": project.dependencies,
        "proceduralGraph": project.procedural_graph,
        "materialXNodeId": node_id,
        "ruleId": rule_id,
    }))?;
    let content = doc["document"]
        .as_str()
        .ok_or("MaterialX document missing")?
        .as_bytes();
    use std::io::Write;
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    if let Err(e) = file.write_all(content).and_then(|_| file.sync_all()) {
        drop(file);
        let _ = std::fs::remove_file(path);
        return Err(e.to_string());
    }
    Ok(serde_json::json!({"path":destination}))
}

/// Validate manually exported baseline without rewriting native-import provenance.
pub fn manual_baseline_request(
    project: &Project,
    node_id: &str,
    baseline: &str,
    catalog: &crate::application_profiles::Catalog,
) -> Result<serde_json::Value, String> {
    project.validate()?;
    let node = project
        .nodes
        .iter()
        .find(|n| n.id == node_id)
        .ok_or("Node missing")?;
    if node.data.operation != "application" || node.data.app.is_empty() {
        return Err("Choose a configured application profile before linking a baseline".into());
    }
    let native_id: Option<&str> = None;
    let handoff = node
        .data
        .handoff
        .as_ref()
        .ok_or("Create handoff USD first")?;
    let policy = catalog.policy(&node.data)?;
    if !handoff.policy.same_preparation_contract(&policy) {
        return Err(
            "Settings differ from the handoff; reconcile or prepare a new handoff first".into(),
        );
    }
    let axis = if node.data.axis == "preserve" {
        &policy.profile.up_axis
    } else {
        &node.data.axis
    };
    let unit = node
        .data
        .application_meters_per_unit
        .unwrap_or(policy.profile.meters_per_unit);
    if axis != &handoff.conversion.up_axis
        || (unit - handoff.conversion.meters_per_unit).abs() > 1e-10 * unit
        || (node.data.scale * policy.profile.scale - handoff.conversion.scale).abs()
            > 1e-10 * handoff.conversion.scale
    {
        return Err("Coordinate settings differ from the recorded handoff".into());
    }
    let source = project
        .dependencies
        .iter()
        .find(|d| d.id == handoff.dependency_id)
        .ok_or("Handoff link missing")?;
    let baseline = existing_export(baseline, "manual baseline")?;
    if crate::project::file_key(&baseline.to_string_lossy())
        == crate::project::file_key(&source.path)
    {
        return Err(
            "Baseline must be a separate unedited application export, not the handoff".into(),
        );
    }
    Ok(
        serde_json::json!({"operation":"validate_manual_baseline","baseline":baseline,"handoff":existing_file(&source.path,"handoff")?,"currentGraph":application_input_graph(project,node_id)?,"policy":policy,"conversion":handoff.conversion,"nativeDependencyId":native_id,"handoffDependencyId":handoff.dependency_id}),
    )
}

pub fn reuse_handoff_request(
    project: &Project,
    node_id: &str,
    path: &str,
    catalog: &crate::application_profiles::Catalog,
) -> Result<serde_json::Value, String> {
    project.validate()?;
    let node = project
        .nodes
        .iter()
        .find(|n| n.id == node_id && n.data.operation == "application")
        .ok_or("Choose an Application node")?;
    if node.data.native_project_dependency_id.is_some() {
        return Err("This node already has a native project; changing its handoff requires explicit new preparation".into());
    }
    let donor = project
        .nodes
        .iter()
        .find(|n| {
            n.id != node_id
                && n.data.handoff.as_ref().is_some_and(|h| {
                    project.dependencies.iter().any(|d| {
                        d.id == h.dependency_id
                            && crate::project::file_key(&d.path) == crate::project::file_key(path)
                    })
                })
        })
        .ok_or("Select a handoff already prepared by another node in this project")?;
    let handoff = donor.data.handoff.as_ref().unwrap();
    let policy = catalog.policy(&node.data)?;
    let axis = if node.data.axis == "preserve" {
        policy.profile.up_axis.as_str()
    } else {
        node.data.axis.as_str()
    };
    let unit = node
        .data
        .application_meters_per_unit
        .unwrap_or(policy.profile.meters_per_unit);
    let scale = node.data.scale * policy.profile.scale;
    if axis != handoff.conversion.up_axis
        || (unit - handoff.conversion.meters_per_unit).abs() > 1e-10 * unit
        || (scale - handoff.conversion.scale).abs() > 1e-10 * scale
    {
        return Err("Handoff axis, units or scale differ. Create a separate handoff".into());
    }
    let layer_paths: Vec<_> = handoff
        .layer_dependency_ids
        .iter()
        .map(|id| {
            project
                .dependencies
                .iter()
                .find(|d| &d.id == id)
                .map(|d| d.path.clone())
                .ok_or("Generated handoff layer link missing")
        })
        .collect::<Result<_, _>>()?;
    let round_trip = donor
        .data
        .native_import
        .as_ref()
        .and_then(|n| n.report.get("roundTripId"))
        .cloned();
    Ok(
        serde_json::json!({"selection":handoff.selection,"operation":"reuse_handoff","path":existing_file(path,"shared handoff")?,"graph":application_input_graph(project,node_id)?,"donorGraph":application_input_graph(project,&donor.id)?,"conversion":handoff.conversion,"policy":policy,"layerPaths":layer_paths,"roundTripId":round_trip}),
    )
}
