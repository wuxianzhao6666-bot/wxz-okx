class OkxTicker24h {
  const OkxTicker24h({
    required this.instId,
    required this.lastPrice,
    required this.open24hPrice,
    required this.sodUtc8Price,
    required this.changePercent24h,
    required this.todayChangePercent,
  });

  factory OkxTicker24h.fromJson(Map<String, dynamic> json) {
    final instId = json['instId'] as String? ?? '';
    final last = double.tryParse(json['last']?.toString() ?? '') ?? 0;
    final open24h = double.tryParse(json['open24h']?.toString() ?? '') ?? 0;
    final sodUtc8 = double.tryParse(json['sodUtc8']?.toString() ?? '') ?? 0;
    double pct;
    if (open24h == 0) {
      pct = double.negativeInfinity;
    } else {
      pct = (last - open24h) / open24h * 100;
    }
    double todayPct;
    if (sodUtc8 == 0) {
      todayPct = double.negativeInfinity;
    } else {
      todayPct = (last - sodUtc8) / sodUtc8 * 100;
    }
    return OkxTicker24h(
      instId: instId,
      lastPrice: last,
      open24hPrice: open24h,
      sodUtc8Price: sodUtc8,
      changePercent24h: pct,
      todayChangePercent: todayPct,
    );
  }

  final String instId;
  final double lastPrice;
  final double open24hPrice;
  final double sodUtc8Price;
  final double changePercent24h;
  final double todayChangePercent;
}
