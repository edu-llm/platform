"""Registering a repository, held to the shape the four files it touches already have.

REGISTRATION WAS NEVER ONE FILE, AND THAT IS WHAT THE TOOL UNDER TEST IS FOR. An entry in
``config/repositories.yaml`` on its own lands a red pull request. The ECR template must
declare a repository for every registration, and the publisher role must carry the GitHub
repository id, the subject pattern and the destination ARN. Six tests across two modules
read those pairings in both directions, and each of them fails on a half-registration --
which is the failure ``edullm-data`` actually shipped, inert for a day behind an AssumeRole
denial that reads like a broken role ARN.

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
    "infra/ecr-repositories.yaml",
    "infra/iam/ecr-publisher-role.yaml",
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
    arguments = {
        "--repository": "olmo-mixer",
        "--github-repository-id": "1399999999",
        "--dockerfile-path": ".edullm/Dockerfile",
        "--reason": REASON,
        "--project-root": str(tree),
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


def test_the_runbook_names_the_laptop_deploy_and_says_no_workflow_can_do_it() -> None:
    """The step that cannot be automated, and the one most likely to be assumed away.

    ``InternSandboxBoundary`` withholds ``iam:CreateRole`` and the rest of the role
    lifecycle from every CI role in this account, deliberately, so the publisher widening
    is a laptop operation and no amount of workflow design changes that. A runbook that
    listed it beside four things a tool can do would read as one more command to run.
    """
    laptop = register_repository.FOLLOW_UPS[0]

    assert "laptop" in laptop.summary.lower()
    assert "sbsandbox-intern-edullm-ecr-publisher-iam" in laptop.detail
    assert "no workflow can do this" in laptop.detail


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
    assert job["permissions"] == {"contents": "write", "pull-requests": "write"}
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


def test_the_two_shell_steps_run_end_to_end_and_open_the_pull_request_they_describe(
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

    ``gh`` is stubbed and ``origin`` is a bare repository on disk. What is being checked is
    the shell, not GitHub.
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
    write_stub(stub_bin, "gh", f'printf "%s\\n" "$@" > {shlex.quote(str(tmp_path))}/gh.txt')

    environment = {
        "HOME": str(tmp_path),
        "RUNNER_TEMP": str(tmp_path),
        "RECORD": str(tree / "registration.json"),
        "GH_TOKEN": "stub",
    }
    job = next(iter(load_workflow(WORKFLOW_PATH)["jobs"].values()))
    for name in ("Commit the registration to a branch of its own", "Open the pull request"):
        outcome = run_step_script(
            step(job, name)["run"],
            cwd=tree,
            env=environment,
            stub_bin=stub_bin,
        )
        assert outcome.returncode == 0, f"{name}: {outcome.stdout}{outcome.stderr}"

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

    arguments = (tmp_path / "gh.txt").read_text(encoding="utf-8").splitlines()
    assert arguments[:2] == ["pr", "create"]
    assert arguments[arguments.index("--base") + 1] == "main"
    assert arguments[arguments.index("--head") + 1] == record["branch"]
    assert arguments[arguments.index("--title") + 1] == record["pull_request_title"]
    body = Path(arguments[arguments.index("--body-file") + 1])
    assert body.read_text(encoding="utf-8") == record["pull_request_body"]
    assert (
        subprocess.run(
            ["git", "-C", str(remote), "rev-parse", "--verify", record["branch"]],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    ), "the branch was never pushed, so gh pr create would have had no head to open against"


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
