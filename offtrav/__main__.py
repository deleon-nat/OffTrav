"""Lets the package run as `python -m offtrav ...` (ingest, ask, chat, plan). See cli.py."""
import sys

from .cli import main

sys.exit(main())
