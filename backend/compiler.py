"""Compiler for PyChain's deliberately small, typed EVM contract language.

This is not a Python runtime or a source-to-source rewrite of arbitrary Python.
Only constructs represented by the IR below are accepted.
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from typing import Any


class CompileFailure(Exception):
    def __init__(self, diagnostics: list[dict[str, Any]]):
        self.diagnostics = diagnostics
        super().__init__("Compilation failed")


@dataclass(frozen=True)
class TypeRef:
    name: str | None = None
    key: TypeRef | None = None
    value: TypeRef | None = None

    @property
    def is_mapping(self) -> bool:
        return self.name is None

    def solidity(self) -> str:
        if self.is_mapping:
            assert self.key and self.value
            return f"mapping({self.key.solidity()} => {self.value.solidity()})"
        assert self.name
        return self.name

    def as_dict(self) -> dict[str, Any] | str:
        if not self.is_mapping:
            return self.name or ""
        assert self.key and self.value
        return {"mapping": [self.key.as_dict(), self.value.as_dict()]}


@dataclass
class Expr:
    kind: str
    type_name: str | None = None
    value: Any = None
    args: list[Expr] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": self.kind}
        if self.value is not None:
            result["value"] = self.value
        if self.type_name is not None:
            result["type"] = self.type_name
        if self.args:
            result["args"] = [arg.as_dict() for arg in self.args]
        return result


@dataclass
class Statement:
    kind: str
    line: int
    target: Expr | None = None
    expr: Expr | None = None
    name: str | None = None
    declared_type: TypeRef | None = None
    operator: str | None = None
    body: list[Statement] = field(default_factory=list)
    otherwise: list[Statement] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": self.kind, "line": self.line}
        if self.name is not None:
            result["name"] = self.name
        if self.operator is not None:
            result["operator"] = self.operator
        if self.declared_type is not None:
            result["type"] = self.declared_type.as_dict()
        if self.target is not None:
            result["target"] = self.target.as_dict()
        if self.expr is not None:
            result["expr"] = self.expr.as_dict()
        if self.body:
            result["body"] = [item.as_dict() for item in self.body]
        if self.otherwise:
            result["else"] = [item.as_dict() for item in self.otherwise]
        return result


@dataclass
class StateField:
    name: str
    type_ref: TypeRef
    initial: Expr | None
    line: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type_ref.as_dict(),
            "initial": self.initial.as_dict() if self.initial else None,
        }


@dataclass
class Event:
    name: str
    args: list[tuple[str, TypeRef]]
    line: int

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "args": [{"name": name, "type": typ.as_dict()} for name, typ in self.args]}


@dataclass
class Function:
    name: str
    args: list[tuple[str, TypeRef]]
    returns: TypeRef | None
    visibility: str
    mutability: str
    payable: bool
    body: list[Statement]
    line: int
    is_constructor: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "args": [{"name": name, "type": typ.as_dict()} for name, typ in self.args],
            "returns": self.returns.as_dict() if self.returns else None,
            "visibility": self.visibility,
            "mutability": self.mutability or "nonpayable",
            "payable": self.payable,
            "body": [statement.as_dict() for statement in self.body],
        }


@dataclass
class ContractIR:
    name: str
    state: list[StateField]
    events: list[Event]
    functions: list[Function]
    uses_send_eth: bool = False

    def as_dict(self) -> dict[str, Any]:
        features = ["typed-state"]
        if any(item.type_ref.is_mapping for item in self.state):
            features.append("mappings")
        if self.events:
            features.append("events")
        if any(function.payable for function in self.functions):
            features.append("payable")
        if self.uses_send_eth:
            features.append("eth-transfer")
        all_expressions = [expr for function in self.functions for expr in _ir_expressions(function.body)]
        if any(expr.kind == "context" for expr in all_expressions):
            features.append("evm-context")
        if any(statement.kind == "if" for function in self.functions for statement in _ir_statements(function.body)):
            features.append("conditionals")
        return {
            "kind": "Contract",
            "name": self.name,
            "target": "pychain-ir/v1",
            "state": [item.as_dict() for item in self.state],
            "events": [item.as_dict() for item in self.events],
            "functions": [item.as_dict() for item in self.functions],
            "features": features,
        }


def _ir_statements(body: list[Statement]):
    for statement in body:
        yield statement
        yield from _ir_statements(statement.body)
        yield from _ir_statements(statement.otherwise)


def _ir_expressions(body: list[Statement]):
    def visit(expr: Expr | None):
        if expr is None:
            return
        yield expr
        for child in expr.args:
            yield from visit(child)

    for statement in _ir_statements(body):
        yield from visit(statement.target)
        yield from visit(statement.expr)


BASE_TYPES = {"bool", "address", "string", "bytes"}
ALLOWED_IMPORTS = {
    "contract", "event", "external", "view", "pure", "payable",
    "mapping", "msg", "block", "require", "emit", "send_eth",
    "balance", "uint", "int", "bytes32",
}
BINOPS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.FloorDiv: "/"}
AUGOPS = {ast.Add: "+=", ast.Sub: "-=", ast.Mult: "*=", ast.FloorDiv: "/="}
COMPARES = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}


def diagnostic(node: ast.AST, message: str, severity: str = "error", code: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "line": getattr(node, "lineno", 1),
        "column": getattr(node, "col_offset", 0) + 1,
        "severity": severity,
        "message": message,
    }
    if code:
        result["code"] = code
    return result


def is_uint(name: str | None) -> bool:
    return bool(name and re.fullmatch(r"uint(?:8|16|24|32|40|48|56|64|72|80|88|96|104|112|120|128|136|144|152|160|168|176|184|192|200|208|216|224|232|240|248|256)", name))


def is_int(name: str | None) -> bool:
    return bool(name and re.fullmatch(r"int(?:8|16|24|32|40|48|56|64|72|80|88|96|104|112|120|128|136|144|152|160|168|176|184|192|200|208|216|224|232|240|248|256)", name))


def is_numeric(name: str | None) -> bool:
    return name == "literal_number" or is_uint(name) or is_int(name)


def is_reference(name: str | None) -> bool:
    return name in {"string", "bytes"}


def sol_type_with_location(type_ref: TypeRef) -> str:
    solidity_type = type_ref.solidity()
    if is_reference(type_ref.name):
        return f"{solidity_type} memory"
    return solidity_type


class Parser:
    def __init__(self, source: str):
        self.source = source
        self.errors: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []
        self.state: dict[str, TypeRef] = {}
        self.events: dict[str, Event] = {}
        self.locals: dict[str, str] = {}
        self.function_name = ""
        self.function_mutability = ""
        self.uses_send_eth = False
        self.current_return: TypeRef | None = None
        self._decorator_cache: dict[int, set[str]] = {}

    def parse(self) -> tuple[ContractIR, list[dict[str, Any]]]:
        try:
            module = ast.parse(self.source)
        except SyntaxError as exc:
            raise CompileFailure([{
                "line": exc.lineno or 1,
                "column": exc.offset or 1,
                "severity": "error",
                "message": exc.msg,
                "code": "PY001",
            }]) from exc

        contracts: list[ast.ClassDef] = []
        for node in module.body:
            if self._is_module_docstring(node):
                continue
            if isinstance(node, ast.ImportFrom):
                self._validate_import(node)
            elif isinstance(node, ast.ClassDef):
                if self._decorator_set(node) == {"contract"}:
                    contracts.append(node)
                else:
                    self.errors.append(diagnostic(node, "Top-level classes must use exactly one @contract decorator.", code="PY002"))
            else:
                self.errors.append(diagnostic(node, "Only PyChain imports, a module docstring, and one @contract class are allowed at module scope.", code="PY003"))

        if len(contracts) != 1:
            self.errors.append({"line": 1, "column": 1, "severity": "error", "code": "PY004", "message": "Declare exactly one class with @contract."})
        if self.errors:
            raise CompileFailure(self.errors)

        contract = contracts[0]
        fields: list[StateField] = []
        events: list[Event] = []
        functions: list[Function] = []

        # Declarations are collected before method bodies are analyzed so
        # methods can reference state/events regardless of class declaration order.
        for node in contract.body:
            if self._is_docstring(node) or isinstance(node, ast.Pass):
                continue
            if isinstance(node, ast.AnnAssign):
                field_value = self._state_field(node)
                if field_value:
                    fields.append(field_value)
                    self.state[field_value.name] = field_value.type_ref
            else:
                if not isinstance(node, ast.FunctionDef):
                    self.errors.append(diagnostic(node, "Contract bodies support typed state, @event declarations, and methods only.", code="PY005"))

        for node in contract.body:
            if isinstance(node, ast.FunctionDef) and "event" in self._decorator_set(node):
                event = self._event(node)
                if event:
                    events.append(event)
                    self.events[event.name] = event

        for node in contract.body:
            if isinstance(node, ast.FunctionDef) and "event" not in self._decorator_set(node):
                function = self._function(node)
                if function:
                    functions.append(function)

        all_names = [item.name for item in fields] + [item.name for item in events] + [item.name for item in functions if not item.is_constructor]
        if len(all_names) != len(set(all_names)):
            self.errors.append(diagnostic(contract, "State, event, and function names must be unique within a contract.", code="PY006"))
        if sum(function.is_constructor for function in functions) > 1:
            self.errors.append(diagnostic(contract, "A contract may define only one __init__ constructor.", code="PY097"))
        if any(name.startswith("_pychain") for name in all_names):
            self.errors.append(diagnostic(contract, "Names beginning with `_pychain` are reserved for compiler-generated helpers.", code="PY007"))

        if self.errors:
            raise CompileFailure(self.errors)

        ir = ContractIR(contract.name, fields, events, functions, self.uses_send_eth)
        self.warnings.extend(self._security_warnings(ir))
        return ir, self.warnings

    def _validate_import(self, node: ast.ImportFrom) -> None:
        if node.module != "pychain" or node.level != 0:
            self.errors.append(diagnostic(node, "Only `from pychain import ...` is supported; imported Python code is never executed or inlined.", code="PY008"))
            return
        for alias in node.names:
            if alias.name == "*" or alias.asname is not None or alias.name not in ALLOWED_IMPORTS:
                self.errors.append(diagnostic(node, f"Unsupported PyChain import `{alias.name}`.", code="PY009"))

    @staticmethod
    def _is_docstring(node: ast.stmt) -> bool:
        return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)

    @classmethod
    def _is_module_docstring(cls, node: ast.stmt) -> bool:
        return cls._is_docstring(node)

    def _decorator_set(self, node: ast.FunctionDef | ast.ClassDef) -> set[str]:
        if id(node) in self._decorator_cache:
            return self._decorator_cache[id(node)]
        names: set[str] = set()
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Name):
                self.errors.append(diagnostic(decorator, "Decorators must be bare PyChain names, not calls or attributes.", code="PY010"))
            else:
                if decorator.id in names:
                    self.errors.append(diagnostic(decorator, f"Duplicate decorator `@{decorator.id}`.", code="PY011"))
                names.add(decorator.id)
        self._decorator_cache[id(node)] = names
        return names

    def _parse_type(self, node: ast.expr | None, owner: ast.AST, mapping_value: bool = False) -> TypeRef | None:
        if isinstance(node, ast.Name):
            name = node.id
            if name == "uint":
                name = "uint256"
            elif name == "int":
                name = "int256"
            valid = name in BASE_TYPES or is_uint(name) or is_int(name) or bool(re.fullmatch(r"bytes(?:[1-9]|[12][0-9]|3[0-2])", name))
            if valid:
                return TypeRef(name=name)
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "mapping":
            entries = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            if len(entries) == 2:
                key_type = self._parse_type(entries[0], owner)
                value_type = self._parse_type(entries[1], owner, mapping_value=True)
                if key_type and value_type:
                    if key_type.name in {"string", "bytes"} or key_type.is_mapping:
                        self.errors.append(diagnostic(owner, "Mapping keys must be fixed-size value types (for example address or uint).", code="PY012"))
                    if value_type.is_mapping:
                        self.errors.append(diagnostic(owner, "Nested mappings are not supported yet.", code="PY013"))
                    return TypeRef(key=key_type, value=value_type)
        self.errors.append(diagnostic(owner, "Unsupported type. Use uint/int (optionally sized), bool, address, bytesN, string, bytes, or mapping[key, value].", code="PY014"))
        return None

    def _state_field(self, node: ast.AnnAssign) -> StateField | None:
        if not isinstance(node.target, ast.Name):
            self.errors.append(diagnostic(node, "State declarations must use a simple name.", code="PY015"))
            return None
        name = node.target.id
        if name in self.state or name.startswith("_pychain"):
            self.errors.append(diagnostic(node, f"Invalid or duplicate state name `{name}`.", code="PY016"))
        type_ref = self._parse_type(node.annotation, node)
        initial: Expr | None = None
        if type_ref and type_ref.is_mapping:
            if not isinstance(node.value, ast.Dict) or node.value.keys or node.value.values:
                self.errors.append(diagnostic(node, "Mappings start empty; use `{}` or omit the initializer.", code="PY017"))
        elif node.value is not None:
            initial = self._expression(node.value, {}, allow_state=True)
            if type_ref and initial:
                self._check_assignable(type_ref, initial, node.value)
                if initial.kind not in {"literal", "unary"}:
                    self.errors.append(diagnostic(node.value, "State initializers must be compile-time literals; initialize runtime values in __init__.", code="PY018"))
        if type_ref is None:
            return None
        return StateField(name, type_ref, initial, node.lineno)

    def _event(self, node: ast.FunctionDef) -> Event | None:
        if self._decorator_set(node) != {"event"}:
            self.errors.append(diagnostic(node, "An event declaration uses only @event.", code="PY019"))
        if node.args.vararg or node.args.kwarg or node.args.kwonlyargs or node.args.defaults or node.args.posonlyargs:
            self.errors.append(diagnostic(node, "Event declarations support named, typed positional fields only.", code="PY020"))
        if node.returns is not None:
            self.errors.append(diagnostic(node, "Events cannot declare a return type.", code="PY021"))
        if not (len(node.body) == 1 and (
            isinstance(node.body[0], ast.Pass)
            or isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and node.body[0].value.value is Ellipsis
        )):
            self.errors.append(diagnostic(node, "Event declarations use `...` (or `pass`) as their body.", code="PY022"))
        args: list[tuple[str, TypeRef]] = []
        for arg in node.args.args:
            if arg.annotation is None:
                self.errors.append(diagnostic(arg, "Every event field needs a supported type annotation.", code="PY023"))
                continue
            typ = self._parse_type(arg.annotation, arg)
            if typ:
                if typ.is_mapping:
                    self.errors.append(diagnostic(arg, "Event fields cannot be mappings.", code="PY024"))
                args.append((arg.arg, typ))
        return Event(node.name, args, node.lineno)

    def _function(self, node: ast.FunctionDef) -> Function | None:
        decorators = self._decorator_set(node)
        allowed = {"external", "view", "pure", "payable"}
        unexpected = decorators - allowed
        if unexpected:
            self.errors.append(diagnostic(node, f"Unsupported method decorator(s): {', '.join('@' + name for name in sorted(unexpected))}.", code="PY025"))
        if "view" in decorators and "pure" in decorators:
            self.errors.append(diagnostic(node, "A method cannot be both @view and @pure.", code="PY026"))
        if "payable" in decorators and ({"view", "pure"} & decorators):
            self.errors.append(diagnostic(node, "@payable cannot be combined with @view or @pure.", code="PY027"))
        if node.args.vararg or node.args.kwarg or node.args.kwonlyargs or node.args.defaults or node.args.posonlyargs:
            self.errors.append(diagnostic(node, "Contract methods require typed positional parameters without defaults or variadic arguments.", code="PY028"))

        is_constructor = node.name == "__init__"
        if not is_constructor and not (decorators & {"external", "view", "pure"}):
            self.errors.append(diagnostic(node, "Mark callable contract methods explicitly with @external, @view, or @pure.", code="PY103"))
        if is_constructor and decorators & {"view", "pure", "external"}:
            self.errors.append(diagnostic(node, "Constructors cannot use @external, @view, or @pure.", code="PY029"))
        if is_constructor and node.returns is not None:
            self.errors.append(diagnostic(node, "Constructors cannot return a value.", code="PY030"))
        if not node.args.args or node.args.args[0].arg != "self":
            self.errors.append(diagnostic(node, "Contract methods must begin with `self`.", code="PY031"))
        if any(arg.arg == "self" for arg in node.args.args[1:]):
            self.errors.append(diagnostic(node, "`self` is reserved for the contract instance.", code="PY032"))

        args: list[tuple[str, TypeRef]] = []
        local_types: dict[str, str] = {}
        for arg in node.args.args[1:]:
            if arg.annotation is None:
                self.errors.append(diagnostic(arg, "Every method parameter needs a supported type annotation.", code="PY033"))
                continue
            typ = self._parse_type(arg.annotation, arg)
            if typ:
                if typ.is_mapping:
                    self.errors.append(diagnostic(arg, "Mappings cannot be passed as method arguments.", code="PY034"))
                args.append((arg.arg, typ))
                local_types[arg.arg] = typ.solidity()

        returns = self._parse_type(node.returns, node) if node.returns is not None else None
        if returns and returns.is_mapping:
            self.errors.append(diagnostic(node, "Methods cannot return mappings.", code="PY035"))
        self.locals = local_types
        self.function_name = node.name
        self.function_mutability = "view" if "view" in decorators else "pure" if "pure" in decorators else ""
        self.current_return = returns
        body = self._statements(node.body, dict(local_types), allow_declaration=True)

        if self.function_mutability == "pure" and self._body_uses_state_or_context(body):
            self.errors.append(diagnostic(node, "@pure methods cannot read/write contract state or blockchain context.", code="PY036"))
        if self.function_mutability == "view" and self._body_writes_state_or_calls(body):
            self.errors.append(diagnostic(node, "@view methods cannot write state, emit events, or transfer ETH.", code="PY037"))
        if returns and not self._always_returns(body):
            self.errors.append(diagnostic(node, f"Not every path in `{node.name}` returns a value.", code="PY038"))

        payable = "payable" in decorators
        if "attached_value" in self._context_members(body) and not payable:
            self.errors.append(diagnostic(node, "Reading msg.value requires an @payable method or constructor.", code="PY040"))

        return Function(
            name="constructor" if is_constructor else node.name,
            args=args,
            returns=returns,
            visibility="constructor" if is_constructor else "external",
            mutability=self.function_mutability,
            payable=payable,
            body=body,
            line=node.lineno,
            is_constructor=is_constructor,
        )

    def _statements(self, nodes: list[ast.stmt], locals_in: dict[str, str], allow_declaration: bool) -> list[Statement]:
        statements: list[Statement] = []
        locals_scope = locals_in
        for node in nodes:
            if self._is_docstring(node) or isinstance(node, ast.Pass):
                continue
            if isinstance(node, ast.AnnAssign):
                if not allow_declaration:
                    self.errors.append(diagnostic(node, "Typed local declarations are only supported at function scope, not inside branches.", code="PY041"))
                    continue
                if not isinstance(node.target, ast.Name) or node.value is None:
                    self.errors.append(diagnostic(node, "Local declarations must be `name: type = value`.", code="PY042"))
                    continue
                name = node.target.id
                if name in locals_scope or name in self.state or name == "self":
                    self.errors.append(diagnostic(node, f"Local `{name}` conflicts with an existing parameter, local, state field, or reserved name.", code="PY043"))
                    continue
                typ = self._parse_type(node.annotation, node)
                value = self._expression(node.value, locals_scope)
                if typ and value:
                    if typ.is_mapping:
                        self.errors.append(diagnostic(node, "Local mapping declarations are not supported.", code="PY044"))
                        continue
                    self._check_assignable(typ, value, node.value)
                    locals_scope[name] = typ.solidity()
                    statements.append(Statement("local_declare", node.lineno, expr=value, name=name, declared_type=typ))
                continue
            if isinstance(node, ast.Assign):
                if len(node.targets) != 1:
                    self.errors.append(diagnostic(node, "Chained or multiple assignment targets are not supported.", code="PY045"))
                    continue
                target = self._target(node.targets[0], locals_scope)
                value = self._expression(node.value, locals_scope)
                if target and value:
                    self._check_assignable(TypeRef(name=target.type_name), value, node.value)
                    statements.append(Statement("assign", node.lineno, target=target, expr=value))
                continue
            if isinstance(node, ast.AugAssign):
                target = self._target(node.target, locals_scope)
                value = self._expression(node.value, locals_scope)
                operator = AUGOPS.get(type(node.op))
                if not operator:
                    self.errors.append(diagnostic(node, "Only +=, -=, *=, and //= are supported.", code="PY046"))
                elif target and value:
                    if target.type_name and not is_numeric(target.type_name):
                        self.errors.append(diagnostic(node, "Augmented arithmetic is only supported for integer values.", code="PY047"))
                    self._check_numeric_operands(target.type_name, value.type_name, node)
                    statements.append(Statement("augassign", node.lineno, target=target, expr=value, operator=operator))
                continue
            if isinstance(node, ast.Return):
                value = self._expression(node.value, locals_scope) if node.value else None
                if self.current_return is None and value is not None:
                    self.errors.append(diagnostic(node, f"`{self.function_name}` has a return value but no return type annotation.", code="PY039"))
                elif self.current_return is not None:
                    if value is None:
                        self.errors.append(diagnostic(node, "A value-returning method must return a value on every return statement.", code="PY098"))
                    else:
                        self._check_assignable(self.current_return, value, node)
                statements.append(Statement("return", node.lineno, expr=value))
                continue
            if isinstance(node, ast.Assert):
                condition = self._expression(node.test, locals_scope)
                message = self._expression(node.msg, locals_scope) if node.msg else Expr("literal", "string", "Assertion failed")
                if condition and condition.type_name != "bool":
                    self.errors.append(diagnostic(node.test, "An assertion condition must be boolean.", code="PY048"))
                if message and message.type_name != "string":
                    self.errors.append(diagnostic(node.msg or node, "An assertion message must be a string literal.", code="PY049"))
                if condition and message:
                    statements.append(Statement("require", node.lineno, expr=Expr("require", "void", args=[condition, message])))
                continue
            if isinstance(node, ast.If):
                condition = self._expression(node.test, locals_scope)
                if condition and condition.type_name != "bool":
                    self.errors.append(diagnostic(node.test, "An if condition must be boolean.", code="PY050"))
                body = self._statements(node.body, dict(locals_scope), allow_declaration=False)
                otherwise = self._statements(node.orelse, dict(locals_scope), allow_declaration=False)
                statements.append(Statement("if", node.lineno, expr=condition, body=body, otherwise=otherwise))
                continue
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                expression = self._expression(node.value, locals_scope, allow_void_call=True)
                if expression and expression.kind in {"require", "emit", "native_transfer"}:
                    statements.append(Statement(expression.kind, node.lineno, expr=expression))
                else:
                    self.errors.append(diagnostic(node, "Only require(...), emit(...), and send_eth(...) can be standalone calls.", code="PY051"))
                continue
            message = "Loops are not supported because they can create unbounded gas use." if isinstance(node, (ast.For, ast.While, ast.AsyncFor)) else f"{type(node).__name__} statements are not supported by the PyChain contract subset."
            self.errors.append(diagnostic(node, message, code="PY052"))
        return statements

    def _target(self, node: ast.expr, locals_scope: dict[str, str]) -> Expr | None:
        if isinstance(node, ast.Name):
            if node.id not in locals_scope:
                self.errors.append(diagnostic(node, "Assign to a typed local declared earlier (`name: type = value`) or to `self.<state>`.", code="PY053"))
                return None
            return Expr("name", locals_scope[node.id], node.id)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
            type_ref = self.state.get(node.attr)
            if not type_ref:
                self.errors.append(diagnostic(node, f"`{node.attr}` is not a declared contract state field.", code="PY054"))
                return None
            if type_ref.is_mapping:
                self.errors.append(diagnostic(node, "Mapping state must be indexed when read or written.", code="PY055"))
                return None
            return Expr("state", type_ref.name, node.attr)
        if isinstance(node, ast.Subscript):
            target = self._expression(node, locals_scope)
            if target and target.kind == "mapping_get":
                target.kind = "mapping_target"
                return target
        self.errors.append(diagnostic(node, "Assignments may target a declared local, `self.state`, or a mapping item such as `self.balances[msg.sender]`.", code="PY056"))
        return None

    def _expression(self, node: ast.expr | None, locals_scope: dict[str, str], allow_state: bool = True, allow_void_call: bool = False) -> Expr | None:
        if node is None:
            return None
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return Expr("literal", "bool", node.value)
            if isinstance(node.value, int):
                return Expr("literal", "literal_number", node.value)
            if isinstance(node.value, str):
                return Expr("literal", "string", node.value)
            self.errors.append(diagnostic(node, "Only integer, boolean, and string literals are supported.", code="PY057"))
            return None
        if isinstance(node, ast.Name):
            if node.id in locals_scope:
                return Expr("name", locals_scope[node.id], node.id)
            if allow_state and node.id in self.state:
                state_type = self.state[node.id]
                if state_type.is_mapping:
                    self.errors.append(diagnostic(node, "Mappings must be indexed, for example `self.balances[msg.sender]`.", code="PY058"))
                    return None
                return Expr("state", state_type.name, node.id)
            self.errors.append(diagnostic(node, f"Unknown name `{node.id}`. Python globals and arbitrary runtime names are unavailable in contracts.", code="PY059"))
            return None
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "self":
                state_type = self.state.get(node.attr)
                if not state_type:
                    self.errors.append(diagnostic(node, f"`{node.attr}` is not a declared contract state field.", code="PY060"))
                    return None
                if state_type.is_mapping:
                    self.errors.append(diagnostic(node, f"Mapping `{node.attr}` must be indexed.", code="PY061"))
                    return None
                return Expr("state", state_type.name, node.attr)
            if isinstance(node.value, ast.Name) and node.value.id == "msg" and node.attr in {"sender", "value"}:
                if self.function_mutability == "pure":
                    self.errors.append(diagnostic(node, "@pure methods cannot read msg context.", code="PY062"))
                key = "caller" if node.attr == "sender" else "attached_value"
                return Expr("context", "address" if node.attr == "sender" else "uint256", key)
            if isinstance(node.value, ast.Name) and node.value.id == "block" and node.attr == "timestamp":
                if self.function_mutability == "pure":
                    self.errors.append(diagnostic(node, "@pure methods cannot read block context.", code="PY063"))
                return Expr("context", "uint256", "timestamp")
            self.errors.append(diagnostic(node, "Only self.<state>, msg.sender, msg.value, and block.timestamp attribute access is supported.", code="PY064"))
            return None
        if isinstance(node, ast.Subscript):
            if not isinstance(node.value, ast.Attribute) or not isinstance(node.value.value, ast.Name) or node.value.value.id != "self":
                self.errors.append(diagnostic(node, "Only declared contract mappings can be indexed.", code="PY065"))
                return None
            type_ref = self.state.get(node.value.attr)
            if not type_ref or not type_ref.is_mapping:
                self.errors.append(diagnostic(node, f"`{node.value.attr}` is not a declared mapping state field.", code="PY066"))
                return None
            assert type_ref.key and type_ref.value
            key = self._expression(node.slice, locals_scope)
            if key:
                self._check_assignable(type_ref.key, key, node.slice)
            if type_ref.value.is_mapping:
                self.errors.append(diagnostic(node, "Nested mapping reads are not supported yet.", code="PY067"))
                return None
            return Expr("mapping_get", type_ref.value.name, node.value.attr, [key] if key else [])
        if isinstance(node, ast.BinOp):
            operator = BINOPS.get(type(node.op))
            if not operator:
                self.errors.append(diagnostic(node, "Only +, -, *, and // integer arithmetic are supported.", code="PY068"))
                return None
            left = self._expression(node.left, locals_scope)
            right = self._expression(node.right, locals_scope)
            if not left or not right:
                return None
            self._check_numeric_operands(left.type_name, right.type_name, node)
            result_type = self._numeric_result_type(left.type_name, right.type_name)
            if operator == "/" and (
                result_type and is_int(result_type)
                or self._is_negative_literal(node.left)
                or self._is_negative_literal(node.right)
            ):
                self.errors.append(diagnostic(node, "Python floor division differs from Solidity signed division for negative values; use unsigned values or avoid //.", "warning", "PY069"))
            return Expr("binary", result_type, operator, [left, right])
        if isinstance(node, ast.UnaryOp):
            operand = self._expression(node.operand, locals_scope)
            if not operand:
                return None
            if isinstance(node.op, ast.Not):
                if operand.type_name != "bool":
                    self.errors.append(diagnostic(node, "`not` requires a boolean expression.", code="PY070"))
                return Expr("unary", "bool", "!", [operand])
            if isinstance(node.op, ast.USub):
                if not is_numeric(operand.type_name):
                    self.errors.append(diagnostic(node, "Unary minus requires an integer expression.", code="PY071"))
                if is_uint(operand.type_name):
                    self.errors.append(diagnostic(node, "Unary minus is invalid for an unsigned value.", code="PY072"))
                return Expr("unary", operand.type_name, "-", [operand])
            self.errors.append(diagnostic(node, "This unary operator is not supported.", code="PY073"))
            return None
        if isinstance(node, ast.BoolOp):
            operands = [self._expression(value, locals_scope) for value in node.values]
            if any(item is None for item in operands):
                return None
            if any(item.type_name != "bool" for item in operands if item):
                self.errors.append(diagnostic(node, "`and` / `or` require boolean operands.", code="PY074"))
            operator = "&&" if isinstance(node.op, ast.And) else "||" if isinstance(node.op, ast.Or) else None
            if operator is None:
                self.errors.append(diagnostic(node, "This boolean operator is not supported.", code="PY075"))
                return None
            return Expr("boolop", "bool", operator, [item for item in operands if item])
        if isinstance(node, ast.Compare):
            if len(node.ops) != 1 or len(node.comparators) != 1 or type(node.ops[0]) not in COMPARES:
                self.errors.append(diagnostic(node, "Use one supported comparison at a time; chained comparisons are not supported.", code="PY076"))
                return None
            left = self._expression(node.left, locals_scope)
            right = self._expression(node.comparators[0], locals_scope)
            if not left or not right:
                return None
            operator = COMPARES[type(node.ops[0])]
            if not self._comparable(left.type_name, right.type_name, operator):
                self.errors.append(diagnostic(node, "These values cannot be compared with this operator.", code="PY077"))
            if (is_uint(left.type_name) and self._is_negative_literal(node.comparators[0])) or (
                is_uint(right.type_name) and self._is_negative_literal(node.left)
            ):
                self.errors.append(diagnostic(node, "A negative literal cannot be compared with an unsigned integer.", code="PY101"))
            return Expr("compare", "bool", operator, [left, right])
        if isinstance(node, ast.Call):
            return self._call(node, locals_scope, allow_void_call)
        self.errors.append(diagnostic(node, f"Unsupported expression `{type(node).__name__}`. Calls, comprehensions, lambdas, and dynamic Python values are not executed.", code="PY078"))
        return None

    def _call(self, node: ast.Call, locals_scope: dict[str, str], allow_void_call: bool) -> Expr | None:
        if not isinstance(node.func, ast.Name) or node.keywords:
            self.errors.append(diagnostic(node, "Only documented PyChain built-ins may be called; attribute and keyword calls are rejected.", code="PY079"))
            return None
        name = node.func.id
        if name == "require":
            if len(node.args) != 2:
                self.errors.append(diagnostic(node, "require(condition, message) takes exactly two arguments.", code="PY080"))
                return None
            condition = self._expression(node.args[0], locals_scope)
            message = self._expression(node.args[1], locals_scope)
            if condition and condition.type_name != "bool":
                self.errors.append(diagnostic(node.args[0], "require's first argument must be boolean.", code="PY081"))
            if message and message.type_name != "string":
                self.errors.append(diagnostic(node.args[1], "require's message must be a string literal.", code="PY082"))
            if condition and message:
                return Expr("require", "void", args=[condition, message])
            return None
        if name == "send_eth":
            if len(node.args) != 2:
                self.errors.append(diagnostic(node, "send_eth(recipient, amount) takes exactly two arguments.", code="PY083"))
                return None
            recipient = self._expression(node.args[0], locals_scope)
            amount = self._expression(node.args[1], locals_scope)
            if recipient and recipient.type_name != "address":
                self.errors.append(diagnostic(node.args[0], "send_eth recipient must be an address.", code="PY084"))
            if amount and not (is_uint(amount.type_name) or amount.type_name == "literal_number" and amount.value >= 0):
                self.errors.append(diagnostic(node.args[1], "send_eth amount must be an unsigned integer.", code="PY085"))
            self.uses_send_eth = True
            return Expr("native_transfer", "void", args=[recipient, amount] if recipient and amount else [])
        if name == "emit":
            if len(node.args) < 1 or not isinstance(node.args[0], ast.Name):
                self.errors.append(diagnostic(node, "Use emit(EventName, arg1, ...) for a declared @event.", code="PY086"))
                return None
            event = self.events.get(node.args[0].id)
            if not event:
                self.errors.append(diagnostic(node.args[0], f"Unknown event `{node.args[0].id}`.", code="PY087"))
                return None
            values = [self._expression(arg, locals_scope) for arg in node.args[1:]]
            if len(values) != len(event.args):
                self.errors.append(diagnostic(node, f"Event {event.name} expects {len(event.args)} argument(s), got {len(values)}.", code="PY088"))
            for index, (value, (_, typ)) in enumerate(zip(values, event.args)):
                if value:
                    self._check_assignable(typ, value, node.args[index + 1])
            if any(value is None for value in values):
                return None
            return Expr("emit", "void", event.name, [value for value in values if value])
        if name == "balance":
            if node.args:
                self.errors.append(diagnostic(node, "balance() takes no arguments.", code="PY089"))
                return None
            return Expr("native_balance", "uint256")
        if name == "address":
            if len(node.args) != 1:
                self.errors.append(diagnostic(node, "address(value) takes exactly one argument.", code="PY090"))
                return None
            value = self._expression(node.args[0], locals_scope)
            if value and value.type_name not in {"address", "literal_number"}:
                self.errors.append(diagnostic(node.args[0], "Only an address or integer literal can be cast to address.", code="PY091"))
            return Expr("cast", "address", "address", [value] if value else [])
        self.errors.append(diagnostic(node, f"Unknown call `{name}`. External contract calls and arbitrary Python calls are not supported.", code="PY092"))
        return None

    def _check_numeric_operands(self, left: str | None, right: str | None, node: ast.AST) -> None:
        if not is_numeric(left) or not is_numeric(right):
            self.errors.append(diagnostic(node, "Arithmetic operators require integer operands.", code="PY093"))
            return
        if left != "literal_number" and right != "literal_number" and left != right:
            self.errors.append(diagnostic(node, "Mixed signed/unsigned or differently sized integer arithmetic needs an explicit supported type conversion.", code="PY094"))
        operands: list[ast.AST] = []
        if isinstance(node, ast.BinOp):
            operands = [node.left, node.right]
        elif isinstance(node, ast.AugAssign):
            operands = [node.target, node.value]
        if any(self._is_negative_literal(operand) for operand in operands) and (is_uint(left) or is_uint(right)):
            self.errors.append(diagnostic(node, "A negative literal cannot be used in unsigned arithmetic.", code="PY099"))

    @staticmethod
    def _numeric_result_type(left: str | None, right: str | None) -> str:
        if left == "literal_number":
            return right or "literal_number"
        return left or right or "literal_number"

    @staticmethod
    def _is_negative_literal(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.UnaryOp)
            and isinstance(node.op, ast.USub)
            and isinstance(node.operand, ast.Constant)
            and isinstance(node.operand.value, int)
            and not isinstance(node.operand.value, bool)
        )

    @staticmethod
    def _is_negative_expression(expr: Expr) -> bool:
        return (
            expr.kind == "unary"
            and expr.value == "-"
            and len(expr.args) == 1
            and expr.args[0].kind == "literal"
            and expr.args[0].type_name == "literal_number"
        )

    @staticmethod
    def _comparable(left: str | None, right: str | None, operator: str) -> bool:
        if left == right:
            if left in {"string", "bytes"}:
                return False
            return True
        if is_numeric(left) and is_numeric(right):
            return left == "literal_number" or right == "literal_number"
        return False

    def _check_assignable(self, expected: TypeRef, actual: Expr, node: ast.AST) -> None:
        expected_name = expected.name
        actual_name = actual.type_name
        valid = expected_name == actual_name
        if actual_name == "literal_number" and is_numeric(expected_name):
            valid = True
        if expected_name == "address" and actual_name == "literal_number" and actual.value == 0:
            valid = True
        if is_uint(expected_name) and (self._is_negative_literal(node) or self._is_negative_expression(actual)):
            valid = False
            self.errors.append(diagnostic(node, "A negative literal cannot be assigned to an unsigned integer.", code="PY100"))
        if not valid:
            self.errors.append(diagnostic(node, f"Type mismatch: expected `{expected.solidity()}`, got `{actual_name or 'unknown'}`.", code="PY095"))
        if expected_name in {"string", "bytes"} and actual_name == expected_name and actual.kind not in {"literal", "name", "state", "context"}:
            self.errors.append(diagnostic(node, "Dynamic bytes/string values can only be copied from an existing variable or state field in this MVP.", code="PY096"))

    def _body_uses_state_or_context(self, body: list[Statement]) -> bool:
        return any(expr.kind in {"state", "mapping_get", "mapping_target", "context", "native_balance"} for expr in self._walk_exprs(body))

    def _body_writes_state_or_calls(self, body: list[Statement]) -> bool:
        return any(
            item.kind in {"native_transfer", "emit"}
            or item.kind in {"assign", "augassign"} and item.target and item.target.kind in {"state", "mapping_target"}
            for item in self._walk_statements(body)
        )

    def _contains_value_return(self, body: list[Statement]) -> bool:
        return any(item.kind == "return" and item.expr is not None for item in self._walk_statements(body))

    @staticmethod
    def _always_returns(body: list[Statement]) -> bool:
        if not body:
            return False
        last = body[-1]
        if last.kind == "return":
            return True
        if last.kind == "if":
            return Parser._always_returns(last.body) and Parser._always_returns(last.otherwise)
        return False

    def _context_members(self, body: list[Statement]) -> set[str]:
        return {expr.value for expr in self._walk_exprs(body) if expr.kind == "context"}

    def _walk_statements(self, body: list[Statement]):
        for item in body:
            yield item
            yield from self._walk_statements(item.body)
            yield from self._walk_statements(item.otherwise)

    def _walk_exprs(self, body: list[Statement]):
        def visit(expr: Expr | None):
            if not expr:
                return
            yield expr
            for arg in expr.args:
                yield from visit(arg)

        for statement in self._walk_statements(body):
            yield from visit(statement.target)
            yield from visit(statement.expr)

    def _security_warnings(self, ir: ContractIR) -> list[dict[str, Any]]:
        warnings: list[dict[str, Any]] = []
        for function in ir.functions:
            if function.is_constructor or function.mutability in {"view", "pure"}:
                continue
            if any(stmt.kind in {"assign", "augassign"} and stmt.target and stmt.target.kind in {"state", "mapping_target"}
                   for stmt in self._walk_statements(function.body)):
                uses_sender = any(expr.kind == "context" and expr.value == "caller" for expr in self._walk_exprs(function.body))
                if not uses_sender:
                    warnings.append({
                        "line": function.line,
                        "column": 1,
                        "severity": "warning",
                        "code": "SEC001",
                        "message": f"`{function.name}` changes storage without using msg.sender. This may be intentional for permissionless actions; review the intended caller authorization and business rules.",
                    })
            if function.payable and not any(expr.kind == "context" and expr.value == "attached_value" for expr in self._walk_exprs(function.body)):
                warnings.append({
                    "line": function.line,
                    "column": 1,
                    "severity": "warning",
                    "code": "SEC002",
                    "message": f"`{function.name}` accepts ETH but does not read msg.value. Confirm that accepting unaccounted value is intended.",
                })
        if ir.uses_send_eth:
            warnings.append({
                "severity": "info",
                "code": "SEC003",
                "message": "send_eth uses a checked low-level call. PyChain adds a reentrancy lock to external state-changing functions in this contract; still review call ordering and withdrawal invariants.",
            })
        return warnings

    def _expressions_in(self, expr: Expr):
        yield expr
        for child in expr.args:
            yield from self._expressions_in(child)


def _expression_solidity(expr: Expr) -> str:
    if expr.kind == "literal":
        return json.dumps(expr.value, ensure_ascii=False) if isinstance(expr.value, str) else str(expr.value).lower() if isinstance(expr.value, bool) else str(expr.value)
    if expr.kind in {"name", "state"}:
        return str(expr.value)
    if expr.kind == "context":
        return {
            "caller": "msg.sender",
            "attached_value": "msg.value",
            "timestamp": "block.timestamp",
        }[expr.value]
    if expr.kind == "native_balance":
        return "address(this).balance"
    if expr.kind in {"mapping_get", "mapping_target"}:
        return f"{expr.value}[{_expression_solidity(expr.args[0])}]"
    if expr.kind == "binary" or expr.kind == "compare":
        return f"({_expression_solidity(expr.args[0])} {expr.value} {_expression_solidity(expr.args[1])})"
    if expr.kind == "unary":
        return f"({expr.value}{_expression_solidity(expr.args[0])})"
    if expr.kind == "boolop":
        return "(" + f" {expr.value} ".join(_expression_solidity(arg) for arg in expr.args) + ")"
    if expr.kind == "cast":
        return f"address({_expression_solidity(expr.args[0])})"
    raise ValueError(f"Cannot render expression kind {expr.kind}")


def _statement_solidity(statement: Statement, depth: int) -> list[str]:
    pad = "    " * depth
    line_comment = f"{pad}// PyChain source line {statement.line}"
    if statement.kind == "local_declare":
        assert statement.declared_type and statement.expr
        return [line_comment, f"{pad}{sol_type_with_location(statement.declared_type)} {statement.name} = {_expression_solidity(statement.expr)};"]
    if statement.kind == "assign":
        assert statement.target and statement.expr
        return [line_comment, f"{pad}{_expression_solidity(statement.target)} = {_expression_solidity(statement.expr)};"]
    if statement.kind == "augassign":
        assert statement.target and statement.expr and statement.operator
        return [line_comment, f"{pad}{_expression_solidity(statement.target)} {statement.operator} {_expression_solidity(statement.expr)};"]
    if statement.kind == "return":
        if statement.expr:
            return [line_comment, f"{pad}return {_expression_solidity(statement.expr)};"]
        return [line_comment, f"{pad}return;"]
    if statement.kind == "require":
        assert statement.expr and len(statement.expr.args) == 2
        return [line_comment, f"{pad}require({_expression_solidity(statement.expr.args[0])}, {_expression_solidity(statement.expr.args[1])});"]
    if statement.kind == "emit":
        assert statement.expr
        args = ", ".join(_expression_solidity(arg) for arg in statement.expr.args)
        return [line_comment, f"{pad}emit {statement.expr.value}({args});"]
    if statement.kind == "native_transfer":
        assert statement.expr and len(statement.expr.args) == 2
        return [line_comment, f"{pad}_pychainSend({_expression_solidity(statement.expr.args[0])}, {_expression_solidity(statement.expr.args[1])});"]
    if statement.kind == "if":
        assert statement.expr
        result = [line_comment, f"{pad}if ({_expression_solidity(statement.expr)}) {{"]
        if statement.body:
            for child in statement.body:
                result.extend(_statement_solidity(child, depth + 1))
        else:
            result.append(f"{pad}    // no operation")
        if statement.otherwise:
            result.append(f"{pad}}} else {{")
            for child in statement.otherwise:
                result.extend(_statement_solidity(child, depth + 1))
        result.append(f"{pad}}}")
        return result
    raise ValueError(f"Cannot render statement kind {statement.kind}")


def generate_solidity(ir: ContractIR) -> str:
    lines = ["// SPDX-License-Identifier: MIT", "pragma solidity ^0.8.24;", "", f"contract {ir.name} {{"]
    for item in ir.state:
        initial = f" = {_expression_solidity(item.initial)}" if item.initial else ""
        lines.append(f"    {item.type_ref.solidity()} public {item.name}{initial};")
    if ir.state:
        lines.append("")
    for event in ir.events:
        args = ", ".join(f"{typ.solidity()} {name}" for name, typ in event.args)
        lines.append(f"    event {event.name}({args});")
    if ir.events:
        lines.append("")
    if ir.uses_send_eth:
        lines.extend([
            "    bool private _pychainEntered;",
            "",
            "    modifier _pychainNonReentrant() {",
            '        require(!_pychainEntered, "reentrant call");',
            "        _pychainEntered = true;",
            "        _;",
            "        _pychainEntered = false;",
            "    }",
            "",
        ])
    for function in ir.functions:
        if function.is_constructor:
            header = f"    constructor({', '.join(f'{sol_type_with_location(typ)} {name}' for name, typ in function.args)})"
            if function.payable:
                header += " payable"
        else:
            args = ", ".join(f"{sol_type_with_location(typ)} {name}" for name, typ in function.args)
            header = f"    function {function.name}({args}) {function.visibility}"
            if function.mutability:
                header += f" {function.mutability}"
            elif function.payable:
                header += " payable"
            if function.returns:
                header += f" returns ({sol_type_with_location(function.returns)})"
            if ir.uses_send_eth and function.mutability not in {"view", "pure"}:
                header += " _pychainNonReentrant"
        lines.append(header + " {")
        if function.body:
            for statement in function.body:
                lines.extend(_statement_solidity(statement, 2))
        else:
            lines.append("        // no operation")
        lines.extend(["    }", ""])
    if ir.uses_send_eth:
        lines.extend([
            "    function _pychainSend(address recipient, uint256 amount) private {",
            '        (bool success, ) = payable(recipient).call{value: amount}("");',
            '        require(success, "ETH transfer failed");',
            "    }",
            "",
        ])
    lines.append("}")
    return "\n".join(lines) + "\n"


def _default_value(type_ref: TypeRef) -> str:
    if type_ref.is_mapping:
        raise ValueError("mapping argument cannot have a default")
    name = type_ref.name
    if name == "bool":
        return "false"
    if name == "address":
        return "ethers.ZeroAddress"
    if name == "string":
        return '""'
    if name == "bytes":
        return '"0x"'
    if name and name.startswith("bytes"):
        return "ethers.ZeroHash"
    return "0"


def generated_test(ir: ContractIR) -> str:
    constructor = next((function for function in ir.functions if function.is_constructor), None)
    args = ", ".join(_default_value(typ) for _, typ in constructor.args) if constructor else ""
    return f'''import {{ expect }} from "chai";
import {{ ethers }} from "hardhat";

describe("{ir.name}", function () {{
  it("deploys the generated contract", async function () {{
    const Factory = await ethers.getContractFactory("{ir.name}");
    const contract = await Factory.deploy({args});
    await contract.waitForDeployment();
    expect(await contract.getAddress()).not.to.equal(ethers.ZeroAddress);
  }});
}});
'''


def compile_source(source: str, target: str = "solidity") -> dict[str, Any]:
    if target != "solidity":
        raise CompileFailure([{
            "line": 1,
            "column": 1,
            "severity": "error",
            "code": "PY100",
            "message": f"The `{target}` backend is not implemented. Only Solidity / EVM is supported.",
        }])
    try:
        parser = Parser(source)
        ir, diagnostics = parser.parse()
        return {
            "solidity": generate_solidity(ir),
            "ir": ir.as_dict(),
            "diagnostics": diagnostics,
            "tests": generated_test(ir),
            "estimate": {
                "available": False,
                "gas": None,
                "note": "Gas estimation requires executing the generated artifact on a configured EVM node.",
            },
        }
    except RecursionError as exc:
        raise CompileFailure([{
            "line": 1,
            "column": 1,
            "severity": "error",
            "code": "PY102",
            "message": "The source is nested too deeply for the compiler. Simplify deeply nested expressions or conditionals.",
        }]) from exc
