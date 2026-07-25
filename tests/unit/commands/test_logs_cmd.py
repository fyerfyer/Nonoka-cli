import asyncio

from nonoka_cli.commands import logs_cmd


def test_logs_prints_structured_events(tmp_path, monkeypatch, capsys):
  monkeypatch.setattr(logs_cmd, "_event_db", lambda: tmp_path / "events.db")
  async def seed():
    store = logs_cmd.SQLiteEventStore(tmp_path / "events.db")
    await store.append("s1", "run.started", {"model": "test"})
    await store.close()
  asyncio.run(seed())
  args = type("Args", (), {"session_id": "s1", "limit": 10, "json": False})()
  assert logs_cmd.run_logs(args) == 0
  assert "run.started" in capsys.readouterr().out
