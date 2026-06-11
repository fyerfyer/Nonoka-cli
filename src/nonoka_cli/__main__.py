"""Allow running nonoka-cli as a module: python -m nonoka_cli."""

from nonoka_cli.cli import main

if __name__ == "__main__":
  import sys
  sys.exit(main())
