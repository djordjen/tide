"""TIDE's metadata compiler and command-line interface."""

from tide.compiler.compiler import compile_project
from tide.compiler.normalized import ApplicationModel
from tide.diagnostics import CompilationFailed, Diagnostic

__all__ = ["ApplicationModel", "CompilationFailed", "Diagnostic", "compile_project"]
__version__ = "0.1.0"

