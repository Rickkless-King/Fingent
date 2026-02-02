# Fingent
**Fingent** 是一个基于LangGraph 构建的自动化宏观金融分析系统，实现从宏观经济到微观资产的 Top-Down分析流程。

---

## 项目定位

### 一句话总结（新手友好）
**Fingent = 把宏观数据、行情、新闻、预测市场融合起来，自动生成“可读的市场简报 + 风险信号 + 机会线索”。**

你可以把它理解为一个“自动化的宏观研究助理”：  
它会持续拉取数据、做规则判断、生成结构化结果，并提供 Streamlit 面板让你快速浏览。

### 完整功能概览（当前版本）
1. **宏观数据分析**：FRED 宏观指标（利率、通胀、就业）自动汇总  
2. **跨资产行情**：美股、加密、黄金等价格变动与风险方向  
3. **新闻聚合与情绪**：多新闻源轮询 + 统一情感分析  
4. **预测市场监控**（重点）：Polymarket 概率突变榜单 + 24h 曲线  
5. **套利提示**（可选）：期限结构错位检测  
6. **通知与持久化**：Telegram 推送 + SQLite 历史存档  
7. **Streamlit 面板**：可视化查看全部结果

### 核心价值

1. **自动化 Top-Down 分析**：宏观经济 → 跨资产联动 → 市场情绪 → 综合报告
2. **信号标准化**：每个分析节点产出统一格式的 Signal，便于规则引擎处理
3. **插件化架构**：数据源、分析节点、告警规则可独立增删
4. **工程可维护**：不是一次性 demo，而是能持续迭代的系统

### 解决的问题（新手视角）

- **信息碎片化**：新闻、行情、宏观、情绪散落在不同平台  
- **手动盯盘太耗时**：你需要一个“自动汇总 + 预警”的工具  
- **需要可解释的信号**：不仅告诉你“涨跌”，还告诉你“为什么”  
- **希望有实时关注点**：例如 Polymarket 赔率突变、流动性热点

---

## 系统架构

### 数据流向

```
┌─────────────────────────────────────────────────────────────────┐
│                         数据源层                                 │
├───────────┬───────────┬───────────┬───────────┬─────────────────┤
│   FRED    │  Finnhub  │ Marketaux │   FMP     │  Polymarket     │
│ (宏观经济) │ (行情/新闻)│ (金融新闻) │(财经新闻)  │ (预测市场+CLOB)  │
├───────────┼───────────┼───────────┼───────────┼─────────────────┤
│           │  GNews    │AlphaVantage│          │                 │
│           │ (通用新闻) │ (新闻情绪) │          │                 │
└─────┬─────┴─────┬─────┴─────┬─────┴─────┬─────┴────────┬────────┘
      │           │           │           │              │
      ▼           ▼           ▼           ▼              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Provider 适配层                             │
│   FREDProvider / FinnhubProvider / MarketauxProvider / ...      │
│         (统一接口、超时重试、缓存、错误处理)                        │
├─────────────────────────────────────────────────────────────────┤
│                        NewsRouter                                │
│     (多源轮询、配额追踪、自动降级、缓存管理)                         │
├─────────────────────────────────────────────────────────────────┤
│                     SentimentAnalyzer                            │
│   (统一情感分析：数据源情感 → 关键词分析 → LLM 兜底)                 │
└──────────────────────────┬────────────────────┬─────────────────┘
                           │                    │
           ┌───────────────┘                    └───────────────┐
           ▼                                                    ▼
┌─────────────────────────────────────────┐    ┌────────────────────────────┐
│           LangGraph 工作流               │    │     Arbitrage Engine       │
│  ┌──────────┐  ┌──────────┐             │    │  ┌────────────────────┐    │
│  │Bootstrap │→ │  Macro   │→ ...        │    │  │  News Trigger      │    │
│  │  Node    │  │ Auditor  │             │    │  │  (Finnhub Keywords)│    │
│  └──────────┘  └──────────┘             │    │  └─────────┬──────────┘    │
│                      ↓                  │    │            ▼               │
│           ┌─────────────────┐           │    │  ┌────────────────────┐    │
│           │   Synthesize    │           │    │  │ Term Structure     │    │
│           │   & Alert Node  │           │    │  │ Strategy           │    │
│           └────────┬────────┘           │    │  └─────────┬──────────┘    │
└────────────────────┼────────────────────┘    │            ▼               │
                     │                         │  ┌────────────────────┐    │
                     │                         │  │ Risk Manager       │    │
                     │                         │  └─────────┬──────────┘    │
                     │                         └────────────┼───────────────┘
                     │                                      │
              ┌──────┴──────┬───────────────────────────────┘
              ▼             ▼                 ▼
        ┌──────────┐  ┌──────────┐      ┌──────────┐
        │ Telegram │  │ Streamlit│      │  SQLite  │
        │   告警    │  │   面板   │      │  存档    │
        └──────────┘  └──────────┘      └──────────┘
```

### 三层架构

| 层 | 目录 | 职责 | 依赖关系 |
|---|------|-----|---------|
| **Core** | `fingent/core/` | 工程底座：配置、日志、HTTP、缓存 | 无外部依赖 |
| **Domain** | `fingent/domain/` | 业务模型：信号、告警、报告定义 | 只依赖 Core |
| **Infra** | `fingent/providers/` | 数据适配：每个 API 一个封装 | 依赖 Core + Domain |
| **App** | `fingent/nodes/` `graph/` `services/` | 业务编排：LangGraph 节点、工作流 | 依赖上面所有 |

### 设计原则

1. **State 必须 JSON-serializable**：使用 TypedDict + dict/list，不在 state 中使用自定义类
2. **Alert 判定必须 rule-based**：由 config.yaml 中的规则驱动，LLM 只负责报告文字生成
3. **Provider 必须容错**：timeout/retry/cache + 失败降级，单个数据源挂掉不影响整体
4. **Polymarket 可选**：不可用时静默跳过，不阻塞主流程

---

## 目录结构

```
Fingent/
├── pyproject.toml              # 项目配置 + 依赖
├── .env                        # 密钥配置（不提交）
├── .env.example                # 密钥模板
├── README.md                   # 项目说明
├── Dockerfile                  # 容器化
├── docker-compose.yml          # 本地容器测试
│
├── config/
│   ├── config.yaml             # 业务配置（数据源、告警规则）
│   └── logging.yaml            # 日志配置
│
├── fingent/                    # 主包
│   ├── __init__.py
│   │
│   ├── core/                   # 工程底座
│   │   ├── config.py           # 配置加载（支持 .env / AWS Secrets）
│   │   ├── logging.py          # 日志（本地人类可读 / 云端JSON）
│   │   ├── errors.py           # 统一异常定义
│   │   ├── http.py             # HTTP 封装（超时/重试/限流）
│   │   ├── cache.py            # TTL 缓存
│   │   └── timeutil.py         # 时间工具
│   │
│   ├── domain/                 # 业务模型（纯 Python，不碰 HTTP）
│   │   ├── models.py           # MacroIndicator, PriceBar, NewsItem
│   │   ├── signals.py          # Signal 定义 + 聚合逻辑
│   │   ├── alerts.py           # Alert 定义 + RuleEngine
│   │   └── report.py           # Report 结构
│   │
│   ├── providers/              # 数据适配器
│   │   ├── base.py             # BaseProvider / OptionalProvider
│   │   ├── fred.py             # FRED 宏观数据
│   │   ├── finnhub.py          # Finnhub 行情/新闻
│   │   ├── alphavantage.py     # AlphaVantage 新闻情绪
│   │   ├── marketaux.py        # Marketaux 金融新闻（含情感）
│   │   ├── fmp.py              # Financial Modeling Prep 财经新闻
│   │   ├── gnews.py            # GNews 通用新闻（支持搜索）
│   │   ├── news_router.py      # 新闻路由器（多源轮询+降级）
│   │   ├── okx.py              # OKX Crypto 行情
│   │   └── polymarket.py       # Polymarket（可选，含 CLOB 支持）
│   │
│   ├── arb/                    # Polymarket 套利检测
│   │   ├── __init__.py
│   │   ├── engine.py           # 套利引擎（协调全流程）
│   │   ├── strategy.py         # 期限结构策略
│   │   └── risk.py             # 风险控制
│   │
│   ├── nodes/                  # LangGraph 节点
│   │   ├── base.py             # BaseNode 抽象类
│   │   ├── bootstrap.py        # 初始化节点
│   │   ├── macro_auditor.py    # 宏观分析节点
│   │   ├── cross_asset.py      # 跨资产分析节点
│   │   ├── news_impact.py      # 新闻影响节点
│   │   └── synthesize_alert.py # 综合+告警节点
│   │
│   ├── graph/                  # 工作流装配
│   │   ├── state.py            # GraphState 定义
│   │   ├── registry.py         # Provider/Node 注册
│   │   └── builder.py          # 工作流构建器
│   │
│   ├── services/               # 横切能力
│   │   ├── llm.py              # LLM 封装（DeepSeek/Qwen）
│   │   ├── sentiment.py        # 统一情感分析服务
│   │   ├── market_direction.py # 市场方向计算（基于实际市场数据）
│   │   ├── telegram.py         # Telegram 推送
│   │   ├── persistence.py      # 数据持久化
│   │   └── scheduler.py        # 定时任务
│   │
│   ├── ui/
│   │   ├── streamlit_app.py    # Streamlit 主应用
│   │   └── components.py       # 可复用 UI 组件
│   │
│   └── cli/
│       └── main.py             # CLI 入口
│
└── tests/                      # 测试
    ├── test_providers.py
    └── test_nodes.py
```

---

## 核心概念

### GraphState

工作流的"记忆"，贯穿所有节点：

```python
class GraphState(TypedDict):
    # 元信息
    run_id: str                 # 本次运行唯一ID
    asof: str                   # 分析时点 (ISO timestamp)

    # 各节点产出的原始数据
    macro_data: dict            # FRED 宏观指标
    market_data: dict           # 行情数据
    news_data: list             # 新闻列表
    sentiment_data: dict        # Polymarket 数据（可选）

    # 标准化信号（关键！）
    signals: list[dict]         # 所有节点产出的信号

    # 输出
    alerts: list[dict]          # 触发的告警
    report: dict                # 最终报告

    # 运维
    errors: list[dict]          # 错误记录
```

### Signal（信号）

每个分析节点的标准化输出：

```python
signal = {
    "id": "macro_auditor_hawkish_bias_run_xxx",
    "name": "hawkish_bias",
    "direction": "hawkish",
    "score": 0.7,              # -1 到 1
    "confidence": 0.8,         # 0 到 1
    "source_node": "macro_auditor",
    "evidence": {"fed_rate": 5.25, "cpi_yoy": 3.2},
    "timestamp": "2026-01-24T07:00:00Z"
}
```

### Alert（告警）

由规则引擎产生，不依赖 LLM：

```python
alert = {
    "id": "alert_btc_crash_run_xxx",
    "rule_name": "btc_crash",
    "title": "BTC 24h 大跌",
    "message": "BTC 24小时跌幅 -10.5%，超过 -8% 阈值",
    "severity": "high",
    "current_value": -0.105,
    "threshold": -0.08
}
```

---

## MVP 节点清单

| 节点 | 数据源 | 输出信号 |
|------|-------|---------|
| **BootstrapNode** | - | 初始化 run_id、timestamp |
| **MacroAuditorNode** | FRED | hawkish_bias, inflation_rising, labor_strong |
| **CrossAssetNode** | Finnhub, OKX | risk_on, risk_off, yield_curve_inversion |
| **NewsImpactNode** | AlphaVantage | sentiment_bullish, sentiment_bearish |
| **SynthesizeAlertNode** | 所有 signals | 生成 alerts + report |

---

## LLM 使用策略

| 场景 | 使用 LLM | 不使用 LLM |
|------|---------|-----------|
| 新闻摘要 | 生成简报 | 用 API 自带 sentiment_label |
| 告警判定 | ❌ | 规则引擎（config.yaml） |
| 报告生成 | 人话总结 | 结构化数据输出 |

**关键原则**：LLM 只做"锦上添花"，关闭 LLM 系统仍能正常产出结构化报告。

---

## 数据源优先级

```yaml
providers:
  quote:
    us_equity: finnhub      # 美股
    crypto: okx             # 加密货币
    fallback: yfinance      # 备用

  news:
    # NewsRouter 智能路由（按优先级尝试，自动降级）
    priority: [marketaux, fmp, gnews, finnhub]
    daily_limits:
      marketaux: 100        # 免费版 100次/天，含情感分析
      fmp: 200              # 500MB/月，约200次/天
      gnews: 100            # 100次/天，支持关键词搜索
      finnhub: 1000         # 免费版 60次/分钟

  macro:
    primary: fred           # 宏观经济

  sentiment:
    polymarket:
      enabled: false        # 可选，失败不影响主流程
```

### NewsRouter 智能路由

系统使用 `NewsRouter` 自动管理多个新闻源：

1. **优先级轮询**：按 `marketaux → fmp → gnews → finnhub` 顺序尝试
2. **配额追踪**：实时追踪每个 provider 的调用次数，超限自动切换
3. **自动降级**：当前 provider 失败或超限时，自动尝试下一个
4. **缓存管理**：支持运行前清除缓存，确保获取最新数据

```python
from fingent.providers.news_router import get_news_router

router = get_news_router()
news = router.get_market_news(limit=20)
stats = router.get_stats()  # 查看各 provider 调用统计
```

### 统一情感分析 (SentimentAnalyzer)

不同新闻源的情感分析能力不同（Marketaux 自带情感、Finnhub 无情感）。为确保一致性，系统使用 `SentimentAnalyzer` 统一处理：

**分析优先级**：
1. **数据源情感**：若 API 返回 sentiment_score，直接使用
2. **关键词分析**：匹配预定义的看涨/看跌关键词
3. **LLM 分析**（可选）：对于复杂新闻，调用 LLM 分析

**关键词规则**：
- 看涨词：`surge, rally, gain, bullish, 上涨, 利好, 牛市, breakthrough, soar`
- 看跌词：`plunge, crash, loss, bearish, 下跌, 崩盘, 熊市, tumble, slump`

```python
from fingent.services.sentiment import SentimentAnalyzer

analyzer = SentimentAnalyzer()
result = analyzer.analyze_article(article)  # 返回 SentimentResult

# 批量分析
articles = analyzer.analyze_batch(news_items, use_llm=False)

# 聚合统计
aggregate = analyzer.calculate_aggregate_sentiment(articles)
# 返回: avg_sentiment, weighted_sentiment, bullish_count, bearish_count
```

### 市场方向计算 (MarketDirectionCalculator)

**受 CNN Fear & Greed Index 启发**，市场方向（Direction）基于**实际市场数据**计算，而非新闻情绪：

| 指标 | 权重 | 说明 |
|------|-----|------|
| **S&P 500 (SPY)** | 50% | 主要指标，昨日涨跌直接决定方向 |
| **Nasdaq (QQQ)** | 15% | 科技股动向 |
| **VIX** | 20% | 波动率/恐慌指数 |
| **Gold (GLD)** | 15% | 避险情绪，特殊处理极端行情 |

**关键设计**：
- 市场数据占 **80%** 权重，信号聚合仅占 20%
- 新闻情绪仅作为补充参考，**不主导方向判断**
- 黄金暴跌 >5% 被识别为**恐慌性抛售**（bearish），而非传统的 "risk on"

```python
from fingent.services.market_direction import calculate_market_direction

# 使用实际市场数据计算方向
result = calculate_market_direction(signals, market_data)

# 返回示例
{
    "direction": "bearish",       # strong_bullish/bullish/neutral/bearish/strong_bearish
    "score": -0.247,              # -1 到 +1
    "confidence": 0.57,           # 置信度
    "primary_driver": "actual_market_data",  # 主要驱动因素
    "components": {
        "market_data_direct": -0.318,
        "macro_auditor": 0.228,
        "news_impact": -0.330,
    }
}
```

**与传统方法的区别**：

| 传统方法 | Fingent 方法 |
|---------|-------------|
| 新闻情绪 = 市场方向 | 新闻仅占 ~3% 权重 |
| 所有信号权重相同 | SPY/VIX 占 70% 权重 |
| 黄金下跌 = risk on = bullish | 黄金暴跌 = 恐慌 = bearish |

---

## 部署架构

### Phase 1: 本地开发
```
本地开发机
.env + SQLite + APScheduler
python -m fingent.cli.main --once
```

### Phase 2: EC2 部署
```
EC2 (t3.small)
Docker 容器
Secrets Manager + S3 + CloudWatch
```

### Phase 3: Serverless（可选）
```
EventBridge (每天 7:00)
    ↓
Lambda (跑 pipeline)
    ↓
S3 (存报告) + SNS (发告警)
```

---

## 快速开始

### 1. 安装依赖

```bash
# 创建虚拟环境
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# 安装项目
pip install -e ".[dev]"
```

### 2. 配置密钥

复制 `.env.example` 为 `.env`，填入你的 API keys：

```bash
cp .env.example .env
# 编辑 .env 填入密钥
```

**必需的密钥**：
```env
FRED_API_KEY=xxx           # 宏观经济数据
FINNHUB_API_KEY=xxx        # 行情和新闻
```

**新闻源密钥（建议至少配置一个）**：
```env
MARKETAUX_API_KEY=xxx      # 金融新闻（含情感），100次/天
FMP_API_KEY=xxx            # 财经新闻，500MB/月
GNEWS_API_KEY=xxx          # 通用新闻（支持搜索），100次/天
ALPHAVANTAGE_API_KEY=xxx   # 新闻情绪分析
```

**可选密钥**：
```env
DEEPSEEK_API_KEY=xxx       # AI 分析生成
TELEGRAM_BOT_TOKEN=xxx     # Telegram 推送
TELEGRAM_CHAT_ID=xxx       # Telegram Chat ID（推送目标）
POLYMARKET_ENABLED=true    # 启用 Polymarket（监控/套利用）
POLYMARKET_API_KEY=xxx     # 如需鉴权可填写（不需要时可留空）
```

### 3. 运行

```bash
# 单次运行
python -m fingent.cli.main --once

# 定时运行
python -m fingent.cli.main --scheduled

# 预测市场监控（低频全量 + 高频热点）
python -m fingent.cli.main --monitor

# 启动 Streamlit 面板
streamlit run fingent/ui/streamlit_app.py
```

---

## Dashboard 使用说明

### 晨报风格界面

Streamlit Dashboard 采用「晨报/简报」风格，面向普通投资者设计：

```
┌─────────────────────────────────────────────┐
│  📊 Today's Market Brief                    │
│  ─────────────────────────────────────────  │
│  [AI 生成的市场分析摘要，3-5句话]             │
│  [✨ Generate AI Analysis 按钮]             │
├─────────────────────────────────────────────┤
│  Direction: 🟢 BULLISH (+0.35)              │
│  Signals: 5  |  Alerts: 0  |  Errors: 0     │
├─────────────────────────────────────────────┤
│  📰 News (紧凑列表，可展开)                   │
│  🟢 NVIDIA reports record earnings... (2h)  │
│  ⚪ Fed officials signal patience... (5h)   │
│  🔴 China tech stocks fall amid... (8h)    │
├─────────────────────────────────────────────┤
│  💹 Market Overview                         │
│  SPY: $582.30 (+0.8%)  QQQ: $510.20 (+1.2%) │
└─────────────────────────────────────────────┘
```

### Streamlit 页面（新手如何用）
**入口**：`streamlit run fingent/ui/streamlit_app.py`

进入页面后你会看到 4 个 Tab（如果 Polymarket 启用）：

1) **Report**
   - 展示最新一次运行的"市场简报"
   - 包含方向评分、核心信号、新闻摘要、市场概览
   - 支持 AI 生成分析摘要

2) **History**
   - 查看历史运行结果
   - Score Trend 趋势图

3) **Polymarket**
   - **Scan Controls**：Delta 阈值滑块、流动性过滤、最小成交量设置
   - **Shocks**：概率突变榜单（超过阈值的变化）
   - **Top Movers**：变化最大的市场（不论是否超过阈值）
   - **Most Liquid**：成交量最大的市场
   - **Chart**：Delta 散点图（概率 vs 变化幅度，气泡大小=成交量）
   - **Arbitrage**：期限结构套利扫描（折叠区域）

4) **Raw Data**
   - 查看底层原始数据（适合调试）
   - 宏观指标、市场报价、完整 JSON

### UI 架构

Streamlit Dashboard 采用模块化设计：

```
fingent/ui/
├── streamlit_app.py    # 主应用（~500 行）
└── components.py       # 可复用组件（~320 行）
```

**组件清单** (`components.py`)：
- `render_kpi_row` - KPI 指标卡片行
- `render_shock_kpis` - Shock 扫描结果 KPI
- `render_shock_table` - Shock 表格
- `render_movers_table` - Top Movers 表格
- `render_delta_scatter` - Delta 散点图
- `render_news_list` - 新闻列表（带情绪图标）
- `render_market_metrics` - 市场报价卡片
- `render_arb_opportunity` - 套利机会卡片

### Shock 扫描工作流程

**重要：首次扫描建立基准，需等待 60 秒后再次扫描才能检测变化**

```
第一次点击 "Scan Shocks"
    ↓
系统记录当前价格作为基准（baseline）
    ↓
等待 60+ 秒（min_age_seconds 配置）
    ↓
第二次点击 "Scan Shocks"
    ↓
系统计算 delta = 当前价格 - 基准价格
    ↓
返回结果：
  - all_deltas: 所有市场的价格变化（用于 Top Movers、Chart）
  - shocks: 超过阈值的变化（用于 Shocks 表格）
```

**常见显示含义**
- **Events**：扫描到的事件数量
- **Markets**：扫描到的市场数量
- **Shocks**：满足"变化阈值 + 流动性过滤"的突变数量
- **Max Δ**：所有市场中最大的价格变化幅度
- **Total deltas**：计算出 delta 的市场数量（调试信息）

**为什么 delta 都是 0%？**
- 这是正常现象——如果两次扫描间隔很短，市场价格可能没有变化
- 等待更长时间（如 5-10 分钟）后再扫描，会看到实际变化

**为什么 Shocks 数量为 0？**
- 价格变化未超过阈值（默认 5%）
- 可以降低 Delta Threshold 滑块（如 1%）来捕获更小的变化

### 新闻显示

新闻采用紧凑的可展开列表格式：
- **标题行**: `🟢 标题 (时间)` - 情绪图标 + 截断标题 + 发布时间
- **情绪图标**: 🟢 看涨 (>0.3) / 🔴 看跌 (<-0.3) / ⚪ 中性
- **情感方法标注**: 显示情感分析来源（API / Keywords）
- **展开详情**: 点击展开后显示来源、摘要、原文链接

用户可以快速扫描标题，只展开重要的新闻。

### 清除缓存

在侧边栏勾选 **"Clear cache before run"** 可在运行分析前清除所有新闻缓存，确保获取最新数据：

- 清除所有新闻源的缓存（Marketaux、FMP、GNews、Finnhub、AlphaVantage）
- 适用于需要实时数据而非缓存数据的场景
- 默认不勾选，使用缓存以节省 API 配额

### AI 分析生成

点击「✨ Generate AI Analysis」按钮，LLM 会生成通俗易懂的市场分析：

**LLM 内置信号解读指南**：

| 术语 | 通俗解释 |
|------|---------|
| bullish | 市场情绪乐观，风险偏好上升 |
| bearish | 市场情绪悲观，避险情绪上升 |
| hawkish | 央行倾向加息/收紧政策 |
| dovish | 央行倾向降息/宽松政策 |
| risk_on | 投资者愿意承担风险，资金流向股票 |
| risk_off | 投资者规避风险，资金流向国债和黄金 |

**评分含义**：
- `+0.5 以上`: 强看涨信号
- `+0.2 ~ +0.5`: 温和看涨
- `-0.2 ~ +0.2`: 中性
- `-0.5 ~ -0.2`: 温和看跌
- `-0.5 以下`: 强看跌信号

LLM 会将这些技术术语翻译成普通投资者能理解的语言，例如：
> "今日市场整体偏向乐观。美联储官员表态偏温和，通胀数据略有回落，资金正从避险资产流向风险资产。值得关注的是黄金价格有所回调，建议持续关注科技股表现。"

---

## 新手上手指南（一步到位）

如果你是第一次使用 Fingent，可以按下面顺序操作：

1) **安装依赖**
```bash
python -m venv venv
venv\Scripts\activate
pip install -e ".[dev]"
```

2) **配置 `.env`**
复制 `.env.example` 为 `.env`，并至少配置：
- `FINNHUB_API_KEY`（行情/新闻）
- `FRED_API_KEY`（宏观）
- `POLYMARKET_ENABLED=true`（开启预测市场监控）
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`（如需推送）

3) **先跑一次流程**
```bash
python -m fingent.cli.main --once
```
生成一份报告与快照。

4) **打开面板**
```bash
streamlit run fingent/ui/streamlit_app.py
```
进入 **Polymarket** 页签，可手动扫描概率突变和套利机会。

5) **开启常驻监控（推荐）**
```bash
python -m fingent.cli.main --monitor
```
这个命令会：
- 低频全量扫描（默认 30 分钟）
- 高频热点扫描（默认 3 分钟）
- 检测到重大概率变化后自动推送 Telegram

热点和冷却状态会保存在 `data/hotspots.json`、`data/shock_cooldown.json`。
价格基准与历史会写入 SQLite（`shock_prices` 表），用于重启后继续对比变化。

6) **确认四大分区是否命中**
```bash
python -m fingent.cli.main --list-polymarket-tags
```
将输出的 tag slug 或 id 填入 `config/config.yaml` 的 `polymarket_sector_tags`。

---

## 预测市场监控（低频全量 + 高频热点）

监控模式适用于 **实时盯盘 + 重大变化推送** 的场景：

**工作机制**
1. **低频全量扫描**：从 Polymarket 拉取事件/市场，计算活跃度并生成热点列表  
2. **高频热点扫描**：只扫描热点市场，发现概率突变就推送

**历史数据与可视化**
- 每次扫描会把最新 mid 价格写入 SQLite（`shock_prices` 表）
- 在 Streamlit 的 **Polymarket → Shock Details** 中会显示 **24h 历史曲线** 与 **区间变化统计**
- 过期市场（end_time 已过）会在低频全量扫描时自动清理历史数据

**配置位置**：`config/config.yaml`
```yaml
monitoring:
  enabled: true
  low_freq_minutes: 30      # 低频全量扫描
  high_freq_minutes: 3      # 高频热点扫描
  hotspots_limit: 30        # 热点数量
  hotspot_min_volume: 3000  # 热点最小成交量
  cooldown_minutes: 30      # 推送冷却
  max_events_per_tag: 20
  max_markets_per_event: 10
  hotspot_min_depth_usd: 300
  hotspot_max_spread_bps: 600
```

**分区标签配置（推荐：经济/政治/财经/加密）**
```yaml
polymarket_sector_tags:
  politics: ["politics"]
  finance: ["finance"]
  economy: ["economy"]
  crypto: ["crypto"]
```
系统会先通过 Gamma `/tags` 解析 tag_id，再用 `/events?tag_id=...` 拉取事件，避免关键词误召回。若没有配置 `polymarket_sector_tags`，则退回到 `polymarket_sectors` 关键词召回作为兜底。
为了减少 404 噪音，系统会优先使用 `/events` 返回的 `markets` 列表直接解析，必要时才回退 `/events/{id}` 或 `/events/slug/{slug}`。

**如何确认 tag slug/id**
```bash
python -m fingent.cli.main --list-polymarket-tags
```
在输出中找到你想要的分区标签，填入 `polymarket_sector_tags`（支持 slug 或 id）。

---

## 常见问题

**Q: 为什么第一次扫描"没有概率突变"？**
A: 第一次扫描主要用来建立基准（baseline），需要等待至少 60 秒（`min_age_seconds` 配置）后再次扫描才会检测到变化。这是设计行为，不是 bug。

**Q: 为什么 Total deltas 显示 0？**
A: 可能的原因：
1. 第一次扫描刚建立基准，还没有可比较的数据
2. 基准太旧或太新（需要在 `min_age_seconds` 和 `max_age_seconds` 之间）
3. 需要重启 Streamlit 服务器以加载最新代码

**Q: 为什么 delta 都显示 +0.00%？**
A: 这是正常现象——两次扫描间隔短，市场价格没有变化。等待 5-10 分钟后再扫描会看到实际变化。

**Q: 为什么 Max Δ 显示 N/A？**
A: `all_deltas` 列表为空，通常是首次扫描或基准过期。等待 60 秒后再次扫描。

**Q: 没有推送 Telegram？**
A: 检查 `.env` 里的 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` / `TELEGRAM_ENABLED`，以及 `monitoring.cooldown_minutes` 是否过长。


## 配置说明

### 告警规则 (config/config.yaml)

```yaml
alert_rules:
  - name: btc_crash
    description: "BTC 24h 跌幅超 8%"
    condition:
      metric: btc_24h_change
      operator: "<"
      threshold: -0.08
    severity: high

  - name: vix_spike
    description: "VIX 超过 25"
    condition:
      metric: vix_level
      operator: ">"
      threshold: 25
    severity: high
```

### 计算规则

```yaml
calculation_rules:
  # 24h 变化计算方式（写死，避免不一致）
  change_24h: "last_close / close_24h_ago - 1"
  change_7d: "last_close / close_7d_ago - 1"

  # 数据不足时的处理
  insufficient_data: "skip_with_warning"
```

---

## 扩展指南

### 新增数据源

1. 在 `fingent/providers/` 创建新 Provider
2. 继承 `BaseProvider` 或 `OptionalProvider`
3. 实现 `healthcheck()` 和数据获取方法
4. 在 `registry` 注册

### 新增分析节点

1. 在 `fingent/nodes/` 创建新 Node
2. 继承 `BaseNode`
3. 实现 `run(state)` 方法，返回 partial state update
4. 在 `graph/builder.py` 添加到工作流

### 新增告警规则

在 `config/config.yaml` 的 `alert_rules` 中添加新规则即可，无需改代码。

---

## Polymarket 套利检测（Arbitrage）

### 功能概述

Fingent 集成了 Polymarket 期限结构套利检测功能，可以：

1. **新闻触发**：监听 Finnhub 新闻，关键词匹配时触发扫描
2. **市场召回**：从 Polymarket Gamma API 搜索相关事件/市场
3. **期限结构检测**：同一事件下不同到期日的市场，检测概率变动不同步
4. **风险过滤**：成交量、价差、深度、冷却时间等过滤器
5. **可视化**：Streamlit Dashboard 的 Arbitrage 选项卡

### 核心逻辑

**期限结构套利 (Term Structure Arbitrage)**：

```
同一事件，不同到期日的市场（如 3月到期 vs 5月到期）
当短期市场概率剧变，但长期市场未同步变化时，可能存在套利机会

delta_short = current_mid(short) - p0(short)
delta_long = current_mid(long) - p0(long)

if abs(delta_short - delta_long) > threshold:
    → 检测到期限结构错位
```

### 配置

在 `config/config.yaml` 中配置：

```yaml
arbitrage:
  enabled: true  # 启用套利检测

  # 触发关键词（正则表达式，支持中英文）
  trigger_keywords:
    - "(H200|H100|NVIDIA|NVDA)"
    - "(Fed|CPI|inflation|rate cut)"
    - "(Trump|tariff)"
    - "(gold|Gold|GOLD|黄金|XAU|GLD|XAUUSD|gold price|gold futures|bullion)"
    - "(silver|Silver|SILVER|白银|XAG|SLV|XAGUSD|silver price|silver futures)"

  # 同义词映射（自动扩展关键词搜索）
  synonym_map:
    gold: ["gold", "xau", "gld", "bullion", "gold price", "黄金", "金价"]
    silver: ["silver", "xag", "slv", "白银", "银价"]
    nvidia: ["nvidia", "nvda", "geforce", "cuda", "英伟达"]
    bitcoin: ["bitcoin", "btc", "比特币"]
    fed: ["fed", "federal reserve", "fomc", "美联储", "联储"]

  # 期限结构策略
  term_structure:
    delta_threshold: 0.05      # 5% 差值触发
    trigger_window_minutes: 120

  # 风控参数
  risk:
    min_volume_24h: 5000       # 最小成交量 $5000
    max_spread_bps: 300        # 最大价差 3%
    min_depth_usd: 1000        # 最小深度 $1000
    cooldown_seconds: 900      # 冷却 15 分钟
```

### 关键词匹配增强

系统使用智能关键词匹配，支持：

1. **大小写不敏感**：`gold` 匹配 `Gold`, `GOLD`
2. **同义词扩展**：搜索 `gold` 会自动扩展为 `gold, xau, gld, bullion, 黄金, 金价`
3. **词边界匹配**：`gold` 不会误匹配 `golden` 或 `marigold`
4. **中英文支持**：支持中文关键词如 `黄金`, `白银`

```python
# Polymarket 搜索时自动应用同义词扩展
from fingent.providers.polymarket import PolymarketProvider

provider = PolymarketProvider()
markets = provider.search_markets_by_keyword(
    keywords=["gold"],
    synonym_map=config.get("synonym_map", {})
)
# 实际搜索: gold, xau, gld, bullion, 黄金, 金价
```

### 使用方法

**方法 1：Streamlit Dashboard**

```bash
streamlit run fingent/ui/streamlit_app.py
# 点击 "Arbitrage" 选项卡 → "Scan Polymarket"
```

**方法 2：代码调用**

```python
from fingent.arb.engine import ArbEngine

engine = ArbEngine()

# 手动扫描
opportunities = engine.run_scan()

# 新闻触发扫描
opportunities = engine.process_news(
    headline="NVIDIA announces H200 sales to China",
    summary="...",
)

# 完整流程（含 Finnhub 新闻）
results = engine.run_full_pipeline(use_finnhub=True)
```

### 数据模型

| 模型 | 说明 |
|------|------|
| `PolymarketEvent` | 事件（包含多个市场） |
| `PolymarketMarket` | 市场（含 CLOB token IDs、tenor_days） |
| `PolymarketQuote` | 订单簿报价（bid/ask/mid/depth/spread） |
| `ArbSnapshot` | 初始价格快照（P0，用于计算 delta） |
| `ArbOpportunity` | 套利机会（legs、edge、confidence、risk_flags） |

### 注意事项

- Polymarket API 在某些地区可能有访问限制
- 套利检测需要同一事件有 2+ 个不同到期的市场
- 默认为纸面交易（PAPER），不自动下单
- LLM 仅用于解释，不参与套利判断

---

## 技术栈

- **Python 3.11+**
- **LangGraph** - 工作流编排
- **Pydantic v2** - 配置管理
- **httpx** - HTTP 客户端
- **CCXT** - 加密货币交易所 API
- **fredapi** - FRED 数据
- **finnhub-python** - Finnhub 数据
- **SQLAlchemy** - 数据库 ORM
- **APScheduler** - 定时任务
- **Streamlit** - Web UI
- **Docker** - 容器化

---

