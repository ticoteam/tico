//! A memory and CPU ceiling for each harness process on a Linux cloud machine (docs/cloud-machines.md), so one runaway
//! turn is killed on its own while the runner and the other turns go on.
//!
//! The runner is a systemd user service there (runner_support/systemd_units.py), and the user's manager has the
//! memory and cpu controllers delegated (Ubuntu 24.04, systemd 255, cgroup v2). Each harness starts as
//! `tico-runner --turn-scope ... -- <harness>`: that process asks the user's manager for a transient scope holding
//! itself (`StartTransientUnit`, through busctl) with MemoryMax, MemorySwapMax=0 and CPUQuota, raises its own
//! oom_score_adj so the kernel picks a turn before the runner, and then execs the harness, which keeps its pid, its
//! process group and its pipes. The scope is bound to the runner's unit, so a runner restart still ends its turns, as
//! it did while they shared its cgroup. No MemoryHigh: between High and Max the kernel throttles instead of killing,
//! and a runaway turn would crawl rather than end.
//!
//! When the scope is OOM-killed, systemd stops the whole scope (OOMPolicy=stop) and keeps the unit failed with
//! Result=oom-kill. The turn's attempt id names its scopes (`SCOPE_ENV`), so the drive loop can tell its failed turn
//! was the limit (`oom_failure`) and say so.
//!
//! Anywhere else (a Mac, a Docker runner, a Linux computer that is not a cloud machine, or one whose scope probe
//! fails) turns run exactly as before. `TICO_TURN_LIMITS=0` turns this off, `=1` turns it on for a non-cloud Linux
//! runner under systemd; `TICO_TURN_MEMORY_MAX` (bytes, K/M/G suffix, or a percentage of RAM) and
//! `TICO_TURN_CPU_QUOTA` (systemd's CPUQuota form, e.g. `600%`; `0` for none) override the defaults.

use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

/// `tico-runner --turn-scope <busctl> <memory max> <cpu usec per s> <runner unit> <bus address> -- <argv>`.
pub const FLAG: &str = "--turn-scope";
/// The scope name stem a turn's harness gets, set in its environment by the runner and removed before the exec.
pub const SCOPE_ENV: &str = "TICO_TURN_SCOPE";
const STEM: &str = "tico-turn";
/// Above the 200 a user manager's services start with: the kernel's OOM killer takes a turn before the runner.
const OOM_SCORE_ADJ: i32 = 500;
const GIB: u64 = 1 << 30;
/// The failure category a turn stopped at its memory limit carries.
pub const CATEGORY: &str = "turn_memory_limit";

#[derive(Clone, Debug, PartialEq)]
pub struct Limits {
    pub memory_max: u64,
    /// CPU time per second of wall time, in microseconds (systemd's CPUQuotaPerSecUSec); None for no quota.
    pub cpu_usec: Option<u64>,
}

/// One turn may use most of the machine but never all of it: max(4 GiB, 60% of RAM), but at most 90% of RAM so the
/// runner keeps room on a small machine, and 75% of the cores.
pub fn defaults(ram: u64, cores: u64) -> Limits {
    let memory_max = (4 * GIB).max(ram / 10 * 6).min(ram / 10 * 9);
    Limits {
        memory_max,
        cpu_usec: Some(cores.max(1) * 750_000),
    }
}

/// The limits after the ops overrides, or why an override cannot be read.
pub fn limits_from(
    get: impl Fn(&str) -> Option<String>,
    ram: u64,
    cores: u64,
) -> Result<Limits, String> {
    let mut limits = defaults(ram, cores);
    if let Some(v) = get("TICO_TURN_MEMORY_MAX").filter(|v| !v.trim().is_empty()) {
        limits.memory_max = parse_bytes(&v, ram)
            .filter(|&b| b >= 256 << 20)
            .ok_or_else(|| format!("TICO_TURN_MEMORY_MAX={v} is not a size of at least 256M"))?;
    }
    if let Some(v) = get("TICO_TURN_CPU_QUOTA").filter(|v| !v.trim().is_empty()) {
        limits.cpu_usec = parse_cpu(&v)
            .ok_or_else(|| format!("TICO_TURN_CPU_QUOTA={v} is not a percentage such as 600%"))?;
    }
    Ok(limits)
}

/// `8G`, `8192M`, `60%` (of `ram`) or plain bytes.
fn parse_bytes(text: &str, ram: u64) -> Option<u64> {
    let t = text.trim();
    if let Some(p) = t.strip_suffix('%') {
        let p: f64 = p.trim().parse().ok()?;
        return (p > 0.0 && p <= 100.0).then(|| (ram as f64 * p / 100.0) as u64);
    }
    let (digits, unit) = match t
        .char_indices()
        .find(|(_, c)| !c.is_ascii_digit() && *c != '.')
    {
        Some((i, _)) => t.split_at(i),
        None => (t, ""),
    };
    let n: f64 = digits.parse().ok()?;
    let scale = match unit.trim().to_ascii_uppercase().trim_end_matches('B') {
        "" => 1u64,
        "K" => 1 << 10,
        "M" => 1 << 20,
        "G" => GIB,
        "T" => 1 << 40,
        _ => return None,
    };
    Some((n * scale as f64) as u64)
}

/// `600%` -> six CPUs' worth; `0` -> no quota.
fn parse_cpu(text: &str) -> Option<Option<u64>> {
    let t = text.trim();
    if t == "0" || t.eq_ignore_ascii_case("off") {
        return Some(None);
    }
    let p: f64 = t.strip_suffix('%')?.trim().parse().ok()?;
    (p > 0.0).then(|| Some((p * 10_000.0) as u64))
}

/// A unit name stem from an attempt id: letters and digits only, bounded.
pub fn scope_key(aid: &str) -> String {
    let clean: String = aid
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .take(40)
        .collect();
    format!("{STEM}-{clean}")
}

/// The scope a harness process gets: its stem (or the bare stem) and its pid, which is the harness's own.
fn scope_name(stem: Option<&str>, pid: u32) -> String {
    let stem = stem
        .filter(|s| s.starts_with(STEM) && s.chars().all(|c| c.is_ascii_alphanumeric() || c == '-'))
        .unwrap_or(STEM);
    format!("{stem}-{pid}.scope")
}

/// The busctl arguments that start `unit` with `pid` in it, bound to the runner's `runner_unit`.
fn start_args(
    address: &str,
    unit: &str,
    pid: u32,
    limits: &Limits,
    runner_unit: &str,
) -> Vec<String> {
    let mut props: Vec<Vec<String>> = vec![
        vec!["PIDs".into(), "au".into(), "1".into(), pid.to_string()],
        vec![
            "MemoryMax".into(),
            "t".into(),
            limits.memory_max.to_string(),
        ],
        vec!["MemorySwapMax".into(), "t".into(), "0".into()],
        vec!["OOMPolicy".into(), "s".into(), "stop".into()],
        vec![
            "BindsTo".into(),
            "as".into(),
            "1".into(),
            runner_unit.into(),
        ],
        vec!["Description".into(), "s".into(), "Tico turn".into()],
    ];
    if let Some(cpu) = limits.cpu_usec {
        props.push(vec![
            "CPUQuotaPerSecUSec".into(),
            "t".into(),
            cpu.to_string(),
        ]);
    }
    let mut args: Vec<String> = [
        &format!("--address={address}"),
        "--timeout=5",
        "call",
        "org.freedesktop.systemd1",
        "/org/freedesktop/systemd1",
        "org.freedesktop.systemd1.Manager",
        "StartTransientUnit",
        "ssa(sv)a(sa(sv))",
        unit,
        "fail",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    args.push(props.len().to_string());
    args.extend(props.into_iter().flatten());
    args.push("0".into());
    args
}

/// Whether `/proc/<pid>/cgroup` text puts the process in `unit`.
fn in_unit(cgroup: &str, unit: &str) -> bool {
    cgroup.lines().any(|l| {
        l.strip_prefix("0::")
            .is_some_and(|p| p.trim_end().ends_with(&format!("/{unit}")))
    })
}

/// The `oom_kill` count of a cgroup's memory.events.
fn oom_kills(events: &str) -> u64 {
    events
        .lines()
        .find_map(|l| l.strip_prefix("oom_kill "))
        .and_then(|n| n.trim().parse().ok())
        .unwrap_or(0)
}

/// What the bot and the run's history read when its turn hit the limit.
pub fn oom_message(memory_max: u64) -> String {
    format!(
        "This turn used more than {:.1} GB of memory and was stopped: that is the most one turn may use on this \
         computer. Run fewer or smaller processes at once (for example, fewer test workers).",
        memory_max as f64 / GIB as f64
    )
}

/// The harness side (`FLAG`): enter the scope, then exec the harness. A scope that cannot be made leaves the turn
/// running without limits, never failed.
pub fn main(args: &[String]) -> i32 {
    let (busctl, memory, cpu, runner_unit, address, argv) = match args {
        [busctl, memory, cpu, unit, address, dashes, argv @ ..]
            if dashes == "--" && !argv.is_empty() =>
        {
            (busctl, memory, cpu, unit, address, argv)
        }
        _ => {
            eprintln!("usage: tico-runner {FLAG} <busctl> <memory max> <cpu usec> <runner unit> <bus> -- <command> [args...]");
            return 2;
        }
    };
    let stem = std::env::var(SCOPE_ENV).ok();
    std::env::remove_var(SCOPE_ENV);
    let pid = std::process::id();
    let unit = scope_name(stem.as_deref(), pid);
    let limits = Limits {
        memory_max: memory.parse().unwrap_or(0),
        cpu_usec: cpu.parse().ok().filter(|&c: &u64| c > 0),
    };
    if limits.memory_max > 0 {
        if let Err(why) = enter(Path::new(busctl), address, &unit, pid, &limits, runner_unit) {
            eprintln!("tico-runner: {CATEGORY}: this turn runs without its limits ({unit}): {why}");
        }
    }
    let _ = std::fs::write("/proc/self/oom_score_adj", OOM_SCORE_ADJ.to_string());
    use std::os::unix::process::CommandExt;
    let err = std::process::Command::new(&argv[0]).args(&argv[1..]).exec();
    eprintln!("tico-runner {FLAG}: {}: {err}", argv[0]);
    127
}

fn enter(
    busctl: &Path,
    address: &str,
    unit: &str,
    pid: u32,
    limits: &Limits,
    runner_unit: &str,
) -> Result<(), String> {
    let out = std::process::Command::new(busctl)
        .args(start_args(address, unit, pid, limits, runner_unit))
        .stdin(std::process::Stdio::null())
        .output()
        .map_err(|e| format!("busctl: {e}"))?;
    if !out.status.success() {
        let why = String::from_utf8_lossy(&out.stderr);
        return Err(format!(
            "busctl exited {}: {}",
            out.status.code().unwrap_or(-1),
            why.trim().chars().take(200).collect::<String>()
        ));
    }
    // The start job moves this process a moment after the call returns; anything it starts before then would stay
    // outside the scope.
    for _ in 0..200 {
        if std::fs::read_to_string("/proc/self/cgroup").is_ok_and(|c| in_unit(&c, unit)) {
            return Ok(());
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    Err("the scope did not take this process within 2 s".into())
}

/// What the runner needs to wrap a harness: found once, None where limits do not apply.
pub struct Setup {
    pub limits: Limits,
    runner_unit: String,
    busctl: PathBuf,
    bus: crate::env::Env,
    /// The cgroup directory the turn scopes are made in (the runner's own slice).
    slice: PathBuf,
}

fn setup() -> Option<&'static Setup> {
    static SETUP: OnceLock<Option<Setup>> = OnceLock::new();
    SETUP
        .get_or_init(|| match detect() {
            Ok(s) => {
                crate::util::log(&format!(
                    "Tico runner: turn limits on: {:.1} GB of memory and {} per turn",
                    s.limits.memory_max as f64 / GIB as f64,
                    s.limits
                        .cpu_usec
                        .map_or("no CPU quota".into(), |c| format!("{}% CPU", c / 10_000)),
                ));
                Some(s)
            }
            Err(Some(why)) => {
                crate::util::log(&format!(
                    "Tico runner: turn_limits_unavailable: turns run without memory or CPU limits: {why}"
                ));
                None
            }
            Err(None) => None,
        })
        .as_ref()
}

/// Whether this runner wraps its harnesses in limited scopes.
pub fn active() -> bool {
    setup().is_some()
}

/// The command and arguments a harness runs under, or None to run it as before.
pub fn prefix() -> Option<Vec<OsString>> {
    let s = setup()?;
    let exe = std::env::current_exe().ok()?;
    Some(wrapper_argv(exe.into_os_string(), s))
}

fn wrapper_argv(exe: OsString, s: &Setup) -> Vec<OsString> {
    vec![
        exe,
        FLAG.into(),
        s.busctl.clone().into_os_string(),
        s.limits.memory_max.to_string().into(),
        s.limits.cpu_usec.unwrap_or(0).to_string().into(),
        s.runner_unit.clone().into(),
        s.bus
            .get("DBUS_SESSION_BUS_ADDRESS")
            .cloned()
            .unwrap_or_default()
            .into(),
        "--".into(),
    ]
}

/// Err(None): limits do not apply here, nothing to say. Err(Some(why)): they should, and cannot.
fn detect() -> Result<Setup, Option<String>> {
    if !cfg!(target_os = "linux") {
        return Err(None);
    }
    let switch = std::env::var("TICO_TURN_LIMITS")
        .unwrap_or_default()
        .to_ascii_lowercase();
    if matches!(switch.as_str(), "0" | "off" | "false" | "no") {
        return Err(None);
    }
    let forced = matches!(switch.as_str(), "1" | "on" | "true" | "yes");
    if !forced && !crate::cloud_machine::detected() {
        return Err(None);
    }
    let runner_unit = std::env::var("TICO_SYSTEMD_UNIT").unwrap_or_default();
    if !runner_unit.ends_with(".service") || runner_unit.contains('/') {
        return Err(Some(
            "the runner is not a systemd service (TICO_SYSTEMD_UNIT)".into(),
        ));
    }
    let own = std::fs::read_to_string("/proc/self/cgroup").unwrap_or_default();
    if !in_unit(&own, &runner_unit) {
        return Err(Some(format!(
            "the runner is not in {runner_unit}'s cgroup (cgroup v2)"
        )));
    }
    let path = own
        .lines()
        .find_map(|l| l.strip_prefix("0::"))
        .unwrap_or_default()
        .trim();
    let slice = Path::new("/sys/fs/cgroup").join(
        Path::new(path.trim_start_matches('/'))
            .parent()
            .unwrap_or(Path::new("")),
    );
    let controllers = std::fs::read_to_string(slice.join("cgroup.controllers")).unwrap_or_default();
    if !controllers.split_whitespace().any(|c| c == "memory") {
        return Err(Some(format!(
            "the memory controller is not delegated to {} ({:?})",
            slice.display(),
            controllers.trim()
        )));
    }
    let busctl = ["/usr/bin/busctl", "/bin/busctl"]
        .iter()
        .map(PathBuf::from)
        .find(|p| p.is_file())
        .ok_or_else(|| Some("busctl is not installed".to_string()))?;
    let ram = std::fs::read_to_string("/proc/meminfo")
        .ok()
        .and_then(|t| {
            t.lines()
                .find_map(|l| l.strip_prefix("MemTotal:"))
                .and_then(|r| r.split_whitespace().next()?.parse::<u64>().ok())
        })
        .map(|kb| kb * 1024)
        .ok_or_else(|| Some("cannot read MemTotal".to_string()))?;
    let cores = std::thread::available_parallelism().map_or(1, |n| n.get() as u64);
    let limits = limits_from(|k| std::env::var(k).ok(), ram, cores).map_err(Some)?;
    // SAFETY: geteuid has no preconditions.
    let bus = crate::follow::user_bus_env(&std::env::vars().collect(), unsafe { libc::geteuid() });
    let s = Setup {
        limits,
        runner_unit,
        busctl,
        bus,
        slice,
    };
    probe(&s).map_err(Some)?;
    // Turn scopes a runner before this one left failed.
    let _ = systemctl(&s, &["reset-failed", &format!("{STEM}-*.scope")]);
    Ok(s)
}

/// One real wrap of a command that prints its cgroup: the whole path works, or turns run as before.
fn probe(s: &Setup) -> Result<(), String> {
    let exe =
        std::env::current_exe().map_err(|e| format!("cannot find this runner's binary: {e}"))?;
    let argv = wrapper_argv(exe.into_os_string(), s);
    let out = std::process::Command::new(&argv[0])
        .args(&argv[1..])
        .args(["/bin/cat", "/proc/self/cgroup"])
        .env(SCOPE_ENV, format!("{STEM}-probe"))
        .stdin(std::process::Stdio::null())
        .output()
        .map_err(|e| format!("the scope probe did not start: {e}"))?;
    let cgroup = String::from_utf8_lossy(&out.stdout);
    if out.status.success() && cgroup.contains(&format!("/{STEM}-probe-")) {
        return Ok(());
    }
    let why = String::from_utf8_lossy(&out.stderr);
    Err(format!(
        "the scope probe failed: {}",
        why.trim().chars().take(300).collect::<String>()
    ))
}

fn systemctl(s: &Setup, args: &[&str]) -> Option<String> {
    let out = std::process::Command::new("systemctl")
        .arg("--user")
        .args(args)
        .envs(&s.bus)
        .stdin(std::process::Stdio::null())
        .output()
        .ok()?;
    out.status
        .success()
        .then(|| String::from_utf8_lossy(&out.stdout).into_owned())
}

/// The failure text for attempt `aid` when one of its harness scopes was OOM-killed at its limit, else None. Reads
/// a live scope's memory.events, then the failed units systemd keeps (and clears them).
pub async fn oom_failure(aid: &str) -> Option<String> {
    let s = setup()?;
    let stem = format!("{}-", scope_key(aid));
    let live = std::fs::read_dir(&s.slice).ok().is_some_and(|dir| {
        dir.flatten().any(|e| {
            let name = e.file_name().to_string_lossy().into_owned();
            name.starts_with(&stem)
                && name.ends_with(".scope")
                && std::fs::read_to_string(e.path().join("memory.events"))
                    .is_ok_and(|t| oom_kills(&t) > 0)
        })
    });
    let pattern = format!("{stem}*.scope");
    let failed = tokio::task::spawn_blocking(move || {
        let listed = systemctl(
            s,
            &[
                "list-units",
                "--all",
                "--plain",
                "--no-legend",
                "--state=failed",
                &pattern,
            ],
        )?;
        let units: Vec<String> = listed
            .lines()
            .filter_map(|l| l.split_whitespace().next())
            .filter(|u| u.starts_with(STEM))
            .map(String::from)
            .collect();
        let oom = units.iter().any(|u| {
            systemctl(s, &["show", "--property=Result", "--value", u])
                .is_some_and(|r| r.trim() == "oom-kill")
        });
        for u in &units {
            let _ = systemctl(s, &["reset-failed", u]);
        }
        Some(oom)
    })
    .await
    .ok()
    .flatten()
    .unwrap_or(false);
    (live || failed).then(|| oom_message(s.limits.memory_max))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_turn_gets_most_of_the_machine_but_never_all_of_it() {
        // The 8 CPU / 16 GB machine a Builder's runner died on: 9.6 GB and six CPUs per turn.
        let large = defaults(16 * GIB, 8);
        assert_eq!(large.memory_max, 16 * GIB / 10 * 6);
        assert_eq!(large.cpu_usec, Some(6_000_000));
        // Small machines keep at least 4 GiB per turn, but never more than 90% of RAM.
        assert_eq!(defaults(6 * GIB, 2).memory_max, 4 * GIB);
        assert_eq!(defaults(4 * GIB, 2).memory_max, 4 * GIB / 10 * 9);
        assert_eq!(defaults(4 * GIB, 0).cpu_usec, Some(750_000));
    }

    #[test]
    fn ops_override_the_limits_and_a_bad_value_says_so() {
        let env = |pairs: &'static [(&'static str, &'static str)]| {
            move |k: &str| {
                pairs
                    .iter()
                    .find(|(n, _)| *n == k)
                    .map(|(_, v)| v.to_string())
            }
        };
        let ram = 16 * GIB;
        assert_eq!(limits_from(env(&[]), ram, 8).unwrap(), defaults(ram, 8));
        let l = limits_from(
            env(&[
                ("TICO_TURN_MEMORY_MAX", "12G"),
                ("TICO_TURN_CPU_QUOTA", "400%"),
            ]),
            ram,
            8,
        )
        .unwrap();
        assert_eq!((l.memory_max, l.cpu_usec), (12 * GIB, Some(4_000_000)));
        assert_eq!(
            limits_from(env(&[("TICO_TURN_MEMORY_MAX", "50%")]), ram, 8)
                .unwrap()
                .memory_max,
            8 * GIB
        );
        assert_eq!(
            limits_from(env(&[("TICO_TURN_MEMORY_MAX", "6144M")]), ram, 8)
                .unwrap()
                .memory_max,
            6 * GIB
        );
        assert_eq!(
            limits_from(env(&[("TICO_TURN_CPU_QUOTA", "0")]), ram, 8)
                .unwrap()
                .cpu_usec,
            None
        );
        for bad in [
            ("TICO_TURN_MEMORY_MAX", "lots"),
            ("TICO_TURN_MEMORY_MAX", "1M"),
            ("TICO_TURN_CPU_QUOTA", "6"),
        ] {
            let pairs: &'static [(&str, &str)] = Box::leak(Box::new([bad]));
            assert!(limits_from(env(pairs), ram, 8).is_err(), "{bad:?}");
        }
    }

    #[test]
    fn the_scope_is_named_for_the_attempt_and_the_harness_pid() {
        assert_eq!(scope_key("a1b2-c3/../x y"), "tico-turn-a1b2c3xy");
        assert_eq!(
            scope_name(Some("tico-turn-a1b2"), 42),
            "tico-turn-a1b2-42.scope"
        );
        // Anything else in the variable is not trusted as a unit name.
        assert_eq!(scope_name(Some("other.service"), 42), "tico-turn-42.scope");
        assert_eq!(scope_name(Some("tico-turn-x;rm"), 42), "tico-turn-42.scope");
        assert_eq!(scope_name(None, 7), "tico-turn-7.scope");
    }

    #[test]
    fn the_scope_asks_systemd_for_the_limits_bound_to_the_runner() {
        let limits = Limits {
            memory_max: 10 * GIB,
            cpu_usec: Some(6_000_000),
        };
        let args = start_args(
            "unix:path=/run/user/1000/bus",
            "tico-turn-a-9.scope",
            9,
            &limits,
            "tico-bot.service",
        );
        let line = args.join(" ");
        assert!(line.starts_with(
            "--address=unix:path=/run/user/1000/bus --timeout=5 call org.freedesktop.systemd1 /org/freedesktop/systemd1 \
             org.freedesktop.systemd1.Manager StartTransientUnit ssa(sv)a(sa(sv)) tico-turn-a-9.scope fail 7 "
        ), "{line}");
        for prop in [
            "PIDs au 1 9",
            "MemoryMax t 10737418240",
            "MemorySwapMax t 0",
            "OOMPolicy s stop",
            "BindsTo as 1 tico-bot.service",
            "CPUQuotaPerSecUSec t 6000000",
        ] {
            assert!(line.contains(prop), "{prop} in {line}");
        }
        assert!(line.ends_with(" 0"));
        // No quota: one property fewer, and the count says so.
        let none = start_args(
            "a",
            "u.scope",
            1,
            &Limits {
                memory_max: GIB,
                cpu_usec: None,
            },
            "r.service",
        );
        assert!(
            none.join(" ").contains("u.scope fail 6 PIDs")
                && !none.contains(&"CPUQuotaPerSecUSec".into())
        );
    }

    #[test]
    fn the_wrapper_runs_the_harness_after_its_own_arguments() {
        let s = Setup {
            limits: Limits {
                memory_max: 5,
                cpu_usec: None,
            },
            runner_unit: "tico-bot.service".into(),
            busctl: "/usr/bin/busctl".into(),
            bus: [(
                "DBUS_SESSION_BUS_ADDRESS".to_string(),
                "unix:path=/run/user/1000/bus".to_string(),
            )]
            .into(),
            slice: PathBuf::new(),
        };
        let argv: Vec<String> = wrapper_argv("/x/tico-runner".into(), &s)
            .into_iter()
            .map(|a| a.into_string().unwrap())
            .collect();
        assert_eq!(
            argv,
            [
                "/x/tico-runner",
                FLAG,
                "/usr/bin/busctl",
                "5",
                "0",
                "tico-bot.service",
                "unix:path=/run/user/1000/bus",
                "--"
            ]
        );
        // `main` reads the same shape back; a malformed call is refused before anything runs.
        assert_eq!(main(&["a".into(), "b".into()]), 2);
    }

    #[test]
    fn a_kill_at_the_limit_is_read_from_the_cgroup() {
        assert!(in_unit(
            "0::/user.slice/user-1000.slice/user@1000.service/app.slice/tico-bot.service\n",
            "tico-bot.service"
        ));
        assert!(!in_unit(
            "0::/user.slice/x/tico-bot.service.d\n",
            "tico-bot.service"
        ));
        assert_eq!(
            oom_kills("low 0\nhigh 0\nmax 12\noom 1\noom_kill 1\noom_group_kill 0\n"),
            1
        );
        assert_eq!(oom_kills("low 0\noom 0\noom_kill 0\n"), 0);
        assert_eq!(oom_kills(""), 0);
        assert!(oom_message(16 * GIB / 10 * 6)
            .starts_with("This turn used more than 9.6 GB of memory and was stopped"));
    }

    #[test]
    fn nowhere_but_a_linux_cloud_machine_is_limited() {
        // The test computer is no cloud machine (and a Mac is not Linux): harnesses start exactly as before.
        if std::env::var("TICO_TURN_LIMITS").is_err() {
            assert!(prefix().is_none() && !active());
        }
    }
}
