"""Official Azure Translator, DeepL and Baidu text translation APIs."""
import hashlib
import secrets
import threading
import time
from urllib.parse import urlsplit

import requests

from .base import TranslationError, Translator
from ..models import LANGUAGES

PLATFORMS = {"azure": "微软 Azure Translator", "deepl": "DeepL API", "baidu": "百度翻译"}
PLATFORM_FIELDS = ("translation_platform", "azure_endpoint", "azure_region", "deepl_plan", "baidu_app_id")
PLATFORM_SECRETS = ("azure_api_key", "deepl_api_key", "baidu_secret_key")
AZURE_ENDPOINT = "https://api.cognitive.microsofttranslator.com"
_BAIDU_LANGS = {"zh-CN": "zh", "zh-TW": "cht", "ja": "jp", "ko": "kor",
                "fr": "fra", "es": "spa", "ar": "ara", "vi": "vie"}
_BAIDU_LOCK = threading.Lock()
_baidu_last_request = 0.0


def platform_credentials(store) -> dict[str, str]:
    return {**{key: str(store.get(key) or "") for key in PLATFORM_FIELDS},
            **{key: store.get_secret(key) for key in PLATFORM_SECRETS}}


class PlatformTranslator(Translator):
    def __init__(self, config):
        super().__init__(config)
        self.credentials = config.credentials
        self.provider = self.credentials.get("translation_platform", "azure") or "azure"

    def preflight(self) -> None:
        if self.provider not in PLATFORMS:
            raise TranslationError("请选择有效的翻译平台")
        required = {"azure": ("azure_api_key",), "deepl": ("deepl_api_key",),
                    "baidu": ("baidu_app_id", "baidu_secret_key")}[self.provider]
        if any(not self.credentials.get(key, "").strip() for key in required):
            raise TranslationError(f"请在设置 → 翻译平台中填写 {PLATFORMS[self.provider]} 的凭据")
        if self.config.source_language not in LANGUAGES or self.config.target_language not in LANGUAGES or self.config.target_language == "auto":
            raise TranslationError("请选择有效的源语言和目标语言")
        if self.provider == "azure":
            self._azure_url()
        if self.provider == "deepl" and self.credentials.get("deepl_plan", "free") not in {"free", "pro"}:
            raise TranslationError("请选择 DeepL API Free 或 Pro")

    def _azure_url(self) -> str:
        endpoint = (self.credentials.get("azure_endpoint") or AZURE_ENDPOINT).strip().rstrip("/")
        try:
            parts = urlsplit(endpoint)
        except ValueError:
            raise TranslationError("Azure 接口地址格式无效") from None
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise TranslationError("Azure 接口地址须为不含密钥或查询参数的 HTTPS 地址")
        if parts.path.endswith("/translate"):
            return endpoint
        if not parts.path and ".cognitiveservices." in parts.hostname:
            return endpoint + "/translator/text/v3.0/translate"
        return endpoint + "/translate"

    def _language(self, code: str, *, source: bool = False) -> str:
        if self.provider == "azure":
            return {"zh-CN": "zh-Hans", "zh-TW": "zh-Hant"}.get(code, code)
        if self.provider == "baidu":
            return _BAIDU_LANGS.get(code, code)
        if code in {"zh-CN", "zh-TW"}:
            return "ZH" if source else {"zh-CN": "ZH-HANS", "zh-TW": "ZH-HANT"}[code]
        return code.upper()

    def _post(self, url, **kwargs):
        try:
            response = requests.post(url, timeout=(5, 25), **kwargs)
        except requests.RequestException:
            raise TranslationError(f"{PLATFORMS[self.provider]} 连接失败或超时，请检查网络") from None
        if response.status_code >= 400:
            reason = {401: "凭据无效", 403: "凭据无效或无访问权限", 429: "请求过于频繁或额度不足",
                      456: "翻译额度已用完"}.get(response.status_code, "请求失败，请检查配置、语言及服务状态")
            raise TranslationError(f"{PLATFORMS[self.provider]}：{reason}（HTTP {response.status_code}）")
        try:
            return response.json()
        except ValueError:
            raise TranslationError("翻译平台返回了无效的 JSON") from None

    def translate_batch(self, texts: list[str]) -> list[str]:
        if not texts:
            return []
        self.preflight()
        results = list(texts)
        pending = [(i, " ".join(text.splitlines()).strip()) for i, text in enumerate(texts) if text.strip()]
        # Bound both element count and UTF-8 size; leave room for JSON overhead.
        limit = 900 if self.provider == "baidu" else 20000
        batches, batch, size = [], [], 0
        for index, text in pending:
            count = len(text.encode("utf-8")) + 1
            if count > limit:
                raise TranslationError("单段文字超过平台请求限制，请缩小识别区域")
            if batch and (len(batch) >= 50 or size + count > limit):
                batches.append(batch)
                batch, size = [], 0
            batch.append((index, text))
            size += count
        if batch:
            batches.append(batch)
        for batch in batches:
            output = self._translate([text for _, text in batch])
            if len(output) != len(batch) or any(not isinstance(t, str) or not t.strip() for t in output):
                raise TranslationError("平台返回的译文数量或内容异常，已停止本帧以避免译文错位")
            for (index, _), translation in zip(batch, output, strict=True):
                results[index] = translation
        return results

    def _translate(self, texts):
        source = self._language(self.config.source_language, source=True)
        target = self._language(self.config.target_language)
        try:
            if self.provider == "azure":
                headers = {"Ocp-Apim-Subscription-Key": self.credentials["azure_api_key"]}
                region = self.credentials.get("azure_region", "").strip()
                if region:
                    headers["Ocp-Apim-Subscription-Region"] = region
                params = {"api-version": "3.0", "to": target}
                if source != "auto":
                    params["from"] = source
                data = self._post(self._azure_url(), headers=headers, params=params,
                                  json=[{"Text": text} for text in texts])
                return [entry["translations"][0]["text"] for entry in data]
            if self.provider == "deepl":
                host = "api-free.deepl.com" if self.credentials.get("deepl_plan", "free") == "free" else "api.deepl.com"
                payload = {"text": texts, "target_lang": target}
                if source != "AUTO":
                    payload["source_lang"] = source
                data = self._post(f"https://{host}/v2/translate", json=payload,
                                  headers={"Authorization": f"DeepL-Auth-Key {self.credentials['deepl_api_key']}"})
                return [entry["text"] for entry in data["translations"]]
            query = "\n".join(texts)
            salt = secrets.token_hex(16)
            app_id = self.credentials["baidu_app_id"]
            sign = hashlib.md5((app_id + query + salt + self.credentials["baidu_secret_key"]).encode("utf-8")).hexdigest()
            # Conservative 1 QPS, shared across instances (one is made per frame).
            global _baidu_last_request
            with _BAIDU_LOCK:
                time.sleep(max(0.0, 1.05 - (time.monotonic() - _baidu_last_request)))
                _baidu_last_request = time.monotonic()
                data = self._post("https://fanyi-api.baidu.com/api/trans/vip/translate",
                                  data={"q": query, "from": source, "to": target,
                                        "appid": app_id, "salt": salt, "sign": sign})
            if "error_code" in data and str(data["error_code"]) != "52000":
                code = str(data["error_code"])
                reasons = {"52003": "APP ID 无效", "54001": "签名错误，请检查密钥",
                           "54003": "请求过于频繁", "54004": "账户余额不足",
                           "58001": "不支持该语言方向", "58002": "服务未开通或已关闭"}
                raise TranslationError("百度翻译：" + reasons.get(code, "请求失败，请检查账户及接口配置"))
            return [entry["dst"] for entry in data["trans_result"]]
        except (KeyError, TypeError, IndexError):
            raise TranslationError("翻译平台返回结构异常") from None
