#!/usr/bin/env python3
"""
CAgent v10 — CLI 传输层
================================
对照 Claude Code Ch10: CLI 传输层
- 多模式入口: --repl / --interactive / --serve / 直接 query
- Transport 抽象接口
- REPLTransport: 交互式命令行循环
- InteractiveTransport: 增强版 TUI 交互（带颜色、历史）
- SSETransport: Server-Sent Events 流式输出
- 向后兼容: python3 agent.py "query" 行为不变
"""

import json
import os
import sys
import inspect
import subprocess
import threading
import queue
import select
import time
import shutil
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
# Ch10: CLI 传输层
# ═══════════════════════════════════════════════

class Transport:
    """
    传输层抽象接口。
    定义 Agent 与用户之间如何交换消息——终端、HTTP、SDK 等。
    """
    def send(self, text: str):
        """向用户发送文本"""
        raise NotImplementedError
    def send_stream(self, text: str):
        """向用户发送流式文本片段（可选实现）"""
        self.send(text)
    def send_tool_start(self, name: str, args: dict):
        """通知用户工具开始调用"""
        pass
    def send_tool_result(self, name: str, result: str):
        """通知用户工具返回结果"""
        pass
    def send_error(self, msg: str):
        """向用户发送错误信息"""
        self.send(f"[错误] {msg}")
    def receive(self) -> str:
        """从用户接收一条消息"""
        raise NotImplementedError
    def running(self) -> bool:
        """传输层是否仍在运行"""
        return True


class REPLTransport(Transport):
    """
    基础 REPL 传输层。
    终端交互模式，支持多轮对话。
    """
    def __init__(self):
        self._active = True

    def send(self, text: str):
        print(f"\n🤖 {text}")

    def send_stream(self, text: str):
        print(text, end="", flush=True)

    def send_tool_start(self, name: str, args: dict):
        print(f"\n  🔧 {name}({json.dumps(args, ensure_ascii=False)[:80]})", end="", flush=True)

    def send_tool_result(self, name: str, result: str):
        print(f" → {result[:60]}...", flush=True)

    def send_error(self, msg: str):
        print(f"\n⚠️  {msg}", file=sys.stderr)

    def receive(self) -> str:
        try:
            text = input("\n🧑 ").strip()
            if text.lower() in ("/quit", "/exit", "/q"):
                self._active = False
                return ""
            if text.lower() == "/help":
                print("  /quit /exit /q — 退出\n  /help — 帮助\n  /reset — 重置对话")
                return self.receive()
            return text
        except (EOFError, KeyboardInterrupt):
            self._active = False
            return ""

    def running(self) -> bool:
        return self._active


class InteractiveTransport(Transport):
    """
    增强版交互传输层。
    带颜色输出、命令历史、更好的格式。
    """
    def __init__(self):
        self._active = True
        self._has_color = sys.stdout.isatty() and os.name != "nt"

    def _color(self, text: str, code: str) -> str:
        if not self._has_color:
            return text
        codes = {"green": "32", "cyan": "36", "yellow": "33", "red": "31", "blue": "34", "bold": "1", "dim": "2"}
        c = codes.get(code, "0")
        return f"\033[{c}m{text}\033[0m"

    def send(self, text: str):
        separator = self._color("─" * shutil.get_terminal_size().columns, "dim")
        print(f"\n{separator}")
        print(f"{self._color('Agent', 'cyan')}  {text}")

    def send_stream(self, text: str):
        print(self._color(text, "green"), end="", flush=True)

    def send_tool_start(self, name: str, args: dict):
        args_str = json.dumps(args, ensure_ascii=False)[:80]
        print(f"\n  {self._color('🔧', 'yellow')} {self._color(name, 'bold')}({args_str})", end="", flush=True)

    def send_tool_result(self, name: str, result: str):
        print(self._color(f" ✓ {result[:50]}", "dim"), flush=True)

    def receive(self) -> str:
        try:
            text = input(f"\n{self._color('You', 'blue')}  ").strip()
            if text.lower() in ("/quit", "/exit", "/q"):
                self._active = False
                return ""
            if text.lower() == "/help":
                print(self._color("  /quit 退出  /help 帮助  /reset 重置", "dim"))
                return self.receive()
            return text
        except (EOFError, KeyboardInterrupt):
            self._active = False
            return ""

    def running(self) -> bool:
        return self._active


class SSETransport(Transport):
    """
    SSE (Server-Sent Events) 传输层。
    通过 stdout 输出 NDJSON 格式的事件流，供外部程序消费。
    格式: data: {"type": "text|tool_start|tool_result|error|done", "content": "..."}
    """
    def __init__(self):
        self._active = True

    def _emit(self, event_type: str, content: str):
        data = json.dumps({"type": event_type, "content": content}, ensure_ascii=False)
        print(f"data: {data}\n", flush=True)

    def send(self, text: str):
        self._emit("text", text)

    def send_stream(self, text: str):
        self._emit("text", text)

    def send_tool_start(self, name: str, args: dict):
        self._emit("tool_start", json.dumps({"name": name, "args": args}, ensure_ascii=False))

    def send_tool_result(self, name: str, result: str):
        self._emit("tool_result", json.dumps({"name": name, "result": result[:200]}, ensure_ascii=False))

    def send_error(self, msg: str):
        self._emit("error", msg)

    def receive(self) -> str:
        # SSE 模式下 stdin 读一行作为输入
        try:
            line = sys.stdin.readline()
            if not line:
                self._active = False
                return ""
            return json.loads(line).get("content", "")
        except (json.JSONDecodeError, EOFError, KeyboardInterrupt):
            self._active = False
            return ""

    def running(self) -> bool:
        return self._active


# ═══════════════════════════════════════════════
# Ch09: 多 Agent
# ═══════════════════════════════════════════════

class SubAgent:
    """
    子 Agent 实例。
    每个子 Agent 有自己独立的对话上下文和工具。
    用于承担主 Agent 委派的子任务。
    """

    def __init__(self, name: str, instruction: str, tools: list = None):
        self.name = name
        self.messages = [
            {"role": "system", "content": f"你是 {name}，一个专用子 Agent。\n{instruction}"},
        ]
        self.tools = tools or ToolRegistry.get_openai_tools()
        self.result = None
        self.status = "pending"  # pending | running | done | failed

    def run(self, task: str) -> str:
        """执行子任务，返回结果"""
        self.status = "running"
        self.messages.append({"role": "user", "content": task})

        print(f"\n    ┌─ [{self.name}] 收到任务: {task[:60]}...")

        for turn in range(1, 11):  # 子 Agent 最多 10 轮
            try:
                response = CLIENT.chat.completions.create(
                    model=MODEL, messages=self.messages, tools=self.tools,
                )
                msg = response.choices[0].message
                self.messages.append(msg)

                if not msg.tool_calls:
                    result = msg.content or "(无回答)"
                    self.result = result
                    self.status = "done"
                    print(f"    └─ [{self.name}] 完成 ✓")
                    return result

                for tc in msg.tool_calls:
                    func_name, args = tc.function.name, json.loads(tc.function.arguments)
                    tool_result = ToolRegistry.execute(func_name, **args)
                    self.messages.append({
                        "role": "tool", "tool_call_id": tc.id, "content": str(tool_result),
                    })

            except Exception as e:
                self.status = "failed"
                self.result = f"错误: {e}"
                return self.result

        self.status = "done"
        self.result = "(达到最大轮次)"
        return self.result


class SubAgentManager:
    """
    子 Agent 管理器。
    管理子 Agent 的生命周期：创建、运行、获取结果。
    """

    def __init__(self):
        self.agents: dict[str, SubAgent] = {}

    def create_agent(self, name: str, instruction: str) -> str:
        """创建一个子 Agent"""
        if name in self.agents:
            return f"子 Agent '{name}' 已存在"
        self.agents[name] = SubAgent(name, instruction)
        return f"已创建子 Agent '{name}'"

    def run_agent(self, name: str, task: str) -> str:
        """运行子 Agent 执行任务"""
        agent = self.agents.get(name)
        if not agent:
            return f"子 Agent '{name}' 不存在"
        return agent.run(task)

    def run_parallel(self, tasks: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """并行执行多个子任务"""
        results = []
        threads = []
        result_queue = queue.Queue()

        def _run(name: str, task: str):
            agent = self.agents.get(name)
            if agent:
                result = agent.run(task)
                result_queue.put((name, result))
            else:
                result_queue.put((name, f"子 Agent '{name}' 不存在"))

        for name, task in tasks:
            t = threading.Thread(target=_run, args=(name, task))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        while not result_queue.empty():
            results.append(result_queue.get())

        return results

    def get_result(self, name: str) -> str:
        agent = self.agents.get(name)
        if not agent:
            return f"子 Agent '{name}' 不存在"
        if agent.status == "pending":
            return f"子 Agent '{name}' 尚未运行"
        return agent.result or "(无结果)"

    def list_agents(self) -> list[dict]:
        return [
            {"name": name, "status": a.status, "messages": len(a.messages)}
            for name, a in self.agents.items()
        ]


# 全局子 Agent 管理器
SUB_MGR = SubAgentManager()


# ═══════════════════════════════════════════════
# Ch08: 插件生态
# ═══════════════════════════════════════════════

class PluginBase:
    name: str = ""
    version: str = "1.0.0"
    description: str = ""
    enabled: bool = True
    def setup(self): pass
    def teardown(self): pass
    def register_tools(self): pass
    def get_prompt_modules(self) -> list: return []

class PluginManager:
    def __init__(self, plugin_dir: str = None):
        if plugin_dir is None:
            plugin_dir = str(Path(__file__).parent / "plugins")
        self.plugin_dir = Path(plugin_dir)
        self.plugin_dir.mkdir(exist_ok=True)
        self.plugins: dict[str, PluginBase] = {}
        self._prompt_modules: list = []

    def discover_and_load(self) -> int:
        count = 0
        for py_file in sorted(self.plugin_dir.glob("*.py")):
            if py_file.name == "__init__.py": continue
            try:
                spec = importlib.util.spec_from_file_location(f"plugin_{py_file.stem}", py_file)
                if not spec or not spec.loader: continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, type) and issubclass(attr, PluginBase) and attr is not PluginBase:
                        inst = attr()
                        self.plugins[inst.name] = inst
                        print(f"  🔌 加载插件: {inst.name}")
                        count += 1
            except Exception as e:
                print(f"  ⚠ 插件失败: {e}")
        return count

    def activate_all(self):
        for name, plugin in self.plugins.items():
            if plugin.enabled:
                plugin.setup()
                plugin.register_tools()
                self._prompt_modules.extend(plugin.get_prompt_modules())

    def get_prompt_modules(self) -> list: return self._prompt_modules
    def list_plugins(self) -> list[dict]:
        return [{"name": p.name, "version": p.version, "description": p.description, "enabled": p.enabled} for p in self.plugins.values()]

PM = PluginManager()

# ─── 内置插件（简化版） ────────────────────────

MCP_CONFIG_DIR = Path(__file__).parent / "mcp"

class MCPLoaderPlugin(PluginBase):
    name = "mcp_loader"; version = "1.0.0"; description = "从 mcp/ 加载外部工具"
    def setup(self): MCP_CONFIG_DIR.mkdir(exist_ok=True)
    def register_tools(self):
        import importlib.util
        for f in MCP_CONFIG_DIR.glob("*.mcp.json"):
            try:
                data = json.loads(f.read_text())
                for td in data.get("tools", []):
                    ToolRegistry.register(MCPToolWrapper(td["name"], td.get("description",""), td.get("parameters",{})))
                    print(f"    └─ MCP: {td['name']}")
            except: pass

MCP_CONFIG_DIR.mkdir(exist_ok=True)

class MCPToolWrapper:
    def __init__(self, name, description, params):
        self.name, self.description, self._params = name, description, params
    def openai_schema(self) -> dict:
        return {"type":"function","function":{"name":self.name,"description":self.description,"parameters":self._params}}
    def __call__(self, **kwargs) -> str: return f"[外部工具 {self.name}] {kwargs}"


# ═══════════════════════════════════════════════
# Ch06: 上下文管理
# ═══════════════════════════════════════════════

class ContextManager:
    RECENT_TURNS_RESERVED = 3
    def __init__(self, max_tokens: int = 8192):
        self.max_tokens = max_tokens
        self.token_counts = []
        self.compression_count = 0
        # CHARS_PER_TOKEN set via @property below

    @property
    def CHARS_PER_TOKEN(self):
        # 1 token ~= 1.5 Chinese chars (computed as 3/2)
        return 3 / 2

    def estimate_tokens(self, text: str) -> int:
        if not text: return 0
        return int(len(text) / self.CHARS_PER_TOKEN) + 1

    def estimate_message_tokens(self, msg) -> int:
        total = 0
        raw = msg if isinstance(msg, dict) else msg.model_dump()
        for key in ("content","role","name"):
            val = raw.get(key)
            if isinstance(val, str): total += self.estimate_tokens(val)
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item,dict):
                        for sv in item.values():
                            if isinstance(sv,str): total += self.estimate_tokens(sv)
        return total + 4

    def add_message(self, msg): self.token_counts.append(self.estimate_message_tokens(msg))
    def total_used(self): return sum(self.token_counts)
    def should_compress(self): return self.total_used() > self.max_tokens

    def compress(self, messages):
        self.compression_count += 1
        print(f"  ⚡ 压缩 #{self.compression_count}")
        if len(messages)<=2: return messages
        system_msg = messages[0] if messages[0]["role"]=="system" else None
        if system_msg: messages=messages[1:]
        recent = messages[-7:] if len(messages)>7 else messages
        old = messages[:-7] if len(messages)>7 else []
        if not old:
            r=[system_msg] if system_msg else[]; r.extend(messages); return r
        compressed=[]
        for m in old:
            if m["role"]=="tool" and len(m.get("content",""))>100:
                c=m["content"]; compressed.append({**m,"content":c[:60]+f"...({c.count(chr(10))}行)"})
            else: compressed.append(m)
        if len(compressed)>3: compressed=[{"role":"system","content":"[已压缩旧消息]"}]
        r=[system_msg] if system_msg else[]; r.extend(compressed); r.extend(recent)
        self._recount(r); return r

    def _recount(self, msgs): self.token_counts=[self.estimate_message_tokens(m) for m in msgs]
    def get_usage_report(self): return f"上下文: {self.total_used()}/{self.max_tokens} tokens, 压缩 {self.compression_count} 次"

CTX = ContextManager()


# ═══════════════════════════════════════════════
# Ch05: 权限引擎
# ═══════════════════════════════════════════════

RULES_FILE = Path(__file__).parent / ".cagent_rules.json"

class PermissionEngine:
    def __init__(self):
        self.rules_file = RULES_FILE
        self.rules = self._load()
    def _load(self):
        d = {"deny":["rm -rf /","rm -rf /*","mkfs","dd if=",":(){ :|:& };:","> /dev/sda","chmod -R 777 /"],
             "ask":["rm","kill","pkill","shutdown","reboot","poweroff","docker rm","docker rmi","git push --force","git reset --hard"],
             "never_ask":[]}
        if self.rules_file.exists():
            try: d.update(json.loads(self.rules_file.read_text()))
            except: pass
        return d
    def _save(self): self.rules_file.write_text(json.dumps(self.rules, indent=2, ensure_ascii=False))
    def evaluate(self, cmd):
        cl=cmd.lower().strip()
        for a in self.rules["never_ask"]:
            if a in cmd: return "allow", f"信任: {a}"
        for k in self.rules["deny"]:
            if k in cl: return "deny", f"拒绝: {k}"
        for k in self.rules["ask"]:
            if k in cl: return "ask", f"需确认: {k}"
        return "allow",""
    def allow_future(self, cmd): self.rules["never_ask"].append(cmd); self._save()

PERM = PermissionEngine()


# ═══════════════════════════════════════════════
# Ch03: System Prompt
# ═══════════════════════════════════════════════

class PromptModule:
    def render(self): raise NotImplementedError

class RoleModule(PromptModule):
    def render(self): return "你是 CAgent，一个多功能 AI 助手。你有权使用各种工具来帮助用户解决问题。"

class ToolGuideModule(PromptModule):
    def render(self):
        if not ToolRegistry.list_tools(): return ""
        lines=["## 可用工具"]
        for t in ToolRegistry._tools.values(): lines.append(f"- `{t.name}`: {t.description}")
        lines.append("遇到问题时，从以上工具中选择合适工具调用。如需委派子任务，使用 delegate 工具。")
        return "\n".join(lines)

class OutputFormatModule(PromptModule):
    def render(self): return "## 回答要求\n- 简洁、准确\n- 给出具体数据\n- 调用完所有工具后给出最终总结"

class SubAgentModule(PromptModule):
    def render(self):
        agents = SUB_MGR.list_agents()
        if not agents: return ""
        lines=["## 子 Agent 状态"]
        for a in agents:
            lines.append(f"- {a['name']}: {a['status']} ({a['messages']} 条消息)")
        return "\n".join(lines)

class SystemPrompt:
    def __init__(self):
        self.modules = [RoleModule(), ToolGuideModule(), OutputFormatModule(), SubAgentModule()]
    def add_modules(self, mods): self.modules.extend(mods)
    def render(self):
        parts=[m.render() for m in self.modules if m.render()]; return "\n\n".join(parts)


# ═══════════════════════════════════════════════
# Ch04: Shell 安全
# ═══════════════════════════════════════════════

BLOCKED=["rm -rf /","rm -rf /*","mkfs","dd if=",":(){ :|:& };:","> /dev/sda","chmod -R 777 /","wget ","curl ","nc ","ncat ","sudo "]
ALLOWED=[Path("/root/CAgent"),Path("/root"),Path("/tmp")]
STIMEOUT=15; MAXOUT=2000

def valcmd(cmd):
    cl=cmd.lower().strip()
    for k in BLOCKED:
        if k in cl: return False, f"危险: {k}"
    if not cmd.strip(): return False,"空命令"
    return True,""

def issub(path,parent):
    try: path.relative_to(parent); return True
    except: return False

def shell_impl(cmd, wd="."):
    ok,r=valcmd(cmd)
    if not ok: return f"[拒绝] {r}"
    d,rr=PERM.evaluate(cmd)
    if d=="deny": return f"[权限拒绝] {rr}"
    if d=="ask": return f"[需确认] {rr}\n命令: {cmd}\n回复: 允许执行 {cmd}"
    ed=Path(wd).resolve()
    if not any(issub(ed,d) for d in ALLOWED): return f"[拒绝] 禁止在 {ed} 执行"
    try:
        r=subprocess.run(cmd,shell=True,cwd=ed,capture_output=True,text=True,timeout=STIMEOUT)
        out=r.stdout+r.stderr
        if len(out)>MAXOUT: out=out[:MAXOUT]+f"\n...截断({len(out)}字符)"
        return f"exit code: {r.returncode}\n{out}"
    except subprocess.TimeoutExpired: return f"[超时] {STIMEOUT}秒"
    except Exception as e: return f"[错误] {e}"


# ═══════════════════════════════════════════════
# Ch02: 工具系统
# ═══════════════════════════════════════════════

class ToolRegistry:
    _tools={}
    @classmethod
    def register(cls,t): cls._tools[t.name]=t
    @classmethod
    def get_openai_tools(cls): return [t.openai_schema() for t in cls._tools.values()]
    @classmethod
    def execute(cls,n,**kw):
        t=cls._tools.get(n)
        return t(**kw) if t else f"错误: 未知工具 '{n}'"
    @classmethod
    def list_tools(cls): return list(cls._tools.keys())

class ToolBase:
    name=""; description=""
    def __init_subclass__(cls,**kw):
        super().__init_subclass__(**kw)
        if cls.name: ToolRegistry.register(cls())
    def openai_schema(self):
        sig=inspect.signature(self.run); doc=inspect.getdoc(self.run) or ""
        p,r={},[]
        for pn,pp in sig.parameters.items():
            if pn=="self": continue; r.append(pn)
            tm={int:"number",float:"number",str:"string",bool:"boolean"}
            jt=tm.get(pp.annotation,"string"); pd=""
            for line in doc.split("\n"):
                line=line.strip()
                if line.startswith(f"{pn}:"): pd=line.split(":",1)[1].strip(); break
            p[pn]={"type":jt,"description":pd or f"参数 {pn}"}
        return {"type":"function","function":{"name":self.name,"description":self.description,
                "parameters":{"type":"object","properties":p,"required":r}}}
    def run(self,**kw): raise NotImplementedError
    def __call__(self,**kw): return self.run(**kw)


# ═══════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════

class Calculator(ToolBase):
    name="calculator"; description="做四则运算: add, subtract, multiply, divide"
    def run(self,a:float,b:float,op:str)->str:
        """a:第一个数\nb:第二个数\nop:运算"""
        ops={"add":lambda:a+b,"subtract":lambda:a-b,"multiply":lambda:a*b,"divide":lambda:a/b if b!=0 else"除数不能为0"}
        return str(ops.get(op,lambda:f"未知: {op}")())

class GetWeather(ToolBase):
    name="get_weather"; description="查询指定城市的天气"
    def run(self,city:str)->str:
        """city:城市名"""
        m={"深圳":"26°C,多云","北京":"22°C,晴","上海":"24°C,小雨","悉尼":"18°C,晴"}
        return m.get(city,f"{city}:暂无数据")

class RunShell(ToolBase):
    name="run_shell"; description="在安全环境中执行 Shell 命令"
    def run(self,command:str,workdir:str=".")->str:
        """command:要执行的 Shell 命令\nworkdir:执行目录"""
        return shell_impl(command,workdir)


# ═══════════════════════════════════════════════
# Ch09: 多 Agent 工具
# ═══════════════════════════════════════════════

class CreateSubAgent(ToolBase):
    name="create_sub_agent"
    description="创建一个子 Agent，给它一个角色和职责说明，用于委派子任务"
    def run(self, name: str, instruction: str) -> str:
        """name:子Agent名字（英文）\ninstruction:角色和职责说明"""
        return SUB_MGR.create_agent(name, instruction)

class RunSubAgent(ToolBase):
    name="run_sub_agent"
    description="运行一个已创建的子 Agent，给它一个具体的子任务"
    def run(self, name: str, task: str) -> str:
        """name:子Agent名字\ntask:要执行的子任务"""
        return SUB_MGR.run_agent(name, task)

class ListSubAgents(ToolBase):
    name="list_sub_agents"
    description="列出所有子 Agent 及其状态"
    def run(self) -> str:
        agents = SUB_MGR.list_agents()
        if not agents: return "没有子 Agent"
        lines = [f"- {a['name']}: {a['status']} ({a['messages']} 条消息)" for a in agents]
        return "子 Agent 列表:\n" + "\n".join(lines)

# ═══════════════════════════════════════════════
# Ch10: 传输工具
# ═══════════════════════════════════════════════

class EchoStream(ToolBase):
    name="echo_stream"
    description="流式输出一段文本给用户（用于测试流式效果）"
    def run(self, text: str) -> str:
        """text:要输出的文本"""
        return text


# ═══════════════════════════════════════════════
# Agent 循环
# ═══════════════════════════════════════════════

def agent_loop(user_input: str, transport: Transport = None) -> str:
    """带传输层的 Agent 循环"""
    if transport is None:
        transport = REPLTransport()

    sp = SystemPrompt()
    sp.add_modules(PM.get_prompt_modules())
    system_prompt = sp.render()

    messages = [{"role":"system","content":system_prompt}, {"role":"user","content":user_input}]
    for m in messages: CTX.add_message(m)
    tools = ToolRegistry.get_openai_tools()
    transport.send(f"📦 {ToolRegistry.list_tools()}")
    transport.send(f"📊 {CTX.get_usage_report()}")

    for turn in range(1, MAX_TURNS+1):
        transport.send(f"\n── 第 {turn} 轮 ──")
        if CTX.should_compress(): messages = CTX.compress(messages)
        response = CLIENT.chat.completions.create(model=MODEL, messages=messages, tools=tools)
        msg = response.choices[0].message
        messages.append(msg); CTX.add_message(msg)
        if not msg.tool_calls:
            result = msg.content or "(空回答)"
            transport.send(result)
            return result

        for tc in msg.tool_calls:
            fn, args = tc.function.name, json.loads(tc.function.arguments)
            transport.send_tool_start(fn, args)
            result = ToolRegistry.execute(fn, **args)
            transport.send_tool_result(fn, result)
            tm = {"role":"tool","tool_call_id":tc.id,"content":str(result)}
            messages.append(tm); CTX.add_message(tm)

    return "达到最大轮次"


def interactive_session(transport: Transport):
    """
    多轮交互会话。
    在 transport 的 receive/send 循环中持续对话，直到用户退出。
    """
    transport.send("CAgent v10 — CLI 传输层模式")
    transport.send(f"模型: {MODEL} | 工具: {ToolRegistry.list_tools()}")
    transport.send("输入 /help 查看命令, /quit 退出")

    messages_cache = []

    while transport.running():
        user_input = transport.receive()
        if not user_input:
            continue

        if user_input.lower() == "/reset":
            messages_cache = []
            CTX.token_counts = []
            CTX.compression_count = 0
            transport.send("对话已重置 ✓")
            continue

        transport.send_tool_start("agent_loop", {"input": user_input[:60]})
        result = agent_loop(user_input, transport)
        transport.send_tool_result("agent_loop", result)
        transport.send(f"✅ {result}")


# ─── 入口 ─────────────────────────────────────

def main():
    import importlib.util
    count = PM.discover_and_load()
    if count == 0:
        PM.plugins["mcp_loader"] = MCPLoaderPlugin()
        print(f"  🔌 加载 1 个内置插件")
    PM.activate_all()

    # 解析命令行参数
    args = sys.argv[1:]

    if "--serve" in args:
        # HTTP 服务模式（SSE 基础，后续可扩展为 HTTP 服务器）
        transport = SSETransport()
        while transport.running():
            user_input = transport.receive()
            if user_input:
                result = agent_loop(user_input, transport)
        return

    if "--repl" in args:
        # REPL 模式
        transport = REPLTransport()
        interactive_session(transport)
        return

    if "--interactive" in args:
        # 增强交互模式
        transport = InteractiveTransport()
        interactive_session(transport)
        return

    # 默认：单次查询（向后兼容）
    prompt = args[0] if args else "帮我查深圳天气和北京天气，让不同的子 Agent 分别查"
    print(f"🧑 用户: {prompt}")
    result = agent_loop(prompt)
    print(f"\n✅ {result}")


if __name__ == "__main__":
    main()
