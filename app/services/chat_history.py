from supabase import Client


async def save_message(
    sb: Client,
    session_id: str,
    role: str,  # "user" | "assistant"
    content: str,
    grade: str | None = None,
    chapter: str | None = None,
    topic: str | None = None,
    rag_used: bool = False,
    similarity_max: float | None = None,
    subject: str = "toan",
) -> None:
    sb.table("chat_messages").insert({
        "session_id": session_id,
        "role": role,
        "content": content,
        "grade": grade,
        "chapter": chapter,
        "topic": topic,
        "rag_used": rag_used,
        "similarity_max": similarity_max,
        "subject": subject,
    }).execute()


async def get_session_messages(sb: Client, session_id: str) -> list[dict]:
    result = (
        sb.table("chat_messages")
        .select("role, content, created_at")
        .eq("session_id", session_id)
        .order("created_at")
        .execute()
    )
    return result.data or []
