//! Public retry notices never expose a transport's internal error or request details.
use crate::store::Chunk;

pub fn transport(text: &str) -> bool {
    let text = text.to_lowercase();
    [
        "unexpected eof",
        "connection",
        "error sending request",
        "error reading a body",
        "request or response body error",
        "timed out",
        "timeout",
        "could not be reached",
        "secure connection failed",
        "closed the session",
        "reset without closing handshake",
    ]
    .iter()
    .any(|part| text.contains(part))
}

pub fn stored(error: &anyhow::Error, notice: &str) -> String {
    if error
        .chain()
        .any(|cause| cause.is::<reqwest::Error>() || cause.is::<std::io::Error>())
        || transport(&format!("{error:#}"))
    {
        notice.into()
    } else {
        error.to_string()
    }
}

pub fn notice(error: &str, failures: u32, message: &str) -> String {
    if transport(error) || error == message {
        if failures < 3 {
            String::new()
        } else {
            message.into()
        }
    } else {
        error.into()
    }
}

pub fn chunk_notice(chunk: &Chunk) -> String {
    notice(&chunk.error, chunk.attempts, "Transcription is retrying")
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn broken_body_is_quiet_then_one_human_notice_and_resets_on_success() {
        let raw = "request or response body error: error reading a body from connection: unexpected EOF during chunk size line";
        assert!(notice(raw, 1, "Transcription is retrying").is_empty());
        assert!(notice(raw, 2, "Transcription is retrying").is_empty());
        assert_eq!(
            notice(raw, 3, "Transcription is retrying"),
            "Transcription is retrying"
        );
        assert_eq!(
            stored(&anyhow::anyhow!(raw), "Transcription is retrying"),
            "Transcription is retrying"
        );
        assert!(notice("", 0, "Transcription is retrying").is_empty());
        assert_eq!(
            notice("Sign in again", 1, "Transcription is retrying"),
            "Sign in again"
        );
    }
}
