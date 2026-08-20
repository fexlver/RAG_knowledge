from src.agent.planner import QueryPlanner


def test_routes_ordinary_question_to_direct_retrieval():
    plan = QueryPlanner().plan("食品添加剂的使用范围是什么？")

    assert plan.mode == "direct"
    assert len(plan.subqueries) == 1


def test_routes_version_comparison_to_multi_step_retrieval():
    plan = QueryPlanner().plan("比较 GB 1234-2020 和 GB 1234-2025 的变化")

    assert plan.mode == "multi_step"
    assert len(plan.subqueries) >= 3
