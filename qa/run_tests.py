import asyncio
from playwright.async_api import async_playwright
import requests
import json
import time

scenarios = [
    {"id": "A1", "query": "What are our total sales in August 2026?"},
    {"id": "A2", "query": "Which item is the best seller?"},
    {"id": "A3", "query": "Show low stock items"},
    {"id": "A4", "query": "Show all open sales orders"},
    {"id": "A5", "query": "Show sales orders for Pinnacle Media Works"},
    {"id": "A6", "query": "Show purchase orders from supplier Zuckerman Security Ltd."},
    {"id": "A7", "query": "Look up customer Pinnacle Media Works"},
    {"id": "A8", "query": "What's the stock of ITEM-DESK-001?"},
    {"id": "A9_SO", "query": "Show sales order SAL-ORD-2026-00039"},
    {"id": "A9_PO", "query": "Show purchase order PUR-ORD-2026-00002"},
    {"id": "A10_1", "query": "hello"},
    {"id": "A10_2", "query": "what can you do?"}
]

async def run_tests():
    # Login via API to get sid
    session = requests.Session()
    login_res = session.post("http://localhost:8081/api/method/login", data={"usr": "Administrator", "pwd": "admin"})
    if login_res.status_code != 200:
        print("Login failed:", login_res.text)
        return
    sid = session.cookies.get("sid")
    print(f"Logged in, sid={sid}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_cookies([{
            "name": "sid",
            "value": sid,
            "domain": "localhost",
            "path": "/"
        }])

        page = await context.new_page()
        
        results = []
        for s in scenarios:
            print(f"Running {s['id']}...")
            await page.goto("http://localhost:8081/desk")
            
            # Wait for assistant toggle
            try:
                toggle = page.locator(".desk-assistant__toggle")
                await toggle.wait_for(state="attached", timeout=15000)
                
                # Check if assistant is open by looking at aria-expanded
                is_open = await toggle.get_attribute("aria-expanded")
                if is_open != "true":
                    await toggle.click()
                
                # Wait for chat input
                await page.wait_for_selector(".chat-input__textarea", state="visible")
                
                # Get the current number of messages
                messages = page.locator(".chat-message")
                initial_count = await messages.count()

                # Type and send
                await page.fill(".chat-input__textarea", s['query'])
                await page.click(".chat-input__send")

                # Wait for the next assistant message
                # It should be the initial count + 2 (one for user, one for assistant)
                # Or we can just wait for a new message with role assistant
                
                print("Waiting for response...")
                
                # Wait for network request to /chat to finish
                async with page.expect_response("**/chat") as response_info:
                    response = await response_info.value
                    
                resp_json = await response.json()
                reply_text = resp_json.get("response", "")
                
                # Wait for UI to update
                await page.wait_for_timeout(1000)
                
                # Get the last message from UI
                last_msg = page.locator(".chat-message").last
                ui_text = await last_msg.text_content()

                print(f"Result for {s['id']}: {reply_text[:100]}...")
                results.append({
                    "id": s['id'],
                    "query": s['query'],
                    "reply_text": reply_text,
                    "ui_text": ui_text,
                    "status": response.status
                })

            except Exception as e:
                print(f"Failed {s['id']}: {e}")
                await page.screenshot(path=f"qa/error_{s['id']}.png")
                results.append({
                    "id": s['id'],
                    "query": s['query'],
                    "error": str(e)
                })

        with open("qa/test_results.json", "w") as f:
            json.dump(results, f, indent=2)

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_tests())
