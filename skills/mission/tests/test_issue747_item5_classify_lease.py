"""#747 item 5: the lease refusal is one function, asked without minting a lease.

``admit_lease`` used to hold the refusal conditions inside its control flow, so
the only way to ask "is this refused?" was to also mint a lease.  Item 5 needs
the answer on its own.  Extracting it is only safe if the extraction changes
nothing, which is what these fixtures fix: the whole input space of the branch,
compared field by field.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mission_persistence.fenced_commit import (  # noqa: E402
    FencedCommitError,
    FencedLease,
    LeaseHistoryEntry,
    LegacyAbsentLease,
    admit_lease,
    classify_lease,
)

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
TTL = 900
CURRENT = "a" * 32
RETIRED = "h" * 32
FRESH = "n" * 32
HISTORY = (
    LeaseHistoryEntry(
        owner_session_id="s-a",
        lease_id=RETIRED,
        fencing_epoch=1,
        reason="lease-expired-takeover",
        at="2025-12-31T00:00:00Z",
    ),
)


class _Request:
    def __init__(self, owner: str, token: str | None) -> None:
        self.lease_owner_session_id = owner
        self.presented_lease_id = token


def _text(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _base(*, expired: bool, expires_at: datetime | None = None) -> FencedLease:
    if expires_at is None:
        expires_at = NOW - timedelta(seconds=1) if expired else NOW + timedelta(seconds=60)
    return FencedLease("s-a", CURRENT, 1, _text(expires_at), HISTORY)


# The whole input space of the branch: the lease is live or expired, the owner
# matches or does not, and the token is absent, current, retired or fresh.
SPACE = [
    ("live", "s-a", None, "lease-token-required"),
    ("live", "s-a", CURRENT, None),
    ("live", "s-a", RETIRED, "lease-rejected"),
    ("live", "s-a", FRESH, "lease-rejected"),
    ("live", "s-b", None, "lease-rejected"),
    ("live", "s-b", CURRENT, "lease-rejected"),
    ("live", "s-b", RETIRED, "lease-rejected"),
    ("live", "s-b", FRESH, "lease-rejected"),
    ("expired", "s-a", None, None),
    ("expired", "s-a", CURRENT, None),
    ("expired", "s-a", RETIRED, "stale-fencing-token"),
    ("expired", "s-a", FRESH, None),
    ("expired", "s-b", None, None),
    ("expired", "s-b", CURRENT, "stale-fencing-token"),
    ("expired", "s-b", RETIRED, "stale-fencing-token"),
    ("expired", "s-b", FRESH, None),
]


@pytest.mark.parametrize("state,owner,token,expected", SPACE)
def test_the_refusal_covers_the_whole_branch(state, owner, token, expected):
    """Sixteen inputs, measured against the behaviour being preserved."""
    base = _base(expired=state == "expired")

    refusal = classify_lease(_Request(owner, token), base, NOW)

    assert (refusal.code if refusal is not None else None) == expected


@pytest.mark.parametrize("state,owner,token,expected", SPACE)
def test_admit_lease_raises_exactly_what_the_refusal_says(state, owner, token, expected):
    """The caller of the classifier must not add or drop a refusal."""
    base = _base(expired=state == "expired")
    request = _Request(owner, token)

    if expected is None:
        assert admit_lease(request, base, NOW, TTL) is not None
        return
    with pytest.raises(FencedCommitError) as refusal:
        admit_lease(request, base, NOW, TTL)
    assert refusal.value.code == expected


def test_an_absent_lease_is_acquired_rather_than_refused():
    base = LegacyAbsentLease()

    assert classify_lease(_Request("s-a", None), base, NOW) is None

    pending = admit_lease(_Request("s-a", None), base, NOW, TTL)
    assert pending.action == "acquired"
    assert pending.base_was_live is False
    assert pending.target.lease_expires_at == _text(NOW + timedelta(seconds=TTL))


def test_the_instant_the_lease_expires_counts_as_expired():
    """``admitted_at < base_expiry`` -- the boundary itself is not live.

    Only this input separates ``<`` from ``<=``.
    """
    base = _base(expired=False, expires_at=NOW)

    assert classify_lease(_Request("s-b", None), base, NOW) is None

    pending = admit_lease(_Request("s-b", None), base, NOW, TTL)
    assert pending.action == "taken-over"
    assert pending.base_was_live is False


def test_a_renewal_keeps_whichever_expiry_is_later():
    """``max(base_expiry, admitted_at + ttl)`` -- both sides have to be held."""
    far = _base(expired=False, expires_at=NOW + timedelta(seconds=3600))
    near = _base(expired=False, expires_at=NOW + timedelta(seconds=60))

    kept = admit_lease(_Request("s-a", CURRENT), far, NOW, TTL)
    extended = admit_lease(_Request("s-a", CURRENT), near, NOW, TTL)

    assert kept.target.lease_expires_at == _text(NOW + timedelta(seconds=3600))
    assert extended.target.lease_expires_at == _text(NOW + timedelta(seconds=TTL))


def test_a_presented_token_is_checked_before_the_lease_is_classified():
    """The input is one the classifier would refuse for a different reason.

    A renewal would report ``request-invalid`` either way, so it cannot say
    which check ran first; a live lease with a different owner would report
    ``lease-rejected`` if the classifier went first.
    """
    base = _base(expired=False)

    with pytest.raises(FencedCommitError) as refusal:
        admit_lease(_Request("s-b", CURRENT), base, NOW, TTL, generated_lease_id="g" * 32)

    assert refusal.value.code == "request-invalid"
    assert refusal.value.detail == "generated token cannot replace a presented token"


def test_the_clock_is_normalised_before_the_token_is_validated():
    """A naive clock is reported even when the generated token is also unusable."""
    base = _base(expired=False)

    with pytest.raises(FencedCommitError) as refusal:
        admit_lease(
            _Request("s-a", None),
            base,
            datetime(2026, 1, 1, 12, 0, 0),
            TTL,
            generated_lease_id="",
        )

    assert refusal.value.code == "request-invalid"
    assert refusal.value.detail == "clock must return an aware datetime"


def test_an_unusable_generated_token_is_refused_before_the_lease():
    base = _base(expired=False)

    with pytest.raises(FencedCommitError) as refusal:
        admit_lease(_Request("s-a", None), base, NOW, TTL, generated_lease_id="")

    assert refusal.value.code == "record-invalid"
    assert refusal.value.detail == "generated_lease_id is not a Token128"


def test_admit_lease_asks_the_classifier_rather_than_repeating_it(monkeypatch):
    """Two copies of the conditions is how the two would drift apart."""
    import mission_persistence.fenced_commit as module

    monkeypatch.setattr(
        module,
        "classify_lease",
        lambda *_arguments: module.LeaseRefusal("substituted", "substituted"),
    )

    with pytest.raises(FencedCommitError) as refusal:
        admit_lease(_Request("s-a", CURRENT), _base(expired=False), NOW, TTL)

    assert refusal.value.code == "substituted"


def test_base_was_live_is_not_covered_by_the_digest():
    """So a golden that compares digests alone would miss it changing.

    The field decides whether the commit may proceed, while the digest is
    built from the action, the two leases and the timestamp.  Flipping the
    flag on an otherwise identical result leaves the digest untouched, so a
    test that compares digests would not notice.
    """
    import dataclasses

    import mission_persistence.fenced_commit as module

    pending = admit_lease(_Request("s-a", CURRENT), _base(expired=False), NOW, TTL)
    flipped = dataclasses.replace(pending, base_was_live=not pending.base_was_live)

    assert flipped.digest == pending.digest
    assert flipped.base_was_live != pending.base_was_live
    # And the digest really is a function of those four, not of the flag.
    assert module._pending_digest(
        pending.action, pending.base, pending.target, pending.admitted_at
    ) == pending.digest
