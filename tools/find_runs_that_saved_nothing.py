"""Which runs promised a checkpoint and have not got one, which is the failure nothing reports.

**The most expensive mistake available on this platform is silent.** OLMo-core's example
defaults its save folder to ``/tmp``, which is local disk on a machine that stops existing
when the job ends. A twelve-hour run that takes the default trains for twelve hours, writes
checkpoints nobody can reach, exits zero, and is recorded as a success. Nothing in the
lineage disagrees: ``ResultManifest`` has no field for "a checkpoint was expected and none
was found", and an empty ``checkpoints`` tuple already means "this run was not expected to
checkpoint at all".

So this asks the question the record cannot. For every run whose workload profile carries a
checkpoint contract, it reads the run's checkpoint prefix and says whether a resume would
find anything to load.

**THREE ANSWERS, BECAUSE THE MIDDLE ONE IS THE EXPENSIVE ONE.** This counted objects until
it was pointed at the account, and counting cannot tell a checkpoint from a fragment of one.
Eight runs had written exactly one object -- ``step0/train/rank0.pt``, rank 0's trainer state
and no weights or optimizer at all -- and a count of one reads as healthy. A run in that
state resumes from nothing, and the report said it was fine. So the states are: wrote
nothing, wrote something no loader will accept, and wrote a checkpoint that will load.

**The definition of "will load" is not this tool's.** :func:`inspect_checkpoint` already
holds it, derived from OLMo-core's own ``dir_is_checkpoint`` and from the ``_SUCCESS``
protocol, and a second definition here would be one that disagrees with the resume path
eventually. This fetches bytes and asks that module; it has no opinion of its own about what
a checkpoint is.

**Why a tool rather than the recorder.** Putting it in the lifecycle recorder is the right
long-term home and costs two things worth deciding separately: a ``ResultManifest`` field,
which is a contract change that moves a recorded structural digest, and ``s3:ListBucket`` for a
Lambda role that today holds four ``PutObject`` grants and deliberately nothing else. Both
are defensible and neither should be paid at the same time as finding out whether the check
is worth having. This runs from a laptop or from the audit workflow against credentials
that already exist, answers the same question, and is what the person triaging a cohort's
first week actually needs.

**IT ASKS ABOUT RUNS THE PLATFORM RECORDED AS A SUCCESS, WHICH IS THE CASE IT NAMES ABOVE.**
An intent record says what a run was submitted as and nothing about how it ended, so reading
intents alone this could not tell a run that finished and saved nothing from one that never
reached its first interval. That is not a small gap. Fifteen runs carried a contract, one
finished, and the other fourteen were reported every night as though the checkpointer had
failed them. It had not: they died at ``wandb.init()``, in the checkpointer's own teardown,
and on an evaluator fetching a file that is not served. Every one of those is recorded as a
failure already, with an exit code, and repeating it here says nothing new while burying the
run that does.

So the result records scope the question. A run recorded as ``failed`` is listed and not
judged, and so is one with no result record yet, because it has not finished. This is not
the same as trusting the record: the outcome decides *whether to ask*, and the prefix still
decides the answer. A run recorded as a success is read exactly as before, which is the
whole point, since a success that saved nothing is the failure nothing else reports.

**Without the result records it behaves as it did.** They are a separate prefix in the
lineage bucket, and the audit reader role does hold it, so the scheduled run has them and
the degradation is for a laptop pointed at a tree that has none. When no ``result/`` tree is
present every contracted run is judged, as before. Nothing is silently let through by a sync
that did not happen.

**ONE RUN IN THE ACCOUNT CANNOT BE REPAIRED, AND A PERMANENTLY RED JOB REPORTS NOTHING.**
``run_019fbce3-ce4b-7067-b8c7-c2cf25e6b667`` is finished, its prefix is empty, and no
action available to anybody changes either. Left alone it holds the audit red for ever,
which is worse than it sounds: the next real finding in this job arrives at a job that was
already red, and nobody looks.

So an acknowledgement list, read from
``config/reports/checkpoint-acknowledgements.yaml``, names runs that have been read and
adjudicated. Three properties are what make it a record rather than a way of going blind.
It is *per run*: a run id is written down one at a time, and a run nobody has written down
is judged exactly as before, so the job still goes red the first morning after a new
offender. It carries a *reason with a length floor*, because "known issue" is not an
adjudication and the value of the entry is that a later reader can tell whether the prefix
was understood or waved through. And an acknowledged run stays *in the report*, in its own
section with its reason beside it, because a run that disappears is a run nobody reads --
which is the failure this whole tool exists to answer.

**A date cutoff was the obvious alternative and is the wrong shape.** "Ignore runs before
2026-08-02" is one line and covers this run, and it also covers every other run submitted
before that date, including ones nobody has looked at -- and it keeps covering them as the
reasons for each are forgotten. The list is longer to write and says something true.

An entry that no longer covers a finding is reported rather than left in place, because a
list that only ever grows stops describing anything.

**IT SAYS WHICH RUN IT IS READING, AND THAT IS NOT A CONVENIENCE.** This printed nothing at
all until the report was finished, and on 2026-08-06 that cost the audit its only property
worth having: the job took forty minutes, held the workflow's concurrency group for all of
them, and looked from outside exactly like one wedged on a call that would never return.
Two people watched it and could not tell, and two hand dispatches were discarded by a
scheduled run while they waited. A line per run on stderr settles it -- a log that stops
advancing is hung and one that advances slowly is slow -- and the time each run took is
what says whether the answer is to make it faster or to stop asking it this way.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlparse

from pydantic import BeforeValidator, Field, model_validator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from edullm_platform.checkpoints import (
    MISSING_OBJECT_CODES,
    CheckpointState,
    CheckpointStore,
    inspect_checkpoint,
)
from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import IntentRecord
from edullm_platform.contracts.base import ContractModel, UtcTimestamp, require_ordered_sequence
from edullm_platform.contracts.bindings import GitHubLogin
from edullm_platform.contracts.identity import RunId
from edullm_platform.contracts.lifecycle import AttemptTerminalState
from edullm_platform.contracts.results import ResultManifest, output_prefix
from edullm_platform.contracts.workload import WorkloadCatalog

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Where the adjudications live. Under ``config/reports/`` rather than in ``config/`` itself,
#: and the subdirectory was the whole reason: ``tools/build_admission_lambda.py`` used to
#: glob ``config/*.yaml`` into both Lambda zips, so a file there cost a rebuild, an upload
#: and two release records every time it was edited. Each builder now names the files its
#: own handler reads, so the placement no longer decides that. It stays where it is because
#: it scopes a report rather than configuring the platform, and neither function reads it.
ACKNOWLEDGEMENTS_PATH = PROJECT_ROOT / "config" / "reports" / "checkpoint-acknowledgements.yaml"

EXIT_FOUND_SILENT_FAILURES = 1
EXIT_UNUSABLE = 2

CLI_TIMEOUT_SECONDS = 300

#: How the CLI renders the code S3 returned. ``checkpoints`` recognises a missing object by
#: the shape of the response rather than by a botocore class, which is the whole reason a
#: reader that is not holding boto3 can exist, so this is where that shape is rebuilt.
_ERROR_CODE = re.compile(r"An error occurred \(([^)]+)\)")


class ReportInputError(Exception):
    """The records could not be read, which is never the same as there being none."""


class ObjectMissing(Exception):
    """One key is not there, in the shape :mod:`edullm_platform.checkpoints` reads."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.response = {"Error": {"Code": code}}


class CheckpointAcknowledgement(ContractModel):
    """One run whose prefix somebody read and adjudicated, with their name against it.

    ``reason`` carries the length floor ``ImageScanException`` carries, and for the reason
    recorded there: "known issue" is not an adjudication, and the whole value of the entry is
    that a later reader can tell whether the prefix was understood or waved through. It is
    also the only thing standing between this list and a date cutoff written one run at a
    time.

    A run rather than a date, a profile or a team. Every wider unit acknowledges runs nobody
    has looked at, including ones submitted after it was written.
    """

    run_id: RunId
    reason: str = Field(min_length=40)
    recorded_by: GitHubLogin
    recorded_at: UtcTimestamp


class CheckpointAcknowledgements(ContractModel):
    """The adjudicated runs, as a file this repository reviews like any other change.

    A contract here in the tool rather than under ``contracts/``, because nothing on the
    platform's decision path reads it. Admission does not consult it, no lineage record
    carries it, and a run's outcome is the same whether or not it is named -- it scopes what
    this report holds against the build and nothing else. Putting it beside the other
    contracts would export a schema and enter both Lambdas' package closure for a file
    neither will ever open.
    """

    schema_version: Literal[1]
    acknowledgements: Annotated[
        tuple[CheckpointAcknowledgement, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(default=(), strict=False)

    @model_validator(mode="after")
    def validate_one_acknowledgement_per_run(self) -> Self:
        named = [entry.run_id for entry in self.acknowledgements]
        if len(set(named)) != len(named):
            raise ValueError("a run must not carry more than one acknowledgement")
        return self

    def reasons(self) -> dict[str, str]:
        return {entry.run_id: entry.reason for entry in self.acknowledgements}


class CommandLineObjectStore:
    """The reads :func:`inspect_checkpoint` makes, served by the AWS CLI rather than boto3.

    boto3 is not a dependency of this project, and ``checkpoints`` says why at length: it is
    imported by the admission validator, whose zip the release procedure exists to keep
    small. Taking a store through a Protocol is what that decision bought, and this is the
    thing it bought it for. Nothing here decides anything about a checkpoint; it fetches.

    Reads only. The Protocol names ``put_object`` and this refuses it, because a report that
    can write to the bucket it is auditing is a report that can create the evidence it finds.

    **EVERY CALL IS AN ``aws`` PROCESS, WHICH IS WHY THE LISTING ANSWERS AS MUCH AS IT CAN.**
    One CLI invocation costs about a second before it has spoken to S3 at all, and that is
    the whole cost model here: what makes this report slow is the number of processes, not
    the number of bytes. A listing already carries every key under the prefix with its size
    and its write time, so the reads that only want those are answered from it rather than
    paid for again. :func:`_newest_write` heads every object in a step directory for its
    ``LastModified``, and a distributed run's step directory holds one object per rank, so
    that alone was hundreds of processes per run.

    Measured against the audit's own account on 2026-08-06: eighty-three contracted runs took
    thirty-nine minutes at about twenty-one processes a run, nearly all of them heads on the
    twelve prefixes holding a checkpoint deep enough to describe. The job had been taking
    around forty minutes for at least eleven hours -- 38, 43, 45, 44, 39 across five
    consecutive runs -- and it grows with the intent tree, so it was on its way to being
    slower than the schedule it runs on. It also held the workflow's concurrency group for
    every one of those minutes, which is the part that made the audit undispatchable rather
    than merely slow, and audit.yml is where that half is fixed.
    """

    def __init__(self, *, profile: str | None = None, region: str | None = None) -> None:
        self._profile = profile
        self._region = region
        self._listings: dict[tuple[str, str], list[Mapping[str, Any]]] = {}

    def _call(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        command = ["aws", *arguments]
        if self._profile:
            command += ["--profile", self._profile]
        if self._region:
            command += ["--region", self._region]
        try:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=CLI_TIMEOUT_SECONDS,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ReportInputError(
                f"the AWS CLI could not run {' '.join(arguments[:2])}: {type(error).__name__}"
            ) from error

    def _refusal(self, arguments: Sequence[str], stderr: str) -> Exception:
        """A missing key as an absence, and everything else as a tool that cannot answer.

        THIS IS THE BRANCH THE REPORT'S HONESTY RESTS ON. An expired credential and an empty
        prefix both end a call early, and collapsing them turns a credentials problem into an
        accusation that somebody's twelve-hour run saved nothing. Only the codes S3 uses for
        an absent object become an absence; anything else, including a CLI that could not
        find the profile and printed no code at all, stops the report.
        """
        matched = _ERROR_CODE.search(stderr)
        code = matched.group(1) if matched else None
        if code is not None and code in MISSING_OBJECT_CODES:
            return ObjectMissing(code, stderr.strip())
        return ReportInputError(
            f"could not {' '.join(arguments[:2])}: {stderr.strip() or 'the CLI said nothing'}"
        )

    def _json(self, arguments: Sequence[str]) -> Mapping[str, Any]:
        finished = self._call([*arguments, "--output", "json"])
        if finished.returncode != 0:
            raise self._refusal(arguments, finished.stderr)
        if not finished.stdout.strip():
            return {}
        try:
            answer = json.loads(finished.stdout)
        except ValueError as error:
            raise ReportInputError(
                f"the AWS CLI answered {' '.join(arguments[:2])} with something that is not JSON"
            ) from error
        return answer if isinstance(answer, Mapping) else {}

    def list_objects_v2(self, **arguments: Any) -> Any:
        bucket = str(arguments["Bucket"])
        prefix = str(arguments["Prefix"])
        cached = self._listings.get((bucket, prefix))
        if cached is None:
            # Cached because inspect_checkpoint lists the same prefix a second time when it
            # holds no step directory, and the report asks for the count as well.
            answer = self._json(
                ["s3api", "list-objects-v2", "--bucket", bucket, "--prefix", prefix]
            )
            contents = answer.get("Contents") or []
            cached = [entry for entry in contents if isinstance(entry, Mapping)]
            self._listings[(bucket, prefix)] = cached
        # IsTruncated is stated rather than omitted, and it is the one line here that is
        # about the caller. The CLI follows the continuation itself and hands back one
        # merged answer -- measured against a thirteen-object prefix with --page-size 3 --
        # so this is always the whole listing. inspect_checkpoint asks for the next page
        # while a store says there is one, and a store that answered from a cache keyed on
        # the prefix while claiming truncation would hand back the same page for ever.
        return {"Contents": list(cached), "IsTruncated": False}

    def _listed(self, bucket: str, key: str) -> tuple[bool, Mapping[str, Any] | None]:
        """What an already-fetched listing says about one key, and whether one covers it.

        A listing here is complete for its prefix -- the CLI follows the continuation itself
        and ``list_objects_v2`` above says so -- so a cached listing whose prefix contains
        this key is authoritative about whether the key is there. That is the only reason
        an absence may be answered without asking S3, and it is worth stating because
        getting it wrong in the lenient direction would have this report accuse a run of
        saving nothing on the strength of a listing that had been truncated.

        Three answers rather than two, and the middle one is the one that matters.
        ``(False, None)`` is nobody has listed a prefix containing this key, so only S3
        knows. ``(True, None)`` is a listing covers it and does not hold it, so the object
        is not there. ``(True, entry)`` is what the listing said about it.
        """
        for (listed_bucket, prefix), contents in self._listings.items():
            if listed_bucket != bucket or not key.startswith(prefix):
                continue
            for entry in contents:
                if str(entry.get("Key", "")) == key:
                    return True, entry
            return True, None
        return False, None

    def head_object(self, **arguments: Any) -> Any:
        bucket = str(arguments["Bucket"])
        key = str(arguments["Key"])
        mode = arguments.get("ChecksumMode")
        # A checksum is the one thing a listing does not carry, so the digest verification
        # path still pays for its own call. Everything else a head answers here -- the write
        # time and the size -- was in the listing, and asking again buys the same numbers for
        # a second of process startup each.
        if not mode:
            covered, entry = self._listed(bucket, key)
            if covered and entry is None:
                raise ObjectMissing(
                    "NotFound",
                    f"An error occurred (NotFound) when calling the HeadObject operation: "
                    f"s3://{bucket}/{key} is not in the listing of its own prefix",
                )
            if entry is not None:
                return self._head_from(entry)
        call = ["s3api", "head-object", "--bucket", bucket, "--key", key]
        if mode:
            call += ["--checksum-mode", str(mode)]
        head = dict(self._json(call))
        written = head.get("LastModified")
        # The CLI renders a timestamp as text and checkpoints.py compares datetimes, so an
        # unconverted value reads there as a store that reports no write time at all.
        if isinstance(written, str):
            head["LastModified"] = datetime.fromisoformat(written)
        return head

    @staticmethod
    def _head_from(entry: Mapping[str, Any]) -> dict[str, Any]:
        """One listing entry in the shape a head returns, and no field it did not carry.

        ``ContentLength`` rather than ``Size`` because that is what a head is asked for, and
        nothing invented beside it: a head answers more than this, and a caller reaching for
        a field a listing has no equivalent of should find it absent rather than guessed at.
        """
        head: dict[str, Any] = {}
        written = entry.get("LastModified")
        if isinstance(written, str):
            written = datetime.fromisoformat(written)
        if isinstance(written, datetime):
            head["LastModified"] = written
        size = entry.get("Size")
        if isinstance(size, int) and not isinstance(size, bool):
            head["ContentLength"] = size
        tag = entry.get("ETag")
        if isinstance(tag, str):
            head["ETag"] = tag
        return head

    def get_object(self, **arguments: Any) -> Any:
        bucket = str(arguments["Bucket"])
        key = str(arguments["Key"])
        # The marker read is one of these per run and it almost always misses, because a
        # library-written checkpoint carries no marker of ours. A listing that covers the
        # prefix has already said the key is not there.
        covered, entry = self._listed(bucket, key)
        if covered and entry is None:
            raise ObjectMissing(
                "NoSuchKey",
                f"An error occurred (NoSuchKey) when calling the GetObject operation: "
                f"s3://{bucket}/{key} is not in the listing of its own prefix",
            )
        call = ["s3api", "get-object", "--bucket", bucket, "--key", key]
        with tempfile.TemporaryDirectory() as directory:
            landing = Path(directory) / "object"
            finished = self._call([*call, str(landing)])
            if finished.returncode != 0:
                raise self._refusal(call, finished.stderr)
            return {"Body": io.BytesIO(landing.read_bytes())}

    def put_object(self, **arguments: Any) -> Any:
        raise ReportInputError("this report reads the bucket and must never write to it")


@dataclass(frozen=True)
class RunCheckpointState:
    """One run's prefix, as the checkpoint reader found it."""

    run_id: str
    team: str
    workload_profile: str
    prefix: str
    objects: int
    state: CheckpointState
    detail: str
    #: What the run's result record says it ended as, or ``None`` for a run with no result
    #: record. ``None`` also covers the case where no result records were read at all.
    outcome: AttemptTerminalState | None = None
    #: Whether the outcome above was looked for. False means no ``result/`` tree was present,
    #: which is not the same as a run having no result record, and the two must not be
    #: collapsed: one is an absent permission and the other is a run that has not finished.
    outcome_known: bool = False
    #: Why this run's prefix has been adjudicated, or ``None`` for the ordinary case of a run
    #: nobody has written down. The reason rather than a flag, because it is printed beside
    #: the run: an acknowledgement whose justification is not in the report is one nobody can
    #: check, and this list is only defensible while every entry can be read.
    acknowledged: str | None = None

    @property
    def judged(self) -> bool:
        """Whether this run's prefix is held against it.

        A run is judged when the platform recorded it as a success, which is the state this
        report exists to contradict. With no result records read at all, every run is judged,
        because a report that quietly stopped asking would be the failure it looks for.
        """
        if not self.outcome_known:
            return True
        return self.outcome is AttemptTerminalState.SUCCEEDED

    @property
    def held_against_the_build(self) -> bool:
        """Whether this run's prefix is allowed to fail the audit.

        Two separate reasons not to, kept separate. ``judged`` is about whether the question
        applies at all -- a run that failed has already reported its own failure. This is
        about a run the question applies to, whose answer somebody has read and written down.
        Collapsing them would let an acknowledgement quietly change what the report *asks*
        rather than only what it holds against the build.
        """
        return self.judged and self.acknowledged is None

    @property
    def saved_nothing(self) -> bool:
        return self.state is CheckpointState.ABSENT

    @property
    def is_loadable(self) -> bool:
        return self.state is CheckpointState.COMMITTED

    @property
    def wrote_a_foreign_checkpoint(self) -> bool:
        """Wrote a complete checkpoint that OLMo-core's loader does not read.

        Kept out of both categories below rather than folded into either, because it is not
        a fault and the other two are. A HuggingFace ``Trainer`` checkpoint resumes through
        its own trainer perfectly well; what it does not do is satisfy this platform's retry
        path, which reruns the same command from the start.

        Before this existed such a run landed in "wrote nothing" and was told it had
        probably forgotten `--save-folder "$EDULLM_CHECKPOINT_DIR"`. It had not. That is a
        false accusation aimed at whichever migration succeeds first, and post-training's
        is the one in flight.
        """
        return self.state is CheckpointState.FOREIGN

    @property
    def wrote_something_unloadable(self) -> bool:
        """Wrote, and a resume would still start from step zero.

        The state that was invisible while this counted objects, and the one that costs the
        most: the run looks like it saved and did not.
        """
        return (
            not self.saved_nothing and not self.is_loadable and not self.wrote_a_foreign_checkpoint
        )


def _load_intents(directory: Path) -> list[IntentRecord]:
    root = directory / "intent"
    if not root.is_dir():
        raise ReportInputError(f"no intent/ directory under {directory}")
    records: list[IntentRecord] = []
    unparsed = 0
    for path in sorted(root.rglob("*.json")):
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise ReportInputError(f"{path} is not readable JSON: {error}") from error
        # A record is stored as a JSON string holding JSON, because the state machine writes
        # the handler's canonical bytes rather than re-encoding them.
        if isinstance(loaded, str):
            loaded = json.loads(loaded)
        if not isinstance(loaded, dict):
            continue
        try:
            records.append(IntentRecord.model_validate(loaded))
        except ValueError:
            # Counted rather than dropped. A store producing records this tree cannot read
            # is a defect in the recorder, and a report that quietly described the readable
            # subset would hide exactly that.
            unparsed += 1
    if unparsed:
        print(
            f"note: {unparsed} intent record(s) did not parse against the current contract "
            "and were left out of this report",
            file=sys.stderr,
        )
    return records


def _load_outcomes(directory: Path) -> dict[str, AttemptTerminalState] | None:
    """How each run ended, or ``None`` when the result records were not read at all.

    ``None`` rather than an empty mapping, because the two mean opposite things. An empty
    mapping is "these runs finished and none of them succeeded"; ``None`` is "nobody looked",
    and a report that treated the second as the first would pass every night by knowing less.
    """
    root = directory / "result"
    if not root.is_dir():
        return None
    outcomes: dict[str, AttemptTerminalState] = {}
    for path in sorted(root.rglob("*.json")):
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise ReportInputError(f"{path} is not readable JSON: {error}") from error
        if isinstance(loaded, str):
            loaded = json.loads(loaded)
        if not isinstance(loaded, dict):
            continue
        try:
            record = ResultManifest.model_validate(loaded)
        except ValueError:
            # Left out rather than guessed at. A result this tree cannot read says nothing
            # about how the run ended, and the run stays unjudged, which is the safe way to
            # be wrong here: it is listed and reported on, just not held against the build.
            continue
        outcomes[record.run_id] = AttemptTerminalState(record.outcome)
    return outcomes


def _load_acknowledgements(path: Path) -> CheckpointAcknowledgements:
    """The adjudicated runs, or none of them when the file is not there.

    An absent file is an empty list rather than an error, because that is the state this
    repository should be in and the state a fresh checkout of it may legitimately be in. An
    unreadable one stops the report: a file that exists and does not parse is somebody having
    written an entry wrongly, and treating that as "no acknowledgements" would turn a typo
    into a red job whose cause is a YAML error nobody is shown.
    """
    if not path.is_file():
        return CheckpointAcknowledgements(schema_version=1)
    try:
        return load_yaml(path, CheckpointAcknowledgements)
    except (OSError, TypeError, ValueError) as error:
        raise ReportInputError(f"{path} is not a readable acknowledgement list: {error}") from error


def _objects_under(store: CheckpointStore, prefix: str) -> int:
    location = urlparse(prefix)
    answer = store.list_objects_v2(Bucket=location.netloc, Prefix=location.path.lstrip("/"))
    return len(answer.get("Contents") or [])


def checkpoint_states(
    records: Sequence[IntentRecord],
    catalog: WorkloadCatalog,
    *,
    store: CheckpointStore | None = None,
    profile: str | None = None,
    region: str | None = None,
    outcomes: Mapping[str, AttemptTerminalState] | None = None,
    acknowledgements: Mapping[str, str] | None = None,
) -> list[RunCheckpointState]:
    """One entry per run whose profile promised checkpoints, newest last.

    Runs whose profile carries no checkpoint contract are skipped rather than reported as
    saving nothing, because for them that is the correct outcome and mixing the two would
    make the report noise.

    ``outcomes`` scopes which of them are judged rather than which appear. Every contracted
    run is still inspected and still listed, so nothing leaves the report by ending badly.

    ``acknowledgements`` scopes narrower still: an adjudicated run is inspected, listed and
    read out of the bucket exactly as before, and only the exit code treats it differently.
    Nothing here decides whether a prefix is a finding; the list decides whether the finding
    is news.
    """
    reader = store if store is not None else CommandLineObjectStore(profile=profile, region=region)
    known = {workload.name for workload in catalog.workloads}
    contracted = {
        workload.name for workload in catalog.workloads if workload.checkpoint is not None
    }
    # A run submitted before the workload profiles were renamed names one the catalog no
    # longer has. Skipping it silently would be the same defect this tool exists to find --
    # a run quietly left out of a report about runs being quietly left out -- so the names
    # are collected and said out loud.
    retired: set[str] = set()
    states: list[RunCheckpointState] = []
    # Chosen before the loop rather than inside it so the progress line below can say how
    # many runs there are. A count arriving only at the end is what made a slow job and a
    # wedged one read the same.
    asked: list[IntentRecord] = []
    for record in records:
        if record.manifest.workload_profile not in known:
            retired.add(record.manifest.workload_profile)
            continue
        if record.manifest.workload_profile not in contracted:
            continue
        asked.append(record)
    started = time.monotonic()
    for number, record in enumerate(asked, start=1):
        manifest = record.manifest
        prefix = output_prefix(team=manifest.team, run_id=record.run_id) + "checkpoints/"
        # SAID BEFORE THE RUN IS READ RATHER THAN AFTER, WHICH IS THE WHOLE POINT. This
        # printed nothing until the report was finished, so a job forty minutes into a
        # bucket looked identical from outside to one wedged on a call that would never
        # return -- and on 2026-08-06 two people had to guess which they were watching. A
        # line naming the run it is about to read distinguishes them: a log that stops
        # advancing is hung and one that advances slowly is slow. Every line here is
        # timestamped by the runner, so the rate is readable without this counting anything.
        print(
            f"note: reading {number}/{len(asked)} {record.run_id} "
            f"({time.monotonic() - started:.0f}s elapsed)",
            file=sys.stderr,
            flush=True,
        )
        # LISTED BEFORE IT IS INSPECTED, WHICH BUYS THE ONE READ THE INSPECTION MAKES TOO
        # EARLY TO COVER ITSELF. inspect_checkpoint reads the marker first, deliberately, so
        # that a prefix with none costs one call rather than a description; but a marker read
        # before anything has been listed is a call the store cannot answer, and it misses on
        # every library-written checkpoint, which is all of them. One listing first and the
        # store already knows the marker is not there. Everything after it is covered
        # whichever way round these two go, because the inspection lists before it heads.
        objects = _objects_under(reader, prefix)
        inspected = inspect_checkpoint(reader, prefix=prefix)
        states.append(
            RunCheckpointState(
                run_id=record.run_id,
                team=manifest.team,
                workload_profile=manifest.workload_profile,
                prefix=prefix,
                objects=objects,
                state=inspected.state,
                detail=inspected.detail,
                outcome=None if outcomes is None else outcomes.get(record.run_id),
                outcome_known=outcomes is not None,
                acknowledged=None if acknowledgements is None else acknowledgements.get(
                    record.run_id
                ),
            )
        )
    if retired:
        print(
            "note: skipped run(s) naming workload profiles the catalog no longer has "
            f"({', '.join(sorted(retired))}). Those names were renamed and the stored "
            "records keep the old ones deliberately; see config/workload-catalog.yaml.",
            file=sys.stderr,
        )
    return states


def render(
    states: Sequence[RunCheckpointState],
    *,
    acknowledgements_covering_nothing: Sequence[str] = (),
) -> str:
    if not states:
        return (
            "No run has been submitted under a workload profile that promises checkpoints, "
            "so there is nothing here to be wrong.\n"
        )

    judged = [state for state in states if state.held_against_the_build]
    adjudicated = [state for state in states if state.judged and state.acknowledged is not None]
    unjudged = [state for state in states if not state.judged]
    silent = [state for state in judged if state.saved_nothing]
    unloadable = [state for state in judged if state.wrote_something_unloadable]
    foreign = [state for state in judged if state.wrote_a_foreign_checkpoint]
    loadable = [state for state in judged if state.is_loadable]
    headline = (
        f"{len(states)} run(s) were submitted under a profile carrying a checkpoint "
        f"contract. Of the {len(judged)} the platform recorded as finishing successfully, "
        f"{len(silent)} wrote nothing, {len(unloadable)} wrote something no loader will "
        f"accept, and {len(loadable)} can be resumed from."
    )
    if foreign:
        headline += (
            f" A further {len(foreign)} wrote a complete checkpoint in another trainer's "
            "layout; they saved correctly and this platform cannot resume them."
        )
    if adjudicated:
        headline += (
            f" A further {len(adjudicated)} finished successfully and have been read and "
            "adjudicated; they are listed with their reasons and are not held against this "
            "report."
        )
    if unjudged:
        headline += (
            f" The other {len(unjudged)} did not finish successfully and are listed at the "
            "end without being held against anything."
        )
    lines = ["# Runs that promised a checkpoint", "", headline, ""]

    if silent:
        explanation = (
            "Each of these declared a checkpoint contract and left its prefix empty. The "
            "usual cause is a training command that did not pass "
            '`--save-folder "$EDULLM_CHECKPOINT_DIR"`, in which case the checkpoints were '
            "written to local disk on a machine that no longer exists. Submission now "
            "refuses that command, so a run appearing here was either submitted before that "
            "guard or carries `EDULLM_CHECKPOINT_CHECK=waived`, and the manifest says which."
        )
        lines += [
            "## Wrote nothing",
            "",
            explanation,
            "",
            "| Run | Team | Profile |",
            "| --- | --- | --- |",
        ]
        lines += [
            f"| `{state.run_id}` | {state.team} | {state.workload_profile} |" for state in silent
        ]
        lines.append("")

    if unloadable:
        explanation = (
            "These wrote to the right place and a resume would still start from step zero, "
            "which is the state a count of objects reads as healthy. A step directory "
            "holding only `train/rank0.pt` is rank 0's trainer state with no weights and no "
            "optimizer beside it, so OLMo-core's own `dir_is_checkpoint` refuses it and so "
            "does the resume path."
        )
        lines += [
            "## Wrote something that will not load",
            "",
            explanation,
            "",
            "| Run | Team | Objects | What is there |",
            "| --- | --- | --- | --- |",
        ]
        lines += [
            f"| `{state.run_id}` | {state.team} | {state.objects} | {state.detail} |"
            for state in unloadable
        ]
        lines.append("")

    if foreign:
        explanation = (
            "These saved correctly, in a layout OLMo-core's loader does not read. That is "
            "ordinarily a HuggingFace `Trainer`, which writes `checkpoint-{step}/` where "
            "OLMo-core writes `step{N}/`, and which resumes from its own checkpoints "
            "through `resume_from_checkpoint`. Nothing here is wrong with the run. What is "
            "worth knowing is that this platform's retry reruns the submitted command from "
            "the start, so a second attempt does not continue from these unless the "
            "command itself resumes."
        )
        lines += [
            "## Wrote a checkpoint this platform cannot resume",
            "",
            explanation,
            "",
            "| Run | Team | Objects | What is there |",
            "| --- | --- | --- | --- |",
        ]
        lines += [
            f"| `{state.run_id}` | {state.team} | {state.objects} | {state.detail} |"
            for state in foreign
        ]
        lines.append("")

    if loadable:
        lines += [
            "## Wrote a checkpoint that will load",
            "",
            "| Run | Team | Objects | What is there |",
            "| --- | --- | --- | --- |",
        ]
        lines += [
            f"| `{state.run_id}` | {state.team} | {state.objects} | {state.detail} |"
            for state in loadable
        ]
        lines.append("")

    if adjudicated:
        explanation = (
            "Each of these has nothing a resume could load, has been read, and cannot be "
            "repaired -- the run is over and its prefix is what it is. They are named one "
            "run at a time in `config/reports/checkpoint-acknowledgements.yaml`, with the "
            "reason beside each, so that a permanently red job does not end up hiding the "
            "next finding. A run that is not named there is judged exactly as before."
        )
        lines += [
            "## Read and adjudicated",
            "",
            explanation,
            "",
            "| Run | Team | What is there | Why it is not held against this report |",
            "| --- | --- | --- | --- |",
        ]
        lines += [
            f"| `{state.run_id}` | {state.team} | {state.detail} | {state.acknowledged} |"
            for state in adjudicated
        ]
        lines.append("")

    if acknowledgements_covering_nothing:
        named = ", ".join(f"`{run_id}`" for run_id in acknowledgements_covering_nothing)
        lines += [
            "## Acknowledgements that cover nothing",
            "",
            (
                f"{named} is acknowledged and is not a finding: either the run has a "
                "checkpoint that loads, it is not recorded as a success, or no intent record "
                "here names it. The entry is doing nothing and should be removed, because a "
                "list that only grows stops describing anything. This is a note rather than "
                "a failure -- a stale entry hides no run."
            ),
            "",
        ]

    if unjudged:
        explanation = (
            "These never reached the state this report is about. A run the platform recorded "
            "as failed has already said so, with an exit code, and one with no result record "
            "has not finished. Neither is a checkpoint that went missing, and an empty prefix "
            "under a run that died before its first interval is the expected shape."
        )
        lines += [
            "## Not asked about",
            "",
            explanation,
            "",
            "| Run | Team | Recorded as | Objects |",
            "| --- | --- | --- | --- |",
        ]
        lines += [
            f"| `{state.run_id}` | {state.team} | "
            f"{state.outcome.value if state.outcome else 'no result record'} | "
            f"{state.objects} |"
            for state in unjudged
        ]
        lines.append("")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--lineage-root",
        type=Path,
        required=True,
        help="a directory holding an intent/ tree, synced from the lineage bucket",
    )
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
    parser.add_argument(
        "--acknowledgements",
        type=Path,
        default=ACKNOWLEDGEMENTS_PATH,
        help="runs that have been read and adjudicated, which are reported but not held "
        "against the exit code",
    )
    parser.add_argument("--output", type=Path, help="write the report here rather than to stdout")
    parser.add_argument("--profile")
    parser.add_argument("--region")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    try:
        records = _load_intents(options.lineage_root)
        outcomes = _load_outcomes(options.lineage_root)
        catalog = load_yaml(options.config_dir / "workload-catalog.yaml", WorkloadCatalog)
        acknowledged = _load_acknowledgements(options.acknowledgements).reasons()
        states = checkpoint_states(
            records,
            catalog,
            profile=options.profile,
            region=options.region,
            outcomes=outcomes,
            acknowledgements=acknowledged,
        )
    except ReportInputError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_UNUSABLE

    # An entry earns its place by covering a finding. One that covers a run which now loads,
    # or which this lineage root does not contain, is reported so it can be removed; it is
    # not a failure, because a stale entry conceals nothing.
    covering_something = {
        state.run_id for state in states if state.judged and not state.is_loadable
    }
    report = render(
        states,
        acknowledgements_covering_nothing=sorted(set(acknowledged) - covering_something),
    )
    if options.output:
        options.output.write_text(report, encoding="utf-8")
    else:
        print(report, end="")

    # A non-zero exit so this can gate something without being rewritten. It is not an error
    # in the tool; it is the tool having found what it looks for. Both failing states count:
    # a run that wrote a fragment is no more resumable than one that wrote nothing, and the
    # fragment is the one nothing else on the platform reports. Only judged runs count, since
    # a run that is recorded as having failed is one the platform already reported, and only
    # unacknowledged ones, since a run somebody has already read and written down is not what
    # this job is asking about the next morning.
    return (
        EXIT_FOUND_SILENT_FAILURES
        if any(not state.is_loadable for state in states if state.held_against_the_build)
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
