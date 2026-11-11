# -*- coding: utf-8 -*-
"""CSDN 同步助手 MCP 直连探针（stdio JSON-RPC，绕过 WorkBuddy 信任流程先实测）。
用后即弃。用法：
  python scripts/_probe_csdn_sync.py list     # 握手 + 列工具
  python scripts/_probe_csdn_sync.py call <tool_name> [json_args]
"""
import json
import os
import shutil
import subprocess
import sys
import time

TOKEN = "21ba6f83-1e5d-438f-a282-dc86690de5e0"
# 127.0.0.1 代理污染是本机常发坑：WebSocket 连扩展必须绕过系统代理
ENV = dict(os.environ,
           CSDN_PUBLISH_MCP_TOKEN=TOKEN,
           CSDN_PUBLISH_MCP_PORT="3721")
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
          "ALL_PROXY", "all_proxy"):
    ENV.pop(k, None)
ENV["NO_PROXY"] = ENV["no_proxy"] = "127.0.0.1,localhost"
NPX = shutil.which("npx") or shutil.which("npx.cmd")
if not NPX:
    raise SystemExit("找不到 npx")

class MCP:
    def __init__(self):
        self.proc = subprocess.Popen(
            [NPX, "-y", "csdn-article-sync-mcp@latest"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=ENV, text=True, encoding="utf-8")
        self._id = 0

    def _send(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def _recv(self, want_id):
        while True:
            line = self.proc.stdout.readline()
            if not line:
                err = self.proc.stderr.read() if self.proc.poll() is not None else ""
                raise RuntimeError("MCP 无响应 (exit=%s) %s" % (self.proc.poll(), err[:300]))
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            if msg.get("id") == want_id:
                return msg

    def initialize(self):
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "probe", "version": "0.0.1"}}})
        r = self._recv(self._id)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return r["result"]

    def tools_list(self):
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": "tools/list"})
        return self._recv(self._id)["result"]["tools"]

    def tools_call(self, name, args):
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": "tools/call",
                    "params": {"name": name, "arguments": args}})
        return self._recv(self._id)["result"]

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        self.proc.terminate()

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "list"
    m = MCP()
    try:
        init = m.initialize()
        print("server:", init.get("serverInfo"))
        tools = m.tools_list()
        print("工具 %d 个:" % len(tools))
        for t in tools:
            print(" -", t["name"], "|", (t.get("description") or "")[:80])
            print("   params:", json.dumps(t.get("inputSchema", {}).get("properties", {}),
                                           ensure_ascii=False)[:200])
        if mode == "call":
            name = sys.argv[2]
            args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
            wait = int(sys.argv[4]) if len(sys.argv) > 4 else 20
            print("等待扩展重连 %ds（扩展是 WS client，需 MCP server 进程存活期间重试）..." % wait)
            time.sleep(wait)
            print("\n== call %s %s" % (name, json.dumps(args, ensure_ascii=False)[:200]))
            r = m.tools_call(name, args)
            print(json.dumps(r, ensure_ascii=False))
    finally:
        m.close()

if __name__ == "__main__":
    main()
