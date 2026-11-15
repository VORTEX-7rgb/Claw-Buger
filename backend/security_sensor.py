import re
import logging
from typing import Dict, Any, List, Optional, Callable
from playwright.async_api import Page

logger = logging.getLogger("clawbugger.security_sensor")

# Rule 2: Flexible regex lengths matching seeded and production token patterns
REGEX_PATTERNS = {
    "Google / Firebase API Key": r"AIzaSy[0-9A-Za-z\-_]{15,45}",
    "Stripe Secret / Restricted Key": r"sk_(?:live|test)_[0-9a-zA-Z_]{10,45}",
    "GitHub Personal Access Token": r"ghp_[0-9a-zA-Z]{20,45}",
}

async def run_security_audit(
    page: Page,
    target_url: str,
    main_doc_headers: Dict[str, str],
    log_emitter: Optional[Callable[[str, str], Any]] = None
) -> Dict[str, Any]:
    """
    100% Defensive & Passive Client Security & Header Hygiene Inspector.
    Zero offensive attack payloads are transmitted (Filter-Safe Guarantee).
    """
    findings: Dict[str, Any] = {
        "missing_headers": [],
        "leaked_secrets": [],
        "insecure_storage": [],
        "mixed_content": []
    }

    if log_emitter:
        await log_emitter("INFO", "🛡️ Running Defensive Security & Header Hygiene Inspection...")

    # -----------------------------------------------------------------
    # Check 1: Grouped HTTP Security Headers (SEC-001)
    # -----------------------------------------------------------------
    # Normalize header keys to lowercase
    normalized_headers = {k.lower(): v for k, v in main_doc_headers.items()}
    
    missing_headers = []
    if "content-security-policy" not in normalized_headers:
        missing_headers.append("Content-Security-Policy (CSP)")
    if "x-frame-options" not in normalized_headers:
        missing_headers.append("X-Frame-Options (Clickjacking Protection)")
    if "x-content-type-options" not in normalized_headers:
        missing_headers.append("X-Content-Type-Options: nosniff")

    if missing_headers:
        findings["missing_headers"] = missing_headers
        if log_emitter:
            await log_emitter("WARN", f"🚨 SECURITY DEFECT: Missing defensive headers: {', '.join(missing_headers)}")

    # -----------------------------------------------------------------
    # Check 2: Leaked Secret & Token Scanner in DOM & Comments (SEC-002)
    # -----------------------------------------------------------------
    try:
        html_content = await page.content()
        for token_type, pattern in REGEX_PATTERNS.items():
            matches = re.findall(pattern, html_content)
            if matches:
                for match in set(matches):
                    # Mask the middle of the token for secure reporting
                    masked = match[:6] + "..." + match[-4:] if len(match) > 10 else "******"
                    findings["leaked_secrets"].append({
                        "type": token_type,
                        "raw_token": match,
                        "masked": masked,
                        "sample_context": f"Exposed in client markup: {masked}"
                    })
                    if log_emitter:
                        await log_emitter("WARN", f"🚨 CRITICAL SECURITY: {token_type} exposed in client markup ({masked})!")
    except Exception as ex:
        logger.debug(f"Secret scanner error: {ex}")

    # -----------------------------------------------------------------
    # Check 3: Plaintext Sensitive Token in LocalStorage (SEC-003)
    # -----------------------------------------------------------------
    try:
        storage_findings = await page.evaluate("""() => {
            const sensitiveKeys = ['auth', 'token', 'jwt', 'secret', 'password', 'apikey'];
            const leaks = [];
            for (let i = 0; i < localStorage.length; i++) {
                const key = localStorage.key(i);
                const val = localStorage.getItem(key);
                const lowerKey = key.toLowerCase();
                if (sensitiveKeys.some(k => lowerKey.includes(k))) {
                    leaks.push({
                        storage: 'localStorage',
                        key: key,
                        val_preview: val.length > 8 ? val.substring(0, 4) + '...' : '***'
                    });
                }
            }
            return leaks;
        }""")
        if storage_findings:
            findings["insecure_storage"] = storage_findings
            if log_emitter:
                for leak in storage_findings:
                    await log_emitter("WARN", f"🚨 SECURITY DEFECT: Sensitive key '{leak['key']}' stored in plaintext in localStorage!")
    except Exception as ex:
        logger.debug(f"Storage audit error: {ex}")

    # -----------------------------------------------------------------
    # Check 4: Mixed Content Detection (HTTP on HTTPS)
    # -----------------------------------------------------------------
    if target_url.startswith("https://"):
        try:
            mixed_urls = await page.evaluate("""() => {
                const urls = [];
                document.querySelectorAll('img[src], script[src], link[href]').forEach(el => {
                    const src = el.src || el.href;
                    if (src && src.startsWith('http://')) urls.push(src);
                });
                return urls;
            }""")
            if mixed_urls:
                findings["mixed_content"] = mixed_urls
                if log_emitter:
                    await log_emitter("WARN", f"🚨 SECURITY DEFECT: Insecure mixed-content detected ({len(mixed_urls)} items)!")
        except Exception as ex:
            logger.debug(f"Mixed content check error: {ex}")

    return findings
