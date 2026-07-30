# A guided walkthrough of this repository

Written for somebody who has never seen this codebase and wants to be able to explain it
to somebody else. It goes end to end, one piece at a time, and each lesson ends with a
paragraph you could say out loud to a colleague.

The lessons build on each other and are best read in order. If you would rather have the
nouns before the machinery, read Lesson 3 first — it is the glossary, and nothing in it
depends on the two lessons before it.

Two sidebars sit between the lessons. They are not detours: both answer a question that
comes up the moment somebody looks closely at the submission path, and both are about
places where this system is honest about a limit rather than papering over it.

---

## Orientation: what this repository is

A research group wants to run compute jobs — CPU and GPU — on AWS. This repository is the
gatekeeper for those jobs. It answers three questions and then proves it answered them:

1. **What is a valid run?** Written down as contracts in `src/edullm_platform/contracts/`.
2. **Who may approve it, and with what?** Written down as reviewed YAML in `config/`.
3. **Did the rules actually hold in the real account?** Captured as sanitized evidence in
   `fixtures/evidence/`, and graded by the phase gates.

The sentence that ties it together is in the top-level README: *the library is the single
implementation*. The GitHub workflow that submits a job, the AWS Lambda that admits it, and
the test gates that grade the whole thing all import the same Python modules. Nobody
re-implements the rules in a second place where they can drift.

Rough scale: about 19,000 lines of Python across ~50 modules, 96 test files, 12
CloudFormation templates, 6 GitHub workflows.

| Path | Contents |
| --- | --- |
| `src/edullm_platform/` | The validation library — contracts, hashing, config loading, evidence models, acceptance gates |
| `config/` | Reviewed bindings — roster, approval policy, repository registry, workload and compute catalog, execution targets |
| `infra/` | CloudFormation for everything deployed, plus the runbook for procedures that need a laptop |
| `.github/workflows/` | Publishing an image, and submitting a run for admission |
| `fixtures/` | Representative run manifests, and sanitized captures of what the account did |
| `schemas/` | JSON Schemas generated from the models |
| `proof/` | Phase acceptance bundles — what a reviewer reads instead of the test suite |
| `tools/` | Maintainer scripts, run by hand |
| `tests/` | The suite that keeps all of the above honest |

---

## Lesson 1 — Contracts

### The problem that makes contracts necessary

A request to run a job travels a long way: through a GitHub workflow, an approval step, into
AWS, through a Lambda, into a job queue, and finally onto a machine with a GPU. At every
handoff the request is just **text** — JSON in a file, JSON in an HTTP body, JSON in an S3
object.

Text has no opinions. Nothing about a blob of JSON stops it from saying
`"gpu_count": "banana"`. So each stage has a choice: trust the text, or check it. If every
stage writes its own checking code, those checks drift apart, and eventually stage 3 accepts
something stage 5 chokes on.

A **contract** is the answer: one written-down definition of what a valid thing looks like,
that every stage imports and checks against. Not a document describing the shape — executable
code, so it cannot fall out of date with reality the way a wiki page does.

### What Pydantic is

Pydantic is a Python library for exactly this. You describe the shape of your data as a
class, and validation is generated from it:

```python
from pydantic import BaseModel

class Workload(BaseModel):
    name: str
    gpu_count: int
```

`Workload(**some_json)` either hands back an object where `name` really is a string and
`gpu_count` really is a whole number, or raises an error naming the field that was wrong.

Two more things this repository leans on: **serialization** (turning the object back into
JSON, which matters because these things travel between systems constantly) and **JSON
Schema export** (which is where the generated `schemas/` directory comes from).

Pydantic's defaults are friendly. It will happily convert the string `"3"` into the integer
`3`. Hold that thought.

### What this repository does with it

Every contract inherits from one base class, and that class turns nearly all of Pydantic's
friendliness off:

```python
# src/edullm_platform/contracts/base.py
class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )
```

| Setting | Default behaviour | What it becomes here | Why |
| --- | --- | --- | --- |
| `extra="forbid"` | Unknown fields ignored | Unknown fields rejected | A typo like `gpu_cont` would otherwise pass as "field not provided" |
| `frozen=True` | Objects mutable | Immutable after construction | Nothing can validate an object and then quietly modify it |
| `strict=True` | Types coerced | No coercion; `"3"` is not `3` | A wrong type means something upstream is broken — do not paper over it |
| `validate_default=True` | Defaults unchecked | Defaults validated too | Nobody passes a default in, so nobody notices when it is invalid |

The pattern across all four: **prefer a loud failure now over a quiet wrong answer later.**
That is the temperament of the whole codebase and the single most useful thing to understand
about it.

### The surprising part

Numbers in these contracts are strings.

Anything decimal is declared `StrictDecimal`, which accepts a Python `Decimal` or a base-ten
string and rejects a float outright. Timestamps get the same treatment: `UtcTimestamp`
demands exactly six digits of sub-second precision and a literal `Z`.

This looks like paranoia until you connect it to hashing, which is Lesson 2. A float has
multiple valid spellings; a timestamp can be written `+00:00` or `Z`, with three decimals or
six. Every variation is the same value and a different hash.

The rule is **one value, one spelling, always**, and every constraint in `base.py` serves it.

The same instinct appears in `contracts/identity.py`: run identifiers are not plain UUIDs but
UUIDv7 with a `run_` prefix. UUIDv7 embeds a timestamp in its leading bits, so the ids sort
chronologically on their own, and the prefix means passing a run id where an attempt id
belongs fails a regex rather than causing confusion three systems downstream.

> **Say it out loud.** The contracts are the system's vocabulary — manifest, workload, image,
> policy, execution. They are Pydantic models, so the shape of the data is a Python class and
> the validation comes from it automatically. But Pydantic is locked down here: unknown fields
> are rejected, objects are frozen, types are not coerced, and decimals are strings rather than
> floats. That last one is because this system hashes everything to detect tampering, and a
> hash only means something if each value has exactly one way of being written down.

---

## Lesson 2 — Hashing, canonicalization, and loading config

### What a hash function is

A hash function takes an input of any size and returns a fixed-size fingerprint. SHA-256
always returns 256 bits — 64 hex characters — whether the input is one letter or a
two-gigabyte file.

Four properties make it useful:

- **Deterministic.** Same input, same fingerprint, on any machine, forever.
- **Avalanche.** Change one bit and about half the output bits flip. There is no "close"
  hash; two hashes match exactly or tell you nothing.
- **One-way.** You cannot work backward to the input. A hash is not encryption (nothing
  decrypts) and not compression (the data is genuinely gone).
- **Collision-resistant.** Nobody has ever found two inputs producing the same SHA-256 output.

### What you do with that

A hash is a **tamper-evident seal**. An approver signs off on a manifest; you record its
hash — 64 characters. Later, before the job burns real GPU hours, you re-hash and compare.
Match means byte-for-byte the approved document. Mismatch means something changed in transit,
and you refuse.

Note what you avoided: storing a copy, diffing field by field, or trusting the systems that
carried it. This is also how container image digests work — `sha256:abc123…` is an
unambiguous instruction in a way `:latest` never is.

### The catch

Hashes operate on **bytes**, not meaning. These are the same data and completely different
hashes:

```json
{"name": "train", "gpus": 4}
{"gpus":4,"name":"train"}
```

So a seal only works if the data has exactly one byte representation. Producing it is called
**canonicalization**, and it is the whole of `src/edullm_platform/canonical.py`:

```python
def canonical_json_bytes(model: ContractModel) -> bytes:
    payload = model.model_dump(mode="json", by_alias=True, exclude_none=False)
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_digest(model: ContractModel) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(model)).hexdigest()}"
```

Nineteen lines, and the whole repository rests on them. Every argument closes one way the
same data could be written twice:

| Choice | Ambiguity removed |
| --- | --- |
| `mode="json"` | Python types become JSON types; where `Decimal` and `datetime` hit the strict serializers |
| `by_alias=True` | Always the wire name, never the Python attribute name |
| `exclude_none=False` | A null is always present as `null`, never dropped |
| `ensure_ascii=False` | Real UTF-8, never `\uXXXX` escapes |
| `allow_nan=False` | Rejects `NaN` and `Infinity`, which Python emits and no other JSON parser accepts |
| `sort_keys=True` | Alphabetical, so insertion order cannot leak into the bytes |
| `separators=(",", ":")` | Zero whitespace; the default would insert `", "` and `": "` |
| `.encode("utf-8")` | An encoding chosen explicitly rather than inherited from the environment |

Lesson 1 should now click. Decimals-as-strings and six-digit timestamps exist because
`base.py` and `canonical.py` are **one design split across two files**. The contracts give
each value one spelling; the canonicalizer gives the document assembling those values one
spelling. Neither works alone.

The digest is emitted as `sha256:abc…` rather than bare hex — self-describing, matching the
OCI convention, and exactly the pattern `Sha256Digest` validates back in `base.py`.

### Loading config

`src/edullm_platform/config.py` reads a YAML file and returns a validated contract. You hand
it a path and a model class; you get an instance or an exception. There is no third outcome
where you get an unvalidated dict.

Two defensive choices.

**`SafeLoader` rather than PyYAML's default.** YAML is a much larger language than most
people realise, and the full loader can construct arbitrary Python objects from a document —
a well-known code-execution path. `SafeLoader` builds only plain data.

**A duplicate-key check**, which is why the loader is subclassed at all. Standard YAML
accepts this silently, last-one-wins:

```yaml
approvers: [alice]
approvers: [mallory]
```

Recall what these files are. `organization.yaml` says who works here; `policy.yaml` says who
may approve what. They are **reviewed** files. A human reads top to bottom and sees `alice`;
the machine takes `mallory`. The review and the enforcement disagree, and nothing reports it.
So the loader raises `duplicate mapping key` instead.

> **Say it out loud.** A hash is a fixed-length fingerprint — same input gives the same one,
> any change gives a totally different one, and you cannot reverse it. That makes it a
> tamper-evident seal: hash the manifest at approval, re-check before the job runs, and any
> change shows up. But hashing works on raw bytes, so `{"a":1,"b":2}` and `{"b":2, "a":1}` seal
> differently despite being identical data. `canonical.py` fixes that with sorted keys, no
> whitespace, explicit UTF-8 and no floats, so a document has exactly one byte sequence. That
> is why the contracts are so strict about spelling. And `config.py` is the front door for the
> reviewed YAML: it loads safely, rejects duplicate keys so a reviewer and the machine cannot
> read the same file two ways, and returns a validated contract or nothing.

---

## Lesson 3 — The glossary

### What physically happens

Strip away the software and here is the event this repository governs:

> A researcher wants to run a program — tokenizing a corpus, or training a language model.
> That program lives in a **git repository**. It needs a specific software environment
> (Python version, CUDA drivers, libraries), packaged as a **container image**. It needs
> **data** to read. It needs **hardware**, for some number of hours, which costs real money.
> And somebody with authority has to **approve** spending that money before it is spent.

Every noun in the codebase is one of those things, written down precisely.

### The nouns, in the order they appear in a request

**Repository** — a GitHub repo holding research code. Here, `OLMo-core` (model training) and
`edullm-data` (data processing). Not any repo: only ones listed in `config/repositories.yaml`,
which is where a repo gets assigned its container registry and base image. An unlisted repo
cannot produce a runnable image, so naming one is refused outright.

**Commit SHA** — the exact 40-hex-character git commit to run. Not a branch, not a tag.
Branches move; a commit never does. The contract enforces `^[0-9a-f]{40}$`, which makes "run
whatever is on main" literally unexpressible.

**Image** and **image digest** — a container image is a frozen filesystem plus the config to
start a process in it: OS, Python, CUDA, dependencies, all pinned. It is how you get the same
environment on a laptop and on a GPU node. The digest is the SHA-256 of its content. Tags
like `:latest` are mutable and therefore banned; only the digest identifies an image that
cannot be swapped underneath you.

**Image scan** — images are scanned for known vulnerabilities, and the result becomes a fact
the policy consults. The field carries a comment worth reading:

```python
#: Whether this image's scan findings have been seen: clean of the severities policy
#: blocks on, or carrying a recorded exception. Required rather than defaulted, and
#: deliberately so -- a security fact with a default is a security fact that is true
#: whenever somebody forgets [...]
image_scan_reviewed: bool
```

**Dataset release** — a named, versioned snapshot of data, like `dolma-2026-07`. Versioned
for the same reason as everything else: "the dataset" changes, `dolma-2026-07` does not.

**Team** — which group of humans owns this run. Drives who may approve it and which S3 paths
it may write to.

**Compute profile** — a named bundle of hardware *and its price*. `cpu-32vcpu` means a
`c7i.8xlarge`, one node, at a known dollars-per-hour. Two fields are unusually honest:
`pricing_source` and `pricing_observed_at` record where the price came from and when somebody
looked, because a hardcoded price with no provenance silently rots. And `provisioned` is the
difference between "we have a price for this" and "this hardware exists in the account" —
there is a dedicated `UnprovisionedComputeProfileError` for exactly that case.

**Workload profile** — a registered *kind of job*: this repo, on that compute profile, for at
most this long, with at most this many retries. The point of the indirection is that a request
names a profile rather than describing hardware freehand, so what can be asked for is a menu
somebody reviewed rather than an open text field.

**Maximum runtime hours / maximum attempts** — the cost bounds. Runtime is the per-attempt
timeout and, as the sidebar below explains, is the only thing that stops a runaway job.

**Checkpoint** — periodically saving training state to S3 so a crashed job resumes instead of
restarting. A rule links it to retries, enforced on both the manifest and the workload
profile: if you allow retries, you must checkpoint. Otherwise a retry means paying for the
same hours twice from scratch, which is worse than not retrying and easy to configure by
accident.

**Fan-out** — running the same job N times with a varying index, for sweeps or multi-seed
runs. Size at least 2; parallelism cannot exceed size.

**Run** and **attempt** — a *run* is the logical thing that was approved (`run_0198…`). An
*attempt* is one execution of it (`att_0198…`). One run, up to `maximum_attempts` attempts.

**W&B project** — Weights & Biases, a training-metrics dashboard. The manifest names which
project the run reports into.

### The manifest

A **run manifest** is the complete, self-contained description of one job, and it is the
central noun of the system:

```python
class RunManifest(ContractModel):
    schema_version: Literal[1]
    repository: str
    commit_sha: str            # 40 hex characters
    image_digest: str          # sha256:...
    dataset_release: str
    command: tuple[str, ...]
    team: str
    wandb_project: str
    workload_profile: str
    compute_profile: str
    maximum_runtime_hours: PositiveStrictDecimal
    maximum_attempts: int
    checkpoint: CheckpointContract | None
    fanout: FanOut | None = None
```

This is the thing that gets hashed, the thing an approver approves, and the thing that travels
to AWS. Read it as answering: *what code, at what version, in what environment, on what data,
on what hardware, for whom, for how long, at what cost ceiling.*

A real one lives at `fixtures/manifests/cpu-routine.yaml`. Note `maximum_runtime_hours: "2"`
in quotes — that is `StrictDecimal` from Lesson 1, visible in the wild.

### Policy

The **approval policy** is the reviewed rulebook, and it sorts every request into one of two
**approval classes**:

- **Routine** — within all the normal bounds. One approver in the routine role.
- **Exception** — over a threshold or missing a guarantee. Needs a stronger approver.

The sorting function is one boolean expression, in `contracts/policy.py`:

```python
def classify_request(facts, thresholds) -> ApprovalClass:
    if (
        facts.repository_registered
        and facts.dataset_registered
        and facts.compute_profile_registered
        and facts.immutable_revision
        and facts.immutable_image
        and facts.image_scan_reviewed
        and facts.estimated_cost_usd <= thresholds.routine_maximum_cost_usd
        and facts.maximum_runtime_hours <= thresholds.routine_maximum_runtime_hours
        and facts.maximum_attempts <= thresholds.routine_maximum_attempts
        and facts.fanout_size <= thresholds.routine_maximum_fanout_size
        and facts.fanout_parallelism <= thresholds.routine_maximum_parallelism
    ):
        return ApprovalClass.ROUTINE
    return ApprovalClass.EXCEPTION
```

Everything must be true for routine; anything else falls through to exception. The direction
of the default matters — unusual requests get *more* scrutiny automatically, never less.

Three related ideas sit alongside:

**`denied_outright`** — conditions no approver can override. Unregistered repository,
unregistered dataset, unregistered compute profile, mutable revision, mutable image reference,
unreviewed scan findings. Not "needs a bigger signature," but "no."

**A structural rule about approvers** — the routine approver role may not appear in the
exception roles list. Otherwise "exception approval" would be a label with no extra authority
behind it and the two-tier design would be decorative. The contract makes that
misconfiguration impossible to write.

**`policy_version`** — a monotonic `v1`, `v2`, `v3`, deliberately not a date. A decision record
naming only its outcome becomes uninterpretable once thresholds move, and two amendments on one
day are ordinary while two colliding dates are not orderable.

### The rest

| Term | Meaning |
| --- | --- |
| **Admission** | The gate inside AWS. Last checkpoint before real resources are consumed |
| **Lineage record** | The immutable audit trail: what was approved, by whom, under which policy version, at what permitted cost |
| **Execution** | The job actually running on AWS Batch |
| **Lifecycle** | The state a run moves through — submitted, runnable, running, succeeded, failed, cancelled |
| **Evidence** | Sanitized read-only observations of the real account, committed as proof the rules held |
| **Denial** | A recorded refusal. Refusals are first-class here — roughly 2,400 lines across three modules |
| **Job type** | corpus preprocessing, tokenizer training, model pretraining, fine-tuning, evaluation, batch inference |
| **Data classification** | public / internal / restricted |
| **Retention class** | transient / standard / long-lived / permanent |

> **Say it out loud.** A manifest is one job request, complete in a single document: which
> repo, at which exact commit, in which container image by digest, on which dataset release,
> running which command, for which team, on which compute profile, with a runtime and retry
> ceiling. Everything that could drift is pinned to something immutable, so the thing that runs
> is provably the thing that was approved. Compute profiles and workload profiles are
> pre-reviewed menu items, so a researcher picks from a list rather than describing hardware
> freehand. The policy sorts each manifest into routine or exception; a few conditions are
> denied outright and no approver can override them. Once approved, the manifest is hashed so
> admission can confirm that what is about to consume GPU hours is byte-for-byte what was
> signed off.

---

## Lesson 4 — `config/`, the declared world

The code in `src/` holds the **rules**. `config/` holds the **facts** — seven small YAML
files, about 21 KB in total, that a human reviews in a pull request. Change a rule and you
change behaviour for everyone; change a fact and you have made one declaration. Separating
them means most decisions are a reviewable data diff rather than a code change.

Every one of these goes through `load_yaml()` from Lesson 2, so each is checked against a
contract on the way in. There is no unvalidated config in this system.

| File | Declares |
| --- | --- |
| `organization.yaml` | Who works here, who are admins, who are team leads |
| `policy.yaml` | Approval thresholds and what is denied outright |
| `repositories.yaml` | Which repos may submit, and their base images |
| `workload-catalog.yaml` | The compute and workload profile menu |
| `datasets.yaml` | Registered dataset releases |
| `execution-targets.yaml` | Where jobs actually land in AWS |
| `image-exceptions.yaml` | Recorded sign-offs on scan findings |

### The abstractions, with real values

`policy.yaml` turns the thresholds into numbers:

```yaml
thresholds:
  routine_maximum_cost_usd: "500"
  routine_maximum_runtime_hours: "12"
  routine_maximum_attempts: 2
  routine_maximum_fanout_size: 64
  routine_maximum_parallelism: 8
```

Over $500, or over 12 hours, or more than 2 attempts, and `classify_request` returns
`EXCEPTION` — a team lead is no longer enough and a platform admin is required. That is the
entire authority model, in five lines you could read to a stakeholder.

`repositories.yaml` has two entries, and note what a registration pins: even the *base* image
is pinned by digest rather than `python:3.12`. Both repos deliberately share one base, with
the reasoning inline — a second base would be a second thing to review, scan and re-pin.

### The part worth stealing

`organization.yaml` lists real people, and its comments record a **known disagreement between
this file and the world**:

```yaml
# The GitHub `team-leads` team gates the run-approval-lead environment and is what actually
# stops a job. It is not in this repository and does not follow this edit; changing it is an
# owner action in the organization settings, and until it happens the two disagree.
```

This repo declares who the leads are, but a GitHub org setting physically blocks the job, and
editing one does not edit the other. Rather than pretend, the file names the seam and says who
has to close it. The comment above it is just as good: a lead swap is documented, and the
person replaced stays in `members`, because leaving the organization and ceasing to lead a
group are two different facts and only one of them happened.

The generalizable lesson: **config comments should carry the reasoning and the known gaps, not
restate the key names.** A future reader's real question is never "what is `team_leads`" — it
is "why is this person on the list, and does it still match reality?"

> **Say it out loud.** `config/` is the reviewed facts, separate from the code that enforces
> them. Seven YAML files: the roster, the approval thresholds, the registered repos, the
> compute menu, the datasets, the AWS targets, and recorded security exceptions. Each is
> validated against a contract on load, so bad config fails at startup rather than at 3am.
> Changing who can approve a $500 job is a data diff a non-programmer can review.

---

## Lesson 5 — A submission

A researcher opens GitHub Actions, picks "Submit a run," and fills in a form. Four jobs run
in order:

| Job | Does | Holds AWS credentials? |
| --- | --- | --- |
| `resolve` | Ask the registry which image the declared commit published | Yes — two read-only ECR calls |
| `compile` | Build the manifest, price it, classify it routine or exception | **No — by construction** |
| `deny-unapproved` | Prove an unapproved job *cannot* get the admission role | Yes, and expects refusal |
| `submit` | Behind the approval gate: re-verify, then hand to AWS | Yes |

### Who picks the approver

`compile` is where a submission becomes routine or exception. That job declares no `id-token`
permission at all, so it physically cannot mint an AWS token — a stronger guarantee than
trusting it not to.

Then the gate:

```yaml
    environment:
      name: ${{ needs.compile.outputs.environment }}
```

That `needs` is load-bearing. GitHub would equally accept an expression over the dispatch
`inputs`, and then a submitter could type their own approval environment into a text box and
route an exception to the friendlier gate. **The gate is named by the code that read the
policy, never by the person asking.**

### Derive rather than ask

The form has fourteen fields: seven required, seven optional overrides. It deliberately does
not ask for things it can work out. Picking a workload profile already fixes the compute
profile, runtime bound, attempt bound and checkpoint contract; asking again invites
contradicting the catalog.

The image digest is the sharpest version. It used to be required, and the module docstring is
candid about why that was bad:

> It used to be a required seventy-one-character field that had to agree with the declared
> commit and was compared with nothing, so a submission could name commit A beside an image
> built from commit B and be faultless on every field.

Now it is derived from the commit, and the override is checked against what that commit
actually published. **Two fields that must agree became one field plus a derivation** — the
misconfiguration stopped being possible rather than being detected.

Same lesson, different pair: nothing used to compare the declared repository against the
workload profile's repository, so `OLMo-core` with `dolma-tokenize-smoke` compiled cleanly and
would have run under another codebase's runtime and checkpoint contract.

### The hash tripwire

The manifest hash is computed **three times**:

1. At compile, and shown to the approver.
2. After the gate releases, from the artifact that crossed it.
3. Inside AWS at admission, because the runner is not trusted to report on itself.

For that to be a real tripwire rather than noise, both checkouts pin `github.sha` rather than a
branch name. Against a moving ref the hashes could differ merely because the branch advanced,
and *a tripwire that fires for an ordinary reason is one people learn to route around*.

The manifest crosses the gate as an **artifact**, not a job output, so the far side hashes
exactly the bytes the near side hashed.

### What the approver sees

Cost is shown as the arithmetic, not just a total:

```
`$<rate>/hour x <n> node(s) x <h>h x <a> attempt(s) x <c> cell(s)` = **$<total>**
```

And an exception says *which* ceiling it broke, in words, because "a cost figure on its own
invites a rubber stamp."

There is a nice small bug fixed in the `_plain()` helper: `StrictDecimal` normalizes `"500"`
into `Decimal("5E+2")`, so interpolating it directly put **`$5E+2`** in front of a human
approver.

### Refuse before you spend a human

Anything checkable without AWS is refused at compile — unregistered workload, mismatched
repository, retries without a checkpoint, an unpriceable compute profile:

> Exit 1 is a refusal on the merits, and it is the whole point of refusing here: a submission
> naming an unregistered dataset would be denied by admission whatever a reviewer said, and
> spending human attention on it first teaches reviewers that approving is a formality.

That is a claim about **human behaviour**, not software. Note also that "refused on the merits"
and "the form could not be read" exit with distinct messages — a broken tool must never read
like a judgement.

> **Say it out loud.** A researcher fills in a form. One job reads which image that commit
> published; a second job, holding no AWS credentials at all, turns the form into a full
> manifest, prices its worst case, and classifies it routine or exception. That classification
> names the GitHub environment gating the next job, so the submitter cannot choose their own
> approvers. The approver sees the cost as multiplication, which ceiling was exceeded if any,
> and the manifest hash. After they release the gate, the hash is recomputed from the artifact
> that crossed it, and recomputed again inside AWS. Anything refusable without AWS is refused
> before a human is asked, so an approval is never spent on something admission would reject.

---

## Sidebar A — GitHub environments, and why `deny-unapproved` exists

### What "environment" means here

A **GitHub environment** is a named object in repository settings carrying **protection
rules**. The one that matters is *required reviewers*. When a job says:

```yaml
environment:
  name: run-approval-lead
```

GitHub pauses the job before its first step, marks the run waiting, and notifies the
configured reviewers. Nothing runs until a human clicks Approve. **The environment is the
approval gate.** There are two:

| Environment | Reviewers | Used for |
| --- | --- | --- |
| `run-approval-lead` | team leads | routine submissions |
| `run-approval-admin` | platform admins | exception submissions |

### How a job gets AWS credentials

No AWS access key is stored in this repository. Instead, **OIDC federation**: GitHub mints a
short-lived signed token describing the job, and AWS trusts GitHub as an identity provider.
The token carries claims — repository, workflow file, branch, and a `sub` (subject) claim.

The admission role's trust policy lists what those claims must be:

```yaml
StringEquals:
  token.actions.githubusercontent.com:aud: sts.amazonaws.com
  token.actions.githubusercontent.com:job_workflow_ref: edu-llm/platform/.github/workflows/submit-run.yml@refs/heads/main
  token.actions.githubusercontent.com:repository_owner_id: "306859726"
  token.actions.githubusercontent.com:repository_id: "1311508598"
  token.actions.githubusercontent.com:sub:
    - repo:...:environment:run-approval-lead
    - repo:...:environment:run-approval-admin
```

The hinge: **GitHub puts `:environment:<name>` into the subject claim only for a job that
declared an environment and cleared its protection rules.** A job without one gets a
ref-scoped subject instead. So the subject claim is cryptographic proof that a human approved
— AWS is not trusting the workflow to be honest, it is reading a signed statement from GitHub.

Two details in the template comments are worth stealing. The environments are **enumerated,
not wildcarded**: anyone who can edit a workflow can bring a new environment into existence
just by naming it, and an auto-created environment has *no protection rules at all*, so
`StringLike` on `:environment:*` would accept a subject that never passed a gate. And renaming
`run-approval-lead` in repo settings does not fail loudly — it just makes every submission die
at AssumeRole with an error that reads like a broken role ARN.

### Why the probe

Everything above is a claim about a **deployed** configuration. The trust policy lives in a
CloudFormation template in git, but the role is created from a laptop and is not redeployed by
CI. Someone could widen it in the console and **every test in this repository would stay
green** while the approval gate quietly became decorative.

`deny-unapproved` is a live experiment run on every submission. Same file, same repo, same
branch, differing from `submit` in exactly one respect: it declares no `environment:`. So
GitHub mints a ref-scoped subject, and the job tries to assume the admission role with it.

It expects to fail. Passing is the failure. Three things make it a careful experiment rather
than a gesture:

- **It is a controlled variable.** Only the environment key differs, so a refusal isolates the
  environment condition.
- **It must live in this file.** The trust policy pins `job_workflow_ref` with `StringEquals`,
  so the same test elsewhere would be refused because the *file* is wrong — a refusal that
  says nothing about what is under test.
- **It refuses to pass for the wrong reason.** It checks that STS was reached and answered
  specifically `AccessDenied`; anything else reports `deny_probe_inconclusive`.

The pattern generalizes: **test what you are forbidden from doing, using the actual
credentials, at the moment you hold them.** A permissions template in git says what should be
true; only an attempt says what is.

### The known limitation

On any branch other than `main`, the `job_workflow_ref` condition also fails, and STS reports
`AccessDenied` without saying which condition caused it. So on a branch the job goes green for
a reason it cannot distinguish from the one it is testing. The workflow says so.

See the appendix for what could be done about that.

---

## Sidebar B — Why nothing can cancel a running job

Cancelling the submission workflow in GitHub does not cancel the Batch job. This is the one
capability the README names as *missing* rather than merely unobserved.

### It is the default, not a decision

GitHub's cancel button cancels a **GitHub workflow run**. The Batch job is a **separate object
in AWS** with its own lifetime. The two systems have no relationship except that one made an
API call to the other, minutes ago, and that call already returned. Cancellation is a feature
you must build.

### Why not just add the permission

The admission role is **the role a submitter holds**. After approval clears, the workflow
assumes it, and for those fifteen minutes the submitter's workflow *is* that identity. Adding
`batch:TerminateJob` would mean anyone who can get any submission approved can kill any job on
the queue — someone else's training run, six hours in.

And look at the asymmetry: submitting requires a named human to click Approve on a specific
manifest; terminating would require nothing, because the credential is already in hand. That
is an **unapproved destructive action riding on an approved constructive one**.

The insight is that *"who may submit a run"* and *"who may kill this run"* are different
questions with different answers, and the second has not been designed.

The IAM template is explicit that the absences are deliberate:

```yaml
# Deliberately absent: states:StopExecution, so an approved submitter cannot
# abort an admission decision that is already being recorded; iam:PassRole,
# because the state machine already carries its own execution role and this
# role never names one; and every s3: action, because the lineage record is
# written by the state machine, not by the caller who asked for it.
```

### What bounds the money

The **mandatory per-attempt timeout**. Every manifest carries `maximum_runtime_hours`, it
becomes Batch's `attemptDurationSeconds`, and it has been observed stopping a real job. That
is why the approver summary shows worst-case cost as `rate × nodes × hours × attempts` — the
number is a genuine ceiling precisely *because* nothing can run past it. Blunt, but capped and
signed off in advance.

### What is done instead of faking it

**The cancel step tells the truth.** Instead of a button that appears to work, a step summary
names the run id, states plainly that AWS compute is still running, and points at the runbook.
It deliberately does not fail the job — a red X would report a defect where there is a person
changing their mind.

**A human can actually do it.** `infra/README.md` carries the procedure, including the detail
that `list-jobs` takes one status at a time and a job waiting for capacity needs
`--job-status RUNNABLE` too — which is the more likely state for a run somebody gave up on.

**A manual termination still lands in the audit trail.** Batch emits a state change, the rule
delivers it, the recorder writes state `cancelled`, and `lifecycle_projection.py` reads the
termination reason to distinguish an operator's cancellation from a crash. The capability is
manual; the record is not.

**The scoreboard does not get to claim it.** Phase 3's criteria used to include cancellation
checks. Rather than mark them covered, or deferred (which passes the gate), they were
**deleted** — and the numbering now skips 5, 6 and 7. The gap in the sequence is a permanent
scar saying something used to be measured here and stopped.

### The generalizable point

The reasoning is sound and the outcome is still a real gap; the interesting part is the choice
between faking it, hiding it, and saying it. **The cost of a missing feature is bounded when
everyone knows it is missing.** The dangerous version of this system is not the one without a
cancel button — it is the one with a cancel button that silently does nothing, where you click
it, watch the workflow go grey, and walk away while a GPU bills for another eleven hours.

See the appendix for a proposed design.

---

## Lesson 6 — Admission

Admission is the gate inside AWS: a Step Functions state machine with a Lambda validator, and
the last checkpoint before real resources are consumed. It is where the approved manifest
arrives and either becomes a Batch job or becomes a recorded refusal.

### The one-sentence principle

The module docstring of `src/edullm_platform/admission.py` states it:

> The point of doing this here rather than in the workflow that submits is that **nothing a
> caller sends is taken as a finding**.

The caller supplies two things: a manifest, and the hash a reviewer approved. Everything else
— whether the repository, dataset and compute profile are registered, what the run may cost,
which class it falls in, whether the approver may release it — is **re-derived** from
configuration packaged with the deployed code. A caller that lies about a derived value does
not change the outcome, because the derived value is never read from the input.

This is why the workflow doing all the same work earlier is not duplication. The workflow's job
is to fail fast and inform a human; admission's job is to decide. Only one of them is inside
the trust boundary.

### Three orderings that are not interchangeable

**The manifest hash is checked before anything is derived from the manifest.** An environment
gate approves a *job*, not *content* — so until the hash matches, the manifest is a document of
unknown provenance and deriving facts from it would mean judging something nobody approved.

**The approving environment is checked against the re-derived class rather than trusted.** AWS
accepts the subject claim as proof a gate was passed, but the claim says only *which* gate, not
that it was the right one. Re-deriving the class here and comparing is what stops an exception
being released by a lead:

```python
required_environment = ApprovalEnvironment.for_approval_class(approval_class)
if approving_environment is not required_environment:
    return decide(reason=AdmissionReason.APPROVAL_ENVIRONMENT_MISMATCH, ...)
```

**Authorization is evaluated last**, because it is the only question whose answer depends on a
person rather than on the request.

There is a fourth ordering, after the decision rather than within it: where an accepted run
*goes* — the queue, the job definition, the two IAM roles — is resolved once everything else
has said yes. A profile with nowhere to run becomes a refusal with its own reason
(`NO_EXECUTION_TARGET`) rather than an exception, so a reader of the record can see that the
submission was classified, priced and authorized, and the only thing wrong with it was capacity.

### Authorization

`contracts/authorization.py` answers "may this person release this?" and returns a decision
carrying **twelve possible reasons** — four that grant and eight that refuse. The refusals are
specific: `submitter_not_in_roster`, `approver_not_in_roster`,
`self_approval_not_permitted_for_member`, `approver_lacks_admin_role`,
`approver_does_not_lead_submitter_team`, `submitter_not_in_claimed_team`, and so on.

Two design points.

**The reason and the outcome cannot disagree.** A model validator enforces it:

```python
@model_validator(mode="after")
def validate_outcome_matches_reason(self) -> Self:
    if self.granted != (self.reason in GRANTING_REASONS):
        raise ValueError("authorization outcome must match the recorded reason")
    return self
```

So a record saying "granted" for a refusing reason cannot be constructed, let alone stored.

**Self-approval is permitted for leads and admins, and refused for members.** A lead releasing
their own routine run is `routine_self_authorized`; an admin releasing their own exception is
`exception_self_approved_by_admin`. Both are intended. A plain member doing the same is
`self_approval_not_permitted_for_member`. That prohibition is enforced twice, independently:
here in code, and by members simply not being reviewers on the environments.

### The Lambda decides and does not record

`admission_handler.py` is a thin shell over `admit()`. It **holds no S3 permission and makes no
AWS call.** It returns the two records and the state machine writes them, which buys three
things:

- the write appears as a first-class event in the execution history rather than inside an
  opaque function,
- the component that parses an untrusted manifest **cannot write anything at all**,
- the bytes S3 stores are the canonical serialization rather than a re-encoding of it.

The same split, more sharply, for launching: the handler builds the exact parameter blocks for
`batch:RegisterJobDefinition` and `batch:SubmitJob`, and holds neither permission, nor
`iam:PassRole`. **The component that parses an attacker-shapeable manifest decides what would
be submitted and cannot submit it.**

### Configuration is what was deployed, not what was sent

Policy, roster, repository registry, catalog, dataset registry, scan exceptions and execution
targets are all packaged into the deployment artifact and read from disk. Nothing in the event
can supply or override them. **That is what makes `policy_version` in a decision record a fact
about the platform rather than a claim by the caller.**

The image scan follows the same rule and is worth calling out, because the compile job could
not do this:

```python
# The state machine puts the ECR describe result here, from a task it ran itself.
# It is deliberately not passed through from the execution input: the caller
# supplies the manifest, and letting it also supply the scan findings would let it
# declare its own image clean.
```

### Every refusal earns a record — except one

Each refusal path returns a `DecisionRecord` naming the reason, the detail, the policy version,
the classification, the authorization decision and the cost. Those get written to the lineage
bucket, immutably.

The exception is `UnreadableManifestError`, and the distinction is careful:

> A rejected submission is one this system understood and refused, and it earns a decision
> record saying so. A payload that does not parse cannot be described by a record whose shape
> embeds a manifest, so it fails the execution instead and is left to the execution history. No
> compute follows either way.

### Two details worth stealing

**Read the account id from the invocation context, not from configuration.** An environment
variable says what somebody wrote into a template, which is a claim that can be wrong — and a
wrong one would build queue ARNs pointing at another account, failing with a message about a
missing queue rather than about a misconfigured function. `invoked_function_arn` is Lambda's own
statement about where it is running and cannot disagree. STS would be equally true but costs a
network call from a component whose design property is making none — and
`sts:GetCallerIdentity` cannot be denied by a policy, so the grant would be invisible in a role
diff.

**Return objects, not strings, and round-trip them through the canonical bytes.** Learned from a
live run that stored every record quoted and escaped. The S3 SDK integration JSON-encodes
whatever it is handed, so returning the canonical *string* stores `"{\"run_id\":...}"` and every
reader parses twice. Returning an object stores ordinary JSON — and round-tripping through
`canonical_json_bytes` first yields a mapping whose keys are already sorted, so what S3 stores
is byte-identical to what was hashed. Using `model_dump` instead would hand Step Functions
field-definition order and quietly lose that.

> **Say it out loud.** Admission is the gate inside AWS, and its rule is that nothing the caller
> sends is taken as a finding. The caller supplies a manifest and the hash a reviewer approved;
> everything else is re-derived from configuration baked into the deployment. The hash is
> checked first, because until it matches, the manifest is a document nobody approved. Then the
> approval class is re-derived and compared against which gate actually released it, so an
> exception cannot be waved through by a lead. Authorization comes last, because it is the only
> question about a person. The Lambda decides and cannot record or launch — it returns the
> records and the request blocks, and the state machine holds those permissions. Every refusal
> it understands becomes an immutable decision record; a payload that does not even parse fails
> the execution instead, because a record whose shape embeds a manifest cannot describe one.

---

## Appendix — design questions raised during the walkthrough

These are proposals, not decisions, and none of them is implemented. They are recorded here
because the questions came up while reading and are worth someone's judgement.

### A1. The `deny-unapproved` probe passes for the wrong reason on branches

**Problem.** On `main`, the only trust-policy condition the probe violates is `sub`, so a
refusal proves the environment condition works. On any other branch `job_workflow_ref` also
fails, and STS reports a flat `AccessDenied` without saying which condition caused it. Two
causes, one indistinguishable outcome. The job goes green having established nothing.

**Option 1 — skip rather than pass.** Add `if: github.ref == 'refs/heads/main'`. A skipped job
renders grey rather than green. A green check that means nothing is worse than no check,
because people read checkmarks and stop thinking. Branch submissions cannot assume the role
anyway, so nothing is weakened.

**Option 2 — remove the confound with a canary role.** Deploy a second role whose trust policy
is the admission role's with one change: `job_workflow_ref` becomes a `StringLike` on
`…/submit-run.yml@*`. Then `sub` is the only condition that can refuse, on any branch, and the
attribution is clean. Two things keep it safe: grant it **nothing** (no `Policies` block at
all — you never make a call with the credentials, so an assumable role that can do nothing is
harmless), and assert with the existing `role_drift` machinery that its trust statements equal
the admission role's modulo that one condition.

**Option 3 — a static substitute on branches.** Where you cannot probe, read: fetch the
deployed trust policy with `iam:GetRole` and compare against `infra/iam/admission-role.yaml`
using the drift comparison that already exists. Weaker in kind — reading a claim rather than
testing behaviour — but strictly better than a false green.

**Suggested.** Option 1 now, because it converts a lie into an honest absence for one line.
Option 2 when the role is next touched.

### A2. Nothing verifies the approval environments still have reviewers

**Problem.** Three failure modes hide behind the environment design, with very different
severities:

| Failure | Outcome | Covered? |
| --- | --- | --- |
| Someone names a new environment to skip the gate | Refused — the subject is not one of the two enumerated | Yes, by the enumeration |
| `run-approval-lead` is renamed | Fail-closed; every submission dies at AssumeRole | Not prevented, but safe |
| Reviewers are removed while the name stays | **Fail-open, silently** | Only by periodic evidence capture |

The third is the dangerous one. Strip the required reviewers and the job no longer pauses, but
it still declares that environment, so GitHub still mints the same subject and AWS still grants.
Every check stays green and the gate is gone. AWS structurally cannot catch it: the trust policy
sees a *name*, and only GitHub knows whether that name still has teeth.

**What already exists.** `phase2_evidence.py` captures the reviewer lists, whether self-review
is prevented, whether an admin can bypass via "Start all waiting jobs", and — deliberately —
*every* environment on the repository rather than only the two expected, so a third unprotected
one is visible to a reader. It also stores a reviewer as a type plus a name, because a team and
a user are different controls wearing the same slot.

**The residual gap.** That capture is a point-in-time snapshot with a 30-day expiry, not a live
check. Between captures there is a window.

**Proposal.** Have the credential-free `compile` job call
`GET /repos/{owner}/{repo}/environments/{name}` and refuse if the environment it is about to
route to has no required reviewers. It needs no AWS access, it is one API call, and it converts
the failure from fail-open-until-somebody-captures-evidence into fail-closed-per-submission.

**The idea underneath both A1 and A2.** Each is an invariant spanning two systems where neither
system can check it alone. AWS enforces "the subject names an approved environment"; GitHub
enforces "this environment requires a reviewer." The property actually wanted — *a human
approved this* — is the conjunction, and nothing owns it. Which is why the answer keeps being a
third thing: a probe, a capture, a drift check. Something standing outside both, asking each what
it currently believes.

### A3. Building cancellation

**What already exists.** The *receiving* half. `lifecycle_projection.py` defines
`CANCELLATION_REASON_MARKERS = ("edullm:cancelled",)` and refuses to guess at anything else, the
`cancelled` run state exists, and the projection distinguishes an operator stopping a job from a
job dying. What is missing is the thing that writes the marker.

**The core principle.** Requesting a cancellation and performing one are different operations
held by different identities — the same shape admission already has, with a different verb:

| Component | Holds | Why |
| --- | --- | --- |
| `cancel-run.yml` workflow | OIDC → cancellation role | The caller asks |
| Cancellation role (GitHub-facing) | One `states:StartExecution` | Cannot terminate anything itself |
| Cancellation state machine | Invokes the authorizer, then terminates | The platform acts |
| Authorizer Lambda | Reads lineage, evaluates the roster | The decision, inside the trust boundary |
| State machine execution role | `batch:TerminateJob`, scoped to this project's queues | The only holder of the dangerous verb |

**Who may cancel.** Read the run's owner off its own lineage record — the platform wrote it and
the caller cannot edit it — then permit the submitter, the team lead of the run's team, or a
platform admin. Anyone else is refused, and the refusal is recorded.

Note the deliberate asymmetry with approval: **an action that reduces risk should be easier to
take than one that creates it.** Submitting spends money and needs a human gate; cancelling stops
spending and should need none, because a cancellation waiting on an approver costs money while it
waits. The downside — losing in-progress work — is bounded precisely because the run checkpoints
and can be resubmitted.

**Four decisions worth defending.**

- **A first-class workflow, not just a cancel-button hook.** `cancel-run.yml` takes a run id and
  can be dispatched at any time, including for a run whose submitting workflow already finished.
  Fire it best-effort from `if: cancelled()` as well. The rule: never hang a critical operation
  off a best-effort hook — GitHub's five-minute grace window is a nicety, not a mechanism.
- **Idempotent by construction.** Name the execution after the run id, as admission does.
  Cancelling an already-finished job is a **successful no-op**: the requested state is "not
  running," and it already holds. A cancel path that errors when it has nothing to do trains
  people to ignore its output.
- **Cover both statuses.** The runbook already learned that the common case is a job stuck in
  `RUNNABLE`, and `list-jobs` takes one status at a time. Look at both and say which was found.
- **Always write the marker.** `--reason edullm:cancelled by <actor>`; the prefix is what makes
  the projection record a human decision rather than a crash.

**Prove the negative.** The new role gets its own denial matrix: attempt `batch:TerminateJob`
directly, attempt `states:StartExecution` on the *admission* machine, attempt a lineage write.
All must be refused, or the hole has been rebuilt somewhere with less scrutiny.

**Bound the money independently.** Cancellation should not be the only cost control. Alongside
the existing timeout: `maxvCpus` on each compute environment as a hard ceiling on concurrent
spend, and AWS Budgets with alerts so nobody learns about a runaway from the monthly bill.

**The generalizable version.** When a capability is dangerous because of *who would hold it*, do
not drop it — **relocate it**. Split the request from the act, give the caller only the right to
ask, and put the decision somewhere the caller cannot reach, judged against a record the caller
did not write.

And the sequencing lesson from what was actually done here: the **receiving** half was built
first. The marker convention, the projection, the `cancelled` state and the operator-versus-crash
distinction were all present and tested before anything could produce them. When the initiating
half lands, it plugs into an audit trail that already knows what to do with it.

---

## Still to cover

The walkthrough is not finished. Remaining stops:

- **The refusals** — `admission_denials.py`, `batch_denials.py`, `publisher_denials.py`,
  roughly 2,400 lines treating "no" as a first-class result.
- **Execution and lifecycle** — `execution.py`, `lifecycle_projection.py`, `checkpoints.py`,
  and the Batch event path.
- **Evidence capture** — `capture_tooling.py`, `evidence.py`, `fixtures/evidence/`, and why
  records expire after 30 days.
- **Phase gates and proof bundles** — `criteria.py`, the five gates, `proof_generator.py`, and
  the three-status rule (`covered` / `deferred` / `gap`) a reviewer reads instead of the test
  suite.
