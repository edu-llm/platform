"""The join, and the one way it fails without anybody noticing.

**A SHORT LIST IS INDISTINGUISHABLE FROM A CLEAN DAY, AND THAT IS WHAT THESE TESTS ARE FOR.**
The filter joins launch events to people through a table of role names. A role the table does
not carry is filtered out, so its launches produce no mismatch and no error, and the report
comes back empty exactly as it does on a morning when nothing was wrong. The fix is that the
report carries its denominator and the renderer prints it, and the test that holds that is
`test_a_list_shortened_by_a_missing_role_does_not_read_like_a_clean_day` below.

Nothing here reaches AWS. Launch events are built by hand, because the shape this parses is
fixed by CloudTrail and the contents change every hour.
"""

from __future__ import annotations

from datetime import UTC, datetime

from edullm_platform.mismatch import (
    LaunchEvent,
    compute_mismatches,
    render_line,
    render_section,
)

AMY = "Intern-amy.lin-sbsandbox"
ALAN = "Intern-alan.abraham-sbsandbox"
AUTOSCALING = "AWSServiceRoleForAutoScaling"
PREVIEW = "sbsandbox-intern-edullm-run-preview"

ROSTER = {AMY: "alsy7009", ALAN: "aabraham"}
A_RUN = "run_019fa73d-be37-7066-984b-a4bacf194f49"
#: A well-formed run id that no test ever puts in `known_run_ids`. Named rather than written
#: inline because the test below is only testing anything while it differs from `A_RUN`, and
#: an inline literal is one careless copy away from being the same string.
AN_UNRECORDED_RUN = "run_019fa96f-8f10-705a-a7a9-69c42eafce16"
assert AN_UNRECORDED_RUN != A_RUN


def _launch(event_id: str, role_name: str, *, run_id: str | None = None) -> LaunchEvent:
    return LaunchEvent(
        event_id=event_id,
        event_name="RunInstances",
        occurred_at=datetime(2026, 8, 4, 14, 16, tzinfo=UTC),
        role_name=role_name,
        run_id=run_id,
    )


def test_a_roster_launch_with_no_lineage_record_is_a_mismatch() -> None:
    """Mutation: treat an absent run id as accounted for rather than as a mismatch."""
    report = compute_mismatches(
        [_launch("e1", AMY)],
        role_logins=ROSTER,
        excluded_roles=(),
        known_run_ids=frozenset(),
    )
    assert [(m.role_name, m.github_login) for m in report.mismatches] == [(AMY, "alsy7009")]


def test_a_roster_launch_carrying_a_known_run_id_is_accounted_for() -> None:
    """Mutation: ignore known_run_ids, which makes every platform launch a mismatch."""
    report = compute_mismatches(
        [_launch("e1", AMY, run_id=A_RUN)],
        role_logins=ROSTER,
        excluded_roles=(),
        known_run_ids=frozenset({A_RUN}),
    )
    assert report.mismatches == ()
    assert report.accounted == 1


def test_a_run_id_the_lineage_store_has_never_heard_of_is_a_mismatch() -> None:
    """Mutation: accept any run id rather than checking it against the store.

    A tag is written by whoever launched the instance, so a run id nobody recorded is a
    claim rather than a record, and treating the tag as proof would let one tag clear a
    launch the platform never saw.

    The launched run id has to differ from the recorded one or this test passes on a
    mismatch that is not the one it means; `AN_UNRECORDED_RUN` asserts that at import.
    """
    report = compute_mismatches(
        [_launch("e1", AMY, run_id=AN_UNRECORDED_RUN)],
        role_logins=ROSTER,
        excluded_roles=(),
        known_run_ids=frozenset({A_RUN}),
    )
    assert len(report.mismatches) == 1


def test_a_role_the_table_does_not_carry_is_counted_rather_than_dropped() -> None:
    """Mutation: skip an unresolved role instead of tallying it.

    This is the denominator. Without it the two launches below leave no trace at all.
    """
    report = compute_mismatches(
        [_launch("e1", AUTOSCALING), _launch("e2", AUTOSCALING), _launch("e3", AMY)],
        role_logins=ROSTER,
        excluded_roles=(),
        known_run_ids=frozenset(),
    )
    assert report.events_examined == 3
    assert [(t.role_name, t.launches) for t in report.unresolved] == [(AUTOSCALING, 2)]
    assert report.unresolved_launches == 2


def test_every_event_lands_in_exactly_one_bucket() -> None:
    """Mutation: any branch that returns early without tallying.

    The four buckets are the whole of the denominator, so an event that reaches none of them
    is an event the report has silently lost.
    """
    report = compute_mismatches(
        [
            _launch("e1", AMY),
            _launch("e2", ALAN, run_id=A_RUN),
            _launch("e3", AUTOSCALING),
            _launch("e4", PREVIEW),
        ],
        role_logins=ROSTER,
        excluded_roles=(PREVIEW,),
        known_run_ids=frozenset({A_RUN}),
    )
    assert report.events_examined == 4
    assert len(report.mismatches) == 1
    assert report.accounted == 1
    assert report.unresolved_launches == 1
    assert report.excluded_launches == 1
    assert report.adds_up is True


def test_a_list_shortened_by_a_missing_role_does_not_read_like_a_clean_day() -> None:
    """Mutation: drop `unresolved` from MismatchReport, from is_clean, or from render_section.

    THIS IS THE TEST THE WHOLE MODULE EXISTS FOR. Both reports below find zero mismatches.
    One found zero because the day was clean; the other found zero because the only person
    who launched anything is missing from the table. If those two produce the same verdict or
    the same text, the instrument is lying in the direction nobody checks.
    """
    clean = compute_mismatches(
        [_launch("e1", AMY, run_id=A_RUN)],
        role_logins=ROSTER,
        excluded_roles=(),
        known_run_ids=frozenset({A_RUN}),
    )
    narrowed = compute_mismatches(
        [_launch("e1", "Intern-nobody.listed-sbsandbox")],
        role_logins=ROSTER,
        excluded_roles=(),
        known_run_ids=frozenset(),
    )

    assert clean.mismatches == ()
    assert narrowed.mismatches == ()
    assert clean.is_clean is True
    assert narrowed.is_clean is False
    assert "Intern-nobody.listed-sbsandbox" in render_section(narrowed)
    assert render_line(clean) != render_line(narrowed)


def test_an_unresolved_role_that_looks_like_a_person_is_called_out() -> None:
    """Mutation: report unresolved roles as a bare count with no names.

    Forty AWS service roles and one Intern- role read identically as the number 41. Every
    human role in this account carries that prefix, so the prefix is worth reporting on --
    and it is a highlight rather than a filter, which is why nothing is dropped for missing
    it and both roles below are still counted.
    """
    report = compute_mismatches(
        [_launch("e1", AUTOSCALING), _launch("e2", "Intern-nobody.listed-sbsandbox")],
        role_logins=ROSTER,
        excluded_roles=(),
        known_run_ids=frozenset(),
    )
    assert [t.role_name for t in report.unresolved_people] == ["Intern-nobody.listed-sbsandbox"]
    assert report.unresolved_launches == 2


def test_the_line_names_the_figures_the_message_is_read_for() -> None:
    """Mutation: print the mismatch count without the denominator beside it."""
    report = compute_mismatches(
        [_launch("e1", AMY), _launch("e2", AUTOSCALING)],
        role_logins=ROSTER,
        excluded_roles=(),
        known_run_ids=frozenset(),
    )
    line = render_line(report)
    assert "1 mismatch" in line
    assert "2 launch events" in line
    assert "1 role" in line


def test_the_plural_of_mismatch_is_not_mismatchs() -> None:
    """Mutation: build every plural by adding an s.

    It is the first figure of the message and the word the whole surface is named after. A
    reader who meets "2 mismatchs" in the opening clause has been told something about how
    carefully the rest of the arithmetic was done.
    """
    two = compute_mismatches(
        [_launch("e1", AMY), _launch("e2", ALAN)],
        role_logins=ROSTER,
        excluded_roles=(),
        known_run_ids=frozenset(),
    )
    assert "2 mismatches" in render_line(two)
    assert "mismatchs" not in render_line(two)


def test_a_denominator_that_does_not_add_up_says_so_rather_than_being_read() -> None:
    """Mutation: drop the adds_up clause from the line, or from is_clean.

    A report whose buckets do not sum to what it examined has a defect in this module rather
    than a finding about the account, and every figure on it is then unreadable. Built by
    hand here rather than by breaking the computation, because the point is that the renderer
    says so whatever produced it.
    """
    from edullm_platform.mismatch import MismatchReport, RoleTally

    wrong = MismatchReport(
        events_examined=9,
        mismatches=(),
        accounted=1,
        resolved=(RoleTally(role_name=AMY, launches=1),),
        unresolved=(),
        excluded=(),
    )
    assert wrong.adds_up is False
    assert wrong.is_clean is False
    assert "do not add up" in render_line(wrong)


def test_the_section_says_what_the_filter_cannot_see() -> None:
    """Mutation: drop the blind-spot paragraph once the list looks convincing.

    Three of the four things this cannot see are permanent and none of them is being fixed,
    so saying them is the whole of the mitigation. A page that lists mismatches and says
    nothing about its own scope reads as an account of the account.
    """
    section = render_section(
        compute_mismatches(
            [_launch("e1", AMY)],
            role_logins=ROSTER,
            excluded_roles=(),
            known_run_ids=frozenset(),
        )
    )
    assert "not a launch" in section
    assert "administrators" in section
    assert "no AWS role at all" in section
