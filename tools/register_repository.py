"""Write every file a repository registration has to touch, as one reviewable change.

REGISTRATION IS NOT ONE FILE AND NEVER WAS, WHICH IS MOST OF WHY IT FELT HEAVY. Adding an
entry to ``config/repositories.yaml`` on its own lands a red pull request, because three
tests read the registry and hold something else against it. ``infra/ecr-repositories.yaml``
must declare a repository for every registration, or
``test_the_template_creates_one_repository_for_every_registered_research_repository``
fails. ``infra/iam/ecr-publisher-role.yaml`` must carry the GitHub repository id, the
subject pattern and the destination ARN, or three tests in
``tests/test_repository_registry.py`` fail. So the work was never "add four lines", it was
"find the four places", and the finding is what took the afternoon. This writes all of them.

**THE FOURTH AND FIFTH FILES ARE THE ONES A REGISTRATION USED TO SILENTLY OMIT.** A
repository with no entry in ``config/workload-catalog.yaml`` never reaches the submission
form's ``repository`` dropdown, so nothing can be submitted for it -- and unlike the three
files above, nothing went red about it. The test that should have caught it compared the
dropdown against *the registered repositories that have a workload profile*, which is a
filter both sides went through, so a registration with no workload was absent from each and
the comparison agreed. ``edullm-data`` sat in that state from the day it was registered:
built, scanned, publishable, and impossible to name in a run. The catalog entry and the two
dropdown options are written here for the same reason the ECR template is -- because
leaving them to a follow-up is what produced the state this paragraph is about.

The bounds on the entry it writes are defaults rather than measurements, and it says so in
the comment it leaves above them. One hour, one attempt and no checkpoint contract is the
shape every repository's first workload has; anything that trains needs a person to pick
real numbers, which is the last of the follow-ups below.

**IT CREATES NO ECR REPOSITORY, AND THAT IS THE WHOLE DESIGN RATHER THAN A LIMITATION.**
Every ECR repository in this account is a CloudFormation resource in
``sbsandbox-intern-edullm-phase1-ecr``, applied by ``.github/workflows/deploy-phase1-ecr.yml``
when a change to ``infra/ecr-repositories.yaml`` reaches ``main``. A tool that called
``ecr:CreateRepository`` would put the repository in the account and leave the stack still
intending to create it, so the next Phase 1 deploy would fail on a name that already exists
and take the other registered repositories down with it, since one stack holds them all.
The grant to do it exists and is real, measured on 2026-08-02 by simulating
``ecr:CreateRepository`` for ``sbsandbox-intern-edullm-infra-deployer`` against a repository
ARN that does not exist, which answered ``allowed``. Holding a permission is not a reason to
use it. Adding the resource to the template reaches the same account through the path that
already owns it.

**WHAT STILL NEEDS A PERSON, STATED HERE BECAUSE THE POINT OF THIS TOOL IS THAT NOTHING
ELSE DOES.** The publisher role edit is an IAM change, and every IAM stack in this
repository is applied from a laptop by somebody holding an SSO session, for the reason
``infra/README.md`` opens with. So the pull request this produces lands in two moves rather
than one, and until the second move the registration is inert in the specific way
``edullm-data`` was inert for a day. The build assumes the publisher role, the token
presents a repository id the trust policy does not list, and the run dies at AssumeRole
reading like a broken role ARN. ``--dry-run`` prints the same record and writes nothing.

**THE REASON IS A REQUIRED ARGUMENT.** Registration is meant to be cheap, and cheap is not
the same as unconsidered. The one question a reviewer cannot answer from the diff is why
this needs a home of its own rather than a workload in an existing one, and the person
asking is the only person who knows. It is wrapped into a comment above the entry, so the
answer arrives in the file it is about rather than in a pull request description that stops
being readable once the branch is deleted.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import textwrap
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from edullm_platform.config import load_yaml
from edullm_platform.contracts.repository_registry import (
    RegisteredRepository,
    RepositoryRegistry,
)
from edullm_platform.contracts.workload import WorkloadCatalog, WorkloadProfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]

EXIT_REFUSED = 1
EXIT_UNUSABLE = 2

REGISTRY_PATH = Path("config/repositories.yaml")
WORKLOAD_CATALOG_PATH = Path("config/workload-catalog.yaml")
SUBMISSION_FORM_PATH = Path(".github/workflows/submit-run.yml")
ECR_TEMPLATE_PATH = Path("infra/ecr-repositories.yaml")
PUBLISHER_TEMPLATE_PATH = Path("infra/iam/ecr-publisher-role.yaml")

#: The deployer's ECR grant is scoped to `repository/sbsandbox-intern-edullm-*`, narrower
#: than the contract's own pattern, which accepts any `sbsandbox-intern-` name. A
#: registration naming `sbsandbox-intern-something` therefore validates, merges, and is
#: refused by IAM when CloudFormation tries to create it. Checked here so the refusal
#: arrives while somebody is still holding the decision rather than during a deploy.
DEPLOYABLE_ECR_PREFIX = "sbsandbox-intern-edullm-"

#: A repository name reaches three places that are not YAML strings in a vacuum. It is
#: written unquoted into the registry, interpolated into an OIDC subject pattern in a trust
#: policy, and used to derive a CloudFormation logical id. Anything outside this set is
#: refused rather than escaped, because escaping it correctly in all three is a harder
#: promise to keep than declining the name.
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: What a value may look like to be emitted as an unquoted YAML scalar. A colon or a hash
#: in one of these would silently re-parse as a mapping or a comment, and the verification
#: pass below would then compare a document nobody meant to write.
PLAIN_SCALAR = re.compile(r"^[^\s#][^:#]*$")

#: Where the comment above a registry entry wraps. Matched to the neighbouring entries
#: rather than to the linter, which does not read YAML comments.
COMMENT_WIDTH = 88

DEFAULT_BASE_IMAGE_REPOSITORY = "docker.io/library/python"

#: What a first workload profile looks like, taken from the four that exist rather than
#: chosen. ``olmo-core-check``, ``edullm-data-validate``, ``olmo-eval-check`` and
#: ``edullm-alt-cl-check`` all declare one hour, one attempt and no checkpoint contract,
#: because a repository's first entry is the run somebody makes to prove the path works. It
#: is the wrong shape for anything that trains, and ``--maximum-runtime-hours`` and
#: ``--maximum-attempts`` are how that is said. A checkpoint contract is deliberately not
#: derivable here at all: whether a workload resumes from ``$EDULLM_CHECKPOINT_DIR`` is a
#: fact about the program, and declaring one for a codebase that ignores it would buy a
#: second attempt that silently repeats the first at full price.
DEFAULT_MAXIMUM_RUNTIME_HOURS = "1"
DEFAULT_MAXIMUM_ATTEMPTS = 1


@dataclass(frozen=True)
class FollowUp:
    """One thing this tool cannot do, named with what does it and what stays broken until."""

    summary: str
    detail: str
    #: A file or tool the step acts on, asserted to exist by
    #: ``tests/test_register_repository.py`` so a rename cannot leave a runbook pointing at
    #: something that is gone. A step whose subject is the account rather than the tree
    #: carries none.
    paths: tuple[str, ...] = ()


#: EVERYTHING A REGISTRATION NEEDS THAT IS NOT A FILE EDIT, IN THE ORDER IT HAS TO HAPPEN.
#
# Measured rather than reasoned about. A fourth registration was applied to a scratch tree
# on 2026-08-02 and the full suite run against it; eleven tests in seven modules failed, and
# every entry below is one of the families they fell into. Three of the five were not
# predictable from reading the registry, which is the whole argument for writing them down
# here instead of leaving each one to be rediscovered as a red test.
#
# This list is the honest answer to "registration should be a ten minute action". The three
# file edits above are ten minutes. The tail is not, and none of it is removable from inside
# this tool. Two steps need an AWS session, one needs an S3 write, and one is a literal in
# a test module this tool has no business editing.
FOLLOW_UPS: tuple[FollowUp, ...] = (
    FollowUp(
        summary="Deploy the publisher role from a laptop",
        detail=(
            "`aws cloudformation deploy --stack-name "
            "sbsandbox-intern-edullm-ecr-publisher-iam --template-file "
            "infra/iam/ecr-publisher-role.yaml --capabilities CAPABILITY_NAMED_IAM "
            "--no-fail-on-empty-changeset --profile sbsandbox --region us-east-1`. "
            "InternSandboxBoundary withholds the role lifecycle from CI, so no workflow "
            "can do this and none ever will. Until it runs, the build assumes the "
            "publisher role, presents a repository id the trust policy does not list, and "
            "dies at AssumeRole reading like a broken role ARN. That is the shape "
            "edullm-data shipped in for a day."
        ),
        paths=("infra/iam/ecr-publisher-role.yaml", "infra/README.md"),
    ),
    FollowUp(
        summary="Re-capture the publisher role, or record the gap while it is open",
        detail=(
            "The committed capture is compared against the template that declares it, so "
            "amending the template makes it report three narrower findings. "
            "`tools/capture_phase1_evidence.py --target roles` refreshes it once the "
            "deploy above has run. To land before that deploy instead, add a "
            "`PendingAmendment` to `src/edullm_platform/pending_amendments.py`, which is "
            "where a template committed ahead of the account is declared, and delete it "
            "when the capture is refreshed."
        ),
        paths=(
            "tools/capture_phase1_evidence.py",
            "src/edullm_platform/pending_amendments.py",
            "fixtures/evidence/phase-1/roles",
        ),
    ),
    FollowUp(
        summary="Re-record the publisher role golden digest",
        detail=(
            "`uv run python tools/record_goldens.py --force`. The golden is a canonical "
            "serialization of the template, so it moves whenever the template does. "
            "Offline and needs no credential, and the re-recording is deliberately behind "
            "a flag so a change to what a role may do cannot be absorbed by re-running the "
            "tool."
        ),
        paths=("tools/record_goldens.py",),
    ),
    FollowUp(
        summary="Release both Lambdas",
        detail=(
            "`uv run python tools/release_lambda.py`. `build_package` copies "
            "`config/*.yaml` into both zips, so editing the registry moves the admission "
            "validator's digest and the lifecycle recorder's, and both release tripwires "
            "are red until the recorded zips are the ones this tree builds. Nothing the "
            "recorder does reads the registry; it is released anyway because the bytes "
            "changed. Without a laptop session, dispatch "
            "`deploy-phase2-admission.yml` with `release_lambdas=true` for the upload half."
        ),
        paths=("tools/release_lambda.py",),
    ),
    FollowUp(
        summary="Widen the pilot-list snapshot in the registry tests",
        detail=(
            "`test_the_registry_and_the_pilot_list_are_asked_different_questions` pins "
            "`registered - pilots` to a literal set of the repositories registered on the "
            "day it was written. Its own docstring says the set grows on every "
            "registration, and the assertion does not, so every registration falsifies it "
            "however it is made. This is a pre-existing blocker on the fourth repository "
            "rather than anything this tool introduces."
        ),
        paths=("tests/test_repository_registry.py",),
    ),
    FollowUp(
        summary="Decide the workload profile's real bounds, and add the rest of them",
        detail=(
            "This change writes one entry in `config/workload-catalog.yaml` and offers it "
            "on the submission form, so the registration is submittable rather than "
            "decorative -- which is the part `edullm-data` went without. What it cannot "
            "decide is the policy: the entry carries one hour, one attempt and no "
            "checkpoint contract, which is the shape of every repository's first entry and "
            "is right only for a check. A workload that trains needs a runtime bound under "
            "the 24-hour ceiling in `config/policy.yaml`, and a second attempt is safe only "
            "if the program resumes from `$EDULLM_CHECKPOINT_DIR` on its own. Add further "
            "entries the same way, each with a matching option on the `workload_profile` "
            "dropdown in `.github/workflows/submit-run.yml`."
        ),
        paths=(
            "config/workload-catalog.yaml",
            "config/policy.yaml",
            ".github/workflows/submit-run.yml",
        ),
    ),
)


class RegistrationRefused(Exception):
    """The registration cannot be written as asked, and the reason is the message."""


class SourceUnusable(Exception):
    """A file this tool has to read or edit is missing, unparseable, or an unknown shape."""


@dataclass(frozen=True)
class Edit:
    """One file, before and after, so the diff can be shown before anything is written."""

    path: Path
    before: str
    after: str

    def unified(self) -> str:
        return "".join(
            difflib.unified_diff(
                self.before.splitlines(keepends=True),
                self.after.splitlines(keepends=True),
                fromfile=f"a/{self.path.as_posix()}",
                tofile=f"b/{self.path.as_posix()}",
            )
        )


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise SourceUnusable(f"unreadable:{path.as_posix()}") from error


def parse(path: Path, text: str) -> dict[str, Any]:
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise SourceUnusable(f"unparseable:{path.as_posix()}") from error
    if not isinstance(document, dict):
        raise SourceUnusable(f"not_a_mapping:{path.as_posix()}")
    return document


def require_plain_scalar(field: str, value: str) -> str:
    if PLAIN_SCALAR.fullmatch(value) is None:
        raise RegistrationRefused(
            f"{field} cannot be written as an unquoted YAML scalar: {value!r}"
        )
    return value


def ecr_repository_name_for(repository: str) -> str:
    """The destination name this tool would choose, which is not a rule anybody wrote down.

    Two of the three registered repositories follow ``sbsandbox-intern-edullm-`` plus the
    lowercased name. The third does not: ``edullm-data`` publishes to
    ``sbsandbox-intern-edullm-data`` rather than to ``sbsandbox-intern-edullm-edullm-data``,
    because the prefix already ends in the word the name starts with. That collapse is
    reproduced here so the derivation agrees with the account as it stands, and it is a
    guess about intent rather than a documented convention, which is why
    ``--ecr-repository`` exists and overrides it.
    """
    slug = repository.lower().replace("_", "-").removeprefix("edullm-")
    return f"{DEPLOYABLE_ECR_PREFIX}{slug}"


def workload_profile_name_for(repository: str) -> str:
    """The name a first workload profile gets when nobody supplies one.

    A default rather than a derivation, which is the difference between this and
    ``ecr_repository_name_for`` above. Two of the four catalogued repositories are spelled
    this way -- ``olmo-core-check`` and ``edullm-alt-cl-check`` -- and the other two are
    not: ``edullm-data-validate`` names what the entry runs, and ``olmo-eval-check`` drops
    a segment of a long repository name. Both are better names than this would have
    produced, which is what ``--workload-profile`` is for.
    """
    return f"{repository.lower()}-check"


def logical_id_for(repository: str) -> str:
    """The CloudFormation logical id, derived the way the three existing ones were.

    ``OLMo-core`` is ``OlmoCoreRepository``, ``edullm-data`` is ``EdullmDataRepository`` and
    ``olmo-eval-full`` is ``OlmoEvalFullRepository``. Splitting on non-alphanumerics and
    capitalising each part reproduces all three, including the lowercasing inside ``OLMo``.
    """
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", repository) if part]
    return "".join(part.capitalize() for part in parts) + "Repository"


def registry_entry_text(entry: RegisteredRepository, reason: str) -> str:
    """The eight fields in the order the file already writes them, under a wrapped comment.

    Field order is not enforced by anything, and matching it anyway is what keeps the diff
    of a fourth registration readable beside the third.
    """
    comment = textwrap.fill(
        " ".join(reason.split()),
        width=COMMENT_WIDTH,
        initial_indent=f"  # {entry.repository}: ",
        subsequent_indent="  # ",
        # A repository name is full of hyphens and every one of them is a wrap opportunity
        # by default, so `OLMo-core` arrives split across two comment lines and stops being
        # greppable in the file it identifies.
        break_on_hyphens=False,
        break_long_words=False,
    )
    return (
        f"{comment}\n"
        f"  - repository: {entry.repository}\n"
        f"    github_repository_id: {entry.github_repository_id}\n"
        f"    default_branch: {entry.default_branch}\n"
        f"    ecr_repository: {entry.ecr_repository}\n"
        f"    base_image_repository: {entry.base_image_repository}\n"
        f"    base_image_digest: {entry.base_image_digest}\n"
        f"    dockerfile_path: {entry.dockerfile_path}\n"
        f"    build_context: {entry.build_context}\n"
    )


def workload_entry_text(workload: WorkloadProfile, reason: str) -> str:
    """The catalog entry, under a comment saying which of its numbers nobody measured.

    The bounds are this tool's defaults unless they were passed, and an entry that did not
    say so would read as a policy somebody set for this repository. The comment also states
    what the entry deliberately does not declare -- a machine -- because the field was
    removed from ``WorkloadProfile`` after a submission form override made every name that
    promised one a fiction.
    """
    # The lead sentence and the reason are wrapped as one paragraph rather than the reason
    # being wrapped under a long ``initial_indent``. textwrap counts the indent against the
    # width, so a prefix longer than the width leaves the first line unwrapped and past the
    # margin every other comment in the file keeps to.
    comment = textwrap.fill(
        " ".join(
            (
                f"{workload.name}: the first workload profile for {workload.repository}, "
                "written with the registration because a repository with no entry here "
                f"reaches no dropdown and no run can name it. {reason}"
            ).split()
        ),
        width=COMMENT_WIDTH,
        initial_indent="  # ",
        subsequent_indent="  # ",
        break_on_hyphens=False,
        break_long_words=False,
    )
    caveat = textwrap.fill(
        "The bounds are this tool's defaults and not a measurement: one hour, one attempt "
        "and no checkpoint contract is the shape of every repository's first entry, and it "
        "is right for a check and wrong for anything that trains. The machine is the "
        "submission form's compute_profile field and is deliberately not named here.",
        width=COMMENT_WIDTH,
        initial_indent="  # ",
        subsequent_indent="  # ",
        break_on_hyphens=False,
        break_long_words=False,
    )
    return (
        f"{comment}\n"
        "  #\n"
        f"{caveat}\n"
        f"  - name: {workload.name}\n"
        f"    repository: {workload.repository}\n"
        f'    maximum_runtime_hours: "{workload.maximum_runtime_hours}"\n'
        f"    maximum_attempts: {workload.maximum_attempts}\n"
        "    checkpoint: null\n"
    )


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def insert_form_option(
    text: str, field: str, option: str, *, key: Callable[[str], str]
) -> str:
    """Add one option to one dropdown on the submission form, in the order it is sorted in.

    Anchored on the input's name and then on the ``options:`` key beneath it, because the
    form has five dropdowns and every one of them spells that key the same way. Two keys
    with the same name, or an input with no options list, is a refusal rather than a guess.

    THE POSITION IS COMPUTED RATHER THAN APPENDED, and that is the whole reason this is not
    ``insert_after_last_list_item``. ``tests/test_submission_form_options.py`` holds the
    ``repository`` dropdown equal to the registry sorted case-insensitively and the
    ``workload_profile`` dropdown equal to the offerable workloads sorted, so an option
    added at the end of either list is a red test rather than a working form. The order the
    lists are already in is checked first, so this refuses to guess a position in a list
    that was not sorted to begin with.

    A comment block immediately above the item being displaced moves with it, since in this
    file those comments say what their entry runs and separating the two would attach each
    to the wrong option.
    """
    lines = text.splitlines(keepends=True)
    field_positions = [
        index for index, line in enumerate(lines) if line.strip() == f"{field}:"
    ]
    if len(field_positions) != 1:
        raise SourceUnusable(f"form_input_occurrences:{field}:{len(field_positions)}")
    start = field_positions[0]
    field_indent = indent_of(lines[start])

    options_at: int | None = None
    for index in range(start + 1, len(lines)):
        if not lines[index].strip():
            continue
        if indent_of(lines[index]) <= field_indent:
            break
        if lines[index].strip() == "options:":
            options_at = index
            break
    if options_at is None:
        raise SourceUnusable(f"form_input_has_no_options:{field}")
    options_indent = indent_of(lines[options_at])

    items: list[tuple[int, str]] = []
    end = len(lines)
    for index in range(options_at + 1, len(lines)):
        stripped = lines[index].strip()
        if not stripped or indent_of(lines[index]) <= options_indent:
            end = index
            break
        if stripped.startswith("- "):
            items.append((index, stripped.removeprefix("- ").strip()))
        elif not stripped.startswith("#"):
            raise SourceUnusable(f"form_options_are_not_a_flat_list:{field}")
    if not items:
        raise SourceUnusable(f"form_input_has_no_options:{field}")

    offered = [value for _, value in items]
    if option in offered:
        raise RegistrationRefused(f"the {field} dropdown already offers {option!r}")
    if offered != sorted(offered, key=key):
        raise SourceUnusable(f"form_options_are_not_sorted:{field}")

    displaced = next(
        (index for index, value in items if key(value) > key(option)), None
    )
    if displaced is None:
        at = end
    else:
        at = displaced
        first_item = items[0][0]
        while at - 1 >= first_item and lines[at - 1].strip().startswith("#"):
            at -= 1
    margin = " " * indent_of(lines[items[0][0]])
    lines.insert(at, f"{margin}- {option}\n")
    return "".join(lines)


def lifecycle_policy_text(template: dict[str, Any]) -> str:
    """The lifecycle policy an existing repository carries, copied rather than restated.

    ``test_ecr_lifecycle_expires_old_untagged_images_and_nothing_else`` requires every
    repository in the template to carry the same policy, so a literal here would be a second
    copy able to go stale against the first. Reading it off a repository that is already
    there means a change to the policy reaches the next registration for free.
    """
    for resource in existing_repositories(template).values():
        policy = resource.get("Properties", {}).get("LifecyclePolicy", {})
        text = policy.get("LifecyclePolicyText")
        if isinstance(text, str) and text.strip():
            return text
    raise SourceUnusable("no_lifecycle_policy_to_copy")


def existing_repositories(template: dict[str, Any]) -> dict[str, dict[str, Any]]:
    resources = template.get("Resources")
    if not isinstance(resources, dict):
        raise SourceUnusable("ecr_template_has_no_resources")
    return {
        str(logical_id): resource
        for logical_id, resource in resources.items()
        if isinstance(resource, dict) and resource.get("Type") == "AWS::ECR::Repository"
    }


def ecr_resource_text(logical_id: str, ecr_repository: str, policy: str) -> str:
    body = "\n".join(
        f"          {line}" if line.strip() else ""
        for line in policy.rstrip("\n").splitlines()
    )
    return (
        f"  {logical_id}:\n"
        "    Type: AWS::ECR::Repository\n"
        "    DeletionPolicy: Retain\n"
        "    UpdateReplacePolicy: Retain\n"
        "    Properties:\n"
        f"      RepositoryName: {ecr_repository}\n"
        "      EncryptionConfiguration:\n"
        "        EncryptionType: AES256\n"
        "      ImageScanningConfiguration:\n"
        "        ScanOnPush: true\n"
        "      ImageTagMutability: IMMUTABLE\n"
        "      LifecyclePolicy:\n"
        "        LifecyclePolicyText: |\n"
        f"{body}\n"
        "\n"
    )


def insert_before_outputs(text: str, block: str) -> str:
    lines = text.splitlines(keepends=True)
    positions = [index for index, line in enumerate(lines) if line.rstrip("\n") == "Outputs:"]
    if len(positions) != 1:
        raise SourceUnusable(f"outputs_sections:{len(positions)}")
    lines.insert(positions[0], block)
    return "".join(lines)


def insert_after_last_list_item(text: str, key: str, item: str) -> str:
    """Extend the YAML list under ``key``, refusing when it cannot say which list that is.

    Anchored on the key and on the indentation of the items below it rather than on the
    text of the last one, so this keeps working when somebody else adds an entry or a
    comment between them. Two keys with the same name, or a key with no list under it, is
    a refusal rather than a guess, for the reason ``tools/release_lambda.py`` gives about
    editing exactly one line.
    """
    lines = text.splitlines(keepends=True)
    positions = [index for index, line in enumerate(lines) if line.strip() == f"{key}:"]
    if len(positions) != 1:
        raise SourceUnusable(f"list_key_occurrences:{key}:{len(positions)}")
    start = positions[0]
    key_indent = len(lines[start]) - len(lines[start].lstrip(" "))

    last: int | None = None
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        indent = len(lines[index]) - len(lines[index].lstrip(" "))
        if not stripped or indent <= key_indent:
            break
        if stripped.startswith("- "):
            last = index
        elif not stripped.startswith("#"):
            break
    if last is None:
        raise SourceUnusable(f"list_has_no_items:{key}")

    margin = " " * (len(lines[last]) - len(lines[last].lstrip(" ")))
    lines.insert(last + 1, f"{margin}{item}\n")
    return "".join(lines)


def owner_id_of(publisher: dict[str, Any]) -> str:
    """The organisation id the publisher role already trusts, read rather than hardcoded.

    It is one of the three conditions that must never become a list, so there is exactly
    one to find, and the subject pattern this tool writes has to carry the same value or
    the new entry fails ``StringLike`` while satisfying ``StringEquals``.
    """
    for resource in publisher.get("Resources", {}).values():
        if not isinstance(resource, dict) or resource.get("Type") != "AWS::IAM::Role":
            continue
        statement = resource["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
        owner = statement["Condition"]["StringEquals"][
            "token.actions.githubusercontent.com:repository_owner_id"
        ]
        if isinstance(owner, str) and owner:
            return owner
    raise SourceUnusable("publisher_has_no_repository_owner_id")


def registered_digest_for(registry: RepositoryRegistry, base_repository: str) -> str | None:
    for entry in registry.repositories:
        if entry.base_image_repository == base_repository:
            return entry.base_image_digest
    return None


def build_entry(options: argparse.Namespace, registry: RepositoryRegistry) -> RegisteredRepository:
    repository = str(options.repository)
    if SAFE_NAME.fullmatch(repository) is None:
        raise RegistrationRefused(
            f"repository name reaches a trust policy and a logical id, so it is limited to "
            f"letters, digits, dot, dash and underscore: {repository!r}"
        )
    ecr_repository = str(options.ecr_repository or ecr_repository_name_for(repository))
    if not ecr_repository.startswith(DEPLOYABLE_ECR_PREFIX):
        raise RegistrationRefused(
            f"the deployer role may only create repositories under "
            f"{DEPLOYABLE_ECR_PREFIX!r}, so CloudFormation would be denied on "
            f"{ecr_repository!r}"
        )

    base_repository = str(options.base_image_repository)
    digest = options.base_image_digest or registered_digest_for(registry, base_repository)
    if digest is None:
        raise RegistrationRefused(
            f"no registration pins {base_repository!r}, so there is no reviewed digest to "
            "inherit. Pass --base-image-digest with the digest a person read, and expect "
            "config/image-exceptions.yaml to need that base's findings reviewed too."
        )

    try:
        entry = RegisteredRepository(
            repository=repository,
            github_repository_id=int(options.github_repository_id),
            default_branch=str(options.default_branch),
            ecr_repository=ecr_repository,
            base_image_repository=base_repository,
            base_image_digest=str(digest),
            dockerfile_path=str(options.dockerfile_path),
            build_context=str(options.build_context),
        )
    except ValidationError as error:
        raise RegistrationRefused(f"the registration is not a valid entry: {error}") from error

    for field, value in (
        ("repository", entry.repository),
        ("default_branch", entry.default_branch),
        ("ecr_repository", entry.ecr_repository),
        ("base_image_repository", entry.base_image_repository),
        ("dockerfile_path", entry.dockerfile_path),
        ("build_context", entry.build_context),
    ):
        require_plain_scalar(field, value)
    return entry


def build_workload(
    options: argparse.Namespace, entry: RegisteredRepository
) -> WorkloadProfile:
    """The catalog entry that makes the registration submittable, validated before it is
    written.

    ``WorkloadProfile`` refuses a second attempt with no checkpoint contract, which is the
    one combination this tool could produce from its arguments and must not: a retry with
    nowhere to resume from repeats the whole of the first attempt at full price. The
    refusal is raised here so the message names the two flags rather than arriving as a
    pydantic error over a document nobody has seen yet.
    """
    name = str(options.workload_profile or workload_profile_name_for(entry.repository))
    if SAFE_NAME.fullmatch(name) is None:
        raise RegistrationRefused(
            f"a workload profile name is written unquoted into the catalog and onto a "
            f"dropdown, so it is limited to letters, digits, dot, dash and underscore: "
            f"{name!r}"
        )
    require_plain_scalar("workload_profile", name)
    # Validated from a mapping rather than from keyword arguments, because
    # ``maximum_runtime_hours`` is a ``Decimal`` that only ever arrives as base-ten text --
    # a bound that went through binary floating point is not the number the approver reads.
    # The contract's own validator is what turns the string into one, and going through
    # ``model_validate`` is what lets it.
    try:
        workload = WorkloadProfile.model_validate(
            {
                "name": name,
                "repository": entry.repository,
                "maximum_runtime_hours": str(options.maximum_runtime_hours),
                "maximum_attempts": int(options.maximum_attempts),
                "checkpoint": None,
            }
        )
    except ValidationError as error:
        raise RegistrationRefused(
            f"the workload profile is not a valid entry: {error}"
        ) from error
    return workload


def refuse_a_repeat(registry: RepositoryRegistry, entry: RegisteredRepository) -> None:
    """The three uniqueness rules the registry enforces, checked before anything is written.

    ``RepositoryRegistry`` would refuse a duplicate too, and the verification pass below
    would catch it. Checking here is what makes the message name the field that collided
    instead of a pydantic error over the whole document.
    """
    for existing in registry.repositories:
        if existing.repository == entry.repository:
            raise RegistrationRefused(f"already registered: {entry.repository}")
        if existing.github_repository_id == entry.github_repository_id:
            raise RegistrationRefused(
                f"GitHub repository id {entry.github_repository_id} is already registered "
                f"to {existing.repository}"
            )
        if existing.ecr_repository == entry.ecr_repository:
            raise RegistrationRefused(
                f"{entry.ecr_repository} is already the destination for "
                f"{existing.repository}, so pass --ecr-repository with another name"
            )


def plan(
    root: Path, entry: RegisteredRepository, workload: WorkloadProfile, reason: str
) -> list[Edit]:
    registry_path = root / REGISTRY_PATH
    catalog_path = root / WORKLOAD_CATALOG_PATH
    form_path = root / SUBMISSION_FORM_PATH
    ecr_path = root / ECR_TEMPLATE_PATH
    publisher_path = root / PUBLISHER_TEMPLATE_PATH

    registry_before = read(registry_path)
    catalog_before = read(catalog_path)
    form_before = read(form_path)
    ecr_before = read(ecr_path)
    publisher_before = read(publisher_path)

    registry_document = parse(registry_path, registry_before)
    if set(registry_document) != {"repositories"}:
        raise SourceUnusable(f"registry_top_level_keys:{sorted(registry_document)}")
    registry_after = registry_before.rstrip("\n") + "\n" + registry_entry_text(entry, reason)

    # Appended rather than inserted, which is only correct while `workloads` is the last key
    # in the file. Checked rather than assumed: an entry appended under `compute_profiles`
    # would validate as neither and take the whole catalog down.
    catalog_document = parse(catalog_path, catalog_before)
    if list(catalog_document)[-1:] != ["workloads"]:
        raise SourceUnusable(f"catalog_last_key:{list(catalog_document)[-1:]}")
    if any(
        item.get("name") == workload.name
        for item in catalog_document["workloads"]
        if isinstance(item, dict)
    ):
        raise RegistrationRefused(
            f"the catalog already declares a workload profile called {workload.name!r}, so "
            "pass --workload-profile with another name"
        )
    catalog_after = (
        catalog_before.rstrip("\n") + "\n" + workload_entry_text(workload, reason)
    )

    form_after = insert_form_option(
        form_before, "repository", entry.repository, key=str.lower
    )
    form_after = insert_form_option(
        form_after, "workload_profile", workload.name, key=str
    )

    ecr_document = parse(ecr_path, ecr_before)
    logical_id = logical_id_for(entry.repository)
    if logical_id in existing_repositories(ecr_document):
        raise RegistrationRefused(f"the template already declares {logical_id}")
    ecr_after = insert_before_outputs(
        ecr_before,
        ecr_resource_text(logical_id, entry.ecr_repository, lifecycle_policy_text(ecr_document)),
    )
    ecr_after = (
        ecr_after.rstrip("\n")
        + f"\n  {logical_id}Name:\n    Value:\n      Ref: {logical_id}\n"
    )

    owner_id = owner_id_of(parse(publisher_path, publisher_before))
    publisher_after = insert_after_last_list_item(
        publisher_before,
        "token.actions.githubusercontent.com:repository_id",
        f'- "{entry.github_repository_id}"  # {entry.repository}',
    )
    publisher_after = insert_after_last_list_item(
        publisher_after,
        "token.actions.githubusercontent.com:sub",
        f"- repo:edu-llm@{owner_id}/{entry.repository}@{entry.github_repository_id}"
        ":ref:refs/heads/*",
    )
    publisher_after = insert_after_last_list_item(
        publisher_after,
        "Resource",
        "- Fn::Sub: arn:${AWS::Partition}:ecr:${AWS::Region}:${AWS::AccountId}"
        f":repository/{entry.ecr_repository}",
    )

    return [
        Edit(REGISTRY_PATH, registry_before, registry_after),
        Edit(WORKLOAD_CATALOG_PATH, catalog_before, catalog_after),
        Edit(SUBMISSION_FORM_PATH, form_before, form_after),
        Edit(ECR_TEMPLATE_PATH, ecr_before, ecr_after),
        Edit(PUBLISHER_TEMPLATE_PATH, publisher_before, publisher_after),
    ]


def form_inputs_of(text: str) -> dict[str, Any]:
    """The dispatch form's inputs, read the way the tests that hold them to a registry do.

    PyYAML reads a bare ``on:`` key as the boolean ``True``, which is a YAML 1.1 rule and a
    recurring surprise in this repository. Both spellings are tried so a future quoting
    change cannot make the verification pass below read an empty mapping and approve
    anything.
    """
    document = yaml.safe_load(text)
    triggers = document.get(True) or document.get("on")
    return dict(triggers["workflow_dispatch"]["inputs"])


def verify(
    edits: Sequence[Edit], entry: RegisteredRepository, workload: WorkloadProfile
) -> None:
    """Re-run the invariants the test suite asserts, against the text about to be written.

    Every edit above is a text insertion, which is the only way to add to these files
    without a YAML round trip discarding the comments that carry their arguments. The cost
    of text insertion is that it can put something syntactically fine in the wrong place,
    so nothing is written until the result parses and says what it was supposed to say.
    Failing here leaves the tree untouched, because all five files are written together
    afterwards or not at all.

    The form is checked for the two options this change adds and for the order of the lists
    they went into, and deliberately not for agreement with the whole registry. A tree that
    already carries a registration nobody made submittable is a defect this tool did not
    introduce and must not report as its own refusal;
    ``test_every_registered_repository_is_submittable_or_is_visibly_excused`` is what names
    that one, against the committed files, where it can be read.
    """
    by_path = {edit.path: edit.after for edit in edits}

    try:
        registry = RepositoryRegistry.model_validate(
            yaml.safe_load(by_path[REGISTRY_PATH])
        )
    except (ValidationError, yaml.YAMLError) as error:
        raise SourceUnusable(f"registry_would_not_validate:{error}") from error
    if registry.repositories[-1] != entry:
        raise SourceUnusable("registry_entry_is_not_the_one_that_was_asked_for")

    try:
        catalog = WorkloadCatalog.model_validate(
            yaml.safe_load(by_path[WORKLOAD_CATALOG_PATH])
        )
    except (ValidationError, yaml.YAMLError) as error:
        raise SourceUnusable(f"catalog_would_not_validate:{error}") from error
    if catalog.workloads[-1] != workload:
        raise SourceUnusable("catalog_entry_is_not_the_one_that_was_asked_for")
    if workload.repository not in {item.repository for item in registry.repositories}:
        raise SourceUnusable("catalog_entry_names_no_registered_repository")

    try:
        inputs = form_inputs_of(by_path[SUBMISSION_FORM_PATH])
    except (AttributeError, TypeError, KeyError, yaml.YAMLError) as error:
        raise SourceUnusable(f"submission_form_would_not_parse:{error}") from error
    offered_repositories = list(inputs["repository"]["options"])
    offered_workloads = list(inputs["workload_profile"]["options"])
    if entry.repository not in offered_repositories:
        raise SourceUnusable("submission_form_does_not_offer_the_repository")
    if workload.name not in offered_workloads:
        raise SourceUnusable("submission_form_does_not_offer_the_workload")
    if offered_repositories != sorted(offered_repositories, key=str.lower):
        raise SourceUnusable("submission_form_repository_options_are_out_of_order")
    if offered_workloads != sorted(offered_workloads):
        raise SourceUnusable("submission_form_workload_options_are_out_of_order")

    template = yaml.safe_load(by_path[ECR_TEMPLATE_PATH])
    repositories = existing_repositories(template)
    declared = {
        str(resource["Properties"]["RepositoryName"]) for resource in repositories.values()
    }
    if declared != {item.ecr_repository for item in registry.repositories}:
        raise SourceUnusable("template_and_registry_disagree_on_destinations")
    referenced = {
        value["Value"]["Ref"]
        for value in template["Outputs"].values()
        if isinstance(value.get("Value"), dict) and "Ref" in value["Value"]
    }
    if referenced != set(repositories):
        raise SourceUnusable("template_outputs_do_not_reference_every_repository")

    publisher = yaml.safe_load(by_path[PUBLISHER_TEMPLATE_PATH])
    role = next(
        resource
        for resource in publisher["Resources"].values()
        if resource.get("Type") == "AWS::IAM::Role"
    )
    statement = role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    condition = statement["Condition"]
    owner_id = condition["StringEquals"]["token.actions.githubusercontent.com:repository_owner_id"]
    expected_ids = {str(item.github_repository_id) for item in registry.repositories}
    expected_subs = {
        f"repo:edu-llm@{owner_id}/{item.repository}@{item.github_repository_id}"
        ":ref:refs/heads/*"
        for item in registry.repositories
    }
    trusted = {
        str(value)
        for value in condition["StringEquals"]["token.actions.githubusercontent.com:repository_id"]
    }
    accepted = {
        str(value)
        for value in condition["StringLike"]["token.actions.githubusercontent.com:sub"]
    }
    if trusted != expected_ids:
        raise SourceUnusable("publisher_trust_ids_and_registry_disagree")
    if accepted != expected_subs:
        raise SourceUnusable("publisher_subjects_and_registry_disagree")

    granted = {
        str(resource["Fn::Sub"]).rsplit("repository/", 1)[-1]
        for policy in role["Properties"]["Policies"]
        for item in policy["PolicyDocument"]["Statement"]
        for resource in (
            item["Resource"] if isinstance(item["Resource"], list) else [item["Resource"]]
        )
        if isinstance(resource, dict) and "repository/" in str(resource.get("Fn::Sub", ""))
    }
    if granted != {item.ecr_repository for item in registry.repositories}:
        raise SourceUnusable("publisher_push_scope_and_registry_disagree")


def commit_message(entry: RegisteredRepository, reason: str) -> str:
    """One imperative sentence and a body, which is the shape every commit here has.

    The body says what the registration does not yet do, because the reader most likely to
    need that is somebody running ``git log`` on a repository whose builds are failing at
    AssumeRole and wondering when it was onboarded.
    """
    return (
        f"Register {entry.repository} and give it somewhere to publish\n"
        "\n"
        f"{' '.join(reason.split())}\n"
        "\n"
        f"Images go to {entry.ecr_repository}, which "
        ".github/workflows/deploy-phase1-ecr.yml creates when this merges. The publisher "
        "role widening in the same change is an IAM stack, so it is applied from a laptop "
        "and the registration is inert until somebody does.\n"
    )


def pull_request_body(
    entry: RegisteredRepository,
    workload: WorkloadProfile,
    reason: str,
    *,
    base_known: bool,
) -> str:
    """What a reviewer needs that the diff does not say, and nothing the diff already says.

    The follow-up list is the part worth having. Three of its steps are not predictable
    from the files this changes, so a reviewer who approves the diff and stops has left a
    registration that looks complete and cannot publish.
    """
    lines = [
        f"Registers `{entry.repository}` and points its images at `{entry.ecr_repository}`.",
        "",
        f"**Why a repository of its own.** {' '.join(reason.split())}",
        "",
        (
            f"**It is submittable, and the bounds are a default.** `{workload.name}` is on "
            "the `workload_profile` dropdown and `"
            f"{entry.repository}` is on the `repository` one, so a run can name this "
            f"repository the day the image lands. The entry declares "
            f"{workload.maximum_runtime_hours} hour and "
            f"{workload.maximum_attempts} attempt with no checkpoint contract, which is "
            "the shape of a check rather than a measurement of this workload -- see the "
            "last follow-up below."
        ),
        "",
        "**Read the base image.** This registration pins "
        f"`{entry.immutable_base_reference}`. "
        + (
            "That is the digest an existing registration already carries, so it has been "
            "reviewed and `config/image-exceptions.yaml` already covers its findings."
            if base_known
            else "No other registration uses this base, so it is a second thing to "
            "review, scan and re-pin, and `config/image-exceptions.yaml` will need its "
            "findings read before anything built on it can run."
        ),
        "",
        (
            "**The ECR repository is not created yet.** It is a CloudFormation resource in "
            "`sbsandbox-intern-edullm-phase1-ecr`, and `deploy-phase1-ecr.yml` creates it "
            "when this merges. Nothing reached the account to open this pull request."
        ),
        "",
        "## Before this repository can publish",
        "",
    ]
    lines += [
        line
        for index, item in enumerate(FOLLOW_UPS, start=1)
        for line in (f"{index}. **{item.summary}.** {item.detail}", "")
    ]
    lines += [
        "## One thing about the checks on this pull request",
        "",
        (
            "It was opened by `github-actions[bot]` using the workflow token, so its "
            "`pull_request` runs start in an approval-required state and the merge box "
            "carries an **Approve workflows to run** banner. Somebody with write has to "
            "press it before CI reports anything. That is GitHub declining to let a token "
            "trigger its own workflows rather than a failure, and a reviewer is already "
            "here."
        ),
    ]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write the registry, ECR template and publisher role edits a "
        "repository registration needs."
    )
    parser.add_argument("--repository", required=True, help="the GitHub repository name")
    parser.add_argument(
        "--github-repository-id",
        required=True,
        type=int,
        help="the immutable numeric id, from `gh api repos/edu-llm/<name> --jq .id`",
    )
    parser.add_argument(
        "--dockerfile-path",
        required=True,
        help="repository-relative path to the Dockerfile, conventionally .edullm/Dockerfile",
    )
    parser.add_argument(
        "--reason",
        required=True,
        help="why this needs a repository of its own rather than a workload in an existing "
        "one. Wrapped into a comment above the entry, where the next reader finds it.",
    )
    parser.add_argument(
        "--ecr-repository",
        default=None,
        help="the destination name, derived from the repository name when omitted",
    )
    parser.add_argument(
        "--workload-profile",
        default=None,
        help="the name of the first workload profile, which is what a submitter picks on "
        "the form. Defaults to the repository name with -check on the end. Without an "
        "entry in config/workload-catalog.yaml a registration reaches no dropdown and no "
        "run can name it, so this is written rather than left as a follow-up.",
    )
    parser.add_argument(
        "--maximum-runtime-hours",
        default=DEFAULT_MAXIMUM_RUNTIME_HOURS,
        help="the workload profile's runtime bound, in base-ten text. Defaults to the one "
        "hour every repository's first entry declares, which is right for a check.",
    )
    parser.add_argument(
        "--maximum-attempts",
        type=int,
        default=DEFAULT_MAXIMUM_ATTEMPTS,
        help="the workload profile's attempt bound. Defaults to one. More than one is "
        "refused here, because a retry needs a checkpoint contract to resume from and this "
        "tool cannot know whether the program writes one.",
    )
    parser.add_argument(
        "--base-image-repository",
        default=DEFAULT_BASE_IMAGE_REPOSITORY,
        help="the base to build from, with no tag or digest. Defaults to the one two "
        "registrations already share, because a second base is a second thing to review.",
    )
    parser.add_argument(
        "--base-image-digest",
        default=None,
        help="the digest to pin. Inherited from an existing registration of the same base "
        "when omitted, and required when that base is new here.",
    )
    parser.add_argument("--default-branch", default="main")
    parser.add_argument("--build-context", default=".")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the same record and write nothing",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    root = Path(options.project_root)

    try:
        registry = load_yaml(root / REGISTRY_PATH, RepositoryRegistry)
        entry = build_entry(options, registry)
        refuse_a_repeat(registry, entry)
        workload = build_workload(options, entry)
        edits = plan(root, entry, workload, str(options.reason))
        verify(edits, entry, workload)
    except RegistrationRefused as refusal:
        print(f"registration_refused: {refusal}", file=sys.stderr)
        return EXIT_REFUSED
    except (SourceUnusable, OSError, KeyError, ValidationError, yaml.YAMLError) as error:
        print(f"registration_unusable: {error}", file=sys.stderr)
        return EXIT_UNUSABLE

    if not options.dry_run:
        # Every file after every check, so a refusal partway through leaves a tree that is
        # merely unchanged rather than one carrying a registration in one file and not the
        # other two. That half state is the exact shape `edullm-data` shipped in.
        try:
            for edit in edits:
                (root / edit.path).write_text(edit.after, encoding="utf-8")
        except OSError as error:
            print(f"registration_unusable: {error}", file=sys.stderr)
            return EXIT_UNUSABLE

    known_bases = {item.base_image_repository for item in registry.repositories}
    base_known = entry.base_image_repository in known_bases
    reason = str(options.reason)
    print(
        json.dumps(
            {
                "repository": entry.repository,
                "github_repository_id": entry.github_repository_id,
                "ecr_repository": entry.ecr_repository,
                "base_image_reference": entry.immutable_base_reference,
                "base_image_already_registered": base_known,
                "branch": f"register/{entry.repository.lower()}",
                "commit_message": commit_message(entry, reason),
                "dry_run": bool(options.dry_run),
                "follow_ups": [
                    {"summary": item.summary, "detail": item.detail}
                    for item in FOLLOW_UPS
                ],
                "paths": [edit.path.as_posix() for edit in edits],
                "pull_request_body": pull_request_body(
                    entry, workload, reason, base_known=base_known
                ),
                "pull_request_title": f"Register {entry.repository}",
                "workload_profile": workload.name,
                "diff": "".join(edit.unified() for edit in edits),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
