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


def _expected_target(action, base, request):
    """The lease the admission should produce, built without asking it.

    A takeover mints its identifier, so that one field is read back; every
    other field is stated here, including the epoch and the history entry the
    superseded lease leaves behind.
    """
    if action == "renewed":
        return FencedLease(
            base.owner_session_id,
            base.lease_id,
            base.fencing_epoch,
            _text(max(_moment(base.lease_expires_at), NOW + timedelta(seconds=TTL))),
            base.lease_history,
        )
    assert action == "taken-over"
    return FencedLease(
        request.lease_owner_session_id,
        request.presented_lease_id or _MINTED,
        base.fencing_epoch + 1,
        _text(NOW + timedelta(seconds=TTL)),
        base.lease_history
        + (
            LeaseHistoryEntry(
                owner_session_id=base.owner_session_id,
                lease_id=base.lease_id,
                fencing_epoch=base.fencing_epoch,
                reason="lease-expired-takeover",
                at=_text(NOW),
            ),
        ),
    )


def _moment(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class _Minted:
    """Stands for the identifier a takeover mints, which cannot be predicted."""

    def __eq__(self, other):
        return isinstance(other, str) and len(other) == 32 and _HEX.issuperset(other)

    def __repr__(self):  # pragma: no cover - only for assertion output
        return "<minted token>"


_HEX = set("0123456789abcdef")
_MINTED = _Minted()


TOKEN_REQUIRED = ("lease-token-required", "the matching owner omitted its token")
STILL_LIVE = ("lease-rejected", "the current fenced lease is still live")
RETIRED_TOKEN = ("stale-fencing-token", "the presented token is retired")

# The whole input space of the branch: the lease is live or expired, the owner
# matches or does not, and the token is absent, current, retired or fresh.
# Each row carries what the admission produces, so a refusal is pinned by its
# detail as well as its code, and a success by the fields the commit reads.
SPACE = [
    ("live", "s-a", None, TOKEN_REQUIRED),
    ("live", "s-a", CURRENT, ("renewed", True)),
    ("live", "s-a", RETIRED, STILL_LIVE),
    ("live", "s-a", FRESH, STILL_LIVE),
    ("live", "s-b", None, STILL_LIVE),
    ("live", "s-b", CURRENT, STILL_LIVE),
    ("live", "s-b", RETIRED, STILL_LIVE),
    ("live", "s-b", FRESH, STILL_LIVE),
    ("expired", "s-a", None, ("taken-over", False)),
    ("expired", "s-a", CURRENT, ("renewed", False)),
    ("expired", "s-a", RETIRED, RETIRED_TOKEN),
    ("expired", "s-a", FRESH, ("taken-over", False)),
    ("expired", "s-b", None, ("taken-over", False)),
    ("expired", "s-b", CURRENT, RETIRED_TOKEN),
    ("expired", "s-b", RETIRED, RETIRED_TOKEN),
    ("expired", "s-b", FRESH, ("taken-over", False)),
]
REFUSAL_CODES = {TOKEN_REQUIRED[0], STILL_LIVE[0], RETIRED_TOKEN[0]}


@pytest.mark.parametrize("state,owner,token,expected", SPACE)
def test_the_refusal_covers_the_whole_branch(state, owner, token, expected):
    """Sixteen inputs, measured against the behaviour being preserved."""
    base = _base(expired=state == "expired")

    refusal = classify_lease(_Request(owner, token), base, NOW)

    if expected[0] in REFUSAL_CODES:
        assert (refusal.code, refusal.detail) == expected
    else:
        assert refusal is None


@pytest.mark.parametrize("state,owner,token,expected", SPACE)
def test_admit_lease_produces_exactly_what_it_did_before(state, owner, token, expected):
    """The caller of the classifier must not add, drop or reword a refusal.

    A success is pinned by every field the commit later reads -- the action
    and ``base_was_live`` among them, since neither is recoverable from the
    digest.
    """
    base = _base(expired=state == "expired")
    request = _Request(owner, token)

    if expected[0] in REFUSAL_CODES:
        with pytest.raises(FencedCommitError) as refusal:
            admit_lease(request, base, NOW, TTL)
        assert (refusal.value.code, refusal.value.detail) == expected
        return

    pending = admit_lease(request, base, NOW, TTL)
    action, base_was_live = expected
    assert pending.action == action
    assert pending.base_was_live is base_was_live
    assert pending.base == base
    assert pending.admitted_at == _text(NOW)
    assert pending.target == _expected_target(action, base, request)


def test_a_retired_token_is_found_beyond_the_first_history_entry():
    """The history is searched, not sampled.

    Reading only its first entry leaves every earlier takeover able to present
    a token the rules retired.
    """
    older = LeaseHistoryEntry(
        owner_session_id="s-a",
        lease_id="c" * 32,
        fencing_epoch=1,
        reason="lease-expired-takeover",
        at="2025-12-30T00:00:00Z",
    )
    newer = LeaseHistoryEntry(
        owner_session_id="s-a",
        lease_id="d" * 32,
        fencing_epoch=2,
        reason="lease-expired-takeover",
        at="2025-12-31T00:00:00Z",
    )
    base = FencedLease(
        "s-a", CURRENT, 3, _text(NOW - timedelta(seconds=1)), (older, newer)
    )

    for entry in (older, newer):
        refusal = classify_lease(_Request("s-b", entry.lease_id), base, NOW)
        assert refusal is not None, entry.lease_id
        assert (refusal.code, refusal.detail) == RETIRED_TOKEN


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
    flag on an otherwise identical result leaves the digest untouched.
    """
    import dataclasses

    pending = admit_lease(_Request("s-a", CURRENT), _base(expired=False), NOW, TTL)
    flipped = dataclasses.replace(pending, base_was_live=not pending.base_was_live)

    assert flipped.digest == pending.digest
    assert flipped.base_was_live != pending.base_was_live


def test_a_takeover_with_a_supplied_identifier_is_fixed_whole():
    """Injecting the identifier removes the one unpredictable field.

    With it supplied, every field of the superseding lease and its digest can
    be written down, which is what the parametrised cases cannot do while the
    identifier is minted.
    """
    base = _base(expired=True)

    pending = admit_lease(
        _Request("s-b", None), base, NOW, TTL, generated_lease_id="b" * 32
    )

    assert pending.action == "taken-over"
    assert pending.base_was_live is False
    assert pending.target == FencedLease(
        "s-b",
        "b" * 32,
        base.fencing_epoch + 1,
        _text(NOW + timedelta(seconds=TTL)),
        base.lease_history
        + (
            LeaseHistoryEntry(
                owner_session_id="s-a",
                lease_id=CURRENT,
                fencing_epoch=1,
                reason="lease-expired-takeover",
                at=_text(NOW),
            ),
        ),
    )
    assert pending.digest == (
        "sha256:ebb8dbe8bf9faacaa712d42ae015332df9c3dbf35b3683d8c94cb2966b7362eb"
    )


def test_a_minted_identifier_is_never_one_the_rules_have_retired():
    """Reusing the superseded token would defeat the fencing it establishes."""
    base = _base(expired=True)

    minted = {
        admit_lease(_Request("s-b", None), base, NOW, TTL).target.lease_id
        for _ in range(20)
    }

    assert CURRENT not in minted
    assert RETIRED not in minted
    assert len(minted) == 20, "a minted identifier that repeats is not minted"


def test_the_digest_of_a_renewal_is_the_value_it_has_always_had():
    """Recorded from the behaviour being preserved, not recomputed from it.

    Asking the production helper for the expectation would agree with any
    digest it happens to produce.  This renewal has no minted identifier, so
    its digest is fixed and can be written down.
    """
    pending = admit_lease(_Request("s-a", CURRENT), _base(expired=False), NOW, TTL)

    assert pending.digest == (
        "sha256:d05ef86634b5b4148771970d6056777c54b5198667dfb0c05ee16b02cf70471c"
    )
