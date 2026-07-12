namespace VPet.Plugin.Speaking
{
    /// <summary>
    /// 对应 get_message.py 的测试样例入口。
    /// </summary>
    public static class GetMessage
    {
        /// <summary>测试样例文本（与 get_message.py 一致）。</summary>
        public const string TestSample = "This is used for testing. You just need to change this line in furture.";

        /// <summary>
        /// 获取要合成的文本。当前直接返回传入内容；后续可在此接入 LLM / 对话逻辑。
        /// </summary>
        public static string get_message(string? message)
        {
            try
            {
                if (message != null)
                    return message;
            }
            catch
            {
                // ignore
            }
            return string.Empty;
        }
    }
}
