<!--
What this changes, and why, in the imperative. The title becomes a line in the next release
note, so write it for a researcher reading that note rather than for a reviewer reading this
diff.

BEFORE YOU OPEN THIS, ANSWER ONE QUESTION. Does this change what an installed `edullm`
answers? If it touches the CLI, a module the CLI imports, or one of the six configuration
files it reads, then yes, and `Checks (CI)` will not let it merge until `project.version`
says which size of release it earns. Pick one and commit what it writes.

  uv run python tools/next_version.py --bump patch   # anything a re-install fixes
  uv run python tools/next_version.py --bump minor   # a new command, flag or optional spec
                                                     # field, or a new refusal that can stop
                                                     # a submission which used to go through
  uv run python tools/next_version.py --bump major   # a flag or field removed or given a
                                                     # new meaning, or a changed exit code

Each moves pyproject.toml, uv.lock and the pinned install line together. Commit all three.

The size is a statement to thirty-five people who will re-install on it, and nothing can
work it out for you. Leaving it off does not quietly get you a patch; the check fails and
names the three commands.

Nothing to declare is the ordinary case. A change to tests, tools, workflows, guides or
documentation reaches nobody's install, and the check says so and passes.
-->
