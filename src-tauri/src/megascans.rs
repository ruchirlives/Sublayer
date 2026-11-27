//! Central Megascans & Fab library scanner, browser webpage drag-to-canvas resolver,
//! local Downloads auto-ingest watcher, and optional Quixel/Fab TCP socket listener.
//!
//! Keeps `.sublayer` project files lightweight: heavy meshes and textures live only in
//! the configured central folder (e.g. `I:\EpicGames\Megascans`) and are linked in place.

use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, HashMap, HashSet},
    io::Read,
    net::TcpListener,
    path::{Path, PathBuf},
    process::Command,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant, SystemTime},
};

fn default_true() -> bool {
    true
}
fn default_socket_port() -> u16 {
    24981
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct MegascansConfig {
    #[serde(default)]
    pub library_root: String,
    #[serde(default)]
    pub downloads_dirs: Vec<String>,
    #[serde(default = "default_true")]
    pub socket_enabled: bool,
    #[serde(default = "default_socket_port")]
    pub socket_port: u16,
}

impl Default for MegascansConfig {
    fn default() -> Self {
        Self {
            library_root: detect_default_library_root(),
            downloads_dirs: default_downloads_dirs(),
            socket_enabled: true,
            socket_port: 24981,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MegascansAssetSummary {
    pub id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub listing_uid: Option<String>,
    pub name: String,
    pub asset_type: String,
    pub folder_path: String,
    pub primary_path: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub preview_image_path: Option<String>,
    pub tags: Vec<String>,
    pub available_lods: Vec<u32>,
    pub available_resolutions: Vec<String>,
    pub mesh_formats: Vec<String>,
    pub pbr_maps: BTreeMap<String, String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FabDropOutcome {
    pub status: String,
    pub drop_id: String,
    pub title: String,
    pub url: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub asset: Option<MegascansAssetSummary>,
    pub message: String,
}

pub fn detect_default_library_root() -> String {
    let candidate = PathBuf::from(r"I:\EpicGames\Megascans");
    if candidate.is_dir() {
        return candidate.to_string_lossy().into_owned();
    }
    String::new()
}

fn expand_env_vars(raw: &str) -> String {
    let mut out = String::new();
    let mut rest = raw;
    while let Some(start) = rest.find('%') {
        out.push_str(&rest[..start]);
        let after = &rest[start + 1..];
        match after.find('%') {
            Some(end) => {
                let name = &after[..end];
                match std::env::var(name) {
                    Ok(v) => out.push_str(&v),
                    Err(_) => {
                        out.push('%');
                        out.push_str(name);
                        out.push('%');
                    }
                }
                rest = &after[end + 1..];
            }
            None => {
                out.push('%');
                rest = after;
            }
        }
    }
    out.push_str(rest);
    out
}

/// Real Windows Downloads folder (may be relocated, e.g. `M:\Desktop\Downloads`).
pub fn windows_known_downloads_dir() -> Option<PathBuf> {
    let mut cmd = Command::new("reg.exe");
    cmd.args([
        "query",
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        "/v",
        "{374DE290-123F-4565-9164-39C4925E467B}",
    ]);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000);
    }
    let out = cmd.output().ok()?;
    let text = String::from_utf8_lossy(&out.stdout);
    for line in text.lines() {
        if let Some(idx) = line.find("REG_") {
            let after = &line[idx..];
            let value = after
                .split_whitespace()
                .skip(1)
                .collect::<Vec<_>>()
                .join(" ");
            if !value.is_empty() {
                let p = PathBuf::from(expand_env_vars(&value));
                if p.is_dir() {
                    return Some(p);
                }
            }
        }
    }
    None
}

pub fn detect_default_downloads_dir() -> String {
    if let Some(p) = windows_known_downloads_dir() {
        return p.to_string_lossy().into_owned();
    }
    if let Some(user_profile) = std::env::var_os("USERPROFILE") {
        let dl = PathBuf::from(user_profile).join("Downloads");
        if dl.is_dir() {
            return dl.to_string_lossy().into_owned();
        }
    }
    String::new()
}

fn default_downloads_dirs() -> Vec<String> {
    let path = detect_default_downloads_dir();
    if path.is_empty() { vec![] } else { vec![path] }
}

impl MegascansConfig {
    pub fn validate(&self) -> Result<(), String> {
        if self.socket_port < 1024 {
            return Err("Socket port must be between 1024 and 65535".into());
        }
        if self.downloads_dirs.len() > 16 {
            return Err("At most 16 Downloads folders are supported".into());
        }
        for path in std::iter::once(&self.library_root).chain(self.downloads_dirs.iter()) {
            if !path.is_empty() && (!Path::new(path).is_absolute() || !Path::new(path).is_dir()) {
                return Err(format!(
                    "Choose an absolute existing folder or leave it empty: {path}"
                ));
            }
        }
        Ok(())
    }
}

/// Shared application catalog is the only settings destination.
pub fn load_config(path: &Path) -> Result<MegascansConfig, String> {
    let bytes = if path.is_file() {
        Some(std::fs::read(path).map_err(|e| e.to_string())?)
    } else {
        None
    };
    if let Some(bytes) = bytes {
        let value: serde_json::Value = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
        if let Some(settings) = value.get("megascansLibrary") {
            return serde_json::from_value(settings.clone()).map_err(|e| e.to_string());
        }
    }
    let legacy = path.with_file_name("megascans_library.json");
    if legacy.is_file() {
        let mut value: serde_json::Value =
            serde_json::from_slice(&std::fs::read(legacy).map_err(|e| e.to_string())?)
                .map_err(|e| e.to_string())?;
        if let Some(dir) = value
            .get("downloadsDir")
            .and_then(|v| v.as_str())
            .map(str::to_owned)
        {
            value["downloadsDirs"] = serde_json::json!([dir]);
            value.as_object_mut().unwrap().remove("downloadsDir");
        }
        return serde_json::from_value(value).map_err(|e| e.to_string());
    }
    Ok(MegascansConfig::default())
}

const MESH_EXTS: &[&str] = &[
    "usd", "usdc", "usda", "usdz", "abc", "fbx", "glb", "gltf", "obj",
];
const IMG_EXTS: &[&str] = &["jpg", "jpeg", "png", "exr", "tif", "tiff", "tga", "webp"];
const HDRI_EXTS: &[&str] = &["hdr", "exr"];

fn mesh_ext_priority(ext: &str) -> usize {
    MESH_EXTS
        .iter()
        .position(|e| e.eq_ignore_ascii_case(ext))
        .unwrap_or(999)
}

fn classify_pbr_channel(stem_lower: &str) -> Option<&'static str> {
    if stem_lower.contains("_thumb")
        || stem_lower.contains("_preview")
        || stem_lower.contains("_retina")
        || stem_lower.contains("_brush")
        || stem_lower.ends_with("_sp")
    {
        return None;
    }
    let tokens: Vec<&str> = stem_lower
        .split(|c: char| !c.is_ascii_alphanumeric())
        .filter(|s| !s.is_empty())
        .collect();
    let has = |w: &str| tokens.contains(&w);
    if has("albedo")
        || has("basecolor")
        || (has("base") && has("color"))
        || has("diffuse")
        || has("col")
    {
        return Some("albedo");
    }
    if has("orm") || has("arm") || stem_lower.contains("occlusionroughnessmetallic") {
        return Some("orm");
    }
    if has("roughness") || has("rough") {
        return Some("roughness");
    }
    if has("gloss") || has("glossiness") {
        return Some("gloss");
    }
    if has("metalness") || has("metallic") || has("metal") {
        return Some("metalness");
    }
    if has("specular") || has("spec") {
        return Some("specular");
    }
    if has("normal") || has("nrm") || has("nor") {
        return Some("normal");
    }
    if has("bump") {
        return Some("bump");
    }
    if has("opacity") || has("alpha") || has("mask") {
        return Some("opacity");
    }
    if has("translucency") || has("subsurface") || has("sss") || has("transmission") {
        return Some("translucency");
    }
    if has("emissive") || has("emission") {
        return Some("emissive");
    }
    if has("ao") || has("ambientocclusion") || has("occlusion") {
        return Some("ao");
    }
    if has("cavity") {
        return Some("cavity");
    }
    if has("displacement") || has("disp") || has("height") {
        return Some("displacement");
    }
    None
}

fn extract_res_rank(name_lower: &str) -> (Option<&'static str>, u32) {
    for (tag, label, rank) in [
        ("16k", "16K", 16000),
        ("8k", "8K", 8192),
        ("4k", "4K", 4096),
        ("2k", "2K", 2048),
        ("1k", "1K", 1024),
    ] {
        let tokens: Vec<&str> = name_lower
            .split(|c: char| !c.is_ascii_alphanumeric())
            .collect();
        if tokens.contains(&tag) {
            return (Some(label), rank);
        }
    }
    (None, 2048)
}

fn extract_lod_num(name_lower: &str) -> Option<u32> {
    if name_lower.contains("highpoly") {
        return None;
    }
    let bytes = name_lower.as_bytes();
    for i in 0..bytes.len().saturating_sub(3) {
        if &bytes[i..i + 3] == b"lod" {
            let prev_ok = i == 0 || !bytes[i - 1].is_ascii_alphabetic();
            if !prev_ok {
                continue;
            }
            let mut j = i + 3;
            while j < bytes.len() && bytes[j] == b'_' {
                j += 1;
            }
            let start = j;
            while j < bytes.len() && bytes[j].is_ascii_digit() {
                j += 1;
            }
            if j > start {
                if let Ok(v) = name_lower[start..j].parse::<u32>() {
                    if v <= 15 {
                        return Some(v);
                    }
                }
            }
        }
    }
    None
}

fn is_descriptor_json(path: &Path) -> Option<serde_json::Value> {
    let meta = std::fs::metadata(path).ok()?;
    if meta.len() > 8 * 1024 * 1024 {
        return None;
    }
    let bytes = std::fs::read(path).ok()?;
    let val: serde_json::Value = serde_json::from_slice(&bytes).ok()?;
    let obj = val.as_object()?;
    if obj.contains_key("nodes") && obj.contains_key("edges") {
        return None;
    }
    let is_ms_or_fab = obj
        .get("sublayerAssetKind")
        .and_then(|v| v.as_str())
        .is_some_and(|k| k == "fab" || k == "megascans")
        || obj.contains_key("components")
        || obj.contains_key("categories")
        || obj.contains_key("semanticTags")
        || obj.contains_key("pack")
        || obj.contains_key("listingUid")
        || obj.contains_key("assetId");
    if is_ms_or_fab { Some(val) } else { None }
}

pub fn inspect_asset_dir_or_file(
    target: &Path,
    category_hint: Option<&str>,
) -> Option<MegascansAssetSummary> {
    if target.is_file() {
        let ext = target
            .extension()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        if ext == "json" {
            if let Some(parent) = target.parent() {
                return inspect_asset_folder(parent, Some(target), category_hint);
            }
        }
        if HDRI_EXTS.contains(&ext.as_str()) {
            let stem = target
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("HDRI")
                .to_string();
            let full = target.to_string_lossy().into_owned();
            return Some(MegascansAssetSummary {
                id: sanitize_slug(&stem),
                listing_uid: None,
                name: stem,
                asset_type: "hdri".into(),
                folder_path: target
                    .parent()
                    .map(|p| p.to_string_lossy().into_owned())
                    .unwrap_or_default(),
                primary_path: full,
                preview_image_path: None,
                tags: vec!["hdri".into()],
                available_lods: vec![],
                available_resolutions: vec![],
                mesh_formats: vec![],
                pbr_maps: BTreeMap::new(),
            });
        }
        if MESH_EXTS.contains(&ext.as_str()) {
            // If file sits inside a Megascans/Fab folder with a descriptor JSON, prefer full folder inspection
            if let Some(parent) = target.parent() {
                if let Some(summary) = inspect_asset_folder(parent, None, category_hint) {
                    if summary.primary_path.to_ascii_lowercase().ends_with(".json") {
                        return Some(summary);
                    }
                }
            }
            let stem = target
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("Model")
                .to_string();
            let full = target.to_string_lossy().into_owned();
            return Some(MegascansAssetSummary {
                id: sanitize_slug(&stem),
                listing_uid: None,
                name: stem,
                asset_type: category_hint.unwrap_or("3d").into(),
                folder_path: target
                    .parent()
                    .map(|p| p.to_string_lossy().into_owned())
                    .unwrap_or_default(),
                primary_path: full,
                preview_image_path: None,
                tags: vec![],
                available_lods: vec![0],
                available_resolutions: vec![],
                mesh_formats: vec![ext],
                pbr_maps: BTreeMap::new(),
            });
        }
        return None;
    }
    if target.is_dir() {
        return inspect_asset_folder(target, None, category_hint);
    }
    None
}

fn inspect_asset_folder(
    folder: &Path,
    explicit_json: Option<&Path>,
    category_hint: Option<&str>,
) -> Option<MegascansAssetSummary> {
    let mut json_path: Option<PathBuf> = explicit_json.map(Path::to_path_buf);
    let mut json_doc: Option<serde_json::Value> = explicit_json.and_then(|p| is_descriptor_json(p));

    if json_doc.is_none() {
        if let Ok(rd) = std::fs::read_dir(folder) {
            let mut entries: Vec<PathBuf> = rd
                .filter_map(|e| e.ok().map(|x| x.path()))
                .filter(|p| {
                    p.is_file()
                        && p.extension()
                            .and_then(|s| s.to_str())
                            .is_some_and(|x| x.eq_ignore_ascii_case("json"))
                })
                .collect();
            entries.sort();
            // Prefer fab_asset.json first if present so Fab listingUid is retained
            entries.sort_by_key(|p| {
                if p.file_name()
                    .and_then(|s| s.to_str())
                    .is_some_and(|n| n.eq_ignore_ascii_case("fab_asset.json"))
                {
                    0
                } else {
                    1
                }
            });
            for cand in entries {
                if let Some(doc) = is_descriptor_json(&cand) {
                    json_path = Some(cand);
                    json_doc = Some(doc);
                    break;
                }
            }
        }
    }

    let mut lods = HashSet::new();
    let mut resolutions = HashSet::new();
    let mut mesh_exts = HashSet::new();
    let mut best_mesh: Option<(usize, u32, PathBuf)> = None;
    let mut preview_candidates: Vec<(u8, PathBuf)> = Vec::new();
    let mut pbr_scored: HashMap<&'static str, ((u8, u32, u8), PathBuf)> = HashMap::new();
    let mut has_var_dirs = false;

    let mut stack = vec![(folder.to_path_buf(), 0usize, false, false)];
    while let Some((cur, depth, in_thumbs, in_previews)) = stack.pop() {
        if depth > 4 {
            continue;
        }
        let Ok(rd) = std::fs::read_dir(&cur) else {
            continue;
        };
        for entry in rd.flatten() {
            let path = entry.path();
            if is_link(&path) {
                continue;
            }
            let fname = entry.file_name().to_string_lossy().into_owned();
            let fl = fname.to_ascii_lowercase();
            if path.is_dir() {
                if skip_folder(&fl) && fl != "thumbs" && fl != "previews" {
                    continue;
                }
                if depth == 0
                    && fl.starts_with("var")
                    && fl[3..].chars().all(|c| c.is_ascii_digit())
                    && fl.len() > 3
                {
                    has_var_dirs = true;
                }
                let next_thumbs = in_thumbs || fl == "thumbs";
                let next_previews = in_previews || fl == "previews";
                stack.push((path, depth + 1, next_thumbs, next_previews));
                continue;
            }
            let ext = path
                .extension()
                .and_then(|s| s.to_str())
                .unwrap_or("")
                .to_ascii_lowercase();
            if !in_thumbs && !in_previews && MESH_EXTS.contains(&ext.as_str()) {
                mesh_exts.insert(ext.clone());
                let lod = extract_lod_num(&fl).unwrap_or(0);
                lods.insert(lod);
                let prio = mesh_ext_priority(&ext);
                let replace = match &best_mesh {
                    None => true,
                    Some((bp, blod, _)) => (prio, lod) < (*bp, *blod),
                };
                if replace {
                    best_mesh = Some((prio, lod, path.clone()));
                }
            }
            if IMG_EXTS.contains(&ext.as_str()) {
                let stem_l = path
                    .file_stem()
                    .and_then(|s| s.to_str())
                    .unwrap_or("")
                    .to_ascii_lowercase();
                if stem_l.ends_with("_preview") || stem_l.contains("_thumb_") {
                    let rank = if stem_l.contains("_thumb") && (ext == "jpg" || ext == "jpeg") {
                        0
                    } else if stem_l.ends_with("_sp") {
                        1
                    } else if stem_l.ends_with("_preview") {
                        2
                    } else {
                        3
                    };
                    preview_candidates.push((rank, path.clone()));
                    continue;
                }
                if in_previews || fl.contains("billboard") {
                    continue;
                }
                let (res_label, res_rank) = extract_res_rank(&fl);
                if !in_thumbs {
                    if let Some(lbl) = res_label {
                        resolutions.insert((res_rank, lbl.to_string()));
                    }
                }
                if let Some(ch) = classify_pbr_channel(&stem_l) {
                    let ext_pref = if ext == "png" || ext == "jpg" || ext == "jpeg" {
                        2u8
                    } else {
                        1u8
                    };
                    let score = (if in_thumbs { 0u8 } else { 1u8 }, res_rank, ext_pref);
                    let better = match pbr_scored.get(ch) {
                        None => true,
                        Some((prev_s, _)) => score > *prev_s,
                    };
                    if better {
                        pbr_scored.insert(ch, (score, path));
                    }
                }
            }
        }
    }

    if best_mesh.is_none() && pbr_scored.is_empty() {
        return None;
    }

    preview_candidates.sort_by_key(|(r, _)| *r);
    let preview_image_path = preview_candidates
        .first()
        .map(|(_, p)| p.to_string_lossy().into_owned());

    let folder_name = folder
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("Asset")
        .to_string();

    let mut id = json_path
        .as_ref()
        .and_then(|p| p.file_stem().and_then(|s| s.to_str()))
        .filter(|s| *s != "fab_asset" && *s != "metadata")
        .map(str::to_string)
        .unwrap_or_else(|| {
            folder_name
                .rsplit('_')
                .next()
                .unwrap_or(&folder_name)
                .to_string()
        });
    let mut listing_uid = None;
    let mut name = humanize_folder_name(&folder_name);
    let mut tags = Vec::new();
    let mut doc_cats = Vec::new();

    if let Some(doc) = &json_doc {
        if let Some(n) = doc.get("name").and_then(|v| v.as_str()) {
            if !n.trim().is_empty() {
                name = n.trim().to_string();
            }
        } else if let Some(n) = doc
            .get("semanticTags")
            .and_then(|v| v.get("name"))
            .and_then(|v| v.as_str())
        {
            if !n.trim().is_empty() {
                name = n.trim().to_string();
            }
        }
        if let Some(uid) = doc.get("listingUid").and_then(|v| v.as_str()) {
            if !uid.trim().is_empty() {
                listing_uid = Some(uid.trim().to_string());
                if id == folder_name {
                    id = uid.trim().to_string();
                }
            }
        }
        if let Some(aid) = doc.get("id").and_then(|v| v.as_str()) {
            if !aid.trim().is_empty() {
                id = aid.trim().to_string();
            }
        }
        if let Some(arr) = doc.get("tags").and_then(|v| v.as_array()) {
            for t in arr.iter().filter_map(|x| x.as_str()) {
                if tags.len() < 16 {
                    tags.push(t.to_string());
                }
            }
        }
        if let Some(categories) = doc.get("assetCategories").and_then(|v| v.as_object()) {
            doc_cats.extend(categories.keys().map(|key| key.to_ascii_lowercase()));
        }
        if let Some(arr) = doc.get("categories").and_then(|v| v.as_array()) {
            for c in arr.iter().filter_map(|x| x.as_str()) {
                doc_cats.push(c.to_ascii_lowercase());
            }
        }
    }

    let asset_type = if let Some(hint) = category_hint {
        if best_mesh.is_none() && (hint == "3d" || hint == "fab") {
            "surface".to_string()
        } else {
            hint.to_string()
        }
    } else if has_var_dirs || doc_cats.iter().any(|c| c == "3dplant") {
        "3dplant".to_string()
    } else if best_mesh.is_none() {
        if doc_cats.iter().any(|c| c == "atlas") {
            "atlas".to_string()
        } else {
            "surface".to_string()
        }
    } else {
        "3d".to_string()
    };

    // Primary path: for Megascans/Fab folders with a JSON descriptor AND meshes, link the .json
    // so _open_source_stage composes variations, LODs, units, and PBR materials automatically!
    // If there is only a single standalone .glb/.usdz with no extra folder PBR maps, link the mesh directly.
    let primary_path = if best_mesh.is_some() {
        if let Some(jp) = &json_path {
            jp.to_string_lossy().into_owned()
        } else if let Some((_, _, mp)) = &best_mesh {
            if !pbr_scored.is_empty()
                && !matches!(
                    mp.extension()
                        .and_then(|s| s.to_str())
                        .unwrap_or("")
                        .to_ascii_lowercase()
                        .as_str(),
                    "glb" | "gltf" | "usd" | "usda" | "usdc" | "usdz"
                )
            {
                // Synthesize lightweight fab_asset.json only inside central library folders? Never mutate random folders!
                mp.to_string_lossy().into_owned()
            } else {
                mp.to_string_lossy().into_owned()
            }
        } else {
            folder.to_string_lossy().into_owned()
        }
    } else if let Some(jp) = &json_path {
        jp.to_string_lossy().into_owned()
    } else {
        folder.to_string_lossy().into_owned()
    };

    let mut available_lods: Vec<u32> = lods.into_iter().collect();
    available_lods.sort_unstable();
    let mut res_vec: Vec<(u32, String)> = resolutions.into_iter().collect();
    res_vec.sort_by(|a, b| b.0.cmp(&a.0));
    let available_resolutions = res_vec.into_iter().map(|(_, s)| s).collect();
    let mut mesh_formats: Vec<String> = mesh_exts.into_iter().collect();
    mesh_formats.sort_by_key(|e| mesh_ext_priority(e));

    let pbr_maps = pbr_scored
        .into_iter()
        .map(|(k, (_, p))| (k.to_string(), p.to_string_lossy().into_owned()))
        .collect();

    Some(MegascansAssetSummary {
        id,
        listing_uid,
        name,
        asset_type,
        folder_path: folder.to_string_lossy().into_owned(),
        primary_path,
        preview_image_path,
        tags,
        available_lods,
        available_resolutions,
        mesh_formats,
        pbr_maps,
    })
}

fn humanize_folder_name(raw: &str) -> String {
    let parts: Vec<&str> = raw.split('_').filter(|s| !s.is_empty()).collect();
    let trimmed = if parts.len() >= 2
        && parts
            .last()
            .is_some_and(|last| last.len() >= 5 && last.chars().all(|c| c.is_ascii_alphanumeric()))
    {
        &parts[..parts.len() - 1]
    } else {
        &parts[..]
    };
    if trimmed.is_empty() {
        return raw.to_string();
    }
    trimmed
        .iter()
        .map(|w| {
            let mut chars = w.chars();
            match chars.next() {
                None => String::new(),
                Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn is_link(path: &Path) -> bool {
    let Ok(meta) = std::fs::symlink_metadata(path) else {
        return true;
    };
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if meta.file_attributes() & 0x400 != 0 {
            return true;
        }
    }
    meta.file_type().is_symlink()
}
fn skip_folder(name: &str) -> bool {
    matches!(
        name,
        "uassets"
            | "thumbs"
            | "previews"
            | "support"
            | "temp"
            | "brush"
            | "dhi"
            | "_incoming"
            | ".git"
            | "__macosx"
    )
}

pub fn scan_library(library_root: &Path) -> Result<Vec<MegascansAssetSummary>, String> {
    if !library_root.is_dir() {
        return Ok(vec![]);
    }
    let mut results = Vec::new();
    let mut visited = HashSet::new();
    let mut stack = vec![(library_root.to_path_buf(), 0, None)];
    let mut count = 0;
    while let Some((folder, depth, hint)) = stack.pop() {
        if !visited.insert(folder.to_string_lossy().to_ascii_lowercase()) {
            continue;
        }
        if depth > 6 || count >= 100_000 {
            continue;
        }
        let Ok(entries) = std::fs::read_dir(&folder) else {
            continue;
        };
        let mut children = Vec::new();
        let mut asset = false;
        for entry in entries.flatten() {
            count += 1;
            if count > 100_000 {
                break;
            }
            let path = entry.path();
            if is_link(&path) {
                continue;
            }
            let name = entry.file_name().to_string_lossy().to_ascii_lowercase();
            if skip_folder(&name) {
                continue;
            }
            if path.is_dir() {
                if name.starts_with("var")
                    && name.len() > 3
                    && name[3..].chars().all(|c| c.is_ascii_digit())
                {
                    asset = true;
                }
                children.push(path);
                continue;
            }
            let ext = path
                .extension()
                .and_then(|v| v.to_str())
                .unwrap_or("")
                .to_ascii_lowercase();
            if ext == "zip" {
                let dest = path.with_extension("");
                if !dest.exists() {
                    extract_zip_safely(&path, &dest)?;
                }
                if dest.is_dir() {
                    children.push(dest);
                }
            }
            asset |= MESH_EXTS.contains(&ext.as_str())
                || (ext == "json" && is_descriptor_json(&path).is_some())
                || (IMG_EXTS.contains(&ext.as_str()) && classify_pbr_channel(&name).is_some());
        }
        if asset {
            if let Some(summary) = inspect_asset_folder(&folder, None, hint) {
                results.push(summary);
            }
            continue;
        }
        for child in children {
            let name = child
                .file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .to_ascii_lowercase();
            let next_hint = match name.as_str() {
                "3d" => Some("3d"),
                "3dplant" => Some("3dplant"),
                "surface" => Some("surface"),
                "atlas" => Some("atlas"),
                "fab" => Some("fab"),
                _ => hint,
            };
            stack.push((child, depth + 1, next_hint));
        }
    }

    results.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
    Ok(results)
}

pub fn read_thumbnail_data_uri(path: &Path) -> Result<String, String> {
    if !path.is_file() {
        return Err("Preview image does not exist".into());
    }
    let meta = std::fs::metadata(path).map_err(|e| e.to_string())?;
    if meta.len() > 12 * 1024 * 1024 {
        return Err("Preview image exceeds 12 MB limit".into());
    }
    let ext = path
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    let mime = match ext.as_str() {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "webp" => "image/webp",
        _ => return Err("Unsupported thumbnail image format".into()),
    };
    let bytes = std::fs::read(path).map_err(|e| e.to_string())?;
    Ok(format!("data:{mime};base64,{}", encode_base64(&bytes)))
}

fn encode_base64(input: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(input.len().div_ceil(3) * 4);
    for chunk in input.chunks(3) {
        let b0 = chunk[0] as u32;
        let b1 = if chunk.len() > 1 { chunk[1] as u32 } else { 0 };
        let b2 = if chunk.len() > 2 { chunk[2] as u32 } else { 0 };
        let n = (b0 << 16) | (b1 << 8) | b2;
        out.push(TABLE[((n >> 18) & 63) as usize] as char);
        out.push(TABLE[((n >> 12) & 63) as usize] as char);
        if chunk.len() > 1 {
            out.push(TABLE[((n >> 6) & 63) as usize] as char);
        } else {
            out.push('=');
        }
        if chunk.len() > 2 {
            out.push(TABLE[(n & 63) as usize] as char);
        } else {
            out.push('=');
        }
    }
    out
}

pub fn sanitize_slug(raw: &str) -> String {
    let mut out = String::new();
    let mut prev_underscore = false;
    for ch in raw.chars() {
        if ch.is_ascii_alphanumeric() {
            out.push(ch);
            prev_underscore = false;
        } else if !prev_underscore && !out.is_empty() {
            out.push('_');
            prev_underscore = true;
        }
        if out.len() >= 48 {
            break;
        }
    }
    let trimmed = out.trim_matches('_').to_string();
    if trimmed.is_empty() {
        "Fab_Asset".into()
    } else {
        trimmed
    }
}

pub fn parse_fab_or_asset_url(
    url: &str,
    title_hint: &str,
) -> (Option<String>, Option<String>, String) {
    let trimmed = url.trim();
    let no_query = trimmed.split(&['?', '#'][..]).next().unwrap_or(trimmed);
    let lower = no_query.to_ascii_lowercase();

    // Check fab.com/listings/<uid>
    let mut listing_uid = None;
    if let Some(idx) = lower.find("/listings/") {
        let rest = &no_query[idx + "/listings/".len()..];
        let seg = rest.split('/').next().unwrap_or("").trim();
        if !seg.is_empty() {
            listing_uid = Some(seg.to_string());
        }
    }
    // Check quixel.com assetId=<id>
    if listing_uid.is_none() {
        if let Some(q_idx) = trimmed.find("assetId=") {
            let rest = &trimmed[q_idx + "assetId=".len()..];
            let seg = rest.split('&').next().unwrap_or("").trim();
            if !seg.is_empty() {
                listing_uid = Some(seg.to_string());
            }
        }
    }

    // Check direct downloadable file extension
    let direct_ext = Path::new(no_query)
        .extension()
        .and_then(|s| s.to_str())
        .map(|s| s.to_ascii_lowercase())
        .filter(|ext| {
            MESH_EXTS.contains(&ext.as_str()) || HDRI_EXTS.contains(&ext.as_str()) || ext == "zip"
        });

    let derived_title = if !title_hint.trim().is_empty() && !title_hint.trim().starts_with("http") {
        title_hint.trim().to_string()
    } else if let Some(uid) = &listing_uid {
        format!("Fab_{}", &uid[..uid.len().min(12)])
    } else {
        Path::new(no_query)
            .file_stem()
            .and_then(|s| s.to_str())
            .filter(|s| !s.is_empty())
            .unwrap_or("Fab Asset")
            .replace(['-', '_'], " ")
    };

    (listing_uid, direct_ext, derived_title)
}

pub fn match_existing_library_asset(
    library_root: &Path,
    listing_uid: Option<&str>,
    title: &str,
) -> Option<MegascansAssetSummary> {
    let assets = scan_library(library_root).ok()?;
    if let Some(uid) = listing_uid {
        let uid_l = uid.to_ascii_lowercase();
        if let Some(hit) = assets.iter().find(|a| {
            a.listing_uid
                .as_deref()
                .is_some_and(|u| u.eq_ignore_ascii_case(uid))
                || a.id.eq_ignore_ascii_case(uid)
                || a.folder_path
                    .to_ascii_lowercase()
                    .ends_with(&format!("_{uid_l}"))
        }) {
            return Some(hit.clone());
        }
        // Also check short Megascans suffix inside Fab slug (e.g. "...-sboft" or exact id)
        if let Some(last_tok) = uid_l.rsplit('-').next() {
            if last_tok.len() >= 5 {
                if let Some(hit) = assets.iter().find(|a| a.id.eq_ignore_ascii_case(last_tok)) {
                    return Some(hit.clone());
                }
            }
        }
    }
    let norm_title = sanitize_slug(title).to_ascii_lowercase();
    if norm_title.len() >= 4 && norm_title != "fab_asset" {
        let exact_matches: Vec<&MegascansAssetSummary> = assets
            .iter()
            .filter(|a| sanitize_slug(&a.name).to_ascii_lowercase() == norm_title)
            .collect();
        if exact_matches.len() == 1 {
            return Some(exact_matches[0].clone());
        }
    }
    None
}

fn extract_zip_safely(zip_path: &Path, dest_dir: &Path) -> Result<(), String> {
    if dest_dir.exists() {
        return Err("Archive destination already exists".into());
    }
    let parent = dest_dir
        .parent()
        .ok_or("Archive destination requires a parent")?;
    let staging = tempfile::tempdir_in(parent).map_err(|e| e.to_string())?;
    let destination = dest_dir;
    let dest_dir = staging.path();
    let python =
        crate::runtime_setup::find_imaging_python().unwrap_or_else(|| PathBuf::from("python"));
    let script = concat!(
        "import os, shutil, sys, zipfile\n",
        "zp, dst = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])\n",
        "os.makedirs(dst, exist_ok=True)\n",
        "with zipfile.ZipFile(zp, 'r') as zf:\n",
        "    if len(zf.infolist()) > 100000 or sum(i.file_size for i in zf.infolist()) > 100 * 1024**3: raise RuntimeError('Archive exceeds extraction limits')\n",
        "    for info in zf.infolist():\n",
        "        name = info.filename.replace('\\\\', '/')\n",
        "        if not name or name.startswith('/') or '__MACOSX' in name.split('/'):\n",
        "            continue\n",
        "        if (info.external_attr >> 16) & 0o170000 == 0o120000: raise RuntimeError('Archive symlinks are unsupported')\n",
        "        if ':' in name or '..' in name.split('/'): raise RuntimeError('Unsafe archive path')\n",
        "        out_p = os.path.abspath(os.path.join(dst, name))\n",
        "        if os.path.commonpath([dst, out_p]) != dst:\n",
        "            raise RuntimeError(f'Unsafe archive entry rejected: {name}')\n",
        "        if info.is_dir() or name.endswith('/'):\n",
        "            os.makedirs(out_p, exist_ok=True)\n",
        "        else:\n",
        "            os.makedirs(os.path.dirname(out_p), exist_ok=True)\n",
        "            with zf.open(info, 'r') as src, open(out_p, 'xb') as dst_f:\n",
        "                shutil.copyfileobj(src, dst_f, 1024 * 1024)\n",
    );
    let mut cmd = Command::new(python);
    cmd.arg("-c").arg(script).arg(zip_path).arg(dest_dir);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000);
    }
    let out = cmd
        .output()
        .map_err(|e| format!("Failed starting archive extractor: {e}"))?;
    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr);
        return Err(format!("Failed extracting Fab archive: {}", stderr.trim()));
    }

    // Flatten a single redundant top-level folder inside dest_dir if present
    if let Ok(rd) = std::fs::read_dir(dest_dir) {
        let entries: Vec<PathBuf> = rd.filter_map(|e| e.ok().map(|x| x.path())).collect();
        if entries.len() == 1 && entries[0].is_dir() {
            let inner = &entries[0];
            if let Ok(inner_rd) = std::fs::read_dir(inner) {
                let inner_items: Vec<PathBuf> =
                    inner_rd.filter_map(|e| e.ok().map(|x| x.path())).collect();
                let can_hoist = inner_items.iter().all(|p| {
                    p.file_name()
                        .map(|n| !dest_dir.join(n).exists())
                        .unwrap_or(false)
                });
                if can_hoist {
                    for item in inner_items {
                        if let Some(name) = item.file_name() {
                            let _ = std::fs::rename(&item, dest_dir.join(name));
                        }
                    }
                    let _ = std::fs::remove_dir(inner);
                }
            }
        }
    }
    std::fs::rename(dest_dir, destination).map_err(|e| e.to_string())?;
    Ok(())
}

pub fn library_subdirectory(root: &Path, relative: &str) -> Result<PathBuf, String> {
    if !root.is_absolute() || !root.is_dir() {
        return Err("Set your library folder in Applications".into());
    }
    let mut path = root.to_path_buf();
    for component in Path::new(relative).components() {
        let std::path::Component::Normal(name) = component else {
            return Err("Invalid library subdirectory".into());
        };
        path.push(name);
        if path.exists() {
            if is_link(&path) || !path.is_dir() {
                return Err("Library subdirectories must be ordinary directories".into());
            }
        } else {
            std::fs::create_dir(&path).map_err(|e| e.to_string())?;
        }
    }
    Ok(path)
}

pub fn ingest_downloaded_file_into_central_library(
    source_file: &Path,
    library_root: &Path,
    listing_uid: Option<&str>,
    url: Option<&str>,
    title_hint: &str,
) -> Result<MegascansAssetSummary, String> {
    if !source_file.is_file() {
        return Err(format!(
            "Downloaded file not found: {}",
            source_file.display()
        ));
    }
    if !library_root.is_dir() {
        return Err(format!(
            "Central Megascans/Fab folder is not configured or missing: {}",
            library_root.display()
        ));
    }

    let stem = source_file
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("Fab_Asset");
    let effective_title = if !title_hint.trim().is_empty() && title_hint != "Fab Asset" {
        title_hint.trim()
    } else {
        stem
    };
    let slug = sanitize_slug(effective_title);
    let uid_suffix = listing_uid
        .map(|u| {
            let clean: String = u.chars().filter(|c| c.is_ascii_alphanumeric()).collect();
            clean[..clean.len().min(10)].to_string()
        })
        .unwrap_or_else(|| {
            let mut h: u32 = 2166136261;
            for b in source_file.to_string_lossy().bytes() {
                h ^= b as u32;
                h = h.wrapping_mul(16777619);
            }
            format!("{h:08x}")
        });

    let mut target_dir = library_root
        .join("Downloaded")
        .join("Fab")
        .join(format!("{slug}_{uid_suffix}"));
    if target_dir.exists() {
        target_dir =
            target_dir.with_file_name(format!("{slug}_{uid_suffix}_{}", uuid::Uuid::new_v4()));
    }
    library_subdirectory(library_root, "Downloaded/Fab")?;

    let ext = source_file
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();

    if ext == "zip" {
        extract_zip_safely(source_file, &target_dir)?;
    } else {
        std::fs::create_dir(&target_dir).map_err(|e| e.to_string())?;
        let fname = source_file.file_name().ok_or("Invalid source filename")?;
        let dest_file = target_dir.join(fname);
        if dest_file != source_file {
            std::fs::copy(source_file, &dest_file).map_err(|e| e.to_string())?;
        }
    }

    // Write fab_asset.json inside the central folder so future drags of the same listing
    // resolve immediately and _open_source_stage composes meshes + textures cleanly.
    let manifest_path = target_dir.join("fab_asset.json");
    let manifest = serde_json::json!({
        "sublayerAssetKind": "fab",
        "id": listing_uid.unwrap_or(&uid_suffix),
        "listingUid": listing_uid,
        "url": url,
        "name": effective_title,
        "categories": ["fab", "3d"],
    });
    if !std::fs::read_dir(&target_dir)
        .map_err(|e| e.to_string())?
        .flatten()
        .any(|entry| is_descriptor_json(&entry.path()).is_some())
    {
        std::fs::write(
            &manifest_path,
            serde_json::to_vec_pretty(&manifest).map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())?;
    }

    inspect_asset_dir_or_file(&target_dir, Some("fab")).ok_or_else(|| {
        format!(
            "Archive extracted to {}, but contained no supported 3D mesh or PBR textures.",
            target_dir.display()
        )
    })
}

pub fn download_direct_asset_url(
    url: &str,
    ext: &str,
    library_root: &Path,
    title_hint: &str,
) -> Result<MegascansAssetSummary, String> {
    let tmp_dir = std::env::temp_dir().join("sublayer_fab_dl");
    std::fs::create_dir_all(&tmp_dir).map_err(|e| e.to_string())?;
    let slug = sanitize_slug(title_hint);
    let tmp_file = tmp_dir.join(format!("{slug}.{ext}"));

    let mut cmd = Command::new("curl.exe");
    cmd.args([
        "-L",
        "--fail",
        "--silent",
        "--show-error",
        "--max-time",
        "180",
        "-o",
    ])
    .arg(&tmp_file)
    .arg(url);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000);
    }
    let out = cmd
        .output()
        .map_err(|e| format!("Failed invoking curl.exe: {e}"))?;
    if !out.status.success() || !tmp_file.is_file() {
        let err = String::from_utf8_lossy(&out.stderr);
        return Err(format!("Direct asset download failed: {}", err.trim()));
    }
    let res = ingest_downloaded_file_into_central_library(
        &tmp_file,
        library_root,
        None,
        Some(url),
        title_hint,
    );
    let _ = std::fs::remove_file(&tmp_file);
    res
}

// ---------------------------------------------------------------------------
// Active Downloads Folder Watcher for Literal `fab.com` Webpage Drops
// ---------------------------------------------------------------------------

#[derive(Default)]
pub struct FabWatchRegistry {
    active: Mutex<HashMap<String, Arc<AtomicBool>>>,
}

impl FabWatchRegistry {
    pub fn has_pending(&self) -> bool {
        self.active
            .lock()
            .map(|m| m.values().any(|flag| !flag.load(Ordering::Relaxed)))
            .unwrap_or(true)
    }
    pub fn cancel(&self, drop_id: &str) {
        if let Ok(mut map) = self.active.lock() {
            if let Some(flag) = map.remove(drop_id) {
                flag.store(true, Ordering::Relaxed);
            }
        }
    }

    pub fn start_watch<F>(
        &self,
        drop_id: String,
        downloads_dirs: Vec<PathBuf>,
        library_root: PathBuf,
        listing_uid: Option<String>,
        url: String,
        title: String,
        on_done: F,
    ) where
        F: FnOnce(Result<MegascansAssetSummary, String>) + Send + 'static,
    {
        self.cancel(&drop_id);
        let cancel_flag = Arc::new(AtomicBool::new(false));
        if let Ok(mut map) = self.active.lock() {
            map.insert(drop_id.clone(), cancel_flag.clone());
        }

        let mut baseline: HashMap<_, _> = downloads_dirs
            .iter()
            .flat_map(|dir| snapshot_downloads(dir))
            .collect();
        let started_at = SystemTime::now();

        std::thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(600);
            while Instant::now() < deadline {
                if cancel_flag.load(Ordering::Relaxed) {
                    return;
                }
                std::thread::sleep(Duration::from_millis(750));
                if cancel_flag.load(Ordering::Relaxed) {
                    return;
                }
                if let Some(candidate) = downloads_dirs
                    .iter()
                    .find_map(|dir| find_completed_download(dir, &baseline, started_at))
                {
                    let candidate_name =
                        sanitize_slug(candidate.file_stem().and_then(|s| s.to_str()).unwrap_or(""));
                    let candidate_name = candidate_name.to_ascii_lowercase();
                    let title_slug = sanitize_slug(&title).to_ascii_lowercase();
                    let uid_matches = listing_uid.as_ref().is_some_and(|uid| {
                        candidate_name.contains(&sanitize_slug(uid).to_ascii_lowercase())
                    });
                    if !uid_matches
                        && (title_slug.is_empty()
                            || title_slug == "fab_asset"
                            || !candidate_name.contains(&title_slug))
                    {
                        if let Ok(meta) = std::fs::metadata(&candidate) {
                            baseline.insert(candidate.clone(), (meta.len(), meta.modified().ok()));
                        }
                        continue;
                    }
                    // Wait 500 ms and re-verify file size stability and absence of lock/temp sibling
                    let Ok(m1) = std::fs::metadata(&candidate) else {
                        continue;
                    };
                    std::thread::sleep(Duration::from_millis(550));
                    let Ok(m2) = std::fs::metadata(&candidate) else {
                        continue;
                    };
                    if m1.len() == 0 || m1.len() != m2.len() || has_partial_sibling(&candidate) {
                        continue;
                    }
                    if std::fs::File::open(&candidate).is_err() {
                        continue;
                    }
                    if !claim_download(&candidate) {
                        continue;
                    }
                    let outcome = ingest_downloaded_file_into_central_library(
                        &candidate,
                        &library_root,
                        listing_uid.as_deref(),
                        Some(&url),
                        &title,
                    );
                    cancel_flag.store(true, Ordering::Relaxed);
                    on_done(outcome);
                    return;
                }
            }
            if !cancel_flag.load(Ordering::Relaxed) {
                cancel_flag.store(true, Ordering::Relaxed);
                on_done(Err(format!(
                    "Timed out waiting for \"{title}\" to finish downloading in {}",
                    downloads_dirs
                        .iter()
                        .map(|p| p.display().to_string())
                        .collect::<Vec<_>>()
                        .join(", ")
                )));
            }
        });
    }
}

fn claim_download(path: &Path) -> bool {
    static CLAIMED: std::sync::OnceLock<Mutex<HashSet<PathBuf>>> = std::sync::OnceLock::new();
    CLAIMED
        .get_or_init(|| Mutex::new(HashSet::new()))
        .lock()
        .map(|mut paths| paths.insert(path.to_path_buf()))
        .unwrap_or(false)
}

pub fn watch_browser_session<F, G, H>(
    config: MegascansConfig,
    active: F,
    can_ingest: H,
    on_asset: G,
) where
    F: Fn() -> bool + Send + 'static,
    H: Fn() -> bool + Send + 'static,
    G: Fn(Result<MegascansAssetSummary, String>) + Send + 'static,
{
    let mut dirs: Vec<_> = config
        .downloads_dirs
        .iter()
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .collect();
    dirs.push(PathBuf::from(&config.library_root));
    let mut baseline: HashMap<_, _> = dirs
        .iter()
        .flat_map(|dir| snapshot_downloads(dir))
        .collect();
    std::thread::spawn(move || {
        while active() {
            std::thread::sleep(Duration::from_millis(750));
            if !can_ingest() {
                continue;
            }
            for dir in &dirs {
                if let Some(path) = find_completed_download(dir, &baseline, SystemTime::now()) {
                    let Ok(before) = std::fs::metadata(&path) else {
                        continue;
                    };
                    std::thread::sleep(Duration::from_millis(550));
                    let Ok(after) = std::fs::metadata(&path) else {
                        continue;
                    };
                    if !active() || before.len() != after.len() || has_partial_sibling(&path) {
                        continue;
                    }
                    baseline.insert(path.clone(), (after.len(), after.modified().ok()));
                    if !claim_download(&path) {
                        continue;
                    }
                    let title = path
                        .file_stem()
                        .and_then(|s| s.to_str())
                        .unwrap_or("Fab Asset");
                    on_asset(ingest_downloaded_file_into_central_library(
                        &path,
                        Path::new(&config.library_root),
                        None,
                        None,
                        title,
                    ));
                }
            }
        }
    });
}

fn snapshot_downloads(dir: &Path) -> HashMap<PathBuf, (u64, Option<SystemTime>)> {
    let mut map = HashMap::new();
    if let Ok(rd) = std::fs::read_dir(dir) {
        for entry in rd.flatten() {
            let p = entry.path();
            if let Ok(meta) = entry.metadata() {
                if meta.is_file() {
                    map.insert(p, (meta.len(), meta.modified().ok()));
                }
            }
        }
    }
    map
}

fn has_partial_sibling(path: &Path) -> bool {
    let base = path.to_string_lossy();
    for suffix in [".crdownload", ".part", ".tmp", ".download"] {
        if Path::new(&format!("{base}{suffix}")).exists() {
            return true;
        }
    }
    false
}

fn find_completed_download(
    dir: &Path,
    baseline: &HashMap<PathBuf, (u64, Option<SystemTime>)>,
    started_at: SystemTime,
) -> Option<PathBuf> {
    let rd = std::fs::read_dir(dir).ok()?;
    let mut best: Option<(SystemTime, PathBuf)> = None;

    for entry in rd.flatten() {
        let p = entry.path();
        let Ok(meta) = entry.metadata() else {
            continue;
        };
        if !meta.is_file() || meta.len() == 0 {
            continue;
        }
        let ext = p
            .extension()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        if matches!(ext.as_str(), "crdownload" | "part" | "tmp" | "download") {
            continue;
        }
        let supported =
            ext == "zip" || MESH_EXTS.contains(&ext.as_str()) || HDRI_EXTS.contains(&ext.as_str());
        if !supported || has_partial_sibling(&p) {
            continue;
        }
        let mtime = meta.modified().unwrap_or(started_at);
        let is_new_or_changed = match baseline.get(&p) {
            None => true,
            Some((old_len, old_mtime)) => {
                *old_len != meta.len() || old_mtime.is_none_or(|t| mtime > t)
            }
        };
        if !is_new_or_changed {
            continue;
        }
        if best.as_ref().is_none_or(|(bt, _)| mtime > *bt) {
            best = Some((mtime, p));
        }
    }
    best.map(|(_, p)| p)
}

pub fn resolve_dropped_local_file(
    file_name: &str,
    file_size: u64,
    cfg: &MegascansConfig,
    project_dir: Option<&str>,
) -> Result<MegascansAssetSummary, String> {
    if file_name.trim().is_empty() || file_name.contains(['/', '\\', '\0']) {
        return Err("Invalid dropped filename".into());
    }
    let lib_root = PathBuf::from(&cfg.library_root);

    // 1. Check standard top-level directories first (Downloads, Desktop, Project Dir)
    let mut quick_dirs = Vec::new();
    for dir in &cfg.downloads_dirs {
        if !dir.is_empty() {
            quick_dirs.push((PathBuf::from(dir), true));
        }
    }
    if let Some(up) = std::env::var_os("USERPROFILE") {
        quick_dirs.push((PathBuf::from(up).join("Desktop"), false));
    }
    if let Some(pd) = project_dir {
        if !pd.trim().is_empty() {
            quick_dirs.push((PathBuf::from(pd), false));
        }
    }

    for (dir, is_downloads) in quick_dirs {
        let cand = dir.join(file_name);
        if let Ok(meta) = std::fs::metadata(&cand) {
            if meta.is_file() && meta.len() == file_size {
                let ext = cand
                    .extension()
                    .and_then(|s| s.to_str())
                    .unwrap_or("")
                    .to_ascii_lowercase();
                if (is_downloads || ext == "zip") && lib_root.is_dir() {
                    let stem = cand.file_stem().and_then(|s| s.to_str()).unwrap_or("Asset");
                    return ingest_downloaded_file_into_central_library(
                        &cand, &lib_root, None, None, stem,
                    );
                }
                return inspect_asset_dir_or_file(&cand, None)
                    .ok_or_else(|| format!("Unsupported asset file: {}", cand.display()));
            }
        }
    }

    // 2. Search inside the central Megascans/Fab library (in-place link, zero copies)
    if lib_root.is_dir() {
        if let Some(found) = find_file_in_tree(&lib_root, file_name, file_size, 5) {
            return inspect_asset_dir_or_file(&found, None)
                .ok_or_else(|| format!("Unsupported asset file: {}", found.display()));
        }
    }

    Err(format!(
        "Could not resolve full path for dropped file \"{file_name}\". Place it in {} or your Downloads folder, or use Megascans / Fab Browser.",
        if cfg.library_root.is_empty() {
            "your central Megascans folder"
        } else {
            &cfg.library_root
        }
    ))
}

fn find_file_in_tree(
    root: &Path,
    wanted_name: &str,
    wanted_size: u64,
    max_depth: usize,
) -> Option<PathBuf> {
    let mut stack = vec![(root.to_path_buf(), 0usize)];
    let mut visited = 0usize;
    while let Some((dir, depth)) = stack.pop() {
        if depth > max_depth || visited > 50_000 {
            continue;
        }
        let Ok(rd) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in rd.flatten() {
            visited += 1;
            let p = entry.path();
            let name = entry.file_name();
            if p.is_dir() {
                let nl = name.to_string_lossy().to_ascii_lowercase();
                if nl != "uassets" && nl != "thumbs" {
                    stack.push((p, depth + 1));
                }
            } else if name.to_string_lossy().eq_ignore_ascii_case(wanted_name) {
                if let Ok(m) = entry.metadata() {
                    if m.len() == wanted_size {
                        return Some(p);
                    }
                }
            }
        }
    }
    None
}

// ---------------------------------------------------------------------------
// Optional Quixel Bridge / Fab Launcher Custom Socket Listener (127.0.0.1:port)
// ---------------------------------------------------------------------------

pub struct MegascansSocketServer {
    stop: Arc<AtomicBool>,
    port: u16,
}

impl MegascansSocketServer {
    pub fn start<F>(port: u16, callback: F) -> Result<Self, String>
    where
        F: Fn(Vec<MegascansAssetSummary>) + Send + Sync + 'static,
    {
        let listener = TcpListener::bind(("127.0.0.1", port))
            .map_err(|e| format!("Cannot bind Megascans/Fab socket on 127.0.0.1:{port}: {e}"))?;
        let _ = listener.set_nonblocking(true);
        let stop = Arc::new(AtomicBool::new(false));
        let stop_thread = stop.clone();
        let cb = Arc::new(callback);

        std::thread::spawn(move || {
            while !stop_thread.load(Ordering::Relaxed) {
                match listener.accept() {
                    Ok((mut stream, _addr)) => {
                        let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
                        let mut buf = Vec::new();
                        if stream.read_to_end(&mut buf).is_ok() && !buf.is_empty() {
                            let assets = parse_socket_payload(&buf);
                            if !assets.is_empty() {
                                cb(assets);
                            }
                        }
                    }
                    Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        std::thread::sleep(Duration::from_millis(200));
                    }
                    Err(_) => {
                        std::thread::sleep(Duration::from_millis(500));
                    }
                }
            }
        });

        Ok(Self { stop, port })
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    pub fn stop(&self) {
        self.stop.store(true, Ordering::Relaxed);
    }
}

impl Drop for MegascansSocketServer {
    fn drop(&mut self) {
        self.stop();
    }
}

fn parse_socket_payload(raw: &[u8]) -> Vec<MegascansAssetSummary> {
    let Ok(val) = serde_json::from_slice::<serde_json::Value>(raw) else {
        return vec![];
    };
    let items: Vec<&serde_json::Value> = match &val {
        serde_json::Value::Array(arr) => arr.iter().collect(),
        obj @ serde_json::Value::Object(_) => vec![obj],
        _ => vec![],
    };
    let mut out = Vec::new();
    for item in items {
        let path_str = item
            .get("path")
            .or_else(|| item.get("folderPath"))
            .or_else(|| item.get("AssetFolderPath"))
            .or_else(|| item.get("sourcePath"))
            .and_then(|v| v.as_str());
        if let Some(ps) = path_str {
            if let Some(summary) = inspect_asset_dir_or_file(Path::new(ps), None) {
                out.push(summary);
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nested_library_zip_and_skipped_directories() {
        let temp = tempfile::tempdir().unwrap();
        let library = temp.path().join("library");
        let nested = library.join("MyAssets/Category/Deep/Stone");
        std::fs::create_dir_all(&nested).unwrap();
        std::fs::write(nested.join("stone.fbx"), b"fixture").unwrap();
        for skipped in [
            "UAssets",
            "Thumbs",
            "previews",
            "support",
            "temp",
            "brush",
            "DHI",
            "_incoming",
            ".git",
        ] {
            let dir = library.join(skipped);
            std::fs::create_dir_all(&dir).unwrap();
            std::fs::write(dir.join("skip.fbx"), b"fixture").unwrap();
        }
        let archive = library.join("loose.zip");
        let python =
            crate::runtime_setup::find_imaging_python().unwrap_or_else(|| PathBuf::from("python"));
        let result = Command::new(python).args(["-c", "import sys,zipfile; z=zipfile.ZipFile(sys.argv[1],'w'); z.writestr('wrapper/prop.glb',b'glTF'); z.close()"]).arg(&archive).status().unwrap();
        assert!(result.success());
        let assets = scan_library(&library).unwrap();
        assert_eq!(assets.len(), 2);
        assert!(archive.is_file());
        assert!(library.join("loose/prop.glb").is_file());
        let before = std::fs::read(library.join("loose/prop.glb")).unwrap();
        assert_eq!(scan_library(&library).unwrap().len(), 2);
        assert_eq!(
            std::fs::read(library.join("loose/prop.glb")).unwrap(),
            before
        );
        assert!(
            assets.iter().any(|a| Path::new(&a.folder_path) == nested),
            "{assets:?}"
        );
    }

    #[test]
    fn unsafe_zip_is_rejected_without_installing_partial_asset() {
        let temp = tempfile::tempdir().unwrap();
        let archive = temp.path().join("bad.zip");
        let python =
            crate::runtime_setup::find_imaging_python().unwrap_or_else(|| PathBuf::from("python"));
        assert!(Command::new(python).args(["-c", "import sys,zipfile; z=zipfile.ZipFile(sys.argv[1],'w'); z.writestr('good.glb',b'glTF'); z.writestr('../escape.txt',b'bad'); z.close()"]).arg(&archive).status().unwrap().success());
        let destination = temp.path().join("asset");
        assert!(extract_zip_safely(&archive, &destination).is_err());
        assert!(!destination.exists());
        assert!(!temp.path().join("escape.txt").exists());
    }

    #[test]
    fn shared_library_config_and_revision_validation() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("applications.json");
        let mut catalog = crate::application_profiles::Catalog::defaults();
        catalog.megascans_library = MegascansConfig {
            library_root: temp.path().to_string_lossy().into_owned(),
            downloads_dirs: vec![temp.path().to_string_lossy().into_owned()],
            socket_enabled: false,
            socket_port: 24981,
        };
        catalog.revision = 2;
        crate::application_profiles::save(&path, &catalog).unwrap();
        assert_eq!(load_config(&path).unwrap(), catalog.megascans_library);
        catalog.megascans_library.socket_port = 24982;
        assert!(crate::application_profiles::save(&path, &catalog).is_err());
        catalog.revision += 1;
        crate::application_profiles::save(&path, &catalog).unwrap();
        catalog.megascans_library.library_root = "relative/path".into();
        assert!(catalog.megascans_library.validate().is_err());
        catalog.megascans_library.library_root.clear();
        catalog.megascans_library.socket_port = 1;
        assert!(catalog.megascans_library.validate().is_err());
    }

    #[test]
    fn test_parse_fab_listing_and_direct_urls() {
        let (uid, ext, title) = parse_fab_or_asset_url(
            "https://www.fab.com/listings/9f8e7d6c-1234-5678-abcd-ef0123456789?utm=1",
            "Mossy Cliff Rock",
        );
        assert_eq!(uid.as_deref(), Some("9f8e7d6c-1234-5678-abcd-ef0123456789"));
        assert_eq!(ext, None);
        assert_eq!(title, "Mossy Cliff Rock");

        let (uid2, ext2, _) =
            parse_fab_or_asset_url("https://cdn.example.com/models/crate_4k.glb?sig=abc", "");
        assert_eq!(uid2, None);
        assert_eq!(ext2.as_deref(), Some("glb"));
    }

    #[test]
    fn test_extract_lod_and_channel_classification() {
        assert_eq!(extract_lod_num("aset_rock_lod0.fbx"), Some(0));
        assert_eq!(extract_lod_num("var3_lod4.abc"), Some(4));
        assert_eq!(extract_lod_num("rock_highpoly.fbx"), None);
        assert_eq!(classify_pbr_channel("ulfucbn_4k_albedo"), Some("albedo"));
        assert_eq!(
            classify_pbr_channel("ulfucbn_4k_normal_lod0"),
            Some("normal")
        );
        assert_eq!(
            classify_pbr_channel("smencbp_4k_translucency"),
            Some("translucency")
        );
        assert_eq!(classify_pbr_channel("ulfucbn_thumb_highpoly"), None);
    }

    #[test]
    fn test_central_library_ingest_and_instant_fab_match() {
        let tmp = tempfile::tempdir().unwrap();
        let central = tmp.path().join("Megascans");
        std::fs::create_dir_all(&central).unwrap();

        let fake_glb = tmp.path().join("ancient_vase.glb");
        std::fs::write(&fake_glb, b"glTF\x02\x00\x00\x00").unwrap();

        let summary = ingest_downloaded_file_into_central_library(
            &fake_glb,
            &central,
            Some("abcd-1234-fab-uid"),
            Some("https://www.fab.com/listings/abcd-1234-fab-uid"),
            "Ancient Vase",
        )
        .unwrap();
        assert_eq!(summary.name, "Ancient Vase");
        assert!(summary.primary_path.ends_with("fab_asset.json"));
        assert!(
            Path::new(&summary.folder_path).starts_with(central.join("Downloaded").join("Fab"))
        );

        // Subsequent webpage drag of the same Fab listing resolves immediately from central folder
        let matched =
            match_existing_library_asset(&central, Some("abcd-1234-fab-uid"), "Ancient Vase")
                .expect("should match ingested Fab asset");
        assert_eq!(matched.primary_path, summary.primary_path);
    }

    #[test]
    fn test_scan_real_megascans_library_if_present() {
        let real = Path::new(r"I:\EpicGames\Megascans");
        if !real.is_dir() {
            return;
        }
        let scanned = scan_library(real).unwrap();
        assert!(
            scanned.len() >= 20,
            "Expected at least 20 assets in I:\\EpicGames\\Megascans, got {}",
            scanned.len()
        );
        assert!(scanned.iter().any(|a| a.asset_type == "3d"));
        assert!(scanned.iter().any(|a| a.asset_type == "3dplant"));
        assert!(scanned.iter().any(|a| a.asset_type == "surface"));
    }
}
