from hpcagent.agent import HPCAgent
from hpcagent.core.llm import CLI_BACKENDS, PROVIDER_BASE_URLS


def main():
    import argparse

    known = sorted(PROVIDER_BASE_URLS.keys()) + sorted(CLI_BACKENDS)

    parser = argparse.ArgumentParser(description="hpcagent — HPC AI Agent")
    parser.add_argument("--backend", default=None, help=f"LLM provider ({', '.join(known)}, or 'custom')")
    parser.add_argument("--api-key", default=None, help="API key (or set <PROVIDER>_API_KEY env var)")
    parser.add_argument("--api-base-url", default=None, help="Base URL (required for custom backends)")
    parser.add_argument("--model", default=None, help="Model name")
    parser.add_argument("--system-prompt", default=None, help="System prompt file or string")
    parser.add_argument("--docs-base-path", default=None, help="Base path for doc tools")
    args = parser.parse_args()

    kwargs = {}
    for key in ("backend", "api_key", "api_base_url", "model", "docs_base_path"):
        val = getattr(args, key.replace("-", "_"), None)
        if val:
            kwargs[key] = val
    if args.system_prompt:
        with open(args.system_prompt) as f:
            kwargs["system_prompt"] = f.read()

    agent = HPCAgent(**kwargs)
    agent.run()


if __name__ == "__main__":
    main()
