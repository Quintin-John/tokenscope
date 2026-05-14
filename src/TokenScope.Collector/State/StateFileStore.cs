using System.Text.Json;
using System.Text.Json.Serialization;

namespace TokenScope.Collector.State;

/// <summary>
/// Atomic load/save of the resume-state file. Corruption is converted into
/// "empty state with warning" rather than a thrown exception — the
/// collector's design treats stale state as "rescan from the beginning,"
/// which is always safe, just more work.
/// </summary>
public static class StateFileStore
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    /// <summary>
    /// Load the state file. Returns <see cref="ResumeState.Empty"/> if the
    /// file does not exist or contains malformed JSON. The
    /// <paramref name="onWarning"/> callback is invoked for the latter
    /// case so the host can log it.
    /// </summary>
    public static ResumeState Load(string filePath, Action<string>? onWarning = null)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(filePath);

        if (!File.Exists(filePath))
        {
            return ResumeState.Empty;
        }

        try
        {
            using var stream = new FileStream(
                filePath,
                FileMode.Open,
                FileAccess.Read,
                FileShare.Read | FileShare.Delete);
            return JsonSerializer.Deserialize<ResumeState>(stream, JsonOptions)
                   ?? ResumeState.Empty;
        }
        catch (JsonException ex)
        {
            onWarning?.Invoke($"State file '{filePath}' is corrupt ({ex.Message}); falling back to full rescan.");
            return ResumeState.Empty;
        }
        catch (IOException ex)
        {
            onWarning?.Invoke($"State file '{filePath}' could not be read ({ex.Message}); falling back to full rescan.");
            return ResumeState.Empty;
        }
    }

    /// <summary>
    /// Atomically save the state file. Writes to <c>{path}.tmp</c> first,
    /// then <see cref="File.Move(string, string, bool)"/>s into place. A
    /// crash mid-write cannot corrupt the destination file.
    /// </summary>
    public static void Save(string filePath, ResumeState state)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(filePath);
        ArgumentNullException.ThrowIfNull(state);

        var dir = Path.GetDirectoryName(filePath)
                  ?? throw new ArgumentException(
                      $"Cannot derive directory from '{filePath}'.",
                      nameof(filePath));
        Directory.CreateDirectory(dir);

        var tempPath = filePath + ".tmp";
        using (var stream = new FileStream(
                   tempPath,
                   FileMode.Create,
                   FileAccess.Write,
                   FileShare.None))
        {
            JsonSerializer.Serialize(stream, state, JsonOptions);
            stream.Flush();
        }

        File.Move(tempPath, filePath, overwrite: true);
    }
}
