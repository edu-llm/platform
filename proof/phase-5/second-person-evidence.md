# Phase 5 second-person evidence

Three runs, submitted by `aryanjverma` and released by `pianomaster99`. Rendered from the captures committed under `fixtures/evidence/phase-5/runs/`, read through the same module the tests read them through.

**Why this is the whole phase.** Every accepted decision record written before these read `routine_self_authorized` or `exception_self_approved_by_admin`. Both are granted authorizations and neither is evidence about the path a member takes, because in both the person who approved is the person who submitted. Twenty-five dispatches produced twenty-five of them. The reason code below, `routine_approved_by_lead_or_admin`, is what the entire two-person approval design exists to produce, and it had never been written.

## Authorization, as the decision records carry it

| run | submitter | approver | reason | claimed team | team verified |
| --- | --- | --- | --- | --- | --- |
| run_019fb4ce-cf24-7028-8eed-a32a28ec2493 | aryanjverma | pianomaster99 | routine_approved_by_lead_or_admin | tokenizer | no |
| run_019fb4f6-6679-708d-9bee-1ef5ccf5a002 | aryanjverma | pianomaster99 | routine_approved_by_lead_or_admin | tokenizer | no |
| run_019fb505-9b0f-70cc-b890-2c60037cfe41 | aryanjverma | pianomaster99 | routine_approved_by_lead_or_admin | tokenizer | no |

Every one of them was released by somebody other than their submitter, which is what check 2 is and is why it is covered. It also closes Phase 2's criterion 3 -- any team lead approval succeeding while `approval_scope` is `organization` -- which could not be closed by writing code and has been open across every submission this platform has ever taken.

**`team verified` is `no` on every row, and that is correct rather than a defect.** The team a submitter claims is recorded and not enforced: nothing binds a team to a person yet, which is Phase 6 item 6.5. A record reading `yes` here would be evidence for a control that does not exist. The pilot limitations page tells a user the same thing in the same words -- `team` routes approval rather than granting permission.

## What each run did

| run | profile | scheduler | exit | recorded states | result |
| --- | --- | --- | --- | --- | --- |
| run_019fb4ce-cf24-7028-8eed-a32a28ec2493 | cpu-32vcpu | FAILED | — | runnable, runnable, failed | no result record |
| run_019fb4f6-6679-708d-9bee-1ef5ccf5a002 | cpu-32vcpu | SUCCEEDED | 0 | runnable, runnable, running, succeeded | succeeded |
| run_019fb505-9b0f-70cc-b890-2c60037cfe41 | cpu-32vcpu | FAILED | 1 | runnable, runnable, running, failed | failed |

**The failures are committed deliberately.** A phase whose evidence is only its successes is a phase that has not been tested, and each of these two failed in a way worth keeping.

- The run with no result record was admitted, submitted to Batch, and died on an instance resolving its entire command line against `$PATH` -- the submitter's shell quoting survived into the form field, so `shlex.split` returned one token and the whole line became argv[0]. Its states read `runnable, runnable, failed`, which is what a container that never started looks like from outside, and the absence of a result record is correct rather than missing evidence. The contract now refuses a first element that is empty or carries whitespace or a quote, so the refusal lands at compile ahead of the approval gate rather than on a warm instance after a lead has read it.
- The run that exited 1 did so on `No API key configured`. Its command logged to Weights and Biases, and `CONTAINER_SHAPES['cpu-32vcpu']` declares `secrets=()` while `gpu-1xa10g` names the W&B secret -- so no run on the CPU profile has ever been able to authenticate. This is a finding rather than a user error, and it is recorded here rather than closed because closing it widens a grant.

## What the digest that ran establishes

Check 4 is covered and it is the one that most repays reading the method rather than the verdict. The digest is compared against `container.image` in the scheduler's own description of the job. Before this phase, `batch_submit_request` built `ContainerOverrides` with a command and an environment and no image, so the container that ran was whatever the CloudFormation job definition said, while the digest a submitter typed was validated, gated admission through the ECR scan, and written immutably into lineage. The two coincided only because the exception file happened to contain exactly those digests -- which made the lineage record's image provenance true by convention. Reading the digest back out of the template would have proved the convention.

Each of these runs was submitted against a job definition registered for it and named after it, which is the mechanism that makes the digest selectable at all. A shared definition pins one image for every run, so a matching digest would be a coincidence rather than a property.
