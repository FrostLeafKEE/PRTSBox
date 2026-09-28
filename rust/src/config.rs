//! Settings compatible with the Python application's data/config.json.

use anyhow::{Context, Result};
use base64::Engine;
use serde_json::{Map, Value, json};
use std::{fs, os::windows::ffi::OsStrExt, path::PathBuf};
use windows_sys::Win32::{
    Foundation::LocalFree,
    Security::Cryptography::{CRYPT_INTEGER_BLOB, CryptProtectData, CryptUnprotectData},
    Storage::FileSystem::{MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH, MoveFileExW},
};

#[derive(Clone)]
pub struct Config {
    pub path: PathBuf,
    pub data: Map<String, Value>,
}

impl Config {
    pub fn load() -> Result<Self> {
        let root = std::env::var_os("PRTSBOX_DATA_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                let executable = std::env::current_exe().unwrap_or_default();
                let installed = executable
                    .parent()
                    .unwrap_or(std::path::Path::new("."))
                    .join("data");
                if cfg!(debug_assertions) && !installed.exists() {
                    std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                        .parent()
                        .unwrap()
                        .join("data")
                } else {
                    installed
                }
            });
        let path = root.join("config.json");
        let mut data = defaults();
        if path.exists() {
            match fs::read(&path)
                .ok()
                .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
            {
                Some(Value::Object(map)) => data.extend(map),
                _ => eprintln!(
                    "设置文件读取失败，使用默认值；原文件已保留：{}",
                    path.display()
                ),
            }
        }
        let engine = match data
            .get("engine")
            .and_then(Value::as_str)
            .unwrap_or("local")
        {
            "local" | "openai" | "platform" => None,
            "local_model" | "llama" => Some("local"),
            "openai_compatible" => Some("openai"),
            _ => Some("local"),
        };
        if let Some(engine) = engine {
            data.insert("engine".into(), json!(engine));
        }
        Ok(Self { path, data })
    }

    pub fn translation_key(&self) -> String {
        let keys = [
            "engine",
            "source_language",
            "target_language",
            "local_model",
            "local_runtime_variant",
            "translation_platform",
            "openai_base_url",
            "openai_model",
            "openai_api_key_dpapi",
            "azure_endpoint",
            "azure_region",
            "azure_api_key_dpapi",
            "deepl_plan",
            "deepl_api_key_dpapi",
            "baidu_app_id",
            "baidu_secret_key_dpapi",
        ];
        self.key_for(&keys)
    }
    fn key_for(&self, keys: &[&str]) -> String {
        serde_json::to_string(
            &keys
                .iter()
                .map(|key| self.data.get(*key))
                .collect::<Vec<_>>(),
        )
        .unwrap()
    }
    pub fn processing_key(&self) -> String {
        format!(
            "{}:{}",
            self.translation_key(),
            self.key_for(&[
                "window_hwnd",
                "region_bottom_only",
                "region_bottom_percent",
                "ocr_backend",
                "skip_chinese"
            ])
        )
    }

    pub fn get(&self, key: &str) -> &str {
        self.data.get(key).and_then(Value::as_str).unwrap_or("")
    }

    pub fn bool(&self, key: &str) -> bool {
        self.data.get(key).and_then(Value::as_bool).unwrap_or(false)
    }
    pub fn int(&self, key: &str) -> i64 {
        self.data.get(key).and_then(Value::as_i64).unwrap_or(0)
    }
    pub fn set(&mut self, key: &str, value: Value) {
        self.data.insert(key.into(), value);
    }

    pub fn secret(&self, key: &str) -> String {
        let Some(encoded) = self
            .data
            .get(&format!("{key}_dpapi"))
            .and_then(Value::as_str)
        else {
            return String::new();
        };
        let Ok(mut input) = base64::engine::general_purpose::STANDARD.decode(encoded) else {
            return String::new();
        };
        let mut entropy = b"PRTSBox.credentials.v1".to_vec();
        let input_blob = CRYPT_INTEGER_BLOB {
            cbData: input.len() as u32,
            pbData: input.as_mut_ptr(),
        };
        let entropy_blob = CRYPT_INTEGER_BLOB {
            cbData: entropy.len() as u32,
            pbData: entropy.as_mut_ptr(),
        };
        let mut output = CRYPT_INTEGER_BLOB::default();
        // SAFETY: buffers remain alive for the call; DPAPI allocates output, freed by LocalFree.
        unsafe {
            if CryptUnprotectData(
                &input_blob,
                std::ptr::null_mut(),
                &entropy_blob,
                std::ptr::null(),
                std::ptr::null(),
                1,
                &mut output,
            ) == 0
            {
                return String::new();
            }
            let bytes = std::slice::from_raw_parts(output.pbData, output.cbData as usize);
            let result = String::from_utf8_lossy(bytes).into_owned();
            LocalFree(output.pbData.cast());
            result
        }
    }

    pub fn set_secret(&mut self, key: &str, value: &str) -> Result<()> {
        let storage = format!("{key}_dpapi");
        if value.is_empty() {
            self.data.remove(&storage);
            return Ok(());
        }
        let mut input = value.as_bytes().to_vec();
        let mut entropy = b"PRTSBox.credentials.v1".to_vec();
        let input_blob = CRYPT_INTEGER_BLOB {
            cbData: input.len() as u32,
            pbData: input.as_mut_ptr(),
        };
        let entropy_blob = CRYPT_INTEGER_BLOB {
            cbData: entropy.len() as u32,
            pbData: entropy.as_mut_ptr(),
        };
        let mut output = CRYPT_INTEGER_BLOB::default();
        let description: Vec<u16> = "PRTSBox\0".encode_utf16().collect();
        // SAFETY: pointers reference live buffers; DPAPI owns output until LocalFree.
        unsafe {
            if CryptProtectData(
                &input_blob,
                description.as_ptr(),
                &entropy_blob,
                std::ptr::null(),
                std::ptr::null(),
                1,
                &mut output,
            ) == 0
            {
                anyhow::bail!("Windows DPAPI 加密失败");
            }
            let bytes = std::slice::from_raw_parts(output.pbData, output.cbData as usize);
            let encoded = base64::engine::general_purpose::STANDARD.encode(bytes);
            LocalFree(output.pbData.cast());
            self.data.insert(storage, json!(encoded));
        }
        Ok(())
    }

    pub fn save(&self) -> Result<()> {
        let parent = self.path.parent().context("设置路径无父目录")?;
        fs::create_dir_all(parent)?;
        let temp = self.path.with_extension("json.tmp");
        fs::write(&temp, serde_json::to_vec_pretty(&self.data)?)?;
        let from = temp
            .as_os_str()
            .encode_wide()
            .chain(Some(0))
            .collect::<Vec<_>>();
        let to = self
            .path
            .as_os_str()
            .encode_wide()
            .chain(Some(0))
            .collect::<Vec<_>>();
        // SAFETY: both paths are valid NUL-terminated UTF-16 and live through the call.
        if unsafe {
            MoveFileExW(
                from.as_ptr(),
                to.as_ptr(),
                MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
            )
        } == 0
        {
            return Err(std::io::Error::last_os_error()).context("保存设置失败");
        }
        Ok(())
    }
}

fn defaults() -> Map<String, Value> {
    json!({
        "engine":"local", "source_language":"auto", "target_language":"zh-CN",
        "layout_mode":"below", "overlay_font_size":14, "show_latency":false,
        "show_source_text":false, "overlay_capturable":false, "skip_chinese":true,
        "region_bottom_only":false, "region_bottom_percent":30, "theme":"dark",
        "local_model":"hy-mt2-1.8b", "local_runtime_variant":"auto",
        "ocr_backend":"auto", "download_source":"auto",
        "openai_base_url":"https://api.openai.com/v1", "openai_model":"gpt-4o-mini",
        "translation_platform":"azure", "azure_endpoint":"https://api.cognitive.microsofttranslator.com",
        "azure_region":"", "deepl_plan":"free", "baidu_app_id":"",
        "window_hwnd":0, "window_title":"", "pet_visible":false
    }).as_object().unwrap().clone()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn defaults_match_existing_config() {
        let data = defaults();
        assert_eq!(data["engine"], "local");
        assert_eq!(data["region_bottom_percent"], 30);
        assert_eq!(data["translation_platform"], "azure");
    }
    #[test]
    fn dpapi_round_trip_and_atomic_replacement() {
        let path = std::env::temp_dir()
            .join(format!("prtsbox-rust-config-{}", std::process::id()))
            .join("config.json");
        let mut config = Config {
            path: path.clone(),
            data: defaults(),
        };
        config.set_secret("openai_api_key", "一个测试密钥").unwrap();
        assert_eq!(config.secret("openai_api_key"), "一个测试密钥");
        config.save().unwrap();
        config.set("target_language", json!("ja"));
        config.save().unwrap();
        let read: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        assert_eq!(read["target_language"], "ja");
        assert!(read.get("openai_api_key").is_none());
        let _ = fs::remove_file(&path);
        let _ = fs::remove_dir(path.parent().unwrap());
    }
}

#[cfg(test)]
#[test]
fn display_settings_preserve_processing_cache() {
    let mut config = Config {
        path: PathBuf::new(),
        data: defaults(),
    };
    let original = config.processing_key();
    for (key, value) in [
        ("theme", json!("light")),
        ("overlay_font_size", json!(22)),
        ("show_source_text", json!(true)),
        ("layout_mode", json!("above")),
    ] {
        config.set(key, value);
        assert_eq!(original, config.processing_key());
    }
    let translation = config.translation_key();
    config.set("region_bottom_percent", json!(55));
    assert_ne!(original, config.processing_key());
    assert_eq!(translation, config.translation_key());
    config.set("source_language", json!("ja"));
    assert_ne!(translation, config.translation_key());
}
