import Flutter
import AudioToolbox
import UIKit
import AVFoundation

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  private var alarmEngine: AVAudioEngine?
  private var alarmSourceNode: AVAudioSourceNode?
  private var alarmSampleRate: Double = 44_100
  private var alarmCarrierPhase: Double = 0
  private var alarmSweepPhase: Double = 0

  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    let channel = FlutterMethodChannel(
      name: "aiokx/app_attention",
      binaryMessenger: engineBridge.applicationRegistrar.messenger()
    )
    channel.setMethodCallHandler { [weak self] call, result in
      guard let self = self else {
        result(nil)
        return
      }

      switch call.method {
      case "startPersistentAlarm":
        self.startPersistentAlarm()
        result(nil)
      case "stopPersistentAlarm":
        self.stopPersistentAlarm()
        result(nil)
      case "requestDockAttention":
        result(nil)
      default:
        result(FlutterMethodNotImplemented)
      }
    }
  }

  private func startPersistentAlarm() {
    stopPersistentAlarm()

    do {
      let session = AVAudioSession.sharedInstance()
      try session.setCategory(.playback, mode: .default, options: [.mixWithOthers])
      try session.setActive(true)

      let engine = AVAudioEngine()
      let format = AVAudioFormat(
        standardFormatWithSampleRate: 44_100,
        channels: 2
      )!

      alarmSampleRate = format.sampleRate
      alarmCarrierPhase = 0
      alarmSweepPhase = 0

      let sourceNode = AVAudioSourceNode { [weak self] _, _, frameCount, audioBufferList -> OSStatus in
        guard let self = self else {
          return noErr
        }

        let bufferList = UnsafeMutableAudioBufferListPointer(audioBufferList)
        let frameTotal = Int(frameCount)
        let twoPi = Double.pi * 2

        for frame in 0..<frameTotal {
          let sweep = (sin(self.alarmSweepPhase) + 1) * 0.5
          let frequency = 720.0 + (sweep * 520.0)
          let sampleValue = Float(sin(self.alarmCarrierPhase) * 0.28)

          self.alarmCarrierPhase += twoPi * frequency / self.alarmSampleRate
          if self.alarmCarrierPhase >= twoPi {
            self.alarmCarrierPhase -= twoPi
          }

          self.alarmSweepPhase += twoPi * 1.8 / self.alarmSampleRate
          if self.alarmSweepPhase >= twoPi {
            self.alarmSweepPhase -= twoPi
          }

          for buffer in bufferList {
            let pointer = buffer.mData!.assumingMemoryBound(to: Float.self)
            pointer[frame] = sampleValue
          }
        }

        return noErr
      }

      engine.attach(sourceNode)
      engine.connect(sourceNode, to: engine.mainMixerNode, format: format)
      engine.mainMixerNode.outputVolume = 1.0
      try engine.start()

      alarmEngine = engine
      alarmSourceNode = sourceNode
      AudioServicesPlaySystemSound(kSystemSoundID_Vibrate)
    } catch {
      AudioServicesPlaySystemSound(kSystemSoundID_Vibrate)
    }
  }

  private func stopPersistentAlarm() {
    alarmEngine?.stop()
    if let sourceNode = alarmSourceNode {
      alarmEngine?.detach(sourceNode)
    }
    alarmSourceNode = nil
    alarmEngine = nil

    do {
      try AVAudioSession.sharedInstance().setActive(false, options: [.notifyOthersOnDeactivation])
    } catch {
      // Ignore session shutdown failures.
    }
  }
}
