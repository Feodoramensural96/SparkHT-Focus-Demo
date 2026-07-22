from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from .models import FocusSessionCreate
from .service import FocusService


def create_app(service: FocusService) -> FastAPI:
    app = FastAPI(title="SparkHT Focus Orchestrator", version="0.1.0")
    app.state.focus_service = service

    @app.post("/api/focus/sessions")
    async def create_session(request: FocusSessionCreate, response: Response) -> dict:
        session, reused = await service.create_session(request)
        response.status_code = 200 if reused else 201
        return {
            "session_id": session.session_id,
            "state": session.state.value,
            "capture_interval_seconds": (
                service.demo_capture_interval
                if session.mode.value == "demo"
                else service.normal_capture_interval
            ),
            "batch_size": service.batch_builder.batch_size,
            "reused_existing_session": reused,
        }

    @app.post("/api/focus/sessions/{session_id}/stop")
    async def stop_session(session_id: str) -> dict:
        try:
            session = await service.stop_session(session_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="session not found") from None
        return session.model_dump(mode="json")

    @app.post("/api/focus/sessions/{session_id}/cancel")
    async def cancel_session(session_id: str) -> dict:
        try:
            session = await service.cancel_session(session_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="session not found") from None
        return session.model_dump(mode="json")

    @app.get("/api/focus/active")
    async def get_active_session() -> dict:
        session = service.active_session
        if session is None:
            raise HTTPException(status_code=404, detail="no active session")
        return session.model_dump(mode="json")

    @app.get("/api/focus/sessions/{session_id}")
    async def get_session(session_id: str) -> dict:
        try:
            return service.get_session(session_id).model_dump(mode="json")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="session not found") from None

    @app.get("/api/focus/sessions/{session_id}/report")
    async def get_report(session_id: str) -> dict:
        try:
            return service.get_report(session_id).model_dump(mode="json")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="report not found") from None

    @app.get("/api/focus/sessions/{session_id}/events")
    async def events(
        session_id: str,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        async def stream():
            async for event in service.events.subscribe(session_id, last_event_id):
                if event is None:
                    yield ": keepalive\n\n"
                    continue
                payload = event.model_dump_json()
                yield f"id: {event.event_id}\nevent: {event.type}\ndata: {payload}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/focus/sessions/{session_id}/frames/latest")
    async def latest_frame(session_id: str) -> FileResponse:
        frames = sorted(service.store.frame_dir(session_id).glob("*.jpg"))
        if not frames:
            raise HTTPException(status_code=404, detail="frame not found")
        return FileResponse(frames[-1], media_type="image/jpeg")

    @app.get("/health")
    async def health() -> dict:
        return (await service.health()).model_dump(mode="json")

    @app.get("/", response_class=HTMLResponse)
    async def dashboard() -> str:
        return _DASHBOARD_HTML

    return app


_DASHBOARD_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>看见专注 · SparkHT</title><style>
:root{color-scheme:dark;font-family:Inter,"Noto Sans SC",sans-serif;background:#080d18;color:#e7eefc}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 15% 0,#152743 0,#080d18 43%);min-height:100vh}
header{padding:26px 34px;border-bottom:1px solid #20324c;display:flex;justify-content:space-between;align-items:center}
h1{font-size:24px;margin:0}.sub{color:#90a5c7;margin-top:7px}.model{padding:10px 14px;border:1px solid #2e69ad;border-radius:10px;background:#0d2038;color:#72b8ff}
main{display:grid;grid-template-columns:1.15fr .85fr;gap:18px;padding:22px}.card{border:1px solid #20324c;background:#0d1626dd;border-radius:16px;padding:18px;box-shadow:0 18px 50px #0005}
.frame{aspect-ratio:4/3;background:#050912;border-radius:12px;display:grid;place-items:center;overflow:hidden}.frame img{width:100%;height:100%;object-fit:contain}
.metrics{display:grid;grid-template-columns:1fr 1fr;gap:12px}.metric{padding:18px;background:#111f33;border-radius:12px}.metric b{display:block;font-size:30px;color:#60aaff;margin-top:7px}
.status{display:flex;gap:8px;align-items:center}.dot{width:9px;height:9px;border-radius:50%;background:#eab308}.timeline{max-height:330px;overflow:auto;font-family:ui-monospace,monospace;font-size:12px;color:#a9b8d0}.event{padding:9px;border-bottom:1px solid #1d2b40}
footer{padding:0 34px 24px;color:#647a9d;font-size:12px}@media(max-width:850px){main{grid-template-columns:1fr}}
</style></head><body><header><div><h1>看见专注</h1><div class="sub">端侧快慢双系统机器人 · DGX Spark</div></div><div class="model">Step3-VL-10B-FP8</div></header>
<main><section class="card"><h2>最新画面</h2><div class="frame"><img id="frame" alt="等待机器人画面"><span id="empty">等待会话启动</span></div><p id="frameMeta" class="sub">640×480 · 本地处理，不上传云端</p></section>
<section class="card"><h2>核心指标</h2><div class="metrics"><div class="metric">在位率<b id="presence">—</b></div><div class="metric">手机可见率<b id="phone">—</b></div><div class="metric">疑似杯子移动<b id="drink">—</b></div><div class="metric">专注趋势指数<b id="score">—</b></div></div></section>
<section class="card"><h2>时间线</h2><div id="timeline" class="timeline"><div class="event">等待 SSE 事件…</div></div></section>
<section class="card"><h2>技术状态</h2><p class="status"><span id="healthDot" class="dot"></span><span id="health">正在检查本地服务</span></p><p id="counts">抓拍 — · 已分析 — · 失败 — · 丢弃 —</p><p id="latency">最近 Step3 批次：—</p><p>视觉推理单并发 · 语音活动时暂停/取消慢任务</p><p>只观察人物、明显手机与杯子变化；不做 OCR、身份或情绪识别。</p></section></main><footer>所有指标均为低分辨率视觉代理统计，仅供参考。</footer>
<script>
const qs=new URLSearchParams(location.search);let sid=qs.get('session'),eventSource;
const pct=v=>v==null?'—':Math.round(v*100)+'%';
const ui={presence:document.querySelector('#presence'),phone:document.querySelector('#phone'),drink:document.querySelector('#drink'),score:document.querySelector('#score'),frame:document.querySelector('#frame')};
function renderStats(d){ui.presence.textContent=pct(d.presence_ratio);ui.phone.textContent=pct(d.phone_visible_ratio);ui.drink.textContent=d.suspected_drink_events??'—';ui.score.textContent=d.focus_proxy_score==null?'—':Math.round(d.focus_proxy_score)}
function renderSession(s){renderStats(s.stats);document.querySelector('#counts').textContent=`抓拍 ${s.captured_frames} · 已分析 ${s.stats.analyzed_frames} · 失败 ${s.failed_frames} · 丢弃 ${s.dropped_batches}`}
async function health(){try{const r=await fetch('/health'),x=await r.json(),parts=Object.entries(x.components).map(([n,v])=>`${n}:${v.status}`);document.querySelector('#health').textContent=parts.join(' · ');document.querySelector('#healthDot').style.background=x.status==='healthy'?'#22c55e':x.status==='degraded'?'#eab308':'#ef4444'}catch{document.querySelector('#health').textContent='健康检查失败';document.querySelector('#healthDot').style.background='#ef4444'}}
async function hydrate(){try{const r=await fetch(`/api/focus/sessions/${sid}`);if(r.ok)renderSession(await r.json())}catch{}ui.frame.src=`/api/focus/sessions/${sid}/frames/latest?t=${Date.now()}`;document.querySelector('#empty').hidden=true}
function addTimeline(x){const line=document.createElement('div');line.className='event';line.textContent=`${x.occurred_at}  ${x.type}  ${JSON.stringify(x.data)}`;document.querySelector('#timeline').prepend(line)}
function connect(){hydrate();eventSource=new EventSource(`/api/focus/sessions/${sid}/events`);['session.state_changed','camera.frame_captured','camera.capture_failed','vision.batch_started','vision.batch_paused','vision.batch_completed','vision.batch_failed','stats.updated','voice.turn_started','voice.turn_completed','service.degraded'].forEach(t=>eventSource.addEventListener(t,e=>{const x=JSON.parse(e.data);addTimeline(x);if(x.type==='stats.updated')renderStats(x.data);if(x.type==='camera.frame_captured'){ui.frame.src=`/api/focus/sessions/${sid}/frames/latest?t=${Date.now()}`;document.querySelector('#frameMeta').textContent=`640×480 · ${x.data.frame_id} · 抓拍 ${x.data.latency_ms} ms`}if(x.type==='vision.batch_completed')document.querySelector('#latency').textContent=`最近 Step3 批次：${x.data.latency_ms} ms · ${x.data.model_name}`;if(['session.state_changed','camera.frame_captured','camera.capture_failed','vision.batch_completed','vision.batch_failed'].includes(x.type))hydrate()}))}
async function watchActive(){try{const r=await fetch('/api/focus/active');if(!r.ok)return;const s=await r.json();if(s.session_id===sid)return;if(eventSource)eventSource.close();sid=s.session_id;history.replaceState(null,'',`/?session=${encodeURIComponent(sid)}`);document.querySelector('#timeline').textContent='';connect()}catch{}}
health();setInterval(health,5000);if(sid)connect();watchActive();setInterval(watchActive,1000);
</script></body></html>"""
