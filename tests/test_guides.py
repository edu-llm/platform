"""The guides, held to the platform they describe.

A guide is a promise in the same way a dropdown is: everything in it works. The difference
is that a dropdown is checked by the tests beside it and prose is checked by nobody, so it
rots quietly and the first person to find out is a researcher following it at two in the
morning.

**These read the guides against the thing rather than against a copy of the thing.** The
workload names come out of the catalog, the environment variables out of
``batch_submit_request``, the form fields out of the workflow. A rename on either side
fails here, which is the only way a document stays true to a system that keeps moving.

**These tests used to read one file.** ``GETTING-STARTED.md`` was both the platform's
onboarding and OLMo-core's training tutorial, which was fine while OLMo-core was the only
repository anyone could submit from. It stopped being fine when three were, so the guide
split: everything true whichever repository you work in went to ``guides/the-platform.md``,
and the training material went to ``guides/olmo-core.md``. Every assertion below survived the
split and is aimed at whichever half now owns the claim.

What is deliberately *not* tested is the prose. There is no assertion that a paragraph
still reads well or that the traps are explained convincingly. Those are worth having and a
test cannot hold them; what a test can hold is that every identifier a guide puts in front
of somebody still exists.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from edullm_platform.config import load_yaml
from edullm_platform.contracts.workload import WorkloadCatalog

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GUIDES_DIR = PROJECT_ROOT / "guides"
PLATFORM_GUIDE_PATH = GUIDES_DIR / "the-platform.md"
OLMO_CORE_GUIDE_PATH = GUIDES_DIR / "olmo-core.md"
README_PATH = PROJECT_ROOT / "README.md"
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "submit-run.yml"
CANCEL_WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "cancel-run.yml"
GPU_ROLES_PATH = PROJECT_ROOT / "infra" / "iam" / "batch-gpu-roles.yaml"


@pytest.fixture(scope="module")
def platform_guide() -> str:
    return PLATFORM_GUIDE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def olmo_core_guide() -> str:
    return OLMO_CORE_GUIDE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    # `on:` is parsed by PyYAML as the boolean True, which is the same trap the other
    # workflow tests document; reading the mapping back by identity avoids arguing with it.
    parsed: dict[str, Any] = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return parsed


@pytest.fixture(scope="module")
def cancel_workflow() -> dict[str, Any]:
    parsed: dict[str, Any] = yaml.safe_load(CANCEL_WORKFLOW_PATH.read_text(encoding="utf-8"))
    return parsed


def form_inputs(workflow: dict[str, Any]) -> dict[str, Any]:
    triggers = workflow.get("on") or workflow.get(True)
    inputs: dict[str, Any] = triggers["workflow_dispatch"]["inputs"]
    return inputs


def test_every_guide_the_readme_sends_a_reader_to_exists() -> None:
    """Mutation: rename a guide and leave the README linking the old name.

    The README is the only entry point now that there is no single file to land on, so a
    dead link there is a reader who concludes the documentation does not exist. Read out of
    the README rather than listed here, so adding a fourth guide needs no edit to this test
    and removing one that is still linked fails.
    """
    linked = set(re.findall(r"\((guides/[a-z0-9-]+\.md)\)", README_PATH.read_text(encoding="utf-8")))

    assert linked, "the README links no guide at all, so nothing sends a reader anywhere"
    missing = {target for target in linked if not (PROJECT_ROOT / target).exists()}
    assert missing == set(), f"the README links guides that do not exist: {sorted(missing)}"


def test_every_workload_the_guide_names_is_one_the_form_offers(olmo_core_guide: str) -> None:
    """Mutation: rename a workload and leave the guide saying the old one.

    The guide tells a first-time reader to pick ``olmo-core-check`` and a researcher to pick
    ``olmo-core-train``. Both are names, and names have moved on this platform twice: every
    workload ended in ``-smoke``, and then every one of these ended in the machine it was
    believed to fix.
    """
    catalog = load_yaml(PROJECT_ROOT / "config" / "workload-catalog.yaml", WorkloadCatalog)
    registered = {workload.name for workload in catalog.workloads}

    named = set(re.findall(r"`(olmo-core-[a-z0-9-]+)`", olmo_core_guide))

    assert named, "the guide names no workload at all, which cannot be right"
    assert named <= registered, (
        f"the guide names workloads that are not in the catalog: {sorted(named - registered)}"
    )


def test_every_form_field_the_guide_documents_is_a_field_the_form_has(
    platform_guide: str,
    workflow: dict[str, Any],
) -> None:
    """Mutation: rename a form input without touching the guide.

    ``project`` became ``experiment`` and ``team`` went from free text to a closed dropdown,
    so this is a live risk rather than a theoretical one. Only fields the guide puts in
    backticks are checked, because the prose also uses ordinary words like "command".
    """
    inputs = form_inputs(workflow)
    documented = set(re.findall(r"`(commit_sha|workload_profile|team|experiment|wandb_project|command|compute_profile|image_digest|dataset_release|maximum_runtime_hours|maximum_attempts)`", platform_guide))

    assert documented, "the guide documents no form field"
    assert documented <= set(inputs), (
        f"the guide documents fields the form does not have: {sorted(documented - set(inputs))}"
    )


def test_every_team_the_guide_names_is_one_the_form_offers(
    platform_guide: str,
    workflow: dict[str, Any],
) -> None:
    """Mutation: rename a research group and leave the guide listing the old set.

    Three groups were renamed and two added on 2026-08-01, and the field became a closed
    dropdown in the same change. A guide naming the old set now tells a reader to pick
    something the form will not offer, and the reader's conclusion is that they are on the
    wrong page rather than that the page is stale.
    """
    offered = set(form_inputs(workflow)["team"]["options"])
    named = {
        team
        for team in re.findall(r"`([a-z][a-z-]+)`", platform_guide)
        if team in offered or team in {"tokenizer", "modeling", "curriculum"}
    }

    assert named, "the guide names no research group"
    assert named <= offered, (
        f"the guide names groups the form does not offer: {sorted(named - offered)}"
    )


def test_every_environment_variable_the_guide_promises_is_one_the_container_gets() -> None:
    """THE ONE THAT MATTERS. Mutation: drop EDULLM_CHECKPOINT_DIR from the submit request.

    The training guide's central instruction rests on the container being handed a place to
    write. If that variable stopped being sent, the guides would be telling thirty-four
    people to write their checkpoints to an empty string -- and the failure is the quiet one
    they exist to prevent, because the run still exits zero.

    Read out of ``batch_submit_request`` rather than a list, so the two cannot drift.
    """
    from edullm_platform.execution import batch_submit_request
    from tests.test_phase3_execution import RUN_ID, manifest, target

    # Two requests, because three of the variables the guide documents only exist on a run
    # that named a published corpus. A run submitted with `dataset_release: none` is given no
    # EDULLM_DATASET_ID at all rather than an empty one -- an empty value would read as a
    # resolution that was attempted and failed. So the set the guide is checked against is
    # what a container can be given, which is the union, and the guide says which of the two
    # cases each variable belongs to.
    from tests.test_phase4_training_submission import published_reference

    # A fan-out beside the two dataset cases, and for the same reason. A cell is told
    # EDULLM_FANOUT_INDEX_PARAMETER and a single run is not, so a union built only from
    # single runs would report the guide's fan-out section as promising something the
    # container never gets. AWS_BATCH_JOB_ARRAY_INDEX is documented in the same table and
    # is deliberately absent from this comparison: Batch sets it in each child, so nothing
    # here sends it and a submit request that did would be overriding the one value the
    # platform must not author.
    sent: set[str] = set()
    for dataset_reference in (None, published_reference("regmix-10b-v1")):
        for fanout in (None, {"size": 4, "index_parameter": "seed"}):
            request = batch_submit_request(
                manifest=manifest(fanout=fanout),
                target=target(),
                run_id=RUN_ID,
                job_definition=target().job_definition_arn,
                dataset_reference=dataset_reference,
            )
            sent |= {entry["Name"] for entry in request["ContainerOverrides"]["Environment"]}

    # THE WAIVER TOKEN TRAVELS THE OTHER WAY AND IS THE ONE EXCLUSION. Every name below is
    # something the platform hands the container, and a guide naming one the container never
    # gets is the defect this test exists for. EDULLM_LAUNCH_CHECK is the opposite: the
    # submitter writes it into their own command to record a decision, so it is never in the
    # submit request and never should be. Derived from the constant rather than spelled out,
    # so renaming the token moves the exclusion with it.
    from edullm_platform.checkpoint_commands import CHECKPOINT_CHECK_WAIVER
    from edullm_platform.launchers import LAUNCH_CHECK_WAIVER

    set_by_the_submitter = {
        token.split("=", 1)[0] for token in (LAUNCH_CHECK_WAIVER, CHECKPOINT_CHECK_WAIVER)
    }

    # Both halves, because the variable table lives in one and the instructions that depend
    # on it live in the other, and either could promise something the container never gets.
    promised: set[str] = set()
    for path in (PLATFORM_GUIDE_PATH, OLMO_CORE_GUIDE_PATH):
        promised |= set(
            re.findall(r"`?\$?(EDULLM_[A-Z_]+|WANDB_[A-Z_]+)`?", path.read_text(encoding="utf-8"))
        )
    promised -= set_by_the_submitter

    assert "EDULLM_CHECKPOINT_DIR" in promised, (
        "the guides no longer mention the variable the training instructions depend on"
    )
    assert promised <= sent, (
        f"the guides promise variables the container is not given: {sorted(promised - sent)}"
    )


def test_the_readme_names_someone_to_tell_when_a_run_breaks() -> None:
    """Mutation: soften it to "open an issue" with nobody attached.

    An unowned intake is one people stop using after the first unanswered issue. The name
    is the difference between a queue and a wall, and it is cheap to assert that one is
    there at all.

    **Asserted against the README rather than the guide, since the split.** Support lived in
    both and duplicated prose rots; the rule that separated them is that the README says what
    the platform is and where to ask, and the guides say what to type. Where to ask is the
    README's, so the name is asserted where it now lives.
    """
    readme = README_PATH.read_text(encoding="utf-8")

    assert "@philote-dev" in readme, "the README names nobody to tell when a run breaks"


def test_the_guide_leads_with_the_command_a_researcher_copies(olmo_core_guide: str) -> None:
    """Whatever the one line currently is, it belongs above the explanation of why.

    THIS ASSERTION MOVED ONCE ALREADY, WHICH IS THE POINT OF WRITING IT THIS WAY. It used to
    require ``EDULLM_CHECKPOINT_DIR`` in the first half, because the line a researcher
    copied was ``--save-folder "$EDULLM_CHECKPOINT_DIR"`` and forgetting it cost a whole
    twelve-hour run. Then the entry point took that over and the variable became something
    the container handles, so the old assertion still passed -- a few paragraphs from going
    red for a reason that had nothing to do with the guide getting worse.

    So it holds the shape rather than the string: the command comes before the section that
    explains the traps behind it. A guide that opens with six caveats and buries the thing
    to paste is complete and unread.
    """
    command = olmo_core_guide.index("bash -lc")
    caveats = olmo_core_guide.index("## Required configuration")

    assert command < caveats, (
        "the guide explains the traps before it gives the command to run, which is the "
        "order somebody skimming at two in the morning reads exactly backwards"
    )


def workload_role_statements() -> list[dict[str, Any]]:
    """Every statement on the GPU workload role, Deny included.

    Deny included because that is where the interesting half now is. A reader that kept only
    the Allows would report the delete this role holds and not the refusal that bounds it,
    which is the more important of the two facts.
    """
    parsed: dict[str, Any] = yaml.safe_load(GPU_ROLES_PATH.read_text(encoding="utf-8"))
    role = parsed["Resources"]["BatchGpuWorkloadRole"]["Properties"]
    return [
        statement
        for policy in role["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
    ]


def _actions_of(statement: dict[str, Any]) -> set[str]:
    action = statement.get("Action", [])
    return {action} if isinstance(action, str) else set(action)


def _resources_of(statement: dict[str, Any]) -> set[str]:
    """The ARNs a statement names, with ``Fn::Sub`` unwrapped to the string inside it.

    Every resource in this template is written as a ``Fn::Sub`` so that the partition comes
    from the stack rather than being hard-coded, which means the plain YAML load hands back a
    one-key mapping rather than a string.
    """
    resource = statement.get("Resource", [])
    listed = [resource] if isinstance(resource, (str, dict)) else list(resource)
    return {one["Fn::Sub"] if isinstance(one, dict) else one for one in listed}


def test_the_prune_trap_is_named_and_the_refusal_it_assumes_still_happens(
    olmo_core_guide: str,
) -> None:
    """Mutation: grant the delete without the deny, and leave the guide saying a prune fails.

    ``CheckpointerCallback.max_checkpoints`` defaults to 3 and counts permanent checkpoints
    only, which means the one written at step 0 plus those at ``save_interval`` and
    ``fixed_steps``. The fourth of those schedules the oldest for removal, and the removal
    runs at the top of the following ``post_train_batch``. It deletes the step directory's
    ``.metadata.json`` first, through ``remove_file``, which is a single ``s3:DeleteObject``
    and is the call the role is refused.

    THE REFUSAL IS NOT SWALLOWED, WHICH IS WHY THIS IS A TRAP RATHER THAN AN UNTIDY BUCKET.
    ``_s3_remove_file`` re-raises anything that is not a 404, ``@retriable`` treats every
    botocore ``ClientError`` as retriable and so turns it into ``OLMoNetworkError`` after
    three attempts, and ``_remove_checkpoint`` catches only ``FileNotFoundError``. So it
    reaches ``Trainer.fit``, which records it and re-raises. The directory clear that would
    have run next does swallow the same refusal, so the order of the two is what decides
    whether the run dies or merely leaks objects.

    It fires early, not late. At ``--save-interval 200`` the fourth permanent checkpoint is
    step 600, which on one A10G is a bit over an hour in.

    **WHAT THIS ASSERTED UNTIL THE ROLE GAINED A DELETE, AND WHY THE CONCLUSION SURVIVED.**
    It read every action on the role and required that none of them be a delete, which was
    the same assertion while the role held no delete at all. It does now, scoped to
    ``checkpoints/*``, because a retry has to rewrite the step directory its own lost attempt
    tore -- and the mechanism the guide describes is unchanged, because the prune's first
    call is a delete of ``.metadata.json`` and that key is denied by name.

    So the assertion moved from "no delete exists" to "this delete is refused", which is the
    thing the paragraph actually claims. The wider reading would now be satisfied by
    withdrawing the repair's grant, and the narrower one is satisfied only by the refusal the
    guide says a person will hit.
    """
    assert "max_checkpoints" in olmo_core_guide, (
        "the guide no longer names the setting that stops OLMo-core pruning"
    )
    assert "max_checkpoints=null" in olmo_core_guide, (
        "the guide names the setting without giving the value that disables the prune"
    )

    denied = {
        resource
        for statement in workload_role_statements()
        if statement.get("Effect") == "Deny"
        for resource in _resources_of(statement)
        if "s3:DeleteObject" in _actions_of(statement)
    }

    assert any(resource.endswith("/checkpoints/*/.metadata.json") for resource in denied), (
        "nothing denies the GPU workload role a delete of .metadata.json, so a prune left "
        "at OLMo-core's default of three now succeeds and deletes a finished checkpoint. "
        "The guide's paragraph describes a refusal that would no longer happen: either "
        "restore the deny or rewrite the paragraph, but do not leave the two disagreeing"
    )


def test_the_save_interval_is_named_against_the_contract_the_workload_declares(
    olmo_core_guide: str,
) -> None:
    """Mutation: move the checkpoint contract to 60 minutes, leave the guide saying 30.

    ``--save-interval`` is the flag that decides what a lost machine costs, and the resume
    section is the only place a reader is told so. The figure it tells them to come in under
    is the workload's own ``interval_minutes``, so it is read out of the catalog rather than
    copied here, and it is asserted inside the paragraph that names the flag rather than
    anywhere in the document. A guide that cites 30 while the contract says 60 is telling
    people to checkpoint twice as often as they have to, and one that cites 60 against a
    contract of 30 is worse.

    The measured figures beside it, 3.2 GB and 40 seconds and 23 minutes, are observations
    of one model on one machine. Nothing in this repository can check them and this test
    does not pretend to.
    """
    catalog = load_yaml(PROJECT_ROOT / "config" / "workload-catalog.yaml", WorkloadCatalog)
    contracts = {
        workload.name: workload.checkpoint
        for workload in catalog.workloads
        if workload.checkpoint is not None
    }
    declared = contracts["olmo-core-train"].interval_minutes

    paragraphs = [block for block in olmo_core_guide.split("\n\n") if "--save-interval" in block]

    assert paragraphs, (
        "the guide no longer names --save-interval, so nothing tells a reader which flag "
        "decides how much work a lost machine throws away"
    )
    assert any(str(declared) in block for block in paragraphs), (
        f"olmo-core-train declares a checkpoint every {declared} minutes and no "
        "paragraph naming --save-interval cites that number, so the advice and the "
        "contract it rests on can drift apart without anything saying so"
    )


def test_the_evaluator_trap_names_both_callbacks(olmo_core_guide: str) -> None:
    """Mutation: name one evaluator and leave the other on.

    TWO CALLBACKS, TWO UNRELATED CAUSES, AND THE GUIDE USED TO GIVE ONE CAUSE FOR BOTH.
    ``lm_evaluator`` builds a ``NumpyPaddedFSLDataset`` over a C4 validation shard on
    ``olmo-data.org``, which needs the ``.csv.gz`` of document offsets sitting beside the
    ``.npy``. That file 404s, while ``c4-train.00000-00099.csv.gz`` in the same directory
    returns 200, so the shape of the failure is a file nobody published rather than a
    protocol or a container that cannot reach the internet. The observed error is
    ``RuntimeError: Source metadata file 'c4-validation.00000-00008.csv.gz' is required to
    calculate document indices``.

    ``downstream_evaluator`` fails on ``from olmo_eval import HFTokenizer``.
    ``ai2-olmo-eval`` is OLMo-core's ``eval`` extra and the training image installs
    ``.[wandb]`` and ``boto3``, so it is not there. Nothing has ever logged it, because
    ``lm_evaluator`` is built first and ends the process.

    What survives that correction is the reason both are named: they fail during trainer
    construction rather than at the first eval interval, so a guide that disables one sends
    the reader back to a crash seconds later with the obvious fix already applied.

    The worked command is asserted separately from the prose. A trap explained in a
    paragraph and missing from the line people paste is a trap that is still set.
    """
    for callback in ("lm_evaluator", "downstream_evaluator"):
        assert f"trainer.callbacks.{callback}.enabled=false" in olmo_core_guide, (
            f"the guide does not tell a reader to disable {callback}"
        )

    # The example is named in prose as well as in the command, so the command is picked out
    # by the `bash -lc` wrapper rather than by the script path alone.
    worked = [
        line
        for line in olmo_core_guide.splitlines()
        if "src/examples/llm/train.py" in line and "bash -lc" in line
    ]
    assert worked, "the guide no longer carries a worked command for the OLMo-core example"
    for callback in ("lm_evaluator", "downstream_evaluator"):
        assert all(f"trainer.callbacks.{callback}.enabled=false" in line for line in worked), (
            f"the guide's worked command does not disable {callback}, so somebody who "
            "copies the command rather than reading the prose still hits it"
        )


def test_the_tmp_trap_is_named_wherever_the_guide_puts_it(olmo_core_guide: str) -> None:
    """The most expensive mistake available here, and the one nothing reports.

    A run that takes OLMo-core's ``/tmp`` default trains for hours, writes checkpoints onto
    a machine that then stops existing, exits zero, and is recorded as an unqualified
    success. ``ResultManifest`` has no field for it and an empty ``checkpoints`` tuple
    already means something else, so the guide is the only place a researcher is warned.

    Asserted by substance rather than position, because where it belongs has already
    changed once: it was the headline instruction, and it is now a reason the entry point
    exists. Both are fine. Its absence is not.
    """
    assert "/tmp" in olmo_core_guide
    assert "recorded as a success" in olmo_core_guide, (
        "the guide no longer says that a run which saved nothing is recorded as a success, "
        "which is the half that makes it a trap rather than an inconvenience"
    )


def test_every_corpus_the_guide_tabulates_is_one_the_form_offers(
    platform_guide: str, workflow: dict[str, Any]
) -> None:
    """The table is a promise in the same way the dropdown is.

    Read against the form rather than the registry, because the registry may hold a corpus no
    workload can construct a tokenizer for — the guide should name what a person can pick.
    """
    tabulated = set(
        re.findall(r"^\| `([a-z0-9][a-z0-9.-]*)` \| [\d.]+B \|", platform_guide, re.MULTILINE)
    )
    offered = set(form_inputs(workflow)["dataset_release"]["options"]) - {"none"}

    assert tabulated, "the guide names no corpus, so either the table or this pattern moved"
    assert tabulated == offered


def test_the_guide_sends_a_reader_who_wants_to_stop_a_run_to_the_button(
    platform_guide: str,
    cancel_workflow: dict[str, Any],
) -> None:
    """Mutation: put back a caveat routing the reader to somebody with a credential.

    Stopping your own run is self-service, and the section that says so is the only place a
    researcher learns it. A caveat here is obeyed long after it stops being true, because
    the person reading it has no way to find out otherwise -- they ask, wait, and conclude
    the button is not theirs.

    Held against the form rather than against prose. The section tells people to tick
    **stop**, so the workflow it names has to offer that input; a rename on either side
    leaves the guide describing a control that is not on the page.
    """
    heading = "## Looking at a run, and stopping one"
    assert heading in platform_guide, "the guide no longer tells anybody how to look at a run"
    section = platform_guide.split(heading, 1)[1].split("\n## ", 1)[0]

    assert "cancel-run.yml" in section, "the section names no workflow to reach for"
    assert "**stop**" in section

    assert "stop" in form_inputs(cancel_workflow), (
        "the guide tells people to tick stop and the workflow it points at has no such "
        "input, so the instruction names a control that is not there"
    )

    for routed_away in ("Not live yet", "ask an admin", "Ask an admin"):
        assert routed_away not in section, (
            f"the section carries {routed_away!r}, which sends a researcher to somebody "
            "else for a run they can stop themselves"
        )


def test_the_guide_names_every_machine_that_needs_a_launcher(olmo_core_guide: str) -> None:
    """Mutation: promote a tenth shape and leave the guide listing the seven it knew.

    A multi-GPU shape the guide does not name is one a researcher meets for the first time
    in a refusal, and the refusal is the wrong place to learn that a whole class of machine
    exists. Read out of ``CONTAINER_SHAPES`` rather than from a list here, because that
    table is what the registered job definition asks Batch for and therefore what is billed.

    Only the multi-GPU direction is asserted. Naming a single-GPU shape in the guide is
    harmless, and requiring the two lists to be equal would fail on the sentence that
    contrasts them.
    """
    from edullm_platform.execution import CONTAINER_SHAPES

    section = olmo_core_guide.split("## Multi-GPU jobs", 1)
    assert len(section) == 2, "the guide no longer has a section about multi-GPU machines"
    body = section[1].split("\n## ", 1)[0]

    needs_a_launcher = {name for name, shape in CONTAINER_SHAPES.items() if shape.gpus > 1}
    unnamed = {name for name in needs_a_launcher if f"`{name}`" not in body}

    assert unnamed == set(), (
        f"the guide's multi-GPU section does not name {sorted(unnamed)}, so somebody who "
        "picks one of them finds out what it needs from a refusal"
    )


def test_the_guides_print_the_way_through_the_launcher_check_verbatim(
    platform_guide: str,
    olmo_core_guide: str,
) -> None:
    """Mutation: change the waiver token and leave the guides printing the old one.

    The waiver is matched exactly and case-sensitively, so a guide that is one character out
    documents a way through that does not work -- and the person following it concludes the
    escape is theoretical and picks a smaller machine instead, which is the outcome the
    escape exists to prevent.

    Both halves, because the convention is explained in one and applied in the other. A
    reader meets the token in whichever they opened first.
    """
    from edullm_platform.launchers import LAUNCH_CHECK_WAIVER

    for name, text in (("the-platform", platform_guide), ("olmo-core", olmo_core_guide)):
        assert LAUNCH_CHECK_WAIVER in text, (
            f"{name}.md no longer prints the token that lets a deliberate single-process "
            "run onto a multi-GPU machine"
        )


def test_the_guide_names_every_shape_whose_card_has_no_bfloat16(olmo_core_guide: str) -> None:
    """Mutation: promote a shape on another Turing family and leave the guide listing three.

    A researcher picks a shape from the table above this section, and the one thing that
    table cannot show is which cards lack a number format. Read out of the catalog and the
    capability map rather than from a list here, so that a fourth shape without bfloat16 is a
    red test rather than a fourth way to find out from a refusal -- or, worse, from a job
    that dies on the device because the dtype was set in code and nothing refused it.
    """
    from edullm_platform.precision import gpu_of

    section = olmo_core_guide.split("## The bfloat16 refusal", 1)
    assert len(section) == 2, "the guide no longer has a section about the bfloat16 refusal"
    body = section[1].split("\n## ", 1)[0]

    catalog = load_yaml(PROJECT_ROOT / "config" / "workload-catalog.yaml", WorkloadCatalog)
    without = {
        profile.name
        for profile in catalog.compute_profiles
        if profile.provisioned
        and (gpu := gpu_of(profile)) is not None
        and not gpu.architecture.supports_bfloat16
    }
    # The same non-vacuity guard tests/test_bfloat16_guard.py makes, restated here because
    # this assertion would otherwise pass against an empty set and prove nothing.
    assert without, "no provisioned shape lacks bfloat16, so this test asserts nothing"

    unnamed = {name for name in without if f"`{name}`" not in body}

    assert unnamed == set(), (
        f"the guide's bfloat16 section does not name {sorted(unnamed)}, so somebody who "
        "picks one of them has no way to know its card cannot run the format"
    )


def test_the_setup_section_does_not_deny_the_credential_two_verbs_need(
    platform_guide: str,
) -> None:
    """Mutation: put "Nothing else." back after uv and gh.

    IT WAS TRUE FOR FIVE VERBS AND FALSE FOR TWO, WHICH IS THE WORST SHAPE A SETUP
    INSTRUCTION CAN HAVE. ``check`` and ``submit`` hold no cloud credential by design and the
    sentence was written about them. ``edullm run`` and ``edullm shell`` shipped afterwards
    and need an AWS session, so the guide went on telling every reader in as many words that
    the two verbs they were about to try required nothing they did not already have. A person
    who believes it reads the refusal as a broken tool rather than as a missing step.

    Held as the absence of the denial and the presence of the command, rather than as a
    paragraph, because how it is worded is nobody's business but the writer's and whether it
    contradicts the platform is everybody's.
    """
    setup = platform_guide.split("## From a terminal", 1)[1].split("\n## ", 1)[0]

    assert "Nothing else." not in setup, (
        "the setup section says nothing beyond uv and gh is needed, which is false for "
        "edullm run and edullm shell -- both need an AWS session and the Session Manager "
        "plugin, and this is the only place a reader is told what setup costs"
    )
    for verb in ("edullm run", "edullm shell"):
        assert verb in setup, (
            f"the section that lists what you need does not mention {verb}, so the two "
            "verbs with a credential requirement are documented nowhere"
        )


def test_the_guide_and_the_refusal_name_the_same_way_to_get_a_session(
    platform_guide: str,
) -> None:
    """Mutation: reword one of the two and leave the other.

    There is one broker in this organization and no long-lived keys, so there is one command,
    and a guide and a refusal that name it differently send a reader looking for a second
    one. Read out of the constant the refusal interpolates rather than typed here, so the
    copy in the guide is held to the copy a person is shown at the moment they need it.
    """
    from edullm_platform.cli.lane import AWS_LOGIN_COMMAND

    assert AWS_LOGIN_COMMAND in platform_guide, (
        f"the guide does not name {AWS_LOGIN_COMMAND!r}, which is what edullm prints when "
        "a lane verb finds no AWS session"
    )


def test_the_guide_does_not_promise_a_size_that_costs_a_download(platform_guide: str) -> None:
    """The largest corpus is 630 GB on a machine with far less disk.

    What makes it usable is that shards are memory-mapped as the loader reaches them, so the
    guide has to say so -- otherwise the sensible reading of the table is that picking the
    157B corpus means waiting for 157B tokens to arrive.
    """
    assert "memory-mapped" in platform_guide
