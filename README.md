# Singu Xfriend

Singu Xfriend 是面向 Windows 桌面的个人 AI 助手与多专家工作台。提供中文界面、星形小星悬浮助手、语音交互、专家与团队对话，以及本地用户数据管理。

## 当前状态

当前源码版本为 **1.4.0**。已生成 Windows x64 安装包与便携包，并验证随包服务、首次账号设置、声音库及真实双引擎任务。完整回归为 4242 项通过、148 项跳过；详细范围及验证限制见 [开发方案](docs/xfriend-1.2-design.md)。

1.4 新增统一双引擎专家、顺序团队任务中心、插件升级回退、引擎版本管理及个人声音库。

## 已有功能

- 小星悬浮助手：文字输入、可配置唤醒词、语音识别与回复朗读；语音监听默认关闭。
- 小米 MiMo：ASR、TTS、VoiceDesign 和 VoiceClone，使用管理员配置的服务凭据。
- 专家可绑定原生或 DSH 引擎；新建会话遵循绑定，已存在的历史继续沿用原引擎。
- 执行任务中心支持最多四步顺序协作、权限确认、提问回答、停止和中断记录；完成后可沿原绑定继续追问。
- 插件专家模板可预览后新建；插件升级保存旧文件，失败恢复，并可手动回退。
- DSH 使用独立 ACP 运行时；候选版本经文件校验和初始化/恢复检查后激活，已有受管理会话固定原运行时。
- 个人声音库支持 WAV / MP3 导入、MiMo 声音设计、试听、重命名、删除及默认克隆参考。
- 对话可按单轮、勾选消息、起止范围或整个会话导出 PDF、HTML、DOCX 和 PNG。
- 平台协同入口：连接平台并查看 PCMATE 设备，PCMATE 保持独立应用。平台实测目前按要求跳过。
- 安装升级保留用户目录；支持校验备份并恢复到新目录，保留原始数据。

从“专家”页或“小星助手设置”进入执行任务中心；选择一个专家直接对话，选择多个专家则按所选顺序执行。DSH 需要管理员配置可用的 ACP runner 和现有本地工作目录。原生专家的模型配置与引擎选择相互独立。

随包 DSH 使用 DeepSeek Messages 协议。使用官方服务时，runner 环境中的 `DEEPSEEK_BASE_URL` 为 `https://api.deepseek.com/anthropic`，密钥通过 `DEEPSEEK_API_KEY` 配置；不要直接照搬原生 OpenAI 兼容提供方的基础地址。

插件管理中的“Xfriend 执行专家”提供规划、DSH 执行和审核模板；安装后在任务中心预览并创建独立实例。模板升级不会自动覆盖已有专家的角色、记忆和工作区。可信 Python 插件在宿主进程运行，声明权限不等于提供沙箱。

模型、语音和平台功能需要对应服务的有效配置。使用远程语音服务时，录音及试读内容会发送至该服务。

## 源码运行

需要 Python 3.12+、uv，以及构建界面所需的 Node.js / npm。

```bash
uv sync
cd dashboard
npm ci
npm run build
cd ..
uv run xfriend run
```

首次运行通过设置向导创建管理员，不需要读取密码文件。应用数据默认保存在用户目录下的 `.xfriend`；可通过 `XFRIEND_HOME` 指定独立的数据目录。

## Windows 安装包构建

准备固定版本的 DSH 运行时及浏览器组件，并安装 Go、Wails 3、Git Bash 和 NSIS。构建脚本校验组件清单，输出目录必须是新目录。

```powershell
./scripts/build_xfriend_windows.ps1 -DshDirectory <DSH目录> -BrowsersDirectory <浏览器组件目录> -OutputDirectory <新的输出目录>
```

构建产物、运行数据、密钥及本地工具缓存不提交至 Git。安装包与便携包应作为独立下载文件分发。

## 开发检查

```bash
make install-hooks
make all PYTEST_JOBS=4
cd dashboard
npx tsc -b
npm run build
```

桌面 Go 检查在 `desktop/src` 中执行 `go test ./...`。

## 许可与来源

本项目基于 Octop 平台进行桌面化和产品改造，保留必要的内部 Python 包名及上游版权声明。使用 [MIT 许可证](LICENSE)。DSH 等独立组件遵循其各自许可证。
