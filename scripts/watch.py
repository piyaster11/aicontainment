"""CONTAINMENT daily refresh (free sources only).
Automatic signals: headline heat (Google News RSS) + prediction markets (Manifold API).
Curated signals: data/curated.json (edited by hand, every claim linked).
Usage: python3 scripts/watch.py            -> refresh today
       python3 scripts/watch.py --backfill -> rebuild last 30 days from dated data
"""
import json, sys, datetime as dt, urllib.request, urllib.parse, xml.etree.ElementTree as ET, email.utils, bisect
D = "data/"
UA = {"User-Agent": "Mozilla/5.0 containment-dashboard"}
BKK = dt.timezone(dt.timedelta(hours=7))
Q = '("AI agent" OR "AI model") (rogue OR "shutdown resistance" OR "self-replicating" OR escaped OR "sandbox escape" OR "loss of control")'
HEAT_FULL = 80          # 80+ matching headlines in 7 days = 10/10
MARKETS = ["rogue-ais-before-2028", "will-a-frontier-model-be-found-to-h", "will-there-be-an-ai-loss-of-control"]
W_HEAT, W_MKT = 0.15, 0.15
BANDS = [(2.5, "CONTAINED"), (4.5, "WATCH"), (6.5, "LEAKING"), (8.5, "BREACH"), (99, "LOOSE")]

def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read()
def jload(f, d=None):
    try: return json.load(open(D + f))
    except Exception: return d
def jsave(f, o):
    json.dump(o, open(D + f, "w"), ensure_ascii=False, indent=1)
def band(s): return next(n for t, n in BANDS if s < t)

def news(window):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": f"{Q} {window}", "hl": "en-US", "gl": "US", "ceid": "US:en"})
    items = []
    for it in ET.fromstring(get(url)).iter("item"):
        t = it.findtext("title", ""); src = it.findtext("source", "")
        if src and t.endswith(" - " + src): t = t[: -len(src) - 3]
        d = email.utils.parsedate_to_datetime(it.findtext("pubDate"))
        items.append({"t": t, "src": src, "url": it.findtext("link"), "date": d.astimezone(BKK).strftime("%Y-%m-%d %H:%M")})
    return items

def heat_score(n): return round(min(10, 10 * n / HEAT_FULL), 1)

def markets(at_ms=None):
    out = []
    for slug in MARKETS:
        m = json.loads(get(f"https://api.manifold.markets/v0/slug/{slug}"))
        p = m.get("probability")
        if at_ms:  # probability as of a past moment, from the public bet history
            bets = json.loads(get(f"https://api.manifold.markets/v0/bets?contractSlug={slug}&limit=1000"))
            past = [b for b in bets if b["createdTime"] <= at_ms and "probAfter" in b]
            if not past: continue
            p = max(past, key=lambda b: b["createdTime"])["probAfter"]
        out.append({"q": m["question"], "p": round(p, 3), "url": m["url"]})
    return out

def mkt_score(ms): return round(10 * sum(m["p"] for m in ms) / len(ms), 1) if ms else None

def total(cur, heat, mkt):
    parts = [(s["weight"], s["score"]) for s in cur] + [(W_HEAT, heat), (W_MKT, mkt)]
    parts = [(w, v) for w, v in parts if v is not None]
    return round(sum(w * v for w, v in parts) / sum(w for w, _ in parts), 1)

def today():
    now = dt.datetime.now(BKK); day = now.strftime("%Y-%m-%d")
    cur = jload("curated.json")["signals"]
    prev = jload("score.json", {})
    try:
        heads = news("when:7d"); heads.sort(key=lambda x: x["date"], reverse=True)
        heat = heat_score(len(heads)); jsave("headlines.json", {"updated": now.strftime("%Y-%m-%d %H:%M"), "items": heads[:15]})
    except Exception as e:
        print("news failed", e); heads = None; heat = prev.get("heat", {}).get("score")
    try:
        ms = markets(); mk = mkt_score(ms)
    except Exception as e:
        print("markets failed", e); ms = prev.get("markets", {}).get("list", []); mk = prev.get("markets", {}).get("score")
    sc = total(cur, heat, mk)
    hist = [h for h in jload("history.json", []) if h["date"] != day] + [{"date": day, "score": sc, "backfill": False}]
    hist = sorted(hist, key=lambda h: h["date"])[-60:]
    jsave("history.json", hist)
    jsave("score.json", {"updated": now.strftime("%Y-%m-%d %H:%M") + " ICT", "score": sc, "band": band(sc),
        "heat": {"score": heat, "count7d": len(heads) if heads is not None else prev.get("heat", {}).get("count7d"), "weight": W_HEAT, "full": HEAT_FULL,
                 "src": "https://news.google.com/search?" + urllib.parse.urlencode({"q": Q + " when:7d"})},
        "markets": {"score": mk, "weight": W_MKT, "list": ms}})
    print("score", sc, band(sc))

def backfill(days=30):
    tl = jload("curated_timeline.json")["changes"]; cur = jload("curated.json")["signals"]
    state, dated = {}, []
    for c in tl:
        state.update({k: v for k, v in c.items() if k in ("real", "lab", "cap", "gov")}); dated.append((c["date"], dict(state)))
    hist = {h["date"]: h for h in jload("history.json", []) if not h.get("backfill")}
    end = dt.datetime.now(BKK).date()
    for i in range(days, 0, -1):
        d = end - dt.timedelta(days=i); ds = d.isoformat()
        if ds in hist: continue
        idx = bisect.bisect_right([x[0] for x in dated], ds) - 1
        if idx < 0: continue
        sig = [dict(s, score=dated[idx][1][s["id"]]) for s in cur]
        try: heat = heat_score(len(news(f"after:{(d - dt.timedelta(days=7)).isoformat()} before:{(d + dt.timedelta(days=1)).isoformat()}")))
        except Exception: heat = None
        at = int(dt.datetime.combine(d, dt.time(7), BKK).timestamp() * 1000)
        try: mk = mkt_score(markets(at))
        except Exception: mk = None
        hist[ds] = {"date": ds, "score": total(sig, heat, mk), "backfill": True}
        print(ds, hist[ds]["score"])
    jsave("history.json", sorted(hist.values(), key=lambda h: h["date"]))

if __name__ == "__main__":
    today()
    if "--backfill" in sys.argv: backfill()
