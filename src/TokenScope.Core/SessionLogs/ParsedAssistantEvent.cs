using TokenScope.Core.Domain;

namespace TokenScope.Core.SessionLogs;

/// <summary>
/// Cost-relevant projection of a single Claude Code <c>assistant</c> log event.
/// All fields the cost engine needs, plus the pricing modifiers observed in the
/// log (currently extracted but not yet applied by Phase 2's cost engine).
/// Privacy: no content payloads, tool inputs, or thinking text are exposed here.
/// </summary>
public sealed record ParsedAssistantEvent(
    string SessionId,
    string RequestId,
    string MessageId,
    string Model,
    DateTimeOffset Timestamp,
    string? Cwd,
    string? Version,
    string? GitBranch,
    bool IsSidechain,
    TokenUsage Usage,
    string? StopReason,
    string? ServiceTier,
    string? InferenceGeo,
    string? Speed,
    bool IsDuplicate)
{
    /// <summary>
    /// Convenience: produce a <see cref="Request"/> suitable for
    /// <see cref="Costing.CostCalculator.Calculate"/>. Note that
    /// downstream code should still consult <see cref="IsDuplicate"/> to
    /// avoid double-counting when aggregating costs across the file.
    /// </summary>
    public Request ToRequest() => new(RequestId, Model, Timestamp, Usage);
}
