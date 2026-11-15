// Drives Stripe-hosted pages in TEST MODE only, from the isolated-QA acceptance runner.
// Modes (MODE env): "pay" (complete hosted Checkout with the supplied TEST card) and "portal" (open the billing portal).
// Input comes from environment variables only; nothing sensitive is printed (no URLs, no emails, no card data).
// Output: one JSON line  {"mode":..,"outcome":..,"detail":..}  where outcome is one of
//   complete | declined | portal_loaded | blocked
import { chromium } from "@playwright/test";

const mode = process.env.MODE;
const url = process.env.TARGET_URL || "";
const host = new URL(url).hostname;
const allowed = mode === "portal" ? "billing.stripe.com" : "checkout.stripe.com";
const out = (outcome, detail = "") => {
  console.log(JSON.stringify({ mode, outcome, detail }));
};
if (host !== allowed) {
  out("blocked", "unexpected host");
  process.exit(0);
}

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 1600 } });
  page.setDefaultTimeout(45000);
  await page.goto(url, { waitUntil: "domcontentloaded" });

  if (mode === "portal") {
    await page.waitForLoadState("networkidle").catch(() => {});
    const text = (await page.locator("body").innerText()).toLowerCase();
    const email = (process.env.EXPECT_EMAIL || "").toLowerCase();
    const hasEmail = email !== "" && text.includes(email);
    const hasBilling = /billing|subscription|invoice|payment/.test(text);
    out(hasBilling ? "portal_loaded" : "blocked", `billing_content=${hasBilling} customer_email_shown=${hasEmail}`);
  } else {
    const card = process.env.TEST_CARD || "";
    if (!/^(4242424242424242|4000000000000002)$/.test(card)) {
      out("blocked", "card is not an allowed Stripe test card");
    } else {
      const number = page.getByLabel(/card number/i).first();
      await number.waitFor({ state: "visible" });
      await number.fill(card);
      await page.getByLabel(/expiration|expiry/i).first().fill("12 / 34");
      await page.getByLabel(/security code|cvc/i).first().fill("123");
      const name = page.getByLabel(/name on card|cardholder name/i).first();
      if (await name.count()) await name.fill("QA Acceptance");
      const postcode = page.getByLabel(/postal code|zip|postcode/i).first();
      if (await postcode.count()) await postcode.fill("SW1A 1AA");
      const email = page.getByLabel(/^email/i).first();
      if ((await email.count()) && (await email.inputValue().catch(() => "")) === "" && process.env.EXPECT_EMAIL) {
        await email.fill(process.env.EXPECT_EMAIL);
      }
      await page.locator('button[type="submit"]').first().click();
      // Success redirects away from checkout.stripe.com; a decline keeps the page and shows an error.
      const result = await Promise.race([
        page.waitForURL((u) => u.hostname !== "checkout.stripe.com", { timeout: 90000 }).then(() => "complete"),
        page.getByText(/card was declined|payment failed|declined/i).first().waitFor({ state: "visible", timeout: 90000 }).then(() => "declined"),
      ]).catch(() => "blocked");
      out(result, result === "blocked" ? "no redirect or decline message within 90s" : "");
    }
  }
} catch (e) {
  out("blocked", String(e && e.name ? e.name : "error"));
} finally {
  await browser.close();
}
