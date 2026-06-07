"""
金属异动原因分析 - DeepSeek 触发式调用
- 只在异动品种上调用（不异动不调，省 token）
- 复用 themes.json 的 A股映射逻辑
"""
import os
import json
import requests
from datetime import datetime
from threading import Thread

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-db05f287613143af81e704f1dd30ce53")
DEEPSEEK_BASE = "https://api.deepseek.com"


def deepseek_analyze_metal(metal, evaluation):
    """
    单品种异动分析
    metal: 品种配置
    evaluation: monitor 的评估结果
    """
    try:
        prompt = f"""你是顶级大宗商品分析师。分析下面这个金属品种的异动原因，给出驱动因素和A股映射。

【品种】{metal['name']} ({metal['code']})
【概念】{metal['concept']}
【应用领域】{metal['application']}
【当前价】{evaluation['price']} {evaluation['unit']}
【单日涨跌】{evaluation['daily_pct']:+.2f}%
【3日涨跌】{evaluation.get('pct_3d', 0):+.2f}%
【5日涨跌】{evaluation.get('pct_5d', 0):+.2f}%
【10日涨跌】{evaluation.get('pct_10d', 0):+.2f}%
【z-score】日:{evaluation.get('z_daily', 0):.1f}σ, 3日:{evaluation.get('z_3d', 0):.1f}σ, 5日:{evaluation.get('z_5d', 0):.1f}σ
【加速度】{evaluation.get('acceleration', 0):+.2f}%
【A股映射】{', '.join(metal.get('stocks', []))}

请输出 JSON 格式：
{{
  "trigger_type": "price_jump" | "trend_acceleration" | "policy_news" | "supply_disruption" | "demand_change" | "macro_factor",
  "drivers": ["驱动因素1", "驱动因素2", "驱动因素3"],
  "a_stock_impact": "哪几个 A 股标的可能最受益 + 简要原因",
  "outlook": "短期展望（看多/看空/震荡）"
}}
"""

        r = requests.post(
            f"{DEEPSEEK_BASE}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-reasoner",
                "messages": [
                    {"role": "system", "content": "你是大宗商品分析师。简洁输出 JSON，不要 markdown。"},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 1500,
            },
            timeout=60,
        )

        if r.status_code != 200:
            return {"error": f"API {r.status_code}: {r.text[:200]}"}

        result = r.json()
        content = result["choices"][0]["message"]["content"].strip()

        # 解析 JSON（reasoner 可能包含 <think> 标签）
        if "</think>" in content:
            content = content.split("</think>", 1)[1].strip()

        # 提取 JSON 块
        if "{" in content and "}" in content:
            start = content.find("{")
            end = content.rfind("}") + 1
            content = content[start:end]

        return json.loads(content)
    except json.JSONDecodeError as e:
        return {"error": f"JSON 解析失败: {e}", "raw": content[:200] if 'content' in locals() else ''}
    except Exception as e:
        return {"error": str(e)}


# ── 异步批量分析 ─────────────────────────────
def batch_analyze_async(evaluations, metals_config, callback=None):
    """异步分析所有异动品种"""
    def _do():
        results = {}
        for ev in evaluations:
            if ev["level"] not in ["L1", "L2", "L3"]:
                continue
            metal = next((m for m in metals_config["metals"] if m["code"] == ev["code"]), None)
            if not metal:
                continue
            print(f"[metals_anomaly] 分析 {metal['name']}...", flush=True)
            ai = deepseek_analyze_metal(metal, ev)
            if ai and "error" not in ai:
                results[ev["code"]] = ai
        # 保存结果
        cache_file = os.path.join(os.path.dirname(__file__), "cache", "metals_anomalies.json")
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": datetime.now().isoformat(),
                "analyses": results,
            }, f, ensure_ascii=False, indent=2)
        if callback:
            callback(results)

    t = Thread(target=_do, daemon=True)
    t.start()
    return t


def load_anomaly_cache():
    """加载最近的异动分析结果"""
    cache_file = os.path.join(os.path.dirname(__file__), "cache", "metals_anomalies.json")
    if not os.path.exists(cache_file):
        return {}
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d.get("analyses", {})
    except:
        return {}


if __name__ == "__main__":
    from metals_monitor import run_metals_monitor
    result = run_metals_monitor()
    config = {"metals": result["all_evaluations"]}
    # 真实 config 重新加载
    import json as _json
    with open(os.path.join(os.path.dirname(__file__), "metals_config.json"), "r", encoding="utf-8") as f:
        config = _json.load(f)

    anomalies = result["anomalies"][:3]  # 测试前 3 个
    for a in anomalies:
        metal = next(m for m in config["metals"] if m["code"] == a["code"])
        print(f"\n=== {metal['name']} ({a['level']}) ===")
        ai = deepseek_analyze_metal(metal, a)
        print(ai)
