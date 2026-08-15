import json

from .test_issue501_k2_parity import _actual_cli_snapshots
from .mission_state_fixture_corpus import generate_cli_state_corpus


def test_measure(tmp_path):
    snapshots = _actual_cli_snapshots(generate_cli_state_corpus(tmp_path.resolve()))
    print("K2_SNAPSHOT_COUNT", len(snapshots))
    sizes = [
        (
            name,
            len(
                json.dumps(
                    state,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ),
        )
        for name, state in snapshots
    ]
    print("K2_MAX_COMPACT", max(sizes, key=lambda item: item[1]))
    fields = (
        "specialists_selected",
        "specialist_invocations",
        "provider_plan_imports",
        "review_tier_signals",
    )
    for field in fields:
        values = []
        for name, state in snapshots:
            default = {} if field == "provider_plan_imports" else []
            value = state.get(field, default)
            values.append(
                (
                    name,
                    len(value),
                    len(
                        json.dumps(
                            value,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ),
                )
            )
        print(
            "K2_FIELD",
            field,
            max(values, key=lambda item: item[1]),
            max(values, key=lambda item: item[2]),
        )
