# -*- coding: utf-8 -*-
"""OpenAI 兼容 Chat Completions 客户端(纯标准库实现,带重试与用量统计)。

只封装 Agent 需要的最小面:chat + tool_calls 流式解析出的结构化结果。
每次调用的 token 用量都记入 CostMeter,供成本门禁与评测使用。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

# JSON 序列化循环依赖注入点(runner 设置),避免 LLM 层反向依赖 trace
_obs_hook: Callable[["Usage"], None] | None = None


def set_usage_hook(hook: Callable[["Usage"], None]) -> None:
    global _obs_hook
    _obs_hook = hook


class LLMError(RuntimeError):
    pass


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, u: dict[str, Any]) -> None:
        self.prompt_tokens += int(u.get("prompt_tokens", 0))
        self.completion_tokens += int(u.get("completion_tokens", 0))
        self.calls += 1

    def as_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total,
            "llm_calls": self.calls,
        }


@dataclass
class LLMResult:
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = ""
    usage: Usage = field(default_factory=Usage)


class LLMClient:
    """极薄封装:失败重试 1 次,超时 120s,返回结构化 LLMResult。"""

    def __init__(self, cfg, model: str | None = None):
        self.cfg = cfg
        self.model = model or cfg.model
        self.usage = Usage()

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             temperature: float | None = None) -> LLMResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if temperature is None:
            temperature = self.cfg.temperature
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        last_err: Exception | None = None
        # 指数退避重试:网关对长 payload(tools+system)存在间歇性 503/超时,
        # 2 次紧重试不够;退避 2/4/8s 共 4 次尝试显著提升成功率
        for attempt in range(4):
            try:
                data = self._post(payload)
                return self._parse(data)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, LLMError) as e:
                last_err = e
                if attempt < 3:
                    time.sleep(2 ** (attempt + 1))  # 2s, 4s, 8s
        raise LLMError(f"LLM 调用失败(model={self.model}): {last_err}") from last_err

    # ---- 内部 ----
    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.cfg.api_base.rstrip("/") + "/chat/completions",
            # ensure_ascii=False:中文直传 UTF-8 比 \u 转义小 ~40%,
            # 大 payload(多工具+长系统提示)在网关侧更稳定
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.cfg.api_key(),
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))

    def chat_stream(self, messages: list[dict], tools: list[dict] | None = None,
                    temperature: float | None = None):
        """流式 chat:逐块 yield {'delta': str} 或结束时 {'usage': {...}}。

        非流式重试逻辑不适用于流(已发送部分无法回滚):
        连接建立失败重试一次;流中断则抛 LLMError。
        """
        payload: dict[str, Any] = {"model": self.model, "messages": messages,
                                   "stream": True,
                                   "stream_options": {"include_usage": True}}
        if temperature is None:
            temperature = self.cfg.temperature
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_err: Exception | None = None
        resp = None
        for attempt in range(2):  # 仅连接阶段可安全重试
            try:
                req = urllib.request.Request(
                    self.cfg.api_base.rstrip("/") + "/chat/completions",
                    data=body,
                    headers={"Content-Type": "application/json",
                             "Authorization": "Bearer " + self.cfg.api_key(),
                             "Accept": "text/event-stream"},
                    method="POST")
                resp = urllib.request.urlopen(req, timeout=120)
                break
            except (urllib.error.URLError, urllib.error.HTTPError) as e:
                last_err = e
                if attempt == 0:
                    time.sleep(2)
        if resp is None:
            raise LLMError(f"LLM 流式连接失败(model={self.model}): {last_err}")

        import codecs
        reader = codecs.getreader("utf-8")(resp)
        try:
            for line in reader:
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usage"):
                    self.usage.add(chunk["usage"])
                    yield {"usage": self.usage.as_dict()}
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {})
                if delta.get("content"):
                    yield {"delta": delta["content"]}
                for tc in delta.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        yield {"tool_call": fn["name"]}
        finally:
            resp.close()

    @staticmethod
    def _post_raw(cfg, model: str, payload: dict) -> dict:
        """类方法通道:供并行 review worker 使用,不共享实例状态。"""
        req = urllib.request.Request(
            cfg.api_base.rstrip("/") + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + cfg.api_key(),
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))

    def _parse(self, data: dict) -> LLMResult:
        if not data.get("choices"):
            raise LLMError(f"响应无 choices: {str(data)[:300]}")
        msg = data["choices"][0]["message"]
        result = LLMResult(
            content=msg.get("content") or "",
            finish_reason=data["choices"][0].get("finish_reason", ""),
        )
        for tc in msg.get("tool_calls") or []:
            if tc.get("type") != "function":
                continue
            fn = tc["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": fn.get("arguments", "")}
            result.tool_calls.append({
                "id": tc.get("id", ""),
                "name": fn.get("name", ""),
                "arguments": args,
            })
        if data.get("usage"):
            self.usage.add(data["usage"])
            result.usage.add(data["usage"])
        return result

    # ---- 批量并行调用(用于并行 Review / 多模型投票) ----
    @classmethod
    def chat_parallel(cls, cfg, requests: list[tuple[str, list[dict]]],
                      max_workers: int = 3, temperature: float | None = None) -> list[Any]:
        """并行发起多个独立 chat 请求。requests = [(model, messages), ...]。

        返回与输入同序的 LLMResult 列表;单个失败不影响其他(对应位置为 LLMError)。
        """
        import concurrent.futures

        def _one(model: str, messages: list[dict]) -> LLMResult:
            client = cls(cfg, model=model)
            return client.chat(messages, temperature=temperature)

        results: list[Any] = [None] * len(requests)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_one, m, msgs): i for i, (m, msgs) in enumerate(requests)}
            for fut in concurrent.futures.as_completed(futs):
                idx = futs[fut]
                try:
                    results[idx] = fut.result()
                except Exception as e:  # 单点失败降级,不炸整批
                    results[idx] = LLMError(str(e))
        # 汇总用量到各结果(实例用量在 _one 内已计,无需重复)
        return results
