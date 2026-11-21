use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().collect();
    let code = f2fs_mkfs::run_cli(&args, true);
    ExitCode::from(code as u8)
}
