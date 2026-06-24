#!/usr/bin/env python3
"""Self-animating hpcpilot demo for recording a README GIF.

Renders a scripted session using hpcpilot's *real* UI components (banner,
streaming markdown renderer, tool-status line), so the recording looks exactly
like the app. Used by ``assets/demo.tape`` with `vhs`; run it directly to
preview:  python scripts/demo.py
"""

import sys
import time

from hpcpilot.agent import DEFAULT_BANNER
from hpcpilot.core.ui import StreamRenderer, c, print_banner, tool_status


def _sleep(s):
    time.sleep(s)


def type_prompt(text, cps=0.035):
    sys.stdout.write(f"{c.CYAN}❯{c.RESET} ")
    sys.stdout.flush()
    for ch in text:
        sys.stdout.write(ch)
        sys.stdout.flush()
        _sleep(cps)
    print()
    _sleep(0.35)


def stream(text, chunk=3, delay=0.012):
    """Stream markdown through the real renderer, like a model response."""
    print(f"{c.TEAL}{c.BOLD}●{c.RESET} ", end="", flush=True)
    r = StreamRenderer()
    for i in range(0, len(text), chunk):
        r.process_chunk(text[i:i + chunk])
        _sleep(delay)
    r.flush()
    print()


def tool(name, work=0.6):
    tool_status(name, status="running")
    _sleep(work)
    tool_status(name, status="success")
    print()


def main():
    print_banner(DEFAULT_BANNER, model="gemini-2.5-flash", effort="medium", animate=True)
    _sleep(0.6)

    # ── 1) Diagnose a pending job ───────────────────────────────────────────
    type_prompt("why is job 1837465 pending?")
    tool("predict_pending_job_wait")
    tool("read_slurm_partitions")
    stream(
        "Job **1837465** is pending in the `gpu` partition. Reason: **QOSMaxGRES** "
        "— your account is at its concurrent GPU limit.\n\n"
        "- Estimated start: **~3h 20m** (3 jobs ahead in your QOS)\n"
        "- Free it sooner: cancel one of your two running `gpu` jobs, or submit to "
        "`gpu-shared`, which currently has **6 idle A100s**.\n"
    )
    _sleep(0.8)

    # ── 2) Pull in the cluster's own docs by URL ────────────────────────────
    type_prompt("/docs add  https://docs.rcc.uchicago.edu/")
    for i, page in enumerate(
        ["slurm/sbatch", "slurm/partitions", "storage/main", "software/apps-and-envs"], 1
    ):
        sys.stdout.write(
            f"\r{c.GRAY}Mirroring docs [{i*17}/70] "
            f"https://docs.rcc.uchicago.edu/{page}\033[K{c.RESET}"
        )
        sys.stdout.flush()
        _sleep(0.5)
    print(f"\r{c.GREEN}✓ Mirrored 70 pages — ready offline\033[K{c.RESET}")
    _sleep(0.7)

    # ── 3) Answer from the mirrored guide ───────────────────────────────────
    type_prompt("how do I request a GPU node here?")
    tool("read_slurm_sbatch")
    stream(
        "From the RCC user guide, request a GPU with an `sbatch` directive:\n\n"
        "```bash\n"
        "#SBATCH --partition=gpu\n"
        "#SBATCH --gres=gpu:1        # 1 GPU; up to 4 per node\n"
        "#SBATCH --account=pi-yourpi\n"
        "```\n\n"
        "For a quick interactive session, use `sinteractive --gres=gpu:1 -p gpu`.\n"
    )
    _sleep(1.0)

    type_prompt("/exit")
    print(f"{c.GREEN}Goodbye!{c.RESET}")
    _sleep(0.8)


if __name__ == "__main__":
    main()
