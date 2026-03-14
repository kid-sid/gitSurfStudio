// GitSurf Studio — Tauri Backend
// This is the Rust entry point that launches the native window
// and will eventually manage the Python sidecar process.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running GitSurf Studio");
}
