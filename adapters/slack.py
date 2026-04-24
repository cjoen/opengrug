"""Slack adapter — translates Orchestrator events into Slack API calls.

Registers event/action handlers on a Slack Bolt App instance. All Slack-specific
UI (Block Kit JSON, reactions, ephemeral messages) lives here.
"""

import json
import threading
from core.orchestrator import MessageReply, ApprovalRequired, ErrorReply


class SlackAdapter:
    """Thin adapter that wires Slack events to the Orchestrator."""

    def __init__(self, app, orchestrator, session_store):
        self.app = app
        self.orchestrator = orchestrator
        self.session_store = session_store
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

        # Add receipt reaction
        try:
            client.reactions_add(channel=channel_id, timestamp=ts, name="eyes")
        except Exception:
            pass

        def on_result(result_event):
            # Remove receipt reaction
            try:
                client.reactions_remove(channel=channel_id, timestamp=ts, name="eyes")
            except Exception:
                pass
            self._deliver(client, channel_id, thread_ts, result_event)

        self.orchestrator.enqueue(
            session_id=thread_ts,
            text=text,
            user_id=user_id,
            metadata={"channel_id": channel_id, "ts": ts, "platform": "slack"},
            on_result=on_result,
        )

    def handle_approve(self, ack, body, client):
        ack()
        thread_ts = body["actions"][0]["value"]
        channel = body["channel"]["id"]
        clicker = body["user"]["id"]

        result, pending = self.orchestrator.execute_approved_action(thread_ts, clicker)

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
            event = self.orchestrator.re_infer(thread_ts)
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
    # Delivery helper
    # ------------------------------------------------------------------

    def _deliver(self, client, channel_id, thread_ts, event):
        """Translate an orchestrator event into Slack API calls."""
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
                        {"type": "button", "text": {"type": "plain_text", "text": "Approve"}, "style": "primary", "action_id": "grug_approve", "value": thread_ts},
                        {"type": "button", "text": {"type": "plain_text", "text": "Deny"}, "style": "danger", "action_id": "grug_deny", "value": thread_ts},
                    ]
                }
            ]
            client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts,
                text=f"Grug wants to run {event.tool_name}. Approve?",
                blocks=blocks,
            )
        elif isinstance(event, (MessageReply, ErrorReply)):
            client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts,
                text=event.text,
            )
