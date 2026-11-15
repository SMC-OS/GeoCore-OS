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
      // Stripe-hosted Checkout may render inputs directly or inside frames; search every frame by autocomplete attribute.
      const find = async (selector, timeout = 30000) => {
        const end = Date.now() + timeout;
        while (Date.now() < end) {
          for (const f of page.frames()) {
            const loc = f.locator(selector).first();
            if ((await loc.count()) > 0 && (await loc.isVisible().catch(() => false))) return loc;
          }
          await page.waitForTimeout(500);
        }
        return null;
      };
      const diag = async (why) => {
        const frames = page.frames().length;
        const captcha = (await page.locator('iframe[src*="hcaptcha"], iframe[title*="captcha" i], iframe[src*="recaptcha"]').count()) > 0;
        const labels = await page.evaluate(() => Array.from(document.querySelectorAll("label, button, [role=radio], [role=tab]")).map((e) => (e.innerText || e.getAttribute("aria-label") || "").trim().slice(0, 40)).filter(Boolean).slice(0, 30));
        const autos = await page.evaluate(() => Array.from(document.querySelectorAll("input")).map((e) => e.getAttribute("autocomplete") || e.name || e.type).slice(0, 20));
        await page.screenshot({ path: "qa-stripe-checkout-diag.png", fullPage: true }).catch(() => {});
        out("blocked", `${why}; frames=${frames} captcha=${captcha} labels=${JSON.stringify(labels)} inputs=${JSON.stringify(autos)}`);
      };
      // The newer layout may default to another payment method: choose Card when a selector exists.
      const cardChoice = page.getByRole("radio", { name: /card/i }).first();
      if ((await cardChoice.count()) > 0) await cardChoice.click().catch(() => {});
      const number = await find('input[autocomplete="cc-number"], input[name="cardNumber"], #cardNumber');
      if (!number) {
        await diag("card number field not found");
      } else {
        await number.fill(card);
        const exp = await find('input[autocomplete="cc-exp"], input[name="cardExpiry"], #cardExpiry', 10000);
        const cvc = await find('input[autocomplete="cc-csc"], input[name="cardCvc"], #cardCvc', 10000);
        if (exp) await exp.fill("12 / 34");
        if (cvc) await cvc.fill("123");
        const name = await find('input[autocomplete="cc-name"], input[name="billingName"], #billingName', 3000);
        if (name) await name.fill("QA Acceptance");
        const postcode = await find('input[autocomplete="postal-code"], input[name="billingPostalCode"], #billingPostalCode', 3000);
        if (postcode) await postcode.fill("SW1A 1AA");
        const email = await find('input[autocomplete="email"], input[name="email"], #email', 3000);
        if (email && (await email.inputValue().catch(() => "")) === "" && process.env.EXPECT_EMAIL) await email.fill(process.env.EXPECT_EMAIL);
        await page.locator('button[type="submit"]').first().click();
        const result = await Promise.race([
          page.waitForURL((u) => u.hostname !== "checkout.stripe.com", { timeout: 90000 }).then(() => "complete"),
          page.getByText(/card was declined|payment failed|declined/i).first().waitFor({ state: "visible", timeout: 90000 }).then(() => "declined"),
        ]).catch(() => "blocked");
        if (result === "blocked") await diag("no redirect or decline message within 90s");
        else out(result, "");
      }
    }
  }
} catch (e) {
  out("blocked", String(e && e.name ? e.name : "error"));
} finally {
  await browser.close();
}
