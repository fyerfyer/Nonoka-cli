"""Tests for local tool loading and built-in tools."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from nonoka.core.context import RunContext

from nonoka_cli.core.context import CLIContext
from nonoka_cli.tools.builtins.file_tools import get_tools as get_builtin_tools
from nonoka_cli.tools.loader import ToolLoader


class TestToolLoader:
  """Tests for ToolLoader directory scanning."""

  def test_load_all_includes_builtins_by_default(self):
    loader = ToolLoader([], include_builtins=True)
    registry = loader.load_all()

    names = registry.names()
    assert "read_file" in names
    assert "view" in names
    assert "view_dir" in names
    assert "write_file" in names
    assert "edit_file" in names
    assert "search_and_replace" in names
    assert "delete_file" in names
    assert "list_dir" in names
    assert "grep_files" in names
    assert "execute_command" in names

  def test_load_all_can_exclude_builtins(self):
    loader = ToolLoader([], include_builtins=False)
    registry = loader.load_all()
    assert registry.names() == []

  def test_scan_directory_discovers_decorated_tools(self, tmp_path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    tool_file = tools_dir / "my_tools.py"
    tool_file.write_text(
      """
from nonoka import tool

@tool
def hello_tool(name: str) -> str:
  return f"Hello, {name}!"

@tool(description="A greeting tool")
def greet(name: str, greeting: str = "Hi") -> str:
  return f"{greeting}, {name}!"
"""
    )

    loader = ToolLoader([tools_dir], include_builtins=False)
    registry = loader.load_all()

    assert "hello_tool" in registry.names()
    assert "greet" in registry.names()

  def test_scan_directory_skips_files_without_tools(self, tmp_path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "helpers.py").write_text("def helper():\n  pass\n")

    loader = ToolLoader([tools_dir], include_builtins=False)
    registry = loader.load_all()
    assert registry.names() == []

  def test_missing_directory_is_handled_gracefully(self, tmp_path):
    missing = tmp_path / "does_not_exist"
    loader = ToolLoader([missing], include_builtins=False)
    registry = loader.load_all()
    assert registry.names() == []

  def test_reload_refreshes_registry(self, tmp_path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    tool_file = tools_dir / "dynamic.py"
    tool_file.write_text(
      """from nonoka import tool

@tool
def first() -> str:
  return 'first'
"""
    )

    loader = ToolLoader([tools_dir], include_builtins=False)
    registry = loader.load_all()
    assert "first" in registry.names()
    assert "second" not in registry.names()

    tool_file.write_text(
      """from nonoka import tool

@tool
def second() -> str:
  return 'second'
"""
    )
    registry = loader.reload()
    assert "second" in registry.names()


class TestBuiltinTools:
  """Tests for built-in file-system tools."""

  @pytest.fixture
  def ctx(self, tmp_path):
    mock_ctx = MagicMock(spec=RunContext)
    mock_ctx.deps = CLIContext(
      user="local",
      session_id="test",
      config=MagicMock(),
      working_dir=tmp_path,
    )
    return mock_ctx

  @pytest.fixture
  def registry(self):
    loader = ToolLoader([], include_builtins=True)
    return loader.load_all()

  @pytest.mark.asyncio
  async def test_write_file_creates_file(self, registry, ctx, tmp_path):
    write_tool = registry.get("write_file")
    result = await write_tool.invoke(ctx, {"path": "hello.txt", "content": "world"})

    assert "Success" in result["result"]
    assert (tmp_path / "hello.txt").read_text() == "world"

  @pytest.mark.asyncio
  async def test_write_file_append(self, registry, ctx, tmp_path):
    (tmp_path / "hello.txt").write_text("hello ")
    write_tool = registry.get("write_file")
    result = await write_tool.invoke(
      ctx,
      {"path": "hello.txt", "content": "world", "append": True},
    )

    assert "appended" in result["result"]
    assert (tmp_path / "hello.txt").read_text() == "hello world"

  @pytest.mark.asyncio
  async def test_read_file_returns_content(self, registry, ctx, tmp_path):
    (tmp_path / "hello.txt").write_text("hello world")

    read_tool = registry.get("read_file")
    result = await read_tool.invoke(ctx, {"path": "hello.txt"})

    assert "hello world" in result["result"]

  @pytest.mark.asyncio
  async def test_edit_file_replaces_unique_string(self, registry, ctx, tmp_path):
    (tmp_path / "file.txt").write_text("foo bar baz")

    edit_tool = registry.get("edit_file")
    result = await edit_tool.invoke(
      ctx,
      {"path": "file.txt", "old_string": "bar", "new_string": "qux"},
    )

    assert "Success" in result["result"]
    assert (tmp_path / "file.txt").read_text() == "foo qux baz"

  @pytest.mark.asyncio
  async def test_edit_file_rejects_non_unique_string(self, registry, ctx, tmp_path):
    (tmp_path / "file.txt").write_text("foo foo foo")

    edit_tool = registry.get("edit_file")
    result = await edit_tool.invoke(
      ctx,
      {"path": "file.txt", "old_string": "foo", "new_string": "bar"},
    )

    assert "not unique" in result["result"]
    assert (tmp_path / "file.txt").read_text() == "foo foo foo"

  @pytest.mark.asyncio
  async def test_delete_file_removes_file(self, registry, ctx, tmp_path):
    target = tmp_path / "delete_me.txt"
    target.write_text("bye")

    delete_tool = registry.get("delete_file")
    result = await delete_tool.invoke(ctx, {"path": "delete_me.txt"})

    assert "Success" in result["result"]
    assert not target.exists()

  @pytest.mark.asyncio
  async def test_list_dir_shows_entries(self, registry, ctx, tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b_dir").mkdir()

    list_tool = registry.get("list_dir")
    result = await list_tool.invoke(ctx, {"path": "."})

    output = result["result"]
    assert "a.txt" in output
    assert "b_dir/" in output

  @pytest.mark.asyncio
  async def test_grep_files_finds_matches(self, registry, ctx, tmp_path):
    (tmp_path / "one.py").write_text("print('hello')\n")
    (tmp_path / "two.py").write_text("def hello():\n  pass\n")

    grep_tool = registry.get("grep_files")
    result = await grep_tool.invoke(ctx, {"pattern": "hello", "glob": "*.py"})

    output = result["result"]
    assert "one.py" in output
    assert "two.py" in output

  @pytest.mark.asyncio
  async def test_execute_command_runs_shell(self, registry, ctx, tmp_path):
    cmd_tool = registry.get("execute_command")
    result = await cmd_tool.invoke(ctx, {"command": "echo test-output"})

    assert "exit code 0" in result["result"]
    assert "test-output" in result["result"]

  @pytest.mark.asyncio
  async def test_view_shows_line_numbers(self, registry, ctx, tmp_path):
    (tmp_path / "code.py").write_text("line one\nline two\nline three\n")

    view_tool = registry.get("view")
    result = await view_tool.invoke(ctx, {"path": "code.py", "offset": 2, "limit": 2})

    output = result["result"]
    assert "2 | line two" in output
    assert "3 | line three" in output
    assert "1 | line one" not in output

  @pytest.mark.asyncio
  async def test_view_dir_shows_tree(self, registry, ctx, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("pass")
    (tmp_path / "README.md").write_text("hi")

    view_dir_tool = registry.get("view_dir")
    result = await view_dir_tool.invoke(ctx, {"path": "."})

    output = result["result"]
    assert "src/" in output
    assert "main.py" in output
    assert "README.md" in output

  @pytest.mark.asyncio
  async def test_search_and_replace_replaces_all(self, registry, ctx, tmp_path):
    (tmp_path / "file.txt").write_text("foo foo foo")

    srp_tool = registry.get("search_and_replace")
    result = await srp_tool.invoke(
      ctx,
      {"path": "file.txt", "old_string": "foo", "new_string": "bar"},
    )

    assert "replaced all" in result["result"]
    assert (tmp_path / "file.txt").read_text() == "bar bar bar"

  @pytest.mark.asyncio
  async def test_search_and_replace_first_only(self, registry, ctx, tmp_path):
    (tmp_path / "file.txt").write_text("foo foo foo")

    srp_tool = registry.get("search_and_replace")
    result = await srp_tool.invoke(
      ctx,
      {
        "path": "file.txt",
        "old_string": "foo",
        "new_string": "bar",
        "replace_all": False,
      },
    )

    assert "first" in result["result"]
    assert (tmp_path / "file.txt").read_text() == "bar foo foo"

  def test_get_tools_returns_all_builtins(self):
    tools = get_builtin_tools()
    names = [t.name for t in tools]
    assert "read_file" in names
    assert "view" in names
    assert "view_dir" in names
    assert "search_and_replace" in names
    assert "write_file" in names
    assert len(tools) == 10
