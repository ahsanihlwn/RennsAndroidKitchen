/// Platform-tools baseline version string.
pub const PLATFORM_TOOLS_VERSION: &str = "37.0.3";

/// Full platform-tools version + AOSP baseline build number string (`37.0.3-16887417`).
pub const PLATFORM_TOOLS_VERSION_FULL: &str = "37.0.3-16887417";

/// Build identifier for the MIT Rust implementation.
pub const PLATFORM_TOOLS_BUILD_ID: &str = "rust-mit";

/// ADB protocol banner version string.
pub const ADB_PROTOCOL_VERSION_STR: &str = "1.0.41";

/// Returns the formatted version string (`<version>-<build_id>`).
pub fn version_string() -> String {
    format!("{PLATFORM_TOOLS_VERSION}-{PLATFORM_TOOLS_BUILD_ID}")
}

/// Returns a human-readable description of the host operating system and architecture.
pub fn os_description() -> String {
    format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_version_constants() {
        assert_eq!(PLATFORM_TOOLS_VERSION, "37.0.3");
        assert_eq!(PLATFORM_TOOLS_BUILD_ID, "rust-mit");
        assert_eq!(ADB_PROTOCOL_VERSION_STR, "1.0.41");
        assert_eq!(version_string(), "37.0.3-rust-mit");
        assert!(!os_description().is_empty());
    }
}
