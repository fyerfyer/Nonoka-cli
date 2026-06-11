"""Real LLM integration tests using Deepseek API.

These tests verify that nonoka-cli works end-to-end with a real LLM:
- Agent can be built and execute prompts
- Streaming events are emitted correctly
- Context is maintained across calls

Requires DEEPSEEK_API_KEY and DEEPSEEK_BASE_URL (or OPENAI_API_KEY / OPENAI_BASE_URL)
to be set in the environment or .env file.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from nonoka import Runner
from nonoka.core.runner import StreamEvent

from nonoka_cli.config.models import CLIConfig
from nonoka_cli.core.agent_factory import AgentFactory
from nonoka_cli.core.context import CLIContext
from nonoka_cli.core.orchestrator import Orchestrator


@pytest.fixture
def real_config() -> CLIConfig:
  """Return config using a real model from environment."""
  # Deepseek models via OpenAI-compatible endpoint
  model = os.getenv("NONOKA_TEST_MODEL", "deepseek-chat")
  return CLIConfig(
    model=model,
    system_prompt="You are a helpful assistant. Keep answers very brief (1-2 sentences).",
  )


@pytest.fixture
def has_api_key() -> bool:
  """Check if API credentials are available."""
  return bool(
    os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
  )


@pytest.mark.asyncio
@pytest.mark.skipif(
  not os.getenv("OPENAI_API_KEY") and not os.getenv("DEEPSEEK_API_KEY"),
  reason="No API key available — set OPENAI_API_KEY or DEEPSEEK_API_KEY",
)
class TestRealAgentExecution:
  """Tests that exercise the real LLM API."""

  @pytest.mark.asyncio
  async def test_agent_factory_builds_runnable_agent(self, real_config):
    """AgentFactory should produce an Agent that can be run."""
    factory = AgentFactory(real_config)
    agent = factory.build()
    assert agent.model == real_config.model
    assert agent.system_prompt == real_config.system_prompt

  @pytest.mark.asyncio
  async def test_runner_single_prompt_stream(self, real_config):
    """Runner should stream content_delta events for a simple prompt."""
    factory = AgentFactory(real_config)
    agent = factory.build()
    runner = Runner()
    deps = CLIContext(
      user="local",
      session_id="test-session",
      config=real_config,
      working_dir=__import__("pathlib").Path.cwd(),
    )

    events = []
    async for event in runner.run_react_stream(agent, "Say 'hello'.", deps=deps):
      events.append(event)
      # Print for visibility during test runs
      if event.type == "content_delta":
        print(event.data.get("content", ""), end="", flush=True)

    print()  # newline after stream

    # Verify event sequence
    content_events = [e for e in events if e.type == "content_delta"]
    final_events = [e for e in events if e.type == "final"]

    assert len(content_events) > 0, "Expected at least one content_delta event"
    assert len(final_events) == 1, "Expected exactly one final event"
    assert final_events[0].data.get("success") is True

  @pytest.mark.asyncio
  async def test_streaming_event_types_are_valid(self, real_config):
    """All streamed events should have recognized types."""
    factory = AgentFactory(real_config)
    agent = factory.build()
    runner = Runner()
    deps = CLIContext(
      user="local",
      session_id="test-session",
      config=real_config,
      working_dir=__import__("pathlib").Path.cwd(),
    )

    valid_types = {"content_delta", "tool_call_start", "tool_call_result", "error", "final"}
    events = []

    async for event in runner.run_react_stream(agent, "Count to 3.", deps=deps):
      assert event.type in valid_types, f"Unexpected event type: {event.type}"
      events.append(event)

    assert len(events) > 0
    assert any(e.type == "final" for e in events)

  @pytest.mark.asyncio
  async def test_orchestrator_execute_with_real_llm(self, real_config):
    """Orchestrator should execute prompts end-to-end with real LLM."""
    orch = Orchestrator(config=real_config)
    await orch.initialize()

    try:
      events = []
      async for event in orch.execute("What is 2+2? Answer with just the number."):
        events.append(event)
        if event.type == "content_delta":
          print(event.data.get("content", ""), end="", flush=True)

      print()

      content_events = [e for e in events if e.type == "content_delta"]
      final_events = [e for e in events if e.type == "final"]

      assert len(content_events) > 0
      assert len(final_events) == 1
      assert final_events[0].data.get("success") is True

      # The response should contain "4"
      full_response = "".join(e.data.get("content", "") for e in content_events)
      assert "4" in full_response, f"Expected '4' in response, got: {full_response!r}"

    finally:
      await orch.shutdown()

  @pytest.mark.asyncio
  async def test_session_context_preserved(self, real_config):
    """Runner should maintain context within the same session_id."""
    factory = AgentFactory(real_config)
    agent = factory.build()
    runner = Runner()
    deps = CLIContext(
      user="local",
      session_id="context-test",
      config=real_config,
      working_dir=__import__("pathlib").Path.cwd(),
    )
    session_id = "test-context-session"

    # First turn: tell the assistant a fact
    async for event in runner.run_react_stream(
      agent,
      "Remember: my favorite color is blue.",
      deps=deps,
      session_id=session_id,
    ):
      if event.type == "final":
        assert event.data.get("success") is True

    # Second turn: ask about the fact using same session_id
    responses = []
    async for event in runner.run_react_stream(
      agent,
      "What is my favorite color? Answer in one word.",
      deps=deps,
      session_id=session_id,
    ):
      if event.type == "content_delta":
        responses.append(event.data.get("content", ""))
      if event.type == "final":
        assert event.data.get("success") is True

    full_response = "".join(responses).lower()
    print(f"\nContext response: {full_response!r}")
    assert "blue" in full_response, f"Expected 'blue' in context response, got: {full_response!r}"

  @pytest.mark.asyncio
  async def test_new_session_isolates_context(self, real_config):
    """new_session() should create a fresh session without prior context."""
    orch = Orchestrator(config=real_config)
    await orch.initialize()

    try:
      # First session
      async for event in orch.execute("Remember: the secret code is 12345."):
        pass

      # New session
      old_id = orch.session_id
      new_id = orch.new_session()
      assert new_id != old_id

      responses = []
      async for event in orch.execute("What is the secret code? Answer in one word."):
        if event.type == "content_delta":
          responses.append(event.data.get("content", ""))

      full_response = "".join(responses).lower()
      print(f"\nNew session response: {full_response!r}")
      # LLM should not know the code in a new session
      assert "12345" not in full_response, (
        f"Expected new session to not remember code, got: {full_response!r}"
      )

    finally:
      await orch.shutdown()
