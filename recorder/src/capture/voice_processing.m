// Raw capture owns the recording. A separate VoiceProcessingIO engine is only a live-text aid.
#import <AVFAudio/AVFAudio.h>
#import <AudioToolbox/AudioToolbox.h>
#import <CoreAudio/CoreAudio.h>
#import <stdatomic.h>

typedef void (*TicoVoiceReceive)(void *, const float *const *, UInt32, UInt32, UInt64, double);
typedef void (*TicoVoiceRelease)(void *);
typedef void (*TicoVoiceState)(void *, bool);

static BOOL readProperty(AudioObjectID object, AudioObjectPropertySelector selector,
                         AudioObjectPropertyScope scope, void *value, UInt32 size) {
    AudioObjectPropertyAddress address = {selector, scope, kAudioObjectPropertyElementMain};
    return AudioObjectGetPropertyData(object, &address, 0, NULL, &size, value) == noErr;
}
static BOOL builtIn(AudioDeviceID device) {
    UInt32 transport = 0;
    return readProperty(device, kAudioDevicePropertyTransportType, kAudioObjectPropertyScopeGlobal,
                        &transport, sizeof(transport)) && transport == kAudioDeviceTransportTypeBuiltIn;
}
static BOOL speakers(AudioDeviceID device) {
    if (!builtIn(device)) return NO;
    UInt32 source = 0;
    if (readProperty(device, kAudioDevicePropertyDataSource, kAudioDevicePropertyScopeOutput,
                     &source, sizeof(source))) return source == 'ispk';
    UInt32 connected = 0;
    if (readProperty(device, kAudioDevicePropertyJackIsConnected, kAudioDevicePropertyScopeOutput,
                     &connected, sizeof(connected))) return connected == 0;
    CFStringRef uid = NULL;
    if (!readProperty(device, kAudioDevicePropertyDeviceUID, kAudioObjectPropertyScopeGlobal,
                      &uid, sizeof(uid))) return NO;
    BOOL result = [(__bridge NSString *)uid isEqualToString:@"BuiltInSpeakerDevice"];
    CFRelease(uid);
    return result;
}
static BOOL eligible(const char *selected) {
    AudioDeviceID input = 0, output = 0;
    if (!readProperty(kAudioObjectSystemObject, kAudioHardwarePropertyDefaultInputDevice,
                      kAudioObjectPropertyScopeGlobal, &input, sizeof(input)) || !builtIn(input) ||
        !readProperty(kAudioObjectSystemObject, kAudioHardwarePropertyDefaultOutputDevice,
                      kAudioObjectPropertyScopeGlobal, &output, sizeof(output)) || !speakers(output)) return NO;
    if (selected) {
        CFStringRef name = NULL;
        if (!readProperty(input, kAudioObjectPropertyName, kAudioObjectPropertyScopeGlobal,
                          &name, sizeof(name))) return NO;
        BOOL matches = [(__bridge NSString *)name isEqualToString:[NSString stringWithUTF8String:selected]];
        CFRelease(name);
        if (!matches) return NO;
    }
    return YES;
}

// Tap blocks retain their context until their last callback has returned, including teardown.
@interface TicoVoiceContext : NSObject
@property void *pointer;
@property TicoVoiceRelease releaseContext;
@property TicoVoiceReceive receive;
@property TicoVoiceState state;
@end
@implementation TicoVoiceContext
- (void)dealloc { if (_releaseContext) _releaseContext(_pointer); }
@end

@interface TicoVoiceCapture : NSObject
@property AVAudioEngine *raw;
@property AVAudioEngine *voice;
@property TicoVoiceContext *rawContext;
@property TicoVoiceContext *voiceContext;
@property dispatch_queue_t queue;
@property NSMutableArray *observers;
@property BOOL stopped;
@property AudioDeviceID selectedDevice;
@property NSString *selectedName;
@property BOOL testing;
@property BOOL failVoice;
@property NSUInteger generation;
- (BOOL)restart;
- (void)close;
@end

@implementation TicoVoiceCapture
- (AVAudioEngine *)engine:(BOOL)processed context:(TicoVoiceContext *)context {
    @try {
        AVAudioEngine *engine = [AVAudioEngine new];
        AVAudioInputNode *input = engine.inputNode;
        NSError *error = nil;
        if (!processed && self.selectedDevice != 0) {
            AudioDeviceID device = self.selectedDevice;
            if (AudioUnitSetProperty(input.audioUnit, kAudioOutputUnitProperty_CurrentDevice,
                                     kAudioUnitScope_Global, 0, &device, sizeof(device)) != noErr) return nil;
        }
        if (processed) {
            if (![input setVoiceProcessingEnabled:YES error:&error]) return nil;
            input.voiceProcessingOtherAudioDuckingConfiguration =
                (AVAudioVoiceProcessingOtherAudioDuckingConfiguration){NO, AVAudioVoiceProcessingOtherAudioDuckingLevelMin};
            input.voiceProcessingAGCEnabled = NO;
        }
        AVAudioFormat *hardware = [input outputFormatForBus:0];
        if (hardware.channelCount == 0 || hardware.sampleRate < 8000) return nil;
        // A mixer converts both routes to a fixed client format. Hardware rate/channel changes
        // never reinterpret queued packets or change the recording's timestamp scale.
        AVAudioFormat *format = [[AVAudioFormat alloc] initStandardFormatWithSampleRate:16000 channels:1];
        AVAudioMixerNode *mixer = [AVAudioMixerNode new];
        [engine attachNode:mixer];
        [engine connect:input to:mixer format:hardware];
        [engine connect:mixer to:engine.mainMixerNode format:format];
        engine.mainMixerNode.outputVolume = 0;
        [mixer installTapOnBus:0 bufferSize:1024 format:format block:^(AVAudioPCMBuffer *buffer, AVAudioTime *time) {
            context.receive(context.pointer, (const float *const *)buffer.floatChannelData,
                            buffer.format.channelCount, buffer.frameLength, time.hostTime, buffer.format.sampleRate);
        }];
        if (![engine startAndReturnError:&error]) { [engine stop]; return nil; }
        return engine;
    } @catch (NSException *exception) { return nil; }
}
- (void)observe:(id)engine {
    __weak TicoVoiceCapture *weak = self;
    NSUInteger generation = self.generation;
    id observer = [[NSNotificationCenter defaultCenter] addObserverForName:AVAudioEngineConfigurationChangeNotification
        object:engine queue:nil usingBlock:^(NSNotification *notification) {
            (void)notification;
            TicoVoiceCapture *capture = weak;
            if (!capture) return;
            // AVAudioEngine notifications run on its internal thread. Rebuild on our own queue;
            // never stop/release the engine on that notification thread.
            dispatch_async(capture.queue, ^{
                if (!capture.stopped && capture.generation == generation) [capture restart];
            });
        }];
    [self.observers addObject:observer];
}
- (BOOL)restart {
    for (id observer in self.observers) [[NSNotificationCenter defaultCenter] removeObserver:observer];
    [self.observers removeAllObjects];
    self.generation += 1;
    if (!self.testing) [self.voice stop];
    self.voice = nil;
    self.rawContext.state(self.rawContext.pointer, false);
    if (!self.testing) [self.raw stop];
    self.raw = nil;
    if (self.testing) {
        // Hardware-free bridge seam uses the same notification/restart/fallback lifecycle.
        self.raw = (AVAudioEngine *)[NSObject new];
        if (!self.failVoice) self.voice = (AVAudioEngine *)[NSObject new];
    } else {
        self.raw = [self engine:NO context:self.rawContext];
        if (!self.raw) {
            // A route can announce its new format before its hardware is ready. Keep trying
            // plain capture rather than removing the last observer and silently losing the mic.
            __weak TicoVoiceCapture *weak = self;
            NSUInteger generation = self.generation;
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC / 5), self.queue, ^{
                TicoVoiceCapture *capture = weak;
                if (capture && !capture.stopped && capture.generation == generation) [capture restart];
            });
            return NO;
        }
        if (eligible(self.selectedName.UTF8String)) self.voice = [self engine:YES context:self.voiceContext];
    }
    self.rawContext.state(self.rawContext.pointer, self.voice != nil);
    [self observe:self.raw];
    if (self.voice) [self observe:self.voice];
    return YES;
}
- (void)close {
    self.stopped = YES;
    for (id observer in self.observers) [[NSNotificationCenter defaultCenter] removeObserver:observer];
    [self.observers removeAllObjects];
    if (!self.testing) { [self.voice stop]; [self.raw stop]; }
    self.voice = nil;
    self.raw = nil;
}
- (void)dealloc { [self close]; }
@end

// Both contexts transfer on success and failure. Raw and processed callbacks have separate
// producers: their two audio threads must never share a mutable Rust sink.
void *tico_voice_start(const char *selected, void *raw, void *voice, TicoVoiceReceive receive,
                       TicoVoiceRelease releaseContext, TicoVoiceState state, bool testing) {
    @autoreleasepool {
        TicoVoiceCapture *capture = [TicoVoiceCapture new];
        capture.queue = dispatch_queue_create("tico.voice-route", DISPATCH_QUEUE_SERIAL);
        capture.observers = [NSMutableArray new];
        capture.testing = testing;
        capture.rawContext = [TicoVoiceContext new];
        capture.rawContext.pointer = raw;
        capture.rawContext.receive = receive;
        capture.rawContext.releaseContext = releaseContext;
        capture.rawContext.state = state;
        capture.voiceContext = [TicoVoiceContext new];
        capture.voiceContext.pointer = voice;
        capture.voiceContext.receive = receive;
        capture.voiceContext.releaseContext = releaseContext;
        if (!testing && !eligible(selected)) return NULL;
        if (selected && !testing) {
            capture.selectedName = [NSString stringWithUTF8String:selected];
            AudioDeviceID device = 0;
            if (!readProperty(kAudioObjectSystemObject, kAudioHardwarePropertyDefaultInputDevice,
                              kAudioObjectPropertyScopeGlobal, &device, sizeof(device))) return NULL;
            capture.selectedDevice = device;
        }
        __block BOOL started = NO;
        dispatch_sync(capture.queue, ^{ started = [capture restart]; });
        if (!started) return NULL;
        return (__bridge_retained void *)capture;
    }
}
void tico_voice_stop(void *pointer) {
    @autoreleasepool {
        TicoVoiceCapture *capture = (__bridge_transfer TicoVoiceCapture *)pointer;
        dispatch_sync(capture.queue, ^{ [capture close]; });
    }
}

// Called only by Rust bridge tests; it posts the actual configuration notification and waits
// for its queued restart before producing distinguishable raw/processed samples.
void tico_voice_test_configuration(void *pointer, bool failVoice) {
    TicoVoiceCapture *capture = (__bridge TicoVoiceCapture *)pointer;
    if (!capture.testing) return;
    capture.failVoice = failVoice;
    [[NSNotificationCenter defaultCenter] postNotificationName:AVAudioEngineConfigurationChangeNotification object:capture.raw];
    dispatch_sync(capture.queue, ^{
        float raw = 0.25f, processed = 0.75f;
        const float *rawData = &raw, *processedData = &processed;
        capture.rawContext.receive(capture.rawContext.pointer, &rawData, 1, 1, 0, 16000);
        if (capture.voice) capture.voiceContext.receive(capture.voiceContext.pointer, &processedData, 1, 1, 0, 16000);
    });
}
