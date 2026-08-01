# Proof bundles

**This directory is not documentation and you should not read it to learn how anything
works.** If you want to run a job, [the guides](../guides/the-platform.md) are what you
want. If you want to know how the platform is built,
[MAINTAINING.md](../MAINTAINING.md) is.

Each `phase-N/` directory is a generated record of what was true about this repository at
the moment it was built: which acceptance criteria were covered, which were deferred and
why, what the test suite reported, and the digests of the fixtures and captures the claims
rest on. They exist so a reviewer can decide whether a phase is done without reading the
test suite, and so a claim made months ago can be checked against the tree that made it.

## They are generated, so do not edit them

Every file here is written by `tools/build_phaseN_proof.py`. An edit by hand is overwritten
by the next build and, worse, is a claim nothing produced — which is precisely what these
bundles exist to make impossible.

```bash
uv run python tools/build_phase0_proof.py
```

`serialization-goldens.json` is the one that will stop you. It records the canonical digest
of every fixture, and the builder refuses to rewrite it silently: a moved digest is either a
change you meant, in which case `--regenerate-goldens` re-records it and you review the diff
in the same commit, or a regression, in which case re-recording it is the wrong repair. The
builder prints both options and takes neither on your behalf.

## What a moved digest means

The bundles record a `Source commit` and a generation timestamp, so they always name the
commit *before* the one that contains them. That is expected and is not drift.

What is drift is a digest changing when nothing about the thing it describes did. The
goldens are a serialization tripwire: a change to field ordering, to a serializer, to a
default value, or to a fixture lands here and nowhere else in the suite.
