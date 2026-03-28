use colored::Colorize;
use notify::{Config, RecommendedWatcher, RecursiveMode, Watcher};
use std::path::PathBuf;
use std::sync::mpsc::channel;
use std::thread;
use std::time::{Duration, Instant};
use turboplex::{load_config, python_config_effective};

mod part1;
mod part2;

pub(crate) fn entry() {
    let args: Vec<String> = std::env::args().collect();

    if args.iter().any(|a| a == "--help" || a == "-h") {
        part1::print_help();
        return;
    }

    if args.len() > 1 && args[1] == "mcp" {
        std::process::exit(part1::run_mcp_server());
    }

    let mut test_paths: Vec<String> = Vec::new();
    let watch_mode = args.iter().any(|a| a == "--watch" || a == "-w");
    let compat = args.iter().any(|a| a == "--compat");

    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--path" | "-p" if i + 1 < args.len() => {
                test_paths.push(args[i + 1].clone());
                i += 2;
            }
            "--watch" | "-w" => {
                i += 1;
            }
            "--compat" => {
                i += 1;
            }
            a if !a.starts_with('-') && a != "turboplex" => {
                test_paths.push(args[i].clone());
                i += 1;
            }
            _ => i += 1,
        }
    }

    println!("\n{}", "TurboTest Engine".bold().cyan());
    let runtime_env =
        part1::build_runtime_python_env(compat, if watch_mode { "watch=1" } else { "watch=0" });

    let config_paths = [
        "turbo_config.toml",
        "../turbo_config.toml",
        "./pyproject.toml",
    ];
    let config = config_paths
        .iter()
        .find(|p| std::path::Path::new(p).exists())
        .map(|p| load_config(p))
        .unwrap_or_default();

    let py_cfg = python_config_effective(&config);

    let paths_to_use: Vec<String> = if test_paths.is_empty() {
        if py_cfg.test_paths.is_empty() {
            part1::discover_test_paths_from(&runtime_env.cwd, part1::DEFAULT_MAX_DEPTH)
                .iter()
                .map(|p| p.to_string_lossy().to_string())
                .collect()
        } else {
            py_cfg.test_paths.clone()
        }
    } else {
        test_paths
    };

    if watch_mode {
        println!(
            "\n{} {} - Press Ctrl+C to exit",
            "👀".yellow().bold(),
            "Watch Mode enabled".yellow()
        );

        part2::run_tests_with_paths(&paths_to_use, true, &runtime_env);

        let (tx, rx) = channel();
        let paths_clone: Vec<PathBuf> = paths_to_use
            .iter()
            .map(|p| part2::resolve_test_path(&runtime_env, p))
            .collect();

        let mut watcher = RecommendedWatcher::new(
            move |res: Result<notify::Event, notify::Error>| {
                if let Ok(event) = res {
                    if event.kind.is_modify() || event.kind.is_create() {
                        let _ = tx.send(());
                    }
                }
            },
            Config::default().with_poll_interval(Duration::from_secs(1)),
        )
        .unwrap();

        for p in &paths_clone {
            let _ = watcher.watch(p, RecursiveMode::Recursive);
        }

        println!("\n{} Watching for changes...", "👀".yellow());

        let mut last_run = Instant::now();
        loop {
            if rx.recv_timeout(Duration::from_secs(1)).is_ok() {
                thread::sleep(Duration::from_millis(500));
                while rx.try_recv().is_ok() {}

                let elapsed = last_run.elapsed();
                if elapsed > Duration::from_secs(1) {
                    println!("\n{} File changed, re-running tests...", "🔄".cyan());
                    part2::run_tests_with_paths(&paths_to_use, true, &runtime_env);
                    last_run = Instant::now();
                }
            }
        }
    } else {
        part2::run_tests_with_paths(&paths_to_use, false, &runtime_env);
    }
}
