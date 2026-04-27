import 'package:flutter/material.dart';

import '../models/okx_endpoint_config.dart';
import 'contract_scanner_page.dart';
import 'gate_scanner_page.dart';
import 'trading_board_page.dart';

class AppShellPage extends StatefulWidget {
  const AppShellPage({super.key});

  @override
  State<AppShellPage> createState() => _AppShellPageState();
}

class _AppShellPageState extends State<AppShellPage> {
  int _selectedIndex = 0;
  OkxEndpointConfig _selectedEndpoint = OkxEndpointConfig.us;

  @override
  Widget build(BuildContext context) {
    final pages = <Widget>[
      ContractScannerPage(
        key: ValueKey('home-${_selectedEndpoint.id}'),
        endpoint: _selectedEndpoint,
        onEndpointChanged: _handleEndpointChanged,
      ),
      const GateScannerPage(),
      TradingBoardPage(
        key: ValueKey('trade-${_selectedEndpoint.id}'),
        endpoint: _selectedEndpoint,
        onEndpointChanged: _handleEndpointChanged,
      ),
    ];

    return Scaffold(
      body: IndexedStack(
        index: _selectedIndex,
        children: pages,
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _selectedIndex,
        onDestinationSelected: (index) {
          setState(() {
            _selectedIndex = index;
          });
        },
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.home_outlined),
            selectedIcon: Icon(Icons.home),
            label: 'OKX',
          ),
          NavigationDestination(
            icon: Icon(Icons.candlestick_chart_outlined),
            selectedIcon: Icon(Icons.candlestick_chart),
            label: 'Gate',
          ),
          NavigationDestination(
            icon: Icon(Icons.list_alt_outlined),
            selectedIcon: Icon(Icons.list_alt),
            label: '挂单页',
          ),
        ],
      ),
    );
  }

  void _handleEndpointChanged(OkxEndpointConfig endpoint) {
    if (_selectedEndpoint.id == endpoint.id) {
      return;
    }
    setState(() {
      _selectedEndpoint = endpoint;
    });
  }
}
