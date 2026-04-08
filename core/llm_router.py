"""
LLM Router — routes requests to Gemini (deep reasoning) or Ollama (low-latency generation).
"""
import json
import logging
from typing import Any, Dict, Optional

import google.generativeai as genai
import httpx

from core.config import settings

logger = logging.getLogger(__name__)

class LLMRouter:
    def __init__(self):
        # Gemini Init
        genai.configure(api_key=settings.gemini.api_key)
        self.gemini_model = genai.GenerativeModel(
            model_name=settings.gemini.model,
            generation_config=genai.types.GenerationConfig(
                temperature=settings.gemini.temperature,
                max_output_tokens=settings.gemini.max_tokens,
            ),
        )
        # Ollama config
        self.ollama_url = f"{settings.ollama.base_url}/api/generate"
        self.ollama_model = settings.ollama.model
        self.ollama_temp = settings.ollama.temperature
        self.http_client = httpx.AsyncClient(timeout=10.0)

    async def get_fast_decision(self, prompt: str) -> str:
        """Use local Ollama for low-latency decisions (e.g., tick-level feature evaluation)."""
        payload = {
            "model": self.ollama_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": self.ollama_temp}
        }
        try:
            response = await self.http_client.post(self.ollama_url, json=payload)
            response.raise_for_status()
            data = response.json()
            return data.get("response", "")
        except Exception as e:
            logger.error("Ollama API failed: %s", e)
            return ""

    async def get_deep_reasoning(self, prompt: str) -> str:
        """Use Gemini for robust, context-heavy analysis (e.g. daily regime, macro)."""
        import asyncio
        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(
                None,
                lambda: self.gemini_model.generate_content(prompt),
            )
            return response.text.strip()
        except Exception as e:
            logger.error("Gemini API failed: %s", e)
            raise e

llm_router = LLMRouter()
