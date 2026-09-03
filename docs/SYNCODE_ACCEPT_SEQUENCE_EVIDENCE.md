# SynCode accept-sequence evidence (SynViz contract)

Implementation-specific notes for SynCode **0.4.16** as used by this platform.
Verified runtime (production local interpreter):

- `C:\synviz-venv\Scripts\python.exe` — Python 3.11.9
- `syncode==0.4.16` → `C:\synviz-venv\Lib\site-packages\syncode\`
- `transformers==4.53.2`
- Lark via `syncode.larkm` **1.1.8**

## 1. Prefix, remainder, and accept paths

At mask time SynCode works from the visible generation prefix \(C_k\):

1. Lexically **fixed** material already committed by the incremental lexer/parser.
2. A lexical **remainder** \(r\) (possibly empty) — unfinished bytes/text for the
   current terminal (`ParseResult.remainder` + `remainder_state`).

**Accept sequences** are ordered lists of **grammar terminal names**. They are
the DFA lookup paths SynCode builds in
`ParseResult.from_accept_terminals` (`syncode/parse_result.py`).

They are **not**:

- LLM tokenizer-token sequences;
- “selected token + future tokens”;
- ordinary Lark expected-next-terminal lists from structural parser analysis.

SynCode tests **remainder \(r\) + candidate tokenizer-token bytes** against those
terminal paths when building the accept mask.

## 2. Core lookahead \(k=2\)

In SynCode 0.4.16 the effective **core** accept-sequence lookahead is
**\(k=2\) grammar terminals** (pair-following in the mask store).

Platform field (new evidence only):

- `core_lookahead_k = 2`
- `core_lookahead_unit = "grammar_terminals"`
- `sequence_construction = "syncode.ParseResult.from_accept_terminals@0.4.16"`

Never call \(k\) “token length” in the LLM sense.

## 3. Why a path can show 3 terminals

Under `RemainderState.MAYBE_COMPLETE`, SynCode may insert an **ignored**
terminal (e.g. `WS`) between the final current terminal and the next:

`[final_terminal, ignore_terminal, next_terminal]`

That is **ignored-terminal intercalation**, not \(k=3\).

Ignore-only length-1 paths (`[WS]`, …) are also always unioned into the set.

## 4. Timing

Evidence is captured **before the selected LLM token**
(`evidence_timing = "before_selected_token"`): the ParseResult passed into
`dfa_mask_store.get_accept_mask` for that decoding step.

## 5. Recorded vs Recomputed

| Path | `origin` | Outer Prov / semantics |
|------|----------|-------------------------|
| Live mask capture | `live_mask_runtime` | Recorded; `semantics_provenance=recorded` when newly serialized under 0.4.16 |
| Import parser-only recompute | `import_recomputed_parser_only` | Recomputed; does **not** replay the tokenizer DFA mask |
| Historical JSON without new fields | (unchanged) | Missing fields stay **Unavailable** — never invent Recorded |

If the UI infers \(k=2\) only from `syncode_version` starting with `0.4.16`
without stored `core_lookahead_k`, label it **Derived from SynCode 0.4.16**.

## 6. Truncation vs path length

Platform storage caps (`config.py`):

- max **64** sequences stored (`accept_sequence_count_stored` / `_total`)
- max **16** terminals per sequence (safety)
- max **64** characters per terminal name

**“stored 64 / total 131”** is sequence-count truncation. It is **not** \(k\)
and is **not** the terminal count of a single path.

## 7. Historical / unavailable fields

Old experiments may lack:

- `core_lookahead_k` / `core_lookahead_unit` / `sequence_construction`
- `current_accept_terminals` / `next_accept_terminals` / `ignore_terminals`
- per-sequence `construction_kind` / `contains_ignored_terminal` /
  `displayed_terminal_count`
- `semantics_provenance`

Treat absence as **Unavailable**. Do not backfill as Recorded.
