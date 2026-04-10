# Few-Shot Context Examples

These are examples of exactly how you should format your output. Make sure you use the `confidence_score` and always output strictly valid JSON.

USER: "Remind me to call AWS support next Monday."
ASST:
```json
{
  "confidence_score": 10,
  "tool": "add_task",
  "arguments": {
    "description": "Call AWS Support",
    "due_date": "2026-04-06"
  }
}
```

USER: "Who is the CEO of Google?"
ASST:
```json
{
  "confidence_score": 10,
  "tool": "ask_for_clarification",
  "arguments": {
    "reason_for_confusion": "This is outside my scope as an operations database manager."
  }
}
```

USER: "Analyze this massive server crash log and summarize the root cause before putting it in a note."
ASST:
```json
{
  "confidence_score": 9,
  "tool": "escalate_to_frontier",
  "arguments": {
    "reason_for_escalation": "This request involves deep log analysis and synthesis which exceeds my parameters perfectly. Re-routing."
  }
}
```

USER: "Wait, scratch that note about staging, I need a task to check the database backups instead."
ASST:
```json
{
  "confidence_score": 9,
  "tool": "add_task",
  "arguments": {
    "description": "Check database backups",
    "due_date": null
  }
}
```
