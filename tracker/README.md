# Meta tracker (Core Club)

Our own server-side Meta tracking for the Core Supplements Shopify store, replacing WeTracked. It sends store activity to Meta's Conversions API, so the purchases and ROAS in Ads Manager come from this service.

## What it does differently

- **Only real new checkouts count as Purchase.** Automatic subscription renewals (Shopify `source_name` `subscription_contract` / `subscription_contract_checkout_one`, or an order Kaching tagged `Kaching Subscription Recurring Order`) go to Meta as a separate `SubscriptionRenewal` event. In the week of Sep 20–27, 2026, 25 of the 33 orders WeTracked sent as purchases were renewals ($1,457 of $1,944), which inflated ROAS and trained Meta on existing subscribers. A subscription's first order (tagged `Kaching Subscription First Order`) is a real new sale and stays a Purchase. The hub decides rebills with the same test (`tracking.is_renewal`), so it never counts a sale Meta was told was a rebill.
- **Every order is checked.** Orders arrive by webhook, by a poll every 60 seconds, and by a reconciler that re-reads the last 72 hours every 10 minutes. Each one ends up sent or deliberately skipped, and Meta's trace id is stored.
- **Better match data on purchases.** The customer's IP and browser come from the Shopify order itself, and the Meta click id (`fbc`) and browser id (`fbp`) come from our storefront pixel, matched to the order by checkout token.
- **One decision per sale: the last ad click.** `attribution.resolve` decides, once, which click a sale is credited to. The Purchase sent to Meta carries that click (`fbc`, stamped with when it really happened) and the order keeps the same record (`orders.attribution`) for the hub, so the two can't disagree. See "How a sale is credited" below.
- **Can't be inflated by fake traffic.** Purchases are only ever created from real Shopify orders, never from the public pixel endpoint.
- **Express checkouts still report a checkout.** A buyer who pays with Shop Pay, Google Pay or Apple Pay from the cart skips the checkout page, so the storefront pixel never sends `InitiateCheckout`. See "Express checkout" below.
- **Tells you when something breaks.** `/report` and the MCP tools list problems, such as a silent pixel, failed sends, a broken Shopify token, or orders stuck in the queue. An optional Slack/Discord webhook gets alerts.

## Deploy on Railway

1. In the Railway project that runs the Shopify MCP, click **New → GitHub Repo** and pick this repo.
2. Open the new service's **Settings**. Set **Root Directory** to the folder that contains this `Dockerfile` (`tracker` when uploaded correctly; if the folder ended up nested, e.g. `tracker/tracker/tracker`, use that path). The build fails if the root directory has no Dockerfile.
3. **Settings → Volumes → Add Volume** and mount it at `/data`. This keeps the tracker's memory across redeploys.
4. **Settings → Networking → Generate Domain.** Copy the URL.
5. **Variables** (the Shopify values are the same as the Shopify MCP service):

| Variable | Value |
|---|---|
| `META_PIXEL_ID` | `1298114545063437` (Core Club) |
| `META_ACCESS_TOKEN` | Conversions API token from Events Manager → Core Club → Settings |
| `SHOPIFY_STORE` | `n3zxxv-01` |
| `SHOPIFY_ACCESS_TOKEN` **or** `SHOPIFY_CLIENT_ID` + `SHOPIFY_CLIENT_SECRET` | same as the Shopify MCP service (needs `read_orders`; if Shopify's visit history needs more, the watchdog's "Shopify visit history" check names the permission Shopify asked for) |
| `STORE_URL` | `https://getcoresupps.com` |
| `PUBLIC_URL` | the Railway URL from step 4 |
| `ADMIN_TOKEN` | any long random string; protects `/report` and `/mcp` |
| `DATA_DIR` | `/data` |
| `META_TEST_EVENT_CODE` | the code from Events Manager → Test events. Only during the test phase; delete it to go live |

Optional variables:
- `SHOPIFY_WEBHOOK_SECRET`: the app's API secret key, for instant webhooks. Without it, the poller picks orders up within a minute.
- `ALERT_WEBHOOK_URL`: a Slack or Discord incoming webhook for alerts.
- `RENEWAL_EVENT_NAME`: set it empty to stop sending renewals at all.
- `RENEWAL_SOURCE_NAMES` / `RENEWAL_TAGS`: what makes an order a rebill. Tags are comma-separated and must match a whole Shopify tag (any case); the default is Kaching's `Kaching Subscription Recurring Order`. Set `RENEWAL_TAGS` empty to ignore tags. If the tag lands a few seconds after the order was created, the poller copies it onto the order before it is sent, as long as it hasn't gone to Meta yet.
- `CONTENT_ID_FIELD`: `product_id` (default), `variant_id` or `sku`. It must match your Meta catalog.
- `PURCHASE_VALUE_FIELD`: `total_price` (default) or `subtotal_price`.
- `TRACK_ORDERS_FROM`: see "Orders placed before launch" below.
- `SKIP_SOURCE_NAMES`: order sources that are never reported (default: draft orders, POS, the merchant's mobile app). Cancelled or voided orders are always skipped.
- `PURCHASE_GRACE_SECONDS` (default 300) and `MATCH_RETRY_SECONDS` (default `60,120`): how long a new sale with no storefront session and no Shopify visit record waits for one, and when it looks again. See "Waiting for the shopper's visit" below. `0` sends at once.
- `JOURNEY_TIMEOUT_SECONDS` (default 6): the most one read of Shopify's visit history may take.
- `META_PIXEL_NAME`, `META_PIXEL_NAME_2` ...: what the hub and the watchdog call each pixel. Without them, Meta's own dataset name is used once it has been read, else the built-in names (`1298114545063437` Core Club, `1717074239276698` Eczema).

Backup pixels: set `META_PIXEL_ID_2` and `META_ACCESS_TOKEN_2` (then `_3`, `_4` ...) to send a copy of every event to another dataset. Each needs its own Conversions API token from that dataset's Settings. A backup only gets orders placed after it was first configured, so orders another tracker already sent it are not counted twice. `/report` lists each backup under `backup_pixels`.

Check it's running by opening `https://<your-url>/report?key=<ADMIN_TOKEN>` (the key is scrubbed from the server log; prefer an `Authorization: Bearer` header from scripts).

## Install the storefront pixel

1. Open `pixel/custom-pixel.js` and replace `https://YOUR-TRACKER.up.railway.app` with your Railway URL, keeping the `/collect` at the end.
2. In Shopify admin, go to **Settings → Customer events → Add custom pixel** and name it `Meta tracker`.
3. Paste the whole file into the code box. Under the pixel's settings choose **Permission: Not required** (unless the store shows a consent banner) and **Data sale: Does not qualify as data sale**. Click **Save**, then **Connect**.

There's no app install and no theme change. You can disconnect it with one click.

The pixel also sends the page that referred the shopper (the tracker keeps its host only, like `fertilityinmen.netlify.app`), so the watchdog can name a landing page that drops the ad ids. A pixel pasted before Sep 27, 2026 doesn't send it: paste the file again to get it.

## How a sale is credited

Every storefront arrival from a Meta ad (its parameters or an `fbclid`) becomes that browser's current click; the newest always wins. Its `fbc` is stamped with the moment that `fbclid` first reached the store, so a reload of the same link is the same click, and an older `_fbc` cookie never replaces a newer click. Meta gives every ad click its own `fbclid`, so an older ad link opened again (a restored tab, the back button) is that old click coming back: it keeps the moment it first arrived and never takes over from a newer click, even when the pixel re-stamps its cookie with it. The arrival also joins the browser's click history (its last 20 ad arrivals, `HISTORY_MAX`). An `lp` parameter (the landing page the shopper came through, like `lp=listicle-v2-one-line`) is kept on the click, its history entry and the sale. Utm tags with no ad, ad set or campaign id and no `lp` came through the old listicle, which stripped the ids: those are marked `lp: listicle`, `ids_stripped: true`.

When a new sale comes in, `attribution.resolve` looks at every click it can find and credits the newest one inside `ATTRIBUTION_WINDOW_DAYS`:
1. the buyer's storefront session (matched by checkout token), with its click history,
2. Shopify's record of the buyer's last visit (`customerJourneySummary.lastVisit`: when, the landing page with its ad ids and `fbclid`, the utm tags),
3. the old tracker's `note_attributes` (WeTracked wrote the last click's `fbc`, with its real time, and utm names),
4. the order's `landing_site`, only when nothing else exists. Shopify keeps the buyer's **first** landing page for about two weeks, so it is the first touch, not the visit that led to the purchase. When its real time is known (Shopify's first visit, the pixel saw that `fbclid` arrive, or the old tracker's note or Shopify's last visit carries the same `fbclid`), that time is used and the window applies. When it isn't, the sale is still sent with it (the conversion signal matters) and marked `first_visit_unverified`, but only when no known click came before the sale: the first landing page came before any such click, so a click older than the window proves the landing page is too. Then it is not a Meta sale, and Meta gets that older click with its real time, never the landing page stamped with the order time.

The first-visit ad is kept as `first_touch` and listed as an assist when it isn't the ad that sold. The Purchase's `event_id` stays `order_<id>`.

Shopify cuts `landing_site` at 255 characters, and the `fbclid` is its last parameter, so a long ad link keeps only the start of it (order #c3711 kept `IwZXh0bgNhZW0BMABwZ`). Those 19 characters are only the header every `fbclid` starts with, not the click itself. A cut `fbclid` counts as the same click as a longer one it begins only when the landing page is at Shopify's limit and ends with it, it is longer than the 17 characters many clicks share (`IwZXh0bgNhZW0BMAB`), and the two records don't name two different ads (by ad id, else by their link's names either way round). Shorter than 40 characters, it also needs both moments known (Shopify's first visit dates the landing page) and within 30 minutes; a longer cut carries the click's own characters, and only a known gap of over 30 minutes rules it out. Then the landing page names the ad of a click the browser kept only as its `_fbc` cookie, and Meta gets the whole click id. A record whose own names find its ad in Meta's catalog never takes another record's ad. Resolver v4.

Names to ids: the live URL templates put the ad set in `utm_content` and the ad in `utm_term`, but older links did it the other way round, so names are never trusted for which is which. When a click has names but no ad id, the tracker looks them up in the ads Meta reported in the window (and the ads it has names for by id), inside the link's campaign (`utm_id`, else the campaign name), trying ad set = `utm_content` with ad = `utm_term` and the swap. It takes the ad id only when exactly one ad fits; otherwise the names are kept with `ad_id` empty and `ambiguous: true`.

A sale no Meta click got gets a channel instead: `Shop app ads` (`utm_source=shop_campaigns`), `Google`, `TikTok`, `Email or SMS` (Klaviyo, Postscript, Attentive, email, sms), `Other referral (<site>)`, else `Direct`. Meta sales have the channel `Meta ads`.

The stored record, `orders.attribution`: `source` (`browser`, `shopify_last_visit`, `order_note`, `first_visit`, `first_visit_unverified` or `click_id`), `ad_id`, `adset_id`, `campaign_id`, `ad_name`, `adset_name`, `campaign_name`, `click_at`, `lp`, `ids_stripped`, `first_touch`, `assists`, `channel`, `ambiguous`, plus `meta`, `click` and the resolver version `v`. A record whose ad was named after it was sent also has `identity_refreshed: true` (see "Naming a click that was sent without its ad").

Shopify's visit history is read with the tracker's existing Shopify access. A missing permission, a throttled or slow answer, or no record for the order never hold a Purchase up: the problem is logged once and the watchdog shows it.

**Waiting for the shopper's visit.** A new sale that matches no storefront session and has no Shopify visit record yet waits: matching is tried again about 1, 2 and 5 minutes after the order was placed, then it goes out with the best data found. The wait is saved on the order row, so a restart doesn't lose it. Sales that already match go out at once, except a browser that matched with no ad click, which leaves only a first landing page with no known time: Shopify builds its visit record after the order, so that sale waits the same way. An order found late (older than 5 minutes) never waits. Every order still ends up sent or skipped, well inside Meta's 7 days.

**Backfill.** On the first start of a new resolver version, the tracker re-decides the stored credit of the last 7 days' new sales (sent or skipped) and logs how many it changed. It never sends or resends anything to Meta: a Purchase can't be corrected after the fact, and a resend under a new id would count twice. Records the tracker sent are never rewritten, whatever resolver version wrote them: they keep the `fbc` Meta got, and a retry to a backup pixel repeats that same click. Records this resolver already wrote are left alone too.

**Naming a click that was sent without its ad.** Sometimes the buyer's browser kept only its `_fbc` cookie and Shopify cut the landing page's `fbclid` (order #c3711): the Purchase went out with the right click, but the stored record says `source: click_id` with no ad id and no ad name. The tracker then looks again at Shopify's record of the buyer's last visit (`lastVisit.landingPage`) and the order's `landing_site`. When one of them is provably the same click as the `fbc` Meta was sent (the very same `fbclid`, or a landing page Shopify cut short that passes the strict same-click rules above), it copies only the ad's `ad_id`, `adset_id`, `campaign_id`, `ad_name`, `adset_name`, `campaign_name`, `lp` and `ids_stripped` into the record and sets `identity_refreshed: true`. The `fbc`, `source`, `click_at` and everything else Meta got stay exactly as they were, and nothing is sent or resent. It runs at every start (for the last 7 days of sent sales; a named record is never touched again) and when the hub shows the order (at most once an hour per order while it stays unnamed). A sale whose ad still can't be named shows as "Meta ad (name unknown)".

**Express checkout.** Shop Pay, Google Pay and Apple Pay from the cart skip Shopify's checkout page, so the storefront pixel never sees `checkout_started` and Meta would get a Purchase with no `InitiateCheckout` before it. When a new sale goes to Meta as a Purchase and the pixel reported no checkout for the buyer's browser (matched by checkout token or client id, from an hour before the order on), or no browser matched at all, the tracker also sends one server `InitiateCheckout` to each pixel, right before the Purchase in the same send:
- `event_id` `checkout_<checkout_token>` (`checkout_order_<order id>` when the order has no checkout token), `event_time` the order's `created_at` less 1 second, `action_source` `website`, `event_source_url` the store URL;
- the same `user_data` as the Purchase, and `custom_data` with the order's `value`, `currency`, `content_ids`, `contents` and `num_items`.

Never for MRR, test orders, orders placed before the tracking start or skipped orders, and only when the Purchase itself goes out as a website event (Meta needs the buyer's browser for one). Each pixel gets it at most once: it is recorded in `events` under the order like other server events, and a retry or a resend (even one that repeats the Purchase) never sends it again. A failed one is recorded as failed and never holds up the Purchase. The Purchase is unchanged. The order's type, its pixel ticks in the hub and the resend suggestions go by the Purchase alone, and the funnel only counts the storefront pixel's own events, so the buyer (already counted at every step) isn't counted twice.

## The hub

Open `https://<your-url>/hub` and log in with `ADMIN_TOKEN`. The page is black and white (Inter from Google Fonts, the only thing it loads from elsewhere; colour only on status dots and real failures) and works on a phone. Top to bottom:
- A header with a status pill (All good / Needs a look / Broken) that jumps to Tracking health.
- **Waiting for your OK**, only while the watchdog has suggestions pending: Approve or Dismiss each one ("Got it" for heads-ups), with the result shown in place.
- **Profit and loss**: the P&L app's own numbers and code (see below), with its own chips: Today, Yesterday, 7D, 30D, MTD, All.
- The hub's range tabs (Today, Yesterday, 7 days, 30 days), which drive the four sections under them: the **Shopper funnel** (five step cards with a Meta ads / Not from Meta / All switch, and product page vs listicle), **Creatives that sold**, **Assists** and **Orders**.
- **Tracking health** (the watchdog, which re-checks every link of the chain every 5 minutes), **Match quality** per pixel, the order feed with a Resend button, and Tools.

Spend, ROAS and creatives need `META_ADS_TOKEN` (a token with `ads_read`, e.g. the P&L's) and `META_AD_ACCOUNT_IDS`. Store-confirmed creatives need URL parameters on the ads (the hub shows the exact line to paste in Ads Manager).

Profit belongs to the P&L app. The top section shows the P&L's own numbers, run through a copy of the P&L page's own code (`hub_page.py`, marked where it starts and ends), so the two never disagree: the hero (net profit, margin, days, per day; revenue, COGS, ad spend, fees) and the cards (Revenue, Net Profit, Meta Spend with ROAS (new) and CAC, Orders, MRR collecting, MRR at risk). The hub adds **Expenses** (hero revenue less net profit: COGS, ad spend, fees with the fee true-up, chargebacks, Shopify bills and software, listed under it) and an **MRR net** card (the range's MRR revenue less its COGS). Labels say MRR where the P&L says recurring; blended ROAS isn't shown. `GET /hub/api/pnl?from=YYYY-MM-DD&to=YYYY-MM-DD` (today in New York by default; `from=2000-01-01` is the P&L's "All") reads `PNL_URL`: its `/api/pnl` for the range (cached a minute, trimmed to the store, product and day totals that code reads; no orders, no customers), the owner's own lines (`/api/state`, cached a minute) and the software list on its page (`SW_TOOLS` and `DAYS_PER_MONTH`, cached 10 minutes; a copy in `pnl.py` stands in when the page can't be read, marked `sw_tools_source: copy`). `PNL_API_KEY`, when set, is sent as a Bearer token. A P&L that doesn't answer (30 s) shows "The P&L server did not answer", never zeros. Like the P&L page on load, when the range includes today and the P&L's Meta spend is over 15 minutes old (and Meta isn't rate limiting it), the hub asks the P&L to sync Meta for that range, in the background, at most once per 15 minutes.

Everything else in the hub shows what the ads sold. Subscription rebills are called **MRR** on the page (the API keeps the value `rebill`); they never count as ad sales.

The ROAS strip at the top of Creatives that sold:
- **Product ROAS** (the headline): new-sale revenue of the products you are advertising / all ad spend in the range. MRR is left out. A mixed order counts in proportion: its total x (advertised line items' price x quantity / all line items' price x quantity).
- **Ad ROAS**: new sales the store traced to a Meta ad click / ad spend (the creatives table's store-confirmed sales).
- **Meta ROAS**: what Ads Manager reports.
- Then the range's ad spend, Meta sales (with the click/view split) and store sales. Without an ads connection the section shows the steps to connect it. (`/hub/api/overview` still returns the older cards and 7-day series; the page no longer shows them.)

Which products are "advertised" needs no setup. The advertised products for a range are the products sold by the campaigns with spend in it. A campaign's products are learned from the new sales the store tied to it (the sale's campaign id, else its campaign name, else its ad's campaign) in the last 30 store days. Only each sale's main line counts: the line with the biggest price x quantity, and only when it is the main line of at least a quarter of the campaign's sales. So cheaper add-ons in the cart (Shipping Protection, an extra bottle) don't make a product advertised, and neither does a pricier product one buyer happened to add. A sale whose lines carry no prices teaches every product in it. A campaign with no such sale is matched by name: a word of 3 or more letters from its name found inside a product title from the last 30 days of orders, both reduced to lowercase letters and digits (`sperm` finds `SpermFuel+`). A number on its own (a budget, a date, a year, like `250` in "CBO 250") is never used; words with a digit in them (`b12`, `5htp`) are. Words campaigns use for their setup (`test`, `cbo`, `asc`, `broad`, `tof` and similar) are skipped, so "Test - SpermFuel" can't also claim a "Testosterone" product. Free line items (a price of 0, like gifts) never make a product advertised. A campaign still unmatched keeps its spend in Product ROAS (the overview API names them in `unmapped_campaigns`).

Ads are named by Meta. Wherever the hub shows an ad it has an id for (the order feed, assisted sales, creatives with sales but no delivery in the range), it asks Meta for that ad's current ad, ad set and campaign names, 50 ids per call, cached a day per id. Each call waits at most 8 seconds. A timeout, a rate limit or any other refusal stops the lookup at its first call, and those ids are asked again 10 minutes later, so a slow or throttled Meta never holds up the orders, creatives and assists sections for long. The names in the ad's link are only a fallback: links don't agree on which of `utm_content` and `utm_term` holds the ad, so the creatives table tries both.

Every section reads the sale's stored record (see "How a sale is credited"). An order the tracker has on record but never credited (mostly orders skipped as placed before go-live) is decided by the same resolver the first time the hub shows it, and stored. An order the tracker never saw is decided the same way from what the order itself says, without storing anything.

The order feed:
- Each order carries a `channel`: `Meta ads` for Meta sales, else where the sale came from (`Shop app ads`, `Google`, `TikTok`, `Email or SMS`, `Other referral (<site>)`, `Direct`), so a sale no ad got doesn't just say "No ad".
- Orders placed before go-live have the type `before_go_live` and read "Before go-live, WeTracked sent this", whatever the tracker stored for them: an order it sent during test mode only reached Test Events, so its pixels don't show as sent. The one exception is an order the tracker sent live after go-live (Claude's resend tool), which keeps its sent label. They still count as sales on the cards, but none of them can be resent from the hub: WeTracked sent them under its own event ids, so Meta would count them twice.
- An order a pixel already accepted keeps the type it was sent as: approving an MRR tag later never turns a sale Meta got as a Purchase into MRR here.
- Pixels are shown by name: Meta's dataset name once read, else Core Club and Eczema (or `META_PIXEL_NAME` / `META_PIXEL_NAME_<n>`).

How the creatives table credits sales:
- **Last click with assists** (like Triple Whale). A sale goes to the last Meta ad the buyer clicked within `ATTRIBUTION_WINDOW_DAYS` (the stored decision above). The pixel also keeps each browser's last 20 Meta ad arrivals (`HISTORY_MAX`; a repeat of the same ad within 30 minutes counts once). Every other ad clicked earlier in the window (up to 19, `ASSISTS_MAX`: all of a full click history but the ad that sold), and the first-visit ad when it isn't the one that sold, are stored on the sale as `assists`, newest first. Nothing else trims them: the order feed, the creatives table and the Assists section list them all. The Assists column counts the new sales each ad helped; assists never add to sales or revenue. The order feed reads "Sold by <ad> · assisted by <ad>, <ad>".
- **Meta sales, click vs view.** The insights read asks Meta for the `7d_click` and `1d_view` windows, so each ad's Meta sales show as "N (C click, V view)". Ads where Meta counts more view sales than click sales get a "mostly view" tag. The Meta total itself is unchanged. When Meta gives no split for an ad, the total shows alone.
- **An ad gets a row when it spent `HUB_MIN_AD_SPEND` (default $15) in the range, or had any sale (store-confirmed or Meta's) or a Meta add to cart.** The rest are summed in one `small` line ("+N other ads under $15 with no sales or add to carts: $X spend"): each listed ad set has one for its own small ads, and each campaign one for its ad sets where no ad spent that much (those ad sets aren't listed). Every total still counts every ad, so rows plus lines add up. When ad spend couldn't all be read, every ad keeps its row (`min_ad_spend: null`).
- **Via listicle.** Each ad's `via_listicle` counts its store sales whose click came through a listicle (the stored record's `lp`, or `ids_stripped`).

**Assisted sales** (`GET /hub/api/assists?range=`): one row per ad that assisted at least one new sale, most assists first, then by spend. Each row has the assisting ad (its ad set and campaign, its name), its Meta spend in the range (`null` when ad spend isn't connected), how many new sales it assisted, and the ads that got those sales (`closers`: each one's ad set, name, how many of them it closed and their value; the page calls them "Creatives that got the sale", and an ad with no id and no name is "Meta ad (name unknown)"). There is no cap on the rows or the closers. A sale counts once per assisting ad, the ad that got a sale is never its own assist, and MRR never counts. The footer counts the ad sales that had no earlier ad click. Click history started on Sep 27, 2026 at 6:25 PM New York time (`ASSISTS_FROM` in `hub.py`), so there are no assists before that. The footer only counts sales whose click history is on record: placed after that moment and credited by the tracker itself (a sale it hasn't handled yet is decided from what the order itself says, so its earlier clicks are unknown). When the range starts before click history did, the footer says so: "N sales since Sep 27, 6:25 PM had no earlier ad click."

**Watchdog checks** added with the one-decision release (the older ones stay):
- **Ad tags without an ad ID** (warn): shoppers in the last 24 hours who arrived from a Meta ad with utm tags but no ad id and no `lp`, naming the site that sent them when the pixel knows it.
- **Shopify visit history** (ok or warn): whether the tracker can read Shopify's record of each buyer's visits, and in plain words why not. When no sale asked in the last 6 hours, it asks about the newest order on record.
- **Sales credited from a first visit only** (warn above 20%): the share of the week's new sales sent to Meta that were credited from the buyer's first landing page alone, because no later click was seen.
- **Meta vs store sales per ad** (information only, needs ad spend connected): on how many ads today Meta's purchase count differs from the sales the store confirmed for that ad.

**Suggestions** (`GET /hub/api/proposals`; approve with `POST /hub/api/proposals/{id}/approve`, or `/dismiss`, both needing the `X-Hub-Request: 1` header like every hub action). The watchdog suggests fixes, each once, and nothing happens until the owner approves:
- **Send order #X to Meta**: an order placed after go-live that a pixel hasn't accepted after 3 tries (or in 30 minutes), or that has waited over 30 minutes. When some pixels already have it, the suggestion names the ones that don't ("Send order #X to Eczema (backup)"). Approving sends it now to those pixels only, never again to one that has it; when every pixel has it, nothing is sent ("Already sent"). It closes itself when the retries get it through, or when the order becomes skipped on purpose. Never suggested for orders placed before go-live.
- **Treat orders tagged T as MRR?**: a tag with "recurring", "rebill" or "renewal" in it that isn't a rebill tag yet, on orders counted as new sales. Approving adds it to the rebill tags (kept in the database, on top of `RENEWAL_TAGS`), for future orders only: an order any pixel already has stays what it was sent as, in Meta and in the hub, and a backup pixel still missing it gets the same Purchase.
- **A landing page is dropping the ad IDs**: a heads-up only; "Got it" marks it seen.

**Funnel.** Each browser counts once, at the furthest step it reached in the range and at every step before it, so no step is ever more than the one above. Purchases are the browsers tied to a new order by its checkout token; a buyer counts at every step, even when the pixel missed its visit in the range. Sales no known browser placed are counted apart (`untied_sales`, "N more sales we could not tie to a browser"). Meta ads / Not from Meta is decided once per browser; the API returns `meta`, `other` and `all` (the two added up) and `groups`, their labels ("Meta ads", "Not from Meta", "All") with the hover tip for Not from Meta: shoppers who did not come from a Meta ad in the `ATTRIBUTION_WINDOW_DAYS` days before (typed the site in, Google, email, the Shop app, returning customers). The page shows one group at a time as five step cards (Visitors, Product views, Add to cart, Checkout, Purchases), the share of the step above on the arrow between two cards and "N% of visitors bought" under them; its switch (Meta ads by default) is kept in the URL hash. One row from 1024px wide, three or two to a row below that. `counting_since` names the pixel's oldest storefront event on record when the range starts before it. The `listicle` block splits shoppers from Meta ads: "Product page" first, then "Listicle", with visitors (by where their ad click landed), sales and revenue (new sales credited to a Meta ad, by the stored `lp` and `ids_stripped`) and conversion rate (sales over visitors). The order feed marks those sales with `listicle: true`.

## Connect it to Claude

Add a custom connector in Claude with the URL `https://<your-url>/mcp?key=<ADMIN_TOKEN>`. The tools:
- `tracker_status`: the health report
- `tracker_recent_events`: the latest events sent to Meta, including failures
- `tracker_order`: the delivery status of one order
- `tracker_resend_order`: re-send one order now (same event id, so Meta dedupes it). Every pixel gets it once more; if one fails, the retries after it go only to the pixels that haven't accepted it since, never again to one that has. It still sends an order placed before go-live, but its reply carries a `warning`: WeTracked sent that one under its own event id, so Meta will count it twice.
- `tracker_send_test_event`: send one test PageView to Events Manager → Test events

## Go-live plan (no double counting)

1. **Test phase.** Deploy with `META_TEST_EVENT_CODE` set and install the pixel. Everything goes to **Events Manager → Test events** only, and WeTracked keeps running untouched. Browse the store, add to cart, start a checkout, and place a real or discounted order. You should see PageView, ViewContent, AddToCart, InitiateCheckout and Purchase in Test events, with match keys listed.
2. **Switch over, all within a few minutes:**
   - In Railway, delete `META_TEST_EVENT_CODE`. The service redeploys.
   - Turn off WeTracked's Meta/Conversions API sending, or uninstall WeTracked.
   - In Shopify → **Sales channels → Facebook & Instagram → Settings**, make sure it isn't also sending events to Core Club. Otherwise Meta would get every purchase twice.
3. **After 24–48 hours:** check `tracker_status`, and look at Events Manager → Core Club → Overview for Event Match Quality. For comparison, WeTracked had Purchase at 8.4, IP/browser on only 67% of purchases, and `fbc` on 83%.

### Orders placed before launch
On its first start, the tracker records the time and ignores older orders, because WeTracked already reported them. When it switches from test mode to live (you delete `META_TEST_EVENT_CODE`), it moves that start forward again, so orders placed during the test phase are not sent twice. To deliberately backfill, set `TRACK_ORDERS_FROM` to an ISO time; it is authoritative whenever set. `tracker_resend_order` can always send a single order by hand, even one from before the start, but Meta only dedupes repeats of our own event id: WeTracked's copy of an order placed before go-live has a different one, so that order is counted twice (the tool's reply warns). The hub's Resend button refuses those orders.

## Backup

`GET /admin/backup` returns the whole database as one SQLite file, taken with SQLite's backup API so it is a consistent snapshot, named `tracker-YYYYMMDD-HHMM.db` (store time). It only accepts `ADMIN_TOKEN` in an `Authorization: Bearer` header: not `?key=` (URLs end up in logs and browser history) and not the hub's login cookie.

```
curl -H "Authorization: Bearer $ADMIN_TOKEN" -o tracker.db https://<your-url>/admin/backup
```

## Endpoints

| Path | Access | Purpose |
|---|---|---|
| `POST /collect` | public | storefront pixel events |
| `POST /webhooks/shopify` | Shopify HMAC | `orders/create`, registered automatically when `PUBLIC_URL` and the webhook secret are set |
| `GET /health` | public | Railway health check |
| `GET /report` | `ADMIN_TOKEN` | health report JSON |
| `POST /admin/resend/{order_id}` | `ADMIN_TOKEN` | re-send one order |
| `GET /admin/backup` | `ADMIN_TOKEN` (Bearer header only) | a consistent snapshot of the database |
| `/mcp` | `ADMIN_TOKEN` | MCP tools for Claude |
| `GET /hub/api/pnl` | hub login | the P&L app's numbers for a range |
| `GET /hub/api/proposals` | hub login | the watchdog's suggestions |
| `POST /hub/api/proposals/{id}/approve` or `/dismiss` | hub login + `X-Hub-Request: 1` | act on one |

## Development

```
pip install -r requirements.txt pytest
python -m pytest tests
```
