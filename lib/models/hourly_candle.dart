class HourlyCandle {
  const HourlyCandle({
    required this.openTime,
    required this.open,
    required this.high,
    required this.low,
    required this.close,
    required this.volume,
    required this.isConfirmed,
  });

  factory HourlyCandle.fromOkxRow(List<dynamic> row) {
    if (row.length < 9) {
      throw const FormatException('Unexpected OKX candle payload');
    }

    return HourlyCandle(
      openTime: DateTime.fromMillisecondsSinceEpoch(
        int.parse(row[0].toString()),
        isUtc: true,
      ),
      open: double.parse(row[1].toString()),
      high: double.parse(row[2].toString()),
      low: double.parse(row[3].toString()),
      close: double.parse(row[4].toString()),
      volume: double.parse(row[5].toString()),
      isConfirmed: row[8].toString() == '1',
    );
  }

  factory HourlyCandle.fromGateJson(
    Map<String, dynamic> json, {
    required Duration interval,
  }) {
    final openTime = DateTime.fromMillisecondsSinceEpoch(
      (int.parse(json['t'].toString())) * 1000,
      isUtc: true,
    );

    return HourlyCandle(
      openTime: openTime,
      open: double.parse(json['o'].toString()),
      high: double.parse(json['h'].toString()),
      low: double.parse(json['l'].toString()),
      close: double.parse(json['c'].toString()),
      volume: double.tryParse(json['v'].toString()) ?? 0,
      isConfirmed: DateTime.now().toUtc().isAfter(openTime.add(interval)),
    );
  }

  final DateTime openTime;
  final double open;
  final double high;
  final double low;
  final double close;
  final double volume;
  final bool isConfirmed;

  bool get isBullish => close > open;

  double get changeRatio {
    if (open == 0) {
      return 0;
    }
    return (close - open) / open;
  }

  double get changePercent => changeRatio * 100;

  /// 振幅：(最高 - 最低) / 开盘价，与涨幅同一量级（比例）
  double get amplitudeRatio {
    if (open == 0) {
      return 0;
    }
    return (high - low) / open;
  }

  double get amplitudePercent => amplitudeRatio * 100;
}
