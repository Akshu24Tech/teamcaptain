from __future__ import annotations

from tc.extraction.extractor import Extractor, LLMExtractor, RuleBasedExtractor
from tc.llm import OpenAICompatibleModel


def get_extractor(kind: str = "llm") -> Extractor:
    if kind == "rules":
        return RuleBasedExtractor()
    return LLMExtractor(OpenAICompatibleModel())
