"""Counting asks, and the one thing the count is for.

The count is not a metric. system-overview.md says one place makes asks countable "which turns
the third identical one into a config change" -- so the output that matters is the list of kinds
that have crossed the threshold, and the counts are how it is derived.
"""

from __future__ import annotations

from tools.report_asks import ASK_KINDS, asks_worth_a_config_change, count_by_kind


def issue(*labels: str) -> dict[str, object]:
    return {"labels": [{"name": name} for name in labels]}


def test_an_ask_is_counted_under_every_kind_label_it_carries() -> None:
    """Mutation: take the first label and stop.

    An ask can genuinely be two kinds -- a dataset request that also reports the run it broke
    is both -- and taking the first would attribute it to whichever label GitHub happened to
    return first.
    """
    counts = count_by_kind(
        [issue("ask", "access-request"), issue("ask", "dataset-request", "run-problem")]
    )

    assert counts["access-request"] == 1
    assert counts["dataset-request"] == 1
    assert counts["run-problem"] == 1


def test_a_label_that_is_not_a_kind_is_ignored() -> None:
    """Mutation: count every label.

    Issues carry labels nobody chose from the form -- `good first issue`, `wontfix`, a milestone
    label, and `ask` itself, which every one of them has. Counting them makes the output a label
    census rather than an answer about asks, and `ask` in particular would appear as a kind with
    the highest count on the board.
    """
    counts = count_by_kind([issue("ask", "access-request", "wontfix", "P2")])

    assert set(counts) == set(ASK_KINDS)
    assert counts["access-request"] == 1


def test_every_kind_appears_in_the_count_including_the_empty_ones() -> None:
    """Mutation: build the mapping only from labels that appeared.

    A kind with no asks is a real answer and a different one from a kind the counter has never
    heard of. Dropping the zeros makes the two indistinguishable, which is the same denominator
    argument 2026-08-04-the-instruments.md makes about the mismatch list.
    """
    counts = count_by_kind([])

    assert counts == dict.fromkeys(ASK_KINDS, 0)


def test_a_kind_at_the_threshold_is_reported_and_one_below_it_is_not() -> None:
    """Mutation: use a strict comparison.

    "The third identical one" means three, so three crosses it. An exclusive comparison reports
    on the fourth and silently moves the rule the overview states.
    """
    counts = dict.fromkeys(ASK_KINDS, 0)
    counts["access-request"] = 3
    counts["dataset-request"] = 2

    assert asks_worth_a_config_change(counts, threshold=3) == ("access-request",)


def test_the_report_is_ordered_by_count_and_then_by_name() -> None:
    """Mutation: return the kinds in ASK_KINDS order.

    The output is read by a person deciding what to build next, so the largest has to be first.
    Ties broken by name so two runs over the same data print the same thing, which is what makes
    the audit's step summary diffable.

    The two fives are deliberately given in the order that makes ASK_KINDS order and name order
    disagree: `dataset-request` is set before `access-request` here and sorts after it, so a
    counter that preserved insertion order rather than sorting would return them the other way
    round.
    """
    counts = dict.fromkeys(ASK_KINDS, 0)
    counts["dataset-request"] = 5
    counts["access-request"] = 5
    counts["feedback"] = 9

    assert asks_worth_a_config_change(counts, threshold=3) == (
        "feedback",
        "access-request",
        "dataset-request",
    )


def test_the_kinds_counted_are_the_kinds_the_form_and_the_verb_offer() -> None:
    """Mutation: give this tool a list of its own, copied from the form on the day it was read.

    Three readers -- the form's dropdown, `edullm ask --kind`, and this counter -- and one list,
    which lives in the package because that is the copy an installed wheel carries and so the
    only one that cannot move. A kind this tool did not know about is an ask filed under a label
    nothing counts, and it is invisible rather than wrong: the board simply never shows it.
    """
    from edullm_platform.cli.intake import ASK_KINDS as SHIPPED

    assert ASK_KINDS is SHIPPED
