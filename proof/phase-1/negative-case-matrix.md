# Phase 1 negative-case matrix

The 8 Phase 1 acceptance criteria, mapped to the tests cited for each one by node id. Each cited node id was collected and executed by this generator before the bundle was written; a citation pytest cannot collect aborts generation rather than being printed.

This mapping is defined once, in `src/edullm_platform/phase1_criteria.py`. The acceptance gate reads the same definition and executes the same node ids, so this matrix and `tools/validate_phase1.py` cannot disagree.

Verification run: 405 tests executed, 405 passed, 0 failed, 0 errored, pytest exit code 0.

Three statuses exist and no more. **COVERED** means one or more cited tests prove the criterion as stated against the shipped configuration and all of them pass; the gate passes it. **DEFERRED** means an explicit recorded decision not to satisfy it yet, which requires both a written reason and a written trigger describing what makes it live again; the gate passes it. **GAP** is everything else, and the gate fails it. There is no in-between status, because an in-between status is what lets a gate be green and wrong at the same time.

`proving` tests prove the criterion as stated against the shipped configuration; only a COVERED criterion may cite one. `supporting` tests are cited evidence that does not amount to proof — either because they exercise the code path under a synthetic configuration that is not what ships, or because they prove only part of the claim. Both kinds are executed. A supporting citation that is renamed or deleted still fails the criterion.

| # | status | proving | supporting | check |
| --- | --- | --- | --- | --- |
| 1 | COVERED | 3 | 3 | A pushed branch commit produces a digest. |
| 2 | COVERED | 14 | 1 | Rebuilding identical inputs is explainable even if byte-level image reproducibility differs. |
| 3 | COVERED | 8 | 0 | A dirty or unpushed commit is rejected. |
| 4 | COVERED | 7 | 4 | A commit from an unauthorized repository is rejected. |
| 5 | COVERED | 4 | 4 | A pull-request test job cannot request AWS credentials. |
| 6 | COVERED | 3 | 5 | The publisher role cannot submit jobs, read datasets, alter IAM, or modify Batch. |
| 7 | COVERED | 2 | 2 | An immutable tag cannot be overwritten. |
| 8 | COVERED | 4 | 0 | A run manifest using a tag instead of a digest is rejected. |

## Checks

### Check 1 — A pushed branch commit produces a digest.

**Status: COVERED**

Scope:

- This is the one criterion that could only ever close with evidence. The publish workflow ran against a real branch commit of OLMo-core, ECR returned a digest, and the sanitized record of what the registry holds is committed under fixtures/evidence/phase-1/run/. Every test in this repository stops at the edge of the AWS call, so no test substitutes; what the cited tests prove is that the committed record says what it is read as saying.
- The digest belongs to the commit rather than to whatever was last pushed. The tag is the commit's first twelve characters and the contract re-checks that, the recorded base image digest is the one config/repositories.yaml registers, and the recorded push time falls inside the window of a publisher session the capture tied to the push through the session-creation instant the push itself carries.
- One commit, one repository, one run. Nothing here says the next commit will publish, and nothing here is a claim about a repository other than the one registered.
- This rests on captured evidence and expires with it. The records under fixtures/evidence/phase-1/run/ stop loading thirty days after they were observed, tests/test_phase1_run_evidence.py goes red when they do, and this criterion is a gap again from that date. What has lapsed then is not the run — the image, its scan, the session and the refusals are all still in the account and in CloudTrail — but how recently anybody went and looked. Re-capturing costs a read of the account and not another publish.

Proving tests (3), all executed and passing:

- `tests/test_phase1_run_evidence.py::test_the_committed_records_of_the_run_all_hold`
- `tests/test_phase1_run_evidence.py::test_a_pushed_branch_commit_produced_a_digest`
- `tests/test_phase1_run_evidence.py::test_the_digest_was_pushed_by_a_bounded_publisher_session`

Supporting tests (3), all executed and passing, cited as evidence rather than as proof:

- `tests/test_build_research_image_workflow.py::test_publish_job_takes_the_digest_from_an_ecr_read_back_not_the_local_build`
- `tests/test_build_research_image_workflow.py::test_publish_job_builds_from_the_registered_base_digest_under_an_immutable_tag`
- `tests/test_build_research_image_workflow.py::test_publish_job_reverifies_the_source_before_it_holds_aws_credentials`

### Check 2 — Rebuilding identical inputs is explainable even if byte-level image reproducibility differs.

**Status: COVERED**

Scope:

- What is claimed is explainability, and what closes it is an explanation with an executable check behind it rather than a paragraph. The same commit was built from the same digest-pinned base four times, the image the workflow published was fetched from the registry to compare against, and the five image configurations are committed under fixtures/evidence/phase-1/rebuild/. Of seventy leaf fields, two independent no-cache builds of identical inputs differ in exactly two: the instant the image records for itself and the same instant against the one step this Dockerfile executes.
- Four causes account for every difference in all four comparisons, and each is checked rather than asserted. Varying only the per-run label adds that label and nothing else. Varying only the file modification times of the checkout adds the copied layer's digest and nothing else. The published image differs further in the layer the WORKDIR creates, which carries the build's own clock. A field derived from a pinned input — the environment, the command, the working directory, the architecture, the three content labels, every recorded build step, and all four layers inherited from the base — never moves in any comparison, and that is asserted separately so the list of causes cannot be widened until it covers anything.
- The builds are local and are not workflow runs, and they could not have been. The publish job looks the tag up before it builds, so a re-run of the same commit resumes to the published digest rather than building again — correct behaviour, and the reason the shipped path can never produce this comparison. The comparison therefore describes one builder on one machine, both recorded in the file, and says nothing about a different BuildKit.
- Byte-level reproducibility is not claimed and is not attempted. Three of the four causes are clock readings that SOURCE_DATE_EPOCH could pin; the fourth is the per-run label, which is deliberate and whose removal would cost the provenance that lets somebody holding a digest find the run that produced it. Deciding to pin the clocks is a change to the publish workflow that nobody has asked for, and this criterion does not ask for it.

Proving tests (14), all executed and passing:

- `tests/test_phase1_rebuild_comparison.py::test_two_builds_of_identical_inputs_differ_only_in_two_clock_readings`
- `tests/test_phase1_rebuild_comparison.py::test_every_difference_from_the_first_build_has_a_recorded_cause[b]`
- `tests/test_phase1_rebuild_comparison.py::test_every_difference_from_the_first_build_has_a_recorded_cause[c]`
- `tests/test_phase1_rebuild_comparison.py::test_every_difference_from_the_first_build_has_a_recorded_cause[d]`
- `tests/test_phase1_rebuild_comparison.py::test_every_difference_from_the_first_build_has_a_recorded_cause[published]`
- `tests/test_phase1_rebuild_comparison.py::test_no_field_derived_from_a_pinned_input_ever_differs[b]`
- `tests/test_phase1_rebuild_comparison.py::test_no_field_derived_from_a_pinned_input_ever_differs[c]`
- `tests/test_phase1_rebuild_comparison.py::test_no_field_derived_from_a_pinned_input_ever_differs[d]`
- `tests/test_phase1_rebuild_comparison.py::test_no_field_derived_from_a_pinned_input_ever_differs[published]`
- `tests/test_phase1_rebuild_comparison.py::test_the_differences_are_exactly_the_ones_recorded[b]`
- `tests/test_phase1_rebuild_comparison.py::test_the_filesystem_the_image_carries_is_identical_when_nothing_varies`
- `tests/test_phase1_rebuild_comparison.py::test_the_layers_inherited_from_the_pinned_base_never_move`
- `tests/test_phase1_rebuild_comparison.py::test_the_builds_were_made_from_the_base_this_repository_registers`
- `tests/test_phase1_rebuild_comparison.py::test_every_pinned_field_pattern_matches_something_that_was_recorded`

Supporting tests (1), all executed and passing, cited as evidence rather than as proof:

- `tests/test_build_research_image_workflow.py::test_a_published_tag_short_circuits_to_the_digest_the_registry_already_holds`

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
- A second mechanism refuses the same thing further down: the publisher role's trust policy pins repository_id and the OIDC subject to OLMo-core, so a workflow running in another repository cannot assume the role even if source-identity verification were bypassed entirely. That is a fact about the deployed role and not only about the template. The publisher role was deployed once from a laptop and is not redeployed by CI, so this template began as a claim about the account rather than a description of it. A capture has since been taken and compared, and the deployed role matches the template with no findings in either direction; the sanitized record it compared is committed under fixtures/evidence/phase-1/roles/ and the tests cited beside this one re-run the comparison. The citation is still supporting rather than proving, because what a trust policy refuses is an argument from a policy rather than a refusal anybody has observed, and because it expires with the capture.

Proving tests (7), all executed and passing:

- `tests/test_source_identity.py::test_unknown_repository_fails`
- `tests/test_source_identity.py::test_wrong_repository_id_fails`
- `tests/test_verify_source_identity_cli.py::test_rejected_identities_exit_non_zero_with_only_a_machine_readable_reason[overrides0-unregistered_repository]`
- `tests/test_verify_source_identity_cli.py::test_rejected_identities_exit_non_zero_with_only_a_machine_readable_reason[overrides1-repository_id_mismatch]`
- `tests/test_repository_registry.py::test_shipped_repository_registry_contains_exact_olmo_core_registration`
- `tests/test_repository_registry.py::test_repository_registry_unknown_lookups_raise_domain_error`
- `tests/test_build_research_image_workflow.py::test_publish_job_reverifies_the_source_before_it_holds_aws_credentials`

Supporting tests (4), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase1_infrastructure.py::test_publisher_trusts_only_the_existing_github_oidc_provider`
- `tests/test_phase1_deployed_roles.py::test_a_capture_is_committed_for_every_role_a_template_declares`
- `tests/test_phase1_deployed_roles.py::test_every_committed_capture_is_inside_its_freshness_window`
- `tests/test_phase1_deployed_roles.py::test_every_committed_capture_matches_the_template_that_declares_it`

### Check 5 — A pull-request test job cannot request AWS credentials.

**Status: COVERED**

Scope:

- Two independent mechanisms close this and both are cited, because citing one would let the other be removed without anything going red.
- The first is proved as stated. The job that runs untrusted branch code holds contents: read and nothing else, so it has no id-token permission to request a token with; it cannot rather than may not. A reusable workflow can only narrow the permissions its caller grants, so no caller can widen this, and the workflow accepts no secrets either.
- The second is the trust policy's subject condition: sub must match ref:refs/heads/*, and a pull-request job's subject ends in :pull_request, so the role refuses it. The deployed role carries that condition and not merely the template. The publisher role was deployed once from a laptop and is not redeployed by CI, so this template began as a claim about the account rather than a description of it. A capture has since been taken and compared, and the deployed role matches the template with no findings in either direction; the sanitized record it compared is committed under fixtures/evidence/phase-1/roles/ and the tests cited beside this one re-run the comparison. The citation is still supporting rather than proving, because what a trust policy refuses is an argument from a policy rather than a refusal anybody has observed, and because it expires with the capture.
- What is not proved is the caller side. OLMo-core has no caller workflow yet, so nothing here constrains what a future pull-request job in that repository grants itself. The trust policy above is what stands between such a job and this account, and it is supporting evidence rather than proof: a condition nobody has watched refuse anything.

Proving tests (4), all executed and passing:

- `tests/test_build_research_image_workflow.py::test_workflow_has_exactly_three_ordered_jobs_with_exact_permission_maps`
- `tests/test_build_research_image_workflow.py::test_verify_job_never_requests_an_oidc_token_by_any_spelling`
- `tests/test_build_research_image_workflow.py::test_nothing_lets_the_publish_job_run_after_a_gate_has_failed`
- `tests/test_build_research_image_workflow.py::test_workflow_is_reusable_with_exact_inputs_and_no_secrets`

Supporting tests (4), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase1_infrastructure.py::test_publisher_trusts_only_the_existing_github_oidc_provider`
- `tests/test_phase1_deployed_roles.py::test_a_capture_is_committed_for_every_role_a_template_declares`
- `tests/test_phase1_deployed_roles.py::test_every_committed_capture_is_inside_its_freshness_window`
- `tests/test_phase1_deployed_roles.py::test_every_committed_capture_matches_the_template_that_declares_it`

### Check 6 — The publisher role cannot submit jobs, read datasets, alter IAM, or modify Batch.

**Status: COVERED**

Scope:

- Two mechanisms close this and they are different in kind, so read them separately. The first is the distance between the template and the account: the committed template grants one inline policy of nine ECR actions on one repository plus the authorization-token call, no Batch, S3, EC2 or IAM action appears in it, and the deployed role matches. edullm_platform.role_drift compares a captured DeployedRoleEvidence against the committed template and reports any divergence in trust conditions, permission statements, boundary, session duration or attached managed policies, in both directions. tools/capture_phase1_evidence.py ran it against the sandbox: two roles compared, no findings. The sanitized records are committed under fixtures/evidence/phase-1/roles/ and tests/test_phase1_deployed_roles.py re-runs the comparison on every test run, so a policy widened in the console would now be caught the next time either is executed rather than leaving every test green.
- The second is what actually proves it: refusals observed rather than argued. A session issued to the publisher role through OIDC attempted a Batch job submission, an S3 listing, an IAM role creation, a Batch compute-environment update and a deletion of an ECR repository, and was refused all five. Each refusal is committed under fixtures/evidence/phase-1/run/denials/ with the CloudTrail event id a reviewer can look up, and the record must hold one denial per matrix action in matrix order — four refusals would prove the criterion for four actions, and a partial set read later would look like a run that was refused them all.
- The S3 half is narrower than the criterion's words and will stay so. The probe is ListBuckets, an account-level call with no bucket to be absent, so a refusal proves the role holds no account-wide S3 permission rather than that it cannot read a dataset: a policy granting only s3:GetObject on one bucket would be refused ListBuckets just the same. Closing that difference needs an object read that reaches authorization, which needs a bucket this project owns and an object in it that exists. No such bucket is deployed, and pointing the probe at another team's bucket in the shared account would read a refusal from their policy rather than ours.
- Why the probe is ListBuckets at all is worth knowing before anybody adds a sixth. The original S3 probe read an object from a bucket chosen not to exist and answered AccessDenied on one run and NoSuchBucket on the next, for the same role against the same absent bucket — a flake that fails towards passing, since AccessDenied is what the matrix is looking for. edullm_platform.publisher_denials.PROBE_SELECTION_LESSONS records the rule and the run that taught it, and a cited test holds every probe in the matrix to it.
- Five refusals under one session at one moment. A role widened tomorrow would be refused nothing tomorrow, and this record would still read as it does now, which is what the freshness window is for. This rests on captured evidence and expires with it. The records under fixtures/evidence/phase-1/run/ stop loading thirty days after they were observed, tests/test_phase1_run_evidence.py goes red when they do, and this criterion is a gap again from that date. What has lapsed then is not the run — the image, its scan, the session and the refusals are all still in the account and in CloudTrail — but how recently anybody went and looked. Re-capturing costs a read of the account and not another publish.

Proving tests (3), all executed and passing:

- `tests/test_phase1_run_evidence.py::test_the_committed_records_of_the_run_all_hold`
- `tests/test_phase1_run_evidence.py::test_the_publisher_session_was_refused_every_action_the_matrix_attempts`
- `tests/test_phase1_run_evidence.py::test_every_service_criterion_six_names_was_refused`

Supporting tests (5), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase1_deployed_roles.py::test_the_deployed_publisher_grants_ecr_and_nothing_else`
- `tests/test_publisher_denials.py::test_every_probe_in_the_matrix_obeys_the_first_lesson`
- `tests/test_phase1_deployed_roles.py::test_a_capture_is_committed_for_every_role_a_template_declares`
- `tests/test_phase1_deployed_roles.py::test_every_committed_capture_is_inside_its_freshness_window`
- `tests/test_phase1_deployed_roles.py::test_every_committed_capture_matches_the_template_that_declares_it`

### Check 7 — An immutable tag cannot be overwritten.

**Status: COVERED**

Scope:

- Three things are recorded and only one of them is the criterion. The committed template declares IMMUTABLE; the deployed repository was captured and is IMMUTABLE; and a second push of a different image under a tag the registry already held was refused with ImageTagAlreadyExistsException. The first is a document, the second is a setting read back from a describe call, and only the third is a push that was turned away.
- The refusal and the survival are separate claims and both are recorded. The committed refusal carries the digest the tag resolves to after the attempt, and a test checks it against the digest of the image the run published, so this says the original image is still there rather than only that one push failed.
- The second push was made by hand from a laptop, under an identity that is not the publisher role, and the record says so in a field of its own. That is a real limit and a small one: tag immutability is a property of the repository rather than of the caller, so what was observed is that ECR refuses the overwrite, which is the whole of what the criterion claims. What was not observed is the publisher role meeting the same refusal, and the reason nobody arranged that is that the publish workflow deliberately cannot produce it: its pre-flight tag lookup resumes instead of pushing again. The identity that attempted it is not named, because in a shared sandbox account it is a person.
- This rests on captured evidence and expires with it. The records under fixtures/evidence/phase-1/run/ stop loading thirty days after they were observed, tests/test_phase1_run_evidence.py goes red when they do, and this criterion is a gap again from that date. What has lapsed then is not the run — the image, its scan, the session and the refusals are all still in the account and in CloudTrail — but how recently anybody went and looked. Re-capturing costs a read of the account and not another publish.

Proving tests (2), all executed and passing:

- `tests/test_phase1_run_evidence.py::test_the_committed_records_of_the_run_all_hold`
- `tests/test_phase1_run_evidence.py::test_an_immutable_tag_was_not_overwritten_and_the_original_digest_survived`

Supporting tests (2), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase1_infrastructure.py::test_ecr_repository_is_encrypted_scanned_immutable_and_retained`
- `tests/test_build_research_image_workflow.py::test_a_published_tag_short_circuits_to_the_digest_the_registry_already_holds`

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
