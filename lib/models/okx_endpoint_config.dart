class OkxEndpointConfig {
  const OkxEndpointConfig({
    required this.id,
    required this.label,
    required this.restHost,
    required this.publicWsHost,
    required this.businessWsHost,
  });

  final String id;
  final String label;
  final String restHost;
  final String publicWsHost;
  final String businessWsHost;

  static const global = OkxEndpointConfig(
    id: 'global',
    label: 'www.okx.com',
    restHost: 'www.okx.com',
    publicWsHost: 'ws.okx.com',
    businessWsHost: 'ws.okx.com',
  );

  static const us = OkxEndpointConfig(
    id: 'us',
    label: 'us.okx.com',
    restHost: 'us.okx.com',
    publicWsHost: 'wsus.okx.com',
    businessWsHost: 'wsus.okx.com',
  );

  static const eea = OkxEndpointConfig(
    id: 'eea',
    label: 'eea.okx.com',
    restHost: 'eea.okx.com',
    publicWsHost: 'wseea.okx.com',
    businessWsHost: 'wseea.okx.com',
  );

  static const values = <OkxEndpointConfig>[global, us, eea];
}
