# Siavash — Wallex Spot Trading Agent

Spot-only algorithmic trading agent for the Wallex challenge. Quant signals, an ML scorer and an
LLM reviewer propose trades; a hard risk layer that the AI cannot bypass sizes and vetoes them.

The project plan and single source of truth is [WALLEX_AGENT_PLAN.md](WALLEX_AGENT_PLAN.md);
task status lives in [PROGRESS.md](PROGRESS.md).

## Quick start (development)

```bash
make install   # pip install -e ".[dev]"
make lint
make test
```
