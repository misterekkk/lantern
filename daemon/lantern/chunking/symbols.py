"""Code symbols (functions, classes, methods, types) with their exact line, from the tree-sitter syntax tree.

Shared by the chunker (chunk titles) and the symbol table used for "where is `def add_two(x, y)` defined" search.
Grammars disagree on where a definition keeps its name, so name lookup has a few per-grammar rules (C/C++
declarators, Go type specs, R assignments, Elixir `def` calls, JS arrow functions assigned to a name).
"""

import re
from dataclasses import dataclass
from functools import cache


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str  # function | method | class | struct | interface | enum | trait | module | type | table
    line: int  # 1-based line of the definition (the `def` line, not its decorators)
    end_line: int
    signature: str  # definition header, whitespace collapsed
    parent: str | None = None  # enclosing class/module


# Node types that define something named, across the supported grammars.
DEFINITION_TYPES = frozenset(
    {
        "function_definition",
        "class_definition",
        "decorated_definition",  # python, c/c++ functions, scala
        "function_declaration",
        "generator_function_declaration",
        "class_declaration",
        "method_definition",
        "interface_declaration",
        "type_alias_declaration",
        "enum_declaration",
        "abstract_class_declaration",
        "method_declaration",
        "constructor_declaration",
        "record_declaration",
        "namespace_declaration",
        "struct_declaration",  # java, c#
        "function_item",
        "impl_item",
        "struct_item",
        "enum_item",
        "trait_item",
        "mod_item",  # rust
        "type_declaration",  # go
        "class_specifier",
        "struct_specifier",
        "enum_specifier",
        "namespace_definition",  # c/c++
        "protocol_declaration",
        "extension_declaration",
        "init_declaration",  # swift
        "object_declaration",  # kotlin
        "object_definition",
        "trait_definition",  # scala
        "class_implementation",
        "class_interface",  # objc
        "method",
        "class",
        "module",
        "singleton_method",  # ruby
        "create_function",
        "create_table",
        "create_view",
        "create_procedure",  # sql
    }
)
# Wrappers whose children carry the actual definition (export statements, templates).
WRAPPER_TYPES = frozenset({"export_statement", "template_declaration"})
# C-family specifiers are also used as plain type references (`struct P *p`): only those with a body define.
_NEEDS_BODY = frozenset({"class_specifier", "struct_specifier", "enum_specifier"})
_NAME_NODES = frozenset(
    {"identifier", "type_identifier", "simple_identifier", "constant", "field_identifier", "word", "name"}
)
_FUNCTION_VALUES = frozenset({"arrow_function", "function_expression", "function", "generator_function"})
_ELIXIR_DEFS = frozenset({"def", "defp", "defmacro", "defmacrop", "defmodule", "defprotocol", "defimpl"})
_BODY_TYPES = frozenset(
    {
        "block",
        "compound_statement",
        "class_body",
        "declaration_list",
        "field_declaration_list",
        "function_body",
        "template_body",
        "do_block",
        "braced_expression",
        "statement_list",
        "implementation_definition",
        "enum_variant_list",
        "constructor_body",
        "body_statement",
    }
)


@cache
def parser(language: str):
    from tree_sitter_language_pack import get_parser

    return get_parser(language)


def _text(node) -> str:
    return node.text.decode("utf-8", "replace")


def _declarator_name(node):
    """C/C++: `int *Foo::bar(int x)` keeps the name inside nested declarators. Only a real function declarator
    (name + parameter list) counts: macros like `NAMESPACE_BEGIN namespace x {` also parse as function_definition."""
    while node is not None and node.type != "function_declarator":
        node = node.child_by_field_name("declarator")
    if node is None:
        return None
    name = node.child_by_field_name("declarator")
    if name is not None and name.type == "qualified_identifier":
        name = name.child_by_field_name("name") or name
    if name is None or name.type not in {"identifier", "field_identifier", "destructor_name", "operator_name"}:
        return None
    return name


def definition_node(node):
    """The node that holds the name/signature (python decorators wrap the real definition)."""
    if node.type == "decorated_definition":
        return node.child_by_field_name("definition") or node
    return node


def definition_name(node) -> str | None:
    target = definition_node(node)
    if target.type == "function_definition" and target.child_by_field_name("declarator") is not None:
        name = _declarator_name(target.child_by_field_name("declarator"))  # c/c++
    elif target.type == "type_declaration":  # go: type_declaration > type_spec(name)
        spec = next((c for c in target.named_children if c.type in {"type_spec", "type_alias"}), None)
        name = spec.child_by_field_name("name") if spec is not None else None
    elif target.type.startswith("create_"):  # sql: CREATE FUNCTION/TABLE <object_reference>
        ref = next((c for c in target.named_children if c.type == "object_reference"), None)
        name = (ref.child_by_field_name("name") or ref) if ref is not None else None
    else:
        name = target.child_by_field_name("name")
        if name is None:
            for child in target.named_children:  # e.g. rust `impl Foo`, objc `@implementation P`
                if child.type in _NAME_NODES or child.type == "dot_index_expression":
                    name = child
                    break
            else:
                name = target.child_by_field_name("type")
    if name is None:
        return None
    text = _text(name)
    return text if len(text) <= 200 and "\n" not in text else None


def is_definition(node, language: str) -> bool:
    if language == "r" and node.type == "function_definition":  # anonymous; named via the assignment
        return False
    if node.type in _NEEDS_BODY:
        return node.child_by_field_name("body") is not None
    return node.type in DEFINITION_TYPES


def _extra_definition(node, language: str) -> tuple[str, str] | None:
    """Definitions that grammars express as assignments or calls: (name, kind)."""
    t = node.type
    if t == "variable_declarator" or t in {"public_field_definition", "field_definition"}:  # js/ts
        value = node.child_by_field_name("value")
        name = node.child_by_field_name("name") or node.child_by_field_name("property")
        if value is not None and value.type in _FUNCTION_VALUES and name is not None:
            return _text(name), "function"
    elif t == "binary_operator" and language == "r":  # add_two <- function(x, y) ...
        lhs, rhs = node.child_by_field_name("lhs"), node.child_by_field_name("rhs")
        if lhs is not None and rhs is not None and rhs.type == "function_definition":
            return _text(lhs), "function"
    elif t == "call" and language == "elixir":
        target = node.child_by_field_name("target")
        if target is not None and _text(target) in _ELIXIR_DEFS:
            args = next((c for c in node.named_children if c.type == "arguments"), None)
            first = args.named_children[0] if args is not None and args.named_children else None
            if first is not None and first.type == "call":
                first = first.child_by_field_name("target") or first
            if first is not None:
                module = _text(target) in {"defmodule", "defprotocol", "defimpl"}
                return _text(first), "module" if module else "function"
    elif (t, language) in {("function", "haskell"), ("function_signature", "dart")}:
        name = node.child_by_field_name("name")
        if name is not None:
            return _text(name), "function"
    return None


_CONTAINERS = frozenset({"class", "struct", "interface", "trait", "enum"})


def _kind(node_type: str, parent_kind: str | None) -> str:
    if "constructor" in node_type or node_type == "init_declaration":
        return "method"
    for word, kind in (
        ("interface", "interface"),
        ("protocol", "interface"),
        ("trait", "trait"),
        ("enum", "enum"),
        ("struct", "struct"),
        ("record", "struct"),
        ("impl", "class"),
        ("class", "class"),
        ("object", "class"),
        ("extension", "class"),
        ("module", "module"),
        ("mod_item", "module"),
        ("namespace", "module"),
        ("table", "table"),
        ("view", "table"),
        ("type", "type"),
    ):
        if word in node_type:
            return kind
    container = parent_kind in _CONTAINERS
    return "method" if container or node_type in {"method", "method_declaration", "method_definition"} else "function"


def _signature(node, text_bytes: bytes) -> str:
    body = node.child_by_field_name("body")
    if body is None:
        body = next((c for c in node.named_children if c.type in _BODY_TYPES), None)
    if body is not None and body.start_byte > node.start_byte:
        header = text_bytes[node.start_byte : body.start_byte].decode("utf-8", "replace")
    else:  # no recognisable body (one-liners, assignments): the first line
        header = text_bytes[node.start_byte : node.end_byte].decode("utf-8", "replace").split("\n", 1)[0]
    header = " ".join(_until_body(header).split()).rstrip(" {:=")
    return header[:200]


def _until_body(header: str) -> str:
    """Cut at the first `{` outside parentheses: when error recovery (macros) hides the body node, the header would
    otherwise run into the body. Braces inside parameters (`def f(x={})`) are kept."""
    depth = 0
    for i, ch in enumerate(header):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif ch == "{" and depth == 0 and i > 0:
            return header[:i]
    return header


def extract_symbols(text: str, language: str) -> list[Symbol]:
    try:
        tree = parser(language).parse(text.encode("utf-8"))
    except Exception:  # unknown grammar or parser failure
        return []
    data = text.encode("utf-8")
    out: list[Symbol] = []

    def visit(node, parent: str | None, parent_kind: str | None, depth: int) -> None:
        if depth > 60:  # pathological nesting (generated code)
            return
        for child in node.named_children:
            name, kind, holder = None, None, child
            if is_definition(child, language):
                holder = definition_node(child)
                name = definition_name(child)
                kind = _kind(holder.type, parent_kind)
            else:
                extra = _extra_definition(child, language)
                if extra is not None:
                    name, kind = extra
                    if kind == "function" and parent_kind in _CONTAINERS:
                        kind = "method"
            if name:
                line = holder.start_point[0] + 1
                out.append(Symbol(name, kind, line, holder.end_point[0] + 1, _signature(holder, data), parent))
                visit(holder, name, kind, depth + 1)
            else:
                visit(child, parent, parent_kind, depth + 1)

    visit(tree.root_node, None, None, 0)
    return out


_IDENT_SPLIT = re.compile(r"[_\-$]+")


def name_key(name: str) -> str:
    """Style-insensitive key: add_two, addTwo and AddTwo all become "addtwo"."""
    return _IDENT_SPLIT.sub("", name.rsplit("::", 1)[-1].rsplit(".", 1)[-1]).lower()


def arity(signature: str) -> int | None:
    """Number of parameters in the first (...) of a signature or query; None without parentheses."""
    start = signature.find("(")
    if start < 0:
        return None
    depth, count, seen = 0, 0, False
    for ch in signature[start:]:
        if ch in "([{<":
            depth += 1
            continue
        if ch in ")]}>":
            depth -= 1
            if depth == 0:
                break
            continue
        if depth == 1:
            if ch == ",":
                count += 1
            elif not ch.isspace():
                seen = True
    return count + 1 if seen else 0
