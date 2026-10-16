from dataclasses import dataclass, field
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr


class ProviderUnavailable(Exception):
    """Provider failure whose details must never enter client responses."""


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    value: StrictStr = Field(max_length=500)


class RecognizedIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent_id: Optional[StrictStr]
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)
    entities: List[Entity] = Field(max_length=20)
    needs_clarification: StrictBool
    clarification_question: Optional[StrictStr] = Field(max_length=300)
    context_summary: Optional[StrictStr] = Field(max_length=500)


@dataclass
class Decision:
    node: Optional[dict] = None
    method: str = "none"
    score: Optional[float] = None
    entities: dict = field(default_factory=dict)
    needs_clarification: bool = False
    clarification_question: Optional[str] = None
    context_summary: Optional[str] = None

    def payload(self, attempts=None, warnings=None):
        return {"method": self.method, "node_id": self.node["id"] if self.node else None,
            "node_number": self.node.get("number") if self.node else None,
            "intent_name": self.node["name"] if self.node else None,
            "score": self.score, "entities": self.entities,
            "needs_clarification": self.needs_clarification,
            "clarification_question": self.clarification_question, "context_summary": self.context_summary,
            "attempts": list(attempts or []), "warnings": list(dict.fromkeys(warnings or []))}
