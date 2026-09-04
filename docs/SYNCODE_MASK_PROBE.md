# SynCode mask diagnostic probe (Checkpoint 3A)

Research-only tool. **Not** wired into FastAPI, `llm_service`, or generation.

## Isolation

| Must not import the probe | Why |
|---------------------------|-----|
| `llm_service` / generation runner | Production masking path stays untouched |
| FastAPI startup / routes | Normal backend must not load tokenizers or build mask stores |
| Shared `SYNCODE_CACHE` | Probe uses an explicit isolated `--cache-root` |

## CLI

```bash
# From backend/ — default is local_files_only (no network).
# Pass --allow-download only when intentional; it is recorded in provenance.
# Pass --trust-remote-code only when intentional; also recorded.
python scripts/run_syncode_mask_probe.py \
  --case research_cases/nemotron_newline_step24.template.json \
  --tokenizer-id <EXACT_MODEL_ID> \
  --tokenizer-revision <EXACT_REVISION> \
  --cache-root /path/to/isolated_syncode_cache \
  --output-dir /path/to/probe_out
```

Failed mandatory stages exit nonzero with `report_status=failed` / `failure_stage`
set. JSON and Markdown are written atomically.

Compare existing vs fresh reports:

```bash
python scripts/compare_syncode_mask_probes.py report_existing.json report_fresh.json
```

## Case JSON

See `backend/research_cases/*.template.json`. Required fills:

- `source_trace_path`
- `tokenizer_model_id` / `tokenizer_revision`
- witness suffix or `witness_source_file`
- cache path when `mask_store_mode=existing_cache`

Prefix reconstruction (zero-based step `i`):

`concat(selected_token for steps [0, i))` — no `prefix_tail`, no trimming.

## Output contract

- `{case_id}.json` — `SyncodeMaskProbeResult` (schema `syncode_mask_probe_v1`)
- `{case_id}.md` — decision sequence with VERIFIED / CONTRADICTED / UNAVAILABLE / INFERENCE

### JSON highlights

- provenance (versions, grammar SHA, SynCode source file SHAs, mask-store identity)
- prefix text / UTF-8 SHA / char & byte counts
- per-candidate HF tokenizer evidence
- per-candidate ByteTokenizer evidence
- full (untruncated) accept sequences + remainder / fixed-prefix validation
- runtime mask bits + per-sequence attribution + `reconstructed_union_equal_runtime`
- constructive canonical witness (+ optional minimal-grammar **control**)
- root-cause report

## SynCode 0.4.16 internals used

- `IncrementalParser.get_acceptable_next_terminals`
- `ParseResult` / `AcceptSequence` / `RemainderState`
- `MaskStore.get_accept_mask` (once)
- `MaskStore._lookup_next_tokens` logic via version-pinned adapter
  (`complete_case_lookup` / `incomplete_case_lookup` /
  `_lookup_next_tokens_for_fsm_state`)
- `ByteTokenizer.decode([id])`

Detailed DFA byte-transition traces are **UNAVAILABLE** without unsupported
private instrumentation. Final mask bits and verified attribution are mandatory.

## Checkpoint 3D (based number / ``'h`` vs ``'ha``)

Causal focus: SynCode ``ByteFSM.consume_prefix`` from a final NUMBER state after
decimal remainder ``16``. Direct FSM walk for ``'h`` ends live/non-final with
digit outs; ``consume_prefix`` returns the full token as remainder. ``'ha``
reaches accept and is stored.

Local fixtures under ``backend/research_cases/fixtures/``. Full-vocab Nemotron
mask confirmation still requires an NSCC run (not part of the implementation
checkpoint). Use ``run_number_causal_trace=True`` when wired.

Conclusion scope (before NSCC):

- ``minimal_control_conclusion`` =
  ``verified_viable_nonfinal_number_state_discarded``
- ``original_nemotron_conclusion`` =
  ``awaiting_full_runtime_verification``
- ``conclusion_scope`` = ``mixed_pending_nscc``
- fixed-k remains ``UNAVAILABLE``

## Integrity notes

- Fail-closed unless SynCode == 0.4.16 (explicit override recorded).
- Never conclude “SynCode bug confirmed” from `raw_argmax_blocked=True` alone.
- Attribution marked **unreliable** if reconstructed union ≠ runtime mask.
- Minimal-grammar oracle is explicitly labelled and must not be confused with
  canonical Verilog success.
