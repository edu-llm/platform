# Phase 5 serialization goldens

The canonical digest of each of the three committed pilot-run captures, taken over the parsed record rather than over the file bytes. Reindenting a capture is therefore not drift; a field changing value is.

Phases 1, 2 and 3 record this tripwire over role templates, because a role is the thing that can be widened without anybody noticing. Here the thing that can move without anybody noticing is a capture: these records are the only evidence that two people used this platform, and re-taking one after the account has moved on would change what the bundle claims while leaving every test green.

| run | path | contract | canonical bytes | digest |
| --- | --- | --- | --- | --- |
| run_019fb4ce-cf24-7028-8eed-a32a28ec2493 | fixtures/evidence/phase-5/runs/run_019fb4ce-cf24-7028-8eed-a32a28ec2493/admitted-run.sanitized.json | AdmittedRunEvidence | 1307 | sha256:119cbb59e7bf200397b056d7bd579a0619a9f56286b5e245481b843026048d66 |
| run_019fb4f6-6679-708d-9bee-1ef5ccf5a002 | fixtures/evidence/phase-5/runs/run_019fb4f6-6679-708d-9bee-1ef5ccf5a002/admitted-run.sanitized.json | AdmittedRunEvidence | 1428 | sha256:030398f5e1be28de5a76ab81ad2666fd067e0ea88afa53063941bc8fdeb30a94 |
| run_019fb505-9b0f-70cc-b890-2c60037cfe41 | fixtures/evidence/phase-5/runs/run_019fb505-9b0f-70cc-b890-2c60037cfe41/admitted-run.sanitized.json | AdmittedRunEvidence | 1419 | sha256:7cede1896ed58c4ed673e952fa45e8bfae174efbf90a25aea030666ddfdc33fb |
