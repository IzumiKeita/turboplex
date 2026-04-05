use colored::Colorize;
use notify::{Config, RecommendedWatcher, RecursiveMode, Watcher};
use std::path::PathBuf;
use std::sync::mpsc::channel;
use std::thread;
use std::time::{Duration, Instant};
use turboplex::{load_config, python_config_effective};

mod doctor;
mod output;
mod part1;
mod part2;
mod pytest_compat;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum ExecutionMode {
    Native,
    Pytest,
    Unittest,
    Behave,
}

pub(crate) fn entry() {
    let args: Vec<String> = std::env::args().collect();

    if args.iter().any(|a| a == "--help" || a == "-h") {
        part1::print_help();
        return;
    }

    // Check for --analyze mode first
    if args.iter().any(|a| a == "--analyze") {
        let report_path = std::path::PathBuf::from("turboplex_full_report.json");
        if !report_path.exists() {
            eprintln!(
                "Error: turboplex_full_report.json not found. Run tests first with --jsonl flag."
            );
            std::process::exit(1);
        }

        match turboplex::indexer::analyze_report(&report_path) {
            Ok(report) => {
                // Imprimir resumen ejecutivo
                println!("\n{}", "═".repeat(60).cyan().bold());
                println!("{}", " TurboPlex Analysis Report ".cyan().bold());
                println!("{}", "═".repeat(60).cyan().bold());

                println!("\n{}", "📊 Summary".bold());
                println!("   Total:  {}", report.total_tests);
                println!("   Passed: {}", report.passed.to_string().green());
                println!("   Failed: {}", report.failed.to_string().red());
                println!(
                    "   Rate:   {:.1}%",
                    (report.passed as f64 / report.total_tests as f64) * 100.0
                );

                if !report.critical_issues.is_empty() {
                    println!("\n{}", "🚨 Critical Issues".red().bold());
                    for issue in &report.critical_issues {
                        println!("   • {}", issue);
                    }
                }

                println!("\n{}", "📋 Error Categories".bold());
                for cat in &report.categories {
                    let color = if cat.count > 20 {
                        "red".to_string()
                    } else if cat.count > 5 {
                        "yellow".to_string()
                    } else {
                        "white".to_string()
                    };
                    println!(
                        "   [{}] {} - {}",
                        cat.count.to_string().color(color),
                        cat.category,
                        cat.pattern.dimmed()
                    );
                }

                println!("\n{}", "💡 Top Recommendations".green().bold());
                for (i, rec) in report.recommendations.iter().enumerate() {
                    println!("   {}. {}", i + 1, rec);
                }

                // Opcional: Generar JSON del análisis para la IA
                match serde_json::to_string_pretty(&report) {
                    Ok(analysis_json) => {
                        println!("\n{}", "📄 Full Analysis JSON (for AI agents):".dimmed());
                        println!("{}", analysis_json);
                    }
                    Err(e) => {
                        eprintln!("Warning: Could not serialize analysis JSON: {}", e);
                    }
                }

                std::process::exit(0);
            }
            Err(e) => {
                eprintln!("Analysis failed: {}", e);
                std::process::exit(1);
            }
        }
    }

    if args.len() > 1 && args[1] == "mcp" {
        std::process::exit(part1::run_mcp_server());
    }

    // Check for --doctor mode
    if args.iter().any(|a| a == "--doctor") {
        let cwd = std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."));
        let doctor_json = args.iter().any(|a| a == "--json");
        let fail_on_warn = args.iter().any(|a| a == "--fail-on-warn");
        let unittest_mode = args.iter().any(|a| a == "--unittest");
        let behave_mode = args.iter().any(|a| a == "--behave");
        let compat = args
            .iter()
            .any(|a| a == "--compat" || a == "--compat-session");

        if unittest_mode && behave_mode {
            eprintln!("Error: --unittest and --behave are mutually exclusive");
            std::process::exit(1);
        }
        if (unittest_mode || behave_mode) && compat {
            eprintln!("Error: --compat cannot be combined with --unittest/--behave");
            std::process::exit(1);
        }

        let execution_mode = if unittest_mode {
            ExecutionMode::Unittest
        } else if behave_mode {
            ExecutionMode::Behave
        } else if compat {
            ExecutionMode::Pytest
        } else {
            ExecutionMode::Native
        };

        let env = part1::build_runtime_python_env(execution_mode, compat, compat, "doctor=1");
        let code = doctor::diagnose_project(
            &cwd,
            &env,
            doctor::DoctorOptions {
                json: doctor_json,
                fail_on_warn,
            },
        );
        std::process::exit(code);
    }

    let mut test_paths: Vec<String> = Vec::new();
    let watch_mode = args.iter().any(|a| a == "--watch" || a == "-w");
    let mut compat = false;
    let mut compat_per_test = false;
    let light_mode = args.iter().any(|a| a == "--light");
    let mut unittest_mode = false;
    let mut behave_mode = false;
    let mut quiet = false;
    let mut verbose = false;
    let mut json = false;
    let mut out_json: Option<String> = None;
    let mut workers: Option<usize> = None;

    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--path" | "-p" if i + 1 < args.len() => {
                test_paths.push(args[i + 1].clone());
                i += 2;
            }
            "--out-json" | "--report-json" if i + 1 < args.len() => {
                out_json = Some(args[i + 1].clone());
                i += 2;
            }
            "--workers" | "-j" if i + 1 < args.len() => {
                if let Ok(w) = args[i + 1].parse::<usize>() {
                    workers = Some(w);
                }
                i += 2;
            }
            "--watch" | "-w" => {
                i += 1;
            }
            "--compat" => {
                compat = true;
                i += 1;
            }
            "--compat-session" => {
                compat = true;
                i += 1;
            }
            "--compat-per-test" => {
                compat = true;
                compat_per_test = true;
                i += 1;
            }
            "--unittest" => {
                unittest_mode = true;
                i += 1;
            }
            "--behave" => {
                behave_mode = true;
                i += 1;
            }
            "--light" => {
                i += 1;
            }
            "--quiet" => {
                quiet = true;
                i += 1;
            }
            "--verbose" => {
                verbose = true;
                i += 1;
            }
            "--json" => {
                json = true;
                i += 1;
            }
            a if !a.starts_with('-') && a != "turboplex" => {
                test_paths.push(args[i].clone());
                i += 1;
            }
            _ => i += 1,
        }
    }

    let mode = if json {
        output::OutputMode::Json
    } else if quiet {
        output::OutputMode::Quiet
    } else if verbose {
        output::OutputMode::Verbose
    } else {
        output::OutputMode::Quiet
    };

    let out_opts = output::OutputOptions {
        mode,
        out_json: out_json.map(std::path::PathBuf::from),
    };

    if mode == output::OutputMode::Verbose {
        println!("\n{}", "TurboTest Engine".bold().cyan());
    }

    // Set TPX_WORKERS if --workers flag is used
    if let Some(w) = workers {
        std::env::set_var("TPX_WORKERS", w.to_string());
        if mode == output::OutputMode::Verbose {
            println!("{} Workers: {}", "⚡".yellow(), w);
        }
    }
    if light_mode {
        std::env::set_var("TPX_MCP_LIGHT_COLLECT", "1");
        if mode == output::OutputMode::Verbose {
            println!(
                "{} Light collect mode enabled (skipping conftest.py)",
                "⚡".yellow()
            );
        }
    }

    if unittest_mode && behave_mode {
        eprintln!("Error: --unittest and --behave are mutually exclusive");
        std::process::exit(1);
    }
    if (unittest_mode || behave_mode) && compat {
        eprintln!("Error: --compat cannot be combined with --unittest/--behave");
        std::process::exit(1);
    }

    let execution_mode = if unittest_mode {
        ExecutionMode::Unittest
    } else if behave_mode {
        ExecutionMode::Behave
    } else if compat {
        ExecutionMode::Pytest
    } else {
        ExecutionMode::Native
    };

    let compat_session = compat && !compat_per_test;
    let runtime_env = part1::build_runtime_python_env(
        execution_mode,
        compat,
        compat_session,
        if watch_mode { "watch=1" } else { "watch=0" },
    );

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
        if mode == output::OutputMode::Verbose {
            println!(
                "\n{} {} - Press Ctrl+C to exit",
                "👀".yellow().bold(),
                "Watch Mode enabled".yellow()
            );
        }

        part2::run_tests_with_paths(&paths_to_use, true, &runtime_env, &out_opts);

        let (tx, rx) = channel();
        let paths_clone: Vec<PathBuf> = paths_to_use
            .iter()
            .map(|p| part2::resolve_test_path(&runtime_env, p))
            .collect();

        let mut watcher = match RecommendedWatcher::new(
            move |res: Result<notify::Event, notify::Error>| {
                if let Ok(event) = res {
                    if event.kind.is_modify() || event.kind.is_create() {
                        let _ = tx.send(());
                    }
                }
            },
            Config::default().with_poll_interval(Duration::from_secs(1)),
        ) {
            Ok(w) => w,
            Err(e) => {
                eprintln!("Error: Failed to create file watcher: {}", e);
                eprintln!("Watch mode requires file system notifications.");
                std::process::exit(1);
            }
        };

        for p in &paths_clone {
            let _ = watcher.watch(p, RecursiveMode::Recursive);
        }

        if mode == output::OutputMode::Verbose {
            println!("\n{} Watching for changes...", "👀".yellow());
        }

        let mut last_run = Instant::now();
        loop {
            if rx.recv_timeout(Duration::from_secs(1)).is_ok() {
                thread::sleep(Duration::from_millis(500));
                while rx.try_recv().is_ok() {}

                let elapsed = last_run.elapsed();
                if elapsed > Duration::from_secs(1) {
                    if mode == output::OutputMode::Verbose {
                        println!("\n{} File changed, re-running tests...", "🔄".cyan());
                    }
                    part2::run_tests_with_paths(&paths_to_use, true, &runtime_env, &out_opts);
                    last_run = Instant::now();
                }
            }
        }
    } else {
        part2::run_tests_with_paths(&paths_to_use, false, &runtime_env, &out_opts);
    }
}
