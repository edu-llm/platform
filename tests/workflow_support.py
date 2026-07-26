"""Helpers shared by the GitHub Actions workflow test modules.

Not collected by pytest: the filename deliberately does not start with ``test_``.
"""

from __future__ import annotations

import ast
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_ROOT = PROJECT_ROOT / ".github" / "workflows"
STEP_SCRIPT_TIMEOUT_SECONDS = 30


class GitHubActionsLoader(yaml.SafeLoader):
    """Parse YAML booleans without treating the GitHub Actions `on` key as true."""


GitHubActionsLoader.yaml_implicit_resolvers = {
    key: list(resolvers) for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
for resolver_key, resolvers in GitHubActionsLoader.yaml_implicit_resolvers.items():
    GitHubActionsLoader.yaml_implicit_resolvers[resolver_key] = [
        resolver for resolver in resolvers if resolver[0] != "tag:yaml.org,2002:bool"
    ]
GitHubActionsLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$", re.IGNORECASE),
    list("tTfF"),
)


def load_workflow(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"required file is missing: {path}"
    loaded = yaml.load(path.read_text(encoding="utf-8"), Loader=GitHubActionsLoader)
    assert isinstance(loaded, dict)
    return loaded


def only_job(workflow: dict[str, Any]) -> dict[str, Any]:
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    assert len(jobs) == 1
    job = next(iter(jobs.values()))
    assert isinstance(job, dict)
    return job


def step(job: dict[str, Any], name: str) -> dict[str, Any]:
    matching = [candidate for candidate in job["steps"] if candidate.get("name") == name]
    assert len(matching) == 1
    return matching[0]


def shell_syntax_without_heredoc_bodies(script: str) -> str:
    heredoc_pattern = re.compile(r"<<-?\s*(?:'([^']+)'|\"([^\"]+)\"|([a-zA-Z_][a-zA-Z0-9_]*))")
    shell_lines = []
    delimiter: str | None = None

    for line in script.splitlines():
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
            continue

        shell_lines.append(line)
        match = heredoc_pattern.search(line)
        if match is not None:
            delimiter = next(group for group in match.groups() if group is not None)

    assert delimiter is None, "unterminated shell heredoc"
    return "\n".join(shell_lines)


def aws_word_count(script: str) -> int:
    shell_syntax = shell_syntax_without_heredoc_bodies(script)
    return len(re.findall(r"(?<![a-zA-Z0-9_-])aws(?=\s)", shell_syntax))


def aws_commands(script: str) -> list[list[str]]:
    normalized = re.sub(r"\\\s*\n", " ", script)
    commands = []
    for line in normalized.splitlines():
        tokens = shlex.split(line)
        if tokens[:1] == ["aws"]:
            commands.append(tokens)
    assert aws_word_count(normalized) == len(commands), (
        "every aws invocation must be an explicit top-level command"
    )
    return commands


def command_tokens(script: str, service: str, operation: str) -> list[str]:
    matching = [
        command for command in aws_commands(script) if command[:3] == ["aws", service, operation]
    ]
    assert len(matching) == 1, f"expected exactly one aws {service} {operation} command"
    return matching[0]


def run_step_script(
    script: str,
    *,
    cwd: Path,
    env: dict[str, str],
    stub_bin: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute a workflow ``run`` body the way the Actions runner executes it.

    GitHub runs an unqualified ``run`` body as ``bash -e <file>`` on Linux runners, so the
    script's own ``set`` line is what supplies the rest of the strictness. The environment
    is replaced rather than extended so a test cannot pass because of an inherited value.
    """
    script_path = cwd / "step.sh"
    script_path.write_text(script, encoding="utf-8")
    search_path = os.defpath if stub_bin is None else f"{stub_bin}{os.pathsep}{os.defpath}"
    return subprocess.run(
        ["bash", "-e", str(script_path)],
        cwd=cwd,
        env={"PATH": search_path, **env},
        check=False,
        capture_output=True,
        text=True,
        timeout=STEP_SCRIPT_TIMEOUT_SECONDS,
    )


def write_stub(directory: Path, name: str, body: str) -> Path:
    """Install an executable stub so a step script can run without its real tooling."""
    directory.mkdir(parents=True, exist_ok=True)
    stub = directory / name
    stub.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{body}", encoding="utf-8")
    stub.chmod(0o755)
    return stub


def literal_assignment(source: str, name: str) -> object:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"missing literal assignment: {name}")
