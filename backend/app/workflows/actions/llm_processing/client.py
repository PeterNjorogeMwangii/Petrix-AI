"""Groq text generation: no tool execution, retries or provider error leakage."""
from dataclasses import dataclass
from typing import Optional

import httpx
from groq import APIError, APITimeoutError, Groq


@dataclass(frozen=True)
class ProcessingResult:
    outcome: str
    text: Optional[str] = None
    error: Optional[str] = None


class GroqProcessingClient:
    def __init__(self, settings): self.settings = settings

    @property
    def available(self): return bool(self.settings.groq_api_key.get_secret_value())

    def process(self, model, messages, temperature, timeout_seconds):
        if not self.available: return ProcessingResult("error", error="provider_unavailable")
        try:
            with httpx.Client(timeout=timeout_seconds, follow_redirects=False, trust_env=False) as transport, Groq(
                    api_key=self.settings.groq_api_key.get_secret_value(), base_url="https://api.groq.com", http_client=transport,
                    timeout=timeout_seconds, max_retries=0) as client:
                options = {"reasoning_format": "hidden"} if model.startswith("openai/gpt-oss-") else {}
                response = client.chat.completions.create(model=model, messages=messages, temperature=temperature,
                    max_completion_tokens=self.settings.llm_processing_max_tokens, stream=False, **options)
            choice = response.choices[0]
            text = choice.message.content
            if choice.finish_reason != "stop" or type(text) is not str or not text.strip():
                return ProcessingResult("error", error="invalid_response")
            text = text.strip()
            if len(text) > 8192 or len(text.encode("utf-8")) > 16000:
                return ProcessingResult("error", error="response_too_large")
            return ProcessingResult("success", text=text)
        except (APITimeoutError, httpx.TimeoutException):
            return ProcessingResult("timeout", error="timeout")
        except APIError as error:
            return ProcessingResult("error", error="provider_rate_limit" if getattr(error, "status_code", None) == 429 else "provider_error")
        except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError, AttributeError):
            return ProcessingResult("error", error="invalid_response")
