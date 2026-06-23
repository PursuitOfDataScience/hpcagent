from hpcagent.hpc.jobs import parse_requested_gpu_spec
from hpcagent.hpc.disk import parse_size_to_bytes


def test_parse_requested_gpu_spec():
    assert parse_requested_gpu_spec("gres/gpu:a100=2") == (2, "A100")
    assert parse_requested_gpu_spec("gpu:v100:1") == (1, "V100")
    assert parse_requested_gpu_spec("gpu:4") == (4, "")
    assert parse_requested_gpu_spec("") == (0, "")
    assert parse_requested_gpu_spec(None) == (0, "")
    # Mixed formats
    assert parse_requested_gpu_spec("gpu:a100:2,gpu:v100:1") == (3, "A100")


def test_parse_size_to_bytes():
    assert parse_size_to_bytes("100") == 100
    assert parse_size_to_bytes("100K") == 102400
    assert parse_size_to_bytes("2.5M") == 2621440
    assert parse_size_to_bytes("1.2G") == 1288490188
    assert parse_size_to_bytes("invalid") == 0
    assert parse_size_to_bytes("  10  ") == 10
