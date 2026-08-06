"""Exceptions raised across the submission path, in a module neither side of it owns.

``SubmissionRefusedError`` lived in :mod:`edullm_platform.submission` for as long as that
module was the only thing raising it. It stopped being: the rules deciding which image a
declared commit resolves to are in :mod:`edullm_platform.image_resolution`, they refuse the
same way the rest of compiling refuses, and ``compile_submission`` calls them. So the two
modules point at each other.

**The failure that produced this module was reproduced rather than anticipated.** With the
exception defined in ``submission.py``, adding ``from edullm_platform.image_resolution import
resolve_image`` to that module's import block makes ``import edullm_platform.submission``
raise ``ImportError: cannot import name 'SubmissionRefusedError' from partially initialized
module``. Position is as much the cause as the cycle is -- the class sat below
``submission.py``'s own imports, so by the time ``image_resolution`` asked for it the
partially initialized module did not have the name yet. It presents as an ImportError on the
package rather than on the feature, which is the kind that reads like a broken installation
and sends somebody to reinstall their environment.

A module of its own rather than a home on either side, because an exception owned by one end
of a dependency is precisely what produced the cycle. The denial vocabularies that admission,
Batch and the publisher each keep are registries of *reasons* rather than exceptions, and
they stay where they are.

**WHAT IS ALSO HERE IS THE CODE EACH REFUSAL IS KNOWN BY, AND ONE READER DECIDED THAT.**
``cli/preflight.py`` puts a code on every refusal because a code is what a skill and a test
match on, and matching on an English sentence stops working the moment somebody rewords it.
The compile step raised the same refusals with prose and nothing else, so the rules were
named twice: once in the message here, and once at the call site in ``preflight.py`` that
catches this, which had to invent ``workload_profile_repository_mismatch``,
``process_per_device`` and four more of its own. A second spelling of a rule is a second
answer to a settled question, which is the failure ``preflight.py``'s own docstring warns
about, and it fails in the expensive direction: the two spellings drift, and the CLI clears
a submission the compile step then refuses.

So the code is a ``ClassVar`` on the class that is raised, and both sides read it off there.
``type(exc).reason_code`` where preflight catches the exception, and ``SomeError.reason_code``
where preflight asks the same question over again locally because it holds no catalog
lookup to catch. Neither side spells a code, so neither can fork one, and
``tests/test_refusal_codes.py`` is what holds that. The pattern is
``ComputeProfileResolutionError``'s in :mod:`edullm_platform.contracts.workload`, followed
rather than reinvented so that ``preflight.py`` reads a code the same way wherever it reads
one.

**TWO OF THE CODES BELOW ARE NOT WRITTEN HERE, WHICH IS THE SAME ARGUMENT ONE STEP ON.** The
roster refusal and the unpriceable-profile refusal are the conditions ``AuthorizationReason``
and ``UnregisteredComputeProfileError`` already name, so this reads their strings rather than
retyping them. Retyping is what the imports below cost less than.
"""

from __future__ import annotations

from typing import ClassVar

from edullm_platform.contracts.authorization import AuthorizationReason
from edullm_platform.contracts.workload import UnregisteredComputeProfileError

__all__ = [
    "AmbiguousImageError",
    "Bfloat16NotInTheHardwareError",
    "CheckpointPathNotInCommandError",
    "DeniedOutrightError",
    "ExperimentNotASlugError",
    "ImageNotPublishedFromTheCommitError",
    "NoPublishedImageError",
    "ProcessPerDeviceError",
    "RetiredDatasetReleaseError",
    "RetryWithoutACheckpointContractError",
    "SubmissionRefusedError",
    "SubmitterNotOnTheRosterError",
    "TeamNotASlugError",
    "UnpriceableComputeProfileError",
    "UnregisteredRepositoryError",
    "UnregisteredWorkloadProfileError",
    "WorkloadProfileRepositoryMismatchError",
]


class SubmissionRefusedError(ValueError):
    """The form describes something that cannot be resolved into a manifest.

    Raised in the credential-free compile job, before a reviewer is asked for anything.
    Refusing here rather than letting the request reach a gate is deliberate: a submission
    naming an unregistered dataset is going to be denied by admission whatever a reviewer
    says, and spending a human's attention on it first teaches reviewers that approving is
    a formality.

    Annotated and unassigned, exactly as ``ComputeProfileResolutionError`` is, so that this
    class carries no code to inherit. Raising it directly is therefore the shape of a
    refusal somebody forgot to name, and it is the shape
    ``tests/test_refusal_codes.py`` fails on.
    """

    reason_code: ClassVar[str]


# ---------------------------------------------------------------------------------------
# Refused while the form is being turned into a manifest: edullm_platform.submission
# ---------------------------------------------------------------------------------------


class UnregisteredWorkloadProfileError(SubmissionRefusedError):
    reason_code: ClassVar[str] = "unregistered_workload_profile"


class UnregisteredRepositoryError(SubmissionRefusedError):
    reason_code: ClassVar[str] = "unregistered_repository"


class SubmitterNotOnTheRosterError(SubmissionRefusedError):
    """The roster refusal admission would arrive at, asked while it is still cheap.

    Its code is admission's own, read off the enum rather than retyped, because this is
    that condition happening earlier rather than a second condition resembling it.
    """

    reason_code: ClassVar[str] = AuthorizationReason.SUBMITTER_NOT_IN_ROSTER.value


class WorkloadProfileRepositoryMismatchError(SubmissionRefusedError):
    reason_code: ClassVar[str] = "workload_profile_repository_mismatch"


class RetiredDatasetReleaseError(SubmissionRefusedError):
    """A registered corpus its owner has stopped naming as the one to use.

    NAMED AFTER THE FORM FIELD RATHER THAN AFTER A POLICY CONDITION, WHICH IS THE WHOLE OF
    WHERE THIS RULE LIVES. ``unregistered_dataset`` and ``dataset_is_not_a_corpus`` are
    conditions ``config/policy.yaml`` denies outright, derived inside the admission
    validator from its own packaged registry and refusable by nobody. This is neither. It
    is refused twice before the approval gate -- on the laptop by ``edullm check`` and in
    the credential-free compile job -- and it is liftable by the people who own the file
    that sets the flag. ``retired_dataset_release`` reads beside
    ``workload_profile_repository_mismatch`` for that reason and deliberately not beside
    ``dataset_is_not_a_corpus``.
    """

    reason_code: ClassVar[str] = "retired_dataset_release"


class RetryWithoutACheckpointContractError(SubmissionRefusedError):
    reason_code: ClassVar[str] = "retry_without_a_checkpoint_contract"


class ExperimentNotASlugError(SubmissionRefusedError):
    reason_code: ClassVar[str] = "experiment_not_a_slug"


class UnpriceableComputeProfileError(SubmissionRefusedError):
    """A profile with no rate, which is a profile the catalog does not register.

    Reached by catching ``UnregisteredComputeProfileError`` out of the pricing helper, so
    it carries that class's code: one condition, arriving at a submitter as a refusal here
    and at ``edullm check`` as the same word from the resolver.
    """

    reason_code: ClassVar[str] = UnregisteredComputeProfileError.reason_code


class TeamNotASlugError(SubmissionRefusedError):
    """No preflight counterpart, and named to read beside the one that has one.

    ``edullm check`` reports this inside ``submission_cannot_be_priced``, because the
    laptop meets it as whichever field ``RequestFacts`` objected to rather than as a rule
    of its own. Named ``team_not_a_slug`` so that the day it is reported separately it
    reads beside ``experiment_not_a_slug`` rather than against it.
    """

    reason_code: ClassVar[str] = "team_not_a_slug"


class DeniedOutrightError(SubmissionRefusedError):
    """Policy denies this rather than classifying it, whoever would have released it.

    One code for the refusal and not one per condition. The conditions are policy's
    vocabulary and are already named individually where they are read: ``edullm check``
    puts each tripped condition on a refusal of its own, out of
    ``denied_outright_conditions``, and this says only that at least one was tripped.
    """

    reason_code: ClassVar[str] = "denied_outright_by_policy"


# ---------------------------------------------------------------------------------------
# Refused while the declared commit is resolved to an image: image_resolution
# ---------------------------------------------------------------------------------------


class NoPublishedImageError(SubmissionRefusedError):
    """The one refusal here a laptop cannot make, and it shares its name with the deferral.

    ``preflight.DEFERRED_TO_SUBMIT`` names this check as deferred rather than passed, under
    this code, because asking the registry needs a credential the binary does not hold. The
    code is the same word in both places so that a reader who was told a check was deferred
    recognises the refusal when it arrives.
    """

    reason_code: ClassVar[str] = "no_published_image"


class ImageNotPublishedFromTheCommitError(SubmissionRefusedError):
    reason_code: ClassVar[str] = "image_not_published_from_the_commit"


class AmbiguousImageError(SubmissionRefusedError):
    reason_code: ClassVar[str] = "image_is_ambiguous"


# ---------------------------------------------------------------------------------------
# Refused on the text of the command: launchers, checkpoint_commands, precision
# ---------------------------------------------------------------------------------------


class ProcessPerDeviceError(SubmissionRefusedError):
    """One code over the two ways the rule is broken, which is how the rule is written.

    ``require_a_process_for_every_device`` refuses a command naming no launcher and a
    command naming the wrong rank count, and its docstring says the two are one rule in
    both directions. ``edullm check`` has always reported them under one code, and
    splitting them here would be this change inventing a distinction while claiming to
    change nothing.
    """

    reason_code: ClassVar[str] = "process_per_device"


class TensorParallelFlagIgnoredError(SubmissionRefusedError):
    """A code of its own rather than ``process_per_device``, which it sits beside.

    That rule asks whether the command's process count matches the shape, and its answer is
    a number a submitter can change. This asks whether a flag the submitter did write is one
    the harness reads, and it is true of commands that pass the device-count check -- the
    short spelling declares four and the harness hears one, so both readings of
    ``process_per_device`` are the wrong sentence for it.
    """

    reason_code: ClassVar[str] = "tensor_parallel_flag_ignored"


class CheckpointPathNotInCommandError(SubmissionRefusedError):
    reason_code: ClassVar[str] = "checkpoint_path_not_in_command"


class Bfloat16NotInTheHardwareError(SubmissionRefusedError):
    reason_code: ClassVar[str] = "bfloat16_not_in_the_hardware"
