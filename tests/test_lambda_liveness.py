"""The instrument that catches a silent failure, held to not crying wolf on every deploy.

**This file exists because the check lied and lied loudly.** ``--invoke`` invoked the notifier,
read CloudWatch immediately, found no datapoint because Lambda publishes those a couple of
minutes late, and raised ``deployed_lambda_never_invoked`` about a function it had just watched
run four times cleanly. The window it lies in is exactly the window a deploy workflow runs it
in, and the sentence it lies with is the sentence it would use for a real outage. An instrument
built to catch silent failure that cries wolf on every deploy is ignored inside a week.

Every test here drives :func:`check` against a fabricated window rather than an account, and
that is the point rather than a convenience. The whole defect is a race with a publication
delay, so a test that reached CloudWatch would have to wait the delay out to observe either
half of it, which is the thing nobody does.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tools.release_lambda import FUNCTIONS
from tools.verify_lambdas_actually_run import (
    EXIT_DISAGREES,
    PUBLICATION_LAG_SECONDS,
    LambdaLivenessFinding,
    Window,
    check,
)


def window(*, age: int, invocations: int, errors: int = 0) -> Window:
    return Window(
        deployed_at=datetime.now(tz=UTC) - timedelta(seconds=age),
        invocations=invocations,
        errors=errors,
        seconds=age,
    )


@pytest.fixture
def account(monkeypatch: pytest.MonkeyPatch) -> Any:
    """One place to say what the account answered, for both of the calls ``check`` makes."""

    def arrange(*, measured: Window, smoke: bool | Exception = True) -> None:
        def invoked(key: str, name: str, **_: Any) -> bool:
            del key, name
            if isinstance(smoke, Exception):
                raise smoke
            return smoke

        monkeypatch.setattr("tools.verify_lambdas_actually_run.smoke_invoke", invoked)
        monkeypatch.setattr(
            "tools.verify_lambdas_actually_run.read_window", lambda *a, **k: measured
        )

    return arrange


def notifier(*, invoke: bool = True) -> str:
    return check(
        "notifier", FUNCTIONS["notifier"], invoke=invoke, profile=None, region="us-east-1"
    )


def test_a_smoke_that_returned_is_never_reported_as_never_invoked(account: Any) -> None:
    """THE LIE, IN THE EXACT SHAPE IT ARRIVED. Mutation: read the metric and believe a zero.

    This process invoked the function, read the response, and found no ``FunctionError``.
    Reporting in the same breath that the function has never been invoked is a
    self-contradiction rather than a finding. The invocation is a direct observation and the
    counter is a delayed report of the same event.
    """
    account(measured=window(age=40, invocations=0), smoke=True)

    answer = notifier()

    assert "returned on a smoke invocation just now" in answer
    assert "publication lag rather than a finding" in answer


def test_a_window_younger_than_the_lag_cannot_say_a_schedule_is_broken(account: Any) -> None:
    """The same defect on the function that has no smoke fixture.

    The janitor is schedule driven, so a silent window is a finding for it. It is not a
    finding two minutes after a deploy, because a schedule that has not fired yet and a
    schedule that is detached look identical this soon and the difference is a wait rather
    than a measurement.
    """
    account(measured=window(age=PUBLICATION_LAG_SECONDS - 1, invocations=0))

    answer = check("janitor", FUNCTIONS["janitor"], invoke=True, profile=None, region="us-east-1")

    assert "publishes Lambda metrics about 5 minutes late" in answer
    assert "Run this again in a few minutes." in answer


def test_a_schedule_that_is_silent_past_the_lag_is_still_a_finding(account: Any) -> None:
    """Mutation: soften the check into never raising on an empty window.

    The whole reason this file exists is to catch a function that does not run, and a
    schedule-driven one with a genuinely empty window past the publication delay is exactly
    that. Everything above narrows when an empty window may be believed. Nothing widens it.
    """
    account(measured=window(age=PUBLICATION_LAG_SECONDS + 60, invocations=0))

    with pytest.raises(LambdaLivenessFinding) as raised:
        check("janitor", FUNCTIONS["janitor"], invoke=True, profile=None, region="us-east-1")

    assert raised.value.reason == "deployed_lambda_never_invoked"
    assert raised.value.code == EXIT_DISAGREES


def test_a_function_that_has_never_succeeded_is_still_a_finding(account: Any) -> None:
    """The notifier's own state on 2026-08-06, which is the bug shape this was written for.

    934 invocations, 934 ``FileNotFoundError``, every artifact check green. Nothing in the
    two guards above touches this: the invocations are published, the errors are published,
    and no smoke was taken.
    """
    account(measured=window(age=86400, invocations=934, errors=934))

    with pytest.raises(LambdaLivenessFinding) as raised:
        notifier(invoke=False)

    assert raised.value.reason == "deployed_lambda_has_never_succeeded"


def test_a_smoke_that_returned_beside_published_failures_names_both(account: Any) -> None:
    """Two true things rather than a contradiction, and the reader needs both.

    The published invocations all raised and the one this process made did not. That is a
    function which works on the committed fixture and is failing on whatever real events are
    reaching it, which is a sharper finding than either half alone and is not something to
    raise about: raising would say the deployed code does not run, and it demonstrably does.
    """
    account(measured=window(age=600, invocations=3, errors=3), smoke=True)

    answer = notifier()

    assert "returned on a smoke invocation just now" in answer
    assert "real events are failing on a path the committed fixture does not take" in answer


def test_a_smoke_that_raised_is_a_finding_whatever_the_metric_says(account: Any) -> None:
    """The direct observation cuts both ways, which is what makes trusting it honest."""
    account(
        measured=window(age=40, invocations=0),
        smoke=LambdaLivenessFinding("smoke_invocation_raised", "it raised", code=EXIT_DISAGREES),
    )

    with pytest.raises(LambdaLivenessFinding) as raised:
        notifier()

    assert raised.value.reason == "smoke_invocation_raised"


def test_a_healthy_window_still_reads_as_a_count(account: Any) -> None:
    """The ordinary answer, unchanged. Mutation: report the smoke instead of the window."""
    account(measured=window(age=3600, invocations=7, errors=1), smoke=True)

    answer = notifier()

    assert "has succeeded 6 of 7 invocations since it was deployed at" in answer
    assert "Smoke invocation returned without error." in answer


def test_traffic_driven_functions_are_unchanged_by_any_of_this(account: Any) -> None:
    """A quiet night for the validator is an ordinary night and was never a finding."""
    account(measured=window(age=60, invocations=0))

    answer = check(
        "validator", FUNCTIONS["validator"], invoke=True, profile=None, region="us-east-1"
    )

    assert "driven by researcher traffic, so a quiet window is not a finding" in answer
