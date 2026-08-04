"""Driving the two workflows that already do this work, through ``gh``.

NOTHING HERE TALKS TO AWS AND NOTHING HERE MAY. ``system-overview.md`` puts it in one
sentence under "The submission path": the submission role's trust policy pins to one
workflow file, so ``submit`` dispatches rather than talking to AWS. The same is true of the
other three verbs by a different route -- ``cancel-run.yml``'s header records that the role
which may describe, tail and stop a Batch job is trusted to that file specifically, so a
binary on a laptop cannot obtain it and should not try.

So this module is a typist. It fills in ``submit-run.yml``'s fifteen fields, it fills in
``cancel-run.yml``'s three, and it reads the answers back out of what those runs leave
behind.

**READING THE ANSWER BACK IS THE AWKWARD HALF, AND THE AWKWARDNESS IS GITHUB'S.** A step
summary is rendered on the run page and exposed by no REST endpoint -- ``submit-run.yml``
says so in its own comment, and uploads a copy as an artifact for exactly that reason.
``cancel-run.yml`` solves it differently and without knowing it did: every block it writes
to the summary is written through ``tee``, so the same bytes are in the job log, and a job
log *is* readable. That is what ``status``, ``logs`` and ``cancel`` read.

**WHAT THAT COSTS, SAID RATHER THAN LEFT TO BE FOUND OUT.** Asking what a Batch job is
doing means dispatching a workflow and waiting for a runner, which is tens of seconds
rather than the instant a transcript implies. The alternative is a credential on every
researcher's laptop, which is the thing this design exists to avoid. Where an answer can be
had from GitHub alone it is: ``status`` with no run id reads the caller's own submission
workflow runs and needs no dispatch at all, which is the state people check most often --
whether a lead has tapped yet.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Final

from edullm_platform.cli.workspace import CommandResult, CommandRunner

__all__ = [
    "CANCEL_WORKFLOW",
    "PLATFORM_REPOSITORY",
    "SUBMIT_WORKFLOW",
    "GithubUnreachableError",
    "PlatformActions",
    "SubmissionRun",
    "elapsed_said",
]

#: The repository holding both workflows. Restated here rather than imported from
#: ``phase0_gate``, which owns ``EXPECTED_GITHUB_ORG`` and ``EXPECTED_GITHUB_REPOSITORY``:
#: importing a phase gate would pull the whole evidence and criteria graph into a binary
#: whose ``--help`` should cost nothing. ``tests/test_cli_actions.py`` compares the two, so
#: the copy cannot drift silently -- the same seam-test arrangement the queue names get.
PLATFORM_REPOSITORY: Final = "edu-llm/platform"

SUBMIT_WORKFLOW: Final = "submit-run.yml"
CANCEL_WORKFLOW: Final = "cancel-run.yml"

#: The artifact ``submit-run.yml``'s compile job uploads. It is written before the approval
#: gate, so it is readable while a run is still waiting for somebody to tap -- which is the
#: whole reason ``status`` can name a run id during the wait.
COMPILED_SUBMISSION_ARTIFACT: Final = "compiled-submission"


class GithubUnreachableError(RuntimeError):
    """``gh`` answered with something this cannot act on.

    Never a refusal. A submission nobody could reach GitHub to dispatch has not been
    declined, and reporting the two the same way is how somebody spends an afternoon
    editing a spec that was fine.
    """


@dataclass(frozen=True)
class SubmissionRun:
    """One dispatch of ``submit-run.yml``, as GitHub describes it and as we read it.

    ``state`` is this platform's word rather than GitHub's pair of them. A workflow run
    carries a status and a conclusion, and the four combinations that matter here are four
    different things to the person who submitted: waiting for a lead, being compiled and
    admitted, admitted, and refused.
    """

    workflow_run_id: int
    state: str
    created_at: datetime
    url: str
    run_id: str | None = None
    experiment: str | None = None
    cells: int | None = None

    @property
    def short_run_id(self) -> str:
        """``run_019fd2a1``, which is what every transcript prints and what people say."""
        if self.run_id is None:
            return f"actions/{self.workflow_run_id}"
        return self.run_id[: len("run_") + 8]


def elapsed_said(since: datetime, *, now: datetime | None = None) -> str:
    """``38s``, ``4m``, ``1h11m``, ``3h48m`` -- the forms the transcripts use.

    Seconds only under a minute, because past that they are noise on a wait measured in
    approvals; hours and minutes past an hour, because ``188m`` is a number a reader has to
    divide.
    """
    moment = datetime.now(UTC) if now is None else now
    seconds = max(int((moment - since).total_seconds()), 0)
    if seconds < 60:
        return f"{seconds}s"
    minutes, _ = divmod(seconds // 60, 1)
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h{minutes % 60:02d}m"


class PlatformActions:
    """Every call this binary makes to GitHub, in one place so a test can supply them all."""

    def __init__(
        self,
        runner: CommandRunner,
        *,
        repository: str = PLATFORM_REPOSITORY,
        sleep: Any = time.sleep,
    ) -> None:
        self._runner = runner
        self._repository = repository
        self._sleep = sleep

    @property
    def repository(self) -> str:
        return self._repository

    def dispatch(self, workflow: str, fields: Mapping[str, str]) -> None:
        """``gh workflow run``, with every value passed as its own ``-f`` argument.

        One argument per field rather than a formatted string, so that a command containing
        a quote, a newline or a shell metacharacter reaches GitHub as the submitter typed
        it. The compile job POSIX-splits the command on the far side; anything this layer
        did to it first would be a second parse.
        """
        argv: list[str] = [
            "gh",
            "workflow",
            "run",
            workflow,
            "--repo",
            self._repository,
        ]
        for name, value in fields.items():
            argv.extend(("-f", f"{name}={value}"))
        result = self._runner(tuple(argv))
        if not result.ok:
            raise GithubUnreachableError(
                f"gh could not dispatch {workflow}: {_said(result)}. This is not a refusal "
                "of the submission. Check gh auth status and that you can see "
                f"{self._repository}."
            )

    def workflow_runs(
        self, workflow: str, *, actor: str | None = None, limit: int = 20
    ) -> tuple[dict[str, Any], ...]:
        """The recent dispatches of one workflow, newest first, optionally one person's."""
        query = f"per_page={limit}&event=workflow_dispatch"
        if actor is not None:
            query += f"&actor={actor}"
        answered = self._api(
            f"repos/{self._repository}/actions/workflows/{workflow}/runs?{query}"
        )
        runs = answered.get("workflow_runs")
        if not isinstance(runs, list):
            raise GithubUnreachableError(
                f"the workflow runs endpoint answered without a workflow_runs list for "
                f"{workflow}"
            )
        return tuple(run for run in runs if isinstance(run, dict))

    def wait_for_a_new_run(
        self,
        workflow: str,
        *,
        actor: str | None,
        after: datetime,
        attempts: int = 20,
        interval: float = 3.0,
    ) -> dict[str, Any] | None:
        """The run a dispatch just created, found by being newer than the dispatch.

        GitHub's ``workflow run`` returns nothing that identifies what it started -- no run
        id, no url -- so the only join available is time. Bounded rather than open-ended,
        and answering ``None`` rather than raising: a dispatch that succeeded and a run
        this could not find is still a submission that is on its way, and telling somebody
        it failed would send them to dispatch it a second time.
        """
        for attempt in range(attempts):
            if attempt:
                self._sleep(interval)
            for run in self.workflow_runs(workflow, actor=actor, limit=10):
                created = _instant(run.get("created_at"))
                if created is not None and created >= after:
                    return run
        return None

    def compiled_submission(self, workflow_run_id: int) -> dict[str, Any] | None:
        """What the compile job recorded, read out of the artifact it uploaded.

        ``gh run download`` rather than the artifacts endpoint, because that endpoint
        answers with a zip and every command here is run in text mode -- a binary body read
        as text is corrupted before anything can unpack it. ``gh`` unpacks it into a
        directory instead, which is also the spelling a person would use by hand.

        ``None`` while the compile job has not finished, and ``None`` where it refused.
        Both are ordinary and neither is worth raising over: a submission being compiled has
        no run id yet, and a refused one never will.
        """
        with TemporaryDirectory(prefix="edullm-artifact-") as directory:
            downloaded = self._runner(
                (
                    "gh",
                    "run",
                    "download",
                    str(workflow_run_id),
                    "--repo",
                    self._repository,
                    "--name",
                    COMPILED_SUBMISSION_ARTIFACT,
                    "--dir",
                    directory,
                )
            )
            if not downloaded.ok:
                return None
            found = sorted(Path(directory).rglob("*.json"))
            if not found:
                return None
            try:
                document = json.loads(found[0].read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
        return document if isinstance(document, dict) else None

    def job_log(self, workflow_run_id: int) -> str:
        """The whole log of a finished run, which is where the step summary's bytes are."""
        result = self._runner(
            ("gh", "run", "view", str(workflow_run_id), "--repo", self._repository, "--log")
        )
        if not result.ok:
            raise GithubUnreachableError(
                f"the log for workflow run {workflow_run_id} could not be read: "
                f"{_said(result)}"
            )
        return result.stdout

    def wait_for_completion(
        self,
        workflow_run_id: int,
        *,
        attempts: int = 100,
        interval: float = 6.0,
    ) -> str:
        """Poll one run to a conclusion, and answer with whichever one it reached."""
        for attempt in range(attempts):
            if attempt:
                self._sleep(interval)
            run = self._api(f"repos/{self._repository}/actions/runs/{workflow_run_id}")
            status = run.get("status")
            if status == "completed":
                conclusion = run.get("conclusion")
                return str(conclusion) if isinstance(conclusion, str) else "unknown"
        raise GithubUnreachableError(
            f"workflow run {workflow_run_id} had not finished after "
            f"{attempts * interval:.0f}s. Nothing has been decided either way; the run page "
            "carries what it is doing."
        )

    def _api(self, path: str) -> dict[str, Any]:
        result = self._runner(("gh", "api", path))
        if not result.ok:
            raise GithubUnreachableError(f"gh api {path} answered: {_said(result)}")
        try:
            document = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise GithubUnreachableError(f"gh api {path} answered with something that is not JSON") from exc
        if not isinstance(document, dict):
            raise GithubUnreachableError(f"gh api {path} answered with a {type(document).__name__}")
        return document


def read_submission_runs(
    actions: PlatformActions,
    *,
    actor: str | None,
    limit: int = 10,
    resolve_run_ids: bool = True,
) -> tuple[SubmissionRun, ...]:
    """The caller's recent submissions, with the run id resolved where one exists yet."""
    found: list[SubmissionRun] = []
    for run in actions.workflow_runs(SUBMIT_WORKFLOW, actor=actor, limit=limit):
        created = _instant(run.get("created_at"))
        identifier = run.get("id")
        if created is None or not isinstance(identifier, int):
            continue
        compiled = actions.compiled_submission(identifier) if resolve_run_ids else None
        manifest = compiled.get("manifest") if isinstance(compiled, dict) else None
        fanout = manifest.get("fanout") if isinstance(manifest, dict) else None
        found.append(
            SubmissionRun(
                workflow_run_id=identifier,
                state=submission_state(run),
                created_at=created,
                url=str(run.get("html_url") or ""),
                run_id=_string(compiled, "run_id"),
                experiment=_string(compiled, "experiment"),
                cells=fanout.get("size") if isinstance(fanout, dict) else None,
            )
        )
    return tuple(found)


def submission_state(run: Mapping[str, Any]) -> str:
    """GitHub's status and conclusion, read as the four things they mean to a submitter.

    ``waiting`` is the one that matters and the one GitHub names least helpfully: it is a
    run parked at an environment with reviewers, which is exactly "a lead has not tapped
    yet". Everything past the gate is the workflow's own business, and a submitter reads
    the conclusion.
    """
    status = run.get("status")
    if status == "waiting":
        return "PENDING_APPROVAL"
    if status in {"queued", "requested", "pending"}:
        return "DISPATCHED"
    if status == "in_progress":
        return "COMPILING"
    conclusion = run.get("conclusion")
    if conclusion == "success":
        return "SUBMITTED"
    if conclusion == "cancelled":
        return "CANCELLED"
    if conclusion is None:
        return "UNKNOWN"
    return "REFUSED"


def _string(document: dict[str, Any] | None, key: str) -> str | None:
    if document is None:
        return None
    value = document.get(key)
    return value if isinstance(value, str) else None


def _instant(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # GitHub answers in UTC with a Z, which ``fromisoformat`` reads as an offset, so this
    # branch is for a document that carried a naive instant. Assumed UTC rather than local:
    # every timestamp on this path is the API's and the API's are UTC.
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _said(result: CommandResult) -> str:
    """What went wrong, from whichever stream carried it, trimmed to one readable line."""
    text = (result.stderr or result.stdout).strip()
    first = text.splitlines()[0] if text else f"exit {result.returncode}"
    return first[:200]


def read_report_sections(log: str, headings: Sequence[str]) -> str:
    """The parts of ``cancel-run.yml``'s output a reader asked for, out of the job log.

    ``gh run view --log`` prefixes every line with the job name, the step name and a
    timestamp, so the report that workflow ``tee``s into the step summary arrives here
    wrapped in three columns of noise. Stripping the prefix is what makes the markdown it
    wrote readable again.

    Section rather than whole log because the workflow writes four reports into one job and
    the verbs want different ones -- ``status`` the description, ``logs`` the tail.
    """
    keeping = False
    kept: list[str] = []
    for raw in log.splitlines():
        line = _unprefixed(raw)
        if line.startswith(("## ", "### ")):
            keeping = any(heading.lower() in line.lower() for heading in headings)
        if keeping:
            kept.append(line)
    return "\n".join(kept).strip()


def _unprefixed(line: str) -> str:
    """``job\tstep\t2026-08-04T12:00:00.0000000Z message`` down to ``message``."""
    parts = line.split("\t")
    tail = parts[-1]
    head, separator, rest = tail.partition(" ")
    if separator and head.endswith("Z") and "T" in head:
        return rest
    return tail
