<div align="center">

```
██╗  ██╗██████╗  ██████╗     █████╗  ██████╗ ███████╗███╗   ██╗████████╗
██║  ██║██╔══██╗██╔════╝    ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝
███████║██████╔╝██║         ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║   
██╔══██║██╔═══╝ ██║         ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║   
██║  ██║██║     ╚██████╗    ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║   
╚═╝  ╚═╝╚═╝      ╚═════╝    ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   
```

**Talk to your SLURM cluster in plain English.**

A colorful terminal agent that checks nodes, diagnoses jobs, predicts wait times,
fixes permissions, and reads your cluster's own docs — no bash incantations required.

[![CI](https://github.com/PursuitOfDataScience/hpcagent/actions/workflows/ci.yml/badge.svg)](https://github.com/PursuitOfDataScience/hpcagent/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](#license)
[![Status: beta](https://img.shields.io/badge/status-beta-orange.svg)](#status)

</div>

---

## See it

```text
❯ why is job 1837465 pending?

● Job 1837465 is pending in the gpu partition. The reason is QOSMaxGRES —
  your account has hit its concurrent GPU limit.

  ⚡ predict_pending_job_wait   ✓
  ⚡ read_slurm_partitions       ✓

  • Estimated start: ~3h 20m (3 jobs ahead in the same QOS)
  • Free it sooner: cancel one of your two running gpu jobs, or submit to
    the `gpu-shared` partition which currently has 6 idle A100s.

  Want me to show your running gpu jobs?
```

> The agent streams its answer with live markdown, shows each tool as it runs,
> and asks before anything that changes state. Type `/` any time for commands.

## Why hpcagent

- 🗣️ **Plain-English ops** — "show my pending jobs", "who's hogging node midway3-0042", "extend job 1837465 by 2 hours".
- 📚 **Knows *your* cluster** — point it at your docs folder **or a docs-site URL** and it mirrors the whole user guide locally, then cites the right page.
- 🛟 **Safe by default** — mutating actions (chmod, chgrp, `scontrol update`) show a preview and need confirmation; secrets are stored `0600`, session-only unless you opt in.
- 🔌 **Bring any model** — it auto-detects an installed `claude`/`codex`, or use any API provider; switch and search models live with `/model`.
- 🎨 **A real TUI** — gradient banner, streaming answers, live tool status, searchable menus, persistent history.

## Quickstart

```bash
pip install "hpcagent[full]"

hpcagent          # first run walks you through setup
```

That's it. On first launch a short wizard helps you pick a model, point at your
docs, and start asking questions. If you already have the `claude` or `codex` CLI
installed, hpcagent uses it automatically — **zero config, no API key needed.**

## Living in the TUI

Type `/` to open the command menu (arrow keys to choose, Enter to run):

| Command | What it does |
|---------|--------------|
| `/model` | Browse, **search**, and switch models (type to filter a long list) |
| `/effort` | Set reasoning effort (low / medium / high) |
| `/docs` | Show, add, or `/docs sync` your cluster documentation |
| `/tools` | List the HPC tools the agent can call |
| `/config` | Re-run the setup wizard |
| `/keys` | See where your API key comes from (or clear it) |
| `/copy` · `/save` | Copy the last answer · save the transcript |
| `/clear` · `/retry` · `/help` · `/exit` | …and the usual essentials |

Don't know a model's exact name? Just open `/model` and type `fast`, `reasoning`,
`gemini`, or part of a name — the picker filters as you type.

## Teach it your cluster's docs

Point hpcagent at a **local folder** of markdown, or at a **docs-site URL** — a whole
user guide with many pages. A URL is crawled once (sitemap, then the site's own nav
links) into a local cache, so the agent reads it instantly and offline:

```bash
# A local folder…
hpcagent --docs-base-path /path/to/cluster-docs

# …or mirror an online user guide (the whole site, not one page)
hpcagent --docs-url https://docs.rcc.uchicago.edu/
```

You can also paste a path or URL in the wizard's **Docs** step. Inside the TUI,
`/docs` shows what's loaded, `/docs add` adds a source, and `/docs sync` re-crawls
when the docs change. Then ask *"how do I request a GPU node?"* and the agent reads
the relevant page and answers with specifics. *(URL mirroring uses the `full` extra.)*

## 🎬 Record an animated demo

A ready-made [vhs](https://github.com/charmbracelet/vhs) script lives at
[`assets/demo.tape`](assets/demo.tape). With `vhs` installed:

```bash
vhs assets/demo.tape        # renders assets/demo.gif
```

Then drop the GIF at the top of this README:

```markdown
<p align="center"><img src="assets/demo.gif" width="760" alt="hpcagent demo"></p>
```

`vhs` scripts the whole session deterministically (no live screen recording), so the
demo is reproducible and easy to tweak.

<details>
<summary><strong>Models & providers</strong> (click to expand)</summary>

hpcagent talks to any OpenAI-compatible API, plus the Claude and Codex CLIs. It does
**not** hard-code model names — use `/model` (or `--list-models <provider>`) to see
what each provider currently offers and pick the latest.

| Provider | `--backend` | Auth |
|----------|-------------|------|
| OpenAI | `openai` | `OPENAI_API_KEY` |
| Google Gemini | `gemini` | `GEMINI_API_KEY` |
| Groq | `groq` | `GROQ_API_KEY` |
| OpenRouter | `openrouter` | `OPENROUTER_API_KEY` |
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` |
| Mistral | `mistral` | `MISTRAL_API_KEY` |
| xAI | `xai` | `XAI_API_KEY` |
| Together | `together` | `TOGETHER_API_KEY` |
| OpenCode | `opencode` | `OPENCODE_API_KEY` |
| Any OpenAI-compatible API | `custom` | `--api-base-url` |
| Claude CLI | `claude` | uses the `claude` binary |
| Codex CLI | `codex` | uses the `codex` binary |

**API vs. CLI backends:** API backends use hpcagent's curated HPC tools (job wait
prediction, permission checks, quota lookups, doc reading). CLI backends drive the
external agent binary directly with its own toolset.

Useful flags: `--model`, `--effort {low,medium,high}`, `--docs-url`, `--docs-base-path`,
`--list-models`, `--list-tools`, `--dangerous-bypass`, `--config-path`.

</details>

## Install from source

```bash
git clone https://github.com/PursuitOfDataScience/hpcagent.git
cd hpcagent
pip install -e ".[full]"
hpcagent
```

## Status

Beta. The portable SLURM tools (`squeue`/`sinfo`/`scontrol`) work on any SLURM
cluster; site-specific account/quota commands are configurable. Issues and PRs welcome.

## License

MIT — see [LICENSE](LICENSE).
