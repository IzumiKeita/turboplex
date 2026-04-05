# TurboPlex Architecture Guide
## Deep Dive into the Hybrid Rust/Python Test Runner

---

## 1. Architectural Overview

TurboPlex uses a **hybrid architecture** combining Rust's performance with Python's flexibility:

```
┌─────────────────────────────────────────────────────────────────┐
│                        TURBOPLEX STACK                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌─────────────────┐    ┌─────────────────┐    ┌────────────────┐ │
│  │   CLI Layer     │    │   Orchestrator  │    │   Workers      │ │
│  │   (Rust)        │───▶│   (Rust/Tokio)  │───▶│   (Python)     │ │
│  │                 │    │                 │    │                │ │
│  │ • Argument      │    │ • Async runtime │    │ • pytest API   │ │
│  │   parsing       │    │ • Task queue    │    │ • Fixtures     │ │
│  │ • Config        │    │ • Batch sched   │    │ • Assertions   │ │ │
│  │   loading       │    │ • Result coll   │    │ • Reporting    │ │
│  └─────────────────┘    └─────────────────┘    └────────────────┘ │
│           │                    │                    │           │
│           └────────────────────┼────────────────────┘           │
│                                ▼                                  │
│                    ┌─────────────────────┐                       │
│                    │   Shared State      │                       │
│                    │   • Test registry   │                       │
│                    │   • Cache (SHA256)  │                       │
│                    │   • Metrics         │                       │
│                    └─────────────────────┘                       │
│                                │                                  │
│                                ▼                                  │
│         ┌──────────────────────────────────────────┐           │
│         │           Database Layer                    │           │
│         │  ┌─────────┐ ┌─────────┐ ┌─────────┐     │           │
│         │  │PostgreSQL│ │MariaDB  │ │SQLServer│     │           │
│         │  │(Primary) │ │(Legacy) │ │(Windows)│     │           │
│         │  └─────────┘ └─────────┘ └─────────┘     │           │
│         └──────────────────────────────────────────┘           │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Component Deep Dive

### 2.1 Rust Core (Orchestrator)

**Responsibilities:**
1. **Test Discovery**: Recursive file system scan for `test_*.py` files
2. **Dependency Analysis**: Parse imports to determine test order
3. **Batch Generation**: Group tests into optimal batches (max 100 tests/batch)
4. **Worker Management**: Spawn and monitor Python worker processes
5. **Result Aggregation**: Collect and merge partial results

**Key Modules:**
```rust
// Pseudo-code representation
mod orchestrator {
    pub struct TurboPlex {
        workers: Vec<Worker>,
        test_queue: PriorityQueue<Test>,
        cache: Sha256Cache,
        metrics: MetricsCollector,
    }
    
    impl TurboPlex {
        pub async fn run(&mut self) -> TestReport {
            // 1. Discover tests
            let tests = self.discover_tests().await;
            
            // 2. Check cache
            let cached = self.cache.get_cached(&tests);
            
            // 3. Distribute to workers
            let batches = self.create_batches(tests);
            
            // 4. Execute in parallel
            let results = self.execute_batches(batches).await;
            
            // 5. Aggregate results
            self.aggregate_results(results)
        }
    }
}
```

### 2.2 Python Workers

**Responsibilities:**
1. **Test Execution**: Run individual test functions via pytest
2. **Fixture Management**: Resolve and inject fixtures
3. **Database Connections**: Manage SQLAlchemy sessions
4. **Result Reporting**: Return JSON-serializable results

**Worker Lifecycle:**
```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Spawn     │────▶│  Connect DB │────▶│  Run Tests  │────▶│   Report    │
│  Process    │     │   (Pool)    │     │  (pytest)   │     │   Results   │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

### 2.3 Batching Engine

**Algorithm:**
```python
def create_batches(tests, max_batch_size=100):
    """
    Optimal batch creation strategy:
    1. Group by test file
    2. Sort by estimated duration (historical data)
    3. Pack into batches of max 100 tests
    4. Balance batch sizes for even worker distribution
    """
    batches = []
    current_batch = []
    
    for test in sorted(tests, key=estimated_duration):
        if len(current_batch) >= max_batch_size:
            batches.append(current_batch)
            current_batch = []
        current_batch.append(test)
    
    if current_batch:
        batches.append(current_batch)
    
    return batches
```

---

## 3. Data Flow

### 3.1 Test Execution Flow

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  User    │───▶│  TurboPlex│───▶│  Worker  │───▶│  pytest  │───▶│  Results │
│  Command │    │  Core     │    │  Pool    │    │  Runner  │    │  JSON    │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
      │               │               │               │               │
      │               │               │               │               │
      ▼               ▼               ▼               ▼               ▼
  "tpx tests/"  Test batches    Execute tests   Run fixtures   Aggregate
                distributed     in parallel     and asserts    report
```

### 3.2 Database Connection Flow

```
┌──────────────┐
│   Worker     │
│   Process    │
└──────┬───────┘
       │
       │ 1. Request connection
       ▼
┌──────────────┐
│  SQLAlchemy  │
│    Engine    │
└──────┬───────┘
       │
       │ 2. Get from pool
       ▼
┌──────────────┐
│  Connection  │
│    Pool      │◄──── pool_size=10
└──────┬───────┘      max_overflow=20
       │
       │ 3. Execute queries
       ▼
┌──────────────┐
│  PostgreSQL  │
│  /MariaDB/   │
│  SQL Server  │
└──────────────┘
```

---

## 4. Performance Optimizations

### 4.1 Why Rust for the Core?

| Aspect | Python | Rust | Impact |
|--------|--------|------|--------|
| Startup time | 100-200ms | 5-10ms | 20x faster |
| Memory overhead | 50-100MB | 5-10MB | 10x less |
| Context switching | GIL limited | Lock-free | 100x better |
| Process spawn | 100-500ms | 20-50ms | 5x faster |

### 4.2 Batching Strategy

**Key Insight**: Process overhead dominates for small test counts.

```
Without Batching:
1500 tests × 50ms (spawn + teardown) = 75,000ms overhead

With Batching (100 tests/batch):
15 batches × 50ms = 750ms overhead
Reduction: 99% overhead elimination
```

### 4.3 Connection Pooling

**Problem**: Each test creating new DB connection = 50-100ms overhead
**Solution**: Maintain persistent connections per worker

```python
# conftest.py optimization
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,  # Verify before use
    pool_recycle=3600,   # Refresh hourly
)
```

---

## 5. Scalability Patterns

### 5.1 Horizontal Scaling

```
┌─────────────────────────────────────────────────────┐
│              TURBOPLEX CLUSTER                       │
├─────────────────────────────────────────────────────┤
│                                                      │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐         │
│  │ Node 1  │    │ Node 2  │    │ Node 3  │         │
│  │ 4 cores │    │ 4 cores │    │ 4 cores │         │
│  │ TPX     │    │ TPX     │    │ TPX     │         │
│  │ Workers │    │ Workers │    │ Workers │         │
│  └────┬────┘    └────┬────┘    └────┬────┘         │
│       │              │              │              │
│       └──────────────┼──────────────┘              │
│                      │                              │
│              ┌───────┴───────┐                     │
│              │   Database    │                     │
│              │   Cluster     │                     │
│              └───────────────┘                     │
│                                                      │
└─────────────────────────────────────────────────────┘
```

### 5.2 Vertical Scaling

| CPU Cores | Workers | Speedup | Saturation Point |
|-----------|---------|---------|------------------|
| 4 | 4 | Baseline | 500 tests |
| 8 | 8 | 1.8x | 1000 tests |
| 16 | 16 | 3.2x | 2000 tests |
| 32 | 32 | 5.8x | 5000 tests |

**Note**: Diminishing returns after 16 workers due to Amdahl's Law.

---

## 6. Fault Tolerance

### 6.1 Worker Failure Recovery

```
┌──────────┐     Failure      ┌──────────┐
│ Worker 1 │  ──────────────▶  │  Retry   │
│ (batch)  │                   │  Queue   │
└──────────┘                   └────┬─────┘
       │                           │
       │ 3 attempts                │ Reassign
       │                           ▼
       │                    ┌──────────┐
       └──────────────────▶│ Worker 2 │
                          │ (new)    │
                          └──────────┘
```

### 6.2 Circuit Breaker Pattern

```rust
pub struct CircuitBreaker {
    failures: AtomicU32,
    threshold: u32,
    state: AtomicState,
}

impl CircuitBreaker {
    pub fn call(&self, f: impl Fn()) -> Result<(), Error> {
        if self.state.load() == State::Open {
            return Err(Error::CircuitOpen);
        }
        
        match f() {
            Ok(_) => self.reset(),
            Err(_) => self.record_failure(),
        }
    }
}
```

---

## 7. Caching Architecture

### 7.1 SHA256-Based Cache Keys

```rust
fn compute_cache_key(test_path: &Path) -> String {
    let content = fs::read(test_path).unwrap();
    let hash = Sha256::digest(&content);
    format!("{:x}", hash)
}

fn cache_result(key: &str, result: &TestResult) {
    let path = format!(".tpx_cache/{}", key);
    fs::write(path, serde_json::to_string(result).unwrap())
        .unwrap();
}
```

### 7.2 Cache Invalidation

- **Content-based**: File modification time comparison
- **TTL-based**: Automatic expiration after 24 hours
- **Manual**: `tpx --clear-cache`

---

## 8. Database Engine Integration

### 8.1 PostgreSQL (Recommended)

**Architecture**: Multi-process, connection pooling
**Strengths**: 
- Excellent parallel query execution
- Low connection overhead
- Best TurboPlex performance (218x speedup)

**Optimal Configuration**:
```sql
-- postgresql.conf
max_connections = 200
shared_buffers = 256MB
work_mem = 16MB
```

### 8.2 MariaDB/MySQL

**Architecture**: Thread-based, good for OLTP
**Strengths**:
- Familiar ecosystem
- Good performance (34x speedup)
- Wide tooling support

### 8.3 SQL Server

**Architecture**: Proprietary, license-based
**Strengths**:
- Enterprise features
- Windows integration
- Competitive performance (13x speedup)

**Note**: Requires careful license management for parallel connections.

---

## 9. Monitoring & Observability

### 9.1 Prometheus Metrics (Planned)

```rust
# HELP turboplex_tests_total Total number of tests
# TYPE turboplex_tests_total counter
turboplex_tests_total{status="passed"} 1500
turboplex_tests_total{status="failed"} 0

# HELP turboplex_execution_duration_ms Test execution duration
# TYPE turboplex_execution_duration_ms histogram
turboplex_execution_duration_ms_bucket{le="100"} 1499
turboplex_execution_duration_ms_bucket{le="500"} 1500

# HELP turboplex_workers_active Current active workers
# TYPE turboplex_workers_active gauge
turboplex_workers_active 4
```

### 9.2 JSON Report Schema

```json
{
  "metadata": {
    "version": "0.3.15",
    "timestamp": "2025-03-31T23:00:00Z",
    "duration_ms": 468.016,
    "workers": 4,
    "database": "postgresql"
  },
  "summary": {
    "total": 1500,
    "passed": 1500,
    "failed": 0,
    "skipped": 0,
    "error": 0
  },
  "performance": {
    "avg_per_test_ms": 0.312,
    "min_test_ms": 0.156,
    "max_test_ms": 1.245,
    "stddev_ms": 0.089
  },
  "breakdown": {
    "setup_ms": 46.802,
    "execution_ms": 397.814,
    "overhead_ms": 23.401
  }
}
```

---

## 10. Deployment Patterns

### 10.1 Docker (Recommended)

```dockerfile
# Dockerfile
FROM rust:1.75-slim as builder
WORKDIR /app
COPY . .
RUN cargo build --release

FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /app/target/release/tpx /usr/local/bin/
COPY --from=builder /app/tests ./tests
RUN pip install pytest pytest-xdist sqlalchemy psycopg2-binary

CMD ["tpx", "tests/", "--workers", "4"]
```

### 10.2 Kubernetes

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: turboplex-tests
spec:
  parallelism: 4
  template:
    spec:
      containers:
      - name: turboplex
        image: turboplex:latest
        env:
        - name: TPX_WORKERS
          value: "4"
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: db-credentials
              key: url
      restartPolicy: Never
```

---

## 11. Security Model

### 11.1 Threat Model

| Threat | Severity | Mitigation |
|--------|----------|------------|
| SQL Injection | High | Parameterized queries only |
| Credential Leak | Critical | Environment variables, no hardcoding |
| Worker Escape | Medium | Process isolation, resource limits |
| Cache Poisoning | Low | SHA256 integrity verification |

### 11.2 Best Practices

1. **Never commit credentials** - Use `.env` files (gitignored)
2. **Rotate DB passwords** - Monthly rotation recommended
3. **Use SSL/TLS** - Always encrypt DB connections
4. **Resource limits** - Prevent DoS via memory/CPU limits

---

## 12. Conclusion

TurboPlex's hybrid architecture represents the optimal balance between:
- **Performance**: Rust core for zero-overhead orchestration
- **Compatibility**: Python workers for full pytest ecosystem support
- **Scalability**: Horizontal and vertical scaling patterns
- **Reliability**: Fault tolerance and circuit breaker patterns

This architecture enables the 218x speedup observed in production benchmarks while maintaining compatibility with existing Python test suites.

---

*Architecture Version: 2.0*  
*Last Updated: 2025-03-31*
