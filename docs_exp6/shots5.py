from playwright.sync_api import sync_playwright
BASE="http://localhost:8765"
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={"width":1300,"height":2300})
    pg.goto(f"{BASE}/inspector"); pg.wait_for_timeout(7000)
    pg.get_by_role("combobox", name="Tool").click(); pg.keyboard.type("get_diff"); pg.keyboard.press("Enter"); pg.wait_for_timeout(1500)
    ta=pg.get_by_label("Arguments (JSON)"); ta.fill('{"pr_id": "INV-09"}'); ta.press("Control+Enter"); pg.wait_for_timeout(1000)
    pg.get_by_role("button", name="Send tools/call").click(); pg.wait_for_timeout(4000)
    pg.screenshot(path="images/raw_inspector_call.png")
    pg.goto(f"{BASE}/security"); pg.wait_for_timeout(7000)
    pg.get_by_role("combobox", name="Attack").click(); pg.keyboard.type("Path traversal"); pg.keyboard.press("Enter"); pg.wait_for_timeout(1500)
    pg.get_by_role("button", name="Run attack").click(); pg.wait_for_timeout(6000)
    pg.screenshot(path="images/raw_security_attack.png")
    b.close()
