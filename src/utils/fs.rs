//! File system utilities for atomic writes

use std::fs;
use std::path::Path;

/// Atomic write: write to temp file, then rename to target
/// This ensures that the file is never in a partially written state visible to readers
pub fn atomic_write_json(path: &Path, text: &str) -> std::io::Result<()> {
    let temp_path = path.with_extension("tmp");
    fs::write(&temp_path, text)?;
    fs::rename(&temp_path, path)
}

/// Atomic write for binary/text content with explicit temp extension
pub fn atomic_write(path: &Path, content: &str, temp_ext: &str) -> std::io::Result<()> {
    let temp_path = path.with_extension(temp_ext);
    fs::write(&temp_path, content)?;
    fs::rename(&temp_path, path)
}
