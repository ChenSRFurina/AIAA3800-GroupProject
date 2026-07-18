extends Node

const API_HOST: String = "127.0.0.1"
const API_PORT: int = 8000
const API_PATH: String = "/chat"

var headers: PackedStringArray = ["Content-Type: application/json"]
var http_client: HTTPClient
var sse_buffer: String = ""

# Connection state
var is_connected: bool = false
var pending_message: String = ""  # Queue for retry
var connection_attempts: int = 0

@onready var http_status: HTTPRequest = $HTTPStatus


func _ready() -> void:
	SignalManager.user_query.connect(_on_user_query)
	http_status.request_completed.connect(_on_request_completed)

	http_client = HTTPClient.new()
	_do_connect()
	get_status()
	_setup_voice_polling()


func _do_connect() -> void:
	http_client.close()
	var err: int = http_client.connect_to_host(API_HOST, API_PORT)
	if err == OK:
		is_connected = true
		connection_attempts = 0
		print("HTTPManager: Connecting to ", API_HOST, ":", API_PORT)
	else:
		is_connected = false
		printerr("HTTPManager: connect_to_host failed, error: ", err)


func get_status() -> void:
	http_status.request(
		"http://" + API_HOST + ":" + str(API_PORT) + "/health",
		headers,
		HTTPClient.METHOD_GET
	)


func _on_user_query(query: String) -> void:
	print("HTTPManager: User query received: ", query)

	# Ensure we have a fresh connection for each request
	# HTTPClient doesn't handle Keep-Alive reliably across SSE streams
	http_client.close()
	_do_connect()
	pending_message = query


func _try_send() -> void:
	if pending_message == "":
		return

	var status: int = http_client.get_status()

	if status == HTTPClient.STATUS_CONNECTED:
		var body: String = JSON.stringify({"message": pending_message})
		var err: int = http_client.request(HTTPClient.METHOD_POST, API_PATH, headers, body)
		if err == OK:
			print("HTTPManager: Sending request: ", pending_message)
			pending_message = ""  # Clear queue on success
			sse_buffer = ""  # Reset buffer for new response
		else:
			printerr("HTTPManager: request() failed, error: ", err)
	elif status == HTTPClient.STATUS_DISCONNECTED:
		print("HTTPManager: Disconnected, reconnecting...")
		_do_connect()
	elif status == HTTPClient.STATUS_CONNECTING or status == HTTPClient.STATUS_RESOLVING:
		pass  # Still connecting, will retry next frame
	else:
		# STATUS_BODY, STATUS_REQUESTING - unexpected for sending
		# STATUS_CONNECTION_ERROR, STATUS_TLS_HANDSHAKE_ERROR, STATUS_CANT_RESOLVE
		print("HTTPManager: Bad status ", status, ", reconnecting...")
		_do_connect()


var was_in_body: bool = false  # Track state transitions

func _process(_delta: float) -> void:
	if not is_connected:
		return

	http_client.poll()
	var status: int = http_client.get_status()

	# Try to send pending message if connected
	if pending_message != "":
		_try_send()
		return  # Don't read response while trying to send

	# Detect transition OUT of BODY → flush remaining SSE buffer
	if was_in_body and status != HTTPClient.STATUS_BODY:
		_flush_sse_buffer()

	# Read all available SSE chunks
	if status == HTTPClient.STATUS_BODY:
		was_in_body = true
		while true:
			if not http_client.has_response():
				break

			var chunk: PackedByteArray = http_client.read_response_body_chunk()
			if chunk.size() == 0:
				break

			var chunk_str: String = chunk.get_string_from_utf8()
			_print_raw_sse(chunk_str)
			_parse_sse(chunk_str)

			http_client.poll()
			status = http_client.get_status()

			if status != HTTPClient.STATUS_BODY:
				# Left BODY mid-read — flush remaining
				_flush_sse_buffer()
				was_in_body = false
				break
	else:
		was_in_body = false


func _flush_sse_buffer() -> void:
	"""Process any remaining SSE data left in the buffer when stream ends."""
	var trimmed: String = sse_buffer.strip_edges()
	sse_buffer = ""

	if trimmed == "":
		return

	# Strip "data:" prefix if present
	if trimmed.begins_with("data:"):
		trimmed = trimmed.substr(5).strip_edges()

	if not trimmed.begins_with("{"):
		print("SSE flush: discarding non-JSON: ", trimmed.substr(0, 60))
		return

	print("SSE flush: ", trimmed.substr(0, 80))
	var json_parser := JSON.new()
	var parse_err: int = json_parser.parse(trimmed)
	if parse_err != OK:
		print("SSE flush: JSON parse failed, discarding")
		return

	var data = json_parser.get_data()
	if typeof(data) != TYPE_DICTIONARY:
		return

	var event_type: String = data.get("type", "")
	match event_type:
		"done":
			SignalManager.message_received.emit("\n")
			print("--- Response complete (flushed) ---")
		"assistant":
			var content: String = data.get("content", "")
			if content != "":
				SignalManager.message_received.emit(content)
		"error":
			SignalManager.message_received.emit("[Error: " + data.get("message", "?") + "]")


func _print_raw_sse(raw: String) -> void:
	# Debug: print raw SSE data (truncated)
	var preview: String = raw.substr(0, 200)
	if raw.length() > 200:
		preview += "..."
	print("SSE raw: ", preview.replace("\n", "\\n"))


func _parse_sse(raw_data: String) -> void:
	sse_buffer += raw_data

	# Keep splitting on "data:" boundary and processing complete events
	while true:
		var idx: int = sse_buffer.find("data:")
		if idx == -1:
			# No "data:" — buffer might be a lone JSON or leftover junk
			_try_parse_standalone()
			return

		# Skip past the "data:" prefix
		var content_start: int = idx + 5
		var next_idx: int = sse_buffer.find("data:", content_start)

		if next_idx == -1:
			# Only one "data:" — strip prefix and try to parse
			sse_buffer = sse_buffer.substr(content_start)
			_try_parse_standalone()
			return

		# Extract event between idx and next_idx
		var event_str: String = sse_buffer.substr(content_start, next_idx - content_start).strip_edges()
		sse_buffer = sse_buffer.substr(next_idx)  # Keep remaining

		if event_str != "":
			_dispatch_sse_event(event_str)


func _try_parse_standalone() -> void:
	"""Try to parse sse_buffer as a complete JSON event."""
	var trimmed: String = sse_buffer.strip_edges()
	if trimmed == "":
		sse_buffer = ""
		return
	if not trimmed.begins_with("{"):
		# Not JSON — discard garbage (trailing newlines, partial data: prefix, etc.)
		print("SSE: discarding non-JSON buffer: ", trimmed.substr(0, 60))
		sse_buffer = ""
		return

	var json_parser := JSON.new()
	if json_parser.parse(trimmed) != OK:
		return  # Incomplete JSON — wait for more data

	var data = json_parser.get_data()
	if typeof(data) != TYPE_DICTIONARY:
		sse_buffer = ""
		return

	# Successfully parsed — process and clear buffer
	sse_buffer = ""
	_dispatch_event_data(data)


func _dispatch_sse_event(event_str: String) -> void:
	var json_parser := JSON.new()
	var parse_err: int = json_parser.parse(event_str)
	if parse_err != OK:
		print("JSON parse error: ", event_str.substr(0, 80))
		return

	var data = json_parser.get_data()
	if typeof(data) != TYPE_DICTIONARY:
		print("SSE: Not a dict: ", event_str.substr(0, 80))
		return

	_dispatch_event_data(data)


func _dispatch_event_data(data: Dictionary) -> void:
	var event_type: String = data.get("type", "")

	match event_type:
		"assistant":
			var content: String = data.get("content", "")
			if content != "":
				SignalManager.message_received.emit(content)
		"user_message":
			print("SSE: user_message echoed")
		"tool_call":
			print("SSE: tool_call - ", data.get("name", "?"))
		"tool_result":
			print("SSE: tool_result")
		"done":
			SignalManager.message_received.emit("\n")
			print("--- Response complete ---")
		"error":
			var err_msg: String = data.get("message", "Unknown")
			printerr("SSE error: ", err_msg)
			SignalManager.message_received.emit("[Error: " + err_msg + "]")


# ── 语音消息轮询 ──────────────────────────────────────────────────────

var voice_http: HTTPRequest
var voice_timer: Timer
var voice_enabled: bool = true  # 默认开启语音


func _setup_voice_polling() -> void:
	# 创建语音轮询 HTTPRequest
	voice_http = HTTPRequest.new()
	voice_http.name = "VoiceHTTP"
	add_child(voice_http)
	voice_http.request_completed.connect(_on_voice_poll_complete)

	# 创建定时器，每秒轮询
	voice_timer = Timer.new()
	voice_timer.name = "VoiceTimer"
	voice_timer.wait_time = 1.0
	voice_timer.autostart = true
	add_child(voice_timer)
	voice_timer.timeout.connect(_poll_voice_messages)


func _poll_voice_messages() -> void:
	if not voice_enabled:
		return
	var url = "http://" + API_HOST + ":" + str(API_PORT) + "/voice/messages"
	voice_http.request(url, headers, HTTPClient.METHOD_GET)


func _on_voice_poll_complete(_result: int, _response_code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	var body_string: String = body.get_string_from_utf8()

	# If response is empty or error (backend may not have voice module), skip silently
	if body_string == "" or _response_code != 200:
		return

	var json_parser := JSON.new()
	if json_parser.parse(body_string) != OK:
		print("Voice poll: invalid JSON: ", body_string.substr(0, 80))
		return

	var data = json_parser.get_data()
	if typeof(data) != TYPE_DICTIONARY:
		return

	var messages: Array = data.get("messages", [])
	if messages.is_empty():
		return

	print("Voice poll: received ", messages.size(), " message(s)")

	for msg in messages:
		if typeof(msg) != TYPE_DICTIONARY:
			continue
		var msg_type: String = msg.get("type", "")
		var content: String = msg.get("content", "")
		var source: String = msg.get("source", "")

		if source != "voice":
			continue

		print("Voice message: type=", msg_type, " content=", content.substr(0, 50))

		match msg_type:
			"user_message":
				print("Voice transcript -> TextEdit: ", content)
				SignalManager.voice_transcript.emit(content)
			"assistant":
				print("Voice response -> dialogue: ", content)
				SignalManager.voice_response.emit(content)


func _on_request_completed(_result: int, _response_code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	var json_parser := JSON.new()
	var body_string: String = body.get_string_from_utf8()
	var parse_err: int = json_parser.parse(body_string)
	if parse_err == OK:
		var res = json_parser.get_data()
		SignalManager.check_status.emit(res)
