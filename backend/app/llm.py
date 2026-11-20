"""Minimal streaming client for any OpenAI-compatible chat-completions API.

One function, no SDK: yields ('content', str) deltas as they arrive and a
final ('tool_calls', [...]) if the model asked for tools. Swapping provider
is LLM_BASE_URL / LLM_MODEL / LLM_API_KEY only.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from .config import settings

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(settings.llm_timeout_s, connect=5.0))
    return _client


class LLMError(RuntimeError):
    pass


async def stream_chat(messages: list[dict], tools: list[dict] | None = None,
                      temperature: float = 0.2, max_tokens: int = 400) -> AsyncIterator[tuple[str, object]]:
    if not settings.llm_enabled:
        raise LLMError('LLM not configured')
    body = {'model': settings.llm_model, 'messages': messages, 'stream': True,
            'temperature': temperature, 'max_tokens': max_tokens}
    if tools:
        body['tools'] = tools
        body['tool_choice'] = 'auto'
    url = settings.llm_base_url.rstrip('/') + '/chat/completions'
    headers = {'Authorization': f'Bearer {settings.llm_api_key}', 'Content-Type': 'application/json'}
    calls: dict[int, dict] = {}
    try:
        async with client().stream('POST', url, json=body, headers=headers) as r:
            if r.status_code >= 400:
                detail = (await r.aread()).decode(errors='replace')[:300]
                raise LLMError(f'LLM HTTP {r.status_code}: {detail}')
            async for line in r.aiter_lines():
                if not line.startswith('data:'):
                    continue
                data = line[5:].strip()
                if data == '[DONE]':
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                for ch in chunk.get('choices') or []:
                    d = ch.get('delta') or {}
                    if d.get('content'):
                        yield 'content', d['content']
                    for tc in d.get('tool_calls') or []:
                        slot = calls.setdefault(tc.get('index', 0), {'id': '', 'name': '', 'arguments': ''})
                        slot['id'] = tc.get('id') or slot['id']
                        fn = tc.get('function') or {}
                        slot['name'] += fn.get('name') or ''
                        slot['arguments'] += fn.get('arguments') or ''
    except httpx.HTTPError as e:
        raise LLMError(f'LLM transport error: {type(e).__name__}') from e
    if calls:
        yield 'tool_calls', [calls[k] for k in sorted(calls)]
