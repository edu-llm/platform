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
import shlex
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from edullm_platform.cli.actions import ADMITTED, DECLINED, submission_state
from edullm_platform.config import load_yaml
from edullm_platform.contracts.dataset_registry import DatasetRegistry
from edullm_platform.contracts.image_tokenizers import ImageTokenizerRecord
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.corpora import corpora

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GUIDES_DIR = PROJECT_ROOT / "guides"
PLATFORM_GUIDE_PATH = GUIDES_DIR / "the-platform.md"
OLMO_CORE_GUIDE_PATH = GUIDES_DIR / "olmo-core.md"
DAY_ONE_GUIDE_PATH = GUIDES_DIR / "day-one.md"
README_PATH = PROJECT_ROOT / "README.md"
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "submit-run.yml"
CANCEL_WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "cancel-run.yml"
GPU_ROLES_PATH = PROJECT_ROOT / "infra" / "iam" / "batch-gpu-roles.yaml"
POLICY_PATH = PROJECT_ROOT / "config" / "policy.yaml"
CAPACITY_PATH = PROJECT_ROOT / "config" / "capacity.yaml"
CONFIG_DIR = PROJECT_ROOT / "config"
CATALOGUE_PATH = CONFIG_DIR / "workload-catalog.yaml"
ACCELERATORS_PATH = CONFIG_DIR / "accelerators.yaml"
INFRA_README_PATH = PROJECT_ROOT / "infra" / "README.md"
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"


def catalogue() -> WorkloadCatalog:
    return load_yaml(CATALOGUE_PATH, WorkloadCatalog)


def released_version() -> str:
    """The version somebody who runs the install line in day one will end up holding.

    Read out of ``pyproject.toml`` rather than out of the installed distribution, because a
    contributor's environment is an editable install of this tree and the number a guide has
    to be true about is the one this tree publishes.
    """
    found = re.search(r"^version = \"([^\"]+)\"", PYPROJECT_PATH.read_text(encoding="utf-8"), re.MULTILINE)
    assert found is not None, "pyproject.toml declares no version"
    return found.group(1)


def researcher_facing() -> dict[str, str]:
    """Every committed page a researcher is sent to, keyed by name.

    Globbed rather than listed, so a sixth guide is covered by the tests below on the day
    it is added rather than on the day somebody remembers to name it here. ``day-one.md``
    was the fifth and arrived carrying two claims that were already false.
    """
    pages = {path.name: path.read_text(encoding="utf-8") for path in sorted(GUIDES_DIR.glob("*.md"))}
    pages[README_PATH.name] = README_PATH.read_text(encoding="utf-8")
    return pages


def fenced_blocks(text: str) -> list[str]:
    return re.findall(r"^```\n(.*?)^```$", text, re.MULTILINE | re.DOTALL)


def _in_words(count: int) -> str:
    """A small count as the guides write it, because they write numbers as words.

    Only as far as the catalogue can reach. A twenty-first profile should fail here rather
    than pass with a digit the pages do not use.
    """
    words = {
        11: "eleven",
        12: "twelve",
        13: "thirteen",
        14: "fourteen",
        15: "fifteen",
        16: "sixteen",
        17: "seventeen",
        18: "eighteen",
        19: "nineteen",
        20: "twenty",
    }
    assert count in words, f"nothing here spells {count}; extend this or use a digit"
    return words[count]


def to_cents(rate: Decimal) -> str:
    """A catalogue rate as the guides write it, rounded the way money is rather than the
    way :func:`round` is. ``0.5260`` is ``0.53`` here and ``0.52`` under banker's rounding.
    """
    return str(rate.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@pytest.fixture(scope="module")
def platform_guide() -> str:
    return PLATFORM_GUIDE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def day_one_guide() -> str:
    return DAY_ONE_GUIDE_PATH.read_text(encoding="utf-8")


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


def test_the_guide_sends_a_reader_to_the_verb_rather_than_tabulating_the_corpora(
    platform_guide: str, workflow: dict[str, Any]
) -> None:
    """**THE TABLE THIS REPLACES WAS CORRECT AND THAT WAS ITS WHOLE PROBLEM.**

    Mutation: put the table back, or leave a shorter one behind as a convenience.

    Sixteen rows of name and token count, every number right on the day it was typed. What
    held them was this test, and this test only compared the *names* against the submission
    form's dropdown -- so adding a corpus went red and somebody typed a row, and re-sealing
    one at a new size went green for ever. The numbers were held by nothing, in the one place
    a researcher would read them.

    A table also cannot carry the column that matters. Eight registered corpora are current
    and refused by nothing and reach a container that exits 69, and which eight is a join over
    ``config/datasets.yaml``, ``edullm_platform.tokenizers.TOKENIZERS`` and
    ``config/image-tokenizers.yaml`` that changes on its own the day a published image is read
    again. A page cannot recompute itself.

    So the assertion moves with the answer: the section names the verb, and no row of the
    shape the table used to have survives anywhere on the page. Both halves are needed --
    naming the verb while leaving the rows would be two answers, which is how they disagree.
    """
    tabulated = re.findall(
        r"^\| `([a-z0-9][a-z0-9.-]*)` \| [\d.]+B \|", platform_guide, re.MULTILINE
    )
    heading = "## The corpora"

    assert heading in platform_guide, "the guide no longer tells anybody what corpora exist"
    section = platform_guide.split(heading, 1)[1].split("\n## ", 1)[0]
    assert "edullm data" in section, (
        "the corpora section names no verb, so a reader has nowhere to go; edullm data is "
        "the only route to the list that carries a size, a tokenizer and a licence"
    )
    assert not tabulated, (
        f"the guide tabulates {tabulated} again. Those numbers are held by nothing here, "
        "which is what the table was deleted for, and a second answer beside edullm data is "
        "how the two come to disagree"
    )
    # And the verb it names is one a reader can actually pick a corpus off, which is the
    # promise the dropdown comparison used to make. Asked of the registry through the same
    # join the verb uses, rather than of the form, because the verb's whole point is that it
    # answers for the registered corpora the form cannot show.
    offered = set(form_inputs(workflow)["dataset_release"]["options"]) - {"none"}
    registry = load_yaml(PROJECT_ROOT / "config" / "datasets.yaml", DatasetRegistry)
    images = load_yaml(PROJECT_ROOT / "config" / "image-tokenizers.yaml", ImageTokenizerRecord)
    runnable = {
        row.reference_id for row in corpora(registry, images=images) if row.runnability.will_run
    }

    assert runnable == offered, (
        "edullm data and the submission form disagree about which corpora will run, so the "
        "page the guide now points at is not the page the form is offering"
    )


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


def test_both_guides_name_both_prerequisites_and_which_is_checked_first(
    platform_guide: str, day_one_guide: str
) -> None:
    """**A REFUSAL IS SOMETHING YOU HIT AND A GUIDE IS SOMETHING THAT STOPS YOU HITTING IT.**
    Mutation: drop the plugin from either guide, or drop the word "first".

    Two people walked the CLI on 2026-08-06, one on macOS and one on Windows, and the first
    `edullm run` either of them ever attempted refused twice on two different prerequisites.
    Neither guide named both, and `guides/day-one.md` named neither. That is the larger half
    of the defect, because the refusals are where somebody already blocked finds out and the
    guides are where somebody preparing could have avoided it.

    **THE ORDER IS THE FACT AND NOT MERELY THE PAIR.** `cli/main.py`'s `_lane_session`
    checks the plugin before it calls `sts:GetCallerIdentity`, so a person who settles the
    session and not the plugin meets the plugin's refusal on the next attempt, having
    believed they were finished. Both guides have to say which comes first.

    **WHAT IS DELIBERATELY NOT ASSERTED IS AN INSTALL COMMAND IN EITHER GUIDE.** AWS
    publishes five and which one a reader wants depends on their operating system and their
    processor. A guide cannot know that and the refusal can, so the commands live in
    `lane.plugin_install_commands` alone. Two copies of an install line is two things to keep
    true against a URL AWS owns.
    """
    from edullm_platform.cli.lane import AWS_LOGIN_COMMAND, SESSION_PLUGIN

    for name, guide in (("the-platform.md", platform_guide), ("day-one.md", day_one_guide)):
        readable = guide.replace(SESSION_PLUGIN, "Session Manager plugin")
        assert "Session Manager plugin" in readable, (
            f"{name} does not mention the Session Manager plugin, which is the first of the "
            "two things edullm run refuses without"
        )
        assert AWS_LOGIN_COMMAND in guide, f"{name} does not name the way to get a session"
        assert "first" in guide, (
            f"{name} does not say which of the two prerequisites is checked first, so a "
            "reader who fixes them in the other order meets a second refusal"
        )


def test_no_guide_carries_a_plugin_install_command_the_refusal_already_prints(
    platform_guide: str, day_one_guide: str
) -> None:
    """Mutation: paste the macOS or Windows installer into either guide.

    The refusal knows the operating system and the processor and prints the one line that
    reader needs. A guide knows neither, so a copy there is either all five of AWS's
    installers or the wrong one, and in both cases it is a second thing to keep true when
    AWS moves a URL. This is the assertion behind that choice rather than a note about it.
    """
    from edullm_platform.cli.lane import PLUGIN_DOWNLOADS

    for name, guide in (("the-platform.md", platform_guide), ("day-one.md", day_one_guide)):
        assert PLUGIN_DOWNLOADS not in guide, (
            f"{name} carries an installer URL. The refusal prints the one for the machine "
            "the reader is actually on, and this copy will rot separately"
        )


def test_the_guide_does_not_promise_a_size_that_costs_a_download(platform_guide: str) -> None:
    """The largest corpus is 630 GB on a machine with far less disk.

    What makes it usable is that shards are memory-mapped as the loader reaches them, so the
    guide has to say so -- otherwise the sensible reading of the table is that picking the
    157B corpus means waiting for 157B tokens to arrive.
    """
    assert "memory-mapped" in platform_guide


# THE SEVEN BELOW HOLD NUMBERS RATHER THAN NAMES, AND THAT IS THE GAP THEY CLOSE.
#
# Every assertion above this line reads an identifier out of a guide and asks whether the
# platform still has one by that name. None of them can see a figure. On 2026-08-06 a red
# team found six numbers in these pages that disagreed with what the tool answers, one of
# them by a factor of a hundred, and the whole suite was green throughout: the approval
# bound had gone from $5 to $500, a rate ceiling that routed runs to an admin had been
# deleted, and two compute profiles the guides priced had stopped being startable. A name
# check cannot catch any of that, because every name involved was still correct.
#
# So these read the figure and the tool together. Where a guide quotes what a command
# printed, the command is driven here and the quote has to be in what came back.

#: The submission ``guides/the-platform.md`` quotes ``edullm check`` against. Spelled out
#: rather than parsed back out of the block, because a test that derived the inputs from
#: the thing it is checking would agree with any block at all.
QUOTED_CHECK_COMMIT = "9ea6d144f89c0000000000000000000000000000"
QUOTED_CHECK_WORKLOAD = "olmo-core-check"
QUOTED_CHECK_COMPUTE = "gpu-1xt4"
QUOTED_CHECK_COMMAND = "bash -lc 'python .edullm/time_attention.py \"$EDULLM_RUN_ID\"'"


def test_the_check_block_the_guide_quotes_is_the_block_check_prints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE ONE THE OTHER SIX EXIST BECAUSE OF. Mutation: reword any line of the sample.

    ``guides/the-platform.md`` prints what ``edullm check`` said about one submission, and
    that block was wrong for long enough that nobody knows when it stopped being right. It
    quoted a $25.25 ceiling on a run the guide had described as one hour, an approval line
    reading ``routine -> run-approval-lead``, and a sentence about moving a *short* run
    under the bound. By 2026-08-06 the tool put the total on the heading line, wrote the
    approval as one sentence naming the bound, and had stopped saying "short". Every test
    in this file stayed green, because every identifier in the block was still real.

    The CLI is driven here over the same submission, through ``tests/cli_support``, which
    reaches no network and no AWS. The guide's block has to be a contiguous substring of
    what came back. Contiguous rather than line by line, so a reordering fails too, and a
    substring rather than an equality because the guide cuts the first line, which names
    a configuration directory that is different on every machine.

    ``what it has taken`` is held with everything else. It is measured over
    ``config/run-history.json``, which is committed, so regenerating that file turns this
    red and the fix is to paste the new block into the guide. That is the intended cost. A
    guide quoting a median nobody has recomputed is the same defect in a smaller size.
    """
    from tests.cli_support import FakeRunner, git_answers, invoke, write_spec

    write_spec(
        tmp_path,
        workload=QUOTED_CHECK_WORKLOAD,
        compute=QUOTED_CHECK_COMPUTE,
        command=QUOTED_CHECK_COMMAND,
    )
    runner = FakeRunner(
        git_answers(tmp_path, repository="OLMo-core", commit=QUOTED_CHECK_COMMIT)
    )
    code, printed, _ = invoke(
        ["check", "--experiment", "onboarding", "--dataset", "none", "--team", "scratch"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )
    assert code == 0, f"the submission the guide quotes is now refused:\n{printed}"

    quoted = [block for block in fenced_blocks(platform_guide_text()) if block.startswith("manifest\n")]
    assert len(quoted) == 1, (
        "the platform guide no longer carries exactly one fenced block starting with the "
        "manifest that edullm check prints, so there is nothing here to hold against the "
        "tool. Either restore the sample output or delete this test deliberately"
    )

    assert quoted[0].rstrip("\n") in printed, (
        "the sample edullm check output in guides/the-platform.md is not what edullm check "
        "prints for that submission any more. What it prints today is below. Paste it in, "
        "dropping the first line, which names a directory that differs per machine.\n\n"
        f"{printed}"
    )


def platform_guide_text() -> str:
    return PLATFORM_GUIDE_PATH.read_text(encoding="utf-8")


def test_the_bound_the_guides_quote_is_the_bound_the_tool_prints() -> None:
    """Mutation: leave $5 in the approval paragraph after policy raises it.

    That is not hypothetical, it is what happened. ``automatic_below_cost_usd`` went from
    ``"5"`` to ``"500"`` and ``guides/the-platform.md`` went on saying that a run under
    five dollars starts on its own, so a researcher pricing an ordinary training run
    believed they needed a lead for it and either padded the request or asked for an
    approval nobody had to give.

    Held against the string the CLI itself renders rather than against a number formatted
    here, so a bound that gains a decimal place moves the guide and the terminal together.

    Aimed at the sentence that makes the claim rather than at the section, and that is the
    whole strength of it. The section also carries the boundary either side of the bound,
    a worked example, and the figure it used to be, so a test asking only whether $500
    appears somewhere passes a page that has one right mention and one wrong one. The
    sentence saying nobody releases a run is the one a reader acts on, and every figure in
    it has to be the bound.
    """
    from edullm_platform.cli.presentation import plain_decimal
    from edullm_platform.contracts.policy import ApprovalPolicy

    policy = load_yaml(POLICY_PATH, ApprovalPolicy)
    written = f"${plain_decimal(policy.thresholds.automatic_below_cost_usd)}"

    approval = platform_guide_text().split("\n## Approval", 1)
    assert len(approval) == 2, "the platform guide no longer has a section about approval"
    section = approval[1].split("\n## ", 1)[0]

    assert written in section, (
        f"the approval section does not name {written}, which is the figure "
        "config/policy.yaml draws the line at and the figure edullm check prints beside "
        "the word automatic. A guide naming a different one tells people to wait for an "
        "approval nobody has to give"
    )

    claims = [block for block in section.split("\n\n") if "nobody releases it" in block]
    assert len(claims) == 1, (
        "the approval section no longer has exactly one paragraph saying which runs "
        "nobody releases, so there is nothing here to pin the figure to"
    )
    assert set(re.findall(r"\$[\d,]+(?:\.\d+)?", claims[0])) == {written}, (
        f"the sentence that tells a reader when nobody releases a run quotes "
        f"{sorted(set(re.findall(r'[$][\\d,]+(?:[.]\\d+)?', claims[0])))} and the bound is "
        f"{written}. That sentence went on saying $5 for as long as the bound was $500"
    )


def test_no_guide_offers_a_compute_profile_that_cannot_be_started() -> None:
    """Mutation: withdraw a profile in the catalogue and leave the guides pricing it.

    Both H100 shapes went ``provisioned: false`` on 2026-08-04 because EC2 has never sold
    this account a p5 of either size. Five researcher-facing pages went on offering eight
    H100s at $55.04 an hour and one of them said in as many words that nothing refuses it.
    Something does: ``unprovisioned_compute_profile``, after the reader has picked the
    machine and before anything is dispatched.

    A page may still name one, and should, because the number is the one everybody has
    heard. What it may not do is name one in a table row without saying in that row that
    it is refused, or name one anywhere without the page explaining what unprovisioned
    means. Both halves are asserted, because the guides failed the first and passed the
    second by accident.
    """
    withdrawn = {
        profile.name for profile in catalogue().compute_profiles if not profile.provisioned
    }
    assert withdrawn, (
        "every profile in the catalogue is provisioned, so this test asserts nothing. "
        "Delete it or give it a shape it can fail on"
    )

    for name, page in researcher_facing().items():
        named = {profile for profile in withdrawn if f"`{profile}`" in page}
        if not named:
            continue
        assert "unprovisioned" in page, (
            f"{name} names {sorted(named)}, which cannot be started, and the word "
            "unprovisioned appears nowhere on the page. A reader has no way to learn "
            "that the shape is priced and refused"
        )
        for line in page.splitlines():
            if not line.startswith("|"):
                continue
            offered = {profile for profile in withdrawn if f"`{profile}`" in line}
            if not offered:
                continue
            assert "refused" in line, (
                f"{name} has a table row offering {sorted(offered)} beside shapes that "
                f"can be started, and nothing in the row says it is refused:\n  {line}"
            )


def test_the_hourly_range_the_guides_quote_is_the_range_that_can_be_booked() -> None:
    """Mutation: promote or withdraw a profile and leave the range where it was.

    Four pages quoted "$0.53 to $55.04" for months after the top of that range stopped
    being bookable. The floor was right and the ceiling was a shape admission refuses, so
    every reader who sized a budget against it sized it against a machine nobody can have.

    **A range may quote either top, and the priced one costs a sentence.** $30.13 is what
    can be started; $55.04 is what the dropdown offers. A page that gives the priced top
    without saying that the shape cannot be started is the original defect, and a page that
    gives only the placeable top hides a shape somebody will pick anyway -- so the priced
    top is allowed exactly where the page also names the refusal it earns.

    Both ends are read out of the catalogue. Promoting the L40S node or withdrawing the H100
    one moves a number here rather than leaving four pages quoting history.
    """
    profiles = catalogue().compute_profiles
    startable = sorted(p.hourly_rate_usd for p in profiles if p.provisioned)
    assert startable, "no profile is provisioned, so this test asserts nothing"
    floor, ceiling = to_cents(startable[0]), to_cents(startable[-1])
    priced = to_cents(max(p.hourly_rate_usd for p in profiles))

    patterns = (
        r"\$(\d+\.\d\d) an hour to \$(\d+\.\d\d)",
        r"between \$(\d+\.\d\d) and \$(\d+\.\d\d) an hour",
    )
    found = 0
    for name, page in researcher_facing().items():
        for pattern in patterns:
            for low, high in re.findall(pattern, page):
                found += 1
                assert low == floor, (
                    f"{name} starts a range at ${low} an hour, and the cheapest shape in "
                    f"config/workload-catalog.yaml is ${floor}"
                )
                if high == priced and priced != ceiling:
                    assert "unprovisioned_compute_profile" in page, (
                        f"{name} tops a range at ${high} an hour, which is a shape that "
                        "cannot be started, and the page never names the refusal that "
                        f"answers it. Say so, or quote ${ceiling}"
                    )
                    continue
                assert high == ceiling, (
                    f"{name} quotes a range of ${low} to ${high} an hour. What can "
                    f"actually be started today is ${floor} to ${ceiling}, and what is "
                    f"priced runs to ${priced}. Neither is ${high}"
                )

    assert found, (
        "no guide quotes an hourly range in either shape this reads, so the assertion "
        "above ran against nothing. Either a range was reworded, in which case teach this "
        "the new shape, or they were all removed and this test should go"
    )


def test_the_placement_column_says_what_the_measurements_say() -> None:
    """Mutation: a probe re-measures a shape and the guide's column stays where it was.

    ``config/capacity.yaml`` is the only place that records whether a machine arrives, and
    it is measured by asking EC2 for one instance rather than inferred from the family.
    Three provisioned shapes read ``unreliably``, meaning a probe asked and got nothing,
    and one of them logged 4,060 refusals in a day without producing a single instance.
    A researcher picking off the guide's table needs that column to be the file's column.

    ``**refused**`` in the guide is the other answer, and it means the catalogue says the
    shape is not provisioned at all rather than that a probe was slow.
    """
    from edullm_platform.placement import read_capacity

    measured = {record.profile: record.places for record in read_capacity(CAPACITY_PATH)}
    provisioned = {profile.name: profile.provisioned for profile in catalogue().compute_profiles}
    spelled = {"reliably": "reliably", "after a wait": "after_a_wait", "unreliably": "unreliably"}

    rows = re.findall(
        r"^\| `([a-z0-9-]+)` \|.*\| \$[\d.]+/hr \| ([^|]+) \|$",
        OLMO_CORE_GUIDE_PATH.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert rows, (
        "the training guide's profile tables no longer carry a placement column in the "
        "shape this reads, so nothing here is checked"
    )

    seen = set()
    for profile, column in rows:
        answer = column.strip()
        assert profile in provisioned, f"the guide tabulates {profile}, which is in no catalogue"
        if answer == "**refused**":
            assert not provisioned[profile], (
                f"the guide's table says {profile} is refused and the catalogue has it "
                "provisioned, so the page is telling people to avoid a machine they can have"
            )
            seen.add("refused")
            continue
        assert provisioned[profile], (
            f"the guide's table offers {profile} with a placement of {answer!r} and the "
            "catalogue reads provisioned: false, so a submission naming it is refused "
            "whatever a probe found"
        )
        assert answer in spelled, (
            f"the guide gives {profile} a placement of {answer!r}, which is not one of "
            f"{sorted(spelled)}"
        )
        assert measured[profile] == spelled[answer], (
            f"the guide says {profile} places {answer!r} and config/capacity.yaml records "
            f"{measured[profile]!r}. The file is the measurement and the guide is the copy"
        )
        seen.add(answer)

    assert {"reliably", "unreliably", "refused"} <= seen, (
        f"the table only exercises {sorted(seen)}, so this test could pass with the "
        "interesting answers missing from the guide entirely"
    )


def profile_row(name: str) -> str:
    """One profile as the training guide's tables write it, composed from the three files.

    The guide used to type these. Every figure in them lived somewhere a program could read
    -- the rate in the catalogue, the placement in ``config/capacity.yaml``, and since
    ``config/accelerators.yaml`` landed the card and its memory too -- so a typed row was a
    fourth copy that nothing compared against the other three. This is the row those files
    say, and :func:`test_the_profile_tables_are_the_three_config_files_rendered` holds the
    page to it.
    """
    from edullm_platform.accelerators import read_accelerators, record_for
    from edullm_platform.placement import read_capacity

    profile = next(p for p in catalogue().compute_profiles if p.name == name)
    card = record_for(name, accelerators=read_accelerators(ACCELERATORS_PATH))
    assert card is not None, f"config/accelerators.yaml has no entry for {name}"
    places = {record.profile: record.places for record in read_capacity(CAPACITY_PATH)}
    said = {"reliably": "reliably", "unreliably": "unreliably", "after_a_wait": "after a wait"}
    placing = "**refused**" if not profile.provisioned else said[places[name]]
    return (
        f"| `{name}` | {card.devices} x {card.device} | {card.memory_mib_total:,} MiB "
        f"| ${profile.hourly_rate_usd.normalize():f}/hr | {placing} |"
    )


def test_the_profile_tables_are_the_three_config_files_rendered() -> None:
    """Mutation: change any cell of any row, or any figure in any of the three files.

    Held cell by cell rather than column by column, because the columns went wrong
    separately and for different reasons. The memory column said "24 GB" for a card that
    reports 22,888 MiB, which is the same quantity and the wrong number to size a batch
    against. The rate column was rounded to the cent, so ``gpu-8xl4`` read $13.35 for
    $13.3504 and the four-figure rates all looked like round numbers somebody chose. The
    device count and the card were prose nothing could check at all until
    ``config/accelerators.yaml`` existed.

    ``normalize()`` on the rate is what the catalogue's own renderer does, so $0.526 stays
    three places and $55.04 stays two rather than every rate being padded to four.
    """
    guide = OLMO_CORE_GUIDE_PATH.read_text(encoding="utf-8")
    # Every compute profile is named `<device>-<shape>`, so the prefix is what separates a
    # profile row from the workload and flag tables that also lead with a backticked cell.
    tabulated = re.findall(r"^\| `((?:gpu|cpu)-[a-z0-9-]+)` \|.*\|$", guide, re.MULTILINE)
    priced = {profile.name for profile in catalogue().compute_profiles}

    assert len(tabulated) >= 16, (
        f"the training guide tabulates {len(tabulated)} profiles, and the catalogue prices "
        "seventeen with one of them a CPU shape. Either a table was cut or this stopped "
        "matching the rows"
    )
    for name in tabulated:
        assert name in priced, (
            f"the training guide tabulates {name!r}, which the catalogue does not price. A "
            "submission naming it is refused as unregistered, which is what a typo earns"
        )
        expected = profile_row(name)
        assert expected in guide, (
            f"the row for {name} disagrees with configuration. It should read:\n\n"
            f"{expected}\n\nRun `uv run python tools/render_profile_table.py` and take the "
            "figures from it rather than editing this row by hand"
        )


def test_the_training_guide_names_every_profile_the_catalogue_prices() -> None:
    """Mutation: register an eighteenth profile and leave the guide at seventeen.

    A shape nobody documents is a shape somebody meets in the dropdown with nothing to read
    about it, and a profile added to the catalogue is exactly the change that never reaches
    prose. The tool this calls is the one the repository already ships for the purpose, so
    the check here and the check a person runs by hand cannot answer differently.
    """
    import sys

    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    try:
        from render_profile_table import profiles_missing_from
    finally:
        sys.path.pop(0)

    missing = profiles_missing_from(
        OLMO_CORE_GUIDE_PATH.read_text(encoding="utf-8"), catalogue().compute_profiles
    )
    assert missing == (), (
        f"guides/olmo-core.md never names {sorted(missing)}. Every priced profile is in the "
        "dropdown, so every priced profile needs a row or a sentence"
    )


def test_the_platform_guide_quotes_the_priced_range_and_the_placeable_one() -> None:
    """Mutation: quote one range and drop the other, whichever one.

    Four pages quoted "$0.53 to $55.04" for months after the top of that range stopped
    being purchasable, which sent people planning eight-H100 runs. Quoting only the
    placeable top is the opposite error and just as wrong: $30.13 as the whole range hides
    that the dropdown offers a $55.04 shape, so the first person to pick it reads the
    refusal as a bug rather than as the thing this page told them about.

    Both numbers are computed here. The catalogue moves and a rate change should fail this
    rather than silently make the page a historical document.
    """
    profiles = catalogue().compute_profiles
    priced = max(profile.hourly_rate_usd for profile in profiles)
    placeable = max(profile.hourly_rate_usd for profile in profiles if profile.provisioned)
    cheapest = min(profile.hourly_rate_usd for profile in profiles)
    assert priced != placeable, (
        "every priced profile can now be started, so there is one range rather than two "
        "and this test is asserting a distinction that no longer exists. Rewrite the page "
        "and then rewrite this"
    )

    section = PLATFORM_GUIDE_PATH.read_text(encoding="utf-8").split("## Choosing a machine", 1)
    assert len(section) == 2, "the platform guide has no 'Choosing a machine' section"
    body = section[1].split("\n## ", 1)[0]

    # The priced range, as a range, rather than $55.04 appearing anywhere in the section.
    # The H100 paragraph below quotes that figure too, so presence proves nothing about
    # whether the range a reader budgets against carries it.
    quoted = re.findall(r"from \$(\d+\.\d\d) an hour to \$(\d+\.\d\d)", body)
    assert quoted == [(to_cents(cheapest), to_cents(priced))], (
        f"'Choosing a machine' quotes {quoted} as the priced range, and the catalogue "
        f"prices ${to_cents(cheapest)} to ${to_cents(priced)}. That is the range the "
        "dropdown offers, and it is the one somebody reads before picking a shape they "
        "cannot have"
    )
    stops = re.search(r"that range stops at \$(\d+\.\d\d)", body)
    assert stops is not None and stops.group(1) == to_cents(placeable), (
        f"'Choosing a machine' does not say the range that can be started stops at "
        f"${to_cents(placeable)}. Without it the page quotes one range and a reader takes "
        "the priced top for a machine they can book"
    )

    startable = sum(1 for profile in profiles if profile.provisioned)
    for count, what in ((len(profiles), "priced"), (startable, "startable")):
        assert f"{_in_words(count)} " in body.lower(), (
            f"'Choosing a machine' never says how many shapes are {what}, and the "
            f"catalogue says {count}. The two counts are what make two ranges read as two "
            "ranges rather than as a contradiction"
        )


def test_the_lane_refusal_the_reference_quotes_is_the_one_the_lane_composes() -> None:
    """Mutation: reword the refusal, or leave the guide's copy where it was.

    The page carried "log in the way you normally do", which the tool stopped saying because
    it is an instruction for somebody who has already done it once. It now names the one
    command that produces a session in this account. A reader comparing a refusal on their
    screen against a different one on the page has to work out which is out of date, and the
    ordinary conclusion is that the tool is broken.

    The AWS line is quoted too. It is the reason the paragraph is four lines rather than two
    and it is what somebody searches for.

    ``opens_a_session=True`` is the ``run`` and ``shell`` form, which is the one the page
    quotes and the one the two people walking the CLI met. ``edullm stop`` composes the same
    paragraph with a different sentence about the plugin, because it checks for none, and
    the page says so in prose beneath rather than quoting a second block nobody would
    diff against the first.
    """
    from edullm_platform.cli.main import _no_aws_session

    said = (
        "aws: [ERROR]: An error occurred (NoCredentials): Unable to locate credentials.\n"
        'You can configure credentials by running "aws login".'
    )
    composed = [
        line
        for line in _no_aws_session(said, opens_a_session=True).splitlines()
        if line.strip()
    ]
    page = PLATFORM_GUIDE_PATH.read_text(encoding="utf-8")
    quoted = {line for block in fenced_blocks(page) for line in block.splitlines()}

    for line in composed:
        assert line in quoted, (
            f"the platform reference does not quote {line!r}, which is a line the lane "
            "prints when it has no credential. Run `edullm run` with no AWS session and "
            "paste what it says"
        )


def test_the_reference_does_not_make_a_researcher_pass_a_flag_the_lane_defaults() -> None:
    """Mutation: make ``--compute`` required again, or write it back into the two rows.

    Four flags before anybody saw a GPU was the friction the default removed, and a table
    that keeps printing the flag teaches the friction back. ``--project`` is the opposite
    case and has to stay in both rows, because nothing but the person knows it.
    """
    from edullm_platform.cli.configuration import load_reviewed_configuration
    from edullm_platform.cli.lane import default_compute_profile

    page = PLATFORM_GUIDE_PATH.read_text(encoding="utf-8")
    rows = [
        line
        for line in page.splitlines()
        if line.startswith(("| `edullm run", "| `edullm shell"))
    ]
    assert len(rows) == 2, f"the verbs table has {len(rows)} lane rows rather than two"

    defaulted = default_compute_profile(load_reviewed_configuration(CONFIG_DIR)) is not None
    for row in rows:
        assert "--project" in row, f"{row!r} drops --project, which the lane requires"
        if defaulted:
            assert "--compute c" not in row, (
                f"{row!r} spells --compute into the command a reader copies, and the lane "
                "picks a shape when the flag is absent. The row teaches a flag nobody needs"
            )


def test_the_guides_name_the_approval_classes_a_submission_can_actually_reach() -> None:
    """Mutation: leave the admin tier in a guide after policy stops routing anything to it.

    ``guides/olmo-core.md`` told people that every profile over $20 an hour needed an
    admin rather than a team lead. Policy v5 deleted that ceiling along with four others,
    and ``classify_request`` has returned two answers ever since. A researcher who wanted
    eight A100s went looking for an approver who does not need to be found, and the run
    they were avoiding starts on its own.

    The reachable set is computed by driving ``classify_request`` rather than read off the
    enum, because the enum still carries the third member on purpose and a test reading it
    would report a tier no submission has taken since v5.
    """
    from edullm_platform.contracts.policy import (
        ApprovalClass,
        ApprovalPolicy,
        RequestFacts,
        classify_request,
    )

    thresholds = load_yaml(POLICY_PATH, ApprovalPolicy).thresholds
    reachable = set()
    for cost in ("0.53", "1000"):
        for fanout in (1, 4):
            for scanned in (True, False):
                reachable.add(
                    classify_request(
                        RequestFacts(
                            claimed_team="scratch",
                            repository_registered=True,
                            dataset_registered=True,
                            dataset_is_a_corpus=True,
                            compute_profile_registered=True,
                            immutable_revision=True,
                            immutable_image=True,
                            image_scan_reviewed=scanned,
                            estimated_cost_usd=Decimal(cost),
                            maximum_runtime_hours=Decimal(1),
                            maximum_attempts=1,
                            fanout_size=fanout,
                        ),
                        thresholds,
                    )
                )

    unreachable = set(ApprovalClass) - reachable
    assert unreachable, (
        "every approval class is reachable now, so the paragraph the guides carry about "
        "there being no admin tier is wrong and this test should be rewritten rather than "
        "deleted"
    )

    approval = platform_guide_text().split("\n## Approval", 1)[1].split("\n## ", 1)[0]
    for reached in sorted(reachable):
        assert f"`{reached.value}`" in approval, (
            f"a submission can be classified {reached.value!r} and the approval section "
            "does not name it, so a researcher meets the word for the first time in the "
            "output of edullm check"
        )
    for missing in sorted(unreachable):
        assert f"`{missing.value}`" in approval and "no submission reaches it" in approval, (
            f"{missing.value!r} is a class nothing routes to and the approval section does "
            "not say so. Two guides sent people looking for an admin for a run that starts "
            "on its own, which is the shape of mistake this sentence prevents"
        )


def test_each_repository_guide_tabulates_every_workload_registered_for_it() -> None:
    """Mutation: register a second entry and leave the guide saying there is one.

    ``guides/olmo-eval-full.md`` said ``olmo-eval-check`` was "the only entry this
    repository has" while the catalogue also carried ``olmo-eval-sweep`` at two hours. A
    person whose eval needs two hours read that they had to ask for something, and the
    thing they needed was already on the form.

    The guide is matched to its repository by filename, which is how the three of them are
    already named, and the section is the one headed with the profiles.
    """
    registered: dict[str, set[str]] = {}
    for workload in catalogue().workloads:
        registered.setdefault(workload.repository.lower(), set()).add(workload.name)

    checked = 0
    for path in sorted(GUIDES_DIR.glob("*.md")):
        owned = registered.get(path.stem.lower())
        if owned is None:
            continue
        page = path.read_text(encoding="utf-8")
        halves = page.split("\n## Workload profiles", 1)
        if len(halves) != 2:
            continue
        section = halves[1].split("\n## ", 1)[0]
        checked += 1
        named = {entry for entry in owned if f"`{entry}`" in section}
        assert named == owned, (
            f"{path.name} tabulates {sorted(named)} and config/workload-catalog.yaml "
            f"registers {sorted(owned)} against {path.stem}. A researcher who needs the "
            "missing one is told to ask for something they can already pick"
        )

    assert checked >= 2, (
        f"only {checked} guide(s) were matched to a repository, so this compared almost "
        "nothing. Either a guide was renamed away from its repository or the section "
        "heading moved"
    )


def test_no_guide_denies_a_notification_channel_the_infrastructure_record_says_exists() -> None:
    """Mutation: put "Nothing sends them yet" back into day one.

    ``guides/day-one.md`` was written on 2026-08-06 saying the webhook the notifier posts
    to had never been supplied and that no message had been read end to end. It had been
    supplied the previous day, ``infra/README.md`` said so under "It already exists" the
    whole time, and messages were posted through the deployed function within hours of the
    guide landing. So the one page a newcomer reads first told thirty-five people that a
    working notification does not work, and pointed them at polling a command whose state
    never changes.

    Held as a contradiction between two committed documents rather than as a fact about
    Slack, because Slack is not observable from a test and a disagreement between these
    two files is. It fails in both directions.
    """
    infra = INFRA_README_PATH.read_text(encoding="utf-8")

    assert "sbsandbox-intern-edullm-runs-webhook" in infra, (
        "infra/README.md no longer records the webhook secret, so the two documents cannot "
        "be held against each other. Restore the record, or rewrite this test against "
        "whatever replaced it"
    )
    supplied = "**It already exists.**" in infra

    denials = (
        "Nothing sends them yet",
        "has never been supplied",
        "No notification is delivered",
        "why you will not get one",
    )
    for name, page in researcher_facing().items():
        said = [phrase for phrase in denials if phrase in page]
        assert not (supplied and said), (
            f"{name} says {said}, and infra/README.md records the webhook as already "
            "created and pointing at #edullm-runs. One of the two is stale and it is not "
            "the one under infra/"
        )


def test_the_two_notification_lines_day_one_shows_are_lines_the_notifier_composes() -> None:
    """Mutation: reword any clause in ``notifications/messages``, or the guide's copy of it.

    Day one shows a researcher what to look for in ``#edullm-runs``, one succeeded line and
    one failed line, and those lines are the only description anybody gets of a message they
    are told to wait for rather than poll. The page carried them with a ``[runs]`` prefix
    the notifier does not write: the channel is a field on the message and never a prefix on
    its text, so a reader scanning the channel was looking for the wrong first character.

    Held by rendering both from the composer rather than by reading the words back. The
    facts are hand-built so the figures stay the ones the page shows, which makes this an
    equality on the wording and on nothing else. Money, duration and the checkpoint clause
    all come out of that module, so any of them being reworded fails here.
    """
    from edullm_platform.notifications.facts import RunEndedFacts
    from edullm_platform.notifications.messages import render_run_ended

    def facts(**overridden: Any) -> RunEndedFacts:
        settled: dict[str, Any] = {
            "run_id": "run_019fa73d-be37-7066-984b-a4bacf194f49",
            "outcome": "succeeded",
            "person": "Aryan Verma",
            "team": "pre-training",
            "experiment": "plan-b-phase0-100m-superbpe-eval",
            "queue_name": None,
            "compute_profile": "gpu-1xa10g",
            "hourly_rate_usd": Decimal("1.006"),
            "seconds_spent": 60,
            "spent_usd": Decimal("0.02"),
            "authorised_usd": Decimal("2.01"),
            "exit_code": None,
            "output_prefix": None,
            "cells_total": None,
            "cells_failed": None,
            "cells_succeeded": None,
            "cells_measured": None,
            "failed_cell_indexes": None,
            "checkpoint_state": "unknown",
        }
        settled.update(overridden)
        return RunEndedFacts(**settled)

    succeeded = render_run_ended(facts()).text
    failed = render_run_ended(
        facts(
            outcome="failed",
            seconds_spent=42 * 60,
            spent_usd=Decimal("0.70"),
            authorised_usd=None,
            exit_code=1,
        )
    ).text

    day_one = DAY_ONE_GUIDE_PATH.read_text(encoding="utf-8")
    # Whole lines of a fenced block rather than a substring of the page, so a prefix in
    # front of the message fails. That was the defect: `[runs] ` reads as part of what
    # arrives, and a substring check cannot see it.
    quoted = {row for block in fenced_blocks(day_one) for row in block.splitlines()}
    for line in (succeeded, failed):
        assert line in quoted, (
            "day-one.md does not show the line the notifier composes. It now reads:"
            f"\n\n{line}\n\nPut that in the notification block on a line of its own, with "
            "nothing in front of it, or the one page a newcomer reads describes a message "
            "that is not the one arriving"
        )


def test_the_version_day_one_makes_a_reader_check_for_is_one_they_can_install() -> None:
    """Mutation: raise the floor in day one above the version this repository releases.

    The quoting bug shipped in the tool rather than on the platform, so merging the fix
    repaired nobody: an install at 3.4.7 keeps sending unquoted commands until the person
    holding it runs ``uv tool install --force`` again. Day one therefore makes a reader read
    ``edullm --version`` back and names a floor, and a floor is a number that goes stale in
    two ways. Above the released version it sends everybody chasing a tool that does not
    exist. Below the release that fixed the thing it is not a floor at all, which is why the
    lower bound here is the release the fix went out in rather than anything softer.
    """
    pages = researcher_facing()
    floors = {
        name: set(re.findall(r"read \*{0,2}(\d+\.\d+\.\d+) or higher", page))
        for name, page in pages.items()
    }

    assert floors[DAY_ONE_GUIDE_PATH.name], (
        "day-one.md names no version floor, so a reader with a 3.4.7 install is told to "
        "run --version and given nothing to compare it against"
    )
    named = set().union(*floors.values())
    assert len(named) == 1, (
        f"the pages name more than one floor between them, {sorted(named)}: "
        f"{ {name: sorted(found) for name, found in floors.items() if found} }. A reader "
        "who follows two of these cannot tell which install is good enough"
    )

    floor = tuple(int(part) for part in next(iter(named)).split("."))
    released = released_version()
    assert floor >= (3, 4, 8), (
        f"the guides ask for {next(iter(named))} or higher, which is below 3.4.8. 3.4.8 is "
        "the release that stopped submit unquoting the command, so a lower floor clears an "
        "install that still has the bug"
    )
    assert floor <= tuple(int(part) for part in released.split(".")), (
        f"the guides ask for {next(iter(named))} or higher and this repository releases "
        f"{released}, so nobody following the install line can satisfy it"
    )


def test_the_refusal_day_one_quotes_is_the_refusal_a_stale_install_earns() -> None:
    """Mutation: reword the refusal day one quotes, or the guard that raises it.

    A researcher on 3.4.7 meets this text and nothing else, two minutes after submitting,
    and the recognisable part is what it says rather than that it was a refusal. So the
    block is quoted verbatim and held against the guard that produces it, with the day-one
    command as the input, because that is the command the thirty-five of them will run.

    The advice on the last line is why quoting it matters. It tells them to quote the whole
    program, they already did, and the tool took the quotes off between their terminal and
    the form. The guide has to say that, and it can only say it while the words it is
    talking about are the words that arrive.
    """
    from edullm_platform.contracts.validation import require_a_shell_command_that_kept_its_quotes

    day_one = DAY_ONE_GUIDE_PATH.read_text(encoding="utf-8")
    quoted = ["bash", "-lc", 'python .edullm/time_attention.py "$EDULLM_RUN_ID"']

    # What 3.4.7 put on the form, and what the compile job then splits it back into.
    unquoted = shlex.split(" ".join(quoted))
    with pytest.raises(ValueError) as refusal:
        require_a_shell_command_that_kept_its_quotes(unquoted)
    printed = " ".join(str(refusal.value).split())

    blocks = [" ".join(block.split()) for block in fenced_blocks(day_one)]
    assert printed in blocks, (
        "day-one.md does not quote the refusal a 3.4.7 install earns. The guard now says:"
        f"\n\n{printed}\n\nQuote that, wrapped however the page wraps, or the one page a "
        "newcomer reads describes a refusal they will not recognise when it arrives"
    )

    assert require_a_shell_command_that_kept_its_quotes(quoted) == quoted, (
        "the guard now refuses the day-one command as it is actually quoted, which makes "
        "the whole submission path unreachable rather than making this guide wrong"
    )


def states_status_can_print() -> set[str]:
    """Every state ``edullm status`` prints for a submission, driven out of the function.

    Every GitHub status crossed with every conclusion, rather than the handful anybody would
    think to list, so a branch added to :func:`submission_state` is covered here the day it
    lands. ``DECLINED`` is not returned by that function -- a decline and a compile refusal
    reach the runs endpoint identically and only the approvals endpoint separates them -- so
    it is unioned in from the constant the two readers of it share.
    """
    return {
        submission_state({"status": status, "conclusion": conclusion})
        for status in ("queued", "in_progress", "completed", "requested", "waiting", "pending")
        for conclusion in (None, "success", "failure", "cancelled", "skipped", "timed_out")
    } | {ADMITTED, DECLINED}


def test_the_reference_names_every_state_edullm_status_can_print() -> None:
    """Mutation: rename a state in ``actions.py`` and leave the guides where they are.

    **THIS IS THE STALENESS THE PAGES ACTUALLY SUFFERED.** ``guides/day-one.md`` and
    ``guides/the-platform.md`` both taught ``SUBMITTED`` as the word every finished run
    reads, in three places between them, one of which was a row in day one's standing-walls
    table. Both were true when they were written. The moment the word became ``ADMITTED``
    they described a tool nobody had, and a guide that warns a reader about something that
    no longer happens is how a document teaches people to stop believing the rest of it.

    Coverage rather than a forbidden-word list, and that is what makes it catch a rename in
    the direction a rename actually breaks. A state the tool gains and the page does not
    mention fails here; a state the page keeps and the tool has dropped fails here too, by
    the state that replaced it being absent. A list of words not to say would need editing
    every time and would be edited by whoever renamed the state, which is the one person who
    has already forgotten the pages exist.

    ``the-platform.md`` and not every page, because it is the reference and day one is a
    walkthrough. A walkthrough naming three of eight states is doing its job.

    ``UNKNOWN`` is in the set and therefore has to be in the table. It is a real answer --
    GitHub reports a completed run and no conclusion -- and a reader who meets it with no
    entry to look up has met an undocumented word, which is the whole complaint here.
    """
    page = PLATFORM_GUIDE_PATH.read_text(encoding="utf-8")
    missing = sorted(
        state for state in states_status_can_print() if f"`{state}`" not in page
    )

    assert not missing, (
        f"edullm status prints {missing} and guides/the-platform.md names none of them. "
        "The page is the reference for this verb, so a state it does not carry is a word a "
        "researcher meets in their terminal with nowhere to look it up"
    )
