import json
import threading
from typing import Any, Generator
from learn_agent.llm import GenerationCancelledError, LLM
from learn_agent.memory import Memory
from learn_agent.tool.toolkit import Toolkit


class Agent:
    def __init__(
        self,
        llm: LLM,
        session_id: str,
        name: str,
        tools: list[Toolkit],
        memory: Memory,
        system_prompt: str = "",
    ):
        self.session_id = session_id
        self.name = name
        self.system_prompt = system_prompt
        self.llm = llm
        self.tools = tools
        self.memory = memory
        self.max_tool_rounds = 8

        self.memory.add_message(role="system", content=system_prompt)

    def _all_tool_schemas(self) -> list[dict]:
        # 汇总所有工具的 schema，给模型识别可调用的工具
        schemas = []
        for toolkit in self.tools:
            schemas.extend(toolkit.list_tools_schemas())
        return schemas

    def _dispatch_tool(self, tool_name: str, args: dict) -> Any:
        # 找到具体工具并执行
        for toolkit in self.tools:
            if toolkit.has(tool_name):
                return toolkit.call(tool_name, **args)
        raise ValueError(f"Tool {tool_name} not found in any toolkit.")

    def _ensure_not_cancelled(self, cancel_event: threading.Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise GenerationCancelledError("Agent run cancelled")

    def run(self, user_text: str, cancel_event: threading.Event | None = None) -> str:
        snapshot = self.memory.snapshot()

        try:
            self._ensure_not_cancelled(cancel_event)
        # 把用户输入加入上下文
            self.memory.add_message(role="user", content=user_text)

            tool_schema = self._all_tool_schemas()

            for _round in range(self.max_tool_rounds):
                self._ensure_not_cancelled(cancel_event)
                messages = self.memory.get_context()

                assistant_content_buffer = ""
                tool_calls_info: list[dict] = []

                for event in self.llm.chat_stream(
                    messages=messages,
                    tools=tool_schema,
                    cancel_event=cancel_event,
                ):
                    event_type = event.get("type")
                    if event_type == "content":
                        assistant_content_buffer += event["content"]
                    elif event_type == "tool_calls":
                        tool_calls_info = event["tool_calls"]
                    elif event_type == "done":
                        break

                self._ensure_not_cancelled(cancel_event)

                assistant_dict = {"role": "assistant"}
                if assistant_content_buffer:
                    assistant_dict["content"] = assistant_content_buffer
                if tool_calls_info:
                    assistant_dict["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": tc.get("type", "function"),
                            "function": {
                                "name": tc["function"]["name"],
                                "arguments": tc["function"]["arguments"],
                            },
                        }
                        for tc in tool_calls_info
                    ]

                self.memory.add_message(**assistant_dict)

                if not tool_calls_info:
                    return assistant_content_buffer

                for tc in tool_calls_info:
                    self._ensure_not_cancelled(cancel_event)
                    tc_id = tc["id"]
                    fn_name = tc["function"]["name"]
                    raw_args = tc["function"].get("arguments") or "{}"

                    try:
                        args = (
                            json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                        )
                    except Exception:
                        args = {}

                    try:
                        result = self._dispatch_tool(fn_name, args)
                        tool_content = json.dumps(
                            {"ok": True, "result": result}, ensure_ascii=False
                        )
                    except Exception as e:
                        tool_content = json.dumps(
                            {
                                "ok": False,
                                "error": {"code": "TOOL_EXEC_ERROR", "message": str(e)},
                            },
                            ensure_ascii=False,
                        )

                    self.memory.add_message(
                        role="tool",
                        content=tool_content,
                        tool_call_id=tc_id,
                    )

            return "ERROR: reached maximum tool rounds without a final answer."
        except GenerationCancelledError:
            self.memory.restore(snapshot)
            raise

    def run_stream(
        self, user_text: str
    ) -> Generator[dict, None, None]:
        """
        流式运行方法

        Yields:
            dict: 事件字典
                - {"type": "assistant", "content": "xxx", "response": "xxx"}  # 助手回复片段
                - {"type": "tool_call", "name": "xxx", "args": {...}}  # 工具调用开始
                - {"type": "tool_result", "name": "xxx", "result": {...}}  # 工具执行结果
                - {"type": "done", "final": "xxx", "response": "xxx"}  # 完成
                - {"type": "error", "message": "xxx"}  # 错误
        """
        # 把用户输入加入上下文
        self.memory.add_message(role="user", content=user_text)
        yield {"type": "user_message", "content": user_text}

        tool_schema = self._all_tool_schemas()

        for _round in range(self.max_tool_rounds):
            messages = self.memory.get_context()

            # 使用流式 LLM 调用
            assistant_content_buffer = ""
            tool_calls_info: list[dict] = []

            for event in self.llm.chat_stream(messages=messages, tools=tool_schema):
                event_type = event.get("type")

                if event_type == "content":
                    content_chunk = event["content"]
                    assistant_content_buffer += content_chunk
                    yield {
                        "type": "assistant",
                        "content": content_chunk,
                        "response": content_chunk,
                    }

                elif event_type == "tool_calls":
                    tool_calls_info = event["tool_calls"]

                elif event_type == "done":
                    break

            # 处理工具调用（如果有）
            if tool_calls_info:
                # 先把 assistant 消息（包含 tool_calls）添加到 memory
                self.memory.add_message(
                    role="assistant",
                    content=assistant_content_buffer or None,
                    tool_calls=[
                        {
                            "id": tc["id"],
                            "type": tc.get("type", "function"),
                            "function": {
                                "name": tc["function"]["name"],
                                "arguments": tc["function"]["arguments"],
                            },
                        }
                        for tc in tool_calls_info
                    ],
                )

                # 发送工具调用事件
                for tc in tool_calls_info:
                    fn_name = tc["function"]["name"]
                    raw_args = tc["function"]["arguments"]

                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except Exception:
                        args = {}

                    # 发送工具调用开始事件
                    yield {"type": "tool_call", "name": fn_name, "args": args}

                    # 执行工具
                    try:
                        result = self._dispatch_tool(fn_name, args)
                        tool_content = json.dumps(
                            {"ok": True, "result": result}, ensure_ascii=False
                        )
                        yield {"type": "tool_result", "name": fn_name, "result": result}
                    except Exception as e:
                        tool_content = json.dumps(
                            {
                                "ok": False,
                                "error": {"code": "TOOL_EXEC_ERROR", "message": str(e)},
                            },
                            ensure_ascii=False,
                        )
                        yield {"type": "tool_error", "name": fn_name, "error": str(e)}

                    # 添加到上下文
                    self.memory.add_message(
                        role="tool",
                        content=tool_content,
                        tool_call_id=tc["id"],
                    )

                # 继续下一轮（获取最终回答）
                continue

            # 没有工具调用，任务完成
            self.memory.add_message(
                role="assistant", content=assistant_content_buffer
            )
            yield {
                "type": "done",
                "final": assistant_content_buffer,
                "response": assistant_content_buffer,
            }
            return

        # 达到最大轮次
        yield {"type": "error", "message": "ERROR: reached maximum tool rounds without a final answer."}
