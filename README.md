# hpchpcagent

Agnostic HPC AI Agent framework. Multi-backend LLM client, SLURM cluster tools, terminal UI, and web search — all separable by layer.

## Quickstart

```python
from hpchpcagent.agent import HPCAgent

agent = HPCAgent(
    system_prompt="You are a helpful HPC assistant.",
    api_key="...",
    model="gpt-4o",
)

agent.run()
```

## Layers

- `hpchpcagent.core` — Framework layer (LLM clients, UI, tools registry, web). Zero HPC dependencies.
- `hpchpcagent.hpc` — SLURM-specific tooling (node hardware, job prediction, accounts, disk). Depends on `core`.
- `hpchpcagent.agent` — Batteries-included `HPCAgent` class wiring everything together.

## Installation

```bash
pip install hpchpcagent
# With optional backends:
pip install "hpchpcagent[full]"
```
