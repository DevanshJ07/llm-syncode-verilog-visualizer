"""
Research-only SynCode mask diagnostic probe (Checkpoint 3A).

Isolation contract
------------------
This package must NEVER be imported by:
  - app.services.llm_service
  - generation runner
  - FastAPI startup / production routes
  - SynCode constraint wrappers used in live generation

Running the normal backend must not load a tokenizer, construct a mask store,
or execute this probe.
"""

from __future__ import annotations

PROBE_PACKAGE = "app.research.syncode_mask_probe"
