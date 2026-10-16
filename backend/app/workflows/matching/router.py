"""Layer order and branch scope live here; providers never execute actions."""
from app.workflows.matching.embeddings import EmbeddingClient, SemanticMatcher
from app.workflows.matching.fuzzy import normalize, similarity
from app.workflows.matching.groq_intents import GroqIntentRecognizer
from app.workflows.matching.types import Decision, ProviderUnavailable


class IntentMatcher:
    def __init__(self, settings):
        self.settings = settings
        self.semantic = SemanticMatcher(EmbeddingClient(settings), settings)
        self.llm = GroqIntentRecognizer(settings)

    def session(self, document, agent, history=None, before_llm=None):
        return MatchingSession(self, document, agent, history or [], before_llm)


class MatchingSession:
    def __init__(self, matcher, document, agent, history, before_llm):
        self.matcher, self.document, self.agent = matcher, document, agent
        self.history, self.before_llm = history, before_llm
        self.attempts, self.warnings, self.query_cache = [], [], {}
        self.last = Decision()
        self.scope = "root"

    @property
    def report(self):
        return {**self.last.payload(self.attempts, self.warnings), "scope": self.scope}

    def attempt(self, stage, status, scope, score=None):
        self.attempts.append({"stage": stage, "status": status, "scope": scope,
            "score": round(score, 4) if score is not None else None})

    def finish(self, decision):
        self.last = decision
        return decision

    def choose(self, nodes, event, language, scope="root"):
        self.scope = scope
        fallback = next((node for node in nodes if node["enabled"] and node["kind"] == "trigger" and node["type"] == "fallback"), None)
        eligible = [node for node in nodes if node["enabled"] and node["kind"] == "trigger" and node["type"] == event["event"]]
        if event["event"] != "message_received":
            if event["event"] == "attachment_received":
                eligible = [node for node in eligible if event["attachment"]["media_type"] in node["config"]["attachment_types"]]
            if eligible:
                return self.finish(Decision(node=eligible[0], method="event", score=1.0))
            return self.finish(Decision(node=fallback if event["event"] in {"attachment_received", "collect_feedback"} else None,
                method="fallback" if fallback and event["event"] in {"attachment_received", "collect_feedback"} else "none"))

        candidates = []
        for node in eligible:
            translations = node["config"].get("examples", {})
            examples = translations.get(language) or translations.get(self.agent.default_language) or []
            if examples:
                candidates.append({"node": node, "examples": examples})
        # Exact mode is an explicit opt-out from embedding/LLM interpretation.
        exact = [value for value in candidates if value["node"]["config"].get("match_mode") == "exact"]
        for value in exact:
            if any(normalize(event["content"]) == normalize(example) for example in value["examples"]):
                self.attempt("exact", "matched", scope, 1.0)
                return self.finish(Decision(node=value["node"], method="exact", score=1.0))
        candidates = [value for value in candidates if value["node"]["config"].get("match_mode") != "exact"]
        if not candidates:
            self.attempt("fuzzy", "no_candidates", scope)
            return self.finish(Decision(node=fallback, method="fallback" if fallback else "none"))

        settings = self.matcher.settings
        ranked = sorted([(max(similarity(event["content"], example) for example in value["examples"]), value)
            for value in candidates], key=lambda item: item[0], reverse=True)
        # Compare the best passing candidate with the runner-up across all candidates,
        # including one just below its threshold, before treating a match as certain.
        passing = [(score, value) for score, value in ranked if score >= value["node"]["config"].get("threshold", 0.65)]
        if passing:
            score, candidate = passing[0]
            other = max((value for value, item in ranked if item is not candidate), default=-1.0)
            if score - other >= settings.intent_fuzzy_margin:
                self.attempt("fuzzy", "matched", scope, score)
                return self.finish(Decision(node=candidate["node"], method="fuzzy", score=score))
            self.attempt("fuzzy", "ambiguous", scope, score)
        else:
            self.attempt("fuzzy", "below_threshold", scope, ranked[0][0])

        if settings.intent_semantic_enabled:
            key = (str(self.agent.workspace_id), str(self.agent.id), self.document.revision, language)
            try:
                semantic = self.matcher.semantic.rank(candidates, event["content"], key, self.query_cache)
                score, candidate = semantic[0]
                other = semantic[1][0] if len(semantic) > 1 else -1.0
                if score >= settings.intent_semantic_threshold and score - other >= settings.intent_semantic_margin:
                    self.attempt("semantic", "matched", scope, score)
                    return self.finish(Decision(node=candidate["node"], method="semantic", score=score))
                self.attempt("semantic", "ambiguous" if score >= settings.intent_semantic_threshold else "below_threshold", scope, score)
            except ProviderUnavailable:
                self.attempt("semantic", "unavailable", scope)
                self.warnings.append("Embeddings are unavailable. Prepare the local model or check the embedding connection.")
        else:
            self.attempt("semantic", "disabled", scope)

        if self.agent.llm_enabled and self.matcher.llm.available:
            if self.before_llm:
                self.before_llm()
            try:
                result = self.matcher.llm.recognize(candidates, event["content"], language, self.history)
                # Validate allow-list membership again at the boundary before execution,
                # even when an alternate recognizer or test adapter is installed.
                selected = next((value["node"] for value in candidates if value["node"]["id"] == result.intent_id), None)
                if result.intent_id is not None and selected is None:
                    raise ProviderUnavailable()
                if result.needs_clarification or (selected and result.confidence < settings.intent_llm_threshold):
                    question = result.clarification_question or "Could you clarify what you would like to do?"
                    self.attempt("llm", "clarification", scope, result.confidence)
                    return self.finish(Decision(method="clarification", score=result.confidence, needs_clarification=True,
                        clarification_question=question, context_summary=result.context_summary))
                if selected:
                    self.attempt("llm", "matched", scope, result.confidence)
                    return self.finish(Decision(node=selected, method="llm", score=result.confidence,
                        entities={entity.name: entity.value for entity in result.entities}, context_summary=result.context_summary))
                self.attempt("llm", "no_match", scope, result.confidence)
            except ProviderUnavailable:
                self.attempt("llm", "unavailable", scope)
                self.warnings.append("Groq intent recognition is unavailable. Check the backend connection and model settings.")
        else:
            self.attempt("llm", "disabled" if not self.agent.llm_enabled or not settings.intent_llm_enabled else "not_configured", scope)
        return self.finish(Decision(node=fallback, method="fallback" if fallback else "none"))
