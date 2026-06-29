# 先配好 API key
export OPENAI_API_KEY="sk-xxx"

# 单次查询（向后兼容）
python3 agent.py "今天深圳多少度？"

# REPL 模式（基础交互）
python3 agent.py --repl

# 增强交互模式（彩色输出 + 分隔线）
python3 agent.py --interactive

# SSE 服务模式（NDJSON 流式输出，供外部程序消费）
python3 agent.py --serve

# Web 页面模式（内置 HTTP 服务器 + 浏览器 UI）
python3 agent.py --web

## 查看对话历史

每次对话自动保存在 `.cagent_sessions/` 目录，包含 tracer 数据。用 `--history` 查看：

```bash
python3 agent.py --history
```

## 目录结构

```
CAgent/
├── agent.py                   # 主程序
├── .env                       # API key 配置
├── .cagent_sessions/          # 对话历史（自动生成）
│   ├── 20260629_185005.json
│   └── ...
├── mcp/tools.mcp.json         # MCP 外部工具配置
├── plugins/                   # 插件目录
└── README.md
```
