from hpcpilot.hpc.slurm import (
    canonical_node_name,
    coerce_bool,
    detect_gpu_type,
    extract_token,
    gpu_type_matches,
    is_gpu_node,
    merge_node_state,
    normalize_node_state,
    normalize_null,
    normalize_partition_name,
    parse_gpu_total_from_gres,
    parse_mem_to_mb,
    parse_slurm_time_to_minutes,
    parse_tres_memory_mb,
    parse_tres_value,
    pct,
    percentile,
    safe_int,
    split_csv,
)


def test_normalize_null():
    assert normalize_null(None) == ""
    assert normalize_null("   ") == ""
    assert normalize_null("null") == ""
    assert normalize_null("(null)") == ""
    assert normalize_null("N/A") == ""
    assert normalize_null("None") == ""
    assert normalize_null("valid") == "valid"


def test_split_csv():
    assert split_csv("a, b, c") == ["a", "b", "c"]
    assert split_csv("a,,b") == ["a", "b"]
    assert split_csv(None) == []


def test_safe_int():
    assert safe_int(123) == 123
    assert safe_int("456") == 456
    assert safe_int("abc-789xyz") == -789
    assert safe_int("no digits") == 0
    assert safe_int(None) == 0


def test_extract_token():
    line = "JobId=123 Partition=debug Name=test"
    assert extract_token(line, "JobId") == "123"
    assert extract_token(line, "Partition") == "debug"
    assert extract_token(line, "Nonexistent") == ""


def test_canonical_node_name():
    assert canonical_node_name("Node01.domain.com") == "node01"
    assert canonical_node_name("node02") == "node02"
    assert canonical_node_name(None) == ""


def test_normalize_node_state():
    assert normalize_node_state("allocated*") == "alloc"
    assert normalize_node_state("mixed+") == "mix"
    assert normalize_node_state("completing") == "comp"
    assert normalize_node_state("draining") == "drng"
    assert normalize_node_state("DRAINED") == "drain"
    assert normalize_node_state("idle") == "idle"


def test_merge_node_state():
    assert merge_node_state("idle", "alloc") == "alloc"
    assert merge_node_state("maint", "idle") == "maint"
    assert merge_node_state("idle", None) == "idle"


def test_parse_tres_value():
    tres = "cpu=64,mem=256G,node=1"
    assert parse_tres_value(tres, "cpu") == 64
    assert parse_tres_value(tres, "mem") == 256
    assert parse_tres_value(tres, "node") == 1
    assert parse_tres_value(tres, "gpu") == 0


def test_parse_gpu_total_from_gres():
    assert parse_gpu_total_from_gres("gpu:4") == 4
    assert parse_gpu_total_from_gres("gpu:a100:2,gpu:v100:1") == 3
    assert parse_gpu_total_from_gres("") == 0


def test_is_gpu_node():
    assert is_gpu_node("gpu", "", "", 0) is True
    assert is_gpu_node("", "", "gpu:a100:1", 1) is True
    assert is_gpu_node("cpu", "cpu_only", "none", 0) is False


def test_detect_gpu_type():
    assert detect_gpu_type("", "gpu:a100:2") == "A100"
    assert detect_gpu_type("gpu_rtx3090", "") == "RTX3090"
    assert detect_gpu_type("", "gpu:1") == "GPU"
    assert detect_gpu_type("", "") == ""


def test_pct():
    assert pct(5, 10) == 50.0
    assert pct(1, 3) == 33.3
    assert pct(5, 0) is None


def test_coerce_bool():
    assert coerce_bool(True) is True
    assert coerce_bool("yes") is True
    assert coerce_bool("off") is False
    assert coerce_bool(None, default=True) is True


def test_gpu_type_matches():
    assert gpu_type_matches("A100-SXM4", "A100") is True
    assert gpu_type_matches("A100", "v100") is False
    assert gpu_type_matches("", "A100") is False
    assert gpu_type_matches("A100", "") is True


def test_parse_mem_to_mb():
    assert parse_mem_to_mb("256M") == 256
    assert parse_mem_to_mb("4G") == 4096
    assert parse_mem_to_mb("1T") == 1048576
    assert parse_mem_to_mb("512") == 512


def test_parse_tres_memory_mb():
    assert parse_tres_memory_mb("cpu=16,mem=16G") == 16384
    assert parse_tres_memory_mb("cpu=8") == 0


def test_parse_slurm_time_to_minutes():
    assert parse_slurm_time_to_minutes("1-12:30:00") == 2190.0  # 1 day = 1440 mins + 12 hours (720 mins) + 30 mins
    assert parse_slurm_time_to_minutes("45:00") == 45.0
    assert parse_slurm_time_to_minutes("UNLIMITED") == 0.0


def test_percentile():
    assert percentile([1, 2, 3, 4, 5], 50) == 3.0
    assert percentile([1, 10], 90) == 9.1
    assert percentile([], 50) == 0.0


def test_normalize_partition_name():
    assert normalize_partition_name("debug*") == "debug"
    assert normalize_partition_name("gpu,compute") == "gpu"
