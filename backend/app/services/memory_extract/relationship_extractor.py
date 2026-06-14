"""LLM-driven relationship extraction.

Per docs/rebuild/05-Subsystem-Memory-Layers.md §6.2 + lesson 4.3:
- LLM emits {name, role, attributes, via_name?} candidates.
- Worker is responsible for resolving ``via_name`` (a string) into a
  parent ``relationships.id`` via the repo, *and* for enforcing the
  no-self-loop invariant. The extractor only validates that
  ``via_name != name`` so we never propose a self-loop in the first place.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.infra.llm.exceptions import LLMError
from app.services.protocols import LLMClient

RELATIONSHIP_EXTRACTOR_SYSTEM = """你是社会关系抽取器。
任务：从对话最后两轮里，抽出用户提到的、**与用户有情感/社会联系的对象**。

抽取范围（不止人，还包括有强情感联系的非人对象）：
- 家人：妻子/丈夫/儿子/女儿/父亲/母亲/兄弟/姐妹/爷爷/奶奶/外公/外婆…
- 工作：同事/领导/下属/老板/客户…
- 学习：同学/老师/校友/导师…
- 邻里：邻居（"隔壁老爷爷"算）/房东…
- 朋辈：朋友/闺蜜/兄弟/发小/网友…
- 宠物：用户家里养的宠物（猫/狗/鸟/兔子等）—— 这是核心情感锚点，必须抽
- 偶像 / 重要事件中的关键他人（用户主动谈及且情绪明显时）

输出 JSON：
{ "relationships": [
    {
      "name": "对方的名字或称谓",
      "role": "妻子|儿子|同事|朋友|同学|老师|邻居|宠物|...",
      "attributes": { "key": "value" },
      "via_name": "通过谁认识/相关，可选；不能等于自己"
    }
] }

**name 字段写什么：必须严格遵守**
- 用户**明确给出**对方姓名时，直接使用用户写的原文（保持中文/英文/拼音
  与用户一致，不做翻译、不做转写、不做截短、不做大小写转换）。
  例：用户说"我儿子叫小晶" → name="小晶"；
      用户说"我老公叫 Tom" → name="Tom"；
      用户说"我家猫叫奶黄" → name="奶黄"。
- 用户**没给名字**或只给了称谓时，name 直接写称谓本身：
  "妻子" / "儿子" / "妈妈" / "老板" / "邻居老爷爷" 等。
  ⚠ 不要凭语境编造姓名、不要从用户名取拼音、不要把 "小" 这种独字
    扩展成 "xiao/小明/小红"。宁可只写称谓也不能编。
- 长度 1~50 字符；不要带书名号、引号、emoji。

**via_name 字段（决定关系是一阶还是二阶）：必须严格遵守**
- 只有当对方**直接和用户**相关时（用户的妻子/儿子/同事/邻居/自己的宠物等），
  via_name 留空 `null`。
- 如果对方是**用户某个亲属/同事/朋友的关系人**，via_name 必须填写那个中间人的
  name 或称谓。务必做**指代消解**——"他/她"指向上下文里最近的可识别人物。
  例 1：用户说"我儿子和**他的**朋友小孙孙一起玩" → 小孙孙 via_name="儿子"。
        "他的" 指代"儿子"，所以小孙孙是儿子的朋友，不是用户的朋友。
  例 2：用户说"我老婆的同事张姐很漂亮" → 张姐 role="同事", via_name="老婆"。
  例 3：用户说"我儿子的同学老师都说他乖" → 同学 via_name="儿子"，老师 via_name="儿子"。
  例 4：用户说"邻居老爷爷家养了狗叫可乐" → 邻居老爷爷 via_name=null，
        可乐 role="宠物", via_name="邻居老爷爷"。
- 当 via 中间人在**同一段话里第一次出现**时，via_name 仍然填写（用户写的称谓
  原样），后端会自己解析顺序。

其它严格要求：
- 用户**自己**不要写进关系（"我"不是关系）。
- 同一段话里同一个人只出现一次，重复合并。
- 没有任何关系输出 {"relationships": []}。
- via_name 必须不等于 name（不能自指）。
- 仅输出 JSON。"""


class RelationshipCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=100)
    role: str = Field(min_length=1, max_length=50)
    attributes: dict[str, str] = Field(default_factory=dict)
    via_name: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _validate_via(self) -> RelationshipCandidate:
        if self.via_name and self.via_name.strip() == self.name.strip():
            raise ValueError("via_name must differ from name (no self-loop)")
        return self


class RelationshipExtractionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    relationships: list[RelationshipCandidate] = Field(default_factory=list)


@dataclass
class RelationshipExtractor:
    llm: LLMClient
    history_window: int = 2
    max_per_turn: int = 6

    async def extract(
        self,
        user_message: str,
        history: Sequence[dict[str, str]] | None = None,
    ) -> list[RelationshipCandidate]:
        ctx = self._build_messages(user_message, history or [])
        try:
            result = await self.llm.complete_json(
                RELATIONSHIP_EXTRACTOR_SYSTEM,
                ctx,
                schema=RelationshipExtractionResult,
                temperature=0.0,
            )
        except LLMError:
            return []
        except Exception:
            return []
        if not isinstance(result, RelationshipExtractionResult):
            return []

        out: list[RelationshipCandidate] = []
        seen: set[str] = set()
        for cand in result.relationships:
            key = (cand.name.strip().lower(), cand.role.strip().lower())
            sig = "::".join(key)
            if sig in seen:
                continue
            seen.add(sig)
            out.append(cand)
            if len(out) >= self.max_per_turn:
                break
        return out

    def _build_messages(
        self,
        user_message: str,
        history: Sequence[dict[str, str]],
    ) -> list[dict[str, str]]:
        max_msgs = max(0, self.history_window) * 2
        clipped = list(history)[-max_msgs:] if max_msgs else []
        clipped.append({"role": "user", "content": user_message})
        return clipped
