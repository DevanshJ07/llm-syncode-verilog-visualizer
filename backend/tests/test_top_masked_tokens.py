"""Tests for server-side top masked token computation."""

from __future__ import annotations

import pytest
import torch

from app.services.llm_service import _compute_top_masked_tokens


class _StubTokenizer:
    """Minimal tokenizer stub mapping ids to strings."""

    def decode(self, ids, **kwargs) -> str:
        tokens = {
            0: " +",
            1: "always",
            2: "module",
            3: "\n",
            4: "\t",
        }
        return tokens[ids[0]]


def test_returns_empty_when_nothing_masked() -> None:
    probs = torch.tensor([0.5, 0.3, 0.2])
    masked = torch.tensor([False, False, False])
    assert _compute_top_masked_tokens(probs, masked, _StubTokenizer()) == []


def test_sorts_masked_by_pre_mask_probability_descending() -> None:
    probs = torch.tensor([0.05, 0.50, 0.30, 0.15])
    masked = torch.tensor([True, True, False, True])
    entries = _compute_top_masked_tokens(probs, masked, _StubTokenizer(), limit=50)
    assert [e.token_id for e in entries] == [1, 3, 0]
    assert [e.pre_mask_prob for e in entries] == pytest.approx([0.50, 0.15, 0.05])


def test_respects_limit() -> None:
    probs = torch.tensor([0.4, 0.3, 0.2, 0.1])
    masked = torch.tensor([True, True, True, True])
    entries = _compute_top_masked_tokens(probs, masked, _StubTokenizer(), limit=2)
    assert len(entries) == 2
    assert entries[0].pre_mask_prob >= entries[1].pre_mask_prob


def test_whitespace_tokens_preserved_in_decode() -> None:
    probs = torch.tensor([0.0, 0.0, 0.0, 0.7, 0.3])
    masked = torch.tensor([False, False, False, True, True])
    entries = _compute_top_masked_tokens(probs, masked, _StubTokenizer(), limit=50)
    assert entries[0].token == "\n"
    assert entries[1].token == "\t"


def test_status_defaults_to_masked_by_syncode() -> None:
    probs = torch.tensor([0.9, 0.1])
    masked = torch.tensor([True, False])
    entries = _compute_top_masked_tokens(probs, masked, _StubTokenizer(), limit=50)
    assert entries[0].status == "masked by SynCode"
