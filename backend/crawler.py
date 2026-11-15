import asyncio
import logging
from urllib.parse import urlparse, urljoin
from typing import List, Dict, Any, Optional, Callable, Set
from playwright.async_api import Page
from models import CrawlNode, MultiPageTelemetry

logger = logging.getLogger("clawbugger.crawler")

async def extract_same_origin_links(page: Page, base_url: str) -> List[str]:
    """
    Extracts all unique, same-origin internal links from the page.
    Filters out anchors, javascript:, mailto:, and external domains.
    """
    parsed_base = urlparse(base_url)
    base_host = parsed_base.netloc
    base_path_root = parsed_base.path.rstrip('/')

    raw_links = await page.evaluate("""() => {
        return Array.from(document.querySelectorAll('a[href]'))
            .map(a => a.getAttribute('href'))
            .filter(href => href && !href.startsWith('#') && !href.startsWith('javascript:') && !href.startsWith('mailto:'));
    }""")

    unique_urls: Set[str] = set()
    for link in raw_links:
        full_url = urljoin(base_url, link)
        parsed_link = urlparse(full_url)
        # Verify same origin (hostname and port)
        if parsed_link.netloc == base_host:
            # Normalize trailing slash
            cleaned = parsed_link.scheme + "://" + parsed_link.netloc + parsed_link.path
            if cleaned != base_url.rstrip('/') and cleaned != base_url:
                unique_urls.add(cleaned)

    return list(unique_urls)

async def check_mobile_drawer_trap(page: Page, log_emitter: Optional[Callable[[str, str], Any]] = None) -> Dict[str, Any]:
    """
    Safeguard 1: Tests for mobile hamburger drawer trap at 375x667.
    If a trap is detected, force-clears it via JS so subsequent interactions are never blocked.
    """
    trap_result = {"detected": False, "details": None}
    
    try:
        # Ensure mobile viewport (375x667) so mobile hamburger toggles (e.g. sm:hidden) are visible
        current_vp = page.viewport_size
        await page.set_viewport_size({"width": 375, "height": 667})
        await page.wait_for_timeout(300)

        # Check for mobile hamburger button
        menu_btn = page.locator("#mobile-nav-toggle, button[aria-label*='menu' i], button:has-text('Menu'), button:has-text('☰')").first
        if await menu_btn.count() > 0 and await menu_btn.is_visible():
            if log_emitter:
                await log_emitter("INFO", "📱 Testing mobile hamburger drawer for UX trap at 375x667...")
            
            await menu_btn.click(force=True, timeout=2000)
            await page.wait_for_timeout(400)

            # Inspect if a drawer/overlay is covering the viewport
            drawer_check = await page.evaluate("""() => {
                const candidates = Array.from(document.querySelectorAll('#mobile-nav-drawer, [class*="drawer"], [id*="drawer"], [class*="nav-menu"]'));
                for (const el of candidates) {
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    const isVisible = style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
                    const coversScreen = (rect.width >= window.innerWidth * 0.7) && (rect.height >= window.innerHeight * 0.5);
                    
                    if (isVisible && coversScreen) {
                        // Check if drawer has a visible close button
                        const closeBtns = Array.from(el.querySelectorAll('button, a, [role="button"]')).filter(btn => {
                            const bStyle = window.getComputedStyle(btn);
                            const bText = (btn.innerText || '').toLowerCase();
                            const bAria = (btn.getAttribute('aria-label') || '').toLowerCase();
                            return bStyle.display !== 'none' && (
                                bText.includes('close') || bText.includes('×') || bText.includes('✕') ||
                                bAria.includes('close') || btn.id.includes('close')
                            );
                        });
                        return {
                            hasDrawer: true,
                            id: el.id,
                            className: el.className,
                            hasCloseBtn: closeBtns.length > 0
                        };
                    }
                }
                return { hasDrawer: false };
            }""")

            if drawer_check.get("hasDrawer") and not drawer_check.get("hasCloseBtn"):
                trap_result["detected"] = True
                trap_result["details"] = {
                    "element": f"#{drawer_check.get('id', 'mobile-nav-drawer')}",
                    "issue": "Full-screen mobile drawer opened without a dismiss or close control (User Trap)"
                }
                if log_emitter:
                    await log_emitter("WARN", "🚨 BUG DETECTED: Mobile Navigation Drawer Trap (Missing close control)!")

            # Safeguard 1: Immediately force-hide the trapped overlay so it never blocks subsequent button testing!
            await page.evaluate("""() => {
                const drawer = document.getElementById('mobile-nav-drawer') || document.querySelector('[class*="drawer"], [id*="drawer"]');
                if (drawer) {
                    drawer.style.display = 'none';
                }
            }""")
            await page.wait_for_timeout(200)

        # Restore original viewport
        if current_vp:
            await page.set_viewport_size(current_vp)
            await page.wait_for_timeout(200)

    except Exception as ex:
        logger.debug(f"Mobile drawer check error: {ex}")

    return trap_result

async def test_safe_button_clicks(
    page: Page,
    current_url: str,
    log_emitter: Optional[Callable[[str, str], Any]] = None
) -> List[Dict[str, Any]]:
    """
    Safeguard 2: Clicks up to 3 safe interactive buttons/tabs while trapping on-click JS console exceptions.
    Sends Escape after each click to dismiss any open modals.
    """
    captured_crashes: List[Dict[str, Any]] = []

    # Temporary listener for uncaught page exceptions
    def handle_page_error(exc):
        err_msg = str(exc)
        captured_crashes.append({
            "url": current_url,
            "error_type": "Uncaught Page Exception",
            "message": err_msg
        })

    page.on("pageerror", handle_page_error)

    try:
        # Prioritize test targets like #filter-crafts-btn, or generic interactive buttons
        buttons_to_try = [
            "#filter-crafts-btn",
            "button:has-text('Filter')",
            "button:has-text('Deals')",
            "button:not([type='submit']):not(#checkout-btn):not(#apply-coupon-btn)"
        ]

        clicked_count = 0
        for selector in buttons_to_try:
            if clicked_count >= 3:
                break

            btn = page.locator(selector).first
            if await btn.count() > 0 and await btn.is_visible():
                btn_text = (await btn.inner_text()).strip() or selector
                if log_emitter:
                    await log_emitter("INFO", f"⚡ Testing button click: '{btn_text}'...")

                try:
                    await btn.click(force=True, timeout=2000)
                    await page.wait_for_timeout(300)
                    clicked_count += 1
                except Exception as click_err:
                    logger.debug(f"Click error on {selector}: {click_err}")

                # Safeguard: Send Escape to close any modal dialog or popup opened by the click
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(100)

    except Exception as ex:
        logger.debug(f"Button testing error: {ex}")
    finally:
        page.remove_listener("pageerror", handle_page_error)

    if captured_crashes and log_emitter:
        for crash in captured_crashes:
            await log_emitter("WARN", f"🚨 BUG DETECTED: Click-triggered JS Runtime Error: {crash['message'][:80]}")

    return captured_crashes

async def run_multipage_crawler(
    page: Page,
    target_url: str,
    log_emitter: Optional[Callable[[str, str], Any]] = None,
    node_emitter: Optional[Callable[[Dict[str, Any]], Any]] = None
) -> MultiPageTelemetry:
    """
    Autonomous Multi-Page QA Crawler.
    Safeguards 1, 2, & 3 enforced:
    - Reuses same page instance (CDP screencast never drops).
    - Tests entry page first, then hops to sub-routes (max 4).
    - Trapped mobile drawers are force-cleared after detection.
    """
    telemetry = MultiPageTelemetry(entry_url=target_url)

    if log_emitter:
        await log_emitter("INFO", "=== Phase 4: Autonomous Multi-Page QA Crawler & Site Tree Discovery ===")

    # -----------------------------------------------------------------
    # Step 1: Audit Entry Page (Safeguard 2)
    # -----------------------------------------------------------------
    entry_path = urlparse(target_url).path or "/"
    entry_node = CrawlNode(url=target_url, path=entry_path, status_code=200, status="healthy")

    # Meta check on entry page
    entry_meta = await page.evaluate("""() => {
        return {
            title: document.title,
            hasMetaDesc: !!document.querySelector('meta[name="description"]')
        };
    }""")
    entry_node.title = entry_meta.get("title")
    entry_node.has_meta_desc = entry_meta.get("hasMetaDesc", False)

    # Click-triggered exception test on entry page (#filter-crafts-btn -> CLAW-007)
    entry_crashes = await test_safe_button_clicks(page, target_url, log_emitter)
    if entry_crashes:
        entry_node.status = "buggy"
        for c in entry_crashes:
            telemetry.console_crashes.append(c)
            entry_node.issues.append(f"JS Crash on Click: {c['message']}")

    # Mobile drawer trap test on entry page (#mobile-nav-toggle -> CLAW-006)
    drawer_res = await check_mobile_drawer_trap(page, log_emitter)
    if drawer_res["detected"]:
        entry_node.status = "buggy"
        entry_node.drawer_trap_detected = True
        telemetry.mobile_traps.append(drawer_res["details"])
        entry_node.issues.append("Mobile Navigation Drawer Trap (Missing close control)")

    # Emit entry node to Live Site Tree Map
    telemetry.crawled_nodes.append(entry_node)
    if node_emitter:
        await node_emitter({
            "type": "crawl_node",
            "url": entry_node.url,
            "path": entry_node.path,
            "status": entry_node.status,
            "statusCode": entry_node.status_code,
            "issues": entry_node.issues
        })

    # -----------------------------------------------------------------
    # Step 2: Discover Same-Origin Internal Links (Max 4 sub-routes)
    # -----------------------------------------------------------------
    discovered_links = await extract_same_origin_links(page, target_url)
    telemetry.total_routes_discovered = len(discovered_links) + 1

    if log_emitter:
        await log_emitter("INFO", f"🌐 Discovered {len(discovered_links)} internal routes on {urlparse(target_url).netloc}...")

    # Strict cap to max 4 sub-routes (Rule 3)
    routes_to_crawl = discovered_links[:4]

    # -----------------------------------------------------------------
    # Step 3: Sequential Route Hops on SAME page instance (Rule 2)
    # -----------------------------------------------------------------
    for route in routes_to_crawl:
        route_path = urlparse(route).path or "/"
        sub_node = CrawlNode(url=route, path=route_path)

        if log_emitter:
            await log_emitter("INFO", f"➡️ Crawling sub-route: {route_path}...")

        try:
            # Sequential navigation inside the SAME page instance so CDP screencast continues
            res = await page.goto(route, wait_until="domcontentloaded", timeout=12000)
            status_code = res.status if res else 200
            sub_node.status_code = status_code

            if status_code >= 400:
                sub_node.status = "buggy"
                sub_node.issues.append(f"HTTP {status_code} Dead Route / Broken Link")
                telemetry.dead_links.append(route)
                if log_emitter:
                    await log_emitter("WARN", f"🚨 BUG DETECTED: Broken Internal Route (HTTP {status_code}) on {route_path}!")
            else:
                # Check metadata
                meta_info = await page.evaluate("""() => {
                    return {
                        title: document.title,
                        hasMetaDesc: !!document.querySelector('meta[name="description"]')
                    };
                }""")
                sub_node.title = meta_info.get("title")
                sub_node.has_meta_desc = meta_info.get("hasMetaDesc", False)

                # Test interactive buttons on sub-page
                sub_crashes = await test_safe_button_clicks(page, route, log_emitter)
                if sub_crashes:
                    sub_node.status = "buggy"
                    for sc in sub_crashes:
                        telemetry.console_crashes.append(sc)
                        sub_node.issues.append(f"JS Crash: {sc['message']}")

        except Exception as hop_err:
            sub_node.status = "buggy"
            sub_node.status_code = 404
            sub_node.issues.append(f"Navigation Failure: {str(hop_err)[:60]}")
            telemetry.dead_links.append(route)
            if log_emitter:
                await log_emitter("WARN", f"🚨 Route failed to load: {route_path} ({str(hop_err)[:40]})")

        telemetry.crawled_nodes.append(sub_node)
        if node_emitter:
            await node_emitter({
                "type": "crawl_node",
                "url": sub_node.url,
                "path": sub_node.path,
                "status": sub_node.status,
                "statusCode": sub_node.status_code,
                "issues": sub_node.issues
            })

        await page.wait_for_timeout(300)

    # Navigate back to original entry URL so the visual state resets cleanly
    try:
        await page.goto(target_url, wait_until="domcontentloaded", timeout=12000)
    except Exception:
        pass

    if log_emitter:
        buggy_count = sum(1 for n in telemetry.crawled_nodes if n.status == "buggy")
        await log_emitter("SUCCESS", f"✅ Multi-Page Crawl Complete: {len(telemetry.crawled_nodes)} routes scanned ({buggy_count} flagged with issues).")

    return telemetry
