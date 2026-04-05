# TurboPlex Operations Manual
## Production Deployment and Maintenance Guide

---

## 1. Deployment Overview

### 1.1 System Requirements

**Minimum Requirements:**
- CPU: 2 cores (4 recommended)
- RAM: 4GB (8GB recommended for large suites)
- Disk: 1GB free space
- OS: Linux/macOS/Windows with Docker support

**Recommended for Production:**
- CPU: 8+ cores
- RAM: 16GB
- SSD storage
- Dedicated test database server

### 1.2 Architecture Diagram

```
Production Deployment:
┌─────────────────────────────────────────────────────────────┐
│                     CI/CD Pipeline                          │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────┐    ┌──────────┐    ┌──────────────────────┐   │
│  │  GitHub  │───▶│  Actions │───▶│  TurboPlex Runner  │   │
│  │  Push    │    │  Trigger │    │  (Docker Container)│   │
│  └──────────┘    └──────────┘    └──────────┬──────────┘   │
│                                              │               │
│                               ┌──────────────┼──────────┐   │
│                               │              │          │   │
│                               ▼              ▼          ▼   │
│                         ┌────────┐     ┌────────┐   ┌────────┐│
│                         │Postgres│     │MariaDB │   │SQL Srv ││
│                         │ (Primary)     │(Backup)│   │(Legacy)││
│                         └────────┘     └────────┘   └────────┘│
│                               │              │          │   │
│                               └──────────────┴──────────┘   │
│                                              │               │
│                                              ▼               │
│                                    ┌─────────────────┐      │
│                                    │  Results JSON   │      │
│                                    │  → Prometheus   │      │
│                                    │  → Slack Alert  │      │
│                                    └─────────────────┘      │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Installation Procedures

### 2.1 Binary Installation

**Linux/macOS:**
```bash
# Download latest release
curl -L -o turboplex.tar.gz \
  https://github.com/turboplex/releases/latest/download/turboplex-$(uname -s)-$(uname -m).tar.gz

# Extract and install
tar -xzf turboplex.tar.gz
sudo mv turboplex /usr/local/bin/
sudo chmod +x /usr/local/bin/turboplex

# Verify installation
turboplex --version
```

**Windows (PowerShell):**
```powershell
# Download
Invoke-WebRequest -Uri "https://github.com/turboplex/releases/latest/download/turboplex-Windows-x86_64.zip" -OutFile "turboplex.zip"

# Extract
Expand-Archive -Path "turboplex.zip" -DestinationPath "C:\Program Files\TurboPlex"

# Add to PATH
[Environment]::SetEnvironmentVariable("Path", $env:Path + ";C:\Program Files\TurboPlex", "Machine")
```

### 2.2 Docker Installation

**Pull and Run:**
```bash
# Pull image
docker pull turboplex/turboplex:latest

# Run with PostgreSQL
docker run -d --name postgres \
  -e POSTGRES_PASSWORD=secret \
  -p 5432:5432 postgres:15

# Run TurboPlex
docker run --rm \
  -v $(pwd)/tests:/tests \
  -e DATABASE_URL=postgresql://postgres:secret@host.docker.internal:5432/test \
  turboplex/turboplex:latest \
  /tests --workers 4
```

### 2.4 Docker Compose (Dev + DB)

Levanta una DB MariaDB y un contenedor runner que ejecuta TurboPlex/Pytest contra el código montado (hot).

```bash
docker compose up -d db_tpx

# Pytest (benchmark DB real)
docker compose run --rm tpx_tests python -m pytest -q tests/benchmark/test_bench_0100.py

# TurboPlex (modo compat / pytest en sesión por worker)
docker compose run --rm tpx_tests cargo run --release --bin tpx -- --compat --path tests/benchmark/test_bench_0100.py

# TurboPlex (compat legacy / pytest por test)
docker compose run --rm tpx_tests cargo run --release --bin tpx -- --compat-per-test --path tests/benchmark/test_bench_0100.py

# TurboPlex puro (micro benchmark, sin DB)
docker compose run --rm -e TPX_RUNNER_LIGHT=1 -e TPX_WORKERS=8 tpx_tests cargo run --release --bin tpx -- --path .benchmarks/scripts/test_pure_1500.py

# Tip: para no reinstalar Python/apt en cada `run`, levanta el runner una vez y usa `exec`
docker compose up -d tpx_tests
docker compose exec tpx_tests cargo run --release --bin tpx -- --compat --path tests/benchmark/test_bench_0100.py
```

### 2.3 Source Installation

**Requirements:**
- Rust 1.70+
- Python 3.9+
- PostgreSQL/MariaDB client libraries

**Build Steps:**
```bash
# Clone repository
git clone https://github.com/turboplex/turboplex.git
cd turboplex

# Build Rust core
cargo build --release

# Install Python dependencies
pip install -r requirements.txt

# Verify
./target/release/tpx --version
```

---

## 3. Configuration Management

### 3.1 Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes | - | Database connection string |
| `TPX_WORKERS` | No | 4 | Number of parallel workers |
| `TPX_TIMEOUT` | No | 300 | Test timeout in seconds |
| `TPX_CACHE_DIR` | No | `.tpx_cache` | Cache directory path |
| `TPX_REPORT_FORMAT` | No | `json` | Report output format |
| `RUST_LOG` | No | `info` | Log level (error/warn/info/debug/trace) |

### 3.2 Configuration Files

**Global Config (`~/.tpx/config.toml`):**
```toml
[core]
workers = 4
timeout = 300
cache_dir = "~/.tpx/cache"

[database]
pool_size = 10
max_overflow = 20
pool_recycle = 3600

[reporting]
format = "json"
output_dir = "./reports"
```

**Project Config (`.tpx.toml` in project root):**
```toml
[core]
workers = 8  # Override for this project

[database]
url = "postgresql://localhost/test"

[paths]
test_dir = "tests/integration"
exclude = ["tests/legacy"]
```

### 3.3 Database Configuration

**PostgreSQL Connection String:**
```
postgresql://username:password@host:port/database?sslmode=require&connect_timeout=10
```

**MariaDB Connection String:**
```
mysql+pymysql://username:password@host:port/database?charset=utf8mb4
```

**SQL Server Connection String:**
```
mssql+pymssql://username:password@host:port/database?tds_version=7.4
```

---

## 4. Deployment Scenarios

### 4.1 GitHub Actions

```yaml
# .github/workflows/test.yml
name: TurboPlex Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_PASSWORD: test
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
        ports:
          - 5432:5432
    
    steps:
    - uses: actions/checkout@v3
    
    - name: Install TurboPlex
      run: |
        curl -L -o tpx.tar.gz \
          https://github.com/turboplex/releases/latest/download/tpx-Linux-x86_64.tar.gz
        tar -xzf tpx.tar.gz
        sudo mv tpx /usr/local/bin/
    
    - name: Run Tests
      env:
        DATABASE_URL: postgresql://postgres:test@localhost:5432/test
        TPX_WORKERS: 4
      run: |
        tpx tests/ --workers 4 --report-json results.json
    
    - name: Upload Results
      uses: actions/upload-artifact@v3
      with:
        name: test-results
        path: results.json
```

### 4.2 GitLab CI

```yaml
# .gitlab-ci.yml
stages:
  - test

turboplex_tests:
  stage: test
  image: turboplex/turboplex:latest
  services:
    - postgres:15
  variables:
    POSTGRES_PASSWORD: test
    DATABASE_URL: postgresql://postgres:test@postgres/test
    TPX_WORKERS: 4
  script:
    - tpx tests/ --workers 4 --report-json results.json
  artifacts:
    reports:
      junit: results.xml
    paths:
      - results.json
  coverage: '/TOTAL.*\s+(\d+%)$/'
```

### 4.3 Jenkins Pipeline

```groovy
// Jenkinsfile
pipeline {
    agent {
        docker {
            image 'turboplex/turboplex:latest'
            args '--network host'
        }
    }
    
    environment {
        DATABASE_URL = 'postgresql://postgres:test@localhost:5432/test'
        TPX_WORKERS = '4'
    }
    
    stages {
        stage('Setup Database') {
            steps {
                sh '''
                    docker run -d --name postgres \
                        -e POSTGRES_PASSWORD=test \
                        -p 5432:5432 postgres:15
                    sleep 5
                '''
            }
        }
        
        stage('Run Tests') {
            steps {
                sh 'tpx tests/ --workers 4 --report-json results.json'
            }
        }
        
        stage('Publish Results') {
            steps {
                junit 'results.xml'
                archiveArtifacts artifacts: 'results.json', fingerprint: true
            }
        }
    }
    
    post {
        always {
            sh 'docker rm -f postgres || true'
        }
    }
}
```

### 4.4 Kubernetes Deployment

```yaml
# k8s-deployment.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: turboplex-tests
spec:
  template:
    spec:
      containers:
      - name: turboplex
        image: turboplex/turboplex:latest
        command: ["tpx", "tests/", "--workers", "4"]
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: db-credentials
              key: url
        - name: TPX_WORKERS
          value: "4"
        resources:
          requests:
            memory: "2Gi"
            cpu: "2"
          limits:
            memory: "4Gi"
            cpu: "4"
        volumeMounts:
        - name: test-results
          mountPath: /results
      volumes:
      - name: test-results
        emptyDir: {}
      restartPolicy: Never
  backoffLimit: 2
```

---

## 5. Monitoring and Alerting

### 5.1 Prometheus Integration

**Metrics Endpoint (Planned Feature):**
```
# /metrics endpoint
# HELP turboplex_tests_total Total number of tests executed
# TYPE turboplex_tests_total counter
turboplex_tests_total{status="passed"} 1500
turboplex_tests_total{status="failed"} 2

# HELP turboplex_execution_duration_seconds Test execution duration
# TYPE turboplex_execution_duration_seconds histogram
turboplex_execution_duration_seconds_bucket{le="0.5"} 1498
turboplex_execution_duration_seconds_bucket{le="1.0"} 1500

# HELP turboplex_workers_active Current number of active workers
# TYPE turboplex_workers_active gauge
turboplex_workers_active 4
```

### 5.2 Grafana Dashboard

```json
{
  "dashboard": {
    "title": "TurboPlex Performance",
    "panels": [
      {
        "title": "Test Execution Time",
        "targets": [
          {
            "expr": "rate(turboplex_execution_duration_seconds_sum[5m]) / rate(turboplex_execution_duration_seconds_count[5m])"
          }
        ]
      },
      {
        "title": "Test Pass Rate",
        "targets": [
          {
            "expr": "turboplex_tests_total{status=\"passed\"} / turboplex_tests_total"
          }
        ]
      }
    ]
  }
}
```

### 5.3 Alerting Rules

**Prometheus AlertManager:**
```yaml
groups:
- name: turboplex
  rules:
  - alert: TurboPlexHighFailureRate
    expr: |
      (
        turboplex_tests_total{status="failed"}
        /
        turboplex_tests_total
      ) > 0.1
    for: 5m
    labels:
      severity: warning
    annotations:
      summary: "High test failure rate detected"
      
  - alert: TurboPlexSlowExecution
    expr: |
      turboplex_execution_duration_seconds > 30
    for: 10m
    labels:
      severity: warning
    annotations:
      summary: "Test execution is slower than expected"
```

---

## 6. Maintenance Procedures

### 6.1 Daily Checks

**Health Check Script:**
```bash
#!/bin/bash
# /usr/local/bin/tpx-health-check.sh

# Check TurboPlex version
tpx --version || exit 1

# Check database connectivity
tpx tests/health/ --workers 1 --timeout 10 || exit 1

# Check cache directory
if [ $(df -h ~/.tpx/cache | tail -1 | awk '{print $5}' | tr -d '%') -gt 90 ]; then
    echo "WARNING: Cache directory is >90% full"
fi

echo "Health check passed"
```

### 6.2 Weekly Maintenance

**Cache Cleanup:**
```bash
# Remove cache entries older than 7 days
find ~/.tpx/cache -type f -mtime +7 -delete

# Vacuum database connections
psql $DATABASE_URL -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle' AND state_change < NOW() - INTERVAL '1 hour';"
```

**Log Rotation:**
```bash
# Rotate logs
logrotate /etc/logrotate.d/turboplex

# Compress old logs
gzip /var/log/turboplex/*.log.1
```

### 6.3 Monthly Review

**Performance Baseline:**
```bash
# Run benchmark suite
tpx tests/benchmark/ --workers 4 --report-json baseline-$(date +%Y%m).json

# Compare with previous month
# If regression >10%, investigate
```

**Dependency Updates:**
```bash
# Update Rust toolchain
rustup update

# Rebuild with latest dependencies
cargo build --release

# Run validation tests
tpx tests/ --workers 4
```

---

## 7. Troubleshooting Guide

### 7.1 Common Issues

#### Issue: Tests Hang Indefinitely

**Symptoms:**
- Process runs forever
- No output
- High CPU usage

**Diagnosis:**
```bash
# Check for deadlocks
ps aux | grep tpx
strace -p <PID>

# Check database connections
psql $DATABASE_URL -c "SELECT * FROM pg_stat_activity WHERE state = 'active';"
```

**Solutions:**
1. Reduce worker count: `TPX_WORKERS=2`
2. Enable connection pool pre-ping
3. Check for infinite loops in tests
4. Increase timeout: `--timeout 600`

#### Issue: Out of Memory

**Symptoms:**
- OOM killer invoked
- Container restarts
- "Cannot allocate memory" errors

**Diagnosis:**
```bash
# Check memory usage
free -h
docker stats

# Check for memory leaks
valgrind --tool=massif ./target/release/tpx tests/
```

**Solutions:**
1. Reduce worker count
2. Add container memory limits
3. Enable swap (emergency only)
4. Check for test memory leaks

#### Issue: Database Connection Errors

**Symptoms:**
- "Connection refused"
- "Too many connections"
- SSL/TLS errors

**Diagnosis:**
```bash
# Test connectivity
psql $DATABASE_URL -c "SELECT 1;"

# Check connection count
psql $DATABASE_URL -c "SELECT count(*) FROM pg_stat_activity;"
```

**Solutions:**
1. Verify database is running
2. Increase `max_connections`
3. Tune connection pool size
4. Check SSL certificates

### 7.2 Debug Mode

**Enable Verbose Logging:**
```bash
export RUST_LOG=debug
export RUST_BACKTRACE=1
tpx tests/ --verbose 2>&1 | tee debug.log
```

**Analyze Logs:**
```bash
# Find errors
grep -i error debug.log

# Find slow queries
grep "duration" debug.log | sort -k3 -n | tail -10
```

### 7.3 Recovery Procedures

**Complete Reset:**
```bash
# Clear cache
rm -rf ~/.tpx/cache/*

# Reset database
psql $DATABASE_URL -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

# Restart from scratch
cargo clean
cargo build --release
```

---

## 8. Security Hardening

### 8.1 Database Security

**Connection Encryption:**
```bash
# Require SSL
export DATABASE_URL="postgresql://user:pass@host/db?sslmode=require"

# Verify SSL certificate
export DATABASE_URL="postgresql://user:pass@host/db?sslmode=verify-full&sslrootcert=/path/to/ca.crt"
```

**Credential Management:**
```bash
# Use Docker secrets
docker secret create db_password <password_file>

# Use Kubernetes secrets
kubectl create secret generic db-credentials \
  --from-literal=url="postgresql://user:pass@host/db"
```

### 8.2 Container Security

**Docker Security Options:**
```bash
docker run --rm \
  --read-only \
  --security-opt=no-new-privileges:true \
  --cap-drop=ALL \
  --cap-add=DAC_OVERRIDE \
  -v $(pwd)/tests:/tests:ro \
  turboplex/turboplex:latest \
  /tests --workers 4
```

### 8.3 Network Security

**Firewall Rules:**
```bash
# Only allow database connections from test runners
iptables -A INPUT -p tcp --dport 5432 -s 10.0.0.0/8 -j ACCEPT
iptables -A INPUT -p tcp --dport 5432 -j DROP
```

---

## 9. Backup and Recovery

### 9.1 Cache Backup

**Backup Script:**
```bash
#!/bin/bash
# /usr/local/bin/backup-tpx-cache.sh

BACKUP_DIR="/backup/turboplex/$(date +%Y%m%d)"
mkdir -p $BACKUP_DIR

tar -czf $BACKUP_DIR/cache.tar.gz ~/.tpx/cache/
rsync -av $BACKUP_DIR/ remote-backup-server:/backups/turboplex/

# Keep only last 7 days
find /backup/turboplex -type d -mtime +7 -exec rm -rf {} \;
```

### 9.2 Disaster Recovery

**Recovery Procedure:**
```bash
# 1. Restore cache from backup
tar -xzf /backup/turboplex/20250331/cache.tar.gz -C ~/

# 2. Verify installation
tpx --version

# 3. Run validation tests
tpx tests/smoke/ --workers 1

# 4. Resume normal operations
tpx tests/ --workers 4
```

---

## 10. Scaling Guidelines

### 10.1 Vertical Scaling

| Test Count | Workers | RAM | CPU | Database |
|------------|---------|-----|-----|----------|
| 100 | 2 | 2GB | 2 cores | Shared |
| 500 | 4 | 4GB | 4 cores | Shared |
| 1,000 | 8 | 8GB | 8 cores | Dedicated |
| 5,000 | 16 | 16GB | 16 cores | Cluster |
| 10,000 | 32 | 32GB | 32 cores | Cluster |

### 10.2 Horizontal Scaling

**Distributed Test Execution (Roadmap):**
```yaml
# docker-compose.distributed.yml
version: '3.8'
services:
  coordinator:
    image: turboplex/turboplex:coordinator
    environment:
      - MODE=coordinator
      - WORKER_COUNT=4
  
  worker-1:
    image: turboplex/turboplex:worker
    environment:
      - MODE=worker
      - COORDINATOR=coordinator:8080
  
  worker-2:
    image: turboplex/turboplex:worker
    environment:
      - MODE=worker
      - COORDINATOR=coordinator:8080
  
  # ... more workers
```

---

## 11. Support and Escalation

### 11.1 Support Tiers

| Tier | Response Time | Contact | Cost |
|------|---------------|---------|------|
| Community | Best effort | GitHub Issues | Free |
| Professional | 24 hours | support@turboplex.io | $500/mo |
| Enterprise | 4 hours | dedicated@turboplex.io | $2,000/mo |
| Emergency | 1 hour | emergency@turboplex.io | $500/incident |

### 11.2 Information to Include in Support Requests

**Required:**
- TurboPlex version (`tpx --version`)
- Operating system and version
- Database type and version
- Test count and approximate duration
- Error messages (full stack trace)
- Configuration files (sanitized)

**Optional but helpful:**
- Debug logs (`RUST_LOG=debug`)
- Performance profiles
- Database query logs
- Container/resource specifications

---

## 12. Conclusion

This operations manual provides comprehensive guidance for deploying, monitoring, and maintaining TurboPlex in production environments. Regular review of performance metrics and adherence to best practices will ensure optimal test execution performance.

**Key Takeaways:**
- Start with 4 workers and scale based on metrics
- Monitor database connection pools closely
- Use Docker for consistent environments
- Enable monitoring early for proactive issue detection

---

*Operations Manual Version: 1.0*  
*Last Updated: 2025-03-31*  
*Next Review: 2025-06-30*
