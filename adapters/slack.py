"""Slack adapter — translates Orchestrator events into Slack API calls.

Registers event/action handlers on a Slack Bolt App instance. All Slack-specific
UI (Block Kit JSON, reactions, ephemeral messages) lives here.
"""

import json
import threading
from core.orchestrator import MessageReply, ApprovalRequired, ErrorReply
from core.queue import QueuedMessage


class SlackAdapter:
    """Thin adapter that wires Slack events to the Orchestrator."""

    def __init__(self, app, orchestrator, session_store, message_queue):
        self.app = app
        self.orchestrator = orchestrator
        self.session_store = session_store
        self.message_queue = message_queue
        self._register_handlers()

    def _register_handlers(self):
        self.app.event("message")(self.handle_message)
        self.app.action("grug_approve")(self.handle_approve)
        self.app.action("grug_deny")(self.handle_deny)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def handle_message(self, event, say, client):
        if event.get("subtype"):
            return
        text = event.get("text")
        if not text:
            return

        thread_ts = event.get("thread_ts", event["ts"])
        channel_id = event.get("channel")
        ts = event.get("ts")
        user_id = event.get("user")

        self.message_queue.enqueue(QueuedMessage(
            thread_ts=thread_ts,
            channel_id=channel_id,
            ts=ts,
            user_id=user_id,
            text=text,
            client=client,
        ))

    def handle_approve(self, ack, body, client):
        ack()
        thread_ts = body["actions"][0]["value"]
        channel = body["channel"]["id"]
        clicker = body["user"]["id"]

        result, pending = self.orchestrator.execute_approved_action(thread_ts, channel, clicker)

        if result is None:
            client.chat_postMessage(channel=channel, text="No pending action found (expired or already handled).")
            return

        if result == "unauthorized":
            client.chat_postEphemeral(channel=channel, user=clicker, text=":no_entry_sign: Only the person who requested this action can approve it.")
            return

        status_prefix = "" if result.success else ":x: "
        client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=f"{status_prefix}<@{clicker}> approved `{pending['tool_name']}`: {result.output}"
        )

        def _re_infer():
            event = self.orchestrator.re_infer(thread_ts, channel)
            if event:
                client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=event.text)

        threading.Thread(target=_re_infer, daemon=True).start()

    def handle_deny(self, ack, body, client):
        ack()
        thread_ts = body["actions"][0]["value"]
        channel = body["channel"]["id"]
        clicker = body["user"]["id"]

        session = self.session_store.get_or_create(thread_ts, channel)
        pending = session["pending_hitl"]

        if not pending:
            client.chat_postMessage(channel=channel, text="No pending action found (expired or already handled).")
            return

        if clicker != pending["user"]:
            client.chat_postEphemeral(channel=channel, user=clicker, text=":no_entry_sign: Only the person who requested this action can deny it.")
            return

        self.session_store.set_pending_hitl(thread_ts, None)
        client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=f":no_entry_sign: <@{clicker}> denied `{pending['tool_name']}`. Cancelled.")

    # ------------------------------------------------------------------
    # Queue worker callback
    # ------------------------------------------------------------------

    def process_queued_message(self, msg: QueuedMessage):
        """Called by GrugMessageQueue workers. Translates orchestrator events to Slack."""
        event = self.orchestrator.process_message(
            text=msg.text,
            thread_ts=msg.thread_ts,
            channel_id=msg.channel_id,
            user_id=msg.user_id,
        )

        if isinstance(event, ApprovalRequired):
            args_preview = json.dumps(event.arguments or {}, indent=2)
            blocks = [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f":warning: Grug wants to run *{event.tool_name}*\n```\n{args_preview}\n```"}
                },
                {
                    "type": "actions",
                    "elements": [
                        {"type": "button", "text": {"type": "plain_text", "text": "Approve"}, "style": "primary", "action_id": "grug_approve", "value": msg.thread_ts},
                        {"type": "button", "text": {"type": "plain_text", "text": "Deny"}, "style": "danger", "action_id": "grug_deny", "value": msg.thread_ts},
                    ]
                }
            ]
            msg.client.chat_postMessage(
                channel=msg.channel_id, thread_ts=msg.thread_ts,
                text=f"Grug wants to run {event.tool_name}. Approve?",
                blocks=blocks,
            )
        elif isinstance(event, (MessageReply, ErrorReply)):
            msg.client.chat_postMessage(
                channel=msg.channel_id, thread_ts=msg.thread_ts,
                text=event.text,
            )
