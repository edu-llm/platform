"""A committed change the account has not caught up with yet.

Two kinds of record live here and they share one mechanism, which is the reason they share
a module. :class:`PendingAmendment` is an IAM template amended in a commit and not yet
applied to the account; :class:`PendingRelease` is a packaged module changed in a commit
and not yet uploaded as a Lambda zip. Both describe the same window -- between the commit
that changes what the repository says and the act that changes what the account does -- and
both have to survive the same objection, that naming a difference is a way of excusing it.
The answers are the same in both cases and are set out below: a record says *what* it is
waiting on precisely enough to stop describing anything the moment that thing happens, says
why and what ends it, and fails loudly rather than quietly when it stops fitting.

The paragraphs that follow are about the IAM case, which came first and is where the
reasoning was worked out. :class:`PendingRelease` restates only what differs.

Every role this repository compares against a template is created or amended from a
laptop, because ``infra/README.md`` exists to keep role creation out of a pipeline. So a
template change and the deploy that realises it are two acts with a window between them,
and inside that window the comparison in :mod:`edullm_platform.role_drift` reports the
deployed role as ``NARROWER`` than the template. It is right to: the committed template
has stopped describing the account.

**Why this is a library module rather than a note in a test.** It was a note in a test,
and the cost showed up immediately. A test module knew which difference was expected;
:mod:`edullm_platform.phase1_capture` did not, so it reported the role as ``DRIFTED`` --
the same verdict a role widened in the console gets -- and every consumer downstream had
to re-derive "is this the expected one?" from whatever it happened to have. The proof
generator did not re-derive it at all: it treated *any* capture that stopped holding as
the pending case, so an expired capture and an undeployed amendment produced the same
skip. An expiry that reads as a deploy nobody has run yet is exactly the kind of quiet
substitution the freshness window exists to prevent.

So the record lives here, the capture reader consults it, and the state gets its own
verdict: :attr:`~edullm_platform.phase1_capture.CaptureVerdict.PENDING_DEPLOY`.

**Naming the state is not the same as excusing it.** A capture waiting on a deploy still
does not hold, the checks resting on it are still not satisfied, and the release tripwire
still refuses to write a bundle. What changes is that the refusal can say which of the two
things happened, and that a reader downstream can tell an expected difference from an
unexplained one without guessing.

**Every record is self-clearing, and that is the whole design.** An entry carries the two
things a ``DEFERRED`` criterion carries -- a :attr:`~PendingAmendment.reason` a reader can
weigh and a :attr:`~PendingAmendment.cleared_by` that says what ends it -- plus a third a
criterion does not: the :attr:`~PendingAmendment.findings` themselves, compared for
equality. So the record fails the moment the account stops differing in exactly this way,
in either direction. It cannot outlive the deploy it is waiting for, and it cannot quietly
absorb a second difference that arrives while it is open.

**What ends it is derived and not typed, since 2026-08-06.** ``cleared_by`` was a string
somebody wrote, and a string nothing compares is prose however much it reads like a control.
It was wrong in a live record and the audit that followed found it wrong in three of the
five: two Phase 3 records and the admission states record each named a deploy workflow, and
no deploy workflow in this repository applies a stack under ``infra/iam/``. All three would
have sent a reader to watch a merge that could not have cleared them. The field is gone.
:attr:`~PendingAmendment.cleared_by` is now a property, resolved role to template to stack
through :func:`declared_role_templates` and
:mod:`edullm_platform.stack_templates`, and ``tests/test_pending_amendment_stacks.py``
holds the resolution to the templates themselves.

**Every finding must be ``NARROWER``.** An undeployed template change can only leave the
account behind the template. A deployed role that grants something its template does not
is a security finding, and nothing pending explains one.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Final

from edullm_platform.phase2_evidence import PHASE2_ROLE_TEMPLATES
from edullm_platform.role_drift import (
    COMMITTED_ROLE_TEMPLATES,
    PHASE3_ROLE_TEMPLATES,
    RESEARCHER_ROLE_TEMPLATES,
    DriftDirection,
    RoleDriftFinding,
)
from edullm_platform.stack_templates import (
    UnmappedTemplateError,
    applied_from_a_laptop,
    stack_for_template,
)

__all__ = [
    "PENDING_AMENDMENTS",
    "PENDING_RELEASES",
    "RELEASABLE_FUNCTIONS",
    "RELEASE_COMMAND",
    "RELEASE_WINDOW",
    "PendingAmendment",
    "PendingAmendmentError",
    "PendingRelease",
    "PendingReleaseError",
    "ReleasableFunction",
    "ReleaseComparison",
    "ReleaseVerdict",
    "compare_release",
    "declared_role_templates",
    "one_record_per_function",
    "pending_amendments",
    "pending_for",
    "pending_release_for",
    "pending_releases",
    "releases_beyond_their_window",
]

class PendingAmendmentError(ValueError):
    """A recorded pending amendment is not something a reader could act on."""


def declared_role_templates() -> dict[str, str]:
    """Every role some committed template declares, across the registries merged here.

    The registries stay separate where they are defined, because a Phase 3 role drifting
    must not fail a Phase 1 capture. They are merged only here, and only to answer one
    question: is there a template that will ever compare this role? A pending amendment for
    a role nothing compares would never clear, because nothing would ever report the
    findings it is waiting to stop seeing.

    ``PHASE5_ROLE_TEMPLATES`` and ``DATASET_VALIDATOR_ROLE_TEMPLATES`` are absent and were
    absent before ``RESEARCHER_ROLE_TEMPLATES`` was added. That is a gap rather than a
    decision: an amendment naming the image resolver or the dataset validator would be
    refused here even though both are captured and compared. It is left alone deliberately,
    because widening this is a change with its own reviewer and this one is about a role
    being added.
    """
    return {
        **dict(COMMITTED_ROLE_TEMPLATES),
        **dict(PHASE2_ROLE_TEMPLATES),
        **dict(PHASE3_ROLE_TEMPLATES),
        **dict(RESEARCHER_ROLE_TEMPLATES),
    }


@dataclass(frozen=True)
class PendingAmendment:
    """A template amendment that is committed and has not been applied to the account.

    **Three fields, and the fourth was deleted because it was a fact typed twice.** A
    record used to carry a ``cleared_by`` string naming the stack whose application ends
    it. Nothing compared that string to anything, so it was prose wearing the clothes of a
    control, and on 2026-08-05 it was wrong in a live record: an amendment to
    ``sbsandbox-intern-edullm-lifecycle-lambda`` named
    ``sbsandbox-intern-edullm-phase3-batch-iam``, which is the stack for three entirely
    different roles. Somebody following it would have applied a template that never
    mentions the grant, watched the apply succeed, re-taken the capture, found the finding
    still there, and had no way to tell a failed deploy from a lying record.

    The stack is now derived rather than typed. A record names a role, exactly one
    committed template declares that role, and exactly one stack is applied from that
    template, so :attr:`cleared_by` is a lookup through
    :func:`declared_role_templates` and
    :func:`~edullm_platform.stack_templates.stack_for_template`. Both steps are total or
    the record is refused at construction. There is no longer a spelling of this record
    that names the wrong stack, because there is no longer a spelling of it that names a
    stack at all.

    What could not be derived stays in :attr:`reason`, which is honestly uncompared prose
    and always was. The follow-up capture, what breaks while the record stands, a bucket
    flag one oversized template needs: none of that is a fact this repository holds
    anywhere else, so deriving it would mean inventing it.
    """

    role_name: str
    reason: str
    findings: tuple[RoleDriftFinding, ...]

    def __post_init__(self) -> None:
        if not self.findings:
            raise self._fail(
                "records no findings; a pending amendment that expects no difference is "
                "a record with nothing to clear"
            )
        if not self.reason.strip():
            raise self._fail("does not say reason")
        # THE DERIVATION IS CHECKED HERE RATHER THAN WHERE IT IS READ, because a record
        # whose chain does not resolve is a record nobody can act on, and the place to
        # refuse it is the place it is written. Reading `cleared_by` for its side effect
        # rather than its value: it raises unless both steps land.
        _ = self.cleared_by
        ahead = [
            finding for finding in self.findings if finding.direction is not DriftDirection.NARROWER
        ]
        if ahead:
            raise self._fail(
                "records a finding that is not narrower: "
                + ", ".join(f"{one.direction.value} at {one.element}" for one in ahead)
                + ". An undeployed template change leaves the account behind the "
                "template; a role that grants more than its template is a security "
                "finding and no pending deploy explains it"
            )

    def _fail(self, problem: str) -> PendingAmendmentError:
        return PendingAmendmentError(f"pending amendment for {self.role_name}: {problem}")

    @property
    def template(self) -> str:
        """The one committed template that declares this role.

        Refused rather than answered for a role no registry declares, and the refusal is
        the one :func:`declared_role_templates` was written for: nothing would ever compare
        such a role, so nothing would ever report the findings the record is waiting to
        stop seeing, so the record could never clear.
        """
        try:
            return declared_role_templates()[self.role_name]
        except KeyError:
            raise self._fail(
                "no committed template declares that role, so nothing here will ever "
                "compare it and the record would never clear"
            ) from None

    @property
    def cleared_by(self) -> str:
        """The stack whose application ends this record.

        Derived, and the two steps are separate lookups rather than one table, because they
        are separately true: which template declares a role is a fact about the role
        registries, and which stack applies a template is a fact about how this repository
        deploys. Something that widened one without the other would break here rather than
        answer a plausible wrong name.
        """
        try:
            return stack_for_template(self.template)
        except UnmappedTemplateError as error:
            raise self._fail(
                f"{self.template} declares the role and no stack in "
                f"edullm_platform.stack_templates is applied from it, so there is no apply "
                f"that would clear this record. {error}"
            ) from None

    @property
    def needs_a_laptop(self) -> bool:
        """Whether ending this takes a person with an SSO session rather than a merge.

        Derived too, and it is the half three records got wrong before any of this was
        derived. Each named one of the three deploy workflows as the thing that would clear
        it. None of those workflows applies a single stack under ``infra/iam/``, because the
        deployer role holds no ``iam:CreateRole``, so all three described a merge that could
        not have ended them.
        """
        return applied_from_a_laptop(self.cleared_by)

    def describe_clearing(self) -> str:
        """What ends this record, in a sentence a reader can act on without looking it up."""
        how = (
            "from a laptop with an SSO session, per infra/README.md"
            if self.needs_a_laptop
            else "by the workflow that deploys it"
        )
        return (
            f"Apply {self.cleared_by} {how}, from {self.template}, which is the one "
            f"committed template that declares {self.role_name}. Then re-take the capture "
            "for this role and delete this record."
        )

    def explains(self, findings: Sequence[RoleDriftFinding]) -> bool:
        """Whether these are exactly the differences this record is waiting on.

        Equality rather than containment, in both directions. Containment would let a
        second difference arrive under cover of the first, and would go on reading as
        explained after the deploy removed only part of what is recorded.
        """
        return tuple(findings) == self.findings


def pending_amendments() -> tuple[PendingAmendment, ...]:
    """Every committed template amendment the account has not caught up with yet."""
    # Empty, which is the state this registry is meant to spend most of its life in. An
    # entry lives here only between a template amendment being committed and the laptop
    # deploy that realises it. Four have been removed so far: the Phase 2 deployer
    # amendment and the Phase 3 one -- a third job_workflow_ref for deploy-phase3-batch.yml
    # and the deploy-phase3-batch-stacks inline policy -- both on 2026-07-27 when
    # sbsandbox-intern-edullm-infra-deployer-iam was applied; the
    # sbsandbox-intern-edullm-batch-workload `read-the-dataset-airlock` policy on
    # 2026-08-04 when sbsandbox-intern-edullm-phase3-batch-iam was applied and the
    # re-capture reported no findings on any of the four Phase 3 roles; and the
    # sbsandbox-intern-edullm-admission-states queue enumeration later the same day.
    #
    # Removal rather than exemption is the rule. The findings are compared for equality,
    # so a record left here after its deploy fails rather than lingering, and nothing in
    # this module offers a way to keep one that no longer describes a difference.
    #
    # THE FOURTH REMOVAL IS WORTH READING BEFORE THE NEXT RECORD IS WRITTEN, BECAUSE IT WAS
    # NOT THE ORDINARY CASE THIS MODULE WAS BUILT FOR. The other three described a template
    # amendment waiting on somebody to find a laptop. That one described a template IAM
    # refused to store: rendered with the account's own partition, region and id,
    # run-admission-workflow came to 10599 bytes against a 10240 cap on the aggregate of a
    # role's inline policies, so the 2026-08-02 deploy failed with ServiceLimitExceeded,
    # the stack sat in UPDATE_ROLLBACK_COMPLETE, and re-running the deploy reproduced the
    # rollback. Five advertised compute profiles were unsubmittable for two days. What
    # cleared it was a change to the template -- collapsing each paired `-run` and `-run:*`
    # job-definition ARN into one `-run*`, which an IAM wildcard covers because it matches
    # ':' like any other character -- and the deploy of 2026-08-04, after which the
    # re-capture reported no findings on any of the three Phase 2 roles.
    #
    # A record whose `cleared_by` needs a code change rather than a deploy is a legitimate
    # use of this registry and reads exactly like the ordinary one from every consumer's
    # side, so say which it is in the text. The lasting fix for that particular gap is not
    # here: tests/test_phase2_infrastructure.py now measures the rendered policy against
    # IAM's cap, so a document the account will refuse fails before anybody deploys it.
    #
    # ONE RECORD FOR THIS ROLE AND NOT TWO, BECAUSE TWO PLANS AMENDED IT IN THE SAME WEEK.
    # The expiry janitor and the notifier each add a Lambda to a stack CI deploys, and
    # lambda:CreateFunction takes a role ARN the caller must be allowed to pass, so both
    # widened the same iam:PassRole statements; the janitor also needs the EventBridge
    # Scheduler verbs. This registry is keyed by role, and the drift a capture reports is the
    # sum of both, so splitting it into two records would describe an account state that
    # never exists. Applying the deployer stack once clears all of it.
    #
    # A THIRD PLAN JOINED THE SAME RECORD ON 2026-08-05 AND THE ARGUMENT DID NOT CHANGE.
    # The working tier's bucket is granted to the deployer by its exact name, so the same
    # one application of the deployer stack clears this alongside the other two. Joining
    # rather than adding a fourth record is not a preference: `explains` compares the
    # findings for equality, so two records for one role would each describe a difference
    # the account never has on its own, and whichever was read first would decide which
    # half counted as expected.
    #
    # WHY THE NEW FINDING IS A WHOLE STATEMENT RATHER THAN A WIDER RESOURCE LIST, WHICH IS
    # THE PART WORTH READING BEFORE THE NEXT S3 GRANT IS WRITTEN. Adding the bucket to the
    # existing prefix-scoped S3 statement would have been three fewer lines, and it would
    # have granted the deployer s3:DeleteBucket and s3:PutBucketPolicy over the working
    # tier as well. Splitting that statement instead -- sharing its read half between the
    # prefix and the exact name -- was measured and refused for a mechanical reason: a
    # deployed statement that becomes two template statements pairs against the larger of
    # them and reports the actions left behind as WIDER, and __post_init__ refuses a
    # finding that is not NARROWER. A record cannot describe that window at all, which is
    # the registry saying that the account should never be behind a template in that shape.
    #
    # AND THOSE THREE JOINED PLANS CAME APART AGAIN ON 2026-08-06, WHICH IS THE COST OF
    # JOINING AND IS STILL CHEAPER THAN THE ALTERNATIVE. The deployer stack was applied
    # while the record stood, and it closed three of the four findings and not the fourth,
    # so the record stopped fitting and had to be trimmed by re-reading the account. That is
    # the maintenance a joined record buys: a partial apply moves it rather than clearing
    # it. The alternative was never four records -- `explains` compares per role, so four
    # records for one role would each describe an account state that never exists, and after
    # the same apply three of them would be failing and unclearable rather than one of them
    # being shorter.
    #
    # ALL SIX RECORDS WERE CLEARED ON 2026-08-06 BY ONE SITTING OF HAND APPLIES. Five of
    # the six were one registration: #250 registered edullm-p1, and
    # tools/register_repository.py writes infra/ecr-repositories.yaml and
    # infra/iam/ecr-publisher-role.yaml and nothing else, so the repository had to be added
    # by hand to the publisher, both Batch roles, both GPU Batch roles and the admission
    # states role. Four stacks carried that: sbsandbox-intern-edullm-ecr-publisher-iam,
    # -phase3-batch-iam, -phase4-gpu-iam and -phase2-admission-service-roles. The deployer's
    # edullm-scratch grant cleared with -infra-deployer-iam and the recorder's
    # s3:GetObjectAttributes with -phase3-lifecycle-iam. Six records covered seven roles'
    # worth of drift, because the two GPU roles carry no committed capture and so reported
    # none of it.
    #
    # THREE OF THE SIX NAMED A WORKFLOW AS WHAT WOULD END THEM AND NO WORKFLOW COULD HAVE.
    # The batch-execution, batch-instance and admission-states records each said a
    # deploy-phase3-batch.yml or deploy-phase2-admission.yml run on merge to main would
    # clear them. Neither workflow deploys the stack that holds those roles: the Phase 3 one
    # applies phase3-outputs, phase3-network, phase3-batch, phase4-gpu, phase4-gpu-shapes,
    # notifications, phase3-events and janitor, and the Phase 2 one applies phase2-lineage,
    # phase2-artifacts and phase2-admission. Not one -iam stack appears in either, which is
    # infra/README.md's "Why IAM is laptop-only" holding exactly as written. So those three
    # records were waiting on an event that was never going to happen, and they would have
    # stood until somebody read the workflow rather than the record.
    #
    # WHAT TO WRITE INSTEAD, BECAUSE THE FAILURE WAS CHEAP TO MAKE AND EXPENSIVE TO SPOT. A
    # cleared_by naming a workflow is a prediction about that workflow's contents, and
    # nothing here compares the two, so the register cannot tell a run that has not happened
    # from a run that will never contain the stack. Where the role lives in an IAM stack,
    # say the apply and say it is a laptop one.
    amendments: tuple[PendingAmendment, ...] = ()
    # The role-is-declared check used to be a loop here. It is in ``__post_init__`` now,
    # because the same lookup is the first step of deriving ``cleared_by`` and two places
    # asking the same question is how the answers part company.
    names = [amendment.role_name for amendment in amendments]
    if len(set(names)) != len(names):
        raise PendingAmendmentError(f"one role may carry one pending amendment; got {names}")
    return amendments


PENDING_AMENDMENTS: Final = pending_amendments()


def pending_for(role_name: str) -> PendingAmendment | None:
    """The amendment recorded for this role, or ``None`` if none is."""
    return next(
        (amendment for amendment in PENDING_AMENDMENTS if amendment.role_name == role_name), None
    )


# --------------------------------------------------------------------------------------
# A packaged change that is committed and has not been uploaded yet
# --------------------------------------------------------------------------------------
#
# THE SAME SHAPE AS AN UNDEPLOYED AMENDMENT, ARRIVED AT FROM THE OTHER END, AND IT WAS
# COSTING AN ADMIN MERGE EVERY TIME. `tools/build_admission_lambda.py` packages the modules
# the handler imports and the seven config files it reads, so a change to any of them moves
# the release digest and `tests/test_released_zips.py` goes red until the zip is
# uploaded and `infra/admission-validator-release.yaml` records it. The upload needs AWS
# credentials, and the only path to them that is always available is
# `deploy-phase2-admission.yml`, which runs from `main` and nowhere else.
#
# Those three facts make a cycle: the zip cannot be uploaded until the change is on `main`,
# and the change cannot merge green while the tripwire is red. Every change to a packaged
# module therefore lands by an administrator merging past a required check -- twice on
# 2026-08-04 alone, once for this structural reason and once for a plain missing signature.
# That is the real cost rather than the inconvenience: a bypass that happens routinely stops
# being read, and it is the same bypass that would let a genuinely broken change through.
#
# WHAT IS NOT DONE HERE, BECAUSE IT WOULD DEFEAT THE CONTROL.
# `infra/admission-validator-release.yaml` sets out both of the tempting repairs and refuses
# them: writing a digest for a zip nobody uploaded makes the record assert the one thing it
# exists to deny, and suppressing the test is the same act differently spelled. Neither is
# what happens below. The records still tie a digest to an S3 object version and still
# describe what is deployed rather than what builds; nothing added here can write one, and a
# release still has to be cut before the tripwire passes.
#
# What changes is only that the interval between "the tree moved" and "the release was cut"
# can be *named* -- by a record that says which function, which two digests, and which
# command -- and that an unnamed interval still fails exactly as loudly as it did before.


@dataclass(frozen=True)
class ReleasableFunction:
    """One Lambda a release can be cut for, and where what is deployed is written down."""

    #: What a reader calls it. Matches `name` on tools/release_lambda.py's Function.
    display: str
    #: Repository-relative path to the record naming the deployed digest and object version.
    release_record: str


#: Every function `tools/release_lambda.py` can release, restated rather than imported.
#:
#: A library module reaching into `tools/` is the wrong direction -- it would put the
#: release tool's subprocess and argparse machinery inside everything that reads this
#: registry -- so the two spellings are held together by a test instead.
#: `tests/test_pending_releases.py` compares this against `release_lambda.FUNCTIONS` in both
#: directions, so a third function added to the tool and not here is a failure by name.
#:
#: It is here at all for the reason `declared_role_templates` exists above: a pending record
#: for something nothing ever compares would never clear, because nothing would ever report
#: the difference it is waiting to stop seeing.
RELEASABLE_FUNCTIONS: Final[dict[str, ReleasableFunction]] = {
    "validator": ReleasableFunction(
        display="admission validator",
        release_record="infra/admission-validator-release.yaml",
    ),
    "recorder": ReleasableFunction(
        display="lifecycle recorder",
        release_record="infra/lifecycle-recorder-release.yaml",
    ),
    "janitor": ReleasableFunction(
        display="expiry janitor",
        release_record="infra/expiry-janitor-release.yaml",
    ),
    "notifier": ReleasableFunction(
        display="notifier",
        release_record="infra/notifier-release.yaml",
    ),
}

#: How long a recorded pending release may stand before it stops explaining anything.
#:
#: A quarter of the thirty days `edullm_platform.evidence.FRESHNESS_WINDOW` gives a capture,
#: and the gap between the two numbers is the gap between what each one is waiting on. A
#: role capture waits on somebody finding a laptop, a browser login and a set of credentials
#: the broker has been known to revoke; a release waits on one command run against a tree
#: that is already on `main`, and the only reason it could not be run sooner is that the
#: workflow holding the credential refuses to run from a branch.
#:
#: So the window has to be longer than review latency and shorter than the point at which
#: whoever moved the packaged bytes has stopped thinking about them. Seven days is both.
#: What it must not be is absent: a record with no expiry turns the tripwire off for one
#: function permanently the first time somebody forgets, and a forgotten release looks
#: exactly like a performed one from every consumer's side.
RELEASE_WINDOW: Final = timedelta(days=7)

#: A sha256 written the way a release record writes it. Lowercase is part of the shape:
#: an uppercase digest never compares equal to one the builder reports, so a record holding
#: one would read as unexplained skew for a defect in a committed file.
_HEX_DIGEST: Final = re.compile(r"^[0-9a-f]{64}$")

#: The command that clears a pending release, and the only one that can. Recorded entries
#: are required to name it, so a reader who meets a skipped tripwire is looking at the
#: instruction rather than at a description of one.
RELEASE_COMMAND: Final = "tools/release_lambda.py"


class PendingReleaseError(ValueError):
    """A recorded pending release is not something a reader could act on."""


def _today() -> date:
    return datetime.now(tz=UTC).date()


@dataclass(frozen=True)
class PendingRelease:
    """A change to a packaged module that is committed and has not been uploaded yet.

    Every field is compared against something, which is what stops this being an exemption
    with a date on it. :attr:`builds_to` and :attr:`released` are compared for equality
    against the pair the tripwire actually finds, in both directions and for the same reason
    :meth:`PendingAmendment.explains` does it that way -- containment would let a second
    packaged change arrive under cover of the first, and would go on reading as explained
    after the release that cleared it. :attr:`recorded_on` is compared against the clock.
    """

    #: A key of :data:`RELEASABLE_FUNCTIONS`, so the record names something releasable.
    function: str
    #: Why the bytes moved, in a sentence somebody reading a skipped test can weigh.
    reason: str
    #: The exact command that ends this. Required to name :data:`RELEASE_COMMAND` and this
    #: function, because a skip whose reader has to go and look up the procedure is a skip
    #: that gets ignored.
    cleared_by: str
    #: What a zip built from this tree hashes to. Moves if anything else the package
    #: carries is edited while this record is open, at which point it stops explaining.
    builds_to: str
    #: What the release record still names, which is what the account is running. Moves if
    #: somebody else cuts a release, at which point this stops explaining too.
    released: str
    #: The day this was written down. Not the day the change was made and not a deadline
    #: somebody chose: it is what :data:`RELEASE_WINDOW` is measured from.
    recorded_on: date

    def __post_init__(self) -> None:
        if self.function not in RELEASABLE_FUNCTIONS:
            raise self._fail(
                f"names no releasable function; {sorted(RELEASABLE_FUNCTIONS)} are the ones "
                "tools/release_lambda.py can cut a release for, and a record for anything "
                "else would never clear because nothing would ever compare it"
            )
        for name in ("reason", "cleared_by"):
            if not getattr(self, name).strip():
                raise self._fail(f"does not say {name.replace('_', ' ')}")
        for name in ("builds_to", "released"):
            if _HEX_DIGEST.fullmatch(getattr(self, name)) is None:
                raise self._fail(
                    f"records {name} as {getattr(self, name)!r}, which is not sixty-four "
                    "lowercase hexadecimal characters and so is not a digest anything here "
                    "will ever compare equal to"
                )
        if self.builds_to == self.released:
            raise self._fail(
                "records the same digest as built and released; a pending release that "
                "expects no difference is a record with nothing to clear"
            )
        if RELEASE_COMMAND not in self.cleared_by:
            raise self._fail(
                f"does not name {RELEASE_COMMAND} in cleared_by, and that is the only thing "
                "that ends a pending release. A skip that does not carry the command sends "
                "its reader to the procedure to work one out, which is how a labelled skip "
                "becomes an ignored one"
            )
        if not any(
            flag in self.cleared_by for flag in (f"--function {self.function}", "--function all")
        ):
            raise self._fail(
                f"gives a command that does not select {self.function}, so following it "
                "would release some other function and leave this record standing"
            )
        if self.recorded_on > _today():
            raise self._fail(
                f"is dated {self.recorded_on.isoformat()}, which is in the future. The "
                "window is measured from this field, so a date ahead of the clock is a way "
                "to stand for longer than RELEASE_WINDOW allows"
            )

    def _fail(self, problem: str) -> PendingReleaseError:
        return PendingReleaseError(f"pending release of the {self.function}: {problem}")

    @property
    def releasable(self) -> ReleasableFunction:
        return RELEASABLE_FUNCTIONS[self.function]

    @property
    def expires_on(self) -> date:
        """The last day this record explains anything."""
        return self.recorded_on + RELEASE_WINDOW

    def expired(self, today: date | None = None) -> bool:
        return (today or _today()) > self.expires_on

    def explains(self, *, built: str, released: str) -> bool:
        """Whether this is exactly the difference the record is waiting on.

        Equality on both digests, so the record cannot outlive either half of what it
        describes: not the release it is waiting for, and not the tree it was written
        against.
        """
        return (built, released) == (self.builds_to, self.released)

    def describe(self) -> str:
        """What a reader who meets this as a skipped test needs in order to act.

        Names the function, both digests, the record that would carry the new one, the day
        this stops being an explanation, and the command. A skip that says only that
        something is expected tells a reader that things are fine, which is the failure this
        whole module is trying not to commit.
        """
        return (
            f"Waiting on a release of the {self.releasable.display}. This tree builds "
            f"{self.builds_to} and {self.releasable.release_record} records {self.released} "
            f"as deployed, so the account is running the previous bytes on purpose and for "
            f"a recorded reason: {self.reason.strip()} "
            f"Clear it by running `{self.cleared_by.strip()}` from main and committing the "
            f"two files it edits. Recorded {self.recorded_on.isoformat()}; this record stops "
            f"explaining the difference after {self.expires_on.isoformat()}, when this test "
            "fails again whether or not the release has been cut."
        )


def pending_releases() -> tuple[PendingRelease, ...]:
    """Every committed change to a packaged module that has not been uploaded yet."""
    # Empty, which is the state this registry is meant to spend most of its life in. An
    # entry lives here only between a change to a packaged module being committed and the
    # release cut from `main` that uploads it, and that interval is a review plus one
    # command.
    #
    # Removal rather than exemption is the rule, and here it is not even a rule so much as
    # an inevitability: `tools/release_lambda.py` writes the new digest into the release
    # record, at which point `released` no longer matches the file and the entry fails
    # rather than lingering. The tool cannot remove the entry itself -- it edits two files
    # by regular expression and adding a third that deletes Python would be a worse tool --
    # so what happens instead is that the same commit carrying the release deletes it, and
    # the suite says so if it does not.
    #
    # WHAT AN ENTRY MUST NOT BE USED FOR. A digest that moved for a reason nobody has read
    # is the exact thing the tripwire exists to catch, and writing an entry to make it green
    # is writing down a sentence that is not true -- `reason` is compared against nothing,
    # so it is the one field here that can lie. What keeps that honest is that the entry is
    # a diff in a reviewed pull request, and that the two digests beside the reason are not
    # guessable: they have to be read off a build and off the release record, and a reader
    # can rebuild the zip and check both.
    #
    # THREE HAVE BEEN REMOVED SO FAR AND THE FIRST IS THE WHOLE LIFECYCLE IN ONE ENTRY. The
    # validator record written on 2026-08-05 named the two refusals #221 removed from the
    # packaged half of the platform, and it was deleted the same day in the commit that cut
    # the release. The deploy is what actually ends it: uploading the zip and editing the
    # template makes `released` match `builds_to`, at which point `compare_release` reports
    # the record as SKEWED rather than letting it stand. So the deletion is not tidiness. A
    # record left here would go on absorbing the next unexplained difference that happened
    # to arrive on the same pair of digests.
    #
    # THE OTHER TWO WERE CLEARED TOGETHER ON 2026-08-06 AND THEY DEMONSTRATE THE ONE THING
    # THE FIRST DID NOT. The validator record had by then been extended seven times -- the
    # org.yaml header rewrite, the frontload-cl corpora, the workflow rename, the retired
    # enforcement, the identity table, schema version two, and registering edullm-p1 --
    # because `one_record_per_function` refuses two entries for one zip and is right to: a
    # zip carries whatever the tree holds when it is built, so there is one difference
    # between the account and this tree however many changes went into it. The recorder
    # record was extended once, by the eval-metrics read.
    #
    # AND THE VALIDATOR WAS RELEASED TWICE IN THE SPACE OF AN HOUR, WHICH IS THE PART WORTH
    # KEEPING. The first release was cut from main at 985c2cb; #250 then registered
    # edullm-p1, which writes config/repositories.yaml and config/workload-catalog.yaml, and
    # both are in ADMISSION_CONFIG. So a zip that was current when it was uploaded was stale
    # before it was merged, through no mistake by either change. The second release was cut
    # from the rebased tree and is the one the template points at. A release is a snapshot of
    # a tree and not a claim about main, which is why the digest is worth reading off the
    # account rather than inferred from the fact that a release happened.
    #
    # THE POLICY v5 PAIR WAS OPENED AND CLEARED ON 2026-08-05, AND THE CLEARING IS THIS
    # DELETION. Both zips were uploaded from main by
    # `deploy-phase2-admission.yml -f release_lambdas=true`, the two version ids and digests
    # were pasted into the templates and the release records in the pull request that
    # carries this line, and `compare_release` now finds both records matching the tree. The
    # validator entry was the first one ever written that a submitter could have met: it
    # named the window in which admission classified against v4 while config/policy.yaml on
    # main said v5, and the window is closed.
    #
    # AND IT WAS OPEN AGAIN THE SAME DAY, FOR THE FIVE DATASET REGISTRATIONS, AND CLEARED BY
    # THE RELEASE THAT CARRIES THIS DELETION. That was the third validator record of
    # 2026-08-06 and the shape was by now familiar rather than alarming:
    # config/datasets.yaml is in ADMISSION_CONFIG, so a registration is a release, and a
    # release cut before the registration merged cannot carry it. What is worth noticing is
    # that the last four entries in a row were all the kind a submitter can meet, where
    # every entry before them was a digest moving with no behaviour behind it. The register
    # is doing the job it was built for rather than absorbing noise.
    #
    # IT WAS ALSO EXTENDED ONCE MORE BEFORE IT CLEARED, AND THAT IS THE PART WORTH READING.
    # The record first named 7a149fc4, a zip cut from 6bbb42e and uploaded; while it sat in
    # review #271 added CheckpointPayload to contracts/results.py, which the validator
    # imports without reading, so the tree started building b8db05da and the uploaded zip
    # stopped being the one owed. The entry absorbed that correctly -- one function, one
    # record, whatever the tree holds when it is built -- and the release was re-cut from
    # 6f90582 rather than merged and repaired afterwards.
    #
    # THE HABIT THAT CAUGHT IT IS RE-DERIVING THE PAIR, AND THE TIMING OF THE RE-DERIVATION
    # IS THE LESSON. Checking before dispatching is what says the release is the one owed;
    # checking again before pasting is what says it still is. The first check passed, the
    # second failed, and only the second was load-bearing, because the interval that moves
    # is the one between the upload and the merge.
    #
    # A FIFTH VALIDATOR ENTRY OPENED FOR THE REFUSAL THAT CLOSED THE HOLE THE FOURTH ONE
    # OPENED, AND IT IS CLEARED BY THE RELEASE THAT CARRIES THIS DELETION. Registering the
    # five corpora made pretrain/fineweb2-equal-bytes nameable, and that corpus is raw text
    # in a trainable family, so the release that ended a submitter's disagreement with their
    # own tooling also put a silent hazard within reach. Both are correct changes and the
    # second is owed to the first.
    #
    # IT IS THE FIRST ENTRY IN THIS REGISTER WHERE THE ACCOUNT WAS THE PERMISSIVE SIDE, AND
    # THAT IS WORTH KEEPING AS THE SHAPE TO PREFER. Every entry before it described a tree
    # that had learned something the deployed zip had not, so the refusal a submitter met
    # came from inside AWS after the approval gate. This one ran the other way: the tree
    # refused and the account admitted, so for the ninety seconds it stood, a submitter met
    # the refusal on their own laptop from `edullm check`, ahead of the gate and ahead of
    # anybody else's attention. A change that has to sit in this window should be arranged
    # to sit in it this way round where it can be.
    #
    # A SIXTH AND SEVENTH ENTRY, BOTH CLEARED BY THE RELEASE THAT CARRIES THIS DELETION, AND
    # A THIRD ZIP THAT SHOULD HAVE HAD AN ENTRY AND DID NOT. The recorder and the validator
    # both moved on ResultManifest gaining status_reason and container_reason, and both were
    # recorded here before the merge. The notifier moved on the same contract, through
    # notifications/facts.py importing CheckpointListingOutcome from it, and was recorded
    # nowhere.
    #
    # NOTHING CAUGHT THE THIRD, AND THE REASON IS THE INTERESTING PART. The recorder and the
    # validator each have a test that builds the zip and compares it with the release record,
    # so each went red the moment the contract moved and each got an entry because a red test
    # asked for one. The notifier has no such test, so its zip drifted in silence -- and it
    # was already drifting before this change arrived, from 35a2634ce885 against a recorded
    # b55a71d58701. The register is only as complete as the tripwires that feed it, and one
    # of the four functions has none.
    #
    # All three are released now, so this deletion is bookkeeping rather than a decision. The
    # notifier's missing tripwire is not, and it outlives these entries.
    #
    # IT WAS CLOSED THE SAME NIGHT, AND NOT BY GIVING THE NOTIFIER A FOURTH COPY. The three
    # that existed were three copies of one idea, each written by copying the last, and that
    # arrangement is why the fourth function was missed: adding the tripwire was a step
    # somebody had to remember rather than something the register did on its own. It is one
    # test now, `tests/test_released_zips.py::test_the_released_zip_is_the_one_this_tree_builds`,
    # parametrized over `tools/release_lambda.py`'s FUNCTIONS -- the table a function has to be
    # in before a release can be cut for it at all -- so a fifth feeds this register from the
    # day it is added rather than from the day somebody deploys by hand and notices.
    #
    # Two further silences went with it. Every `AWS::Lambda::Function` under `infra/` is now
    # held to being releasable, so a function CI can deploy and no record describes fails on the
    # change that declares it; and every `tripwire` citation is resolved rather than read, which
    # is the check that would have caught this one. The notifier's named a real module full of
    # real assertions and no digest comparison, and both tools that cite it believed it.
    #
    # AN EIGHTH, AND IT IS THE FIRST ONE HERE THAT CARRIES NO CODE AT ALL. Every entry above
    # moved a digest by changing a module; this one moves it by editing
    # config/organization.yaml, which `ADMISSION_CONFIG` in tools/build_admission_lambda.py
    # packages into the zip. That makes a roster change -- the most ordinary edit anybody
    # makes to this repository, and the one most likely to be made by somebody who has never
    # read this file -- a change that drifts the deployed validator.
    #
    # Worth writing down as a shape rather than only as this instance. Seven of the eight
    # entries in this register have been code, so the reading a person carries away from it is
    # that releases follow code, and the entry that will be missed is the one where somebody
    # adds a line to a YAML file and merges it. The tripwire catches it, which is the whole
    # argument for the tripwire; nothing warns them in advance.
    #
    # AND A NINTH, WHICH IS THE SAME EDIT MOVING A SECOND ZIP, AND IT WAS INVISIBLE UNTIL THE
    # CHECK ABOVE LANDED. The eighth entry was written believing config/organization.yaml
    # reached one function. It reaches two: `NOTIFIER_CONFIG` in tools/build_notifier_lambda.py
    # names organization.yaml, workload-catalog.yaml and execution-targets.yaml, so the roster
    # moves the notifier's packaged bytes by exactly the same mechanism that moves the
    # validator's. Nothing said so at the time, because the notifier was the one function of
    # the four with no digest comparison, which is the silence the paragraph above describes.
    #
    # THIS IS THAT PARAGRAPH'S ARGUMENT COLLECTING ON ITSELF WITHIN THE HOUR, AND IT IS THE
    # reason to prefer one parametrized check over three copies rather than merely a tidier
    # arrangement. The eighth entry and the consolidation were written in the same evening by
    # different hands; the entry named one function because the author could only see the
    # functions that had tripwires, and the consolidation gave the fourth one a tripwire
    # without knowing an unrecorded change to it was already in flight. They met at the merge,
    # where the new case went red on the notifier and printed both digests and the record to
    # write. Neither change could have found this alone.
    #
    # It grants nothing and it changes nobody's authorization, for the reason the eighth entry
    # gives at length: the roster edit binds a second AWS role to a login already on the
    # roster and excludes two task roles nobody can assume. What the notifier reads the roster
    # for is deciding who to tell about a run, so the window is a notification addressed by
    # the roster as it was, which is the same reporting gap the entry above describes and not
    # a second kind of hazard.
    #
    # THE EIGHTH WAS CLEARED BY ONE RELEASE AND THE NINTH BY THE ONE THAT CARRIES THIS
    # DELETION, WHICH IS THE FIRST TIME THIS REGISTER HAS BEEN CLEARED IN TWO PASSES. The
    # validator zip was built from 77e5af9, uploaded as object version
    # CEH5XuEEq7XCVbt4GX7eqmgQFLtH_57_, and both its files name da7313e1. Splitting them was
    # right: two unverified digests in one diff is two claims the account can only confirm one
    # at a time, and the second pass had the first already answered by
    # tools/verify_deployed_lambdas.py before it began.
    #
    # The ninth is the notifier, moved by the same roster edit through NOTIFIER_CONFIG in
    # tools/build_notifier_lambda.py, released 2026-08-06 as object version
    # yfZX9j6aaYvhNyGNgaBr8pWxkJOd93pV, with infra/notifier-release.yaml and
    # infra/notifications.yaml both now naming 3b54966b. What it changes is who a run is
    # reported to, so the window it closes was notifications addressed by the roster as it
    # was, and no authorization moved in either direction.
    #
    # A RELEASE IS A STAGING STEP AND THE DEPLOYED BYTES DO NOT MOVE UNTIL THE STACK DOES.
    # The upload and the two template edits are all this deletion rests on; what makes the
    # account run 3b54966b is deploy-phase3-batch.yml, which fires on a push to main touching
    # infra/notifications.yaml. So the digest worth reading is the one Lambda answers with
    # after that, and tools/verify_deployed_lambdas.py is what reads it. A workflow log saying
    # the upload succeeded is not the same claim.
    #
    # A TENTH, ELEVENTH AND TWELFTH, ALL ONE CONTRACT FIELD, AND THE FIRST TIME ALL THREE ZIPS
    # HAVE MOVED TOGETHER FOR A REASON THAT IS THE SAME REASON. ResultManifest.attempt_id
    # became nullable, so a job Batch never places writes a result naming why instead of
    # writing nothing. The recorder is the function that does it; the validator and the
    # notifier move because they import the contract, which is the mechanism the sixth,
    # seventh and the unrecorded third describe, arriving again with all three tripwires in
    # place this time. Each was checked against a build of origin/main first, and all three
    # matched their release records there, so this change is the whole of the difference.
    #
    # THE WINDOW IS AN OLD RECORD READING BETTER THAN A NEW ONE, WHICH IS THE HARMLESS
    # DIRECTION. The deployed recorder goes on writing no result for a job that never
    # placed -- exactly what it does today -- so nothing regresses and nothing a submitter
    # can meet changes. What waits is the improvement. The validator and the notifier read
    # the contract and neither reads this field, so for them the window is bytes and no
    # behaviour at all.
    #
    # THE TENTH AND ELEVENTH WERE CLEARED BY THE RELEASES THAT CARRY THIS DELETION AND THE
    # TWELFTH WAS DELIBERATELY LEFT STANDING, WHICH IS THE FIRST TIME THIS REGISTER HAS BEEN
    # CLEARED IN PART ON PURPOSE. The recorder was uploaded as object version
    # x3ZwR.bxnokkwdBGyk9RV9udNpANux8y and the validator as
    # LNv7E3nuhEowl_a7C7BQZys.g7190V13, both cut from 5ca93d2, and both templates and both
    # release records name the digests those builds produced.
    #
    # The notifier entry that stood here was not an oversight and it is gone now, which is
    # the arrangement it asked for rather than a departure from it. It said in as many words
    # that edullm/the-approval-message was in flight over exactly this zip, that releasing
    # the notifier from under it would upload bytes somebody was midway through changing,
    # and that whoever merged that branch should cut the release and delete the entry in the
    # same commit. That branch is this one. The notifier was uploaded as object version
    # vjBGeKHvXZfL13W4gFrrvlQlKgtaS..W, and its template and release record both name the
    # digest that build produced.
    #
    # So the deferral worked exactly as intended and cost one function one release rather
    # than two, which is what the "released twice in the space of an hour" paragraph above
    # was written to prevent. What is worth keeping from it is the rule rather than the
    # instance: a zip a branch is rewriting is one to leave alone and record, not one to cut
    # from main because the register looks untidy.
    #
    # A THIRTEENTH WAS OPENED AND CLEARED WITHIN THE HOUR, AND WHAT IS WORTH KEEPING IS THAT
    # THE THING IT WAS WAITING FOR HAD ALREADY HAPPENED WHEN IT WAS WRITTEN. The janitor's
    # zip carries researcher_lane.py for two tag keys and a role name, and #318 moved that
    # module by routing `load_lane_settings` through
    # edullm_platform.reviewed_configuration. janitor_handler.py imports WARNING_TAG_KEY and
    # LaneSettings from it and builds its settings in `_settings_from_environment`, so it
    # never calls the function that changed and nothing it does can differ across the
    # release.
    #
    # The entry declined to cut the release because a second branch was in flight over the
    # same zip, reworking the handler so one unreachable machine cannot end a sweep, and it
    # said whichever landed second should cut it. That branch was #301 and it had merged at
    # 05:17:08Z, two and a half hours before #318 -- and it had cut and deployed its own
    # release a minute later, which is why the account was running 10f94f8a7082 rather than
    # 11a0f7a07e26. So the entry was written waiting on an event that had already passed.
    #
    # WHY THAT WAS HARD TO SEE FROM THE BRANCH, BECAUSE IT WILL BE HARD TO SEE AGAIN. Every
    # merge here is a squash, so the branch's own commits stay unreachable from main and
    # `git rev-list --count origin/main..origin/<branch>` answers a non-zero number forever.
    # origin/edullm/janitor-one-machine-cannot-stop-the-sweep still reads two commits ahead
    # today and has been merged since 05:17. A branch that looks unmerged is not evidence
    # that it is; the pull request state is, and so is the digest -- building the zip at
    # each of the last twenty commits of main showed the janitor moving to 10f94f8a7082
    # exactly at #301's commit and not moving again until #318's, which settles both halves
    # at once and needs nobody's memory.
    #
    # A FOURTEENTH, AND IT IS THE JANITOR AGAIN THROUGH THE SAME MODULE, WHICH IS WORTH
    # NOTICING RATHER THAN JUST RECORDING. Adding a reviewed configuration file means adding
    # a ConfigFile member, because tests/test_config_resolution.py holds the vocabulary and
    # the contents of config/ level in both directions. researcher_lane.py imports that enum
    # and the janitor's zip carries researcher_lane.py for two tag keys and a role name, so
    # a new line in a StrEnum moves a Lambda digest. That is the second time this register
    # has opened for that exact path in a day, and the coupling is the finding: the file the
    # header of reviewed_configuration.py went out of its way to keep the vocabulary *out
    # of* is config.py, and it reached three zips through a different module anyway.
    # A FIFTEENTH AND A SIXTEENTH, BOTH FROM ONE CORRECTED COMMENT, WHICH IS THE CHEAPEST
    # EDIT THIS REGISTER HAS EVER OPENED FOR AND IS WORTH RECORDING AS SUCH. config/
    # policy.yaml is packaged verbatim by two builders -- it is in ADMISSION_CONFIG and it
    # joined NOTIFIER_CONFIG when the approval message started reading it -- so a comment in
    # it moves two zips and no behaviour whatever. Isolated rather than assumed: with the
    # three source files in this change restored and policy.yaml alone left edited, both
    # tripwires fire and no other does, and with policy.yaml restored and the three source
    # files edited, none does.
    #
    # WHAT THE COMMENT SAID AND WHY IT COULD NOT STAY. The v5 note justified retiring
    # routine_maximum_attempts partly on retry_without_a_checkpoint_contract "refus[ing] a
    # retry that would restart from nothing", and it does not: it refuses more than one
    # attempt on a workload profile carrying no checkpoint contract, which is a fact about
    # config/workload-catalog.yaml rather than about the codebase that would have to resume.
    # Two of the six registered repositories pass it and restart from step 0.
    #
    # ALL THREE WERE CLEARED BY ONE COMMAND ON 2026-08-06, AND THE SHAPE OF THAT IS THE POINT
    # RATHER THAN THE TIDYING. The register held three entries for three unrelated causes: the
    # janitor for a ConfigFile member #351 had to add, and the validator and the notifier for
    # the corrected policy.yaml comment above. The change that cleared them was doing neither
    # of those things -- it was correcting a third file, config/workload-catalog.yaml, which
    # told anybody reaching for the unobtainable gpu-8xh100 that gpu-8xa100 was "eight 80 GB
    # cards" when config/accelerators.yaml measures p4d.24xlarge at 40,960 MiB per device.
    #
    # `tools/release_lambda.py --function all` cut one release per function and each carried
    # everything its tree held, which is the only way a zip can be released: it is built from
    # the working tree and not from a change, so the validator's new bytes carry the policy
    # comment *and* the catalogue correction, and the janitor's carry the ConfigFile member
    # even though nothing in this change touched it. Three causes, three releases, not five.
    # The recorder was left alone because its builder names no file under config/ and no
    # module that moved, and the tool skipped it rather than being told to.
    #
    # WHAT THAT COSTS SOMEBODY READING THIS LATER. A release note cannot be written per cause,
    # because the digest has no per-cause decomposition -- the only true statement about the
    # bytes is the tree they were built from. So the commit is the record, and the reason each
    # entry above gave for its own difference is kept in this comment rather than deleted with
    # the entry, because a digest that moved for three reasons is a digest nobody can explain
    # from the register once the register is empty.
    #
    # A SEVENTEENTH, AN EIGHTEENTH AND A NINETEENTH, AND THE FIRST OF THEM IS THE ONLY ENTRY
    # THIS REGISTER HAS HELD WHOSE WINDOW A SUBMITTER MEETS AS A REFUSAL RATHER THAN AS A
    # WRONG LABEL. Policy v6 gives the day a ceiling on what it will commit with nobody asked,
    # and the compile job raises a submission from automatic to routine when it is crossed.
    # The deployed validator cannot re-derive that -- the ledger is a branch in GitHub, not
    # anything in AWS -- so it is taught instead to accept the lead gate for a run it derives
    # as automatic, which is `ApprovalEnvironment.satisfies`.
    #
    # UNTIL THIS IS RELEASED, EVERY RUN THE CEILING ROUTES IS REFUSED AFTER A LEAD RELEASES
    # IT. The deployed zip carries the old equality check and config/policy.yaml at v5, so a
    # raised submission reaches admission, fails `approval_environment_mismatch` and writes a
    # decision record saying so. Nothing spends money in that window and nothing runs that
    # should not; what it costs is one lead's click per submission that crosses the ceiling,
    # and a refusal whose text does not mention the ceiling at all.
    #
    # So that record was not the usual "a digest moved and nobody will notice", and it is the
    # only entry this register has held that was cleared inside the hour it was opened. The
    # ordering it named is the ordering that was followed: merge, then
    # `deploy-phase2-admission.yml -f release_lambdas=true` from main, then the version id
    # and digest into infra/admission-validator-release.yaml and
    # infra/admission-state-machine.yaml, which is where the reason for that release now
    # lives. Its entry is gone from here and the window it described is closed.
    #
    # THE OTHER TWO ARE THE OPPOSITE KIND OF ENTRY AND THEY STAY, WHICH IS THE DECISION WORTH
    # RECORDING RATHER THAN THE TIDYING. The recorder is here for the first time, because its
    # builder names no file under config/ and the last sweep skipped it for that reason.
    # Neither the recorder nor the notifier classifies anything, so neither reads
    # the ceiling and neither behaves differently by a byte of it. What moved their zips
    # is that both package `contracts/admission.py` and `contracts/policy.py`, which
    # gained `satisfies` and `automatic_daily_ceiling_usd`.
    #
    # THE RECORDER'S ZIP IS ALREADY IN THE BUCKET AND NOTHING POINTS AT IT, WHICH IS THE
    # RIGHT PLACE FOR IT. `--function all` uploads both admission zips, so the release that
    # closed the validator's window built and uploaded the recorder's too. Repointing
    # infra/batch-events.yaml at it would put a Lambda nobody changed through a phase-3
    # CloudFormation update to close a window that costs nothing, so the object is left
    # unreferenced and the difference is left here. An artifact addressed by version that
    # nobody points at is a rollback target rather than a mess.
    #
    # They are recorded anyway, and separately, because the register compares bytes and
    # is right to. A digest that moved for a reason nobody wrote down is exactly what the
    # tripwire is for, and "it cannot matter for this function" is the sentence somebody
    # says just before it does. The difference from the validator entry is only in what
    # the window costs: nothing, in these two cases, which is why neither was worth a
    # deploy of its own.
    #
    # THE NOTIFIER'S ENTRY WAS EXTENDED THE SAME DAY RATHER THAN JOINED BY A TWENTIETH, WHICH
    # IS `one_record_per_function` WORKING AS THE VALIDATOR'S SEVEN-TIME EXTENSION ABOVE
    # DESCRIBES. A zip carries whatever the tree holds when it is built, so there is one
    # difference between the account and this tree however many changes went into it, and two
    # records for one zip would each describe a state that never exists.
    #
    # WHAT THE SECOND CHANGE IS, AND IT IS THE FIRST IN THIS REGISTER TO ADD A FILE TO A
    # BUILDER'S CONFIG LIST RATHER THAN EDIT ONE ALREADY IN IT. The approval request names the
    # machine somebody is being asked to pay for and now names the memory on it, which puts
    # config/accelerators.yaml in NOTIFIER_CONFIG and edullm_platform/accelerators.py in the
    # import closure. Unlike policy v6 above, this one does change what the account sends: the
    # deployed zip goes on posting the same five lines without the clause, which is the
    # previous message rather than a broken one, so the window still costs nothing anybody
    # meets as a failure.
    #
    # WHAT IT COMMITS THE NOTIFIER TO, WHICH IS LESS THAN THE OTHER FIVE FILES DO. Every file
    # in NOTIFIER_CONFIG is one whose edit becomes a release, and the five already there are
    # files people edit -- a policy bump, a roster change, a nightly reading of the account.
    # The policy v6 entry above is that cost arriving twice in a week. This one transcribes
    # `describe-instance-types`, and its own header records that the figures are not expected
    # to move, because what memory an H100 carries is a fact about silicon. What would move it
    # is a new instance family being priced, which is a new row and a release for the shape it
    # prices anyway.
    #
    # A TWENTY-FIRST, AND IT OPENS A NEW VALIDATOR RECORD RATHER THAN EXTENDING ONE, WHICH IS
    # THE ONE THING THIS ENTRY HAD TO BE REWRITTEN FOR. It was written against a tree where
    # the policy v6 validator record was still open, and it read as a second cause folded
    # into that record. That release was cut before this merged, so there is no record left
    # to extend: infra/admission-validator-release.yaml now names 237cc46703bc, the tree
    # built exactly that, and the window that entry described is closed. Re-applying the old
    # side wholesale would have resurrected a window somebody has already shut and quoted a
    # `released` digest the account no longer carries, so what follows is a new record
    # measured against the newly deployed bytes.
    #
    # WHAT REOPENS IT. maximum_attempts on open-instruct-scored-rewards-train drops from 2
    # to 1 -- grpo_fast.py:477 gates its checkpoint load on os.path.exists against the s3://
    # URI this platform hands it, so a second attempt restarted from step 0 at full price.
    # config/workload-catalog.yaml is in ADMISSION_CONFIG and in NOTIFIER_CONFIG and in
    # neither of the other two builders, so it reaches exactly the validator and the
    # notifier. The recorder's entry below is untouched by it and its digest is the same
    # either way, and the notifier's is extended a second time rather than joined by another
    # record, for the reason the paragraph above already gives.
    #
    # WHAT THE WINDOW COSTS, AND IT IS BEHAVIOUR RATHER THAN BYTES, WHICH ONLY THE CEILING'S
    # ENTRY HAS BEEN BEFORE. That one was urgent because a lead's release was refused inside
    # it. This one is not urgent and it is not nothing either: the deployed copy prices and
    # provisions that profile for two attempts and the notifier quotes them, which is money
    # a submission could spend rather than a submission that cannot run. It costs nothing
    # today because config/run-history.json records no run of open-instruct-scored-rewards
    # under either of its profiles.
    #
    # A TWENTY-SECOND, AND IT WIDENS BOTH OF THOSE RATHER THAN THE PAIR IT WAS DRAFTED OVER.
    # This change was written against a tree whose validator and notifier records were the
    # attempt_id pair, and it widened those. Every record it was written over has since been
    # cleared by a release, so re-applying that side wholesale would have resurrected four
    # windows other people have already shut, each quoting a `released` digest the account no
    # longer carries. What is here instead is main's list with this change's own cause added
    # to the two records that are open on it.
    #
    # WHAT THE CAUSE IS. config/workload-catalog.yaml gains the edullm-p1-train profile, and
    # ADMISSION_CONFIG and NOTIFIER_CONFIG both package that file, so the validator's and the
    # notifier's zips move and the recorder's and the janitor's do not. Both of those records
    # were opened by the changes above and are extended here rather than joined by new ones,
    # because `one_record_per_function` refuses two records for one zip and a digest has no
    # per-cause decomposition.
    #
    # NEITHER FUNCTION READS A WORKLOAD PROFILE BY NAME, SO THIS CAUSE IS BYTES AND NO
    # BEHAVIOUR, WHICH IS NOT TRUE OF THE ONE ABOVE IT. RunManifest.workload_profile is a
    # plain string and nothing looks it up: a submission is compiled on a runner from main's
    # catalog and carries its own runtime bound, attempt count and checkpoint contract, and
    # admission re-derives the class from those fields and from the compute profile's rate.
    # So a run naming edullm-p1-train is admitted correctly by the deployed validator today.
    # The catalog entries that would not survive this window are compute profiles, which
    # admission does look up, and none moved.
    # ALL THREE WERE CLEARED ON 2026-08-06 BY ONE `--function all`, AND THE JANITOR WAS
    # SKIPPED BY THE TOOL RATHER THAN BY ANYBODY DECIDING TO SKIP IT. The validator was
    # uploaded as object version Ss.xSF15xYJdzaXxrXaG0gNfT0lNOGUv, the recorder as
    # VW_zBa_zGntjWyICmJPsFdUwdf_HStdZ and the notifier as 3lqptMfT1E224SaFA3gKYYWES5Dr7.iI,
    # all cut from 307c18b, and each function's template and release record name the digest
    # its build produced.
    #
    # THE RECORDER'S ENTRY WAS THE ONE WORTH RE-DERIVING RATHER THAN INHERITING, AND THE
    # RE-DERIVATION MOVED THE ARGUMENT WITHOUT MOVING THE CONCLUSION. It said the recorder
    # packages contracts/admission.py and contracts/policy.py. It packages only the second:
    # the zip carries sixteen modules and `ApprovalEnvironment.satisfies`, which is the half
    # of policy v6 that could have mattered to anything reading a decision back, is not among
    # them. What is left is one optional field defaulting to None on a model this function
    # never loads from configuration, in the one zip of the four that carries no
    # configuration at all. So the window was as harmless as the entry claimed, for a
    # narrower reason than the entry gave.
    #
    # IT WAS RELEASED ANYWAY, AND THE COSTS ARE WHY RATHER THAN TIDINESS. Building the zip at
    # each of the last twenty-four commits of main shows this digest moving exactly at
    # 92c8516 and not once in the ten commits since, so the difference was fully described
    # and small -- which is the moment to close one, not a reason to leave it. Against that,
    # deferring is not free: a standing entry turns the one tripwire covering this function
    # into a skip until it lapses, and `explains` compares both digests, so whoever next
    # touches any of those sixteen modules inherits this same derivation under whatever time
    # pressure they are under. And the marginal deploy was zero this morning. The notifier's
    # release edits infra/notifications.yaml, which fires deploy-phase3-batch.yml, and that
    # workflow applies infra/batch-events.yaml in the same run whether or not this digest
    # moved. Deferring would have saved one put-object and spent the failure this function's
    # release record names as its own: lineage written by code nobody can point at, which
    # reads exactly like lineage written by the right code and has no later reader.
    #
    # AN EIGHTH ENTRY, AND IT IS THE FIRST ONE OPENED BY A CHANGE THAT EXISTS TO STOP THIS
    # FUNCTION BEING PAGED ABOUT. `edullm stop` gives a researcher a way to end their own lane
    # machine, which is a thing they can now do inside the five-minute window between the
    # sweep's describe-instances and its stop-instances. Landing in that window, the stop
    # answers IncorrectInstanceState or InvalidInstanceID.NotFound, and until this change that
    # counted as a refusal and failed the invocation on purpose -- correctly, for a machine
    # that is expired, warned and unstoppable, and exactly wrongly for one that is already
    # terminated. So the handler now tolerates those two codes on the stop, records the
    # outcome as `already_gone` rather than crediting itself with the reclaim, and goes on
    # failing for everything else.
    #
    # THE ACCOUNT IS THE STRICT SIDE FOR AS LONG AS THIS STANDS, WHICH IS THE ROUND THE
    # PRECEDING ENTRY ARGUES FOR. The deployed sweep will fail an invocation when a researcher
    # ends a machine near its expiry, which is a page about a machine that is not billing --
    # noisy and never wrong in the direction that costs money. The tree is the permissive side
    # and it is the side under review, so nobody meets this until the release, and what they
    # meet before it is a false alarm rather than a missed reclaim.
    #
    # A NINTH, AND IT IS THE FIRST ENTRY IN THIS REGISTER WHOSE WINDOW IS A LIVE HAZARD RATHER
    # THAN A WRONG LABEL, A REFUSAL OR BYTES WITH NO BEHAVIOUR BEHIND THEM. The notifier
    # interpolates the experiment into the run-ended message and escapes nothing, and Slack
    # reads `<`, `>` and `&` as control characters, so a run whose experiment is named
    # `<!channel>` notifies every member of the workspace each time it ends and a fan-out
    # notifies them once per cell. `messages.escaped` closes it per field, on the way in, and
    # `tests/test_notification_escaping.py` holds both halves of that.
    #
    # THE HAZARD STANDS UNTIL THE RELEASE AND THE DEPLOY, AND THAT IS WORTH SAYING PLAINLY
    # RATHER THAN LEAVING TO BE INFERRED FROM THE SHAPE OF THE RECORD. Every entry above
    # describes an account that is behind on something harmless or on something strict. This
    # one describes an account that goes on being able to ring every phone in the workspace for
    # as long as it stands, so it is the one entry here that is worth releasing on its own
    # rather than folding into whatever `--function all` next sweeps up. The run-ended message
    # is the one being delivered today; the approval message has no caller yet, so the half of
    # this change that touches it is not reachable in the account either way.
    #
    # WHAT MOVED THE ZIP IS ONE MODULE AND NOTHING ELSE, CHECKED RATHER THAN ASSUMED. A build
    # of origin/main produces d78c4a48 exactly, which is what infra/notifier-release.yaml
    # records, so no earlier change is riding along and this difference is entirely
    # notifications/messages.py. No file under config/ moved, so the other three zips are
    # untouched and the janitor's entry below is unaffected.
    #
    # AND IT IS EXTENDED RATHER THAN JOINED, WHICH IS THE FOURTEENTH ENTRY'S FINDING ARRIVING
    # FOR THE THIRD TIME IN TWO DAYS. `edullm studio` adds config/reports/studio.yaml, adding a
    # reviewed configuration file means adding a ConfigFile member because
    # tests/test_config_resolution.py holds the vocabulary and the contents of config/ level in
    # both directions, researcher_lane.py imports that enum, and this zip carries
    # researcher_lane.py for two tag keys and a role name. So a new line in a StrEnum moves a
    # Lambda digest again, and the coupling the fourteenth entry named as the finding is still
    # the finding: the vocabulary reaches three zips through a module none of them reads it in.
    #
    # NOTHING THE JANITOR DOES CHANGES BY A BYTE OF IT. janitor_handler.py imports
    # WARNING_TAG_KEY and LaneSettings and builds its settings in `_settings_from_environment`,
    # so it never opens a configuration file and never reaches the enum member that moved. The
    # sweep is unaware Studio exists, which is also the honest statement of what this verb does
    # not get: no janitor arm, no ExpiresAt, and `--stop` as the only thing that stops an app.
    #
    # AND THEN A THIRD CAUSE ON THE SAME RECORD, FROM THE SAME COUPLING, IN THE SAME DAY.
    # `edullm data` adds config/reports/corpora.json, which is a reviewed configuration file,
    # which is a ConfigFile member, which researcher_lane.py imports and this zip carries. So
    # the digest moved a third time for a report the sweep has no reader for. Three arrivals of
    # one finding is no longer an incident recurring: the vocabulary reaches three zips through
    # a module none of them reads it in, and every new report under config/ will go on moving
    # them until that import is broken. That is the thing to fix, and it is not this merge.
    #
    # ONE RECORD AND NOT THREE, WHICH IS THE ONLY SHAPE `one_record_per_function` PERMITS AND
    # ALSO THE TRUE ONE. A zip is built from the working tree rather than from a change, so a
    # digest has no per-cause decomposition and whoever runs the line below ships all three
    # whether they meant to or not. The first cause is the one with behaviour in it and is
    # still the reason to cut the release; the other two are bytes.
    #
    # THE DIGEST HERE IS THE MERGED TREE'S AND MATCHES NEITHER BRANCH'S. #408 recorded
    # d9cb4a6f for studio alone and #409 recorded 318e4531 for data alone. Both were right
    # about the tree they were built on and both are wrong about this one, which is the
    # ordinary arithmetic of integrating two changes that move one artifact and the reason
    # this was rebuilt rather than chosen between.
    releases: tuple[PendingRelease, ...] = (
        PendingRelease(
            function="notifier",
            reason=(
                "the run-ended message interpolated the submitter's experiment into Slack "
                "without escaping it, so a run named <!channel> notified the whole workspace "
                "every time it ended. messages.escaped now converts the three characters "
                "Slack parses, per field and before the line is assembled so the link the "
                "approval message builds survives."
            ),
            cleared_by=f"uv run python {RELEASE_COMMAND} --function notifier",
            builds_to="15058807c08d7bddefbfa7413ee737a3ecd910de0955f4e6879cf0b2ddf75d8d",
            released="d78c4a48482558039e7affc51331ec558e5880f8e48876bafb567fe683ee67b9",
            recorded_on=date(2026, 8, 6),
        ),
        PendingRelease(
            function="janitor",
            reason=(
                "edullm stop lets a researcher end their own machine inside the window "
                "between this sweep's describe and its stop, and the handler now reads the "
                "two EC2 codes that mean the machine is already off the clock as an outcome "
                "rather than as a refusal that fails the invocation. The digest then moved "
                "twice more for causes with no behaviour behind them at all: edullm studio "
                "and edullm data each add a reviewed configuration file and therefore a "
                "ConfigFile member, which researcher_lane.py imports and this zip carries, so "
                "a line in a StrEnum moved a Lambda for two reports the sweep never reads. "
                "The fourteenth entry in this register recorded that coupling as the finding "
                "rather than a one-off, and this is its third and fourth arrival. Its fifth "
                "is config/image-contents.yaml, which is what edullm data now reads a "
                "corpus's runnability out of instead of guessing at it from this platform's "
                "own tokenizer map -- a reviewed file, therefore a ConfigFile member, "
                "therefore these bytes, and the sweep does not read that one either."
            ),
            cleared_by=f"uv run python {RELEASE_COMMAND} --function janitor",
            builds_to="ed8bb614cf35013780a3eac043885bd173b7197ff92d9fc71224c1a7501d5fb7",
            released="e07efe963ec9cadb79f7345a14d9074c125e359a588e0661f99db687a757e96a",
            recorded_on=date(2026, 8, 7),
        ),
    )
    return one_record_per_function(releases)


def one_record_per_function(
    releases: Sequence[PendingRelease],
) -> tuple[PendingRelease, ...]:
    """The records, or a refusal if two of them describe the same zip.

    Its own function rather than four lines inside :func:`pending_releases`, so that the
    refusal can be exercised: the register above holds a literal, which is the right shape
    for a thing edited by hand in a reviewed diff and the wrong shape for a test to inject
    into.
    """
    functions = [release.function for release in releases]
    if len(set(functions)) != len(functions):
        raise PendingReleaseError(
            f"one function may carry one pending release; got {functions}. Two records for "
            "the same zip cannot both describe it, and whichever is read first would decide "
            "which difference counts as expected while the other sat describing nothing"
        )
    return tuple(releases)


PENDING_RELEASES: Final = pending_releases()


def pending_release_for(function: str) -> PendingRelease | None:
    """The release recorded for this function, or ``None`` if none is."""
    return next(
        (release for release in PENDING_RELEASES if release.function == function), None
    )


def releases_beyond_their_window(today: date | None = None) -> tuple[PendingRelease, ...]:
    """Every recorded pending release that has stood longer than it may.

    Separate from :func:`compare_release` and cheap on purpose. The comparison needs a zip,
    which costs a `uv pip install` and is therefore marked slow and skipped by `-m "not
    slow"`; this needs nothing but the clock. A forgotten release has to become visible on
    the run everybody makes, not on the one they opt into.
    """
    return tuple(release for release in PENDING_RELEASES if release.expired(today))


class ReleaseVerdict(StrEnum):
    """Whether the zip this tree builds is the zip the account is running."""

    #: The digests agree. Nothing is outstanding and nothing is recorded.
    MATCHES = "release_matches"
    #: They disagree, and a record says which difference this is, why, and what ends it.
    PENDING_RELEASE = "release_pending"
    #: They disagree and nothing here explains it -- or a record does not fit, or has stood
    #: too long. One member for all three, because the caller's move is the same in each:
    #: fail, loudly, with the detail saying which happened.
    SKEWED = "release_skewed"


@dataclass(frozen=True)
class ReleaseComparison:
    """One function's deployed bytes against this tree's, and what that state is called."""

    function: str
    built: str
    released: str
    verdict: ReleaseVerdict
    #: A sentence naming what a reader should do, whichever of the three states this is.
    detail: str

    @property
    def holds(self) -> bool:
        return self.verdict is ReleaseVerdict.MATCHES

    @property
    def waiting(self) -> bool:
        """Whether standing down is the recorded, explained, time-limited thing to do."""
        return self.verdict is ReleaseVerdict.PENDING_RELEASE


def compare_release(
    function: str,
    *,
    built: str,
    released: str,
    today: date | None = None,
) -> ReleaseComparison:
    """Compare a built digest against a recorded one, consulting the pending register.

    THE ONLY THING THAT MAY TURN A MISMATCH INTO A SKIP IS A RECORD THAT FITS IT EXACTLY,
    AND THE FIT IS CHECKED IN BOTH DIRECTIONS. That is what does the work
    :func:`~edullm_platform.phase1_capture.only_a_pending_deploy_stands_in_the_way` does for
    captures, where the caller asks a whole-tree question and one unexplained capture has to
    stop it standing down. The question here is per-function -- one zip, one record, one
    pair of digests -- and every way a second problem could arrive is already a change to
    one of those two digests:

    * something else packaged is edited while the record is open, and ``built`` moves;
    * somebody else cuts a release, and ``released`` moves;
    * the release this record was waiting for is cut, and the two become equal.

    Each of those makes the record stop explaining, and each is reported here as
    :attr:`ReleaseVerdict.SKEWED` rather than absorbed. So there is no set of other things
    to check for -- which is a property of the digests being a total description of the
    comparison, not a reason to check less.
    """
    releasable = RELEASABLE_FUNCTIONS.get(function)
    if releasable is None:
        raise PendingReleaseError(
            f"{function!r} is not a releasable function; "
            f"{sorted(RELEASABLE_FUNCTIONS)} are"
        )
    recorded = pending_release_for(function)

    def answer(verdict: ReleaseVerdict, detail: str) -> ReleaseComparison:
        return ReleaseComparison(
            function=function, built=built, released=released, verdict=verdict, detail=detail
        )

    if built == released:
        if recorded is None:
            return answer(
                ReleaseVerdict.MATCHES,
                f"The {releasable.display} zip this tree builds is the zip "
                f"{releasable.release_record} records as deployed.",
            )
        return answer(
            ReleaseVerdict.SKEWED,
            f"The release the pending record for the {function} was waiting on has been "
            f"cut: {releasable.release_record} now records {released}, which is what this "
            "tree builds. Delete the entry from "
            "edullm_platform.pending_amendments.pending_releases(). It is failing rather "
            "than lapsing quietly because a record left behind would absorb the next "
            "unexplained difference that happens to arrive on the same pair of digests.",
        )

    if recorded is None:
        return answer(
            ReleaseVerdict.SKEWED,
            "Nothing in edullm_platform.pending_amendments.pending_releases() records a "
            f"pending release of the {releasable.display}, so this difference is not one "
            "anybody has explained. If it is expected -- the change is on its way to main "
            "and deploy-phase2-admission.yml will not run from a branch -- record a "
            f"PendingRelease with function={function!r}, builds_to={built!r}, "
            f"released={released!r} and today's date. That turns this into a labelled skip "
            f"for {RELEASE_WINDOW.days} days and no longer, and it does not touch "
            f"{releasable.release_record}, which goes on describing what is actually "
            "deployed.",
        )

    if not recorded.explains(built=built, released=released):
        return answer(
            ReleaseVerdict.SKEWED,
            f"A pending release of the {releasable.display} is recorded, and it is not this "
            f"one: it is waiting for {recorded.builds_to} to replace {recorded.released}, "
            f"and this tree builds {built} against a recorded {released}. Something moved "
            "after the record was written -- another packaged file, or a release somebody "
            "else cut -- so the record no longer describes the difference and is not "
            "standing in for it. Rebuild, and either update the entry or remove it.",
        )

    if recorded.expired(today):
        return answer(
            ReleaseVerdict.SKEWED,
            f"The pending release of the {releasable.display} was recorded "
            f"{recorded.recorded_on.isoformat()} and stopped explaining anything after "
            f"{recorded.expires_on.isoformat()}, which is {RELEASE_WINDOW.days} days later. "
            "The difference it describes is still there, so the release was never cut: run "
            f"`{recorded.cleared_by.strip()}` from main. If it genuinely still cannot be "
            "cut, that is a fact worth a fresh record and a fresh reason rather than a "
            "window that renews itself.",
        )

    return answer(ReleaseVerdict.PENDING_RELEASE, recorded.describe())
