import 'dart:async';

import 'package:flutter/material.dart';

import '../models/okx_endpoint_config.dart';
import '../models/okx_pending_order.dart';
import '../models/okx_position.dart';
import '../services/okx_api_service.dart';

class TradingBoardPage extends StatefulWidget {
  const TradingBoardPage({
    super.key,
    required this.endpoint,
    required this.onEndpointChanged,
  });

  final OkxEndpointConfig endpoint;
  final ValueChanged<OkxEndpointConfig> onEndpointChanged;

  @override
  State<TradingBoardPage> createState() => _TradingBoardPageState();
}

class _TradingBoardPageState extends State<TradingBoardPage> {
  late final OkxApiService _apiService;

  List<OkxPendingOrder> _pendingOrders = const <OkxPendingOrder>[];
  List<OkxPosition> _positions = const <OkxPosition>[];
  bool _isLoading = false;
  String? _errorMessage;
  Timer? _refreshTimer;

  @override
  void initState() {
    super.initState();
    _apiService = OkxApiService(endpoint: widget.endpoint);
    unawaited(_refreshData(initialLoad: true));
    _refreshTimer = Timer.periodic(const Duration(seconds: 6), (_) {
      unawaited(_refreshData());
    });
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    _apiService.dispose();
    super.dispose();
  }

  Future<void> _refreshData({bool initialLoad = false}) async {
    if (_isLoading) {
      return;
    }
    if (!_apiService.hasTradingCredentials) {
      if (!mounted) {
        return;
      }
      setState(() {
        _pendingOrders = const <OkxPendingOrder>[];
        _positions = const <OkxPosition>[];
        _errorMessage = '未配置 OKX API 凭证，当前无法加载委托和仓位';
      });
      return;
    }

    setState(() {
      _isLoading = true;
      if (initialLoad) {
        _errorMessage = null;
      }
    });

    try {
      final results = await Future.wait([
        _apiService.fetchPendingOrders(),
        _apiService.fetchPositions(),
      ]);
      if (!mounted) {
        return;
      }
      setState(() {
        _pendingOrders = results[0] as List<OkxPendingOrder>;
        _positions = results[1] as List<OkxPosition>;
        _errorMessage = null;
      });
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
          _isLoading = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('挂单页'),
          actions: [
            PopupMenuButton<OkxEndpointConfig>(
              tooltip: '切换域名',
              initialValue: widget.endpoint,
              onSelected: widget.onEndpointChanged,
              itemBuilder: (context) => OkxEndpointConfig.values
                  .map(
                    (endpoint) => PopupMenuItem<OkxEndpointConfig>(
                      value: endpoint,
                      child: Text(endpoint.label),
                    ),
                  )
                  .toList(),
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12),
                child: Center(
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.public, size: 18),
                      const SizedBox(width: 6),
                      Text(widget.endpoint.label),
                    ],
                  ),
                ),
              ),
            ),
            IconButton(
              onPressed: _isLoading ? null : () => _refreshData(),
              tooltip: '刷新',
              icon: _isLoading
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.refresh),
            ),
          ],
          bottom: const TabBar(
            tabs: [
              Tab(text: '当前委托列表'),
              Tab(text: '当前仓位列表'),
            ],
          ),
        ),
        body: TabBarView(
          children: [
            _OrdersTab(
              orders: _pendingOrders,
              isLoading: _isLoading,
              errorMessage: _errorMessage,
              onRefresh: _refreshData,
            ),
            _PositionsTab(
              positions: _positions,
              isLoading: _isLoading,
              errorMessage: _errorMessage,
              onRefresh: _refreshData,
            ),
          ],
        ),
      ),
    );
  }
}

class _OrdersTab extends StatelessWidget {
  const _OrdersTab({
    required this.orders,
    required this.isLoading,
    required this.errorMessage,
    required this.onRefresh,
  });

  final List<OkxPendingOrder> orders;
  final bool isLoading;
  final String? errorMessage;
  final Future<void> Function() onRefresh;

  @override
  Widget build(BuildContext context) {
    if (isLoading && orders.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }

    return RefreshIndicator(
      onRefresh: onRefresh,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.all(16),
        children: [
          if (errorMessage != null) _ErrorBanner(message: errorMessage!),
          if (orders.isEmpty)
            const _EmptyCard(message: '当前没有委托单')
          else
            ...orders.map(
              (order) => Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Card(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Expanded(
                              child: Text(
                                order.instId,
                                style: Theme.of(context).textTheme.titleMedium
                                    ?.copyWith(fontWeight: FontWeight.w700),
                              ),
                            ),
                            _TagChip(
                              label: '${order.side.toUpperCase()} / ${order.posSide}',
                            ),
                          ],
                        ),
                        const SizedBox(height: 10),
                        Wrap(
                          spacing: 12,
                          runSpacing: 8,
                          children: [
                            _MetaText(label: '状态', value: order.state),
                            _MetaText(label: '模式', value: order.tdMode),
                            _MetaText(label: '类型', value: order.ordType),
                            _MetaText(
                              label: '价格',
                              value: _formatNumber(order.price),
                            ),
                            _MetaText(
                              label: '数量',
                              value: _formatNumber(order.size),
                            ),
                            _MetaText(
                              label: '剩余',
                              value: _formatNumber(order.remainingSize),
                            ),
                            _MetaText(
                              label: '创建时间',
                              value: _formatDateTime(order.createdAt),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _PositionsTab extends StatelessWidget {
  const _PositionsTab({
    required this.positions,
    required this.isLoading,
    required this.errorMessage,
    required this.onRefresh,
  });

  final List<OkxPosition> positions;
  final bool isLoading;
  final String? errorMessage;
  final Future<void> Function() onRefresh;

  @override
  Widget build(BuildContext context) {
    if (isLoading && positions.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }

    return RefreshIndicator(
      onRefresh: onRefresh,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.all(16),
        children: [
          if (errorMessage != null) _ErrorBanner(message: errorMessage!),
          if (positions.isEmpty)
            const _EmptyCard(message: '当前没有持仓')
          else
            ...positions.map(
              (position) => Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Card(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Expanded(
                              child: Text(
                                position.instId,
                                style: Theme.of(context).textTheme.titleMedium
                                    ?.copyWith(fontWeight: FontWeight.w700),
                              ),
                            ),
                            _TagChip(
                              label:
                                  '${position.posSide.toUpperCase()} / ${position.mgnMode}',
                            ),
                          ],
                        ),
                        const SizedBox(height: 10),
                        Wrap(
                          spacing: 12,
                          runSpacing: 8,
                          children: [
                            _MetaText(
                              label: '杠杆',
                              value: '${_formatNumber(position.leverage)}x',
                            ),
                            _MetaText(
                              label: '持仓量',
                              value: _formatNumber(position.positionSize),
                            ),
                            _MetaText(
                              label: '开仓均价',
                              value: _formatNumber(position.avgPrice),
                            ),
                            _MetaText(
                              label: '标记价格',
                              value: _formatNumber(position.markPrice),
                            ),
                            _MetaText(
                              label: '未实现盈亏',
                              value: _formatNumber(position.unrealizedPnl),
                            ),
                            _MetaText(
                              label: '收益率',
                              value:
                                  '${(position.unrealizedPnlRatio * 100).toStringAsFixed(2)}%',
                            ),
                            _MetaText(
                              label: '预估强平',
                              value: _formatNumber(position.liquidationPrice),
                            ),
                            _MetaText(
                              label: '更新时间',
                              value: _formatDateTime(position.updatedAt),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _ErrorBanner extends StatelessWidget {
  const _ErrorBanner({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: theme.colorScheme.errorContainer,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Text(
          message,
          style: theme.textTheme.bodyMedium?.copyWith(
            color: theme.colorScheme.onErrorContainer,
          ),
        ),
      ),
    );
  }
}

class _EmptyCard extends StatelessWidget {
  const _EmptyCard({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Center(child: Text(message)),
      ),
    );
  }
}

class _TagChip extends StatelessWidget {
  const _TagChip({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: theme.colorScheme.primaryContainer,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        label,
        style: theme.textTheme.labelMedium?.copyWith(
          color: theme.colorScheme.onPrimaryContainer,
          fontWeight: FontWeight.w700,
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
    return RichText(
      text: TextSpan(
        style: Theme.of(context).textTheme.bodyMedium,
        children: [
          TextSpan(
            text: '$label: ',
            style: Theme.of(context).textTheme.bodyMedium?.copyWith(
              fontWeight: FontWeight.w700,
            ),
          ),
          TextSpan(text: value),
        ],
      ),
    );
  }
}

String _formatNumber(double value) {
  if (value == 0) {
    return '0';
  }
  if (value.abs() >= 1000) {
    return value.toStringAsFixed(2);
  }
  if (value.abs() >= 1) {
    return value.toStringAsFixed(4);
  }
  return value.toStringAsFixed(6);
}

String _formatDateTime(DateTime? time) {
  if (time == null) {
    return '--';
  }
  final month = time.month.toString().padLeft(2, '0');
  final day = time.day.toString().padLeft(2, '0');
  final hour = time.hour.toString().padLeft(2, '0');
  final minute = time.minute.toString().padLeft(2, '0');
  final second = time.second.toString().padLeft(2, '0');
  return '$month-$day $hour:$minute:$second';
}
