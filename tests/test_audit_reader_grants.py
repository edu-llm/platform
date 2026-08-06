"""The four reads the instruments need, asserted against the template rather than the account.

**THE GRANTS ARE ALREADY LIVE AND THAT IS WHY THIS IS WORTH WRITING NOW RATHER THAN LATER.**
All four are on the deployed role, applied by hand across three separate changes, and until
this file existed nothing held the committed template to them. The mutation these kill is a
future edit: an argument that one of them is unused, a statement tidied away with a stack, a
second spelling of the tagging grant pasted from somewhere else. A role and the tool asking
for it drift apart one statement at a time.

A committed template is not a deployed role, and this repository already has a check for that
disagreement -- ``tools/verify_deployed_stacks.py``, run in the audit. What this asserts is the
narrower thing a test can assert: that the template declares each grant, that none is wider
than the action needs, and that the tagging statement is character-for-character the one
``tools/visibility_board.py`` quotes in its own report.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from read_launch_events import LAUNCH_EVENT_NAMES
from visibility_board import MISSING_TAG_GRANT

TEMPLATE = PROJECT_ROOT / "infra/iam/audit-reader-role.yaml"

#: The lineage bucket's name, as the template spells it inside an ``Fn::Sub``. Spelled here
#: rather than imported because what is being read is the template's text and not the
#: platform's idea of the bucket; the two agreeing is `tests/test_audit_workflow.py`'s job.
LINEAGE_BUCKET = "sbsandbox-intern-edullm-lineage"


def _statements() -> list[dict[str, object]]:
    # safe_load parses this template as it stands: every intrinsic in infra/iam/ is written
    # in the long `Fn::Sub:` form rather than as the `!Sub` tag, which is what lets a plain
    # YAML reader here and in tools/verify_deployed_stacks.py read the same bytes.
    document = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    role = document["Resources"]["AuditReaderRole"]["Properties"]
    return [
        statement
        for policy in role["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
    ]


def _actions() -> set[str]:
    """Every action the role holds, flattened.

    Written out rather than as a set comprehension over ``statement["Action"]``, because IAM
    allows a statement's Action to be a string or a list and this template uses both: a
    comprehension raises TypeError on the list-valued ones, which is a crash rather than a
    failing assertion, and a test that crashes is a test whose assertion was never reached.
    """
    found: set[str] = set()
    for statement in _statements():
        action = statement.get("Action")
        if isinstance(action, str):
            found.add(action)
        elif action is not None:
            found.update(action)  # type: ignore[arg-type]
    return found


def _sid(sid: str) -> dict[str, object]:
    found = [statement for statement in _statements() if statement.get("Sid") == sid]
    assert len(found) == 1, f"expected exactly one {sid} statement, found {len(found)}"
    return found[0]


#: Every Sid the template declares, as of 2026-08-05. Held by equality below rather than by
#: containment, which is the point of the list existing.
EXPECTED_SIDS = frozenset(
    {
        "ReadIntentRecords",
        "ReadResultRecords",
        "ReadAttemptRecords",
        "ReadBindingRecords",
        "ListLineageRecords",
        "ReadRunOutput",
        "ListRunOutput",
        "ReadTheWandbKeyTheCheckValidates",
        "ReadAdmissionValidatorDeployedCode",
        "ReadLifecycleRecorderDeployedCode",
        # These two arrived while this list was being written, from #248 and #253, and this
        # list is how each was noticed rather than absorbed. A count or a subset assertion
        # would have said nothing about either.
        #
        # #253 also shows the cost of equality and it is the cost worth paying: it and #251
        # were both green and neither was rebased onto the other, so main went red for the
        # minutes between them. A subset assertion would have stayed green and would also
        # have stayed green if every grant below were deleted.
        "ReadExpiryJanitorDeployedCode",
        "ReadNotifierDeployedCode",
        # The four from the 2026-08-06 notifier outage. Every grant above this line reads
        # what is deployed, and all of them answered correctly for the whole of the time the
        # notifier was raising on every invocation, because the bytes were right and the
        # handler read a directory the builder never wrote to. These read outcomes instead:
        # whether the functions succeed, whether the one function that is safe to invoke does
        # run, whether the alarms have anywhere to fire and whether that anywhere reaches a
        # person.
        "ReadWhetherTheDeployedFunctionsSucceed",
        "SmokeInvokeTheNotifierAndNothingElse",
        "ReadWhetherTheAlarmsHaveSomewhereToFire",
        "ReadWhetherTheAlarmTopicHasASubscriber",
        "ReadTheDeployedTemplateOfEachStack",
        "FindStacksNothingInTheRepositoryAccountsFor",
        "FindEveryResourceThisPlatformTagged",
        "LookUpLaunchEvents",
        "ReadTheQueuesThePlacementVerdictNeeds",
        # The first grant here that is about people rather than about runs, and the equality
        # is what put it in front of a reader. Nothing in the account or the repository could
        # say who holds AWS access that `config/organization.yaml` has never heard of, so a
        # person could self-serve a role and be refused by admission a week later with no
        # symptom in between. What it discloses is every role name in a shared account, which
        # is why it is argued at length beside the statement rather than only listed here.
        "ListTheInternRolesTheRosterIsComparedAgainst",
    }
)


def test_the_template_declares_exactly_these_statements() -> None:
    """The guard that stops every assertion below passing over an empty set.

    A template restructured so that this reader finds no statements would make each test
    here compare nothing against nothing. Four checks in this repository have failed that
    way, so the derived side is pinned before anything is read off it.

    EQUALITY RATHER THAN A COUNT OR A SUBSET, AND THAT IS THE WHOLE DESIGN OF THIS TEST. The
    version this replaces asserted `len(_statements()) >= 4`, which is the seventh instance of
    the shape #247 fixed: an assertion that gets *easier* to satisfy every time the thing it
    guards changes, and which eleven statements could be deleted without disturbing.

    Equality also makes this fail when a statement is **added**, which is intended. This role
    is the one identity a scheduled workflow assumes, so a grant arriving without a reader
    noticing is the failure worth catching, and the fix is one line here plus the paragraph
    the template already requires beside every statement.
    """
    found = {str(statement["Sid"]) for statement in _statements() if "Sid" in statement}
    assert found == EXPECTED_SIDS
    assert len(_statements()) == len(EXPECTED_SIDS), "a statement carries no Sid"


def test_the_role_may_look_up_launch_events() -> None:
    """Mutation: leave cloudtrail:LookupEvents out of the template.

    Without it `tools/read_launch_events.py` is refused at 05:00 and the morning message
    reports no mismatches because it examined no events, which is the shape of failure the
    denominator exists to make visible and would here be invisible for a different reason.
    """
    assert "cloudtrail:LookupEvents" in _actions()


def test_the_launch_event_read_is_confined_to_one_region() -> None:
    """Mutation: drop the region condition.

    LookupEvents takes no resource type, so `Resource: "*"` is forced rather than chosen and
    the region condition is the only narrowing the action admits. Everything this platform
    runs is in one region.
    """
    statement = _sid("LookUpLaunchEvents")
    assert statement["Action"] == "cloudtrail:LookupEvents"
    assert statement["Resource"] == "*"
    assert statement["Condition"] == {  # type: ignore[index]
        "StringEquals": {"aws:RequestedRegion": {"Fn::Sub": "${AWS::Region}"}}
    }


def test_the_launch_read_is_the_only_grant_the_event_names_need() -> None:
    """Mutation: reach for a second CloudTrail action while adding an event name.

    `GetEventSelectors`, `LookupEvents` and the data-event reads are three different grants,
    and only one of them answers the question this asks. A reader that called for another
    would be refused at 05:00 with an exit that says nothing about the account -- the failure
    the visibility board spent its first weeks in.
    """
    cloudtrail = {action for action in _actions() if action.startswith("cloudtrail:")}
    assert cloudtrail == {"cloudtrail:LookupEvents"}
    assert LAUNCH_EVENT_NAMES, "the reader asks for no event at all, so this asserts nothing"


def test_the_tagging_statement_is_the_one_the_board_asks_for() -> None:
    """Mutation: write a second spelling of the tagging grant.

    `tools/visibility_board.py` prints MISSING_TAG_GRANT into its report as the statement to
    paste. If the template's version differs, whoever pastes the report's version changes the
    role to something no test covers.
    """
    quoted = yaml.safe_load(MISSING_TAG_GRANT)[0]
    statement = _sid(quoted["Sid"])

    assert statement["Action"] == quoted["Action"]
    assert statement["Effect"] == quoted["Effect"]
    assert statement["Resource"] == quoted["Resource"]
    assert statement["Condition"] == quoted["Condition"]


def test_the_role_may_read_the_attempt_records() -> None:
    """Mutation: leave attempt/ out, on the argument that the reports degrade without it.

    They do degrade, and the degradation is total: every duration and every cost comes out of
    the attempt records, so without this the morning message has no largest run to name, the
    activity page has no figures, and the budget line loses its per-team split. Three of the
    slice's five parts are dark over one prefix.
    """
    reads = {
        statement["Resource"]["Fn::Sub"]  # type: ignore[index]
        for statement in _statements()
        if statement.get("Action") == "s3:GetObject"
        and isinstance(statement.get("Resource"), dict)
    }
    assert any(resource.endswith("/attempt/*") for resource in reads)


def test_the_listing_condition_admits_the_attempt_prefix() -> None:
    """Mutation: grant GetObject on attempt/ and leave the prefix off the listing condition.

    THIS IS THE HALF THAT IS EASY TO MISS, AND IT HAS BEEN MISSED HERE BEFORE. Listing is a
    bucket-level action that cannot be scoped by an object ARN, so the prefix condition is the
    whole narrowing -- and `aws s3 sync` lists before it fetches. A GetObject grant with no
    matching prefix on the listing reads as granted in a policy review and is refused at the
    first call, with no object fetched.
    """
    # By Sid rather than by action: this role lists two buckets, the lineage store and the
    # outputs store, and the outputs listing is not narrowed by these prefixes at all.
    prefixes = _sid("ListLineageRecords")["Condition"]["StringLike"]["s3:prefix"]  # type: ignore[index]
    assert "attempt/*" in prefixes


def test_every_prefix_the_role_can_fetch_it_can_also_list() -> None:
    """Mutation: add a prefix to one half of a grant and not the other.

    The two halves are written in different statements and nothing at runtime compares them,
    so a half-grant is the ordinary way this template goes wrong. It reads as granted and is
    refused at the first call. Asserted as an equality rather than as containment, because
    the excess direction is a read nobody asked for.
    """
    marker = f":s3:::{LINEAGE_BUCKET}/"
    fetchable = {
        resource["Fn::Sub"].split(marker, 1)[1].rsplit("/*", 1)[0]
        for statement in _statements()
        if statement.get("Action") == "s3:GetObject"
        and isinstance(resource := statement.get("Resource"), dict)
        and marker in resource["Fn::Sub"]
    }
    condition = _sid("ListLineageRecords")["Condition"]["StringLike"]["s3:prefix"]  # type: ignore[index]
    listable = {
        entry.rsplit("/*", 1)[0]
        for entry in (condition if isinstance(condition, list) else [condition])
    }

    assert fetchable, "no lineage GetObject statement parsed out of the role"
    assert listable, "no lineage prefix condition parsed out of the role"
    assert fetchable == listable
