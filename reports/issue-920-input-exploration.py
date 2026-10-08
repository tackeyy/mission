"""Independent 54-input replay: pure decoders and temporary private files only."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "mission" / "lib"))

from budgeted_exec import FRAME_LIMIT, read_frame, strict_json
from mission_application.spawn_trampoline import decode_job
from mission_persistence import spawn_jobs
from scoring_provenance import build_request

PIN = dict(entry_point="neutral", distribution="neutral-verifier", module="neutral_verifier",
           source_digest="sha256:" + "a" * 64, version="1.0", entry_point_value="neutral_verifier:verify")
REQUEST = build_request(session_id="test", mission_id="abc12345",
    revision_scope={"kind": "not-applicable", "reason_code": "non-git"},
    terminal_object_digest="sha256:" + "b" * 64, approval_evidence_ref="sha256:" + "a" * 64,
    approved_actor="role:owner", approved_at=dt.datetime.now(dt.timezone.utc).isoformat(),
    reason_code="user-override", event_nonce="c" * 64)
BASE = dict(schema="mission-exec-job/1", kind="approval-verifier", result_fd=3, verifier=PIN, request=REQUEST)

def decoder_cases():
    cases = [("decoder/base", BASE, True)]

    def add(name, update):
        value = json.loads(json.dumps(BASE))
        update(value)
        cases.append((name, value, False))

    for name, update in [
        ("schema", lambda x: x.update(schema="x")),
        ("kind", lambda x: x.update(kind="shell")),
        ("kind-missing", lambda x: x.pop("kind")),
        ("fd-string", lambda x: x.update(result_fd="3")),
        ("fd-two", lambda x: x.update(result_fd=2)),
        ("fd-bool", lambda x: x.update(result_fd=True)),
        ("verifier-list", lambda x: x.update(verifier=[])),
        ("verifier-extra", lambda x: x["verifier"].update(extra="x")),
        ("request-list", lambda x: x.update(request=[])),
        ("request-extra", lambda x: x["request"].update(extra=1)),
        ("request-schema", lambda x: x["request"].update(schema="x")),
    ]:
        add("decoder/" + name, update)
    for name, key, value in [
        ("entry-uppercase", "entry_point", "A"), ("entry-too-long", "entry_point", "a" * 65),
        ("distribution-leading-dash", "distribution", "-bad"),
        ("module-dash", "module", "x-y"),
        ("digest-uppercase", "source_digest", "sha256:" + "A" * 64),
        ("digest-short", "source_digest", "sha256:" + "a" * 63),
        ("version-empty", "version", ""), ("entry-value-space", "entry_point_value", "bad value"),
    ]:
        add("decoder/pin-" + name, lambda x, k=key, v=value: x["verifier"].update({k: v}))
    for name, key, value in [
        ("iteration-bool", "iteration", True), ("iteration-negative", "iteration", -1),
        ("phase-unknown", "phase", "x"), ("risk-scopes-string", "risk_scopes", "x"),
        ("risk-scopes-int-item", "risk_scopes", [1]),
        ("session-empty", "session_id", ""),
        ("packet-digest-null", "outbound_packet_digest", None),
        ("phase-bool", "phase", True), ("risk-scopes-bool", "risk_scopes", True),
        ("mission-id-null", "mission_id", None),
        ("evidence-ref-empty", "evidence_ref", ""),
    ]:
        add("decoder/request-" + name, lambda x, k=key, v=value: x["request"].update({k: v}))
    return cases

def strict_cases():
    return [
        ("strict/valid-object", b'{"a":1}', True),
        ("strict/duplicate-key", b'{"a":1,"a":2}', False),
        ("strict/nan", b'{"a":NaN}', False),
        ("strict/infinity", b'{"a":Infinity}', False),
        ("strict/trailing-bytes", b'{"a":1}x', False),
        ("strict/array", b"[]", True),
        ("strict/invalid-utf8", b"\xff", False),
        ("strict/null", b"null", True),
    ]

def frame_cases():
    return [
        ("frame/valid-object", (2).to_bytes(4, "big") + b"{}", True),
        ("frame/zero-length", (0).to_bytes(4, "big"), False),
        ("frame/truncated", (5).to_bytes(4, "big") + b"{}", False),
        ("frame/over-limit", (FRAME_LIMIT + 1).to_bytes(4, "big"), False),
        ("frame/scalar", (3).to_bytes(4, "big") + b"123", False),
        ("frame/invalid-utf8", (1).to_bytes(4, "big") + b"\xff", False),
    ]

def run_case(name, value, expected):
    observed = False
    try:
        if name.startswith("decoder/"):
            decode_job(json.dumps(value, separators=(",", ":"), allow_nan=False).encode())
            observed = True
        elif name.startswith("strict/"):
            strict_json(value)
            observed = True
        elif name.startswith("frame/"):
            read_fd, write_fd = os.pipe()
            os.write(write_fd, value)
            os.close(write_fd)
            try:
                read_frame(SimpleNamespace(pid=0), read_fd, 0, exit_probe=lambda _pid: True)
                observed = True
            finally:
                os.close(read_fd)
        else:
            mode = value
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary) / "jobs"
                path, digest = spawn_jobs.create_job(directory, b"{}")
                if mode == "symlink":
                    original = path.with_suffix(".original")
                    path.rename(original)
                    path.symlink_to(original)
                elif mode == "fifo":
                    path.unlink()
                    os.mkfifo(path)
                else:
                    setup = {
                        "normal": lambda: None, "digest": lambda: None, "limit": lambda: None,
                        "mode": lambda: path.chmod(0o644),
                        "hardlink": lambda: os.link(path, path.with_suffix(".link")),
                        "directory-mode": lambda: directory.chmod(0o755),
                        "invalid-utf8": lambda: path.write_bytes(b"\xff"),
                    }
                    setup[mode]()
                observed = spawn_jobs.read_job(path, "0" * 64 if mode == "digest" else digest,
                                               limit=1 if mode == "limit" else spawn_jobs.JOB_LIMIT) == b"{}"
    except Exception:
        observed = False
    return {"name": name, "expected": expected, "observed": observed, "match": expected == observed}


def main():
    cases = decoder_cases() + strict_cases() + frame_cases()
    modes = "normal digest limit mode hardlink symlink directory-mode fifo invalid-utf8".split()
    cases += [("job/" + mode, mode, mode == "normal") for mode in modes]
    results = [run_case(name, value, expected) for name, value, expected in cases]
    categories = {"decoder": "decoder/", "strict_json": "strict/", "frame": "frame/", "job_file": "job/"}
    report = dict(trial_count=len(results),
        categories={key: sum(name.startswith(prefix) for name, _, _ in cases) for key, prefix in categories.items()},
        matches=sum(item["match"] for item in results), mismatches=[item for item in results if not item["match"]],
        results=results)
    (ROOT / "reports" / "issue-920-input-exploration.json").write_text(json.dumps(report, separators=(",", ":")) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, sort_keys=True))
    return 0 if not report["mismatches"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
