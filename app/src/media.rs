//! Origin checks for macOS media capture. Wry's permission callback exposes the device kind,
//! while WebKit's UI delegate also receives the origin of the requesting frame.

use block2::DynBlock;
use objc2::rc::Retained;
use objc2::runtime::{AnyObject, NSObject, NSObjectProtocol, ProtocolObject, Sel};
use objc2::{define_class, msg_send, DefinedClass, MainThreadOnly};
use objc2_web_kit::{
    WKFrameInfo, WKMediaCaptureType, WKPermissionDecision, WKSecurityOrigin, WKUIDelegate,
    WKWebView,
};
use tauri::webview::{PermissionKind, PermissionResponse, Webview};
use url::Url;

// WKWebView can expose getDisplayMedia even when its capture delegate prevents the screen picker.
pub(crate) const NO_SCREEN_CAPTURE_SCRIPT: &str = "window.ticoScreenCaptureUnavailable = true;";

pub(crate) fn default_permission(kind: PermissionKind) -> PermissionResponse {
    match kind {
        PermissionKind::Microphone | PermissionKind::Camera => PermissionResponse::Deny,
        _ => PermissionResponse::Default,
    }
}

struct DelegateState {
    hub: Url,
    original: Retained<ProtocolObject<dyn WKUIDelegate>>,
}

define_class!(
    #[unsafe(super(NSObject))]
    #[thread_kind = MainThreadOnly]
    #[ivars = DelegateState]
    struct CaptureDelegate;

    unsafe impl NSObjectProtocol for CaptureDelegate {
        #[unsafe(method(respondsToSelector:))]
        fn responds_to_selector(&self, selector: Sel) -> bool {
            let own: bool = unsafe { msg_send![super(self), respondsToSelector: selector] };
            own || self.ivars().original.respondsToSelector(selector)
        }
    }

    impl CaptureDelegate {
        // Wry still handles its other delegate callbacks, including new windows and the file picker.
        #[unsafe(method(forwardingTargetForSelector:))]
        fn forwarding_target(&self, _selector: Sel) -> &AnyObject {
            (*self.ivars().original).as_ref()
        }

        #[unsafe(method(webView:requestMediaCapturePermissionForOrigin:initiatedByFrame:type:decisionHandler:))]
        fn request_media_capture(
            &self,
            view: &WKWebView,
            origin: &WKSecurityOrigin,
            frame: &WKFrameInfo,
            kind: WKMediaCaptureType,
            reply: &DynBlock<dyn Fn(WKPermissionDecision)>,
        ) {
            let allow = kind == WKMediaCaptureType::Microphone
                && unsafe { frame.isMainFrame() }
                && unsafe { view.URL() }
                    .and_then(|url| url.absoluteString())
                    .and_then(|url| Url::parse(&url.to_string()).ok())
                    .is_some_and(|url| url.origin() == self.ivars().hub.origin())
                && crate::windows::matches_capture_origin(
                    &self.ivars().hub,
                    &unsafe { origin.protocol() }.to_string(),
                    &unsafe { origin.host() }.to_string(),
                    unsafe { origin.port() },
                );
            reply.call((if allow { WKPermissionDecision::Grant } else { WKPermissionDecision::Deny },));
        }
    }

    unsafe impl WKUIDelegate for CaptureDelegate {}
);

pub(crate) fn install(webview: &Webview, hub: Url) {
    static DELEGATE_KEY: u8 = 0;
    if let Err(e) = webview.with_webview(move |platform| unsafe {
        // Tauri lends the live WKWebView on the main thread. WebKit keeps its UI delegate weakly,
        // so the view retains the proxy for its own lifetime.
        let view = &*platform.inner().cast::<WKWebView>();
        let Some(original) = view.UIDelegate() else {
            return;
        };
        let delegate =
            CaptureDelegate::alloc(view.mtm()).set_ivars(DelegateState { hub, original });
        let delegate: Retained<CaptureDelegate> = msg_send![super(delegate), init];
        objc2::ffi::objc_setAssociatedObject(
            platform.inner().cast(),
            (&raw const DELEGATE_KEY).cast(),
            Retained::as_ptr(&delegate).cast_mut().cast(),
            objc2::ffi::OBJC_ASSOCIATION_RETAIN_NONATOMIC,
        );
        view.setUIDelegate(Some(ProtocolObject::from_ref(&*delegate)));
    }) {
        log::warn!("could not install the microphone permission handler: {e}");
    }
}
