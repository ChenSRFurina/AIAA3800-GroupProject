extends Node


signal user_query(query :String) # 用户提问
signal message_received(message: String) # 接收到AI回复
signal check_status(message:Dictionary) # 服务状态


####

# 
signal send_button_press(content :Array)
# 
signal update_current_preset(presets :Dictionary)

signal add_new_preset_panel

signal change_canvas(path :String)

signal change_setting_window_visible


# change side bar menu
signal change_side_menu(menu_name)


# spawn search panel
signal spawn_search_panel(res)

signal update_ediotr_text(text: String)


# change windows sence visible
signal change_windows_visible
signal open_chat_history

# voice assistant
signal voice_transcript(text: String)  # 语音转文字结果
signal voice_response(text: String)    # 语音助手回复 → 对话气泡
signal voice_mode_changed(enabled: bool)  # 语音模式切换

# 清除对话 (新对话开始时触发)
signal clear_dialogue
