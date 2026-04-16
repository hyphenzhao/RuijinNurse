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

## 2️⃣ 核心功能

根据当前仓库中已经实现的 Django 与前端代码，本项目当前已具备或正在直接支撑以下能力：

- 💬 **基于 Ollama 的多模型对话宣教**
  - 支持从宣讲页面直接与本地或指定 Ollama 服务进行对话
  - 支持普通单模型模式，也支持按 `Agent` 配置切换不同智能体
  - 已实现流式输出，前端可实时显示“思考中 / 最终回答”两类内容

- 🧠 **可配置的智能体管理**
  - 通过 Django 管理页风格的 `setup/` 页面维护智能体
  - 每个智能体可配置：
    - Ollama 主机与端口
    - 指定模型名
    - system prompt / 角色设定
    - 参考知识文本
    - 启用 / 停用状态

- 📚 **知识文档导入与知识文本维护**
  - 支持上传并抽取 `docx`、`pptx`、`pdf` 文档文本
  - 上传后的内容会保存到 `KnowledgeDocument`，并自动并入系统知识文本
  - 项目中同时保留了基于 `role.txt` / `knowledge.txt` 的默认上下文机制

- 🗂️ **会话级上下文记忆**
  - 系统会按 Django session 与当前模型组合保存对话上下文
  - 运行期上下文会落盘到 `mysite/promotions/runtime_sessions/`
  - 同一会话内的多轮提问可继续带上历史消息

- 🔐 **登录保护**
  - 已实现自定义中间件，默认要求用户登录后才能访问主要页面
  - 登录页使用 Django 内置认证视图

- 🖼️ **Unity 场景嵌入式前端**
  - 当前宣讲页面已经嵌入 Unity WebGL 页面
  - 页面布局支持桌面端三栏布局与移动端 Unity 背景式交互布局

- 🎬 **多媒体宣教界面**
  - 宣讲页中已预留入院介绍视频、富文本回答渲染、代码高亮和媒体卡片等交互能力

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

### 4.4 配置中文语音输入 / 输出组件（`faster-whisper` + `Piper`）

对于当前项目，推荐将语音能力单独部署为一个独立的语音服务，而不是直接耦合进 `Django` 主进程。

推荐组合：

- `faster-whisper`
  - 用于语音识别（ASR, speech-to-text）
  - 支持中文，适合离线部署
- `Piper`
  - 用于语音合成（TTS, text-to-speech）
  - 开源、可离线部署，适合作为独立服务组件

推荐部署方式：

- 与 `Django` 部署在同一台机器
- 部署在局域网内另一台边缘节点机器
- 部署在医院内网服务器上，通过 HTTP API 提供服务

建议语音服务至少提供以下接口：

- `POST /asr`
  - 输入音频文件
  - 输出识别文本
- `POST /tts`
  - 输入文本
  - 输出音频文件或音频流
- `GET /health`
  - 用于部署连通性检测

#### 4.4.1 Ubuntu 部署示例

1. 安装系统依赖：

   ```bash
   sudo apt update
   sudo apt install -y python3 python3-venv python3-pip ffmpeg curl
   ```

2. 创建语音服务目录并进入：

   ```bash
   mkdir -p ~/ruijin_speech_service
   cd ~/ruijin_speech_service
   ```

3. 创建虚拟环境并激活：

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

4. 安装 `faster-whisper` 及基础服务依赖：

   ```bash
   pip install -U pip
   pip install faster-whisper fastapi uvicorn python-multipart soundfile
   ```

5. 安装 `Piper`：

   - 方式 A：使用系统包 / 预编译二进制（推荐）
   - 方式 B：从 `Piper` 官方仓库下载对应 Linux 可执行文件

   常见做法是下载：

   - `piper` 可执行文件
   - 中文语音模型文件，如：
     - `zh_CN-huayan-medium.onnx`
     - `zh_CN-huayan-medium.onnx.json`

6. 准备目录结构：

   ```text
   ruijin_speech_service/
   ├─ .venv/
   ├─ app.py
   ├─ piper/
   │  ├─ piper
   │  ├─ zh_CN-huayan-medium.onnx
   │  └─ zh_CN-huayan-medium.onnx.json
   └─ tmp/
   ```

7. 启动语音服务：

   ```bash
   uvicorn app:app --host 0.0.0.0 --port 8010
   ```

#### 4.4.2 Windows 11 部署示例

1. 安装基础环境：

   - 安装 `Python 3.10+`
   - 安装 `ffmpeg`
   - 建议安装 `Git`

2. 创建语音服务目录：

   ```powershell
   mkdir C:\ruijin_speech_service
   cd C:\ruijin_speech_service
   ```

3. 创建并激活虚拟环境：

   ```powershell
   python -m venv .venv
   .venv\Scripts\activate
   ```

4. 安装 `faster-whisper` 及基础服务依赖：

   ```powershell
   pip install -U pip
   pip install faster-whisper fastapi uvicorn python-multipart soundfile
   ```

5. 安装 `Piper`：

   - 下载 Windows 版 `Piper` 可执行文件
   - 下载中文语音模型，例如：
     - `zh_CN-huayan-medium.onnx`
     - `zh_CN-huayan-medium.onnx.json`

6. 推荐目录结构：

   ```text
   C:\ruijin_speech_service
   ├─ .venv\
   ├─ app.py
   ├─ piper\
   │  ├─ piper.exe
   │  ├─ zh_CN-huayan-medium.onnx
   │  └─ zh_CN-huayan-medium.onnx.json
   └─ tmp\
   ```

7. 启动语音服务：

   ```powershell
   uvicorn app:app --host 0.0.0.0 --port 8010
   ```

#### 4.4.3 macOS 部署示例

1. 安装基础依赖：

   ```bash
   brew install python ffmpeg
   ```

2. 创建语音服务目录：

   ```bash
   mkdir -p ~/ruijin_speech_service
   cd ~/ruijin_speech_service
   ```

3. 创建虚拟环境并激活：

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

4. 安装 `faster-whisper` 及基础服务依赖：

   ```bash
   pip install -U pip
   pip install faster-whisper fastapi uvicorn python-multipart soundfile
   ```

5. 安装 `Piper`：

   - 下载 macOS 版本 `Piper` 可执行文件
   - 下载中文语音模型，例如：
     - `zh_CN-huayan-medium.onnx`
     - `zh_CN-huayan-medium.onnx.json`

6. 启动语音服务：

   ```bash
   uvicorn app:app --host 0.0.0.0 --port 8010
   ```

#### 4.4.4 中文支持说明

`faster-whisper`：

- 对普通话中文支持较好
- 对中英混合场景通常也有较好表现
- 更建议优先测试医疗术语、药名、科室名、人名和地名识别效果

`Piper`：

- 支持中文语音合成，但效果取决于具体中文声学模型
- 不同中文模型在自然度、音色和停顿处理上差异较大
- 建议优先固定一套经过实际测试的中文模型后再进入部署阶段

#### 4.4.5 部署建议

对于医疗科研和离线场景，建议：

- 优先使用完全离线部署
- 将语音服务与 `Django` 服务分离
- 通过配置文件指定语音服务地址，例如：
  - `SPEECH_SERVICE_URL=http://127.0.0.1:8010`
- 优先保存文本日志，而不是默认长期保存原始音频
- 在正式使用前，针对中文医疗术语进行专项测试

#### 4.4.6 当前阶段建议

在项目初期，建议先完成以下最小可用路径：

1. 浏览器或 Unity 端录音
2. 将音频发送到独立语音服务的 `/asr`
3. 将识别文本送入当前 `Django + Ollama` 问答流程
4. 将回答文本发送到 `/tts`
5. 返回音频给前端播放

这样可以在不破坏现有对话架构的前提下，逐步引入中文语音输入输出能力。

---

## 5️⃣ 仓库目录结构（当前）

```text
RuijinNurse/
├─ mysite/                    # Django 后端项目目录
│  ├─ manage.py
│  ├─ mysite/                 # Django project settings / urls
│  └─ promotions/             # 当前主要业务 app
│     ├─ models.py            # Agent、KnowledgeDocument
│     ├─ views.py             # 宣讲对话、流式接口、系统管理页
│     ├─ forms.py             # 智能体与对话表单
│     ├─ middleware.py        # 登录保护中间件
│     ├─ templates/
│     ├─ static/
│     └─ migrations/
├─ Unity/            # Unity 客户端工程目录
│  ├─ Assets/
│  ├─ ProjectSettings/
│  └─ ...
├─ .gitignore
└─ README.md         # 本文件
```

> 由于项目仍在活跃开发中，上述结构可能会持续调整与扩展。

---

## 6️⃣ LLM 接入（当前实现与设计思路）

当前 Django 代码已经实际接入 **Ollama**，并围绕其构建了统一的对话调用逻辑。当前实现包括：

- 可通过 `setup/` 页面配置默认 Ollama 主机与端口
- 可动态拉取指定 Ollama 服务上的可用模型列表
- 可为每个智能体单独指定其 Ollama 地址与模型
- 支持流式调用 `/api/chat` 并将输出转为前端可消费的 SSE 风格事件
- 兼容部分模型输出中的 `<think>...</think>` 推理内容拆分

在此基础上，本项目仍预期继续扩展多种 LLM 接入方式，包括但不限于：

- ☁️ **云端模型**
  - 如 Kimi、文心一言等中文能力较强的模型
  - 通过官方 SDK 或 REST API 调用
- 🖥️ **本地部署模型**
  - 使用本地推理服务器（如 `llama.cpp`、`vllm` 等）
  - 适用于对隐私和数据合规要求更高的场景（医院内网、科研专网）

Django 后端目前已经对外提供统一的宣讲 / 问答接口，内部根据模型选择或智能体配置决定具体调用目标，便于后续替换和扩展。

---

## 7️⃣ 当前已实现页面与路由

结合当前 `urls.py` 与 `promotions` app，已可确认的主要路由包括：

- `/promotion/`
  - 主宣讲对话页面
  - 集成 Unity WebGL、聊天面板、模型选择与调试信息
- `/promotion/stream/`
  - 流式问答接口
  - 使用 `StreamingHttpResponse` 返回 SSE 风格事件
- `/promotion/setup/`
  - 系统管理页面
  - 管理默认模型配置、智能体配置、知识文本与知识文档导入
- `/promotion/setup/ollama-models/`
  - 根据给定主机 / 端口查询 Ollama 模型列表
- `/accounts/login/` 与 `/accounts/logout/`
  - Django 内置认证入口

---

## 8️⃣ 适用场景（愿景）

- 医院病房护理团队的辅助工具（在合规沙箱环境中）
- 神经功能疾病专科门诊的宣传教育与随访辅助手段
- 家庭照护者的护理知识查询与日常照护提醒
- 护理专业的教学/培训场景（演练 PD / 癫痫等患者照护流程）
- 结合可穿戴设备、步态监测、EEG/EMG 数据的多模态扩展（科研方向）

---

## 9️⃣ 贡献方式

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

## 🔟 许可与使用说明

本项目当前主要用于 **科研与教学探索**：

- 📌 不构成临床诊断或治疗依据  
- 📌 不可替代专业医生和护士的判断  
- 📌 使用时应严格遵守当地法律法规及所在机构伦理和数据合规要求  

如需在真实临床环境中试用或进一步合作，请联系仓库维护者。

---

## 1️⃣1️⃣ 联系方式

- GitHub：[@hyphenzhao](https://github.com/hyphenzhao)

如你对 **AI + 护理 + 神经功能疾病** 的交叉方向感兴趣，欢迎一起完善 Ruijin Nurse，让智能系统更好地服务神经功能障碍患者与照护者。
