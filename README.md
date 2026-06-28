# CAgent — 对照 Claude Code 学习的迷你 Agent

对照 [Deep Dive Claude Code](https://github.com/waiterxiaoyy/Deep-Dive-Claude-Code) 的 13 章，逐层从零搭建一个 Python Agent。

## 路线图

| 阶段 | 章 | CAgent 版本 | 状态 |
|------|----|-------------|------|
| 核心循环 | Ch01 Agent 循环 | `v1` — 最简 loop + 2个工具 + maxTurns | ✅ |
| 工具系统 | Ch02 工具系统 | `v2` — ToolBase 基类、自动 schema 生成、注册式架构 | ✅ |
| 提示词工程 | Ch03 Prompt 工程 | `v3` — SystemPrompt 模块化组装、动态注入 | ✅ |
| Shell 安全 | Ch04 Shell 安全 | `v4` — run_shell 沙箱、黑名单、超时、截断 | ✅ |
| 权限引擎 | Ch05 权限引擎 | `v5` — PermissionEngine allow/deny/ask + 规则持久化 | ✅ |
| 上下文管理 | Ch06 上下文管理 | `v6` — ContextManager token 预算、智能压缩 | ✅ |
| MCP 协议 | Ch07 MCP 协议 | `v7` — MCPClient 外部工具发现、动态加载 | ✅ |
| 插件生态 | Ch08 插件生态 | `v8` — PluginBase/PluginManager + 3 内置插件 | ✅ |
| 多 Agent | Ch09 多 Agent | 子 Agent/Team/Swarm | ⏳ |
| CLI 传输层 | Ch10 CLI 传输 | 多种运行模式 | ⏳ |

## 运行

```bash
# 先配好 API key
export OPENAI_API_KEY="sk-xxx"

# 运行
python3 agent.py "今天深圳多少度？"

# 自定义输入
python3 agent.py "帮我算 25 * 4，然后查一下北京的天气"
```
