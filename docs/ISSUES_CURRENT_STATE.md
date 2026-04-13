# NexaNote — Rewritten GitHub Issues (Current State)

## Reality snapshot (from code, not wishlist)

### Fully implemented today
- Python backend with FastAPI routes for notebooks, notes, page text/ink, sync trigger/status, stats, and storage info.
- SQLite storage layer with schema + CRUD for notebooks/notes/pages/strokes.
- WebDAV server (WsgiDAV provider) and WebDAV sync client/engine.
- Conflict resolution module and automated test coverage (`64 passed`).
- Flutter desktop/mobile layouts with notebook sidebar, note list, editor, and ink canvas (pen/highlighter/eraser, pressure, undo, clear, zoom).

### Partially implemented / unstable
- Sync settings UX is split between Flutter `SharedPreferences` and backend disk config; no clear “source of truth”.
- Data directory can be typed in settings, but changing it only affects a shell config file for next restart.
- Editor is effectively single-page in UI (always page 1), while backend data model supports pages.
- Search runs on every keypress with no debounce/cancel.
- Error handling in app state is often swallowed (`catch (_) {}`), so failures are hard to debug.

### Missing (or not productized yet)
- Docker/deployment packaging.
- PDF export.
- OCR.
- End-to-end encryption.
- Mature Android release workflow.

---

## Issue 1 — Align README feature claims with current behavior
**Suggested label:** `good first issue`

## Overview
README currently presents some items as fully done, but parts are still rough in real usage (for example sync/setup UX and mobile readiness).

## What needs to be done
- [ ] Compare README “What works today / What’s coming” against actual backend + Flutter behavior.
- [ ] Update wording so completed vs experimental vs planned is explicit.
- [ ] Add one “Known limitations” section with short, concrete bullets.

## Goal
Contributors and users can trust project status without reading all source files.

## Notes
Keep scope to docs only (no code refactor). This is a safe starter task.

---

## Issue 2 — Add API client error messages in Flutter instead of silent failures
**Suggested label:** `good first issue`

## Overview
Several calls in `AppState` use `catch (_) {}` and just continue, which hides backend failures and creates “nothing happened” UX.

## What needs to be done
- [ ] Replace silent catches with structured error strings in `AppState`.
- [ ] Surface errors via Snackbar/toast in screens that trigger the actions.
- [ ] Add one small widget test for visible error state.

## Goal
When API calls fail, users and contributors immediately see why.

## Notes
Do not redesign state management; keep Provider + current architecture.

---

## Issue 3 — Debounce note search requests in `HomeScreen`
**Suggested label:** `good first issue`

## Overview
Search currently calls backend on every character change, which can spam requests and make typing feel laggy.

## What needs to be done
- [ ] Add a short debounce timer (e.g., 250–400ms) before `loadNotes(search: ...)`.
- [ ] Cancel pending debounce when new input arrives.
- [ ] Keep notebook filtering behavior unchanged.

## Goal
Smooth search typing with fewer unnecessary API requests.

## Notes
No backend changes required.

---

## Issue 4 — Make sync settings source-of-truth explicit (backend vs local prefs)
**Suggested label:** `help wanted`

## Overview
Sync settings are stored both in Flutter `SharedPreferences` and backend `sync_config.json` (excluding password). Behavior after restart is confusing.

## What needs to be done
- [ ] Decide and document source of truth for `server_url`, `username`, and `conflict_strategy`.
- [ ] Add a small API endpoint or startup flow so settings UI reads from backend on load.
- [ ] Keep password handling memory-only on backend (no plaintext persistence).

## Goal
A user sees consistent sync settings across restarts and devices.

## Notes
Must preserve current security decision: backend never writes sync password to disk.

---

## Issue 5 — Apply data directory changes safely with restart guidance
**Suggested label:** `help wanted`

## Overview
Settings screen allows editing data directory, but the running backend keeps using the old DB path until restart.

## What needs to be done
- [ ] Clarify in UI that data directory change is applied on next backend restart.
- [ ] Add validation for empty/invalid path before saving.
- [ ] Add optional “copy existing DB” utility script or documented manual migration steps.

## Goal
Users can change storage location without accidental data loss or confusion.

## Notes
Avoid hot-swapping live SQLite path in this issue; keep restart-based behavior.

---

## Issue 6 — Expose multi-page notes in Flutter editor (incremental)
**Suggested label:** `help wanted`

## Overview
Backend model supports pages, but editor UI only reads/saves page 1.

## What needs to be done
- [ ] Add page switcher UI (previous/next + page index).
- [ ] Load selected page from note payload instead of always page 1.
- [ ] Save text/ink to active page number.
- [ ] Keep existing editor behavior for single-page notes.

## Goal
Contributors can create and edit real multi-page notes without changing backend schema.

## Notes
Do this incrementally; no need for page thumbnails in first pass.

---

## Issue 7 — Add integration tests for sync configure/trigger/status API flow
**Suggested label:** `good first issue`

## Overview
Core API routes are tested, but sync route flow coverage can be stronger for regression safety.

## What needs to be done
- [ ] Add tests for `/sync/configure`, `/sync/trigger` (unconfigured and configured), and `/sync/status`.
- [ ] Assert expected response structure and key fields.
- [ ] Keep external network mocked/faked.

## Goal
Sync API behavior is stable during future refactors.

## Notes
Follow existing `tests/test_api.py` style and fixtures.

---

## Issue 8 — Contributor onboarding: add “run backend + app + tests” quick workflow
**Suggested label:** `good first issue`

## Overview
Project is approachable, but contributors still need to piece together commands and expected outputs manually.

## What needs to be done
- [ ] Add a short “First 30 minutes” section in README.
- [ ] Include exact commands for backend run, Flutter run, and pytest.
- [ ] Add troubleshooting notes for common local issues (ports occupied, backend unreachable).

## Goal
A new contributor can clone and run the stack in one sitting.

## Notes
Documentation-only change; no architecture changes.
