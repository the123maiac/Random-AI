from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from .. import db, usage
from . import tools

MAX_STEPS = 15


async def run_agent(
    *,
    user_id: int,
    api_key: str,
    base_url: str | None,
    model: str,
    messages: list[dict],
    allowed_tools: set[str] | None = None,
    max_steps: int = MAX_STEPS,
) -> dict:
    """Run an OpenAI-compat tool-calling loop. Returns {final_text, steps, status, run_id}."""

    schemas = tools.TOOL_SCHEMAS
    if allowed_tools is not None:
        schemas = [s for s in schemas if s["function"]["name"] in allowed_tools]

    client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)
    convo: list[dict[str, Any]] = list(messages)

    with db.connect() as c:
        cur = c.execute(
            "INSERT INTO agent_runs (user_id, kind, status) VALUES (?, 'chat', 'running')",
            (user_id,),
        )
        run_id = cur.lastrowid

    final = ""
    status = "ok"
    steps = 0

    for step in range(max_steps):
        ok, _, cap = usage.check(user_id, "llm_step")
        if not ok:
            status = "capped"
            final = f"⚠ Daily LLM step cap reached ({cap}/day) or kill switch on. Stopping."
            break
        usage.increment(user_id, "llm_step")

        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=convo,
                tools=schemas if schemas else None,
                tool_choice="auto" if schemas else None,
            )
        except Exception as e:
            status = "failed"
            final = f"⚠ Provider error: {e}"
            break

        msg = resp.choices[0].message
        steps += 1
        convo.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in (msg.tool_calls or [])
            ] or None,
        })

        if not msg.tool_calls:
            final = msg.content or ""
            break

        for tc in msg.tool_calls:
            result = await tools.dispatch(
                user_id, tc.function.name, tc.function.arguments, allowed_tools
            )
            convo.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })
    else:
        status = "ok" if final else "capped"
        if not final:
            final = "⚠ Hit max steps without final answer."

    with db.connect() as c:
        c.execute(
            """UPDATE agent_runs SET status = ?, steps = ?, ended_at = CURRENT_TIMESTAMP, output = ?
               WHERE id = ?""",
            (status, steps, final[:8000], run_id),
        )

    return {"final_text": final, "steps": steps, "status": status, "run_id": run_id}
