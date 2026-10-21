// call-captions-audio: sets up the Mac's audio routing for Call Captions.
//
// Creates "Call Captions Output", a multi-output device that plays everything to the device you
// listen on (your headset) and, at the same time, into BlackHole, which the captioner reads.
// It also reads and switches the Mac's sound output so the routing is only active while
// captions run. It never creates, changes or removes BlackHole itself.
//
//   call-captions-audio outputs                     uid<TAB>kind<TAB>name for each output device
//   call-captions-audio default-output              uid of the current sound output
//   call-captions-audio default-input               uid of the current sound input
//   call-captions-audio set-output UID              make UID the sound output
//   call-captions-audio ensure-multi HEAR_UID BLACKHOLE_UID
//                                                   create or update "Call Captions Output";
//                                                   prints "created" or "unchanged"
//   call-captions-audio remove-multi                remove "Call Captions Output"

#include <CoreAudio/CoreAudio.h>
#include <CoreFoundation/CoreFoundation.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MULTI_UID "local.callcaptions.multioutput"
#define MULTI_NAME "Call Captions Output"

static AudioObjectPropertyAddress addr(AudioObjectPropertySelector selector, AudioObjectPropertyScope scope) {
    AudioObjectPropertyAddress a = {selector, scope, kAudioObjectPropertyElementMain};
    return a;
}

static void cfstring_to_c(CFStringRef s, char *out, size_t size) {
    out[0] = '\0';
    if (s) CFStringGetCString(s, out, (CFIndex)size, kCFStringEncodingUTF8);
}

static int get_string(AudioObjectID dev, AudioObjectPropertySelector selector, char *out, size_t size) {
    AudioObjectPropertyAddress a = addr(selector, kAudioObjectPropertyScopeGlobal);
    CFStringRef value = NULL;
    UInt32 dataSize = sizeof(value);
    out[0] = '\0';
    if (AudioObjectGetPropertyData(dev, &a, 0, NULL, &dataSize, &value) != noErr || !value) return -1;
    cfstring_to_c(value, out, size);
    CFRelease(value);
    return 0;
}

static UInt32 output_channels(AudioObjectID dev) {
    AudioObjectPropertyAddress a = addr(kAudioDevicePropertyStreamConfiguration, kAudioObjectPropertyScopeOutput);
    UInt32 size = 0;
    if (AudioObjectGetPropertyDataSize(dev, &a, 0, NULL, &size) != noErr || size == 0) return 0;
    AudioBufferList *list = malloc(size);
    if (!list) return 0;
    UInt32 channels = 0;
    if (AudioObjectGetPropertyData(dev, &a, 0, NULL, &size, list) == noErr) {
        for (UInt32 i = 0; i < list->mNumberBuffers; i++) channels += list->mBuffers[i].mNumberChannels;
    }
    free(list);
    return channels;
}

static const char *kind_of(AudioObjectID dev) {
    AudioObjectPropertyAddress a = addr(kAudioDevicePropertyTransportType, kAudioObjectPropertyScopeGlobal);
    UInt32 transport = 0, size = sizeof(transport);
    AudioObjectGetPropertyData(dev, &a, 0, NULL, &size, &transport);
    switch (transport) {
        case kAudioDeviceTransportTypeBuiltIn: return "builtin";
        case kAudioDeviceTransportTypeUSB: return "usb";
        case kAudioDeviceTransportTypeBluetooth:
        case kAudioDeviceTransportTypeBluetoothLE: return "bluetooth";
        case kAudioDeviceTransportTypeVirtual: return "virtual";
        case kAudioDeviceTransportTypeAggregate: return "aggregate";
        default: return "other";
    }
}

// Returns a malloc'd array of all device IDs; *count receives its length.
static AudioObjectID *all_devices(UInt32 *count) {
    AudioObjectPropertyAddress a = addr(kAudioHardwarePropertyDevices, kAudioObjectPropertyScopeGlobal);
    UInt32 size = 0;
    *count = 0;
    if (AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, &a, 0, NULL, &size) != noErr) return NULL;
    AudioObjectID *ids = malloc(size ? size : 1);
    if (!ids) return NULL;
    if (AudioObjectGetPropertyData(kAudioObjectSystemObject, &a, 0, NULL, &size, ids) != noErr) {
        free(ids);
        return NULL;
    }
    *count = size / sizeof(AudioObjectID);
    return ids;
}

static AudioObjectID find_device(const char *uid) {
    UInt32 count = 0;
    AudioObjectID *ids = all_devices(&count);
    AudioObjectID found = kAudioObjectUnknown;
    char buf[512];
    for (UInt32 i = 0; i < count; i++) {
        if (get_string(ids[i], kAudioDevicePropertyDeviceUID, buf, sizeof(buf)) == 0 && strcmp(buf, uid) == 0) {
            found = ids[i];
            break;
        }
    }
    free(ids);
    return found;
}

static int print_default(AudioObjectPropertySelector selector) {
    AudioObjectPropertyAddress a = addr(selector, kAudioObjectPropertyScopeGlobal);
    AudioObjectID dev = kAudioObjectUnknown;
    UInt32 size = sizeof(dev);
    char uid[512];
    if (AudioObjectGetPropertyData(kAudioObjectSystemObject, &a, 0, NULL, &size, &dev) != noErr ||
        get_string(dev, kAudioDevicePropertyDeviceUID, uid, sizeof(uid)) != 0) {
        fprintf(stderr, "Couldn't read the current sound device.\n");
        return 1;
    }
    printf("%s\n", uid);
    return 0;
}

static int cmd_outputs(void) {
    UInt32 count = 0;
    AudioObjectID *ids = all_devices(&count);
    char uid[512], name[512];
    for (UInt32 i = 0; i < count; i++) {
        if (output_channels(ids[i]) == 0) continue;
        if (get_string(ids[i], kAudioDevicePropertyDeviceUID, uid, sizeof(uid)) != 0) continue;
        get_string(ids[i], kAudioObjectPropertyName, name, sizeof(name));
        printf("%s\t%s\t%s\n", uid, kind_of(ids[i]), name);
    }
    free(ids);
    return 0;
}

static int cmd_set_output(const char *uid) {
    AudioObjectID dev = find_device(uid);
    if (dev == kAudioObjectUnknown) {
        fprintf(stderr, "No sound device with id %s.\n", uid);
        return 1;
    }
    AudioObjectPropertyAddress a = addr(kAudioHardwarePropertyDefaultOutputDevice, kAudioObjectPropertyScopeGlobal);
    if (AudioObjectSetPropertyData(kAudioObjectSystemObject, &a, 0, NULL, sizeof(dev), &dev) != noErr) {
        fprintf(stderr, "Couldn't switch the sound output.\n");
        return 1;
    }
    return 0;
}

// True if our multi-output device exists with exactly these two sub-devices, in this order.
static int multi_matches(AudioObjectID dev, const char *hear, const char *blackhole) {
    AudioObjectPropertyAddress a = addr(kAudioAggregateDevicePropertyFullSubDeviceList, kAudioObjectPropertyScopeGlobal);
    CFArrayRef list = NULL;
    UInt32 size = sizeof(list);
    if (AudioObjectGetPropertyData(dev, &a, 0, NULL, &size, &list) != noErr || !list) return 0;
    int ok = 0;
    if (CFArrayGetCount(list) == 2) {
        char first[512], second[512];
        cfstring_to_c((CFStringRef)CFArrayGetValueAtIndex(list, 0), first, sizeof(first));
        cfstring_to_c((CFStringRef)CFArrayGetValueAtIndex(list, 1), second, sizeof(second));
        ok = strcmp(first, hear) == 0 && strcmp(second, blackhole) == 0;
    }
    CFRelease(list);
    return ok;
}

static CFDictionaryRef sub_device(const char *uid, int drift) {
    CFStringRef keys[2] = {CFSTR(kAudioSubDeviceUIDKey), CFSTR(kAudioSubDeviceDriftCompensationKey)};
    CFStringRef uidString = CFStringCreateWithCString(NULL, uid, kCFStringEncodingUTF8);
    CFNumberRef driftNumber = CFNumberCreate(NULL, kCFNumberIntType, &drift);
    const void *values[2] = {uidString, driftNumber};
    CFDictionaryRef d = CFDictionaryCreate(NULL, (const void **)keys, values, 2, &kCFTypeDictionaryKeyCallBacks,
                                           &kCFTypeDictionaryValueCallBacks);
    CFRelease(uidString);
    CFRelease(driftNumber);
    return d;
}

static int cmd_ensure_multi(const char *hear, const char *blackhole) {
    if (find_device(hear) == kAudioObjectUnknown) {
        fprintf(stderr, "The device to listen on (%s) isn't connected.\n", hear);
        return 1;
    }
    if (find_device(blackhole) == kAudioObjectUnknown) {
        fprintf(stderr, "BlackHole (%s) wasn't found.\n", blackhole);
        return 1;
    }
    AudioObjectID existing = find_device(MULTI_UID);
    if (existing != kAudioObjectUnknown) {
        if (multi_matches(existing, hear, blackhole)) {
            printf("unchanged\n");
            return 0;
        }
        AudioHardwareDestroyAggregateDevice(existing);
    }

    int zero = 0, one = 1;
    CFNumberRef no = CFNumberCreate(NULL, kCFNumberIntType, &zero);
    CFNumberRef yes = CFNumberCreate(NULL, kCFNumberIntType, &one);
    CFStringRef hearString = CFStringCreateWithCString(NULL, hear, kCFStringEncodingUTF8);
    // Your headset keeps the clock; BlackHole follows it with drift correction.
    CFDictionaryRef subs[2] = {sub_device(hear, 0), sub_device(blackhole, 1)};
    CFArrayRef subList = CFArrayCreate(NULL, (const void **)subs, 2, &kCFTypeArrayCallBacks);

    CFMutableDictionaryRef desc = CFDictionaryCreateMutable(NULL, 0, &kCFTypeDictionaryKeyCallBacks,
                                                            &kCFTypeDictionaryValueCallBacks);
    CFDictionarySetValue(desc, CFSTR(kAudioAggregateDeviceNameKey), CFSTR(MULTI_NAME));
    CFDictionarySetValue(desc, CFSTR(kAudioAggregateDeviceUIDKey), CFSTR(MULTI_UID));
    CFDictionarySetValue(desc, CFSTR(kAudioAggregateDeviceIsPrivateKey), no);  // visible system-wide
    CFDictionarySetValue(desc, CFSTR(kAudioAggregateDeviceIsStackedKey), yes); // multi-output
    CFDictionarySetValue(desc, CFSTR(kAudioAggregateDeviceMainSubDeviceKey), hearString);
    CFDictionarySetValue(desc, CFSTR(kAudioAggregateDeviceSubDeviceListKey), subList);

    AudioObjectID created = kAudioObjectUnknown;
    OSStatus status = AudioHardwareCreateAggregateDevice(desc, &created);

    CFRelease(desc);
    CFRelease(subList);
    CFRelease(subs[0]);
    CFRelease(subs[1]);
    CFRelease(hearString);
    CFRelease(no);
    CFRelease(yes);

    if (status != noErr || created == kAudioObjectUnknown) {
        fprintf(stderr, "Couldn't create \"%s\" (error %d).\n", MULTI_NAME, (int)status);
        return 1;
    }
    // Let the audio system finish setting the new device up before anyone switches to it.
    CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.3, false);
    printf("created\n");
    return 0;
}

static int cmd_remove_multi(void) {
    AudioObjectID existing = find_device(MULTI_UID);
    if (existing == kAudioObjectUnknown) return 0;
    if (AudioHardwareDestroyAggregateDevice(existing) != noErr) {
        fprintf(stderr, "Couldn't remove \"%s\".\n", MULTI_NAME);
        return 1;
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc >= 2) {
        const char *cmd = argv[1];
        if (strcmp(cmd, "outputs") == 0) return cmd_outputs();
        if (strcmp(cmd, "default-output") == 0) return print_default(kAudioHardwarePropertyDefaultOutputDevice);
        if (strcmp(cmd, "default-input") == 0) return print_default(kAudioHardwarePropertyDefaultInputDevice);
        if (strcmp(cmd, "set-output") == 0 && argc == 3) return cmd_set_output(argv[2]);
        if (strcmp(cmd, "ensure-multi") == 0 && argc == 4) return cmd_ensure_multi(argv[2], argv[3]);
        if (strcmp(cmd, "remove-multi") == 0) return cmd_remove_multi();
    }
    fprintf(stderr,
            "usage: call-captions-audio outputs | default-output | default-input | set-output UID |\n"
            "       ensure-multi HEAR_UID BLACKHOLE_UID | remove-multi\n");
    return 2;
}
