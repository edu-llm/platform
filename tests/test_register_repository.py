"""Registering a repository, held to the shape the eight files it touches already have.

REGISTRATION WAS NEVER ONE FILE, AND THAT IS WHAT THE TOOL UNDER TEST IS FOR. An entry in
``config/repositories.yaml`` on its own lands a red pull request. The ECR template must
declare a repository for every registration, and the publisher role must carry the GitHub
repository id, the subject pattern and the destination ARN. Six tests across two modules
read those pairings in both directions, and each of them fails on a half-registration --
which is the failure ``edullm-data`` actually shipped, inert for a day behind an AssumeRole
denial that reads like a broken role ARN.

**THE OTHER FAILURE ``edullm-data`` SHIPPED WAS QUIETER AND LASTED LONGER.** A registration
with no entry in ``config/workload-catalog.yaml`` never reaches the submission form's
``repository`` dropdown, so nothing can ever be submitted for it -- and nothing went red,
because the test that should have caught it compared the dropdown against the registered
repositories *that have a workload profile*, a filter that removed the broken case from
both sides. The tool writes the catalog entry and the two dropdown options now, so the
registration it produces is submittable rather than merely publishable.

So the tests here are mostly about the whole change rather than about the tool's internals.
:func:`test_a_registration_satisfies_every_invariant_the_suite_asserts_about_these_files`
re-runs those pairings against a tree the tool has written, which is the only assertion that
would notice the tool learning to write two of the three files.

**The derivation tests read the registrations that exist.** ``edullm-data`` publishes to
``sbsandbox-intern-edullm-data`` rather than to ``sbsandbox-intern-edullm-edullm-data``,
which is not a rule anybody wrote down and is the reason a derived destination name needs
checking against reality rather than against itself.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import quote

import pytest
import yaml
from workflow_support import (
    GitHubActionsLoader,
    load_workflow,
    run_step_script,
    step,
    unreal_context_references,
    write_stub,
)

from edullm_platform.cli.actions import registration_compare_url
from edullm_platform.config import load_yaml
from edullm_platform.contracts.repository_registry import RepositoryRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import register_repository

WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "register-repository.yml"
WORKFLOW_FILE = "register-repository.yml"

TOUCHED = (
    "config/repositories.yaml",
    "config/workload-catalog.yaml",
    ".github/workflows/submit-run.yml",
    "infra/ecr-repositories.yaml",
    "infra/iam/ecr-publisher-role.yaml",
    "infra/iam/batch-roles.yaml",
    "infra/iam/batch-gpu-roles.yaml",
    "infra/iam/admission-service-roles.yaml",
)

#: The five roles that have to reach an image the registration has not built yet, paired
#: with the template that declares each. Duplicated from ``IMAGE_PULLING_ROLES`` in
#: ``tests/test_phase3_infrastructure.py`` and one entry of the states role's grant, because
#: what is being asserted here is different: that module holds the shipped files equal to
#: the shipped registry, and this one holds the tool to writing every one of them for a
#: registration that has not shipped. A shared constant would make a tool that writes none
#: of them pass both.
PULL_GRANTS = (
    ("infra/iam/batch-roles.yaml", "sbsandbox-intern-edullm-batch-execution"),
    ("infra/iam/batch-roles.yaml", "sbsandbox-intern-edullm-batch-instance"),
    ("infra/iam/batch-gpu-roles.yaml", "sbsandbox-intern-edullm-batch-gpu-execution"),
    ("infra/iam/batch-gpu-roles.yaml", "sbsandbox-intern-edullm-batch-gpu-instance"),
    (
        "infra/iam/admission-service-roles.yaml",
        "sbsandbox-intern-edullm-admission-states",
    ),
)

REASON = (
    "A mixture-of-experts trainer whose fused kernels need a build step OLMo-core does not "
    "carry, so sharing an image would put a compiler in every training run."
)

#: The three registrations that existed when the destination-name derivation was written,
#: as a literal rather than read from the registry. Reading it would compare a rule against
#: whatever the file says today and pass on a fourth entry that was named by hand, which is
#: precisely the case ``--ecr-repository`` exists for. These three are what the rule has to
#: reproduce, including the collapse that makes ``edullm-data`` the odd one.
OBSERVED_DERIVATIONS = (
    ("OLMo-core", "sbsandbox-intern-edullm-olmo-core", "OlmoCoreRepository"),
    ("edullm-data", "sbsandbox-intern-edullm-data", "EdullmDataRepository"),
    ("olmo-eval-full", "sbsandbox-intern-edullm-olmo-eval-full", "OlmoEvalFullRepository"),
)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """The three files a registration touches, copied out of the repository as they are.

    Copied rather than fabricated, because a fixture registry would be a second opinion
    about the shape of these files and the tool inserts into them by anchoring on that
    shape. A minimal fixture would keep passing after a reshuffle that broke the tool.
    """
    for relative in TOUCHED:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            (PROJECT_ROOT / relative).read_text(encoding="utf-8"), encoding="utf-8"
        )
    return tmp_path


class Result(NamedTuple):
    """One invocation, read once.

    ``capsys.readouterr()`` drains the buffer, so both streams are taken together here and
    handed back. A test that called it a second time for stderr would read an empty string
    and pass whatever the tool had said.
    """

    code: int
    record: Any
    stderr: str


def run(tree: Path, capsys: pytest.CaptureFixture[str], /, **overrides: str) -> Result:
    """One invocation, offline unless a test says otherwise.

    ``--offline`` IS THE DEFAULT HERE AND NOT IN THE TOOL, and the asymmetry is deliberate.
    Every test in this module is about what gets written into eight files, and none of them
    is about GitHub; a suite that reached the network to answer those questions would be slow,
    flaky and dependent on the state of six repositories nobody here controls. The claim reads
    are exercised against a stubbed ``gh`` in the section at the bottom, where the subject is
    the reads themselves.
    """
    arguments = {
        "--repository": "olmo-mixer",
        "--github-repository-id": "1399999999",
        "--dockerfile-path": ".edullm/Dockerfile",
        "--reason": REASON,
        "--project-root": str(tree),
        "--offline": "",
    }
    arguments.update(overrides)
    code = register_repository.main(
        [part for pair in arguments.items() for part in pair if part]
    )
    captured = capsys.readouterr()
    return Result(
        code,
        json.loads(captured.out) if captured.out.strip() else None,
        captured.err,
    )


def registry_of(tree: Path) -> RepositoryRegistry:
    return load_yaml(tree / "config/repositories.yaml", RepositoryRegistry)


def loaded(tree: Path, relative: str) -> Any:
    return yaml.safe_load((tree / relative).read_text(encoding="utf-8"))


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


# ---------------------------------------------------------------------------------------
# The whole change, held to what the suite already asserts about these files
# ---------------------------------------------------------------------------------------


def test_a_registration_satisfies_every_invariant_the_suite_asserts_about_these_files(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: write the registry entry and not the template, or not the publisher role.

    The six pairings restated here are the ones ``tests/test_phase1_infrastructure.py`` and
    ``tests/test_repository_registry.py`` assert about the shipped files. They are re-run
    against a tree this tool wrote rather than trusted from its own verification pass,
    because the verification pass and the tool are the same author and would agree with
    each other about a mistake they shared.
    """
    result = run(tree, capsys)
    assert result.code == 0, result.stderr

    registry = registry_of(tree)
    entry = registry.repository_by_name("olmo-mixer")
    assert entry.ecr_repository == "sbsandbox-intern-edullm-olmo-mixer"
    assert entry.dockerfile_path == ".edullm/Dockerfile"
    assert entry.build_context == "."
    assert entry.default_branch == "main"

    template = loaded(tree, "infra/ecr-repositories.yaml")
    repositories = {
        logical_id: resource
        for logical_id, resource in template["Resources"].items()
        if resource.get("Type") == "AWS::ECR::Repository"
    }
    assert {
        resource["Properties"]["RepositoryName"] for resource in repositories.values()
    } == {item.ecr_repository for item in registry.repositories}
    assert {
        value["Value"]["Ref"] for value in template["Outputs"].values() if "Ref" in value["Value"]
    } == set(repositories)

    publisher = loaded(tree, "infra/iam/ecr-publisher-role.yaml")
    role = next(
        resource
        for resource in publisher["Resources"].values()
        if resource.get("Type") == "AWS::IAM::Role"
    )
    condition = role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]["Condition"]
    owner = condition["StringEquals"]["token.actions.githubusercontent.com:repository_owner_id"]
    assert {
        str(value)
        for value in as_list(
            condition["StringEquals"]["token.actions.githubusercontent.com:repository_id"]
        )
    } == {str(item.github_repository_id) for item in registry.repositories}
    assert {
        str(value)
        for value in as_list(condition["StringLike"]["token.actions.githubusercontent.com:sub"])
    } == {
        f"repo:edu-llm@{owner}/{item.repository}@{item.github_repository_id}:ref:refs/heads/*"
        for item in registry.repositories
    }
    assert {
        str(resource["Fn::Sub"]).rsplit("repository/", 1)[-1]
        for policy in role["Properties"]["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
        for resource in as_list(statement["Resource"])
        if isinstance(resource, dict)
    } == {item.ecr_repository for item in registry.repositories}


def reachable_ecr_repositories(tree: Path, relative: str, role_name: str) -> set[str]:
    """Every ECR repository one role's policies name, read out of a written template."""
    template = loaded(tree, relative)
    role = next(
        resource
        for resource in template["Resources"].values()
        if resource.get("Type") == "AWS::IAM::Role"
        and resource["Properties"].get("RoleName") == role_name
    )
    return {
        str(resource["Fn::Sub"]).rsplit(":repository/", 1)[1]
        for policy in role["Properties"].get("Policies", [])
        for statement in policy["PolicyDocument"]["Statement"]
        for resource in as_list(statement["Resource"])
        if isinstance(resource, dict) and ":repository/" in str(resource.get("Fn::Sub", ""))
    }


def test_the_registration_can_pull_the_image_it_can_push(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: widen the publisher role and none of the other four.

    THIS IS THE ONE THAT WOULD HAVE CAUGHT THE GAP THIS TOOL SHIPPED WITH. Until 2026-08-05
    it wrote the publisher role and nothing else, so a registration made with it could push
    an image and no identity in the account could fetch it back. The failure arrives last
    and reads least like its cause: the scan is read, the decision recorded, the job
    definition registered, the job submitted, the queue finds capacity and an instance
    scales up and joins the cluster, and what lands is a CannotPullContainerError inside a
    job that has already cost money, naming a registry path rather than a policy.

    All five grants are asserted in one pass because widening four is the likelier mistake
    than widening none: the two batch roles are spelled twice, once per compute stack, so a
    CPU-only widening leaves every CPU submission working and every GPU one dead.
    """
    assert run(tree, capsys).code == 0

    destinations = {item.ecr_repository for item in registry_of(tree).repositories}
    assert "sbsandbox-intern-edullm-olmo-mixer" in destinations
    for relative, role_name in PULL_GRANTS:
        assert destinations <= reachable_ecr_repositories(tree, relative, role_name), (
            f"{role_name} in {relative} cannot reach every registered repository"
        )


def test_a_grant_list_that_stopped_naming_repositories_is_a_refusal_rather_than_a_guess(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: return the text unchanged instead of raising when nothing was widened.

    The insertion anchors on a ``Resource`` list already naming a registered repository,
    which is what lets it find five statements across three files without being told which
    roles they belong to. The cost of that is that a template rewritten to scope its grants
    some other way -- a wildcard over the project prefix, say, which would be a perfectly
    good change -- offers nothing to anchor on. Writing the other seven files and quietly
    skipping that one is the failure this tool exists to prevent, so it refuses instead and
    leaves the tree untouched.
    """
    path = tree / "infra/iam/batch-gpu-roles.yaml"
    before = path.read_text(encoding="utf-8")
    path.write_text(
        before.replace(":repository/sbsandbox-intern-edullm-", ":repository/unrelated-"),
        encoding="utf-8",
    )

    result = run(tree, capsys)

    assert result.code == register_repository.EXIT_UNUSABLE
    assert "no_per_repository_grant_to_widen:infra/iam/batch-gpu-roles.yaml" in result.stderr
    assert "olmo-mixer" not in (tree / "config/repositories.yaml").read_text(encoding="utf-8")


def test_the_new_repository_carries_the_properties_every_other_one_carries(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: drop `ImageTagMutability`, or write a lifecycle policy of its own.

    Immutability is the property the whole digest-pinning design rests on and the only one
    whose absence is invisible until somebody overwrites a tag. The lifecycle policy is
    copied off a repository already in the template rather than restated, so this also
    fails if that copy stops happening and a literal goes stale instead.
    """
    assert run(tree, capsys).code == 0

    template = loaded(tree, "infra/ecr-repositories.yaml")
    added = template["Resources"]["OlmoMixerRepository"]
    assert added["DeletionPolicy"] == "Retain"
    assert added["UpdateReplacePolicy"] == "Retain"
    assert added["Properties"]["EncryptionConfiguration"] == {"EncryptionType": "AES256"}
    assert added["Properties"]["ImageScanningConfiguration"] == {"ScanOnPush": True}
    assert added["Properties"]["ImageTagMutability"] == "IMMUTABLE"

    policies = {
        json.dumps(
            json.loads(resource["Properties"]["LifecyclePolicy"]["LifecyclePolicyText"]),
            sort_keys=True,
        )
        for resource in template["Resources"].values()
        if resource.get("Type") == "AWS::ECR::Repository"
    }
    assert len(policies) == 1


def test_the_comment_above_the_entry_carries_the_reason_that_was_given(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: make ``--reason`` optional, or write it only into the pull request body.

    A pull request body stops being reachable once the branch is deleted and the number is
    all that survives. The one question a reviewer cannot answer from the diff is why this
    needs a home of its own, so the answer goes in the file it is about.
    """
    assert run(tree, capsys).code == 0

    text = (tree / "config/repositories.yaml").read_text(encoding="utf-8")
    comment = "".join(
        line.strip().removeprefix("#").strip() + " "
        for line in text.splitlines()
        if line.strip().startswith("# olmo-mixer:") or line.strip().startswith("# carry,")
    )
    assert "olmo-mixer:" in comment
    assert "fused kernels" in comment
    # Wrapped without breaking on hyphens, so a repository name stays greppable in the
    # comment that identifies it.
    assert "OLMo-core" in text.split("- repository: olmo-mixer")[0].rsplit("olmo-mixer:", 1)[-1]


def test_the_registration_is_submittable_and_not_merely_publishable(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """THE HALF THAT USED TO BE LEFT UNDONE AND WENT UNNOTICED. Mutation: write the registry,
    the template and the publisher role, and stop.

    A registration with no entry in ``config/workload-catalog.yaml`` has nothing a run can
    name, so a submission naming the repository is refused whatever else is in place. That
    is invisible from the three infrastructure files -- the image builds, the scan runs, the
    digest is resolvable -- and it is the state ``edullm-data`` was registered in.

    The ``repository`` dropdown is read back in full rather than searched, because
    ``tests/test_submission_form_options.py`` holds it to the registry sorted
    case-insensitively. An option appended to the end is a green tool and a red suite.

    THE WORKLOAD IS CHECKED IN THE CATALOG AND NOT ON THE FORM, WHICH IS THE CHANGE. That
    input is free text, so a catalog entry is selectable the moment it merges; what makes
    the registration submittable is the entry existing, and there is no second list for it
    to be missing from. The form is asserted to still be free text, because a ``choice``
    here would make the entry this tool just wrote unselectable and nothing else would say
    so.
    """
    record = run(tree, capsys).record

    catalog = loaded(tree, "config/workload-catalog.yaml")
    added = next(item for item in catalog["workloads"] if item["name"] == "olmo-mixer-check")
    assert record["workload_profile"] == "olmo-mixer-check"
    assert added["repository"] == "olmo-mixer"
    # Base-ten text rather than a YAML number. A bound that went through binary floating
    # point is not the figure the approver reads off the summary.
    assert added["maximum_runtime_hours"] == "1"
    assert added["maximum_attempts"] == 1
    assert added["checkpoint"] is None

    inputs = yaml.safe_load(
        (tree / ".github/workflows/submit-run.yml").read_text(encoding="utf-8")
    )[True]["workflow_dispatch"]["inputs"]
    repositories = list(inputs["repository"]["options"])

    assert "olmo-mixer" in repositories
    assert repositories == sorted(repositories, key=str.lower)
    assert repositories == sorted(
        {entry.repository for entry in registry_of(tree).repositories}, key=str.lower
    )
    assert inputs["workload_profile"]["type"] == "string"
    assert "options" not in inputs["workload_profile"]


def test_the_catalog_entry_says_which_of_its_numbers_nobody_measured(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: write the bounds with no comment, or with one that reads as a decision.

    One hour and one attempt is the shape of a check, and it is what this tool writes
    whatever the repository turns out to run. A reader who takes it as policy somebody set
    for this workload has been misled by a file that is otherwise a policy document, so the
    entry has to say that the numbers are a default and that the machine is not named here
    at all.
    """
    assert run(tree, capsys).code == 0

    text = (tree / "config/workload-catalog.yaml").read_text(encoding="utf-8")
    comment = " ".join(
        line.strip().removeprefix("#").strip()
        for line in text.rsplit("- name: olmo-mixer-check", 1)[0].splitlines()
        if line.strip().startswith("#")
    )

    assert "olmo-mixer-check:" in comment
    assert "fused kernels" in comment, "the registration's reason belongs on the entry too"
    assert "not a measurement" in comment
    assert "compute_profile" in comment


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"--workload-profile": "olmo-core-check"}, "already declares a workload profile"),
        ({"--workload-profile": "olmo mixer check"}, "letters, digits"),
        ({"--maximum-attempts": "2"}, "checkpoint contract"),
        ({"--maximum-runtime-hours": "0"}, "not a valid entry"),
    ],
    ids=[
        "a workload name the catalog already uses",
        "a workload name that is not a plain scalar",
        "a retry with nowhere to resume from",
        "a runtime bound of zero",
    ],
)
def test_a_workload_profile_that_cannot_be_written_is_refused_before_anything_is(
    tree: Path,
    capsys: pytest.CaptureFixture[str],
    overrides: dict[str, str],
    expected: str,
) -> None:
    """Mutation: accept two attempts with no checkpoint contract, since Batch allows it.

    ``WorkloadProfile`` refuses that pairing and this tool can produce it from two flags, so
    the refusal is raised where the flags are named. A second attempt with nowhere to resume
    from does not fail -- it repeats the whole of the first attempt at full price and
    succeeds, which is the expensive shape of being right.

    All five files stay untouched, for the reason the registry refusals above give: a tree
    carrying a catalog entry whose registration was never written is the half state this
    tool exists to make unreachable, arriving from the new end of it.
    """
    before = {relative: (tree / relative).read_text(encoding="utf-8") for relative in TOUCHED}

    result = run(tree, capsys, **overrides)

    assert result.code == register_repository.EXIT_REFUSED
    assert expected in result.stderr
    for relative, text in before.items():
        assert (tree / relative).read_text(encoding="utf-8") == text


def test_a_dropdown_option_lands_in_sorted_position_rather_than_at_the_end(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: append the option, which is what every other insertion here does.

    ``insert_after_last_list_item`` is right for the publisher role's three lists, whose
    order carries nothing, and wrong for this one, which is compared against a sorted
    registry. A name that sorts to the middle is what tells the two apart: appending it
    leaves a form that parses, offers the right set, and fails the equality the submission
    form tests hold it to.

    ONE LIST NOW, AND THE OTHER HALF OF THIS TEST MOVED RATHER THAN WENT. It also inserted a
    ``workload_profile`` option and asserted that the comment above the displaced entry
    moved with it, which was demonstrable because every option on that list carried one.
    There is no such list -- the input is free text -- so the comment behaviour is asserted
    directly against ``insert_form_option`` in the test below, where it does not depend on
    which dropdown happens to be commented today.
    """
    result = run(
        tree,
        capsys,
        **{"--repository": "nn-thing", "--github-repository-id": "1300000001"},
    )
    assert result.code == 0, result.stderr

    text = (tree / ".github/workflows/submit-run.yml").read_text(encoding="utf-8")
    inputs = yaml.safe_load(text)[True]["workflow_dispatch"]["inputs"]
    repositories = list(inputs["repository"]["options"])

    # It lands in the middle, which is where appending and inserting differ. `nn-thing`
    # sorts after `edullm-data` and before `OLMo-core` only under a case-insensitive key,
    # so this also fails if the list is sorted plainly.
    assert repositories == sorted(repositories, key=str.lower)
    assert repositories[repositories.index("nn-thing") + 1] == "OLMo-core"

    # The one comment on this list belongs to the list rather than to its first option, so
    # it stays above the whole thing however far forward an option sorts.
    heading = text.split("- edullm-alt-cl\n")[0]
    assert "Sorted case-insensitively" in heading


def test_a_comment_above_a_displaced_option_moves_with_the_option_it_describes() -> None:
    """Mutation: insert at the item and leave the comment where it was.

    A comment immediately above an option says what that option is, so an insertion between
    the two attaches the sentence to the wrong entry -- a form that parses, offers the right
    set, and lies to the next reader in a way no test of the parsed document can see.

    ASSERTED ON A FRAGMENT RATHER THAN ON THE REAL FORM, and that is the change. It used to
    be demonstrated on the ``workload_profile`` dropdown, whose every option carried a
    comment; that input is free text now and ``repository`` carries one comment for the
    whole list. So the behaviour would be untested exactly until somebody comments a
    repository option, which is the moment it starts mattering again.
    """
    form = (
        "on:\n"
        "  workflow_dispatch:\n"
        "    inputs:\n"
        "      repository:\n"
        "        type: choice\n"
        "        options:\n"
        "          - alpha\n"
        "          # What gamma is, which is a sentence about gamma and about nothing else.\n"
        "          - gamma\n"
    )

    inserted = register_repository.insert_form_option(
        form, "repository", "beta", key=str
    )

    displaced = inserted.split("- gamma")[0].rsplit("- beta", 1)[-1]
    assert "What gamma is" in displaced, (
        "beta was inserted between gamma and the comment explaining what gamma is, so the "
        "sentence now introduces the wrong option"
    )
    # Read through the tool's own reader, which knows that PyYAML turns a bare `on:` into
    # the boolean True. A comment moved into the wrong place can still leave a document that
    # parses, so this says the result is the list it should be as well as reading right.
    offered = register_repository.form_inputs_of(inserted)["repository"]["options"]
    assert list(offered) == ["alpha", "beta", "gamma"]


# ---------------------------------------------------------------------------------------
# The derivations, against the registrations that exist rather than against themselves
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("repository", "destination", "logical_id"),
    OBSERVED_DERIVATIONS,
    ids=[item[0] for item in OBSERVED_DERIVATIONS],
)
def test_the_derivations_reproduce_the_registrations_that_already_exist(
    repository: str, destination: str, logical_id: str
) -> None:
    """Mutation: derive the destination as the prefix plus the lowercased name.

    That is right for two of the three and wrong for ``edullm-data``, whose destination is
    ``sbsandbox-intern-edullm-data`` because the prefix already ends in the word the name
    starts with. The logical id has the same shape of trap in ``OLMo-core``, where the
    capitalisation inside the name is not the capitalisation the template uses.
    """
    assert register_repository.ecr_repository_name_for(repository) == destination
    assert register_repository.logical_id_for(repository) == logical_id


def test_every_registered_destination_is_one_the_deployer_may_actually_create() -> None:
    """THE CONTRACT IS WIDER THAN THE GRANT, AND THIS IS THE GAP.

    ``ECR_REPOSITORY_PATTERN`` accepts any ``sbsandbox-intern-`` name, because the prefix it
    is built from is the shared sandbox one. The deployer role scopes its ECR actions to
    ``repository/sbsandbox-intern-edullm-*``, narrower by one segment, and the account
    stops there. A registration naming ``sbsandbox-intern-something`` therefore validates,
    reviews cleanly, merges, and is refused by IAM when CloudFormation tries to create it,
    with the denial arriving in a deploy rather than in the change that caused it.
    """
    registry = load_yaml(PROJECT_ROOT / "config/repositories.yaml", RepositoryRegistry)

    outside = [
        entry.ecr_repository
        for entry in registry.repositories
        if not entry.ecr_repository.startswith(register_repository.DEPLOYABLE_ECR_PREFIX)
    ]

    assert not outside, (
        f"{outside} validate against the registry contract and sit outside the deployer "
        "ECR scope, so CloudFormation is denied on them"
    )


# ---------------------------------------------------------------------------------------
# What it refuses, and what it leaves alone when it does
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"--repository": "OLMo-core"}, "already registered"),
        ({"--github-repository-id": "1306868157"}, "already registered to"),
        ({"--repository": "olmo/core"}, "letters, digits"),
        ({"--repository": "olmo core"}, "letters, digits"),
        ({"--ecr-repository": "sbsandbox-intern-mixer"}, "may only create repositories"),
        (
            {"--base-image-repository": "docker.io/library/alpine"},
            "no reviewed digest to inherit",
        ),
    ],
    ids=[
        "a name already registered",
        "an id already registered",
        "a name carrying a path separator",
        "a name carrying a space",
        "a destination outside the deployer scope",
        "a base image nothing has reviewed",
    ],
)
def test_a_registration_that_cannot_be_written_is_refused_before_anything_is(
    tree: Path,
    capsys: pytest.CaptureFixture[str],
    overrides: dict[str, str],
    expected: str,
) -> None:
    """Mutation: write the files first and validate afterwards.

    Every refusal here has to leave three unchanged files rather than one changed and two
    not, because a tree carrying a registry entry with no ECR repository and no publisher
    trust is the state this whole tool exists to make unreachable. Exit 1 rather than 2,
    which distinguishes a registration that was refused from a tree that could not be read.
    """
    before = {relative: (tree / relative).read_text(encoding="utf-8") for relative in TOUCHED}

    result = run(tree, capsys, **overrides)

    assert result.code == register_repository.EXIT_REFUSED
    assert result.record is None
    assert expected in result.stderr
    for relative, text in before.items():
        assert (tree / relative).read_text(encoding="utf-8") == text


def test_a_destination_another_registration_already_uses_is_refused(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: let two repositories share one ECR repository.

    Nothing fails when they do. Tags are per-commit so they would not collide, and what is
    lost is silent. The repository an image came from stops being answerable from where it
    is stored, and the tag immutability the provenance chain rests on is protecting one
    namespace shared by two codebases.
    """
    result = run(tree, capsys, **{"--ecr-repository": "sbsandbox-intern-edullm-olmo-core"})

    assert result.code == register_repository.EXIT_REFUSED
    assert "already the destination for" in result.stderr


def test_an_unreadable_tree_is_a_different_exit_code_from_a_refused_registration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A caller has to tell "this registration is wrong" from "this checkout is wrong"."""
    result = run(tmp_path, capsys)

    assert result.code == register_repository.EXIT_UNUSABLE


def test_a_dry_run_reports_exactly_what_it_would_have_written_and_writes_nothing(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: make ``--dry-run`` skip the verification pass as well as the write.

    A dry run that does less checking than a real one answers a different question from the
    one it is being asked, and the answer it gives is the reassuring one.
    """
    before = {relative: (tree / relative).read_text(encoding="utf-8") for relative in TOUCHED}

    dry = run(tree, capsys, **{"--dry-run": ""}).record

    assert dry["dry_run"] is True
    for relative, text in before.items():
        assert (tree / relative).read_text(encoding="utf-8") == text

    wet = run(tree, capsys).record
    assert {key: value for key, value in wet.items() if key != "dry_run"} == {
        key: value for key, value in dry.items() if key != "dry_run"
    }


def test_the_base_image_digest_is_inherited_from_a_registration_that_pinned_it(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: default the digest to a literal.

    A literal is a fourth place the pin is written down, and re-pinning the base would then
    leave the next registration inheriting the digest before last. Reading it off an
    existing registration means the default follows the review.
    """
    registry = registry_of(tree)
    python_base = next(
        entry
        for entry in registry.repositories
        if entry.base_image_repository == "docker.io/library/python"
    )

    record = run(tree, capsys).record

    assert record["base_image_reference"] == python_base.immutable_base_reference
    assert record["base_image_already_registered"] is True
    assert registry_of(tree).repository_by_name("olmo-mixer").base_image_digest == (
        python_base.base_image_digest
    )


def test_a_base_nothing_has_reviewed_is_accepted_only_with_a_digest_and_is_flagged(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Base image review stays a human step, so this surfaces rather than refuses.

    A second base is a second thing to review, scan, re-pin and carry exceptions for, and
    ``config/image-exceptions.yaml`` keys its reviews on the vulnerability rather than on
    the image, so a base nothing else uses arrives with none of its findings read. The
    record says so and the pull request body repeats it, which is where a person decides.
    """
    digest = "sha256:" + "b" * 64

    record = run(
        tree,
        capsys,
        **{
            "--base-image-repository": "docker.io/nvidia/cuda-experimental",
            "--base-image-digest": digest,
        },
    ).record

    assert record["base_image_already_registered"] is False
    assert "will need its findings read" in record["pull_request_body"]


# ---------------------------------------------------------------------------------------
# The runbook the tool hands over, and the workflow that renders it
# ---------------------------------------------------------------------------------------


def test_every_follow_up_names_something_that_is_still_there() -> None:
    """Mutation: rename a tool and leave the runbook pointing at the old name.

    These five steps are the measured tail of a registration, and three of them are not
    predictable from the files the tool writes. A runbook naming a path that has moved is
    worse than none, because it reads as authoritative.
    """
    assert register_repository.FOLLOW_UPS

    for item in register_repository.FOLLOW_UPS:
        assert item.summary.strip()
        assert item.detail.strip()
        for relative in item.paths:
            assert (PROJECT_ROOT / relative).exists(), f"{item.summary}: {relative}"


def test_the_runbook_names_every_laptop_deploy_and_says_no_workflow_can_do_them() -> None:
    """The steps that cannot be automated, and the ones most likely to be assumed away.

    ``InternSandboxBoundary`` withholds ``iam:CreateRole`` and the rest of the role
    lifecycle from every CI role in this account, deliberately, so an IAM widening is a
    laptop operation and no amount of workflow design changes that. A runbook that listed
    one beside four things a tool can do would read as one more command to run.

    FOUR STACKS RATHER THAN ONE, WHICH IS THE PART THAT WAS MISSING. The publisher stack is
    what lets a build push. The other three are what let anything pull, and a registration
    that deploys only the first produces a repository that publishes an image nothing may
    fetch. Every stack a registration moves is asserted here by name, so adding a grant to a
    template without adding its deploy to the runbook is a red test.

    AND IT SAYS TO READ THE STACK BEFORE WRITING OVER IT. These four are laptop-applied, so
    a stack ahead of `main` is the ordinary state of this repository rather than a fault:
    two registrations a day apart both reach the account by hand. Deploying an older tree
    over a newer stack drops the newer registration's grants and CloudFormation reports a
    successful deploy, so the repository that published this morning stops being able to
    start a job and nothing anywhere says why. The warning is asserted rather than trusted
    to survive the next edit to this string.
    """
    laptop = [
        item
        for item in register_repository.FOLLOW_UPS
        if "laptop" in item.summary.lower()
    ]
    detail = " ".join(item.detail for item in laptop)

    assert len(laptop) == 2
    for stack in (
        "sbsandbox-intern-edullm-ecr-publisher-iam",
        "sbsandbox-intern-edullm-phase3-batch-iam",
        "sbsandbox-intern-edullm-phase4-gpu-iam",
        "sbsandbox-intern-edullm-phase2-admission-service-roles",
    ):
        assert stack in detail
    assert "no workflow can do this" in detail
    assert "get-template" in detail, (
        "the runbook has to say to read the deployed stack before applying this tree over "
        "it, because these deploys are by hand and a stack ahead of main is ordinary"
    )


def test_the_workflow_reaches_no_aws_account_at_all(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """THE ASSERTION THIS WHOLE FILE IS ORGANISED AROUND. Mutation: add `id-token: write`.

    The deployer role holds ``ecr:CreateRepository`` and could be made to create the
    repository directly. It must not be. Every ECR repository in this account is a
    CloudFormation resource in one stack, so a repository created through the API sits in
    the account with that stack still intending to create it, and the next Phase 1 deploy
    fails on a name that already exists and rolls back over every other registered
    repository.

    The token permission is what would make that reachable, so its absence is the control.
    Nothing else in the workflow needs it. There is no ``aws`` command, no role ARN and no
    OIDC anywhere in the file.
    """
    workflow = load_workflow(WORKFLOW_PATH)
    job = next(iter(workflow["jobs"].values()))
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert workflow["permissions"] == {"contents": "read"}
    # `pull-requests: write` came off with the `gh pr create` it was for. The organization
    # refuses that call whatever this workflow asks for, so the grant bought nothing and read
    # as a dependency the file had. Pushing the branch is the whole of what it writes.
    assert job["permissions"] == {"contents": "write"}
    assert "id-token" not in job["permissions"]
    assert "aws-actions/configure-aws-credentials" not in text
    assert "AWS_INFRA_DEPLOYER_ROLE_ARN" not in text
    for item in job["steps"]:
        assert "aws " not in str(item.get("run", ""))


def test_the_workflow_is_not_a_deploy_workflow_and_carries_no_deploy_guard() -> None:
    """Mutation: name it ``deploy-repository.yml``, or paste the admin guard into it.

    ``tests/test_deploy_authorization.py`` treats every ``deploy-*.yml`` as a workflow that
    reconciles a stack and must refuse a dispatch from outside the admin roster. This one
    reconciles nothing and opens a pull request, which anybody holding write can already do
    with two git commands, so the guard would forbid through a button what it does not
    forbid at all. The name has to stay outside that glob for the two decisions to keep
    agreeing with each other.
    """
    assert not WORKFLOW_FILE.startswith("deploy-")

    guard = "Refuse a hand-started deploy from somebody who may not make one"
    assert guard not in WORKFLOW_PATH.read_text(encoding="utf-8")


def test_the_workflow_references_nothing_github_does_not_define() -> None:
    # An unknown property on a known context resolves to the empty string rather than
    # failing the run, so a plausible typo is invisible until something downstream
    # misbehaves. Here that would be an empty repository name reaching the tool.
    assert unreal_context_references(WORKFLOW_PATH) == []


def test_the_workflow_hands_the_tool_every_input_it_offers() -> None:
    """Reads BOTH sides. Mutation: add an input to the form and not pass it through.

    An input nothing reads is worse than an absent one. The dispatch form asks for it, the
    person fills it in, and the registration is written as though they had not.
    """
    workflow = yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=GitHubActionsLoader)
    declared = set(workflow["on"]["workflow_dispatch"]["inputs"])
    step = next(
        item
        for item in next(iter(workflow["jobs"].values()))["steps"]
        if "tools/register_repository.py" in str(item.get("run", ""))
    )

    assert {name.lower() for name in step["env"]} == declared
    for name in declared:
        assert f"${{{{ inputs.{name} }}}}" in yaml.dump(step["env"])


def test_the_dispatch_form_defaults_and_the_tool_defaults_are_the_same_values() -> None:
    """Reads BOTH sides. Mutation: change the default base image in one of the two places.

    The form has to state its defaults, because a dispatch form that shows an empty box is
    a form nobody can answer without reading the tool. Two statements of one value drift,
    and the direction that hurts is the form winning, because an empty string passed
    explicitly overrides an argparse default rather than falling back to it.
    """
    workflow = yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=GitHubActionsLoader)
    form = workflow["on"]["workflow_dispatch"]["inputs"]
    parser = {
        action.dest: action.default for action in register_repository.build_parser()._actions
    }

    assert form["base_image_repository"]["default"] == parser["base_image_repository"]
    assert form["default_branch"]["default"] == parser["default_branch"]
    assert form["dockerfile_path"]["default"] == ".edullm/Dockerfile"
    # Empty rather than absent, and the tool has to read it as "inherit the reviewed one".
    # A required input here would make the ordinary registration ask for a digest nobody
    # has any business re-deciding.
    assert form["base_image_digest"]["default"] == ""
    assert parser["base_image_digest"] is None


def _handover(
    tree: Path, tmp_path: Path, record: dict[str, Any], stub_bin: Path
) -> tuple[str, str]:
    """Run the two shell steps and give back what the second one printed and summarised."""
    summary = tmp_path / "summary.md"
    summary.write_text("", encoding="utf-8")
    environment = {
        "HOME": str(tmp_path),
        "RUNNER_TEMP": str(tmp_path),
        "RECORD": str(tree / "registration.json"),
        "GH_TOKEN": "stub",
        "GITHUB_STEP_SUMMARY": str(summary),
        "PLATFORM_REPOSITORY": "edu-llm/platform",
        "SERVER_URL": "https://github.com",
    }
    job = next(iter(load_workflow(WORKFLOW_PATH)["jobs"].values()))
    printed = ""
    for name in (
        "Commit the registration to a branch of its own",
        "Push the branch and hand the pull request to a person",
    ):
        outcome = run_step_script(
            step(job, name)["run"], cwd=tree, env=environment, stub_bin=stub_bin
        )
        assert outcome.returncode == 0, f"{name}: {outcome.stdout}{outcome.stderr}"
        printed = outcome.stdout
    assert record["branch"] in printed
    return printed, summary.read_text(encoding="utf-8")


def test_the_two_shell_steps_run_end_to_end_and_hand_over_the_pull_request_they_describe(
    tree: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """BOTH STEPS EXECUTED RATHER THAN READ, WHICH IS THE ONLY WAY THREE OF THESE SHOW UP.

    A workflow can be well-formed YAML, reference only real contexts, pass every argument
    its tool requires, and still exit non-zero on the first dispatch. Three things here are
    exactly that shape, and none of them is visible in a parsed workflow.

    ``read -r`` returns non-zero at end of file when it has not met its delimiter, so a
    branch or title file written without a trailing newline aborts its step under ``set -e``
    after assigning the variable correctly, which reads as the previous step having failed.
    ``git add --pathspec-from-file`` takes its list from a file the step before it writes,
    so a key renamed in the record stages nothing and the commit fails with a message about
    an empty index. And the two steps share state only through files under ``RUNNER_TEMP``,
    so the second one works or does not entirely on whether the first wrote what it reads.

    ``origin`` is a bare repository on disk. What is being checked is the shell, not GitHub.

    **And since the pull request is handed over rather than opened, what the second step
    produces is a link and a body rather than a call.** The only dispatch this workflow has
    ever had pushed its branch and then died on ``GitHub Actions is not permitted to create
    or approve pull requests``, which is an organization setting that is staying on. So the
    assertions below are that the branch reached the remote, that the compare URL names it,
    and that the body reached the job summary whole -- the last of those because it does not
    fit in the URL and the failure worth catching is a body quietly cut to make it fit.
    """
    # The three files are committed before the registration is written, so what the step
    # meets is the state a dispatch meets, a clean checkout of main with three modified
    # files and, deliberately, two untracked ones the pathspec has to decline.
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(tree)], check=True)
    for name, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(tree), "config", name, value], check=True)
    subprocess.run(["git", "-C", str(tree), "add", "--all"], check=True)
    subprocess.run(
        ["git", "-C", str(tree), "commit", "--quiet", "--message", "before"], check=True
    )

    record = run(tree, capsys).record
    (tree / "registration.json").write_text(json.dumps(record), encoding="utf-8")
    (tree / "stray.txt").write_text("not part of a registration\n", encoding="utf-8")

    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(remote)], check=True)
    subprocess.run(["git", "-C", str(tree), "remote", "add", "origin", str(remote)], check=True)

    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "python", f'exec {shlex.quote(sys.executable)} "$@"')

    printed, summary = _handover(tree, tmp_path, record, stub_bin)

    committed = subprocess.run(
        ["git", "-C", str(tree), "show", "--name-only", "--format=%s", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()

    assert committed[0] == "Register"
    assert set(committed[-len(TOUCHED) :]) == set(TOUCHED)
    assert "stray.txt" not in committed
    assert "registration.json" not in committed

    assert (
        subprocess.run(
            ["git", "-C", str(remote), "rev-parse", "--verify", record["branch"]],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    ), "the branch was never pushed, so the compare URL would open against nothing"

    compare = f"https://github.com/edu-llm/platform/compare/{record['branch']}?expand=1"
    assert compare in printed
    assert compare in summary
    assert quote(record["pull_request_title"], safe="") in printed

    # The body is around eleven thousand characters, so it cannot be in the link and the
    # whole of it has to be somewhere. Compared entire rather than by a first line, because
    # the mutation worth catching is a truncation that still looks like a body.
    assert record["pull_request_body"].rstrip("\n") in summary
    assert quote(record["pull_request_body"], safe="") not in printed


def test_the_url_the_cli_prints_names_the_branch_this_tool_pushes(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: rename the branch here and leave ``edullm add repository`` saying the old one.

    The two are a copy of one string with nothing connecting them, which is deliberate --
    ``tools/`` is not importable from an installed CLI, and the alternative is a second API
    call to read a branch name that is a pure function of the repository being registered.
    This is the test that makes the copy safe, the same way ``PLATFORM_REPOSITORY`` is.
    """
    record = run(tree, capsys).record

    url = registration_compare_url(record["repository"], platform_repository="edu-llm/platform")

    assert url == f"https://github.com/edu-llm/platform/compare/{record['branch']}?expand=1"


def test_a_body_too_long_for_a_url_is_neither_carried_nor_cut(
    tree: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The measurement, made rather than assumed, and the two directions around it.

    A compare URL can prefill the body, and for a registration it cannot: eleven thousand
    characters percent-encode into roughly fifteen kilobytes of URL against the eight
    thousand a server will take before answering ``414 URI Too Long``. The step therefore
    measures rather than guesses, and this pins both sides of that -- a short body rides in
    the link, and the real one does not and is not shortened to.
    """
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(tree)], check=True)
    for name, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(tree), "config", name, value], check=True)
    subprocess.run(["git", "-C", str(tree), "add", "--all"], check=True)
    subprocess.run(["git", "-C", str(tree), "commit", "--quiet", "--message", "before"], check=True)

    record = run(tree, capsys).record
    assert len(record["pull_request_body"]) > 8192, (
        "this test is only interesting while a registration body is too long for a URL"
    )
    (tree / "registration.json").write_text(json.dumps(record), encoding="utf-8")

    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(remote)], check=True)
    subprocess.run(["git", "-C", str(tree), "remote", "add", "origin", str(remote)], check=True)

    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "python", f'exec {shlex.quote(sys.executable)} "$@"')

    short = "Registers one repository. Nothing else."
    record_with_a_short_body = {**record, "pull_request_body": short}
    (tree / "registration.json").write_text(json.dumps(record_with_a_short_body), encoding="utf-8")
    printed, summary = _handover(tree, tmp_path, record, stub_bin)

    assert f"body={quote(short, safe='')}" in printed
    assert "Read them and press Create" in summary
    # Not repeated underneath the link it is already in. The long case is the one that has to
    # put it somewhere, and this is the assertion that says the two cases really do differ.
    assert "````markdown" not in summary


def test_the_commit_message_is_one_imperative_sentence_with_no_trailing_period(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record = run(tree, capsys).record

    subject, blank, *body = record["commit_message"].splitlines()

    assert subject == "Register olmo-mixer and give it somewhere to publish"
    assert not subject.endswith(".")
    assert blank == ""
    assert any("laptop" in line for line in body)


def test_the_pull_request_body_says_what_the_diff_cannot(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: drop the follow-up list, or the note about the checks.

    A reviewer who approves the diff and stops has left a registration that looks complete
    and cannot publish. The checks note matters for a smaller reason that wastes the same
    afternoon. A pull request opened with the workflow token starts its runs in an
    approval-required state, so an empty checks list is GitHub waiting rather than CI
    passing.
    """
    body = run(tree, capsys).record["pull_request_body"]

    assert REASON in " ".join(body.split())
    assert "sbsandbox-intern-edullm-olmo-mixer" in body
    assert "deploy-phase1-ecr.yml" in body
    for item in register_repository.FOLLOW_UPS:
        assert item.summary in body
    assert "Approve workflows to run" in body


# --- What the registration reads off the repository it names -------------------------------
#
# THE DEFECT THESE COVER IS THAT THERE WAS NOTHING HERE. Four of the eight fields describe a
# tree this repository does not own and every one of them went into a reviewed file
# unexamined, which is how `open-instruct-scored-rewards` was registered declaring a
# Dockerfile against a repository with no `.edullm` directory and reached the submission form
# with nothing anywhere going red.
#
# The reads themselves are tested in `tests/test_registered_dockerfiles.py`, which owns them.
# What is tested here is the wiring: that the tool asks, that it asks about the entry it is
# about to write rather than about anything else, that a false claim stops the write, and that
# a question nobody could put is not mistaken for a question that came back clean.


def refuse(reason: str, message: str = "not there") -> object:
    return register_repository.Finding(
        reason, message, register_repository.EXIT_MISSING
    )


def could_not_look(reason: str = "registered_dockerfile_not_read") -> object:
    return register_repository.Finding(
        reason, "gh auth login", register_repository.EXIT_UNUSABLE
    )


def test_the_claims_are_read_about_the_entry_the_tool_is_about_to_write(
    tree: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: check the registry as it already stands rather than the new entry.

    A check that read the committed entries would pass on every registration ever made and
    say nothing about the one being added, which is the only one nobody has looked at. So the
    subject of the read is asserted, field by field, against the arguments handed in.
    """
    seen: list[tuple[object, str]] = []

    def spy(entry: object, organization: str) -> tuple[object, ...]:
        seen.append((entry, organization))
        return ()

    monkeypatch.setattr(register_repository, "check_registration", spy)

    arguments = {
        "--repository": "olmo-mixer",
        "--github-repository-id": "1399999999",
        "--dockerfile-path": "images/Dockerfile",
        "--default-branch": "release",
        "--build-context": "src",
        "--reason": REASON,
        "--project-root": str(tree),
    }
    assert (
        register_repository.main(
            [part for pair in arguments.items() for part in pair if part]
        )
        == 0
    )
    capsys.readouterr()

    assert len(seen) == 1
    entry, organization = seen[0]
    assert entry.repository == "olmo-mixer"
    assert entry.github_repository_id == 1399999999
    assert entry.default_branch == "release"
    assert entry.dockerfile_path == "images/Dockerfile"
    assert entry.build_context == "src"
    assert organization == "edu-llm"


def test_a_claim_the_repository_falsifies_refuses_the_registration_and_writes_nothing(
    tree: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE WHOLE POINT, and the assertion that matters is that the tree is untouched.

    A tool that reported the finding and wrote the files anyway would leave the pull request
    that started this, with the refusal one scroll up a workflow log nobody reads twice.
    """
    before = {relative: (tree / relative).read_text(encoding="utf-8") for relative in TOUCHED}
    monkeypatch.setattr(
        register_repository,
        "check_registration",
        lambda *_: (
            refuse(
                "registered_dockerfile_is_absent",
                "images/Dockerfile is not on release.",
            ),
        ),
    )

    arguments = {
        "--repository": "olmo-mixer",
        "--github-repository-id": "1399999999",
        "--dockerfile-path": "images/Dockerfile",
        "--reason": REASON,
        "--project-root": str(tree),
    }
    code = register_repository.main(
        [part for pair in arguments.items() for part in pair if part]
    )
    captured = capsys.readouterr()

    assert code == register_repository.EXIT_REFUSED
    assert "registered_dockerfile_is_absent" in captured.err
    assert "images/Dockerfile is not on release." in captured.err
    for relative, text in before.items():
        assert (tree / relative).read_text(encoding="utf-8") == text, relative


def test_every_falsified_claim_is_reported_rather_than_the_first(
    tree: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One refusal per dispatch is three dispatches to learn three things.

    The caller is a person waiting on a workflow run, and the argument
    `skills/edullm-platform/SKILL.md` makes about `edullm check` -- list every refusal at once
    rather than one per attempt -- applies to the tool that opens the pull request too.
    """
    monkeypatch.setattr(
        register_repository,
        "check_registration",
        lambda *_: (
            refuse("registered_dockerfile_is_absent", "no Dockerfile"),
            refuse("no_workflow_calls_the_platform_build", "no caller"),
        ),
    )

    arguments = {
        "--repository": "olmo-mixer",
        "--github-repository-id": "1399999999",
        "--dockerfile-path": ".edullm/Dockerfile",
        "--reason": REASON,
        "--project-root": str(tree),
    }
    code = register_repository.main(
        [part for pair in arguments.items() for part in pair if part]
    )
    captured = capsys.readouterr()

    assert code == register_repository.EXIT_REFUSED
    assert "registered_dockerfile_is_absent" in captured.err
    assert "no_workflow_calls_the_platform_build" in captured.err


def test_a_question_nobody_could_put_is_exit_two_rather_than_a_refusal(
    tree: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: treat an unreadable answer as a falsified claim.

    Reporting an absent Dockerfile on the morning `gh` is logged out sends somebody to open a
    pull request against a repository whose file is sitting right there. Exit 2 is the
    tool-could-not-be-driven door, it names `--offline` as the way past, and it still writes
    nothing -- a claim nobody could check is not a claim that holds.
    """
    before = (tree / "config/repositories.yaml").read_text(encoding="utf-8")
    monkeypatch.setattr(
        register_repository,
        "check_registration",
        lambda *_: (could_not_look(),),
    )

    arguments = {
        "--repository": "olmo-mixer",
        "--github-repository-id": "1399999999",
        "--dockerfile-path": ".edullm/Dockerfile",
        "--reason": REASON,
        "--project-root": str(tree),
    }
    code = register_repository.main(
        [part for pair in arguments.items() for part in pair if part]
    )
    captured = capsys.readouterr()

    assert code == register_repository.EXIT_UNUSABLE
    assert code != register_repository.EXIT_REFUSED
    assert "registered_dockerfile_not_read" in captured.err
    assert "--offline" in captured.err
    assert (tree / "config/repositories.yaml").read_text(encoding="utf-8") == before


def test_the_local_checks_run_before_the_network_ones(
    tree: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A name already registered is a refusal that costs no round trip and no waiting.

    Everything the tool can decide from this tree is free, deterministic and offline, so it
    is decided first. It also makes a repository-side refusal mean what it says: the entry
    has already been shown to be writable, so the refusal is about the repository.
    """
    asked = False

    def spy(*_: object) -> tuple[object, ...]:
        nonlocal asked
        asked = True
        return ()

    monkeypatch.setattr(register_repository, "check_registration", spy)

    arguments = {
        "--repository": "OLMo-core",  # already in the committed registry
        "--github-repository-id": "1306868157",
        "--dockerfile-path": ".edullm/Dockerfile",
        "--reason": REASON,
        "--project-root": str(tree),
    }
    code = register_repository.main(
        [part for pair in arguments.items() for part in pair if part]
    )
    capsys.readouterr()

    assert code == register_repository.EXIT_REFUSED
    assert not asked, "a repeat registration must not cost a read"


def test_the_body_names_every_claim_that_was_checked_and_every_one_that_cannot_be(
    tree: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CHECK NOBODY IS TOLD ABOUT IS WORTH LESS THAN ONE THEY ARE, and a short list of
    checks reads as a complete one unless the gaps are printed beside it.

    So the body carries both, and it carries the sentence that stops the registration-time
    check being mistaken for the audit -- which is how the audit gets deleted as redundant.
    """
    monkeypatch.setattr(register_repository, "check_registration", lambda *_: ())

    arguments = {
        "--repository": "olmo-mixer",
        "--github-repository-id": "1399999999",
        "--dockerfile-path": ".edullm/Dockerfile",
        "--reason": REASON,
        "--project-root": str(tree),
    }
    assert (
        register_repository.main(
            [part for pair in arguments.items() for part in pair if part]
        )
        == 0
    )
    record = json.loads(capsys.readouterr().out)
    body = record["pull_request_body"]
    flat = " ".join(body.split())

    for claim, _ in register_repository.CLAIMS:
        assert claim in flat, claim
    for claim, _ in register_repository.UNCHECKABLE_CLAIMS:
        assert claim in flat, claim
    assert "None of that is a statement about tomorrow" in flat
    assert "audit.yml" in body
    assert record["claims_checked"] is True
    assert record["claims"] == [claim for claim, _ in register_repository.CLAIMS]


def test_offline_says_so_in_the_record_and_in_the_body_rather_than_reading_as_checked(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mutation: make `--offline` quiet.

    A flag that skips the checks and leaves the body reading exactly as a checked one turns
    every claim back into a string nobody put to GitHub while looking like the opposite. The
    record carries a boolean so a later tool can tell without parsing English.
    """
    record = run(tree, capsys).record
    flat = " ".join(record["pull_request_body"].split())

    assert record["claims_checked"] is False
    assert record["claims"] == []
    assert "**Nothing was.**" in flat
    assert "--offline" in flat
    assert "open-instruct-scored-rewards" in flat
    # Still lists what a reviewer now has to read by hand, and still says the audit will ask.
    for claim, _ in register_repository.CLAIMS:
        assert claim in flat, claim
    assert "audit.yml" in flat


def test_every_claim_nothing_here_can_check_says_why_and_names_no_credential_as_the_fix() -> None:
    """WHAT SEPARATES A CHECKABLE CLAIM FROM AN UNCHECKABLE ONE IS THE ENDPOINT, NOT EFFORT.

    `repositories/{id}`, `contents` and `branches` answer a token that is a collaborator on
    nothing, which is what makes the five checked claims free. `actions/variables`,
    `actions/secrets`, `hooks`, `keys` and `branches/{branch}/protection` answer 401, and the
    credential that would open them is a stored collaborator token --
    `test_the_repository_holds_no_secret_a_branch_could_read` forbids one by name. The rule
    that would have to be broken to build the live check is the rule the check would be
    watching.

    So every entry has to say why it cannot be checked rather than merely that it is not, and
    none of them may propose a stored credential as the answer.
    """
    assert register_repository.UNCHECKABLE_CLAIMS

    for claim, detail in register_repository.UNCHECKABLE_CLAIMS:
        assert claim.strip()
        assert len(detail.split()) > 20, claim
        assert detail.rstrip().endswith("."), claim

    joined = " ".join(detail for _, detail in register_repository.UNCHECKABLE_CLAIMS)
    assert "401" in joined
    assert "actions/variables" in joined
    # The two doors an unreadable fact may leave by: a check somewhere it can be made, or an
    # honest silence. Never a token in this repository.
    assert "build-research-image.yml" in joined
    assert "audit" in joined


def test_no_claim_is_both_checked_and_declared_uncheckable() -> None:
    """The two lists go into the same pull request body, so an overlap is a contradiction."""
    checked = {claim for claim, _ in register_repository.CLAIMS}
    uncheckable = {claim for claim, _ in register_repository.UNCHECKABLE_CLAIMS}

    assert checked
    assert uncheckable
    assert checked & uncheckable == set()


def test_the_runbook_and_the_skill_both_name_the_variable_nothing_can_read() -> None:
    """THE PUREST CASE OF THE CLASS, AND IT WAS DOCUMENTED NOWHERE.

    All six research repositories carry `AWS_ECR_PUBLISHER_ROLE_ARN` as a repository
    variable, set by hand, with no organization variable behind it -- and before 2026-08-06
    its only mention in this repository was one example comment in the reusable build. Not the
    runbook this tool prints, not the skill that writes the caller workflow. A person could
    follow either to the letter and produce a repository that reads as fully registered and
    publishes nothing, which is what `edullm-p1` did for days.

    Read from both documents in both directions: the step exists, and it says the thing that
    makes it a step rather than a default, which is that nothing inherits it.

    The skill read here is the shipped one. There was a second, local copy under `.cursor/`
    saying the same thing to an agent working in this checkout, who is a maintainer and never
    registers anybody else's repository from here; it was deleted, and this now reads the file
    that actually reaches the person doing the registering.
    """
    runbook = " ".join(
        f"{item.summary} {item.detail}" for item in register_repository.FOLLOW_UPS
    )
    skill = " ".join(
        (
            PROJECT_ROOT / "skills/edullm-platform/SKILL.md"
        ).read_text(encoding="utf-8").split()
    )

    for text, where in ((runbook, "runbook"), (skill, "skill")):
        assert "AWS_ECR_PUBLISHER_ROLE_ARN" in text, where
        assert "no organization variable" in text, where
        assert "per repository" in text, where
        assert "publisher_role_arn_is_empty" in text, where


def test_the_workflow_gives_the_tool_a_token_and_never_asks_it_to_skip_the_reads() -> None:
    """Mutation: dispatch with `--offline`, or forget the token and let the reads fail.

    The reads are the point of the tool now, and there are two ways to have written them and
    still ship a registration nobody checked. `--offline` is the loud one; an absent token is
    the quiet one, because `gh` needs something to present even for a read an anonymous caller
    could make, and without it every claim leaves by the exit-2 door -- which somebody in a
    hurry then passes `--offline` to get around.

    The token is on the job rather than on the step, because
    `test_the_workflow_hands_the_tool_every_input_it_offers` holds that step's `env` equal to
    the dispatch inputs in both directions and a credential is not an input.
    """
    workflow = load_workflow(WORKFLOW_PATH)
    job = workflow["jobs"]["register"]
    registration = step(job, "Write the registration")

    assert job["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "--offline" not in registration["run"]
    assert "--offline" not in str(workflow)
    # No stored credential appears anywhere: the reads are ones a token that is a collaborator
    # on nothing can make, which is what keeps the whole check free.
    assert "secrets." not in str(registration)
