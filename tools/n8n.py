"""n8n tool hub integration for Grug.

Discovers active n8n workflows tagged 'grug-tool', parses a 'grug' JSON
block from each workflow's description, and registers a webhook-calling
Python tool per workflow. Tracks owned names so reload() cleanly swaps
the registry without touching builtins.
"""

import json
import logging
from functools import partial

import requests

from core.registry import ToolRegistry

logger = logging.getLogger(__name__)


def _call_webhook(base_url: str, webhook_path: str, timeout: int, **kwargs) -> str:
    """POST tool arguments to an n8n webhook and return the response as a string."""
    url = f"{base_url}/webhook/{webhook_path}"
    try:
        resp = requests.post(url, json=kwargs, timeout=timeout)
        if not resp.ok:
            return f"n8n error {resp.status_code}: {resp.text[:200]}"
        return resp.text
    except requests.exceptions.Timeout:
        return f"n8n tool unavailable: request timed out after {timeout}s"
    except requests.exceptions.RequestException as e:
        return f"n8n tool unavailable: {e}"


class N8nToolLoader:
    """Discovers active n8n workflows and registers them as Grug tools."""

    def __init__(self, registry: ToolRegistry, n8n_cfg):
        self._registry = registry
        self._base_url = n8n_cfg.base_url.rstrip("/")
        self._api_key = n8n_cfg.api_key
        self._tag = getattr(n8n_cfg, "tag", "grug-tool")
        self._timeout = getattr(n8n_cfg, "timeout_seconds", 10)
        self._owned_names: set = set()

    def _fetch_workflows(self) -> list:
        """Fetch active workflows with the grug tag from n8n REST API.

        Returns a list of workflow dicts. Returns [] on any error so callers
        never see an exception from discovery.
        """
        url = f"{self._base_url}/api/v1/workflows"
        params = {"active": "true", "tags": self._tag}
        headers = {"X-N8N-API-KEY": self._api_key}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
            # n8n may return {"data": [...]} or a bare list
            if isinstance(data, dict):
                return data.get("data", [])
            if isinstance(data, list):
                return data
            return []
        except Exception as e:
            logger.warning(f"[n8n] Failed to fetch workflows: {e}")
            return []

    def load(self) -> int:
        """Discover active tagged workflows and register them as tools.

        Returns the count of tools successfully registered.
        """
        workflows = self._fetch_workflows()
        registered = 0

        for wf in workflows:
            description_raw = wf.get("description") or ""
            if not description_raw:
                continue

            try:
                parsed = json.loads(description_raw)
                grug = parsed.get("grug")
            except (json.JSONDecodeError, AttributeError):
                wf_name = wf.get("name", "<unnamed>")
                logger.warning(f"[n8n] Workflow '{wf_name}': description is not valid JSON — skipping")
                continue

            if not grug:
                continue

            name = grug.get("name")
            webhook_path = grug.get("webhook_path")
            description = grug.get("description")

            if not all([name, webhook_path, description]):
                logger.warning(f"[n8n] Workflow missing required grug fields (name/webhook_path/description) — skipping")
                continue

            if name in self._owned_names:
                logger.warning(f"[n8n] Duplicate tool name '{name}' across n8n workflows — skipping second")
                continue

            if name in self._registry._python_tools or name in self._registry._cli_tools:
                logger.warning(f"[n8n] Tool name '{name}' conflicts with existing builtin — skipping n8n tool, preserving builtin")
                continue

            parameters = grug.get("parameters", {})
            required = grug.get("required", [])
            read_only = grug.get("read_only", True)
            category = grug.get("category", "N8N")
            response_rules = grug.get("response_rules")

            schema = {
                "description": description,
                "type": "object",
                "properties": parameters,
            }
            if required:
                schema["required"] = required

            self._registry.register_python_tool(
                name=name,
                schema=schema,
                func=partial(_call_webhook, self._base_url, webhook_path, self._timeout),
                destructive=not read_only,
                category=category,
                friendly_name=name,
                response_rules=response_rules,
            )
            self._owned_names.add(name)
            registered += 1
            logger.info(f"[n8n] Registered tool '{name}' (read_only={read_only}, category={category})")

        logger.info(f"[n8n] Loaded {registered} tool(s) from n8n")
        return registered

    def reload(self, **_kwargs) -> str:
        """Remove owned tools, re-discover, return a Slack-ready summary string."""
        old_count = len(self._owned_names)
        for name in list(self._owned_names):
            self._registry.remove_tool(name)
        self._owned_names.clear()

        try:
            count = self.load()
            return f"Reloaded n8n tools: {count} registered (was {old_count})."
        except Exception as e:
            return f"n8n reload failed: {e}"
