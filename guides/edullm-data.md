# Validating a corpus with edullm-data

Checking each shard of a staged corpus against the bounds its family declares. Access, the form, the corpora, the run id, the environment and stopping a run are in [`the-platform.md`](the-platform.md).

> **The read grant is deployed.** The CPU workload role carries `s3:GetObject` and `s3:ListBucket` on both `edullm-landing` and `edullm-data`, in an inline policy named `read-the-dataset-airlock`. This banner used to say the grant was committed and not yet applied, and to expect `AccessDenied` before the first shard was checked. The committed capture of the deployed role, `fixtures/evidence/phase-3/roles/sbsandbox-intern-edullm-batch-workload.sanitized.json`, names that policy and `arn:aws:s3:::edullm-landing/*`, and it was read off the account at 2026-08-06T02:04Z. Read that file. Do not reach for `aws iam list-role-policies`, which most of us cannot run and which this guide used to tell you to.

> **`cpu-32vcpu` is not advice here, it is the only profile that works.** Only the CPU workload role reads `edullm-landing`. Every GPU profile runs as `sbsandbox-intern-edullm-batch-gpu-workload`, which reads the sealed `edullm-data` and not the landing zone, so a validator sent to a GPU queue fails on its first read at between $0.53 and $30.13 an hour.

## Prerequisites

- [ ] You have read the two notes above
- [ ] Your branch is named `edullm/…`. A merge to `main` builds nothing here, see below
- [ ] The build workflow has gone green on your commit
- [ ] The image has finished its security scan, a few minutes *after* the build goes green
- [ ] You have the full commit SHA (`git rev-parse HEAD`)

## Capabilities

| Capability | Available | Why |
| --- | --- | --- |
| Reading `edullm-landing` or `edullm-data` | Yes, on `cpu-32vcpu` | `s3:GetObject` on both, plus an unconditioned `s3:ListBucket` on each so the manifest walk and the `--prefix`-less discovery can see what is there. Deployed, and the capture named in the banner is the evidence |
| Reading `edullm-landing` on a GPU profile | No | The GPU workload role reads the sealed `edullm-data` and nothing in the landing zone, which is the one bucket the two workload roles deliberately differ on. A validator reads a candidate before it reads the published copy, so it fails on its first call |
| Writing `_VALIDATED.json` / `_REJECTED.json`, or `--promote` | No | Both land in the dataset project's buckets, and this role holds no write there at all. The promoter runs as a role of its own that no submitted job assumes |
| Writing under your run's prefix | Yes | `s3:PutObject` under `sbsandbox-intern-edullm-outputs/teams/*/runs/*` is the one write this role has |

## Building your image

```bash
git push -u origin edullm/my-validation   # watch "Build eduLLM research image" on Actions
git rev-parse HEAD                        # the commit you put on the form
```

| You push to | Result |
| --- | --- |
| `edullm/**` | Image built and published |
| **`main`** | **No image, and nothing warns you.** The push succeeds, the checks go green, and the commit is refused at submission long after the merge |
| Anything else | No image, and nothing warns you |
| Manual dispatch | Image built |

**Unlike OLMo-core, whose caller also lists `main`, this one builds on `edullm/**` and manual dispatch only.**

| | |
| --- | --- |
| What the build checks | The whole suite: the project's test extra into a throwaway virtualenv, then `pytest -q -p no:cacheprovider` over everything |
| Dockerfile and base | `.edullm/Dockerfile` on `docker.io/library/python` 3.12.13 pinned by digest, the same base OLMo-core registers. Not `infra/Dockerfile.validator`, whose `ENTRYPOINT` of `python -m edullm_data.validate --promote` would prepend the promoter to whatever you type |
| Tag and digest | The first twelve characters of the commit, and ECR refuses to overwrite a tag, so one commit is one image. Leave `image_digest` blank and it resolves from your commit |

**A green build is not the last step.** The registry scans every image it accepts, and a submission naming an image whose scan has not finished is routed to an admin rather than to your team lead. The summary on the run page says the scan was still running rather than naming a vulnerability. It takes a few minutes. Wait, then resubmit the same commit, and the run goes back to the lead gate.

## Workload profiles

| Profile | Limits | Use for |
| --- | --- | --- |
| `edullm-data-validate` | 1h, 1 attempt, no checkpoint | The only entry this repository has. Reading a corpus holds no state worth resuming |

Pick `cpu-32vcpu` for `compute_profile`, which is c7i.8xlarge at $1.428/hr. Validating a corpus is CPU work over S3 with no accelerator to ask for, and this entry used to say so by naming the profile. It cannot any more, because the form overrode whatever it named, so a GPU shape here is available to you. Unlike everything else in the catalog it does not merely cost more, it does not work. The landing-zone read lives on the CPU workload role alone.

## Running a validation

```
bash -lc 'edullm-data-validate --prefix pretrain/olmo-127b/v1'
```

There is no `ENTRYPOINT` and no `CMD`, so the script name comes first; `bash -lc` is required, for the reason [`the-platform.md`](the-platform.md) gives.

| Flag | Default | What it does |
| --- | --- | --- |
| `--landing-bucket`, `--data-bucket` | `edullm-landing`, `edullm-data` | Where staged datasets are read from, and the sealed library they are checked against and promoted into. Both are the dataset project's own; neither is the platform's output bucket |
| `--prefix` | omitted | One `<dataset_id>/<version>`, the part of a corpus URI after the bucket, so `pretrain/olmo-127b/v1` names what the form calls `olmo-127b-v1`. Omitted, it discovers what is pending |
| `--promote`, `--promote-workers` | off, 1 | Copy a passing dataset into the sealed library, on that many threads for the copy and CRC loops |
| `--now` | none | An ISO-8601 timestamp to stamp markers with |

## Required configuration

| Setting | Value | Why | Basis |
| --- | --- | --- | --- |
| `compute_profile` | `cpu-32vcpu` | The landing-zone read is on the CPU workload role and on no other. A GPU profile fails with `AccessDenied` on the first read rather than merely costing more | configuration |
| Source-bucket reads | Granted and deployed | `read-the-dataset-airlock` in `infra/iam/batch-roles.yaml` grants `s3:GetObject` and `s3:ListBucket` on both buckets, and the committed capture of the deployed role carries it. An `AccessDenied` here is now worth reporting rather than expecting | evidence |
| `--prefix` | Set it | With no prefix and nothing pending, the script prints `no pending datasets` and returns zero, which Batch and the record both read as a success. Read the log, not the exit code | configuration |
| `--promote` | Leave off | It writes, and this profile's bounds are a read's bounds. At `--promote-workers 1` promotion is roughly two S3 round-trips per object, and `olmo-150b-dolma2-v1` is 6,851 objects | configuration |

## Output locations

| Variable | Path | Contents |
| --- | --- | --- |
| `$EDULLM_OUTPUT_PREFIX` | `teams/{team}/runs/{run id}/` | Everything the run writes |

## Support

If you are first through this path, record what you typed and what came back. A failure is worth recording as carefully as a success. Everything else is in [`the-platform.md`](the-platform.md).
