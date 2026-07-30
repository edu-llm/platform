"""Refusals that no one stage of the pipeline owns.

:class:`SubmissionRefusedError` was defined in :mod:`edullm_platform.submission`, which is
where it is raised most, and that stopped being the right home the moment a second module
had to raise it too. :mod:`edullm_platform.image_resolution` imports the exception from
submission, submission imports ``resolve_image`` back, and because the class sat below
submission's own import block the second import reached a partially initialized module that
did not have the name yet. An exception owned by one side of a dependency is what produced
that cycle, so it lives here instead: a module both sides import and neither is imported by.

What that buys is the shape of the failure and not merely its absence. A cycle here does not
break the feature that closed it. It raises ``ImportError`` on
``edullm_platform.submission`` -- on the package, at import time, before a single resolution
rule has run -- so what anyone meets is not a refusal behaving oddly but a module that will
not import at all. That is the kind of failure that gets read as a broken installation, and
the hour it costs is spent on an environment that was never wrong.
"""

from __future__ import annotations

__all__ = [
    "SubmissionRefusedError",
]


class SubmissionRefusedError(ValueError):
    """The form describes something that cannot be resolved into a manifest.

    Raised in the credential-free compile job, before a reviewer is asked for anything.
    Refusing here rather than letting the request reach a gate is deliberate: a submission
    naming an unregistered dataset is going to be denied by admission whatever a reviewer
    says, and spending a human's attention on it first teaches reviewers that approving is
    a formality.
    """
