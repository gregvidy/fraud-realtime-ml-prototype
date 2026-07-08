# MLM v2.5 Fixation — Index

> **Windows-native, backward-compatible upgrades to the CAFS ML Platform.**
> Fixes critical latency, crash, and observability gaps in the current `cafs-machinelearning` product.

---

## Who Should Read What

This documentation serves **two audiences** with different reading paths.

### Product / Leadership

Start here → skim for scope, timelines, and trade-offs.

| Read | What You'll Get |
|------|----------------|
| **This page** (scroll down) | Quick performance targets and effort overview |
| [00. Summary & Delivery Plan](00-summary-and-delivery-plan.md) | 6-month calendar plan, team roles, testing gates, DB schema, dependencies |
| [04. Option Comparison](04-option-comparison.md) | Key architecture decisions (FastAPI vs ONNX, etc.) with recommendations |
| [05. Caveats & Risks](05-caveats-and-risks.md) | Cross-cutting risks table at the bottom — skip per-tier detail |

### Engineering

Read **tier-by-tier**, and **always cross-reference the caveats** for the item you're implementing.

| Implementing… | Read the spec | Then read the caveats | Key decision |
|---------------|--------------|----------------------|--------------|
| **#1** Remove `sys.exit()` | [01. Tier 1](01-tier1-critical-fixes.md) → Change #1 | [05. Caveats](05-caveats-and-risks.md) → Tier 1 → `sys.exit() Removal` | — |
| **#2** Pre-load model cache | [01. Tier 1](01-tier1-critical-fixes.md) → Change #2 | — (low risk) | — |
| **#3** Split scoring + training services | [01. Tier 1](01-tier1-critical-fixes.md) → Change #3 | [05. Caveats](05-caveats-and-risks.md) → Tier 1 → `Service Split` | — |
| **#4** Increase worker count | [01. Tier 1](01-tier1-critical-fixes.md) → Change #4 | — | [04. Options](04-option-comparison.md) → Decision 2: Waitress vs Gevent |
| **#5** ThreadPoolExecutor | [01. Tier 1](01-tier1-critical-fixes.md) → Change #5 | — (low risk) | — |
| **#6** Flask → FastAPI | [02. Tier 2](02-tier2-performance-improvements.md) → Change #6 | — | [04. Options](04-option-comparison.md) → Decision 1: FastAPI vs ONNX |
| **#7** ONNX Runtime scoring | [02. Tier 2](02-tier2-performance-improvements.md) → Change #7 | [05. Caveats](05-caveats-and-risks.md) → Tier 2 → `ONNX Export` | [04. Options](04-option-comparison.md) → Decision 1: FastAPI vs ONNX |
| **#8** Model hot-reload | [02. Tier 2](02-tier2-performance-improvements.md) → Change #8 | — | [04. Options](04-option-comparison.md) → Decision 3: FileSystemWatcher vs TTLCache |
| **#9** DataHub pagination | [02. Tier 2](02-tier2-performance-improvements.md) → Change #9 | [05. Caveats](05-caveats-and-risks.md) → Tier 2 → `DataHub Paginated Extraction` | — |
| **#10** Isotonic calibration in C# | [02. Tier 2](02-tier2-performance-improvements.md) → Change #10 | — (low risk) | — |
| **#11** Large dataset training | [02. Tier 2](02-tier2-performance-improvements.md) → Change #11 | [05. Caveats](05-caveats-and-risks.md) → Tier 2 → `Large Dataset Training` | [04. Options](04-option-comparison.md) → Decision 6: Chunked vs Ray vs Dask |
| **#12** XGBoost support | [02. Tier 2](02-tier2-performance-improvements.md) → Change #12 | [05. Caveats](05-caveats-and-risks.md) → Tier 2 → `XGBoost Algorithm Support` | [04. Options](04-option-comparison.md) → Decision 7: XGBoost vs LightGBM |
| **#13** GBM hyperparameters | [02. Tier 2](02-tier2-performance-improvements.md) → Change #13 | [05. Caveats](05-caveats-and-risks.md) → Tier 2 → `Additional GBM Hyperparameters` | — |
| **#14** Backtesting | [02. Tier 2](02-tier2-performance-improvements.md) → Change #14 | [05. Caveats](05-caveats-and-risks.md) → Tier 3 → `Backtesting` | [04. Options](04-option-comparison.md) → Decision 9: Criteria vs CSV |
| **Monitoring** (Components 1-7) | [03. Tier 3](03-tier3-model-monitoring.md) → Components 1-7 | [05. Caveats](05-caveats-and-risks.md) → Tier 3 (Score logging, PSI, Angular) | [04. Options](04-option-comparison.md) → Decision 4 + Decision 5 |
| **Permutation Importance** | [03. Tier 3](03-tier3-model-monitoring.md) → Component 8 | [05. Caveats](05-caveats-and-risks.md) → Tier 3 → `Permutation Importance` | [04. Options](04-option-comparison.md) → Decision 8: When to compute |
| **Explainability** (SHAP + PDP) | [03. Tier 3](03-tier3-model-monitoring.md) → Component 9 | [05. Caveats](05-caveats-and-risks.md) → Tier 3 → `Model Explainability` | [04. Options](04-option-comparison.md) → Decision 10 + Decision 11 |

> **Rule of thumb for engineers**: Before you start coding any change, open the spec page **and** the caveats page side-by-side. If the item has an architecture decision, read the option comparison first.

---

## Document Map

```
MLM v2.5 Fixation/
│
├── 00. Summary & Delivery Plan                ← START HERE (Product)
│     6-month calendar, team roles, testing gates, DB schema
│
├── 01. Tier 1: Critical Fixes (Month 1)       ← Engineering
│     5 changes — sys.exit, model preload, service split,
│     worker count, ThreadPoolExecutor
│
├── 02. Tier 2a: Scoring Path (Month 2)        ← Engineering
│     5 changes — FastAPI/ONNX, hot-reload, pagination,
│     calibration
│
│   Tier 2b: New Capabilities (Month 3)        ← Engineering
│     4 changes — large dataset, XGBoost, hyperparams,
│     backtesting
│
├── 03. Tier 3a: Monitoring (Month 4)          ← Engineering
│     7 components — DB tables, services, API, Angular UI,
│     PSI alerts
│
│   Tier 3b: Explainability (Month 5)          ← Engineering
│     2 components — permutation importance, SHAP/PDP
│
├── 04. Option Comparison: v2.5 Architecture   ← READ BEFORE DECIDING (Both)
│     11 architecture decisions with side-by-side tables
│     and recommendations
│
└── 05. Caveats and Risks                      ← READ WHILE IMPLEMENTING (Engineering)
      Per-tier risks + cross-cutting risks with mitigations
```

---

## Quick Reference

| Metric | v2.4 (Current) | After Tier 1 | After Tier 2 (ONNX) | After Tier 3 |
|--------|---------------|-------------|---------------------|-------------|
| P50 Latency | > 500ms | ~150-200ms | **~5-10ms** | ~6-12ms |
| Throughput | < 50 RPS | ~150-200 RPS | ~500+ RPS | ~450+ RPS |
| Crash Cascade | Yes | No | No | No |
| Training Impact | Service down | Zero | Zero | Zero |
| Max Training Rows | ~2-3M | ~2-3M | **50M+** | 50M+ |
| Algorithms | LightGBM, RF, GBM | Same | + **XGBoost** | Same |
| Monitoring | None | None | None | ✅ PSI, volume, latency |
| Explainability | None | None | None | ✅ PDP + SHAP + Permutation |

| Tier | Scope | Calendar | Testing Gate |
|------|-------|----------|-------------|
| Onboarding + Tier 1 | Critical fixes (5 changes) | Month 1 (June) | ✅ <200ms, no crash |
| Tier 2a | Scoring path (ONNX + hot-reload) | Month 2 (July) | ✅ <10ms ONNX |
| Tier 2b | Capabilities (large data, XGB, backtest) | Month 3 (August) | ✅ 10M+ rows, backtest |
| Tier 3a | Monitoring (logging, PSI, dashboard) | Month 4 (September) | ✅ PSI alerts |
| Tier 3b | Explainability (SHAP, PDP, permutation) | Month 5 (October) | ✅ SHAP <5s |
| Hardening + UAT | Regression, load test, release | Month 6 (Nov–Dec) | 🚀 Release |
