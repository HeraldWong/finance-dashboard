"""
宏观预警 DeepSeek 分析 - 触发式 + 30 分钟冷却锁
"""
import os
import json
import requests
from datetime import datetime, timedelta
from threading import Thread
from macro_spot import load_config

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-db05f287613143af81e704f1dd30ce53")
DEEPSEEK_BASE = "https://api.deepseek.com"

CACHE_FILE = os.path.join(os.path.dirname(__file__), "cache", "macro_anomalies.json")


def deepseek_analyze_macro(monitor_result):
    """
    对宏观预警做 DeepSeek 分析
    28 指标共享一次分析 - token 最省
    """
    try:
        # 构造 prompt
        top5 = monitor_result.get("top_indicators", [])
        top5_text = "\n".join([f"- {t['name']}: 当前 {t['current_value']} (z={t['z_score']})" for t in top5])

        modules_text = "\n".join([f"- {m['name']}: 模块 z={m['module_z']}" for m in monitor_result.get("modules", [])])

        prompt = f"""你是顶级宏观策略师（高盛/摩根士丹利级别）。分析当前中美宏观环境，给出风险评估和操作建议。

【综合 z-score】{monitor_result['composite_z']}
【预警等级】{monitor_result['alert_name']} - {monitor_result['alert_desc']}

【6 大模块 z-score】
{modules_text}

【Top 5 异动指标】
{top5_text}

【关键信号】
- 中国 PMI z: {monitor_result.get('key_signals', {}).get('cn_pmi_z')}
- 美国 ISM PMI z: {monitor_result.get('key_signals', {}).get('us_ism_z')}
- 美 10Y-2Y 利差: {monitor_result.get('key_signals', {}).get('us_10y2y_spread')}%
- VIX: {monitor_result.get('key_signals', {}).get('us_vix')}

请输出 JSON（不要 markdown）：
{{
  "trigger_type": "growth_slowdown" | "inflation_spike" | "liquidity_crisis" | "rate_shock" | "sentiment_panic" | "composite_risk",
  "drivers": ["驱动因素1", "驱动因素2", "驱动因素3"],
  "cross_market_signal": "跨市场联动信号分析（中美联动 / 风险传染 / 政策对冲）",
  "a_stock_impact": "对 A 股 / 港股 / 美股的具体影响 + 受影响板块",
  "outlook_1m": "未来 1 个月展望",
  "outlook_3m": "未来 3 个月展望",
  "position_advice": "仓位建议（轻仓/标准/加仓/观望）",
  "key_watch": "下个月最值得关注的 2-3 个数据"
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
                    {"role": "system", "content": "你是顶级宏观策略师。简洁输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 1800,
            },
            timeout=60,
        )

        if r.status_code != 200:
            return {"error": f"API {r.status_code}: {r.text[:200]}"}

        result = r.json()
        content = result["choices"][0]["message"]["content"].strip()

        if "</think>" in content:
            content = content.split("</think>", 1)[1].strip()

        if "{" in content and "}" in content:
            start = content.find("{")
            end = content.rfind("}") + 1
            content = content[start:end]

        return json.loads(content)
    except json.JSONDecodeError as e:
        return {"error": f"JSON 解析失败: {e}"}
    except Exception as e:
        return {"error": str(e)}


# ── 冷却锁 ─────────────────────────────
def can_analyze():
    """检查是否在冷却期"""
    config = load_config()
    cooldown = config.get("cooldown_minutes", 30)
    if not os.path.exists(CACHE_FILE):
        return True
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        last_ts = cache.get("timestamp")
        if not last_ts:
            return True
        last = datetime.fromisoformat(last_ts)
        if datetime.now() - last < timedelta(minutes=cooldown):
            remaining = cooldown - (datetime.now() - last).seconds // 60
            return False, remaining
        return True, 0
    except:
        return True, 0


def save_analysis(analysis, alert_name):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "alert_name": alert_name,
            "analysis": analysis,
        }, f, ensure_ascii=False, indent=2)


def load_analysis():
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return None


# ── 触发式分析 ─────────────────────────────
def analyze_if_needed(monitor_result, force=False):
    """
    满足预警条件时启动分析
    """
    if not force:
        # 只在 L1/L2 时触发
        if monitor_result["alert_level"] not in ["L1", "L2"]:
            return None, "预警等级未达 L1/L2，跳过 AI 分析"
        can, remaining = can_analyze()
        if not can:
            return None, f"冷却中，剩余 {remaining} 分钟"

    # 启动异步
    def _do():
        ai = deepseek_analyze_macro(monitor_result)
        save_analysis(ai, monitor_result["alert_name"])

    t = Thread(target=_do, daemon=True)
    t.start()
    return True, "已启动分析"


if __name__ == "__main__":
    from macro_monitor import run_macro_monitor
    r = run_macro_monitor()
    print(f"综合 z: {r['composite_z']}, 预警: {r['alert_name']}")
    ai = deepseek_analyze_macro(r)
    print(json.dumps(ai, ensure_ascii=False, indent=2))
