import httpx
from fastapi import HTTPException


class LLMTester:
    """Server-configured OpenAI-compatible chat completion adapter."""

    def __init__(self, settings):
        self.settings = settings

    @property
    def available(self):
        return bool(self.settings.llm_base_url and self.settings.llm_model)

    def reply(self, agent, messages, language=None):
        if not self.available:
            raise HTTPException(503, "Connect a language model before testing generative replies.")
        headers = {}
        key = self.settings.llm_api_key.get_secret_value()
        if key:
            headers["Authorization"] = "Bearer " + key
        prompt = agent.persona or "You are a helpful assistant."
        if language is not None:
            if language not in agent.languages:
                raise HTTPException(422, "Choose a language configured for this agent.")
            prompt += "\nActive conversation language: {}. Respond in this language. Supported languages: {}.".format(language, ", ".join(agent.languages))
        else:
            prompt += "\nRespond in the user's supported language where possible. Supported languages: {}. Default: {}.".format(
                ", ".join(agent.languages), agent.default_language)
        try:
            with httpx.Client(timeout=self.settings.llm_timeout_seconds, follow_redirects=False) as client:
                response = client.post(self.settings.llm_base_url.rstrip("/") + "/chat/completions", headers=headers,
                    json={"model": self.settings.llm_model, "messages": [{"role": "system", "content": prompt}] + messages,
                        "max_tokens": self.settings.llm_max_tokens, "temperature": 0.4})
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise ValueError
                return content.strip()[:16000]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, ImportError):
            # Provider bodies and prompts can contain sensitive data; never expose them.
            raise HTTPException(503, "The language model could not reply. Check its connection and try again.") from None
