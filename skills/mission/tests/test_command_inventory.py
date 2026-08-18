"""A1 lifecycle command ownership and adapter-shape contracts."""

from __future__ import annotations

import ast
import argparse
import importlib.util
from pathlib import Path
import sys

import pytest


A1_COMMANDS = {
    "activity-end",
    "activity-start",
    "advance",
    "cleanup-stale",
    "halt",
    "init",
    "mark-halt",
    "reactivate",
    "refresh-pid",
    "resume",
    "set",
    "update-project-root",
}

FORBIDDEN_LEGACY_CALLS = {
    "StateLock",
    "_add_to_aggregate",
    "_transition_phase",
    "_write_terminal_outcome",
    "atomic_write_json",
    "backup_state",
    "json.loads",
}

COMMAND_APPLICATION_ROUTES = {
    "cmd_activity_end": "run_activity_end",
    "cmd_activity_start": "run_activity_start",
    "cmd_advance": "run_advance",
    "cmd_cleanup_stale": "run_mark_halt",
    "cmd_halt": "run_mark_halt",
    "cmd_init": "run_initialize",
    "cmd_mark_halt": "run_mark_halt",
    "cmd_reactivate": "run_reactivate",
    "cmd_refresh_pid": "run_refresh_pid",
    "cmd_resume": "run_refresh_pid",
    "cmd_set": "run_set_fields",
    "cmd_update_project_root": "run_update_project_root",
}

FORBIDDEN_SESSION_WRITER_CALLS = {"StateLock", "atomic_write_json"}
ALLOWED_NON_C2_CALL_SITES = {
    # C1-owned initialization writes and locks.  These exemptions are scoped
    # to cmd_init so another entry cannot inherit them by calling the helper.
    ("cmd_init", "_guarded_init_state_lock", "StateLock"),
    ("cmd_init", "_initialize_legacy_v4", "atomic_write_json"),
    ("cmd_init", "_initialize_new_v5_session", "StateLock"),
    # These helpers never write session state: the first only maintains the
    # rebuildable aggregate index, and the second only locks review-lineage
    # serialization.  Their C2 exemption is therefore safe even when the C2
    # entry reaches them; each permitted entry is still listed explicitly.
    ("cmd_init", "_remove_from_aggregate", "atomic_write_json"),
    ("cmd_permission_preflight", "_remove_from_aggregate", "atomic_write_json"),
    ("cmd_reactivate", "_review_lineage_transaction", "StateLock"),
    ("cmd_refresh_pid", "_review_lineage_transaction", "StateLock"),
    ("cmd_set", "_review_lineage_transaction", "StateLock"),
    ("cmd_supersede_reviews", "_review_lineage_transaction", "StateLock"),
}


def _load_mission_state_module():
    source = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("mission_state_command_inventory", source)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _leaf_parser_commands(parser, prefix=()):
    subparsers = [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    if not subparsers:
        return {" ".join(prefix)}
    commands = set()
    for action in subparsers:
        for name, child in action.choices.items():
            commands.update(_leaf_parser_commands(child, prefix + (name,)))
    return commands


def forbidden_calls_in_reachable(entry_names, *, tree=None):
    """Find statically reachable forbidden calls, including local callables.

    All nested functions, closures, and methods are indexed.  Attribute calls
    are resolved conservatively by their final method name, so a local class
    method is followed without attempting type inference.  ``getattr`` and
    other dynamic dispatch remain outside what a static AST guard can prove.
    """

    if tree is None:
        source = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
    module_functions = {}
    module_classes = {}
    module_aliases = {}
    local_functions = {}
    local_classes = {}
    local_aliases = {}
    local_attribute_aliases = {}
    local_lambdas = {}
    parent_functions = {}
    methods_by_name = {}
    class_initializers = {}
    classes_by_name = {}
    qualified_names = {}

    class DefinitionCollector(ast.NodeVisitor):
        def __init__(self):
            self.function_stack = []
            self.scope_stack = []

        def _visit_function(self, node):
            parent = self.function_stack[-1] if self.function_stack else None
            parent_functions[id(node)] = parent
            if self.scope_stack and self.scope_stack[-1][0] == "class":
                qualified_name = self.scope_stack[-1][1] + "." + node.name
                methods_by_name.setdefault(node.name, []).append(node)
                if node.name in {"__new__", "__init__"}:
                    class_initializers.setdefault(
                        self.scope_stack[-1][1], []
                    ).append(node)
            elif parent is None:
                qualified_name = node.name
                module_functions[node.name] = node
            else:
                qualified_name = (
                    qualified_names[id(parent)] + ".<locals>." + node.name
                )
                local_functions.setdefault(id(parent), {}).setdefault(
                    node.name, []
                ).append(node)
            qualified_names[id(node)] = qualified_name
            self.function_stack.append(node)
            self.scope_stack.append(("function", qualified_name))
            for statement in node.body:
                self.visit(statement)
            self.scope_stack.pop()
            self.function_stack.pop()

        def visit_FunctionDef(self, node):
            self._visit_function(node)

        def visit_AsyncFunctionDef(self, node):
            self._visit_function(node)

        def visit_ClassDef(self, node):
            parent = self.function_stack[-1] if self.function_stack else None
            if not self.scope_stack:
                qualified_name = node.name
                module_classes[node.name] = qualified_name
            elif self.scope_stack[-1][0] == "function":
                qualified_name = (
                    self.scope_stack[-1][1] + ".<locals>." + node.name
                )
                local_classes.setdefault(id(parent), {}).setdefault(
                    node.name, []
                ).append(qualified_name)
            else:
                qualified_name = self.scope_stack[-1][1] + "." + node.name
            classes_by_name.setdefault(node.name, []).append(qualified_name)
            self.scope_stack.append(("class", qualified_name))
            for statement in node.body:
                self.visit(statement)
            self.scope_stack.pop()

        def visit_Assign(self, node):
            if self.function_stack:
                owner = id(self.function_stack[-1])
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        if isinstance(node.value, ast.Lambda):
                            local_lambdas.setdefault(owner, {}).setdefault(
                                target.id, []
                            ).append(node.value)
                        elif isinstance(node.value, ast.Name):
                            local_aliases.setdefault(owner, {}).setdefault(
                                target.id, []
                            ).append(node.value.id)
                        elif isinstance(node.value, ast.Attribute):
                            local_attribute_aliases.setdefault(
                                owner, {}
                            ).setdefault(target.id, []).append(node.value.attr)
            elif not self.scope_stack and isinstance(node.value, ast.Name):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        module_aliases.setdefault(target.id, []).append(
                            node.value.id
                        )
            self.generic_visit(node)

        def visit_AnnAssign(self, node):
            if self.function_stack and isinstance(node.target, ast.Name):
                owner = id(self.function_stack[-1])
                if isinstance(node.value, ast.Lambda):
                    local_lambdas.setdefault(owner, {}).setdefault(
                        node.target.id, []
                    ).append(node.value)
                elif isinstance(node.value, ast.Name):
                    local_aliases.setdefault(owner, {}).setdefault(
                        node.target.id, []
                    ).append(node.value.id)
                elif isinstance(node.value, ast.Attribute):
                    local_attribute_aliases.setdefault(owner, {}).setdefault(
                        node.target.id, []
                    ).append(node.value.attr)
            self.generic_visit(node)

        def visit_NamedExpr(self, node):
            if self.function_stack and isinstance(node.target, ast.Name):
                owner = id(self.function_stack[-1])
                if isinstance(node.value, ast.Lambda):
                    local_lambdas.setdefault(owner, {}).setdefault(
                        node.target.id, []
                    ).append(node.value)
                elif isinstance(node.value, ast.Name):
                    local_aliases.setdefault(owner, {}).setdefault(
                        node.target.id, []
                    ).append(node.value.id)
                elif isinstance(node.value, ast.Attribute):
                    local_attribute_aliases.setdefault(owner, {}).setdefault(
                        node.target.id, []
                    ).append(node.value.attr)
            self.generic_visit(node)

        def visit_Lambda(self, node):
            if self.function_stack:
                parent_functions.setdefault(
                    id(node), self.function_stack[-1]
                )
            self.generic_visit(node)

    DefinitionCollector().visit(tree)

    class DirectCallVisitor(ast.NodeVisitor):
        def __init__(self):
            self.calls = []

        def visit_Call(self, node):
            self.calls.append(node)
            self.generic_visit(node)

        def visit_FunctionDef(self, node):
            self._visit_definition_expressions(node)

        def visit_AsyncFunctionDef(self, node):
            self._visit_definition_expressions(node)

        def visit_ClassDef(self, node):
            for decorator in node.decorator_list:
                self.visit(decorator)
            for base in node.bases:
                self.visit(base)
            for keyword in node.keywords:
                self.visit(keyword.value)
            for statement in node.body:
                self.visit(statement)

        def visit_Lambda(self, node):
            for default in node.args.defaults:
                self.visit(default)
            for default in node.args.kw_defaults:
                if default is not None:
                    self.visit(default)

        def _visit_definition_expressions(self, node):
            for decorator in node.decorator_list:
                self.visit(decorator)
            for default in node.args.defaults:
                self.visit(default)
            for default in node.args.kw_defaults:
                if default is not None:
                    self.visit(default)

    def resolve_attribute_callable(called_name):
        return [
            *methods_by_name.get(called_name, ()),
            *(
                [module_functions[called_name]]
                if called_name in module_functions
                else []
            ),
            *(
                initializer
                for class_name in classes_by_name.get(called_name, ())
                for initializer in class_initializers.get(class_name, ())
            ),
        ]

    def resolve_named_callable(function, called_name, resolving=frozenset()):
        current = function
        while current is not None:
            owner = id(current)
            matches = [
                *local_functions.get(owner, {}).get(called_name, ()),
                *local_lambdas.get(owner, {}).get(called_name, ()),
            ]
            class_names = local_classes.get(id(current), {}).get(called_name)
            if class_names:
                matches.extend(
                    initializer
                    for class_name in class_names
                    for initializer in class_initializers.get(class_name, ())
                )
            aliases = local_aliases.get(owner, {}).get(called_name, ())
            attribute_aliases = local_attribute_aliases.get(owner, {}).get(
                called_name, ()
            )
            alias_key = (owner, called_name)
            if aliases and alias_key not in resolving:
                for alias in aliases:
                    matches.extend(
                        resolve_named_callable(
                            current,
                            alias,
                            resolving | {alias_key},
                        )
                    )
            for attribute_alias in attribute_aliases:
                matches.extend(resolve_attribute_callable(attribute_alias))
            if matches or aliases or attribute_aliases:
                return matches
            current = parent_functions.get(id(current))
        module_function = module_functions.get(called_name)
        if module_function is not None:
            return [module_function]
        matches = list(
            class_initializers.get(module_classes.get(called_name, ""), ())
        )
        alias_key = (None, called_name)
        if alias_key not in resolving:
            for alias in module_aliases.get(called_name, ()):
                matches.extend(
                    resolve_named_callable(
                        function,
                        alias,
                        resolving | {alias_key},
                    )
                )
        return matches

    def resolve_forbidden_aliases(function, called_name, resolving=frozenset()):
        if called_name in FORBIDDEN_SESSION_WRITER_CALLS:
            return {called_name}
        current = function
        while current is not None:
            owner = id(current)
            aliases = local_aliases.get(owner, {}).get(called_name, ())
            attribute_aliases = local_attribute_aliases.get(owner, {}).get(
                called_name, ()
            )
            alias_key = (owner, called_name)
            if aliases or attribute_aliases:
                forbidden = {
                    attribute_alias
                    for attribute_alias in attribute_aliases
                    if attribute_alias in FORBIDDEN_SESSION_WRITER_CALLS
                }
                if alias_key not in resolving:
                    for alias in aliases:
                        forbidden.update(
                            resolve_forbidden_aliases(
                                current,
                                alias,
                                resolving | {alias_key},
                            )
                        )
                return forbidden
            current = parent_functions.get(id(current))
        alias_key = (None, called_name)
        if alias_key in resolving:
            return set()
        forbidden = set()
        for alias in module_aliases.get(called_name, ()):
            forbidden.update(
                resolve_forbidden_aliases(
                    function,
                    alias,
                    resolving | {alias_key},
                )
            )
        return forbidden

    def returned_callables(function, resolving_returns=frozenset()):
        if id(function) in resolving_returns:
            return [], set()
        if isinstance(function, ast.Lambda):
            return resolve_callable_expression(
                function,
                function.body,
                resolving_returns | {id(function)},
            )

        class ReturnVisitor(ast.NodeVisitor):
            def __init__(self):
                self.values = []

            def visit_Return(self, node):
                if node.value is not None:
                    self.values.append(node.value)

            def visit_FunctionDef(self, node):
                return

            def visit_AsyncFunctionDef(self, node):
                return

            def visit_ClassDef(self, node):
                return

            def visit_Lambda(self, node):
                return

        visitor = ReturnVisitor()
        for statement in function.body:
            visitor.visit(statement)
        callables = []
        forbidden = set()
        for value in visitor.values:
            found, blocked = resolve_callable_expression(
                function,
                value,
                resolving_returns | {id(function)},
            )
            callables.extend(found)
            forbidden.update(blocked)
        return callables, forbidden

    def resolve_callable_expression(function, expression, resolving_returns=frozenset()):
        if isinstance(expression, ast.Name):
            return (
                resolve_named_callable(function, expression.id),
                resolve_forbidden_aliases(function, expression.id),
            )
        if isinstance(expression, ast.Attribute):
            forbidden = (
                {expression.attr}
                if expression.attr in FORBIDDEN_SESSION_WRITER_CALLS
                else set()
            )
            return resolve_attribute_callable(expression.attr), forbidden
        if isinstance(expression, ast.Lambda):
            return [expression], set()
        if isinstance(expression, ast.NamedExpr):
            return resolve_callable_expression(
                function, expression.value, resolving_returns
            )
        if isinstance(expression, ast.Call):
            factories, forbidden = resolve_callable_expression(
                function, expression.func, resolving_returns
            )
            returned = []
            for factory in factories:
                found, blocked = returned_callables(factory, resolving_returns)
                returned.extend(found)
                forbidden.update(blocked)
            return returned, forbidden
        return [], set()

    called_parameter_cache = {}

    def directly_called_parameters(function):
        cached = called_parameter_cache.get(id(function))
        if cached is not None:
            return cached
        parameters = {
            argument.arg
            for argument in (
                *function.args.posonlyargs,
                *function.args.args,
                *function.args.kwonlyargs,
            )
        }
        visitor = DirectCallVisitor()
        if isinstance(function, ast.Lambda):
            visitor.visit(function.body)
        else:
            for statement in function.body:
                visitor.visit(statement)
        called = {
            call.func.id
            for call in visitor.calls
            if isinstance(call.func, ast.Name) and call.func.id in parameters
        }
        called_parameter_cache[id(function)] = called
        return called

    def forbidden_callback_arguments(caller, call, callee):
        called_parameters = directly_called_parameters(callee)
        if not called_parameters:
            return set()
        positional = [*callee.args.posonlyargs, *callee.args.args]
        if (
            isinstance(call.func, ast.Attribute)
            and positional
            and positional[0].arg in {"self", "cls"}
        ):
            positional = positional[1:]
        forbidden = set()
        for parameter, argument in zip(positional, call.args):
            if parameter.arg in called_parameters:
                _targets, blocked = resolve_callable_expression(caller, argument)
                forbidden.update(blocked)
        keyword_parameters = {
            argument.arg: argument
            for argument in (*callee.args.args, *callee.args.kwonlyargs)
        }
        for keyword in call.keywords:
            if (
                keyword.arg in keyword_parameters
                and keyword.arg in called_parameters
            ):
                _targets, blocked = resolve_callable_expression(
                    caller, keyword.value
                )
                forbidden.update(blocked)
        return forbidden

    violations = set()
    for entry_name in sorted(entry_names):
        entry = module_functions.get(entry_name)
        pending = [entry] if entry is not None else []
        visited = set()
        while pending:
            function = pending.pop()
            function_identity = id(function)
            if function_identity in visited:
                continue
            visited.add(function_identity)
            visitor = DirectCallVisitor()
            if isinstance(function, ast.Lambda):
                visitor.visit(function.body)
                function_name = "<lambda>"
            else:
                for statement in function.body:
                    visitor.visit(statement)
                function_name = qualified_names[id(function)]
            for call in visitor.calls:
                called_functions, forbidden_names = resolve_callable_expression(
                    function, call.func
                )
                for called_function in called_functions:
                    forbidden_names.update(
                        forbidden_callback_arguments(
                            function,
                            call,
                            called_function,
                        )
                    )
                for forbidden_name in forbidden_names:
                    call_site = (entry_name, function_name, forbidden_name)
                    if call_site not in ALLOWED_NON_C2_CALL_SITES:
                        violations.add((entry_name, forbidden_name, call.lineno))
                pending.extend(called_functions)
    return sorted(violations)


def test_forbidden_call_inventory_follows_module_level_helpers():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    def _hidden_writer():
        atomic_write_json()

    _supersede_reviews_locked()
    _hidden_writer()

def _supersede_reviews_locked():
    atomic_write_json()
"""
    )

    assert forbidden_calls_in_reachable(
        {"cmd_supersede_reviews"}, tree=tree
    ) == [
        ("cmd_supersede_reviews", "atomic_write_json", 4),
        ("cmd_supersede_reviews", "atomic_write_json", 10),
    ]


def test_forbidden_call_inventory_follows_attribute_methods():
    tree = ast.parse(
        """
class LocalWriter:
    def persist(self):
        storage.atomic_write_json()

def cmd_supersede_reviews():
    writer = LocalWriter()
    writer.persist()
"""
    )

    assert forbidden_calls_in_reachable(
        {"cmd_supersede_reviews"}, tree=tree
    ) == [("cmd_supersede_reviews", "atomic_write_json", 4)]


def test_forbidden_call_inventory_follows_called_lambda_closures():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    hidden_writer = lambda: atomic_write_json()
    hidden_writer()
"""
    )

    assert forbidden_calls_in_reachable(
        {"cmd_supersede_reviews"}, tree=tree
    ) == [("cmd_supersede_reviews", "atomic_write_json", 3)]


def test_forbidden_call_inventory_allows_non_session_lock_for_declared_entry():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    _review_lineage_transaction()

def _review_lineage_transaction():
    locks.StateLock()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == []


def test_forbidden_call_inventory_does_not_share_allowlist_between_entries():
    tree = ast.parse(
        """
def cmd_unrelated():
    _review_lineage_transaction()

def _review_lineage_transaction():
    StateLock()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_unrelated"}, tree=tree) == [
        ("cmd_unrelated", "StateLock", 6)
    ]


def test_forbidden_call_inventory_does_not_allow_same_named_nested_helper():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    def _review_lineage_transaction():
        StateLock()
    _review_lineage_transaction()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "StateLock", 4)
    ]


def test_forbidden_call_inventory_does_not_allow_same_named_class_method():
    tree = ast.parse(
        """
class LocalLock:
    def _review_lineage_transaction(self):
        StateLock()

def cmd_supersede_reviews():
    LocalLock()._review_lineage_transaction()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "StateLock", 4)
    ]


def test_forbidden_call_inventory_follows_class_constructor():
    tree = ast.parse(
        """
class LocalWriter:
    def __init__(self):
        atomic_write_json()

def cmd_supersede_reviews():
    LocalWriter()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 4)
    ]


def test_forbidden_call_inventory_checks_nested_definition_expressions():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    @atomic_write_json()
    def hidden(value=atomic_write_json()):
        pass
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 3),
        ("cmd_supersede_reviews", "atomic_write_json", 4),
    ]


def test_forbidden_call_inventory_checks_reachable_class_body():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    class HiddenWriter:
        atomic_write_json()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 4)
    ]


def test_forbidden_call_inventory_follows_simple_closure_alias():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    def hidden_writer():
        atomic_write_json()
    alias = hidden_writer
    alias()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 4)
    ]


def test_forbidden_call_inventory_follows_named_expression_lambda():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    (hidden_writer := lambda: atomic_write_json())()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 3)
    ]


def test_forbidden_call_inventory_follows_alias_of_forbidden_callable():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    writer = atomic_write_json
    alias = writer
    alias({})
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 5)
    ]


def test_forbidden_call_inventory_checks_lambda_defaults():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    hidden = lambda value=atomic_write_json(): None
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 3)
    ]


def test_forbidden_call_inventory_follows_module_class_alias_constructor():
    tree = ast.parse(
        """
class LocalWriter:
    def __init__(self):
        atomic_write_json()

WriterAlias = LocalWriter

def cmd_supersede_reviews():
    WriterAlias()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 4)
    ]


def test_forbidden_call_inventory_follows_nested_class_constructor():
    tree = ast.parse(
        """
class Namespace:
    class LocalWriter:
        def __init__(self):
            atomic_write_json()

def cmd_supersede_reviews():
    Namespace.LocalWriter()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 5)
    ]


def test_forbidden_call_inventory_follows_bound_method_alias():
    tree = ast.parse(
        """
class LocalWriter:
    def persist(self):
        atomic_write_json()

def cmd_supersede_reviews():
    writer = LocalWriter()
    alias = writer.persist
    alias()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 4)
    ]


def test_forbidden_call_inventory_follows_factory_returned_closure():
    tree = ast.parse(
        """
def factory():
    def hidden_writer():
        atomic_write_json()
    return hidden_writer

def cmd_supersede_reviews():
    factory()()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 4)
    ]


def test_forbidden_call_inventory_follows_lambda_factory_returned_closure():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    factory = lambda: (lambda: atomic_write_json())
    factory()()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 3)
    ]


def test_forbidden_call_inventory_resolves_sibling_closure_from_lambda():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    def hidden_writer():
        atomic_write_json()
    callback = lambda: hidden_writer()
    callback()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 4)
    ]


def test_forbidden_call_inventory_follows_lambda_returned_named_closure():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    def hidden_writer():
        atomic_write_json()
    factory = lambda: hidden_writer
    factory()()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 4)
    ]


def test_forbidden_call_inventory_follows_called_callback_argument():
    tree = ast.parse(
        """
def invoke(callback):
    callback()

def cmd_supersede_reviews():
    invoke(atomic_write_json)
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 6)
    ]


def test_all_parser_commands_have_exactly_one_declared_owner():
    from mission_application.command_owners import COMMAND_OWNER_REGISTRY

    parser_commands = _leaf_parser_commands(_load_mission_state_module()._build_parser())

    assert set(COMMAND_OWNER_REGISTRY) == parser_commands
    assert all(isinstance(owner, str) and owner for owner in COMMAND_OWNER_REGISTRY.values())


def test_c2_stage_a_and_direct_write_allowlist_are_closed_and_disjoint():
    from mission_application.command_owners import (
        C2_DIRECT_WRITE_ALLOWLIST,
        C2_REPOSITORY_COMMANDS,
        COMMAND_OWNER_REGISTRY,
    )

    assert C2_REPOSITORY_COMMANDS == frozenset(
        {"planning reselect", "supersede-reviews"}
    )
    assert C2_DIRECT_WRITE_ALLOWLIST == frozenset(
        {
            "executor-handoff begin",
            "executor-handoff complete",
            "executor-handoff record-step",
            "executor-handoff verify-step",
            "manual-score-capture",
            "planning adopt-core",
            "planning promote-provider-plan",
            "specialists invoke-command",
            "specialists invoke-prepared",
            "specialists log-invocation",
            "specialists plan-import",
            "specialists prepare-invocation",
            "specialists recommend",
            "specialists reconcile-invocation",
            "specialists verify-approval",
        }
    )
    assert C2_REPOSITORY_COMMANDS.isdisjoint(C2_DIRECT_WRITE_ALLOWLIST)
    assert C2_REPOSITORY_COMMANDS | C2_DIRECT_WRITE_ALLOWLIST <= set(
        COMMAND_OWNER_REGISTRY
    )


def test_c2_repository_commands_have_no_direct_legacy_session_writer_calls():
    target_names = {"cmd_planning_reselect", "cmd_supersede_reviews"}

    assert forbidden_calls_in_reachable(target_names) == []


def test_direct_legacy_call_inventory_has_no_silent_parser_adapter_gap():
    from mission_application.command_owners import (
        C2_DIRECT_WRITE_FUNCTIONS,
        NON_SESSION_DIRECT_CALL_FUNCTIONS,
    )

    source = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    entry_names = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and (node.name.startswith("cmd_") or node.name.startswith("_cmd_"))
    }
    discovered = {
        entry_name
        for entry_name, _forbidden_name, _line in forbidden_calls_in_reachable(
            entry_names, tree=tree
        )
    }

    assert discovered == C2_DIRECT_WRITE_FUNCTIONS | NON_SESSION_DIRECT_CALL_FUNCTIONS


def test_a1_registry_has_one_owner_for_every_lifecycle_command():
    from mission_application.lifecycle import LIFECYCLE_COMMAND_OWNERS

    assert set(LIFECYCLE_COMMAND_OWNERS) == A1_COMMANDS
    assert all(owner == "A1.lifecycle" for owner in LIFECYCLE_COMMAND_OWNERS.values())


def test_advance_command_does_not_read_state_outside_repository():
    source = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"cmd_activity_start", "cmd_activity_end", "cmd_advance"}
    }

    advance_calls = {
        node.func.attr
        for node in ast.walk(functions["cmd_advance"])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "read_text"
    }
    assert advance_calls == set()


def test_a1_command_ast_has_no_direct_legacy_mutation_calls():
    source = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    target_names = {
        "cmd_activity_end",
        "cmd_activity_start",
        "cmd_advance",
        "cmd_cleanup_stale",
        "cmd_halt",
        "cmd_init",
        "cmd_mark_halt",
        "cmd_reactivate",
        "cmd_refresh_pid",
        "cmd_resume",
        "cmd_set",
        "cmd_update_project_root",
    }
    violations = []
    for function in (
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in target_names
    ):
        for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
            called = None
            if isinstance(call.func, ast.Name):
                called = call.func.id
            elif isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
                called = f"{call.func.value.id}.{call.func.attr}"
            if called in FORBIDDEN_LEGACY_CALLS:
                violations.append((function.name, called, call.lineno))
    assert violations == []


def _called_function_names(function: ast.FunctionDef) -> set[str]:
    called = set()
    for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
        if isinstance(call.func, ast.Name):
            called.add(call.func.id)
            if (
                call.func.id == "_capture_command_output"
                and call.args
                and isinstance(call.args[0], ast.Name)
            ):
                called.add(call.args[0].id)
    return called


@pytest.mark.parametrize(
    ("command_name", "application_name"),
    COMMAND_APPLICATION_ROUTES.items(),
)
def test_a1_commands_route_through_application_use_cases(
    command_name, application_name
):
    source = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    pending = [command_name]
    visited = set()
    called_names = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        direct_calls = _called_function_names(functions[current])
        called_names.update(direct_calls)
        pending.extend(
            name
            for name in direct_calls
            if name in functions and name not in visited
        )

    assert application_name in called_names


def test_lifecycle_module_has_no_process_or_stdout_io_dependency():
    source = Path(__file__).resolve().parents[1] / "lib" / "mission_application" / "lifecycle.py"
    text = source.read_text(encoding="utf-8")
    tree = ast.parse(text)
    imported_roots = {
        alias.name.partition(".")[0]
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        node.module.partition(".")[0]
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    )

    assert imported_roots.isdisjoint({"os", "subprocess"})
    assert "sys.stdout" not in text


def test_parser_routes_each_a1_command_to_its_single_registered_adapter():
    source = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    parser_names = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "add_parser"
            and node.value.args
            and isinstance(node.value.args[0], ast.Constant)
            and isinstance(node.value.args[0].value, str)
        ):
            variable = node.targets[0].id
            name = node.value.args[0].value
            if variable in {"p_activity_start", "p_activity_end"}:
                name = "activity-" + name
            parser_names[variable] = name

    routes = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "set_defaults"
            and isinstance(node.func.value, ast.Name)
        ):
            function = next(
                (
                    keyword.value.id
                    for keyword in node.keywords
                    if keyword.arg == "func" and isinstance(keyword.value, ast.Name)
                ),
                None,
            )
            parser_name = parser_names.get(node.func.value.id)
            if function and function in {
                "cmd_activity_end",
                "cmd_activity_start",
                "cmd_advance",
                "cmd_cleanup_stale",
                "cmd_halt",
                "cmd_init",
                "cmd_mark_halt",
                "cmd_reactivate",
                "cmd_refresh_pid",
                "cmd_resume",
                "cmd_set",
                "cmd_update_project_root",
            }:
                routes[parser_name] = function

    assert routes == {
        "activity-end": "cmd_activity_end",
        "activity-start": "cmd_activity_start",
        "advance": "cmd_advance",
        "cleanup-stale": "cmd_cleanup_stale",
        "halt": "cmd_halt",
        "init": "cmd_init",
        "mark-halt": "cmd_mark_halt",
        "reactivate": "cmd_reactivate",
        "refresh-pid": "cmd_refresh_pid",
        "resume": "cmd_resume",
        "set": "cmd_set",
        "update-project-root": "cmd_update_project_root",
    }
