"""Backward-compatibility shim — imports moved to core.registry and core.router."""

from core.registry import ToolExecutionResult, ToolRegistry, load_prompt_files, _sanitize_untrusted  # noqa: F401
from core.router import GrugRouter  # noqa: F401
