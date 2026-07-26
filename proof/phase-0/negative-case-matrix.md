# Phase 0 negative-case matrix

The 13 Phase 0 acceptance criteria, mapped to the tests cited for each one by node id. Each cited node id was collected and executed by this generator before the bundle was written; a citation pytest cannot collect aborts generation rather than being printed.

This mapping is defined once, in `src/edullm_platform/phase0_criteria.py`. The acceptance gate reads the same definition and executes the same node ids, so this matrix and `tools/validate_phase0.py` cannot disagree.

Verification run: 254 tests executed, 254 passed, 0 failed, 0 errored, pytest exit code 0.

Three statuses exist and no more. **COVERED** means one or more cited tests prove the criterion as stated against the shipped configuration and all of them pass; the gate passes it. **DEFERRED** means an explicit recorded decision not to satisfy it yet, which requires both a written reason and a written trigger describing what makes it live again; the gate passes it. **GAP** is everything else, and the gate fails it. There is no in-between status, because an in-between status is what lets a gate be green and wrong at the same time.

`proving` tests prove the criterion as stated against the shipped configuration; only a COVERED criterion may cite one. `supporting` tests are cited evidence that does not amount to proof — either because they exercise the code path under a synthetic configuration that is not what ships, or because they prove only part of the claim. Both kinds are executed. A supporting citation that is renamed or deleted still fails the criterion.

| # | status | proving | supporting | check |
| --- | --- | --- | --- | --- |
| 1 | COVERED | 26 | 0 | Valid fixtures compile identically across repeated runs. |
| 2 | COVERED | 18 | 0 | Field ordering does not change the canonical hash. |
| 3 | COVERED | 9 | 0 | Unknown schema versions fail closed. |
| 4 | COVERED | 41 | 0 | Missing commit, image, data, runtime, or authorization fields fail closed. |
| 5 | COVERED | 6 | 0 | Mutable image tags are rejected. |
| 6 | COVERED | 5 | 0 | Short commit SHAs are rejected. |
| 7 | COVERED | 22 | 0 | Arbitrary IAM roles, queues, networking, instance types, and mounts are rejected. |
| 8 | COVERED | 13 | 0 | Logical run IDs and attempt IDs cannot be confused. |
| 9 | DEFERRED | 0 | 15 | Cross-team attribution fails; a submission naming a team the submitter does not belong to is rejected. Approver scope is a separate question and follows `approval_scope`. |
| 10 | DEFERRED | 0 | 10 | Lead self-authorization succeeds only within the lead's bound team and policy. |
| 11 | COVERED | 7 | 0 | A fan-out is priced across the whole submission, not per cell. |
| 12 | COVERED | 9 | 0 | A fan-out whose total exceeds the routine ceiling classifies as an exception, so a costly sweep cannot be decomposed into routine single runs. |
| 13 | COVERED | 7 | 0 | A fan-out mixing compute profiles, image digests, or dataset releases is rejected. |
| D1 | DEFERRED | 0 | 6 | Wrong-team lead approver is rejected. |

Rows numbered 1 to 13 are the phase criteria. Rows numbered D-something are recorded decisions adjacent to a criterion; they are shown so the decision is visible, and they are not counted as phase criteria by the gate.

## Deferred by explicit decision

These wait on sub-team assignments. They are recorded here rather than omitted, no test in this bundle claims them as proved, and each one states the condition that makes it live again.

### Check 9 (DEFERRED) — Cross-team attribution fails; a submission naming a team the submitter does not belong to is rejected. Approver scope is a separate question and follows `approval_scope`.

The rule is implemented and exercised, but it rejects nothing in the shipped configuration. config/organization.yaml leaves team_bindings.teams empty, so no submitter or lead is bound to a team and membership cannot be checked at all. Enforcing the rule literally today would deny every submission, including all six run-manifest fixtures, so evaluate_authorization treats empty bindings as unverifiable rather than as failure and records team_verified: false on every shipped decision. No test can therefore show the shipped rejection this criterion asks for, which is why it is not COVERED. It is a deferral rather than a gap because the thing that is missing is data, the decision to withhold that data is recorded here and on D1, and the condition that reverses it is written down below.

Live again when: config/organization.yaml populates team_bindings.teams, which happens once sub-team assignments exist. Enforcement becomes live at that moment with no code change: the supporting tests cited here already drive the denial against populated bindings. When that lands, this criterion must be re-recorded as COVERED with those citations promoted to proving tests, or argued again.

### Check 10 (DEFERRED) — Lead self-authorization succeeds only within the lead's bound team and policy.

The criterion has two halves and only one of them is proved, which under three statuses is not COVERED. Proved today: a team lead may self-authorize a routine submission, a plain member may not, and a lead may not self-authorize an exception, which needs a platform admin. Not proved: that the submission falls inside a team the lead is bound to. config/policy.yaml sets approval_scope to organization and config/organization.yaml leaves team_bindings.teams empty, so no submitter or lead is bound to a team. There is therefore no bound team for self-authorization to be confined to. The unproved half is withheld by the same recorded decision that defers criterion 9 and D1, not by oversight.

Live again when: config/organization.yaml populates team_bindings.teams and sub-team assignments exist. Self-authorization is deliberately unaffected by approval_scope today, and the last two supporting tests cited here pin that so the decision stays visible. Once leads are bound to teams, decide whether self-authorization is confined to the lead's own team and re-record this criterion against that answer.

### Check D1 (DEFERRED) — Wrong-team lead approver is rejected.

By explicit decision, until sub-team assignments exist. This is Phase 2's check, and criterion 9 hands it off by name. approval_scope is currently organization, so any team lead may approve any member's routine submission and a wrong-team lead approver is therefore granted, not rejected. The supporting tests cited here prove the code path against a synthetic team-scoped policy with populated bindings; they do not prove the shipped behaviour.

Live again when: config/policy.yaml sets approval_scope to team and config/organization.yaml populates team_bindings.teams. Both are configuration values; flipping them makes the check live with no code change, and this entry must then be proved or reopened.

## Checks

### Check 1 — Valid fixtures compile identically across repeated runs.

**Status: COVERED**

Proving tests (26), all executed and passing:

- `tests/test_manifest.py::test_representative_manifest_compiles_identically_on_every_load[cpu-routine.yaml]`
- `tests/test_manifest.py::test_representative_manifest_compiles_identically_on_every_load[gpu-exception.yaml]`
- `tests/test_manifest.py::test_representative_manifest_compiles_identically_on_every_load[gpu-routine.yaml]`
- `tests/test_manifest.py::test_representative_manifest_compiles_identically_on_every_load[multiseed-routine.yaml]`
- `tests/test_manifest.py::test_representative_manifest_compiles_identically_on_every_load[olmo-branch-routine.yaml]`
- `tests/test_manifest.py::test_representative_manifest_compiles_identically_on_every_load[sagemaker-routine.yaml]`
- `tests/test_phase0_golden.py::test_recorded_fixture_digest_still_matches_the_live_contract[admin-exception.yaml]`
- `tests/test_phase0_golden.py::test_recorded_fixture_digest_still_matches_the_live_contract[lead-self-authorization.yaml]`
- `tests/test_phase0_golden.py::test_recorded_fixture_digest_still_matches_the_live_contract[member-approval.yaml]`
- `tests/test_phase0_golden.py::test_recorded_fixture_digest_still_matches_the_live_contract[cpu-routine.yaml]`
- `tests/test_phase0_golden.py::test_recorded_fixture_digest_still_matches_the_live_contract[gpu-exception.yaml]`
- `tests/test_phase0_golden.py::test_recorded_fixture_digest_still_matches_the_live_contract[gpu-routine.yaml]`
- `tests/test_phase0_golden.py::test_recorded_fixture_digest_still_matches_the_live_contract[multiseed-routine.yaml]`
- `tests/test_phase0_golden.py::test_recorded_fixture_digest_still_matches_the_live_contract[olmo-branch-routine.yaml]`
- `tests/test_phase0_golden.py::test_recorded_fixture_digest_still_matches_the_live_contract[sagemaker-routine.yaml]`
- `tests/test_phase0_golden.py::test_recorded_fixture_contract_and_length_still_match[admin-exception.yaml]`
- `tests/test_phase0_golden.py::test_recorded_fixture_contract_and_length_still_match[lead-self-authorization.yaml]`
- `tests/test_phase0_golden.py::test_recorded_fixture_contract_and_length_still_match[member-approval.yaml]`
- `tests/test_phase0_golden.py::test_recorded_fixture_contract_and_length_still_match[cpu-routine.yaml]`
- `tests/test_phase0_golden.py::test_recorded_fixture_contract_and_length_still_match[gpu-exception.yaml]`
- `tests/test_phase0_golden.py::test_recorded_fixture_contract_and_length_still_match[gpu-routine.yaml]`
- `tests/test_phase0_golden.py::test_recorded_fixture_contract_and_length_still_match[multiseed-routine.yaml]`
- `tests/test_phase0_golden.py::test_recorded_fixture_contract_and_length_still_match[olmo-branch-routine.yaml]`
- `tests/test_phase0_golden.py::test_recorded_fixture_contract_and_length_still_match[sagemaker-routine.yaml]`
- `tests/test_phase0_golden.py::test_recorded_goldens_cover_every_fixture_on_disk`
- `tests/test_phase0_golden.py::test_the_proof_bundle_records_golden_digests`

### Check 2 — Field ordering does not change the canonical hash.

**Status: COVERED**

Proving tests (18), all executed and passing:

- `tests/test_manifest.py::test_source_field_order_does_not_change_a_fixture_digest[cpu-routine.yaml]`
- `tests/test_manifest.py::test_source_field_order_does_not_change_a_fixture_digest[gpu-exception.yaml]`
- `tests/test_manifest.py::test_source_field_order_does_not_change_a_fixture_digest[gpu-routine.yaml]`
- `tests/test_manifest.py::test_source_field_order_does_not_change_a_fixture_digest[multiseed-routine.yaml]`
- `tests/test_manifest.py::test_source_field_order_does_not_change_a_fixture_digest[olmo-branch-routine.yaml]`
- `tests/test_manifest.py::test_source_field_order_does_not_change_a_fixture_digest[sagemaker-routine.yaml]`
- `tests/test_authorization_fixtures.py::test_source_field_order_does_not_change_a_scenario_digest[admin-exception.yaml]`
- `tests/test_authorization_fixtures.py::test_source_field_order_does_not_change_a_scenario_digest[lead-self-authorization.yaml]`
- `tests/test_authorization_fixtures.py::test_source_field_order_does_not_change_a_scenario_digest[member-approval.yaml]`
- `tests/test_canonical.py::test_canonical_json_bytes_sorts_keys`
- `tests/test_canonical.py::test_canonical_json_bytes_sorts_hand_built_payload`
- `tests/test_fanout.py::test_fanout_manifest_digest_is_stable_across_field_ordering`
- `tests/test_dataset.py::test_reordering_input_fields_does_not_change_the_digest[dataset-release]`
- `tests/test_lifecycle.py::test_reordering_input_fields_does_not_change_the_digest[logical-run]`
- `tests/test_lifecycle.py::test_reordering_input_fields_does_not_change_the_digest[scheduler-attempt]`
- `tests/test_lifecycle.py::test_reordering_input_fields_does_not_change_the_digest[lifecycle-event]`
- `tests/test_results.py::test_reordering_input_fields_does_not_change_the_digest[checkpoint-manifest]`
- `tests/test_results.py::test_reordering_input_fields_does_not_change_the_digest[result-manifest]`

### Check 3 — Unknown schema versions fail closed.

**Status: COVERED**

Proving tests (9), all executed and passing:

- `tests/test_manifest.py::test_manifest_rejects_invalid_field_values[schema_version-2-literal_error]`
- `tests/test_dataset.py::test_unknown_schema_version_fails_closed`
- `tests/test_lifecycle.py::test_unknown_schema_version_fails_closed[logical-run]`
- `tests/test_lifecycle.py::test_unknown_schema_version_fails_closed[scheduler-attempt]`
- `tests/test_lifecycle.py::test_unknown_schema_version_fails_closed[lifecycle-event]`
- `tests/test_results.py::test_unknown_schema_version_fails_closed[checkpoint-manifest]`
- `tests/test_results.py::test_unknown_schema_version_fails_closed[result-manifest]`
- `tests/test_results.py::test_nested_checkpoint_schema_version_fails_closed`
- `tests/test_authorization_fixtures.py::test_scenario_unknown_schema_version_fails_closed`

### Check 4 — Missing commit, image, data, runtime, or authorization fields fail closed.

**Status: COVERED**

Proving tests (41), all executed and passing:

- `tests/test_manifest.py::test_manifest_rejects_a_payload_that_omits_a_required_field[schema_version]`
- `tests/test_manifest.py::test_manifest_rejects_a_payload_that_omits_a_required_field[repository]`
- `tests/test_manifest.py::test_manifest_rejects_a_payload_that_omits_a_required_field[commit_sha]`
- `tests/test_manifest.py::test_manifest_rejects_a_payload_that_omits_a_required_field[image_digest]`
- `tests/test_manifest.py::test_manifest_rejects_a_payload_that_omits_a_required_field[dataset_release]`
- `tests/test_manifest.py::test_manifest_rejects_a_payload_that_omits_a_required_field[command]`
- `tests/test_manifest.py::test_manifest_rejects_a_payload_that_omits_a_required_field[team]`
- `tests/test_manifest.py::test_manifest_rejects_a_payload_that_omits_a_required_field[wandb_project]`
- `tests/test_manifest.py::test_manifest_rejects_a_payload_that_omits_a_required_field[workload_profile]`
- `tests/test_manifest.py::test_manifest_rejects_a_payload_that_omits_a_required_field[compute_profile]`
- `tests/test_manifest.py::test_manifest_rejects_a_payload_that_omits_a_required_field[maximum_runtime_hours]`
- `tests/test_manifest.py::test_manifest_rejects_a_payload_that_omits_a_required_field[maximum_attempts]`
- `tests/test_manifest.py::test_manifest_rejects_a_payload_that_omits_a_required_field[checkpoint]`
- `tests/test_authorization_fixtures.py::test_scenario_rejects_a_payload_that_omits_a_required_field[schema_version]`
- `tests/test_authorization_fixtures.py::test_scenario_rejects_a_payload_that_omits_a_required_field[scenario]`
- `tests/test_authorization_fixtures.py::test_scenario_rejects_a_payload_that_omits_a_required_field[submitter]`
- `tests/test_authorization_fixtures.py::test_scenario_rejects_a_payload_that_omits_a_required_field[approver]`
- `tests/test_authorization_fixtures.py::test_scenario_rejects_a_payload_that_omits_a_required_field[request]`
- `tests/test_authorization_fixtures.py::test_scenario_rejects_a_payload_that_omits_a_required_field[expected]`
- `tests/test_authorization.py::test_decision_rejects_a_payload_that_omits_a_required_field[submitter]`
- `tests/test_authorization.py::test_decision_rejects_a_payload_that_omits_a_required_field[approver]`
- `tests/test_authorization.py::test_decision_rejects_a_payload_that_omits_a_required_field[granted]`
- `tests/test_authorization.py::test_decision_rejects_a_payload_that_omits_a_required_field[approval_class]`
- `tests/test_authorization.py::test_decision_rejects_a_payload_that_omits_a_required_field[approval_scope]`
- `tests/test_authorization.py::test_decision_rejects_a_payload_that_omits_a_required_field[claimed_team]`
- `tests/test_authorization.py::test_decision_rejects_a_payload_that_omits_a_required_field[team_verified]`
- `tests/test_authorization.py::test_decision_rejects_a_payload_that_omits_a_required_field[reason]`
- `tests/test_manifest.py::test_the_manifest_payload_this_module_uses_supplies_every_required_field`
- `tests/test_authorization_fixtures.py::test_the_scenario_payload_this_module_uses_supplies_every_required_field`
- `tests/test_authorization.py::test_the_decision_payload_this_module_uses_supplies_every_required_field`
- `tests/test_policy.py::test_request_facts_require_an_explicit_claimed_team`
- `tests/test_dataset.py::test_object_provenance_fields_are_mandatory[checksum]`
- `tests/test_dataset.py::test_object_provenance_fields_are_mandatory[s3_version_id]`
- `tests/test_results.py::test_result_manifest_must_reference_both_the_run_and_its_attempt[run_id]`
- `tests/test_results.py::test_result_manifest_must_reference_both_the_run_and_its_attempt[attempt_id]`
- `tests/test_results.py::test_checkpoint_must_state_its_success_marker_explicitly`
- `tests/test_evidence.py::test_github_plan_rejects_missing_observed_at`
- `tests/test_evidence.py::test_service_quotas_rejects_missing_observed_at`
- `tests/test_evidence.py::test_service_quotas_rejects_missing_quota_code`
- `tests/test_evidence.py::test_service_quotas_rejects_missing_environment`
- `tests/test_evidence.py::test_service_quotas_rejects_missing_account_alias`

### Check 5 — Mutable image tags are rejected.

**Status: COVERED**

Proving tests (6), all executed and passing:

- `tests/test_manifest.py::test_manifest_rejects_mutable_image_digest`
- `tests/test_manifest.py::test_manifest_rejects_image_digest_with_trailing_tag`
- `tests/test_manifest.py::test_manifest_rejects_non_sha256_image_digest`
- `tests/test_manifest.py::test_manifest_rejects_bare_image_digest_without_algorithm_prefix`
- `tests/test_policy.py::test_unregistered_or_mutable_facts_classify_as_exception[immutable_image-False]`
- `tests/test_phase0_gate.py::test_approval_paths_fails_when_denial_paths_incomplete`

### Check 6 — Short commit SHAs are rejected.

**Status: COVERED**

Proving tests (5), all executed and passing:

- `tests/test_manifest.py::test_manifest_rejects_short_commit_sha`
- `tests/test_manifest.py::test_manifest_rejects_mutable_commit_sha`
- `tests/test_manifest.py::test_manifest_rejects_uppercase_commit_sha`
- `tests/test_manifest.py::test_manifest_rejects_commit_sha_with_trailing_suffix`
- `tests/test_policy.py::test_unregistered_or_mutable_facts_classify_as_exception[immutable_revision-False]`

### Check 7 — Arbitrary IAM roles, queues, networking, instance types, and mounts are rejected.

**Status: COVERED**

Proving tests (22), all executed and passing:

- `tests/test_manifest.py::test_manifest_rejects_an_infrastructure_field_the_compute_profile_owns[iam_role]`
- `tests/test_manifest.py::test_manifest_rejects_an_infrastructure_field_the_compute_profile_owns[job_queue]`
- `tests/test_manifest.py::test_manifest_rejects_an_infrastructure_field_the_compute_profile_owns[subnet_ids]`
- `tests/test_manifest.py::test_manifest_rejects_an_infrastructure_field_the_compute_profile_owns[security_group_ids]`
- `tests/test_manifest.py::test_manifest_rejects_an_infrastructure_field_the_compute_profile_owns[instance_type]`
- `tests/test_manifest.py::test_manifest_rejects_an_infrastructure_field_the_compute_profile_owns[mounts]`
- `tests/test_manifest.py::test_manifest_rejects_an_infrastructure_field_the_compute_profile_owns[volumes]`
- `tests/test_manifest.py::test_no_manifest_field_names_an_execution_backend`
- `tests/test_manifest.py::test_sagemaker_and_ec2_training_requests_have_identical_field_sets`
- `tests/test_manifest.py::test_a_sagemaker_request_differs_from_its_ec2_twin_only_in_the_profile_it_names`
- `tests/test_config.py::test_contracts_are_strict_and_forbid_extra_fields`
- `tests/test_fanout.py::test_the_fanout_block_cannot_carry_per_cell_resource_overrides[compute_profile]`
- `tests/test_fanout.py::test_the_fanout_block_cannot_carry_per_cell_resource_overrides[image_digest]`
- `tests/test_fanout.py::test_the_fanout_block_cannot_carry_per_cell_resource_overrides[dataset_release]`
- `tests/test_fanout.py::test_the_fanout_block_cannot_carry_per_cell_resource_overrides[overrides]`
- `tests/test_compute_profiles.py::test_unpriced_profile_is_refused_as_unregistered`
- `tests/test_compute_profiles.py::test_shipped_profile_is_refused_at_execution_until_provisioned[gpu-4xa10g]`
- `tests/test_workload.py::test_resolving_unknown_profile_reports_unregistered_profile`
- `tests/test_phase0_gate.py::test_representative_manifests_fails_for_unregistered_compute_profile`
- `tests/test_bindings.py::test_team_binding_rejects_s3_namespaces_outside_the_sandbox[memory-split]`
- `tests/test_results.py::test_output_prefix_outside_the_sandbox_bucket_namespace_is_rejected[s3://edullm-checkpoints/runs/olmo/]`
- `tests/test_workload.py::test_checkpoint_rejects_destination_prefix_outside_sandbox_bucket_namespace[s3://edullm-checkpoints/runs/]`

### Check 8 — Logical run IDs and attempt IDs cannot be confused.

**Status: COVERED**

Proving tests (13), all executed and passing:

- `tests/test_identity.py::test_attempt_id_is_rejected_where_a_run_id_is_required`
- `tests/test_identity.py::test_run_id_is_rejected_where_an_attempt_id_is_required`
- `tests/test_identity.py::test_swapped_identifier_pair_is_rejected_on_both_fields`
- `tests/test_identity.py::test_generated_identifier_kinds_are_never_interchangeable`
- `tests/test_identity.py::test_identifier_parsers_reject_the_other_identifier_kind`
- `tests/test_lifecycle.py::test_scheduler_attempt_rejects_an_attempt_id_where_a_run_id_belongs`
- `tests/test_lifecycle.py::test_scheduler_attempt_rejects_a_run_id_where_an_attempt_id_belongs`
- `tests/test_lifecycle.py::test_scheduler_attempt_rejects_a_swapped_identifier_pair_on_both_fields`
- `tests/test_lifecycle.py::test_logical_run_rejects_an_attempt_id_as_its_parent`
- `tests/test_lifecycle.py::test_lifecycle_event_rejects_identifiers_of_another_kind[run-id]`
- `tests/test_lifecycle.py::test_lifecycle_event_rejects_identifiers_of_another_kind[attempt-id]`
- `tests/test_results.py::test_result_manifest_rejects_an_attempt_id_where_a_run_id_belongs`
- `tests/test_results.py::test_result_manifest_rejects_a_run_id_where_an_attempt_id_belongs`

### Check 9 — Cross-team attribution fails; a submission naming a team the submitter does not belong to is rejected. Approver scope is a separate question and follows `approval_scope`.

**Status: DEFERRED**

Deferred because:

The rule is implemented and exercised, but it rejects nothing in the shipped configuration. config/organization.yaml leaves team_bindings.teams empty, so no submitter or lead is bound to a team and membership cannot be checked at all. Enforcing the rule literally today would deny every submission, including all six run-manifest fixtures, so evaluate_authorization treats empty bindings as unverifiable rather than as failure and records team_verified: false on every shipped decision. No test can therefore show the shipped rejection this criterion asks for, which is why it is not COVERED. It is a deferral rather than a gap because the thing that is missing is data, the decision to withhold that data is recorded here and on D1, and the condition that reverses it is written down below.

Live again when:

config/organization.yaml populates team_bindings.teams, which happens once sub-team assignments exist. Enforcement becomes live at that moment with no code change: the supporting tests cited here already drive the denial against populated bindings. When that lands, this criterion must be re-recorded as COVERED with those citations promoted to proving tests, or argued again.

Scope:

- Attribution travels the whole path. RunManifest.team fills RequestFacts.claimed_team, which is required rather than defaulted so a caller cannot skip it, and every AuthorizationDecision records both the claimed team and whether membership was verified. Three states are distinguishable in the audit record: verified and correct (team_verified true), verified and wrong (denied with submitter_not_in_claimed_team), and not verifiable yet (team_verified false with an ordinary approval reason). Every shipped decision today is the third state.
- Attribution is checked against the submitter's own membership and nothing else. It is independent of who approves, so a lead self-authorising and an admin self-approving an exception are both refused a team they do not belong to. It is also independent of the repository: RepositoryBinding exists but no rule derives a team from it.

No test proves this check.

Supporting tests (15), all executed and passing, cited as evidence rather than as proof:

- `tests/test_authorization.py::test_a_submitter_naming_their_own_team_is_granted_and_recorded_verified`
- `tests/test_authorization.py::test_a_submitter_naming_another_teams_id_is_denied_despite_a_valid_lead_approval`
- `tests/test_authorization.py::test_a_team_id_no_roster_defines_is_denied_the_same_way_as_a_foreign_team`
- `tests/test_authorization.py::test_a_lead_self_authorizing_cannot_attribute_the_run_to_a_foreign_team`
- `tests/test_authorization.py::test_an_admin_may_not_attribute_their_run_to_another_teams_budget`
- `tests/test_policy.py::test_request_facts_require_an_explicit_claimed_team`
- `tests/test_authorization.py::test_attribution_is_recorded_unverified_while_the_roster_has_no_teams[memory-split]`
- `tests/test_authorization.py::test_attribution_is_recorded_unverified_while_the_roster_has_no_teams[curriculum]`
- `tests/test_authorization.py::test_attribution_is_recorded_unverified_while_the_roster_has_no_teams[not-a-team]`
- `tests/test_authorization.py::test_attribution_changes_no_classification_outcome[cpu-routine.yaml]`
- `tests/test_authorization.py::test_attribution_changes_no_classification_outcome[gpu-exception.yaml]`
- `tests/test_authorization.py::test_attribution_changes_no_classification_outcome[gpu-routine.yaml]`
- `tests/test_authorization.py::test_attribution_changes_no_classification_outcome[multiseed-routine.yaml]`
- `tests/test_authorization.py::test_attribution_changes_no_classification_outcome[olmo-branch-routine.yaml]`
- `tests/test_authorization.py::test_attribution_changes_no_classification_outcome[sagemaker-routine.yaml]`

### Check 10 — Lead self-authorization succeeds only within the lead's bound team and policy.

**Status: DEFERRED**

Deferred because:

The criterion has two halves and only one of them is proved, which under three statuses is not COVERED. Proved today: a team lead may self-authorize a routine submission, a plain member may not, and a lead may not self-authorize an exception, which needs a platform admin. Not proved: that the submission falls inside a team the lead is bound to. config/policy.yaml sets approval_scope to organization and config/organization.yaml leaves team_bindings.teams empty, so no submitter or lead is bound to a team. There is therefore no bound team for self-authorization to be confined to. The unproved half is withheld by the same recorded decision that defers criterion 9 and D1, not by oversight.

Live again when:

config/organization.yaml populates team_bindings.teams and sub-team assignments exist. Self-authorization is deliberately unaffected by approval_scope today, and the last two supporting tests cited here pin that so the decision stays visible. Once leads are bound to teams, decide whether self-authorization is confined to the lead's own team and re-record this criterion against that answer.

No test proves this check.

Supporting tests (10), all executed and passing, cited as evidence rather than as proof:

- `tests/test_authorization.py::test_lead_self_authorizes_a_routine_run`
- `tests/test_authorization.py::test_routine_actor_matrix_under_organization_scope[ericrcwu001-None-True-routine_self_authorized]`
- `tests/test_authorization.py::test_plain_member_self_authorizing_a_routine_run_is_denied`
- `tests/test_authorization.py::test_lead_may_not_approve_an_exception`
- `tests/test_authorization.py::test_exception_actor_matrix_under_organization_scope[ericrcwu001-None-False-approver_lacks_admin_role]`
- `tests/test_authorization.py::test_case_variants_of_a_lead_login_are_recognized_as_self_authorization`
- `tests/test_authorization_fixtures.py::test_authorization_fixture_produces_exactly_its_expected_reason[lead-self-authorization.yaml]`
- `tests/test_authorization_fixtures.py::test_the_lead_scenario_records_no_second_approver`
- `tests/test_authorization.py::test_team_scope_leaves_lead_self_authorization_untouched`
- `tests/test_authorization.py::test_team_scope_with_empty_team_bindings_still_allows_lead_self_authorization`

### Check 11 — A fan-out is priced across the whole submission, not per cell.

**Status: COVERED**

Proving tests (7), all executed and passing:

- `tests/test_fanout.py::test_a_twenty_cell_fanout_costs_twenty_times_one_cell`
- `tests/test_fanout.py::test_attempts_multiply_within_a_cell_and_size_multiplies_across_cells`
- `tests/test_fanout.py::test_the_submission_total_is_rounded_once_rather_than_cell_by_cell`
- `tests/test_fanout.py::test_the_multiseed_fixture_is_priced_across_the_whole_submission`
- `tests/test_fanout.py::test_the_multiseed_fixture_is_not_priced_one_seed_at_a_time`
- `tests/test_fanout.py::test_a_manifest_without_a_fanout_prices_exactly_as_before`
- `tests/test_fanout.py::test_request_facts_carry_the_fanout_shape_declared_by_the_manifest`

### Check 12 — A fan-out whose total exceeds the routine ceiling classifies as an exception, so a costly sweep cannot be decomposed into routine single runs.

**Status: COVERED**

Proving tests (9), all executed and passing:

- `tests/test_fanout.py::test_a_sweep_is_priced_as_one_submission_so_it_cannot_hide_behind_cheap_cells`
- `tests/test_fanout.py::test_a_hundred_trivial_cells_is_an_exception_on_count_alone`
- `tests/test_fanout.py::test_parallelism_above_the_bound_is_an_exception_on_its_own`
- `tests/test_fanout.py::test_a_fanout_at_both_count_ceilings_stays_routine`
- `tests/test_fanout.py::test_the_multiseed_fixture_stays_within_the_routine_ceilings`
- `tests/test_policy.py::test_numeric_bound_violations_classify_as_exception[fanout_size-65]`
- `tests/test_policy.py::test_numeric_bound_violations_classify_as_exception[fanout_parallelism-9]`
- `tests/test_policy.py::test_numeric_values_at_threshold_remain_routine[fanout_size-64]`
- `tests/test_policy.py::test_numeric_values_at_threshold_remain_routine[fanout_parallelism-8]`

### Check 13 — A fan-out mixing compute profiles, image digests, or dataset releases is rejected.

**Status: COVERED**

Proving tests (7), all executed and passing:

- `tests/test_fanout.py::test_a_fanout_manifest_cannot_declare_two_of_a_shared_resource[compute_profile]`
- `tests/test_fanout.py::test_a_fanout_manifest_cannot_declare_two_of_a_shared_resource[image_digest]`
- `tests/test_fanout.py::test_a_fanout_manifest_cannot_declare_two_of_a_shared_resource[dataset_release]`
- `tests/test_fanout.py::test_the_fanout_block_cannot_carry_per_cell_resource_overrides[compute_profile]`
- `tests/test_fanout.py::test_the_fanout_block_cannot_carry_per_cell_resource_overrides[image_digest]`
- `tests/test_fanout.py::test_the_fanout_block_cannot_carry_per_cell_resource_overrides[dataset_release]`
- `tests/test_fanout.py::test_fanout_participates_in_the_manifest_digest`

### Check D1 — Wrong-team lead approver is rejected.

**Status: DEFERRED**

Deferred because:

By explicit decision, until sub-team assignments exist. This is Phase 2's check, and criterion 9 hands it off by name. approval_scope is currently organization, so any team lead may approve any member's routine submission and a wrong-team lead approver is therefore granted, not rejected. The supporting tests cited here prove the code path against a synthetic team-scoped policy with populated bindings; they do not prove the shipped behaviour.

Live again when:

config/policy.yaml sets approval_scope to team and config/organization.yaml populates team_bindings.teams. Both are configuration values; flipping them makes the check live with no code change, and this entry must then be proved or reopened.

No test proves this check.

Supporting tests (6), all executed and passing, cited as evidence rather than as proof:

- `tests/test_authorization.py::test_team_scope_grants_when_the_approver_leads_the_submitters_team`
- `tests/test_authorization.py::test_flipping_approval_scope_alone_turns_a_grant_into_a_denial`
- `tests/test_authorization.py::test_team_scope_bounds_lead_authority_but_not_admin_authority`
- `tests/test_authorization.py::test_team_scope_reports_absent_bindings_distinctly_from_a_team_mismatch`
- `tests/test_authorization.py::test_team_scope_with_empty_team_bindings_denies_member_routine_runs_without_raising`
- `tests/test_authorization.py::test_decision_records_the_scope_in_force`
