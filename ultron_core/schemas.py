"""Output contract for the ULTRON brain.

Two views of the same contract:
  * TOOL_SCHEMAS / RESPONSE_SCHEMA: formal JSON Schema (draft 2020-12), sent to the model engines
    (Gemini `response_json_schema`, Ollama `format`, vLLM `guided_json`).
  * Pydantic models: the local validator. Every model reply is validated here, whatever the engine
    promises, so the executor never sees malformed tool calls.
"""
from __future__ import annotations

import copy
import json
from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --------------------------------------------------------------------------------------
# 1. Formal JSON Schemas for the four system tools
# --------------------------------------------------------------------------------------
TOOL_SCHEMAS: dict = {
    "execute_terminal_cmd": {
        "description": "Run one shell command on the owner's machine and return stdout/stderr/exit code.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "minLength": 1, "maxLength": 2000,
                            "description": "Exact command line. One command or a single pipeline."},
                "shell": {"type": "string", "enum": ["auto", "bash", "powershell", "cmd"],
                          "description": "Shell to use. 'auto' = the device's default shell."},
                "working_dir": {"type": "string", "maxLength": 500,
                                "description": "Absolute directory to run in. Empty string = home directory."},
                "timeout_s": {"type": "integer", "minimum": 1, "maximum": 600},
                "destructive": {"type": "boolean",
                                "description": "True if the command deletes, overwrites, kills, formats, "
                                               "uninstalls, shuts down or changes system settings."},
            },
            "required": ["command", "shell", "working_dir", "timeout_s", "destructive"],
            "additionalProperties": False,
        },
    },
    "get_system_diagnostics": {
        "description": "Read live system health metrics.",
        "parameters": {
            "type": "object",
            "properties": {
                "metrics": {"type": "array", "minItems": 1, "uniqueItems": True,
                            "items": {"type": "string", "enum": ["cpu", "memory", "disk", "network", "battery",
                                                                 "processes", "gpu", "temperature", "uptime", "os"]}},
                "top_n_processes": {"type": "integer", "minimum": 0, "maximum": 50,
                                    "description": "How many top processes to list (0 if 'processes' not requested)."},
            },
            "required": ["metrics", "top_n_processes"],
            "additionalProperties": False,
        },
    },
    "launch_app": {
        "description": "Open an application, optionally with arguments (a file, URL or project path).",
        "parameters": {
            "type": "object",
            "properties": {
                "app_name": {"type": "string", "minLength": 1, "maxLength": 120,
                             "description": "Common app name, e.g. 'vscode', 'chrome', 'spotify', 'calculator'."},
                "arguments": {"type": "array", "maxItems": 10, "items": {"type": "string", "maxLength": 500}},
                "device": {"type": "string", "enum": ["this", "laptop", "phone"],
                           "description": "Where to open it. 'this' = the device the owner is talking to."},
            },
            "required": ["app_name", "arguments", "device"],
            "additionalProperties": False,
        },
    },
    "run_local_script": {
        "description": "Run a script from the owner's approved scripts folder (no arbitrary paths).",
        "parameters": {
            "type": "object",
            "properties": {
                "script_name": {"type": "string", "pattern": r"^[A-Za-z0-9_\-]+(\.[A-Za-z0-9]+)?$",
                                "description": "File name inside the scripts folder, e.g. 'backup_notes.py'."},
                "args": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 500}},
                "timeout_s": {"type": "integer", "minimum": 1, "maximum": 3600},
            },
            "required": ["script_name", "args", "timeout_s"],
            "additionalProperties": False,
        },
    },
}
TOOL_NAMES = list(TOOL_SCHEMAS)


def _tool_call_variant(name: str) -> dict:
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string", "enum": [name]},
            "arguments": copy.deepcopy(TOOL_SCHEMAS[name]["parameters"]),
        },
        "required": ["name", "arguments"],
        "additionalProperties": False,
    }


# The full response envelope. Property order matters for constrained decoding: `thinking` is
# generated first, so the reasoning is produced before the answer and tool calls depend on it.
RESPONSE_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "UltronResponse",
    "type": "object",
    "properties": {
        "thinking": {"type": "string", "maxLength": 2000,
                     "description": "Short numbered reasoning steps. Never spoken."},
        "spoken_response": {"type": "string", "maxLength": 1200,
                            "description": "What ULTRON says aloud. Plain sentences, no markdown."},
        "tool_calls": {"type": "array", "maxItems": 5,
                       "items": {"anyOf": [_tool_call_variant(n) for n in TOOL_NAMES]}},
    },
    "required": ["thinking", "spoken_response", "tool_calls"],
    "additionalProperties": False,
}

# Fallback for engines that reject anyOf / nested strictness: arguments travel as a JSON string,
# and the Pydantic layer below enforces the real per-tool schema after decoding.
FLAT_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "thinking": {"type": "string"},
        "spoken_response": {"type": "string"},
        "tool_calls": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string", "enum": TOOL_NAMES},
                           "arguments_json": {"type": "string",
                                              "description": "The tool arguments as a JSON object string."}},
            "required": ["name", "arguments_json"]}},
    },
    "required": ["thinking", "spoken_response", "tool_calls"],
}


# --------------------------------------------------------------------------------------
# 2. Pydantic mirror (the validator)
# --------------------------------------------------------------------------------------
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExecuteTerminalCmdArgs(_Strict):
    command: str = Field(min_length=1, max_length=2000)
    shell: Literal["auto", "bash", "powershell", "cmd"] = "auto"
    working_dir: str = Field("", max_length=500)
    timeout_s: int = Field(60, ge=1, le=600)
    destructive: bool = False


class GetSystemDiagnosticsArgs(_Strict):
    metrics: List[Literal["cpu", "memory", "disk", "network", "battery", "processes", "gpu",
                          "temperature", "uptime", "os"]] = Field(min_length=1)
    top_n_processes: int = Field(0, ge=0, le=50)

    @field_validator("metrics")
    @classmethod
    def _unique(cls, v):
        return list(dict.fromkeys(v))


class LaunchAppArgs(_Strict):
    app_name: str = Field(min_length=1, max_length=120)
    arguments: List[str] = Field(default_factory=list, max_length=10)
    device: Literal["this", "laptop", "phone"] = "this"


class RunLocalScriptArgs(_Strict):
    script_name: str = Field(pattern=r"^[A-Za-z0-9_\-]+(\.[A-Za-z0-9]+)?$")
    args: List[str] = Field(default_factory=list, max_length=20)
    timeout_s: int = Field(300, ge=1, le=3600)


class ExecuteTerminalCmd(_Strict):
    name: Literal["execute_terminal_cmd"]
    arguments: ExecuteTerminalCmdArgs


class GetSystemDiagnostics(_Strict):
    name: Literal["get_system_diagnostics"]
    arguments: GetSystemDiagnosticsArgs


class LaunchApp(_Strict):
    name: Literal["launch_app"]
    arguments: LaunchAppArgs


class RunLocalScript(_Strict):
    name: Literal["run_local_script"]
    arguments: RunLocalScriptArgs


ToolCall = Annotated[Union[ExecuteTerminalCmd, GetSystemDiagnostics, LaunchApp, RunLocalScript],
                     Field(discriminator="name")]


class UltronResponse(_Strict):
    thinking: str = Field("", max_length=4000)
    spoken_response: str = Field(min_length=1, max_length=2000)
    tool_calls: List[ToolCall] = Field(default_factory=list, max_length=5)


def unflatten(data: dict) -> dict:
    """Convert a FLAT_RESPONSE_SCHEMA reply ({name, arguments_json}) into the canonical shape."""
    calls = []
    for c in data.get("tool_calls") or []:
        if "arguments_json" in c and "arguments" not in c:
            raw = c.get("arguments_json") or "{}"
            c = {"name": c.get("name"), "arguments": json.loads(raw) if isinstance(raw, str) else raw}
        calls.append(c)
    return {**data, "tool_calls": calls}
