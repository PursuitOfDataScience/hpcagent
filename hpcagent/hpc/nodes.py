import os
import sqlite3
import subprocess
from collections import Counter

from hpcagent.hpc.slurm import (
    BUSY_NODE_STATES,
    NODE_STATE_SEVERITY,
    canonical_node_name,
    coerce_bool,
    detect_gpu_type,
    extract_token,
    is_gpu_node,
    merge_node_state,
    normalize_node_state,
    normalize_null,
    parse_gpu_total_from_gres,
    parse_tres_value,
    pct,
    safe_int,
    split_csv,
)

NODE_MONITOR_DB_DEFAULT_PATH = "/project/rcc/youzhi/slurm_node_monitor.db"


def new_node_hardware_payload(node_name: str) -> dict:
    return {
        "node_name": node_name,
        "partitions": set(),
        "partition_name": "",
        "state": "",
        "cpus": 0,
        "memory_mb": 0,
        "features": "",
        "gres": "",
        "cpu_alloc": 0,
        "cpu_total": 0,
        "gpu_alloc": 0,
        "gpu_total": 0,
        "gpu_type": "",
        "sources": [],
        "errors": [],
        "has_data": False,
        "is_gpu": False,
        "node_type": "Unknown",
    }


def merge_node_hardware(base: dict, incoming: dict) -> None:
    if not incoming:
        return
    incoming_partitions = incoming.get("partitions", set())
    if incoming_partitions:
        base["partitions"].update(incoming_partitions)
    base["state"] = merge_node_state(base.get("state", ""), incoming.get("state", ""))
    base["cpus"] = max(base.get("cpus", 0), incoming.get("cpus", 0))
    base["memory_mb"] = max(base.get("memory_mb", 0), incoming.get("memory_mb", 0))
    incoming_features = normalize_null(incoming.get("features", ""))
    if (not base.get("features") and incoming_features) or len(incoming_features) > len(base.get("features", "")):
        base["features"] = incoming_features
    incoming_gres = normalize_null(incoming.get("gres", ""))
    if (not base.get("gres") and incoming_gres) or len(incoming_gres) > len(base.get("gres", "")):
        base["gres"] = incoming_gres
    base["cpu_alloc"] = max(base.get("cpu_alloc", 0), incoming.get("cpu_alloc", 0))
    base["cpu_total"] = max(base.get("cpu_total", 0), incoming.get("cpu_total", 0))
    base["gpu_alloc"] = max(base.get("gpu_alloc", 0), incoming.get("gpu_alloc", 0))
    base["gpu_total"] = max(base.get("gpu_total", 0), incoming.get("gpu_total", 0))
    incoming_gpu_type = normalize_null(incoming.get("gpu_type", ""))
    if not base.get("gpu_type") and incoming_gpu_type:
        base["gpu_type"] = incoming_gpu_type
    for source in incoming.get("sources", []):
        if source not in base["sources"]:
            base["sources"].append(source)


_SUBPROCESS_TIMEOUT = 30


def collect_node_info_from_sinfo(node_name: str) -> tuple:
    command = ["sinfo", "-h", "-N", "-n", node_name, "-o", "%N|%P|%t|%c|%m|%f|%G"]
    try:
        output = subprocess.check_output(command, text=True, stderr=subprocess.STDOUT, timeout=_SUBPROCESS_TIMEOUT)
    except FileNotFoundError:
        return None, "sinfo command not found."
    except subprocess.CalledProcessError as e:
        detail = e.output.strip() if hasattr(e, "output") and e.output else str(e)
        return None, f"sinfo failed: {detail}"
    if not output.strip():
        return None, f"sinfo returned no data for node '{node_name}'."
    requested = canonical_node_name(node_name)
    info = new_node_hardware_payload(node_name)
    parsed_rows = 0
    for raw_line in output.splitlines():
        if not raw_line.strip():
            continue
        parts = raw_line.split("|", 6)
        if len(parts) != 7:
            continue
        name, partition, state, cpus, memory, features, gres = (part.strip() for part in parts)
        if name and requested and canonical_node_name(name) != requested:
            continue
        parsed_rows += 1
        if partition:
            partition = partition.rstrip("*")
            info["partitions"].update(split_csv(partition))
        info["state"] = merge_node_state(info["state"], state)
        info["cpus"] = max(info["cpus"], safe_int(cpus))
        info["memory_mb"] = max(info["memory_mb"], safe_int(memory))
        features = normalize_null(features)
        if (not info["features"] and features) or len(features) > len(info["features"]):
            info["features"] = features
        gres = normalize_null(gres)
        if (not info["gres"] and gres) or len(gres) > len(info["gres"]):
            info["gres"] = gres
    if parsed_rows == 0:
        return None, f"sinfo did not return a parsable row for node '{node_name}'."
    info["sources"].append("sinfo")
    return info, ""


def collect_node_info_from_scontrol(node_name: str) -> tuple:
    command = ["scontrol", "show", "node", node_name, "-o"]
    try:
        output = subprocess.check_output(command, text=True, stderr=subprocess.STDOUT, timeout=_SUBPROCESS_TIMEOUT)
    except FileNotFoundError:
        return None, "scontrol command not found."
    except subprocess.CalledProcessError as e:
        detail = e.output.strip() if hasattr(e, "output") and e.output else str(e)
        return None, f"scontrol failed: {detail}"
    if not output.strip():
        return None, f"scontrol returned no data for node '{node_name}'."
    requested = canonical_node_name(node_name)
    info = new_node_hardware_payload(node_name)
    parsed_rows = 0
    for line in output.splitlines():
        if not line.strip():
            continue
        raw_name = extract_token(line, "NodeName")
        if raw_name and requested and canonical_node_name(raw_name) != requested:
            continue
        parsed_rows += 1
        partitions = split_csv(normalize_null(extract_token(line, "Partitions")))
        if partitions:
            info["partitions"].update(partitions)
        state = extract_token(line, "State")
        info["state"] = merge_node_state(info["state"], state)
        cpu_alloc = safe_int(extract_token(line, "CPUAlloc"))
        cpu_total = safe_int(extract_token(line, "CPUTot"))
        if cpu_total <= 0:
            cpu_total = safe_int(extract_token(line, "CPUEfctv"))
        info["cpu_alloc"] = max(info["cpu_alloc"], cpu_alloc)
        info["cpu_total"] = max(info["cpu_total"], cpu_total)
        info["cpus"] = max(info["cpus"], cpu_total)
        real_memory = safe_int(extract_token(line, "RealMemory"))
        info["memory_mb"] = max(info["memory_mb"], real_memory)
        features_candidates = [
            normalize_null(extract_token(line, "Features")),
            normalize_null(extract_token(line, "ActiveFeatures")),
            normalize_null(extract_token(line, "AvailableFeatures")),
        ]
        merged_features = ",".join([feat for feat in features_candidates if feat])
        if (not info["features"] and merged_features) or len(merged_features) > len(info["features"]):
            info["features"] = merged_features
        gres = normalize_null(extract_token(line, "Gres"))
        if (not info["gres"] and gres) or len(gres) > len(info["gres"]):
            info["gres"] = gres
        alloc_tres = normalize_null(extract_token(line, "AllocTRES"))
        cfg_tres = normalize_null(extract_token(line, "CfgTRES"))
        info["gpu_alloc"] = max(info["gpu_alloc"], parse_tres_value(alloc_tres, "gres/gpu"))
        info["gpu_total"] = max(info["gpu_total"], parse_tres_value(cfg_tres, "gres/gpu"))
    if parsed_rows == 0:
        return None, f"scontrol did not return a parsable row for node '{node_name}'."
    info["sources"].append("scontrol")
    return info, ""


def collect_node_info_from_snapshot_db(node_name: str, db_path: str | None = None) -> tuple:
    if db_path is None:
        db_path = NODE_MONITOR_DB_DEFAULT_PATH
    db_path = os.path.expanduser(db_path)
    if not os.path.exists(db_path):
        return None, f"snapshot DB not found at {db_path}"
    short_name = normalize_null(node_name).split(".", 1)[0]
    query = """
        SELECT ns.name, ns.partition_name, ns.partitions, ns.state, ns.cpus, ns.memory_mb,
               ns.features, ns.gres, ns.is_gpu, ns.gpu_type, ns.cpu_alloc, ns.cpu_total,
               ns.gpu_alloc, ns.gpu_total, s.collected_at
        FROM node_states ns
        JOIN snapshots s ON s.id = ns.snapshot_id
        WHERE ns.name = ? OR ns.name = ?
        ORDER BY ns.snapshot_id DESC
        LIMIT 1
    """
    try:
        with sqlite3.connect(db_path, timeout=5.0) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(query, (node_name, short_name)).fetchone()
    except sqlite3.Error as e:
        return None, f"snapshot DB query failed: {e}"
    if not row:
        return None, f"node '{node_name}' not found in snapshot DB"
    info = new_node_hardware_payload(node_name)
    info["partitions"].update(split_csv(row["partitions"]))
    partition_name = normalize_null(row["partition_name"])
    if partition_name:
        info["partitions"].add(partition_name)
    info["state"] = normalize_node_state(row["state"])
    info["cpus"] = max(info["cpus"], safe_int(row["cpus"]))
    info["memory_mb"] = max(info["memory_mb"], safe_int(row["memory_mb"]))
    info["features"] = normalize_null(row["features"])
    info["gres"] = normalize_null(row["gres"])
    info["cpu_alloc"] = max(info["cpu_alloc"], safe_int(row["cpu_alloc"]))
    info["cpu_total"] = max(info["cpu_total"], safe_int(row["cpu_total"]))
    info["gpu_alloc"] = max(info["gpu_alloc"], safe_int(row["gpu_alloc"]))
    info["gpu_total"] = max(info["gpu_total"], safe_int(row["gpu_total"]))
    info["gpu_type"] = normalize_null(row["gpu_type"])
    snapshot_time = normalize_null(row["collected_at"])
    if snapshot_time:
        info["sources"].append(f"snapshot-db ({snapshot_time})")
    else:
        info["sources"].append("snapshot-db")
    return info, ""


def resolve_node_hardware(node_name: str, db_path: str | None = None) -> dict:
    clean_name = normalize_null(node_name)
    payload = new_node_hardware_payload(clean_name)
    if not clean_name:
        payload["errors"] = ["node_name is required."]
        return payload
    sinfo_info, sinfo_error = collect_node_info_from_sinfo(clean_name)
    if sinfo_info:
        merge_node_hardware(payload, sinfo_info)
    elif sinfo_error:
        payload["errors"].append(sinfo_error)
    scontrol_info, scontrol_error = collect_node_info_from_scontrol(clean_name)
    if scontrol_info:
        merge_node_hardware(payload, scontrol_info)
    elif scontrol_error:
        payload["errors"].append(scontrol_error)
    needs_db_fallback = (not payload["sources"]) or (not payload["gres"]) or (payload["gpu_total"] == 0)
    if needs_db_fallback:
        db_info, db_error = collect_node_info_from_snapshot_db(clean_name, db_path)
        if db_info:
            merge_node_hardware(payload, db_info)
        elif db_error and not payload["sources"]:
            payload["errors"].append(db_error)
    if payload["cpu_total"] <= 0 and payload["cpus"] > 0:
        payload["cpu_total"] = payload["cpus"]
    if payload["gpu_total"] <= 0:
        payload["gpu_total"] = parse_gpu_total_from_gres(payload["gres"])
    if payload["gpu_total"] > 0 and payload["gpu_alloc"] > payload["gpu_total"]:
        payload["gpu_alloc"] = payload["gpu_total"]
    partitions = sorted([part for part in payload["partitions"] if part])
    payload["partitions"] = partitions
    payload["partition_name"] = partitions[0] if partitions else ""
    partitions_csv = ",".join(partitions)
    if not payload["gpu_type"]:
        payload["gpu_type"] = detect_gpu_type(payload["features"], payload["gres"])
    payload["is_gpu"] = is_gpu_node(partitions_csv, payload["features"], payload["gres"], payload["gpu_total"])
    payload["node_type"] = "GPU node" if payload["is_gpu"] else "CPU-only node"
    payload["has_data"] = bool(
        payload["partitions"] or payload["state"] or payload["cpus"] > 0
        or payload["cpu_total"] > 0 or payload["memory_mb"] > 0
        or payload["features"] or payload["gres"] or payload["gpu_total"] > 0
    )
    return payload


def format_node_hardware_summary(payload: dict) -> str:
    if not payload:
        return ""
    lines = []
    if payload.get("is_gpu") and payload.get("gpu_type"):
        lines.append(f"Node type: {payload['node_type']} ({payload['gpu_type']})")
    else:
        lines.append(f"Node type: {payload.get('node_type', 'Unknown')}")
    if payload.get("gpu_total", 0) > 0:
        gpu_type_label = payload.get("gpu_type") or "GPU"
        lines.append(f"GPU allocation: {payload.get('gpu_alloc', 0)}/{payload.get('gpu_total', 0)} ({gpu_type_label})")
    elif payload.get("is_gpu"):
        gpu_type_label = payload.get("gpu_type") or "GPU"
        lines.append(f"GPU allocation: detected ({gpu_type_label}), count unavailable")
    else:
        lines.append("GPU allocation: none detected")
    partitions = payload.get("partitions", [])
    if partitions:
        lines.append(f"Partitions: {', '.join(partitions)}")
    if payload.get("state"):
        lines.append(f"State: {payload['state']}")
    cpu_total = payload.get("cpu_total", 0)
    cpu_alloc = payload.get("cpu_alloc", 0)
    cpus = payload.get("cpus", 0)
    if cpu_total > 0:
        lines.append(f"CPU allocation: {cpu_alloc}/{cpu_total}")
    elif cpus > 0:
        lines.append(f"CPUs: {cpus}")
    memory_mb = payload.get("memory_mb", 0)
    if memory_mb > 0:
        lines.append(f"Memory: {round(memory_mb / 1024.0, 1)} GB")
    if payload.get("features"):
        lines.append(f"Features: {payload['features']}")
    if payload.get("gres"):
        lines.append(f"GRES: {payload['gres']}")
    if payload.get("sources"):
        lines.append(f"Data source: {', '.join(payload['sources'])}")
    return "\n".join(lines)


def check_node_hardware(node_name: str, db_path: str | None = None) -> str:
    payload = resolve_node_hardware(node_name, db_path=db_path)
    if not payload.get("has_data"):
        details = "; ".join(payload.get("errors", []))
        if details:
            return f"No hardware information found for node '{node_name}'. Details: {details}"
        return f"No hardware information found for node '{node_name}'."
    lines = [
        f"Node '{payload.get('node_name', node_name)}' hardware summary:",
        format_node_hardware_summary(payload),
    ]
    if payload.get("errors"):
        lines.append(f"Warnings: {'; '.join(payload['errors'])}")
    return "\n".join([line for line in lines if line]).strip()


def parse_cpu_summary(raw: str) -> tuple:
    text = normalize_null(raw)
    if not text:
        return 0, 0, 0, 0
    parts = [part.strip() for part in text.split("/")]
    if len(parts) != 4:
        return 0, 0, 0, 0
    values = [safe_int(part) for part in parts]
    return values[0], values[1], values[2], values[3]


def fetch_scontrol_node_metrics(node_names: set) -> tuple:
    if not node_names:
        return {}, True, "no nodes requested"
    command = ["scontrol", "show", "node", "-o"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=_SUBPROCESS_TIMEOUT)
    except FileNotFoundError:
        return {}, False, "scontrol command not found."
    if result.returncode != 0:
        stderr = result.stderr.strip() or "(no stderr)"
        return {}, False, f"scontrol failed (exit {result.returncode}): {stderr}"
    metrics = {}
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        raw_name = extract_token(line, "NodeName")
        canonical_name = canonical_node_name(raw_name)
        if not canonical_name or canonical_name not in node_names:
            continue
        partitions = split_csv(normalize_null(extract_token(line, "Partitions")))
        cpu_alloc = safe_int(extract_token(line, "CPUAlloc"))
        cpu_total = safe_int(extract_token(line, "CPUTot"))
        if cpu_total <= 0:
            cpu_total = safe_int(extract_token(line, "CPUEfctv"))
        alloc_tres = normalize_null(extract_token(line, "AllocTRES"))
        cfg_tres = normalize_null(extract_token(line, "CfgTRES"))
        gpu_alloc = parse_tres_value(alloc_tres, "gres/gpu")
        gpu_total = parse_tres_value(cfg_tres, "gres/gpu")
        state = extract_token(line, "State")
        real_memory = safe_int(extract_token(line, "RealMemory"))
        alloc_memory = safe_int(extract_token(line, "AllocMem"))
        free_memory = safe_int(extract_token(line, "FreeMem"))
        features_candidates = [
            normalize_null(extract_token(line, "Features")),
            normalize_null(extract_token(line, "ActiveFeatures")),
            normalize_null(extract_token(line, "AvailableFeatures")),
        ]
        merged_features = ",".join([value for value in features_candidates if value])
        gres = normalize_null(extract_token(line, "Gres"))
        metrics[canonical_name] = {
            "partitions": set(partitions),
            "cpu_alloc": cpu_alloc, "cpu_total": cpu_total,
            "gpu_alloc": gpu_alloc, "gpu_total": gpu_total,
            "state": normalize_node_state(state),
            "memory_mb": real_memory, "memory_alloc_mb": alloc_memory, "memory_free_mb": free_memory,
            "features": merged_features, "gres": gres,
        }
    return metrics, True, "ok"


def fetch_cluster_nodes(partitions: list, use_scontrol: bool = True) -> tuple:
    command = ["sinfo", "-h", "-N", "-o", "%N|%P|%t|%c|%m|%f|%G|%C"]
    if partitions:
        command.extend(["-p", ",".join(partitions)])
    try:
        output = subprocess.check_output(command, text=True, stderr=subprocess.STDOUT, timeout=_SUBPROCESS_TIMEOUT)
    except FileNotFoundError:
        raise RuntimeError("sinfo command not found.")
    except subprocess.CalledProcessError as e:
        detail = e.output.strip() if hasattr(e, "output") and e.output else str(e)
        raise RuntimeError(f"sinfo failed: {detail}")

    merged = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 7)
        if len(parts) != 8:
            continue
        name, partition, state, cpus, memory, features, gres, cpu_summary = (part.strip() for part in parts)
        canonical_name = canonical_node_name(name)
        if not canonical_name:
            continue
        partition = partition.rstrip("*")
        partition_items = set(split_csv(partition)) if partition else set()
        normalized_state = normalize_node_state(state)
        features = normalize_null(features)
        gres = normalize_null(gres)
        cpus_int = safe_int(cpus)
        memory_mb = safe_int(memory)
        cpu_alloc, cpu_idle, cpu_other, cpu_total = parse_cpu_summary(cpu_summary)
        if cpu_total <= 0:
            cpu_total = cpus_int
        if canonical_name not in merged:
            merged[canonical_name] = {
                "name": canonical_name, "partitions": set(partition_items),
                "state": normalized_state, "cpus": cpus_int, "memory_mb": memory_mb,
                "memory_alloc_mb": None, "memory_free_mb": None,
                "features": features, "gres": gres,
                "cpu_alloc": cpu_alloc, "cpu_idle": cpu_idle, "cpu_other": cpu_other,
                "cpu_total": cpu_total, "gpu_alloc": 0,
                "gpu_total": parse_gpu_total_from_gres(gres), "gpu_type": "",
            }
            continue
        entry = merged[canonical_name]
        entry["partitions"].update(partition_items)
        entry["state"] = merge_node_state(entry["state"], normalized_state)
        entry["cpus"] = max(entry["cpus"], cpus_int)
        entry["memory_mb"] = max(entry["memory_mb"], memory_mb)
        if (not entry["features"] and features) or len(features) > len(entry["features"]):
            entry["features"] = features
        if (not entry["gres"] and gres) or len(gres) > len(entry["gres"]):
            entry["gres"] = gres
        entry["cpu_alloc"] = max(entry["cpu_alloc"], cpu_alloc)
        entry["cpu_idle"] = max(entry["cpu_idle"], cpu_idle)
        entry["cpu_other"] = max(entry["cpu_other"], cpu_other)
        entry["cpu_total"] = max(entry["cpu_total"], cpu_total)
        entry["gpu_total"] = max(entry["gpu_total"], parse_gpu_total_from_gres(gres))

    scontrol_metrics = {}
    scontrol_ok = False
    scontrol_message = "disabled by use_scontrol=False"
    if use_scontrol:
        scontrol_metrics, scontrol_ok, scontrol_message = fetch_scontrol_node_metrics(set(merged.keys()))

    nodes = []
    for canonical_name, entry in merged.items():
        info = scontrol_metrics.get(canonical_name)
        if info:
            entry["partitions"].update(info.get("partitions", set()))
            entry["state"] = merge_node_state(entry["state"], info.get("state", ""))
            entry["cpu_alloc"] = max(entry["cpu_alloc"], info.get("cpu_alloc", 0))
            if info.get("cpu_total", 0) > 0:
                entry["cpu_total"] = max(entry["cpu_total"], info["cpu_total"])
                entry["cpus"] = max(entry["cpus"], info["cpu_total"])
            if info.get("memory_mb", 0) > 0:
                entry["memory_mb"] = max(entry["memory_mb"], info["memory_mb"])
            if info.get("memory_alloc_mb") is not None:
                entry["memory_alloc_mb"] = max(0, info.get("memory_alloc_mb", 0))
            if info.get("memory_free_mb") is not None:
                entry["memory_free_mb"] = max(0, info.get("memory_free_mb", 0))
            info_features = normalize_null(info.get("features", ""))
            if (not entry["features"] and info_features) or len(info_features) > len(entry["features"]):
                entry["features"] = info_features
            info_gres = normalize_null(info.get("gres", ""))
            if (not entry["gres"] and info_gres) or len(info_gres) > len(entry["gres"]):
                entry["gres"] = info_gres
            entry["gpu_alloc"] = max(entry["gpu_alloc"], info.get("gpu_alloc", 0))
            entry["gpu_total"] = max(entry["gpu_total"], info.get("gpu_total", 0))
        if entry["cpu_total"] <= 0:
            entry["cpu_total"] = entry["cpus"]
        if entry["cpu_total"] > 0 and entry["cpu_alloc"] > entry["cpu_total"]:
            entry["cpu_alloc"] = entry["cpu_total"]
        if entry["gpu_total"] <= 0:
            entry["gpu_total"] = parse_gpu_total_from_gres(entry["gres"])
        if entry["gpu_total"] > 0 and entry["gpu_alloc"] > entry["gpu_total"]:
            entry["gpu_alloc"] = entry["gpu_total"]
        partition_list = sorted([part for part in entry["partitions"] if part])
        partition_name = partition_list[0] if partition_list else ""
        partitions_csv = ",".join(partition_list)
        gpu_type = entry["gpu_type"] or detect_gpu_type(entry["features"], entry["gres"])
        nodes.append({
            "name": canonical_name, "partitions": partition_list,
            "partition_name": partition_name, "state": entry["state"],
            "cpus": entry["cpus"], "memory_mb": entry["memory_mb"],
            "memory_alloc_mb": entry.get("memory_alloc_mb"),
            "memory_free_mb": entry.get("memory_free_mb"),
            "features": entry["features"], "gres": entry["gres"],
            "is_gpu": is_gpu_node(partitions_csv, entry["features"], entry["gres"], entry["gpu_total"]),
            "gpu_type": gpu_type, "cpu_alloc": entry["cpu_alloc"],
            "cpu_idle": entry["cpu_idle"], "cpu_other": entry["cpu_other"],
            "cpu_total": entry["cpu_total"], "gpu_alloc": entry["gpu_alloc"],
            "gpu_total": entry["gpu_total"],
        })
    nodes.sort(key=lambda node: (NODE_STATE_SEVERITY.get(node["state"], 99), node.get("partition_name") or "~", node["name"]))
    if use_scontrol and scontrol_ok and not scontrol_message:
        scontrol_message = "ok"
    return nodes, " ".join(command), scontrol_ok, scontrol_message


def summarize_cluster_nodes(nodes: list) -> dict:
    state_counts = Counter(node.get("state", "unknown") for node in nodes)
    total_nodes = len(nodes)
    busy_nodes = sum(1 for node in nodes if node.get("state") in BUSY_NODE_STATES)
    gpu_nodes = sum(1 for node in nodes if node.get("is_gpu"))
    cpu_alloc_total = sum(int(node.get("cpu_alloc", 0)) for node in nodes)
    cpu_total_total = sum(int(node.get("cpu_total", 0)) for node in nodes)
    gpu_alloc_total = sum(int(node.get("gpu_alloc", 0)) for node in nodes)
    gpu_total_total = sum(int(node.get("gpu_total", 0)) for node in nodes)
    return {
        "total_nodes": total_nodes, "busy_nodes": busy_nodes,
        "idle_nodes": state_counts.get("idle", 0),
        "down_nodes": state_counts.get("down", 0),
        "mix_nodes": state_counts.get("mix", 0),
        "alloc_nodes": state_counts.get("alloc", 0),
        "gpu_nodes": gpu_nodes, "non_gpu_nodes": total_nodes - gpu_nodes,
        "node_busy_percent": pct(busy_nodes, total_nodes),
        "cpu_alloc_total": cpu_alloc_total, "cpu_total_total": cpu_total_total,
        "cpu_alloc_percent": pct(cpu_alloc_total, cpu_total_total),
        "gpu_alloc_total": gpu_alloc_total, "gpu_total_total": gpu_total_total,
        "gpu_alloc_percent": pct(gpu_alloc_total, gpu_total_total),
        "state_counts": dict(sorted(state_counts.items(), key=lambda item: (-item[1], item[0]))),
    }


def format_percent(value) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f}%"


def format_cluster_snapshot_summary(summary: dict, partition_rows: list, scope: list, command: str, scontrol_ok: bool, scontrol_message: str) -> str:
    scope_label = ",".join(scope) if scope else "all partitions"
    lines = [
        "Exact cluster snapshot summary (live Slurm)",
        f"Scope: {scope_label}", "",
        "Overall",
        f"- Total nodes: {summary['total_nodes']}",
        f"- Busy nodes: {summary['busy_nodes']} ({format_percent(summary['node_busy_percent'])})",
        f"- Idle nodes: {summary['idle_nodes']}",
        f"- Down nodes: {summary['down_nodes']}",
        f"- CPU allocation: {summary['cpu_alloc_total']}/{summary['cpu_total_total']} ({format_percent(summary['cpu_alloc_percent'])})",
        f"- GPU allocation: {summary['gpu_alloc_total']}/{summary['gpu_total_total']} ({format_percent(summary['gpu_alloc_percent'])})",
        f"- GPU nodes: {summary['gpu_nodes']}", "",
        "State counts",
    ]
    if summary.get("state_counts"):
        for state, count in summary["state_counts"].items():
            lines.append(f"- {state}: {count}")
    else:
        lines.append("- none")
    if partition_rows:
        lines.append(""); lines.append("Partition breakdown")
        lines.append("Partition            Total  Busy  Idle  Down    CPU%    GPU%")
        lines.append("-------------------------------------------------------------")
        for row in partition_rows:
            lines.append(f"{row['partition'][:18]:<18} {row['total_nodes']:>5} {row['busy_nodes']:>5} {row['idle_nodes']:>5} {row['down_nodes']:>5} {format_percent(row['cpu_alloc_percent']):>7} {format_percent(row['gpu_alloc_percent']):>7}")
    lines.append(""); lines.append(f"Command: {command}")
    if scontrol_ok:
        lines.append("scontrol enrichment: enabled")
    else:
        lines.append(f"scontrol enrichment: unavailable ({scontrol_message})")
        lines.append("Note: GPU allocation numbers may be incomplete without scontrol.")
    return "\n".join(lines).strip()


def summarize_partition_breakdown(nodes: list) -> list:
    partition_stats = {}
    for node in nodes:
        partitions = node.get("partitions") or ([node.get("partition_name")] if node.get("partition_name") else [])
        unique_partitions = sorted(set([part for part in partitions if part]))
        if not unique_partitions:
            unique_partitions = ["unknown"]
        for partition in unique_partitions:
            if partition not in partition_stats:
                partition_stats[partition] = {
                    "partition": partition, "total_nodes": 0, "busy_nodes": 0,
                    "idle_nodes": 0, "down_nodes": 0, "gpu_nodes": 0,
                    "cpu_alloc_total": 0, "cpu_total_total": 0,
                    "gpu_alloc_total": 0, "gpu_total_total": 0,
                }
            row = partition_stats[partition]
            row["total_nodes"] += 1
            state = node.get("state")
            if state in BUSY_NODE_STATES:
                row["busy_nodes"] += 1
            if state == "idle":
                row["idle_nodes"] += 1
            if state == "down":
                row["down_nodes"] += 1
            if node.get("is_gpu"):
                row["gpu_nodes"] += 1
            row["cpu_alloc_total"] += int(node.get("cpu_alloc", 0))
            row["cpu_total_total"] += int(node.get("cpu_total", 0))
            row["gpu_alloc_total"] += int(node.get("gpu_alloc", 0))
            row["gpu_total_total"] += int(node.get("gpu_total", 0))
    rows = []
    for row in partition_stats.values():
        row["busy_percent"] = pct(row["busy_nodes"], row["total_nodes"])
        row["cpu_alloc_percent"] = pct(row["cpu_alloc_total"], row["cpu_total_total"])
        row["gpu_alloc_percent"] = pct(row["gpu_alloc_total"], row["gpu_total_total"])
        rows.append(row)
    rows.sort(key=lambda item: (-item["total_nodes"], item["partition"]))
    return rows


def check_cluster_snapshot_summary(partition: str | None = None, include_partition_breakdown: bool = True, use_scontrol: bool = True) -> str:
    if isinstance(partition, list):
        scope = [item for item in [normalize_null(p) for p in partition] if item]
    else:
        scope = split_csv(partition)
    scontrol_enabled = coerce_bool(use_scontrol, default=True)
    try:
        nodes, command, scontrol_ok, scontrol_message = fetch_cluster_nodes(scope, use_scontrol=scontrol_enabled)
    except RuntimeError as e:
        return f"Error collecting cluster snapshot summary: {e}"
    if not nodes:
        if scope:
            return f"No nodes found for partition scope: {', '.join(scope)}"
        return "No nodes found in cluster snapshot."
    summary = summarize_cluster_nodes(nodes)
    partition_rows = summarize_partition_breakdown(nodes) if include_partition_breakdown else []
    return format_cluster_snapshot_summary(summary, partition_rows, scope, command, scontrol_ok, scontrol_message)


def query_top_gpu_nodes(nodes: list, limit: int = 30) -> list:
    safe_limit = max(1, min(200, safe_int(limit) if safe_int(limit) > 0 else 30))
    rows = []
    for node in nodes:
        gpu_total = int(node.get("gpu_total", 0))
        if gpu_total <= 0:
            continue
        gpu_alloc = int(node.get("gpu_alloc", 0))
        partitions = [part for part in (node.get("partitions") or []) if part]
        partition_name = node.get("partition_name") or (partitions[0] if partitions else "unknown")
        rows.append({
            "name": node.get("name", ""), "partition_name": partition_name,
            "partitions": partitions, "state": node.get("state", "unknown"),
            "gpu_type": node.get("gpu_type") or "GPU",
            "gpu_alloc": gpu_alloc, "gpu_total": gpu_total,
            "gpu_alloc_percent": pct(gpu_alloc, gpu_total),
        })
    rows.sort(key=lambda item: (-(item["gpu_alloc_percent"] if item["gpu_alloc_percent"] is not None else -1.0), -item["gpu_alloc"], item["name"]))
    return rows[:safe_limit]


def format_top_gpu_nodes(rows: list, scope: list, command: str, scontrol_ok: bool, scontrol_message: str) -> str:
    scope_label = ",".join(scope) if scope else "all partitions"
    lines = ["Top GPU-utilized nodes (live Slurm)", f"Scope: {scope_label}", ""]
    if not rows:
        lines.append("No GPU nodes found for this scope.")
    else:
        lines.append("Node                 Partition        GPU Type   State    GPU Alloc    GPU%")
        lines.append("-------------------------------------------------------------------------")
        for row in rows:
            alloc_str = f"{row['gpu_alloc']}/{row['gpu_total']}"
            lines.append(f"{row['name'][:20]:<20} {row['partition_name'][:14]:<14} {row['gpu_type'][:10]:<10} {row['state'][:8]:<8} {alloc_str:>9} {format_percent(row['gpu_alloc_percent']):>7}")
        multi_partition_rows = [row for row in rows if len(set(row.get("partitions", []))) > 1]
        if multi_partition_rows:
            lines.append(""); lines.append("Nodes in multiple partitions")
            for row in multi_partition_rows:
                lines.append(f"- {row['name']}: {', '.join(sorted(set(row['partitions'])))}")
    lines.append(""); lines.append(f"Command: {command}")
    if scontrol_ok:
        lines.append("scontrol enrichment: enabled")
    else:
        lines.append(f"scontrol enrichment: unavailable ({scontrol_message})")
        lines.append("Note: GPU utilization ranking may be less accurate without scontrol TRES data.")
    return "\n".join(lines).strip()


def check_top_gpu_utilized_nodes(partition: str | None = None, limit: int = 30, use_scontrol: bool = True) -> str:
    if isinstance(partition, list):
        scope = [item for item in [normalize_null(p) for p in partition] if item]
    else:
        scope = split_csv(partition)
    scontrol_enabled = coerce_bool(use_scontrol, default=True)
    try:
        nodes, command, scontrol_ok, scontrol_message = fetch_cluster_nodes(scope, use_scontrol=scontrol_enabled)
    except RuntimeError as e:
        return f"Error collecting top GPU nodes: {e}"
    if not nodes:
        if scope:
            return f"No nodes found for partition scope: {', '.join(scope)}"
        return "No nodes found in cluster snapshot."
    rows = query_top_gpu_nodes(nodes, limit=limit)
    return format_top_gpu_nodes(rows=rows, scope=scope, command=command, scontrol_ok=scontrol_ok, scontrol_message=scontrol_message)
