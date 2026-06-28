#!/usr/bin/env python3
"""
CAgent v6 — 上下文管理
================================
对照 Claude Code Ch06: 上下文管理
- ContextManager 跟踪 token 用量
- Token 预算控制
- 智能压缩：旧工具结果摘要化、保留关键消息
- 防止长对话撑爆上下文窗口
"""

import json
import os
import sys
import inspect
import subprocess
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
MAX_TURNS = 20  # v6 提高了轮次上限，因为上下文管理能力更强了


# ═══════════════════════════════════════════════
# Ch06: 上下文管理
# ═══════════════════════════════════════════════

class ContextManager:
    """
    管理 messages 数组的大小。
    
    策略：
    1. 跟踪每条消息的估算 token 数
    2. 总 token 超过预算时触发压缩
    3. 压缩策略：system prompt 保留 → 旧 tool 结果摘要化 → 旧对话轮次丢弃
    """

    # 粗略估算: 1 token ≈ 0.75 英文字符 ≈ 1.5 中文字符
    CHARS_PER_TOKEN = 1.5
    SYSTEM_BUDGET_RATIO = 0.15      # system prompt 占用预算上限
    TOOL_BUDGET_RATIO = 0.35        # tool 结果占用预算上限
    RECENT_TURNS_RESERVED = 3       # 至少保留最近 N 轮

    def __init__(self, max_tokens: int = 8192):
        """
        max_tokens: 上下文窗口预算（不是模型的实际窗口，是我们自己设定的工作上限）
        """
        self.max_tokens = max_tokens
        self.token_counts: list[int] = []  # 每条消息的 token 数
        self.compression_count = 0

    def estimate_tokens(self, text: str) -> int:
        """估算文本的 token 数"""
        if not text:
            return 0
        return int(len(text) / self.CHARS_PER_TOKEN) + 1

    def estimate_message_tokens(self, msg) -> int:
        """估算一条消息的 token 数。支持 dict 和 Pydantic 对象。"""
        total = 0
        # 把消息转成 dict（兼容 Pydantic 和 plain dict）
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
        """当前总 token 用量"""
        return sum(self.token_counts)

    def add_message(self, msg):
        """记录一条新消息的 token 数"""
        count = self.estimate_message_tokens(msg)
        self.token_counts.append(count)

    def should_compress(self) -> bool:
        """是否需要压缩"""
        return self.total_used() > self.max_tokens

    def compress(self, messages: list) -> list:
        """
        压缩 messages 数组。
        返回压缩后的新数组。
        """
        self.compression_count += 1
        print(f"  ⚡ 上下文压缩 #{self.compression_count} (当前: {self.total_used()} / {self.max_tokens} tokens)")

        if len(messages) <= 2:
            return messages  # 没什么好压缩的

        # 分离各层
        system_msg = None
        if messages[0]["role"] == "system":
            system_msg = messages[0]
            messages = messages[1:]

        # 保留最近的 RECENT_TURNS_RESERVED 轮
        keep_count = self.RECENT_TURNS_RESERVED * 2 + 1  # user + tool/assistant 配对
        recent = messages[-keep_count:] if len(messages) > keep_count else messages
        old = messages[:-keep_count] if len(messages) > keep_count else []

        if not old:
            result = [system_msg] if system_msg else []
            result.extend(messages)
            return result

        # 压缩旧的 tool 结果 —— 用简短的摘要替换长输出
        compressed_old = []
        for msg in old:
            if msg["role"] == "tool" and msg.get("content", ""):
                content = msg["content"]
                if len(content) > 100:
                    # 保留前 60 字符 + "..." + 行数
                    lines = content.count("\n")
                    summary = content[:60] + f"... ({lines} 行)"
                    compressed_old.append({**msg, "content": summary})
                else:
                    compressed_old.append(msg)
            else:
                compressed_old.append(msg)

        # 把多个旧轮次压缩成一条摘要（如果还有多余的旧消息）
        assistant_msgs = [m for m in compressed_old if m["role"] == "assistant" and m.get("content")]
        if assistant_msgs and len(compressed_old) > 3:
            summary_text = f"[已压缩 {len(compressed_old)} 条旧消息，保留最近的 {keep_count} 轮对话]"
            compressed_old = [{"role": "system", "content": summary_text}]

        # 重组
        result = []
        if system_msg:
            result.append(system_msg)
        if compressed_old:
            result.extend(compressed_old)
        result.extend(recent)

        # 重新统计 token 数
        self._recount(result)
        return result

    def _recount(self, messages: list):
        """重新统计所有消息的 token"""
        self.token_counts = [self.estimate_message_tokens(m) for m in messages]

    def get_usage_report(self) -> str:
        """生成用量报告"""
        total = self.total_used()
        pct = int(total / self.max_tokens * 100)
        return f"上下文: {total}/{self.max_tokens} tokens ({pct}%), 已压缩 {self.compression_count} 次"


# 全局上下文管理器
CTX = ContextManager(max_tokens=8192)


# ═══════════════════════════════════════════════
# Ch05: 权限引擎
# ═══════════════════════════════════════════════

RULES_FILE = Path(__file__).parent / ".cagent_rules.json"

class PermissionEngine:
    def __init__(self, rules_file: Path = RULES_FILE):
        self.rules_file = rules_file
        self.rules = self._load_rules()

    def _load_rules(self) -> dict:
        default = {
            "allow_keywords": [],
            "deny_keywords": [
                "rm -rf /", "rm -rf /*", "mkfs", "dd if=",
                ":(){ :|:& };:", "> /dev/sda", "chmod -R 777 /",
            ],
            "ask_keywords": [
                "rm", "kill", "pkill",
                "shutdown", "reboot", "poweroff",
                "docker rm", "docker rmi",
                "git push --force", "git reset --hard",
            ],
            "never_ask": [],
        }
        if self.rules_file.exists():
            try:
                stored = json.loads(self.rules_file.read_text())
                default.update(stored)
            except Exception:
                pass
        return default

    def _save_rules(self):
        self.rules_file.write_text(json.dumps(self.rules, indent=2, ensure_ascii=False))

    def evaluate(self, command: str) -> tuple[str, str]:
        cmd_lower = command.lower().strip()
        for allowed in self.rules["never_ask"]:
            if allowed in command:
                return "allow", f"已在信任列表: {allowed}"
        for keyword in self.rules["deny_keywords"]:
            if keyword in cmd_lower:
                return "deny", f"拒绝: 包含禁止关键词 '{keyword}'"
        for keyword in self.rules["ask_keywords"]:
            if keyword in cmd_lower:
                return "ask", f"需要确认: 该命令包含敏感操作 '{keyword}'"
        return "allow", ""

    def allow_future(self, command: str):
        self.rules["never_ask"].append(command)
        self._save_rules()


PERM = PermissionEngine()


# ═══════════════════════════════════════════════
# Ch03: System Prompt
# ═══════════════════════════════════════════════

class PromptModule:
    def render(self) -> str:
        raise NotImplementedError

class RoleModule(PromptModule):
    def render(self) -> str:
        return """你是 CAgent，一个多功能 AI 助手。你有权使用各种工具来帮助用户解决问题。"""

class ToolGuideModule(PromptModule):
    def render(self) -> str:
        if not ToolRegistry.list_tools():
            return ""
        lines = ["## 可用工具"]
        for tool in ToolRegistry._tools.values():
            lines.append(f"- `{tool.name}`: {tool.description}")
        lines.append("遇到问题时，从以上工具中选择合适的调用。")
        return "\n".join(lines)

class OutputFormatModule(PromptModule):
    def render(self) -> str:
        return "## 回答要求\n- 简洁、准确\n- 给出具体数据\n- 调用完所有工具后给出最终总结"

class SystemPrompt:
    def __init__(self):
        self.modules = [RoleModule(), ToolGuideModule(), OutputFormatModule()]

    def render(self) -> str:
        parts = [m.render() for m in self.modules if m.render()]
        return "\n\n".join(parts)


# ═══════════════════════════════════════════════
# Ch04: Shell 安全
# ═══════════════════════════════════════════════

BLOCKED_KEYWORDS = [
    "rm -rf /", "rm -rf /*", "mkfs", "dd if=",
    ":(){ :|:& };:", "> /dev/sda", "chmod -R 777 /",
    "wget ", "curl ", "nc ", "ncat ", "sudo ",
]
ALLOWED_DIRS = [Path("/root/CAgent"), Path("/root"), Path("/tmp")]
SHELL_TIMEOUT = 15
MAX_OUTPUT_CHARS = 2000

def validate_command(command: str) -> tuple[bool, str]:
    cmd_lower = command.lower().strip()
    for keyword in BLOCKED_KEYWORDS:
        if keyword in cmd_lower:
            return False, f"危险命令被拒绝: 包含禁止的关键词 '{keyword}'"
    if not command.strip():
        return False, "命令不能为空"
    return True, ""

def _is_subpath(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False

def run_shell_impl(command: str, workdir: str = ".") -> str:
    ok, reason = validate_command(command)
    if not ok:
        return f"[安全拒绝] {reason}"
    decision, reason = PERM.evaluate(command)
    if decision == "deny":
        return f"[权限拒绝] {reason}"
    if decision == "ask":
        return f"[需确认] {reason}\n命令: {command}\n如需执行，回复: 允许执行 {command}"
    exec_dir = Path(workdir).resolve()
    allowed = any(_is_subpath(exec_dir, d) for d in ALLOWED_DIRS)
    if not allowed:
        return f"[安全拒绝] 禁止在 {exec_dir} 目录执行命令"
    try:
        result = subprocess.run(command, shell=True, cwd=exec_dir,
                                capture_output=True, text=True, timeout=SHELL_TIMEOUT)
        output = result.stdout + result.stderr
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + f"\n...（截断，共 {len(output)} 字符）"
        return f"exit code: {result.returncode}\n{output}"
    except subprocess.TimeoutExpired:
        return f"[超时] 命令超过 {SHELL_TIMEOUT} 秒"
    except Exception as e:
        return f"[执行错误] {e}"


# ═══════════════════════════════════════════════
# Ch02: 工具系统
# ═══════════════════════════════════════════════

class ToolRegistry:
    _tools: dict[str, "ToolBase"] = {}
    @classmethod
    def register(cls, tool: "ToolBase"):
        cls._tools[tool.name] = tool
    @classmethod
    def get_openai_tools(cls) -> list[dict]:
        return [t.openai_schema() for t in cls._tools.values()]
    @classmethod
    def execute(cls, name: str, **kwargs) -> str:
        tool = cls._tools.get(name)
        return tool(**kwargs) if tool else f"错误: 未知工具 '{name}'"
    @classmethod
    def list_tools(cls) -> list[str]:
        return list(cls._tools.keys())

class ToolBase:
    name: str = ""
    description: str = ""
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.name:
            ToolRegistry.register(cls())
    def openai_schema(self) -> dict:
        sig = inspect.signature(self.run)
        doc = inspect.getdoc(self.run) or ""
        properties, required = {}, []
        for p_name, p_param in sig.parameters.items():
            if p_name == "self": continue
            required.append(p_name)
            type_map = {int: "number", float: "number", str: "string", bool: "boolean"}
            json_type = type_map.get(p_param.annotation, "string")
            param_desc = ""
            for line in doc.split("\n"):
                line = line.strip()
                if line.startswith(f"{p_name}:"):
                    param_desc = line.split(":", 1)[1].strip()
                    break
            properties[p_name] = {"type": json_type, "description": param_desc or f"参数 {p_name}"}
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        }}
    def run(self, **kwargs) -> str:
        raise NotImplementedError
    def __call__(self, **kwargs) -> str:
        return self.run(**kwargs)


# ═══════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════

class Calculator(ToolBase):
    name = "calculator"
    description = "做四则运算: add, subtract, multiply, divide"
    def run(self, a: float, b: float, op: str) -> str:
        """a: 第一个数\nb: 第二个数\nop: 运算，可选值: add, subtract, multiply, divide"""
        ops = {"add": lambda: a+b, "subtract": lambda: a-b,
               "multiply": lambda: a*b, "divide": lambda: a/b if b!=0 else "除数不能为0"}
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
# Agent 循环（v6: 集成上下文管理）
# ═══════════════════════════════════════════════

def agent_loop(user_input: str) -> str:
    system_prompt = SystemPrompt().render()

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input},
    ]
    for m in messages:
        CTX.add_message(m)

    tools = ToolRegistry.get_openai_tools()
    print(f"📦 已注册工具: {ToolRegistry.list_tools()}")
    print(f"📊 {CTX.get_usage_report()}")

    for turn in range(1, MAX_TURNS + 1):
        print(f"\n── 第 {turn} 轮 ──")

        # 每次调用 API 前检查上下文预算
        if CTX.should_compress():
            messages = CTX.compress(messages)
            print(f"  📊 {CTX.get_usage_report()}")

        response = CLIENT.chat.completions.create(
            model=MODEL, messages=messages, tools=tools,
        )
        msg = response.choices[0].message
        messages.append(msg)
        CTX.add_message(msg)

        if not msg.tool_calls:
            print(f"  → 模型回答: {msg.content}")
            print(f"\n📊 {CTX.get_usage_report()}")
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

    print(f"  ⚠ 达到最大轮次 {MAX_TURNS}")
    return "达到最大轮次限制"


# ─── 入口 ─────────────────────────────────────
if __name__ == "__main__":
    prompt = sys.argv[1] if len(sys.argv) > 1 else "今天深圳多少度？顺便帮我算 3.14 * 2.71"
    print(f"🧑 用户: {prompt}")
    result = agent_loop(prompt)
    print(f"\n✅ 最终回答: {result}")
