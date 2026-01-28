import time
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from playwright.async_api import async_playwright
import uuid

app = FastAPI()

browser_process_id = None
process_start = None
browser_start = None
context_start = None

force_debug = False
ok_count = 0
ko_count = 0

playwright = None
browser = None
browser_lock = asyncio.Lock()

def log_process(message, process_id, force=force_debug):
    if force:
        print(f"[{process_id}] {message}")

def log_start_process(process_id):
    global process_start
    process_start = time.perf_counter()
    log_process("Process start", process_id, True)

def log_end_process(process_id):
    if process_start:
        log_process(f"Process end: {time.perf_counter() - process_start:.2f}s", process_id, True)
        log_process(f"Totals OK: {ok_count}", process_id, True)
        log_process(f"Totals KO: {ko_count}", process_id, True)

def log_start_browser():
    global browser_process_id, browser_start
    browser_process_id = uuid.uuid4().hex[:8]
    browser_start = time.perf_counter()
    log_process("Browser start", browser_process_id, True)

def log_end_browser():
    global browser_process_id
    if browser_start:
        log_process(f"Browser end: {time.perf_counter() - browser_start:.2f}s", browser_process_id, True)

def log_start_context(process_id):
    global context_start
    context_start = time.perf_counter()
    log_process("Context start", process_id)

def log_end_context(process_id):
    if context_start:
        log_process(f"Context end: {time.perf_counter() - context_start:.2f}s", process_id)

async def start_browser():
    global playwright, browser
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-translate",
            "--disable-notifications",
            "--disable-default-apps",
            "--mute-audio",
            "--no-first-run",
            "--no-zygote"
        ],
    )
    log_start_browser()

@app.on_event("startup")
async def startup():
    async with browser_lock:
        await start_browser()

@app.on_event("shutdown")
async def shutdown():
    global playwright, browser
    if browser:
        await browser.close()
    if playwright:
        await playwright.stop()
    log_end_browser()

def http_response(message, process_id, status=400):
    global ok_count, ko_count

    if status == 200:
        ok_count += 1
        body = {"code": message}
    else:
        ko_count += 1
        body = {"message": f"{message} [{process_id}]", "code": status}
        log_process(f"Response: {message}", process_id)

    log_end_process(process_id)

    return JSONResponse(
        status_code=status,
        content=body,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST",
            "Access-Control-Allow-Headers": "Content-Type",
        }
    )

@app.post("/")
async def fetch(request: Request):
    global force_debug

    process_id = uuid.uuid4().hex[:8]

    log_start_process(process_id)
    context = None
    captured_code = None

    try:
        payload = await request.json()
        url = payload.get("url")
        email = payload.get("email")
        password = payload.get("password")
        timeout_page = payload.get("timeout_page", 50000)
        timeout_input = payload.get("timeout_input", 50000)
        force_debug = payload.get("debug", False)

        if not url or not email or not password:
            return http_response("Missing required params", process_id)

        async with browser_lock:
            log_start_context(process_id)

            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                viewport={"width": 1280, "height": 720},
                java_script_enabled=True,
                bypass_csp=True,
                ignore_https_errors=True,
            )

            page = await context.new_page()

            #await page.route("**/*", lambda route, request: (
                #route.abort()
                #if request.resource_type in {"image", "media", "font", "stylesheet"}
                #or any(x in request.url for x in [
                #    "google-analytics",
                #    "googletagmanager",
                #    "doubleclick",
                #    "facebook",
                #    "hotjar",
                #    "clarity",
                #    "segment",
                #    "mixpanel"
                #])
                #else route.continue_()
            #))

            async def on_request_failed(req):
                nonlocal captured_code
                if req.url.startswith("mym"):
                    try:
                        query = req.url.split("?", 1)[1]
                        params = dict(p.split("=") for p in query.split("&"))
                        code = params.get("code")
                        if code:
                            captured_code = code
                            log_process("Code captured!", process_id)
                    except Exception as e:
                        log_process(f"URL parse error: {e}", process_id)

            page.on("requestfailed", on_request_failed)

            log_process(f"Navigating to login: {url}", process_id)
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_page)

            SELECTORS = {
                "email": '#gigya-login-form input[name="username"]',
                "password": '#gigya-login-form input[name="password"]',
                "submit": '#gigya-login-form input[type="submit"]',
                "authorize": '#cvs_from input[type="submit"]',
            }

            log_process("Waiting for login form...", process_id)
            await page.wait_for_selector(SELECTORS["email"], timeout=timeout_input)
            await page.wait_for_selector(SELECTORS["password"], timeout=timeout_input)

            log_process("Filling credentials...", process_id)
            await page.type(SELECTORS["email"], email, delay=50)
            await page.type(SELECTORS["password"], password, delay=50)

            log_process("Submitting login form...", process_id)
            await page.click(SELECTORS["submit"])

            log_process("Waiting for redirects...", process_id)
            await page.wait_for_load_state("domcontentloaded", timeout=timeout_page)

            log_process("Waiting for confirm form...", process_id)
            await page.wait_for_selector(SELECTORS["authorize"], timeout=timeout_input)

            log_process("Submitting confirm form...", process_id)
            await page.click(SELECTORS["authorize"])

            log_process("Waiting for code capture...", process_id)
            await asyncio.wait_for(
                asyncio.to_thread(lambda: captured_code),
                timeout=timeout_page / 1000
            )

            await context.close()
            log_end_context(process_id)

            if captured_code:
                return http_response(captured_code, process_id, 200)

            return http_response("Code not found", process_id)

    except Exception as e:
        log_process(f"Error: {e}", process_id, True)
        if context:
            await context.close()
            log_end_context(process_id)

        if captured_code:
            return http_response(captured_code, process_id, 200)

        return http_response(str(e), process_id)

@app.get("/health")
async def healthcheck():
    global playwright, browser

    process_id = uuid.uuid4().hex[:8]

    log_start_process(process_id)

    async with browser_lock:
        log_process("Check browser", process_id,True)
        try:
            context = await asyncio.wait_for(
                browser.new_context(),
                timeout=10000
            )
            page = await context.new_page()
            await page.goto("about:blank", timeout=10000)
            await context.close()

        except Exception as e:
            log_process(f"Restarting browser: {e}", process_id,True)
            try:
                if browser:
                    await browser.close()
            except Exception:
                pass

            try:
                if playwright:
                    await playwright.stop()
            except Exception:
                pass

            await start_browser()

    log_end_process(process_id)

    return {
        "status": "ok"
    }
