import pytest

from teaming24.prompting.registry import (
    PromptTemplate,
    PromptTemplateError,
    PromptTemplateRegistry,
    render_prompt,
)


def test_default_template_render_success() -> None:
    rendered = render_prompt(
        "core.local_refinement",
        round_num=2,
        original_task="Build a forecasting model",
        previous_output="Too short",
    )
    assert "Refinement round 2" in rendered
    assert "Build a forecasting model" in rendered


def test_registry_render_requires_all_variables() -> None:
    reg = PromptTemplateRegistry()
    reg.register(
        PromptTemplate(
            template_id="test.simple",
            version="1.0.0",
            role="test",
            template="A=$a B=$b",
        )
    )
    with pytest.raises(PromptTemplateError, match="missing required variables"):
        reg.render("test.simple", a="x")


def test_registry_render_rejects_unknown_variables() -> None:
    reg = PromptTemplateRegistry()
    reg.register(
        PromptTemplate(
            template_id="test.unknown",
            version="1.0.0",
            role="test",
            template="X=$x",
        )
    )
    with pytest.raises(PromptTemplateError, match="unknown variables"):
        reg.render("test.unknown", x="ok", y="unexpected")

