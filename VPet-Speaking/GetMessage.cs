namespace VPet.Plugin.Speaking
{
    /// <summary>
    /// DIY「说话」调试用固定文本（合成播放，不调用 LLM）。
    /// </summary>
    public static class GetMessage
    {
        /// <summary>点击「说话」时固定合成的调试文本。</summary>
        public const string ChatPrompt = "好无聊啊，和我聊聊天吧";

        /// <summary>兼容旧命名。</summary>
        public const string TestSample = ChatPrompt;

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
