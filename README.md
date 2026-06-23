# hpchpcagent

**Interactive HPC AI Agent** — a terminal TUI that helps you manage SLURM clusters, diagnose jobs, check nodes and quotas, search docs, and more. Supports 10+ LLM providers.

```text
   ██████╗  █████╗ ██████╗ ██╗     ██╗ ██████╗
   ██╔════╝ ██╔══██╗██╔══██╗██║     ██║██╔════╝
   ██║  ███╗███████║██████╔╝██║     ██║██║
   ██║   ██║██╔══██║██╔══██╗██║     ██║██║
   ╚██████╔╝██║  ██║██║  ██║███████╗██║╚██████╗
   ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝ ╚═════╝
   ▸ openai:gpt-4o
```

## Quickstart

```bash
# Install
pip install "hpchpcagent[full]"

# Launch the TUI
hpchpcagent --backend openai --model gpt-4o --api-key "$OPENAI_API_KEY"
```

Or with any supported provider:

```bash
hpchpcagent --backend groq --model mixtral-8x7b-32768
hpchpcagent --backend deepseek --model deepseek-chat
hpchpcagent --backend openrouter --model anthropic/claude-sonnet-4
hpchpcagent --backend custom --api-base-url https://my-llm.example.com/v1 --model my-model
```

## Features

- **Terminal TUI** — streaming responses, thinking animations, markdown rendering, tool call status
- **SLURM tools** — job status/ETA/extension, node hardware, GPU utilization, quotas, disk usage, permissions, QOS, account management
- **Provider-agnostic** — openai, groq, openrouter, deepseek, mistral, xai, github, opencode, codex, claude, agy, or any OpenAI-compatible API
- **Web search & fetch** — DuckDuckGo + trafilatura for external context
- **Skills system** — load documentation from a `skills.md` file as discoverable tools

## Programmatic use

```python
from hpchpcagent import HPCAgent

agent = HPCAgent(
    backend="openai",
    api_key="...",
    model="gpt-4o",
    system_prompt="You are an HPC cluster assistant.",
    docs_base_path="/path/to/user-guide",
)
agent.add_doc_tool("read_slurm_docs", "SLURM documentation", "slurm/main.md")
agent.run()
```

## Layers

| Layer | Path | Depends on |
|-------|------|------------|
| **core** | `hpchpcagent.core` | zero HPC deps |
| **hpc** | `hpchpcagent.hpc` | core |
| **agent** | `hpchpcagent.agent` | core + hpc |

## Installation

```bash
pip install hpchpcagent
pip install "hpchpcagent[full]"    # + web search
pip install "hpchpcagent[dev]"     # + dev tooling
```
