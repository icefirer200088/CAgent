#!/usr/bin/env python3
"""
CAgent v8 — 插件生态
================================
对照 Claude Code Ch08: 插件生态
- PluginBase: 插件基类，支持工具注册、System Prompt 注入
- PluginManager: 动态安装/启用/禁用插件
- 内置插件: mcp_loader、time_tool、memory
- 插件可热加载（运行中启用禁用）
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
# Ch08: 插件生态
# ═══════════════════════════════════════════════

class PluginBase:
    """
    插件基类。
    
    每个插件可以:
    1. 注册工具 (override register_tools)
    2. 添加 System Prompt 模块 (override get_prompt_modules)
    3. 初始化/清理 (setup/teardown)
    """

    name: str = ""
    version: str = "1.0.0"
    description: str = ""
    enabled: bool = True

    def setup(self):
        """插件加载时调用"""
        pass

    def teardown(self):
        """插件卸载时调用"""
        pass

    def register_tools(self):
        """注册工具到 ToolRegistry"""
        pass

    def get_prompt_modules(self) -> list:
        """返回要添加到 System Prompt 的 PromptModule 列表"""
        return []


class PluginManager:
    """
    插件管理器。
    从 plugins/ 目录动态加载插件。
    """

    def __init__(self, plugin_dir: str = None):
        if plugin_dir is None:
            plugin_dir = str(Path(__file__).parent / "plugins")
        self.plugin_dir = Path(plugin_dir)
        self.plugin_dir.mkdir(exist_ok=True)
        self.plugins: dict[str, PluginBase] = {}
        self._prompt_modules: list = []

    def discover_and_load(self) -> int:
        """扫描 plugins/ 目录下的所有 .py 文件，加载插件"""
        count = 0
        for py_file in sorted(self.plugin_dir.glob("*.py")):
            if py_file.name == "__init__.py":
                continue
            try:
                plugin = self._load_plugin(py_file)
                if plugin:
                    self.plugins[plugin.name] = plugin
                    count += 1
            except Exception as e:
                print(f"  ⚠ 插件加载失败 [{py_file.name}]: {e}")
        return count

    def _load_plugin(self, path: Path) -> PluginBase | None:
        """加载单个插件文件"""
        spec = importlib.util.spec_from_file_location(f"plugin_{path.stem}", path)
        if not spec or not spec.loader:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # 寻找 PluginBase 子类
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, type) and issubclass(attr, PluginBase) and attr is not PluginBase:
                instance = attr()
                print(f"  🔌 加载插件: {instance.name} v{instance.version}")
                return instance
        return None

    def enable(self, name: str) -> bool:
        """启用一个插件"""
        if name in self.plugins:
            self.plugins[name].enabled = True
            self.plugins[name].setup()
            self.plugins[name].register_tools()
            print(f"  ✅ 插件启用: {name}")
            return True
        return False

    def disable(self, name: str) -> bool:
        """禁用一个插件"""
        if name in self.plugins:
            self.plugins[name].enabled = False
            self.plugins[name].teardown()
            print(f"  ⏹ 插件禁用: {name}")
            return True
        return False

    def activate_all(self):
        """启用所有插件"""
        for name, plugin in self.plugins.items():
            if plugin.enabled:
                plugin.setup()
                plugin.register_tools()
                mods = plugin.get_prompt_modules()
                if mods:
                    self._prompt_modules.extend(mods)

    def get_prompt_modules(self) -> list:
        return self._prompt_modules

    def list_plugins(self) -> list[dict]:
        return [
            {
                "name": p.name,
                "version": p.version,
                "description": p.description,
                "enabled": p.enabled,
            }
            for p in self.plugins.values()
        ]


# 全局插件管理器
PM = PluginManager()


# ─── 内置插件：MCP 加载器 ─────────────────────

MCP_CONFIG_DIR = Path(__file__).parent / "mcp"

class MCPLoaderPlugin(PluginBase):
    name = "mcp_loader"
    version = "1.0.0"
    description = "从 mcp/ 目录加载外部工具"

    def setup(self):
        MCP_CONFIG_DIR.mkdir(exist_ok=True)

    def register_tools(self):
        from pathlib import Path
        for config_file in MCP_CONFIG_DIR.glob("*.mcp.json"):
            try:
                data = json.loads(config_file.read_text())
                for td in data.get("tools", []):
                    wrapper = MCPToolWrapper(
                        name=td["name"],
                        description=td.get("description", ""),
                        parameters=td.get("parameters", {}),
                    )
                    ToolRegistry.register(wrapper)
                    print(f"    └─ MCP 工具: {td['name']}")
            except Exception as e:
                print(f"    ⚠ MCP 配置加载失败: {e}")


# ─── 内置插件：查看时间 ─────────────────────────

class TimeToolPlugin(PluginBase):
    name = "time_tool"
    version = "1.0.0"
    description = "提供 get_time 工具，查询当前日期和时间"

    def register_tools(self):
        ToolRegistry.register(GetTimeTool())


class GetTimeTool:
    name = "get_time"
    description = "查询当前日期和时间"

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }

    def __call__(self, **kwargs) -> str:
        from datetime import datetime
        now = datetime.now()
        return f"当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')} (北京时间, UTC+8)"


# ─── 内置插件：简单记忆 ─────────────────────────

MEMORY_FILE = Path(__file__).parent / ".cagent_memory.json"

class MemoryPlugin(PluginBase):
    name = "memory"
    version = "1.0.0"
    description = "短时记忆：记住用户说过的事实"

    def setup(self):
        if not MEMORY_FILE.exists():
            MEMORY_FILE.write_text("[]")

    def register_tools(self):
        ToolRegistry.register(SaveMemoryTool())
        ToolRegistry.register(RecallMemoryTool())

    def get_prompt_modules(self) -> list:
        return [MemoryPromptModule()]


class SaveMemoryTool:
    name = "save_memory"
    description = "记住一条信息（key:value 格式），以后可以回忆"

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "信息的键名，如 'user_name'"},
                        "value": {"type": "string", "description": "信息的值，如 '张三'"},
                    },
                    "required": ["key", "value"],
                },
            },
        }

    def __call__(self, key: str, value: str) -> str:
        memories = json.loads(MEMORY_FILE.read_text() or "[]")
        memories.append({"key": key, "value": value})
        MEMORY_FILE.write_text(json.dumps(memories, ensure_ascii=False))
        return f"已记住: {key} = {value}"


class RecallMemoryTool:
    name = "recall_memory"
    description = "回忆所有记住的信息"

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }

    def __call__(self, **kwargs) -> str:
        memories = json.loads(MEMORY_FILE.read_text() or "[]")
        if not memories:
            return "还没有记住任何信息"
        lines = [f"- {m['key']}: {m['value']}" for m in memories]
        return "已记住的信息:\n" + "\n".join(lines)


class MemoryPromptModule:
    def render(self) -> str:
        memories = json.loads(MEMORY_FILE.read_text() or "[]")
        if not memories:
            return ""
        lines = [f"- {m['key']}: {m['value']}" for m in memories]
        return f"## 我已记住的上下文\n{chr(10).join(lines)}"


# ═══════════════════════════════════════════════
# Ch07: MCP 工具包装
# ═══════════════════════════════════════════════

class MCPToolWrapper:
    def __init__(self, name: str, description: str, parameters: dict):
        self.name = name
        self.description = description
        self._params = parameters

    def openai_schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": self._params,
        }}

    def __call__(self, **kwargs) -> str:
        return f"[外部工具 {self.name}] 收到参数: {kwargs}"


# ═══════════════════════════════════════════════
# Ch06: 上下文管理
# ═══════════════════════════════════════════════

class ContextManager:
    CHARS_PER_TOKEN = float(3) / float(2)  # = 1.5

    def __init__(self, max_tokens: int = 8192):
        self.max_tokens = max_tokens
        self.token_counts = []
        self.compression_count = 0

    def estimate_tokens(self, text: str) -> int:
        if not text: return 0
        return int(len(text) / self.CHARS_PER_TOKEN) + 1

    def estimate_message_tokens(self, msg) -> int:
        total = 0
        raw = msg if isinstance(msg, dict) else msg.model_dump()
        for key in ("content", "role", "name"):
            val = raw.get(key)
            if isinstance(val, str): total += self.estimate_tokens(val)
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        for sv in item.values():
                            if isinstance(sv, str): total += self.estimate_tokens(sv)
        return total + 4

    def add_message(self, msg):
        self.token_counts.append(self.estimate_message_tokens(msg))

    def total_used(self) -> int: return sum(self.token_counts)
    def should_compress(self) -> bool: return self.total_used() > self.max_tokens

    def compress(self, messages: list) -> list:
        self.compression_count += 1
        print(f"  ⚡ 上下文压缩 #{self.compression_count}")
        if len(messages) <= 2: return messages
        system_msg = messages[0] if messages[0]["role"] == "system" else None
        if system_msg: messages = messages[1:]
        recent = messages[-7:] if len(messages) > 7 else messages
        old = messages[:-7] if len(messages) > 7 else []
        if not old:
            result = [system_msg] if system_msg else []; result.extend(messages); return result
        compressed = []
        for m in old:
            if m["role"] == "tool" and len(m.get("content", "")) > 100:
                c = m["content"]
                compressed.append({**m, "content": c[:60] + f"...({c.count(chr(10))} 行)"})
            else: compressed.append(m)
        if len(compressed) > 3:
            compressed = [{"role": "system", "content": "[已压缩旧消息]"}]
        result = [system_msg] if system_msg else []
        result.extend(compressed); result.extend(recent)
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
        d = {"deny_keywords": ["rm -rf /","rm -rf /*","mkfs","dd if=",":(){ :|:& };:","> /dev/sda","chmod -R 777 /"],
             "ask_keywords": ["rm","kill","pkill","shutdown","reboot","poweroff","docker rm","docker rmi","git push --force","git reset --hard"],
             "never_ask": []}
        if self.rules_file.exists():
            try: d.update(json.loads(self.rules_file.read_text()))
            except: pass
        return d
    def _save(self): self.rules_file.write_text(json.dumps(self.rules, indent=2, ensure_ascii=False))
    def evaluate(self, command: str):
        cl = command.lower().strip()
        for a in self.rules["never_ask"]:
            if a in command: return "allow", f"信任: {a}"
        for k in self.rules["deny_keywords"]:
            if k in cl: return "deny", f"拒绝: {k}"
        for k in self.rules["ask_keywords"]:
            if k in cl: return "ask", f"需确认: {k}"
        return "allow", ""
    def allow_future(self, command: str): self.rules["never_ask"].append(command); self._save()

PERM = PermissionEngine()


# ═══════════════════════════════════════════════
# Ch03: System Prompt
# ═══════════════════════════════════════════════

class PromptModule:
    def render(self) -> str: raise NotImplementedError

class RoleModule(PromptModule):
    def render(self) -> str: return "你是 CAgent，一个多功能 AI 助手。你有权使用各种工具来帮助用户解决问题。"

class ToolGuideModule(PromptModule):
    def render(self) -> str:
        if not ToolRegistry.list_tools(): return ""
        lines = ["## 可用工具"]
        for t in ToolRegistry._tools.values(): lines.append(f"- `{t.name}`: {t.description}")
        lines.append("遇到问题时，从以上工具中选择合适的调用。")
        return "\n".join(lines)

class OutputFormatModule(PromptModule):
    def render(self) -> str: return "## 回答要求\n- 简洁、准确\n- 给出具体数据\n- 调用完所有工具后给出最终总结"

class SystemPrompt:
    def __init__(self):
        self.modules = [RoleModule(), ToolGuideModule(), OutputFormatModule()]
    def add_modules(self, mods: list): self.modules.extend(mods)
    def render(self) -> str:
        parts = [m.render() for m in self.modules if m.render()]
        return "\n\n".join(parts)


# ═══════════════════════════════════════════════
# Ch04: Shell 安全
# ═══════════════════════════════════════════════

BLOCKED_KEYWORDS = ["rm -rf /","rm -rf /*","mkfs","dd if=",":(){ :|:& };:","> /dev/sda","chmod -R 777 /","wget ","curl ","nc ","ncat ","sudo "]
ALLOWED_DIRS = [Path("/root/CAgent"), Path("/root"), Path("/tmp")]
SHELL_TIMEOUT = 15; MAX_OUTPUT_CHARS = 2000

def validate_command(cmd: str):
    cl = cmd.lower().strip()
    for k in BLOCKED_KEYWORDS:
        if k in cl: return False, f"危险命令被拒绝: {k}"
    if not cmd.strip(): return False, "命令不能为空"
    return True, ""

def _is_subpath(path, parent):
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
    _tools = {}
    @classmethod
    def register(cls, tool): cls._tools[tool.name] = tool
    @classmethod
    def get_openai_tools(cls) -> list: return [t.openai_schema() for t in cls._tools.values()]
    @classmethod
    def execute(cls, name: str, **kwargs) -> str:
        t = cls._tools.get(name)
        return t(**kwargs) if t else f"错误: 未知工具 '{name}'"
    @classmethod
    def list_tools(cls) -> list: return list(cls._tools.keys())

class ToolBase:
    name: str = ""
    description: str = ""
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.name: ToolRegistry.register(cls())
    def openai_schema(self) -> dict:
        sig = inspect.signature(self.run); doc = inspect.getdoc(self.run) or ""
        props, req = {}, []
        for pn, pp in sig.parameters.items():
            if pn == "self": continue; req.append(pn)
            tm = {int:"number",float:"number",str:"string",bool:"boolean"}
            jt = tm.get(pp.annotation,"string"); pd = ""
            for line in doc.split("\n"):
                line = line.strip()
                if line.startswith(f"{pn}:"): pd = line.split(":",1)[1].strip(); break
            props[pn] = {"type":jt,"description":pd or f"参数 {pn}"}
        return {"type":"function","function":{"name":self.name,"description":self.description,
                "parameters":{"type":"object","properties":props,"required":req}}}
    def run(self, **kwargs) -> str: raise NotImplementedError
    def __call__(self, **kwargs) -> str: return self.run(**kwargs)


# ═══════════════════════════════════════════════
# 内置工具
# ═══════════════════════════════════════════════

class Calculator(ToolBase):
    name = "calculator"
    description = "做四则运算: add, subtract, multiply, divide"
    def run(self, a: float, b: float, op: str) -> str:
        """a: 第一个数\nb: 第二个数\nop: 运算"""
        ops = {"add":lambda:a+b,"subtract":lambda:a-b,"multiply":lambda:a*b,"divide":lambda:a/b if b!=0 else"除数不能为0"}
        return str(ops.get(op,lambda:f"未知运算: {op}")())

class GetWeather(ToolBase):
    name = "get_weather"
    description = "查询指定城市的天气"
    def run(self, city: str) -> str:
        """city: 城市名"""
        m = {"深圳":"26°C,多云","北京":"22°C,晴","上海":"24°C,小雨","悉尼":"18°C,晴"}
        return m.get(city,f"{city}: 暂时查不到天气数据")

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
    sp = SystemPrompt()
    sp.add_modules(PM.get_prompt_modules())
    system_prompt = sp.render()

    messages = [{"role":"system","content":system_prompt},{"role":"user","content":user_input}]
    for m in messages: CTX.add_message(m)
    tools = ToolRegistry.get_openai_tools()
    print(f"📦 已注册工具: {ToolRegistry.list_tools()}")
    print(f"📊 {CTX.get_usage_report()}")

    for turn in range(1, MAX_TURNS+1):
        print(f"\n── 第 {turn} 轮 ──")
        if CTX.should_compress(): messages = CTX.compress(messages)
        response = CLIENT.chat.completions.create(model=MODEL, messages=messages, tools=tools)
        msg = response.choices[0].message
        messages.append(msg); CTX.add_message(msg)
        if not msg.tool_calls:
            print(f"  → 模型回答: {msg.content}"); return msg.content
        for tc in msg.tool_calls:
            fn, args = tc.function.name, json.loads(tc.function.arguments)
            print(f"  → 调用工具: {fn}({args})")
            result = ToolRegistry.execute(fn, **args)
            print(f"  ← 结果: {result[:100]}...")
            tm = {"role":"tool","tool_call_id":tc.id,"content":str(result)}
            messages.append(tm); CTX.add_message(tm)
    return "达到最大轮次限制"


# ─── 入口 ─────────────────────────────────────
if __name__ == "__main__":
    # 1. 扫描并加载插件
    count = PM.discover_and_load()
    if count == 0:
        # 没有外部插件，启用内置插件
        PM.plugins["mcp_loader"] = MCPLoaderPlugin()
        PM.plugins["time_tool"] = TimeToolPlugin()
        PM.plugins["memory"] = MemoryPlugin()
        print(f"  🔌 加载 {len(PM.plugins)} 个内置插件")

    # 2. 激活所有插件
    PM.activate_all()
    print(f"\n📋 插件列表: {PM.list_plugins()}")

    prompt = sys.argv[1] if len(sys.argv) > 1 else "今天深圳多少度？顺便帮我算 3.14 * 2.71"
    print(f"🧑 用户: {prompt}")
    result = agent_loop(prompt)
    print(f"\n✅ 最终回答: {result}")
