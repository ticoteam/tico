//! Opt-in real-provider gate. It never opens capture inputs or a meeting library.
use anyhow::{Context, Result, ensure};
use serde_json::json;
use std::{path::Path, time::Instant};
use tico_recorder_native::{audio, network::transcribe_audio, settings};

fn words(text: &str) -> Vec<String> {
    text.split(|character: char| !character.is_alphanumeric())
        .filter(|word| !word.is_empty())
        .map(str::to_lowercase)
        .collect()
}

fn word_errors(expected: &[String], actual: &[String]) -> usize {
    let mut previous: Vec<_> = (0..=actual.len()).collect();
    for (index, word) in expected.iter().enumerate() {
        let mut current = vec![index + 1; actual.len() + 1];
        for (column, observed) in actual.iter().enumerate() {
            current[column + 1] = (previous[column] + usize::from(word != observed))
                .min(previous[column + 1] + 1)
                .min(current[column] + 1);
        }
        previous = current;
    }
    previous[actual.len()]
}

fn save_report(path: &Path, receipt: &serde_json::Value) -> Result<()> {
    use std::io::Write;
    let temporary = path.with_extension(format!("{}.tmp", uuid::Uuid::new_v4()));
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temporary)?;
    let result = (|| -> Result<()> {
        file.write_all(&serde_json::to_vec_pretty(receipt)?)?;
        file.sync_all()?;
        std::fs::rename(&temporary, path)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&temporary);
    }
    result
}

pub fn verify_transcription(
    file: &Path,
    expected: &str,
    report: &Path,
    keychain: bool,
    local_test_gateway: bool,
) -> Result<()> {
    let mut receipt = json!({
        "status":"not_run", "inference_attempted":false,
        "input_kind":"provided_wav_fixture", "native_capture_verified":false,
        "tico_delivery_attempted":false, "maximum_word_error_rate":0.25,
        "transport":"production transcribe_audio"
    });
    let result = (|| -> Result<()> {
        let expected_words = words(expected);
        ensure!(
            (5..=1000).contains(&expected_words.len()),
            "Provide 5–1000 expected words"
        );
        ensure!(
            std::fs::metadata(file)?.len() <= 10_000_000,
            "Use a WAV fixture smaller than 10 MB"
        );
        let bytes = std::fs::read(file)?;
        let signal = audio::inspect_wav(&bytes)?;
        receipt["signal"] = serde_json::to_value(&signal)?;
        ensure!(
            (1..=30_000).contains(&signal.duration_ms),
            "Use a WAV fixture lasting at most 30 seconds"
        );
        ensure!(signal.nonzero > 0, "The fixture contains digital silence");
        let config = settings::read(&settings::root())?;
        let endpoint = reqwest::Url::parse(&settings::safe_url(&config.transcription_url)?)?;
        let loopback =
            ["localhost", "127.0.0.1", "::1", "[::1]"].contains(&endpoint.host_str().unwrap_or(""));
        ensure!(
            endpoint.scheme() == "https" && !loopback || local_test_gateway && loopback,
            "Use remote HTTPS, or opt into a loopback test router with --local-test-gateway"
        );
        receipt["gateway_kind"] = json!(if loopback {
            "loopback-test-gateway"
        } else {
            "remote-provider"
        });
        receipt["model"] = json!(config.transcription_model);
        receipt["endpoint"] = json!(config.transcription_url);
        let key = match std::env::var("TICO_REAL_RECORDER_STT_KEY") {
            Ok(value) if !value.trim().is_empty() => value,
            _ if keychain => settings::secret("transcription")?,
            _ => String::new(),
        };
        ensure!(
            !key.trim().is_empty(),
            "Set TICO_REAL_RECORDER_STT_KEY, or explicitly use --keychain after configuring native Settings. No inference ran."
        );
        receipt["inference_attempted"] = json!(true);
        receipt["status"] = json!("failed");
        let started = Instant::now();
        let reply = transcribe_audio(&config, &key, &bytes);
        receipt["whole_response_ms"] = json!(started.elapsed().as_millis());
        let text = reply.context("Real transcription request failed")?;
        let actual = words(&text);
        ensure!(
            actual.len() <= 5000,
            "The transcription exceeded the expected fixture size"
        );
        let errors = word_errors(&expected_words, &actual);
        let rate = errors as f64 / expected_words.len() as f64;
        receipt["expected_text"] = json!(expected);
        receipt["recognized_text"] = json!(text);
        receipt["word_errors"] = json!(errors);
        receipt["word_error_rate"] = json!(rate);
        ensure!(
            rate <= 0.25,
            "Real STT failed fixture recognition: word error rate {rate:.3} exceeds 0.25"
        );
        receipt["status"] = json!("passed");
        Ok(())
    })();
    if let Err(error) = &result {
        receipt["error"] = json!(format!("{error:#}"));
    }
    save_report(report, &receipt).context("Could not save the private acceptance receipt")?;
    println!(
        "Real transcription gate: {}. Receipt: {}",
        receipt["status"],
        report.display()
    );
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recognition_scoring_counts_missing_invented_and_replaced_words() {
        assert_eq!(
            word_errors(
                &words("Pilot starts Friday."),
                &words("PILOT starts friday!")
            ),
            0
        );
        assert_eq!(
            word_errors(&words("pilot starts Friday"), &words("pilot Friday")),
            1
        );
        assert_eq!(
            word_errors(
                &words("pilot starts Friday"),
                &words("pilot starts next Friday")
            ),
            1
        );
        assert_eq!(
            word_errors(&words("pilot starts Friday"), &words("pilot starts Monday")),
            1
        );
        assert_eq!(word_errors(&words("pilot starts Friday"), &words("")), 3);
    }

    #[test]
    fn silent_fixture_is_rejected_without_claiming_inference_or_capture() {
        let directory = tempfile::tempdir().unwrap();
        let input = directory.path().join("silent.wav");
        let report = directory.path().join("receipt.json");
        std::fs::write(&input, audio::encode_wav(&vec![0.; 16000], 16000).unwrap()).unwrap();
        assert!(
            verify_transcription(
                &input,
                "The recorder pilot will start Friday",
                &report,
                false,
                false
            )
            .is_err()
        );
        let receipt: serde_json::Value =
            serde_json::from_slice(&std::fs::read(&report).unwrap()).unwrap();
        assert_eq!(receipt["status"], "not_run");
        assert_eq!(receipt["inference_attempted"], false);
        assert_eq!(receipt["native_capture_verified"], false);
        assert_eq!(receipt["signal"]["nonzero"], 0);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                std::fs::metadata(report).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }
    }
}
