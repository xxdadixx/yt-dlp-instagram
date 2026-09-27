"""
tests/test_code_integrity.py - Automated CPython Symbol Table & Module Import Integrity Suite.
Leverages Python's native symtable compilation engine to detect genuine unbound variables,
unimported module references, and syntax anomalies across all project modules.
"""

from __future__ import annotations

import builtins
import importlib
import os
import symtable
import sys
import unittest
from pathlib import Path

# Standard module attributes injected into every module namespace by Python's loader
MODULE_SPECIAL_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "__file__",
        "__name__",
        "__doc__",
        "__package__",
        "__loader__",
        "__spec__",
        "__path__",
        "__annotations__",
        "__builtins__",
        "__all__",
    }
)

# Compiler sentinels and platform-specific standard aliases
COMPILER_SENTINELS: frozenset[str] = frozenset(
    {
        "__class__",  # Implicit cell created by CPython for zero-argument super()
        "WindowsError",  # Standard Windows alias for OSError
    }
)


class CodeIntegrityTests(unittest.TestCase):
    """Verifies that all project source files pass module import verification and lexical symbol checks."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.project_root = Path(__file__).resolve().parent.parent
        cls.target_dirs = ["config", "core", "gui", "utils"]
        if str(cls.project_root) not in sys.path:
            sys.path.insert(0, str(cls.project_root))

    def _get_project_py_files(self) -> list[Path]:
        files: list[Path] = []
        for target_dir in self.target_dirs:
            dir_path = self.project_root / target_dir
            if dir_path.is_dir():
                files.extend(dir_path.rglob("*.py"))
        root_main = self.project_root / "main.py"
        if root_main.exists():
            files.append(root_main)
        return files

    def test_module_import_sanity(self) -> None:
        """Verifies that every application module imports cleanly with no NameError, ImportError, or SyntaxError."""
        py_files = self._get_project_py_files()
        self.assertGreater(len(py_files), 0, "No Python source files discovered.")

        import_failures: list[str] = []

        for file_path in py_files:
            rel_path = file_path.relative_to(self.project_root)
            parts = list(rel_path.with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            if not parts:
                continue

            module_name = ".".join(parts)
            try:
                importlib.import_module(module_name)
            except Exception as exc:
                import_failures.append(
                    f"{module_name} ({rel_path}): {type(exc).__name__}: {exc}"
                )

        self.assertEqual(
            len(import_failures),
            0,
            "Module import sanity failures detected:\n" + "\n".join(import_failures),
        )

    def test_ast_unbound_symbols_in_functions(self) -> None:
        """Parses all project files via CPython symtable to detect genuine unimported/unbound references

        while correctly honoring LEGB scopes, closures, comprehensions, and compound unpacking targets.
        """
        py_files = self._get_project_py_files()
        builtin_names = set(dir(builtins))
        unbound_errors: list[str] = []

        for file_path in py_files:
            rel_path = file_path.relative_to(self.project_root)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    code = f.read()
                mod_table = symtable.symtable(code, str(file_path), "exec")
            except SyntaxError as syn_err:
                unbound_errors.append(f"SyntaxError in {rel_path}: {syn_err}")
                continue

            # Collect all identifiers bound at module scope (imports, assignments, class/def declarations)
            defined_module_globals: set[str] = set()
            for sym in mod_table.get_symbols():
                if sym.is_assigned() or sym.is_imported() or sym.is_declared_global():
                    defined_module_globals.add(sym.get_name())

            valid_globals = (
                defined_module_globals
                | builtin_names
                | MODULE_SPECIAL_ATTRIBUTES
                | COMPILER_SENTINELS
            )

            # Check module-level referenced expressions
            for sym in mod_table.get_symbols():
                if sym.is_referenced() and not (sym.is_assigned() or sym.is_imported()):
                    name = sym.get_name()
                    if name not in valid_globals:
                        unbound_errors.append(
                            f"Unbound symbol '{name}' at module level in {rel_path}"
                        )

            def check_scope(table: symtable.SymbolTable) -> None:
                tbl_type = table.get_type()

                if tbl_type == "function":
                    for sym in table.get_symbols():
                        if not sym.is_referenced():
                            continue
                        name = sym.get_name()
                        # Local symbols: parameters, assignments, local imports, unpacking targets
                        if sym.is_local():
                            continue
                        # Free symbols: closures resolved from enclosing outer functions
                        if sym.is_free():
                            continue
                        # Global symbols: looked up in module or built-in namespace
                        if sym.is_global() and name in valid_globals:
                            continue
                        if name in valid_globals:
                            continue
                        unbound_errors.append(
                            f"Unbound symbol '{name}' in {rel_path}::{table.get_name()} (line {table.get_lineno()})"
                        )

                elif tbl_type == "class":
                    class_symbols = set(table.get_identifiers())
                    for sym in table.get_symbols():
                        if not sym.is_referenced():
                            continue
                        name = sym.get_name()
                        if sym.is_local() or sym.is_assigned():
                            continue
                        if name in class_symbols:
                            continue
                        if name in valid_globals:
                            continue
                        unbound_errors.append(
                            f"Unbound symbol '{name}' in class {rel_path}::{table.get_name()} (line {table.get_lineno()})"
                        )

                # Recursively walk child tables (nested functions, methods, closures, comprehensions)
                for child in table.get_children():
                    check_scope(child)

            for child_table in mod_table.get_children():
                check_scope(child_table)

        self.assertEqual(
            len(unbound_errors),
            0,
            "Unbound variables / missing imports detected:\n"
            + "\n".join(unbound_errors),
        )


if __name__ == "__main__":
    unittest.main()
