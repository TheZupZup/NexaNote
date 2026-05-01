# NexaNote — Android & S26 Ultra Roadmap

> **Status:** Planning document. No Android release exists yet. This roadmap describes
> the path from the current working desktop app to a first useful Android build, with
> Samsung S26 Ultra–style stylus use as the primary target experience.

---

## 1. Current Project Status

NexaNote today is a working **Linux desktop app** backed by a **Python server** running
locally on the same machine.

### What is fully implemented

| Component | Location | Notes |
|---|---|---|
| Python REST API (FastAPI) | `nexanote/api/routes.py` | Runs on port 8766 |
| SQLite storage layer | `nexanote/storage/database.py` | WAL mode, CRUD for notebooks/notes/pages/strokes |
| Data model | `nexanote/models/note.py` | Notebook → Note → Page → InkStroke → Point (x, y, pressure, ts) |
| WebDAV server (WsgiDAV) | `nexanote/sync/webdav_provider.py` | Exposes notes as filesystem on port 8765 |
| WebDAV sync client | `nexanote/sync/client.py` | PULL → DIFF → RESOLVE → PUSH flow |
| Conflict resolution | `nexanote/sync/conflict.py` | Three strategies: LAST_WRITE_WINS, KEEP_BOTH, MERGE_STROKES |
| Flutter UI (Linux) | `app/lib/` | Notebooks sidebar, note list, Markdown editor, ink canvas |
| Ink canvas widget | `app/lib/widgets/ink_canvas.dart` | Pen, highlighter, eraser, pressure, undo, zoom |
| 64 automated tests | `tests/` | Models, DB, WebDAV provider, conflict resolution, REST API |

### What is incomplete or unstable

- The Flutter app requires the Python backend to be running on the same network. There is
  no standalone offline mode in the app itself.
- The editor always uses page 1; multi-page UI does not exist yet (backend supports it).
- Sync settings are split between Flutter `SharedPreferences` and backend `sync_config.json`
  with no clear source of truth.
- Error handling in `AppState` often swallows failures silently (`catch (_) {}`).
- Search queries fire on every keystroke with no debounce.
- No Docker packaging, no Android APK/AAB release workflow, no PDF export, no OCR, no E2E encryption.

---

## 2. Android & S26 Ultra Goals

The goal is to make NexaNote genuinely useful as a daily mobile note app on a large-screen
Android phone with an S Pen–style stylus (targeting the S26 Ultra form factor: ~6.9" display,
active stylus with pressure and tilt).

**Target use cases:**

- Quick handwritten notes during meetings or study, captured with the S Pen directly into a
  NexaNote notebook.
- Offline-first: the phone works without any server reachable. Notes are saved locally.
- When back on the home network (or via a VPN), notes sync automatically to a self-hosted
  NAS or Nextcloud over WebDAV.
- No data ever leaves infrastructure the user controls.
- Typed Markdown notes work as well as handwritten ones.

**Non-goals for this roadmap:**

- Cloud hosting or any Anthropic/Google/Samsung accounts.
- Real-time collaboration.
- iOS support (Flutter makes it possible later, but it is out of scope here).
- Handwriting OCR (listed in README as a future item, not in this plan).

---

## 3. Stylus & Handwriting Requirements

### What the current model already supports

The `InkStroke` / `Point` model in `nexanote/models/note.py` already stores:

```
Point:  x, y, pressure (0.0–1.0), timestamp_ms
InkStroke: id, points[], color, width, tool (pen|highlighter|eraser), created_at
```

The Flutter ink canvas (`app/lib/widgets/ink_canvas.dart`) already reads `PointerEvent`
pressure for stroke width variation and supports pen, highlighter, and eraser tools.

### What needs to be added for S Pen quality

| Feature | Current state | Required |
|---|---|---|
| Pressure → line width | Implemented | Keep as-is |
| Tilt / angle | Not captured | Add `tilt_x`, `tilt_y` to `Point` model |
| Hover distance | Not captured | Nice to have, lower priority |
| Palm rejection | Not implemented | Required on a phone; Android `MotionEvent.TOOL_TYPE_FINGER` vs `TOOL_TYPE_STYLUS` |
| S Pen button | Not implemented | Map to tool switch (e.g. eraser) |
| Stroke latency | Unknown | Target < 20 ms input-to-pixel on Android; evaluate `flutter_stylus_events` or raw `Listener` widget |

### Page canvas sizing

The current `Page` model defaults to `1404 × 1872 px` (Remarkable tablet dimensions).
For an S26 Ultra (~1440 × 3088 px physical, ~480 dpi), a sensible logical canvas would be
`A4 portrait at 150 dpi (≈ 1240 × 1754 logical px)` or a user-selectable size. The page
dimensions are already stored per-page, so no schema change is needed — only the Flutter
default values and canvas rendering need adjusting.

### Ink rendering quality

- The current canvas renders strokes as variable-width paths using `Paint` with `StrokeCap.round`.
  This approach works but should be validated on high-dpi hardware to confirm it feels natural.
- The `MERGE_STROKES` conflict strategy (`nexanote/sync/conflict.py`) is already optimised for
  multi-device ink use: strokes added on different devices are unioned by ID, so drawing on phone
  and desktop simultaneously is safe.

---

## 4. Offline-First Note Storage

### The core architectural change needed

Today the Flutter app is a thin API client: every read and write goes to the Python backend
over HTTP. **On Android this must change.** The phone must own its own local store.

The two viable approaches are:

**Option A — Embed a local SQLite DB in the Flutter app**
Flutter writes notes directly to a local SQLite file using `sqflite` (or `drift`). The Python
backend becomes optional sync infrastructure, not a requirement. This is the recommended path.

**Option B — Bundle the Python backend in the APK**
Run the Python backend as a subprocess inside the app using `chaquopy` or a compiled binary.
This is fragile, produces large APKs, and has poor lifecycle management on Android. Not recommended.

### What Option A implies

- Add a local data layer to the Flutter app (new `services/local_store.dart` or similar).
- The `NexaNoteDB` schema from `nexanote/storage/database.py` is the reference; replicate the
  same tables in SQLite on the device.
- `AppState` needs to write to local DB first, then mark the note as `SyncStatus.modified`
  and enqueue a background sync rather than calling the Python API directly.
- The Python backend remains the canonical sync hub (WebDAV server) for users who self-host it,
  but it is no longer required to use the app at all.

### Storage locations

- Notes DB: standard `path_provider` app documents directory (already a dependency in `pubspec.yaml`).
- Note attachments (ink files) co-located in the same directory.
- No cloud storage; no third-party SDK.

---

## 5. WebDAV / NAS Sync

### Current sync architecture

The Python backend (`nexanote/sync/webdav_provider.py`) exposes notes over WebDAV in this structure:

```
/                                    ← DAV root
/{notebook-slug}__{id[:8]}/          ← notebook folder
/{notebook-slug}__{id[:8]}/
    {note-slug}__{id[:8]}/
        note.json                    ← metadata + typed content
        page_1.ink                   ← ink strokes as JSON
        page_2.ink
```

This is readable and writable by any standard WebDAV client (Nextcloud, rclone, Cyberduck,
Ugreen NAS UGOS Pro — confirmed working per README).

### Sync flow (already implemented in client.py)

```
PULL remote notes
  → DIFF against local updated_at timestamps
    → RESOLVE conflicts (MERGE_STROKES by default)
      → PUSH local-only / modified notes to remote
        → COMMIT: mark synced notes as SyncStatus.SYNCED
```

### What needs to happen on Android

- Re-implement this sync flow in Dart (a new `services/webdav_sync.dart`), or call the
  Python backend's `/sync/trigger` endpoint if the backend is reachable on the home network.
- The simplest first implementation: when the app regains network, call `/sync/trigger` on
  the Python backend (the backend handles the heavy lifting). This works only when the backend
  is reachable.
- The full native Dart sync is the long-term goal so the app can sync to any WebDAV server
  directly without running the Python backend.
- Authentication: HTTP Basic Auth over HTTPS. The password must never be written to disk
  (consistent with the existing backend policy in `nexanote/sync/client.py`).

### Supported sync targets (intended)

- Self-hosted Python backend (running on same home network machine or a VPS)
- Nextcloud (standard WebDAV)
- Any NAS with WebDAV enabled (Ugreen, Synology, QNAP, etc.)

---

## 6. Conflict Handling

### Current strategies

The `ConflictResolver` in `nexanote/sync/conflict.py` implements three strategies:

| Strategy | Behaviour | Best for |
|---|---|---|
| `LAST_WRITE_WINS` | Most recent `updated_at` wins | Simple text notes |
| `KEEP_BOTH` | Remote becomes primary; local becomes a copy with a conflict suffix | Safe fallback |
| `MERGE_STROKES` | Metadata: last-write-wins. Ink strokes: union by stroke ID | Handwritten notes (default) |

### Why MERGE_STROKES is the right default for S26 Ultra use

When a user draws on their phone during the day and later reviews on a desktop, the two
sessions produce disjoint sets of strokes. `MERGE_STROKES` unions them by stroke ID, so
nothing is lost. This is already the default in `SyncConfig`.

### Edge cases to address

- A note edited as typed text on one device and as ink on another: the `MERGE_STROKES`
  strategy handles this (text: last-write-wins per page; strokes: union).
- A notebook deleted on one device while notes were added on another: not yet handled.
  This must be addressed before WebDAV sync is considered stable.
- Clock skew between devices: `updated_at` comparisons assume reasonably-synced clocks.
  Consider adding a server-assigned `server_updated_at` field as a more reliable tiebreaker.

---

## 7. PDF Export (Later)

PDF export is listed in the README as a coming feature. It is **not** part of the Android
foundation work and should be done after the local note model and sync are stable.

### Expected scope when it is tackled

- Render each `Page`'s typed content and ink strokes to a PDF page.
- For ink: replay strokes onto a canvas and encode as vector or raster.
- For text: render Markdown to styled paragraphs.
- Delivery format: a `Share` intent on Android (system share sheet), a file save dialog on desktop.
- No dependency on a third-party cloud rendering service.

---

## 8. Security & Privacy Expectations

NexaNote's privacy stance is explicit in the README ("respects your data"). The following
constraints must hold throughout Android development:

| Expectation | Requirement |
|---|---|
| No telemetry | Zero analytics SDKs; no crash reporting to third-party services |
| No cloud accounts | No Google, Samsung, or Anthropic account required |
| Local storage only | Notes live in the app's private storage directory; not accessible to other apps without explicit export |
| Sync password | Never written to disk in plaintext. Stored in Android `EncryptedSharedPreferences` or prompted each session. Consistent with current backend policy. |
| WebDAV over HTTPS | TLS required for sync to any non-localhost target. `verify_ssl: True` is the default in `SyncConfig`. |
| Soft delete | Notes are soft-deleted (`is_deleted=True`) before permanent purge, matching current DB behaviour |
| Open source | MPL 2.0 — any modification must remain open source |
| No end-to-end encryption yet | E2EE is listed as a future item. Until it is implemented, WebDAV transport security (HTTPS) is the only protection in transit. This should be documented clearly to users. |

---

## 9. Suggested Phased PR Plan

Each phase is scoped to produce a reviewable, mergeable PR on its own. Phases do not need
to be strictly sequential, but each depends on the previous one being merged.

---

### Phase 1 — Document architecture and data model

**Goal:** Make the codebase legible to a new Android contributor without reading all source files.

**Deliverables:**
- This document (`docs/android-roadmap.md`) — done in this PR.
- A short architecture diagram (ASCII or Mermaid) showing: Flutter app → REST API → SQLite
  and Flutter app → WebDAV server → NAS.
- Annotated description of `Note`, `Page`, `InkStroke`, `Point` fields and their Android
  relevance (pressure, timestamp_ms, template).

**Files:** `docs/` only. No code changes.

---

### Phase 2 — Local note model in Flutter

**Goal:** Define and implement the local data layer in the Flutter app so notes can be
stored without the Python backend.

**Deliverables:**
- `app/lib/services/local_store.dart` — SQLite wrapper using `sqflite` (or `drift`) mirroring
  the schema in `nexanote/storage/database.py`.
- Models in `app/lib/models/` corresponding to `Note`, `Notebook`, `Page`, `InkStroke`, `Point`.
- Unit tests for create/read/update/delete.

**What it does NOT do:** Replace `AppState` or `ApiClient`. The existing Linux flow stays intact.

---

### Phase 3 — Offline save/load in AppState

**Goal:** `AppState` writes notes to the local DB first and reads from it on startup,
without requiring the Python backend.

**Deliverables:**
- `AppState` extended with a `localStore` reference.
- Note create/edit/delete operations write to local DB and set `sync_status = modified`.
- On startup, notes load from local DB; backend connection becomes optional.
- The `ConnectScreen` is updated to offer "continue offline" as a valid choice.

**Tests:** Widget tests covering offline note creation and persistence across app restarts.

---

### Phase 4 — WebDAV sync foundation

**Goal:** The app can sync notes to the Python backend (or any WebDAV server) when reachable.

**Deliverables (minimum viable):**
- `app/lib/services/webdav_sync.dart` — calls the Python backend's `/sync/trigger` endpoint
  when a network change is detected (using `connectivity_plus`).
- Sync status indicator in the UI showing last sync time and current `SyncStatus`.
- Settings screen cleaned up: one source of truth for sync URL (stored in
  `EncryptedSharedPreferences`; password never to disk).

**Deliverables (full native, follow-on PR):**
- Pure Dart WebDAV sync client that talks directly to any WebDAV server using HTTP PROPFIND/
  GET/PUT, replicating the logic in `nexanote/sync/client.py`.
- Conflict resolution ported to Dart (MERGE_STROKES as default).

---

### Phase 5 — Stylus and ink improvements

**Goal:** The ink canvas feels natural with an S Pen on Android.

**Deliverables:**
- Palm rejection: filter `MotionEvent.TOOL_TYPE_FINGER` vs `TOOL_TYPE_STYLUS` in the Flutter
  `Listener` widget (or a platform channel if needed).
- Tilt capture: extend `Point` with optional `tilt_x` / `tilt_y` if Flutter exposes
  `PointerEvent.radiusMinor` / `.orientation` on Android.
- S Pen button: map the side button to eraser tool toggle.
- Input latency audit: profile stroke-to-pixel time on a real Android device; adjust
  canvas repaint strategy if > 20 ms.
- Page size defaults tuned for phone screen aspect ratios.
- Page template rendering: draw lined/grid/dotted backgrounds (`template` field already
  exists in the model but is not rendered in the Flutter canvas).

**Model change:** Adding tilt to `Point` is a minor additive schema change. Existing
strokes without tilt data are read as `tilt_x=0, tilt_y=0`.

---

### Phase 6 — Android polish and release workflow

**Goal:** A working, installable APK/AAB that can be side-loaded or distributed through F-Droid.

**Deliverables:**
- `app/android/` build config reviewed and updated for a release build (signing config, minSdk,
  targetSdk appropriate for S Pen support).
- Adaptive app icon.
- Android-specific UX: back gesture handling, system insets, keyboard avoidance.
- A GitHub Actions workflow (`.github/workflows/`) that builds a release APK on every tagged
  commit.
- `README.md` updated with Android install instructions.
- F-Droid metadata (`fastlane/metadata/android/`) if the build is fully reproducible.

---

## Quick Reference — Key File Locations

| What | Where |
|---|---|
| Data models | `nexanote/models/note.py` |
| SQLite schema & CRUD | `nexanote/storage/database.py` |
| Conflict resolution | `nexanote/sync/conflict.py` |
| WebDAV provider | `nexanote/sync/webdav_provider.py` |
| Sync client (Python) | `nexanote/sync/client.py` |
| Flutter app entry | `app/lib/main.dart` |
| Ink canvas widget | `app/lib/widgets/ink_canvas.dart` |
| App state | `app/lib/services/app_state.dart` |
| REST API client | `app/lib/services/api_client.dart` |
| Flutter deps | `app/pubspec.yaml` |
| Python deps | `requirements.txt` |
| Test suite | `tests/` (64 tests) |
| Known issues | `docs/ISSUES_CURRENT_STATE.md` |
