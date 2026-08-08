# The downstream lane

Turning a checkpoint the capacity block wrote into a model somebody can type at. Access, the form, the run id and stopping a run are in [`the-platform.md`](the-platform.md); training is in [`olmo-core.md`](olmo-core.md); task evaluation is in [`olmo-eval-full.md`](olmo-eval-full.md).

The chain has five links and four of them were measured on a GPU on 2026-08-08. The `basis` column of every table below says which claims were run and which are read off configuration.

**Somebody can chat with a model today, and [link three](#three-a-chat-surface) is the three commands that do it.** Everything under it was run against a real base model on an L40S: no chat template, four turns in context, a page in a browser on a laptop. Start there if that is what you came for; the rest of this document is why each step is the step it is.

## vLLM serves the block's architecture, natively, under the name the export writes

**`architectures` is the whole question and it is `FlexOlmoForCausalLM`.** vLLM dispatches on that single string in `config.json`, matched exactly against `_TEXT_GENERATION_MODELS` in `vllm/model_executor/models/registry.py`. An architecture it implements under a different spelling fails at load identically to one it never implemented, so "basically supported" is not an answer — the string has to match, and it does.

The chain that produces it is worth knowing because every link is a place it could stop matching. `olmo_core.nn.hf.config.get_hf_config` returns a `transformers.FlexOlmoConfig` for any `MoETransformer`. `save_hf_model` builds a model from that config with `AutoModelForCausalLM.from_config` and calls `save_pretrained`, which records the instantiated class's name. `transformers` maps `FlexOlmoConfig` to `FlexOlmoForCausalLM`. vLLM registers `FlexOlmoForCausalLM` to `vllm.model_executor.models.flex_olmo`, and overrides the config class for `model_type: flex_olmo` with its own in `_CONFIG_REGISTRY`, which is what satisfies the `isinstance` assertion in `FlexOlmoAttention`.

| Claim | Answer | Basis |
| --- | --- | --- |
| `olmoe_7b_32x4` exports as | `FlexOlmoForCausalLM`, `model_type: flex_olmo` | Run, on a meta-device build of the real 7.12B recipe |
| vLLM registers that name | Yes, natively, in `_TEXT_GENERATION_MODELS` | Run, against the installed vLLM 0.19.1 and read on `main` |
| vLLM loads a real export of it | Yes, fused-MoE kernel, tokens out | Run, on an L40S |
| `OlmoeForCausalLM`, `Olmo2ForCausalLM`, `Olmo3ForCausalLM` | Also registered | Read, `vllm/model_executor/models/registry.py` |
| Shared-expert arm exports | No, and cannot | Read, `.edullm/train_on_corpus.py` refuses it at config time |

**The demo is a configuration exercise and not a model definition to write.** Budget hours, not days.

The one thing that does *not* survive the trip is the shared-expert arm. HuggingFace has no MoE architecture with an always-on expert, so `FlexOlmoConfig` has no field for one, and `--hf-export` with `--moe-shared-experts` is refused before training starts rather than at the first export. That arm is comparable on validation loss and is not demonstrable.

Ask the question again, cheaply, whenever the recipe moves:

```bash
python src/scripts/downstream_lane/check_export_is_servable.py --factory olmoe_7b_32x4
python src/scripts/downstream_lane/check_export_is_servable.py --exported-dir /path/to/hf
```

## What runs, in order

Each step assumes the one above it. Stop at the first failure, because a fix below a break is a fix to something that was never reached.

### One: checkpoint to HuggingFace

`src/examples/huggingface/convert_checkpoint_to_hf.py` already does this and needs no wrapper. It reads the experiment config out of the checkpoint directory, so a run's own `config.json` supplies the model shape and the tokenizer.

```bash
python src/examples/huggingface/convert_checkpoint_to_hf.py \
  -i "$EDULLM_CHECKPOINT_DIR/step10000" \
  -o /work/hf/step10000 \
  --max-sequence-length 4096 \
  --device cuda --validation-device cuda
```

**`--device cuda` is not a performance choice for a MoE, it is the only way this runs.** A dropless MoE routes through `olmo_core.ops.moe`, whose gather and scatter are Triton kernels, and Triton refuses a CPU pointer on a host where CUDA is present. Conversion of a MoE therefore needs a GPU allocated to it even though the tensors would fit in host memory, which is a fact about the code path rather than about the model's size. The export step of this lane is a GPU job.

**`--max-sequence-length` is not optional either.** `get_hf_config` writes `max_position_embeddings=-1`, and the converter overwrites it only from that flag or from a tokenizer that carries `model_max_length`. An export that got neither hands vLLM -1 as its context window, which fails in a way that looks like a broken model.

Leave validation on. It runs both models over the same tokens and compares logits; without it the step produces a directory rather than a proof. The MoE conversion was broken outright until 2026-08-07 and was fixed on `edullm/hf-converter-and-evals`, which `edullm/base-run-spec` descends from — anything older converts every architecture except the one the block is training.

### Two: HuggingFace to vLLM

Nothing to configure beyond the context length. The export loads through vLLM's native `flex_olmo` implementation, the fused MoE kernel initialises for 32 experts, and tokens come out. Measured on a `g6e.xlarge`, cold start under a minute for a small model.

### Three: a chat surface

This is the part somebody types into, and it is three commands. They live on `edullm/a-thing-to-type-into` in OLMo-core, which descends from `edullm/downstream-lane`.

```bash
edullm run   --project the-demo --compute gpu-1xl40s --hours 4 \
  -- bash src/scripts/downstream_lane/start_the_demo.sh s3://.../run/checkpoints
edullm shell --project the-demo --notebook      # then open http://localhost:8890/
edullm stop  --project the-demo
```

The first returns when there is something to connect to and not when the command was accepted. The second is a tunnel and nothing else; the servers keep running when you close it, and re-running it reconnects. The third is what stops the bill.

**Point it at the run's checkpoint directory rather than at a step, because the step you want is not the one you can see.** `serve_a_checkpoint.py` works out for itself whether it was handed a run directory, a single checkpoint, an export or a hub id, and does only the work that shape needs. Against a run directory it takes the highest step that passes `Checkpointer.dir_is_checkpoint` — the same test the loader applies — and not the highest step in the listing. A job that is still training has a directory for the step it is writing right now: listed, largest, and unloadable. Sorting a listing therefore picks the one checkpoint guaranteed to fail, several minutes into reading its weights, on a machine that is already paid for. Serving a live run is the ordinary case here, because the model anybody wants to show is being trained the same week.

**`/v1/chat/completions` answers because a template is installed on the way past, and a post-trained export keeps its own.** The 400 above is not a defect to work around; it is the absence of something post-training writes. `base_model_chat_template.jinja` supplies it, `serve_a_checkpoint.py` writes it into `tokenizer_config.json` where there is no `chat_template` key already, and passes the same file to the server with `--chat-template`. A checkpoint that arrives with a template is left alone, because replacing turn markers a model was tuned on with markers it has never seen makes a good model look like a bad one. **This is what stops the demonstration depending on the SFT run happening.**

**The template is plain `User:`/`Assistant:` text and not ChatML, and that is a decision about the model rather than about convention.** dolma2 carries `<|im_start|>` and `<|im_end|>` at 100264 and 100265, so a ChatML template tokenises cleanly and inspects correctly in every way available without a GPU. It is still wrong here: those ids occur essentially nowhere in a pretraining corpus, their embeddings are wherever initialisation left them, and a base model prompted with them drifts. Ordinary words in a transcript layout are what makes a base model answer in the shape of a reply at all.

**A base model does not end its turn, so the reply is stopped on the blank line after it.** This was the one thing that had to be measured rather than reasoned about. Stopping on `"\nUser:"` looks obviously right and does not work: asked what language is spoken in Tokyo, `OLMoE-1B-7B-0924` answered *"Japanese."* and then continued into a Slashdot comment thread for the remaining three hundred tokens without writing `User:` anywhere. A base model continues the document it was given, and once the answer is finished the likely continuation is whatever usually follows a paragraph on the internet. What it does not do is run two paragraphs together. Stopping on `"\n\n"` costs a genuinely multi-paragraph reply, which a model that has not been taught to write one rarely produces, and it turns the same four questions into four clean answers.

**The stop strings live in the front end's proxy and not in the server, so a client that goes straight to port 8000 gets the rambling version.** vLLM has no flag for default stop strings and its `generation_config` support does not carry them. Anything driving the endpoint directly — a script, a rehearsal, a second front end — has to send `"stop": ["\n\n", "\nUser:"]` itself. This disappears with post-training, which installs a token the model emits on purpose.

**The front end is one file importing only the standard library, and Gradio was rejected on dependency risk rather than on taste.** Nothing in either repository has ever used Gradio, Streamlit or Chainlit, so there is no precedent to follow and no wheel already in an image. Choosing it means resolving its `fastapi`, `pydantic` and `httpx` pins against vLLM's, in the same interpreter, on the demonstration machine, hours before the demonstration — an argument a file with no imports cannot lose. Its share link was the other attraction and is the other reason: it relays through a third party, and a demonstration whose reachability depends on somebody else's uptime has a failure mode nobody in the room can fix.

**The page proxies rather than letting the browser reach vLLM, which buys one origin, one port and one place for the stop strings.** Same origin means no CORS flags to edit on the day. One port means one Systems Manager forward, and the lane opens exactly one. Measured overhead is nothing: 0.02s to first token on the machine, 0.07s through the proxy and the tunnel together, from a laptop.

**It binds 8888 because that is the one port on a lane machine a laptop can reach.** The lane's security group holds zero ingress rules deliberately, so nothing on the machine is addressable from anywhere, and the only route in is the forward `edullm shell --notebook` opens — wired to 8888 on the machine and 8890 on the laptop. Binding there makes reachability a verb that already exists instead of a security-group amendment and a review. Jupyter wants the same port; on a machine whose job is to serve a demonstration that is not a cost.

**The tunnel is the right answer for a room and it is presenter-driven, which is the tradeoff to know before Thursday.** It is the only option needing no new infrastructure, it puts no third party in the path, it survives conference wifi because it is an outbound connection to Systems Manager rather than an inbound one to the machine, and a dropped connection is repaired by re-running one verb while the servers keep running and the transcript on screen stays intact. What it does not do is let the audience open it on their own phones — that wants a load balancer and a certificate, which is infrastructure and a review rather than an afternoon. If the forward will not open, `edullm studio --project the-demo` reaches a GPU space over an authenticated HTTPS proxy with no local plugin at all; it is the fallback because it is a second machine, a second disk and a second rate. The last resort is `edullm run -- curl` against the endpoint, which is a conversation in a terminal and still a conversation.

**Three failure modes were built for, and the page is dull on purpose.** A slow first token shows a caret rather than looking hung. A dropped endpoint takes the failed exchange back out of both the transcript and the page and returns the question to the input box, because leaving two bubbles on screen that are no longer in the history being sent puts the page and the next request into disagreement and the audience is reading the wrong one. An endpoint that dies mid-reply arrives as `IncompleteRead`; uncaught it took the handler thread with it and closed the socket on an unfinished chunk, which a browser reports as a bare network error with the tokens already on screen thrown away, so it now ends the stream with a sentence inside it and keeps them. Typing while a reply streams is allowed and the draft survives.

| Measured on one `g6e.xlarge`, `OLMoE-1B-7B-0924`, 2026-08-08 | |
| --- | --- |
| Cold start, command to answering endpoint, weights on local disk | 41s |
| Cold start on a machine with no vLLM and no weights | add ~4 min for `pip install vllm`, ~2 min for a 13 GB pull |
| Time to first token, on the machine | 0.02s |
| Time to first token, laptop through the proxy and the tunnel | 0.07s |
| One person | 256 tok/s |
| Three people at once | 123–136 tok/s each, so about 400 aggregate |
| Prompt of ~500 tokens rather than ~10 | 245 tok/s, first token unchanged |

**Nothing in that table is the bottleneck a demonstration will hit, and the thing that will is the first two rows.** Four hundred tokens a second across three streams is faster than three people read. A cold start of four to seven minutes on a fresh machine is not, so start the machine before the room fills; `edullm run` finds a machine that is already up rather than starting a second one, so starting early costs only the hours it runs.

**What changes when the block's own checkpoint replaces the stand-in is one step and one number.** The step is the conversion in [link one](#one-checkpoint-to-huggingface), which `serve_a_checkpoint.py` runs by itself when the URI is a checkpoint rather than an export — with `--device cuda`, because a dropless MoE routes through Triton, and with a sequence length, because `get_hf_config` writes -1. It is the only part of this that has not been run end to end from an eduLLM URI, for the plain reason that no eduLLM checkpoint exists to point at yet; `s3://edullm-olmo-370m-ckpts/` is not a bucket. The number is the card: `OLMoE-1B-7B-0924` is 7B with 1B active and fits an L40S with room to spare, and the block's `olmoe_7b_32x4` is 7.12B, so `gpu-1xl40s` remains the shape. Nothing about the template, the stop strings, the page, the port or the tunnel changes, and none of it is specific to the model it was proven against.

### Four: scoring a checkpoint

Three different things get called evaluation here and only two of them exist.

| What | State | Basis |
| --- | --- | --- |
| Held-out cross-entropy, in the training loop | Wired, on the corpus's own validation shards | Read, `LMEvaluatorCallbackConfig` in `.edullm/train_on_corpus.py` |
| Downstream task scores, offline, from a checkpoint URI | `OlmoCoreProvider` reads a sharded OLMo-core checkpoint directly; the image position that carries torch is built | Read, `olmo-eval-full` on `edullm/a-third-position-for-olmo-core` |
| Held-out cross-entropy, offline, from a checkpoint URI | Does not exist | — |

The pre-registered metric is the third row. The in-loop evaluator reports it while a run is alive and writes it into the run's metrics, which is enough for an arm that ran to completion; it is not enough for an arm whose training job has already finished when somebody wants the number recomputed, or for comparing two checkpoints the loop never scored together. Writing it is small — the model config comes out of the checkpoint's `config.json`, the held-out shards out of the same `NumpyDatasetConfig` the training run built, and the loop is the one `LMEvaluatorCallback` already runs, outside the trainer.

**`guides/olmo-eval-full.md`'s capability table is out of date on the second row.** It says the published image loads only `mock` because torch is absent. That was true of the position that was published; a third position carrying torch and `ai2-olmo-core` has since been built and its digest recorded, and `OlmoCoreProvider` is the only provider of the three that reads a sharded OLMo-core checkpoint without an export in between.

### Five: post-training

**`open_instruct` does not load weights with transformers, and that is the fact everything else follows from.** `olmo_core_finetune.py` builds an OLMo-core `Transformer` from a config *name*, parallelises it, and pours HF weights into that model's state dict with `olmo_core.nn.hf.load_hf_model`. So "can it SFT from an export" is two questions with different answers.

The weights question is now yes. Reading an exported MoE back was broken in two independent ways, both fixed on `edullm/downstream-lane` in OLMo-core: the fused expert parameters had no inverse, and the dropless permutation the exporter applies to `w1` and `w3` had none either. The second is the dangerous one, because undoing the first alone leaves every shape matching and every expert computing against a transposed weight. The round trip is verified against the same artifact vLLM served.

The naming question is still no. `get_transformer_config` resolves a string against `TransformerConfig` attributes, and `TransformerConfig.olmoe_7b_32x4` does not exist — the recipe lives as a function inside `.edullm/train_on_corpus.py`, deliberately, so that a submitted run cannot quietly read the upstream AI2 mix. `olmoe_1B_7B` exists and is a different model. Until the recipe is reachable by name, open-instruct cannot construct the model to pour weights into, however good the weights are.

**The shortest path skips HuggingFace entirely.** `olmo_core_finetune.py` loads an OLMo-core checkpoint directly with `trainer.load_checkpoint(..., load_trainer_state=False)` when the path is not an HF directory, which reaches none of the conversion code. Feed it the checkpoint the block wrote, not the export. What it still needs is the config name, so the smallest real change is one of: a `TransformerConfig` classmethod for the recipe, or an escape hatch in open-instruct's resolver that accepts a config dict.

One trap on the way: `is_hf_checkpoint` decides by asking `os.path.isabs`, so an `s3://` URI is classified as a HuggingFace hub id. Stage the checkpoint locally, or pass an absolute local path.

## What stands between here and Thursday

| Gap | Cost | Note |
| --- | --- | --- |
| The serving path has never met an eduLLM checkpoint | Half a day, once one exists | Everything above was proven against `OLMoE-1B-7B-0924`, which is the same family and needs no conversion. Ours needs the GPU conversion step first, and that step has been run on a MoE but never on a checkpoint the block wrote |
| Nobody can reach the demonstration except the presenter | Two days | The tunnel is per-laptop by construction. Audience-reachable means a load balancer, a certificate and a security-group amendment, which is a review rather than an afternoon |
| The bare endpoint rambles for any client that is not the page | An hour, and it may not be worth it | Stop strings are applied in the proxy because vLLM takes no default for them. A wrapper around `vllm serve` could inject them; post-training deletes the problem |
| `TransformerConfig` cannot name the recipe, so no post-training | Half a day | A classmethod, or an open-instruct resolver that takes a config dict. This is also what would retire the base-model chat template |
| `open-instruct` has no image on the platform | A day | `.edullm/Dockerfile` is written and pushed on `edullm/add-research-image`; it needs a registry entry and a build |
| No offline validation-loss scorer | Half a day | The loop already computes it; this runs the same evaluator outside the trainer |
| `OlmoCoreProvider` unproven against an eduLLM checkpoint | Half a day | Image position built, never pointed at one of our runs |
| The lane node cannot read training outputs | An hour, plus a review | `edullm-lane-instance` holds S3 rights on `edullm-scratch` and nothing else, so the machine that is supposed to post-train intermediate checkpoints cannot fetch one |
| Exporting a MoE needs a GPU | None, but plan for it | Triton, not memory. The lane node has to be a GPU node even for the conversion |

Four things about a lane machine that cost an attempt each, none of them documented anywhere and all of them cheap once known. **The image has `python3` and no `python`**, which the verb's own `127` message says and which every habit from a laptop breaks. **pip's console scripts land in `~/.local/bin`, which is not on the path**, so a clean `pip install vllm` leaves an importable library and no `vllm` command — a state that reads as a broken install rather than a missing directory. **`/work` is not writable**; the lane grants exactly one directory under it, the project's own, and a sibling for a downloaded model fails at `mkdir`, so the home directory is where large temporary things go. And **the executable bit does not survive the trip**: the lane ships a working tree by syncing it through S3, an object store has no mode to carry, and a script committed 755 arrives at 644 and refuses to run on a machine where `ls` plainly shows it. Call shell scripts through `bash`.

**`edullm stop` with a project that has no machine lists the projects that do**, which is the only way to find a machine somebody left running under a name they have forgotten. There is no verb for it and this is the closest thing; a stopped instance still bills its volume, and the lane's own expiry terminates rather than stopping, so anything left behind was left by something other than the lane.

Two defects in `edullm` itself, both hit on the way through and neither specific to this lane. The lane verbs assume the AWS region is in the environment: after `assume-role` they pass only the three credential variables, so `AWS_PROFILE` is dropped and with it the region `~/.aws/config` holds under that profile. Every regional call then fails with `NoRegion`, and `edullm run` reports that as *the platform's own network could not be found* — a message that names a deploy that has not happened, for a laptop that has not exported `AWS_REGION`. And `edullm stop` and `edullm studio` do not resolve the broker's profile the way `edullm run` does, so they answer *AWS would not say who you are* on a machine where `edullm run` has just worked. Exporting `AWS_REGION=us-east-1` and `AWS_PROFILE=sbsandbox` clears both.
