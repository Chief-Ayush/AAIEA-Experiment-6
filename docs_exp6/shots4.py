from playwright.sync_api import sync_playwright
BASE="http://localhost:8765"
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={"width":1300,"height":1700})
    def go(path, wait=6000):
        pg.goto(f"{BASE}/{path}"); pg.wait_for_timeout(wait)
    go("ask")
    pg.get_by_test_id("stChatInputTextArea").fill("Analyze calculator.py and run its tests"); pg.keyboard.press("Enter"); pg.wait_for_timeout(9000)
    pg.screenshot(path="images/raw_ask.png")
    go("code"); pg.screenshot(path="images/raw_code0.png")
    pg.get_by_role("button", name="Run tests").click(); pg.wait_for_timeout(6000); pg.screenshot(path="images/raw_code.png")
    go("batch-testing"); pg.get_by_role("button", name="Run batch").click(); pg.wait_for_timeout(60000)
    pg.screenshot(path="images/raw_batch.png")
    go("how-it-works"); pg.screenshot(path="images/raw_overview.png")
    go("MCP_Inspector"); 
    go("evaluation"); pg.screenshot(path="images/raw_report.png")
    go("security-tests"); pg.screenshot(path="images/raw_security0.png")
    go("traces"); pg.screenshot(path="images/raw_traces.png")
    b.close()
