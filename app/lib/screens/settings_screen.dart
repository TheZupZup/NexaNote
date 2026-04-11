// lib/screens/settings_screen.dart
// Écran de configuration — sync WebDAV NAS

import 'dart:async';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../services/app_state.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  // Backend API
  final _apiUrlCtrl = TextEditingController();

  // Sync WebDAV NAS
  final _nasUrlCtrl = TextEditingController();
  final _nasUserCtrl = TextEditingController();
  final _nasPassCtrl = TextEditingController();
  bool _nasPassVisible = false;

  // État
  bool _isSyncing = false;
  bool _isSaved = false;
  String? _syncMessage;
  Color _syncColor = Colors.green;
  Map<String, dynamic> _syncStatus = {};
  Map<String, dynamic> _stats = {};
  Map<String, dynamic> _storageInfo = {};
  final _dataDirCtrl = TextEditingController();
  bool _dataDirSaved = false;

  // Sync auto
  bool _autoSync = false;
  Timer? _autoSyncTimer;

  @override
  void initState() {
    super.initState();
    _loadSettings();
    _loadStats();
    _loadStorageInfo();
  }

  Future<void> _loadSettings() async {
    final prefs = await SharedPreferences.getInstance();
    final state = context.read<AppState>();
    setState(() {
      _apiUrlCtrl.text = state.apiUrl;
      _nasUrlCtrl.text = prefs.getString('nas_url') ?? 'http://192.168.132.214:5005/';
      _nasUserCtrl.text = prefs.getString('nas_user') ?? 'admin';
      _nasPassCtrl.text = prefs.getString('nas_pass') ?? '';
      _autoSync = prefs.getBool('auto_sync') ?? false;
    });
    if (_autoSync) _startAutoSync();
    await _loadSyncStatus();
  }

  Future<void> _loadStats() async {
    final state = context.read<AppState>();
    try {
      final stats = await state.client.getStats();
      setState(() => _stats = stats);
    } catch (_) {}
  }

  Future<void> _loadStorageInfo() async {
    final state = context.read<AppState>();
    try {
      final info = await state.client.getStorageInfo();
      final prefs = await SharedPreferences.getInstance();
      final savedDir = prefs.getString('data_dir') ?? info['data_dir'] ?? '';
      setState(() {
        _storageInfo = info;
        _dataDirCtrl.text = savedDir;
      });
    } catch (_) {}
  }

  Future<void> _saveDataDir() async {
    final prefs = await SharedPreferences.getInstance();
    final newDir = _dataDirCtrl.text.trim();
    if (newDir.isEmpty) return;
    await prefs.setString('data_dir', newDir);

    // Écrire dans ~/.nexanote-config pour que nexanote.sh le lise au prochain démarrage
    try {
      final home = Platform.environment['HOME'] ?? '/tmp';
      final configFile = File('$home/.nexanote-config');
      await configFile.writeAsString('data_dir=$newDir\n');
    } catch (_) {}

    setState(() => _dataDirSaved = true);
    Future.delayed(const Duration(seconds: 3), () {
      if (mounted) setState(() => _dataDirSaved = false);
    });
  }

  Future<void> _loadSyncStatus() async {
    final state = context.read<AppState>();
    try {
      final status = await state.client.getSyncStatus();
      setState(() => _syncStatus = status);
    } catch (_) {}
  }

  Future<void> _saveSettings() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('nas_url', _nasUrlCtrl.text.trim());
    await prefs.setString('nas_user', _nasUserCtrl.text.trim());
    await prefs.setString('nas_pass', _nasPassCtrl.text);
    await prefs.setBool('auto_sync', _autoSync);

    // Configure le sync sur le backend
    final state = context.read<AppState>();
    await state.client.configureSync(
      serverUrl: _nasUrlCtrl.text.trim(),
      username: _nasUserCtrl.text.trim(),
      password: _nasPassCtrl.text,
    );

    setState(() => _isSaved = true);
    Future.delayed(const Duration(seconds: 2), () {
      if (mounted) setState(() => _isSaved = false);
    });
  }

  Future<void> _triggerSync() async {
    await _saveSettings();
    setState(() {
      _isSyncing = true;
      _syncMessage = null;
    });

    final state = context.read<AppState>();
    final msg = await state.triggerSync();
    await _loadSyncStatus();
    await _loadStats();

    setState(() {
      _isSyncing = false;
      _syncMessage = msg;
      _syncColor = msg.contains('failed') || msg.contains('erreur')
          ? Colors.red
          : Colors.green;
    });
  }

  void _startAutoSync() {
    _autoSyncTimer?.cancel();
    _autoSyncTimer = Timer.periodic(const Duration(minutes: 5), (_) {
      _triggerSync();
    });
  }

  @override
  void dispose() {
    _autoSyncTimer?.cancel();
    _apiUrlCtrl.dispose();
    _nasUrlCtrl.dispose();
    _nasUserCtrl.dispose();
    _nasPassCtrl.dispose();
    _dataDirCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Settings'),
        actions: [
          if (_isSaved)
            const Padding(
              padding: EdgeInsets.only(right: 16),
              child: Row(
                children: [
                  Icon(Icons.check_circle, color: Colors.green, size: 18),
                  SizedBox(width: 4),
                  Text('Saved', style: TextStyle(color: Colors.green, fontSize: 13)),
                ],
              ),
            ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [

          // ── Stats ──────────────────────────────────────────────
          _SectionTitle(icon: Icons.bar_chart, title: 'Storage'),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceAround,
                children: [
                  _StatChip(label: 'Notebooks', value: '${_stats['notebooks'] ?? 0}'),
                  _StatChip(label: 'Notes', value: '${_stats['notes'] ?? 0}'),
                  _StatChip(label: 'Pages', value: '${_stats['pages'] ?? 0}'),
                  _StatChip(label: 'Strokes', value: '${_stats['strokes'] ?? 0}'),
                ],
              ),
            ),
          ),

          const SizedBox(height: 24),

          // ── Sync NAS WebDAV ────────────────────────────────────
          _SectionTitle(icon: Icons.sync, title: 'WebDAV Sync — NAS'),

          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // URL NAS
                  TextField(
                    controller: _nasUrlCtrl,
                    decoration: const InputDecoration(
                      labelText: 'NAS WebDAV URL',
                      hintText: 'http://192.168.132.214:5005/',
                      prefixIcon: Icon(Icons.dns_outlined),
                    ),
                  ),
                  const SizedBox(height: 12),

                  // Username
                  TextField(
                    controller: _nasUserCtrl,
                    decoration: const InputDecoration(
                      labelText: 'Username',
                      hintText: 'admin',
                      prefixIcon: Icon(Icons.person_outline),
                    ),
                  ),
                  const SizedBox(height: 12),

                  // Password
                  TextField(
                    controller: _nasPassCtrl,
                    obscureText: !_nasPassVisible,
                    decoration: InputDecoration(
                      labelText: 'Password',
                      prefixIcon: const Icon(Icons.lock_outline),
                      suffixIcon: IconButton(
                        icon: Icon(_nasPassVisible
                            ? Icons.visibility_off
                            : Icons.visibility),
                        onPressed: () => setState(
                            () => _nasPassVisible = !_nasPassVisible),
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),

                  // Auto sync toggle
                  SwitchListTile(
                    contentPadding: EdgeInsets.zero,
                    title: const Text('Auto sync every 5 minutes'),
                    subtitle: const Text('Sync automatically in background'),
                    value: _autoSync,
                    activeColor: const Color(0xFF6366F1),
                    onChanged: (v) {
                      setState(() => _autoSync = v);
                      if (v) {
                        _startAutoSync();
                      } else {
                        _autoSyncTimer?.cancel();
                      }
                    },
                  ),

                  const SizedBox(height: 8),

                  // Boutons
                  Row(
                    children: [
                      Expanded(
                        child: OutlinedButton.icon(
                          icon: const Icon(Icons.save_outlined, size: 16),
                          label: const Text('Save'),
                          onPressed: _saveSettings,
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: FilledButton.icon(
                          icon: _isSyncing
                              ? const SizedBox(
                                  width: 16,
                                  height: 16,
                                  child: CircularProgressIndicator(
                                      strokeWidth: 2,
                                      color: Colors.white),
                                )
                              : const Icon(Icons.sync, size: 16),
                          label: Text(_isSyncing ? 'Syncing...' : 'Sync now'),
                          onPressed: _isSyncing ? null : _triggerSync,
                          style: FilledButton.styleFrom(
                            backgroundColor: const Color(0xFF6366F1),
                          ),
                        ),
                      ),
                    ],
                  ),

                  // Message résultat sync
                  if (_syncMessage != null) ...[
                    const SizedBox(height: 12),
                    Container(
                      padding: const EdgeInsets.all(10),
                      decoration: BoxDecoration(
                        color: _syncColor.withOpacity(0.1),
                        borderRadius: BorderRadius.circular(8),
                        border: Border.all(color: _syncColor.withOpacity(0.3)),
                      ),
                      child: Row(
                        children: [
                          Icon(
                            _syncColor == Colors.green
                                ? Icons.check_circle_outline
                                : Icons.error_outline,
                            color: _syncColor,
                            size: 16,
                          ),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              _syncMessage!,
                              style: TextStyle(
                                  fontSize: 12, color: _syncColor),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],

                  // Dernier statut sync
                  if (_syncStatus.isNotEmpty &&
                      _syncStatus['status'] != 'never_synced') ...[
                    const SizedBox(height: 12),
                    const Divider(),
                    const SizedBox(height: 8),
                    Text('Last sync',
                        style: TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                            color: scheme.onSurface.withOpacity(0.5))),
                    const SizedBox(height: 6),
                    Row(
                      children: [
                        _SyncStat(
                            label: 'Pulled',
                            value: '${_syncStatus['notes_pulled'] ?? 0}'),
                        const SizedBox(width: 16),
                        _SyncStat(
                            label: 'Pushed',
                            value: '${_syncStatus['notes_pushed'] ?? 0}'),
                        const SizedBox(width: 16),
                        _SyncStat(
                            label: 'Conflicts',
                            value:
                                '${_syncStatus['conflicts_resolved'] ?? 0}'),
                        const SizedBox(width: 16),
                        _SyncStat(
                            label: 'Duration',
                            value:
                                '${(_syncStatus['duration_seconds'] as num?)?.toStringAsFixed(1) ?? '0'}s'),
                      ],
                    ),
                  ],
                ],
              ),
            ),
          ),

          const SizedBox(height: 24),

          // ── Dossier de notes ───────────────────────────────────
          _SectionTitle(icon: Icons.folder_outlined, title: 'Dossier de notes'),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // Dossier actuel (lu depuis l'API)
                  if (_storageInfo['data_dir'] != null) ...[
                    Row(children: [
                      Icon(Icons.folder, size: 16, color: const Color(0xFF6366F1)),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          'Actuel : ${_storageInfo['data_dir']}',
                          style: TextStyle(
                            fontSize: 12,
                            color: Theme.of(context).colorScheme.onSurface.withOpacity(0.6),
                            fontFamily: 'monospace',
                          ),
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      if (_storageInfo['db_size_mb'] != null)
                        Text(
                          '${_storageInfo['db_size_mb']} MB',
                          style: TextStyle(
                            fontSize: 11,
                            color: Theme.of(context).colorScheme.onSurface.withOpacity(0.4),
                          ),
                        ),
                    ]),
                    const SizedBox(height: 12),
                  ],

                  // Champ pour choisir un nouveau dossier
                  TextField(
                    controller: _dataDirCtrl,
                    decoration: const InputDecoration(
                      labelText: 'Dossier de données',
                      hintText: '/home/user/mes-notes',
                      prefixIcon: Icon(Icons.folder_open_outlined),
                    ),
                  ),
                  const SizedBox(height: 8),

                  // Info redémarrage
                  Container(
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: const Color(0xFF6366F1).withOpacity(0.08),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Row(children: [
                      const Icon(Icons.info_outline, size: 14, color: Color(0xFF6366F1)),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          'Le changement sera pris en compte au prochain démarrage via nexanote.sh',
                          style: TextStyle(
                            fontSize: 11,
                            color: Theme.of(context).colorScheme.onSurface.withOpacity(0.6),
                          ),
                        ),
                      ),
                    ]),
                  ),
                  const SizedBox(height: 12),

                  // Bouton sauvegarder
                  SizedBox(
                    width: double.infinity,
                    child: OutlinedButton.icon(
                      icon: _dataDirSaved
                          ? const Icon(Icons.check_circle, color: Colors.green, size: 16)
                          : const Icon(Icons.save_outlined, size: 16),
                      label: Text(_dataDirSaved ? 'Sauvegardé !' : 'Sauvegarder le dossier'),
                      onPressed: _saveDataDir,
                      style: _dataDirSaved
                          ? OutlinedButton.styleFrom(foregroundColor: Colors.green)
                          : null,
                    ),
                  ),
                ],
              ),
            ),
          ),

          const SizedBox(height: 24),

          // ── Backend API ────────────────────────────────────────
          _SectionTitle(icon: Icons.api, title: 'Backend API'),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                children: [
                  TextField(
                    controller: _apiUrlCtrl,
                    decoration: const InputDecoration(
                      labelText: 'API URL',
                      hintText: 'http://127.0.0.1:8766',
                      prefixIcon: Icon(Icons.computer_outlined),
                    ),
                  ),
                  const SizedBox(height: 12),
                  SizedBox(
                    width: double.infinity,
                    child: OutlinedButton.icon(
                      icon: const Icon(Icons.link, size: 16),
                      label: const Text('Reconnect'),
                      onPressed: () async {
                        await context.read<AppState>().connect(
                              url: _apiUrlCtrl.text.trim(),
                            );
                        if (mounted) {
                          ScaffoldMessenger.of(context).showSnackBar(
                            const SnackBar(content: Text('Reconnecting...')),
                          );
                        }
                      },
                    ),
                  ),
                ],
              ),
            ),
          ),

          const SizedBox(height: 24),

          // ── About ──────────────────────────────────────────────
          _SectionTitle(icon: Icons.info_outline, title: 'About'),
          Card(
            child: Column(
              children: [
                ListTile(
                  leading: Container(
                    width: 36,
                    height: 36,
                    decoration: BoxDecoration(
                      color: const Color(0xFF6366F1),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: const Icon(Icons.edit_note,
                        color: Colors.white, size: 22),
                  ),
                  title: const Text('NexaNote',
                      style: TextStyle(fontWeight: FontWeight.bold)),
                  subtitle: const Text('v0.1.0 — Open-source note-taking'),
                ),
                const Divider(height: 1),
                ListTile(
                  leading: const Icon(Icons.code, size: 20),
                  title: const Text('GitHub'),
                  subtitle: const Text('github.com/YOUR_USER/NexaNote'),
                  trailing: const Icon(Icons.open_in_new, size: 16),
                  onTap: () {},
                ),
                ListTile(
                  leading: const Icon(Icons.balance, size: 20),
                  title: const Text('License'),
                  subtitle: const Text('MPL 2.0'),
                ),
              ],
            ),
          ),

          const SizedBox(height: 32),
        ],
      ),
    );
  }
}

// ── Widgets helpers ──────────────────────────────────────────────

class _SectionTitle extends StatelessWidget {
  final IconData icon;
  final String title;
  const _SectionTitle({required this.icon, required this.title});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        children: [
          Icon(icon, size: 16,
              color: Theme.of(context).colorScheme.onSurface.withOpacity(0.5)),
          const SizedBox(width: 6),
          Text(title.toUpperCase(),
              style: TextStyle(
                  fontSize: 11,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.8,
                  color: Theme.of(context)
                      .colorScheme
                      .onSurface
                      .withOpacity(0.5))),
        ],
      ),
    );
  }
}

class _StatChip extends StatelessWidget {
  final String label;
  final String value;
  const _StatChip({required this.label, required this.value});

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Text(value,
            style: const TextStyle(
                fontSize: 22,
                fontWeight: FontWeight.bold,
                color: Color(0xFF6366F1))),
        Text(label,
            style: TextStyle(
                fontSize: 11,
                color: Theme.of(context)
                    .colorScheme
                    .onSurface
                    .withOpacity(0.5))),
      ],
    );
  }
}

class _SyncStat extends StatelessWidget {
  final String label;
  final String value;
  const _SyncStat({required this.label, required this.value});

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(value,
            style: const TextStyle(
                fontSize: 16, fontWeight: FontWeight.bold)),
        Text(label,
            style: TextStyle(
                fontSize: 11,
                color: Theme.of(context)
                    .colorScheme
                    .onSurface
                    .withOpacity(0.5))),
      ],
    );
  }
}

