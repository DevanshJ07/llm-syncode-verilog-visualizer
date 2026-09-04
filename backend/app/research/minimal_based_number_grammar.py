"""
Minimal research-only grammar for Checkpoint 3D based-number masking.

Preserves the important token boundary: one token ends at decimal digits
(``16``), the next may be ``'h`` or ``'ha``. Not a production grammar.
"""

MINIMAL_BASED_NUMBER_GRAMMAR = r"""
start: "assign" expr ";"

?expr: NUMBER
     | expr "?" expr ":" expr

// Exact NUMBER construct from the canonical Verilog grammar.
NUMBER: /[0-9]*'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+|[0-9]+/

%import common.WS
%ignore WS
"""

# Canonical NUMBER regexp (must stay in sync with verilog.lark).
CANONICAL_NUMBER_REGEXP = (
    r"[0-9]*'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+|[0-9]+"
)
