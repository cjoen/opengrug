"""Orchestrator — Dispatcher-driven task engine.

Owns the TaskQueue, classifies inbound messages via the Dispatcher, and
executes tasks against AgentContainers. External API (`enqueue`, `start`,
`process_message`, `execute_approved_action`, `re_infer`, `queue` property)
is stable so adapters and other tools don't need to change.
"""

import threading
from dataclasses import dataclass

from core.task import Task, TaskPriority, TaskState
from core.task_queue import TaskQueue


# ---------------------------------------------------------------------------
# Event dataclasses — pure state representations, no UI logic
# ---------------------------------------------------------------------------

@dataclass
class MessageReply:
    text: str
    user_message: str = ""
    assistant_content: str = ""


@dataclass
class ApprovalRequired:
    tool_name: str
    arguments: dict
    user_id: str


@dataclass
class ErrorReply:
    text: str


class Orchestrator:
    """Stateless message processor using TaskQueue + Dispatcher."""

    def __init__(self, router, registry, session_store, storage, summarizer,
                 vector_memory, config, build_system_prompt,
                 find_turn_boundary, auto_offload_pruned_turns, base_prompt,
                 worker_count=1, agents=None, dispatcher=None,
                 background_runnable=None):
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
        self.agents = agents
        self.dispatcher = dispatcher
        self._queue = TaskQueue(
            process_fn=self._run_batch,
            worker_count=worker_count,
            background_runnable=background_runnable,
        )

    @property
    def queue(self):
        return self._queue

    def start(self):
        self._queue.start()

    # ------------------------------------------------------------------
    # Ingress: classify → enqueue
    # ------------------------------------------------------------------

    def enqueue(self, session_id, text, user_id, metadata=None,
                on_result=None, priority=TaskPriority.URGENT):
        """Classify the message and enqueue it as a Task."""
        metadata = metadata or {}
        agent_name, context, plan = self._classify(session_id, text)
        task = Task(
            session_id=session_id,
            user_id=user_id,
            agent_name=agent_name,
            context=context,
            priority=priority,
            plan=plan,
            metadata={"raw_text": text, **metadata},
            on_result=on_result,
        )
        self._queue.enqueue(task)
        return task

    def _classify(self, session_id, text):
        """Run the Dispatcher; safe-fallback to chat_agent on any error."""
        if self.dispatcher is None or not self.agents:
            return "chat_agent", text, None
        try:
            session = self.session_store.get_or_create(session_id, "")
            history = session["messages"][-self.config.memory.thread_history_limit:]
            decision = self.dispatcher.classify(
                user_message=text,
                history=history,
                available_agents=list(self.agents.keys()),
            )
            return decision.agent, decision.context or text, decision.plan
        except Exception as e:
            print(f"[orchestrator] dispatcher error, defaulting to chat_agent: {e}")
            return "chat_agent", text, None

    # ------------------------------------------------------------------
    # Task execution (queue worker callback)
    # ------------------------------------------------------------------

    def _run_batch(self, batch):
        for task in batch:
            self._run_task(task)

    def _run_task(self, task: Task):
        try:
            task.transition(TaskState.RUNNING)
        except Exception:
            return  # already cancelled or terminal

        result_event = None
        try:
            if task.cancel_event.is_set():
                task.transition(TaskState.CANCELLED)
                result_event = ErrorReply(text="Task cancelled.")
                return

            container = (self.agents or {}).get(task.agent_name)
            if container is None:
                # Fall back to a direct chat_agent path with no scoping.
                result_event = self._run_chat_legacy(task)
            elif task.agent_name == "chat_agent":
                result_event = self._run_chat_agent(task, container)
            else:
                result_event = self._run_expert_agent(task, container)

            task.transition(TaskState.COMPLETED)
        except Exception as e:
            print(f"[orchestrator] task {task.id[:8]} failed: {e}")
            try:
                task.transition(TaskState.FAILED)
            except Exception:
                pass
            result_event = ErrorReply(text="Grug brain hurt. Something went wrong. Try again?")
        finally:
            if task.on_result:
                try:
                    task.on_result(result_event)
                except Exception as cb_err:
                    print(f"[orchestrator] on_result callback error: {cb_err}")

    def _run_chat_agent(self, task: Task, container):
        """Conversational path: full history, scoped registry, dispatch context."""
        text = task.metadata.get("raw_text", task.context)
        return self._execute_with_session(task, container, text)

    def _run_chat_legacy(self, task: Task):
        """Compatibility path when no agents are configured."""
        text = task.metadata.get("raw_text", task.context)
        return self._execute_with_session(task, None, text)

    def _execute_with_session(self, task: Task, container, text):
        # Inject schedule + dispatch context for tool closures (threadlocal on router).
        rs = self.router._request_state
        rs._schedule_channel = task.metadata.get("channel_id")
        rs._schedule_user = task.user_id
        rs._schedule_thread_ts = task.session_id
        rs._dispatch_session_id = task.session_id
        rs._dispatch_user_id = task.user_id
        rs._dispatch_on_result = task.on_result

        try:
            session = self.session_store.get_or_create(task.session_id, task.metadata.get("channel_id", ""))
            history = session["messages"][-self.config.memory.thread_history_limit:]

            base = container.base_prompt if container else self.base_prompt
            system_prompt = self._build_context(base, text)
            messages = history + [{"role": "user", "content": text}]
            messages = self._prune_turns(system_prompt, messages)

            result = self.router.route_message(
                user_message=text,
                system_prompt=system_prompt,
                message_history=messages,
                agent_container=container,
                cancel_event=task.cancel_event,
            )

            if result.requires_approval:
                early = session["messages"] + [{"role": "user", "content": text}]
                self.session_store.update_messages(task.session_id, early)
                self.session_store.set_pending_hitl(task.session_id, {
                    "tool_name": result.tool_name,
                    "arguments": result.arguments,
                    "user": task.user_id,
                })
                return ApprovalRequired(
                    tool_name=result.tool_name,
                    arguments=result.arguments,
                    user_id=task.user_id,
                )

            reply_text = result.output or "Grug did the thing, but got nothing back to show."
            new_msgs = session["messages"] + [{"role": "user", "content": text}]
            if result.tool_output:
                new_msgs.append({"role": "tool", "content": result.tool_output})
            new_msgs.append({"role": "assistant", "content": reply_text})
            self.session_store.update_messages(task.session_id, new_msgs)

            return MessageReply(text=reply_text, user_message=text, assistant_content=reply_text)
        finally:
            rs._schedule_channel = None
            rs._schedule_user = None
            rs._schedule_thread_ts = None
            rs._dispatch_session_id = None
            rs._dispatch_user_id = None
            rs._dispatch_on_result = None

    def _run_expert_agent(self, task: Task, container):
        """Clean-Slate path: distilled context + plan, no chat history."""
        rs = self.router._request_state
        rs._schedule_channel = task.metadata.get("channel_id")
        rs._schedule_user = task.user_id
        rs._schedule_thread_ts = task.session_id
        rs._dispatch_session_id = task.session_id
        rs._dispatch_user_id = task.user_id
        rs._dispatch_on_result = task.on_result

        try:
            framing = task.context or ""
            if task.plan:
                plan_lines = "\n".join(f"{i+1}. {step}" for i, step in enumerate(task.plan))
                framing = f"{framing}\n\nTo-Do List:\n{plan_lines}".strip()

            messages = [{"role": "user", "content": framing}]
            result = self.router.route_message(
                user_message=framing,
                system_prompt=container.base_prompt,
                message_history=messages,
                agent_container=container,
                cancel_event=task.cancel_event,
                max_steps=getattr(self.config.memory, "expert_max_steps", 5),
            )

            if result.output and task.session_id:
                # Append result into session history so the chat_agent sees it next turn.
                try:
                    session = self.session_store.get_or_create(task.session_id, task.metadata.get("channel_id", ""))
                    msgs = session["messages"] + [
                        {"role": "assistant", "content": f"[{task.agent_name}] {result.output}"},
                    ]
                    self.session_store.update_messages(task.session_id, msgs)
                except Exception:
                    pass

            return MessageReply(text=result.output or f"[{task.agent_name}] (no output)")
        finally:
            rs._schedule_channel = None
            rs._schedule_user = None
            rs._schedule_thread_ts = None
            rs._dispatch_session_id = None
            rs._dispatch_user_id = None
            rs._dispatch_on_result = None

    # ------------------------------------------------------------------
    # Synchronous chat_agent path (used by tests & background loops)
    # ------------------------------------------------------------------

    def process_message(self, text, session_id, user_id, metadata=None):
        """Synchronously run a message through chat_agent. Returns an event."""
        metadata = metadata or {}
        try:
            container = (self.agents or {}).get("chat_agent")
            task = Task(
                session_id=session_id,
                user_id=user_id,
                agent_name="chat_agent",
                context=text,
                metadata={"raw_text": text, **metadata},
            )
            return self._execute_with_session(task, container, text)
        except Exception as e:
            print(f"[orchestrator] error: {e}")
            return ErrorReply(text="Grug brain hurt. Something went wrong. Try again?")

    # ------------------------------------------------------------------
    # Context helpers (used by chat-agent path & re_infer)
    # ------------------------------------------------------------------

    def _build_context(self, base_prompt, text):
        capped_tail = self.storage.get_capped_tail(self.config.memory.capped_tail_lines)
        rag_context = ""
        try:
            rag_hits = self.vector_memory.query_memory_raw(text, limit=self.config.memory.rag_result_limit)
            if rag_hits and not rag_hits[0].get("offline"):
                rag_context = "\n".join(h["content"] for h in rag_hits)
        except Exception as e:
            print(f"[rag] pre-flight search failed: {e}")
        instructions_block = self.storage.get_instructions_block()
        return self.build_system_prompt(base_prompt, capped_tail, rag_context=rag_context, instructions_block=instructions_block)

    def _prune_turns(self, system_prompt, messages):
        estimated_tokens = len(str(system_prompt) + str(messages)) // 4
        dispatcher_tier = getattr(self.config.dispatcher, "worker_tier", "local-fast")
        worker_cfg = getattr(self.config.workers, dispatcher_tier, None)
        target_tokens = getattr(worker_cfg, "target_context_tokens", 2048) if worker_cfg else 2048
        while estimated_tokens > target_tokens and len(messages) > 1:
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

    # ------------------------------------------------------------------
    # HITL paths
    # ------------------------------------------------------------------

    def execute_approved_action(self, session_id, approver_id):
        pending = self.session_store.claim_pending_hitl(session_id)
        if not pending:
            return None, None
        if approver_id != pending["user"]:
            self.session_store.set_pending_hitl(session_id, pending)
            return "unauthorized", None
        result = self.registry.execute(pending["tool_name"], pending["arguments"], skip_hitl=True)
        session = self.session_store.get_or_create(session_id, "")
        msgs = session["messages"]
        msgs.append({"role": "assistant", "content": f"[Tool executed: {pending['tool_name']}] {result.output}"})
        self.session_store.update_messages(session_id, msgs)
        return result, pending

    def re_infer(self, session_id):
        try:
            updated = self.session_store.get_or_create(session_id, "")
            hist = updated["messages"][-self.config.memory.thread_history_limit:]
            last_user = next((m["content"] for m in reversed(hist) if m.get("role") == "user"), "")
            container = (self.agents or {}).get("chat_agent")
            base = container.base_prompt if container else self.base_prompt
            sys_prompt = self._build_context(base, last_user)
            follow_up = self.router.route_message(
                user_message="", system_prompt=sys_prompt, message_history=hist,
                agent_container=container,
            )
            if follow_up.output and not follow_up.requires_approval:
                msgs = updated["messages"]
                if follow_up.tool_output:
                    msgs.append({"role": "tool", "content": follow_up.tool_output})
                msgs.append({"role": "assistant", "content": follow_up.output})
                self.session_store.update_messages(session_id, msgs)
                return MessageReply(text=follow_up.output)
        except Exception as e:
            print(f"[re-infer] error: {e}")
        return None
