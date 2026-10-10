"""Approval execution in isolated registry children or legacy callable children."""
from __future__ import annotations
import contextlib
import hashlib
import importlib.metadata
import importlib.util
import multiprocessing
from multiprocessing.connection import wait
import os
from pathlib import Path
import time

from scoring_provenance import validate_recorded_envelope
import re

from .runtime_guard import (
    RegisteredEntryPointDistributionObservation,
    validate_registered_approval_entry_point_distribution,
)
from budgeted_exec import cleanup_group, read_frame, run_job, write_frame

ENTRY_POINT_GROUP = 'mission.approval_verifiers'

def valid_module_name(name):
    return isinstance(name, str) and all(part.isidentifier() for part in name.split('.'))


def invoke_registered(verifier, request):
    discovered = importlib.metadata.entry_points()
    candidates = (discovered.select(group=ENTRY_POINT_GROUP)
                  if hasattr(discovered, "select") else discovered.get(ENTRY_POINT_GROUP, ()))
    matches = [item for item in candidates if item.name == verifier["entry_point"]]
    if len(matches) != 1:
        raise ValueError("approval verifier entry point is not installed")
    entry_point = matches[0]
    attached_distribution = getattr(entry_point, 'dist', None)
    distribution = attached_distribution
    if distribution is None:
        distribution = importlib.metadata.distribution(
            verifier["distribution"]
        )
    observed_distribution = RegisteredEntryPointDistributionObservation(
        entry_point_name=entry_point.name,
        entry_point_value=entry_point.value,
        has_attached_distribution=attached_distribution is not None,
        distribution_name=distribution.metadata["Name"],
        distribution_version=distribution.version,
        owned_entry_points=tuple(
            map(
                lambda item: (item.group, item.name, item.value),
                distribution.entry_points,
            )
        ),
    )
    validate_registered_approval_entry_point_distribution(
        observed_distribution,
        verifier,
        group=ENTRY_POINT_GROUP,
    )
    module_name = getattr(entry_point, "module", "")
    if (module_name != verifier["module"]
            or getattr(entry_point, "value", None) != verifier["entry_point_value"]):
        raise ValueError("approval verifier entry point changed after pinning")
    module_spec = importlib.util.find_spec(module_name)
    origin = getattr(module_spec, "origin", None)
    if not isinstance(origin, str) or "sha256:" + hashlib.sha256(Path(origin).read_bytes()).hexdigest() != verifier["source_digest"]:
        raise ValueError("approval verifier source digest mismatch")
    callback = entry_point.load()
    if not callable(callback):
        raise ValueError("approval verifier entry point is invalid")
    return callback(request)


def _callable_child(verifier, request, control, sender, receiver):
    os.close(receiver)
    try:
        os.setsid()  # failure must prevent callback execution
        control.send(True)
        if control.recv() is not True:
            return
        write_frame(sender, {'ok': True, 'result': verifier(request)})
    except Exception:
        pass
    finally:
        os.close(sender)
        control.close()


def run_callable(verifier, request, *, timeout=5, grace=.2):
    context = multiprocessing.get_context('fork')
    parent, child_control = context.Pipe()
    receiver, sender = os.pipe()
    child = context.Process(target=_callable_child, args=(verifier, request, child_control, sender, receiver))
    deadline = time.monotonic() + timeout
    started, ready, timed_out, cleaned = False, False, False, False
    try:
        child.start()
        started = True
        child_control.close()
        os.close(sender)
        sender = None
        try:
            ready = parent.poll(max(0, deadline - time.monotonic())) and parent.recv() is True
        except EOFError:
            ready = False
        if not ready:
            raise ValueError('approval verifier rejected the evidence')
        ready = True
        probe = lambda _: bool(wait([child.sentinel], timeout=0))
        try:
            parent.send(True)
            frame = read_frame(child, receiver, deadline, exit_probe=probe)
        except TimeoutError as exc:
            timed_out = True
            raise ValueError('approval verifier timed out') from exc
        finally:
            cleaned = True
            confirmed = cleanup_group(child, term_grace=grace, kill_wait=grace,
                                      timed_out=timed_out, exit_probe=probe, reap=lambda: child.join(0))
            if not confirmed:
                raise ValueError('approval verifier timed out (kill-unconfirmed)' if timed_out else 'kill-unconfirmed')
        if child.exitcode != 0 or set(frame) != {'ok', 'result'} or frame['ok'] is not True or not isinstance(frame['result'], dict):
            raise ValueError('approval verifier rejected the evidence')
        return frame['result']
    finally:
        # Before readiness, no callback is allowed and the group is unknown.
        # Kill only this legacy child; never signal the parent's group.
        if started and not ready:
            if not wait([child.sentinel], timeout=0):
                child.kill()
            child.join(grace)
        if started and ready and not cleaned:
            cleanup_group(child, term_grace=grace, kill_wait=grace,
                          exit_probe=lambda _: bool(wait([child.sentinel], timeout=0)), reap=lambda: child.join(0))
        parent.close()
        child_control.close()
        with contextlib.suppress(OSError):
            os.close(receiver)
        if sender is not None:
            with contextlib.suppress(OSError):
                os.close(sender)
        if started and wait([child.sentinel], timeout=0):
            child.join(0)
            child.close()


def run_approval(verifier, request, directory=None, *, timeout=5, grace=.2, cwd=None):
    if isinstance(verifier, dict):
        directory = directory or (cwd or Path.cwd()) / '.mission-state' / 'exec-jobs'
        try:
            return run_job('approval-verifier', {'verifier': verifier, 'request': request}, directory,
                           timeout=timeout, term_grace=grace, kill_wait=grace, cwd=cwd, session_id=request['session_id'])
        except Exception as exc:
            raise ValueError('approval verifier rejected the evidence') from exc
    return run_callable(verifier, request, timeout=timeout, grace=grace)


def verify_approval_request(request, verifier_name, *, verifiers, resolve, execute, cwd=None,
                            budgeted=False, state=None):
    if not isinstance(verifier_name, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', verifier_name):
        raise ValueError('approval verifier is invalid or not configured')
    if state is not None and (not isinstance(state, dict) or not isinstance(state.get('extensions', {}), dict)):
        raise ValueError('approval state is invalid')
    verifier = verifiers.get(verifier_name)
    policy_present = state is not None and ('budget_ledger' in state or 'budget_ledger' in (state.get('extensions') or {}))
    if verifier is not None and (budgeted or policy_present):
        raise ValueError('budget-deadline-unenforceable')
    descriptor = resolve(cwd, verifier_name) if verifier is None and cwd is not None else None
    if verifier is None and descriptor is None:
        raise ValueError('approval verifier is not configured')
    try:
        result = execute(verifier if verifier is not None else descriptor, request, cwd=cwd)
    except Exception as exc:
        raise ValueError('approval verifier rejected the evidence') from exc
    try:
        validated = validate_recorded_envelope({'request':request, 'response':result,
                                               'receipt_ref':result.get('receipt_ref'), 'consumed':True})
    except (AttributeError, ValueError) as exc:
        raise ValueError('approval verifier did not return a verified envelope') from exc
    if validated['response']['verifier_id'] != verifier_name:
        raise ValueError('approval verifier did not return a verified envelope')
    return validated
