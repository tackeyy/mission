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
