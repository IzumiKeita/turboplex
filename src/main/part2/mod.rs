//! Test execution engine - Orchestrates test collection, caching, batching, and parallel execution.
//!
//! This module provides the core test running functionality, split into sub-modules:
//! - `temp`: Temporary file management for JSON communication
//! - `reports`: Failure report generation and management
//! - `cache`: Test result caching and skip detection
//! - `batch`: Batch test execution in Python subprocesses
//! - `collection`: Test discovery and collection with caching
//! - `runner`: Main test execution orchestration

mod batch;
mod cache;
mod collection;
mod reports;
mod runner;
mod temp;

pub(crate) use collection::resolve_test_path;
pub(crate) use runner::run_tests_with_paths;
