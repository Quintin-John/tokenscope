namespace TokenScope.Otel.Tracking;

/// <summary>
/// Records the most recent event timestamp per session id so the
/// <c>tokenscope.sessions.active</c> observable gauge can count
/// sessions whose latest activity falls within a configurable window.
/// </summary>
public interface ISessionActivityTracker
{
    void MarkActive(string sessionId, DateTimeOffset at);
    int CountActive(DateTimeOffset now, TimeSpan window);
}
