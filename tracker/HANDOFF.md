# Handoff: Core Club Meta tracker

Read this first when picking the project up in a new Claude Code session.

## What this is
A self-hosted replacement for the WeTracked app. It sends Core Supplements storefront and order events to Meta's Conversions API (dataset **Core Club**, id `1298114545063437`), so Ads Manager purchases and ROAS come from our own tracking. Full design and setup: `README.md`. Every setting: `env.example`.

## Facts
- Store: Core Supplements, getcoresupps.com, myshopify `n3zxxv-01`, USD, America/New_York.
- Core Club is shared with ad accounts Core, Core Supplements 2, QC1 and **leggings** (1537379363921450), so the leggings account sees our purchases.
- Hosting: Railway, a second service in the project that runs the Shopify MCP (`server.py`, repo `coresupplementscs-a11y/shopify-mcp-v1`). Needs a volume mounted at `/data`.
- Secrets live only in Railway variables (or a local `.env`, which is git-ignored). Never commit them.

## Why WeTracked "wasn't working" (Sep 20–27, 2026)
33 Shopify orders, 33 Purchases sent to Meta, but 25 of them ($1,457 of $1,944) were automatic subscription rebills (`source_name` `subscription_contract_checkout_one`, billed ~12:01 PT daily) carrying the original click's fbc. Only 8 were new checkouts. Ads Manager ROAS was inflated and Meta optimised toward existing subscribers. This tracker sends renewals as `SubscriptionRenewal` (action_source system_generated, no click ids) and only real checkouts as `Purchase`.
Baseline match quality on Core Club: Purchase 8.4 (IP/UA on 67%, fbc 83%, phone 50%); PageView/ViewContent/AddToCart 6.1.

## State of the code
Built and reviewed. A 132-agent adversarial review (6 reviewers, 2–3 judges per finding, a completeness critic) produced 46 confirmed findings; all are fixed and covered by the 37 tests in `tests/`. Highlights of what changed:
- Failed orders are never stranded (no attempt cap; backoff + 7-day age is the limit).
- Email-only session matches require Shopify's own browser_ip/user_agent to agree (stops attribution hijack via the public pixel endpoint).
- Client IP comes from the trusted proxy hop; bodies are size-capped before reading; non-object payloads give 400.
- The pixel keys sessions on Shopify's `clientId`, not the `_fbp` cookie Safari evicts.
- fbc is rebuilt from the order's `landing_site` `?fbclid=` when the pixel missed it.
- Cancelled/voided orders, draft/POS/merchant-app orders are skipped, not reported; action_source is derived (`website` / `physical_store` / `other`).
- Test→live switch moves the tracking start forward so test-phase orders don't double count; `TRACK_ORDERS_FROM` is authoritative when set.
- Manual resend really resends (Meta dedupes) and keeps retrying under force.
- Retention loop prunes old rows; recovered failures aren't reported as losses; admin key is scrubbed from logs; storage-not-on-volume is reported.
- Meta hashing keeps accented letters; phone numbers go through libphonenumber (E.164).

## What has NOT been verified
Only live traffic can prove: the custom pixel on the real storefront (cookie writes, checkout token match) and real Meta acceptance (this sandbox could not reach graph.facebook.com; tests use a mocked Meta). Follow the go-live plan in the README: test mode first, watch Events Manager → Test events, then switch.

## Next steps
1. Get this `tracker/` folder onto the Core repo at `tracker/` (there is a mis-nested `tracker/tracker/tracker` copy from an earlier upload — delete it).
2. Deploy on Railway (README → "Deploy on Railway"), variables from `env.example`, `META_TEST_EVENT_CODE` set.
3. Paste `pixel/custom-pixel.js` into Shopify → Settings → Customer events (set its permission to "Not required" unless the store uses a consent banner).
4. Place a test order; confirm PageView/ViewContent/AddToCart/InitiateCheckout/Purchase in Test events with fbc/fbp/em/ph present.
5. Go live: delete `META_TEST_EVENT_CODE`, turn off WeTracked and the Facebook & Instagram channel's event sharing the same minute.
6. Add the MCP connector (`/mcp?key=ADMIN_TOKEN`) to Claude and ask `tracker_status` daily for the first week.
