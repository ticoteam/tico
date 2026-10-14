//! 1Password references in a bot's granted credentials, resolved on this computer before the run starts
//! (runner/op.py): a value like `op://vault/item/field` is read with `op read` and the service-account token in
//! `secrets/_shared.env` (OP_SERVICE_ACCOUNT_TOKEN), which never reaches the run itself.

use std::collections::BTreeMap;
use std::time::Duration;

use crate::util::log;

pub const OP_REF: &str = "op://";
pub const OP_TIMEOUT_S: u64 = 20;
pub const TOKEN_KEY: &str = "OP_SERVICE_ACCOUNT_TOKEN";

/// How one `op read` went: the secret, or why not.
async fn read(program: &str, reference: &str, token: &str) -> Result<String, String> {
    let mut cmd = tokio::process::Command::new(program);
    cmd.args(["read", "--no-newline", reference])
        .env(TOKEN_KEY, token)
        .stdin(std::process::Stdio::null())
        .kill_on_drop(true);
    match tokio::time::timeout(Duration::from_secs(OP_TIMEOUT_S), cmd.output()).await {
        Err(_) => Err(format!("op read timed out after {OP_TIMEOUT_S}s")),
        Ok(Err(e)) if e.kind() == std::io::ErrorKind::NotFound => Err("the 1Password CLI (`op`) is not installed on PATH".into()),
        Ok(Err(e)) => Err(format!("op could not run: {e}")),
        Ok(Ok(out)) => {
            let stdout = String::from_utf8_lossy(&out.stdout).into_owned();
            if out.status.success() && !stdout.is_empty() {
                Ok(stdout)
            } else {
                let stderr = String::from_utf8_lossy(&out.stderr);
                Err(stderr.trim().lines().last().map(String::from).unwrap_or_else(|| format!("op exited {}", out.status.code().unwrap_or(-1))))
            }
        }
    }
}

/// Replace every `op://` value in `env` with the secret 1Password holds for it. One that does not resolve
/// becomes empty, so the run reports the credential missing instead of failing halfway with a broken one.
/// `env[OP_SERVICE_ACCOUNT_TOKEN]` is the token to use. Returns {key: "ok" | reason} for the references seen.
pub async fn resolve_op_refs(env: &mut BTreeMap<String, String>) -> BTreeMap<String, String> {
    resolve_with(env, "op").await
}

pub async fn resolve_with(env: &mut BTreeMap<String, String>, program: &str) -> BTreeMap<String, String> {
    let refs: Vec<(String, String)> = env.iter().filter(|(_, v)| v.starts_with(OP_REF)).map(|(k, v)| (k.clone(), v.clone())).collect();
    let mut out = BTreeMap::new();
    if refs.is_empty() {
        return out;
    }
    let token = env.get(TOKEN_KEY).map(|t| t.trim().to_string()).unwrap_or_default();
    if token.is_empty() {
        for (k, _) in &refs {
            env.insert(k.clone(), String::new());
            out.insert(k.clone(), "OP_SERVICE_ACCOUNT_TOKEN is not set in secrets/_shared.env".to_string());
        }
        log("op: OP_SERVICE_ACCOUNT_TOKEN is not set in secrets/_shared.env");
        return out;
    }
    let mut cache: BTreeMap<String, Result<String, String>> = BTreeMap::new();
    for (k, reference) in refs {
        if !cache.contains_key(&reference) {
            let got = read(program, &reference, &token).await;
            cache.insert(reference.clone(), got);
        }
        match &cache[&reference] {
            Ok(value) => {
                env.insert(k.clone(), value.clone());
                out.insert(k, "ok".into());
            }
            Err(why) => {
                env.insert(k.clone(), String::new());
                log(&format!("op: {k} did not resolve from 1Password: {why}"));
                out.insert(k, why.clone());
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    #[tokio::test]
    async fn references_resolve_once_each_and_a_failure_is_an_empty_value() {
        let dir = std::env::temp_dir().join(format!("tico-op-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&dir).unwrap();
        let fake = dir.join("op");
        let calls = dir.join("calls");
        std::fs::write(
            &fake,
            format!(
                "#!/bin/sh\necho \"$3 $OP_SERVICE_ACCOUNT_TOKEN\" >> '{}'\ncase \"$3\" in\n  op://v/good/password) printf 'resolved-secret' ;;\n  *) echo \"[ERROR] item not found\" >&2; exit 1 ;;\nesac\n",
                calls.display()
            ),
        )
        .unwrap();
        std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).unwrap();
        let mut env: BTreeMap<String, String> = [
            ("A", "op://v/good/password"),
            ("B", "op://v/good/password"),
            ("C", "op://v/missing/password"),
            ("D", "plain"),
            (TOKEN_KEY, " ops_token "),
        ]
        .into_iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect();
        let report = resolve_with(&mut env, fake.to_str().unwrap()).await;
        assert_eq!((env["A"].as_str(), env["B"].as_str(), env["C"].as_str(), env["D"].as_str()), ("resolved-secret", "resolved-secret", "", "plain"));
        assert_eq!((report["A"].as_str(), report["C"].as_str()), ("ok", "[ERROR] item not found"));
        assert_eq!(std::fs::read_to_string(&calls).unwrap().lines().collect::<Vec<_>>(), ["op://v/good/password ops_token", "op://v/missing/password ops_token"]);

        let mut untokened: BTreeMap<String, String> = [("A".to_string(), "op://v/good/password".to_string())].into_iter().collect();
        let report = resolve_with(&mut untokened, fake.to_str().unwrap()).await;
        assert_eq!(untokened["A"], "");
        assert!(report["A"].contains("OP_SERVICE_ACCOUNT_TOKEN is not set"));
        let mut absent: BTreeMap<String, String> = [("A".to_string(), "op://x".to_string()), (TOKEN_KEY.to_string(), "t".to_string())].into_iter().collect();
        let report = resolve_with(&mut absent, "/nonexistent/op").await;
        assert!(report["A"].contains("not installed"));
        std::fs::remove_dir_all(dir).ok();
    }
}
