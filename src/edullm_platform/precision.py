"""Whether the devices a submission is billed for can run the number format it asks for.

bfloat16 is a property of the silicon rather than of the driver or the container. Turing --
the T4 -- has no bfloat16 arithmetic at all; Ampere, Ada Lovelace and Hopper do. A training
command that asks a T4 for bfloat16 is not slow and is not degraded: it dies on the first
kernel that needs the format, after the submission has been compiled, classified, released
by a lead, admitted, and given an instance.

**THE MEASUREMENT ON 2026-08-04 IS WHY THIS IS URGENT RATHER THAN THEORETICAL.**
``config/capacity.yaml`` records that ``gpu-8xt4`` is the only shape above four cards this
account can obtain, and every other placing GPU shape is a single card. So anybody who needs
more than one device is routed onto Turing, which is exactly the population most likely to be
training something large enough to want bfloat16.

**NOTHING BEFORE THE DEVICE SAYS NO, INCLUDING THE OBVIOUS THING TO ASK.**
``torch.cuda.is_bf16_supported()`` returns true on a T4, so a program that checks before
committing to a dtype gets the wrong answer from the only source that ought to know. There is
no earlier signal anywhere on the path, which is what makes a compile-time refusal the only
place this can be caught while it is still cheap.

**WHERE THE CAPABILITY IS RECORDED, AND WHY IT IS NOT A LIST OF SHAPE NAMES.**
:data:`GPUS_BY_INSTANCE_FAMILY` is keyed on the EC2 instance family, which each compute
profile already declares in ``config/workload-catalog.yaml`` as ``instance_type``. So the set
of shapes is the reviewed catalog's and never this module's, and a shape added, renamed or
demoted there is answered here without an edit -- ``gpu-2xt4`` on a ``g4dn`` would be refused
on the day it was priced. A list of profile names in Python is the shape of mistake that put
the submission form's dropdown out of step with the account, and it is the one thing this
must not become.

Two tables rather than one, and the split is what keeps the fact stated once.
:data:`TURING` and its three siblings each say whether a generation has bfloat16, so the
answer is written four times for the whole catalog rather than once per shape; the family map
says which card each instance family carries. Three T4 profiles agreeing that Turing has no
bfloat16 is then a consequence rather than three claims that could disagree.

**WHY NOT ON ``ComputeProfile``, WHERE IT BELONGS.** For the reason ``config/capacity.yaml``
and :mod:`edullm_platform.placement` both record about ``places``: that model's structural
digest is in five committed proof bundles, so a field beside ``provisioned`` is a bundle
regeneration rather than a config edit. ``ComputeProfile`` and
:data:`~edullm_platform.execution.CONTAINER_SHAPES` are also both inside the admission
Lambda's import closure, so either would turn this into a Lambda release for a rule that runs
before anything reaches AWS. Nothing in this module is packaged into either zip. When that
regeneration happens, the family map folds into the catalog and this keeps only the reading
of the command.

**WHAT THIS READS IS THE TEXT OF A COMMAND, AND THAT IS A REAL LIMIT RATHER THAN A CAVEAT.**
:func:`bfloat16_request_in` finds the three ways a submitter writes a bfloat16 request into
argv, and it cannot find bfloat16 that was never written there. The largest miss is the
platform's own documented training command: ``.edullm/train_on_corpus.py`` in the OLMo-core
image constructs its data-parallel config with ``param_dtype=DType.bfloat16`` and exposes no
flag for it, so ``python .edullm/train_on_corpus.py "$EDULLM_RUN_ID"`` is a bfloat16 run
carrying no bfloat16 token. That command on ``gpu-8xt4`` is not refused by this guard and
will fail on the device. The refusal below says which of the two it checked, in as many
words, because a guard that lets a submitter believe it covers more than it does is worse
than one nobody relies on.

**NO WAIVER, WHICH IS WHERE THIS PARTS FROM ITS TWO NEIGHBOURS.**
``EDULLM_LAUNCH_CHECK=waived`` and ``EDULLM_CHECKPOINT_CHECK=waived`` both exist because the
waived run still works and the platform is only declining to assert something about it -- one
process on eight cards is a benchmark, and a run that places its own checkpoints is ordinary.
A waived bfloat16 run on a Turing card does not work. There is nothing for a submitter to
know that the hardware does not already decide, so an escape here would only record a
decision to spend an approval on a job that cannot start. What takes its place is a narrow
detector: a bfloat16 spelling is read as a request only where the key beside it names a
precision setting, so there is no correct command this refuses and no need for a way past it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from .contracts.base import serialize_decimal
from .contracts.workload import ComputeProfile, WorkloadCatalog
from .errors import SubmissionRefusedError
from .launchers import simple_commands

__all__ = [
    "BFLOAT16_SPELLINGS",
    "GPUS_BY_INSTANCE_FAMILY",
    "Gpu",
    "GpuArchitecture",
    "bfloat16_request_in",
    "gpu_of",
    "instance_family",
    "require_bfloat16_only_where_the_hardware_has_it",
]


@dataclass(frozen=True)
class GpuArchitecture:
    """One NVIDIA generation, and the single thing about it this platform has to know.

    ``supports_bfloat16`` is written here rather than per shape so that the catalog's three
    T4 profiles cannot disagree with each other. It is a fact about the silicon: it does not
    change, it is not re-measured, and it is not a property of this account -- which is what
    separates it from ``places`` in ``config/capacity.yaml``, where the answer is a dated
    reading of what EC2 had to sell and belongs in reviewed configuration for that reason.
    """

    name: str
    supports_bfloat16: bool


#: Turing is the whole of the problem. It is the only generation NVIDIA shipped with tensor
#: cores and without bfloat16, so it is the one row here whose flag is false and the reason
#: this module exists at all.
TURING: Final = GpuArchitecture(name="Turing", supports_bfloat16=False)
AMPERE: Final = GpuArchitecture(name="Ampere", supports_bfloat16=True)
ADA_LOVELACE: Final = GpuArchitecture(name="Ada Lovelace", supports_bfloat16=True)
HOPPER: Final = GpuArchitecture(name="Hopper", supports_bfloat16=True)


@dataclass(frozen=True)
class Gpu:
    """The card one EC2 instance family carries, and the generation it belongs to."""

    model: str
    architecture: GpuArchitecture


#: Which card each GPU instance family in ``config/workload-catalog.yaml`` carries.
#:
#: KEYED ON THE FAMILY RATHER THAN ON THE INSTANCE TYPE, because every size of a family
#: carries the same card: g4dn.xlarge, g4dn.12xlarge and g4dn.metal are one, four and eight
#: T4s. Keying on the type would be fifteen rows saying six things and would need an edit for
#: a size the account has never used.
#:
#: ``g6`` AND ``g6e`` ARE DIFFERENT FAMILIES AND DIFFERENT CARDS -- L4 and L40S -- which is
#: the one place a prefix match rather than an exact one would get the wrong answer. They
#: happen to share a generation, so the mistake would cost nothing today and would cost
#: everything the first time two families of one letter parted.
#:
#: A family absent from this map has no answer, and ``tests/test_bfloat16_guard.py`` is what
#: keeps that from being a silent pass: it requires every profile the catalog declares as a
#: GPU to resolve here, so promoting a shape on a family nobody has priced before is a red
#: test rather than a submission this guard quietly stops applying to.
GPUS_BY_INSTANCE_FAMILY: Final[Mapping[str, Gpu]] = {
    "g4dn": Gpu(model="T4", architecture=TURING),
    "g5": Gpu(model="A10G", architecture=AMPERE),
    "g6": Gpu(model="L4", architecture=ADA_LOVELACE),
    "g6e": Gpu(model="L40S", architecture=ADA_LOVELACE),
    "p4d": Gpu(model="A100", architecture=AMPERE),
    "p5": Gpu(model="H100", architecture=HOPPER),
}

#: How bfloat16 is written where a value is expected. Exact matches only, case-folded.
#: ``bf16-ablation`` is an experiment name and ``bf16_layers`` is somebody's module, and a
#: substring test would refuse both.
BFLOAT16_SPELLINGS: Final = frozenset({"bf16", "bfloat16", "torch.bfloat16"})

#: Flags that are the request rather than carrying it: ``--bf16`` in HuggingFace's
#: ``TrainingArguments`` and in DeepSpeed takes no value and means bfloat16 by existing.
_SWITCHES: Final = frozenset({"bf16", "bfloat16"})

#: What makes a key a precision setting. Two substrings rather than a list of exact flag
#: names, because the flag is spelled differently by every trainer that has one --
#: ``--dtype``, ``--torch_dtype``, ``--mixed_precision``, ``param_dtype``,
#: ``--amp-dtype`` -- and a list of the ones anybody had seen would stop covering the fourth
#: registered repository on the day it was registered.
_PRECISION_SETTING_WORDS: Final = ("dtype", "precision")

#: A value that turns a switch off. Read case-folded, so ``--bf16 False`` is somebody saying
#: no rather than somebody asking for bfloat16 with an unusual argument.
_TURNED_OFF: Final = frozenset({"false", "0", "no", "off", "none", "null"})


def instance_family(instance_type: str) -> str:
    """``g4dn.metal`` is ``g4dn``.

    Split rather than pattern-matched. ``INSTANCE_TYPE_PATTERN`` on ``ComputeProfile`` already
    holds an instance type to exactly one dot, so the part in front of it is the family and
    there is nothing here to get wrong.
    """
    return instance_type.split(".", maxsplit=1)[0]


def gpu_of(profile: ComputeProfile) -> Gpu | None:
    """The card this profile's instances carry, or ``None`` if there is no answer here.

    ``None`` means two different things and neither is "it has bfloat16". A CPU profile has
    no card, which is decided by the catalog's own ``accelerator`` field rather than inferred
    from the instance type -- ``c7i`` looking unfamiliar and ``g4dn`` looking familiar is not
    a distinction worth resting on. A GPU profile on a family this map does not carry has an
    answer nobody has written down, and the caller below declines to refuse on it: a guess in
    either direction is worse than the test that makes the case unreachable in a shipped
    catalog.
    """
    if profile.accelerator != "gpu":
        return None
    return GPUS_BY_INSTANCE_FAMILY.get(instance_family(profile.instance_type))


def require_bfloat16_only_where_the_hardware_has_it(
    *,
    command: Sequence[str],
    compute_profile: str,
    catalog: WorkloadCatalog,
) -> None:
    """Refuse a bfloat16 command on a shape whose devices have no bfloat16.

    An unregistered profile is not checked, and that is deliberate rather than a hole, for
    the reason :func:`~edullm_platform.launchers.require_a_process_for_every_device` records
    about a profile with no container shape: it cannot run, it is refused a few lines later
    for the thing that is actually wrong with it, and a refusal here would name a dtype for a
    submission whose real problem is that the machine does not exist.

    Raises :class:`~edullm_platform.errors.SubmissionRefusedError`, as both neighbouring
    command rules do, so the caller needs no second branch.
    """
    profile = next(
        (entry for entry in catalog.compute_profiles if entry.name == compute_profile), None
    )
    if profile is None:
        return
    gpu = gpu_of(profile)
    if gpu is None or gpu.architecture.supports_bfloat16:
        return
    request = bfloat16_request_in(command)
    if request is None:
        return
    raise SubmissionRefusedError(
        _refusal(profile=profile, gpu=gpu, request=request, catalog=catalog)
    )


# ---------------------------------------------------------------------------------------
# Reading the command
# ---------------------------------------------------------------------------------------


def bfloat16_request_in(command: Sequence[str]) -> str | None:
    """The part of this command that asks for bfloat16, quoted back, or ``None``.

    The offending text rather than a boolean, because that is what makes a wrong answer
    diagnosable: a submitter reading "this command asks for bfloat16" and disagreeing has
    nowhere to go, and one reading the seven characters that were matched can see at once
    whether the guard is right.

    Read over the words of every simple command, wrappers opened, comments dropped -- which
    is what :func:`~edullm_platform.launchers.simple_commands` returns. Words rather than the
    raw text, which is the opposite of what
    :mod:`edullm_platform.checkpoint_commands` needs and is right for the opposite reason:
    that guard asks whether a shell would *expand* something, so quoting decides the answer
    and ``shlex`` deletes the evidence. This asks what a program is handed, and
    ``--dtype 'bfloat16'`` and ``--dtype bfloat16`` hand it the same thing.
    """
    for segment in simple_commands(tuple(command)):
        for position, word in enumerate(segment):
            following = segment[position + 1] if position + 1 < len(segment) else None
            found = _request_at(word, following)
            if found is not None:
                return found
    return None


def _request_at(word: str, following: str | None) -> str | None:
    """Whether this word, with the one after it, asks for bfloat16.

    Three forms, and they are three because that is how many ways the trainers this platform
    can run accept the setting.

    ``NAME=value`` covers both the assignment a shell consumes and the dotted override
    OLMo-core's own config system takes -- ``train_module.dp_config.param_dtype=bfloat16`` is
    how every committed script in that repository spells it, and ``Config.from_dict``
    resolves the dotted path itself. The last dotted segment is the key, so a path of any
    depth is read the same way.

    ``--flag value`` is the ordinary argparse spelling: ``--dtype bfloat16``,
    ``--mixed_precision bf16``, ``--torch-dtype torch.bfloat16``.

    A bare ``--bf16`` is its own third form because there is no value to inspect. It has to
    be read as true when nothing follows it and as false when ``false`` does, since both are
    written.
    """
    key, separator, value = word.partition("=")
    if separator:
        name = _setting_name(key)
        if name in _SWITCHES:
            return None if value.casefold() in _TURNED_OFF else word
        if _names_a_precision_setting(name) and value.casefold() in BFLOAT16_SPELLINGS:
            return word
        return None

    # A leading dash from here down, because these two forms are flags. Without it
    # ``python bf16_ablation.py bfloat16`` would read as a request, and a positional argument
    # is never how a dtype is passed.
    if not word.startswith("-"):
        return None
    name = _setting_name(word)
    if name in _SWITCHES:
        return None if following is not None and following.casefold() in _TURNED_OFF else word
    if (
        _names_a_precision_setting(name)
        and following is not None
        and following.casefold() in BFLOAT16_SPELLINGS
    ):
        return f"{word} {following}"
    return None


def _setting_name(word: str) -> str:
    """What a key is called, with the punctuation that varies between spellings removed.

    Leading dashes go, a dotted path collapses to its last segment, hyphens become
    underscores and the case is folded. ``--torch-dtype``, ``TORCH_DTYPE`` and
    ``train_module.dp_config.param_dtype`` then all carry the word this is looking for.
    """
    return word.lstrip("-").split(".")[-1].replace("-", "_").casefold()


def _names_a_precision_setting(name: str) -> bool:
    return any(word in name for word in _PRECISION_SETTING_WORDS)


# ---------------------------------------------------------------------------------------
# What a submitter reads
# ---------------------------------------------------------------------------------------

#: What this guard did and did not look at, said on the refusal itself rather than left in a
#: guide. A submitter who has met this once will reasonably believe the platform knows which
#: runs are bfloat16, and it does not: it knows which *commands say so*. The gap is not small
#: -- the image's own training entrypoint sets the dtype in code -- so the sentence that
#: bounds the claim travels with every refusal that makes it.
_WHAT_WAS_CHECKED: Final = (
    "This read the words of your command and nothing else. bfloat16 that is set inside the "
    "program, or in a config file in the image, or through a shell variable this cannot "
    "resolve, is invisible here and is not refused -- .edullm/train_on_corpus.py sets it in "
    "code with no flag for it, so a command that merely runs that program is a bfloat16 run "
    "this guard cannot see."
)


def _refusal(
    *,
    profile: ComputeProfile,
    gpu: Gpu,
    request: str,
    catalog: WorkloadCatalog,
) -> str:
    return (
        f"compute profile {profile.name!r} is {profile.instance_type}, whose {gpu.model} is a "
        f"{gpu.architecture.name}-generation card with no bfloat16 in the hardware, and this "
        f"command asks for bfloat16: {request}. The run would be classified, released by a "
        f"lead, admitted and placed, and then die on the first kernel that needs the format, "
        f"at ${serialize_decimal(profile.hourly_rate_usd)}/hour. Nothing before the device "
        "reports a problem, and torch.cuda.is_bf16_supported() returns true on this card, so "
        "a program that asks before committing to a dtype is told the wrong thing by the "
        f"only source that would know. {_bfloat16_shapes_said(catalog)} Or keep the shape and "
        "change the recipe -- fp16 with loss scaling, or fp32 master weights and a smaller "
        "micro-batch -- which is a deviation for you to declare rather than one this platform "
        f"makes on your behalf. {_WHAT_WAS_CHECKED}"
    )


def _bfloat16_shapes_said(catalog: WorkloadCatalog) -> str:
    """The shapes that do have bfloat16, or the sentence for a catalog with none.

    Provisioned only, because an unprovisioned profile is refused a few lines earlier by
    ``resolve_compute_profile_for_execution`` and pointing at one would be a dropdown that
    lost an option. Whether a named shape can be *placed* is deliberately not consulted:
    that is what ``config/capacity.yaml`` and
    :func:`~edullm_platform.placement.placement_warning` answer, it is a warning rather than
    a refusal for reasons that module argues at length, and a submission naming any shape
    here already gets that sentence separately. A shape can place and lack bfloat16, and it
    can have bfloat16 and not place; folding the two together would make one message that is
    wrong about both.

    The empty branch exists and is reachable only from a catalog in which no provisioned GPU
    profile has bfloat16, which is not the shipped one. It says so rather than printing an
    empty list, because a sentence ending in a colon and nothing is read as a bug in the tool
    rather than as the answer.
    """
    offered = sorted(
        entry.name
        for entry in catalog.compute_profiles
        if entry.provisioned
        and (found := gpu_of(entry)) is not None
        and found.architecture.supports_bfloat16
    )
    if not offered:
        return (
            "No provisioned compute profile in config/workload-catalog.yaml carries a card "
            "with bfloat16, so there is no shape to move this to."
        )
    return f"Provisioned shapes whose cards have bfloat16: {', '.join(offered)}."
