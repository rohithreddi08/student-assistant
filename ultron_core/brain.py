"""UltronBrain: prompt assembly -> engine -> robust parsing -> validation/repair -> policy guard.

    brain = UltronBrain(OllamaBackend(), owner="Rohith")
    result = brain.respond("why is my laptop slow", state={"os": "Windows 11", "device": "laptop"})
    result.response.spoken_response      # -> TTS
    result.calls                         # -> your executor (check .needs_confirmation / .blocked)
    brain.submit_tool_results([...])     # -> next turn with the results

or let it drive the whole loop:   brain.run(query, state, executor=my_exec, confirm=ask_owner)
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from pydantic import ValidationError

from .prompts import build_system_prompt, build_tool_results_turn, build_user_turn
from .schemas import UltronResponse, unflatten

FALLBACK_LINE = "My reasoning core returned noise. Say that again, and I will do better."

# Commands that need the owner's confirmation even if the model forgot to flag them.
_RISKY = re.compile(
    r"""(\brm\s+(-[a-z]*[rf]|--recursive|--force)|\brmdir\b|\bdel\b|\berase\b|remove-item|\bformat(-volume)?\b|
         \bmkfs|\bdd\s+if=|diskpart|\bshutdown\b|restart-computer|stop-computer|\breboot\b|
         \bkill(all)?\b|stop-process|taskkill|\breg\s+(delete|add)|set-itemproperty|\bchmod\s+-r|\bchown\s+-r|
         \buninstall|winget\s+uninstall|apt(-get)?\s+(remove|purge)|pip\s+uninstall|
         \bgit\s+(push\s+.*--force|reset\s+--hard|clean\s+-[a-z]*f)|
         (curl|wget|iwr|invoke-webrequest)[^|]*\|\s*(sh|bash|iex|invoke-expression)|
         \bset-executionpolicy|\bnetsh\b|\bbcdedit\b|\bcipher\s+/w|>\s*/dev/sd|:\(\)\s*\{)""",
    re.I | re.X)


@dataclass
class PlannedCall:
    name: str
    arguments: dict
    needs_confirmation: bool = False
    blocked: bool = False
    reason: str = ""


@dataclass
class BrainResult:
    response: UltronResponse
    calls: list[PlannedCall]
    raw: str
    engine: str
    latency_s: float
    repairs: int = 0
    ok: bool = True


@dataclass
class Policy:
    allowed_scripts: Optional[set[str]] = None       # None = don't restrict here (executor decides)
    allow_terminal: bool = True
    always_confirm_terminal: bool = False

    def review(self, call: dict) -> PlannedCall:
        name, args = call["name"], call["arguments"]
        pc = PlannedCall(name, args)
        if name == "execute_terminal_cmd":
            if not self.allow_terminal:
                pc.blocked, pc.reason = True, "terminal access is disabled"
            elif args.get("destructive") or _RISKY.search(args["command"]) or self.always_confirm_terminal:
                pc.needs_confirmation, pc.reason = True, "destructive or system-changing command"
        elif name == "run_local_script" and self.allowed_scripts is not None \
                and args["script_name"] not in self.allowed_scripts:
            pc.blocked, pc.reason = True, f"script '{args['script_name']}' is not in the approved list"
        return pc


# ------------------------------------------------------------------------------------------------
def extract_json(text: str) -> dict:
    """Find the reply object even if a model wraps it in prose or ```json fences."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        pass
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    raise ValueError("no JSON object found in model output")


_MD = [(re.compile(r"```.*?```", re.S), " "), (re.compile(r"`([^`]*)`"), r"\1"),
       (re.compile(r"https?://\S+"), "the link"), (re.compile(r"\*\*|__|[*_#>]+"), ""),
       (re.compile(r"^\s*[-•]\s+", re.M), ""), (re.compile(r"[\U0001F000-\U0001FFFF☀-➿️]"), ""),
       (re.compile(r"\s+"), " ")]


def tts_clean(text: str) -> str:
    """Last line of defence so the TTS never reads markdown, emoji or URLs."""
    for rx, rep in _MD:
        text = rx.sub(rep, text)
    return text.strip()


# ------------------------------------------------------------------------------------------------
class UltronBrain:
    def __init__(self, backend, owner: str = "Boss", policy: Policy | None = None,
                 history_messages: int = 16, max_repairs: int = 2, compact_prompt: bool = False):
        self.backend, self.owner = backend, owner
        self.policy = policy or Policy()
        self.history: list[dict] = []
        self.history_messages, self.max_repairs = history_messages, max_repairs
        self.system = build_system_prompt(owner, compact=compact_prompt)

    # ---- one model turn ------------------------------------------------------------------
    def _turn(self, user_content: str) -> BrainResult:
        t0 = time.time()
        msgs = self._trimmed() + [{"role": "user", "content": user_content}]
        raw, err, repairs = "", None, 0
        for attempt in range(self.max_repairs + 1):
            raw = self.backend.generate(self.system, msgs)
            try:
                resp = self.parse(raw)
                break
            except (ValueError, ValidationError) as e:
                err, repairs = e, attempt + 1
                msgs = msgs + [{"role": "assistant", "content": raw[:4000]},
                               {"role": "user", "content": "Your last output did not match the required JSON schema:\n"
                                f"{str(e)[:800]}\nReply again with ONLY the corrected JSON object."}]
        else:
            resp = UltronResponse(thinking=f"unparseable output: {err}"[:3000], spoken_response=FALLBACK_LINE)
            return BrainResult(resp, [], raw, self.backend.name, time.time() - t0, repairs, ok=False)

        resp.spoken_response = tts_clean(resp.spoken_response) or FALLBACK_LINE
        self.history += [{"role": "user", "content": user_content},
                         {"role": "assistant", "content": resp.model_dump_json()}]
        calls = [self.policy.review(c.model_dump()) for c in resp.tool_calls]
        return BrainResult(resp, calls, raw, self.backend.name, time.time() - t0, repairs)

    @staticmethod
    def parse(raw: str) -> UltronResponse:
        return UltronResponse.model_validate(unflatten(extract_json(raw)))

    def _trimmed(self) -> list[dict]:
        h = self.history[-self.history_messages:]
        while h and h[0]["role"] != "user":
            h = h[1:]
        return h

    # ---- public API ----------------------------------------------------------------------
    def respond(self, query: str, state: dict | None = None) -> BrainResult:
        return self._turn(build_user_turn(query, state))

    def submit_tool_results(self, results: list[dict]) -> BrainResult:
        """results = [{"name": ..., "ok": bool, "output": ...}, ...] in the same order as the calls."""
        return self._turn(build_tool_results_turn(results))

    def reset(self):
        self.history.clear()

    def run(self, query: str, state: dict | None, executor: Callable[[str, dict], object],
            confirm: Callable[[PlannedCall], bool] = lambda c: False,
            on_speak: Callable[[str], None] = lambda s: None, max_steps: int = 4) -> BrainResult:
        """Full agent loop: think -> act -> observe -> answer. `executor(name, args)` is YOUR wrapper."""
        res = self.respond(query, state)
        for _ in range(max_steps):
            on_speak(res.response.spoken_response)
            if not res.calls:
                return res
            results = []
            for c in res.calls:
                if c.blocked:
                    results.append({"name": c.name, "ok": False, "output": f"blocked by policy: {c.reason}"})
                elif c.needs_confirmation and not confirm(c):
                    results.append({"name": c.name, "ok": False, "output": "owner declined"})
                else:
                    try:
                        results.append({"name": c.name, "ok": True, "output": executor(c.name, c.arguments)})
                    except Exception as e:  # the model should hear about failures, not crash
                        results.append({"name": c.name, "ok": False, "output": f"{type(e).__name__}: {e}"})
            res = self.submit_tool_results(results)
        on_speak(res.response.spoken_response)
        return res
