from app.knowledge.retrieval import select_ranked


def test_relative_score_floor_removes_weak_tail_matches() -> None:
    scores = [(60.0, 0), (48.0, 1), (36.0, 2), (27.0, 3), (12.0, 4)]

    assert select_ranked(scores, limit=10) == [(60.0, 0), (48.0, 1), (36.0, 2)]


def test_rank_selection_respects_limit_and_empty_input() -> None:
    assert select_ranked([(3.0, 0), (2.0, 1)], limit=1) == [(3.0, 0)]
    assert select_ranked([], limit=5) == []
    assert select_ranked([(3.0, 0)], limit=0) == []
