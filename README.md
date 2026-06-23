<div align="center">

```
██╗  ██╗██████╗  ██████╗     █████╗  ██████╗ ███████╗███╗   ██╗████████╗
██║  ██║██╔══██╗██╔════╝    ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝
███████║██████╔╝██║         ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║   
██╔══██║██╔═══╝ ██║         ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║   
██║  ██║██║     ╚██████╗    ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║   
╚═╝  ╚═╝╚═╝      ╚═════╝    ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   
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

| Provider | `--backend` | Auth | Type |
|----------|-------------|------|------|
| OpenAI | `openai` | `OPENAI_API_KEY` | API (Curated Tools) |
| Google Gemini | `gemini` | `GEMINI_API_KEY` | API (Curated Tools) |
| Groq | `groq` | `GROQ_API_KEY` | API (Curated Tools) |
| OpenRouter | `openrouter` | `OPENROUTER_API_KEY` | API (Curated Tools) |
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` | API (Curated Tools) |
| Mistral | `mistral` | `MISTRAL_API_KEY` | API (Curated Tools) |
| xAI | `xai` | `XAI_API_KEY` | API (Curated Tools) |
| Together | `together` | `TOGETHER_API_KEY` | API (Curated Tools) |
| OpenCode | `opencode` | `OPENCODE_API_KEY` | API (Curated Tools) |
| Any OpenAI-compatible API | `custom` | set `--api-base-url` | API (Curated Tools) |
| Codex CLI | `codex` | _(uses `codex` binary)_ | CLI (Bring-your-own-tools) |
| Claude CLI | `claude` | _(uses `claude` binary)_ | CLI (Bring-your-own-tools) |

> [!NOTE]
> **API vs. CLI backends:** API backends use the python-based curated HPC tools registered in `hpcagent` (e.g. permission checks, job wait time estimates, custom quotas). CLI backends run external CLI binaries directly, executing bash commands and using their own tool systems without the python-based custom wrappers.

## Documentation

Point the agent at your cluster docs — either a **local folder** of markdown, or a
**docs-site URL** (a whole user guide). A URL is crawled once into a local cache
(sitemap first, then a focused crawl) so the agent reads it instantly and offline:

```bash
# Local folder of docs/skills
hpcagent --backend openai --model gpt-4o --docs-base-path /path/to/docs

# Or mirror an online user guide into a local cache
hpcagent --backend openai --model gpt-4o --docs-url https://docs.your-cluster.edu/user-guide/
```

You can also set this in the setup wizard's *Docs* step (paste a path or a URL).
In the TUI, `/docs` shows what's loaded, `/docs add` adds a source, and `/docs sync`
re-crawls the URL when the docs change. Then ask "how do I request GPUs?" and the
agent reads the relevant page. (Requires the `full` extra for `trafilatura`.)

## Install from source

```bash
git clone https://github.com/PursuitOfDataScience/hpcagent.git
cd hpcagent
pip install -e ".[full]"
hpcagent --backend openai --model gpt-4o
```
