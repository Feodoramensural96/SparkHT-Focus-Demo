from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from .models import FocusSessionCreate, SessionState
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
        if session is None or session.state in {
            SessionState.COMPLETED,
            SessionState.FAILED,
            SessionState.CANCELLED,
        }:
            raise HTTPException(status_code=404, detail="no active session")
        return session.model_dump(mode="json")

    @app.get("/api/focus/recent")
    async def get_recent_session() -> dict:
        try:
            return service.store.latest_session().model_dump(mode="json")
        except FileNotFoundError:
            raise HTTPException(
                status_code=404, detail="no persisted session"
            ) from None

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

    @app.get("/api/focus/sessions/{session_id}/history")
    async def event_history(session_id: str) -> list[dict]:
        try:
            return [
                event.model_dump(mode="json")
                for event in service.store.load_events(session_id, limit=200)
            ]
        except FileNotFoundError:
            raise HTTPException(
                status_code=404, detail="event history not found"
            ) from None

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
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>看见专注 · SparkHT</title>
<style>
:root{color-scheme:dark;font-family:Inter,"Noto Sans SC","Microsoft YaHei",sans-serif;background:#06101d;color:#edf4ff;--line:#223b59;--muted:#8fa7c6;--blue:#62adff;--up:#2dd4bf;--down:#a78bfa;--danger:#fb7185}
*{box-sizing:border-box}html{scrollbar-gutter:stable}body{margin:0;min-height:100vh;background:radial-gradient(circle at 45% -20%,#183c63 0,#0b1a2d 36%,#06101d 75%);line-height:1.42}
.shell{width:min(100% - 24px,3440px);min-height:100vh;margin:0 auto;padding:12px 0 9px;display:grid;grid-template-columns:minmax(230px,.72fr) minmax(660px,1.8fr) minmax(340px,1fr) minmax(300px,.9fr);grid-template-rows:auto minmax(0,1.7fr) minmax(190px,.85fr) auto;grid-template-areas:"top top top top" "metrics frame conversation status" "metrics frame conversation timeline" "privacy privacy privacy privacy";gap:10px}
.top{grid-area:top;display:flex;align-items:center;gap:16px;min-width:0;padding:2px 5px 5px}.top__title{display:flex;align-items:baseline;gap:12px;white-space:nowrap}.eyebrow{font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:#6eaef3}.top h1{font-size:24px;line-height:1;margin:0}.topline{min-width:0;display:flex;align-items:center;justify-content:flex-end;gap:7px;margin-left:auto;color:var(--muted);font-size:12px}.topline__copy{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.pill{width:max-content;max-width:100%;padding:4px 9px;border:1px solid #315d8e;border-radius:999px;background:#10243d;color:#8dc6ff;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.card{min-width:0;min-height:0;border:1px solid var(--line);background:#0b1728f2;border-radius:12px;padding:12px;box-shadow:0 12px 35px #0003;display:flex;flex-direction:column;overflow:hidden}.metrics-card{grid-area:metrics}.frame-card{grid-area:frame}.conversation-card{grid-area:conversation}.status-card{grid-area:status}.timeline-card{grid-area:timeline}.section-head{flex:0 0 auto;display:flex;flex-direction:column;gap:3px;margin-bottom:9px}.section-head--row{flex-direction:row;align-items:flex-start;justify-content:space-between;gap:8px}.section-head h2{font-size:15px;margin:0}.hint,.meta{color:var(--muted);font-size:11px;margin:0}.limit{flex:0 0 auto;padding:2px 7px;border:1px solid #35506f;border-radius:999px;color:#a8bdd8;font-size:10px;white-space:nowrap}
.metrics{flex:1;min-height:0;display:flex;flex-direction:column;justify-content:center;gap:7px}.metric{min-height:56px;padding:9px 11px;border:1px solid #263c5b;border-radius:9px;background:#101f34;display:flex;align-items:center;justify-content:space-between;gap:10px}.metric span{font-size:12px;color:#b7c7df}.metric b{font-size:24px;line-height:1;color:var(--blue);font-variant-numeric:tabular-nums}.metric--score{min-height:70px;border-color:#315d8e;background:linear-gradient(105deg,#10223a,#153455)}.metric--score b{color:#8bc7ff;font-size:30px}
.conversation{flex:1;min-height:0;display:flex;flex-direction:column;gap:8px;overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable;padding-right:4px}.dialogue{flex:0 0 auto;border:1px solid;padding:9px 10px;border-radius:9px}.dialogue--up{border-color:#1f7c74;background:#0c2b2d}.dialogue--down{border-color:#5b45a0;background:#211b3b}.dialogue__head{display:flex;flex-direction:column;gap:2px;margin-bottom:4px;font-size:10px}.dialogue--up .dialogue__head{color:#68eadb}.dialogue--down .dialogue__head{color:#c4b5fd}.dialogue__text{font-size:14px;white-space:pre-wrap;overflow-wrap:anywhere}.dialogue__meta{margin-top:5px;color:#9caec8;font-size:10px}.empty-state{padding:15px 10px;border:1px dashed #304563;border-radius:9px;color:var(--muted);font-size:12px;text-align:center}
.frame-card .section-head{margin-bottom:6px}.frame-wrap{width:min(100%,640px);margin:auto}.frame{position:relative;width:640px;max-width:100%;aspect-ratio:4/3;background:#03070d;border:0;box-shadow:0 0 0 1px #2a3d58,0 18px 50px #0006;border-radius:8px;overflow:hidden;display:flex;align-items:center;justify-content:center}.frame img{display:block;width:640px;max-width:100%;height:auto;aspect-ratio:4/3;object-fit:contain}.frame span{position:absolute;color:#758aa8;font-size:13px}.frame-link{display:block;color:inherit;text-decoration:none}.frame-link:focus-visible{outline:2px solid var(--blue);outline-offset:3px}.frame-caption{display:flex;flex-direction:row;justify-content:space-between;gap:8px;margin-top:7px}.frame-caption .meta:last-child{text-align:right}
.status{display:flex;align-items:flex-start;gap:7px;margin:0}.dot{flex:0 0 auto;width:8px;height:8px;margin-top:5px;border-radius:50%;background:#eab308}.status-copy{font-size:11px;color:#b8c8df;overflow-wrap:anywhere}.facts{min-height:0;display:flex;flex-direction:column;gap:5px;margin-top:8px;overflow-y:auto}.fact{padding:7px 9px;border-radius:7px;background:#081321;color:#aebed5;font-size:11px}
.timeline{flex:1;min-height:0;display:flex;flex-direction:column;overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable;padding-right:3px}.event{display:grid;grid-template-columns:64px minmax(0,1fr);column-gap:8px;padding:6px 2px;border-bottom:1px solid #1b2a3f}.event:last-child{border-bottom:0}.event__time{grid-row:1/3;color:#6f87a8;font:10px ui-monospace,SFMono-Regular,Consolas,monospace}.event__title{font-size:11px;color:#dce8f8}.event__detail{font-size:10px;color:#8fa5c4;overflow-wrap:anywhere}.event--danger .event__title{color:var(--danger)}
.privacy{grid-area:privacy;min-width:0;font-size:10px;color:#7086a4;padding:1px 5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(min-width:1751px) and (min-height:800px){body{overflow:hidden}.shell{height:100dvh}}
@media(max-width:1750px){.shell{grid-template-columns:minmax(260px,.75fr) minmax(600px,1.6fr);grid-template-rows:auto auto auto auto auto;grid-template-areas:"top top" "metrics frame" "conversation conversation" "status timeline" "privacy privacy"}.metrics{display:grid;grid-template-columns:1fr 1fr}.metric--score{grid-column:1/-1}.conversation{max-height:430px}.status-card,.timeline-card{min-height:260px}}
@media(max-width:920px){.shell{width:min(100% - 12px,760px);padding-top:8px;display:flex;flex-direction:column}.top{align-items:flex-start;flex-direction:column;gap:7px}.topline{width:100%;margin-left:0;justify-content:flex-start;flex-wrap:wrap}.topline__copy{width:100%}.card{padding:10px;overflow:visible}.metrics{display:flex}.conversation{max-height:420px}.frame-caption{flex-direction:column}.frame-caption .meta:last-child{text-align:left}.status-card,.timeline-card{min-height:0}.timeline{max-height:320px}.privacy{white-space:normal}}
</style>
</head>
<body>
<main class="shell">
  <header class="top">
    <div class="top__title"><h1>看见专注</h1><div class="eyebrow">SparkHT · On-device focus</div></div>
    <div class="topline">
      <span class="topline__copy">端侧快慢双系统机器人 · 带鱼屏横向全景监控</span>
      <span class="pill">Step3-VL-10B-FP8</span>
      <span id="sessionState" class="pill">未选择会话</span>
    </div>
  </header>

  <section class="card metrics-card" aria-labelledby="metricsTitle">
    <div class="section-head"><h2 id="metricsTitle">核心指标</h2><p class="hint">真实累计统计 · 不确定样本不进入分母</p></div>
    <div class="metrics">
      <div class="metric metric--score"><span>专注趋势指数</span><b id="score">—</b></div>
      <div class="metric"><span>人员在位率</span><b id="presence">—</b></div>
      <div class="metric"><span>手机可见率</span><b id="phone">—</b></div>
      <div class="metric"><span>手机状态变化</span><b id="phoneTransitions">—</b></div>
      <div class="metric"><span>疑似杯子移动</span><b id="drink">—</b></div>
    </div>
  </section>

  <section class="card conversation-card" aria-labelledby="conversationTitle">
    <div class="section-head section-head--row"><div><h2 id="conversationTitle">机器人对话</h2><p class="hint">青色上行 · 紫色下行 · 自动滚动</p></div><span class="limit">最近 4 条</span></div>
    <div id="conversation" class="conversation" aria-live="polite"><div id="conversationEmpty" class="empty-state">等待机器人上行或下行对话</div></div>
  </section>

  <section class="card frame-card" aria-labelledby="frameTitle">
    <div class="section-head"><h2 id="frameTitle">机器人原始画面</h2><p class="hint">按 640×480 原始 4:3 比例显示，不裁切、不拉伸</p></div>
    <div class="frame-wrap">
      <a id="frameLink" class="frame-link" target="_blank" rel="noopener" aria-label="在新窗口查看原始图片">
        <div class="frame"><img id="frame" alt="机器人最新 640×480 抓拍"><span id="empty">等待会话画面</span></div>
      </a>
      <div class="frame-caption"><p id="frameMeta" class="meta">原始分辨率 640×480 · 本地处理</p><p class="meta">点击图片可单独查看原始 JPEG</p></div>
    </div>
  </section>

  <section class="card status-card" aria-labelledby="statusTitle">
    <div class="section-head"><h2 id="statusTitle">技术状态</h2></div>
    <p class="status"><span id="healthDot" class="dot"></span><span id="health" class="status-copy">正在检查本地服务</span></p>
    <div class="facts"><div id="counts" class="fact">抓拍 — · 已分析 — · 失败 — · 丢弃 —</div><div id="latency" class="fact">最近 Step3 批次：—</div><div class="fact">视觉单并发 · 语音活动时暂停或取消慢任务</div></div>
  </section>

  <section class="card timeline-card" aria-labelledby="timelineTitle">
    <div class="section-head"><h2 id="timelineTitle">事件时间线</h2><p class="hint">新事件在上 · 对话已单独高亮</p></div>
    <div id="timeline" class="timeline" aria-live="polite"><div class="empty-state">等待会话事件</div></div>
  </section>

  <footer class="privacy">仅观察人物、明显手机与杯子变化；不做 OCR、身份或情绪识别。所有指标均为低分辨率视觉代理统计，仅供参考。</footer>
</main>

<script>
const EVENT_TYPES=['session.state_changed','camera.frame_captured','camera.capture_failed','vision.batch_started','vision.batch_paused','vision.batch_completed','vision.batch_failed','stats.updated','voice.turn_started','voice.turn_completed','service.degraded'];
const STATE_LABELS={starting:'启动中',running:'统计中',finalizing:'收尾中',completed:'已完成',failed:'失败',cancelled:'已取消'};
const INTENT_LABELS={start:'开始统计',status:'查询状态',stop:'结束统计',cancel:'取消统计',auto_summary:'自动总结'};
const MAX_DIALOGUES=4,qs=new URLSearchParams(location.search);let sid=qs.get('session'),eventSource,reportSummary='',seenEvents=new Set();
const pct=v=>v==null?'—':Math.round(v*100)+'%';
const ui={presence:document.querySelector('#presence'),phone:document.querySelector('#phone'),phoneTransitions:document.querySelector('#phoneTransitions'),drink:document.querySelector('#drink'),score:document.querySelector('#score'),frame:document.querySelector('#frame'),frameLink:document.querySelector('#frameLink')};
function renderStats(d){ui.presence.textContent=pct(d.presence_ratio);ui.phone.textContent=pct(d.phone_visible_ratio);ui.phoneTransitions.textContent=d.phone_transition_count??'—';ui.drink.textContent=d.suspected_drink_events??'—';ui.score.textContent=d.focus_proxy_score==null?'—':Math.round(d.focus_proxy_score)}
function renderSession(s){renderStats(s.stats);document.querySelector('#sessionState').textContent=`${STATE_LABELS[s.state]||s.state} · ${s.session_id}`;document.querySelector('#counts').textContent=`抓拍 ${s.captured_frames} · 已分析 ${s.stats.analyzed_frames} · 失败 ${s.failed_frames} · 丢弃 ${s.dropped_batches}`}
function setFrame(frameId,latency){const url=`/api/focus/sessions/${encodeURIComponent(sid)}/frames/latest?t=${Date.now()}`;ui.frame.src=url;ui.frameLink.href=url;document.querySelector('#empty').hidden=true;ui.frame.onload=()=>{const size=`${ui.frame.naturalWidth}×${ui.frame.naturalHeight}`;document.querySelector('#frameMeta').textContent=`原始分辨率 ${size}${frameId?` · ${frameId}`:''}${latency!=null?` · 抓拍 ${latency} ms`:''}`}}
async function health(){try{const r=await fetch('/health'),x=await r.json(),parts=Object.entries(x.components).map(([n,v])=>`${n} ${v.status}`);document.querySelector('#health').textContent=parts.join(' · ');document.querySelector('#healthDot').style.background=x.status==='healthy'?'#22c55e':x.status==='degraded'?'#eab308':'#ef4444'}catch{document.querySelector('#health').textContent='健康检查失败';document.querySelector('#healthDot').style.background='#ef4444'}}
function clock(value){try{return new Intl.DateTimeFormat('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value))}catch{return value}}
function addDialogue(x){let direction,text,label,meta='';if(x.type==='voice.turn_started'&&x.data.transcript){direction='up';text=x.data.transcript;label='机器人上行 · 麦克风 → SparkHT';meta='语音识别完成'}else if(x.type==='voice.turn_completed'){direction='down';text=x.data.reply||(x.data.source==='session_timer'?reportSummary:'机器人已响应（旧事件未保存回复文本）');label='机器人下行 · SparkHT → 扬声器';const intent=INTENT_LABELS[x.data.intent]||x.data.intent;const latency=x.data.speech_to_first_audio_ms;meta=[intent,latency==null?'':`首音 ${latency} ms`].filter(Boolean).join(' · ')}else{return}document.querySelector('#conversationEmpty')?.remove();const conversation=document.querySelector('#conversation'),item=document.createElement('article');item.className=`dialogue dialogue--${direction}`;const head=document.createElement('div');head.className='dialogue__head';head.textContent=`${label} · ${clock(x.occurred_at)}`;const body=document.createElement('div');body.className='dialogue__text';body.textContent=text||'下行播放完成';item.append(head,body);if(meta){const extra=document.createElement('div');extra.className='dialogue__meta';extra.textContent=meta;item.append(extra)}conversation.append(item);const items=[...conversation.querySelectorAll('.dialogue')];items.slice(0,-MAX_DIALOGUES).forEach(old=>old.remove());requestAnimationFrame(()=>conversation.scrollTo({top:conversation.scrollHeight,behavior:'smooth'}))}
function eventCopy(x){const d=x.data;switch(x.type){case'session.state_changed':return['会话状态',STATE_LABELS[d.state]||d.state];case'camera.frame_captured':return['抓拍完成',`${d.frame_id} · ${d.latency_ms} ms`];case'camera.capture_failed':return['抓拍失败',d.error||'未知错误'];case'vision.batch_started':return['Step3 开始',`${(d.frame_ids||[]).length} 帧`];case'vision.batch_paused':return['Step3 因语音暂停',`${(d.frame_ids||[]).join('、')}`];case'vision.batch_completed':return['Step3 完成',`${d.latency_ms} ms · ${(d.observations||[]).length} 个观察`];case'vision.batch_failed':return['Step3 失败',d.error||'分析失败'];case'stats.updated':return['核心指标已更新',`已分析 ${d.analyzed_frames} 帧`];case'service.degraded':return['服务降级',`${d.component||''} ${d.reason||''}`.trim()];default:return[x.type,'']}}
function addTimeline(x){if(x.type.startsWith('voice.'))return;const empty=document.querySelector('#timeline .empty-state');if(empty)empty.remove();const [title,detail]=eventCopy(x);const line=document.createElement('div');line.className=`event ${x.type.includes('failed')||x.type==='service.degraded'?'event--danger':''}`;const time=document.createElement('div');time.className='event__time';time.textContent=clock(x.occurred_at);const heading=document.createElement('div');heading.className='event__title';heading.textContent=title;line.append(time,heading);if(detail){const copy=document.createElement('div');copy.className='event__detail';copy.textContent=detail;line.append(copy)}document.querySelector('#timeline').prepend(line)}
function renderEvent(x){if(seenEvents.has(x.event_id))return;seenEvents.add(x.event_id);addDialogue(x);addTimeline(x);if(x.type==='stats.updated')renderStats(x.data);if(x.type==='camera.frame_captured')setFrame(x.data.frame_id,x.data.latency_ms);if(x.type==='vision.batch_completed')document.querySelector('#latency').textContent=`最近 Step3 批次：${x.data.latency_ms} ms · ${x.data.model_name}`;if(['session.state_changed','camera.capture_failed','vision.batch_completed','vision.batch_failed'].includes(x.type))hydrateSession()}
async function hydrateSession(){try{const r=await fetch(`/api/focus/sessions/${encodeURIComponent(sid)}`);if(r.ok)renderSession(await r.json())}catch{}}
async function hydrate(){await hydrateSession();try{const r=await fetch(`/api/focus/sessions/${encodeURIComponent(sid)}/report`);if(r.ok)reportSummary=(await r.json()).summary}catch{}try{const r=await fetch(`/api/focus/sessions/${encodeURIComponent(sid)}/history`);if(r.ok)(await r.json()).forEach(renderEvent)}catch{}setFrame()}
async function connect(){await hydrate();eventSource=new EventSource(`/api/focus/sessions/${encodeURIComponent(sid)}/events`);EVENT_TYPES.forEach(type=>eventSource.addEventListener(type,event=>renderEvent(JSON.parse(event.data))))}
function resetView(){seenEvents=new Set();reportSummary='';document.querySelector('#conversation').innerHTML='<div id="conversationEmpty" class="empty-state">等待机器人上行或下行对话</div>';document.querySelector('#timeline').innerHTML='<div class="empty-state">等待会话事件</div>'}
async function watchActive(){try{let r=await fetch('/api/focus/active');if(!r.ok){if(sid)return;r=await fetch('/api/focus/recent');if(!r.ok)return}const s=await r.json();if(s.session_id===sid)return;if(eventSource)eventSource.close();sid=s.session_id;history.replaceState(null,'',`/?session=${encodeURIComponent(sid)}`);resetView();connect()}catch{}}
health();setInterval(health,10000);if(sid)connect();watchActive();setInterval(watchActive,3000);
</script>
</body>
</html>"""
