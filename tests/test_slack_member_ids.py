"""What the Slack matcher must refuse, and the measurement its whole design rests on.

THE FAILURE THIS GUARDS AGAINST IS SILENT AND THE TESTS ARE SHAPED AROUND THAT. A Slack member
id is used to address somebody. An id matched to the wrong person produces a message that
arrives, renders correctly, and pings a stranger, and the only reader placed to notice is the
person who should have been asked and was not. There is no exception, no red test and no
retry. So every case below is either a refusal or a piece of evidence, and the fuzzy matching
that would make the unresolved list shorter is the thing being kept out.

``config/organization.yaml`` reached the same place twice for W&B accounts, in its own words:
an exact equality is accepted, a prefix is not, and a near-match waits for the owner to
confirm it. This is that rule for a second identity system.

Nothing here reaches Slack. ``match_roster`` is pure and takes the accounts as an argument
precisely so that these can be written.
"""

from __future__ import annotations

from typing import Any, Final

import pytest

from edullm_platform.config import load_yaml
from edullm_platform.contracts.inventory import OrganizationInventory
from tools.collect_slack_member_ids import (
    BY_AWS_ROLE,
    BY_DERIVED_EMAIL,
    BY_DISPLAY_NAME,
    WEAK_EVIDENCE,
    SlackAccount,
    aws_role_local_parts,
    derive_email_local_part,
    match_roster,
    render,
    slack_accounts,
)

ROSTER: Final = "config/organization.yaml"

pytestmark = pytest.mark.xdist_group("slack-member-ids")


def account(member_id: str, real_name: str, email: str | None = None) -> SlackAccount:
    return SlackAccount(member_id=member_id, real_name=real_name, email=email)


@pytest.fixture(scope="module")
def roster() -> OrganizationInventory:
    return load_yaml(ROSTER, OrganizationInventory)


# ---------------------------------------------------------------------------------------
# the measurement the design rests on
# ---------------------------------------------------------------------------------------


def test_the_derived_address_is_wrong_for_somebody_the_roster_can_check(
    roster: OrganizationInventory,
) -> None:
    """**THE WHOLE ARGUMENT FOR NOT MATCHING ON A DERIVED EMAIL, PINNED SO IT CANNOT ROT.**

    Corporate mail here is ``first.last@alphaaiengineering.com``, and the roster's
    ``Intern-<first>.<last>-sbsandbox`` role names come out of the same directory, so the
    derivation can be measured rather than assumed. It is wrong for ``ninLi0``, who is on the
    roster as ``Eric Ni`` and holds ``Intern-linjian.ni-sbsandbox``. Derivation would address
    ``eric.ni``, which is somebody else's address or nobody's.

    Mutation: drop the AWS role rule and match on the derived address alone. Every test below
    still passes and this one fails, which is the point. If this ever goes green because the
    roster changed, the design question is open again rather than settled, and the header of
    ``tools/collect_slack_member_ids.py`` is what has to be re-measured.
    """
    roles = aws_role_local_parts(roster)
    member = next(m for m in roster.members if m.github_login == "ninLi0")

    assert derive_email_local_part(member.display_name) == "eric.ni"
    assert "linjian.ni" in roles["ninLi0"]
    assert "eric.ni" not in roles["ninLi0"]


def test_one_person_holds_two_directory_spellings_and_both_are_kept(
    roster: OrganizationInventory,
) -> None:
    """Mutation: keep one role per person.

    ``meric233`` holds ``Intern-meric.xing-sbsandbox`` and ``Intern-langming.xing-sbsandbox``,
    and the roster records the owner confirming both are the same human. Keeping one is a
    coin toss on which address Slack happens to carry, and losing that toss puts the person in
    the unresolved list while the evidence to resolve them was in the file.
    """
    assert aws_role_local_parts(roster)["meric233"] == {"meric.xing", "langming.xing"}


def test_a_display_name_that_is_not_two_words_derives_nothing(
    roster: OrganizationInventory,
) -> None:
    """Mutation: take the first and last word and ignore the middle.

    ``Yuen Kai Chow`` would derive to ``yuen.chow``, which is a guess at which of three words
    is the surname. Refusing is what puts the person on the unresolved list with their real
    name beside them, which is answerable by asking them.
    """
    member = next(m for m in roster.members if m.github_login == "yuen-kai")

    assert member.display_name == "Yuen Kai Chow"
    assert derive_email_local_part(member.display_name) is None


# ---------------------------------------------------------------------------------------
# what counts as a match
# ---------------------------------------------------------------------------------------


def test_an_email_matching_an_aws_role_resolves_the_person_the_derivation_gets_wrong(
    roster: OrganizationInventory,
) -> None:
    """The rule earning its place. Same person as the measurement above, resolved correctly."""
    found = match_roster(
        roster, [account("U01", "Eric Ni", "linjian.ni@alphaaiengineering.com")]
    )

    matched = {match.github_login: match for match in found.matched}
    assert matched["ninLi0"].account.member_id == "U01"
    assert matched["ninLi0"].evidence == BY_AWS_ROLE


def test_an_exact_display_name_is_evidence_and_a_prefix_of_one_is_not(
    roster: OrganizationInventory,
) -> None:
    """**Mutation: match on ``startswith`` to catch the near-misses.**

    ``Sophia Z`` is the display name W&B carries for ``zsophiaaa``, whose roster name is
    ``Sophia Zhang``, and the roster comment above her entry records that the prefix was
    refused and the owner asked instead. A prefix rule also makes ``Eric Wu`` match anybody
    called Eric, and this workspace has two people called Eric.
    """
    found = match_roster(
        roster,
        [account("U01", "Sophia Zhang"), account("U02", "Sophia Z"), account("U03", "Eric")],
    )

    matched = {match.github_login: match for match in found.matched}
    assert matched["zsophiaaa"].account.member_id == "U01"
    assert matched["zsophiaaa"].evidence == BY_DISPLAY_NAME
    unresolved = {login for login, _ in found.unresolved}
    assert "ninLi0" in unresolved, "a bare `Eric` must not resolve either person called Eric"
    assert "ericrcwu001" in unresolved


def test_a_match_only_a_derived_address_supports_is_labelled_for_confirmation(
    roster: OrganizationInventory,
) -> None:
    """Mutation: report it beside the evidenced matches with nothing distinguishing it.

    Fifteen roster members hold no AWS role, so this is the only rule that reaches them and
    dropping it would leave the tool answering almost nothing. It is kept and marked, so that
    what a reader copies without thinking is the evidenced half.
    """
    # Somebody the roster holds no `Intern-*` role for, so the evidenced rule cannot reach
    # them and only the derivation can. Fifteen of the thirty-five are in that position.
    assert "banksaj27" not in aws_role_local_parts(roster)
    found = match_roster(roster, [account("U09", "", "adam.banks@alphaaiengineering.com")])

    matched = {match.github_login: match for match in found.matched}
    assert matched["banksaj27"].evidence == BY_DERIVED_EMAIL
    assert BY_DERIVED_EMAIL in WEAK_EVIDENCE
    assert "confirm each of these" in render(found, [account("U09", "", "a@b.c")])


# ---------------------------------------------------------------------------------------
# what it refuses
# ---------------------------------------------------------------------------------------


def test_two_accounts_claiming_one_person_emits_neither(roster: OrganizationInventory) -> None:
    """**Mutation: take the first, or take the one with the stronger evidence.**

    A person with a second account, or a deactivated one Slack still lists, is ordinary. There
    is nothing in either account that says which one they read, so choosing is a coin toss
    whose losing side is a message nobody sees while the sender believes it was delivered.
    """
    found = match_roster(
        roster,
        [
            account("U01", "Frank Gonzalez", "frank.gonzalez@alphaaiengineering.com"),
            account("U02", "Frank Gonzalez", "frank.gonzalez@contractor.invalid"),
        ],
    )

    assert not [match for match in found.matched if match.github_login == "philote-dev"]
    assert any("philote-dev" in note for note in found.ambiguous)


def test_one_account_claimed_by_two_people_emits_neither(
    roster: OrganizationInventory,
) -> None:
    """**Mutation: let both keep it, since each was matched on its own evidence.**

    This is the two-way half and it needs its own pass over the results, because each match
    looks correct in isolation. A shared or misconfigured account, or two people whose
    directory spellings collide, ends with one id written against two logins, and every
    message to one of them goes to the other as well.
    """
    found = match_roster(
        roster,
        [account("U01", "Meric Xing", "meric.xing@alphaaiengineering.com")],
    )
    assert [match.github_login for match in found.matched] == ["meric233"]

    contested = match_roster(
        roster,
        [account("U01", "Grant Matherne", "nathan.zhao@alphaaiengineering.com")],
    )

    assert contested.matched == []
    assert any("U01" in note for note in contested.ambiguous)
    assert any("GMatherne" in note and "nzhao721" in note for note in contested.ambiguous)


def test_bots_deleted_accounts_and_slackbot_never_reach_the_matcher() -> None:
    """Mutation: filter them after matching, or not at all.

    An app's account carries a real name and sometimes an address. Matching one and then
    hiding it leaves the ambiguity checks counting accounts that are not people, so a person
    with one real account plus a colliding bot is refused as ambiguous for no reason.
    """
    payload: list[dict[str, Any]] = [
        {"id": "U01", "profile": {"real_name_normalized": "Frank Gonzalez"}},
        {"id": "U02", "is_bot": True, "profile": {"real_name_normalized": "eduLLM Notifications"}},
        {"id": "U03", "deleted": True, "profile": {"real_name_normalized": "Someone Gone"}},
        {"id": "USLACKBOT", "profile": {"real_name_normalized": "Slackbot"}},
        {"profile": {"real_name_normalized": "No id at all"}},
    ]

    assert [found.member_id for found in slack_accounts(payload)] == ["U01"]


def test_a_workspace_with_no_addresses_reads_as_a_missing_scope(
    roster: OrganizationInventory,
) -> None:
    """Mutation: report thirty-five unresolved people and say nothing about why.

    A token holding ``users:read`` and not ``users:read.email`` returns every account with the
    email field absent rather than failing, so ``users.list`` succeeds and the report is empty
    for a reason the report does not state. Adding a scope in the app configuration also does
    nothing until the app is reinstalled to the workspace, which is the step people skip.
    """
    accounts = [account("U01", "Frank Gonzalez"), account("U02", "Amy Lin")]

    printed = render(match_roster(roster, accounts), accounts)

    assert "users:read.email" in printed
    assert "reinstall" in printed


def test_the_report_says_it_wrote_nothing(roster: OrganizationInventory) -> None:
    """Mutation: write the ids into the roster while you are here.

    The brief for this tool was to make the ids collectable and to stop there, because whether
    to address people individually at all is an open question with a measured three minute
    median wait against it. A tool that edits the roster answers that question by acting.
    """
    accounts = [account("U01", "Frank Gonzalez", "frank.gonzalez@alphaaiengineering.com")]

    printed = render(match_roster(roster, accounts), accounts)

    assert "Nothing was written" in printed
    assert "does not edit config/organization.yaml" in printed
