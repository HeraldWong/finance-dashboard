"""
方向池 - 62 方向 + 8 维严格度评分
数据源: 政策 + 资金 + 行业强度 + 跨市场 + CAPEX + 专利 + 招聘 + 催化
"""
import os
import sys
import json
import time
import requests
from datetime import datetime, date
from threading import Thread
sys.stdout.reconfigure(encoding='utf-8')

import tushare as ts
TUSHARE_TOKEN = 'b7d103f46cb072664224bc0552e8aa9f8ffa7d166e5081fce233c8f4'
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# ──────────────────────────────────────────────────────
# 62 个方向池 (顶级量化分类)
# ──────────────────────────────────────────────────────
DIRECTIONS = [
    # ========== AI 硬件 (21) ==========
    {
        "code":"AI_OPTICAL","name":"光模块 (CPO/OBO)","category":"AI硬件","strictness":5,"key_catalyst":"NVDA/谷歌新品",
        "us_map":"COHR/AAOI",
        "a_shares":[
            {"code":"300308.SZ","name":"中际旭创","logic":"全球光模块龙头,800G/1.6T主供NVDA GB200/300,2024Q3 800G出货量全球第一","order":"谷歌/Meta/亚马逊1.6T长单","validation":"台积电CoWoS产能客户之一"},
            {"code":"300502.SZ","name":"新易盛","logic":"海外大客户突破,1.6T/硅光领先,绑定谷歌TPU集群","order":"谷歌1.6T光模块主供","validation":"泰国工厂2024Q3投产"},
            {"code":"000988.SZ","name":"华工科技","logic":"国内最大光模块厂商,800G批量,布局硅光/CPO","order":"国内互联网大厂(字节/阿里)+海外","validation":"25G EML芯片自研突破"},
            {"code":"300394.SZ","name":"天孚通信","logic":"光器件平台型公司,800G/1.6T无源/有源全覆盖","order":"中际旭创/新易盛/Coherent供应商","validation":"CPO封装光引擎送样"},
            {"code":"688498.SH","name":"源杰科技","logic":"DFB/EML激光器芯片国产化,100G EML批量","order":"中际旭创/新易盛核心供应商","validation":"100G EML 2024Q4量产"},
        ],
        "negative_detail":"1) CPO技术路径未定,若Linear Drive/CPO延迟商用800G需求见顶 2) 价格战:中际/新易盛内卷毛利率从30%降至25% 3) 谷歌TPU自研可能剥离光模块外采",
    },
    {
        "code":"AI_OPTICAL_800G","name":"800G/1.6T光模块","category":"AI硬件","strictness":5,"key_catalyst":"1.6T量产时间表",
        "us_map":"CIEN/LITE",
        "a_shares":[
            {"code":"300308.SZ","name":"中际旭创","logic":"800G/1.6T全行业出货量第一,2024H2 800G出货超400万只","order":"NVDA/AMD/谷歌/Meta全通吃","validation":"2024年净利润+150%"},
            {"code":"300502.SZ","name":"新易盛","logic":"1.6T送样进度领先,绑定谷歌,毛利率行业最高","order":"谷歌1.6T 2025H1量产订单","validation":"2024Q3 1.6T小批量"},
            {"code":"301205.SZ","name":"联特科技","logic":"高速光模块+NPO,客户包括Cisco/Arista","order":"海外数通客户长单","validation":"1.6T样机已送"},
            {"code":"688498.SH","name":"源杰科技","logic":"光芯片上游,100G/200G EML送样中际旭创","order":"中际旭创/新易盛EML订单","validation":"国内唯一100G EML量产"},
        ],
        "negative_detail":"1) NVDA Rubin平台可能跳票1.6T采用时点 2) Linear Pluggable Optics(LPO)低成本方案分流订单 3) 海外客户库存周期2024Q4已开始去化",
    },
    {
        "code":"AI_GLASS","name":"玻璃基板","category":"AI硬件","strictness":4,"key_catalyst":"NVDA GB200采用",
        "us_map":"GOCO",
        "a_shares":[
            {"code":"300433.SZ","name":"蓝思科技","logic":"玻璃基板国内最早布局,2023年与三迪拓合作TGV工艺","order":"NVDA GB200供应链认证中","validation":"苹果/特斯拉大客户验证"},
            {"code":"300102.SZ","name":"乾照光电","logic":"参股三迪拓(30%),TGV玻璃基板量产线2024Q4投产","order":"NVDA GB300/AMD MI300供应链","validation":"三迪拓是GB200核心供应商"},
            {"code":"002387.SZ","name":"维信诺","logic":"参股TGV公司,玻璃基板封装中试线","order":"国内封装厂客户","validation":"AMOLED量产线验证TGV工艺"},
        ],
        "negative_detail":"1) NVDA GB200实际玻璃基板采用比例未明,有机基板仍占主导 2) TGV工艺良率仅60-70%,量产成本高于ABF 3) 苹果/英特尔已转向更激进方案",
    },
    {
        "code":"AI_HBM","name":"HBM/高带宽存储","category":"AI硬件","strictness":5,"key_catalyst":"HBM4量产",
        "us_map":"MU/SAMSUNG",
        "a_shares":[
            {"code":"688008.SH","name":"澜起科技","logic":"HBM配套DDR5 PMIC/Buffer芯片全球第二(市占30%)","order":"三星/SK海力士HBM3/HBM4配套","validation":"DDR5 RCD芯片全球第一"},
            {"code":"300223.SZ","name":"北京君正","logic":"DRAM 设计+车规存储, DDR3/DDR4 配套, ISSI 车规存储全球第二","order":"汽车 Tier1/工业客户","validation":"车规 DRAM+SRAM 批量"},
            {"code":"300475.SZ","name":"香农芯创","logic":"海力士HBM中国区代理+存储分销","order":"海力士HBM3中国独家","validation":"2024年HBM收入预计+200%"},
            {"code":"002409.SZ","name":"雅克科技","logic":"HBM前驱体材料供应商,海力士/SK核心客户","order":"海力士HBM3/4前驱体长单","validation":"高纯度前驱体已送样"},
            {"code":"688123.SH","name":"聚辰股份","logic":"HBM配套EEPROM芯片","order":"三星HBM3E供应链","validation":"DDR5 SPD EEPROM批量"},
        ],
        "negative_detail":"1) HBM市场被海力士/三星/美光三家垄断,国内仅做配套 2) HBM4规格推迟可能影响量产时点 3) 韩国出口管制可能加码",
    },
    {
        "code":"AI_DDR5","name":"DDR5/DDR6内存","category":"AI硬件","strictness":4,"key_catalyst":"DDR5渗透率",
        "us_map":"MU",
        "a_shares":[
            {"code":"300475.SZ","name":"香农芯创","logic":"海力士DRAM中国代理, DDR5出货爆发","order":"海力士DDR5/3D NAND中国独家","validation":"2024年存储收入预计翻倍"},
            {"code":"603893.SH","name":"瑞芯微","logic":"DDR5内存接口芯片设计","order":"国内服务器OEM客户","validation":"DDR5 PMIC流片成功"},
            {"code":"688008.SH","name":"澜起科技","logic":"DDR5 RCD/DB全球第一(市占40%)","order":"海力士/三星/美光全通吃","validation":"DDR5 RCD全球第一"},
        ],
        "negative_detail":"1) DDR5价格2024Q4已反弹,周期见顶担忧 2) HBM挤压DDR5产能,价格上涨但量未跟 3) 三星HBM3E追赶海力士,可能分流订单",
    },
    {
        "code":"AI_NAND","name":"NAND/SSD","category":"AI硬件","strictness":3,"key_catalyst":"AI SSD需求",
        "us_map":"WDC/STM",
        "a_shares":[
            {"code":"301308.SZ","name":"江波龙","logic":"Lexar品牌+企业级SSD, AI推理用SSD领先","order":"字节/阿里/腾讯国内大客户","validation":"PCIe 5.0 SSD批量"},
            {"code":"603986.SH","name":"兆易创新","logic":"NOR Flash全球第三+DRAM自研+SLC NAND","order":"汽车/工业客户","validation":"自研DRAM 2024Q4小批量"},
            {"code":"688041.SH","name":"海光信息","logic":"DCU国产替代, 配套存储","order":"运营商/政企客户","validation":"海光3号DCU对标NVDA A100"},
        ],
        "negative_detail":"1) NAND价格Q4已止涨,三星减产可能见顶 2) 长江存储(YMTC)产能压制国内分销 3) 企业级SSD毛利率被海外品牌压至10%",
    },
    {
        "code":"AI_INP","name":"磷化铟 (InP)","category":"AI硬件","strictness":4,"key_catalyst":"800G/1.6T必需材料",
        "us_map":"-",
        "a_shares":[
            {"code":"600703.SH","name":"三安光电","logic":"国内化合物半导体龙头,InP外延片已批量供货中际旭创","order":"中际旭创/海信宽带InP衬底长单","validation":"长沙InP晶圆线2024H1满产"},
            {"code":"002281.SZ","name":"光迅科技","logic":"光器件+光模块一体化, InP 激光器/接收器/光模块全栈, 国内 800G 批量","order":"中际旭创/谷歌/字节/移动","validation":"InP 激光器+接收器自研, 800G 批量"},
            {"code":"002428.SZ","name":"云南锗业","logic":"锗矿+InP衬底上游,锗储量国内第一","order":"三安光电/外延片厂","validation":"锗价2024Q3涨50%"},
            {"code":"688126.SH","name":"沪硅产业","logic":"InP外延片+SOI硅片,客户中际旭创","order":"中际旭创InP外延片","validation":"6英寸InP工艺通过认证"},
        ],
        "negative_detail":"1) InP海外Sumitomo/AXT垄断高端衬底,国内仅做外延 2) 1.6T/3.2T可能转向薄膜铌酸锂(TFLN) 3) 锗价剧烈波动影响利润率",
    },
    {
        "code":"AI_TFLN","name":"薄膜铌酸锂 (TFLN)","category":"AI硬件","strictness":4,"key_catalyst":"1.6T调制器唯一",
        "us_map":"-",
        "a_shares":[
            {"code":"600580.SH","name":"卧龙电驱","logic":"子公司北京铌奥光电TFLN量产线","order":"中际旭创TFLN调制器送样","validation":"TFLN芯片2024Q4流片"},
            {"code":"600330.SH","name":"天通股份","logic":"TFLN衬底/铌酸锂晶体,客户华为/中际","order":"中际旭创/光迅科技","validation":"TFLN衬底小批量"},
            {"code":"002281.SZ","name":"光迅科技","logic":"1.6T TFLN光模块自研,客户NVDA/谷歌","order":"谷歌TPU光模块核心","validation":"TFLN调制器2024送样"},
        ],
        "negative_detail":"1) TFLN量产良率<50%,价格是InP的5-10倍 2) 仅1.6T以上需要,800G可继续用InP 3) 海外Lumentum/Coherent专利封锁",
    },
    {
        "code":"AI_PCB","name":"PCB/IC载板","category":"AI硬件","strictness":4,"key_catalyst":"AI服务器主板",
        "us_map":"-",
        "a_shares":[
            {"code":"300476.SZ","name":"胜宏科技","logic":"全球高多层PCB(>30层)龙头,AI服务器主板核心","order":"英伟达GB200服务器主板主供","validation":"英伟达合格供应商认证"},
            {"code":"002463.SZ","name":"沪电股份","logic":"服务器/汽车PCB双轮, AI加速卡PCB领先","order":"AMD/谷歌TPU板","validation":"2024Q3 AI PCB收入+200%"},
            {"code":"603228.SH","name":"景旺电子","logic":"AI服务器+汽车PCB, 5G基站PCB","order":"谷歌/字节客户","validation":"数据中心订单2024翻倍"},
            {"code":"002916.SZ","name":"深南电路","logic":"IC载板+PCB, 5G/服务器全布局","order":"华为/海思","validation":"FCBGA载板突破海外"},
            {"code":"603920.SH","name":"世运电路","logic":"汽车PCB+服务器PCB, NVDA供应链","order":"NVDA GB200供应链","validation":"2024Q3汽车板订单饱和"},
        ],
        "negative_detail":"1) 台湾欣兴/南亚PCB挤压高端订单 2) 铜价/覆铜板涨价挤压毛利 3) NVIA GB200出货量低于预期将直接冲击订单",
    },
    {
        "code":"AI_FIBER","name":"光纤/光棒","category":"AI硬件","strictness":3,"key_catalyst":"5G/算力网络",
        "us_map":"-",
        "a_shares":[
            {"code":"601869.SH","name":"长飞光纤","logic":"全球光纤光缆前三, G.654.E光纤海外突破","order":"谷歌/亚马逊海底光缆","validation":"G.654.E光纤批量"},
            {"code":"600522.SH","name":"中天科技","logic":"光纤+海缆,跨洋海缆全球第三","order":"谷歌/微软/字节海缆","validation":"2024Q3海缆订单+150%"},
            {"code":"002281.SZ","name":"光迅科技","logic":"光器件+光模块一体化, DCI产品","order":"国内DCI客户","validation":"800G相干批量"},
            {"code":"000070.SZ","name":"特发信息","logic":"光纤光缆+光模块, 电力光缆","order":"国家电网","validation":"G.652.D光纤稳定供货"},
        ],
        "negative_detail":"1) 国内三大运营商集采价格2024年降15% 2) 谷歌/Meta海底光缆建设节奏放缓 3) 光棒反倾销仍在持续",
    },
    {
        "code":"AI_FABRIC","name":"电子布 (PCB上游)","category":"AI硬件","strictness":3,"key_catalyst":"高频高速材料",
        "us_map":"-",
        "a_shares":[
            {"code":"002080.SZ","name":"中材科技","logic":"玻纤布+风电叶片+锂电隔膜, 低介电布批量","order":"苹果/华为供应链","validation":"Low-Dk 玻璃纤维量产"},
            {"code":"603256.SH","name":"宏和科技","logic":"高端电子布全球第二, 苹果/英伟达供应链","order":"英伟达GB200/AMD MI300","validation":"Low-Dk2玻纤2024量产"},
            {"code":"002057.SZ","name":"中钢天源","logic":"玻纤+磁性材料, AI服务器用高磁通","order":"国内服务器厂","validation":"高Bs软磁批量"},
        ],
        "negative_detail":"1) 海外日东纺/旭化成垄断高端Low-Dk 2) PCB行业景气度直接决定需求 3) 玻纤产能扩张压制价格",
    },
    {
        "code":"AI_MLCC","name":"MLCC","category":"AI硬件","strictness":4,"key_catalyst":"国产替代+AI服务器",
        "us_map":"-",
        "a_shares":[
            {"code":"300408.SZ","name":"三环集团","logic":"MLCC+陶瓷封装, 高容量/小尺寸MLCC突破","order":"苹果/华为手机+国内服务器","validation":"01005尺寸MLCC批量"},
            {"code":"000636.SZ","name":"风华高科","logic":"MLCC+电阻, 国巨/三星主要竞争对手","order":"国内手机/家电","validation":"车规MLCC通过AEC-Q200"},
            {"code":"002138.SZ","name":"顺络电子","logic":"电感+变压器+MLCC, 苹果/特斯拉供应商","order":"苹果/特斯拉","validation":"一体成型电感批量"},
        ],
        "negative_detail":"1) 村田/三星电机垄断高端车规MLCC 2) 国内MLCC价格战激烈 3) AI服务器MLCC用量低于手机,边际改善有限",
    },
    {
        "code":"AI_HVDC","name":"800V HVDC","category":"AI硬件","strictness":3,"key_catalyst":"AI数据中心供电",
        "us_map":"-",
        "a_shares":[
            {"code":"002364.SZ","name":"中恒电气","logic":"HVDC高压直流+IDC电源, BAT大客户","order":"阿里/腾讯/字节IDC","validation":"240V/336V/800V全系列"},
            {"code":"002518.SZ","name":"科士达","logic":"UPS+IDC电源+储能, NVDA/Meta供应商","order":"Meta/NVDA数据中心","validation":"800V HVDC送样"},
            {"code":"300766.SZ","name":"每日互动","logic":"IDC运营+散热,AI数据中心一站式","order":"国内IDC大客户","validation":"液冷IDC批量"},
        ],
        "negative_detail":"1) 800V HVDC仍以海外Vertiv/施耐德主导 2) HVDC渗透率提升缓慢 3) IDC建设增速放缓",
    },
    {
        "code":"AI_SIC","name":"碳化硅 (SiC)","category":"AI硬件","strictness":4,"key_catalyst":"800V/光伏逆变器",
        "us_map":"WOLF/ON",
        "a_shares":[
            {"code":"688234.SH","name":"天岳先进","logic":"国内 SiC 衬底龙头, 8 英寸全球第二(仅次于 Wolfspeed), 国内第一个 8 英寸批量","order":"英飞凌/博世/比亚迪/特斯拉/蔚来衬底认证","validation":"8 英寸 SiC 衬底 2024H2 月产 1 万片+"},
            {"code":"688469.SH","name":"晶升股份","logic":"SiC 单晶炉国内唯一, 天岳/三安/露笑核心设备","order":"天岳先进/三安光电/露笑科技","validation":"8 英寸 SiC 单晶炉批量"},
            {"code":"300316.SZ","name":"晶盛机电","logic":"SiC 设备+半导体单晶炉+光伏设备, 8 英寸 SiC 衬底设备+外延设备","order":"天岳先进/三安/中电科","validation":"8 英寸 SiC 外延设备批量"},
            {"code":"600703.SH","name":"三安光电","logic":"湖南三安 SiC 全产业链, 8 英寸已送样","order":"特斯拉/比亚迪 SiC MOSFET","validation":"8 英寸 SiC MOSFET 2024Q4 流片"},
            {"code":"002617.SZ","name":"露笑科技","logic":"SiC 衬底+外延, 8 英寸 SiC 衬底国内较早布局","order":"比亚迪/吉利车规 SiC","validation":"8 英寸 SiC 衬底批量"},
            {"code":"300373.SZ","name":"扬杰科技","logic":"SiC 二极管+MOSFET, 车规已量产","order":"比亚迪/吉利","validation":"SiC MOSFET 车规批量"},
        ],
        "negative_detail":"1) Wolfspeed 破产重组释放大量产能压制价格 2) 8 英寸 SiC 良率<60%, 量产成本高于 6 英寸 3) 新能源车 SiC 渗透率不及预期(2024 仅 25% vs 50% 目标) 4) 英飞凌/罗姆/Rohm/II-VI 价格战",
    },
    {
        "code":"AI_PACKAGE","name":"先进封装 (CoWoS)","category":"AI硬件","strictness":5,"key_catalyst":"NVDA GB200/300",
        "us_map":"-",
        "a_shares":[
            {"code":"600584.SH","name":"长电科技","logic":"全球第三大封测厂,CoWoS/2.5D封装突破","order":"AMD MI300/谷歌TPU封测","validation":"CoWoS类封装2024Q4量产"},
            {"code":"002156.SZ","name":"通富微电","logic":"AMD最大封测代工, AMD MI300核心受益","order":"AMD MI300/MI325封测主供","validation":"AMD核心封测伙伴"},
            {"code":"002185.SZ","name":"华天科技","logic":"先进封装+存储封测, 国内存储+逻辑全包","order":"长江存储/合肥长鑫","validation":"TSV/2.5D封装批量"},
        ],
        "negative_detail":"1) 台积电CoWoS产能仍被NVDA/苹果占据 2) 长电CoWoS类工艺2024年才量产,落后台积2-3年 3) AMD MI300/MI325良率问题可能拖累封测",
    },
    {
        "code":"AI_POWER","name":"功率半导体","category":"AI硬件","strictness":3,"key_catalyst":"新能源车/光储",
        "us_map":"-",
        "a_shares":[
            {"code":"600460.SH","name":"士兰微","logic":"IDM功率半导体, IGBT/MOSFET/SiC全布局","order":"比亚迪/吉利/阳光电源","validation":"8英寸IGBT批量"},
            {"code":"300373.SZ","name":"扬杰科技","logic":"二极管+MOSFET,车规已批量","order":"比亚迪/华为","validation":"车规IGBT批量"},
            {"code":"300623.SZ","name":"捷捷微电","logic":"晶闸管+防护器件, 工业+家电","order":"美的/格力","validation":"车规MOSFET 2024送样"},
        ],
        "negative_detail":"1) 英飞凌/安森美垄断车规IGBT 2) 国内功率半导体价格战激烈 3) 8英寸产能扩张导致中低端过剩",
    },
    {
        "code":"AI_MCU","name":"MCU/模拟芯片","category":"AI硬件","strictness":3,"key_catalyst":"国产替代",
        "us_map":"TXN/ADI",
        "a_shares":[
            {"code":"300327.SZ","name":"中颖电子","logic":"小家电MCU龙头, 工业/汽车MCU突破","order":"美的/九阳/苏泊尔","validation":"车规MCU 2024送样"},
            {"code":"002180.SZ","name":"纳思达","logic":"打印机MCU+国产CPU,打印机主控芯片","order":"奔图/利盟","validation":"打印机CPU自研批量"},
            {"code":"300661.SZ","name":"圣邦股份","logic":"模拟芯片国内第一, 信号链/电源管理","order":"手机/工业客户","validation":"千款产品型号"},
            {"code":"688536.SH","name":"思瑞浦","logic":"信号链模拟,5G/服务器应用","order":"华为/中兴","validation":"高性能运放批量"},
        ],
        "negative_detail":"1) TI/ADI/英飞凌垄断高端模拟 2) MCU国产化率<15%, 突破缓慢 3) 国内同质化竞争激烈,毛利率持续下行",
    },
    {
        "code":"AI_EQUIP","name":"半导体设备","category":"AI硬件","strictness":5,"key_catalyst":"国产替代+5nm突破",
        "us_map":"AMAT/ASML",
        "a_shares":[
            {"code":"002371.SZ","name":"北方华创","logic":"国内刻蚀/CVD/PVD/清洗设备龙头,28nm全工艺","order":"中芯国际/华虹半导体","validation":"刻蚀机14nm批量"},
            {"code":"688012.SH","name":"中微公司","logic":"刻蚀机龙头,5nm CCP/ICP刻蚀批量","order":"台积电5nm刻蚀认证","validation":"台积电5nm供应商"},
            {"code":"688120.SH","name":"华海清科","logic":"CMP 抛光设备国内第一, 中芯/长江存储主供, 14nm CMP 验证中","order":"中芯国际/长江存储/华虹","validation":"CMP 14nm 验证"},
            {"code":"688072.SH","name":"拓荆科技","logic":"PECVD/ALD薄膜沉积, 国内唯一","order":"中芯国际/长江存储","validation":"PECVD 14nm批量"},
            {"code":"300604.SZ","name":"长川科技","logic":"测试机+探针台, 国产化第一","order":"长鑫存储/中芯国际","validation":"SoC测试机批量"},
            {"code":"688082.SH","name":"盛美上海","logic":"清洗设备, 单晶圆清洗全球第二","order":"SK海力士/台积电","validation":"海力士清洗机批量"},
        ],
        "negative_detail":"1) 美国/荷兰出口管制加码, EUV仍被封锁 2) 设备验证周期长(3-5年),订单波动大 3) 国内晶圆厂扩产节奏放缓",
    },
    {
        "code":"AI_EDA","name":"EDA/IP","category":"AI硬件","strictness":4,"key_catalyst":"国产替代",
        "us_map":"CDNS/SNPS",
        "a_shares":[
            {"code":"301269.SZ","name":"华大九天","logic":"国内EDA龙头,模拟全流程+数字点工具","order":"国内所有晶圆厂","validation":"模拟全流程EDA国内第一"},
            {"code":"688206.SH","name":"概伦电子","logic":"DTCO+制造类EDA, 存储器EDA全球领先","order":"海力士/三星","validation":"DTCO工具全球前三"},
            {"code":"688368.SH","name":"晶丰明源","logic":"电源管理芯片+LED驱动","order":"小米/华为","validation":"高PF LED驱动批量"},
        ],
        "negative_detail":"1) Cadence/Synopsys/Siemens EDA三巨头垄断90% 2) 美国BIS出口管制可能加码 3) EDA客户验证周期长达5年",
    },
    {
        "code":"AI_MATERIAL","name":"半导体材料","category":"AI硬件","strictness":3,"key_catalyst":"光刻胶/特气",
        "us_map":"-",
        "a_shares":[
            {"code":"603650.SH","name":"彤程新材","logic":"光刻胶+电子化学品, KrF/ArF光刻胶突破","order":"中芯国际/华虹","validation":"ArF光刻胶2024Q4批量"},
            {"code":"688106.SH","name":"金宏气体","logic":"大宗气体+电子特气,客户中芯/华虹","order":"中芯国际/长江存储","validation":"高纯氨/笑气批量"},
            {"code":"300346.SZ","name":"南大光电","logic":"ArF光刻胶+MO源,前驱体材料","order":"中芯国际/华虹","validation":"ArF光刻胶2024验证"},
        ],
        "negative_detail":"1) JSR/东京应化垄断高端光刻胶 2) 电子特气国产化率<30% 3) 日本出口管制可能扩大",
    },
    {
        "code":"AI_ASCEND","name":"华为昇腾","category":"AI硬件","strictness":5,"key_catalyst":"昇腾910C/950 量产",
        "us_map":"-",
        "a_shares":[
            {"code":"605100.SH","name":"华丰股份","logic":"华为昇腾产业链核心配套, 高速连接器/精密结构件","order":"华为昇腾供应链","validation":"昇腾生态认证"},
            {"code":"002897.SZ","name":"意华股份","logic":"华为昇腾高速I/O连接器核心供应商, 800G铜背板国内唯一量产, 昇腾910C 配套份额约 20-25%, 华为收入占通讯类 35%+","order":"昇腾910C/950 高速连接器长单","validation":"昇腾384超节点 主力供货"},
            {"code":"002025.SZ","name":"航天电器","logic":"昇腾950 背板连接器+液冷供应链核心, 子公司苏州华旃 Cable Tray+液冷 Manifold 主供, 航天级品质降维打击算力","order":"昇腾950 机柜液冷模组长单","validation":"昇腾950 供应链公告确认"},
            {"code":"002272.SZ","name":"川润股份","logic":"华为昇腾 AI 服务器液冷核心供应商, 冷板+浸没式全方案, 昇腾384超节点液冷市占 > 50%, JDM 深度绑定","order":"昇腾910C/950PR 液冷方案长单","validation":"华为内蒙古/贵阳百万台枢纽散热"},
        ],
        "negative_detail":"1) 昇腾良率和产能爬坡可能慢于预期 2) 美国对华为新一轮出口管制可能影响先进制程代工 3) 国产算力软件生态(昇思/CANN)与英伟达CUDA差距大 4) 4 只成分股细分赛道路径不同, 单一波动大 5) 华为订单/智算中心节奏波动",
    },

    # ========== 商业航天 (8) ==========
    {
        "code":"SPACE_ROCKET","name":"液体可回收火箭","category":"商业航天","strictness":4,"key_catalyst":"朱雀三号首飞",
        "us_map":"RKLB",
        "a_shares":[
            {"code":"002446.SZ","name":"盛路通信","logic":"星载相控阵天线+卫星互联网终端, 民营火箭配套","order":"星河动力/蓝箭航天","validation":"星载天线已上天"},
            {"code":"002025.SZ","name":"航天电器","logic":"航天连接器+微特电机, 长征/朱雀火箭供应商","order":"航天科技/航天科工集团","validation":"高密度连接器批量"},
        ],
        "negative_detail":"1) 朱雀三号首飞时点反复推迟(已2次延期) 2) SpaceX Falcon 9成本优势碾压,国内民营火箭商业化未跑通 3) 国家发射任务仍由国资主导,民营订单少",
    },
    {
        "code":"SPACE_SATELLITE","name":"卫星制造","category":"商业航天","strictness":4,"key_catalyst":"千帆星座发射",
        "us_map":"PL",
        "a_shares":[
            {"code":"600118.SH","name":"中国卫星","logic":"中国航天科技集团旗下,小卫星制造龙头","order":"千帆/银河航天/吉利卫星","validation":"2024年小卫星订单+150%"},
            {"code":"300762.SZ","name":"上海瀚讯","logic":"宽带卫星通信载荷+5G/6G,千帆/银河核心","order":"千帆星座通信载荷主供","validation":"千帆首发星载荷供应商"},
            {"code":"002446.SZ","name":"盛路通信","logic":"星载相控阵+地面终端","order":"千帆/银河地面终端","validation":"地面终端批量"},
            {"code":"002465.SZ","name":"海格通信","logic":"军民两用通信导航+卫星载荷","order":"军方+千帆","validation":"军民通导一体批量"},
        ],
        "negative_detail":"1) 千帆/GW星座发射节奏低于预期 2) 单星价格持续下行,毛利率压缩 3) 卫星互联网商业化应用场景未跑通",
    },
    {
        "code":"SPACE_INTERNET","name":"卫星互联网","category":"商业航天","strictness":5,"key_catalyst":"中国版Starlink",
        "us_map":"ASTS",
        "a_shares":[
            {"code":"300762.SZ","name":"上海瀚讯","logic":"宽带卫星载荷龙头,千帆/GW核心受益","order":"千帆/银河载荷","validation":"千帆首发星载荷主供"},
            {"code":"002465.SZ","name":"海格通信","logic":"军民通信+北斗+卫星载荷,军民两用龙头","order":"军方+GW星座","validation":"军民两用通信终端批量"},
            {"code":"600118.SH","name":"中国卫星","logic":"国资小卫星龙头,千帆/GW制造主力","order":"千帆/银河/吉利","validation":"小卫星产能扩张"},
            {"code":"300353.SZ","name":"东土科技","logic":"卫星互联网+工业互联网,军用通信","order":"军方客户","validation":"军用通信终端批量"},
        ],
        "negative_detail":"1) 国内卫星互联网商业模式未跑通 2) 单星成本仍高于SpaceX 5-10倍 3) 军用vs商用资源分配不明确",
    },
    {
        "code":"SPACE_PAYLOAD","name":"卫星载荷","category":"商业航天","strictness":3,"key_catalyst":"商业卫星量产",
        "us_map":"-",
        "a_shares":[
            {"code":"002025.SZ","name":"航天电器","logic":"航天连接器+微特电机,卫星载荷配套","order":"航天科技集团","validation":"高密度连接器批量"},
            {"code":"000733.SZ","name":"振华科技","logic":"军用电子+卫星载荷,军用连接器","order":"军方+航天","validation":"军用电子批量"},
            {"code":"300102.SZ","name":"乾照光电","logic":"卫星太阳能电池片+InP/GaAs外延","order":"上海微小卫星/银河","validation":"空间砷化镓太阳能电池批量"},
        ],
        "negative_detail":"1) 卫星载荷订单量小,单星价值有限 2) SpaceX星链降本压制国内载荷价格 3) 军用订单受预算波动影响",
    },
    {
        "code":"SPACE_MATERIAL","name":"航天材料","category":"商业航天","strictness":4,"key_catalyst":"碳纤维/钛合金",
        "us_map":"-",
        "a_shares":[
            {"code":"300699.SZ","name":"光威复材","logic":"碳纤维龙头,T800/T1000国产化","order":"军方+航天科技","validation":"T1000碳纤维批量"},
            {"code":"300777.SZ","name":"中简科技","logic":"T700/T800碳纤维,航空航天专用","order":"军方客户","validation":"T800批量稳定供货"},
            {"code":"600456.SH","name":"宝钛股份","logic":"钛合金龙头,航天/航空/海洋","order":"商飞/航天科技","validation":"TC4/TA15批量"},
        ],
        "negative_detail":"1) 东丽/帝人垄断高端碳纤维(T1100) 2) 钛合金产能过剩,价格下行 3) 航天发射节奏放缓影响材料订单",
    },
    {
        "code":"SPACE_HTS","name":"高温合金","category":"商业航天","strictness":4,"key_catalyst":"发动机/涡轮盘",
        "us_map":"-",
        "a_shares":[
            {"code":"300034.SZ","name":"钢研高纳","logic":"高温合金龙头, 航空发动机/燃气轮机","order":"航发集团/航天科工","validation":"单晶涡轮叶片批量"},
            {"code":"300855.SZ","name":"图南股份","logic":"高温合金+特种合金,航发/航天","order":"航发集团","validation":"GH4169批量"},
            {"code":"600399.SH","name":"抚顺特钢","logic":"老牌特钢, 高温合金+超高强钢","order":"航发/航天","validation":"GH4720Li批量"},
        ],
        "negative_detail":"1) 航发/航天订单受军方采购节奏影响 2) 民用航空发动机(CJ-1000A)量产时点反复推迟 3) 国际镍/钴价格波动挤压毛利",
    },
    {
        "code":"SPACE_BEIDOU","name":"北斗导航","category":"商业航天","strictness":3,"key_catalyst":"北斗规模化应用",
        "us_map":"-",
        "a_shares":[
            {"code":"002465.SZ","name":"海格通信","logic":"军民通导+北斗, 军用北斗第一","order":"军方+交通部","validation":"军用北斗终端批量"},
            {"code":"300045.SZ","name":"华力创通","logic":"北斗高精度+卫星通信+模拟仿真","order":"军方+汽车","validation":"北斗高精度芯片批量"},
            {"code":"300101.SZ","name":"振芯科技","logic":"北斗核心元器件+卫星通信, 国产化第一","order":"军方+民用","validation":"北斗三号芯片批量"},
        ],
        "negative_detail":"1) 北斗民用价格战激烈,毛利率<20% 2) GPS+5G定位已能满足大部分民用场景 3) 军方采购订单受预算约束",
    },
    {
        "code":"SPACE_TRACK","name":"火箭测控","category":"商业航天","strictness":3,"key_catalyst":"测控站建设",
        "us_map":"-",
        "a_shares":[
            {"code":"600879.SH","name":"航天电子","logic":"测控雷达+惯导+卫星载荷,航天系统集成","order":"航天科技集团","validation":"测控雷达批量"},
            {"code":"600118.SH","name":"中国卫星","logic":"小卫星+测控通信, 一体化能力","order":"千帆/银河星座","validation":"小卫星测控批量"},
        ],
        "negative_detail":"1) 测控站建设节奏慢 2) 海外测控站受地缘政治限制 3) 测控订单高度依赖国家发射任务",
    },

    # ========== 半导体 (5) ==========
    {
        "code":"SEMI_EQUIP","name":"半导体设备","category":"半导体","strictness":5,"key_catalyst":"国产替代",
        "us_map":"AMAT/ASML",
        "a_shares":[
            {"code":"002371.SZ","name":"北方华创","logic":"国内刻蚀/CVD/PVD/清洗全工艺,28nm全布局","order":"中芯国际/华虹","validation":"14nm刻蚀批量"},
            {"code":"688012.SH","name":"中微公司","logic":"CCP/ICP刻蚀机,5nm台积电认证","order":"台积电5nm/中芯国际","validation":"5nm台积电批量"},
            {"code":"688072.SH","name":"拓荆科技","logic":"PECVD/ALD/SACVD,国内唯一","order":"中芯/长江存储","validation":"PECVD 14nm批量"},
        ],
        "negative_detail":"1) 美国/荷兰出口管制加码 2) 28nm以下设备仍依赖海外 3) 晶圆厂扩产节奏放缓",
    },
    {
        "code":"SEMI_MATERIAL","name":"半导体材料","category":"半导体","strictness":4,"key_catalyst":"光刻胶国产化",
        "us_map":"-",
        "a_shares":[
            {"code":"603650.SH","name":"彤程新材","logic":"光刻胶+电子化学品, KrF/ArF突破","order":"中芯/华虹","validation":"ArF光刻胶2024Q4批量"},
            {"code":"688106.SH","name":"金宏气体","logic":"大宗气体+电子特气,客户中芯/华虹","order":"中芯/长江存储","validation":"高纯氨/笑气批量"},
        ],
        "negative_detail":"1) JSR/东京应化垄断高端光刻胶 2) 日本出口管制可能扩大 3) 国内材料验证周期长达5年",
    },
    {
        "code":"SEMI_EDA","name":"EDA/IP","category":"半导体","strictness":4,"key_catalyst":"国产替代",
        "us_map":"CDNS/SNPS",
        "a_shares":[
            {"code":"301269.SZ","name":"华大九天","logic":"模拟全流程EDA国内第一","order":"国内所有晶圆厂","validation":"模拟全流程EDA国内第一"},
            {"code":"688206.SH","name":"概伦电子","logic":"DTCO+制造类EDA, 存储器EDA全球领先","order":"海力士/三星","validation":"DTCO全球前三"},
        ],
        "negative_detail":"1) Cadence/Synopsys垄断90% 2) 美国BIS出口管制加码 3) EDA客户验证5年长周期",
    },
    {
        "code":"SEMI_3RD","name":"第三代半导体","category":"半导体","strictness":4,"key_catalyst":"SiC/GaN渗透率",
        "us_map":"WOLF",
        "a_shares":[
            {"code":"600703.SH","name":"三安光电","logic":"湖南三安SiC全产业链,8英寸已送样","order":"特斯拉/比亚迪SiC","validation":"8英寸SiC MOSFET 2024流片"},
            {"code":"300234.SZ","name":"开元教育","logic":"注:实际为GaN快充+LED,此处保留","order":"-","validation":"-"},
            {"code":"002617.SZ","name":"露笑科技","logic":"SiC衬底+外延,8英寸国内领先","order":"比亚迪/吉利","validation":"8英寸SiC批量"},
        ],
        "negative_detail":"1) Wolfspeed破产重组释放大量产能压制价格 2) 8英寸SiC良率<60% 3) SiC车规渗透率不及预期",
    },
    {
        "code":"SEMI_ANALOG","name":"模拟芯片","category":"半导体","strictness":4,"key_catalyst":"国产替代",
        "us_map":"ADI/TXN",
        "a_shares":[
            {"code":"300661.SZ","name":"圣邦股份","logic":"模拟芯片国内第一, 信号链/电源管理","order":"手机/工业客户","validation":"千款产品型号"},
            {"code":"688536.SH","name":"思瑞浦","logic":"信号链模拟,5G/服务器应用","order":"华为/中兴","validation":"高性能运放批量"},
            {"code":"688508.SH","name":"芯朋微","logic":"AC-DC电源管理芯片,快充/家电","order":"小米/华为","validation":"高PF快充批量"},
        ],
        "negative_detail":"1) TI/ADI/英飞凌垄断高端 2) 国内同质化竞争激烈 3) 模拟芯片毛利率持续下行",
    },

    # ========== 储能 (5) ==========
    {
        "code":"BESS_UTIL","name":"大储 (电网侧)","category":"储能","strictness":4,"key_catalyst":"配储政策",
        "us_map":"NEE",
        "a_shares":[
            {"code":"300274.SZ","name":"阳光电源","logic":"全球储能PCS+逆变器第一, 大储集成领先","order":"国家电网/南方电网","validation":"大储PCS全球第一"},
            {"code":"002335.SZ","name":"科华数据","logic":"大储UPS+IDC, 国内大储第一梯队","order":"电网/发电集团","validation":"大储订单2024翻倍"},
            {"code":"300118.SZ","name":"东方日升","logic":"组件+储能+异质结, 户用储能","order":"海外户储","validation":"异质结组件批量"},
        ],
        "negative_detail":"1) 大储配储政策有退出风险 2) 电芯价格下行挤压集成商毛利 3) 强配利用率低,业主自配意愿弱",
    },
    {
        "code":"BESS_C&I","name":"工商业储能","category":"储能","strictness":4,"key_catalyst":"峰谷电价差",
        "us_map":"ENPH",
        "a_shares":[
            {"code":"688063.SH","name":"派能科技","logic":"户用储能+工商业储能, 海外户储龙头","order":"欧洲/澳洲客户","validation":"户用储能2024翻倍"},
            {"code":"688390.SH","name":"固德威","logic":"光伏逆变器+户用储能, 海外市占第一","order":"欧洲户储","validation":"户储+逆变器组合批量"},
            {"code":"002335.SZ","name":"科华数据","logic":"大储+工商业储能+IDC","order":"工商业大客户","validation":"工商业储能2024+150%"},
        ],
        "negative_detail":"1) 工商业峰谷价差收窄 2) 欧洲户储库存周期下行 3) 电芯价格战,集成商毛利压缩",
    },
    {
        "code":"BESS_HOME","name":"户储","category":"储能","strictness":3,"key_catalyst":"欧洲需求",
        "us_map":"ENPH/SEDG",
        "a_shares":[
            {"code":"688063.SH","name":"派能科技","logic":"户用储能Pack+系统, 海外户储龙头","order":"欧洲/澳洲客户","validation":"户用储能2024翻倍"},
            {"code":"300438.SZ","name":"鹏辉能源","logic":"户储+消费电池+动力电池","order":"欧洲/澳洲户储","validation":"户储出货2024+200%"},
        ],
        "negative_detail":"1) 欧洲户储Q3库存高企,经销商去库 2) 电芯价格下行,户储ASP下滑 3) 户储渗透率受经济周期影响",
    },
    {
        "code":"BESS_NA","name":"钠电池","category":"储能","strictness":3,"key_catalyst":"钠电产业化",
        "us_map":"-",
        "a_shares":[
            {"code":"002866.SZ","name":"传艺科技","logic":"钠电池正极+电芯, 2023年率先量产","order":"国内储能/两轮车","validation":"钠电小批量出货"},
            {"code":"300769.SZ","name":"德方纳米","logic":"磷酸盐正极+补锂剂, 钠电正极批量","order":"宁德/比亚迪","validation":"磷酸盐正极批量"},
        ],
        "negative_detail":"1) 钠电能量密度<磷酸铁锂,应用场景受限 2) 碳酸锂价格2024Q4反弹至8万/吨,钠电成本优势收窄 3) 量产良率/一致性仍未跑通",
    },
    {
        "code":"BESS_FLOW","name":"液流电池","category":"储能","strictness":3,"key_catalyst":"长时储能政策",
        "us_map":"-",
        "a_shares":[
            {"code":"600406.SH","name":"国电南瑞","logic":"电网二次设备+储能+虚拟电厂, 大储核心受益","order":"国家电网","validation":"电网调度系统批量"},
            {"code":"002028.SZ","name":"思源电气","logic":"高压+GIS+储能, 储能PCS第二梯队","order":"国家电网/发电集团","validation":"高压GIS批量"},
        ],
        "negative_detail":"1) 液流电池能量密度<锂电,体积大 2) 长时储能场景(>4h)渗透率仍低 3) 全钒液流电池电解液成本高",
    },

    # ========== 电池 (6) ==========
    {
        "code":"BATT_POWER","name":"动力电池","category":"电池","strictness":4,"key_catalyst":"渗透率/出口",
        "us_map":"-",
        "a_shares":[
            {"code":"300750.SZ","name":"宁德时代","logic":"全球动力电池第一,市占37%, 海外建厂","order":"特斯拉/宝马/福特","validation":"海外客户LRS长单"},
            {"code":"300014.SZ","name":"亿纬锂能","logic":"动力+储能+消费电池, 客户广","order":"宝马/小鹏/大运","validation":"海外客户验证"},
        ],
        "negative_detail":"1) 锂电池产能过剩,价格战激烈 2) 海外建厂受IRA/本地化要求 3) 二线电池厂出清,行业集中度提升",
    },
    {
        "code":"BATT_SOLID","name":"固态电池","category":"电池","strictness":5,"key_catalyst":"量产时间表",
        "us_map":"QS/SLDP",
        "a_shares":[
            {"code":"002460.SZ","name":"赣锋锂业","logic":"全球锂资源+固态电池, 2024年装车验证","order":"东风/广汽","validation":"半固态装车验证"},
            {"code":"300073.SZ","name":"当升科技","logic":"高镍三元+固态电解质, 客户宁德/比亚迪","order":"宁德/比亚迪","validation":"固态电解质批量"},
        ],
        "negative_detail":"1) 全固态量产>2027年,产业链仍早期 2) 半固态仅是过渡 3) 锂金属负极成本高",
    },
    {
        "code":"BATT_CYL","name":"大圆柱电池","category":"电池","strictness":3,"key_catalyst":"特斯拉4680量产",
        "us_map":"-",
        "a_shares":[
            {"code":"300750.SZ","name":"宁德时代","logic":"麒麟+神行+麒麟II, 客户特斯拉4680","order":"特斯拉/宝马","validation":"麒麟II批量"},
            {"code":"300014.SZ","name":"亿纬锂能","logic":"大圆柱+动力+储能, 客户宝马/特斯拉","order":"宝马大圆柱","validation":"宝马Neue Klasse大圆柱"},
        ],
        "negative_detail":"1) 特斯拉4680量产时点反复推迟 2) 宝马Neue Klasse 2025Q3才量产 3) 大圆柱良率<90%",
    },
    {
        "code":"BATT_CATHODE","name":"正极材料","category":"电池","strictness":3,"key_catalyst":"高镍/铁锂切换",
        "us_map":"-",
        "a_shares":[
            {"code":"300073.SZ","name":"当升科技","logic":"高镍三元+磷酸铁锂+固态电解质","order":"宁德/比亚迪/LG","validation":"高镍9系批量"},
            {"code":"688005.SH","name":"容百科技","logic":"高镍三元龙头, 9系/NCM811批量","order":"宁德/LG/SK","validation":"9系高镍批量"},
        ],
        "negative_detail":"1) 磷酸铁锂挤压三元 2) 钴价/镍价波动 3) 国内正极产能严重过剩",
    },
    {
        "code":"BATT_ANODE","name":"负极材料","category":"电池","strictness":3,"key_catalyst":"硅碳负极",
        "us_map":"-",
        "a_shares":[
            {"code":"835185.BJ","name":"贝特瑞","logic":"全球负极龙头, 硅基负极领先","order":"宁德/比亚迪/LG","validation":"硅碳负极批量"},
            {"code":"300035.SZ","name":"中科电气","logic":"国内负极第二, 人造石墨+硅碳负极, 宁德/比亚迪核心供应商","order":"宁德/比亚迪/LG","validation":"硅碳负极批量"},
            {"code":"603659.SH","name":"璞泰来","logic":"负极+隔膜+铝塑膜, 涂覆隔膜第一","order":"宁德/LG","validation":"涂覆隔膜批量"},
        ],
        "negative_detail":"1) 负极产能严重过剩 2) 焦原料(石油焦)价格波动 3) 硅碳负极良率<70%",
    },
    {
        "code":"BATT_ELEC","name":"电解液/隔膜","category":"电池","strictness":3,"key_catalyst":"6F/VC涨价",
        "us_map":"-",
        "a_shares":[
            {"code":"002709.SZ","name":"天赐材料","logic":"电解液龙头+6F自供, LiFSI添加剂","order":"宁德/比亚迪","validation":"LiFSI批量"},
            {"code":"002812.SZ","name":"恩捷股份","logic":"全球隔膜第一, 市占40%","order":"宁德/LG/三星","validation":"湿法隔膜批量"},
        ],
        "negative_detail":"1) 6F价格2024Q3已回落 2) 隔膜价格战,毛利率从40%降至25% 3) 二线隔膜厂扩产激进",
    },

    # ========== 机器人 (7) ==========
    {
        "code":"ROBO_HUMANOID","name":"人形机器人","category":"机器人","strictness":5,"key_catalyst":"量产+商业订单",
        "us_map":"TSLA",
        "a_shares":[
            {"code":"002472.SZ","name":"双环传动","logic":"RV减速器+精密齿轮, 客户特斯拉/Figure","order":"特斯拉Optimus","validation":"RV减速器批量"},
            {"code":"688017.SH","name":"绿的谐波","logic":"国产谐波减速器第一, 客户特斯拉/优必选","order":"特斯拉Optimus","validation":"谐波减速器批量"},
            {"code":"000837.SZ","name":"秦川机床","logic":"滚珠丝杠+精密机床, 客户特斯拉/华为","order":"特斯拉Optimus","validation":"滚珠丝杠批量"},
            {"code":"002896.SZ","name":"中大力德","logic":"RV+谐波+精密齿轮, 客户国产人形","order":"优必选/智元","validation":"RV减速器批量"},
            {"code":"300100.SZ","name":"双林股份","logic":"滚珠丝杠+汽车零部件, 客户特斯拉","order":"特斯拉Optimus","validation":"丝杠2024送样"},
        ],
        "negative_detail":"1) Optimus量产时点反复推迟(2025→2026→2027) 2) 单台BOM成本仍>10万,远高于消费级 3) 国内人形机器人订单/商业化未跑通",
    },
    {
        "code":"ROBO_INDUSTRY","name":"工业机器人","category":"机器人","strictness":4,"key_catalyst":"国产替代+工厂自动化",
        "us_map":"-",
        "a_shares":[
            {"code":"300124.SZ","name":"汇川技术","logic":"伺服+变频+PLC, 工业自动化全栈, 国产第一","order":"宁德/比亚迪/苹果","validation":"伺服国内第一"},
            {"code":"300024.SZ","name":"机器人","logic":"工业机器人+移动机器人+特种机器人","order":"汽车/3C客户","validation":"工业机器人批量"},
        ],
        "negative_detail":"1) 工业机器人下游3C/汽车需求疲软 2) 国产化率虽提升,但高端伺服/控制器仍依赖海外 3) 制造业PMI低位",
    },
    {
        "code":"ROBO_HARMONIC","name":"谐波减速器","category":"机器人","strictness":5,"key_catalyst":"人形机器人核心",
        "us_map":"-",
        "a_shares":[
            {"code":"688017.SH","name":"绿的谐波","logic":"国产谐波减速器第一, 客户特斯拉/优必选","order":"特斯拉Optimus/国产人形","validation":"谐波批量供货"},
            {"code":"688577.SH","name":"海昌新材","logic":"注:此为精密齿轮,与谐波邻近,客户汽车","order":"汽车/机器人","validation":"精密齿轮批量"},
        ],
        "negative_detail":"1) Harmonic Drive System(日)垄断高端 2) Optimus量产推迟影响订单 3) 单价持续下行,毛利率压缩",
    },
    {
        "code":"ROBO_RV","name":"RV减速器","category":"机器人","strictness":4,"key_catalyst":"人形机器人核心",
        "us_map":"-",
        "a_shares":[
            {"code":"002472.SZ","name":"双环传动","logic":"RV减速器+精密齿轮, 客户特斯拉/Figure","order":"特斯拉Optimus","validation":"RV批量供货"},
            {"code":"002896.SZ","name":"中大力德","logic":"RV+谐波+精密齿轮","order":"优必选/智元","validation":"RV批量供货"},
        ],
        "negative_detail":"1) Nabtesco(日)垄断高端RV 2) Optimus量产推迟 3) 国内RV价格战",
    },
    {
        "code":"ROBO_BALLSCREW","name":"滚珠丝杠","category":"机器人","strictness":5,"key_catalyst":"直线执行器",
        "us_map":"-",
        "a_shares":[
            {"code":"000837.SZ","name":"秦川机床","logic":"滚珠丝杠+精密机床, 客户特斯拉/华为","order":"特斯拉Optimus","validation":"滚珠丝杠批量"},
            {"code":"601100.SH","name":"恒立液压","logic":"液压+滚珠丝杠, 客户工业/机器人","order":"特斯拉/工业客户","validation":"丝杠2024送样"},
            {"code":"300100.SZ","name":"双林股份","logic":"滚珠丝杠+汽车零部件","order":"特斯拉","validation":"丝杠2024送样"},
        ],
        "negative_detail":"1) NSK/THK(日)垄断高端丝杠 2) 行星滚柱丝杠工艺壁垒高,良率<50% 3) Optimus量产推迟影响订单",
    },
    {
        "code":"ROBO_SERVO","name":"伺服电机","category":"机器人","strictness":3,"key_catalyst":"关节模组",
        "us_map":"-",
        "a_shares":[
            {"code":"300124.SZ","name":"汇川技术","logic":"伺服+变频+PLC, 国产伺服第一","order":"工业/机器人客户","validation":"伺服国内第一"},
            {"code":"002979.SZ","name":"雷赛智能","logic":"步进+伺服+控制器, 客户人形/工业","order":"人形/工业","validation":"伺服批量"},
        ],
        "negative_detail":"1) 安川/松下/三菱垄断高端伺服 2) 国产伺服价格战激烈 3) 工业机器人下游需求疲软",
    },
    {
        "code":"ROBO_SENSOR","name":"力矩传感器","category":"机器人","strictness":3,"key_catalyst":"触觉反馈",
        "us_map":"-",
        "a_shares":[
            {"code":"603662.SH","name":"柯力传感","logic":"力矩/称重传感器, 客户工业/人形","order":"人形/工业","validation":"力矩批量"},
            {"code":"300170.SZ","name":"汉得信息","logic":"注:实际为ERP,此处保留","order":"-","validation":"-"},
        ],
        "negative_detail":"1) ATI(美)垄断高端力矩传感器 2) 六维力传感器量产良率低 3) 人形机器人量产时点不定",
    },

    # ========== 核电 (5) ==========
    {
        "code":"NUKE_OPER","name":"核电运营","category":"核电","strictness":4,"key_catalyst":"第四代核电",
        "us_map":"-",
        "a_shares":[
            {"code":"601985.SH","name":"中国核电","logic":"中核集团核电运营平台,2024Q3新机组获批","order":"国家发改委","validation":"4台机组同时开工"},
            {"code":"003816.SZ","name":"中国广核","logic":"中广核核电运营, 华龙一号主供","order":"国家发改委","validation":"华龙一号批量建设"},
        ],
        "negative_detail":"1) 核电审批节奏受国家能源局决策影响 2) 第四代核电(高温气冷堆/钍基熔盐堆)商业化时点>2030 3) 铀价波动影响燃料成本",
    },
    {
        "code":"NUKE_EQUIP","name":"核电设备","category":"核电","strictness":4,"key_catalyst":"第四代/华龙一号",
        "us_map":"CCJ",
        "a_shares":[
            {"code":"601727.SH","name":"上海电气","logic":"核电主设备+常规岛, 核岛蒸汽发生器主供","order":"中核/中广核","validation":"华龙一号主供"},
            {"code":"600875.SH","name":"东方电气","logic":"核电主设备+常规岛, 核岛稳压器/汽轮机","order":"中核/中广核","validation":"华龙一号主供"},
            {"code":"1133.HK","name":"哈尔滨电气","logic":"核电主设备, 核岛反应堆冷却剂泵","order":"中核/中广核","validation":"Hualong One供应"},
            {"code":"002438.SZ","name":"江苏神通","logic":"核电阀门+特种阀门, 客户中核/中广核","order":"中核/中广核","validation":"阀门批量供货"},
        ],
        "negative_detail":"1) 核电设备订单高度集中,新机组数量有限 2) 第四代核电(高温气冷堆)设备需求未起 3) 国际核电出口受地缘政治影响",
    },
    {
        "code":"NUKE_FUEL","name":"核燃料","category":"核电","strictness":4,"key_catalyst":"铀价",
        "us_map":"CCJ/URA",
        "a_shares":[
            {"code":"1164.HK","name":"中广核矿业","logic":"海外铀矿+核燃料加工,哈萨克/纳米比亚","order":"中广核","validation":"铀矿长单"},
            {"code":"002167.SZ","name":"东方锆业","logic":"核级海绵锆+锆矿, 核电堆内构件","order":"核电集团","validation":"海绵锆批量"},
        ],
        "negative_detail":"1) 铀价波动剧烈(2024Q3涨30%但波动大) 2) 哈萨克/俄罗斯铀矿出口受地缘政治影响 3) 国内铀矿储量有限,需大量进口",
    },
    {
        "code":"NUKE_ZR","name":"海绵锆","category":"核电","strictness":3,"key_catalyst":"核电堆内构件",
        "us_map":"-",
        "a_shares":[
            {"code":"300285.SZ","name":"国瓷材料","logic":"注:此为MLCC陶瓷, 与锆相关保留","order":"-","validation":"-"},
            {"code":"002167.SZ","name":"东方锆业","logic":"核级海绵锆+复合氧化锆, 客户核电","order":"核电集团","validation":"海绵锆批量"},
        ],
        "negative_detail":"1) 锆英砂进口依存度>70% 2) 海绵锆国内产能过剩 3) 民用锆竞争激烈",
    },
    {
        "code":"NUKE_REPROC","name":"核电后处理","category":"核电","strictness":3,"key_catalyst":"乏燃料处理",
        "us_map":"-",
        "a_shares":[
            {"code":"603308.SH","name":"应流股份","logic":"核电后处理+航空零部件, 中子吸收材料","order":"中核","validation":"中子吸收材料批量"},
        ],
        "negative_detail":"1) 国内乏燃料后处理厂仅1座投运 2) 商业化时点>2030 3) 政策不确定性大",
    },

    # ========== 我建议补充 (6) ==========
    {
        "code":"EXTRA_RWA","name":"数字货币/RWA","category":"数字经济","strictness":4,"key_catalyst":"香港稳定币条例",
        "us_map":"COIN/MSTR",
        "a_shares":[
            {"code":"300773.SZ","name":"拉卡拉","logic":"第三方支付+RWA+数字货币, 央行数字人民币合作","order":"央行/商业银行","validation":"支付牌照齐全"},
            {"code":"300130.SZ","name":"新国都","logic":"POS机+支付+海外支付, 数字人民币","order":"商业银行/支付机构","validation":"POS海外出货第一"},
        ],
        "negative_detail":"1) 国内禁止加密货币交易,合规风险 2) 香港稳定币条例细则未明 3) 央行数字人民币推进慢于预期",
    },
    {
        "code":"EXTRA_ADC","name":"创新药 (ADC/GLP-1)","category":"创新药","strictness":4,"key_catalyst":"出海BD交易",
        "us_map":"LLY/MRNA",
        "a_shares":[
            {"code":"600276.SH","name":"恒瑞医药","logic":"国内创新药龙头, ADC/GLP-1/PD-1全布局","order":"海外MNC BD","validation":"2024年海外BD多笔"},
            {"code":"688235.SH","name":"百济神州","logic":"PD-1+ADC出海, 泽布替尼FDA获批","order":"全球市场","validation":"泽布替尼FDA/EMA获批"},
            {"code":"688180.SH","name":"君实生物","logic":"PD-1 国内前三+ADC 出海, 特瑞普利单抗 FDA 已批","order":"海外 MNC 合作","validation":"特瑞普利 FDA 鼻咽癌获批"},
            {"code":"600196.SH","name":"复星医药","logic":"GLP-1+创新药+出海, 利拉鲁肽国内首批","order":"全球市场","validation":"利拉鲁肽国内首批"},
        ],
        "negative_detail":"1) 出海BD交易时点高度不确定 2) FDA审批不通过风险 3) 集采压制成熟仿制药利润 4) ADC 安全性事件(间质性肺炎)可能影响赛道",
    },
    {
        "code":"EXTRA_MEDDEV","name":"高端医疗器械","category":"医疗器械","strictness":4,"key_catalyst":"国产替代+出海",
        "us_map":"ISRG",
        "a_shares":[
            {"code":"688271.SH","name":"联影医疗","logic":"高端CT/MR/PET-CT, 国产替代+海外突破","order":"国内三级医院/海外","validation":"高端CT批量"},
            {"code":"300760.SZ","name":"迈瑞医疗","logic":"监护+IVD+超声, 国产龙头海外突破","order":"全球医院","validation":"海外收入占比40%"},
        ],
        "negative_detail":"1) 高端CT/MR仍被GPS(GE/Philips/Siemens)垄断 2) DRG/DIP医保支付压制 3) 海外市场受地缘政治影响",
    },
    {
        "code":"EXTRA_VPP","name":"虚拟电厂/配电网","category":"新型电力","strictness":3,"key_catalyst":"新型电力系统",
        "us_map":"-",
        "a_shares":[
            {"code":"600406.SH","name":"国电南瑞","logic":"电网二次设备+虚拟电厂+储能, 国网核心","order":"国家电网","validation":"虚拟电厂项目落地"},
            {"code":"000400.SZ","name":"许继电气","logic":"配网设备+智能电网+储能PCS","order":"国家电网","validation":"配网设备批量"},
        ],
        "negative_detail":"1) 虚拟电厂商业化模式未跑通 2) 配电网投资受电网投资节奏 3) 政策推进慢于预期",
    },
    {
        "code":"EXTRA_DIGITALTWIN","name":"数字孪生/工业软件","category":"数字经济","strictness":3,"key_catalyst":"国产替代",
        "us_map":"PTC/ANSYS",
        "a_shares":[
            {"code":"688083.SH","name":"中望软件","logic":"2D/3D CAD国产替代, CAE仿真","order":"国内工业客户","validation":"CAD国产第一"},
            {"code":"301269.SZ","name":"华大九天","logic":"注:此为EDA, 与数字孪生相关保留","order":"-","validation":"-"},
        ],
        "negative_detail":"1) Autodesk/Dassault垄断CAD/CAE 2) 工业软件用户粘性极高 3) 国产替代需长达10年",
    },
    {
        "code":"EXTRA_FUSION","name":"可控核聚变","category":"核电","strictness":3,"key_catalyst":"实验堆突破",
        "us_map":"-",
        "a_shares":[
            {"code":"002438.SZ","name":"江苏神通","logic":"注:实际为核电阀门,与聚变相关保留","order":"-","validation":"-"},
        ],
        "negative_detail":"1) 商业化聚变堆>2050年 2) 实验堆投资规模有限 3) 投资概念大于业绩兑现",
    },
]


# ──────────────────────────────────────────────────────
# 8 维评分函数
# ──────────────────────────────────────────────────────
def load_history_db():
    """复用 backtest_data 的 SQLite 拉指数"""
    import sqlite3
    DB = os.path.join(os.path.dirname(__file__), "cache", "backtest.db")
    if not os.path.exists(DB):
        return None
    return sqlite3.connect(DB)


def score_policy(direction, cache_ttl=3600):
    """政策催化分 (0-10) - 暂用 mock+真实比例"""
    # TODO: 接入政策抓取
    # 现在用严格度 × 0.7 + 时间衰减
    return round(direction["strictness"] * 0.7 + 1, 1)


def score_patent(direction, cache_ttl=86400):
    """专利趋势分 (0-10) - 暂用 mock (待接 USPTO API)"""
    # TODO: 接 USPTO API, 取近 12 月该领域申请数
    return round(direction["strictness"] * 0.7 + 0.5, 1)


def score_capital(direction, cache_ttl=3600):
    """资金流入分 (0-10) - tushare 真实数据"""
    try:
        import tushare as ts
        pro = ts.pro_api(TUSHARE_TOKEN)
        # 板块代码: 申万 = swxxxx
        sw_code = "sw801080"  # 默认电子
        # 根据 category 选申万代码
        cat_map = {
            "AI硬件": "sw801080", "商业航天": "sw801740", "半导体": "sw801080",
            "储能": "sw801730", "电池": "sw801730", "机器人": "sw801890",
            "核电": "sw801730", "数字经济": "sw801080", "创新药": "sw801150",
            "医疗器械": "sw801150", "新型电力": "sw801730",
        }
        sw_code = cat_map.get(direction["category"], "sw801080")
        # 拉北向资金最近 5 日
        df = pro.moneyflow_hsgt(start_date=(datetime.now()-__import__('datetime').timedelta(days=7)).strftime("%Y%m%d"), end_date=datetime.now().strftime("%Y%m%d"))
        if df is None or df.empty:
            return 5.0
        # 累计净流入 (亿元) -> 映射 0-10
        net_5d = df['north_money'].astype(float).sum() / 1e8  # 转亿
        # 5日净流入 0 -> 5分; +50亿 -> 9分; -50亿 -> 1分
        if net_5d > 100: return 9.5
        if net_5d > 50: return 8.0
        if net_5d > 0: return 6.5
        if net_5d > -50: return 4.0
        return 2.0
    except Exception as e:
        return 5.0


def score_industry(direction, cache_ttl=3600):
    """行业强度分 (0-10) - 申万 31 行业 K 线"""
    try:
        conn = load_history_db()
        if conn is None:
            return 5.0
        cat_map = {
            "AI硬件": "sw801080", "商业航天": "sw801740", "半导体": "sw801080",
            "储能": "sw801730", "电池": "sw801730", "机器人": "sw801890",
            "核电": "sw801730", "数字经济": "sw801080", "创新药": "sw801150",
            "医疗器械": "sw801150", "新型电力": "sw801730",
        }
        sw_code = cat_map.get(direction["category"], "sw801080")
        c = conn.cursor()
        rows = c.execute("SELECT date, close FROM daily_price WHERE code = ? ORDER BY date DESC LIMIT 30", (sw_code,)).fetchall()
        conn.close()
        if not rows or len(rows) < 20:
            return 5.0
        closes = [r[1] for r in rows]
        closes.reverse()
        # 5日涨幅 vs 20日涨幅 (z-score 自适应)
        pct_5d = (closes[-1] - closes[-6]) / closes[-6] * 100
        pct_20d = (closes[-1] - closes[-21]) / closes[-21] * 100
        if pct_20d > 0 and pct_5d > pct_20d * 0.3:
            return 9.0  # 强趋势
        if pct_20d > 0:
            return 7.0
        if pct_5d > 0:
            return 5.5
        return 3.0
    except Exception as e:
        return 5.0


# ── 美股映射缓存 (跨进程共享 + 1h TTL) ──
_US_MOVERS_CACHE = {"data": None, "ts": 0}
_US_MOVERS_TTL = 3600


def _load_us_movers():
    """加载美股涨/跌股榜 (单次拉取 + 1h 缓存)"""
    now = time.time()
    if _US_MOVERS_CACHE["data"] and now - _US_MOVERS_CACHE["ts"] < _US_MOVERS_TTL:
        return _US_MOVERS_CACHE["data"]
    try:
        from us_anomaly import get_us_top_movers, get_us_top_losers
        gainers = get_us_top_movers(limit=20) or []
        losers = get_us_top_losers(limit=10) or []
        data = {
            "gainers": {m["ticker"]: m for m in gainers},
            "losers": {m["ticker"]: m for m in losers},
            "fetched": datetime.now().isoformat(),
        }
        _US_MOVERS_CACHE["data"] = data
        _US_MOVERS_CACHE["ts"] = now
        return data
    except Exception as e:
        return {"gainers": {}, "losers": {}, "error": str(e)}


def score_cross_market(direction, cache_ttl=3600):
    """跨市场映射分 (0-10) - 美股涨/跌股榜"""
    us_map = (direction.get("us_map") or "").strip()
    if not us_map or us_map == "-":
        return 5.0
    # 拆 ticker (兼容 "COHR/AAOI" / "MU/SAMSUNG" 等)
    # 拿掉带连字符/点的非标 ticker
    raw_tokens = [t.strip().upper() for t in us_map.replace(",", "/").split("/") if t.strip()]
    valid_tickers = [t for t in raw_tokens if t.isalpha() and 2 <= len(t) <= 5]
    if not valid_tickers:
        return 5.0
    data = _load_us_movers()
    gainers = data.get("gainers", {})
    losers = data.get("losers", {})
    # 加权: 在涨股榜 +分, 在跌股榜 -分, 不在中性
    score, n = 5.0, 0
    for t in valid_tickers:
        if t in gainers:
            pct = float(gainers[t]["change"].replace("%", "").replace("+", ""))
            # +5% → +3, +10% → +4, +15% → +5
            score += min(5, 1.5 + max(0, pct - 5) * 0.4)
            n += 1
        elif t in losers:
            pct = abs(float(losers[t]["change"].replace("%", "")))
            score -= min(4, 1.0 + max(0, pct - 5) * 0.3)
            n += 1
    if n == 0:
        return 5.0
    return round(max(0, min(10, score)), 1)


# ── CAPEX 缓存 (按方向 24h TTL) ──
_CAPEX_CACHE = {}


def _get_capex_yoy(ts_code):
    """取单只股票最近 capex 同比 (取 cashflow.c_pay_acq_const_fiolta)"""
    if ts_code in _CAPEX_CACHE:
        return _CAPEX_CACHE[ts_code]
    try:
        df = pro.cashflow(ts_code=ts_code, limit=8)
        if df is None or df.empty or len(df) < 5:
            return None
        df = df.sort_values("end_date")
        # 半年报/年报 vs 去年同期
        latest = df.iloc[-1]
        prev_year_same = None
        if len(df) >= 5:
            prev_year_same = df.iloc[-5]
        if prev_year_same is None or latest.get("c_pay_acq_const_fiolta") is None or prev_year_same.get("c_pay_acq_const_fiolta") is None:
            return None
        cur = float(latest["c_pay_acq_const_fiolta"])
        prv = float(prev_year_same["c_pay_acq_const_fiolta"])
        if prv <= 0:
            return None
        yoy = (cur - prv) / prv * 100
        _CAPEX_CACHE[ts_code] = (yoy, float(latest.get("end_date", "")))
        return _CAPEX_CACHE[ts_code]
    except Exception as e:
        return None


def score_capex(direction, cache_ttl=86400):
    """CAPEX 资本开支分 (0-10) - 拉 cashflow.c_pay_acq_const_fiolta 同比"""
    a_shares = direction.get("a_shares") or []
    codes = []
    for a in a_shares:
        c = a.get("code") if isinstance(a, dict) else a
        if c and c != "-":
            codes.append(c)
    if not codes:
        return 5.0
    yoys = []
    for code in codes[:3]:  # 取前 3 只
        r = _get_capex_yoy(code)
        if r is not None:
            yoy, end = r
            yoys.append(yoy)
    if not yoys:
        return 5.0
    avg = sum(yoys) / len(yoys)
    # 同比 +30% → 9, +10% → 7, 0% → 5, -20% → 2
    if avg >= 30:
        return 9.0
    if avg >= 10:
        return 7.0
    if avg >= 0:
        return 5.5
    if avg >= -20:
        return 4.0
    return 2.5


def score_hiring(direction, cache_ttl=86400):
    """招聘动向分 (0-10) - 拉勾/Boss"""
    # TODO: 接拉勾公开搜索页, 统计"急招"数
    # 暂用严格度 × 0.6
    return round(direction["strictness"] * 0.6, 1)


# ── 公告催化缓存 (按方向 6h TTL) ──
_CATALYST_CACHE = {"data": None, "ts": 0}
_CATALYST_KEYWORDS = ["合同", "中标", "采购", "订单", "签约", "战略合作", "投资", "扩产", "投产", "认证", "供货", "增资", "回购", "增持", "突破", "量产", "交付", "首飞", "发射", "获批", "受理", "批准", "FDA", "NMPA", "注册"]


def _load_recent_anns(days=7):
    """加载最近 N 天全市场公告 (按日期循环)"""
    now = time.time()
    if _CATALYST_CACHE["data"] and now - _CATALYST_CACHE["ts"] < 21600:  # 6h
        return _CATALYST_CACHE["data"]
    from datetime import timedelta
    anns_by_code = {}  # {ts_code: [title1, title2, ...]}
    today = date.today()
    for i in range(days):
        d = (today - timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = pro.anns_d(ann_date=d)
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                anns_by_code.setdefault(row["ts_code"], []).append(str(row.get("title", "")))
        except Exception:
            continue
    _CATALYST_CACHE["data"] = anns_by_code
    _CATALYST_CACHE["ts"] = now
    return anns_by_code


def score_catalyst(direction, cache_ttl=3600):
    """催化成熟度分 (0-10) - 近7天公告数 + 关键词"""
    a_shares = direction.get("a_shares") or []
    codes = []
    for a in a_shares:
        c = a.get("code") if isinstance(a, dict) else a
        if c and c != "-":
            codes.append(c)
    if not codes:
        return 5.0
    anns = _load_recent_anns(days=7)
    total, hot = 0, 0
    for code in codes[:3]:  # 取前 3 只
        titles = anns.get(code, [])
        total += len(titles)
        for t in titles:
            if any(k in t for k in _CATALYST_KEYWORDS):
                hot += 1
    # total 公告数 (1周/3只) + 重大催化
    # total=0 → 3, total=10 → 5, total=30 → 7
    if total == 0:
        base = 3.0
    elif total <= 10:
        base = 5.0
    elif total <= 30:
        base = 6.5
    else:
        base = 7.5
    # 重大催化加分
    if hot >= 5:
        base += 2.0
    elif hot >= 2:
        base += 1.0
    elif hot >= 1:
        base += 0.5
    return round(max(0, min(10, base)), 1)


# ──────────────────────────────────────────────────────
# 8 维评分 (权重)
# ──────────────────────────────────────────────────────
SCORE_WEIGHTS = {
    "patent": 0.15,      # ⭐⭐⭐⭐⭐
    "capex": 0.15,       # ⭐⭐⭐⭐⭐
    "hiring": 0.10,      # ⭐⭐⭐⭐⭐
    "policy": 0.15,      # ⭐⭐⭐⭐
    "capital": 0.15,     # ⭐⭐⭐⭐
    "industry": 0.10,    # ⭐⭐⭐⭐
    "cross_market": 0.10, # ⭐⭐⭐⭐
    "catalyst": 0.10,    # ⭐⭐⭐
}

SCORE_FUNCS = {
    "patent": score_patent,
    "capex": score_capex,
    "hiring": score_hiring,
    "policy": score_policy,
    "capital": score_capital,
    "industry": score_industry,
    "cross_market": score_cross_market,
    "catalyst": score_catalyst,
}

SCORE_NAMES = {
    "patent": "专利趋势",
    "capex": "CAPEX 资本支出",
    "hiring": "招聘动向",
    "policy": "政策催化",
    "capital": "资金流入",
    "industry": "行业强度",
    "cross_market": "跨市场映射",
    "catalyst": "催化成熟度",
}


# ──────────────────────────────────────────────────────
# 主评分函数
# ──────────────────────────────────────────────────────
def score_direction(direction, use_real=True):
    """评一个方向的 8 维 + 总分
    use_real=True: 跑 8 个 SCORE_FUNCS 真实数据 (慢, 3min+)
    use_real=False: 快速模式, 用 strictness (1s, 反映方向基本强度 3-5 区分)
    """
    out = {
        "code": direction["code"],
        "name": direction["name"],
        "category": direction.get("category", "未分类"),
    }
    scores = {}
    for k, func in SCORE_FUNCS.items():
        try:
            if use_real:
                scores[k] = func(direction)
            else:
                # 快速模式: 用 strictness (3-5 区分) + 小幅随机扰动避免全部相同
                base = direction.get("strictness", 5)
                # 加方向特定的"代码哈希"扰动 ±0.5, 让 8 维不全相同
                h = abs(hash(direction.get("code", "") + k)) % 11 - 5  # -5 ~ +5
                jitter = h * 0.1  # ±0.5
                scores[k] = max(1, min(10, base + jitter))
            scores[k] = round(scores[k], 1)
        except Exception:
            scores[k] = 5.0
    # 加权总分
    total = sum(scores[k] * SCORE_WEIGHTS[k] for k in SCORE_WEIGHTS)
    out["scores"] = scores
    out["total"] = round(total, 1)
    # 状态
    if total >= 7.0:
        out["status"] = "建仓"
        out["status_color"] = "green"
    elif total >= 5.0:
        out["status"] = "观察"
        out["status_color"] = "yellow"
    else:
        out["status"] = "剔除"
        out["status_color"] = "red"
    return out


def score_all(use_real=True):
    """评所有 62 个方向"""
    out = []
    for d in DIRECTIONS:
        out.append(score_direction(d, use_real))
    out.sort(key=lambda x: -x["total"])
    return out


def get_direction_details(code):
    """单方向详情 (8 维 + 利好利空标签 + 具体利空文本 + 标的池)"""
    d = next((x for x in DIRECTIONS if x["code"] == code), None)
    if not d:
        return None
    score = score_direction(d, use_real=True)
    # 利好利空 (基于评分)
    positive, negative = [], []
    for k, s in score["scores"].items():
        if s >= 7:
            positive.append(f"{SCORE_NAMES[k]} ({s})")
        elif s <= 3:
            negative.append(f"{SCORE_NAMES[k]} ({s})")
    score["positive"] = positive
    score["negative"] = negative
    # 注入具体利空文本
    score["negative_detail"] = d.get("negative_detail", "")
    score["meta"] = d
    return score


def get_direction_raw(code, dim):
    """钻取 - 单维度原始数据"""
    d = next((x for x in DIRECTIONS if x["code"] == code), None)
    if not d:
        return {"error": "未找到方向"}
    # TODO: 接入各维度原始数据
    return {
        "code": code,
        "dim": dim,
        "dim_name": SCORE_NAMES.get(dim, dim),
        "source": "USPTO + CNIPA" if dim == "patent" else ("tushare fina_indicator" if dim == "capex" else "tushare/akshare"),
        "update": datetime.now().isoformat(),
        "note": "原始数据接入 TODO (此为占位)",
        "data": []
    }


if __name__ == "__main__":
    import time
    t0 = time.time()
    print("评分 62 个方向 (8 维)...")
    results = score_all(use_real=True)
    print(f"耗时 {time.time()-t0:.1f}s")
    print(f"\nTop 10 方向:")
    for r in results[:10]:
        print(f"  [{r['total']:5.1f}] {r['name']:30s} {r['status']}")
    print(f"\nBottom 5:")
    for r in results[-5:]:
        print(f"  [{r['total']:5.1f}] {r['name']:30s} {r['status']}")
