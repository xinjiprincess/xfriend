# Xfriend 助手与旧模板升级

内置通用助手显示为 **Xfriend 助手**（英文：**Xfriend Assistant**），运行于 Singu Xfriend。小星是桌面助手入口，可以调用已配置的专家；专家本身仍可保留用户自定义的名称和角色。

## 本次调整

- 默认专家库名称、欢迎语、人格文件和帮助技能统一为 Xfriend 助手。
- 随包帮助说明覆盖小星、专家、团队、原生 / DSH 引擎、插件、语音、声音库、导出与平台协同。
- 移除旧产品官网、文档链接、一键安装建议及已经不适用的 CLI 登录说明。帮助优先指向应用内实际入口；命令使用 `xfriend --help` 核对。
- 默认助手直接处理用户的问题，不再首先要求用户给助手取名或完成身份问卷。其他专家的原有引导流程保留。

## 已安装用户如何生效

服务启动时，系统仅重命名来自 `general-assistant` 模板且仍使用旧出厂名称的专家。如果新名称已经被同一用户使用，则添加该专家的 ID 作为区分。用户自定义名称不改动。

该专家下次启动时，通过工作区存储接口检查旧内置帮助技能、`SOUL.md` 和欢迎信息。只有内容与历史出厂版本的 SHA256 指纹完全一致时才替换；文本换行、BOM 和 JSON 排版差异不会妨碍识别。

升级同时识别旧工作区根目录、当前系统目录及历史系统目录中的技能。旧帮助技能仅删除已识别的 `SKILL.md` 文件，保留同目录中的其他资料。新帮助技能写入当前技能目录，重复启动不会重复迁移。

如果旧助手帮助技能被用户禁用，替换后的新技能也保持禁用；用户仍可在技能管理中手动启用。

聊天记录、模型配置、凭据、记忆、用户文档和用户修改过的技能 / 人格 / 欢迎语不被覆盖。用户主动删除的帮助技能不会被重新添加。对于修改过的旧技能，系统保留原内容；可在技能管理中删除该技能，或根据新的内置模板手动更新其中的个人内容。

## 实现与回归

- `src/octop/infra/agents/experts/assistant_upgrade.py`：按内容指纹迁移旧出厂文件。
- `src/octop/infra/agents/experts/assistant_legacy_hashes.json`：旧模板内容指纹；不包含旧帮助正文。
- `src/octop/infra/agents/experts/library/general-assistant/`：新名称、欢迎语、人格与帮助技能。
- `src/octop/infra/agents/manager.py`：启动时名称迁移、工作区升级，以及默认助手的直接对话模式。
- `tests/unit/agents/test_assistant_upgrade.py`：覆盖重复升级、历史存储路径、定制内容及会话保留。

回归命令：

```powershell
uv run pytest tests/unit/agents/test_assistant_upgrade.py tests/unit/agents/test_general_assistant_template.py tests/unit/agents/test_default_agent.py tests/unit/agents/test_agent_manager.py -q
```
