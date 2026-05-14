using System.Text.Json;
using TokenScope.Core.Domain;

namespace TokenScope.Core.SessionLogs;

/// <summary>
/// Parses Claude Code <c>.jsonl</c> session logs into cost-relevant
/// <see cref="ParsedAssistantEvent"/> records. The parser:
///
/// <list type="bullet">
///   <item>Reads line by line — never materializes the whole file.</item>
///   <item>Opens with <c>FileShare.ReadWrite | FileShare.Delete</c> so a live
///         session (still being written) can be read concurrently.</item>
///   <item>Skips non-<c>assistant</c> events silently — they don't carry token usage.</item>
///   <item>Warns and continues on malformed JSON or missing required fields.</item>
///   <item>Marks duplicate <c>(SessionId, RequestId)</c> appearances via
///         <see cref="ParsedAssistantEvent.IsDuplicate"/> so downstream
///         aggregators can dedupe without dropping the underlying events.</item>
///   <item>Never deserializes prompt content, tool inputs, thinking text, or
///         file-history-snapshot bodies — they are not in the DTO graph.</item>
/// </list>
/// </summary>
public static class SessionLogParser
{
    private const string AssistantType = "assistant";

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        ReadCommentHandling = JsonCommentHandling.Skip,
        AllowTrailingCommas = true,
    };

    public static IEnumerable<ParsedAssistantEvent> EnumerateAssistantEvents(
        string filePath,
        Action<ParseWarning>? onWarning = null)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(filePath);

        using var stream = OpenForSharedRead(filePath);
        foreach (var ev in EnumerateAssistantEvents(stream, filePath, onWarning))
        {
            yield return ev;
        }
    }

    public static IEnumerable<ParsedAssistantEvent> EnumerateAssistantEvents(
        Stream stream,
        string source,
        Action<ParseWarning>? onWarning = null)
    {
        ArgumentNullException.ThrowIfNull(stream);
        ArgumentNullException.ThrowIfNull(source);

        var seen = new HashSet<(string Session, string Request)>(StringTupleEqualityComparer.Instance);
        using var reader = new StreamReader(stream, leaveOpen: true);

        var lineNumber = 0;
        while (reader.ReadLine() is { } line)
        {
            lineNumber++;
            if (line.Length == 0)
            {
                continue;
            }

            LogEntryDto? dto;
            try
            {
                dto = JsonSerializer.Deserialize<LogEntryDto>(line, JsonOptions);
            }
            catch (JsonException ex)
            {
                onWarning?.Invoke(new ParseWarning.MalformedJson(source, lineNumber, ex.Message));
                continue;
            }

            if (dto is null || !string.Equals(dto.Type, AssistantType, StringComparison.Ordinal))
            {
                continue;
            }

            var ev = TryProject(dto, source, lineNumber, onWarning);
            if (ev is null)
            {
                continue;
            }

            var key = (ev.SessionId, ev.RequestId);
            var isDuplicate = !seen.Add(key);
            yield return isDuplicate ? ev with { IsDuplicate = true } : ev;
        }
    }

    /// <summary>
    /// Parse one JSON-encoded line in isolation. The caller is responsible
    /// for line-by-line reading and dedup tracking — useful when a host
    /// (like the Collector) needs byte-offset accounting that the
    /// iterator-based API doesn't expose.
    ///
    /// Warnings reported via the returned outcome use
    /// <paramref name="source"/> and <paramref name="lineNumber"/> so the
    /// caller's real-file context appears in the messages.
    /// </summary>
    public static ParseLineOutcome ParseLine(string jsonLine, string source = "", int lineNumber = 1)
    {
        ArgumentNullException.ThrowIfNull(jsonLine);
        ArgumentNullException.ThrowIfNull(source);

        if (jsonLine.Length == 0)
        {
            return ParseLineOutcome.Blank.Instance;
        }

        LogEntryDto? dto;
        try
        {
            dto = System.Text.Json.JsonSerializer.Deserialize<LogEntryDto>(jsonLine, JsonOptions);
        }
        catch (System.Text.Json.JsonException ex)
        {
            return new ParseLineOutcome.MalformedJson(
                new ParseWarning.MalformedJson(source, lineNumber, ex.Message));
        }

        if (dto is null || !string.Equals(dto.Type, AssistantType, StringComparison.Ordinal))
        {
            return ParseLineOutcome.SkippedNonAssistant.Instance;
        }

        var warnings = System.Collections.Immutable.ImmutableArray.CreateBuilder<ParseWarning>();
        var ev = TryProject(dto, source, lineNumber, warnings.Add);
        if (ev is null)
        {
            return new ParseLineOutcome.InvalidEvent(warnings.ToImmutable());
        }

        return new ParseLineOutcome.AssistantEvent(ev);
    }

    private static ParsedAssistantEvent? TryProject(
        LogEntryDto dto,
        string source,
        int lineNumber,
        Action<ParseWarning>? onWarning)
    {
        if (string.IsNullOrEmpty(dto.SessionId))
        {
            onWarning?.Invoke(new ParseWarning.MissingRequiredField(source, lineNumber, "sessionId"));
            return null;
        }
        if (string.IsNullOrEmpty(dto.RequestId))
        {
            onWarning?.Invoke(new ParseWarning.MissingRequiredField(source, lineNumber, "requestId"));
            return null;
        }
        if (dto.Timestamp is null)
        {
            onWarning?.Invoke(new ParseWarning.MissingRequiredField(source, lineNumber, "timestamp"));
            return null;
        }
        if (dto.Message is null)
        {
            onWarning?.Invoke(new ParseWarning.MissingRequiredField(source, lineNumber, "message"));
            return null;
        }
        if (string.IsNullOrEmpty(dto.Message.Model))
        {
            onWarning?.Invoke(new ParseWarning.MissingRequiredField(source, lineNumber, "message.model"));
            return null;
        }
        if (string.IsNullOrEmpty(dto.Message.Id))
        {
            onWarning?.Invoke(new ParseWarning.MissingRequiredField(source, lineNumber, "message.id"));
            return null;
        }
        if (dto.Message.Usage is null)
        {
            onWarning?.Invoke(new ParseWarning.MissingRequiredField(source, lineNumber, "message.usage"));
            return null;
        }

        var usage = ProjectUsage(dto.Message.Usage);

        return new ParsedAssistantEvent(
            SessionId: dto.SessionId,
            RequestId: dto.RequestId,
            MessageId: dto.Message.Id,
            Model: dto.Message.Model,
            Timestamp: dto.Timestamp.Value,
            Cwd: dto.Cwd,
            Version: dto.Version,
            GitBranch: dto.GitBranch,
            IsSidechain: dto.IsSidechain ?? false,
            Usage: usage,
            StopReason: dto.Message.StopReason,
            ServiceTier: dto.Message.Usage.ServiceTier,
            InferenceGeo: dto.Message.Usage.InferenceGeo,
            Speed: dto.Message.Usage.Speed,
            IsDuplicate: false);
    }

    private static TokenUsage ProjectUsage(AssistantUsageDto dto) => new(
        Input: dto.InputTokens ?? 0,
        Output: dto.OutputTokens ?? 0,
        CacheRead: dto.CacheReadInputTokens ?? 0,
        CacheWrite5m: dto.CacheCreation?.Ephemeral5mInputTokens ?? 0,
        CacheWrite1h: dto.CacheCreation?.Ephemeral1hInputTokens ?? 0);

    private static FileStream OpenForSharedRead(string filePath)
    {
        const int maxAttempts = 2;
        const int backoffMs = 50;
        Exception? lastError = null;

        for (var attempt = 1; attempt <= maxAttempts; attempt++)
        {
            try
            {
                return new FileStream(
                    filePath,
                    FileMode.Open,
                    FileAccess.Read,
                    FileShare.ReadWrite | FileShare.Delete);
            }
            catch (IOException ex) when (attempt < maxAttempts)
            {
                lastError = ex;
                Thread.Sleep(backoffMs);
            }
        }

        throw new IOException(
            $"Could not open '{filePath}' for shared read after {maxAttempts} attempts.",
            lastError);
    }

    private sealed class StringTupleEqualityComparer : IEqualityComparer<(string, string)>
    {
        public static StringTupleEqualityComparer Instance { get; } = new();
        public bool Equals((string, string) x, (string, string) y) =>
            string.Equals(x.Item1, y.Item1, StringComparison.Ordinal)
            && string.Equals(x.Item2, y.Item2, StringComparison.Ordinal);
        public int GetHashCode((string, string) obj) =>
            HashCode.Combine(obj.Item1, obj.Item2);
    }
}
