# Phase 1 negative-case matrix

The 8 Phase 1 acceptance criteria, mapped to the tests cited for each one by node id. Each cited node id was collected and executed by this generator before the bundle was written; a citation pytest cannot collect aborts generation rather than being printed.

This mapping is defined once, in `src/edullm_platform/phase1_criteria.py`. The acceptance gate reads the same definition and executes the same node ids, so this matrix and `tools/validate_phase1.py` cannot disagree.

Verification run: 308 tests executed, 308 passed, 0 failed, 0 errored, pytest exit code 0.

Three statuses exist and no more. **COVERED** means one or more cited tests prove the criterion as stated against the shipped configuration and all of them pass; the gate passes it. **DEFERRED** means an explicit recorded decision not to satisfy it yet, which requires both a written reason and a written trigger describing what makes it live again; the gate passes it. **GAP** is everything else, and the gate fails it. There is no in-between status, because an in-between status is what lets a gate be green and wrong at the same time.

`proving` tests prove the criterion as stated against the shipped configuration; only a COVERED criterion may cite one. `supporting` tests are cited evidence that does not amount to proof — either because they exercise the code path under a synthetic configuration that is not what ships, or because they prove only part of the claim. Both kinds are executed. A supporting citation that is renamed or deleted still fails the criterion.

| # | status | proving | supporting | check |
| --- | --- | --- | --- | --- |
| 1 | GAP | 0 | 0 | A pushed branch commit produces a digest. |
| 2 | GAP | 0 | 0 | Rebuilding identical inputs is explainable even if byte-level image reproducibility differs. |
| 3 | COVERED | 8 | 0 | A dirty or unpushed commit is rejected. |
| 4 | COVERED | 7 | 1 | A commit from an unauthorized repository is rejected. |
| 5 | COVERED | 4 | 1 | A pull-request test job cannot request AWS credentials. |
| 6 | GAP | 0 | 0 | The publisher role cannot submit jobs, read datasets, alter IAM, or modify Batch. |
| 7 | GAP | 0 | 0 | An immutable tag cannot be overwritten. |
| 8 | COVERED | 4 | 0 | A run manifest using a tag instead of a digest is rejected. |

## Gaps

Read these first. A matrix that overstates coverage is worse than no matrix. Every gap here fails the acceptance gate, and each one is unfinished work rather than a recorded decision to postpone: a deferral needs a written reason and a written trigger, and neither exists for any of these.

### Check 1 (GAP) — A pushed branch commit produces a digest.

- The reusable publish workflow has never completed a run. OLMo-core has neither a caller workflow nor the registered Dockerfile, so the build path has not executed once. No ECR digest exists for any commit, so the one thing this criterion asserts has not happened.
- This closes with evidence rather than with a test: a completed run of the publish workflow against a real branch commit, and the digest the registry returned for it. No test in this repository can substitute, because every one of them stops at the edge of the AWS call.

### Check 2 (GAP) — Rebuilding identical inputs is explainable even if byte-level image reproducibility differs.

- The reusable publish workflow has never completed a run. OLMo-core has neither a caller workflow nor the registered Dockerfile, so the build path has not executed once. Nothing has been built once, so nothing has been rebuilt.
- The claim is about two runs and a written account of the difference between them, and neither the runs nor the account exists. The account has to cover at least the image label carrying the run URL, which differs per run and therefore changes the manifest digest by construction, and the base image, which is pinned by digest and so should not.
- Producing the comparison at all takes a deliberate second build. The pre-flight tag lookup makes an ordinary re-run of the same commit short-circuit to the digest already in the registry, which is the correct behaviour and is not a rebuild.

### Check 6 (GAP) — The publisher role cannot submit jobs, read datasets, alter IAM, or modify Batch.

- The criterion is about what the live role can do. The committed template grants one inline policy of nine ECR actions on one repository, plus the authorization-token call that takes no resource, and no Batch, S3, EC2, or IAM action appears anywhere in it. That is a fact about a document.
- edullm_platform.role_drift compares a captured DeployedRoleEvidence against the committed template and reports any divergence in trust conditions, permission statements, boundary, session duration or attached managed policies, in both directions. tools/capture_phase1_evidence.py runs it against the live account and refuses to report success when the two disagree. What is missing is the capture: no run against the account has been taken, so nothing has compared anything yet. So a policy widened in the console is now detectable, and is still undetected: a comparison nobody has run leaves every test in this repository green exactly as before.
- Two things close this and both are runs rather than tests. The first is a capture: tools/capture_phase1_evidence.py against the sandbox, and the sanitized role record committed under fixtures/evidence/ with no drift findings. That would show the deployed role is the template, which is what makes the template's absence of Batch, S3 and IAM actions mean something.
- The second is a denial observed rather than argued: a session issued to the publisher role attempting a Batch submit, an S3 read, and an IAM change, and the CloudTrail records of those three refusals. edullm_platform.publisher_denials attempts exactly that matrix and tools/verify_publisher_denials.py runs it, and no session has run it.
- A citation on the capture would expire. See the freshness rule in this module's docstring: the record stops loading thirty days after it was taken, and the criterion is a gap again from that moment.

### Check 7 (GAP) — An immutable tag cannot be overwritten.

- The committed ECR template declares IMMUTABLE tag mutability and the repository was deployed from it. Neither fact is the criterion: what is claimed here is that a second push to an existing tag is refused, and that behaviour belongs to ECR at push time.
- tools/capture_phase1_evidence.py can now record what the deployed repository's tag mutability actually is, which would at least close the distance between the template and the account. It would not close this: a setting read back from a describe call is still not a push that was refused.
- The reusable publish workflow has never completed a run. OLMo-core has neither a caller workflow nor the registered Dockerfile, so the build path has not executed once. No image has been pushed once, let alone twice.
- Closing this needs a live second push of a different image under a tag the registry already holds, and the error it returns. The pre-flight tag lookup in the publish workflow exists because that refusal is real and unrecoverable, so proving it also confirms the reason that lookup is there.

## Checks

### Check 1 — A pushed branch commit produces a digest.

**Status: GAP**

Gap:

- The reusable publish workflow has never completed a run. OLMo-core has neither a caller workflow nor the registered Dockerfile, so the build path has not executed once. No ECR digest exists for any commit, so the one thing this criterion asserts has not happened.
- This closes with evidence rather than with a test: a completed run of the publish workflow against a real branch commit, and the digest the registry returned for it. No test in this repository can substitute, because every one of them stops at the edge of the AWS call.

No test proves this check.

### Check 2 — Rebuilding identical inputs is explainable even if byte-level image reproducibility differs.

**Status: GAP**

Gap:

- The reusable publish workflow has never completed a run. OLMo-core has neither a caller workflow nor the registered Dockerfile, so the build path has not executed once. Nothing has been built once, so nothing has been rebuilt.
- The claim is about two runs and a written account of the difference between them, and neither the runs nor the account exists. The account has to cover at least the image label carrying the run URL, which differs per run and therefore changes the manifest digest by construction, and the base image, which is pinned by digest and so should not.
- Producing the comparison at all takes a deliberate second build. The pre-flight tag lookup makes an ordinary re-run of the same commit short-circuit to the digest already in the registry, which is the correct behaviour and is not a rebuild.

No test proves this check.

### Check 3 — A dirty or unpushed commit is rejected.

**Status: COVERED**

Scope:

- Rejection is proved against real git repositories rather than against mocks: each cited test builds a bare origin and a checkout, dirties or diverges it, and asserts the reason the verifier returns.
- Four rejections are proved and they are not the same rejection. A modified tracked file and an untracked file are both a dirty tree; a local commit the remote has not seen is a remote-ref mismatch; a branch the remote does not have at all is a missing remote ref; and a commit that is no longer the checkout's own HEAD is a head mismatch.
- The last citation is what puts the verifier on the shipped path. The publish job re-derives source identity on its own runner before it configures AWS credentials, so a branch head that moved while the gate was running is caught after the gate passed and before anything is pushed.

Proving tests (8), all executed and passing:

- `tests/test_source_identity.py::test_dirty_tracked_worktree_fails`
- `tests/test_source_identity.py::test_dirty_untracked_worktree_fails`
- `tests/test_source_identity.py::test_unpushed_commit_fails_branch_head_verification`
- `tests/test_source_identity.py::test_missing_remote_ref_fails`
- `tests/test_source_identity.py::test_checkout_head_mismatch_fails`
- `tests/test_verify_source_identity_cli.py::test_a_dirty_tree_is_rejected_without_leaking_paths_or_environment`
- `tests/test_verify_source_identity_cli.py::test_rejected_identities_exit_non_zero_with_only_a_machine_readable_reason[overrides4-remote_ref_missing]`
- `tests/test_build_research_image_workflow.py::test_publish_job_reverifies_the_source_before_it_holds_aws_credentials`

### Check 4 — A commit from an unauthorized repository is rejected.

**Status: COVERED**

Scope:

- Authorization is by name and by GitHub's numeric repository id, and both are checked. The name alone would be reusable: a repository can be renamed and its old name claimed by another, while the numeric id it was registered under cannot move.
- The shipped registry authorizes exactly one repository, so the negative case is everything else rather than a curated deny list.
- A second mechanism refuses the same thing further down. The publisher role was deployed once from a laptop and is not redeployed by CI. edullm_platform.role_drift can now compare the deployed role to this template, and tools/capture_phase1_evidence.py runs that comparison as it captures, but no capture has been taken, so the comparison has nothing to run against. Until one is committed under fixtures/evidence/, this citation stays supporting: it proves what the template says rather than what the role does. What the template says is that the publisher role's trust policy pins repository_id and the OIDC subject to OLMo-core, so a workflow running in another repository cannot assume the role even if source-identity verification were bypassed entirely.

Proving tests (7), all executed and passing:

- `tests/test_source_identity.py::test_unknown_repository_fails`
- `tests/test_source_identity.py::test_wrong_repository_id_fails`
- `tests/test_verify_source_identity_cli.py::test_rejected_identities_exit_non_zero_with_only_a_machine_readable_reason[overrides0-unregistered_repository]`
- `tests/test_verify_source_identity_cli.py::test_rejected_identities_exit_non_zero_with_only_a_machine_readable_reason[overrides1-repository_id_mismatch]`
- `tests/test_repository_registry.py::test_shipped_repository_registry_contains_exact_olmo_core_registration`
- `tests/test_repository_registry.py::test_repository_registry_unknown_lookups_raise_domain_error`
- `tests/test_build_research_image_workflow.py::test_publish_job_reverifies_the_source_before_it_holds_aws_credentials`

Supporting tests (1), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase1_infrastructure.py::test_publisher_trusts_only_the_existing_github_oidc_provider`

### Check 5 — A pull-request test job cannot request AWS credentials.

**Status: COVERED**

Scope:

- Two independent mechanisms close this and both are cited, because citing one would let the other be removed without anything going red.
- The first is proved as stated. The job that runs untrusted branch code holds contents: read and nothing else, so it has no id-token permission to request a token with; it cannot rather than may not. A reusable workflow can only narrow the permissions its caller grants, so no caller can widen this, and the workflow accepts no secrets either.
- The second is the trust policy's subject condition. The publisher role was deployed once from a laptop and is not redeployed by CI. edullm_platform.role_drift can now compare the deployed role to this template, and tools/capture_phase1_evidence.py runs that comparison as it captures, but no capture has been taken, so the comparison has nothing to run against. Until one is committed under fixtures/evidence/, this citation stays supporting: it proves what the template says rather than what the role does. What the template says is that sub must match ref:refs/heads/*, and a pull-request job's subject ends in :pull_request, so the role refuses it.
- What is not proved is the caller side. OLMo-core has no caller workflow yet, so nothing here constrains what a future pull-request job in that repository grants itself. The trust policy above is what stands between such a job and this account, and it is supporting evidence rather than proof.

Proving tests (4), all executed and passing:

- `tests/test_build_research_image_workflow.py::test_workflow_has_exactly_three_ordered_jobs_with_exact_permission_maps`
- `tests/test_build_research_image_workflow.py::test_verify_job_never_requests_an_oidc_token_by_any_spelling`
- `tests/test_build_research_image_workflow.py::test_nothing_lets_the_publish_job_run_after_a_gate_has_failed`
- `tests/test_build_research_image_workflow.py::test_workflow_is_reusable_with_exact_inputs_and_no_secrets`

Supporting tests (1), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase1_infrastructure.py::test_publisher_trusts_only_the_existing_github_oidc_provider`

### Check 6 — The publisher role cannot submit jobs, read datasets, alter IAM, or modify Batch.

**Status: GAP**

Gap:

- The criterion is about what the live role can do. The committed template grants one inline policy of nine ECR actions on one repository, plus the authorization-token call that takes no resource, and no Batch, S3, EC2, or IAM action appears anywhere in it. That is a fact about a document.
- edullm_platform.role_drift compares a captured DeployedRoleEvidence against the committed template and reports any divergence in trust conditions, permission statements, boundary, session duration or attached managed policies, in both directions. tools/capture_phase1_evidence.py runs it against the live account and refuses to report success when the two disagree. What is missing is the capture: no run against the account has been taken, so nothing has compared anything yet. So a policy widened in the console is now detectable, and is still undetected: a comparison nobody has run leaves every test in this repository green exactly as before.
- Two things close this and both are runs rather than tests. The first is a capture: tools/capture_phase1_evidence.py against the sandbox, and the sanitized role record committed under fixtures/evidence/ with no drift findings. That would show the deployed role is the template, which is what makes the template's absence of Batch, S3 and IAM actions mean something.
- The second is a denial observed rather than argued: a session issued to the publisher role attempting a Batch submit, an S3 read, and an IAM change, and the CloudTrail records of those three refusals. edullm_platform.publisher_denials attempts exactly that matrix and tools/verify_publisher_denials.py runs it, and no session has run it.
- A citation on the capture would expire. See the freshness rule in this module's docstring: the record stops loading thirty days after it was taken, and the criterion is a gap again from that moment.

No test proves this check.

### Check 7 — An immutable tag cannot be overwritten.

**Status: GAP**

Gap:

- The committed ECR template declares IMMUTABLE tag mutability and the repository was deployed from it. Neither fact is the criterion: what is claimed here is that a second push to an existing tag is refused, and that behaviour belongs to ECR at push time.
- tools/capture_phase1_evidence.py can now record what the deployed repository's tag mutability actually is, which would at least close the distance between the template and the account. It would not close this: a setting read back from a describe call is still not a push that was refused.
- The reusable publish workflow has never completed a run. OLMo-core has neither a caller workflow nor the registered Dockerfile, so the build path has not executed once. No image has been pushed once, let alone twice.
- Closing this needs a live second push of a different image under a tag the registry already holds, and the error it returns. The pre-flight tag lookup in the publish workflow exists because that refusal is real and unrecoverable, so proving it also confirms the reason that lookup is there.

No test proves this check.

### Check 8 — A run manifest using a tag instead of a digest is rejected.

**Status: COVERED**

Scope:

- Rejection happens at contract validation, so it applies to every manifest that is loaded at all rather than to a checked path a caller might skip.
- Four ways of not being a digest are refused: a bare tag, a digest with a tag appended, a digest under an algorithm other than sha256, and 64 hex characters with no algorithm prefix. The last two matter because each one looks like a digest to a human reader.
- This is the one Phase 1 criterion that was already true before Phase 1 began. The manifest contract is Phase 0 work and the digest it demands is what Phase 1 produces; the criterion is recorded here because the phase depends on it, not because the phase built it.

Proving tests (4), all executed and passing:

- `tests/test_manifest.py::test_manifest_rejects_mutable_image_digest`
- `tests/test_manifest.py::test_manifest_rejects_image_digest_with_trailing_tag`
- `tests/test_manifest.py::test_manifest_rejects_non_sha256_image_digest`
- `tests/test_manifest.py::test_manifest_rejects_bare_image_digest_without_algorithm_prefix`
