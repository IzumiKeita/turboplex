use std::io::Read;
use std::process::{Command, Stdio};
use std::thread;
use std::time::Duration;
use wait_timeout::ChildExt;

use super::config::CapturedOutput;

/// Macro para logging condicional de debug
/// Solo activo cuando el feature "debug-logging" esta habilitado
#[cfg(feature = "debug-logging")]
macro_rules! debug_log {
    ($($arg:tt)*) => {
        eprintln!($($arg)*)
    };
}

#[cfg(not(feature = "debug-logging"))]
macro_rules! debug_log {
    ($($arg:tt)*) => {};
}

pub fn run_process_with_timeout(
    cmd: &mut Command,
    timeout: Duration,
) -> std::io::Result<CapturedOutput> {
    cmd.stdin(Stdio::null());
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());

    let mut child = cmd.spawn()?;
    let mut stdout_pipe = child.stdout.take().expect("stdout piped");
    let mut stderr_pipe = child.stderr.take().expect("stderr piped");

    let stdout_handle = thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = stdout_pipe.read_to_end(&mut buf);
        buf
    });
    let stderr_handle = thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = stderr_pipe.read_to_end(&mut buf);
        buf
    });

    let wait_result = match child.wait_timeout(timeout) {
        Ok(r) => r,
        Err(e) => {
            let _ = child.kill();
            let _ = child.wait();
            let _ = stdout_handle.join();
            let _ = stderr_handle.join();
            return Err(e);
        }
    };

    match wait_result {
        Some(status) => {
            let stdout = stdout_handle.join().unwrap_or_default();
            let stderr = stderr_handle.join().unwrap_or_default();
            Ok(CapturedOutput {
                status: status.code(),
                stdout,
                stderr,
                timed_out: false,
            })
        }
        None => {
            if let Err(_e) = child.kill() {
                debug_log!("Failed to kill timed-out child process: {}", _e);
            }
            let _ = child.wait();
            let stdout = stdout_handle.join().unwrap_or_default();
            let stderr = stderr_handle.join().unwrap_or_default();
            Ok(CapturedOutput {
                status: None,
                stdout,
                stderr,
                timed_out: true,
            })
        }
    }
}
