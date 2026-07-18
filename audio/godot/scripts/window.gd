extends Window

@onready var chat_history: PanelContainer = %ChatHistory
@onready var text_edit: TextEdit = %TextEdit


const EXPAND = preload("res://assets/expand.svg")
const SHRINK = preload("res://assets/shrink.svg")

var orig_size : Vector2i
@export var grow_len: int = 400
var final_size : Vector2i

@onready var icon_button: Button = %IconButton
@export var tween_duration : float = 1.0

# generate Send window in central Sreen
func _ready() -> void:
	orig_size = size
	final_size = Vector2i(orig_size.x,orig_size.y + grow_len)

	borderless = false
	var screen_size := DisplayServer.screen_get_size()
	position = screen_size / 2
	SignalManager.change_windows_visible.connect(
		func():
			visible = !visible
	)
	SignalManager.check_status.connect(
		func(msg:Dictionary):
			print("Received status message:", msg)
			%Status.text = str(msg.get("status","Unknown"))
	)
	# 语音转文字 → 只填入输入框
	SignalManager.voice_transcript.connect(
		func(text: String):
			print("Voice transcript -> TextEdit: ", text)
			text_edit.text = text
	)

# close windows
func _on_close_requested() -> void:
	#queue_free()
	visible = false


func _on_texture_rect_2_pressed() -> void:

	if !chat_history.visible: # cha_history is not visible

		var tween : Tween = get_tree().create_tween().set_parallel()
		tween.tween_property(self,"size",final_size,tween_duration).set_trans(Tween.TRANS_LINEAR)
		tween.tween_property(self,"position",position + Vector2i(-50,-50),tween_duration).set_trans(Tween.TRANS_LINEAR)
		chat_history.visible = true
		await tween.finished

		icon_button.icon = SHRINK
	else:
		var tween : Tween = get_tree().create_tween().set_parallel()
		tween.tween_property(self,"size",orig_size,tween_duration).set_trans(Tween.TRANS_LINEAR)
		tween.tween_property(self,"position",position + Vector2i(50,50),tween_duration).set_trans(Tween.TRANS_LINEAR)
		await tween.finished

		icon_button.icon = EXPAND
		chat_history.visible = false


func _on_send_button_pressed() -> void:
		SignalManager.user_query.emit(text_edit.text)
		prints("User query:", text_edit.text)
		text_edit.text = ""
