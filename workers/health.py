"""Structured worker health reporting.

Workers expose ``health_check()`` returning a ``WorkerHealth`` so the monitor
and circuit breaker can read a boolean directly instead of grepping a free-form
string.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerHealth:
    healthy: bool
    status: str  # short human-readable phrase
