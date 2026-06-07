"""
高切低选股: 方向池热度前 20, 找 3/5 日跌幅最大前 3
- 拉每方向前 3 只 A 股的近 5 日 K 线
- 等权平均算出方向指数的 3/5 日涨幅
- 排序取跌幅最大前 3
- 总 6 只候选
- 数据源: 腾讯 K 线 (稳, 0.4s/只, qfq 前复权)
"""
import os
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

_CACHE = {"data": None, "ts": 0, "ttl": 3600}  # 1h 缓存
_SESSION = None  # 复用 TCP 连接


def _session():
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://gu.qq.com/",
        })
    return _SESSION


def _get_top_directions(top_n=20, use_real=False):
    """调 direction_pool 拿全部方向, 按 total 排序取前 N
    关键: score_direction 不会把 a_shares/strictness 注入结果,
    这里从 DIRECTIONS 原始列表取, 再按 total 排序
    """
    try:
        from direction_pool import score_all, DIRECTIONS
        all_dirs = score_all(use_real=use_real)
        if not all_dirs:
            return []
        # 注入 a_shares / strictness (从原始 DIRECTIONS 取)
        by_code = {d["code"]: d for d in DIRECTIONS}
        for r in all_dirs:
            orig = by_code.get(r["code"], {})
            r["a_shares"] = orig.get("a_shares", [])
            r["strictness"] = orig.get("strictness", 0)
        sorted_dirs = sorted(all_dirs, key=lambda d: d.get("total", 0), reverse=True)
        return sorted_dirs[:top_n]
    except Exception as e:
        print(f"[high_low_switch] 拉方向池失败: {e}")
        return []


def _parse_code(code):
    """300308.SZ -> ('sz300308', '300308', 'sz')"""
    sym = code.split(".")[0] if "." in code else code
    if not sym or not sym[0].isdigit():
        return None, sym, None
    # 腾讯不支持北交所 (4/8/9 开头)
    if sym[0] in ("4", "8", "9"):
        return None, sym, None
    prefix = "sh" if sym[0] in ("6", "5") else "sz"
    return f"{prefix}{sym}", sym, prefix


def _fetch_closes_tx(code6, days=8, max_retry=2):
    """腾讯 K 线: 拉 N 天, 返回 [(date, close), ...]
    https://web.ifzq.gtimg.cn/appstock/app/fqkline/get
    """
    if code6 is None:
        return []
    sess = _session()
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    for attempt in range(max_retry + 1):
        try:
            r = sess.get(url, params={
                "param": f"{code6},day,,,{days},qfq"
            }, timeout=8)
            j = r.json()
            if j.get("code") != 0:
                continue
            data = j.get("data", {}).get(code6, {})
            klines = data.get("qfqday") or data.get("day", [])
            return [(k[0], float(k[2])) for k in klines]  # [date, close]
        except Exception as e:
            if attempt < max_retry:
                time.sleep(0.5)
                continue
            return []
    return []


def _calc_one_stock_pct(code, period_days):
    """单只 A 股 N 日涨幅 (%)
    腾讯: 1 次拉 days+5 天, 取最后 period_days 天算涨幅
    """
    try:
        code6, _, _ = _parse_code(code)
        if code6 is None:
            return None
        # 拉 period_days+5 天 (跨周末 / 节假日 buffer)
        klines = _fetch_closes_tx(code6, days=period_days + 5)
        if len(klines) < period_days:
            return None
        closes = [c for _, c in klines[-period_days:]]
        pct = (closes[-1] - closes[0]) / closes[0] * 100
        return round(pct, 2)
    except Exception:
        return None


def _calc_direction_pct(direction, period_days):
    """算单个方向的 N 日涨幅 = 前 3 只 A 股等权平均"""
    a_shares = direction.get("a_shares", [])[:3]
    if not a_shares:
        return None
    codes = [a.get("code", "") for a in a_shares if a.get("code")]
    if not codes:
        return None
    # 并发拉 (4 workers, 避免限频)
    results = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(_calc_one_stock_pct, c, period_days): c for c in codes}
        for f in as_completed(futures, timeout=30):
            c = futures[f]
            try:
                r = f.result(timeout=20)
                if r is not None:
                    results[c] = r
            except Exception:
                pass
    if not results:
        return None
    avg = sum(results.values()) / len(results)
    worst_code = min(results, key=results.get)
    return {
        "avg_pct": round(avg, 2),
        "worst_code": worst_code,
        "worst_pct": results[worst_code],
        "stocks": [{"code": c, "pct": p} for c, p in results.items()],
    }


def _calc_one_stock_consec_down(code, lookback=10):
    """单只 A 股连跌天数 (从最新一天往前数, 连续 close<prev_close)
    返回 int (0 表示未连跌或数据不够)
    """
    try:
        code6, _, _ = _parse_code(code)
        if code6 is None:
            return None
        klines = _fetch_closes_tx(code6, days=lookback + 2)
        if len(klines) < 2:
            return 0
        # _fetch_closes_tx 返回 [(date, close), ...]
        closes = [c for _, c in klines]
        consecutive = 0
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] < closes[i - 1]:
                consecutive += 1
            else:
                break
        return consecutive
    except Exception:
        return None


def _calc_direction_consec(direction, lookback=10):
    """算单个方向的连跌天数 = 前 3 只 A 股平均连跌天数 (从最新一天往前数)
    返回 {avg_consec, max_consec, max_code, stocks: [{code, consec}]}
    """
    a_shares = direction.get("a_shares", [])[:3]
    if not a_shares:
        return None
    codes = [a.get("code", "") for a in a_shares if a.get("code")]
    if not codes:
        return None
    results = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(_calc_one_stock_consec_down, c, lookback): c for c in codes}
        for f in as_completed(futures, timeout=30):
            c = futures[f]
            try:
                r = f.result(timeout=20)
                if r is not None:
                    results[c] = r
            except Exception:
                pass
    if not results:
        return None
    # 平均连跌 (0 也要算, 反映"未连跌")
    avg = round(sum(results.values()) / len(results), 1)
    # 找连跌最久的 1 只
    max_code = max(results, key=results.get)
    return {
        "avg_consec": avg,
        "max_consec": results[max_code],
        "max_code": max_code,
        "stocks": [{"code": c, "consec": v} for c, v in results.items()],
    }


def get_high_low_switch(top_n=20, top_k_3d=5, top_k_5d=3, top_k_consec=3):
    """主入口: 拿方向池热度前 N, 找 3/5 日跌幅最大前 K + 连跌天数前 K
    top_k_3d=5: 3 日 Top 5
    top_k_5d=3: 5 日 Top 3
    top_k_consec=3: 连跌天数 Top 3 (用户新加)
    """
    now = time.time()
    if _CACHE["data"] and (now - _CACHE["ts"]) < _CACHE["ttl"]:
        return _CACHE["data"]

    top_dirs = _get_top_directions(top_n=top_n)
    if not top_dirs:
        return {"error": "方向池为空", "top_n": top_n, "directions_analyzed": 0, "candidates": []}

    # 算每个方向的 3/5 日涨幅 + 连跌天数 (并发)
    results = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {}
        for d in top_dirs:
            futures[ex.submit(_calc_direction_pct, d, 3)] = ("3d", d)
            futures[ex.submit(_calc_direction_pct, d, 5)] = ("5d", d)
            futures[ex.submit(_calc_direction_consec, d)] = ("consec", d)
        for f in as_completed(futures, timeout=180):
            period, d = futures[f]
            try:
                r = f.result(timeout=60)
                if r:
                    results.append({
                        "code": d.get("code"),
                        "name": d.get("name"),
                        "category": d.get("category"),
                        "total": d.get("total"),
                        "period": period,
                        **r,
                    })
            except Exception:
                pass

    # 3 日跌幅最大前 5, 5 日 Top 3, 连跌天数 Top 3 (按 avg_consec 降序)
    top_3d = sorted([r for r in results if r["period"] == "3d"], key=lambda r: r["avg_pct"])[:top_k_3d]
    top_5d = sorted([r for r in results if r["period"] == "5d"], key=lambda r: r["avg_pct"])[:top_k_5d]
    top_consec = sorted([r for r in results if r["period"] == "consec"], key=lambda r: r["avg_consec"], reverse=True)[:top_k_consec]

    data = {
        "timestamp": time.time(),
        "duration_sec": round(time.time() - t0, 1),
        "top_n": top_n,
        "directions_analyzed": len(top_dirs),
        "directions_success": len({r["code"] for r in results}),
        "top_3d_drop": top_3d,
        "top_5d_drop": top_5d,
        "top_consec_drop": top_consec,
        "summary": {
            "3d_count": len(top_3d),
            "5d_count": len(top_5d),
            "consec_count": len(top_consec),
            "best_drop_3d": top_3d[0] if top_3d else None,
            "best_drop_5d": top_5d[0] if top_5d else None,
            "best_consec": top_consec[0] if top_consec else None,
        },
    }
    _CACHE["data"] = data
    _CACHE["ts"] = now
    return data


if __name__ == "__main__":
    import json
    r = get_high_low_switch(top_n=20)
    print(json.dumps(r, ensure_ascii=False, indent=2)[:3000])
