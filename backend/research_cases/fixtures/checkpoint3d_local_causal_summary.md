# Checkpoint 3D local causal summary

- **Trace step:** 65 (zero-based; recorded `step` field equals index)
- **Candidate:** raw_argmax `6782` = `'h`, blocked=`True`; selected `56257` = `'ha`
- **Prefix tail:** `(a == 4) ? 16`
- **Tokenizer bytes (UTF-8 of decoded text):** `'h` → `2768`; `'ha` → `276861` (full HF round-trip still requires NSCC tokenizer load)
- **Witness:** P+`'h`+S with fixture `nemotron_base_literal_step65_witness_h.sv` → canonical parse **complete_valid**
- **NUMBER FSM after `16`+`'h`:** live non-final with digit outgoing transitions; `consume_prefix` returns remainder `2768` (full token)
- **NUMBER FSM after `16`+`'ha`:** accepting; `consume_prefix` remainder empty
- **Minimal MaskStore bits:** `'h`=`False`, `'ha`=`True`
- **First verified divergence:** `ByteFSM.consume_prefix` / `viable_nonfinal_state_discarded`
- **Supported conclusion (minimal control):** `verified_viable_nonfinal_number_state_discarded`
- **Original Nemotron conclusion:** `awaiting_full_runtime_verification`
- **Conclusion scope:** `mixed_pending_nscc`
- **Fixed-k:** UNAVAILABLE (not independently varied)
- **NSCC full-vocab confirmation:** still required (not run in this checkpoint)
