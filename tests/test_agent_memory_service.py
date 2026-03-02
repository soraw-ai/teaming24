from dataclasses import dataclass, field

from teaming24.api.services import agent_memory as agent_memory_service
from teaming24.api.services.agent_memory import build_agent_execution_prompt, is_agent_memory_enabled, should_respect_context_window
from teaming24.config import get_config


@dataclass
class _Msg:
    role: str
    content: str


def test_config_exposes_runtime_memory_via_convenience_property():
    cfg = get_config()

    assert cfg.memory is cfg.runtime.memory
    assert cfg.memory.max_context_length > 0


def test_build_agent_execution_prompt_includes_memory_and_current_request():
    cfg = get_config()
    prompt = build_agent_execution_prompt(
        [
            _Msg(role="user", content="Earlier question"),
            _Msg(role="assistant", content="Earlier answer"),
            _Msg(role="user", content="Current question"),
        ],
        "Current question",
        cfg=cfg,
        agent_memory_context="Known durable fact",
    )

    assert "[Relevant long-term memory]" in prompt
    assert "Known durable fact" in prompt
    assert "[Conversation context]" in prompt
    assert "Earlier question" in prompt
    assert "[Current user request]" in prompt
    assert "Current question" in prompt


@dataclass
class _MemoryCfg:
    enabled: bool = True
    max_context_length: int = 16
    respect_context_window: bool = True
    chat_context_message_preview: int = 5
    chat_context_token_reserve: int = 1


@dataclass
class _AgentsDefaults:
    max_tokens: int = 1


@dataclass
class _Agents:
    defaults: _AgentsDefaults = field(default_factory=_AgentsDefaults)


@dataclass
class _Cfg:
    memory: _MemoryCfg = field(default_factory=_MemoryCfg)
    agents: _Agents = field(default_factory=_Agents)


def test_runtime_memory_switches_fall_back_to_config_when_no_db_override(monkeypatch):
    monkeypatch.setattr(agent_memory_service, "_runtime_setting", lambda key, default: default)
    cfg = _Cfg()

    assert is_agent_memory_enabled(cfg) is True
    assert should_respect_context_window(cfg) is True


def test_build_agent_execution_prompt_keeps_full_history_when_context_limit_disabled(monkeypatch):
    monkeypatch.setattr(agent_memory_service, "_runtime_setting", lambda key, default: default)
    cfg = _Cfg(memory=_MemoryCfg(enabled=True, max_context_length=8, respect_context_window=False))
    prompt = build_agent_execution_prompt(
        [
            _Msg(role="user", content="Earlier question that should stay visible"),
            _Msg(role="assistant", content="Earlier answer that should also stay visible"),
            _Msg(role="user", content="Current question"),
        ],
        "Current question",
        cfg=cfg,
        agent_memory_context="",
    )

    assert "Earlier question that should stay visible" in prompt
    assert "Earlier answer that should also stay visible" in prompt


def test_runtime_memory_switches_honor_db_override(monkeypatch):
    overrides = {
        "agentMemoryEnabled": False,
        "respectContextWindow": False,
    }
    monkeypatch.setattr(agent_memory_service, "_runtime_setting", lambda key, default: overrides.get(key, default))
    cfg = _Cfg(memory=_MemoryCfg(enabled=True, respect_context_window=True))

    assert is_agent_memory_enabled(cfg) is False
    assert should_respect_context_window(cfg) is False
