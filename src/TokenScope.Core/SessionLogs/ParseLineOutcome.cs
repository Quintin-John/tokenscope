using System.Collections.Immutable;

namespace TokenScope.Core.SessionLogs;

/// <summary>
/// Outcome of parsing a single JSON line. The
/// <see cref="SessionLogParser.ParseLine"/> entry point is for callers
/// (like the Collector) that read the file themselves and need exact
/// byte-offset tracking — the iterator-based
/// <see cref="SessionLogParser.EnumerateAssistantEvents(string, Action{ParseWarning}?)"/>
/// API is fine for non-resumable contexts.
/// </summary>
public abstract record ParseLineOutcome
{
    private ParseLineOutcome() { }

    public sealed record Blank : ParseLineOutcome
    {
        public static Blank Instance { get; } = new();
    }

    public sealed record SkippedNonAssistant : ParseLineOutcome
    {
        public static SkippedNonAssistant Instance { get; } = new();
    }

    public sealed record AssistantEvent(ParsedAssistantEvent Value) : ParseLineOutcome;

    public sealed record MalformedJson(ParseWarning.MalformedJson Warning) : ParseLineOutcome;

    public sealed record InvalidEvent(ImmutableArray<ParseWarning> Warnings) : ParseLineOutcome;
}
