"""A bfloat16 command on a Turing card, refused before anybody is asked to release it.

``config/capacity.yaml`` records that ``gpu-8xt4`` is the only shape above four cards this
account can obtain and that every other placing GPU shape is a single card, so whoever needs
more than one device is routed onto T4s -- which have no bfloat16 in the silicon. Until this
guard the platform accepted a bfloat16 recipe there, classified it, sent it to a lead, placed
it, and let it die on the device at $7.824/hour.

**EVERY CASE HERE IS BUILT FROM THE SHIPPED ``config/``.** The shapes, their instance types
and their provisioning are the real catalog's, so a case that passes is a statement about
what this platform would do rather than about a fixture. That is also what makes the
capability checks below meaningful: they compare a table in Python against the file that
decides which shapes exist.

**THE THREE THINGS THAT WOULD MAKE THIS A TEST UNABLE TO FAIL, AND WHAT ANSWERS EACH.**

A guard that refuses nothing passes every negative case, so
:func:`test_a_bfloat16_command_on_every_turing_shape_is_refused` runs the positive case over
every T4 shape the catalog prices, generated from the catalog rather than listed.

A capability table in which nothing lacks bfloat16 makes the refusal unreachable while every
test still passes -- the empty-collection failure. So
:func:`test_the_catalog_prices_at_least_one_provisioned_shape_without_bfloat16` asserts the
population is non-empty and names it, and it fails if ``TURING`` is ever given the flag.

A family this module has never heard of resolves to no answer and is not refused, which is
the quiet way a promoted shape leaves the guard's coverage. So
:func:`test_every_gpu_shape_the_catalog_prices_resolves_to_a_known_card` holds the family map
to the catalog, and a shape priced on an unfamiliar family is a red test rather than a silent
pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from edullm_platform.config import load_yaml
from edullm_platform.contracts.base import serialize_decimal
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.errors import SubmissionRefusedError
from edullm_platform.precision import (
    GPUS_BY_INSTANCE_FAMILY,
    bfloat16_request_in,
    gpu_of,
    instance_family,
    require_bfloat16_only_where_the_hardware_has_it,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = PROJECT_ROOT / "config" / "workload-catalog.yaml"

CATALOG = load_yaml(CATALOG_PATH, WorkloadCatalog)

#: Generated from the catalog rather than written down, so a T4 shape priced tomorrow joins
#: every case below without anybody remembering to add it -- the same argument
#: ``tests/test_multi_gpu_launcher.py`` makes for reading its device counts out of
#: ``CONTAINER_SHAPES``.
SHAPES_WITHOUT_BFLOAT16 = tuple(
    sorted(
        profile.name
        for profile in CATALOG.compute_profiles
        if (gpu := gpu_of(profile)) is not None and not gpu.architecture.supports_bfloat16
    )
)
SHAPES_WITH_BFLOAT16 = tuple(
    sorted(
        profile.name
        for profile in CATALOG.compute_profiles
        if (gpu := gpu_of(profile)) is not None and gpu.architecture.supports_bfloat16
    )
)

#: The eight-card T4 machine the capacity measurement made unavoidable, and the single A10G
#: that is the nearest shape with the format. Named because the worked examples read better
#: with a real pair, not because the cases above depend on them.
EIGHT_T4 = "gpu-8xt4"
ONE_A10G = "gpu-1xa10g"
NO_GPUS = "cpu-32vcpu"

#: How OLMo-core's own committed scripts spell it. ``src/examples/llm/train.py``,
#: ``src/scripts/train/template.py`` and ``.edullm/train_on_corpus.py`` all construct
#: ``TransformerDataParallelConfig(..., param_dtype=DType.bfloat16, ...)``, and the dotted
#: override is how that field is reached from a command line -- which is the form the
#: platform's own guide teaches, since ``guides/olmo-core.md`` prints a worked command
#: carrying ``train_module.compile_model=false`` and five more of the same shape.
OLMO_CORE_OVERRIDE = "train_module.dp_config.param_dtype=bfloat16"


def wrapped(inner: str) -> tuple[str, ...]:
    """A command as every real submission arrives: inside the shell wrapper the guide prints.

    Not decoration. ``ContainerOverrides.Command`` is exec form, so ``$EDULLM_RUN_ID`` has to
    be expanded by a shell the submitter supplies, and a guard that could only read a bare
    argv would read nothing about any real training command.
    """
    return ("bash", "-lc", inner)


def refuse(command: tuple[str, ...], *, compute_profile: str) -> str:
    with pytest.raises(SubmissionRefusedError) as exc_info:
        require_bfloat16_only_where_the_hardware_has_it(
            command=command, compute_profile=compute_profile, catalog=CATALOG
        )
    return str(exc_info.value)


def allow(command: tuple[str, ...], *, compute_profile: str) -> None:
    require_bfloat16_only_where_the_hardware_has_it(
        command=command, compute_profile=compute_profile, catalog=CATALOG
    )


# ---------------------------------------------------------------------------------------
# The capability data, held to the file that decides which shapes exist
# ---------------------------------------------------------------------------------------


def test_every_gpu_shape_the_catalog_prices_resolves_to_a_known_card() -> None:
    """Mutation: price a shape on an instance family the map does not carry.

    This is the hole a guard keyed on hardware quietly falls through. A profile whose family
    is unrecorded gets ``None`` from :func:`gpu_of`, and ``None`` is not refused -- so the
    shape is submittable with a bfloat16 command and the guard says nothing, which is
    indistinguishable from the guard working. Held here rather than left to the runtime
    branch because the honest place to answer "we have never heard of this card" is a red
    test, not a silent pass on somebody's submission.
    """
    unresolved = [
        profile.name
        for profile in CATALOG.compute_profiles
        if profile.accelerator == "gpu" and gpu_of(profile) is None
    ]

    assert unresolved == [], (
        f"{unresolved} are priced as GPU shapes and their instance families are not in "
        "GPUS_BY_INSTANCE_FAMILY, so nothing knows whether their cards have bfloat16"
    )


def test_the_catalog_prices_at_least_one_provisioned_shape_without_bfloat16() -> None:
    """Mutation: give ``TURING`` ``supports_bfloat16=True``.

    THE EMPTY-COLLECTION FAILURE, WRITTEN DOWN BEFORE IT COULD HAPPEN. Every other test in
    this file passes against a table in which no card lacks bfloat16, because the refusal
    simply never fires and the negative cases are all still correct. This is the one that
    does not: it says the population the guard exists for is non-empty, and it says which
    shapes are in it, so a table edit that emptied it is a failure naming the edit.

    Provisioned as well as priced, because an unprovisioned shape is refused earlier for
    having no compute environment and a guard whose only subjects were unrunnable would be
    machinery with nothing to act on.
    """
    provisioned_without = [
        profile.name
        for profile in CATALOG.compute_profiles
        if profile.provisioned
        and (gpu := gpu_of(profile)) is not None
        and not gpu.architecture.supports_bfloat16
    ]

    assert provisioned_without, (
        "no provisioned shape in config/workload-catalog.yaml lacks bfloat16, so this guard "
        "can never refuse anything and every case in this file would pass with it deleted"
    )
    # Named, so that a T4 shape quietly acquiring the format is a test edit somebody has to
    # justify rather than a line that disappears without a sound.
    assert sorted(provisioned_without) == ["gpu-1xt4", "gpu-4xt4", "gpu-8xt4"]


def test_the_capability_comes_from_the_instance_family_and_not_from_the_profile_name() -> None:
    """Mutation: key the table on profile names.

    A list of shape names in Python is how the submission form's dropdown came to disagree
    with the account. The catalog is the only place the set of shapes is written down, so a
    fictional profile on a real family has to answer correctly without this module having
    heard of it.
    """
    assert instance_family("g4dn.metal") == "g4dn"
    assert instance_family("g6e.xlarge") == "g6e"
    # g6 and g6e are different cards, which is the one pair a prefix match gets wrong.
    assert GPUS_BY_INSTANCE_FAMILY["g6"].model == "L4"
    assert GPUS_BY_INSTANCE_FAMILY["g6e"].model == "L40S"
    assert not GPUS_BY_INSTANCE_FAMILY["g4dn"].architecture.supports_bfloat16
    assert GPUS_BY_INSTANCE_FAMILY["g5"].architecture.supports_bfloat16

    unpriced_t4_shape = CATALOG.compute_profiles[0].model_copy(
        update={"name": "gpu-2xt4", "instance_type": "g4dn.2xlarge", "accelerator": "gpu"}
    )

    gpu = gpu_of(unpriced_t4_shape)
    assert gpu is not None
    assert not gpu.architecture.supports_bfloat16


def test_a_cpu_profile_has_no_card_and_is_never_refused() -> None:
    """Mutation: infer the accelerator from the instance type instead of reading the field.

    ``c7i`` is absent from the family map for the same reason ``g7`` would be, and the two
    absences mean different things. The catalog declares which profiles are GPU profiles, so
    that is what is read.
    """
    assert gpu_of(next(p for p in CATALOG.compute_profiles if p.name == NO_GPUS)) is None

    allow(wrapped(f"python train.py {OLMO_CORE_OVERRIDE}"), compute_profile=NO_GPUS)


# ---------------------------------------------------------------------------------------
# The refusal
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("compute_profile", SHAPES_WITHOUT_BFLOAT16)
def test_a_bfloat16_command_on_every_turing_shape_is_refused(compute_profile: str) -> None:
    """The case the whole thing exists for, over every shape it applies to."""
    message = refuse(
        wrapped(f'python .edullm/train.py "$EDULLM_RUN_ID" {OLMO_CORE_OVERRIDE}'),
        compute_profile=compute_profile,
    )

    assert compute_profile in message


@pytest.mark.parametrize("compute_profile", SHAPES_WITH_BFLOAT16)
def test_the_same_command_on_every_shape_that_has_bfloat16_is_accepted(
    compute_profile: str,
) -> None:
    """Mutation: refuse bfloat16 everywhere, or refuse on the GPU flag rather than the card.

    The counterpart the positive case is worth nothing without. A guard that refused every
    bfloat16 command would pass every test above and would make the ten shapes that can run
    the format unusable for the work they exist for.
    """
    allow(
        wrapped(f'python .edullm/train.py "$EDULLM_RUN_ID" {OLMO_CORE_OVERRIDE}'),
        compute_profile=compute_profile,
    )


def test_the_refusal_names_the_shape_the_card_the_token_and_where_to_go() -> None:
    """Mutation: refuse with ``bfloat16_not_in_the_hardware`` and nothing else.

    A reason code sends a submitter to whoever wrote it. What makes this self-service is the
    shape they picked, the card behind it, the words of their own command that were matched
    -- so a wrong match is visible rather than an argument -- and somewhere to move to.
    """
    message = refuse(wrapped(f"python train.py {OLMO_CORE_OVERRIDE}"), compute_profile=EIGHT_T4)
    by_name = {profile.name: profile for profile in CATALOG.compute_profiles}

    assert EIGHT_T4 in message
    assert by_name[EIGHT_T4].instance_type in message
    assert "T4" in message
    assert "Turing" in message
    assert OLMO_CORE_OVERRIDE in message
    # The rate is interpolated from the catalog rather than written into the sentence, so it
    # cannot go on saying one figure after somebody re-reads the price list.
    assert f"${serialize_decimal(by_name[EIGHT_T4].hourly_rate_usd)}/hour" in message
    assert ONE_A10G in message


def test_the_refusal_offers_only_shapes_that_are_provisioned() -> None:
    """Mutation: offer every priced shape with the format.

    ``gpu-1xh100`` has bfloat16 and no compute environment, and
    ``resolve_compute_profile_for_execution`` refuses it a few lines earlier. Naming it here
    would send somebody from a refusal they can act on to one they cannot.
    """
    message = refuse(wrapped(f"python train.py {OLMO_CORE_OVERRIDE}"), compute_profile=EIGHT_T4)

    offered = message.split("Provisioned shapes whose cards have bfloat16: ")[1].split(".")[0]
    named = {name.strip() for name in offered.split(",")}

    by_name = {profile.name: profile for profile in CATALOG.compute_profiles}
    assert named
    assert all(by_name[name].provisioned for name in named)
    assert "gpu-1xh100" not in named


def test_the_refusal_says_what_it_read_and_what_it_could_not() -> None:
    """Mutation: drop the sentence bounding the claim.

    THIS IS THE ASSERTION THAT KEEPS THE GUARD HONEST RATHER THAN THE ONE THAT MAKES IT WORK.
    The platform's own image sets ``param_dtype=DType.bfloat16`` in ``train_on_corpus.py`` and
    offers no flag for it, so the most common bfloat16 run in this account carries no
    bfloat16 token and is not refused. A submitter who has met this refusal once will
    otherwise conclude the platform knows which runs are bfloat16; it knows which commands
    say so, and the difference has to travel with the message.
    """
    message = refuse(wrapped(f"python train.py {OLMO_CORE_OVERRIDE}"), compute_profile=EIGHT_T4)

    assert "read the words of your command and nothing else" in message
    assert "train_on_corpus.py" in message


def test_the_bfloat16_run_this_guard_cannot_see_is_accepted() -> None:
    """The documented limit, asserted rather than described.

    The guide's own training line on the eight-card T4 machine. It is a bfloat16 run and this
    accepts it, because the dtype is in the program. Written as a test so that the gap is a
    fact in the suite rather than a paragraph somebody has to believe -- and so that whoever
    closes it deletes an assertion on purpose.
    """
    allow(
        wrapped(
            'python .edullm/train_on_corpus.py "$EDULLM_RUN_ID" '
            '--save-folder "$EDULLM_CHECKPOINT_DIR" --steps 4000'
        ),
        compute_profile=EIGHT_T4,
    )


def test_an_unregistered_profile_is_left_to_the_refusal_that_owns_it() -> None:
    """Mutation: refuse a bfloat16 command on a profile nothing prices.

    The profile has no instance type, so there is no card to be right or wrong about, and a
    dtype refusal would point at a field that was never what stood in the way. The same
    reasoning ``require_a_process_for_every_device`` records for a profile with no shape.
    """
    allow(wrapped(f"python train.py {OLMO_CORE_OVERRIDE}"), compute_profile="gpu-64xt4")


# ---------------------------------------------------------------------------------------
# Which spellings are read as a request
# ---------------------------------------------------------------------------------------

#: The forms this claims to detect. Each is a real spelling rather than a permutation
#: invented to fill a table: the dotted override is OLMo-core's, ``--dtype`` is vLLM's and
#: the HuggingFace generation path's, ``--mixed_precision`` is accelerate's, ``--bf16`` is
#: ``TrainingArguments``' and DeepSpeed's, and ``torch_dtype`` is ``from_pretrained``'s.
DETECTED = (
    OLMO_CORE_OVERRIDE,
    "train_module.dp_config.param_dtype=bf16",
    "--dtype bfloat16",
    "--dtype=bfloat16",
    "--dtype bf16",
    "--torch-dtype torch.bfloat16",
    "--torch_dtype=bfloat16",
    "--mixed_precision bf16",
    "--mixed-precision=bf16",
    "--precision bf16",
    "--bf16",
    "--bf16 true",
    "--bf16=True",
    "--bfloat16",
    "TORCH_DTYPE=bfloat16",
    "--dtype BFloat16",
)

#: Commands that must not be refused. The first four are somebody asking for something else,
#: and the rest are the false positives a looser reading would produce -- an experiment named
#: after the format, a module named after it, and the format written in a comment.
NOT_DETECTED = (
    "--dtype float32",
    "--dtype fp16",
    "--fp16",
    "--bf16 false",
    "--bf16=0",
    "--wandb-project bf16-ablation",
    "--run-name bfloat16-vs-fp16",
    "python bf16_ablation.py",
    "--steps 4000 # switched off bf16 for this one",
)


@pytest.mark.parametrize("fragment", DETECTED)
def test_the_spellings_this_claims_to_read_are_read(fragment: str) -> None:
    assert bfloat16_request_in(wrapped(f"python train.py {fragment}")) is not None

    message = refuse(wrapped(f"python train.py {fragment}"), compute_profile=EIGHT_T4)
    assert EIGHT_T4 in message


@pytest.mark.parametrize("fragment", NOT_DETECTED)
def test_a_command_that_asks_for_something_else_is_not_refused(fragment: str) -> None:
    """Mutation: match ``bf16`` anywhere in any word.

    Each of these passes a substring test and none of them is a bfloat16 request. A guard
    that refused an experiment called ``bf16-ablation`` would be uninstalled within a week,
    and the version of it people would ask for is no guard at all.
    """
    assert bfloat16_request_in(wrapped(f"python train.py {fragment}")) is None

    allow(wrapped(f"python train.py {fragment}"), compute_profile=EIGHT_T4)


def test_a_request_inside_a_wrapper_inside_a_wrapper_is_still_read() -> None:
    """Mutation: read the outer argv only.

    Every real submission is already one wrapper deep, so a guard that read only the words it
    was handed would read ``bash``, ``-lc`` and one long string on every training command in
    this account and find nothing in any of them.
    """
    assert bfloat16_request_in(("python", "train.py", "--dtype", "bfloat16")) is not None
    assert bfloat16_request_in(wrapped("python train.py --dtype bfloat16")) is not None
    assert (
        bfloat16_request_in(wrapped("bash -c 'python train.py --dtype bfloat16'")) is not None
    )
    # ``exec`` in front of the program is ordinary and is read, because the words of the
    # segment are scanned whatever runs them.
    assert bfloat16_request_in(wrapped("exec python train.py --dtype bfloat16")) is not None


def test_a_transparent_prefix_in_front_of_a_nested_shell_is_a_known_miss() -> None:
    """The one wrapper form this does not open, asserted so that it is a fact and not a hope.

    ``shell_command_string`` recognises a shell only in the first word of a simple command,
    so ``exec bash -c '...'`` -- which :mod:`edullm_platform.launchers` names as an ordinary
    thing to write -- keeps its inner text as a single word and nothing inside it is read.
    The same blind spot is in the device-count and checkpoint guards, because all three read
    a command the same way on purpose, and widening it is a change to that shared reading
    rather than to this rule.

    Recorded as a passing test asserting the miss, rather than as an xfail or a sentence in a
    docstring, so that closing it is a deliberate deletion by whoever closes it.
    """
    assert bfloat16_request_in(wrapped("exec bash -c 'python t.py --dtype bfloat16'")) is None


def test_a_request_in_a_later_simple_command_is_read() -> None:
    """A setup step and then the trainer, which is how a command carrying two things reads."""
    assert (
        bfloat16_request_in(wrapped("mkdir -p /tmp/dc && python train.py --dtype bfloat16"))
        is not None
    )


def test_the_matched_text_is_quoted_back_rather_than_summarised() -> None:
    """What makes a wrong answer arguable rather than mysterious."""
    assert bfloat16_request_in(wrapped("python t.py --dtype bfloat16")) == "--dtype bfloat16"
    assert bfloat16_request_in(wrapped(f"python t.py {OLMO_CORE_OVERRIDE}")) == OLMO_CORE_OVERRIDE


# ---------------------------------------------------------------------------------------
# Where the rule is asked, which is the half a unit test of the rule proves nothing about
# ---------------------------------------------------------------------------------------

#: A real eight-rank training submission, satisfying both neighbouring command guards so that
#: what these two cases differ in is the card and nothing else: the launcher starts one
#: process per device, and the save folder is the variable a retry re-derives.
EIGHT_RANK_BFLOAT16 = (
    "bash",
    "-lc",
    (
        "python -m torch.distributed.run --nproc-per-node=8 --standalone "
        '-m olmo_core.train --save-folder "$EDULLM_CHECKPOINT_DIR" '
        f"{OLMO_CORE_OVERRIDE}"
    ),
)

#: The eight-card A10G shape: the same device count, the same command, a card with the
#: format. What the accepted case has to hold constant for the refused one to mean anything.
EIGHT_A10G = "gpu-8xa10g"


def test_the_submission_path_refuses_a_bfloat16_run_on_the_eight_card_t4() -> None:
    """Mutation: leave ``precision`` importable and never call it from ``compile_submission``.

    A guard nothing invokes passes every test written against the guard. This is the one that
    fails, because it goes through the function the submission workflow's compile job calls
    and asserts the refusal arrives from there.
    """
    from test_phase2_submission import compile_payload, olmo_payload

    with pytest.raises(SubmissionRefusedError) as exc_info:
        compile_payload(
            olmo_payload(
                command=list(EIGHT_RANK_BFLOAT16),
                compute_profile=EIGHT_T4,
            )
        )

    assert EIGHT_T4 in str(exc_info.value)
    assert "bfloat16" in str(exc_info.value)


def test_the_submission_path_compiles_the_same_run_on_a_card_that_has_bfloat16() -> None:
    """The control, and the reason the case above is evidence rather than a coincidence.

    Same command, same rank count, same workload, same everything except the shape. If this
    also failed, the refusal above would be telling us nothing about bfloat16.
    """
    from test_phase2_submission import compile_payload, olmo_payload

    submission = compile_payload(
        olmo_payload(
            command=list(EIGHT_RANK_BFLOAT16),
            compute_profile=EIGHT_A10G,
        )
    )

    assert submission.manifest.compute_profile == EIGHT_A10G
    assert OLMO_CORE_OVERRIDE in " ".join(submission.manifest.command)
