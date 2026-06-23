from hpcagent.hpc.nodes import fetch_cluster_nodes
from hpcagent.hpc.slurm import (
    UNAVAILABLE_NODE_STATES,
    extract_token,
    gpu_type_matches,
    normalize_node_state,
    normalize_null,
    normalize_partition_name,
    parse_mem_to_mb,
    parse_slurm_time_to_minutes,
    parse_tres_memory_mb,
    percentile,
    run_cli_command,
    safe_int,
)


def parse_requested_gpu_spec(text: str) -> tuple:
    value = normalize_null(text).lower()
    if not value:
        return 0, ""
    total = 0
    gpu_type = ""
    patterns = (
        r"gres/gpu(?::([a-z0-9._-]+))?=(\d+)",
        r"gpu(?::([a-z0-9._-]+))?:(\d+)",
    )
    for pattern in patterns:
        for match in __import__('re').finditer(pattern, value):
            candidate = (match.group(1) or "").replace("_", "-").upper()
            if candidate and candidate not in {"GPU", "MPS", "SHARD"} and not candidate.isdigit() and not gpu_type:
                gpu_type = candidate
            total += int(match.group(2))
    return total, gpu_type


def format_eta_minutes(minutes: float) -> str:
    if minutes is None:
        return "unknown"
    if minutes <= 0:
        return "ready now"
    rounded = max(1, int(round(minutes)))
    days = rounded // 1440
    hours = (rounded % 1440) // 60
    mins = rounded % 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins and days == 0:
        parts.append(f"{mins}m")
    return "~" + " ".join(parts[:2])


def format_memory_gb(memory_mb: int) -> str:
    if memory_mb <= 0:
        return "unknown"
    value = memory_mb / 1024.0
    rounded = round(value, 1)
    if abs(rounded - round(rounded)) < 0.05:
        return f"{int(round(rounded))} GB"
    return f"{rounded:.1f} GB"


def slurm_job_exists(job_id: str) -> bool:
    ok, out = run_cli_command(["scontrol", "show", "job", job_id], timeout=20)
    if not ok:
        return False
    return bool(__import__('re').search(rf"JobId\s*=\s*{job_id}", out))


def extend_slurm_job(job_id: str, time_limit: str) -> str:
    if not slurm_job_exists(job_id):
        return f"Error: Job {job_id} does not exist or is not yours."
    cmd = ["scontrol", "update", f"JobID={job_id}", f"TimeLimit={time_limit}"]
    ok, output = run_cli_command(cmd, timeout=20)
    if not ok:
        return f"SLURM Error: {output}"
    return f"Successfully extended job {job_id} to {time_limit}.\n{output}" if output else f"Successfully extended job {job_id} to {time_limit}."


def get_job_details(job_id: str) -> str:
    cmd = ["scontrol", "show", "job", job_id]
    ok, output = run_cli_command(cmd, timeout=20)
    if not ok:
        return f"Error getting details for job {job_id}: {output}"
    return output if output else f"No details found for job {job_id}"


def parse_job_prediction_request(job_id: str) -> tuple:
    ok, output = run_cli_command(["scontrol", "show", "job", job_id], timeout=20)
    if not ok:
        return None, f"Error getting details for job {job_id}: {output}"
    import re
    job_line = " ".join(output.split())
    if not re.search(rf"\bJobId={re.escape(str(job_id))}\b", job_line):
        return None, f"No details found for job {job_id}"
    partition = normalize_partition_name(extract_token(job_line, "Partition"))
    if not partition:
        return None, f"Could not determine a partition for job {job_id}"
    nodes = max(1, safe_int(extract_token(job_line, "NumNodes")))
    total_cpus = max(1, safe_int(extract_token(job_line, "NumCPUs")))
    cpus_per_node = safe_int(extract_token(job_line, "MinCPUsNode"))
    if cpus_per_node <= 0:
        cpus_per_node = max(1, (total_cpus + nodes - 1) // nodes)
    min_mem_node_mb = parse_mem_to_mb(extract_token(job_line, "MinMemoryNode"))
    min_mem_cpu_mb = parse_mem_to_mb(extract_token(job_line, "MinMemoryCPU"))
    total_mem_mb = parse_tres_memory_mb(extract_token(job_line, "TRES"))
    memory_per_node_mb = min_mem_node_mb
    if memory_per_node_mb <= 0 and min_mem_cpu_mb > 0:
        memory_per_node_mb = min_mem_cpu_mb * cpus_per_node
    if memory_per_node_mb <= 0 and total_mem_mb > 0:
        memory_per_node_mb = max(1, (total_mem_mb + nodes - 1) // nodes)
    gpus_per_node, gpu_type = parse_requested_gpu_spec(extract_token(job_line, "TresPerNode"))
    if gpus_per_node <= 0:
        gpus_per_node, gpu_type = parse_requested_gpu_spec(extract_token(job_line, "ReqGRES"))
    if gpus_per_node <= 0:
        total_gpus, total_gpu_type = parse_requested_gpu_spec(extract_token(job_line, "TRES"))
        if total_gpus > 0:
            gpus_per_node = max(1, (total_gpus + nodes - 1) // nodes)
            gpu_type = total_gpu_type
    user_token = extract_token(job_line, "UserId")
    user_name = user_token.split("(", 1)[0] if user_token else ""
    runtime = extract_token(job_line, "RunTime")
    time_limit = extract_token(job_line, "TimeLimit")
    return {
        "job_id": str(job_id), "job_name": extract_token(job_line, "JobName"),
        "user": user_name, "partition": partition,
        "state": normalize_null(extract_token(job_line, "JobState")).upper(),
        "reason": normalize_null(extract_token(job_line, "Reason")),
        "runtime": runtime, "time_limit": time_limit,
        "remaining_minutes": max(0.0, (parse_slurm_time_to_minutes(time_limit) - parse_slurm_time_to_minutes(runtime)
                                          if parse_slurm_time_to_minutes(time_limit) > 0 else 30.0)),
        "nodes": nodes, "total_cpus": total_cpus, "cpus_per_node": cpus_per_node,
        "memory_per_node_mb": memory_per_node_mb, "gpus_per_node": gpus_per_node,
        "gpu_type": gpu_type, "req_node_list": normalize_null(extract_token(job_line, "ReqNodeList")),
    }, None


def fetch_partition_prediction_jobs(partition: str) -> tuple:
    cmd = ["squeue", "-h", "-p", partition, "-o", "%i|%t|%M|%l|%D|%j"]
    ok, output = run_cli_command(cmd, timeout=25)
    if not ok:
        return None, f"Error checking jobs in partition {partition}: {output}"
    jobs = []
    for raw_line in output.splitlines():
        if not raw_line.strip():
            continue
        parts = raw_line.split("|", 5)
        if len(parts) != 6:
            continue
        job_id, state, runtime, time_limit, nodes, name = (part.strip() for part in parts)
        jobs.append({"job_id": job_id, "state": state, "runtime": runtime,
                      "time_limit": time_limit, "nodes": max(1, safe_int(nodes)), "name": name})
    return jobs, None


def prediction_node_can_ever_fit(node: dict, job_request: dict) -> bool:
    cpu_capacity = max(safe_int(node.get("cpu_total")), safe_int(node.get("cpus")))
    memory_capacity = safe_int(node.get("memory_mb"))
    gpu_capacity = safe_int(node.get("gpu_total"))
    if cpu_capacity < job_request["cpus_per_node"]:
        return False
    if memory_capacity < job_request["memory_per_node_mb"]:
        return False
    requested_gpus = job_request["gpus_per_node"]
    if requested_gpus > 0:
        if gpu_capacity < requested_gpus:
            return False
        if not gpu_type_matches(node.get("gpu_type", ""), job_request.get("gpu_type", "")):
            return False
    return True


def prediction_node_can_run_now(node: dict, job_request: dict) -> bool:
    if normalize_node_state(node.get("state", "")) in UNAVAILABLE_NODE_STATES:
        return False
    if not prediction_node_can_ever_fit(node, job_request):
        return False
    cpu_total = max(safe_int(node.get("cpu_total")), safe_int(node.get("cpus")))
    cpu_free = max(0, cpu_total - safe_int(node.get("cpu_alloc")))
    if cpu_free < job_request["cpus_per_node"]:
        return False
    requested_gpus = job_request["gpus_per_node"]
    if requested_gpus > 0:
        gpu_free = max(0, safe_int(node.get("gpu_total")) - safe_int(node.get("gpu_alloc")))
        if gpu_free < requested_gpus:
            return False
    memory_total = safe_int(node.get("memory_mb"))
    memory_alloc = node.get("memory_alloc_mb")
    if memory_alloc is not None:
        schedulable_mb = max(0, memory_total - safe_int(memory_alloc))
        return schedulable_mb >= job_request["memory_per_node_mb"]
    return normalize_node_state(node.get("state", "")) == "idle" and memory_total >= job_request["memory_per_node_mb"]


def estimate_pending_job_wait_minutes(nodes_required: int, runnable_now_nodes: int, running_jobs: list, competing_pending_nodes: int) -> tuple:
    if runnable_now_nodes >= nodes_required:
        return 0.0, 0.0
    nodes_short = nodes_required - runnable_now_nodes
    release_events = []
    for job in running_jobs:
        time_limit_minutes = parse_slurm_time_to_minutes(job.get("time_limit", ""))
        runtime_minutes = parse_slurm_time_to_minutes(job.get("runtime", ""))
        if time_limit_minutes <= 0:
            remaining = 30.0
        else:
            remaining = max(0.0, time_limit_minutes - runtime_minutes)
        release_events.extend([remaining] * max(1, safe_int(job.get("nodes"))))
    release_events.sort()
    if not release_events:
        return None, None
    target = competing_pending_nodes + nodes_short
    if target <= len(release_events):
        window = release_events[:target]
        return percentile(window, 50), percentile(window, 75)
    positive = [value for value in release_events if value > 0]
    if not positive:
        return None, None
    avg = sum(positive) / len(positive)
    extra = target - len(release_events)
    cycles = extra / max(1, len(release_events))
    tail = release_events[-1] + (cycles * avg)
    p50 = release_events[-1] + (cycles * percentile(positive, 50))
    p75 = tail * 1.25
    return p50, p75


def predict_pending_job_wait(job_id: str, use_scontrol: bool = True) -> str:
    from hpcagent.hpc.slurm import coerce_bool
    clean_job_id = normalize_null(job_id)
    if not clean_job_id:
        return "Error: job_id is required."
    job_request, error = parse_job_prediction_request(clean_job_id)
    if error:
        return error
    state = job_request["state"]
    if state != "PENDING":
        if state == "RUNNING":
            lines = [
                "Pending job wait prediction (live Slurm)",
                f"Job {clean_job_id} is not pending; current state: {state}.",
                f"Partition: {job_request['partition']}",
                f"Runtime: {job_request['runtime'] or 'unknown'}",
                f"Time limit: {job_request['time_limit'] or 'unknown'}",
                f"Remaining wall-clock time by time limit: {format_eta_minutes(job_request['remaining_minutes'])}",
            ]
            return "\n".join(lines)
        return f"Job {clean_job_id} is not pending; current state: {state or 'unknown'}."
    partition = job_request["partition"]
    scontrol_enabled = coerce_bool(use_scontrol, default=True)
    try:
        nodes, command, scontrol_ok, scontrol_message = fetch_cluster_nodes([partition], use_scontrol=scontrol_enabled)
    except RuntimeError as e:
        return f"Error collecting live node data for partition {partition}: {e}"
    if not nodes:
        return f"No nodes found for partition '{partition}'."
    partition_jobs, queue_error = fetch_partition_prediction_jobs(partition)
    if queue_error:
        return queue_error
    candidate_nodes = [node for node in nodes if prediction_node_can_ever_fit(node, job_request)]
    runnable_nodes = [node for node in candidate_nodes if prediction_node_can_run_now(node, job_request)]
    running_jobs = [job for job in partition_jobs if job["job_id"] != clean_job_id and job.get("state") != "PD"]
    pending_jobs = [job for job in partition_jobs if job["job_id"] != clean_job_id and job.get("state") == "PD"]
    competing_pending_nodes = sum(max(1, safe_int(job.get("nodes"))) for job in pending_jobs)
    eta_p50, eta_p75 = estimate_pending_job_wait_minutes(
        nodes_required=job_request["nodes"], runnable_now_nodes=len(runnable_nodes),
        running_jobs=running_jobs, competing_pending_nodes=competing_pending_nodes,
    )
    available_nodes = [node for node in nodes if normalize_node_state(node.get("state", "")) not in UNAVAILABLE_NODE_STATES]
    cpu_total = sum(max(safe_int(node.get("cpu_total")), safe_int(node.get("cpus"))) for node in available_nodes)
    cpu_alloc = sum(safe_int(node.get("cpu_alloc")) for node in available_nodes)
    utilization_pct = round((cpu_alloc * 100.0) / cpu_total, 1) if cpu_total > 0 else 0.0
    if len(runnable_nodes) >= job_request["nodes"]:
        eta_summary = "ready now from a resource-fit perspective"
    elif eta_p50 is not None:
        eta_summary = f"{format_eta_minutes(eta_p50)} to {format_eta_minutes(eta_p75)}"
    else:
        eta_summary = "unknown"
    lines = [
        "Pending job wait prediction (live Slurm)",
        f"Job: {clean_job_id}" + (f" ({job_request['job_name']})" if job_request.get("job_name") else ""),
        f"User: {job_request['user'] or 'unknown'}", f"Partition: {partition}",
        f"Pending reason: {job_request['reason'] or 'unknown'}", "",
        "Requested resources",
        f"- Nodes: {job_request['nodes']}",
        f"- CPU per node: {job_request['cpus_per_node']} (total CPUs: {job_request['total_cpus']})",
        f"- Memory per node: {format_memory_gb(job_request['memory_per_node_mb'])}",
    ]
    if job_request["gpus_per_node"] > 0:
        gpu_label = f"{job_request['gpus_per_node']} GPU/node"
        if job_request.get("gpu_type"):
            gpu_label += f" ({job_request['gpu_type']})"
        lines.append(f"- GPU request: {gpu_label}")
    else:
        lines.append("- GPU request: none")
    lines.extend(["", "Live partition snapshot",
        f"- Matching nodes for this request: {len(candidate_nodes)}/{len(nodes)}",
        f"- Nodes that could run it now: {len(runnable_nodes)}",
        f"- Partition CPU utilization: {utilization_pct:.1f}%",
        f"- Other running jobs in partition: {len(running_jobs)}",
        f"- Other pending jobs in partition: {len(pending_jobs)}",
        f"- Other pending node demand: {competing_pending_nodes}", "",
        "Estimated wait", f"- ETA: {eta_summary}",
    ])
    if eta_p50 is not None and len(runnable_nodes) < job_request["nodes"]:
        lines.append(f"- Median ETA (p50): {format_eta_minutes(eta_p50)}")
        lines.append(f"- Conservative ETA (p75): {format_eta_minutes(eta_p75)}")
    lines.extend(["", "Notes",
        "- Estimate is based on current compatible-node fit, other pending node demand, and remaining wall time of running jobs.",
        "- Slurm priority and policy decisions can move the actual start earlier or later than this resource-based ETA.",
    ])
    if not scontrol_ok:
        lines.append(f"- Exact schedulable-memory checks are degraded because scontrol enrichment is unavailable ({scontrol_message}).")
    if len(candidate_nodes) < job_request["nodes"]:
        lines.append("- The live node snapshot shows fewer matching nodes than the job requests, so the wait may be dominated by constraints not visible in this simplified predictor.")
    if job_request.get("req_node_list"):
        lines.append(f"- Job is constrained to requested nodes '{job_request['req_node_list']}', which can make the real wait longer than the partition-wide estimate.")
    lines.append(f"- Live node command: {command}")
    return "\n".join(lines)
