"""The account's ``edullm-researcher`` compared against the template that declares it.

The other half of ``tests/test_researcher_role_template.py``, which says so in its own first
paragraph and pointed at this module by name before this module existed. That one reads a
committed template, which is a claim about what the account will be asked for; every
statement in it would stay green against a role that was never deployed. This one reads a
capture of the role the account holds.

**It could not be written until 2026-08-06, and the reason is worth keeping.** The role was
applied by hand that night, and the capture that would have fed this module raised
``PolicyNotComparableError`` on ``${aws:PrincipalTag/project}`` rather than producing a
record. ``normalize_policy_string`` refused every ``${...}`` that survived its folding, which
is correct for a CloudFormation substitution that failed to resolve and wrong for an IAM
policy variable -- a value CloudFormation passes through untouched and IAM resolves per
request. So the one role whose fence is built on a policy variable was the one role that
could not be captured, and nothing said so, because nothing ran this target.

The consequence is the part to keep: ``RESEARCHER_ROLE_TEMPLATES`` and
``RESEARCHER_ROLE_CAPTURE_DIR`` were declared and no test read either, so the registry looked
maintained and compared nothing. A capture target with no test is a target that runs the day
somebody runs it by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

from edullm_platform.phase1_capture import CaptureVerdict, read_committed_role_captures
from edullm_platform.role_drift import (
    RESEARCHER_ROLE_CAPTURE_DIR,
    RESEARCHER_ROLE_TEMPLATES,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESEARCHER_ROLE_NAME = "edullm-researcher"

#: The committed capture of the role as the account holds it, taken after the 2026-08-06
#: apply of ``sbsandbox-intern-edullm-researcher-iam``. The copy a later reader can check
#: without an AWS session.
#:
#: Taken with ``tools/capture_phase3_evidence.py --target researcher-role``, which walks
#: ``RESEARCHER_ROLE_TEMPLATES`` rather than another phase's roles -- one registry per unit of
#: work, so this role drifting cannot fail a capture of theirs and vice versa.
DEPLOYED_ROLE_CAPTURE = (
    PROJECT_ROOT / RESEARCHER_ROLE_CAPTURE_DIR / f"{RESEARCHER_ROLE_NAME}.sanitized.json"
)

#: The two IAM policy variables this role is built on, spelled as both the template and the
#: account spell them, because CloudFormation does not touch either.
#:
#: Written out rather than derived from the capture or the template. A set read off one side
#: of a comparison and asserted against that same side asserts nothing, and the whole point of
#: these two strings is that a normalisation once could not represent them.
SOURCE_IDENTITY_VARIABLE = "${aws:SourceIdentity}"
PROJECT_TAG_VARIABLE = "${aws:PrincipalTag/project}"


def captured_document() -> str:
    return DEPLOYED_ROLE_CAPTURE.read_text(encoding="utf-8")


def test_the_deployed_researcher_role_matches_the_template_that_declares_it() -> None:
    """Mutation: widen the deployed role in the console. Mutation: amend the template and
    do not apply it.

    The whole-record comparison, and the only check in this repository that reads this role
    from the account. It covers what a template test cannot see at all: the permissions
    boundary, the trust policy, any attached managed policy, the session duration, and every
    statement of the inline policy as IAM stored it rather than as YAML declared it.

    Reported in both directions by the reader, which is why the capture lives in a directory
    of its own: a capture present for a role the registry does not declare is a finding too,
    and a directory shared with another registry would make one registry's filing look like
    the other's drift.
    """
    captures = read_committed_role_captures(
        PROJECT_ROOT,
        capture_dir=PROJECT_ROOT / RESEARCHER_ROLE_CAPTURE_DIR,
        role_templates=RESEARCHER_ROLE_TEMPLATES,
    )

    assert len(captures) == len(RESEARCHER_ROLE_TEMPLATES)
    for capture in captures:
        assert capture.verdict is CaptureVerdict.OK, (capture.role_name, capture.detail)
        assert capture.report is not None
        assert capture.report.matches, capture.report.findings


def test_the_account_stored_the_policy_variables_literally() -> None:
    """Mutation: wrap either variable in Fn::Sub in the template.

    The premise the comparison rests on, asserted against the account rather than assumed.
    An IAM policy variable is only comparable because CloudFormation leaves it alone, so both
    sides hold the identical characters; if the deploy had resolved or mangled either of
    these, the test above would be comparing a template against a role that means something
    else and would still have to report drift to say so.

    Wrapping ``${aws:SourceIdentity}`` in an ``Fn::Sub`` is the specific mistake this guards,
    and the template says so in a comment beside the statement. CloudFormation would refuse
    the stack on an unresolved parameter named ``aws:SourceIdentity``, but a template author
    who reached instead for ``${!aws:SourceIdentity}`` to get past that would deploy a role
    whose fence names a literal that no session identity can ever equal, which denies every
    write the lane makes and reads, in the console, exactly like the correct policy.
    """
    document = captured_document()

    assert SOURCE_IDENTITY_VARIABLE in document
    assert PROJECT_TAG_VARIABLE in document


def test_the_working_tier_fence_is_the_statement_the_capture_actually_compared() -> None:
    """Mutation: drop the fence from the template and from the account together.

    A comparison of two documents that both lost a statement matches, so the test above
    cannot see a fence that is gone from both sides. This names it.

    It is the statement most worth naming. ``DenyWorkingTierWritesOutsideYourOwnPrefix`` is a
    ``Deny`` on a ``NotResource``, which reaches every resource except the ones listed, so its
    absence does not narrow the role by one grant -- it removes the only thing stopping a
    session writing anywhere in the working tier under somebody else's name.
    """
    record = json.loads(captured_document())
    policies = {policy["policy_name"]: policy for policy in record["inline_policies"]}
    sids = {
        statement.get("sid") for policy in policies.values() for statement in policy["statements"]
    }

    assert "DenyWorkingTierWritesOutsideYourOwnPrefix" in sids
    fence = next(
        statement
        for policy in policies.values()
        for statement in policy["statements"]
        if statement.get("sid") == "DenyWorkingTierWritesOutsideYourOwnPrefix"
    )
    assert fence["effect"] == "Deny"
    assert fence["resource_match"]["element"] == "NotResource"
    assert any(
        SOURCE_IDENTITY_VARIABLE in resource for resource in fence["resource_match"]["resources"]
    )
