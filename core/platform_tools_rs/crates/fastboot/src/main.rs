use std::process::ExitCode;

#[tokio::main]
async fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let code = fastboot::run_cli(&args).await;
    ExitCode::from(code as u8)
}
