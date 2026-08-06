"""The roster held against the account, and the ways that comparison could pass by accident.

**THIS IS A CHECK ABOUT PEOPLE, SO THE WAY IT FAILS QUIETLY IS DIFFERENT FROM THE OTHERS.**
The account checks in ``audit.yml`` compare digests and templates: a broken one is usually a
loud one, because the thing it reads is not there. This one compares two lists of names, and
every way it can break produces a shorter list rather than an error -- an account read that
came back empty, a subtraction against the wrong side, a name comparison that matches nothing.
A short list here reads as "the roster is up to date", which is the answer somebody acts on by
doing nothing.

So the cases below are mostly about the ways it could report clean. The two that matter most
are :func:`test_an_account_that_answered_with_nothing_is_not_a_tidy_account`, which is the
guard on the read, and :func:`test_a_role_named_after_a_roster_member_suggests_that_member`,
which is the positive case for the suggestion column that the live account does not contain --
none of the 23 unnamed roles on 2026-08-05 matched a roster display name, so a suggestion
machine that had silently stopped working would look exactly like the truth.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS = PROJECT_ROOT / "tools"


def load_tool(name: str) -> Any:
    """Import a module out of ``tools/``, reusing the one already imported.

    The reason is the one ``tests/test_audit_workflow.py`` gives at length: a second module
    object for the same file is silent until something patches one copy and calls the other.
    """
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    specification = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool() -> Any:
    return load_tool("report_roster_against_the_account")


@pytest.fixture(scope="module")
def inventory() -> Any:
    from edullm_platform.config import load_yaml
    from edullm_platform.contracts.inventory import OrganizationInventory

    return load_yaml(PROJECT_ROOT / "config" / "organization.yaml", OrganizationInventory)


def rosters(tool: Any, **overrides: Any) -> Any:
    """A small inventory to compare against, so a case is not written against 35 real people."""
    from edullm_platform.contracts.inventory import OrganizationInventory

    payload: dict[str, Any] = {
        "admins": ["philote-dev"],
        "team_leads": ["philote-dev"],
        "pilot_repositories": ["OLMo-core"],
        "members": [
            {"github_login": "philote-dev", "display_name": "Frank Gonzalez"},
            {"github_login": "gorpyshortlegs", "display_name": "Arhant Choudhary"},
            {"github_login": "nobody-in-aws", "display_name": "Nobody In Aws"},
        ],
        "team_bindings": {
            "teams": [
                {
                    "team_id": "platform",
                    "github_team_slug": "platform",
                    "lead_logins": ["philote-dev"],
                    "member_logins": [],
                    "s3_namespace": "sbsandbox-intern-edullm-outputs/teams/platform",
                    "wandb_entity": "eduLLM",
                }
            ]
        },
        "aws_identities": {
            "roles": [
                {
                    "role_name": "Intern-frank.gonzalez-sbsandbox",
                    "github_login": "philote-dev",
                },
                {
                    "role_name": "Intern-arhant.choudhary-sbsandbox",
                    "github_login": "gorpyshortlegs",
                },
            ],
            "excluded_roles": [
                {"role_name": "Intern-p3math-smoke-download-20260731", "reason": "a task, not a person"}
            ],
        },
    }
    payload.update(overrides)
    return OrganizationInventory.model_validate(payload)


# ---------------------------------------------------------------------------------------
# The exact half: two set differences, and the four ways each could be taken wrongly
# ---------------------------------------------------------------------------------------


def test_a_role_the_roster_neither_binds_nor_excludes_is_reported(tool: Any) -> None:
    """Mutation: subtract only the bound roles and forget the account read entirely.

    This is the finding. A role here is somebody who can reach AWS today and would be refused
    by `edullm submit`, and until this report existed the only way to learn that was to list
    the account by hand and read 43 names against 35.
    """
    account = [
        tool.AccountRole("Intern-frank.gonzalez-sbsandbox", created_at="2026-07-21T15:39:35Z"),
        tool.AccountRole("Intern-ryan.deelstra-sbsandbox", created_at="2026-07-28T21:45:11Z"),
    ]

    unnamed = tool.roles_the_roster_does_not_name(account, rosters(tool))

    assert [entry.role.role_name for entry in unnamed] == ["Intern-ryan.deelstra-sbsandbox"]
    assert unnamed[0].role.created_at == "2026-07-28T21:45:11Z"


def test_an_excluded_role_is_not_reported_as_one_the_roster_does_not_name(tool: Any) -> None:
    """Mutation: subtract the bindings and not the exclusions.

    `excluded_roles` is where a role that launches compute and belongs to nobody is written
    down with the reason beside it. Reporting one every morning is how a list somebody reads
    becomes a list somebody skims, and the two `Intern-p3math-*` task roles would be on it
    every day forever.
    """
    account = [tool.AccountRole("Intern-p3math-smoke-download-20260731")]

    assert tool.roles_the_roster_does_not_name(account, rosters(tool)) == ()


def test_a_role_that_does_not_carry_the_prefix_is_not_this_report_s_business(tool: Any) -> None:
    """Mutation: report every role in the account.

    This is a shared sandbox with several hundred roles in it belonging to about a dozen
    unrelated projects. A report naming all of them is one nobody reads, and the prefix is
    what the self-serve broker actually puts on the roles this platform's admission cares
    about.
    """
    account = [tool.AccountRole("mcat-dev-analytics-writer")]

    assert tool.roles_the_roster_does_not_name(account, rosters(tool)) == ()


def test_a_task_role_carrying_the_prefix_is_still_reported_when_nothing_excludes_it(
    tool: Any,
) -> None:
    """Mutation: filter out anything whose name has no `<first>.<last>` in it.

    That would be the tidy answer and it is the wrong one. `Intern-p3math-olmo370-eval-*` is
    a job rather than a person, and the way it stops being reported is somebody writing it
    into `excluded_roles` with a reason -- not this tool deciding by shape. A prefix used as a
    filter swallows the next role somebody names unexpectedly, and it disappears with nothing
    saying it had stopped being looked at.
    """
    account = [tool.AccountRole("Intern-p3math-olmo370-eval-20260731")]

    unnamed = tool.roles_the_roster_does_not_name(account, rosters(tool))

    assert [entry.role.role_name for entry in unnamed] == ["Intern-p3math-olmo370-eval-20260731"]
    assert unnamed[0].reads_as_a_name is None


def test_a_roster_member_with_no_bound_role_is_reported_and_one_with_a_role_is_not(
    tool: Any,
) -> None:
    """Mutation: compare the roster against the account's role names instead of the bindings.

    That is the naive join and it is wrong in both directions, which is how this gap was
    miscounted before. A role name is an email address and a roster key is a GitHub login;
    `gorpyshortlegs` is Arhant Choudhary and no comparison of the two strings says so. The
    binding table is the only thing that knows, so it is the only thing joined on.
    """
    unheld = tool.members_holding_no_role(rosters(tool))

    assert unheld == ("nobody-in-aws",)


def test_the_member_side_is_case_insensitive_about_a_login(tool: Any) -> None:
    """Mutation: compare the logins as written.

    GitHub logins are case-insensitive and this file has held `Adarsh-Rajesh-gitHub` since it
    was written. A case-sensitive subtraction reports somebody as holding no role while their
    role sits in the table two hundred lines above, which is a finding that sends a reader to
    AWS to look for something that is already recorded.
    """
    inventory = rosters(
        tool,
        aws_identities={
            "roles": [
                {"role_name": "Intern-frank.gonzalez-sbsandbox", "github_login": "PHILOTE-DEV"}
            ],
            "excluded_roles": [],
        },
    )

    assert "philote-dev" not in tool.members_holding_no_role(inventory)


# ---------------------------------------------------------------------------------------
# The suggestion half, which decides nothing and must still work
# ---------------------------------------------------------------------------------------


def test_a_role_named_after_a_roster_member_suggests_that_member(tool: Any) -> None:
    """**THE POSITIVE CASE THE LIVE ACCOUNT DOES NOT CONTAIN.** Mutation: return () always.

    Not one of the 23 unnamed roles on 2026-08-05 matches a roster display name, so a
    suggestion machine that returned nothing would produce exactly today's output and nobody
    would ever find out. The column is a hint rather than a finding, which is the reason it
    can rot unnoticed and therefore the reason it needs a case of its own.
    """
    account = [tool.AccountRole("Intern-arhant.choudhary-sbsandbox")]
    inventory = rosters(
        tool, aws_identities={"roles": [], "excluded_roles": []}
    )

    unnamed = tool.roles_the_roster_does_not_name(account, inventory)

    assert unnamed[0].reads_as_a_name == "Arhant Choudhary"
    assert unnamed[0].suggested_logins == ("gorpyshortlegs",)


def test_a_near_match_suggests_nobody_and_the_role_is_still_reported(tool: Any) -> None:
    """Mutation: match on a surname, or on any word in common.

    `Intern-langming.xing-sbsandbox` is the roster's Meric Xing -- owner-confirmed on
    2026-08-04, when the same person's W&B account was resolved -- and a surname match would
    find it. It would also bind `Intern-tiffany.lam` to `Intern-kim.lam`'s person, and
    `Intern-avaneesh.mantrala` to Avaneesh Ramesh, both of whom are real and different people
    in this account. A misattributed role is reported as somebody else and looks correct; an
    unattributed one is reported by name every morning. Only one of those is recoverable.
    """
    account = [tool.AccountRole("Intern-langming.xing-sbsandbox")]
    inventory = rosters(
        tool,
        members=[
            {"github_login": "philote-dev", "display_name": "Frank Gonzalez"},
            {"github_login": "meric233", "display_name": "Meric Xing"},
        ],
        admins=["philote-dev"],
        team_leads=["philote-dev"],
        aws_identities={"roles": [], "excluded_roles": []},
    )

    unnamed = tool.roles_the_roster_does_not_name(account, inventory)

    assert unnamed[0].reads_as_a_name == "Langming Xing"
    assert unnamed[0].suggested_logins == ()


@pytest.mark.parametrize(
    ("role_name", "expected"),
    [
        ("Intern-skye.flowers-sbsandbox", "Skye Flowers"),
        ("Intern-saadhya.vijayvargiya-sbsandbox", "Saadhya Vijayvargiya"),
        ("Intern-p3math-olmo370-eval-20260731", None),
        ("Intern-p3math-smoke-download-20260731", None),
        ("sbsandbox-intern-edullm-run-preview", None),
        ("Intern-one.two.three-sbsandbox", None),
    ],
)
def test_what_a_role_name_reads_as(tool: Any, role_name: str, expected: str | None) -> None:
    """Mutation: split on the first dot and keep going.

    The two task roles are the cases that matter. `Intern-p3math-olmo370-eval-20260731` has
    dots nowhere and hyphens everywhere, and a lenient parser turns it into a person's name
    that a display-name comparison then hunts for.
    """
    assert tool.person_the_role_is_named_after(role_name) == expected


def test_two_members_of_the_same_name_are_both_suggested_rather_than_one_chosen(
    tool: Any,
) -> None:
    """Mutation: return the first match.

    Picking one is the fuzzy match pretending to be a lookup, and the reader has no way to
    see that a second candidate existed. Two of them printed side by side is a question; one
    of them printed alone is an answer, and this column is not entitled to give answers.
    """
    inventory = rosters(
        tool,
        admins=["philote-dev"],
        team_leads=["philote-dev"],
        members=[
            {"github_login": "philote-dev", "display_name": "Frank Gonzalez"},
            {"github_login": "grace-one", "display_name": "Grace Yan"},
            {"github_login": "grace-two", "display_name": "grace yan"},
        ],
        aws_identities={"roles": [], "excluded_roles": []},
    )

    assert tool.roster_members_named(inventory, "Grace Yan") == ("grace-one", "grace-two")


# ---------------------------------------------------------------------------------------
# The exit codes, which are the whole of how this job behaves on the schedule
# ---------------------------------------------------------------------------------------


def test_a_disagreement_is_reported_and_is_not_a_failure(
    tool: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """**THE PROPERTY THE WHOLE JOB RESTS ON.** Mutation: return 1 when the lists are not empty.

    A person appearing in the account before the roster is the ordinary order of events:
    self-serving takes five minutes and a roster change is a reviewed pull request. A red
    cross for that is a red cross most mornings, and the cost is not this job's alone -- it is
    the five account checks beside it on the same schedule, whose red crosses stop being read.
    """
    monkeypatch.setattr(
        tool,
        "read_account_roles",
        lambda **_: (tool.AccountRole("Intern-ryan.deelstra-sbsandbox", created_at="2026-07-28"),),
    )

    code = tool.main([])
    printed = capsys.readouterr().out

    assert code == 0
    assert "Intern-ryan.deelstra-sbsandbox" in printed
    assert "roles the roster does not name" in printed


def test_an_account_that_answered_with_nothing_is_not_a_tidy_account(
    tool: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """**THE GUARD THAT STOPS THIS BEING A CHECK THAT CANNOT FAIL.** Mutation: report it clean.

    An empty account read produces an empty unnamed list, and an empty unnamed list prints as
    a roster that is up to date. The two are indistinguishable in the output and the wrong one
    is the one somebody acts on, by doing nothing. This account has never held zero `Intern-*`
    roles -- every person with AWS access in it has one -- so zero is a read that did not work.
    """
    monkeypatch.setattr(tool, "read_account_roles", lambda **_: ())

    code = tool.main([])
    captured = capsys.readouterr()

    assert code == 2
    assert tool.ACCOUNT_HOLDS_NO_INTERN_ROLES in captured.err
    assert "roles the roster does not name" not in captured.out


def test_a_refused_read_says_so_and_quotes_the_grant_that_would_fix_it(
    tool: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: let the CaptureFailedError escape as a traceback.

    A denial here is almost always `iam:ListRoles`, which this role did not hold until the
    check was written and which is applied by hand from a laptop. A traceback sends the reader
    to the tool; the grant sends them to the one file and the one action that fixes it.
    """
    from edullm_platform.capture_tooling import CaptureFailedError

    def refuse(**_: Any) -> None:
        raise CaptureFailedError("aws_call_failed:iam:list-roles")

    monkeypatch.setattr(tool, "read_account_roles", refuse)

    code = tool.main([])
    captured = capsys.readouterr()

    assert code == 2
    assert "roster_comparison_unmade" in captured.err
    assert "iam:ListRoles" in captured.err
    assert "audit-reader-role.yaml" in captured.err


def test_the_grant_this_prints_is_the_one_the_role_actually_declares(tool: Any) -> None:
    """Mutation: reword the statement in one of the two files.

    The tool prints this into the 05:00 report as the thing to paste, so a second spelling
    would mean whoever pastes it changes the role into something no test covers. The same
    argument `tools/visibility_board.py` makes about its own two quoted grants.
    """
    declared = (PROJECT_ROOT / "infra" / "iam" / "audit-reader-role.yaml").read_text(
        encoding="utf-8"
    )
    quoted = [line.strip() for line in tool.MISSING_LIST_ROLES_GRANT.splitlines()]

    for line in quoted:
        assert line in declared, line


def test_there_is_no_exit_code_one_anywhere_in_this_tool(tool: Any) -> None:
    """Mutation: add `return 1` on the finding, to make the audit job go red on it.

    This is the mechanism behind the paragraph in `audit.yml`'s header, and it is here rather
    than as `continue-on-error` on the job because that would also swallow exit 2, which is a
    real failure. Read off the source, because a case that drove every input would only ever
    cover the inputs somebody thought of.
    """
    source = (TOOLS / "report_roster_against_the_account.py").read_text(encoding="utf-8")

    assert "return 1" not in source
    assert "return 2" in source, "the unreadable-source path has been removed"


# ---------------------------------------------------------------------------------------
# The committed file, which is what the comparison is actually made against
# ---------------------------------------------------------------------------------------


def test_the_committed_roster_binds_roles_so_the_comparison_is_not_vacuous(
    tool: Any, inventory: Any
) -> None:
    """Guards every case above against passing on an empty table.

    Mutation: empty `aws_identities.roles`. Every role in the account would be reported as
    unnamed and every member as holding none, which is loud -- but the reverse mutation is
    not: a table that had swallowed the whole account would report nothing at all. Both
    directions are asserted, so neither can be the way this quietly stops saying anything.
    """
    bound = inventory.aws_identities.role_logins()

    assert len(bound) > 1, "nothing is bound, so every role would report as unknown"
    assert tool.members_holding_no_role(inventory), (
        "every member holds a role, which has not been true of this account and would make "
        "the second half of the report unable to say anything"
    )
