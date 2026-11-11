"""ULTRON core ML layer: persona prompt, tool schemas, structured-output brain, engine backends."""
from .backends import BackendError, GeminiBackend, OllamaBackend, OpenAICompatBackend
from .brain import BrainResult, PlannedCall, Policy, UltronBrain, extract_json, tts_clean
from .prompts import build_system_prompt, build_tool_results_turn, build_user_turn
from .schemas import RESPONSE_SCHEMA, TOOL_SCHEMAS, UltronResponse

__all__ = ["UltronBrain", "BrainResult", "PlannedCall", "Policy", "GeminiBackend", "OllamaBackend",
           "OpenAICompatBackend", "BackendError", "TOOL_SCHEMAS", "RESPONSE_SCHEMA", "UltronResponse",
           "build_system_prompt", "build_user_turn", "build_tool_results_turn", "extract_json", "tts_clean"]
