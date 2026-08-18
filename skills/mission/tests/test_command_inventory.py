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


def forbidden_calls_in_reachable(
    entry_names,
    *,
    tree=None,
    allowed_call_sites=ALLOWED_NON_C2_CALL_SITES,
    include_call_sites=False,
):
    """Find statically reachable forbidden calls, including local callables.

    All nested functions, closures, and methods are indexed.  Attribute calls
    are resolved conservatively by their final method name, so a local class
    method is followed without attempting type inference.  The guard is
    fail-closed when a forbidden callable is lexically reachable through a
    container subscript, a Call-wrapped alias, or a comprehension binding.

    Static AST analysis cannot prove targets selected by ``getattr`` or other
    runtime dispatch, code produced by ``eval``/``exec``, the implementation
    of an imported callable whose body is outside the analyzed tree, or
    class-body attribute assignments resolved through attribute calls (for
    example, ``class C: fn = atomic_write_json; C.fn()``), because class
    namespaces are not tracked.  Writes hidden exclusively behind those
    boundaries are not detected.
    """

    if tree is None:
        source = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
    module_functions = {}
    module_classes = {}
    module_aliases = {}
    module_call_values = {}
    module_container_values = {}
    module_expression_values = {}
    local_functions = {}
    local_classes = {}
    local_aliases = {}
    local_attribute_aliases = {}
    local_call_values = {}
    local_container_values = {}
    local_expression_values = {}
    local_lambdas = {}
    local_parameters = {}
    parent_functions = {}
    methods_by_name = {}
    class_initializers = {}
    classes_by_name = {}
    function_decorators = {}
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
            function_decorators[id(node)] = list(node.decorator_list)
            local_parameters[id(node)] = {
                argument.arg
                for argument in (
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                )
            }
            if node.args.vararg is not None:
                local_parameters[id(node)].add(node.args.vararg.arg)
            if node.args.kwarg is not None:
                local_parameters[id(node)].add(node.args.kwarg.arg)
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

        def _binding_pairs(self, target, value):
            if isinstance(target, ast.Name):
                return [(target.id, value)]
            if isinstance(target, ast.Starred):
                return self._binding_pairs(target.value, value)
            if isinstance(target, (ast.List, ast.Tuple)):
                if (
                    isinstance(value, (ast.List, ast.Tuple))
                    and len(target.elts) == len(value.elts)
                ):
                    return [
                        pair
                        for child_target, child_value in zip(
                            target.elts, value.elts
                        )
                        for pair in self._binding_pairs(
                            child_target, child_value
                        )
                    ]
                return [
                    pair
                    for child_target in target.elts
                    for pair in self._binding_pairs(child_target, value)
                ]
            return []

        def _record_local_binding(self, owner, name, value):
            if isinstance(value, ast.Lambda):
                local_lambdas.setdefault(owner, {}).setdefault(
                    name, []
                ).append(value)
            elif isinstance(value, ast.Name):
                local_aliases.setdefault(owner, {}).setdefault(name, []).append(
                    value.id
                )
            elif isinstance(value, ast.Attribute):
                local_attribute_aliases.setdefault(owner, {}).setdefault(
                    name, []
                ).append(value.attr)
            elif isinstance(value, (ast.Dict, ast.List, ast.Set, ast.Tuple)):
                local_container_values.setdefault(owner, {}).setdefault(
                    name, []
                ).append(value)
            elif isinstance(value, ast.Call):
                local_call_values.setdefault(owner, {}).setdefault(
                    name, []
                ).append(value)
            else:
                local_expression_values.setdefault(owner, {}).setdefault(
                    name, []
                ).append(value)

        def _record_module_binding(self, name, value):
            if isinstance(value, ast.Name):
                module_aliases.setdefault(name, []).append(value.id)
            elif isinstance(value, (ast.Dict, ast.List, ast.Set, ast.Tuple)):
                module_container_values.setdefault(name, []).append(value)
            elif isinstance(value, ast.Call):
                module_call_values.setdefault(name, []).append(value)
            else:
                module_expression_values.setdefault(name, []).append(value)

        def _record_bindings(self, target, value):
            if value is None:
                return
            for name, bound_value in self._binding_pairs(target, value):
                self._record_binding(name, bound_value)

        def _record_binding(self, name, bound_value):
            if self.function_stack:
                self._record_local_binding(
                    id(self.function_stack[-1]), name, bound_value
                )
            elif not self.scope_stack:
                self._record_module_binding(name, bound_value)

        def _record_iter_bindings(self, target, iterable):
            pairs = None
            if (
                isinstance(target, (ast.List, ast.Tuple))
                and isinstance(iterable, (ast.List, ast.Set, ast.Tuple))
                and all(
                    isinstance(row, (ast.List, ast.Tuple))
                    and len(row.elts) == len(target.elts)
                    for row in iterable.elts
                )
            ):
                pairs = [
                    pair
                    for row in iterable.elts
                    for child_target, child_value in zip(
                        target.elts, row.elts
                    )
                    for pair in self._binding_pairs(
                        child_target, child_value
                    )
                ]
            if pairs is None:
                pairs = self._binding_pairs(target, iterable)
            for name, bound_value in pairs:
                self._record_binding(name, bound_value)

        def _root_name(self, expression):
            current = expression
            while isinstance(current, (ast.Attribute, ast.Subscript)):
                current = current.value
            return current.id if isinstance(current, ast.Name) else None

        def visit_Assign(self, node):
            for target in node.targets:
                self._record_bindings(target, node.value)
                if isinstance(target, (ast.Attribute, ast.Subscript)):
                    root_name = self._root_name(target)
                    if root_name is not None:
                        self._record_bindings(
                            ast.Name(id=root_name, ctx=ast.Store()), node.value
                        )
            self.generic_visit(node)

        def visit_AnnAssign(self, node):
            self._record_bindings(node.target, node.value)
            self.generic_visit(node)

        def visit_NamedExpr(self, node):
            self._record_bindings(node.target, node.value)
            self.generic_visit(node)

        def visit_AugAssign(self, node):
            root_name = self._root_name(node.target)
            if root_name is not None:
                self._record_bindings(
                    ast.Name(id=root_name, ctx=ast.Store()), node.value
                )
            self.generic_visit(node)

        def visit_For(self, node):
            self._record_iter_bindings(node.target, node.iter)
            self.generic_visit(node)

        def visit_AsyncFor(self, node):
            self._record_iter_bindings(node.target, node.iter)
            self.generic_visit(node)

        def visit_With(self, node):
            for item in node.items:
                if item.optional_vars is not None:
                    self._record_bindings(
                        item.optional_vars, item.context_expr
                    )
            self.generic_visit(node)

        def visit_AsyncWith(self, node):
            self.visit_With(node)

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
            self.comprehension_taint_sources = {}
            self._comprehension_bindings = []

        def visit_Call(self, node):
            self.calls.append(node)
            if (
                self._comprehension_bindings
                and isinstance(node.func, ast.Name)
                and node.func.id in self._comprehension_bindings[-1]
            ):
                self.comprehension_taint_sources.setdefault(id(node), []).extend(
                    self._comprehension_bindings[-1][node.func.id]
                )
            self.generic_visit(node)

        def visit_GeneratorExp(self, node):
            self._visit_comprehension(node.generators, [node.elt])

        def visit_ListComp(self, node):
            self._visit_comprehension(node.generators, [node.elt])

        def visit_SetComp(self, node):
            self._visit_comprehension(node.generators, [node.elt])

        def visit_DictComp(self, node):
            self._visit_comprehension(node.generators, [node.key, node.value])

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

        def _visit_comprehension(self, generators, result_expressions):
            bindings = (
                dict(self._comprehension_bindings[-1])
                if self._comprehension_bindings
                else {}
            )
            for generator in generators:
                self._comprehension_bindings.append(dict(bindings))
                self.visit(generator.iter)
                self._comprehension_bindings.pop()
                bindings.update(
                    self._target_bindings(
                        generator.target,
                        self._binding_sources(generator.iter, bindings),
                    )
                )
                self._comprehension_bindings.append(dict(bindings))
                for condition in generator.ifs:
                    self.visit(condition)
                self._comprehension_bindings.pop()
            self._comprehension_bindings.append(bindings)
            for expression in result_expressions:
                self.visit(expression)
            self._comprehension_bindings.pop()

        def _binding_sources(self, expression, bindings):
            if isinstance(expression, ast.Name) and expression.id in bindings:
                return bindings[expression.id]
            return [expression]

        def _target_bindings(self, target, sources):
            if isinstance(target, ast.Name):
                return {target.id: sources}
            if isinstance(target, (ast.List, ast.Tuple)):
                bindings = {}
                for index, element in enumerate(target.elts):
                    positional_sources = []
                    for source in sources:
                        if isinstance(source, (ast.List, ast.Set, ast.Tuple)):
                            rows = source.elts
                        else:
                            rows = [source]
                        for row in rows:
                            if (
                                isinstance(row, (ast.List, ast.Tuple))
                                and index < len(row.elts)
                            ):
                                positional_sources.append(row.elts[index])
                            else:
                                positional_sources.append(source)
                    bindings.update(
                        self._target_bindings(element, positional_sources)
                    )
                return bindings
            if isinstance(target, ast.Starred):
                return self._target_bindings(target.value, sources)
            return {}

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
            if called_name in local_parameters.get(owner, ()):
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

    def resolve_forbidden_aliases(
        function,
        called_name,
        resolving=frozenset(),
        *,
        follow_call_returns=True,
    ):
        if called_name in FORBIDDEN_SESSION_WRITER_CALLS:
            return {called_name}
        current = function
        while current is not None:
            owner = id(current)
            aliases = local_aliases.get(owner, {}).get(called_name, ())
            attribute_aliases = local_attribute_aliases.get(owner, {}).get(
                called_name, ()
            )
            container_values = local_container_values.get(owner, {}).get(
                called_name, ()
            )
            call_values = local_call_values.get(owner, {}).get(called_name, ())
            expression_values = local_expression_values.get(owner, {}).get(
                called_name, ()
            )
            alias_key = (owner, called_name)
            if (
                aliases
                or attribute_aliases
                or container_values
                or call_values
                or expression_values
            ):
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
                                follow_call_returns=follow_call_returns,
                            )
                        )
                    for container in container_values:
                        forbidden.update(
                            forbidden_callables_in_taint_source(
                                current,
                                container,
                                resolving | {alias_key},
                                follow_call_returns=follow_call_returns,
                            )
                        )
                    for call_value in call_values:
                        forbidden.update(
                            forbidden_callables_in_taint_source(
                                current,
                                call_value,
                                resolving | {alias_key},
                                follow_call_returns=follow_call_returns,
                            )
                        )
                    for expression_value in expression_values:
                        forbidden.update(
                            forbidden_callables_in_taint_source(
                                current,
                                expression_value,
                                resolving | {alias_key},
                                follow_call_returns=follow_call_returns,
                            )
                        )
                return forbidden
            if called_name in local_parameters.get(owner, ()):
                return set()
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
                    follow_call_returns=follow_call_returns,
                )
            )
        for container in module_container_values.get(called_name, ()):
            forbidden.update(
                forbidden_callables_in_taint_source(
                    function,
                    container,
                    resolving | {alias_key},
                    follow_call_returns=follow_call_returns,
                )
            )
        for call_value in module_call_values.get(called_name, ()):
            forbidden.update(
                forbidden_callables_in_taint_source(
                    function,
                    call_value,
                    resolving | {alias_key},
                    follow_call_returns=follow_call_returns,
                )
            )
        for expression_value in module_expression_values.get(called_name, ()):
            forbidden.update(
                forbidden_callables_in_taint_source(
                    function,
                    expression_value,
                    resolving | {alias_key},
                    follow_call_returns=follow_call_returns,
                )
            )
        return forbidden

    def returned_expressions(function):
        if isinstance(function, ast.Lambda):
            return [function.body]

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
        return visitor.values

    def forbidden_callables_in_taint_source(
        function,
        expression,
        resolving,
        *,
        follow_call_returns=True,
    ):
        if isinstance(expression, ast.Name):
            return resolve_forbidden_aliases(
                function,
                expression.id,
                resolving,
                follow_call_returns=follow_call_returns,
            )
        if isinstance(expression, ast.Attribute):
            return (
                {expression.attr}
                if expression.attr in FORBIDDEN_SESSION_WRITER_CALLS
                else set()
            )
        forbidden = set()
        for child in ast.iter_child_nodes(expression):
            forbidden.update(
                forbidden_callables_in_taint_source(
                    function,
                    child,
                    resolving,
                    follow_call_returns=follow_call_returns,
                )
            )
        if isinstance(expression, ast.Call) and follow_call_returns:
            factories, _blocked = resolve_callable_expression(
                function, expression.func
            )
            for factory in factories:
                return_key = ("taint-return", id(factory))
                if return_key in resolving:
                    continue
                for returned in returned_expressions(factory):
                    forbidden.update(
                        forbidden_callables_in_taint_source(
                            factory,
                            returned,
                            resolving | {return_key},
                            follow_call_returns=follow_call_returns,
                        )
                    )
        return forbidden

    def returned_callables(function, resolving_returns=frozenset()):
        if id(function) in resolving_returns:
            return [], set()
        callables = []
        forbidden = set()
        for value in returned_expressions(function):
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
            callables = resolve_named_callable(function, expression.id)
            forbidden = resolve_forbidden_aliases(function, expression.id)
            for callable_node in callables:
                for decorator in function_decorators.get(id(callable_node), ()):
                    forbidden.update(
                        forbidden_callables_in_taint_source(
                            function, decorator, frozenset()
                        )
                    )
            return callables, forbidden
        if isinstance(expression, ast.Attribute):
            forbidden = (
                {expression.attr}
                if expression.attr in FORBIDDEN_SESSION_WRITER_CALLS
                else set()
            )
            callables = resolve_attribute_callable(expression.attr)
            for callable_node in callables:
                for decorator in function_decorators.get(id(callable_node), ()):
                    forbidden.update(
                        forbidden_callables_in_taint_source(
                            function, decorator, frozenset()
                        )
                    )
            return callables, forbidden
        if isinstance(expression, ast.Lambda):
            return [expression], set()
        if isinstance(expression, ast.NamedExpr):
            return resolve_callable_expression(
                function, expression.value, resolving_returns
            )
        if isinstance(expression, ast.Subscript):
            return [], forbidden_callables_in_taint_source(
                function, expression.value, frozenset()
            )
        if isinstance(expression, ast.Call):
            factories, forbidden = resolve_callable_expression(
                function, expression.func, resolving_returns
            )
            for argument in expression.args:
                forbidden.update(
                    forbidden_callables_in_taint_source(
                        function, argument, frozenset()
                    )
                )
            for keyword in expression.keywords:
                forbidden.update(
                    forbidden_callables_in_taint_source(
                        function, keyword.value, frozenset()
                    )
                )
            returned = []
            for factory in factories:
                found, blocked = returned_callables(factory, resolving_returns)
                returned.extend(found)
                forbidden.update(blocked)
            return returned, forbidden
        return [], forbidden_callables_in_taint_source(
            function, expression, frozenset()
        )

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
        if function.args.vararg is not None:
            parameters.add(function.args.vararg.arg)
        if function.args.kwarg is not None:
            parameters.add(function.args.kwarg.arg)
        visitor = DirectCallVisitor()
        if isinstance(function, ast.Lambda):
            visitor.visit(function.body)
        else:
            for statement in function.body:
                visitor.visit(statement)
        called = set()
        for call in visitor.calls:
            root = call.func
            while isinstance(root, ast.Subscript):
                root = root.value
            if isinstance(root, ast.Name) and root.id in parameters:
                called.add(root.id)
        called_parameter_cache[id(function)] = called
        return called

    def parameter_defaults(function):
        positional = [*function.args.posonlyargs, *function.args.args]
        defaults = {
            parameter.arg: default
            for parameter, default in zip(
                positional[-len(function.args.defaults) :],
                function.args.defaults,
            )
        }
        defaults.update(
            {
                parameter.arg: default
                for parameter, default in zip(
                    function.args.kwonlyargs,
                    function.args.kw_defaults,
                )
                if default is not None
            }
        )
        return defaults

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
        supplied_parameters = set()
        for parameter, argument in zip(positional, call.args):
            if parameter.arg in called_parameters:
                supplied_parameters.add(parameter.arg)
                _targets, blocked = resolve_callable_expression(caller, argument)
                forbidden.update(blocked)
        if (
            callee.args.vararg is not None
            and callee.args.vararg.arg in called_parameters
        ):
            for argument in call.args[len(positional) :]:
                _targets, blocked = resolve_callable_expression(caller, argument)
                forbidden.update(blocked)
        keyword_parameters = {
            argument.arg: argument
            for argument in (*callee.args.args, *callee.args.kwonlyargs)
        }
        for keyword in call.keywords:
            if keyword.arg is None:
                if called_parameters:
                    _targets, blocked = resolve_callable_expression(
                        caller, keyword.value
                    )
                    forbidden.update(blocked)
                if isinstance(keyword.value, ast.Dict):
                    supplied_parameters.update(
                        key.value
                        for key in keyword.value.keys
                        if isinstance(key, ast.Constant)
                        and isinstance(key.value, str)
                        and key.value in called_parameters
                    )
                continue
            if (
                keyword.arg in keyword_parameters
                and keyword.arg in called_parameters
            ):
                supplied_parameters.add(keyword.arg)
                _targets, blocked = resolve_callable_expression(
                    caller, keyword.value
                )
                forbidden.update(blocked)
            elif (
                callee.args.kwarg is not None
                and callee.args.kwarg.arg in called_parameters
            ):
                _targets, blocked = resolve_callable_expression(
                    caller, keyword.value
                )
                forbidden.update(blocked)
        for parameter_name in called_parameters - supplied_parameters:
            default = parameter_defaults(callee).get(parameter_name)
            if default is not None:
                forbidden.update(
                    forbidden_callables_in_taint_source(
                        callee, default, frozenset()
                    )
                )
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
                if (
                    isinstance(call.func, ast.Attribute)
                    and call.func.attr
                    in {"add", "append", "extend", "insert", "setdefault", "update"}
                ):
                    for argument in (
                        *call.args,
                        *(keyword.value for keyword in call.keywords),
                    ):
                        forbidden_names.update(
                            forbidden_callables_in_taint_source(
                                function,
                                argument,
                                frozenset(),
                                follow_call_returns=False,
                            )
                        )
                for taint_source in visitor.comprehension_taint_sources.get(
                    id(call), ()
                ):
                    forbidden_names.update(
                        forbidden_callables_in_taint_source(
                            function, taint_source, frozenset()
                        )
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
                    if call_site not in allowed_call_sites:
                        violation = (
                            call_site
                            if include_call_sites
                            else (entry_name, forbidden_name, call.lineno)
                        )
                        violations.add(violation)
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
    StateLock()
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


def test_forbidden_call_inventory_rejects_dict_subscript_writer():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    writers = {"write": atomic_write_json}
    writers["write"]()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 4)
    ]


def test_forbidden_call_inventory_rejects_list_subscript_writer():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    writers = [StateLock]
    writers[0]()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "StateLock", 4)
    ]


def test_forbidden_call_inventory_rejects_nested_subscript_writer():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    writers = {"write": [atomic_write_json]}
    writers["write"][0]()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 4)
    ]


def test_forbidden_call_inventory_rejects_helper_returned_container_writer():
    tree = ast.parse(
        """
def make_writers():
    return [atomic_write_json]

def cmd_supersede_reviews():
    make_writers()[0]()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 6)
    ]


def test_forbidden_call_inventory_rejects_container_mutation_writers():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    writers = {}
    writers["write"] = atomic_write_json
    writers["write"]()
    lock_factories = []
    lock_factories.append(StateLock)
    lock_factories[0]()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "StateLock", 7),
        ("cmd_supersede_reviews", "atomic_write_json", 5),
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


def test_forbidden_call_inventory_rejects_module_call_wrapped_writer():
    tree = ast.parse(
        """
def identity(callable_value):
    return callable_value

safe_writer = identity(atomic_write_json)

def cmd_supersede_reviews():
    safe_writer()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 8)
    ]


def test_forbidden_call_inventory_rejects_local_call_wrapped_writer():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    safe_writer = functools.partial(atomic_write_json, {})
    safe_writer()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 4)
    ]


def test_forbidden_call_inventory_rejects_annotated_taint_sources():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    writers: list = [atomic_write_json]
    writers[0]()
    safe_writer: object = functools.partial(StateLock)
    safe_writer()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "StateLock", 6),
        ("cmd_supersede_reviews", "atomic_write_json", 4),
    ]


def test_forbidden_call_inventory_rejects_tainted_callable_defaults():
    tree = ast.parse(
        """
def invoke_container(writers=[atomic_write_json]):
    writers[0]()

def invoke_call_alias(writer=identity(StateLock)):
    writer()

def cmd_supersede_reviews():
    invoke_container()
    invoke_call_alias()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "StateLock", 10),
        ("cmd_supersede_reviews", "atomic_write_json", 9),
    ]


def test_forbidden_call_inventory_respects_safe_parameter_shadowing():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    writer = atomic_write_json
    def invoke(writer=print):
        writer()
    invoke(print)
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == []


def test_forbidden_call_inventory_rejects_variadic_callback_forwarding():
    tree = ast.parse(
        """
def invoke_args(*writers):
    writers[0]()

def invoke_kwargs(**writers):
    writers["write"]()

def invoke_named(writer):
    writer()

def cmd_supersede_reviews():
    invoke_args(atomic_write_json)
    invoke_kwargs(write=StateLock)
    invoke_named(**{"writer": atomic_write_json})
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "StateLock", 13),
        ("cmd_supersede_reviews", "atomic_write_json", 12),
        ("cmd_supersede_reviews", "atomic_write_json", 14),
    ]


def test_forbidden_call_inventory_rejects_loop_and_context_bindings():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    for writer in [atomic_write_json]:
        writer()
    with contextlib.nullcontext(StateLock) as lock_factory:
        lock_factory()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "StateLock", 6),
        ("cmd_supersede_reviews", "atomic_write_json", 4),
    ]


def test_forbidden_call_inventory_rejects_direct_call_wrapped_writer():
    tree = ast.parse(
        """
def identity(callable_value):
    return callable_value

def cmd_supersede_reviews():
    identity(atomic_write_json)()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 6)
    ]


def test_forbidden_call_inventory_rejects_call_wrapped_decorator():
    tree = ast.parse(
        """
def identity(callable_value):
    return callable_value

@identity(atomic_write_json)
def safe_writer():
    pass

def cmd_supersede_reviews():
    safe_writer()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 10)
    ]


def test_forbidden_call_inventory_rejects_unresolved_tainted_expressions():
    tree = ast.parse(
        """
def safe_writer():
    pass

def cmd_supersede_reviews(flag):
    selected = atomic_write_json if flag else safe_writer
    selected()
    (atomic_write_json if flag else safe_writer)()
    fallback = flag and safe_writer or atomic_write_json
    fallback()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 7),
        ("cmd_supersede_reviews", "atomic_write_json", 8),
        ("cmd_supersede_reviews", "atomic_write_json", 10),
    ]


def test_forbidden_call_inventory_rejects_comprehension_bound_writer():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    list(writer() for writer in [atomic_write_json])
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 3)
    ]


def test_forbidden_call_inventory_does_not_leak_comprehension_taint():
    tree = ast.parse(
        """
def safe_writer():
    pass

def cmd_supersede_reviews():
    writer = safe_writer
    list(value for writer in [atomic_write_json] for value in [1])
    writer()
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == []


def test_forbidden_call_inventory_follows_comprehension_binding_chain():
    tree = ast.parse(
        """
def cmd_supersede_reviews():
    list(writer() for group in [[atomic_write_json]] for writer in group)
"""
    )

    assert forbidden_calls_in_reachable({"cmd_supersede_reviews"}, tree=tree) == [
        ("cmd_supersede_reviews", "atomic_write_json", 3)
    ]


def test_forbidden_call_inventory_tracks_positional_destructuring():
    assignment_tree = ast.parse(
        """
def cmd_supersede_reviews():
    unused, writer = (print, atomic_write_json)
    writer()
"""
    )
    safe_comprehension_tree = ast.parse(
        """
def cmd_supersede_reviews():
    list(writer() for writer, unused in [(print, atomic_write_json)])
"""
    )
    safe_for_tree = ast.parse(
        """
def cmd_supersede_reviews():
    for unused, writer in [(atomic_write_json, print)]:
        writer()
"""
    )

    assert forbidden_calls_in_reachable(
        {"cmd_supersede_reviews"}, tree=assignment_tree
    ) == [("cmd_supersede_reviews", "atomic_write_json", 4)]
    assert forbidden_calls_in_reachable(
        {"cmd_supersede_reviews"}, tree=safe_comprehension_tree
    ) == []
    assert forbidden_calls_in_reachable(
        {"cmd_supersede_reviews"}, tree=safe_for_tree
    ) == []


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


def test_c2_repository_and_direct_write_inventories_are_closed_and_disjoint():
    from mission_application.command_owners import (
        C2_DIRECT_WRITE_ALLOWLIST,
        C2_REPOSITORY_COMMANDS,
        COMMAND_OWNER_REGISTRY,
    )

    assert C2_REPOSITORY_COMMANDS == frozenset(
        {
            "executor-handoff begin",
            "executor-handoff complete",
            "executor-handoff record-step",
            "executor-handoff verify-step",
            "planning reselect",
            "supersede-reviews",
            # Batch 2
            "specialists recommend",
            "specialists log-invocation",
            "specialists verify-approval",
            "specialists prepare-invocation",
            "specialists invoke-command",
            "specialists invoke-prepared",
            "specialists reconcile-invocation",
            "specialists plan-import",
        }
    )
    assert C2_DIRECT_WRITE_ALLOWLIST == frozenset(
        {
            "manual-score-capture",
            "planning adopt-core",
            "planning promote-provider-plan",
        }
    )
    assert C2_REPOSITORY_COMMANDS.isdisjoint(C2_DIRECT_WRITE_ALLOWLIST)
    assert C2_REPOSITORY_COMMANDS | C2_DIRECT_WRITE_ALLOWLIST <= set(
        COMMAND_OWNER_REGISTRY
    )


def test_c2_repository_commands_have_no_direct_legacy_session_writer_calls():
    target_names = {
        "cmd_executor_handoff_begin",
        "cmd_executor_handoff_complete",
        "cmd_executor_handoff_record",
        "cmd_executor_handoff_verify",
        "cmd_planning_reselect",
        "cmd_supersede_reviews",
        # Batch 2
        "cmd_specialists",
        "cmd_log_specialist_invocation",
        "cmd_verify_provider_approval",
        "cmd_prepare_provider_invocation",
        "cmd_invoke_command_provider",
        "cmd_reconcile_provider_invocation",
        "cmd_plan_import",
    }

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


def test_direct_legacy_call_allowlist_has_no_stale_entries():
    source = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    entry_names = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and (node.name.startswith("cmd_") or node.name.startswith("_cmd_"))
    }
    unallowlisted_call_sites = set(
        forbidden_calls_in_reachable(
            entry_names,
            tree=tree,
            allowed_call_sites=frozenset(),
            include_call_sites=True,
        )
    )

    assert ALLOWED_NON_C2_CALL_SITES - unallowlisted_call_sites == set()


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
