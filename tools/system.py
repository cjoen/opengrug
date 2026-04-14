"""System tools for Grug (clarification, reply, capabilities)."""


def ask_for_clarification(reason_for_confusion):
    return f"Grug confused! {reason_for_confusion}"


def reply_to_user(message):
    return message


def list_capabilities(registry):
    """List all registered tools (excluding internal ones)."""
    hidden_tools = {"ask_for_clarification", "list_capabilities", "reply_to_user"}
    lines = ["I can help you with the following things:"]
    for name, data in registry._python_tools.items():
        if name in hidden_tools:
            continue
        friendly = data[3]
        lines.append(f"• {friendly}")
    for name, data in registry._cli_tools.items():
        if name in hidden_tools:
            continue
        friendly = data[3]
        lines.append(f"• {friendly}")
    return "\n".join(lines)
