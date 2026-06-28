#!/usr/bin/env python3
"""
CAgent - 最简 Agent 核心循环  v1
========================================
对照 Claude Code 的 Ch01: Agent 循环

教学版 ~30 行, 加上基础的工程化 ~60 行
"""

import json
import os
import sys
from pathlib import Path
from openai import OpenAI

# ─── 从 .env 加载（如有）─────────────────────────────
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().strip().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.strip().split("=", 1)
            os.environ.setdefault(k, v)

# ─── 配置 ─────────────────────────────────────────────
# 从环境变量读取, 支持 DeepSeek / OpenAI / 任意兼容接口
CLIENT = OpenAI(
    base_url=os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1"),
    api_key=os.environ.get("OPENAI_API_KEY", ""),
)

MODEL = os.environ.get("CA_LLM_MODEL", "deepseek-chat")
MAX_TURNS = 10                  # 最大循环轮次（防止无限循环烧钱）


# ─── 工具定义 ─────────────────────────────────────────
# 这就是 Claude Code 里每个工具需要的 schema
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "做四则运算: add, subtract, multiply, divide",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "number", "description": "第一个数"},
                    "b": {"type": "number", "description": "第二个数"},
                    "op": {
                        "type": "string",
                        "enum": ["add", "subtract", "multiply", "divide"],
                        "description": "运算"
                    }
                },
                "required": ["a", "b", "op"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市的天气",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名"}
                },
                "required": ["city"]
            }
        }
    }
]

# ─── 工具实现（简单模拟）───────────────────────────────
TOOL_HANDLERS = {}

def calculator(a: float, b: float, op: str) -> str:
    ops = {
        "add": lambda: a + b,
        "subtract": lambda: a - b,
        "multiply": lambda: a * b,
        "divide": lambda: a / b if b != 0 else "错误: 除数不能为0",
    }
    result = ops.get(op, lambda: f"未知运算: {op}")()
    return f"{result}"

TOOL_HANDLERS["calculator"] = calculator


def get_weather(city: str) -> str:
    # 模拟天气查询
    mock = {
        "深圳": "26°C, 多云",
        "北京": "22°C, 晴",
        "上海": "24°C, 小雨",
        "悉尼": "18°C, 晴",
    }
    return mock.get(city, f"{city}: 暂时查不到天气数据")

TOOL_HANDLERS["get_weather"] = get_weather


# ─── 核心 Agent 循环 ─────────────────────────────────
def agent_loop(user_input: str) -> str:
    """
    这就是 Claude Code 的 QueryEngine.submitMessage() 的简化版。
    
    循环: 用户输入 → 调 API → 检查 stop_reason → 执行工具 → 继续
    """
    messages = [{"role": "user", "content": user_input}]

    for turn in range(1, MAX_TURNS + 1):
        print(f"\n── 第 {turn} 轮 ──")

        # 1. 调用 LLM API
        response = CLIENT.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
        )

        msg = response.choices[0].message
        messages.append(msg)

        # 2. 检查 stop_reason
        #    - tool_use → 有工具要执行, 继续循环
        #    - end_turn  → 模型给出了最终回答, 结束
        if not msg.tool_calls:
            print(f"  → 模型回答: {msg.content}")
            return msg.content

        # 3. 执行工具
        for tc in msg.tool_calls:
            func_name = tc.function.name
            args = json.loads(tc.function.arguments)

            print(f"  → 调用工具: {func_name}({args})")

            handler = TOOL_HANDLERS.get(func_name)
            if handler is None:
                result = f"错误: 未知工具 '{func_name}'"
            else:
                result = handler(**args)

            print(f"  ← 工具结果: {result}")

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": str(result),
            })

        # 继续循环 → 把工具结果送回给 LLM 再推理

    # 超过最大轮次
    print(f"  ⚠ 达到最大轮次 {MAX_TURNS}, 强制结束")
    return "达到最大轮次限制"


# ─── 入口 ─────────────────────────────────────────────
if __name__ == "__main__":
    prompt = sys.argv[1] if len(sys.argv) > 1 else "今天深圳多少度？顺便帮我算 3.14 * 2.71"
    print(f"🧑 用户: {prompt}")
    result = agent_loop(prompt)
    print(f"\n✅ 最终回答: {result}")
