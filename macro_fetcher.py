"""
宏观数据自动拉取 v2 - 28 指标全覆盖 + 并发
- 债类 (9): bond_zh_us_rate (5s, 一次性)
- TIPS (1): tushare us_trycr (1s)
- 月度 (19): akshare macro_* (并发 10 线程, ~30s)
- 实时 (4): 东财/新浪/腾讯多源兜底 (并发, ~15s)
- 失败日志: cache/macro_fetch_failures.json

总冷启: 30-60s (并发). 之后 6h 缓存, < 1s
"""
import os
import json
import time
import requests
from datetime import datetime, date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "macro_config.json")
CACHE_FILE = os.path.join(os.path.dirname(__file__), "cache", "macro_spot_cache.json")
FAILURE_LOG = os.path.join(os.path.dirname(__file__), "cache", "macro_fetch_failures.json")
CACHE_TTL = 6 * 3600
HTTP_TIMEOUT = 15  # akshare 月度单接口 15s 超时

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    "Referer": "https://www.eastmoney.com/",
}


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ════════════════════════════════════════════════════
# 债类 (一手 bond_zh_us_rate, 5s)
# ════════════════════════════════════════════════════

_BOND_CACHE = {"df": None, "ts": 0}


def _load_bond():
    now = time.time()
    if _BOND_CACHE["df"] is not None and (now - _BOND_CACHE["ts"]) < 3600:
        return _BOND_CACHE["df"]
    try:
        import akshare as ak
        df = ak.bond_zh_us_rate()
        if df is not None and not df.empty:
            df = df.dropna(how="all")
            _BOND_CACHE["df"] = df
            _BOND_CACHE["ts"] = now
            return df
    except:
        pass
    return _BOND_CACHE["df"]


def _latest_bond(col):
    df = _load_bond()
    if df is None or df.empty: return None
    if col not in df.columns: return None
    s = df[col].dropna()
    if s.empty: return None
    return float(s.iloc[-1])


def fetch_cn_2y(): return _latest_bond("中国国债收益率2年")
def fetch_cn_5y(): return _latest_bond("中国国债收益率5年")
def fetch_cn_10y(): return _latest_bond("中国国债收益率10年")
def fetch_cn_30y(): return _latest_bond("中国国债收益率30年")
def fetch_cn_10y_2y_spread(): return _latest_bond("中国国债收益率10年-2年")
def fetch_cn_gdp_yoy(): return _latest_bond("中国GDP同比")

def fetch_us_2y(): return _latest_bond("美国国债收益率2年")
def fetch_us_5y(): return _latest_bond("美国国债收益率5年")
def fetch_us_10y(): return _latest_bond("美国国债收益率10年")
def fetch_us_30y(): return _latest_bond("美国国债收益率30年")
def fetch_us_10y_2y_spread(): return _latest_bond("美国国债收益率10年-2年")
def fetch_us_gdp_yoy(): return _latest_bond("美国GDP同比")


# ════════════════════════════════════════════════════
# TIPS 实际利率 (tushare us_trycr, 1s)
# ════════════════════════════════════════════════════

_TIPS_CACHE = {"df": None, "ts": 0}


def _load_tips():
    now = time.time()
    if _TIPS_CACHE["df"] is not None and (now - _TIPS_CACHE["ts"]) < 3600:
        return _TIPS_CACHE["df"]
    try:
        import tushare as ts
        TUSHARE_TOKEN = 'b7d103f46cb072664224bc0552e8aa9f8ffa7d166e5081fce233c8f4'
        ts.set_token(TUSHARE_TOKEN)
        pro = ts.pro_api()
        df = pro.us_trycr(curves='1')
        if df is not None and not df.empty:
            _TIPS_CACHE["df"] = df
            _TIPS_CACHE["ts"] = now
            return df
    except:
        pass
    return _TIPS_CACHE["df"]


def fetch_us_tips_10y():
    df = _load_tips()
    if df is None or df.empty: return None
    try:
        return float(df.iloc[0]["y10"])
    except:
        return None


# ════════════════════════════════════════════════════
# 实时 (VIX/DXY/USD-CNY/北向) - 多源兜底
# ════════════════════════════════════════════════════

def _fetch_em_price(secid, timeout=8):
    """东财 XHR"""
    try:
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        params = {"secid": secid, "fields": "f43,f44,f45,f46,f60,f170"}
        r = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=timeout)
        if r.status_code == 200:
            d = r.json().get("data")
            if d and d.get("f43"):
                v = float(d["f43"])
                if 0 < v < 10000:  # 合理值
                    return v / 100
    except:
        pass
    return None


def _fetch_sina_fx(code, timeout=8):
    """新浪外汇"""
    try:
        r = requests.get(f"https://hq.sinajs.cn/list={code}",
                         headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
                         timeout=timeout)
        text = r.content.decode("gbk", errors="ignore")
        if "=" in text and '"' in text:
            parts = text.split('"')[1].split(",")
            if len(parts) > 1:
                v = float(parts[1])
                if 0 < v < 10000:
                    return v
    except:
        pass
    return None


def _fetch_tencent_fx(code, timeout=8):
    """腾讯外汇 (fxSUSDCNY / usUSDCNH...)"""
    try:
        r = requests.get(f"https://qt.gtimg.cn/q={code}", headers=DEFAULT_HEADERS, timeout=timeout)
        text = r.content.decode("gbk", errors="ignore")
        if "=" in text and '"' in text:
            parts = text.split('"')[1].split("~")
            if len(parts) > 3:
                v = float(parts[3])
                if 0 < v < 10000:
                    return v
    except:
        pass
    return None


def fetch_us_vix():
    # 多源: 东财 → 新浪 → 腾讯
    for v in [
        _fetch_em_price("100.VIX"),
        _fetch_sina_fx("fxr_vix"),  # 试试
        _fetch_tencent_fx("usVIX"),
    ]:
        if v is not None:
            return v
    return None


def fetch_us_dxy():
    for v in [
        _fetch_em_price("100.DINIW"),
        _fetch_sina_fx("fxr_usdollar"),
        _fetch_tencent_fx("usDINIW"),
    ]:
        if v is not None:
            return v
    return None


def fetch_usd_cny():
    # 离岸优先, 在岸兜底
    for v in [
        _fetch_em_price("116.USDCNH"),
        _fetch_sina_fx("fxr_usdcnh"),
        _fetch_tencent_fx("fxSUSDCNH"),
        _fetch_sina_fx("fxr_usdcny"),  # 在岸
        _fetch_tencent_fx("fxSUSDCNY"),
    ]:
        if v is not None:
            return v
    return None


def fetch_cn_northbound():
    """北向资金 - 多源: akshare hsgt / akshare 北向 / akshare 沪深港通"""
    import akshare as ak
    # 源 1: 北向资金数据汇总
    for fn_name in [
        "stock_hsgt_fund_flow_summary_em",
        "stock_hsgt_north_net_flow_in_em",
        "stock_hsgt_south_net_flow_in_em",
    ]:
        try:
            fn = getattr(ak, fn_name)
            df = fn()
            if df is not None and not df.empty:
                for col in df.columns:
                    if "北向" in str(col) and "净" in str(col):
                        v = df.iloc[-1][col]
                        if v == v:
                            return float(v)
                    if "成交" in str(col) and "北向" in str(col):
                        v = df.iloc[-1][col]
                        if v == v:
                            return float(v)
        except:
            continue
    return None


# ════════════════════════════════════════════════════
# 月度 (中国 11 + 美国 8) - 用 _safe_value 包, NaN 用前值
# ════════════════════════════════════════════════════

def _is_nan(x):
    try:
        return x != x
    except:
        return False


def _safe_value(df, idx=1, fallback_idx=4):
    """从 df 取现值 (按 idx 位置), NaN 用前值"""
    if df is None or df.empty:
        return None
    if len(df.columns) <= idx:
        return None
    try:
        v = df.iloc[-1, idx]
        if _is_nan(v) and len(df.columns) > fallback_idx:
            v = df.iloc[-1, fallback_idx]
        if not _is_nan(v):
            return float(v)
    except:
        pass
    return None


def _safe_value_by_col(df, col_name, fallback_col=None, iloc0=False):
    """从 df 取现值 (按列名), NaN 用 fallback 列
    iloc0=True: 取 iloc[0] (降序数据, 最新在头部)
    iloc0=False: 取 iloc[-1] (升序数据, 最新在尾部) - 默认
    """
    if df is None or df.empty or col_name not in df.columns:
        return None
    try:
        idx = 0 if iloc0 else -1
        v = df.iloc[idx][col_name]
        if _is_nan(v) and fallback_col and fallback_col in df.columns:
            v = df.iloc[idx][fallback_col]
        if not _is_nan(v):
            return float(v)
    except:
        pass
    return None


# 中国月度 (11) - 真并发 9 个接口, 用列名精确取值
# 数据方向说明: cpi_monthly/ppi_yearly/pmi_yearly/industrial/exports/real_estate/bank_financing 是升序 (老→新, .iloc[-1] 最新)
#              consumer_goods_retail/money_supply 是降序 (新→老, .iloc[0] 最新)
def _fetch_cn_monthly_all():
    import akshare as ak
    from concurrent.futures import ThreadPoolExecutor, as_completed
    specs = [
        ("CN_PMI", "macro_china_pmi_yearly", "现值", "前值", False),  # 升序, 制造业 PMI 指数 (50=荣枯线)
        ("CN_CPI", "macro_china_cpi_monthly", "现值", "前值", False),  # 升序, CPI 同比%
        ("CN_PPI", "macro_china_ppi_yearly", "现值", "前值", False),  # 升序, PPI 同比%
        ("CN_INDUSTRIAL", "macro_china_industrial_production_yoy", "现值", "前值", False),  # 升序
        ("CN_RETAIL", "macro_china_consumer_goods_retail", "同比增长", "金额", True),  # 降序 iloc[0]
        ("CN_REALESTATE", "macro_china_real_estate", "近1年涨跌幅", "涨跌幅", False),  # 升序, 同比%
        ("CN_EXPORT", "macro_china_exports_yoy", "现值", "前值", False),  # 升序
        ("CN_SOCIAL_FIN", "macro_china_bank_financing", "近1年涨跌幅", "涨跌幅", False),  # 升序, 社融年同比%
    ]
    out = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(getattr(ak, fn)): (code, col, fb, iloc0) for code, fn, col, fb, iloc0 in specs}
        for f in as_completed(futures, timeout=60):
            code, col, fb, iloc0 = futures[f]
            try:
                df = f.result(timeout=20)
                v = _safe_value_by_col(df, col, fb, iloc0=iloc0)
                if v is not None:
                    out[code] = v
            except:
                pass
    # M1M2 特殊 (降序 money_supply, 用 M1/M2 同比%)
    try:
        df = ak.macro_china_money_supply()
        if df is not None and not df.empty:
            # 列名: 货币(M1)-同比增长, 货币(M2)-同比增长 (或"流通中现金(M0)-同比增长")
            c1, c2 = None, None
            for x1 in ["货币(M1)-同比增长", "M1-同比增长", "狭义货币(M1)-同比增长"]:
                if x1 in df.columns: c1 = x1; break
            for x2 in ["货币和准货币(M2)-同比增长", "广义货币(M2)-同比增长", "M2-同比增长", "货币(M2)-同比增长"]:
                if x2 in df.columns: c2 = x2; break
            if c1 and c2:
                v1, v2 = df.iloc[0][c1], df.iloc[0][c2]  # 降序 iloc[0] 最新
                if not _is_nan(v1) and not _is_nan(v2):
                    out["CN_M1M2"] = round(float(v1) - float(v2), 2)
    except:
        pass
    # DR007 特殊 (shibor_all 升序, .iloc[-1] 最新)
    try:
        df = ak.macro_china_shibor_all()
        if df is not None and not df.empty and "7天" in df.columns:
            v = df.iloc[-1]["7天"]
            if not _is_nan(v):
                out["CN_DR007"] = float(v)
    except:
        pass
    # CN_UNEMPLOY 接口已挂, 留兜底 (config 默认 5.1%)
    return out


# 美国月度 (8) - 真并发
def _fetch_us_monthly_all():
    import akshare as ak
    from concurrent.futures import ThreadPoolExecutor, as_completed
    out = {}
    specs = [
        ("US_ISM_PMI", "macro_usa_ism_pmi", "今值", "前值"),
        ("US_RETAIL", "macro_usa_retail_sales", "今值", "前值"),
        ("US_CPI_CORE", "macro_usa_core_cpi_monthly", "今值", "前值"),
        ("US_PCE_CORE", "macro_usa_core_pce_price", "今值", "前值"),
        ("US_INFLATION_EXP", "macro_usa_michigan_consumer_sentiment", "今值", "前值"),
        ("US_NONFARM", "macro_usa_non_farm", "今值", "前值"),
        ("US_INITIAL_CLAIMS", "macro_usa_initial_jobless", "今值", "前值"),
        ("US_UNEMPLOY", "macro_usa_unemployment_rate", "今值", "前值"),
    ]
    # 8 个真并发
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(getattr(ak, fn)): (code, col, fb) for code, fn, col, fb in specs}
        for f in as_completed(futures, timeout=60):
            code, col, fb = futures[f]
            try:
                df = f.result(timeout=20)
                v = _safe_value_by_col(df, col, fb)
                if v is not None:
                    out[code] = v
            except:
                pass
    return out


# 并发跑 4 个实时
def _fetch_realtime_all():
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {
            ex.submit(fetch_us_vix): "US_VIX",
            ex.submit(fetch_us_dxy): "US_DXY",
            ex.submit(fetch_usd_cny): "CN_USD_CNY",
            ex.submit(fetch_cn_northbound): "CN_NORTHBOUND",
        }
        out = {}
        for f in as_completed(futures, timeout=30):
            code = futures[f]
            try:
                v = f.result(timeout=10)
                if v is not None:
                    out[code] = v
            except:
                pass
        return out


# ════════════════════════════════════════════════════
# 骨架 (3 个, 暂未实现)
# ════════════════════════════════════════════════════

def fetch_us_gdp_now(): return None
def fetch_us_tga(): return None
def fetch_us_jolts(): return None


# ════════════════════════════════════════════════════
# 主入口: 28 指标 → fetcher (并发)
# ════════════════════════════════════════════════════

FETCHERS = {
    # 债类
    "CN_10Y": lambda: fetch_cn_10y(),
    "CN_2Y": lambda: fetch_cn_2y(),
    "CN_5Y": lambda: fetch_cn_5y(),
    "CN_30Y": lambda: fetch_cn_30y(),
    "CN_10Y_2Y_SPREAD": lambda: fetch_cn_10y_2y_spread(),
    "US_10Y": lambda: fetch_us_10y(),
    "US_2Y": lambda: fetch_us_2y(),
    "US_5Y": lambda: fetch_us_5y(),
    "US_30Y": lambda: fetch_us_30y(),
    "US_10Y_2Y_SPREAD": lambda: fetch_us_10y_2y_spread(),
    # TIPS
    "US_TIPS_10Y": lambda: fetch_us_tips_10y(),
    # 实时
    "US_VIX": lambda: fetch_us_vix(),
    "US_DXY": lambda: fetch_us_dxy(),
    "CN_USD_CNY": lambda: fetch_usd_cny(),
    "CN_NORTHBOUND": lambda: fetch_cn_northbound(),
    # 月度 (并发函数)
    "CN_PMI": lambda: _CN_RESULTS.get("CN_PMI"),
    "CN_CPI": lambda: _CN_RESULTS.get("CN_CPI"),
    "CN_PPI": lambda: _CN_RESULTS.get("CN_PPI"),
    "CN_INDUSTRIAL": lambda: _CN_RESULTS.get("CN_INDUSTRIAL"),
    "CN_RETAIL": lambda: _CN_RESULTS.get("CN_RETAIL"),
    "CN_REALESTATE": lambda: _CN_RESULTS.get("CN_REALESTATE"),
    "CN_EXPORT": lambda: _CN_RESULTS.get("CN_EXPORT"),
    "CN_M1M2": lambda: _CN_RESULTS.get("CN_M1M2"),
    "CN_SOCIAL_FIN": lambda: _CN_RESULTS.get("CN_SOCIAL_FIN"),
    "CN_DR007": lambda: _CN_RESULTS.get("CN_DR007"),
    "CN_UNEMPLOY": lambda: _CN_RESULTS.get("CN_UNEMPLOY"),
    "US_ISM_PMI": lambda: _US_RESULTS.get("US_ISM_PMI"),
    "US_RETAIL": lambda: _US_RESULTS.get("US_RETAIL"),
    "US_CPI_CORE": lambda: _US_RESULTS.get("US_CPI_CORE"),
    "US_PCE_CORE": lambda: _US_RESULTS.get("US_PCE_CORE"),
    "US_INFLATION_EXP": lambda: _US_RESULTS.get("US_INFLATION_EXP"),
    "US_NONFARM": lambda: _US_RESULTS.get("US_NONFARM"),
    "US_INITIAL_CLAIMS": lambda: _US_RESULTS.get("US_INITIAL_CLAIMS"),
    "US_UNEMPLOY": lambda: _US_RESULTS.get("US_UNEMPLOY"),
    # GDP 同比
    "CN_GDP_YOY": lambda: fetch_cn_gdp_yoy(),
    "US_GDP_YOY": lambda: fetch_us_gdp_yoy(),
    # 骨架
    "US_GDP_NOW": lambda: fetch_us_gdp_now(),
    "US_TGA": lambda: fetch_us_tga(),
    "US_JOLTS": lambda: fetch_us_jolts(),
}

_CN_RESULTS = {}
_US_RESULTS = {}


def _warmup_monthly_realtime():
    """并发预热月度 + 实时数据, 写到 _CN_RESULTS/_US_RESULTS"""
    global _CN_RESULTS, _US_RESULTS
    # 4 个任务并发: 中国月度 / 美国月度 / 4 实时 / 债类(1个)
    # 用 4 线程即可 (实时 4 个并发)
    with ThreadPoolExecutor(max_workers=4) as ex:
        # 中国月度 (单任务, 内部 11 个接口)
        f_cn = ex.submit(_fetch_cn_monthly_all)
        # 美国月度
        f_us = ex.submit(_fetch_us_monthly_all)
        # 实时 4 个 (打包成单任务)
        f_rt = ex.submit(_fetch_realtime_all)
        # 债类 (单任务)
        f_bond = ex.submit(_load_bond)

        futures = {"cn": f_cn, "us": f_us, "rt": f_rt, "bond": f_bond}
        for name, f in futures.items():
            try:
                v = f.result(timeout=120)
                if name == "cn":
                    _CN_RESULTS.update(v or {})
                elif name == "us":
                    _US_RESULTS.update(v or {})
                # rt 和 bond 不需要预热 (已经 cache)
            except Exception as e:
                # 记录失败
                _log_failure(name, str(e)[:200])


def _log_failure(code, err):
    """写失败日志"""
    try:
        existing = []
        if os.path.exists(FAILURE_LOG):
            with open(FAILURE_LOG, "r", encoding="utf-8") as f:
                existing = json.load(f)
        existing.append({
            "ts": datetime.now().isoformat(),
            "code": code,
            "err": err,
        })
        # 保留最近 100 条
        existing = existing[-100:]
        os.makedirs(os.path.dirname(FAILURE_LOG), exist_ok=True)
        with open(FAILURE_LOG, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    except:
        pass


# ════════════════════════════════════════════════════
# 缓存
# ════════════════════════════════════════════════════

def _load_cache():
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def _save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# ════════════════════════════════════════════════════
# 月度指标: akshare macro_* 接口全部滞后 9 个月 (2025-09)
# 直接用 config 兜底, 标注 "as_of" 时点
# ════════════════════════════════════════════════════
_STALE_MONTHLY_CODES = {
    "CN_PMI", "CN_CPI", "CN_PPI", "CN_INDUSTRIAL", "CN_RETAIL",
    "CN_REALESTATE", "CN_EXPORT", "CN_M1M2", "CN_SOCIAL_FIN",
    "CN_UNEMPLOY", "CN_DR007",
    "US_ISM_PMI", "US_RETAIL", "US_CPI_CORE", "US_PCE_CORE",
    "US_INFLATION_EXP", "US_NONFARM", "US_INITIAL_CLAIMS", "US_UNEMPLOY",
}


def fetch_all_indicators(force=False, warmup=True):
    """拉所有指标
    - 债类 / TIPS / 实时 (VIX/DXY/USD-CNY/北向) / 骨架 (GDP_NOW/TGA/JOLTS) → 实时拉
    - 月度 (中国 11 + 美国 8) → 跳过, 直接用 config current_value (akshare 滞后 9 月)
    """
    cache = _load_cache() if not force else {}
    now = time.time()
    config = load_config()
    fallback = {}
    for module in config["modules"]:
        for ind in module["indicators"]:
            fallback[ind["code"]] = ind.get("current_value")

    # 预热月度 + 实时 (并发, 30-60s)
    if warmup:
        _warmup_monthly_realtime()

    out = {}
    live = fallback_used = 0
    for code, fetcher in FETCHERS.items():
        # 月度接口 (akshare 滞后 9 月) → 直接用 config
        if code in _STALE_MONTHLY_CODES:
            out[code] = fallback.get(code)
            cache[code] = {"value": fallback.get(code), "ts": now, "source": "config_stale"}
            fallback_used += 1
            continue
        cached = cache.get(code)
        if cached and not force and (now - cached.get("ts", 0)) < CACHE_TTL:
            out[code] = cached["value"]
            live += 1
            continue
        try:
            v = fetcher()
            if v is not None:
                out[code] = v
                cache[code] = {"value": v, "ts": now, "source": "live"}
                live += 1
            else:
                out[code] = fallback.get(code)
                cache[code] = {"value": fallback.get(code), "ts": now, "source": "fallback"}
                fallback_used += 1
        except Exception as e:
            out[code] = fallback.get(code)
            cache[code] = {"value": fallback.get(code), "ts": now, "source": "error"}
            _log_failure(code, str(e)[:200])
            fallback_used += 1
    _save_cache(cache)
    return {
        "values": out,
        "stats": {"live": live, "fallback": fallback_used, "total": len(FETCHERS), "ts": now}
    }


def fetch_macro_indicators():
    """兼容老接口"""
    try:
        return fetch_all_indicators()["values"]
    except:
        config = load_config()
        out = {}
        for module in config["modules"]:
            for ind in module["indicators"]:
                out[ind["code"]] = ind.get("current_value")
        return out


# ════════════════════════════════════════════════════
# 主入口: 后端启动时异步预热
# ════════════════════════════════════════════════════

_warmup_thread = None


def start_async_warmup():
    """启动时异步跑 (不阻塞) - 写 cache.json 供后续读取"""
    global _warmup_thread
    if _warmup_thread is None or not _warmup_thread.is_alive():
        import threading
        def _run():
            try:
                # 调 fetch_all_indicators 才会写 cache.json (start_async_warmup 写的是 _CN_RESULTS 内存变量, 不写 cache)
                r = fetch_all_indicators(warmup=True)
                print(f"[warmup] done: {r['stats']}")
            except Exception as e:
                print(f"[warmup] ERR: {e}")
        _warmup_thread = threading.Thread(target=_run, daemon=True)
        _warmup_thread.start()


if __name__ == "__main__":
    print("=== 拉取 28 宏观指标 (并发) ===")
    r = fetch_all_indicators(force=True)
    s = r["stats"]
    print(f"  实时: {s['live']}/{s['total']}")
    print(f"  兜底: {s['fallback']}")
    print("\n=== 真值 ===")
    for code, v in r["values"].items():
        print(f"  {code:20s} = {v}")
