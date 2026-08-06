"""The working tier's bucket, and the two properties that make it different from the outputs one.

WHAT THIS MODULE IS NOT. It reads a committed template, which is a claim about what the account
will be asked for rather than a description of what it holds. Every assertion here stays green
against a bucket that was never deployed. Task 10's drill is the half that closes that distance,
by writing an object through the verbs and reading it back.
"""

from __future__ import annotations

from pathlib import Path

from edullm_platform.cli.lane import WORK_BUCKET, load_working_tier_settings
from tests.infrastructure_support import INFRA_ROOT, load_template

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = INFRA_ROOT / "work-bucket.yaml"
SETTINGS = load_working_tier_settings(PROJECT_ROOT / "config" / "reports" / "working-tier.yaml")


def bucket() -> dict[str, object]:
    resources = load_template(TEMPLATE_PATH)["Resources"]
    return next(
        value["Properties"]
        for value in resources.values()
        if isinstance(value, dict) and value.get("Type") == "AWS::S3::Bucket"
    )


def rules() -> list[dict[str, object]]:
    lifecycle = bucket()["LifecycleConfiguration"]
    assert isinstance(lifecycle, dict)
    found = lifecycle["Rules"]
    assert isinstance(found, list)
    return found


def test_the_bucket_is_the_one_the_overview_names() -> None:
    """Mutation: give it the sbsandbox-intern-edullm- prefix every other stack's bucket has.

    system-overview.md and decisions.md both name it edullm-work, and a person types it. The
    prefix would also be a lie about who owns it: the deployer role's S3 scope keys on that
    prefix, and a prefixed name would put a bucket CI can rewrite under the one tier whose whole
    property is that it is a person's own.
    """
    assert bucket()["BucketName"] == WORK_BUCKET


def test_objects_expire_on_the_schedule_configuration_names() -> None:
    """THE ONE PROPERTY THAT KEEPS THIS FROM BECOMING A BILL NOBODY CAN EXPLAIN.
    Mutation: change the number here and leave config/reports/working-tier.yaml alone.

    The lane defaults its output here, so this bucket grows with every machine anybody starts and
    nothing prunes it. Held equal to the configuration file rather than written twice, which is
    what stops the deployed rule and the number the CLI quotes disagreeing.
    """
    expiry = next(rule for rule in rules() if rule["Id"] == "expire-working-objects")

    assert expiry["Status"] == "Enabled"
    assert expiry["ExpirationInDays"] == SETTINGS.object_expiry_days


def test_an_abandoned_multipart_upload_is_cleaned_up() -> None:
    """Mutation: drop the rule.

    An incomplete multipart upload is invisible in a listing and is billed. A machine reclaimed
    partway through syncing a large file is exactly how one is left behind, and this bucket is the
    one place where a machine going away mid-write is normal rather than exceptional.
    """
    abort = next(rule for rule in rules() if rule["Id"] == "abort-incomplete-multipart-uploads")

    assert abort["AbortIncompleteMultipartUpload"] == {"DaysAfterInitiation": 7}


def test_there_is_no_versioning_and_that_is_a_decision() -> None:
    """Mutation: copy the outputs bucket's versioning configuration.

    The outputs bucket versions because a retried attempt overwrites its own keys and the first
    attempt's partial output would otherwise vanish. Nothing retries in the lane, so versioning
    here would double the bill to protect a property nobody has, and the expiry rule would then
    need a noncurrent clause to match.
    """
    assert "VersioningConfiguration" not in bucket()


def test_nothing_public_can_reach_it() -> None:
    """Mutation: leave the block off and rely on the account setting.

    This is the one bucket a researcher writes to by hand from a machine they control, so it is
    the one where an accidental public grant is most likely to be made and least likely to be
    reviewed. The block is on the bucket so it holds whatever anybody sets later.
    """
    assert bucket()["PublicAccessBlockConfiguration"] == {
        "BlockPublicAcls": True,
        "BlockPublicPolicy": True,
        "IgnorePublicAcls": True,
        "RestrictPublicBuckets": True,
    }


def test_the_bucket_survives_the_stack_being_deleted() -> None:
    """Mutation: drop the retention policy.

    A stack delete that also deleted this would be data loss for thirty-five people who were not
    party to the rollback. The lineage and outputs buckets both carry Retain for the same reason
    and infra/outputs-bucket.yaml states the cost: recreating the stack fails until the orphan is
    removed by hand.
    """
    resources = load_template(TEMPLATE_PATH)["Resources"]
    entry = next(
        value
        for value in resources.values()
        if isinstance(value, dict) and value.get("Type") == "AWS::S3::Bucket"
    )

    assert entry["DeletionPolicy"] == "Retain"
    assert entry["UpdateReplacePolicy"] == "Retain"


def test_the_stack_is_one_the_audit_knows_about() -> None:
    """Mutation: add the template and not the stack.

    tools/verify_deployed_stacks.py compares the account against a table, and a stack absent from
    it is a stack nothing observes. That is how a template and a deployed resource start
    disagreeing without anybody being told.
    """
    from tools.verify_deployed_stacks import STACKS

    assert any(stack.name == "sbsandbox-intern-edullm-work" for stack in STACKS.values()), [
        stack.name for stack in STACKS.values()
    ]
