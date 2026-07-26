# Phase 1 image rebuild comparison

Criterion 2 asks that rebuilding identical inputs be *explainable*, and is careful not to ask that it be reproducible. This is the explanation.

The comparison could not come from the publish workflow and was never going to. That job looks the tag up before it builds, so a re-run of the same commit resumes to the digest already in the registry rather than building again — correct behaviour, because ECR tags are immutable and the run-URL label guarantees a second build would carry a different digest that the tag could never be moved to. So the builds below were made deliberately on one laptop, and the image the workflow published was fetched from the registry to compare against. Both the builder and the platform are recorded, because the answer depends on both.

| fact | value |
| --- | --- |
| commit | `4204375e6db85abc244ec7f626de8d3cc3511402` |
| base image | `sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de` |
| dockerfile | `.edullm/Dockerfile` |
| platform | `linux/amd64` |
| builder | docker 29.6.1 with buildkit, on darwin/arm64 building linux/amd64 |
| configuration fields compared | 70 |
| record | `fixtures/evidence/phase-1/rebuild/local-rebuild-comparison.json` |

## What differs, against the first build

| comparison | what was varied | fields differing | which |
| --- | --- | --- | --- |
| `a` vs `b` | second build, no cache, byte-for-byte the same inputs as a | 2 | `created`, `history[12].created` |
| `a` vs `c` | as a, varying only the per-run label to a second run URL | 3 | `config.Labels.edullm.workflow.run.url`, `created`, `history[12].created` |
| `a` vs `d` | as a, from a copy of the same source whose file modification times were rewritten | 3 | `created`, `history[12].created`, `rootfs.diff_ids[5]` |
| `a` vs `published` | the image the workflow published, its configuration fetched from the registry | 6 | `created`, `history[10].created`, `history[11].created`, `history[12].created`, `rootfs.diff_ids[4]`, `rootfs.diff_ids[5]` |

Every one of the 70 fields not named above is identical in every build, and the ones derived from a pinned input are checked to be identical rather than merely observed to be: the environment, the command, the working directory, the architecture, the three content labels, every recorded build step, and all four layers inherited from the base image. Without that check the account below could be satisfied by widening the list of causes until it covered anything.

## Why each field differs

| cause | fields | deliberate |
| --- | --- | --- |
| per-run label | `^config\.Labels\.edullm\.workflow\.run\.url$` | yes |
| image creation timestamp | `^created$` | no |
| history entry timestamp | `^history\[\d+\]\.created$` | no |
| layer content timestamp | `^rootfs\.diff_ids\[\d+\]$` | no |

### per-run label

The publish workflow labels every image with the URL of the run that built it, which is different on every run by construction. This is the one difference that is deliberate: it is what lets somebody holding a digest find the run that produced it, and it is also why a re-run of the same commit could never produce the same manifest digest even if everything else were pinned.

### image creation timestamp

BuildKit stamps the configuration with the wall-clock instant the build finished. Nothing derives it from an input, so two builds a second apart differ here and two builds a month apart differ here by a month.

### history entry timestamp

The same clock reading, recorded again against each step this build executed. The history entries inherited from the base image carry the base build's timestamps and are identical, because the base is pinned by digest; only the entries this Dockerfile adds move.

### layer content timestamp

A layer digest covers the tar of the layer, and a tar carries a modification time per entry. Two of the layers here are the build's own and each picks up a clock: the directory the WORKDIR creates is stamped with the instant the build ran, and the layer the source is copied into carries the modification times of the checkout it was copied from, which a fresh clone sets to the moment it ran. The bytes of every file are identical either way; the metadata around them is not. Layers inherited from the pinned base never move, because their tars are the base's and are fetched rather than built.

## What this does and does not establish

- Two independent builds of identical inputs produce an image whose filesystem is identical layer for layer, and whose identity is not. Only two fields move, and both are clock readings.
- One of the four causes is deliberate. The per-run label is what lets somebody holding a digest find the run that produced it, and it is also why no re-run of the workflow could ever reproduce a digest even if every clock were pinned.
- The other three are clocks, and `SOURCE_DATE_EPOCH` would pin them. Nobody has asked for byte-level reproducibility and this criterion does not, so nothing here proposes it.
- This says nothing about a different builder. A BuildKit that wrote layer metadata differently would produce a different answer, which is why the builder is recorded in the file rather than assumed.
