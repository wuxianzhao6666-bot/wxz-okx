enum ScanStrategy {
  breakout('突破 + 前高'),
  amplitudeChain('连阳振幅');

  const ScanStrategy(this.label);

  final String label;
}
