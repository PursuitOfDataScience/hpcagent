import os
import sys

from hpcpilot.agent import HPCPilot
from hpcpilot.core.llm import (
    CLI_BACKENDS,
    PROVIDER_BASE_URLS,
    PROVIDER_DEFAULT_MODELS,
    PROVIDER_ENV_KEYS,
    PROVIDER_MODELS,
    discover_models,
)


def main():
    import argparse

    known = sorted(PROVIDER_BASE_URLS.keys()) + sorted(CLI_BACKENDS)

    parser = argparse.ArgumentParser(description="hpcpilot — HPC AI Agent")
    parser.add_argument("--backend", default=None, help=f"LLM provider ({', '.join(known)}, or 'custom')")
    parser.add_argument("--api-key", default=None, help="API key (or set <PROVIDER>_API_KEY env var)")
    parser.add_argument("--api-base-url", default=None, help="Base URL (required for custom backends)")
    parser.add_argument("--model", default=None, help="Model name")
    parser.add_argument("--system-prompt", default=None, help="Path to system prompt file")
    parser.add_argument("--system-prompt-append", default=None, help="Appended system prompt text")
    parser.add_argument("--docs-base-path", default=None, help="Base path for doc tools")
    parser.add_argument("--docs-url", default=None,
                        help="Docs-site URL to mirror into a local cache the agent can read")
    parser.add_argument("--version", action="store_true", help="Show version and exit")
    parser.add_argument("--list-models", default=None, nargs="?", const="opencode",
                        help="List available models for a provider (default: opencode)")
    parser.add_argument("--list-tools", action="store_true", help="List registered HPC tools")
    parser.add_argument("--config-path", default=None, help="Path to config file")
    parser.add_argument("--dangerous-bypass", action="store_true",
                        help="Allow mutating tools without confirmation")
    parser.add_argument("--effort", default=None, choices=["low", "medium", "high", "none"],
                        help="Reasoning effort level (low, medium, high, none)")
    args = parser.parse_args()

    if args.version:
        try:
            from importlib.metadata import version as imv
            print(f"hpcpilot {imv('hpcpilot')}")
        except Exception:
            print("hpcpilot 0.1.0")
        sys.exit(0)

    if args.list_models:
        provider = args.list_models
        # Resolve API key from environment for live discovery if possible
        env_var = PROVIDER_ENV_KEYS.get(provider, "")
        api_key = os.environ.get(env_var, "") if env_var else ""
        try:
            models = discover_models(provider, api_key=api_key)
        except Exception:
            models = []
        if not models:
            models = PROVIDER_MODELS.get(provider, [])
        default = PROVIDER_DEFAULT_MODELS.get(provider, "")
        if models:
            print(f"Models for {provider}:")
            for m in sorted(models):
                marker = " (default)" if m == default else ""
                print(f"  - {m}{marker}")
        else:
            print(f"No models found for {provider}. Try setting --model or running without --list-models.")
        sys.exit(0)

    if args.list_tools:
        agent = HPCPilot(**{"backend": "opencode", "api_key": ""})
        print("Registered HPC tools:")
        for schema in agent.tools.get_schemas():
            risk = agent.tools.get_risk(schema["name"]).value if hasattr(agent.tools, "get_risk") else "read-only"
            print(f"  - {schema['name']}: {schema['description']} [{risk}]")
        sys.exit(0)

    kwargs = {}
    for key in ("backend", "api_key", "api_base_url", "model", "docs_base_path",
                "docs_url", "system_prompt_append", "config_path", "dangerous_bypass", "effort"):
        val = getattr(args, key.replace("-", "_"), None)
        if val is not None:
            kwargs[key] = val
    if args.system_prompt:
        try:
            with open(args.system_prompt) as f:
                kwargs["system_prompt"] = f.read()
        except FileNotFoundError:
            print(f"Error: system prompt file not found: {args.system_prompt}", file=sys.stderr)
            sys.exit(1)

    agent = HPCPilot(**kwargs)

    # A --docs-url was passed: use the existing mirror if present, else build it.
    if args.docs_url:
        from hpcpilot.core.docfetch import load_manifest, mirror_dir_for
        dest = mirror_dir_for(args.docs_url)
        if load_manifest(dest):
            agent.docs_url = args.docs_url
            agent.docs_base_path = dest
            agent._load_all_configured_skills()
        else:
            agent._mirror_docs_url(args.docs_url)

    # Auto-load skills from docs base path
    if agent.docs_base_path:
        skills_path = os.path.join(agent.docs_base_path, "skills.md")
        if os.path.isfile(skills_path):
            try:
                agent.load_skills(skills_path)
            except Exception:
                pass

    agent.run()


if __name__ == "__main__":
    main()
