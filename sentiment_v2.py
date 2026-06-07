"""
市场情绪 + 策略引擎 V2
- 15 个独立指标（每个独立打分 + 阈值 + 解读）
- 4 阶段判断：修复/启动/主升/退潮
- 策略建议：方向 + 仓位 + 节奏
"""
import akshare as ak_local
from datetime import datetime, date, timedelta


# ── 工具 ──────────────────────────────────────────────
def _pct_to_score(value, danger_low, warn_low, good_low, good_high, warn_high, danger_high, reverse=False):
    """值转 0-100 分"""
    if value is None or value == "—":
        return 50
    if reverse:
        # 越低越好（炸板率、跌幅等）
        if value <= danger_low: return 95
        elif value <= warn_low: return 75
        elif value <= good_low: return 55
        elif value <= good_high: return 45
        elif value <= warn_high: return 25
        else: return 5
    else:
        # 越高越好（涨停家数等）
        if value <= danger_low: return 5
        elif value <= warn_low: return 25
        elif value <= good_low: return 45
        elif value <= good_high: return 55
        elif value <= warn_high: return 75
        else: return 95


def _grade(p):
    if p >= 80: return ("极佳", "loose")
    elif p >= 65: return ("良好", "loose")
    elif p >= 45: return ("中性", "neutral")
    elif p >= 30: return ("偏弱", "neutral-tight")
    else: return ("极差", "tight")


def _tag(p, reverse=False):
    """根据分值返回文字标签"""
    if p >= 80: return "🟢 健康"
    elif p >= 60: return "🟡 正常"
    elif p >= 40: return "🟠 警惕"
    else: return "🔴 危险"


# ── 数据获取工具 ──────────────────────────────────────
def get_zt_data(today):
    """统一获取涨停+炸板+跌停数据"""
    zt, zb, dt = None, None, None
    try: zt = ak_local.stock_zt_pool_em(date=today)
    except: pass
    try: zb = ak_local.stock_zt_pool_zbgc_em(date=today)
    except: pass
    try: dt = ak_local.stock_zt_pool_dtgc_em(date=today)
    except: pass
    return zt, zb, dt


def get_yesterday_zt_performance(today_str):
    """昨日涨停今日表现（赚钱效应核心）"""
    try:
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
        # 跳过周末
        while True:
            yd = date.today() - timedelta(days=(date.today() - datetime.strptime(yesterday, "%Y%m%d").date()).days)
            if yd.weekday() < 5:
                break
            yesterday = (yd - timedelta(days=1)).strftime("%Y%m%d")
        df = ak_local.stock_zt_pool_em(date=yesterday)
        if df is None or df.empty:
            return None
        codes = df["代码"].tolist()
        # 取这些票今天的涨跌幅（用akshare的实时榜单）
        # 简化：用昨日列表中已知涨跌幅均值做近似
        return {
            "yesterday_date": yesterday,
            "count": len(df),
            "codes": codes
        }
    except: return None


# ── 15 个独立指标 ───────────────────────────────────
def indicator_1_zt_count(today):
    """1. 涨停家数"""
    try:
        df, _, _ = get_zt_data(today)
        val = len(df) if df is not None and not df.empty else 0
        score = _pct_to_score(val, 20, 40, 60, 100, 150, 200)
        return {
            "name": "涨停家数", "value": val, "unit": "家", "score": score,
            "tag": _tag(score), "grade": _grade(score)[0], "gradeColor": _grade(score)[1],
            "thresholds": "极冷<30 | 冷30-60 | 热60-100 | 过热>100",
            "logic": "做多动能直接量化", "danger": "<30 持续下行=退潮"
        }
    except: return None


def indicator_2_boards(today):
    """2. 最高连板 + 梯队完整度"""
    try:
        df, _, _ = get_zt_data(today)
        if df is None or df.empty:
            return None
        max_boards = int(df["连板数"].max())
        # 梯队统计
        ladder = {}
        for b in range(1, 6):
            ladder[f"{b}板"] = int((df["连板数"] == b).sum())
        ladder["5板+"] = int((df["连板数"] >= 5).sum())

        # 梯队完整度评分：金字塔为佳
        s1 = ladder.get("1板", 0)
        s2 = ladder.get("2板", 0)
        s3 = ladder.get("3板", 0)
        s4 = ladder.get("4板", 0)
        s5 = ladder.get("5板+", 0)

        # 健康：s1 >> s2 >> s3 >> s4 >> s5（每级衰减30-50%）
        if s5 == 0 and s4 == 0:
            ladder_score = 30  # 没高度
        elif s5 == 0 and s4 <= 2:
            ladder_score = 50
        elif s5 >= 1 and s5 * 3 <= s4 and s4 * 2 <= s3:
            ladder_score = 90  # 健康金字塔
        elif s5 >= 1 and s4 >= 5 and s5 * 5 <= s4:
            ladder_score = 75  # 准健康
        elif s5 >= 2 and s4 == 0:
            ladder_score = 40  # 断档（高位孤军）
        else:
            ladder_score = 60

        # 综合：连板高度 + 梯队
        height_score = _pct_to_score(max_boards, 0, 2, 4, 7, 10, 15)
        combined = int(height_score * 0.4 + ladder_score * 0.6)

        return {
            "name": "连板梯队", "value": max_boards, "unit": "板",
            "ladder": ladder, "ladderScore": ladder_score,
            "score": combined, "tag": _tag(combined), "grade": _grade(combined)[0], "gradeColor": _grade(combined)[1],
            "thresholds": "梯队完整（金字塔）=10分 | 断档=0-3分",
            "logic": "连板高度+梯队完整度双重判断", "danger": "梯队断档（高位孤军）= 见顶信号"
        }
    except: return None


def indicator_3_zbgc_rate(today):
    """3. 炸板率"""
    try:
        df_zt, df_zb, _ = get_zt_data(today)
        zt_n = len(df_zt) if df_zt is not None and not df_zt.empty else 0
        zb_n = len(df_zb) if df_zb is not None and not df_zb.empty else 0
        total = zt_n + zb_n
        rate = round(zb_n / total * 100, 1) if total > 0 else 0
        score = _pct_to_score(rate, 0, 10, 20, 30, 40, 60, reverse=True)
        return {
            "name": "炸板率", "value": rate, "unit": "%", "score": score,
            "tag": _tag(score, True), "grade": _grade(score)[0], "gradeColor": _grade(score)[1],
            "thresholds": "强封<20% | 正常20-30% | 警惕30-45% | 危险>45%",
            "logic": "封板强度=强封→反复炸+午后放量漏单", "danger": ">35-45% 且持续上行=退潮"
        }
    except: return None


def indicator_4_high_zbgc_ratio(today):
    """4. 高位炸板占比（4板+炸板 / 总炸板）"""
    try:
        df_zt, df_zb, _ = get_zt_data(today)
        zb_n = len(df_zb) if df_zb is not None and not df_zb.empty else 0
        if zb_n == 0:
            ratio = 0
        else:
            # 假设炸板池中"连板数>=3"的比例作为高位炸板近似
            high_zb = 0
            if "连板数" in df_zb.columns:
                high_zb = int((df_zb["连板数"] >= 3).sum())
            else:
                # 退路：用"首次封板时间"接近收盘作为近似
                high_zb = int((df_zb["涨跌幅"] >= 9).sum()) // 2
            ratio = round(high_zb / zb_n * 100, 1)
        score = _pct_to_score(ratio, 0, 5, 10, 20, 30, 50, reverse=True)
        return {
            "name": "高位炸板占比", "value": ratio, "unit": "%", "score": score,
            "tag": _tag(score, True), "grade": _grade(score)[0], "gradeColor": _grade(score)[1],
            "thresholds": "正常<5% | 警惕5-15% | 见顶信号>15%",
            "logic": "高位炸板=龙头资金分歧，最强见顶信号", "danger": ">15%=板块见顶"
        }
    except: return None


def indicator_5_yzt_performance(today):
    """5. 赚钱效应 = 今日涨停/(涨停+跌停) 比值（核心赚钱指标）"""
    try:
        df_zt, _, df_dt = get_zt_data(today)
        if df_zt is None:
            return None
        zt_count = len(df_zt)
        dt_count = len(df_dt) if (df_dt is not None and not df_dt.empty) else 0
        total = zt_count + dt_count
        # 赚钱效应比率: 涨停/(涨停+跌停) - 越高越好
        if total == 0:
            ratio = 50.0
        else:
            ratio = round(zt_count / total * 100, 1)
        # 评分: >80%=极佳, 60-80%好, 40-60%中性, 20-40%差, <20%极差
        score = _pct_to_score(ratio, 0, 20, 40, 60, 80, 100)
        return {
            "name": "赚钱效应", "value": ratio, "unit": "%",
            "zt_count": zt_count, "dt_count": dt_count,
            "score": score, "tag": _tag(score), "grade": _grade(score)[0], "gradeColor": _grade(score)[1],
            "thresholds": "极佳>80% | 好60-80% | 中40-60% | 差20-40% | 极差<20%",
            "logic": "赚钱效应=涨停/(涨停+跌停)。>60%资金面健康，<40%亏钱效应蔓延",
            "danger": "<20%=市场恐慌 / 赚钱效应断裂"
        }
    except: return None


def indicator_6_top3_concentration(today):
    """6. 板块涨停集中度（TOP3 板块占涨停比例）"""
    try:
        df, _, _ = get_zt_data(today)
        if df is None or df.empty:
            return None
        # 按行业分组统计
        if "所属行业" in df.columns:
            industries = df["所属行业"].value_counts()
            top3_sum = industries.head(3).sum()
            total = len(df)
            concentration = round(top3_sum / total * 100, 1) if total > 0 else 0
            top3 = industries.head(3).to_dict()
        else:
            concentration = 0
            top3 = {}
        # 太高（>80%）=过度集中（单一热点）
        # 健康（40-60%）=主线明确但不极端
        # 太低（<30%）=没有主线
        if concentration >= 80: score = 40  # 过度集中警惕
        elif concentration >= 50: score = 85  # 主线明确
        elif concentration >= 30: score = 65  # 多线并行
        else: score = 35
        return {
            "name": "涨停TOP3板块集中度", "value": concentration, "unit": "%",
            "top3": top3, "score": score,
            "tag": _tag(score), "grade": _grade(score)[0], "gradeColor": _grade(score)[1],
            "thresholds": ">60%主线 | 40-60%健康 | 30-40%多线 | <30%无主线",
            "logic": "涨停集中度=主线明确度", "danger": ">80% 过度集中 / <30% 无主线"
        }
    except: return None


def indicator_7_big_face(today):
    """7. 大面占比（跌停+大跌股）"""
    try:
        df_zt, df_zb, df_dt = get_zt_data(today)
        # 大面 = 跌停 + 大幅下跌 + 炸板中跌幅大的
        dt_n = len(df_dt) if df_dt is not None and not df_dt.empty else 0
        big_drop = 0
        if df_zb is not None and not df_zb.empty:
            big_drop = int((df_zb["涨跌幅"] < 5).sum())
        big_face = dt_n + big_drop
        zt_n = len(df_zt) if df_zt is not None and not df_zt.empty else 0
        ratio = round(big_face / max(zt_n, 1) * 100, 1)
        score = _pct_to_score(ratio, 0, 10, 20, 40, 60, 100, reverse=True)
        return {
            "name": "大面占比", "value": ratio, "unit": "%", "score": score,
            "tag": _tag(score, True), "grade": _grade(score)[0], "gradeColor": _grade(score)[1],
            "thresholds": "低<5% | 中5-10% | 高10-15% | 危险>15%",
            "logic": "大面股=今天就把接力资金活埋", "danger": ">10% 警惕 / >15% 退潮"
        }
    except: return None


def indicator_8_drop5(today):
    """8. 跌幅<-5%股数 (全 A 5524 只实时统计, 30min 缓存)"""
    try:
        # 改用全 A 真实统计, 不是炸板池样本
        a_data = _get_all_a_spot_cached()
        if a_data and a_data.get("total", 0) > 1000:
            drop_n = a_data.get("n_drop5", 0)
            total = a_data.get("total", 5524)
            score = _pct_to_score(drop_n, 0, 30, 80, 300, 600, 1200, reverse=True)
            return {
                "name": "跌幅<-5%股数", "value": drop_n, "unit": f"家 / {total} 只", "score": score,
                "tag": _tag(score, True), "grade": _grade(score)[0], "gradeColor": _grade(score)[1],
                "thresholds": "低<80 | 中80-300 | 高300-600 | 危险>600",
                "logic": "全 A 实时统计 (5524 只, 30min 缓存)", "danger": "突然翻倍式跳升=退潮预警"
            }
        # 兜底: 跌停 + 炸板池中跌幅大的 (旧逻辑)
        _, df_zb, df_dt = get_zt_data(today)
        dt_n = len(df_dt) if df_dt is not None and not df_dt.empty else 0
        drop_n = 0
        if df_zb is not None and not df_zb.empty:
            drop_n = int((df_zb["涨跌幅"] < -5).sum())
        total = dt_n + drop_n
        score = _pct_to_score(total, 0, 30, 80, 200, 400, 800, reverse=True)
        return {
            "name": "跌幅<-5%股数", "value": total, "unit": f"家 (样本, 全A 失败)", "score": score,
            "tag": _tag(score, True), "grade": _grade(score)[0], "gradeColor": _grade(score)[1],
            "thresholds": "低<100 | 中100-300 | 高300-500 | 危险>500",
            "logic": "结构崩比指数崩更前置 (样本)", "danger": "突然翻倍式跳升=退潮预警"
        }
    except: return None


# 全 A 30min 缓存 (避免每次 sentiment 都拉 25s)
_ALL_A_V2_CACHE = {"data": None, "ts": 0, "ttl": 1800}  # 30min


# ── 涨停金字塔完整度率 ──
# 理论金字塔: 1板 50% / 2板 25% / 3板 12.5% / 4板 6.25% / 5+板 3.1% (等比 0.5 递减)
_PYRAMID_THEORY = [0.5, 0.25, 0.125, 0.0625, 0.03125]
_s = sum(_PYRAMID_THEORY)
_PYRAMID_THEORY = [x / _s for x in _PYRAMID_THEORY]


def indicator_pyramid_completeness(today):
    """10. 金字塔完整度率 (打板专用, v3 加 2 板晋级率)
    前置条件 (全部满足才能进完整度评估):
    - 总涨停 >= 30 只 (市场有热度)
    - 最高板 >= 4 (有龙头空间)
    - 4+板数量 >= 2 只 (高板接力有量, 不独苗)
    - 2 板数量 >= 7 只 (顶级游资最低线)
    - 2 板晋级率 (今日2板/昨日1板) >= 25% (健康接力)
    不满足任一 → 直接 0, "前置未通过"

    多维度加权 (前置通过后):
    - 板位分布相似度 (cosine vs 理论金字塔): 50%
    - 高板存活率分位 (4+板占总涨停): 30%
    - 封板质量 (涨停 / (涨停+炸板)): 20%

    阈值: > 0.7 适合打板 | 0.5-0.7 观望 | < 0.5 不打
    """
    import math
    try:
        import akshare as ak
        from datetime import date, timedelta
        today_str = today.strftime("%Y%m%d") if hasattr(today, 'strftime') else today
        yesterday_str = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
        # 找最近一个有数据的交易日 (跳过周末)
        for d_back in range(1, 7):
            d_try = (date.today() - timedelta(days=d_back)).strftime("%Y%m%d")
            try:
                _df_try = ak.stock_zt_pool_em(date=d_try)
                if _df_try is not None and not _df_try.empty:
                    yesterday_str = d_try
                    break
            except:
                continue

        # 1. 今日涨停池板位分布
        df = ak.stock_zt_pool_em(date=today_str)
        if df is None or df.empty or "连板数" not in df.columns:
            return None

        counts = [0, 0, 0, 0, 0]  # 1板, 2板, 3板, 4板, 5+板
        for v in df["连板数"]:
            try:
                n = int(v)
            except (ValueError, TypeError):
                n = 1
            if n <= 1: counts[0] += 1
            elif n == 2: counts[1] += 1
            elif n == 3: counts[2] += 1
            elif n == 4: counts[3] += 1
            else: counts[4] += 1
        total = sum(counts)
        if total == 0:
            return None

        # 最高板 + 4+板 + 2板数
        max_board = max([int(v) for v in df["连板数"] if str(v).isdigit()] or [1])
        high_count = counts[3] + counts[4]
        n_2board = counts[1]

        # 2. 昨日涨停池 (算 2 板晋级率)
        n_1b_yesterday = 0
        try:
            df_y = ak.stock_zt_pool_em(date=yesterday_str)
            if df_y is not None and not df_y.empty:
                n_1b_yesterday = len(df_y)
        except:
            pass
        promote_rate = (n_2board / n_1b_yesterday) if n_1b_yesterday > 0 else 0

        # 3. 炸板数
        n_zb = 0
        try:
            df_zb = ak.stock_zt_pool_zbgc_em(date=today_str)
            n_zb = len(df_zb) if df_zb is not None and not df_zb.empty else 0
        except:
            pass

        # === 前置条件检查 (5 项) ===
        prelim_fail = []
        if total < 30:
            prelim_fail.append(f"涨停{total}只(<30, 市场冷)")
        if max_board < 4:
            prelim_fail.append(f"最高板{max_board}板(<4, 无龙头)")
        if high_count < 2:
            prelim_fail.append(f"4+板{high_count}只(<2, 高板独苗)")
        if n_2board < 7:
            prelim_fail.append(f"2板{n_2board}只(<7, 主流资金未动)")
        if promote_rate < 0.25 and n_1b_yesterday > 0:
            prelim_fail.append(f"2板晋级率{promote_rate*100:.0f}%(<25%, 接力弱)")

        if prelim_fail:
            return {
                "name": "金字塔完整度率", "value": 0, "unit": "/1.0 (前置未通过)",
                "score": 10,
                "tag": _tag(10, True), "grade": _grade(10)[0], "gradeColor": _grade(10)[1],
                "thresholds": "前置: 涨停≥30 + 最高≥4 + 4+板≥2 + 2板≥7 + 2板晋级率≥25%",
                "logic": f"板位 {counts} | 2板 {n_2board}只 | 2板晋级率 {promote_rate*100:.0f}% (昨{n_1b_yesterday}→今{n_2board}) | {'; '.join(prelim_fail)}",
                "danger": "前置未通过, 严禁打板",
                "ban_action": "不打",  # 暴露给策略
                "ban_score": 0,       # 打板强度分 (0=不打, 100=全力打)
            }

        # === 前置通过, 算完整度 ===
        actual = [c / total for c in counts]
        dot = sum(a * t for a, t in zip(actual, _PYRAMID_THEORY))
        norm_a = math.sqrt(sum(a*a for a in actual))
        norm_t = math.sqrt(sum(t*t for t in _PYRAMID_THEORY))
        cosine = dot / (norm_a * norm_t) if norm_a > 0 and norm_t > 0 else 0

        high_pct = high_count / total
        high_score = min(1.0, high_pct / 0.094) if high_pct > 0 else 0
        seal_quality = total / (total + n_zb) if (total + n_zb) > 0 else 0

        completeness = round(0.5 * cosine + 0.3 * high_score + 0.2 * seal_quality, 3)

        if completeness >= 0.7: score = 80
        elif completeness >= 0.5: score = 55
        elif completeness >= 0.3: score = 35
        else: score = 15

        if counts[0] < counts[1]:
            score = max(10, score - 30)

        # 打板建议
        if completeness >= 0.7:
            ban_action = "打"
        elif completeness >= 0.5:
            ban_action = "观望"
        else:
            ban_action = "不打"

        return {
            "name": "金字塔完整度率", "value": completeness, "unit": "/1.0 (打板评分)",
            "score": score,
            "tag": _tag(score, False), "grade": _grade(score)[0], "gradeColor": _grade(score)[1],
            "thresholds": "前置: 涨停≥30 + 最高≥4 + 4+板≥2 + 2板≥7 + 2板晋级率≥25% | 评分: >0.7 打 | 0.5-0.7 观望 | <0.5 不打",
            "logic": f"板位 {counts} | 2板 {n_2board}只 | 2板晋级率 {promote_rate*100:.0f}% | 4+板 {high_count}/{total}={high_pct*100:.0f}% | 炸板 {n_zb} | cosine={cosine:.2f}",
            "danger": "倒金字塔 (1板<2板) 严禁打板" if counts[0] < counts[1] else "连续 3 天 < 0.5 = 退潮期",
            "ban_action": ban_action,
            "ban_score": score,
        }
    except Exception as e:
        return None


def _get_all_a_spot_cached():
    """全 A 实时统计 (30min 缓存, 4 层兜底)"""
    import time
    now = time.time()
    if _ALL_A_V2_CACHE["data"] and (now - _ALL_A_V2_CACHE["ts"]) < _ALL_A_V2_CACHE["ttl"]:
        return _ALL_A_V2_CACHE["data"]
    # 调 backend 通用函数 (直接 import, 第一次会加载但不会循环, 后续 cached)
    try:
        import backend
        data = backend.get_all_a_snapshot()
        if data and data.get("total", 0) > 1000:
            _ALL_A_V2_CACHE["data"] = data
            _ALL_A_V2_CACHE["ts"] = now
            return data
    except Exception as e:
        # backend 还没 load 时 (主入口没启) 走兜底
        pass
    # 兜底: 自己拉一次 (单次 25s, 慢但能跑)
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot()
        if df is not None and not df.empty and len(df) > 1000:
            chgs = []
            for _, r in df.iterrows():
                try:
                    pct = float(str(r.get("涨跌幅", 0)).replace("%", "").replace("+", ""))
                    chgs.append(pct)
                except: pass
            data = {
                "chgs": chgs, "total": len(chgs),
                "n_up5": sum(1 for c in chgs if c >= 5),
                "n_up9": sum(1 for c in chgs if c >= 9),
                "n_drop5": sum(1 for c in chgs if c <= -5),
                "n_drop7": sum(1 for c in chgs if c <= -7),
                "n_drop10": sum(1 for c in chgs if c <= -9.8),
            }
            _ALL_A_V2_CACHE["data"] = data
            _ALL_A_V2_CACHE["ts"] = now
            return data
    except:
        pass
    return None


def indicator_9_new_high_low(today):
    """9. 新高/新低比"""
    try:
        df = ak_local.stock_a_high_low_statistics(symbol="all")
        if df is None or df.empty: return None
        latest = df.tail(1).iloc[0]
        h20 = int(latest.get("high20", 0))
        l20 = int(latest.get("low20", 0))
        ratio = round(h20 / max(l20, 1), 2)
        score = _pct_to_score(ratio, 0.1, 0.3, 0.5, 1.5, 2.5, 4.0)
        return {
            "name": "新高/新低比", "value": ratio, "unit": "倍", "score": score,
            "tag": _tag(score), "grade": _grade(score)[0], "gradeColor": _grade(score)[1],
            "thresholds": "强>1 | 中0.5-1 | 弱<0.5",
            "logic": "趋势结构退化", "danger": "新高<新低 持续3天=趋势结束"
        }
    except: return None


def indicator_10_cr10(today):
    """10. 成交集中度 CR10（涨停+炸板池前10成交额占比）"""
    try:
        df_zt, df_zb, _ = get_zt_data(today)
        all_rows = []
        for df in [df_zt, df_zb]:
            if df is not None and not df.empty:
                all_rows.extend([r.get("成交额", 0) for _, r in df.iterrows()])
        all_rows = sorted([x for x in all_rows if x > 0], reverse=True)
        total = sum(all_rows[:50])  # 前50只近似全市场
        top = sum(all_rows[:10])
        cr10 = round(top / max(total, 1) * 100, 2)
        score = _pct_to_score(cr10, 0, 0.5, 1.0, 2.0, 2.8, 4.0, reverse=True)
        return {
            "name": "成交集中度CR10", "value": cr10, "unit": "%", "score": score,
            "tag": _tag(score, True), "grade": _grade(score)[0], "gradeColor": _grade(score)[1],
            "thresholds": "健康<2% | 警惕2-3% | 过热>3% | 极值>4.5%",
            "logic": "前10只占全市场成交额=头部拥挤度", "danger": ">3%=踩踏风险 / >4.5%=极值"
        }
    except: return None


def indicator_11_margin_concentration(today):
    """11. 融资集中度（Top5行业）"""
    try:
        df = ak_local.stock_margin_underlying_info_szse(date=today)
        if df is None or df.empty:
            # 试上交所
            df = ak_local.stock_margin_underlying_info_sse(date=today)
        if df is None or df.empty: return None
        top5 = df.nlargest(5, "融资买入额")
        top5_sum = top5["融资买入额"].sum()
        total = df["融资买入额"].sum()
        concentration = round(top5_sum / max(total, 1) * 100, 1) if total > 0 else 0
        score = _pct_to_score(concentration, 0, 20, 35, 55, 70, 85, reverse=True)
        return {
            "name": "融资集中度Top5", "value": concentration, "unit": "%", "score": score,
            "tag": _tag(score, True), "grade": _grade(score)[0], "gradeColor": _grade(score)[1],
            "thresholds": "分散<35% | 集中35-55% | 高集中>60%",
            "logic": "杠杆扎堆方向=加速器", "danger": ">60%=强平踩踏风险"
        }
    except: return None


def indicator_12_volume_price_divergence(today):
    """12. 量价配合 = 涨停只数 vs 涨停股均成交额"""
    try:
        df_zt, _, _ = get_zt_data(today)
        if df_zt is None or df_zt.empty:
            return None
        zt_count = len(df_zt)
        today_amount = df_zt["成交额"].sum() if "成交额" in df_zt.columns else 0
        # 平均每只涨停股成交额
        avg_amt_per_zt = today_amount / zt_count if zt_count > 0 else 0
        avg_amt_yi = avg_amt_per_zt / 1e8  # 转换为亿
        # 综合数值: zt_count(只) + 涨停均额(亿)
        composite_str = str(zt_count) + "\u53ea/" + ("%.1f" % avg_amt_yi) + "\u4ebf"
        # 健康/危险判断
        if zt_count >= 60 and avg_amt_yi < 5:
            state, score = "\u7f29\u91cf\u6da8\u505c(\u5065\u5eb7)", 75
        elif zt_count >= 30 and avg_amt_yi < 10:
            state, score = "\u91cf\u4ef7\u914d\u5408", 60
        elif zt_count <= 30 and avg_amt_yi > 15:
            state, score = "\u653e\u91cf\u4e0d\u6da8(\u5371\u9669)", 30
        else:
            state, score = "\u91cf\u4ef7\u540c\u6b65", 60
        return {
            "name": "量价配合", "value": composite_str, "unit": "\u53ea/\u4ebf",
            "zt_count": zt_count, "avg_amt_yi": round(avg_amt_yi, 2),
            "state": state, "score": score,
            "tag": _tag(score), "grade": _grade(score)[0], "gradeColor": _grade(score)[1],
            "thresholds": "缩量涨停健康(<5亿/只) | 量价配合 | 放量不涨危险(>15亿/只)",
            "logic": "涨停只数 / 涨停均成交额=每只涨停的资金集中度",
            "danger": "放量不涨(涨停少+成交高)=主力出货"
        }
    except: return None


# ── 阶段判断 ──────────────────────────────────────────
def determine_stage(indicators):
    """根据多个指标判断当前市场阶段"""
    # 提取关键指标
    by_name = {i["name"]: i for i in indicators if i}
    zt = by_name.get("涨停家数", {}).get("value", 0)
    high_zb = by_name.get("高位炸板占比", {}).get("value", 100)
    big_face = by_name.get("大面占比", {}).get("value", 100)
    drop5 = by_name.get("跌幅<-5%股数", {}).get("value", 1000)
    yzt = by_name.get("赚钱效应", {}).get("score", 50)
    concentration = by_name.get("涨停TOP3板块集中度", {}).get("score", 50)
    pyramid = by_name.get("金字塔完整度率", {})

    # 综合分
    scores = [i["score"] for i in indicators if i and "score" in i]
    avg = sum(scores) / max(len(scores), 1)

    # === 金字塔信号 (强信号) ===
    pyramid_value = pyramid.get("value", 0.5)
    pyramid_ban = pyramid.get("ban_action", "观望")

    # 阶段判断
    # 优先级: 金字塔率 < 0.3 直接退潮 (硬性禁令, 胜过其他指标)
    if pyramid_value < 0.3:
        return "退潮期"
    # 修复期
    if zt < 30 and big_face > 15 and high_zb > 20:
        return "修复期"
    # 退潮期 (传统 + 金字塔辅助)
    elif high_zb > 15 and big_face > 12 and avg < 50:
        return "退潮期"
    # 主升期
    elif zt > 60 and high_zb < 8 and big_face < 8 and concentration > 70 and pyramid_value >= 0.7:
        return "主升期"
    # 启动期
    elif zt > 30 and high_zb < 15 and big_face < 12 and pyramid_value >= 0.5:
        return "启动期"
    elif avg < 45:
        return "退潮期"
    else:
        return "中性/轮动"


# ── 策略引擎 ──────────────────────────────────────────
def generate_strategy(indicators, stage, main_board=None):
    """根据阶段和指标生成策略建议"""
    # 提取金字塔完整度率
    pyramid = next((i for i in indicators if i.get("name") == "金字塔完整度率"), None)
    ban_action = pyramid.get("ban_action", "观望") if pyramid else "观望"
    ban_score = pyramid.get("ban_score", 0) if pyramid else 0
    pyramid_value = pyramid.get("value", 0) if pyramid else 0

    strategies = {
        "主升期": {
            "position": "60-80%",
            "direction": main_board or "AI算力/科技",
            "rhythm": "龙头低吸+强势追涨",
            "action": "可积极做主线，次日开盘-3%以内低吸龙头股",
            "risk": "高位炸板>15%时立即减仓至30%",
            "color": "loose",
            "ban_signal": f"打板: {ban_action} (金字塔率 {pyramid_value:.2f})",
        },
        "启动期": {
            "position": "30-50%",
            "direction": main_board or "试错新主线",
            "rhythm": "打板试错+小仓位跟随",
            "action": "试错小仓位，3板以上确认后加仓",
            "risk": "主线不明确时控制仓位50%以内",
            "color": "neutral",
            "ban_signal": f"打板: {ban_action} (金字塔率 {pyramid_value:.2f})",
        },
        "中性/轮动": {
            "position": "20-40%",
            "direction": "防御+题材轮动",
            "rhythm": "快进快出+打板",
            "action": "短线5日内的题材轮动，止损严",
            "risk": "主线不明确=不重仓",
            "color": "neutral",
            "ban_signal": f"打板: {ban_action} (金字塔率 {pyramid_value:.2f})",
        },
        "退潮期": {
            "position": "0-20%",
            "direction": "防御板块（银行/高股息/公用事业）",
            "rhythm": "防守+空仓",
            "action": "不参与高位股，防御板块低吸高股息",
            "risk": "涨停家数>50 + 高位炸板<5% 时考虑切换回主升",
            "color": "tight",
            "ban_signal": f"打板: {ban_action} (金字塔率 {pyramid_value:.2f})",
        },
        "修复期": {
            "position": "0-10%",
            "direction": "空仓观望",
            "rhythm": "等待止跌信号",
            "action": "空仓为主，等待跌停家数<10 + 涨停>30",
            "risk": "不要左侧抄底，等右侧",
            "color": "tight",
            "ban_signal": f"打板: {ban_action} (金字塔率 {pyramid_value:.2f})",
        }
    }
    return strategies.get(stage, strategies["中性/轮动"])


# ── 主入口 ──────────────────────────────────────────
def get_market_strategy():
    """主入口：返回所有指标 + 阶段 + 策略"""
    today = date.today().strftime("%Y%m%d")

    # 跑所有 13 个指标
    raw_indicators = [
        indicator_1_zt_count(today),
        indicator_2_boards(today),
        indicator_3_zbgc_rate(today),
        indicator_4_high_zbgc_ratio(today),
        indicator_5_yzt_performance(today),
        indicator_6_top3_concentration(today),
        indicator_7_big_face(today),
        indicator_8_drop5(today),
        indicator_9_new_high_low(today),
        indicator_10_cr10(today),
        indicator_11_margin_concentration(today),
        indicator_12_volume_price_divergence(today),
        indicator_pyramid_completeness(today),  # 13. 金字塔完整度率 (打板专用)
    ]
    indicators = [i for i in raw_indicators if i]

    # 综合分
    if indicators:
        avg = sum(i["score"] for i in indicators) / len(indicators)
    else:
        avg = 50

    # 主线板块（从涨停TOP3提取）
    top3_indicator = next((i for i in indicators if i["name"] == "涨停TOP3板块集中度"), None)
    main_board = None
    if top3_indicator and top3_indicator.get("top3"):
        main_board = list(top3_indicator["top3"].keys())[0]

    # 阶段判断
    stage = determine_stage(indicators)

    # 策略
    strategy = generate_strategy(indicators, stage, main_board)

    # 提取金字塔率 + 打板建议
    pyramid_ind = next((i for i in indicators if i.get("name") == "金字塔完整度率"), None)
    ban_signal = pyramid_ind.get("ban_signal") if pyramid_ind else None
    ban_action = pyramid_ind.get("ban_action") if pyramid_ind else "观望"
    pyramid_value = pyramid_ind.get("value", 0) if pyramid_ind else 0

    return {
        "timestamp": datetime.now().isoformat(),
        "compositeScore": round(avg, 1),
        "grade": _grade(avg)[0],
        "gradeColor": _grade(avg)[1],
        "stage": stage,
        "mainBoard": main_board,
        "strategy": strategy,
        "ban_signal": ban_signal,         # "打板: 不打 (金字塔率 0.00)"
        "ban_action": ban_action,         # "打" / "观望" / "不打"
        "pyramid_value": pyramid_value,   # 0-1
        "indicators": indicators
    }
