from icecream import ic
from openai import OpenAI
import os
from pathlib import Path
from dotenv import load_dotenv
from typing import Generator
import threading


class GenerationCancelledError(Exception):
    pass

# 统一从 VPet/.env 加载（learn_agent -> backend -> audio -> VPet）
_VPET_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_VPET_ROOT / ".env", override=True)
load_dotenv(override=False)


class LLM:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens

        if not self.api_key:
            raise ValueError(
                "缺少 API Key。请在 VPet/.env 中设置 DEEPSEEK_API_KEY=sk-..."
            )

        # 初始化 OpenAI 客户端，后续所有调用都走这个 client
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )

    def chat(self, messages: list[dict], tools: list[dict] | None = None):
        # 组装请求参数，按需加入可选项
        kwargs = dict(
            model=self.model,
            messages=messages,
            stream=False,
        )

        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        if tools:
            kwargs["tools"] = tools
            # kwargs["tool_choice"] = "auto"  # 一般默认就是 auto，可显式打开
        print("LLM chat with params:\n", kwargs)
        # 发起一次非流式对话请求，非流式更适合学习和调试
        response = self.client.chat.completions.create(**kwargs)
        print("LLM response:\n", response)
        msg = response.choices[0].message
        return msg

    def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Generator[dict, None, None]:
        """
        流式对话方法，返回生成器

        Yields:
            dict: 包含不同类型事件的字典
                - {"type": "content", "content": "xxx"}  # 内容片段
                - {"type": "tool_calls", "tool_calls": [...]}  # 工具调用
                - {"type": "done"}  # 流结束
        """
        kwargs = dict(
            model=self.model,
            messages=messages,
            stream=True,  # 启用流式
        )

        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        if tools:
            kwargs["tools"] = tools

        response = self.client.chat.completions.create(**kwargs)

        content_buffer = ""
        tool_calls_buffer: dict[int, dict] = {}

        try:
            for chunk in response:
                if cancel_event is not None and cancel_event.is_set():
                    raise GenerationCancelledError("LLM generation cancelled")

                delta = chunk.choices[0].delta

                # 处理内容块
                if delta.content:
                    content_chunk = delta.content
                    content_buffer += content_chunk
                    yield {"type": "content", "content": content_chunk}

                # 处理工具调用块
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        tc_index = tc.index
                        if tc_index not in tool_calls_buffer:
                            tool_calls_buffer[tc_index] = {
                                "id": "",
                                "type": "",
                                "function": {"name": "", "arguments": ""},
                            }

                        if tc.id:
                            tool_calls_buffer[tc_index]["id"] = tc.id
                        if tc.type:
                            tool_calls_buffer[tc_index]["type"] = tc.type

                        if tc.function and tc.function.name:
                            tool_calls_buffer[tc_index]["function"]["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            tool_calls_buffer[tc_index]["function"][
                                "arguments"
                            ] += tc.function.arguments

            if cancel_event is not None and cancel_event.is_set():
                raise GenerationCancelledError("LLM generation cancelled")

            # 流结束，输出完整工具调用信息
            if tool_calls_buffer:
                tool_calls_list = [
                    tool_calls_buffer[i] for i in sorted(tool_calls_buffer.keys())
                ]
                yield {"type": "tool_calls", "tool_calls": tool_calls_list}

            yield {"type": "done"}
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


class DeepSeek(LLM):
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = "deepseek-chat",
    ):
        # 不要在默认参数里读 getenv（定义时只求值一次）
        resolved = (api_key or os.getenv("DEEPSEEK_API_KEY") or "").strip().strip("'\"")
        super().__init__(
            api_key=resolved or None,
            model=model,
            base_url="https://api.deepseek.com",
        )
