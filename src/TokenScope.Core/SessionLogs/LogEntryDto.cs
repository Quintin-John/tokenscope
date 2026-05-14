using System.Text.Json.Serialization;

namespace TokenScope.Core.SessionLogs;

/// <summary>
/// JSON-deserialization shape for one line of a Claude Code session log.
/// Internal — public consumers see <see cref="ParsedAssistantEvent"/>.
///
/// Privacy note: this DTO deliberately omits content / thinking / tool_input /
/// tool_result / attachment / snapshot / lastPrompt / aiTitle bodies.
/// System.Text.Json silently skips unknown properties, so those payloads are
/// never bound to a CLR object.
/// </summary>
internal sealed class LogEntryDto
{
    public string? Type { get; set; }
    public string? Uuid { get; set; }
    public string? SessionId { get; set; }
    public string? RequestId { get; set; }
    public DateTimeOffset? Timestamp { get; set; }
    public string? Cwd { get; set; }
    public string? Version { get; set; }
    public string? GitBranch { get; set; }
    public bool? IsSidechain { get; set; }
    public AssistantMessageDto? Message { get; set; }
}

internal sealed class AssistantMessageDto
{
    public string? Id { get; set; }
    public string? Model { get; set; }
    public string? Role { get; set; }

    [JsonPropertyName("stop_reason")]
    public string? StopReason { get; set; }

    public AssistantUsageDto? Usage { get; set; }
}

internal sealed class AssistantUsageDto
{
    [JsonPropertyName("input_tokens")]
    public long? InputTokens { get; set; }

    [JsonPropertyName("output_tokens")]
    public long? OutputTokens { get; set; }

    [JsonPropertyName("cache_read_input_tokens")]
    public long? CacheReadInputTokens { get; set; }

    [JsonPropertyName("cache_creation")]
    public CacheCreationDto? CacheCreation { get; set; }

    [JsonPropertyName("service_tier")]
    public string? ServiceTier { get; set; }

    [JsonPropertyName("inference_geo")]
    public string? InferenceGeo { get; set; }

    [JsonPropertyName("speed")]
    public string? Speed { get; set; }
}

internal sealed class CacheCreationDto
{
    [JsonPropertyName("ephemeral_5m_input_tokens")]
    public long? Ephemeral5mInputTokens { get; set; }

    [JsonPropertyName("ephemeral_1h_input_tokens")]
    public long? Ephemeral1hInputTokens { get; set; }
}
