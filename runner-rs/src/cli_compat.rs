//! Which releases of a harness CLI Tico supports: the oldest it supports and the ones known broken, from the
//! manifest's `[version]` table (runner-rs/harnesses/*.toml, docs/harnesses.md). Nothing is fetched: the list ships
//! with the runner.
//!
//! The daily `latest` update never moves to a known-broken release (it stays on the installed one and says why), an
//! install or Update without a version takes the newest release that is not broken, and an installed release below
//! the minimum or on the list is named in the harness's status. Flags only newer releases have are gated by version
//! in the host adapter (hosts/claude.rs), not here.

use crate::harness_tools::version_key;

/// One `broken` entry: comparators that must all hold, e.g. `2.1.301` or `>=2.1.300 <2.1.305`.
#[derive(Clone, Debug, PartialEq)]
pub struct Range {
    pub text: String,
    parts: Vec<(Op, Vec<u64>)>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum Op {
    Eq,
    Lt,
    Le,
    Gt,
    Ge,
}

/// Three numbers and nothing else: what a range and the minimum are written in.
fn release(text: &str) -> Option<Vec<u64>> {
    let parts: Vec<&str> = text.split('.').collect();
    (parts.len() == 3
        && parts
            .iter()
            .all(|p| !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit())))
    .then(|| version_key(text))
}

impl Range {
    pub fn parse(text: &str) -> Result<Range, String> {
        let mut parts = vec![];
        for word in text.split_whitespace() {
            let (op, rest) = [
                (">=", Op::Ge),
                ("<=", Op::Le),
                (">", Op::Gt),
                ("<", Op::Lt),
                ("=", Op::Eq),
            ]
            .iter()
            .find_map(|(p, op)| word.strip_prefix(p).map(|rest| (*op, rest)))
            .unwrap_or((Op::Eq, word));
            let key = release(rest).ok_or_else(|| {
                format!("{text:?} is not a version (1.2.3) or a range (>=1.2.3 <1.2.5)")
            })?;
            parts.push((op, key));
        }
        if parts.is_empty() {
            return Err("an empty range".into());
        }
        Ok(Range {
            text: text.split_whitespace().collect::<Vec<_>>().join(" "),
            parts,
        })
    }

    pub fn contains(&self, version: &str) -> bool {
        let v = version_key(version);
        !v.is_empty()
            && self.parts.iter().all(|(op, k)| match op {
                Op::Eq => &v == k,
                Op::Lt => &v < k,
                Op::Le => &v <= k,
                Op::Gt => &v > k,
                Op::Ge => &v >= k,
            })
    }
}

/// A manifest's `[version] minimum` and `broken`.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Compat {
    /// The oldest release whose every flag and setting the runner passes unconditionally exists. "" for none.
    pub minimum: String,
    pub broken: Vec<Range>,
}

impl Compat {
    pub fn parse(minimum: &str, broken: &[String]) -> Result<Compat, String> {
        if !minimum.is_empty() && release(minimum).is_none() {
            return Err(format!("{minimum:?} is not a version (1.2.3)"));
        }
        Ok(Compat {
            minimum: minimum.into(),
            broken: broken
                .iter()
                .map(|r| Range::parse(r))
                .collect::<Result<_, _>>()?,
        })
    }

    /// The entry that lists `version` as broken.
    pub fn broken(&self, version: &str) -> Option<&Range> {
        self.broken.iter().find(|r| r.contains(version))
    }

    pub fn below_minimum(&self, version: &str) -> bool {
        !self.minimum.is_empty()
            && !version_key(version).is_empty()
            && version_key(version) < version_key(&self.minimum)
    }

    /// The newest of `listed` that is older than `newest`, at least the minimum, not broken, and a plain release (no
    /// pre-release or build suffix): what an install takes when the newest release is known broken.
    pub fn newest_allowed(&self, listed: &[String], newest: &str) -> Option<String> {
        listed
            .iter()
            .filter(|v| release(v).is_some())
            .filter(|v| version_key(v) < version_key(newest))
            .filter(|v| !self.below_minimum(v) && self.broken(v).is_none())
            .max_by_key(|v| version_key(v))
            .cloned()
    }

    /// Why `installed` is a problem, for the harness's status: below the minimum or on the broken list.
    pub fn problem(&self, name: &str, installed: &str) -> Option<String> {
        if self.below_minimum(installed) {
            return Some(format!(
                "{name} {installed} is older than {}, the oldest Tico supports; update it",
                self.minimum
            ));
        }
        self.broken(installed).map(|r| {
            format!(
                "{name} {installed} is a known-broken release ({}); update it",
                r.text
            )
        })
    }

    /// Why the daily update holds at `installed` instead of taking `latest`, when it does.
    pub fn held(&self, name: &str, latest: &str, installed: &str) -> Option<String> {
        self.broken(latest).map(|r| {
            format!(
                "{name} {latest} is a known-broken release ({}); staying on {installed}",
                r.text
            )
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn compat(minimum: &str, broken: &[&str]) -> Compat {
        Compat::parse(
            minimum,
            &broken.iter().map(|s| s.to_string()).collect::<Vec<_>>(),
        )
        .unwrap()
    }

    #[test]
    fn ranges_are_one_version_or_comparators_that_all_hold() {
        let one = Range::parse("2.1.301").unwrap();
        assert!(one.contains("2.1.301"));
        assert!(!one.contains("2.1.30"));
        assert!(!one.contains("2.1.3010"));
        let span = Range::parse(" >=2.1.300   <2.1.305 ").unwrap();
        assert_eq!(span.text, ">=2.1.300 <2.1.305");
        assert!(span.contains("2.1.300") && span.contains("2.1.304"));
        assert!(!span.contains("2.1.299") && !span.contains("2.1.305"));
        assert!(Range::parse("<=0.140.0").unwrap().contains("0.140.0"));
        assert!(Range::parse(">0.140.0").unwrap().contains("0.141.0"));
        assert!(!Range::parse("=0.140.0").unwrap().contains("0.140.1"));
        // A version nothing could be read from is never in a range.
        assert!(!Range::parse("<9.9.9").unwrap().contains(""));
        for bad in ["", "2.1", "~2.1.0", ">=2.1.x", "2.1.0-beta", "latest"] {
            assert!(Range::parse(bad).is_err(), "{bad:?}");
        }
        assert!(Compat::parse("2.1", &[]).is_err());
        assert!(Compat::parse("", &["nope".into()]).is_err());
    }

    #[test]
    fn minimum_and_broken_name_the_problem_with_an_installed_release() {
        let c = compat("2.1.74", &[">=2.1.300 <2.1.302"]);
        assert!(c.below_minimum("2.1.73") && !c.below_minimum("2.1.74"));
        assert!(
            !c.below_minimum(""),
            "an unreadable version is not reported as old"
        );
        assert_eq!(
            c.problem("Claude Code", "2.1.70").unwrap(),
            "Claude Code 2.1.70 is older than 2.1.74, the oldest Tico supports; update it"
        );
        assert!(c
            .problem("Claude Code", "2.1.301")
            .unwrap()
            .contains("known-broken"));
        assert_eq!(c.problem("Claude Code", "2.1.302"), None);
        assert_eq!(
            c.held("Claude Code", "2.1.300", "2.1.299").unwrap(),
            "Claude Code 2.1.300 is a known-broken release (>=2.1.300 <2.1.302); staying on 2.1.299"
        );
        assert_eq!(c.held("Claude Code", "2.1.302", "2.1.299"), None);
        assert_eq!(Compat::default().problem("Codex", "0.1.0"), None);
    }

    #[test]
    fn an_install_past_a_broken_newest_takes_the_newest_plain_release_below_it() {
        let c = compat("2.1.74", &["2.1.305", "2.1.304"]);
        let listed: Vec<String> = [
            "2.1.70",
            "2.1.302",
            "2.1.303-beta.1",
            "2.1.303",
            "2.1.304",
            "2.1.305",
            "2.1.306",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        assert_eq!(
            c.newest_allowed(&listed, "2.1.305").as_deref(),
            Some("2.1.303")
        );
        assert_eq!(
            c.newest_allowed(&listed, "2.1.70"),
            None,
            "nothing at or above the minimum"
        );
    }
}
