"""Live smoke test against a real engine.

    python -m ultron_core.live_test gemini
    python -m ultron_core.live_test ollama [model]
"""
import sys
import time

from ultron_core import GeminiBackend, OllamaBackend, Policy, UltronBrain

STATE = {"device": "laptop", "os": "Windows 11", "scripts": ["backup_notes.py"],
         "project_dir": r"C:\Users\Rohith\student-assistant"}
QUERIES = ["what is a hash map, briefly", "open vs code in my project folder",
           "delete everything in my downloads folder", "back up my notes", "how is my laptop doing"]
FAKE_RESULTS = {"get_system_diagnostics": {"cpu": 87, "memory": 74, "processes": [{"name": "chrome.exe", "cpu": 55}]}}


def main():
    engine = sys.argv[1] if len(sys.argv) > 1 else "gemini"
    if engine == "gemini":
        import db  # the assistant's local store holds the key
        be = GeminiBackend(api_key=db.get_local("gemini_key"))
    else:
        # CPU-only laptops: long timeout, small context, compact prompt
        be = OllamaBackend(model=sys.argv[2] if len(sys.argv) > 2 else "qwen2.5:14b-instruct",
                           timeout_s=1200, num_ctx=4096)
    compact = engine != "gemini"
    queries = QUERIES if len(sys.argv) <= 3 else QUERIES[:int(sys.argv[3])]
    brain = UltronBrain(be, owner="Rohith", policy=Policy(allowed_scripts={"backup_notes.py"}), compact_prompt=compact)
    print(f"engine={engine} prompt={'compact' if compact else 'full'} (~{len(brain.system) // 4} tokens)", flush=True)
    for q in queries:
        t = time.time()
        r = brain.respond(q, STATE)
        print(f"\nQ: {q} | ok={r.ok} repairs={r.repairs} {time.time() - t:.1f}s" + (f" mode={be.mode} model={be.last_model}" if engine == "gemini" else ""))
        print("  THINK:", r.response.thinking.replace("\n", " ")[:200])
        print("  SAY  :", r.response.spoken_response)
        for c in r.calls:
            print("  CALL :", c.name, c.arguments, "[needs confirm]" if c.needs_confirmation else "", "[BLOCKED]" if c.blocked else "")
        if any(c.name in FAKE_RESULTS for c in r.calls):   # one tool round-trip
            r2 = brain.submit_tool_results([{"name": c.name, "ok": True, "output": FAKE_RESULTS.get(c.name, "done")}
                                            for c in r.calls])
            print("  AFTER RESULTS:", r2.response.spoken_response, f"(ok={r2.ok})")


if __name__ == "__main__":
    main()
