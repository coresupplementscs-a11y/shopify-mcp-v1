# tracker/ — Meta CAPI tracker for Core Supplements

Start by reading `HANDOFF.md` (context, decisions, what is and isn't verified), then `README.md`.

Rules for this folder:
- Purchases come only from Shopify orders, never from `/collect`. Subscription renewals are never sent as Purchase.
- Never commit secrets. `.env` is git-ignored; real values live in Railway variables.
- Run `python -m pytest tests` before every commit; keep every fix covered by a test.
- Meta calls are mocked in tests (`FakeMeta`); verify real delivery in Events Manager → Test events.
- Plain, direct communication with the owner; no compliance disclaimers.
