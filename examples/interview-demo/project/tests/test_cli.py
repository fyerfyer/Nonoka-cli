import json

from parcelwatch.cli import main


def test_cli_emits_deterministic_json_with_newline(monkeypatch, capsys, tmp_path):
  feed = tmp_path / "feed.jsonl"
  feed.write_text(
    '{"event_id":"a","package_id":"p2","status":"CREATED","occurred_at":"2026-07-30T00:00:00Z"}\n',
    encoding="utf-8",
  )
  monkeypatch.setattr("sys.argv", ["parcelwatch", str(feed)])

  main()

  output = capsys.readouterr().out
  assert output.endswith("\n")
  assert output == json.dumps(json.loads(output), sort_keys=True) + "\n"
