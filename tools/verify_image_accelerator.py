"""Refuse to publish an image whose torch cannot reach a card.

THE FAILURE THIS EXISTS FOR IS THE ONLY EXPENSIVE SILENT ONE IN THE BUILD PATH. A CPU
build and a CUDA build of torch carry the same version number, import the same way, and
run the same code. A job given the CPU build starts, logs its steps, writes its
checkpoints and finishes -- on the GPU it was billed for and never touched. Nothing in the
run says so. Every other way an image can be wrong announces itself in the first seconds.

WHY IT IS HERE AND NOT IN A DOCKERFILE. Three of the registered repositories already
assert ``torch.version.cuda`` in their own Dockerfile and it works, but a Dockerfile
assertion protects exactly the images built from that Dockerfile: it does not survive a
fork that rewrites the install block, it is absent from a repository registered tomorrow
by somebody who never read this one, and a BuildKit cache hit skips the layer it lives in
without re-running it. This runs on the assembled image, after the last layer and before
the push, so none of those three reach it.

WHY IT NEEDS NO PER-REPOSITORY DECLARATION, WHICH WAS THE ALTERNATIVE. A registry field
saying "this image runs on a GPU" would let the platform demand torch, and would also let
the platform be wrong about a repository nobody here maintains. It is not needed: there is
no registered repository for which a CPU-only build of torch is the right answer. Some
publish no torch at all -- a dataset validator, an evaluation image that calls a hosted
API -- and those are accepted untouched. What is refused is the one state that is wrong
everywhere: torch present, and built without a GPU runtime.

An image that cannot answer is refused with the ones that answer wrongly, which is the
same posture ``verify_published_image`` takes: this gate says "shown to be able to reach a
card" or it stops.

Like its siblings the first line it prints is a machine-readable reason and nothing it
prints is derived from the image it ran: the runner log is world readable for any public
caller repository, and the image reference names the account.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from typing import Final

#: Tried in order against ``docker run --entrypoint``, which resolves each against the
#: image's own PATH -- so a project whose interpreter lives in a virtualenv is found by
#: the same name a container command would use.
INTERPRETERS: Final = ("python", "python3")

#: Prefixed rather than bare so that anything the image prints on the way up -- a
#: sitecustomize, a deprecation warning routed to stdout, a banner from the base -- cannot
#: be mistaken for the answer.
SENTINEL: Final = "EDULLM_ACCELERATOR_PROBE"

#: Runs inside the image. Stdlib only, no network, no file it writes, and every failure
#: reported as a type name: an exception message from somebody else's image could carry a
#: path or a token into a world-readable log.
PROBE: Final = f"""
import json, sys

report = {{"probe": "accelerator", "version": 1}}
try:
    import torch
except ModuleNotFoundError as exc:
    # A missing `torch` is a different fact from a torch whose extension modules did not
    # load. The second reads as an ordinary ImportError and would otherwise be accepted as
    # "this image has no torch".
    if exc.name == "torch":
        report["torch"] = None
    else:
        report["torch"] = "unimportable"
        report["error"] = "ModuleNotFoundError"
except BaseException as exc:
    report["torch"] = "unimportable"
    report["error"] = type(exc).__name__
else:
    version = getattr(torch, "version", None)
    report["torch"] = getattr(torch, "__version__", "")
    report["cuda"] = getattr(version, "cuda", None)
    report["hip"] = getattr(version, "hip", None)

sys.stdout.write("{SENTINEL} " + json.dumps(report) + chr(10))
"""

#: Cold, `import torch` reads roughly a gigabyte of shared objects off a runner disk. The
#: bound is generous because the cost of being slightly too tight is a red build on a
#: correct image, and the cost of being loose is seconds on a job that already spent
#: minutes building.
PROBE_TIMEOUT_SECONDS: Final = 600

REJECTION_GUIDANCE: Final = {
    "cpu_only_torch": (
        "torch imported and reported no CUDA and no ROCm runtime, which is the CPU-only "
        "build. It is the same version number and the same API as the GPU build, so a run "
        "on this image would train to completion on the CPU of a GPU instance without one "
        "log line saying so. An exact version pin does not prevent this: under PEP 440 "
        "the local version 2.9.0+cpu satisfies ==2.9.0, so a pin and a version assertion "
        "both admit it and only torch.version.cuda separates the two. Look for an index "
        "URL serving the cpu channel, a lockfile regenerated on a machine without CUDA, a "
        "later install in the same layer that moved torch, or a build on an arm64 runner, "
        "where the wheel PyPI serves for torch is CPU-only."
    ),
    "torch_unimportable": (
        "torch is installed in this image and importing it raised. That is not the silent "
        "failure this gate is for, but an image whose torch does not load cannot be shown "
        "to reach a card either, and it would fail on a paid instance rather than here."
    ),
    "image_probe_unanswered": (
        "No interpreter in this image answered on stdout. Every registered repository "
        "publishes a Python image and this gate runs the same probe a container command "
        "would run, so an image that cannot answer it is an image whose contents cannot be "
        "established -- which reads the same here as an image that answered wrongly."
    ),
}

__all__ = [
    "INTERPRETERS",
    "PROBE",
    "PROBE_TIMEOUT_SECONDS",
    "REJECTION_GUIDANCE",
    "SENTINEL",
    "AcceleratorError",
    "build_parser",
    "main",
    "probe_command",
    "read_report",
    "require_reachable_accelerator",
]


class AcceleratorError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)

    @property
    def guidance(self) -> str | None:
        return REJECTION_GUIDANCE.get(self.reason)


def probe_command(image_reference: str, interpreter: str) -> list[str]:
    """The container this gate runs, which holds no credential and reaches no network.

    ``--network none`` because the probe imports one module and an image is free to have
    opinions about what to do on the way up. ``--entrypoint`` rather than a command,
    because the base image's entrypoint is inherited deliberately by these Dockerfiles and
    would otherwise swallow the arguments.
    """
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--entrypoint",
        interpreter,
        image_reference,
        "-c",
        PROBE,
    ]


def read_report(stdout: str) -> dict[str, object] | None:
    """The probe's answer, or None if this interpreter did not give one.

    The last sentinel line wins. A repeated sentinel would mean something in the image
    echoed one, and the probe's own line is written after every import has run.
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


def require_reachable_accelerator(report: dict[str, object]) -> str:
    """Raise unless this image is either free of torch or carrying a GPU build of it.

    Returns the accepted state so the caller can say which one it was. ROCm counts: no
    registered repository builds for it today, but a ROCm wheel reports ``version.hip``
    and leaves ``version.cuda`` None, so treating cuda as the only answer would refuse a
    working GPU image for being the wrong vendor.
    """
    torch_version = report.get("torch")
    if torch_version is None:
        return "torch_absent"
    if torch_version == "unimportable":
        raise AcceleratorError("torch_unimportable")
    if not isinstance(torch_version, str):
        raise AcceleratorError("image_probe_unanswered")

    cuda = report.get("cuda")
    if isinstance(cuda, str) and cuda:
        return "cuda"
    hip = report.get("hip")
    if isinstance(hip, str) and hip:
        return "rocm"
    raise AcceleratorError("cpu_only_torch")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-reference", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)

    report: dict[str, object] | None = None
    for interpreter in INTERPRETERS:
        try:
            completed = subprocess.run(
                probe_command(arguments.image_reference, interpreter),
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            print("image_probe_timed_out", file=sys.stderr)
            return 1
        except OSError:
            # docker itself is not runnable. That is the runner being broken rather than
            # the image being wrong, so it is a 2 like every other tooling failure here.
            print("docker_unavailable", file=sys.stderr)
            return 2
        # An interpreter that is not in the image exits before the probe runs and writes
        # nothing to stdout, so the next candidate is tried. Neither stream is printed:
        # both are the image's, and this log is world readable.
        report = read_report(completed.stdout)
        if report is not None:
            break

    if report is None:
        print("image_probe_unanswered", file=sys.stderr)
        guidance = REJECTION_GUIDANCE["image_probe_unanswered"]
        print(guidance, file=sys.stderr)
        return 1

    try:
        accepted = require_reachable_accelerator(report)
    except AcceleratorError as exc:
        print(exc.reason, file=sys.stderr)
        if exc.guidance is not None:
            print(exc.guidance, file=sys.stderr)
        return 1

    # The accepted state is named because "passed" and "there was no torch to check" are
    # different facts about the image, and only one of them means the check did anything.
    print(f"accelerator_verified:{accepted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
