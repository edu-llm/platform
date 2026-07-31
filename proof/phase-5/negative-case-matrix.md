# Phase 5 negative-case matrix

The fifteen Phase 5 acceptance criteria, mapped to the tests cited for each one by node id. Each cited node id was collected and executed by this generator before the bundle was written; a citation pytest cannot collect aborts generation rather than being printed.

This mapping is defined once, in `src/edullm_platform/phase5_criteria.py`. The acceptance gate reads the same definition and executes the same node ids, so this matrix and `tools/validate_phase5.py` cannot disagree.

**The numbering is the migration document's eleven checks followed by four.** Criteria 1 to 11 are the checks the 2026-07-29 re-cut listed, in its order. Criteria 12 to 15 are what deriving a run's image from its declared commit owes over merely comparing the two, and they are appended rather than interleaved so that nothing already argued about had to be renumbered. Criterion 14 keeps its number after being rewritten, for the same reason.

Verification run: 91 tests executed, 91 passed, 0 failed, 0 errored, pytest exit code 0.

Three statuses exist and no more. **COVERED** means one or more cited tests prove the criterion as stated against the shipped configuration and all of them pass; the gate passes it. **DEFERRED** means an explicit recorded decision not to satisfy it yet, which requires both a written reason and a written trigger describing what makes it live again; the gate passes it. **GAP** is everything else, and the gate fails it. There is no in-between status, because an in-between status is what lets a gate be green and wrong at the same time.

`proving` tests prove the criterion as stated against the shipped configuration; only a COVERED criterion may cite one. `supporting` tests are cited evidence that does not amount to proof — either because they exercise the code path under a synthetic configuration that is not what ships, or because they prove only part of the claim. Both kinds are executed. A supporting citation that is renamed or deleted still fails the criterion.

| # | status | proving | supporting | check |
| --- | --- | --- | --- | --- |
| 1 | COVERED | 2 | 2 | A researcher who is not the author dispatches the submission workflow successfully. |
| 2 | COVERED | 2 | 1 | A run is released by a lead who is not the submitter, and the decision record reads routine_approved_by_lead_or_admin. |
| 3 | COVERED | 2 | 1 | An image built from a commit pushed today, with no hand-written exception entry, is accepted. |
| 4 | COVERED | 1 | 1 | The digest in the accepted manifest is the digest of the container that ran, evidenced from the Batch job description rather than from the template. |
| 5 | COVERED | 1 | 2 | A submission declaring a commit that did not produce its image is refused at compile, before a reviewer is asked. |
| 6 | DEFERRED | 0 | 4 | A GPU run claiming a team other than platform writes its checkpoint successfully. |
| 7 | COVERED | 2 | 3 | A refused submission tells the submitter which refusal it was, in the workflow, with no account id disclosed. |
| 8 | COVERED | 2 | 2 | An accepted run tells the submitter where its output, logs and W&B project are. |
| 9 | COVERED | 6 | 8 | A member with write access cannot trigger a deploy workflow. |
| 10 | COVERED | 2 | 1 | A member with write access cannot merge a workflow change without a code-owner review. |
| 11 | COVERED | 4 | 3 | Every accepted submission tells the submitter, on the summary it ends on, that cancelling does not stop the job, that a checkpoint omits optimizer state, and that team routes approval rather than granting permission. |
| 12 | COVERED | 2 | 1 | A submission that supplies a commit and no digest resolves to the image published from that commit. |
| 13 | COVERED | 1 | 1 | A commit with no published image is refused at compile, with a reason naming the build workflow. |
| 14 | COVERED | 1 | 3 | A commit publishes at most one image, so a submission resolving from a commit has no rebuild to choose between. |
| 15 | COVERED | 1 | 1 | An image_digest override naming a digest published from a different commit is refused. |

## Checks

### Check 1 — A researcher who is not the author dispatches the submission workflow successfully.

**Status: COVERED**

Scope:

- The submitter is asserted not to be the author rather than asserted to be one particular person. Naming the person would make the criterion go red the day a second pilot user arrives, which is the direction this phase is trying to move in.
- 'Successfully' is read as reaching admission rather than as pressing the button. A dispatch that failed on the first form field leaves a workflow run and nothing else, so what is checked is a manifest hash recorded against the run id -- which only admission writes.
- Write access is what made this possible and it is not free. Twenty-five of the thirty-five collaborators still cannot see the Run button, because workflow_dispatch requires write and GitHub's execution protections narrow who may trigger rather than widen it. Criteria 9 and 10 are the containment that had to land in the same change.

Proving tests (2), all executed and passing:

- `tests/test_phase5_run_evidence.py::test_the_workflow_was_dispatched_by_somebody_who_did_not_build_the_platform`
- `tests/test_phase5_run_evidence.py::test_the_dispatch_reached_admission_rather_than_stopping_at_the_form`

Supporting tests (2), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase5_run_evidence.py::test_a_capture_that_is_not_there_is_reported_rather_than_read_as_nothing_to_prove`
- `tests/test_phase5_run_evidence.py::test_every_committed_run_loads_as_the_record_its_directory_says_it_holds`

### Check 2 — A run is released by a lead who is not the submitter, and the decision record reads routine_approved_by_lead_or_admin.

**Status: COVERED**

Scope:

- THIS IS THE CRITERION THE PHASE IS NAMED AFTER AND IT HAD NEVER BEEN CLOSABLE. Twenty-five dispatches preceded these and every one was the author's, so every accepted decision record in the store read routine_self_authorized or exception_self_approved_by_admin. Both are granted authorizations, which is why the reason code rather than the grant is what this asserts.
- It also closes Phase 2 criterion 3, which asks that any team lead approval succeeds while approval_scope is organization. That criterion could not be closed by writing code and is closed here as a side effect of a person doing something.
- It rests on one participant, and that is worth stating. The pilot cohort is three and two of them are leads, who authorize their own routine runs by design -- so the only person in it whose submission needs releasing by somebody else at all is the one non-lead. A cohort of three leads would have produced three self-authorizations and no evidence about the path a member takes.
- team_verified is false on every record and is cited as supporting for that reason. The team a submitter claims is recorded and not enforced, because nothing binds a team to a person yet -- that is Phase 6 item 6.5. A record claiming a verified team would be evidence for a control that does not exist.

Proving tests (2), all executed and passing:

- `tests/test_phase5_run_evidence.py::test_a_run_was_released_by_a_lead_who_is_not_the_person_who_submitted_it`
- `tests/test_phase5_run_evidence.py::test_the_reason_code_the_two_person_design_exists_to_produce_was_written`

Supporting tests (1), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase5_run_evidence.py::test_the_team_the_submitter_claimed_is_recorded_as_a_claim_and_not_as_a_fact`

### Check 3 — An image built from a commit pushed today, with no hand-written exception entry, is accepted.

**Status: COVERED**

Scope:

- THIS WAS UNPASSABLE BY CONSTRUCTION UNTIL THE UNIT OF REVIEW CHANGED. config/image-exceptions.yaml held two entries, each naming one image digest; an image is refused unless somebody has written its digest there; and every build produces a new digest. So exactly two digests in the world could be submitted and every iteration needed a reviewed pull request from an admin. The exceptions list is empty now and the reviews are of vulnerabilities, which are facts about the shared base rather than about any one image.
- 'Pushed today' is compared against the runs' own submission timestamps rather than against the clock, so the check keeps meaning the same thing tomorrow instead of going red overnight.
- The registry is on BASIC scanning, which reads the operating system package database and does not look at Python distributions at all. About three gigabytes of installed Python in this image was not scanned by anything, so 'no unreviewed finding' is a statement about what was looked at.

Proving tests (2), all executed and passing:

- `tests/test_phase5_run_evidence.py::test_no_hand_written_exception_entry_stood_behind_the_image_that_was_accepted`
- `tests/test_phase5_run_evidence.py::test_the_image_was_published_on_the_day_the_runs_that_used_it_were_submitted`

Supporting tests (1), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase5_run_evidence.py::test_the_image_the_pilot_runs_were_admitted_on_was_published_from_their_commit`

### Check 4 — The digest in the accepted manifest is the digest of the container that ran, evidenced from the Batch job description rather than from the template.

**Status: COVERED**

Scope:

- EVIDENCED FROM THE JOB DESCRIPTION BECAUSE THE TEMPLATE IS THE THING THAT USED TO BE WRONG. batch_submit_request built ContainerOverrides with a command and an environment and no image, so the image was pinned in the CloudFormation job definition: the digest a submitter typed was validated, gated admission through the ECR scan, and was written immutably into lineage while the container that ran was whatever the template said. The two coincided only because the exception file contained exactly those digests, which made the lineage record's image provenance true by convention. Reading the digest back out of the template would have proved the convention.
- The per-run job definition is the mechanism and is cited separately. A shared definition pins one image for every run, so a matching digest would be a coincidence maintained by the exception file rather than a property of the submission.
- The run that never started is evidence here too, and deliberately. Batch reports the image for a job whose container failed to exec, so a run that pulled the right image and then died on its own command line still establishes which image was pulled.

Proving tests (1), all executed and passing:

- `tests/test_phase5_run_evidence.py::test_the_container_that_ran_was_the_image_the_manifest_was_admitted_on`

Supporting tests (1), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase5_run_evidence.py::test_the_job_definition_a_pilot_run_was_submitted_against_is_its_own`

### Check 5 — A submission declaring a commit that did not produce its image is refused at compile, before a reviewer is asked.

**Status: COVERED**

Scope:

- Under derivation the override is the only route by which a commit and a digest that did not come from it can both reach a manifest, so refusing the override is the whole of this. Before derivation the pair was accepted silently and the lineage record -- which every other guarantee rests on -- then named a commit that did not produce the image.
- The position of the refusal is half the criterion. At compile means before a lead is asked to read anything, and the case that motivated it is the class of two-form-inputs-each-individually-valid failures that have historically landed after a human approved.
- The honoured case is cited as supporting rather than dropped. A rule that refuses every override is not the rule -- a deliberate rebuild-and-pin has to stay available -- so the check that a legitimate override still works is what stops the refusal being satisfied by refusing everything.

Proving tests (1), all executed and passing:

- `tests/test_image_resolution.py::test_an_override_naming_a_digest_from_a_different_commit_is_refused`

Supporting tests (2), all executed and passing, cited as evidence rather than as proof:

- `tests/test_image_resolution.py::test_an_override_against_an_unbuilt_commit_reports_the_unbuilt_commit`
- `tests/test_image_resolution.py::test_an_override_naming_a_digest_this_commit_published_is_honoured`

### Check 6 — A GPU run claiming a team other than platform writes its checkpoint successfully.

**Status: DEFERRED**

Deferred because:

NOTHING IS MISSING BUT THE RUN. The GPU workload role already reaches teams/*/runs/* -- item 5.5 widened it to match the CPU role -- so a checkpoint under any team is permitted, and the checkpoint machinery is what Phase 4 proved on three GPU jobs under team platform. What has not happened is the two together. THE DEFERRAL WAS WITHDRAWN ONCE ON 2026-07-31 AND RE-GRANTED THE SAME DAY AGAINST A DIFFERENT MECHANISM, WHICH IS WORTH READING BEFORE TRUSTING IT. The first re-cut was granted against a page: a deferral may never be pilot-blocking, so what it stopped guarding had to be written where a reader could act on it, and the pilot limitations page was where that went. The page then left the README on a standing decision about what this repository publishes, the condition stopped holding, and the deferral was withdrawn rather than allowed to expire quietly. WHAT IT IS GRANTED AGAINST NOW IS NARROWER AND HARDER TO LOSE. The warning is printed on the run summary, to the submissions it applies to and not to the rest: a run carrying a checkpoint contract under a team other than platform is told it may be the first, and told what to check. That is delivered at the moment the person can act on it rather than on a page they would have had to open first, and test_the_first_gpu_checkpoint_under_a_new_team_is_warned_about_and_only_it_is fails if either the warning or its guard is removed. A page can be moved out of the README by an unrelated decision, which is exactly what happened; this cannot go quiet without a test going red.

Live again when:

One GPU submission claiming a team other than platform that writes a checkpoint closes it, and the criterion is re-recorded as COVERED or GAP against that capture rather than against this text. Phase 6's closeout campaign carries it as its first item. Until it runs this phase is not declarable complete, and if the campaign is abandoned this returns to a gap rather than remaining deferred.

Scope:

- THE PHASE'S CLAIM IS NARROWER THAN A GATE VERDICT WILL READ, WHICHEVER WAY THE VERDICT GOES. All three pilot runs went to cpu-32vcpu and the workload they carried was a print statement and two W&B calls, so none of them wrote a checkpoint and there is nothing to capture. No pilot run has trained anything or touched a GPU. What Phase 5 established is that the two-person path completes -- which had never been established in twenty-five prior dispatches and is the thing the phase is named after -- and not that this platform carries a research workload for somebody who did not build it. Closing this one criterion is not a pass on that larger question either.
- The team half is already demonstrated and is worth separating from the GPU half. Every one of the three pilot runs claimed team tokenizer -- the first team other than platform or the data-prep placeholder to appear in this store -- and the succeeded run's output prefix is teams/tokenizer/runs/. What is missing is a GPU run doing the same and writing a checkpoint at the end of it.
- THE HARM IS CARRIED BY A WARNING RATHER THAN BY THE MARKER, AND THIS IS WHAT THAT EXCHANGE COSTS. A checkpoint the workload role cannot write is a GPU run's whole output lost at the end of the run, after the money is spent. A deferral may never be pilot-blocking, so the marker comes off and a written limitation is what is owed in exchange: the person about to be first is told so on their own run summary and told what to check. That is a person checking rather than a system preventing, which is weaker, and it is the trade a deferral is. The trigger below is what ends it.

No test proves this check.

Supporting tests (4), all executed and passing, cited as evidence rather than as proof:

- `tests/test_pilot_limitations.py::test_the_first_gpu_checkpoint_under_a_new_team_is_warned_about_and_only_it_is`
- `tests/test_phase5_team_isolation.py::test_both_workload_roles_reach_every_team_by_decision`
- `tests/test_phase5_team_isolation.py::test_the_prefix_the_roles_grant_is_the_prefix_the_platform_derives`
- `tests/test_phase5_run_evidence.py::test_a_run_that_succeeded_recorded_where_its_output_went`

### Check 7 — A refused submission tells the submitter which refusal it was, in the workflow, with no account id disclosed.

**Status: COVERED**

Scope:

- Both halves are one criterion because they pull against each other. The useful thing to print is the state machine's own error and cause, and that is exactly the text most likely to carry an account id -- so a workflow that satisfied the first half by printing everything would fail the second, and one that satisfied the second by printing a bare token would reproduce the defect this criterion exists to close.
- Proved against the workflow rather than against a refusal somebody received, and that is one step weaker. No pilot submission has been refused on its merits: the two failed dispatches today were a tool invoked without a required argument and a container that could not start, neither of which is a refusal. What is asserted is what the workflow does with a refusal it is given.
- The sibling defect is what makes this worth its marker. A pilot user's first image build failed on a page reading remote_ref_mismatch and nothing else, which names a condition rather than a cause -- the same shape of failure in the build workflow rather than in this one.

Proving tests (2), all executed and passing:

- `tests/test_phase2_submit_run_workflow.py::test_a_refused_submission_tells_the_submitter_which_refusal_and_where_to_read_it`
- `tests/test_phase2_submit_run_workflow.py::test_the_cause_of_a_refusal_is_masked_before_it_reaches_the_step_summary`

Supporting tests (3), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase2_submit_run_workflow.py::test_a_refusal_with_no_error_or_cause_says_none_rather_than_printing_null`
- `tests/test_phase2_submit_run_workflow.py::test_no_denial_matrix_failure_echoes_the_account_id`
- `tests/test_phase2_submit_run_workflow.py::test_any_other_start_execution_error_fails_without_echoing_the_account_id`

### Check 8 — An accepted run tells the submitter where its output, logs and W&B project are.

**Status: COVERED**

Scope:

- WHAT THIS PROVES IS THAT THE SUBMITTER IS TOLD, AND NOT THAT WHAT THEY ARE TOLD IS REACHABLE. The step names the run id, the execution, the Batch job and queue, the log group, the S3 output prefix and the W&B project, and every one is derived rather than written down a second time. The prefix in particular comes from contracts/results.py, which exists because three places once answered that question and two of them agreed.
- THE W&B LEG WAS TRUE AND USELESS ON THE CPU PROFILE WHEN THE THIRD PILOT RUN MET IT, AND IT IS NOW CLOSED. The summary always named the project honestly, but CONTAINER_SHAPES['cpu-32vcpu'] declared secrets=() while gpu-1xa10g named the W&B secret, so no CPU run could authenticate and that run died on 'No API key configured' -- pointed at a project nothing could write to. Both halves are fixed: the CPU execution role may read the secret and the CPU job definition injects it, so the two profiles now carry the same secrets. The three runs this bundle rests on predate the fix, which is why none of them has a W&B run to show.
- No URL is invented for the W&B run, deliberately. A run is named for its run id, but W&B mints its own id for the URL and the entity belongs to the API key rather than to any reviewed configuration in this repository, so what can be stated truthfully is the project and the name to search for. Recording the run itself in lineage is Phase 7 item 7.4: lifecycle_projection hardcodes wandb_run=None on every result manifest, which the supporting test asserts rather than works around.

Proving tests (2), all executed and passing:

- `tests/test_phase2_submit_run_workflow.py::test_an_accepted_submission_says_where_every_trace_of_it_will_be`
- `tests/test_phase2_submit_run_workflow.py::test_the_output_prefix_it_prints_is_the_one_the_container_is_told`

Supporting tests (2), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase5_run_evidence.py::test_a_run_that_succeeded_recorded_where_its_output_went`
- `tests/test_phase5_run_evidence.py::test_the_result_manifest_still_names_no_weights_and_biases_run`

### Check 9 — A member with write access cannot trigger a deploy workflow.

**Status: COVERED**

Scope:

- NOT BUILT THE WAY THE PLAN SPECIFIED, AND THE SUBSTITUTION IS THE INTERESTING PART. The plan asked for a repository actor rule in evaluate mode. The organization is on the free plan, where ruleset enforcement is active or disabled and evaluate is Enterprise Cloud only, so 'measured before it refuses anything' was unavailable. An environment gate is worse than it looks: infra/iam/infra-deployer-role.yaml pins the OIDC subject with StringLike to a ref, and naming an environment on a deploy job rewrites that claim and silently revokes every deployment.
- What shipped is a guard step, first in each deploy job and before the checkout, failing rather than skipping, tied to the admin list in config/organization.yaml. It guards the dispatch path only, which is why the push-to-main case is cited too: a control that also blocked the merge path would have stopped deployment entirely.
- Unmarked for the pilot rung because it is a condition on granting write access rather than a guard on a run. Nobody's run loses money, data, attribution or lineage integrity if this is absent; what is at risk is the account, which is why it landed in the same change as the grant rather than after it.
- Three copies of the guard exist and are asserted word for word identical, because three copies that drift are one workflow silently unguarded.

Proving tests (6), all executed and passing:

- `tests/test_deploy_authorization.py::test_every_deploy_workflow_refuses_a_dispatch_before_it_does_anything[deploy-phase1-ecr.yml]`
- `tests/test_deploy_authorization.py::test_every_deploy_workflow_refuses_a_dispatch_before_it_does_anything[deploy-phase2-admission.yml]`
- `tests/test_deploy_authorization.py::test_every_deploy_workflow_refuses_a_dispatch_before_it_does_anything[deploy-phase3-batch.yml]`
- `tests/test_deploy_authorization.py::test_the_actors_a_deploy_workflow_accepts_are_the_roster_admins[deploy-phase1-ecr.yml]`
- `tests/test_deploy_authorization.py::test_the_actors_a_deploy_workflow_accepts_are_the_roster_admins[deploy-phase2-admission.yml]`
- `tests/test_deploy_authorization.py::test_the_actors_a_deploy_workflow_accepts_are_the_roster_admins[deploy-phase3-batch.yml]`

Supporting tests (8), all executed and passing, cited as evidence rather than as proof:

- `tests/test_deploy_authorization.py::test_a_refused_dispatch_fails_rather_than_reporting_itself_as_skipped[deploy-phase1-ecr.yml]`
- `tests/test_deploy_authorization.py::test_a_refused_dispatch_fails_rather_than_reporting_itself_as_skipped[deploy-phase2-admission.yml]`
- `tests/test_deploy_authorization.py::test_a_refused_dispatch_fails_rather_than_reporting_itself_as_skipped[deploy-phase3-batch.yml]`
- `tests/test_deploy_authorization.py::test_a_push_to_main_deploys_without_meeting_the_guard[deploy-phase1-ecr.yml]`
- `tests/test_deploy_authorization.py::test_a_push_to_main_deploys_without_meeting_the_guard[deploy-phase2-admission.yml]`
- `tests/test_deploy_authorization.py::test_a_push_to_main_deploys_without_meeting_the_guard[deploy-phase3-batch.yml]`
- `tests/test_deploy_authorization.py::test_the_three_deploy_workflows_carry_the_same_guard_word_for_word`
- `tests/test_deploy_authorization.py::test_no_deploy_workflow_exists_that_this_module_does_not_know_about`

### Check 10 — A member with write access cannot merge a workflow change without a code-owner review.

**Status: COVERED**

Scope:

- THE MASTER PLAN'S SENTENCE IS UNQUALIFIED AND THIS ONE IS NOT, AND THE NARROWING IS THE POINT. The plan asks that a change to a workflow file cannot reach main without a code-owner review. That is false for the three admins, because enforce_admins is off -- and it stays off by decision rather than by omission: turning it on makes every pull request the author writes wait on the one other code owner, on a repository where the author is writing most of them.

So the criterion is about what a member may do. A gate asserting the unqualified sentence would be asserting something untrue about this account, which is worse than a narrower claim that holds. The captured record carries enforce_admins as a field rather than leaving it out, so a reader sees the limit rather than inferring it from an assertion nobody made.
- CODEOWNERS had to be widened before the grant, not after. It covered the workflow files and the infrastructure and left the admission validator's own source and the policy it enforces uncovered -- and tools/build_admission_lambda.py copies config/*.yaml and the whole src/edullm_platform tree into the zip, so a change to either decides whether a run is authorized. That makes them the same kind of path as a workflow file, which is why the proving test walks the packaged set rather than checking that the file merely exists.
- The required checks are asserted beside the review, because a code-owner review with nothing else behind it lets a member merge a red branch, which is the same bypass by another route.
- This rests on a capture of how a branch is protected, which is a statement about one moment and is one browser click from being false. The record is a FreshEvidenceModel, so thirty days after it was taken it stops loading, the cited tests fail and this criterion is a gap again with the gate red. That is the window working.

Proving tests (2), all executed and passing:

- `tests/test_phase5_run_evidence.py::test_the_default_branch_requires_a_review_from_somebody_who_owns_the_code`
- `tests/test_phase5_run_evidence.py::test_every_path_a_released_lambda_packages_is_owned_by_a_code_owner`

Supporting tests (1), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase5_run_evidence.py::test_the_control_binds_members_and_the_admins_may_still_bypass_it`

### Check 11 — Every accepted submission tells the submitter, on the summary it ends on, that cancelling does not stop the job, that a checkpoint omits optimizer state, and that team routes approval rather than granting permission.

**Status: COVERED**

Scope:

- THE STATEMENT WAS REWRITTEN ON 2026-07-31 AND THIS RECORDS WHAT IT USED TO ASK FOR, BECAUSE A CRITERION THAT CHANGES TO MATCH WHAT WAS BUILT IS THE MOVE THIS LADDER EXISTS TO CATCH. It previously read: the pilot limitations page is in the README and names the checkpoint's missing optimizer state, the absence of cancellation, and that team routes approval rather than granting permission. The page left the README that day on the owner's standing decision that the README is a public artifact and a candid list of what this platform has not finished is not. That decision is settled and is not what this criterion measures.
- WHAT WAS GIVEN UP IS REAL AND IS NOT RECOVERED BY THE REWRITE. The full page said more than three things and said them in one place a reader could review before deciding to use the platform at all. That page is now readable on one laptop. Nothing here claims otherwise, and the candid assessment being private is a cost carried rather than closed.
- WHAT IS DELIVERED INSTEAD IS NARROWER AND LANDS HARDER, WHICH IS WHY THIS IS COVERED RATHER THAN DEFERRED. The three facts above are the load-bearing subset: each is discovered by being caught out by it, and each is expensive when it is -- the spend that continues after a cancellation, the resumed run whose optimizer restarted cold, the user who reads team as an access control. They are printed on the summary every accepted submission ends on, which is read by every submitter at the moment each wrong belief would otherwise form, rather than on a page a reader had to already decide to open. A README section is the weaker delivery of the two and was not chosen for being stronger.
- THE FOURTH TEST IS THE ONE THAT MATTERS AND IS NOT REDUNDANT. Each sentence could be true of a document nobody opens; test_all_three_are_on_the_page_every_accepted_submission_ends_on asserts they arrive together under one heading on the summary a submitter is already reading to find their run id. Split them across three places and every other test here still passes while the property is gone.
- The cancellation wording is fixed rather than free, and the test asserts the sentence rather than the topic. Phase 3's three cancellation criteria transfer to Phase 8 on the condition that a user is told, in words they can act on, that cancelling the workflow does not stop the job -- so wording that mentioned cancellation vaguely would quietly withdraw the grounds for that transfer.
- test_the_readme_does_not_carry_the_section_the_record_says_it_lost stays cited, and is the check that a recorded absence is a real one. Within an hour of the section being removed, an editor holding the file wrote its buffer back and the section returned while this criterion went on reporting it private, and nothing failed because the tests that read the section had just been deleted.

Proving tests (4), all executed and passing:

- `tests/test_pilot_limitations.py::test_a_submitter_is_told_that_cancelling_does_not_stop_the_job`
- `tests/test_pilot_limitations.py::test_a_submitter_is_told_the_checkpoint_leaves_the_optimizer_behind`
- `tests/test_pilot_limitations.py::test_a_submitter_is_told_that_team_routes_approval_rather_than_granting_access`
- `tests/test_pilot_limitations.py::test_all_three_are_on_the_page_every_accepted_submission_ends_on`

Supporting tests (3), all executed and passing, cited as evidence rather than as proof:

- `tests/test_pilot_limitations.py::test_the_readme_does_not_carry_the_section_the_record_says_it_lost`
- `tests/test_pilot_limitations.py::test_the_page_discloses_no_account_id_and_no_credential`
- `tests/test_pilot_limitations.py::test_the_summary_a_submitter_reads_discloses_no_account_id_and_no_credential`

### Check 12 — A submission that supplies a commit and no digest resolves to the image published from that commit.

**Status: COVERED**

Scope:

- The positive path, and the one the form now depends on. Its absence is a lineage record naming a commit that did not produce the image, arrived at by a different route than the mismatch criterion 5 refuses -- a comparison says a wrong pair is caught and says nothing about what the right one resolves to.
- This is what demoted image_digest from a required field to an optional override. Asking a person who did not build this platform to copy seventy-one characters out of another repository's job log is exactly the friction the phase exists to delete, and a derivation makes the mismatch unrepresentable rather than merely refused.
- The live half is cited as supporting rather than proving. The pilot runs' manifests carry a digest, because the resolve job fills it in before compile -- so what they demonstrate is that the resolved digest is the one the commit published, not that a submitter left the field empty.

Proving tests (2), all executed and passing:

- `tests/test_image_resolution.py::test_a_commit_with_exactly_one_published_image_resolves_to_that_image`
- `tests/test_image_resolution.py::test_the_same_commit_resolves_to_the_same_image_every_time_it_is_asked`

Supporting tests (1), all executed and passing, cited as evidence rather than as proof:

- `tests/test_phase5_run_evidence.py::test_the_image_the_pilot_runs_were_admitted_on_was_published_from_their_commit`

### Check 13 — A commit with no published image is refused at compile, with a reason naming the build workflow.

**Status: COVERED**

Scope:

- Unmarked because the submission cannot resolve to an image either way, so what this buys is the position of the refusal and the quality of its message rather than money, data, attribution or a lineage record. It is worth building anyway, because today's failure is the misleading one: an unbuilt commit used to reach admission and be refused for unreviewed scan findings, which blames the submitter's image for something nothing looked at.
- The message names the build workflow rather than describing the state, which is the difference between a refusal a reader can act on and one they have to interpret. The same distinction is what criterion 7 is about one job earlier.

Proving tests (1), all executed and passing:

- `tests/test_image_resolution.py::test_a_commit_with_no_published_image_is_refused_and_the_message_names_the_build_workflow`

Supporting tests (1), all executed and passing, cited as evidence rather than as proof:

- `tests/test_image_resolution.py::test_an_override_against_an_unbuilt_commit_reports_the_unbuilt_commit`

### Check 14 — A commit publishes at most one image, so a submission resolving from a commit has no rebuild to choose between.

**Status: COVERED**

Scope:

- THE STATEMENT WAS REWRITTEN WHEN THE PUBLISH PATH WAS MEASURED, AND THE OLD ONE IS HERE SO THE CHANGE IS LEGIBLE. It read: 'A commit built more than once resolves deterministically to the most recently published image, and the decision record names which digest was chosen.' It was marked pilot-blocking on the reasoning that an unrecorded choice among several is an attribution loss no later query can repair.

The state it describes cannot occur. Three mechanisms hold at once: the tag is the first twelve characters of the commit and carries nothing that varies between builds; both ECR repositories set ImageTagMutability to IMMUTABLE, so a tag cannot be moved; and build-research-image.yml resolves the tag in a pre-flight step and skips the build entirely when it is already published, so a re-run resumes onto the existing digest rather than pushing beside it. A commit therefore publishes at most one image.

The master plan's premise that Phase 1 measured a single commit built four times is about LOCAL rebuilds recorded under fixtures/evidence/phase-1/rebuild/, which were never pushed to a registry. That is a different claim and it does not reach this.

So this is retired as written and replaced by the property that survives, which is stronger: not 'the choice is recorded' but 'there is no choice to make'. It is not recorded as covered against the old sentence, because nothing exercises the most-recent-wins rule and pretending otherwise is the thing the three-status rule exists to make impossible. The rules themselves stay in image_resolution.py as unreachable defence, with a comment saying which three configuration choices would make them live -- so a later reader neither mistakes them for exercised behaviour nor deletes them without knowing what they guard.

It keeps its pilot-blocking marker. The harm it guards has not changed: if any of the three mechanisms is relaxed, a commit can publish twice and the lineage record starts naming an image nobody chose.
- The two rules for a state that cannot occur are cited as supporting rather than deleted from the citation list. They are correct, they are tested, and nothing reaches them -- so citing them as proving would claim the platform exhibits behaviour it cannot, and dropping them would leave a reader of image_resolution.py with a rule and no indication that anybody had thought about whether it runs.
- There is a residual and it is not this criterion's. Two commits sharing a twelve-hex-character prefix cannot both publish, and under derivation the second would resolve to the first's image -- a lineage record naming commit B for an image commit A produced. Forty-eight bits makes it negligible, the build workflow already refuses the colliding build by verifying the published image against the commit, and the tag stays twelve characters because widening it would falsify two committed Phase 1 captures and dissolve the rationale for the one field exempt from the secret scan. It was on the limitations page until that page was taken out of the README on 2026-07-31, and it is now recorded here and nowhere a pilot user reads.

Proving tests (1), all executed and passing:

- `tests/test_image_resolution.py::test_the_publish_path_cannot_produce_the_second_image_those_two_branches_need`

Supporting tests (3), all executed and passing, cited as evidence rather than as proof:

- `tests/test_image_resolution.py::test_resolve_image_records_that_nothing_reaches_the_multi_image_branches`
- `tests/test_image_resolution.py::test_a_commit_built_more_than_once_resolves_to_the_most_recently_published_image`
- `tests/test_image_resolution.py::test_two_images_pushed_at_the_same_instant_are_refused_rather_than_picked_between`

### Check 15 — An image_digest override naming a digest published from a different commit is refused.

**Status: COVERED**

Scope:

- The override is the one path by which a submitter can still supply a digest beside a commit, so this is the check that stops the surviving field reopening the hole derivation closes. It is a different claim from criterion 5 despite resting on the same refusal: criterion 5 is about where the refusal lands, and this is about the field still existing.
- The proving citation is the case that is HONOURED, which reads backwards and is deliberate. A rule that refused every override would satisfy the criterion's sentence and destroy the capability it is scoping -- a deliberate rebuild-and-pin has to stay available -- so what proves the override is bounded rather than removed is that a legitimate one still works. The refusal is cited beside it.

Proving tests (1), all executed and passing:

- `tests/test_image_resolution.py::test_an_override_naming_a_digest_this_commit_published_is_honoured`

Supporting tests (1), all executed and passing, cited as evidence rather than as proof:

- `tests/test_image_resolution.py::test_an_override_naming_a_digest_from_a_different_commit_is_refused`
