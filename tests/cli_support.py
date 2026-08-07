"""Fixtures for the CLI tests, and the one property they all rest on.

**NOTHING HERE REACHES A NETWORK, A CREDENTIAL OR AWS, AND THAT IS ENFORCED RATHER THAN
INTENDED.** :class:`FakeRunner` answers only the commands a test has written an answer
for and raises on anything else, so a change that made the CLI shell out to something new
fails here rather than passing quietly on a laptop where the tool happens to exist. It also
means an ``aws`` call added anywhere on this path is a test failure by construction, which
is the property worth having: this binary is a facade over two workflows and has no
business holding a cloud credential.

The reviewed configuration is the real ``config/`` in this repository rather than a fixture
copy, for the reason ``tests/test_compile_submission_cli.py`` gives about the same choice:
the values that decide a submission's fate are in those files, and a fixture copy would be
a second answer to every question they settle. It is reached by ``--config-dir``, which is an
absolute path, and never by the process finding it.

**AND THE PROCESS IS PUT IN THE TEMPORARY DIRECTORY IT IS TOLD ABOUT, WHICH IT WAS NOT UNTIL
2026-08-06.** ``invoke`` handed ``main`` a ``cwd`` and left the interpreter's own working
directory where pytest started it, which is the root of this repository. So every relative
path the CLI resolved -- ``config/reports/working-tier.yaml`` among them -- found a platform
checkout under it, in the suite and only in the suite. ``edullm run`` and ``edullm shell``
shipped and had never worked anywhere else, with 207 test modules green behind them, because
the one condition that would have shown it was the condition the suite could not produce.

:func:`invoke` now chdirs, so a verb that reads a file relative to the working directory
reads it out of an empty temporary directory here exactly as it would on a researcher's
laptop. Nothing was moved to make that safe: every fixture in this suite is reached through
``PROJECT_ROOT``, which is absolute and computed from ``__file__``.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import edullm_platform.cli.main as main_module
from edullm_platform.cli.lane import AWS_BROKER, SESSION_PLUGIN
from edullm_platform.cli.main import main
from edullm_platform.cli.preferences import DEFAULT_TEAM_FILE, PREFERENCES_DIRECTORY
from edullm_platform.cli.studio import IMAGE_ACCOUNT_PARAMETER
from edullm_platform.cli.workspace import CommandResult
from edullm_platform.researcher_lane import EXPIRES_AT_TAG_KEY, PROJECT_TAG_KEY

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"

#: Somebody the roster names on exactly one declared group, so team resolution has a single
#: answer and a test about anything else is not also a test about team resolution.
#: ``config/organization.yaml`` puts caiiris on memory-split and on scratch.
SUBMITTER = "caiiris"
SUBMITTER_TEAM = "memory-split"

#: A submitter the roster puts on two declared groups, which is what makes team resolution
#: ambiguous. ``alphaxia2100`` is one of the seven ``decisions.md`` counts.
SUBMITTER_ON_TWO_TEAMS = "alphaxia2100"

COMMIT = "8076c077533eb79742f4ed22aade439df123a593"

#: A command that satisfies both command guards on a one-device profile: it names a program,
#: it keeps its quoting through a shell wrapper, and the checkpoint directory expands.
TRAINING_COMMAND = (
    'bash -lc \'python .edullm/train_on_corpus.py "$EDULLM_RUN_ID" '
    '--save-folder "$EDULLM_CHECKPOINT_DIR"\''
)


class UnexpectedCommandError(AssertionError):
    """The CLI ran something no test told it how to answer."""


class FakeRunner:
    """Answers the commands a test declares, and refuses to invent one it did not.

    Matched on a prefix of the argv rather than on the whole of it, because the interesting
    part of most of these calls is the first three or four words and the rest is a path in a
    temporary directory. Longest prefix wins, so a test can answer ``git rev-parse HEAD``
    differently from ``git rev-parse --show-toplevel``.
    """

    def __init__(
        self,
        answers: Mapping[
            tuple[str, ...], CommandResult | Callable[[tuple[str, ...]], CommandResult]
        ],
    ) -> None:
        self._answers = dict(answers)
        self.calls: list[tuple[str, ...]] = []
        #: Beside ``calls`` rather than zipped into it, because a case about the lane wants the
        #: credential a call carried and every case that came before wants only the argv.
        self.environments: list[dict[str, str]] = []
        #: Which calls asked for a standard input the caller's own cannot close. Recorded rather
        #: than asserted here, because whether a given session needs one is the verb's decision
        #: and :meth:`held_stdin_open_for` is how a case reads it back.
        self.stdins_held: list[bool] = []
        #: Which calls were handed this process's own terminal rather than being captured. The
        #: companion to ``stdins_held`` and recorded for the same reason: which sessions are a
        #: person typing is the verb's decision, and :meth:`handed_over_the_terminal_for` is how
        #: a case reads it back.
        self.terminals_handed_over: list[bool] = []

    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        stdin_stays_open: bool = False,
        hands_over_the_terminal: bool = False,
    ) -> CommandResult:
        self.calls.append(argv)
        self.environments.append(dict(env or {}))
        self.stdins_held.append(stdin_stays_open)
        self.terminals_handed_over.append(hands_over_the_terminal)
        matches = [prefix for prefix in self._answers if argv[: len(prefix)] == prefix]
        if not matches:
            raise UnexpectedCommandError(
                f"no answer was declared for {' '.join(argv)}. Every command this CLI runs "
                "has to be one a test knew about, which is what keeps the suite off the "
                "network and away from AWS."
            )
        answer = self._answers[max(matches, key=len)]
        return answer(argv) if callable(answer) else answer

    def ran(self, *prefix: str) -> list[tuple[str, ...]]:
        return [argv for argv in self.calls if argv[: len(prefix)] == tuple(prefix)]

    def held_stdin_open_for(self, *prefix: str) -> list[bool]:
        """Whether each matching call asked the runner to hold its standard input open."""
        return [
            held
            for argv, held in zip(self.calls, self.stdins_held, strict=True)
            if argv[: len(prefix)] == tuple(prefix)
        ]

    def handed_over_the_terminal_for(self, *prefix: str) -> list[bool]:
        """Whether each matching call was given this process's terminal instead of a pipe."""
        return [
            handed
            for argv, handed in zip(self.calls, self.terminals_handed_over, strict=True)
            if argv[: len(prefix)] == tuple(prefix)
        ]

    def environment_for(self, *prefix: str) -> list[dict[str, str]]:
        """What each matching call was given on top of the ambient environment.

        The companion to :meth:`ran` for the two calls a lane verb makes as the person rather
        than as the lane: an empty mapping here means the call inherited whatever the shell
        had, which is the state a resolved profile is supposed to replace.
        """
        return [
            environment
            for argv, environment in zip(self.calls, self.environments, strict=True)
            if argv[: len(prefix)] == tuple(prefix)
        ]


def ok(stdout: str = "") -> CommandResult:
    return CommandResult(returncode=0, stdout=stdout, stderr="")


def failed(stderr: str = "", returncode: int = 1) -> CommandResult:
    return CommandResult(returncode=returncode, stdout="", stderr=stderr)


def git_answers(
    root: Path,
    *,
    repository: str = "OLMo-core",
    commit: str = COMMIT,
    dirty: Iterable[str] = (),
    untracked: Iterable[str] = (),
    pushed: bool = True,
) -> dict[tuple[str, ...], CommandResult]:
    """What ``read_git_facts`` asks git, answered as a clean pushed checkout by default.

    ``dirty`` is tracked files somebody has changed and ``untracked`` is files in no commit,
    and they are two arguments because the tool now answers them differently: the first is a
    gap between the laptop and the image and the second cannot be. One argument spelling both
    as ``M`` is what let a refusal written for the first arrive at the second.
    """
    return {
        ("git", "rev-parse", "--show-toplevel"): ok(f"{root}\n"),
        ("git", "rev-parse", "HEAD"): ok(f"{commit}\n"),
        ("git", "rev-parse", "--abbrev-ref", "HEAD"): ok("edullm/an-arm\n"),
        ("git", "remote", "get-url", "origin"): ok(f"git@github.com:edu-llm/{repository}.git\n"),
        ("git", "status", "--porcelain"): ok(
            "".join(f" M {path}\n" for path in dirty)
            + "".join(f"?? {path}\n" for path in untracked)
        ),
        ("git", "branch", "--remotes", "--contains"): ok(
            "  origin/edullm/an-arm\n" if pushed else ""
        ),
    }


#: The account id every lane fixture answers with. AWS reserves it for documentation, and
#: tests/test_evidence.py scans the tracked tree for anything shaped like a real one and allows
#: exactly this. Twelve zeroes is rejected there, so a fixture cannot quietly use one.
FAKE_ACCOUNT = "123456789012"

#: What ``sb-aws-creds install-profiles`` leaves in ``~/.aws/config``, copied off a laptop it had
#: been run on rather than composed. Three lines per profile and no role ARN among them, which is
#: the fact :func:`edullm_platform.cli.lane.broker_profiles` is built around.
#:
#: ``sbsandbox`` is a literal here and it is safe to be one: the broker's own client carries that
#: label as the one the intern route provisions, and its ``self-provision-sandbox`` call is
#: documented as hardcoding that target server-side, so it is an account label rather than
#: anything derived from a person. Nothing in the resolver reads the spelling, which is the
#: property that matters -- this fixture could name the profile anything and the code would behave
#: the same, because what identifies it is the broker on the ``credential_process`` line.
ONE_BROKER_PROFILE = """\
# >>> sb-aws-creds (managed) >>>
# Generated by sb-aws-creds. Do not edit by hand.

[profile sbsandbox]
credential_process = sb-aws-creds credential_process --profile sbsandbox
region = us-east-1

# <<< sb-aws-creds (managed) <<<
"""

LANE_INSTANCE = "i-0000000000000aaaa"

#: Every zone ``infra/batch-network.yaml`` declares a subnet in, and the one of the six that is
#: different. The account answers ``describe-subnets`` with all six for the lane's tag filter,
#: in an order of its own that puts ``us-east-1f`` first, which is the order these are written
#: in. Measured on 2026-08-06.
LANE_ZONES = (
    "us-east-1f",
    "us-east-1a",
    "us-east-1d",
    "us-east-1c",
    "us-east-1e",
    "us-east-1b",
)

#: The p5-only zone. ``infra/batch-network.yaml`` declares it for the two H100 shapes and its
#: Name tag says so, and EC2 offers neither ``g6.xlarge`` nor ``g4dn.xlarge`` there.
LANE_ZONE_FOR_P5_ONLY = "us-east-1e"

#: What EC2 answers for every shape a lane verb defaults to, which is five of the six. Kept as
#: the derived set rather than written out, so a zone added above reaches both.
LANE_ZONES_OFFERING = tuple(zone for zone in LANE_ZONES if zone != LANE_ZONE_FOR_P5_ONLY)

#: A fake subnet id per zone, shaped like a real one and distinct per zone so a test can read
#: which zone a launch was aimed at out of the argv.
LANE_SUBNETS = {zone: f"subnet-0000000000000{index:02d}b" for index, zone in enumerate(LANE_ZONES)}

#: What EC2 says when a zone has none of a shape to sell, in the words it actually uses. Copied
#: from a ``p5.4xlarge`` refused in ``us-east-1a`` on 2026-08-06, including the trailing list of
#: other zones -- which is not a capacity reading and names zones that were also empty.
NO_CAPACITY = (
    "An error occurred (InsufficientInstanceCapacity) when calling the RunInstances "
    "operation (reached max retries: 2): We currently do not have sufficient {instance_type} "
    "capacity in the Availability Zone you requested ({zone}). Our system will be working on "
    "provisioning additional capacity."
)

#: The expiry a reused fixture machine carries on its tag. Deliberately a round instant that no
#: arithmetic against the test clock produces, so a verb that computed one instead of reading
#: this cannot match it by coincidence.
LANE_EXISTING_EXPIRY = "2026-08-06T09:00:00Z"


def _run_instances(
    capacity_in: Collection[str],
) -> Callable[[tuple[str, ...]], CommandResult]:
    """A launch that answers per zone, read off the ``--subnet-id`` the argv carries.

    Keyed on the subnet rather than on a call counter, because what the lane decides is *which
    zone* and a counter would pass for a verb that asked the same zone five times -- which is
    exactly the defect this exists to catch.
    """
    zone_of = {subnet: zone for zone, subnet in LANE_SUBNETS.items()}

    def answer(argv: tuple[str, ...]) -> CommandResult:
        subnet = argv[argv.index("--subnet-id") + 1]
        instance_type = argv[argv.index("--instance-type") + 1]
        zone = zone_of.get(subnet, subnet)
        if zone in capacity_in:
            return ok(f"{LANE_INSTANCE}\n")
        return failed(NO_CAPACITY.format(instance_type=instance_type, zone=zone), returncode=254)

    return answer


#: How long a machine ``edullm stop`` finds has been up, and the shape it is.
#:
#: A duration rather than an instant, resolved against the clock when the fixture is built, so
#: a case can assert on the sentence the verb composes without freezing time. Two hours and a
#: quarter is 8100 seconds, which divides into hours exactly, so the money is the same figure
#: whether the verb runs a millisecond or a second after this is computed.
LANE_MACHINE_UPTIME = timedelta(hours=2, minutes=15)

#: ``gpu-1xt4``'s instance type, which ``config/workload-catalog.yaml`` prices. Named as the
#: type rather than the profile because the type is what EC2 reports and what the verb has to
#: look the price up by.
LANE_MACHINE_TYPE = "g4dn.xlarge"


def _describe_instances(
    *,
    existing: str | None,
    existing_expiry: str | None,
    stoppable: Collection[Mapping[str, object]] | None,
) -> Callable[[tuple[str, ...]], CommandResult]:
    """One ``describe-instances`` answering the two different questions the lane asks of it.

    **BRANCHED ON THE ``--query`` RATHER THAN ANSWERED ONCE, BECAUSE THE TWO CALLS ASK FOR
    DIFFERENT FIELDS AND A FIXTURE THAT CONFLATED THEM WOULD PROVE NOTHING ABOUT EITHER.**
    ``find_machine_argv`` asks for an id and its tags, which is what reuse needs.
    ``find_lane_machines_argv`` asks for the state, the type, the launch time and the market
    as well, because ``edullm stop`` reports what a machine was and what it ran up -- and a
    fake that returned the reuse shape to it would let a verb that never asked for those
    fields pass every case about the sentence they compose.
    """

    def answer(argv: tuple[str, ...]) -> CommandResult:
        query = argv[argv.index("--query") + 1]
        if "state:State.Name" in query:
            return ok(json.dumps(list(stoppable or [])))
        return ok(
            json.dumps(
                [
                    {
                        "machine": existing,
                        "tags": [{"Key": PROJECT_TAG_KEY, "Value": "mixlaw"}]
                        + (
                            [{"Key": EXPIRES_AT_TAG_KEY, "Value": existing_expiry}]
                            if existing_expiry
                            else []
                        ),
                    }
                ]
                if existing
                else []
            )
        )

    return answer


def a_machine_you_have(
    *,
    machine: str = LANE_INSTANCE,
    project: str = "mixlaw",
    state: str = "running",
    instance_type: str = LANE_MACHINE_TYPE,
    up_for: timedelta | None = LANE_MACHINE_UPTIME,
    stopped_for: timedelta | None = None,
    transition: str | None = None,
    spot: bool = False,
) -> dict[str, object]:
    """One entry in what ``find_lane_machines_argv`` gets back, in the account's own shape.

    ``up_for`` of ``None`` is a machine EC2 answered no ``LaunchTime`` for, which is the input
    that decides whether the verb states a cost or says it cannot. ``lifecycle`` is omitted
    entirely for an On-Demand machine rather than set to a word, because that is what EC2 does
    and a fake that spelled it out would let a reader that keyed on the wrong absence pass.

    ``stopped_for`` is a machine that has sat stopped that long: it is ``stopped``, and its
    ``StateTransitionReason`` carries the instant in EC2's own words, which were read off
    ``i-0303e11fbe92f4d9e`` in the sandbox account on 2026-08-06 and are ``User initiated
    (2026-08-06 14:13:58 GMT)`` -- a space rather than a ``T``, no offset, and the zone spelled
    ``GMT``. ``up_for`` still measures from the launch, so the two compose into a machine that
    ran ``up_for - stopped_for``.

    ``transition`` writes that field directly, for the reason with no instant in it that a
    ``shutdown -h`` from inside the machine leaves. A ``state`` given without either is a
    machine EC2 said nothing about, which is the same input one field further along.
    """
    # Whole seconds, because EC2 writes StateTransitionReason to the second and a fixture that
    # kept microseconds on one endpoint and not the other would make the interval between them
    # a fraction short -- and a cent of rounding that moves with the clock is a flaky case.
    now = datetime.now(tz=UTC).replace(microsecond=0)
    launched = None if up_for is None else now - up_for
    stopped = None if stopped_for is None else now - stopped_for
    said = transition
    if said is None and stopped is not None:
        said = f"User initiated ({stopped.strftime('%Y-%m-%d %H:%M:%S')} GMT)"
    return {
        "machine": machine,
        "state": "stopped" if stopped_for is not None else state,
        "type": instance_type,
        "launched": None if launched is None else launched.isoformat(),
        **({"transition": said} if said is not None else {}),
        **({"lifecycle": "spot"} if spot else {}),
        "tags": [
            {"Key": PROJECT_TAG_KEY, "Value": project},
            {"Key": EXPIRES_AT_TAG_KEY, "Value": LANE_EXISTING_EXPIRY},
        ],
    }


def lane_answers(
    *,
    existing: str | None = None,
    existing_expiry: str | None = LANE_EXISTING_EXPIRY,
    remote_exit: int | None = 0,
    agent: str = "Online",
    capacity_in: Collection[str] | None = None,
    offerings: Collection[str] | None = None,
    stoppable: Collection[Mapping[str, object]] | None = None,
) -> dict[tuple[str, ...], CommandResult | Callable[[tuple[str, ...]], CommandResult]]:
    """Every AWS call a lane verb makes, answered as a laptop already holding a session.

    ``remote_exit`` of ``None`` is a session that dropped: the stream carries output and no
    sentinel, which is what a Spot interruption in the middle of a command looks like.

    ``describe-instances`` answers the shape :func:`~edullm_platform.cli.lane.find_machine_argv`
    asks for, an instance id beside its tag list, rather than the bare id it asked for until
    2026-08-06. That is the whole seam the stale-expiry defect lived in: the fixture and the
    account have to agree about the answer's shape or a test proves nothing about a laptop.
    ``existing_expiry`` of ``None`` is a machine carrying no such tag, which is a machine
    launched before the tag existed or one somebody stripped.

    **``describe-subnets`` ANSWERS ALL SIX WITH THEIR ZONES, AND IT ANSWERED ONE BARE ID UNTIL
    2026-08-06 -- WHICH IS THE SAME DEFECT ONE CALL OVER.** A fixture holding a single subnet
    cannot tell a lane that tries every zone from one that pins the first, so every test about
    the thing this network is for would have passed either way. The six are the six the account
    declares, in the order the account returns them, and the fifth of them is the ``us-east-1e``
    subnet that exists for ``p5`` and nothing else.

    ``capacity_in`` is the zones that have a machine to sell, defaulting to all of them.
    ``offerings`` is what ``describe-instance-type-offerings`` answers, defaulting to the five
    zones the account offers every shape the lane can default to. Passing ``()`` for either is
    a zone list with nothing in it, which is the state a failed or throttled call leaves.

    Which repository the caller is standing in is :func:`git_answers`' business and not this
    one's. It changes no AWS answer, and the case the whole slice turns on -- a directory nothing
    registers -- is expressed by passing it there.
    """
    sentinel = "" if remote_exit is None else f"\nedullm-exit:{remote_exit}\n"
    return {
        ("aws", "sts", "get-caller-identity"): ok(
            json.dumps(
                {
                    "Account": FAKE_ACCOUNT,
                    "Arn": (
                        f"arn:aws:sts::{FAKE_ACCOUNT}:assumed-role/Intern-caiiris-sbsandbox"
                        "/broker-caiiris-1785873426"
                    ),
                }
            )
        ),
        ("aws", "sts", "assume-role"): ok(
            json.dumps(
                {
                    "Credentials": {
                        "AccessKeyId": "AKIAEXAMPLE",
                        "SecretAccessKey": "secret",
                        "SessionToken": "token",
                        "Expiration": "2026-08-06T00:00:00Z",
                    }
                }
            )
        ),
        ("aws", "ssm", "get-parameter"): ok("ami-000000000000000aa\n"),
        ("aws", "ec2", "describe-subnets"): ok(
            json.dumps([{"subnet": LANE_SUBNETS[zone], "zone": zone} for zone in LANE_ZONES])
        ),
        ("aws", "ec2", "describe-instance-type-offerings"): ok(
            json.dumps(list(LANE_ZONES_OFFERING if offerings is None else offerings))
        ),
        ("aws", "ec2", "describe-security-groups"): ok(json.dumps(["sg-000000000000000cc"])),
        ("aws", "ec2", "describe-instances"): _describe_instances(
            existing=existing, existing_expiry=existing_expiry, stoppable=stoppable
        ),
        ("aws", "ec2", "terminate-instances"): ok("shutting-down\n"),
        ("aws", "ec2", "run-instances"): _run_instances(
            LANE_ZONES if capacity_in is None else capacity_in
        ),
        ("aws", "ssm", "describe-instance-information"): ok(f"{agent}\n"),
        ("aws", "s3", "sync"): ok(""),
        ("aws", "ssm", "start-session"): ok(f"hello from the machine{sentinel}"),
    }


#: What ``create-presigned-domain-url`` hands back, minus the token, which is what makes it a
#: URL somebody could paste into a browser and not a credential sitting in a fixture.
STUDIO_URL = "https://studio-d-example.studio.us-east-1.sagemaker.aws/auth?token=not-a-token"

#: What the public SSM parameter answers with, standing in for Amazon's image account.
#:
#: NOT THE REAL TWELVE DIGITS, AND NOT BECAUSE THEY ARE SECRET. ``tests/test_evidence.py``
#: refuses every 12-digit run in the tracked tree and does not judge whose account an id is,
#: which is why the verb reads this from SSM rather than carrying it. A fixture holding the
#: real value would put back the literal the design exists to avoid.
STUDIO_IMAGE_ACCOUNT = "00image00acct"

#: The person ``lane_answers`` federates as, that person's Studio name, and the space one
#: project of theirs lives in. Held here rather than in the test module because
#: :func:`studio_answers` has to build a ``ListSpaces`` answer that agrees with what the verb
#: will derive, and two spellings of that agreement is one more than can be kept in step.
STUDIO_PERSON = "caiiris"
STUDIO_PROJECT = "mixlaw"
STUDIO_SPACE = f"{STUDIO_PERSON}-{STUDIO_PROJECT}"

#: The sign-in token :func:`invoke` hands ``edullm console`` instead of asking AWS for one.
#:
#: **THE ONE HTTPS REQUEST IN THIS BINARY IS STUBBED HERE FOR THE REASON EVERY SUBPROCESS IS.**
#: ``_console`` exchanges the caller's credentials at ``signin.aws.amazon.com``, and a suite that
#: made that call would reach a network, would need a real credential to get anything but a 400,
#: and would fail on a laptop with no route out. :func:`~edullm_platform.cli.console.signin_token`
#: is unit-tested directly against a fake opener, which is where the parsing belongs; what the
#: cases through ``invoke`` are about is what the verb does with a token once it has one.
CONSOLE_SIGNIN_TOKEN = "not-a-signin-token"

#: What ``aws configure export-credentials`` answers, in the shape the real CLI prints.
#:
#: Deliberately not a plausible-looking key. ``tests/test_evidence.py`` reads the tracked tree for
#: things that look like credentials, and a fixture that imitated one well enough to be useful
#: would be a fixture that trips it -- correctly.
CONSOLE_CREDENTIALS = json.dumps(
    {
        "Version": 1,
        "AccessKeyId": "not-an-access-key",
        "SecretAccessKey": "not-a-secret",
        "SessionToken": "not-a-session-token",
    }
)


def console_answers() -> dict[tuple[str, ...], CommandResult]:
    """The one command ``edullm console`` runs that no other verb does.

    Its own function rather than a line in :func:`lane_answers`, because the verb deliberately
    does not enter the lane -- ``cli/console.py`` argues why -- so a fixture that folded it in
    would describe a verb assuming a role this one never assumes.
    """
    return {("aws", "configure", "export-credentials"): ok(CONSOLE_CREDENTIALS)}


#: What every space in the live domain is sized at, which is not what the verb creates one at.
#: A fixture that used the configured size would never catch the verb quoting the configured
#: size for a space that has its own.
STUDIO_VOLUME_GIB = 5

#: The domain's idle timeout on the afternoon of 2026-08-06. A number here rather than in the
#: verb is the whole arrangement: this is a fixture standing in for what ``DescribeDomain``
#: says, and the verb has none of its own to go stale.
STUDIO_IDLE_MINUTES = 240


def studio_answers(
    *,
    app_status: str | None = None,
    space_exists: bool = True,
    profile_exists: bool = True,
    instance_type: str = "ml.t3.medium",
    image_account: str | None = STUDIO_IMAGE_ACCOUNT,
    spaces: Sequence[tuple[str, str, int]] | None = None,
    profiles: Sequence[str] | None = None,
    running: Sequence[str] = (),
    idle_minutes: int | None = STUDIO_IDLE_MINUTES,
) -> dict[tuple[str, ...], CommandResult | Callable[[tuple[str, ...]], CommandResult]]:
    """Every SageMaker call ``edullm studio`` makes, answered as a laptop holding a session.

    Separate from :func:`lane_answers` even though the verb enters the lane, because the two
    describe different surfaces and merging them would make every lane test carry a Studio
    domain it never reaches. ``a_platform`` merges both, which is the arrangement that keeps
    each one about the thing it is about.

    ``app_status`` of ``None`` is the ordinary state: a space with no app on it, so
    ``describe-app`` fails the way it does for a space that has never been started. The
    defaults are a person who has been here before -- profile and space already made -- because
    that is every invocation after the first, and the first is expressed by passing
    ``space_exists=False``.

    ``spaces`` is ``(name, owner, volume)`` triples and overrides ``space_exists`` entirely, for
    the cases about somebody who owns several or somebody whose derived name has landed on a
    space that is not theirs. ``running`` names the spaces ``ListApps`` reports an app on, which
    is what the listing and both stops read; ``app_status`` is what ``DescribeApp`` says about
    one space, which is what a start reads. They are separate because the verb asks two
    different questions and a fixture that fused them could not express a disagreement.

    ``image_account`` of ``None`` is the public SSM parameter refusing, which is the one
    failure that stops a start before anything is created. ``idle_minutes`` of ``None`` is a
    domain with lifecycle management off, which is what this one was until 2026-08-06.

    **THE SSM ANSWER SHARES A PREFIX WITH ``lane_answers``' AMI LOOKUP AND MUST WIN.**
    ``FakeRunner`` matches on the longest declared prefix, and both are
    ``("aws", "ssm", "get-parameter")``, so this entry is keyed on the parameter name as well.
    Merged after the lane's, which is why ``a_platform`` updates in that order.
    """
    described = (
        failed("An error occurred (ResourceNotFound) when calling the DescribeApp operation")
        if app_status is None
        else ok(
            json.dumps(
                {
                    "AppName": "default",
                    "Status": app_status,
                    "ResourceSpec": {"InstanceType": instance_type},
                }
            )
        )
    )
    if spaces is None:
        spaces = ((STUDIO_SPACE, STUDIO_PERSON, STUDIO_VOLUME_GIB),) if space_exists else ()
    if profiles is None:
        profiles = (STUDIO_PERSON,) if profile_exists else ()
    known = set(profiles)

    def profile_of(argv: tuple[str, ...]) -> CommandResult:
        """``describe-user-profile`` is asked about two names and answers about both.

        The caller's own, to decide whether to create it, and the space name the project
        derived, to catch the shared-namespace collision before ``CreateSpace`` reports it in
        words nobody can act on. A fixture that answered the same way to both could not tell
        those two calls apart, which is exactly the bug this catches.
        """
        name = argv[argv.index("--user-profile-name") + 1]
        return (
            ok(json.dumps({"UserProfileName": name}))
            if name in known
            else failed("An error occurred (ResourceNotFound)")
        )

    return {
        # Longer than ``lane_answers``' bare ``get-parameter`` on purpose: ``FakeRunner`` takes
        # the longest matching prefix, so naming the parameter is what keeps the AMI lookup and
        # this one from answering each other.
        ("aws", "ssm", "get-parameter", "--name", IMAGE_ACCOUNT_PARAMETER): (
            failed("An error occurred (ParameterNotFound)")
            if image_account is None
            else ok(f"{image_account}\n")
        ),
        ("aws", "sagemaker", "describe-domain"): ok(json.dumps(_a_domain(idle_minutes))),
        ("aws", "sagemaker", "list-spaces"): ok(json.dumps(_listed_spaces(spaces))),
        ("aws", "sagemaker", "list-apps"): ok(json.dumps(_listed_apps(running, instance_type))),
        ("aws", "sagemaker", "describe-app"): described,
        ("aws", "sagemaker", "describe-user-profile"): profile_of,
        ("aws", "sagemaker", "create-user-profile"): ok(json.dumps({"UserProfileArn": "arn:x"})),
        ("aws", "sagemaker", "create-space"): ok(json.dumps({"SpaceArn": "arn:x"})),
        ("aws", "sagemaker", "create-app"): ok(json.dumps({"AppArn": "arn:x"})),
        ("aws", "sagemaker", "delete-app"): ok(""),
        ("aws", "sagemaker", "create-presigned-domain-url"): ok(f"{STUDIO_URL}\n"),
    }


def _a_domain(idle_minutes: int | None) -> dict[str, object]:
    """``DescribeDomain`` as the live domain answers it, with or without a timeout.

    The nesting is copied from the real answer rather than flattened, because the reader under
    test walks four keys to reach the number and a shallower fixture would pass whatever it
    did.
    """
    idle: dict[str, object] = (
        {"LifecycleManagement": "DISABLED"}
        if idle_minutes is None
        else {
            "LifecycleManagement": "ENABLED",
            "IdleTimeoutInMinutes": idle_minutes,
            "MinIdleTimeoutInMinutes": 60,
            "MaxIdleTimeoutInMinutes": 480,
        }
    )
    return {
        "DomainId": "d-example",
        "Status": "InService",
        "AuthMode": "IAM",
        "DefaultUserSettings": {
            "JupyterLabAppSettings": {"AppLifecycleManagement": {"IdleSettings": idle}},
        },
    }


def _listed_spaces(spaces: Sequence[tuple[str, str, int]]) -> dict[str, object]:
    """``ListSpaces`` as the service shapes it, ``Summary`` suffixes and all.

    The suffixes are the trap this fixture exists to hold: ``DescribeSpace`` answers
    ``OwnershipSettings`` and ``ListSpaces`` answers ``OwnershipSettingsSummary``, so a reader
    written against the wrong one finds nothing and reports that nobody owns anything.
    """
    return {
        "Spaces": [
            {
                "DomainId": "d-example",
                "SpaceName": name,
                "Status": "InService",
                "SpaceSettingsSummary": {
                    "AppType": "JupyterLab",
                    "SpaceStorageSettings": {"EbsStorageSettings": {"EbsVolumeSizeInGb": volume}},
                },
                "SpaceSharingSettingsSummary": {"SharingType": "Private"},
                "OwnershipSettingsSummary": {"OwnerUserProfileName": owner},
            }
            for name, owner, volume in spaces
        ]
    }


def _listed_apps(running: Sequence[str], instance_type: str) -> dict[str, object]:
    """``ListApps``, carrying a ``Deleted`` record beside every live one.

    Studio never forgets an app: a space stopped last week still appears, with ``Status``
    ``Deleted``. A fixture that listed only running apps would let a reader that ignores status
    pass, and that reader tells everybody in the domain they are paying for something they
    stopped.
    """
    apps: list[dict[str, object]] = [
        {
            "DomainId": "d-example",
            "SpaceName": "a-space-somebody-stopped",
            "AppType": "JupyterLab",
            "AppName": "default",
            "Status": "Deleted",
            "ResourceSpec": {"InstanceType": instance_type},
        }
    ]
    apps.extend(
        {
            "DomainId": "d-example",
            "SpaceName": space,
            "AppType": "JupyterLab",
            "AppName": "default",
            "Status": "InService",
            "ResourceSpec": {"InstanceType": instance_type},
        }
        for space in running
    )
    return {"Apps": apps}


def write_spec(
    root: Path,
    *,
    workload: str = "olmo-core-train",
    compute: str | None = "gpu-1xa10g",
    command: str = TRAINING_COMMAND,
    fanout: tuple[int, str] | None = None,
) -> Path:
    path = root / ".edullm" / "run.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["schema_version: 1", f"workload_profile: {workload}"]
    if compute is not None:
        lines.append(f"suggested_compute: {compute}")
    lines.append(f"command: {json.dumps(command)}")
    if fanout is not None:
        lines.extend(["fanout:", f"  size: {fanout[0]}", f"  index_parameter: {fanout[1]}"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def config_home(cwd: Path) -> Path:
    """The XDG config home :func:`invoke` gives one run, which is per-test and empty."""
    return cwd / "_no-config-home"


def default_team_path(cwd: Path) -> Path:
    """Where a personal default would live under that config home.

    Composed from the module's own constants rather than spelled here, so renaming the
    directory or the file moves the tests with it rather than leaving them asserting against
    a path nothing reads.
    """
    return config_home(cwd) / PREFERENCES_DIRECTORY / DEFAULT_TEAM_FILE


def write_default_team(cwd: Path, contents: str) -> Path:
    """Put a personal default where this run will find it, exactly as a researcher would.

    Takes the whole file contents rather than a team id, because half of what is worth
    testing here is what the reader does with a file somebody typed by hand: a trailing
    newline, a blank first line, something left on a second line.
    """
    path = default_team_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    return path


#: Every hand-off page a verb asked a browser to show during the last :func:`invoke`.
#:
#: **A LIST RATHER THAN A RETURN VALUE, BECAUSE ALMOST NO CASE CARES AND EVERY CASE IS AT RISK.**
#: ``edullm studio`` and ``edullm console`` end by opening a browser, so without a seam here the
#: suite would launch one per case on the maintainer's laptop -- and, worse, would pass while
#: doing it. Recording rather than merely suppressing is what lets the two cases that *are* about
#: the browser assert that it was reached, and with what.
#:
#: Cleared by :func:`invoke` rather than by a fixture, so a case that calls the CLI twice sees
#: only the second call's pages unless it looks in between. Under ``-n`` each worker is its own
#: process, so there is no sharing to get wrong.
OPENED_PAGES: list[Path] = []


def pages_opened() -> tuple[Path, ...]:
    """What the last :func:`invoke` handed a browser, in the order it handed them over."""
    return tuple(OPENED_PAGES)


def _record_a_page(page: Path) -> bool:
    """Stand in for :func:`~edullm_platform.cli.browser.open_a_browser` and answer success.

    ``True`` because the interesting failure is the other one: a case where the browser could
    not be opened is written by patching this to return ``False``, and a default of ``False``
    would silently send every other case down the fallback path and assert nothing.
    """
    OPENED_PAGES.append(page)
    return True


def invoke(
    argv: list[str],
    *,
    runner: FakeRunner,
    cwd: Path,
    monkeypatch: pytest.MonkeyPatch,
    login: str | None = SUBMITTER,
    config_dir: Path = CONFIG_DIR,
    plugin: bool = True,
    broker: bool = True,
    aws_config: str | None = ONE_BROKER_PROFILE,
    aws_profile: str | None = None,
    ssh: bool = False,
) -> tuple[int, str, str]:
    """Run the CLI as a person would, with both streams captured and no ambient identity.

    ``GH_CONFIG_DIR`` is pointed at an empty directory on every path, including the one
    where a login is declared. Without it a suite run on a laptop with ``gh auth login``
    already done would read that person's login out of their home directory, and the test
    for "nobody is logged in" would pass or fail depending on whose machine it ran on.

    ``XDG_CONFIG_HOME`` is pointed at a second empty directory for the same reason, one layer
    along. The personal default team lives under the config home, so a maintainer who has set
    one for their own submissions would otherwise have every team assertion in this suite
    answer with their preference instead of with the roster. A test that wants a default calls
    :func:`write_default_team` first, which writes it into this same directory.

    ``--config-dir`` goes after the verb because that is where it lives: the root parser
    takes no option carrying a value, which is what lets a first word be read as a verb
    without parsing, and is what lets a retired name be answered with its replacement
    rather than with argparse's list of choices.

    ``config_dir`` is this repository's own ``config/`` unless a test says otherwise, for the
    reason at the top of this module. The override exists for the one question that is about
    the directory rather than about its contents: ``check`` now names which reviewed
    configuration answered, and a case asserting that has to be able to point it somewhere it
    can recognise in the output.

    ``plugin`` and ``broker`` put a Session Manager plugin and a credential broker on PATH, or
    keep one off it, and are the same kind of measure as the two directories above. The lane
    verbs check for both with ``shutil.which``, which reads the developer's own laptop: without
    this, whether the lane cases pass would depend on what that laptop has installed, and the
    cases asserting the two refusals would fail on a laptop that has them. **The owner's laptop
    is exactly such a laptop for the broker**, which makes this the difference between a suite
    that is hermetic and one that passes here and fails everywhere else. PATH is prepended
    rather than replaced where both are wanted, because the runner is a fake and the ``git``
    this suite does not shell out to is still wanted by anything that looks.

    ``aws_config`` is the third of those and it is the one that reaches outside the temporary
    directory if it is left alone. ``resolve_aws_profile`` reads ``~/.aws/config`` unless
    ``AWS_CONFIG_FILE`` says otherwise, so an unset variable points every lane case at the
    developer's real profiles: green on a laptop that has run ``sb-aws-creds install-profiles``,
    red in CI, and both for reasons having nothing to do with the change under test. ``None``
    means no file at all, which is the state of a laptop that has never run the broker's second
    step.

    ``aws_profile`` is the one a person exported for themselves, and it defaults to nothing being
    set rather than to whatever the maintainer running the suite has. Exporting it is what
    reaching the lane took before the resolution existed, so a maintainer who still has it in
    their shell would otherwise send every lane case down the branch that resolves nothing.

    ``ssh`` is the fourth of those and it is opt-in for the same reason the others are opt-out:
    the two browser verbs refuse to open a window over SSH, so whether they open one would
    otherwise depend on how the maintainer happened to be logged in when they ran the suite. The
    variables are cleared on every path and set only here, which makes "this person is remote" a
    condition a case asks for rather than one it inherits.
    """
    # THE PROCESS GOES WHERE THE CALLER SAYS THE PERSON IS STANDING. The header says what one
    # line of this bought and what its absence cost. It is deliberately not conditional: a
    # case that wanted the repository under its feet would be a case testing the CLI in a
    # condition no researcher is ever in.
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("GH_CONFIG_DIR", str(cwd / "_no-gh-config"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home(cwd)))
    tools = cwd / "_tools"
    tools.mkdir(exist_ok=True)
    for wanted, name in ((plugin, SESSION_PLUGIN), (broker, AWS_BROKER)):
        if not wanted:
            continue
        stub = tools / name
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
    if plugin and broker:
        monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
    else:
        # THE WHOLE PATH AND NOT A PREPEND, because a prepend cannot hide a tool that is
        # further along it. Nothing under test shells out for real -- the runner is a fake --
        # so an empty PATH answers the one question this branch is asking.
        monkeypatch.setenv("PATH", str(tools))
    # NAMED WHETHER OR NOT IT EXISTS. Pointing the variable at a path with no file behind it is
    # what makes "this laptop has no profiles" a state a case can ask for; leaving the variable
    # unset would fall back to the developer's own home directory instead.
    written = cwd / "_aws-config"
    if aws_config is None:
        written.unlink(missing_ok=True)
    else:
        written.write_text(aws_config, encoding="utf-8")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(written))
    # NEVER INHERITED. A maintainer with AWS_PROFILE exported -- which is what reaching the lane
    # took before this -- would otherwise take every case down the branch that resolves nothing.
    if aws_profile is None:
        monkeypatch.delenv("AWS_PROFILE", raising=False)
    else:
        monkeypatch.setenv("AWS_PROFILE", aws_profile)
    if login is None:
        monkeypatch.delenv("EDULLM_GITHUB_LOGIN", raising=False)
    else:
        monkeypatch.setenv("EDULLM_GITHUB_LOGIN", login)
    # NO BROWSER IS EVER OPENED BY THIS SUITE, AND NO HAND-OFF FILE IS EVER WRITTEN OUTSIDE THE
    # CASE'S OWN DIRECTORY. Both are the ``PATH`` measure above applied to a third thing that
    # reads the developer's machine. ``open_a_browser`` is replaced by a recorder rather than by
    # a no-op so the two cases that are about the browser have something to assert against, and
    # ``tempfile.tempdir`` is redirected rather than the writer being stubbed, so the real
    # ``write_handoff`` and the real sweep are what run -- a stub there would test nothing and
    # would leave the one file in this design that carries a credential unexercised.
    #
    # THE SSH VARIABLES ARE CLEARED FOR THE REASON ``AWS_PROFILE`` IS. ``no_browser_here`` reads
    # them, so a maintainer running the suite inside an SSH session would take every one of these
    # cases down the printing branch and would see two failures nobody else can reproduce.
    for named in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
        monkeypatch.delenv(named, raising=False)
    if ssh:
        monkeypatch.setenv("SSH_CONNECTION", "10.0.0.1 52814 10.0.0.2 22")
    handoffs = cwd / "_handoffs"
    handoffs.mkdir(exist_ok=True)
    monkeypatch.setattr(tempfile, "tempdir", str(handoffs))
    OPENED_PAGES.clear()
    monkeypatch.setattr(main_module, "open_a_browser", _record_a_page)
    monkeypatch.setattr(main_module, "signin_token", lambda url: CONSOLE_SIGNIN_TOKEN)
    out, err = io.StringIO(), io.StringIO()
    verb, *rest = argv
    code = main(
        [verb, "--config-dir", str(config_dir), *rest],
        runner=runner,
        out=out,
        err=err,
        cwd=cwd,
    )
    return code, out.getvalue(), err.getvalue()
