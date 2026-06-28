#!/usr/bin/env python3
"""
CAgent v7 — MCP 协议
================================
对照 Claude Code Ch07: MCP 协议
- 工具不再硬编码，通过协议从外部发现
- MCPClient 从远程/本地加载工具
- 动态注册到 ToolRegistry
- 模拟 MCP Server 演示协议交互
"""

import json
import os
import sys
import inspect
import subprocess
import importlib.util
from pathlib import Path
from openai import OpenAI

# ─── 从 .env 加载 ─────────────────────────────
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().strip().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.strip().split("=", 1)
            os.environ.setdefault(k, v)

# ─── 配置 ─────────────────────────────────────
CLIENT = OpenAI(
    base_url=os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1"),
    api_key=os.environ.get("OPENAI_API_KEY", ""),
)
MODEL = os.environ.get("CA_LLM_MODEL", "deepseek-chat")
MAX_TURNS = 20


# ═══════════════════════════════════════════════
# Ch07: MCP 协议 — 外部工具发现
# ═══════════════════════════════════════════════

class MCPToolDefinition:
    """
    MCP 工具定义。
    类似 MCP 协议中 tool 资源的 JSON 结构。
    """
    def __init__(self, name: str, description: str, parameters: dict, handler=None):
        self.name = name
        self.description = description
        self.parameters = parameters   # JSON Schema
        self._handler = handler        # 实际执行函数

    def to_openai_schema(self) -> dict:
        """转为 OpenAI tool schema 格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def execute(self, **kwargs) -> str:
        if self._handler:
            return self._handler(**kwargs)
        return f"[MCP 外部工具 {self.name}] 收到参数: {kwargs}"


class MCPClient:
    """
    MCP 客户端。
    从外部来源发现并加载工具。
    
    支持三种来源:
    1. 本地 MCP 配置文件 (*.mcp.json)
    2. Python 脚本动态注册
    3. 远程 MCP Server (HTTP, 简化版)
    """

    def __init__(self):
        self.definitions: list[MCPToolDefinition] = []
        self.server_urls: list[str] = []

    def discover_from_local_config(self, config_path: str) -> list[MCPToolDefinition]:
        """
        从本地 MCP 配置文件发现工具。
        配置文件格式: {"tools": [{"name": "...", "description": "...", "parameters": {...}}, ...]}
        """
        path = Path(config_path)
        if not path.exists():
            print(f"  ⚠ MCP 配置不存在: {config_path}")
            return []

        try:
            data = json.loads(path.read_text())
            for tool_def in data.get("tools", []):
                td = MCPToolDefinition(
                    name=tool_def["name"],
                    description=tool_def.get("description", ""),
                    parameters=tool_def.get("parameters", {"type": "object", "properties": {}}),
                )
                self.definitions.append(td)
                print(f"  🔌 MCP 发现工具: {td.name}")
            return self.definitions
        except Exception as e:
            print(f"  ⚠ MCP 配置解析失败: {e}")
            return []

    def discover_from_script(self, script_path: str) -> list[MCPToolDefinition]:
        """
        从 Python 脚本动态加载工具。
        脚本需要提供一个 get_mcp_tools() 函数，
        返回 MCPToolDefinition 列表。
        """
        path = Path(script_path)
        if not path.exists():
            print(f"  ⚠ MCP 脚本不存在: {script_path}")
            return []

        try:
            spec = importlib.util.spec_from_file_location("mcp_module", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if hasattr(module, "get_mcp_tools"):
                tools = module.get_mcp_tools()
                for td in tools:
                    self.definitions.append(td)
                    print(f"  🔌 MCP 从脚本加载工具: {td.name}")
                return tools
            else:
                print(f"  ⚠ 脚本 {script_path} 缺少 get_mcp_tools() 函数")
                return []
        except Exception as e:
            print(f"  ⚠ MCP 脚本加载失败: {e}")
            return []

    def discover_from_remote(self, url: str) -> list[MCPToolDefinition]:
        """
        从远程 MCP Server 发现工具（HTTP 协议简化版）。
        实际 MCP 协议使用 JSON-RPC over stdio/SSE。
        """
        self.server_urls.append(url)
        print(f"  🔗 MCP 远程服务器: {url}")
        # 简化实现：返回一个模拟工具
        return []

    def register_all(self):
        """将所有发现的 MCP 工具注册到 ToolRegistry"""
        for td in self.definitions:
            # 用 MCPToolWrapper 适配 ToolBase 接口
            wrapper = MCPToolWrapper(td)
            ToolRegistry.register(wrapper)
        print(f"  ✅ MCP 注册 {len(self.definitions)} 个外部工具")


class MCPToolWrapper:
    """
    将 MCPToolDefinition 包装成 ToolBase 兼容的接口，
    使其能通过 ToolRegistry 统一调度。
    无需继承 ToolBase（用 duck typing 适配）。
    """

    def __init__(self, definition: MCPToolDefinition):
        self.name = definition.name
        self.description = definition.description
        self._def = definition

    def openai_schema(self) -> dict:
        return self._def.to_openai_schema()

    def __call__(self, **kwargs) -> str:
        return self._def.execute(**kwargs)


# 全局 MCP 客户端
MCP = MCPClient()


def init_mcp():
    """初始化 MCP：从默认配置和脚本发现工具"""
    config_dir = Path(__file__).parent / "mcp"
    config_dir.mkdir(exist_ok=True)

    # 1. 本地配置文件
    config_path = config_dir / "tools.mcp.json"
    if config_path.exists():
        MCP.discover_from_local_config(str(config_path))

    # 2. Python 脚本
    for script_path in config_dir.glob("*.py"):
        if script_path.name != "__init__.py":
            MCP.discover_from_script(str(script_path))

    # 3. 注册到 ToolRegistry
    if MCP.definitions:
        MCP.register_all()
        print(f"📦 MCP 工具总数: {len(MCP.definitions)}")


# ═══════════════════════════════════════════════
# Ch06: 上下文管理
# ═══════════════════════════════════════════════

class ContextManager:
    CHARS_PER_TOKEN = 1.5

    def __init__(self, max_tokens: int = 8192):
        self.max_tokens = max_tokens
        self.token_counts: list[int] = []
        self.compression_count = 0

    def estimate_tokens(self, text: str) -> int:
        if not text: return 0
        return int(len(text) / self.CHARS_PER_TOKEN) + 1

    def estimate_message_tokens(self, msg) -> int:
        total = 0
        raw = msg if isinstance(msg, dict) else msg.model_dump()
        for key in ("content", "role", "name"):
            val = raw.get(key)
            if isinstance(val, str):
                total += self.estimate_tokens(val)
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        for subv in item.values():
                            if isinstance(subv, str):
                                total += self.estimate_tokens(subv)
        return total + 4

    def total_used(self) -> int:
        return sum(self.token_counts)

    def add_message(self, msg):
        self.token_counts.append(self.estimate_message_tokens(msg))

    def should_compress(self) -> bool:
        return self.total_used() > self.max_tokens

    def compress(self, messages: list) -> list:
        self.compression_count += 1
        print(f"  ⚡ 上下文压缩 #{self.compression_count}")
        if len(messages) <= 2: return messages
        system_msg = messages[0] if messages[0]["role"] == "system" else None
        if system_msg: messages = messages[1:]
        keep = 7
        recent = messages[-keep:] if len(messages) > keep else messages
        old = messages[:-keep] if len(messages) > keep else []
        if not old:
            result = [system_msg] if system_msg else []
            result.extend(messages)
            return result
        compressed_old = []
        for msg in old:
            if msg["role"] == "tool" and len(msg.get("content", "")) > 100:
                c = msg["content"]
                compressed_old.append({**msg, "content": c[:60] + f"...({c.count(chr(10))} 行)"})
            else:
                compressed_old.append(msg)
        if len(compressed_old) > 3:
            compressed_old = [{"role": "system", "content": f"[已压缩旧消息，保留最近对话]"}]
        result = []
        if system_msg: result.append(system_msg)
        result.extend(compressed_old)
        result.extend(recent)
        self._recount(result)
        return result

    def _recount(self, messages: list):
        self.token_counts = [self.estimate_message_tokens(m) for m in messages]

    def get_usage_report(self) -> str:
        return f"上下文: {self.total_used()}/{self.max_tokens} tokens, 压缩 {self.compression_count} 次"

CTX = ContextManager()


# ═══════════════════════════════════════════════
# Ch05: 权限引擎
# ═══════════════════════════════════════════════

RULES_FILE = Path(__file__).parent / ".cagent_rules.json"

class PermissionEngine:
    def __init__(self):
        self.rules_file = RULES_FILE
        self.rules = self._load()

    def _load(self) -> dict:
        default = {"deny_keywords": ["rm -rf /", "rm -rf /*", "mkfs", "dd if=", ":(){ :|:& };:", "> /dev/sda", "chmod -R 777 /"],
                   "ask_keywords": ["rm", "kill", "pkill", "shutdown", "reboot", "poweroff",
                                    "docker rm", "docker rmi", "git push --force", "git reset --hard"],
                   "never_ask": []}
        if self.rules_file.exists():
            try: default.update(json.loads(self.rules_file.read_text()))
            except: pass
        return default

    def _save(self):
        self.rules_file.write_text(json.dumps(self.rules, indent=2, ensure_ascii=False))

    def evaluate(self, command: str) -> tuple[str, str]:
        cmd_lower = command.lower().strip()
        for allowed in self.rules["never_ask"]:
            if allowed in command: return "allow", f"信任: {allowed}"
        for kw in self.rules["deny_keywords"]:
            if kw in cmd_lower: return "deny", f"拒绝: {kw}"
        for kw in self.rules["ask_keywords"]:
            if kw in cmd_lower: return "ask", f"需确认: {kw}"
        return "allow", ""

    def allow_future(self, command: str):
        self.rules["never_ask"].append(command)
        self._save()

PERM = PermissionEngine()


# ═══════════════════════════════════════════════
# Ch03: System Prompt
# ═══════════════════════════════════════════════

class PromptModule:
    def render(self) -> str: raise NotImplementedError

class RoleModule(PromptModule):
    def render(self) -> str:
        return "你是 CAgent，一个多功能 AI 助手。你有权使用各种工具来帮助用户解决问题。"

class ToolGuideModule(PromptModule):
    def render(self) -> str:
        if not ToolRegistry.list_tools(): return ""
        lines = ["## 可用工具"]
        for tool in ToolRegistry._tools.values():
            lines.append(f"- `{tool.name}`: {tool.description}")
        lines.append("遇到问题时，从以上工具中选择合适的调用。")
        return "\n".join(lines)

class OutputFormatModule(PromptModule):
    def render(self) -> str: return "## 回答要求\n- 简洁、准确\n- 给出具体数据\n- 调用完所有工具后给出最终总结"

class SystemPrompt:
    def __init__(self):
        self.modules = [RoleModule(), ToolGuideModule(), OutputFormatModule()]
    def render(self) -> str:
        parts = [m.render() for m in self.modules if m.render()]
        return "\n\n".join(parts)


# ═══════════════════════════════════════════════
# Ch04: Shell 安全
# ═══════════════════════════════════════════════

BLOCKED_KEYWORDS = ["rm -rf /", "rm -rf /*", "mkfs", "dd if=", ":(){ :|:& };:", "> /dev/sda", "chmod -R 777 /", "wget ", "curl ", "nc ", "ncat ", "sudo "]
ALLOWED_DIRS = [Path("/root/CAgent"), Path("/root"), Path("/tmp")]
SHELL_TIMEOUT = 15
MAX_OUTPUT_CHARS = 2000

def validate_command(command: str) -> tuple[bool, str]:
    cmd_lower = command.lower().strip()
    for kw in BLOCKED_KEYWORDS:
        if kw in cmd_lower: return False, f"危险命令被拒绝: {kw}"
    if not command.strip(): return False, "命令不能为空"
    return True, ""

def _is_subpath(path: Path, parent: Path) -> bool:
    try: path.relative_to(parent); return True
    except ValueError: return False

def run_shell_impl(command: str, workdir: str = ".") -> str:
    ok, reason = validate_command(command)
    if not ok: return f"[安全拒绝] {reason}"
    decision, reason = PERM.evaluate(command)
    if decision == "deny": return f"[权限拒绝] {reason}"
    if decision == "ask": return f"[需确认] {reason}\n命令: {command}\n如需执行，回复: 允许执行 {command}"
    exec_dir = Path(workdir).resolve()
    if not any(_is_subpath(exec_dir, d) for d in ALLOWED_DIRS):
        return f"[安全拒绝] 禁止在 {exec_dir} 目录执行命令"
    try:
        r = subprocess.run(command, shell=True, cwd=exec_dir, capture_output=True, text=True, timeout=SHELL_TIMEOUT)
        out = r.stdout + r.stderr
        if len(out) > MAX_OUTPUT_CHARS: out = out[:MAX_OUTPUT_CHARS] + f"\n...（截断，共 {len(out)} 字符）"
        return f"exit code: {r.returncode}\n{out}"
    except subprocess.TimeoutExpired: return f"[超时] {SHELL_TIMEOUT}秒"
    except Exception as e: return f"[执行错误] {e}"


# ═══════════════════════════════════════════════
# Ch02: 工具系统
# ═══════════════════════════════════════════════

class ToolRegistry:
    _tools: dict = {}
    @classmethod
    def register(cls, tool):
        cls._tools[tool.name] = tool
    @classmethod
    def get_openai_tools(cls) -> list:
        return [t.openai_schema() for t in cls._tools.values()]
    @classmethod
    def execute(cls, name: str, **kwargs) -> str:
        tool = cls._tools.get(name)
        return tool(**kwargs) if tool else f"错误: 未知工具 '{name}'"
    @classmethod
    def list_tools(cls) -> list:
        return list(cls._tools.keys())

class ToolBase:
    name: str = ""
    description: str = ""
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.name: ToolRegistry.register(cls())
    def openai_schema(self) -> dict:
        sig = inspect.signature(self.run)
        doc = inspect.getdoc(self.run) or ""
        props, req = {}, []
        for pn, pp in sig.parameters.items():
            if pn == "self": continue
            req.append(pn)
            type_map = {int: "number", float: "number", str: "string", bool: "boolean"}
            jt = type_map.get(pp.annotation, "string")
            pd = ""
            for line in doc.split("\n"):
                line = line.strip()
                if line.startswith(f"{pn}:"): pd = line.split(":", 1)[1].strip(); break
            props[pn] = {"type": jt, "description": pd or f"参数 {pn}"}
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                "parameters": {"type": "object", "properties": props, "required": req}}}
    def run(self, **kwargs) -> str: raise NotImplementedError
    def __call__(self, **kwargs) -> str: return self.run(**kwargs)


# ═══════════════════════════════════════════════
# 内置工具（v1～v6）
# ═══════════════════════════════════════════════

class Calculator(ToolBase):
    name = "calculator"
    description = "做四则运算: add, subtract, multiply, divide"
    def run(self, a: float, b: float, op: str) -> str:
        """a: 第一个数\nb: 第二个数\nop: 运算"""
        ops = {"add": lambda: a+b, "subtract": lambda: a-b, "multiply": lambda: a*b, "divide": lambda: a/b if b!=0 else "除数不能为0"}
        return str(ops.get(op, lambda: f"未知运算: {op}")())

class GetWeather(ToolBase):
    name = "get_weather"
    description = "查询指定城市的天气"
    def run(self, city: str) -> str:
        """city: 城市名"""
        mock = {"深圳": "26°C, 多云", "北京": "22°C, 晴", "上海": "24°C, 小雨", "悉尼": "18°C, 晴"}
        return mock.get(city, f"{city}: 暂时查不到天气数据")

class RunShell(ToolBase):
    name = "run_shell"
    description = "在安全环境中执行 Shell 命令"
    def run(self, command: str, workdir: str = ".") -> str:
        """command: 要执行的 Shell 命令\nworkdir: 执行目录"""
        return run_shell_impl(command, workdir)


# ═══════════════════════════════════════════════
# Agent 循环
# ═══════════════════════════════════════════════

def agent_loop(user_input: str) -> str:
    system_prompt = SystemPrompt().render()
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_input}]
    for m in messages: CTX.add_message(m)
    tools = ToolRegistry.get_openai_tools()
    print(f"📦 已注册工具: {ToolRegistry.list_tools()}")
    print(f"📊 {CTX.get_usage_report()}")

    for turn in range(1, MAX_TURNS + 1):
        print(f"\n── 第 {turn} 轮 ──")
        if CTX.should_compress():
            messages = CTX.compress(messages)
            print(f"  📊 {CTX.get_usage_report()}")

        response = CLIENT.chat.completions.create(model=MODEL, messages=messages, tools=tools)
        msg = response.choices[0].message
        messages.append(msg)
        CTX.add_message(msg)

        if not msg.tool_calls:
            print(f"  → 模型回答: {msg.content}")
            return msg.content

        for tc in msg.tool_calls:
            func_name = tc.function.name
            args = json.loads(tc.function.arguments)
            print(f"  → 调用工具: {func_name}({args})")
            result = ToolRegistry.execute(func_name, **args)
            print(f"  ← 结果: {result[:100]}...")
            tool_msg = {"role": "tool", "tool_call_id": tc.id, "content": str(result)}
            messages.append(tool_msg)
            CTX.add_message(tool_msg)

    return "达到最大轮次限制"


# ─── 入口 ─────────────────────────────────────
if __name__ == "__main__":
    # 启动时初始化 MCP
    init_mcp()

    prompt = sys.argv[1] if len(sys.argv) > 1 else "今天深圳多少度？顺便帮我算 3.14 * 2.71"
    print(f"🧑 用户: {prompt}")
    result = agent_loop(prompt)
    print(f"\n✅ 最终回答: {result}")
