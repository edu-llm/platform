# Phase 5 image provenance evidence

How a commit became the container that ran, read from the registry and from the scheduler rather than from a template.

## The image the pilot runs were admitted on

| field | value |
| --- | --- |
| repository | sbsandbox-intern-edullm-olmo-core |
| declared commit | 8da0d6aa45b297feb25625fc9edf390b030c5c21 |
| published tag | 8da0d6aa45b2 |
| image digest | sha256:1cd62aca4ed4599b96d96728b41958b732196b993ea6c57be045de97185185a9 |
| pushed at | 2026-07-30T20:47:45+00:00 |
| tags in the repository | 9 |

The tag is the first twelve characters of the commit, which is what ties the digest to the commit rather than leaving two facts sitting beside each other. The capture refuses to load if the tag is not a prefix of the commit it names.

## One commit, one image

The registry holds nine tags and nine distinct images. That is not a coincidence, and check 14 is the criterion that says so. Three mechanisms hold at once: the tag carries nothing that varies between builds, both ECR repositories set `ImageTagMutability` to `IMMUTABLE`, and `build-research-image.yml` resolves the tag in a pre-flight step and skips the build entirely when it is already published.

**Check 14 was rewritten rather than retired silently, and the bundle should say so where a reviewer will see it.** It asked that a commit built more than once resolve deterministically to the most recently published image and that the decision record name the chosen digest. That state cannot occur through the only path that publishes, so the criterion was not untested -- it was unreachable by construction, which is a stronger outcome than the check was asking for. The criterion now states the property that survives and carries the retired sentence in a scope limit. The rules for the state that cannot occur remain in `image_resolution.py` as unreachable defence, cited as supporting rather than proving, with a comment recording which three configuration choices would make them live.

## No hand-written exception stood behind it

Check 3 is covered, and until the unit of review changed it was unpassable by construction rather than merely unproven. `config/image-exceptions.yaml` held two entries, each naming one image digest; an image is refused unless somebody has written its digest there; and every build produces a new digest. So exactly two digests in the world could be submitted, and every iteration needed a reviewed pull request from an admin before it could run -- which is the friction this platform removed from choosing an image, arriving one step to the left.

The per-digest list is empty now. What a reviewer actually did when writing those two entries was read four CVEs and decide they were acceptable, and the file records that instead: four reviewed vulnerabilities, all inherited from the digest-pinned base every image shares. A finding nobody has reviewed still refuses the run, which is the thing the per-digest form could not express.

The residual is stated where it applies. The registry is on BASIC scanning, which reads the operating system package database and does not look at Python distributions at all -- so the roughly three gigabytes of installed Python in this image was not scanned by anything, and "no unreviewed finding" is a statement about what was looked at.

## What each run declared and what it was given

| run | declared digest | container digest | agree |
| --- | --- | --- | --- |
| run_019fb4ce-cf24-7028-8eed-a32a28ec2493 | sha256:1cd62aca4ed4599b96d96728b41958b732196b993ea6c57be045de97185185a9 | sha256:1cd62aca4ed4599b96d96728b41958b732196b993ea6c57be045de97185185a9 | yes |
| run_019fb4f6-6679-708d-9bee-1ef5ccf5a002 | sha256:1cd62aca4ed4599b96d96728b41958b732196b993ea6c57be045de97185185a9 | sha256:1cd62aca4ed4599b96d96728b41958b732196b993ea6c57be045de97185185a9 | yes |
| run_019fb505-9b0f-70cc-b890-2c60037cfe41 | sha256:1cd62aca4ed4599b96d96728b41958b732196b993ea6c57be045de97185185a9 | sha256:1cd62aca4ed4599b96d96728b41958b732196b993ea6c57be045de97185185a9 | yes |

**A twelve-character tag is a collision surface and the residual is recorded rather than closed.** Two commits sharing a twelve-hex-character prefix cannot both publish, and under derivation the second would resolve to the first's image -- a lineage record naming commit B for an image commit A produced, which is the exact defect class this phase exists to close, arriving by a route nothing looks at. Forty-eight bits makes it negligible at this volume, and `build-research-image.yml` already refuses the colliding build by verifying the published image against the commit. The tag stays twelve characters because widening it would falsify two committed Phase 1 captures and dissolve the rationale for the one field exempt from the secret scan. It is on the pilot limitations page instead.
