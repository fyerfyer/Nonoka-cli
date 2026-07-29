"""Tests for ChatRequestHandler."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nonoka.core.errors import RuntimeTerminatedError
from nonoka.core.runtime import TerminalReason, Termination

from nonoka_cli.bridge.handler import ChatRequestHandler
from nonoka_cli.bridge.protocol import (
  BRIDGE_PROTOCOL_VERSION,
  ChatMessage,
  ChatRequest,
  ExternalMCPServerDefinition,
  ExternalMCPToolDefinition,
  ExternalSkillDefinition,
  ExternalSkillToolDefinition,
  ExternalToolDefinition,
  ProtocolContract,
  ToolCall,
)
from nonoka_cli.core.task_state import TaskStateService


@pytest.fixture
def sent():
  return []


@pytest.fixture
def handler(sent):
  value = ChatRequestHandler(send=AsyncMock(side_effect=lambda msg: sent.append(msg)))
  value._negotiate_protocol = AsyncMock(return_value=True)
  return value


async def test_handle_rejects_missing_protocol_contract(sent):
  handler = ChatRequestHandler(send=AsyncMock(side_effect=lambda msg: sent.append(msg)))
  await handler.handle(ChatRequest(messages=[ChatMessage(role="user", content="hello")]))
  error = next(message for message in sent if message.type == "error")
  assert error.code == "protocol_contract_required"
  assert handler.orchestrator is None


async def test_handle_acknowledges_compatible_protocol_before_initialization(sent):
  handler = ChatRequestHandler(send=AsyncMock(side_effect=lambda msg: sent.append(msg)))
  initialized = AsyncMock(side_effect=RuntimeError("stop"))
  with patch.object(handler, "_ensure_orchestrator", new=initialized):
    await handler.handle(
      ChatRequest(
        protocol=ProtocolContract(
          version=BRIDGE_PROTOCOL_VERSION,
          required_capabilities=["persistent_runtime_limits"],
        ),
        messages=[ChatMessage(role="user", content="hello")],
      )
    )
  assert sent[0].type == "protocol_ack"
  assert "persistent_runtime_limits" in sent[0].capabilities


async def test_handle_rejects_missing_required_capability(sent):
  handler = ChatRequestHandler(send=AsyncMock(side_effect=lambda msg: sent.append(msg)))
  await handler.handle(
    ChatRequest(
      protocol=ProtocolContract(version="1.0", required_capabilities=["not_supported"]),
      messages=[ChatMessage(role="user", content="hello")],
    )
  )
  error = next(message for message in sent if message.type == "error")
  assert error.code == "protocol_incompatible"
  assert error.details["missing_capabilities"] == ["not_supported"]


async def test_handle_new_session(handler, sent):
  with patch.object(handler, "_ensure_orchestrator", new=AsyncMock()):
    with patch.object(handler, "_apply_session", new=AsyncMock()):
      orc = MagicMock()
      orc.session_id = "new-session-id"
      orc.new_session = AsyncMock(return_value="new-session-id")
      orc.execute = MagicMock(return_value=async_empty())
      handler._orchestrator = orc

      msg = ChatRequest(
        messages=[ChatMessage(role="user", content="hello")],
        new_session=True,
      )
      await handler.handle(msg)

      orc.new_session.assert_awaited_once()
      orc.execute.assert_called_once_with("hello", working_dir=handler._working_dir)
      assert any(m.type == "session_init" for m in sent)


async def test_handle_title_uses_tool_free_title_path(handler, sent):
  with patch.object(handler, "_ensure_orchestrator", new=AsyncMock()):
    with patch.object(handler, "_apply_session", new=AsyncMock()):
      orc = MagicMock()
      orc.session_id = "title-session"
      orc.execute_title = MagicMock(return_value=async_empty())
      handler._orchestrator = orc
      handler._session_id = "title-session"
      await handler.handle(
        ChatRequest(
          purpose="title",
          messages=[ChatMessage(role="user", content="Generate a title")],
        )
      )
      orc.execute_title.assert_called_once_with(
        prompt="Generate a title",
        working_dir=handler._working_dir,
      )


async def test_existing_orchestrator_receives_generation_overrides(handler):
  orchestrator = MagicMock()
  handler._orchestrator = orchestrator

  await handler._ensure_orchestrator(
    ChatRequest(
      messages=[ChatMessage(role="user", content="hello")],
      temperature=0.0,
      max_turns=12,
      timeout_seconds=90.0,
      tool_budget=30,
    )
  )

  orchestrator.set_generation_options.assert_called_once_with(
    temperature=0.0,
    max_turns=12,
    timeout_seconds=90.0,
    wall_timeout_seconds=None,
    tool_budget=30,
    max_context_bytes=None,
    max_external_result_bytes=None,
    require_workspace_mutation=False,
    require_observed_effect=False,
    require_focused_verification=False,
    verification_enforcement="strict",
    max_completion_corrections=1,
  )


async def test_handle_resume_approval(handler, sent):
  with patch.object(handler, "_ensure_orchestrator", new=AsyncMock()):
    with patch.object(handler, "_apply_session", new=AsyncMock()):
      orc = MagicMock()
      orc.session_id = "sess-1"
      orc.resume_approval = MagicMock(return_value=async_empty())
      handler._orchestrator = orc
      handler._session_id = "sess-1"

      msg = ChatRequest(
        messages=[
          ChatMessage(role="user", content="hello"),
          ChatMessage(
            role="tool",
            content=json.dumps(
              [
                {
                  "type": "tool-approval-response",
                  "toolCallId": "call_1",
                  "approved": True,
                }
              ]
            ),
          ),
        ],
      )
      await handler.handle(msg)

      orc.resume_approval.assert_called_once()
      call_kwargs = orc.resume_approval.call_args.kwargs
      assert call_kwargs["session_id"] == "sess-1"
      assert call_kwargs["approvals"] == {"call_1": {"approved": True}}


async def test_handle_with_external_tools_executes(handler, sent):
  with patch.object(handler, "_ensure_orchestrator", new=AsyncMock()):
    with patch.object(handler, "_apply_session", new=AsyncMock()):
      orc = MagicMock()
      orc.session_id = "sess-1"
      orc.execute_with_external_tools = MagicMock(return_value=async_empty())
      handler._orchestrator = orc
      handler._session_id = "sess-1"

      msg = ChatRequest(
        messages=[ChatMessage(role="user", content="run ls")],
        tools=[
          ExternalToolDefinition(
            name="bash",
            description="Run commands",
            parameters={"type": "object", "properties": {}},
          )
        ],
      )
      await handler.handle(msg)

      orc.execute_with_external_tools.assert_called_once()
      call_kwargs = orc.execute_with_external_tools.call_args.kwargs
      assert call_kwargs["prompt"] == "run ls"
      assert call_kwargs["working_dir"] == handler._working_dir
      assert len(call_kwargs["tools"]) == 1
      assert call_kwargs["tools"][0].name == "bash"


async def test_handle_with_external_tools_resumes(handler, sent):
  with patch.object(handler, "_ensure_orchestrator", new=AsyncMock()):
    with patch.object(handler, "_apply_session", new=AsyncMock()):
      orc = MagicMock()
      orc.session_id = "sess-1"
      orc.resume_external_tools = MagicMock(return_value=async_empty())
      handler._orchestrator = orc
      handler._session_id = "sess-1"

      msg = ChatRequest(
        messages=[
          ChatMessage(role="user", content="run ls"),
          ChatMessage(
            role="tool",
            content="total 0",
            tool_call_id="call_1",
          ),
        ],
        tools=[
          ExternalToolDefinition(
            name="bash",
            description="Run commands",
            parameters={"type": "object", "properties": {}},
          )
        ],
      )
      await handler.handle(msg)

      orc.resume_external_tools.assert_called_once()
      call_kwargs = orc.resume_external_tools.call_args.kwargs
      assert call_kwargs["session_id"] == "sess-1"
      assert call_kwargs["results"] == {"call_1": "total 0"}
      assert len(call_kwargs["tools"]) == 1


async def test_handle_with_external_tools_passes_host_system_prompt(handler, sent):
  with patch.object(handler, "_ensure_orchestrator", new=AsyncMock()):
    with patch.object(handler, "_apply_session", new=AsyncMock()):
      orc = MagicMock()
      orc.session_id = "sess-1"
      orc.execute_with_external_tools = MagicMock(return_value=async_empty())
      handler._orchestrator = orc
      handler._session_id = "sess-1"

      msg = ChatRequest(
        messages=[
          ChatMessage(role="system", content="Host prompt."),
          ChatMessage(role="user", content="run ls"),
        ],
        tools=[
          ExternalToolDefinition(
            name="bash",
            description="Run commands",
            parameters={"type": "object", "properties": {}},
          )
        ],
      )
      await handler.handle(msg)

      call_kwargs = orc.execute_with_external_tools.call_args.kwargs
      assert call_kwargs["host_system_prompt"] == "Host prompt."


def test_extract_tool_results_filters_unknown_ids():
  msg = ChatRequest(
    messages=[
      ChatMessage(
        role="assistant",
        content="",
        tool_calls=[ToolCall(id="call_1", name="bash", arguments="{}")],
      ),
      ChatMessage(role="tool", content="ok", tool_call_id="call_1"),
      ChatMessage(role="tool", content="orphan", tool_call_id="call_999"),
    ]
  )
  results = ChatRequestHandler._extract_tool_results(msg)
  assert results == {"call_1": "ok"}


def test_extract_tool_results_accepts_all_when_no_pending_ids():
  msg = ChatRequest(
    messages=[
      ChatMessage(role="assistant", content="Hi."),
      ChatMessage(role="tool", content="ok", tool_call_id="call_1"),
    ]
  )
  results = ChatRequestHandler._extract_tool_results(msg)
  assert results == {"call_1": "ok"}


def test_extract_tool_results_preserves_typed_observation_receipt():
  receipt = {
    "result": "preview",
    "host": "opencode",
    "artifact_ref": "/tmp/output.txt",
    "original_bytes": 9000,
    "truncated": False,
    "completeness": "partial",
  }
  msg = ChatRequest(
    messages=[
      ChatMessage(
        role="assistant",
        content="",
        tool_calls=[ToolCall(id="call_1", name="inspect", arguments="{}")],
      ),
      ChatMessage(
        role="tool",
        content="preview",
        tool_call_id="call_1",
        result=receipt,
      ),
    ]
  )

  assert ChatRequestHandler._extract_tool_results(msg) == {"call_1": receipt}


def test_extract_tool_results_preserves_empty_output_receipt():
  workspace = {
    "root": "/testbed",
    "before_digest": "same",
    "after_digest": "same",
    "created": [],
    "modified": [],
    "deleted": [],
  }
  receipt = {
    "result": "",
    "exit_code": 1,
    "host": "opencode",
    "completeness": "complete",
    "workspace": workspace,
  }
  msg = ChatRequest(
    messages=[
      ChatMessage(
        role="assistant",
        content="",
        tool_calls=[ToolCall(id="call_1", name="bash", arguments="{}")],
      ),
      ChatMessage(
        role="tool",
        content="",
        tool_call_id="call_1",
        result=receipt,
      ),
    ]
  )

  assert ChatRequestHandler._extract_tool_results(msg) == {"call_1": receipt}


def test_extract_tool_results_keeps_empty_receipt_in_parallel_batch():
  workspace = {
    "root": "/testbed",
    "before_digest": "same",
    "after_digest": "same",
  }
  empty_receipt = {
    "result": "",
    "exit_code": 0,
    "host": "opencode",
    "completeness": "complete",
    "workspace": workspace,
  }
  output_receipt = {
    **empty_receipt,
    "result": "status output",
  }
  msg = ChatRequest(
    messages=[
      ChatMessage(
        role="assistant",
        content="",
        tool_calls=[
          ToolCall(id="call_1", name="bash", arguments="{}"),
          ToolCall(id="call_2", name="bash", arguments="{}"),
        ],
      ),
      ChatMessage(role="tool", content="", tool_call_id="call_1", result=empty_receipt),
      ChatMessage(
        role="tool",
        content="status output",
        tool_call_id="call_2",
        result=output_receipt,
      ),
    ]
  )

  assert ChatRequestHandler._extract_tool_results(msg) == {
    "call_1": empty_receipt,
    "call_2": output_receipt,
  }


async def async_empty() -> AsyncIterator[None]:
  if False:
    yield None


async def test_consume_stream_preserves_structured_runtime_termination(handler, sent):
  async def terminated_stream():
    if False:
      yield None
    raise RuntimeTerminatedError(
      Termination(
        reason=TerminalReason.EXECUTION_POLICY_VIOLATION,
        message="protected input changed",
        dimension="workspace_policy",
        diagnostics={"paths": ["fixture.db"]},
      )
    )

  await handler._consume_stream(terminated_stream())

  error = next(message for message in sent if message.type == "error")
  finish = next(message for message in sent if message.type == "finish")
  assert error.code == "execution_policy_violation"
  assert error.details["termination"]["diagnostics"] == {"paths": ["fixture.db"]}
  assert finish.termination["reason"] == "execution_policy_violation"


async def test_handle_with_external_mcp_and_skills_passes_them_to_orchestrator(handler, sent):
  with patch.object(handler, "_ensure_orchestrator", new=AsyncMock()):
    with patch.object(handler, "_apply_session", new=AsyncMock()):
      orc = MagicMock()
      orc.session_id = "sess-1"
      orc.execute_with_external_tools = MagicMock(return_value=async_empty())
      handler._orchestrator = orc
      handler._session_id = "sess-1"

      msg = ChatRequest(
        messages=[ChatMessage(role="user", content="run ls")],
        tools=[
          ExternalToolDefinition(
            name="bash",
            description="Run commands",
            parameters={"type": "object", "properties": {}},
          )
        ],
        external_mcp_servers=[
          ExternalMCPServerDefinition(
            name="fs",
            description="Filesystem",
            tools=[
              ExternalMCPToolDefinition(
                name="list",
                description="List",
                parameters={"type": "object", "properties": {}},
              )
            ],
          )
        ],
        external_skills=[
          ExternalSkillDefinition(
            name="code-review",
            description="Review",
            tools=[
              ExternalSkillToolDefinition(
                name="review_file",
                description="Review",
                parameters={"type": "object", "properties": {}},
              )
            ],
          )
        ],
      )
      await handler.handle(msg)

      orc.execute_with_external_tools.assert_called_once()
      call_kwargs = orc.execute_with_external_tools.call_args.kwargs
      assert call_kwargs["external_mcp_servers"] == msg.external_mcp_servers
      assert call_kwargs["external_skills"] == msg.external_skills
      assert len(call_kwargs["tools"]) == 1
      assert call_kwargs["tools"][0].name == "bash"


async def test_sync_task_state_from_todowrite(tmp_path):
  sent = []
  handler = ChatRequestHandler(send=AsyncMock(side_effect=lambda msg: sent.append(msg)))
  handler._session_id = "sess-1"
  handler._orchestrator = MagicMock()
  handler._orchestrator.session_id = "sess-1"
  handler._task_state_service = TaskStateService(
    tasks_dir=".nonoka/tasks",
    base_dir=tmp_path,
  )

  from nonoka.core.runner import StreamEvent

  event = StreamEvent(
    type="tool_call_start",
    data={
      "tool_calls": [
        {
          "id": "call_todo",
          "function": {
            "name": "todowrite",
            "arguments": json.dumps(
              {
                "todos": [
                  {"id": "1", "content": "step 1", "status": "completed"},
                  {"id": "2", "content": "step 2", "status": "in_progress"},
                ]
              }
            ),
          },
        }
      ]
    },
  )
  handler._sync_task_state(event)

  state = handler._task_state_service.load("sess-1")
  assert state is not None
  assert len(state.steps) == 2
  assert state.steps[0].status == "completed"
  assert state.steps[1].status == "in_progress"
