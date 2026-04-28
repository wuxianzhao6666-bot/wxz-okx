import 'package:flutter/foundation.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter/services.dart';

class LocalNotificationService {
  LocalNotificationService._();

  static final LocalNotificationService instance = LocalNotificationService._();
  static const MethodChannel _attentionChannel = MethodChannel(
    'aiokx/app_attention',
  );

  final FlutterLocalNotificationsPlugin _plugin =
      FlutterLocalNotificationsPlugin();

  final ValueNotifier<bool> alarmActive = ValueNotifier<bool>(false);

  bool _initialized = false;
  int _notificationId = 0;

  Future<void> initialize() async {
    if (_initialized) {
      return;
    }

    const android = AndroidInitializationSettings('@mipmap/ic_launcher');
    const darwin = DarwinInitializationSettings(
      requestAlertPermission: true,
      requestBadgePermission: true,
      requestSoundPermission: true,
    );
    const settings = InitializationSettings(
      android: android,
      iOS: darwin,
      macOS: darwin,
    );

    await _plugin.initialize(settings: settings);
    await _requestPermissions();
    _initialized = true;
  }

  Future<void> showMonitorAlert({
    required String title,
    required String body,
  }) async {
    await initialize();

    const androidDetails = AndroidNotificationDetails(
      'monitor_alerts',
      '实时监控提醒',
      channelDescription: '监控条件命中时的本地通知提醒',
      importance: Importance.max,
      priority: Priority.high,
      ticker: 'ticker',
      playSound: false,
    );
    const darwinDetails = DarwinNotificationDetails(
      presentAlert: true,
      presentBadge: true,
      presentSound: false,
    );
    const details = NotificationDetails(
      android: androidDetails,
      iOS: darwinDetails,
      macOS: darwinDetails,
    );

    _notificationId += 1;
    await _plugin.show(
      id: _notificationId,
      title: title,
      body: body,
      notificationDetails: details,
    );
    await startPersistentAlarm();
    await _requestDockAttention();
  }

  Future<void> togglePersistentAlarm() async {
    if (alarmActive.value) {
      await stopPersistentAlarm();
      return;
    }
    await startPersistentAlarm();
  }

  Future<void> startPersistentAlarm() async {
    if (alarmActive.value) {
      return;
    }
    alarmActive.value = true;

    if (kIsWeb) {
      return;
    }

    try {
      switch (defaultTargetPlatform) {
        case TargetPlatform.iOS:
          await _attentionChannel.invokeMethod<void>('startPersistentAlarm');
        case TargetPlatform.macOS:
          await _attentionChannel.invokeMethod<void>('startPersistentAlarm');
        case TargetPlatform.android:
          await HapticFeedback.vibrate();
        case TargetPlatform.fuchsia:
        case TargetPlatform.linux:
        case TargetPlatform.windows:
          break;
      }
    } catch (_) {
      alarmActive.value = false;
    }
  }

  Future<void> stopPersistentAlarm() async {
    if (!alarmActive.value) {
      return;
    }
    alarmActive.value = false;

    if (kIsWeb) {
      return;
    }

    try {
      switch (defaultTargetPlatform) {
        case TargetPlatform.macOS:
        case TargetPlatform.iOS:
          await _attentionChannel.invokeMethod<void>('stopPersistentAlarm');
        case TargetPlatform.android:
        case TargetPlatform.fuchsia:
        case TargetPlatform.linux:
        case TargetPlatform.windows:
          break;
      }
    } catch (_) {
      // Ignore stop failures and let the UI continue.
    }
  }

  Future<void> _requestPermissions() async {
    if (kIsWeb) {
      return;
    }

    final androidPlugin = _plugin.resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin>();
    await androidPlugin?.requestNotificationsPermission();

    final iosPlugin = _plugin.resolvePlatformSpecificImplementation<
        IOSFlutterLocalNotificationsPlugin>();
    await iosPlugin?.requestPermissions(
      alert: true,
      badge: true,
      sound: true,
    );

    final macosPlugin = _plugin.resolvePlatformSpecificImplementation<
        MacOSFlutterLocalNotificationsPlugin>();
    await macosPlugin?.requestPermissions(
      alert: true,
      badge: true,
      sound: true,
    );
  }

  Future<void> _requestDockAttention() async {
    if (kIsWeb || defaultTargetPlatform != TargetPlatform.macOS) {
      return;
    }
    try {
      await _attentionChannel.invokeMethod<void>('requestDockAttention');
    } catch (_) {
      // Keep notifications working even if the native macOS bridge is unavailable.
    }
  }
}
