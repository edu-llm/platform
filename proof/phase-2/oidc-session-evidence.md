# Phase 2 OIDC session evidence

**This document is empty, and it is empty for one reason.** What it would describe has already happened, and nothing captured it. Phase 2's path went end to end on 2026-07-27, and its three roles were deployed from a laptop the same day; what `tools/capture_phase2_evidence.py` records is the state that left behind -- the lineage objects, the execution list, the GitHub configuration -- and not the artifact this document is for. It is generated empty rather than omitted because a bundle missing a document reads as a phase with fewer claims, and a reviewer counting what is here should count this too.

## What this document records

The CloudTrail AssumeRoleWithWebIdentity records for the accepted path and the refused path, each with the subject from responseElements.subjectFromWebIdentityToken, the audience and the provider.

## What would fill it

- The refused call is the one worth having and it has already happened on every live run. The `deny-unapproved` job sits in the submission workflow so that the environment is the only variable -- same repository, same workflow ref, same branch -- and it succeeded each time, meaning STS refused the ref-based subject with `AccessDenied`.
- The accepted call beside it, so the pair shows one subject admitted and the other refused rather than a refusal on its own.
- Retries designed around CloudTrail's documented fifteen-minute delivery window rather than the roughly three minutes Phase 1 happened to observe.

## Criteria waiting on it

| criterion | status today |
| --- | --- |
| 6 | a gap |
| 7 | a gap |

Each of those is recorded in `src/edullm_platform/phase2_criteria.py` with the same account of what is missing, and `uv run python tools/validate_phase2.py` reports it. This document and that definition are two views of one fact rather than two claims.
