"""
金属异动监控引擎
- 4 条价格异动规则（单日 / 3日 / 5日 / 10日）
- z-score 动态阈值（按品种波动率等级 + 60日滚动σ）
- 加速度 = 当日涨幅 - 昨日涨幅
- 量价配合（成交量 - 暂以价格波动幅度作为代理）
- 跨市场共振（同概念 + A股映射）
- 双向监控（涨 + 跌）
- 智能分级频率（L1=15min, L2=1h, L3=1day）
"""
import os
import json
import math
from datetime import datetime, date, timedelta
from statistics import mean, stdev
from metals_spot import load_config, load_history, fetch_all_metals_spot


# ── 工具：计算统计指标 ─────────────────────────────
def calc_returns(prices_dict):
    """从 {date: price} 字典返回 [(date, return_pct)] 列表（按日期升序）"""
    sorted_dates = sorted([k for k in prices_dict if not k.endswith("_pct")])
    if len(sorted_dates) < 2:
        return []
    rets = []
    for i in range(1, len(sorted_dates)):
        prev = prices_dict[sorted_dates[i-1]]
        cur = prices_dict[sorted_dates[i]]
        if prev and prev > 0 and cur is not None:
            r = (cur - prev) / prev * 100
            rets.append((sorted_dates[i], r))
    return rets


def calc_z_score(value, history_values):
    """z-score = (value - mean) / std"""
    if len(history_values) < 5:
        return None
    mu = mean(history_values)
    sigma = stdev(history_values)
    if sigma == 0:
        return 0
    return (value - mu) / sigma


def get_recent_pct(prices_dict, days):
    """获取 N 日累计涨幅 (%)"""
    sorted_dates = sorted([k for k in prices_dict if not k.endswith("_pct")])
    if len(sorted_dates) < days + 1:
        return None
    cur = prices_dict[sorted_dates[-1]]
    prev = prices_dict[sorted_dates[-1 - days]]
    if not cur or not prev or prev == 0:
        return None
    return (cur - prev) / prev * 100


# ── 单品种评估 ─────────────────────────────
def evaluate_metal(metal, prices_dict, vol_defaults, z_thresholds, fallback):
    """
    评估单个品种的异动情况
    返回: {
        "code", "name", "concept", "application", "stocks",
        "price", "daily_pct", "pct_3d", "pct_5d", "pct_10d",
        "z_daily", "z_3d", "z_5d", "z_10d",
        "acceleration", "z_accel",
        "level": "L1"|"L2"|"L3"|"normal"|"fallback",
        "direction": "up"|"down",
        "anomaly_type": "...",
        "msg": "..."
    }
    """
    code = metal["code"]
    if not prices_dict:
        return None

    sorted_dates = sorted([k for k in prices_dict if not k.endswith("_pct")])
    if len(sorted_dates) < 2:
        return None

    cur = prices_dict[sorted_dates[-1]]
    prev = prices_dict[sorted_dates[-2]] if len(sorted_dates) >= 2 else cur
    daily_pct = (cur - prev) / prev * 100 if prev and prev > 0 else 0

    pct_3d = get_recent_pct(prices_dict, 3)
    pct_5d = get_recent_pct(prices_dict, 5)
    pct_10d = get_recent_pct(prices_dict, 10)

    # 历史日收益率
    rets = calc_returns(prices_dict)
    if len(rets) >= 5:
        daily_rets = [r for _, r in rets[-60:]]  # 最近 60 个交易日
    else:
        daily_rets = []

    # z-score（动态）
    vol_class = metal.get("volatility_class", "industrial")
    default_sigma = vol_defaults.get(vol_class, 2.0)  # 百分比

    if len(daily_rets) >= 30:
        # 30 天后用真实滚动 σ（约束：不低于默认 50%，不高于默认 200%）
        real_sigma = stdev(daily_rets)
        sigma = max(default_sigma * 0.5, min(default_sigma * 2.0, real_sigma))
        mu = mean(daily_rets)
    else:
        # 冷启动用预设 σ
        sigma = default_sigma
        mu = 0

    # 防止 σ = 0
    if sigma < 0.1:
        sigma = 0.1

    z_daily = (daily_pct - mu) / sigma if sigma else 0

    # 多周期 z-score（用最近 N 日累计）
    z_3d = None
    z_5d = None
    z_10d = None
    if pct_3d is not None and len(daily_rets) >= 6:
        # 估算 3 日累计的 σ = daily_sigma * sqrt(3)
        sigma_3d = sigma * math.sqrt(3)
        z_3d = (pct_3d - mu * 3) / sigma_3d if sigma_3d else 0
    if pct_5d is not None and len(daily_rets) >= 10:
        sigma_5d = sigma * math.sqrt(5)
        z_5d = (pct_5d - mu * 5) / sigma_5d if sigma_5d else 0
    if pct_10d is not None and len(daily_rets) >= 15:
        sigma_10d = sigma * math.sqrt(10)
        z_10d = (pct_10d - mu * 10) / sigma_10d if sigma_10d else 0

    # 加速度
    acceleration = None
    z_accel = None
    if len(daily_rets) >= 2:
        acceleration = daily_pct - daily_rets[-2]  # 今日 - 昨日
        if len(daily_rets) >= 10:
            accel_history = [daily_rets[i] - daily_rets[i-1] for i in range(1, len(daily_rets))]
            z_accel = calc_z_score(acceleration, accel_history[-30:])

    # 综合评级
    direction = "up" if daily_pct > 0 else ("down" if daily_pct < 0 else "flat")
    level = "normal"
    anomaly_type = None
    msg = ""

    if len(daily_rets) < 5:
        # 历史数据不足 - 用固定 fallback 阈值
        abs_pct = abs(daily_pct)
        if abs_pct >= fallback["daily_pct_l1"]:
            level = "L1"
            anomaly_type = "fallback_daily"
        elif abs_pct >= fallback["daily_pct_l2"]:
            level = "L2"
            anomaly_type = "fallback_daily"
        elif abs_pct >= fallback["daily_pct_l3"]:
            level = "L3"
            anomaly_type = "fallback_daily"
    else:
        # z-score 评估
        z_abs = abs(z_daily)
        if z_abs >= z_thresholds["L1"]:
            level = "L1"
            anomaly_type = "z_daily"
        elif z_abs >= z_thresholds["L2"]:
            level = "L2"
            anomaly_type = "z_daily"
        elif z_abs >= z_thresholds["L3"]:
            level = "L3"
            anomaly_type = "z_daily"

    # 多周期加权（如果某周期触发了更高级别，升级）
    if pct_3d is not None and abs(pct_3d) > 12 and level != "L1":
        level = "L1"
        anomaly_type = "z_3d"
    elif pct_5d is not None and abs(pct_5d) > 15 and level != "L1":
        level = "L1"
        anomaly_type = "z_5d"
    elif pct_10d is not None and abs(pct_10d) > 22 and level != "L1":
        level = "L1"
        anomaly_type = "z_10d"

    # 加速度信号
    if acceleration is not None:
        if acceleration > 3 and daily_pct > 5 and level in ["L1", "L2"]:
            anomaly_type = "acceleration_up"
            msg = f"加速上涨 (加速度+{acceleration:.1f}%)"
        elif acceleration < -3 and daily_pct < -5 and level in ["L1", "L2"]:
            anomaly_type = "acceleration_down"
            msg = f"加速下跌 (加速度{acceleration:.1f}%)"

    if not msg:
        if direction == "up":
            msg = f"涨幅 {daily_pct:+.1f}% (z={z_daily:.1f}σ)"
        elif direction == "down":
            msg = f"跌幅 {daily_pct:+.1f}% (z={z_daily:.1f}σ)"
        else:
            msg = f"持平"

    return {
        "code": code,
        "name": metal["name"],
        "unit": metal.get("unit", ""),
        "concept": metal.get("concept", ""),
        "application": metal.get("application", ""),
        "stocks": metal.get("stocks", []),
        "volatility_class": vol_class,
        "price": round(cur, 2),
        "daily_pct": round(daily_pct, 2),
        "pct_3d": round(pct_3d, 2) if pct_3d is not None else None,
        "pct_5d": round(pct_5d, 2) if pct_5d is not None else None,
        "pct_10d": round(pct_10d, 2) if pct_10d is not None else None,
        "z_daily": round(z_daily, 2) if z_daily is not None else None,
        "z_3d": round(z_3d, 2) if z_3d is not None else None,
        "z_5d": round(z_5d, 2) if z_5d is not None else None,
        "z_10d": round(z_10d, 2) if z_10d is not None else None,
        "acceleration": round(acceleration, 2) if acceleration is not None else None,
        "z_accel": round(z_accel, 2) if z_accel is not None else None,
        "level": level,
        "direction": direction,
        "anomaly_type": anomaly_type,
        "msg": msg,
    }


# ── 跨市场共振检测 ─────────────────────────────
def detect_resonance(evaluations):
    """
    检测同概念多品种同向异动
    返回: {concept: [同向异动品种列表]}
    """
    concept_map = {}  # concept -> {"up": [...], "down": [...]}
    for ev in evaluations:
        if ev["level"] in ["L1", "L2", "L3"] and ev["direction"] in ["up", "down"]:
            concept_map.setdefault(ev["concept"], {"up": [], "down": []})
            concept_map[ev["concept"]][ev["direction"]].append(ev["code"])

    resonance = {}
    for concept, dirs in concept_map.items():
        for d, codes in dirs.items():
            if len(codes) >= 2:
                # 行业级共振
                level = "🥈" if len(codes) >= 2 else "🥉"
                resonance[concept] = {
                    "concept": concept,
                    "direction": d,
                    "count": len(codes),
                    "codes": codes,
                    "level": "industry" if len(codes) >= 2 else "single",
                }
    return resonance


# ── 主入口 ─────────────────────────────
def run_metals_monitor():
    """主监控入口，返回所有品种的评估 + 异动列表 + 共振"""
    config = load_config()
    vol_defaults = config["volatility_defaults"]
    z_thresholds = config["z_thresholds"]
    fallback = config["fallback_thresholds"]

    history = load_history()
    spot = fetch_all_metals_spot()

    # 评估所有品种
    evaluations = []
    for m in config["metals"]:
        code = m["code"]
        if code not in history:
            continue
        prices = history[code].get("prices", {})
        # 如果有实时价，用实时价覆盖今日
        if code in spot and spot[code].get("price") is not None:
            prices = dict(prices)
            today = date.today().isoformat()
            prices[today] = spot[code]["price"]

        ev = evaluate_metal(m, prices, vol_defaults, z_thresholds, fallback)
        if ev:
            evaluations.append(ev)

    # 检测共振
    resonance = detect_resonance(evaluations)

    # 给共振品种加分
    for ev in evaluations:
        if ev["concept"] in resonance:
            r = resonance[ev["concept"]]
            if ev["code"] in r["codes"]:
                ev["resonance"] = r
                # 共振升级（最多 L1）
                if r["count"] >= 3 and ev["level"] != "L1":
                    ev["level"] = "L1"
                    ev["anomaly_type"] = "resonance"

    # 异动列表
    anomalies = [ev for ev in evaluations if ev["level"] in ["L1", "L2", "L3"]]

    # 按等级 + |daily_pct| 排序
    level_order = {"L1": 0, "L2": 1, "L3": 2, "normal": 3, "fallback": 3}
    anomalies.sort(key=lambda x: (level_order.get(x["level"], 4), -abs(x["daily_pct"])))

    return {
        "timestamp": datetime.now().isoformat(),
        "total_metals": len(evaluations),
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
        "all_evaluations": evaluations,
        "resonance": list(resonance.values()),
    }


if __name__ == "__main__":
    result = run_metals_monitor()
    print(f"总品种: {result['total_metals']}")
    print(f"异动: {result['anomaly_count']}")
    for a in result["anomalies"][:10]:
        print(f"  [{a['level']}] {a['name']}: {a['msg']} - {a['concept']}")
