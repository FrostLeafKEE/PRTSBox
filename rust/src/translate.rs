//! Network translation providers; settings and DPAPI keys are shared with Python.

use crate::{cancel::CancelToken, config::Config, local::LocalServer};
use anyhow::{Context, Result, bail};
use reqwest::{Client, Url};
use serde_json::{Value, json};
use std::{
    sync::{Mutex, OnceLock},
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

pub fn translate(
    config: &Config,
    texts: &[String],
    local: &mut LocalServer,
) -> Result<Vec<String>> {
    translate_cancellable(config, texts, local, &CancelToken::default())
}

pub fn translate_cancellable(
    config: &Config,
    texts: &[String],
    local: &mut LocalServer,
    token: &CancelToken,
) -> Result<Vec<String>> {
    token.check()?;
    if texts.is_empty() {
        return Ok(Vec::new());
    }
    match config.get("engine") {
        "openai" => translate_openai(config, texts, token),
        "platform" => translate_platform(config, texts, token),
        "local" => translate_local(config, texts, local, token),
        other => bail!("未知翻译引擎：{other}"),
    }
}

fn translate_platform(
    config: &Config,
    texts: &[String],
    token: &CancelToken,
) -> Result<Vec<String>> {
    let provider = config.get("translation_platform");
    if !["azure", "deepl", "baidu"].contains(&provider) {
        bail!("请选择有效的翻译平台");
    }
    let limit = if provider == "baidu" { 900 } else { 20_000 };
    let mut output = Vec::with_capacity(texts.len());
    let mut offset = 0;
    while offset < texts.len() {
        let mut end = offset;
        let mut bytes = 0;
        while end < texts.len() && end - offset < 50 {
            let next = texts[end].len() + 1;
            if next > limit {
                bail!("单段文字超过翻译平台限制，请缩小识别区域");
            }
            if bytes + next > limit && end > offset {
                break;
            }
            bytes += next;
            end += 1;
        }
        let part = &texts[offset..end];
        output.extend(match provider {
            "azure" => translate_azure(config, part, token)?,
            "deepl" => translate_deepl(config, part, token)?,
            _ => translate_baidu(config, part, token)?,
        });
        offset = end;
    }
    checked_output(output, texts.len())
}

fn client(seconds: u64) -> Result<Client> {
    Ok(Client::builder()
        .connect_timeout(Duration::from_secs(5))
        .timeout(Duration::from_secs(seconds))
        .build()?)
}

fn send_json(request: reqwest::RequestBuilder, token: &CancelToken) -> Result<Value> {
    token.check()?;
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    runtime.block_on(async {
        let future = async {
            let response = request.send().await.context("翻译接口连接失败")?;
            let status = response.status();
            if !status.is_success() {
                bail!("翻译接口返回 HTTP {status}");
            }
            response.json().await.context("翻译接口返回无效 JSON")
        };
        let mut future = std::pin::pin!(future);
        loop {
            token.check()?;
            if let Ok(result) =
                tokio::time::timeout(Duration::from_millis(25), future.as_mut()).await
            {
                token.check()?;
                return result;
            }
        }
    })
}

fn source_instruction(config: &Config) -> String {
    match config.get("source_language") {
        "" | "auto" => "自动识别源语言。".into(),
        code => format!("源语言为{}。", language_name(code)),
    }
}

fn checked_output(output: Vec<String>, expected: usize) -> Result<Vec<String>> {
    if output.len() != expected || output.iter().any(|text| text.trim().is_empty()) {
        bail!("译文数量或内容异常，已停止本帧以避免错位");
    }
    Ok(output)
}

fn language(provider: &str, code: &str, source: bool) -> String {
    match provider {
        "azure" => match code {
            "zh-CN" => "zh-Hans",
            "zh-TW" => "zh-Hant",
            _ => code,
        }
        .into(),
        "baidu" => match code {
            "zh-CN" => "zh",
            "zh-TW" => "cht",
            "ja" => "jp",
            "ko" => "kor",
            "fr" => "fra",
            "es" => "spa",
            "ar" => "ara",
            "vi" => "vie",
            _ => code,
        }
        .into(),
        _ => match code {
            "zh-CN" if !source => "ZH-HANS".into(),
            "zh-TW" if !source => "ZH-HANT".into(),
            "zh-CN" | "zh-TW" => "ZH".into(),
            _ => code.to_ascii_uppercase(),
        },
    }
}

fn language_name(code: &str) -> &str {
    match code {
        "zh-CN" => "简体中文",
        "zh-TW" => "繁体中文",
        "en" => "英语",
        "ja" => "日语",
        "ko" => "韩语",
        "fr" => "法语",
        "de" => "德语",
        "es" => "西班牙语",
        "pt" => "葡萄牙语",
        "ru" => "俄语",
        "it" => "意大利语",
        "ar" => "阿拉伯语",
        "th" => "泰语",
        "vi" => "越南语",
        _ => code,
    }
}

fn translate_azure(config: &Config, texts: &[String], token: &CancelToken) -> Result<Vec<String>> {
    let key = config.secret("azure_api_key");
    if key.is_empty() {
        bail!("请在设置中填写 Azure API Key");
    }
    let endpoint = if config.get("azure_endpoint").is_empty() {
        "https://api.cognitive.microsofttranslator.com"
    } else {
        config.get("azure_endpoint")
    }
    .trim_end_matches('/');
    let parsed = Url::parse(endpoint).context("Azure 接口地址格式无效")?;
    if parsed.scheme() != "https"
        || parsed.host_str().is_none()
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
    {
        bail!("Azure 接口地址须为不含密钥或查询参数的 HTTPS 地址");
    }
    let url = if endpoint.ends_with("/translate") {
        endpoint.to_owned()
    } else if parsed.path() == "/"
        && parsed
            .host_str()
            .unwrap_or("")
            .contains(".cognitiveservices.")
    {
        format!("{endpoint}/translator/text/v3.0/translate")
    } else {
        format!("{endpoint}/translate")
    };
    let mut request = client(30)?
        .post(url)
        .header("Ocp-Apim-Subscription-Key", key)
        .query(&[
            ("api-version", "3.0"),
            (
                "to",
                language("azure", config.get("target_language"), false).as_str(),
            ),
        ]);
    let source = language("azure", config.get("source_language"), true);
    if source != "auto" {
        request = request.query(&[("from", &source)]);
    }
    if !config.get("azure_region").is_empty() {
        request = request.header("Ocp-Apim-Subscription-Region", config.get("azure_region"));
    }
    let body: Vec<_> = texts.iter().map(|text| json!({"Text":text})).collect();
    let data = send_json(request.json(&body), token)?;
    let output = data
        .as_array()
        .context("Azure 响应结构异常")?
        .iter()
        .map(|item| {
            item["translations"][0]["text"]
                .as_str()
                .unwrap_or("")
                .to_owned()
        })
        .collect();
    checked_output(output, texts.len())
}

fn translate_deepl(config: &Config, texts: &[String], token: &CancelToken) -> Result<Vec<String>> {
    let key = config.secret("deepl_api_key");
    if key.is_empty() {
        bail!("请在设置中填写 DeepL API Key");
    }
    let host = if config.get("deepl_plan") == "pro" {
        "api.deepl.com"
    } else {
        "api-free.deepl.com"
    };
    let mut body = json!({"text":texts, "target_lang":language("deepl", config.get("target_language"), false)});
    let source = language("deepl", config.get("source_language"), true);
    if source != "AUTO" {
        body["source_lang"] = json!(source);
    }
    let data = send_json(
        client(30)?
            .post(format!("https://{host}/v2/translate"))
            .header("Authorization", format!("DeepL-Auth-Key {key}"))
            .json(&body),
        token,
    )?;
    let output = data["translations"]
        .as_array()
        .context("DeepL 响应结构异常")?
        .iter()
        .map(|item| item["text"].as_str().unwrap_or("").to_owned())
        .collect();
    checked_output(output, texts.len())
}

fn translate_baidu(config: &Config, texts: &[String], token: &CancelToken) -> Result<Vec<String>> {
    let app_id = config.get("baidu_app_id");
    let secret = config.secret("baidu_secret_key");
    if app_id.is_empty() || secret.is_empty() {
        bail!("请在设置中填写百度翻译 APP ID 和密钥");
    }
    let query = texts.join("\n");
    if query.len() > 900 {
        bail!("文字超过百度单次请求限制，请缩小识别区域");
    }
    let salt = SystemTime::now()
        .duration_since(UNIX_EPOCH)?
        .as_nanos()
        .to_string();
    let sign = format!(
        "{:x}",
        md5::compute(format!("{app_id}{query}{salt}{secret}"))
    );
    let params = [
        ("q", query),
        (
            "from",
            language("baidu", config.get("source_language"), true),
        ),
        (
            "to",
            language("baidu", config.get("target_language"), false),
        ),
        ("appid", app_id.into()),
        ("salt", salt),
        ("sign", sign),
    ];
    static LAST: OnceLock<Mutex<Instant>> = OnceLock::new();
    let lock = LAST.get_or_init(|| Mutex::new(Instant::now() - Duration::from_secs(2)));
    let mut last = lock
        .lock()
        .map_err(|_| anyhow::anyhow!("百度翻译频率控制异常"))?;
    let wait = Duration::from_millis(1050).saturating_sub(last.elapsed());
    if !wait.is_zero() {
        token.sleep(wait)?;
    }
    *last = Instant::now();
    let data = send_json(
        client(30)?
            .post("https://fanyi-api.baidu.com/api/trans/vip/translate")
            .form(&params),
        token,
    )?;
    if let Some(error) = data["error_code"].as_str()
        && error != "52000"
    {
        bail!("百度翻译返回错误代码 {error}");
    }
    let output = data["trans_result"]
        .as_array()
        .context("百度翻译响应结构异常")?
        .iter()
        .map(|item| item["dst"].as_str().unwrap_or("").to_owned())
        .collect();
    checked_output(output, texts.len())
}

fn translate_openai(config: &Config, texts: &[String], token: &CancelToken) -> Result<Vec<String>> {
    let key = config.secret("openai_api_key");
    if key.is_empty() {
        bail!("请在设置中填写接口 API Key");
    }
    let model = config.get("openai_model");
    if model.is_empty() {
        bail!("请在设置中填写模型名称");
    }
    let base = config.get("openai_base_url").trim_end_matches('/');
    let url = Url::parse(&format!("{base}/chat/completions")).context("接口地址无效")?;
    if !["http", "https"].contains(&url.scheme()) {
        bail!("接口地址必须是 HTTP 或 HTTPS");
    }
    let prompt = format!(
        "{}把下面的文本逐条翻译为{}。只返回一个 JSON 字符串数组，元素数量和顺序必须与输入一致，不要解释。\n{}",
        source_instruction(config),
        language_name(config.get("target_language")),
        serde_json::to_string(texts)?
    );
    let mut body = json!({"model":model, "temperature":0,
        "messages":[{"role":"system","content":"你是实时屏幕翻译器，准确简洁地翻译文本。"},
                    {"role":"user","content":prompt}]});
    let lowered = model.to_ascii_lowercase().replace('_', "-");
    if lowered.starts_with("deepseek")
        || lowered.starts_with("kimi-k2.6")
        || lowered.starts_with("glm-")
    {
        body["thinking"] = json!({"type":"disabled"});
    } else if lowered.starts_with("minimax-m3") {
        body["reasoning"] = json!({"effort":"none"});
    }
    let data = send_json(client(45)?.post(url).bearer_auth(key).json(&body), token)?;
    let raw = data["choices"][0]["message"]["content"]
        .as_str()
        .context("AI 接口响应结构异常")?
        .trim();
    let stripped = raw
        .strip_prefix("```json")
        .or_else(|| raw.strip_prefix("```"))
        .unwrap_or(raw)
        .trim();
    let stripped = stripped.strip_suffix("```").unwrap_or(stripped).trim();
    checked_output(
        serde_json::from_str(stripped).context("AI 模型没有返回 JSON 译文数组")?,
        texts.len(),
    )
}

fn translate_local(
    config: &Config,
    texts: &[String],
    local: &mut LocalServer,
    token: &CancelToken,
) -> Result<Vec<String>> {
    let port = local.ensure(config, token)?;
    let client = client(60)?;
    let mut output = Vec::with_capacity(texts.len());
    for batch in texts.chunks(4) {
        token.check()?;
        let results = thread::scope(|scope| {
            let handles = batch.iter().map(|text| {
                let client = client.clone();
                let target = language_name(config.get("target_language")).to_owned();
                let source = source_instruction(config);
                scope.spawn(move || -> Result<String> {
                    let body = json!({"messages":[{"role":"user","content":format!(
                        "{source}将以下文本翻译为 {target}，注意只需要输出翻译后的结果，不要额外解释：\n\n{text}")}],
                        "temperature":0, "max_tokens":512});
                    let mut last = None;
                    for _ in 0..2 {
                        token.check()?;
                        let attempt = (|| -> Result<String> {
                            let data = send_json(client.post(format!("http://127.0.0.1:{port}/v1/chat/completions"))
                                .json(&body), token)?;
                            let content = data["choices"][0]["message"]["content"].as_str()
                                .context("本地模型响应结构异常")?.trim();
                            Ok(content.trim_matches(['"','\'','“','”','‘','’']).trim().to_owned())
                        })();
                        match attempt { Ok(value) => return Ok(value), Err(error) => last = Some(error) }
                    }
                    Err(last.unwrap())
                })
            }).collect::<Vec<_>>();
            handles
                .into_iter()
                .map(|handle| handle.join())
                .collect::<Vec<_>>()
        });
        for result in results {
            output.push(result.map_err(|_| anyhow::anyhow!("本地翻译线程失败"))??);
        }
    }
    checked_output(output, texts.len())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        io::{Read, Write},
        net::TcpListener,
        path::PathBuf,
        thread,
    };

    #[test]
    fn cancelled_http_releases_worker_before_server_responds() {
        use std::sync::{atomic::Ordering, mpsc};
        for partial_body in [false, true] {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let port = listener.local_addr().unwrap().port();
            let token = CancelToken::default();
            let epoch = token.epoch.clone();
            let (release, wait) = mpsc::channel();
            let server = thread::spawn(move || {
                let (mut socket, _) = listener.accept().unwrap();
                socket
                    .set_read_timeout(Some(Duration::from_secs(3)))
                    .unwrap();
                let mut buffer = [0; 4096];
                assert!(socket.read(&mut buffer).unwrap() > 0);
                if partial_body {
                    socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 1000\r\n\r\n{").unwrap();
                }
                thread::sleep(Duration::from_millis(100));
                epoch.store(1, Ordering::Release);
                let _ = wait.recv_timeout(Duration::from_secs(5));
            });
            let started = Instant::now();
            let result = send_json(
                client(30).unwrap().get(format!("http://127.0.0.1:{port}")),
                &token,
            );
            let elapsed = started.elapsed();
            let _ = release.send(());
            server.join().unwrap();
            assert!(result.unwrap_err().to_string().contains("取消"));
            assert!(elapsed < Duration::from_secs(2), "{elapsed:?}");
        }
    }

    #[test]
    fn model_prompts_include_explicit_source_language() {
        let mut config = Config {
            path: PathBuf::new(),
            data: serde_json::Map::new(),
        };
        config.set("source_language", json!("ja"));
        assert_eq!(source_instruction(&config), "源语言为日语。");
        config.set("source_language", json!("auto"));
        assert_eq!(source_instruction(&config), "自动识别源语言。");
    }

    #[test]
    fn openai_compatible_endpoint_preserves_batch_alignment() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            let mut request = Vec::new();
            let mut buffer = [0u8; 8192];
            let bytes = socket.read(&mut buffer).unwrap();
            request.extend_from_slice(&buffer[..bytes]);
            let text = String::from_utf8_lossy(&request);
            assert!(text.starts_with("POST /v1/chat/completions"));
            assert!(
                text.to_ascii_lowercase()
                    .contains("authorization: bearer test-key")
            );
            let payload =
                json!({"choices":[{"message":{"content":"```json\n[\"你好\",\"世界\"]\n```"}}]})
                    .to_string();
            write!(socket, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                payload.len(), payload).unwrap();
        });
        let mut config = Config {
            path: PathBuf::from("unused.json"),
            data: serde_json::Map::new(),
        };
        config.set("engine", json!("openai"));
        config.set("target_language", json!("zh-CN"));
        config.set(
            "openai_base_url",
            json!(format!("http://127.0.0.1:{port}/v1")),
        );
        config.set("openai_model", json!("mock-model"));
        config.set_secret("openai_api_key", "test-key").unwrap();
        let translated = translate(
            &config,
            &["hello".into(), "world".into()],
            &mut LocalServer::new(),
        )
        .unwrap();
        assert_eq!(translated, ["你好", "世界"]);
        server.join().unwrap();
    }
}
