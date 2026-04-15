from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Tuple

import requests


class ResponseAgent:
    def __init__(self) -> None:
        self._use_polisher = os.getenv("USE_RESPONSE_POLISHER", "true").lower() == "true"
        self._timeout_sec = int(os.getenv("RESPONSE_POLISH_TIMEOUT_SEC", "2"))
        self._api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self._api_url = self._normalize_api_url(os.getenv("LLM_API_URL", "https://api.openai.com/v1/chat/completions"))
        self._model = os.getenv("RESPONSE_POLISH_MODEL", os.getenv("REASON_MODEL", os.getenv("PARSER_MODEL", "gpt-4o-mini")))

    def _humanize(self, text: str) -> str:
        if not text:
            return text
        if text.endswith("。"):
            return text
        return f"{text}。"

    def _normalize_api_url(self, raw_url: str) -> str:
        if raw_url.endswith("/v1/chat/completions"):
            return raw_url
        if raw_url.endswith("/"):
            raw_url = raw_url[:-1]
        if raw_url.endswith("/v1"):
            return f"{raw_url}/chat/completions"
        if raw_url.startswith("http"):
            return f"{raw_url}/v1/chat/completions"
        return "https://api.openai.com/v1/chat/completions"

    def _extract_content(self, data: Dict) -> str:
        choices = data.get("choices", [])
        if not choices:
            return ""
        msg = choices[0].get("message", {})
        content = msg.get("content", "")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(str(item))
            content = "".join(parts)
        content = str(content).strip()
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        return content.strip()

    def _guard_reply(self, *, text: str, recs: List[Dict[str, object]]) -> str:
        out = self._humanize(text.strip())
        if not recs:
            return out
        top_name = str(recs[0].get("name", "")).strip()
        if top_name and top_name not in out:
            out = f"{top_name}：{out}"
        # Avoid overly long verbose outputs in chat bubble.
        if len(out) > 220:
            out = out[:220].rstrip("，；。 ") + "。"
        return out

    def _polish(self, *, base_text: str, mode: str, recs: List[Dict[str, object]]) -> str:
        if not self._use_polisher or not self._api_key or not base_text.strip():
            return base_text
        try:
            top_name = str((recs[0].get("name") if recs else "") or "")
            payload = {
                "mode": mode,
                "text": base_text,
                "top_name": top_name,
                "constraint": "更像真人说话，简洁自然，不添加新事实，不改变推荐结论。",
            }
            req = {
                "model": self._model,
                "temperature": 0.2,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是中文外卖助手文案润色器。"
                            "请只做口语化润色，不新增事实，不更改店名/结论。"
                            "输出1-2句，长度不超过120字。"
                        ),
                    },
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
            }
            headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
            resp = requests.post(self._api_url, headers=headers, json=req, timeout=self._timeout_sec)
            resp.raise_for_status()
            text = self._extract_content(resp.json())
            return text or base_text
        except Exception:
            return base_text

    def finalize_reply(self, *, base_text: str, mode: str, recs: List[Dict[str, object]], fast_mode: bool = False) -> str:
        text = base_text
        if mode in ("recommend", "qa") and not fast_mode:
            text = self._polish(base_text=base_text, mode=mode, recs=recs)
        return self._guard_reply(text=text, recs=recs)

    def build_compare_cards(self, recs: List[Dict[str, object]]) -> List[Dict[str, object]]:
        if len(recs) < 2:
            return []
        cards: List[Dict[str, object]] = []
        for r in recs[:2]:
            ev = str(r.get("reason", ""))
            cards.append(
                {
                    "name": r.get("name", "-"),
                    "score": r.get("score", "-"),
                    "top_dish": (r.get("recommended_dishes") or [""])[0],
                    "reason_hint": ev[:40] + ("..." if len(ev) > 40 else ""),
                }
            )
        return cards

    def extract_known_constraints(self, query: str) -> str:
        q = query or ""
        keys = []
        if "预算" in q:
            keys.append("预算")
        if any(k in q for k in ["公里", "附近", "近一点", "更近"]):
            keys.append("距离")
        if any(k in q for k in ["送达", "快一点", "分钟"]):
            keys.append("时效")
        if any(k in q for k in ["清淡", "不油腻", "酸辣", "麻辣", "高蛋白", "减脂"]):
            keys.append("口味/饮食偏好")
        return "、".join(keys) if keys else "预算、口味、距离、时效"

    def _health_score(self, rec: Dict[str, object]) -> float:
        text = " ".join(
            [
                str(rec.get("name", "")),
                " ".join(str(x) for x in (rec.get("recommended_dishes") or [])),
                str(rec.get("reason", "")),
            ]
        )
        score = 0.0
        if any(k in text for k in ["轻食", "沙拉", "清淡", "不油腻", "高蛋白", "暖胃", "粥", "汤面"]):
            score += 2.0
        if any(k in text for k in ["炸鸡", "汉堡", "奶茶", "烧烤", "重口", "油炸"]):
            score -= 2.0
        score += max(0.0, 5.0 - float(rec.get("score", 0.0))) * 0.05
        return score

    def answer_question(self, *, text: str, recs: List[Dict[str, object]]) -> Tuple[str, List[Dict[str, object]]]:
        if not recs:
            if any(k in text for k in ["健康", "热量", "减脂"]):
                return "如果你在控卡，优先思路是：少油炸、少酱料、饮料无糖、主食减量加蛋白。你给我一个具体店名，我可以按那家给你更细建议。", []
            return "你这个问题我可以答，但我现在没有可解释的推荐结果。先给我一句点餐需求，我先帮你排一轮。", []

        t = (text or "").strip()
        if any(k in t for k in ["哪个更健康", "哪家更健康", "哪个热量更低", "哪家热量更低", "哪个更适合减脂", "哪家更适合减脂"]):
            ranked = sorted(recs[:3], key=self._health_score, reverse=True)
            best = ranked[0]
            runner = ranked[1] if len(ranked) > 1 else None
            best_name = best.get("name", "这家")
            best_dish = (best.get("recommended_dishes") or [""])[0]
            dish_hint = f"比如先点“{best_dish}”" if best_dish else "优先点相对清淡、少炸少酱的餐品"
            compare_hint = ""
            if runner:
                compare_hint = f"，相比 {runner.get('name', '另一家')} 会更适合控油控热量"
            return (
                self._humanize(
                    f"如果你更在意健康或减脂，我会先偏向 {best_name}{compare_hint}：{dish_hint}。"
                    "整体思路是优先清淡、高蛋白、少油炸；如果你要，我也可以按“减脂友好”给你重排一版"
                ),
                self.build_compare_cards(ranked) if len(ranked) > 1 else [],
            )
        if any(k in t for k in ["健康吗", "健不健康", "热量高吗", "适合减脂吗", "会胖吗"]):
            top = recs[0]
            name = top.get("name", "这家")
            dish = (top.get("recommended_dishes") or [""])[0]
            dish_part = f"比如优先点“{dish}”" if dish else "优先选相对清淡的餐品"
            return (
                self._humanize(
                    f"{name} 不算典型的轻食店，但也能吃得相对健康：{dish_part}，"
                    "少酱少糖、饮料换无糖、避开高油炸组合会更稳。"
                    "如果你愿意，我可以马上按“减脂友好”给你重排一版"
                ),
                [],
            )
        if any(k in t for k in ["哪个", "哪家", "第一", "top1", "top 1"]):
            top = recs[0]
            dish = (top.get("recommended_dishes") or [""])[0]
            dish_text = f"，推荐菜可以先试“{dish}”" if dish else ""
            return self._humanize(f"如果只选一个，我会先推 {top.get('name', '这家')}，核心原因是：{top.get('reason', '')}{dish_text}"), []

        if any(k in t for k in ["为什么", "理由", "解释"]):
            lines = []
            for i, r in enumerate(recs[:3], start=1):
                lines.append(f"{i}. {r.get('name', '-')}：{r.get('reason', '-')}")
            return "我按你刚才的条件这样判断的：\n" + "\n".join(lines), []

        if any(k in t for k in ["对比", "区别", "差别"]):
            if len(recs) < 2:
                return self._humanize(f"目前只有 {recs[0].get('name', '1家')} 可比。你可以让我“再给一个备选”，我会补一条方便你横向比较"), []
            a = recs[0]
            b = recs[1]
            return self._humanize(
                f"{a.get('name', 'A店')} 和 {b.get('name', 'B店')} 对比："
                f"前者更偏“{a.get('reason', '')[:24]}...”，后者更偏“{b.get('reason', '')[:24]}...”。"
                "你更在意价格、距离还是口味，我可以据此二选一"
            ), self.build_compare_cards(recs)

        return "我理解你在追问解释。基于上一轮结果，我可以给你“最优一家原因”或“前两家对比”，你回复“解释top1”或“对比前两家”即可。", []

    def build_recommend_reply(self, *, query: str, recs: List[Dict[str, object]]) -> str:
        if not recs:
            return "我按你的要求筛了一轮，暂时没找到特别合适的店。你可以放宽一点预算或距离，我马上再给你重排。"
        top = recs[0]
        top_name = top.get("name", "这家店")
        top_reason = top.get("reason", "")
        top_dishes = top.get("recommended_dishes", []) or []
        dish_text = f"，可以先试试“{top_dishes[0]}”" if top_dishes else ""
        return self._humanize(f"按你这轮需求，我先给你排了更稳的一家：{top_name}{dish_text}。{top_reason}")

    def suggestions(self, *, mode: str, recs: List[Dict[str, object]]) -> List[str]:
        if mode == "qa":
            return ["解释top1", "对比前两家", "那换个更近的"]
        if mode == "recommend" and recs:
            top_name = recs[0].get("name", "这家")
            return [f"为什么优先推荐 {top_name}？", "换个更近的", "预算再降到30以内"]
        return ["预算30以内，清淡不油腻，送达快一点", "我想吃热乎一点的正餐", "给我两个对比明显的选择"]

    def handle_smalltalk(self, *, text: str, has_last_query: bool) -> str:
        t = (text or "").strip()
        if any(k in t for k in ["谢谢", "多谢", "辛苦了"]):
            if has_last_query:
                return "不客气，我在这。要不要我基于刚才那一轮再给你一个“更近”或“更便宜”的版本？"
            return "不客气。你直接告诉我预算、口味和送达时效，我就能开始帮你点餐。"
        if any(k in t for k in ["你好", "哈喽", "在吗"]):
            return "在的。你可以直接说“预算+口味+时效”，我会按外卖场景给你连续推荐。"
        return "我在。你可以继续追问，比如“换个更近的”或“解释为什么推荐这家”。"

