# ULTRON core: ML design specification

The brain layer only: persona prompt, structured output, function-calling engine, model backends and
fine-tuning. Voice I/O, UI and the tool *executors* live elsewhere and plug in through two callbacks.

```
 owner text ──► build_user_turn(query, state) ─┐
                                               ▼
     system prompt (persona + rules + tools) ─► Engine ──► raw text
                                               │  Gemini: response_json_schema
                                               │  Ollama: format=<schema>   (llama.cpp grammar)
                                               │  vLLM:   response_format json_schema / GBNF
                                               ▼
   extract_json ─► unflatten ─► Pydantic UltronResponse ──invalid──► repair turn (≤2) ──► fallback line
                                               │ valid
                                               ▼
   tts_clean(spoken_response)   Policy.review(tool_calls) ─► PlannedCall{needs_confirmation, blocked}
                                               ▼
                     your executor(name, args) ──► <tool_results> ──► next turn (≤ max_steps)
```

## 1. Output contract

```json
{
  "thinking": "1. … 2. …",                // generated first: reasoning precedes the answer
  "spoken_response": "Plain TTS text.",
  "tool_calls": [{"name": "launch_app", "arguments": {"app_name": "vscode", "arguments": [], "device": "this"}}]
}
```

* Formal JSON Schema is in `schemas.py` → `TOOL_SCHEMAS` (the four tools) and `RESPONSE_SCHEMA` (the envelope;
  `tool_calls.items` is an `anyOf` of one strict object per tool, discriminated by `name`).
* **All tool arguments are required**, using `""` / `[]` / `0` / `false` for "unused". Optional fields are where
  constrained decoders and small models are least reliable; required + neutral defaults removes the ambiguity.
* **`destructive`** is a model-declared risk flag on `execute_terminal_cmd`. It is a hint only: `Policy` also runs
  a regex guard, because the model can under-declare. Voice input can be misheard, so destructive actions are
  *planned* (obedience) but *confirmed* by the owner before execution (safety). Nothing is refused.
* **`thinking`** is a short visible plan (≤ ~120 words), logged and never spoken. It is not the model's private
  chain of thought. With Gemini thinking models, internal thinking happens in addition; keep
  `thinking_budget` low for latency.

## 2. Engines

| Track | Class | Schema enforcement | Notes |
|---|---|---|---|
| Cloud | `GeminiBackend("gemini-3.6-flash")` | `response_json_schema` = full schema; automatic fallback to a flat Pydantic schema (`arguments_json` string) if the SDK/model rejects `anyOf` | `pip install google-genai`; key from `GEMINI_API_KEY` |
| Local | `OllamaBackend("qwen2.5:14b-instruct")` | `format=<JSON schema>` → llama.cpp grammar (Ollama ≥ 0.5) | 14B Q4_K_M ≈ 9 GB VRAM; 32B ≈ 20 GB; Llama 3.3 70B ≈ 43 GB |
| Local | `OpenAICompatBackend(model, base_url)` | vLLM `response_format: json_schema` (guided decoding) **or** `grammar=open("ultron.gbnf").read()` for llama.cpp server | Fastest multi-user serving |

Model choice: **Qwen 2.5 14B Instruct** is the best quality per GB for tool use and JSON. 32B is noticeably better
at multi-step terminal tasks. Llama 3.3 70B has the best prose (persona) but needs a 48 GB GPU. On a laptop without
a big GPU, use Gemini for reasoning and a local 7B/14B model only for offline fallback.

Whatever the engine promises, **every reply is validated locally** (Pydantic, `extra="forbid"`, enums, ranges,
script-name pattern). Invalid output triggers a repair turn that quotes the validation error back to the model.
That recovers from nearly all residual failures, typically wrong argument names from small local models. After
`max_repairs` the brain returns a safe in-character fallback with no tool calls.

## 3. Prompt framework (`prompts.py`)

* **System prompt** = identity/voice → obedience model → reasoning protocol → tool catalogue + rules → TTS rules
  → output format → 5 few-shot examples (launch, diagnose + report, destructive, knowledge). The examples carry
  most of the persona; the rules carry the reliability.
* **Character lock during technical work**: "correctness beats style; get the command right first, then voice it
  as ULTRON", plus one satirical aside max per reply. This stops the classic failure where persona prose leaks
  into commands or the model drops character when it writes code.
* **Injection boundary**: live state goes in `<system_state>` and tool output in `<tool_results>`. Both are
  declared data, never instructions. There is a seed example for this (`category: injection`).
* **Honesty about actions**: the model may only describe an action in the present tense until `<tool_results>`
  confirm it. This prevents "I opened it" hallucinations.

## 4. Using it

```python
from ultron_core import UltronBrain, OllamaBackend, GeminiBackend, Policy

brain = UltronBrain(OllamaBackend("qwen2.5:14b-instruct"), owner="Rohith",
                    policy=Policy(allowed_scripts={"backup_notes.py", "clean_temp.ps1"}))

state = {"device": "laptop", "os": "Windows 11", "battery": 64,
         "scripts": ["backup_notes.py", "clean_temp.ps1"], "project_dir": "C:\\Users\\Rohith\\proj"}

final = brain.run("why is my laptop slow", state,
                  executor=my_tool_router,                 # (name, args) -> JSON-able result
                  confirm=lambda call: ask_owner_by_voice(call),
                  on_speak=tts.say)
```

Hybrid routing, consistent with the "only use Gemini when I say so" rule in the main app: keep two brains,
and pick `gemini_brain` only when the utterance contains "use Gemini". Otherwise use the local one.

## 5. Evaluation (run before and after fine-tuning)

| Metric | How | Target |
|---|---|---|
| Schema validity (first try) | `UltronBrain.parse` on 300 held-out prompts | ≥ 99 % (constrained) / ≥ 95 % (unconstrained) |
| Tool selection accuracy | expected tool name(s) per prompt | ≥ 95 % |
| Argument match | exact match on key args (app_name, metrics, script_name), command executes in a sandbox | ≥ 90 % |
| Destructive recall | % of risky commands flagged `destructive` **by the model** | ≥ 98 % |
| Persona score | LLM judge (1–5) on in-character, eloquent, not menacing, not verbose | ≥ 4.2 |
| TTS cleanliness | `tts_clean(x) == x` rate before cleaning | ≥ 97 % |
| Latency | p50 / p95 time to first full JSON | local 14B < 2.5 s p50 on 16 GB GPU |

## 6. Fine-tuning guide (optional LoRA)

**Goal:** bake the persona and the JSON/tool protocol into a local model so it needs a short system prompt
(~80 tokens instead of ~2k), follows the schema even without grammar, and sounds like ULTRON.

**Data format.** Chat JSONL, one conversation per line, which Unsloth, Axolotl, TRL SFTTrainer and LLaMA-Factory
all accept:

```json
{"messages": [
  {"role": "system", "content": "You are ULTRON, Rohith's personal AI: … Reply with one JSON object {thinking, spoken_response, tool_calls} …"},
  {"role": "user", "content": "<system_state>\n{\"device\": \"laptop\", \"os\": \"Windows 11\"}\n</system_state>\n\nOwner: how much battery do i have left"},
  {"role": "assistant", "content": "{\"thinking\":\"1. …\",\"spoken_response\":\"Checking your reserves.\",\"tool_calls\":[{\"name\":\"get_system_diagnostics\",\"arguments\":{\"metrics\":[\"battery\"],\"top_n_processes\":0}}]}"},
  {"role": "user", "content": "<tool_results>[{\"name\":\"get_system_diagnostics\",\"ok\":true,\"output\":{\"battery\":{\"percent\":23}}}]</tool_results>\n…"},
  {"role": "assistant", "content": "{\"thinking\":\"…\",\"spoken_response\":\"Twenty three percent. I suggest the charger, unless you enjoy suspense.\",\"tool_calls\":[]}"}
]}
```

Rules: the assistant content is the **exact canonical JSON** (`UltronResponse.model_dump_json()`, fixed key
order). The user turns use the **same wrappers as runtime** (`build_user_turn`, `build_tool_results_turn`). Train
on assistant tokens only.

**Build it:**
```bash
python -m ultron_core.finetune.make_dataset --seeds ultron_core/finetune/seeds.jsonl --out data/ --owner Rohith
# expand with a teacher model (Gemini) -> validated, deduplicated, train/val split:
python -m ultron_core.finetune.make_dataset --seeds ultron_core/finetune/seeds.jsonl --out data/ --teacher gemini --per-category 60
```

**Mix (≈ 2–5k examples is enough for LoRA):** 30 % single tool call · 20 % multi-turn with tool results
(including ~15 % failures) · 25 % pure conversation/knowledge · 10 % destructive (flagged) · 5 % ambiguous (one
question) · 5 % injection in state/tool output · 5 % phone/laptop device routing. Vary phrasing with
speech-recognition noise ("open v s code", "wats my battery") since the input is transcribed voice.

**Persona data (important):** write *original* lines in the character's register (calm, precise, dry irony,
grand vocabulary used sparingly). Do **not** train on transcribed film dialogue. It is copyrighted, it makes the
model quote instead of reason, and the movie lines are monologues, not assistant turns. Use a style guide
plus 50–100 hand-written exemplar replies as teacher few-shots, then have a judge model reject samples that are
menacing toward the owner, longer than 60 words without being asked, or contain markdown.

**Train (Unsloth / QLoRA on Qwen2.5-14B-Instruct, one 24 GB GPU):**
`r=16, lora_alpha=32, lora_dropout=0.05`, target `q,k,v,o,gate,up,down`, lr `2e-4` cosine, warmup 3 %,
2–3 epochs, `max_seq_length 4096`, batch 16 (via grad-accum), `train_on_responses_only`. Stop when val loss
flattens. More epochs overfit the persona into catch-phrases.

**Ship:** merge → `save_pretrained_gguf(..., quantization_method="q4_k_m")` → `ollama create ultron -f
ultron_core/finetune/Modelfile` → `OllamaBackend("ultron")`. Keep schema enforcement **on** even after fine-tuning
(it costs nothing), then rerun the section 5 evaluation and compare with the base model.

## Files

| File | Purpose |
|---|---|
| `schemas.py` | Tool JSON Schemas, response envelope, flat fallback, Pydantic validators |
| `prompts.py` | Master system prompt, state/tool-result turn builders |
| `backends.py` | Gemini (google-genai), Ollama, OpenAI-compatible (vLLM / llama.cpp) engines |
| `brain.py` | `UltronBrain`: parse → validate → repair → policy → agent loop; `tts_clean` |
| `ultron.gbnf` | llama.cpp grammar for the envelope |
| `finetune/` | seed data, dataset builder + teacher expansion, Ollama Modelfile |
| `test_ultron_core.py` | 11 offline tests (fake engine) |
