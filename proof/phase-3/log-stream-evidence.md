# Phase 3 log stream evidence

The stream each job recorded, fetched back and returning the line its container printed. The stream and not the group: a group name reads as complete and resolves to every job on the queue, so a record carrying one looks healthy and locates nothing.

The lines are reproduced here because these are smoke commands whose output this repository wrote. That is a deliberate exception to D8's rule that references travel rather than contents, and it does not generalise: a research workload's stdout is the least predictable text this platform handles and belongs behind a reference.

## run_019fa73d-be37-7066-984b-a4bacf194f49

| fact | value |
| --- | --- |
| log group | `/aws/batch/sbsandbox-intern-edullm-cpu` |
| log stream | `cpu-run/default/462480fa644f4112a85e292d07b0d3b6` |
| lines retrieved | 1 |
| truncated | no |

```
edullm deliberate failure
```

## run_019fa96f-8f10-705a-a7a9-69c42eafce16

| fact | value |
| --- | --- |
| log group | `/aws/batch/sbsandbox-intern-edullm-cpu` |
| log stream | `cpu-run/default/d64383e765184601b7c5bcf80a9de736` |
| lines retrieved | 1 |
| truncated | no |

```
edullm smoke ok 3.12.13 (main, Jul 14 2026, 02:09:00) [GCC 14.2.0]
```

## run_019fa984-085c-7088-9c94-799e4b5d9126

Refused before submission, so no container ran and no stream exists.

## run_019fa9a6-4460-7095-a358-a1552e250f1b

| fact | value |
| --- | --- |
| log group | `/aws/batch/sbsandbox-intern-edullm-cpu` |
| log stream | `cpu-run/default/981f34c19cad4efca2de0c41a514590d` |
| lines retrieved | 1 |
| truncated | no |

```
edullm runaway begins
```

