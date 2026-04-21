class OkxPendingOrder {
  const OkxPendingOrder({
    required this.ordId,
    required this.instId,
    required this.side,
    required this.posSide,
    required this.tdMode,
    required this.ordType,
    required this.state,
    required this.price,
    required this.size,
    required this.filledSize,
    required this.createdAt,
  });

  factory OkxPendingOrder.fromJson(Map<String, dynamic> json) {
    return OkxPendingOrder(
      ordId: json['ordId']?.toString() ?? '',
      instId: json['instId']?.toString() ?? '',
      side: json['side']?.toString() ?? '',
      posSide: json['posSide']?.toString() ?? '',
      tdMode: json['tdMode']?.toString() ?? '',
      ordType: json['ordType']?.toString() ?? '',
      state: json['state']?.toString() ?? '',
      price: double.tryParse(json['px']?.toString() ?? '') ?? 0,
      size: double.tryParse(json['sz']?.toString() ?? '') ?? 0,
      filledSize: double.tryParse(json['accFillSz']?.toString() ?? '') ?? 0,
      createdAt: _parseMillis(json['cTime']),
    );
  }

  final String ordId;
  final String instId;
  final String side;
  final String posSide;
  final String tdMode;
  final String ordType;
  final String state;
  final double price;
  final double size;
  final double filledSize;
  final DateTime? createdAt;

  double get remainingSize {
    final remaining = size - filledSize;
    return remaining > 0 ? remaining : 0;
  }

  static DateTime? _parseMillis(Object? value) {
    final raw = int.tryParse(value?.toString() ?? '');
    if (raw == null || raw <= 0) {
      return null;
    }
    return DateTime.fromMillisecondsSinceEpoch(raw, isUtc: true).toLocal();
  }
}
