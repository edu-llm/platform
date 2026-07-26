# Phase 1 deployed-role drift

Both Phase 1 roles were created once from a laptop and neither is redeployed by CI, so each committed template is a claim about the account rather than a description of it. `edullm_platform.role_drift` is what turns the claim back into something checkable, and `tools/capture_phase1_evidence.py` runs it against the live account as it captures.

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

Nothing. No captured role evidence is committed under `fixtures/evidence/phase-1/roles/`, so the comparison had nothing to run against. The machinery above is built and tested against synthetic roles derived from these templates; it has not been pointed at the account.

To change that: run `uv run python tools/capture_phase1_evidence.py --aws-profile <profile> --aws-region us-east-1 --environment sandbox --repository OLMo-core --output-dir docs-frank/working/phase-1-evidence/<date>`, read what it wrote, and copy the sanitized role records into `fixtures/evidence/phase-1/roles/`. Regenerate this bundle afterwards and this section will report the comparison.

A capture is a statement about one moment. Every evidence record stops loading thirty days after it was taken, so a committed capture expires and this section goes back to reporting nothing until somebody looks again. That is the intended behaviour and not a defect to work around.
