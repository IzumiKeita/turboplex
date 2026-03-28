use std::path::PathBuf;
use walkdir::WalkDir;

pub fn discover_test_paths(max_depth: usize) -> Vec<PathBuf> {
    let mut paths = Vec::new();
    let current_dir = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));

    let exclude_patterns = [
        ".git",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "node_modules",
        ".tox",
        ".eggs",
        "*.egg-info",
        ".coverage",
        ".hypothesis",
    ];

    for entry in WalkDir::new(&current_dir)
        .max_depth(max_depth)
        .follow_links(false)
        .into_iter()
        .filter_map(|e| e.ok())
    {
        let path = entry.path();

        let should_skip = path.components().any(|c| {
            let component = c.as_os_str().to_string_lossy();
            exclude_patterns.iter().any(|p| {
                if let Some(stripped) = p.strip_prefix('*') {
                    component.ends_with(stripped)
                } else {
                    component == *p
                }
            })
        });

        if should_skip {
            continue;
        }

        if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
            if name.starts_with("test_") && name.ends_with(".py") {
                if let Some(parent) = path.parent() {
                    if !paths.contains(&parent.to_path_buf()) {
                        paths.push(parent.to_path_buf());
                    }
                }
            }
        }
    }

    paths.sort();
    paths.dedup();
    paths
}
