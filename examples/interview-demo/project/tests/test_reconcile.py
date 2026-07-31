from parcelwatch.reconcile import reconcile_feed


def test_reconcile_preserves_first_duplicate_and_transition_contract(tmp_path):
  feed = tmp_path / "feed.jsonl"
  feed.write_text(
    '\n'.join(
      [
        '{"event_id":"e2","package_id":"p1","status":"IN_TRANSIT","occurred_at":"2026-07-30T10:30:00+08:00"}',
        '{"event_id":"e1","package_id":"p1","status":"CREATED","occurred_at":"2026-07-30T02:00:00Z"}',
        '{"event_id":"e2","package_id":"p1","status":"DELIVERED","occurred_at":"2026-07-30T03:00:00Z"}',
        '{not-json}',
        '{"event_id":"e3","package_id":"p1","status":"DELIVERED","occurred_at":"2026-07-30T04:00:00Z"}',
      ]
    )
    + '\n',
    encoding="utf-8",
  )

  result = reconcile_feed(feed)

  assert result["accepted"] == ["e1", "e2", "e3"]
  assert result["states"] == {"p1": "DELIVERED"}
  assert result["rejected"] == [
    {"line": 3, "event_id": "e2", "reason": "duplicate_event_id"},
    {"line": 4, "reason": "malformed_json"},
  ]


def test_reconcile_rejects_skipped_transition_without_mutating_state(tmp_path):
  feed = tmp_path / "feed.jsonl"
  feed.write_text(
    '\n'.join(
      [
        '{"event_id":"a","package_id":"p2","status":"CREATED","occurred_at":"2026-07-30T00:00:00Z"}',
        '{"event_id":"b","package_id":"p2","status":"DELIVERED","occurred_at":"2026-07-30T00:01:00Z"}',
      ]
    )
    + '\n',
    encoding="utf-8",
  )

  result = reconcile_feed(feed)

  assert result["accepted"] == ["a"]
  assert result["states"] == {"p2": "CREATED"}
  assert result["rejected"] == [
    {"line": 2, "event_id": "b", "reason": "invalid_transition"}
  ]
