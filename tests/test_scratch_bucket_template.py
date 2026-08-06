"""The working tier's bucket, and the two properties that make it different from the outputs one.

WHAT THIS MODULE IS NOT. It reads a committed template, which is a claim about what the account
will be asked for rather than a description of what it holds. Every assertion here stays green
against a bucket that was never deployed. Task 10's drill is the half that closes that distance,
by writing an object through the verbs and reading it back.
"""

from __future__ import annotations

from pathlib import Path

from edullm_platform.cli.lane import SCRATCH_BUCKET, load_working_tier_settings
from tests.infrastructure_support import INFRA_ROOT, load_template

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = INFRA_ROOT / "scratch-bucket.yaml"
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

    system-overview.md and decisions.md both name it edullm-scratch, and a person types it. The
    prefix would also be a lie about who owns it: it is the deployer role's wildcard, so a name
    under it says CI may rewrite this bucket freely, and this is the one tier whose whole property
    is that it is a person's own. CI can still create it, by the exact-name grant in
    infra/iam/infra-deployer-role.yaml, which is a narrower thing than the prefix would say.
    """
    assert bucket()["BucketName"] == SCRATCH_BUCKET


def test_every_place_that_names_the_working_tier_agrees() -> None:
    """THE TEST THAT MAKES A HALF-APPLIED RENAME OF THIS BUCKET IMPOSSIBLE.
    Mutation: rename the bucket in any one of the four files and leave the other three.

    Five things have to say the same name and only two of them fail loudly when they disagree.
    A wrong grant in the lane instance role fails at the sync with a message naming the bucket,
    and a wrong BucketName fails at CreateBucket. The researcher role's seventh statement is the
    one that does not: its NotResource fences a lane write to one person's prefix, and a name
    that matches no bucket fences nothing while a name that matches the wrong bucket denies every
    write in the real one with no message a researcher can act on. That statement merged in #243
    and has not deployed, so nothing in the account would surface the mistake either.

    Held against the module constant rather than a literal written here, because a literal in a
    test is a fifth answer to the question and this test exists to establish there is one.

    THE RESEARCHER ROLE'S ENTRY CARRIES THE LAYOUT AS WELL AS THE NAME, WHICH IS WHY IT IS THE
    ONE SPELT OUT IN FULL. The other three name the bucket and stop. That one names the bucket
    and then the path inside it, and the path lost its <team>/ segment on 2026-08-05, so the two
    edits land on one line and either half being stale is the same silent denial. The exact
    string is asserted rather than a bucket-name search, so the three-segment spelling coming
    back fails here as well as in tests/test_researcher_role_template.py's tripwire.
    """
    deployer = (INFRA_ROOT / "iam" / "infra-deployer-role.yaml").read_text(encoding="utf-8")
    instance = (INFRA_ROOT / "iam" / "lane-instance-role.yaml").read_text(encoding="utf-8")
    researcher = (INFRA_ROOT / "iam" / "researcher-role.yaml").read_text(encoding="utf-8")

    assert bucket()["BucketName"] == SCRATCH_BUCKET
    assert f"arn:${{AWS::Partition}}:s3:::{SCRATCH_BUCKET}\n" in deployer, (
        "The deployer's exact-name grant does not name this bucket, so CI is refused at "
        "CreateBucket and the refusal reads like a broken template rather than a missing grant."
    )
    assert f"- arn:aws:s3:::{SCRATCH_BUCKET}\n" in instance
    assert f"- arn:aws:s3:::{SCRATCH_BUCKET}/*\n" in instance, (
        "A lane machine cannot reach the objects it syncs, which fails loudly at the sync."
    )
    assert f"- arn:aws:s3:::{SCRATCH_BUCKET}/${{aws:SourceIdentity}}/*\n" in researcher, (
        "The researcher role fences writes into a bucket by this name and no such bucket "
        "exists, or into a path shaped unlike the one the lane writes. Either denies every lane "
        "write and says nothing about why. The layout is <person>/<project>/, so the excepted "
        "path carries exactly one segment above the objects."
    )


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

    assert any(stack.name == "sbsandbox-intern-edullm-scratch" for stack in STACKS.values()), [
        stack.name for stack in STACKS.values()
    ]
