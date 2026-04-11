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
  final _nasUrlCtrl = TextEditingController();
  final _nasUserCtrl = TextEditingController();
  final _nasPassCtrl = TextEditingController();
  bool _passVisible = false;
  bool _isSyncing = false;
  String? _syncMsg;
  Map _stats = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final prefs = await SharedPreferences.getInstance();
    setState(() {
      _nasUrlCtrl.text = prefs.getString('nas_url') ?? '';
      _nasUserCtrl.text = prefs.getString('nas_user') ?? 'admin';
      _nasPassCtrl.text = prefs.getString('nas_pass') ?? '';
    });
    try {
      final s = await context.read<AppState>().client.getStats();
      setState(() => _stats = s);
    } catch (_) {}
  }

  Future<void> _save() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('nas_url', _nasUrlCtrl.text.trim());
    await prefs.setString('nas_user', _nasUserCtrl.text.trim());
    await prefs.setString('nas_pass', _nasPassCtrl.text);
    await context.read<AppState>().client.configureSync(
      serverUrl: _nasUrlCtrl.text.trim(),
      username: _nasUserCtrl.text.trim(),
      password: _nasPassCtrl.text,
    );
    if (mounted) ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Saved ✅')));
  }

  Future<void> _sync() async {
    await _save();
    setState(() { _isSyncing = true; _syncMsg = null; });
    final msg = await context.read<AppState>().triggerSync();
    setState(() { _isSyncing = false; _syncMsg = msg; });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        Card(child: Padding(padding: const EdgeInsets.all(16),
          child: Row(mainAxisAlignment: MainAxisAlignment.spaceAround, children: [
            _stat('Notebooks', '${_stats['notebooks'] ?? 0}'),
            _stat('Notes', '${_stats['notes'] ?? 0}'),
            _stat('Pages', '${_stats['pages'] ?? 0}'),
          ]))),
        const SizedBox(height: 16),
        const Text('WEBDAV SYNC', style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, letterSpacing: 1)),
        const SizedBox(height: 8),
        Card(child: Padding(padding: const EdgeInsets.all(16), child: Column(children: [
          TextField(controller: _nasUrlCtrl,
            decoration: const InputDecoration(labelText: 'NAS URL', hintText: 'http://192.168.X.X:5005/', prefixIcon: Icon(Icons.dns_outlined))),
          const SizedBox(height: 12),
          TextField(controller: _nasUserCtrl,
            decoration: const InputDecoration(labelText: 'Username', prefixIcon: Icon(Icons.person_outline))),
          const SizedBox(height: 12),
          TextField(controller: _nasPassCtrl, obscureText: !_passVisible,
            decoration: InputDecoration(labelText: 'Password', prefixIcon: const Icon(Icons.lock_outline),
              suffixIcon: IconButton(icon: Icon(_passVisible ? Icons.visibility_off : Icons.visibility),
                onPressed: () => setState(() => _passVisible = !_passVisible)))),
          const SizedBox(height: 16),
          Row(children: [
            Expanded(child: OutlinedButton(onPressed: _save, child: const Text('Save'))),
            const SizedBox(width: 12),
            Expanded(child: FilledButton.icon(
              onPressed: _isSyncing ? null : _sync,
              icon: _isSyncing
                ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                : const Icon(Icons.sync, size: 16),
              label: Text(_isSyncing ? 'Syncing...' : 'Sync now'),
              style: FilledButton.styleFrom(backgroundColor: const Color(0xFF6366F1)))),
          ]),
          if (_syncMsg != null) ...[
            const SizedBox(height: 12),
            Container(padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(color: Colors.green.withOpacity(0.1), borderRadius: BorderRadius.circular(8)),
              child: Text(_syncMsg!, style: const TextStyle(fontSize: 12, color: Colors.green))),
          ],
        ]))),
      ]),
    );
  }

  Widget _stat(String label, String value) => Column(children: [
    Text(value, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold, color: Color(0xFF6366F1))),
    Text(label, style: const TextStyle(fontSize: 11)),
  ]);
}
