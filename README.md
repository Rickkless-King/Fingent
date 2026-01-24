# Fingent
**Fingent** 是一个基于LangGraph 构建的自动化宏观金融分析系统，实现从宏观经济到微观资产的 Top-Down分析流程。

---

## 项目定位

### 核心价值

1. **自动化 Top-Down 分析**：宏观经济 → 跨资产联动 → 市场情绪 → 综合报告
2. **信号标准化**：每个分析节点产出统一格式的 Signal，便于规则引擎处理
3. **插件化架构**：数据源、分析节点、告警规则可独立增删
4. **工程可维护**：不是一次性 demo，而是能持续迭代的系统

### 解决的问题

- 手动盯盘太累，信息分散在多个平台
- 宏观数据（利率、CPI）、市场数据（BTC、黄金、美股）、情绪数据需要整合分析
- 需要系统自动帮你"连点成线"，给出结构化判断

---

## 系统架构

### 数据流向

```
┌─────────────────────────────────────────────────────────────────┐
│                         数据源层                                 │
├─────────────┬─────────────┬─────────────┬──────────────────────┤
│    FRED     │   Finnhub   │ AlphaVantage│  Polymarket(可选)    │
│  (宏观经济)  │  (行情/新闻) │  (新闻情绪)  │   (预测市场)         │
└──────┬──────┴──────┬──────┴──────┬──────┴───────────┬──────────┘
       │             │             │                  │
       ▼             ▼             ▼                  ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Provider 适配层                             │
│   FREDProvider / FinnhubProvider / AlphaVantageProvider / ...   │
│         (统一接口、超时重试、缓存、错误处理)                        │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                     LangGraph 工作流                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────────┐  │
│  │Bootstrap │→ │  Macro   │→ │  Cross   │→ │      News       │  │
│  │  Node    │  │ Auditor  │  │  Asset   │  │     Impact      │  │
│  └──────────┘  └──────────┘  └──────────┘  └────────┬────────┘  │
│                                                     │           │
│                              ┌───────────────────────           │
│                              ▼                                  │
│                      ┌─────────────────┐                        │
│                      │   Synthesize    │                        │
│                      │   & Alert Node  │                        │
│                      └────────┬────────┘                        │
└───────────────────────────────┼─────────────────────────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                 ▼
        ┌──────────┐     ┌──────────┐      ┌──────────┐
        │ Telegram │     │ Streamlit│      │  SQLite  │
        │   告警    │     │   面板   │      │  存档    │
        └──────────┘     └──────────┘      └──────────┘
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
│   │   ├── okx.py              # OKX Crypto 行情
│   │   └── polymarket.py       # Polymarket（可选）
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
│   │   ├── telegram.py         # Telegram 推送
│   │   ├── persistence.py      # 数据持久化
│   │   └── scheduler.py        # 定时任务
│   │
│   ├── ui/
│   │   └── streamlit_app.py    # Streamlit 面板
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
    primary: alphavantage   # 新闻情绪
    fallback: finnhub

  macro:
    primary: fred           # 宏观经济

  sentiment:
    polymarket:
      enabled: false        # 可选，失败不影响主流程
```

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

### 3. 运行

```bash
# 单次运行
python -m fingent.cli.main --once

# 定时运行
python -m fingent.cli.main --scheduled

# 启动 Streamlit 面板
streamlit run fingent/ui/streamlit_app.py
```

---

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

