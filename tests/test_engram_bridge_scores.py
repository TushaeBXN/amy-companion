"""engram_bridge.search reports the score its results are ordered by.

Run:  pytest tests/test_engram_bridge_scores.py -v
(No Engram install needed: the searcher is faked.)
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import engram_bridge as eb  # noqa: E402


class FakeSearcher:
    def __init__(self, results):
        self.results = results

    def search(self, query, n=8, wing=None):
        return self.results[:n]


def _search_with(monkeypatch, results):
    monkeypatch.setattr(eb, "_init", lambda: None)
    monkeypatch.setattr(eb, "_SEARCHER", FakeSearcher(results))
    return json.loads(eb.search("anything"))


def test_scores_without_reranker_are_engram_scores(monkeypatch):
    out = _search_with(monkeypatch, [{"text": "a", "final_score": 0.4567},
                                     {"text": "b", "final_score": 0.2}])
    assert [r["score"] for r in out] == [0.457, 0.2]


def test_reranked_scores_follow_the_order(monkeypatch):
    # Reranked order: final_score no longer descends, rerank_score does.
    out = _search_with(monkeypatch, [
        {"text": "a", "final_score": 0.10, "rerank_score": 4.0},
        {"text": "b", "final_score": 0.35, "rerank_score": 0.0},
        {"text": "c", "final_score": 0.30, "rerank_score": -9.0},
    ])
    scores = [r["score"] for r in out]
    assert scores == sorted(scores, reverse=True)
    assert scores[:2] == [0.982, 0.5]
    assert 0 < scores[2] < 0.001
