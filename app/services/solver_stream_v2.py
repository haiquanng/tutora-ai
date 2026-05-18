import asyncio
from typing import Optional, List, AsyncGenerator
from google import genai
from google.genai import types
from ..utils.prompt import TUTOR_SYSTEM_V2, CHAT_SYSTEM, build_solve_prompt_v2


async def solve_stream_v2(
    client: genai.Client,
    question: str,
    message_id: str,
    session_id: str,
    rag_chunks: Optional[List[dict]] = None,
    history: Optional[List[dict]] = None,
    is_problem: bool = True,
) -> AsyncGenerator[dict, None]:
    """Stream text tự nhiên theo phong cách gia sư, không JSON."""
    contents = []
    for msg in (history or []):
        role = "user" if msg["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=[types.Part(text=msg["content"])]))

    if is_problem:
        current_prompt = build_solve_prompt_v2(question, rag_chunks)
        system = TUTOR_SYSTEM_V2
    else:
        current_prompt = question
        system = CHAT_SYSTEM

    contents.append(types.Content(role="user", parts=[types.Part(text=current_prompt)]))

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def _stream_in_thread():
        try:
            response = client.models.generate_content_stream(
                model="gemini-2.5-flash",
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0.5,
                    top_p=1,
                    system_instruction=system,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )
            for chunk in response:
                text = chunk.text if chunk.text else ""
                if text:
                    loop.call_soon_threadsafe(queue.put_nowait, text)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    thread_future = loop.run_in_executor(None, _stream_in_thread)

    while True:
        text = await queue.get()
        if text is None:
            break
        yield {
            "id": message_id,
            "session_id": session_id,
            "delta": text,
            "done": False,
        }

    await thread_future

    yield {
        "id": message_id,
        "session_id": session_id,
        "delta": "",
        "done": True,
    }
