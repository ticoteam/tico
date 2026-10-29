//! Authenticator codes (RFC 6238) from a login's stored secret, so the fill endpoint types the current code and
//! the secret never leaves the runner.

use serde_json::Value;

/// RFC 4648 base32, as authenticator setup keys are written: any case, spaces, dashes and padding ignored.
pub fn base32(text: &str) -> Option<Vec<u8>> {
    let mut out = vec![];
    let (mut buffer, mut bits) = (0u32, 0u32);
    for c in text.chars().filter(|c| !matches!(c, ' ' | '-' | '=' | '\t')) {
        let v = match c.to_ascii_uppercase() {
            u @ 'A'..='Z' => u as u32 - 'A' as u32,
            d @ '2'..='7' => d as u32 - '2' as u32 + 26,
            _ => return None,
        };
        buffer = (buffer << 5) | v;
        bits += 5;
        if bits >= 8 {
            bits -= 8;
            out.push((buffer >> bits) as u8);
            buffer &= (1 << bits) - 1;
        }
    }
    (!out.is_empty()).then_some(out)
}

/// The secret as the redactor should also know it: upper case with the spacing taken out.
pub fn normalised(secret: &str) -> String {
    secret.chars().filter(|c| !matches!(c, ' ' | '-' | '\t')).collect::<String>().to_ascii_uppercase()
}

/// The code for `at` (seconds since the epoch): `digits` (6 to 10), `period` seconds, SHA1, SHA256 or SHA512.
pub fn code_at(secret: &str, digits: u32, period: u64, algorithm: &str, at: u64) -> Option<String> {
    let key = base32(secret)?;
    let alg = match algorithm.trim().to_ascii_uppercase().replace('-', "").as_str() {
        "" | "SHA1" => ring::hmac::HMAC_SHA1_FOR_LEGACY_USE_ONLY,
        "SHA256" => ring::hmac::HMAC_SHA256,
        "SHA512" => ring::hmac::HMAC_SHA512,
        _ => return None,
    };
    if !(6..=10).contains(&digits) || period == 0 {
        return None;
    }
    let counter = (at / period).to_be_bytes();
    let tag = ring::hmac::sign(&ring::hmac::Key::new(alg, &key), &counter);
    let mac = tag.as_ref();
    let offset = (mac[mac.len() - 1] & 0x0f) as usize;
    let number = u32::from_be_bytes([mac[offset] & 0x7f, mac[offset + 1], mac[offset + 2], mac[offset + 3]]) as u64;
    Some(format!("{:0width$}", number % 10u64.pow(digits), width = digits as usize))
}

/// The current code for a login's `totp` object (`{"secret","digits","period","algorithm"}`).
pub fn current(totp: &Value) -> Option<String> {
    let secret = totp["secret"].as_str().filter(|s| !s.trim().is_empty())?;
    let digits = totp["digits"].as_u64().unwrap_or(6) as u32;
    let period = totp["period"].as_u64().unwrap_or(30);
    let algorithm = totp["algorithm"].as_str().unwrap_or("SHA1");
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).ok()?.as_secs();
    code_at(secret, digits, period, algorithm, now)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn encode(data: &[u8]) -> String {
        const ALPHABET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
        let (mut out, mut buffer, mut bits) = (String::new(), 0u32, 0u32);
        for b in data {
            buffer = (buffer << 8) | *b as u32;
            bits += 8;
            while bits >= 5 {
                bits -= 5;
                out.push(ALPHABET[((buffer >> bits) & 31) as usize] as char);
            }
            buffer &= (1 << bits) - 1;
        }
        if bits > 0 {
            out.push(ALPHABET[((buffer << (5 - bits)) & 31) as usize] as char);
        }
        out
    }

    #[test]
    fn rfc_6238_appendix_b_vectors() {
        let sha1 = encode(b"12345678901234567890");
        let sha256 = encode(b"12345678901234567890123456789012");
        let sha512 = encode(b"1234567890123456789012345678901234567890123456789012345678901234");
        let cases: &[(u64, &str, &str, &str)] = &[
            (59, "94287082", "46119246", "90693936"),
            (1111111109, "07081804", "68084774", "25091201"),
            (1111111111, "14050471", "67062674", "99943326"),
            (1234567890, "89005924", "91819424", "93441116"),
            (2000000000, "69279037", "90698825", "38618901"),
            (20000000000, "65353130", "77737706", "47863826"),
        ];
        for (at, a, b, c) in cases {
            assert_eq!(code_at(&sha1, 8, 30, "SHA1", *at).as_deref(), Some(*a));
            assert_eq!(code_at(&sha256, 8, 30, "SHA256", *at).as_deref(), Some(*b));
            assert_eq!(code_at(&sha512, 8, 30, "sha-512", *at).as_deref(), Some(*c));
        }
        // Six digits are the last six of the eight; setup keys come lower case and spaced.
        let spaced = sha1.to_lowercase().as_bytes().chunks(4).map(|c| std::str::from_utf8(c).unwrap()).collect::<Vec<_>>().join(" ");
        assert_eq!(code_at(&spaced, 6, 30, "SHA1", 59).as_deref(), Some("287082"));
    }

    #[test]
    fn a_bad_secret_or_shape_gives_no_code() {
        assert_eq!(code_at("not base32!", 6, 30, "SHA1", 59), None);
        assert_eq!(code_at("GEZDGNBV", 6, 30, "MD5", 59), None);
        assert_eq!(code_at("GEZDGNBV", 4, 30, "SHA1", 59), None);
        assert_eq!(code_at("GEZDGNBV", 6, 0, "SHA1", 59), None);
        assert_eq!(current(&serde_json::json!(null)), None);
    }
}
