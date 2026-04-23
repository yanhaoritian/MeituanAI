from __future__ import annotations

from app.agents.contracts import AgentDecision


class OrchestratorAgent:
    def _normalize(self, text: str) -> str:
        return "".join((text or "").strip().lower().split())

    def decide(self, *, message: str, has_last_query: bool) -> AgentDecision:
        text = (message or "").strip()
        compact = self._normalize(text)
        if not text:
            return AgentDecision(mode="fallback", reason="empty_message", confidence=1.0)

        reset_markers = ["新需求", "重新开始", "换个话题", "不聊点餐了", "清空会话", "从头开始"]
        if any(k in text for k in reset_markers):
            return AgentDecision(mode="reset", reason="reset_marker", confidence=1.0)

        question_markers = [
            "?",
            "？",
            "为什么",
            "咋",
            "怎么",
            "哪个",
            "哪家",
            "区别",
            "对比",
            "推荐理由",
            "解释下",
            "解释top1",
            "解释 top1",
            "top1为什么",
            "top 1为什么",
            "健康吗",
            "健不健康",
            "热量高吗",
            "适合减脂吗",
            "会胖吗",
            "可以吗",
            "合适吗",
        ]
        qa_commands = [
            "解释top1",
            "解释top2",
            "解释第一家",
            "top1为什么",
            "top2为什么",
            "对比前两家",
            "对比一下",
            "哪个好一点",
            "哪个更好",
            "哪个更健康",
            "哪个热量更低",
            "哪个更适合减脂",
        ]
        has_question_intent = any(k in text for k in question_markers) or any(k in compact for k in qa_commands)
        optimize_cues = ["更近", "更便宜", "更快", "预算", "距离", "送达", "不吃", "不要", "换", "再来", "改成", "降到"]
        has_optimize_intent = any(k in text for k in optimize_cues)
        if has_question_intent and has_last_query and has_optimize_intent:
            return AgentDecision(mode="mixed_intent", reason="qa_plus_optimize", confidence=0.9)
        if has_question_intent:
            return AgentDecision(mode="qa", reason="question_marker", confidence=0.95)

        smalltalk_exact = {"你好", "在吗", "哈喽", "谢谢", "多谢", "辛苦了", "再见", "收到", "好的", "ok"}
        if compact in smalltalk_exact:
            return AgentDecision(mode="smalltalk", reason="smalltalk_marker", confidence=0.9)

        order_keywords = [
            "吃",
            "饭",
            "午餐",
            "晚餐",
            "预算",
            "附近",
            "推荐",
            "送达",
            "口味",
            "不油腻",
            "清淡",
            "换一家",
            "换一个",
            "别要",
            "不要",
            "来点",
        ]
        if any(k in text for k in order_keywords):
            return AgentDecision(mode="recommend", reason="order_marker", confidence=0.9)

        if has_last_query and len(text) <= 60 and any(k in text for k in optimize_cues):
            return AgentDecision(mode="recommend", reason="followup_optimize", confidence=0.85)

        return AgentDecision(mode="fallback", reason="insufficient_constraints", confidence=0.7)

