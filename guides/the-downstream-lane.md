# The downstream lane

Turning a checkpoint the capacity block wrote into a model somebody can type at. Access, the form, the run id and stopping a run are in [`the-platform.md`](the-platform.md); training is in [`olmo-core.md`](olmo-core.md); task evaluation is in [`olmo-eval-full.md`](olmo-eval-full.md).

The chain has five links and four of them were measured on a GPU on 2026-08-08. The `basis` column of every table below says which claims were run and which are read off configuration.

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

**Use vLLM's OpenAI-compatible server, not `olmo_core.generate.chat`.** That module is a single-process rich terminal over an OLMo-core checkpoint: one person, one keyboard, weights reloaded per invocation, and marked beta. The server holds the weights across requests, speaks the protocol every chat front end already implements, and can be driven from a browser on somebody else's laptop — which is the only property that matters when the person typing at the presentation is not the person who built it.

```bash
src/scripts/downstream_lane/serve_exported_checkpoint.sh /work/hf/step10000 8000 edullm
curl -s localhost:8000/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"edullm","messages":[{"role":"user","content":"Hello"}],"max_tokens":64}'
```

**A base-model export has no chat template, so `/v1/chat/completions` returns 400 and only `/v1/completions` works.** The exported `tokenizer_config.json` carries no `chat_template` key, and since transformers 4.44 there is no default to fall back on. This is not a bug in the export; a chat template is something post-training installs, and a base model has not been post-trained. Either put a template in front of the server, or accept that the demo is completion rather than conversation, or run the SFT that would have written one. The same server, pointed at a post-trained OLMoE, holds a multi-turn conversation with no further configuration — verified against `allenai/OLMoE-1B-7B-0924-Instruct` on the same machine.

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
| Nothing serves the model yet — no endpoint, no front end | Half a day | The server is one command; a UI in front of it and somewhere to run it is the rest |
| No chat template on a base-model export | An hour, if a template is enough; a day if real SFT is wanted | Only blocks `/v1/chat/completions`, not the model |
| `TransformerConfig` cannot name the recipe, so no post-training | Half a day | A classmethod, or an open-instruct resolver that takes a config dict |
| `open-instruct` has no image on the platform | A day | `.edullm/Dockerfile` is written and pushed on `edullm/add-research-image`; it needs a registry entry and a build |
| No offline validation-loss scorer | Half a day | The loop already computes it; this runs the same evaluator outside the trainer |
| `OlmoCoreProvider` unproven against an eduLLM checkpoint | Half a day | Image position built, never pointed at one of our runs |
| The lane node cannot read training outputs | An hour, plus a review | `edullm-lane-instance` holds S3 rights on `edullm-scratch` and nothing else, so the machine that is supposed to post-train intermediate checkpoints cannot fetch one |
| Exporting a MoE needs a GPU | None, but plan for it | Triton, not memory. The lane node has to be a GPU node even for the conversion |

Two defects in `edullm` itself, both hit on the way through and neither specific to this lane. The lane verbs assume the AWS region is in the environment: after `assume-role` they pass only the three credential variables, so `AWS_PROFILE` is dropped and with it the region `~/.aws/config` holds under that profile. Every regional call then fails with `NoRegion`, and `edullm run` reports that as *the platform's own network could not be found* — a message that names a deploy that has not happened, for a laptop that has not exported `AWS_REGION`. And `edullm stop` and `edullm studio` do not resolve the broker's profile the way `edullm run` does, so they answer *AWS would not say who you are* on a machine where `edullm run` has just worked. Exporting `AWS_REGION=us-east-1` and `AWS_PROFILE=sbsandbox` clears both.
