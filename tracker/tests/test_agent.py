"""The hub's agent: a fake Anthropic client stands in for Claude, so these
tests check the loop, the tools, the budget and the safety rails without any
network call."""
import asyncio
import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

import agent
import config
import db
from test_hub import API, POST, PII, ad_rows, client, fresh, meta, seed, shop  # noqa: F401  (fixtures)


class Block(SimpleNamespace):
    def to_dict(self):
        return dict(vars(self))


def text(t):
    return Block(type="text", text=t)


def tool(name, args, id_="tu_1"):
    return Block(type="tool_use", id=id_, name=name, input=args)


def reply(stop, *content, inp=1000, out=200):
    return SimpleNamespace(stop_reason=stop, content=list(content),
                           usage=SimpleNamespace(input_tokens=inp, output_tokens=out, cache_read_input_tokens=0,
                                                 cache_creation_input_tokens=0))


class FakeClaude:
    """Answers each create() with the next scripted reply and records what it was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    async def create(self, **kw):
        self.calls.append(json.loads(json.dumps(kw, default=lambda o: o.to_dict() if hasattr(o, "to_dict") else str(o))))
        return self.replies.pop(0)


@pytest.fixture
def claude(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(config, "AGENT_DAILY_CAP_USD", 3.0)

    def use(*replies):
        fake = FakeClaude(*replies)
        monkeypatch.setattr(agent, "_client", fake)
        return fake
    return use


def ask(client, question, chat_id=""):
    return client.post("/hub/api/agent", headers=POST, json={"chat_id": chat_id, "question": question}).json()


def today():
    return agent.hub._today().isoformat()


def test_the_agent_answers_from_its_tools_and_keeps_the_whole_chat(client, shop, claude):
    seed(shop)
    fake = claude(reply("tool_use", text("Checking."), tool("orders", {"since": today(), "until": today()})),
                  reply("end_turn", text("4 sales today. **#c104** was the biggest.")))
    body = ask(client, "what got sales today?")
    assert body["error"] == "" and body["answer"].startswith("4 sales today") and body["checked"] == ["Orders"]
    # The model got the store's time, the tools, the system rules, and the fallback opt-in.
    first = fake.calls[0]
    assert first["model"] == config.AGENT_MODEL and first["fallbacks"] == "default"
    assert first["betas"] == [agent.FALLBACK_BETA] and first["output_config"] == {"effort": "medium"}
    assert "New York time" in first["messages"][0]["content"][0]["text"]
    assert {t["name"] for t in first["tools"]} == set(agent.RUNNERS)
    # The tool's answer went back as a tool_result, with the orders and no customer details.
    result = fake.calls[1]["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "tu_1" and result["is_error"] is False
    assert "#c10" in result["content"] and not any(p in result["content"] for p in PII)
    # The whole chat is stored and only appended to: the next question carries every earlier block unchanged.
    stored = db.agent_chat(body["chat_id"])
    assert [m["role"] for m in stored] == ["user", "assistant", "user", "assistant"]
    fake2 = claude(reply("end_turn", text("Yesterday had none.")))
    again = ask(client, "and yesterday?", body["chat_id"])
    assert again["chat_id"] == body["chat_id"]
    assert fake2.calls[0]["messages"][:4] == stored and len(fake2.calls[0]["messages"]) == 5


def test_every_tool_runs_against_the_hub_and_never_returns_customer_details(client, shop, meta):
    seed(shop)
    for name in agent.RUNNERS:
        args = {} if name == "tracking_health" else {"since": today(), "until": today()}
        out, is_error = asyncio.run(agent.run_tool(name, args))
        assert not is_error, (name, out)
        assert not any(p in out for p in PII), name
        json.loads(out)
    # Dates outside the last 30 days, or the wrong way round, come back as a clear error.
    out, is_error = asyncio.run(agent.run_tool("orders", {"since": "2020-01-01", "until": today()}))
    assert is_error and "YYYY-MM-DD" in json.loads(out)["error"]
    assert asyncio.run(agent.run_tool("orders", {"since": today(), "until": "2020-01-01"}))[1] is True
    assert asyncio.run(agent.run_tool("nope", {}))[1] is True


def test_ad_performance_lists_every_ad_not_just_the_sellers(client, shop, meta, monkeypatch):
    seed(shop)
    monkeypatch.setattr(config, "META_AD_ACCOUNT_IDS", ["123"])
    meta.ad_rows = ad_rows()
    out = json.loads(asyncio.run(agent.run_tool("ad_performance", {"since": today(), "until": today()}))[0])
    ads = {a["ad_id"]: a for a in out["ads"]}
    assert "AD9" in ads and ads["AD9"]["store_sales"] == 0              # spent, no sale: still listed for the agent
    assert out["ads"] == sorted(out["ads"], key=lambda a: -(a["spend"] or 0))
    assert ads["AD1"]["ctr"] is not None and "cpc" in ads["AD1"]
    # The page's table still shows only the sellers.
    page = client.get("/hub/api/creatives?range=today", headers=API).json()
    shown = {a["ad_id"] for c in page["campaigns"] for g in c["groups"] for a in g["ads"]}
    assert "AD9" not in shown


def test_the_daily_budget_stops_the_agent(client, claude):
    claude(reply("end_turn", text("ok")))
    db.add_agent_spend(agent._store_day(), 3.0)
    body = ask(client, "what spent the most?")
    assert "budget" in body["error"] and "$3.00" in body["error"]
    # Spend is counted from the tokens each reply used.
    assert agent.cost(SimpleNamespace(input_tokens=1_000_000, output_tokens=100_000, cache_read_input_tokens=0,
                                      cache_creation_input_tokens=0), "claude-sonnet-5-5") == pytest.approx(3.0)


def test_without_a_key_the_agent_says_how_to_switch_it_on(client, monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    assert "ANTHROPIC_API_KEY" in ask(client, "hi")["error"]
    st = client.get("/hub/api/agent/status", headers=API).json()
    assert st["configured"] is False and st["cap"] == config.AGENT_DAILY_CAP_USD


def test_a_declined_question_gets_the_one_line_answer(client, claude):
    claude(reply("refusal"))
    assert ask(client, "write me a poem")["answer"] == agent.OFF_TOPIC
    assert "I only answer questions about your store and your ads." in agent.SYSTEM


def test_the_agent_needs_the_hub_login_and_a_hub_post(client, claude):
    claude(reply("end_turn", text("ok")))
    assert client.post("/hub/api/agent", json={"question": "hi"}).status_code == 401
    assert client.post("/hub/api/agent", headers=API, json={"question": "hi"}).status_code == 403
    assert client.get("/hub/api/agent/status").status_code == 401


def test_a_bad_key_reads_plainly(client, claude):
    async def boom(**kw):
        raise anthropic.AuthenticationError(
            "bad key", response=httpx.Response(401, request=httpx.Request("POST", "https://api.anthropic.com")),
            body=None)
    fake = claude()
    fake.beta.messages.create = boom
    assert "ANTHROPIC_API_KEY" in ask(client, "hi")["error"]


def test_the_pnl_tool_matches_the_pnl_tabs_own_math():
    # The P&L tab works net profit out in the page (recalc): product lines, the owner's dated lines,
    # Shopify fees, then shipping, chargebacks, the fee true-up, Shopify's bills and software prorated.
    d = {"ok": True, "days_per_month": 30, "sw_tools": [{"name": "Tool", "monthly": 30.0, "freq": "monthly"}],
         "manual": {"revenue": [{"name": "Old store", "val": 1000}],                      # undated: all time only
                    "cogs": [], "ads": [], "opex": [{"name": "VA", "val": 50, "date": "2026-09-10"},
                                                    {"name": "Later", "val": 999, "date": "2026-10-20"}]},
         "pnl": {"all": {"kpi": {"revenue_new": 600, "revenue_recurring": 400, "orders_new": 6, "orders_recurring": 4,
                                 "cogs_coverage": 1, "new_subs": 2, "be_roas": 1.6, "mrr_runrate": 900,
                                 "active_subs": 30, "mrr_at_risk": 45, "overdue_subs": 2, "cogs_recurring": 80},
                         "fees": {"processing": {"total": 30}, "conversion": {"total": 10}},
                         "net": {"provisional": False}},
                 "products": [{"title": "SpermFuel+", "revenue": {"new": 600, "recurring": 400},
                               "orders": {"new": 6, "recurring": 4},
                               "by_variant": [{"variant_title": "3", "packs": 5, "cogs": 200}],
                               "campaigns": [{"campaign_name": "sperm", "spend": 250}]}],
                 "unattributed": {}, "by_day": [],
                 "store": {"shipping_revenue": 20, "chargebacks": 5, "fee_adjustment": 3, "platform_bills": 12}}}
    st = agent.pnl_statement(d, "2026-09-01", "2026-09-30")
    # 1000 + 20 - 200 - 250 - (30 + 10 + 50) - 5 - 3 - 12 - 30 (software, 30 days of 30 a month)
    assert st["headline"]["net_profit"] == 430.0 and st["headline"]["revenue"] == 1020.0
    assert st["expenses"] == {"cogs": 200.0, "meta_ad_spend": 250.0, "shopify_and_currency_fees": 93.0,
                              "shopify_bills": 12.0, "software": 30.0, "chargebacks": 5.0}
    assert [x["line"] for x in st["your_own_lines"]] == ["VA"]          # in its dates; the undated one is all-time only
    assert st["ratios"]["roas_new"] == 2.4 and st["ratios"]["cost_per_new_order"] == round(250 / 6, 2)
    assert st["subscriptions"]["mrr_at_risk_failing_payments"] == 45 and st["subscriptions"]["mrr_net_after_cogs"] == 320.0
    assert st["per_product"][0]["profit_before_fees_and_overheads"] == 550.0
    # All time counts the undated line too, and software from the first day with data.
    every = agent.pnl_statement({**d, "pnl": {**d["pnl"], "by_day": [{"date": "2026-09-01"}]}}, "2000-01-01", "2026-09-30")
    assert every["headline"]["net_profit"] == 430.0 + 1000.0 - 999.0 * 0 and every["days"] == 30


def test_the_pnl_tool_reaches_back_further_than_30_days():
    assert agent._pnl_dates({"since": "2000-01-01", "until": today()}) == ("2000-01-01", today())
    with pytest.raises(agent.ToolError):
        agent._pnl_dates({"since": today(), "until": "2000-01-01"})
