import os, re, subprocess, sqlite3
from collections import Counter


TOOL_CMD_TIMEOUT = 25


GPU_HINTS = (
    "gpu", "a100", "a40", "a30", "h100", "h200", "v100", "p100",
    "k80", "t4", "l4", "l40", "rtx", "tesla", "mi100", "mi200", "mi250", "mi300",
)

GPU_TYPE_CANDIDATES = (
    "h200", "h100", "a100", "a40", "a30", "v100", "p100", "k80", "t4", "l40", "l4",
    "rtx6000", "rtx5000", "rtx4090", "rtx3090", "rtx2080", "rtx",
    "mi300", "mi250", "mi200", "mi100", "tesla",
)

NODE_STATE_SEVERITY = {
    "unknown": 0, "idle": 1, "comp": 2, "mix": 3, "alloc": 4,
    "resv": 5, "drng": 6, "drain": 6, "maint": 7, "down": 8,
}

BUSY_NODE_STATES = {"alloc", "mix", "comp", "drng", "drain", "resv", "maint"}
UNAVAILABLE_NODE_STATES = {"down", "drain", "drng", "maint", "fail", "unknown", "pow_up", "reboot"}


def normalize_null(text: str) -> str:
    if text is None:
        return ""
    value = str(text).strip()
    if not value:
        return ""
    if value.lower() in {"(null)", "null", "n/a", "none"}:
        return ""
    return value


def split_csv(text: str) -> list:
    return [item.strip() for item in normalize_null(text).split(",") if item.strip()]


def safe_int(text) -> int:
    if text is None:
        return 0
    match = re.search(r"-?\d+", str(text))
    if not match:
        return 0
    try:
        return int(match.group(0))
    except ValueError:
        return 0


def extract_token(line: str, key: str) -> str:
    match = re.search(rf"{re.escape(key)}=([^ ]+)", line)
    if not match:
        return ""
    return match.group(1).strip()


def canonical_node_name(node_name: str) -> str:
    return normalize_null(node_name).split(".", 1)[0].lower()


def normalize_node_state(state: str) -> str:
    value = normalize_null(state).lower()
    if not value:
        return ""
    value = value.rstrip("*")
    value = value.split("+", 1)[0]
    aliases = {
        "allocated": "alloc", "mixed": "mix", "completing": "comp",
        "draining": "drng", "drained": "drain", "maintenance": "maint", "reserved": "resv",
    }
    return aliases.get(value, value)


def merge_node_state(current_state: str, new_state: str) -> str:
    current = normalize_node_state(current_state)
    incoming = normalize_node_state(new_state)
    if not current:
        return incoming
    if not incoming:
        return current
    if NODE_STATE_SEVERITY.get(incoming, 0) >= NODE_STATE_SEVERITY.get(current, 0):
        return incoming
    return current


def parse_tres_value(tres: str, key: str) -> int:
    text = normalize_null(tres)
    if not text:
        return 0
    for token in text.split(","):
        token = token.strip()
        if token.startswith(f"{key}="):
            return safe_int(token.split("=", 1)[1])
    return 0


def parse_gpu_total_from_gres(gres: str) -> int:
    text = normalize_null(gres).lower()
    if not text:
        return 0
    total = 0
    for match in re.finditer(r"gpu(?::[^,:()]+)?:(\d+)", text):
        total += int(match.group(1))
    return total


def is_gpu_node(partitions_csv: str, features: str, gres: str, gpu_total: int) -> bool:
    if gpu_total > 0:
        return True
    text = " ".join((normalize_null(partitions_csv), normalize_null(features), normalize_null(gres))).lower()
    return any(token in text for token in GPU_HINTS)


def detect_gpu_type(features: str, gres: str) -> str:
    text = f"{normalize_null(features)},{normalize_null(gres)}".lower()
    gres_model = re.search(r"gpu:([a-z0-9._-]+):\d+", text)
    if gres_model:
        candidate = gres_model.group(1).replace("_", "-").upper()
        if candidate and not candidate.isdigit() and candidate not in {"GPU", "MPS", "SHARD"}:
            return candidate
    for token in GPU_TYPE_CANDIDATES:
        if token in text:
            return token.upper()
    if "gpu" in text:
        return "GPU"
    return ""


def pct(numerator: int, denominator: int):
    if denominator <= 0:
        return None
    return round((numerator * 100.0) / denominator, 1)


def coerce_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y"}
    return bool(value)


def gpu_type_matches(node_gpu_type: str, requested_gpu_type: str) -> bool:
    request = normalize_null(requested_gpu_type).lower().replace("_", "-")
    if not request:
        return True
    have = normalize_null(node_gpu_type).lower().replace("_", "-")
    if not have:
        return False
    return request in have or have in request


def parse_mem_to_mb(raw: str) -> int:
    value = normalize_null(raw).upper()
    if not value or value == "0":
        return 0
    match = re.match(r"^(\d+(?:\.\d+)?)([KMGTP])(?:[NC])?$", value)
    if match:
        number = float(match.group(1))
        scale = {"K": 1 / 1024, "M": 1, "G": 1024, "T": 1024 ** 2, "P": 1024 ** 3}
        return int(number * scale[match.group(2)])
    return max(0, safe_int(value))


def parse_tres_memory_mb(tres: str) -> int:
    text = normalize_null(tres)
    if not text:
        return 0
    for token in text.split(","):
        token = token.strip()
        if token.startswith("mem="):
            return parse_mem_to_mb(token.split("=", 1)[1])
    return 0


def parse_slurm_time_to_minutes(time_str: str) -> float:
    value = normalize_null(time_str).upper()
    if not value or value in {"UNLIMITED", "NOT_SET", "N/A", "INVALID", "UNKNOWN"}:
        return 0.0
    if "-" in value:
        day_part, rest = value.split("-", 1)
        return safe_int(day_part) * 1440 + parse_slurm_time_to_minutes(rest)
    parts = value.split(":")
    if len(parts) == 3:
        return safe_int(parts[0]) * 60 + safe_int(parts[1]) + (safe_int(parts[2]) / 60.0)
    if len(parts) == 2:
        return safe_int(parts[0]) + (safe_int(parts[1]) / 60.0)
    return float(safe_int(parts[0])) if parts else 0.0


def percentile(values: list, pct_val: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    index = (pct_val / 100.0) * (len(ordered) - 1)
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return float(ordered[lower])
    return float(ordered[lower]) + ((index - lower) * (float(ordered[upper]) - float(ordered[lower])))


def normalize_partition_name(raw: str) -> str:
    value = normalize_null(raw).rstrip("*")
    if "," in value:
        return value.split(",", 1)[0].strip()
    return value


def run_cli_command(cmd: list, timeout: int = TOOL_CMD_TIMEOUT) -> tuple:
    try:
        output = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT, timeout=timeout)
        return True, output.strip()
    except FileNotFoundError:
        return False, f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired as e:
        partial = ""
        if hasattr(e, "output") and e.output:
            partial = e.output.strip()
        message = f"Command timed out after {timeout}s: {' '.join(cmd)}"
        if partial:
            message += f"\nPartial output:\n{partial}"
        return False, message
    except subprocess.CalledProcessError as e:
        detail = e.output.strip() if hasattr(e, "output") and e.output else str(e)
        return False, detail


def is_fatal_command_error(message: str) -> bool:
    lower = (message or "").lower()
    return "timed out" in lower or "command not found" in lower
