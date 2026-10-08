"""Independent bounded-input exploration for Issue 920 F2p boundaries.

This script intentionally exercises only pure decoders and temporary private
job files. It does not edit tracked source files or invoke git/GitHub.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "skills" / "mission" / "lib"
sys.path.insert(0, str(LIB))

from budgeted_exec import FRAME_LIMIT, read_frame, strict_json
from mission_application.spawn_trampoline import decode_job
from mission_persistence import spawn_jobs
from scoring_provenance import build_request


PIN = {
    "entry_point": "neutral",
    "distribution": "neutral-verifier",
    "module": "neutral_verifier",
    "source_digest": "sha256:" + "a" * 64,
    "version": "1.0",
    "entry_point_value": "neutral_verifier:verify",
}
REQUEST = build_request(
    session_id="test",
    mission_id="abc12345",
    revision_scope={"kind": "not-applicable", "reason_code": "non-git"},
    terminal_object_digest="sha256:" + "b" * 64,
    approval_evidence_ref="sha256:" + "a" * 64,
    approved_actor="role:owner",
    approved_at=dt.datetime.now(dt.timezone.utc).isoformat(),
    reason_code="user-override",
    event_nonce="c" * 64,
)
BASE = {
    "schema": "mission-exec-job/1",
    "kind": "approval-verifier",
    "result_fd": 3,
    "verifier": PIN,
    "request": REQUEST,
}


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
        ("entry-uppercase", "entry_point", "A"),
        ("entry-too-long", "entry_point", "a" * 65),
        ("distribution-leading-dash", "distribution", "-bad"),
        ("module-dash", "module", "x-y"),
        ("digest-uppercase", "source_digest", "sha256:" + "A" * 64),
        ("digest-short", "source_digest", "sha256:" + "a" * 63),
        ("version-empty", "version", ""),
        ("entry-value-space", "entry_point_value", "bad value"),
    ]:
        add("decoder/pin-" + name, lambda x, k=key, v=value: x["verifier"].update({k: v}))
    for name, key, value in [
        ("iteration-bool", "iteration", True),
        ("iteration-negative", "iteration", -1),
        ("phase-unknown", "phase", "x"),
        ("risk-scopes-string", "risk_scopes", "x"),
        ("risk-scopes-int-item", "risk_scopes", [1]),
        ("session-empty", "session_id", ""),
        ("packet-digest-null", "outbound_packet_digest", None),
        ("phase-bool", "phase", True),
        ("risk-scopes-bool", "risk_scopes", True),
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


class _Exited:
    pid = 0


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
                read_frame(_Exited(), read_fd, 0, exit_probe=lambda _pid: True)
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
    cases = decoder_cases()
    cases += strict_cases()
    cases += frame_cases()
    cases += [("job/" + name, mode, expected) for name, mode, expected in [
        ("normal", "normal", True), ("digest", "digest", False),
        ("limit", "limit", False), ("mode", "mode", False),
        ("hardlink", "hardlink", False), ("symlink", "symlink", False),
        ("directory-mode", "directory-mode", False), ("fifo", "fifo", False),
        ("invalid-utf8", "invalid-utf8", False),
    ]]
    results = [run_case(name, value, expected) for name, value, expected in cases]
    report = {
        "trial_count": len(results),
        "categories": {
            "decoder": sum(name.startswith("decoder/") for name, _, _ in cases),
            "strict_json": sum(name.startswith("strict/") for name, _, _ in cases),
            "frame": sum(name.startswith("frame/") for name, _, _ in cases),
            "job_file": sum(name.startswith("job/") for name, _, _ in cases),
        },
        "matches": sum(item["match"] for item in results),
        "mismatches": [item for item in results if not item["match"]],
        "results": results,
    }
    output = ROOT / "reports" / "issue-920-input-exploration.json"
    output.write_text(json.dumps(report, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("trial_count", "categories", "matches", "mismatches")}, sort_keys=True))
    return 0 if not report["mismatches"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
