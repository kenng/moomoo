from __future__ import annotations

from pathlib import Path

from cursor_sdk import Agent, AgentOptions, CursorAgentError, LocalAgentOptions

from momo.config import ROOT_DIR, get_settings


class CursorAgentAdapterError(Exception):
    """Cursor SDK agent failure."""


def run_prompt(
    *,
    system: str,
    user: str,
    api_key: str | None = None,
    model: str | None = None,
    cwd: str | Path | None = None,
) -> str:
    """Run a one-shot Cursor agent and return the final assistant text.

    Built-in tools are disabled (`tools=[]`) so the model can only reply with text.
    """
    settings = get_settings()
    token = (api_key if api_key is not None else settings.cursor_api_key).strip()
    if not token:
        raise CursorAgentAdapterError("CURSOR_API_KEY is not set")

    used_model = model if model is not None else settings.cursor_model
    workdir = str(cwd if cwd is not None else ROOT_DIR)
    prompt = (
        f"{system.strip()}\n\n"
        f"{user.strip()}\n\n"
        "Respond with the structured analysis only. "
        "Do not call tools, edit files, or ask follow-up questions."
    )

    try:
        result = Agent.prompt(
            prompt,
            AgentOptions(
                api_key=token,
                model=used_model,
                local=LocalAgentOptions(cwd=workdir),
                tools=[],
            ),
        )
    except CursorAgentError as exc:
        raise CursorAgentAdapterError(str(exc)) from exc

    if result.status == "error":
        raise CursorAgentAdapterError(
            f"Cursor agent run failed (run_id={result.id}, agent_id={result.agent_id})"
        )

    text = str(result.result or "").strip()
    if not text:
        raise CursorAgentAdapterError("Cursor agent returned empty content")
    return text
