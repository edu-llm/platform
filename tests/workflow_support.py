"""Helpers shared by the GitHub Actions workflow test modules.

Not collected by pytest: the filename deliberately does not start with ``test_``.
"""

from __future__ import annotations

import ast
import os
import re
import shlex
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
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
        # SAY WHICH LINE, BECAUSE shlex's OWN MESSAGE NAMES NOTHING. An apostrophe in a
        # comment -- "the run's prefix" -- is an unclosed single quote to the lexer, and
        # what a reader gets is `ValueError: No closing quotation` with no file, no line and
        # no hint that a comment they wrote in prose is the cause. This has cost three
        # separate debugging sessions on three different workflows.
        try:
            tokens = shlex.split(line)
        except ValueError as unbalanced:
            raise ValueError(
                f"{unbalanced} while reading this line of a workflow script:\n"
                f"    {line.strip()}\n"
                "An apostrophe inside a shell comment reads as an unclosed quote here. "
                "Reword it -- 'the prefix of a run' rather than 'the run's prefix'."
            ) from None
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


# GitHub resolves an unknown property on a known context to the empty string instead of
# failing the run, so a plausible-looking typo is indistinguishable from a real property
# until something downstream quietly misbehaves. These sets are the documented contents of
# the fixed contexts; the workflow itself supplies the contents of the rest.
GITHUB_CONTEXT_PROPERTIES = frozenset(
    {
        "action",
        "action_path",
        "action_ref",
        "action_repository",
        "action_status",
        "actor",
        "actor_id",
        "api_url",
        "base_ref",
        "env",
        "event",
        "event_name",
        "event_path",
        "graphql_url",
        "head_ref",
        "job",
        "path",
        "ref",
        "ref_name",
        "ref_protected",
        "ref_type",
        "repository",
        "repository_id",
        "repository_owner",
        "repository_owner_id",
        "repositoryUrl",
        "retention_days",
        "run_attempt",
        "run_id",
        "run_number",
        "secret_source",
        "server_url",
        "sha",
        "token",
        "triggering_actor",
        "workflow",
        "workflow_ref",
        "workflow_sha",
        "workspace",
    }
)
JOB_CONTEXT_PROPERTIES = frozenset(
    {
        "check_run_id",
        "container",
        "services",
        "status",
        "workflow_file_path",
        "workflow_ref",
        "workflow_repository",
        "workflow_sha",
    }
)
RUNNER_CONTEXT_PROPERTIES = frozenset(
    {"arch", "debug", "environment", "name", "os", "temp", "tool_cache"}
)
STRATEGY_CONTEXT_PROPERTIES = frozenset({"fail-fast", "job-index", "job-total", "max-parallel"})
FREE_FORM_CONTEXTS = frozenset({"env", "matrix", "secrets", "vars"})
EXPRESSION_FUNCTIONS = frozenset(
    {
        "always",
        "cancelled",
        "contains",
        "endswith",
        "failure",
        "format",
        "fromjson",
        "hashfiles",
        "join",
        "startswith",
        "success",
        "tojson",
    }
)
EXPRESSION_LITERALS = frozenset({"false", "null", "true"})
FREE_FORM_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
EXPRESSION_PATTERN = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
STEP_OUTPUT_WRITE_PATTERN = re.compile(
    r'echo\s+"(?P<name>[A-Za-z_][A-Za-z0-9_-]*)=[^"]*"\s*>>\s*"\$\{GITHUB_OUTPUT\}"'
)
QUOTED_LITERAL_PATTERN = re.compile(r"'(?:[^']|'')*'")
PROPERTY_CHAIN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.$-])([A-Za-z_][A-Za-z0-9_-]*)((?:\.[A-Za-z0-9_*-]+)*)"
)


@dataclass(frozen=True)
class ExpressionScope:
    """Where an expression sits, and therefore which names it is allowed to reach."""

    where: str
    inputs: frozenset[str]
    job_ids: frozenset[str]
    needs: frozenset[str]
    step_ids: frozenset[str]
    job_outputs: Mapping[str, frozenset[str]]
    step_outputs: Mapping[str, frozenset[str]]
    allows_jobs_context: bool


def _iter_expressions(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            # GitHub evaluates an `if:` as an expression whether or not it is braced, so a
            # reader that only looks inside `${{ }}` leaves every gate in the file unread.
            if key == "if" and isinstance(value, str):
                braced = EXPRESSION_PATTERN.findall(value)
                yield from braced if braced else [value]
            else:
                yield from _iter_expressions(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_expressions(item)
    elif node is not None:
        yield from EXPRESSION_PATTERN.findall(str(node))


def _as_set(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, str):
        return frozenset({value})
    if isinstance(value, dict):
        return frozenset(str(key) for key in value)
    if isinstance(value, list):
        return frozenset(str(item) for item in value)
    return frozenset()


def _declared_inputs(workflow: Mapping[str, Any]) -> frozenset[str]:
    triggers = workflow.get("on")
    if not isinstance(triggers, dict):
        return frozenset()
    declared: set[str] = set()
    for trigger in ("workflow_call", "workflow_dispatch"):
        definition = triggers.get(trigger)
        if isinstance(definition, dict):
            declared |= _as_set(definition.get("inputs"))
    return frozenset(declared)


def _job_outputs(workflow: Mapping[str, Any]) -> dict[str, frozenset[str]]:
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        return {}
    return {str(name): _as_set(job.get("outputs")) for name, job in jobs.items()}


def _step_outputs(
    steps: list[Any],
    declared: Mapping[str, Sequence[str]],
) -> dict[str, frozenset[str]]:
    """What each step in a job can be shown to put on GITHUB_OUTPUT.

    A shell-written output is readable straight out of the run body. An output written by
    an action or by a platform CLI is not, so the caller declares it; that declaration is
    what makes a rename on either side visible instead of resolving to the empty string.
    """
    outputs: dict[str, frozenset[str]] = {}
    for item in steps:
        if not isinstance(item, dict) or "id" not in item:
            continue
        identifier = str(item["id"])
        written = set(STEP_OUTPUT_WRITE_PATTERN.findall(str(item.get("run", ""))))
        outputs[identifier] = frozenset(written | set(declared.get(identifier, ())))
    return outputs


def _validate_chain(context: str, segments: list[str], scope: ExpressionScope) -> str | None:
    """Return a rejection reason, or ``None`` when the reference is real."""
    if context in FREE_FORM_CONTEXTS:
        if len(segments) != 1 or FREE_FORM_NAME_PATTERN.fullmatch(segments[0]) is None:
            return f"{context} takes exactly one variable name"
        return None
    if context == "github":
        if not segments or segments[0] not in GITHUB_CONTEXT_PROPERTIES:
            return f"github has no property {'.'.join(segments) or '(none)'}"
        if segments[0] != "event" and len(segments) != 1:
            return f"github.{segments[0]} is not an object"
        return None
    if context == "job":
        if not segments or segments[0] not in JOB_CONTEXT_PROPERTIES:
            return f"job has no property {'.'.join(segments) or '(none)'}"
        if segments[0] == "container" and segments[1:] not in ([], ["id"], ["network"]):
            return f"job.container has no property {'.'.join(segments[1:])}"
        if segments[0] not in ("container", "services") and len(segments) != 1:
            return f"job.{segments[0]} is not an object"
        return None
    if context in ("runner", "strategy"):
        known = RUNNER_CONTEXT_PROPERTIES if context == "runner" else STRATEGY_CONTEXT_PROPERTIES
        if len(segments) != 1 or segments[0] not in known:
            return f"{context} has no property {'.'.join(segments) or '(none)'}"
        return None
    if context == "inputs":
        if len(segments) != 1 or segments[0] not in scope.inputs:
            return f"inputs has no declared input {'.'.join(segments) or '(none)'}"
        return None
    if context == "steps":
        if not segments or segments[0] not in scope.step_ids:
            return f"steps has no step id {'.'.join(segments[:1]) or '(none)'} in {scope.where}"
        if segments[1:2] in (["outcome"], ["conclusion"]):
            return None if len(segments) == 2 else f"steps.{segments[0]}.{segments[1]} is a string"
        if segments[1:2] != ["outputs"] or len(segments) != 3:
            return f"steps.{segments[0]} exposes only outputs, outcome, and conclusion"
        if segments[2] not in scope.step_outputs.get(segments[0], frozenset()):
            return f"step {segments[0]} writes no output {segments[2]}"
        return None
    if context in ("needs", "jobs"):
        if context == "jobs" and not scope.allows_jobs_context:
            return "jobs is only available to reusable workflow outputs"
        reachable = scope.needs if context == "needs" else scope.job_ids
        if not segments or segments[0] not in reachable:
            return f"{context} cannot reach job {'.'.join(segments[:1]) or '(none)'} from {scope.where}"
        if segments[1:] == ["result"]:
            return None
        if segments[1:2] != ["outputs"] or len(segments) != 3:
            return f"{context}.{segments[0]} exposes only result and outputs"
        if segments[2] not in scope.job_outputs.get(segments[0], frozenset()):
            return f"job {segments[0]} declares no output {segments[2]}"
        return None
    return f"{context} is not a workflow context"


def _reject_expression(expression: str, scope: ExpressionScope) -> Iterator[str]:
    stripped = QUOTED_LITERAL_PATTERN.sub(" ", expression)
    for match in PROPERTY_CHAIN_PATTERN.finditer(stripped):
        head = match.group(1)
        if head.lower() in EXPRESSION_FUNCTIONS | EXPRESSION_LITERALS:
            continue
        segments = [segment for segment in match.group(2).split(".") if segment]
        reason = _validate_chain(head, segments, scope)
        if reason is not None:
            yield f"{scope.where}: ${{{{{expression}}}}} -> {reason}"


def unreal_context_references(
    path: Path,
    *,
    declared_step_outputs: Mapping[str, Sequence[str]] | None = None,
) -> list[str]:
    """Report every expression reference that names something GitHub does not define.

    ``declared_step_outputs`` supplies the outputs of steps whose run body cannot be read
    for them, keyed by step id: an ``uses:`` step, or one whose outputs are written by a
    platform CLI whose own tests already pin the names.
    """
    declared = declared_step_outputs or {}
    workflow = load_workflow(path)
    jobs = workflow.get("jobs") if isinstance(workflow.get("jobs"), dict) else {}
    outputs = _job_outputs(workflow)
    shared = {
        "inputs": _declared_inputs(workflow),
        "job_ids": frozenset(str(name) for name in jobs),
        "job_outputs": outputs,
    }

    problems: list[str] = []
    workflow_scope = ExpressionScope(
        where=f"{path.name} (workflow)",
        needs=frozenset(),
        step_ids=frozenset(),
        step_outputs={},
        allows_jobs_context=True,
        **shared,
    )
    top_level = {key: value for key, value in workflow.items() if key != "jobs"}
    for expression in _iter_expressions(top_level):
        problems.extend(_reject_expression(expression, workflow_scope))

    for name, job in jobs.items():
        steps = job.get("steps") if isinstance(job.get("steps"), list) else []
        job_scope = ExpressionScope(
            where=f"{path.name} (job {name})",
            needs=_as_set(job.get("needs")),
            step_ids=frozenset(str(item["id"]) for item in steps if "id" in item),
            step_outputs=_step_outputs(steps, declared),
            allows_jobs_context=False,
            **shared,
        )
        for expression in _iter_expressions(job):
            problems.extend(_reject_expression(expression, job_scope))
    return problems


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


def region_between(text: str, *, after: str = "", before: str = "") -> str:
    """The slice of ``text`` between two markers, refusing text that lacks one.

    ``text.split(marker)[0]`` IS THE SHAPE THIS EXISTS TO REPLACE, AND IT PASSES LOUDEST
    WHEN IT IS TESTING NOTHING. ``str.split`` on a marker that is not there returns the
    whole string in one piece, so the slice silently widens to the entire document and the
    assertion after it becomes "is this sentence anywhere in this file" -- which, for the
    file it was written about, it always is. The same is true of ``rsplit(marker, 1)[-1]``.
    Both are green on the day the anchor is renamed, which is the day the narrowing they
    exist for stops happening.

    That is the same defect as a pipeline whose exit status came from the last stage rather
    than from the program: a check that reports success because it never actually ran.

    So an absent marker is an :class:`AssertionError` naming the marker. Widening the region
    is the one failure mode a text assertion cannot survive, and it is the only one the
    naive spelling has.

    Both markers are optional and default to the ends of the text, so this covers the two
    shapes the suite uses -- everything ahead of a marker, and everything between a pair.
    ``after`` is matched last-first, because the region wanted is the one nearest the
    ``before`` marker rather than the first of several earlier ones.
    """
    region = text
    if before:
        if before not in region:
            raise AssertionError(
                f"{before!r} is not in this text, so a region ending at it would be the "
                "whole document and the assertion over it would test nothing"
            )
        region = region.split(before, 1)[0]
    if after:
        if after not in region:
            raise AssertionError(
                f"{after!r} is not in this text"
                + (f" ahead of {before!r}" if before else "")
                + ", so a region starting at it would be the whole document and the "
                "assertion over it would test nothing"
            )
        region = region.rsplit(after, 1)[-1]
    return region
