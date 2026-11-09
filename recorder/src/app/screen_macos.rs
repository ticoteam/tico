//! Main-thread ownership for the picker/stream; callbacks only replace a single JPEG slot.
use super::Session;
use block2::RcBlock;
use objc2::{
    AnyThread, ClassType, DefinedClass, define_class, msg_send, rc::Retained,
    runtime::ProtocolObject, sel,
};
use objc2_core_foundation::{
    CFArray, CFBoolean, CFData, CFDictionary, CFMutableData, CFNumber, CFRetained, CFString, CFType,
};
use objc2_core_graphics::{CGImage, CGPreflightScreenCaptureAccess};
use objc2_core_media::{CMSampleBuffer, CMTime};
use objc2_foundation::{
    NSArray, NSDictionary, NSError, NSNumber, NSObject, NSObjectProtocol, NSString,
};
use objc2_image_io::{CGImageDestination, CGImageSource};
use objc2_screen_capture_kit::*;
use std::{
    cell::RefCell,
    sync::{Arc, Mutex, atomic::Ordering},
    time::{Duration, Instant},
};

#[link(name = "VideoToolbox", kind = "framework")]
unsafe extern "C" {
    fn VTCreateCGImageFromCVPixelBuffer(
        buffer: *const std::ffi::c_void,
        options: *const std::ffi::c_void,
        image: *mut *mut CGImage,
    ) -> i32;
}
struct Ivars {
    session: Arc<Session>,
    preview_at: Mutex<Instant>,
}
fn jpeg(image: &CGImage, quality: f64) -> Option<Vec<u8>> {
    let data = CFMutableData::new(None, 0)?;
    let destination = unsafe {
        CGImageDestination::with_data(&data, &CFString::from_str("public.jpeg"), 1, None)?
    };
    let quality = CFNumber::new_f64(quality);
    let properties = CFDictionary::from_slices(
        &[unsafe { objc2_image_io::kCGImageDestinationLossyCompressionQuality }],
        &[&*quality],
    );
    unsafe {
        destination.add_image(image, Some(properties.cast_unchecked()));
    }
    unsafe { destination.finalize() }.then(|| data.to_vec())
}
fn small_image(bytes: &[u8], edge: i32) -> Option<Vec<u8>> {
    let data = CFData::from_bytes(bytes);
    let source = unsafe { CGImageSource::with_data(&data, None)? };
    let edge = CFNumber::new_i32(edge);
    let properties = CFDictionary::<CFType, CFType>::from_slices(
        &[
            unsafe { objc2_image_io::kCGImageSourceCreateThumbnailFromImageAlways }.as_ref(),
            unsafe { objc2_image_io::kCGImageSourceThumbnailMaxPixelSize }.as_ref(),
        ],
        &[CFBoolean::new(true).as_ref(), edge.as_ref()],
    );
    let image = unsafe { source.thumbnail_at_index(0, Some(properties.cast_unchecked()))? };
    jpeg(&image, 0.6)
}
define_class!(
    #[unsafe(super(NSObject))]
    #[name = "TicoScreenObserver"]
    #[ivars = Ivars]
    struct Observer;
    unsafe impl NSObjectProtocol for Observer {}
    unsafe impl SCContentSharingPickerObserver for Observer {
        #[unsafe(method(contentSharingPicker:didCancelForStream:))]
        fn cancelled(&self, _picker: &SCContentSharingPicker, _stream: Option<&SCStream>) {
            self.ivars().session.stop("");
        }
        #[unsafe(method(contentSharingPickerStartDidFailWithError:))]
        fn failed(&self, _error: &NSError) {
            self.ivars()
                .session
                .stop("macOS could not open the screen picker");
        }
        #[unsafe(method(contentSharingPicker:didUpdateWithFilter:forStream:))]
        fn selected(
            &self,
            _picker: &SCContentSharingPicker,
            filter: &SCContentFilter,
            _stream: Option<&SCStream>,
        ) {
            let observer = unsafe { Retained::retain(self as *const Self as *mut Self) }
                .expect("live observer");
            let filter = unsafe {
                Retained::retain(filter as *const SCContentFilter as *mut SCContentFilter)
            }
            .expect("live filter");
            let selected = RcBlock::new(move || observer.selected_main(&filter));
            unsafe {
                dispatch2::DispatchQueue::main().exec_async_with_block(
                    (&*selected as *const block2::Block<dyn Fn()>).cast_mut(),
                );
            }
        }
    }
    unsafe impl SCStreamDelegate for Observer {
        #[unsafe(method(stream:didStopWithError:))]
        fn stream_stopped(&self, _stream: &SCStream, error: &NSError) {
            self.ivars().session.stop(if error.code() == -3817 {
                ""
            } else {
                "Screen sharing stopped in macOS"
            });
        }
    }
    unsafe impl SCStreamOutput for Observer {
        #[unsafe(method(stream:didOutputSampleBuffer:ofType:))]
        fn frame(&self, _stream: &SCStream, sample: &CMSampleBuffer, kind: SCStreamOutputType) {
            let session = &self.ivars().session;
            if kind != SCStreamOutputType::Screen || session.stopped.load(Ordering::Acquire) {
                return;
            }
            let Some(attachments) = (unsafe { sample.sample_attachments_array(false) }) else {
                return;
            };
            let attachments = unsafe { CFRetained::cast_unchecked::<CFArray<CFType>>(attachments) };
            let Some(first) = attachments.get(0) else {
                return;
            };
            // SCK's per-frame attachment is a CFDictionary/NSDictionary with a numeric status.
            let info = unsafe {
                &*CFRetained::as_ptr(&first)
                    .as_ptr()
                    .cast::<NSDictionary<NSString, NSNumber>>()
            };
            if info
                .objectForKey(unsafe { SCStreamFrameInfoStatus })
                .is_none_or(|n| n.integerValue() != SCFrameStatus::Complete.0)
            {
                return;
            }
            let Some(buffer) = (unsafe { sample.image_buffer() }) else {
                return;
            };
            let mut image = std::ptr::null_mut();
            // VideoToolbox borrows the callback's retained pixel buffer and creates a +1 CGImage.
            if unsafe {
                VTCreateCGImageFromCVPixelBuffer(
                    CFRetained::as_ptr(&buffer).as_ptr().cast(),
                    std::ptr::null(),
                    &mut image,
                )
            } != 0
            {
                return;
            }
            let Some(image) =
                std::ptr::NonNull::new(image).map(|p| unsafe { CFRetained::from_raw(p) })
            else {
                return;
            };
            let mut bytes = None;
            for quality in [0.65, 0.5, 0.35] {
                let Some(encoded) = jpeg(&image, quality) else {
                    return;
                };
                if encoded.len() <= 1_000_000 {
                    bytes = Some(encoded);
                    break;
                }
                if quality == 0.35 {
                    bytes = small_image(&encoded, 1280).filter(|b| b.len() <= 1_000_000);
                }
            }
            // A difficult frame is skipped; it never ends an otherwise healthy share.
            let Some(bytes) = bytes else {
                return;
            };
            if self.ivars().preview_at.lock().unwrap().elapsed() >= Duration::from_secs(1) {
                let preview = small_image(&bytes, 320);
                let mut thumbnail = session.thumbnail.lock().unwrap();
                if !session.stopped.load(Ordering::Acquire) {
                    *thumbnail = preview;
                }
                *self.ivars().preview_at.lock().unwrap() = Instant::now();
            }
            let mut latest = session.latest.lock().unwrap();
            if !session.stopped.load(Ordering::Acquire) {
                *latest = Some(bytes);
            }
        }
    }
);
impl Observer {
    fn selected_main(&self, filter: &SCContentFilter) {
        if self.ivars().session.selected.swap(true, Ordering::AcqRel) {
            return;
        }
        if self.ivars().session.stopped.load(Ordering::Acquire) {
            return;
        }
        // Selection changes are disabled. Never retarget an already running stream.
        let running = CAPTURE.with(|c| c.borrow().as_ref().is_some_and(|c| c.stream.is_some()));
        if running {
            return;
        }
        let is_display = unsafe { filter.style() == SCShareableContentStyle::Display };
        if is_display {
            // includedDisplays is a newer API; refuse rather than expose Recorder private chat
            // on an older OS where the chosen display cannot safely be reconstructed.
            if !filter.respondsToSelector(sel!(includedDisplays)) {
                self.ivars().session.stop("Choose a window on this version of macOS; whole-screen sharing is unavailable here");
                return;
            }
            let displays = unsafe { filter.includedDisplays() };
            let Some(display) = displays.firstObject() else {
                self.ivars().session.stop("No display was selected");
                return;
            };
            let session = self.ivars().session.clone();
            let observer = unsafe { Retained::retain(self as *const Self as *mut Self) }
                .expect("live observer");
            let handler = RcBlock::new(
                move |content: *mut SCShareableContent, _error: *mut NSError| {
                    let Some(content) = (unsafe { content.as_ref() }) else {
                        session.stop(if !CGPreflightScreenCaptureAccess() { "Whole-screen sharing needs Screen Recording permission in System Settings → Privacy & Security; share a window instead" } else { "macOS could not exclude Recorder from the screen share; share a window instead" });
                        return;
                    };
                    let own: Vec<_> = unsafe { content.applications() }
                        .iter()
                        .filter(|a| unsafe { a.processID() } == std::process::id() as i32)
                        .collect();
                    if own.is_empty() {
                        session.stop(
                            "Could not exclude Recorder's private windows; share a window instead",
                        );
                        return;
                    }
                    let filter = unsafe {
                        SCContentFilter::initWithDisplay_excludingApplications_exceptingWindows(
                            SCContentFilter::alloc(),
                            &display,
                            &NSArray::from_retained_slice(&own),
                            &NSArray::new(),
                        )
                    };
                    // This callback has no main-thread guarantee; retain objects within the block
                    // and dispatch Objective-C work to the main queue, without moving them across Rust threads.
                    let start = RcBlock::new({
                        let observer = observer.clone();
                        move || observer.start(&filter, "Screen")
                    });
                    unsafe {
                        dispatch2::DispatchQueue::main().exec_async_with_block(
                            (&*start as *const block2::Block<dyn Fn()>).cast_mut(),
                        )
                    };
                },
            );
            unsafe { SCShareableContent::getShareableContentWithCompletionHandler(&handler) };
        } else {
            self.start(filter, "Selected window");
        }
    }
    fn start(&self, filter: &SCContentFilter, label: &str) {
        let session = &self.ivars().session;
        if session.stopped.load(Ordering::Acquire) {
            return;
        }
        let configuration = unsafe { SCStreamConfiguration::new() };
        let rect = unsafe { filter.contentRect() };
        let scale = (1920. / rect.size.width.max(rect.size.height).max(1.))
            .min(unsafe { filter.pointPixelScale() } as f64);
        unsafe {
            configuration.setWidth((rect.size.width * scale).max(2.) as usize);
            configuration.setHeight((rect.size.height * scale).max(2.) as usize);
            configuration.setMinimumFrameInterval(CMTime::new(1, 5));
            configuration.setQueueDepth(3);
            configuration.setShowsCursor(true);
            configuration.setCapturesAudio(false);
        }
        let stream = unsafe {
            SCStream::initWithFilter_configuration_delegate(
                SCStream::alloc(),
                filter,
                &configuration,
                Some(ProtocolObject::from_ref(self)),
            )
        };
        let queue = dispatch2::DispatchQueue::new("team.tico.recorder.screen", None);
        if unsafe {
            stream.addStreamOutput_type_sampleHandlerQueue_error(
                ProtocolObject::from_ref(self),
                SCStreamOutputType::Screen,
                Some(&queue),
            )
        }
        .is_err()
        {
            session.stop("macOS could not connect the screen capture output");
            return;
        }
        *session.source.lock().unwrap() = label.into();
        let started = session.clone();
        let handler = RcBlock::new(move |error: *mut NSError| {
            if error.is_null() && !started.stopped.load(Ordering::Acquire) {
                started.publish();
            } else {
                started.stop(
                    "macOS could not start screen capture; check Screen Recording permission",
                );
            }
        });
        unsafe {
            stream.startCaptureWithCompletionHandler(Some(&handler));
        }
        CAPTURE.with(|c| {
            if let Some(c) = c
                .borrow_mut()
                .as_mut()
                .filter(|c| std::ptr::eq(&*c.observer, self))
            {
                c.stream = Some(stream);
            }
        });
    }
}
struct Capture {
    observer: Retained<Observer>,
    stream: Option<Retained<SCStream>>,
}
thread_local! { static CAPTURE: RefCell<Option<Capture>> = const { RefCell::new(None) }; }
pub fn clear_if(id: &str) {
    let matches = CAPTURE.with(|c| {
        c.borrow()
            .as_ref()
            .is_some_and(|c| c.observer.ivars().session.capture_id == id)
    });
    if matches {
        clear();
    }
}
fn clear() {
    CAPTURE.with(|c| {
        if let Some(capture) = c.borrow_mut().take() {
            let picker = unsafe { SCContentSharingPicker::sharedPicker() };
            unsafe {
                picker.removeObserver(ProtocolObject::from_ref(&*capture.observer));
                picker.setActive(false);
                if let Some(stream) = capture.stream {
                    let retained_stream = stream.clone();
                    let observer = capture.observer;
                    let completed = RcBlock::new(move |_error: *mut NSError| {
                        std::hint::black_box((&*observer, &*retained_stream));
                    });
                    stream.stopCaptureWithCompletionHandler(Some(&completed));
                }
            }
        }
    });
}
pub fn pick(session: Arc<Session>) {
    if session.stopped.load(Ordering::Acquire) {
        return;
    }
    clear();
    let allocated = Observer::alloc().set_ivars(Ivars {
        session,
        preview_at: Mutex::new(Instant::now() - Duration::from_secs(1)),
    });
    let observer: Retained<Observer> = unsafe { msg_send![super(allocated), init] };
    let picker = unsafe { SCContentSharingPicker::sharedPicker() };
    let configuration = unsafe { SCContentSharingPickerConfiguration::new() };
    unsafe {
        let mut modes = SCContentSharingPickerMode::SingleWindow;
        if SCContentFilter::class()
            .instance_method(sel!(includedDisplays))
            .is_some()
            && CGPreflightScreenCaptureAccess()
        {
            modes |= SCContentSharingPickerMode::SingleDisplay;
        }
        configuration.setAllowedPickerModes(modes);
        configuration.setAllowsChangingSelectedContent(false);
        configuration.setExcludedBundleIDs(&NSArray::from_retained_slice(&[NSString::from_str(
            "team.tico.recorder.native",
        )]));
        picker.setDefaultConfiguration(&configuration);
        picker.addObserver(ProtocolObject::from_ref(&*observer));
    }
    CAPTURE.with(|c| {
        *c.borrow_mut() = Some(Capture {
            observer,
            stream: None,
        })
    });
    unsafe {
        picker.setActive(true);
        picker.present();
    }
}
