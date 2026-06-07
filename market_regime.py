"""
市场环境评估引擎 v2 (修正版)
============================
模块 A: 压力/支撑位强度评分 (8 维度, 0/1 二分, 权重和 17)
模块 B: 反转因子 (11 因子, -1~+1 加权)
老登老灯: 14 指标 (含 0-10 打分 + 5 档预警)
"""
import os
import sys
import time
import json
import requests
import sqlite3
from datetime import datetime, date, timedelta
sys.stdout.reconfigure(encoding='utf-8')

import tushare as ts
import numpy as np
import pandas as pd
import akshare as ak

TUSHARE_TOKEN = 'b7d103f46cb072664224bc0552e8aa9f8ffa7d166e5081fce233c8f4'
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════
# 模块 A: 压力/支撑位强度评分 (8 维度, 0/1 二分, 总权重 17)
# ═══════════════════════════════════════════════════════════════════════════
SUPPORT_RESISTANCE_WEIGHTS = {
    "volume_persist": 3,        # 1. 量能持续
    "test_count": 2,            # 2. 历史测试次数
    "ma_hold": 2,               # 3. 均线守住
    "fibonacci": 2,             # 4. 黄金分割
    "classic_pattern": 2,       # 5. 经典形态
    "gap": 1,                   # 6. 缺口
    "chip_density": 3,          # 7. 筹码密集度
    "kline_reversal": 2,        # 8. K线反转形态
}
TOTAL_WEIGHT_A = sum(SUPPORT_RESISTANCE_WEIGHTS.values())  # 17
STRONG_THRESHOLD_A = 11  # 强位 ≥ 11/17 (65%)


def _score_volume_persist(volumes):
    """1. 量能持续 (0/1): 近 10 日均量 > 近 30 日均量 × 1.2"""
    if len(volumes) < 30: return 0
    avg_10 = np.mean(volumes[-10:])
    avg_30 = np.mean(volumes[-30:])
    return 1 if avg_10 > avg_30 * 1.2 else 0


def _score_test_count(closes, current):
    """2. 历史测试次数 (0/1): 近 60 日内碰过当前价 ±2% ≥ 2 次"""
    if len(closes) < 60: return 0
    last_60 = closes[-60:]
    touches = sum(1 for p in last_60 if abs(p - current) / current < 0.02)
    return 1 if touches >= 2 else 0


def _score_ma_hold(price, ma20, ma60, ma250):
    """3. 均线守住 (0/1): 距 任一重要均线 < 3%"""
    for ma in [ma20, ma60, ma250]:
        if ma and abs(price - ma) / price < 0.03:
            return 1
    return 0


def _score_fibonacci(high_60d, low_60d, current):
    """4. 黄金分割 (0/1): 在 0.382/0.5/0.618 附近 ±2%"""
    diff = high_60d - low_60d
    if diff <= 0: return 0
    for fib in [0.382, 0.500, 0.618]:
        fib_price = low_60d + fib * diff
        if abs(current - fib_price) / current < 0.02:
            return 1
    return 0


def _score_classic_pattern(closes, current):
    """5. 经典形态 (0/1): 头肩/双头/双底 颈线附近 ±5%"""
    if len(closes) < 120: return 0
    highs, lows = [], []
    for i in range(10, len(closes) - 5):
        if closes[i] == max(closes[i-5:i+5]):
            highs.append(closes[i])
        if closes[i] == min(closes[i-5:i+5]):
            lows.append(closes[i])
    # 双头颈线
    if len(highs) >= 2 and abs(highs[-1] - highs[-2]) / current < 0.05:
        return 1
    # 双底颈线
    if len(lows) >= 2 and abs(lows[-1] - lows[-2]) / current < 0.05:
        return 1
    return 0


def _score_gap(closes):
    """6. 缺口 (0/1): 近 30 日有未回补的跳空 ±2%"""
    if len(closes) < 30: return 0
    last_30 = closes[-30:]
    for i in range(1, len(last_30)):
        gap_pct = abs(last_30[i] - last_30[i-1]) / last_30[i-1]
        if gap_pct > 0.02:
            # 检查是否回补
            gap_mid = (last_30[i] + last_30[i-1]) / 2
            if last_30[i] > last_30[i-1]:  # 向上跳空
                filled = any(p < gap_mid for p in last_30[i+1:])
            else:  # 向下跳空
                filled = any(p > gap_mid for p in last_30[i+1:])
            if not filled: return 1
    return 0


def _score_chip_density(price, ma60):
    """7. 筹码密集度 (0/1): 当前价 相对 60 日 MA 偏离 < 5%"""
    if ma60 <= 0: return 0
    return 1 if abs(price - ma60) / ma60 < 0.05 else 0


def _score_kline_reversal(closes, opens, current):
    """8. K线反转形态 (0/1): 最近 3 根 K 线有锤子/十字星 在该位置 ±2%"""
    if len(closes) < 5: return 0
    for i in range(-1, -4, -1):
        c = closes[i]
        o = opens[i]
        if abs(c - current) / current < 0.02:
            body = abs(c - o) / max(o, 1e-9)
            if body < 0.005:  # 十字星
                return 1
            # 锤子线 (实体 < 1.5%)
            if body < 0.015:
                return 1
    return 0


def score_support_resistance(closes, volumes, opens, current, ma20, ma60, ma250, high_60d, low_60d, kind="support"):
    """评分一个支撑位或压力位 (0/1 二分 + 权重 = 0-17)"""
    scores = {}
    scores["volume_persist"] = _score_volume_persist(volumes)
    scores["test_count"] = _score_test_count(closes, current)
    scores["ma_hold"] = _score_ma_hold(current, ma20, ma60, ma250)
    scores["fibonacci"] = _score_fibonacci(high_60d, low_60d, current)
    scores["classic_pattern"] = _score_classic_pattern(closes, current)
    scores["gap"] = _score_gap(closes)
    scores["chip_density"] = _score_chip_density(current, ma60)
    scores["kline_reversal"] = _score_kline_reversal(closes, opens, current)
    total = sum(scores[k] * SUPPORT_RESISTANCE_WEIGHTS[k] for k in SUPPORT_RESISTANCE_WEIGHTS)
    return {"scores": scores, "total": total, "is_strong": total >= STRONG_THRESHOLD_A, "kind": kind, "price": current}


def find_support_resistance(closes, volumes, opens):
    """找候选支撑/压力位 (基于近 60 日高低 + 均线 + 整数关口)"""
    if len(closes) < 60: return [], []
    current = closes[-1]
    ma20 = np.mean(closes[-20:])
    ma60 = np.mean(closes[-60:])
    ma250 = np.mean(closes[-250:]) if len(closes) >= 250 else ma60
    high_60d = max(closes[-60:])
    low_60d = min(closes[-60:])
    levels = [
        (high_60d, "resistance", "60日高点"),
        (low_60d, "support", "60日低点"),
        (ma60, "support" if current > ma60 else "resistance", "60日均线"),
        (ma250, "support" if current > ma250 else "resistance", "250日均线(牛熊线)"),
    ]
    step = 100 if current > 1000 else 50 if current > 500 else 10
    for i in [-2, -1, 1, 2]:
        level = round(current / step) * step + i * step
        if level != current and abs(level - current) / current < 0.1:
            kind = "resistance" if level > current else "support"
            levels.append((level, kind, f"整数关口{level}"))
    supports, resistances = [], []
    for price, kind, label in levels:
        s = score_support_resistance(closes, volumes, opens, price, ma20, ma60, ma250, high_60d, low_60d, kind)
        s["label"] = label
        if kind == "support": supports.append(s)
        else: resistances.append(s)
    supports.sort(key=lambda x: -x["total"])
    resistances.sort(key=lambda x: -x["total"])
    return supports, resistances


# ═══════════════════════════════════════════════════════════════════════════
# 模块 B: 反转因子 (11 因子)
# ═══════════════════════════════════════════════════════════════════════════
REVERSAL_FACTOR_WEIGHTS = {
    "trend_higher_lows": 0.10,
    "volume_buy_signal": 0.10,
    "ice_point": 0.10,
    "valuation_low": 0.12,
    "erp_high": 0.10,
    "macro_low_risk": 0.10,
    "trend_lower_highs": -0.10,
    "volume_sell_signal": -0.10,
    "overheat": -0.10,
    "valuation_high_hold": -0.12,
    "yield_curve_inverted": -0.06,
}


def _trend_structure(closes, lookback=120):
    if len(closes) < lookback: return {"higher_lows": False, "lower_highs": False}
    recent = closes[-lookback:]
    highs, lows = [], []
    for i in range(10, len(recent) - 5):
        if recent[i] == max(recent[i-5:i+5]):
            highs.append((i, recent[i]))
        if recent[i] == min(recent[i-5:i+5]):
            lows.append((i, recent[i]))
    higher_lows = False
    lower_highs = False
    if len(lows) >= 2:
        higher_lows = lows[-1][1] > lows[-2][1]
    if len(highs) >= 2:
        lower_highs = highs[-1][1] < highs[-2][1]
    return {"higher_lows": higher_lows, "lower_highs": lower_highs}


def compute_reversal_factors(closes, volumes, df_idx, df_margin, df_zt_dt, pe_pct):
    factors = {}
    ts_data = _trend_structure(closes)
    factors["trend_higher_lows"] = {"triggered": ts_data["higher_lows"], "value": "一底比一底高" if ts_data["higher_lows"] else "未触发", "polarity": "+", "name": "趋势-底抬高"}
    factors["trend_lower_highs"] = {"triggered": ts_data["lower_highs"], "value": "一顶比一顶低" if ts_data["lower_highs"] else "未触发", "polarity": "-", "name": "趋势-顶降低"}

    if len(closes) >= 20 and len(volumes) >= 20:
        avg_20 = np.mean(volumes[-20:])
        last_vol = volumes[-1]
        last_close = closes[-1]
        prev_close = closes[-2]
        buy_signal = bool(last_vol > avg_20 * 1.3 and last_close > prev_close)
        sell_signal = bool(last_vol > avg_20 * 1.3 and last_close < prev_close)
    else:
        buy_signal = sell_signal = False
    factors["volume_buy_signal"] = {"triggered": buy_signal, "value": "放量+长阳" if buy_signal else "未触发", "polarity": "+", "name": "量价-买入"}
    factors["volume_sell_signal"] = {"triggered": sell_signal, "value": "放量+长阴" if sell_signal else "未触发", "polarity": "-", "name": "量价-卖出"}

    zt = df_zt_dt.get("zt", 0)
    dt = df_zt_dt.get("dt", 0)
    margin_chg_5d = 0
    if df_margin is not None and not df_margin.empty and len(df_margin) >= 5:
        margin_chg_5d = (df_margin['rzye'].iloc[-1] - df_margin['rzye'].iloc[-5]) / df_margin['rzye'].iloc[-5] * 100
    ice_point = (dt > zt * 1.5) and (margin_chg_5d < -3)
    overheat = (zt > dt * 3) and (margin_chg_5d > 3)
    factors["ice_point"] = {"triggered": ice_point, "value": f"跌停{dt}只+融资{margin_chg_5d:+.1f}%" if ice_point else "未触发", "polarity": "+", "name": "冰点信号"}
    factors["overheat"] = {"triggered": overheat, "value": f"涨停{zt}只+融资{margin_chg_5d:+.1f}%" if overheat else "未触发", "polarity": "-", "name": "顶部过热"}

    val_low = bool(pe_pct is not None and pe_pct < 20)
    val_high = bool(pe_pct is not None and pe_pct > 80)
    factors["valuation_low"] = {"triggered": val_low, "value": f"PE {pe_pct:.0f}%分位" if pe_pct is not None and val_low else ("数据缺失" if pe_pct is None else "未触发"), "polarity": "+", "name": "估值低位"}
    factors["valuation_high_hold"] = {"triggered": val_high, "value": f"PE {pe_pct:.0f}%分位" if pe_pct is not None and val_high else ("数据缺失" if pe_pct is None else "未触发"), "polarity": "-", "name": "估值高位"}

    pe = df_idx.get('pe_ttm') or 15
    erp = (1.0 / pe) * 100 - 1.85
    erp_high = erp > 5
    factors["erp_high"] = {"triggered": erp_high, "value": f"ERP {erp:.2f}%" if erp_high else "未触发", "polarity": "+", "name": "ERP高位"}

    macro_risk = None
    try:
        with open(os.path.join(CACHE_DIR, "macro_score.json"), "r", encoding="utf-8") as f:
            macro_risk = json.load(f)
    except Exception:
        pass
    if macro_risk:
        comp_z = macro_risk.get("composite_z", 0)
        macro_low = comp_z < -1.0
        factors["macro_low_risk"] = {"triggered": macro_low, "value": f"宏观z={comp_z:.2f}" if macro_low else "未触发", "polarity": "+", "name": "宏观低风险"}
    else:
        factors["macro_low_risk"] = {"triggered": False, "value": "无数据", "polarity": "+", "name": "宏观低风险"}

    factors["yield_curve_inverted"] = {"triggered": False, "value": "无数据", "polarity": "-", "name": "利差倒挂"}

    total = 0
    for k, f in factors.items():
        if f["triggered"]:
            total += REVERSAL_FACTOR_WEIGHTS[k]
    return {"factors": factors, "score": round(total, 3)}


# ═══════════════════════════════════════════════════════════════════════════
# 老登老灯: 14 指标 (含 0-10 打分 + 5 档预警)
# ═══════════════════════════════════════════════════════════════════════════
def _score_and_alert(name, value, score, alert_type="normal"):
    """score (0-10) + 5 档预警"""
    if score >= 8: alert = "🚨 极强"
    elif score >= 6: alert = "⚠️ 偏强"
    elif score >= 4: alert = "🟡 中性"
    elif score >= 2: alert = "⚠️ 偏弱"
    else: alert = "🚨 极弱"
    return {"name": name, "value": value, "score": round(score, 1), "alert": alert, "alert_type": alert_type}


def compute_old_school(closes, volumes, df_idx, df_margin, df_zt_dt):
    """14 个老登指标, 每个含 score (0-10) + 5 档预警"""
    indicators = []
    current = closes[-1]
    pe = df_idx.get('pe_ttm') or 15
    pb = df_idx.get('pb') or 1.5
    pe_pct = df_idx.get('pe_pct')  # 可能是 None
    pb_pct = df_idx.get('pb_pct')
    pe_pct_safe = pe_pct if pe_pct is not None else 50
    pb_pct_safe = pb_pct if pb_pct is not None else 50

    # A. 趋势 (3)
    ma20 = np.mean(closes[-20:])
    ma60 = np.mean(closes[-60:])
    ma250 = np.mean(closes[-250:]) if len(closes) >= 250 else ma60
    if current > ma250:
        score = min(10, 5 + (current - ma250) / ma250 * 30)
    else:
        score = max(0, 5 - (ma250 - current) / ma250 * 30)
    indicators.append(_score_and_alert("牛熊线(200日MA)", f"现{current:.0f} vs {ma250:.0f}", score))
    if current > ma20 > ma60: ma_score = 8
    elif current > ma20: ma_score = 6
    elif current > ma60: ma_score = 4
    elif current < ma20 < ma60: ma_score = 2
    else: ma_score = 1
    indicators.append(_score_and_alert("MA排列(20/60日)", f"现{current:.0f} 20:{ma20:.0f} 60:{ma60:.0f}", ma_score))
    if len(closes) >= 30:
        ema12 = pd.Series(closes).ewm(span=12, adjust=False).mean().iloc[-1]
        ema26 = pd.Series(closes).ewm(span=26, adjust=False).mean().iloc[-1]
        dif = ema12 - ema26
        macd_score = max(0, min(10, 5 + dif / ma60 * 100))
        indicators.append(_score_and_alert("MACD", f"DIF={dif:.2f}", macd_score))
    else:
        indicators.append(_score_and_alert("MACD", "数据不足", 5))

    # B. 估值 (5)
    pe_str = f"{pe_pct:.0f}% (PE={pe:.1f})" if pe_pct is not None else "数据缺失"
    pb_str = f"{pb_pct:.0f}% (PB={pb:.2f})" if pb_pct is not None else "数据缺失"
    indicators.append(_score_and_alert("PE 5年分位", pe_str, pe_pct_safe / 10))
    indicators.append(_score_and_alert("PB 5年分位", pb_str, pb_pct_safe / 10))
    growth = df_idx.get('growth_yoy', 50) or 50
    peg = pe / growth if growth > 0 else 99
    peg_score = max(0, min(10, 10 - peg * 4))
    indicators.append(_score_and_alert("PEG", f"{peg:.2f} (增速{growth}%)", peg_score))
    erp = (1.0 / pe) * 100 - 1.85
    erp_score = max(0, min(10, erp * 1.5))
    indicators.append(_score_and_alert("ERP风险溢价", f"{erp:.2f}%", erp_score))
    buffett = 75  # hardcode
    buffett_score = max(0, min(10, (buffett - 60) / 4))
    indicators.append(_score_and_alert("巴菲特指标", f"{buffett:.0f}% (估)", buffett_score))

    # C. 资金 (3)
    if df_margin is not None and not df_margin.empty and len(df_margin) >= 20:
        chg_20d = (df_margin['rzye'].iloc[-1] - df_margin['rzye'].iloc[-20]) / df_margin['rzye'].iloc[-20] * 100
        margin_score = max(0, min(10, 5 - chg_20d * 0.8))
        indicators.append(_score_and_alert("融资余额(20日)", f"{chg_20d:+.1f}%", margin_score))
    else:
        indicators.append(_score_and_alert("融资余额(20日)", "无数据", 5))
    indicators.append(_score_and_alert("北向资金(30日)", "见后端", 5))
    indicators.append(_score_and_alert("基金仓位", "季报延迟", 5))

    # D. 情绪 (2)
    zt = df_zt_dt.get("zt", 0)
    dt = df_zt_dt.get("dt", 0)
    zt_dt_ratio = zt / max(dt, 1)
    if zt_dt_ratio > 3: sent_score = 2
    elif zt_dt_ratio > 1.5: sent_score = 4
    elif zt_dt_ratio > 0.5: sent_score = 6
    else: sent_score = 8
    indicators.append(_score_and_alert("涨跌停比", f"{zt}:{dt} = {zt_dt_ratio:.1f}", sent_score))
    new_high_low_ratio = zt / max(zt + dt, 1) * 100
    indicators.append(_score_and_alert("新高新低比", f"估 {new_high_low_ratio:.0f}%", new_high_low_ratio / 10))

    # E. 宏观 (1)
    macro_risk = None
    try:
        with open(os.path.join(CACHE_DIR, "macro_score.json"), "r", encoding="utf-8") as f:
            macro_risk = json.load(f)
    except Exception:
        pass
    if macro_risk:
        comp_z = macro_risk.get("composite_z", 0)
        macro_score = max(0, min(10, 5 + comp_z * 2))
        indicators.append(_score_and_alert("宏观风险综合", f"z={comp_z:.2f}", macro_score))
    else:
        indicators.append(_score_and_alert("宏观风险综合", "无数据", 5))

    avg = np.mean([x["score"] for x in indicators])
    return {"indicators": indicators, "composite_score": round(avg, 1)}


# ═══════════════════════════════════════════════════════════════════════════
# 主函数: 4 指数独立评估
# ═══════════════════════════════════════════════════════════════════════════
_ZT_DT_CACHE = {"data": None, "ts": 0}
def _load_zt_dt():
    now = time.time()
    if _ZT_DT_CACHE["data"] and now - _ZT_DT_CACHE["ts"] < 86400:
        return _ZT_DT_CACHE["data"]
    try:
        df = pro.limit_list_d(trade_date=datetime.now().strftime("%Y%m%d"))
        zt = len(df[df['limit'] == 'U']) if df is not None and not df.empty and 'limit' in df.columns else 0
        dt = len(df[df['limit'] == 'D']) if df is not None and not df.empty and 'limit' in df.columns else 0
        result = {"zt": zt, "dt": dt}
        _ZT_DT_CACHE["data"] = result
        _ZT_DT_CACHE["ts"] = now
        return result
    except Exception:
        return {"zt": 0, "dt": 0}


# 4 个目标指数
INDICES = [
    {"key": "shanghai", "name": "上证指数", "code": "000001.SH", "icon": "🟦"},
    {"key": "shenzhen", "name": "深成指", "code": "399001.SZ", "icon": "🟧"},
    {"key": "chinext", "name": "创业板指", "code": "399006.SZ", "icon": "🟩"},
    {"key": "kechuang", "name": "科创综指", "code": "000680.SH", "icon": "🟪"},
]

# 10 个概念指数 (已剔除 - ETF 代理不准, 数据源不稳)
CONCEPT_INDICES = []


def _get_index_valuation(code, name):
    """统一获取指数 PE/PB + 10年分位
    数据源:
    - 000001/399001/399006 用 tushare index_dailybasic (10年2772天)
    - 000688 科创50 tushare 不支持 → akshare (1个月20天)
    返回: (pe_ttm, pb, pe_pct, pb_pct, source, sample_count, last_date)
    """
    # tushare 支持的指数 (3 个): 用 10 年数据
    if code in ["000001.SH", "399001.SZ", "399006.SZ"]:
        try:
            df_v = pro.index_dailybasic(ts_code=code, start_date='20150101', end_date='20260603', fields='trade_date,pe,pe_ttm,pb')
            if df_v is None or df_v.empty:
                return None, None, None, None, "tushare_empty", 0, None
            df_v = df_v.sort_values('trade_date').reset_index(drop=True)
            latest_v = df_v.iloc[-1]
            pe_ttm = float(latest_v.get('pe_ttm')) if pd.notna(latest_v.get('pe_ttm')) else None
            pb = float(latest_v.get('pb')) if pd.notna(latest_v.get('pb')) else None
            pe_series = df_v['pe_ttm'].dropna()
            pe_pct = (pe_series < pe_ttm).sum() / len(pe_series) * 100 if pe_ttm is not None and len(pe_series) > 0 else None
            pb_pct = (df_v['pb'].dropna() < pb).sum() / len(df_v['pb'].dropna()) * 100 if pb is not None and len(df_v['pb'].dropna()) > 0 else None
            return pe_ttm, pb, pe_pct, pb_pct, "tushare_10y", len(pe_series), latest_v.get('trade_date')
        except Exception as e:
            return None, None, None, None, f"tushare_err: {str(e)[:30]}", 0, None
    # 科创综指 (000680.SH) 用 akshare (1 个月)
    else:
        sym = code.split('.')[0]
        try:
            df = ak.stock_zh_index_value_csindex(symbol=sym)
            if df is None or df.empty:
                return None, None, None, None, "akshare_empty", 0, None
            df = df.sort_values('日期').reset_index(drop=True)
            latest = df.iloc[-1]
            pe_ttm = float(latest['市盈率2']) if pd.notna(latest.get('市盈率2')) else None
            pe_series = df['市盈率2'].dropna()
            pe_pct = (pe_series < pe_ttm).sum() / len(pe_series) * 100 if pe_ttm is not None and len(pe_series) > 0 else None
            return pe_ttm, None, pe_pct, None, "akshare_1m", len(pe_series), latest['日期']
        except Exception as e:
            return None, None, None, None, f"akshare_err: {str(e)[:30]}", 0, None


def _evaluate_one_index(code, name, key, icon, df_margin, df_zt_dt):
    """评估一个指数"""
    try:
        # K 线 (统一用 tushare)
        df_k = pro.index_daily(ts_code=code, start_date='20210101', end_date='20260603')
        if df_k is None or df_k.empty:
            return {"key": key, "name": name, "icon": icon, "error": "无K线数据"}
        df_k = df_k.sort_values('trade_date').reset_index(drop=True)
        closes = df_k['close'].tolist()
        volumes = df_k['vol'].tolist() if 'vol' in df_k.columns else [0] * len(closes)
        opens = df_k['open'].tolist() if 'open' in df_k.columns else closes
        # 估值 (混合数据源: 中证用 akshare, 深证用 tushare)
        pe_ttm, pb, pe_pct, pb_pct, val_source, sample_count, val_date = _get_index_valuation(code, name)

        # 强支撑/压力
        supports, resistances = find_support_resistance(closes, volumes, opens)
        strong_supports = [s for s in supports if s["is_strong"]]
        strong_resistances = [r for r in resistances if r["is_strong"]]
        # 反转分
        reversal = compute_reversal_factors(closes, volumes, {"pe_ttm": pe_ttm, "pb": pb, "growth_yoy": 50}, df_margin, df_zt_dt, pe_pct)
        # 老登
        old_school = compute_old_school(closes, volumes, {"pe_ttm": pe_ttm, "pb": pb, "pe_pct": pe_pct, "pb_pct": pb_pct, "growth_yoy": 50}, df_margin, df_zt_dt)

        # 阶段判定
        current = closes[-1]
        above_resistance = any(current > r["price"] for r in strong_resistances) if strong_resistances else False
        below_support = any(current < s["price"] for s in strong_supports) if strong_supports else False
        if strong_supports and strong_resistances and not above_resistance and not below_support:
            regime, regime_en, position_pct = "震荡市 🟡", "sideways", 50
        elif above_resistance:
            regime, regime_en, position_pct = "牛市 🟢", "bull", 80
        elif below_support:
            regime, regime_en, position_pct = "熊市 🔴", "bear", 20
        elif strong_supports and not strong_resistances:
            regime, regime_en, position_pct = "弱势整理 🟡", "weak", 35
        else:
            regime, regime_en, position_pct = "强势整理 🟢", "strong", 65
        # 反转分调整
        r_score = reversal["score"]
        if r_score >= 0.5:
            position_pct = min(100, position_pct + 20)
            confirm = "反转支持: 加仓"
        elif r_score <= -0.5:
            position_pct = max(0, position_pct - 20)
            confirm = "反转反对: 减仓"
        else:
            confirm = "反转中性"

        return {
            "key": key,
            "name": name,
            "icon": icon,
            "code": code,
            "current_price": round(float(current), 2),
            "pe_ttm": round(pe_ttm, 2) if pe_ttm is not None else None,
            "pe_pct": round(pe_pct, 1) if pe_pct is not None else None,
            "pb": round(pb, 2) if pb is not None else None,
            "val_source": val_source,
            "sample_count": sample_count,
            "val_date": str(val_date) if val_date else None,
            "regime": regime,
            "regime_en": regime_en,
            "position_pct": position_pct,
            "confirm": confirm,
            "supports": supports[:5],
            "resistances": resistances[:5],
            "strong_supports_count": len(strong_supports),
            "strong_resistances_count": len(strong_resistances),
            "reversal": reversal,
            "old_school": old_school,
        }
    except Exception as e:
        import traceback
        print(f"[EVAL ERR] {name} ({code}): {e}")
        traceback.print_exc()
        return {"key": key, "name": name, "icon": icon, "error": str(e)[:200]}


def _evaluate_concept_index(spec, df_margin, df_zt_dt):
    """完整版概念指数评估 (用 ETF 代理 K 线, 申万 PE)"""
    key = spec["key"]
    name = spec["name"]
    code = spec["code"]
    icon = spec["icon"]
    if code == "000000" or not code:
        return {"key": key, "name": name, "icon": icon, "error": "无 ETF 代理"}
    # 1) ETF K 线
    df_k = pro.fund_daily(ts_code=code, start_date='20210101', end_date='20260603')
    if df_k is None or df_k.empty:
        return {"key": key, "name": name, "icon": icon, "error": "无 K 线"}
    df_k = df_k.sort_values('trade_date').reset_index(drop=True)
    closes = df_k['close'].tolist()
    volumes = df_k['vol'].tolist() if 'vol' in df_k.columns else [0] * len(closes)
    opens = df_k['open'].tolist() if 'open' in df_k.columns else closes
    # 2) PE/PB (申万行业)
    pe_ttm, pb, pe_pct, pe_source = None, None, None, None
    try:
        df_all = ak.sw_index_third_info()
        df_all.columns = ['code', 'name', 'parent', 'count', 'pe_static', 'pe_ttm', 'pb', 'div_yield']
        keywords_map = {
            "semi": ["半导体", "801080", "801081"],
            "comm": ["通信", "801770"],
            "bess": ["储能", "锂电", "电池"],
            "space": ["航天", "军工", "航空装备"],
            "cpo": ["光通信", "光模块", "CPO", "通信设备"],
            "grid": ["电网", "电力设备"],
            "quantum": ["量子"],
            "batt": ["电池"],
            "wind": ["风电"],
            "drug": ["创新药", "化学制药", "生物制品"],
        }
        kws = keywords_map.get(key, [key])
        def _match(n):
            return any(kw in str(n) for kw in kws)
        matched = df_all[df_all['name'].apply(_match)]
        if not matched.empty:
            pe_series = pd.to_numeric(matched['pe_ttm'], errors='coerce').dropna()
            pb_series = pd.to_numeric(matched['pb'], errors='coerce').dropna()
            pe_ttm = float(pe_series.mean()) if not pe_series.empty else None
            pb = float(pb_series.mean()) if not pb_series.empty else None
            pe_pct = (pe_series < pe_ttm).sum() / len(pe_series) * 100 if pe_ttm else None
            pe_source = f"申万{len(matched)}子"
    except Exception:
        pass
    # 3) 强支撑/压力
    try:
        supports, resistances = find_support_resistance(closes, volumes, opens)
    except Exception:
        supports, resistances = [], []
    strong_supports = [s for s in supports if s["is_strong"]]
    strong_resistances = [r for r in resistances if r["is_strong"]]
    # 4) 反转分 (简化, 用 K 线 + 全市场资金)
    try:
        reversal = compute_reversal_factors(closes, volumes, {"pe_ttm": pe_ttm, "pb": pb, "growth_yoy": 50}, df_margin, df_zt_dt, pe_pct or 50)
    except Exception:
        reversal = {"score": 0, "factors": {}}
    # 5) 老登
    try:
        old_school = compute_old_school(closes, volumes, {"pe_ttm": pe_ttm, "pb": pb, "pe_pct": pe_pct, "pb_pct": None, "growth_yoy": 50}, df_margin, df_zt_dt)
    except Exception:
        old_school = {"composite_score": 5, "indicators": []}
    # 6) 阶段判定
    current = closes[-1]
    above_resistance = any(current > r["price"] for r in strong_resistances) if strong_resistances else False
    below_support = any(current < s["price"] for s in strong_supports) if strong_supports else False
    if strong_supports and strong_resistances and not above_resistance and not below_support:
        regime, regime_en, position_pct = "震荡市 🟡", "sideways", 50
    elif above_resistance:
        regime, regime_en, position_pct = "牛市 🟢", "bull", 80
    elif below_support:
        regime, regime_en, position_pct = "熊市 🔴", "bear", 20
    elif strong_supports and not strong_resistances:
        regime, regime_en, position_pct = "弱势整理 🟡", "weak", 35
    else:
        regime, regime_en, position_pct = "强势整理 🟢", "strong", 65
    r_score = reversal.get("score", 0)
    if r_score >= 0.5:
        position_pct = min(100, position_pct + 20)
    elif r_score <= -0.5:
        position_pct = max(0, position_pct - 20)
    return {
        "key": key,
        "name": name,
        "icon": icon,
        "code": code,
        "current_price": round(float(current), 3),
        "pe_ttm": round(pe_ttm, 1) if pe_ttm else None,
        "pe_pct": round(pe_pct, 1) if pe_pct else None,
        "pe_source": pe_source,
        "regime": regime,
        "regime_en": regime_en,
        "position_pct": position_pct,
        "supports": supports[:3],
        "resistances": resistances[:3],
        "strong_supports_count": len(strong_supports),
        "strong_resistances_count": len(strong_resistances),
        "reversal": reversal,
        "old_school": old_school,
    }


_MR_CACHE = {"data": None, "ts": 0}
def get_market_regime():
    now = time.time()
    if _MR_CACHE["data"] and now - _MR_CACHE["ts"] < 300:
        return _MR_CACHE["data"]
    df_margin = pro.margin(start_date='20260415', end_date='20260603', exchange_id='SSE')
    if df_margin is not None and not df_margin.empty:
        df_margin = df_margin.sort_values('trade_date').reset_index(drop=True)
    df_zt_dt = _load_zt_dt()

    indices_data = []
    for spec in INDICES:
        r = _evaluate_one_index(spec["code"], spec["name"], spec["key"], spec["icon"], df_margin, df_zt_dt)
        indices_data.append(r)

    return {
        "timestamp": datetime.now().isoformat(),
        "indices": indices_data,
    }
    _MR_CACHE["data"] = result
    _MR_CACHE["ts"] = time.time()
    return result


if __name__ == "__main__":
    r = get_market_regime()
    for idx in r['indices']:
        print(f"\n{idx['icon']} {idx['name']} ({idx.get('code','')}) @ {idx.get('current_price','?')}")
        if 'error' in idx:
            print(f"  ERR: {idx['error']}")
            continue
        print(f"  阶段: {idx['regime']}, 仓位: {idx['position_pct']}%, PE={idx['pe_ttm']} ({idx['pe_pct']}%分位)")
        print(f"  强支撑 {idx['strong_supports_count']} / 强压力 {idx['strong_resistances_count']}")
        print(f"  反转分: {idx['reversal']['score']:+.2f}, 老登: {idx['old_school']['composite_score']:.1f}/10")
        if idx['supports']:
            print(f"  Top支撑: {idx['supports'][0]['label']} @ {idx['supports'][0]['price']:.0f} [{idx['supports'][0]['total']}/17]")
        if idx['resistances']:
            print(f"  Top压力: {idx['resistances'][0]['label']} @ {idx['resistances'][0]['price']:.0f} [{idx['resistances'][0]['total']}/17]")
