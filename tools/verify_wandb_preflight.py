"""Decide a submission against the last W&B credential verdict the audit published.

Makes no AWS call and holds no AWS credential, which is the point of it rather than a
limitation. ``edullm_platform.wandb_preflight`` carries the argument in full; the short
version is that the three principals able to read
``sbsandbox-intern-edullm-wandb-api-key-*`` are the two Batch execution roles and
``sbsandbox-intern-edullm-audit-reader``, none of them reachable from ``submit-run.yml``,
and the right response to that is to read the answer the audit already computes rather
than to put the platform's shared W&B credential behind a runner.

Everything it needs comes through ``gh``, under the ``actions: read`` the submit job
already holds for the approvals endpoint. So this adds no permission of any kind, to any
identity, in either GitHub or AWS.

Three exit codes, because there are three answers and two of them must not read alike:

0   W&B accepted the stored key, recently enough to still mean something.
1   W&B would refuse it. A finding, and the reason this exists.
2   Nothing was established. Never a refusal and never reported as a pass.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from edullm_platform.wandb_preflight import (
    AUDIT_BRANCH,
    AUDIT_VERDICT_ARTIFACT,
    AUDIT_VERDICT_FILENAME,
    AUDIT_WORKFLOW,
    FRESHNESS,
    Outcome,
    Preflight,
    decide,
)

EXIT_PROCEED: Final = 0
EXIT_REFUSE: Final = 1
EXIT_NOT_ESTABLISHED: Final = 2

#: How far back to look for a run that published one. More than a schedule's worth, because
#: an audit that failed before its check ran publishes nothing and the answer from the
#: night before is still the newest one anybody has. Bounded rather than unbounded so the
#: step costs a fixed handful of API calls; anything older is stale by any reading.
RUNS_EXAMINED: Final = 10

#: How long any one ``gh`` call may take. A runner that cannot reach the API is an outage,
#: and an outage must arrive as exit 2 within seconds rather than as a hung submission.
CALL_TIMEOUT_SECONDS: Final = 60

__all__ = [
    "CALL_TIMEOUT_SECONDS",
    "EXIT_NOT_ESTABLISHED",
    "EXIT_PROCEED",
    "EXIT_REFUSE",
    "RUNS_EXAMINED",
    "GitHubUnreachable",
    "build_parser",
    "completed_audit_runs",
    "download_the_verdict",
    "main",
    "read_the_published_verdict",
]


class GitHubUnreachable(RuntimeError):
    """The API did not answer, so nothing is known either way."""


def _gh(arguments: Sequence[str]) -> str:
    try:
        finished = subprocess.run(
            ["gh", *arguments],
            capture_output=True,
            text=True,
            timeout=CALL_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitHubUnreachable(f"gh {arguments[0]} failed: {exc.__class__.__name__}") from exc
    if finished.returncode != 0:
        # The body is deliberately not carried into the message. It is an API error from a
        # token this job holds, and this text reaches a world-readable log.
        raise GitHubUnreachable(f"gh {arguments[0]} exited {finished.returncode}")
    return finished.stdout


def completed_audit_runs(
    *, repository: str, workflow: str, branch: str, examined: int = RUNS_EXAMINED
) -> list[int]:
    """Completed runs of that workflow on that branch, newest first.

    Filtered to one branch, and that is load-bearing rather than tidy. The audit reader
    role pins its subject to ``ref:refs/heads/main``, so a dispatch of the audit from a
    branch cannot assume it and cannot read the secret at all -- a verdict from one would be
    a statement produced by a tool somebody edited, about a value it never saw.
    """
    query = (
        f"repos/{repository}/actions/workflows/{workflow}/runs"
        f"?branch={branch}&status=completed&per_page={examined}"
    )
    body = _gh(["api", query, "--jq", ".workflow_runs[].id"])
    return [int(line) for line in body.split() if line.isdigit()]


def download_the_verdict(*, repository: str, run_id: int, into: Path) -> dict[str, Any] | None:
    """The report that run published, or ``None`` if it published none this can read.

    ``None`` covers every way a run can be silent -- a job that failed before the check, an
    artifact that has expired, a zip holding some other file -- because the caller's next
    move is the same for all of them, which is to ask the run before it.
    """
    try:
        _gh(
            [
                "run",
                "download",
                str(run_id),
                "--repo",
                repository,
                "--name",
                AUDIT_VERDICT_ARTIFACT,
                "--dir",
                str(into),
            ]
        )
    except GitHubUnreachable:
        return None
    published = into / AUDIT_VERDICT_FILENAME
    if not published.is_file():
        return None
    try:
        document = json.loads(published.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def read_the_published_verdict(
    *, repository: str, workflow: str, branch: str, examined: int = RUNS_EXAMINED
) -> tuple[dict[str, Any] | None, int | None]:
    """The newest published report and the run that published it, or ``(None, None)``."""
    for run_id in completed_audit_runs(
        repository=repository, workflow=workflow, branch=branch, examined=examined
    ):
        with tempfile.TemporaryDirectory() as scratch:
            report = download_the_verdict(
                repository=repository, run_id=run_id, into=Path(scratch)
            )
        if report is not None:
            return report, run_id
    return None, None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Decide a submission against the last published W&B credential verdict."
    )
    parser.add_argument("--repository", required=True, help="owner/name of this repository.")
    parser.add_argument("--workflow", default=AUDIT_WORKFLOW)
    parser.add_argument("--branch", default=AUDIT_BRANCH)
    parser.add_argument(
        "--freshness-hours",
        type=float,
        default=FRESHNESS.total_seconds() / 3600,
        help="How old an acceptance may be and still be treated as current.",
    )
    parser.add_argument("--output", type=Path, default=None)
    return parser


def _render(preflight: Preflight, *, report: dict[str, Any] | None, run_id: int | None) -> str:
    document: dict[str, Any] = {
        "outcome": str(preflight.outcome),
        "reason": preflight.reason,
        "sentence": preflight.sentence,
        "verdict": str(preflight.verdict) if preflight.verdict is not None else None,
        "checked_at": preflight.checked_at.isoformat() if preflight.checked_at else None,
        "audit_run_id": run_id,
        # The published report verbatim. It carries a length, a four character prefix, a
        # truncated digest and the entity W&B named, and never the key -- the tool that
        # writes it has a test for exactly that, and the audit already prints the same
        # bytes into a world-readable log.
        "published": report,
    }
    return json.dumps(document, indent=2, sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)

    try:
        report, run_id = read_the_published_verdict(
            repository=options.repository, workflow=options.workflow, branch=options.branch
        )
    except GitHubUnreachable as exc:
        print(
            _render(
                Preflight(
                    outcome=Outcome.NOT_ESTABLISHED,
                    reason="wandb_verdict_unreadable",
                    sentence=(
                        f"The published W&B credential verdict could not be looked up: "
                        f"{exc}. Nothing was checked and this submission continues."
                    ),
                ),
                report=None,
                run_id=None,
            )
        )
        return EXIT_NOT_ESTABLISHED

    preflight = decide(
        report=report,
        now=datetime.now(tz=UTC),
        freshness=timedelta(hours=options.freshness_hours),
    )
    rendered = _render(preflight, report=report, run_id=run_id)
    print(rendered)
    if options.output is not None:
        options.output.write_text(rendered + "\n", encoding="utf-8")

    if preflight.outcome is Outcome.REFUSE:
        return EXIT_REFUSE
    if preflight.outcome is Outcome.PROCEED:
        return EXIT_PROCEED
    return EXIT_NOT_ESTABLISHED


if __name__ == "__main__":
    sys.exit(main())
