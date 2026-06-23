<div align="center">

```
H   H PPPP   CCC          A    GGG  EEEEE N   N TTTTT
H   H P   P C   C        A A  G     E     NN  N   T  
HHHHH PPPP  C           AAAAA G GG  EEEE  N N N   T  
H   H P     C   C       A   A G   G E     N  NN   T  
H   H P      CCC        A   A  GGG  EEEEE N   N   T  
```

</div>

# hpcagent

Talk to your SLURM cluster in plain English. An interactive TUI agent that checks nodes, diagnoses jobs, predicts wait times, fixes permissions, searches docs, and more. Powered by the LLM of your choice.

[![CI](https://github.com/PursuitOfDataScience/hpcagent/actions/workflows/ci.yml/badge.svg)](https://github.com/PursuitOfDataScience/hpcagent/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](#-license)
[![Status: beta](https://img.shields.io/badge/status-beta-orange.svg)](#-status)

---

## Quickstart

```bash
pip install "hpcagent[full]"

# OpenAI
hpcagent --backend openai --model gpt-4o --api-key "$OPENAI_API_KEY"

# or Groq (free tier)
hpcagent --backend groq --model mixtral-8x7b-32768
```

Type your question — "show me all GPU jobs", "why is job 1234 pending?", "check my quota" — and the agent runs SLURM commands, reads docs, or searches the web to answer.

## Supported providers

| Provider | `--backend` | Auth |
|----------|-------------|------|
| OpenAI | `openai` | `OPENAI_API_KEY` |
| Groq | `groq` | `GROQ_API_KEY` |
| OpenRouter | `openrouter` | `OPENROUTER_API_KEY` |
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` |
| Mistral | `mistral` | `MISTRAL_API_KEY` |
| xAI | `xai` | `XAI_API_KEY` |
| GitHub Models | `github` | `GITHUB_TOKEN` |
| OpenCode | `opencode` | `OPENCODE_API_KEY` |
| Codex CLI | `codex` | _(none — uses `codex` binary)_ |
| Claude CLI | `claude` | _(none — uses `claude` binary)_ |
| agy | `agy` | _(none — uses `agy` binary)_ |
| Any OpenAI-compatible API | `custom` | set `--api-base-url` |

## Documentation

```bash
# Load a skills file to give the agent access to your cluster docs
hpcagent --backend openai --model gpt-4o --docs-base-path /path/to/docs
```

Then in the TUI, ask "how do I request GPUs?" and the agent reads the relevant doc.

## Install from source

```bash
git clone https://github.com/PursuitOfDataScience/hpcagent.git
cd hpcagent
pip install -e ".[full]"
hpcagent --backend openai --model gpt-4o
```
