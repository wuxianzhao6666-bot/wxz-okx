class GateContract {
  const GateContract({
    required this.name,
    required this.orderSizeMin,
    required this.quantoMultiplier,
    required this.leverageMax,
    required this.status,
    required this.inDelisting,
  });

  factory GateContract.fromJson(Map<String, dynamic> json) {
    return GateContract(
      name: json['name']?.toString() ?? '',
      orderSizeMin: double.tryParse(json['order_size_min']?.toString() ?? '') ?? 0,
      quantoMultiplier:
          double.tryParse(json['quanto_multiplier']?.toString() ?? '') ?? 0,
      leverageMax: double.tryParse(json['leverage_max']?.toString() ?? '') ?? 0,
      status: json['status']?.toString() ?? '',
      inDelisting: json['in_delisting'] == true,
    );
  }

  final String name;
  final double orderSizeMin;
  final double quantoMultiplier;
  final double leverageMax;
  final String status;
  final bool inDelisting;

  bool get isTrading => status.toLowerCase() == 'trading' && !inDelisting;

  String get displayName => name.replaceAll('_USDT', '');
}
