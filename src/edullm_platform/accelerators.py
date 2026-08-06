"""What card a compute profile puts under a run, and how much memory is on it.

**THE FIGURE THIS SUPPLIES DID NOT EXIST ANYWHERE A PROGRAM COULD READ.** The card behind an
instance type lived in the prose of ``guides/olmo-core.md``, in four comments across
``config/`` and ``infra/``, and in one hand-written dict inside ``tests/test_capacity.py``.
Three of the failures analysed in the week to 2026-08-06 were CUDA out-of-memory, and the
platform had nothing to say about any of them beforehand -- not because the check was hard, but
because the number it needed was not written down.

``config/accelerators.yaml`` is that number, measured with ``describe-instance-types`` against
this account rather than copied from a specification sheet, and this is what reads it.

**IT ANSWERS ONE HALF OF A QUESTION AND DELIBERATELY DOES NOT PRETEND TO THE OTHER.** Whether a
model fits on a shape needs the shape's memory and the model's size. This module supplies the
first exactly. Nothing on the submission path supplies the second:
``schemas/submission-inputs.schema.json`` carries fifteen properties and no model size,
:class:`~edullm_platform.contracts.manifest.RunManifest` carries none either, and the only
integer parameter count anywhere in the tree is in
:mod:`edullm_platform.phase4_evidence`, recorded from a run that has already finished. So there
is no fit refusal here and no fit warning, and that is a decision with a tripwire under it
rather than a gap: ``tests/test_accelerators.py`` fails on the day a submission can name a size,
which is the day the arithmetic stops being a guess about somebody's command line.

**WHAT WOULD BE WRONG WITH BUILDING IT ANYWAY.** The only place a size appears today is the text
of a command, and only for one of the six registered repositories -- ``--model-factory
olmo2_1B`` is OLMo-core's spelling and nothing else uses it. The platform's own documented
training command, ``python .edullm/train_on_corpus.py "$EDULLM_RUN_ID"``, names no model at all
and trains a 190M one. A refusal reading that string would therefore fire on almost no real
submission, which is the check-that-cannot-fail this repository has found twelve instances of in
three days; and widened until it did fire, it would be refusing runs on a parameter count it
inferred. :mod:`edullm_platform.precision` is the counter-example that shows what the bar is:
it refuses only where it can quote the exact word of the command that asks for the thing the
hardware cannot do, and there is no equivalent word for "how big is your model".

**MIB THROUGHOUT, WHICH IS THE UNIT THE MEASUREMENT CAME IN AND THE ONE THE MISTAKE HIDES IN.**
The A10G and the L4 are both sold as 24 GB cards and both report 22,888 MiB. Those agree --
24 GB in decimal bytes is 22.35 GiB -- but a recipe annotated "fits in 24 GB" and sized against
24 GiB is over the card by 1.65 GiB on six of this platform's seventeen shapes. Rendering GB
here would put the figure that hides that back in front of the person choosing.
:mod:`edullm_platform.execution` already sizes containers in MiB and ``nvidia-smi`` prints MiB,
so this is also the unit the rest of the path speaks.

Read with ``yaml`` rather than through a contract model, which is the choice
:mod:`edullm_platform.placement` made and recorded for ``config/capacity.yaml``: these fields
belong on ``ComputeProfile`` beside ``provisioned``, and they are not there because that model
forbids extra fields and its structural digest is in ``fixtures/goldens/contract-models.json``.
``ComputeProfile`` is also inside the admission Lambda's import closure, so putting them there
would make a Lambda release out of a fact a renderer reads. Nothing here is packaged into either
zip.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml

from edullm_platform.config import SafeUniqueKeyLoader

__all__ = [
    "ACCELERATORS_FILENAME",
    "AcceleratorRecord",
    "UnreadableAcceleratorsError",
    "device_said",
    "memory_said",
    "read_accelerators",
    "record_for",
]

#: Where the answers live, relative to the reviewed configuration directory.
ACCELERATORS_FILENAME: Final = "accelerators.yaml"


class UnreadableAcceleratorsError(ValueError):
    """``config/accelerators.yaml`` is not a document this can act on.

    Raised rather than defaulted to an empty table, for the reason
    :class:`~edullm_platform.placement.UnreadableCapacityError` gives about its own file: the
    default that fails silently is the one that leaves every caller reading exactly as it did
    before this module existed. A renderer that quietly dropped two columns would look like a
    renderer nobody had got round to extending.
    """


@dataclass(frozen=True)
class AcceleratorRecord:
    """One profile's card, its device count, and the memory on each device in MiB.

    ``memory_mib_total`` is stored rather than computed from the two fields beside it, and
    :func:`read_accelerators` then checks that they agree. That is not redundancy: the reviewed
    file records ``GpuInfo.Gpus[].MemoryInfo.SizeInMiB`` and ``GpuInfo.TotalGpuMemoryInMiB``,
    which are two separate fields of the same API response, so a figure mistyped into either
    one shows up as an inconsistency rather than as a wrong number nothing can see. Deriving
    the total here would make the file's own copy unverifiable and would be a check that
    cannot fail.

    ``device`` is ``None`` for exactly the profiles with no accelerator, which is one of the
    seventeen. It is not an "unknown" -- every priced profile appears in the file, and a shape
    whose card could not be established would be an unreadable file rather than a null here.
    """

    profile: str
    device: str | None
    devices: int
    memory_mib_per_device: int
    memory_mib_total: int


def _whole_number(entry: dict[str, object], field: str, *, path: Path) -> int:
    """One non-negative integer field, with ``bool`` refused rather than counted as 0 or 1.

    ``isinstance(True, int)`` is true in Python, so a ``devices: yes`` that YAML parsed as a
    boolean would otherwise be read as one device -- a plausible-looking count for a shape
    whose real one nobody wrote down.
    """
    value = entry.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise UnreadableAcceleratorsError(
            f"{path} gives {entry.get('profile')!r} a {field} of {value!r}, which is not a "
            "count of anything"
        )
    return value


def read_accelerators(path: Path) -> tuple[AcceleratorRecord, ...]:
    """Read the recorded cards and memory, refusing anything that is not a measurement.

    ``SafeUniqueKeyLoader`` rather than ``yaml.safe_load``, so two entries for one profile is
    an error here as it is for every other reviewed file. Two memory figures for one shape
    would otherwise resolve to whichever was written second, and a reviewer reading the diff
    would see two plausible rows rather than a conflict.

    Three cross-field rules, and each of them exists because the failure it prevents is one a
    reader could not see:

    ``devices * memory_mib_per_device == memory_mib_total`` holds the file's two independently
    transcribed API fields against each other, which is the whole reason both are stored.

    A card is named exactly when there is a card. A ``device`` on a zero-device row is a name
    for a thing that is not there; a row with devices and no name cannot say what a submitter
    would be running on. It is the same failure guarded from both sides, the way
    :func:`~edullm_platform.placement.read_capacity` guards ``wait``.

    A device with zero memory is refused outright. That is what an absent
    ``MemoryInfo.SizeInMiB`` would transcribe to, and zero is the one wrong value that would
    make every fit comparison built on this file answer "does not fit" rather than fail.
    """
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=SafeUniqueKeyLoader)
    if not isinstance(document, dict):
        raise UnreadableAcceleratorsError(f"{path} is not a top-level mapping")
    entries = document.get("profiles")
    if not isinstance(entries, list):
        raise UnreadableAcceleratorsError(f"{path} lists no profiles")

    records: list[AcceleratorRecord] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise UnreadableAcceleratorsError(f"{path} holds an entry that is not a mapping")
        profile = entry.get("profile")
        if not isinstance(profile, str) or not profile.strip():
            raise UnreadableAcceleratorsError(
                f"{path} holds an entry that names no profile: {entry!r}"
            )
        devices = _whole_number(entry, "devices", path=path)
        per_device = _whole_number(entry, "memory_mib_per_device", path=path)
        total = _whole_number(entry, "memory_mib_total", path=path)
        device = entry.get("device")

        if devices * per_device != total:
            raise UnreadableAcceleratorsError(
                f"{path} records {profile!r} as {devices} x {per_device} MiB and a total of "
                f"{total} MiB, which is {devices * per_device}. These are two separate fields "
                "of one describe-instance-types response and they have to agree"
            )
        if devices == 0:
            if device is not None:
                raise UnreadableAcceleratorsError(
                    f"{path} gives {profile!r} no devices and names {device!r} as its card, "
                    "which is a card that is not there"
                )
        else:
            if not isinstance(device, str) or not device.strip():
                raise UnreadableAcceleratorsError(
                    f"{path} records {devices} devices for {profile!r} and does not say what "
                    "they are, so nothing can tell a submitter what they would be running on"
                )
            if per_device == 0:
                raise UnreadableAcceleratorsError(
                    f"{path} records {profile!r} as carrying {devices} x {device} with no "
                    "memory on them, which is what an unread MemoryInfo transcribes to rather "
                    "than a measurement"
                )
        records.append(
            AcceleratorRecord(
                profile=profile,
                device=device,
                devices=devices,
                memory_mib_per_device=per_device,
                memory_mib_total=total,
            )
        )
    return tuple(records)


def record_for(
    profile: str, *, accelerators: Sequence[AcceleratorRecord]
) -> AcceleratorRecord | None:
    """This profile's row, or ``None`` when the file has no answer for it.

    ``None`` means the file is missing a row rather than that the shape has no card, which is
    the distinction ``devices: 0`` exists to keep readable. A caller that treated the two the
    same would print "no accelerator" over a newly promoted GPU shape.
    """
    return next((record for record in accelerators if record.profile == profile), None)


#: What a row with no accelerator says in both columns. Said as a word rather than left blank,
#: because an empty cell in a generated table reads as a figure the tool failed to find, and
#: this one is a measurement: ``c7i.8xlarge`` returns no ``GpuInfo`` at all.
_NO_ACCELERATOR: Final = "none"


def device_said(record: AcceleratorRecord) -> str:
    """``8 x L40S``, in the spelling ``guides/olmo-core.md`` used when a person maintained it.

    The count is repeated here even though the profile name already carries it, because the
    name is a slug a reader parses and this is the column they read. ``gpu-8xl40s`` and
    ``8 x L40S`` disagreeing would be caught by ``tests/test_accelerators.py``, which checks
    the name against the measurement rather than trusting either.
    """
    if record.device is None:
        return _NO_ACCELERATOR
    return f"{record.devices} x {record.device}"


def memory_said(record: AcceleratorRecord) -> str:
    """``366,208 MiB``: the total across the shape's devices, grouped for reading.

    The total rather than the per-device figure, because the column answers "how much memory
    does this machine have" and a per-device number invites the reader to do a multiplication
    the table could have done. The per-device figure is the one that decides whether an
    unsharded tensor fits, and it is on :class:`AcceleratorRecord` for a caller that needs it.

    MiB and not GB, for the reason this module's header gives at length: the rounding to
    "24 GB" is where 1.65 GiB of an A10G goes missing.
    """
    if record.device is None:
        return _NO_ACCELERATOR
    return f"{record.memory_mib_total:,} MiB"
