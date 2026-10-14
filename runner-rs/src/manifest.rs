//! A bot's `bot.yaml` (older: `employee.yaml`), read as YAML into JSON values (clients/manifest.py and the
//! `yaml.safe_load` calls around it). One reader for readiness, watchers, MCP servers, declared tools and
//! the files a bot publishes.
//!
//! Read as PyYAML's `safe_load` reads (YAML 1.1): a plain `yes`/`no`/`on`/`off` is a boolean, `~` is null,
//! `0o17`-style and `1e5` stay text while `017` is octal and `1.5e+3` a float, and `<<` merges a mapping.
//! Quoted scalars are always text.

use std::collections::HashMap;
use std::path::Path;

use serde_json::{Map, Value};
use yaml_rust2::parser::{Event, MarkedEventReceiver, Parser, Tag};
use yaml_rust2::scanner::{Marker, TScalarStyle};

pub use crate::readiness::manifest_path;

/// A plain scalar as PyYAML's implicit resolvers read it (yaml/resolver.py).
fn resolve(s: &str) -> Value {
    match s {
        "" | "~" | "null" | "Null" | "NULL" => return Value::Null,
        "yes" | "Yes" | "YES" | "true" | "True" | "TRUE" | "on" | "On" | "ON" => return Value::Bool(true),
        "no" | "No" | "NO" | "false" | "False" | "FALSE" | "off" | "Off" | "OFF" => return Value::Bool(false),
        _ => {}
    }
    if let Some(i) = int(s) {
        return Value::from(i);
    }
    if let Some(f) = float(s) {
        return serde_json::Number::from_f64(f).map_or_else(|| Value::String(s.into()), Value::Number);
    }
    Value::String(s.into())
}

fn signed(s: &str) -> (i64, &str) {
    match s.as_bytes().first() {
        Some(b'-') => (-1, &s[1..]),
        Some(b'+') => (1, &s[1..]),
        _ => (1, s),
    }
}

fn int(s: &str) -> Option<i64> {
    let (sign, body) = signed(s);
    let digits = |t: &str, radix: u32| -> Option<i64> {
        let t: String = t.chars().filter(|c| *c != '_').collect();
        (!t.is_empty()).then_some(())?;
        i64::from_str_radix(&t, radix).ok().map(|v| sign * v)
    };
    let ok = |t: &str, set: &dyn Fn(char) -> bool| !t.is_empty() && t.chars().all(|c| c == '_' || set(c));
    if let Some(b) = body.strip_prefix("0b") {
        return ok(b, &|c| c == '0' || c == '1').then(|| digits(b, 2)).flatten();
    }
    if let Some(x) = body.strip_prefix("0x") {
        return ok(x, &|c| c.is_ascii_hexdigit()).then(|| digits(x, 16)).flatten();
    }
    if body == "0" {
        return Some(0);
    }
    if let Some(o) = body.strip_prefix('0') {
        return ok(o, &|c| ('0'..='7').contains(&c)).then(|| digits(o, 8)).flatten();
    }
    if body.starts_with(|c: char| ('1'..='9').contains(&c)) {
        if body.contains(':') {
            // Sexagesimal: 1:30 is 90.
            let mut parts = body.split(':');
            let mut total = digits(parts.next()?, 10)? * sign;
            for p in parts {
                let v: i64 = p.parse().ok().filter(|v| (0..60).contains(v) && p.len() <= 2)?;
                total = total * 60 + v;
            }
            return Some(sign * total);
        }
        return ok(body, &|c| c.is_ascii_digit()).then(|| digits(body, 10)).flatten();
    }
    None
}

fn float(s: &str) -> Option<f64> {
    let (sign, body) = signed(s);
    match body {
        ".inf" | ".Inf" | ".INF" => return Some(sign as f64 * f64::INFINITY),
        ".nan" | ".NaN" | ".NAN" if sign == 1 && !s.starts_with('+') => return Some(f64::NAN),
        _ => {}
    }
    // [0-9][0-9_]*\.[0-9_]*(?:[eE][-+][0-9]+)? or \.[0-9_]+(?:[eE][-+][0-9]+)?: a dot is required.
    let (mantissa, exponent) = match body.find(['e', 'E']) {
        Some(i) => (&body[..i], Some(&body[i + 1..])),
        None => (body, None),
    };
    let (whole, frac) = mantissa.split_once('.')?;
    let digits = |t: &str| t.chars().all(|c| c.is_ascii_digit() || c == '_');
    let shape = if whole.is_empty() { !frac.is_empty() && digits(frac) } else { whole.starts_with(|c: char| c.is_ascii_digit()) && digits(whole) && digits(frac) };
    let exp_ok = exponent.is_none_or(|e| e.len() > 1 && e.starts_with(['-', '+']) && e[1..].chars().all(|c| c.is_ascii_digit()));
    if !shape || !exp_ok {
        return None;
    }
    let clean: String = body.chars().filter(|c| *c != '_').collect();
    clean.parse::<f64>().ok().map(|f| sign as f64 * f)
}

fn tagged(value: String, style: TScalarStyle, tag: Option<Tag>) -> Value {
    match tag {
        Some(t) if t.handle == "tag:yaml.org,2002:" || t.handle == "!!" => match t.suffix.as_str() {
            "str" => Value::String(value),
            "bool" | "int" | "float" | "null" => resolve(&value),
            _ => Value::String(value),
        },
        Some(_) => Value::String(value),
        None if style == TScalarStyle::Plain => resolve(&value),
        None => Value::String(value),
    }
}

/// A key as Python's `str()` of what PyYAML made of it.
fn key_text(k: &Value) -> String {
    match k {
        Value::String(s) => s.clone(),
        other => py_str(other),
    }
}

enum Frame {
    Seq(usize, Vec<Value>),
    Map(usize, Map<String, Value>, Option<String>, Vec<Value>),
}

#[derive(Default)]
struct Loader {
    stack: Vec<Frame>,
    anchors: HashMap<usize, Value>,
    done: Option<Value>,
    error: Option<String>,
}

impl Loader {
    fn push(&mut self, v: Value, anchor: usize, merge_key: bool) {
        if anchor > 0 {
            self.anchors.insert(anchor, v.clone());
        }
        match self.stack.last_mut() {
            None => {
                if self.done.is_none() {
                    self.done = Some(v);
                }
            }
            Some(Frame::Seq(_, items)) => items.push(v),
            Some(Frame::Map(_, m, key, merges)) => match key.take() {
                None if merge_key => *key = Some("<<".into()),
                None => *key = Some(key_text(&v)),
                Some(k) if k == "<<" => match v {
                    Value::Object(_) => merges.push(v),
                    Value::Array(list) => merges.extend(list),
                    _ => self.error = Some("a merge key needs a mapping".into()),
                },
                Some(k) => {
                    m.insert(k, v);
                }
            },
        }
    }
}

impl MarkedEventReceiver for Loader {
    fn on_event(&mut self, ev: Event, _mark: Marker) {
        match ev {
            Event::Scalar(value, style, anchor, tag) => {
                let awaiting_key = matches!(self.stack.last(), Some(Frame::Map(_, _, None, _)));
                let merge = awaiting_key && style == TScalarStyle::Plain && tag.is_none() && value == "<<";
                let v = tagged(value, style, tag);
                self.push(v, anchor, merge);
            }
            Event::Alias(id) => match self.anchors.get(&id).cloned() {
                Some(v) => self.push(v, 0, false),
                None => self.error = Some("found undefined alias".into()),
            },
            Event::SequenceStart(anchor, _) => self.stack.push(Frame::Seq(anchor, vec![])),
            Event::MappingStart(anchor, _) => self.stack.push(Frame::Map(anchor, Map::new(), None, vec![])),
            Event::SequenceEnd | Event::MappingEnd => match self.stack.pop() {
                Some(Frame::Seq(anchor, items)) => self.push(Value::Array(items), anchor, false),
                Some(Frame::Map(anchor, mut m, _, merges)) => {
                    // Earlier merged mappings win over later ones; the mapping's own keys win over all.
                    for merged in merges {
                        for (k, v) in merged.as_object().into_iter().flatten() {
                            m.entry(k.clone()).or_insert_with(|| v.clone());
                        }
                    }
                    self.push(Value::Object(m), anchor, false);
                }
                None => {}
            },
            _ => {}
        }
    }
}

/// The document as JSON: an empty file is `{}` (Python's `yaml.safe_load(...) or {}`).
pub fn parse(text: &str) -> Result<Value, String> {
    let mut loader = Loader::default();
    Parser::new_from_str(text).load(&mut loader, false).map_err(|e| e.to_string())?;
    if let Some(e) = loader.error {
        return Err(e);
    }
    Ok(match loader.done.unwrap_or(Value::Null) {
        Value::Null => Value::Object(Map::new()),
        other => other,
    })
}

/// The folder's manifest, or None when it is missing or not YAML.
pub fn read(folder: &Path) -> Option<Value> {
    let text = std::fs::read_to_string(manifest_path(folder)).ok()?;
    parse(&text).ok()
}

/// The value under the new key, or the old key when the new one is absent (manifest.pick).
pub fn pick<'a>(manifest: &'a Value, new: &str, old: &str) -> Option<&'a Value> {
    let m = manifest.as_object()?;
    if m.contains_key(new) {
        m.get(new)
    } else {
        m.get(old)
    }
}

/// The declared tools: `tools:`, else the older `access:`.
pub fn tools_of(manifest: &Value) -> Value {
    pick(manifest, "tools", "access").cloned().unwrap_or(Value::Null)
}

/// Python's `str(value)` for a scalar: text as is, `True`/`False`, `None`, numbers as written.
pub fn py_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "True" } else { "False" }.into(),
        Value::Null => "None".into(),
        Value::Number(n) => {
            if n.is_f64() {
                let f = n.as_f64().unwrap_or(0.0);
                if f.fract() == 0.0 && f.abs() < 1e16 { format!("{f:.1}") } else { f.to_string() }
            } else {
                n.to_string()
            }
        }
        other => crate::util::py_dumps(other),
    }
}

/// Python truthiness of `value or ""` followed by `str()`: "" for a falsy value.
pub fn text_or_empty(v: Option<&Value>) -> String {
    if crate::util::truthy(v) {
        py_str(v.unwrap())
    } else {
        String::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn yaml_reads_into_json_with_the_old_names_as_fallbacks() {
        let doc = parse("name: ops\nmodel: \"x\"  # c\nwatchers:\n  - name: a\n    every: 5m\n    enabled: false\naccess:\n  - {service: board, can: [read]}\n").unwrap();
        assert_eq!(doc["name"], "ops");
        assert_eq!(doc["watchers"][0]["enabled"], false);
        assert_eq!(tools_of(&doc), json!([{"service": "board", "can": ["read"]}]));
        assert_eq!(parse("").unwrap(), json!({}));
        assert!(parse("a: [1,").is_err());
        assert_eq!(parse("1: x\ntrue: y\n").unwrap(), json!({"1": "x", "True": "y"}));
        assert_eq!(py_str(&json!(1.0)), "1.0");
        assert_eq!(py_str(&json!(5)), "5");
    }

    #[test]
    fn plain_scalars_resolve_as_pyyaml_reads_yaml_1_1() {
        let doc = parse("a: no\nb: On\nc: 'no'\nd: ~\ne: 017\nf: 1e5\ng: 1.5e+3\nh: 0x1F\ni: 1:30\nj: !!str yes\nk: .inf\nl: 1_000\nyes: m\n").unwrap();
        assert_eq!(doc["a"], false);
        assert_eq!(doc["b"], true);
        assert_eq!(doc["c"], "no");
        assert_eq!(doc["d"], Value::Null);
        assert_eq!(doc["e"], 15);
        assert_eq!(doc["f"], "1e5");
        assert_eq!(doc["g"], 1500.0);
        assert_eq!(doc["h"], 31);
        assert_eq!(doc["i"], 90);
        assert_eq!(doc["j"], "yes");
        assert_eq!(doc["l"], 1000);
        assert_eq!(doc["True"], "m");
        let merged = parse("base: &b {x: 1, y: 2}\nmore: &m {x: 5, z: 6}\nbot:\n  <<: [*b, *m]\n  y: 3\n").unwrap();
        assert_eq!(merged["bot"], json!({"x": 1, "y": 3, "z": 6}));
        assert_eq!(parse("watchers:\n  - name: a\n    enabled: off\n").unwrap()["watchers"][0]["enabled"], false);
    }
}
