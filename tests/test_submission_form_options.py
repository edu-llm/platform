"""What the submission form offers, held to what the platform will actually accept.

Four of the eight required fields are keys into committed registries, and until now all
eight were free-text boxes. A researcher had to know that ``dolma-2026-07`` is the dataset
id and that ``olmo-core-check-gpu`` is a workload profile, and a typo in either was a
refusal after a human had already approved the submission.

They are dropdowns now, and a dropdown is a promise: everything in this list works. That
promise is what these tests keep. A ``choice`` input's options are static text in the
workflow YAML with nothing behind them, so a registry entry added and not offered is
invisible, and an option offered and not registered is a refusal wearing a menu item.

**The workload list is the sharp one, and it has two ways of being wrong.**
``dolma-tokenize`` is a registered workload naming a repository that nothing
registers -- there is no ECR repository for dolma and no image can be published for it.
``olmo-core-train-4gpu`` is the other shape: its repository is registered, but the compute
profile it inherits is ``gpu-4xa10g``, which the catalog prices, does not call provisioned,
and no execution target backs. Both compile, classify as routine, route to a lead, and are
refused at admission *after* the approval -- the first with ``unregistered_repository``, the
second with ``no_execution_target``. Both are deliberately absent from the dropdown, and the
two tests below are what make each absence a decision rather than an oversight: the moment
dolma is registered, or ``gpu-4xa10g`` is provisioned and given a target, the option has to
appear.

**``team`` is the fourth key and was the last one still open.** It is different from the
other three in what a wrong value costs. An unregistered dataset or workload is refused at
admission, so the submitter finds out; an unrecognised team is refused by nothing. It
reaches the manifest, the immutable decision record, the S3 prefix the run writes under and
the ``edullm:team`` tag its spend is grouped by, and the first reader placed to notice is
somebody wondering why their group's total is short. With eight declared groups there is no
list to be incomplete against, so this one is held to equality with the declaration in both
directions and in order.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import pytest
import yaml

from edullm_platform.config import load_yaml
from edullm_platform.contracts.execution import (
    ExecutionTargetCatalog,
    UnbackedComputeProfileError,
)
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.workload import ComputeProfileResolutionError, WorkloadCatalog
from edullm_platform.execution import resolve_execution_target
from tools.build_gpu_training_submission import TOKENIZERS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "submit-run.yml"

#: How a dropdown spells "leave this empty", because a choice option cannot be blank.
INHERIT = "inherit"

#: AWS's own documented example account, which this repository's secret scan exempts.
#: ``resolve_execution_target`` composes ARNs from whatever account it is handed and this
#: test cares only about whether it can compose them at all, so no real account is needed.
EXAMPLE_ACCOUNT_ID = "123456789012"


def form_inputs() -> dict[str, Any]:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML reads a bare `on:` key as the boolean True, which is a YAML 1.1 rule and a
    # recurring surprise. Both spellings are tried so a future quoting change cannot make
    # every test here silently pass over an empty mapping.
    triggers = document.get(True) or document.get("on")
    return dict(triggers["workflow_dispatch"]["inputs"])


def options_for(field: str) -> list[str]:
    spec = form_inputs()[field]
    assert spec["type"] == "choice", f"{field} is a {spec['type']}, so it offers no options"
    return list(spec["options"])


def registry(name: str) -> Any:
    return yaml.safe_load((PROJECT_ROOT / "config" / name).read_text(encoding="utf-8"))


def workload_catalog() -> WorkloadCatalog:
    return load_yaml(PROJECT_ROOT / "config" / "workload-catalog.yaml", WorkloadCatalog)


def execution_targets() -> ExecutionTargetCatalog:
    return load_yaml(PROJECT_ROOT / "config" / "execution-targets.yaml", ExecutionTargetCatalog)


def resolution_failure(compute_profile: str) -> str | None:
    """The reason code a run on this profile would be refused with, or ``None`` for none.

    The same call admission makes, through the same resolver, so a profile this reports on
    is a profile the submitter meets the identical refusal for. The account is an argument
    to the resolver rather than something it reads, which is what lets this ask the
    question without one.
    """
    try:
        resolve_execution_target(
            compute_profile=compute_profile,
            catalog=workload_catalog(),
            targets=execution_targets(),
            account_id=EXAMPLE_ACCOUNT_ID,
        )
    except (ComputeProfileResolutionError, UnbackedComputeProfileError) as refusal:
        return str(refusal.reason_code)
    return None


def offerable_workloads() -> list[str]:
    """Every workload a submitter could pick and have reach Batch, sorted.

    Two conditions, and a workload has to clear both. Its repository has to be registered,
    or there is no image to run. And the compute profile it inherits has to resolve to an
    execution target, or there is nowhere to run it. Each is asserted on its own below;
    this is the set the dropdown is compared against.
    """
    registered = {entry["repository"] for entry in registry("repositories.yaml")["repositories"]}
    return sorted(
        workload.name
        for workload in workload_catalog().workloads
        if workload.repository in registered
        and resolution_failure(workload.compute_profile) is None
    )


def test_the_repository_dropdown_offers_the_registered_repositories_that_have_a_workload() -> None:
    """REGISTERED IS NOT THE SAME AS USABLE, and this is where the difference shows.

    Mutation: offer every registered repository. A registration says where a repository's
    images go and what base they build from; it does not say there is anything to run. A
    repository with no workload profile cannot be submitted for at all -- the workload is
    what fixes the compute profile, the bounds and the checkpoint contract -- so offering
    it is the same broken promise as offering a workload whose repository is unregistered,
    one step further along.

    ``edullm-data`` is registered and has no workload yet. It appears in this dropdown the
    moment one exists, and not before.
    """
    registered = {
        entry["repository"] for entry in registry("repositories.yaml")["repositories"]
    }
    with_work = {
        workload["repository"]
        for workload in registry("workload-catalog.yaml")["workloads"]
        if workload["repository"] in registered
    }

    assert options_for("repository") == sorted(with_work, key=str.lower)
    assert "edullm-data" in registered
    assert "edullm-data" not in options_for("repository"), (
        "registered, but nothing runs there yet; delete this assertion when a workload "
        "profile names it"
    )


def corpora_a_run_could_actually_train_on() -> set[str]:
    """Registered published corpora whose declared tokenizer this platform can build.

    The join that makes the dropdown a promise rather than a list. A corpus is offerable when
    it is registered AND something can turn its tokenizer into a model, and the second half is
    a fact about OLMo-core rather than about the registry.
    """
    return {
        entry["reference_id"]
        for entry in registry("datasets.yaml").get("published", [])
        if entry["tokenizer"] in TOKENIZERS
    }


def test_the_dataset_dropdown_offers_the_registered_things_a_run_could_actually_use() -> None:
    """Mutation: offer the release ids and forget the published references.

    ``unregistered_dataset`` is a denied-outright condition, so an option that is not in the
    registry is a menu item whose only outcome is a refusal -- and the refusal arrives after
    the approval gate, having spent somebody's attention.

    Two lists on one registry means two ways to be incomplete, which is why this reads both.
    ``releases`` are identifiers this platform will itself produce; ``published`` are corpora
    somebody else built and sealed. They are separate keys because they are separate claims --
    see ``PublishedDatasetReference`` for why a published corpus cannot be a ``DatasetRelease``
    -- but they are one dropdown, because a submitter picking a dataset is not being asked
    which of those two things it is.

    REGISTERED IS NOT THE SAME AS OFFERABLE, and there are two separate reasons a registered
    thing is not offered. A published corpus can be excluded by computation: the test below
    covers one whose tokenizer nothing here can build, and that exclusion resolves itself the
    day OLMo-core grows a byte tokenizer. A release can only be excluded by declaration --
    ``dolma-2026-07`` has nothing to look up, because nothing was ever published under the
    name, so ``retired: true`` in the registry is the only place that fact can live.

    Both are the same promise from a submitter's side: everything in this list works. What
    differs is who can answer, and conflating them would mean either de-registering a release
    that every historical intent record names, or offering a dataset that reads nothing.
    """
    document = registry("datasets.yaml")
    registered = [
        entry["release_id"] for entry in document["releases"] if not entry.get("retired", False)
    ]
    retired = [entry["release_id"] for entry in document["releases"] if entry.get("retired", False)]
    registered += sorted(corpora_a_run_could_actually_train_on())
    offered = options_for("dataset_release")

    # Set equality rather than sorted order. What this test is for is that no option can be
    # picked which admission would then deny; the order is a separate decision, and it is made
    # deliberately against alphabetical so that `none` -- the true answer for a run that reads
    # nothing -- is the option a first-time submitter reaches first.
    assert set(offered) == set(registered)
    assert len(offered) == len(registered), f"an option is listed twice: {offered!r}"
    assert not (set(offered) & set(retired)), (
        f"a retired release is still on the form: {sorted(set(offered) & set(retired))}"
    )
    # And the retirement is real rather than a flag nobody set. The moment a corpus is
    # registered properly this assertion is what has to change, which is the intent.
    assert retired == ["dolma-2026-07"], (
        "dolma-2026-07 is the one release that is registered and deliberately not offered; "
        "if that set has changed, the reasoning in config/datasets.yaml has to change with it"
    )


def test_a_corpus_whose_tokenizer_nothing_can_build_stays_registered_and_unoffered() -> None:
    """Mutation: offer every registered corpus, since all of them are real and readable.

    ``lean4-mathlib-bytes-v3`` is published, sealed, frozen and 326 MB -- the cheapest thing
    any run here could read. It is still not offerable, and the reason is not about the corpus
    at all. It depends on ``tokenizer/bytes-utf8``, and OLMo-core has no byte tokenizer:
    ``TokenizerConfig`` offers dolma2, dolma2_sigdig, gpt_neox_olmo_dolma_v1_5, gpt2 and
    from_hf, and nothing under ``olmo_core/data/`` mentions bytes or utf8 at all. Measured
    against the checkout the training image builds from, on 2026-08-01.

    So a submitter picking it would fill in eight fields, wait for a lead, and reach a
    container that cannot construct a model for the tokens it just resolved. That is the
    ``unregistered_repository`` shape this module was written about, arriving through a field
    nobody had connected to the tokenizer before.

    IT STAYS IN THE REGISTRY, and the split is the point. The registry answers "may a
    submission name this", and the honest answer is yes -- it is a real corpus, admission can
    resolve it, and a workload that is not an OLMo-core training run could read it tomorrow.
    The dropdown answers "will this work", which today it will not.

    Self-retiring, in both directions, which is why the exclusion is computed rather than
    listed. Add ``tokenizer/bytes-utf8`` to ``TOKENIZERS`` and the test above starts demanding
    the option; publish a corpus on a tokenizer nothing can build and it starts demanding its
    absence. Neither needs anybody to remember this test exists.
    """
    registered = {
        entry["reference_id"] for entry in registry("datasets.yaml").get("published", [])
    }
    offerable = corpora_a_run_could_actually_train_on()

    assert "lean4-mathlib-bytes-v3" in registered
    assert "lean4-mathlib-bytes-v3" not in offerable
    assert "lean4-mathlib-bytes-v3" not in options_for("dataset_release")
    assert "regmix-10b-v1" in offerable, (
        "at least one registered corpus must be trainable, or the dropdown offers no real "
        "data at all and the exclusion above has quietly become the rule"
    )


def test_every_offered_dataset_resolves_to_something_a_reader_could_open() -> None:
    """Mutation: register a reference whose version nobody published.

    A ``reference_id`` in the registry is a promise the corpus can be read. ``dataset_id`` and
    ``version`` are what the reader is called with, so an entry whose version is wrong is a
    submission that compiles, classifies, routes to a lead, is approved, reaches the container
    and fails there -- the most expensive place in the path to learn it.

    Checked as agreement between the URI and the two reader arguments rather than by asking
    S3, deliberately. What can go wrong offline is that the three fields stop being one
    statement: ``PublishedDatasetReference`` stores ``dataset_id`` and ``version`` apart from
    the URI precisely so nothing has to re-split the string, and the cost of storing them
    apart is that they can disagree. A unit test cannot know the corpus is still there --
    that is D3's job, in a container, against the account -- but it can know that whoever
    reads the URI and whoever reads the pair are asking for the same thing.
    """
    for entry in registry("datasets.yaml").get("published", []):
        assert entry["uri"] == f"s3://edullm-data/{entry['dataset_id']}/{entry['version']}/", (
            f"{entry['reference_id']}'s uri, dataset_id and version disagree; the reader is "
            "called with the pair and a human reads the uri, so these must be one statement"
        )


def test_no_offered_dataset_is_one_a_training_run_cannot_use_as_a_corpus() -> None:
    """Mutation: offer tokenizer/dolma2-bpe, which is published, sealed and readable.

    IT IS ALL THREE AND IT IS STILL NOT A CORPUS. It declares no partitions, so the reader
    returns every object and no trainable split; its group's container types itself, so the
    resolved dtype is ``None``; and a ``NumpyFSLDatasetConfig`` handed ``None`` takes
    OLMo-core's ``uint16`` default and memmaps ``tokenizer.json`` as tokens. The run trains,
    the loss moves, and the tokens are a JSON file -- which is D1's silent failure arriving by
    a route D1's dtype assertion cannot see, because there the program does pass the resolved
    dtype.

    A tokenizer is an input to a corpus, not a corpus. Asserted on the family segment because
    that is the mechanical form of the distinction and because the standard's own family enum
    does not contain ``tokenizer`` at all.
    """
    for entry in registry("datasets.yaml").get("published", []):
        assert entry["dataset_id"].split("/", maxsplit=1)[0] == "pretrain", (
            f"{entry['reference_id']} is not a pretrain corpus; a dataset offered on this "
            "form is a corpus a training run reads, and every other family published so far "
            "is an input to one rather than one"
        )


def test_every_offered_dataset_names_the_tokenizer_it_was_built_with() -> None:
    """Mutation: leave ``tokenizer`` off the two entries, since both are dolma2. One is not.

    ``pretrain/regmix-10b`` depends on ``tokenizer/dolma2-bpe`` and
    ``pretrain/lean4-mathlib-bytes`` on ``tokenizer/bytes-utf8``, both read live from
    ``groups[].depends_on[]`` with role ``tokenizer`` on 2026-08-01. The upstream family file
    turns its own family-wide tokenizer default off and says why: a mismatched tokenizer's ids
    usually still fall in range, so the failure is a plausible loss curve rather than an
    exception.
    """
    for entry in registry("datasets.yaml").get("published", []):
        assert entry["tokenizer"].startswith("tokenizer/")


def test_the_three_guards_above_have_actually_seen_a_row() -> None:
    """Mutation: empty the ``published`` list. All three loops above pass over nothing.

    A guard that has never run its body is not a guard, and three of them iterate a list this
    registry did not have until today. This is the one assertion that fails when that list is
    empty, so the emptiness is reported once, here, by name -- rather than as three tests
    quietly going green while the dropdown loses two options and the set-equality test above
    absorbs it as a matching pair of removals.
    """
    assert registry("datasets.yaml").get("published"), (
        "the three loops above are vacuous without at least one published reference"
    )


def declared_teams() -> tuple[str, ...]:
    inventory = load_yaml(PROJECT_ROOT / "config" / "organization.yaml", OrganizationInventory)
    return tuple(team.team_id for team in inventory.team_bindings.teams)


def test_the_team_dropdown_offers_exactly_the_groups_the_roster_declares() -> None:
    """THE ONE WHOSE WRONG ANSWER NOTHING REFUSES. Mutation: leave the field free text.

    Every other field on this form is checked somewhere. An unregistered dataset, an
    unregistered repository and an unbacked compute profile are all denied at admission, so
    a submitter who types one is told. ``team`` is checked by nothing: ``RunManifest.team``
    is a plain string, admission does not look it up against these bindings, and
    ``evaluate_authorization`` compares a claimed team only against a submitter whose own
    membership is recorded -- which nobody's is. So ``pre-traning`` compiles, is approved,
    runs, writes under ``teams/pre-traning/runs/`` and carries that spelling on its
    ``edullm:team`` tag forever, because the decision record is immutable.

    Equality in both directions, and in declaration order, which is stronger than the set
    comparison the dataset dropdown gets. There is no computed exclusion here of the kind
    that keeps ``lean4-mathlib-bytes-v3`` registered and unoffered: a declared group is a
    group somebody may submit under, full stop. So declaring a ninth group and not offering
    it fails, offering a name the roster does not declare fails, and reordering the file
    without reordering the form fails too -- the order is the only thing that decides which
    group a first-time submitter reaches first, and it should be decided in one place.
    """
    declared = declared_teams()

    assert options_for("team") == list(declared), (
        "the team dropdown and config/organization.yaml disagree; a group declared and not "
        "offered cannot be submitted under, and an option the roster does not declare is a "
        "run whose spend and output are filed under a group that does not exist"
    )
    assert len(declared) == len(set(declared))
    # The eight the owner decided on. Pinned so that adding or removing a group is an edit
    # to this line as well, rather than something the comparison above absorbs silently.
    assert set(declared) == {
        "platform",
        "memory-split",
        "input-core",
        "pre-training",
        "post-training",
        "data-prep",
        "eval-inference",
        "scratch",
    }


def test_no_option_on_the_team_dropdown_is_a_name_this_rename_retired() -> None:
    """Mutation: keep the old name beside the new one, so old records still validate.

    ``tokenizer``, ``modeling`` and ``curriculum`` were renamed and the stored records were
    deliberately not rewritten, for the reason ``config/organization.yaml`` sets out beside
    the table: a lineage record carries a digest over its own bytes, so editing the team
    inside one falsifies it. Keeping the old names offerable would be the obvious way to
    make an audit tidy and is the wrong one -- it would split each group's spend across two
    names going forward, which is the defect this dropdown exists to prevent, arriving from
    the direction of compatibility.

    A forward rename means the old names stay readable and stop being submittable. Readable
    is a property of the records, which nothing here touches; unsubmittable is this
    assertion.
    """
    retired = {"tokenizer", "modeling", "curriculum"}
    offered = set(options_for("team"))

    assert offered & retired == set(), (
        f"{sorted(offered & retired)} are pre-rename names still on the form, so a group's "
        "spend can go on being split between its old and new name"
    )
    assert offered & {"evaluation"} == set(), (
        "two lineage records claim team `evaluation`, which was never a declared group and "
        "is not eval-inference under an earlier name; offering it would declare it by "
        "accident"
    )


def test_the_workload_dropdown_offers_only_workloads_whose_repository_is_registered() -> None:
    """THE ONE THAT MATTERS. Mutation: offer every workload in the catalog.

    ``dolma-tokenize`` is in the catalog and names a repository nothing registers.
    Offering it would put a menu item in front of a researcher whose only possible outcome
    is ``unregistered_repository`` at admission -- after they filled in eight fields and a
    lead approved it.

    Written as a comparison rather than a list, so this fails in *both* directions. Register
    dolma without adding its workload and the dropdown is missing something that now works;
    offer a workload whose repository is still unregistered and the dropdown is promising
    something that does not.

    The set it compares against carries the second condition too -- the inherited compute
    profile has to have somewhere to run -- because a dropdown held to one of two
    requirements is a dropdown that can still promise a refusal. That half is isolated in
    the test below, so a failure here says which of the two moved.
    """
    registered = {entry["repository"] for entry in registry("repositories.yaml")["repositories"]}

    assert options_for("workload_profile") == offerable_workloads()
    assert "dolma-tokenize" not in options_for("workload_profile"), (
        "dolma has no registration and no ECR repository, so this workload cannot run; "
        "when it is registered this assertion is what has to be deleted"
    )
    assert {
        workload.repository for workload in workload_catalog().workloads
    } - registered == {"dolma"}


def test_every_offered_workload_inherits_a_compute_profile_with_somewhere_to_run() -> None:
    """THE THIRD INSTANCE OF ONE DEFECT, CLOSED. Mutation: offer olmo-core-train-4gpu.

    A workload profile fixes the compute profile, and a submitter who leaves the override
    on ``inherit`` never types that profile in. So the two fields are individually valid --
    a registered workload, and an override the submitter did not touch -- and jointly
    refused, which is the shape ``dolma-tokenize`` and an unprovisioned override
    already had.

    ``olmo-core-train-4gpu`` was the live instance. Its repository is registered and its
    image is published, and it inherits ``gpu-4xa10g``: priced in the catalog, not marked
    provisioned, and named by no execution target. A submission on it compiles, classifies
    as routine at $5.67, routes to a lead, waits for a person, is approved, and is then
    refused at admission with ``no_execution_target``. The whole cost of that is a human's
    attention, spent on a decision that could never have gone the other way.

    Asserted through ``resolve_execution_target`` rather than by reading ``provisioned``
    out of the catalog, so this fails for the reason the submitter would meet: the resolver
    separates a profile nobody registered from one nobody provisioned from one whose two
    configuration files disagree, and any of the three is a refused submission.
    """
    for name in options_for("workload_profile"):
        workload = next(
            candidate for candidate in workload_catalog().workloads if candidate.name == name
        )
        assert resolution_failure(workload.compute_profile) is None, (
            f"{name} inherits compute profile {workload.compute_profile!r}, which has "
            "nowhere to run, so every submission that picks it is refused after approval"
        )

    # And the profile that put this test here is still the one that cannot run. Written as
    # its own assertion because the loop above passes vacuously if the dropdown is emptied.
    assert resolution_failure("gpu-4xa10g") == "unprovisioned_compute_profile"
    assert "olmo-core-train-4gpu" not in options_for("workload_profile"), (
        "gpu-4xa10g is priced and not provisioned, so this workload has nowhere to run; "
        "when a compute environment backs it this assertion is what has to be deleted"
    )
    # The catalog keeps it, deliberately: the entry is the shape a four-GPU training run
    # takes and its pricing row is what the catalog is for. Removing the menu item is not
    # the same as removing the workload.
    assert "olmo-core-train-4gpu" in {
        workload.name for workload in workload_catalog().workloads
    }


def test_a_workload_that_reads_a_corpus_is_offered_and_has_somewhere_to_run() -> None:
    """Mutation: drop the training profile from the dropdown and leave the checks above.

    Every test around this one compares the two directions of a list, so all of them pass
    on an empty dropdown -- offering nothing offers nothing unbacked. What none of them
    says is that the one workload a researcher actually came here for is on the menu.

    That workload is ``olmo-core-train-1gpu``. It is the only entry that is for training
    rather than for proving the platform works: twelve hours against the policy ceiling,
    two attempts, and a checkpoint contract so the second attempt resumes instead of
    repeating the first at full price. The sibling ``olmo-core-train-4gpu`` has the same
    shape on ``gpu-4xa10g``, which is priced and not provisioned -- so picking the
    plausible one costs a lead's approval and then a ``no_execution_target``.
    """
    offered = options_for("workload_profile")
    assert "olmo-core-train-1gpu" in offered

    workload = next(
        candidate
        for candidate in workload_catalog().workloads
        if candidate.name == "olmo-core-train-1gpu"
    )
    assert resolution_failure(workload.compute_profile) is None
    # A run long enough to read a corpus, with somewhere for the interrupted half to live.
    assert int(workload.maximum_runtime_hours) > 1
    assert workload.checkpoint is not None


def test_resolving_a_compute_profile_is_the_same_question_the_provisioned_flag_answers() -> None:
    """Mutation: mark a profile provisioned and deploy no execution target for it.

    ``offerable_workloads`` asks the resolver rather than the flag, which is the stronger
    read of the two -- but only while the two agree. They can disagree in exactly one way,
    and it is a configuration contradiction rather than an honest state: a profile the
    catalog calls provisioned that ``config/execution-targets.yaml`` does not back. The
    resolver has its own error for that, and this is what stops the dropdown offering such
    a profile on the strength of the flag alone.
    """
    provisioned = {
        profile.name for profile in workload_catalog().compute_profiles if profile.provisioned
    }

    assert provisioned == execution_targets().backed_profiles
    assert provisioned == {"cpu-32vcpu", "gpu-1xa10g"}
    for name in provisioned:
        assert resolution_failure(name) is None


def test_the_compute_override_offers_inherit_and_the_provisioned_profiles() -> None:
    """Mutation: offer a profile the catalog prices but nothing backs.

    The catalog prices twelve profiles and two are provisioned. Offering an unprovisioned
    one is the same failure as the dolma workload: a selectable option whose only outcome is
    a refusal, this time ``unprovisioned_compute_profile``.
    """
    catalog = registry("workload-catalog.yaml")["compute_profiles"]
    provisioned = sorted(
        profile["name"] for profile in catalog if profile.get("provisioned") is True
    )

    assert options_for("compute_profile") == [INHERIT, *provisioned]


def test_inherit_is_translated_away_before_the_form_is_assembled() -> None:
    """Mutation: drop the translation and let ``inherit`` through.

    A ``choice`` option cannot be blank, so "take the registered default" has to be spelled
    as a word. Left untranslated it reaches admission as the name of a compute profile
    nothing has ever registered, and the refusal names the profile rather than the form --
    which sends the reader to the catalog to look for something that was never meant to be
    there.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert f'INHERIT = "{INHERIT}"' in workflow
    assert "if text and text != INHERIT:" in workflow


def test_every_dropdown_field_has_no_free_text_twin() -> None:
    """Mutation: keep the old string input beside the new choice one.

    Two inputs for one value is worse than either alone, because the workflow reads one and
    the researcher may have filled the other. The form has to have exactly one field per
    thing it asks for.
    """
    inputs = form_inputs()
    names = list(inputs)

    assert len(names) == len(set(names))
    for field in ("repository", "workload_profile", "dataset_release", "team", "compute_profile"):
        assert inputs[field]["type"] == "choice"
        assert f"{field}_name" not in inputs
        assert f"{field}_other" not in inputs


@pytest.mark.parametrize("field", ["commit_sha", "image_digest", "command", "wandb_project"])
def test_the_fields_that_cannot_be_a_dropdown_are_still_free_text(field: str) -> None:
    """A recorded decision rather than a guard. Mutation: none.

    These four have no finite list behind them. A commit and a digest are per-build, a
    command is per-run, and a W&B project is whatever the researcher calls it. Two of them
    are the reason the form is still hard to use -- a digest is sixty-four characters copied
    from another repository's build output, and a command was 6,733 bytes on the last real
    training run -- and neither is fixed by a menu.
    """
    assert form_inputs()[field]["type"] == "string"


def test_the_form_stays_within_what_github_will_accept() -> None:
    """Mutation: add inputs past the cap.

    ``workflow_dispatch`` allowed 10 inputs until December 2025 and allows 25 now. This form
    has fifteen, so the headroom is real but finite -- and the failure is the workflow
    refusing to parse, which takes the submission path down entirely rather than degrading.
    """
    assert len(form_inputs()) <= 25


def test_the_command_the_form_arrives_pre_filled_with_is_one_the_contract_accepts() -> None:
    """Mutation: put a command line in the default that cannot be split into arguments.

    The whole value of a pre-filled command is that a first submission needs no typing, and
    that value inverts if the thing it arrives with is refused. ``RunManifest.command``
    requires a first element naming a program rather than a whole command line, which is
    exactly the mistake a hand-written default makes -- the same mistake a stored intent
    from 2026-07-30 now trips over.

    Asserted through ``shlex`` and the contract rather than against the literal string, so
    that changing the example stays free and breaking it does not.
    """
    default = form_inputs()["command"].get("default")

    assert default, "the command field arrives empty, so a first run has to type one"
    arguments = shlex.split(str(default))
    assert arguments, "the default splits to nothing"
    RunManifest(
        schema_version=1,
        repository="OLMo-core",
        commit_sha="1" * 40,
        image_digest="sha256:" + "a" * 64,
        dataset_release="none",
        command=arguments,
        team="platform",
        wandb_project="onboarding",
        workload_profile="olmo-core-check-cpu",
        compute_profile="cpu-32vcpu",
        maximum_runtime_hours="1",
        maximum_attempts=1,
        checkpoint=None,
        fanout=None,
    )
