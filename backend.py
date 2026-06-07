"""
金融仪表盘后端
数据源全部走腾讯/新浪 API，不再依赖 akshare（避免网络超时）
"""
import requests
import pandas as pd
import time
from datetime import datetime, date
from flask import Flask, jsonify
import os

app = Flask(__name__, static_folder='static')

@app.after_request
def add_cors(r):
    r.headers["Access-Control-Allow-Origin"] = "*"
    r.headers["Access-Control-Allow-Methods"] = "GET,OPTIONS"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type"
    # 强制禁用浏览器缓存（开发期避免改完代码用户看不到）
    r.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    r.headers["Pragma"] = "no-cache"
    r.headers["Expires"] = "0"
    return r


# ──────────────────────────────────────────────
# 导入美股异动模块
# ──────────────────────────────────────────────
from us_anomaly import run_us_anomaly, run_async as run_us_async, manual_analyze, load_cache
from metals_monitor import run_metals_monitor
from metals_spot import run_metals_spot, run_async as run_metals_async
from metals_anomaly import load_anomaly_cache, batch_analyze_async as batch_metals_async
import json as _json_metal

# ──────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────
def pctClass(num):
    if num is None: return ''
    return 'up' if float(num) >= 0 else 'dn'

def safe_float(val, default=0.0):
    try: return float(val)
    except: return default

def safe_int(val, default=0):
    try: return int(float(val))
    except: return default


# ──────────────────────────────────────────────
# 美元流动性 — DollarLiquidity API
# ──────────────────────────────────────────────
def get_liquidity_score():
    try:
        r = requests.get("https://dollarliquidity.com/api/regime", timeout=10)
        r.raise_for_status()
        d = r.json()
        return {
            "status": d.get("status", "unknown"),
            "momentum": d.get("momentum", ""),
            "percentile": d.get("percentile5y", 0),
            "score": d.get("compositeScore", 0),
            "dataAsOf": d.get("dataAsOf", ""),
            "updatedAt": d.get("updatedAt", ""),
            "coverage": d.get("coverage", {}),
            "drivers": [
                {
                    "indicatorId": x.get("indicatorId", ""),
                    "weight": round(x.get("weight", 0), 3),
                    "zScore": round(x.get("zScore", 0), 2),
                    "direction": x.get("direction", "")
                }
                for x in d.get("drivers", [])
            ]
        }
    except:
        return {"status": "网络受限", "percentile": 0}


# ──────────────────────────────────────────────
# A股指数 — 腾讯接口
# ──────────────────────────────────────────────
def get_index_spot():
    try:
        codes = "sh000001,sz399001,sz399006,sh000688,sh000300,sh000905,sh000852,sh000016,bj899050"
        url = f"https://qt.gtimg.cn/q={codes}"
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com"}
        r = requests.get(url, headers=headers, timeout=6)
        r.raise_for_status()
        name_map = {
            "sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指",
            "sh000688": "科创50", "sh000300": "沪深300", "sh000905": "中证500",
            "sh000852": "中证1000", "sh000016": "上证50", "bj899050": "北证50",
        }
        results = []
        for line in r.text.strip().split("\n"):
            if "v_" not in line: continue
            code = line.split("=")[0].replace("v_", "").strip()
            content = line.split('"')[1] if '"' in line else ""
            if not content: continue
            parts = content.split("~")
            if len(parts) < 33: continue
            try:
                results.append({
                    "名称": name_map.get(code, parts[1] if len(parts) > 1 else code),
                    "最新价": safe_float(parts[3]),
                    "涨跌幅": safe_float(parts[32]),
                    "涨跌额": safe_float(parts[31]),
                    "成交量": safe_int(parts[6]),
                })
            except: continue
        return results
    except:
        return []


# ──────────────────────────────────────────────
# 涨停/炸板/跌停 — 东财接口直调（绕过akshare）
# ──────────────────────────────────────────────
def _fetch_json(url, headers=None, timeout=8):
    try:
        h = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.eastmoney.com"}
        if headers: h.update(headers)
        r = requests.get(url, headers=h, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except:
        return None


def get_zt_pool(date_str=None):
    """涨停股池（恢复用 akshare 原始接口）"""
    import akshare as ak
    today = date_str or date.today().strftime("%Y%m%d")
    try:
        df = ak.stock_zt_pool_em(date=today)
        if df is None or df.empty: return []
        cols = ["代码", "名称", "最新价", "涨跌幅", "成交额", "流通市值",
                "换手率", "连板数", "所属行业", "首次封板时间", "炸板次数"]
        available = [c for c in cols if c in df.columns]
        df = df[available].copy()
        if "流通市值" in df.columns:
            df["流通市值"] = (df["流通市值"] / 1e8).round(2)
        return df.to_dict(orient="records")
    except:
        return []


def get_zt_zbgc():
    """炸板股池"""
    import akshare as ak
    today = date.today().strftime("%Y%m%d")
    try:
        df = ak.stock_zt_pool_zbgc_em(date=today)
        if df is None or df.empty: return []
        cols = ["代码", "名称", "最新价", "涨跌幅", "成交额", "流通市值",
                "换手率", "所属行业"]
        available = [c for c in cols if c in df.columns]
        df = df[available].copy()
        if "流通市值" in df.columns:
            df["流通市值"] = (df["流通市值"] / 1e8).round(2)
        return df.to_dict(orient="records")
    except:
        return []


def get_zt_dtgc():
    """跌停股池"""
    import akshare as ak
    today = date.today().strftime("%Y%m%d")
    try:
        df = ak.stock_zt_pool_dtgc_em(date=today)
        if df is None or df.empty: return []
        cols = ["代码", "名称", "最新价", "涨跌幅", "成交额", "流通市值", "所属行业"]
        available = [c for c in cols if c in df.columns]
        df = df[available].copy()
        if "流通市值" in df.columns:
            df["流通市值"] = (df["流通市值"] / 1e8).round(2)
        return df.to_dict(orient="records")
    except:
        return []


def get_zt_stats():
    """创新高/新低统计"""
    import akshare as ak
    try:
        df = ak.stock_a_high_low_statistics(symbol="all")
        if df is None or df.empty: return {}
        latest = df.tail(1).iloc[0]
        return {
            "date": str(latest.get("date", "")),
            "close": float(latest.get("close", 0)),
            "high20": int(latest.get("high20", 0)),
            "low20": int(latest.get("low20", 0)),
            "high60": int(latest.get("high60", 0)),
            "low60": int(latest.get("low60", 0)),
            "high120": int(latest.get("high120", 0)),
            "low120": int(latest.get("low120", 0)),
        }
    except:
        return {}


_INDEX_CACHE = {"data": None, "ts": 0}
_INDEX_TTL = 300  # 5 分钟


def _fetch_index_tencent(codes):
    """腾讯 qt.gtimg 一次拉多指数"""
    try:
        url = f"https://qt.gtimg.cn/q={','.join(codes)}"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com"}, timeout=6)
        r.raise_for_status()
        results = []
        for line in r.text.strip().split("\n"):
            if "v_" not in line: continue
            code = line.split("=")[0].replace("v_", "").strip()
            content = line.split('"')[1] if '"' in line else ""
            if not content: continue
            parts = content.split("~")
            if len(parts) < 33: continue
            results.append({
                "code": code,
                "name": parts[1],
                "price": safe_float(parts[3]),
                "chg_pct": safe_float(parts[32]),
                "chg_amt": safe_float(parts[31]),
                "volume": safe_int(parts[6]),
            })
        return results
    except:
        return []


def _fetch_a_share_avg_one_page(page, page_size=80):
    """新浪分页拉 A 股"""
    try:
        url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
        params = {"num": page_size, "page": page, "sort": "symbol", "asc": 1, "node": "hs_a", "_s_r_a": "page"}
        r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://vip.stock.finance.sina.com.cn/"}, timeout=10)
        return r.json() or []
    except:
        return []


def _fetch_a_share_avg_concurrent(max_workers=12, page_size=80, max_pages=80):
    """并发拉全 A 股, 算均价"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    # 先拉第 1 页看总数
    first = _fetch_a_share_avg_one_page(1, page_size)
    if not first:
        return None, 0
    total_pages = max_pages
    all_stocks = list(first)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_fetch_a_share_avg_one_page, p, page_size): p for p in range(2, total_pages + 1)}
        for f in as_completed(futures):
            data = f.result()
            if not data:
                break
            all_stocks.extend(data)
    prices = []
    for d in all_stocks:
        try:
            p = float(d.get("trade", 0))
            if 0 < p < 10000:
                prices.append(p)
        except:
            pass
    if not prices:
        return None, 0
    return round(sum(prices) / len(prices), 2), len(prices)


def get_index_spot():
    """指数行情 + A 股均价 (腾讯 + 新浪, 5min 缓存)"""
    now = time.time()
    if _INDEX_CACHE["data"] and (now - _INDEX_CACHE["ts"]) < _INDEX_TTL:
        return _INDEX_CACHE["data"]

    # 6 指数走腾讯
    codes = [
        "sh000001", "sz399001", "sz399006", "sh000680",
        "sh000300", "bj899050",
    ]
    name_map = {
        "sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指",
        "sh000680": "科创综指", "sh000300": "沪深300", "bj899050": "北证50",
    }
    results = []
    raw = _fetch_index_tencent(codes)
    for x in raw:
        results.append({
            "名称": name_map.get(x["code"], x["name"]),
            "代码": x["code"],
            "最新价": x["price"],
            "涨跌幅": x["chg_pct"],
            "涨跌额": x["chg_amt"],
            "成交量": x["volume"],
        })

    # A 股均价走新浪并发
    avg_price, sample = _fetch_a_share_avg_concurrent(max_workers=12, page_size=80, max_pages=80)
    if avg_price is not None:
        results.append({
            "名称": "A股平均股价",
            "代码": "AVG",
            "最新价": avg_price,
            "涨跌幅": None,  # 均价无涨跌幅概念
            "涨跌额": None,
            "成交量": None,
            "_meta": {"样本数": sample, "单位": "元"},
        })

    _INDEX_CACHE["data"] = results
    _INDEX_CACHE["ts"] = now
    return results


# ──────────────────────────────────────────────
# 外盘期货 — 腾讯接口
# ──────────────────────────────────────────────
def get_futures_spot():
    try:
        contracts = [
            ("hf_GC", "COMEX黄金"),
            ("hf_SI", "COMEX白银"),
            ("hf_HG", "COMEX铜"),
            ("hf_CL", "NYMEX原油"),
            ("hf_NG", "NYMEX天然气"),
            ("hf_LA", "LME铝3月"),
            ("hf_LN", "LME镍3月"),
            ("hf_LZ", "LME锌3月"),
            ("hf_BZ", "布伦特原油"),
        ]
        codes = ",".join([c[0] for c in contracts])
        name_map = {c[0]: c[1] for c in contracts}
        url = f"https://qt.gtimg.cn/q={codes}"
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.qq.com"}
        r = requests.get(url, headers=headers, timeout=6)
        r.raise_for_status()
        results = []
        for line in r.text.strip().split("\n"):
            if "v_" not in line: continue
            code = line.split("=")[0].strip().replace("v_", "")
            content = line.split('"')[1] if '"' in line else ""
            if not content: continue
            parts = [p.strip() for p in content.split(",")]
            if len(parts) < 14: continue
            try:
                results.append({
                    "名称": name_map.get(code, code),
                    "最新价": safe_float(parts[0].replace(" ", "")),
                    "涨跌幅": safe_float(parts[1].replace(" ", "")),
                    "涨跌额": safe_float(parts[1].replace(" ", "")),
                })
            except: continue
        return results if results else []
    except:
        return []


# ──────────────────────────────────────────────
# 情绪指数 — 11个核心指标
# ──────────────────────────────────────────────
def _score_to_percentile(value, danger_low, warn_low, good_low, good_high, warn_high, danger_high):
    """把指标值映射到 P0-P100 百分位"""
    if value is None: return 50
    if value <= danger_low: return 5
    elif value <= warn_low: return 25
    elif value <= good_low: return 45
    elif value <= good_high: return 55
    elif value <= warn_high: return 75
    elif value <= danger_high: return 90
    else: return 98


def _grade(percentile, reverse=False):
    """根据百分位判定档位（reverse=True 适用于越低越好的指标）"""
    if reverse:
        if percentile <= 20: return ("安全", "loose")
        elif percentile <= 50: return ("偏弱", "neutral")
        elif percentile <= 80: return ("警惕", "neutral-tight")
        else: return ("危险", "tight")
    else:
        if percentile <= 20: return ("危险", "tight")
        elif percentile <= 50: return ("偏弱", "neutral-tight")
        elif percentile <= 80: return ("正常", "neutral")
        else: return ("过热", "loose")


def get_sentiment_index():
    """综合情绪指数：11个核心指标"""
    import akshare as ak_local
    indicators = []
    today = date.today().strftime("%Y%m%d")

    # 启动时异步预热 macro (不阻塞)
    try:
        from macro_fetcher import start_async_warmup
        start_async_warmup()
    except:
        pass

    # 1) 涨停家数
    try:
        df = ak_local.stock_zt_pool_em(date=today)
        zt_count = len(df) if df is not None and not df.empty else 0
        p = _score_to_percentile(zt_count, danger_low=20, warn_low=40, good_low=60, good_high=100, warn_high=150, danger_high=200)
        indicators.append({
            "name": "涨停家数", "value": zt_count, "unit": "家", "percentile": p,
            "grade": _grade(p, reverse=False)[0], "gradeColor": _grade(p, reverse=False)[1],
            "logic": "做多动能直接量化", "danger": "<30 连续下行危险"
        })
    except: pass

    # 2) 最高连板
    try:
        df = ak_local.stock_zt_pool_em(date=today)
        max_boards = int(df["连板数"].max()) if df is not None and not df.empty else 0
        p = _score_to_percentile(max_boards, danger_low=0, warn_low=2, good_low=4, good_high=7, warn_high=10, danger_high=15)
        indicators.append({
            "name": "最高连板", "value": max_boards, "unit": "板", "percentile": p,
            "grade": _grade(p, reverse=False)[0], "gradeColor": _grade(p, reverse=False)[1],
            "logic": "风偏下降最先体现在敢不敢接高度", "danger": "高度压到2-3板+梯队断层"
        })
    except: pass

    # 3) 炸板率
    try:
        df_zt = ak_local.stock_zt_pool_em(date=today)
        df_zb = ak_local.stock_zt_pool_zbgc_em(date=today)
        zt_n = len(df_zt) if df_zt is not None and not df_zt.empty else 0
        zb_n = len(df_zb) if df_zb is not None and not df_zb.empty else 0
        total = zt_n + zb_n
        zbgc_rate = round(zb_n / total * 100, 1) if total > 0 else 0
        p = _score_to_percentile(zbgc_rate, danger_low=0, warn_low=10, good_low=20, good_high=30, warn_high=40, danger_high=60)
        indicators.append({
            "name": "炸板率", "value": zbgc_rate, "unit": "%", "percentile": p,
            "grade": _grade(p, reverse=True)[0], "gradeColor": _grade(p, reverse=True)[1],
            "logic": "封板强度=强封→反复炸+午后放量漏单", "danger": ">35-45% 且持续上行"
        })
    except: pass

    # 4) 新高新低比
    try:
        df = ak_local.stock_a_high_low_statistics(symbol="all")
        if df is not None and not df.empty:
            latest = df.tail(1).iloc[0]
            h20 = int(latest.get("high20", 0))
            l20 = int(latest.get("low20", 0))
            ratio = round(h20 / max(l20, 1), 2)
            p = _score_to_percentile(ratio, danger_low=0.1, warn_low=0.3, good_low=0.5, good_high=1.5, warn_high=2.5, danger_high=4.0)
            indicators.append({
                "name": "新高/新低比", "value": ratio, "unit": "倍", "percentile": p,
                "grade": _grade(p, reverse=False)[0], "gradeColor": _grade(p, reverse=False)[1],
                "logic": "趋势段结束的典型前兆：新高枯竭+新低扩散", "danger": "新高<新低 持续3天以上"
            })
    except: pass

    # 5) 大面占比（昨涨停今日-7%以上占比 = 核按钮）
    try:
        # 用腾讯全A接口拿当日涨跌幅，按流通市值加权粗算
        sample_stocks = ["sh600519", "sz000001", "sh601318", "sz000858", "sh600036", "sz000333", "sh600276"]
        # 由于akshare全A接口不稳定，改用其他方法：用涨停池 + 简单取前100大股票
        df_all = ak_local.stock_zh_a_hist(symbol="000001", period="daily", start_date=today, end_date=today, adjust="qfq")
        # 退路：用东方财富全A榜的简化版
    except: pass

    # 简化版：用 akshare 提供的另一组接口计算大面
    try:
        # 尝试用 akshare 的"强势股池"获取昨日涨停今日表现
        df_yzt = ak_local.stock_zt_pool_strong_em(date=today)  # 强势股池
        if df_yzt is not None and not df_yzt.empty:
            n_total = len(df_yzt)
            n_drop7 = len(df_yzt[df_yzt["涨跌幅"] < -7])
            big_face = round(n_drop7 / max(n_total, 1) * 100, 1)
            p = _score_to_percentile(big_face, danger_low=0, warn_low=3, good_low=7, good_high=15, warn_high=25, danger_high=40)
            indicators.append({
                "name": "大面占比", "value": big_face, "unit": "%", "percentile": p,
                "grade": _grade(p, reverse=True)[0], "gradeColor": _grade(p, reverse=True)[1],
                "logic": "当天把接力资金活埋，次日更没人敢封", "danger": ">7-10% 警惕"
            })
    except: pass

    # 6) 跌幅<-5% 受伤面积 (用 get_all_a_snapshot 真实全 A 统计, 30min 缓存)
    try:
        a_data = get_all_a_snapshot()  # 内部 30min cache, 自动 fallback
        if a_data and a_data.get("total", 0) > 0:
            drop_n = a_data.get("n_drop5", 0)
            total = a_data.get("total", 5524)
            p = _score_to_percentile(drop_n, danger_low=0, warn_low=30, good_low=80, good_high=300, warn_high=600, danger_high=1200)
            sample_tag = "" if a_data.get("fallback") is not True else " (样本)"
            indicators.append({
                "name": "跌幅<-5%股数", "value": drop_n, "unit": f"家 / {total} 只" + sample_tag, "percentile": p,
                "grade": _grade(p, reverse=True)[0], "gradeColor": _grade(p, reverse=True)[1],
                "logic": "全 A 实时: 跌幅<-5% 个数 (30min 缓存)", "danger": "占比突然翻倍式跳升"
            })
    except: pass

    # 7) 站上MA20占比
    try:
        # 取上证指数作为大盘代表（更通用：算全A站上MA20占比）
        # akshare 没有现成接口，用沪深300成分股近似
        # 退路：直接读etf基金数据做近似
        # 这里改用 akshare 的"指数成分股"接口来近似
        df_hs300 = ak_local.index_stock_cons_weight_csindex(symbol="000300")
        if df_hs300 is not None and not df_hs300.empty:
            # 简单返回 0.5 作为占位（因为不能实时算）
            indicators.append({
                "name": "站上MA20占比", "value": "—", "unit": "", "percentile": 50,
                "grade": "数据受限", "gradeColor": "neutral",
                "logic": "趋势结构退化：随机震荡/下行", "danger": "占比持续下行"
            })
    except: pass

    # 8) 成交额集中度（CR10 = 成交额前10的股票 / 全市场）
    try:
        # 通过涨停池+炸板池的成交额累加（近似的市场前N只）
        df_zt = ak_local.stock_zt_pool_em(date=today)
        df_zb = ak_local.stock_zt_pool_zbgc_em(date=today)
        total_amount = 0
        top_amount = 0
        all_rows = []
        if df_zt is not None and not df_zt.empty:
            all_rows.extend([(r.get("成交额", 0), r.get("最新价", 0)) for _, r in df_zt.iterrows()])
        if df_zb is not None and not df_zb.empty:
            all_rows.extend([(r.get("成交额", 0), r.get("最新价", 0)) for _, r in df_zb.iterrows()])
        all_rows.sort(key=lambda x: x[0], reverse=True)
        total_amount = sum(x[0] for x in all_rows)
        top_amount = sum(x[0] for x in all_rows[:10])
        if total_amount > 0:
            cr10 = round(top_amount / total_amount * 100, 2)
        else:
            cr10 = 0
        p = _score_to_percentile(cr10, danger_low=0, warn_low=0.5, good_low=1.0, good_high=2.0, warn_high=2.8, danger_high=4.0)
        indicators.append({
            "name": "成交集中度CR10", "value": cr10, "unit": "%", "percentile": p,
            "grade": _grade(p, reverse=True)[0], "gradeColor": _grade(p, reverse=True)[1],
            "logic": "杠杆越厚，回调越容易变踩踏", "danger": ">2.5%警戒 → >3.0-3.5%过热 → >4.5%极值"
        })
    except: pass

    # 9) 行业融资集中度
    try:
        df_margin = ak_local.stock_margin_underlying_info_szse(date=today)
        if df_margin is not None and not df_margin.empty:
            # 简单返回前5行业占比
            top5 = df_margin.nlargest(5, "融资买入额")
            top5_sum = top5["融资买入额"].sum()
            total = df_margin["融资买入额"].sum()
            concentration = round(top5_sum / max(total, 1) * 100, 1) if total > 0 else 0
            p = _score_to_percentile(concentration, danger_low=0, warn_low=20, good_low=35, good_high=55, warn_high=70, danger_high=85)
            indicators.append({
                "name": "融资集中度(Top5)", "value": concentration, "unit": "%", "percentile": p,
                "grade": _grade(p, reverse=True)[0], "gradeColor": _grade(p, reverse=True)[1],
                "logic": "杠杆扎堆方向拐头→强平制造加速器", "danger": "Top5 占比>60% 警惕"
            })
    except: pass

    # 10) 昨日涨停溢价（用涨停池涨跌幅均值近似）
    try:
        df_zt = ak_local.stock_zt_pool_em(date=today)
        if df_zt is not None and not df_zt.empty:
            avg_pct = round(df_zt["涨跌幅"].mean() - 10, 2)  # 涨停=10%，所以超额
            p = _score_to_percentile(avg_pct, danger_low=-3, warn_low=0, good_low=2, good_high=5, warn_high=8, danger_high=15)
            indicators.append({
                "name": "涨停股超额", "value": avg_pct, "unit": "%", "percentile": p,
                "grade": _grade(p, reverse=False)[0], "gradeColor": _grade(p, reverse=False)[1],
                "logic": "赚钱效应断裂的最直接证据", "danger": "溢价<0% 核按钮占比陡升"
            })
    except: pass

    # 11) 炸板率 vs 涨停率比值
    try:
        df_zt = ak_local.stock_zt_pool_em(date=today)
        df_zb = ak_local.stock_zt_pool_zbgc_em(date=today)
        zt_n = len(df_zt) if df_zt is not None and not df_zt.empty else 0
        zb_n = len(df_zb) if df_zb is not None and not df_zb.empty else 0
        if zt_n > 0:
            ratio = round(zb_n / zt_n, 2)
            p = _score_to_percentile(ratio, danger_low=0, warn_low=0.3, good_low=0.6, good_high=1.0, warn_high=1.5, danger_high=2.5)
            indicators.append({
                "name": "炸板/涨停比", "value": ratio, "unit": "", "percentile": p,
                "grade": _grade(p, reverse=True)[0], "gradeColor": _grade(p, reverse=True)[1],
                "logic": "触及涨停但没封住的占比", "danger": ">1.0 大量炸板=情绪退潮"
            })
    except: pass

    # 综合分数
    if indicators:
        valid = [i for i in indicators if isinstance(i["percentile"], (int, float))]
        avg = sum(i["percentile"] for i in valid) / max(len(valid), 1)
        composite = {
            "name": "综合情绪", "value": round(avg, 1), "unit": "P",
            "percentile": round(avg, 1),
            "grade": _grade(avg, reverse=False)[0],
            "gradeColor": _grade(avg, reverse=False)[1],
            "logic": f"基于{len(valid)}个有效指标综合", "danger": ""
        }
    else:
        composite = {"percentile": 50, "grade": "数据受限", "gradeColor": "neutral"}

    return {
        "timestamp": datetime.now().isoformat(),
        "composite": composite,
        "indicators": indicators
    }


# ──────────────────────────────────────────────
# 路由
# ──────────────────────────────────────────────
@app.route("/api/sentiment")
def api_sentiment():
    """市场情绪 (5min memory cache - sentiment_v2 计算耗时 25s+)"""
    import time
    now = time.time()
    if "_sent_cache" in globals() and now - _sent_cache["ts"] < 300:
        return jsonify(_sent_cache["data"])
    data = get_sentiment_index()
    globals()["_sent_cache"] = {"data": data, "ts": now}
    return jsonify(data)


@app.route("/api/us_anomaly")
def api_us_anomaly():
    """美股异动 → A股映射（带缓存，同一天不重复跑）"""
    try:
        return jsonify(run_us_anomaly(force=False))
    except Exception as e:
        # 出错时回退到缓存，保证前端不 500
        cache = load_cache()
        if cache:
            cache["stale"] = True
            cache["error"] = str(e)
            return jsonify(cache)
        return jsonify({
            "timestamp": datetime.now().isoformat(),
            "results": [],
            "movers_count": 0,
            "losers_count": 0,
            "message": f"抓取失败: {e}",
            "error": str(e)
        }), 500


@app.route("/api/us_anomaly/refresh")
def api_us_anomaly_refresh():
    """强制刷新（异步）"""
    run_us_async()
    return jsonify({"status": "started", "message": "刷新任务已启动"})


@app.route("/api/us_anomaly/manual", methods=["POST"])
def api_us_anomaly_manual():
    """手动喂数据：用户输入美股异动"""
    from flask import request
    data = request.get_json(force=True, silent=True) or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "请输入美股异动内容"})
    return jsonify(manual_analyze(text))


# ──────────────────────────────────────────────
# 汪汪队 ETF 监测
# ──────────────────────────────────────────────
WANGWANG_ETFS = [
    ("510300", "华泰柏瑞沪深300ETF"),
    ("510310", "易方达沪深300ETF"),
    ("510330", "华夏沪深300ETF"),
    ("159919", "嘉实沪深300ETF"),
    ("510050", "华夏上证50ETF"),
    ("510500", "南方中证500ETF"),
    ("159915", "易方达创业板ETF"),
    ("512100", "南方中证1000ETF"),
    ("588200", "易方达上证科创板50ETF"),
    ("159845", "华夏中证1000ETF"),
    ("512800", "广发中证1000ETF"),
    ("159629", "富国中证1000ETF"),
]


_ALL_A_CACHE = {"data": None, "ts": 0}
_ALL_A_TTL = 1800  # 30 分钟 (全 A 拉一次要 45s)


def _fetch_all_a_spot():
    """拉全 A 实时行情 (新浪接口, ~5524 只, 45s)"""
    import akshare as ak_local
    import time as _t
    today = date.today().strftime("%Y%m%d")
    # 1) 新浪 (5524 只, 45s) - 重试 3 次, 间隔 5s
    for retry in range(3):
        try:
            df = ak_local.stock_zh_a_spot()
            if df is not None and not df.empty and len(df) > 3000:
                chgs = []
                for _, r in df.iterrows():
                    try:
                        pct = float(str(r.get("涨跌幅", 0)).replace("%", "").replace("+", ""))
                        chgs.append(pct)
                    except:
                        pass
                return {
                    "chgs": chgs, "total": len(chgs),
                    "n_up5": sum(1 for c in chgs if c >= 5),
                    "n_up9": sum(1 for c in chgs if c >= 9),
                    "n_drop5": sum(1 for c in chgs if c <= -5),
                    "n_drop7": sum(1 for c in chgs if c <= -7),
                    "n_drop10": sum(1 for c in chgs if c <= -9.8),
                }
        except:
            if retry < 2:
                _t.sleep(5)
    # 2) 东财 XHR 直接调 (绕过 akshare)
    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": 1, "pz": 8000, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+m:1+t:2",
            "fields": "f2,f3",
            "_": int(_t.time() * 1000)
        }
        for retry in range(2):
            try:
                r = requests.get(url, params=params, headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://quote.eastmoney.com/",
                }, timeout=20)
                if r.status_code == 200 and r.text and r.text[0] == "{":
                    data = r.json()
                    if data.get("data") and data["data"].get("diff"):
                        chgs = [float(d.get("f3", 0)) for d in data["data"]["diff"] if d.get("f3") is not None]
                        if len(chgs) > 3000:
                            return {
                                "chgs": chgs, "total": len(chgs),
                                "n_up5": sum(1 for c in chgs if c >= 5),
                                "n_up9": sum(1 for c in chgs if c >= 9),
                                "n_drop5": sum(1 for c in chgs if c <= -5),
                                "n_drop7": sum(1 for c in chgs if c <= -7),
                                "n_drop10": sum(1 for c in chgs if c <= -9.8),
                            }
            except:
                if retry < 1: _t.sleep(5)
    except:
        pass
    # 3) 兜底: 大幅下跌股池 + 涨停股池 (代表性样本, 用 akshare)
    try:
        df_dc = ak_local.stock_zt_pool_dc_em(date=today)
        df_zt = ak_local.stock_zt_pool_em(date=today)
        drop_n = len(df_dc) if df_dc is not None and not df_dc.empty else 0
        zt_n = len(df_zt) if df_zt is not None and not df_zt.empty else 0
        if drop_n == 0 and zt_n == 0:
            raise Exception("both empty")
        return {
            "chgs": [], "total": 0,
            "n_up5": zt_n, "n_up9": zt_n,
            "n_drop5": drop_n, "n_drop7": drop_n, "n_drop10": 0,
            "fallback": True,
        }
    except:
        # 3.1) 兜底兜底: 用东财 XHR 直接拉跌幅榜 (限频低, 数据全)
        try:
            url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
            # 跌停股池 (东财接口, 给 st 和普通各一档)
            params_zd = {"secid": "1.920305", "fields1": "f1,f2,f3,f4", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58", "klt": "101", "fqt": "1", "end": "20500101", "lmt": "200"}
            # 这里太复杂, 简单用 stock_zt_pool_dc_em 重试
            for retry in range(2):
                df_dc = ak_local.stock_zt_pool_dc_em(date=today)
                if df_dc is not None and not df_dc.empty:
                    drop_n = len(df_dc)
                    return {
                        "chgs": [], "total": 0,
                        "n_up5": 0, "n_up9": 0,
                        "n_drop5": drop_n, "n_drop7": drop_n, "n_drop10": 0,
                        "fallback": True,
                    }
                _t.sleep(3)
        except:
            pass
    return None


def get_all_a_snapshot():
    """全 A 统计 (30min 缓存, 失败时用旧缓存)"""
    now = time.time()
    if _ALL_A_CACHE["data"] and (now - _ALL_A_CACHE["ts"]) < _ALL_A_TTL:
        return _ALL_A_CACHE["data"]
    # 缓存过期: 尝试重拉
    data = _fetch_all_a_spot()
    if data:
        _ALL_A_CACHE["data"] = data
        _ALL_A_CACHE["ts"] = now
        return data
    # 重拉失败: 继续用旧缓存 (最多 4h 兜底)
    if _ALL_A_CACHE["data"] and (now - _ALL_A_CACHE["ts"]) < 14400:
        return _ALL_A_CACHE["data"]
    return None


_WANGWANG_CACHE = {"data": None, "ts": 0}


def get_wangwang_etfs():
    """拉汪汪队ETF行情 + 量比（当日成交 / 5日均量）, 3 分钟缓存"""
    now = time.time()
    if _WANGWANG_CACHE["data"] and now - _WANGWANG_CACHE["ts"] < 180:
        return _WANGWANG_CACHE["data"]
    results = []

    # 拼接代码（带 sh/sz 前缀）
    codes = []
    for code, name in WANGWANG_ETFS:
        prefix = "sh" if code.startswith("5") or code.startswith("6") else "sz"
        codes.append(f"{prefix}{code}")
    sym_str = ",".join(codes)

    # 1) 拉实时行情
    quotes = {}
    try:
        url = f"https://qt.gtimg.cn/q={sym_str}"
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com"}
        r = requests.get(url, headers=headers, timeout=8)
        r.raise_for_status()
        for line in r.text.strip().split("\n"):
            if "v_" not in line: continue
            content = line.split('"')[1] if '"' in line else ""
            if not content: continue
            parts = content.split("~")
            if len(parts) < 33: continue
            code_full = line.split("=")[0].replace("v_", "").strip()
            code = code_full[2:]
            try:
                quotes[code] = {
                    "code": code,
                    "name": next((n for c, n in WANGWANG_ETFS if c == code), code),
                    "price": float(parts[3]),
                    "pct_change": float(parts[32]),
                    "change": float(parts[31]),
                    "volume": float(parts[6]),  # 成交量(手)
                    "turnover": float(parts[37] or 0),
                }
            except: continue
    except Exception as e:
        return {"error": str(e)}

    # 2) 拉5日K线算量比（当日量/5日均量）
    for code, _ in WANGWANG_ETFS:
        if code not in quotes: continue
        prefix = "sh" if code.startswith("5") or code.startswith("6") else "sz"
        try:
            # 腾讯 K 线接口 - 拿5日数据
            kl_url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            kl_params = {"param": f"{prefix}{code},day,,,5,qfq"}
            kr = requests.get(kl_url, params=kl_params, headers=headers, timeout=6)
            kr.raise_for_status()
            data = kr.json()
            # 字段名是 qfqday（前复权日K）
            klines = data.get("data", {}).get(f"{prefix}{code}", {}).get("qfqday", [])
            if klines and len(klines) >= 2:
                # 格式: [date, open, close, high, low, volume]
                # 末条 = 今天，倒数第2条 = 昨天
                try:
                    today_vol = float(klines[-1][5])
                except:
                    today_vol = quotes[code]["volume"]  # 退化
                # 取前4日（不含今天）做均量
                volumes = []
                for k in klines[:-1][-4:]:
                    try:
                        volumes.append(float(k[5]))
                    except: pass
                if volumes:
                    avg_vol_5d = sum(volumes) / len(volumes)
                    vr = today_vol / avg_vol_5d if avg_vol_5d > 0 else None
                    quotes[code]["volume_ratio"] = round(vr, 2) if vr else None
                    quotes[code]["avg_vol_5d"] = int(avg_vol_5d)
                else:
                    quotes[code]["volume_ratio"] = None
            else:
                quotes[code]["volume_ratio"] = None
        except:
            quotes[code]["volume_ratio"] = None

    # 按涨幅排序
    results = sorted(quotes.values(), key=lambda x: x["pct_change"], reverse=True)
    _WANGWANG_CACHE["data"] = results
    _WANGWANG_CACHE["ts"] = now
    return results


@app.route("/api/etf_wangwang")
def api_etf_wangwang():
    """汪汪队 ETF (5min cache)"""
    import time
    now = time.time()
    if "_etf_cache" in globals() and now - _etf_cache["ts"] < 300:
        return jsonify(_etf_cache["data"])
    data = get_wangwang_etfs()
    globals()["_etf_cache"] = {"data": data, "ts": now}
    return jsonify(data)

@app.route("/api/liquidity")
def api_liquidity():
    return jsonify(get_liquidity_score())

@app.route("/api/index")
def api_index():
    return jsonify(get_index_spot())

@app.route("/api/zt/pool")
def api_zt_pool():
    return jsonify(get_zt_pool())

@app.route("/api/zt/zbgc")
def api_zt_zbgc():
    return jsonify(get_zt_zbgc())

@app.route("/api/zt/dtgc")
def api_zt_dtgc():
    return jsonify(get_zt_dtgc())

@app.route("/api/zt/stats")
def api_zt_stats():
    return jsonify(get_zt_stats())

@app.route("/api/futures")
def api_futures():
    return jsonify(get_futures_spot())

@app.route("/api/strategy")
def api_strategy():
    """市场环境 + 阶段 + 策略建议"""
    from sentiment_v2 import get_market_strategy
    return jsonify(get_market_strategy())

# ── 金属异动监控 ─────────────────────────────
@app.route("/api/metals/monitor")
def api_metals_monitor():
    """金属异动监控（z-score + 4 规则 + 加速度 + 共振）(5min cache)"""
    import time
    now = time.time()
    if "_metals_cache" in globals() and now - _metals_cache["ts"] < 300:
        return jsonify(_metals_cache["data"])
    try:
        result = run_metals_monitor()
        # 附加最近 DeepSeek 分析结果
        analyses = load_anomaly_cache()
        for a in result.get("anomalies", []):
            if a["code"] in analyses:
                a["ai_analysis"] = analyses[a["code"]]
        globals()["_metals_cache"] = {"data": result, "ts": now}
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "anomalies": [], "all_evaluations": []}), 500


@app.route("/api/metals/refresh")
def api_metals_refresh():
    """金属数据异步刷新（抓取 + 更新历史）"""
    run_metals_async()
    return jsonify({"message": "金属数据刷新已启动", "status": "started"})


@app.route("/api/metals/analyze")
def api_metals_analyze():
    """对当前异动品种做 DeepSeek 触发分析（异步）"""
    try:
        result = run_metals_monitor()
        anomalies = [a for a in result.get("anomalies", []) if a["level"] in ["L1", "L2"]]
        with open(os.path.join(os.path.dirname(__file__), "metals_config.json"), "r", encoding="utf-8") as f:
            config = _json_metal.load(f)
        batch_metals_async(anomalies, config)
        return jsonify({
            "message": f"已启动 {len(anomalies)} 个异动分析任务",
            "count": len(anomalies),
            "status": "started",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/metals/<code>")
def api_metals_detail(code):
    """单个金属详情：当前价 + 历史 + 评估 + AI分析"""
    try:
        from metals_spot import load_config as _lc, load_history as _lh, fetch_all_metals_spot
        from metals_monitor import evaluate_metal

        config = _lc()
        vol_defaults = config["volatility_defaults"]
        z_thresholds = config["z_thresholds"]
        fallback = config["fallback_thresholds"]

        # 找品种
        metal = next((m for m in config["metals"] if m["code"] == code.upper()), None)
        if not metal:
            return jsonify({"error": f"未找到品种: {code}"}), 404

        # 历史
        history = _lh()
        if code.upper() not in history:
            return jsonify({"error": f"无历史数据: {code}"}), 404

        prices = dict(history[code.upper()].get("prices", {}))

        # 覆盖今日为 spot
        spot = fetch_all_metals_spot()
        from datetime import date
        today = date.today().isoformat()
        if code.upper() in spot and spot[code.upper()].get("price") is not None:
            prices[today] = spot[code.upper()]["price"]

        # 评估
        ev = evaluate_metal(metal, prices, vol_defaults, z_thresholds, fallback)
        if not ev:
            return jsonify({"error": "评估失败"}), 500

        # 附加 AI 分析
        analyses = load_anomaly_cache()
        if code.upper() in analyses:
            ev["ai_analysis"] = analyses[code.upper()]

        # 历史数组（按日期升序）
        sorted_dates = sorted([k for k in prices if not k.endswith("_pct")])
        history_arr = [
            {"date": d, "price": prices[d]} for d in sorted_dates
        ]

        # 计算 60 日曲线：min/max/change
        if len(history_arr) >= 2:
            price_vals = [p["price"] for p in history_arr]
            ev["history_min"] = round(min(price_vals), 2)
            ev["history_max"] = round(max(price_vals), 2)
            ev["history_start"] = round(price_vals[0], 2)
            ev["history_end"] = round(price_vals[-1], 2)
            ev["history_change_pct"] = round((price_vals[-1] - price_vals[0]) / price_vals[0] * 100, 2) if price_vals[0] else 0

        ev["history"] = history_arr
        ev["concept"] = metal.get("concept", "")
        ev["application"] = metal.get("application", "")
        ev["stocks"] = metal.get("stocks", [])

        return jsonify(ev)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/metals/list")
def api_metals_list():
    """返回所有金属列表（用于下拉框）"""
    try:
        from metals_spot import load_config as _lc
        config = _lc()
        items = []
        for m in config["metals"]:
            items.append({
                "code": m["code"],
                "name": m["name"],
                "concept": m.get("concept", ""),
                "application": m.get("application", ""),
                "unit": m.get("unit", ""),
                "stocks": m.get("stocks", []),
            })
        # 按概念分组
        by_concept = {}
        for it in items:
            by_concept.setdefault(it["concept"], []).append(it)
        return jsonify({"items": items, "by_concept": by_concept, "total": len(items)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── 宏观风险仪表盘 ─────────────────────────────
@app.route("/api/macro/monitor")
def api_macro_monitor():
    """宏观风险监控：6 大模块 + z-score + 综合分 + 预警规则"""
    try:
        from macro_monitor import run_macro_monitor
        from macro_anomaly import load_analysis

        result = run_macro_monitor()
        # 附加最近 AI 分析（如果有）
        analysis = load_analysis()
        if analysis:
            result["ai_analysis"] = analysis
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/macro/refresh")
def api_macro_refresh():
    """宏观数据异步刷新"""
    from macro_spot import run_async
    run_async()
    return jsonify({"message": "宏观数据刷新已启动", "status": "started"})


@app.route("/api/macro/analyze")
def api_macro_analyze():
    """触发式 DeepSeek 宏观分析（异步 + 30 分钟冷却）"""
    try:
        from macro_monitor import run_macro_monitor
        from macro_anomaly import analyze_if_needed
        from flask import request

        force = request.args.get("force", "false").lower() == "true"
        result = run_macro_monitor()
        ok, msg = analyze_if_needed(result, force=force)
        if ok is None:
            return jsonify({"message": msg, "skipped": True, "alert_level": result["alert_level"]})
        return jsonify({"message": msg, "alert_level": result["alert_level"], "status": "started"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── 方向池 ──────────────────────────────────────────────
@app.route("/api/direction_pool/list")
def api_direction_pool_list():
    """62 方向池总览
    默认 use_real=False (1s 跑完, strictness 区分 3-5) - 缩略卡用
    ?use_real=true 走真实 8 维评估 (3min, 10min 缓存) - 详情 panel 用
    """
    from flask import request
    use_real = request.args.get("use_real", "false").lower() == "true"

    if not use_real:
        # 快速模式, 无需缓存, 1s
        try:
            from direction_pool import score_all
            results = score_all(use_real=False)
            data = {"timestamp": datetime.now().isoformat(), "total": len(results), "directions": results, "use_real": False}
            return jsonify(data)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # 真实模式: 10min 内存 + 文件缓存
    import time, os
    cache_path = os.path.join(os.path.dirname(__file__), "cache", "direction_pool_cache.json")
    now = time.time()
    mem_key = "_dp_cache_real"
    if mem_key in globals() and now - globals()[mem_key]["ts"] < 600:
        return jsonify(globals()[mem_key]["data"])
    if os.path.exists(cache_path):
        try:
            import json
            with open(cache_path, "r", encoding="utf-8") as f:
                fd = json.load(f)
            if now - fd.get("ts", 0) < 600:
                globals()[mem_key] = fd
                return jsonify(fd["data"])
        except Exception:
            pass
    try:
        from direction_pool import score_all
        results = score_all(use_real=True)
        data = {"timestamp": datetime.now().isoformat(), "total": len(results), "directions": results, "use_real": True}
        record = {"data": data, "ts": now}
        globals()[mem_key] = record
        try:
            import json
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, default=str)
        except Exception as e:
            print(f"[direction_pool cache write] {e}")
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/direction_pool/<code>/details")
def api_direction_pool_details(code):
    """单方向详情 (8 维详细 + 利好利空)"""
    try:
        from direction_pool import get_direction_details
        d = get_direction_details(code)
        if d is None:
            return jsonify({"error": f"未找到方向: {code}"}), 404
        return jsonify(d)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/direction_pool/<code>/raw/<dim>")
def api_direction_pool_raw(code, dim):
    """单维度原始数据 (钻取层)"""
    try:
        from direction_pool import get_direction_raw
        return jsonify(get_direction_raw(code, dim))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── 高切低选股 (方向池热度前 20 × 3/5 日跌幅 Top3) ──
@app.route("/api/high_low_switch")
def api_high_low_switch():
    """高切低: 方向池热度前 20, 找 3/5 日跌幅最大前 3
    返回 6 只候选 (3d+5d 各 3), 含 avg_pct / worst_code / 个股 pct
    """
    try:
        from flask import request
        from high_low_switch import get_high_low_switch
        top_n = int(request.args.get("top_n", 20))
        data = get_high_low_switch(top_n=top_n)
        return _safe_jsonify(data)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ── 市场环境评估 (新模式) ──────────────────────────────
import json as _json

class _NumpyEncoder(_json.JSONEncoder):
    def default(self, obj):
        try:
            return str(obj)
        except Exception:
            return None

def _safe_jsonify(d):
    from flask import Response
    return Response(_json.dumps(d, ensure_ascii=False, default=str), mimetype="application/json")

@app.route("/api/market/regime")
def api_market_regime():
    """综合: 阶段判定 + 仓位建议 + 反转分 + 老登综合分"""
    try:
        from market_regime import get_market_regime
        r = get_market_regime()
        return _safe_jsonify(r)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/market/support_resistance")
def api_market_support_resistance():
    """模块 A: 强支撑 + 强压力位 (8 维度评分)"""
    try:
        from market_regime import get_market_regime
        r = get_market_regime()
        return _safe_jsonify({
            "timestamp": r.get("timestamp"),
            "current_price": r.get("current_price"),
            "supports": r.get("supports", []),
            "resistances": r.get("resistances", []),
            "strong_supports_count": r.get("strong_supports_count", 0),
            "strong_resistances_count": r.get("strong_resistances_count", 0),
            "total_weight": 17,
            "strong_threshold": 11,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/market/reversal_factors")
def api_market_reversal_factors():
    """模块 B: 反转因子 (11 因子)"""
    try:
        from market_regime import get_market_regime
        r = get_market_regime()
        return _safe_jsonify(r.get("reversal", {}))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/market/old_school")
def api_market_old_school():
    """老登老灯: 14 指标 (5 类)"""
    try:
        from market_regime import get_market_regime
        r = get_market_regime()
        return _safe_jsonify(r.get("old_school", {}))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/")
def index():
    return app.send_static_file("index.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 仪表盘启动中... http://localhost:{port}")
    print("📡 数据源：DollarLiquidity + 腾讯财经（直连，绕过akshare）")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)

