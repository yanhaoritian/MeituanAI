import os
from typing import Any, Dict, List

import requests
import streamlit as st
from dotenv import load_dotenv

try:
    from streamlit_js_eval import get_geolocation
except Exception:  # pragma: no cover
    get_geolocation = None

load_dotenv()
api_base = os.getenv("BACKEND_API_BASE", "http://127.0.0.1:8000")
timeout_sec = int(os.getenv("FRONTEND_TIMEOUT_SEC", "20"))

st.set_page_config(page_title="美团 AI 点餐助手", page_icon="🍱", layout="wide")
st.title("🍱 美团 AI 点餐助手")
st.caption("直接聊天说需求，我会连续帮你筛店、解释和微调。")


def call_chat(payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    resp = requests.post(f"{api_base.rstrip('/')}/v1/chat", json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def call_geocode(address: str, city: str, timeout: int) -> Dict[str, Any]:
    resp = requests.get(f"{api_base.rstrip('/')}/v1/location/geocode", params={"address": address, "city": city}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _render_chat_recommendations(recs: List[Dict[str, Any]]) -> None:
    if not recs:
        return
    st.markdown("**这轮推荐：**")
    for i, item in enumerate(recs[:5], start=1):
        dish = (item.get("recommended_dishes") or [""])[0]
        dish_text = f"｜推荐菜：{dish}" if dish else ""
        st.markdown(f"{i}. {item.get('name', '-')}{dish_text}")


def _render_compare_cards(compare_cards: List[Dict[str, Any]]) -> None:
    if not compare_cards:
        return
    st.markdown("**对比卡片：**")
    cols = st.columns(len(compare_cards))
    for idx, card in enumerate(compare_cards):
        with cols[idx]:
            st.markdown(f"**{card.get('name', '-')}**")
            st.caption(f"推荐分：{card.get('score', '-')}")
            if card.get("top_dish"):
                st.caption(f"推荐菜：{card.get('top_dish')}")
            st.caption(f"摘要：{card.get('reason_hint', '-')}")


def _send_chat_message(message: str, *, timeout_sec: int) -> None:
    st.session_state["dialog_messages"].append({"role": "user", "content": message, "recs": []})
    with st.chat_message("user"):
        st.markdown(message)
    try:
        chat_payload: Dict[str, Any] = {
            "user_id": st.session_state.get("chat_user_id", "u_001"),
            "message": message,
        }
        if st.session_state.get("chat_session_id"):
            chat_payload["session_id"] = st.session_state["chat_session_id"]
        if st.session_state.get("geo_ready"):
            chat_payload["location"] = {
                "lat": float(st.session_state["geo_lat"]),
                "lng": float(st.session_state["geo_lng"]),
            }
        with st.chat_message("assistant"):
            thinking = st.empty()
            thinking.markdown("🤔 思考中...")
            chat_result = call_chat(chat_payload, timeout_sec)
            thinking.empty()
            st.markdown(chat_result.get("assistant_reply", ""))
            _render_chat_recommendations(chat_result.get("recommendations", []))
            _render_compare_cards(chat_result.get("compare_cards", []))
        st.session_state["chat_session_id"] = chat_result.get("session_id", st.session_state.get("chat_session_id", ""))
        st.session_state["last_followup_suggestions"] = chat_result.get("followup_suggestions", [])
        st.session_state["dialog_messages"].append(
            {
                "role": "assistant",
                "content": chat_result.get("assistant_reply", ""),
                "recs": chat_result.get("recommendations", []),
                "compare_cards": chat_result.get("compare_cards", []),
            }
        )
    except requests.RequestException as exc:
        with st.chat_message("assistant"):
            st.markdown(f"请求失败：{exc}")
        st.session_state["dialog_messages"].append(
            {"role": "assistant", "content": f"请求失败：{exc}", "recs": [], "compare_cards": []}
        )


if "dialog_messages" not in st.session_state:
    st.session_state["dialog_messages"] = [
        {
            "role": "assistant",
            "content": "你好，我是你的外卖点餐助手。你可以直接说预算、口味、距离和送达时效，我会连续帮你调。",
            "recs": [],
            "compare_cards": [],
        }
    ]
if "chat_session_id" not in st.session_state:
    st.session_state["chat_session_id"] = ""
if "last_followup_suggestions" not in st.session_state:
    st.session_state["last_followup_suggestions"] = []
if "geo_lat" not in st.session_state:
    st.session_state["geo_lat"] = 31.2304
if "geo_lng" not in st.session_state:
    st.session_state["geo_lng"] = 121.4737
if "chat_user_id" not in st.session_state:
    st.session_state["chat_user_id"] = "u_001"
if "geo_ready" not in st.session_state:
    st.session_state["geo_ready"] = False
if "geo_permission_requested" not in st.session_state:
    st.session_state["geo_permission_requested"] = False
if "geo_accuracy_m" not in st.session_state:
    st.session_state["geo_accuracy_m"] = None

with st.sidebar:
    st.markdown("### 个人设置")
    st.session_state["chat_user_id"] = st.text_input("你的昵称/ID", value=st.session_state["chat_user_id"])
    if st.button("🧹 开启新对话", use_container_width=True):
        st.session_state["chat_session_id"] = ""
        st.session_state["last_followup_suggestions"] = []
        st.session_state["dialog_messages"] = [
            {
                "role": "assistant",
                "content": "我们开始新一轮吧。你可以说“预算30以内，清淡不油腻，送达快一点”。",
                "recs": [],
                "compare_cards": [],
            }
        ]
        st.rerun()

    st.markdown("### 定位（推荐开启）")
    st.caption("进入页面后会自动请求浏览器定位权限。没有定位也可以先聊天，补充定位后推荐会更准。")
    if get_geolocation is None:
        st.warning("定位组件不可用，请执行 pip install -r requirements.txt。你仍然可以先聊天。")
    else:
        if not st.session_state["geo_permission_requested"] or not st.session_state["geo_ready"]:
            geo = get_geolocation()
            st.session_state["geo_permission_requested"] = True
            if geo and geo.get("coords"):
                st.session_state["geo_lat"] = float(geo["coords"]["latitude"])
                st.session_state["geo_lng"] = float(geo["coords"]["longitude"])
                st.session_state["geo_accuracy_m"] = float(geo["coords"].get("accuracy", 0.0) or 0.0)
                st.session_state["geo_ready"] = True

        if st.session_state["geo_ready"]:
            acc = st.session_state.get("geo_accuracy_m")
            if isinstance(acc, (int, float)) and acc > 300:
                st.warning(f"定位已获取，但精度较低（约{acc:.0f}米），建议重试一次定位或地址纠偏。")
            else:
                st.success("定位已就绪，后续推荐会更贴近你附近的店")
        else:
            st.info("定位暂未就绪。你可以先聊天，也可以在浏览器弹窗中点击“允许”来提升推荐准确度。")

        if st.button("🔁 重新请求定位权限", use_container_width=True):
            st.session_state["geo_permission_requested"] = False
            st.session_state["geo_ready"] = False
            st.rerun()

        st.markdown("#### 地址纠偏（定位不准时）")
        correction_addr = st.text_input("输入你当前地址/地标", value="", placeholder="例如：静安寺地铁站")
        correction_city = st.text_input("城市（可选）", value="")
        if st.button("🧭 用地址校准定位", use_container_width=True):
            if not correction_addr.strip():
                st.warning("请先输入地址或地标")
            else:
                try:
                    geo_fix = call_geocode(correction_addr.strip(), correction_city.strip(), timeout_sec)
                    if geo_fix.get("ok") and geo_fix.get("data"):
                        d = geo_fix["data"]
                        st.session_state["geo_lat"] = float(d.get("lat", st.session_state["geo_lat"]))
                        st.session_state["geo_lng"] = float(d.get("lng", st.session_state["geo_lng"]))
                        st.session_state["geo_ready"] = True
                        st.success(f"已校准到：{d.get('formatted_address', correction_addr)}")
                    else:
                        st.warning(f"地址校准失败：{geo_fix.get('status', 'unknown')}")
                except requests.RequestException:
                    st.warning("地址校准请求失败，请稍后重试")

    st.caption(f"当前坐标：{st.session_state['geo_lat']:.6f}, {st.session_state['geo_lng']:.6f}")

st.subheader("对话模式")
for msg in st.session_state.get("dialog_messages", []):
    with st.chat_message(msg.get("role", "assistant")):
        st.markdown(msg.get("content", ""))
        _render_chat_recommendations(msg.get("recs", []))
        _render_compare_cards(msg.get("compare_cards", []))

if not st.session_state.get("geo_ready", False):
    st.info("你可以先直接说预算、口味和时效；如果补充定位，我会把附近店铺排得更准。")

chat_input = st.chat_input(
    "比如：预算30以内，清淡不油腻，送达快一点；然后继续追问“换个更近的”",
)
if chat_input:
    _send_chat_message(chat_input, timeout_sec=timeout_sec)
    st.rerun()

suggestions = st.session_state.get("last_followup_suggestions", [])
if suggestions:
    st.caption("继续追问：")
    cols = st.columns(len(suggestions))
    for i, s in enumerate(suggestions):
        if cols[i].button(s, key=f"suggest_{i}", use_container_width=True):
            _send_chat_message(s, timeout_sec=timeout_sec)
            st.rerun()
