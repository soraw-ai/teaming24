from teaming24.agent.core import LocalCrew


def _bare_crew() -> LocalCrew:
    crew = LocalCrew.__new__(LocalCrew)
    crew.task_manager = None
    return crew


def test_plan_like_result_rejected_when_user_did_not_request_plan():
    crew = _bare_crew()
    ok, feedback = crew._check_execution_evidence(
        "Give me tomorrow's prediction result directly.",
        "Recommended steps: 1) collect data, 2) train model, 3) produce output.",
        task_id=None,
    )
    assert ok is False
    assert ("plan-oriented" in feedback.lower()) or ("empirical request" in feedback.lower())


def test_plan_like_result_allowed_when_user_explicitly_requests_plan():
    crew = _bare_crew()
    ok, _feedback = crew._check_execution_evidence(
        "Please provide the full strategy and steps only, do not execute.",
        "Recommended steps: 1) collect data, 2) train model, 3) produce output.",
        task_id=None,
    )
    assert ok is True


def test_empirical_request_requires_evidence():
    crew = _bare_crew()
    ok, feedback = crew._check_execution_evidence(
        "Predict TSLA price tomorrow using ML model.",
        "Final answer: I recommend using LSTM and gather data first.",
        task_id=None,
    )
    assert ok is False
    assert "evidence" in feedback.lower()


def test_empirical_request_accepts_concrete_evidence():
    crew = _bare_crew()
    ok, _feedback = crew._check_execution_evidence(
        "Predict TSLA price tomorrow using ML model.",
        (
            "Final Answer: 247.31 USD\n"
            "metrics: rmse=3.41, mae=2.18\n"
            "data source: yfinance (2025-01-01 to 2026-02-24)\n"
            "artifact: outputs/task_x/model_regression.pkl"
        ),
        task_id=None,
    )
    assert ok is True


def test_task_class_policy_resolution_uses_empirical_profile() -> None:
    crew = _bare_crew()
    policy = crew._get_quality_policy("Predict next-day stock price with ML from live data.")
    assert policy["task_class"] == "empirical"
    assert int(policy["max_rounds"]) >= 3
    assert bool(policy["allow_plan_output"]) is False


def test_fast_profile_reduces_rounds(monkeypatch) -> None:
    from teaming24.config import get_config

    cfg = get_config()
    monkeypatch.setattr(cfg.system.api, "quality_benchmark_profile", "fast")
    crew = _bare_crew()
    rounds = crew._resolve_max_rounds_for_prompt("Implement a small API endpoint with tests.")
    assert rounds >= 1
    assert rounds <= 3


def test_confidence_gate_rejects_low_confidence(monkeypatch) -> None:
    from teaming24.config import get_config

    cfg = get_config()
    monkeypatch.setattr(cfg.system.api, "quality_gate_enabled", True)
    monkeypatch.setattr(cfg.system.api, "quality_auto_fallback_low_confidence", True)
    monkeypatch.setattr(cfg.system.api, "quality_confidence_threshold", 0.8)

    crew = _bare_crew()
    # Avoid external LLM calls; force deterministic low-confidence path.
    monkeypatch.setattr(
        crew,
        "_run_organizer_quality_eval",
        lambda *_args, **_kwargs: {"satisfied": True, "feedback": "", "confidence": 0.55},
    )
    monkeypatch.setattr(
        crew,
        "_run_independent_verifier_eval",
        lambda *_args, **_kwargs: {"satisfied": True, "feedback": "", "confidence": 0.5},
    )
    monkeypatch.setattr(crew, "_task_has_execution_trace", lambda _task_id: False)

    ok, feedback = crew._evaluate_result(
        "Analyze this architecture and provide the final output.",
        {
            "status": "success",
            "result": (
                "Final Answer: This is the definitive architecture output with concrete details "
                "and result value 42 for validation."
            ),
        },
        round_num=1,
        task_id=None,
    )
    assert ok is False
    assert "confidence" in feedback.lower()
