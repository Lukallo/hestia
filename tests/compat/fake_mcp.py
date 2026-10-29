"""A stand-in connector for tests/compat/run.sh: one tool that leaves a marker file when it really runs."""
import json, os, sys

TOOLS = [{
    "name": "send_message",
    "description": "Send a chat message to the team channel.",
    "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
}]

def reply(msg_id, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": msg_id}
    out["error" if error else "result"] = error or result
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    if not line.strip():
        continue
    msg = json.loads(line)
    method, msg_id = msg.get("method"), msg.get("id")
    if msg_id is None:
        continue  # notifications need no answer
    if method == "initialize":
        reply(msg_id, {"protocolVersion": msg["params"].get("protocolVersion", "2025-06-18"),
                       "capabilities": {"tools": {}},
                       "serverInfo": {"name": "fake_slack", "version": "1.0"}})
    elif method == "tools/list":
        reply(msg_id, {"tools": TOOLS})
    elif method == "tools/call":
        with open(os.environ["MARKER"], "a") as f:
            f.write(json.dumps(msg["params"].get("arguments", {})) + "\n")
        reply(msg_id, {"content": [{"type": "text", "text": "Message sent."}]})
    elif method == "ping":
        reply(msg_id, {})
    else:
        reply(msg_id, error={"code": -32601, "message": "unknown method"})
