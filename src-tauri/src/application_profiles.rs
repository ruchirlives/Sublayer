//! Application-wide defaults/capabilities. Projects store node choices and historical snapshots only.
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, path::Path};
fn catalog_revision() -> u32 {
    1
}
pub const SCOPES: [&str; 8] = [
    "transforms",
    "points",
    "topology",
    "uvs",
    "materials",
    "animation",
    "skeletons",
    "deletions",
];
#[derive(Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Catalog {
    pub version: u32,
    #[serde(default = "catalog_revision")]
    pub revision: u32,
    pub applications: Vec<Profile>,
    #[serde(default)]
    pub megascans_library: crate::megascans::MegascansConfig,
}
#[derive(Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Profile {
    pub id: String,
    pub revision: u32,
    pub app: String,
    pub application_version: String,
    pub adapter_version: u32,
    pub up_axis: String,
    pub meters_per_unit: f64,
    pub scale: f64,
    pub executable: String,
    pub import_behavior: String,
    pub export_behavior: String,
    pub capabilities: BTreeMap<String, Capability>,
}
#[derive(Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Capability {
    pub status: String,
    pub reason: String,
}
#[derive(Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PolicySnapshot {
    pub profile: Profile,
    pub edit_scope: Vec<String>,
}
impl Catalog {
    pub fn defaults() -> Self {
        let mut catalog: Self = serde_json::from_str(include_str!("../application-defaults.json"))
            .expect("valid bundled application catalog");
        catalog.megascans_library = Default::default();
        catalog
    }
    pub fn validate(&self) -> Result<(), String> {
        if self.revision == 0 || self.version != 1 || self.applications.len() > 100 {
            return Err("Invalid application catalog version or count".into());
        }
        self.megascans_library.validate()?;
        let mut ids = std::collections::HashSet::new();
        for p in &self.applications {
            if p.id.trim().is_empty()
                || !ids.insert(&p.id)
                || p.app.trim().is_empty()
                || p.application_version.trim().is_empty()
                || p.revision == 0
                || p.adapter_version == 0
                || !["Y", "Z"].contains(&p.up_axis.as_str())
                || [p.meters_per_unit, p.scale]
                    .iter()
                    .any(|v| !v.is_finite() || *v <= 0.)
            {
                return Err(
                    "Invalid application identity, coordinate defaults or duplicate ID".into(),
                );
            }
            if !p.executable.is_empty()
                && (!Path::new(&p.executable).is_absolute()
                    || !Path::new(&p.executable)
                        .extension()
                        .is_some_and(|x| x.eq_ignore_ascii_case("exe")))
            {
                return Err("Application executable must be an absolute .exe path or empty".into());
            }
            if p.capabilities.len() != SCOPES.len()
                || SCOPES.iter().any(|key| !p.capabilities.contains_key(*key))
            {
                return Err("Each application must declare all edit capabilities".into());
            }
            for c in p.capabilities.values() {
                if !["supported", "unsupported", "unverified"].contains(&c.status.as_str())
                    || c.reason.trim().is_empty()
                {
                    return Err("Capabilities require supported/unsupported/unverified status and an explanation".into());
                }
            }
        }
        Ok(())
    }
    pub fn policy(&self, data: &crate::project::NodeData) -> Result<PolicySnapshot, String> {
        self.validate()?;
        let id = data
            .application_profile_id
            .as_ref()
            .ok_or("Select an application profile")?;
        let profile = self
            .applications
            .iter()
            .find(|p| &p.id == id)
            .ok_or("Application profile is unavailable")?;
        if profile.app != data.app {
            return Err("Node application and profile do not match".into());
        }
        for scope in &data.edit_scope {
            let c = profile
                .capabilities
                .get(scope)
                .ok_or("Unknown edit scope")?;
            if c.status != "supported" {
                return Err(format!(
                    "{} cannot enable {scope}: {}",
                    profile.app, c.reason
                ));
            }
        }
        Ok(PolicySnapshot {
            profile: profile.clone(),
            edit_scope: data.edit_scope.clone(),
        })
    }
}
pub fn load(path: &Path) -> Result<Catalog, String> {
    if !path.exists() {
        let mut catalog = Catalog::defaults();
        catalog.megascans_library = crate::megascans::load_config(path)?;
        return Ok(catalog);
    }
    if std::fs::metadata(path).map_err(|e| e.to_string())?.len() > 1024 * 1024 {
        return Err("Application catalog exceeds 1 MB".into());
    }
    let result: Catalog = serde_json::from_slice(&std::fs::read(path).map_err(|e| e.to_string())?)
        .map_err(|e| format!("Invalid application catalog: {e}"))?;
    let mut result = result;
    result.megascans_library = crate::megascans::load_config(path)?;
    result.validate()?;
    Ok(result)
}
pub fn save(path: &Path, catalog: &Catalog) -> Result<(), String> {
    catalog.validate()?;
    let old = load(path).unwrap_or(Catalog {
        version: 1,
        revision: 1,
        applications: vec![],
        megascans_library: Default::default(),
    });
    if catalog.megascans_library != old.megascans_library && catalog.revision <= old.revision {
        return Err("Increase catalog revision when changing library settings".into());
    }
    for p in &catalog.applications {
        if let Some(previous) = old.applications.iter().find(|x| x.id == p.id) {
            if p != previous && p.revision <= previous.revision {
                return Err(format!(
                    "Increase profile {} revision when changing its configuration",
                    p.id
                ));
            }
        }
    }
    let parent = path
        .parent()
        .ok_or("Application configuration directory is missing")?;
    std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    // Serializing/validation precedes the write. App settings are deliberately
    // separate from project JSON and are written only by the settings Save action.
    let json = serde_json::to_vec_pretty(catalog).map_err(|e| e.to_string())?;
    if json.len() > 1024 * 1024 {
        return Err("Application catalog exceeds 1 MB".into());
    }
    use std::io::Write;
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_nanos();
    let temporary = parent.join(format!(".applications-{}-{stamp}.tmp", std::process::id()));
    let result = (|| {
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)
            .map_err(|e| e.to_string())?;
        file.write_all(&json)
            .and_then(|_| file.sync_all())
            .map_err(|e| e.to_string())?;
        drop(file);
        std::fs::rename(&temporary, path).map_err(|e| e.to_string())
    })();
    if temporary.exists() {
        let _ = std::fs::remove_file(temporary);
    }
    result
}

impl PolicySnapshot {
    pub fn same_preparation_contract(&self, other: &Self) -> bool {
        let mut a = self.profile.clone();
        let mut b = other.profile.clone();
        a.executable.clear();
        b.executable.clear();
        a.revision = 0;
        b.revision = 0;
        self.edit_scope == other.edit_scope && a == b
    }

    pub fn validate(&self) -> Result<(), String> {
        Catalog {
            version: 1,
            revision: 1,
            applications: vec![self.profile.clone()],
            megascans_library: Default::default(),
        }
        .validate()?;
        let mut seen = std::collections::HashSet::new();
        for scope in &self.edit_scope {
            if !seen.insert(scope)
                || self
                    .profile
                    .capabilities
                    .get(scope)
                    .is_none_or(|c| c.status != "supported")
            {
                return Err("Snapshot has duplicate, unknown or unsupported edit scope".into());
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn node() -> crate::project::NodeData {
        serde_json::from_value(serde_json::json!({"label":"Blender","operation":"application","app":"Blender","applicationProfileId":"blender","axis":"preserve","scale":1})).unwrap()
    }
    #[test]
    fn changing_executable_default_does_not_change_preparation_contract() {
        let policy = PolicySnapshot {
            profile: Catalog::defaults().applications[0].clone(),
            edit_scope: vec![],
        };
        let mut changed = policy.clone();
        changed.profile.executable = "C:/Apps/Blender/blender.exe".into();
        changed.profile.revision += 1;
        assert!(policy.same_preparation_contract(&changed));
        changed.profile.up_axis = "Y".into();
        assert!(!policy.same_preparation_contract(&changed));
        changed.profile.up_axis = "Z".into();
        changed
            .profile
            .capabilities
            .get_mut("points")
            .unwrap()
            .status = "supported".into();
        assert!(!policy.same_preparation_contract(&changed));
        assert!(policy.profile.executable.is_empty());
    }
    #[test]
    fn unknown_capabilities_fail_closed_and_snapshots_remain_immutable() {
        let mut catalog = Catalog::defaults();
        let mut node = node();
        node.edit_scope = vec!["transforms".into()];
        assert!(catalog.policy(&node).is_err());
        let capability = catalog.applications[0]
            .capabilities
            .get_mut("transforms")
            .unwrap();
        capability.status = "supported".into();
        capability.reason = "Verified test fixture".into();
        let snapshot = catalog.policy(&node).unwrap();
        snapshot.validate().unwrap();
        catalog.applications[0].revision += 1;
        catalog.applications[0]
            .capabilities
            .get_mut("transforms")
            .unwrap()
            .status = "unsupported".into();
        assert!(catalog.policy(&node).is_err());
        assert_eq!(snapshot.profile.revision, 2);
        assert_eq!(
            snapshot.profile.capabilities["transforms"].status,
            "supported"
        );
        snapshot.validate().unwrap();
        node.edit_scope = vec!["unknown-schema".into()];
        assert!(catalog.policy(&node).is_err());
        node.edit_scope.clear();
        node.application_profile_id = Some("missing".into());
        assert!(catalog.policy(&node).is_err());
    }
    #[test]
    fn catalog_save_is_separate_revisioned_and_validated() {
        let root = std::env::temp_dir().join(format!(
            "orchflow-profiles-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let path = root.join("applications.json");
        let mut catalog = load(&path).unwrap();
        assert!(!path.exists());
        save(&path, &catalog).unwrap();
        let before = std::fs::read(&path).unwrap();
        catalog.applications[0].meters_per_unit = 0.01;
        assert!(save(&path, &catalog).is_err());
        assert_eq!(std::fs::read(&path).unwrap(), before);
        catalog.applications[0].revision += 1;
        save(&path, &catalog).unwrap();
        assert_eq!(load(&path).unwrap().applications[0].meters_per_unit, 0.01);
        catalog.applications[0].up_axis = "X".into();
        assert!(save(&path, &catalog).is_err());
        std::fs::write(&path, b"invalid JSON").unwrap();
        assert!(load(&path).is_err());
        catalog.applications[0].up_axis = "Z".into();
        save(&path, &catalog).unwrap();
        assert_eq!(std::fs::read_dir(&root).unwrap().count(), 1);
        std::fs::remove_file(path).unwrap();
        std::fs::remove_dir(root).unwrap();
    }
    #[test]
    fn profiles_require_complete_capability_records() {
        let mut c = Catalog::defaults();
        c.applications[0].capabilities.remove("uvs");
        assert!(c.validate().is_err());
        let mut c = Catalog::defaults();
        c.applications.push(c.applications[0].clone());
        assert!(c.validate().is_err());
        let mut c = Catalog::defaults();
        c.applications[0]
            .capabilities
            .get_mut("uvs")
            .unwrap()
            .reason
            .clear();
        assert!(c.validate().is_err());
    }
}
