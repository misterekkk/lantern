"""Phase 8: code symbols (tree-sitter) and definition search ("def add_two(x, y):" -> skrypt.py line 200)."""

import pytest
from conftest import write

from lantern.chunking.code import chunk_code
from lantern.chunking.symbols import arity, extract_symbols, name_key
from lantern.indexer import run_queue
from lantern.scanner.diff import scan_root
from lantern.search.hybrid import search
from lantern.search.symbols import parse_symbol_query, symbol_search
from lantern.store import db

# language -> (source, expected (name, kind, line) subset)
SAMPLES = {
    "python": ("import x\n\n@cache\ndef add_two(x, y):\n    return x + y\n\n"
               "class A:\n    def m(self, z):\n        pass\n",
               {("add_two", "function", 4), ("A", "class", 7), ("m", "method", 8)}),
    "javascript": ("function addTwo(x, y) { return x + y; }\n"
                   "const sub = (a, b) => a - b;\nclass A { m() {} }\n",
                   {("addTwo", "function", 1), ("sub", "function", 2), ("A", "class", 3), ("m", "method", 3)}),
    "typescript": ("export function addTwo(x: number, y: number): number { return x + y; }\n"
                   "interface P { a: number }\n",
                   {("addTwo", "function", 1), ("P", "interface", 2)}),
    "go": ("package m\ntype P struct { A int }\nfunc AddTwo(x, y int) int { return x + y }\n"
           "func (p *P) Method(x int) int { return x }\n",
           {("P", "type", 2), ("AddTwo", "function", 3), ("Method", "method", 4)}),
    "rust": ("fn add_two(x: i32, y: i32) -> i32 { x + y }\nstruct P { a: i32 }\nimpl P { fn m(&self) {} }\n",
             {("add_two", "function", 1), ("P", "struct", 2), ("m", "method", 3)}),
    "java": ("class A {\n  A() {}\n  int addTwo(int x, int y) { return x + y; }\n}\n",
             {("A", "class", 1), ("A", "method", 2), ("addTwo", "method", 3)}),
    "csharp": ("namespace N { class A { int AddTwo(int x, int y) { return x + y; } } }\n",
               {("N", "module", 1), ("A", "class", 1), ("AddTwo", "method", 1)}),
    "c": ("static int *add_two(int x, int y) { return 0; }\nstruct P { int a; };\nvoid f(struct P *p) {}\n",
          {("add_two", "function", 1), ("P", "struct", 2), ("f", "function", 3)}),
    "cpp": ("int Foo::bar(int x) { return x; }\nnamespace ns { class P { public: void m(int a) {} }; }\n"
            "template<typename T> T add_two(T x, T y) { return x + y; }\n",
            {("bar", "function", 1), ("P", "class", 2), ("m", "method", 2), ("add_two", "function", 3)}),
    "swift": ("func addTwo(_ x: Int, _ y: Int) -> Int { x + y }\nclass P { func m(x: Int) -> Int { x } }\n",
              {("addTwo", "function", 1), ("P", "class", 2), ("m", "method", 2)}),
    "kotlin": ("fun addTwo(x: Int, y: Int): Int = x + y\nclass P { fun m(x: Int) = x }\n",
               {("addTwo", "function", 1), ("P", "class", 2), ("m", "method", 2)}),
    "objc": ("@implementation P\n- (int)addTwo:(int)x y:(int)y { return x + y; }\n@end\n",
             {("P", "class", 1), ("addTwo", "method", 2)}),
    "scala": ("object O { def addTwo(x: Int, y: Int): Int = x + y }\n", {("O", "class", 1), ("addTwo", "method", 1)}),
    "ruby": ("class A\n  def add_two(x, y)\n    x + y\n  end\nend\n", {("A", "class", 1), ("add_two", "method", 2)}),
    "php": ("<?php\nfunction add_two($x, $y) { return $x + $y; }\nclass A { public function m($a) {} }\n",
            {("add_two", "function", 2), ("A", "class", 3), ("m", "method", 3)}),
    "lua": ("local function add_two(x, y) return x + y end\n", {("add_two", "function", 1)}),
    "r": ("add_two <- function(x, y) { x + y }\n", {("add_two", "function", 1)}),
    "dart": ("int addTwo(int x, int y) => x + y;\nclass P { void m() {} }\n",
             {("addTwo", "function", 1), ("P", "class", 2), ("m", "method", 2)}),
    "elixir": ("defmodule M do\n  def add_two(x, y), do: x + y\nend\n",
               {("M", "module", 1), ("add_two", "function", 2)}),
    "haskell": ("addTwo :: Int -> Int -> Int\naddTwo x y = x + y\n", {("addTwo", "function", 2)}),
    "sql": ("CREATE FUNCTION add_two(x int, y int) RETURNS int AS $$ SELECT x + y $$ LANGUAGE sql;\n"
            "CREATE TABLE users (id int);\n", {("add_two", "function", 1), ("users", "table", 2)}),
    "bash": ("add_two() { echo 1; }\n", {("add_two", "function", 1)}),
}  # fmt: skip


@pytest.mark.parametrize("language", sorted(SAMPLES))
def test_symbols_in_every_supported_language(language):
    source, expected = SAMPLES[language]
    found = {(s.name, s.kind, s.line) for s in extract_symbols(source, language)}
    assert expected <= found, found


def test_signature_and_parent():
    syms = {s.name: s for s in extract_symbols("class A:\n    def m(self, z):\n        return z\n", "python")}
    assert syms["m"].signature == "def m(self, z)" and syms["m"].parent == "A"


@pytest.mark.parametrize(
    ("language", "source", "symbol"),
    [
        ("c", "static int *add_two(int x, int y) { return 0; }\n", "add_two"),  # was "int"
        ("cpp", "int Foo::bar(int x) { return x; }\n", "bar"),
        ("go", "package m\ntype P struct { A int }\n", "P"),  # was unnamed
    ],
)
def test_chunks_carry_the_real_name(language, source, symbol):
    assert symbol in {p.symbol for p in chunk_code(source, language)}


def test_name_key_and_arity():
    assert name_key("add_two") == name_key("addTwo") == name_key("AddTwo") == "addtwo"
    assert name_key("Foo::bar") == "bar"
    assert arity("def add_two(x, y)") == 2 and arity("f()") == 0 and arity("g(a, h(b, c))") == 2
    assert arity("Map<K, V> get(int i)") == 1 and arity("class A") is None


def test_parse_symbol_query():
    q = parse_symbol_query("def add_two(x, y):")
    assert (q.name, q.kind, q.arity, q.exact_only) == ("add_two", "function", 2, False)
    q = parse_symbol_query("Client::connect")
    assert (q.name, q.parent) == ("connect", "Client")
    assert parse_symbol_query("class Client").kind == "class"
    assert parse_symbol_query("pies").exact_only  # a plain word: exact names only
    assert parse_symbol_query("optymalizacja drzewa decyzyjnego") is None  # natural language
    assert parse_symbol_query("public static void main(String[] args)").name == "main"


@pytest.fixture
def code_project(tmp_path):
    root = tmp_path / "code"
    filler = "".join(f"# line {i}\n" for i in range(1, 200))
    write(root, "skrypt.py", filler + "def add_two(x, y):\n    return x + y\n")
    write(root, "uzycie.py", "from skrypt import add_two\n\nprint(add_two(1, 2))\n")  # usage, not a definition
    write(root, "math_utils.py", "def add_two_numbers(a, b, c):\n    return a + b + c\n")
    write(root, "lib/auth.ts", "export function parseUserToken(raw: string): string { return raw; }\n")
    write(root, "notes.md", "# Pies\n\nNotatka o psach.\n")
    return root


def test_definition_search_pinpoints_file_and_line(conn, code_project, embedder):
    scan_root(conn, code_project)
    run_queue(conn, embedder)

    top = symbol_search(conn, "def add_two(x, y):")[0]
    assert top.path.endswith("skrypt.py") and top.line == 200 and top.match == "exact"
    assert top.signature == "def add_two(x, y)" and top.chunk_id is not None
    assert symbol_search(conn, "addTwo")[0].line == 200  # style-insensitive
    assert [d.name for d in symbol_search(conn, "add_two")][:2] == ["add_two", "add_two_numbers"]  # exact, prefix
    assert symbol_search(conn, "UserToken")[0].name == "parseUserToken"  # fragment of a camelCase name
    assert symbol_search(conn, "pies") == []  # natural language: no definitions

    resp = search(conn, embedder, "def add_two(x, y):", log=True)
    assert resp.sections()[0][0] == "definitions" and resp.definitions[0].line == 200


def test_symbols_follow_the_file(conn, code_project, embedder):
    scan_root(conn, code_project)
    run_queue(conn, embedder)
    (code_project / "skrypt.py").write_text("def renamed(x, y):\n    return x + y\n", encoding="utf-8")
    scan_root(conn, code_project)
    run_queue(conn, embedder)
    assert symbol_search(conn, "renamed")[0].line == 1
    assert not [d for d in symbol_search(conn, "add_two") if d.path.endswith("skrypt.py")]

    (code_project / "math_utils.py").unlink()
    scan_root(conn, code_project)
    assert conn.execute("SELECT COUNT(*) FROM symbols WHERE name = 'add_two_numbers'").fetchone()[0] == 0
    with db.transaction(conn):
        db.remove_root(conn, conn.execute("SELECT id FROM roots").fetchone()[0])
    assert conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == 0


def test_open_numbers_follow_the_display_order(code_project, lantern_home):
    import os
    import subprocess
    import sys

    env = {**os.environ, "LANTERN_HOME": str(lantern_home), "LANTERN_EMBEDDER": "fake",
           "LANTERN_SOCKET": str(lantern_home / "none.sock")}  # fmt: skip

    def cli(*args):
        return subprocess.run([sys.executable, "-m", "lantern.cli", *args], capture_output=True, text=True, env=env)

    assert cli("index", str(code_project)).returncode == 0
    out = cli("search", "def add_two(x, y):")
    assert out.stdout.splitlines()[1] == "Definitions:" and "skrypt.py:200" in out.stdout
    assert cli("open", "1", "--print").stdout.strip().endswith("skrypt.py:200")
