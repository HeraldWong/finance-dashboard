"""
美股异动 → A股映射（playwright抓数据 + DeepSeek分析）
"""
import requests
import json
import time
import os
import threading
from datetime import datetime, date
from playwright.sync_api import sync_playwright

THEMES_FILE = os.path.join(os.path.dirname(__file__), "themes.json")
CACHE_FILE = os.path.join(os.path.dirname(__file__), "cache", "us_anomaly.json")

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-db05f287613143af81e704f1dd30ce53")
DEEPSEEK_BASE = "https://api.deepseek.com"


def load_themes():
    with open(THEMES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def build_themes_prompt():
    themes = load_themes()
    lines = []
    for tag, stocks in themes.items():
        lines.append(f"- {tag}: {', '.join(stocks[:5])}")
    return "\n".join(lines)


# ── Step 1: 抓美股异动 ─────────────────────────────
# 美股大盘股代码清单（500+ 只大票，覆盖各板块）
US_LARGE_CAPS = [
    # 科技巨头
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "TSLA", "AVGO", "ORCL",
    "CRM", "AMD", "INTC", "QCOM", "TXN", "MU", "AMAT", "LRCX", "KLAC", "SNPS",
    "CDNS", "MRVL", "FTNT", "PANW", "CRWD", "ZS", "DDOG", "NET", "TEAM", "WDAY",
    "ADBE", "INTU", "ORCL", "SAP", "NOW", "ANSS", "ROP", "TYL", "PAYC",
    # 通信
    "T", "VZ", "TMUS", "CMCSA", "DIS", "NFLX", "PARA", "WBD",
    # 消费
    "WMT", "COST", "TGT", "HD", "LOW", "MCD", "SBUX", "NKE", "CMG", "YUM",
    "DG", "DLTR", "ROST", "BBY", "ULTA", "TJX", "MAR", "HLT", "RCL", "CCL",
    "F", "GM", "STLA", "TM", "HMC", "RIVN", "LCID",
    # 金融
    "JPM", "BAC", "WFC", "C", "GS", "MS", "BLK", "SCHW", "AXP", "V", "MA",
    "PYPL", "SQ", "COF", "USB", "PNC", "TFC", "BCS", "BRK-B",
    # 医疗
    "JNJ", "PFE", "MRK", "ABBV", "LLY", "UNH", "CVS", "CI", "HUM", "ELV",
    "TMO", "DHR", "ABT", "MDT", "BSX", "EW", "SYK", "ZTS", "BDX", "BAX",
    "ISRG", "REGN", "VRTX", "GILD", "AMGN", "BIIB", "MRNA", "NVAX", "BNTX",
    "LLY", "NVO", "AZN",
    # 能源
    "XOM", "CVX", "COP", "SLB", "EOG", "PSX", "MPC", "VLO", "OXY", "PXD",
    "HES", "DVN", "FANG", "BKR", "HAL",
    # 工业
    "BA", "CAT", "DE", "GE", "HON", "MMM", "UNP", "UPS", "FDX", "RTX",
    "LMT", "NOC", "GD", "WM", "EMR", "ETN", "PH", "CMI", "PCAR",
    # 材料
    "LIN", "APD", "ECL", "FCX", "NEM", "GOLD", "NUE", "STLD", "VMC", "MLM",
    "ALB", "CE", "MOS", "CF", "NTR",
    # 公用事业
    "NEE", "DUK", "SO", "AEP", "EXC", "SRE", "XEL", "PEG", "WEC", "ED",
    # 房地产
    "AMT", "PLD", "CCI", "EQIX", "PSA", "O", "SPG", "WELL", "VICI", "AVB",
    "EQR", "MAA",
    # 半导体/AI
    "NVDA", "AMD", "INTC", "MU", "QCOM", "AVGO", "TXN", "ADI", "MCHP", "MPWR",
    "ON", "NXPI", "SWKS", "QRVO", "SMCI", "ARM", "DELL",
    # AI 数据中心
    "CRDO", "COHR", "LITE", "AAOI", "POWI", "VRT", "GDS", "EQIX", "DLR", "AMT",
    # 中概股
    "BABA", "PDD", "JD", "BIDU", "NTES", "TME", "BILI", "TAL", "YMM", "ZTO",
    "NIO", "XPEV", "LI", "BABA", "JD", "WB", "MNSO", "GDS", "VNET", "TME",
    "RLX", "LU", "KC", "TUYA", "DOYU", "IQ", "DADA", "ZH",
    # ETF
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "ARKK", "SOXL", "TQQQ", "SQQQ",
    "XLE", "XLF", "XLK", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE",
    "GLD", "SLV", "USO", "UNG", "DBC", "GDX", "GDXJ", "SIL", "COPX", "URA",
    "BITO", "IBIT", "ETHE", "GBTC", "ARKB",
]


def get_us_top_movers(limit=20, scan_count=80):
    """用Yahoo screener API抓全市场涨幅榜（包含中小盘）"""
    movers = []

    # 优先级1: screener API - 全市场涨股
    try:
        url = "https://query2.finance.yahoo.com/v1/finance/screener/predefined/saved"
        params = {
            "scrIds": "day_gainers",
            "count": 50,  # 抓前50只
        }
        r = requests.get(url, params=params,
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=10)
        if r.status_code == 200:
            data = r.json()
            finance = data.get("finance", {})
            result = finance.get("result", [])
            if result:
                quotes = result[0].get("quotes", [])
                print(f"screener 返回 {len(quotes)} 只涨股")
                for q in quotes:
                    sym = q.get("symbol", "")
                    pct = q.get("regularMarketChangePercent", 0)
                    price = q.get("regularMarketPrice", 0)
                    name = q.get("shortName") or q.get("longName") or sym
                    if not sym or not price: continue
                    if abs(pct) >= 5:
                        movers.append({
                            "ticker": sym,
                            "name": name,
                            "price": price,
                            "change": f"{pct:+.2f}%",
                            "change_amt": q.get("regularMarketChange", 0),
                            "market_cap": q.get("marketCap", 0) or 0
                        })
    except Exception as e:
        print(f"screener API失败: {e}")

    # 备选: 如果screener没拿到，用大票清单
    if not movers:
        print("screener无数据，回退到大票清单")
        batch = US_LARGE_CAPS[:scan_count]
        for symbol in batch:
            try:
                r = requests.get(
                    f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}",
                    params={"interval": "1d", "range": "5d"},
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=6
                )
                if r.status_code == 200:
                    data = r.json()
                    meta = data["chart"]["result"][0]["meta"]
                    price = meta.get("regularMarketPrice", 0)
                    prev = meta.get("chartPreviousClose", 0)
                    name = meta.get("longName") or meta.get("shortName") or symbol
                    if not price or not prev: continue
                    chg_pct = (price - prev) / prev * 100
                    if abs(chg_pct) >= 5:
                        movers.append({
                            "ticker": symbol,
                            "name": name,
                            "price": price,
                            "change": f"{chg_pct:+.2f}%",
                            "change_amt": round(price - prev, 2)
                        })
            except: pass
            time.sleep(0.1)

    movers.sort(
        key=lambda x: float(x["change"].replace("%", "").replace("+", "")),
        reverse=True
    )
    return movers[:limit]


def get_us_top_losers(limit=10):
    """抓跌幅榜（用来做风险预警）"""
    losers = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("https://finance.yahoo.com/markets/stocks/losers/", timeout=20000)
            page.wait_for_timeout(3000)
            rows = page.locator("table tbody tr").all()
            for r in rows[:limit]:
                try:
                    cells = r.locator("td").all()
                    if len(cells) >= 4:
                        losers.append({
                            "ticker": cells[0].inner_text().strip(),
                            "name": cells[1].inner_text().strip(),
                            "price": cells[2].inner_text().strip(),
                            "change": cells[3].inner_text().strip()
                        })
                except: continue
            browser.close()
    except Exception as e:
        print(f"抓Yahoo losers失败: {e}")
    return losers


def get_stock_news(ticker, count=3):
    """Yahoo Finance 搜索接口拉新闻（带描述）"""
    try:
        r = requests.get(
            "https://query2.finance.yahoo.com/v1/finance/search",
            params={"q": ticker, "quotesCount": 1, "newsCount": count},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=6
        )
        if r.status_code == 200:
            data = r.json()
            news = data.get("news", [])
            results = []
            for n in news:
                title = n.get("title", "")
                pub = n.get("publisher", "?")
                # Yahoo 给的 news_summary 字段有简短描述（200-300字符）
                summary = n.get("summary", "") or n.get("description", "")
                if title:
                    results.append({
                        "publisher": pub,
                        "title": title,
                        "summary": summary[:300]  # 截断
                    })
            return results
    except: pass
    return []


# ── Step 2: DeepSeek 分析 ─────────────────────────────
def deepseek_analyze(stock, news_list=None):
    """单只美股异动分析（带新闻描述上下文）"""
    themes_prompt = build_themes_prompt()

    news_section = ""
    if news_list:
        news_section = "\n【近期新闻（含摘要）】\n"
        for n in news_list:
            line = f"- [{n['publisher']}] {n['title']}"
            if n.get('summary'):
                line += f"\n  摘要: {n['summary']}"
            news_section += line + "\n"

    prompt = f"""你是资深美股分析师和A股策略师。请深度分析这只美股的异动原因。

【美股异动】
- 代码：{stock['ticker']}
- 名称：{stock['name']}
- 价格：${stock.get('price', 'N/A')}
- 涨跌：{stock.get('change', 'N/A')}
{news_section}

【主题库参考】（不强制，AI可自主发现新概念）
{themes_prompt}

【任务要求】
1. 必须基于上面新闻摘要分析，不要瞎猜
2. 异动原因要"具体到概念/产品/订单/财报数据"
3. 关联原因要"具体到产业链环节"

只输出严格JSON（不要markdown包裹）：
{{
  "is_real_anomaly": bool,
  "summary": "一句话异动原因（20-30字内，必须具体）",
  "catalyst_type": "财报"|"订单"|"政策"|"概念"|"技术"|"宏观",
  "persistence": 1-7,
  "key_drivers": ["驱动事件1", "驱动事件2", ...],  // 3-5条具体事件
  "related_a_shares": [
    {{"ticker": "代码.SZ/SH", "name": "中文", "confidence": 0-1, "reason": "具体关联原因"}},
    ... 最多8个
  ],
  "reverse_picks": [
    {{"ticker": "代码.SZ/SH", "name": "中文", "reason": "未涨但潜在受益"}},
    ... 最多3个
  ]
}}
"""
    try:
        r = requests.post(
            f"{DEEPSEEK_BASE}/v1/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-reasoner",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 2000
            },
            timeout=30
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        return json.loads(content)
    except Exception as e:
        print(f"DeepSeek分析失败 ({stock['ticker']}): {e}")
        return None


# ── 缓存 ─────────────────────────────────────────────
def save_cache(data):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_cache():
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return None


# ── 主流程 ─────────────────────────────────────────
def run_us_anomaly(force=False):
    """执行美股异动分析（带缓存）"""
    if not force:
        cache = load_cache()
        if cache:
            cache_time = datetime.fromisoformat(cache["timestamp"])
            if cache_time.date() == date.today() and cache.get("results"):
                return cache

    movers = get_us_top_movers(limit=8)  # 减到 8 只 (从15)
    losers = get_us_top_losers(limit=5)
    if not movers:
        return {
            "timestamp": datetime.now().isoformat(),
            "results": [],
            "movers_count": 0,
            "losers_count": 0,
            "message": "抓取失败"
        }

    results = []
    for stock in movers[:8]:
        print(f"分析 {stock['ticker']} {stock['name']}...")
        try:
            news = get_stock_news(stock['ticker'], count=3)
        except Exception as e:
            print(f"  新闻获取失败: {e}")
            news = []
        try:
            ai = deepseek_analyze(stock, news_list=news)
        except Exception as e:
            print(f"  AI 分析失败: {e}")
            ai = None
        if ai and ai.get("is_real_anomaly"):
            results.append({
                "stock": stock,
                "analysis": ai,
                "news_titles": news
            })
        # 增量保存 (每分析完一只就 save, 防止卡死丢数据)
        partial_data = {
            "timestamp": datetime.now().isoformat(),
            "results": results,
            "movers_count": len(movers),
            "losers_count": len(losers),
            "losers": losers,
            "partial": True,
        }
        save_cache(partial_data)
        time.sleep(0.5)

    data = {
        "timestamp": datetime.now().isoformat(),
        "results": results,
        "movers_count": len(movers),
        "losers_count": len(losers),
        "losers": losers,
        "partial": False,
    }
    save_cache(data)
    return data


# ── 异步触发 ─────────────────────────────────────
_thread = None
def run_async():
    global _thread
    if _thread and _thread.is_alive():
        return False
    _thread = threading.Thread(target=run_us_anomaly, args=(True,))
    _thread.daemon = True
    _thread.start()
    return True


# ── 手动分析接口 ──────────────────────────────────
def manual_analyze(text):
    """手动喂文本：每行一只异动股+原因"""
    results = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line: continue
        # 解析 ticker / name / 涨幅 / 原因
        # 简单解析：NVDA 涨8% Blackwell出货
        import re
        m = re.match(r"([A-Z]+)[\s:](.*?)(?:涨(\d+\.?\d*)%|跌(\d+\.?\d*)%)?(.*)", line, re.IGNORECASE)
        if m:
            ticker = m.group(1).upper()
            name_part = m.group(2).strip()
            change = (m.group(3) or m.group(4) or "5")
            direction = "涨" if m.group(3) else "跌"
            reason = m.group(4) or "异动"
            stock = {
                "ticker": ticker,
                "name": name_part or ticker,
                "price": "N/A",
                "change": f"{direction}{change}%"
            }
            ai = deepseek_analyze(stock)
            if ai:
                results.append({
                    "stock": stock,
                    "analysis": ai,
                    "manual": True
                })
        time.sleep(0.3)
    return {
        "timestamp": datetime.now().isoformat(),
        "results": results,
        "manual": True
    }
