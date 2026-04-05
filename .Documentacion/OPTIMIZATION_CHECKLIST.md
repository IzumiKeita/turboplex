# TurboPlex Optimization Checklist
## Ensuring Peak Performance (218x Speedup)

---

## 🎯 Purpose

This checklist ensures that TurboPlex is configured for maximum performance. Use this before benchmarking or deploying to production.

---

## ✅ Core Configuration

### 1. Database Connection Pooling

**File**: `tests/benchmark/conftest.py`

- [ ] `echo=False` configured (line 134)
- [ ] PostgreSQL pool: `pool_size=10, max_overflow=20`
- [ ] SQL Server pool: `pool_size=15, max_overflow=15`
- [ ] `pool_pre_ping=True` enabled
- [ ] `pool_recycle=3600` configured

**Verification**:
```python
# Should see in conftest.py:
engine = create_engine(DATABASE_URL, echo=False, **pool_config)
```

---

### 2. Rust Release Profile

**File**: `Cargo.toml`

- [x] `[profile.release]` section exists
- [x] `opt-level = 3` (maximum optimization)
- [x] `lto = true` (Link-Time Optimization)
- [x] `codegen-units = 1` (single codegen unit)
- [x] `strip = true` (remove debug symbols)
- [x] `panic = 'abort'` (faster panic handling)

**Verification**:
```bash
# Build with optimizations
cargo build --release

# Binary should be in target/release/tpx.exe
ls -lh target/release/tpx.exe
```

**Expected impact**: 10-20% additional performance vs unoptimized release build.

---

### 3. Python Dependencies

**File**: `pyproject.toml`

- [x] `pytest>=7.0` (modern version)
- [x] `pytest-xdist>=3.0` (parallel support)
- [x] Minimal dependencies (no bloat)

**Verification**:
```bash
pip list | grep pytest
# Should show pytest 7.x or higher
```

---

## 🗄️ Database Configuration

### PostgreSQL (Recommended)

**Server Configuration** (`postgresql.conf`):
- [ ] `max_connections = 200`
- [ ] `shared_buffers = 256MB`
- [ ] `effective_cache_size = 1GB`
- [ ] `work_mem = 16MB`

**Docker Configuration**:
```bash
docker run -d --name postgres \
  --memory=2g \
  --cpus=2 \
  -e POSTGRES_PASSWORD=turboplex \
  -p 5432:5432 \
  postgres:15 \
  -c 'max_connections=200' \
  -c 'shared_buffers=256MB'
```

### SQL Server

**Docker Configuration**:
```bash
docker run -d --name mssql \
  --memory=3g \
  --cpus=4 \
  -e ACCEPT_EULA=Y \
  -e SA_PASSWORD=TurboP1ex! \
  -e MSSQL_MEMORY_LIMIT_MB=2500 \
  -p 1433:1433 \
  mcr.microsoft.com/mssql/server:2022-latest
```

**Server Configuration**:
```sql
EXEC sp_configure 'max server memory (MB)', 2560;
EXEC sp_configure 'max degree of parallelism', 4;
RECONFIGURE;
```

### MariaDB

**Docker Configuration**:
```bash
docker run -d --name mariadb \
  --memory=1g \
  -e MYSQL_ROOT_PASSWORD=turboplex \
  -e MYSQL_DATABASE=turboplex_test \
  -p 3306:3306 \
  mariadb:10.11
```

---

## 🧹 Cleanup & Maintenance

### Remove Obsolete Files

- [ ] Delete old egg-info directories:
  ```bash
  rmdir /s /q turbotest.egg-info
  rmdir /s /q turboptest.egg-info
  ```
- [ ] Keep only `turboplex.egg-info` (current)

### Clean Temporary Files

- [ ] Remove old benchmark results:
  ```bash
  # Moved to .benchmarks/results/
  # Old files in root should be deleted
  ```

### Verify .gitignore

- [x] Benchmark results excluded: `.benchmarks/results/*.json`
- [x] Logs excluded: `.benchmarks/logs/*.log`
- [x] Scripts included: `!.benchmarks/scripts/*.py`
- [x] Temporary files excluded: `*.tplex_report.json`, `latest_failures.md`

---

## 🚀 Performance Validation

### Pre-Benchmark Checklist

Before running benchmarks:

1. **Clean environment**:
   ```bash
   # Stop all containers
   docker stop $(docker ps -aq)
   
   # Clear cache
   rm -rf .tpx_cache/*
   
   # Rebuild Rust binary
   cargo clean
   cargo build --release
   ```

2. **Start database**:
   ```bash
   # PostgreSQL (recommended)
   docker run -d --name postgres \
     -e POSTGRES_PASSWORD=turboplex \
     -p 5432:5432 postgres:15
   
   # Wait for ready
   sleep 10
   ```

3. **Verify configuration**:
   ```bash
   # Check conftest.py has echo=False
   grep "echo=False" tests/benchmark/conftest.py
   
   # Check Cargo.toml has release profile
   grep -A 5 "\[profile.release\]" Cargo.toml
   ```

4. **Run micro-profiling**:
   ```bash
   python .benchmarks/scripts/bench_microprofiling.py
   ```

### Expected Results

| Database | Tests | Target Speedup | Target Latency |
|----------|-------|----------------|----------------|
| PostgreSQL | 1500 | >200x | <0.5 ms/test |
| MariaDB | 1500 | >30x | <2.5 ms/test |
| SQL Server | 1500 | >10x | <2.0 ms/test |
| SQLite | 1500 | >1.8x | <6.0 ms/test |

**If results are below target**:
1. Check database is running: `docker ps`
2. Verify connection pool config in conftest.py
3. Ensure `echo=False` is set
4. Check Rust binary is release build: `file target/release/tpx.exe`
5. Monitor resource usage: `docker stats`

---

## 📊 Monitoring

### During Execution

**CPU Usage**:
```bash
# Should be 90-100% during benchmark
top -p $(pgrep tpx)
```

**Memory Usage**:
```bash
# Should be <1GB for 4 workers
docker stats
```

**Database Connections**:
```sql
-- PostgreSQL
SELECT count(*) FROM pg_stat_activity;
-- Should be ≤30 connections

-- SQL Server
SELECT COUNT(*) FROM sys.dm_exec_sessions WHERE is_user_process = 1;
-- Should be ≤30 connections
```

### Post-Execution

**Verify Results**:
```bash
# Check JSON output
cat .benchmarks/results/benchmark_microprofiling_*.json | jq '.comparison.speedup'
# Should show >200 for PostgreSQL
```

**Analyze Logs**:
```bash
# Check for errors
grep -i error .benchmarks/logs/*.log

# Check for warnings
grep -i warn .benchmarks/logs/*.log
```

---

## 🔧 Troubleshooting

### Performance Below Target

**Symptom**: Speedup <100x on PostgreSQL

**Possible causes**:
1. ❌ `echo=True` in conftest.py (SQL logging overhead)
2. ❌ Connection pool too small
3. ❌ Database not optimized (low `max_connections`)
4. ❌ Debug build instead of release build
5. ❌ Insufficient hardware resources

**Solutions**:
```bash
# 1. Verify echo=False
grep "echo=" tests/benchmark/conftest.py

# 2. Check pool size
grep "pool_size" tests/benchmark/conftest.py

# 3. Increase DB connections
docker exec postgres psql -U postgres -c "ALTER SYSTEM SET max_connections = 200;"

# 4. Rebuild release
cargo build --release

# 5. Increase Docker limits
docker update --memory=4g --cpus=4 postgres
```

### High Variability (CV >10%)

**Symptom**: Coefficient of Variation >10%

**Possible causes**:
1. Background processes consuming resources
2. Database not warmed up
3. Network latency (if DB is remote)

**Solutions**:
```bash
# 1. Close unnecessary apps
# 2. Run warm-up pass first
python .benchmarks/scripts/bench_microprofiling.py --passes 1
# Then run actual benchmark
python .benchmarks/scripts/bench_microprofiling.py --passes 3

# 3. Use local database (Docker)
```

---

## 📝 Maintenance Schedule

### Daily (CI/CD)
- [ ] Run smoke tests (10 tests)
- [ ] Verify speedup >10x

### Weekly
- [ ] Run full benchmark suite
- [ ] Archive old results
- [ ] Clean logs >7 days old

### Monthly
- [ ] Update dependencies
- [ ] Rebuild from scratch
- [ ] Validate against baseline

---

## ✅ Final Verification

Before claiming "production-ready":

- [x] Cargo.toml has `[profile.release]`
- [x] conftest.py has `echo=False`
- [x] Connection pools configured
- [ ] PostgreSQL benchmark shows >200x speedup
- [ ] Documentation is up to date
- [ ] All tests passing
- [ ] No memory leaks detected
- [ ] Resource limits tested

---

## 📚 References

- **Performance Whitepaper**: `.Documentacion/PERFORMANCE_WHITEPAPER.md`
- **Database Tuning**: `.Documentacion/DATABASE_TUNING.md`
- **Operations Manual**: `.Documentacion/OPERATIONS_MANUAL.md`

---

*Checklist Version: 1.0*  
*Last Updated: 2025-03-31*  
*Maintained by: TurboPlex Engineering Team*
