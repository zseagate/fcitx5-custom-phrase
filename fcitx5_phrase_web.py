#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fcitx5-phrase 的可视化管理界面（仅用 Python 标准库）。

安全设计：
* 默认只监听 127.0.0.1，不对外网开放。
* 每次启动生成随机 CSRF token，页面通过自定义请求头携带；
  恶意网页无法读取 token（同源策略），也无法跨站设置该请求头。
* 校验 Host 头，拒绝 DNS rebinding。
"""

from __future__ import annotations

import errno
import json
import secrets
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import fcitx5_phrase as fp

MAX_BODY = 1 << 20  # 1 MiB
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="csrf-token" content="__CSRF_TOKEN__">
<title>Fcitx5 自定义词组</title>
<style>
:root{
  --bg:#f5f6fa; --card:#fff; --text:#1f2430; --muted:#7a8194;
  --line:#e6e8ef; --accent:#4f6ef7; --accent-2:#3d59d8;
  --ok:#1f9d63; --warn:#b7791f; --err:#d64545; --radius:14px;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  background:var(--bg); color:var(--text);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans CJK SC",
       "WenQuanYi Micro Hei",sans-serif;
}
.app{max-width:1040px;margin:0 auto;padding:28px 20px 60px}
header{display:flex;align-items:flex-start;gap:16px;margin-bottom:22px}
h1{font-size:24px;margin:0;letter-spacing:.5px}
.sub{margin:4px 0 0;color:var(--muted);font-size:13px}
.badges{margin-left:auto;display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}
.badge{
  font-size:12px;padding:5px 11px;border-radius:999px;
  background:#eef0f6;color:#5a6172;white-space:nowrap;
}
.badge.ok{background:#e5f6ee;color:var(--ok)}
.badge.warn{background:#fdf3e2;color:var(--warn)}
.card{
  background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  padding:20px 22px;margin-bottom:18px;box-shadow:0 1px 2px rgba(20,30,60,.04);
}
h2{font-size:16px;margin:0 0 14px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
label{display:flex;flex-direction:column;gap:6px;font-size:13px;color:#565d70}
input[type=text],input[type=number],input:not([type]),textarea,.search{
  width:100%;border:1px solid var(--line);border-radius:9px;padding:9px 11px;
  font:inherit;color:var(--text);background:#fbfbfe;outline:none;
  transition:border-color .15s, box-shadow .15s;
}
input:focus,textarea:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(79,110,247,.12)}
input[readonly]{background:#f0f1f5;color:var(--muted)}
textarea{resize:vertical;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px}
.row{display:flex;align-items:center;gap:10px;margin-top:14px;flex-wrap:wrap}
.spacer{flex:1}
.check{flex-direction:row;align-items:center;gap:7px;color:var(--muted);font-size:13px;cursor:pointer}
.check input{width:16px;height:16px;accent-color:var(--accent)}
button{
  font:inherit;font-size:14px;border-radius:9px;padding:9px 16px;cursor:pointer;
  border:1px solid transparent;transition:background .15s,border-color .15s,color .15s;
}
button:disabled{opacity:.55;cursor:not-allowed}
.primary{background:var(--accent);color:#fff}
.primary:hover:not(:disabled){background:var(--accent-2)}
.ghost{background:#fff;border-color:var(--line);color:#4b5266}
.ghost:hover{border-color:#c9cede;background:#fafbff}
.danger-ghost{background:#fff;border-color:#f0d2d2;color:var(--err)}
.danger-ghost:hover{background:#fdf3f3}
.hint{color:var(--muted);font-size:12.5px;margin:12px 0 0}
.toolbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.toolbar h2{margin:0}
.search{width:220px;padding:8px 11px}
.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:10px 12px;text-align:left;border-bottom:1px solid var(--line)}
th{background:#fafbfe;color:var(--muted);font-weight:600;font-size:12.5px;white-space:nowrap}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover{background:#fafbff}
td.ops,th.ops{text-align:right;white-space:nowrap}
.phrase{font-weight:600}
.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px;color:#3f4658}
.muted{color:#a4a9b8;font-size:12px}
.link{background:none;border:none;padding:4px 8px;color:var(--accent);font-size:13px}
.link:hover{text-decoration:underline}
.link.danger{color:var(--err)}
.empty{text-align:center;color:var(--muted);padding:26px 0;margin:0}
.paths{color:#a0a5b4;font-size:11.5px;margin:14px 0 0;word-break:break-all;line-height:1.8}
.import-box{border:1px dashed #cfd4e3;border-radius:10px;padding:14px;margin-bottom:16px;background:#fbfcff}
.toast{
  position:fixed;right:24px;bottom:24px;max-width:min(460px,90vw);
  padding:12px 16px;border-radius:10px;color:#fff;font-size:14px;
  box-shadow:0 8px 28px rgba(20,30,60,.18);z-index:99;
}
.toast.ok{background:#1f9d63}
.toast.err{background:#d64545}
.toast.info{background:#4f6ef7}
.bye{max-width:560px;margin:120px auto;text-align:center;color:var(--muted);font-size:18px}
@media (max-width:760px){
  .grid{grid-template-columns:1fr 1fr}
  .search{width:100%}
  header{flex-direction:column}
  .badges{margin-left:0}
}
@media (max-width:480px){ .grid{grid-template-columns:1fr} }
</style>
</head>
<body>
<div class="app">
  <header>
    <div>
      <h1>Fcitx5 自定义词组</h1>
      <p class="sub">管理拼音 / 五笔拼音的自定义词条，保存后立即生效</p>
    </div>
    <div class="badges">
      <span id="imStatus" class="badge">检测中…</span>
      <span id="countBadge" class="badge">0 条</span>
    </div>
  </header>

  <div id="toast" class="toast" hidden></div>

  <section class="card">
    <h2 id="formTitle">添加词组</h2>
    <form id="phraseForm" autocomplete="off">
      <div class="grid">
        <label>词组
          <input id="phrase" required placeholder="例如：微信">
        </label>
        <label>拼音
          <input id="pinyin" placeholder="可留空，如 weixin">
        </label>
        <label>五笔编码
          <input id="wubi" placeholder="可留空，如 tmwy">
        </label>
        <label>权重
          <input id="weight" type="number" min="0" value="100">
        </label>
      </div>
      <div class="row">
        <label class="check">
          <input id="applyNow" type="checkbox" checked>
          立即应用（会短暂重启 fcitx5）
        </label>
        <div class="spacer"></div>
        <button type="button" id="resetBtn" class="ghost">清空</button>
        <button type="submit" id="submitBtn" class="primary">添加</button>
      </div>
    </form>
    <p class="hint">拼音可连写（<code>weixin</code> 自动切成 <code>wei'xin</code>）；留空则自动推断。多音字建议手动填写拼音。</p>
  </section>

  <section class="card">
    <div class="toolbar">
      <h2>词组列表</h2>
      <div class="spacer"></div>
      <input id="search" class="search" placeholder="搜索词组 / 拼音 / 编码">
      <button id="applyAllBtn" class="ghost">应用全部</button>
      <button id="exportBtn" class="ghost">导出</button>
      <button id="importToggle" class="ghost">导入</button>
      <button id="shutdownBtn" class="danger-ghost">退出服务</button>
    </div>

    <div id="importBox" class="import-box" hidden>
      <textarea id="importText" rows="6" placeholder="每行一条：词组  拼音  五笔  权重（Tab / 逗号 / 空格分隔均可）
例如：
微信	weixin	tmwy	100
王者荣耀	wangzherongyao		100"></textarea>
      <div class="row">
        <label class="check">
          <input id="importReplace" type="checkbox">
          替换现有词组表（勾选前请先导出备份）
        </label>
        <div class="spacer"></div>
        <button id="importCancel" class="ghost">取消</button>
        <button id="importSubmit" class="primary">导入</button>
      </div>
    </div>

    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>词组</th><th>拼音</th><th>五笔</th><th>权重</th>
            <th class="ops">操作</th>
          </tr>
        </thead>
        <tbody id="tbody"></tbody>
      </table>
      <p id="empty" class="empty" hidden>还没有自定义词组，先在上方添加一条吧。</p>
    </div>
    <p class="paths" id="paths"></p>
  </section>
</div>

<script>
"use strict";
const TOKEN = document.querySelector('meta[name="csrf-token"]').content;
const $ = (id) => document.getElementById(id);
let entries = [];
let editing = null;
let pathsText = "";

function esc(s){
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[c]));
}

async function api(path, opts = {}){
  const headers = Object.assign({"X-CSRF-Token": TOKEN}, opts.headers || {});
  if (opts.body && typeof opts.body !== "string"){
    opts.body = JSON.stringify(opts.body);
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, Object.assign({}, opts, {headers}));
  if (!res.ok){
    let msg = res.statusText;
    try { const j = await res.json(); msg = j.error || msg; } catch (e) {}
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res;
}

let toastTimer = null;
function toast(msg, kind = "ok"){
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast " + kind;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 4500);
}

function render(){
  const q = $("search").value.trim().toLowerCase();
  const list = entries.filter((e) => {
    if (!q) return true;
    return (e.phrase || "").toLowerCase().includes(q)
        || (e.pinyin || "").toLowerCase().includes(q)
        || (e.wubi || "").toLowerCase().includes(q);
  });
  $("tbody").innerHTML = list.map((e) => `
    <tr>
      <td class="phrase">${esc(e.phrase)}</td>
      <td class="mono">${esc(e.pinyin)}</td>
      <td class="mono">${e.wubi ? esc(e.wubi) : '<span class="muted">自动/无</span>'}</td>
      <td>${esc(e.weight)}</td>
      <td class="ops">
        <button class="link" data-edit="${esc(e.phrase)}">编辑</button>
        <button class="link danger" data-del="${esc(e.phrase)}">删除</button>
      </td>
    </tr>`).join("");
  $("empty").hidden = list.length !== 0;
  $("countBadge").textContent = `${entries.length} 条`;
  $("paths").textContent = pathsText;
  $("tbody").querySelectorAll("[data-edit]").forEach((b) => {
    b.onclick = () => startEdit(b.getAttribute("data-edit"));
  });
  $("tbody").querySelectorAll("[data-del]").forEach((b) => {
    b.onclick = () => removePhrase(b.getAttribute("data-del"));
  });
}

async function load(){
  try {
    const s = await api("/api/state");
    entries = s.entries;
    pathsText = `词组表：${s.config_path}　拼音词库：${s.pinyin_dict}　五笔词库：${s.wbpy_dict}　v${s.version}`;
    const st = $("imStatus");
    st.textContent = s.fcitx_running ? "fcitx5 运行中" : "fcitx5 未运行";
    st.className = "badge " + (s.fcitx_running ? "ok" : "warn");
    render();
  } catch (e) {
    toast("加载失败：" + e.message, "err");
  }
}

function resetForm(){
  editing = null;
  $("phrase").readOnly = false;
  $("phrase").value = "";
  $("pinyin").value = "";
  $("wubi").value = "";
  $("weight").value = 100;
  $("formTitle").textContent = "添加词组";
  $("submitBtn").textContent = "添加";
}

function startEdit(phrase){
  const e = entries.find((x) => x.phrase === phrase);
  if (!e) return;
  editing = e.phrase;
  $("phrase").value = e.phrase;
  $("phrase").readOnly = true;
  $("pinyin").value = e.pinyin || "";
  $("wubi").value = e.wubi || "";
  $("weight").value = e.weight;
  $("formTitle").textContent = `编辑「${e.phrase}」`;
  $("submitBtn").textContent = "保存";
  $("phrase").scrollIntoView({behavior: "smooth", block: "center"});
  $("pinyin").focus();
}

$("phraseForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const body = {
    phrase: $("phrase").value.trim(),
    pinyin: $("pinyin").value.trim(),
    wubi: $("wubi").value.trim(),
    weight: parseInt($("weight").value || "100", 10),
    apply: $("applyNow").checked,
  };
  if (!body.phrase) return;
  const btn = $("submitBtn");
  btn.disabled = true;
  try {
    const r = await api("/api/add", {method: "POST", body});
    toast(`已保存「${r.entry.phrase}」${body.apply ? "，并已应用到 fcitx5" : ""}`);
    resetForm();
    await load();
  } catch (e) {
    toast("保存失败：" + e.message, "err");
  } finally {
    btn.disabled = false;
  }
});

async function removePhrase(phrase){
  if (!confirm(`确定删除「${phrase}」吗？`)) return;
  try {
    await api("/api/remove", {method: "POST", body: {phrase, apply: true}});
    toast(`已删除「${phrase}」`);
    if (editing === phrase) resetForm();
    await load();
  } catch (e) {
    toast("删除失败：" + e.message, "err");
  }
}

$("resetBtn").onclick = resetForm;
$("search").addEventListener("input", render);

$("applyAllBtn").onclick = async () => {
  const btn = $("applyAllBtn");
  btn.disabled = true;
  try {
    const r = await api("/api/apply", {method: "POST", body: {}});
    toast(`已应用 ${r.count} 条词组`);
    await load();
  } catch (e) {
    toast("应用失败：" + e.message, "err");
  } finally {
    btn.disabled = false;
  }
};

$("exportBtn").onclick = async () => {
  try {
    const res = await fetch("/api/export", {headers: {"X-CSRF-Token": TOKEN}});
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "fcitx5-custom-phrase.tsv";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1500);
    toast("已导出词组表");
  } catch (e) {
    toast("导出失败：" + e.message, "err");
  }
};

$("importToggle").onclick = () => { $("importBox").hidden = !$("importBox").hidden; };
$("importCancel").onclick = () => { $("importBox").hidden = true; };
$("importSubmit").onclick = async () => {
  const text = $("importText").value;
  if (!text.trim()) { toast("请先粘贴要导入的内容", "err"); return; }
  try {
    const r = await api("/api/import", {
      method: "POST",
      body: {text, replace: $("importReplace").checked, apply: true},
    });
    toast(`已导入 ${r.imported} 条，共 ${r.total} 条`);
    $("importText").value = "";
    $("importReplace").checked = false;
    $("importBox").hidden = true;
    await load();
  } catch (e) {
    toast("导入失败：" + e.message, "err");
  }
};

$("shutdownBtn").onclick = async () => {
  if (!confirm("退出后需要重新从菜单启动服务，确定吗？")) return;
  try { await api("/api/shutdown", {method: "POST", body: {}}); } catch (e) {}
  document.body.innerHTML = '<div class="bye">服务已退出，可以关闭此页面。</div>';
};

load();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "fcitx5-phrase-web/" + fp.VERSION
    protocol_version = "HTTP/1.1"

    # ---- 基础响应 ----
    def _common_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")

    def _send(self, code: int, body: bytes, ctype: str,
              extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self._common_headers()
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, code: int, message: str) -> None:
        self._json(code, {"error": message})

    # ---- 安全 ----
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip()
        if host.startswith("["):          # [::1]:8765
            host = host.split("]")[0] + "]"
        else:
            host = host.split(":")[0]
        return host in LOCAL_HOSTS

    def _authorized(self) -> bool:
        token = getattr(self.server, "token", "")
        got = self.headers.get("X-CSRF-Token") or ""
        return bool(token) and secrets.compare_digest(got, token)

    # ---- 路由 ----
    def do_GET(self) -> None:
        if not self._host_ok():
            self._error(403, "Host 不被允许")
            return
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            page = HTML.replace("__CSRF_TOKEN__", self.server.token)
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        if not self._authorized():
            self._error(403, "无效的 CSRF token")
            return
        try:
            if path == "/api/state":
                self._json(200, fp.state_info())
            elif path == "/api/export":
                data = fp.export_text().encode("utf-8")
                self._send(
                    200, data, "text/tab-separated-values; charset=utf-8",
                    {"Content-Disposition":
                     'attachment; filename="fcitx5-custom-phrase.tsv"'},
                )
            else:
                self._error(404, "未找到")
        except fp.PhraseError as exc:
            self._error(400, str(exc))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._error(500, f"内部错误: {exc}")

    def do_POST(self) -> None:
        if not self._host_ok():
            self._error(403, "Host 不被允许")
            return
        if not self._authorized():
            self._error(403, "无效的 CSRF token")
            return
        path = urlsplit(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._error(400, "Content-Length 无效")
            return
        if length < 0 or length > MAX_BODY:
            self._error(413, "请求体过大")
            return
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(data, dict):
                raise ValueError
        except Exception:
            self._error(400, "请求体必须是 JSON 对象")
            return

        try:
            with self.server.op_lock:  # type: ignore[attr-defined]
                if path == "/api/add":
                    entry, _ = fp.perform_add(
                        str(data.get("phrase", "")).strip(),
                        str(data.get("pinyin", "")).strip() or None,
                        str(data.get("wubi", "")).strip() or None,
                        int(data.get("weight", 100)),
                        apply=bool(data.get("apply", True)),
                    )
                    self._json(200, {"entry": entry})
                elif path == "/api/remove":
                    total = fp.perform_remove(
                        str(data.get("phrase", "")).strip(),
                        apply=bool(data.get("apply", True)),
                    )
                    self._json(200, {"total": total})
                elif path == "/api/import":
                    imported, total = fp.perform_import(
                        str(data.get("text", "")),
                        replace=bool(data.get("replace", False)),
                        apply=bool(data.get("apply", True)),
                    )
                    self._json(200, {"imported": imported, "total": total})
                elif path == "/api/apply":
                    entries = fp.read_phrase_file()
                    fp.apply_entries(entries, verbose=False)
                    self._json(200, {"count": len(entries)})
                elif path == "/api/shutdown":
                    self._json(200, {"ok": True})
                    threading.Thread(target=self.server.shutdown,
                                     daemon=True).start()
                else:
                    self._error(404, "未找到")
        except fp.PhraseError as exc:
            self._error(400, str(exc))
        except (TypeError, ValueError) as exc:
            self._error(400, f"参数错误: {exc}")
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._error(500, f"内部错误: {exc}")

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("[web] " + (fmt % args) + "\n")


def run_web(host: str = "127.0.0.1", port: int = 8765,
            token: str | None = None, open_browser: bool = True) -> int:
    token = token or secrets.token_urlsafe(24)
    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            url = f"http://{host}:{port}/"
            print(f"端口 {port} 已被占用，可能服务已在运行：{url}")
            if open_browser:
                webbrowser.open(url)
            return 0
        raise
    httpd.daemon_threads = True
    httpd.token = token           # type: ignore[attr-defined]
    httpd.op_lock = threading.Lock()  # type: ignore[attr-defined]

    url = f"http://{host}:{port}/"
    print(f"fcitx5-phrase web 已启动：{url}", flush=True)
    print("在浏览器中操作；关闭服务可点页面上的「退出服务」，或按 Ctrl+C。",
          flush=True)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(run_web())
