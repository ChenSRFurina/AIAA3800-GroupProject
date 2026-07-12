# 测试样例：获取要合成的文本
# 对应 C# 侧 GetMessage.get_message / GetMessage.TestSample

def get_message(message):
    try:
        if message is not None:
            return message
    except Exception as e:
        return str(e)
    return ""


# 测试用例：
message = get_message("This is used for testing. You just need to change this line in furture.")
