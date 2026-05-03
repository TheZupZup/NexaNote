/// Strips display artifacts that leak into note titles from the WebDAV
/// slug or filesystem. Lives in its own file so both the sync engine and
/// the live API client can call it without dragging the rest of the sync
/// machinery into api_client.dart (which would create an import cycle
/// through SyncService).
///
/// Specifically removes:
/// - a trailing `.md` extension,
/// - the `__<id-prefix>` slug suffix appended by the WebDAV provider —
///   `<title-slug>__<id-prefix>` round-trips through `.title()` into
///   things like `Foo Bar__Md.Q2Hhd` for plain Markdown files whose
///   synthetic id starts with `md.`,
/// - any combination of the two (e.g. `My Note__Md.Q2Hhd.md`).
///
/// The cleanup is conservative: when the suffix doesn't look like an
/// id-prefix the title is returned unchanged.
String cleanRemoteTitle(String raw) {
  var title = raw.trim();
  while (true) {
    final stripped = _stripOnce(title);
    if (stripped == title) break;
    title = stripped;
  }
  return title.isEmpty ? raw.trim() : title;
}

String _stripOnce(String input) {
  var out = input;
  if (out.toLowerCase().endsWith('.md')) {
    out = out.substring(0, out.length - 3).trimRight();
  }
  final match = _slugSuffixRe.firstMatch(out);
  if (match != null) {
    out = out.substring(0, match.start).trimRight();
  }
  return out;
}

final RegExp _slugSuffixRe =
    RegExp(r'__(?:[0-9a-fA-F]{1,16}|[Mm][Dd]\.[A-Za-z0-9_-]+)\s*$');
