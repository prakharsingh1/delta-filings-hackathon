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

use tauri::menu::{MenuBuilder, MenuItemBuilder, SubmenuBuilder};
use tauri::webview::{DownloadEvent, NewWindowResponse};
use tauri::{AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_opener::OpenerExt;

struct Backend(Mutex<Option<Child>>);

/// The splash page's own URL, so errors can be shown on it from any page.
struct Splash(Mutex<Option<Url>>);

/// Unexpected backend exits so far; past MAX_RESTARTS the error stays on screen.
struct Restarts(Mutex<u32>);
const MAX_RESTARTS: u32 = 3;

/// What the backend reported in its ready line.
#[derive(Default, Clone)]
struct Ready {
    origin: String,
    data_dir: String,
    logs_dir: String,
    backups_dir: String,
}
struct ReadyState(Mutex<Option<Ready>>);

fn build_menu(app: &AppHandle) -> tauri::Result<tauri::menu::Menu<tauri::Wry>> {
    let ai = MenuItemBuilder::with_id("ai_settings", "AI Provider…")
        .accelerator("CmdOrCtrl+,")
        .build(app)?;
    let app_menu = SubmenuBuilder::new(app, "Delta Filings")
        .about(None)
        .separator()
        .item(&ai)
        .separator()
        .services()
        .separator()
        .hide()
        .hide_others()
        .show_all()
        .separator()
        .quit()
        .build()?;
    let file = SubmenuBuilder::new(app, "File")
        .item(&MenuItemBuilder::with_id("open_data", "Open Data Folder").build(app)?)
        .item(&MenuItemBuilder::with_id("open_backups", "Open Backups Folder").build(app)?)
        .separator()
        .close_window()
        .build()?;
    // Without an Edit menu, macOS web views lose copy, paste and undo shortcuts.
    let edit = SubmenuBuilder::new(app, "Edit")
        .undo()
        .redo()
        .separator()
        .cut()
        .copy()
        .paste()
        .select_all()
        .build()?;
    let view = SubmenuBuilder::new(app, "View")
        .item(&MenuItemBuilder::with_id("back", "Back").accelerator("CmdOrCtrl+[").build(app)?)
        .item(&MenuItemBuilder::with_id("forward", "Forward").accelerator("CmdOrCtrl+]").build(app)?)
        .item(&MenuItemBuilder::with_id("reload", "Reload").accelerator("CmdOrCtrl+R").build(app)?)
        .item(&MenuItemBuilder::with_id("home", "Home").accelerator("CmdOrCtrl+Shift+H").build(app)?)
        .separator()
        .fullscreen()
        .build()?;
    let window = SubmenuBuilder::new(app, "Window").minimize().build()?;
    let help = SubmenuBuilder::new(app, "Help")
        .item(&MenuItemBuilder::with_id("open_logs", "Open Logs Folder").build(app)?)
        .item(&MenuItemBuilder::with_id("website", "Delta Filings Website").build(app)?)
        .build()?;
    MenuBuilder::new(app)
        .items(&[&app_menu, &file, &edit, &view, &window, &help])
        .build()
}

fn on_menu(app: &AppHandle, id: &str) {
    let ready = app.state::<ReadyState>().0.lock().unwrap().clone();
    let window = app.get_webview_window("main");
    match (id, ready, window) {
        ("website", _, _) => {
            let _ = app.opener().open_url("https://deltafiling.tech", None::<&str>);
        }
        ("open_data", Some(r), _) => {
            let _ = app.opener().open_path(r.data_dir, None::<&str>);
        }
        ("open_backups", Some(r), _) => {
            let _ = std::fs::create_dir_all(&r.backups_dir);
            let _ = app.opener().open_path(r.backups_dir, None::<&str>);
        }
        ("open_logs", Some(r), _) => {
            let _ = app.opener().open_path(r.logs_dir, None::<&str>);
        }
        ("ai_settings", Some(r), Some(w)) => {
            if let Ok(u) = Url::parse(&format!("{}/settings/ai", r.origin)) {
                let _ = w.navigate(u);
            }
        }
        ("home", Some(r), Some(w)) => {
            if let Ok(u) = Url::parse(&format!("{}/", r.origin)) {
                let _ = w.navigate(u);
            }
        }
        ("reload", _, Some(w)) => {
            let _ = w.eval("location.reload()");
        }
        ("back", _, Some(w)) => {
            let _ = w.eval("history.back()");
        }
        ("forward", _, Some(w)) => {
            let _ = w.eval("history.forward()");
        }
        _ => {}
    }
}

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

/// Hand a non-local link to the user's browser or mail app. Other schemes
/// (file:, smb:, custom protocol handlers) could start programs, so they are dropped.
fn open_outside(app: &AppHandle, url: &Url) {
    if matches!(url.scheme(), "http" | "https" | "mailto") {
        let _ = app.opener().open_url(url.as_str(), None::<&str>);
    }
}

/// Put the splash page back up with a message. Carrying the message in the URL
/// means it shows even if the page had not finished loading yet.
fn show_error(app: &AppHandle, message: &str) {
    let splash = app.state::<Splash>().0.lock().unwrap().clone();
    if let (Some(mut url), Some(w)) = (splash, app.get_webview_window("main")) {
        url.set_query(None);
        url.query_pairs_mut().append_pair("error", message);
        let _ = w.navigate(url);
    }
}

fn show_splash(app: &AppHandle) {
    let splash = app.state::<Splash>().0.lock().unwrap().clone();
    if let (Some(mut url), Some(w)) = (splash, app.get_webview_window("main")) {
        url.set_query(None);
        let _ = w.navigate(url);
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
    let mut failed = false;
    for line in BufReader::new(stdout).lines().map_while(Result::ok) {
        if !ready && line.contains("\"event\": \"error\"") {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(&line) {
                let detail = v["message"].as_str().unwrap_or("unknown error");
                show_error(&app, &format!("Delta Filings could not start: {detail}. Choose Help, Open Logs Folder, and send backend.log to support."));
                if let Some(logs) = v["logs_dir"].as_str() {
                    app.state::<ReadyState>().0.lock().unwrap().replace(Ready {
                        logs_dir: logs.to_string(),
                        ..Default::default()
                    });
                }
                failed = true;
            }
            continue;
        }
        if ready || !line.contains("\"event\": \"ready\"") {
            continue; // keep draining so the pipe never fills
        }
        let parsed: serde_json::Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(_) => continue,
        };
        if let Some(url) = parsed["url"].as_str().and_then(|u| Url::parse(u).ok()) {
            let text = |k: &str| parsed[k].as_str().unwrap_or_default().to_string();
            let origin = format!("http://127.0.0.1:{}", url.port().unwrap_or(80));
            app.state::<ReadyState>().0.lock().unwrap().replace(Ready {
                origin,
                data_dir: text("data_dir"),
                logs_dir: text("logs_dir"),
                backups_dir: text("backups_dir"),
            });
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.navigate(url);
                ready = true;
            }
        }
    }
    // Output ended: the backend exited. If the app is quitting it already took the child.
    let taken = {
        let state = app.state::<Backend>();
        let mut slot = state.0.lock().unwrap();
        slot.take()
    };
    let Some(mut child) = taken else {
        return;
    };
    let _ = child.wait();
    if !ready && !failed {
        show_error(&app, "The local service stopped before it was ready. Restart Delta Filings. If it keeps happening, choose Help, Open Logs Folder, and send backend.log to support.");
        return;
    }
    if ready {
        // It was running and stopped unexpectedly: start it again a few times.
        let restarts = {
            let state = app.state::<Restarts>();
            let mut n = state.0.lock().unwrap();
            *n += 1;
            *n
        };
        if restarts <= MAX_RESTARTS {
            show_splash(&app);
            start_backend(app);
        } else {
            show_error(&app, "The local service keeps stopping. Quit and reopen Delta Filings. If it keeps happening, choose Help, Open Logs Folder, and send backend.log to support.");
        }
    }
}

fn main() {
    tauri::Builder::default()
        // First, so a second launch only brings this window forward. Two
        // backends on one data folder would fight over the database and logs.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.show();
                let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_opener::init())
        .manage(Backend(Mutex::new(None)))
        .manage(ReadyState(Mutex::new(None)))
        .manage(Splash(Mutex::new(None)))
        .manage(Restarts(Mutex::new(0)))
        .setup(|app| {
            let menu = build_menu(app.handle())?;
            app.set_menu(menu)?;
            app.on_menu_event(|app, event| on_menu(app, event.id().as_ref()));
            let opener = app.handle().clone();
            let popups = app.handle().clone();
            let downloads = app.path().download_dir().ok();
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Delta Filings")
                .inner_size(1360.0, 880.0)
                .min_inner_size(1024.0, 680.0)
                .on_navigation(move |url| {
                    if is_local(url) {
                        return true;
                    }
                    // Source filings and every outside link open in the user's browser.
                    open_outside(&opener, url);
                    false
                })
                // target="_blank" and window.open: Windows and Linux drop these
                // unless handled. App pages open in this window (View, Back
                // returns), everything else in the browser.
                .on_new_window(move |url, _features| {
                    if is_local(&url) && matches!(url.scheme(), "http" | "https") {
                        if let Some(w) = popups.get_webview_window("main") {
                            let _ = w.navigate(url);
                        }
                    } else {
                        open_outside(&popups, &url);
                    }
                    NewWindowResponse::Deny
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
            if let Ok(url) = window.url() {
                app.state::<Splash>().0.lock().unwrap().replace(url);
            }
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
