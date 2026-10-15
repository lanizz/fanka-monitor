"""分析 mitmproxy 抓包文件，按时间顺序 dump 17wanxiao/newcapec 相关请求。

用法：
  pip install mitmproxy
  python3 capture_analyze.py [capture.flows]

用于寻找 authorize / login / bootcallback 请求，提取 config.json 所需参数。
"""
import sys

from mitmproxy import io, http

HOSTS = ("17wanxiao.com", "newcapec.cn")
path = sys.argv[1] if len(sys.argv) > 1 else "capture.flows"

i = 0
with open(path, "rb") as f:
    for flow in io.FlowReader(f).stream():
        if not isinstance(flow, http.HTTPFlow):
            continue
        if not any(h in flow.request.pretty_host for h in HOSTS):
            continue
        status = flow.response.status_code if flow.response else None
        print(f"[{i}] {flow.request.method} {status} {flow.request.pretty_url[:200]}")
        if flow.request.content:
            print(f"    REQ: {flow.request.content.decode('utf-8', errors='replace')[:300]}")
        if flow.response:
            loc = flow.response.headers.get("Location")
            if loc:
                print(f"    LOCATION: {loc[:250]}")
            for sc in flow.response.headers.get_all("Set-Cookie") or []:
                print(f"    SET-COOKIE: {sc[:130]}")
            body = flow.response.content or b""
            if body.strip().startswith((b"{", b"[")):
                print(f"    RESP: {body.decode('utf-8', errors='replace')[:300]}")
        i += 1
