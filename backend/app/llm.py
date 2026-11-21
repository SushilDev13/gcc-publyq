"""Claude via the official Anthropic SDK: one streamed request per tool round.

`stream_round` yields ('text', str) as tokens arrive, then ('final', message).
The caller runs the tools and loops (manual loop: we need our own approval
gate and per-token streaming to voice and chat).
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import anthropic

from .config import settings

_client: anthropic.AsyncAnthropic | None = None
BETAS = ['server-side-fallback-2026-07-01']   # server-side refusal fallback ("default" routing)


class LLMError(RuntimeError):
    pass


def client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=settings.llm_timeout_s,
                                           max_retries=1)
    return _client


async def stream_round(system: list[dict], messages: list[dict], tools: list[dict],
                       max_tokens: int = 16000) -> AsyncIterator[tuple[str, object]]:
    if not settings.llm_enabled:
        raise LLMError('LLM not configured')
    try:
        async with client().beta.messages.stream(
            model=settings.llm_model, max_tokens=max_tokens, system=system, messages=messages, tools=tools,
            output_config={'effort': settings.llm_effort}, betas=BETAS, fallbacks='default',
        ) as stream:
            async for event in stream:
                if event.type == 'text':
                    yield 'text', event.text
            yield 'final', await stream.get_final_message()
    except ValueError as e:      # tool input JSON the SDK could not parse at all
        raise LLMError(f'unparseable tool input: {e}') from e
    except anthropic.AuthenticationError as e:
        raise LLMError('Anthropic API key rejected') from e
    except anthropic.RateLimitError as e:
        raise LLMError('Anthropic rate limit') from e
    except anthropic.APIConnectionError as e:
        raise LLMError('cannot reach Anthropic API') from e
    except anthropic.APIStatusError as e:
        raise LLMError(f'Anthropic API error {e.status_code}') from e
