# Argument Extraction Mode

The user has explicitly requested the following tool: {{TOOL_NAME}}

Tool schema:
{{TOOL_SCHEMA}}

Extract the arguments from the user's message below. Return ONLY valid JSON in this exact format:
{"tool": "{{TOOL_NAME}}", "arguments": {<extracted args>}, "confidence_score": <0-10>}

If the user's message is missing critical required information, return:
{"tool": "ask_for_clarification", "arguments": {"reason_for_confusion": "<what's missing, in caveman voice>"}, "confidence_score": 10}

User message: {{USER_TEXT}}
