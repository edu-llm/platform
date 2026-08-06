<!--
What this changes, and why, in the imperative. The title becomes a line in the next release
note, so write it for a researcher reading that note rather than for a reviewer reading this
diff.

BEFORE YOU OPEN THIS, ANSWER ONE QUESTION. Does this change what an installed `edullm`
answers? If it touches the CLI, a module the CLI imports, or one of the configuration files
it reads, then yes, and `Checks (CI)` will not let it merge until `project.version` says
which size of release it earns.

ALMOST EVERY CHANGE HERE IS A PATCH. Anything a re-install fixes is one, including a config
addition and a reworded refusal. It needs no reason and no argument.

  uv run python tools/next_version.py --bump patch

A MINOR AND A MAJOR HAVE TO SAY WHY, AND THE SENTENCE IS PUBLISHED. It is committed above
the version line so a reviewer reads it here, and `release-tag.yml` puts it in the release
note as the Summary of a minor or the Break of a major. There is no way to widen the version
without writing it.

  uv run python tools/next_version.py --bump minor --why "<what a researcher can now do>"
  uv run python tools/next_version.py --bump major --why "<what stopped working for them>"

A minor is a new command, a new flag, a new optional spec field, or a new refusal that can
stop a submission which used to go through. A major is a flag or spec field removed or given
a new meaning, or a changed exit code, and it breaks somebody who already has this installed.
If you cannot write the sentence, this is a patch.

Each moves pyproject.toml, uv.lock and the pinned install line together. Commit all three.

Nothing to declare is the ordinary case. A change to tests, tools, workflows, guides or
documentation reaches nobody's install, and the check says so and passes.
-->
