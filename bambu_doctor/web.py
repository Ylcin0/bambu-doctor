"""本地网页界面。

    bambu-doctor serve

零依赖：只用标准库的 http.server，页面是服务端渲染的 HTML + 内联 CSS。
默认只绑 127.0.0.1（不对局域网开放）；要手机访问就 `--host 0.0.0.0`。

⚠️ 页面里所有来自知识库的文本都经过 html.escape —— 知识库是**别人能提 PR 的数据文件**，
   不做转义等于让别人往你浏览器里塞脚本。
"""

from __future__ import annotations

import base64
import html
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from .diagnose import STATUS_CONFIRMED, STATUS_EXCLUDED, STATUS_MANUAL, STATUS_UNDETERMINED, diagnose
from .knowledge import KnowledgeBase
from .profiles import ORIGIN_USER, ProfileIndex
from .vision import VisionError, identify

# 照片转 base64 后 payload 会比原图大约 1/3
MAX_UPLOAD_BYTES = 18 * 1024 * 1024

CSS = """
:root{
  --bg:#f4f5f7; --card:#fff; --fg:#16181d; --muted:#6b7280; --border:#e2e4e9;
  --red:#c5221f; --red-bg:#fdecea; --amber:#a05a00; --amber-bg:#fef4e6;
  --blue:#1558d6; --blue-bg:#e8f0fe; --gray:#5f6368; --gray-bg:#f1f3f4;
  --radius:12px;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#0f1115; --card:#171a1f; --fg:#e8eaed; --muted:#9aa0a6; --border:#262a31;
    --red:#ff8a80; --red-bg:#2a1618; --amber:#ffb454; --amber-bg:#2a2114;
    --blue:#8ab4f8; --blue-bg:#141d2e; --gray:#9aa0a6; --gray-bg:#1d2025;
  }
}
*{box-sizing:border-box;margin:0;padding:0}
html{background:var(--bg)}
body{
  font-family:system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
  background:var(--bg); color:var(--fg); line-height:1.65;
  min-height:100vh; padding-bottom:4rem; -webkit-text-size-adjust:100%;
}
a{color:var(--blue); text-decoration:none}
a:hover{text-decoration:underline}
header{
  background:var(--card); border-bottom:1px solid var(--border);
  padding:.9rem 1.25rem; position:sticky; top:0; z-index:10;
}
header .inner{max-width:860px; margin:0 auto; display:flex; align-items:baseline; gap:.75rem; flex-wrap:wrap}
header h1{font-size:1.05rem; font-weight:650; letter-spacing:-.01em}
header .sub{color:var(--muted); font-size:.85rem}
main{max-width:860px; margin:0 auto; padding:1.5rem 1.25rem}
.lead{color:var(--muted); font-size:.9rem; margin-bottom:1.25rem}
.picker{
  background:var(--card); border:1px solid var(--border); border-radius:var(--radius);
  padding:1rem 1.1rem; margin-bottom:1.5rem;
}
.picker label{display:block; font-size:.8rem; color:var(--muted); margin-bottom:.4rem}
select{
  width:100%; padding:.55rem .7rem; font:inherit; font-size:.95rem;
  background:var(--bg); color:var(--fg); border:1px solid var(--border);
  border-radius:8px;
}
h2.group{
  font-size:.78rem; font-weight:650; letter-spacing:.06em; text-transform:uppercase;
  display:flex; align-items:center; gap:.5rem; margin:2rem 0 .85rem;
}
h2.group .dot{width:9px; height:9px; border-radius:50%; flex:none}
h2.group .count{color:var(--muted); font-weight:500; letter-spacing:0}
h2.group.g-confirmed{color:var(--red)} h2.group.g-confirmed .dot{background:var(--red)}
h2.group.g-manual{color:var(--blue)}    h2.group.g-manual .dot{background:var(--blue)}
h2.group.g-undetermined{color:var(--amber)} h2.group.g-undetermined .dot{background:var(--amber)}
h2.group.g-excluded{color:var(--gray)}  h2.group.g-excluded .dot{background:var(--gray)}
h2.group.g-neutral{color:var(--muted)}  h2.group.g-neutral .dot{background:var(--muted)}
.card{
  background:var(--card); border:1px solid var(--border); border-radius:var(--radius);
  padding:1.1rem 1.25rem; margin-bottom:.85rem;
}
.card.c-confirmed{border-left:3px solid var(--red)}
.card.c-manual{border-left:3px solid var(--blue)}
.card.c-undetermined{border-left:3px solid var(--amber)}
.card.c-excluded{border-left:3px solid var(--gray); opacity:.75}
.card h3{font-size:1rem; font-weight:600; margin-bottom:.5rem}
.card h3 .no{color:var(--muted); font-weight:500; margin-right:.35rem}
.ev{
  font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:.82rem;
  background:var(--gray-bg); border-radius:6px; padding:.5rem .7rem;
  margin-bottom:.75rem; white-space:pre-wrap; word-break:break-word;
}
dl{display:grid; grid-template-columns:3.6rem 1fr; gap:.35rem .8rem; font-size:.9rem}
dt{color:var(--muted); font-size:.82rem; padding-top:.1rem}
dd{min-width:0}
.badge{
  display:inline-block; font-size:.72rem; padding:.1rem .45rem; border-radius:5px;
  background:var(--amber-bg); color:var(--amber); margin-left:.4rem; vertical-align:1px;
}
.ask{
  background:var(--card); border:1px solid var(--border); border-radius:var(--radius);
  padding:1rem 1.1rem; margin-bottom:1.5rem;
}
.ask p{font-size:.9rem; margin-bottom:.6rem}
.chips{display:flex; gap:.5rem; flex-wrap:wrap}
.chip{
  background:var(--bg); border:1px solid var(--border); border-radius:999px;
  padding:.35rem .85rem; font-size:.85rem;
}
.chip:hover{border-color:var(--blue); text-decoration:none}
.sym-grid{display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:.85rem}
.sym{display:block; color:inherit}
.sym:hover{text-decoration:none; border-color:var(--blue)}
.sym h3{font-size:.98rem}
.sym p{color:var(--muted); font-size:.85rem; margin-top:.3rem}
ul{list-style:none} ul li{padding-left:1rem; position:relative; font-size:.88rem; color:var(--muted)}
ul li::before{content:"·"; position:absolute; left:.25rem}
footer{
  max-width:860px; margin:2.5rem auto 0; padding:1.25rem;
  color:var(--muted); font-size:.8rem; border-top:1px solid var(--border);
}
.empty{color:var(--muted); font-size:.9rem}
.warn{color:var(--red); font-size:.88rem}
input[type=file]{font-size:.9rem; color:var(--muted); max-width:100%}
#photo-result{margin-top:.85rem}
.photo-card{background:var(--bg); border:1px solid var(--border); border-radius:10px; padding:.85rem 1rem}
.photo-card .obs{font-size:.86rem; color:var(--muted); margin:.3rem 0 .65rem}
.photo-card .conf{font-size:.8rem; color:var(--muted)}
@media (max-width:520px){
  dl{grid-template-columns:1fr; gap:.15rem}
  dt{padding-top:.5rem}
}
"""


def esc(text) -> str:
    return html.escape(str(text), quote=True)


def _page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<style>{CSS}</style>
</head>
<body>
<header><div class="inner">
  <h1><a href="/" style="color:inherit">bambu-doctor</a></h1>
  <span class="sub">Bambu Lab 打印问题顾问</span>
</div></header>
<main>{body}</main>
<footer>
  不配 AI 时全部本地分析；看图会把照片发给你自己配置的服务商。
  判断由确定性规则给出，AI 只负责识别照片里的现象，不参与推理。
</footer>
</body>
</html>"""


# ------------------------------------------------------------------ 页面

PHOTO_BLOCK = """
<div class="picker">
  <label>或者：拍/传一张照片，让 AI 判断是什么问题</label>
  <input type="file" accept="image/*" onchange="pickPhoto(this)">
  <div id="photo-result"></div>
</div>
<script>
function escHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
async function pickPhoto(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  const box = document.getElementById('photo-result');
  box.innerHTML = '<p class="empty">正在识别…（第一次调用要几秒）</p>';
  try {
    const b64 = await new Promise(function (resolve, reject) {
      const reader = new FileReader();
      reader.onload = function () { resolve(String(reader.result).split(',')[1]); };
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
    const resp = await fetch('/identify', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({image: b64, mime: file.type || 'image/jpeg'})
    });
    const data = await resp.json();
    if (!data.ok) {
      box.innerHTML = '<p class="warn">' + escHtml(data.error) + '</p>';
      return;
    }
    const seen = (data.observed || []).map(escHtml).join('；');
    if (!data.known) {
      box.innerHTML = '<div class="photo-card"><b>没认出是哪种问题</b>'
        + '<p class="obs">看到的：' + seen + '</p>'
        + '<p class="warn">' + escHtml(data.note) + '</p>'
        + '<p class="empty">换一张更清楚的照片（对准出问题的部位、别隔太远、光线足一些），'
        + '或者直接在上面选一个最接近的症状。</p></div>';
      return;
    }
    box.innerHTML = '<div class="photo-card"><b>照片识别：' + escHtml(data.name) + '</b>'
      + '<p class="obs">看到的：' + seen + '</p>'
      + '<p class="conf">置信度 ' + escHtml(data.confidence)
      + (data.alternative_name ? ('　也可能是：' + escHtml(data.alternative_name)) : '')
      + (data.note ? ('　' + escHtml(data.note)) : '') + '</p>'
      + '<p style="margin-top:.65rem"><a class="chip" href="/diagnose?symptom='
      + encodeURIComponent(data.symptom) + '&profile='
      + encodeURIComponent(data.profile) + '">看诊断结果 →</a></p></div>';
  } catch (err) {
    box.innerHTML = '<p class="warn">识别失败：' + escHtml(err) + '</p>';
  }
}
</script>
"""


def render_home(index: ProfileIndex, kb: KnowledgeBase, selected: str = "") -> str:
    filaments = index.profiles(origin=ORIGIN_USER, kind="filament")
    if not filaments:
        return _page(
            "bambu-doctor",
            '<p class="empty">没有找到自定义耗材 profile。'
            "先去 Bambu Studio 里存一个自己的耗材档，再刷新这一页。</p>",
        )

    options = "".join(
        f'<option value="{esc(p.name)}"{" selected" if p.name == selected else ""}>'
        f"{esc(p.name)}</option>"
        for p in filaments
    )

    default_profile = selected or filaments[0].name
    cards = "".join(
        f'<div class="card sym"><h3>{esc(s.name)}</h3>'
        f"<p>{esc(s.description)}</p>"
        f'<p style="margin-top:.6rem">'
        f'<a class="chip" href="/diagnose?symptom={quote(s.id)}'
        f'&amp;profile={quote(default_profile)}">查出原因</a></p></div>'
        for s in sorted(kb.symptoms.values(), key=lambda x: x.id)
    )

    ask = ""

    return _page(
        "bambu-doctor",
        f"""<p class="lead">描述你遇到的问题，查出<b>在你的参数里</b>哪一条原因成立、该改哪个值。</p>
<div class="picker">
  <label for="pf">用哪份耗材档判断</label>
  <select id="pf" onchange="location.href='/?profile='+encodeURIComponent(this.value)">
    {options}
  </select>
</div>
{ask}
{PHOTO_BLOCK}
<h2 class="group g-neutral"><span class="dot"></span>你遇到了什么问题<span class="count">
{len(kb.symptoms)} 个已收录症状</span></h2>
<div class="sym-grid">{cards}</div>""",
    )


_GROUPS = [
    (STATUS_CONFIRMED, "g-confirmed", "c-confirmed", "判断成立", "下面这些原因，在你的参数里是成立的"),
    (STATUS_MANUAL, "g-manual", "c-manual", "需要你自己查", "工具读不到，但你两分钟能确认"),
    (STATUS_UNDETERMINED, "g-undetermined", "c-undetermined", "数据不足", "想判但缺参数"),
    (STATUS_EXCLUDED, "g-excluded", "c-excluded", "已排除", "这些解释在你这里不成立，不用试了"),
]

_FIELDS = [
    ("mechanism", "机理"),
    ("manual_check", "怎么查"),
    ("action", "动作"),
    ("cost", "代价"),
    ("verify", "验证"),
    ("note", "注意"),
]


def render_result(report, kb: KnowledgeBase) -> str:
    blocks = []
    for status, gcls, ccls, title, hint in _GROUPS:
        group = report.group(status)
        if not group:
            continue
        cards = []
        for i, verdict in enumerate(group, 1):
            cause = verdict.cause
            badge = (
                '<span class="badge">置信度较低</span>'
                if cause.confidence == "low" else ""
            )
            evidence = "".join(
                f'<div class="ev">{esc(text)}</div>' for text in verdict.evidence
            )
            rows = ""
            # 「已排除」只给依据就够——把动作/代价列出来反而像在推荐它
            if status != STATUS_EXCLUDED:
                for attr, label in _FIELDS:
                    value = getattr(cause, attr, "")
                    if value:
                        rows += f"<dt>{esc(label)}</dt><dd>{esc(value)}</dd>"
            dl = f"<dl>{rows}</dl>" if rows else ""
            cards.append(
                f'<div class="card {ccls}"><h3><span class="no">{i}.</span>{esc(cause.name)}{badge}</h3>'
                f"{evidence}{dl}</div>"
            )
        blocks.append(
            f'<h2 class="group {gcls}"><span class="dot"></span>{esc(title)}'
            f'<span class="count">{len(group)} 项 · {esc(hint)}</span></h2>' + "".join(cards)
        )

    extra = ""
    if report.symptom.distinguishing:
        extra += (
            '<h2 class="group g-undetermined"><span class="dot"></span>对号入座</h2>'
            '<div class="card"><ul>'
            + "".join(f"<li>{esc(x)}</li>" for x in report.symptom.distinguishing)
            + "</ul></div>"
        )
    if report.notes:
        extra += '<div class="card"><ul>' + "".join(
            f"<li>{esc(n)}</li>" for n in report.notes
        ) + "</ul></div>"

    machine = esc(report.target_machine or "未识别")
    return _page(
        f"{report.symptom.name} · bambu-doctor",
        f"""<p class="lead"><a href="/">← 换个症状</a></p>
<h2 class="group g-confirmed" style="margin-top:.5rem">
  <span class="dot"></span>{esc(report.symptom.name)}</h2>
<p class="lead">耗材档「{esc(report.profile_name)}」｜机型 {machine}</p>
{''.join(blocks) if blocks else '<p class="empty">没有可显示的结果。</p>'}
{extra}""",
    )


def render_error(message: str) -> str:
    return _page("出错了 · bambu-doctor", f'<p class="lead">{esc(message)}</p><p><a href="/">← 回到首页</a></p>')


# ------------------------------------------------------------------ 服务

def make_handler(index: ProfileIndex, kb: KnowledgeBase):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: str, code: int = 200) -> None:
            payload = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _send_json(self, payload: dict, code: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802 - stdlib 接口
            parsed = urlparse(self.path)
            if parsed.path != "/identify":
                self._send_json({"ok": False, "error": "这个地址不接受 POST"}, 404)
                return

            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_UPLOAD_BYTES:
                self._send_json(
                    {"ok": False, "error": "上传内容为空或过大（照片请先压一下）"}, 413
                )
                return

            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                image = base64.b64decode(payload.get("image") or "")
            except Exception as exc:
                self._send_json({"ok": False, "error": f"请求格式不对：{exc}"}, 400)
                return

            profile = (parse_qs(parsed.query).get("profile") or [""])[0]
            try:
                sighting = identify(
                    image, kb, mime=payload.get("mime") or "image/jpeg"
                )
            except VisionError as exc:
                # 识别失败不是错误页——降级成"手动选症状"就是了
                self._send_json({"ok": False, "error": str(exc)})
                return

            known = sighting.is_known
            symptom = kb.get_symptom(sighting.symptom_id) if known else None
            alternative = (
                kb.get_symptom(sighting.alternative) if sighting.alternative else None
            )
            self._send_json({
                "ok": True,
                "known": known,
                "symptom": sighting.symptom_id,
                "name": symptom.name if symptom else "",
                "alternative_name": alternative.name if alternative else "",
                "observed": sighting.observed,
                "confidence": sighting.confidence,
                "note": sighting.note,
                "profile": profile,
            })

        def do_GET(self) -> None:  # noqa: N802 - stdlib 接口
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)

            try:
                if parsed.path == "/":
                    selected = (params.get("profile") or [""])[0]
                    self._send(render_home(index, kb, selected))
                    return

                if parsed.path == "/diagnose":
                    symptom_id = (params.get("symptom") or [""])[0]
                    profile = (params.get("profile") or [""])[0] or None
                    answers = {
                        k: v[0] for k, v in params.items()
                        if k not in ("symptom", "profile") and v
                    }
                    report = diagnose(index, kb, symptom_id, profile_name=profile,
                                      answers=answers or None)
                    self._send(render_result(report, kb))
                    return

                if parsed.path == "/favicon.ico":
                    self._send("", 204)
                    return
            except ValueError as exc:
                self._send(render_error(str(exc)), 404)
                return
            except Exception as exc:  # pragma: no cover - 兜底，别让页面白屏
                self._send(render_error(f"内部错误：{exc}"), 500)
                return

            self._send(render_error("这个地址不存在。"), 404)

        def log_message(self, fmt, *args):  # 静音默认的每请求日志
            pass

    return Handler


def serve(index: ProfileIndex, kb: KnowledgeBase, host: str = "127.0.0.1",
          port: int = 8000, open_browser: bool = True) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(index, kb))
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{shown}:{server.server_address[1]}/"
    print(f"bambu-doctor 已启动：{url}")
    if host in ("0.0.0.0", "::"):
        print("（已对局域网开放——手机连同一个 Wi-Fi 就能访问，注意同网段的人也能打开）")
    print("按 Ctrl+C 停止。")
    if open_browser:
        webbrowser.open(url)
    return server
