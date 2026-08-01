"""The draft assignment report, and the inferences it is built not to make.

Every test here is about one of two things. Whether a signal that names a team is graded by
how it named it, and whether a signal that does not name a team is left alone. The second
half is the point of the module: the failure this report exists to avoid is a plausible
guess rendered in the same table as a run record, and a guess is indistinguishable from a
fact once it is in `member_logins`.

The roster is built rather than loaded from ``config/organization.yaml``. The shipped one
declares six teams with no members, which is the state the report has to handle and is also
a state where several of these assertions cannot be written at all.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from edullm_platform.contracts.bindings import TeamBinding, TeamBindingCatalog
from edullm_platform.contracts.inventory import OrganizationInventory, PersonRef

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from report_team_assignments import (
    EXIT_OK,
    EXIT_UNUSABLE,
    Assignment,
    Confidence,
    Evidence,
    ReportInputError,
    SubmittedRun,
    assignments,
    main,
    parse_evidence,
    render,
    render_csv,
)

OBSERVED_AT = "2026-08-01T14:00:00+00:00"

EVERYBODY = (
    "philote-dev",
    "aryanjverma",
    "syz2026",
    "hiyasvyas",
    "caiiris",
    "katiehehe",
    "mccorkel",
)


def inventory(
    *,
    members: tuple[str, ...] = EVERYBODY,
    team_members: dict[str, tuple[str, ...]] | None = None,
) -> OrganizationInventory:
    listed = team_members or {}
    return OrganizationInventory(
        admins=("philote-dev",),
        team_leads=("philote-dev",),
        members=tuple(PersonRef(github_login=login) for login in members),
        pilot_repositories=("OLMo-core",),
        team_bindings=TeamBindingCatalog(
            teams=tuple(
                TeamBinding(
                    team_id=team_id,
                    github_team_slug=team_id,
                    member_logins=listed.get(team_id, ()),
                    s3_namespace=f"sbsandbox-intern-edullm-outputs/teams/{team_id}",
                    wandb_entity="eduLLM",
                )
                for team_id in ("curriculum", "memory-split", "platform", "tokenizer")
            )
        ),
    )


def gathered(**overrides: object) -> dict[str, object]:
    return {
        "observed_at": OBSERVED_AT,
        "organization": "edu-llm",
        "github_team_members": {},
        "output_prefix_runs": {},
        "repository_contributors": {},
        "repository_push_actors": {},
        "authored_path_segments": {},
        **overrides,
    }


def evidence(**overrides: object) -> Evidence:
    return parse_evidence(gathered(**overrides))


def drafted(people: list[Assignment], login: str) -> Assignment:
    return next(entry for entry in people if entry.github_login == login)


# ---------------------------------------------------------------------------------------
# A record of the person and the team together
# ---------------------------------------------------------------------------------------


def test_a_run_submitted_under_a_declared_team_places_its_submitter_strongly() -> None:
    people = assignments(
        inventory(),
        runs=(SubmittedRun(run_id="run_a", submitter="aryanjverma", team="tokenizer"),),
        evidence=evidence(),
    )

    entry = drafted(people, "aryanjverma")
    assert (entry.team_id, entry.confidence) == ("tokenizer", Confidence.STRONG)
    assert "submitted 1 run under team `tokenizer`" in entry.evidence


def test_a_run_claiming_a_team_the_bindings_do_not_carry_places_nobody() -> None:
    """Mutation: treat the string in the manifest as a team because a run carried it.

    A claimed team is a claim, and `evaluation` is the live instance: two records carry it,
    it was never a declared group, and it is not `eval-inference` under an earlier spelling.
    Grading a claim as a record would let it become a group with a member in it, and the
    roster would then carry a team nothing declares.
    """
    people = assignments(
        inventory(),
        runs=(SubmittedRun(run_id="run_a", submitter="philote-dev", team="evaluation"),),
        evidence=evidence(),
    )

    entry = drafted(people, "philote-dev")
    assert (entry.team_id, entry.confidence) == (None, Confidence.NONE)
    assert "`team_bindings` does not declare" in entry.evidence


def test_the_prefix_a_run_wrote_to_is_reported_beside_the_team_it_claimed() -> None:
    """Two claims and not one. Mutation: fold the bucket into the lineage signal.

    The manifest says what the submitter asked for and the prefix says where the bytes
    went. Reported together, a run whose artifacts landed under a team its manifest does
    not name reads as one person with two teams, which is what gets it looked at.
    """
    people = assignments(
        inventory(),
        runs=(SubmittedRun(run_id="run_a", submitter="philote-dev", team="platform"),),
        evidence=evidence(output_prefix_runs={"platform": ["run_a"], "memory-split": []}),
    )

    entry = drafted(people, "philote-dev")
    assert entry.confidence is Confidence.STRONG
    assert {signal.source for signal in entry.placed} == {"lineage", "outputs"}


def test_a_prefix_holding_somebody_elses_run_says_nothing_about_this_person() -> None:
    people = assignments(
        inventory(),
        runs=(SubmittedRun(run_id="run_a", submitter="philote-dev", team="platform"),),
        evidence=evidence(output_prefix_runs={"platform": ["run_a"]}),
    )

    assert drafted(people, "katiehehe").signals == ()


def test_membership_of_the_matching_github_team_places_somebody_strongly() -> None:
    """Somebody put this person in that group, in a system, on purpose."""
    people = assignments(
        inventory(),
        runs=(),
        evidence=evidence(github_team_members={"memory-split": ["syz2026"]}),
    )

    entry = drafted(people, "syz2026")
    assert (entry.team_id, entry.confidence) == ("memory-split", Confidence.STRONG)


def test_a_roster_line_that_already_exists_is_read_rather_than_redrafted() -> None:
    """Mutation: build the draft only from evidence outside the roster.

    Groups are filled in one at a time by their leads. A report that ignored the lines
    already written would propose a weaker answer for somebody whose lead has settled it,
    and the correction it invites is to undo a decision.
    """
    people = assignments(
        inventory(team_members={"curriculum": ("hiyasvyas",)}),
        runs=(),
        evidence=evidence(repository_contributors={"memory-split": ["hiyasvyas"]}),
    )

    entry = drafted(people, "hiyasvyas")
    assert (entry.team_id, entry.confidence) == ("curriculum", Confidence.STRONG)


def test_a_login_spelled_in_another_case_is_still_the_same_person() -> None:
    """GitHub folds a login and the roster is written by people spelling their own names."""
    people = assignments(
        inventory(),
        runs=(SubmittedRun(run_id="run_a", submitter="ARYANJVERMA", team="tokenizer"),),
        evidence=evidence(github_team_members={"memory-split": ["SYZ2026"]}),
    )

    assert drafted(people, "aryanjverma").team_id == "tokenizer"
    assert drafted(people, "syz2026").team_id == "memory-split"


# ---------------------------------------------------------------------------------------
# A name, which is weaker, and a topic, which is nothing
# ---------------------------------------------------------------------------------------


def test_a_repository_called_what_a_team_is_called_places_somebody_weakly() -> None:
    people = assignments(
        inventory(),
        runs=(),
        evidence=evidence(repository_contributors={"Memory-Split": ["caiiris"]}),
    )

    entry = drafted(people, "caiiris")
    assert (entry.team_id, entry.confidence) == ("memory-split", Confidence.WEAK)
    assert "`Memory-Split`" in entry.evidence


def test_a_directory_called_what_a_team_is_called_places_somebody_weakly() -> None:
    people = assignments(
        inventory(),
        runs=(),
        evidence=evidence(
            authored_path_segments={"hiyasvyas": ["OLMo-core:curriculum", "OLMo-core:scripts"]}
        ),
    )

    entry = drafted(people, "hiyasvyas")
    assert (entry.team_id, entry.confidence) == ("curriculum", Confidence.WEAK)
    assert "`curriculum/` in OLMo-core" in entry.evidence


def test_a_repository_named_for_a_team_and_a_project_is_still_named_for_the_team() -> None:
    """``Memory-Split-P3`` is the memory-split group's third project.

    The team id followed by a separator, which is a name somebody composed rather than a
    coincidence of letters. Still weak: it says the work was filed under the group.
    """
    people = assignments(
        inventory(),
        runs=(),
        evidence=evidence(repository_contributors={"Memory-Split-P3": ["caiiris"]}),
    )

    entry = drafted(people, "caiiris")
    assert (entry.team_id, entry.confidence) == ("memory-split", Confidence.WEAK)


@pytest.mark.parametrize(
    "name",
    [
        "tokenizer_utils",
        "detokenizer",
        "edullm-token-selection",
        "platformer",
        "my-platform",
    ],
)
def test_a_name_that_merely_contains_a_team_name_places_nobody(name: str) -> None:
    """Mutation: match a team id anywhere in a name.

    ``src/olmo_core/data/tokenizer.py`` is a file called tokenizer.py and half the
    repository has edited it. ``edullm-token-selection`` is about which tokens a training
    step keeps loss on, which is not a tokenizer and not the tokenizer group. A contains
    rule grades both, and each one it grades is a person filed under a group on the
    strength of a coincidence of letters.
    """
    people = assignments(
        inventory(),
        runs=(),
        evidence=evidence(
            repository_contributors={name: ["katiehehe"]},
            authored_path_segments={"katiehehe": [f"OLMo-core:{name}"]},
        ),
    )

    entry = drafted(people, "katiehehe")
    assert (entry.team_id, entry.confidence) == (None, Confidence.NONE)


def test_somebody_whose_commits_github_cannot_attribute_is_found_by_their_pushes() -> None:
    """Mutation: read the contributor list and nothing else.

    A commit authored from a laptop whose git email belongs to no GitHub account has no
    author login, so however much this person wrote the contributor list does not carry
    them and the draft reports them as invisible. GitHub still knows who pushed the branch.
    """
    people = assignments(
        inventory(),
        runs=(),
        evidence=evidence(repository_push_actors={"Memory-Split-P3": ["katiehehe"]}),
    )

    entry = drafted(people, "katiehehe")
    assert (entry.team_id, entry.confidence) == ("memory-split", Confidence.WEAK)
    assert "pushed branches" in entry.evidence


def test_writing_and_pushing_in_one_repository_is_reported_as_having_written() -> None:
    """Somebody who pushed their own commits is not two people working in one place."""
    people = assignments(
        inventory(),
        runs=(),
        evidence=evidence(
            repository_contributors={"Memory-Split-P3": ["katiehehe"]},
            repository_push_actors={"Memory-Split-P3": ["katiehehe"]},
        ),
    )

    assert drafted(people, "katiehehe").evidence.count("Memory-Split-P3") == 1


def test_somebody_active_in_a_repository_no_team_is_named_after_is_placed_by_nothing() -> None:
    """THE INFERENCE THIS REPORT REFUSES TO MAKE. Mutation: add a topic-to-team map.

    An evaluation repository is not one of the six declared teams. Reading it as
    ``modeling``, or as anything else, would be one person's inference rendered in the same
    column as a run record, and once it is in ``member_logins`` nothing can tell the two
    apart. The repository is named in the evidence instead, so whoever corrects the draft
    can see the person is working and decide.
    """
    people = assignments(
        inventory(),
        runs=(),
        evidence=evidence(repository_contributors={"olmo-eval-full": ["katiehehe"]}),
    )

    entry = drafted(people, "katiehehe")
    assert (entry.team_id, entry.confidence) == (None, Confidence.NONE)
    assert "`olmo-eval-full`" in entry.evidence
    assert "no declared team is named after" in entry.evidence


def test_a_record_beats_a_name_when_one_person_has_both() -> None:
    people = assignments(
        inventory(),
        runs=(SubmittedRun(run_id="run_a", submitter="syz2026", team="platform"),),
        evidence=evidence(repository_contributors={"memory-split": ["syz2026"]}),
    )

    entry = drafted(people, "syz2026")
    assert (entry.team_id, entry.confidence) == ("platform", Confidence.STRONG)
    assert "memory-split" in entry.evidence


def test_two_teams_at_the_same_strength_still_draft_one_line_and_name_the_other() -> None:
    """Mutation: report no team when the evidence points at two.

    A person who has worked under two groups is the interesting case, and a blank would
    report them as the same thing as a person nobody knows about.
    """
    people = assignments(
        inventory(),
        runs=(
            SubmittedRun(run_id="run_a", submitter="philote-dev", team="platform"),
            SubmittedRun(run_id="run_b", submitter="philote-dev", team="platform"),
            SubmittedRun(run_id="run_c", submitter="philote-dev", team="memory-split"),
        ),
        evidence=evidence(),
    )

    entry = drafted(people, "philote-dev")
    assert (entry.team_id, entry.confidence) == ("platform", Confidence.STRONG)
    assert "memory-split" in entry.evidence


# ---------------------------------------------------------------------------------------
# Who is in the draft at all
# ---------------------------------------------------------------------------------------


def test_somebody_the_roster_does_not_name_gets_no_line() -> None:
    """Mutation: draft over the union of the roster and the organization.

    ``member_logins`` is validated against ``members``, so a line proposing a group for
    somebody who is not on the roster proposes an edit that cannot be applied. Whether they
    belong on the roster at all is what ``tools/report_onboarding_readiness.py`` answers.
    """
    people = assignments(
        inventory(members=("philote-dev",)),
        runs=(SubmittedRun(run_id="run_a", submitter="a-stranger", team="platform"),),
        evidence=evidence(),
    )

    assert [entry.github_login for entry in people] == ["philote-dev"]


def test_an_excluded_login_is_left_out_even_though_the_roster_carries_it() -> None:
    people = assignments(inventory(), runs=(), evidence=evidence(), exclude=("MCCORKEL",))

    assert "mccorkel" not in [entry.github_login for entry in people]


def test_the_draft_is_ordered_so_the_answered_lines_come_first() -> None:
    people = assignments(
        inventory(),
        runs=(SubmittedRun(run_id="run_a", submitter="aryanjverma", team="tokenizer"),),
        evidence=evidence(repository_contributors={"memory-split": ["caiiris"]}),
    )

    bands = [entry.confidence for entry in people]
    assert bands == sorted(bands, key=(Confidence.STRONG, Confidence.WEAK, Confidence.NONE).index)


# ---------------------------------------------------------------------------------------
# What gets written out
# ---------------------------------------------------------------------------------------


def test_the_csv_is_the_four_columns_the_draft_is_corrected_in() -> None:
    rows = render_csv(
        assignments(
            inventory(),
            runs=(SubmittedRun(run_id="run_a", submitter="aryanjverma", team="tokenizer"),),
            evidence=evidence(),
        )
    ).splitlines()

    assert rows[0] == "login,team,confidence,evidence"
    assert rows[1].startswith("aryanjverma,tokenizer,strong,")


def test_a_person_placed_by_nothing_has_an_empty_team_column() -> None:
    """Mutation: write a placeholder there.

    The column is read by whoever fills the group in, and any word in it is a suggestion.
    """
    rows = render_csv(assignments(inventory(), runs=(), evidence=evidence())).splitlines()

    assert "katiehehe,,none,no signal in any source" in rows


def test_an_evidence_column_carrying_a_comma_is_quoted() -> None:
    """Mutation: join the columns without quoting.

    Every evidence string is composed from a list, so a comma in it is the ordinary case
    and an unquoted one moves a column without failing anything.
    """
    rows = render_csv(
        assignments(
            inventory(),
            runs=(),
            evidence=evidence(
                repository_contributors={"p3math": ["caiiris"], "p7stuff": ["caiiris"]}
            ),
        )
    ).splitlines()

    row = next(line for line in rows if line.startswith("caiiris,"))
    assert row.count(",") > 3
    assert '"' in row


def test_the_report_separates_a_person_with_no_signal_from_one_nothing_places() -> None:
    """The two answers a lead has to act on differently.

    One person is working somewhere this cannot read as a group. The other is not visible
    anywhere at all. Reported together they read as one list of people to chase.
    """
    people = assignments(
        inventory(),
        runs=(),
        evidence=evidence(repository_contributors={"olmo-eval-full": ["caiiris"]}),
    )
    report = render(people, evidence())

    active = report.index("## Active, and named by nothing")
    silent = report.index("## No signal in any source")
    assert report.index("caiiris", active) < silent
    assert report.index("katiehehe", silent) > silent


def test_the_report_says_when_it_was_taken() -> None:
    """Team membership is changed in a browser and leaves no artifact in this repository."""
    assert OBSERVED_AT in render(assignments(inventory(), runs=(), evidence=evidence()), evidence())


def test_a_lineage_record_the_contracts_refuse_is_counted_rather_than_ignored() -> None:
    """Mutation: report only the records that parsed.

    One stored record does not validate against the current contract, and it is a record of
    somebody running under a group. Left out silently it under-evidences whoever submitted
    it, and the report says so rather than describing a subset.
    """
    report = render(assignments(inventory(), runs=(), evidence=evidence()), evidence(), unparsed=1)

    assert "1 lineage record did not validate" in report


def test_the_report_says_out_loud_that_it_is_a_draft() -> None:
    """Mutation: render it as a table of assignments.

    Everything downstream of this treats `member_logins` as settled, so the one thing the
    report must not do is read like the record it is a draft for.
    """
    report = render(assignments(inventory(), runs=(), evidence=evidence()), evidence())

    assert "draft to correct and not a record" in report


# ---------------------------------------------------------------------------------------
# Reading the gathered file
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "absent",
    [
        "observed_at",
        "github_team_members",
        "output_prefix_runs",
        "repository_contributors",
        "repository_push_actors",
    ],
)
def test_a_gathered_file_missing_a_key_is_refused_rather_than_read_as_empty(
    absent: str,
) -> None:
    """Empty is the loudest answer this can be given: it places everybody at ``none``."""
    document = gathered()
    del document[absent]

    with pytest.raises(ReportInputError):
        parse_evidence(document)


def test_a_gathered_file_whose_shape_is_wrong_is_refused() -> None:
    with pytest.raises(ReportInputError):
        parse_evidence(gathered(github_team_members={"platform": "philote-dev"}))


# ---------------------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------------------


def lineage(root: Path, records: list[dict[str, object]]) -> Path:
    (root / "intent").mkdir(parents=True)
    (root / "attempt").mkdir(parents=True)
    for index, record in enumerate(records):
        (root / "intent" / f"{index}.json").write_text(json.dumps(record), encoding="utf-8")
    return root


def test_the_command_reports_and_refuses_nothing(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    """Exit 0 whatever it finds. This grades evidence; it has nothing to decline."""
    facts = tmp_path / "evidence.json"
    facts.write_text(json.dumps(gathered()), encoding="utf-8")
    csv = tmp_path / "draft.csv"

    exit_code = main(
        [
            "--evidence",
            str(facts),
            "--lineage-root",
            str(lineage(tmp_path / "lineage", [])),
            "--csv",
            str(csv),
        ]
    )

    assert exit_code == EXIT_OK
    assert csv.read_text(encoding="utf-8").startswith("login,team,confidence,evidence")
    assert "Draft research-group assignments" in capsys.readouterr().out


def test_an_unreadable_gathered_file_is_reported_as_unusable(tmp_path: Path) -> None:
    exit_code = main(
        [
            "--evidence",
            str(tmp_path / "absent.json"),
            "--lineage-root",
            str(lineage(tmp_path / "lineage", [])),
        ]
    )

    assert exit_code == EXIT_UNUSABLE


def test_a_call_that_never_reached_github_is_tried_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: lose the sweep to one dropped connection.

    A gather is tens of thousands of requests over several minutes and one of them will
    fail to connect. Without a retry the whole sweep is thrown away, which is what happened
    the first time this was run against the live organization.
    """
    import report_team_assignments as tool

    answers = iter(
        [
            _completed(1, stderr='dial tcp 140.82.114.6:443: connect: operation timed out'),
            _completed(0, stdout="[]"),
        ]
    )
    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: next(answers))
    monkeypatch.setattr(tool.time, "sleep", lambda _seconds: None)

    assert tool._github("orgs/edu-llm/repos") == []


def test_a_refusal_github_actually_answered_is_not_tried_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: retry everything.

    A 404 is an answer. Asking three more times gets the same answer more slowly, and on a
    sweep this size that turns one wrong repository name into minutes of waiting.
    """
    import report_team_assignments as tool

    calls = 0

    def once(*_args: object, **_kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return _completed(1, stderr="gh: Not Found (HTTP 404)")

    monkeypatch.setattr(tool.subprocess, "run", once)

    with pytest.raises(ReportInputError):
        tool._github("orgs/edu-llm/teams/absent/members")
    assert calls == 1


def _completed(returncode: int, *, stdout: str = "", stderr: str = ""):  # type: ignore[no-untyped-def]
    import subprocess

    return subprocess.CompletedProcess(args=["gh"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_gathering_into_the_fixtures_directory_is_refused(tmp_path: Path) -> None:
    """It names who has worked in every repository, so somebody reads it before it lands."""
    destination = tmp_path / "fixtures" / "evidence.json"
    destination.parent.mkdir(parents=True)

    exit_code = main(
        [
            "--gather",
            "--evidence",
            str(destination),
            "--lineage-root",
            str(lineage(tmp_path / "lineage", [])),
        ]
    )

    assert exit_code == EXIT_UNUSABLE
    assert not destination.exists()
