class OkxInstrument {
  const OkxInstrument({
    required this.instId,
    required this.baseCcy,
    required this.quoteCcy,
    required this.settleCcy,
    required this.state,
    required this.tickSz,
    required this.lotSz,
    required this.minSz,
    required this.ctVal,
    required this.ctValCcy,
  });

  factory OkxInstrument.fromJson(Map<String, dynamic> json) {
    return OkxInstrument(
      instId: json['instId'] as String? ?? '',
      baseCcy: json['baseCcy'] as String? ?? '',
      quoteCcy: json['quoteCcy'] as String? ?? '',
      settleCcy: json['settleCcy'] as String? ?? '',
      state: json['state'] as String? ?? '',
      tickSz: double.tryParse(json['tickSz']?.toString() ?? '') ?? 0,
      lotSz: double.tryParse(json['lotSz']?.toString() ?? '') ?? 0,
      minSz: double.tryParse(json['minSz']?.toString() ?? '') ?? 0,
      ctVal: double.tryParse(json['ctVal']?.toString() ?? '') ?? 0,
      ctValCcy: json['ctValCcy'] as String? ?? '',
    );
  }

  final String instId;
  final String baseCcy;
  final String quoteCcy;
  final String settleCcy;
  final String state;
  final double tickSz;
  final double lotSz;
  final double minSz;
  final double ctVal;
  final String ctValCcy;

  bool get isLive => state.toLowerCase() == 'live';

  String get displayName => instId.replaceAll('-SWAP', '');

  Uri get detailUri =>
      Uri.parse('https://www.okx.com/trade-swap/${instId.toLowerCase()}');
}
