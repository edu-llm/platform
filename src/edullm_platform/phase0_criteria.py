"""The Phase 0 acceptance criteria and the tests that are cited for each one.

This module is the only definition of that mapping. Both consumers — the acceptance
gate in ``edullm_platform.phase0_gate`` and the proof-bundle generator in
``tools/build_phase0_proof.py`` — import it from here. Neither carries its own copy,
and ``tests/test_phase0_criteria.py`` fails if a second definition appears anywhere
under ``src/`` or ``tools/``.

What a criterion is, what the three statuses mean, and which citations are legal are all
defined once in ``edullm_platform.criteria`` and shared with every later phase. This
module holds Phase 0's data and nothing else: its fixture corpus, its thirteen criteria,
and the one recorded deferral that sits beside them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from .contracts.authorization import AuthorizationDecision
from .contracts.base import ContractModel
from .contracts.decision_matrix import AuthorizationScenario
from .contracts.manifest import RunManifest
from .criteria import (
    CriteriaDefinitionError,
    CriterionSpec,
    CriterionStatus,
    validate_criterion_specs,
)

__all__ = [
    "FIXTURE_DIRECTORIES",
    "FixtureReference",
    "discover_fixtures",
    "phase0_criteria",
    "recorded_checks",
    "related_deferrals",
]

PHASE0_CRITERION_COUNT: Final = 13

FIXTURE_DIRECTORIES: Final[tuple[tuple[str, type[ContractModel]], ...]] = (
    ("fixtures/manifests", RunManifest),
    ("fixtures/authorization", AuthorizationScenario),
)


@dataclass(frozen=True)
class FixtureReference:
    fixture: str
    relative_path: str
    model_type: type[ContractModel]

    @property
    def contract(self) -> str:
        return self.model_type.__name__


def discover_fixtures(repo_root: Path) -> tuple[FixtureReference, ...]:
    references: list[FixtureReference] = []
    for directory, model_type in FIXTURE_DIRECTORIES:
        for path in sorted((repo_root / directory).glob("*.yaml")):
            references.append(
                FixtureReference(
                    fixture=path.name,
                    relative_path=f"{directory}/{path.name}",
                    model_type=model_type,
                )
            )
    names = [reference.fixture for reference in references]
    if len(set(names)) != len(names):
        raise CriteriaDefinitionError(
            "fixture file names must be unique across fixture directories"
        )
    return tuple(sorted(references, key=lambda reference: reference.relative_path))


def _per_fixture(node: str, fixtures: Sequence[str]) -> tuple[str, ...]:
    return tuple(f"{node}[{fixture}]" for fixture in fixtures)


def _per_required_field(node: str, model: type[ContractModel]) -> tuple[str, ...]:
    return tuple(
        f"{node}[{name}]" for name, model_field in model.model_fields.items() if model_field.is_required()
    )


@dataclass(frozen=True)
class _FixtureNames:
    manifests: tuple[str, ...] = field(default=())
    scenarios: tuple[str, ...] = field(default=())
    every: tuple[str, ...] = field(default=())


def _fixture_names(references: Sequence[FixtureReference]) -> _FixtureNames:
    return _FixtureNames(
        manifests=tuple(
            reference.fixture for reference in references if reference.model_type is RunManifest
        ),
        scenarios=tuple(
            reference.fixture
            for reference in references
            if reference.model_type is AuthorizationScenario
        ),
        every=tuple(reference.fixture for reference in references),
    )


GOLDEN_DIGEST_NODE: Final = (
    "tests/test_phase0_golden.py::test_recorded_fixture_digest_still_matches_the_live_contract"
)

TEAM_BINDINGS_ARE_EMPTY: Final = (
    "config/organization.yaml declares six teams and records no member in any of them, so no "
    "submitter or lead is bound to a team"
)


def phase0_criteria(references: Sequence[FixtureReference]) -> tuple[CriterionSpec, ...]:
    """The thirteen Phase 0 acceptance criteria, in order."""
    names = _fixture_names(references)
    manifests = names.manifests
    scenarios = names.scenarios
    every = names.every
    specs = (
        CriterionSpec(
            number="1",
            statement="Valid fixtures compile identically across repeated runs.",
            status=CriterionStatus.COVERED,
            proving_node_ids=(
                _per_fixture(
                    "tests/test_manifest.py"
                    "::test_representative_manifest_compiles_identically_on_every_load",
                    manifests,
                )
                + _per_fixture(GOLDEN_DIGEST_NODE, every)
                + _per_fixture(
                    "tests/test_phase0_golden.py"
                    "::test_recorded_fixture_contract_and_length_still_match",
                    every,
                )
                + (
                    "tests/test_phase0_golden.py::test_recorded_goldens_cover_every_fixture_on_disk",
                    "tests/test_phase0_golden.py::test_the_proof_bundle_records_golden_digests",
                )
            ),
        ),
        CriterionSpec(
            number="2",
            statement="Field ordering does not change the canonical hash.",
            status=CriterionStatus.COVERED,
            proving_node_ids=(
                _per_fixture(
                    "tests/test_manifest.py::test_source_field_order_does_not_change_a_fixture_digest",
                    manifests,
                )
                + _per_fixture(
                    "tests/test_authorization_fixtures.py"
                    "::test_source_field_order_does_not_change_a_scenario_digest",
                    scenarios,
                )
                + (
                    "tests/test_canonical.py::test_canonical_json_bytes_sorts_keys",
                    "tests/test_canonical.py::test_canonical_json_bytes_sorts_hand_built_payload",
                    "tests/test_fanout.py::test_fanout_manifest_digest_is_stable_across_field_ordering",
                    "tests/test_dataset.py::test_reordering_input_fields_does_not_change_the_digest[dataset-release]",
                    "tests/test_lifecycle.py::test_reordering_input_fields_does_not_change_the_digest[logical-run]",
                    "tests/test_lifecycle.py::test_reordering_input_fields_does_not_change_the_digest[scheduler-attempt]",
                    "tests/test_lifecycle.py::test_reordering_input_fields_does_not_change_the_digest[lifecycle-event]",
                    "tests/test_results.py::test_reordering_input_fields_does_not_change_the_digest[checkpoint-manifest]",
                    "tests/test_results.py::test_reordering_input_fields_does_not_change_the_digest[result-manifest]",
                )
            ),
        ),
        CriterionSpec(
            number="3",
            statement="Unknown schema versions fail closed.",
            status=CriterionStatus.COVERED,
            proving_node_ids=(
                "tests/test_manifest.py::test_manifest_rejects_invalid_field_values[schema_version-2-literal_error]",
                "tests/test_dataset.py::test_unknown_schema_version_fails_closed",
                "tests/test_lifecycle.py::test_unknown_schema_version_fails_closed[logical-run]",
                "tests/test_lifecycle.py::test_unknown_schema_version_fails_closed[scheduler-attempt]",
                "tests/test_lifecycle.py::test_unknown_schema_version_fails_closed[lifecycle-event]",
                "tests/test_results.py::test_unknown_schema_version_fails_closed[checkpoint-manifest]",
                "tests/test_results.py::test_unknown_schema_version_fails_closed[result-manifest]",
                "tests/test_results.py::test_nested_checkpoint_schema_version_fails_closed",
                "tests/test_authorization_fixtures.py::test_scenario_unknown_schema_version_fails_closed",
            ),
        ),
        CriterionSpec(
            number="4",
            statement=(
                "Missing commit, image, data, runtime, or authorization fields fail closed."
            ),
            status=CriterionStatus.COVERED,
            proving_node_ids=(
                _per_required_field(
                    "tests/test_manifest.py"
                    "::test_manifest_rejects_a_payload_that_omits_a_required_field",
                    RunManifest,
                )
                + _per_required_field(
                    "tests/test_authorization_fixtures.py"
                    "::test_scenario_rejects_a_payload_that_omits_a_required_field",
                    AuthorizationScenario,
                )
                + _per_required_field(
                    "tests/test_authorization.py"
                    "::test_decision_rejects_a_payload_that_omits_a_required_field",
                    AuthorizationDecision,
                )
                + (
                    "tests/test_manifest.py::test_the_manifest_payload_this_module_uses_supplies_every_required_field",
                    "tests/test_authorization_fixtures.py::test_the_scenario_payload_this_module_uses_supplies_every_required_field",
                    "tests/test_authorization.py::test_the_decision_payload_this_module_uses_supplies_every_required_field",
                    "tests/test_policy.py::test_request_facts_require_an_explicit_claimed_team",
                    "tests/test_dataset.py::test_object_provenance_fields_are_mandatory[checksum]",
                    "tests/test_dataset.py::test_object_provenance_fields_are_mandatory[s3_version_id]",
                    "tests/test_results.py::test_result_manifest_must_reference_both_the_run_and_its_attempt[run_id]",
                    "tests/test_results.py::test_result_manifest_must_reference_both_the_run_and_its_attempt[attempt_id]",
                    "tests/test_results.py::test_checkpoint_must_state_its_success_marker_explicitly",
                    "tests/test_evidence.py::test_github_plan_rejects_missing_observed_at",
                    "tests/test_evidence.py::test_service_quotas_rejects_missing_observed_at",
                    "tests/test_evidence.py::test_service_quotas_rejects_missing_quota_code",
                    "tests/test_evidence.py::test_service_quotas_rejects_missing_environment",
                    "tests/test_evidence.py::test_service_quotas_rejects_missing_account_alias",
                )
            ),
        ),
        CriterionSpec(
            number="5",
            statement="Mutable image tags are rejected.",
            status=CriterionStatus.COVERED,
            proving_node_ids=(
                "tests/test_manifest.py::test_manifest_rejects_mutable_image_digest",
                "tests/test_manifest.py::test_manifest_rejects_image_digest_with_trailing_tag",
                "tests/test_manifest.py::test_manifest_rejects_non_sha256_image_digest",
                "tests/test_manifest.py::test_manifest_rejects_bare_image_digest_without_algorithm_prefix",
                "tests/test_policy.py::test_unregistered_or_mutable_facts_classify_as_exception[immutable_image-False]",
                "tests/test_phase0_gate.py::test_approval_paths_fails_when_denial_paths_incomplete",
            ),
        ),
        CriterionSpec(
            number="6",
            statement="Short commit SHAs are rejected.",
            status=CriterionStatus.COVERED,
            proving_node_ids=(
                "tests/test_manifest.py::test_manifest_rejects_short_commit_sha",
                "tests/test_manifest.py::test_manifest_rejects_mutable_commit_sha",
                "tests/test_manifest.py::test_manifest_rejects_uppercase_commit_sha",
                "tests/test_manifest.py::test_manifest_rejects_commit_sha_with_trailing_suffix",
                "tests/test_policy.py::test_unregistered_or_mutable_facts_classify_as_exception[immutable_revision-False]",
            ),
        ),
        CriterionSpec(
            number="7",
            statement=(
                "Arbitrary IAM roles, queues, networking, instance types, and mounts are "
                "rejected."
            ),
            status=CriterionStatus.COVERED,
            proving_node_ids=(
                _per_fixture(
                    "tests/test_manifest.py"
                    "::test_manifest_rejects_an_infrastructure_field_the_compute_profile_owns",
                    (
                        "iam_role",
                        "job_queue",
                        "subnet_ids",
                        "security_group_ids",
                        "instance_type",
                        "mounts",
                        "volumes",
                    ),
                )
                + (
                    "tests/test_manifest.py::test_no_manifest_field_names_an_execution_backend",
                    "tests/test_manifest.py::test_sagemaker_and_ec2_training_requests_have_identical_field_sets",
                    "tests/test_manifest.py::test_a_sagemaker_request_differs_from_its_ec2_twin_only_in_the_profile_it_names",
                    "tests/test_config.py::test_contracts_are_strict_and_forbid_extra_fields",
                    "tests/test_fanout.py::test_the_fanout_block_cannot_carry_per_cell_resource_overrides[compute_profile]",
                    "tests/test_fanout.py::test_the_fanout_block_cannot_carry_per_cell_resource_overrides[image_digest]",
                    "tests/test_fanout.py::test_the_fanout_block_cannot_carry_per_cell_resource_overrides[dataset_release]",
                    "tests/test_fanout.py::test_the_fanout_block_cannot_carry_per_cell_resource_overrides[overrides]",
                    "tests/test_compute_profiles.py::test_unpriced_profile_is_refused_as_unregistered",
                    # Repointed three times as profiles were promoted, since the
                    # parametrisation is derived from what is still unprovisioned and a
                    # promoted profile leaves it. gpu-4xa10g was the cited case until the nine
                    # GPU shapes went in, then gpu-1xl40s until two teams asked for it, and
                    # gpu-1xa10g-sagemaker is the only unprovisioned profile left. A fourth
                    # promotion leaves this criterion with nothing shipped to cite.
                    "tests/test_compute_profiles.py::test_an_unprovisioned_profile_is_refused_at_execution[gpu-1xa10g-sagemaker]",
                    "tests/test_workload.py::test_resolving_unknown_profile_reports_unregistered_profile",
                    "tests/test_phase0_gate.py::test_representative_manifests_fails_for_unregistered_compute_profile",
                    "tests/test_bindings.py::test_team_binding_rejects_s3_namespaces_outside_the_sandbox[memory-split]",
                    "tests/test_results.py::test_output_prefix_outside_the_sandbox_bucket_namespace_is_rejected[s3://edullm-checkpoints/runs/olmo/]",
                    "tests/test_workload.py::test_checkpoint_rejects_destination_prefix_outside_sandbox_bucket_namespace[s3://edullm-checkpoints/runs/]",
                )
            ),
        ),
        CriterionSpec(
            number="8",
            statement="Logical run IDs and attempt IDs cannot be confused.",
            status=CriterionStatus.COVERED,
            proving_node_ids=(
                "tests/test_identity.py::test_attempt_id_is_rejected_where_a_run_id_is_required",
                "tests/test_identity.py::test_run_id_is_rejected_where_an_attempt_id_is_required",
                "tests/test_identity.py::test_swapped_identifier_pair_is_rejected_on_both_fields",
                "tests/test_identity.py::test_generated_identifier_kinds_are_never_interchangeable",
                "tests/test_identity.py::test_identifier_parsers_reject_the_other_identifier_kind",
                "tests/test_lifecycle.py::test_scheduler_attempt_rejects_an_attempt_id_where_a_run_id_belongs",
                "tests/test_lifecycle.py::test_scheduler_attempt_rejects_a_run_id_where_an_attempt_id_belongs",
                "tests/test_lifecycle.py::test_scheduler_attempt_rejects_a_swapped_identifier_pair_on_both_fields",
                "tests/test_lifecycle.py::test_logical_run_rejects_an_attempt_id_as_its_parent",
                "tests/test_lifecycle.py::test_lifecycle_event_rejects_identifiers_of_another_kind[run-id]",
                "tests/test_lifecycle.py::test_lifecycle_event_rejects_identifiers_of_another_kind[attempt-id]",
                "tests/test_results.py::test_result_manifest_rejects_an_attempt_id_where_a_run_id_belongs",
                "tests/test_results.py::test_result_manifest_rejects_a_run_id_where_an_attempt_id_belongs",
            ),
        ),
        CriterionSpec(
            number="9",
            statement=(
                "Cross-team attribution fails; a submission naming a team the submitter does "
                "not belong to is rejected. Approver scope is a separate question and follows "
                "`approval_scope`."
            ),
            status=CriterionStatus.DEFERRED,
            # THIS WAS COVERED ON 2026-08-02 AND IS DEFERRED AGAIN ON 2026-08-05, AND THE
            # HONEST WORD FOR THAT IS A DECISION RATHER THAN A REGRESSION.
            #
            # It was closed by four tests run against config/organization.yaml itself, two
            # of which asserted that evaluate_authorization denies a mis-claimed team. That
            # branch is gone. It ran inside admission, downstream of the approval gate, so
            # it never once prevented a submission from committing money -- a lead or an
            # admin had already said yes every time it spoke. It spoke four times in 158
            # submissions, all four against real researchers, two of them the same person
            # twenty-six seconds apart.
            #
            # WHAT IS LEFT IS NOT NOTHING AND IS NOT THE CRITERION EITHER. The comparison
            # still happens twice where it is free: the form's `team` dropdown offers the
            # eight declared ids, and cli.preflight._check_team refuses a claim the roster
            # contradicts before anything is dispatched. Neither is the statement above. A
            # submitter dispatching the form by hand can still name a group they are not in,
            # and what happens then is a decision record carrying team_verified false.
            #
            # So the criterion is recorded rather than enforced, which is the state Phase 0
            # and Phase 2 both described before the assignments landed, reached this time by
            # a deliberate removal rather than by an empty roster.
            deferral_reason=(
                "The refusal that closed this ran inside admission, on the far side of the "
                "approval gate, so it could never prevent spend. It denied four submissions "
                "in 158 and every one of them already had a lead's or an admin's approval "
                "spent on it. It was removed on 2026-08-05 and what a mis-claimed team now "
                "produces is team_verified false on the decision record. The comparison is "
                "still made before the gate, by the form's dropdown and by edullm check, "
                "and neither of those is the statement above: a submitter dispatching the "
                "form by hand can name a group they are not in and be admitted."
            ),
            deferral_trigger=(
                "Something reading team_verified. The flag is on every decision record and "
                "false on 79 of the 158 written so far, and no report surfaces it: the "
                "nightly does not, and tools/build_phase2_proof.py and "
                "tools/build_phase5_proof.py print it per run in documents nobody reads on "
                "a schedule. A cost report that lists runs whose attribution nothing "
                "established closes this as recorded. Refusing again closes it as enforced, "
                "and would have to happen before the gate to be worth anything."
            ),
            supporting_node_ids=(
                (
                    "tests/test_authorization.py::test_a_submitter_naming_their_own_team_is_granted_and_recorded_verified",
                    "tests/test_authorization.py::test_a_recorded_member_claiming_their_own_group_is_verified_on_the_shipped_roster",
                    "tests/test_authorization.py::test_attribution_is_recorded_against_the_shipped_roster_and_not_enforced[curriculum]",
                    "tests/test_authorization.py::test_attribution_is_recorded_against_the_shipped_roster_and_not_enforced[not-a-team]",
                    "tests/test_authorization.py::test_the_verified_flag_is_the_only_thing_a_foreign_team_changes_for_a_lead",
                    "tests/test_authorization.py::test_no_evaluation_against_the_shipped_roster_reaches_the_claimed_team_reason",
                    "tests/test_authorization.py::test_the_retired_claimed_team_reason_still_reads_back_off_a_stored_record",
                    "tests/test_authorization.py::test_every_recorded_member_may_claim_scratch",
                    "tests/test_cli_check.py::test_naming_a_team_the_roster_does_not_put_you_on_is_refused_before_the_gate",
                    "tests/test_policy.py::test_request_facts_require_an_explicit_claimed_team",
                )
                + _per_fixture(
                    "tests/test_authorization.py"
                    "::test_attribution_changes_no_classification_outcome",
                    manifests,
                )
            ),
            scope_limits=(
                (
                    "Attribution travels the whole path. RunManifest.team fills "
                    "RequestFacts.claimed_team, which is required rather than defaulted so a "
                    "caller cannot skip it, and every AuthorizationDecision records both the "
                    "claimed team and whether membership was verified. Two states are "
                    "distinguishable in the audit record now rather than three: verified "
                    "(team_verified true) and not verified (team_verified false), the second "
                    "covering both a claim the roster contradicts and a submitter whose own "
                    "membership nothing records. A reader who needs those apart has the "
                    "roster and the claimed team on the same record and can compare them."
                ),
                (
                    "Attribution is read off the submitter's own membership and nothing else. "
                    "It is independent of who approves, so a lead self-authorising and an "
                    "admin self-approving an exception both record team_verified false for a "
                    "team they do not belong to rather than being refused it. It is also "
                    "independent of the repository: RepositoryBinding exists but no rule "
                    "derives a team from it."
                ),
                (
                    "Four decision records carry reason submitter_not_in_claimed_team and the "
                    "enum member is kept for them. Nothing produces it now, and "
                    "cli.preflight reads the same member for the local refusal, so the word "
                    "in the history and the word a submitter meets cannot drift apart."
                ),
            ),
        ),
        CriterionSpec(
            number="10",
            statement=(
                "Lead self-authorization succeeds only within the lead's bound team and policy."
            ),
            status=CriterionStatus.DEFERRED,
            supporting_node_ids=(
                "tests/test_authorization.py::test_lead_self_authorizes_a_routine_run",
                "tests/test_authorization.py::test_routine_actor_matrix_under_organization_scope[ericrcwu001-None-True-routine_self_authorized]",
                "tests/test_authorization.py::test_plain_member_self_authorizing_a_routine_run_is_denied",
                "tests/test_authorization.py::test_lead_may_not_approve_an_exception",
                "tests/test_authorization.py::test_exception_actor_matrix_under_organization_scope[ericrcwu001-None-False-approver_lacks_admin_role]",
                "tests/test_authorization.py::test_case_variants_of_a_lead_login_are_recognized_as_self_authorization",
                "tests/test_authorization_fixtures.py::test_authorization_fixture_produces_exactly_its_expected_reason[lead-self-authorization.yaml]",
                "tests/test_authorization_fixtures.py::test_the_lead_scenario_records_no_second_approver",
                "tests/test_authorization.py::test_team_scope_leaves_lead_self_authorization_untouched",
                "tests/test_authorization.py::test_team_scope_with_empty_team_bindings_still_allows_lead_self_authorization",
            ),
            deferral_reason=(
                "The criterion has two halves and only one of them is proved, which under three "
                "statuses is not COVERED. Proved today: a team lead may self-authorize a routine "
                "submission, a plain member may not, and a lead may not self-authorize an "
                "exception, which needs a platform admin. Not proved: that the submission falls "
                "inside a team the lead is bound to. config/policy.yaml sets approval_scope to "
                f"organization and {TEAM_BINDINGS_ARE_EMPTY}. There is therefore no bound team "
                "for self-authorization to be confined to. The unproved half is withheld by the "
                "same "
                "recorded decision that defers criterion 9 and D1, not by oversight."
            ),
            deferral_trigger=(
                "config/organization.yaml records member_logins and lead_logins on a team. "
                "Self-authorization is deliberately unaffected by approval_scope today, "
                "and the last two supporting tests cited here pin that so the decision stays "
                "visible. "
                "Once leads are bound to teams, decide whether self-authorization is confined to "
                "the lead's own team and re-record this criterion against that answer."
            ),
        ),
        CriterionSpec(
            number="11",
            statement="A fan-out is priced across the whole submission, not per cell.",
            status=CriterionStatus.COVERED,
            proving_node_ids=(
                "tests/test_fanout.py::test_a_twenty_cell_fanout_costs_twenty_times_one_cell",
                "tests/test_fanout.py::test_attempts_multiply_within_a_cell_and_size_multiplies_across_cells",
                "tests/test_fanout.py::test_the_submission_total_is_rounded_once_rather_than_cell_by_cell",
                "tests/test_fanout.py::test_the_multiseed_fixture_is_priced_across_the_whole_submission",
                "tests/test_fanout.py::test_the_multiseed_fixture_is_not_priced_one_seed_at_a_time",
                "tests/test_fanout.py::test_a_manifest_without_a_fanout_prices_exactly_as_before",
                "tests/test_fanout.py::test_request_facts_carry_the_fanout_shape_declared_by_the_manifest",
            ),
        ),
        CriterionSpec(
            number="12",
            statement=(
                "A fan-out whose total exceeds the routine ceiling classifies as an exception, "
                "so a costly sweep cannot be decomposed into routine single runs."
            ),
            status=CriterionStatus.COVERED,
            proving_node_ids=(
                "tests/test_fanout.py::test_a_sweep_is_priced_as_one_submission_so_it_cannot_hide_behind_cheap_cells",
                "tests/test_fanout.py::test_a_hundred_trivial_cells_is_an_exception_on_count_alone",
                # The manifest-level parallelism citation that sat here went with
                # FanOut.max_parallel. No manifest can raise fanout_parallelism any more, so
                # a test classifying a sweep as an exception on that bound alone cannot be
                # written from a submission. The two test_policy.py entries below still
                # prove the bound itself, built from RequestFacts directly, and
                # test_no_manifest_can_raise_the_parallelism_a_request_is_classified_on
                # records why nothing reaches it from this side.
                "tests/test_fanout.py::test_a_fanout_at_the_count_ceiling_stays_routine",
                "tests/test_fanout.py::test_the_multiseed_fixture_stays_within_the_routine_ceilings",
                "tests/test_policy.py::test_numeric_bound_violations_classify_as_exception[fanout_size-65]",
                "tests/test_policy.py::test_numeric_bound_violations_classify_as_exception[fanout_parallelism-9]",
                "tests/test_policy.py::test_numeric_values_at_threshold_remain_routine[fanout_size-64]",
                "tests/test_policy.py::test_numeric_values_at_threshold_remain_routine[fanout_parallelism-8]",
            ),
        ),
        CriterionSpec(
            number="13",
            statement=(
                "A fan-out mixing compute profiles, image digests, or dataset releases is "
                "rejected."
            ),
            status=CriterionStatus.COVERED,
            proving_node_ids=(
                "tests/test_fanout.py::test_a_fanout_manifest_cannot_declare_two_of_a_shared_resource[compute_profile]",
                "tests/test_fanout.py::test_a_fanout_manifest_cannot_declare_two_of_a_shared_resource[image_digest]",
                "tests/test_fanout.py::test_a_fanout_manifest_cannot_declare_two_of_a_shared_resource[dataset_release]",
                "tests/test_fanout.py::test_the_fanout_block_cannot_carry_per_cell_resource_overrides[compute_profile]",
                "tests/test_fanout.py::test_the_fanout_block_cannot_carry_per_cell_resource_overrides[image_digest]",
                "tests/test_fanout.py::test_the_fanout_block_cannot_carry_per_cell_resource_overrides[dataset_release]",
                "tests/test_fanout.py::test_fanout_participates_in_the_manifest_digest",
            ),
        ),
    )
    if len(specs) != PHASE0_CRITERION_COUNT:
        raise CriteriaDefinitionError(
            f"Phase 0 has {PHASE0_CRITERION_COUNT} acceptance criteria; the definition lists "
            f"{len(specs)}"
        )
    validate_criterion_specs(specs)
    return specs


def related_deferrals(references: Sequence[FixtureReference]) -> tuple[CriterionSpec, ...]:
    """Recorded decisions that are adjacent to the criteria but are not criteria.

    D1 exists because criterion 9 explicitly hands approver scope off to
    ``approval_scope``. It is reported so the decision is visible, and its citations are
    executed like any other, but it does not count toward the thirteen and the gate does
    not read it as a phase criterion.
    """
    del references
    specs = (
        CriterionSpec(
            number="D1",
            statement="Wrong-team lead approver is rejected.",
            status=CriterionStatus.DEFERRED,
            supporting_node_ids=(
                "tests/test_authorization.py::test_team_scope_grants_when_the_approver_leads_the_submitters_team",
                "tests/test_authorization.py::test_flipping_approval_scope_alone_turns_a_grant_into_a_denial",
                "tests/test_authorization.py::test_team_scope_bounds_lead_authority_but_not_admin_authority",
                "tests/test_authorization.py::test_team_scope_reports_absent_bindings_distinctly_from_a_team_mismatch",
                "tests/test_authorization.py::test_team_scope_with_empty_team_bindings_denies_member_routine_runs_without_raising",
                "tests/test_authorization.py::test_decision_records_the_scope_in_force",
            ),
            deferral_reason=(
                "By explicit decision, until sub-team assignments exist. This is Phase 2's "
                "check, and criterion 9 hands it off by name. approval_scope is currently "
                "organization, so any team lead may approve any member's routine submission and "
                "a wrong-team lead approver is therefore granted, not rejected. The supporting "
                "tests cited here prove the code path against a synthetic team-scoped policy with "
                "populated bindings; they do not prove the shipped behaviour."
            ),
            deferral_trigger=(
                "config/policy.yaml sets approval_scope to team and config/organization.yaml "
                "records member_logins on a team. Both are configuration values; flipping them "
                "makes the check live with no code change, and this entry must then be proved "
                "or reopened."
            ),
        ),
    )
    validate_criterion_specs(specs)
    return specs


def recorded_checks(references: Sequence[FixtureReference]) -> tuple[CriterionSpec, ...]:
    """The thirteen criteria followed by the related deferrals, validated together."""
    specs = phase0_criteria(references) + related_deferrals(references)
    validate_criterion_specs(specs)
    return specs
