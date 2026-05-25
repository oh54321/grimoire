from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Symbol:
    module: str          # path relative to root, e.g. "pkg/api.py"
    qualname: str        # "ping" or "Client.send"
    kind: str            # "function" | "class" | "method"
    signature: str       # "def ping(host: str)" / "class Client"
    doc_first_line: str
    mcp_tool: bool


def survey(root: Path, sub: str | None = None) -> tuple[list[Symbol], list[str]]:
    """Walk Python files under root, returning (symbols, skipped_files)."""
    base = root / sub if sub else root
    symbols: list[Symbol] = []
    skipped: list[str] = []
    for path in sorted(p for p in base.rglob("*") if p.is_file()):
        rel = str(path.relative_to(root))
        if path.suffix != ".py":
            skipped.append(rel)
            continue
        try:
            tree = ast.parse(path.read_text(errors="ignore"))
        except (SyntaxError, OSError):
            skipped.append(rel)
            continue
        symbols.extend(_module_symbols(tree, rel))
    return symbols, skipped


def _module_symbols(tree: ast.Module, rel: str) -> list[Symbol]:
    out: list[Symbol] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(_func(node, rel, node.name, "function"))
        elif isinstance(node, ast.ClassDef):
            out.append(Symbol(rel, node.name, "class", f"class {node.name}",
                              _doc(node), _is_tool(node)))
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append(_func(item, rel, f"{node.name}.{item.name}", "method"))
    return out


def _func(node: ast.FunctionDef | ast.AsyncFunctionDef, rel: str, qualname: str, kind: str) -> Symbol:
    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    sig = f"def {node.name}({ast.unparse(node.args)}){ret}"
    return Symbol(rel, qualname, kind, sig, _doc(node), _is_tool(node))


def _doc(node: ast.AST) -> str:
    doc = ast.get_docstring(node) or ""
    return doc.strip().splitlines()[0] if doc.strip() else ""


def _is_tool(node: ast.AST) -> bool:
    for dec in getattr(node, "decorator_list", []):
        target = dec.func if isinstance(dec, ast.Call) else dec
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if name == "tool":
            return True
    return False


def read_symbol(root: Path, rel_path: str, symbol: str | None = None) -> str:
    """Raw source of a file, or of one top-level symbol ('name' or 'Class.method').

    A single-symbol slice begins at the 'def'/'class' keyword; any decorators
    (e.g. @app.tool()) are not included.
    """
    path = root / rel_path
    text = path.read_text(errors="ignore")
    if symbol is None:
        return text
    tree = ast.parse(text)
    node = _find(tree, symbol.split("."))
    if node is None:
        raise KeyError(f"symbol not found: {symbol}")
    return ast.get_source_segment(text, node) or ""


def _find(scope: ast.AST, parts: list[str]) -> ast.AST | None:
    name, rest = parts[0], parts[1:]
    for node in getattr(scope, "body", []):
        if getattr(node, "name", None) == name:
            return _find(node, rest) if rest else node
    return None
