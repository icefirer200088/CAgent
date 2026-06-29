#!/usr/bin/env python3
"""
MCP Server — 百度搜索工具
MCP 协议: 通过 stdio 接收 JSON-RPC 请求，返回结果
"""

import sys
import json
import urllib.request
import urllib.parse
import re


TOOLS = [
    {
        "name": "baidu_search",
        "description": "通过百度搜索互联网获取实时信息。返回网页标题和摘要。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "web_fetch",
        "description": "获取指定 URL 的网页内容（纯文本版）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要获取的网页 URL"}
            },
            "required": ["url"]
        }
    }
]


def baidu_search(query: str) -> str:
    """通过百度搜索"""
    try:
        url = f"https://www.baidu.com/s?wd={urllib.parse.quote(query)}&tn=baiduwb"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", errors="replace")
        out = []
        # 提取搜索结果标题
        for item in re.findall(r'<h3[^>]*>(.*?)</h3>', html, re.DOTALL)[:8]:
            title = re.sub(r'<[^>]+>', '', item).strip()
            if title:
                # 去掉标签里的 em 标签残留
                title = re.sub(r'<[^>]+>', '', title)
                out.append(f"• {title}")
        if not out:
            return f"(搜索 '{query}' 暂无结果)"
        return "\n".join(out)
    except Exception as e:
        return f"[搜索出错] {e}"


def web_fetch(url: str) -> str:
    """获取网页文本内容"""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", errors="replace")
        # 简单去标签提取文本
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:3000] or "(空内容)"
    except Exception as e:
        return f"[抓取出错] {e}"


def handle_request(request: dict) -> dict:
    """处理 MCP JSON-RPC 请求"""
    method = request.get("method", "")
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "cagent-mcp-search",
                    "version": "1.0.0"
                }
            }
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": TOOLS
            }
        }

    if method == "tools/call":
        params = request.get("params", {})
        name = params.get("name", "")
        arguments = params.get("arguments", {})

        if name == "baidu_search":
            result = baidu_search(arguments.get("query", ""))
        elif name == "web_fetch":
            result = web_fetch(arguments.get("url", ""))
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"未知工具: {name}"}
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [
                    {"type": "text", "text": result}
                ]
            }
        }

    if method == "notifications/initialized":
        # 通知不需要响应，但需要消耗掉 req_id
        return None

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"未知方法: {method}"}
    }


def main():
    """MCP Server 主循环：从 stdin 读 JSON-RPC 请求，通过 stdout 返回"""
    # 启动时通知 stderr（不影响 stdout 的 JSON 通信）
    sys.stderr.write("MCP Server started (baidu_search + web_fetch)\n")
    sys.stderr.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
            if response is not None:
                # stdout 输出 JSON，每行一个
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError as e:
            sys.stderr.write(f"JSON parse error: {e}\n")
            sys.stderr.flush()


if __name__ == "__main__":
    main()
