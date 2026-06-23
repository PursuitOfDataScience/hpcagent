import os
import re

from hpcagent.core.config import JsonConfig
from hpcagent.core.llm import LLMClient
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
    r"  _   _ _____ _____     ___   _   _ _____ _   _ ",
    r" | | | |  ___|  ___|   / _ \ | \ | |_   _| \ | |",
    r" | |_| | |__ | |__    / /_\ \|  \| | | | |  \| |",
    r" |  _  |  __||  __|   |  _  || . ` | | | | . ` |",
    r" | | | | |___| |___   | | | || |\  |_| |_| |\  |",
    r" \_| |_|____/|____/   \_| |_/\_| \_/\___/\_| \_/",
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
        self.backend = kwargs.get("backend", os.environ.get("HPCAGENT_BACKEND", "opencode"))
        self.model = kwargs.get("model") or os.environ.get("OPENCODE_MODEL", "deepseek-v4-flash-free")
        self.api_key = kwargs.get("api_key", "")
        self.api_base_url = kwargs.get("api_base_url", "")

        # LLM client — strip keys that LLMClient doesn't expect, pass rest
        llm_skip = {"system_prompt", "system_prompt_append", "banner_lines",
                     "banner_subtitle", "config_path", "docs_base_path",
                     "web_base_path", "register_hooks", "backend"}
        llm_kwargs = {k: v for k, v in kwargs.items() if k not in llm_skip}
        self.llm = LLMClient(backend=self.backend, **llm_kwargs)

        # Config
        self.config = JsonConfig(kwargs.get("config_path", "~/.hpcagent_config"))

        # Tool registry
        self.tools = ToolRegistry()

        # Documentation paths (set by subclass)
        self.doc_paths = {}
        self.web_doc_paths = {}
        self.docs_base_path = kwargs.get("docs_base_path", "")
        self.web_base_path = kwargs.get("web_base_path", "")

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

    def run(self):
        self.conversation = []
        self.codex_state = {"thread_id": None, "conv": []}

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
