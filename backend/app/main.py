"""FastAPI app: REST tools, agent turn (SSE), OpenAI-compatible endpoint."""
import logging
import time

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import db
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
