# Meituan AI-Diet

基于自然语言与多-Agent编排的外卖对话推荐系统。  
当前版本提供连续对话、定位附近店、可解释推荐、追问重排与自动回归检查。

## 快速开始

### 1) 安装

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2) 配置 `.env`

至少配置：

```bash
OPENAI_API_KEY=your_key
LLM_API_URL=https://your-provider
BACKEND_API_BASE=http://127.0.0.1:8000
```

建议同时配置：

```bash
USE_LIVE_POI=true
AMAP_API_KEY=your_amap_key
```

### 3) 启动

后端：

```bash
uvicorn app.main:app --reload
```

前端：

```bash
streamlit run streamlit_app.py
```

## 核心能力

- 连续对话：`/v1/chat`
- 普通推荐：`/v1/recommend`
- 反馈学习：`/v1/feedback`
- 定位能力：`/v1/location/*`
- 多-Agent编排：
  - `OrchestratorAgent`：轮次路由（recommend / qa / smalltalk / reset）
  - `RetrievalAgent`：候选召回、作用域锁定、兜底扩展
  - `ResponseAgent`：回答组织、对比卡片、建议追问、润色
  - `MemoryAgent`：结构化约束记忆（预算/距离/时效/口味）

## 关键优化点（当前版本）

- 追问默认走 `fast_mode`，降低时延
- `换个更近的` 支持硬约束过滤（无更近时明确提示）
- 回答支持 QA 与推荐分流，减少答非所问
- 聊天会话持久化到 SQLite，支持并发锁与 JSON 迁移
- 定位支持自动授权 + 地址纠偏
- 输出支持对比卡片 `compare_cards`
- `debug.agent_steps` 可查看 agent 决策与耗时

## 回归测试

```bash
python scripts/chat_regression_check.py
```

当前覆盖 12 条关键边界场景（QA 路由、健康追问、更近重排、reset、agent 耗时等）。

## 主要接口

- `POST /v1/chat`
- `POST /v1/recommend`
- `POST /v1/feedback`
- `GET /v1/location/geocode`
- `GET /v1/location/ip`
- `GET /v1/location/reverse`
- `GET /v1/location/health`

## 常用配置项（节选）

- LLM:
  - `USE_LLM_PARSER`
  - `PARSER_MODEL`
  - `LLM_API_URL`
  - `LLM_TIMEOUT_SEC`
- 推荐质量与速度:
  - `USE_LLM_REASONER`
  - `LLM_REASON_TOP_K`
  - `LLM_REASON_WORKERS`
  - `USE_VECTOR_SEMANTIC`
  - `EMBEDDING_API_URL`
- 回复润色:
  - `USE_RESPONSE_POLISHER`
  - `RESPONSE_POLISH_MODEL`
  - `RESPONSE_POLISH_TIMEOUT_SEC`
- 定位与实时 POI:
  - `USE_LIVE_POI`
  - `AMAP_API_KEY`
  - `AMAP_RADIUS_M`
  - `AMAP_TIMEOUT_SEC`
- 前端:
  - `FRONTEND_TIMEOUT_SEC`

## 数据文件

- 商家样本：`app/data/merchants.json`
- 商家语料：`app/data/merchant_profiles.json`
- 用户反馈：`app/data/user_profiles.json`
- 会话库：`app/data/chat_sessions.sqlite3`（运行时生成）
