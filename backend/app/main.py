"""FastAPI app: REST tools, agent turn (SSE), OpenAI-compatible endpoint."""
import json
import logging
import time
import uuid

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from . import db
from . import roles as R
from . import brief, voice
from . import mcp as mcp_server
from .agent import run_turn
from . import tools as T
from .config import settings

logging.basicConfig(level=settings.log_level, format='%(asctime)s %(levelname)s %(name)s %(message)s')
log = logging.getLogger('gcc')

app = FastAPI(title='GCC Publyq', version='0.1.0')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])


@app.middleware('http')
async def timing(request: Request, call_next):
    t0 = time.perf_counter()
    resp = await call_next(request)
    ms = (time.perf_counter() - t0) * 1000
    resp.headers['Server-Timing'] = f'app;dur={ms:.1f}'
    log.info('%s %s %s %.1fms', request.method, request.url.path, resp.status_code, ms)
    return resp


@app.exception_handler(T.ToolError)
async def tool_error(_: Request, e: T.ToolError):
    return JSONResponse({'error': str(e)}, status_code=404 if 'No ' in str(e) else 400)


# ── health ────────────────────────────────────────────────────────────────
@app.get('/api/health')
def health():
    try:
        n = db.fetchone('SELECT count(*) n FROM complaints')['n']
        return {'ok': True, 'db': 'up', 'complaints': n, 'llm': settings.llm_enabled,
                'ward': settings.ward_no}
    except Exception as e:  # report, don't crash
        log.exception('health check failed')
        return JSONResponse({'ok': False, 'db': 'down', 'error': type(e).__name__}, status_code=503)


# ── facts ─────────────────────────────────────────────────────────────────
@app.get('/api/complaints')
def complaints(status_group: str = 'open', locality: str | None = None, issue_group: str | None = None,
               complaint_type: str | None = None, safety: bool | None = None, overdue: bool | None = None,
               q: str | None = None, holder: str | None = None, limit: int = Query(20, ge=1, le=50)):
    return T.search_complaints(status_group, locality, issue_group, complaint_type, safety, overdue, q, holder, limit)


@app.get('/api/complaints/{cid}')
def complaint(cid: str):
    return T.get_complaint(cid)


@app.get('/api/ward/{ward_no}/summary')
def ward(ward_no: int):
    return T.ward_summary(ward_no)


@app.get('/api/ward/{ward_no}/officials')
def officials(ward_no: int, role: str | None = None):
    return T.find_officials(role, ward_no)


@app.get('/api/officials/{post_id}')
def official(post_id: str):
    return T.get_official(post_id)


@app.get('/api/sops')
def sop(topic: str):
    return T.get_sop(topic)


class TriageIn(BaseModel):
    text: str = Field(min_length=3, max_length=2000)


@app.post('/api/triage')
def triage(body: TriageIn):
    return T.triage(body.text)


# ── workflow ledger ───────────────────────────────────────────────────────
class ActionIn(BaseModel):
    kind: str
    complaint_id: str
    target_post_id: str | None = None
    note: str | None = Field(None, max_length=2000)
    due_in_days: float | None = Field(None, ge=0, le=60)
    session_id: str | None = Field(None, max_length=80)


@app.get('/api/actions')
def actions(status: str | None = None, kind: str | None = None, complaint_id: str | None = None,
            due_only: bool = False):
    return T.list_actions(status, kind, complaint_id, due_only)


@app.post('/api/actions')
def propose(body: ActionIn):
    return T.propose_action(**body.model_dump())


@app.post('/api/actions/{aid}/{op}')
def transition(aid: int, op: str):
    fn = {'confirm': T.confirm_action, 'cancel': T.cancel_action, 'done': T.complete_action}.get(op)
    if not fn:
        raise HTTPException(404, 'op must be confirm, cancel or done')
    return fn(aid)


# ── roles + chat history ──────────────────────────────────────────────────
@app.get('/api/roles')
def roles():
    return {'roles': R.list_roles()}


@app.get('/api/conversations')
def conversations(device_id: str = Query(min_length=8, max_length=80), limit: int = Query(20, ge=1, le=100)):
    rows = db.fetchall("""SELECT c.id, c.role_id, c.title, c.created_at, c.updated_at,
                                (SELECT count(*) FROM turns t WHERE t.session_id = c.id) turns
                         FROM conversations c WHERE c.device_id = %s AND c.deleted_at IS NULL
                         ORDER BY c.updated_at DESC LIMIT %s""", (device_id, limit))
    return {'items': rows}


def _own_conversation(cid: str, device_id: str) -> dict:
    c = db.fetchone('SELECT * FROM conversations WHERE id = %s AND device_id = %s AND deleted_at IS NULL',
                    (cid, device_id))
    if not c:
        raise HTTPException(404, 'conversation not found')
    return c


@app.get('/api/conversations/{cid}')
def conversation(cid: str, device_id: str = Query(min_length=8, max_length=80)):
    c = _own_conversation(cid, device_id)
    turns = db.fetchall("""SELECT id, text, reply, route, intent, role_id, latency_ms, cards, created_at,
                                 ctx->>'lang' AS lang
                          FROM turns WHERE session_id = %s ORDER BY id""", (cid,))
    return {**c, 'turns': turns}


@app.delete('/api/conversations/{cid}')
def delete_conversation(cid: str, device_id: str = Query(min_length=8, max_length=80)):
    _own_conversation(cid, device_id)
    db.execute('UPDATE conversations SET deleted_at = now() WHERE id = %s', (cid,))
    return {'ok': True, 'note': 'Hidden from history. Actions it created stay in the action ledger.'}


# ── voice calls (Agora ConvoAI + Murf) ───────────────────────────────────
class VoiceStartIn(BaseModel):
    channel: str = Field(min_length=1, max_length=64)
    agent_uid: int = Field(gt=0)
    user_uid: int = Field(gt=0)
    session_id: str = Field(min_length=1, max_length=80)
    role_id: str | None = Field(None, max_length=20)
    device_id: str = Field(min_length=8, max_length=80)
    voice: str = Field('anisha', max_length=20)
    lang: str = Field('en', max_length=4)            # the language the agent speaks; it hears all three
    greet_turn_id: int | None = Field(None, gt=0)    # restart to switch language: open with this turn's answer


class VoiceStopIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=128)


@app.get('/api/voice/status')
def voice_status():
    return {**voice.configured(), 'voices': [{'key': k, **v} for k, v in voice.VOICES.items()],
            'default_voice': voice.DEFAULT_VOICE, 'llm': settings.llm_enabled,
            'languages': {k: {'code': v['code'], 'voices': list(v['voices'])} for k, v in voice.LANGS.items()},
            'telegram': bool(settings.bot_token and settings.group_chat_id)}


@app.get('/api/communications')
def communications(complaint_id: str | None = None):
    return T.list_communications(complaint_id)


@app.get('/api/voice/config')
def voice_config(session_id: str = Query(min_length=1, max_length=80)):
    try:
        return voice.connection_config(session_id)
    except voice.VoiceError as e:
        raise HTTPException(503, str(e))


@app.post('/api/voice/start')
async def voice_start(body: VoiceStartIn):
    # register the conversation up front so the call shows in history even before the first turn
    db.execute("""INSERT INTO conversations (id, device_id, role_id, title) VALUES (%s,%s,%s,%s)
                  ON CONFLICT (id) DO UPDATE SET updated_at = now()""",
               (body.session_id, body.device_id, body.role_id, 'Voice call'))
    hello = None
    if body.greet_turn_id:
        t = db.fetchone('SELECT reply FROM turns WHERE id = %s AND session_id = %s', (body.greet_turn_id, body.session_id))
        if not t:
            raise HTTPException(404, 'turn not found in this conversation')
        hello = (t['reply'] or '').strip() or None
    try:
        return await voice.start(body.channel, body.agent_uid, body.user_uid, body.session_id, body.role_id,
                                 body.voice, body.lang, hello)
    except voice.VoiceError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        log.exception('voice start failed')
        raise HTTPException(502, f'Agora agent failed to start: {type(e).__name__}')


@app.post('/api/voice/stop')
async def voice_stop(body: VoiceStopIn):
    try:
        await voice.stop(body.agent_id)
    except Exception as e:
        log.warning('voice stop failed: %s', e)
        return {'ok': False, 'error': type(e).__name__}
    return {'ok': True}


# ── MCP (Streamable HTTP, stateless, JSON responses) ─────────────────────
@app.post('/mcp')
async def mcp_endpoint(request: Request):
    if not settings.mcp_token:
        raise HTTPException(503, 'MCP is not enabled on this server')
    if request.headers.get('authorization', '') != f'Bearer {settings.mcp_token}':
        return JSONResponse({'error': 'unauthorized'}, status_code=401,
                            headers={'WWW-Authenticate': 'Bearer realm="gcc-publyq-mcp"'})
    try:
        msg = await request.json()
    except ValueError:
        return JSONResponse(mcp_server._err(None, -32700, 'Parse error'), status_code=400)
    if isinstance(msg, list):
        return JSONResponse(mcp_server._err(None, -32600, 'Batching is not supported'), status_code=400)
    import asyncio
    out = await asyncio.to_thread(mcp_server.handle, msg)
    if out is None:                                   # notification or response: accepted, no body
        return Response(status_code=202)
    return JSONResponse(out)


@app.get('/mcp')
@app.delete('/mcp')
def mcp_no_stream():
    # stateless server: no server-initiated SSE stream and no sessions to terminate
    return Response(status_code=405, headers={'Allow': 'POST'})


# ── morning brief (Vercel Cron: GET with Authorization: Bearer $CRON_SECRET) ──
def _cron_auth(request: Request) -> None:
    if not settings.cron_secret or request.headers.get('authorization', '') != f'Bearer {settings.cron_secret}':
        raise HTTPException(401, 'unauthorized')


@app.get('/api/cron/morning-brief')
def morning_brief(request: Request, lang: str | None = None, dry_run: bool = False, force: bool = False):
    _cron_auth(request)
    if lang not in (None, 'ta', 'en'):
        raise HTTPException(400, 'lang must be ta or en')
    if dry_run:
        b = brief.build(lang or settings.brief_lang)
        return {'status': 'preview', 'lang': b['lang'], 'chars': b['chars'], 'html': b['html']}
    return brief.send_today(lang, force)


# ── reports ───────────────────────────────────────────────────────────────
@app.get('/api/reports/complaint/{cid}')
def report(cid: str):
    return T.complaint_report(cid)


@app.get('/api/reports/ward')
def ward_report():
    return T.ward_brief()


# ── media (dev only; prod uses MEDIA_BASE_URL) ────────────────────────────
@app.get('/media/{path:path}')
def media(path: str):
    base = settings.media_path.resolve()
    f = (base / path).resolve()
    if base not in f.parents or not f.is_file():
        raise HTTPException(404, 'not found')
    return FileResponse(f, headers={'Cache-Control': 'public, max-age=86400'})


# ── agent: web front door (SSE) ───────────────────────────────────────────
class TurnIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    session_id: str = Field(min_length=1, max_length=80)
    role_id: str | None = Field(None, max_length=20)
    device_id: str | None = Field(None, max_length=80)
    lang: str | None = Field(None, max_length=4)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@app.post('/api/agent/turn')
async def agent_turn(body: TurnIn):
    async def gen():
        try:
            async for ev, data in run_turn(body.text, body.session_id, 'web', body.role_id, body.device_id,
                                           body.lang):
                yield _sse(ev, data)
        except Exception:
            log.exception('turn failed')
            yield _sse('delta', {'text': 'Something went wrong on my side. Please try again.'})
            yield _sse('done', {'route': 'error'})
    return StreamingResponse(gen(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


# ── agent: Agora ConvoAI front door (OpenAI-compatible custom LLM) ────────
def _last_user_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get('role') == 'user':
            c = m.get('content')
            if isinstance(c, list):  # content parts
                c = ' '.join(p.get('text', '') for p in c if isinstance(p, dict))
            return (c or '').strip()
    return ''


@app.post('/v1/chat/completions')
@app.post('/chat/completions')
async def chat_completions(request: Request, session: str | None = None, role: str | None = None,
                           lang: str | None = None):
    if settings.custom_llm_token:
        if request.headers.get('authorization', '') != f'Bearer {settings.custom_llm_token}':
            raise HTTPException(401, 'bad token')
    body = await request.json()
    text = _last_user_text(body.get('messages') or [])
    sid = session or str(body.get('user') or 'convoai')
    cid = f'chatcmpl-{uuid.uuid4().hex[:12]}'
    model = 'gcc-publyq'

    def chunk(delta: dict, finish: str | None = None) -> str:
        return 'data: ' + json.dumps({'id': cid, 'object': 'chat.completion.chunk', 'created': int(time.time()),
                                      'model': model, 'choices': [{'index': 0, 'delta': delta,
                                                                   'finish_reason': finish}]}) + '\n\n'

    async def gen():
        yield chunk({'role': 'assistant'})
        try:
            if text:
                switching = False
                async for ev, data in run_turn(text, sid, 'convoai', role, lang=lang):
                    if ev == 'route' and lang in voice.LANGS and data.get('lang') not in (None, lang):
                        # This agent's voice can't speak the new language. Hold briefly; the browser restarts
                        # the agent in that language and it opens with this turn's answer.
                        switching = True
                        yield chunk({'content': voice.HOLD[lang]})
                    elif ev == 'delta' and not switching:
                        yield chunk({'content': data['text']})
        except Exception:
            log.exception('convoai turn failed')
            yield chunk({'content': 'Sorry, something went wrong. Please try again.'})
        yield chunk({}, 'stop')
        yield 'data: [DONE]\n\n'

    if body.get('stream', True):
        return StreamingResponse(gen(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache'})
    out = ''
    async for ev, data in run_turn(text, sid, 'convoai', role, lang=lang):
        if ev == 'delta':
            out += data['text']
    return {'id': cid, 'object': 'chat.completion', 'created': int(time.time()), 'model': model,
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': out}, 'finish_reason': 'stop'}]}


# ── dev: serve the web client from the same origin (Vercel serves web/ statically) ──
from fastapi.staticfiles import StaticFiles  # noqa: E402

from .config import ROOT  # noqa: E402

if (ROOT / 'web').is_dir():
    app.mount('/', StaticFiles(directory=ROOT / 'web', html=True), name='web')
