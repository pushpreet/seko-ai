"""Agent harness definitions shared across the control plane.

A *harness* is the coding agent a user drives inside a workspace. The workspace image ships
several interchangeable harnesses on ``PATH`` (all pointed at the same LiteLLM endpoint); the
chosen harness is stored per :class:`~seko_ai.models.Workspace` and only decides which binary
the connect hint / self-host kit launches.
"""

from __future__ import annotations

# Map each selectable harness label to the binary that launches it inside the workspace.
HARNESS_BINARIES: dict[str, str] = {
    "pi": "pi",
    "oh-my-pi": "omp",
}

DEFAULT_HARNESS = "pi"

# (value, human label) pairs for rendering the <select> in create forms.
HARNESS_CHOICES: list[tuple[str, str]] = [
    ("pi", "pi"),
    ("oh-my-pi", "oh-my-pi (omp)"),
]


def normalize_harness(value: str | None) -> str:
    """Return a known harness label, falling back to the default for unknown input."""
    candidate = (value or "").strip()
    return candidate if candidate in HARNESS_BINARIES else DEFAULT_HARNESS


def harness_binary(harness: str | None) -> str:
    """Return the launch binary for a harness label (default harness if unknown)."""
    return HARNESS_BINARIES.get(normalize_harness(harness), HARNESS_BINARIES[DEFAULT_HARNESS])
