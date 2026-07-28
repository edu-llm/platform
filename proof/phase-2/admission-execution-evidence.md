# Phase 2 admission execution evidence

Every execution `sbsandbox-intern-edullm-admission` has run, read from `fixtures/evidence/phase-2/executions.sanitized.json`. Sourced from the execution list rather than from CloudWatch, because execution history is guaranteed and log delivery is best-effort.

| execution name | status | error |
| --- | --- | --- |
| `run_019fa439-203e-70c7-bf8a-9ce33bc71f20` | FAILED | `States.Runtime` |
| `run_019fa446-8a4e-7094-9e29-d44fffbd2491` | SUCCEEDED | — |
| `run_019fa468-c9b5-706a-8849-87c1d0b5befb` | SUCCEEDED | — |
| `run_019fa46a-5478-70ea-aab6-28de23c41f7f` | SUCCEEDED | — |
| `run_019fa471-0173-7050-a41b-22ca01969b52` | SUCCEEDED | — |
| `run_019fa4c0-390d-7081-b539-08d9ff6b58be` | FAILED | `AdmissionRejected` |
| `tampered-probe-4965` | FAILED | `AdmissionRejected` |

**The name is the run id, and that is what makes the duplicate-name refusal mean something.** Step Functions answers a second `StartExecution` under a name that has already closed with `ExecutionAlreadyExists` for ninety days, so the name is a deduplication key rather than a label.

## Reading the failures

`AdmissionRejected` is the validator refusing a submission, and two executions carry it. Anything else is the machine itself failing, and the two mean very different things about whether admission worked: one execution failed with `States.Runtime`, once, before the handler and the state machine definition agreed on a payload shape.

A refusal that left no record would make a rejected submission indistinguishable from one nobody made. Each `AdmissionRejected` execution has an intent record and a decision record under its name, and the decision reads `manifest_hash_mismatch` with `accepted: false`. That join is shown in `lineage-record-evidence.md` rather than asserted here.

## What this document does not carry

- **The duplicate-name refusal itself.** Step Functions refused a second `StartExecution` under an existing name with `400 ExecutionAlreadyExists` on 2026-07-27, and the response was never captured. What is committed is the store it left behind, in which no run id appears twice -- which is the consequence rather than the refusal.
- **The execution ARNs and their histories.** The capture records names and terminal states. An ARN carries the account id, and `GetExecutionHistory` carries the submitted payload, so both need a projection designed for them rather than a scan afterwards.

| criterion | status today | what it is short of |
| --- | --- | --- |
| 12 | a gap | the `ExecutionAlreadyExists` response and the S3 412 beside it |
| 13 | covered | nothing -- the refused runs left committed decision records |
