//! Host launch boundary contracts. The subprocess keeps a test identity out of parallel host tests;
//! real UID checks are opt-in, in a disposable Linux container only, against files created here.
use super::Settings;
use serde_json::{json, Value};
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::path::{Path, PathBuf};

pub(super) fn child(test: &str) -> bool {
    const MARKER: &str = "TICO_TEST_HOST_IDENTITY";
    if std::env::var(MARKER).as_deref() == Ok(test) {
        return true;
    }
    let uid = if unsafe { libc::geteuid() } == 10003 {
        10004
    } else {
        10003
    };
    let out = std::process::Command::new(std::env::current_exe().unwrap())
        .args(["--exact", test, "--nocapture"])
        .env(MARKER, test)
        .env(crate::isolation::UID_ENV, uid.to_string())
        .env(crate::isolation::GID_ENV, "10002")
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "{}{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    false
}

pub(super) fn prefix(command: &tokio::process::Command, argv: &[String]) {
    let cmd = command.as_std();
    assert_eq!(cmd.get_program(), crate::isolation::SETPRIV);
    let mut expected = vec![
        format!(
            "--reuid={}",
            std::env::var(crate::isolation::UID_ENV).unwrap()
        ),
        "--regid=10002".into(),
        "--clear-groups".into(),
        "--inh-caps=-all".into(),
        "--ambient-caps=-all".into(),
    ];
    expected.extend_from_slice(argv);
    assert_eq!(
        cmd.get_args()
            .map(|a| a.to_string_lossy().into_owned())
            .collect::<Vec<_>>(),
        expected
    );
}

pub(super) struct Fixture {
    pub root: PathBuf,
    pub private: PathBuf,
    pub script: PathBuf,
}
impl Fixture {
    pub fn new() -> Self {
        let root = std::env::temp_dir().join(format!(
            "tico-host-isolation-{}",
            uuid::Uuid::new_v4().simple()
        ));
        std::fs::create_dir(&root).unwrap();
        std::fs::set_permissions(&root, std::fs::Permissions::from_mode(0o755)).unwrap();
        let private = root.join("runner-private.json");
        std::fs::write(&private, "supervisor fixture").unwrap();
        std::fs::set_permissions(&private, std::fs::Permissions::from_mode(0o600)).unwrap();
        let script = root.join("probe.py");
        std::fs::write(&script, r#"import os,json,pathlib
def read(path):
 try: return pathlib.Path(path).read_text()
 except OSError: return None
result={'uid':os.geteuid(),'gid':os.getegid(),'private':read(os.environ['TEST_PRIVATE'])}
result['read']={p:read(p) for p in json.loads(os.environ.get('TEST_READ','[]'))}
result['write']={}
for p in json.loads(os.environ.get('TEST_WRITE','[]')):
 try:
  with open(p,'a') as f:f.write('bot write\n')
  result['write'][p]=True
 except OSError:result['write'][p]=False
result['caps']=next(line.split(':',1)[1].strip() for line in pathlib.Path('/proc/self/status').read_text().splitlines() if line.startswith('CapEff:'))
print(json.dumps(result))
"#).unwrap();
        Self {
            root,
            private,
            script,
        }
    }
    pub fn argv(&self) -> Vec<String> {
        vec!["/usr/bin/python3".into(), self.script.display().to_string()]
    }
    pub fn settings(&self) -> Settings {
        let mut s = Settings {
            cwd: self.root.display().to_string(),
            ..Default::default()
        };
        s.env.insert("PATH".into(), "/usr/bin:/bin".into());
        s.env.insert("HOME".into(), self.root.display().to_string());
        s.env
            .insert("TEST_PRIVATE".into(), self.private.display().to_string());
        s
    }
    pub fn actual(&self) -> bool {
        if std::env::var_os("TICO_TEST_DISPOSABLE").is_none() {
            return false;
        }
        assert!(
            cfg!(target_os = "linux"),
            "real UID checks require disposable Linux"
        );
        assert_eq!(
            unsafe { libc::geteuid() },
            0,
            "disposable root supervisor fixture"
        );
        true
    }
    pub async fn run(
        &self,
        mut command: tokio::process::Command,
        read: &[&Path],
        write: &[&Path],
    ) -> Value {
        let out = command
            .env("TEST_READ", json!(read).to_string())
            .env("TEST_WRITE", json!(write).to_string())
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .output()
            .await
            .unwrap();
        assert!(
            out.status.success(),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
        let value: Value = serde_json::from_slice(&out.stdout).unwrap();
        assert_eq!(value["uid"], 10003);
        assert_eq!(value["gid"], 10002);
        assert_eq!(value["caps"], "0000000000000000");
        assert!(
            value["private"].is_null(),
            "bot process cannot read supervisor fixture"
        );
        for path in read {
            assert!(
                value["read"][path.display().to_string()].is_string(),
                "read {path:?}: {value}"
            );
        }
        for path in write {
            assert_eq!(
                value["write"][path.display().to_string()],
                true,
                "write {path:?}: {value}"
            );
        }
        assert_eq!(self.private.metadata().unwrap().uid(), 0);
        assert_eq!(
            self.private.metadata().unwrap().permissions().mode() & 0o777,
            0o600
        );
        value
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}
