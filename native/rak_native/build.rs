// SPDX-License-Identifier: MPL-2.0
fn main() {
    println!("cargo:rerun-if-env-changed=RAK_NATIVE_BUILD_FINGERPRINT");
}
