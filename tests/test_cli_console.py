"""``edullm console``: what it signs in as, what it withholds, and where it lands.

Nothing here reaches a network. :func:`~edullm_platform.cli.console.signin_token` takes its
opener as an argument, which is what lets the one HTTPS request in this binary be exercised
rather than stubbed out of existence, and ``tests/cli_support.py`` stubs it at the ``invoke``
layer so the end-to-end cases are about what the verb does with a token rather than about
getting one.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.cli.console import (
    CONSOLE_PLACES,
    FEDERATION_ENDPOINT,
    console_destination,
    export_credentials_argv,
    login_url,
    session_string,
    signin_token,
    signin_token_url,
    where_said,
)
from edullm_platform.cli.lane import SCRATCH_BUCKET, working_prefix
from edullm_platform.cli.main import EXIT_OK, EXIT_UNREACHABLE
from tests.cli_support import (
    CONSOLE_SIGNIN_TOKEN,
    FakeRunner,
    console_answers,
    failed,
    git_answers,
    invoke,
    lane_answers,
    ok,
    pages_opened,
)

A_SECRET = "not-a-secret-access-key"
CREDENTIALS = {
    "Version": 1,
    "AccessKeyId": "not-an-access-key",
    "SecretAccessKey": A_SECRET,
    "SessionToken": "not-a-session-token",
}


def a_console(tmp_path: Path, exported: Any = None) -> FakeRunner:
    """A laptop holding an ordinary broker session, answering the two calls this verb makes.

    ``exported`` overrides what ``export-credentials`` answers, which is the only call whose
    failure this verb has a branch for. A parameter rather than a merged dict, because the key
    is a tuple and keyword arguments are not.
    """
    answers: dict[tuple[str, ...], Any] = {**git_answers(tmp_path), **lane_answers()}
    answers.update(console_answers())
    if exported is not None:
        answers[("aws", "configure", "export-credentials")] = exported
    return FakeRunner(answers)


class _Answers:
    """An opener that returns one body, standing in for the sign-in endpoint."""

    def __init__(self, body: str) -> None:
        self.body = body
        self.asked: list[str] = []

    def open(self, url: str, data: bytes | None = None, timeout: float = 0) -> Any:
        self.asked.append(url)
        return io.BytesIO(self.body.encode("utf-8"))


class _Raises:
    """An opener that fails the way a blocked network does, with the URL in the message."""

    def __init__(self) -> None:
        self.reason = f"{FEDERATION_ENDPOINT}?Action=getSigninToken&Session=...{A_SECRET}..."

    def open(self, url: str, data: bytes | None = None, timeout: float = 0) -> Any:
        raise urllib.error.URLError(self.reason)


# ---------------------------------------------------------------------------------------
# the one that costs a credential
# ---------------------------------------------------------------------------------------


def test_a_failed_exchange_never_quotes_the_url_it_was_exchanging() -> None:
    """THE ONE THAT MATTERS HERE. Mutation: put the error in the refusal, as every other one does.

    Every other refusal in this binary quotes what AWS said, and that is right everywhere else.
    Here the URL being fetched carries the caller's secret access key in its query string, and
    ``urllib``'s exceptions stringify to include the URL -- so the helpful version of this
    refusal prints a live credential into a terminal, and from there into the issue somebody
    pastes it in.
    """
    opener = _Raises()

    assert signin_token(signin_token_url("{}"), opener=opener) == ""
    assert A_SECRET in opener.reason, "the fixture stopped describing the leak under test"


def test_the_credentials_go_in_the_query_string_and_the_session_duration_does_not() -> None:
    """Mutation: ask for twelve hours explicitly.

    AWS documents ``SessionDuration`` as available only where the caller obtained its own
    credentials by calling ``AssumeRole*``, and says the request fails for role chaining. These
    arrive from the broker already assumed. Omitted, "the session defaults to the duration of
    the credentials" -- which is the number the parameter would have been asking for.
    """
    url = signin_token_url(session_string(CREDENTIALS))
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

    assert query["Action"] == ["getSigninToken"]
    assert "SessionDuration" not in query
    assert json.loads(query["Session"][0]) == {
        "sessionId": CREDENTIALS["AccessKeyId"],
        "sessionKey": A_SECRET,
        "sessionToken": CREDENTIALS["SessionToken"],
    }


@pytest.mark.parametrize(
    "credentials",
    [{}, {"AccessKeyId": "k"}, {"AccessKeyId": "k", "SecretAccessKey": "s"}, {"AccessKeyId": ""}],
)
def test_a_credential_missing_a_field_is_empty_rather_than_a_traceback(
    credentials: dict[str, str],
) -> None:
    """Mutation: index into it.

    A ``KeyError`` out of here is a traceback in front of a researcher whose broker session has
    gone stale, which is the ordinary morning state rather than an exceptional one.
    """
    assert session_string(credentials) == ""


@pytest.mark.parametrize("body", ["", "not json", "[]", "{}", '{"SigninToken": 3}'])
def test_a_body_that_is_not_a_token_is_empty_rather_than_a_traceback(body: str) -> None:
    """Mutation: trust the shape. There is one thing the caller does about any of these."""
    assert signin_token(signin_token_url("{}"), opener=_Answers(body)) == ""


def test_a_token_is_read_out_of_the_body_aws_actually_returns() -> None:
    """The control, so the case above cannot pass by the parse never working at all."""
    opener = _Answers('{"SigninToken": "a-token"}')

    assert signin_token(signin_token_url("{}"), opener=opener) == "a-token"
    assert opener.asked[0].startswith(FEDERATION_ENDPOINT)


# ---------------------------------------------------------------------------------------
# where it lands
# ---------------------------------------------------------------------------------------


def test_the_login_url_carries_the_token_and_the_destination() -> None:
    """Mutation: drop the issuer, or the destination.

    Without a destination the console opens wherever it last was, which for somebody who has
    never had a console session is a page about signing in. The issuer is where a person is
    sent when the session ends, and a repository explains itself better than a sign-in form
    nobody has a password for.
    """
    url = login_url(token="a-token", destination="https://console.aws.amazon.com/s3")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

    assert query["Action"] == ["login"]
    assert query["SigninToken"] == ["a-token"]
    assert query["Destination"] == ["https://console.aws.amazon.com/s3"]
    assert query["Issuer"][0].startswith("https://")


def test_the_work_bucket_lands_on_the_caller_s_own_prefix() -> None:
    """Mutation: land on the bucket root.

    "How do I look at my bucket" is the question this place exists for, and the bucket is
    shared: the root of it is thirty-five people's prefixes, which is the answer to somebody
    else's question. The prefix is the same one ``edullm run`` syncs to, derived rather than
    spelled, so the console and the sync cannot point at two different places.
    """
    where = console_destination("work", region="us-east-1", person="frank.gonzalez", domain_id="d-x")

    assert SCRATCH_BUCKET in where
    assert urllib.parse.quote(working_prefix(person="frank.gonzalez")) in where


def test_the_studio_place_names_the_domain_rather_than_the_service() -> None:
    """Mutation: link the SageMaker landing page.

    There is one domain and the account has other SageMaker things in it. A link to the service
    is a link to a page somebody then has to search, which is the navigation this verb exists
    to skip.
    """
    where = console_destination("studio", region="us-east-1", person="p", domain_id="d-bxqz8")

    assert "d-bxqz8" in where
    assert "sagemaker" in where


@pytest.mark.parametrize("place", CONSOLE_PLACES)
def test_every_place_is_a_console_url_in_the_configured_region(place: str) -> None:
    """Mutation: leave a place out of the mapping and let it fall through silently.

    ``choices`` means argparse accepts every name here, so a name with no destination behind it
    opens the front door and says it opened somewhere else. The region is the domain's rather
    than the shell's, for the reason ``config/reports/studio.yaml`` gives about ARNs: a console
    page in the wrong region is an empty page, and an empty bucket list reads as a missing
    bucket rather than as a misconfigured console.
    """
    where = console_destination(place, region="eu-west-2", person="p", domain_id="d-x")

    assert where.startswith("https://console.aws.amazon.com/")
    assert "eu-west-2" in where
    assert where_said(place, person="p").startswith("Signing in to ")


def test_two_places_do_not_share_one_destination() -> None:
    """Mutation: return the front door for anything unrecognised, including recognised names."""
    everywhere = {
        console_destination(place, region="us-east-1", person="p", domain_id="d-x")
        for place in CONSOLE_PLACES
    }

    assert len(everywhere) == len(CONSOLE_PLACES)


# ---------------------------------------------------------------------------------------
# the verb
# ---------------------------------------------------------------------------------------


def test_it_opens_a_browser_and_puts_no_credential_in_the_scrollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print the URL, which is what ``studio`` did and what broke everybody.

    A login URL is a bearer credential for as long as it lives. Printing it as well as opening
    it puts one in the terminal history of every person who never needed to see it.
    """
    code, out, err = invoke(
        ["console"], runner=a_console(tmp_path), cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_OK
    assert pages_opened()
    assert out == ""
    assert CONSOLE_SIGNIN_TOKEN not in err
    assert "Opened it in your browser" in err


def test_print_url_puts_the_url_on_stdout_and_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print the explanation to stdout beside it.

    The whole point of the flag is that ``edullm console --print-url | pbcopy`` copies a URL.
    A caller that has to strip three paragraphs off it is a caller who parses prose, which is
    the thing every machine-readable surface in this binary exists to avoid.
    """
    code, out, err = invoke(
        ["console", "logs", "--print-url"],
        runner=a_console(tmp_path),
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK
    assert not pages_opened()
    assert out.strip().startswith(FEDERATION_ENDPOINT)
    assert CONSOLE_SIGNIN_TOKEN in out
    assert out.count("\n") == 1
    assert "logsV2" in urllib.parse.unquote(out)
    assert "treat it as a password" in err


def test_it_assumes_no_role_because_it_grants_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE PROPERTY THAT SEPARATES THIS VERB FROM EVERY OTHER AWS ONE. Mutation: enter the lane.

    ``run``, ``shell`` and ``studio`` all assume the researcher lane, because they are about to
    spend money under a role whose policy bounds the spending. This spends nothing. Signing
    somebody into a console as a role they did not ask for would show them a different account
    than their own commands see, which is the confusing half of an answer.
    """
    runner = a_console(tmp_path)

    invoke(["console"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert runner.ran("aws", "sts", "assume-role") == []
    assert runner.ran("aws", "configure", "export-credentials")


def test_a_broker_session_that_has_gone_stale_is_a_refusal_and_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: let the exporter's failure through.

    This is the ordinary morning state. It is 3 rather than 1 because nothing about what the
    person typed is wrong, which is the distinction a retry loop is built on.
    """
    runner = a_console(tmp_path, exported=failed("Unable to locate credentials"))

    code, out, err = invoke(["console"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_UNREACHABLE
    assert out == ""
    assert "no_aws_session" in err
    assert not pages_opened()


def test_a_credential_the_exporter_printed_as_nonsense_is_refused_before_the_exchange(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: send whatever came back to AWS.

    An exporter that exits zero and prints something that is not a credential document is a
    broker half-way through an upgrade. Sending it produces a 400 whose refusal has to withhold
    its own reason, which is a much worse sentence than the one that names the actual problem.
    """
    runner = a_console(tmp_path, exported=ok("not a credential"))

    code, _, err = invoke(["console"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_UNREACHABLE
    assert "no_aws_session" in err


def test_the_exporter_is_asked_for_the_format_this_can_read() -> None:
    """Mutation: take the default format, which is an env-var script rather than JSON."""
    assert export_credentials_argv() == (
        "aws",
        "configure",
        "export-credentials",
        "--format",
        "process",
    )
