# Phase 2 admission denial matrix

The admission role may do exactly one thing: start one Step Functions state machine and read that execution back. The six actions below are the ways that grant could have been wider than it reads, and each is attempted under a real admission session after the approval gate and immediately before `StartExecution`.

That distinction is the whole reason this matrix exists. Every other test of this role reads a committed CloudFormation template, which is what the account was asked for rather than what it holds -- and a role widened in the console leaves every one of them green.

## What is attempted, and why a permitted call would still change nothing

| action | why a permitted call changes nothing |
| --- | --- |
| `batch:SubmitJob` | the queue and the job definition named do not exist |
| `ec2:CreateKeyPair` | the call is a dry run, so nothing is created either way |
| `s3:PutObject` | not inert, and bounded instead -- see below. It is aimed at the real bucket, because an invented one is answered `NoSuchBucket` before anybody is authorized |
| `states:StartExecution` | the state machine named sits beside the real one and does not exist |
| `states:StopExecution` | the execution named is under the real admission machine and was never started |
| `iam:CreateRole` | the role name is the caller's own, which IAM already holds |

The list is read from `edullm_platform.admission_denials` rather than written here, so adding a probe or renaming an action changes this document rather than leaving it behind.

**One of them is not inert and says so.** S3 has no dry run and the bucket has to be the real one, so a permitted `s3:PutObject` writes a zero-byte object once, under `denial-probe/this-object-must-never-exist.txt` and never under `intent/`, `decision/` or `conflicts/`. What is bounded is that it cannot forge or overwrite a lineage record and cannot write a second time; what is not bounded is that the first object exists.

## What choosing a probe has cost

Read this before adding one. Each entry is a rule some probe broke, with what taught it, because a rule with no incident attached reads as caution and gets skipped. Phase 1's list still applies; these are what the admission matrix added.

### A probe whose target carries a resource policy can be refused by that policy instead of by the identity, and S3 does not say which one refused it.

**Learned from.** Reading infra/lineage-bucket.yaml while writing the s3:PutObject probe. The bucket denies s3:PutObject to Principal '*' whenever s3:if-none-match is absent, so an unconditional write is refused for every caller in the account no matter what the admission role holds -- and the refusal is the two words 'Access Denied', with nothing in it that names a policy.

The direction of the failure is what makes it serious. The probe would have answered AccessDenied on every run, including every run on which the role had been widened to write lineage records, so the matrix would have reported the most important entry in it as proved at exactly the moment it stopped being true. Phase 1's resource-policy check does not catch this: it reads the message, and there is no message to read.

The fix is to shape the call so the resource policy has nothing to say about it. Sending --if-none-match '*' satisfies the bucket's condition, so the Deny does not apply and the identity policy is the only thing left that can refuse the call. It also means the write is conditional, so it can never overwrite an object that is already there.

The general rule is to read the target's own policy before pointing a probe at it. Phase 1 could state that none of its five targets carried one; this matrix cannot, because the claim it makes is about this bucket and no other target would prove it.

### A service words both answers in its own vocabulary, so the codes that mean 'refused' and 'allowed' belong to the probe rather than to the matrix.

**Learned from.** Writing the EC2 probe against Phase 1's classifier. EC2 answers a refusal with UnauthorizedOperation rather than AccessDenied, and answers a permitted dry run with DryRunOperation -- an error, with a non-zero exit status, that means the role can mutate EC2.

Two failures, in opposite directions, from one assumption that every service spells these two answers the way IAM and S3 do. Read with Phase 1's code set, a genuine EC2 refusal is 'failed for another reason', so the probe could never prove anything and the matrix would have been quietly one entry short. Read with 'permitted means returncode == 0', a role that can launch GPU instances reports as an inconclusive probe rather than as the emergency it is.

Making a probe inert is what created the second half: --dry-run is the only way to ask EC2 this question without a permitted call starting an instance, and it is precisely what turns success into an error. A technique that makes a probe harmless will often change what its answers look like, and both have to be read together.

### A probe that cannot be made inert is written down as one, not quietly shipped.

**Learned from.** The s3:PutObject probe. S3 has no dry run, the bucket must be the real one -- Phase 1's first lesson is that an absent bucket is answered NoSuchBucket before anybody is authorized -- and there is no form of PutObject that reaches authorization and cannot write.

What is bounded: the body is empty, the key is under denial-probe/ and never under intent/, decision/ or conflicts/, so a permitted write cannot forge or overwrite a lineage record; --if-none-match '*' means it cannot overwrite anything at all; and because that header makes every later write of the same key a 412, a permitted probe can create one object and never a second. What is not bounded: that first object exists. It can be removed, because the bucket enables Object Lock but sets no default retention rule, so nothing holds a stray probe object beyond somebody noticing it.

Two alternatives were considered and rejected. A deliberately wrong --content-md5 would make a permitted write fail after authorization, but whether S3 validates the digest before or after it authorizes cannot be settled without a live run, and getting it wrong makes the most important entry in the matrix permanently unprovable. Writing to a bucket this project creates for the purpose proves the role cannot write to that bucket, which is not the claim. Both trade a bounded, visible cost for an unbounded, invisible one.

## Why this document is not evidence

**It ran, and nothing captured it.** The live matrix executed on 2026-07-27 and refused all six entries. The submission workflow already uploads the result as an `admission-denials` artifact, so what is missing is a download rather than another run: the artifact sanitized, committed under `fixtures/evidence/phase-2/`, and a test that reads it -- with the CloudTrail event id of each refusal, so a reviewer can look any of them up in the account.

**The EC2 entry claims less than it looks like.** `ec2:RunInstances` could not be made conclusive, because EC2 validates the image format, then looks the image up, and only then authorizes, so no absent image ever reaches the question. `ec2:CreateKeyPair` has no resource preconditions and answers from authorization alone, which establishes that this session is refused EC2 mutation rather than that `RunInstances` specifically is refused. The compute path this platform uses is Batch, and `batch:SubmitJob` is denied beside it.

Criterion 14 rests on this document and is a gap for exactly that reason.
