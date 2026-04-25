class OkxPosition {
  const OkxPosition({
    required this.instId,
    required this.posSide,
    required this.mgnMode,
    required this.leverage,
    required this.rawPositionSize,
    required this.positionSize,
    required this.avgPrice,
    required this.markPrice,
    required this.unrealizedPnl,
    required this.unrealizedPnlRatio,
    required this.liquidationPrice,
    required this.updatedAt,
  });

  factory OkxPosition.fromJson(Map<String, dynamic> json) {
    return OkxPosition(
      instId: json['instId']?.toString() ?? '',
      posSide: json['posSide']?.toString() ?? '',
      mgnMode: json['mgnMode']?.toString() ?? '',
      leverage: double.tryParse(json['lever']?.toString() ?? '') ?? 0,
      rawPositionSize: json['pos']?.toString() ?? '',
      positionSize: double.tryParse(json['pos']?.toString() ?? '') ?? 0,
      avgPrice: double.tryParse(json['avgPx']?.toString() ?? '') ?? 0,
      markPrice: double.tryParse(json['markPx']?.toString() ?? '') ?? 0,
      unrealizedPnl: double.tryParse(json['upl']?.toString() ?? '') ?? 0,
      unrealizedPnlRatio: double.tryParse(json['uplRatio']?.toString() ?? '') ?? 0,
      liquidationPrice: double.tryParse(json['liqPx']?.toString() ?? '') ?? 0,
      updatedAt: _parseMillis(json['uTime'] ?? json['cTime']),
    );
  }

  final String instId;
  final String posSide;
  final String mgnMode;
  final double leverage;
  final String rawPositionSize;
  final double positionSize;
  final double avgPrice;
  final double markPrice;
  final double unrealizedPnl;
  final double unrealizedPnlRatio;
  final double liquidationPrice;
  final DateTime? updatedAt;

  static DateTime? _parseMillis(Object? value) {
    final raw = int.tryParse(value?.toString() ?? '');
    if (raw == null || raw <= 0) {
      return null;
    }
    return DateTime.fromMillisecondsSinceEpoch(raw, isUtc: true).toLocal();
  }
}
