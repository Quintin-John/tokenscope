using System.Collections.Immutable;
using System.Text.Json.Serialization;

namespace TokenScope.Collector.State;

/// <summary>
/// On-disk shape of the resume-state file (<c>seen.json</c>). One entry per
/// session-log file the collector has processed at least one line from.
/// </summary>
public sealed record ResumeState
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; init; } = 1;

    [JsonPropertyName("files")]
    public ImmutableArray<ResumeFileEntry> Files { get; init; } = ImmutableArray<ResumeFileEntry>.Empty;

    public static ResumeState Empty { get; } = new();
}

public sealed record ResumeFileEntry
{
    [JsonPropertyName("path")]
    public string Path { get; init; } = string.Empty;

    [JsonPropertyName("last_modified_utc")]
    public DateTimeOffset LastModifiedUtc { get; init; }

    [JsonPropertyName("byte_offset")]
    public long ByteOffset { get; init; }

    [JsonPropertyName("last_processed_line_number")]
    public int LastProcessedLineNumber { get; init; }
}
