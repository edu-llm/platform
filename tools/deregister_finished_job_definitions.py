"""Retire the per-run Batch job definitions that accumulate one per submission, for ever.

**Every submission registers a job definition and nothing has ever deregistered one.**
Measured in the account on 2026-08-02: 58 ACTIVE definitions named
``sbsandbox-intern-edullm-run_<uuid>``, the oldest from 2026-07-30 and the newest four
minutes old, 47 of them created on a single day. Every one is at revision 1, because each
run registers a fresh *name* rather than a new revision of a shared one, so the growth is in
the name count and no revision-retention setting bounds it.

**They exist for one reason and it is a real one.** ``SubmitJob`` takes overrides for the
command, the environment and the resource requirements, and takes no override for the
image. A run's image is a digest pinned to its own commit, so the only place that digest can
be stated is a job definition of its own. Sharing one definition per shape would mean every
run on that shape ran whatever image the last deploy pinned, which is the provenance model
inverted.

**THE NAME IN THE PLAN IS WRONG AND IT IS THE ONE DETAIL THAT DECIDES WHETHER THIS WORKS.**
The build dispatch describes them as ``run_<uuid>``. Nothing in the account is named that. A
sweeper matching ``run_`` at the start of the name matches zero definitions and reports a
clean account for ever, which is worse than not having written it. The real name is
``job_definition_name()`` in ``edullm_platform.execution``, which is the run id under
``SANDBOX_RESOURCE_PREFIX``, and this module imports that function rather than restating the
pattern so the two cannot drift.

**Why a sweeper rather than the lifecycle recorder.** The dispatch names both as acceptable
owners. The recorder is the tidier one and costs more than it looks: it is the component
whose failure loses the event, the attempt and the result for a run that demonstrably
happened, it holds four ``PutObject`` grants and one narrow listing and no other client at
all, and giving it a Batch client plus ``batch:DeregisterJobDefinition`` widens the blast
radius of the one thing that must not throw. A definition surviving an extra day costs
nothing; a lineage record lost to a deregistration error cannot be recovered. So this runs
beside the other account-reading nightlies, against credentials that already exist.

**What it refuses to do, which is most of the design.**

It never deregisters a definition whose run has not finished. Batch documents that a running
job survives its definition being deregistered, and this does not rely on that: a definition
is retired only once the lineage store holds a ``result/`` record for its run, which is this
platform's own written statement that the run reached a terminal state. A run still going,
a run whose recorder never fired, and a run submitted a minute ago are all left alone.

It never deregisters anything not named by ``job_definition_name()``. The shared per-shape
definitions from ``config/execution-targets.yaml``, and the out-of-band ones like
``edullm-validator``, are somebody else's to manage. ``edullm-validator`` is at ten live
ACTIVE revisions and ``edullm-reservoir-ingest`` at seven, which is a second accumulation
pattern on definitions this platform did not register and does not name; it is reported and
not touched.

**It reports and does not act unless told to.** ``--apply`` is required to deregister
anything, matching every other tool here that can reach the account. A dry run prints the
same table and changes nothing, so the first thing anybody does with this is see the list.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from edullm_platform.execution import SANDBOX_RESOURCE_PREFIX

#: The lineage prefix whose presence means a run reached a terminal state.
#:
#: ``result/`` rather than ``events/`` or ``attempt/``, because a result record is written
#: once per run at the end and the other two are written throughout. Reading the wrong one
#: would retire a definition underneath a job that is between attempts.
RESULT_PREFIX = "result/"

#: What a per-run definition is called, as a prefix rather than the whole name.
#:
#: ``job_definition_name(run_id)`` is ``f"{SANDBOX_RESOURCE_PREFIX}{run_id}"`` and every run
#: id begins ``run_``, so this is that function's output with the variable part removed. The
#: prefix is derived from the same constant the submitter uses, so a change to the resource
#: prefix moves both together.
DEFINITION_PREFIX = f"{SANDBOX_RESOURCE_PREFIX}run_"


class ReportInputError(RuntimeError):
    """Something this tool was asked to read is not there or is not what it claims."""


class BatchClient(Protocol):
    def describe_job_definitions(self, **arguments: Any) -> Any: ...

    def deregister_job_definition(self, **arguments: Any) -> Any: ...


class ObjectLister(Protocol):
    def list_objects_v2(self, **arguments: Any) -> Any: ...


@dataclass(frozen=True)
class Definition:
    """One registered per-run job definition."""

    name: str
    revision: int
    arn: str

    @property
    def run_id(self) -> str:
        return self.name.removeprefix(SANDBOX_RESOURCE_PREFIX)


def active_run_definitions(batch: BatchClient) -> list[Definition]:
    """Every ACTIVE definition this platform registered for one run.

    Paged to the end. ``describe_job_definitions`` answers a hundred at a time and the
    account already holds more than that in total, so a single call would silently describe
    a prefix of the list and this would report a clean account while the rest accumulated.
    """
    definitions: list[Definition] = []
    arguments: dict[str, Any] = {"status": "ACTIVE", "maxResults": 100}
    while True:
        answer = batch.describe_job_definitions(**arguments)
        for entry in answer.get("jobDefinitions") or []:
            name = str(entry.get("jobDefinitionName", ""))
            if not name.startswith(DEFINITION_PREFIX):
                continue
            revision = entry.get("revision")
            definitions.append(
                Definition(
                    name=name,
                    revision=int(revision) if isinstance(revision, int) else 1,
                    arn=str(entry.get("jobDefinitionArn", "")),
                )
            )
        token = answer.get("nextToken")
        if not isinstance(token, str) or not token:
            return definitions
        arguments["nextToken"] = token


def runs_with_a_result(lister: ObjectLister, *, lineage_bucket: str) -> set[str]:
    """Every run the lineage store records as finished.

    The key under ``result/`` is the run id, which is the same join everything else here
    uses. A run absent from this set has not finished as far as any record goes, and its
    definition is left alone whatever its age.
    """
    finished: set[str] = set()
    arguments: dict[str, Any] = {"Bucket": lineage_bucket, "Prefix": RESULT_PREFIX}
    while True:
        answer = lister.list_objects_v2(**arguments)
        for entry in answer.get("Contents") or []:
            key = str(entry.get("Key", ""))
            name = key.removeprefix(RESULT_PREFIX).removesuffix(".json")
            if name.startswith("run_"):
                finished.add(name)
        token = answer.get("NextContinuationToken")
        if not answer.get("IsTruncated") or not isinstance(token, str) or not token:
            return finished
        arguments["ContinuationToken"] = token


def retirable(
    definitions: Sequence[Definition],
    *,
    finished: set[str],
) -> tuple[list[Definition], list[Definition]]:
    """Split what may be retired from what must be left, and keep both.

    Returning the kept ones rather than filtering them away is what makes a dry run worth
    reading. "58 definitions, 51 retirable" and "58 definitions, 0 retirable" are different
    accounts, and a tool that printed only the first list would look identical in both.
    """
    retire = [entry for entry in definitions if entry.run_id in finished]
    keep = [entry for entry in definitions if entry.run_id not in finished]
    return retire, keep


def deregister(batch: BatchClient, definitions: Sequence[Definition]) -> Iterator[tuple[str, str]]:
    """Deregister each, yielding what happened, and never stopping on one failure.

    A definition already gone, or one another sweeper took between the listing and here, is
    an ordinary outcome rather than an error: this is idempotent by construction because the
    listing only returns ACTIVE ones and deregistration is the only thing that changes that.
    Raising on the first would leave the rest of a backlog in place for the sake of a race.
    """
    for entry in definitions:
        try:
            batch.deregister_job_definition(jobDefinition=f"{entry.name}:{entry.revision}")
        except Exception as error:  # noqa: BLE001
            yield entry.name, f"refused: {error}"
        else:
            yield entry.name, "deregistered"


def render(
    retire: Sequence[Definition],
    keep: Sequence[Definition],
    *,
    applied: bool,
) -> str:
    total = len(retire) + len(keep)
    if not total:
        return (
            f"No ACTIVE job definition is named {DEFINITION_PREFIX}*, so this platform has "
            "registered none or they have all been retired.\n"
        )
    verb = "Deregistered" if applied else "Would deregister"
    lines = [
        "# Per-run Batch job definitions",
        "",
        (
            f"{total} ACTIVE definition(s) are named {DEFINITION_PREFIX}*. {verb} "
            f"{len(retire)} whose run has a result record, and left {len(keep)} whose run "
            "has not finished or was never recorded as finishing."
        ),
        "",
    ]
    if retire:
        lines += ["## Retired", "", "| Definition | Run |", "| --- | --- |"]
        lines += [f"| `{entry.name}` | `{entry.run_id}` |" for entry in sorted(retire, key=_by_name)]
        lines.append("")
    if keep:
        lines += [
            "## Left alone",
            "",
            (
                "No result record, so as far as anything written down goes these runs have "
                "not finished. A definition here that is weeks old is worth chasing: it "
                "means a terminal event was never projected, which is a gap in the recorder "
                "rather than in this."
            ),
            "",
            "| Definition | Run |",
            "| --- | --- |",
        ]
        lines += [f"| `{entry.name}` | `{entry.run_id}` |" for entry in sorted(keep, key=_by_name)]
        lines.append("")
    return "\n".join(lines)


def _by_name(entry: Definition) -> str:
    return entry.name


def _lineage_bucket(uri: str) -> str:
    location = urlparse(uri)
    if location.scheme == "s3" and location.netloc:
        return location.netloc
    if "/" in uri or not uri:
        raise ReportInputError(f"a lineage bucket must be a name or an s3:// URI, not {uri!r}")
    return uri


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--lineage-bucket",
        default="sbsandbox-intern-edullm-lineage",
        help="where result records live; a run with one here has finished",
    )
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--profile", default=None)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually deregister; without it this reports and changes nothing",
    )
    arguments = parser.parse_args(argv)

    import boto3

    session = boto3.Session(profile_name=arguments.profile, region_name=arguments.region)
    batch = session.client("batch")
    lister = session.client("s3")

    definitions = active_run_definitions(batch)
    finished = runs_with_a_result(lister, lineage_bucket=_lineage_bucket(arguments.lineage_bucket))
    retire, keep = retirable(definitions, finished=finished)

    if arguments.apply:
        for name, outcome in deregister(batch, retire):
            print(f"{name}: {outcome}", file=sys.stderr)

    print(render(retire, keep, applied=arguments.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
