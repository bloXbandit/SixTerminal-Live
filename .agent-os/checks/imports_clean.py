"""
Standing-goal check: the core modules must import cleanly (no syntax errors,
no broken imports). Exit 0 = OK; a failed import exits non-zero automatically.
"""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import server                     # noqa: F401
import engine.edit_engine         # noqa: F401
import engine.schedule_model      # noqa: F401
import engine.xer_reader          # noqa: F401
import engine.xml_reader          # noqa: F401
import engine.xml_writer          # noqa: F401
import interpreter.llm_interpreter  # noqa: F401

print("OK: core modules import cleanly")
