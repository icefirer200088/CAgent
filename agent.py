#!/usr/bin/env python3
"""
CAgent v5 — 权限引擎
===============================
对照 Claude Code Ch05: 权限引擎
- allow/deny/ask 三级权限规则
- 执行危险操作前询问用户
- 规则持久化（.cagent_rules.json）
- run_shell 集成权限检查
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
MAX_TURNS = 10
RULES_FILE = Path(__file__).parent / ".cagent_rules.json"


# ═══════════════════════════════════════════════
# Ch05: 权限引擎
# ═══════════════════════════════════════════════

class PermissionEngine:
    """
    三级权限引擎，仿 Claude Code 的 PermissionEngine。
    
    对每条命令，判断策略：
      - allow: 直接执行（不需要询问）
      - deny:  直接拒绝
      - ask:   输出 TODO 标记，由外层处理（询问用户）
    """

    def __init__(self, rules_file: Path = RULES_FILE):
        self.rules_file = rules_file
        self.rules = self._load_rules()

    def _load_rules(self) -> dict:
        """加载持久化规则"""
        default = {
            "allow_keywords": [],   # 包含这些词的命令直接允许
            "deny_keywords": [      # 包含这些词的命令直接拒绝（比黑名单更灵活）
                "rm -rf /", "rm -rf /*",
                "mkfs", "dd if=",
                ":(){ :|:& };:",
                "> /dev/sda",
                "chmod -R 777 /",
            ],
            "ask_keywords": [       # 包含这些词的命令需要询问用户
                "rm", "kill", "pkill",
                "shutdown", "reboot", "poweroff",
                "docker rm", "docker rmi",
                "git push --force", "git reset --hard",
            ],
            "never_ask": [],        # 用户说过"以后都允许"的完整命令
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
        """
        返回 (decision, reason)
        decision: "allow" | "deny" | "ask"
        """
        cmd_lower = command.lower().strip()

        # 1. never_ask 优先——用户说过"以后都直接执行"
        for allowed in self.rules["never_ask"]:
            if allowed in command:
                return "allow", f"已在信任列表: {allowed}"

        # 2. deny 检查
        for keyword in self.rules["deny_keywords"]:
            if keyword in cmd_lower:
                return "deny", f"拒绝: 包含禁止关键词 '{keyword}'"

        # 3. ask 检查（需要用户确认的操作）
        for keyword in self.rules["ask_keywords"]:
            if keyword in cmd_lower:
                return "ask", f"需要确认: 该命令包含敏感操作 '{keyword}'"

        # 4. 默认允许
        return "allow", ""

    def allow_future(self, command: str):
        """用户允许某条命令以后不再询问"""
        self.rules["never_ask"].append(command)
        self._save_rules()

    def add_ask_rule(self, keyword: str):
        """添加新的 ask 关键词"""
        if keyword not in self.rules["ask_keywords"]:
            self.rules["ask_keywords"].append(keyword)
            self._save_rules()

    def add_deny_rule(self, keyword: str):
        """添加新的 deny 关键词"""
        if keyword not in self.rules["deny_keywords"]:
            self.rules["deny_keywords"].append(keyword)
            self._save_rules()


# 全局权限引擎实例
PERM = PermissionEngine()


# ═══════════════════════════════════════════════
# Ch03: System Prompt
# ═══════════════════════════════════════════════

class PromptModule:
    def render(self) -> str:
        raise NotImplementedError


class RoleModule(PromptModule):
    def render(self) -> str:
        return """你是 CAgent，一个多功能 AI 助手。
你有权使用各种工具来帮助用户解决问题。
在每一步中，你应该分析用户的需求，合理选择工具来完成任务。"""


class ToolGuideModule(PromptModule):
    def render(self) -> str:
        if not ToolRegistry.list_tools():
            return ""
        lines = ["## 可用工具"]
        for tool in ToolRegistry._tools.values():
            lines.append(f"- `{tool.name}`: {tool.description}")
        lines.append("")
        lines.append("当你需要完成某个任务时，从以上工具中选择合适的工具调用。")
        return "\n".join(lines)


class OutputFormatModule(PromptModule):
    def render(self) -> str:
        return """## 回答要求
- 简洁、准确
- 涉及数据时给出具体数值
- 完成所有工具调用后，给出最终总结"""


class SafetyModule(PromptModule):
    def render(self) -> str:
        return """## 安全限制
- 不要执行任何危害系统安全的操作
- 不要尝试读取敏感文件
- 严格按照工具的参数格式进行调用"""


class SystemPrompt:
    modules: list[PromptModule] = []

    def __init__(self):
        self.modules = [
            RoleModule(),
            ToolGuideModule(),
            OutputFormatModule(),
            SafetyModule(),
        ]

    def add_module(self, module: PromptModule):
        self.modules.append(module)

    def render(self) -> str:
        parts = [m.render() for m in self.modules]
        parts = [p for p in parts if p]
        return "\n\n".join(parts)


# ═══════════════════════════════════════════════
# Ch04: Shell 安全
# ═══════════════════════════════════════════════

BLOCKED_KEYWORDS = [
    "rm -rf /", "rm -rf /*",
    "mkfs", "dd if=", "format ",
    ":(){ :|:& };:",
    "> /dev/sda", "> /dev/nvme",
    "chmod 777 /", "chmod -R 777 /",
    "wget ", "curl ",
    "nc ", "ncat ",
    "sudo ",
]

ALLOWED_DIRS = [
    Path("/root/CAgent"),
    Path("/root"),
    Path("/tmp"),
]

SHELL_TIMEOUT = 15
MAX_OUTPUT_CHARS = 2000


def validate_command(command: str) -> tuple[bool, str]:
    cmd_lower = command.lower().strip()
    for keyword in BLOCKED_KEYWORDS:
        if keyword in cmd_lower:
            return False, f"危险命令被拒绝: 包含禁止的关键词 '{keyword}'"
    if not command.strip():
        return False, "命令不能为空"
    for sep in ["&&", "||", ";"]:
        parts = command.split(sep)
        if len(parts) > 1:
            for part in parts:
                ok, msg = validate_command(part.strip())
                if not ok:
                    return False, f"多段命令中发现危险操作: {msg}"
    return True, ""


def run_shell_impl(command: str, workdir: str = ".") -> str:
    # 安全检查
    ok, reason = validate_command(command)
    if not ok:
        return f"[安全拒绝] {reason}"

    # 权限检查
    decision, reason = PERM.evaluate(command)
    if decision == "deny":
        return f"[权限拒绝] {reason}"
    if decision == "ask":
        return f"[需确认] {reason}\n命令: {command}\n如需执行，请显式要求：'允许执行 {command}'"

    # 路径校验
    exec_dir = Path(workdir).resolve()
    allowed = any(
        True
        for d in ALLOWED_DIRS
        if _is_subpath(exec_dir, d)
    )
    if not allowed:
        return f"[安全拒绝] 禁止在 {exec_dir} 目录执行命令"

    # 执行
    try:
        result = subprocess.run(
            command, shell=True, cwd=exec_dir,
            capture_output=True, text=True, timeout=SHELL_TIMEOUT,
        )
        output = result.stdout + result.stderr
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + f"\n...（截断，共 {len(output)} 字符）"
        return f"exit code: {result.returncode}\n{output}"
    except subprocess.TimeoutExpired:
        return f"[超时] 命令超过 {SHELL_TIMEOUT} 秒"
    except Exception as e:
        return f"[执行错误] {e}"


def _is_subpath(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


# Ch05: 用户确认处理——如果 run_shell 返回 [需确认]，尝试用 LLM 重试
def handle_permission_decision(
    command: str,
    tool_call_id: str,
    messages: list,
) -> bool:
    """
    当 run_shell 返回 [需确认] 时，由外层 LLM 判断
    是否要执行（通过追加一条 user 消息模拟询问）。
    
    简化实现：LLM 发现返回了 [需确认]，它会自己决定
    是否告知用户/改命令/请求执行。
    这里不做真实交互——后续可扩展为暂停 Agent 循环等用户输入。
    """
    # 在消息中追加一条 tool 结果即可，LLM 会自己推理
    return True


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
        if tool is None:
            return f"错误: 未知工具 '{name}'"
        return tool(**kwargs)

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
        properties = {}
        required = []
        for p_name, p_param in sig.parameters.items():
            if p_name == "self":
                continue
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
        return {
            "type": "function",
            "function": {
                "name": self.name, "description": self.description,
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        }

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
        ops = {"add": lambda: a + b, "subtract": lambda: a - b,
               "multiply": lambda: a * b,
               "divide": lambda: a / b if b != 0 else "错误: 除数不能为0"}
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
    description = "在安全环境中执行 Shell 命令（有安全过滤、权限引擎和超时保护）"
    def run(self, command: str, workdir: str = ".") -> str:
        """
        command: 要执行的 Shell 命令
        workdir: 执行目录，默认当前目录
        """
        return run_shell_impl(command, workdir)


# ═══════════════════════════════════════════════
# Agent 循环
# ═══════════════════════════════════════════════

def agent_loop(user_input: str) -> str:
    system_prompt = SystemPrompt().render()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input},
    ]
    tools = ToolRegistry.get_openai_tools()

    print(f"📦 已注册工具: {ToolRegistry.list_tools()}")

    for turn in range(1, MAX_TURNS + 1):
        print(f"\n── 第 {turn} 轮 ──")

        response = CLIENT.chat.completions.create(
            model=MODEL, messages=messages, tools=tools,
        )
        msg = response.choices[0].message
        messages.append(msg)

        if not msg.tool_calls:
            print(f"  → 模型回答: {msg.content}")
            return msg.content

        for tc in msg.tool_calls:
            func_name = tc.function.name
            args = json.loads(tc.function.arguments)
            print(f"  → 调用工具: {func_name}({args})")

            result = ToolRegistry.execute(func_name, **args)
            print(f"  ← 结果: {result[:200]}...")

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": str(result),
            })

    print(f"  ⚠ 达到最大轮次 {MAX_TURNS}, 强制结束")
    return "达到最大轮次限制"


# ─── 入口 ─────────────────────────────────────
if __name__ == "__main__":
    prompt = sys.argv[1] if len(sys.argv) > 1 else "帮我看看当前目录有没有日志文件？"
    print(f"🧑 用户: {prompt}")
    result = agent_loop(prompt)
    print(f"\n✅ 最终回答: {result}")
