"""A decoder must bound stored values, never a subclass's reported value."""
from copy import deepcopy

import pytest

from mission_kernel.fresh_review import (
    FreshReviewError, decode_request, decode_projection, request_document,
    projection_document, validate_budgets, BUDGET_LIMITS, candidate_identity,
)
from mission_kernel.fresh_review_receipts import (
    decode_launch_receipt, decode_terminal_receipt, validate_dispatch_intent,
    validate_running_record,
)
from .test_issue895_fresh_review import _pure_projection
from .test_issue909_fresh_review_receipts import launch_document, terminal_document, evidence
from .test_issue917_fresh_review_bounds import maximum_intent, maximum_running, maximum_terminal


class DictSubclass(dict):
    pass


class LyingList(list):
    def __len__(self):
        return min(list.__len__(self), 61)


class LyingString(str):
    def __len__(self):
        return min(str.__len__(self), 128)

    def isascii(self):
        return True


class IntSubclass(int):
    pass


def entry_documents():
    projection = _pure_projection()
    return [
        ('request', decode_request, request_document(projection.requests[0].request)),
        ('projection', decode_projection, {'fresh_review': projection_document(projection)}),
        ('launch', decode_launch_receipt, launch_document()),
        *[(outcome, decode_terminal_receipt, terminal_document(outcome))
          for outcome in ('completed', 'failed', 'blocked', 'abandoned-unknown')],
        ('intent', validate_dispatch_intent, maximum_intent()),
        ('running', validate_running_record, maximum_running()),
        ('budgets', validate_budgets, dict(BUDGET_LIMITS)),
        ('candidate', candidate_identity, {'command-1': 'sha256:' + 'a' * 64}),
    ]


def nodes(value, path=()):
    yield path, value
    if type(value) is dict:
        for key, item in value.items():
            yield from nodes(item, path + (key,))
    elif type(value) is list:
        for index, item in enumerate(value):
            yield from nodes(item, path + (index,))


def replaced(value, path, item):
    raw = deepcopy(value)
    if not path:
        return item
    target = raw
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = item
    return raw


def subclass_cases():
    classes = {dict: DictSubclass, list: LyingList, str: LyingString, int: IntSubclass}
    for name, decoder, raw in entry_documents():
        for path, item in nodes(raw):
            if type(item) in classes:
                yield pytest.param(decoder, replaced(raw, path, classes[type(item)](item)),
                    id=name + ':' + '.'.join(map(str, path)) + ':subclass')
            if type(item) is int:
                for invalid in (True, float(item)):
                    yield pytest.param(decoder, replaced(raw, path, invalid),
                        id=name + ':' + '.'.join(map(str, path)) + ':' + type(invalid).__name__)


@pytest.mark.parametrize('decoder,raw', list(subclass_cases()))
def test_every_input_node_rejects_overridable_builtins(decoder, raw):
    with pytest.raises(FreshReviewError):
        decoder(raw)


@pytest.mark.parametrize('decoder,raw,code', [
    (decode_request, dict(request_document(_pure_projection().requests[0].request),
                          perspective=LyingString('counterexamples')), 'perspective-invalid'),
    (decode_terminal_receipt, dict(maximum_terminal('completed'),
                                  findings=LyingList(maximum_terminal('completed')['findings'] + [
                                      dict(evidence('fresh-review-finding'), size=262144, digest='sha256:' + 'f' * 64,
                                           relative_path='evidence/fresh-review/' + 'f' * 64 + '.json')])), 'findings-invalid'),
    (validate_dispatch_intent, dict(maximum_intent(), budget_class=LyingString('x' * 2048)), 'budget-class-invalid'),
    (decode_launch_receipt, dict(launch_document(), context_mode=LyingString('fresh')), 'context-invalid'),
    (validate_running_record, dict(maximum_running(), status=LyingString('running')), 'running-invalid'),
])
def test_subclass_rejection_preserves_field_reason(decoder, raw, code):
    with pytest.raises(FreshReviewError, match='^fresh-review-' + code + '$'):
        decoder(raw)


@pytest.mark.parametrize('with_projection', [False, True])
def test_plain_json_scores_outside_d_keep_existing_projection_behavior(with_projection):
    raw = {'score': 0.5, 'legacy_score': float('nan')}
    if with_projection:
        raw['fresh_review'] = projection_document(_pure_projection())
    expected = decode_projection({'fresh_review': raw['fresh_review']} if with_projection else {})
    assert decode_projection(raw) == expected


def test_plain_json_result_keeps_its_existing_free_form_numbers():
    from dataclasses import replace
    from mission_kernel.json_codec import freeze_json_value
    projection = _pure_projection()
    record = replace(projection.requests[0], status='consumed', operation_id='dispatch',
                     intent_digest='sha256:' + 'b' * 64, payload_digest='sha256:' + 'c' * 64,
                     result=freeze_json_value({'score': 0.5}))
    projection = replace(projection, requests=(record,))
    assert decode_projection({'fresh_review': projection_document(projection)}) == projection


@pytest.mark.parametrize('name,decoder,raw', entry_documents(), ids=lambda x: x if type(x) is str else None)
def test_subclass_keys_and_list_roots_are_never_json_objects(name, decoder, raw):
    key = next(iter(raw))
    keyed = {LyingString(key) if field == key else field: value for field, value in raw.items()}
    for invalid in (keyed, LyingList([raw])):
        with pytest.raises(FreshReviewError):
            decoder(invalid)


@pytest.mark.parametrize('container', [dict, list])
def test_cyclic_input_is_rejected_with_a_closed_shape_reason(container):
    raw = container()
    if container is dict:
        raw['cycle'] = raw
    else:
        raw.append(raw)
    with pytest.raises(FreshReviewError, match='^fresh-review-projection-shape-invalid$'):
        decode_projection({'extra': raw})


# Plain JSON (what a decoder produces) must keep the reason codes the
# closed-shape and field checks gave before the builtins walk existed.
PLAIN_REPLACEMENTS = (0.5, 1e308, -0.0, None, True, False, 0, -1, 2**63, '', 'x',
                      [], {}, [0.5], {'a': 0.5}, {'operation_id': 0.5})
PLAIN_EXTRA_FIELDS = (('operation_id', 0.5), ('operation_id', 'x'), ('zzz', 0.5),
                      ('status', 0.5), ('schema', 1.5), ('nonce', [0.5]))


def _outcome(decoder, raw):
    try:
        decoder(raw)
    except FreshReviewError as exc:
        return str(exc)
    except ValueError as exc:
        return 'value-error:' + str(exc)
    return 'accepted'


def plain_mutations(raw):
    for path, item in nodes(raw):
        for replacement in PLAIN_REPLACEMENTS:
            yield path, 'replace', replacement, replaced(raw, path, deepcopy(replacement))
        if type(item) is dict:
            for key, value in PLAIN_EXTRA_FIELDS:
                if key not in item:
                    yield path, 'extra', key, replaced(raw, path, dict(item, **{key: value}))


@pytest.mark.parametrize('name,decoder,raw', entry_documents(), ids=lambda x: x if type(x) is str else None)
def test_plain_json_reason_codes_do_not_depend_on_the_builtins_walk(name, decoder, raw, monkeypatch):
    import mission_kernel.fresh_review as fresh_review
    import mission_kernel.fresh_review_receipts as receipts
    cases = list(plain_mutations(raw))
    with_walk = [_outcome(decoder, case[-1]) for case in cases]
    monkeypatch.setattr(fresh_review, '_json_builtins', lambda *args, **kwargs: None)
    monkeypatch.setattr(receipts, '_json_builtins', lambda *args, **kwargs: None)
    without_walk = [_outcome(decoder, case[-1]) for case in cases]
    differing = [(case[:3], walked, plain) for case, walked, plain
                 in zip(cases, with_walk, without_walk) if walked != plain]
    assert len(cases) > 30
    assert not differing, differing[:5]


@pytest.mark.parametrize('name,decoder,raw', entry_documents(), ids=lambda x: x if type(x) is str else None)
def test_plain_floats_never_reach_a_typed_field(name, decoder, raw):
    accepted = [path for path, item in nodes(raw) if path and type(item) is not dict
                for value in (0.5, 1.0, 1e308)
                if _outcome(decoder, replaced(raw, path, value)) == 'accepted']
    assert accepted == []


def _deep(depth):
    root = current = []
    for _ in range(depth):
        child = []
        current.append(child)
        current = child
    return root


@pytest.mark.parametrize('name,decoder,raw', entry_documents(), ids=lambda x: x if type(x) is str else None)
def test_nesting_too_deep_to_walk_is_rejected_with_the_entry_reason(name, decoder, raw):
    import sys
    deep = dict(raw, zzz=_deep(sys.getrecursionlimit() * 2))
    with pytest.raises(FreshReviewError) as caught:
        decoder(deep)
    assert type(caught.value.__cause__) is RecursionError
    assert str(caught.value) == 'fresh-review-' + ENTRY_REASONS[name]


# The reason each entry's builtins walk reports for the whole tree.
ENTRY_REASONS = {
    'request': 'request-shape-invalid', 'projection': 'projection-shape-invalid',
    'launch': 'launch-shape-invalid', 'completed': 'terminal-shape-invalid',
    'failed': 'terminal-shape-invalid', 'blocked': 'terminal-shape-invalid',
    'abandoned-unknown': 'terminal-shape-invalid', 'intent': 'dispatch-invalid',
    'running': 'running-invalid', 'budgets': 'budget-invalid', 'candidate': 'candidate-invalid',
}


def test_entry_reasons_cover_every_entry_document():
    assert set(ENTRY_REASONS) == {name for name, _, _ in entry_documents()}
