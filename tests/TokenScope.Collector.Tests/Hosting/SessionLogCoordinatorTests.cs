using System.Diagnostics.Metrics;
using AwesomeAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using TokenScope.Collector.Configuration;
using TokenScope.Collector.Hosting;
using TokenScope.Collector.State;
using TokenScope.Core.Pricing;
using TokenScope.Otel.Metrics;
using TokenScope.Otel.Tracking;
using Xunit;

namespace TokenScope.Collector.Tests.Hosting;

public class SessionLogCoordinatorTests : IDisposable
{
    private readonly string _tempHome;
    private readonly string _projectsDir;
    private readonly string _stateDir;
    private readonly IPricingTable _pricing;

    public SessionLogCoordinatorTests()
    {
        _tempHome = Path.Combine(Path.GetTempPath(), "tokenscope-coord-" + Guid.NewGuid().ToString("N"));
        _projectsDir = Path.Combine(_tempHome, ".claude", "projects");
        _stateDir = Path.Combine(_tempHome, ".tokenscope", "state");
        Directory.CreateDirectory(Path.Combine(_projectsDir, "test-proj"));
        Directory.CreateDirectory(_stateDir);

        // A minimal pricing table covering opus-4-7 only — sufficient for fixtures.
        _pricing = PricingLoader.LoadFromJson(PricingJson());
    }

    public void Dispose()
    {
        try { Directory.Delete(_tempHome, recursive: true); } catch { }
    }

    [Fact]
    public async Task InitialScan_ProcessesPreExistingFile_EmitsMetrics_PersistsState()
    {
        var sessionFile = Path.Combine(_projectsDir, "test-proj", "session-1.jsonl");
        File.WriteAllText(sessionFile, AssistantLine("session-1", "req-A", input: 100, output: 50));

        using var capture = new MetricCapture();
        using var coordinator = NewCoordinator(out var disposeMetrics);

        await RunUntilQuiet(coordinator);

        capture.SampleObservables();
        capture.Samples.Should().Contain(s => s.Name == "tokenscope.tokens.input");

        var statePath = Path.Combine(_stateDir, "seen.json");
        var savedState = StateFileStore.Load(statePath);
        savedState.Files.Should().ContainSingle()
            .Which.Path.Should().Be(sessionFile);
        savedState.Files[0].ByteOffset.Should().BeGreaterThan(0);

        disposeMetrics();
    }

    [Fact]
    public async Task DuplicateRequestId_OnlyCountedOnce()
    {
        var sessionFile = Path.Combine(_projectsDir, "test-proj", "session-dup.jsonl");
        var line = AssistantLine("session-dup", "req-DUP", input: 10, output: 10);
        File.WriteAllText(sessionFile, line + line); // same requestId twice in a row

        using var capture = new MetricCapture();
        using var coordinator = NewCoordinator(out var disposeMetrics);

        await RunUntilQuiet(coordinator);

        var requestsTotal = capture.Samples.Where(s => s.Name == "tokenscope.requests.total").ToList();
        requestsTotal.Should().ContainSingle("dedup should drop the second appearance");
        requestsTotal[0].Value.Should().Be(1L);

        disposeMetrics();
    }

    [Fact]
    public async Task PartialLastLine_NotProcessedYet_OffsetDoesNotAdvancePastPartial()
    {
        var sessionFile = Path.Combine(_projectsDir, "test-proj", "session-partial.jsonl");
        var goodLine = AssistantLine("session-partial", "req-OK", input: 5, output: 5);
        var partial = "{\"type\":\"assistant\",\"requestId\":\"req_TRUNC\",\"sessio";
        File.WriteAllBytes(sessionFile, System.Text.Encoding.UTF8.GetBytes(goodLine + partial));

        using var capture = new MetricCapture();
        using var coordinator = NewCoordinator(out var disposeMetrics);

        await RunUntilQuiet(coordinator);

        // Only the good line was processed.
        capture.Samples.Where(s => s.Name == "tokenscope.requests.total").Should().ContainSingle();

        var state = StateFileStore.Load(Path.Combine(_stateDir, "seen.json"));
        var entry = state.Files.Single();
        // Offset is past the goodLine but before the partial.
        entry.ByteOffset.Should().Be(System.Text.Encoding.UTF8.GetByteCount(goodLine));

        disposeMetrics();
    }

    [Fact]
    public async Task ResumeFromState_DoesNotReprocessAlreadySeen()
    {
        var sessionFile = Path.Combine(_projectsDir, "test-proj", "session-resume.jsonl");
        var firstLine = AssistantLine("session-resume", "req-1", input: 5, output: 5);
        var secondLine = AssistantLine("session-resume", "req-2", input: 5, output: 5);
        File.WriteAllText(sessionFile, firstLine + secondLine);

        // Seed state to point past the first line.
        var seededOffset = System.Text.Encoding.UTF8.GetByteCount(firstLine);
        var state = new ResumeState
        {
            Files = System.Collections.Immutable.ImmutableArray.Create(
                new ResumeFileEntry
                {
                    Path = sessionFile,
                    LastModifiedUtc = File.GetLastWriteTimeUtc(sessionFile),
                    ByteOffset = seededOffset,
                    LastProcessedLineNumber = 1,
                }),
        };
        StateFileStore.Save(Path.Combine(_stateDir, "seen.json"), state);

        using var capture = new MetricCapture();
        using var coordinator = NewCoordinator(out var disposeMetrics);

        await RunUntilQuiet(coordinator);

        // Only req-2 processed (req-1 skipped by offset).
        var requests = capture.Samples.Where(s => s.Name == "tokenscope.requests.total").ToList();
        requests.Should().ContainSingle();

        disposeMetrics();
    }

    [Fact]
    public async Task InitialScanDisabled_DoesNotProcessPreExistingFiles()
    {
        var sessionFile = Path.Combine(_projectsDir, "test-proj", "session-noscan.jsonl");
        File.WriteAllText(sessionFile, AssistantLine("session-noscan", "req-X", input: 5, output: 5));

        using var capture = new MetricCapture();
        var (coordinator, dispose) = NewCoordinatorWithOptions(initialScanEnabled: false);
        using (coordinator)
        {
            await RunUntilQuiet(coordinator);
        }

        capture.Samples.Where(s => s.Name == "tokenscope.requests.total").Should().BeEmpty();
        dispose();
    }

    [Fact]
    public async Task LiveAppend_DetectedByFileSystemWatcher()
    {
        var sessionFile = Path.Combine(_projectsDir, "test-proj", "session-live.jsonl");
        File.WriteAllText(sessionFile, AssistantLine("session-live", "req-1", input: 5, output: 5));

        using var capture = new MetricCapture();
        using var coordinator = NewCoordinator(out var disposeMetrics);

        // Start the coordinator and keep it running.
        using var cts = new CancellationTokenSource();
        await coordinator.StartAsync(cts.Token);

        // Allow the initial scan to settle.
        await Task.Delay(TimeSpan.FromMilliseconds(600));
        var initialRequests = capture.Samples.Count(s => s.Name == "tokenscope.requests.total");
        initialRequests.Should().Be(1, "the initial scan should process req-1");

        // Append a second event while the coordinator is still running.
        File.AppendAllText(sessionFile, AssistantLine("session-live", "req-2", input: 5, output: 5));

        // Poll for up to 5s waiting for the watcher to fire + debounce + process.
        var deadline = DateTimeOffset.UtcNow + TimeSpan.FromSeconds(5);
        while (DateTimeOffset.UtcNow < deadline)
        {
            if (capture.Samples.Count(s => s.Name == "tokenscope.requests.total") > initialRequests)
            {
                break;
            }
            await Task.Delay(100);
        }

        await coordinator.StopAsync(CancellationToken.None);
        cts.Cancel();

        capture.Samples.Count(s => s.Name == "tokenscope.requests.total")
            .Should().BeGreaterThan(initialRequests);

        disposeMetrics();
    }

    // ---- helpers ----

    private SessionLogCoordinator NewCoordinator(out Action disposeMetrics)
    {
        var (coordinator, dispose) = NewCoordinatorWithOptions(initialScanEnabled: true);
        disposeMetrics = dispose;
        return coordinator;
    }

    private (SessionLogCoordinator Coordinator, Action DisposeMetrics) NewCoordinatorWithOptions(bool initialScanEnabled)
    {
        var options = new TokenScopeOptions
        {
            SessionLogs = new SessionLogsOptions
            {
                Path = _projectsDir,
                InitialScanEnabled = initialScanEnabled,
                InitialScanMaxAgeDays = null,
                ActiveSessionWindowMinutes = 10,
            },
        };
        var resolved = new ResolvedTokenScopeOptions(
            options,
            SessionLogsPath: _projectsDir,
            SessionLogsPathIsExplicit: true,
            StatePath: _stateDir,
            PricingConfigPath: "");

        var metrics = new TokenScopeMetrics(
            new SessionActivityTracker(),
            new CacheRatioTracker(),
            activeWindow: TimeSpan.FromMinutes(10));

        var coordinator = new SessionLogCoordinator(
            NullLogger<SessionLogCoordinator>.Instance,
            resolved,
            _pricing,
            metrics);

        return (coordinator, () => metrics.Dispose());
    }

    private static async Task RunUntilQuiet(SessionLogCoordinator coordinator, int settleMs = 600)
    {
        using var cts = new CancellationTokenSource();
        await coordinator.StartAsync(cts.Token);
        // Let initial scan + watcher process pre-existing files
        await Task.Delay(settleMs);
        cts.Cancel();
        await coordinator.StopAsync(CancellationToken.None);
    }

    private static string AssistantLine(string sessionId, string requestId, long input, long output)
    {
        var inv = System.Globalization.CultureInfo.InvariantCulture;
        return "{\"type\":\"assistant\",\"requestId\":\"" + requestId
            + "\",\"sessionId\":\"" + sessionId
            + "\",\"uuid\":\"u-" + requestId
            + "\",\"timestamp\":\"2026-05-14T12:00:00.000Z\",\"cwd\":\"/x\",\"version\":\"2.1.126\",\"userType\":\"external\",\"isSidechain\":false,\"parentUuid\":null,"
            + "\"message\":{\"id\":\"msg-" + requestId
            + "\",\"model\":\"claude-opus-4-7\",\"role\":\"assistant\",\"type\":\"message\",\"usage\":{"
            + "\"input_tokens\":" + input.ToString(inv)
            + ",\"output_tokens\":" + output.ToString(inv)
            + ",\"cache_read_input_tokens\":0,\"cache_creation\":{\"ephemeral_5m_input_tokens\":0,\"ephemeral_1h_input_tokens\":0}}}}\n";
    }

    private static string PricingJson() => """
        {
          "schema_version": 1,
          "currency": "USD",
          "models": [{
            "id": "claude-opus-4-7",
            "rates": [{
              "effective_date": "2026-01-01T00:00:00Z",
              "input_per_mtok": 5.00,
              "output_per_mtok": 25.00,
              "cache_read_per_mtok": 0.50,
              "cache_write_5m_per_mtok": 6.25,
              "cache_write_1h_per_mtok": 10.00
            }]
          }]
        }
        """;
}

/// <summary>
/// MeterListener-based in-memory capture, mirrors the Phase 4 helper in
/// TokenScope.Otel.Tests. Kept inline to avoid cross-test-project sharing.
/// </summary>
internal sealed class MetricCapture : IDisposable
{
    public sealed record Sample(string Name, object Value);

    private readonly MeterListener _listener;
    private readonly List<Sample> _samples = new();
    private readonly object _gate = new();

    public IReadOnlyList<Sample> Samples
    {
        get
        {
            lock (_gate)
            {
                return _samples.ToList();
            }
        }
    }

    public MetricCapture()
    {
        _listener = new MeterListener
        {
            InstrumentPublished = (instrument, listener) =>
            {
                if (instrument.Meter.Name == TokenScopeMetrics.MeterName)
                {
                    listener.EnableMeasurementEvents(instrument);
                }
            },
        };
        _listener.SetMeasurementEventCallback<long>(Record<long>);
        _listener.SetMeasurementEventCallback<double>(Record<double>);
        _listener.Start();
    }

    public void SampleObservables() => _listener.RecordObservableInstruments();

    public void Dispose() => _listener.Dispose();

    private void Record<T>(Instrument instrument, T measurement, ReadOnlySpan<KeyValuePair<string, object?>> tags, object? _)
        where T : struct
    {
        var sample = new Sample(instrument.Name, measurement!);
        lock (_gate)
        {
            _samples.Add(sample);
        }
    }
}
