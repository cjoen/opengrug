# OpenGrug Testing & Evals Strategy

## Current State: Intent Evals
The OpenGrug test suite includes a dedicated reasoning framework in `evals/run_evals.py`. Currently, it tests **LLM Intent**—whether the model selects the correct tool and provides the correct schema arguments for a given user prompt. 

Because it intercepts the LLM response *before* execution, it runs fast, relies on deterministic JSON assertions, and does not produce side effects in the local database.

---

## Future Implementation: Output Evals (LLM-as-a-Judge)
While Intent Evals guarantee the LLM chooses the right tool, they do not test the quality of the final response (e.g., how well Grug formats a board summary). To test output quality, we will implement an "LLM-as-a-Judge" grading pipeline.

### The Problem
Traditional testing uses exact string matching (`assert output == "Here are your tasks"`). LLM outputs are non-deterministic, making string matching brittle and useless for subjective tasks like summarization.

### The Solution: LLM-as-a-Judge
We will use a second LLM inference call to "grade" the output of the first LLM inference call based on a strict rubric.

### Planned Workflow
1. **Mock Tool Execution:** 
   When the primary LLM chooses a tool (e.g., `summarize_board`), the test harness will inject a static, fake dataset (e.g., "Mock Data: 2 open tasks, 1 note") instead of querying the real database.
2. **Generate Final Response:** 
   The primary LLM will use this mocked data to generate its final string response to the user.
3. **Execute the Judge:** 
   The harness will pass the mock data and the LLM's final response to a secondary "Judge" prompt.
   *Example Judge Prompt:* `"You are grading an AI assistant. The assistant was provided the following data: [Mock Data]. The assistant replied with: [Final Response]. Did the assistant accurately include all tasks from the data without hallucinating? Reply exactly with PASS or FAIL."`
4. **Assert Results:** 
   The test passes if the Judge replies `PASS`.

### Proposed Changes to Dataset Schema
To support Output Evals, the `golden_dataset.jsonl` will be expanded to include an optional `judge_rubric` and `mock_tool_output`:

```json
{
  "session_id": "eval-output-001",
  "messages": [{"role": "user", "content": "What's on my board?"}],
  "expected_tool": "summarize_board",
  "mock_tool_output": "Task 1: Fix bug. Task 2: Write tests.",
  "judge_rubric": "Ensure both 'Fix bug' and 'Write tests' are mentioned clearly."
}
```

### Phased Rollout
1. Expand `run_evals.py` to check for `mock_tool_output`. If present, simulate the tool return and get the final LLM string.
2. Implement a lightweight `Judge` class that wraps `OllamaClient` specifically for pass/fail classification.
3. Add a `--run-judges` flag to `run_evals.py` to allow skipping the slower Judge evaluations during rapid development cycles.
