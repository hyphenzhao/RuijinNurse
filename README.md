# Ruijin Nurse——神经功能障碍患者的智能护理助手

> AI / LLM-based Nursing Expert System for Parkinson’s disease, Epilepsy and Other Functional Neural Illnesses  

---

## 1️⃣ 项目简介

**Ruijin Nurse** 是一个面向 **帕金森病（PD）、癫痫等神经功能性疾病患者** 的智能护理专家系统。  
它结合了：

- **大语言模型（LLM）** 的知识推理与对话能力  
- **Django 后端** 的医疗业务逻辑与数据服务  
- **Unity 前端** 的可视化 / 交互界面（例如 3D 场景、虚拟病房、交互式指导）  

目标是为**护士、照护者以及家庭照护者**提供一个安全可靠的辅助手段，用于：

- 规范化护理流程与健康宣教  
- 辅助发现风险信号（如跌倒、高风险症状）  
- 记录和追踪患者日常功能状态与用药情况  
- 提供个性化的护理建议与康复训练提示  

> ⚠️ **重要声明：本项目仅用于科研与教学探索，不构成医疗诊断或治疗意见，不可替代专业医师或护士的临床决策。**

---

## 2️⃣ 核心功能（规划中）

根据当前设计与后续规划，本项目将逐步实现以下能力：

- 🩺 **护理问答与行为指导**
  - 围绕 PD、癫痫等疾病特征，提供标准化护理问答
  - 针对常见问题（如用药、步态冻结、癫痫发作处理）给出循证指导建议（来源于指南与文献）

- 📋 **护理流程与评估表单助手**
  - 结合 Django 后端管理常见护理评估量表与流程
  - 支持对话式填写/核对评估表单和记录（例如跌倒风险、生活自理能力等）

- 🤖 **多模型支持的 LLM 护理专家**
  - 支持调用云端大模型（如 Kimi、文心一言等）
  - 支持本地大模型（如本地 LLM 推理服务），便于私有化部署与数据合规

- 🧠 **Unity 场景与交互应用（可选）**
  - 通过 Unity 提供更直观的交互界面：
    - 虚拟病房/家庭场景教学
    - 护理操作流程演练
    - 患者/家属可视化教育内容

---

## 3️⃣ 系统架构概览

当前仓库主要包含两大部分：

- `mysite/`  
  - 基于 **Django** 的后端服务
  - 负责：
    - 用户与权限管理
    - API 接口（与 Unity 或 Web 前端交互）
    - 调用 LLM 推理服务
    - 存储与管理结构化的护理相关数据（如记录、量表等）

- `Unity/`  
  - 基于 **Unity** 的前端 / 客户端工程
  - 负责：
    - 展示交互界面、3D 场景或多媒体内容
    - 通过 HTTP / WebSocket 等方式与 Django 后端通讯
    - 对用户输入（护士/照护者/患者）进行采集并传给后端 LLM

整体结构示意（简化）：

```text
[用户：护士/照护者/患者]
          │
          ▼
   [Unity 前端 或 Web 页面]
          │  HTTP / WebSocket
          ▼
      [Django 后端 mysite]
          │  调用 REST / SDK / 本地服务
          ▼
      [LLM 推理服务（云端或本地）]
```

---

## 4️⃣ 快速开始（开发环境）

> 以下步骤仅为一个示意性指引，具体命令/路径可根据你本地环境调整。

### 4.1 克隆仓库

```bash
git clone https://github.com/hyphenzhao/RuijinNurse.git
cd RuijinNurse
```

### 4.2 配置并运行 Django 后端（mysite）

1. 进入 Django 目录：

   ```bash
   cd mysite
   ```

2. 创建并激活虚拟环境（示例为 Python 方式）：

   ```bash
   python -m venv venv
   # Windows
   venv\Scripts\activate
   # macOS / Linux
   source venv/bin/activate
   ```

3. 安装依赖（如果有 `requirements.txt`）：

   ```bash
   pip install -r requirements.txt
   ```

4. 创建并配置 `.env`（不要提交到 Git）：
   - 配置 LLM 接口 Key（如 `OPENAI_API_KEY` / `KIMI_API_KEY` / `BAIDU_API_KEY` 等）
   - 配置数据库连接或其他敏感参数

5. 迁移数据库并启动开发服务器：

   ```bash
   python manage.py migrate
   python manage.py runserver
   ```

   默认访问地址通常为：  
   `http://127.0.0.1:8000/`

### 4.3 配置并运行 Unity 客户端（Unity）

1. 使用 **Unity Hub** 打开 `Unity/` 目录中的工程。
2. 在 Unity 中设置：
   - 与 Django 后端通讯的服务器地址（如 `http://127.0.0.1:8000/api/...`）
   - 场景加载、UI 文本、语言等
3. 进入对应场景，点击 **Play** 进行测试。

---

## 5️⃣ 仓库目录结构（示意）

```text
RuijinNurse/
├─ mysite/           # Django 后端项目目录
│  ├─ manage.py
│  ├─ <app1>/
│  ├─ <app2>/
│  └─ ...
├─ Unity/            # Unity 客户端工程目录
│  ├─ Assets/
│  ├─ ProjectSettings/
│  └─ ...
├─ .gitignore
└─ README.md         # 本文件
```

> 由于项目仍在活跃开发中，上述结构可能会持续调整与扩展。

---

## 6️⃣ LLM 接入（设计思路）

本项目预期支持多种 LLM 接入方式，包括但不限于：

- ☁️ **云端模型**
  - 如 Kimi、文心一言等中文能力较强的模型
  - 通过官方 SDK 或 REST API 调用
- 🖥️ **本地部署模型**
  - 使用本地推理服务器（如 `llama.cpp`、`vllm` 等）
  - 适用于对隐私和数据合规要求更高的场景（医院内网、科研专网）

Django 后端会对外提供统一的「护理问答/推理接口」，内部再根据配置选择具体 LLM 实现，便于后续替换和扩展。

---

## 7️⃣ 适用场景（愿景）

- 医院病房护理团队的辅助工具（在合规沙箱环境中）
- 神经功能疾病专科门诊的宣传教育与随访辅助手段
- 家庭照护者的护理知识查询与日常照护提醒
- 护理专业的教学/培训场景（演练 PD / 癫痫等患者照护流程）
- 结合可穿戴设备、步态监测、EEG/EMG 数据的多模态扩展（科研方向）

---

## 8️⃣ 贡献方式

欢迎对以下方面感兴趣的同学与同事一起协作：

- 护理学 / 神经科 / 康复医学相关的流程与知识体系整理  
- Django / RESTful API / Web 前端开发  
- Unity 场景、交互界面、3D 可视化制作  
- LLM 提示词工程（prompt engineering）、知识库构建与评估  
- 数据合规、安全与隐私保护策略设计  

如需参与：

1. Fork 本仓库
2. 创建你的分支：`git checkout -b feature/xxx`
3. 提交修改：`git commit -m "feat: xxx"`
4. 推送分支：`git push origin feature/xxx`
5. 提交 Pull Request

---

## 9️⃣ 许可与使用说明

本项目当前主要用于 **科研与教学探索**：

- 📌 不构成临床诊断或治疗依据  
- 📌 不可替代专业医生和护士的判断  
- 📌 使用时应严格遵守当地法律法规及所在机构伦理和数据合规要求  

如需在真实临床环境中试用或进一步合作，请联系仓库维护者。

---

## 🔟 联系方式

- GitHub：[@hyphenzhao](https://github.com/hyphenzhao)

如你对 **AI + 护理 + 神经功能疾病** 的交叉方向感兴趣，欢迎一起完善 Ruijin Nurse，让智能系统更好地服务神经功能障碍患者与照护者。
