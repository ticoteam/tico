//! Idle load: once an hour the runner names the processes and Docker containers on its computer that are busy, and
//! posts the ones busy in two hourly samples in a row to `runners/idle-load`, for Check health
//! (backend/maintenance_health.py). Two samples an hour apart are not a turn that happened to be running. The model CLIs
//! that run turns are left out: they are busy on purpose.
//!
//! A sample holds names and numbers only: an executable's base name (letters, digits and `._-`), its CPU and its
//! memory. Never a command line, an argument or the environment. The post goes out every hour, even with nothing
//! busy: an empty list is how the server clears what it found before.
//!
//! The first sample comes a minute after start. The last sample's busy names are kept in memory, so a restart starts
//! the two-sample rule over. A server without the route answers 404 and is asked again at the next hour.
//!
//! Settings from the environment: `HEALTH_BUSY_CPU` (percent of one core, default 50) and `HEALTH_BUSY_MB` (resident
//! memory in MB, default 2048). A name is busy at or above either.

use std::collections::{HashMap, HashSet};
use std::io::Read;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, OnceLock};
use std::time::{Duration, Instant};

use regex::Regex;
use serde_json::{json, Value};

use crate::runner::{describe, Runner};
use crate::util::{epoch_s, iso, log, text_of};

/// The time between samples; the first one comes a minute after start.
pub const EVERY: Duration = Duration::from_secs(3600);
const FIRST: Duration = Duration::from_secs(60);
/// Findings posted at most, the highest CPU first.
const MAX_BUSY: usize = 50;
/// Characters a name keeps.
const NAME_MAX: usize = 40;
const PS_LIMIT: Duration = Duration::from_secs(20);
const DOCKER_LIMIT: Duration = Duration::from_secs(40);

/// Each name to its busiest reading: (CPU percent of one core, resident MB).
pub type Sample = HashMap<String, (f64, f64)>;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Thresholds {
    pub cpu: f64,
    pub mb: f64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Finding {
    pub name: String,
    pub cpu: f64,
    pub mb: f64,
}

/// Set while a server refuses the route; cleared when one takes it. Only the first refusal in a row is logged.
static REFUSED: AtomicBool = AtomicBool::new(false);

/// Samples every hour and posts the names that two samples in a row found busy. It runs on its own timer, never in
/// the heartbeat loop, and a failure here is logged and forgotten until the next hour.
pub async fn run_loop(runner: Arc<Runner>) {
    let mut ticker = tokio::time::interval_at(tokio::time::Instant::now() + FIRST, EVERY);
    ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    let mut hourly = Hourly::default();
    while !runner.stopping() {
        ticker.tick().await;
        if runner.stopping() {
            break;
        }
        let t = thresholds();
        let Ok(sample) = tokio::task::spawn_blocking(take_sample).await else {
            continue;
        };
        let found = hourly.findings(&sample, t);
        let at = text_of(&iso(&json!(epoch_s().floor())));
        match runner
            .client
            .post("runners/idle-load", &body(&at, &found), None)
            .await
        {
            Ok(_) => REFUSED.store(false, Ordering::SeqCst),
            Err(e) if not_taken(e.status) => {
                if !REFUSED.swap(true, Ordering::SeqCst) {
                    log("Tico runner: this server does not take idle load; it is asked again at the next hour");
                }
            }
            Err(e) => log(&format!(
                "Tico runner: idle load not sent: {}",
                describe(&e)
            )),
        }
    }
}

/// A server answers 404 for a route it does not have. Other 4xx are the same answer, except the ones about sign-in,
/// a timeout or a rate limit, which say nothing about the route.
fn not_taken(status: u16) -> bool {
    (400..500).contains(&status) && !matches!(status, 401 | 403 | 407 | 408 | 429)
}

/// The settings from the environment, read at each sample.
pub fn thresholds() -> Thresholds {
    Thresholds {
        cpu: env_number("HEALTH_BUSY_CPU", 50.0),
        mb: env_number("HEALTH_BUSY_MB", 2048.0),
    }
}

fn env_number(name: &str, default: f64) -> f64 {
    std::env::var(name)
        .ok()
        .and_then(|v| v.trim().parse::<f64>().ok())
        .filter(|v| v.is_finite())
        .unwrap_or(default)
}

/// A name as a finding carries it: the base name, its letters, digits and `._-`, at most 40 characters, or `unknown`.
pub fn clean(name: &str) -> String {
    let base = name.rsplit('/').next().unwrap_or("");
    let kept: String = base
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
        .take(NAME_MAX)
        .collect();
    if kept.is_empty() {
        "unknown".into()
    } else {
        kept
    }
}

/// One sample of the computer: its processes, and its containers when Docker is installed here. A turn's own model CLI
/// is busy on purpose, so the harness programs are left out.
fn take_sample() -> Sample {
    let ps = output_within("ps", &["-axo", "pcpu=,rss=,comm="], PS_LIMIT).unwrap_or_default();
    let mut sample = parse_ps(&ps, &crate::harness_tools::turn_executables());
    if crate::env::which("docker", &std::env::var("PATH").unwrap_or_default()).is_some() {
        let format = "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}";
        if let Some(out) = output_within(
            "docker",
            &["stats", "--no-stream", "--format", format],
            DOCKER_LIMIT,
        ) {
            sample.extend(parse_docker(&out));
        }
    }
    sample
}

/// A command's stdout, or None when it cannot start or runs past `limit` (then it is killed). The exit status is not
/// read: a command that printed what it had counts. The pipe is drained while the command runs, so a long listing
/// cannot stall it.
fn output_within(program: &str, args: &[&str], limit: Duration) -> Option<String> {
    let mut child = Command::new(program)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let mut out = child.stdout.take()?;
    let reader = std::thread::spawn(move || {
        let mut bytes = Vec::new();
        let _ = out.read_to_end(&mut bytes);
        bytes
    });
    let deadline = Instant::now() + limit;
    loop {
        match child.try_wait() {
            Ok(Some(_)) => {
                return reader
                    .join()
                    .ok()
                    .map(|b| String::from_utf8_lossy(&b).into_owned())
            }
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(20)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    }
}

/// `ps -axo pcpu=,rss=,comm=` as a sample: the busiest reading of each process name, the `turns` programs left out.
/// The rss is in KB, as `ps` prints it.
pub fn parse_ps(out: &str, turns: &[String]) -> Sample {
    let mut seen = Sample::new();
    for line in out.lines() {
        let Some((cpu, rss, comm)) = ps_fields(line) else {
            continue;
        };
        let (Ok(cpu), Ok(rss)) = (cpu.parse::<f64>(), rss.parse::<u64>()) else {
            continue;
        };
        if !cpu.is_finite() {
            continue;
        }
        let name = clean(comm);
        if turns.contains(&name) {
            continue;
        }
        let mb = rss as f64 / 1024.0;
        let entry = seen.entry(format!("process:{name}")).or_insert((0.0, 0.0));
        entry.0 = entry.0.max(cpu);
        entry.1 = entry.1.max(mb);
    }
    seen
}

/// The first two fields of a `ps` line, then the rest of the line: a command name may hold spaces.
fn ps_fields(line: &str) -> Option<(&str, &str, &str)> {
    let (first, rest) = next_word(line)?;
    let (second, rest) = next_word(rest)?;
    let third = rest.trim_start();
    (!third.is_empty()).then_some((first, second, third))
}

fn next_word(text: &str) -> Option<(&str, &str)> {
    let text = text.trim_start();
    if text.is_empty() {
        return None;
    }
    let end = text.find(char::is_whitespace).unwrap_or(text.len());
    Some((&text[..end], &text[end..]))
}

fn memory_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^([\d.]+)\s*([A-Za-z]+)").expect("memory pattern"))
}

/// `docker stats --no-stream --format "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"` as a sample: one reading per container,
/// its memory the used side of `MemUsage` in MB. A row that does not read is skipped.
pub fn parse_docker(out: &str) -> Sample {
    let mut seen = Sample::new();
    for line in out.lines() {
        let parts: Vec<&str> = line.split('\t').collect();
        if parts.len() < 3 {
            continue;
        }
        let Ok(cpu) = parts[1].trim().trim_end_matches('%').trim().parse::<f64>() else {
            continue;
        };
        let used = parts[2].split('/').next().unwrap_or("").trim();
        let mb = match memory_re().captures(used) {
            Some(c) => {
                let Ok(amount) = c[1].parse::<f64>() else {
                    continue;
                };
                amount * unit_mb(&c[2])
            }
            None => 0.0,
        };
        seen.insert(format!("container:{}", clean(parts[0])), (cpu, mb));
    }
    seen
}

fn unit_mb(unit: &str) -> f64 {
    match unit.to_ascii_lowercase().as_str() {
        "kib" => 1.0 / 1024.0,
        "kb" => 1.0 / 1000.0,
        "mib" | "mb" => 1.0,
        "gib" => 1024.0,
        "gb" => 1000.0,
        "b" => 1.0 / 1048576.0,
        _ => 1.0,
    }
}

/// The names busy in one sample: CPU at or above its threshold, or memory at or above its threshold.
pub fn over_threshold(sample: &Sample, t: Thresholds) -> Vec<Finding> {
    sample
        .iter()
        .filter(|(_, (cpu, mb))| *cpu >= t.cpu || *mb >= t.mb)
        .map(|(name, (cpu, mb))| Finding {
            name: name.clone(),
            cpu: *cpu,
            mb: *mb,
        })
        .collect()
}

/// The busy names of the last hourly sample. A name is a finding when it is busy in this sample and in the one before.
#[derive(Default)]
pub struct Hourly {
    last: HashSet<String>,
}

impl Hourly {
    /// This hour's findings: the names busy in both samples, with this sample's numbers, highest CPU first and at most
    /// `MAX_BUSY`. This sample becomes the one before whether or not the post goes out.
    pub fn findings(&mut self, sample: &Sample, t: Thresholds) -> Vec<Finding> {
        let previous = std::mem::take(&mut self.last);
        let now = over_threshold(sample, t);
        self.last = now.iter().map(|f| f.name.clone()).collect();
        let mut both: Vec<Finding> = now
            .into_iter()
            .filter(|f| previous.contains(&f.name))
            .collect();
        both.sort_by(|a, b| {
            b.cpu
                .total_cmp(&a.cpu)
                .then(b.mb.total_cmp(&a.mb))
                .then(a.name.cmp(&b.name))
        });
        both.truncate(MAX_BUSY);
        both
    }
}

fn round1(x: f64) -> f64 {
    (x * 10.0).round() / 10.0
}

/// The post: when the sample was taken (RFC 3339, UTC) and its findings.
pub fn body(sampled_at: &str, found: &[Finding]) -> Value {
    let busy: Vec<Value> = found
        .iter()
        .map(|f| json!({"name": f.name, "cpu": round1(f.cpu), "mb": round1(f.mb)}))
        .collect();
    json!({"sampled_at": sampled_at, "busy": busy})
}

#[cfg(test)]
mod tests {
    use super::*;

    const T: Thresholds = Thresholds {
        cpu: 50.0,
        mb: 2048.0,
    };

    fn sample(rows: &[(&str, f64, f64)]) -> Sample {
        rows.iter()
            .map(|(name, cpu, mb)| (name.to_string(), (*cpu, *mb)))
            .collect()
    }

    #[test]
    fn names_are_base_names_without_odd_characters() {
        assert_eq!(clean("/usr/bin/python3"), "python3");
        assert_eq!(
            clean("Google Chrome Helper (Renderer)"),
            "GoogleChromeHelperRenderer"
        );
        assert_eq!(
            clean("/Applications/Foo.app/Contents/MacOS/Foo.v2_beta-1"),
            "Foo.v2_beta-1"
        );
        assert_eq!(clean("é"), "unknown");
        assert_eq!(clean("/usr/bin/"), "unknown");
        assert_eq!(clean(&"a".repeat(60)).len(), 40);
    }

    #[test]
    fn busy_is_at_or_above_either_threshold() {
        let s = sample(&[
            ("process:cpu", 50.0, 1.0),
            ("process:mem", 0.0, 2048.0),
            ("process:quiet", 49.9, 2047.9),
        ]);
        let mut names: Vec<String> = over_threshold(&s, T).into_iter().map(|f| f.name).collect();
        names.sort();
        assert_eq!(names, ["process:cpu", "process:mem"]);
    }

    #[test]
    fn ps_output_keeps_the_busiest_reading_per_name_and_skips_turns() {
        let out = "  0.0    1024 /usr/sbin/cron\n 72.5 3145728 /Users/x/bin/python3\n 10.0 2048 /usr/bin/python3\n  1.0  100 /usr/local/bin/claude\n 80.0 2048 Google Chrome Helper\nnot a line\n  x 1 y\n";
        let turns = vec!["claude".to_string()];
        let s = parse_ps(out, &turns);
        assert_eq!(s["process:python3"], (72.5, 3072.0));
        assert_eq!(s["process:GoogleChromeHelper"], (80.0, 2.0));
        assert_eq!(s["process:cron"], (0.0, 1.0));
        assert!(!s.contains_key("process:claude"));
        assert_eq!(s.len(), 3);
    }

    #[test]
    fn docker_rows_give_name_cpu_and_used_memory_in_mb() {
        let out = "web\t12.5%\t1.5GiB / 7.7GiB\ndb\t0.5%\t512MiB / 2GiB\nodd\tx%\t1MiB / 2GiB\nshort\t1%\n";
        let s = parse_docker(out);
        assert_eq!(s["container:web"], (12.5, 1536.0));
        assert_eq!(s["container:db"], (0.5, 512.0));
        assert!(!s.contains_key("container:odd"));
        assert!(!s.contains_key("container:short"));
    }

    #[test]
    fn a_name_must_be_busy_in_two_samples_in_a_row() {
        let mut hourly = Hourly::default();
        let first = sample(&[("process:a", 60.0, 1.0), ("process:b", 1.0, 1.0)]);
        assert!(hourly.findings(&first, T).is_empty());
        let second = sample(&[("process:a", 70.0, 1.0), ("process:c", 90.0, 1.0)]);
        let found = hourly.findings(&second, T);
        assert_eq!(
            found.iter().map(|f| f.name.as_str()).collect::<Vec<_>>(),
            ["process:a"]
        );
        assert_eq!(found[0].cpu, 70.0);
        // Busy, quiet, busy: never two in a row.
        assert!(hourly
            .findings(&sample(&[("process:a", 1.0, 1.0)]), T)
            .is_empty());
        assert!(hourly
            .findings(&sample(&[("process:a", 80.0, 1.0)]), T)
            .is_empty());
        // Nothing busy is still a sample, so the next hour starts from empty.
        assert!(hourly.findings(&sample(&[]), T).is_empty());
    }

    #[test]
    fn at_most_fifty_findings_and_the_highest_cpu_first() {
        let s: Sample = (0..60)
            .map(|i| (format!("process:p{i:02}"), (50.0 + i as f64, 1.0)))
            .collect();
        let mut hourly = Hourly::default();
        hourly.findings(&s, T);
        let found = hourly.findings(&s, T);
        assert_eq!(found.len(), 50);
        assert_eq!(found[0].name, "process:p59");
        assert_eq!(found[49].name, "process:p10");
    }

    #[test]
    fn the_post_names_the_sample_time_and_each_finding() {
        let found = [Finding {
            name: "process:python3".into(),
            cpu: 72.46,
            mb: 3000.0,
        }];
        assert_eq!(
            body("2026-11-04T10:00:00+00:00", &found),
            json!({"sampled_at": "2026-11-04T10:00:00+00:00", "busy": [{"name": "process:python3", "cpu": 72.5, "mb": 3000.0}]})
        );
        assert_eq!(body("t", &[]), json!({"sampled_at": "t", "busy": []}));
    }
}
