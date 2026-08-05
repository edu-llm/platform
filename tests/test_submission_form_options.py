"""What the submission form offers, held to what the platform will actually accept.

Five of the nine required fields are keys into committed registries, and until recently all
of them were free-text boxes. A researcher had to know that ``dolma-2026-07`` is the dataset
id and that ``olmo-core-check`` is a workload profile, and a typo in either was a refusal
after a human had already approved the submission.

Four of them are dropdowns now, and a dropdown is a promise: everything in this list works.
That promise is what these tests keep. A ``choice`` input's options are static text in the
workflow YAML with nothing behind them, so a registry entry added and not offered is
invisible, and an option offered and not registered is a refusal wearing a menu item.

**``workload_profile`` is the fifth and went back to free text, which is a different promise
and not a weaker one.** The list was costing more than it bought:
``config/workload-catalog.yaml`` is owned by the admins and all eight team leads, and
``.github/workflows/submit-run.yml`` by two people, because two IAM trust policies pin its
filename with ``StringEquals``. So a lead could merge a workload profile and could not merge
the option that let anybody select it, and the equality this module used to assert between
the two files was the second lock on the same door. What the dropdown was protecting against
-- a name nothing registers -- is refused while the submission compiles, before the approval
gate and before any credential exists. So the promise moved: the refusal has to name what
could have been typed, and that is asserted here in both directions exactly as the option
list was.

**Two lists can promise a refusal, and they used to be one list checked twice.** A workload
could name a repository nothing registers, or inherit a compute profile nothing backs.
Either one compiled, classified as routine, routed to a lead, and was refused at admission
*after* the approval, the first with ``unregistered_repository`` and the second with
``no_execution_target``. The cost of that is a person's attention spent on a decision that
could never have gone the other way.

**The second half is asked of the compute dropdown now, because that is where the machine
is chosen.** A workload profile declared one and the form overrode it, so the join these
tests walked was never what decided where a run landed. The catalog carries policy presets
now and names no machine, so a workload has one way of being wrong and the machine has its
own, asserted directly against the list a submitter picks from.

``dolma-tokenize`` is the live instance of the first: there is no ECR repository for dolma
and no image can be published for it, so it is deliberately absent from what the refusal
suggests. The second is demonstrated by whichever priced profile has no execution target,
which is derived rather than named below, because which profiles are provisioned is a
deployment fact that moves and a test naming one goes red for a promotion rather than for a
defect.

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
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from edullm_platform.admission import denied_outright_conditions
from edullm_platform.config import load_yaml
from edullm_platform.contracts.dataset_registry import TRAINABLE_FAMILIES, DatasetRegistry
from edullm_platform.contracts.execution import (
    ExecutionTargetCatalog,
    UnbackedComputeProfileError,
)
from edullm_platform.contracts.inventory import OrganizationInventory
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.policy import ApprovalPolicy
from edullm_platform.contracts.repository_registry import RepositoryRegistry
from edullm_platform.contracts.workload import ComputeProfileResolutionError, WorkloadCatalog
from edullm_platform.errors import (
    RetiredDatasetReleaseError,
    UnregisteredWorkloadProfileError,
)
from edullm_platform.execution import resolve_execution_target
from edullm_platform.manifest_helpers import build_request_facts
from edullm_platform.submission import (
    _resolve_workload,
    require_a_dataset_release_that_is_current,
)
from tools.build_gpu_training_submission import TOKENIZERS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "submit-run.yml"

#: How a dropdown used to spell "leave this empty" while ``compute_profile`` was an
#: override. Kept as a name for the thing that must stay gone, the way the retired team
#: names are, rather than as something the form may still say.
RETIRED_SENTINEL = "inherit"

#: Registered repositories deliberately kept off the ``repository`` dropdown, each mapped to
#: the reason. Empty, and it is the emptiness that is worth keeping rather than the
#: mechanism.
#:
#: THIS EXISTS BECAUSE THE COMPARISON BELOW USED TO EXCLUDE THE CASE IT WAS GUARDING. It
#: held the dropdown equal to *the registered repositories that have a workload profile*,
#: and a registration with no workload profile is absent from both sides of that: it is not
#: on the form, and the filter drops it before the comparison sees it. So the one state the
#: test was written about -- registered, and impossible to submit for -- was the one state
#: it could not fail on. ``edullm-data`` sat in it from its registration until
#: ``edullm-data-validate`` was added, with this module green throughout.
#:
#: The dropdown is compared against the registry now, so a registration is either
#: submittable or listed here with a sentence saying why. Both are visible in a diff;
#: omission was not.
UNSUBMITTABLE_BY_DESIGN: dict[str, str] = {}

#: AWS's own documented example account, which this repository's secret scan exempts.
#: ``resolve_execution_target`` composes ARNs from whatever account it is handed and this
#: test cares only about whether it can compose them at all, so no real account is needed.
EXAMPLE_ACCOUNT_ID = "123456789012"

#: What a submitter types into the ``workload_profile`` box to reach the refusal below. No
#: catalog name is a substring of it and it is a substring of none, which the guard on the
#: refusal comparison checks rather than assumes.
UNREGISTERED_WORKLOAD = "no-such-thing"


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


def workloads_for(repository: str) -> set[str]:
    """Every catalog entry a submission naming this repository could compile with.

    One condition, and it is the only one a workload profile can still fail: the entry has
    to be written for the repository the submission declares, or ``compile_submission``
    refuses the pair with ``workload_profile_repository_mismatch``. A registered repository
    is implied rather than checked, because ``require_registered_repository`` runs first and
    the repository field is still a dropdown.
    """
    return {
        workload.name
        for workload in workload_catalog().workloads
        if workload.repository == repository
    }


def workload_refusal(repository: str) -> str:
    """What a submitter reads when the workload box holds a name nothing registers.

    The same call the compile job makes, through the same function, so a name this reports
    on is a name the submitter meets the identical refusal for. That is the pattern
    ``resolution_failure`` above already follows for the compute profile, and it is what
    makes this a claim about the submission path rather than about a string in a test.
    """
    try:
        _resolve_workload(workload_catalog(), UNREGISTERED_WORKLOAD, repository=repository)
    except UnregisteredWorkloadProfileError as refusal:
        return str(refusal)
    raise AssertionError(
        f"{UNREGISTERED_WORKLOAD!r} resolved to a catalog entry, so this helper is "
        "measuring a submission that compiles rather than the refusal it was written about"
    )


def offerable_compute_profiles() -> list[str]:
    """Every machine a submitter could pick and have a job placed on, sorted.

    Computed through the resolver rather than read off ``provisioned``, for the reason the
    test below records: the flag and the deployed target can disagree, and what a submitter
    meets is the resolver's answer.
    """
    return sorted(
        profile.name
        for profile in workload_catalog().compute_profiles
        if resolution_failure(profile.name) is None
    )


def registered_repositories() -> set[str]:
    return {entry["repository"] for entry in registry("repositories.yaml")["repositories"]}


def repositories_with_a_workload() -> set[str]:
    return {
        workload["repository"] for workload in registry("workload-catalog.yaml")["workloads"]
    }


def test_every_registered_repository_is_submittable_or_is_visibly_excused() -> None:
    """A REGISTRATION WITH NO WORKLOAD PROFILE CAN NEVER BE SUBMITTED FOR, AND NOTHING SAID
    SO. Mutation: register a repository and write no workload profile for it.

    That mutation used to be invisible here. The dropdown was held equal to the registered
    repositories *that have a workload profile*, which is a filter both sides of the
    comparison went through: the unsubmittable registration is off the form, and the filter
    drops it before the comparison, so the two lists agreed about a repository neither of
    them contained. The assertion could not fail in the case it existed for.

    ``edullm-data`` is the worked example rather than a hypothetical. It was registered with
    an ECR repository and publisher-role scope and no catalog entry, so it never reached the
    dropdown and no run could name it, and this module was green for the whole of that
    window. ``edullm-data-validate`` closed it; this closes the hole it went through.

    The claim is asked of the registry now. Every registration either has a workload profile
    or is named in ``UNSUBMITTABLE_BY_DESIGN`` with a reason, so the two ways a repository
    can be off the form -- a decision and a defect -- stop looking alike. An exemption is a
    line somebody wrote and a reviewer can argue with; an omission was neither.

    ``dolma`` is the opposite side of the same join and is asserted separately below: it has
    a workload and no registration, so there is no image its workload could run from.
    """
    registered = registered_repositories()
    with_work = repositories_with_a_workload()

    unsubmittable = sorted(registered - with_work - set(UNSUBMITTABLE_BY_DESIGN))
    assert not unsubmittable, (
        f"{unsubmittable} are registered and have no workload profile in "
        "config/workload-catalog.yaml, so they cannot appear on the submission form and no "
        "run can name them. Give each one a catalog entry, or declare it in "
        "UNSUBMITTABLE_BY_DESIGN with the reason it is registered and not runnable"
    )

    # AN EXEMPTION THAT HAS STOPPED BEING TRUE IS THE SAME DEFECT WEARING A REASON. A name
    # here that is no longer registered, or that has since gained a workload, would go on
    # excusing something and would also keep that repository off the dropdown comparison
    # below -- which is the omission this whole mechanism replaced.
    stale = sorted(name for name in UNSUBMITTABLE_BY_DESIGN if name not in registered)
    assert not stale, (
        f"{stale} are excused from the submission form and are not registered at all, so "
        "the exemption is excusing nothing and hiding the dropdown from a comparison"
    )
    resolved = sorted(name for name in UNSUBMITTABLE_BY_DESIGN if name in with_work)
    assert not resolved, (
        f"{resolved} now have a workload profile, so they are submittable and the exemption "
        "is what is keeping them off the form"
    )
    for name, reason in UNSUBMITTABLE_BY_DESIGN.items():
        assert reason.strip(), f"{name} is excused with no reason, which is an omission again"


def test_the_repository_dropdown_offers_every_registration_that_is_not_excused() -> None:
    """Mutation: leave a registration off the form and let it drop out of the comparison.

    The other half of the test above, and the half a submitter meets. Compared against the
    registry minus the declared exemptions rather than against the registrations that happen
    to have a workload, so a repository can be absent from this list only because somebody
    said it should be.

    Written as an equality, so it fails in both directions. A registration missing from the
    dropdown is a repository nobody can submit for; an option the registry does not carry is
    a menu item whose only outcome is ``unregistered_repository`` at admission, after a lead
    has spent an approval on it.
    """
    offerable = sorted(registered_repositories() - set(UNSUBMITTABLE_BY_DESIGN), key=str.lower)

    assert options_for("repository") == offerable
    assert "dolma" not in options_for("repository"), (
        "dolma has a workload and no registration, so there is no image to run it from"
    )


def corpora_a_run_could_actually_train_on() -> set[str]:
    """Registered published corpora whose declared tokenizer this platform can build.

    The join that makes the dropdown a promise rather than a list, and three conditions
    because there are three ways a real sealed corpus is not a thing to offer.

    ``family in TRAINABLE_FAMILIES`` is the safety one, and it is read from the contract
    rather than spelled here so that the form and admission cannot answer differently. It is
    what keeps a tokenizer off the form; ``config/datasets.yaml`` has the failure it prevents.

    ``not retired`` is the declared one. Nothing computable separates fineweb-edu-1b v2 from
    v6 -- same family, same tokenizer, same pinned digest -- so which is current is a fact
    only the corpus's owner holds, and the registry is where they put it.

    ``tokenizer in TOKENIZERS`` is the computed one, and it is a fact about OLMo-core rather
    than about this registry, which is why it is looked up. Note that a corpus declaring no
    tokenizer fails it, because ``None`` is not a key in that map, and that is the right
    answer rather than a lucky one: the offered path builds a model over a vocabulary, and a
    corpus of pre-tokenization conversation text has no vocabulary to build it over.
    """
    return {
        entry["reference_id"]
        for entry in registry("datasets.yaml").get("published", [])
        if entry["dataset_id"].split("/", maxsplit=1)[0] in TRAINABLE_FAMILIES
        and not entry.get("retired", False)
        and entry["tokenizer"] in TOKENIZERS
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

    TWO CORPORA ARE IN THAT STATE AND THERE WERE THREE, WHICH IS THIS TEST WORKING RATHER
    THAN THIS TEST WEAKENING. ``math-memory-full-v1`` joins lean4-mathlib-bytes on
    ``tokenizer/bytes-utf8``, which has no OLMo-core equivalent to name and is still a
    missing upstream feature.

    ``fineweb-edu-1b-v6`` left this list, and the paragraph above predicted the exact
    mechanism: it was on ``tokenizer/smollm2-bpe``, which had an exact OLMo-core equivalent
    and no line naming it, and "only the second resolves by somebody deciding to resolve
    it". Somebody did. ``TokenizerConfig.from_hf("HuggingFaceTB/SmolLM2-135M")`` is in both
    ``TOKENIZERS`` maps now, so the join above started demanding the option and this test
    started demanding its absence, and the two could not both be satisfied until one of them
    was corrected. Nobody had to remember either was here.

    ``fineweb-edu-1b-v2`` is on that tokenizer too and stays off the form regardless, because
    it is retired -- which is what stopped the resolution offering two versions of one corpus
    at once, and is asserted separately below.

    THIS IS NOT THE TEST THAT KEEPS A TOKENIZER OFF THE FORM, and the two are worth not
    confusing. A tokenizer entry declares no tokenizer of its own, so it fails the map lookup
    below as well -- but that is a coincidence of shape and used to be the only thing standing
    in the way. ``TRAINABLE_FAMILIES`` is the rule now, and it is asserted separately.
    """
    registered = {
        entry["reference_id"] for entry in registry("datasets.yaml").get("published", [])
    }
    offerable = corpora_a_run_could_actually_train_on()

    for reference_id in ("lean4-mathlib-bytes-v3", "math-memory-full-v1"):
        assert reference_id in registered
        assert reference_id not in offerable
        assert reference_id not in options_for("dataset_release")
    assert "fineweb-edu-1b-v6" in offerable, (
        "the tokenizer line landed, so the corpus this test used to hold up as unofferable "
        "is now the worked example of the exclusion retiring itself"
    )
    assert "regmix-10b-v1" in offerable, (
        "at least one registered corpus must be trainable, or the dropdown offers no real "
        "data at all and the exclusion above has quietly become the rule"
    )


def test_two_versions_of_one_corpus_are_never_offered_at_the_same_time() -> None:
    """Mutation: un-retire fineweb-edu-1b-v2, since both versions are published and sealed.

    ``pretrain/fineweb-edu-1b`` is the live instance and the only corpus registered twice.
    v2 and v6 are both published, sealed and frozen, so both belong in the registry, and only
    v6 is current. Nothing computable separates them. They share a family and a tokenizer and
    pin the same digest, that last because their token manifests are byte identical, and the
    bucket does not help either -- v6 supersedes v5 and v2 supersedes v1, with v3 through v5
    never published, so no chain in the data reaches from one to the other. ``retired: true``
    on v2 is where that fact lives, because there is nowhere else it can.

    A superseded corpus on the dropdown is not a refusal a submitter would ever see. It is a
    run that reads real tokens nobody meant it to read, costs a GPU day, and produces a
    result against the wrong version of the data that looks exactly like the right one.

    CHECKED ON THE REGISTRY FIRST AND ON THE FORM SECOND, WHICH IS THE PART THAT MAKES THIS
    HOLD TODAY. The second assertion alone would pass for the wrong reason right now, since
    ``tokenizer/smollm2-bpe`` is in neither version's way and both are excluded by the
    tokenizer join regardless of what ``retired`` says -- so un-retiring v2 would change
    nothing visible until the day that tokenizer lands, which is the day nobody is looking.
    The registry-level assertion fails the moment somebody un-retires it.
    """
    published = registry("datasets.yaml").get("published", [])
    live = [entry["dataset_id"] for entry in published if not entry.get("retired", False)]
    duplicated = sorted({name for name in live if live.count(name) > 1})

    assert any(entry.get("retired", False) for entry in published), (
        "no published entry is retired, so this guard is asserting over a flag nothing sets; "
        "fineweb-edu-1b-v2 is the entry it was written about"
    )
    assert not duplicated, (
        f"{duplicated} is registered at more than one un-retired version, so both reach the "
        "form as soon as their tokenizer does and a submitter chooses between them on a "
        "reference id rather than on which one is current; config/datasets.yaml records "
        "which version each corpus's owner named"
    )

    offered = [
        entry["dataset_id"]
        for entry in published
        if entry["reference_id"] in corpora_a_run_could_actually_train_on()
    ]
    assert offered, "no corpus is offered at all, so this guard proves nothing"
    assert sorted(offered) == sorted(set(offered)), (
        f"one corpus is offered at two versions: {sorted(offered)}"
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

    THIS USED TO ASSERT THAT EVERY PUBLISHED ENTRY WAS A pretrain CORPUS, AND THAT WAS ONLY
    EVER TRUE BY ACCIDENT. ``tokenizer`` was a required non-null field, and every other family
    in the bucket declares none, so ``sft/pedagogy70-normal30``, ``vendor/openai-prm800k`` and
    the tokenizers could not be written into the file at all. The registry now holds all of
    them, because dependents pin them by digest and admission has to resolve what a lineage
    record names, so the assertion is over what reaches the form and over the rule itself.

    THE SECOND ASSERTION IS THE ONE THAT MATTERS AND IT LOOKS LIKE A TAUTOLOGY. It is not.
    ``TRAINABLE_FAMILIES`` is an editable frozenset in a contract module, and the whole safety
    property rests on one string never being added to it. Everything else here follows from
    that set being right, so the set is held to it directly, and a reader who adds
    ``tokenizer`` to make some other test pass gets this failure with the reason attached.

    THE LOOP BELOW WAS A TAUTOLOGY AND NOW READS THE FORM. It walked the corpora
    ``corpora_a_run_could_actually_train_on`` returns and asserted the family condition that
    function had already filtered on, so it could not fail: an entry in a non-trainable
    family is dropped before the loop sees it. The dropdown is the artifact this is a claim
    about, so the dropdown is what it iterates, and a tokenizer's reference id typed onto the
    form now fails here rather than only in the set comparison further up.
    """
    assert "tokenizer" not in TRAINABLE_FAMILIES, (
        "a tokenizer is an input to a corpus and must never be nameable as one; a run handed "
        "one gets every object rather than a trainable split, no dtype, OLMo-core's uint16 "
        "default, and a loss curve computed over tokenizer.json that nothing reports as wrong"
    )
    assert "vendor" not in TRAINABLE_FAMILIES, (
        "vendor/openai-prm800k's own limitations block says any train-ready representation "
        "must be a separately validated derived artifact, so naming the mirror as a corpus "
        "asks a run to train on the thing that corpus says is not trainable yet"
    )

    published = registry("datasets.yaml").get("published", [])
    offered = set(options_for("dataset_release"))
    reaching_the_form = [entry for entry in published if entry["reference_id"] in offered]

    assert reaching_the_form, (
        "the dropdown offers no published corpus at all, so the loop below is passing over "
        "nothing and the family rule is being asserted against an empty list"
    )
    for entry in reaching_the_form:
        assert entry["dataset_id"].split("/", maxsplit=1)[0] in TRAINABLE_FAMILIES, (
            f"{entry['reference_id']} reaches the form and is not in a trainable family; a "
            "dataset offered here is a corpus a training run reads"
        )

    unoffered_families = {
        entry["dataset_id"].split("/", maxsplit=1)[0]
        for entry in published
        if entry["reference_id"] not in offered
    }
    assert "tokenizer" in unoffered_families, (
        "no tokenizer is registered, so the exclusion above is asserting over nothing; "
        "tokenizer/dolma2-bpe and tokenizer/smollm2-bpe are the entries it was written about"
    )


def test_every_offered_dataset_names_the_tokenizer_it_was_built_with() -> None:
    """Mutation: leave ``tokenizer`` off the two entries, since both are dolma2. One is not.

    ``pretrain/regmix-10b`` depends on ``tokenizer/dolma2-bpe`` and
    ``pretrain/lean4-mathlib-bytes`` on ``tokenizer/bytes-utf8``, both read live from
    ``groups[].depends_on[]`` with role ``tokenizer`` on 2026-08-01. The upstream family file
    turns its own family-wide tokenizer default off and says why: a mismatched tokenizer's ids
    usually still fall in range, so the failure is a plausible loss curve rather than an
    exception.

    NARROWED FROM EVERY PUBLISHED ENTRY TO THE OFFERED ONES, AND THE TITLE WAS ALREADY SAYING
    SO. Four registered datasets now declare no tokenizer, honestly -- two sft corpora of
    pre-tokenization text, a verbatim vendor mirror and the tokenizers -- and none of them is
    offered, because ``None`` is not a key in ``TOKENIZERS``. The claim worth holding is about
    what a submitter can pick, which is a corpus whose tokens exist and whose tokenizer is
    therefore a fact somebody recorded.

    NARROWED AGAINST THE FORM RATHER THAN AGAINST THE JOIN, WHICH IS WHAT MAKES IT ABLE TO
    FAIL. It read ``corpora_a_run_could_actually_train_on``, whose own filter requires the
    tokenizer to be a key in ``TOKENIZERS`` -- and every key in that map is a non-null string
    beginning ``tokenizer/``, so both assertions below followed from the filter and neither
    could ever fire. A corpus is checked because a submitter can pick it, so the list of
    things a submitter can pick is the list to walk.
    """
    offered = set(options_for("dataset_release"))
    checked = [
        entry
        for entry in registry("datasets.yaml").get("published", [])
        if entry["reference_id"] in offered
    ]

    assert checked, "no corpus is offered, so this loop is passing over nothing"
    for entry in checked:
        assert entry["tokenizer"] is not None, (
            f"{entry['reference_id']} reaches the form and names no tokenizer; the offered "
            "path builds a model over a vocabulary and there is none to build it over"
        )
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


def test_the_workload_profile_box_is_free_text_and_the_machine_box_is_not() -> None:
    """THE ONE ASYMMETRY ON THIS FORM, RECORDED SO THE NEXT READER DOES NOT TIDY IT AWAY.
    Mutation: make ``workload_profile`` a choice again, or make ``compute_profile`` a string
    for consistency with it.

    A ``choice`` input is worth having when a wrong value is worse than an unavailable one.
    That is true of the machine and was never true of the workload.

    ``compute_profile`` earned its list on 2026-08-04. gpu-1xh100 and gpu-8xh100 came off it
    because EC2 will not sell this account a p5, and the removal is what made those shapes
    *unexpressible* rather than merely refusable: ``resolve_execution_target`` runs inside
    admission, past the approval gate, so an unprovisioned profile reaching that far is
    refused with ``no_execution_target`` having already spent somebody's signature. There is
    no earlier refusal to fall back on, which is why the list is the mechanism.

    ``workload_profile`` had no such property to protect. Every unregistered name is refused
    by ``tools/compile_submission.py`` in the compile job -- before the gate, before any
    credential exists -- and the refusal names the alternatives, which the test below holds
    it to. What the list cost is who may register a workload:
    ``config/workload-catalog.yaml`` is owned by the admins and all eight team leads, and
    ``.github/workflows/submit-run.yml`` by two people because two IAM trust policies pin
    its filename with ``StringEquals``. So a lead could merge a catalog entry and could not
    merge the line that let anybody select it.

    THIS TEST REPLACES AN EQUALITY BETWEEN THE DROPDOWN AND THE CATALOG. That equality was
    the other half of the same cost: a lead adding an entry made this module red and had
    nowhere to fix it. What it was protecting -- that the catalog and what a researcher can
    get through the form do not drift -- is asserted against the refusal instead, because
    the refusal is what took the dropdown's place.
    """
    inputs = form_inputs()

    assert inputs["workload_profile"]["type"] == "string"
    assert inputs["workload_profile"]["required"] is True
    assert "options" not in inputs["workload_profile"]
    assert inputs["compute_profile"]["type"] == "choice"


def refusals_for(dataset_release: str) -> tuple[str, ...]:
    """Every refusal a submission naming this dataset meets, and nothing else about it.

    The functions the compile job and admission use, called the way they call them, so a
    name this reports nothing for is a name neither of them refuses.
    ``build_request_facts`` derives ``dataset_registered`` and ``dataset_is_a_corpus`` from
    the registry and ``denied_outright_conditions`` reads policy's list.

    **IT WAS THAT PAIR ALONE AND THAT STOPPED BEING THE WHOLE ANSWER**, which is this
    helper doing its job rather than this helper being wrong. The pair is everything policy
    denies outright, and a refusal does not have to be a policy condition to be a refusal:
    ``require_a_dataset_release_that_is_current`` refuses a retired entry in the compile job
    and on the laptop, before the approval gate and out of the same registry, and
    deliberately not in ``denied_outright`` -- see that function for why. A derivation that
    read only policy's list would now report a name as refused by nothing while the compile
    job refuses it, which is the failure this whole module is written against, arriving
    inside the measurement rather than inside the form.

    Everything unrelated to the dataset is held at a value nothing objects to -- a
    registered repository, a catalog workload, a priced profile, a cost of zero -- so that
    the only refusal this can report is the one being asked about.
    """
    manifest = RunManifest(
        schema_version=1,
        repository="OLMo-core",
        commit_sha="1" * 40,
        image_digest="sha256:" + "a" * 64,
        dataset_release=dataset_release,
        command=["python", "-m", "olmo_core.data.tokenize"],
        team="platform",
        wandb_project="onboarding",
        workload_profile="olmo-core-check",
        compute_profile="cpu-32vcpu",
        maximum_runtime_hours="1",
        maximum_attempts=1,
        checkpoint=None,
        fanout=None,
    )
    datasets = load_yaml(PROJECT_ROOT / "config" / "datasets.yaml", DatasetRegistry)
    facts = build_request_facts(
        manifest,
        repositories=load_yaml(
            PROJECT_ROOT / "config" / "repositories.yaml", RepositoryRegistry
        ),
        catalog=workload_catalog(),
        dataset_registry=datasets,
        estimated_cost_usd=Decimal(0),
    )
    policy = load_yaml(PROJECT_ROOT / "config" / "policy.yaml", ApprovalPolicy)
    refused = list(denied_outright_conditions(facts, policy))
    try:
        require_a_dataset_release_that_is_current(dataset_release, datasets=datasets)
    except RetiredDatasetReleaseError as refusal:
        refused.append(type(refusal).reason_code)
    return tuple(refused)


def test_the_dataset_box_is_a_choice_because_five_registered_names_are_refused_by_nothing() -> (
    None
):
    """THE SECOND ASYMMETRY ON THIS FORM, AND THE ONE #232's REASONING DOES NOT REACH.
    Mutation: make ``dataset_release`` free text by analogy with ``workload_profile``.

    The bottleneck is real and identical: registering a corpus takes
    ``config/datasets.yaml``, owned by nine, and this workflow file, owned by two. That is
    the exact two-file shape #232 removed for workload profiles, and reasoning from it to
    this field is the obvious move. It is wrong, and what makes it wrong is not the cost --
    which is the same -- but what the list is holding back.

    The workload dropdown was holding back *unregistered names*, and those were already
    refused while compiling. This one is not. An unregistered dataset name is refused while
    compiling too, so on that alone the list would be redundant. What it is actually holding
    back is *registered* names, and the platform refuses only some of them. A corpus whose
    tokenizer nothing here can construct, and one that declares none at all, are both
    resolvable, both trainable by family, and both reach an approved run with nothing
    anywhere saying a word. What a run picking one costs is a whole GPU allocation spent
    reaching a container that cannot construct a model for the tokens it just resolved,
    exiting 69, which is ``config/datasets.yaml``'s own description of that state.

    So for those the option list is not a second lock. It is the only one.

    **THIS SET HELD SEVEN AND HOLDS FIVE, WHICH IS THIS TEST WORKING RATHER THAN THIS TEST
    WEAKENING.** ``dolma-2026-07`` and ``fineweb-edu-1b-v2`` left it because the refusal
    they were waiting for got built. ``retired:`` used to have no enforcement anywhere --
    ``config/datasets.yaml`` said so in as many words, that the flag "keeps admission's
    answer and removes the menu item" -- and it now refuses in the compile job and on the
    laptop, through ``require_a_dataset_release_that_is_current``. That is exactly what the
    paragraph below promised would happen, and neither the set above nor this docstring
    would have moved on their own: ``refusals_for`` had to learn about the new refusal
    first, which is the edit that keeps this measuring the platform rather than measuring
    policy's list.

    Two of the five are the case that bites hardest and is easiest to miss.
    ``lean4-mathlib-bytes-v3`` and ``math-memory-full-v1`` depend on
    ``tokenizer/bytes-utf8``, which OLMo-core has no equivalent for, so the exclusion
    resolves itself the day upstream grows one rather than needing a refusal built here.

    SELF-RETIRING IN THE DIRECTION THAT MATTERS. Build the missing refusals and this set
    shrinks; empty it and this test says so, at which point the list has become the second
    lock #232 was about and can go. Register a corpus that is offered and the set does not
    move. The names are derived rather than listed, so nothing here needs editing when one
    is added -- only when what refuses it changes.
    """
    # Asked before the options are read rather than after, because ``options_for`` refuses
    # a field that is not a choice and would report the mutation as a missing list.
    assert form_inputs()["dataset_release"]["type"] == "choice", (
        "every name below compiles clean, classifies routine and is admitted, so the option "
        "list is the only thing that refuses them"
    )

    document = registry("datasets.yaml")
    registered = [entry["reference_id"] for entry in document.get("published", [])] + [
        entry["release_id"] for entry in document["releases"]
    ]
    offered = set(options_for("dataset_release"))

    held_back_only_by_this_form = {
        name for name in registered if name not in offered and not refusals_for(name)
    }

    assert held_back_only_by_this_form == {
        "frontload-cl-chat-sft-v1",
        "lean4-mathlib-bytes-v3",
        "math-memory-full-v1",
        "math-sft-60m-v1",
        "pedagogy70-normal30-v1",
    }, (
        "the set of registered datasets that nothing refuses has moved. If it shrank, a "
        "refusal was built and the argument for this being a choice is weaker by exactly "
        "that name; if it is empty, this field can be free text on #232's reasoning. If it "
        "grew, something registered became reachable that nothing checks."
    )


def test_the_two_names_this_set_lost_are_refused_rather_than_merely_unlisted() -> None:
    """Mutation: shrink the set above and let the reason for the shrinking go unrecorded.

    A derived set going down by two is the same shape whether a refusal was built or the
    derivation stopped seeing one, and only one of those is progress. So the two names are
    asserted on the other side of the move, under the code that now refuses them.

    That code is deliberately not a condition ``config/policy.yaml`` denies outright, which
    is why the assertion is on ``refusals_for`` rather than on
    ``denied_outright_conditions``. ``require_a_dataset_release_that_is_current`` carries
    the argument; the short of it is that a resume from a checkpoint written against a
    retired corpus has to go on naming that corpus, and a refusal nobody can lift would
    make naming a different one the only route.
    """
    for retired in ("dolma-2026-07", "fineweb-edu-1b-v2"):
        assert refusals_for(retired) == ("retired_dataset_release",)
    assert refusals_for("fineweb-edu-1b-v6") == ()


def test_an_unregistered_dataset_is_refused_by_something_other_than_this_form() -> None:
    """The half that IS the workload case, asserted so the argument above stays honest.

    Nothing here claims the dropdown is load-bearing against a typo. A name nothing
    registers is refused while compiling, before the approval gate and before any credential
    exists, exactly as an unregistered workload profile is -- so if the five above ever
    acquire refusals of their own, there is nothing left for the list to do.
    """
    assert refusals_for("no-such-corpus-v9") == ("unregistered_dataset",)


def test_the_workload_refusal_names_every_entry_the_declared_repository_registers() -> None:
    """THE ONE THAT MATTERS, ASKED WHERE THE ANSWER NOW LIVES. Mutation: list the whole
    catalog in the refusal, or hard-code the names into it.

    Trading a dropdown for a refusal is a good trade only while the refusal names what the
    dropdown would have offered. So this is the equality that used to hold
    ``options_for("workload_profile")`` against the catalog, moved onto the text a submitter
    reads, and it still fails in both directions: an entry a lead adds and the refusal does
    not name is a workload nobody can find, and a name in the refusal that the catalog does
    not carry for this repository is a suggestion whose only outcome is another refusal.

    ASKED PER REPOSITORY BECAUSE THE WHOLE CATALOG WAS NEVER THE ANSWER. The check
    immediately after this one in ``compile_submission`` refuses a workload written for
    another codebase, so seven of the nine entries would be a suggestion that cannot be
    taken. That is the ``dolma-tokenize`` defect this module was written about, arriving
    inside an error message instead of on a menu.

    ``dolma-tokenize`` itself is the live instance and is asserted separately below, because
    it cannot be reached from any repository the form offers at all.
    """
    catalog_names = {workload.name for workload in workload_catalog().workloads}
    # Containment is how the comparison below reads the refusal, so a name that is a
    # substring of another would make one of them unfalsifiable. No catalog name is one
    # today; this is what notices the day somebody registers `olmo-core` beside
    # `olmo-core-check`, rather than the comparison quietly weakening.
    for name in catalog_names:
        others = catalog_names - {name}
        assert not any(name in other for other in others), (
            f"{name!r} is a substring of another catalog entry, so a refusal naming the "
            "other one reads as naming this one and the comparison below cannot fail"
        )
        assert name not in UNREGISTERED_WORKLOAD and UNREGISTERED_WORKLOAD not in name

    for repository in sorted(registered_repositories()):
        refusal = workload_refusal(repository)
        named = {name for name in catalog_names if name in refusal}

        assert named == workloads_for(repository), (
            f"the refusal a submitter naming {repository} meets and "
            "config/workload-catalog.yaml disagree about what could have been typed; the "
            "form offers no list any more, so this refusal is the only thing that answers"
        )
        assert named, (
            f"nothing is registered for {repository}, so the comparison above is between "
            "two empty sets and the refusal could say anything"
        )
        assert "config/workload-catalog.yaml" in refusal, (
            "the refusal names alternatives and not the file they live in, so a submitter "
            "who wants a workload nobody has written cannot find where to add it"
        )


def test_no_workload_refusal_suggests_the_one_entry_nothing_can_run() -> None:
    """Mutation: name every catalog entry in the refusal, since all of them are real.

    ``dolma-tokenize`` is in the catalog and names a repository nothing registers, so there
    is no image for it to run and no repository on the form it can be paired with. It was
    kept off the dropdown for that reason and it has to stay out of the refusal for the same
    one: a name offered to somebody who has just been refused is a second refusal waiting.

    Written against the registry rather than against the word, so that registering dolma
    resolves this on its own -- the entry becomes reachable, the comparison above starts
    demanding it, and this assertion starts demanding its absence, and the two cannot both
    be satisfied until somebody deletes this. That is the intent.
    """
    registered = registered_repositories()
    unregistered = {
        workload.repository for workload in workload_catalog().workloads
    } - registered

    assert unregistered == {"dolma"}, (
        "the set of catalog repositories nothing registers has changed; a workload written "
        "for one of them can be named by no submission the form can express"
    )
    for repository in sorted(registered):
        assert "dolma-tokenize" not in workload_refusal(repository), (
            "dolma has no registration and no ECR repository, so suggesting its workload "
            "sends a refused submitter to a second refusal; when it is registered this "
            "assertion is what has to be deleted"
        )


def test_every_compute_profile_the_form_offers_has_somewhere_to_run() -> None:
    """THE SAME DEFECT, ASKED WHERE THE MACHINE IS NOW CHOSEN. Mutation: offer every priced
    profile.

    This used to walk the workload dropdown and check the compute profile each entry
    inherited. That join was the wrong one to check even while it existed, because the
    compute field overrode whatever a workload declared: a submitter who picked a profile
    with no execution target got ``no_execution_target`` regardless of which workload they
    named, and this test could not see it. The catalog declares no machine now and the
    field is required, so the question is asked of the list the submitter actually picks
    from.

    Written as a comparison rather than a loop, so it fails in both directions. A profile
    provisioned and not offered is a machine nobody can reach; a profile offered and not
    backed is a menu item whose only outcome is a refusal after a lead has spent an
    approval on it.

    Asserted through ``resolve_execution_target`` rather than by reading ``provisioned``
    out of the catalog, so this fails for the reason the submitter would meet: the resolver
    separates a profile nobody registered from one nobody provisioned from one whose two
    configuration files disagree, and any of the three is a refused submission.

    THE EXCLUDED PROFILES ARE DERIVED RATHER THAN NAMED, WHICH IS A DELIBERATE WEAKENING.
    Naming one pins a deployment fact into a test, so promoting that profile turns this red
    for a reason that is not a defect. What is asserted instead is that the catalog prices
    at least one profile the form withholds, so the comparison above has something to be
    right about.
    """
    offered = options_for("compute_profile")
    priced = {profile.name for profile in workload_catalog().compute_profiles}
    withheld = sorted(priced - set(offered))

    assert offered == offerable_compute_profiles()
    assert offered, "the compute dropdown is empty, so the comparison above proves nothing"
    assert withheld, (
        "every priced compute profile is offered, so this test would pass on a catalog that "
        "had stopped withholding anything; if that is now genuinely true, the assertion "
        "below has to be replaced by something else that can fail"
    )
    for name in withheld:
        assert resolution_failure(name) is not None, (
            f"{name} is priced, resolves to an execution target and is not offered, so a "
            "machine this platform can start is unreachable from the submission form"
        )


def test_a_workload_that_reads_a_corpus_is_reachable_and_can_be_run_long_enough() -> None:
    """Mutation: drop the training profile from the catalog and leave the checks above.

    Every test around this one compares the two directions of a list, so all of them pass
    on an empty catalog -- naming nothing names nothing unbacked. What none of them says is
    that the one workload a researcher actually came here for is findable.

    ``olmo-core-train`` is the collapse of ``olmo-core-train-1gpu`` and
    ``olmo-core-train-4gpu``, which carried identical bounds and differed only in a machine
    the form overrode, so the pair this test used to require is one entry and the machine is
    a separate field. Its bounds are twenty-four hours against the policy ceiling and two
    attempts, and it declares a checkpoint contract so the second attempt resumes instead of
    repeating the first at full price.

    ASKED OF THE REFUSAL RATHER THAN OF A DROPDOWN, because there is no dropdown. A
    submitter reaches this entry by typing its name, so what "findable" means now is that
    somebody who typed something else is told about it.
    """
    assert "olmo-core-train" in workload_refusal("OLMo-core")

    workload = next(
        candidate
        for candidate in workload_catalog().workloads
        if candidate.name == "olmo-core-train"
    )
    # A run long enough to read a corpus, with somewhere for the interrupted half to live.
    assert int(workload.maximum_runtime_hours) > 1
    assert workload.checkpoint is not None
    # And a machine to run it on, which is the submitter's choice and has to be offerable.
    assert "gpu-4xa10g" in options_for("compute_profile")


def test_resolving_a_compute_profile_is_the_same_question_the_provisioned_flag_answers() -> None:
    """Mutation: mark a profile provisioned and deploy no execution target for it.

    ``offerable_workloads`` asks the resolver rather than the flag, which is the stronger
    read of the two -- but only while the two agree. They can disagree in exactly one way,
    and it is a configuration contradiction rather than an honest state: a profile the
    catalog calls provisioned that ``config/execution-targets.yaml`` does not back. The
    resolver has its own error for that, and this is what stops the dropdown offering such
    a profile on the strength of the flag alone.

    THE SET SHRANK BY TWO ON 2026-08-04 AND NEITHER FILE WAS WRONG. gpu-1xh100 and
    gpu-8xh100 were withdrawn from both together, which is what keeps the equality above
    true. Everything this test guards is about the two files agreeing; what it cannot see is
    the third thing that has to be true for an offer to be honest, which is that EC2 will
    sell the account the instance. It will not sell either p5, and no seam in this
    repository could have said so -- the environment, the queue, the definition and both
    roles all exist and are healthy. That is why the demotion is a measurement recorded in
    config/workload-catalog.yaml rather than a rule anything here can enforce.
    """
    provisioned = {
        profile.name for profile in workload_catalog().compute_profiles if profile.provisioned
    }

    assert provisioned == execution_targets().backed_profiles
    assert provisioned == {
        "cpu-32vcpu",
        "gpu-1xt4",
        "gpu-4xt4",
        "gpu-8xt4",
        "gpu-1xa10g",
        "gpu-4xa10g",
        "gpu-8xa10g",
        "gpu-1xl4",
        "gpu-4xl4",
        "gpu-8xl4",
        "gpu-1xl40s",
        "gpu-4xl40s",
        "gpu-8xl40s",
        "gpu-8xa100",
    }
    for name in provisioned:
        assert resolution_failure(name) is None


def test_the_compute_dropdown_offers_exactly_the_provisioned_profiles() -> None:
    """Mutation: offer a profile the catalog prices but nothing backs.

    Offering an unprovisioned profile is the same failure as the dolma workload: a
    selectable option whose only outcome is a refusal, this time
    ``unprovisioned_compute_profile``.

    Read straight off the ``provisioned`` flag rather than through the resolver, which is
    the weaker of the two reads and is the point of having both. The test above asks the
    resolver, so the two together say that the dropdown, the flag and the deployed target
    are one answer; on their own either could agree with a dropdown the other refuses.
    """
    catalog = registry("workload-catalog.yaml")["compute_profiles"]
    provisioned = sorted(
        profile["name"] for profile in catalog if profile.get("provisioned") is True
    )

    assert options_for("compute_profile") == provisioned


def test_the_form_spells_absence_nowhere_because_nothing_on_it_is_absent() -> None:
    """Mutation: leave the sentinel in place beside a required field.

    A ``choice`` option cannot be blank, so while ``compute_profile`` was an override it
    spelled "take the registered default" as ``inherit`` and the workflow translated it back
    to nothing before assembling the form. Nothing on this form takes a registered default
    from the catalog any more, because the catalog declares no machine, so the sentinel
    would be a default that resolves to nothing and every submission that left it alone
    would be refused for naming a compute profile nobody registered.

    Three places, because the word lived in three and leaving any one of them would put it
    back within a rename: an option on the dropdown, the input's default, and the constant
    the assembly step translated against. The last is asserted on the identifier rather than
    on the word, so that a comment explaining why the sentinel is gone does not read as the
    sentinel still being there.
    """
    inputs = form_inputs()

    for name, spec in inputs.items():
        assert spec.get("default") != RETIRED_SENTINEL, name
        assert RETIRED_SENTINEL not in spec.get("options", ()), name
    assert "INHERIT" not in WORKFLOW.read_text(encoding="utf-8"), (
        "the assembly step still translates a sentinel away; there is nothing left for a "
        "field to take a registered default from, so nothing needs a word for its absence"
    )
    assert inputs["compute_profile"]["required"] is True


def test_every_registry_backed_field_has_no_second_input_for_the_same_value() -> None:
    """Mutation: keep the old string input beside the new choice one.

    Two inputs for one value is worse than either alone, because the workflow reads one and
    the researcher may have filled the other. The form has to have exactly one field per
    thing it asks for.

    ``workload_profile`` is in the twin check and out of the ``choice`` check, and both
    halves of that are deliberate. It is free text now for the reason recorded above, so
    asserting its type here would be the second place that decision lives; a twin beside it
    would be the same defect as ever, and a slightly worse one -- an ``_other`` box next to
    a text box is a field whose only purpose is to be read by nothing.
    """
    inputs = form_inputs()
    names = list(inputs)

    assert len(names) == len(set(names))
    for field in ("repository", "workload_profile", "dataset_release", "team", "compute_profile"):
        assert f"{field}_name" not in inputs
        assert f"{field}_other" not in inputs
    for field in ("repository", "dataset_release", "team", "compute_profile"):
        assert inputs[field]["type"] == "choice"


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
        workload_profile="olmo-core-check",
        compute_profile="cpu-32vcpu",
        maximum_runtime_hours="1",
        maximum_attempts=1,
        checkpoint=None,
        fanout=None,
    )
