"""Run the repository's own assertion about its image, inside that image, before the push.

THE FAILURE THIS EXISTS FOR COSTS A BILLED ALLOCATION TO DISCOVER AND IS INVISIBLE
EVERYWHERE ELSE. ``run_019fde30-1d27-7096-8bd9-3ef9b7748d7b`` waited four and a half minutes
for a ``gpu-1xa10g``, started, and died eleven seconds later: every ``olmo3_*`` and
``olmo2_*`` factory in OLMo-core's ``TransformerConfig`` hardcodes ``attn_backend=flash_2``,
``Attention.__init__`` calls ``assert_supported()`` while the model is being *constructed*,
and the registered image installs ``.[wandb]`` plus torch, boto3 and edullm-data -- flash-attn
reaches that project only through an unused ``fa4`` extra, and the registered base is a bare
python image with no nvcc to build one. So the model could not be instantiated at all, every
researcher picking ``olmo-core-train`` was going to buy the same eleven seconds, and nothing
between the commit and the machine had any opinion about it.

WHY THE PLATFORM SUPPLIES THE PLACE AND THE REPOSITORY SUPPLIES THE QUESTION. Its sibling
``verify_image_accelerator`` needs no per-repository declaration because it asks the one
question that has the same right answer everywhere: torch present and built without a GPU
runtime is wrong in every registered image, so the platform can ask it unprompted. There is
no such universal spelling of "this image can construct the models it exists to train". The
factory names, the constructor arguments and the list of sizes worth asserting on are facts
about a codebase nobody here maintains, and a platform that guessed at them would be red on a
correct image the first time a repository renamed a factory.

So this runs a program the repository wrote, at a conventional path, and judges it by its
exit status alone. The platform contributes the four properties that make the answer worth
having and that a repository cannot give itself: it runs on the *assembled* image rather than
in a layer, after the last instruction and before the push, with no network, and on every
build.

IN THE REUSABLE WORKFLOW FOR EVERY REGISTERED REPOSITORY, DECLARED BY THE FILE'S PRESENCE
RATHER THAN BY A REGISTRY FIELD. A field in ``config/repositories.yaml`` saying "this one has
a self-check" is a second place to keep true, it is reviewed here and edited by somebody who
is not the person writing the check, and it goes stale in the direction that fails open --
the file lands, the field does not, and the build goes on green. The file travels in the same
commit as the code it asserts about, so it cannot disagree with it. What the presence rule
costs is that a repository with no check is not refused, which is the same posture
``verify_image_accelerator`` takes toward an image with no torch: the state is *named* on
every build rather than passed over in silence, so "this repository asserts nothing about its
image" is a line in the log rather than an absence nobody can see.

IT CANNOT ASK FOR A GPU, AND THAT IS A CONSTRAINT ON WHAT MAY BE WRITTEN HERE RATHER THAN A
LIMITATION TO WORK AROUND. The container is started with no ``--gpus``, on an
``ubuntu-latest`` runner that has no device to give it. For the failure above that costs
nothing: flash-attn is not installed, so ``assert_supported()`` raises an ``ImportError``
with no device anywhere in the question, and the whole finding is reachable for free. It
stays free in the direction that passes, too -- a flash-attn that *is* installed imports a
compiled extension linked against the CUDA runtime in the torch wheel, and loading it needs
neither a driver nor a card, because what needs a card is launching a kernel and constructing
a module does not launch one. Constructing on ``init_device="meta"`` keeps the memory free
with it.

What is genuinely out of reach is a backend whose support test reads the device -- a
capability check for a Hopper-only kernel is the real example. A check that needs one must
not be written here, because it would go red on every build of a correct image. The nearest
workable place for it is the one this platform already has: a ``*-check`` workload profile,
one hour and one attempt on the smallest GPU shape, which is what ``olmo-core-check`` and
``edullm-alt-cl-check`` are. That is about fifty cents and a wait, against a training
allocation, and it is the right trade for the residue rather than for the whole question.

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

#: Runs inside the image and decides nothing: it executes the repository's file and reports
#: how that ended. Stdlib only. The failure is reported as a type name rather than as a
#: message because the message is printed separately and deliberately, and folding the two
#: together would put an arbitrary string inside the JSON line this tool parses.
PROBE: Final = f"""
import json, runpy, sys, traceback

script = {MOUNT_POINT!r} + "/" + {SELF_CHECK_PATH.rsplit("/", 1)[-1]!r}
# The mounted directory rather than the runner's cwd, so a check split across two files in
# `.edullm/` imports its own sibling the way it would when run from a checkout.
sys.path.insert(0, {MOUNT_POINT!r})

report = {{"probe": "self_check", "version": 1}}
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

#: Cold, this pays for `import torch` off a runner disk before the check has done anything,
#: and then for constructing whatever the repository asked for. Generous for the reason the
#: accelerator probe's bound is: too tight is a red build on a correct image, and too loose
#: costs seconds on a job that already spent minutes building.
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
    "PROBE",
    "PROBE_TIMEOUT_SECONDS",
    "REJECTION_GUIDANCE",
    "SELF_CHECK_PATH",
    "SENTINEL",
    "SelfCheckError",
    "build_parser",
    "main",
    "probe_command",
    "read_report",
    "require_the_check_passed",
    "self_check_directory",
]


class SelfCheckError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    @property
    def guidance(self) -> str | None:
        return REJECTION_GUIDANCE.get(self.reason)


def self_check_directory(repository_root: Path) -> Path | None:
    """The directory to mount, or None where this repository asserts nothing.

    The file is required rather than the directory: every registered repository has an
    ``.edullm/`` because that is where its Dockerfile lives, so mounting on the strength of
    the directory would run a check that is not there.
    """
    script = repository_root / SELF_CHECK_PATH
    return script.parent if script.is_file() else None


def probe_command(image_reference: str, interpreter: str, mounted: Path) -> list[str]:
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
        PROBE,
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


def require_the_check_passed(report: dict[str, object]) -> None:
    """Raise unless the repository's own check ran to a pass.

    An outcome this tool does not recognise is refused with the ones that failed. The probe
    is the platform's own program and writes one of three words, so a fourth means the
    sentinel came from something else in the image -- which establishes nothing, and
    establishing nothing is what this gate refuses.
    """
    outcome = report.get("outcome")
    if outcome == "passed":
        return
    if outcome == "refused":
        raise SelfCheckError("self_check_refused")
    if outcome == "raised":
        raise SelfCheckError("self_check_raised")
    raise SelfCheckError("self_check_unanswered")


def _tail(*streams: str) -> str:
    """The end of what the check said, which is where a traceback keeps its cause.

    The probe's own sentinel line is dropped. It is this tool talking to itself, it is the
    last thing on stdout, and leaving it in would put the machine-readable verdict in the
    middle of the human-readable one.
    """
    lines = [
        line
        for stream in streams
        for line in stream.splitlines()
        if not line.startswith(f"{SENTINEL} ")
    ]
    return "\n".join(lines[-FAILURE_OUTPUT_LINES:])


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


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    mounted = self_check_directory(arguments.repository_root)
    if mounted is None:
        # Named rather than silent. "This repository asserts nothing about its image" is a
        # fact worth being able to read off a build, and the alternative is an absence that
        # looks identical to a check that passed.
        print(f"self_check_verified:absent:{SELF_CHECK_PATH}")
        return 0

    report: dict[str, object] | None = None
    streams = ("", "")
    for interpreter in INTERPRETERS:
        try:
            completed = subprocess.run(
                probe_command(arguments.image_reference, interpreter, mounted),
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
            streams = (completed.stdout, completed.stderr)
            break

    if report is None:
        print("self_check_unanswered", file=sys.stderr)
        print(REJECTION_GUIDANCE["self_check_unanswered"], file=sys.stderr)
        return 1

    try:
        require_the_check_passed(report)
    except SelfCheckError as exc:
        print(exc.reason, file=sys.stderr)
        # The check's own words first and this tool's second, so the reader meets the cause
        # before the explanation of what kind of thing the cause is.
        said = _tail(*streams)
        if said:
            print(said, file=sys.stderr)
        if exc.guidance is not None:
            print(exc.guidance, file=sys.stderr)
        return 1

    print(f"self_check_verified:passed:{SELF_CHECK_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
