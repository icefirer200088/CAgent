#!/usr/bin/env python3
"""
CAgent v2 — 工具注册式架构
=============================
对照 Claude Code Ch02: 工具系统
- ToolBase 基类 + 自动 schema 生成
- ToolRegistry 自动收集/调用
- 保持 v1 的 Agent 循环不变
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
# Ch02: 工具系统
# ═══════════════════════════════════════════════

class ToolRegistry:
    """全局工具注册表——自动收集所有 Tool 子类"""

    _tools: dict[str, "ToolBase"] = {}

    @classmethod
    def register(cls, tool: "ToolBase"):
        cls._tools[tool.name] = tool

    @classmethod
    def get_openai_tools(cls) -> list[dict]:
        """生成 OpenAI 所需的 tools 参数列表"""
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
    """
    工具基类。
    子类只需定义:
      name        — 工具名
      description — 描述 (LLM 靠这个匹配)
      run()       — 实际执行逻辑
    
    openai_schema() 和注册逻辑自动完成。
    """

    name: str = ""
    description: str = ""

    def __init_subclass__(cls, **kwargs):
        """自动注册所有非抽象子类"""
        super().__init_subclass__(**kwargs)
        # 跳过 ToolBase 本身（如果用 ABC 则更优雅，但为了简洁用 name 判断）
        if cls.name:
            ToolRegistry.register(cls())

    def openai_schema(self) -> dict:
        """从 run() 的签名和文档自动生成 OpenAI tool schema"""
        sig = inspect.signature(self.run)
        doc = inspect.getdoc(self.run) or ""

        properties = {}
        required = []

        for p_name, p_param in sig.parameters.items():
            if p_name == "self":
                continue
            required.append(p_name)

            # 类型: 支持 int, float, str, bool
            type_map = {
                int: "number",
                float: "number",
                str: "string",
                bool: "boolean",
            }
            json_type = type_map.get(p_param.annotation, "string")

            # 从文档提取参数说明（"a: 第一个数" 格式）
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
        """子类实现——实际执行逻辑"""
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
        op: 运算
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
# Agent 循环（与 v1 相同，只是调注册表）
# ═══════════════════════════════════════════════

def agent_loop(user_input: str) -> str:
    messages = [{"role": "user", "content": user_input}]
    tools = ToolRegistry.get_openai_tools()

    print(f"📦 已注册工具: {ToolRegistry.list_tools()}")
    print(f"📋 生成 schema: {json.dumps(tools, indent=2, ensure_ascii=False)}")

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
