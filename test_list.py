import os
import json
from core.orchestrator import ToolRegistry, GrugRouter

registry = ToolRegistry()
router = GrugRouter(registry)

res = registry.execute("list_capabilities", {})
print("List capabilities output:")
print(res.output)

res2 = registry.execute("reply_to_user", {"message": "Hello!"})
print("\nReply output:")
print(res2.output)
