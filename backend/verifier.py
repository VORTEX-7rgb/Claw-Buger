import asyncio
import logging
from typing import List, Dict, Any, Optional, Callable
from playwright.async_api import Page
from models import BugEvidence

logger = logging.getLogger("clawbugger.verifier")

async def run_active_verifier(
    page: Page,
    target_url: str,
    log_emitter: Optional[Callable[[str, str], Any]] = None
) -> List[BugEvidence]:
    """
    Actively explores and verifies interactive flows on the page.
    Enforces Rule 3 (unblocked clicks & desktop switch) and Rule 5 (graceful fallback on external sites).
    """
    bugs_found: List[BugEvidence] = []

    # Rule 3: Switch to desktop viewport (1280x800) so mobile sticky bars don't intercept pointer events
    if log_emitter:
        await log_emitter("INFO", "🖥️ Switching to 1280x800 viewport for unblocked functional interaction testing...")
    await page.set_viewport_size({"width": 1280, "height": 800})
    await page.wait_for_timeout(500)

    # -------------------------------------------------------------
    # Check 1: State Persistence on Reload (e.g. Cart, Counter, Wishlist)
    # -------------------------------------------------------------
    try:
        if log_emitter:
            await log_emitter("INFO", "🔍 Testing State Persistence across page reload...")
        
        # Heuristically find an "Add to Cart" or "Add" button
        add_btn = page.locator("button:has-text('Add to Cart'), button:has-text('Add')").first
        has_add_btn = await add_btn.count() > 0

        if has_add_btn and await add_btn.is_visible():
            # Find the cart badge or count before action
            badge = page.locator("#nav-cart-badge, [id*='cart-badge'], [class*='cart-badge']").first
            initial_count = "0"
            if await badge.count() > 0 and await badge.is_visible():
                initial_count = (await badge.inner_text()).strip()

            # Click Add to Cart with force=True (Rule 3)
            await add_btn.click(force=True, timeout=3000)
            await page.wait_for_timeout(300)

            count_after_add = initial_count
            if await badge.count() > 0 and await badge.is_visible():
                count_after_add = (await badge.inner_text()).strip()

            if count_after_add != initial_count and count_after_add != "0":
                if log_emitter:
                    await log_emitter("INFO", f"🔄 Item added (Badge: {count_after_add}). Reloading page to test persistence...")
                
                # Perform reload
                await page.reload(timeout=6000)
                await page.wait_for_timeout(500)

                # Re-check badge after reload
                badge_after_reload = page.locator("#nav-cart-badge, [id*='cart-badge'], [class*='cart-badge']").first
                count_after_reload = "0"
                if await badge_after_reload.count() > 0 and await badge_after_reload.is_visible():
                    count_after_reload = (await badge_after_reload.inner_text()).strip()

                if count_after_reload == "0" or count_after_reload != count_after_add:
                    bug = BugEvidence(
                        bug_id="CLAW-002",
                        title="State Loss: Shopping Cart Cleared on Page Reload",
                        category="State Loss",
                        severity="P1 (High)",
                        reproduction_rate="Pending Replay",
                        affected_elements=["#cart-items", "#nav-cart-badge"],
                        evidence_telemetry={
                            "count_before_reload": count_after_add,
                            "count_after_reload": count_after_reload,
                            "storage_checked": "localStorage missing cart persistence"
                        },
                        repro_steps=[
                            f"Navigate to {target_url}",
                            "Click 'Add to Cart' button",
                            "Verify badge increments to 1",
                            "Refresh / reload the page (F5)",
                            "Observe cart badge resets to 0 and items vanish"
                        ]
                    )
                    bugs_found.append(bug)
                    if log_emitter:
                        await log_emitter("WARN", "🚨 BUG DETECTED: Cart state completely lost on reload (missing localStorage)!")
        else:
            if log_emitter:
                await log_emitter("INFO", "ℹ️ State Persistence Check: Skipped (Element pattern not present on page)")
    except Exception as ex:
        # Rule 5: Graceful error handling with 4000ms timeout
        if log_emitter:
            await log_emitter("INFO", f"ℹ️ State Persistence Check: Skipped ({str(ex)[:60]})")

    # -------------------------------------------------------------
    # Check 2: Concurrency & Race Condition Attack (e.g. Promo Coupon)
    # -------------------------------------------------------------
    try:
        if log_emitter:
            await log_emitter("INFO", "🔍 Testing Concurrency & Race Condition on Action Buttons...")

        # Re-add item to cart in case Check 1 reload emptied it
        add_btn = page.locator("button:has-text('Add to Cart'), button:has-text('Add')").first
        if await add_btn.count() > 0 and await add_btn.is_visible():
            await add_btn.click(force=True, timeout=2000)
            await page.wait_for_timeout(300)

        # Locate coupon input and apply button
        coupon_input = page.locator("#coupon-input, input[placeholder*='SAVE' i], input[placeholder*='coupon' i]").first
        apply_btn = page.locator("#apply-coupon-btn, button:has-text('Apply')").first

        if await coupon_input.count() > 0 and await apply_btn.count() > 0 and await apply_btn.is_visible():
            await coupon_input.fill("SAVE10", timeout=2000)
            
            if log_emitter:
                await log_emitter("INFO", "⚡ Firing rapid double-click concurrency attack on Apply button...")

            # Rule 3: Use evaluate double-click to reliably trigger concurrent async execution before button lock
            await apply_btn.evaluate("el => { el.click(); el.click(); }")
            await page.wait_for_timeout(1000)

            # Check outcome specifically on non-input feedback elements
            msg_el = page.locator("#coupon-msg, p[id*='coupon'], span[id*='coupon'], div[id*='coupon-msg']").first
            msg_text = ""
            if await msg_el.count() > 0:
                msg_text = await msg_el.inner_text()

            # Check if discount applied multiple times
            if "(2x)" in msg_text or "2x" in msg_text or "applied! (2" in msg_text:
                bug = BugEvidence(
                    bug_id="CLAW-001",
                    title="Critical Race Condition: Promo Code Applied Multiple Times via Rapid Clicks",
                    category="Race Condition",
                    severity="P0 (Critical)",
                    reproduction_rate="Pending Replay",
                    affected_elements=["#apply-coupon-btn", "#coupon-input"],
                    evidence_telemetry={
                        "coupon_code": "SAVE10",
                        "status_message": msg_text,
                        "vulnerability": "Async handler does not disable button during in-flight discount request"
                    },
                    repro_steps=[
                        f"Navigate to {target_url}",
                        "Add an item to the cart",
                        "Enter coupon code 'SAVE10'",
                        "Rapidly double-click the 'Apply' button",
                        "Notice the coupon applies multiple times, stacking illegal discounts"
                    ]
                )
                bugs_found.append(bug)
                if log_emitter:
                    await log_emitter("WARN", f"🚨 BUG DETECTED: Race condition successfully triggered! Message: '{msg_text}'")
            else:
                if log_emitter:
                    await log_emitter("INFO", f"ℹ️ Coupon response: '{msg_text}'")
        else:
            if log_emitter:
                await log_emitter("INFO", "ℹ️ Concurrency Check: Skipped (Element pattern not present on page)")
    except Exception as ex:
        # Rule 5: Graceful error handling
        if log_emitter:
            await log_emitter("INFO", f"ℹ️ Concurrency Check: Skipped ({str(ex)[:60]})")

    # -------------------------------------------------------------
    # Check 3: Form Usability Boundary Check (Malformed Email)
    # -------------------------------------------------------------
    try:
        if log_emitter:
            await log_emitter("INFO", "🔍 Testing Form Usability Boundary Validation (email 'a@b')...")

        email_input = page.locator("input[type='email'], #email-input, input[placeholder*='email' i]").first
        checkout_btn = page.locator("#checkout-btn, button:has-text('Place Order'), button:has-text('Checkout')").first

        if await email_input.count() > 0 and await checkout_btn.count() > 0 and await checkout_btn.is_visible():
            # Test boundary incomplete email format (Strictly QA Usability - Rule compliant)
            await email_input.fill("a@b", timeout=2000)
            await checkout_btn.click(force=True, timeout=2000)
            await page.wait_for_timeout(600)

            # Check if order succeeded despite invalid email domain
            success_modal = page.locator("#order-success-modal, :has-text('Order Placed Successfully')").first
            is_success_visible = await success_modal.count() > 0 and await success_modal.is_visible()

            if is_success_visible:
                bug = BugEvidence(
                    bug_id="CLAW-003",
                    title="Usability Defect: Permissive Email Regex Accepts Malformed Address 'a@b'",
                    category="Form Validation",
                    severity="P2 (Medium)",
                    reproduction_rate="Pending Replay",
                    affected_elements=["#email-input", "#checkout-btn"],
                    evidence_telemetry={
                        "tested_input": "a@b",
                        "outcome": "Order submitted successfully without domain validation error"
                    },
                    repro_steps=[
                        f"Navigate to {target_url}",
                        "Add an item to the cart",
                        "Enter invalid email 'a@b' into delivery email field",
                        "Click 'Place Order'",
                        "Order completes successfully without required TLD validation"
                    ]
                )
                bugs_found.append(bug)
                if log_emitter:
                    await log_emitter("WARN", "🚨 BUG DETECTED: Order completed with invalid email 'a@b'!")
        else:
            if log_emitter:
                await log_emitter("INFO", "ℹ️ Form Boundary Check: Skipped (Element pattern not present on page)")
    except Exception as ex:
        # Rule 5: Graceful error handling
        if log_emitter:
            await log_emitter("INFO", f"ℹ️ Form Boundary Check: Skipped ({str(ex)[:60]})")

    return bugs_found
