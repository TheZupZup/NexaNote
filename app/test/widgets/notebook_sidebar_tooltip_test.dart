import 'package:flutter_test/flutter_test.dart';
import 'package:nexanote/widgets/notebook_sidebar.dart';

void main() {
  group('NotebookSidebar.buildSyncTooltip', () {
    test('returns "Syncing..." while syncing', () {
      expect(
        NotebookSidebar.buildSyncTooltip(
          isSyncing: true,
          hasSyncError: false,
        ),
        'Syncing...',
      );
    });

    test('returns the error message when sync failed', () {
      expect(
        NotebookSidebar.buildSyncTooltip(
          isSyncing: false,
          hasSyncError: true,
          syncError: 'Sync failed: bad creds',
        ),
        'Sync failed: bad creds',
      );
    });

    test('returns "Not synced yet" when never synced and idle', () {
      expect(
        NotebookSidebar.buildSyncTooltip(
          isSyncing: false,
          hasSyncError: false,
        ),
        'Not synced yet',
      );
    });

    test('returns relative last-sync time when available', () {
      final now = DateTime(2026, 5, 2, 12, 0, 0);
      final lastSync = now.subtract(const Duration(minutes: 2));
      expect(
        NotebookSidebar.buildSyncTooltip(
          isSyncing: false,
          hasSyncError: false,
          lastSyncTime: lastSync,
          now: now,
        ),
        'Last synced: 2 minutes ago',
      );
    });
  });
}
