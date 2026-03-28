use sha2::{Digest, Sha256};
use std::collections::hash_map::DefaultHasher;
use std::fs::{self, File};
use std::hash::{Hash, Hasher};
use std::io::Read;
use std::path::{Path, PathBuf};

use super::config::TestDefinition;
use super::result::TestResult;

pub fn compute_file_hash(path: &Path) -> Option<String> {
    let mut file = File::open(path).ok()?;
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 8192];
    loop {
        let bytes_read = file.read(&mut buffer).ok()?;
        if bytes_read == 0 {
            break;
        }
        hasher.update(&buffer[..bytes_read]);
    }
    Some(hex::encode(hasher.finalize()))
}

pub fn compute_string_hash(s: &str) -> u64 {
    let mut hasher = DefaultHasher::new();
    s.hash(&mut hasher);
    hasher.finish()
}

fn get_cache_path(cache_dir: &Path, test_file: &Path, cache_key: &str) -> PathBuf {
    let mut hasher = DefaultHasher::new();
    test_file.hash(&mut hasher);
    cache_key.hash(&mut hasher);
    let hash = hasher.finish();
    cache_dir.join(format!("{:016x}.cache", hash))
}

pub(crate) fn shell_cache_key(test: &TestDefinition) -> String {
    format!("{}:{:?}", test.command, test.args)
}

pub(crate) fn check_cache(
    cache_dir: &Path,
    test_file: &Path,
    cache_key: &str,
) -> Option<TestResult> {
    let cache_path = get_cache_path(cache_dir, test_file, cache_key);
    let yaml_hash = compute_file_hash(test_file)?;
    let yaml_modified = fs::metadata(test_file).ok()?.modified().ok()?;

    if let Ok(cache_content) = fs::read_to_string(&cache_path) {
        let cache_parts: Vec<&str> = cache_content.split('\n').collect();
        if cache_parts.len() >= 3 {
            let cached_yaml_hash = cache_parts[0];
            let cached_result = cache_parts[1];
            let cached_time: u64 = cache_parts[2].parse().unwrap_or(0);

            if cached_yaml_hash == yaml_hash {
                let yaml_modified_secs = yaml_modified
                    .duration_since(std::time::SystemTime::UNIX_EPOCH)
                    .map(|d| d.as_secs())
                    .unwrap_or(0);

                if cached_time >= yaml_modified_secs {
                    return Some(TestResult {
                        test_name: cached_result.to_string(),
                        passed: true,
                        cached: true,
                        duration_ms: 0,
                        error: None,
                    });
                }
            }
        }
    }
    None
}

pub(crate) fn save_cache(cache_dir: &Path, test_file: &Path, cache_key: &str, result: &TestResult) {
    let cache_path = get_cache_path(cache_dir, test_file, cache_key);
    if let Some(yaml_hash) = compute_file_hash(test_file) {
        if let Ok(metadata) = fs::metadata(test_file) {
            if let Ok(modified) = metadata.modified() {
                let modified_secs = modified
                    .duration_since(std::time::SystemTime::UNIX_EPOCH)
                    .map(|d| d.as_secs())
                    .unwrap_or(0);
                let cache_content =
                    format!("{}\n{}\n{}\n", yaml_hash, result.test_name, modified_secs);
                let _ = fs::create_dir_all(cache_dir);
                let _ = fs::write(&cache_path, cache_content);
            }
        }
    }
}
