using System.Collections.Concurrent;

namespace TokenScope.Otel.Tracking;

public sealed class CacheRatioTracker : ICacheRatioSource
{
    private readonly ConcurrentDictionary<string, Counters> _state
        = new(StringComparer.Ordinal);

    public void Record(
        string sessionId,
        string project,
        string projectName,
        long cacheReadTokens,
        long inputTokens)
    {
        ArgumentException.ThrowIfNullOrEmpty(sessionId);
        ArgumentNullException.ThrowIfNull(project);
        ArgumentNullException.ThrowIfNull(projectName);

        if (cacheReadTokens < 0 || inputTokens < 0)
        {
            throw new ArgumentOutOfRangeException(
                cacheReadTokens < 0 ? nameof(cacheReadTokens) : nameof(inputTokens),
                "Token counts must be non-negative.");
        }

        _state.AddOrUpdate(
            sessionId,
            new Counters(cacheReadTokens, inputTokens, project, projectName),
            (_, existing) => existing with
            {
                CacheRead = existing.CacheRead + cacheReadTokens,
                Input = existing.Input + inputTokens,
                // Project labels are updated on every call so a session whose
                // first event lacked cwd metadata gets the value as soon as
                // an event provides it.
                Project = project,
                ProjectName = projectName,
            });
    }

    public IEnumerable<CacheRatioSnapshot> Snapshot()
    {
        foreach (var (sessionId, counters) in _state)
        {
            var denominator = counters.CacheRead + counters.Input;
            if (denominator == 0)
            {
                continue;
            }
            yield return new CacheRatioSnapshot(
                sessionId,
                counters.Project,
                counters.ProjectName,
                (double)counters.CacheRead / denominator);
        }
    }

    private readonly record struct Counters(
        long CacheRead,
        long Input,
        string Project,
        string ProjectName);
}
