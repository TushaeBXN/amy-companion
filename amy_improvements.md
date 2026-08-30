
## 2026-08-30 [CORRECTION]
**Error:** When asked for a morning brief, Amy looked for a file ("morning_briefing.txt") instead of calling web_search.
**Root cause:** DAILY BRIEFING trigger in persona didn't explicitly say to use web_search immediately. Model defaulted to file-reading behavior.
**Fix applied:** Persona now says "do NOT look for a file — call web_search RIGHT NOW" with explicit step-by-step search sequence.
**Category:** Wrong tool selection for trigger phrases
