# 本机订阅与备用模型

本机可以通过官方 Codex CLI 使用 ChatGPT 登录所包含的 Codex 额度。OpenAI API 单独计费；本功能不会把 Plus 转成 API 密钥，也不会复制登录凭据。

先在运行电脑上安装官方 Codex CLI，并用 `codex login` 登录 ChatGPT。然后在本地 `.env` 中启用：

```dotenv
CODEX_SUBSCRIPTION_ENABLED=true
CODEX_CLI_PATH=codex
CODEX_MODEL=
CODEX_TIMEOUT_SECONDS=60
```

留空 `CODEX_MODEL` 时使用 CLI 默认可用模型。可以指定该账号实际支持的 Codex 模型，不假定它和网页 ChatGPT 的模型菜单完全相同。

启用后的调用顺序是：本机订阅 Codex → DMXAPI `gemini-3.8-flash` → Google Gemini。订阅超时、未登录、额度用尽、CLI 不存在或未返回有效最终文本时，会自动进入 API 备用链。关闭开关即可恢复原来的两级 API 调用。

每个 API 通道不叠加 SDK 内部重试，遇到调用失败后立即进入下一通道，避免一次失败持续阻塞整封简报。

Codex 在空的临时目录中运行，提示词经标准输入传递；禁用命令执行、浏览器、搜索、插件和用户配置，不将 Notion、SMTP 和 API 密钥传入子进程。使用已有的 CLI 凭据存储，不读取或复制 `auth.json`。

当前公开仓库的 GitHub Actions 不启用订阅通道，仍使用 API 备用链；代码会在 CI 中跳过 Codex。要将订阅用于每日自动任务，需要另外选择本机定时执行或私有常开运行环境。仅推送此代码不会让现有 GitHub 定时任务消耗订阅额度。

官方参考：

- [Codex 身份验证与计费方式](https://learn.chatgpt.com/docs/auth)
- [非交互运行及 CI 身份验证](https://learn.chatgpt.com/docs/non-interactive-mode#authenticate-in-automation)
