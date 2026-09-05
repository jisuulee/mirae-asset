from src.retrieval.parent_sections import MAX_SUPPORT_BONUS, aggregate_parent_chunks


def chunk(section, chunk_id, rank, score, **extra):
    return {
        "section_id": section,
        "vector_chunk_id": chunk_id,
        "raw_chunk_rank": rank,
        "score": score,
        "receipt_no": f"R{section}",
        "source_locator": f"xpath:{section}:{chunk_id}",
        "provenance": {"file_id": section},
        **extra,
    }


def test_same_parent_aggregates_and_different_parents_stay_isolated():
    rows = aggregate_parent_chunks(
        [chunk(1, 11, 1, .8), chunk(2, 21, 2, .79), chunk(1, 12, 3, .7)],
        method="support2",
    )
    assert len(rows) == 2
    first = next(row for row in rows if row["section_id"] == 1)
    assert first["contributing_chunk_ids"] == [11, 12]
    assert first["contributing_chunk_count"] == 2


def test_single_chunk_parent_equals_best_chunk_score():
    row = aggregate_parent_chunks([chunk(1, 11, 1, .8)], method="support3")[0]
    assert row["aggregated_score"] == row["best_chunk_score"] == .8
    assert row["support_bonus"] == 0


def test_top2_and_top3_use_bounded_number_of_support_chunks():
    chunks = [chunk(1, 10 + i, i, 1 - i / 10) for i in range(1, 5)]
    assert len(aggregate_parent_chunks(chunks, method="support2")[0]["contributing_chunk_ids"]) == 2
    assert len(aggregate_parent_chunks(chunks, method="support3")[0]["contributing_chunk_ids"]) == 3


def test_ties_are_deterministic_and_provenance_is_preserved():
    chunks = [chunk(2, 22, 1, .8), chunk(1, 12, 1, .8)]
    first = aggregate_parent_chunks(chunks, method="best")
    second = aggregate_parent_chunks(reversed(chunks), method="best")
    assert [row["section_id"] for row in first] == [1, 2]
    assert [row["section_id"] for row in second] == [1, 2]
    assert first[0]["provenance"] == {"file_id": 1}
    assert first[0]["source_locator"] == "xpath:1:12"


def test_support_bonus_is_guarded_against_long_section_raw_sum_bias():
    many = [chunk(1, 100 + i, i, .99) for i in range(1, 101)]
    row = aggregate_parent_chunks(many, method="support3")[0]
    assert 0 <= row["support_bonus"] <= MAX_SUPPORT_BONUS
    assert row["aggregated_score"] <= row["best_chunk_score"] + MAX_SUPPORT_BONUS


def test_empty_invalid_method_and_multi_gold_compatible_output():
    assert aggregate_parent_chunks([], method="best") == []
    try:
        aggregate_parent_chunks([], method="sum")
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported aggregation method must fail")
    rows = aggregate_parent_chunks([chunk(1, 11, 1, .8), chunk(2, 21, 2, .7)], method="best")
    assert {row["section_id"] for row in rows} & {1, 2} == {1, 2}
