"""Code chunking by functions and classes (tree-sitter), with window fallback for the rest of the file."""

from lantern.chunking.symbols import WRAPPER_TYPES, definition_name, definition_node, is_definition, parser
from lantern.chunking.text import WINDOW_CHARS, Piece, windows


def _body(node):
    node = definition_node(node)
    return node.child_by_field_name("body") or node


def chunk_code(text: str, language: str) -> list[Piece]:
    """Definitions that fit become one chunk; big ones are split into their member definitions; the rest of the
    file (imports, top-level statements) goes into line windows."""
    try:
        tree = parser(language).parse(text.encode("utf-8"))
    except Exception:  # unknown grammar or parser failure: plain windows
        return windows(text.split("\n"))

    lines = text.split("\n")
    pieces: list[Piece] = []
    covered: list[tuple[int, int]] = []  # 0-based inclusive line spans

    def emit(node, symbol: str | None) -> None:
        start, end = node.start_point[0], node.end_point[0]
        body = "\n".join(lines[start : end + 1])
        if len(body) <= WINDOW_CHARS * 1.5:
            pieces.append(Piece(body.strip(), start + 1, end + 1, symbol))
        else:
            pieces.extend(windows(lines[start : end + 1], first_line=start + 1, symbol=symbol))
        covered.append((start, end))

    def visit(node, prefix: str) -> None:
        for child in node.children:
            if child.type in WRAPPER_TYPES:
                visit(child, prefix)
                continue
            if not is_definition(child, language):
                continue
            name = definition_name(child)
            symbol = f"{prefix}{name}" if name else (prefix.rstrip(".") or None)
            size = child.end_byte - child.start_byte
            if size <= WINDOW_CHARS * 1.5:
                emit(child, symbol)
                continue
            covered_before = len(covered)
            visit(_body(child), f"{symbol}." if symbol else prefix)
            inner = covered[covered_before:]
            if not inner:  # big definition without nested definitions
                emit(child, symbol)
                continue
            # Keep the header (signature, docstring, fields before the first member) under the parent's symbol;
            # gaps between members are picked up by the uncovered-region pass below.
            start = child.start_point[0]
            inner_start = min(s for s, _ in inner)
            if inner_start > start and "\n".join(lines[start:inner_start]).strip():
                pieces.extend(windows(lines[start:inner_start], first_line=start + 1, symbol=symbol))
                covered.append((start, inner_start - 1))

    visit(tree.root_node, "")

    # Uncovered regions (imports, module-level code, comments) as plain windows.
    taken = [False] * len(lines)
    for start, end in covered:
        for i in range(start, min(end, len(lines) - 1) + 1):
            taken[i] = True
    i = 0
    while i < len(lines):
        if taken[i]:
            i += 1
            continue
        j = i
        while j < len(lines) and not taken[j]:
            j += 1
        if "\n".join(lines[i:j]).strip():
            pieces.extend(windows(lines[i:j], first_line=i + 1))
        i = j
    pieces.sort(key=lambda p: (p.start_line, p.end_line))
    return pieces
