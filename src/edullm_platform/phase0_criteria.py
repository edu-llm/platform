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
                    # Repointed twice as profiles were promoted, since the parametrisation is
                    # derived from what is still unprovisioned and a promoted profile leaves
                    # it. gpu-4xa10g was the cited case until the nine GPU shapes went in;
                    # gpu-1xl40s is a single L40S nobody asked for and is the case now.
                    "tests/test_compute_profiles.py::test_an_unprovisioned_profile_is_refused_at_execution[gpu-1xl40s]",
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
            supporting_node_ids=(
                (
                    "tests/test_authorization.py::test_a_submitter_naming_their_own_team_is_granted_and_recorded_verified",
                    "tests/test_authorization.py::test_a_submitter_naming_another_teams_id_is_denied_despite_a_valid_lead_approval",
                    "tests/test_authorization.py::test_a_team_id_no_roster_defines_is_denied_the_same_way_as_a_foreign_team",
                    "tests/test_authorization.py::test_a_lead_self_authorizing_cannot_attribute_the_run_to_a_foreign_team",
                    "tests/test_authorization.py::test_an_admin_may_not_attribute_their_run_to_another_teams_budget",
                    "tests/test_policy.py::test_request_facts_require_an_explicit_claimed_team",
                )
                + _per_fixture(
                    "tests/test_authorization.py"
                    "::test_attribution_is_recorded_unverified_while_no_member_is_bound_to_a_team",
                    ("memory-split", "curriculum", "not-a-team"),
                )
                + _per_fixture(
                    "tests/test_authorization.py"
                    "::test_attribution_changes_no_classification_outcome",
                    manifests,
                )
            ),
            deferral_reason=(
                "The rule is implemented and exercised, but it rejects nothing in the shipped "
                f"configuration. {TEAM_BINDINGS_ARE_EMPTY} and membership cannot be checked at "
                "all. Enforcing the rule literally today would deny every submission, including "
                "all six run-manifest fixtures, so evaluate_authorization treats empty bindings "
                "as unverifiable rather than as failure and records team_verified: false on "
                "every shipped decision. No test can therefore show the shipped rejection this "
                "criterion asks for, which is why it is not COVERED. It is a deferral rather "
                "than a gap because the thing that is missing is data, the decision to withhold "
                "that data is recorded here and on D1, and the condition that reverses it is "
                "written down below."
            ),
            deferral_trigger=(
                "config/organization.yaml records member_logins on a team, which happens once "
                "each group's lead confirms who is in theirs. Enforcement is per submitter and "
                "needs no code change: the supporting tests cited here already drive the denial "
                "against a bound member. When the first group lands, this criterion must be "
                "re-recorded as COVERED with those citations promoted to proving tests, or "
                "argued again."
            ),
            scope_limits=(
                (
                    "Attribution travels the whole path. RunManifest.team fills "
                    "RequestFacts.claimed_team, which is required rather than defaulted so a "
                    "caller cannot skip it, and every AuthorizationDecision records both the "
                    "claimed team and whether membership was verified. Three states are "
                    "distinguishable in the audit record: verified and correct (team_verified "
                    "true), verified and wrong (denied with submitter_not_in_claimed_team), and "
                    "not verifiable yet (team_verified false with an ordinary approval reason). "
                    "Every shipped decision today is the third state."
                ),
                (
                    "Attribution is checked against the submitter's own membership and nothing "
                    "else. It is independent of who approves, so a lead self-authorising and an "
                    "admin self-approving an exception are both refused a team they do not "
                    "belong to. It is also independent of the repository: RepositoryBinding "
                    "exists but no rule derives a team from it."
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
                "tests/test_fanout.py::test_parallelism_above_the_bound_is_an_exception_on_its_own",
                "tests/test_fanout.py::test_a_fanout_at_both_count_ceilings_stays_routine",
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
