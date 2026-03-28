use serde::Deserialize;
use std::fs;
use std::path::{Path, PathBuf};
use walkdir::WalkDir;

use super::config::{ExpectedOutput, TestDefinition};

#[derive(Debug, Deserialize)]
struct YamlSuiteFile {
    #[allow(dead_code)]
    name: Option<String>,
    tests: Vec<SuiteTestEntry>,
}

#[derive(Debug, Deserialize)]
struct SuiteTestEntry {
    name: String,
    command: String,
    #[serde(default)]
    args: Vec<String>,
    #[serde(default)]
    expected_exit_code: Option<i32>,
    #[serde(default)]
    timeout_ms: Option<u64>,
    #[serde(default)]
    contains: Option<String>,
}

fn normalize_shell_command(command: &str, args: &[String]) -> (String, Vec<String>) {
    if !args.is_empty() {
        return (command.to_string(), args.to_vec());
    }
    let needs_shell = command.contains(' ') || command.contains('\t');
    if !needs_shell {
        return (command.to_string(), vec![]);
    }
    #[cfg(windows)]
    {
        (
            "cmd".to_string(),
            vec!["/C".to_string(), command.to_string()],
        )
    }
    #[cfg(not(windows))]
    {
        (
            "sh".to_string(),
            vec!["-c".to_string(), command.to_string()],
        )
    }
}

fn suite_entry_to_definition(entry: SuiteTestEntry) -> TestDefinition {
    let (command, args) = normalize_shell_command(&entry.command, &entry.args);
    TestDefinition {
        name: entry.name,
        command,
        args,
        expected: Some(ExpectedOutput {
            status_code: entry.expected_exit_code,
            contains: entry.contains,
            timeout_ms: entry.timeout_ms,
        }),
        priority: String::new(),
    }
}

fn parse_yaml_test_file(_path: &Path, content: &str) -> Vec<TestDefinition> {
    if let Ok(def) = serde_yaml::from_str::<TestDefinition>(content) {
        let (command, args) = normalize_shell_command(&def.command, &def.args);
        return vec![TestDefinition {
            command,
            args,
            ..def
        }];
    }
    if let Ok(suite) = serde_yaml::from_str::<YamlSuiteFile>(content) {
        return suite
            .tests
            .into_iter()
            .map(suite_entry_to_definition)
            .collect();
    }
    Vec::new()
}

pub fn discover_tests(tests_dir: &str) -> Vec<(PathBuf, TestDefinition)> {
    let mut tests = Vec::new();

    for entry in WalkDir::new(tests_dir)
        .follow_links(true)
        .into_iter()
        .filter_map(|e| e.ok())
    {
        let path = entry.path();
        let is_yaml = path
            .extension()
            .and_then(|e| e.to_str())
            .map(|e| e.eq_ignore_ascii_case("yaml") || e.eq_ignore_ascii_case("yml"))
            .unwrap_or(false);
        if !is_yaml {
            continue;
        }
        if let Ok(content) = fs::read_to_string(path) {
            let defs = parse_yaml_test_file(path, &content);
            for def in defs {
                tests.push((path.to_path_buf(), def));
            }
        }
    }

    tests
}
