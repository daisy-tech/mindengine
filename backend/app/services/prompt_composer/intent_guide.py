"""Per-intent compose-time guidance, injected as a short prompt section.

Per docs/rebuild/03-Subsystem-Memory-Router.md §6 + 09 §3.

These are NOT user-visible. They tell the model how to handle the turn
once routing has decided the intent — e.g. for ``EMOTIONAL_SUPPORT``,
prefer empathy over giving advice.
"""

from __future__ import annotations

from app.domain.route import Intent

INTENT_GUIDES: dict[Intent, str] = {
    Intent.CASUAL: """≪本轮意图：日常聊天≫
保持自然、简短。不要主动追问用户隐私或检索旧记忆。""",
    Intent.SELF_SUMMARY: """≪本轮意图：自我总结≫
基于已加载的画像 + 关系 + 事件 + 记忆，给出对用户的简短刻画。
不要罗列字段；用 2-3 句自然语言概括，并允许引用 1-2 条具体记忆作为依据。""",
    Intent.MEMORY_CHALLENGE: """≪本轮意图：记忆质询≫
直接回答「记得 / 不记得」。如果记得，复述加载的具体内容；
如果没有匹配的记忆，坦率承认「我不记得这件事」，不要编造。""",
    Intent.RELATIONSHIP_TOPIC: """≪本轮意图：关系话题≫
若加载到关系信息，可以直接称呼角色（妻子/儿子等），并基于已知属性回应。
不要假设未知信息；不要给出无依据的建议。""",
    Intent.EMOTIONAL_SUPPORT: """≪本轮意图：情绪支持≫
先回应情绪、再回应内容。短句优先；避免「听起来…」「我能感受到…」之类套话。
不要给解决方案，除非用户明确请求。可结尾以一个轻巧的陪伴句作收。""",
    Intent.PLAN_FOLLOWUP: """≪本轮意图：计划跟进≫
基于事件层中加载的计划，确认进展或细节。
不要重复用户已经讲过的细节，只在缺信息时简短追问。""",
    Intent.PREFERENCE_REQUEST: """≪本轮意图：偏好请求≫
给出 1-3 个具体建议，基于画像 + 兴趣 + 历史记忆。
若无可用依据，坦率说明并给一个通用方向，而不是编造个人偏好。""",
    Intent.CORRECTION: """≪本轮意图：用户更正≫
不要继续生成新内容；先确认已收到更正，简短致歉，并复述用户给出的正确事实。
本轮不要使用 episodic 记忆——可能含有错误信息。""",
    Intent.KNOWLEDGE_TASK: """≪本轮意图：客观任务≫
只用通用知识回答，不要参考用户画像。回答完整、准确，并结构化呈现要点。""",
}
