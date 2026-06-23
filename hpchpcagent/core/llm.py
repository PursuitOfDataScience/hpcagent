import json, os, subprocess, sys, tempfile, threading, time, re, uuid, ast
from pathlib import Path
from hpchpcagent.core.ui import (
    Colors, ThinkingAnim, StreamRenderer, tool_status,
    stop_animation_if_running, print_assistant_response_text, c,
)
from hpchpcagent.core.tools import ToolRegistry


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
}

CLI_BACKENDS = {"codex", "claude", "agy"}

KNOWN_PROVIDERS = set(PROVIDER_BASE_URLS.keys()) | CLI_BACKENDS


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
        self.codex_use_dangerous_bypass = kwargs.get("codex_use_dangerous_bypass", True)
        self.codex_model_choices = kwargs.get("codex_model_choices", [])

        self.claude_cmd = kwargs.get("claude_cmd", shutil_which("claude") or "claude")
        self.claude_model = kwargs.get("claude_model", "")
        self.claude_effort = kwargs.get("claude_effort", "medium")

        self.agy_bin = kwargs.get("agy_bin") or shutil_which("agy") or ""
        self.agy_timeout = kwargs.get("agy_timeout", 120)

        # ── Internal state ──────────────────────────────────────────────────
        self._effort_override = {}

    # ── Provider helpers ────────────────────────────────────────────────────

    @property
    def is_cli_backend(self) -> bool:
        return self.backend in CLI_BACKENDS

    def _normalize_reasoning_effort(self, value: str | None) -> str | None:
        if value is None:
            return None
        lowered = value.strip().lower()
        return None if lowered in {"none", "default", "auto"} else lowered

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

        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": 0.7,
        }
        if openai_tools:
            payload["tools"] = openai_tools
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort

        url = f"{self.api_base_url.rstrip('/')}/chat/completions"
        try:
            with requests.post(
                url, headers=headers, json=payload, stream=True, timeout=120,
            ) as resp:
                if resp.status_code != 200:
                    yield ('error', f"API {resp.status_code}: {resp.text[:500]}")
                    return
                resp.encoding = "utf-8"
                current_tool = None
                current_tool_input = ""
                reasoning_content = ""

                for line in resp.iter_lines(decode_unicode=True):
                    if not line or line.strip() == "":
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
                            reasoning_content += delta["reasoning_content"]
                        elif delta.get("tool_calls"):
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
        except Exception as e:
            yield ('error', f"API request failed: {e}")

    # ── Generic chat step (OpenAI-compatible) ────────────────────────────────

    def run_chat_step(self, user_input: str, conversation: list, system_prompt: str,
                      tool_registry: ToolRegistry, is_continuation: bool = False) -> list:
        """Single turn against any OpenAI-compatible provider (streaming + tools)."""
        animation = ThinkingAnim()
        animation.start()

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
            tool_use_blocks = []
            current_tool = None
            current_tool_input = ""
            response_text = ""
            reasoning_content = ""

            for event_type, data in self._stream_chat(messages, tool_registry.get_schemas()):
                if event_type == 'error':
                    if animation.running:
                        animation.stop()
                    print(f"{Colors.STATUS_ERR}\u2717 {data}{Colors.RESET}")
                    return conversation

                elif event_type == 'text_delta':
                    if animation and animation.running:
                        animation.stop()
                    if not prompt_label_shown:
                        print(f"\r\033[K{c.TEAL}{c.BOLD}\u25cf{c.RESET} ", end="", flush=True)
                        prompt_label_shown = True
                    response_text += data
                    md_renderer.process_chunk(data)

                elif event_type == 'text_done':
                    md_renderer.flush()

                elif event_type == 'tool_use':
                    md_renderer.flush()
                    if animation and animation.running:
                        animation.stop()
                    current_tool = data
                    current_tool_input = ""
                    tool_status(current_tool['name'], status="running")

                elif event_type == 'tool_input_delta':
                    current_tool_input += data

                elif event_type == 'tool_input_done':
                    current_tool = data
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
                    md_renderer.flush()
                    reasoning_content = data
                    break

            if animation.running:
                animation.stop()

            assistant_msg = {"role": "assistant", "content": response_text}
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
            if reasoning_content:
                for m in reversed(messages):
                    if m["role"] == "assistant":
                        m["reasoning_content"] = reasoning_content
                        break
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
                    with open(last_message_path, 'r') as f:
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
                with open(last_message_path, 'r') as f:
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

    def run_codex_step(self, conversation, codex_state, system_prompt, tool_registry, is_continuation=False):
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
            return [], response_text
        finally:
            try:
                os.unlink(last_message_file.name)
            except FileNotFoundError:
                pass

    # ── Claude CLI ──────────────────────────────────────────────────────────

    def _build_claude_cmd(self, user_msg, first_turn, system_prompt):
        cmd = [self.claude_cmd, "-p", "--output-format", "stream-json",
               "--include-partial-messages",
               "--dangerously-skip-permissions", "--verbose"]
        if first_turn:
            if self.claude_model:
                cmd.extend(["--model", self.claude_model])
            if self.claude_effort:
                cmd.extend(["--effort", self.claude_effort])
            cmd.extend(["--append-system-prompt", system_prompt, user_msg])
        else:
            if self.claude_effort:
                cmd.extend(["--effort", self.claude_effort])
            cmd.extend(["-c", user_msg])
        return cmd

    def run_claude_turn(self, user_msg, first_turn, system_prompt):
        if not shutil_which(self.claude_cmd):
            print(f"{Colors.STATUS_ERR}\u2717 claude command not found. Install with: npm install -g @anthropic-ai/claude-code{Colors.RESET}")
            return ""

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
                            print(f"\r\033[K", end="", flush=True)
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
                print(f"\r\033[K", end="", flush=True)
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
