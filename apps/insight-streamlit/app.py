"""IT Insight — Streamlit dashboard + Text-to-SQL chatbot.

Two pages in one app:

* **Tổng quan** reads the published Gold release through the read-only web API
  (``/api/dashboard``) so the numbers match the dashboard exactly.
* **Trợ lý** talks to the Text-to-SQL agent service (``/ask``), which generates SQL
  through a guard, runs it on Gold read-only, and explains the rows in Vietnamese.

Run locally:
    streamlit run apps/insight-streamlit/app.py --server.port 8020
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pandas as pd
import streamlit as st

WEB_API = os.getenv("INSIGHT_WEB_API", "http://127.0.0.1:8010").rstrip("/")
AGENT_API = os.getenv("INSIGHT_AGENT_API", "http://127.0.0.1:8021").rstrip("/")

st.set_page_config(page_title="IT Insight", page_icon="📊", layout="wide")


def get_json(url: str, timeout: float = 10.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def post_json(url: str, payload: dict, timeout: float = 120.0):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


@st.cache_data(ttl=60)
def load_dashboard() -> dict:
    return get_json(f"{WEB_API}/api/dashboard")


@st.cache_data(ttl=60)
def load_agent_health() -> dict:
    try:
        return get_json(f"{AGENT_API}/health", timeout=5)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"status": "offline", "llm_configured": False, "error": str(exc)}


def money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.0f} đ".replace(",", ".")


def label(value: str | None) -> str:
    return "Chưa xác định" if value in (None, "", "UNKNOWN") else str(value)


def page_overview() -> None:
    data = load_dashboard()
    summary, meta = data["summary"], data["meta"]
    st.caption(
        f"Dữ liệu cập nhật ngày {meta['snapshot_date']} · "
        f"{'đang trong ngưỡng mới' if not meta['stale'] else 'đã cũ'} · "
        f"phạm vi: {meta.get('scope', 'nguồn cung tin quan sát được')}"
    )

    top = st.columns(4)
    top[0].metric("Tin tuyển dụng", f"{summary['jobs']:,}".replace(",", "."))
    top[1].metric("Doanh nghiệp", f"{summary['companies']:,}".replace(",", "."))
    top[2].metric("Kỹ năng", f"{summary['skills']:,}".replace(",", "."))
    top[3].metric("Trung vị lương", money(summary["salary_median"]),
                  f"{summary['salary_samples']:,} mẫu".replace(",", "."))

    left, right = st.columns(2)
    with left:
        st.subheader("Theo chuyên môn")
        frame = pd.DataFrame([{"Nhóm": label(r["value"]), "Số tin": r["count"]} for r in data["domains"]])
        st.bar_chart(frame.set_index("Nhóm"), height=320, width='stretch')
    with right:
        st.subheader("Theo địa điểm")
        frame = pd.DataFrame([{"Địa điểm": label(r["value"]), "Số tin": r["count"]} for r in data["cities"]])
        st.bar_chart(frame.set_index("Địa điểm"), height=320)

    left, right = st.columns(2)
    with left:
        st.subheader("Theo cấp bậc")
        frame = pd.DataFrame([{"Cấp bậc": label(r["value"]), "Số tin": r["count"]} for r in data["levels"]])
        st.bar_chart(frame.set_index("Cấp bậc"), height=320)
    with right:
        st.subheader("Kỹ năng phổ biến")
        frame = pd.DataFrame([{"Kỹ năng": r.get("label") or r["value"], "Số tin": r["count"]}
                              for r in data["skills"][:12]])
        st.bar_chart(frame.set_index("Kỹ năng"), height=320)

    st.subheader("Công ty tuyển dụng nhiều nhất")
    frame = pd.DataFrame([{"Công ty": r["label"], "Số tin": r["count"]} for r in data["companies"][:12]])
    st.dataframe(frame, width='stretch', hide_index=True)

    st.subheader("Nền tảng")
    frame = pd.DataFrame([{"Nền tảng": r["value"], "Số tin": r["count"]} for r in data["platforms"]])
    st.bar_chart(frame.set_index("Nền tảng"), height=260)


def page_chat() -> None:
    health = load_agent_health()
    if health.get("status") == "offline":
        st.error("Dịch vụ trợ lý chưa chạy (cổng 8021).")
        return
    if not health.get("llm_configured"):
        st.warning(
            "Chưa cấu hình API key cho LLM. Thêm `GEMINI_API_KEY=...` hoặc `OPENAI_API_KEY=...` "
            "vào `/home/womcloud/services/it-insight-agent/config/agent.env` rồi khởi động lại dịch vụ."
        )
        return
    st.caption(f"Model: {health.get('provider')} · {health.get('model')} · chỉ đọc Gold, SQL có kiểm soát")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("sql"):
                with st.expander("SQL đã chạy"):
                    st.code(message["sql"], language="sql")

    question = st.chat_input("Hỏi về lương, kỹ năng, địa điểm, công ty…")
    if not question:
        return

    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages[-6:]
        if m["role"] in {"user", "assistant"}
    ]
    with st.chat_message("assistant"):
        with st.spinner("Đang truy vấn Gold…"):
            try:
                result = post_json(f"{AGENT_API}/ask", {"question": question, "history": history[:-1]})
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")
                st.error(f"Trợ lý trả lỗi {exc.code}: {detail[:300]}")
                return
            except (urllib.error.URLError, OSError, ValueError) as exc:
                st.error(f"Không gọi được trợ lý: {exc}")
                return
        st.markdown(result["answer"])
        if result.get("sql"):
            with st.expander("SQL đã chạy"):
                st.code(result["sql"], language="sql")
        if result.get("rows"):
            st.dataframe(pd.DataFrame(result["rows"]), width='stretch', hide_index=True)
    st.session_state.messages.append(
        {"role": "assistant", "content": result["answer"], "sql": result.get("sql")}
    )


def main() -> None:
    st.title("IT Insight — Thị trường việc làm IT Việt Nam")
    overview, chat = st.tabs(["Tổng quan", "Trợ lý dữ liệu"])
    with overview:
        try:
            page_overview()
        except (urllib.error.URLError, OSError, ValueError) as exc:
            st.error(f"Chưa tải được dữ liệu từ web API: {exc}")
    with chat:
        page_chat()


if __name__ == "__main__":
    main()
