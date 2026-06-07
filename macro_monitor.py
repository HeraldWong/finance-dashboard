"""
宏观风险监控引擎
- 6 大模块
- 6 月移动平均预期（动态最稳健）
- z-score surprise
- 影响力加权综合分
- 6 条预警规则
"""
import os
import json
from datetime import datetime
from macro_spot import load_config, load_history, fetch_and_update, get_expected_6m


def evaluate_all():
    """
    评估所有宏观指标
    返回完整结果
    """
    config = load_config()
    spot, history = fetch_and_update()

    module_results = []
    all_evaluations = []

    for module in config["modules"]:
        mod_eval = {
            "name": module["name"],
            "key": module["key"],
            "indicators": [],
            "module_z": 0.0,
            "module_count": 0,
        }
        for ind in module["indicators"]:
            code = ind["code"]
            current = spot.get(code)
            if current is None:
                continue
            mu, sigma = get_expected_6m(history, code)
            if mu is None:
                continue

            z = (current - mu) / sigma if sigma else 0
            # 限制 z 范围（防止极端值）
            z_clipped = max(-4.0, min(4.0, z))

            ev = {
                "code": code,
                "name": ind["name"],
                "country": ind["country"],
                "module": module["key"],
                "current_value": current,
                "expected_mean": round(mu, 4),
                "expected_std": round(sigma, 4),
                "z_score": round(z_clipped, 2),
                "raw_z": round(z, 2),
                "weight": ind["weight"],
                "lead_lag": ind.get("lead_lag", "coincident"),
                "alert_note": ind.get("alert_note", ""),
                "advantage_threshold": ind.get("advantage_threshold", ""),
            }
            mod_eval["indicators"].append(ev)
            mod_eval["module_z"] += z_clipped * ind["weight"]
            mod_eval["module_count"] += 1
            all_evaluations.append(ev)

        if mod_eval["module_count"] > 0:
            # 模块平均 z
            total_weight = sum(i["weight"] for i in mod_eval["indicators"])
            if total_weight > 0:
                mod_eval["module_z"] = round(mod_eval["module_z"] / total_weight, 2)
        module_results.append(mod_eval)

    # 综合评分
    weights = config["weights_by_category"]
    composite_z = 0.0
    total_weight = 0
    for mod in module_results:
        w = weights.get(mod["key"], 0.1)
        composite_z += mod["module_z"] * w
        total_weight += w
    if total_weight > 0:
        composite_z = round(composite_z / total_weight, 2)

    # 评估预警
    alert_level, alert_name, alert_color, alert_desc = "normal", "中性", "#888", "宏观平稳"
    for rule in config["alert_rules"]:
        cond = rule["condition"]
        try:
            # 简化的 condition 求值（仅支持简单比较）
            ok = evaluate_condition(cond, composite_z, all_evaluations)
            if ok:
                alert_level = rule["level"]
                alert_name = rule["name"]
                alert_color = rule["color"]
                alert_desc = rule["description"]
                break  # 找到最高优先级
        except Exception:
            continue

    # 找最值得关注的指标（|z| 最大的 5 个）
    top_indicators = sorted(all_evaluations, key=lambda x: abs(x["z_score"]), reverse=True)[:5]

    # 提取关键 PMIs
    cn_pmi = next((e for e in all_evaluations if e["code"] == "CN_PMI"), None)
    us_ism = next((e for e in all_evaluations if e["code"] == "US_ISM_PMI"), None)
    us_10y2y = next((e for e in all_evaluations if e["code"] == "US_10Y_2Y_SPREAD"), None)
    us_vix = next((e for e in all_evaluations if e["code"] == "US_VIX"), None)

    return {
        "timestamp": datetime.now().isoformat(),
        "composite_z": composite_z,
        "alert_level": alert_level,
        "alert_name": alert_name,
        "alert_color": alert_color,
        "alert_desc": alert_desc,
        "modules": module_results,
        "all_evaluations": all_evaluations,
        "top_indicators": top_indicators,
        "key_signals": {
            "cn_pmi_z": cn_pmi["z_score"] if cn_pmi else None,
            "us_ism_z": us_ism["z_score"] if us_ism else None,
            "us_10y2y_spread": us_10y2y["current_value"] if us_10y2y else None,
            "us_vix": us_vix["current_value"] if us_vix else None,
        }
    }


def evaluate_condition(cond, composite_z, evaluations):
    """
    简化的条件求值
    支持: composite > X, composite < X, abs(composite) <= X
         US_10Y_2Y_SPREAD < 0, US_VIX > 30, CN_PMI > 51
    """
    ind_map = {e["code"]: e["current_value"] for e in evaluations}

    cond = cond.strip()
    # 替换变量
    cond_eval = cond
    cond_eval = cond_eval.replace("composite", str(composite_z))
    for code, v in ind_map.items():
        if v is not None:
            cond_eval = cond_eval.replace(code, str(v))

    # 处理 abs()
    import re
    cond_eval = re.sub(r'abs\(([^)]+)\)', r'abs(\1)', cond_eval)

    # 安全求值
    try:
        return bool(eval(cond_eval))
    except Exception:
        return False


def run_macro_monitor():
    return evaluate_all()


if __name__ == "__main__":
    r = run_macro_monitor()
    print(f"综合 z: {r['composite_z']}")
    print(f"预警: {r['alert_name']} ({r['alert_level']}) - {r['alert_desc']}")
    print(f"\n各模块 z:")
    for m in r["modules"]:
        print(f"  {m['name']}: z={m['module_z']} ({m['module_count']} 指标)")
    print(f"\nTop 5 异动指标:")
    for t in r["top_indicators"][:5]:
        print(f"  {t['name']}: z={t['z_score']} (当前 {t['current_value']})")
