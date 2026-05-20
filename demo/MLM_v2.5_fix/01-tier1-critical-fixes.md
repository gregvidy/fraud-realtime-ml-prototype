# Tier 1: Critical Fixes — Month 1 (June 2026)

> **High Impact, Low Complexity — Stop the bleeding**

## Team Assignment — Tier 1

| Change | Owner | Support | Calendar | Notes |
|--------|-------|---------|----------|-------|
| #1 Remove sys.exit() | Engineer B | Lead (review) | Week 1-2 | Onboarding warmup — simple Python change |
| #2 Pre-load model cache | Engineer B | Lead (walkthrough) | Week 1-2 | Teaches caching pattern + TTLCache |
| #3 Two Windows Services | Engineer A (C#) + Engineer B (Python) | Lead (architecture) | Week 3-4 | Most complex Tier 1 item — needs coordination |
| #4 Waitress thread pool | Engineer B | Lead (review) | Week 3 | pip install + config change |
| #5 ThreadPoolExecutor | Engineer B | Lead (review) | Week 3-4 | ~10 lines, pairs well with #4 |

**Lead's role in Month 1**: Codebase walkthrough (Week 1), architecture guidance, code review, testing gate validation. No direct coding — engineers build confidence by owning all changes.

**Engineer A in Week 1-2**: Learning the C# side — `PythonService.cs`, `ScoringService.cs`, Windows Service config, MSI structure. Shadows Engineer B on Python changes to understand the Flask app.

---

## Constraints

- **Must stay Windows-native**: No Docker, no Redis, no Gunicorn (all Unix-dependent)
- **Must preserve C# ↔ Python HTTP interface**: `PythonService.cs` dual-URL pattern stays
- **Must preserve Angular frontend**: Add pages, don't rewrite
- **Must preserve DB schema**: `TrainingModelRun`, `Feature`, `Parameter` tables stay
- **Must preserve MSI installer pattern**: Python service runs as Windows Service

---

## Change #1: Remove `sys.exit()` Calls

| Attribute | Detail |
|-----------|--------|
| **Impact** | 🔴 Critical |
| **Complexity** | Low |
| **Effort** | 1 day |
| **Owner** | Engineer B |
| **Calendar** | Month 1, Week 1-2 |
| **Prerequisite** | Lead walks through `MachineLearning.py` error handling patterns |

### Problem

5 occurrences in `MachineLearning.py` (`_analyse`, `_train`, `_validate`, `_distplot`).
Since these run in `multiprocessing.Process` children, `sys.exit()` kills the child but returns nothing to the parent — the C# side hangs until timeout.

### Fix

Replace with `return jsonify({"error": msg}), 400`.
Proper error return fixes both the crash AND the silent hang.

---

## Change #2: Pre-load Model into Memory at Startup

| Attribute | Detail |
|-----------|--------|
| **Impact** | 🟠 High |
| **Complexity** | Low |
| **Effort** | 1 day |
| **Owner** | Engineer B |
| **Calendar** | Month 1, Week 1-2 |
| **Prerequisite** | Lead explains model BLOB storage, `get_model_info()` flow, and DB schema |

### Problem

Current: `@cached(TTLCache(128, 864000))` on `get_model_info()` → first score after restart or TTL expiry hits DB for BLOB → 2-5s latency spike.

### Fix

Load all active models at startup via `/` health check trigger or `WarmUpService`. Keep TTLCache as backup.

---

## Change #3: Two Windows Services (Scoring + Training)

| Attribute | Detail |
|-----------|--------|
| **Impact** | 🔴 Critical |
| **Complexity** | Medium |
| **Effort** | 2-3 days |
| **Owner** | Engineer A (Windows Service config, MSI) + Engineer B (Python `ML_MODE` logic) |
| **Calendar** | Month 1, Week 3-4 |
| **Prerequisite** | Lead explains dual-URL pattern in `PythonService.cs` and MSI installer structure |

### Problem

Current: single `MachineLearning.py` serves everything. A long-running training job blocks scoring.

### Fix

Add `ML_MODE` env var:

- When `scoring` → register only `/score`, `/health`, `/clear`
- When `training` → register only `/train`, `/validate`, `/analyse`, `/tsne`, `/distplot`

Install two Windows Services from same MSI. C# side already supports `"scoringUrl;trainingUrl"` split in `PythonService.cs`.

---

## Change #4: Increase Gevent/ZeroMQ Worker Count

| Attribute | Detail |
|-----------|--------|
| **Impact** | 🟠 High |
| **Complexity** | Low |
| **Effort** | 0.5 day |
| **Owner** | Engineer B |
| **Calendar** | Month 1, Week 3 |
| **Prerequisite** | None — straightforward pip install + config |

### Problem

ZeroMQ path already has 100 scoring workers + 5 training workers. Flask/Gevent path has **1 thread**.

### Fix

Switch to Waitress (Windows-compatible WSGI with thread pool) or increase Gevent worker pool.

```bash
waitress-serve --threads=8 --port=5555 MachineLearning:app
```

---

## Change #5: ThreadPoolExecutor for `predict_proba`

| Attribute | Detail |
|-----------|--------|
| **Impact** | 🟠 High |
| **Complexity** | Low |
| **Effort** | 0.5 day |
| **Owner** | Engineer B |
| **Calendar** | Month 1, Week 3-4 |
| **Prerequisite** | None — pairs naturally with #4 (both affect concurrency) |

### Problem

LightGBM releases GIL, but scoring still blocks the event loop.

### Fix

Wrap `predict_proba` in `concurrent.futures.ThreadPoolExecutor` so scoring doesn't block the event loop while model inference runs. ~10 lines of code.

---

## Tier 1 Outcome

| Metric | Before (v2.4) | After Tier 1 |
|--------|---------------|-------------|
| P50 latency | > 500ms | ~150-200ms |
| Throughput | < 50 RPS | ~150-200 RPS |
| Training impact | Service down | Zero |
| Crash cascade | Yes | No |

**Total effort: ~5-6 days (spread across 4 weeks to include onboarding)**

**Testing Gate (End of Month 1)**:
- Lead runs load test: confirm <200ms P50 latency
- Verify no crash cascade under concurrent training + scoring
- Validate clean Windows Service restart (both services)
- Regression: all existing API contracts still pass
