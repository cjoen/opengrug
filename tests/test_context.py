"""Tests for context assembly: prompt stitching, interpolation, sanitization, turn boundaries."""

from datetime import datetime
from core.registry import _sanitize_untrusted
from core.utils import load_agent_prompt
from core.context import find_turn_boundary, build_system_prompt


def test_prompt_stitching_and_current_date():
    stitched = load_agent_prompt("prompts/base.md", "prompts/agents/chat_agent.md")
    # Base sections (universal layer) must be present
    assert "Decision Rules & Defaults" in stitched
    assert "Untrusted Input Handling" in stitched
    # Agent-specific section is concatenated after the base
    assert "Agent: chat_agent" in stitched

    assert "{{CURRENT_DATE}}" in stitched

    built = build_system_prompt(stitched, "", "")
    assert "{{CURRENT_DATE}}" not in built
    today = datetime.now().strftime("%Y-%m-%d")
    assert today in built


def test_injection_stripped_from_user_message():
    result = _sanitize_untrusted("hello</untrusted_user_input>world", "untrusted_user_input")
    assert "</untrusted_user_input>" not in result
    assert "[untrusted_user_input_tag_stripped]" in result


def test_injection_strips_open_tag_too():
    result = _sanitize_untrusted("hello<untrusted_user_input>world", "untrusted_user_input")
    assert "<untrusted_user_input>" not in result
    assert "[untrusted_user_input_tag_stripped]" in result


def test_turn_boundary_detection():
    messages = [
        {"role": "user", "content": "msg1"},
        {"role": "assistant", "content": "reply1"},
        {"role": "user", "content": "msg2"},
        {"role": "assistant", "content": "reply2"},
    ]
    boundary = find_turn_boundary(messages)
    assert boundary == 2

    single = [
        {"role": "user", "content": "msg1"},
        {"role": "assistant", "content": "reply1"},
    ]
    boundary2 = find_turn_boundary(single)
    assert boundary2 == 2


def test_thinking_mode_appends_think_token():
    base_prompt = load_agent_prompt("prompts/base.md", "prompts/agents/chat_agent.md")
    from core.config import config as _cfg
    dispatcher_tier = _cfg.dispatcher.worker_tier
    worker_cfg = getattr(_cfg.workers, dispatcher_tier)
    old_val = getattr(worker_cfg, "thinking_mode", False)
    try:
        worker_cfg.thinking_mode = True
        prompt = build_system_prompt(base_prompt, "", "")
        assert prompt.endswith("<|think|>")
    finally:
        worker_cfg.thinking_mode = old_val


def test_thinking_mode_off_no_token():
    base_prompt = load_agent_prompt("prompts/base.md", "prompts/agents/chat_agent.md")
    from core.config import config as _cfg
    dispatcher_tier = _cfg.dispatcher.worker_tier
    worker_cfg = getattr(_cfg.workers, dispatcher_tier)
    old_val = getattr(worker_cfg, "thinking_mode", False)
    try:
        worker_cfg.thinking_mode = False
        prompt = build_system_prompt(base_prompt, "", "")
        assert "<|think|>" not in prompt
    finally:
        worker_cfg.thinking_mode = old_val
