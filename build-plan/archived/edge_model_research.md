# Research: Edge Models & Tool Calling (JSON vs. Native)

This document explores the architectural and practical implications of using forced JSON output versus native tool calling for small "edge" LLMs (models typically under 10B parameters, such as Gemma 2B/9B or models around ~4B-8B).

## Executive Summary

Your assessment (and Claude's suggestion) is completely accurate: **forcing a strict JSON response directly in the prompt is actively "fighting the model"**, especially for edge/small models. 

While the original design of OpenGrug utilizing a JSON structure for predictable parsing is logical from a traditional software engineering perspective, it fundamentally acts against the statistical nature of small generative models. Switching to native tool calling via API parameters (such as Ollama's `tools` parameter in `/api/chat`) is the single biggest unlock for making edge models perform reliably as agents.

---

## 1. Why Forcing JSON "Fights the Model"

Small LLMs (2B - 9B parameters) have limited reasoning capacities and attention spans compared to frontier models like GPT-4 or Claude 3.5 Sonnet. When you force them to communicate via hand-crafted JSON:

- **Probability Mass Tax:** An LLM generates tokens based on probabilities. When constrained to JSON, the model is forced to spend a significant portion of its "computational power" and attention just maintaining syntax (making sure brackets are closed, quotes are escaped, commas are in the right place). This detracts directly from its ability to *reason* about the actual task.
- **Error Cascades:** A single syntactical mistake in JSON (like a missing quote) ruins the entire output for standard parsers. Edge models are highly prone to these off-by-one errors.
- **Loss of Nuance and Conversational Flow:** By mandating a rigid JSON schema for the entire generation, the model has little room to "think out loud" (Chain-of-Thought reasoning). Letting the model output natural language first before invoking a tool drastically improves its reasoning.

## 2. The Native Tool Calling Paradigm

Modern frameworks and inference engines (like Ollama, vLLM) and newer instruction-tuned models have adopted "native tool calling".

> [!NOTE] 
> *Context on Gemma 4 E4B: The Gemma 4 family (including variants like the Efficient 4B) is designed with native agentic workflows and tool-calling explicitly in mind. Because these models are fine-tuned to recognize control tokens for tools automatically, relying on legacy JSON-forcing methods actively underutilizes the model's native architecture.*

### How it Works (Ollama's `/api/chat`)
Instead of prompting the model to format text as JSON, you pass the available tools to the inference engine (Ollama) in the API request payload using the `tools` array.

1.  **Server-Side Control:** Ollama translates your JSON Schema tool definitions into the specific "Control Tokens" or formats the model was fine-tuned on (e.g., `<tool_call>`, `[CALL_TOOL]`).
2.  **Constrained Decoding / Grammars:** When the model decides to use a tool, the inference engine itself forces the output to conform to valid JSON logic *under the hood*, handling the syntax generation. 
3.  **Structured API Response:** When Ollama returns the completion to your Python code, it strips out the raw model output and provides perfectly formatted JSON objects within a `tool_calls` array in the response header. 

## 3. Advantages of the Native Approach for OpenGrug

There are profound benefits to adopting this architecture for `opengrug`:

> [!TIP]
> **1. Zero Parsing Errors:** You eliminate `json.loads()` errors. Ollama guarantees the `tool_calls` array is structurally sound.
> 
> **2. Smaller System Prompts:** You don't have to waste hundreds of tokens in the system prompt explaining *how* to output JSON ("Respond in this exact JSON format: ..."). The engine handles it, preserving prompt space.
> 
> **3. Breathing Room for the Model:** The LLM can reply with raw text ("I found the file, let me run a command on it") and then attach a tool call natively. This makes the bot significantly more responsive, conversational, and adaptable.

## 4. Conclusion

For edge models (2B-9B), native tool calling is not just a syntax shortcut—it is a functional requirement for stability.

By refactoring OpenGrug to use Ollama's native tool API:
- The Python code becomes cleaner (no manual regex or JSON parsing strings).
- The LLM can dedicate 100% of its parameters to solving the actual problem rather than acting as a faulty JSON formatter.
- The overall system transitions from "fragile prompting" to robust agentic behavior.
