//! Knowing a call has ended, so its recording stops by itself: once a call app has used the microphone during the
//! recording and then no call app has for `QUIET_FOR`, the call is over. A recording no call app ever used the mic
//! in (in person, a voice memo) never stops this way. Fed the apps on the mic (`capture::apps_on_mic`) every
//! `READ_EVERY`; nothing here touches Core Audio.
use std::{
    collections::BTreeSet,
    time::{Duration, Instant},
};

/// How often the apps on the mic are read during a recording.
pub const READ_EVERY: Duration = Duration::from_secs(2);
/// How long no call app may use the mic before the call counts as ended.
pub const QUIET_FOR: Duration = Duration::from_secs(15);

/// Apps whose mic use means a call: browsers (web calls) and call apps, by bundle id. An entry also matches the
/// ids under it, so `com.google.Chrome` covers `com.google.Chrome.helper`, where Chrome's audio runs. Dictation
/// apps (Willow) hold the mic all day, so only these count.
const CALL_APPS: &[&str] = &[
    // Browsers. Safari's capture runs in WebKit's own processes (com.apple.WebKit.GPU).
    "com.google.Chrome",
    "com.apple.Safari",
    "com.apple.WebKit",
    "company.thebrowser.Browser",
    "company.thebrowser.dia",
    "com.microsoft.edgemac",
    "com.brave.Browser",
    "org.mozilla.firefox",
    "org.mozilla.plugincontainer",
    "com.operasoftware.Opera",
    "com.vivaldi.Vivaldi",
    // Call apps. FaceTime's call audio runs in avconferenced.
    "us.zoom",
    "com.microsoft.teams",
    "com.microsoft.teams2",
    "com.tinyspeck.slackmacgap",
    "com.apple.FaceTime",
    "com.apple.avconferenced",
    "Cisco-Systems.Spark",
    "com.webex.meetingmanager",
    "com.cisco.webexmeetingsapp",
    "com.hnc.Discord",
    "co.teamport.around",
    "com.whereby",
    "app.tuple",
    "com.pop.pop",
];

/// Whether an app on the mic, by bundle id, is a call app.
pub fn is_call_app(bundle: &str) -> bool {
    CALL_APPS.iter().any(|app| {
        bundle
            .strip_prefix(app)
            .is_some_and(|rest| rest.is_empty() || rest.starts_with('.'))
    })
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Verdict {
    Continue,
    Stop,
}

/// What one read decided: the call apps on the mic for the first time in this recording, and whether to stop.
#[derive(Debug, PartialEq, Eq)]
pub struct Step {
    pub first_seen: Vec<String>,
    pub verdict: Verdict,
}

/// One recording's watch. A new recording starts a new one.
#[derive(Debug, Default)]
pub struct CallWatch {
    seen: BTreeSet<String>,
    /// The first read since a call app was last on the mic that had none.
    quiet_since: Option<Instant>,
}

impl CallWatch {
    /// Takes one read: the apps (bundle ids) on the mic at `now`.
    pub fn observe(&mut self, now: Instant, on_mic: &[String]) -> Step {
        let calls: Vec<&String> = on_mic.iter().filter(|app| is_call_app(app)).collect();
        let first_seen: Vec<String> = calls
            .iter()
            .filter(|app| self.seen.insert((**app).clone()))
            .map(|app| (*app).clone())
            .collect();
        let verdict = if !calls.is_empty() || self.seen.is_empty() {
            self.quiet_since = None;
            Verdict::Continue
        } else if now.duration_since(*self.quiet_since.get_or_insert(now)) >= QUIET_FOR {
            Verdict::Stop
        } else {
            Verdict::Continue
        };
        Step {
            first_seen,
            verdict,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const WILLOW: &str = "com.seewillow.WillowMac";

    /// Feeds `reads` (seconds after the start, apps on the mic) and answers each verdict.
    fn run(reads: &[(u64, &[&str])]) -> Vec<Verdict> {
        let start = Instant::now();
        let mut watch = CallWatch::default();
        reads
            .iter()
            .map(|(at, apps)| {
                let apps: Vec<String> = apps.iter().map(|app| app.to_string()).collect();
                watch
                    .observe(start + Duration::from_secs(*at), &apps)
                    .verdict
            })
            .collect()
    }

    #[test]
    fn call_apps_match_by_bundle_id_and_the_ids_under_it() {
        for app in [
            "com.google.Chrome",
            "com.google.Chrome.helper",
            "com.apple.WebKit.GPU",
            "us.zoom.xos",
            "com.microsoft.teams2",
            "com.tinyspeck.slackmacgap.helper",
        ] {
            assert!(is_call_app(app), "{app}");
        }
        for app in [
            WILLOW,
            "com.google.Chromebook",
            "us.zoomer",
            "",
            "com.apple.Music",
        ] {
            assert!(!is_call_app(app), "{app}");
        }
    }

    #[test]
    fn a_recording_no_call_app_used_the_mic_in_never_stops() {
        let reads: Vec<(u64, &[&str])> = (0..60).map(|at| (at * 2, &[][..])).collect();
        assert!(run(&reads).iter().all(|v| *v == Verdict::Continue));
    }

    #[test]
    fn a_call_app_gone_for_14_s_continues_and_for_15_s_stops() {
        let zoom: &[&str] = &["us.zoom.xos"];
        assert_eq!(
            run(&[(0, zoom), (2, &[]), (16, &[])]),
            [Verdict::Continue; 3]
        );
        assert_eq!(
            run(&[(0, zoom), (2, &[]), (17, &[])]),
            [Verdict::Continue, Verdict::Continue, Verdict::Stop]
        );
    }

    #[test]
    fn a_call_app_back_on_the_mic_starts_the_quiet_again() {
        let chrome: &[&str] = &["com.google.Chrome.helper"];
        assert_eq!(
            run(&[
                (0, chrome),
                (2, &[]),
                (12, &[]),
                (13, chrome),
                (14, &[]),
                (28, &[]),
                (29, &[])
            ]),
            [
                Verdict::Continue,
                Verdict::Continue,
                Verdict::Continue,
                Verdict::Continue,
                Verdict::Continue,
                Verdict::Continue,
                Verdict::Stop
            ]
        );
    }

    #[test]
    fn a_dictation_app_on_the_mic_is_not_a_call() {
        let willow: &[&str] = &[WILLOW];
        // Willow alone never starts a watch.
        assert!(
            run(&[(0, willow), (20, willow), (40, willow)])
                .iter()
                .all(|v| *v == Verdict::Continue)
        );
        // Willow staying on after the call does not keep it going.
        assert_eq!(
            run(&[(0, &["us.zoom.xos", WILLOW]), (2, willow), (17, willow)]),
            [Verdict::Continue, Verdict::Continue, Verdict::Stop]
        );
    }

    #[test]
    fn each_call_app_is_named_once() {
        let mut watch = CallWatch::default();
        let now = Instant::now();
        let apps =
            |names: &[&str]| -> Vec<String> { names.iter().map(|n| n.to_string()).collect() };
        assert_eq!(
            watch
                .observe(now, &apps(&[WILLOW, "us.zoom.xos"]))
                .first_seen,
            ["us.zoom.xos"]
        );
        assert_eq!(
            watch
                .observe(now, &apps(&["us.zoom.xos", "com.google.Chrome.helper"]))
                .first_seen,
            ["com.google.Chrome.helper"]
        );
        assert!(
            watch
                .observe(now, &apps(&["us.zoom.xos"]))
                .first_seen
                .is_empty()
        );
    }
}
