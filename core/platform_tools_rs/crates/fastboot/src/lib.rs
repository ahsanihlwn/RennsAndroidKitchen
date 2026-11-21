//! Clean-room MIT Android `fastboot` library and CLI runner (`fastboot`).

pub mod commands;
pub mod config;
pub mod protocol;
pub mod transport;

pub use commands::{execute_session, run_cli, run_cli_captured};
pub use config::{
    help_output, is_embedded_mode, parse_cli_args, parse_fastboot_info, set_embedded_mode,
    version_output, FastbootCliAction, FastbootInfoStep, FastbootSessionConfig, FastbootTask,
};
pub use protocol::{
    FastbootError, FastbootEvent, FastbootEventCallback, FastbootResponse, FastbootRunResult,
};
pub use transport::AnyFastbootTransport;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_fastboot_info_manifest() {
        let manifest = "\
# Sample fastboot-info.txt
version 1
flash boot
flash --slot-other system system_other.img
reboot fastboot
update-super
if-wipe erase userdata
";
        let steps = parse_fastboot_info(manifest);
        assert_eq!(
            steps,
            vec![
                FastbootInfoStep::Version(1),
                FastbootInfoStep::Flash {
                    partition: "boot".into(),
                    image_name: None,
                    slot_other: false,
                    if_wipe: false,
                },
                FastbootInfoStep::Flash {
                    partition: "system".into(),
                    image_name: Some("system_other.img".into()),
                    slot_other: true,
                    if_wipe: false,
                },
                FastbootInfoStep::Reboot(Some("fastboot".into())),
                FastbootInfoStep::UpdateSuper,
                FastbootInfoStep::Erase("userdata".into()),
            ]
        );
    }

    #[test]
    fn test_cli_parser_commands() {
        let args: Vec<String> = vec![
            "-s",
            "tcp:127.0.0.1:5554",
            "--disable-verity",
            "--disable-verification",
            "--slot",
            "a",
            "flash",
            "vbmeta",
            "vbmeta.img",
            "reboot",
            "bootloader",
        ]
        .into_iter()
        .map(String::from)
        .collect();
        let FastbootCliAction::Execute(cfg) = parse_cli_args(&args).unwrap() else {
            panic!("expected Execute");
        };
        assert_eq!(cfg.serial.as_deref(), Some("tcp:127.0.0.1:5554"));
        assert!(cfg.disable_verity);
        assert!(cfg.disable_verification);
        assert_eq!(cfg.slot_override.as_deref(), Some("a"));
        assert_eq!(cfg.tasks.len(), 2);
    }
}
