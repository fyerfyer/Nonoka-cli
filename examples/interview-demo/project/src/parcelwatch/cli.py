from __future__ import annotations

import argparse
import json

from parcelwatch.reconcile import reconcile_feed


def main() -> None:
  parser = argparse.ArgumentParser(prog="parcelwatch")
  parser.add_argument("feed")
  args = parser.parse_args()
  # BUG: output is not deterministic and has no guaranteed trailing newline.
  print(json.dumps(reconcile_feed(args.feed)), end="")


if __name__ == "__main__":
  main()
