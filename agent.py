#!/usr/bin/env python3
"""
CAgent v4 — Shell 安全执行
================================
对照 Claude Code Ch04: Shell 安全
- run_shell 工具：沙箱执行 Shell 命令
- 命令黑名单：禁止 rm -rf / 等危险操作
- 超时保护：防止死循环
- 输出截断：防止刷爆上下文
- 路径校验：限制执行目录
"""

import json
import os
import sys
import inspect
import subprocess
import time
import shlex
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


# ═══════════════════════════════════════════════
# Ch03: System Prompt 工程
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
# Ch04: Shell 安全执行
# ═══════════════════════════════════════════════

# 命令黑名单 — 包含任意一个词就拒绝执行
BLOCKED_KEYWORDS = [
    "rm -rf /", "rm -rf /*",
    "mkfs", "dd if=", "format ",
    ":(){ :|:& };:",  # fork bomb
    "> /dev/sda", "> /dev/nvme",
    "chmod 777 /", "chmod -R 777 /",
    "wget ", "curl ",    # 禁止下载未知内容
    "nc ", "ncat ",       # 禁止反弹 shell
    "sudo ",              # 禁止提权
]

# 允许执行的目录白名单（安全基线，子目录自动允许）
ALLOWED_DIRS = [
    Path("/root/CAgent"),
    Path("/root"),
    Path("/tmp"),
]

SHELL_TIMEOUT = 15       # 单条命令最多 15 秒
MAX_OUTPUT_CHARS = 2000  # 输出最多截断到 2000 字符


def validate_command(command: str) -> tuple[bool, str]:
    """
    安全检查：
    1. 命令黑名单
    2. 危险模式检测
    """
    cmd_lower = command.lower().strip()

    # 黑名单匹配
    for keyword in BLOCKED_KEYWORDS:
        if keyword in cmd_lower:
            return False, f"危险命令被拒绝: 包含禁止的关键词 '{keyword}'"

    # 空命令
    if not command.strip():
        return False, "命令不能为空"

    # 检查是否有 && 或 ; 链接的危险命令
    # (简化版：仅检查最明显的风险)
    for sep in ["&&", "||", ";"]:
        parts = command.split(sep)
        if len(parts) > 1:
            for part in parts:
                ok, msg = validate_command(part.strip())
                if not ok:
                    return False, f"多段命令中发现危险操作: {msg}"

    return True, ""


def run_shell_impl(command: str, workdir: str = ".") -> str:
    """
    在安全沙箱中执行命令。
    command: 要执行的 Shell 命令
    workdir: 执行目录（默认当前目录）
    """
    # 1. 安全检查
    ok, reason = validate_command(command)
    if not ok:
        return f"[安全拒绝] {reason}"

    # 2. 路径校验
    exec_dir = Path(workdir).resolve()
    allowed = False
    for allowed_dir in ALLOWED_DIRS:
        try:
            exec_dir.relative_to(allowed_dir)
            allowed = True
            break
        except ValueError:
            continue
    if not allowed:
        return f"[安全拒绝] 禁止在 {exec_dir} 目录执行命令（允许: {[str(d) for d in ALLOWED_DIRS]}）"

    # 3. 执行（带超时和输出截断）
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=exec_dir,
            capture_output=True,
            text=True,
            timeout=SHELL_TIMEOUT,
        )

        output = result.stdout + result.stderr
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + f"\n...（输出截断，共 {len(output)} 字符）"

        exit_code = result.returncode
        return f"exit code: {exit_code}\n{output}"

    except subprocess.TimeoutExpired:
        return f"[超时] 命令执行超过 {SHELL_TIMEOUT} 秒，已终止"
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

            type_map = {
                int: "number",
                float: "number",
                str: "string",
                bool: "boolean",
            }
            json_type = type_map.get(p_param.annotation, "string")

            param_desc = ""
            for line in doc.split("\n"):
                line = line.strip()
                if line.startswith(f"{p_name}:"):
                    param_desc = line.split(":", 1)[1].strip()
                    break

            prop = {"type": json_type, "description": param_desc or f"参数 {p_name}"}
            properties[p_name] = prop

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    def run(self, **kwargs) -> str:
        raise NotImplementedError

    def __call__(self, **kwargs) -> str:
        return self.run(**kwargs)


# ═══════════════════════════════════════════════
# 工具实现
# ═══════════════════════════════════════════════

class Calculator(ToolBase):
    name = "calculator"
    description = "做四则运算: add, subtract, multiply, divide"

    def run(self, a: float, b: float, op: str) -> str:
        """
        a: 第一个数
        b: 第二个数
        op: 运算，可选值: add, subtract, multiply, divide
        """
        ops = {
            "add": lambda: a + b,
            "subtract": lambda: a - b,
            "multiply": lambda: a * b,
            "divide": lambda: a / b if b != 0 else "错误: 除数不能为0",
        }
        result = ops.get(op, lambda: f"未知运算: {op}")()
        return str(result)


class GetWeather(ToolBase):
    name = "get_weather"
    description = "查询指定城市的天气"

    def run(self, city: str) -> str:
        """
        city: 城市名
        """
        mock = {
            "深圳": "26°C, 多云",
            "北京": "22°C, 晴",
            "上海": "24°C, 小雨",
            "悉尼": "18°C, 晴",
        }
        return mock.get(city, f"{city}: 暂时查不到天气数据")


class RunShell(ToolBase):
    name = "run_shell"
    description = "在安全环境中执行 Shell 命令（有安全过滤、超时保护和输出截断）"

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
            model=MODEL,
            messages=messages,
            tools=tools,
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
    prompt = sys.argv[1] if len(sys.argv) > 1 else "帮我看看当前目录有什么文件？再算一下 256 * 8"
    print(f"🧑 用户: {prompt}")
    result = agent_loop(prompt)
    print(f"\n✅ 最终回答: {result}")
