# -*- coding: utf-8 -*-
"""Embedding / LLM 适配层：只用标准库 urllib，不给项目引入第三方依赖。

本项目主线（s1–s4、评测、IAA）零依赖；baseline 里有两类确实需要外部模型能力的东西：
- **Dense retrieval（B3）**：需要条文向量；
- **LLM zero-shot（B4）／LLM+RAG（B5/B6）**：需要生成能力。

本模块只负责"把外部模型包成两个函数"，不参与评测逻辑。配置全部走环境变量，
因此换供应商不改代码：

    # 嵌入（OpenAI 兼容 /v1/embeddings）
    WB_EMB_BASE=https://api.xxx.com/v1
    WB_EMB_KEY=sk-...
    WB_EMB_MODEL=bge-m3            # 中文法律场景推荐 bge-m3 / text-embedding-3-large

    # 生成（OpenAI 兼容 /v1/chat/completions）
    WB_LLM_BASE=https://api.xxx.com/v1
    WB_LLM_KEY=sk-...
    WB_LLM_MODEL=...               # 任一可用对话模型

也可本地跑嵌入（需自行 pip install sentence-transformers）：
    python build/baselines/run_baselines.py --emb local:BAAI/bge-m3

设计取舍：
- 未配置时**不报错退出，而是返回 Unavailable**，让本地 baseline（B1/B2/B7/B8）照常跑完，
  报告里把缺的行标成 `n/a (unconfigured)` 而不是 `pending` —— 论文里不能出现"跑了但没数"。
- 嵌入缓存按 (model, text) 哈希落盘到 build/out/emb_cache/，重跑不再付费。
"""
import hashlib
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.dirname(HERE)
CACHE_DIR = os.path.join(BUILD, "out", "emb_cache")


class Unavailable(RuntimeError):
    """外部模型未配置 / 调用失败。调用方据此把该 baseline 记为 n/a。"""


def _sha(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _post(url, payload, key, timeout=120, retries=3):
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    last = None
    for i in range(retries):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            last = "HTTP %s: %s" % (e.code, detail)
            if e.code in (400, 401, 403, 404):
                break          # 配置错，重试无意义
        except Exception as e:  # noqa: BLE001 网络抖动才重试
            last = "%s: %s" % (type(e).__name__, e)
        time.sleep(1.5 * (i + 1))
    raise Unavailable(last or "unknown error")


# ---------------------------------------------------------------- embeddings
class Embedder:
    def __init__(self, spec=None):
        self.spec = spec or os.environ.get("WB_EMB_MODEL") or ""
        self.base = (os.environ.get("WB_EMB_BASE") or "").rstrip("/")
        self.key = os.environ.get("WB_EMB_KEY") or ""
        self.local = self.spec.startswith("local:")
        self.model = self.spec[6:] if self.local else self.spec
        self._st = None
        self._cache = {}
        self._cache_path = os.path.join(CACHE_DIR, "emb_%s.json" % _sha(self.model)[:12])

    # ---- 配置检查（报告里要写清"缺什么"，不是笼统的 unavailable）
    def why_unavailable(self):
        if self.local:
            try:
                import sentence_transformers  # noqa: F401
            except Exception:  # noqa: BLE001
                return "sentence-transformers 未安装（pip install sentence-transformers）"
            return ""
        missing = [k for k, v in (("WB_EMB_BASE", self.base), ("WB_EMB_MODEL", self.model)) if not v]
        if missing:
            return "未配置环境变量 " + "/".join(missing)
        return ""

    def _load_cache(self):
        if os.path.exists(self._cache_path):
            with io.open(self._cache_path, encoding="utf-8") as f:
                self._cache = json.load(f)

    def _save_cache(self):
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = self._cache_path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._cache, f)
        os.replace(tmp, self._cache_path)

    def _api_batch(self, texts):
        payload = {"model": self.model, "input": texts}
        js = _post(self.base + "/embeddings", payload, self.key)
        if "data" not in js:
            raise Unavailable("嵌入接口返回异常：%s" % str(js)[:200])
        items = sorted(js["data"], key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in items]

    def __call__(self, texts, batch=32, progress=None):
        """返回与 texts 等长的向量列表。带磁盘缓存。"""
        if self.why_unavailable():
            raise Unavailable(self.why_unavailable())
        self._load_cache()
        out, todo, idx = [None] * len(texts), [], []
        for i, t in enumerate(texts):
            k = _sha(t)
            if k in self._cache:
                out[i] = self._cache[k]
            else:
                todo.append(t)
                idx.append(i)
        if todo:
            if self.local:
                if self._st is None:
                    from sentence_transformers import SentenceTransformer
                    self._st = SentenceTransformer(self.model)
                for s in range(0, len(todo), batch):
                    chunk = todo[s:s + batch]
                    vecs = self._st.encode(chunk, normalize_embeddings=True).tolist()
                    for j, v in enumerate(vecs):
                        self._cache[_sha(chunk[j])] = v
                        out[idx[s + j]] = v
                    if progress:
                        progress(min(s + batch, len(todo)), len(todo))
            else:
                for s in range(0, len(todo), batch):
                    chunk = todo[s:s + batch]
                    for attempt in range(3):
                        try:
                            vecs = self._api_batch(chunk)
                            break
                        except Unavailable:
                            if attempt == 2:
                                raise
                            time.sleep(2 * (attempt + 1))
                    if len(vecs) != len(chunk):
                        raise Unavailable("嵌入条数不匹配：%d vs %d" % (len(vecs), len(chunk)))
                    for j, v in enumerate(vecs):
                        self._cache[_sha(chunk[j])] = v
                        out[idx[s + j]] = v
                    if progress:
                        progress(min(s + batch, len(todo)), len(todo))
            self._save_cache()
        return out


# ---------------------------------------------------------------- chat
class Chat:
    def __init__(self, spec=None):
        self.model = spec or os.environ.get("WB_LLM_MODEL") or ""
        self.base = (os.environ.get("WB_LLM_BASE") or "").rstrip("/")
        self.key = os.environ.get("WB_LLM_KEY") or ""
        # 响应级磁盘缓存：温度 0 的评测调用可复现，重跑不付费。
        # 键 = (model, messages, temperature, max_tokens, json_mode) 的哈希。
        self._cache = {}
        self._cache_path = os.path.join(CACHE_DIR, "chat_%s.json" % _sha(self.model)[:12]) \
            if self.model else None
        self._load_cache()

    def _load_cache(self):
        if self._cache_path and os.path.exists(self._cache_path):
            try:
                with io.open(self._cache_path, encoding="utf-8") as f:
                    self._cache = json.load(f)
            except Exception:  # noqa: BLE001 缓存损坏时静默重建
                self._cache = {}

    def _save_cache(self):
        if not self._cache_path:
            return
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = self._cache_path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, ensure_ascii=False)
        os.replace(tmp, self._cache_path)

    def why_unavailable(self):
        missing = [k for k, v in (("WB_LLM_BASE", self.base), ("WB_LLM_MODEL", self.model)) if not v]
        return ("未配置环境变量 " + "/".join(missing)) if missing else ""

    def __call__(self, messages, temperature=0.0, max_tokens=800, json_mode=False):
        if self.why_unavailable():
            raise Unavailable(self.why_unavailable())
        key = _sha(json.dumps([self.model, messages, temperature,
                               max_tokens, json_mode], ensure_ascii=False, sort_keys=True))
        if key in self._cache:
            return self._cache[key]
        payload = {"model": self.model, "messages": messages,
                   "temperature": temperature, "max_tokens": max_tokens}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        js = _post(self.base + "/chat/completions", payload, self.key, timeout=180)
        try:
            out = js["choices"][0]["message"]["content"]
        except Exception:  # noqa: BLE001
            raise Unavailable("生成接口返回异常：%s" % str(js)[:300])
        self._cache[key] = out
        self._save_cache()
        return out


def describe():
    """报告用：当前配置下哪些能力可用。"""
    e, l = Embedder(), Chat()
    return {"embedder": e.model or "(未设置)", "embed_issue": e.why_unavailable(),
            "chat": l.model or "(未设置)", "chat_issue": l.why_unavailable()}


if __name__ == "__main__":
    sys.stdout.write(json.dumps(describe(), ensure_ascii=False, indent=2) + "\n")
