import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from hpcagent.core.tools import ToolRegistry, ToolRisk
from hpcagent.core.ui import (
    Colors,
    StreamRenderer,
    ThinkingAnim,
    c,
    stop_animation_if_running,
    tool_status,
)

# ── Provider registry ──────────────────────────────────────────────────────────

PROVIDER_BASE_URLS = {
    "opencode":    "https://opencode.ai/zen/v1",
    "openai":      "https://api.openai.com/v1",
    "groq":        "https://api.groq.com/openai/v1",
    "openrouter":  "https://openrouter.ai/api/v1",
    "deepseek":    "https://api.deepseek.com/v1",
    "mistral":     "https://api.mistral.ai/v1",
    "xai":         "https://api.x.ai/v1",
    "github":      "https://models.inference.ai.azure.com",
    "together":    "https://api.together.xyz/v1",
}

PROVIDER_ENV_KEYS = {
    "opencode":   "OPENCODE_API_KEY",
    "openai":     "OPENAI_API_KEY",
    "groq":       "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "deepseek":   "DEEPSEEK_API_KEY",
    "mistral":    "MISTRAL_API_KEY",
    "xai":        "XAI_API_KEY",
    "github":     "GITHUB_TOKEN",
    "together":   "TOGETHER_API_KEY",
}

PROVIDER_MODEL_HINTS = {
    "opencode":   "deepseek-v4-flash-free",
    "openai":     "gpt-4o",
    "groq":       "mixtral-8x7b-32768",
    "openrouter": "openai/gpt-4o",
    "deepseek":   "deepseek-chat",
    "mistral":    "mistral-large-latest",
    "xai":        "grok-2",
    "github":     "gpt-4o",
    "together":   "mistralai/Mixtral-8x7B-Instruct-v0.1",
}

PROVIDER_DEFAULT_MODELS = {
    "opencode":   "deepseek-v4-flash-free",
    "openai":     "gpt-4o",
    "groq":       "mixtral-8x7b-32768",
    "openrouter": "openai/gpt-4o",
    "deepseek":   "deepseek-chat",
    "mistral":    "mistral-large-latest",
    "xai":        "grok-2",
    "github":     "gpt-4o",
    "together":   "mistralai/Mixtral-8x7B-Instruct-v0.1",
}

PROVIDER_MODELS = {
    "openai":     ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano", "o3", "o4-mini"],
    "groq":       ["mixtral-8x7b-32768", "llama-3.3-70b-versatile", "llama-3.1-8b-instant", "deepseek-r1-distill-llama-70b"],
    "openrouter": ["openai/gpt-4o", "openai/gpt-4o-mini", "deepseek/deepseek-chat", "anthropic/claude-sonnet-4", "google/gemini-2.0-flash-001"],
    "deepseek":   ["deepseek-chat", "deepseek-reasoner"],
    "mistral":    ["mistral-large-latest", "mistral-small-latest", "codestral-latest", "ministral-8b-latest"],
    "xai":        ["grok-2", "grok-2-vision"],
    "opencode":   ["deepseek-v4-flash-free"],
    "github":     ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini"],
    "together":   ["mistralai/Mixtral-8x7B-Instruct-v0.1", "mistralai/Mistral-7B-Instruct-v0.3"],
}

CLI_BACKENDS = {"codex", "claude", "agy"}

KNOWN_PROVIDERS = set(PROVIDER_BASE_URLS.keys()) | CLI_BACKENDS

# Models where temperature should not be sent
REASONING_ONLY_MODELS = {"o3", "o4-mini", "deepseek-reasoner"}
# Models where reasoning_effort is supported
REASONING_EFFORT_MODELS = {"o3", "o4-mini", "deepseek-reasoner"}


def get_cached_models(provider: str) -> list | None:
    cache_dir = os.path.expanduser("~/.cache/hpcagent")
    cache_file = os.path.join(cache_dir, f"models-{provider}.json")
    if os.path.exists(cache_file):
        try:
            mtime = os.path.getmtime(cache_file)
            if time.time() - mtime < 24 * 3600:
                with open(cache_file) as f:
                    return json.load(f)
        except Exception:
            pass
    return None


def cache_models(provider: str, models: list):
    cache_dir = os.path.expanduser("~/.cache/hpcagent")
    try:
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f"models-{provider}.json")
        with open(cache_file, "w") as f:
            json.dump(models, f)
    except Exception:
        pass


def discover_models(provider: str, api_key: str = "", api_base_url: str | None = None) -> list:
    cached = get_cached_models(provider)
    if cached:
        return cached

    base_url = api_base_url or PROVIDER_BASE_URLS.get(provider, "")
    key = api_key or os.environ.get(PROVIDER_ENV_KEYS.get(provider, ""), "")

    if provider == "opencode":
        try:
            res = subprocess.run(["opencode", "models"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                models = [line.split()[0] for line in res.stdout.strip().split('\n') if line.strip() and not line.startswith('Model')]
                if models:
                    cache_models(provider, models)
                    return models
        except Exception:
            pass

    if not base_url:
        return PROVIDER_MODELS.get(provider, [])

    try:
        import requests
        headers = {}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        url = base_url.rstrip("/") + "/models"
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and "data" in data:
                models = [m["id"] for m in data["data"] if isinstance(m, dict) and "id" in m]
                if models:
                    cache_models(provider, models)
                    return models
            elif isinstance(data, list):
                models = [m["id"] for m in data if isinstance(m, dict) and "id" in m]
                if models:
                    cache_models(provider, models)
                    return models
    except Exception:
        pass

    return PROVIDER_MODELS.get(provider, [])


def resolve_model_alias(provider: str, alias: str, models: list) -> str:
    alias = alias.lower().strip()
    if not models:
        return alias

    if alias in models:
        return alias

    if alias == "free":
        free_models = [m for m in models if "free" in m.lower()]
        if free_models:
            return free_models[0]
        cheap_models = [m for m in models if "cheap" in m.lower() or "flash" in m.lower() or "mini" in m.lower()]
        if cheap_models:
            return cheap_models[0]
    elif alias in ("fast", "cheap"):
        cheap_models = [m for m in models if "flash" in m.lower() or "mini" in m.lower() or "instant" in m.lower() or "8b" in m.lower() or "haiku" in m.lower()]
        if cheap_models:
            return cheap_models[0]
    elif alias in ("smart", "best"):
        smart_models = [m for m in models if "large" in m.lower() or "sonnet" in m.lower() or "gpt-4o" in m.lower() or "pro" in m.lower() or "opus" in m.lower() or "chat" in m.lower()]
        if smart_models:
            return smart_models[0]
    elif alias == "reasoning":
        reasoning_models = [m for m in models if "reasoner" in m.lower() or "r1" in m.lower() or "o1" in m.lower() or "o3" in m.lower() or "o4" in m.lower()]
        if reasoning_models:
            return reasoning_models[0]

    for m in models:
        if alias in m.lower():
            return m

    return alias


def validate_model_choice(provider: str, model: str, models: list) -> str:
    if not models or model in models:
        return model
    resolved = resolve_model_alias(provider, model, models)
    if resolved in models:
        return resolved
    import difflib
    matches = difflib.get_close_matches(model, models, n=3, cutoff=0.3)
    if matches:
        raise ValueError(f"Model '{model}' not found for provider '{provider}'. Did you mean one of: {', '.join(matches)}?")
    raise ValueError(f"Model '{model}' not found for provider '{provider}'. Available models: {', '.join(models[:10])}...")


def shutil_which(cmd: str) -> str | None:
    import shutil
    return shutil.which(cmd)


def convert_tools_to_openai_format(tools: list) -> list:
    return [
        {"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["input_schema"],
        }}
        for t in tools
    ]


class LLMClient:
    """Multi-provider LLM client.

    Provider-agnostic: pass any known backend name (openai, groq, deepseek,
    openrouter, together, mistral, xai, github, opencode, …) or ``custom``.
    CLI-based backends (codex, claude, agy) are also supported.

    API key resolution order:
      1. ``api_key`` kwarg
      2. ``<PROVIDER>_API_KEY`` env var (or ``GITHUB_TOKEN`` for ``github``)
      3. ``api_base_url`` kwarg  (only for ``custom`` / unknown backends)
    """

    def __init__(self, backend: str, **kwargs):
        self.backend = backend
        self.model = kwargs.get("model", "")
        self.reasoning_effort = kwargs.get("reasoning_effort", "")

        # ── Resolve API key & base URL ──────────────────────────────────────
        api_key = kwargs.get("api_key") or os.environ.get(PROVIDER_ENV_KEYS.get(backend, ""), "")
        base_url = kwargs.get("api_base_url", PROVIDER_BASE_URLS.get(backend, ""))
        self.api_key = api_key
        self.api_base_url = base_url

        # ── CLI backend config ──────────────────────────────────────────────
        self.codex_bin = kwargs.get("codex_bin") or shutil_which("codex") or "codex"
        self.codex_model = kwargs.get("codex_model", "")
        self.codex_reasoning_effort = kwargs.get("codex_reasoning_effort", "")
        self.codex_use_dangerous_bypass = kwargs.get("codex_use_dangerous_bypass", False)
        self.codex_model_choices = kwargs.get("codex_model_choices", [])

        self.claude_cmd = kwargs.get("claude_cmd", shutil_which("claude") or "claude")
        self.claude_model = kwargs.get("claude_model", "")
        self.claude_effort = kwargs.get("claude_effort", "")
        self.claude_dangerously_skip_permissions = kwargs.get("claude_dangerously_skip_permissions", False)

        self.agy_bin = kwargs.get("agy_bin") or shutil_which("agy") or ""
        self.agy_timeout = kwargs.get("agy_timeout", 120)

        # ── Internal state ──────────────────────────────────────────────────
        self._effort_override: dict[str, str] = {}
        self.claude_session_id: str | None = None

    # ── Provider helpers ────────────────────────────────────────────────────

    @property
    def is_cli_backend(self) -> bool:
        return self.backend in CLI_BACKENDS

    def _normalize_reasoning_effort(self, value: str | None) -> str | None:
        if value is None:
            return None
        lowered = value.strip().lower()
        return None if lowered in {"none", "default", "auto"} else lowered

    def _is_reasoning_model(self) -> bool:
        model_lower = self.model.strip().lower()
        return any(rm.lower() in model_lower for rm in REASONING_ONLY_MODELS)

    def _supports_reasoning_effort(self) -> bool:
        model_lower = self.model.strip().lower()
        return any(rm.lower() in model_lower for rm in REASONING_EFFORT_MODELS)

    def _clamp_opencode_effort(self, effort: str) -> str:
        effort_lower = effort.lower().strip()
        if effort_lower in ("low", "medium", "high"):
            return effort_lower
        if effort_lower in ("1", "2", "3"):
            mapping = {"1": "low", "2": "medium", "3": "high"}
            return mapping[effort_lower]
        return "medium"

    # ── Generic OpenAI-compatible streaming ─────────────────────────────────

    def _stream_chat(self, messages: list, tools: list):
        """Stream a chat completion from any OpenAI-compatible endpoint."""
        try:
            import requests
        except ImportError:
            yield ('error', "The 'requests' library is not installed. Run: pip install requests")
            return

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        openai_tools = convert_tools_to_openai_format(tools)

        cleaned_messages = []
        for m in messages:
            m_copy = dict(m)
            m_copy.pop("reasoning_content", None)
            if "tool_calls" in m_copy and not m_copy["tool_calls"]:
                m_copy.pop("tool_calls")
            cleaned_messages.append(m_copy)

        payload: dict = {
            "model": self.model,
            "messages": cleaned_messages,
            "stream": True,
        }
        if not self._is_reasoning_model():
            payload["temperature"] = 0.7
        if openai_tools:
            payload["tools"] = openai_tools
        if self.reasoning_effort and self._supports_reasoning_effort():
            if self.backend == "opencode":
                payload["reasoning_effort"] = self._clamp_opencode_effort(self.reasoning_effort)
            else:
                payload["reasoning_effort"] = self.reasoning_effort

        url = f"{self.api_base_url.rstrip('/')}/chat/completions"
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with requests.post(
                    url, headers=headers, json=payload, stream=True, timeout=120,
                ) as resp:
                    if resp.status_code == 429:
                        retry_after = int(resp.headers.get("Retry-After", 2 ** attempt))
                        time.sleep(retry_after)
                        continue
                    if resp.status_code == 502 and attempt < max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue
                    if resp.status_code != 200:
                        yield ('error', f"API {resp.status_code}: {resp.text[:500]}")
                        return
                    resp.encoding = "utf-8"
                    current_tool = None
                    current_tool_input = ""
                    reasoning_content = ""

                    for raw_line in resp.iter_lines():
                        if not raw_line:
                            continue
                        line = raw_line.decode("utf-8", "replace") if isinstance(raw_line, (bytes, bytearray)) else str(raw_line)
                        if not line.strip():
                            continue
                        if line.startswith("data: "):
                            data = line[6:].strip()
                            if data == "[DONE]":
                                if current_tool:
                                    try:
                                        current_tool["input"] = json.loads(current_tool_input) if current_tool_input else {}
                                    except json.JSONDecodeError:
                                        current_tool["input"] = {}
                                    yield ('tool_input_done', current_tool)
                                    current_tool = None
                                    current_tool_input = ""
                                else:
                                    yield ('text_done', None)
                                yield ('done', reasoning_content)
                                continue
                            try:
                                event = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            choice = event.get("choices", [{}])[0] if event.get("choices") else {}
                            delta = choice.get("delta", {})
                            finish_reason = choice.get("finish_reason")

                            if delta.get("content"):
                                yield ('text_delta', delta["content"])
                            if delta.get("reasoning_content"):
                                rc = delta["reasoning_content"]
                                reasoning_content += rc
                                yield ('reasoning_delta', rc)
                            if delta.get("tool_calls"):
                                for tc in delta["tool_calls"]:
                                    if tc.get("index") is not None and tc.get("index") != (current_tool["index"] if current_tool else None):
                                        if current_tool:
                                            try:
                                                current_tool["input"] = json.loads(current_tool_input) if current_tool_input else {}
                                            except json.JSONDecodeError:
                                                current_tool["input"] = {}
                                            yield ('tool_input_done', current_tool)
                                        current_tool = {
                                            "id": tc["id"], "name": tc["function"]["name"],
                                            "input": "", "index": tc["index"],
                                        }
                                        current_tool_input = ""
                                        yield ('tool_use', current_tool)
                                    if tc.get("function", {}).get("arguments"):
                                        current_tool_input += tc["function"]["arguments"]
                                        yield ('tool_input_delta', tc["function"]["arguments"])
                            if finish_reason == "tool_calls" and current_tool:
                                try:
                                    current_tool["input"] = json.loads(current_tool_input) if current_tool_input else {}
                                except json.JSONDecodeError:
                                    current_tool["input"] = {}
                                yield ('tool_input_done', current_tool)
                                current_tool = None
                                current_tool_input = ""
                            if finish_reason in ("stop", "end_turn"):
                                if current_tool:
                                    try:
                                        current_tool["input"] = json.loads(current_tool_input) if current_tool_input else {}
                                    except json.JSONDecodeError:
                                        current_tool["input"] = {}
                                    yield ('tool_input_done', current_tool)
                                    current_tool = None
                                    current_tool_input = ""
                    return
            except requests.exceptions.ConnectionError as e:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                yield ('error', f"API connection failed after {max_retries} retries: {e}")
                return
            except Exception as e:
                yield ('error', f"API request failed: {e}")
                return

    # ── Generic chat step (OpenAI-compatible) ────────────────────────────────

    def run_chat_step(self, user_input: str, conversation: list, system_prompt: str,
                      tool_registry: ToolRegistry, is_continuation: bool = False,
                      confirm_destructive=None) -> list:
        """Single turn against any OpenAI-compatible provider (streaming + tools)."""
        animation = ThinkingAnim()
        animation.start()

        # Safely trim conversation to avoid hitting context limits on long sessions
        if len(conversation) > 30:
            start_idx = len(conversation) - 30
            while start_idx < len(conversation):
                if conversation[start_idx].get("role") == "user":
                    conversation = conversation[start_idx:]
                    break
                start_idx += 1

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(conversation)

        user_already_in = (
            any(m.get("role") == "user" and m.get("content") == user_input
                for m in conversation)
        ) if not is_continuation else False

        if not user_already_in and not is_continuation:
            conversation.append({"role": "user", "content": user_input})
            messages.append({"role": "user", "content": user_input})

        while True:
            md_renderer = StreamRenderer()
            prompt_label_shown = False
            in_reasoning = False
            tool_use_blocks: list[dict[str, Any]] = []
            current_tool = None
            current_tool_input = ""
            response_text = ""
            reasoning_content = ""
            has_output_on_line = False

            for event_type, data in self._stream_chat(messages, tool_registry.get_schemas()):
                if event_type == 'error':
                    if animation.running:
                        animation.stop()
                    print(f"{Colors.STATUS_ERR}\u2717 {data}{Colors.RESET}")
                    return conversation

                elif event_type == 'reasoning_delta':
                    if animation and animation.running:
                        animation.stop()
                    if not in_reasoning:
                        print(f"\r\033[K{c.GRAY}Thinking...{c.RESET}\n", end="", flush=True)
                        in_reasoning = True
                    sys.stdout.write(f"{c.GRAY}{data}{c.RESET}")
                    sys.stdout.flush()
                    has_output_on_line = True

                elif event_type == 'text_delta':
                    if in_reasoning:
                        print(c.RESET + "\n")
                        in_reasoning = False
                    if animation and animation.running:
                        animation.stop()
                    if not prompt_label_shown:
                        if tool_use_blocks:
                            print()
                            print(f"{c.TEAL}{c.BOLD}\u25cf{c.RESET} ", end="", flush=True)
                        else:
                            print(f"\r\033[K{c.TEAL}{c.BOLD}\u25cf{c.RESET} ", end="", flush=True)
                        prompt_label_shown = True
                    response_text += data
                    md_renderer.process_chunk(data)
                    has_output_on_line = True

                elif event_type == 'text_done':
                    md_renderer.flush()

                elif event_type == 'tool_use':
                    if in_reasoning:
                        print(c.RESET + "\n")
                        in_reasoning = False
                    md_renderer.flush()
                    if animation and animation.running:
                        animation.stop()
                    if has_output_on_line:
                        print()
                    current_tool = data
                    current_tool_input = ""
                    tool_status(current_tool['name'], status="running")
                    has_output_on_line = True

                elif event_type == 'tool_input_delta':
                    current_tool_input += data

                elif event_type == 'tool_input_done':
                    current_tool = data
                    # Check if destructive tool requires confirmation
                    risk = tool_registry.get_risk(current_tool['name'])
                    if risk == ToolRisk.DESTRUCTIVE and confirm_destructive:
                        if not confirm_destructive(current_tool['name'], current_tool['input']):
                            result = f"Cancelled by user: {current_tool['name']} was not executed."
                            tool_status(current_tool['name'], status="error")
                        else:
                            tool_status(current_tool['name'], status="success")
                            try:
                                result = tool_registry.execute(current_tool['name'], current_tool['input'])
                            except Exception as e:
                                result = f"Error executing {current_tool['name']}: {str(e)}"
                    else:
                        tool_status(current_tool['name'], status="success")
                        try:
                            result = tool_registry.execute(current_tool['name'], current_tool['input'])
                        except Exception as e:
                            result = f"Error executing {current_tool['name']}: {str(e)}"
                    tool_use_blocks.append({
                        "call_id": current_tool.get('id', ''),
                        "name": current_tool['name'],
                        "arguments": json.dumps(current_tool['input']),
                        "result": result,
                    })
                    current_tool = None
                    current_tool_input = ""

                elif event_type == 'done':
                    if in_reasoning:
                        print(c.RESET + "\n")
                        in_reasoning = False
                    md_renderer.flush()
                    reasoning_content = data
                    break

            if animation.running:
                animation.stop()

            assistant_msg: dict[str, Any] = {"role": "assistant", "content": response_text}
            if reasoning_content:
                assistant_msg["reasoning_content"] = reasoning_content
            if tool_use_blocks:
                assistant_msg["tool_calls"] = [
                    {"id": b["call_id"], "type": "function",
                     "function": {"name": b["name"], "arguments": b["arguments"]}}
                    for b in tool_use_blocks
                ]
            conversation.append(assistant_msg)

            if not tool_use_blocks:
                return conversation

            for block in tool_use_blocks:
                conversation.append({"role": "tool", "tool_call_id": block["call_id"], "content": block["result"]})

            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(conversation)
            continue

    # ── Codex CLI ──────────────────────────────────────────────────────────

    def _build_codex_exec_command(self, last_message_path, prompt, thread_id, system_prompt):
        developer_instructions = json.dumps(system_prompt)
        normalized_effort = self._normalize_reasoning_effort(self.codex_reasoning_effort)
        reasoning_effort = json.dumps(normalized_effort) if normalized_effort else None

        if thread_id:
            command = [
                self.codex_bin, "exec", "resume",
                "--json", "--output-last-message", last_message_path,
                "-c", f"developer_instructions={developer_instructions}",
            ]
            if reasoning_effort:
                command.extend(["-c", f"model_reasoning_effort={reasoning_effort}"])
            if self.codex_use_dangerous_bypass:
                command.append("--dangerously-bypass-approvals-and-sandbox")
            if self.codex_model:
                command.extend(["-m", self.codex_model])
            command.extend([thread_id, prompt])
            return command

        command = [
            self.codex_bin, "exec",
            "--json", "--output-last-message", last_message_path,
            "-c", f"developer_instructions={developer_instructions}",
        ]
        if reasoning_effort:
            command.extend(["-c", f"model_reasoning_effort={reasoning_effort}"])
        if self.codex_use_dangerous_bypass:
            command.append("--dangerously-bypass-approvals-and-sandbox")
        if self.codex_model:
            command.extend(["-m", self.codex_model])
        command.append(prompt)
        return command

    def _is_codex_transient_error(self, stderr_text: str) -> bool:
        text = (stderr_text or "").lower()
        signals = (
            "failed to connect to websocket", "responses_websocket",
            "error sending request", "connection reset", "connection closed",
            "broken pipe", "timed out", "temporarily unavailable",
            "502 bad gateway", "503 service", "504 gateway",
        )
        return any(s in text for s in signals)

    def _is_codex_thread_not_found(self, stderr_text: str) -> bool:
        text = (stderr_text or "").lower()
        return "thread" in text and "not found" in text

    def _clean_codex_error(self, text: str) -> str:
        if not text:
            return ""
        keep = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped == "Reading additional input from stdin...":
                continue
            if re.match(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s+(ERROR|WARN|INFO|DEBUG|TRACE)\b", stripped):
                continue
            keep.append(stripped)
        return "\n".join(keep).strip()

    def _extract_codex_event(self, stdout_text: str, event_type: str):
        for line in stdout_text.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == event_type:
                return event
        return None

    def _extract_codex_last_message(self, stdout_text: str) -> str:
        message_text = ""
        for line in stdout_text.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "agent_message":
                    message_text = item.get("text", "") or message_text
        return message_text

    def _stream_codex_output(self, last_message_path, process, animation, md_renderer, is_continuation):
        stdout_chunks = []
        stderr_chunks = []

        def _drain(pipe, sink):
            try:
                for line in iter(pipe.readline, ''):
                    sink.append(line)
            except Exception:
                pass

        stdout_thread = threading.Thread(target=_drain, args=(process.stdout, stdout_chunks), daemon=True)
        stderr_thread = threading.Thread(target=_drain, args=(process.stderr, stderr_chunks), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        first_content = False
        last_pos = 0
        while process.poll() is None:
            try:
                if Path(last_message_path).exists():
                    with open(last_message_path) as f:
                        f.seek(last_pos)
                        new_content = f.read()
                        if new_content:
                            if not first_content:
                                stop_animation_if_running(animation)
                                animation = None
                                print(f"\r\033[K{c.TEAL}{c.BOLD}\u25cf{c.RESET} ", end="", flush=True)
                                first_content = True
                            md_renderer.process_chunk(new_content)
                            last_pos = f.tell()
            except Exception:
                pass
            time.sleep(0.05)

        try:
            if Path(last_message_path).exists():
                with open(last_message_path) as f:
                    f.seek(last_pos)
                    remaining = f.read()
                    if remaining:
                        if not first_content:
                            stop_animation_if_running(animation)
                            animation = None
                            print(f"\r\033[K{c.TEAL}{c.BOLD}\u25cf{c.RESET} ", end="", flush=True)
                            first_content = True
                        md_renderer.process_chunk(remaining)
        except Exception:
            pass

        md_renderer.flush()
        stop_animation_if_running(animation)
        stdout_thread.join(timeout=2)
        stderr_thread.join(timeout=2)

        stdout_text = "".join(stdout_chunks)
        stderr_text = "".join(stderr_chunks)
        return stdout_text, stderr_text, process.returncode

    def run_codex_step(self, conversation, codex_state, system_prompt, is_continuation=False):
        prompt = self._latest_user_message(conversation)
        if not prompt:
            raise RuntimeError("No user message available for Codex.")

        animation = ThinkingAnim()
        animation.start()

        last_message_file = tempfile.NamedTemporaryFile(prefix="codex-last-", suffix=".txt", delete=False)
        last_message_file.close()

        try:
            def _run_popen():
                command = self._build_codex_exec_command(
                    last_message_file.name, prompt,
                    codex_state.get("thread_id"), system_prompt,
                )
                return subprocess.Popen(
                    command, cwd=os.getcwd(),
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )

            max_transient_retries = 2
            transient_attempts = 0
            thread_reset_done = False
            anim = animation

            while True:
                process = _run_popen()
                md_renderer = StreamRenderer()
                stdout_text, stderr_text, result_code = self._stream_codex_output(
                    last_message_file.name, process, anim, md_renderer, is_continuation,
                )
                anim = None

                if result_code == 0:
                    break

                if not thread_reset_done and codex_state.get("thread_id") and self._is_codex_thread_not_found(stderr_text):
                    thread_reset_done = True
                    codex_state["thread_id"] = None
                    continue

                if transient_attempts < max_transient_retries and self._is_codex_transient_error(stderr_text):
                    transient_attempts += 1
                    time.sleep(1.5 * transient_attempts)
                    continue

                err_msg = self._clean_codex_error(stderr_text) or self._clean_codex_error(stdout_text)
                if self._is_codex_transient_error(stderr_text) and (not err_msg or "websocket" in err_msg.lower()):
                    err_msg = ("Codex could not reach the model endpoint (transport error). "
                               "This is usually a transient backend issue - please try again.")
                if not err_msg:
                    err_msg = f"Codex exited with code {result_code}"
                raise RuntimeError(err_msg)

            thread_started = self._extract_codex_event(stdout_text, "thread.started")
            if thread_started and thread_started.get("thread_id"):
                codex_state["thread_id"] = thread_started["thread_id"]

            response_text = Path(last_message_file.name).read_text() if Path(last_message_file.name).exists() else ""
            if not response_text.strip():
                response_text = self._extract_codex_last_message(stdout_text)

            response_text = response_text.strip()
            print()
            return response_text
        finally:
            try:
                os.unlink(last_message_file.name)
            except FileNotFoundError:
                pass

    # ── Claude CLI ──────────────────────────────────────────────────────────

    def _build_claude_cmd(self, user_msg, first_turn, system_prompt):
        cmd = [self.claude_cmd, "-p", "--output-format", "stream-json"]
        if self.claude_dangerously_skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        cmd.append("--verbose")
        if first_turn or not getattr(self, "claude_session_id", None):
            if self.claude_model:
                cmd.extend(["--model", self.claude_model])
            if self.claude_effort:
                cmd.extend(["--effort", self.claude_effort])
            cmd.extend(["--append-system-prompt", system_prompt, user_msg])
        else:
            if self.claude_effort:
                cmd.extend(["--effort", self.claude_effort])
            cmd.extend(["--resume", self.claude_session_id, user_msg])
        return cmd

    def run_claude_turn(self, user_msg, first_turn, system_prompt):
        if not shutil_which(self.claude_cmd):
            print(f"{Colors.STATUS_ERR}\u2717 claude command not found. Install with: npm install -g @anthropic-ai/claude-code{Colors.RESET}")
            return ""

        if first_turn:
            self.claude_session_id = None

        cmd = self._build_claude_cmd(user_msg, first_turn, system_prompt)
        md_renderer = StreamRenderer()
        animation = ThinkingAnim()
        animation.start()
        response_text = ""
        has_text = False

        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        except FileNotFoundError:
            if animation.running:
                animation.stop()
            print(f"{Colors.STATUS_ERR}\u2717 claude command not found or failed to launch.{Colors.RESET}")
            return ""

        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = event.get("session_id")
            if sid:
                self.claude_session_id = sid
            event_type = event.get("type")

            if event_type == "stream_event":
                inner = event.get("event", {})
                inner_type = inner.get("type")
                if inner_type == "content_block_delta":
                    delta = inner.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            if not has_text:
                                if animation.running:
                                    animation.stop()
                                has_text = True
                                print(f"\r{c.TEAL}{c.BOLD}\u25cf{c.RESET} ", end="", flush=True)
                            response_text += text
                            md_renderer.process_chunk(text)
                elif inner_type == "content_block_stop":
                    md_renderer.flush()
            elif event_type == "assistant":
                if not has_text:
                    msg = event.get("message", {})
                    for block in msg.get("content", []):
                        if block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                if animation.running:
                                    animation.stop()
                                has_text = True
                                print(f"\r{c.TEAL}{c.BOLD}\u25cf{c.RESET} ", end="", flush=True)
                                response_text += text
                                md_renderer.process_chunk(text)
                                md_renderer.flush()
            elif event_type == "result":
                break

        proc.wait()
        stderr_output = proc.stderr.read() if proc.stderr else ""

        if animation.running:
            animation.stop()
        md_renderer.flush()

        if not has_text and not response_text:
            error_line = ""
            for e_line in stderr_output.splitlines():
                if e_line.strip():
                    error_line = e_line.strip()
                    break
            if error_line:
                print(f"\r{c.GRAY}Notice: {error_line}\033[K{c.RESET}", end="", flush=True)
                print()

        return response_text

    # ── agy CLI ────────────────────────────────────────────────────────────

    def _build_agy_prompt(self, user_input: str, conversation: list) -> str:
        parts = []
        recent = []
        count = 0
        for msg in reversed(conversation):
            if msg.get("role") == "user":
                recent.append(("[User]", msg.get("content", "")))
                count += 1
            elif msg.get("role") == "assistant" and msg.get("content"):
                recent.append(("[Assistant]", msg.get("content", "")))
            elif msg.get("role") == "tool" and msg.get("content"):
                recent.append(("[Tool Result]", msg.get("content", "")))
            if count >= 4:
                break
        recent.reverse()
        for label, content in recent:
            parts.append(f"{label}\n{content}\n")
        parts.append(f"[Current user question]\n{user_input}\n")
        parts.append("Answer the current question.")
        return "\n".join(parts)

    def run_agy_step(self, user_input: str, conversation: list, system_prompt: str,
                     is_continuation: bool = False):
        if not self.agy_bin or not os.path.isfile(self.agy_bin):
            print(f"{Colors.STATUS_ERR}\u2717 agy binary not found at {self.agy_bin}{Colors.RESET}")
            return conversation

        if not is_continuation:
            conversation.append({"role": "user", "content": user_input})

        prompt = self._build_agy_prompt(user_input, conversation)

        use_animation = sys.stdout.isatty()
        animation = ThinkingAnim() if use_animation else None
        if animation:
            animation.start()
        else:
            print(f"\r\033[K{c.GRAY}... Running agy...\033[K{c.RESET}", end="", flush=True)

        try:
            proc_env = {**os.environ, 'TERM': 'xterm-256color', 'HOME': os.path.expanduser('~')}
            proc = subprocess.Popen(
                [self.agy_bin, "--print", prompt],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1, env=proc_env,
            )
            if not proc.stdout or not proc.stderr:
                raise RuntimeError("Failed to open agy stdout/stderr streams.")

            md_renderer = StreamRenderer()
            prompt_label_shown = False
            response_chunks = []
            stderr_chunks = []
            import select as sel_mod
            streams = [proc.stdout, proc.stderr]
            start_time = time.time()

            while streams:
                if time.time() - start_time > self.agy_timeout:
                    proc.kill()
                    raise subprocess.TimeoutExpired(proc.args, self.agy_timeout)
                ready, _, _ = sel_mod.select(streams, [], [], 0.1)
                if not ready:
                    if proc.poll() is not None and not streams:
                        break
                    continue
                for stream in list(ready):
                    line = stream.readline()
                    if line == "":
                        if stream in streams:
                            streams.remove(stream)
                        continue
                    if stream is proc.stdout:
                        if animation and animation.running:
                            animation.stop()
                        if not prompt_label_shown:
                            print("\r\033[K", end="", flush=True)
                            print(f"{c.TEAL}{c.BOLD}\u25cf{c.RESET} ", end="", flush=True)
                            prompt_label_shown = True
                        md_renderer.process_chunk(line)
                        response_chunks.append(line)
                    else:
                        stderr_chunks.append(line)

            if animation and animation.running:
                animation.stop()
            md_renderer.flush()
            proc.wait(timeout=1)

            response = "".join(response_chunks).strip()
            if not response:
                response = "*(empty response)*"

            if not prompt_label_shown:
                print("\r\033[K", end="", flush=True)
                print(f"{c.TEAL}{c.BOLD}\u25cf{c.RESET} ", end="", flush=True)
                md_renderer.process_chunk(response)
                md_renderer.flush()

            conversation.append({"role": "assistant", "content": response})

        except subprocess.TimeoutExpired:
            if animation and animation.running:
                animation.stop()
            print(f"\r\033[K{Colors.STATUS_ERR}\u2717 Request timed out ({self.agy_timeout}s){Colors.RESET}")
        except FileNotFoundError:
            if animation and animation.running:
                animation.stop()
            print(f"\r\033[K{Colors.STATUS_ERR}\u2717 agy binary not found{Colors.RESET}")
        except Exception as e:
            if animation and animation.running:
                animation.stop()
            print(f"\r\033[K{Colors.STATUS_ERR}\u2717 Error: {str(e)}{Colors.RESET}")

        return conversation

    # ── Utilities ──────────────────────────────────────────────────────────

    @staticmethod
    def _latest_user_message(conversation: list) -> str:
        for msg in reversed(conversation):
            if msg.get("role") == "user":
                return msg.get("content", "")
        return ""
