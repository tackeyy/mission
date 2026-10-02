"""#878 candidate snapshot regression tests."""
import subprocess


def test_snapshot_uses_dirty_tracked_bytes_and_fresh_materialization(tmp_path):
    from mission_application.verification_runner import capture_candidate, materialize_candidate

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    source = tmp_path / "app.txt"; source.write_text("committed", encoding="utf-8")
    subprocess.run(["git", "add", "app.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    source.write_text("dirty", encoding="utf-8")

    candidate = capture_candidate(tmp_path, declared_untracked=())
    with materialize_candidate(candidate) as directory:
        assert (directory / "app.txt").read_text(encoding="utf-8") == "dirty"
    assert candidate.digest.startswith("sha256:")


def test_rejects_forged_escape_and_distinguishes_deleted_file_from_dash():
    import pytest
    from mission_application.verification_runner import CandidateFile, CandidateSnapshot, VerificationRunnerError, _digest, materialize_candidate

    escape = (CandidateFile("../outside", 0o644, b"x"),)
    with pytest.raises(VerificationRunnerError, match="candidate-path-invalid"):
        _digest(escape)
    deleted = (CandidateFile("result", 0o644, None),)
    dash = (CandidateFile("result", 0o644, b"-"),)
    assert _digest(deleted) != _digest(dash)
    with pytest.raises(VerificationRunnerError, match="candidate-digest-invalid"):
        with materialize_candidate(CandidateSnapshot(dash, _digest(deleted))):
            pass


def test_runner_bounds_output_times_out_and_rejects_zero_test_count(tmp_path):
    from mission_application.verification_runner import capture_candidate, execute_candidate

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "app.py").write_text("x = 1", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    candidate = capture_candidate(tmp_path, declared_untracked=())
    timeout = execute_candidate(candidate, {"argv": ["python3", "-c", "import os, time; os.close(1); os.close(2); time.sleep(2)"], "timeout_sec": 1, "output_limit": 8, "kind": "command", "env": {}}, relative_cwd=".")
    assert timeout["status"] == "blocked" and timeout["timed_out"] is True
    zero = execute_candidate(candidate, {"argv": ["python3", "-c", "print('0 tests')"], "timeout_sec": 5, "output_limit": 4, "kind": "test", "executed_count_pattern": r"(\\d+) tests", "env": {}}, relative_cwd=".")
    assert zero["status"] == "failed" and zero["executed_count"] == 0
    mutation = execute_candidate(candidate, {"argv": ["python3", "-c", "from pathlib import Path; Path('app.py').write_text('mutated')"], "timeout_sec": 5, "output_limit": 8, "kind": "command", "env": {}}, relative_cwd=".")
    assert mutation["status"] == "blocked"
    assert mutation["exit_code"] == 0 and mutation["timed_out"] is False
    assert mutation["block_reason"] == "candidate-stale"
