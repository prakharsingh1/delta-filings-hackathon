// Delta Filings desktop shell.
//
// Starts the bundled backend (desktop/server.py frozen with PyInstaller), waits
// for its ready line, then points the window at the per-launch loopback URL.
// The backend gets no Tauri API access: pages it serves are a remote origin to
// Tauri, and no capability grants them IPC.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::webview::DownloadEvent;
use tauri::{AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_opener::OpenerExt;

struct Backend(Mutex<Option<Child>>);

fn backend_command(app: &AppHandle) -> Command {
    // Development: DELTA_BACKEND_CMD="python3 /path/to/desktop/server.py"
    if let Ok(raw) = std::env::var("DELTA_BACKEND_CMD") {
        let mut parts = raw.split_whitespace();
        let mut cmd = Command::new(parts.next().unwrap_or("python3"));
        cmd.args(parts);
        return cmd;
    }
    let dir: PathBuf = app
        .path()
        .resource_dir()
        .expect("resource directory")
        .join("delta-server");
    let exe = if cfg!(windows) { dir.join("delta-server.exe") } else { dir.join("delta-server") };
    Command::new(exe)
}

fn is_local(url: &Url) -> bool {
    matches!(url.scheme(), "tauri" | "asset" | "about" | "data" | "blob")
        || matches!(url.host_str(), Some("127.0.0.1") | Some("localhost") | Some("tauri.localhost"))
}

fn show_error(app: &AppHandle, message: &str) {
    if let Some(w) = app.get_webview_window("main") {
        let js = format!("window.deltaBackendFailed && window.deltaBackendFailed({:?});", message);
        let _ = w.eval(&js);
    }
}

fn start_backend(app: AppHandle) {
    let mut cmd = backend_command(&app);
    cmd.arg("--watch-stdin")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            show_error(&app, &format!("Delta Filings could not start its local service: {e}"));
            return;
        }
    };
    let stdout = child.stdout.take().expect("piped stdout");
    app.state::<Backend>().0.lock().unwrap().replace(child);

    let mut ready = false;
    for line in BufReader::new(stdout).lines().map_while(Result::ok) {
        if ready || !line.contains("\"event\": \"ready\"") {
            continue; // keep draining so the pipe never fills
        }
        let parsed: serde_json::Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(_) => continue,
        };
        if let Some(url) = parsed["url"].as_str().and_then(|u| Url::parse(u).ok()) {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.navigate(url);
                ready = true;
            }
        }
    }
    if !ready {
        show_error(&app, "The local service stopped before it was ready. Restart Delta Filings; if it keeps happening, send the log from the Help menu.");
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(Backend(Mutex::new(None)))
        .setup(|app| {
            let opener = app.handle().clone();
            let downloads = app.path().download_dir().ok();
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Delta Filings")
                .inner_size(1360.0, 880.0)
                .min_inner_size(1024.0, 680.0)
                .on_navigation(move |url| {
                    if is_local(url) {
                        return true;
                    }
                    // Source filings and every outside link open in the user's browser.
                    let _ = opener.opener().open_url(url.as_str(), None::<&str>);
                    false
                })
                .on_download(move |_webview, event| {
                    if let DownloadEvent::Requested { destination, .. } = event {
                        if let (Some(dir), Some(name)) = (downloads.clone(), destination.file_name()) {
                            *destination = dir.join(name);
                        }
                    }
                    true
                })
                .build()?;
            let handle = app.handle().clone();
            std::thread::spawn(move || start_backend(handle));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Delta Filings")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(mut child) = app.state::<Backend>().0.lock().unwrap().take() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        });
}
