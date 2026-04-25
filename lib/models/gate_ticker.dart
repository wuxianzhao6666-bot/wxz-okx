class GateTicker24h {
  const GateTicker24h({
    required this.contract,
    required this.lastPrice,
    required this.changePercent24h,
  });

  factory GateTicker24h.fromJson(Map<String, dynamic> json) {
    return GateTicker24h(
      contract: json['contract']?.toString() ?? '',
      lastPrice: double.tryParse(json['last']?.toString() ?? '') ?? 0,
      changePercent24h:
          double.tryParse(json['change_percentage']?.toString() ?? '') ??
          double.negativeInfinity,
    );
  }

  final String contract;
  final double lastPrice;
  final double changePercent24h;
}
