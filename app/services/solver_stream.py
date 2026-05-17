import asyncio
from typing import Optional, List, AsyncGenerator
from google import genai
from google.genai import types
from ..utils.prompt import TUTOR_SYSTEM, build_solve_prompt


async def solve_stream(
    client: genai.Client,
    question: str,
    message_id: str,
    chat_id: str,
    rag_chunks: Optional[List[dict]] = None,
    original_solution: Optional[str] = None,
) -> AsyncGenerator[dict, None]:
    """Stream Gemini response chunk by chunk, yielding GauthMath-style dicts."""
    prompt = build_solve_prompt(question, rag_chunks, original_solution)
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def _stream_in_thread():
        try:
            response = client.models.generate_content_stream(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.3,
                    top_p=1,
                    system_instruction=TUTOR_SYSTEM,
                ),
            )
            for chunk in response:
                text = chunk.text if chunk.text else ""
                if text:
                    loop.call_soon_threadsafe(queue.put_nowait, text)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel

    thread_future = loop.run_in_executor(None, _stream_in_thread)

    while True:
        text = await queue.get()
        if text is None:
            break
        yield {
            "id": message_id,
            "session_id": chat_id,
            "delta": text,
            "done": False,
        }

    await thread_future  # propagate any exception from the thread

    yield {
        "id": message_id,
        "session_id": chat_id,
        "delta": "",
        "done": True,
    }
