# Phase 1 deployed-role drift

Both Phase 1 roles were created once from a laptop and neither is redeployed by CI, so each committed template began as a claim about the account rather than a description of it. `edullm_platform.role_drift` is what turns the claim back into something checkable, `tools/capture_phase1_evidence.py` runs it against the live account as it captures, and the sanitized records it wrote are committed under `fixtures/evidence/phase-1/roles/` so the comparison can be re-run by anybody, with no credentials, as often as the suite runs.

## The roles compared

| role | template | inline policies | max session (s) |
| --- | --- | --- | --- |
| sbsandbox-intern-edullm-ecr-publisher | `infra/iam/ecr-publisher-role.yaml` | 1 | 3600 |
| sbsandbox-intern-edullm-infra-deployer | `infra/iam/infra-deployer-role.yaml` | 1 | 3600 |

## What is reported, and in which direction

A deployed role that grants **more** than its template is a security finding. One that grants **less** is not — it is a role that will refuse a push nobody expected it to refuse. Only the first is dangerous and both mean the committed template has stopped describing the account, so every finding carries a direction and none of them passes silently.

| direction | means |
| --- | --- |
| `wider` | the deployed role grants something the template does not |
| `narrower` | the template grants something the deployed role does not |
| `changed` | a difference with no direction: an edited condition value, a renamed boundary, a statement selecting by exclusion where the template selects by inclusion |

## The normalisation, and what it cannot hide

A template spells a resource `arn:${AWS::Partition}:ecr:${AWS::Region}:${AWS::AccountId}:repository/x` and the account returns it expanded, with the account then masked by capture. Reconciling the two is the one place a comparison could quietly make a wider role look identical to a narrower one, so the folding is deliberately mean:

- It is positional. An ARN is split into its six fields — and split on colons *outside* a `${…}`, because `${AWS::Partition}` contains two of them — and only the partition, region and account fields are ever touched.
- It is exact. A field folds only when it holds precisely the pseudo-parameter, or precisely `aws` and `us-east-1`, which the caller names. Another region, another partition, any wildcard, and every character of the resource survive untouched and are still compared.
- It distinguishes accounts. Capture masks this account and any other account to different placeholders, and only the former folds, so a grant pointing at somebody else's account is reported rather than absorbed.
- It refuses what it does not understand. A substitution that is not one of those three raises rather than being guessed at or compared as a literal.

## What this comparison does not see

- Statement order. IAM evaluates a document's statements as a set, so a reordered document grants exactly what the template grants and produces no finding. Every other difference does.
- Wildcard containment. `repository/*` and `repository/x` are reported as one resource gained and one lost rather than as one being wider than the other. Reasoning about IAM's wildcard semantics is where a comparison gets quietly wrong, and being wrong here is worse than being blunt.
- Anything the boundary denies. `InternSandboxBoundary` is an account policy this repository does not own; the comparison records that a role is bounded by it and says nothing about what it permits.
- Everything outside the projection: role tags, description, path, role id, creation and last-used dates. None of it is comparable to a template this repository commits.

## What this bundle compared

One capture per role, taken against the sandbox and committed after review. The generator refuses to write at all if any of them has expired, drifted or stopped loading, so this table can only ever report agreement — the interesting states are reported by the refusal instead.

| role | observed | matches its template | findings | expires |
| --- | --- | --- | --- | --- |
| sbsandbox-intern-edullm-ecr-publisher | 2026-07-26 | yes | 0 | 2026-08-25 |
| sbsandbox-intern-edullm-infra-deployer | 2026-07-26 | yes | 0 | 2026-08-25 |

**Expires** is thirty days after the observation, and it is not a formality. Every Phase 1 evidence record refuses to load past it, so on that date `tests/test_phase1_deployed_roles.py` goes red, every criterion resting on it reverts with reason `cited_test_failed`, `tools/validate_phase1.py` exits 1, and this bundle stops building. Nothing about the roles will have changed; what will have lapsed is anybody's knowledge of them. The two honest responses are to re-capture, or to delete the records and remove the citations resting on them, which is a decision somebody takes in writing.
