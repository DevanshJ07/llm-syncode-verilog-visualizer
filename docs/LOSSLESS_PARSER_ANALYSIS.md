# SynViz — Lossless per-step Verilog parser analysis (Checkpoint 2)

On-demand analysis-only view. **Not** stored inside `ExperimentResult` steps.

## Offset unit

All `start_offset` / `end_offset` values are **Python `str` / Unicode code-point indices**.
UTF-8 byte length is reported separately as `source_utf8_byte_count`.

## Source construction

| Timing | Source |
|--------|--------|
| `before` / `before_selected_token` | `concat(selected_token for steps [0, i))` |
| `after` / `after_selected_token` | `concat(selected_token for steps [0, i+1))` |
| `final_source` | authoritative final generated `.sv` / `generated_code` |

Per-step sources are labelled `derived_from_recorded_selected_tokens` (never “authoritative final”).
Whitespace in recorded tokens is **not** trimmed.

`step_index` on parser-analysis routes is **zero-based**.  
(Existing `GET /experiment/{id}/steps/{step}` remains **1-based**.)

## Endpoints

- `GET /experiment/{id}/steps/{step_index}/parser-analysis?timing=before|after`
- `GET /experiment/{id}/parser-analysis?timing=final_source`
- `GET /imported-experiment/{id}/prompts/{prompt_id}/steps/{step_index}/parser-analysis?timing=before|after`
- `GET /imported-experiment/{id}/prompts/{prompt_id}/parser-analysis?timing=final_source`

Default per-step timing: **before**. Unsupported timing → 422. Missing experiment/prompt → 404.

## Losslessness

Ordered `source_segments` concatenate to `source_text` exactly and cover `[0, len(source_text))` without gaps/overlaps.

Trivia (`%ignore` WS / comments) is recovered as gap segments. Ambiguous gaps → `unparsed` (never dropped).

## Completeness

| Status | Meaning |
|--------|---------|
| `complete` | Full lossless CST |
| `incomplete_prefix` | Truthful partial stream / partial CST — never labelled complete |
| `invalid_prefix` | Exact source preserved; consumed vs invalid suffix distinguished |
| `empty` | Step-0 before → HTTP 200, empty source |

Structural Lark analysis (`keep_all_tokens=False`) remains available alongside this view.

## Integrity

- Canonical `backend/grammar/verilog.lark` and SHA-256 unchanged
- Analysis-only Lark (`keep_all_tokens=True`) — never passed to SynCode / masking
- No per-step tree persistence; bounded in-memory cache only
- CPU work off the asyncio loop via threadpool
