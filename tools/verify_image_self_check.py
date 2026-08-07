"""Run the assertion a repository makes about its image, inside that image, twice.

THE FAILURE THIS EXISTS FOR COSTS A BILLED ALLOCATION TO DISCOVER AND IS INVISIBLE
EVERYWHERE ELSE. ``run_019fde30-1d27-7096-8bd9-3ef9b7748d7b`` waited four and a half minutes
for a ``gpu-1xa10g``, started, and died eleven seconds later. All fourteen ``olmo3_*``
factories in OLMo-core hardcode ``attn_backend=flash_2``; the registered image installs
``.[wandb]`` plus torch, boto3 and edullm-data, flash-attn reaches that project only through
an unused ``fa4`` extra, and the registered base is a bare python image with no nvcc to build
one. So no olmo3 model could be instantiated on a GPU at all, every researcher picking
``olmo-core-train`` was going to buy the same eleven seconds, and nothing between the commit
and the machine had an opinion about it.

WHY THE PLATFORM SUPPLIES THE PLACE AND THE REPOSITORY SUPPLIES THE QUESTION. Its sibling
``verify_image_accelerator`` needs no per-repository declaration because it asks the one
question that has the same right answer everywhere: torch present and built without a GPU
runtime is wrong in every registered image, so the platform can ask it unprompted. There is
no such universal spelling of "this image carries the backends its own configs name". The
backend names, the factories that set them and the modules they resolve to are facts about a
codebase nobody here maintains, and a platform that guessed at them would be red on a correct
image the first time a repository renamed something.

So this runs a program the repository wrote, at a conventional path, and judges it by its
exit status. The platform contributes what a repository cannot give itself: the *assembled*
image rather than a layer, after the last instruction and before the push, with no network,
on every build -- and the differential below, which is the only part of this that is a
judgement about the check rather than about the image.

THE DIFFERENTIAL, WHICH IS WHY THIS RUNS THE CHECK TWICE AND IS THE WHOLE OF WHAT WAS LEARNED
THE HARD WAY. The obvious check to write here is the one that constructs a model config per
registered size and calls the build a failure if it raises. It passes vacuously, and it
passed vacuously in the account. ``Attention.__init__`` opens with

    if not torch.cuda.is_available() and backend != AttentionBackendName.torch:
        warnings.warn(...)
        backend = AttentionBackendName.torch

*before* it calls ``assert_supported()``. A builder has no card, so on a builder every
``olmo3_*`` config quietly downgrades to the torch backend and the question of whether
flash-attn is installed is never asked. OLMo-core's own build assertion constructs
``olmo2_190M`` and has been green throughout, on an image where every olmo3 factory is
unconstructable on the machine that matters. A green like that is worse than no check: it
asserts, in a log, exactly the property it cannot see.

Nothing the platform knows about flash-attn would have caught that, and nothing it could
learn about flash-attn would catch the next one. What is general is the *shape*: a library
that degrades quietly when no device is present turns a build-time assertion into a
statement about a code path the GPU machine will never take, and it does so through one
switch, ``torch.cuda.is_available``. So the check is run a second time in the same image with
that function returning True, and the two answers are compared. A check whose verdict depends
on which run it was is a check whose green means nothing here, and it is refused with the
ones that failed.

This catches the vacuous check without knowing what it asserts. Run the construct-every-size
check above under the second pass and the downgrade branch is skipped, ``assert_supported()``
is reached, flash-attn is absent, and it raises -- so the platform reports that the check is
device-conditional, having been told nothing about attention backends by anybody.

WHAT THE DIFFERENTIAL DOES NOT ESTABLISH, WHICH IS MOST OF WHAT SOMEBODY WOULD WANT.
:data:`PRETENDED_DEVICE_LIMIT` carries the whole of it. In short: it enforces an invariant on
the check rather than supplying one, it covers one spelling of the degradation rather than
the idea of it, and a repository that asserts nothing is still asserting nothing. The
property being asked about lives inside somebody else's image and somebody else's config
code, and this is the most the platform side can honestly hold.

IT CANNOT ASK FOR A GPU, AND DOES NOT NEED ONE. The container is started with no ``--gpus``,
on a runner that has no device to give it. The correct assertion is affordable there:
``AttentionBackendName.flash_2.assert_supported()`` is ``has_flash_attn_2()``, a test against
a module-level import with no device call anywhere in the path, and neither
``flash_attn_2_cuda`` nor torch 2.9.0's ``libtorch_cuda.so`` declares ``libcuda.so.1`` in its
ELF dynamic section -- so the extension loads from a pip install on a driverless builder.
What is out of reach is an assertion that reads the device itself, a capability test for a
Hopper-only kernel being the real example. That must not be written here; it would go red on
every build of a correct image. Its place is the ``*-check`` workload profile this platform
already has, which is a short run on the smallest GPU shape.

WHAT A FAILING CHECK PRINTS, WHICH IS THE ONE PLACE THIS DIVERGES FROM ITS SIBLING.
``verify_image_accelerator`` prints nothing the image said, because the program it runs is
the platform's and the streams are somebody else's image talking into a world-readable log.
Here the program is the caller's own, committed to the caller's own repository, and its
output is the entire product of the check: a gate that refuses without saying why sends an
author to reproduce a container build in order to read a traceback that already exists. So
the streams are printed on a failure and only on a failure, and the bound on them is about
keeping a log readable rather than about secrecy -- ``docker build`` in the same step already
ran every ``RUN`` instruction in that repository's Dockerfile into the same log.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

#: Where a repository writes its assertion, relative to the repository root. The same path
#: ``edullm-p1`` already uses for the check it runs from its own Dockerfile, so this makes a
#: convention out of a spelling that exists rather than inventing a second one beside it.
SELF_CHECK_PATH: Final = ".edullm/verify_image.py"

#: Where the directory holding it is mounted. Not ``/edullm``, which a research image is
#: free to have already; shadowing one would be a mount that changes what the image is while
#: claiming to inspect it.
MOUNT_POINT: Final = "/edullm-self-check"

#: Tried in order against ``docker run --entrypoint``, which resolves each against the
#: image's own PATH -- so a project whose interpreter lives in a virtualenv is found by the
#: same name a container command would use.
INTERPRETERS: Final = ("python", "python3")

#: Prefixed rather than bare so that anything the image or the check prints -- a banner from
#: the base, a warning routed to stdout, the check's own reporting -- cannot be mistaken for
#: the verdict.
SENTINEL: Final = "EDULLM_SELF_CHECK_PROBE"

#: THE ONE FUNCTION THE SECOND PASS PATCHES, AND THE HONEST BOUND ON WHAT THAT BUYS.
#:
#: ``torch.cuda.is_available`` is the switch the proven degradation reads, and patching it is
#: enough to make ``Attention.__init__`` keep the backend its config named and go on to
#: assert on it. Nothing else is patched, deliberately. ``device_count``, ``current_device``
#: and the rest are reachable spellings of the same idea, and patching them widens the set of
#: correct checks this refuses -- a check that asks how many cards there are and gets a
#: fictional answer will go looking for card zero -- without covering meaningfully more of
#: the pattern that has actually cost money.
#:
#: So the coverage is one spelling rather than the concept. A library that degrades on
#: ``device_count() == 0``, on catching a ``RuntimeError``, or on an environment variable
#: slips through this, and a check written around any of those is as vacuous as the one that
#: prompted it. What the differential buys is not proof: it is that the *cheapest and most
#: obvious wrong check* -- construct the models and see whether it raises, which is what a
#: reasonable person writes first, and what OLMo-core has been running green -- is now
#: refused by name instead of being trusted.
#:
#: Two further things it does not do, worth saying because a gate that looks like more than
#: it is is the failure this whole module is about. It enforces an invariant on the check
#: rather than supplying one, so a check asserting something true and trivial passes both
#: runs and establishes nothing. And a repository with no check at all is not refused; the
#: absent state is named on every build, and naming is the whole of what happens to it.
PRETENDED_DEVICE_LIMIT: Final = "torch.cuda.is_available"


def probe_source(*, pretend_a_device: bool) -> str:
    """The program that runs inside the image, in one of its two passes.

    Decides nothing: it executes the repository's file and reports how that ended. Stdlib
    only. A failure is reported as a type name rather than as a message, because the message
    is printed separately and deliberately and folding the two together would put an
    arbitrary string inside the JSON line this tool parses.
    """
    pretend = (
        f"""
try:
    import torch
except BaseException:
    # An image with no importable torch has no {PRETENDED_DEVICE_LIMIT} to patch, so this
    # pass establishes nothing and says so rather than answering as though it had.
    report["pretended"] = False
else:
    torch.cuda.is_available = lambda: True
    report["pretended"] = True
"""
        if pretend_a_device
        else '\nreport["pretended"] = False\n'
    )
    return f"""
import json, runpy, sys, traceback

script = {MOUNT_POINT!r} + "/" + {SELF_CHECK_PATH.rsplit("/", 1)[-1]!r}
# The mounted directory rather than the runner's cwd, so a check split across two files in
# `.edullm/` imports its own sibling the way it would when run from a checkout.
sys.path.insert(0, {MOUNT_POINT!r})

report = {{"probe": "self_check", "version": 2}}
{pretend}
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit as exit_:
    # A check ending in `sys.exit()` or `raise SystemExit(0)` passed. Anything else it chose
    # to exit with is a refusal it made on purpose, and is not collapsed into the crash
    # branch below: the two read differently to whoever is looking at the log.
    code = exit_.code
    report["outcome"] = "passed" if code in (0, None) else "refused"
    if report["outcome"] == "refused":
        report["exit"] = code if isinstance(code, int) else 1
except BaseException as exc:
    report["outcome"] = "raised"
    report["error"] = type(exc).__name__
    traceback.print_exc()
else:
    report["outcome"] = "passed"

sys.stdout.write("{SENTINEL} " + json.dumps(report) + chr(10))
"""


#: Cold, a pass pays for `import torch` off a runner disk before the check has done anything.
#: Generous for the reason the accelerator probe's bound is: too tight is a red build on a
#: correct image, and too loose costs seconds on a job that already spent minutes building.
#: The second pass is warm, because the first left the layers in the page cache.
PROBE_TIMEOUT_SECONDS: Final = 900

#: How much of a failing check's own output is reproduced. The tail rather than the head,
#: because a traceback ends with the line that raised. This bounds a log rather than
#: withholding anything: the whole of it is reproducible by running the same file against
#: the same image.
FAILURE_OUTPUT_LINES: Final = 200

REJECTION_GUIDANCE: Final = {
    "self_check_refused": (
        f"{SELF_CHECK_PATH} ran in the assembled image and exited non-zero, which is this "
        "repository refusing its own image. Nothing here decided that; the file did. Read "
        "its output above. No image was pushed, so the commit is still publishable once the "
        "cause is fixed."
    ),
    "self_check_raised": (
        f"{SELF_CHECK_PATH} raised in the assembled image. That is the same refusal as an "
        "explicit non-zero exit and is reported separately only because an uncaught "
        "exception usually means the check found what it was looking for rather than that "
        "it decided anything. The traceback is above. If instead the check itself is broken "
        "-- an import of something the image does not carry, a path that exists only on a "
        "laptop -- fix the check: it runs with no network, with no GPU, and with only "
        f"{SELF_CHECK_PATH}'s own directory mounted."
    ),
    "self_check_is_device_conditional": (
        f"{SELF_CHECK_PATH} passed on this builder and failed when the same image was run "
        f"again with {PRETENDED_DEVICE_LIMIT}() returning True. The two answers disagree, so "
        "the green one is about a code path a GPU machine will not take, and publishing on "
        "the strength of it would be asserting in a log exactly the property this check "
        "cannot see. The output above is the second pass. THE USUAL CAUSE IS ASSERTING BY "
        "CONSTRUCTION. A library that degrades quietly without a card -- OLMo-core's "
        "Attention.__init__ warns and replaces the configured attention backend with the "
        "torch one before it ever calls assert_supported() -- turns construct-it-and-see "
        "into a test of the fallback. Assert the property directly instead of inferring it "
        "from a constructor: call the backend's own support test, import the module, ask the "
        "library the question rather than asking it to do the work. That answers the same on "
        "both passes because it does not depend on a device being there, which is what makes "
        "a builder-green worth anything."
    ),
    "self_check_needs_a_device": (
        f"{SELF_CHECK_PATH} failed on this builder and passed when the same image was run "
        f"again with {PRETENDED_DEVICE_LIMIT}() returning True, so this check needs a card "
        "to pass and no builder has one. It would go red on every build of a correct image. "
        "Move whatever needs the device behind a *-check workload profile, which is a short "
        "run on the smallest GPU shape, and leave here only what can be answered without "
        "one -- which is more than it looks: a backend support test, an import, a version "
        "assertion and a compiled extension all load without a driver."
    ),
    "self_check_unanswered": (
        "No interpreter in this image ran the check to a verdict. Every registered "
        "repository publishes a Python image and this runs the same interpreter a container "
        "command would, so an image that cannot answer is an image whose self-check cannot "
        "be established -- which reads here the same as one that answered wrongly. An "
        "entrypoint that swallows its arguments is the usual cause."
    ),
}

__all__ = [
    "FAILURE_OUTPUT_LINES",
    "INTERPRETERS",
    "MOUNT_POINT",
    "PRETENDED_DEVICE_LIMIT",
    "PROBE_TIMEOUT_SECONDS",
    "REJECTION_GUIDANCE",
    "SELF_CHECK_PATH",
    "SENTINEL",
    "Pass",
    "SelfCheckError",
    "build_parser",
    "main",
    "probe_command",
    "probe_source",
    "read_report",
    "reconcile_the_two_passes",
    "self_check_directory",
    "verdict_of",
]


class SelfCheckError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    @property
    def guidance(self) -> str | None:
        return REJECTION_GUIDANCE.get(self.reason)


@dataclass(frozen=True)
class Pass:
    """One run of the check in the image, and what the container said while doing it."""

    report: dict[str, object]
    stdout: str = ""
    stderr: str = ""

    @property
    def pretended_a_device(self) -> bool:
        """Whether this pass actually patched anything.

        False for the honest pass, and false for a pretend pass in an image with no
        importable torch -- where there was nothing to patch, so the pass is a repeat of the
        first rather than a comparison against it.
        """
        return self.report.get("pretended") is True

    def said(self) -> str:
        """The end of what the check printed, which is where a traceback keeps its cause.

        The sentinel line is dropped. It is this tool talking to itself, it is the last thing
        on stdout, and leaving it in would put the machine-readable verdict in the middle of
        the human-readable one.
        """
        lines = [
            line
            for stream in (self.stdout, self.stderr)
            for line in stream.splitlines()
            if not line.startswith(f"{SENTINEL} ")
        ]
        return "\n".join(lines[-FAILURE_OUTPUT_LINES:])


def self_check_directory(repository_root: Path) -> Path | None:
    """The directory to mount, or None where this repository asserts nothing.

    The file is required rather than the directory: every registered repository has an
    ``.edullm/`` because that is where its Dockerfile lives, so mounting on the strength of
    the directory would run a check that is not there.
    """
    script = repository_root / SELF_CHECK_PATH
    return script.parent if script.is_file() else None


def probe_command(
    image_reference: str, interpreter: str, mounted: Path, *, pretend_a_device: bool = False
) -> list[str]:
    """The container this gate runs, which holds no credential and reaches no network.

    ``--network none`` because a build-time assertion that needs the internet is asserting
    something other than what this image can do. No ``--gpus``, which is the constraint the
    module docstring argues rather than an omission. The mount is read-only so that a check
    cannot edit itself, and it carries the checked-out file rather than one baked into the
    image: a Dockerfile is not obliged to ``COPY`` ``.edullm/`` at all, and a check that only
    runs where the image happens to have kept it is a check that silently stops running.

    ``--entrypoint`` rather than a command, because the base image's entrypoint is inherited
    deliberately by these Dockerfiles and would otherwise swallow the arguments.
    """
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--volume",
        f"{mounted}:{MOUNT_POINT}:ro",
        "--entrypoint",
        interpreter,
        image_reference,
        "-c",
        probe_source(pretend_a_device=pretend_a_device),
    ]


def read_report(stdout: str) -> dict[str, object] | None:
    """The probe's verdict, or None if this interpreter did not reach one.

    The last sentinel line wins, for the reason its sibling's does: the probe writes its own
    line after the check has finished, so anything earlier came from the check or the image.
    """
    found: dict[str, object] | None = None
    for line in stdout.splitlines():
        if not line.startswith(f"{SENTINEL} "):
            continue
        try:
            payload = json.loads(line[len(SENTINEL) + 1 :])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            found = payload
    return found


def verdict_of(report: dict[str, object]) -> str | None:
    """The reason this pass failed, or None where it passed.

    An outcome this tool does not recognise fails. The probe is the platform's own program
    and writes one of three words, so a fourth means the sentinel came from something else in
    the image -- which establishes nothing, and establishing nothing is what this refuses.
    """
    outcome = report.get("outcome")
    if outcome == "passed":
        return None
    if outcome == "refused":
        return "self_check_refused"
    if outcome == "raised":
        return "self_check_raised"
    return "self_check_unanswered"


def reconcile_the_two_passes(honest: Pass, pretended: Pass | None) -> tuple[str, Pass]:
    """The verdict, and which pass a reader should be shown to understand it.

    Raises unless the check passed *and* would still have passed on a machine with a card.
    Returns the accepted state and the pass it came from, so the caller can say which of the
    three greens this was rather than printing one word for all of them.

    THE DISAGREEING CASE IS THE POINT AND BOTH DIRECTIONS OF IT MEAN SOMETHING. A check that
    passes only without a device is asserting about the fallback, which is the vacuous check.
    One that passes only with a device cannot run here at all and would redden every correct
    build. Neither is a repository refusing its image, so neither is reported as one.
    """
    honest_verdict = verdict_of(honest.report)
    if pretended is None or not pretended.pretended_a_device:
        # Nothing was patched, so there is no second answer to compare against. An image with
        # no importable torch cannot be carrying the degradation this looks for, and one
        # whose torch does not import has already been refused by verify_image_accelerator.
        if honest_verdict is not None:
            raise SelfCheckError(honest_verdict)
        return "no_device_switch_to_pretend_with", honest

    pretended_verdict = verdict_of(pretended.report)
    if honest_verdict is None and pretended_verdict is None:
        return "device_independent", honest
    if honest_verdict is None:
        raise SelfCheckError("self_check_is_device_conditional")
    if pretended_verdict is None:
        raise SelfCheckError("self_check_needs_a_device")
    # Both failed, which is the ordinary refusal. The honest pass is the one reported,
    # because it is the one that ran against the image as it is.
    raise SelfCheckError(honest_verdict)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-reference", required=True)
    parser.add_argument(
        "--repository-root",
        required=True,
        type=Path,
        help=f"the research checkout, which is where {SELF_CHECK_PATH} is looked for",
    )
    return parser


def _run_pass(
    image_reference: str, mounted: Path, *, pretend_a_device: bool
) -> Pass | None | int:
    """One pass, or a process exit code where the runner rather than the image was the problem.

    Every interpreter is tried before giving up, and only on this pass: an image answers to
    the same name in both, so the second pass does not repeat the search.
    """
    for interpreter in INTERPRETERS:
        try:
            completed = subprocess.run(
                probe_command(
                    image_reference, interpreter, mounted, pretend_a_device=pretend_a_device
                ),
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            print("self_check_timed_out", file=sys.stderr)
            return 1
        except OSError:
            # docker itself is not runnable. That is the runner being broken rather than the
            # image being wrong, so it is a 2 like every other tooling failure here.
            print("docker_unavailable", file=sys.stderr)
            return 2
        # An interpreter that is not in the image exits before the probe runs and writes no
        # sentinel, so the next candidate is tried.
        report = read_report(completed.stdout)
        if report is not None:
            return Pass(report=report, stdout=completed.stdout, stderr=completed.stderr)
    return None


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    mounted = self_check_directory(arguments.repository_root)
    if mounted is None:
        # Named rather than silent. "This repository asserts nothing about its image" is a
        # fact worth being able to read off a build, and the alternative is an absence that
        # looks identical to a check that passed.
        print(f"self_check_verified:absent:{SELF_CHECK_PATH}")
        return 0

    passes: list[Pass] = []
    for pretend_a_device in (False, True):
        outcome = _run_pass(
            arguments.image_reference, mounted, pretend_a_device=pretend_a_device
        )
        if isinstance(outcome, int):
            return outcome
        if outcome is None:
            print("self_check_unanswered", file=sys.stderr)
            print(REJECTION_GUIDANCE["self_check_unanswered"], file=sys.stderr)
            return 1
        passes.append(outcome)

    honest, pretended = passes
    try:
        accepted, _ = reconcile_the_two_passes(honest, pretended)
    except SelfCheckError as exc:
        print(exc.reason, file=sys.stderr)
        # The pass a reader needs is not always the first one: for a device-conditional
        # check the interesting output is the run that disagreed, and printing the passing
        # run instead would show them a green transcript under a red heading.
        shown = pretended if exc.reason == "self_check_is_device_conditional" else honest
        said = shown.said()
        if said:
            print(said, file=sys.stderr)
        if exc.guidance is not None:
            print(exc.guidance, file=sys.stderr)
        return 1

    print(f"self_check_verified:passed:{accepted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
