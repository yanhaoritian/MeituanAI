# Meituan AI-Diet 技术方案（当前版本）

## 1. 目标

构建一个面向真实用户的对话式外卖推荐系统，支持：

- 多轮追问与条件收敛
- 附近商家召回与可解释推荐
- 质量可控、性能可控、可持续迭代

---

## 2. 架构总览

```text
Streamlit Frontend
  -> FastAPI /v1/chat
      -> OrchestratorAgent
      -> RetrievalAgent
      -> ResponseAgent
      -> MemoryAgent
  -> FastAPI /v1/recommend
```

支撑服务：

- RecommenderService（排序主链路）
- AmapPoiService（定位与附近 POI）
- MenuService（provider/crawler/llm infer/template 多级回退）
- ProfileService（反馈学习）
- ReasonMetricsLogger（质量日志）

---

## 3. 多-Agent职责

### 3.1 OrchestratorAgent

- 输入：本轮 message、是否存在 last_query
- 输出：`recommend / qa / smalltalk / reset / fallback`
- 作用：稳定路由，减少答非所问

### 3.2 RetrievalAgent

- 输入：query、location、scope_ids、exclude_ids、fast_mode
- 能力：
  - 作用域锁定重排
  - 空结果自动扩展全量候选
  - “更近”硬约束过滤

### 3.3 ResponseAgent

- 负责推荐话术、QA、健康追问、对比卡片、追问建议
- 可选低延迟润色（有超时保护）

### 3.4 MemoryAgent

- 追问改写（把短追问并入上一轮约束）
- 结构化记忆（预算/距离/时效/口味）
- 硬约束提取（更近/更快/更便宜）

---

## 4. 推荐链路

1. 解析：Rule + LLM parser（可开关）
2. 策略：默认值、冲突处理、餐饮语义边界
3. 过滤：预算/距离/时效/饮食限制
4. 排序：语义分 + 评分 + 距离 + 价格 + 偏好
5. 菜品：provider -> crawler -> llm infer -> template -> infer
6. 理由：规则+LLM，支持多样化与对比句

---

## 5. 会话与存储

- 会话存储：SQLite（并发锁 + JSON 迁移）
- 用户画像：`user_profiles.json`
- 质量日志：`app/data/reason_metrics_logs/*.jsonl`

会话核心字段：

- history
- last_query
- last_scope_ids
- last_recommendations
- last_top_metrics
- memory

---

## 6. 定位链路

- 浏览器定位（自动请求）
- 地址纠偏（geocode）
- 坐标反查（reverse）
- 健康检查（/v1/location/health）

异常处理：

- IP 定位失败不阻断主流程
- Amap 失败自动回退 mock 数据

---

## 7. 性能与质量策略

### 性能

- Follow-up 默认 `fast_mode`
- 并发生成 TopK 理由
- 回复润色短超时保护

### 质量

- QA/推荐分流
- 健康/解释/对比专项回答
- 事实守护（避免文案跑偏）
- 更近硬约束可执行

---

## 8. 可观测性

`debug` 中输出：

- parser_source / parser_status
- semantic_status
- reason_statuses
- data_source / amap_status
- selected_snapshot
- agent_steps（决策原因与耗时）

---

## 9. 测试与回归

- 编译检查：`python -m compileall`
- 回归脚本：`python scripts/chat_regression_check.py`
- 当前覆盖 12 条关键场景（路由、追问、reset、QA、latency）

---

## 10. 后续优化建议

- 引入 LLM 二次路由判定（边界句）
- 添加 agent 级监控看板（P95、fallback率）
- 菜单可信度分层（source confidence）
- 线上 A/B 路由策略试验

# Meituan AI-Diet 完整技术方案（MVP 到可扩展阶段）

## 1. 文档目标与范围

本方案用于指导 Meituan AI-Diet 从 0 到 1 的研发与技术评审，覆盖：

- 架构模块划分与接口职责
- 关键边界条件与冲突处理策略
- 运维与稳定性保障（监控、告警、降级、容量）
- 安全、成本、测试与上线方案

不在本期范围：

- 与真实美团生产系统打通（当前为 Mock 数据与可替换数据层）
- 复杂用户画像系统（仅保留轻量偏好记忆）
- 多城市、跨语言、多租户复杂治理

---

## 2. 目标与非功能指标

## 2.1 业务目标

- 将模糊自然语言需求转为可执行约束并返回可解释推荐
- 缩短“选择耗时”，降低用户决策疲劳
- 保证输出结果“可用、可信、可说明”

## 2.2 非功能目标（SLO）

- 端到端 P95 延迟：`<= 2s`（含 LLM）
- 可用性：`>= 99.5%`（MVP 单区域）
- 意图+槽位结构化准确率：`>= 90%`
- 推荐结果可解释率：`100%`（每条结果必须给理由）

---

## 3. 总体架构设计

## 3.1 架构风格

- **应用层**：FastAPI（同步 API + 异步任务）
- **AI 编排层**：Intent/Slot + Query Rewrite + Reason Generator
- **推荐引擎层**：硬过滤 + 软排序 + 规则兜底
- **数据层**：Mock JSON（MVP）-> 可迁移到 PostgreSQL + Vector Store
- **展示层**：Streamlit（内部演示与产品验证）

## 3.2 逻辑模块划分

1. `api-gateway`（FastAPI）
   - 接收用户请求、参数校验、trace 注入、统一错误码返回
2. `nlp-orchestrator`
   - 意图识别、槽位提取、默认值补全、冲突消解
3. `policy-engine`
   - 执行策略边界（预算优先、距离默认、动态阈值）
4. `candidate-retriever`
   - 依据硬约束检索候选集
5. `ranking-engine`
   - 基础分 + 语义分融合排序
6. `reason-generator`
   - 生成“可说服”的自然语言推荐理由
7. `profile-service`（轻量）
   - 保存用户偏好（口味、预算带宽、禁忌项）
8. `observability-kit`
   - 日志、指标、链路追踪、告警

## 3.3 请求处理链路

1. 用户输入自然语言
2. LLM 结构化输出（intent + slots + confidence）
3. policy-engine 补全默认值并处理冲突
4. 硬过滤获取候选商家
5. 向量相似度与基础分融合排序
6. 生成推荐理由并返回 TopN
7. 写入埋点日志用于离线评估

---

## 4. 核心数据模型设计

## 4.1 商家模型（MVP）

```json
{
  "id": "m_1024",
  "name": "暖胃酸辣汤面馆",
  "tags": ["汤面", "酸辣", "暖胃", "高蛋白可选"],
  "avg_price": 28,
  "distance_km": 2.1,
  "rating": 4.7,
  "delivery_eta_min": 32,
  "description": "主打酸辣暖胃汤面，支持少油少盐和加蛋白配料。",
  "is_open": true
}
```

建议字段补充：

- `delivery_eta_min`：满足“送达快一点”场景
- `is_open`：避免推荐不可下单商家
- `diet_flags`：如 `no_raw`, `high_protein`, `low_carb`

## 4.2 查询结构化模型

```json
{
  "intent": "order_food",
  "slots": {
    "taste": ["酸辣", "清淡"],
    "category": ["汤面"],
    "budget_max": 30,
    "distance_max_km": 3,
    "delivery_eta_max_min": 40,
    "dietary_restrictions": ["no_raw", "high_protein"]
  },
  "meta": {
    "confidence": 0.92,
    "missing_slots": [],
    "conflict_flags": ["cheap_vs_premium"]
  }
}
```

---

## 5. 边界条件与策略规范（重点）

## 5.1 默认值策略（冷启动）

- 未提预算：默认 `budget_max = 35`
- 未提距离：默认 `distance_max_km = 3`
- 提到“远点没事”：放宽 `distance_max_km = 8`
- 未提品类：按高评分+高相关语义返回多样化 TopN

## 5.2 模糊词量化（动态阈值）

- “便宜”：取当前候选价格分布 `P25`，并上限 `25`
- “不贵”：取 `P40`
- “附近”：默认 3km；“就近”可收紧到 2km
- “高评分”：默认 `rating >= 4.5`
- “送达快”：`delivery_eta_min <= 35`

## 5.3 冲突与矛盾处理优先级

统一优先级（由高到低）：

1. **硬安全约束**（过敏/禁忌）  
2. **用户显式预算**  
3. **用户显式距离/时效**  
4. **饮食目标**（减脂/高蛋白）  
5. **口味与氛围偏好**  
6. **“高端/网红”等软偏好**

冲突案例：

- “便宜但高端”：预算优先，在预算内选评分/描述“高端感”更高的商家，并解释“在预算内最优”
- “想吃生食但不要生食”：以否定约束优先，移除生食类候选
- “越近越好但必须某品牌”：品牌为显式指定，距离退化为次约束

## 5.4 结果为空时兜底策略

当硬过滤后无候选时，逐级放宽，且每一步都向用户解释：

1. 距离 +1km（最多到 8km）
2. 预算 +10%（最多 +30%）
3. 放宽非关键口味标签
4. 若仍为空，建议改写 query（给出 2-3 个按钮建议）

---

## 6. 推荐与排序技术方案

## 6.1 硬过滤

过滤条件：

- `avg_price <= budget_max`
- `distance_km <= distance_max_km`
- `delivery_eta_min <= delivery_eta_max_min`（如有）
- `is_open == true`
- 满足禁忌限制（如 no_raw）

## 6.2 软排序公式（可解释）

定义标准化分数（0~1）：

- `S_rating`（评分越高越好）
- `S_distance`（距离越近越好）
- `S_price`（越接近预算甜点越好）
- `S_semantic`（query 与 description 向量相似度）
- `S_preference`（历史偏好命中）

综合分：

`S_total = 0.30*S_semantic + 0.25*S_rating + 0.20*S_distance + 0.15*S_price + 0.10*S_preference`

说明：

- 初期权重可人工设定，后续通过离线回放+在线反馈迭代
- 返回时携带 `score_breakdown` 供解释器生成自然语言理由

## 6.3 推荐理由生成规则

理由模板至少包含 2 个“证据点”：

- 需求命中证据：如“酸辣、暖胃”
- 约束符合证据：如“预算内、3km 内、配送 30 分钟”
- 权衡解释证据：如“稍远但评分更高”

---

## 7. LLM 结构化输出与鲁棒性

## 7.1 Prompt 工程要求

- 强制 JSON schema 输出（字段固定、类型固定）
- 对缺失槽位输出 `null` + `missing_slots`
- 输出 `confidence` 和 `conflict_flags`
- 严禁生成超 schema 字段（防止解析漂移）

## 7.2 防故障机制

- 首次解析失败：自动重试一次（低温度）
- 二次失败：启用规则解析器（关键词 + 正则）兜底
- 任何情况下保证 API 返回结构稳定，不抛裸异常

## 7.3 模型切换策略

- 主模型：高质量结构化输出
- 备模型：低成本快速模型（主模型超时/故障时切换）
- 超时保护：LLM 超时阈值建议 `800ms ~ 1200ms`

---

## 8. API 设计（MVP）

## 8.1 `POST /v1/recommend`

请求体：

```json
{
  "user_id": "u_001",
  "query": "预算30以内，清淡不油腻，送得快一点",
  "location": {"lat": 31.23, "lng": 121.47}
}
```

返回体（简化）：

```json
{
  "trace_id": "tr_xxx",
  "parsed_query": {},
  "recommendations": [
    {
      "merchant_id": "m_1024",
      "name": "暖胃酸辣汤面馆",
      "score": 0.89,
      "reason": "符合您30元预算，口味清淡且配送预计32分钟。"
    }
  ],
  "fallback_applied": false
}
```

## 8.2 `POST /v1/feedback`

- 接收用户“喜欢/不喜欢/下单”反馈
- 用于偏好更新与离线评估

## 8.3 错误码规范

- `4001` 参数非法
- `4002` schema 解析失败（已兜底）
- `5001` 排序引擎异常
- `5002` 外部模型超时（已降级）

---

## 9. 运维与稳定性设计

## 9.1 可观测性

日志（结构化 JSON）：

- 请求日志：`trace_id`, `user_id`, `latency_ms`, `status_code`
- AI 日志：`intent`, `slots`, `confidence`, `llm_latency`
- 排序日志：候选数量、过滤原因、TopN 分布

指标（Prometheus）：

- `api_qps`, `api_p95_latency`, `error_rate`
- `llm_timeout_rate`, `fallback_rate`
- `empty_result_rate`, `avg_candidate_count`
- `recommend_click_through_rate`（若前端埋点接入）

告警：

- P95 > 2s 持续 5 分钟
- 错误率 > 2%
- fallback_rate > 15%
- empty_result_rate 异常抬升

## 9.2 弹性与降级

- LLM 超时：降级规则解析 + 简化排序
- Embedding 服务异常：仅用标签匹配与基础分
- 数据源异常：返回“精选兜底商家池”
- Streamlit 异常不影响 API（前后端解耦）

## 9.3 容量与性能预估（MVP）

- 目标并发：`50~100 RPS`
- 单请求预算：  
  - 解析与策略：200ms  
  - 检索与排序：300ms  
  - LLM/理由：800ms  
  - 网络与序列化：200ms  
- 总体目标：P95 2s 内

## 9.4 成本控制

- 缓存高频 query 的解析结果（短 TTL）
- 推荐理由模板化优先，减少大模型长文本生成
- 按需切换 Embedding：本地模型优先，云模型作为补充
- 采样记录完整日志，避免全量高成本存储

---

## 10. 安全与合规

- 不存储明文敏感身份信息（手机号、精确地址脱敏）
- 日志中对 `user_id` 做哈希化
- 输入清洗防 prompt injection（黑白名单 + 长度限制）
- 外部调用使用 API Key 管理与最小权限原则
- 保留审计日志（模型版本、prompt 版本、策略版本）

---

## 11. 测试与质量保障

## 11.1 测试分层

- 单元测试：槽位提取、阈值计算、排序函数
- 集成测试：完整 API 链路（含 mock LLM）
- 回归测试：典型语料集（至少 200 条）
- 压测：P50/P95 延迟、超时率、降级触发率

## 11.2 核心验收用例

- 模糊词解析：便宜、附近、清淡、暖胃
- 冲突语句：便宜但高端、快但远
- 特殊约束：不吃生食、减脂高蛋白
- 空结果场景：极低预算+严格距离

## 11.3 发布门禁（Go/No-Go）

- 结构化准确率 >= 90%
- P95 延迟 <= 2s
- fallback_rate <= 10%
- 无 P1/P2 级缺陷

---

## 12. 里程碑与实施计划

## Phase 1（1-2 周）：MVP

- 完成 FastAPI + Streamlit 最小可用链路
- 接入 20-50 条商家 Mock 数据
- 支持意图识别、槽位提取、基础排序、推荐理由

## Phase 2（2-4 周）：稳定性与可观测

- 接入完整监控告警
- 完成降级、超时、重试、缓存策略
- 建立离线评估脚本与回归数据集

## Phase 3（4-8 周）：效果优化

- 反馈闭环（点击/下单）驱动权重调优
- 引入用户偏好记忆
- 评估迁移到真实数据源

---

## 13. 风险清单与应对

- **风险：LLM 输出漂移导致解析不稳定**  
  应对：强 schema + 双通道解析 + 回归集监控

- **风险：冷启动样本太少导致推荐同质化**  
  应对：扩充标签维度与描述语料；多样性重排

- **风险：延迟超预算**  
  应对：并行化（解析/检索）、缓存、模型降级

- **风险：解释理由“看似合理但不真实”**  
  应对：理由必须基于 `score_breakdown` 证据生成

---

## 14. 评审结论建议（可直接用于会议）

当前方案在 MVP 维度具备较强可落地性，建议按“先稳定结构化解析与策略边界，再优化推荐效果”的路线推进。  
评审重点应放在三点：

1. 边界条件优先级是否可执行且可解释  
2. 降级链路是否覆盖 LLM/Embedding/数据源故障  
3. 指标体系是否能支撑持续优化（而非一次性 Demo）

若以上三项通过，即可进入 Phase 1 开发。
