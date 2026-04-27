import Cocoa
import AVFoundation
import FlutterMacOS

@main
class AppDelegate: FlutterAppDelegate {
  private var dockAttentionRequestId: Int?
  private var alarmEngine: AVAudioEngine?
  private var alarmSourceNode: AVAudioSourceNode?
  private var alarmSampleRate: Double = 44_100
  private var alarmCarrierPhase: Double = 0
  private var alarmSweepPhase: Double = 0

  override func applicationDidFinishLaunching(_ notification: Notification) {
    guard let controller = mainFlutterWindow?.contentViewController as? FlutterViewController else {
      super.applicationDidFinishLaunching(notification)
      return
    }
    let channel = FlutterMethodChannel(
      name: "aiokx/app_attention",
      binaryMessenger: controller.engine.binaryMessenger
    )
    channel.setMethodCallHandler { [weak self] call, result in
      guard let self = self else {
        result(nil)
        return
      }

      switch call.method {
      case "requestDockAttention":
        self.requestDockAttention()
        result(nil)
      case "startPersistentAlarm":
        self.startPersistentAlarm()
        result(nil)
      case "stopPersistentAlarm":
        self.stopPersistentAlarm()
        result(nil)
      default:
        result(FlutterMethodNotImplemented)
      }
    }

    super.applicationDidFinishLaunching(notification)
  }

  override func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
    return true
  }

  override func applicationSupportsSecureRestorableState(_ app: NSApplication) -> Bool {
    return true
  }

  private func requestDockAttention() {
    if let requestId = dockAttentionRequestId {
      NSApp.cancelUserAttentionRequest(requestId)
    }
    dockAttentionRequestId = NSApp.requestUserAttention(.criticalRequest)
  }

  private func startPersistentAlarm() {
    stopPersistentAlarm()

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

    do {
      try engine.start()
      alarmEngine = engine
      alarmSourceNode = sourceNode
    } catch {
      NSSound.beep()
    }
  }

  private func stopPersistentAlarm() {
    alarmEngine?.stop()
    if let sourceNode = alarmSourceNode {
      alarmEngine?.detach(sourceNode)
    }
    alarmSourceNode = nil
    alarmEngine = nil
  }
}
