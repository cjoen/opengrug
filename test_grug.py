import os
from core.storage import GrugStorage
from core.vectors import VectorMemory
from core.orchestrator import ToolRegistry, GrugRouter

def run_tests():
    print("--- TESTING GRUG ARCHITECTURE ---")
    storage = GrugStorage(base_dir="./brain_test")
    vector_memory = VectorMemory(db_path="./brain_test/memory.db")
    registry = ToolRegistry()

    # Tool Bindings
    registry.register_python_tool(
        name="add_note",
        schema={
            "properties": {
                "content": {"type": "string"}
            },
            "required": ["content"]
        },
        func=storage.add_note
    )

    router = GrugRouter(registry)
    with open("prompts/system.md", "r", encoding="utf-8") as f:
        base_prompt = f.read()

    print("\n[TEST 1] Caveman Storage Flow:")
    # Mock LLM to return `add_note`
    router.invoke_gemma = lambda prompt, model="gemma": '{"tool": "add_note", "arguments": {"content": "Fire is hot."}}'
    res = router.route_message("Store this idea: Fire is hot.", context="Test Env", base_system_prompt=base_prompt)
    print(f"Result: {getattr(res, 'output', res)}")
    
    print("\n[TEST 2] Graceful Offline Degradation:")
    # Mock LLM to try escalate first, then fallback to local insight if warning is present
    def mock_gemma_fallback(prompt, model="gemma"):
        if "OFFLINE" in prompt:
            return '{"tool": "add_note", "arguments": {"content": "Grug no reach cloud. Fire hot."}}'
        return '{"tool": "escalate_to_frontier", "arguments": {"reason_for_escalation": "Too hard"}}'
        
    router.invoke_gemma = mock_gemma_fallback
    os.environ["CLAUDE_API_KEY"] = ""  # Force offline
    res_degraded = router.route_message("Explain quantum mechanics.", context="Test Env", base_system_prompt=base_prompt)
    print(f"Result: {getattr(res_degraded, 'output', res_degraded)}")

if __name__ == "__main__":
    run_tests()
