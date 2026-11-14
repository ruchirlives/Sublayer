//! Read-only, bounded recursive lookup for explicitly selected missing files.
use crate::project::Dependency;
use std::{
    collections::HashMap,
    path::{Path, PathBuf},
};

pub fn find_missing(
    folder: &str,
    dependencies: Vec<Dependency>,
) -> Result<serde_json::Value, String> {
    let root = std::fs::canonicalize(folder).map_err(|e| e.to_string())?;
    let spelling = root.to_string_lossy();
    let ordinary = if let Some(unc) = spelling.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{}", unc)
    } else {
        spelling
            .strip_prefix(r"\\?\")
            .unwrap_or(&spelling)
            .to_string()
    };
    let root = PathBuf::from(ordinary);
    if !root.is_dir() {
        return Err("Choose a folder to search".into());
    }
    let mut wanted: HashMap<String, Vec<String>> = HashMap::new();
    let mut matches: HashMap<String, Vec<String>> = HashMap::new();
    let mut available = 0;
    for dependency in dependencies {
        if std::fs::metadata(&dependency.path).is_ok() {
            available += 1;
            continue;
        }
        let name = Path::new(&dependency.path)
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("")
            .to_lowercase();
        if name.is_empty() {
            continue;
        }
        wanted.entry(name).or_default().push(dependency.id.clone());
        matches.insert(dependency.id, Vec::new());
    }
    let mut stack: Vec<(PathBuf, usize)> = vec![(root, 0)];
    let (mut scanned, mut skipped, mut truncated) = (0usize, 0usize, false);
    if !wanted.is_empty() {
        'search: while let Some((directory, depth)) = stack.pop() {
            let entries = match std::fs::read_dir(directory) {
                Ok(entries) => entries,
                Err(_) => {
                    skipped += 1;
                    continue;
                }
            };
            for entry in entries {
                scanned += 1;
                if scanned > 250_000 {
                    truncated = true;
                    break 'search;
                }
                let entry = match entry {
                    Ok(entry) => entry,
                    Err(_) => {
                        skipped += 1;
                        continue;
                    }
                };
                let kind = match entry.file_type() {
                    Ok(kind) => kind,
                    Err(_) => {
                        skipped += 1;
                        continue;
                    }
                };
                if kind.is_dir() {
                    if depth >= 64 {
                        truncated = true;
                        skipped += 1
                    } else {
                        stack.push((entry.path(), depth + 1))
                    }
                } else if kind.is_symlink() {
                    // Never recurse through directory links/junctions: no loops
                    // or unintended searches outside the chosen folder tree.
                    if entry.path().is_dir() {
                        skipped += 1;
                        continue;
                    }
                    if entry.path().is_file() {
                        record(&entry.path(), &wanted, &mut matches, &mut truncated)
                    }
                } else if kind.is_file() {
                    record(&entry.path(), &wanted, &mut matches, &mut truncated)
                }
            }
        }
    }
    let mut rows: Vec<_> = matches
        .into_iter()
        .map(|(id, mut candidates)| {
            candidates.sort();
            serde_json::json!({"id":id,"candidates":candidates})
        })
        .collect();
    rows.sort_by(|a, b| a["id"].as_str().cmp(&b["id"].as_str()));
    Ok(
        serde_json::json!({"matches":rows,"scannedEntries":scanned,"skippedFolders":skipped,"alreadyAvailable":available,"truncated":truncated}),
    )
}
fn record(
    path: &Path,
    wanted: &HashMap<String, Vec<String>>,
    matches: &mut HashMap<String, Vec<String>>,
    truncated: &mut bool,
) {
    let name = path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("")
        .to_lowercase();
    if let Some(ids) = wanted.get(&name) {
        for id in ids {
            let candidates = matches.get_mut(id).unwrap();
            if candidates.len() < 100 {
                candidates.push(path.to_string_lossy().into_owned())
            } else {
                *truncated = true
            }
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn recursive_search_returns_ambiguous_matches_and_never_touches_files() {
        let root = std::env::temp_dir().join(format!(
            "orchflow-search-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let nested = root.join("nested");
        std::fs::create_dir_all(&nested).unwrap();
        let first = root.join("texture.png");
        let second = nested.join("TEXTURE.png");
        let unique = nested.join("normal.png");
        for path in [&first, &second, &unique] {
            std::fs::write(path, b"original").unwrap()
        }
        let deps = serde_json::from_value(serde_json::json!([
            {"id":"ambiguous","kind":"asset","path":root.join("missing/texture.png")},
            {"id":"unique","kind":"asset","path":root.join("missing/normal.png")},
            {"id":"none","kind":"asset","path":root.join("missing/absent.png")},
            {"id":"available","kind":"asset","path":first}
        ]))
        .unwrap();
        let result = find_missing(root.to_str().unwrap(), deps).unwrap();
        let rows = result["matches"].as_array().unwrap();
        assert_eq!(
            rows.iter().find(|r| r["id"] == "ambiguous").unwrap()["candidates"]
                .as_array()
                .unwrap()
                .len(),
            2
        );
        assert_eq!(
            rows.iter().find(|r| r["id"] == "unique").unwrap()["candidates"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(
            rows.iter().find(|r| r["id"] == "none").unwrap()["candidates"]
                .as_array()
                .unwrap()
                .len(),
            0
        );
        assert_eq!(result["alreadyAvailable"], 1);
        for path in [first, second, unique] {
            assert_eq!(std::fs::read(&path).unwrap(), b"original");
            std::fs::remove_file(path).unwrap()
        }
        std::fs::remove_dir(nested).unwrap();
        std::fs::remove_dir(root).unwrap();
    }
}
