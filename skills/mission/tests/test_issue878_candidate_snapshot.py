"""#878 candidate snapshot regression tests."""
import subprocess
import time


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
    with pytest.raises(VerificationRunnerError, match="candidate-path-invalid"):
        _digest((CandidateFile("bad\ud800", 0o644, b"x"),))


def test_snapshot_materializes_declared_local_input_and_rejects_target_collision(tmp_path):
    import pytest
    from mission_application.verification_runner import VerificationRunnerError, capture_candidate, materialize_candidate

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text("candidate", encoding="utf-8")
    (tmp_path / "input.txt").write_text("bound", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "base"], cwd=tmp_path, check=True)

    candidate = capture_candidate(tmp_path, declared_untracked=(), external_inputs=[{"kind": "local-file", "source_path": "input.txt", "target_path": "bound/input.txt"}])
    with materialize_candidate(candidate) as directory:
        assert (directory / "bound" / "input.txt").read_text(encoding="utf-8") == "bound"
    with pytest.raises(VerificationRunnerError, match="external-input-target-conflict"):
        capture_candidate(tmp_path, declared_untracked=(), external_inputs=[{"kind": "local-file", "source_path": "input.txt", "target_path": "tracked.txt"}])
    (tmp_path / "other.txt").write_text("other", encoding="utf-8")
    (tmp_path / "third.txt").write_text("third", encoding="utf-8")
    with pytest.raises(VerificationRunnerError, match="candidate-path-conflict"):
        capture_candidate(tmp_path, declared_untracked=(), external_inputs=[
            {"kind": "local-file", "source_path": "input.txt", "target_path": "bound"},
            {"kind": "local-file", "source_path": "other.txt", "target_path": "bound-other"},
            {"kind": "local-file", "source_path": "third.txt", "target_path": "bound/input.txt"},
        ])
    with pytest.raises(VerificationRunnerError, match="candidate-path-conflict"):
        capture_candidate(tmp_path, declared_untracked=(), external_inputs=[
            {"kind": "local-file", "source_path": "input.txt", "target_path": "tracked.txt/input.txt"},
        ])


def test_runner_bounds_output_times_out_and_rejects_zero_test_count(tmp_path):
    from mission_application.verification_runner import capture_candidate, execute_candidate

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "app.py").write_text("x = 1", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    candidate = capture_candidate(tmp_path, declared_untracked=())
    timeout = execute_candidate(candidate, {"argv": ["python3", "-c", "import os, time; os.close(1); os.close(2); time.sleep(2)"], "timeout_sec": 1, "output_limit": 8, "kind": "command", "env": {}}, relative_cwd=".")
    assert timeout["status"] == "blocked" and timeout["timed_out"] is True
    started = time.monotonic()
    descendant = execute_candidate(candidate, {"argv": ["python3", "-c", "import subprocess, sys; subprocess.Popen([sys.executable, '-c', 'import os, time; os.setsid(); time.sleep(2)'])"], "timeout_sec": 1, "output_limit": 8, "kind": "command", "env": {}}, relative_cwd=".")
    assert descendant["status"] == "blocked" and descendant["timed_out"] is True
    assert time.monotonic() - started < 1.8
    zero = execute_candidate(candidate, {"argv": ["python3", "-c", "from pathlib import Path; Path('result.xml').write_text('<testsuite/>')"], "timeout_sec": 5, "output_limit": 4, "kind": "test", "test_report": {"format": "junit-xml", "path": "result.xml"}, "env": {}}, relative_cwd=".")
    assert zero["status"] == "failed" and zero["executed_count"] == 0
    mutation = execute_candidate(candidate, {"argv": ["python3", "-c", "from pathlib import Path; Path('app.py').write_text('mutated')"], "timeout_sec": 5, "output_limit": 8, "kind": "command", "env": {}}, relative_cwd=".")
    assert mutation["status"] == "blocked"
    assert mutation["exit_code"] == 0 and mutation["timed_out"] is False
    assert mutation["block_reason"] == "candidate-stale"


def test_test_runner_uses_declared_junit_report_not_console_text(tmp_path):
    """A passing process cannot borrow a count from arbitrary stdout."""
    from mission_application.verification_runner import capture_candidate, execute_candidate

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "app.py").write_text("x = 1", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    candidate = capture_candidate(tmp_path, declared_untracked=())
    command = {
        "argv": ["python3", "-c", "from pathlib import Path; print('1 tests'); print('0 tests'); Path('result.xml').write_text('<testsuite><testcase/><testcase><skipped/></testcase></testsuite>')"],
        "timeout_sec": 5, "output_limit": 4, "kind": "test", "env": {},
        "test_report": {"format": "junit-xml", "path": "result.xml"},
    }
    outcome = execute_candidate(candidate, command, relative_cwd=".")
    assert outcome["status"] == "passed"
    assert outcome["executed_count"] == 1
    assert outcome["output_truncated"] is True
    assert outcome["observed_output_bytes"] > 4


def test_test_report_must_be_fresh_valid_and_successful(tmp_path):
    import pytest
    from mission_application.verification_runner import VerificationRunnerError, capture_candidate, execute_candidate

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "app.py").write_text("x = 1", encoding="utf-8")
    (tmp_path / "result.xml").write_text("<testsuite><testcase/></testsuite>", encoding="utf-8")
    subprocess.run(["git", "add", "app.py", "result.xml"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    candidate = capture_candidate(tmp_path, declared_untracked=())
    base = {"argv": ["python3", "-c", "pass"], "timeout_sec": 5, "output_limit": 8, "kind": "test", "env": {}, "test_report": {"format": "junit-xml", "path": "result.xml"}}
    with pytest.raises(VerificationRunnerError, match="test-report-input-conflict"):
        execute_candidate(candidate, base, relative_cwd=".")

    clean = capture_candidate(tmp_path, declared_untracked=())
    deep = "<testsuite>" * 65 + "<testcase/>" + "</testsuite>" * 65
    for report in ("<testsuite><testcase><failure/></testcase></testsuite>", "<unknown><testsuite><testcase/></testsuite></unknown>", "<testsuite tests='2'><testcase/></testsuite>", deep):
        command = {**base, "argv": ["python3", "-c", "from pathlib import Path; Path('fresh.xml').write_text(" + repr(report) + ")"], "test_report": {"format": "junit-xml", "path": "fresh.xml"}}
        outcome = execute_candidate(clean, command, relative_cwd=".")
        assert outcome["status"] == "failed"


def test_runner_rejects_undeclared_path_arguments_and_binds_repro_kind(tmp_path):
    import pytest
    from mission_application.verification_runner import VerificationRunnerError, capture_candidate, execute_candidate

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "app.py").write_text("x = 1", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    candidate = capture_candidate(tmp_path, declared_untracked=())
    with pytest.raises(VerificationRunnerError, match="verifier-explicit-path-unsupported"):
        execute_candidate(candidate, {"argv": ["python3", "/tmp/helper.py"], "timeout_sec": 5, "output_limit": 8, "kind": "command", "env": {}}, relative_cwd=".")
    counterexample = execute_candidate(candidate, {"argv": ["python3", "-c", "pass"], "timeout_sec": 5, "output_limit": 8, "kind": "command", "env": {}}, relative_cwd=".", repro_input=("counterexample", "repro.json", b"same"))
    finding = execute_candidate(candidate, {"argv": ["python3", "-c", "pass"], "timeout_sec": 5, "output_limit": 8, "kind": "command", "env": {}}, relative_cwd=".", repro_input=("finding", "repro.json", b"same"))
    assert counterexample["repro_input_digest"] != finding["repro_input_digest"]


def test_runner_persists_facts_when_materialized_input_becomes_special_file(tmp_path):
    from mission_application.verification_runner import capture_candidate, execute_candidate

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "app.py").write_text("x = 1", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    outcome = execute_candidate(
        capture_candidate(tmp_path, declared_untracked=()),
        {"argv": ["python3", "-c", "from pathlib import Path; Path('app.py').unlink(); Path('app.py').mkdir()"], "timeout_sec": 5, "output_limit": 8, "kind": "command", "env": {}},
        relative_cwd=".",
    )
    assert outcome["status"] == "blocked"
    assert outcome["exit_code"] == 0
    assert outcome["block_reason"] == "candidate-observation-invalid"
