---
name: llm-test
description: Run potner_llm's test suite (all tests/test_llm*.py files) and report pass/fail without ever hitting the real GMS API.
---

Run:

```bash
pytest tests/test_llm.py tests/test_llm_service.py tests/test_llm_context.py tests/test_llm_sensors.py tests/test_llm_factcheck.py tests/test_llm_diary.py -v
```

Then:

1. Summarize pass/fail counts and list any failing test names with their one-line assertion failure — don't
   paste full tracebacks unless the user asks.
2. If a failure's traceback shows an actual outbound HTTP/socket call (anything not going through the
   `monkeypatch.setattr(client_module.urllib.request, "urlopen", ...)` pattern used elsewhere in
   `tests/test_llm.py`), call that out explicitly as an invariant violation, not just a failing test — see
   the "Tests never hit the real GMS endpoint" invariant in `CLAUDE.md`. Do not add a real API key or network
   allowance to fix it; the test itself needs a mock.
3. If all tests pass, just report the count — no need to enumerate individual test names.
