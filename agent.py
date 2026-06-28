#!/usr/bin/env python3
"""
CAgent v3 — 动态 System Prompt
================================
对照 Claude Code Ch03: Prompt 工程
- SystemPrompt 模块化组装
- 角色定义 / 工具说明 / 输出约束 分模块
- 根据已有工具动态生成工具使用说明
- 保持 v2 的 ToolBase + ToolRegistry 不变
"""

import json
import os
import sys
import inspect
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
    """
    Prompt 模块基类。
    每个模块返回一段文字，最终拼接成完整 System Prompt。
    """

    def render(self) -> str:
        raise NotImplementedError


class RoleModule(PromptModule):
    """角色定义"""

    def render(self) -> str:
        return """你是 CAgent，一个多功能 AI 助手。
你有权使用各种工具来帮助用户解决问题。
在每一步中，你应该分析用户的需求，合理选择工具来完成任务。"""


class ToolGuideModule(PromptModule):
    """工具使用指南——根据当前注册的工具动态生成"""

    def render(self) -> str:
        if not ToolRegistry.list_tools():
            return ""

        lines = ["## 可用工具"]
        for tool in ToolRegistry._tools.values():
            lines.append(f"- `{tool.name}`: {tool.description}")
        lines.append("")
        lines.append("当你需要完成某个任务时，从以上工具中选择合适的工具调用。")
        lines.append("如果一次需要多个操作，可以依次调用多个工具。")
        return "\n".join(lines)


class OutputFormatModule(PromptModule):
    """输出格式约束"""

    def render(self) -> str:
        return """## 回答要求
- 简洁、准确
- 涉及数据时给出具体数值
- 完成所有工具调用后，给出最终总结"""


class SafetyModule(PromptModule):
    """安全约束"""

    def render(self) -> str:
        return """## 安全限制
- 不要执行任何危害系统安全的操作
- 不要尝试读取敏感文件
- 严格按照工具的参数格式进行调用"""


class SystemPrompt:
    """组装完整的 System Prompt from 多个模块"""

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
        parts = [p for p in parts if p]  # 去掉空模块
        return "\n\n".join(parts)


# ═══════════════════════════════════════════════
# Ch02: 工具系统（保持不变，多一行 for enum）
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
        enums = {}  # Ch03 改进: 支持 enum 推断

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


# ═══════════════════════════════════════════════
# Agent 循环（v3 改进：注入 System Prompt）
# ═══════════════════════════════════════════════

def agent_loop(user_input: str) -> str:
    # v3: 动态生成 System Prompt
    system_prompt = SystemPrompt().render()
    print(f"📋 System Prompt:\n{system_prompt}\n")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input},
    ]
    tools = ToolRegistry.get_openai_tools()

    print(f"📦 已注册工具: {ToolRegistry.list_tools()}")
    print(f"📋 工具 schema: {json.dumps(tools, indent=2, ensure_ascii=False)}")

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
            print(f"  ← 结果: {result}")

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": str(result),
            })

    print(f"  ⚠ 达到最大轮次 {MAX_TURNS}, 强制结束")
    return "达到最大轮次限制"


# ─── 入口 ─────────────────────────────────────
if __name__ == "__main__":
    prompt = sys.argv[1] if len(sys.argv) > 1 else "今天深圳多少度？顺便帮我算 3.14 * 2.71"
    print(f"🧑 用户: {prompt}")
    result = agent_loop(prompt)
    print(f"\n✅ 最终回答: {result}")
