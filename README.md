# 英语老师式私人学习助手

一个本地优先的英语学习助手：导入自己拥有使用权的课程视频、字幕或公开链接，将课程内容整理成可检索的知识库，并以**课程教学方法启发的助教口吻**进行答疑。它不会自称为老师本人，也不会输出或拼接原课程视频片段。

内置三个相互隔离的智能体：**四级阅读与写作**、**六级阅读与写作**、**考研阅读与写作**。每一个只处理阅读理解或英语写作；导入课程时可指定资料归属，答疑只检索相同考试层级的资料。

## 功能

- 导入 `.txt`、`.srt`、`.vtt` 字幕和音视频；没有字幕时由本机 Whisper 自动识别，不要求先准备字幕。
- 从课程字幕中提炼阅读判断链、证据定位、错误选项识别、写作结构、段落展开和润色方法。
- 回答时区分「课程依据」和「基于课程方法的延伸推演」；后者会明确标识，绝不说成老师原话。
- 支持阅读理解证据链、作文构思、作文修改、英文写作练习与全英文回答。
- 支持拍照提问：手机直接调用相机，或上传题目、作文截图；本地 OCR 后进入对应智能体。
- 支持批量链接：每行一个视频页面，可忽略字幕并强制本地识别音轨。
- 支持 Android PWA：可添加到主屏幕，接近独立 App 的体验。
- 支持后台任务：手机提交长视频后可以锁屏或离开页面，电脑继续识别，重新打开可查看进度。
- 可选企业微信回调：支持明文或 AES 加密的 URL 验证与文本消息回复。

## 启动

```powershell
.\start.ps1
```

首次启动会创建 `.env`、安装依赖并打开本地服务。请先在 `.env` 填入 `OPENAI_API_KEY`；只导入字幕时可暂时不填。启动后打开 <http://127.0.0.1:8000>。资料默认保存在 `data/`；调用转写或回答时，只发送必要的音频/文本给已配置的 AI 服务。

### 手机使用

`.\start.ps1` 默认开启同一局域网访问，并在启动时显示手机地址，例如：

```text
手机访问（需要连接同一 Wi-Fi）：http://10.196.171.62:8000
```

手机和电脑需要连接同一 Wi-Fi。手机使用 `http://` 局域网地址时，拍照入口通过系统文件选择器调用相机；电脑退出程序或切换网络后，地址可能变化。若 Windows 防火墙询问是否允许 Python 访问网络，请选择仅允许专用网络。

只希望电脑自己访问时，运行：

```powershell
.\start.ps1 -LocalOnly
```

### 不在同一 Wi-Fi 时使用手机

推荐使用 Tailscale 私密网络。模型继续运行在电脑上，手机只作为应用界面：

1. 在电脑和手机安装 Tailscale，并登录同一个 Tailscale 账号。
2. 启动本项目：`.\start.ps1`。
3. 在电脑打开一个 PowerShell 窗口，执行：

```powershell
tailscale serve --bg 8000
```

4. 命令会显示一个 `https://你的设备名.你的网络名.ts.net` 地址；首次使用按提示开启 MagicDNS 与 HTTPS。
5. 手机在 Tailscale 开启状态下访问这个 HTTPS 地址。
6. 使用浏览器菜单选择“安装应用”或“添加到主屏幕”，之后从桌面图标打开。

Tailscale Serve 只允许自己 Tailscale 网络中的设备访问，不会把服务公开到普通互联网。电脑需要保持开机、联网和运行本项目；电脑关闭时应用也不可用。如果希望电脑关机后仍可使用，需要把模型和项目部署到带 GPU 的云服务器。

手机端不推荐直接运行 Qwen 7B/8B。iPhone 不支持 Ollama；普通安卓手机即使通过特殊工具运行，也会面临内存不足、速度慢、发热和耗电问题。当前方案让手机使用 App 式界面，把 8B 模型留在 RTX 5060 上运行，体验更稳定。

## 云端部署（电脑可以关闭）

项目包含 `Dockerfile`、`docker-compose.yml` 和 `Caddyfile`。默认使用普通 CPU 云服务器运行网站、OCR、后台任务和 Whisper，文本回答调用 Qwen/DeepSeek 等兼容 API，因此不需要 GPU 服务器和 `OPENAI_API_KEY`。

推荐服务器：

- 最低：2 vCPU、4GB RAM、40GB SSD
- 处理长课程更稳妥：4 vCPU、8GB RAM、80GB SSD
- Ubuntu 22.04/24.04，开放 TCP 80 和 443
- 一个解析到服务器公网 IP 的域名

部署步骤：

```bash
git clone <你的项目仓库地址>
cd 项目目录
cp .env.cloud.example .env.cloud
nano .env.cloud
sudo docker compose up -d --build
```

`.env.cloud` 必须设置：

```dotenv
DOMAIN=你的域名
APP_ACCESS_USER=student
APP_ACCESS_PASSWORD=一个足够长的私人密码
LLM_PROVIDER=compatible
COMPATIBLE_BASE_URL=服务商提供的OpenAI兼容地址
COMPATIBLE_API_KEY=服务商密钥
COMPATIBLE_MODEL=模型名称
```

首次启动后 Caddy 会自动申请 HTTPS 证书。安卓 Chrome 打开 `https://你的域名`，再选择“安装应用”。所有 `/api` 请求都受访问密码保护，课程文件保存在 Docker 持久卷 `app_data` 中，Whisper 模型保存在 `whisper_models` 中。

公共云部署需要云服务器账号、域名、DNS 配置和模型 API 账号。代码可以部署，但这些外部账号和付款不能由项目自动创建。

## 导入说明

- **字幕/文本**：最适合先验证教学风格，SRT/VTT 的时间点会保留。
- **音视频**：应用内置 FFmpeg 与本地 Whisper。没有字幕时会主动识别音轨，使用 `small` 模型和 CPU `int8` 推理作为默认设置；处理后删除临时媒体。
- **公开链接**：应用会先读取公开字幕；没有字幕时临时取得音轨并在本机识别，完成后删除音轨，只保留个人学习所需的文字资料。需要登录才能访问的课程，可配置自己的 Netscape cookie 文件。应用不绕过付费墙或访问权限。
- **批量识别**：每行填写一个公开链接，最多 20 个；勾选“忽略字幕，强制识别音轨”后，即使页面有字幕也会转写音频。
- **拍照提问**：图片通过本地 RapidOCR 识别，支持中英文；识别完成后立即删除图片，不上传图片到 OCR 服务。
- **安卓拍照**：可以分别选择“拍照”或“从相册选择”；点击“先识别到输入框”后，可修正 OCR 结果再提问。
- **后台识别**：批量链接以电脑端后台任务执行；手机断开页面不会取消任务，页面每 5 秒刷新一次进度。

本地识别首次使用会下载模型，默认模型约数百 MB。可在 `.env` 中调整：

```dotenv
TRANSCRIBE_PROVIDER=auto
LOCAL_WHISPER_MODEL=small
LOCAL_WHISPER_DEVICE=cpu
LOCAL_WHISPER_COMPUTE_TYPE=int8
```

`auto` 会优先使用本地 Whisper；`local` 保证课程音频只在当前电脑处理；`openai` 才会调用云端转写。语音识别只解决“音频变文字”，生成教师式阅读/写作回答仍需要 OpenAI 模型或其他本地文本模型。

回答提示词会要求模型把课程方法迁移到当前新问题，并生成原创练习题或两档作文版本，避免只复述课程原文。

### 完全本地回答（Ollama + Qwen3 8B）

RTX 5060 8GB 笔记本显卡建议使用 `qwen3:8b`。模型约 5.2GB，首次下载需要联网，之后可离线推理。

```powershell
irm https://ollama.com/install.ps1 | iex
ollama pull qwen3:8b
ollama run qwen3:8b "请用一句话介绍你自己"
```

确认 Ollama 已运行后，在 `.env` 使用：

```dotenv
LLM_PROVIDER=auto
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:8b
```

重启项目。访问 `/health` 时若显示 `"text_provider":"ollama"`，表示阅读分析、作文修改和原创练习已切换到本地 Qwen，不需要 `OPENAI_API_KEY`。

`auto` 会优先使用正在运行的 Ollama，未运行时才回退到 OpenAI。手机通过 PWA 调用的是电脑上的同一个 Qwen 模型，不会使用性能更弱的手机模型。

默认 `small` 模型在本项目 `models/` 中约占 486MB。长视频使用 CPU 识别可能明显慢于视频时长，建议先导入较短的阅读或写作课程验证效果，再批量处理。

## 企业微信

将企业微信「接收消息服务器配置」指向：

```
https://你的域名/wecom/callback
```

在 `.env` 设置 `WECOM_TOKEN`。若企业微信使用安全模式，还要设置 43 字符的 `WECOM_ENCODING_AES_KEY` 和 `WECOM_CORP_ID`。部署到公网时请使用 HTTPS、反向代理和访问控制；不要将本机开发端口直接暴露到互联网。

群机器人 Webhook 只能主动发送消息，不能接收用户提问；要实现问答，应使用企业微信的「接收消息服务器」或自建应用回调。本实现对应后者。

在企业微信中可用“四级：问题”“六级：问题”“考研：问题”指定智能体；没有前缀时默认进入四级。每个回复仅检索对应层级的课程资料。

## 接口

- `POST /api/import/text`：JSON `{title, content, agent}` 导入文本。
- `POST /api/import/upload`：表单字段 `file`、可选 `title`、`agent` 上传字幕或音视频。
- `POST /api/import/link`：JSON `{url, title?, agent}` 读取公开字幕或临时转写音轨；处理完删除媒体，只保留文字。
- `POST /api/import/links`：JSON `{urls, agent, force_transcribe}` 批量处理最多 20 个链接；单条失败不会中断整批。
- `POST /api/import/links/jobs`：创建后台批量导入任务；`GET /api/jobs` 查询最近任务和进度。
- `POST /api/ask/photo`：表单字段 `file`、可选 `question`、`mode`、`agent`；本地 OCR 后答疑，并返回 `ocr_text`。
- `POST /api/ocr/photo`：只识别图片并将文字返回，供手机端确认和修改。
- `POST /api/ask`：JSON `{question, agent, mode?}`，`agent` 为 `cet4`、`cet6` 或 `postgrad`，`mode` 为 `auto`、`english`、`correction`、`writing`。
- `GET /api/library`：课程库与风格档案。

## 限制

这是个人学习工具，回答体现的是提炼出的教学原则，不代表或冒充视频中的真人。新问题的答案是原创推演，应当自行判断和练习验证。

