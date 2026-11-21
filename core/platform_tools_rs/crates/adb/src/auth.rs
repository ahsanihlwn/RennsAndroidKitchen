//! ADB RSA-2048 authentication (`AUTH`) and `adb keygen` implementation (MIT).

use std::fs;
use std::path::{Path, PathBuf};

use base64::engine::general_purpose::STANDARD as BASE64_STANDARD;
use base64::Engine;
use rsa::pkcs8::{DecodePrivateKey, EncodePrivateKey, LineEnding};
use rsa::traits::PublicKeyParts;
use rsa::{BigUint, Pkcs1v15Sign, RsaPrivateKey, RsaPublicKey};

use crate::protocol::AdbError;

/// ASN.1 DER DigestInfo prefix for SHA-1 (15 bytes) prepended to the 20-byte `A_AUTH` token.
const SHA1_DIGEST_INFO_PREFIX: [u8; 15] = [
    0x30, 0x21, 0x30, 0x09, 0x06, 0x05, 0x2b, 0x0e, 0x03, 0x02, 0x1a, 0x05, 0x00, 0x04, 0x14,
];

/// An RNG adapter backed by `getrandom` that implements `rsa::rand_core::CryptoRngCore`.
struct OsRngAdapter;

impl rsa::rand_core::RngCore for OsRngAdapter {
    fn next_u32(&mut self) -> u32 {
        let mut b = [0u8; 4];
        let _ = getrandom::fill(&mut b);
        u32::from_le_bytes(b)
    }

    fn next_u64(&mut self) -> u64 {
        let mut b = [0u8; 8];
        let _ = getrandom::fill(&mut b);
        u64::from_le_bytes(b)
    }

    fn fill_bytes(&mut self, dest: &mut [u8]) {
        let _ = getrandom::fill(dest);
    }

    fn try_fill_bytes(&mut self, dest: &mut [u8]) -> Result<(), rsa::rand_core::Error> {
        let _ = getrandom::fill(dest);
        Ok(())
    }
}

impl rsa::rand_core::CryptoRng for OsRngAdapter {}

/// Encodes an [`RsaPublicKey`] into the 524-byte Android `RSAPublicKey` binary layout,
/// base64-encoded with `" user@host"` suffix for `adbkey.pub` and `ADB_AUTH_RSAPUBLICKEY`.
pub fn encode_android_pubkey(pub_key: &RsaPublicKey) -> String {
    let n = pub_key.n();
    let e = pub_key.e();

    let mut n_le = n.to_bytes_le();
    n_le.resize(256, 0);

    let n0 = u32::from_le_bytes(n_le[0..4].try_into().unwrap());
    // Compute -1 / n[0] mod 2^32 via Newton-Raphson iteration (n0 is odd for RSA modulus)
    let mut inv = 1u32;
    for _ in 0..5 {
        inv = inv.wrapping_mul(2u32.wrapping_sub(n0.wrapping_mul(inv)));
    }
    let n0inv = inv.wrapping_neg();

    // Compute rr = (2^2048)^2 mod n = 2^4096 mod n
    let two_pow_4096 = BigUint::from(1u32) << 4096;
    let rr = two_pow_4096 % n;
    let mut rr_le = rr.to_bytes_le();
    rr_le.resize(256, 0);

    let e_le = e.to_bytes_le();
    let mut e_bytes = [0u8; 4];
    let copy_len = e_le.len().min(4);
    e_bytes[..copy_len].copy_from_slice(&e_le[..copy_len]);
    let exponent = u32::from_le_bytes(e_bytes);

    let mut struct_bytes = [0u8; 524];
    struct_bytes[0..4].copy_from_slice(&64u32.to_le_bytes()); // RSANUMWORDS = 64
    struct_bytes[4..8].copy_from_slice(&n0inv.to_le_bytes());
    struct_bytes[8..264].copy_from_slice(&n_le[..256]);
    struct_bytes[264..520].copy_from_slice(&rr_le[..256]);
    struct_bytes[520..524].copy_from_slice(&exponent.to_le_bytes());

    let b64 = BASE64_STANDARD.encode(struct_bytes);
    let user = std::env::var("USER")
        .or_else(|_| std::env::var("USERNAME"))
        .unwrap_or_else(|_| "unknown".to_string());
    let host = std::env::var("HOSTNAME")
        .or_else(|_| std::env::var("COMPUTERNAME"))
        .unwrap_or_else(|_| "localhost".to_string());
    format!("{b64} {user}@{host}")
}

/// Generates a 2048-bit RSA private key at `priv_path` and its Android public key at `<priv_path>.pub`.
pub fn generate_adb_keypair(priv_path: &Path) -> Result<(), AdbError> {
    if let Some(parent) = priv_path.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent)?;
        }
    }
    let mut rng = OsRngAdapter;
    let priv_key = RsaPrivateKey::new(&mut rng, 2048)
        .map_err(|e| AdbError::Crypto(format!("RSA keygen failed: {e}")))?;
    let pub_key = RsaPublicKey::from(&priv_key);

    let pem = priv_key
        .to_pkcs8_pem(LineEnding::LF)
        .map_err(|e| AdbError::Crypto(format!("PKCS#8 encode failed: {e}")))?;
    fs::write(priv_path, pem.as_bytes())?;

    let pub_line = format!("{}\n", encode_android_pubkey(&pub_key));
    let mut pub_os = priv_path.as_os_str().to_os_string();
    pub_os.push(".pub");
    let pub_path = PathBuf::from(pub_os);
    fs::write(pub_path, pub_line.as_bytes())?;
    Ok(())
}

/// Signs a 20-byte `A_AUTH(ADB_AUTH_TOKEN)` token using RSA PKCS#1 v1.5.
pub fn sign_auth_token(priv_key: &RsaPrivateKey, token: &[u8]) -> Result<Vec<u8>, AdbError> {
    let mut digest_info = Vec::with_capacity(SHA1_DIGEST_INFO_PREFIX.len() + token.len());
    digest_info.extend_from_slice(&SHA1_DIGEST_INFO_PREFIX);
    digest_info.extend_from_slice(token);
    priv_key
        .sign(Pkcs1v15Sign::new_unprefixed(), &digest_info)
        .map_err(|e| AdbError::Crypto(format!("RSA sign failed: {e}")))
}

fn default_adbkey_path() -> Option<PathBuf> {
    let home = std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE"))?;
    Some(PathBuf::from(home).join(".android").join("adbkey"))
}

/// Loads `~/.android/adbkey` (or generates it if missing) and returns `(private_key, pubkey_bytes_with_nul)`.
pub fn load_or_create_default_key() -> Result<(RsaPrivateKey, Vec<u8>), AdbError> {
    if let Some(path) = default_adbkey_path() {
        if !path.exists() {
            let _ = generate_adb_keypair(&path);
        }
        if let Ok(pem_str) = fs::read_to_string(&path) {
            if let Ok(priv_key) = RsaPrivateKey::from_pkcs8_pem(&pem_str) {
                let pub_key = RsaPublicKey::from(&priv_key);
                let mut pub_bytes = encode_android_pubkey(&pub_key).into_bytes();
                pub_bytes.push(0);
                return Ok((priv_key, pub_bytes));
            }
        }
    }

    let mut rng = OsRngAdapter;
    let priv_key = RsaPrivateKey::new(&mut rng, 2048)
        .map_err(|e| AdbError::Crypto(format!("RSA keygen failed: {e}")))?;
    let pub_key = RsaPublicKey::from(&priv_key);
    let mut pub_bytes = encode_android_pubkey(&pub_key).into_bytes();
    pub_bytes.push(0);
    Ok((priv_key, pub_bytes))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_keygen_and_token_signing() {
        let dir = tempfile::tempdir().unwrap();
        let key_path = dir.path().join("adbkey");
        generate_adb_keypair(&key_path).unwrap();
        assert!(key_path.exists());
        let pub_path = dir.path().join("adbkey.pub");
        assert!(pub_path.exists());

        let pem = fs::read_to_string(&key_path).unwrap();
        let priv_key = RsaPrivateKey::from_pkcs8_pem(&pem).unwrap();
        let token = [0x42u8; 20];
        let sig = sign_auth_token(&priv_key, &token).unwrap();
        assert_eq!(sig.len(), 256);

        let pub_str = fs::read_to_string(&pub_path).unwrap();
        let b64_part = pub_str.split_whitespace().next().unwrap();
        let decoded = BASE64_STANDARD.decode(b64_part).unwrap();
        assert_eq!(decoded.len(), 524);
        assert_eq!(u32::from_le_bytes(decoded[0..4].try_into().unwrap()), 64);
    }
}
