# Phase 3 Batch execution evidence

What Batch says about each job this platform submitted, read back with `describe-jobs` and projected field by field rather than scanned afterwards -- a Batch job detail carries the full container command and environment, so a capture that sanitized by scanning would be one unrecognised field away from committing a workload's arguments.

The exit code column is the one that earns its place. A result record says a run failed; only the exit code separates a command that returned non-zero, which has one, from a job the scheduler killed, which does not.

| run | Batch job id | status | container exit | reason Batch gave |
| --- | --- | --- | --- | --- |
| `run_019fa73d-be37-7066-984b-a4bacf194f49` | `fde2fa08-a611-48dc-a0ef-1c6797147543` | FAILED | 3 | Essential container in task exited |
| `run_019fa96f-8f10-705a-a7a9-69c42eafce16` | `7505b42e-0c45-4600-9488-bab6474de3c1` | SUCCEEDED | 0 | Essential container in task exited |
| `run_019fa984-085c-7088-9c94-799e4b5d9126` | — | no job | — | refused before submission |
| `run_019fa9a6-4460-7095-a358-a1552e250f1b` | `56b43cb9-abcc-4f74-bbf5-6f61f12d1981` | FAILED | — | Job attempt duration exceeded timeout |

## The compute environment these ran on

Read from the deployed environment after every run above had finished. `desiredvCpus` is the reading that matters: `minvCpus` is what the template asks for and cannot catch an environment that scaled up and did not come back down.

| fact | value |
| --- | --- |
| compute environment | `sbsandbox-intern-edullm-cpu` |
| status | VALID, ENABLED |
| job queues routing to it | `sbsandbox-intern-edullm-cpu` |
| vCPUs, min / desired / max | 0 / 0 / 128 |
| observed | 2026-07-28 |
