using System;
using System.IO;
using System.Text;
using System.Threading;

namespace AUTelemetryProbe;

internal static class AtomicFile
{
    private const int DefaultAttempts = 5;
    private const int RetryDelayMs = 5;
    private static readonly Encoding Utf8NoBom = new UTF8Encoding(false);

    public static void WriteAllText(
        string path,
        string content,
        int attempts = DefaultAttempts)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            throw new ArgumentException("A destination path is required.", nameof(path));
        }

        content ??= string.Empty;
        attempts = Math.Max(1, attempts);

        var fullPath = Path.GetFullPath(path);
        var directory = Path.GetDirectoryName(fullPath)
            ?? throw new InvalidOperationException($"Destination has no directory: {fullPath}");
        Directory.CreateDirectory(directory);

        Exception? lastError = null;
        for (var attempt = 1; attempt <= attempts; attempt++)
        {
            var temporaryPath = Path.Combine(
                directory,
                $".{Path.GetFileName(fullPath)}.{Environment.ProcessId}.{Guid.NewGuid():N}.tmp");
            try
            {
                using (var stream = new FileStream(
                           temporaryPath,
                           FileMode.CreateNew,
                           FileAccess.Write,
                           FileShare.None))
                using (var writer = new StreamWriter(stream, Utf8NoBom))
                {
                    writer.Write(content);
                    writer.Flush();
                    stream.Flush(true);
                }

                if (File.Exists(fullPath))
                {
                    try
                    {
                        // ReplaceFile supports open readers with SHARE_DELETE;
                        // MoveFileEx(REPLACE_EXISTING) may still fail with access denied.
                        File.Replace(temporaryPath, fullPath, null);
                    }
                    catch (FileNotFoundException)
                    {
                        File.Move(temporaryPath, fullPath, true);
                    }
                }
                else File.Move(temporaryPath, fullPath, true);
                return;
            }
            catch (Exception exception) when (
                exception is IOException or UnauthorizedAccessException)
            {
                lastError = exception;
                TryDelete(temporaryPath);
                if (attempt < attempts)
                {
                    Thread.Sleep(RetryDelayMs);
                }
            }
        }

        throw new IOException(
            $"Atomic write failed for {fullPath} after {attempts} attempts.",
            lastError);
    }

    private static void TryDelete(string path)
    {
        try
        {
            if (File.Exists(path))
            {
                File.Delete(path);
            }
        }
        catch (Exception)
        {
            // A later attempt uses a fresh temporary file.
        }
    }
}
