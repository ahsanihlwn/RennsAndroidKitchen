use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let code = extfs_mkfs::run_cli(&args);
    ExitCode::from(code as u8)
}
