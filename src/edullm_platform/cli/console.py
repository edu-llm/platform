"""``edullm console``: the AWS console, signed in as whoever is at the keyboard.

**NOBODY IN THIS ORGANISATION HAS A CONSOLE PASSWORD, WHICH IS THE WHOLE ARGUMENT FOR THE VERB.**
The broker issues CLI credentials and nothing else. There is no IAM user with a password, no
Identity Center portal and no SAML provider, so the ordinary way in does not exist -- and the
questions it would answer are asked constantly: what is in my bucket, where are the logs for
that, what did this cost, what does the Studio domain look like. Every one of those has an
answer that is a console page and no answer that is a command anybody remembers.

Federation is the only route to a console session from a credential, and it needs nobody to be
granted anything: ``getSigninToken`` at the AWS sign-in endpoint exchanges the caller's own
temporary credentials for a token, and the token redeems into a console signed in **as that
caller**, with exactly their permissions and nobody else's. It was measured against this account
on 2026-08-06: it signs in as ``Intern-<person>-sbsandbox`` and reaches Studio, S3 and
CloudWatch.

**IT DOES NOT REPLACE ``edullm studio`` AND THE TWO ARE NOT ALTERNATIVES.** That question was
asked directly and the answer is that they solve different problems, which the numbers make
plain. A presigned Studio URL lands *inside* JupyterLab -- on the space's own host, at
``/jupyterlab/default/lab``, with the person's files open -- because ``CreatePresignedDomainUrl``
takes a ``SpaceName`` and resolves it. Federation cannot do that: it reaches the SageMaker
*console*, which is a page about spaces with buttons on it, two clicks and a correct guess away
from the notebook. Presigned URLs also carry the domain's ``ExecutionRoleSessionNameMode`` of
``USER_IDENTITY``, which is what puts a person's own name on what their notebook does in
CloudTrail; a console session does not produce that. So Studio keeps its own door.

What federation is better at is everything that is not Studio, and one thing that is: **it lasts**.
The sign-in token must be redeemed within fifteen minutes, against the Studio URL's five, and the
console session it opens then runs as long as the credentials behind it -- twelve hours here,
against a Studio URL that is dead in three hundred seconds and cannot be asked for more. So this
is the verb for looking at things, and ``edullm studio`` is the verb for working in one.

**THIS MODULE MAKES NO AWS API CALL AND RUNS NO PROCESS.** It builds URLs and sentences;
``main.py`` runs the one command that exports credentials and this module's single HTTPS request
goes through an injected opener, which is ``notifications/delivery.py``'s arrangement and is here
for the same reason.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any, Final, Protocol

from edullm_platform.cli.lane import SCRATCH_BUCKET, working_prefix
from edullm_platform.cli.preflight import Refusal

__all__ = [
    "CONSOLE_PLACES",
    "FEDERATION_ENDPOINT",
    "ISSUER",
    "SIGNIN_TOKEN_MINUTES",
    "Opener",
    "console_destination",
    "could_not_reach_the_sign_in_endpoint",
    "could_not_read_a_credential",
    "export_credentials_argv",
    "login_url",
    "opened_said",
    "session_string",
    "signin_token",
    "signin_token_url",
    "where_said",
]

#: AWS's sign-in endpoint. The global one rather than a regional
#: ``<region>.signin.aws.amazon.com``, because a console session is not regional -- the region
#: appears in the destination page's own query string and the person changes it with the picker
#: in the corner like everybody else.
FEDERATION_ENDPOINT: Final = "https://signin.aws.amazon.com/federation"

#: Where somebody is sent when the console session ends. Optional, recommended by AWS, and worth
#: setting to something that explains itself: a person whose session expires lands on the
#: repository that issued it rather than on a generic sign-in page they have no password for.
ISSUER: Final = "https://github.com/edu-llm/platform"

#: How long the login URL is good for, which is AWS's number and not a setting.
#:
#: **FIFTEEN MINUTES, AGAINST A PRESIGNED STUDIO URL'S FIVE, AND THAT IS THREE TIMES THE BUDGET
#: FOR THE SAME JOB.** It is also a shorter string -- around 1,900 characters against 4,300 --
#: so where ``edullm studio --print-url`` produces something that genuinely cannot be carried
#: through a terminal by hand, this produces something that can. The verb still opens the browser
#: itself, for the reasons ``cli/browser.py`` gives, but the fallback here is a usable fallback.
SIGNIN_TOKEN_MINUTES: Final = 15


class Opener(Protocol):
    """Whatever performs the one HTTPS GET, so a test can answer it without a network.

    :func:`urllib.request.build_opener`'s ``open``, narrowed. The default is built at the call
    site rather than being the :mod:`urllib.request` module, which ``delivery.py`` records the
    reason for: the module has ``urlopen`` and no ``open``, so naming it here type-checks
    against nothing and fails at run time on the first call.
    """

    def open(self, url: str, data: bytes | None = ..., timeout: float = ...) -> Any: ...


def export_credentials_argv() -> tuple[str, ...]:
    """Ask the AWS CLI for the credentials it would use, in a form this can read.

    **THE CALLER'S OWN CREDENTIALS AND NOT THE LANE ROLE'S, WHICH IS THE OPPOSITE OF WHAT THE
    OTHER AWS VERBS DO.** ``edullm run``, ``shell`` and ``studio`` all assume the researcher lane
    first, because they are about to spend money under a role whose policy is what bounds the
    spending. This verb spends nothing and grants nothing: it opens a window onto what the person
    can already see. Signing them into a console as a role they did not ask for would show them a
    different account than their own commands see, which is the confusing half of an answer.

    ``export-credentials`` rather than reading ``~/.aws``: the profile here is a
    ``credential_process``, so there is no key in any file to read, and the CLI is the only thing
    that knows how to run the broker.
    """
    return ("aws", "configure", "export-credentials", "--format", "process")


def session_string(credentials: Mapping[str, Any]) -> str:
    """The three-field JSON document the federation endpoint expects, or ``""`` where it cannot.

    Empty rather than a raise, for :func:`~edullm_platform.cli.studio.studio_name_for`'s reason:
    the caller has a refusal to render, and a traceback in front of a researcher is the one thing
    this binary promises not to produce.

    The field names are AWS's and are not the ones the CLI prints -- ``sessionId`` for the access
    key id, ``sessionKey`` for the secret -- which is the kind of mapping worth doing in one place
    with a name on it.
    """
    try:
        wanted = (
            credentials["AccessKeyId"],
            credentials["SecretAccessKey"],
            credentials["SessionToken"],
        )
    except (KeyError, TypeError):
        return ""
    if not all(isinstance(field, str) and field for field in wanted):
        return ""
    identifier, secret, token = wanted
    return json.dumps({"sessionId": identifier, "sessionKey": secret, "sessionToken": token})


def signin_token_url(session: str) -> str:
    """Where to ask for a sign-in token, with the credentials in the query string.

    **NO ``SessionDuration``, AND ITS ABSENCE IS WHAT MAKES THE SESSION TWELVE HOURS RATHER THAN
    WHAT LIMITS IT TO ONE.** AWS documents the parameter as available only where the caller
    obtained its credentials by calling ``AssumeRole*`` itself, and says in as many words that it
    fails for role chaining. The credentials here arrive from the broker already assumed, so
    passing it is the failure mode rather than the feature. Omitted, "the session defaults to the
    duration of the credentials", which is the broker's twelve hours -- the number this would
    have asked for.
    """
    return f"{FEDERATION_ENDPOINT}?" + urllib.parse.urlencode(
        {"Action": "getSigninToken", "Session": session}
    )


def signin_token(url: str, *, opener: Opener | None = None, timeout: float = 30.0) -> str:
    """Exchange the credentials for a token, or ``""`` where AWS would not.

    Empty on every failure and never an exception, because there is exactly one thing the caller
    does about a connection error, a non-200 and a body that is not the expected JSON, and that
    is :func:`could_not_reach_the_sign_in_endpoint`.

    **NOTHING FROM THE FAILURE IS PASSED OUT AND THAT IS DELIBERATE.** The URL being fetched has
    the caller's secret access key in its query string, and ``urllib``'s exceptions stringify to
    include the URL. A refusal that helpfully quoted the error would print a live credential into
    somebody's terminal, and from there into the issue they paste it in.
    """
    reader = urllib.request.build_opener() if opener is None else opener
    try:
        with reader.open(url, None, timeout) as answer:  # https, and the scheme is ours
            body = json.load(answer)
    except (urllib.error.URLError, OSError, ValueError):
        return ""
    if not isinstance(body, Mapping):
        return ""
    token = body.get("SigninToken")
    return token if isinstance(token, str) else ""


def login_url(*, token: str, destination: str) -> str:
    """The URL that opens a signed-in console at that page.

    A bearer credential, like the presigned Studio URL and for the same reason: whoever holds it
    within fifteen minutes becomes the person it was cut for. It is never put in a ``--json``
    document, for the reason ``studio_document`` gives about the other one.
    """
    return f"{FEDERATION_ENDPOINT}?" + urllib.parse.urlencode(
        {
            "Action": "login",
            "Issuer": ISSUER,
            "Destination": destination,
            "SigninToken": token,
        }
    )


#: The pages people ask for by name, which is a short list on purpose.
#:
#: **EVERY ONE OF THESE IS A QUESTION SOMEBODY HAS ACTUALLY ASKED**, rather than a tour of the
#: console. A place that is not here is still reachable -- the console has its own navigation and
#: this signs somebody into all of it -- so the list is a set of shortcuts and never a fence. It
#: is a mapping of name to a builder rather than to a string because two of them need the region
#: and one needs the person.
CONSOLE_PLACES: Final = ("home", "studio", "work", "outputs", "logs", "batch")


def console_destination(place: str, *, region: str, person: str, domain_id: str) -> str:
    """The console page one name means, defaulting to the console's own front door.

    The region comes from ``config/reports/studio.yaml`` rather than from the caller's shell, for
    the reason that file gives about ARNs: a page opened in whatever region a laptop happens to
    be pointed at is an empty page, and an empty bucket list reads as a missing bucket rather
    than as a misconfigured console.
    """
    console = "https://console.aws.amazon.com"
    if place == "studio":
        return f"{console}/sagemaker/home?region={region}#/studio/{domain_id}"
    if place == "work":
        prefix = urllib.parse.quote(working_prefix(person=person))
        return f"{console}/s3/buckets/{SCRATCH_BUCKET}?region={region}&prefix={prefix}"
    if place == "outputs":
        return f"{console}/s3/buckets/sbsandbox-intern-edullm-outputs?region={region}"
    if place == "logs":
        return f"{console}/cloudwatch/home?region={region}#logsV2:log-groups"
    if place == "batch":
        return f"{console}/batch/home?region={region}#jobs"
    return f"{console}/console/home?region={region}"


def where_said(place: str, *, person: str) -> str:
    """What page this is about to open, said before it opens.

    Said because a browser that comes up on the wrong page is indistinguishable from a browser
    that came up on the right page for the wrong account, and the person is the only one who can
    tell those apart.
    """
    named = {
        "home": "the console home page",
        "studio": "the SageMaker Studio domain",
        "work": f"your own prefix in the {SCRATCH_BUCKET} bucket",
        "outputs": "the outputs bucket",
        "logs": "the CloudWatch log groups",
        "batch": "the Batch job list",
    }.get(place, "the console home page")
    return f"Signing in to {named} as {person}."


def opened_said() -> str:
    """What a person is told once the browser has been handed the page.

    **IT NAMES THE TWO CLOCKS, BECAUSE THEY ARE DIFFERENT AND BOTH MATTER.** The link dies in
    fifteen minutes and the session it opens lives for hours, so somebody who leaves the tab open
    over lunch is fine and somebody who mails the link to a colleague after lunch is not.
    """
    return (
        f"The sign-in link is good for {SIGNIN_TOKEN_MINUTES} minutes and the console session it "
        "opens lasts as long as your credentials do. It signs in as you, so it can see exactly "
        "what your own commands can see and nothing more. Do not share the link: for those "
        f"{SIGNIN_TOKEN_MINUTES} minutes anybody who has it is you."
    )


def could_not_read_a_credential(said: str) -> Refusal:
    """The AWS CLI would not say what credentials it holds, so nothing could be exchanged."""
    return Refusal(
        code="no_aws_session",
        detail=(
            "the AWS CLI could not produce a credential to sign in with, so no console session "
            "was opened. This is the same session every other edullm verb uses, so if it is "
            f"missing they will all fail too. What the CLI said: {said.strip()}"
        ),
    )


def could_not_reach_the_sign_in_endpoint() -> Refusal:
    """AWS would not exchange the credentials, and the reason is withheld on purpose.

    The withholding is not vagueness for its own sake; :func:`signin_token` records that the
    exception text carries the caller's secret access key.
    """
    return Refusal(
        code="sign_in_unreachable",
        detail=(
            "the AWS sign-in endpoint did not return a token, so no console session was opened "
            "and nothing was changed. The reason is withheld because it would quote a URL "
            "carrying your credentials. This is a call that failed rather than anything about "
            "what you typed, so running it again is the remedy."
        ),
    )
