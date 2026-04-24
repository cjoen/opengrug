"""Orchestrator — the core message-processing pipeline.

Owns session management, context assembly, turn pruning, and routing.
Returns platform-agnostic Event dataclasses so adapters can translate
them to Slack, CLI, or any other UI.
"""

import threading
from dataclasses import dataclass
from core.queue import GrugMessageQueue, QueuedMessage


# ---------------------------------------------------------------------------
# Event dataclasses — pure state representations, no UI logic
# ---------------------------------------------------------------------------

@dataclass
class MessageReply:
    """A normal text reply to send back to the user."""
    text: str
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
                 vector_memory, config, build_system_prompt,
                 find_turn_boundary, auto_offload_pruned_turns, base_prompt,
                 worker_count=1):
        self.router = router
        self.registry = registry
        self.session_store = session_store
        self.storage = storage
        self.summarizer = summarizer
        self.vector_memory = vector_memory
        self.config = config
        self.build_system_prompt = build_system_prompt
        self.find_turn_boundary = find_turn_boundary
        self.auto_offload_pruned_turns = auto_offload_pruned_turns
        self.base_prompt = base_prompt
        self._queue = GrugMessageQueue(
            process_fn=self._process_queued,
            worker_count=worker_count,
        )

    @property
    def queue(self):
        """Public access to the message queue (for health tools, stats)."""
        return self._queue

    def start(self):
        """Start the internal message queue workers."""
        self._queue.start()

    def enqueue(self, session_id, text, user_id, metadata=None, on_result=None):
        """Non-blocking: add a message to the processing queue."""
        self._queue.enqueue(QueuedMessage(
            session_id=session_id,
            text=text,
            user_id=user_id,
            metadata=metadata or {},
            on_result=on_result,
        ))

    def _process_queued(self, msg):
        """Internal callback for queue workers."""
        return self.process_message(msg.text, msg.session_id, msg.user_id, msg.metadata)

    def _build_context(self, text, history):
        """Assemble system prompt with capped tail and RAG."""
        capped_tail = self.storage.get_capped_tail(self.config.memory.capped_tail_lines)

        rag_context = ""
        try:
            rag_hits = self.vector_memory.query_memory_raw(text, limit=self.config.memory.rag_result_limit)
            if rag_hits and not rag_hits[0].get("offline"):
                rag_context = "\n".join(h["content"] for h in rag_hits)
        except Exception as e:
            print(f"[rag] pre-flight search failed: {e}")

        instructions_block = self.storage.get_instructions_block()
        return self.build_system_prompt(self.base_prompt, capped_tail, rag_context=rag_context, instructions_block=instructions_block)

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

    def process_message(self, text, session_id, user_id, metadata=None):
        """Process a message and return an Event (MessageReply, ApprovalRequired, or ErrorReply).

        This is the main entry point. Platform adapters call this and translate
        the returned event into UI-specific actions.
        """
        metadata = metadata or {}
        try:
            # Inject schedule context for scheduler tools
            self.router._request_state._schedule_channel = metadata.get("channel_id")
            self.router._request_state._schedule_user = user_id
            self.router._request_state._schedule_thread_ts = session_id

            session = self.session_store.get_or_create(session_id, metadata.get("channel_id", ""))
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
                # Bug 1 Fix: Save user message before returning ApprovalRequired
                early_messages = session["messages"] + [{"role": "user", "content": text}]
                self.session_store.update_messages(session_id, early_messages)

                self.session_store.set_pending_hitl(session_id, {
                    "tool_name": result.tool_name,
                    "arguments": result.arguments,
                    "user": user_id,
                })
                return ApprovalRequired(
                    tool_name=result.tool_name,
                    arguments=result.arguments,
                    user_id=user_id,
                )

            reply_text = result.output or "Grug did the thing, but got nothing back to show."

            # Bug 3 Fix: Handle proper 3-turn native storage
            new_messages = session["messages"] + [{"role": "user", "content": text}]
            if result.tool_output:
                new_messages.append({"role": "tool", "content": result.tool_output})
            new_messages.append({"role": "assistant", "content": reply_text})

            self.session_store.update_messages(session_id, new_messages)

            return MessageReply(
                text=reply_text,
                user_message=text,
                assistant_content=reply_text,
            )

        except Exception as e:
            print(f"[orchestrator] error: {e}")
            return ErrorReply(text="Grug brain hurt. Something went wrong. Try again?")
        finally:
            self.router._request_state._schedule_channel = None
            self.router._request_state._schedule_user = None
            self.router._request_state._schedule_thread_ts = None

    def execute_approved_action(self, session_id, approver_id):
        """Execute a pending HITL action after approval."""
        pending = self.session_store.claim_pending_hitl(session_id)

        if not pending:
            return None, None

        if approver_id != pending["user"]:
            # Put it back — wrong user tried to approve
            self.session_store.set_pending_hitl(session_id, pending)
            return "unauthorized", None

        result = self.registry.execute(pending["tool_name"], pending["arguments"], skip_hitl=True)

        session = self.session_store.get_or_create(session_id, "")
        messages = session["messages"]
        messages.append({"role": "assistant", "content": f"[Tool executed: {pending['tool_name']}] {result.output}"})
        self.session_store.update_messages(session_id, messages)

        return result, pending

    def re_infer(self, session_id):
        """Run a follow-up inference after an approved tool execution.

        Returns a MessageReply or None.
        """
        try:
            updated_session = self.session_store.get_or_create(session_id, "")
            hist = updated_session["messages"][-self.config.memory.thread_history_limit:]

            rag_context = ""
            last_user_msg = next((m["content"] for m in reversed(hist) if m.get("role") == "user"), "")
            if last_user_msg:
                try:
                    rag_hits = self.vector_memory.query_memory_raw(last_user_msg, limit=self.config.memory.rag_result_limit)
                    if rag_hits and not rag_hits[0].get("offline"):
                        rag_context = "\n".join(h["content"] for h in rag_hits)
                except Exception:
                    pass

            capped_tail = self.storage.get_capped_tail(self.config.memory.capped_tail_lines)
            instructions_block = self.storage.get_instructions_block()
            sys_prompt = self.build_system_prompt(self.base_prompt, capped_tail, rag_context=rag_context, instructions_block=instructions_block)

            follow_up = self.router.route_message(user_message="", system_prompt=sys_prompt, message_history=hist)
            if follow_up.output and not follow_up.requires_approval:
                messages_now = updated_session["messages"]
                if follow_up.tool_output:
                    messages_now.append({"role": "tool", "content": follow_up.tool_output})
                messages_now.append({"role": "assistant", "content": follow_up.output})
                self.session_store.update_messages(session_id, messages_now)
                return MessageReply(text=follow_up.output)
        except Exception as e:
            print(f"[re-infer] error: {e}")
        return None
