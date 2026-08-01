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
which is a contract change that regenerates four proof bundles, and ``s3:ListBucket`` for a
Lambda role that today holds four ``PutObject`` grants and deliberately nothing else. Both
are defensible and neither should be paid at the same time as finding out whether the check
is worth having. This runs from a laptop or from the nightly workflow against credentials
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
lineage bucket and the nightly reader role does not hold it yet, so when no ``result/`` tree
is present every contracted run is judged, as before. Nothing is silently let through by a
sync that did not happen.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from edullm_platform.checkpoints import (
    MISSING_OBJECT_CODES,
    CheckpointState,
    CheckpointStore,
    inspect_checkpoint,
)
from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import IntentRecord
from edullm_platform.contracts.lifecycle import AttemptTerminalState
from edullm_platform.contracts.results import ResultManifest, output_prefix
from edullm_platform.contracts.workload import WorkloadCatalog

PROJECT_ROOT = Path(__file__).resolve().parent.parent

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


class CommandLineObjectStore:
    """The reads :func:`inspect_checkpoint` makes, served by the AWS CLI rather than boto3.

    boto3 is not a dependency of this project, and ``checkpoints`` says why at length: it is
    imported by the admission validator, whose zip the release procedure exists to keep
    small. Taking a store through a Protocol is what that decision bought, and this is the
    thing it bought it for. Nothing here decides anything about a checkpoint; it fetches.

    Reads only. The Protocol names ``put_object`` and this refuses it, because a report that
    can write to the bucket it is auditing is a report that can create the evidence it finds.
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

    def head_object(self, **arguments: Any) -> Any:
        call = ["s3api", "head-object", "--bucket", str(arguments["Bucket"])]
        call += ["--key", str(arguments["Key"])]
        mode = arguments.get("ChecksumMode")
        if mode:
            call += ["--checksum-mode", str(mode)]
        head = dict(self._json(call))
        written = head.get("LastModified")
        # The CLI renders a timestamp as text and checkpoints.py compares datetimes, so an
        # unconverted value reads there as a store that reports no write time at all.
        if isinstance(written, str):
            head["LastModified"] = datetime.fromisoformat(written)
        return head

    def get_object(self, **arguments: Any) -> Any:
        call = ["s3api", "get-object", "--bucket", str(arguments["Bucket"])]
        call += ["--key", str(arguments["Key"])]
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
    def saved_nothing(self) -> bool:
        return self.state is CheckpointState.ABSENT

    @property
    def is_loadable(self) -> bool:
        return self.state is CheckpointState.COMMITTED

    @property
    def wrote_something_unloadable(self) -> bool:
        """Wrote, and a resume would still start from step zero.

        The state that was invisible while this counted objects, and the one that costs the
        most: the run looks like it saved and did not.
        """
        return not self.saved_nothing and not self.is_loadable


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
) -> list[RunCheckpointState]:
    """One entry per run whose profile promised checkpoints, newest last.

    Runs whose profile carries no checkpoint contract are skipped rather than reported as
    saving nothing, because for them that is the correct outcome and mixing the two would
    make the report noise.

    ``outcomes`` scopes which of them are judged rather than which appear. Every contracted
    run is still inspected and still listed, so nothing leaves the report by ending badly.
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
    for record in records:
        manifest = record.manifest
        if manifest.workload_profile not in known:
            retired.add(manifest.workload_profile)
            continue
        if manifest.workload_profile not in contracted:
            continue
        prefix = output_prefix(team=manifest.team, run_id=record.run_id) + "checkpoints/"
        inspected = inspect_checkpoint(reader, prefix=prefix)
        states.append(
            RunCheckpointState(
                run_id=record.run_id,
                team=manifest.team,
                workload_profile=manifest.workload_profile,
                prefix=prefix,
                objects=_objects_under(reader, prefix),
                state=inspected.state,
                detail=inspected.detail,
                outcome=None if outcomes is None else outcomes.get(record.run_id),
                outcome_known=outcomes is not None,
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


def render(states: Sequence[RunCheckpointState]) -> str:
    if not states:
        return (
            "No run has been submitted under a workload profile that promises checkpoints, "
            "so there is nothing here to be wrong.\n"
        )

    judged = [state for state in states if state.judged]
    unjudged = [state for state in states if not state.judged]
    silent = [state for state in judged if state.saved_nothing]
    unloadable = [state for state in judged if state.wrote_something_unloadable]
    loadable = [state for state in judged if state.is_loadable]
    headline = (
        f"{len(states)} run(s) were submitted under a profile carrying a checkpoint "
        f"contract. Of the {len(judged)} the platform recorded as finishing successfully, "
        f"{len(silent)} wrote nothing, {len(unloadable)} wrote something no loader will "
        f"accept, and {len(loadable)} can be resumed from."
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
            "written to local disk on a machine that no longer exists."
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
        states = checkpoint_states(
            records,
            catalog,
            profile=options.profile,
            region=options.region,
            outcomes=outcomes,
        )
    except ReportInputError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_UNUSABLE

    report = render(states)
    if options.output:
        options.output.write_text(report, encoding="utf-8")
    else:
        print(report, end="")

    # A non-zero exit so this can gate something without being rewritten. It is not an error
    # in the tool; it is the tool having found what it looks for. Both failing states count:
    # a run that wrote a fragment is no more resumable than one that wrote nothing, and the
    # fragment is the one nothing else on the platform reports. Only judged runs count, since
    # a run that is recorded as having failed is one the platform already reported.
    return (
        EXIT_FOUND_SILENT_FAILURES
        if any(not state.is_loadable for state in states if state.judged)
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
