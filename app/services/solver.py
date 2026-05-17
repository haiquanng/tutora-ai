import json
from typing import Optional, List
from google import genai
from google.genai import types
from ..utils.prompt import TUTOR_SYSTEM, build_solve_prompt

async def solve(
    client: genai.Client,
    question: str,
    rag_chunks: Optional[List[dict]] = None,
    original_solution: Optional[str] = None
) -> dict:
    """Gọi Gemini giải bài toán với RAG context."""
    prompt = build_solve_prompt(question, rag_chunks, original_solution)
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.3,
            top_p=1,
            response_mime_type="application/json",
            system_instruction=TUTOR_SYSTEM
        )
    )
    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())
