# NexaNote — Vault-First, Markdown-First Roadmap

> **Status:** Design document. No code changes are part of this document. It describes
> how NexaNote can evolve toward a vault-based, Markdown-first architecture inspired by
> the *concepts* common to Markdown note-taking tools (Obsidian, Foam, Logseq, etc.),
> building on the `PlainMarkdownNoteStore` backend that already exists in the codebase.

---

## 1. Clean-Room Policy

This roadmap and any work that follows from it are **clean-room**:

- We draw only on **publicly documented, common concepts** of Markdown note-taking:
  a folder of `.md` files as a "vault", YAML frontmatter, `[[wikilinks]]`, `#tags`,
  and backlinks. These are widely shared conventions, not proprietary to any one app.
- We do **not** copy Obsidian (or any other tool's) source code, UI layouts, CSS,
  icons, assets, plugin APIs, or internal data formats.
- We do **not** reverse-engineer closed file formats. Where a format choice exists
  (e.g. how a sidecar is named), we pick what suits NexaNote, not what mirrors a
  specific competitor.
- "Obsidian-compatible" in this document means **the files we write are plain enough
  that Obsidian (or any editor) can open and edit them** — interoperability through
  open standards, not imitation of internals.
- Naming: the file is titled "obsidian-inspired" because Obsidian is the most familiar
  reference point for readers. The design goal is generic Markdown-vault portability.

---

## 2. Goals

- **Treat the notes folder as a vault.** A single directory of human-readable files is
  the source of truth, browsable and editable outside NexaNote.
- **Markdown files are canonical.** The `.md` body is the note. NexaNote metadata is
  auxiliary, never a prerequisite for a file to be a valid note.
- **Human-readable filenames.** `Meeting notes 2026-05.md`, not `a1b2c3d4.md`.
- **Round-trips cleanly.** A note exported, edited in another editor, and re-read by
  NexaNote keeps its identity, metadata, pages, and drawings.
- **YAML frontmatter support** as a first-class (eventually default) way to carry
  metadata inside the `.md` file.
- **`[[wikilinks]]`** between notes, with backlinks surfaced in the UI.
- **`#tags`** parsed from the body in addition to the existing structured `tags` list.
- **Drawings/stylus data live beside notes** as JSON, keyed by stable note id.
- **Idempotent sync.** Reading and re-writing an unchanged note produces no spurious
  diffs, no duplicate files, and no churn for sync clients.

---

## 3. Non-Goals

- **Not** reimplementing Obsidian's plugin ecosystem, graph view styling, or canvas
  format.
- **Not** dropping the YAML-frontmatter `FileNoteStore` backend — it stays as an option.
- **Not** moving away from file storage toward a database-of-record. SQLite/indexes may
  be used as a *cache*, never as the canonical store for vault mode.
- **Not** real-time collaboration or CRDT editing (covered, if ever, separately).
- **Not** handwriting OCR or full-text semantic search in this roadmap.
- **Not** breaking the existing REST/WebDAV/sync API surface. New behavior is additive.

---

## 4. Where We Are Today

NexaNote already ships two storage backends with identical public surfaces (so REST
routes, the WebDAV provider, and the sync engine work against either):

| Backend | Layout | Metadata home |
|---|---|---|
| `FileNoteStore` (`nexanote/storage/file_store.py`) | `notes/<id>.md` with YAML frontmatter | Frontmatter inside the `.md` |
| `PlainMarkdownNoteStore` (`nexanote/storage/plain_store.py`) | `notes/<Sanitized Title>.md` + `notes/<Sanitized Title>.json` sidecar | JSON sidecar beside the `.md` |

The Plain backend is the foundation for vault mode. It already delivers several of the
goals above:

- **Human-readable filenames** via `sanitize_filename` (`nexanote/storage/export.py`).
- **Drawings beside notes** as `drawings/<note_id>.json`, keyed by the stable id.
- **No filename-into-title corruption for managed notes**: the canonical title lives in
  the sidecar (`_build_sidecar` / `_note_from_sidecar`), so renaming the file does not
  rewrite the title, and the title can contain characters the filename can't.
- **Drop-in plain `.md` files work**: a file with no sidecar is synthesized into a Note
  via `synthesize_plain_md_note`, with a deterministic id derived from the filename
  (`plain_md_id_from_stem` / `stem_from_plain_md_id`, base64url of the stem). This avoids
  duplicate imports — the same file always resolves to the same id until the user saves
  through NexaNote and a real UUID sidecar is written.
- **Atomic, rename-safe writes**: `save_note` writes the new `.md`+`.json` pair before
  deleting the old stem, so a crash mid-rename can't lose the note.

What is **missing** for a true vault experience:

- No YAML frontmatter in Plain mode (metadata is in a separate JSON file).
- No `[[wikilink]]` parsing, resolution, or backlink index.
- No `#tag` parsing from body text (tags only come from the structured list).
- No vault-wide link/tag graph or "unresolved links" view.
- Synthesized plain notes are minimally typed (`NoteType.TYPED`, single page) — frontmatter
  could carry richer metadata for hand-authored files.

---

## 5. Proposed Vault Layout

A vault is the `notes/` directory plus its sibling support folders. Target layout:

```
<vault>/
    notes/
        Meeting notes 2026-05.md       # canonical note body (+ optional frontmatter)
        Meeting notes 2026-05.json     # OPTIONAL NexaNote sidecar (see §6)
        Project ideas.md
        ...
    drawings/
        <note_id>.json                 # stylus strokes, keyed by stable note id
    notebooks/
        <notebook_id>.yaml             # notebook metadata (not user-facing vault files)
    .nexanote/                         # NexaNote-private, ignorable by other tools
        index.sqlite                   # OPTIONAL cache: links, tags, backlinks
        storage_mode                   # existing .nexanote_storage_mode marker
```

Principles:

- Everything a human edits lives in `notes/` as readable `.md`.
- NexaNote-private state goes under a single dotted folder (`.nexanote/`) so other tools
  can ignore it with one rule.
- `drawings/` and `notebooks/` keep their current names and semantics — they are not
  part of the "documents a user edits in a Markdown editor" surface, so they don't clutter
  the vault.
- Subfolders inside `notes/` (e.g. `notes/Work/…`) map to notebook hierarchy. This is a
  future enhancement (§9); today notebooks are flat metadata files.

---

## 6. Markdown / Frontmatter Format

### 6.1 Two metadata homes, one canonical body

The `.md` **body** is always canonical. Metadata can live in one of two places, and the
roadmap converges on frontmatter as the preferred home:

1. **Frontmatter (target default for vault mode):** YAML block at the top of the `.md`.
2. **JSON sidecar (current Plain behavior, retained as fallback):** `<stem>.json`.

Resolution order when reading a note: frontmatter (if present) **overlays** sidecar
values; sidecar fills gaps; filesystem stats fill remaining gaps (as
`synthesize_plain_md_note` already does for `created_at`/`updated_at`).

### 6.2 Frontmatter schema

```markdown
---
id: 3f9a1c08-...           # stable UUID; absent => derived from filename (see §8)
title: Meeting notes       # canonical title; MAY differ from filename
tags: [meeting, q2]        # structured tags; merged with inline #tags from body
notebook: Work             # notebook name or id
created: 2026-05-24T09:00:00Z
updated: 2026-05-24T10:30:00Z
pinned: false
note_type: typed           # typed | handwritten | mixed
nexanote:                  # namespaced block for app-specific fields
  pages:                   # page geometry/templates (today lives in the sidecar)
    - page_number: 1
      template: blank
      width_px: 1404
      height_px: 1872
  schema: 1
---

Note body in Markdown. Supports [[wikilinks]] and #inline-tags.
```

Design rules:

- **App-specific fields are namespaced** under a `nexanote:` key so they never collide
  with fields other tools (or the user) add to frontmatter. Standard fields
  (`title`, `tags`, `created`, `updated`) use conventional names for interoperability.
- **Unknown frontmatter keys are preserved verbatim** on round-trip. NexaNote must not
  drop fields it doesn't understand (otherwise it corrupts notes authored elsewhere).
- **Pages**: multi-page notes keep using the existing in-body page delimiter
  (`<!-- nexanote:page N -->`, handled by `_split_pages_body`/`_join_pages_body`) plus
  per-page geometry under `nexanote.pages`. This is unchanged from today's sidecar.
- **Drawings stay external** in `drawings/<note_id>.json` — stroke data is large and
  binary-ish, and does not belong inline in a human-edited Markdown file.

### 6.3 Wikilinks and tags

- `[[Target]]` and `[[Target|Alias]]` resolve to a note **by title first, then by
  filename stem**. Resolution is case-insensitive, matching filename collision handling.
- Unresolved links are recorded (for an "unresolved links" view) but never auto-create
  files silently during sync (that would break idempotency).
- `#tag` tokens in the body are extracted and **merged** with the structured `tags` list.
  The merged set is what search and the tag index use; the structured list remains the
  canonical store so editing a note in NexaNote round-trips deterministically.
- Tag/link extraction is **derived data** — it is cached (in `.nexanote/index.sqlite`),
  never written back into the body. The body is owned by the user.

---

## 7. Sync Implications

The existing WebDAV provider and sync client (`nexanote/sync/`) operate on the store's
public surface, so vault mode rides on top without protocol changes. Key constraints:

- **Idempotency is the hard requirement.** Reading a note and saving it unchanged MUST
  produce byte-identical files. This means:
  - Frontmatter key ordering is **stable and canonical** (fixed field order, sorted
    unknown keys, consistent YAML quoting/indent).
  - Timestamps serialize in one fixed format (`_fmt_dt`).
  - `updated_at` is **not** bumped on a pure read or a metadata-only re-save that changes
    nothing (mirror the `save_pages=False` / `_merge_metadata` path).
  - Inline-tag extraction never rewrites the body.
- **No duplicate imports.** The deterministic `plain_md_id_from_stem` scheme already
  guarantees a sidecar-less `.md` resolves to the same id every scan. Frontmatter `id`
  takes precedence when present; absent, the derived id is used. A note must never appear
  twice because it was seen via two code paths.
- **Conflict resolution** keeps the existing strategies (`LAST_WRITE_WINS`, `KEEP_BOTH`,
  `MERGE_STROKES`). Frontmatter-carried metadata merges by last-write-wins per field;
  body merges per existing page logic; strokes union by id. No new strategy is required.
- **WebDAV visibility:** the `.nexanote/` cache and `drawings/` are sync targets like
  today, but the index cache (`index.sqlite`) should be treated as **rebuildable** and
  ideally excluded from sync to avoid binary churn — it can be regenerated from the vault.

---

## 8. Identity & Filename Rules (Never Corrupt Titles)

These rules formalize what the Plain backend already does and extend it for frontmatter:

1. **Title is never derived from the filename for managed notes.** The title comes from
   frontmatter `title`, else the sidecar `title`. Only a truly bare `.md` (no frontmatter,
   no sidecar) falls back to the filename stem as a *display* title (current
   `synthesize_plain_md_note` behavior) — and even then the stem is used verbatim, not
   "humanized" (no underscore-to-space mangling, no title-casing).
2. **Filenames derive from titles, not the reverse.** `sanitize_filename` produces the
   stem; collisions get ` (N)` suffixes via `_pick_stem`. A rename of the title moves the
   file; a rename of the file (by an external tool) does **not** change the stored title
   when an `id` is present to re-link them.
3. **Stable id precedence:** frontmatter `id` → sidecar `id` → derived
   `plain_md_id_from_stem(stem)`. The derived id is deterministic and reversible, so a
   hand-dropped file keeps a consistent identity across scans until promoted to a UUID.
4. **Promotion:** the first time a bare file is saved through NexaNote, it gets a UUID
   `id` (in frontmatter or sidecar) and is no longer addressed by the derived id. Migration
   must map the old derived id to the new UUID so links/backlinks don't break.

---

## 9. Migration Path

Migration is **incremental and reversible**, gated so existing users opt in.

**Phase A — Frontmatter read support (non-breaking).**
Plain backend learns to read YAML frontmatter and overlay it on sidecar/derived metadata.
No writes change. Existing sidecar-only notes are unaffected. Hand-authored frontmatter
notes from other tools now import correctly instead of treating frontmatter as body text.

**Phase B — Frontmatter write (opt-in flag).**
A storage-mode variant (e.g. `plain+frontmatter`) writes metadata into the `.md`
frontmatter instead of the JSON sidecar. A one-time, idempotent migration walks the vault,
reads each `<stem>.json`, folds it into the `.md` frontmatter, and removes the sidecar
once verified. Re-running the migration is a no-op (idempotent).

**Phase C — Wikilink/tag index.**
Build `.nexanote/index.sqlite` as a derived cache: scan bodies for `[[links]]` and
`#tags`, store resolved/unresolved edges and the tag set. The index is rebuildable from
the vault at any time; deleting it loses no canonical data.

**Phase D — Backlinks & vault UX in the app.**
Surface backlinks, tag browsing, and unresolved links in the Flutter UI. Optional graph
view (clean-room, our own rendering).

**Phase E — Notebook-as-subfolder (optional, later).**
Map notebook hierarchy onto `notes/<Notebook>/…` subfolders instead of (or alongside) the
`notebooks/*.yaml` metadata files. This is the most invasive change and is deliberately
last; it requires careful migration of existing flat notes.

Rollback: each phase before E leaves canonical `.md` bodies intact, so a user can switch
back to sidecar mode or stop using NexaNote without data loss.

---

## 10. Future Features (beyond the core roadmap)

- **Aliases:** frontmatter `aliases: [...]` so a note resolves under multiple `[[names]]`.
- **Embeds / transclusion:** `![[Note]]` to include another note's content (read-only render).
- **Daily notes:** a date-templated quick-capture flow.
- **Templates:** user-defined frontmatter+body templates for new notes.
- **Tag hierarchy:** `#area/subarea` nested tags in the index.
- **Attachment handling:** images/PDFs stored under an `attachments/` folder with relative
  links from the body.
- **Graph view:** clean-room visualization of the link index.
- **Full-text search** backed by the index cache.

---

## 11. Test Plan

Tests live in `tests/` (alongside `test_plain_store.py`, `test_file_store.py`). New
coverage to add as phases land:

**Round-trip & idempotency**
- Save a note, read it, save it again unchanged → files are byte-identical (no `updated_at`
  bump, no frontmatter reordering, no sidecar churn).
- Frontmatter with unknown keys → keys preserved verbatim after a NexaNote save.
- Multi-page note round-trips page delimiters and per-page geometry.

**Identity & filenames**
- Title with characters illegal in filenames → stored title intact, filename sanitized.
- Rename title → file moves, old pair removed, id stable, drawings still linked.
- Two notes with the same title → ` (N)` suffix, distinct ids, no overwrite.
- Bare `.md` dropped in → synthesized once, same derived id on repeated scans (no
  duplicate import). Title equals the stem verbatim (no mangling).
- Promote a bare file to a saved note → gains UUID id; derived id maps to it; no duplicate.

**Frontmatter**
- Read frontmatter overlays sidecar; sidecar fills gaps; FS stats fill the rest.
- Migration A→B folds sidecar into frontmatter; re-running migration is a no-op.
- Frontmatter `id` wins over derived id; absent → derived id used.

**Wikilinks & tags**
- `[[Target]]`, `[[Target|Alias]]` resolve by title then stem, case-insensitive.
- Unresolved link recorded, no file silently created.
- Inline `#tags` merged with structured tags; body never rewritten by extraction.
- Backlink index: A links B ⇒ B lists A as backlink; deleting A updates the index.

**Sync**
- Unchanged note over a sync cycle → no diff pushed (idempotent).
- Index cache deleted → rebuilt from vault with identical results.
- Conflict cases: metadata last-write-wins per field; strokes union by id (existing
  `MERGE_STROKES` behavior preserved).

**Regression**
- All existing `FileNoteStore` and `PlainMarkdownNoteStore` tests continue to pass —
  vault changes are additive and must not alter YAML-frontmatter-backend behavior.
