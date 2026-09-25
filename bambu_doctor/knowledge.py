"""知识库：加载并结构化 symptoms.toml / causes.toml。

知识库是数据文件而非硬编码，目的是**让别人能通过 PR 补充规则**——
一个人的经验覆盖不了所有人的机器和材料。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"


class KnowledgeError(RuntimeError):
    """知识库文件读不到或格式不对。"""


def _load_toml(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise KnowledgeError(f"读不到知识库文件：{path}") from exc
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:
        try:
            import tomli  # 3.9/3.10
        except ModuleNotFoundError as exc:
            raise KnowledgeError(
                "读取知识库需要 Python 3.11+，或安装 tomli（pip install tomli）"
            ) from exc
        try:
            return tomli.loads(text)
        except Exception as exc:
            raise KnowledgeError(f"知识库文件解析失败：{path}\n  {exc}") from exc
    try:
        return tomllib.loads(text)
    except Exception as exc:
        raise KnowledgeError(f"知识库文件解析失败：{path}\n  {exc}") from exc


@dataclass
class Cause:
    """一条根因：可能导致某个症状的参数/物理原因。"""

    id: str
    name: str
    mechanism: str = ""
    verifiable: str = "manual"          # auto / manual
    confidence: str = "medium"          # high / medium / low
    action: str = ""
    cost: str = ""
    verify: str = ""
    note: str = ""
    manual_check: str = ""
    applies: dict = field(default_factory=dict)
    checks: list[dict] = field(default_factory=list)

    @property
    def is_auto(self) -> bool:
        """工具能不能用参数自动验证它。"""
        return self.verifiable == "auto" and bool(self.checks)


@dataclass
class BranchOption:
    answer: str
    causes: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class Branch:
    question: str
    options: list[BranchOption] = field(default_factory=list)


@dataclass
class Symptom:
    """用户能观察到的一个现象。"""

    id: str
    name: str
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    causes: list[str] = field(default_factory=list)
    distinguishing: list[str] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)
    branches: list[Branch] = field(default_factory=list)

    def causes_for(self, answers: dict[str, str] | None = None) -> list[str]:
        """
        取该症状的候选根因。

        answers: {问题原文: 用户选的那个 answer}。给分支问题时，
        只取被选中选项对应的根因；没给就退回症状的默认根因列表。
        """
        if not answers or not self.branches:
            return list(self.causes)

        picked: list[str] = []
        matched_any = False
        for branch in self.branches:
            chosen = answers.get(branch.question)
            if not chosen:
                continue
            for option in branch.options:
                if option.answer == chosen:
                    matched_any = True
                    picked.extend(option.causes)
        # 分支问题都没回答时退回默认
        return picked if matched_any else list(self.causes)


class KnowledgeBase:
    def __init__(self, symptoms: dict[str, Symptom], causes: dict[str, Cause],
                 source_dir: Path | None = None):
        self.symptoms = symptoms
        self.causes = causes
        self.source_dir = source_dir

    # ---------------------------------------------------------------- 加载

    @classmethod
    def load(cls, directory: Path | None = None) -> "KnowledgeBase":
        base = Path(directory) if directory else KNOWLEDGE_DIR

        raw_symptoms = _load_toml(base / "symptoms.toml").get("symptom", [])
        raw_causes = _load_toml(base / "causes.toml").get("cause", [])

        causes: dict[str, Cause] = {}
        for item in raw_causes:
            cause = Cause(
                id=item["id"],
                name=item.get("name", item["id"]),
                mechanism=item.get("mechanism", ""),
                verifiable=item.get("verifiable", "manual"),
                confidence=item.get("confidence", "medium"),
                action=item.get("action", ""),
                cost=item.get("cost", ""),
                verify=item.get("verify", ""),
                note=item.get("note", ""),
                manual_check=item.get("manual_check", ""),
                applies=item.get("applies", {}) or {},
                checks=list(item.get("checks", []) or []),
            )
            causes[cause.id] = cause

        symptoms: dict[str, Symptom] = {}
        for item in raw_symptoms:
            branches = [
                Branch(
                    question=b.get("question", ""),
                    options=[
                        BranchOption(
                            answer=o.get("answer", ""),
                            causes=list(o.get("causes", []) or []),
                            note=o.get("note", ""),
                        )
                        for o in b.get("option", []) or []
                    ],
                )
                for b in item.get("branch", []) or []
            ]
            symptom = Symptom(
                id=item["id"],
                name=item.get("name", item["id"]),
                description=item.get("description", ""),
                keywords=list(item.get("keywords", []) or []),
                causes=list(item.get("causes", []) or []),
                distinguishing=list(item.get("distinguishing", []) or []),
                questions=list(item.get("questions", []) or []),
                branches=branches,
            )
            symptoms[symptom.id] = symptom

        kb = cls(symptoms=symptoms, causes=causes, source_dir=base)
        kb.validate()
        return kb

    # ---------------------------------------------------------------- 校验

    def validate(self) -> None:
        """知识库自洽性检查——别人提 PR 写错引用时，这里会立刻报出来。"""
        problems: list[str] = []
        for sid, symptom in self.symptoms.items():
            for cid in symptom.causes:
                if cid not in self.causes:
                    problems.append(f"症状 {sid} 引用了不存在的根因 {cid}")
            for branch in symptom.branches:
                for option in branch.options:
                    for cid in option.causes:
                        if cid not in self.causes:
                            problems.append(f"症状 {sid} 的分支选项「{option.answer[:20]}」引用了不存在的根因 {cid}")
        for cid, cause in self.causes.items():
            if cause.verifiable == "auto" and not cause.checks:
                problems.append(f"根因 {cid} 标了 auto 但没有 checks")
        if problems:
            raise KnowledgeError("知识库自洽性检查失败：\n  " + "\n  ".join(problems))

    # ---------------------------------------------------------------- 查询

    def get_symptom(self, symptom_id: str) -> Symptom | None:
        return self.symptoms.get(symptom_id)

    def get_cause(self, cause_id: str) -> Cause | None:
        return self.causes.get(cause_id)

    def match_symptoms(self, text: str) -> list[tuple[Symptom, int]]:
        """
        按关键词命中数匹配症状，命中多的排前面。

        中文没有词边界，所以用子串匹配。M1 先用这个跑通；
        之后接 AI 视觉时，AI 直接给出症状 id，不依赖这里。
        """
        lowered = text.lower()
        hits: list[tuple[Symptom, int]] = []
        for symptom in self.symptoms.values():
            score = sum(1 for kw in symptom.keywords if kw.lower() in lowered)
            if score:
                hits.append((symptom, score))
        return sorted(hits, key=lambda pair: (-pair[1], pair[0].id))
