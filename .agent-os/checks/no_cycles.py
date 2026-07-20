"""
Standing-goal check: the edit engine's add_relation must reject circular
dependencies and self-loops. Exit 0 = invariant holds, 1 = violated.
"""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from tests.test_engine import _make_project
from engine.edit_engine import apply_command

p = _make_project()
p.build_lookups()

# Fixture network: A1000 -> A1010 -> A1020. Closing A1020 -> A1000 would loop.
ok_cycle, _ = apply_command(
    p, {"action": "add_relation", "predecessor_id": "A1020", "successor_id": "A1000", "type": "fs"}
)
ok_self, _ = apply_command(
    p, {"action": "add_relation", "predecessor_id": "A1000", "successor_id": "A1000", "type": "fs"}
)

if ok_cycle:
    print("FAIL: circular dependency A1020 -> A1000 was accepted")
    sys.exit(1)
if ok_self:
    print("FAIL: self-loop A1000 -> A1000 was accepted")
    sys.exit(1)

print("OK: circular dependencies and self-loops are rejected")
sys.exit(0)
