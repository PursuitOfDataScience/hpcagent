import os
import re

from hpcagent.core.config import JsonConfig
from hpcagent.core.llm import CLI_BACKENDS, PROVIDER_BASE_URLS, PROVIDER_ENV_KEYS, LLMClient
from hpcagent.core.selectors import _SELECTION_CANCELLED, interactive_select
from hpcagent.core.tools import ToolRegistry
from hpcagent.core.ui import (
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

DEFAULT_BANNER = [
    r"H   H PPPP   CCC          A    GGG  EEEEE N   N TTTTT",
    r"H   H P   P C   C        A A  G     E     NN  N   T  ",
    r"HHHHH PPPP  C           AAAAA G GG  EEEE  N N N   T  ",
    r"H   H P     C   C       A   A G   G E     N  NN   T  ",
    r"H   H P      CCC        A   A  GGG  EEEEE N   N   T  ",
]


class HPCAgent:
    """Batteries-included HPC AI Agent.

    Subclass or configure for your own SLURM cluster's specifics:
      - Set doc_paths to point at your own documentation
      - Set system_prompt for your organization
      - Override tool registrations to add/remove commands
    """

    def __init__(self, **kwargs):
        self.system_prompt = kwargs.get("system_prompt", "")
        self.system_prompt_append = kwargs.get("system_prompt_append", "")
        self.banner_lines = kwargs.get("banner_lines", DEFAULT_BANNER)
        self.banner_subtitle = kwargs.get("banner_subtitle", "HPC Agent")

        # Config file — load saved settings, CLI args override
        self.config = JsonConfig(kwargs.get("config_path", "~/.hpcagent_config"))
        self.backend = kwargs.get("backend") or self.config.get("backend") or os.environ.get("HPCAGENT_BACKEND", "opencode")
        self.model = kwargs.get("model") or self.config.get("model") or os.environ.get("OPENCODE_MODEL", "deepseek-v4-flash-free")
        self.api_key = kwargs.get("api_key") or self.config.get("api_key") or ""
        self.api_base_url = kwargs.get("api_base_url") or self.config.get("api_base_url") or ""
        self.docs_base_path = kwargs.get("docs_base_path") or self.config.get("docs_base_path") or ""
        self.web_base_path = kwargs.get("web_base_path") or self.config.get("web_base_path") or ""

        # LLM client
        self._init_llm()

        # Tool registry
        self.tools = ToolRegistry()

        # Documentation paths
        self.doc_paths = {}
        self.web_doc_paths = {}

        # Conversation state
        self.conversation = []
        self.codex_state = {"thread_id": None, "conv": []}

        # RAG keyword-to-tool mappings (set by subclass)
        self.rag_topic_map = {}

        # Register built-in tools
        self._register_core_tools()

        # Callback for additional tool registration
        self._register_hooks = kwargs.get("register_hooks", None)
        if self._register_hooks:
            self._register_hooks(self.tools)

    def _init_llm(self):
        llm_kwargs = {
            "api_key": self.api_key,
            "api_base_url": self.api_base_url,
        }
        if self.model:
            llm_kwargs["model"] = self.model
        self.llm = LLMClient(backend=self.backend, **llm_kwargs)

    def _register_core_tools(self):
        t = self.tools

        t.register("extend_slurm_job", "Extend a running SLURM job's time limit", {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "The SLURM job ID to extend"},
                "time_limit": {"type": "string", "description": "New time limit (e.g. 4-00:00:00 or +02:00:00)"},
            },
            "required": ["job_id", "time_limit"],
        }, lambda a: extend_slurm_job(a["job_id"], a["time_limit"]))

        t.register("check_user_quota", "Check disk quota for a user", {
            "type": "object", "properties": {"user_id": {"type": "string"}}, "required": ["user_id"],
        }, lambda a: check_user_quota(a["user_id"]))

        t.register("check_user_jobs", "Check SLURM jobs for a user", {
            "type": "object", "properties": {"user_id": {"type": "string"}}, "required": ["user_id"],
        }, lambda a: check_user_jobs(a["user_id"]))

        t.register("get_job_details", "Get details about a specific SLURM job", {
            "type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"],
        }, lambda a: get_job_details(a["job_id"]))

        t.register("predict_pending_job_wait", "Estimate how long a pending SLURM job may wait", {
            "type": "object", "properties": {
                "job_id": {"type": "string"},
                "use_scontrol": {"type": "boolean", "description": "Enrich with scontrol (default: true)"},
            }, "required": ["job_id"],
        }, lambda a: predict_pending_job_wait(a["job_id"], a.get("use_scontrol", True)))

        t.register("check_pi_balance", "Check PI account balance", {
            "type": "object", "properties": {"account_name": {"type": "string"}}, "required": ["account_name"],
        }, lambda a: check_pi_balance(a["account_name"]))

        t.register("check_pi_allocations", "Check PI allocation history", {
            "type": "object", "properties": {"account_name": {"type": "string"}}, "required": ["account_name"],
        }, lambda a: check_pi_allocations(a["account_name"]))

        t.register("check_pi_storage", "Check PI storage allocations", {
            "type": "object", "properties": {"account_name": {"type": "string"}}, "required": ["account_name"],
        }, lambda a: check_pi_storage(a["account_name"]))

        t.register("list_user_accounts", "List accounts a user belongs to", {
            "type": "object", "properties": {"user_id": {"type": "string"}}, "required": [],
        }, lambda a: list_user_accounts(a.get("user_id")))

        t.register("check_account_members", "List members in an account", {
            "type": "object", "properties": {"account_name": {"type": "string"}}, "required": ["account_name"],
        }, lambda a: check_account_members(a["account_name"]))

        t.register("check_su_usage", "Check SU usage for account/user", {
            "type": "object", "properties": {
                "account_name": {"type": "string"}, "user_id": {"type": "string"}, "partition": {"type": "string"},
            }, "required": [],
        }, lambda a: check_su_usage(a.get("account_name"), a.get("user_id"), a.get("partition")))

        t.register("check_qos_info", "Get QOS information", {
            "type": "object", "properties": {"partition": {"type": "string"}}, "required": [],
        }, lambda a: check_qos_info(a.get("partition")))

        t.register("check_recent_jobs", "Get recent job records", {
            "type": "object", "properties": {"account_name": {"type": "string"}, "user_id": {"type": "string"}}, "required": [],
        }, lambda a: check_recent_jobs(a.get("account_name"), a.get("user_id")))

        t.register("check_low_balance_accounts", "Check low balance accounts", {
            "type": "object", "properties": {}, "required": [],
        }, lambda a: check_low_balance_accounts())

        t.register("get_partition_info", "Get partition info", {
            "type": "object", "properties": {"partition": {"type": "string"}}, "required": [],
        }, lambda a: get_partition_info(a.get("partition")))

        t.register("check_cluster_snapshot_summary", "Get cluster snapshot summary", {
            "type": "object", "properties": {
                "partition": {"type": "string"},
                "include_partition_breakdown": {"type": "boolean"},
                "use_scontrol": {"type": "boolean"},
            }, "required": [],
        }, lambda a: check_cluster_snapshot_summary(a.get("partition"), a.get("include_partition_breakdown", True), a.get("use_scontrol", True)))

        t.register("check_top_gpu_utilized_nodes", "Show top GPU-utilized nodes", {
            "type": "object", "properties": {
                "partition": {"type": "string"}, "limit": {"type": "integer"}, "use_scontrol": {"type": "boolean"},
            }, "required": [],
        }, lambda a: check_top_gpu_utilized_nodes(a.get("partition"), a.get("limit", 30), a.get("use_scontrol", True)))

        t.register("check_jobs_by_partition", "Check jobs in a partition", {
            "type": "object", "properties": {"partition": {"type": "string"}}, "required": ["partition"],
        }, lambda a: check_jobs_by_partition(a["partition"]))

        t.register("check_account_jobs", "Check all jobs for an account", {
            "type": "object", "properties": {"account_name": {"type": "string"}, "partition": {"type": "string"}}, "required": ["account_name"],
        }, lambda a: check_account_jobs(a["account_name"], a.get("partition")))

        t.register("check_jobs_by_node", "Check jobs on a node", {
            "type": "object", "properties": {"node_name": {"type": "string"}}, "required": ["node_name"],
        }, lambda a: check_jobs_by_node(a["node_name"]))

        t.register("check_node_hardware", "Check node hardware type", {
            "type": "object", "properties": {"node_name": {"type": "string"}, "db_path": {"type": "string"}}, "required": ["node_name"],
        }, lambda a: check_node_hardware(a["node_name"], a.get("db_path")))

        t.register("get_allocation_cycles", "List allocation cycles", {
            "type": "object", "properties": {}, "required": [],
        }, lambda a: get_allocation_cycles())

        t.register("get_current_user", "Get current username", {
            "type": "object", "properties": {}, "required": [],
        }, lambda a: get_current_user())

        t.register("analyze_disk_usage", "Analyze disk usage (read-only)", {
            "type": "object", "properties": {"directory": {"type": "string"}, "max_depth": {"type": "integer"}}, "required": [],
        }, lambda a: analyze_disk_usage(a.get("directory"), a.get("max_depth", 1)))

        t.register("check_path_info", "Check file/directory metadata", {
            "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"],
        }, lambda a: check_path_info(a["path"]))

        t.register("manage_file_permissions", "Change group ownership/permissions", {
            "type": "object", "properties": {
                "path": {"type": "string"}, "group": {"type": "string"}, "permissions": {"type": "string"},
            }, "required": ["path", "group"],
        }, lambda a: manage_file_permissions(a["path"], a["group"], a.get("permissions", "rX")))

        t.register("web_search", "Run a web search", {
            "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
        }, lambda a: web_search(a["query"]))

        t.register("web_fetch", "Fetch a URL", {
            "type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"],
        }, lambda a: web_fetch(a["url"]))

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

    def _needs_setup(self) -> bool:
        """Check if the agent needs initial setup (no API key, no CLI backend)."""
        if self.backend in CLI_BACKENDS:
            return False
        if self.api_key:
            return False
        env_var = PROVIDER_ENV_KEYS.get(self.backend, "")
        if env_var and os.environ.get(env_var):
            return False
        return True

    def _setup_wizard(self):
        """Interactive setup wizard for first-time users."""
        print(f"\n{c.BOLD}{c.PINK}Welcome to HPC Agent!{c.RESET}")
        print(f"  {c.GRAY}Let's set up your LLM backend.{c.RESET}\n")

        # Pick backend
        known = sorted(PROVIDER_BASE_URLS.keys()) + sorted(CLI_BACKENDS)
        labels = []
        for b in known:
            if b in CLI_BACKENDS:
                labels.append(f"{b}  (CLI — no API key needed)")
            else:
                env = PROVIDER_ENV_KEYS.get(b, "")
                labels.append(f"{b}  (env: ${env})")
        idx = interactive_select(labels, header="Choose your LLM provider",
                                  current_label=self.backend)
        if idx is _SELECTION_CANCELLED:
            return
        self.backend = known[idx]
        self.config.set("backend", self.backend)

        # If CLI backend, we're done
        if self.backend in CLI_BACKENDS:
            print(f"  {c.GREEN}Using {self.backend} CLI.{c.RESET}\n")
            self._init_llm()
            return

        # API key
        env_var = PROVIDER_ENV_KEYS.get(self.backend, "")
        cur_key = self.api_key or os.environ.get(env_var, "")
        if not cur_key:
            print(f"  {c.YELLOW}Enter your API key (or set ${env_var}){c.RESET}")
            key = read_input(f"  {c.CYAN}API key\u276f {c.RESET}").strip()
            if key:
                self.api_key = key
                self.config.set("api_key", key)

        # Model
        cur_model = self.model
        print(f"  {c.YELLOW}Enter model name (or press Enter for '{cur_model}'){c.RESET}")
        model = read_input(f"  {c.CYAN}Model\u276f {c.RESET}").strip()
        if model:
            self.model = model
            self.config.set("model", model)

        # Docs path (optional)
        print(f"  {c.YELLOW}Optional: path to your cluster docs (Enter to skip){c.RESET}")
        docs = read_input(f"  {c.CYAN}Docs path\u276f {c.RESET}").strip()
        if docs:
            self.docs_base_path = docs
            self.config.set("docs_base_path", docs)

        self._init_llm()
        print(f"\n  {c.GREEN}Setup complete!{c.RESET}\n")

    def run(self):
        self.conversation = []
        self.codex_state = {"thread_id": None, "conv": []}

        if self._needs_setup():
            self._setup_wizard()

        print_banner(
            self.banner_lines,
            subtitle=self.banner_subtitle,
            model=self.llm.model,
            effort=self.llm.reasoning_effort,
            animate=True,
        )

        while True:
            try:
                prompt = f"{self.backend}:{self.llm.model}"
                if self.llm.reasoning_effort:
                    prompt += f":{self.llm.reasoning_effort}"
                inp = read_input(f"{prompt} \u276f ").strip()
                print(c.RESET, end="")
                if not inp:
                    continue

                if inp.startswith('/'):
                    if inp in ('/exit', '/quit'):
                        print(f"{c.GREEN}Goodbye!{c.RESET}")
                        break
                    elif inp == '/help':
                        self._show_help()
                        continue
                    elif inp == '/config':
                        self._setup_wizard()
                        continue
                    else:
                        print(f"{c.RED}Unknown command: {inp}{c.RESET}")
                        continue

                self._handle_turn(inp)

            except (EOFError, KeyboardInterrupt):
                print(f"{c.RESET}\n{c.GREEN}Goodbye!{c.RESET}")
                break
            except Exception as e:
                import traceback
                print(f"{c.RED}\u2717 Error: {e}{c.RESET}")
                traceback.print_exc()

    def _handle_turn(self, user_input: str):
        if self.backend == 'codex':
            codex_conv = [{"role": "user", "content": user_input}]
            self.codex_state['conv'] = self.llm.run_codex_step(
                codex_conv, self.codex_state, self.system_prompt, self.tools,
            )
        elif self.backend == 'claude':
            if not hasattr(self, '_claude_first'):
                self._claude_first = True
            self.llm.run_claude_turn(user_input, self._claude_first, self.system_prompt_append or self.system_prompt)
            self._claude_first = False
        elif self.backend == 'agy':
            self.conversation = self.llm.run_agy_step(
                user_input, self.conversation, self.system_prompt,
            )
        else:
            self.conversation = self.llm.run_chat_step(
                user_input, self.conversation, self.system_prompt, self.tools,
            )
        print()

    def _show_help(self):
        print(f"\n{c.BOLD}{c.PINK}\u203a Available Commands{c.RESET}")
        print(f"   {c.GREEN}{c.BOLD}/help{c.RESET}        {c.GRAY}Show this help message{c.RESET}")
        print(f"   {c.RED}{c.BOLD}/exit{c.RESET}, {c.RED}/quit{c.RESET}  {c.GRAY}Exit the agent{c.RESET}")
        print(f"   {c.YELLOW}{c.BOLD}/config{c.RESET}      {c.GRAY}Reconfigure backend / API key / model{c.RESET}")
        print()

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

    def add_command_tool(self, name: str, description: str, schema: dict, handler):
        self.tools.register(name, description, schema, handler)
