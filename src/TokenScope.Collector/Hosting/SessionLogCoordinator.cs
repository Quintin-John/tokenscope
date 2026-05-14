using System.Collections.Concurrent;
using System.Collections.Immutable;
using System.Globalization;
using System.Threading.Channels;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using TokenScope.Collector.Configuration;
using TokenScope.Collector.State;
using TokenScope.Core.Costing;
using TokenScope.Core.Domain;
using TokenScope.Core.Pricing;
using TokenScope.Core.SessionLogs;
using TokenScope.Otel.Metrics;

namespace TokenScope.Collector.Hosting;

/// <summary>
/// The host's main work loop. Wires together:
///
/// <list type="bullet">
///   <item>Initial scan of <c>~/.claude/projects/**/*.jsonl</c> from
///         resume offsets in the state file.</item>
///   <item><see cref="FileSystemWatcher"/> on the session-logs root, with
///         debounced re-processing.</item>
///   <item>Per-line parse via <see cref="SessionLogParser.ParseLine"/>,
///         dedup by <c>(sessionId, requestId)</c>, cost calculation via
///         <see cref="CostCalculator"/>, metric emission via
///         <see cref="TokenScopeMetrics"/>.</item>
///   <item>Atomic state file flush on graceful shutdown.</item>
/// </list>
/// </summary>
public sealed class SessionLogCoordinator : BackgroundService
{
    private readonly ILogger<SessionLogCoordinator> _logger;
    private readonly ResolvedTokenScopeOptions _options;
    private readonly IPricingTable _pricing;
    private readonly TokenScopeMetrics _metrics;
    private readonly TimeProvider _clock;

    private readonly ConcurrentDictionary<string, ResumeFileEntry> _state =
        new(StringComparer.Ordinal);

    private readonly HashSet<(string Session, string Request)> _dedup =
        new(StringTupleComparer.Instance);

    private readonly Channel<string> _workQueue = Channel.CreateUnbounded<string>(
        new UnboundedChannelOptions { SingleReader = true });

    private readonly TimeSpan _debounce = TimeSpan.FromMilliseconds(250);
    private readonly ConcurrentDictionary<string, DateTimeOffset> _lastEnqueuedAt =
        new(StringComparer.Ordinal);

    private readonly object _processingLock = new();

    private FileSystemWatcher? _watcher;
    private string StatePath => Path.Combine(_options.StatePath, "seen.json");

    public SessionLogCoordinator(
        ILogger<SessionLogCoordinator> logger,
        ResolvedTokenScopeOptions options,
        IPricingTable pricing,
        TokenScopeMetrics metrics,
        TimeProvider? clock = null)
    {
        _logger = logger;
        _options = options;
        _pricing = pricing;
        _metrics = metrics;
        _clock = clock ?? TimeProvider.System;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        LogResolvedPaths();
        LoadStateAndRebuildDedup();

        EnsureSessionLogsDirectoryHandled();

        StartFileSystemWatcher();

        if (_options.Options.SessionLogs.InitialScanEnabled)
        {
            EnqueueInitialScan();
        }

        await ProcessWorkQueueAsync(stoppingToken).ConfigureAwait(false);

        // Graceful shutdown — flush state one last time.
        TryFlushState();
    }

    public override async Task StopAsync(CancellationToken cancellationToken)
    {
        _workQueue.Writer.TryComplete();
        if (_watcher is not null)
        {
            _watcher.EnableRaisingEvents = false;
            _watcher.Dispose();
            _watcher = null;
        }
        await base.StopAsync(cancellationToken).ConfigureAwait(false);
    }

    // ---- private ----

    private void LogResolvedPaths()
    {
        _logger.LogInformation(
            "Resolved paths: session_logs={SessionLogsPath} (explicit={Explicit}), state={StatePath}, pricing={PricingPath}",
            _options.SessionLogsPath,
            _options.SessionLogsPathIsExplicit,
            _options.StatePath,
            _options.PricingConfigPath);
    }

    private void LoadStateAndRebuildDedup()
    {
        var loaded = StateFileStore.Load(StatePath, msg => _logger.LogWarning("{Msg}", msg));

        var (resumed, fullScan) = (0, 0);
        foreach (var entry in loaded.Files)
        {
            if (!File.Exists(entry.Path))
            {
                fullScan++;
                continue;
            }
            var info = new FileInfo(entry.Path);
            if (info.Length < entry.ByteOffset || info.LastWriteTimeUtc != entry.LastModifiedUtc)
            {
                _logger.LogWarning(
                    "State for '{Path}' is stale (length={Len}, offset={Off}, mtime={Mtime}); full rescan.",
                    entry.Path, info.Length, entry.ByteOffset, info.LastWriteTimeUtc);
                fullScan++;
                continue;
            }
            _state[entry.Path] = entry;
            resumed++;
        }

        _logger.LogInformation(
            "Resumed {Resumed} files from state; {FullScan} files require full rescan; dedup set starts empty.",
            resumed, fullScan);
    }

    private void EnsureSessionLogsDirectoryHandled()
    {
        if (Directory.Exists(_options.SessionLogsPath))
        {
            return;
        }

        if (_options.SessionLogsPathIsExplicit)
        {
            // Validation should have caught this at load time, but defensive.
            throw new InvalidOperationException(
                $"session_logs.path '{_options.SessionLogsPath}' does not exist.");
        }

        _logger.LogWarning(
            "Auto-detected session_logs path '{Path}' does not exist; watching for creation.",
            _options.SessionLogsPath);
        // Watch the parent directory so the FileSystemWatcher catches subdir creation.
        Directory.CreateDirectory(_options.SessionLogsPath);
    }

    private void StartFileSystemWatcher()
    {
        _watcher = new FileSystemWatcher(_options.SessionLogsPath, "*.jsonl")
        {
            IncludeSubdirectories = true,
            NotifyFilter = NotifyFilters.LastWrite
                          | NotifyFilters.FileName
                          | NotifyFilters.Size
                          | NotifyFilters.CreationTime,
            EnableRaisingEvents = true,
        };
        _watcher.Changed += OnFileEvent;
        _watcher.Created += OnFileEvent;
        _watcher.Renamed += OnFileRenamed;
    }

    private void OnFileEvent(object sender, FileSystemEventArgs e) =>
        EnqueueWithDebounce(e.FullPath);

    private void OnFileRenamed(object sender, RenamedEventArgs e) =>
        EnqueueWithDebounce(e.FullPath);

    private void EnqueueInitialScan()
    {
        if (!Directory.Exists(_options.SessionLogsPath))
        {
            return;
        }

        var maxAgeDays = _options.Options.SessionLogs.InitialScanMaxAgeDays;
        DateTimeOffset? cutoff = maxAgeDays is { } days
            ? _clock.GetUtcNow() - TimeSpan.FromDays(days)
            : null;

        var queued = 0;
        foreach (var file in Directory.EnumerateFiles(
                     _options.SessionLogsPath, "*.jsonl", SearchOption.AllDirectories))
        {
            if (cutoff is { } c)
            {
                var mtime = File.GetLastWriteTimeUtc(file);
                if (mtime < c.UtcDateTime)
                {
                    continue;
                }
            }
            _workQueue.Writer.TryWrite(file);
            queued++;
        }

        _logger.LogInformation(
            "Initial scan enqueued {Count} files (max age={Days} days).",
            queued, maxAgeDays?.ToString(CultureInfo.InvariantCulture) ?? "none");
    }

    private void EnqueueWithDebounce(string path)
    {
        var now = _clock.GetUtcNow();
        var last = _lastEnqueuedAt.GetOrAdd(path, DateTimeOffset.MinValue);
        if (now - last < _debounce)
        {
            return;
        }
        _lastEnqueuedAt[path] = now;
        _workQueue.Writer.TryWrite(path);
    }

    private async Task ProcessWorkQueueAsync(CancellationToken stoppingToken)
    {
        try
        {
            await foreach (var path in _workQueue.Reader.ReadAllAsync(stoppingToken).ConfigureAwait(false))
            {
                try
                {
                    ProcessFile(path);
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "Failed to process '{Path}'; continuing.", path);
                }
            }
        }
        catch (OperationCanceledException)
        {
            // Expected on shutdown.
        }
    }

    private void ProcessFile(string filePath)
    {
        if (!File.Exists(filePath))
        {
            _state.TryRemove(filePath, out _);
            return;
        }

        lock (_processingLock)
        {
            var info = new FileInfo(filePath);
            var existing = _state.TryGetValue(filePath, out var s)
                ? s
                : new ResumeFileEntry { Path = filePath };

            var offset = existing.ByteOffset;
            var lineNumber = existing.LastProcessedLineNumber;

            if (info.Length < offset)
            {
                _logger.LogWarning(
                    "File '{Path}' shrank (len={Len} < offset={Off}); rescanning from start.",
                    filePath, info.Length, offset);
                offset = 0;
                lineNumber = 0;
            }

            if (info.Length == offset)
            {
                return; // nothing new
            }

            using var stream = new FileStream(
                filePath, FileMode.Open, FileAccess.Read,
                FileShare.ReadWrite | FileShare.Delete);

            foreach (var (line, postOffset, nextLine) in ReadCompleteLinesFrom(stream, offset, lineNumber))
            {
                lineNumber = nextLine;
                offset = postOffset;
                ProcessLine(line, filePath, lineNumber);
            }

            _state[filePath] = existing with
            {
                Path = filePath,
                ByteOffset = offset,
                LastProcessedLineNumber = lineNumber,
                LastModifiedUtc = info.LastWriteTimeUtc,
            };
            TryFlushState();
        }
    }

    private void ProcessLine(string line, string source, int lineNumber)
    {
        var outcome = SessionLogParser.ParseLine(line, source, lineNumber);
        switch (outcome)
        {
            case ParseLineOutcome.AssistantEvent ae:
                HandleEvent(ae.Value);
                break;
            case ParseLineOutcome.MalformedJson mj:
                _logger.LogWarning("Malformed JSON at {Source}:{Line} — {Reason}",
                    mj.Warning.Source, mj.Warning.LineNumber, mj.Warning.Reason);
                break;
            case ParseLineOutcome.InvalidEvent ie:
                foreach (var w in ie.Warnings)
                {
                    if (w is ParseWarning.MissingRequiredField mrf)
                    {
                        _logger.LogWarning("Missing field at {Source}:{Line} — {Field}",
                            mrf.Source, mrf.LineNumber, mrf.FieldPath);
                    }
                }
                break;
            case ParseLineOutcome.SkippedNonAssistant:
            case ParseLineOutcome.Blank:
            default:
                break;
        }
    }

    private void HandleEvent(ParsedAssistantEvent ev)
    {
        var key = (ev.SessionId, ev.RequestId);
        if (!_dedup.Add(key))
        {
            return; // already counted this requestId in this process
        }

        var result = CostCalculator.Calculate(ev.ToRequest(), _pricing);
        switch (result)
        {
            case CostResult.Success s:
                _metrics.RecordRequest(ev.Model, ev.SessionId, ev.Usage, s.Cost, ev.Timestamp);
                break;
            case CostResult.ModelNotFound mnf:
                _logger.LogDebug(
                    "Skipping request {RequestId}: model '{Model}' not in pricing config.",
                    ev.RequestId, mnf.ModelId);
                break;
            case CostResult.NoRateEffective nre:
                _logger.LogWarning(
                    "No effective rate for model '{Model}' at {At} (request {RequestId}); skipping.",
                    nre.ModelId, nre.RequestedAt, ev.RequestId);
                break;
        }
    }

    private void TryFlushState()
    {
        try
        {
            var snapshot = new ResumeState
            {
                Files = _state.Values
                    .OrderBy(e => e.Path, StringComparer.Ordinal)
                    .ToImmutableArray(),
            };
            StateFileStore.Save(StatePath, snapshot);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Failed to flush state to '{Path}'.", StatePath);
        }
    }

    /// <summary>
    /// Yields complete (newline-terminated) lines from <paramref name="stream"/>
    /// starting at <paramref name="startOffset"/>, along with each line's
    /// post-newline byte offset and 1-based line number. A partial last line
    /// (no terminating '\n') is intentionally not yielded — the resume offset
    /// only advances past confirmed line boundaries.
    /// </summary>
    private static IEnumerable<(string Line, long PostOffset, int LineNumber)> ReadCompleteLinesFrom(
        Stream stream, long startOffset, int startLineNumber)
    {
        stream.Seek(startOffset, SeekOrigin.Begin);

        var lineNumber = startLineNumber;
        var line = new MemoryStream();
        long offset = startOffset;
        var buffer = new byte[8192];

        int read;
        while ((read = stream.Read(buffer, 0, buffer.Length)) > 0)
        {
            for (var i = 0; i < read; i++)
            {
                offset++;
                if (buffer[i] == (byte)'\n')
                {
                    var lineStr = System.Text.Encoding.UTF8.GetString(line.GetBuffer(), 0, (int)line.Length);
                    line.SetLength(0);
                    lineNumber++;
                    yield return (lineStr, offset, lineNumber);
                }
                else
                {
                    line.WriteByte(buffer[i]);
                }
            }
        }
        // Partial last line in `line` is discarded.
    }

    private sealed class StringTupleComparer : IEqualityComparer<(string, string)>
    {
        public static StringTupleComparer Instance { get; } = new();
        public bool Equals((string, string) x, (string, string) y) =>
            string.Equals(x.Item1, y.Item1, StringComparison.Ordinal)
            && string.Equals(x.Item2, y.Item2, StringComparison.Ordinal);
        public int GetHashCode((string, string) obj) =>
            HashCode.Combine(obj.Item1, obj.Item2);
    }
}
