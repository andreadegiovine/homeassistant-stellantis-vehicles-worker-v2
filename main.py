import time
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from playwright.async_api import async_playwright

app = FastAPI()

process_start = None
browser_start = None


def log_start_process():
    global process_start
    process_start = time.perf_counter()
    print("Process start")


def log_end_process():
    if process_start:
        print(f"Process end: {time.perf_counter() - process_start:.2f}s")


def log_start_browser():
    global browser_start
    browser_start = time.perf_counter()
    print("Browser start")


def log_end_browser():
    if browser_start:
        print(f"Browser end: {time.perf_counter() - browser_start:.2f}s")


CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST",
    "Access-Control-Allow-Headers": "Content-Type",
}


def http_response(message, status=400):
    log_end_process()

    if status == 200:
        body = {"code": message}
    else:
        body = {"message": message, "code": status}

    print("Response:", message)

    return JSONResponse(
        status_code=status,
        content=body,
        headers=CORS_HEADERS,
    )


@app.post("/")
async def fetch(request: Request):
    log_start_process()
    browser = None

    try:
        payload = await request.json()
        url = payload.get("url")
        email = payload.get("email")
        password = payload.get("password")
        timeout_page = payload.get("timeout_page", 30000)
        timeout_input = payload.get("timeout_input", 20000)

        if not url or not email or not password:
            return http_response("Missing required params")

        captured_code = None

        async with async_playwright() as p:
            log_start_browser()

            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )

            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                viewport={"width": 1280, "height": 720},
            )
            
            page = await context.new_page()

            async def on_request_failed(req):
                nonlocal captured_code
                if req.url.startswith("mym"):
                    try:
                        query = req.url.split("?", 1)[1]
                        params = dict(p.split("=") for p in query.split("&"))
                        code = params.get("code")
                        if code:
                            captured_code = code
                            print("Code captured!")
                    except Exception as e:
                        print("URL parse error:", e)

            page.on("requestfailed", on_request_failed)

            print("Navigating to login:", url)
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_page)

            SELECTORS = {
                "email": '#gigya-login-form input[name="username"]',
                "password": '#gigya-login-form input[name="password"]',
                "submit": '#gigya-login-form input[type="submit"]',
                "authorize": '#cvs_from input[type="submit"]',
            }

            print("Waiting for login form...")            
            await page.wait_for_selector(SELECTORS["email"], timeout=timeout_input)
            await page.wait_for_selector(SELECTORS["password"], timeout=timeout_input)

            print("Filling credentials...")
            await page.type(SELECTORS["email"], email, delay=50)
            await page.type(SELECTORS["password"], password, delay=50)

            print("Submitting login form...")
            await page.click(SELECTORS["submit"])

            print("Waiting for redirects...")
            await page.wait_for_load_state("networkidle", timeout=timeout_page)

            print("Waiting for confirm form...")
            await page.wait_for_selector(SELECTORS["authorize"], timeout=timeout_input)

            print("Submitting confirm form...")
            await page.click(SELECTORS["authorize"])

            print("Waiting for code capture...")
            for _ in range((timeout_page/1000)):
                if captured_code:
                    break
                await asyncio.sleep(0.1)

            await browser.close()
            log_end_browser()

        if captured_code:
            return http_response(captured_code, 200)

        return http_response("Code not found")

    except Exception as e:
        print("Error:", e)
        if browser:
            await browser.close()
            log_end_browser()
        return http_response(str(e))
