//! Temporary file utilities for JSON communication

use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::SystemTime;

static TMP_COUNTER: AtomicU64 = AtomicU64::new(0);

/// Generate a unique temporary JSON file path
pub(crate) fn temp_json_path(prefix: &str) -> PathBuf {
    let n = TMP_COUNTER.fetch_add(1, Ordering::Relaxed);
    let ts = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    std::env::temp_dir().join(format!(
        "{}_{}_{}_{}.json",
        prefix,
        std::process::id(),
        ts,
        n
    ))
}
