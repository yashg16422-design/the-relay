from __future__ import annotations

from eval.run_quality import COHORTS


def test_evaluation_cohorts_partition_the_stratified_fifteen_questions() -> None:
    expected = {
        "Q02", "Q04", "Q06", "Q08", "Q10", "Q13", "Q16", "Q17",
        "Q19", "Q24", "Q25", "Q28", "Q31", "Q32", "Q34",
    }

    for cohorts in COHORTS.values():
        flattened = [question_id for ids in cohorts.values() for question_id in ids]
        assert len(flattened) == len(set(flattened))
        assert set(flattened) == expected
