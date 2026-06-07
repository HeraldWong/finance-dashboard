"""
宏观数据抓取（简化版 - 使用当前参考值 + 模拟 6 月历史）
真实数据源接入：东方财富 / FRED / 同花顺
"""
import os
import json
import time
import random
import requests
from datetime import datetime, date, timedelta
from threading import Thread

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "macro_config.json")
HISTORY_FILE = os.path.join(os.path.dirname(__file__), "cache", "macro_history.json")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://www.eastmoney.com/",
}


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ── 真实抓取（用 macro_fetcher, 28 指标全拉 + 6h 缓存） ─────────────────────────────
def fetch_macro_indicators():
    """
    拉 28 个宏观指标实时值
    - 7 高频: tushare us_tycr + 东方财富 (DXY/VIX/人民币/中国 10Y)
    - 21 月度: 骨架 + 回退 config
    缓存 6h
    """
    try:
        from macro_fetcher import fetch_all_indicators
        result = fetch_all_indicators(warmup=False)  # 不重跑 warmup (读 cache)
        return result["values"]
    except Exception as e:
        # 兜底: config current_value
        config = load_config()
        out = {}
        for module in config["modules"]:
            for ind in module["indicators"]:
                out[ind["code"]] = ind.get("current_value")
        return out


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return generate_seed_history()
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def generate_seed_history():
    """
    为 28 个指标生成 180 天历史（6 个月移动平均用）
    """
    config = load_config()
    history = {}
    today = date.today()

    # 每个指标波动 σ（基于类别）
    vol_map = {
        "CN_PMI": 0.6, "CN_INDUSTRIAL": 1.5, "CN_RETAIL": 1.2, "CN_REALESTATE": 4.0,
        "CN_EXPORT": 6.0, "US_ISM_PMI": 0.8, "US_RETAIL": 0.4, "US_GDP_NOW": 0.5,
        "CN_CPI": 0.4, "CN_PPI": 0.8, "US_CPI_CORE": 0.3, "US_PCE_CORE": 0.2,
        "US_INFLATION_EXP": 0.2, "CN_M1M2": 1.5, "CN_SOCIAL_FIN": 0.5, "CN_DR007": 0.1,
        "US_TGA": 0.1, "US_DXY": 0.8, "CN_10Y": 0.06, "US_10Y": 0.08,
        "US_2Y": 0.10, "US_10Y_2Y_SPREAD": 0.10, "US_TIPS_10Y": 0.08,
        "US_VIX": 2.5, "CN_USD_CNY": 0.02, "CN_NORTHBOUND": 50.0,
        "CN_UNEMPLOY": 0.1, "US_NONFARM": 5.0, "US_INITIAL_CLAIMS": 1.5, "US_JOLTS": 30.0,
    }

    random.seed(123)
    for module in config["modules"]:
        for ind in module["indicators"]:
            code = ind["code"]
            current = ind.get("current_value", 100)
            sigma = vol_map.get(code, 1.0)
            history[code] = {"name": ind["name"], "unit": "%", "values": {}}
            for i in range(180, 0, -1):
                d = (date.fromordinal(today.toordinal() - i)).isoformat()
                # 模拟：基础值 + 趋势 + 噪声
                trend = (i / 180) * random.gauss(0, sigma * 0.3)
                noise = random.gauss(0, sigma)
                v = current + trend + noise
                history[code]["values"][d] = round(v, 4)
            # 今天 = 当前值
            history[code]["values"][today.isoformat()] = current

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    return history


def update_history(spot_data):
    """把今日 spot 写入 history"""
    history = load_history()
    today = date.today().isoformat()
    for code, v in spot_data.items():
        if code not in history:
            history[code] = {"name": code, "unit": "", "values": {}}
        if v is not None:
            history[code]["values"][today] = v

    # 截断只保留 365 天
    for code in history:
        if "values" in history[code]:
            sorted_dates = sorted(history[code]["values"].keys())
            for old in sorted_dates[:-365]:
                history[code]["values"].pop(old, None)
    save_history(history)
    return history


# ── 6 月移动平均预期 ─────────────────────────────
def get_expected_6m(history, code):
    """
    过去 6 个月的均值 + σ（用做 z-score 预期和标准差）
    返回: (expected_mean, expected_std) 或 (None, None)
    """
    if code not in history:
        return None, None
    values = history[code].get("values", {})
    sorted_dates = sorted(values.keys())
    if len(sorted_dates) < 30:
        return None, None
    # 取过去 180 天（排除今天，避免数据泄漏）
    recent = [values[d] for d in sorted_dates[-181:-1] if values[d] is not None]
    if len(recent) < 30:
        return None, None
    from statistics import mean, stdev
    mu = mean(recent)
    sigma = stdev(recent) if len(recent) > 1 else 0.1
    if sigma < 0.001:
        sigma = 0.001
    return mu, sigma


# ── 主入口 ─────────────────────────────
def fetch_and_update():
    spot = fetch_macro_indicators()
    history = update_history(spot)
    return spot, history


def run_macro_spot(force=False):
    return fetch_and_update()


# ── 异步 ─────────────────────────────
_thread = None
def run_async():
    global _thread
    if _thread and _thread.is_alive():
        return False
    def _do():
        try:
            fetch_and_update()
        except Exception as e:
            print(f"[macro_spot] async error: {e}")
    _thread = Thread(target=_do, daemon=True)
    _thread.start()
    return True


if __name__ == "__main__":
    spot, hist = fetch_and_update()
    print(f"宏观指标: {len(spot)}")
    for code, v in list(spot.items())[:5]:
        mu, sigma = get_expected_6m(hist, code)
        if mu is not None:
            z = (v - mu) / sigma
            print(f"  {code}: {v} (预期 {mu:.2f}, σ={sigma:.2f}, z={z:+.2f})")
