"""Where the three records of a run disagree, which is the only part of a union worth reading.

Three systems each hold a partial account of what this platform has done. Weights and Biases
holds what a run logged, the account holds what a run ran on, and the outputs bucket holds
what a run left behind. Every one of them is authoritative about its own half and silent
about the other two, and nothing joins them, so a run can be present in one and missing from
the next two for weeks without anybody being told.

**THE DISAGREEMENTS ARE THE PRODUCT AND THE AGREEMENT IS A COUNT.** A board that lists every
run and reports that they all match is a page nobody opens twice. Three disagreements are
worth waking up to and each of them is a different failure with a different owner.

| Finding | What it means |
| --- | --- |
| In the account, not in W&B | Somebody is burning GPU hours nobody can see a loss curve for |
| In W&B, nothing under its output prefix | The run looks successful and produced no artifact |
| An output prefix with no W&B run | A result nobody can trace back to a config |

**THE JOIN KEY IS THE RUN ID AND W&B CARRIES IT IN THREE SPELLINGS, WHICH IS ITSELF A
FINDING.** The account carries the run id in the ``edullm:run-id`` tag and the bucket carries
it as a path segment, and both are set by this platform, so both are exact. W&B is the
exception because nothing on the platform sets a W&B run's name; the workload does, in its
own training command. Read live on 2026-08-02, the eduLLM entity held 218 runs across 17
projects. Of those, 13 named themselves after the run id and 2 of the 13 also carry it under
a ``run_id`` config key, 2 more carry it in a display name with a ``-died`` suffix glued on,
6 are named the literal string ``$EDULLM_RUN_ID`` because a command quoted the variable so
the shell never expanded it, and the remaining 197 carry no run id anywhere. The 8 in the
middle two groups logged perfectly well and are unreachable by run id, and they stand for 5
platform runs, so a board matching only an exact name would report 51 runs as unlogged spend
where 46 is the truth and would accuse five submitters of something that did not happen.

So the match is graded rather than tightened. An exact name is a ``NAMED`` match. A run id
recovered from anywhere else in the run's record is a ``DERIVED`` match, which counts as
logged and is also reported in a section of its own, because a run that cannot be found in
W&B by its run id is one nobody triaging at 05:00 will find at all. A record carrying two
different run ids is joined to neither and reported, since guessing which one owns it would
put one team's loss curve under another team's run.

**A CATEGORY IS NOT REPORTED UNLESS BOTH SOURCES IT COMPARES WERE READ.** This is the
property the whole report rests on. If W&B cannot be reached, then every run in the account
is trivially "not in W&B", and a board that printed that would file 63 false accusations of
unlogged spend on the morning a credential lapsed. So each source is read into a value or
into ``None``, ``None`` means nobody looked rather than nothing was there, and a comparison
whose sides are not both present is skipped and named as unanswered. It is the same
distinction :func:`find_runs_that_saved_nothing._load_outcomes` draws, for the same reason.

**PER TEAM DOLLARS DO NOT COME FROM COST EXPLORER AND THIS DOES NOT TRY.**
:mod:`edullm_platform.run_costs` argues it in full and the short version is that ``sbsandbox``
is a linked account, ``ce:ListCostAllocationTags`` is refused outright, and activation is not
retroactive even once somebody grants it. The arithmetic is rate times nodes times measured
duration, summed over attempts, out of records this platform already writes. This attaches
that figure to unlogged spend so the finding lands as money rather than as a count, and it
degrades to a count when the lineage records cannot be read.

**AN ABSENT TEAM PREFIX IS THE ORDINARY CASE.** ``config/organization.yaml`` declares eight
teams and four of them have never written an object. Read live on 2026-08-02, only
``data-prep``, ``memory-split``, ``platform`` and ``scratch`` hold anything under
``teams/{team}/runs/``, and ``input-core`` and ``pre-training`` have never written anything at
all. A team that has not run yet is a team that has not run yet, so an empty listing is
recorded as zero prefixes and never as a failure.

**WHAT THE BUCKET SIDE CANNOT SEE, SAID OUT LOUD RATHER THAN LEFT AS A SURPRISE.** This lists
``teams/{team}/runs/`` and nothing else, because that is the shape the audit reader role's
``s3:prefix`` condition permits and a request for ``teams/`` is denied outright. Anything a
team wrote elsewhere in the bucket is therefore invisible here, and that is not hypothetical.
On 2026-08-02 the bucket held 1504 objects, 260 of which are outside that shape, 256 of them
under ``teams/post-training/artifacts/``. Widening the listing would mean widening the grant
to every key any team has written anywhere in the bucket, which is a decision about a shared
account rather than a decision about a report, so this board is scoped to what the grant
already allows and says so instead.

**THE ACCOUNT SIDE FORGETS, WHICH IS WHY IT IS READ TWICE.** The resource tagging API reports
a resource while the resource exists, and Batch stops listing a completed job after roughly a
week. So the account side of this board shrinks every day on its own: the platform holds 136
intent records, the tagging API answered for 112 runs on 2026-08-04, and five of the 24 the
board could not see carry a ``binding/`` record, which means Batch accepted them and they
genuinely ran. A denominator that quietly contracts is worse than a small one, because every
count taken over it reads as a trend.

``binding/`` is the second source and it is a join rather than a new read: the board already
syncs this bucket for the cost figures. A binding is written write-once by the state machine
the instant Batch accepts a submission, so it never expires and it is exactly the population
"what this platform started". What it cannot see is anything that ran in the account without
going through admission, which is what the tagging API is for and what the untagged section
below reports. Neither source covers the other, so **the window each one covers is printed**
rather than left for a reader to assume, and the counts say what they were taken over.

**A RECORD THAT NAMES A W&B RUN IS NOT A RECORD THAT WAS CHECKED.**
``ResultManifest.wandb_run`` is composed inside the lifecycle recorder out of the entity and
project the container was handed; nothing asks W&B whether the run is there. Read live on
2026-08-04, 42 of the 102 result records carry a reference and 28 of them name a run W&B does
not have. This board is where that gets asked, because it already reads every run in the
entity -- so the answer costs no second call, no new job on the schedule and no new deployed
artifact. ``tools/wandb_reconciliation.py`` holds the reasoning, including why the answer is
recomputed into this report rather than written back beside the record it is about.

**THE ACCOUNT SIDE NEEDS A GRANT, AND THE ROLE NOW HOLDS IT.**
``infra/iam/audit-reader-role.yaml`` grants ``tag:GetResources``, region-conditioned and
with no adjacent write, which is what makes the account side readable at all. It did not
until 2026-08-04, and this board exited 2 on every night before that, because there is no
substitute read: the role holds no ``batch:`` action deliberately, and the lineage records
say what this platform submitted rather than what the account ran. The refusal is still
caught rather than allowed to escape, since a grant can lapse and a credential can, and a
board that tracebacks reports nothing at all where a degraded one still reports two thirds.
What changed is what a refusal means -- it is now drift or a credential rather than the
expected answer, and it is worth looking at.

Exit codes follow this repository's convention. 0 says every source was read and the three
agree. 1 says they disagree, and the reader's next move is to go and look at a run. 2 says a
source could not be read, so the question was not answered and must not be filed as a pass. A
definite finding outranks an unanswered question when both happen, which is the rule
``tools/verify_deployed_stacks.py`` already follows, and the report still leads with what
could not be read so that nobody mistakes a partial board for a whole one.

**Nothing this prints carries an account id.** A resource ARN from the tagging API carries
the number, and so does the ARN in any denial the CLI reports, so a resource is rendered by
its identifier alone and every line of free text goes through the same mask
``tools/verify_deployed_stacks.py`` uses.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

TOOLS_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIRECTORY.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
if str(TOOLS_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIRECTORY))

from report_run_costs import LINEAGE_PREFIXES, ReportInputError, read_records, sync_bucket
from verify_wandb_credential import SECRET_NAME, WandbCredentialError, read_the_secret
from wandb_reconciliation import (
    RESULT_PREFIX,
    ReferenceReading,
    WandbObservation,
    never_logged,
    observation_document,
    observe,
    read_references,
    render_section,
)

from edullm_platform.capture_tooling import CaptureFailedError, aws
from edullm_platform.config import load_yaml
from edullm_platform.contracts.execution import BatchJobBinding
from edullm_platform.contracts.identity import RUN_ID_PREFIX, RUN_ID_REGEX, UUID7_TEXT_PATTERN
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.results import OUTPUTS_BUCKET, output_prefix
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.evidence import ACCOUNT_ID_IN_FREE_TEXT, AWS_ACCOUNT_ID_PLACEHOLDER
from edullm_platform.execution import WANDB_ENTITY
from edullm_platform.run_costs import RunCost, run_costs

__all__ = [
    "BINDING_PREFIX",
    "DEGRADING_LINEAGE_PREFIXES",
    "EXIT_DISAGREES",
    "EXIT_OK",
    "EXIT_UNUSABLE",
    "MISSING_BINDING_GRANT",
    "PLATFORM_TAG_KEYS",
    "REQUIRED_LINEAGE_PREFIXES",
    "RUN_ID_TAG",
    "Board",
    "BoundRun",
    "Match",
    "OutputPrefix",
    "SourceGap",
    "SourceHorizon",
    "TaggedResource",
    "WandbRun",
    "build_board",
    "build_parser",
    "main",
    "read_binding_records",
    "read_output_prefixes",
    "read_tagged_resources",
    "read_wandb_runs",
    "render",
    "run_id_of",
    "team_runs_prefix",
]

EXIT_OK: Final = 0

#: The three records of a run do not agree. A definite answer, and the reader's next move is
#: to open a run rather than to look at a grant.
EXIT_DISAGREES: Final = 1

#: A source was not read, so the comparisons that need it were not made. Never reported as a
#: pass, for the reason every check in this repository gives, which is that a check that could
#: not look is not a check that found nothing.
EXIT_UNUSABLE: Final = 2

#: Every tag ``batch_submit_request`` puts on a job, in the order that function sets them.
#: Written here rather than imported because ``src/edullm_platform/execution.py`` composes
#: them as a dict literal inside the request and exports no constant, and this board must not
#: edit that module to acquire one. ``tests/test_visibility_board.py`` parses the tag keys out
#: of that file and holds this tuple to them, so a sixth tag fails at review rather than being
#: quietly left out of a board whose whole job is to notice things being left out.
#:
#: Two of the five are conditional on the submission carrying a value, which is why this is
#: the set of keys to filter on rather than the set of keys to expect on a resource.
#: ``edullm:submitter`` is absent from a run admitted before the field existed and
#: ``edullm:experiment`` from one that named no experiment, and an absent tag is not a
#: finding.
PLATFORM_TAG_KEYS: Final = (
    "edullm:run-id",
    "edullm:team",
    "edullm:compute-profile",
    "edullm:experiment",
    "edullm:submitter",
)

#: The one tag that carries the join key. Set unconditionally on every submission, which is
#: the property ``infra/iam/run-canceller-role.yaml`` already depends on for its
#: ``aws:ResourceTag`` condition.
RUN_ID_TAG: Final = "edullm:run-id"

TEAM_TAG: Final = "edullm:team"
SUBMITTER_TAG: Final = "edullm:submitter"
EXPERIMENT_TAG: Final = "edullm:experiment"
COMPUTE_PROFILE_TAG: Final = "edullm:compute-profile"

#: A run id anywhere inside a longer string, built from the same tokens
#: ``contracts/identity.py`` anchors ``RUN_ID_REGEX`` with. Derived rather than written out,
#: because a second spelling of a uuid7 would agree with the first until the day the pattern
#: is tightened, and then this board would join runs the contracts refuse.
RUN_ID_ANYWHERE: Final = re.compile(f"{RUN_ID_PREFIX}{UUID7_TEXT_PATTERN}")

#: A placeholder, used only to ask :func:`output_prefix` what the shape of a team's run
#: directory is. That function raises on an empty run id and it is the only place in this
#: repository that composes the prefix, so borrowing it with a placeholder is how this board
#: avoids becoming the fourth place that answers the same question. Deliberately not a
#: well-formed run id, so that a reader who meets it in a stack trace cannot mistake it for
#: one and go looking for the run.
_SHAPE_PROBE_RUN_ID: Final = "<run-id>"

WANDB_GRAPHQL: Final = "https://api.wandb.ai/graphql"
WANDB_TIMEOUT_SECONDS: Final = 60

#: How many projects and how many runs one GraphQL page asks for. W&B refuses a page far
#: larger than this and answers a smaller one more times, and the entity held 218 runs across
#: 17 projects when this was written, so both fit in one page today and the cursor loop is
#: what keeps that from being an assumption.
WANDB_PAGE_SIZE: Final = 200

#: How the CLI opens every service error. The code inside the brackets is the only part of the
#: message repeated anywhere, because the rest of the line is an ARN or two and every ARN
#: carries the account id.
ERROR_CODE: Final = re.compile(r"An error occurred \(([A-Za-z]+)\)")

#: What the tagging API answers when the caller may not enumerate tags. Named so the report
#: can tell an absent grant from a service that is unwell, since the two send a reader to
#: entirely different places.
ACCESS_DENIED_CODES: Final = frozenset({"AccessDenied", "AccessDeniedException"})

#: The exact statement the account side of this board is read under, and the one to paste back
#: if it ever goes missing. ``infra/iam/audit-reader-role.yaml`` carries it
#: character-for-character and ``tests/test_visibility_board.py`` holds the two together, so
#: this is a quotation of the live grant rather than a request for one. It stays quoted in the
#: report because a refusal now means the role drifted or the credential lapsed, and the first
#: of those is repaired by applying exactly this. ``tag:GetResources`` takes no resource type,
#: so a policy naming a resource denies the call and ``"*"`` is forced rather than chosen; the
#: region condition is the only narrowing the action admits.
MISSING_TAG_GRANT: Final = """\
              - Sid: FindEveryResourceThisPlatformTagged
                Effect: Allow
                Action: tag:GetResources
                Resource: "*"
                Condition:
                  StringEquals:
                    aws:RequestedRegion:
                      Fn::Sub: ${AWS::Region}"""

#: The lineage prefix holding one write-once record per run Batch accepted.
BINDING_PREFIX: Final = "binding"

#: What this board syncs and cannot report without. ``intent`` and ``attempt`` are the cost
#: report's own two, and ``result`` is where a W&B reference lives -- the checkpoint
#: reconciliation already syncs that one, so the reader role already grants it and this costs
#: no IAM change. ``tests/test_audit_workflow.py`` derives the grant it expects from this
#: tuple rather than restating it, which is what stopped ``attempt/`` being missing for
#: months.
REQUIRED_LINEAGE_PREFIXES: Final = (*LINEAGE_PREFIXES, RESULT_PREFIX)

#: What it syncs and survives being refused, in a call of its own so that one denial does not
#: take the required prefixes with it. ``binding/`` is the second account-side source and the
#: audit reader role does not hold it yet, so a refusal here is the expected answer today
#: rather than a finding -- the board reports the narrower horizon and quotes the statement
#: below. It is separated from the required set rather than folded in because a prefix whose
#: absence removes a source is a different thing from one whose absence stops the report.
DEGRADING_LINEAGE_PREFIXES: Final = (BINDING_PREFIX,)

#: What ``infra/iam/audit-reader-role.yaml`` needs so the second account-side source can be
#: read, quoted rather than described for the reason :data:`MISSING_TAG_GRANT` is quoted: the
#: value of naming an IAM change in a 05:00 report is that whoever applies it pastes a
#: reviewed string instead of reconstructing one from a sentence.
#:
#: Only the statement that can be pasted whole is quoted. The second half of the change is an
#: edit to an existing statement -- ``binding/*`` added to the ``s3:prefix`` condition on
#: ``ListLineageRecords`` -- and it is said in the gap's own words rather than quoted, because
#: a fragment that looks pasteable and is not is worse than a sentence. Forgetting it is the
#: failure that reads as fine in a policy review: ``aws s3 sync`` lists before it fetches, so
#: a ``GetObject`` grant whose prefix is missing from the condition is refused at the first
#: call with no object fetched, which is exactly how ``attempt/`` was missing from this board
#: for every night it ran.
MISSING_BINDING_GRANT: Final = """\
              - Sid: ReadBindingRecords
                Effect: Allow
                Action: s3:GetObject
                Resource:
                  Fn::Sub: arn:${AWS::Partition}:s3:::sbsandbox-intern-edullm-lineage/binding/*"""


class Match(StrEnum):
    """How firmly a W&B run says which platform run it belongs to.

    Three values rather than a boolean, because the middle one is a finding. A ``NAMED`` run
    is reachable in W&B by its run id and a ``DERIVED`` one is not, and both of them logged.
    Collapsing them would either accuse eight runs of never logging or quietly report that a
    run is findable when nobody can find it.
    """

    NAMED = "named"
    DERIVED = "derived"
    NONE = "none"


@dataclass(frozen=True)
class SourceGap:
    """One thing this run did not manage to read, and what stops being answerable without it.

    ``unanswered`` is carried rather than derived at the point of printing, because the value
    of a gap is that the reader learns which findings are missing rather than which call
    failed. A denied tagging call is uninteresting; "nothing on this page can tell you about
    unlogged spend" is the sentence that matters.

    ``remedy`` holds the statement or the action that closes the gap where there is one, and
    is empty where the cause is a service rather than a grant.
    """

    source: str
    reason: str
    detail: str
    unanswered: tuple[str, ...]
    remedy: str = ""


@dataclass(frozen=True)
class WandbRun:
    """One run in the eduLLM entity, with the run id it can be shown to belong to.

    ``path`` is W&B's own eight-character identifier, which is what the URL uses and therefore
    what a reader needs in order to open the run. ``display_name`` is what the workload called
    it, and the two are different often enough that printing only one of them leaves somebody
    searching.
    """

    project: str
    path: str
    display_name: str
    state: str
    run_id: str | None
    match: Match

    @property
    def url(self) -> str:
        return f"https://wandb.ai/{WANDB_ENTITY}/{self.project}/runs/{self.path}"


@dataclass(frozen=True)
class TaggedResource:
    """One resource in the account carrying this platform's tags.

    ``identifier`` is the tail of the ARN rather than the ARN, and that is not cosmetic. An
    ARN carries the account id, this report is written into a scheduled log in a public
    repository, and the tail is what ``aws batch describe-jobs`` takes anyway.
    """

    service: str
    identifier: str
    run_id: str | None
    team: str | None
    submitter: str | None
    experiment: str | None
    compute_profile: str | None


@dataclass(frozen=True)
class BoundRun:
    """One run Batch accepted, as the state machine recorded it at the instant it did.

    THE SECOND ACCOUNT-SIDE SOURCE, AND IT EXISTS BECAUSE THE FIRST ONE FORGETS. The tagging
    API reports a resource while the resource exists and Batch drops a completed job after
    about a week, so a board reading tags alone has a denominator that shrinks on its own.
    A binding is written write-once the moment Batch accepts a submission, so it is
    permanent and it is the honest answer to "did this run start".

    Three fields out of the eleven the record carries, and the eight left behind are left
    behind on purpose. The rest are ARNs -- the job, the queue, the job definition -- and
    every ARN holds the account id, which this report goes into a scheduled log in a public
    repository without. What the board needs is which runs started, what they were submitted
    against and when, and none of those three needs an ARN.

    ``compute_profile`` and ``submitted_at`` are optional because three of the committed
    bindings cannot be parsed against ``BatchJobBinding``: an early state machine wrote the
    entire execution payload into ``array_size``, where an integer belongs. Those runs ran,
    the records are immutable, and dropping them would shrink the denominator over a field
    that has nothing to do with the run's identity -- which is the defect this whole source
    exists to close. So the run id is taken from a record the contract refuses and the
    decorations are left empty, and the count of those is reported.
    """

    run_id: str
    compute_profile: str | None
    submitted_at: datetime | None


def _binding_span(bound: Mapping[str, BoundRun] | None) -> str:
    """The dates the binding records actually cover, or nothing to say.

    The window this source claims is "for ever", and a claim of for ever is unfalsifiable
    until it names the oldest record behind it. It is the one horizon here that can be
    measured rather than described, so it is.
    """
    if not bound:
        return ""
    submitted = sorted(
        entry.submitted_at for entry in bound.values() if entry.submitted_at is not None
    )
    if not submitted:
        return ""
    return (
        f", which tonight is {submitted[0].date().isoformat()} "
        f"to {submitted[-1].date().isoformat()}"
    )


@dataclass(frozen=True)
class SourceHorizon:
    """What one source can see, so that a count taken over it can be read.

    A RECONCILIATION THAT QUIETLY CHANGES ITS DENOMINATOR IS WORSE THAN ONE WITH A SMALL
    DENOMINATOR, because the trend line lies. The tagging API's window moves every day as
    Batch forgets finished jobs, the binding records never expire, and the outputs listing
    covers only what a team wrote under ``teams/{team}/runs/``. None of that was on the page
    before, so a reader comparing two mornings was comparing two populations.

    ``counted`` is ``None`` where the source was not read, which is the same distinction
    every other value on this board carries and for the same reason.
    """

    source: str
    window: str
    counted: int | None


@dataclass(frozen=True)
class OutputPrefix:
    """One directory under ``teams/{team}/runs/``, and what is inside it.

    ``segment`` is the path component a run id is supposed to be, kept whether or not it is
    one. A segment that is not a run id is the purest form of the third finding, an output
    nothing can trace back to a config, because there is no identifier to trace with.
    """

    team: str
    segment: str
    objects: int
    bytes: int

    @property
    def run_id(self) -> str | None:
        return self.segment if RUN_ID_REGEX.fullmatch(self.segment) else None

    @property
    def uri(self) -> str:
        return f"s3://{OUTPUTS_BUCKET}/{team_runs_prefix(self.team)}{self.segment}/"


@dataclass(frozen=True)
class Board:
    """Everything the three sources said, before anything is decided about it.

    Each of the three is a mapping or ``None``, and ``None`` means the source was not read.
    That is the distinction the whole report rests on, so it is carried in the type rather
    than signalled by an empty mapping somewhere alongside a flag.
    """

    wandb: Mapping[str, tuple[WandbRun, ...]] | None
    unplaced_wandb: tuple[WandbRun, ...]
    account: Mapping[str, tuple[TaggedResource, ...]] | None
    untagged_account: tuple[TaggedResource, ...]
    #: The second account-side source, or ``None`` where it was not read. Held apart from
    #: ``account`` rather than merged into it because the two cover different windows and the
    #: horizon section has to be able to say which runs came from which.
    bound: Mapping[str, BoundRun] | None
    outputs: Mapping[str, OutputPrefix] | None
    untraceable_outputs: tuple[OutputPrefix, ...]
    #: Teams whose listing was refused, which makes the outputs source partial rather than
    #: absent. The two findings that read it are affected differently and are treated
    #: differently below, which is the whole reason this is carried rather than folded into a
    #: gap and forgotten.
    refused_teams: tuple[str, ...]
    #: ``None`` where the lineage records were not read, for the reason every other source
    #: here carries the same distinction. An empty mapping would say the records were read and
    #: priced nothing, and the report would then attach "no attempt record" to sixty runs that
    #: have one.
    costs: Mapping[str, RunCost] | None
    gaps: tuple[SourceGap, ...]
    #: What W&B said about each reference the result records carry. Empty when no result
    #: tree was read, which the reading below is what distinguishes from "read and there were
    #: none".
    observations: tuple[WandbObservation, ...] = ()
    #: The population the observations were taken over, or ``None`` where the result records
    #: were not read at all.
    reference_reading: ReferenceReading | None = None
    #: How many bindings named a run this tree could not otherwise parse. Reported rather
    #: than hidden, because the whole point of the second source is that nothing quietly
    #: leaves the denominator.
    degraded_bindings: int = 0

    @property
    def account_run_ids(self) -> frozenset[str] | None:
        """Every run the account is known to have started, from whichever sources answered.

        ``None`` only when neither answered. A partial account side is not the same hazard
        as a partial W&B side: every run either source names really did run, so one source
        being unread can only under-report the findings built on top of it, where an unread
        W&B would invent them. That is the same asymmetry ``in_wandb_with_no_output`` and
        ``output_with_no_wandb_run`` are already written around.
        """
        known = [set(source) for source in (self.account, self.bound) if source is not None]
        if not known:
            return None
        return frozenset().union(*known)

    @property
    def in_account_not_in_wandb(self) -> tuple[str, ...] | None:
        started = self.account_run_ids
        if started is None or self.wandb is None:
            return None
        return tuple(sorted(started - set(self.wandb)))

    @property
    def in_wandb_with_no_output(self) -> tuple[str, ...] | None:
        """Runs that logged and wrote nothing, or ``None`` where that cannot be claimed.

        HALF A LISTING IS NOT ENOUGH FOR THIS ONE AND IS ENOUGH FOR THE ONE BELOW, WHICH IS
        WHY THEY ARE NOT WRITTEN THE SAME WAY. This finding is an accusation built out of an
        absence, so a team whose prefix nobody was allowed to list would produce it for every
        run that team ever ran. The other finding is built out of something that was seen, and
        a prefix that is there with no run behind it is there whatever else went unlisted, so
        an incomplete listing can only under-report it.
        """
        if self.wandb is None or self.outputs is None or self.refused_teams:
            return None
        return tuple(sorted(set(self.wandb) - set(self.outputs)))

    @property
    def output_with_no_wandb_run(self) -> tuple[str, ...] | None:
        if self.wandb is None or self.outputs is None:
            return None
        return tuple(sorted(set(self.outputs) - set(self.wandb)))

    @property
    def derived_only(self) -> tuple[WandbRun, ...]:
        """Runs that logged and that nobody can find in W&B by run id.

        Read off the runs rather than off the run ids, because one platform run can hold
        several W&B runs and it is the individual W&B run whose name is wrong.
        """
        if self.wandb is None:
            return ()
        return tuple(
            run
            for runs in self.wandb.values()
            for run in runs
            if run.match is Match.DERIVED
        )

    @property
    def agreeing(self) -> int | None:
        started = self.account_run_ids
        if self.wandb is None or started is None or self.outputs is None:
            return None
        if self.refused_teams:
            # A run under an unlisted team would be counted as present in two sources and
            # absent from the third, which is not agreement and is not disagreement either.
            return None
        return len(set(self.wandb) & started & set(self.outputs))

    @property
    def known_run_ids(self) -> tuple[str, ...]:
        seen: set[str] = set()
        for source in (self.wandb, self.account, self.bound, self.outputs):
            if source is not None:
                seen |= set(source)
        return tuple(sorted(seen))

    @property
    def false_references(self) -> tuple[WandbObservation, ...]:
        """Records naming a W&B run W&B does not have, sorted with the worst case first.

        REPORTED AND NOT GATED, WHICH IS A DECISION RATHER THAN AN OVERSIGHT. This is a real
        disagreement and it is one nobody can repair: the lineage store refuses any write to
        a key that exists, so the 28 records that carry a false reference will carry it for
        ever. Folding it into :attr:`disagrees` would hold the audit red permanently over a
        condition with no remedy, and a job that is red every morning is a job whose next
        real finding arrives unread -- which is the argument
        ``tools/find_runs_that_saved_nothing.py`` makes at length beside its own
        acknowledgement list. The exit code stays a statement about the three-source join.
        """
        return tuple(
            sorted(
                (entry for entry in self.observations if entry.names_nothing),
                key=lambda entry: (not entry.logged_nowhere, entry.reference.run_id),
            )
        )

    @property
    def horizons(self) -> tuple[SourceHorizon, ...]:
        """What each source can see, in the order the findings above lean on them."""
        return (
            SourceHorizon(
                source="the account, from resource tags",
                window=(
                    "resources that still exist. Batch stops listing a completed job after "
                    "roughly a week, so this window moves every day and a run that ran last "
                    "month is not in it"
                ),
                counted=None if self.account is None else len(self.account),
            ),
            SourceHorizon(
                source="the account, from `binding/` records",
                window=(
                    "every run Batch has ever accepted from this platform"
                    + _binding_span(self.bound)
                    + ". The record is written write-once at submission and never expires. "
                    "It cannot see a resource that ran without going through admission, "
                    "which is what the tags above are for"
                ),
                counted=None if self.bound is None else len(self.bound),
            ),
            SourceHorizon(
                source="Weights and Biases",
                window=(
                    f"every run in `{WANDB_ENTITY}`, across every project, for as long as "
                    "the entity keeps them. Most of them never went through this platform"
                ),
                counted=None if self.wandb is None else len(self.wandb),
            ),
            SourceHorizon(
                source="the outputs bucket",
                window=(
                    f"`s3://{OUTPUTS_BUCKET}/teams/{{team}}/runs/` and nothing else, which "
                    "is the shape the reader role's prefix condition permits. Anything a "
                    "team wrote elsewhere in the bucket is invisible here"
                ),
                counted=None if self.outputs is None else len(self.outputs),
            ),
            SourceHorizon(
                source="the result records",
                window=(
                    "one per run that reached a terminal state with an attempt behind it, "
                    "which is what the W&B reference reconciliation is taken over"
                ),
                counted=(
                    None if self.reference_reading is None
                    else self.reference_reading.results_read
                ),
            ),
        )

    @property
    def disagrees(self) -> bool:
        return any(
            found
            for found in (
                self.in_account_not_in_wandb,
                self.in_wandb_with_no_output,
                self.output_with_no_wandb_run,
            )
            if found is not None
        ) or bool(self.untraceable_outputs or self.untagged_account)


def team_runs_prefix(team: str) -> str:
    """``teams/{team}/runs/``, asked of the one function that owns the shape.

    Three places answered this question once and two of them agreed, which is the whole
    argument in :func:`output_prefix`. Composing the string here would make this the fourth,
    and the IAM condition the audit reader role lists under is written against exactly this
    shape, so a board that drifted from it would fail as an access denial at 05:00 rather than
    as anything a reader could diagnose.
    """
    full = output_prefix(team=team, run_id=_SHAPE_PROBE_RUN_ID)
    return full.split(f"{OUTPUTS_BUCKET}/", 1)[1].removesuffix(f"{_SHAPE_PROBE_RUN_ID}/")


def _masked(text: str) -> str:
    """Mask any account id, leaving content digests alone.

    ``edullm_platform.evidence.redact_aws_account_ids`` is the sanctioned mask and is not used
    here, for the reason ``tools/verify_deployed_stacks.py`` gives beside its own copy of this
    function. That one raises on text that also carries another credential shape, which is
    right for a capture somebody is about to commit and wrong for an audit report, where a
    traceback in place of a board would report nothing at all on the one morning the account
    held something unexpected. The same expression is reused so the mask cannot be stepped
    around differently here than anywhere else.
    """
    return ACCOUNT_ID_IN_FREE_TEXT.sub(
        lambda found: AWS_ACCOUNT_ID_PLACEHOLDER if found.group("account") else found.group(0),
        text,
    )


# ----------------------------------------------------------------------------------------
# Source one, what W&B logged
# ----------------------------------------------------------------------------------------


def _unwrap(value: Any) -> Any:
    """One W&B config entry, without the ``{"desc": ..., "value": ...}`` envelope.

    W&B wraps every config key that way and passes through anything it did not write, so both
    shapes arrive from the same field and a reader that assumed one of them would silently
    find nothing in half the entity.
    """
    if isinstance(value, Mapping) and "value" in value:
        return value["value"]
    return value


def run_id_of(display_name: str, config: Mapping[str, Any]) -> tuple[str | None, Match]:
    """Which platform run a W&B run belongs to, and how firmly it says so.

    Three passes in decreasing order of confidence, and the order is what makes the grade
    meaningful rather than a description of which branch happened to run first.

    An exact display name is ``NAMED``. It is the convention the workloads follow when they
    follow one, it is what makes a run reachable in W&B by run id, and it cannot be a
    coincidence.

    A ``run_id`` config key is also exact and is still ``DERIVED``, because the value of a name
    is that a person searching W&B finds the run, and a config key is not searchable that way.

    Anything else is a scan of the whole record. It picks up a display name with a suffix
    glued on and a run id left inside the trainer's ``save_folder``, which is where the six
    runs named ``$EDULLM_RUN_ID`` keep theirs. A scan is the loose pass and one rule keeps it
    honest, which is that a record naming two different run ids is joined to neither. A
    resume that loads another run's checkpoint carries that run's id, and crediting the
    wrong run with having logged would be worse than reporting nothing, because it would
    quietly clear a real finding.
    """
    if RUN_ID_REGEX.fullmatch(display_name):
        return display_name, Match.NAMED

    claimed = _unwrap(config.get("run_id"))
    if isinstance(claimed, str) and RUN_ID_REGEX.fullmatch(claimed):
        return claimed, Match.DERIVED

    found = set(RUN_ID_ANYWHERE.findall(display_name))
    found |= set(RUN_ID_ANYWHERE.findall(json.dumps(config, default=str)))
    if len(found) == 1:
        return found.pop(), Match.DERIVED
    return None, Match.NONE


def _graphql(query: str, variables: Mapping[str, Any], *, key: str) -> Mapping[str, Any]:
    """One W&B GraphQL call, or a ``WandbCredentialError`` naming why there was not one.

    Raw HTTP rather than the ``wandb`` package, for the reason
    ``tools/verify_wandb_credential.py`` gives and this repository applies to boto3 as well.
    Neither is a dependency of this project, both are large, and the two Lambda builders
    resolve the project's closure into size-limited zips, so a dependency taken for a report
    is paid for by two functions that will never import it.
    """
    request = urllib.request.Request(
        WANDB_GRAPHQL,
        data=json.dumps({"query": query, "variables": dict(variables)}).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Basic " + base64.b64encode(f"api:{key}".encode()).decode(),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=WANDB_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, ValueError, TimeoutError) as error:
        raise WandbCredentialError(
            f"W&B did not answer ({error.__class__.__name__})"
        ) from error
    if not isinstance(body, Mapping) or not isinstance(body.get("data"), Mapping):
        # An unrecognised key is a 200 with a null payload rather than a 401, which is the
        # same shape verify_wandb_credential.py had to handle. Reading that as an entity with
        # no runs would report every run in the account as unlogged spend.
        raise WandbCredentialError("W&B answered without data, which is how it refuses a key")
    data: Mapping[str, Any] = body["data"]
    return data


#: The page size travels as a GraphQL variable rather than being interpolated into the query
#: text. Interpolation would make these two strings something a reader has to evaluate before
#: they can tell what was asked for, and W&B validates a variable against the schema where it
#: parses whatever was pasted into the literal.
_PROJECTS_QUERY: Final = """
query($entity: String!, $cursor: String, $first: Int!) {
  projects(entityName: $entity, first: $first, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    edges { node { name } }
  }
}
"""

_RUNS_QUERY: Final = """
query($entity: String!, $project: String!, $cursor: String, $first: Int!) {
  project(name: $project, entityName: $entity) {
    runs(first: $first, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      edges { node { name displayName state config } }
    }
  }
}
"""


def _pages(
    query: str, variables: Mapping[str, Any], *, key: str, path: Sequence[str]
) -> Iterable[Mapping[str, Any]]:
    """Every node under a Relay connection, following the cursor to the end.

    The cursor loop exists even though the entity fits in one page today. A board that read
    the first two hundred runs and reported the rest as never having logged would be a false
    accusation that grows quietly with the entity, and the failure would arrive as a finding
    rather than as an error.
    """
    cursor: str | None = None
    while True:
        asked = {**variables, "cursor": cursor, "first": WANDB_PAGE_SIZE}
        answer: Any = _graphql(query, asked, key=key)
        for step in path:
            if not isinstance(answer, Mapping) or answer.get(step) is None:
                return
            answer = answer[step]
        if not isinstance(answer, Mapping):
            return
        for edge in answer.get("edges") or []:
            if isinstance(edge, Mapping) and isinstance(edge.get("node"), Mapping):
                yield edge["node"]
        page = answer.get("pageInfo") or {}
        if not (isinstance(page, Mapping) and page.get("hasNextPage")):
            return
        cursor = str(page.get("endCursor"))


def read_wandb_runs(*, key: str, entity: str = WANDB_ENTITY) -> tuple[WandbRun, ...]:
    """Every run in the entity, graded by how firmly it names a platform run.

    Every project rather than the one the manifest names. A run logs wherever its training
    config sends it, and ``execution.py`` sets ``WANDB_PROJECT`` without being able to force a
    workload to honour it, so a board scoped to one project would report a run that logged to
    the wrong place as a run that never logged. Which project a run landed in is printed
    beside it instead, which is the more useful answer to the same question.
    """
    runs: list[WandbRun] = []
    for project in _pages(_PROJECTS_QUERY, {"entity": entity}, key=key, path=("projects",)):
        name = str(project.get("name") or "")
        if not name:
            continue
        for node in _pages(
            _RUNS_QUERY,
            {"entity": entity, "project": name},
            key=key,
            path=("project", "runs"),
        ):
            try:
                config = json.loads(node.get("config") or "{}")
            except ValueError:
                # A config this cannot parse is a run whose record W&B stored oddly, and it
                # still ran. Dropping it would remove a run from a report about runs going
                # missing; an empty config just means the scan has one fewer place to look.
                config = {}
            display = str(node.get("displayName") or "")
            run_id, match = run_id_of(display, config if isinstance(config, Mapping) else {})
            runs.append(
                WandbRun(
                    project=name,
                    path=str(node.get("name") or ""),
                    display_name=display,
                    state=str(node.get("state") or "unknown"),
                    run_id=run_id,
                    match=match,
                )
            )
    return tuple(runs)


# ----------------------------------------------------------------------------------------
# Source two, what the account ran
# ----------------------------------------------------------------------------------------


def _refusal(call: Sequence[str], stderr: str) -> str:
    """The error code the CLI reported, and nothing else from the message.

    A denial names the calling role's ARN and the resource ARN, and both carry the account id.
    This report is written into a scheduled log and a step summary in a public repository, so
    only the bracketed code is repeated, which is also the part that decides what to do about
    it.
    """
    found = ERROR_CODE.search(stderr)
    code = found.group(1) if found else "no code"
    return f"aws {' '.join(call[:2])} was refused with {code}"


def read_tagged_resources(
    *, profile: str | None, region: str
) -> tuple[TaggedResource, ...]:
    """Every resource in the account carrying any tag this platform sets.

    One call per tag key rather than one call filtered on ``edullm:run-id``. The tagging API
    ANDs its filters, so several keys in one call would ask for resources carrying all five
    and two of the five are conditional. Asking key by key and unioning by ARN costs four
    extra calls and buys the case that matters, which is a resource tagged as ours that
    carries no run id at all. That resource is spend nothing can attribute, and a board
    filtered on the run id could not see it by construction.

    Read live on 2026-08-02, all 63 tagged resources were Batch jobs and every one of them
    carried a run id, so the extra calls found nothing. That is the answer rather than a
    reason to stop asking.
    """
    by_arn: dict[str, dict[str, str]] = {}
    for tag_key in PLATFORM_TAG_KEYS:
        call = ["resourcegroupstaggingapi", "get-resources", "--tag-filters", f"Key={tag_key}"]
        completed = aws(call, profile=profile, region=region)
        if completed.returncode != 0:
            raise CaptureFailedError(_refusal(call, completed.stderr))
        try:
            answer = json.loads(completed.stdout or "{}")
        except ValueError as error:
            raise CaptureFailedError(
                "the tagging API answered with something that is not JSON"
            ) from error
        for resource in answer.get("ResourceTagMappingList") or []:
            arn = str(resource.get("ResourceARN") or "")
            if not arn:
                continue
            tags = by_arn.setdefault(arn, {})
            for tag in resource.get("Tags") or []:
                tags[str(tag.get("Key"))] = str(tag.get("Value"))

    return tuple(
        sorted(
            (
                TaggedResource(
                    # An ARN is arn:partition:service:region:account:rest, so the service is
                    # the third field and everything from the sixth on is the identifier. Held
                    # apart rather than printed whole because the fifth field is the account
                    # id and this text is public.
                    service=arn.split(":")[2] if arn.count(":") >= 2 else "unknown",
                    identifier=_masked(arn.split(":", 5)[5] if arn.count(":") >= 5 else arn),
                    run_id=tags.get(RUN_ID_TAG),
                    team=tags.get(TEAM_TAG),
                    submitter=tags.get(SUBMITTER_TAG),
                    experiment=tags.get(EXPERIMENT_TAG),
                    compute_profile=tags.get(COMPUTE_PROFILE_TAG),
                )
                for arn, tags in by_arn.items()
            ),
            key=lambda resource: (resource.run_id or "", resource.identifier),
        )
    )


def _binding_document(path: Path) -> object:
    """One stored binding, unwrapped.

    A record is sometimes a JSON string holding JSON, because the state machine writes the
    handler's canonical bytes rather than re-encoding them, and both spellings are in the
    committed fixtures for the same prefix.
    """
    loaded: object = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(loaded, str):
        try:
            loaded = json.loads(loaded)
        except ValueError:
            return loaded
    return loaded


def read_binding_records(root: Path) -> tuple[tuple[BoundRun, ...], int]:
    """Every run Batch accepted, and how many of them the contract would not parse.

    THE CONTRACT IS THE READER AND IT IS NOT THE GATE, WHICH IS THE ONE THING WORTH READING
    HERE. Three of the committed bindings are refused by ``BatchJobBinding``: an early state
    machine passed the whole execution payload where ``array_size`` takes an integer, the
    records are immutable, and the runs behind them ran. Dropping them would take three runs
    out of the account side over a field that says nothing about which run it is -- and a
    denominator that quietly loses records is exactly the defect this second source exists
    to close. So a refused record still yields its run id, provided the run id is one, and
    the decorations the contract would have supplied are left empty and counted.

    The count is returned rather than printed, because the caller is the one that knows
    whether it is reporting at all.

    A tree that is not there raises rather than answering with no runs. An empty account
    side is a claim -- it would say this platform has never started anything -- and a prefix
    nobody synced is not, and this whole board is written around keeping those apart.
    """
    directory = root / BINDING_PREFIX
    if not directory.is_dir():
        raise ReportInputError(f"no {BINDING_PREFIX}/ directory under {root}")
    bound: list[BoundRun] = []
    degraded = 0
    for path in sorted(directory.rglob("*.json")):
        try:
            document = _binding_document(path)
        except (OSError, ValueError):
            degraded += 1
            continue
        try:
            record = BatchJobBinding.model_validate(document)
        except ValueError:
            claimed = document.get("run_id") if isinstance(document, Mapping) else None
            if not isinstance(claimed, str) or RUN_ID_REGEX.fullmatch(claimed) is None:
                # No run id anybody can read. Counted, because a binding this tree cannot
                # place at all is a record the recorder should not have been able to write.
                degraded += 1
                continue
            degraded += 1
            bound.append(BoundRun(run_id=claimed, compute_profile=None, submitted_at=None))
            continue
        bound.append(
            BoundRun(
                run_id=record.run_id,
                compute_profile=record.compute_profile,
                submitted_at=record.submitted_at,
            )
        )
    return tuple(sorted(bound, key=lambda entry: entry.run_id)), degraded


# ----------------------------------------------------------------------------------------
# Source three, what the bucket holds
# ----------------------------------------------------------------------------------------


def read_output_prefixes(
    teams: Sequence[str], *, profile: str | None, region: str, bucket: str = OUTPUTS_BUCKET
) -> tuple[tuple[OutputPrefix, ...], tuple[str, ...]]:
    """Every run directory under each team, and the teams whose listing was refused.

    One listing per team rather than one listing of ``teams/``. The audit reader role's
    ``s3:ListBucket`` grant carries ``StringLike`` on ``s3:prefix`` of ``teams/*/runs/*``, so a
    request for ``teams/`` sends a prefix that does not match and is denied outright, while
    ``teams/{team}/runs/`` matches with the trailing wildcard covering the empty string. The
    cost is one call per declared team and the benefit is that the board runs under the role
    the workflow already has rather than under a grant somebody has to widen.

    A team with nothing under it is returned as no prefixes, which is the ordinary case and
    not an error. A team whose listing is refused is returned separately, because "this team
    has written nothing" and "nobody was allowed to look" are opposite statements and the
    first one is a claim.

    The listing is not delimited. A delimited call answers with the run directories alone and
    would need a second call per run to say what is in one, and the object count is what
    separates a prefix that exists from a prefix that holds a result. Read live on 2026-08-02
    the whole bucket held 1504 objects, so the undelimited call is cheap and the CLI follows
    its own continuation token, which ``tools/find_runs_that_saved_nothing.py`` measured
    against a paged prefix.
    """
    prefixes: dict[tuple[str, str], list[int]] = {}
    refused: list[str] = []
    for team in teams:
        root = team_runs_prefix(team)
        call = ["s3api", "list-objects-v2", "--bucket", bucket, "--prefix", root]
        completed = aws(call, profile=profile, region=region)
        if completed.returncode != 0:
            refused.append(team)
            continue
        try:
            answer = json.loads(completed.stdout or "{}")
        except ValueError:
            refused.append(team)
            continue
        for item in answer.get("Contents") or []:
            key = str(item.get("Key") or "")
            tail = key.removeprefix(root)
            if not tail or "/" not in tail:
                # An object sitting directly under teams/{team}/runs/ belongs to no run
                # directory at all. It is left out here rather than invented into one, and it
                # gets no section of its own because nothing in the account has ever written
                # one.
                continue
            segment = tail.split("/", 1)[0]
            tally = prefixes.setdefault((team, segment), [0, 0])
            tally[0] += 1
            tally[1] += int(item.get("Size") or 0)

    return (
        tuple(
            OutputPrefix(team=team, segment=segment, objects=count, bytes=size)
            for (team, segment), (count, size) in sorted(prefixes.items())
        ),
        tuple(refused),
    )


# ----------------------------------------------------------------------------------------
# What the runs cost, from the lineage this platform already writes
# ----------------------------------------------------------------------------------------


def _read_costs(root: Path, config_dir: Path) -> Mapping[str, RunCost]:
    """Every run's measured cost, keyed by run id.

    Delegated whole to :mod:`edullm_platform.run_costs`, which already prices an attempt at the
    catalog rate and already refuses to price a spot profile. A second arithmetic here would
    disagree with ``tools/report_run_costs.py`` eventually, and two dollar figures for one run
    is worse than one figure and a caveat.
    """
    intents, attempts, _ = read_records(root)
    catalog = load_yaml(config_dir / "workload-catalog.yaml", WorkloadCatalog)
    return {
        cost.run_id: cost
        for cost in run_costs(
            intents=intents, attempts=attempts, compute_profiles=catalog.compute_profiles
        )
    }


def declared_teams(config_dir: Path) -> tuple[str, ...]:
    return tuple(
        team.team_id
        for team in load_yaml(config_dir / "organization.yaml", OrganizationInventory)
        .team_bindings.teams
    )


# ----------------------------------------------------------------------------------------
# Assembling the board
# ----------------------------------------------------------------------------------------


def build_board(
    *,
    wandb_runs: Sequence[WandbRun] | None,
    resources: Sequence[TaggedResource] | None,
    outputs: Sequence[OutputPrefix] | None,
    bindings: Sequence[BoundRun] | None = None,
    refused_teams: Sequence[str] = (),
    costs: Mapping[str, RunCost] | None = None,
    gaps: Sequence[SourceGap] = (),
    observations: Sequence[WandbObservation] = (),
    reference_reading: ReferenceReading | None = None,
    degraded_bindings: int = 0,
) -> Board:
    """Index the three sources by run id, keeping what would not index.

    Nothing is dropped for failing to carry a run id. A W&B run that names none and an output
    prefix whose segment is not one are both findings of their own, and a board that indexed
    only what indexed cleanly would be a board that hides exactly the records nobody can
    trace.
    """
    by_run_wandb: dict[str, list[WandbRun]] | None = None
    unplaced: list[WandbRun] = []
    if wandb_runs is not None:
        by_run_wandb = {}
        for run in wandb_runs:
            if run.run_id is None:
                unplaced.append(run)
            else:
                by_run_wandb.setdefault(run.run_id, []).append(run)

    by_run_account: dict[str, list[TaggedResource]] | None = None
    untagged: list[TaggedResource] = []
    if resources is not None:
        by_run_account = {}
        for resource in resources:
            if resource.run_id is None:
                untagged.append(resource)
            else:
                by_run_account.setdefault(resource.run_id, []).append(resource)

    # A binding names one run by construction -- ``BatchJobBinding`` refuses a record whose
    # Batch job name is not the run id -- so there is no untraceable pile here to match the
    # two above. A record with no readable run id never reaches this point; it is counted as
    # degraded by the reader and reported as a number.
    by_run_bound: dict[str, BoundRun] | None = (
        None if bindings is None else {entry.run_id: entry for entry in bindings}
    )

    by_run_output: dict[str, OutputPrefix] | None = None
    untraceable: list[OutputPrefix] = []
    if outputs is not None:
        by_run_output = {}
        for prefix in outputs:
            if prefix.run_id is None:
                untraceable.append(prefix)
            else:
                by_run_output[prefix.run_id] = prefix

    return Board(
        wandb=(
            None
            if by_run_wandb is None
            else {run_id: tuple(runs) for run_id, runs in by_run_wandb.items()}
        ),
        unplaced_wandb=tuple(unplaced),
        account=(
            None
            if by_run_account is None
            else {run_id: tuple(found) for run_id, found in by_run_account.items()}
        ),
        untagged_account=tuple(untagged),
        bound=by_run_bound,
        outputs=by_run_output,
        untraceable_outputs=tuple(untraceable),
        refused_teams=tuple(refused_teams),
        costs=None if costs is None else dict(costs),
        gaps=tuple(gaps),
        observations=tuple(observations),
        reference_reading=reference_reading,
        degraded_bindings=degraded_bindings,
    )


# ----------------------------------------------------------------------------------------
# The report
# ----------------------------------------------------------------------------------------


def _dollars(cost: RunCost | None, *, costing_read: bool) -> str:
    """One run's spend, or the reason there is no number, which is never a zero.

    Three ways to have no figure and they are different facts. Nobody read the lineage
    records. The records were read and hold no attempt for this run, which :func:`run_costs`
    treats as a run that never reached an instance rather than one that cost nothing. Or the
    run bought interruptible capacity, which the catalog prices at its on-demand rate so that
    an approver sees a ceiling, and :mod:`edullm_platform.run_costs` refuses to report that
    ceiling as a measurement.

    Two decimal places, matching ``tools/report_run_costs.py``. Two reports on one run showing
    two different numbers would send somebody looking for a bug in the arithmetic, and the
    arithmetic is shared.
    """
    if not costing_read:
        return "not costed"
    if cost is None:
        return "no attempt record"
    if cost.cost_usd is None:
        return "no figure"
    return f"${cost.cost_usd:.2f}"


def _bytes(count: int) -> str:
    """A size a person reads at a glance, in the unit that keeps three significant figures."""
    size = Decimal(count)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GiB"


def _gaps_section(board: Board) -> list[str]:
    lines = [
        "## What this run could not read",
        "",
        (
            "Every finding below rests on two sources, so a source that was not read removes "
            "findings rather than producing them. What each gap costs is named beside it, "
            "because the useful sentence is which question stopped being answerable and not "
            "which call failed."
        ),
        "",
    ]
    for gap in board.gaps:
        lines.append(f"- **{gap.source}**: {gap.reason}. {gap.detail}")
        lines.append(f"  Unanswered while this holds: {', '.join(gap.unanswered)}.")
        if gap.remedy:
            lines += ["", "```yaml", gap.remedy, "```", ""]
    lines.append("")
    return lines


def _unlogged_section(board: Board, found: Sequence[str]) -> list[str]:
    costed = board.costs is not None
    priced = [(board.costs or {}).get(run_id) for run_id in found]
    total = sum(
        (cost.cost_usd for cost in priced if cost is not None and cost.cost_usd is not None),
        Decimal(0),
    )
    headline = (
        f"{len(found)} run(s) ran in the account and logged nothing anybody can find in "
        f"W&B under `{WANDB_ENTITY}`."
    )
    if costed:
        headline += f" ${total:.2f} of that is priced from the attempt records."
    lines = [
        "## Spend nobody can see a loss curve for",
        "",
        headline,
        "",
        (
            "The figure is compute at the catalog's published rate, measured from the attempt "
            "records this platform wrote, and `src/edullm_platform/run_costs.py` says why it "
            "is not read out of Cost Explorer. It is the wrong number to read as a bill and "
            "the right one to read as an ordering, so the largest compute profile on this "
            "list is worth more attention than the largest figure."
        ),
        "",
        "| Run | Team | Submitter | Compute | Cost | Experiment | Known from |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for run_id in found:
        resources = (board.account or {}).get(run_id, ())
        first = resources[0] if resources else None
        # A run the tags have already forgotten is described from the two records that
        # outlive them. The binding says which profile it was submitted against and the cost
        # record says which team and person claimed it, so the row is as full as it was
        # before the tagging API dropped the job rather than four columns of "unknown".
        bound = (board.bound or {}).get(run_id)
        cost = (board.costs or {}).get(run_id)
        team = first.team if first else (cost.team if cost else None)
        submitter = (first.submitter if first else None) or (cost.submitter if cost else None)
        profile = (first.compute_profile if first else None) or (
            bound.compute_profile if bound else None
        )
        lines.append(
            f"| `{run_id}` | {team or 'unknown'} "
            f"| {submitter or 'not recorded'} "
            f"| {profile or 'unknown'} "
            f"| {_dollars(cost, costing_read=costed)} "
            f"| {first.experiment if first and first.experiment else 'none'} "
            f"| {_known_from(first is not None, bound is not None)} |"
        )
    lines.append("")
    return lines


def _known_from(tagged: bool, bound: bool) -> str:
    """Which account-side source names this run, printed because the two differ in age.

    A row known only from a binding is a run the tagging API has already forgotten, which is
    the case the second source was added for; a row known only from the tags is a resource
    that never went through admission, which is a different thing entirely and worth seeing
    in the same table.
    """
    if tagged and bound:
        return "tags, binding"
    return "tags" if tagged else "binding"


def _no_output_section(board: Board, found: Sequence[str]) -> list[str]:
    rows = [
        (run_id, run)
        for run_id in found
        for run in (board.wandb or {}).get(run_id, ())
    ]
    lines = [
        "## Runs that saved nothing",
        "",
        (
            f"{len(found)} run(s) logged to W&B and left nothing under "
            f"`s3://{OUTPUTS_BUCKET}/teams/{{team}}/runs/`. "
            "`tools/find_runs_that_saved_nothing.py` asks the narrower question of whether a "
            "checkpoint would load; this one asks whether anything was written at all."
        ),
        "",
        (
            "**The state column decides how much of this matters.** A run W&B recorded as "
            "`finished` that wrote nothing is the expensive case, because it exits zero, "
            "carries a full loss curve and produces no artifact, and nothing else on the "
            "platform reports it. A run recorded as `crashed` or `failed` has already said so "
            "with an exit code, and an empty prefix under it is the shape everybody expects. "
            "Both are listed, because the run that ends up mattering is the one nobody "
            "expected to be in the first group."
        ),
        "",
        "| Run | W&B | State | How the run id was matched |",
        "| --- | --- | --- | --- |",
    ]
    # Finished first, since that is the group somebody has to act on and a page a reader has
    # to scan for the interesting rows is a page whose ordering is doing nothing.
    for run_id, run in sorted(rows, key=lambda row: (row[1].state != "finished", row[0])):
        lines.append(
            f"| `{run_id}` | [{run.project}/{run.display_name}]({run.url}) "
            f"| {run.state} | {run.match} |"
        )
    lines.append("")
    return lines


def _untraceable_section(board: Board, found: Sequence[str]) -> list[str]:
    lines = [
        "## Output nobody can trace back to a config",
        "",
        (
            f"{len(found) + len(board.untraceable_outputs)} prefix(es) hold bytes that no W&B "
            "run accounts for. A result with no run behind it cannot be reproduced, cannot be "
            "attributed and cannot be thrown away with any confidence, so it occupies the "
            "bucket permanently."
        ),
        "",
        "| Prefix | Objects | Size | Why it is here |",
        "| --- | --- | --- | --- |",
    ]
    for run_id in found:
        prefix = (board.outputs or {})[run_id]
        lines.append(
            f"| `{prefix.uri}` | {prefix.objects} | {_bytes(prefix.bytes)} "
            "| no W&B run names this run id |"
        )
    for prefix in board.untraceable_outputs:
        lines.append(
            f"| `{prefix.uri}` | {prefix.objects} | {_bytes(prefix.bytes)} "
            "| the directory name is not a run id, so there is nothing to join on |"
        )
    lines.append("")
    return lines


def _derived_section(board: Board) -> list[str]:
    runs = board.derived_only
    lines = [
        "## Logged under something other than the run id",
        "",
        (
            f"{len(runs)} W&B run(s) belong to a platform run and are not named after it, so "
            "searching W&B for the run id finds nothing and the run reads as unlogged until "
            "somebody opens it. Nothing on this platform sets a W&B run's name; the workload "
            "does, in its own training command, which is why this drifts. The run id was "
            "recovered from the rest of the record instead, and these are counted as logged "
            "above rather than reported as missing."
        ),
        "",
        "| Run | Called | W&B | State |",
        "| --- | --- | --- | --- |",
    ]
    for run in sorted(runs, key=lambda entry: (entry.run_id or "", entry.project)):
        lines.append(
            f"| `{run.run_id}` | `{run.display_name}` "
            f"| [{run.project}/{run.path}]({run.url}) | {run.state} |"
        )
    lines.append("")
    return lines


def _unplaced_section(board: Board) -> list[str]:
    shapes: dict[str, int] = {}
    for run in board.unplaced_wandb:
        shapes[run.project] = shapes.get(run.project, 0) + 1
    return [
        "## W&B runs this board cannot place",
        "",
        (
            f"{len(board.unplaced_wandb)} run(s) in `{WANDB_ENTITY}` carry no platform run id "
            "anywhere in their record, so none of the three comparisons above says anything "
            "about them. Most of them are work that never went through this platform, which "
            "is the expected answer and not a finding; the number is here so that nobody "
            "reads the joined population as the whole entity."
        ),
        "",
        *(
            f"- {project}: {count} run(s)"
            for project, count in sorted(shapes.items(), key=lambda item: (-item[1], item[0]))
        ),
        "",
    ]


def _horizon_section(board: Board) -> list[str]:
    """What each number above was counted over, before anybody compares two mornings.

    STATED RATHER THAN SILENTLY FIXED, WHICH IS THE WHOLE POINT OF THE SECTION. Adding the
    binding records moved the account side without moving anything about the account, and a
    reader with yesterday's board beside today's would have read that as five new runs. Every
    source here has a window, two of them move, and a reconciliation whose denominator
    changes without saying so is worse than one with a small denominator.
    """
    lines = [
        "## What these numbers are counted over",
        "",
        (
            "Each source sees a different slice of the same platform and two of the slices "
            "move on their own. The counts above are only comparable across mornings while "
            "these windows are, so they are printed rather than assumed."
        ),
        "",
        "| Source | Runs it named tonight | What it can see |",
        "| --- | --- | --- |",
    ]
    for horizon in board.horizons:
        counted = "not read" if horizon.counted is None else str(horizon.counted)
        lines.append(f"| {horizon.source} | {counted} | {horizon.window} |")
    lines.append("")
    lines.append(
        f"The account side of every finding above is the union of the first two, which is "
        f"{'not counted' if board.account_run_ids is None else len(board.account_run_ids)} "
        "run(s) tonight."
    )
    if board.degraded_bindings:
        lines.append("")
        lines.append(
            f"{board.degraded_bindings} binding record(s) do not parse against "
            "`BatchJobBinding` and are counted anyway, from their run id alone. An early "
            "state machine wrote the whole execution payload where `array_size` takes an "
            "integer; those runs ran, the records cannot be rewritten, and leaving them out "
            "would be this board losing three runs from its own denominator over a field "
            "that says nothing about which run it is."
        )
    lines.append("")
    return lines


def _untagged_section(board: Board) -> list[str]:
    return [
        "## Resources tagged as ours that carry no run id",
        "",
        (
            f"{len(board.untagged_account)} resource(s) carry one of this platform's tags and "
            f"no `{RUN_ID_TAG}`, so the spend is ours and belongs to nothing. Every submission "
            "sets the run id unconditionally, which is what "
            "`infra/iam/run-canceller-role.yaml` conditions its grant on, so a resource here "
            "was created by something other than a submission."
        ),
        "",
        *(
            f"- `{resource.identifier}` ({resource.service}), team "
            f"{resource.team or 'not recorded'}"
            for resource in board.untagged_account
        ),
        "",
    ]


def render(board: Board) -> str:
    """The board, mismatches first and the agreeing majority as a single number.

    Section order is the order a reader needs and not the order the sources were read in.
    What could not be read comes first because it scopes everything under it, and what each
    source can see comes second for the same reason -- a count is unreadable until somebody
    knows what it was counted over, and two of the windows move on their own. The three
    disagreements come next in descending cost, the W&B reference reconciliation after them
    because it is about the records rather than about the runs, and the explanatory sections
    last because they exist to stop a reader misreading what is above them.
    """
    lines = ["# The visibility board", "", _verdict(board), ""]

    if board.gaps:
        lines += _gaps_section(board)

    lines += _horizon_section(board)

    unlogged = board.in_account_not_in_wandb
    if unlogged:
        lines += _unlogged_section(board, unlogged)

    nothing_saved = board.in_wandb_with_no_output
    if nothing_saved:
        lines += _no_output_section(board, nothing_saved)

    untraceable = board.output_with_no_wandb_run
    if untraceable or board.untraceable_outputs:
        lines += _untraceable_section(board, untraceable or ())

    if board.reference_reading is not None:
        lines += render_section(
            board.observations, reading=board.reference_reading, entity=WANDB_ENTITY
        )

    if board.untagged_account:
        lines += _untagged_section(board)

    if board.derived_only:
        lines += _derived_section(board)

    if board.unplaced_wandb:
        lines += _unplaced_section(board)

    lines += _agreement_section(board)
    return "\n".join(lines) + "\n"


def _verdict(board: Board) -> str:
    """One sentence a reader can act on before reading anything else."""
    counts = {
        "in the account and not in W&B": board.in_account_not_in_wandb,
        "in W&B with nothing saved": board.in_wandb_with_no_output,
        "saved with no W&B run": board.output_with_no_wandb_run,
    }
    found = [
        f"{len(entries)} {phrase}" for phrase, entries in counts.items() if entries
    ]
    if board.untraceable_outputs:
        found.append(f"{len(board.untraceable_outputs)} prefix(es) that are not a run id")
    if board.untagged_account:
        found.append(f"{len(board.untagged_account)} tagged resource(s) with no run id")

    scope = (
        f"{len(board.known_run_ids)} run(s) are known to at least one of the three sources."
    )
    # Said in a sentence of its own rather than folded into the list above, because it is
    # not one of the three findings and does not move the exit code. See
    # ``Board.false_references`` for why a defect nobody can repair is reported and not
    # gated.
    false = board.false_references
    lying = (
        ""
        if not false
        else (
            f" Separately, {len(false)} lineage record(s) name a W&B run that does not "
            "exist."
        )
    )
    if not found:
        if board.gaps:
            return (
                f"{scope} Nothing disagrees among the sources that were read, and "
                f"{len(board.gaps)} source(s) were not read, so this is not a clean "
                f"board.{lying}"
            )
        return f"{scope} All three sources agree about every one of them.{lying}"
    return f"{scope} {', '.join(found)}.{lying}"


def _agreement_section(board: Board) -> list[str]:
    agreeing = board.agreeing
    if agreeing is None:
        return [
            "## What agrees",
            "",
            (
                "Not counted. Agreement is a statement about all three sources and one of "
                "them was not read, so the number would describe the two that were and read "
                "as the three."
            ),
            "",
        ]
    return [
        "## What agrees",
        "",
        (
            f"{agreeing} run(s) are in all three, which is the part of this board that needs "
            "no attention. It is a count rather than a list on purpose. A page that lists "
            "everything buries the handful of rows somebody has to act on, and the rows above "
            "are the reason this runs."
        ),
        "",
    ]


# ----------------------------------------------------------------------------------------
# Running it
# ----------------------------------------------------------------------------------------


def _wandb_key(options: argparse.Namespace) -> str:
    """The key, from the environment on a laptop and from Secrets Manager on the runner.

    The environment first because a person running this by hand usually already has one
    exported and should not need an AWS session to read a board. The secret is what the
    scheduled run uses, and it is the same value ``tools/verify_wandb_credential.py`` checks
    every night, so a board that cannot reach W&B and an audit that reports the key as
    refused are the same finding rather than two.
    """
    exported = os.environ.get("WANDB_API_KEY", "").strip()
    if exported:
        return exported
    return read_the_secret(
        options.wandb_secret, profile=options.profile, region=options.region
    ).strip()


def _collect(options: argparse.Namespace) -> Board:
    gaps: list[SourceGap] = []

    wandb_runs: tuple[WandbRun, ...] | None
    try:
        wandb_runs = read_wandb_runs(key=_wandb_key(options), entity=options.entity)
    except WandbCredentialError as error:
        wandb_runs = None
        gaps.append(
            SourceGap(
                source="Weights and Biases",
                reason="wandb_not_read",
                detail=(
                    f"{error}. The key is the one in Secrets Manager under "
                    f"`{options.wandb_secret}`, which "
                    "`tools/verify_wandb_credential.py` resolves against W&B every night, so "
                    "check that job before looking anywhere else."
                ),
                unanswered=(
                    "which runs logged nothing",
                    "which runs saved nothing",
                    "which output nobody can trace",
                ),
            )
        )

    resources: tuple[TaggedResource, ...] | None
    try:
        resources = read_tagged_resources(profile=options.profile, region=options.region)
    except CaptureFailedError as error:
        resources = None
        denied = any(code in str(error) for code in ACCESS_DENIED_CODES)
        gaps.append(
            SourceGap(
                source="the account",
                reason="tagged_resources_not_read",
                detail=(
                    f"{error}. "
                    + (
                        "The audit reader role is granted the statement below, so a "
                        "denial is a finding rather than the expected answer: either the "
                        "deployed role has drifted from "
                        "`infra/iam/audit-reader-role.yaml` or the credential is not the "
                        "one this job means to be using. Compare the two with "
                        "`tools/verify_deployed_stacks.py`, and re-apply the stack from a "
                        "laptop as `infra/README.md` describes if they disagree. There is "
                        "no substitute read while it holds. Enumerating the queues would "
                        "need `batch:ListJobs` and `batch:DescribeJobs`, which that role "
                        "omits deliberately, and the lineage records say what this platform "
                        "submitted rather than what the account is running."
                        if denied
                        else "That is a statement about the call rather than about the grant."
                    )
                ),
                unanswered=("which runs logged nothing",),
                remedy=MISSING_TAG_GRANT if denied else "",
            )
        )

    teams = sorted(
        set(declared_teams(options.config_dir))
        | {resource.team for resource in resources or () if resource.team}
    )
    outputs, refused = read_output_prefixes(
        teams, profile=options.profile, region=options.region, bucket=options.outputs_bucket
    )
    if refused:
        # Partial rather than absent. A team whose listing was refused is named and the teams
        # that answered are still compared, because the alternative is to throw away a
        # readable seven eighths of the bucket over one denial.
        gaps.append(
            SourceGap(
                source="the outputs bucket",
                reason="team_prefix_not_listed",
                detail=(
                    "Listing was refused for "
                    + ", ".join(f"`{team}`" for team in refused)
                    + ". A refusal here is either the credential or the grant, and the grant "
                    "is narrow on purpose, because the reader role conditions `s3:ListBucket` "
                    "on `teams/*/runs/*` and nothing wider. Runs under the teams that did answer "
                    "are still compared, and a team that answered with nothing has written "
                    "nothing, which is the ordinary case for half the roster."
                ),
                unanswered=(
                    "which runs saved nothing, for every team rather than only these ones",
                ),
            )
        )

    costs: Mapping[str, RunCost] | None = None
    bindings: tuple[BoundRun, ...] | None = None
    degraded = 0
    reading: ReferenceReading | None = None
    observations: tuple[WandbObservation, ...] = ()
    with tempfile.TemporaryDirectory() as scratch:
        root = options.lineage_root
        try:
            if root is None:
                root = Path(scratch)
                sync_bucket(
                    options.lineage_bucket,
                    root,
                    profile=options.profile,
                    region=options.region,
                    prefixes=REQUIRED_LINEAGE_PREFIXES,
                )
            costs = _read_costs(root, options.config_dir)
            # Read after the costs and inside the same try, because both come out of the
            # same tree and a tree that would not sync has neither. The reconciliation is
            # not a second network call: the entity listing above is what answers it.
            reading = read_references(root)
            observations = observe(reading.references, wandb_runs)
        except (CaptureFailedError, ReportInputError, OSError, ValueError) as error:
            gaps.append(
                SourceGap(
                    source="the lineage records",
                    reason="run_costs_not_read",
                    detail=(
                        f"{_masked(str(error))}. The message says which prefix, and every "
                        "prefix `tools/report_run_costs.py:LINEAGE_PREFIXES` names is one "
                        "the audit reader role is granted, so this is a finding rather "
                        "than the expected answer. `attempt/` was the expected answer until "
                        "the grant was added: the role held `intent/` and `result/` while "
                        "the board synced `intent/` and `attempt/`, and it was refused every "
                        "night. Nothing is priced without it -- the whole cost mapping is "
                        "dropped rather than the attempt half, because `sync_bucket` raises "
                        "on a refused prefix -- so unlogged spend is reported as a count of "
                        "runs with no money against it, and every other finding is "
                        "unaffected."
                    ),
                    unanswered=(
                        "what the unlogged spend cost",
                        "which lineage records name a W&B run that does not exist",
                    ),
                )
            )

        # A call of its own, after the required prefixes, because this one is allowed to be
        # refused. The audit reader role does not hold `binding/` yet, and folding it into
        # the sync above would mean one expected denial taking the cost figures, the result
        # records and the reconciliation down with it -- which is precisely what `attempt/`
        # did to the cost mapping for months.
        try:
            if options.lineage_root is None:
                sync_bucket(
                    options.lineage_bucket,
                    Path(scratch),
                    profile=options.profile,
                    region=options.region,
                    prefixes=DEGRADING_LINEAGE_PREFIXES,
                )
            bindings, degraded = read_binding_records(
                options.lineage_root if options.lineage_root is not None else Path(scratch)
            )
        except (CaptureFailedError, ReportInputError, OSError, ValueError) as error:
            bindings = None
            gaps.append(
                SourceGap(
                    source="the account, from `binding/` records",
                    reason="binding_records_not_read",
                    detail=(
                        f"{_masked(str(error))}. This is the expected answer today: "
                        "`infra/iam/audit-reader-role.yaml` grants `intent/`, `attempt/` "
                        "and `result/` and not `binding/`, so the account side is the "
                        "tagging API alone and its window is whatever Batch still lists -- "
                        "roughly a week for a finished job. Two edits close it. Paste the "
                        "statement below under the policy, and add `binding/*` to the "
                        "`s3:prefix` condition on `ListLineageRecords`, which is an edit to "
                        "an existing statement rather than a paste. Both are needed: "
                        "`aws s3 sync` lists before it fetches, so a fetch grant whose "
                        "prefix is missing from the condition is refused at the first call "
                        "with nothing fetched. Then apply the stack from a laptop as "
                        "`infra/README.md` describes."
                    ),
                    unanswered=(
                        "which runs the tagging API has already forgotten",
                        "how much of the account side this board is counting over",
                    ),
                    remedy=MISSING_BINDING_GRANT,
                )
            )

    return build_board(
        wandb_runs=wandb_runs,
        resources=resources,
        outputs=outputs,
        bindings=bindings,
        refused_teams=refused,
        costs=costs,
        gaps=gaps,
        observations=observations,
        reference_reading=reading,
        degraded_bindings=degraded,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
    parser.add_argument(
        "--lineage-root",
        type=Path,
        default=None,
        help=(
            "a directory already holding intent/, attempt/, result/ and binding/ records, "
            "rather than syncing"
        ),
    )
    parser.add_argument("--lineage-bucket", default="sbsandbox-intern-edullm-lineage")
    parser.add_argument("--outputs-bucket", default=OUTPUTS_BUCKET)
    parser.add_argument("--entity", default=WANDB_ENTITY, help="the W&B entity to read")
    parser.add_argument("--wandb-secret", default=SECRET_NAME)
    parser.add_argument("--output", type=Path, help="write the board here rather than to stdout")
    parser.add_argument(
        "--wandb-observations",
        type=Path,
        help=(
            "write the W&B reference reconciliation here as JSON, one record per reference "
            "with its three-state answer. The markdown above is for a person; this is for "
            "anything that wants to count"
        ),
    )
    # No default profile. The audit runs on an assumed role and passes none, and a default
    # of `sbsandbox` would send it looking for an SSO session that is not there.
    parser.add_argument("--profile", default=None)
    parser.add_argument("--region", default="us-east-1")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    board = _collect(options)
    report = render(board)

    if options.output:
        options.output.write_text(report, encoding="utf-8")
    else:
        print(report, end="")

    if options.wandb_observations and board.reference_reading is not None:
        options.wandb_observations.write_text(
            json.dumps(
                observation_document(
                    board.observations,
                    reading=board.reference_reading,
                    entity=options.entity,
                ),
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    for gap in board.gaps:
        print(gap.reason, file=sys.stderr, flush=True)

    # THE RECONCILIATION IS PRINTED AND IS NOT IN THE EXIT CODE. A run that logged nowhere
    # and a record that names a run W&B does not have are both true and neither is
    # repairable: the result records are write-once and the workload's own command decides
    # whether it calls wandb.init(). Gating on either would hold this job red for ever, and
    # the next real finding would arrive at a job that was already red. The count is in the
    # verdict line and the runs are in a table, which is what a reader can act on.
    for entry in never_logged(board.observations):
        print(f"logged_nowhere {entry.reference.run_id}", file=sys.stderr, flush=True)

    # A definite finding outranks an unanswered question, which is the rule
    # tools/verify_deployed_stacks.py already follows and the reason is the same. Somebody
    # holding a run that saved nothing has to go and look at it whatever happened to the other
    # source, and what could not be read is printed at the top of the board rather than
    # encoded in the exit status.
    if board.disagrees:
        return EXIT_DISAGREES
    if board.gaps:
        return EXIT_UNUSABLE
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
