# Phase 3 lineage record evidence

What S3 attests about every object these runs wrote. The writers asking for a checksum and the store having computed one are different claims: the first is read from the state machine definition elsewhere in this bundle, and only `head-object --checksum-mode ENABLED` establishes the second.

`loads` is the column worth reading twice. Three bindings here are attested, versioned and intact -- S3 holds exactly the bytes it was sent -- and are refused by the contract that defines what a binding is, because they were written before the `"Result": null` fix in the admission state machine and carry a whole admission payload where a fan-out size belongs. The lineage store is write-once, so those objects are permanent and no future capture repairs them.

## run_019fa73d-be37-7066-984b-a4bacf194f49

| key | kind | bytes | canonical | loads | VersionId | ChecksumSHA256 |
| --- | --- | --- | --- | --- | --- | --- |
| `att_019fa910-13ef-7af8-ad90-81b03811c034.json` | attempt | 316 | yes | yes | `OFxXHnZYHMNc9uvTM1bPbPIVJ7hkgkQj` | `p1KmQGmv7DPpFARl2wguYkpAXADDxD8cKPqH+aqI4+Q=` |
| `run_019fa73d-be37-7066-984b-a4bacf194f49.json` | binding | 26166 | **no** | **no** | `A9kodlnLf7zM.KbCdLZAK6Vkawl_pxAM` | `H5B/h99okudQc+WDZO/mQjG8Sr7nwT7UpVJPnaNs8pE=` |
| `run_019fa73d-be37-7066-984b-a4bacf194f49.json` | decision | 822 | yes | yes | `lgSVeEDSjNGFBWQ5EMjjLBglHYtwBXiL` | `sOsXOGKVr3n3XAkNVoz11iz6h85KV2AxmSX4NKHGO1I=` |
| `evt_0b479f87-f01b-6d6e-d5aa-b7877e9463ec.json` | events | 264 | yes | yes | `8ZzuIgOuj3wGlHBM3pQzdvYgFe2XyBmL` | `tKYQcNAv39D/QFX51ygJSLIJk6ls5ViysfeluWgy0TQ=` |
| `evt_1923a7f0-bc3d-e17d-c9c4-3fc498d67e13.json` | events | 228 | yes | yes | `KmW1sIRKWY1BXgyMJ6emy1Y6NPk4VbEy` | `K5jdJ5OYtm9OcGe4I1MtHQcEAKhXbqP8wljXF4wTM3M=` |
| `evt_5a876b9a-d3d4-8dc2-a61d-e39dbb1aac9a.json` | events | 227 | yes | yes | `YNiy9_FxhpOVcWwR7MkOYbELRVqqqaTU` | `pDLEJPY23E3/X5w1t/bGYKW7UeIsp8UgXwferajiQgI=` |
| `evt_67450957-0084-8e98-bf00-b8fa8a9d679c.json` | events | 228 | yes | yes | `CLZCUrVC6qDRJHb2SL1Kc9lmFvkkLvju` | `M5gTVfsERCGMJj34b+pDwWC5hdTS/4RYvzNXui4duwU=` |
| `run_019fa73d-be37-7066-984b-a4bacf194f49.json` | intent | 1030 | yes | yes | `jSlaNR2i4Nw0h6XfqrwZxIlkxDbhYSlI` | `TDMlMb1oqrB5QaPgCYgi57tGYhNC8XfaISmphXKq62w=` |
| `run_019fa73d-be37-7066-984b-a4bacf194f49.json` | result | 356 | yes | yes | `4NWOlqcTvArtXX10sfMCpivZikVlpiVG` | `kRWduE7jFxBPLd4EzhfZk9SrZ2QAcNrvq9dElneJ034=` |

Traceable end to end: **no** — unresolved: binding.

## run_019fa96f-8f10-705a-a7a9-69c42eafce16

| key | kind | bytes | canonical | loads | VersionId | ChecksumSHA256 |
| --- | --- | --- | --- | --- | --- | --- |
| `att_019fa974-10b2-74b7-86dd-0c93bc5cd76c.json` | attempt | 319 | yes | yes | `fiAw.nhgRFjjvk0RVhg9hlE3ir1yiYZe` | `gdzDLK+CSz+kprOXAlcOHu8IYzC0J3x559wBCHKMNGo=` |
| `run_019fa96f-8f10-705a-a7a9-69c42eafce16.json` | binding | 653 | **no** | yes | `K108y7GERVnUQ2.lFHkeK19owGFJW.yi` | `/s4H9mCRm2dNmi/CoBYtSP3AaYRKBNLXsuVltYJa9dc=` |
| `run_019fa96f-8f10-705a-a7a9-69c42eafce16.json` | decision | 822 | yes | yes | `8IfrW22UJO7d4kjaAIQIcNZ_hO8ClPOG` | `C1bgf09slsI8xTcIDJ6gv28sawK7oJ93dcdjjeW2hXg=` |
| `evt_3fda5d04-03d9-a280-e069-8d93e147cd30.json` | events | 227 | yes | yes | `6ZJKt8gXSbajd3Z_dF9o8zQlFYVnVQQw` | `wWA2hyJogPy4miYCS4bvsZFhrjnTiRc+fCRN4naKyWc=` |
| `evt_903dd012-f1e0-1fc1-ddd5-4907d0a61b79.json` | events | 228 | yes | yes | `EY7gXvdg8EF2o3nuMCjVa.2aW3UjuJW5` | `0t3Do66/1TV+MhhsuTuYw2CYiFX4EasZx3+7/fJ4HUo=` |
| `evt_ca5bbc7d-5878-04b3-efd0-bbcc13897869.json` | events | 267 | yes | yes | `vrUEyH2Le3MK5FfgD_MLZNKN9BMTBCMY` | `NiE5kucdWe10RFzv8Z9opF6hU8h8ne12ht8gkNbUv18=` |
| `evt_cb1af190-52a8-08d1-3024-da2bc096fde1.json` | events | 228 | yes | yes | `vIafUMaSPsfu2pGk57dlBK2_F2hbQgXC` | `4NIFBkR8Gw0pET7tQByw831+e9QikDJYPUGVcUcEaHY=` |
| `run_019fa96f-8f10-705a-a7a9-69c42eafce16.json` | intent | 1020 | yes | yes | `UwXmzR_DrMDMetMMCDq_GPQ6JqKAGcUL` | `2h6gLSToOoQZubqQ31F4UOG7A/4wgKe6NbKtBP4+C3k=` |
| `run_019fa96f-8f10-705a-a7a9-69c42eafce16.json` | result | 359 | yes | yes | `LHeqtfXTJSBt91DpTVn3mktsgg5eOG1N` | `nBTSdkcuLMqlfhu8YdtqJoo6bgra8lgReamEmmRh6Ig=` |

Traceable end to end: **yes**

## run_019fa984-085c-7088-9c94-799e4b5d9126

| key | kind | bytes | canonical | loads | VersionId | ChecksumSHA256 |
| --- | --- | --- | --- | --- | --- | --- |
| `run_019fa984-085c-7088-9c94-799e4b5d9126.json` | decision | 927 | yes | yes | `8bpmifzpXxep7Ql.9UobX9KHkXNPL0XO` | `B/sPKY60X6GgX3AGm3Jpj/95ySiFqPipzXqRpvgUPdc=` |
| `run_019fa984-085c-7088-9c94-799e4b5d9126.json` | intent | 999 | yes | yes | `ZNbV6cO0hpo51vlQ0TslHZbTH.6R.k.I` | `eVDFMYdnvmDRDAwtsoLaJCt+jvM5Y2PEdo+d0R2i5O0=` |

Traceable end to end: **no** — unresolved: oidc_session, binding, event, attempt, result, batch_job, log_stream.

## run_019fa9a6-4460-7095-a358-a1552e250f1b

| key | kind | bytes | canonical | loads | VersionId | ChecksumSHA256 |
| --- | --- | --- | --- | --- | --- | --- |
| `att_019fa9a9-a41d-7a7c-9412-8a344fde8790.json` | attempt | 316 | yes | yes | `s4PLc0O4mTfABs8GteUwY.ddg5GHCLCc` | `uG1v5jqei6jP8RSfY5Gt/fnduo2dMK4hiMlYKxnqO2U=` |
| `run_019fa9a6-4460-7095-a358-a1552e250f1b.json` | binding | 652 | **no** | yes | `vimdSsvj3h.Hm9b2L9IyZruGVvyvIQ4M` | `XpqGLzZ/+jdbApTuM0SrGuNUeOfGbWlJkuSynqWshkI=` |
| `run_019fa9a6-4460-7095-a358-a1552e250f1b.json` | decision | 825 | yes | yes | `LricTZGaj5PiErEItMxnOdZfQ1NITYDJ` | `62vWRe8da9uGdHdHerq+1u0iHikkKo7sO63CKr6w5lI=` |
| `evt_4ffdc4d5-dea6-0065-d172-6f638a23d80c.json` | events | 264 | yes | yes | `yQ93HBCF4_.Wmvc1v9dljkjSQCuqiQ4B` | `NnLtuI5yyKDj7m3NvqSlKBDjFsuXG+8GPhpwazq5SO8=` |
| `evt_b28c1cf5-48ce-1fb8-ccd1-5e38a49df100.json` | events | 228 | yes | yes | `eYJlnH1pHHNH43b_SsQsSvHvMUsamfpg` | `xaT2+GOvoNorHjbn73nElHtjlKV5Tj5dWCusBvCwBr4=` |
| `evt_f84d8b26-5214-3bf4-0e6e-6ddd7429ff33.json` | events | 227 | yes | yes | `xh0s0wXQ3hHs1ao7.IegjMXqceWcMoWc` | `4JW3g4eGiQdigdJQ+tqz3Pdl3ohRZ1C3HMoAFmEWPtI=` |
| `evt_fdd08554-dd22-4288-840c-1ba42d8845bb.json` | events | 228 | yes | yes | `bS1D5obkWMjZ4W3BQL8FyBCWWPXB2LVh` | `csn9b8rfiNY7dUd51F4diNl9Njb+IFqw9jZvhfQtKfs=` |
| `run_019fa9a6-4460-7095-a358-a1552e250f1b.json` | intent | 1044 | yes | yes | `1dCiuDv2Z3K0lBuZbVPOum.mabNMH3BS` | `Wd/5qhRtA/EnYRvOe8V0yA0zahSQF/vb2+IRRG0p11I=` |
| `run_019fa9a6-4460-7095-a358-a1552e250f1b.json` | result | 356 | yes | yes | `MMRw_949.4l.c7b7xLKfLKDKHXAPc.LK` | `YCBSJwCB/7uE7w0CxZcgeQ38vpTC6oclzytmliMQDXI=` |

Traceable end to end: **yes**

## The eleven artifacts one run id has to resolve to

Named rather than counted, in `phase3_capture.TRACEABLE_ARTIFACTS`, because a check that counted eleven would go on passing after somebody removed one and added another.

`github_workflow_run`, `oidc_session`, `admission_execution`, `intent`, `decision`, `binding`, `event`, `attempt`, `result`, `batch_job`, `log_stream`.

two of the four captured runs resolve all eleven. The others are the runs holding a binding that will never load, and they are reported as not traceable rather than as nearly traceable: an unbroken chain is the claim, and a chain missing a link is not a chain.
