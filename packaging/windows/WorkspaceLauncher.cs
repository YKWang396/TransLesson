using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;

[assembly: AssemblyTitle("课译TransLesson")]
[assembly: AssemblyProduct("课译TransLesson")]
[assembly: AssemblyDescription("课程翻译工作台")]
[assembly: AssemblyVersion("0.2.1.0")]

internal static class WorkspaceLauncher
{
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int MessageBox(IntPtr window, string text, string title, uint flags);

    // Escape each argument with Windows CommandLineToArgvW-compatible rules.
    private static string Quote(string value)
    {
        StringBuilder result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') result.Append('\\', slashes * 2 + 1);
            else result.Append('\\', slashes);
            result.Append(c);
            slashes = 0;
        }
        result.Append('\\', slashes * 2);
        return result.Append('"').ToString();
    }

    [STAThread]
    private static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string[] candidates = {
            Path.Combine(root, "dist", "TransLesson", "课译TransLesson.exe"),
            Path.Combine(root, "MathBridge", "dist", "TransLesson", "课译TransLesson.exe")
        };
        try
        {
            foreach (string target in candidates)
            {
                if (!File.Exists(target)) continue;
                string arguments = string.Join(" ", Array.ConvertAll(args, Quote));
                Process child = Process.Start(new ProcessStartInfo(target, arguments) { UseShellExecute = false });
                if (Array.IndexOf(args, "--smoke-test") >= 0)
                {
                    child.WaitForExit();
                    return child.ExitCode;
                }
                return 0;
            }
            MessageBox(IntPtr.Zero, "未找到完整的程序运行文件。请保留整个文件夹，或下载并完整解压课译TransLesson 的 Windows 程序包。", "课译TransLesson", 0x10);
            return 2;
        }
        catch (Exception error)
        {
            MessageBox(IntPtr.Zero, "程序未能启动：" + error.Message, "课译TransLesson", 0x10);
            return 1;
        }
    }
}
