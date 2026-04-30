import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models/candle_interval.dart';
import '../models/gate_contract.dart';
import '../models/gate_ranked_contract.dart';
import '../models/gate_ticker.dart';
import '../models/hourly_candle.dart';
import '../services/gate_api_service.dart';
import '../services/gate_market_stream_service.dart';
import '../services/local_notification_service.dart';

class GateScannerPage extends StatefulWidget {
  const GateScannerPage({super.key});

  @override
  State<GateScannerPage> createState() => _GateScannerPageState();
}

class _GateScannerPageState extends State<GateScannerPage> {
  static const int _rankingLimit = 30;
  static const int _historyLimit = 20;
  static const List<int> _targetMultipliers = <int>[9, 10, 11, 12];

  late final GateApiService _apiService;
  late final GateMarketStreamService _marketStreamService;
  final Map<CandleInterval, Map<String, List<HourlyCandle>>> _historyCache = {
    CandleInterval.h1: <String, List<HourlyCandle>>{},
    CandleInterval.h4: <String, List<HourlyCandle>>{},
  };
  final Map<CandleInterval, Set<String>> _historyInitialized = {
    CandleInterval.h1: <String>{},
    CandleInterval.h4: <String>{},
  };
  final Set<String> _expandedInstIds = <String>{};
  final Set<String> _loadingHistoryKeys = <String>{};
  final Set<String> _triggeredMonitorSignatures = <String>{};
  final Set<String> _dismissedMonitorSignatures = <String>{};
  final Map<String, GateTicker24h> _pendingTickerUpdates =
      <String, GateTicker24h>{};

  Map<String, GateContract> _contractsById = const <String, GateContract>{};
  Map<String, GateTicker24h> _tickersByInstId = const <String, GateTicker24h>{};
  List<GateRankedContract> _rankings = const <GateRankedContract>[];
  List<_PinnedAlertEntry> _pinnedAlerts = const <_PinnedAlertEntry>[];
  CandleInterval _selectedInterval = CandleInterval.h1;
  bool _isRefreshing = false;
  bool _isMonitorDialogVisible = false;
  String? _errorMessage;
  DateTime? _lastUpdatedAt;
  Timer? _refreshTimer;
  Timer? _tickerFlushTimer;

  @override
  void initState() {
    super.initState();
    _apiService = GateApiService();
    _marketStreamService = GateMarketStreamService();
    unawaited(_bootstrapMarketData(initialLoad: true));
    _refreshTimer = Timer.periodic(const Duration(minutes: 5), (_) {
      unawaited(_bootstrapMarketData());
    });
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    _tickerFlushTimer?.cancel();
    _marketStreamService.dispose();
    _apiService.dispose();
    super.dispose();
  }

  Future<void> _bootstrapMarketData({bool initialLoad = false}) async {
    if (_isRefreshing) {
      return;
    }

    setState(() {
      _isRefreshing = true;
      if (initialLoad) {
        _errorMessage = null;
      }
    });

    try {
      final results = await Future.wait([
        _apiService.fetchUsdtContracts(),
        _apiService.fetchTickerMap(),
      ]);
      final contracts = results[0] as List<GateContract>;
      final tickerMap = results[1] as Map<String, GateTicker24h>;
      final contractsById = <String, GateContract>{
        for (final contract in contracts) contract.name: contract,
      };
      final previousIds = _currentRankingIds();
      final filteredTickerMap = <String, GateTicker24h>{
        for (final entry in tickerMap.entries)
          if (contractsById.containsKey(entry.key) &&
              entry.value.changePercent24h.isFinite)
              entry.key: entry.value,
      };
      final rankings = _buildTopRankings(contractsById, filteredTickerMap);
      final nextIds = rankings.map((item) => item.contract.name).toSet();
      final newIds = nextIds.difference(previousIds);

      if (!mounted) {
        return;
      }

      setState(() {
        _contractsById = contractsById;
        _tickersByInstId = filteredTickerMap;
        _rankings = rankings;
        _lastUpdatedAt = DateTime.now();
        _errorMessage = null;
        _expandedInstIds.removeWhere((instId) => !nextIds.contains(instId));
      });

      await Future.wait([
        for (final interval in const [CandleInterval.h1, CandleInterval.h4])
          _ensureHistoryForRankings(
            interval: interval,
            onlyIds: nextIds,
            fullReloadIds: newIds,
          ),
      ]);
      await _marketStreamService.start(
        onTicker: _handleTickerUpdate,
        onCandle: _handleCandleUpdate,
        onError: _handleStreamError,
      );
      await _marketStreamService.updateTickerSubscriptions(_contractsById.keys);
      await _marketStreamService.updateCandleSubscriptions(
        contracts: nextIds,
        intervals: const [CandleInterval.h1, CandleInterval.h4],
      );
    } catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _errorMessage = error.toString();
      });
    } finally {
      if (mounted) {
        setState(() {
          _isRefreshing = false;
        });
      }
    }
  }

  void _handleTickerUpdate(GateTicker24h ticker) {
    final instId = ticker.contract;
    if (!_contractsById.containsKey(instId) || !ticker.changePercent24h.isFinite) {
      return;
    }

    _pendingTickerUpdates[instId] = ticker;
    _tickerFlushTimer ??= Timer(const Duration(milliseconds: 250), () {
      _tickerFlushTimer = null;
      _flushTickerUpdates();
    });
  }

  void _flushTickerUpdates() {
    if (!mounted || _pendingTickerUpdates.isEmpty) {
      return;
    }

    final previousIds = _currentRankingIds();
    final nextTickerMap = Map<String, GateTicker24h>.from(_tickersByInstId)
      ..addAll(_pendingTickerUpdates);
    _pendingTickerUpdates.clear();

    final nextRankings = _buildTopRankings(_contractsById, nextTickerMap);
    final nextIds = nextRankings.map((item) => item.contract.name).toSet();

    setState(() {
      _tickersByInstId = nextTickerMap;
      _rankings = nextRankings;
      _lastUpdatedAt = DateTime.now();
      _expandedInstIds.removeWhere((id) => !nextIds.contains(id));
    });

    if (_setChanged(previousIds, nextIds)) {
      unawaited(
        _handleRankingMembershipChanged(
          previousIds: previousIds,
          nextIds: nextIds,
        ),
      );
    }
  }

  void _handleCandleUpdate(
    String instId,
    CandleInterval interval,
    HourlyCandle candle,
  ) {
    final cache = _historyCache[interval];
    if (cache == null) {
      return;
    }
    final initialized = _historyInitialized[interval];
    if (initialized == null) {
      return;
    }
    final existing = cache[instId];
    if (existing == null && !_currentRankingIds().contains(instId)) {
      return;
    }

    if (!mounted) {
      return;
    }

    final updated = _mergeCandles(existing, [candle]);

    setState(() {
      cache[instId] = updated;
      _lastUpdatedAt = DateTime.now();
    });

    if (!initialized.contains(instId)) {
      unawaited(
        _syncHistory(
          instId: instId,
          interval: interval,
          fullReload: true,
        ),
      );
      return;
    }

    _evaluateMonitorSignals(
      instId: instId,
      interval: interval,
      candles: updated,
    );
  }

  void _handleStreamError(String message) {
    if (!mounted) {
      return;
    }
    setState(() {
      _errorMessage = message;
    });
  }

  Future<void> _switchInterval(CandleInterval interval) async {
    if (_selectedInterval == interval) {
      return;
    }

    setState(() {
      _selectedInterval = interval;
    });

    await _ensureHistoryForRankings(interval: interval);
  }

  Future<void> _ensureHistoryForRankings({
    required CandleInterval interval,
    Set<String> onlyIds = const <String>{},
    Set<String> fullReloadIds = const <String>{},
  }) async {
    final targetIds = (onlyIds.isEmpty
            ? _rankings.map((item) => item.contract.name)
            : onlyIds)
        .toSet();

    if (targetIds.isEmpty) {
      return;
    }

    await Future.wait(
      targetIds.map(
        (instId) => _syncHistory(
          instId: instId,
          interval: interval,
          fullReload: fullReloadIds.contains(instId),
        ),
      ),
    );
  }

  Future<void> _handleRankingMembershipChanged({
    required Set<String> previousIds,
    required Set<String> nextIds,
  }) async {
    final newIds = nextIds.difference(previousIds);
    if (newIds.isNotEmpty) {
      await Future.wait([
        for (final interval in const [CandleInterval.h1, CandleInterval.h4])
          _ensureHistoryForRankings(
            interval: interval,
            onlyIds: newIds,
            fullReloadIds: newIds,
          ),
      ]);
    }

    await _marketStreamService.updateCandleSubscriptions(
      contracts: nextIds,
      intervals: const [CandleInterval.h1, CandleInterval.h4],
    );
  }

  Future<void> _syncHistory({
    required String instId,
    required CandleInterval interval,
    bool fullReload = false,
  }) async {
    final cache = _historyCache[interval]!;
    final initialized = _historyInitialized[interval]!;
    final existing = cache[instId];
    final shouldFullReload =
        fullReload ||
        existing == null ||
        existing.isEmpty ||
        !initialized.contains(instId);
    final loadingKey = _historyLoadingKey(instId, interval);

    if (_loadingHistoryKeys.contains(loadingKey)) {
      return;
    }

    if (mounted) {
      setState(() {
        _loadingHistoryKeys.add(loadingKey);
      });
    }

    try {
      final incoming = await _apiService.fetchCandles(
        instId,
        interval: interval,
        limit: shouldFullReload ? _historyLimit : 3,
      );

      if (!mounted) {
        return;
      }

      final merged = shouldFullReload ? incoming : _mergeCandles(existing, incoming);

      setState(() {
        cache[instId] = merged;
        initialized.add(instId);
      });
      _evaluateMonitorSignals(
        instId: instId,
        interval: interval,
        candles: merged,
      );
    } catch (_) {
      if (mounted && existing == null) {
        setState(() {
          cache[instId] = const <HourlyCandle>[];
        });
      }
    } finally {
      if (mounted) {
        setState(() {
          _loadingHistoryKeys.remove(loadingKey);
        });
      }
    }
  }

  List<HourlyCandle> _mergeCandles(
    List<HourlyCandle>? existing,
    List<HourlyCandle> incoming,
  ) {
    final merged = <int, HourlyCandle>{
      for (final candle in existing ?? const <HourlyCandle>[])
        candle.openTime.millisecondsSinceEpoch: candle,
    };

    for (final candle in incoming) {
      merged[candle.openTime.millisecondsSinceEpoch] = candle;
    }

    final result = merged.values.toList()
      ..sort((a, b) => a.openTime.compareTo(b.openTime));

    if (result.length <= _historyLimit) {
      return result;
    }
    return result.sublist(result.length - _historyLimit);
  }

  void _toggleExpanded(String instId) {
    final willExpand = !_expandedInstIds.contains(instId);

    setState(() {
      if (!willExpand) {
        _expandedInstIds.remove(instId);
      } else {
        _expandedInstIds.add(instId);
      }
    });

    if (willExpand && !_hasCachedHistory(instId, _selectedInterval)) {
      unawaited(_syncHistory(instId: instId, interval: _selectedInterval));
    }
  }

  bool _hasCachedHistory(String instId, CandleInterval interval) {
    final candles = _historyCache[interval]![instId];
    final initialized = _historyInitialized[interval]!;
    return candles != null && candles.isNotEmpty && initialized.contains(instId);
  }

  List<GateRankedContract> _buildTopRankings(
    Map<String, GateContract> contractsById,
    Map<String, GateTicker24h> tickersByInstId,
  ) {
    final ranked = contractsById.values
        .map((contract) {
          final ticker = tickersByInstId[contract.name];
          final changePercent24h =
              ticker?.changePercent24h ?? double.negativeInfinity;
          return GateRankedContract(
            contract: contract,
            changePercent24h: changePercent24h,
            lastPrice: ticker?.lastPrice ?? 0,
          );
        })
        .where((item) => item.changePercent24h.isFinite)
        .toList()
      ..sort(
        (a, b) => b.changePercent24h.compareTo(a.changePercent24h),
      );

    if (ranked.length <= _rankingLimit) {
      return ranked;
    }
    return ranked.take(_rankingLimit).toList();
  }

  Set<String> _currentRankingIds() {
    return _rankings.map((item) => item.contract.name).toSet();
  }

  bool _setChanged(Set<String> left, Set<String> right) {
    if (left.length != right.length) {
      return true;
    }
    for (final item in left) {
      if (!right.contains(item)) {
        return true;
      }
    }
    return false;
  }

  void _evaluateMonitorSignals({
    required String instId,
    required CandleInterval interval,
    required List<HourlyCandle> candles,
  }) {
    final contract = _contractsById[instId];
    if (contract == null) {
      return;
    }

    final hits = _collectMonitorHits(
      instId: instId,
      interval: interval,
      candles: candles,
    );
    if (hits.isEmpty) {
      return;
    }

    final visibleHits = hits
        .where((hit) => !_dismissedMonitorSignatures.contains(hit.signature))
        .toList();
    if (visibleHits.isEmpty) {
      return;
    }

    final nextBadges = visibleHits
        .map(
          (hit) => _PinnedAlertBadge(
            signature: hit.signature,
            label: hit.label,
            targetPrices: hit.targetPrices,
            maxReachedMultiple: hit.maxReachedMultiple,
          ),
        )
        .toList();

    final nextEntry = _upsertPinnedAlert(
      contract: contract,
      badges: nextBadges,
    );

    final newHits = visibleHits.where((hit) => hit.isNew).toList();
    if (newHits.isNotEmpty) {
      final newBadges = newHits
          .map(
            (hit) => _PinnedAlertBadge(
              signature: hit.signature,
              label: hit.label,
              targetPrices: hit.targetPrices,
              maxReachedMultiple: hit.maxReachedMultiple,
            ),
          )
          .toList();
      unawaited(
        LocalNotificationService.instance.showMonitorAlert(
          title: '${contract.displayName} 监控命中',
          body:
              '命中标签: ${newBadges.map(_formatPinnedBadgeText).join(' / ')}，已加入顶部置顶。',
        ),
      );
    }

    if (newHits.isNotEmpty && !_isMonitorDialogVisible) {
      _isMonitorDialogVisible = true;
      unawaited(_showMonitorDialog(nextEntry, nextBadges));
    }
  }

  List<_MonitorHit> _collectMonitorHits({
    required String instId,
    required CandleInterval interval,
    required List<HourlyCandle> candles,
  }) {
    if (candles.length < 2) {
      return const <_MonitorHit>[];
    }

    final latest = candles[candles.length - 1];
    final previous = candles[candles.length - 2];
    final hits = <_MonitorHit>[];

    if (latest.isBullish &&
        previous.amplitudeRatio > 0 &&
        previous.changePercent > 1 &&
        latest.amplitudeRatio >= previous.amplitudeRatio * 7) {
      final signature = _monitorSignature(
        instId: instId,
        interval: interval,
        rule: 'c1',
        openTime: latest.openTime,
      );
      final isNew = _triggeredMonitorSignatures.add(signature);
      hits.add(
        _MonitorHit(
          signature: signature,
          label: '${_intervalLabel(interval)} · 条件1',
          targetPrices: _computeCondition1TargetPrices(
            previous: previous,
            latest: latest,
          ),
          maxReachedMultiple: latest.amplitudeRatio / previous.amplitudeRatio,
          isNew: isNew,
        ),
      );
    }

    return hits;
  }

  _PinnedAlertEntry _upsertPinnedAlert({
    required GateContract contract,
    required List<_PinnedAlertBadge> badges,
  }) {
    final existingIndex = _pinnedAlerts.indexWhere(
      (entry) => entry.instId == contract.name,
    );
    final existing = existingIndex >= 0 ? _pinnedAlerts[existingIndex] : null;
    final existingBadges = existing?.badges ?? const <_PinnedAlertBadge>[];
    final mergedBadges = <String, _PinnedAlertBadge>{
      for (final badge in existingBadges) badge.label: badge,
      for (final badge in badges)
        badge.label: _mergePinnedBadge(existing: existingBadges, next: badge),
    }.values.toList();

    final nextEntry = _PinnedAlertEntry(
      instId: contract.name,
      contract: contract,
      badges: mergedBadges,
      triggeredAt: DateTime.now(),
    );

    if (!mounted) {
      return nextEntry;
    }

    setState(() {
      final nextList = List<_PinnedAlertEntry>.from(_pinnedAlerts);
      nextList.removeWhere((entry) => entry.instId == contract.name);
      nextList.insert(0, nextEntry);
      _pinnedAlerts = nextList;
    });

    return nextEntry;
  }

  _PinnedAlertBadge _mergePinnedBadge({
    required List<_PinnedAlertBadge> existing,
    required _PinnedAlertBadge next,
  }) {
    _PinnedAlertBadge? current;
    for (final badge in existing) {
      if (badge.label == next.label) {
        current = badge;
        break;
      }
    }
    if (current == null) {
      return next;
    }
    return _PinnedAlertBadge(
      signature: next.signature,
      label: next.label,
      targetPrices: next.targetPrices,
      maxReachedMultiple: math.max(
        current.maxReachedMultiple,
        next.maxReachedMultiple,
      ),
    );
  }

  Future<void> _showMonitorDialog(
    _PinnedAlertEntry entry,
    List<_PinnedAlertBadge> badges,
  ) async {
    if (!mounted) {
      _isMonitorDialogVisible = false;
      return;
    }

    await showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (context) {
        return PopScope(
          canPop: false,
          child: AlertDialog(
            title: const Text('Gate 实时监控命中'),
            content: Text(
              '${entry.contract.displayName} 已进入置顶。\n命中标签: '
              '${badges.map(_formatPinnedBadgeText).join(' / ')}\n'
              '命中时间: ${_formatDateTime(entry.triggeredAt)}',
            ),
            actions: [
              TextButton(
                onPressed: () {
                  Navigator.of(context).pop();
                },
                child: const Text('取消'),
              ),
            ],
          ),
        );
      },
    );

    if (mounted) {
      setState(() {
        _isMonitorDialogVisible = false;
      });
    } else {
      _isMonitorDialogVisible = false;
    }
  }

  void _removePinnedAlert(String instId) {
    setState(() {
      final match = _pinnedAlerts.where((entry) => entry.instId == instId);
      for (final entry in match) {
        _dismissedMonitorSignatures.addAll(
          entry.badges.map((badge) => badge.signature),
        );
      }
      _pinnedAlerts = _pinnedAlerts
          .where((entry) => entry.instId != instId)
          .toList();
    });
  }

  Future<void> _toggleAlarmSound() async {
    await LocalNotificationService.instance.togglePersistentAlarm();
  }

  Future<void> _triggerTestAlert() async {
    final badges = <_PinnedAlertBadge>[
      _PinnedAlertBadge(
        signature: 'test|gate|${DateTime.now().millisecondsSinceEpoch}',
        label: '${_intervalLabel(_selectedInterval)} · 测试命中',
        targetPrices: const <_TargetPriceLevel>[],
        maxReachedMultiple: 10.0,
      ),
    ];
    final contract =
        _rankings.isNotEmpty
            ? _rankings.first.contract
            : const GateContract(
              name: 'TEST_USDT',
              orderSizeMin: 1,
              inDelisting: false,
              quantoMultiplier: 1,
              leverageMax: 1,
              status: 'trading',
            );

    final entry = _upsertPinnedAlert(contract: contract, badges: badges);
    await LocalNotificationService.instance.showMonitorAlert(
      title: '${contract.displayName} 测试命中',
      body: '这是测试提醒，用于验证弹框、通知和警报声。',
    );
    if (!mounted || _isMonitorDialogVisible) {
      return;
    }
    _isMonitorDialogVisible = true;
    await _showMonitorDialog(entry, badges);
  }

  String _monitorSignature({
    required String instId,
    required CandleInterval interval,
    required String rule,
    required DateTime openTime,
  }) {
    return '$instId|${interval.name}|$rule|${openTime.millisecondsSinceEpoch}';
  }

  String _intervalLabel(CandleInterval interval) {
    return interval == CandleInterval.h1 ? '1小时' : '4小时';
  }

  List<_TargetPriceLevel> _computeCondition1TargetPrices({
    required HourlyCandle previous,
    required HourlyCandle latest,
  }) {
    return _targetMultipliers
        .map((multiplier) {
          final targetAmplitudeRatio = previous.amplitudeRatio * multiplier;
          return _TargetPriceLevel(
            multiplier: multiplier,
            price: latest.low + latest.open * targetAmplitudeRatio,
          );
        })
        .toList();
  }

  String _historyLoadingKey(String instId, CandleInterval interval) {
    return '${interval.name}:$instId';
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Gate'),
        actions: [
          ValueListenableBuilder<bool>(
            valueListenable: LocalNotificationService.instance.alarmActive,
            builder: (context, isActive, _) => IconButton(
              onPressed: _toggleAlarmSound,
              tooltip: isActive ? '停止警报声' : '启动警报声',
              icon: Icon(
                isActive ? Icons.notifications_active : Icons.volume_up_outlined,
              ),
            ),
          ),
          IconButton(
            onPressed: _triggerTestAlert,
            tooltip: '测试命中',
            icon: const Icon(Icons.notification_add_outlined),
          ),
          IconButton(
            onPressed: _isRefreshing ? null : () => _bootstrapMarketData(),
            tooltip: '立即刷新',
            icon: _isRefreshing
                ? const SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.refresh),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _bootstrapMarketData,
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.all(16),
          children: [
            _IntervalSwitcher(
              selectedInterval: _selectedInterval,
              onSelected: _switchInterval,
            ),
            const SizedBox(height: 12),
            if (_pinnedAlerts.isNotEmpty) ...[
              _PinnedAlertsSection(
                alerts: _pinnedAlerts,
                onRemove: _removePinnedAlert,
              ),
              const SizedBox(height: 12),
            ],
            _StatusCard(
              rankingCount: _rankings.length,
              isRefreshing: _isRefreshing,
              selectedInterval: _selectedInterval,
              lastUpdatedAt: _lastUpdatedAt,
              errorMessage: _errorMessage,
            ),
            const SizedBox(height: 16),
            Text(
              '实时榜单',
              style: theme.textTheme.titleMedium?.copyWith(
                fontWeight: FontWeight.w700,
              ),
            ),
            const SizedBox(height: 12),
            if (_rankings.isEmpty && _isRefreshing)
              const Padding(
                padding: EdgeInsets.only(top: 60),
                child: Center(child: CircularProgressIndicator()),
              )
            else if (_rankings.isEmpty)
              const _EmptyState()
            else
              ...List.generate(_rankings.length, (index) {
                final item = _rankings[index];
                final instId = item.contract.name;
                final history =
                    _historyCache[_selectedInterval]![instId] ??
                    const <HourlyCandle>[];
                final isExpanded = _expandedInstIds.contains(instId);
                final isHistoryLoading = _loadingHistoryKeys.contains(
                  _historyLoadingKey(instId, _selectedInterval),
                );

                return Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: _RankingCard(
                    rank: index + 1,
                    item: item,
                    interval: _selectedInterval,
                    history: history,
                    isExpanded: isExpanded,
                    isHistoryLoading: isHistoryLoading,
                    onTap: () => _toggleExpanded(instId),
                  ),
                );
              }),
          ],
        ),
      ),
    );
  }
}

class _IntervalSwitcher extends StatelessWidget {
  const _IntervalSwitcher({
    required this.selectedInterval,
    required this.onSelected,
  });

  final CandleInterval selectedInterval;
  final ValueChanged<CandleInterval> onSelected;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: _IntervalButton(
            label: '1 小时',
            selected: selectedInterval == CandleInterval.h1,
            onTap: () => onSelected(CandleInterval.h1),
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: _IntervalButton(
            label: '4 小时',
            selected: selectedInterval == CandleInterval.h4,
            onTap: () => onSelected(CandleInterval.h4),
          ),
        ),
      ],
    );
  }
}

class _IntervalButton extends StatelessWidget {
  const _IntervalButton({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return FilledButton.tonal(
      style: FilledButton.styleFrom(
        padding: const EdgeInsets.symmetric(vertical: 16),
        backgroundColor: selected
            ? theme.colorScheme.primaryContainer
            : theme.colorScheme.surfaceContainerHighest,
      ),
      onPressed: onTap,
      child: Text(
        label,
        style: TextStyle(
          fontWeight: FontWeight.w700,
          color: selected
              ? theme.colorScheme.onPrimaryContainer
              : theme.colorScheme.onSurface,
        ),
      ),
    );
  }
}

class _StatusCard extends StatelessWidget {
  const _StatusCard({
    required this.rankingCount,
    required this.isRefreshing,
    required this.selectedInterval,
    required this.lastUpdatedAt,
    required this.errorMessage,
  });

  final int rankingCount;
  final bool isRefreshing;
  final CandleInterval selectedInterval;
  final DateTime? lastUpdatedAt;
  final String? errorMessage;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Wrap(
              spacing: 12,
              runSpacing: 12,
              children: [
                _InfoChip(
                  label: '当前周期',
                  value: selectedInterval == CandleInterval.h1 ? '1 小时' : '4 小时',
                ),
                _InfoChip(
                  label: '榜单数量',
                  value: '$rankingCount / 30',
                ),
                _InfoChip(
                  label: '刷新状态',
                  value: isRefreshing ? '同步中' : '已就绪',
                ),
                _InfoChip(
                  label: '最近更新',
                  value: lastUpdatedAt == null ? '--' : _formatDateTime(lastUpdatedAt!),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Text(
              'Gate 标签页仅展示行情和监控，不提供下单。',
              style: theme.textTheme.bodySmall,
            ),
            if (errorMessage != null) ...[
              const SizedBox(height: 12),
              Text(
                '刷新失败: $errorMessage',
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: theme.colorScheme.error,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _PinnedAlertsSection extends StatelessWidget {
  const _PinnedAlertsSection({
    required this.alerts,
    required this.onRemove,
  });

  final List<_PinnedAlertEntry> alerts;
  final ValueChanged<String> onRemove;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          '实时监控置顶',
          style: theme.textTheme.titleMedium?.copyWith(
            fontWeight: FontWeight.w700,
          ),
        ),
        const SizedBox(height: 12),
        ...alerts.map(
          (alert) => Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            alert.contract.displayName,
                            style: theme.textTheme.titleMedium?.copyWith(
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                          const SizedBox(height: 4),
                          Text(
                            alert.contract.name,
                            style: theme.textTheme.bodySmall,
                          ),
                          const SizedBox(height: 4),
                          Text(
                            '命中时间: ${_formatDateTime(alert.triggeredAt)}',
                            style: theme.textTheme.bodySmall,
                          ),
                          const SizedBox(height: 10),
                          Wrap(
                            spacing: 8,
                            runSpacing: 8,
                            children: alert.badges
                                .map(
                                  (badge) => Container(
                                    padding: const EdgeInsets.symmetric(
                                      horizontal: 10,
                                      vertical: 6,
                                    ),
                                    decoration: BoxDecoration(
                                      color: theme.colorScheme.primaryContainer,
                                      borderRadius: BorderRadius.circular(999),
                                    ),
                                    child: Text(
                                      _formatPinnedBadgeText(badge),
                                      style: theme.textTheme.labelMedium?.copyWith(
                                        fontWeight: FontWeight.w700,
                                        color: theme.colorScheme.onPrimaryContainer,
                                      ),
                                    ),
                                  ),
                                )
                                .toList(),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 12),
                    IconButton(
                      tooltip: '移除',
                      onPressed: () => onRemove(alert.instId),
                      icon: const Icon(Icons.close),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ],
    );
  }
}

class _InfoChip extends StatelessWidget {
  const _InfoChip({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(label, style: theme.textTheme.labelMedium),
          const SizedBox(height: 4),
          Text(
            value,
            style: theme.textTheme.bodyMedium?.copyWith(
              fontWeight: FontWeight.w700,
            ),
          ),
        ],
      ),
    );
  }
}

class _RankingCard extends StatelessWidget {
  const _RankingCard({
    required this.rank,
    required this.item,
    required this.interval,
    required this.history,
    required this.isExpanded,
    required this.isHistoryLoading,
    required this.onTap,
  });

  final int rank;
  final GateRankedContract item;
  final CandleInterval interval;
  final List<HourlyCandle> history;
  final bool isExpanded;
  final bool isHistoryLoading;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final latest = history.isEmpty ? null : history.last;

    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Container(
                    width: 36,
                    height: 36,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      color: theme.colorScheme.primaryContainer,
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Text(
                      '$rank',
                      style: theme.textTheme.titleSmall?.copyWith(
                        fontWeight: FontWeight.w800,
                        color: theme.colorScheme.onPrimaryContainer,
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          item.contract.displayName,
                          style: theme.textTheme.titleMedium?.copyWith(
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          item.contract.name,
                          style: theme.textTheme.bodySmall,
                        ),
                      ],
                    ),
                  ),
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      Text(
                        _formatSignedPercent(item.changePercent24h),
                        style: theme.textTheme.titleMedium?.copyWith(
                          color: item.changePercent24h >= 0
                              ? Colors.greenAccent.shade400
                              : Colors.redAccent.shade200,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        '24小时涨跌',
                        style: theme.textTheme.labelSmall,
                      ),
                      const SizedBox(height: 8),
                      Text(
                        _formatPrice(item.lastPrice),
                        style: theme.textTheme.bodyMedium?.copyWith(
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        '最新价格',
                        style: theme.textTheme.labelSmall,
                      ),
                    ],
                  ),
                  const SizedBox(width: 8),
                  Icon(
                    isExpanded ? Icons.expand_less : Icons.expand_more,
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ],
              ),
              const SizedBox(height: 12),
              Wrap(
                spacing: 12,
                runSpacing: 8,
                children: [
                  _MetaText(label: '当前展开', value: interval == CandleInterval.h1 ? '1 小时' : '4 小时'),
                  _MetaText(label: '已缓存', value: '${history.length} 根'),
                  _MetaText(label: '当前价格', value: _formatPrice(item.lastPrice)),
                  _MetaText(
                    label: '最新收线',
                    value: latest == null ? '--' : _formatDateTime(latest.openTime),
                  ),
                  _MetaText(
                    label: '最新涨幅',
                    value: latest == null ? '--' : _formatSignedPercent(latest.changePercent),
                  ),
                ],
              ),
              AnimatedCrossFade(
                crossFadeState: isExpanded
                    ? CrossFadeState.showSecond
                    : CrossFadeState.showFirst,
                duration: const Duration(milliseconds: 180),
                firstChild: const SizedBox.shrink(),
                secondChild: Padding(
                  padding: const EdgeInsets.only(top: 16),
                  child: _ExpandedHistory(
                    interval: interval,
                    history: history,
                    isHistoryLoading: isHistoryLoading,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _MetaText extends StatelessWidget {
  const _MetaText({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return RichText(
      text: TextSpan(
        style: theme.textTheme.bodySmall,
        children: [
          TextSpan(
            text: '$label: ',
            style: const TextStyle(fontWeight: FontWeight.w600),
          ),
          TextSpan(text: value),
        ],
      ),
    );
  }
}

class _ExpandedHistory extends StatelessWidget {
  const _ExpandedHistory({
    required this.interval,
    required this.history,
    required this.isHistoryLoading,
  });

  final CandleInterval interval;
  final List<HourlyCandle> history;
  final bool isHistoryLoading;

  @override
  Widget build(BuildContext context) {
    final visibleHistory = history.reversed.take(20).toList();
    final theme = Theme.of(context);
    final orderedHistory = visibleHistory.reversed.toList();

    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerLow,
        borderRadius: BorderRadius.circular(14),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(
                '${interval == CandleInterval.h1 ? '1 小时' : '4 小时'}历史数据',
                style: theme.textTheme.titleSmall?.copyWith(
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(width: 8),
              if (isHistoryLoading)
                const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            '展示最近 ${visibleHistory.length} 根，缓存保留在内存里，后续刷新只会把新K线叠加进来。',
            style: theme.textTheme.bodySmall,
          ),
          const SizedBox(height: 12),
          if (orderedHistory.isEmpty)
            const Text('暂无历史数据')
          else
            Column(
              children: [
                SizedBox(
                  height: 220,
                  width: double.infinity,
                  child: _HistoryChart(
                    candles: orderedHistory,
                    interval: interval,
                  ),
                ),
                const SizedBox(height: 12),
                _ChartLegend(candles: orderedHistory),
              ],
            ),
        ],
      ),
    );
  }
}

class _HistoryChart extends StatelessWidget {
  const _HistoryChart({required this.candles, required this.interval});

  final List<HourlyCandle> candles;
  final CandleInterval interval;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Container(
      padding: const EdgeInsets.fromLTRB(8, 12, 8, 8),
      decoration: BoxDecoration(
        color: theme.colorScheme.surface,
        borderRadius: BorderRadius.circular(16),
      ),
      child: CustomPaint(
        painter: _CandleChartPainter(
          candles: candles,
          gridColor: theme.colorScheme.outlineVariant,
          axisColor: theme.colorScheme.onSurfaceVariant,
        ),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(0, 0, 0, 8),
          child: Align(
            alignment: Alignment.bottomCenter,
            child: Text(
              '时间从左到右，当前周期 ${interval == CandleInterval.h1 ? '1 小时' : '4 小时'}',
              style: theme.textTheme.labelSmall,
            ),
          ),
        ),
      ),
    );
  }
}

class _ChartLegend extends StatelessWidget {
  const _ChartLegend({required this.candles});

  final List<HourlyCandle> candles;

  @override
  Widget build(BuildContext context) {
    final latest = candles.last;
    final highest = candles.map((candle) => candle.high).reduce(math.max);
    final lowest = candles.map((candle) => candle.low).reduce(math.min);

    return Wrap(
      spacing: 12,
      runSpacing: 8,
      children: [
        _MetaText(label: '最新时间', value: _formatDateTime(latest.openTime)),
        _MetaText(label: '开', value: latest.open.toStringAsFixed(4)),
        _MetaText(label: '高', value: highest.toStringAsFixed(4)),
        _MetaText(label: '低', value: lowest.toStringAsFixed(4)),
        _MetaText(label: '收', value: latest.close.toStringAsFixed(4)),
        _MetaText(label: '最新涨幅', value: _formatSignedPercent(latest.changePercent)),
      ],
    );
  }
}

class _CandleChartPainter extends CustomPainter {
  const _CandleChartPainter({
    required this.candles,
    required this.gridColor,
    required this.axisColor,
  });

  final List<HourlyCandle> candles;
  final Color gridColor;
  final Color axisColor;

  @override
  void paint(Canvas canvas, Size size) {
    if (candles.isEmpty) {
      return;
    }

    const leftPad = 4.0;
    const rightPad = 4.0;
    const topPad = 8.0;
    const bottomPad = 26.0;
    final chartHeight = size.height - topPad - bottomPad;
    final chartWidth = size.width - leftPad - rightPad;
    if (chartHeight <= 0 || chartWidth <= 0) {
      return;
    }

    final highest = candles.map((c) => c.high).reduce(math.max);
    final lowest = candles.map((c) => c.low).reduce(math.min);
    final priceSpan = math.max(highest - lowest, highest.abs() * 0.001);

    double yForPrice(double price) {
      final normalized = (price - lowest) / priceSpan;
      return topPad + chartHeight - (normalized * chartHeight);
    }

    final gridPaint = Paint()
      ..color = gridColor.withValues(alpha: 0.28)
      ..strokeWidth = 1;

    for (var i = 0; i < 4; i++) {
      final y = topPad + chartHeight * (i / 3);
      canvas.drawLine(
        Offset(leftPad, y),
        Offset(size.width - rightPad, y),
        gridPaint,
      );
    }

    final candleSlot = chartWidth / candles.length;
    final bodyWidth = math.max(4.0, candleSlot * 0.55);
    final wickPaint = Paint()..strokeWidth = 1.2;

    for (var i = 0; i < candles.length; i++) {
      final candle = candles[i];
      final centerX = leftPad + candleSlot * i + candleSlot / 2;
      final openY = yForPrice(candle.open);
      final closeY = yForPrice(candle.close);
      final highY = yForPrice(candle.high);
      final lowY = yForPrice(candle.low);
      final isBullish = candle.close >= candle.open;
      final color = isBullish
          ? const Color(0xFF23C55E)
          : const Color(0xFFF43F5E);

      wickPaint.color = color;
      canvas.drawLine(
        Offset(centerX, highY),
        Offset(centerX, lowY),
        wickPaint,
      );

      final rectTop = math.min(openY, closeY);
      final rectBottom = math.max(openY, closeY);
      final bodyRect = RRect.fromRectAndRadius(
        Rect.fromLTRB(
          centerX - bodyWidth / 2,
          rectTop,
          centerX + bodyWidth / 2,
          math.max(rectBottom, rectTop + 1.5),
        ),
        const Radius.circular(2),
      );

      final bodyPaint = Paint()
        ..color = color
        ..style = isBullish ? PaintingStyle.fill : PaintingStyle.stroke
        ..strokeWidth = 1.4;
      canvas.drawRRect(bodyRect, bodyPaint);
    }

    final textPainter = TextPainter(
      textDirection: TextDirection.ltr,
      maxLines: 1,
    );

    void drawPriceLabel(String text, double y) {
      textPainter.text = TextSpan(
        text: text,
        style: TextStyle(
          color: axisColor.withValues(alpha: 0.88),
          fontSize: 10,
          fontWeight: FontWeight.w500,
        ),
      );
      textPainter.layout();
      textPainter.paint(
        canvas,
        Offset(size.width - rightPad - textPainter.width, y - 8),
      );
    }

    drawPriceLabel(highest.toStringAsFixed(4), topPad);
    drawPriceLabel(((highest + lowest) / 2).toStringAsFixed(4), topPad + chartHeight / 2);
    drawPriceLabel(lowest.toStringAsFixed(4), topPad + chartHeight);

    final firstLabel = _formatShortTime(candles.first.openTime);
    final lastLabel = _formatShortTime(candles.last.openTime);

    textPainter.text = TextSpan(
      text: firstLabel,
      style: TextStyle(
        color: axisColor.withValues(alpha: 0.88),
        fontSize: 10,
      ),
    );
    textPainter.layout();
    textPainter.paint(canvas, Offset(leftPad, size.height - 16));

    textPainter.text = TextSpan(
      text: lastLabel,
      style: TextStyle(
        color: axisColor.withValues(alpha: 0.88),
        fontSize: 10,
      ),
    );
    textPainter.layout();
    textPainter.paint(
      canvas,
      Offset(size.width - rightPad - textPainter.width, size.height - 16),
    );
  }

  @override
  bool shouldRepaint(covariant _CandleChartPainter oldDelegate) {
    return oldDelegate.candles != candles ||
        oldDelegate.gridColor != gridColor ||
        oldDelegate.axisColor != axisColor;
  }
}

class _MonitorHit {
  const _MonitorHit({
    required this.signature,
    required this.label,
    required this.targetPrices,
    required this.maxReachedMultiple,
    required this.isNew,
  });

  final String signature;
  final String label;
  final List<_TargetPriceLevel> targetPrices;
  final double maxReachedMultiple;
  final bool isNew;
}

class _PinnedAlertBadge {
  const _PinnedAlertBadge({
    required this.signature,
    required this.label,
    required this.targetPrices,
    required this.maxReachedMultiple,
  });

  final String signature;
  final String label;
  final List<_TargetPriceLevel> targetPrices;
  final double maxReachedMultiple;
}

class _TargetPriceLevel {
  const _TargetPriceLevel({
    required this.multiplier,
    required this.price,
  });

  final int multiplier;
  final double price;
}

class _PinnedAlertEntry {
  const _PinnedAlertEntry({
    required this.instId,
    required this.contract,
    required this.badges,
    required this.triggeredAt,
  });

  final String instId;
  final GateContract contract;
  final List<_PinnedAlertBadge> badges;
  final DateTime triggeredAt;
}

class _EmptyState extends StatelessWidget {
  const _EmptyState();

  @override
  Widget build(BuildContext context) {
    return const Card(
      child: Padding(
        padding: EdgeInsets.all(24),
        child: Center(
          child: Text('暂时还没有榜单数据，下拉或点右上角刷新试试。'),
        ),
      ),
    );
  }
}

String _formatPinnedBadgeText(_PinnedAlertBadge badge) {
  final targets = badge.targetPrices
      .map(
        (level) => '${level.multiplier}倍 ${level.price.toStringAsFixed(4)}',
      )
      .join(' · ');
  return '${badge.label} · $targets · 最高到 ${badge.maxReachedMultiple.toStringAsFixed(2)} 倍截止';
}

String _formatDateTime(DateTime dateTime) {
  final local = dateTime.toLocal();

  String twoDigits(int value) => value.toString().padLeft(2, '0');

  return '${local.month}-${twoDigits(local.day)} '
      '${twoDigits(local.hour)}:${twoDigits(local.minute)}:${twoDigits(local.second)}';
}

String _formatSignedPercent(double percent) {
  final sign = percent > 0 ? '+' : '';
  return '$sign${percent.toStringAsFixed(4)}%';
}

String _formatPrice(double price) {
  if (price == 0) {
    return '--';
  }
  if (price >= 1000) {
    return price.toStringAsFixed(2);
  }
  if (price >= 1) {
    return price.toStringAsFixed(4);
  }
  return price.toStringAsFixed(6);
}

String _formatShortTime(DateTime dateTime) {
  final local = dateTime.toLocal();
  String twoDigits(int value) => value.toString().padLeft(2, '0');
  return '${local.month}/${twoDigits(local.day)} ${twoDigits(local.hour)}:${twoDigits(local.minute)}';
}
