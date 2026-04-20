"""Orchestrator — the core message-processing pipeline.

Owns session management, context assembly, turn pruning, and routing.
Returns platform-agnostic Event dataclasses so adapters can translate
them to Slack, CLI, or any other UI.
"""

import threading
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Event dataclasses — pure state representations, no UI logic
# ---------------------------------------------------------------------------

@dataclass
class MessageReply:
    """A normal text reply to send back to the user."""
    text: str
    llm_response: Optional[str] = None
    user_message: str = ""
    assistant_content: str = ""


@dataclass
class ApprovalRequired:
    """A destructive tool needs human approval before execution."""
    tool_name: str
    arguments: dict
    user_id: str


@dataclass
class ErrorReply:
    """Something went wrong — send a fallback message."""
    text: str


class Orchestrator:
    """Stateless message processor. Call process_message() per inbound message."""

    def __init__(self, router, registry, session_store, storage, summarizer,
                 vector_memory, config, load_summary_files, build_system_prompt,
                 find_turn_boundary, auto_offload_pruned_turns, base_prompt):
        self.router = router
        self.registry = registry
        self.session_store = session_store
        self.storage = storage
        self.summarizer = summarizer
        self.vector_memory = vector_memory
        self.config = config
        self.load_summary_files = load_summary_files
        self.build_system_prompt = build_system_prompt
        self.find_turn_boundary = find_turn_boundary
        self.auto_offload_pruned_turns = auto_offload_pruned_turns
        self.base_prompt = base_prompt

    def _build_context(self, text, history):
        """Assemble system prompt with summaries, capped tail, and RAG."""
        import os
        summaries_dir = os.path.join(self.config.storage.base_dir, "summaries")
        summaries = self.load_summary_files(summaries_dir, self.config.memory.summary_days_limit)
        capped_tail = self.storage.get_capped_tail(self.config.memory.capped_tail_lines)

        rag_context = ""
        try:
            rag_hits = self.vector_memory.query_memory(text, limit=self.config.memory.rag_result_limit)
            if rag_hits and not rag_hits[0].get("offline"):
                rag_context = "\n".join(h["content"] for h in rag_hits)
        except Exception as e:
            print(f"[rag] pre-flight search failed: {e}")

        return self.build_system_prompt(self.base_prompt, summaries, capped_tail, rag_context=rag_context)

    def _prune_turns(self, system_prompt, messages):
        """Prune oldest turns when context exceeds target tokens."""
        estimated_tokens = len(str(system_prompt) + str(messages)) // 4
        while estimated_tokens > self.config.llm.target_context_tokens and len(messages) > 1:
            turn_end = self.find_turn_boundary(messages)
            pruned = messages[:turn_end]
            messages = messages[turn_end:]
            threading.Thread(
                target=self.auto_offload_pruned_turns,
                args=(pruned, self.summarizer, self.storage),
                daemon=True,
            ).start()
            estimated_tokens = len(str(system_prompt) + str(messages)) // 4
        return messages

    def process_message(self, text, thread_ts, channel_id, user_id):
        """Process a message and return an Event (MessageReply, ApprovalRequired, or ErrorReply).

        This is the main entry point. Platform adapters call this and translate
        the returned event into UI-specific actions.
        """
        try:
            # Inject schedule context for scheduler tools
            self.router._request_state._schedule_channel = channel_id
            self.router._request_state._schedule_user = user_id
            self.router._request_state._schedule_thread_ts = thread_ts

            session = self.session_store.get_or_create(thread_ts, channel_id)
            history = session["messages"][-self.config.memory.thread_history_limit:]

            system_prompt = self._build_context(text, history)
            messages = history + [{"role": "user", "content": text}]
            messages = self._prune_turns(system_prompt, messages)

            result = self.router.route_message(
                user_message=text,
                system_prompt=system_prompt,
                message_history=messages,
            )

            if result.requires_approval:
                self.session_store.set_pending_hitl(thread_ts, {
                    "tool_name": result.tool_name,
                    "arguments": result.arguments,
                    "user": user_id,
                })
                return ApprovalRequired(
                    tool_name=result.tool_name,
                    arguments=result.arguments,
                    user_id=user_id,
                )

            reply_text = result.output or "Done."
            assistant_content = result.llm_response if result.llm_response else reply_text
            new_messages = session["messages"] + [
                {"role": "user", "content": text},
                {"role": "assistant", "content": assistant_content},
            ]
            self.session_store.update_messages(thread_ts, new_messages)
            return MessageReply(
                text=reply_text,
                llm_response=result.llm_response,
                user_message=text,
                assistant_content=assistant_content,
            )

        except Exception as e:
            print(f"[orchestrator] error: {e}")
            try:
                recent_context = self.storage.get_raw_notes(limit=10)
                if not recent_context:
                    recent_context = "No recent memory. The cave is empty."
                fallback_prompt = self.build_system_prompt(self.base_prompt, "", recent_context, compression_mode="FULL")
                fallback_result = self.router.route_message(
                    user_message=text, system_prompt=fallback_prompt,
                )
                return MessageReply(text=fallback_result.output)
            except Exception as fallback_err:
                print(f"[orchestrator] fallback also failed: {fallback_err}")
                return ErrorReply(text="Grug brain hurt. Something went wrong. Try again?")
        finally:
            self.router._request_state._schedule_channel = None
            self.router._request_state._schedule_user = None
            self.router._request_state._schedule_thread_ts = None

    def execute_approved_action(self, thread_ts, channel_id, approver_id):
        """Execute a pending HITL action after approval.

        Returns (tool_result, re_infer_event) where re_infer_event may be
        a MessageReply or None.
        """
        session = self.session_store.get_or_create(thread_ts, channel_id)
        pending = session["pending_hitl"]

        if not pending:
            return None, None

        if approver_id != pending["user"]:
            return "unauthorized", None

        result = self.registry.execute(pending["tool_name"], pending["arguments"], skip_hitl=True)
        self.session_store.set_pending_hitl(thread_ts, None)

        messages = session["messages"]
        messages.append({"role": "assistant", "content": f"[Tool executed: {pending['tool_name']}] {result.output}"})
        self.session_store.update_messages(thread_ts, messages)

        return result, pending

    def re_infer(self, thread_ts, channel_id):
        """Run a follow-up inference after an approved tool execution.

        Returns a MessageReply or None.
        """
        try:
            updated_session = self.session_store.get_or_create(thread_ts, channel_id)
            hist = updated_session["messages"][-self.config.memory.thread_history_limit:]

            rag_context = ""
            last_user_msg = next((m["content"] for m in reversed(hist) if m.get("role") == "user"), "")
            if last_user_msg:
                try:
                    rag_hits = self.vector_memory.query_memory(last_user_msg, limit=self.config.memory.rag_result_limit)
                    if rag_hits and not rag_hits[0].get("offline"):
                        rag_context = "\n".join(h["content"] for h in rag_hits)
                except Exception:
                    pass

            import os
            summaries_dir = os.path.join(self.config.storage.base_dir, "summaries")
            summaries = self.load_summary_files(summaries_dir, self.config.memory.summary_days_limit)
            capped_tail = self.storage.get_capped_tail(self.config.memory.capped_tail_lines)
            sys_prompt = self.build_system_prompt(self.base_prompt, summaries, capped_tail, rag_context=rag_context)

            follow_up = self.router.route_message(user_message="", system_prompt=sys_prompt, message_history=hist)
            if follow_up.output and not follow_up.requires_approval:
                messages_now = updated_session["messages"]
                assistant_content = follow_up.llm_response if follow_up.llm_response else follow_up.output
                messages_now.append({"role": "assistant", "content": assistant_content})
                self.session_store.update_messages(thread_ts, messages_now)
                return MessageReply(text=follow_up.output, llm_response=follow_up.llm_response)
        except Exception as e:
            print(f"[re-infer] error: {e}")
        return None
