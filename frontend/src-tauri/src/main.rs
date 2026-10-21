#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use serde_json::Value;
use std::{
    fs::{self, OpenOptions},
    io::{Read, Write},
    net::{SocketAddr, TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::Command,
    sync::Mutex,
    thread,
    time::Duration,
};
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use tauri::{AppHandle, Emitter, Manager, RunEvent, State};
use tauri_plugin_shell::{process::CommandChild, process::CommandEvent, ShellExt};

const VERSION: &str = env!("CARGO_PKG_VERSION");
const STANDARD_MODEL: &str = "qwen3:8b";
const LOW_VRAM_MODEL: &str = "qwen3:4b";
const STANDARD_VRAM_THRESHOLD_MIB: u64 = 7168;
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct DesktopBootstrap {
    desktop: bool,
    version: String,
    base_url: String,
    session_token: String,
    runtime_state: String,
    app_data_dir: String,
    workspace_dir: String,
    logs_dir: String,
    selected_model: String,
    model_mode: String,
    low_vram_mode: bool,
    detected_vram_mib: u64,
    ollama_base_url: String,
    ollama_state: String,
    model_state: String,
    model_setup_required: bool,
    ollama_models_root: String,
    ollama_owned: bool,
}

struct RuntimeProcess {
    child: Option<CommandChild>,
    bootstrap: DesktopBootstrap,
}

// None means the owned scientific sidecar is unavailable or has stopped.
struct RuntimeState(Mutex<Option<RuntimeProcess>>);

struct DesktopPaths {
    install_dir: PathBuf,
    resource_dir: PathBuf,
    app_data_dir: PathBuf,
    workspace_dir: PathBuf,
    logs_dir: PathBuf,
    kb_dir: PathBuf,
}

// Tauri may return Windows verbatim paths (\\?\...). The bundled COMSOL JVM
// rejects that spelling in a Java classpath, even though Windows accepts it.
// Convert only the two verbatim forms we know how to preserve exactly; all
// ordinary paths pass through unchanged before reaching the Runtime sidecar.
fn java_compatible_path(path: &Path) -> PathBuf {
    let raw = path.to_string_lossy();
    if let Some(rest) = raw.strip_prefix(r"\\?\UNC\") {
        return PathBuf::from(format!(r"\\{rest}"));
    }
    if let Some(rest) = raw.strip_prefix(r"\\?\") {
        return PathBuf::from(rest);
    }
    path.to_path_buf()
}

impl DesktopPaths {
    fn discover(app: &AppHandle) -> Result<Self, String> {
        let install_dir = java_compatible_path(std::env::current_exe()
            .map_err(|error| error.to_string())?
            .parent()
            .ok_or("Installed executable has no parent directory")?
        );
        let resource_dir = java_compatible_path(&app.path().resource_dir().map_err(|error| error.to_string())?);
        let default_app_data_dir = java_compatible_path(&app.path().app_local_data_dir().map_err(|error| error.to_string())?);
        let app_data_dir = std::env::var_os("COMSOL_WORKBENCH_APP_DATA_DIR")
            .map(|value| java_compatible_path(Path::new(&value)))
            .unwrap_or(default_app_data_dir);
        let workspace_dir = std::env::var_os("COMSOL_WORKBENCH_WORKSPACE_DIR")
            .map(|value| java_compatible_path(Path::new(&value)))
            .unwrap_or_else(|| app_data_dir.join("workspace"));
        let logs_dir = workspace_dir.join("logs");
        let kb_dir = resource_dir.join("resources").join("knowledge_base");
        for directory in [&app_data_dir, &workspace_dir, &logs_dir] {
            fs::create_dir_all(directory).map_err(|error| format!("{}: {error}", directory.display()))?;
        }
        Ok(Self { install_dir, resource_dir, app_data_dir, workspace_dir, logs_dir, kb_dir })
    }
}

fn allocate_loopback_port() -> Result<u16, String> {
    let listener = TcpListener::bind(("127.0.0.1", 0)).map_err(|error| error.to_string())?;
    listener.local_addr().map(|address| address.port()).map_err(|error| error.to_string())
}

fn random_token() -> Result<String, String> {
    let mut bytes = [0_u8; 32];
    getrandom::fill(&mut bytes).map_err(|error| error.to_string())?;
    Ok(bytes.iter().map(|value| format!("{value:02x}")).collect())
}

fn append_runtime_log(path: &Path, stream: &str, bytes: &[u8], session_token: &str) {
    if let Ok(mut handle) = OpenOptions::new().create(true).append(true).open(path) {
        let mut value = String::from_utf8_lossy(bytes).trim_end().replace(session_token, "[REDACTED_SESSION_TOKEN]");
        let lowered = value.to_ascii_lowercase();
        if lowered.contains("authorization: bearer") || lowered.contains("api_key=") || lowered.contains("password=") {
            value = "[REDACTED_SECRET_BEARING_LOG_LINE]".to_string();
        }
        let _ = writeln!(handle, "[{stream}] {value}");
    }
}

fn detect_nvidia_vram_mib() -> u64 {
    // A console child briefly flashes over a windowed Tauri app on Windows.
    // Keep the probe silent; failure still means unknown VRAM, not GPU success.
    let mut command = Command::new("nvidia-smi.exe");
    command.args(["--query-gpu=memory.total", "--format=csv,noheader,nounits"]);
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    let output = command.output();
    let Ok(output) = output else { return 0 };
    if !output.status.success() { return 0 }
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter_map(|line| line.trim().parse::<u64>().ok())
        .max()
        .unwrap_or(0)
}

fn ollama_base_url() -> String {
    let raw = std::env::var("COMSOL_WORKBENCH_OLLAMA_BASE_URL")
        .or_else(|_| std::env::var("OLLAMA_HOST"))
        .unwrap_or_else(|_| "http://127.0.0.1:11434".to_string());
    let with_scheme = if raw.starts_with("http://") { raw } else { format!("http://{raw}") };
    with_scheme.trim_end_matches('/').to_string()
}

fn query_ollama_models(base_url: &str) -> Result<Vec<String>, String> {
    let authority = base_url
        .strip_prefix("http://")
        .ok_or("Only loopback HTTP Ollama endpoints are supported")?
        .split('/')
        .next()
        .ok_or("Invalid Ollama endpoint")?;
    let (host, port) = authority.rsplit_once(':').unwrap_or((authority, "11434"));
    if host != "127.0.0.1" && host != "localhost" && host != "[::1]" {
        return Err("Ollama endpoint must be loopback".to_string());
    }
    let port = port.parse::<u16>().map_err(|_| "Invalid Ollama port")?;
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_millis(800))
        .map_err(|error| error.to_string())?;
    stream.set_read_timeout(Some(Duration::from_secs(2))).map_err(|error| error.to_string())?;
    write!(stream, "GET /api/tags HTTP/1.1\r\nHost: {authority}\r\nConnection: close\r\n\r\n")
        .map_err(|error| error.to_string())?;
    let mut response = Vec::new();
    stream.read_to_end(&mut response).map_err(|error| error.to_string())?;
    let response = String::from_utf8(response).map_err(|error| error.to_string())?;
    let (headers, body) = response.split_once("\r\n\r\n").ok_or("Invalid Ollama response")?;
    if !headers.lines().next().unwrap_or("").contains(" 200 ") {
        return Err("Ollama tags endpoint did not return HTTP 200".to_string());
    }
    let value: Value = serde_json::from_str(body).map_err(|error| error.to_string())?;
    Ok(value.get("models").and_then(Value::as_array).into_iter().flatten().filter_map(|item| {
        item.get("name").or_else(|| item.get("model")).and_then(Value::as_str).map(str::to_string)
    }).collect())
}

fn spawn_runtime(app: &AppHandle) -> Result<RuntimeProcess, String> {
    let paths = DesktopPaths::discover(app)?;
    let ollama_base_url = ollama_base_url();
    let detected_vram_mib = detect_nvidia_vram_mib();
    let low_vram_mode = detected_vram_mib < STANDARD_VRAM_THRESHOLD_MIB;
    let selected_model = if low_vram_mode { LOW_VRAM_MODEL } else { STANDARD_MODEL }.to_string();
    let (ollama_state, available_models) = match query_ollama_models(&ollama_base_url) {
        Ok(models) => ("Detected".to_string(), models),
        Err(_) => ("Missing".to_string(), Vec::new()),
    };
    let model_available = available_models.iter().any(|value| value.eq_ignore_ascii_case(&selected_model));
    let model_state = if model_available { "Detected" } else { "Missing" }.to_string();
    let ollama_models_root = std::env::var("OLLAMA_MODELS").unwrap_or_else(|_| "Ollama default storage".to_string());
    let port = allocate_loopback_port()?;
    let token = random_token()?;
    let base_url = format!("http://127.0.0.1:{port}");
    let java_bridge_dir = paths.resource_dir.join("resources").join("java_bridge");
    let command = app
        .shell()
        .sidecar("scientific-runtime")
        .map_err(|error| error.to_string())?
        .args([
            "--desktop".to_string(),
            "--host".to_string(),
            "127.0.0.1".to_string(),
            "--port".to_string(),
            port.to_string(),
            "--parent-pid".to_string(),
            std::process::id().to_string(),
            "--owned-ollama-pid".to_string(),
            "0".to_string(),
            "--install-dir".to_string(),
            paths.install_dir.to_string_lossy().into_owned(),
            "--resource-dir".to_string(),
            paths.resource_dir.to_string_lossy().into_owned(),
            "--app-data-dir".to_string(),
            paths.app_data_dir.to_string_lossy().into_owned(),
            "--workspace-dir".to_string(),
            paths.workspace_dir.to_string_lossy().into_owned(),
            "--kb-dir".to_string(),
            paths.kb_dir.to_string_lossy().into_owned(),
            "--java-bridge-dir".to_string(),
            java_bridge_dir.to_string_lossy().into_owned(),
        ])
        .env("COMSOL_WORKBENCH_SESSION_TOKEN", &token)
        .env("COMSOL_WORKBENCH_PRODUCT_MODE", "rc_round1")
        .env("COMSOL_WORKBENCH_PRODUCT_MODEL", &selected_model)
        .env("COMSOL_WORKBENCH_MODEL_MODE", if low_vram_mode { "LOW_VRAM_MODE" } else { "STANDARD" })
        .env("COMSOL_WORKBENCH_LOW_VRAM_MODE", if low_vram_mode { "1" } else { "0" })
        .env("COMSOL_WORKBENCH_DETECTED_VRAM_MIB", detected_vram_mib.to_string())
        .env("COMSOL_WORKBENCH_OLLAMA_BASE_URL", &ollama_base_url);
    let command = if let Ok(models_root) = std::env::var("OLLAMA_MODELS") {
        command.env("OLLAMA_MODELS", models_root)
    } else {
        command
    };
    let (mut receiver, child) = match command.spawn() {
        Ok(value) => value,
        Err(error) => {
            return Err(error.to_string());
        }
    };
    let log_path = paths.logs_dir.join("scientific-runtime.log");
    let log_token = token.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = receiver.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => append_runtime_log(&log_path, "stdout", &bytes, &log_token),
                CommandEvent::Stderr(bytes) => append_runtime_log(&log_path, "stderr", &bytes, &log_token),
                CommandEvent::Error(error) => append_runtime_log(&log_path, "error", error.as_bytes(), &log_token),
                CommandEvent::Terminated(payload) => append_runtime_log(&log_path, "terminated", format!("{payload:?}").as_bytes(), &log_token),
                _ => {}
            }
        }
    });
    Ok(RuntimeProcess {
        child: Some(child),
        bootstrap: DesktopBootstrap {
            desktop: true,
            version: VERSION.to_string(),
            base_url,
            session_token: token,
            runtime_state: "starting".to_string(),
            app_data_dir: paths.app_data_dir.to_string_lossy().into_owned(),
            workspace_dir: paths.workspace_dir.to_string_lossy().into_owned(),
            logs_dir: paths.logs_dir.to_string_lossy().into_owned(),
            selected_model,
            model_mode: if low_vram_mode { "LOW_VRAM_MODE".to_string() } else { "STANDARD".to_string() },
            low_vram_mode,
            detected_vram_mib,
            ollama_base_url,
            ollama_state,
            model_state,
            model_setup_required: !model_available,
            ollama_models_root,
            ollama_owned: false,
        },
    })
}

fn request_shutdown(bootstrap: &DesktopBootstrap) {
    let Ok(address) = format!("127.0.0.1:{}", bootstrap.base_url.rsplit(':').next().unwrap_or("0")).parse::<SocketAddr>() else { return };
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(350)) else { return };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    let request = format!(
        "POST /api/desktop/shutdown HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Workbench-Token: {}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
        bootstrap.session_token
    );
    let _ = stream.write_all(request.as_bytes());
    let mut response = [0_u8; 256];
    let _ = stream.read(&mut response);
}

fn stop_owned_runtime(process: &mut RuntimeProcess) {
    request_shutdown(&process.bootstrap);
    thread::sleep(Duration::from_millis(800));
    if let Some(child) = process.child.take() {
        #[cfg(windows)]
        {
            // PyInstaller one-file executables use a bootloader parent plus the
            // actual runtime child. Reclaim only the exact tree whose root PID
            // was returned by the Workbench sidecar spawn.
            let owned_pid = child.pid().to_string();
            let tree_killed = Command::new("taskkill.exe")
                .args(["/PID", &owned_pid, "/T", "/F"])
                .creation_flags(CREATE_NO_WINDOW)
                .status()
                .map(|status| status.success())
                .unwrap_or(false);
            if !tree_killed {
                let _ = child.kill();
            }
        }
        #[cfg(not(windows))]
        {
            let _ = child.kill();
        }
    }
    process.bootstrap.runtime_state = "stopped".to_string();
}

#[tauri::command]
fn desktop_bootstrap(state: State<'_, RuntimeState>) -> Result<DesktopBootstrap, String> {
    let process = state.0.lock().map_err(|_| "Runtime state lock poisoned")?;
    match process.as_ref() {
        Some(active) => Ok(active.bootstrap.clone()),
        None => Err("Scientific Runtime unavailable; retry the owned desktop sidecar".into()),
    }
}

#[tauri::command]
fn restart_runtime(app: AppHandle, state: State<'_, RuntimeState>) -> Result<DesktopBootstrap, String> {
    let mut process = state.0.lock().map_err(|_| "Runtime state lock poisoned")?;
    if let Some(current) = process.as_mut() { stop_owned_runtime(current); }
    *process = Some(spawn_runtime(&app)?);
    Ok(process.as_ref().unwrap().bootstrap.clone())
}

#[tauri::command]
fn open_logs(state: State<'_, RuntimeState>) -> Result<(), String> {
    let process = state.0.lock().map_err(|_| "Runtime state lock poisoned")?;
    Command::new("explorer.exe")
        .arg(&process.as_ref().ok_or("Runtime unavailable")?.bootstrap.logs_dir)
        .spawn()
        .map(|_| ())
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn open_workspace(state: State<'_, RuntimeState>) -> Result<(), String> {
    // The folder is the already-resolved Workbench-owned workspace, not an
    // arbitrary frontend path. Explorer can view it without granting deletion.
    let process = state.0.lock().map_err(|_| "Runtime state lock poisoned")?;
    Command::new("explorer.exe")
        .arg(&process.as_ref().ok_or("Runtime unavailable")?.bootstrap.workspace_dir)
        .spawn()
        .map(|_| ())
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn open_imported_models(state: State<'_, RuntimeState>) -> Result<(), String> {
    // Fixed Workbench-owned subdirectory; the frontend cannot supply an
    // arbitrary path or redirect Explorer to another user's files.
    let process = state.0.lock().map_err(|_| "Runtime state lock poisoned")?;
    let folder = std::path::Path::new(&process.as_ref().ok_or("Runtime unavailable")?.bootstrap.workspace_dir)
        .join("models")
        .join("imported");
    std::fs::create_dir_all(&folder).map_err(|error| error.to_string())?;
    Command::new("explorer.exe")
        .arg(folder)
        .spawn()
        .map(|_| ())
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn open_config_backup(state: State<'_, RuntimeState>) -> Result<(), String> {
    let process = state.0.lock().map_err(|_| "Runtime state lock poisoned")?;
    Command::new("explorer.exe")
        .arg(&process.as_ref().ok_or("Runtime unavailable")?.bootstrap.app_data_dir)
        .spawn()
        .map(|_| ())
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn shutdown_desktop(app: AppHandle) {
    app.exit(0);
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    // Only the workbench's main window owns the Runtime lifetime.
                    // A pet-window close must not terminate a scientific run.
                    window.app_handle().exit(0);
                } else if window.label() == "sky-pet" {
                    api.prevent_close();
                    let _ = window.hide();
                    let _ = window.app_handle().emit_to("main", "sky-pet-hidden", ());
                }
            }
        })
        .setup(|app| {
            let process = spawn_runtime(app.handle()).ok();
            app.manage(RuntimeState(Mutex::new(process)));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![desktop_bootstrap, restart_runtime, open_logs, open_workspace, open_imported_models, open_config_backup, shutdown_desktop])
        .build(tauri::generate_context!())
        .expect("failed to build SKY Workbench desktop shell");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            if let Some(state) = app_handle.try_state::<RuntimeState>() {
                if let Ok(mut process) = state.0.lock() {
                    if let Some(current) = process.as_mut() { stop_owned_runtime(current); }
                }
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn random_port_is_loopback_and_available() {
        let port = allocate_loopback_port().expect("port");
        assert!(port > 0);
        assert!(TcpListener::bind(("127.0.0.1", port)).is_ok());
    }

    #[test]
    fn session_token_has_256_bits_of_hex() {
        let token = random_token().expect("token");
        assert_eq!(token.len(), 64);
        assert!(token.chars().all(|value| value.is_ascii_hexdigit()));
    }

    #[test]
    fn packaged_model_policy_never_selects_above_8b() {
        assert_eq!(STANDARD_MODEL, "qwen3:8b");
        assert_eq!(LOW_VRAM_MODEL, "qwen3:4b");
        assert!(STANDARD_VRAM_THRESHOLD_MIB >= 6144);
    }

    #[test]
    fn ollama_endpoint_defaults_to_local_service() {
        if std::env::var_os("COMSOL_WORKBENCH_OLLAMA_BASE_URL").is_none()
            && std::env::var_os("OLLAMA_HOST").is_none()
        {
            assert_eq!(ollama_base_url(), "http://127.0.0.1:11434");
        }
    }

    #[test]
    fn comsol_jvm_receives_normal_windows_paths() {
        assert_eq!(
            java_compatible_path(Path::new(r"\\?\E:\Workbench\resources\java_bridge")),
            PathBuf::from(r"E:\Workbench\resources\java_bridge")
        );
        assert_eq!(
            java_compatible_path(Path::new(r"\\?\UNC\server\share\bridge")),
            PathBuf::from(r"\\server\share\bridge")
        );
        assert_eq!(
            java_compatible_path(Path::new(r"E:\Workbench\workspace")),
            PathBuf::from(r"E:\Workbench\workspace")
        );
    }
}
