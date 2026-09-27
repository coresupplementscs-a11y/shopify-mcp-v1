# Meta tracker (Core Club)

Our own server-side Meta tracking for the Core Supplements Shopify store, replacing WeTracked. It sends store activity to Meta's Conversions API, so the purchases and ROAS in Ads Manager come from this service.

## What it does differently

- **Only real new checkouts count as Purchase.** Automatic subscription renewals (Shopify `source_name` `subscription_contract` / `subscription_contract_checkout_one`) go to Meta as a separate `SubscriptionRenewal` event. In the week of Sep 20–27, 2026, 25 of the 33 orders WeTracked sent as purchases were renewals ($1,457 of $1,944), which inflated ROAS and trained Meta on existing subscribers.
- **Every order is checked.** Orders arrive by webhook, by a poll every 60 seconds, and by a reconciler that re-reads the last 72 hours every 10 minutes. Each one ends up sent or deliberately skipped, and Meta's trace id is stored.
- **Better match data on purchases.** The customer's IP and browser come from the Shopify order itself, and the Meta click id (`fbc`) and browser id (`fbp`) come from our storefront pixel, matched to the order by checkout token.
- **Can't be inflated by fake traffic.** Purchases are only ever created from real Shopify orders, never from the public pixel endpoint.
- **Tells you when something breaks.** `/report` and the MCP tools list problems, such as a silent pixel, failed sends, a broken Shopify token, or orders stuck in the queue. An optional Slack/Discord webhook gets alerts.

## Deploy on Railway

1. In the Railway project that runs the Shopify MCP, click **New → GitHub Repo** and pick this repo.
2. Open the new service's **Settings**. Set **Root Directory** to `tracker`.
3. **Settings → Volumes → Add Volume** and mount it at `/data`. This keeps the tracker's memory across redeploys.
4. **Settings → Networking → Generate Domain.** Copy the URL.
5. **Variables** (the Shopify values are the same as the Shopify MCP service):

| Variable | Value |
|---|---|
| `META_PIXEL_ID` | `1298114545063437` (Core Club) |
| `META_ACCESS_TOKEN` | Conversions API token from Events Manager → Core Club → Settings |
| `SHOPIFY_STORE` | `n3zxxv-01` |
| `SHOPIFY_ACCESS_TOKEN` **or** `SHOPIFY_CLIENT_ID` + `SHOPIFY_CLIENT_SECRET` | same as the Shopify MCP service (needs `read_orders`) |
| `STORE_URL` | `https://getcoresupps.com` |
| `PUBLIC_URL` | the Railway URL from step 4 |
| `ADMIN_TOKEN` | any long random string; protects `/report` and `/mcp` |
| `DATA_DIR` | `/data` |
| `META_TEST_EVENT_CODE` | the code from Events Manager → Test events. Only during the test phase; delete it to go live |

Optional variables:
- `SHOPIFY_WEBHOOK_SECRET`: the app's API secret key, for instant webhooks. Without it, the poller picks orders up within a minute.
- `ALERT_WEBHOOK_URL`: a Slack or Discord incoming webhook for alerts.
- `RENEWAL_EVENT_NAME`: set it empty to stop sending renewals at all.
- `CONTENT_ID_FIELD`: `product_id` (default), `variant_id` or `sku`. It must match your Meta catalog.
- `PURCHASE_VALUE_FIELD`: `total_price` (default) or `subtotal_price`.
- `TRACK_ORDERS_FROM`: see "Orders placed before launch" below.

Check it's running by opening `https://<your-url>/report?key=<ADMIN_TOKEN>`.

## Install the storefront pixel

1. Open `pixel/custom-pixel.js` and replace `https://YOUR-TRACKER.up.railway.app` with your Railway URL, keeping the `/collect` at the end.
2. In Shopify admin, go to **Settings → Customer events → Add custom pixel** and name it `Meta tracker`.
3. Paste the whole file into the code box, click **Save**, then **Connect**.

There's no app install and no theme change. You can disconnect it with one click.

## Connect it to Claude

Add a custom connector in Claude with the URL `https://<your-url>/mcp?key=<ADMIN_TOKEN>`. The tools:
- `tracker_status`: the health report
- `tracker_recent_events`: the latest events sent to Meta, including failures
- `tracker_order`: the delivery status of one order
- `tracker_resend_order`: re-send one order now (same event id, so Meta dedupes it)
- `tracker_send_test_event`: send one test PageView to Events Manager → Test events

## Go-live plan (no double counting)

1. **Test phase.** Deploy with `META_TEST_EVENT_CODE` set and install the pixel. Everything goes to **Events Manager → Test events** only, and WeTracked keeps running untouched. Browse the store, add to cart, start a checkout, and place a real or discounted order. You should see PageView, ViewContent, AddToCart, InitiateCheckout and Purchase in Test events, with match keys listed.
2. **Switch over, all within a few minutes:**
   - In Railway, delete `META_TEST_EVENT_CODE`. The service redeploys.
   - Turn off WeTracked's Meta/Conversions API sending, or uninstall WeTracked.
   - In Shopify → **Sales channels → Facebook & Instagram → Settings**, make sure it isn't also sending events to Core Club. Otherwise Meta would get every purchase twice.
3. **After 24–48 hours:** check `tracker_status`, and look at Events Manager → Core Club → Overview for Event Match Quality. For comparison, WeTracked had Purchase at 8.4, IP/browser on only 67% of purchases, and `fbc` on 83%.

### Orders placed before launch
On its first start, the tracker records the time. It ignores older orders, because WeTracked already reported them, and sending them again would double-count. Orders placed during the test phase went to Test events only, and WeTracked covered them live. To deliberately backfill, set `TRACK_ORDERS_FROM` to an ISO time before the first deploy. `tracker_resend_order` can always send a single order by hand.

## Endpoints

| Path | Access | Purpose |
|---|---|---|
| `POST /collect` | public | storefront pixel events |
| `POST /webhooks/shopify` | Shopify HMAC | `orders/create`, registered automatically when `PUBLIC_URL` and the webhook secret are set |
| `GET /health` | public | Railway health check |
| `GET /report` | `ADMIN_TOKEN` | health report JSON |
| `POST /admin/resend/{order_id}` | `ADMIN_TOKEN` | re-send one order |
| `/mcp` | `ADMIN_TOKEN` | MCP tools for Claude |

## Development

```
pip install -r requirements.txt pytest
python -m pytest tests
```
