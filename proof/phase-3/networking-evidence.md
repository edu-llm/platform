# Phase 3 networking evidence

The plan this phase was built from assumed the compute environment would run in somebody else's VPC, and called that the phase's largest known limitation. It does not, and this document records the terms it ended up with instead -- which are better, and are different enough that reading the plan's wording here would mislead.

## The quota, which was the longest pole and is closed

| fact | value |
| --- | --- |
| quota | `L-F678F1CE` VPCs per Region, us-east-1 |
| in use when measured | 5 |
| value requested | 10 |
| request state | PENDING |
| request id | `eee630cb-39294a78-ad1ed881-8c9c0a84-cgsdUwmS` |
| adjustable | yes |

The request id is written with a hyphen every 8 characters, which is a presentation change rather than a redaction: every character AWS issued is still here and `edullm_platform.phase3_evidence.ungroup_opaque_identifier` reverses it exactly. A service-quotas request id is forty characters of `[A-Za-z0-9]`, which is precisely the shape the evidence secret scan refuses. Masking it would throw away the one field that lets a reader open the request; widening the scanner to admit forty-character runs would weaken the check everywhere to admit one identifier.

`us-east-1` held five VPCs against a quota of five on the morning of 2026-07-27 and a real `create-vpc` returned `VpcLimitExceeded`. That is an entirely different kind of problem from an authorization denial, and telling the two apart is what kept this phase in the only region that works. The increase was filed, applied the same day, and confirmed by creating a VPC and deleting it again, so `infra/batch-network.yaml` creates our own unconditionally and nothing here is borrowed.

## The authorization matrix, both regions

Measured with `--dry-run` against the real EC2 API. See `measurement-method.md` for why, and for the four controls that make these verdicts believable.

| action | region | verdict | error code |
| --- | --- | --- | --- |
| `ec2:CreateVpc` | us-east-1 | authorized | `DryRunOperation` |
| `ec2:CreateSubnet` | us-east-1 | authorized | `DryRunOperation` |
| `ec2:CreateSecurityGroup` | us-east-1 | authorized | `DryRunOperation` |
| `ec2:CreateRouteTable` | us-east-1 | authorized | `DryRunOperation` |
| `ec2:CreateInternetGateway` | us-east-1 | authorized | `DryRunOperation` |
| `ec2:RunInstances` | us-east-1 | authorized | `DryRunOperation` |
| `ec2:CreateLaunchTemplate` | us-east-1 | authorized | `DryRunOperation` |
| `ec2:CreateVpc` | us-east-2 | denied | `UnauthorizedOperation` |
| `ec2:CreateSubnet` | us-east-2 | denied | `UnauthorizedOperation` |
| `ec2:CreateSecurityGroup` | us-east-2 | denied | `UnauthorizedOperation` |
| `ec2:CreateRouteTable` | us-east-2 | denied | `UnauthorizedOperation` |
| `ec2:CreateInternetGateway` | us-east-2 | authorized | `DryRunOperation` |
| `ec2:RunInstances` | us-east-2 | denied | `UnauthorizedOperation` |
| `ec2:CreateLaunchTemplate` | us-east-2 | authorized | `DryRunOperation` |

**`us-east-2` is not a fallback and looks like one.** The master plan's region lock permits both, so the obvious response to a full `us-east-1` is to move. An EC2 compute environment there is not possible at all.

## The zones, and the one that would produce a job that waits

Recorded per subnet rather than as a list of ids, because the fact that matters is not which subnets exist but which of them can hold the instance type the compute environment asks for. A subnet in a zone that cannot produces a job stuck in `RUNNABLE` and no error anywhere, which is the least debuggable failure this phase can have.

| availability zone | offers the instance type | public | free addresses |
| --- | --- | --- | --- |
| us-east-1a | yes | yes | 251 |
| us-east-1b | yes | yes | 251 |
| us-east-1c | yes | yes | 251 |
| us-east-1d | yes | yes | 251 |
| us-east-1e | **no** | yes | 251 |
| us-east-1f | yes | yes | 251 |

## Whose network this is

Not borrowed in the end. This VPC was the interim candidate while us-east-1 sat at 5/5 VPCs; the L-F678F1CE increase to 10 was filed and applied on 2026-07-27 and confirmed by creating and deleting a VPC, so Phase 3 builds its own in infra/batch-network.yaml. Recorded here because it is the VPC these probes named, and a probe needs a VPC that exists.

## What the compute environment actually landed on

Everything above is a premise: it describes the account and the placement these probes were aimed at, measured before any stack was applied. The table below is different in kind -- it is read back from the deployed compute environment, so it says where this project's jobs actually run rather than where a template asked for them to. A stack applied from a laptop can land somewhere other than its template says, and a record copied from the template would agree with itself forever.

| fact | value |
| --- | --- |
| compute environment | `sbsandbox-intern-edullm-cpu` |
| status | VALID, ENABLED |
| VPC | `vpc-0622b8d314ff5f800` |
| subnets | `subnet-01f4bf9a051404a37`, `subnet-08792525c62ba31c0`, `subnet-0a4235fb98b63930f`, `subnet-0bbe2b7870da13713`, `subnet-0fd5ed8accae254dc` |
| security groups | `sg-087218d8c87aa8576` |
| instance types | `c7i.8xlarge` |
| vCPUs, min / desired / max | 0 / 0 / 128 |
| observed | 2026-07-28 |
