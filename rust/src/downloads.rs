//! Pinned local-model/runtime downloads, resumable and verified before install.

use crate::config::Config;
use anyhow::{Context, Result, bail};
use reqwest::blocking::Client;
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    os::windows::ffi::OsStrExt,
    path::{Path, PathBuf},
    sync::mpsc::Sender,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use windows_sys::Win32::Storage::FileSystem::{
    MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH, MoveFileExW,
};

struct ModelSpec {
    file: &'static str,
    size: u64,
    sha256: &'static str,
    repo: &'static str,
}
fn model(id: &str) -> Option<ModelSpec> {
    match id {
        "hy-mt2-1.8b" => Some(ModelSpec {
            file: "Hy-MT2-1.8B-Q4_K_M.gguf",
            size: 1_133_080_448,
            sha256: "dc5f44fcf1fa496ee7ad725982c0c8c553a4de00259b53af84c4b89fb0c06699",
            repo: "Hy-MT2-1.8B-GGUF",
        }),
        "hy-mt2-7b" => Some(ModelSpec {
            file: "Hy-MT2-7B-Q4_K_M.gguf",
            size: 4_624_648_896,
            sha256: "9f96256500f3fc1ab4d64336b58f52a949a95ad7516b0c229476eef782f9f77b",
            repo: "Hy-MT2-7B-GGUF",
        }),
        _ => None,
    }
}

fn http() -> Result<Client> {
    Ok(Client::builder()
        .connect_timeout(Duration::from_secs(10))
        .timeout(Duration::from_secs(120))
        .build()?)
}

fn download(urls: &[String], part: &Path, expected: u64, status: &Sender<String>) -> Result<()> {
    let client = http()?;
    for url in urls {
        let result = (|| -> Result<()> {
            let current = part.metadata().map(|meta| meta.len()).unwrap_or(0);
            let mut request = client.get(url);
            if current > 0 && current < expected {
                request = request.header("Range", format!("bytes={current}-"));
            }
            let mut response = request
                .send()
                .with_context(|| format!("连接下载源失败：{url}"))?;
            let resume =
                response.status() == reqwest::StatusCode::PARTIAL_CONTENT && current < expected;
            if !response.status().is_success() {
                bail!("下载源返回 HTTP {}", response.status());
            }
            let mut file = OpenOptions::new()
                .write(true)
                .create(true)
                .append(resume)
                .truncate(!resume)
                .open(part)?;
            let mut total = if resume { current } else { 0 };
            let mut buffer = [0u8; 256 * 1024];
            loop {
                let bytes = response.read(&mut buffer)?;
                if bytes == 0 {
                    break;
                }
                file.write_all(&buffer[..bytes])?;
                total += bytes as u64;
                if total % (16 * 1024 * 1024) < bytes as u64 {
                    let _ = status.send(format!(
                        "下载中：{:.1}%",
                        total as f64 * 100.0 / expected as f64
                    ));
                }
                if total > expected {
                    bail!("下载文件大于固定版本的预期大小");
                }
            }
            file.flush()?;
            if total != expected {
                bail!("下载未完成：{total}/{expected} 字节");
            }
            Ok(())
        })();
        if result.is_ok() {
            return result;
        }
        let _ = status.send(format!("下载源失败，尝试下一个：{}", result.unwrap_err()));
    }
    bail!("所有下载源均不可用，保留未完成文件供下次续传")
}

fn preference_urls(config: &Config, domestic: Vec<String>, official: Vec<String>) -> Vec<String> {
    match config.get("download_source") {
        "domestic" => domestic,
        "official" => official,
        _ => domestic.into_iter().chain(official).collect(),
    }
}

pub fn download_model(config: &Config, id: &str, status: &Sender<String>) -> Result<PathBuf> {
    let spec = model(id).context("未知本地模型")?;
    let root = config
        .path
        .parent()
        .context("配置路径无父目录")?
        .join("models");
    fs::create_dir_all(&root)?;
    let destination = root.join(spec.file);
    if destination.is_file()
        && destination.metadata()?.len() == spec.size
        && verify_sha256(&destination, spec.sha256)?
    {
        return Ok(destination);
    }
    let part = destination.with_extension("gguf.part");
    let domestic = vec![
        format!(
            "https://modelscope.cn/models/Tencent-Hunyuan/{}/resolve/master/{}",
            spec.repo, spec.file
        ),
        format!(
            "https://hf-mirror.com/tencent/{}/resolve/main/{}",
            spec.repo, spec.file
        ),
    ];
    let official = vec![format!(
        "https://huggingface.co/tencent/{}/resolve/main/{}",
        spec.repo, spec.file
    )];
    download(
        &preference_urls(config, domestic, official),
        &part,
        spec.size,
        status,
    )?;
    let _ = status.send("校验 SHA-256…".into());
    if !verify_sha256(&part, spec.sha256)? {
        bail!("模型 SHA-256 不匹配，已拒绝安装");
    }
    replace_file(&part, &destination)?;
    Ok(destination)
}

fn verify_sha256(path: &Path, expected: &str) -> Result<bool> {
    let mut file = File::open(path)?;
    let mut hash = Sha256::new();
    let mut buffer = [0u8; 1024 * 1024];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        hash.update(&buffer[..count]);
    }
    Ok(format!("{:x}", hash.finalize()) == expected)
}

pub fn download_runtime(
    config: &Config,
    variant: &str,
    status: &Sender<String>,
) -> Result<PathBuf> {
    let (asset, size, digest) = runtime_spec(variant)?;
    let root = config
        .path
        .parent()
        .context("配置路径无父目录")?
        .join("runtime");
    fs::create_dir_all(&root)?;
    let target = root.join(variant);
    if verify_runtime(&target).is_ok() {
        return Ok(target);
    }
    let archive = root.join(format!("{asset}.part"));
    let official =
        format!("https://github.com/ggml-org/llama.cpp/releases/download/b10227/{asset}");
    let domestic = vec![
        format!("https://gh-proxy.com/{official}"),
        format!("https://ghfast.top/{official}"),
    ];
    download(
        &preference_urls(config, domestic, vec![official]),
        &archive,
        size,
        status,
    )?;
    if !verify_sha256(&archive, digest)? {
        let _ = fs::remove_file(&archive);
        bail!("运行时 SHA-256 不匹配，已拒绝安装，请重新下载");
    }
    let _ = status.send("正在解压运行时…".into());
    let marker = SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos();
    let staging = root.join(format!(
        ".{variant}-installing-{}-{marker}",
        std::process::id()
    ));
    fs::create_dir_all(&staging)?;
    let mut zip = zip::ZipArchive::new(File::open(&archive)?)?;
    for index in 0..zip.len() {
        let mut entry = zip.by_index(index)?;
        let name = entry.enclosed_name().context("压缩包包含不安全路径")?;
        let path = staging.join(name);
        if entry.is_dir() {
            fs::create_dir_all(&path)?;
            continue;
        }
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut output = File::create(&path)?;
        std::io::copy(&mut entry, &mut output)?;
    }
    if !staging.join("llama-server.exe").is_file() {
        // Official releases sometimes nest all entries under a single directory.
        let found = find_server(&staging)?;
        let folder = found.parent().context("运行时目录异常")?.to_owned();
        for item in fs::read_dir(folder)? {
            let item = item?;
            fs::rename(item.path(), staging.join(item.file_name()))?;
        }
    }
    if !staging.join("llama-server.exe").is_file() {
        bail!("运行时压缩包中缺少 llama-server.exe");
    }
    let files = runtime_files(&staging)?;
    fs::write(
        staging.join("verified-runtime.json"),
        serde_json::to_vec(&serde_json::json!({
            "variant": variant, "archive_sha256": digest, "files": files
        }))?,
    )?;
    let backup = root.join(format!(".{variant}-previous-{marker}"));
    if target.exists() {
        fs::rename(&target, &backup)?;
    }
    if let Err(error) = fs::rename(&staging, &target) {
        if backup.exists() {
            let _ = fs::rename(&backup, &target);
        }
        return Err(error).context("安装运行时失败");
    }
    let _ = fs::remove_file(&archive);
    Ok(target)
}

fn replace_file(from: &Path, to: &Path) -> Result<()> {
    let from_wide = from
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect::<Vec<_>>();
    let to_wide = to
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect::<Vec<_>>();
    // SAFETY: both NUL-terminated paths are live through this Win32 call.
    if unsafe {
        MoveFileExW(
            from_wide.as_ptr(),
            to_wide.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    } == 0
    {
        return Err(std::io::Error::last_os_error()).context("替换下载文件失败");
    }
    Ok(())
}

fn find_server(dir: &Path) -> Result<PathBuf> {
    for item in fs::read_dir(dir)? {
        let path = item?.path();
        if path.is_file()
            && path
                .file_name()
                .is_some_and(|name| name == "llama-server.exe")
        {
            return Ok(path);
        }
        if path.is_dir()
            && let Ok(found) = find_server(&path)
        {
            return Ok(found);
        }
    }
    bail!("压缩包中缺少 llama-server.exe")
}

fn runtime_spec(variant: &str) -> Result<(&'static str, u64, &'static str)> {
    match variant {
        "vulkan" => Ok((
            "llama-b10227-bin-win-vulkan-x64.zip",
            34_102_057,
            "f6aeab1674c445d54b59d8ae5f7be581ebdae2aac74e523234d057386ae54184",
        )),
        "cpu" => Ok((
            "llama-b10227-bin-win-cpu-x64.zip",
            18_363_328,
            "fa78c20c800d32df50067afda92cc3380c0e62e783ce5c6bb9bbf864aa87c31a",
        )),
        "hip" => Ok((
            "llama-b10227-bin-win-hip-radeon-x64.zip",
            324_609_557,
            "c4c74d5128b3e2ed85ad9f7edb63069f5a8c04e632892eb47944166fbc95c835",
        )),
        _ => bail!("未知 llama.cpp 运行时"),
    }
}

fn hash_file(path: &Path) -> Result<String> {
    let mut hash = Sha256::new();
    let mut file = File::open(path)?;
    let mut buffer = [0; 65536];
    loop {
        let n = file.read(&mut buffer)?;
        if n == 0 {
            break;
        }
        hash.update(&buffer[..n]);
    }
    Ok(format!("{:x}", hash.finalize()))
}

fn runtime_files(root: &Path) -> Result<std::collections::BTreeMap<String, String>> {
    fn walk(
        root: &Path,
        dir: &Path,
        result: &mut std::collections::BTreeMap<String, String>,
    ) -> Result<()> {
        for item in fs::read_dir(dir)? {
            let path = item?.path();
            if path.is_dir() {
                walk(root, &path, result)?;
            } else if path
                .file_name()
                .is_none_or(|name| name != "verified-runtime.json")
            {
                result.insert(
                    path.strip_prefix(root)?.to_string_lossy().into_owned(),
                    hash_file(&path)?,
                );
            }
        }
        Ok(())
    }
    let mut result = std::collections::BTreeMap::new();
    walk(root, root, &mut result)?;
    Ok(result)
}

pub fn verify_runtime(root: &Path) -> Result<()> {
    let manifest: serde_json::Value = serde_json::from_slice(
        &fs::read(root.join("verified-runtime.json"))
            .context("运行时尚未校验，请在设置中重新下载运行时")?,
    )?;
    let variant = root
        .file_name()
        .and_then(|name| name.to_str())
        .context("运行时目录无效")?;
    let (_, _, digest) = runtime_spec(variant)?;
    if manifest["variant"].as_str() != Some(variant)
        || manifest["archive_sha256"].as_str() != Some(digest)
    {
        bail!("运行时校验记录版本不匹配，请重新下载");
    }
    let expected: std::collections::BTreeMap<String, String> =
        serde_json::from_value(manifest["files"].clone())?;
    if !expected.contains_key("llama-server.exe") || expected != runtime_files(root)? {
        bail!("运行时文件已变化，请在设置中重新下载运行时");
    }
    Ok(())
}

#[cfg(test)]
#[test]
fn runtime_hash_rejects_corruption_and_unverified_install() {
    let root = std::env::temp_dir().join(format!("prtsbox-hash-{}", std::process::id()));
    fs::create_dir_all(&root).unwrap();
    let path = root.join("llama-server.exe");
    fs::write(&path, b"abc").unwrap();
    let digest = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
    assert!(verify_sha256(&path, digest).unwrap());
    assert!(verify_runtime(&root).is_err());
    fs::write(&path, b"abd").unwrap();
    assert!(!verify_sha256(&path, digest).unwrap());
    fs::remove_file(path).unwrap();
    fs::remove_dir(root).unwrap();
}

#[cfg(test)]
#[test]
fn installed_runtime_manifest_detects_changed_and_extra_files() {
    let parent = std::env::temp_dir().join(format!("prtsbox-runtime-{}", std::process::id()));
    let root = parent.join("cpu");
    fs::create_dir_all(&root).unwrap();
    let exe = root.join("llama-server.exe");
    fs::write(&exe, b"fixture").unwrap();
    let files = runtime_files(&root).unwrap();
    fs::write(
        root.join("verified-runtime.json"),
        serde_json::to_vec(&serde_json::json!({
            "variant": "cpu", "archive_sha256": runtime_spec("cpu").unwrap().2, "files": files
        }))
        .unwrap(),
    )
    .unwrap();
    assert!(verify_runtime(&root).is_ok());
    fs::write(&exe, b"changed").unwrap();
    assert!(verify_runtime(&root).is_err());
    fs::write(&exe, b"fixture").unwrap();
    let extra = root.join("extra.dll");
    fs::write(&extra, b"extra").unwrap();
    assert!(verify_runtime(&root).is_err());
    for path in [exe, extra, root.join("verified-runtime.json")] {
        fs::remove_file(path).unwrap();
    }
    fs::remove_dir(root).unwrap();
    fs::remove_dir(parent).unwrap();
}
