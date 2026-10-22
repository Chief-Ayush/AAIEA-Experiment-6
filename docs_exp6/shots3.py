from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={"width":1300,"height":1900})
    pg.goto("http://localhost:8765/"); pg.wait_for_timeout(7000)
    pg.screenshot(path="images/raw_pulls_list.png")
    # select CALC-02 row: click the row checkbox

    pg.mouse.click(392, 191); pg.wait_for_timeout(2500)
    pg.get_by_role("button", name="Review").first.click(); pg.wait_for_timeout(15000)
    pg.screenshot(path="images/raw_pulls_review.png")
    for t in ("Changes","Tests","How it ran"):
        pg.get_by_role("tab", name=t).click(); pg.wait_for_timeout(2500)
        pg.screenshot(path=f"images/raw_tab_{t.split()[0].lower()}.png")
    b.close()
