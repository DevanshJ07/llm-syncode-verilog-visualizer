"""
Minimal research-only grammar fixture for Checkpoint 3C.

Not a production grammar. Identifier ports with ignored WS (common.WS).
"""

MINIMAL_IDENTIFIER_WS_GRAMMAR = r"""
start: "module" IDENT "(" port_list? ")" ";"
port_list: IDENT ("," IDENT)*

IDENT: /[A-Za-z_][A-Za-z0-9_]*/

%import common.WS
%ignore WS
"""
