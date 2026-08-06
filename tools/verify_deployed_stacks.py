"""Hold each deployed CloudFormation stack against the template in ``main`` that declares it.

Every check in this repository compares the repository against itself. The tests read the
templates, the gates read committed records, and the two release tripwires hold a record
against a zip built from the tree. None of that says anything about the account, and the
stacks under ``infra/iam/`` are applied by hand from laptops, so nothing produces a run log
at all. The account can stop matching what ``main`` says and the first symptom is a run
failing for a reason nobody can explain.

**It has already happened once, and the mechanism will recur.** On 2026-08-01 an
``s3:DeleteObject`` grant was added to the GPU workload role and
``sbsandbox-intern-edullm-phase4-gpu-iam`` was applied at 17:11. At 17:53 somebody else
applied the same stack from a branch cut before the grant existed and the grant was gone.
Nothing warned anybody, because a change set shows what the template being applied says and
not what it omits relative to what is live. That template holds three roles that different
changes touch, so two people working on different roles overwrite each other silently.

**Which template a stack was deployed from is declared here rather than derived from the
stack name.** A convention fits most of these and not all: ``…-phase1-ecr`` is
``ecr-repositories.yaml``, ``…-phase2-admission-iam`` is ``iam/admission-role.yaml``, and
``…-phase2-admission-service-roles`` follows neither rule. Something that fits seventeen of
twenty-one skips four by resolving to a path that is not there, which is a silent hole
exactly where a name is unusual. The cost of declaring the table is that it can fall behind
the account, and that cost is paid rather than accepted: the account is listed on every run
and a stack the table does not claim fails the run. **A stack this cannot account for is
reported, never skipped.** That is the single property the rest of this depends on, because
the stack the table will not contain is the one somebody deploys next.

**The comparison is between parsed structures, and comparing text was measured rather than
assumed to be wrong.** CloudFormation does not hand back the bytes it was given: read live on
2026-08-01, every non-ASCII character comes back as a question mark, so the section sign in a
comment in ``infra/outputs-bucket.yaml`` alone would make a text comparison red every night
for four of the twenty-one stacks. Key order, indentation and quoting are all free to move as
well. A check that cries wolf gets ignored, which is the same as not having one.

**A stack is compared only from a status that means a template is applied, and every other
status is a finding.** Three of the twenty-three CloudFormation stack statuses mean the
template took: ``CREATE_COMPLETE``, ``UPDATE_COMPLETE`` and ``IMPORT_COMPLETE``. This was an
exclusion list of two until 2026-08-05, when a stack whose create had failed and rolled back
read as a clean pass here while it was breaking a deploy workflow -- ``get-template`` returns
the template a failed create attempted, so the comparison agreed with itself about resources
the account did not hold. See :data:`STATUSES_WITH_A_TEMPLATE_APPLIED`.

**What makes a stack reportable is that it is ours and that it exists. Its status decides
which finding, never whether there is one.** Ours is :data:`STACK_NAME_PREFIX`, which is
where the seventy-odd stacks belonging to sixteen other teams stop being this check's
business. Exists is every status but ``DELETE_COMPLETE``, which is a name ``list-stacks``
goes on returning forever with no stack behind it. Those are the only two lines, and neither
is drawn on whether a stack looks worth reporting.

**The second line used to be drawn somewhere else and a stack fell through every path at
once.** ``REVIEW_IN_PROGRESS`` was dropped from the listing beside ``DELETE_COMPLETE``, on
the argument that a stack the table claims falls out as declared-and-not-deployed on the
next line. That argument is sound and it holds only for stacks the table claims.
``sbsandbox-intern-edullm-ecr-repositories`` was not one of those: dropped from the listing
it could not be reported as unaccounted for, and absent from :data:`STACKS` it could not be
reported as declared and not deployed. So a misspelling of ``…-phase1-ecr``, left behind by
a deploy on 2026-08-01 whose change set failed and was never executed, produced no finding
on any path for five days -- not a wrong answer, an absence. See
:data:`STATUSES_WITHOUT_A_STACK`.

**The two non-zero exits mean different things and a caller must not merge them.** Exit 1
says the account and ``main`` disagree and sends a reader to a stack. Exit 2 says this check
did not manage to look and sends them to a credential or a grant. Reporting the second as the
first sends somebody hunting a deploy on the morning an IAM stack lapsed; reporting it as a
pass silently stops the check covering anything.

**Nothing this prints carries an account id.** A CloudFormation denial names the calling
role's ARN and the stack ARN, and both carry the number, so only the error code is repeated.
Template content is masked on the way out as well, because the deployed side is whatever was
applied rather than something this repository can promise about.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

from edullm_platform.evidence import ACCOUNT_ID_IN_FREE_TEXT, AWS_ACCOUNT_ID_PLACEHOLDER

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INFRA_ROOT = PROJECT_ROOT / "infra"
IAM_ROOT = INFRA_ROOT / "iam"

__all__ = [
    "DEFAULT_REGION",
    "DIFFERENCES_REPORTED",
    "EXIT_DISAGREES",
    "EXIT_OK",
    "EXIT_UNUSABLE",
    "STACKS",
    "STACK_NAME_PREFIX",
    "STATUSES_WITHOUT_A_STACK",
    "STATUSES_WITH_A_TEMPLATE_APPLIED",
    "DeployedStackFinding",
    "Difference",
    "Stack",
    "build_parser",
    "check",
    "compare",
    "list_deployed_stacks",
    "main",
    "read_committed_template",
    "read_deployed_template",
]

EXIT_OK: Final = 0

#: The region every stack in :data:`STACKS` is deployed in, named once so that a second
#: reader cannot disagree with this one about where to look. ``tools/scoreboard.py`` defaulted
#: to ``us-east-2`` while this defaulted to ``us-east-1``, and a board run with a valid session
#: and no ``--region`` therefore listed an empty region and reported fourteen live stacks as
#: undeployed. It read 30 of 55 where the account holds 43 of 55, and nothing about the output
#: said which region had been read.
DEFAULT_REGION: Final = "us-east-1"

#: The account and ``main`` disagree. A definite answer about the account, which is why a
#: declared stack that is not deployed and a deployed stack nothing declares are both here:
#: the reader's next move is the same for all three, and it is to go and look at a stack.
EXIT_DISAGREES: Final = 1

#: Nothing was read, so nothing is claimed. Never reported as a pass, because a check that
#: cannot look is not a check that found nothing.
EXIT_UNUSABLE: Final = 2

#: How many differences one stack reports before the rest are counted rather than printed.
#: A stack deployed from an entirely different template would otherwise fill the log with a
#: diff nobody reads, and the first few name the resource, which is what a reader needs to
#: decide what happened.
DIFFERENCES_REPORTED: Final = 12

#: How much of one value a difference prints. Long enough to carry an ARN or a policy
#: statement's Sid, which is usually the part that identifies it.
VALUE_WIDTH: Final = 200

#: What every stack this repository deploys is called. The account is a shared sandbox
#: holding about seventy stacks belonging to sixteen other teams, so this prefix is the
#: boundary of what this check claims; without it, every run would report forty-nine stacks
#: as unaccounted for and the report would stop being read. It is the one assumption here
#: that the account cannot contradict on its own: a stack of ours deployed under some other
#: name is not reported as unaccounted for, it is not seen.
#: ``tests/test_deployed_stacks.py`` holds every workflow-deployed name to this prefix, and
#: ``infra/README.md`` names every laptop-deployed one.
STACK_NAME_PREFIX: Final = "sbsandbox-intern-edullm-"

#: The one status under which ``list-stacks`` returns a name and the account holds no stack.
#: A deleted stack is returned forever, so carrying these into the listing would give the
#: report a permanent tail of lines naming stacks that are not there, growing by one every
#: time anybody deletes anything, with nothing for a reader to do about any of them. A report
#: with a standing tail is a report people learn to skim, and skimming is how the thing at
#: the top gets missed.
#:
#: **The test for membership here is "is there a stack", not "is this worth reporting".**
#: That distinction is the whole of this constant. Anything else under
#: :data:`STACK_NAME_PREFIX` is a stack that exists: it holds a name nobody else can use, it
#: may hold resources, and something put it there. So it goes into the listing and earns a
#: finding -- from :data:`STATUSES_WITH_A_TEMPLATE_APPLIED` if its status is not one of the
#: three, from the unaccounted-for pass if :data:`STACKS` does not claim it, and from both if
#: both, which is two true things about one stack rather than a duplicate.
#:
#: **This was a set of two, and the second was ``REVIEW_IN_PROGRESS``.** The module docstring
#: has the stack that cost and why the argument for dropping it did not cover the case that
#: arrived. The general form of that mistake is dropping a status because every stack in it
#: *that anybody had thought of* would be caught elsewhere, and the way to not make it again
#: is to have one reason for dropping a status and have that reason be that there is no stack.
STATUSES_WITHOUT_A_STACK: Final = frozenset({"DELETE_COMPLETE"})

#: Every status under which CloudFormation holds a template applied in full and the resources
#: it declares exist. These three and no others, enumerated from the ``StackStatus`` valid
#: values on the ``Stack`` and ``StackSummary`` types in the CloudFormation API reference,
#: which lists twenty-three.
#:
#: **This was an exclusion list and it hid a live outage.** On 2026-08-05 CI created
#: ``sbsandbox-intern-edullm-notifications`` at 23:42 UTC, ``lambda:CreateFunction`` was
#: refused for want of ``iam:PassRole``, and the stack rolled back six seconds later holding
#: nothing. ``ROLLBACK_COMPLETE`` was outside the two-status exclusion above, so the stack was
#: read as deployed, ``cloudformation:GetTemplate`` returned the template it had tried and
#: rolled back, the comparison found no difference, and this check called it a pass. A stack
#: that failed to exist was indistinguishable from one deployed cleanly, while every dispatch
#: of ``deploy-phase3-batch.yml`` died on it.
#:
#: **An allow-list, and the argument that used to be against one is answered rather than
#: overruled.** That argument was that AWS adds statuses and an allow-list would drop a stack
#: into a new one silently. It would, if an unrecognised status were dropped. It is not:
#: anything outside these three and the two above is its own finding, named with the status
#: it is in. So a status invented after this was written is loud on the first run that meets
#: it, which is the opposite of what the exclusion list did.
#:
#: The excluded middle is deliberate and each part of it earns a finding. Every
#: ``_IN_PROGRESS`` status means a deploy is mid-flight and the account is not yet what any
#: template says. Every ``_FAILED`` status means an operation stopped partway. Every rollback
#: status means an operation was undone, and that includes ``UPDATE_ROLLBACK_COMPLETE`` and
#: ``IMPORT_ROLLBACK_COMPLETE``, which are stable and are still not healthy: the stack holds
#: whatever it held before the operation nobody managed to complete, which is by definition
#: not the template that was just applied to it.
STATUSES_WITH_A_TEMPLATE_APPLIED: Final = frozenset(
    {"CREATE_COMPLETE", "UPDATE_COMPLETE", "IMPORT_COMPLETE"}
)

#: How the CLI opens every service error. The code inside the brackets is a fixed token and
#: it is the only part of the message repeated anywhere, because the rest of the line is an
#: ARN or two and every ARN carries the account id.
ERROR_CODE = re.compile(r"An error occurred \(([A-Za-z]+)\)")

#: What CloudFormation says about a stack name nothing is deployed under. It arrives as a
#: ``ValidationError``, which is also what a malformed request gets, so the phrase is matched
#: as well as the code; the phrase is not printed, only used to choose which finding this is.
NOT_DEPLOYED_CODE = "ValidationError"
NOT_DEPLOYED_PHRASE = "does not exist"


@dataclass(frozen=True)
class Stack:
    """One deployed stack and the file in ``main`` that says what it should be."""

    name: str
    template: Path


def _stacks(*entries: tuple[str, Path]) -> dict[str, Stack]:
    return {name: Stack(name=name, template=template) for name, template in entries}


#: Every stack this repository deploys, in the order the phases created them.
#:
#: A template appears once even where two phases amended it, because a stack is deployed from
#: one file whatever the history of that file is. Adding a stack means adding a row here and
#: adding its ARN to the audit reader's ``cloudformation:GetTemplate`` grant in
#: ``infra/iam/audit-reader-role.yaml``; a row without the grant is a denial rather than a
#: silence, and a grant without a row is caught by the listing.
STACKS: Final = _stacks(
    ("sbsandbox-intern-edullm-phase1-ecr", INFRA_ROOT / "ecr-repositories.yaml"),
    ("sbsandbox-intern-edullm-ecr-publisher-iam", IAM_ROOT / "ecr-publisher-role.yaml"),
    ("sbsandbox-intern-edullm-infra-deployer-iam", IAM_ROOT / "infra-deployer-role.yaml"),
    (
        "sbsandbox-intern-edullm-phase2-admission-service-roles",
        IAM_ROOT / "admission-service-roles.yaml",
    ),
    ("sbsandbox-intern-edullm-phase2-admission-iam", IAM_ROOT / "admission-role.yaml"),
    ("sbsandbox-intern-edullm-phase2-lineage", INFRA_ROOT / "lineage-bucket.yaml"),
    ("sbsandbox-intern-edullm-phase2-artifacts", INFRA_ROOT / "artifacts-bucket.yaml"),
    ("sbsandbox-intern-edullm-phase2-admission", INFRA_ROOT / "admission-state-machine.yaml"),
    ("sbsandbox-intern-edullm-phase3-batch-iam", IAM_ROOT / "batch-roles.yaml"),
    ("sbsandbox-intern-edullm-phase3-lifecycle-iam", IAM_ROOT / "lifecycle-lambda-role.yaml"),
    ("sbsandbox-intern-edullm-phase3-outputs", INFRA_ROOT / "outputs-bucket.yaml"),
    ("sbsandbox-intern-edullm-phase3-network", INFRA_ROOT / "batch-network.yaml"),
    ("sbsandbox-intern-edullm-phase3-batch", INFRA_ROOT / "batch-compute.yaml"),
    ("sbsandbox-intern-edullm-phase3-events", INFRA_ROOT / "batch-events.yaml"),
    ("sbsandbox-intern-edullm-phase4-gpu-iam", IAM_ROOT / "batch-gpu-roles.yaml"),
    ("sbsandbox-intern-edullm-phase4-gpu", INFRA_ROOT / "batch-compute-gpu.yaml"),
    ("sbsandbox-intern-edullm-phase4-gpu-shapes", INFRA_ROOT / "batch-compute-gpu-shapes.yaml"),
    ("sbsandbox-intern-edullm-dataset-validator-iam", IAM_ROOT / "dataset-validator-role.yaml"),
    (
        "sbsandbox-intern-edullm-researcher-iam",
        IAM_ROOT / "researcher-role.yaml",
    ),
    ("sbsandbox-intern-edullm-janitor-iam", IAM_ROOT / "janitor-lambda-role.yaml"),
    ("sbsandbox-intern-edullm-janitor", INFRA_ROOT / "expiry-janitor.yaml"),
    ("sbsandbox-intern-edullm-run-canceller-iam", IAM_ROOT / "run-canceller-role.yaml"),
    ("sbsandbox-intern-edullm-audit-reader-iam", IAM_ROOT / "audit-reader-role.yaml"),
    ("sbsandbox-intern-edullm-phase5-image-resolver-iam", IAM_ROOT / "image-resolver-role.yaml"),
    ("sbsandbox-intern-edullm-run-preview-iam", IAM_ROOT / "run-preview-role.yaml"),
    ("sbsandbox-intern-edullm-notifier-iam", IAM_ROOT / "notifier-lambda-role.yaml"),
    ("sbsandbox-intern-edullm-notifications", INFRA_ROOT / "notifications.yaml"),
    ("sbsandbox-intern-edullm-scratch", INFRA_ROOT / "scratch-bucket.yaml"),
    ("sbsandbox-intern-edullm-lane-instance-iam", IAM_ROOT / "lane-instance-role.yaml"),
)


class DeployedStackFinding(Exception):
    """One stack is not what ``main`` says, or could not be established to be.

    Carries a machine-readable reason first and a sentence naming what to do, the way the
    sibling verifiers do. ``code`` travels with the reason rather than being decided by the
    caller, so a failure mode added later has to choose which of the two non-zero exits it
    is instead of inheriting whichever the caller assumed.
    """

    def __init__(self, reason: str, detail: str, *, code: int) -> None:
        self.reason = reason
        self.detail = detail
        self.code = code
        super().__init__(f"{reason}: {detail}")


@dataclass(frozen=True)
class Difference:
    """One place the account and ``main`` disagree, named by where it is.

    ``path`` is dotted from the root of the template, so it opens with the section and then
    the logical id -- which is the resource, and the resource is what a reader at 05:00 needs
    before anything else.
    """

    path: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}: {self.detail}"


def _masked(text: str) -> str:
    """Mask any account id, leaving content digests alone.

    ``edullm_platform.evidence.redact_aws_account_ids`` is the sanctioned mask and is not
    used here, because it raises on text that also carries another credential shape. That is
    right for a capture somebody is about to commit and wrong for an audit check: a drift
    report that turned into a traceback would report nothing at all, on the one morning the
    account was holding something nobody expected. The same expression is reused so the mask
    cannot be stepped around differently here than it is anywhere else.
    """
    return ACCOUNT_ID_IN_FREE_TEXT.sub(
        lambda found: AWS_ACCOUNT_ID_PLACEHOLDER if found.group("account") else found.group(0),
        text,
    )


def _render(value: object) -> str:
    """One value, short enough to read on a line and long enough to identify it."""
    try:
        rendered = json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):  # pragma: no cover - json falls back to str() above
        rendered = str(value)
    rendered = _masked(rendered)
    if len(rendered) > VALUE_WIDTH:
        return rendered[:VALUE_WIDTH] + "…"
    return rendered


def _child(path: str, key: object) -> str:
    return f"{path}.{key}" if path else str(key)


def _canonical(value: object) -> str:
    """A list element as one comparable string, for aligning two lists.

    Only ever compared with another one of these, so it does not have to be readable and
    does not go through the mask.
    """
    return json.dumps(value, sort_keys=True, default=str)


def compare(account: object, main: object, *, path: str = "") -> list[Difference]:
    """Every place the deployed template and the committed one differ.

    Lists are aligned rather than compared by index. Removing the third of five policy
    statements shifts every statement after it, so a positional comparison reports three
    differences for one edit and two of them describe statements nobody touched -- and the
    reader has to work out which is real, at 05:00, which is more reading than the message
    was supposed to save them. Aligning first means one removed statement reports as one
    removed statement.
    """
    if isinstance(account, dict) and isinstance(main, dict):
        found: list[Difference] = []
        for key in sorted(set(account) | set(main), key=str):
            if key not in main:
                found.append(
                    Difference(_child(path, key), f"only in the account ({_render(account[key])})")
                )
            elif key not in account:
                found.append(
                    Difference(_child(path, key), f"only in main ({_render(main[key])})")
                )
            else:
                found.extend(compare(account[key], main[key], path=_child(path, key)))
        return found

    if isinstance(account, list) and isinstance(main, list):
        return _compare_lists(account, main, path=path)

    if account != main:
        return [
            Difference(path, f"the account has {_render(account)} and main has {_render(main)}")
        ]
    return []


def _compare_lists(account: list[Any], main: list[Any], *, path: str) -> list[Difference]:
    matcher = difflib.SequenceMatcher(
        a=[_canonical(item) for item in account],
        b=[_canonical(item) for item in main],
        autojunk=False,
    )
    found: list[Difference] = []
    for operation, start_a, end_a, start_b, end_b in matcher.get_opcodes():
        if operation == "equal":
            continue
        # An element changed in place keeps its position on both sides, so recursing into it
        # names the property that moved rather than reprinting the whole element twice.
        if operation == "replace" and end_a - start_a == end_b - start_b:
            for offset in range(end_a - start_a):
                found.extend(
                    compare(
                        account[start_a + offset],
                        main[start_b + offset],
                        path=f"{path}[{start_a + offset}]",
                    )
                )
            continue
        # The index is the one on the side the element is actually on, which is the side the
        # reader will be looking at.
        for index in range(start_a, end_a):
            found.append(
                Difference(f"{path}[{index}]", f"only in the account ({_render(account[index])})")
            )
        for index in range(start_b, end_b):
            found.append(Difference(f"{path}[{index}]", f"only in main ({_render(main[index])})"))
    return found


def read_committed_template(path: Path) -> dict[str, Any]:
    """The template ``main`` carries, parsed.

    Read before AWS is called, for the reason the sibling Lambda verifier reads its record
    first: there is nothing to compare an answer against otherwise, so the call would be a
    credential spent on a question the run cannot answer, and a broken checkout then fails
    the same way with no network at all.
    """
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise DeployedStackFinding(
            "committed_template_unusable",
            f"{_relative(path)} could not be read ({error.__class__.__name__}), so there is "
            "nothing to hold the deployed stack against. Every template this check names is "
            "committed, so a missing one means this was run from somewhere other than a "
            "checkout of this repository.",
            code=EXIT_UNUSABLE,
        ) from error
    except yaml.YAMLError as error:
        raise DeployedStackFinding(
            "committed_template_unusable",
            f"{_relative(path)} did not parse as YAML, so what the account should hold is "
            "unknown. Comparing against nothing would report every resource in the stack as "
            "one the account added.",
            code=EXIT_UNUSABLE,
        ) from error

    if not isinstance(loaded, dict):
        raise DeployedStackFinding(
            "committed_template_unusable",
            f"{_relative(path)} parsed to {type(loaded).__name__} rather than a mapping, so "
            "it is not a CloudFormation template and there is nothing to compare.",
            code=EXIT_UNUSABLE,
        )
    return loaded


def read_deployed_template(
    stack_name: str, *, profile: str | None, region: str
) -> dict[str, Any]:
    """What CloudFormation says is deployed under this name, parsed.

    The ``Original`` stage rather than ``Processed``. Processed resolves transforms and
    macros, so it is a derived artifact, and holding a derived artifact against a source file
    would report a difference every time AWS changed how it derives one. Nothing here uses a
    transform today, which is exactly why the wrong stage would go unnoticed until something
    did.

    The CLI rather than boto3, for the reason ``tools/verify_wandb_credential.py`` gives:
    this project does not depend on an AWS SDK, and the two Lambda zips are size-limited
    enough that adding one would be paid for by both functions.
    """
    call = [
        "aws",
        "cloudformation",
        "get-template",
        "--stack-name",
        stack_name,
        "--template-stage",
        "Original",
        "--region",
        region,
        *(["--profile", profile] if profile else []),
        "--output",
        "json",
    ]
    try:
        finished = subprocess.run(call, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DeployedStackFinding(
            "deployed_stack_unreadable",
            f"asking CloudFormation for the template of {stack_name} did not complete "
            f"({error.__class__.__name__}), so nothing was read and nothing is claimed about "
            "what is deployed.",
            code=EXIT_UNUSABLE,
        ) from error

    if finished.returncode != 0:
        raise _refusal(stack_name, finished.returncode, finished.stderr)
    return _template_body(stack_name, finished.stdout)


def _template_body(stack_name: str, payload: str) -> dict[str, Any]:
    """``TemplateBody`` out of the CLI's answer, as a mapping.

    CloudFormation answers with a string for a stack deployed from YAML and with an object
    for one deployed from JSON. Every template here is YAML, so the object branch is the one
    that would rot unnoticed until somebody converted one.
    """
    try:
        answer = json.loads(payload)
    except ValueError as error:
        raise DeployedStackFinding(
            "deployed_stack_unreadable",
            f"the answer CloudFormation gave for {stack_name} did not parse as JSON, so the "
            "deployed template has not been read. Comparing an unparsed answer against the "
            "committed file would report a difference this check has no basis for claiming.",
            code=EXIT_UNUSABLE,
        ) from error

    body = answer.get("TemplateBody") if isinstance(answer, dict) else None
    if isinstance(body, str):
        try:
            body = yaml.safe_load(body)
        except yaml.YAMLError as error:
            raise DeployedStackFinding(
                "deployed_stack_unreadable",
                f"the template CloudFormation returned for {stack_name} did not parse as "
                "YAML, so it has not been read. That is a statement about the answer rather "
                "than about the deployment.",
                code=EXIT_UNUSABLE,
            ) from error

    if not isinstance(body, dict):
        raise DeployedStackFinding(
            "deployed_stack_unreadable",
            f"CloudFormation returned no template body for {stack_name} that reads as a "
            "mapping, so what is deployed has not been read.",
            code=EXIT_UNUSABLE,
        )
    return body


def _refusal(stack_name: str, status: int, stderr: str) -> DeployedStackFinding:
    """Turn a CLI failure into a finding, repeating the error code and nothing else.

    The message the CLI prints continues into the caller's ARN and the stack ARN, and both
    carry the account id. This runs in a scheduled job whose log is public and whose every
    committed capture masks that number, so the code inside the brackets is the only part
    repeated. It is also the part that decides what to do.
    """
    found = ERROR_CODE.search(stderr)
    code = found.group(1) if found else None

    if code == NOT_DEPLOYED_CODE and NOT_DEPLOYED_PHRASE in stderr:
        return DeployedStackFinding(
            "declared_stack_is_not_deployed",
            f"CloudFormation has no stack called {stack_name}, so the template this "
            "repository declares for it is not applied anywhere. Either it never was, or "
            "somebody deleted a stack main still carries, or this was pointed at an account "
            "or a region other than the one it is deployed in -- which answers identically "
            "and is worth ruling out first.",
            code=EXIT_DISAGREES,
        )

    named = f"{code} " if code else ""
    return DeployedStackFinding(
        "deployed_stack_unreadable",
        f"reading the deployed template of {stack_name} was refused with {named}(the CLI "
        f"exited {status}), so what is deployed has not been read and this run says nothing "
        "about it either way. A denial here is usually the grant: the audit reader needs "
        "cloudformation:GetTemplate on this stack, which infra/iam/audit-reader-role.yaml "
        "declares by name and which is applied from a laptop like every IAM stack in "
        "infra/README.md. The full message is not printed because it names the calling and "
        "resource ARNs, and both carry the account id.",
        code=EXIT_UNUSABLE,
    )


def list_deployed_stacks(*, profile: str | None, region: str) -> dict[str, str]:
    """Every stack of ours the account holds, and the status it holds it in.

    This is what stops the check being confined to what it already knows. The table below is
    written by whoever last thought about this file, so the stack it will not contain is the
    one somebody deploys next; listing the account is how that stack gets reported instead of
    skipped.

    Filtered on two things and no others: the name is ours, and the status is not
    :data:`STATUSES_WITHOUT_A_STACK`. Anything filtered here is invisible to every finding
    downstream, because those all read this answer, so a status dropped here is a stack
    nothing reports rather than a stack reported differently. That is what happened to
    ``REVIEW_IN_PROGRESS`` and it is why the second condition is about existence.
    """
    call = [
        "aws",
        "cloudformation",
        "list-stacks",
        "--region",
        region,
        *(["--profile", profile] if profile else []),
        "--output",
        "json",
    ]
    try:
        finished = subprocess.run(call, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DeployedStackFinding(
            "deployed_stacks_not_listed",
            f"listing the account's stacks did not complete ({error.__class__.__name__}), so "
            "this run cannot say whether a stack exists that nothing in this repository "
            "accounts for.",
            code=EXIT_UNUSABLE,
        ) from error

    if finished.returncode != 0:
        found = ERROR_CODE.search(finished.stderr)
        named = f"{found.group(1)} " if found else ""
        raise DeployedStackFinding(
            "deployed_stacks_not_listed",
            f"listing the account's stacks was refused with {named}(the CLI exited "
            f"{finished.returncode}), so a stack nothing in this repository accounts for "
            "would not be seen on this run. The audit reader needs cloudformation:"
            "ListStacks, which infra/iam/audit-reader-role.yaml declares; the action takes "
            "no resource, and the template says why. The full message is not printed because "
            "it names the calling ARN, which carries the account id.",
            code=EXIT_UNUSABLE,
        )

    try:
        answer = json.loads(finished.stdout)
        summaries = answer["StackSummaries"]
    except (ValueError, KeyError, TypeError) as error:
        raise DeployedStackFinding(
            "deployed_stacks_not_listed",
            "the stack listing did not come back in the shape this reads, so the account has "
            "not been enumerated and a stack nothing accounts for would not be seen.",
            code=EXIT_UNUSABLE,
        ) from error

    # A SHORT READ IS REFUSED RATHER THAN RETURNED. The CLI follows the pages itself and
    # strips the token when it reaches the end, so a token surviving into the answer means
    # something capped the walk -- `--no-paginate`, `--max-items`, or a `max_items` in the
    # profile, none of which this call passes and any of which a caller's environment can.
    # Read live on 2026-08-06 the account holds 143 stack summaries over two pages, so this
    # is one page short of the ceiling rather than a hypothetical, and a truncated listing is
    # indistinguishable from a complete one at every point downstream: a stack that fell off
    # the second page reports as declared-and-not-deployed here and reads as an undeployed
    # row on the board. Neither says the page ran out.
    if answer.get("NextToken"):
        raise DeployedStackFinding(
            "deployed_stacks_not_listed",
            f"the stack listing stopped after {len(summaries)} summaries with more pages "
            "left, so the account has not been enumerated. Something capped the pagination: "
            "--no-paginate or --max-items on the call, or a max_items setting in the profile "
            "this ran under. A partial listing is not reported as a listing, because a stack "
            "on a page nobody read is indistinguishable from a stack that is not deployed.",
            code=EXIT_UNUSABLE,
        )

    return {
        summary["StackName"]: summary["StackStatus"]
        for summary in summaries
        if str(summary.get("StackName", "")).startswith(STACK_NAME_PREFIX)
        and summary.get("StackStatus") not in STATUSES_WITHOUT_A_STACK
    }


def check(stack: Stack, *, profile: str | None, region: str) -> None:
    """Raise unless the stack is deployed from the template ``main`` declares for it."""
    committed = read_committed_template(stack.template)
    deployed = read_deployed_template(stack.name, profile=profile, region=region)

    found = compare(deployed, committed)
    if not found:
        return

    shown = [f"  {difference}" for difference in found[:DIFFERENCES_REPORTED]]
    if len(found) > DIFFERENCES_REPORTED:
        shown.append(f"  and {len(found) - DIFFERENCES_REPORTED} further differences")
    raise DeployedStackFinding(
        "deployed_stack_is_not_main",
        f"{stack.name} is not deployed from {_relative(stack.template)} as main has it. "
        "Which way each difference runs decides the repair. Something this repository "
        "declares and the account does not have is the shape of the 2026-08-01 revert -- the "
        "stack was applied from a branch cut before it was added -- and the repair is to "
        "apply the template from main. Something the account has and nobody committed is the "
        "opposite: decide whether it should be adopted first, because applying over it "
        "reconciles it away with no stack error. infra/README.md carries an instance of "
        "each.\n" + "\n".join(shown),
        code=EXIT_DISAGREES,
    )


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _report(finding: DeployedStackFinding) -> None:
    # Written where it happens and flushed, so the order a reader sees is the order the
    # stacks were checked in. Collecting the findings and printing them at the end put them
    # ahead of a passing line written earlier, because stdout is block-buffered into a pipe
    # and stderr is not, and a log that reports the fourth stack before the first is a log
    # that gets misattributed.
    print(finding.reason, file=sys.stderr, flush=True)
    print(finding.detail, file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    # No default profile. The audit runs on an assumed role and passes none, and a default
    # of `sbsandbox` would send it looking for an SSO session that is not there.
    parser.add_argument("--profile", default=None)
    parser.add_argument("--region", default=DEFAULT_REGION)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    findings: list[DeployedStackFinding] = []

    # A refused listing costs the completeness half and not the comparison. The table still
    # says what twenty-one stacks should be, and those answers are still true and still worth
    # a morning; what the run must not do afterwards is exit zero, and the finding below is
    # what stops it.
    deployed: dict[str, str] | None
    try:
        deployed = list_deployed_stacks(profile=options.profile, region=options.region)
    except DeployedStackFinding as finding:
        findings.append(finding)
        _report(finding)
        deployed = None

    # BEFORE ANY COMPARISON, BECAUSE THE COMPARISON IS WHAT HID THE OUTAGE. A stack that is
    # not in one of the three healthy statuses still answers `get-template` -- with whatever
    # it last tried, which for a failed create is the template that never took. Holding that
    # against main finds no difference and reads as a pass. So an unhealthy stack is reported
    # here and excluded from the loop below rather than compared and believed.
    unhealthy: set[str] = set()
    for name, status in sorted((deployed or {}).items()):
        if status in STATUSES_WITH_A_TEMPLATE_APPLIED:
            continue
        unhealthy.add(name)
        unwell = DeployedStackFinding(
            "deployed_stack_is_not_healthy",
            f"{name} is in {status}, which is not one of the three statuses under which "
            "CloudFormation holds a template applied in full: CREATE_COMPLETE, "
            "UPDATE_COMPLETE, IMPORT_COMPLETE. What is deployed under this name has not been "
            "compared against main, because a stack in this state answers get-template with "
            "whatever it last tried and comparing that would report a pass for a stack that "
            "may hold none of those resources.\n"
            "  An _IN_PROGRESS status means a deploy is mid-flight and this run was simply "
            "early; re-run it.\n"
            "  ROLLBACK_COMPLETE means an initial create failed and the stack holds nothing. "
            "It cannot be updated, so every subsequent deploy of it fails on the state rather "
            "than on the template, and the repair is to delete the stack and deploy again "
            "once whatever refused the create has been fixed.\n"
            "  REVIEW_IN_PROGRESS means a change set was created against this name and never "
            "executed, so the name is taken and nothing is deployed under it. A deploy from "
            "here executes a fresh change set and creates the stack normally. If the name "
            "itself is the mistake -- a typo for a stack that already exists is how this one "
            "arrives -- then deleting it costs nothing, but confirm that with "
            "list-stack-resources before you do, because a name being plausible is not "
            "evidence about what is under it.\n"
            "  Any other status means an operation stopped partway or was undone, and the "
            "stack events say which resource and why.",
            code=EXIT_DISAGREES,
        )
        findings.append(unwell)
        _report(unwell)

    # Independent of the status pass above, and a stack can earn both. An unhealthy status
    # says the account is not what any template claims; an unclaimed name says this
    # repository cannot say which template it should be. They are two true things about one
    # stack with two different repairs, and collapsing them would mean choosing which of the
    # two a reader is told -- which is how a stack that is both ends up reported as neither.
    unaccounted = sorted(set(deployed) - set(STACKS)) if deployed is not None else []
    for name in unaccounted:
        unclaimed = DeployedStackFinding(
            "deployed_stack_is_unaccounted_for",
            f"the account holds a stack called {name} and nothing in this repository "
            "declares which template it was deployed from, so this check has no idea whether "
            "it matches main and is not going to pretend it does. Add it to STACKS in "
            "tools/verify_deployed_stacks.py beside the template it was applied from, add "
            "its ARN to the cloudformation:GetTemplate grant in "
            "infra/iam/audit-reader-role.yaml, and record the stack name in "
            "infra/README.md if it is applied by hand. If it should not exist, that is the "
            "finding.",
            code=EXIT_DISAGREES,
        )
        findings.append(unclaimed)
        _report(unclaimed)

    for name, stack in STACKS.items():
        if deployed is not None and name not in deployed:
            undeployed = DeployedStackFinding(
                "declared_stack_is_not_deployed",
                f"{name} is declared here as {_relative(stack.template)} and the account "
                "holds no stack of that name that anything is deployed under. Either the "
                "template was never applied, or the stack was deleted while main went on "
                "declaring it, and the two are worth telling apart before applying anything.",
                code=EXIT_DISAGREES,
            )
            findings.append(undeployed)
            _report(undeployed)
            continue
        if name in unhealthy:
            continue
        try:
            check(stack, profile=options.profile, region=options.region)
        except DeployedStackFinding as finding:
            findings.append(finding)
            _report(finding)
            continue
        print(f"{name} is deployed from {_relative(stack.template)} as main has it.", flush=True)

    if not findings:
        print("Every stack in the account is the template main declares for it.")
        return EXIT_OK

    # A definite finding outranks an unanswered question. Somebody with one stack to repair
    # has to repair it whatever happened to the others, and the others are printed above
    # rather than hidden behind the exit code.
    if any(reported.code == EXIT_DISAGREES for reported in findings):
        return EXIT_DISAGREES
    return EXIT_UNUSABLE


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
