mod cancel;
mod capture;
mod config;
mod downloads;
mod hotkey;
mod local;
mod ocr;
mod pipeline;
mod textproc;
mod translate;
mod ui;

fn main() -> anyhow::Result<()> {
    // Keep CLI diagnostics and the GUI in the same physical-pixel coordinate space.
    unsafe {
        windows_sys::Win32::UI::HiDpi::SetProcessDpiAwarenessContext(
            windows_sys::Win32::UI::HiDpi::DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
        );
    }
    let mut args = std::env::args().skip(1);
    match args.next().as_deref() {
        Some("--list-windows") => {
            for window in capture::list_windows() {
                println!(
                    "{}\t{}\t{}x{}",
                    window.hwnd, window.title, window.width, window.height
                );
            }
        }
        Some("--debug-windows") => {
            let pid: u32 = args
                .next()
                .ok_or_else(|| anyhow::anyhow!("missing PID"))?
                .parse()?;
            for window in capture::debug_windows_for_pid(pid) {
                println!("{window}");
            }
        }
        Some("--debug-geometry") => {
            for arg in args {
                let hwnd: isize = arg.parse()?;
                println!("{}", capture::debug_geometry(hwnd));
            }
        }
        Some("--close-window") => {
            let hwnd: isize = args
                .next()
                .ok_or_else(|| anyhow::anyhow!("missing HWND"))?
                .parse()?;
            capture::request_close(hwnd);
        }
        Some("--capture-window") => {
            let hwnd: isize = args
                .next()
                .ok_or_else(|| anyhow::anyhow!("missing HWND"))?
                .parse()?;
            let output = args
                .next()
                .ok_or_else(|| anyhow::anyhow!("missing output PNG path"))?;
            let window =
                capture::window_info(hwnd).ok_or_else(|| anyhow::anyhow!("window unavailable"))?;
            capture::capture_window(&window)?.save(&output)?;
            println!("saved {output}");
        }
        Some("--show-config") => {
            let config = config::Config::load()?;
            println!("{}", config.path.display());
            for (key, value) in &config.data {
                if !key.ends_with("_dpapi") {
                    println!("{key}: {value}");
                }
            }
        }
        Some("--translate") => {
            let text = args.collect::<Vec<_>>().join(" ");
            let config = config::Config::load()?;
            for result in translate::translate(&config, &[text], &mut local::LocalServer::new())? {
                println!("{result}");
            }
        }
        Some("--translate-batch") => {
            let texts = args.collect::<Vec<_>>();
            if texts.is_empty() {
                anyhow::bail!("provide one or more source texts");
            }
            let config = config::Config::load()?;
            for (source, result) in texts.iter().zip(translate::translate(
                &config,
                &texts,
                &mut local::LocalServer::new(),
            )?) {
                println!("{source}\t{result}");
            }
        }
        Some("--download-runtime") => {
            let variant = args.next().unwrap_or_else(|| "vulkan".into());
            let config = config::Config::load()?;
            let (sender, receiver) = std::sync::mpsc::channel();
            let task =
                std::thread::spawn(move || downloads::download_runtime(&config, &variant, &sender));
            for progress in receiver {
                println!("{progress}");
            }
            println!(
                "{}",
                task.join()
                    .map_err(|_| anyhow::anyhow!("下载线程失败"))??
                    .display()
            );
        }
        Some("--ocr-image") => {
            let path = args
                .next()
                .ok_or_else(|| anyhow::anyhow!("missing image path"))?;
            let frame = image::open(path)?.into_rgba8();
            let assets = pipeline::assets_dir();
            let mut service = ocr::Ocr::load(&assets)?;
            for line in service.recognize(&frame)? {
                println!("{:.2}\t{:?}\t{}", line.score, line.bounds, line.text);
            }
        }
        Some("--live-window") => {
            let hwnd: isize = args
                .next()
                .ok_or_else(|| anyhow::anyhow!("missing HWND"))?
                .parse()?;
            ui::run_with_target(Some(hwnd))?;
        }
        _ => ui::run()?,
    }
    Ok(())
}
