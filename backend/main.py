import os
import sys
import json
import asyncio
import logging

if sys.platform == "win32":
    # Required for Playwright async subprocesses on Windows
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from typing import List, Dict, Any, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from playwright.async_api import async_playwright, Browser, Page

from models import AuditRequest, BugEvidence, TelemetryPayload, MultiPageTelemetry
from sensor import setup_cdp_screencast, run_passive_sensor
from verifier import run_active_verifier
from reproducer import reproduce_bug_3x
from analyst import generate_github_issue, generate_spec_ts
from crawler import run_multipage_crawler
from security_sensor import run_security_audit
from bridge.bridge_manager import (
    start_bridge_job,
    dispatch_telemetry_inbox,
    synthesize_outbox_fallback,
    read_bridge_outbox,
    wait_for_ai_outbox
)
from ai_bridge import get_latest_telemetry_payload

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("clawbugger.main")

app = FastAPI(title="ClawBugger QA Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store app directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET_STORE_DIR = os.path.join(BASE_DIR, "apps", "target-store")

if os.path.exists(TARGET_STORE_DIR):
    app.mount("/store", StaticFiles(directory=TARGET_STORE_DIR, html=True), name="target-store")

# Active WebSocket connections
active_connections: Set[WebSocket] = set()

# Latest audit report cache
latest_report: Dict[str, Any] = {
    "target_url": "",
    "bugs": [],
    "telemetry": {},
    "crawled_nodes": []
}

class ConnectionManager:
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        active_connections.discard(websocket)

    async def broadcast(self, message: dict):
        dead_connections = set()
        for conn in list(active_connections):
            try:
                await conn.send_text(json.dumps(message))
            except Exception:
                dead_connections.add(conn)
        for dead in dead_connections:
            active_connections.discard(dead)

manager = ConnectionManager()

async def launch_browser(p) -> Browser:
    """
    Robust browser launcher: Tries native Chrome, then Edge, then default Chromium.
    """
    for channel in ["chrome", "msedge", None]:
        try:
            if channel:
                logger.info(f"Attempting to launch browser with channel='{channel}'...")
                return await p.chromium.launch(channel=channel, headless=True)
            else:
                logger.info("Attempting to launch default Playwright chromium...")
                return await p.chromium.launch(headless=True)
        except Exception as e:
            logger.warning(f"Browser launch with channel={channel} failed: {e}")
    raise RuntimeError("Unable to launch any Chromium-compatible browser.")

async def execute_full_audit(target_url: str):
    """
    Coordinates the full ClawBugger 4-Category QA pipeline:
    1. CDP Screencast Setup (Continuous 30 FPS stream)
    2. Passive & Mobile Layout Sensor (375x667)
    3. Active UI Flow Verification (1280x800)
    4. 3/3 Parallel Sandbox Reproduction (CLAW-001 to CLAW-003)
    5. Multi-Page Autonomous Crawler (Sequential page hops on SAME page instance)
    6. Defensive Security & Configuration Hygiene Inspector (SEC-001 to SEC-003)
    7. ClawCore Neural Engine Cross-Vector Synthesis (Categorized 11-bug master report)
    """
    async def emit_log(level: str, msg: str):
        logger.info(f"[{level}] {msg}")
        await manager.broadcast({
            "type": "log",
            "level": level,
            "message": msg
        })

    # Rule 4: Clear stale outbox and generate fresh job_id
    job_id = start_bridge_job(target_url)

    # Rule 1: White-labeled neural engine log
    await emit_log("INFO", f"🧠 ClawCore Neural Engine: Initializing audit job #{job_id} on {target_url}...")

    try:
        async with async_playwright() as p:
            browser = await launch_browser(p)
            try:
                # Isolated primary context for inspection
                context = await browser.new_context(viewport={"width": 375, "height": 667})
                page = await context.new_page()

                # Rule 2: Setup CDP Screencast with frame forwarding
                async def on_screencast_frame(base64_data: str):
                    await manager.broadcast({
                        "type": "frame",
                        "data": base64_data
                    })

                cdp = await setup_cdp_screencast(page, on_screencast_frame)

                # -------------------------------------------------------------
                # Phase 1: Passive Sensor (Mobile 375px layout, collisions, a11y)
                # -------------------------------------------------------------
                await emit_log("INFO", "=== Phase 1: Passive Recon & Mobile Collision Math (375x667) ===")
                telemetry = await run_passive_sensor(page, target_url, emit_log)

                if telemetry.layout_collisions:
                    await emit_log("WARN", f"💥 Found {len(telemetry.layout_collisions)} mobile layout collisions!")
                    for coll in telemetry.layout_collisions:
                        await emit_log("WARN", f"   • {coll.floating_selector} blocks {coll.blocked_selector} (Overlap: {coll.overlap_w}x{coll.overlap_h}px)")

                if telemetry.network_errors:
                    for n_err in telemetry.network_errors:
                        await emit_log("WARN", f"⚠️ Network {n_err.status}: {n_err.url}")

                # -------------------------------------------------------------
                # Phase 2: Active UI Flow Verifier (Desktop switch, unblocked clicks)
                # -------------------------------------------------------------
                await emit_log("INFO", "=== Phase 2: Active UI Flow & Chaos QA Verification ===")
                active_bugs = await run_active_verifier(page, target_url, emit_log)

                # -------------------------------------------------------------
                # Phase 3: 3/3 Parallel Isolated Sandbox Reproduction (Rubric)
                # -------------------------------------------------------------
                if active_bugs:
                    await emit_log("INFO", f"=== Phase 3: Mandatory 3/3 Parallel Isolated Replay ({len(active_bugs)} functional bugs) ===")
                    for bug in active_bugs:
                        async def tick_callback(sandbox_id: int, status: str):
                            await manager.broadcast({
                                "type": "repro_tick",
                                "bug_id": bug.bug_id,
                                "sandbox": sandbox_id,
                                "status": status
                            })

                        await reproduce_bug_3x(browser, target_url, bug, emit_log, tick_callback)

                # -------------------------------------------------------------
                # Phase 4: Multi-Page Autonomous Crawler (Same page instance)
                # -------------------------------------------------------------
                async def on_crawl_node(node_dict: dict):
                    await manager.broadcast(node_dict)

                crawl_telemetry = await run_multipage_crawler(page, target_url, emit_log, on_crawl_node)

                # -------------------------------------------------------------
                # Phase 5: Defensive Security & Configuration Hygiene Inspector
                # -------------------------------------------------------------
                await emit_log("INFO", "=== Phase 5: Defensive Security & Configuration Hygiene Inspection ===")
                security_findings = await run_security_audit(page, target_url, telemetry.main_headers, emit_log)

                # -------------------------------------------------------------
                # Phase 6: ClawCore Neural Engine Cross-Vector Synthesis & Bridge Handoff
                # -------------------------------------------------------------
                await emit_log("INFO", "=== Phase 6: ClawCore Neural Engine Cross-Vector Synthesis & Bridge Handoff ===")
                await emit_log("INFO", f"🧠 ClawCore Neural Engine: Ingesting telemetry packet for job #{job_id}...")

                # Write to inbox for Antigravity bridge
                dispatch_telemetry_inbox(
                    job_id,
                    target_url,
                    telemetry.model_dump(),
                    [b.model_dump() for b in active_bugs],
                    crawl_telemetry.model_dump(),
                    security_findings
                )
                await emit_log("INFO", "🧠 ClawCore Neural Engine: Telemetry written to bridge. Waiting for live AI synthesis...")

                # Step 1: Wait up to 45.0s for Antigravity live AI synthesis before fallback
                final_bugs, ai_thoughts = await wait_for_ai_outbox(
                    job_id=job_id,
                    target_url=target_url,
                    emit_log=emit_log,
                    timeout=45.0,
                    telemetry_data=telemetry.model_dump(),
                    active_bugs_input=active_bugs,
                    crawl_data=crawl_telemetry.model_dump(),
                    security_data=security_findings
                )

                if ai_thoughts and "fallback" not in ai_thoughts.lower():
                    await emit_log("INFO", f"🧠 ClawCore Neural Engine [Live CoT]: {ai_thoughts[:250]}...")

                # Close audit context
                await context.close()
            finally:
                await browser.close()

        # Update cache
        latest_report["target_url"] = target_url
        latest_report["job_id"] = job_id
        latest_report["bugs"] = [b.model_dump() for b in final_bugs]
        latest_report["telemetry"] = telemetry.model_dump()
        latest_report["crawled_nodes"] = [n.model_dump() for n in crawl_telemetry.crawled_nodes]
        latest_report["security_findings"] = security_findings

        # Broadcast each proven bug to UI
        for bug in final_bugs:
            await manager.broadcast({
                "type": "bug_found",
                "bug": bug.model_dump()
            })

        await emit_log("SUCCESS", f"🏁 ClawBugger Audit Complete! Found {len(final_bugs)} confirmed bugs across 4 categories.")
        await manager.broadcast({
            "type": "audit_complete",
            "report": latest_report
        })
    except Exception as err:
        logger.error(f"Audit execution error: {err}", exc_info=True)
        await emit_log("ERROR", f"❌ Audit failed on {target_url}: {str(err)[:120]}")
        await manager.broadcast({
            "type": "audit_error",
            "error": str(err)
        })

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # If there's already a report cached, send it upon connecting
        if latest_report["bugs"]:
            await websocket.send_text(json.dumps({
                "type": "audit_complete",
                "report": latest_report
            }))

        while True:
            text = await websocket.receive_text()
            data = json.loads(text)
            action = data.get("action")
            
            if action == "start_audit":
                url = data.get("url", "http://localhost:8000/store")
                asyncio.create_task(execute_full_audit(url))

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)

@app.post("/api/audit")
async def trigger_audit(req: AuditRequest):
    asyncio.create_task(execute_full_audit(req.url))
    return {"status": "started", "target_url": req.url}

@app.get("/api/report")
async def get_report():
    return latest_report

@app.get("/api/telemetry/latest")
async def get_latest_telemetry():
    return get_latest_telemetry_payload()

@app.post("/api/github-issue")
async def create_github_issue(bug_data: Dict[str, Any]):
    try:
        bug = BugEvidence(**bug_data)
        issue_payload = generate_github_issue(bug, latest_report.get("target_url", "http://localhost:8000/store"))
        return {"status": "success", "issue": issue_payload}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/export-spec/{bug_id}")
async def export_spec(bug_id: str):
    for bug_dict in latest_report.get("bugs", []):
        if bug_dict.get("bug_id") == bug_id:
            spec = bug_dict.get("spec_code") or generate_spec_ts(BugEvidence(**bug_dict), latest_report.get("target_url", ""))
            return PlainTextResponse(spec, media_type="text/plain", headers={"Content-Disposition": f"attachment; filename={bug_id}.spec.ts"})
    raise HTTPException(status_code=404, detail="Bug ID not found")

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    if os.path.exists(dashboard_path):
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>ClawBugger Dashboard HTML missing</h1>"

if __name__ == "__main__":
    import uvicorn
    # Use reload=False on Windows to preserve WindowsProactorEventLoop for Playwright subprocesses
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
