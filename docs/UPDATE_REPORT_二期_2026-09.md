# RuijinNurse 二期升级 — 阶段更新报告

- 日期：2026-09-08
- 范围：D:\RuijinNurse_20260616_Update
- 主题：问答数据分析 · 患者管理 · 多模型 AI 分析配置 · 聊天页患者关联

---

## 一、本次更新内容

### 1. 问答数据库与高频问题分析（需求 1）

- 新增 `QARecord` 模型：网页端 / App 端每次问答完成后自动落库，含问题、回答、思考过程、模型键、智能体、用户、关联患者、耗时（ms）、错误信息、关键词、主题、提取状态。
- 三个落库写点全部挂接：`promotion_stream`（Web 流式）、`promotion_view`（Web 老 AJAX 路径）、`api/views.py ChatStreamView`（App 端，经 `chat_history_callback` 回调，App 契约零变化）。
- 90 秒窗口幂等防重（同会话同问题不重复入库）；服务端错误记录带 `error` 字段，数据分析默认排除、单独统计失败数。
- **数据分析看板**（`/promotion/analytics/`，入口在系统管理页顶部导航）：
  - 指标磁贴：总问答数 / 今日新增 / 关联患者问答 / 平均回复耗时
  - 每日问答趋势折线（全部 + 关联患者双系列，补齐连续日期）
  - 患者关注主题 TOP10、高频关键词 TOP10（横向条形图）
  - 高频问题 TOP15 表格、问答明细表（分页 + 详情弹窗）
  - 筛选：日期区间 / 患者 / 疾病 / 主题 / 问题关键词
  - 图表基于 ECharts 5.6（本地 vendored 至 static，无 CDN 依赖），调色板经 dataviz 校验脚本验证通过（7 槽浅色调色板，超出并入灰色，患者姓名脱敏 `张*`）

### 2. 系统管理页改版 + 数据分析 AI 驱动配置（需求 2）

- 新增共享顶部导航 `_admin_nav.html`：⚙️系统管理 / 📊数据分析 / 🛏患者管理 / 🏥返回宣讲页面，三个管理页均接入；系统管理原有功能（单模型配置、知识图谱 CRUD、智能体 CRUD、AI 导入）零改动（仅插入一行 include）。
- 数据分析页顶部 **AI 驱动配置卡**：
  - 服务类型：Ollama / OpenAI / DeepSeek / OpenAI 兼容接口
  - API 地址（按类型自动提示默认地址）→ 获取模型 → 选择或手动填写模型 → 保存（单活跃策略，允许离线保存）
  - 多提供商客户端 `ai_client.py`：Ollama 走原生 `/api/tags` + `/api/chat`，其余走已安装的 openai SDK（OpenAI 兼容，含超时/重试/中文错误提示）
  - **AI 洞察简报**：按当前筛选条件聚合统计 → 调用配置的 AI 生成护理数据分析简报（总体概况/患者关切热点/趋势与变化/护理与宣教建议/风险关注），前端 marked + DOMPurify 渲染防 XSS
  - 聊天对话管线保持不变（仍仅 Ollama 智能体/单模型），多提供商配置仅用于数据分析

### 3. 患者管理 CRUD（需求 3）

- 新增 `Patient` + `SurgeryPlan`（一对多手术计划）模型，`/promotion/patients/` 页面：
  - 字段：床号、姓名、性别、出生日期（自动计算年龄）、疾病种类（帕金森病/癫痫/肌张力障碍/情感障碍/精神分裂症/其他）、入院时间、多条计划手术时间（动态增删行，含术式备注）、预计在院天数范围（x ~ y 天，含校验）、注意事项、备注
  - **量表字段已预留**（`scales` JSONField 恒为 `{}`，页面灰字占位说明，后期开放 UPDRS/MMSE/NPI 等）
  - 列表仅关键列；疾病筛选 + 含已归档开关；**归档为软删除**（历史问答关联保留，可恢复在院）
- Django admin 注册 Patient/SurgeryPlan/QARecord/AIConfig/Agent 等（运维可后台直查）。

### 4. 聊天页左侧患者抽屉（需求 4）

- 宣讲页新增左侧抽屉菜单（左侧悬浮按钮 + 控件区按钮两个入口）：搜索患者 → 选择关联 → 徽章显示当前患者 → 可清除。
- 关联为会话级（Django session），刷新页面保留；患者信息块（`[患者信息]`：床号/姓名/年龄/诊断/入院/手术计划/注意事项）注入聊天系统上下文供 AI 参考。
- 该患者问答自动关联患者 ID（`QARecord.patient`），数据分析页可按患者过滤。
- App 端：`ChatRequestSerializer` 新增可选 `patient_id` 字段——**旧 App 不传则行为与旧版完全一致**；传了则落库关联 + 患者上下文注入。其余 API 端点、SSE 事件序列（status/thinking/delta/done）一律未动。

### 5. 基础设施调整

- `TIME_ZONE` 改为 `Asia/Shanghai`（趋势图按本地日历日聚合；DB 仍存 UTC aware 时间戳）
- SQLite `OPTIONS timeout=30`（批量提取与聊天并发写的锁等待余量）
- 依赖：新增 `jieba==0.42.1`（离线关键词兜底分词）；补齐 venv 中缺失的 DRF 家族包（djangorestframework/simplejwt/cors-headers/drf-spectacular，与 settings 中的 INSTALLED_APPS 对齐）
- 关键词/主题提取为**手动批量触发**（数据分析页按钮，每次最多 30 条）：AI 优先，未配置/失败自动 jieba + 医学领域词典（12 主题桶 × 100+ 领域词）兜底，`extraction_status` 恒收敛到 done；卡死 30 分钟以上的 processing 记录自动重置

## 二、数据模型（迁移 0007）

| 模型 | 关键字段 |
|---|---|
| Patient | bed_no, name, gender, birth_date, disease_type, admission_time, expected_stay_min/max_days, cautions, notes, scales(JSON), is_active |
| SurgeryPlan | patient FK, planned_time, note |
| QARecord | user/patient/agent FK, source(web/api), session_scope, model_key, question, answer, thinking, error, keywords/topics(JSON), extraction_status, extraction_error, latency_ms, created_at（索引：created_at+extraction_status、patient+created_at） |
| AIConfig | provider_type(ollama/openai/deepseek/custom_openai), base_url, api_key, model, is_active |

## 三、测试与验收结果

- 测试结构：`promotions/tests.py` → `promotions/tests/` 包（`git mv` 保留历史），语音集成测试原样保留并打 `@tag('integration')`；新增 `api/tests.py`。
- 新增测试 60 例（5 个测试文件 + API 契约），覆盖：患者 CRUD/软删/手术计划/年龄/校验、问答落库全字段/空回答/90s 幂等/错误记录、领域词典分类/jieba 兜底、AI 提取三态（成功/解析失败/连接失败）+ JSON 解析各形态、分析页统计/筛选/分页/提取端点/洞察端点/AI 配置端点、聊天流患者关联落库与上下文注入（mock Ollama 流）、App API 契约（JWT/agents/sessions/health/serializer 新旧负载）。

```
验收命令（cd mysite，激活 venv）：
python manage.py check                                    ✅ 0 issues
python manage.py test promotions.tests api.tests --exclude-tag integration   ✅ 60/60 OK
python manage.py makemigrations --check --dry-run         ✅ No changes detected
```

- 渲染冒烟：/promotion/、/promotion/setup/、/promotion/patients/、/promotion/analytics/ 全部 200 无模板异常。
- HTTP 冒烟（runserver 8013）：登录中间件 302 跳转正常；echarts/marked/purify 本地静态 200；/api/v1/health/ 200。
- 语音集成测试（依赖外部 Whisper/Piper 服务）未运行，不属于本次验收门禁；服务在线时 `python manage.py test` 全量语义不变。

## 四、风险与遗留事项

1. **API Key 明文存储**（AIConfig.api_key）：内网本地部署假设下可接受；页面 password 型输入不回显，未写入日志；若将来对外网开放需改加密方案。
2. **双轨历史**：会话记忆仍由 `runtime_sessions/` JSON 文件承担（App 兼容），`QARecord` 为二期上线起的增量统计审计数据，不做存量回填。
3. **主题过滤与聚合在 Python 侧**（SQLite 不支持 JSONField 数组 contains / json_each）：当前病区量级（单月数千条）无压力；数据量大幅增长后建议迁移 PostgreSQL。
4. **提取为同步批量**（单次最多 30 条）：AI 未配置时秒级完成；配置 AI 后每条约数秒至数十秒，页面会提示等待。
5. 数据库备份 `mysite/db.sqlite3.bak` 已保留在实施前状态，如迁移异常可回滚。
6. 未提交 git：本次全部改动留在工作区（含迁移 0007），按团队流程自行提交。

## 五、关键文件清单

- 新增：`mysite/promotions/ai_client.py`、`qa_store.py`、`migrations/0007_*.py`、`templates/promotions/{_admin_nav.html, patients.html, analytics.html}`、`static/promotions/js/echarts.min.js`、`tests/` 包 5 个文件、`api/tests.py`、本报告
- 修改：`models.py`、`views.py`、`urls.py`、`view_helper.py`、`admin.py`、`api/views.py`、`api/serializers.py`、`templates/promotions/{promotion.html, setup.html}`、`mysite/settings.py`、`requirements.txt`、`README.md`
