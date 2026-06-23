from hpchpcagent.hpc.slurm import run_cli_command, is_fatal_command_error, normalize_null, safe_int
from collections import Counter
import re


def check_user_quota(user_id: str) -> str:
    cmd = ["quota", "-u", user_id]
    ok, output = run_cli_command(cmd, timeout=20)
    if not ok:
        return f"Error checking quota for {user_id}: {output}"
    return output if output else f"No quota information found for {user_id}"


def check_user_jobs(user_id: str) -> str:
    cmd = ["squeue", "-u", user_id]
    ok, output = run_cli_command(cmd, timeout=20)
    if not ok:
        return f"Error checking jobs for {user_id}: {output}"
    return output if output else f"No jobs found for user {user_id}"


def check_pi_balance(account_name: str) -> str:
    candidates = [account_name]
    if account_name.startswith("pi-"):
        candidates.append(account_name[3:])
    else:
        candidates.insert(0, f"pi-{account_name}")
    for name in candidates:
        cmd = ["accounts", "balance", "-a", name]
        ok, output = run_cli_command(cmd, timeout=25)
        if ok:
            if "doesn't exist" not in output.lower() and "no such account" not in output.lower():
                return output
        elif is_fatal_command_error(output):
            return f"Error checking account balance for {account_name}: {output}"
        else:
            continue
    return f"Could not find account balance for {account_name}"


def check_pi_allocations(account_name: str) -> str:
    candidates = [account_name]
    if account_name.startswith("pi-"):
        candidates.append(account_name[3:])
    else:
        candidates.insert(0, f"pi-{account_name}")
    for name in candidates:
        cmd = ["accounts", "allocations", "-a", name]
        ok, output = run_cli_command(cmd, timeout=25)
        if ok:
            if "doesn't exist" not in output.lower() and "no such account" not in output.lower():
                return output
        elif is_fatal_command_error(output):
            return f"Error checking allocations for {account_name}: {output}"
        else:
            continue
    return f"Could not find allocations for {account_name}"


def check_pi_storage(account_name: str) -> str:
    candidates = [account_name]
    if account_name.startswith("pi-"):
        candidates.append(account_name[3:])
    else:
        candidates.insert(0, f"pi-{account_name}")
    for name in candidates:
        cmd = ["accounts", "storage", "-a", name]
        ok, output = run_cli_command(cmd, timeout=25)
        if ok:
            if "doesn't exist" not in output.lower() and "no such account" not in output.lower():
                return output
        elif is_fatal_command_error(output):
            return f"Error checking storage allocations for {account_name}: {output}"
        else:
            continue
    return f"Could not find storage allocations for {account_name}"


def list_user_accounts(user_id: str = None) -> str:
    cmd = ["accounts", "list"]
    if user_id:
        cmd.extend(["-u", user_id])
    ok, output = run_cli_command(cmd, timeout=25)
    if not ok:
        return f"Error listing accounts: {output}"
    return output if output else "No accounts found"


def check_account_members(account_name: str) -> str:
    candidates = [account_name]
    if account_name.startswith("pi-"):
        candidates.append(account_name[3:])
    else:
        candidates.insert(0, f"pi-{account_name}")
    for name in candidates:
        cmd = ["accounts", "members", "-a", name]
        ok, output = run_cli_command(cmd, timeout=25)
        if ok:
            if "doesn't exist" not in output.lower() and "no such account" not in output.lower():
                return output
        elif is_fatal_command_error(output):
            return f"Error checking members for {account_name}: {output}"
        else:
            continue
    return f"Could not find members for {account_name}"


def check_su_usage(account_name: str = None, user_id: str = None, partition: str = None) -> str:
    cmd = ["accounts", "usage"]
    if account_name:
        name = account_name if account_name.startswith("pi-") else f"pi-{account_name}"
        cmd.extend(["-a", name])
    if user_id:
        cmd.extend(["-u", user_id])
    if partition:
        cmd.extend(["-p", partition])
    ok, output = run_cli_command(cmd, timeout=30)
    if not ok:
        return f"Error checking usage: {output}"
    return output if output else "No usage data found"


def check_qos_info(partition: str = None) -> str:
    cmd = ["accounts", "qos"]
    if partition:
        cmd.extend(["-q", partition])
    ok, output = run_cli_command(cmd, timeout=25)
    if not ok:
        return f"Error getting QOS info: {output}"
    return output if output else "No QOS information found"


def check_recent_jobs(account_name: str = None, user_id: str = None) -> str:
    cmd = ["accounts", "jobs"]
    if account_name:
        name = account_name if account_name.startswith("pi-") else f"pi-{account_name}"
        cmd.extend(["-a", name])
    if user_id:
        cmd.extend(["-u", user_id])
    ok, output = run_cli_command(cmd, timeout=30)
    if not ok:
        return f"Error getting job records: {output}"
    return output if output else "No recent jobs found"


def check_low_balance_accounts() -> str:
    cmd = ["accounts", "checkbalance"]
    ok, output = run_cli_command(cmd, timeout=25)
    if not ok:
        return f"Error checking balances: {output}"
    return output if output else "No low balance accounts found"


def get_partition_info(partition: str = None) -> str:
    if partition:
        if partition.lower() == "shared":
            cmd = ["rcchelp", "sinfo", "shared"]
        else:
            cmd = ["rcchelp", "sinfo", "-p", partition]
    else:
        cmd = ["sinfo", "-o", "%P %a %l %D %T %N"]
    ok, output = run_cli_command(cmd, timeout=25)
    if not ok:
        return f"Error getting partition info: {output}"
    return output if output else "No partition information found"


def check_jobs_by_partition(partition: str) -> str:
    cmd = ["squeue", "-p", partition]
    ok, output = run_cli_command(cmd, timeout=20)
    if not ok:
        return f"Error checking jobs in partition {partition}: {output}"
    if output:
        lines_list = output.split('\n')
        running = sum(1 for line in lines_list[1:] if ' R ' in line)
        pending = sum(1 for line in lines_list[1:] if ' PD ' in line)
        summary = f"Summary: {running} running, {pending} pending jobs in partition '{partition}'\n\n"
        return summary + output
    return f"No jobs found in partition {partition}"


def check_account_jobs(account_name: str, partition: str = None) -> str:
    candidates = [account_name]
    if account_name.startswith("pi-"):
        candidates.append(account_name[3:])
    else:
        candidates.insert(0, f"pi-{account_name}")
    seen = set()
    ordered_candidates = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            ordered_candidates.append(candidate)
            seen.add(candidate)
    last_ok_name = None
    last_error = None
    for name in ordered_candidates:
        cmd = ["squeue", "-h", "-A", name]
        if partition:
            cmd.extend(["-p", partition])
        cmd.extend(["-o", "%i|%u|%T|%P|%R|%j"])
        ok, output = run_cli_command(cmd, timeout=25)
        if ok:
            last_ok_name = name
            if output.strip():
                rows = []
                state_counts = Counter()
                user_counts = Counter()
                pending_reason_counts = Counter()
                unavailable_node_counts = Counter()
                stuck_jobs = []
                for raw_line in output.splitlines():
                    if not raw_line.strip():
                        continue
                    parts = raw_line.split("|", 5)
                    if len(parts) != 6:
                        continue
                    job_id, user_id, state, job_partition, reason_or_node, job_name = [part.strip() for part in parts]
                    rows.append((job_id, user_id, state, job_partition, reason_or_node, job_name))
                    state_counts[state] += 1
                    user_counts[user_id] += 1
                    if state.upper() == "PENDING":
                        normalized_reason = reason_or_node
                        if normalized_reason.startswith("(") and normalized_reason.endswith(")"):
                            normalized_reason = normalized_reason[1:-1]
                        pending_reason_counts[normalized_reason] += 1
                        unavailable_match = re.search(r"ReqNodeNotAvail,\s*UnavailableNodes:([^)]+)", normalized_reason)
                        if unavailable_match:
                            unavailable_nodes = unavailable_match.group(1).strip()
                            unavailable_node_counts[unavailable_nodes] += 1
                            stuck_jobs.append((job_id, user_id, job_name, unavailable_nodes))
                if not rows:
                    return output
                partition_label = partition if partition else "all partitions"
                summary_lines = [
                    f"Account queue for '{name}' on {partition_label}:",
                    f"- Total queued/running jobs: {len(rows)}",
                    f"- Running: {state_counts.get('RUNNING', 0)}",
                    f"- Pending: {state_counts.get('PENDING', 0)}",
                ]
                other_states = {s: count for s, count in state_counts.items() if s not in {"RUNNING", "PENDING"}}
                if other_states:
                    summary_lines.append("- Other states: " + ", ".join(f"{s}={count}" for s, count in sorted(other_states.items())))
                if user_counts:
                    top_users = ", ".join(f"{uid} ({count})" for uid, count in user_counts.most_common(8))
                    summary_lines.append(f"- Distinct submitting users: {len(user_counts)}")
                    summary_lines.append(f"- Top users sharing this account: {top_users}")
                if pending_reason_counts:
                    top_reasons = ", ".join(f"{reason} ({count})" for reason, count in pending_reason_counts.most_common(6))
                    summary_lines.append(f"- Top pending reasons: {top_reasons}")
                if stuck_jobs:
                    summary_lines.append(f"- Stuck pending jobs pinned to unavailable nodes: {len(stuck_jobs)}")
                    summary_lines.append("- Unavailable nodes consuming account slots: " + ", ".join(
                        f"{node} ({count})" for node, count in unavailable_node_counts.most_common(6)))
                    example_jobs = ", ".join(f"{job_id}:{uid}->{node}" for job_id, uid, _, node in stuck_jobs[:8])
                    summary_lines.append(f"- Example stuck jobs: {example_jobs}")
                summary_lines.append("- This includes all users sharing the account, not just the requesting user.")
                header = f"{'JOBID':<10} {'USER':<12} {'STATE':<10} {'PARTITION':<10} {'REASON/NODE':<45} NAME"
                table_lines = [header, "-" * len(header)]
                for job_id, user_id, state, job_partition, reason_or_node, job_name in rows:
                    reason_display = reason_or_node[:45]
                    table_lines.append(f"{job_id:<10} {user_id[:12]:<12} {state[:10]:<10} {job_partition[:10]:<10} {reason_display:<45} {job_name}")
                return "\n".join(summary_lines) + "\n\n" + "\n".join(table_lines)
        elif is_fatal_command_error(output):
            return f"Error checking jobs for account {account_name}: {output}"
        else:
            last_error = output
    partition_msg = f" on partition '{partition}'" if partition else ""
    if last_ok_name is not None:
        return f"No queued or running jobs found for account {account_name}{partition_msg}"
    if last_error:
        return f"Error checking jobs for account {account_name}: {last_error}"
    return f"Could not check jobs for account {account_name}{partition_msg}"


def check_jobs_by_node(node_name: str) -> str:
    from hpchpcagent.hpc.nodes import check_node_hardware
    hardware_summary = check_node_hardware(node_name)
    cmd = ["squeue", "-w", node_name]
    ok, output = run_cli_command(cmd, timeout=20)
    if not ok:
        error_msg = f"Error checking jobs on node {node_name}: {output}"
        return error_msg + f"\n\nNode hardware details:\n{hardware_summary}"
    if output:
        lines_list = output.split('\n')
        job_count = len(lines_list) - 1 if len(lines_list) > 1 else 0
        summary = f"Summary: {job_count} jobs running on node '{node_name}'\n\n"
        return summary + output + f"\n\nNode hardware details:\n{hardware_summary}"
    return f"No jobs found on node {node_name}\n\nNode hardware details:\n{hardware_summary}"


def get_allocation_cycles() -> str:
    cmd = ["accounts", "cycles"]
    ok, output = run_cli_command(cmd, timeout=20)
    if not ok:
        return f"Error getting cycles: {output}"
    return output if output else "No cycles found"


def get_current_user() -> str:
    ok, output = run_cli_command(["whoami"], timeout=10)
    if not ok:
        return f"Error getting current user: {output}"
    username = output.strip()
    if username:
        return f"Current user: {username}"
    return "Error: Could not determine current user"
