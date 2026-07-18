extends Control

var _pending_clear: bool = false


func _ready() -> void:
	print("dialogue.gd ready")
	SignalManager.message_received.connect(_on_message_received)
	SignalManager.user_query.connect(_on_user_message)
	SignalManager.voice_response.connect(_on_voice_response)


func _on_user_message(msg: String) -> void:
	print("User message in dialogue.gd: ", msg)
	%ChatMessageAI.text = "[You]: " + msg + "\n[Pet]: "


func _on_message_received(msg: String) -> void:
	print("AI message in dialogue.gd: ", msg)
	%ChatMessageAI.text += msg


func _on_voice_response(text: String) -> void:
	"""语音回复 — 清旧回复并显示。"""
	print("Voice response in dialogue.gd: ", text)
	%ChatMessageAI.text = "[Voice]: " + text


func set_dialogue_label(msg: String) -> void:
	_on_message_received(msg)
