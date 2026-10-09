"""Landing pages (listicles, quiz) post their own Meta events to /lp; the tracker
sends the server copy to Core Club with the browser's event id."""
import json
import time

import landing
from test_tracker import client, fresh_db, meta, run_pending  # noqa: F401  (fixtures)

PAGE = "https://fertilityinmen.netlify.app/?utm_source=facebook&ad_id=A1&fbclid=IwAR" + "x" * 60


def post(client, **payload):
    base = {"name": "ReportRead", "id": f"rr_{time.time_ns()}", "ts": int(time.time() * 1000), "url": PAGE,
            "fbp": "fb.1.10.99", "content_name": "SpermFuel+ listicle", "percent": 50}
    base.update(payload)
    return client.post("/lp", content=json.dumps(base),
                       headers={"user-agent": "Mozilla/5.0 (iPhone) FBAN/FBIOS", "x-forwarded-for": "203.0.113.9"})


def test_a_read_reaches_core_club_once_with_the_browsers_event_id(client, meta):
    assert post(client, id="rr_abc").status_code == 204
    run_pending(client)
    assert [p for p, _, _ in meta.calls] == ["1298114545063437"]          # Core Club only, where the browser pair is
    ev = meta.events[0]
    assert ev["event_name"] == "ReportRead" and ev["event_id"] == "rr_abc"
    assert ev["action_source"] == "website" and ev["event_source_url"] == PAGE
    assert ev["custom_data"] == {"content_name": "SpermFuel+ listicle", "percent": 50}
    ud = ev["user_data"]
    assert ud["fbp"] == "fb.1.10.99" and ud["fbc"].endswith(".IwAR" + "x" * 60)  # made from the link's fbclid
    assert ud["client_user_agent"].startswith("Mozilla") and "client_ip_address" in ud


def test_the_cookie_click_wins_and_link_tags_never_go(client, meta):
    post(client, fbc="fb.1.1791179139300.IwRealClick" + "y" * 50)
    post(client, url="https://fertilityinmen.netlify.app/?fbclid=fbY2xjawUwwcxyWJ0")
    run_pending(client)
    assert meta.events[0]["user_data"]["fbc"] == "fb.1.1791179139300.IwRealClick" + "y" * 50
    assert "fbc" not in meta.events[1]["user_data"]


def test_quiz_complete_carries_nothing_but_its_name(client, meta):
    post(client, name="QuizComplete", status="RUNNING ON EMPTY", content_name="LOW DRIVE", percent=80,
         url="https://spermfuel-quiz.netlify.app/?qs=abc")
    run_pending(client)
    ev = meta.events[0]
    assert ev["event_name"] == "QuizComplete" and "custom_data" not in ev
    assert "RUNNING" not in json.dumps(ev) and "LOW DRIVE" not in json.dumps(ev)


def test_test_copies_unknown_events_and_bad_payloads_never_reach_meta(client, meta):
    for url in ("https://localhost/x", "http://127.0.0.1:8080/", "https://fertilityinmen.netlify.app/?jump=3",
                "https://fertilityinmen.netlify.app/?noredirect=1"):
        assert post(client, url=url).status_code in (204, 400)
    assert post(client, name="Purchase").status_code == 400            # the store's own events stay the store's
    assert post(client, name="ViewContent").status_code == 400
    assert post(client, id="").status_code == 400
    assert client.post("/lp", content="not json").status_code == 400
    assert client.post("/lp", content="x" * 20000).status_code == 413
    assert client.options("/lp").status_code == 204
    run_pending(client)
    assert meta.events == []


def test_an_old_or_future_time_is_clamped_to_now():
    now = 1_800_000_000.0
    ev = landing.build_event({"name": "ListicleView", "id": "lv1", "url": PAGE, "ts": 1}, "1.2.3.4", "ua", now)
    assert ev["event_time"] == int(now)
    ev = landing.build_event({"name": "QuizView", "id": "qv1", "url": PAGE, "ts": (now + 3600) * 1000}, "", "", now)
    assert ev["event_time"] == int(now)


def test_beacons_from_the_pages_pass_cors_and_local_previews_are_not_takers(client):
    page = "https://fertility-supplements-ranked.netlify.app"
    for path in ("/lp", "/quiz"):
        r = client.options(path, headers={"origin": page, "access-control-request-method": "POST"})
        assert r.status_code == 204
        assert r.headers["access-control-allow-origin"] == page          # never "*": sendBeacon sends credentials
        assert r.headers["access-control-allow-credentials"] == "true"
    step = json.dumps({"session": "qLocalTest01", "kind": "start", "tz": "Europe/London"})
    assert client.post("/quiz", content=step, headers={"origin": "http://localhost:4782"}).status_code == 204
    assert client.post("/quiz", content=step, headers={"origin": page}).status_code == 204
    import db
    assert [r["session"] for r in db.query("SELECT session FROM quiz_events")] == ["qLocalTest01"]
