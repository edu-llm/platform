"""Plumbing shared by the Phase 1 image-build command line tools.

The build workflow runs these tools on a GitHub Actions runner, so every failure has to
collapse to a short machine-readable token. Nothing here may echo a filesystem path, a
subprocess stream, or an environment value: the runner log is world readable for any
public caller repository.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import yaml
from pydantic import ValidationError

from .config import load_yaml
from .contracts.repository_registry import RepositoryRegistry

STEP_OUTPUT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class RegistryUnreadableError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class UnsafeStepOutputError(ValueError):
    """Raised when a value would smuggle extra lines into ``GITHUB_OUTPUT``."""


def load_registry(path: Path) -> RepositoryRegistry:
    try:
        return load_yaml(path, RepositoryRegistry)
    # UnicodeDecodeError is a ValueError, so it would otherwise fall through every branch
    # below and reach the runner log as a traceback naming the full path.
    except (OSError, UnicodeDecodeError) as exc:
        raise RegistryUnreadableError("registry_unreadable") from exc
    except (yaml.YAMLError, TypeError, ValidationError) as exc:
        raise RegistryUnreadableError("registry_invalid") from exc


def require_output_safe(name: str, value: str) -> str:
    if STEP_OUTPUT_NAME_PATTERN.fullmatch(name) is None:
        raise UnsafeStepOutputError("step output names must be lowercase identifiers")
    if any(not character.isprintable() for character in value):
        raise UnsafeStepOutputError("step output values must contain only printable characters")
    return value


def append_step_outputs(path: Path, pairs: Sequence[tuple[str, str]]) -> None:
    """Append ``key=value`` lines, validating every pair before touching the file."""
    rendered = "".join(f"{name}={require_output_safe(name, value)}\n" for name, value in pairs)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(rendered)
