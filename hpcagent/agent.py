import os
import re
from datetime import datetime, timezone

from hpcagent.core.config import JsonConfig
from hpcagent.core.docfetch import is_url, load_manifest, mirror_dir_for, mirror_docs
from hpcagent.core.llm import (
    CLI_BACKENDS,
    PROVIDER_BASE_URLS,
    PROVIDER_DEFAULT_MODELS,
    PROVIDER_ENV_KEYS,
    PROVIDER_MODEL_HINTS,
    PROVIDER_MODELS,
    LLMClient,
    discover_models,
    validate_model_choice,
)
from hpcagent.core.selectors import _SELECTION_CANCELLED, interactive_select
from hpcagent.core.tools import ToolRegistry, ToolRisk
from hpcagent.core.ui import (
    _INPUT_GO_BACK,
    SLASH_MENU,
    c,
    print_banner,
    read_input,
)
from hpcagent.core.web import (
    web_fetch,
    web_search,
)
from hpcagent.hpc.accounts import (
    check_account_jobs,
    check_account_members,
    check_jobs_by_node,
    check_jobs_by_partition,
    check_low_balance_accounts,
    check_pi_allocations,
    check_pi_balance,
    check_pi_storage,
    check_qos_info,
    check_recent_jobs,
    check_su_usage,
    check_user_jobs,
    check_user_quota,
    get_allocation_cycles,
    get_current_user,
    get_partition_info,
    list_user_accounts,
)
from hpcagent.hpc.disk import analyze_disk_usage
from hpcagent.hpc.docs import read_document
from hpcagent.hpc.jobs import (
    extend_slurm_job,
    get_job_details,
    predict_pending_job_wait,
)
from hpcagent.hpc.nodes import (
    check_cluster_snapshot_summary,
    check_node_hardware,
    check_top_gpu_utilized_nodes,
)
from hpcagent.hpc.permissions import check_path_info, manage_file_permissions

_CUSTOM_MODEL_ENTRY = "✎  Enter a custom name / alias…"

DEFAULT_BANNER = [
    "██╗  ██╗██████╗  ██████╗     █████╗  ██████╗ ███████╗███╗   ██╗████████╗",
    "██║  ██║██╔══██╗██╔════╝    ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝",
    "███████║██████╔╝██║         ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║   ",
    "██╔══██║██╔═══╝ ██║         ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║   ",
    "██║  ██║██║     ╚██████╗    ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║   ",
    "╚═╝  ╚═╝╚═╝      ╚═════╝    ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   ",
]

DEFAULT_SYSTEM_PROMPT = """You are hpcagent, an AI assistant specialized in High-Performance Computing (HPC) cluster operations. You help users manage SLURM jobs, check cluster status, manage files, and answer questions about the cluster.

Current date: {today}
User: {username}
Cluster: {cluster_name} (Scheduler: {scheduler})

Available Tools:
{tool_list}

Rules:
1. Use SLURM tools preferentially for cluster operations.
2. Auto-detect the current user with get_current_user when needed.
3. For time-sensitive queries, use web_search and web_fetch.
4. When a MUTATING/DESTRUCTIVE tool is needed, explain what you will do and wait for confirmation.
5. Be concise and precise in answers about cluster status."""


def copy_to_clipboard(text: str) -> bool:
    import shutil
    import subprocess

    if shutil.which("pbcopy"):
        try:
            proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE, text=True)
            proc.communicate(input=text)
            return proc.returncode == 0
        except Exception:
            pass

    if shutil.which("xclip"):
        try:
            proc = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE, text=True)
            proc.communicate(input=text)
            return proc.returncode == 0
        except Exception:
            pass

    if shutil.which("xsel"):
        try:
            proc = subprocess.Popen(["xsel", "-ib"], stdin=subprocess.PIPE, text=True)
            proc.communicate(input=text)
            return proc.returncode == 0
        except Exception:
            pass

    if shutil.which("wl-copy"):
        try:
            proc = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE, text=True)
            proc.communicate(input=text)
            return proc.returncode == 0
        except Exception:
            pass

    if shutil.which("clip"):
        try:
            proc = subprocess.Popen(["clip"], stdin=subprocess.PIPE, text=True)
            proc.communicate(input=text)
            return proc.returncode == 0
        except Exception:
            pass

    return False


class HPCAgent:
    """Batteries-included HPC AI Agent."""

    def __init__(self, **kwargs):
        # Config file — load saved settings, CLI args override
        default_config_path = "~/.config/hpcagent/config.json"
        legacy_config_path = "~/.hpcagent_config"
        config_path = kwargs.get("config_path")
        if not config_path:
            if os.path.exists(os.path.expanduser(legacy_config_path)) and not os.path.exists(os.path.expanduser(default_config_path)):
                config_path = legacy_config_path
            else:
                config_path = default_config_path
        self.config = JsonConfig(config_path)
        self._sanitize_config()
        # Did the user explicitly pick a backend/model on the CLI? If so, don't force onboarding.
        self._cli_configured = bool(kwargs.get("backend") or kwargs.get("model"))

        self._raw_system_prompt = kwargs.get("system_prompt") or self.config.get("system_prompt") or ""
        self.system_prompt_append = kwargs.get("system_prompt_append") or self.config.get("system_prompt_append") or ""
        self.banner_lines = kwargs.get("banner_lines", DEFAULT_BANNER)
        self.banner_subtitle = kwargs.get("banner_subtitle", "")

        self.backend = (kwargs.get("backend") or self.config.get("backend")
                        or os.environ.get("HPCAGENT_BACKEND") or self._auto_default_backend())
        default_model = PROVIDER_DEFAULT_MODELS.get(self.backend, "")
        self.model = kwargs.get("model") or self.config.get("model") or os.environ.get("OPENCODE_MODEL", "") or default_model
        self.api_key = kwargs.get("api_key") or self.config.get("api_key") or ""
        self.api_key_source = kwargs.get("api_key_source") or self.config.get("api_key_source") or ""
        self.api_base_url = kwargs.get("api_base_url") or self.config.get("api_base_url") or ""
        self.docs_base_path = kwargs.get("docs_base_path") or self.config.get("docs_base_path") or ""
        self.docs_url = kwargs.get("docs_url") or self.config.get("docs_url") or ""
        self.docs_max_pages = kwargs.get("docs_max_pages") or self.config.get("docs_max_pages") or 100
        self.web_base_path = kwargs.get("web_base_path") or self.config.get("web_base_path") or ""
        self.dangerous_bypass = kwargs.get("dangerous_bypass") or self.config.get("dangerous_bypass") or False
        self.reasoning_effort = kwargs.get("effort") or kwargs.get("reasoning_effort") or self.config.get("reasoning_effort") or ""

        # Inject configurations into HPC modules
        import hpcagent.hpc.accounts as hpc_accounts
        import hpcagent.hpc.nodes as hpc_nodes

        cluster_cfg = self.config.get("cluster") or {}
        commands_cfg = self.config.get("commands") or {}

        if "account_convention" in cluster_cfg:
            hpc_accounts.ACCOUNT_PREFIX = cluster_cfg["account_convention"]
        if "partition_shared" in cluster_cfg:
            hpc_accounts.SHARED_PARTITION = cluster_cfg["partition_shared"]
        if "quota" in commands_cfg:
            hpc_accounts.QUOTA_COMMAND = commands_cfg["quota"]
        if "accounts" in commands_cfg:
            hpc_accounts.ACCOUNTS_COMMAND = commands_cfg["accounts"]
        if "rcchelp" in commands_cfg:
            hpc_accounts.RCCHELP_COMMAND = commands_cfg["rcchelp"]
        if "snapshot_db" in cluster_cfg:
            hpc_nodes.NODE_MONITOR_DB_DEFAULT_PATH = cluster_cfg["snapshot_db"]

        # LLM client
        self._init_llm()

        # Tool registry
        self.tools = ToolRegistry()

        # Documentation paths
        self.doc_paths = {}
        self.web_doc_paths = {}

        # Conversation state
        self.conversation = []
        self.codex_state = {"thread_id": None}

        # Register built-in tools
        self._register_core_tools()

        # Callback for additional tool registration
        self._register_hooks = kwargs.get("register_hooks", None)
        if self._register_hooks:
            self._register_hooks(self.tools)

        # Auto-load skills and docs at startup
        self._load_all_configured_skills()

    def _build_system_prompt(self) -> str:
        raw_prompt = self._raw_system_prompt
        if raw_prompt and os.path.isfile(os.path.expanduser(raw_prompt)):
            try:
                with open(os.path.expanduser(raw_prompt)) as f:
                    template = f.read()
            except Exception:
                template = DEFAULT_SYSTEM_PROMPT
        elif raw_prompt:
            template = raw_prompt
        else:
            template = DEFAULT_SYSTEM_PROMPT

        username = "unknown"
        try:
            import pwd
            username = pwd.getpwuid(os.getuid()).pw_name
        except Exception:
            pass

        cluster_info = self.config.get("cluster") or {}
        cluster_name = cluster_info.get("name", "SLURM cluster")
        scheduler = cluster_info.get("scheduler", "SLURM")

        # Build tools description dynamically
        tool_catalog = []
        if hasattr(self, "tools") and self.tools:
            for schema in self.tools.get_schemas():
                risk = self.tools.get_risk(schema["name"]).value if hasattr(self.tools, "get_risk") else "read-only"
                tool_catalog.append(f"  • {schema['name']}: {schema['description']} [{risk}]")
        tool_list_str = "\n".join(tool_catalog)

        prompt = template.format(
            today=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            username=username,
            cluster_name=cluster_name,
            scheduler=scheduler,
            tool_list=tool_list_str,
        )

        if self.system_prompt_append:
            append_content = self.system_prompt_append
            if os.path.isfile(os.path.expanduser(append_content)):
                try:
                    with open(os.path.expanduser(append_content)) as f:
                        append_content = f.read()
                except Exception:
                    pass
            prompt += "\n\n" + append_content

        return prompt

    def _init_llm(self):
        if self.backend not in CLI_BACKENDS and self.model:
            env_var = PROVIDER_ENV_KEYS.get(self.backend, "")
            api_key = self.api_key or os.environ.get(env_var, "")
            if api_key:
                try:
                    models = discover_models(self.backend, api_key=api_key, api_base_url=self.api_base_url)
                    if models:
                        self.model = validate_model_choice(self.backend, self.model, models)
                except Exception:
                    pass

        llm_kwargs = {
            "api_key": self.api_key,
            "api_base_url": self.api_base_url,
            "codex_use_dangerous_bypass": self.dangerous_bypass,
            "claude_dangerously_skip_permissions": self.dangerous_bypass,
        }
        if self.model:
            llm_kwargs["model"] = self.model
        if hasattr(self, 'reasoning_effort') and self.reasoning_effort:
            llm_kwargs["reasoning_effort"] = self.reasoning_effort
            llm_kwargs["codex_reasoning_effort"] = self.reasoning_effort
            llm_kwargs["claude_effort"] = self.reasoning_effort
        self.llm = LLMClient(backend=self.backend, **llm_kwargs)

    def _confirm_destructive(self, tool_name: str, args: dict) -> bool:
        if self.dangerous_bypass:
            return True
        if tool_name == "manage_file_permissions":
            path = str(args.get("path", ""))
            group = str(args.get("group", ""))
            permissions = str(args.get("permissions", "rX"))
            try:
                preview = manage_file_permissions(path, group, permissions, dry_run=True)
                print(f"\n{c.YELLOW}{c.BOLD}⚠ Planned Permission Changes:{c.RESET}")
                print(f"{c.GRAY}{preview}{c.RESET}")
            except Exception as e:
                print(f"\n{c.RED}Error generating dry-run preview: {e}{c.RESET}")
        elif tool_name == "extend_slurm_job":
            job_id = args.get("job_id")
            time_limit = args.get("time_limit")
            print(f"\n{c.YELLOW}{c.BOLD}⚠ Planned SLURM Job Update:{c.RESET}")
            print(f"  {c.GRAY}Command: scontrol update JobID={job_id} TimeLimit={time_limit}{c.RESET}")
        else:
            arg_summary = ", ".join(f"{k}={v}" for k, v in args.items())
            print(f"\n{c.YELLOW}{c.BOLD}⚠ Planned Mutating Action: {tool_name}({arg_summary}){c.RESET}")

        print(f"  {c.GRAY}This action WILL modify the system.{c.RESET}")
        val = input(f"  {c.CYAN}Proceed? [y/N] {c.RESET}").strip().lower()
        return val in ("y", "yes")

    def _register_core_tools(self):
        t = self.tools

        # Mutating / destructive tools (require confirmation)
        t.register("extend_slurm_job", "Extend a running SLURM job's time limit", {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "The SLURM job ID to extend"},
                "time_limit": {"type": "string", "description": "New time limit (e.g. 4-00:00:00 or +02:00:00)"},
            },
            "required": ["job_id", "time_limit"],
        }, lambda a: extend_slurm_job(a["job_id"], a["time_limit"]), risk=ToolRisk.DESTRUCTIVE)

        t.register("manage_file_permissions", "Change group ownership/permissions", {
            "type": "object", "properties": {
                "path": {"type": "string"}, "group": {"type": "string"}, "permissions": {"type": "string"},
            }, "required": ["path", "group"],
        }, lambda a: manage_file_permissions(a["path"], a["group"], a.get("permissions", "rX")), risk=ToolRisk.DESTRUCTIVE)

        # Read-only tools
        t.register("check_user_quota", "Check disk quota for a user", {
            "type": "object", "properties": {"user_id": {"type": "string"}}, "required": ["user_id"],
        }, lambda a: check_user_quota(a["user_id"]), risk=ToolRisk.READ_ONLY)

        t.register("check_user_jobs", "Check SLURM jobs for a user", {
            "type": "object", "properties": {"user_id": {"type": "string"}}, "required": ["user_id"],
        }, lambda a: check_user_jobs(a["user_id"]), risk=ToolRisk.READ_ONLY)

        t.register("get_job_details", "Get details about a specific SLURM job", {
            "type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"],
        }, lambda a: get_job_details(a["job_id"]), risk=ToolRisk.READ_ONLY)

        t.register("predict_pending_job_wait", "Estimate how long a pending SLURM job may wait", {
            "type": "object", "properties": {
                "job_id": {"type": "string"},
                "use_scontrol": {"type": "boolean", "description": "Enrich with scontrol (default: true)"},
            }, "required": ["job_id"],
        }, lambda a: predict_pending_job_wait(a["job_id"], a.get("use_scontrol", True)), risk=ToolRisk.READ_ONLY)

        t.register("check_pi_balance", "Check PI account balance", {
            "type": "object", "properties": {"account_name": {"type": "string"}}, "required": ["account_name"],
        }, lambda a: check_pi_balance(a["account_name"]), risk=ToolRisk.READ_ONLY)

        t.register("check_pi_allocations", "Check PI allocation history", {
            "type": "object", "properties": {"account_name": {"type": "string"}}, "required": ["account_name"],
        }, lambda a: check_pi_allocations(a["account_name"]), risk=ToolRisk.READ_ONLY)

        t.register("check_pi_storage", "Check PI storage allocations", {
            "type": "object", "properties": {"account_name": {"type": "string"}}, "required": ["account_name"],
        }, lambda a: check_pi_storage(a["account_name"]), risk=ToolRisk.READ_ONLY)

        t.register("list_user_accounts", "List accounts a user belongs to", {
            "type": "object", "properties": {"user_id": {"type": "string"}}, "required": [],
        }, lambda a: list_user_accounts(a.get("user_id")), risk=ToolRisk.READ_ONLY)

        t.register("check_account_members", "List members in an account", {
            "type": "object", "properties": {"account_name": {"type": "string"}}, "required": ["account_name"],
        }, lambda a: check_account_members(a["account_name"]), risk=ToolRisk.READ_ONLY)

        t.register("check_su_usage", "Check SU usage for account/user", {
            "type": "object", "properties": {
                "account_name": {"type": "string"}, "user_id": {"type": "string"}, "partition": {"type": "string"},
            }, "required": [],
        }, lambda a: check_su_usage(a.get("account_name"), a.get("user_id"), a.get("partition")), risk=ToolRisk.READ_ONLY)

        t.register("check_qos_info", "Get QOS information", {
            "type": "object", "properties": {"partition": {"type": "string"}}, "required": [],
        }, lambda a: check_qos_info(a.get("partition")), risk=ToolRisk.READ_ONLY)

        t.register("check_recent_jobs", "Get recent job records", {
            "type": "object", "properties": {"account_name": {"type": "string"}, "user_id": {"type": "string"}}, "required": [],
        }, lambda a: check_recent_jobs(a.get("account_name"), a.get("user_id")), risk=ToolRisk.READ_ONLY)

        t.register("check_low_balance_accounts", "Check low balance accounts", {
            "type": "object", "properties": {}, "required": [],
        }, lambda a: check_low_balance_accounts(), risk=ToolRisk.READ_ONLY)

        t.register("get_partition_info", "Get partition info", {
            "type": "object", "properties": {"partition": {"type": "string"}}, "required": [],
        }, lambda a: get_partition_info(a.get("partition")), risk=ToolRisk.READ_ONLY)

        t.register("check_cluster_snapshot_summary", "Get cluster snapshot summary", {
            "type": "object", "properties": {
                "partition": {"type": "string"},
                "include_partition_breakdown": {"type": "boolean"},
                "use_scontrol": {"type": "boolean"},
            }, "required": [],
        }, lambda a: check_cluster_snapshot_summary(a.get("partition"), a.get("include_partition_breakdown", True), a.get("use_scontrol", True)), risk=ToolRisk.READ_ONLY)

        t.register("check_top_gpu_utilized_nodes", "Show top GPU-utilized nodes", {
            "type": "object", "properties": {
                "partition": {"type": "string"}, "limit": {"type": "integer"}, "use_scontrol": {"type": "boolean"},
            }, "required": [],
        }, lambda a: check_top_gpu_utilized_nodes(a.get("partition"), a.get("limit", 30), a.get("use_scontrol", True)), risk=ToolRisk.READ_ONLY)

        t.register("check_jobs_by_partition", "Check jobs in a partition", {
            "type": "object", "properties": {"partition": {"type": "string"}}, "required": ["partition"],
        }, lambda a: check_jobs_by_partition(a["partition"]), risk=ToolRisk.READ_ONLY)

        t.register("check_account_jobs", "Check all jobs for an account", {
            "type": "object", "properties": {"account_name": {"type": "string"}, "partition": {"type": "string"}}, "required": ["account_name"],
        }, lambda a: check_account_jobs(a["account_name"], a.get("partition")), risk=ToolRisk.READ_ONLY)

        t.register("check_jobs_by_node", "Check jobs on a node", {
            "type": "object", "properties": {"node_name": {"type": "string"}}, "required": ["node_name"],
        }, lambda a: check_jobs_by_node(a["node_name"]), risk=ToolRisk.READ_ONLY)

        t.register("check_node_hardware", "Check node hardware type", {
            "type": "object", "properties": {"node_name": {"type": "string"}, "db_path": {"type": "string"}}, "required": ["node_name"],
        }, lambda a: check_node_hardware(a["node_name"], a.get("db_path")), risk=ToolRisk.READ_ONLY)

        t.register("get_allocation_cycles", "List allocation cycles", {
            "type": "object", "properties": {}, "required": [],
        }, lambda a: get_allocation_cycles(), risk=ToolRisk.READ_ONLY)

        t.register("get_current_user", "Get current username", {
            "type": "object", "properties": {}, "required": [],
        }, lambda a: get_current_user(), risk=ToolRisk.READ_ONLY)

        t.register("analyze_disk_usage", "Analyze disk usage (read-only)", {
            "type": "object", "properties": {"directory": {"type": "string"}, "max_depth": {"type": "integer"}}, "required": [],
        }, lambda a: analyze_disk_usage(a.get("directory"), a.get("max_depth", 1)), risk=ToolRisk.READ_ONLY)

        t.register("check_path_info", "Check file/directory metadata", {
            "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"],
        }, lambda a: check_path_info(a["path"]), risk=ToolRisk.READ_ONLY)

        t.register("web_search", "Run a web search", {
            "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
        }, lambda a: web_search(a["query"]), risk=ToolRisk.READ_ONLY)

        t.register("web_fetch", "Fetch a URL", {
            "type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"],
        }, lambda a: web_fetch(a["url"]), risk=ToolRisk.READ_ONLY)

    def _execute_doc_tool(self, name: str, inp: dict) -> str:
        if name in self.doc_paths:
            doc_path = self.doc_paths[name]
            content = read_document(doc_path, base_path=self.docs_base_path)
            return f"=== DOCUMENT: {doc_path} ===\n\n{content}"
        if name in self.web_doc_paths:
            doc_path = self.web_doc_paths[name]
            content = read_document(doc_path, base_path=self.web_base_path)
            return f"=== WEB CONTENT: {doc_path} ===\n\n{content}"
        return f"Unknown doc tool: {name}"

    def _sanitize_config(self):
        """Drop garbage values a previous broken wizard may have saved.

        Earlier builds could persist a typed slash-command (e.g. '/exit') as the
        model or docs path. Clear any such junk so startup isn't polluted.
        """
        bad = {"/exit", "/quit", "/config", "/help", "/model", "/models",
               "/backend", "/effort", "/tools", "/clear", "/new", "/docs", "/keys"}
        for key in ("model", "docs_base_path", "web_base_path", "system_prompt",
                    "system_prompt_append"):
            val = self.config.get(key)
            if isinstance(val, str) and val.strip() in bad:
                self.config.set(key, "")

    @staticmethod
    def _auto_default_backend() -> str:
        """Pick a sensible default backend so a zero-arg run 'just works'.

        Prefers an installed agent CLI (no API key needed), then any provider
        whose env key is already set, falling back to opencode.
        """
        import shutil
        for cli in ("claude", "codex"):
            if shutil.which(cli):
                return cli
        if os.environ.get("OPENCODE_API_KEY"):
            return "opencode"
        for prov, env in PROVIDER_ENV_KEYS.items():
            if os.environ.get(env):
                return prov
        return "opencode"

    @staticmethod
    def _provider_menu_values() -> list:
        """Provider menu order: installed CLIs first, then API providers, custom, missing CLIs."""
        import shutil
        cli_present = [b for b in sorted(CLI_BACKENDS) if shutil.which(b)]
        cli_absent = [b for b in sorted(CLI_BACKENDS) if not shutil.which(b)]
        api = sorted(PROVIDER_BASE_URLS.keys())
        return cli_present + api + ["custom"] + cli_absent

    @staticmethod
    def _provider_label(backend: str) -> str:
        import shutil
        if backend in CLI_BACKENDS:
            if shutil.which(backend):
                return f"{backend}  (installed CLI — no API key needed)"
            return f"{backend}  (CLI not installed)"
        if backend == "custom":
            return "custom  (your own OpenAI-compatible endpoint)"
        env = PROVIDER_ENV_KEYS.get(backend, "")
        if env and os.environ.get(env):
            return f"{backend}  (API key detected in ${env})"
        return backend

    def _needs_setup(self) -> bool:
        """Check if the agent needs initial setup (no API key, no CLI backend)."""
        if self.backend in CLI_BACKENDS:
            return False
        if self.api_key_source == "none":
            return False
        if self.api_key:
            return False
        env_var = PROVIDER_ENV_KEYS.get(self.backend, "")
        if env_var and os.environ.get(env_var):
            return False
        return True

    @staticmethod
    def _scan_shell_configs():
        """Scan common shell config files for API key exports."""
        found = {}
        config_files = [
            os.path.expanduser("~/.bashrc"),
            os.path.expanduser("~/.zshrc"),
            os.path.expanduser("~/.profile"),
            os.path.expanduser("~/.bash_profile"),
            os.path.expanduser("~/.zshenv"),
        ]
        pat = re.compile(r'^\s*export\s+(\w+_API_KEY)\s*=\s*["\']?([^"\'#\s]+)["\']?\s*(?:#.*)?$', re.M)
        for fp in config_files:
            if os.path.isfile(fp):
                try:
                    with open(fp) as f:
                        for m in pat.finditer(f.read()):
                            name, val = m.group(1), m.group(2)
                            for backend, env in PROVIDER_ENV_KEYS.items():
                                if env == name and len(val) > 4:
                                    found.setdefault(backend, []).append((fp, val))
                except OSError:
                    pass
        return found

    def _read_or_back(self, prompt):
        val = read_input(prompt)
        if val is _INPUT_GO_BACK:
            return None
        val = str(val).strip() if val is not None else ""
        if val in ('/exit', '/quit'):
            self._exit()
        return val

    def _setup_wizard(self):
        """Interactive setup wizard for first-time users."""
        print(f"\n{c.BOLD}{c.PINK}Welcome to HPC Agent!{c.RESET}")
        print(f"  {c.GRAY}Let's set up your LLM backend.{c.RESET}\n")

        known = self._provider_menu_values()

        # ── Provider menu ──────────────────────────────────────────────────
        while True:
            idx = interactive_select(known, header="Choose your LLM provider",
                                      current_label=self.backend, clear_on_confirm=True,
                                      display_fn=self._provider_label)
            if idx is _SELECTION_CANCELLED:
                return
            self.backend = known[idx]
            self.config.set("backend", self.backend)
            if self.backend in CLI_BACKENDS:
                print(f"  {c.GREEN}Using {self.backend} CLI.{c.RESET}\n")
                self._init_llm()
                return

            if self.backend == "custom":
                print(f"  {c.YELLOW}Enter your Custom API Base URL{c.RESET}")
                base_url = self._read_or_back(f"  {c.CYAN}Base URL\u276f {c.RESET}")
                if base_url is None:
                    continue
                self.api_base_url = base_url
                self.config.set("api_base_url", base_url)

                print(f"  {c.YELLOW}Enter your Custom API Key (Enter to skip/none){c.RESET}")
                key = self._read_or_back(f"  {c.CYAN}API Key\u276f {c.RESET}")
                if key is None:
                    continue
                if key:
                    self.api_key = key
                    self.api_key_source = "file"
                    self.config.set("api_key", key)
                else:
                    self.api_key = ""
                    self.api_key_source = "none"
            else:
                env_var = PROVIDER_ENV_KEYS.get(self.backend, "")
                cur_key = self.api_key or os.environ.get(env_var, "")
                if not cur_key:
                    shell_keys = self._scan_shell_configs()
                    bk = self.backend
                    candidates = shell_keys.get(bk, [])
                    if candidates:
                        src, val = candidates[0]
                        print(f"  {c.GREEN}Found {bk} key in {src}{c.RESET}")
                        masked = "..." + val[-4:] if len(val) > 4 else "..."
                        print(f"  {c.GRAY}Found key ending with: {masked}{c.RESET}")
                        print(f"  {c.YELLOW}Use it for this session without saving? [Y/n] (or type a different key, ESC=back){c.RESET}")
                        ans = self._read_or_back(f"  {c.CYAN}API key\u276f {c.RESET}")
                        if ans is None:
                            continue
                        if ans == "" or ans.lower() in ("y", "yes"):
                            self.api_key = val
                            self.api_key_source = "session"
                            print(f"  {c.GREEN}Using key for this session only (not saved to disk).{c.RESET}")
                        else:
                            self.api_key = ans
                            self.api_key_source = "session"
                    else:
                        print(f"  {c.YELLOW}Enter your {bk} API key (or set ${env_var}){c.RESET}")
                        print(f"  {c.GRAY}Press Enter with no key for session-only mode, ESC to go back{c.RESET}")
                        key = self._read_or_back(f"  {c.CYAN}API key\u276f {c.RESET}")
                        if key is None:
                            continue
                        if key:
                            print(f"  {c.YELLOW}Save this key to config file? [y/N] {c.RESET}")
                            save_ans = self._read_or_back(f"  {c.CYAN}Save? [y/N] {c.RESET}")
                            if save_ans and save_ans.lower() in ("y", "yes"):
                                self.api_key = key
                                self.config.set("api_key", key)
                                self.api_key_source = "file"
                                print(f"  {c.GREEN}Key saved.{c.RESET}")
                            else:
                                self.api_key = key
                                self.api_key_source = "session"
                                print(f"  {c.GREEN}Using key for this session only.{c.RESET}")

            # ── Model ──────────────────────────────────────────────────────
            cur_model = self.model if self.model and self.model not in ('/exit', '/quit') else ""
            print(f"  {c.GRAY}Fetching available models from provider...{c.RESET}")
            env_var = PROVIDER_ENV_KEYS.get(self.backend, "")
            api_key = self.api_key or os.environ.get(env_var, "")
            models = discover_models(self.backend, api_key=api_key, api_base_url=self.api_base_url)
            if not models:
                models = PROVIDER_MODELS.get(self.backend, [])

            if models:
                labels = list(models)
                if cur_model and cur_model in labels:
                    labels.remove(cur_model)
                    labels.insert(0, cur_model)
                labels.append(_CUSTOM_MODEL_ENTRY)
                header = "Choose a model" + (f" (current: {cur_model})" if cur_model else "")
                midx = interactive_select(labels, header=header, default_idx=0,
                                          clear_on_confirm=True, searchable=True,
                                          always_show={_CUSTOM_MODEL_ENTRY})
                if midx is _SELECTION_CANCELLED:
                    continue
                selected = labels[midx]
                if selected == _CUSTOM_MODEL_ENTRY:
                    print(f"  {c.YELLOW}Type model name or search query (e.g. 'gpt-4o', 'deepseek', 'fast'){c.RESET}")
                    typed = self._read_or_back(f"  {c.CYAN}Model Search\u276f {c.RESET}")
                    if typed is None:
                        continue
                    try:
                        resolved = validate_model_choice(self.backend, typed, models)
                        model = resolved
                    except ValueError as ve:
                        print(f"  {c.RED}{ve}{c.RESET}")
                        print(f"  {c.YELLOW}Use exact string '{typed}' anyway? [y/N]{c.RESET}")
                        confirm_typed = self._read_or_back(f"  {c.CYAN}Proceed? [y/N]\u276f {c.RESET}")
                        if confirm_typed and confirm_typed.lower() in ("y", "yes"):
                            model = typed
                        else:
                            continue
                else:
                    model = selected
            else:
                hint = PROVIDER_MODEL_HINTS.get(self.backend, "model-name")
                default = PROVIDER_DEFAULT_MODELS.get(self.backend, "")
                if cur_model:
                    print(f"  {c.YELLOW}Enter model name (or press Enter for '{cur_model}'){c.RESET}")
                elif default:
                    print(f"  {c.YELLOW}Enter model name (press Enter for default: {default}){c.RESET}")
                else:
                    print(f"  {c.YELLOW}Enter model name (e.g. {hint}){c.RESET}")
                print(f"  {c.GRAY}Press ESC to go back{c.RESET}")
                model = self._read_or_back(f"  {c.CYAN}Model\u276f {c.RESET}")
                if model is None:
                    continue
                if not model and default:
                    model = default
            if model:
                self.model = model
                self.config.set("model", model)

            # ── Dangerous bypass ──────────────────────────────────────────
            print(f"  {c.YELLOW}Allow the agent to run commands without asking?{c.RESET}")
            print(f"  {c.GRAY}This lets mutating tools (chmod, chgrp, scontrol update) run without confirmation.{c.RESET}")
            bypass_ans = self._read_or_back(f"  {c.CYAN}Enable dangerous bypass? [y/N] {c.RESET}")
            if bypass_ans and bypass_ans.lower() in ("y", "yes"):
                self.dangerous_bypass = True
                self.config.set("dangerous_bypass", True)
                print(f"  {c.YELLOW}⚠ Dangerous bypass enabled.{c.RESET}")
            else:
                self.dangerous_bypass = False
                self.config.set("dangerous_bypass", False)

            # ── Docs path or URL ───────────────────────────────────────────
            current_docs = self.docs_url or self.docs_base_path
            if current_docs:
                print(f"  {c.YELLOW}Cluster docs — local folder or docs URL (current: {current_docs}){c.RESET}")
                print(f"  {c.GRAY}Enter to keep · new path/URL · '-' to clear · ESC to go back{c.RESET}")
            else:
                print(f"  {c.YELLOW}Optional: a local docs folder OR a docs-site URL (Enter to skip){c.RESET}")
                print(f"  {c.GRAY}A URL is crawled into a local cache the agent reads (re-sync via /docs sync). ESC to go back.{c.RESET}")
            docs = self._read_or_back(f"  {c.CYAN}Docs path\u276f {c.RESET}")
            if docs is None:
                continue
            if docs == "-":
                self.docs_base_path = ""
                self.docs_url = ""
                self.config.set("docs_base_path", "")
                self.config.set("docs_url", "")
            elif is_url(docs):
                self._mirror_docs_url(docs)
            elif docs:
                self.docs_base_path = os.path.expanduser(docs)
                self.docs_url = ""
                self.config.set("docs_base_path", self.docs_base_path)
                self.config.set("docs_url", "")

            self._init_llm()
            # Auto-load skills if docs path is set
            if self.docs_base_path:
                skills_path = os.path.join(self.docs_base_path, "skills.md")
                if os.path.isfile(skills_path):
                    try:
                        self.load_skills(skills_path)
                        print(f"  {c.GREEN}Loaded skills from {skills_path}{c.RESET}")
                    except Exception as e:
                        print(f"  {c.YELLOW}Could not load skills: {e}{c.RESET}")
            self._print_setup_summary()
            return

        self._init_llm()
        self._print_setup_summary()

    def _print_setup_summary(self):
        if self.api_key_source == "session":
            key_label = "session-only (not saved)"
        elif self.api_key_source == "file":
            key_label = "saved to credentials file (0600)"
        elif self.api_key_source == "none":
            key_label = "none (CLI / no key)"
        elif self.backend in CLI_BACKENDS:
            key_label = "handled by the CLI"
        else:
            env_var = PROVIDER_ENV_KEYS.get(self.backend, "")
            key_label = f"environment (${env_var})" if env_var and os.environ.get(env_var) else "none"

        print(f"\n  {c.GREEN}{c.BOLD}Setup complete!{c.RESET}")
        print(f"    {c.GRAY}Provider:{c.RESET} {self.backend}")
        print(f"    {c.GRAY}Model:{c.RESET}    {self.model or '(provider default)'}")
        docs_label = self.docs_url or self.docs_base_path or "(none)"
        if self.docs_url:
            docs_label += " (mirrored locally)"
        print(f"    {c.GRAY}Docs:{c.RESET}     {docs_label}")
        print(f"    {c.GRAY}API key:{c.RESET}  {key_label}")
        print(f"\n  {c.GRAY}Try asking:{c.RESET}")
        print(f"    {c.CYAN}• why is job 1234 pending?{c.RESET}")
        print(f"    {c.CYAN}• how do I request a GPU node?{c.RESET}")
        print(f"  {c.GRAY}Type {c.PINK}/help{c.GRAY} for commands, {c.PINK}/config{c.GRAY} to change this.{c.RESET}\n")

    def run(self):
        self.conversation = []
        self.codex_state = {"thread_id": None}

        first_run = not self.config.get("onboarded")
        needs_setup = self._needs_setup()
        # Run the wizard on first launch (so users always get to pick a model + docs),
        # or whenever an API backend has no key. Skip if they configured via CLI flags.
        if needs_setup or (first_run and not self._cli_configured):
            self._setup_wizard()
            self.config.set("onboarded", True)
            needs_setup = self._needs_setup()
        elif first_run:
            self.config.set("onboarded", True)

        if needs_setup and self.backend not in CLI_BACKENDS:
            env_var = PROVIDER_ENV_KEYS.get(self.backend, "")
            print(f"  {c.YELLOW}⚠ No API key configured for '{self.backend}'.{c.RESET}")
            print(f"  {c.GRAY}Set ${env_var}, run /config, or /backend to pick an installed CLI (claude/codex). "
                  f"Requests will fail until then.{c.RESET}\n")

        print_banner(
            self.banner_lines,
            subtitle=self.banner_subtitle,
            model="" if needs_setup else self.llm.model,
            effort="" if needs_setup else self.llm.reasoning_effort,
            animate=True,
        )

        while True:
            try:
                inp = read_input(f"{c.CYAN}\u276f{c.RESET} ")
                if inp is _INPUT_GO_BACK:
                    continue
                inp = inp.strip()
                print(c.RESET, end="")
                if not inp:
                    continue

                if inp.startswith('/'):
                    parts = inp.split(maxsplit=1)
                    cmd = parts[0].lower()
                    args_str = parts[1].strip() if len(parts) > 1 else ""

                    if cmd in ('/exit', '/quit'):
                        self._exit()

                    elif cmd == '/help':
                        print(f"\n  {c.BOLD}{c.CYAN}Available Slash Commands:{c.RESET}\n")
                        for name, label, desc, aliases in SLASH_MENU:
                            print(f"    {c.CYAN}{label:<15}{c.RESET} {c.GRAY}{desc}{c.RESET}")
                        print()
                        continue

                    elif cmd == '/config':
                        self._setup_wizard()
                        continue

                    elif cmd == '/backend':
                        known = self._provider_menu_values()
                        idx = interactive_select(known, header="Choose your LLM provider",
                                                  current_label=self.backend, clear_on_confirm=True,
                                                  display_fn=self._provider_label)
                        if idx is not _SELECTION_CANCELLED:
                            self.backend = known[idx]
                            self.config.set("backend", self.backend)
                            print(f"  {c.GREEN}Switched backend to {self.backend}.{c.RESET}")
                            if self._needs_setup():
                                print(f"  {c.YELLOW}Provider needs API key configuration. Starting wizard...{c.RESET}")
                                self._setup_wizard()
                            else:
                                self._init_llm()
                                print(f"  {c.GREEN}Initialized backend {self.backend} (model: {self.model}){c.RESET}")
                        continue

                    elif cmd in ('/model', '/models'):
                        cur_model = self.model if self.model else ""
                        print(f"  {c.GRAY}Fetching available models from {self.backend}...{c.RESET}")
                        env_var = PROVIDER_ENV_KEYS.get(self.backend, "")
                        api_key = self.api_key or os.environ.get(env_var, "")
                        models = discover_models(self.backend, api_key=api_key, api_base_url=self.api_base_url)
                        if not models:
                            models = PROVIDER_MODELS.get(self.backend, [])

                        if models:
                            labels = list(models)
                            if cur_model and cur_model in labels:
                                labels.remove(cur_model)
                                labels.insert(0, cur_model)
                            labels.append(_CUSTOM_MODEL_ENTRY)
                            header = "Choose a model" + (f" (current: {cur_model})" if cur_model else "")
                            midx = interactive_select(labels, header=header, default_idx=0,
                                                      clear_on_confirm=True, searchable=True,
                                                      always_show={_CUSTOM_MODEL_ENTRY})
                            if midx is not _SELECTION_CANCELLED:
                                selected = labels[midx]
                                if selected == _CUSTOM_MODEL_ENTRY:
                                    print(f"  {c.YELLOW}Type model name or search query (e.g. 'gpt-4o', 'deepseek', 'fast'){c.RESET}")
                                    typed = self._read_or_back(f"  {c.CYAN}Model Search\u276f {c.RESET}")
                                    if typed is not None:
                                        try:
                                            resolved = validate_model_choice(self.backend, typed, models)
                                            self.model = resolved
                                            self.config.set("model", resolved)
                                            print(f"  {c.GREEN}Model set to: {resolved}{c.RESET}")
                                        except ValueError as ve:
                                            print(f"  {c.RED}{ve}{c.RESET}")
                                            print(f"  {c.YELLOW}Use exact string '{typed}' anyway? [y/N]{c.RESET}")
                                            confirm_typed = self._read_or_back(f"  {c.CYAN}Proceed? [y/N]\u276f {c.RESET}")
                                            if confirm_typed and confirm_typed.lower() in ("y", "yes"):
                                                self.model = typed
                                                self.config.set("model", typed)
                                                print(f"  {c.GREEN}Model set to: {typed}{c.RESET}")
                                else:
                                    self.model = selected
                                    self.config.set("model", selected)
                                    print(f"  {c.GREEN}Model set to: {selected}{c.RESET}")
                                self._init_llm()
                        else:
                            print(f"  {c.RED}No models found or backend does not support model listing.{c.RESET}")
                        continue

                    elif cmd == '/effort':
                        val = args_str.lower()
                        if not val:
                            opts = ['low', 'medium', 'high', 'none']
                            idx = interactive_select(opts, header="Select reasoning effort level", current_label=self.reasoning_effort or 'none')
                            if idx is not _SELECTION_CANCELLED:
                                val = opts[idx]
                        if val in ('low', 'medium', 'high', 'none'):
                            if val == 'none':
                                self.reasoning_effort = ""
                            else:
                                self.reasoning_effort = val
                            self.config.set("reasoning_effort", self.reasoning_effort)
                            self.llm.reasoning_effort = self.reasoning_effort
                            self.llm.codex_reasoning_effort = self.reasoning_effort
                            self.llm.claude_effort = self.reasoning_effort
                            print(f"  {c.GREEN}Reasoning effort set to: {self.reasoning_effort or 'none'}{c.RESET}")
                        else:
                            print(f"  {c.RED}Invalid effort level. Choose from: low, medium, high, none{c.RESET}")
                        continue

                    elif cmd == '/tools':
                        print(f"\n  {c.BOLD}{c.PINK}Registered HPC Tools:{c.RESET}\n")
                        for schema in sorted(self.tools.get_schemas(), key=lambda x: x["name"]):
                            risk_val = self.tools.get_risk(schema["name"])
                            risk_str = risk_val.value if risk_val else "read-only"
                            if risk_str == "destructive":
                                risk_color = c.RED
                            elif risk_str == "mutating":
                                risk_color = c.YELLOW
                            else:
                                risk_color = c.GREEN
                            print(f"    * {c.CYAN}{schema['name']}{c.RESET} [{risk_color}{risk_str}{c.RESET}]")
                            print(f"      {c.GRAY}{schema['description']}{c.RESET}")
                        print()
                        continue

                    elif cmd in ('/clear', '/new'):
                        self.conversation = []
                        self.codex_state = {"thread_id": None}
                        if hasattr(self, '_claude_first'):
                            self._claude_first = True
                        print(f"  {c.GREEN}Conversation cleared. Starting fresh!{c.RESET}")
                        continue

                    elif cmd == '/retry':
                        last_user_idx = -1
                        for idx, msg in enumerate(reversed(self.conversation)):
                            if msg.get("role") == "user":
                                last_user_idx = len(self.conversation) - 1 - idx
                                break
                        if last_user_idx == -1:
                            print(f"  {c.RED}No previous user prompt to retry.{c.RESET}")
                            continue

                        last_prompt = self.conversation[last_user_idx]["content"]
                        self.conversation = self.conversation[:last_user_idx]
                        print(f"  {c.YELLOW}Retrying last prompt: {c.CYAN}{last_prompt}{c.RESET} ...\n")
                        self._handle_turn(last_prompt)
                        continue

                    elif cmd == '/copy':
                        last_resp = ""
                        for msg in reversed(self.conversation):
                            if msg.get("role") == "assistant" and msg.get("content"):
                                last_resp = msg["content"]
                                break
                        if not last_resp:
                            print(f"  {c.RED}No assistant response available to copy.{c.RESET}")
                            continue

                        if copy_to_clipboard(last_resp):
                            print(f"  {c.GREEN}Copied last assistant response to clipboard!{c.RESET}")
                        else:
                            print(f"  {c.YELLOW}Could not copy to clipboard (no clipboard utilities like xclip, wl-copy, pbcopy found).{c.RESET}")
                            print(f"  {c.GRAY}Last response content:{c.RESET}\n")
                            print(last_resp)
                            print()
                        continue

                    elif cmd == '/save':
                        filename = args_str
                        if not filename:
                            default_fn = f"hpcagent_transcript_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
                            print(f"  {c.YELLOW}Enter filename/path to save transcript (Enter for default: {default_fn}){c.RESET}")
                            ans = self._read_or_back(f"  {c.CYAN}Save Path\u276f {c.RESET}")
                            if ans is None:
                                continue
                            filename = ans.strip() or default_fn

                        try:
                            content = []
                            content.append(f"# HPC Agent Chat Transcript - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                            content.append(f"* **Backend:** {self.backend}")
                            content.append(f"* **Model:** {self.model}\n")
                            content.append("---")
                            for msg in self.conversation:
                                role = msg.get("role", "").capitalize()
                                text = msg.get("content", "")
                                if role == "User":
                                    content.append(f"\n### User\n{text}")
                                elif role == "Assistant":
                                    reasoning = msg.get("reasoning_content", "")
                                    if reasoning:
                                        content.append(f"\n### Assistant (Thinking)\n```\n{reasoning}\n```")
                                    content.append(f"\n### Assistant\n{text}")
                                elif role == "Tool":
                                    content.append(f"\n> **Tool Call Result ({msg.get('tool_call_id', '')}):**\n>\n> {text.replace(chr(10), chr(10)+'> ')}")

                            raw_md = "\n".join(content)
                            ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
                            clean_md = ansi_escape.sub('', raw_md)

                            with open(filename, 'w') as f:
                                f.write(clean_md)
                            print(f"  {c.GREEN}Transcript saved to {filename}{c.RESET}")
                        except Exception as e:
                            print(f"  {c.RED}Failed to save transcript: {e}{c.RESET}")
                        continue

                    elif cmd == '/docs':
                        subcmd = args_str.strip().lower()
                        if subcmd.startswith('sync'):
                            if not self.docs_url:
                                print(f"  {c.YELLOW}No docs URL configured. Add one via /config (Docs step) to enable sync.{c.RESET}")
                            elif self._mirror_docs_url(self.docs_url):
                                print(f"  {c.GREEN}Docs re-synced from {self.docs_url}.{c.RESET}")
                            continue
                        if subcmd in ('add', 'set'):
                            print(f"  {c.YELLOW}Enter a local docs folder or a docs-site URL:{c.RESET}")
                            val = self._read_or_back(f"  {c.CYAN}Docs path/URL❯ {c.RESET}")
                            if val:
                                if is_url(val):
                                    self._mirror_docs_url(val)
                                else:
                                    self.docs_base_path = os.path.expanduser(val)
                                    self.docs_url = ""
                                    self.config.set("docs_base_path", self.docs_base_path)
                                    self.config.set("docs_url", "")
                                    self._load_all_configured_skills()
                                    print(f"  {c.GREEN}Docs path set to {self.docs_base_path}.{c.RESET}")
                            continue

                        if self.docs_url:
                            mani = load_manifest(mirror_dir_for(self.docs_url))
                            print(f"\n  {c.BOLD}{c.CYAN}Docs source (mirrored from URL):{c.RESET}")
                            print(f"    {c.GRAY}URL:{c.RESET}     {self.docs_url}")
                            print(f"    {c.GRAY}Cache:{c.RESET}   {self.docs_base_path}")
                            if mani:
                                print(f"    {c.GRAY}Pages:{c.RESET}   {mani.get('page_count', '?')}")
                                print(f"    {c.GRAY}Fetched:{c.RESET} {mani.get('fetched_at', '?')}")
                            print(f"    {c.GRAY}Run {c.PINK}/docs sync{c.GRAY} to refresh.{c.RESET}")
                        elif self.docs_base_path:
                            print(f"\n  {c.BOLD}{c.CYAN}Docs source (local):{c.RESET} {c.GRAY}{self.docs_base_path}{c.RESET}")

                        if not self.doc_paths and not self.web_doc_paths:
                            print(f"  {c.YELLOW}No documentation pages or skills loaded yet.{c.RESET}")
                            print(f"  {c.GRAY}Add docs with {c.PINK}/docs add{c.GRAY} (local folder or URL).{c.RESET}")
                        else:
                            if self.doc_paths:
                                print(f"\n  {c.BOLD}{c.CYAN}Loaded doc pages ({len(self.doc_paths)}):{c.RESET}\n")
                                for name, path in sorted(self.doc_paths.items()):
                                    print(f"    * {c.CYAN}{name:<28}{c.RESET} {c.GRAY}{path}{c.RESET}")
                            if self.web_doc_paths:
                                print(f"\n  {c.BOLD}{c.CYAN}Loaded doc pages (web):{c.RESET}\n")
                                for name, path in sorted(self.web_doc_paths.items()):
                                    print(f"    * {c.CYAN}{name:<28}{c.RESET} {c.GRAY}{path}{c.RESET}")
                        print()
                        continue

                    elif cmd == '/keys':
                        env_var = PROVIDER_ENV_KEYS.get(self.backend, "")
                        has_env = bool(os.environ.get(env_var))
                        subcmd = args_str.lower()
                        if subcmd in ('logout', 'clear', 'forget', 'forget-key'):
                            self.api_key = ""
                            self.api_key_source = "none"
                            self.config.set("api_key", "")
                            print(f"  {c.GREEN}API key cleared from memory and configuration file.{c.RESET}")
                            self._init_llm()
                            continue

                        print(f"\n  {c.BOLD}{c.PINK}API Key Status & Sources:{c.RESET}\n")
                        print(f"    * {c.BOLD}Current Backend:{c.RESET} {self.backend}")
                        source_label = "None"
                        if self.api_key:
                            if self.api_key_source == "env" or (not self.api_key_source and has_env):
                                source_label = f"Environment Variable (${env_var})"
                            elif self.api_key_source == "file":
                                source_label = f"Saved Credentials File ({self.config.secrets_path})"
                            elif self.api_key_source == "session":
                                source_label = "Session-Only (in memory, not saved)"
                            else:
                                source_label = "Manually configured"

                        mask = "..." + self.api_key[-4:] if len(self.api_key) > 4 else "None" if not self.api_key else "..."
                        print(f"    * {c.BOLD}API Key Source:{c.RESET} {source_label}")
                        print(f"    * {c.BOLD}API Key Value:{c.RESET} {c.GRAY}{mask}{c.RESET}")
                        print(f"    * {c.BOLD}Environment Var Status:{c.RESET} " + (f"{c.GREEN}Detected (${env_var}){c.RESET}" if has_env else f"{c.YELLOW}Not Set{c.RESET}"))
                        print(f"\n    {c.GRAY}To clear stored credentials, type: /keys logout or /keys clear{c.RESET}\n")
                        continue

                    elif cmd == '/version':
                        print(f"\n  {c.BOLD}HPC Agent v0.1.0{c.RESET}")
                        print("  Agnostic HPC AI Agent framework — multi-backend LLM, SLURM tools, terminal UI")
                        print("  Built by Advanced Agentic Coding, DeepMind\n")
                        continue

                    else:
                        print(f"  {c.RED}Unknown command: {inp}{c.RESET}")
                        continue

                self._handle_turn(inp)

            except (EOFError, KeyboardInterrupt):
                self._exit()
            except Exception as e:
                import traceback
                print(f"{c.RED}\u2717 Error: {e}{c.RESET}")
                traceback.print_exc()

    def _handle_turn(self, user_input: str):
        system_prompt = self._build_system_prompt()
        if self.backend == 'codex':
            self.conversation.append({"role": "user", "content": user_input})
            response_text = self.llm.run_codex_step(
                self.conversation, self.codex_state, system_prompt,
            )
            if response_text:
                self.conversation.append({"role": "assistant", "content": response_text})
                print(f"\r\033[K{c.TEAL}{c.BOLD}\u25cf{c.RESET} {response_text}")
        elif self.backend == 'claude':
            if not hasattr(self, '_claude_first'):
                self._claude_first = True
            self.conversation.append({"role": "user", "content": user_input})
            response_text = self.llm.run_claude_turn(user_input, self._claude_first, self.system_prompt_append or system_prompt)
            self._claude_first = False
            if response_text:
                self.conversation.append({"role": "assistant", "content": response_text})
        else:
            self.conversation = self.llm.run_chat_step(
                user_input, self.conversation, system_prompt, self.tools,
                confirm_destructive=self._confirm_destructive,
            )
        print()

    @staticmethod
    def _exit():
        print(f"\n{c.GREEN}Goodbye!{c.RESET}")
        raise SystemExit(0)

    def add_doc_tool(self, name: str, description: str, doc_path: str, web: bool = False):
        paths = self.web_doc_paths if web else self.doc_paths
        paths[name] = doc_path
        self.tools.register(name, description, {"type": "object", "properties": {}, "required": []},
                            lambda a, n=name: self._execute_doc_tool(n, a))

    def load_skills(self, skills_path: str, *, web: bool = False):
        """Bulk-register doc tools from a skills.md file.

        Expected format:
            # <tool_name>
            <description>
            <path/to/doc.md>

        Sections are separated by blank lines.
        """
        paths = self.web_doc_paths if web else self.doc_paths
        base = self.web_base_path if web else self.docs_base_path

        with open(skills_path) as f:
            content = f.read()

        sections = re.split(r'\n# ', '\n' + content)
        for sec in sections:
            sec = sec.strip()
            if not sec:
                continue
            lines = sec.splitlines()
            name = lines[0].strip().lstrip('#').strip()
            if not name:
                continue
            desc = ""
            doc_path = ""
            for line in lines[1:]:
                line = line.strip()
                if not line:
                    continue
                if not doc_path and not line.startswith('#'):
                    if not desc:
                        desc = line
                    else:
                        doc_path = line
            if name and doc_path:
                full_path = os.path.join(base, doc_path) if base else doc_path
                paths[name] = full_path
                self.tools.register(
                    name, desc or name,
                    {"type": "object", "properties": {}, "required": []},
                    lambda a, n=name: self._execute_doc_tool(n, a),
                )

    def _mirror_docs_url(self, url: str, *, reload: bool = True) -> bool:
        """Crawl a docs-site URL into a local markdown cache and register it.

        Sets docs_url + docs_base_path (to the cache dir) and persists them so the
        mirror can be re-synced later with /docs sync.
        """
        dest = mirror_dir_for(url)

        def _progress(i, total, page_url):
            short = page_url if len(page_url) <= 68 else page_url[:65] + "..."
            print(f"\r{c.GRAY}Mirroring docs [{i}/{total}] {short}\033[K{c.RESET}", end="", flush=True)

        print(f"  {c.GRAY}Discovering and mirroring pages from {url} ...{c.RESET}")
        try:
            manifest = mirror_docs(url, dest, max_pages=int(self.docs_max_pages), progress=_progress)
        except Exception as e:
            print(f"\r\033[K  {c.RED}Could not mirror docs: {e}{c.RESET}")
            return False

        print(f"\r\033[K  {c.GREEN}Mirrored {manifest['page_count']} page(s) to {dest}{c.RESET}")
        self.docs_url = url
        self.docs_base_path = dest
        self.config.set("docs_url", url)
        self.config.set("docs_base_path", dest)
        if reload:
            self._load_all_configured_skills()
        return True

    def _load_all_configured_skills(self):
        if not self.docs_base_path:
            return

        base = os.path.expanduser(self.docs_base_path)
        if not os.path.isdir(base):
            return

        skills_md_path = os.path.join(base, "skills.md")
        if os.path.isfile(skills_md_path):
            try:
                self.load_skills(skills_md_path)
            except Exception:
                pass

        try:
            for root, dirs, files in os.walk(base):
                for file in files:
                    if file.endswith(".md") and file != "skills.md":
                        full_path = os.path.join(root, file)
                        rel_path = os.path.relpath(full_path, base)

                        title = ""
                        description = ""
                        try:
                            with open(full_path) as f:
                                first_lines = [f.readline().strip() for _ in range(5)]

                            for line in first_lines:
                                if line.startswith("#"):
                                    title = line.lstrip("#").strip()
                                    break
                                elif line.startswith("title:"):
                                    title = line.split(":", 1)[1].strip()
                                elif line.startswith("description:"):
                                    description = line.split(":", 1)[1].strip()
                        except Exception:
                            pass

                        name_part = os.path.splitext(file)[0].lower()
                        name_part = re.sub(r'[^a-z0-9_]', '_', name_part).strip('_')
                        tool_name = f"read_{name_part}"

                        if not description:
                            description = f"Read documentation about {title or name_part.replace('_', ' ')}"

                        if tool_name not in self.tools.get_names():
                            self.add_doc_tool(tool_name, description, rel_path)
        except Exception:
            pass

    def add_command_tool(self, name: str, description: str, schema: dict, handler):
        self.tools.register(name, description, schema, handler)
