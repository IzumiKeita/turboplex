use rayon::prelude::*;
use std::path::PathBuf;

use super::config::{TestContext, TestDefinition};
use super::python::PythonCollectedItem;
use super::result::TestResult;
use super::shell::run_test;

#[derive(Clone)]
pub enum TestJob {
    Shell { file: PathBuf, def: TestDefinition },
    Python { item: PythonCollectedItem },
}

pub fn run_jobs_parallel(jobs: &[TestJob], ctx: &TestContext) -> Vec<(PathBuf, TestResult)> {
    let cache_dir = ctx.cache_dir.clone();
    let progress = ctx.progress.clone();
    let config = ctx.config.clone();

    jobs.par_iter()
        .map(|job| match job {
            TestJob::Shell { file, def } => {
                let result = run_test(def, file, ctx);
                (file.clone(), result)
            }
            TestJob::Python { item } => {
                let path = PathBuf::from(&item.path);
                let result =
                    super::python::run_python_item_fixed(item, &config, &cache_dir, &progress);
                (path, result)
            }
        })
        .collect()
}
