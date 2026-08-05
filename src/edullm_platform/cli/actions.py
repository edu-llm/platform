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
researcher's laptop, which is the thing this design exists to avoid.

**THE LINE BETWEEN THE TWO IS TEMPORAL, NOT TOPICAL, AND IT IS WHERE ``submit-run.yml``
ENDS.** That workflow resolves a commit, compiles a manifest, parks at an approval gate,
starts the admission execution and waits for its decision -- and then it finishes. The Batch
job it caused runs for hours afterwards with nothing on GitHub watching. So everything up to
and including admission is already on GitHub and costs an API call, and everything after it
is inside AWS and costs a runner. ``read_run_facts`` below draws exactly that line: it finds
the submission, asks whether admission happened, and answers from GitHub whenever the answer
is on GitHub. Only a run that reached AWS makes anybody wait.

**AND IT IS DRAWN FROM THE JOBS RATHER THAN FROM THE RUN'S CONCLUSION**, because the
conclusion is ambiguous in the one direction that matters. ``submit-run.yml`` writes where
the run went *after* the admission execution returns, so a workflow that concluded
``failure`` may have a Batch job running regardless -- and a ``cancel`` that believed the
conclusion would refuse to stop it. The admission job's own conclusion is not ambiguous
about whether it started: absent or skipped is certainly-not-admitted, success is
certainly-admitted, and its failure is the honest ``uncertain`` that falls through to a
dispatch, which is what happened before any of this existed.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Final

from edullm_platform.cli.workspace import CommandResult, CommandRunner

__all__ = [
    "ADMISSION_JOB",
    "CANCEL_WORKFLOW",
    "PLATFORM_REPOSITORY",
    "PRINTED_RUN_ID",
    "SUBMIT_WORKFLOW",
    "Admitted",
    "AmbiguousRunIdError",
    "GithubUnreachableError",
    "PlatformActions",
    "RunFacts",
    "SubmissionRun",
    "Waiting",
    "elapsed_said",
    "read_run_facts",
    "report_ceiling_seconds",
]

#: What a poll loop calls between attempts, handed the seconds spent so far. The loop knows
#: when it is idle and nothing else does; what to say about it is the caller's business.
type Waiting = Callable[[float], None]

#: The repository holding both workflows. Restated here rather than imported from
#: ``operational_inventory``, which owns ``EXPECTED_GITHUB_ORG`` and ``EXPECTED_GITHUB_REPOSITORY``:
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

#: ``submit-run.yml``'s admission job, by the display name the jobs endpoint answers with.
#: Matched by name because the REST API exposes no job key, which makes this a copy of a
#: string in a workflow file; ``tests/test_cli_actions.py`` reads that file and compares,
#: the same seam-test arrangement ``PLATFORM_REPOSITORY`` gets.
ADMISSION_JOB: Final = "Submit the approved manifest to admission"

#: How many recent dispatches to look through when joining a run id to its workflow run.
#: There is no index: dispatch inputs are not exposed by the runs API, and the run id does
#: not exist yet when ``run-name`` is evaluated, so the join is by reading each candidate's
#: compiled manifest, newest first, stopping at the first match. Bounded because the cost is
#: one artifact download per miss; a run older than this window is not found and falls
#: through to a dispatch, which is what every run did before this existed.
SUBMISSION_SEARCH_DEPTH: Final = 30

#: How many characters of a run id the listing prints, measured in :attr:`short_run_id`.
PRINTED_RUN_ID: Final = 13

#: The two polls behind every dispatch, hoisted out of the method signatures they used to
#: be defaults on. A caller that has to make somebody wait needs to say how long the wait
#: can run before it starts, and it cannot say that from the inside of the wait -- so the
#: numbers live where both the loop and the sentence about the loop can read them, and
#: :func:`report_ceiling_seconds` adds them up rather than anybody writing the total down.
NEW_RUN_ATTEMPTS: Final = 20
NEW_RUN_INTERVAL: Final = 3.0
COMPLETION_ATTEMPTS: Final = 100
COMPLETION_INTERVAL: Final = 6.0


def report_ceiling_seconds() -> float:
    """The longest a dispatch-and-read can take before it gives up, in seconds.

    Both loops sleep between attempts and not before the first, so a bound of ``n``
    attempts holds ``n - 1`` sleeps. Derived rather than written down because the sentence
    that quotes it reaches a terminal, and ``tests/test_cli_no_hardcoded_bounds.py`` fails
    the build on a duration typed into a string this binary prints.
    """
    return (NEW_RUN_ATTEMPTS - 1) * NEW_RUN_INTERVAL + (
        COMPLETION_ATTEMPTS - 1
    ) * COMPLETION_INTERVAL


class GithubUnreachableError(RuntimeError):
    """``gh`` answered with something this cannot act on.

    Never a refusal. A submission nobody could reach GitHub to dispatch has not been
    declined, and reporting the two the same way is how somebody spends an afternoon
    editing a spec that was fine.
    """


class AmbiguousRunIdError(LookupError):
    """An abbreviated run id that names more than one of the recent submissions.

    Carries the matches rather than a sentence about them, because the caller is what knows
    how to say it -- and what it has to say is *which runs*, since the only remedy is to
    pick one. This is the one lookup failure in this file that must never be answered with
    "list your runs and try again": the listing is where the abbreviation came from.
    """

    def __init__(self, given: str, matches: tuple[SubmissionRun, ...]) -> None:
        super().__init__(f"{given} names {len(matches)} recent submissions")
        self.given = given
        self.matches = matches


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
        """``run_019fcf3c-9878`` -- the whole of the clock the id carries and none of the rest.

        THE LENGTH IS MEASURED RATHER THAN CHOSEN, because the first eight characters were
        chosen and they collide. A run id is a UUIDv7, whose leading twelve hex digits are
        the millisecond it was minted; eight of them are the top thirty-two bits of that,
        which advance once every 65,536 ms. Two submissions inside the same minute
        therefore *have* to share an eight-character prefix -- it is arithmetic and not bad
        luck -- and across the last 74 real submissions ten of them did, in five pairs
        4.6 to 55.6 seconds apart. Retries and a resubmitted sweep are exactly that shape.

        Thirteen characters is the full timestamp: two runs share it only if they were
        minted in the same millisecond, against a smallest observed gap of 3.875 s. It also
        ends on a group boundary, and everything past it is the random half of the id,
        which no reader can reason about. Nine adds only the hyphen and ten leaves 4,096 ms
        of slack, which that 3.875 s gap already sits inside.
        """
        if self.run_id is None:
            return f"actions/{self.workflow_run_id}"
        return self.run_id[: len("run_") + PRINTED_RUN_ID]


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
        dispatched: list[str] | None = None,
    ) -> None:
        self._runner = runner
        self._repository = repository
        self._sleep = sleep
        # A LIST THE CALLER MAY OWN, BECAUSE THE CALLER IS WHAT ANSWERS FOR AN INTERRUPT.
        # ``main`` catches Ctrl-C for the whole binary and has to say whether a workflow is
        # still running with nobody watching it, which is a fact only this class learns and
        # only at the moment of learning it. Handing the list down is what lets one handler
        # answer for four verbs without any of them holding the answer.
        self._dispatched = [] if dispatched is None else dispatched

    @property
    def repository(self) -> str:
        return self._repository

    @property
    def dispatched(self) -> tuple[str, ...]:
        """Every workflow this has set going, in order, and empty until one has been."""
        return tuple(self._dispatched)

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
        self._dispatched.append(workflow)

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
        attempts: int = NEW_RUN_ATTEMPTS,
        interval: float = NEW_RUN_INTERVAL,
        waiting: Waiting | None = None,
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
                self._said_waiting(waiting, attempt * interval)
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

    def jobs(self, workflow_run_id: int) -> tuple[dict[str, Any], ...]:
        """Every job of one workflow run, with the per-job conclusion the run's hides."""
        answered = self._api(f"repos/{self._repository}/actions/runs/{workflow_run_id}/jobs")
        listed = answered.get("jobs")
        if not isinstance(listed, list):
            return ()
        return tuple(job for job in listed if isinstance(job, dict))

    def pending_deployments(self, workflow_run_id: int) -> tuple[dict[str, Any], ...]:
        """Which gate a run is parked at, who may release it, and whether you are one.

        Readable by anyone with read access to the repository -- GitHub's own documentation
        says so on this endpoint -- which is what makes it usable from a binary holding
        nothing but ``gh``. It is also the one place ``current_user_can_approve`` is
        answered, so a lead can be told the run is waiting on *them* rather than on the
        abstraction "a lead".
        """
        return self._api_list(
            f"repos/{self._repository}/actions/runs/{workflow_run_id}/pending_deployments"
        )

    def approvals(self, workflow_run_id: int) -> tuple[dict[str, Any], ...]:
        """Who released a gate and when, which is the question an audit asks in a terminal.

        The same endpoint ``submit-run.yml``'s admission job reads to learn whose
        authorization to evaluate, so this is not a second source for the fact -- it is the
        same source, read by a reader instead of by a workflow.
        """
        return self._api_list(
            f"repos/{self._repository}/actions/runs/{workflow_run_id}/approvals"
        )

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
        attempts: int = COMPLETION_ATTEMPTS,
        interval: float = COMPLETION_INTERVAL,
        waiting: Waiting | None = None,
        elapsed_already: float = 0.0,
    ) -> str:
        """Poll one run to a conclusion, and answer with whichever one it reached.

        ``elapsed_already`` is what the caller spent finding the run in the first place, so
        that the two waits behind one dispatch report one clock rather than restarting it.
        """
        for attempt in range(attempts):
            if attempt:
                self._said_waiting(waiting, elapsed_already + attempt * interval)
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

    @staticmethod
    def _said_waiting(waiting: Waiting | None, elapsed: float) -> None:
        """Hand the caller the clock, before each sleep and never during one.

        Called from inside the loop rather than from a thread, so it can print only at a
        moment the binary is otherwise idle and it can print a whole line. That is the
        difference between a sign of life and a spinner: a spinner needs a carriage return
        and a cursor move, which would make a run piped into a file a different run from
        the one a terminal showed.
        """
        if waiting is not None:
            waiting(elapsed)

    def _api(self, path: str) -> dict[str, Any]:
        document = self._read(path)
        if not isinstance(document, dict):
            raise GithubUnreachableError(f"gh api {path} answered with a {type(document).__name__}")
        return document

    def _api_list(self, path: str) -> tuple[dict[str, Any], ...]:
        """An endpoint whose body is a bare array, which several of these are.

        Answers empty rather than raising where ``gh`` refuses the call. Both of the
        endpoints read through here are supplementary -- they say who may release a run and
        who did -- and a token that cannot see them should cost a reader those two lines
        rather than the whole answer.
        """
        try:
            document = self._read(path)
        except GithubUnreachableError:
            return ()
        if not isinstance(document, list):
            return ()
        return tuple(item for item in document if isinstance(item, dict))

    def _read(self, path: str) -> Any:
        result = self._runner(("gh", "api", path))
        if not result.ok:
            raise GithubUnreachableError(f"gh api {path} answered: {_said(result)}")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise GithubUnreachableError(f"gh api {path} answered with something that is not JSON") from exc


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


class Admitted(StrEnum):
    """Whether a run reached AWS, which is the only question that decides a dispatch.

    Three values rather than two because the honest answer is sometimes neither. ``NO`` and
    ``YES`` are both certainties read from the admission job's own conclusion; ``UNSURE`` is
    everything else -- a workflow run this could not find, an admission job that failed at
    an unknown point, a jobs endpoint that would not answer -- and it behaves exactly as the
    binary behaved before any of this existed, by dispatching.
    """

    NO = "no"
    YES = "yes"
    UNSURE = "unsure"


@dataclass(frozen=True)
class RunFacts:
    """Everything about one run that GitHub can answer without starting a runner."""

    run_id: str
    admitted: Admitted
    #: One line naming what was established and how, printed whichever way this went.
    because: str
    submission: SubmissionRun | None = None
    #: From ``pending_deployments``, and only ever populated while a run is parked.
    gate: str | None = None
    reviewers: tuple[str, ...] = ()
    you_can_release: bool = False
    #: From ``approvals``, once somebody has.
    approver: str | None = None
    approved_at: datetime | None = None
    experiment: str | None = None
    team: str | None = None

    @property
    def needs_a_dispatch(self) -> bool:
        return self.admitted is not Admitted.NO

    @property
    def was_found(self) -> bool:
        """Whether a dispatch in the search window carried this run id at all.

        **THE OTHER TWO WAYS TO BE ``UNSURE`` ARE NOT THIS ONE, AND THE DIFFERENCE DECIDES
        WHETHER A VERB MAY SPEND A RUNNER.** An admission job still running and one that
        ended at an unknown point are both uncertainties *about a run this found*: there is
        a workflow run, a compiled manifest and a page to link, and asking AWS is the honest
        next question. Finding nothing is a different fact. It says only that the window
        does not reach the id, which is equally true of a run that finished last month and
        of an id that was never minted, and neither of those is worth the poll ceiling on a
        verb that reads.

        ``submission`` is the whole of the test because ``read_run_facts`` fills it in on
        every branch but that one. A separate flag would be a second answer to a question
        one field already answers, and the two could disagree.
        """
        return self.submission is not None


def read_run_facts(
    actions: PlatformActions,
    run_id: str,
    *,
    depth: int = SUBMISSION_SEARCH_DEPTH,
) -> RunFacts:
    """Everything GitHub knows about one run, and whether AWS has to be asked as well.

    THE ORDER IS THE CHEAP QUESTION FIRST. Find the submission, read its jobs, and stop the
    moment the answer is certain.     A run parked at a gate, refused while compiling, or still
    being compiled has no Batch job for anybody to describe, so those three end here -- and
    they are a large share of what gets asked, because they are what people check in the
    minutes after submitting, over and over, while they wait for a lead.

    ``run_id`` may be abbreviated. What comes back always carries the whole one, because
    everything downstream of this -- the heading it looks for in a report, the id it
    dispatches ``cancel-run.yml`` with -- is talking to something that only knows the id in
    full.
    """
    found = find_submission(actions, run_id, depth=depth)
    if found is None:
        return RunFacts(
            run_id=run_id,
            admitted=Admitted.UNSURE,
            because=(
                f"no dispatch of {SUBMIT_WORKFLOW} in the last {depth} carries this run id. "
                "GitHub keeps workflow runs and their artifacts for a bounded window, so an "
                "older run is not findable here and has to be asked for directly."
            ),
        )
    submission, compiled = found
    jobs = {str(job.get("name")): job for job in actions.jobs(submission.workflow_run_id)}
    admission = jobs.get(ADMISSION_JOB)
    conclusion = admission.get("conclusion") if admission is not None else None
    facts = RunFacts(
        run_id=submission.run_id or run_id,
        admitted=Admitted.UNSURE,
        because="",
        submission=submission,
        experiment=_string(compiled, "experiment"),
        team=_string(compiled, "team"),
    )

    if submission.state == "PENDING_APPROVAL":
        return _parked(actions, facts)
    if admission is None or conclusion == "skipped":
        return replace(
            facts,
            admitted=Admitted.NO,
            because=(
                f"{SUBMIT_WORKFLOW} finished without running its admission job, so nothing "
                "was ever sent to AWS. What refused it is on the run page."
            ),
        )
    if conclusion == "success":
        return _released(actions, replace(facts, admitted=Admitted.YES, because=""))
    if conclusion is None:
        return replace(
            facts,
            admitted=Admitted.UNSURE,
            because=(
                "the admission job is still running. It may already have started the run, "
                "so what AWS thinks is the only reliable answer."
            ),
        )
    return replace(
        facts,
        admitted=Admitted.UNSURE,
        because=(
            f"the admission job ended {conclusion}, which does not say whether it got as "
            "far as starting the run -- it writes where a run went only after admission "
            "answers. Asking AWS is the only way to be sure."
        ),
    )


def _parked(actions: PlatformActions, facts: RunFacts) -> RunFacts:
    """A run waiting on a person, described down to which person and whether it is you."""
    deployments = actions.pending_deployments(facts.submission.workflow_run_id)  # type: ignore[union-attr]
    gate: str | None = None
    reviewers: list[str] = []
    yours = False
    for deployment in deployments:
        environment = deployment.get("environment")
        if isinstance(environment, dict) and isinstance(environment.get("name"), str):
            gate = environment["name"]
        yours = yours or deployment.get("current_user_can_approve") is True
        for entry in deployment.get("reviewers") or []:
            reviewer = entry.get("reviewer") if isinstance(entry, dict) else None
            named = reviewer.get("login") or reviewer.get("name") if isinstance(reviewer, dict) else None
            if isinstance(named, str) and named not in reviewers:
                reviewers.append(named)
    return replace(
        facts,
        admitted=Admitted.NO,
        because=(
            "this run is parked at an approval gate, which is before anything reaches AWS. "
            "There is no Batch job to describe yet."
        ),
        gate=gate,
        reviewers=tuple(reviewers),
        you_can_release=yours,
    )


def _released(actions: PlatformActions, facts: RunFacts) -> RunFacts:
    """An admitted run, with the gate's release attributed where there was one to release."""
    approver, approved_at = None, None
    for approval in actions.approvals(facts.submission.workflow_run_id):  # type: ignore[union-attr]
        if approval.get("state") != "approved":
            continue
        user = approval.get("user")
        if isinstance(user, dict) and isinstance(user.get("login"), str):
            approver = user["login"]
        approved_at = _instant(approval.get("comment_created_at")) or approved_at
    return replace(
        facts,
        because=(
            "this run was admitted, so what it is doing now is inside AWS. Only "
            f"{CANCEL_WORKFLOW} holds an identity that may read a Batch job."
        ),
        approver=approver,
        approved_at=approved_at,
    )


def find_submission(
    actions: PlatformActions,
    run_id: str,
    *,
    depth: int = SUBMISSION_SEARCH_DEPTH,
) -> tuple[SubmissionRun, dict[str, Any]] | None:
    """Join a platform run id, whole or abbreviated, back to the dispatch that minted it.

    THERE IS NO INDEX AND THERE CANNOT EASILY BE ONE. A workflow run does not expose the
    inputs it was dispatched with, and ``run-name`` -- the one field that would show on the
    runs list -- is evaluated before the compile job exists, so it cannot carry an id that
    job has not minted yet. What is left is to read each candidate's compiled manifest,
    which is one artifact download per candidate examined.

    **A WHOLE ID STOPS AT THE FIRST MATCH AND AN ABBREVIATED ONE CANNOT.** Nothing else
    could report an ambiguity honestly: a prefix that stopped at the first match would
    silently pick the newest of several runs, and picking the wrong one for ``cancel``
    is the worst thing in this file. So the whole window is read for an abbreviation, and
    the whole window is what it costs -- one artifact download per dispatch examined,
    against one or two for an id pasted in full. That is the price of the shorthand and it
    is charged only to whoever uses it.

    :raises AmbiguousRunIdError: when an abbreviation names more than one of them.
    """
    matches: list[tuple[SubmissionRun, dict[str, Any]]] = []
    for run in actions.workflow_runs(SUBMIT_WORKFLOW, limit=depth):
        identifier = run.get("id")
        created = _instant(run.get("created_at"))
        if not isinstance(identifier, int) or created is None:
            continue
        compiled = actions.compiled_submission(identifier)
        minted = None if compiled is None else _string(compiled, "run_id")
        if compiled is None or minted is None or not minted.startswith(run_id):
            continue
        manifest = compiled.get("manifest")
        fanout = manifest.get("fanout") if isinstance(manifest, dict) else None
        found = (
            SubmissionRun(
                workflow_run_id=identifier,
                state=submission_state(run),
                created_at=created,
                url=str(run.get("html_url") or ""),
                run_id=minted,
                experiment=_string(compiled, "experiment"),
                cells=fanout.get("size") if isinstance(fanout, dict) else None,
            ),
            compiled,
        )
        if minted == run_id:
            return found
        matches.append(found)
    if len(matches) > 1:
        raise AmbiguousRunIdError(run_id, tuple(match for match, _ in matches))
    return matches[0] if matches else None


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
