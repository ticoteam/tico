// VoiceProcessingIO uses the hardware output mix (including other apps) as its echo reference
// on macOS. A silent output graph keeps that reference running without replaying the process tap.
#import <AVFAudio/AVFAudio.h>
#import <AudioToolbox/AudioToolbox.h>
#import <CoreAudio/CoreAudio.h>

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

typedef void (*TicoVoiceReceive)(void *, const float *const *, UInt32, UInt32, UInt64, double);
typedef void (*TicoVoiceRelease)(void *);

// The block owns its Rust context. Releasing it after the last callback, rather than after stop,
// also covers a callback still returning when the engine is torn down.
@interface TicoVoiceContext : NSObject
@property void *pointer;
@property TicoVoiceRelease releaseContext;
@end
@implementation TicoVoiceContext
- (void)dealloc { if (_releaseContext) _releaseContext(_pointer); }
@end

@interface TicoVoiceCapture : NSObject
@property AVAudioEngine *engine;
@property BOOL tapped;
@end
@implementation TicoVoiceCapture
- (void)dealloc {
    [_engine stop];
    if (_tapped) [_engine.inputNode removeTapOnBus:0];
}
@end

// The caller transfers context ownership even on failure. Unknown routes take the unchanged
// CPAL path: USB/Bluetooth/headphones and explicit non-default microphones never get processing.
void *tico_voice_start(const char *selected, void *context, TicoVoiceReceive receive,
                       TicoVoiceRelease releaseContext, double *rate) {
    @autoreleasepool {
        TicoVoiceContext *owner = [TicoVoiceContext new];
        owner.pointer = context;
        owner.releaseContext = releaseContext;
        AudioDeviceID input = 0, output = 0;
        if (!readProperty(kAudioObjectSystemObject, kAudioHardwarePropertyDefaultInputDevice,
                          kAudioObjectPropertyScopeGlobal, &input, sizeof(input)) || !builtIn(input) ||
            !readProperty(kAudioObjectSystemObject, kAudioHardwarePropertyDefaultOutputDevice,
                          kAudioObjectPropertyScopeGlobal, &output, sizeof(output)) || !speakers(output)) return NULL;
        if (selected) {
            CFStringRef name = NULL;
            if (!readProperty(input, kAudioObjectPropertyName, kAudioObjectPropertyScopeGlobal,
                              &name, sizeof(name))) return NULL;
            BOOL matches = [(__bridge NSString *)name isEqualToString:[NSString stringWithUTF8String:selected]];
            CFRelease(name);
            if (!matches) return NULL;
        }
        @try {
            TicoVoiceCapture *capture = [TicoVoiceCapture new];
            capture.engine = [AVAudioEngine new];
            AVAudioInputNode *node = capture.engine.inputNode;
            NSError *error = nil;
            if (![node setVoiceProcessingEnabled:YES error:&error]) return NULL;
            // Minimum, fixed ducking; never write a system/device volume or change the default route.
            node.voiceProcessingOtherAudioDuckingConfiguration =
                (AVAudioVoiceProcessingOtherAudioDuckingConfiguration){NO, AVAudioVoiceProcessingOtherAudioDuckingLevelMin};
            node.voiceProcessingAGCEnabled = NO;
            AVAudioFormat *format = [node outputFormatForBus:0];
            if (format.commonFormat != AVAudioPCMFormatFloat32 || format.interleaved ||
                format.channelCount == 0 || format.sampleRate < 8000 || format.sampleRate > 192000) return NULL;
            AVAudioFormat *silenceFormat = [capture.engine.outputNode inputFormatForBus:0];
            AVAudioSourceNode *silence = [[AVAudioSourceNode alloc] initWithFormat:silenceFormat
                renderBlock:^OSStatus(BOOL *silent, const AudioTimeStamp *timestamp, AVAudioFrameCount frames, AudioBufferList *data) {
                    (void)timestamp;
                    (void)frames;
                    *silent = YES;
                    for (UInt32 i = 0; i < data->mNumberBuffers; ++i) memset(data->mBuffers[i].mData, 0, data->mBuffers[i].mDataByteSize);
                    return noErr;
                }];
            [capture.engine attachNode:silence];
            [capture.engine connect:silence to:capture.engine.mainMixerNode format:silenceFormat];
            [node installTapOnBus:0 bufferSize:1024 format:format block:^(AVAudioPCMBuffer *buffer, AVAudioTime *time) {
                receive(owner.pointer, (const float *const *)buffer.floatChannelData,
                        buffer.format.channelCount, buffer.frameLength, time.hostTime, buffer.format.sampleRate);
            }];
            capture.tapped = YES;
            *rate = format.sampleRate;
            if (![capture.engine startAndReturnError:&error]) return NULL;
            return (__bridge_retained void *)capture;
        } @catch (NSException *exception) {
            return NULL;
        }
    }
}

void tico_voice_stop(void *pointer) {
    @autoreleasepool {
        TicoVoiceCapture *capture = (__bridge_transfer TicoVoiceCapture *)pointer;
        [capture.engine stop];
        if (capture.tapped) {
            [capture.engine.inputNode removeTapOnBus:0];
            capture.tapped = NO;
        }
    }
}
