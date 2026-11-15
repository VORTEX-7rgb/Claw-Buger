import asyncio
import logging
from typing import Callable, Optional, Dict, Any, List
from playwright.async_api import Page, CDPSession
from models import TelemetryPayload, LayoutCollision, NetworkError

logger = logging.getLogger("clawbugger.sensor")

async def setup_cdp_screencast(
    page: Page,
    on_frame_callback: Optional[Callable[[str], Any]] = None
) -> Optional[CDPSession]:
    """
    Sets up CDP screencast streaming at 375x667 with immediate frame acknowledgment (Rule 2).
    """
    try:
        cdp = await page.context.new_cdp_session(page)
        
        async def on_screencast_frame(event: dict):
            session_id = event.get("sessionId")
            data = event.get("data")
            
            # Broadcast frame to callback (e.g. WebSocket streamer)
            if on_frame_callback and data:
                try:
                    res = on_frame_callback(data)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as ex:
                    logger.debug(f"Frame callback error: {ex}")
            
            # Rule 2: Immediately send screencastFrameAck so stream never freezes
            if session_id:
                try:
                    await cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
                except Exception as ex:
                    logger.debug(f"Frame ack error: {ex}")

        cdp.on("Page.screencastFrame", lambda ev: asyncio.create_task(on_screencast_frame(ev)))
        
        await cdp.send("Page.startScreencast", {
            "format": "jpeg",
            "quality": 75,
            "maxWidth": 750,
            "maxHeight": 1334,
            "everyNthFrame": 1
        })
        return cdp
    except Exception as e:
        logger.warning(f"Failed to initialize CDP screencast: {e}")
        return None

async def run_passive_sensor(
    page: Page,
    target_url: str,
    log_emitter: Optional[Callable[[str, str], Any]] = None
) -> TelemetryPayload:
    """
    Runs universal passive, layout, collision, and accessibility inspection.
    """
    network_errors: List[NetworkError] = []
    console_errors: List[str] = []

    # Listeners for network and console
    def handle_response(response):
        if response.status >= 400:
            network_errors.append(NetworkError(
                url=response.url,
                status=response.status,
                failure_text=f"HTTP {response.status}"
            ))

    def handle_console(msg):
        if msg.type == "error":
            console_errors.append(msg.text)

    page.on("response", handle_response)
    page.on("console", handle_console)

    if log_emitter:
        await log_emitter("INFO", f"📱 Setting viewport to 375x667 (iPhone SE scale)...")

    # Set mobile viewport for collision & overflow checks
    await page.set_viewport_size({"width": 375, "height": 667})

    if log_emitter:
        await log_emitter("INFO", f"🌐 Navigating to {target_url}...")

    main_res = await page.goto(target_url, wait_until="domcontentloaded", timeout=8000)
    main_headers = main_res.headers if main_res else {}
    await page.wait_for_timeout(500)  # Allow initial render / async scripts to settle

    # 1. Horizontal Scroll Overflow Check
    overflow_data = await page.evaluate("""() => {
        return {
            scrollWidth: document.documentElement.scrollWidth,
            windowWidth: window.innerWidth,
            hasOverflow: document.documentElement.scrollWidth > window.innerWidth
        };
    }""")

    # 2. Universal Mobile Layout Collision Check (Rule 1 enforced across viewport scroll range)
    collisions_raw = await page.evaluate("""() => {
        const collisions = [];
        const seen = new Set();
        const maxScroll = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
        const steps = [0];
        for (let s = 250; s <= maxScroll; s += 250) {
            steps.push(s);
        }
        if (maxScroll > 0 && !steps.includes(maxScroll)) {
            steps.push(maxScroll);
        }

        const all = Array.from(document.querySelectorAll('*'));
        const floating = all.filter(el => {
            const style = window.getComputedStyle(el);
            const isFloating = style.position === 'fixed' || style.position === 'sticky';
            const rect = el.getBoundingClientRect();
            return isFloating && rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
        });

        const interactive = Array.from(document.querySelectorAll('button, a, input, select, textarea, [role="button"]')).filter(el => {
            const style = window.getComputedStyle(el);
            return style.display !== 'none' && style.visibility !== 'hidden';
        });

        for (const scrollY of steps) {
            window.scrollTo(0, scrollY);

            for (const f of floating) {
                const fRect = f.getBoundingClientRect();
                const fTag = f.tagName.toLowerCase();
                const fClass = (f.className && typeof f.className === 'string') ? f.className.trim() : '';
                const fId = f.id ? '#' + f.id : '';
                const fSelector = fTag + (fId || (fClass ? '.' + fClass.split(/\\s+/)[0] : ''));

                for (const item of interactive) {
                    // Rule 1: Always skip if one is child of the other, or identical
                    if (f.contains(item) || item.contains(f) || f === item) {
                        continue;
                    }

                    const iRect = item.getBoundingClientRect();
                    
                    // Must be within or intersecting the visible viewport
                    if (iRect.bottom < 0 || iRect.top > window.innerHeight) {
                        continue;
                    }

                    // Calculate intersection dimensions
                    const interW = Math.max(0, Math.min(fRect.right, iRect.right) - Math.max(fRect.left, iRect.left));
                    const interH = Math.max(0, Math.min(fRect.bottom, iRect.bottom) - Math.max(fRect.top, iRect.top));

                    // Rule 1: Only flag intersections where both width and height are at least 4px
                    if (interW >= 4 && interH >= 4) {
                        const iTag = item.tagName.toLowerCase();
                        const iClass = (item.className && typeof item.className === 'string') ? item.className.trim() : '';
                        const iId = item.id ? '#' + item.id : '';
                        const iSelector = iTag + (iId || (iClass ? '.' + iClass.split(/\\s+/)[0] : ''));
                        const pairKey = fSelector + '->' + iSelector;

                        if (!seen.has(pairKey)) {
                            seen.add(pairKey);
                            collisions.push({
                                floating_tag: fTag,
                                floating_selector: fSelector,
                                blocked_tag: iTag,
                                blocked_selector: iSelector,
                                overlap_w: Math.round(interW),
                                overlap_h: Math.round(interH),
                                overlap_area: Math.round(interW * interH)
                            });
                        }
                    }
                }
            }
        }
        // Restore scroll position
        window.scrollTo(0, 0);
        return collisions;
    }""")

    layout_collisions = [LayoutCollision(**c) for c in collisions_raw]

    # 3. Accessibility & Image Alt Audit
    a11y_issues = await page.evaluate("""() => {
        const issues = [];
        const imgs = Array.from(document.querySelectorAll('img'));
        imgs.forEach(img => {
            const hasAlt = img.hasAttribute('alt');
            const altVal = img.getAttribute('alt');
            const src = img.getAttribute('src') || '';
            const id = img.id ? '#' + img.id : '';
            const className = (img.className && typeof img.className === 'string') ? '.' + img.className.split(/\\s+/)[0] : '';
            const selector = 'img' + (id || className);

            if (!hasAlt || altVal.trim() === '') {
                issues.push({
                    selector: selector,
                    tag: 'img',
                    src: src,
                    issue: 'Missing alt attribute for image (WCAG 1.1.1 Non-text Content)'
                });
            }
        });
        return issues;
    }""")

    # 4. Modern ARIA snapshot (Rule compliant)
    aria_text = None
    try:
        aria_text = await page.locator("body").aria_snapshot()
    except Exception as ex:
        logger.debug(f"ARIA snapshot error: {ex}")

    telemetry = TelemetryPayload(
        url=target_url,
        viewport={"width": 375, "height": 667},
        horizontal_overflow=overflow_data["hasOverflow"],
        scroll_width=overflow_data["scrollWidth"],
        window_width=overflow_data["windowWidth"],
        network_errors=network_errors,
        console_errors=console_errors,
        layout_collisions=layout_collisions,
        accessibility_issues=a11y_issues,
        aria_snapshot=aria_text,
        main_headers=main_headers
    )

    return telemetry
