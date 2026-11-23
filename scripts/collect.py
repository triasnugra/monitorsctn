"""Collector monitor SCTN: tarik output 3 mesin dari GitHub, hitung metrik seragam, tulis data/monitor.json.
Hanya standard library. Jalankan: python scripts/collect.py [--local-root DIR] [--site site]"""
from __future__ import annotations

import argparse, csv, io, json, math, os, random, shutil, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "sctn-monitor/1.0"}


def now_utc():
    return datetime.now(timezone.utc)


def parse_ts(s):
    if not s or str(s) in ("None", "nan"):
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def iso(d):
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


def num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def rnd(x, n=4):
    return None if x is None else round(x, n)


# ---------------------------------------------------------------- fetch
class Source:
    def __init__(self, cfg, local_root=None):
        self.cfg, self.local = cfg, local_root
        self.token = os.getenv("GH_TOKEN", "")
        self.branch = {}

    def _http(self, url, as_json=False):
        h = dict(UA)
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        for i in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
                    body = r.read().decode("utf-8")
                    return json.loads(body) if as_json else body
            except urllib.error.HTTPError as e:
                if e.code in (404, 403, 401):
                    return None
            except Exception:
                pass
            time.sleep(2 * (i + 1))
        return None

    def file(self, eng, path):
        """Isi file teks dari repo mesin (None bila tidak ada)."""
        if self.local:
            p = Path(self.local) / f"{eng['repo']}-main" / path
            return p.read_text(encoding="utf-8") if p.exists() else None
        branches = [self.branch[eng["id"]]] if eng["id"] in self.branch else [self.cfg.get("branch", "main"), "master"]
        for b in branches:
            t = self._http(f"https://raw.githubusercontent.com/{self.cfg['owner']}/{eng['repo']}/{b}/{path}")
            if t is not None:
                self.branch[eng["id"]] = b
                return t
        return None

    def last_run(self, eng):
        if self.local:
            return None
        u = (f"https://api.github.com/repos/{self.cfg['owner']}/{eng['repo']}/actions/workflows/"
             f"{self.cfg.get('workflow_file', 'run-analysis.yml')}/runs?per_page=5")
        d = self._http(u, True)
        runs = (d or {}).get("workflow_runs") or []
        if not runs:
            return None
        ok = next((r for r in runs if r.get("conclusion") == "success"), None)
        r = runs[0]
        return {"status": r.get("status"), "conclusion": r.get("conclusion"), "started": r.get("run_started_at"),
                "updated": r.get("updated_at"), "url": r.get("html_url"),
                "last_success": ok.get("updated_at") if ok else None}


# ---------------------------------------------------------------- statistik
def stats(rs, times):
    n = len(rs)
    out = {"n": n}
    if n == 0:
        return out
    mean = sum(rs) / n
    std = math.sqrt(sum((x - mean) ** 2 for x in rs) / (n - 1)) if n > 1 else 0.0
    gp, gl = sum(x for x in rs if x > 0), -sum(x for x in rs if x < 0)
    pf = gp / gl if gl > 0 else None
    se = std / math.sqrt(n) if n > 1 else None
    ci = (mean - 1.96 * se, mean + 1.96 * se) if se is not None else (None, None)
    bci = (None, None)
    if n >= 10:
        rng = random.Random(42)
        ms = sorted(sum(rng.choice(rs) for _ in range(n)) / n for _ in range(2000))
        bci = (ms[int(0.025 * 2000)], ms[int(0.975 * 2000) - 1])
    eq, peak, dd, cur, streak = 0.0, 0.0, 0.0, 0, 0
    for x in rs:
        eq += x
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
        cur = cur + 1 if x < 0 else 0
        streak = max(streak, cur)
    ts = [t for t in times if t]
    weeks = (max(ts) - min(ts)).total_seconds() / 604800 if len(ts) > 1 else 0.0
    out.update(wins=sum(1 for x in rs if x > 0), losses=sum(1 for x in rs if x < 0), win_rate=sum(1 for x in rs if x > 0) / n,
               avg_r=mean, std_r=std, total_r=sum(rs), profit_factor=pf, sqn=(math.sqrt(n) * mean / std if std > 0 else None),
               ci_low=ci[0], ci_high=ci[1], boot_low=bci[0], boot_high=bci[1], max_drawdown_r=dd, max_loss_streak=streak,
               weeks=weeks, best_r=max(rs), worst_r=min(rs))
    return {k: (rnd(v) if isinstance(v, float) else v) for k, v in out.items()}


def verdict(st, gates):
    n, wk = st.get("n", 0), st.get("weeks", 0) or 0
    g = {"trades": n >= gates["min_trades"], "weeks": wk >= gates["min_weeks"],
         "ci_lower_positive": (st.get("ci_low") if st.get("ci_low") is not None else -1) > 0,
         "profit_factor": (st.get("profit_factor") or 0) >= gates["min_profit_factor"]}
    if n == 0:
        code, text = "NO_DATA", "Belum ada trade tertutup."
    elif not (g["trades"] and g["weeks"]):
        code, text = "UNVERIFIABLE", f"Sampel belum cukup ({n}/{gates['min_trades']} trade, {wk:.1f}/{gates['min_weeks']:.0f} minggu). Jangan simpulkan apa pun."
    elif all(g.values()):
        code, text = "PASS", "Lolos semua gate. Tetap lanjutkan forward test sebelum uang nyata."
    else:
        code, text = "FAIL", "Sampel cukup tetapi gate edge tidak terpenuhi."
    early = None
    if n >= 30 and st.get("ci_high") is not None and st["ci_high"] < 0:
        early = "NEGATIF"
    elif n >= 30 and st.get("ci_low") is not None and st["ci_low"] > 0:
        early = "POSITIF"
    return {"code": code, "text": text, "gates": g, "early": early}


def group(trades, key):
    g = {}
    for t in trades:
        v = t.get(key)
        g.setdefault(str(v) if v not in (None, "") else "?", []).append(t)
    res = {}
    for k, ts in g.items():
        s = stats([t["r"] for t in ts], [])
        res[k] = {x: s.get(x) for x in ("n", "win_rate", "avg_r", "profit_factor", "total_r")}
    return dict(sorted(res.items(), key=lambda kv: -(kv[1]["total_r"] or 0)))


def downsample(pts, k=300):
    if len(pts) <= k:
        return pts
    step = (len(pts) - 1) / (k - 1)
    return [pts[round(i * step)] for i in range(k)]


def read_csv(text):
    return list(csv.DictReader(io.StringIO(text))) if text else []


def loads(t):
    try:
        return json.loads(t) if t else None
    except ValueError:
        return None


# ---------------------------------------------------------------- satu mesin
def build_engine(eng, src, cfg, prev):
    gates = cfg["gates"]
    base = {"id": eng["id"], "name": eng["name"], "color": eng["color"], "repo": eng["repo"]}
    ft = loads(src.file(eng, "output/forward_test.json"))
    sl_txt = src.file(eng, "output/signal_log.csv")
    bt = loads(src.file(eng, "output/backtest/summary.json"))
    u = loads(src.file(eng, "output/universe.json"))
    run = src.last_run(eng)
    if ft is None:
        if prev and prev.get("forward"):
            prev = dict(prev, stale=True, errors=["Gagal mengambil forward_test.json — memakai data terakhir"])
            prev["health"] = dict(prev.get("health", {}), run=run, stale=True)
            return prev, {}
        return dict(base, ok=False, stale=True, errors=["forward_test.json tidak ditemukan (repo privat / belum ada output?)"],
                    health={"run": run, "stale": True}), {}

    trades = ft.get("trades", [])
    closed, active, cancelled = [], [], 0
    for t in trades:
        t["r"] = num(t.get("r"))
        t["_asof"], t["_exit"], t["_fill"] = parse_ts(t.get("asof")), parse_ts(t.get("exit_time")), parse_ts(t.get("fill_time"))
        if t.get("status") == "CLOSED" and t["r"] is not None:
            closed.append(t)
        elif t.get("status") in ("OPEN", "WAITING"):
            active.append(t)
        elif t.get("status") == "CANCELLED":
            cancelled += 1
        s = num(t.get("score"))
        t["score"] = int(s) if s is not None else None
    stamp = lambda t: t["_exit"] or t["_asof"] or now_utc()
    closed.sort(key=stamp)
    st = stats([t["r"] for t in closed], [t["_asof"] for t in closed])
    vd = verdict(st, gates)

    eq, cum, daily = [], 0.0, {}
    for t in closed:
        cum += t["r"]
        eq.append({"t": iso(stamp(t)), "r": round(cum, 3)})
        d = stamp(t).strftime("%Y-%m-%d")
        daily.setdefault(d, [0.0, 0])
        daily[d][0] += t["r"]
        daily[d][1] += 1
    holds = [(t["_exit"] - t["_fill"]).total_seconds() / 3600 for t in closed if t["_exit"] and t["_fill"]]
    outcomes = {}
    for t in closed:
        o = t.get("outcome") or "?"
        outcomes[o] = outcomes.get(o, 0) + 1
    cols = ("signal_id", "asof", "symbol", "direction", "trigger", "score")
    fwd = {
        "updated": ft.get("updated"), "stats": st,
        "counts": {"CLOSED": len(closed), "OPEN": sum(1 for t in active if t["status"] == "OPEN"),
                   "WAITING": sum(1 for t in active if t["status"] == "WAITING"), "CANCELLED": cancelled},
        "equity": downsample(eq), "outcomes": outcomes, "avg_hold_h": rnd(sum(holds) / len(holds), 1) if holds else None,
        "by_trigger": group(closed, "trigger"), "by_symbol": group(closed, "symbol"),
        "by_direction": group(closed, "direction"), "by_score": group(closed, "score"),
        "daily": [{"d": d, "r": round(v[0], 3), "n": v[1]} for d, v in sorted(daily.items())],
        "recent": [{k: t.get(k) for k in cols + ("outcome", "r", "exit_time")} for t in closed[-40:]][::-1],
        "active": [{k: t.get(k) for k in cols + ("entry", "stop", "tp1", "tp2", "status", "r")} for t in active],
        "fill_rate": rnd(len(closed) / (len(closed) + cancelled), 3) if (len(closed) + cancelled) else None,
    }

    rows = [r for r in read_csv(sl_txt) if str(r.get("demo", "0")) != "1"]
    by_status, per_day = {}, {}
    for r in rows:
        by_status[r.get("status", "?")] = by_status.get(r.get("status", "?"), 0) + 1
        d = (r.get("asof_utc") or "")[:10]
        per_day[d] = per_day.get(d, 0) + 1
    last = max((parse_ts(r.get("asof_utc")) for r in rows), default=None)
    hashes = [r.get("config_hash") for r in rows if r.get("config_hash")]
    sig = {"total": len(rows), "by_status": by_status, "last_signal": iso(last),
           "per_day": [{"d": d, "n": n} for d, n in sorted(per_day.items())[-21:]],
           "recent": [{k: r.get(k) for k in ("asof_utc", "symbol", "action", "status", "score", "trigger", "entry", "stop", "tp1")} for r in rows[-12:]][::-1]}
    cfg_hash = hashes[-1] if hashes else None

    bt_doc = None
    if bt:
        bs = bt.get("stats", {})
        keep = ("n", "win_rate", "avg_r", "total_r", "profit_factor", "sqn", "ci_low", "ci_high", "max_drawdown_r", "weeks", "status")
        bt_doc = {"generated": bt.get("generated"), "period": bt.get("period"), "days": bt.get("days"), "config_hash": bt.get("config_hash"),
                  "symbols": bt.get("symbols"), "stats": {k: (rnd(bs[k]) if isinstance(bs.get(k), float) else bs.get(k)) for k in keep},
                  "by_trigger": bt.get("by_trigger"), "by_symbol": bt.get("by_symbol"), "by_direction": bt.get("by_direction"),
                  "equity": downsample([{"t": str(p["t"]), "r": p["r"]} for p in bt.get("equity", [])], 250)}
    uni = {k: u.get(k) for k in ("source", "fetched_at", "top_n", "symbols", "rules_hash")} if u else None

    upd = parse_ts(ft.get("updated"))
    age = (now_utc() - upd).total_seconds() / 3600 if upd else None
    ok_t = parse_ts((run or {}).get("last_success"))
    ages = [a for a in (age, (now_utc() - ok_t).total_seconds() / 3600 if ok_t else None) if a is not None]
    health = {"run": run, "state_age_h": rnd(age, 1), "stale": (not ages) or min(ages) > cfg.get("stale_after_hours", 3),
              "config_hash": cfg_hash, "config_hash_changes": sum(1 for a, b in zip(hashes, hashes[1:]) if a != b),
              "bt_hash_match": bool(bt_doc and cfg_hash and bt_doc.get("config_hash") == cfg_hash)}
    bvf = None
    if bt_doc:
        b_avg, f_avg = bt_doc["stats"].get("avg_r"), st.get("avg_r")
        bvf = {"bt_avg_r": b_avg, "fw_avg_r": f_avg, "delta_avg_r": rnd(f_avg - b_avg) if None not in (b_avg, f_avg) else None,
               "bt_pf": bt_doc["stats"].get("profit_factor"), "fw_pf": st.get("profit_factor")}
    doc = dict(base, ok=True, stale=False, errors=[], verdict=vd, forward=fwd, signals=sig, backtest=bt_doc, universe=uni,
               health=health, backtest_vs_forward=bvf)
    keys = {"fuzzy": {(t.get("symbol"), t.get("direction"), t.get("trigger"), t["_asof"].strftime("%Y-%m-%d %H"))
                      for t in trades if t["_asof"]},
            "daily": {d: v[0] for d, v in daily.items()}}
    return doc, keys


def overlap(docs, keys):
    ids = [d["id"] for d in docs if d["id"] in keys]
    pairs = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            ka, kb = keys[a]["fuzzy"], keys[b]["fuzzy"]
            union = len(ka | kb)
            da, db = keys[a]["daily"], keys[b]["daily"]
            common = sorted(set(da) & set(db))
            corr = None
            if len(common) >= 5:
                xa, xb = [da[d] for d in common], [db[d] for d in common]
                ma, mb = sum(xa) / len(xa), sum(xb) / len(xb)
                sa, sb = math.sqrt(sum((x - ma) ** 2 for x in xa)), math.sqrt(sum((x - mb) ** 2 for x in xb))
                if sa > 0 and sb > 0:
                    corr = rnd(sum((x - ma) * (y - mb) for x, y in zip(xa, xb)) / (sa * sb), 3)
            pairs.append({"a": a, "b": b, "shared": len(ka & kb), "jaccard": rnd(len(ka & kb) / union, 3) if union else None,
                          "daily_corr": corr, "common_days": len(common)})
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config" / "engines.json"))
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--site", default=None, help="bangun folder situs (index.html + data/)")
    ap.add_argument("--local-root", default=None, help="uji offline: folder berisi <repo>-main/")
    a = ap.parse_args()
    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    data = Path(a.data_dir)
    data.mkdir(parents=True, exist_ok=True)
    pm, hp = data / "monitor.json", data / "history.json"
    prev = {}
    if pm.exists():
        try:
            prev = {e["id"]: e for e in json.loads(pm.read_text(encoding="utf-8")).get("engines", [])}
        except ValueError:
            pass
    src = Source(cfg, a.local_root)
    docs, keys = [], {}
    for eng in cfg["engines"]:
        d, k = build_engine(eng, src, cfg, prev.get(eng["id"]))
        docs.append(d)
        if k:
            keys[eng["id"]] = k
        print(f"[{eng['id']}] ok={d.get('ok')} stale={d.get('stale')} n={d.get('forward', {}).get('stats', {}).get('n')}")
    doc = {"generated": iso(now_utc()), "owner": cfg["owner"], "gates": cfg["gates"], "engines": docs, "overlap": overlap(docs, keys)}
    pm.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    hist = []
    if hp.exists():
        try:
            hist = json.loads(hp.read_text(encoding="utf-8"))
        except ValueError:
            hist = []
    snap = {"t": doc["generated"], "e": {d["id"]: {"n": d["forward"]["stats"].get("n", 0), "r": d["forward"]["stats"].get("total_r", 0),
                                                   "s": d["verdict"]["code"]} for d in docs if d.get("forward")}}
    lt = parse_ts(hist[-1]["t"]) if hist else None
    if not lt or (now_utc() - lt).total_seconds() > 25 * 60:
        hist.append(snap)
    hp.write_text(json.dumps(hist[-4000:], separators=(",", ":")), encoding="utf-8")

    if a.site:
        s = Path(a.site)
        if s.exists():
            shutil.rmtree(s)
        (s / "data").mkdir(parents=True)
        shutil.copy(ROOT / "index.html", s / "index.html")
        (s / ".nojekyll").write_text("")
        shutil.copy(pm, s / "data" / "monitor.json")
        shutil.copy(hp, s / "data" / "history.json")
        print("Situs dibangun di", s)
    return 0 if any(d.get("ok") for d in docs) else 1


if __name__ == "__main__":
    sys.exit(main())
