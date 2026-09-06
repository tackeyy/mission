#!/usr/bin/env python3
"""Enforce the monotonic thin-adapter violation budget."""

from __future__ import annotations

import ast
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterable, NamedTuple


RULE_IDS = frozenset(
    {
        "call.non-allowlisted",
        "control.branch",
        "control.loop",
        "dispatch.dynamic",
        "io.direct",
        "logic.arithmetic",
        "logic.boolean",
        "logic.business-container",
        "logic.compare",
        "logic.comprehension",
        "logic.threshold-literal",
        "state.mutation",
        "state.raw-access",
        "time.policy",
    }
)
SOURCE_PATH = Path("skills/mission/bin/mission-state.py")
BASELINE_PATH = Path("skills/mission/tests/fixtures/thin-adapter-baseline.jsonl")
BASE_SHA_ENV = "THIN_ADAPTER_BASE_SHA"


class Violation(NamedTuple):
    path: str
    function: str
    rule_id: str
    lineno: int


class BaselineError(ValueError):
    """Raised when a ratchet baseline is malformed or noncanonical."""


Baseline = dict[tuple[str, str], dict[str, int]]


_CONTROL_RULES = {
    ast.If: "control.branch",
    ast.IfExp: "control.branch",
    ast.For: "control.loop",
    ast.AsyncFor: "control.loop",
    ast.While: "control.loop",
    ast.Compare: "logic.compare",
    ast.BoolOp: "logic.boolean",
    ast.BinOp: "logic.arithmetic",
    ast.ListComp: "logic.comprehension",
    ast.SetComp: "logic.comprehension",
    ast.DictComp: "logic.comprehension",
    ast.GeneratorExp: "logic.comprehension",
}
if hasattr(ast, "Match"):
    _CONTROL_RULES[ast.Match] = "control.branch"

_MUTATION_METHODS = {"append", "extend", "pop", "setdefault", "update"}
_TIME_NAMES = {"datetime", "math", "time", "timedelta"}
_STATE_NAMES = {"data", "document", "state"}
_IO_METHODS = {
    "exists",
    "is_dir",
    "is_file",
    "iterdir",
    "mkdir",
    "open",
    "read_bytes",
    "read_text",
    "replace",
    "unlink",
    "write_bytes",
    "write_text",
}
_ALLOWED_BUILTIN_CALLS = {
    "Path",
    "bool",
    "float",
    "int",
    "list",
    "print",
    "str",
    "tuple",
}
_ALLOWED_MODULE_CALLS = {
    "argparse": {"ArgumentParser"},
    "json": {"dumps"},
    "sys": {"exit"},
}
_PARSER_METHODS = {
    "add_argument",
    "add_mutually_exclusive_group",
    "add_parser",
    "add_subparsers",
    "set_defaults",
}


def _top_level_functions(tree: ast.Module) -> dict[str, list[ast.AST]]:
    """Return the module's own functions, including those defined under control flow.

    ``if TYPE_CHECKING: def f(): ...`` and its cousins define module-level
    names just as a bare ``def`` does, so leaving them out would let a
    function be defined and injected without ever entering the budget.  Class
    bodies are not included: their functions are attributes, not module names.

    A name maps to *every* definition that binds it, not just the first.
    Which one survives to run depends on a condition this pass does not
    evaluate, so all of them are measured: otherwise a trivial definition
    placed ahead of the real one would decide the budget.
    """
    functions: dict[str, list[ast.AST]] = {}
    _collect_module_functions(tree.body, functions)
    return functions


def _collect_module_functions(body, functions: dict[str, list[ast.AST]]) -> None:
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.setdefault(node.name, []).append(node)
        elif isinstance(node, ast.ClassDef):
            continue
        else:
            for _field, value in ast.iter_fields(node):
                for item in value if isinstance(value, list) else [value]:
                    if isinstance(item, ast.stmt):
                        _collect_module_functions([item], functions)
                    elif isinstance(item, ast.ExceptHandler):
                        _collect_module_functions(item.body, functions)
                    elif hasattr(ast, "match_case") and isinstance(item, ast.match_case):
                        _collect_module_functions(item.body, functions)


_DYNAMIC_LOOKUP_NAMES = {"globals", "locals", "vars"}


def _import_time_expressions(body):
    """Yield the expressions that run when the module is imported.

    A definition introduces a name rather than running it, so its body is not
    import-time code -- but its decorators and default arguments are, and so
    is a class body.  A nested definition inside a module-level ``if`` is
    skipped for the same reason a top-level one is: nothing has called it.
    """
    for node in body:
        yield from _import_time_expressions_of(node)


def _import_time_expressions_of(node: ast.AST):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        yield from node.decorator_list
        yield from node.args.defaults
        yield from (default for default in node.args.kw_defaults if default is not None)
        if node.returns is not None:
            yield node.returns
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            if argument.annotation is not None:
                yield argument.annotation
        for argument in (node.args.vararg, node.args.kwarg):
            if argument is not None and argument.annotation is not None:
                yield argument.annotation
        return
    if isinstance(node, ast.ClassDef):
        yield from node.decorator_list
        yield from node.bases
        yield from (keyword.value for keyword in node.keywords)
        yield from _import_time_expressions(node.body)
        return
    for _field, value in ast.iter_fields(node):
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, ast.stmt):
                yield from _import_time_expressions_of(item)
            elif isinstance(item, ast.ExceptHandler):
                yield from _import_time_expressions(item.body)
                if item.type is not None:
                    yield item.type
            elif hasattr(ast, "match_case") and isinstance(item, ast.match_case):
                yield from _import_time_expressions(item.body)
                if item.guard is not None:
                    yield item.guard
            elif isinstance(item, ast.AST):
                yield item


def _names_run_at_import(body, functions: dict[str, list[ast.AST]]):
    """Yield every top-level function named by code that runs at import time."""
    for expression in _import_time_expressions(body):
        for inner in ast.walk(expression):
            if (
                isinstance(inner, ast.Name)
                and isinstance(inner.ctx, ast.Load)
                and inner.id in functions
            ):
                yield inner.id


def _import_time_lookup_is_dynamic(body) -> bool:
    """Say whether import-time code reaches a function by a computed name.

    ``Services(globals()["_helper"])`` hands the helper over without naming
    it, so no name-based search can find it.  Rather than let that take the
    helper out of the budget, every function is treated as reachable.

    **What this does not see.**  The detection recognises the builtins by
    name, so an alias (``lookup = globals`` then ``lookup()[name]``) or a
    route through ``sys.modules`` is not caught.  Neither is a lookup one
    frame deeper -- only import-time expressions are scanned, so
    ``def _wire(): return globals()["_helper"]`` called as ``SERVICES =
    _wire()`` does not put ``_helper`` in the budget.  Writing a *new*
    ``globals()`` does add a ``dispatch.dynamic`` the ratchet would stop on,
    but pointing an *existing* one at a different helper leaves the count
    unchanged, so that is not a safeguard either.

    Rooting every function unconditionally would close all of these, at the
    cost of putting functions nobody calls in the budget for good (462 -> 480
    in this repository), which stops the number from measuring how thin the
    adapter is.  Picking these forms out *without* that over-count needs
    value tracking, which an AST pass cannot do.  This guard exists to stop a
    refactor from quietly shrinking the budget, not to withstand someone
    arranging to evade it; a reviewer reading such code is the remaining
    check.
    """
    for expression in _import_time_expressions(body):
        for inner in ast.walk(expression):
            if not isinstance(inner, ast.Call) or not isinstance(inner.func, ast.Name):
                continue
            if inner.func.id in _DYNAMIC_LOOKUP_NAMES:
                return True
            if inner.func.id == "getattr" and (
                len(inner.args) < 2 or not isinstance(inner.args[1], ast.Constant)
            ):
                return True
    return False


def _handler_roots(tree: ast.Module, functions: dict[str, list[ast.AST]]) -> set[str]:
    roots = {
        name
        for name in functions
        if name == "main" or name.startswith(("cmd_", "_cmd_"))
    }
    # A helper handed to a use case at module level still runs on every
    # invocation of the commands that use it, but no handler mentions it by
    # name any more.  Without this, injecting a helper rather than calling it
    # takes it out of the budget: the count falls because the guard stopped
    # looking, not because the adapter got thinner.
    #
    # Every module-level statement counts, not only an assignment: a bare
    # ``register(_helper)`` hands the helper over just as effectively, and so
    # does one inside a module-level ``if`` or ``for``, a decorator, a default
    # argument, or a class body.  What is skipped is a *function body*, which
    # runs only when called and is scanned as its own function.
    if _import_time_lookup_is_dynamic(tree.body):
        # A computed name cannot be searched for; keeping everything in the
        # budget is the fail-closed answer.
        roots.update(functions)
    else:
        roots.update(_names_run_at_import(tree.body, functions))
    for function in _every_definition(functions):
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "set_defaults":
                continue
            roots.add(function.name)
            for keyword in node.keywords:
                if keyword.arg == "func" and isinstance(keyword.value, ast.Name):
                    roots.add(keyword.value.id)
    return roots


def _every_definition(functions: dict[str, list[ast.AST]]):
    for definitions in functions.values():
        yield from definitions


def _reachable_functions(
    tree: ast.Module,
    *,
    extra_roots: Iterable[str] = (),
) -> list[ast.AST]:
    functions = _top_level_functions(tree)
    pending = list(_handler_roots(tree, functions) | set(extra_roots))
    reached: set[str] = set()
    while pending:
        name = pending.pop()
        if name in reached or name not in functions:
            continue
        reached.add(name)
        for definition in functions[name]:
            for node in ast.walk(definition):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    if node.id in functions and node.id not in reached:
                        pending.append(node.id)
    return [
        definition for name in sorted(reached) for definition in functions[name]
    ]


def _root_name(node: ast.AST) -> str | None:
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        if isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, ast.Attribute):
            node = node.value
        else:
            node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _import_origins(tree: ast.Module) -> dict[str, str]:
    origins: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                origins[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                origins[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return origins


def _is_parser_wiring(function: ast.AST) -> bool:
    return "parser" in function.name and any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"add_argument", "add_parser", "add_subparsers", "set_defaults"}
        for node in ast.walk(function)
    )


def _is_dynamic_dispatch(call: ast.Call) -> bool:
    target = call.func
    if isinstance(target, ast.Call):
        inner = target
        return isinstance(inner.func, ast.Name) and inner.func.id in {"getattr", "globals"}
    if isinstance(target, ast.Name) and target.id == "globals":
        return True
    if isinstance(target, ast.Name) and target.id == "getattr":
        return len(call.args) < 2 or not isinstance(call.args[1], ast.Constant)
    return False


def _call_is_allowlisted(
    call: ast.Call,
    *,
    imports: dict[str, str],
    local_functions: set[str],
    parser_wiring: bool,
) -> bool:
    if isinstance(call.func, ast.Name):
        name = call.func.id
        if parser_wiring and (name == "sorted" or name in local_functions):
            return True
        if name in _ALLOWED_BUILTIN_CALLS:
            return True
        if name == "getattr" and 2 <= len(call.args) <= 3:
            return isinstance(call.args[1], ast.Constant) and isinstance(
                call.args[1].value, str
            ) and (len(call.args) == 2 or isinstance(call.args[2], ast.Constant))
        origin = imports.get(name, "")
        return origin.startswith(
            (
                "mission_adapter.rendering.",
                "mission_application.",
                "mission_projection.",
            )
        )
    if isinstance(call.func, ast.Attribute):
        root = _root_name(call.func)
        if (
            root == "sys"
            and call.func.attr == "write"
            and isinstance(call.func.value, ast.Attribute)
            and call.func.value.attr in {"stderr", "stdout"}
        ):
            return True
        if call.func.attr in _PARSER_METHODS:
            return True
        if root in _ALLOWED_MODULE_CALLS:
            return call.func.attr in _ALLOWED_MODULE_CALLS[root]
        origin = imports.get(root or "", "")
        if origin.startswith(("mission_application", "mission_projection")):
            return True
        if origin.startswith("mission_adapter.rendering"):
            return True
    return False


def _application_call_origin(
    call: ast.Call,
    *,
    imports: dict[str, str],
) -> str | None:
    root: str | None = None
    if isinstance(call.func, ast.Name):
        root = call.func.id
    elif isinstance(call.func, ast.Attribute):
        root = _root_name(call.func)
    origin = imports.get(root or "", "")
    if not origin.startswith(("mission_application", "mission_projection")):
        return None
    leaf = (
        call.func.attr
        if isinstance(call.func, ast.Attribute)
        else origin.rsplit(".", 1)[-1]
    )
    if leaf.endswith(("Error", "Failure", "Observation", "Request", "Services")):
        return None
    return origin


def _application_use_case_calls(
    node: ast.AST,
    *,
    imports: dict[str, str],
) -> list[ast.Call]:
    return [
        candidate
        for candidate in ast.walk(node)
        if isinstance(candidate, ast.Call)
        and _application_call_origin(candidate, imports=imports) is not None
    ]


def _exception_is_application_failure(
    exception_type: ast.AST | None,
    *,
    imports: dict[str, str],
) -> bool:
    if isinstance(exception_type, ast.Tuple):
        return bool(exception_type.elts) and all(
            _exception_is_application_failure(item, imports=imports)
            for item in exception_type.elts
        )
    if isinstance(exception_type, ast.Name):
        origin = imports.get(exception_type.id, "")
        leaf = origin.rsplit(".", 1)[-1]
    elif isinstance(exception_type, ast.Attribute):
        origin = imports.get(_root_name(exception_type) or "", "")
        leaf = exception_type.attr
    else:
        return False
    return origin.startswith("mission_application.") and leaf.endswith(
        ("Error", "Failure")
    )


def _handler_is_output_and_exit_only(
    handler: ast.ExceptHandler,
    *,
    imports: dict[str, str],
    local_functions: set[str],
) -> bool:
    if not handler.body:
        return False
    rendered = False
    exited = False
    for statement in handler.body:
        if not isinstance(statement, (ast.Expr, ast.Raise)):
            return False
        if isinstance(statement, ast.Raise):
            if not (
                isinstance(statement.exc, ast.Call)
                and isinstance(statement.exc.func, ast.Name)
                and statement.exc.func.id == "SystemExit"
            ):
                return False
            exited = True
        for call in (item for item in ast.walk(statement) if isinstance(item, ast.Call)):
            if isinstance(call.func, ast.Name) and call.func.id == "SystemExit":
                exited = True
                continue
            if (
                isinstance(call.func, ast.Attribute)
                and _root_name(call.func) == "sys"
                and call.func.attr == "exit"
            ):
                exited = True
            if isinstance(call.func, ast.Name) and call.func.id == "print":
                rendered = True
            if (
                isinstance(call.func, ast.Attribute)
                and _root_name(call.func) == "sys"
                and call.func.attr == "write"
                and isinstance(call.func.value, ast.Attribute)
                and call.func.value.attr in {"stderr", "stdout"}
            ):
                rendered = True
            root = _root_name(call.func)
            if imports.get(root or "", "").startswith("mission_adapter.rendering"):
                rendered = True
            if _application_call_origin(call, imports=imports) is not None:
                return False
            if not _call_is_allowlisted(
                call,
                imports=imports,
                local_functions=local_functions,
                parser_wiring=False,
            ):
                return False
    return rendered and exited


def _try_is_named_failure_mapping(
    node: ast.Try,
    *,
    imports: dict[str, str],
    local_functions: set[str],
) -> bool:
    if node.orelse or node.finalbody or len(node.body) != 1 or not node.handlers:
        return False
    if len(_application_use_case_calls(node.body[0], imports=imports)) != 1:
        return False
    return all(
        _exception_is_application_failure(handler.type, imports=imports)
        and _handler_is_output_and_exit_only(
            handler,
            imports=imports,
            local_functions=local_functions,
        )
        for handler in node.handlers
    )


def _snapshot_fallback_node_ids(function: ast.AST) -> set[int]:
    """Allow only the compatibility facade's exact authoritative fallback."""
    if function.name != "_derive_next_action":
        return set()
    exempt: set[int] = set()
    for assignment in ast.walk(function):
        if not (
            isinstance(assignment, ast.Assign)
            and len(assignment.targets) == 1
            and isinstance(assignment.targets[0], ast.Name)
            and assignment.targets[0].id == "snapshot"
        ):
            continue
        node = assignment.value
        if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
            continue
        if len(node.values) != 2 or not isinstance(node.values[0], ast.Name):
            continue
        fallback = node.values[1]
        if (
            node.values[0].id == "authoritative"
            and isinstance(fallback, ast.Call)
            and isinstance(fallback.func, ast.Name)
            and fallback.func.id == "authoritative_snapshot_from_document"
            and len(fallback.args) == 1
            and isinstance(fallback.args[0], ast.Name)
            and fallback.args[0].id == "data"
            and not fallback.keywords
        ):
            exempt.update((id(node), id(fallback)))
    return exempt


def _iter_rule_hits(
    function: ast.AST,
    *,
    imports: dict[str, str],
    local_functions: set[str],
) -> Iterable[tuple[str, int]]:
    parser_wiring = _is_parser_wiring(function)
    callable_names = local_functions | {
        node.name
        for node in ast.walk(function)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    exit_literals = {
        id(argument)
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "SystemExit")
            or (
                isinstance(node.func, ast.Attribute)
                and _root_name(node.func) == "sys"
                and node.func.attr == "exit"
            )
        )
        for argument in node.args
        if isinstance(argument, ast.Constant) and argument.value in (0, 1, 2)
    }
    snapshot_fallback_nodes = _snapshot_fallback_node_ids(function)
    use_case_calls = _application_use_case_calls(function, imports=imports)
    for extra_call in use_case_calls[1:]:
        yield "call.non-allowlisted", extra_call.lineno
    for try_node in (item for item in ast.walk(function) if isinstance(item, ast.Try)):
        if not _try_is_named_failure_mapping(
            try_node,
            imports=imports,
            local_functions=callable_names,
        ):
            yield "call.non-allowlisted", try_node.lineno
    for node in ast.walk(function):
        rule_id = _CONTROL_RULES.get(type(node))
        if rule_id is not None and not (
            parser_wiring and rule_id == "logic.arithmetic"
        ) and id(node) not in snapshot_fallback_nodes:
            yield rule_id, node.lineno
            if isinstance(node, (ast.DictComp, ast.SetComp)):
                yield "logic.business-container", node.lineno

        if isinstance(node, ast.UnaryOp) and not parser_wiring:
            yield "logic.arithmetic", node.lineno
        if isinstance(node, (ast.Dict, ast.Set)) and not parser_wiring:
            yield "logic.business-container", node.lineno
        if (
            isinstance(node, ast.Constant)
            and type(node.value) in (int, float)
            and not parser_wiring
            and id(node) not in exit_literals
        ):
            yield "logic.threshold-literal", node.lineno
        if isinstance(node, ast.Subscript) and _root_name(node.value) in _STATE_NAMES:
            yield "state.raw-access", node.lineno
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                yield "state.mutation", node.lineno
        if isinstance(node, ast.Call):
            root = _root_name(node.func)
            attr = node.func.attr if isinstance(node.func, ast.Attribute) else None
            if root in _STATE_NAMES and attr == "get":
                yield "state.raw-access", node.lineno
            if attr in _MUTATION_METHODS:
                yield "state.mutation", node.lineno
            if root in _TIME_NAMES:
                yield "time.policy", node.lineno
            if root in {"os", "subprocess"} or attr in _IO_METHODS:
                yield "io.direct", node.lineno
            if id(node) in snapshot_fallback_nodes:
                continue
            if _is_dynamic_dispatch(node):
                yield "dispatch.dynamic", node.lineno
            elif not _call_is_allowlisted(
                node,
                imports=imports,
                local_functions=callable_names,
                parser_wiring=parser_wiring,
            ):
                yield "call.non-allowlisted", node.lineno


def scan_source(
    source: str,
    *,
    path: str,
    extra_roots: Iterable[str] = (),
) -> list[Violation]:
    """Return deterministic violations for reachable adapter functions."""
    tree = ast.parse(source, filename=path)
    imports = _import_origins(tree)
    local_functions = set(_top_level_functions(tree))
    violations = [
        Violation(path, function.name, rule_id, lineno)
        for function in _reachable_functions(tree, extra_roots=extra_roots)
        for rule_id, lineno in _iter_rule_hits(
            function,
            imports=imports,
            local_functions=local_functions,
        )
    ]
    return sorted(violations)


def scan_repository(repo_root: Path) -> Baseline:
    """Scan the canonical CLI and any extracted adapter modules."""
    repo_root = Path(repo_root)
    relative_paths = [Path("skills/mission/bin/mission-state.py")]
    adapter_root = repo_root / "skills" / "mission" / "lib" / "mission_adapter"
    if adapter_root.exists():
        relative_paths.extend(
            path.relative_to(repo_root)
            for path in sorted(adapter_root.rglob("*.py"))
        )
    violations: list[Violation] = []
    for relative_path in relative_paths:
        source = (repo_root / relative_path).read_text(encoding="utf-8")
        extra_roots: Iterable[str] = ()
        if "mission_adapter" in relative_path.parts:
            tree = ast.parse(source, filename=relative_path.as_posix())
            extra_roots = _top_level_functions(tree)
        violations.extend(
            scan_source(
                source,
                path=relative_path.as_posix(),
                extra_roots=extra_roots,
            )
        )
    return baseline_from_violations(violations)


def baseline_from_violations(violations: Iterable[Violation]) -> Baseline:
    counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for violation in violations:
        counts[(violation.path, violation.function)][violation.rule_id] += 1
    return {
        key: dict(sorted(rules.items()))
        for key, rules in sorted(counts.items())
        if rules
    }


def load_baseline_text(text: str) -> Baseline:
    baseline: Baseline = {}
    previous_key: tuple[str, str] | None = None
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            raise BaselineError(f"line {lineno}: blank lines are not allowed")
        try:
            record = json.loads(raw_line)
        except (TypeError, ValueError) as exc:
            raise BaselineError(f"line {lineno}: invalid JSON") from exc
        if not isinstance(record, dict) or set(record) != {"path", "function", "rules"}:
            raise BaselineError(f"line {lineno}: expected only path/function/rules")
        path = record["path"]
        function = record["function"]
        rules = record["rules"]
        if not isinstance(path, str) or not path or Path(path).is_absolute():
            raise BaselineError(f"line {lineno}: path must be repository-relative")
        if not isinstance(function, str) or not function:
            raise BaselineError(f"line {lineno}: function must be non-empty")
        if not isinstance(rules, dict) or not rules:
            raise BaselineError(f"line {lineno}: rules must be a non-empty object")
        normalized_rules: dict[str, int] = {}
        for rule_id, count in rules.items():
            if rule_id not in RULE_IDS:
                raise BaselineError(f"line {lineno}: unknown rule {rule_id!r}")
            if type(count) is not int or count <= 0:
                raise BaselineError(f"line {lineno}: count must be a positive integer")
            normalized_rules[rule_id] = count
        if list(rules) != sorted(rules):
            raise BaselineError(f"line {lineno}: rule ids must be sorted")
        key = (path, function)
        if key in baseline:
            raise BaselineError(f"line {lineno}: duplicate function key {key!r}")
        if previous_key is not None and key <= previous_key:
            raise BaselineError(f"line {lineno}: records must be sorted")
        baseline[key] = normalized_rules
        previous_key = key
    return baseline


def dump_baseline(baseline: Baseline) -> str:
    records = []
    for (path, function), rules in sorted(baseline.items()):
        records.append(
            json.dumps(
                {
                    "path": path,
                    "function": function,
                    "rules": dict(sorted(rules.items())),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    return "".join(record + "\n" for record in records)


def compare_baselines(base: Baseline, current: Baseline) -> list[str]:
    """Return ratchet violations; unchanged and decreasing budgets are valid."""
    errors: list[str] = []
    for key, current_rules in sorted(current.items()):
        if key not in base:
            errors.append(f"new function {key[0]}:{key[1]}")
            continue
        base_rules = base[key]
        for rule_id, current_count in sorted(current_rules.items()):
            if rule_id not in base_rules:
                errors.append(f"new rule {key[0]}:{key[1]}:{rule_id}")
            elif current_count > base_rules[rule_id]:
                errors.append(
                    f"increased {key[0]}:{key[1]}:{rule_id} "
                    f"{base_rules[rule_id]} -> {current_count}"
                )
    return errors


def _baseline_mismatch_errors(recorded: Baseline, scanned: Baseline) -> list[str]:
    errors: list[str] = []
    all_keys = sorted(set(recorded) | set(scanned))
    for key in all_keys:
        if key not in recorded:
            errors.append(f"baseline missing function {key[0]}:{key[1]}")
        elif key not in scanned:
            errors.append(f"baseline retains removed function {key[0]}:{key[1]}")
        elif recorded[key] != scanned[key]:
            errors.append(
                f"baseline count mismatch {key[0]}:{key[1]} "
                f"recorded={recorded[key]} scanned={scanned[key]}"
            )
    return errors


def _git_show(repo_root: Path, base_sha: str, relative_path: Path) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{base_sha}:{relative_path.as_posix()}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


GUARD_PATH = Path("scripts/check-thin-adapter-ratchet.py")


def _base_scanned_baseline(repo_root: Path, base_sha: str) -> Baseline | None:
    """Measure the base commit's source with the rules this run uses."""
    source = _git_show(repo_root, base_sha, SOURCE_PATH)
    if source is None:
        return None
    violations = list(scan_source(source, path=SOURCE_PATH.as_posix()))
    listed = subprocess.run(
        [
            "git",
            "ls-tree",
            "-r",
            "-z",
            "--name-only",
            base_sha,
            "skills/mission/lib/mission_adapter",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    # NUL-separated, so a path containing a space stays one path.
    names = (name for name in listed.stdout.split("\0") if name)
    for name in sorted(names) if listed.returncode == 0 else ():
        if not name.endswith(".py"):
            continue
        module_source = _git_show(repo_root, base_sha, Path(name))
        if module_source is None:
            continue
        violations.extend(
            scan_source(
                module_source,
                path=name,
                extra_roots=_top_level_functions(ast.parse(module_source, filename=name)),
            )
        )
    return baseline_from_violations(violations)


def load_base_baseline(repo_root: Path, base_sha: str) -> tuple[Baseline, str]:
    """Load an established baseline, or bootstrap the first PR from base source.

    Both sides have to be measured with the same ruler.  When this guard's own
    rules change -- a wider notion of which functions are reachable, say -- the
    baseline recorded at the base was produced by the older rules, and every
    function the new rules newly see would read as a violation someone just
    added.  So when the guard differs from the base's copy of it, the base is
    measured again here rather than read from its file.
    """
    if re.fullmatch(r"[0-9a-fA-F]{7,64}", base_sha) is None:
        raise BaselineError("base SHA must be a hexadecimal git object id")
    base_guard = _git_show(repo_root, base_sha, GUARD_PATH)
    try:
        current_guard = (repo_root / GUARD_PATH).read_text(encoding="utf-8")
    except OSError:
        # A tree without this file cannot have changed the rules; the
        # comparison falls back to whatever the base recorded.
        current_guard = base_guard
    if base_guard is not None and base_guard != current_guard:
        rescanned = _base_scanned_baseline(repo_root, base_sha)
        if rescanned is not None:
            return rescanned, "rescanned-base-source"
    baseline_text = _git_show(repo_root, base_sha, BASELINE_PATH)
    if baseline_text is not None:
        return load_baseline_text(baseline_text), "recorded-baseline"
    rescanned = _base_scanned_baseline(repo_root, base_sha)
    if rescanned is None:
        raise BaselineError("base contains neither the ratchet baseline nor canonical source")
    return rescanned, "bootstrap-source-scan"


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    try:
        scanned = scan_repository(repo_root)
        recorded = load_baseline_text(
            (repo_root / BASELINE_PATH).read_text(encoding="utf-8")
        )
        errors = _baseline_mismatch_errors(recorded, scanned)
        base_sha = os.environ.get(BASE_SHA_ENV, "").strip()
        base_source = "current-only"
        if base_sha:
            base, base_source = load_base_baseline(repo_root, base_sha)
            errors.extend(compare_baselines(base, recorded))
    except (BaselineError, OSError, SyntaxError) as exc:
        print(f"thin-adapter guard failed: {exc}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"thin-adapter guard failed: {error}", file=sys.stderr)
        return 1
    print(
        "thin-adapter guard passed: "
        f"functions={len(recorded)} base={base_source}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
