//! llama.cpp child lifecycle. The Windows Job Object kills it if the GUI crashes.

use crate::{cancel::CancelToken, config::Config};
use anyhow::{Context, Result, bail};
use std::{
    fs::{self, File},
    mem::size_of,
    net::TcpListener,
    os::windows::{io::AsRawHandle, process::CommandExt},
    process::{Child, Command, Stdio},
    time::{Duration, Instant},
};
use windows_sys::Win32::{
    Foundation::{CloseHandle, HANDLE},
    System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
        SetInformationJobObject,
    },
};

pub struct LocalServer {
    child: Option<Child>,
    job: Option<HANDLE>,
    port: u16,
    model: String,
    runtime: String,
}

impl LocalServer {
    pub fn new() -> Self {
        Self {
            child: None,
            job: None,
            port: 0,
            model: String::new(),
            runtime: String::new(),
        }
    }

    pub fn ensure(&mut self, config: &Config, token: &CancelToken) -> Result<u16> {
        token.check()?;
        let model = config.get("local_model");
        if self.model == model
            && self.runtime == config.get("local_runtime_variant")
            && self
                .child
                .as_mut()
                .is_some_and(|child| child.try_wait().ok().flatten().is_none())
        {
            return Ok(self.port);
        }
        self.stop();
        let filename = match model {
            "hy-mt2-7b" => "Hy-MT2-7B-Q4_K_M.gguf",
            _ => "Hy-MT2-1.8B-Q4_K_M.gguf",
        };
        let root = config.path.parent().context("设置路径无父目录")?;
        let model_path = root.join("models").join(filename);
        if !model_path.is_file() {
            bail!("本地模型未安装：请先下载 {filename}");
        }
        let requested = config.get("local_runtime_variant");
        let variants = if requested == "auto" {
            vec!["vulkan", "hip", "cpu"]
        } else {
            vec![requested, "vulkan", "hip", "cpu"]
        };
        let mut seen = std::collections::HashSet::new();
        let executables = variants
            .iter()
            .filter(|variant| seen.insert(**variant))
            .map(|variant| root.join("runtime").join(variant).join("llama-server.exe"))
            .filter(|path| path.is_file())
            .collect::<Vec<_>>();
        if executables.is_empty() {
            bail!("本地运行时未安装，请在设置中下载 llama.cpp");
        }
        let mut errors = Vec::new();
        for executable in executables {
            token.check()?;
            match self.start(&executable, &model_path, root, model, token) {
                Ok(port) => {
                    self.runtime = requested.into();
                    return Ok(port);
                }
                Err(error) => {
                    errors.push(format!("{}：{error:#}", executable.display()));
                    self.stop();
                }
            }
        }
        bail!("已安装的运行时均无法启动：{}", errors.join("；"))
    }

    fn start(
        &mut self,
        executable: &std::path::Path,
        model_path: &std::path::Path,
        root: &std::path::Path,
        model: &str,
        token: &CancelToken,
    ) -> Result<u16> {
        token.check()?;
        crate::downloads::verify_runtime(executable.parent().context("运行时路径无效")?)?;
        token.check()?;
        let listener = TcpListener::bind("127.0.0.1:0")?;
        let port = listener.local_addr()?.port();
        drop(listener);
        let log_path = root.join("logs").join("llama-server.log");
        fs::create_dir_all(log_path.parent().unwrap())?;
        let stdout = File::create(log_path)?;
        let stderr = stdout.try_clone()?;
        let mut child = Command::new(executable)
            .args([
                "-m",
                model_path.to_str().context("模型路径无效")?,
                "--host",
                "127.0.0.1",
                "--port",
                &port.to_string(),
                "-ngl",
                "99",
                "-c",
                "4096",
                "-np",
                "4",
            ])
            .current_dir(executable.parent().unwrap())
            .stdin(Stdio::null())
            .stdout(Stdio::from(stdout))
            .stderr(Stdio::from(stderr))
            .creation_flags(0x08000000)
            .spawn()
            .context("启动 llama-server 失败")?;
        let job = unsafe { create_job(&child) };
        let Some(job) = job else {
            let _ = child.kill();
            bail!("无法将本地模型进程绑定到 Windows Job Object");
        };
        self.child = Some(child);
        self.job = Some(job);
        self.port = port;
        self.model = model.into();
        let deadline = Instant::now() + Duration::from_secs(180);
        let client = reqwest::blocking::Client::builder()
            .timeout(Duration::from_millis(250))
            .build()?;
        while Instant::now() < deadline {
            token.check()?;
            if self
                .child
                .as_mut()
                .is_some_and(|child| child.try_wait().ok().flatten().is_some())
            {
                self.stop();
                bail!("llama-server 启动后退出，请查看 data/logs/llama-server.log");
            }
            if client
                .get(format!("http://127.0.0.1:{port}/health"))
                .send()
                .is_ok_and(|response| response.status().is_success())
            {
                return Ok(port);
            }
            token.sleep(Duration::from_millis(250))?;
        }
        self.stop();
        bail!("本地模型 180 秒内未就绪，请查看 data/logs/llama-server.log")
    }

    pub fn stop(&mut self) {
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
        if let Some(job) = self.job.take() {
            unsafe {
                CloseHandle(job);
            }
        }
        self.port = 0;
        self.model.clear();
        self.runtime.clear();
    }
}

impl Drop for LocalServer {
    fn drop(&mut self) {
        self.stop();
    }
}

unsafe fn create_job(child: &Child) -> Option<HANDLE> {
    // SAFETY: valid child handle; job is closed on every failure and in Drop.
    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            return None;
        }
        let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            (&info as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
            size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        ) == 0
            || AssignProcessToJobObject(job, child.as_raw_handle()) == 0
        {
            CloseHandle(job);
            return None;
        }
        Some(job)
    }
}
