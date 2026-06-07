"""
新闻快讯自动解析 → 写入 macro_config.json  v1.1
- 多源融合: 新浪 RSS / 东方财富 / akshare 兜底
- 关键词匹配: 美国/CPI/非农/ISM/PCE/初请/密歇根/FOMC
- 写 config: 只改 current_value / as_of / data_source, 保留其他字段
- **优先用户输入**: --text "5月非农 17.2万人" 直接解析

用法:
    python news_parser.py                                  # 自动跑, 从多源抓
    python news_parser.py --test                           # 测试模式, 不写 config
    python news_parser.py --text "5月非农 17.2万人"          # 直接解析用户输入
    python news_parser.py --text "5月非农 17.2万人, 6月核心CPI同比 2.9%"  # 批量

设计: 不写 cache (cache 是历史 181 天时间序列, 写入会污染)
       只改 config.json, 后端 cache 6h 后自动用新值
"""
import os
import re
import sys
import json
import time
import requests
from datetime import datetime, date

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "macro_config.json")
LOG_FILE = os.path.join(os.path.dirname(__file__), "cache", "news_parser_log.json")

# ── 指标映射: 关键词 → config code ──
# 每个指标多个关键词, 优先匹配精确的
INDICATOR_PATTERNS = [
    ("US_NONFARM", ["非农", "非农就业"], "wan"),
    ("US_CPI_CORE", ["核心CPI", "核心 CPI"], "pct"),
    ("US_CPI_HEAD", ["CPI"], "pct"),  # 通用 CPI (核心/整体)
    ("US_PCE_CORE", ["核心PCE", "核心 PCE"], "pct"),
    ("US_ISM_PMI", ["ISM"], "ppi"),
    ("US_INFLATION_EXP", ["密歇根", "通胀预期", "通胀预期"], "pct"),
    ("US_INITIAL_CLAIMS", ["初请", "首次申请失业金", "初请失业金"], "wan"),
    ("US_RETAIL", ["零售销售"], "pct"),
    ("US_UNEMPLOY", ["失业率"], "pct"),
    ("CN_CPI", ["中国CPI", "中国 CPI", "中国 消费者价格"], "pct"),
    ("CN_PPI", ["中国PPI", "中国 PPI"], "pct"),
    ("CN_PMI", ["制造业 PMI", "官方制造业 PMI"], "ppi"),
    ("CN_RETAIL", ["社零", "社会消费品零售"], "pct"),
    ("CN_INDUSTRIAL", ["工业增加值", "规模以上工业"], "pct"),
    ("CN_EXPORT", ["出口同比"], "pct"),
    ("CN_REALESTATE", ["房地产销售"], "pct"),
]


def parse_user_text(text):
    """解析用户输入文字, 返回 matched dict
    支持: "5月非农 17.2万人, 6月核心CPI同比 2.9%"
    """
    matched = {}
    # 1. 按逗号 / 中文句号分多句
    sentences = re.split(r'[,，;；。\n]', text)
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        for code, name_kws, unit_type in INDICATOR_PATTERNS:
            matched_code = None
            kw_pos = -1
            for kw in name_kws:
                pos = sent.find(kw)
                if pos >= 0:
                    matched_code = code
                    kw_pos = pos
                    break
            if not matched_code:
                continue
            # 提取数字 - 只在关键词之后 (避免 "5月" 里的 5)
            after_kw = sent[kw_pos + len(name_kws[0]) if name_kws else kw_pos:]
            value = _extract_number(after_kw, unit_type)
            if value is not None:
                # 提取月份/时点 - 用整句
                as_of = _extract_date_hint(sent) or date.today().strftime("%Y-%m")
                matched[matched_code] = {
                    "value": value,
                    "as_of": as_of,
                    "source": "user_input",
                    "event": sent[:60],
                }
    return matched


def _extract_number(text, unit_type):
    """从关键词后文本提取数字"""
    # 找带单位的数字
    patterns = [
        (r'(-?\d+\.?\d*)\s*万人', "wan"),
        (r'(-?\d+\.?\d*)\s*万(?!\s*人)', "wan"),
        (r'(-?\d+\.?\d*)\s*%', "pct"),
        (r'(-?\d+\.?\d*)\s*个百分点', "pct"),
        (r'(-?\d+\.?\d*)\s*PMI', "ppi"),
    ]
    for pat, pat_type in patterns:
        m = re.search(pat, text)
        if m:
            return float(m.group(1))
    # 兜底: 取关键词后第一个数字 (排除明显月份)
    nums = re.findall(r'(-?\d+\.?\d*)', text)
    for n in nums:
        v = float(n)
        # 排除明显是月份的 (1-12, 出现在 "X月" 之前)
        if unit_type == "pct" and 0 < v < 30:
            return v
        if unit_type == "wan" and 0 < v < 10000:
            return v
        if unit_type == "ppi" and 0 < v < 100:
            return v
    return None


def _extract_date_hint(text):
    """提取月份/时点 (X月, 2026-05, 截至 MM-DD)"""
    # "5月" → "2026-05" (假设今年)
    m = re.search(r'(\d{1,2})\s*月', text)
    if m:
        return f"2026-{int(m.group(1)):02d}"
    m = re.search(r'(\d{4})[年\-](\d{1,2})[月\-]?(\d{1,2})?', text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.search(r'截至\s*(\d{1,2})[月\-](\d{1,2})', text)
    if m:
        return f"2026-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return None


def fetch_sina_rss():
    """抓新浪 RSS 美国经济新闻"""
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.baidu.com/"}
    urls = [
        ("https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2517&num=30&page=1", "新浪国际财经"),
        ("https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2514&num=30&page=1", "新浪环球市场"),
        ("https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&num=30&page=1", "新浪国际"),
    ]
    items = []
    for url, name in urls:
        try:
            r = requests.get(url, headers=headers, timeout=10)
            j = r.json()
            data = j.get("result", {}).get("data", [])
            for it in data:
                title = it.get("title", "")
                if any(k in title for k in ["美国", "美联储", "非农", "CPI", "ISM", "PCE", "初请", "密歇根", "FOMC", "通胀", "零售"]):
                    items.append((name, title, it.get("ctime", 0)))
        except Exception as e:
            print(f"  {name} ERR: {e}")
    return items


def fetch_akshare_economic():
    """akshare news_economic_baidu (经济日历, 99 行, 含'公布'列实际值)"""
    try:
        import akshare as ak
        df = ak.news_economic_baidu()
        return df
    except Exception as e:
        print(f"  akshare ERR: {e}")
        return None


def fetch_akshare_matched():
    """akshare 经济日历匹配"""
    df = fetch_akshare_economic()
    if df is None or df.empty:
        return {}
    matched = {}
    for _, row in df.iterrows():
        event = str(row.get("事件", ""))
        if "美国" not in event and "美联储" not in event:
            continue
        for code, name_kws, unit_type in INDICATOR_PATTERNS:
            if not code.startswith("US_"):
                continue
            for kw in name_kws:
                if kw in event:
                    val = row.get("公布")
                    if val is None or (isinstance(val, float) and val != val):
                        break
                    try:
                        matched[code] = {
                            "value": float(val),
                            "as_of": str(row.get("日期", "?")),
                            "source": "akshare news_economic_baidu",
                            "event": event[:60],
                        }
                    except (ValueError, TypeError):
                        pass
                    break
    return matched


def update_config(matched, dry_run=False):
    """写入 macro_config.json, 只改 current_value/as_of/data_source, 保留其他字段"""
    if not matched:
        return []

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    updates = []
    for module in config["modules"]:
        for ind in module["indicators"]:
            code = ind["code"]
            if code in matched:
                m = matched[code]
                old_val = ind.get("current_value")
                new_val = m["value"]
                ind["_old_current_value"] = old_val
                ind["current_value"] = new_val
                ind["as_of"] = m.get("as_of", date.today().isoformat())
                ind["data_source"] = m.get("source", "news_parser")
                updates.append((code, old_val, new_val, m.get("as_of"), m.get("event", "")[:50]))

    if updates and not dry_run:
        # 备份
        backup = CONFIG_FILE + ".bak"
        with open(CONFIG_FILE, "rb") as f:
            with open(backup, "wb") as bf:
                bf.write(f.read())
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    return updates


def main():
    dry_run = "--test" in sys.argv

    # 用户输入模式
    user_text = None
    for i, arg in enumerate(sys.argv):
        if arg == "--text" and i + 1 < len(sys.argv):
            user_text = " ".join(sys.argv[i+1:])
            break

    print(f"=== news_parser.py 启动 (mode: {'测试' if dry_run else '实模式'}) ===")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  config: {CONFIG_FILE}\n")

    matched = {}

    if user_text:
        print(f"[0] 用户输入: {user_text}")
        matched.update(parse_user_text(user_text))
        print(f"  命中: {len(matched)} 个")
    else:
        # 1. 新浪 RSS
        print("[1] 抓新浪 RSS 国际财经...")
        sina = fetch_sina_rss()
        print(f"  美经济相关快讯: {len(sina)} 条 (参考用, 需手动 parse)")
        for name, title, ts in sina[:5]:
            print(f"    [{name}] {title[:80]}")

        # 2. akshare 经济日历
        print("\n[2] 抓 akshare news_economic_baidu...")
        matched.update(fetch_akshare_matched())
        print(f"  命中: {len(matched)} 个美国指标")

    if matched:
        print("\n=== 解析结果 ===")
        for code, m in matched.items():
            print(f"  {code:20s} = {m['value']:8} (as_of={m.get('as_of')}, source={m.get('source')})")

    # 3. 写 config
    print(f"\n[3] 写入 macro_config.json (dry_run={dry_run})...")
    updates = update_config(matched, dry_run=dry_run)
    if updates:
        for code, old, new, as_of, event in updates:
            print(f"  [OK] {code:20s} {old} -> {new} (as_of={as_of})")
        if not dry_run:
            print(f"\n  [NOTE] 已写 {len(updates)} 个, 缓存 cache 6h 后生效")
            print(f"  或: 删除 cache/macro_spot_cache.json 重启后端 立即生效")
    else:
        print("  无更新 (matched 为空 或 已在 config 中)")

    # log
    log_entry = {
        "ts": datetime.now().isoformat(),
        "mode": "user_text" if user_text else "auto",
        "input": user_text or "",
        "matched": len(matched),
        "updates": [{"code": u[0], "old": u[1], "new": u[2]} for u in updates],
    }
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        logs = []
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        logs.append(log_entry)
        logs = logs[-30:]
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
    except:
        pass

    print(f"\n=== 完成 ===")


if __name__ == "__main__":
    main()
