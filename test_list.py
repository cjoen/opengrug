import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.orchestrator import ToolRegistry, GrugRouter

registry = ToolRegistry()
router = GrugRouter(registry)

res = registry.execute("list_capabilities", {})
assert res.success is True
assert "I can help you" in res.output
print("[PASS] list_capabilities returns expected output")

res2 = registry.execute("reply_to_user", {"message": "Hello!"})
assert res2.success is True
assert res2.output == "Hello!"
print("[PASS] reply_to_user returns the message")

print("\n--- test_list.py ALL PASSED ---")
