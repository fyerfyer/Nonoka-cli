import asyncio
import json

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


def test_logs_can_report_operational_signals_from_bridge_trace(tmp_path, capsys):
  trace = tmp_path / "trace.ndjson"
  trace.write_text("\n".join([
    json.dumps({
      "ts": "2026-07-27T00:00:00+00:00",
      "request_id": "req-1",
      "event": "request_entry",
    }),
    json.dumps({
      "ts": "2026-07-27T00:00:02+00:00",
      "request_id": "req-1",
      "event": "stream_event",
      "event_type": "content_delta",
      "data": {"len": 2},
    }),
    json.dumps({
      "ts": "2026-07-27T00:00:03+00:00",
      "request_id": "req-1",
      "event": "stream_event",
      "event_type": "final",
      "data": {"success": True},
    }),
  ]))
  args = type("Args", (), {"trace": [str(trace)], "json": True})()
  assert logs_cmd.run_logs(args) == 0
  payload = json.loads(capsys.readouterr().out)
  assert payload["traces"] == 1
  assert payload["p50"]["time_to_first_output_seconds"] == 2
  assert payload["individual"][0]["output_timing_source"] == "bridge_stream_event"
