"""视觉识别：照片 → 症状 ID。

这是 AI 在整套系统里的**唯一**职责：把照片翻译成一个预定义的症状 ID。
它不给建议、不做推理——那些交给知识库和用户的真实参数（理由见 DESIGN.md）。

三条硬规则：
  1. 模型编出来的症状 id 一律作废（知识库是唯一的事实来源）
  2. 尺寸偏差这类照片看不出的症状，不让模型猜
  3. 认不出来就说 unknown，不许硬凑

接口按 OpenAI 兼容格式写——换服务商只改 base_url / model。
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
API_KEY_ENV = ("BAMBU_DOCTOR_API_KEY", "DEEPSEEK_API_KEY")
MAX_IMAGE_BYTES = 12 * 1024 * 1024

# 照片上根本看不出来的症状，不该让视觉模型去猜
NOT_VISIBLE = {"dimension-off"}


class VisionError(Exception):
    """识别失败（没密钥、网络、返回格式）。

    调用方应当**降级到手动选症状**，而不是把错误甩给用户。
    """


@dataclass
class Sighting:
    symptom_id: str = "unknown"
    observed: list[str] = field(default_factory=list)
    confidence: str = "low"
    alternative: str = ""
    note: str = ""
    model: str = ""
    raw: str = ""

    @property
    def is_known(self) -> bool:
        return self.symptom_id not in ("", "unknown")


def _dotenv_candidates() -> list[Path]:
    """去哪里找 key：当前目录的 .env、用户主目录的 .bambu-doctor.env。"""
    return [Path.cwd() / ".env", Path.home() / ".bambu-doctor.env"]


def _read_dotenv(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() in API_KEY_ENV:
            found = value.strip().strip('"').strip("'")
            if found:
                return found
    return ""


def api_key_from_env() -> str:
    """先找环境变量，再退一步找 .env 文件（双击启动时不会有环境变量）。"""
    for name in API_KEY_ENV:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    for path in _dotenv_candidates():
        found = _read_dotenv(path)
        if found:
            return found
    return ""


def build_prompt(kb) -> str:
    lines = [
        "你在帮人识别 3D 打印件的照片里有什么问题。",
        "你只需要说出**你看到了什么**，不要给怎么修的建议。",
        "",
        "从下面这些症状里选出最接近的一个：",
        "",
    ]
    for symptom in sorted(kb.symptoms.values(), key=lambda s: s.id):
        if symptom.id in NOT_VISIBLE:
            continue
        lines.append(f"- {symptom.id}：{symptom.name}——{symptom.description}")
        if symptom.distinguishing:
            lines.append(f"  区别要点：{'；'.join(symptom.distinguishing)}")

    lines += [
        "",
        "注意：",
        "- 尺寸偏差这类问题从照片上看不出来，不要凭想象选。",
        "- 只输出一个 JSON 对象，不要写任何解释、不要用 markdown 代码块。",
        "",
        '{"symptom": "<上面列出的 id，或 unknown>",',
        ' "observed": ["你在图里看到的具体特征", "..."],',
        ' "confidence": "high|medium|low",',
        ' "alternative": "<第二可能的 id，没有就空字符串>",',
        ' "note": "<看不清的地方，或需要用户补充什么>"}',
        "",
        "observed 里只写**图里实际看得到的**，不要写推测。",
        "如果照片跟 3D 打印无关、太模糊、或者拿不准，symptom 就填 unknown，",
        "confidence 填 low —— 认错比承认认不出更糟。",
    ]
    return "\n".join(lines)


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if value:
        return [str(value).strip()]
    return []


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise VisionError(f"模型回复里找不到 JSON：{text[:200]}")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise VisionError(f"模型返回的 JSON 解析失败：{exc}") from exc


def parse_reply(text: str, kb) -> Sighting:
    data = _extract_json(text)
    symptom_id = str(data.get("symptom") or "").strip()

    # 模型可能编一个不存在的 id —— 校验，编的当"认不出"处理
    if symptom_id and symptom_id != "unknown" and kb.get_symptom(symptom_id) is None:
        return Sighting(
            symptom_id="unknown",
            observed=_as_list(data.get("observed")),
            confidence="low",
            note=f"模型给出了知识库里没有的症状 id「{symptom_id}」，已忽略",
            raw=text,
        )

    alternative = str(data.get("alternative") or "").strip()
    if alternative and kb.get_symptom(alternative) is None:
        alternative = ""

    confidence = str(data.get("confidence") or "low").lower()
    if confidence not in ("high", "medium", "low"):
        confidence = "low"

    return Sighting(
        symptom_id=symptom_id or "unknown",
        observed=_as_list(data.get("observed")),
        confidence=confidence,
        alternative=alternative,
        note=str(data.get("note") or "").strip(),
        raw=text,
    )


def identify(image_bytes: bytes, kb, *, api_key: str = "",
             base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL,
             mime: str = "image/jpeg", timeout: int = 90) -> Sighting:
    """把一张照片交给视觉模型，拿回一个症状 ID。"""
    key = api_key or api_key_from_env()
    if not key:
        raise VisionError(
            "没找到 API key。设一下环境变量 BAMBU_DOCTOR_API_KEY"
            "（或 DEEPSEEK_API_KEY），或者手动选症状。"
        )
    if not image_bytes:
        raise VisionError("图片是空的。")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise VisionError(
            f"图片太大了（{len(image_bytes) / 1024 / 1024:.1f} MB，上限 "
            f"{MAX_IMAGE_BYTES // 1024 // 1024} MB）。手机拍的原图可以先压一下再传。"
        )

    b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": build_prompt(kb)},
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }],
        # 关掉思考：识别现象不需要深思，而思考会吃掉大量 token（实测占比可达 90%）
        "reasoning_effort": "none",
        "max_tokens": 1200,
        "temperature": 0.2,
    }

    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise VisionError(f"接口返回 {exc.code}：{detail}") from exc
    except Exception as exc:
        raise VisionError(f"请求失败：{exc}") from exc

    choices = body.get("choices") or []
    content = ""
    if choices:
        content = choices[0].get("message", {}).get("content") or ""
    if not content.strip():
        # 实测过的坑：max_tokens 给小了（或被思考吃光）时接口返回 200 但正文是空的。
        # 这种情况绝不能当成"模型说没问题"。
        raise VisionError(
            f"模型返回了空正文（大概率是 token 不够）。usage={body.get('usage', {})}"
        )

    sighting = parse_reply(content, kb)
    sighting.model = model
    return sighting
