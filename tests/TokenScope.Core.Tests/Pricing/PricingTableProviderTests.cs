using System.Collections.Concurrent;
using System.Text;
using AwesomeAssertions;
using TokenScope.Core.Pricing;
using Xunit;

namespace TokenScope.Core.Tests.Pricing;

public class PricingTableProviderTests : IDisposable
{
    private readonly string _tempDir;
    private readonly string _pricingPath;

    public PricingTableProviderTests()
    {
        _tempDir = Path.Combine(Path.GetTempPath(), "tokenscope-tests-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(_tempDir);
        _pricingPath = Path.Combine(_tempDir, "pricing.json");
    }

    public void Dispose()
    {
        try { Directory.Delete(_tempDir, recursive: true); } catch { /* best-effort */ }
    }

    [Fact]
    public void Construction_LoadsInitialTableAndRaisesLoadedEvent()
    {
        File.WriteAllText(_pricingPath, SimpleConfig(input: 5m));
        var events = new ConcurrentQueue<PricingReloadEvent>();

        using var provider = new PricingTableProvider(
            _pricingPath,
            watch: false,
            onReload: events.Enqueue);

        provider.Current.KnownModelIds.Should().Contain("claude-opus-4-7");
        events.Should().ContainSingle().Which.Should().BeOfType<PricingReloadEvent.Loaded>();
    }

    [Fact]
    public void Reload_OnValidChange_SwapsTableAtomically()
    {
        File.WriteAllText(_pricingPath, SimpleConfig(input: 5m));
        using var provider = new PricingTableProvider(_pricingPath, watch: false);

        File.WriteAllText(_pricingPath, SimpleConfig(input: 7m));
        provider.Reload();

        var lookup = provider.Lookup("claude-opus-4-7", new DateTimeOffset(2026, 5, 1, 0, 0, 0, TimeSpan.Zero));
        lookup.Should().BeOfType<PricingLookupResult.Found>()
            .Which.Rate.InputPerMTok.Should().Be(7m);
    }

    [Fact]
    public void Reload_OnValidationError_KeepsPreviousTableAndReportsFailure()
    {
        File.WriteAllText(_pricingPath, SimpleConfig(input: 5m));
        var events = new ConcurrentQueue<PricingReloadEvent>();
        using var provider = new PricingTableProvider(
            _pricingPath,
            watch: false,
            onReload: events.Enqueue);

        // Corrupt the file: negative rate violates validation.
        File.WriteAllText(_pricingPath, SimpleConfig(input: -1m));
        provider.Reload();

        // Table is unchanged.
        var lookup = provider.Lookup("claude-opus-4-7", new DateTimeOffset(2026, 5, 1, 0, 0, 0, TimeSpan.Zero));
        lookup.Should().BeOfType<PricingLookupResult.Found>()
            .Which.Rate.InputPerMTok.Should().Be(5m);

        events.Should().Contain(e => e is PricingReloadEvent.ValidationFailed);
    }

    [Fact]
    public void Reload_TemporalCorrectness_OldRatesForOldTimestampsAfterReload()
    {
        // Pricing config preserves historical entries; new file adds a new entry.
        File.WriteAllText(_pricingPath, MultiRateConfig());
        using var provider = new PricingTableProvider(_pricingPath, watch: false);

        var oldDate = new DateTimeOffset(2026, 1, 15, 0, 0, 0, TimeSpan.Zero);
        var newDate = new DateTimeOffset(2026, 4, 15, 0, 0, 0, TimeSpan.Zero);

        var oldLookup = provider.Lookup("claude-opus-4-7", oldDate);
        var newLookup = provider.Lookup("claude-opus-4-7", newDate);

        oldLookup.Should().BeOfType<PricingLookupResult.Found>()
            .Which.Rate.InputPerMTok.Should().Be(3m);  // old rate
        newLookup.Should().BeOfType<PricingLookupResult.Found>()
            .Which.Rate.InputPerMTok.Should().Be(5m);  // new rate
    }

    [Fact]
    public void Reload_AfterDispose_IsNoOp()
    {
        File.WriteAllText(_pricingPath, SimpleConfig(input: 5m));
        var provider = new PricingTableProvider(_pricingPath, watch: false);
        provider.Dispose();

        var act = provider.Reload;

        act.Should().NotThrow();
    }

    [Fact]
    public void Dispose_IsIdempotent()
    {
        File.WriteAllText(_pricingPath, SimpleConfig(input: 5m));
        var provider = new PricingTableProvider(_pricingPath, watch: false);

        provider.Dispose();
        var act = provider.Dispose;

        act.Should().NotThrow();
    }

    [Fact]
    public void FileSystemWatcher_OnFileEdit_TriggersDebouncedReload()
    {
        File.WriteAllText(_pricingPath, SimpleConfig(input: 5m));
        var reloadedSignal = new ManualResetEventSlim(false);

        using var provider = new PricingTableProvider(
            _pricingPath,
            watch: true,
            debounceInterval: TimeSpan.FromMilliseconds(50),
            onReload: ev =>
            {
                // Ignore the initial Loaded event; signal only on subsequent loads.
                if (ev is PricingReloadEvent.Loaded l && l.Table.Lookup(
                        "claude-opus-4-7",
                        new DateTimeOffset(2026, 5, 1, 0, 0, 0, TimeSpan.Zero))
                    is PricingLookupResult.Found f && f.Rate.InputPerMTok == 9m)
                {
                    reloadedSignal.Set();
                }
            });

        File.WriteAllText(_pricingPath, SimpleConfig(input: 9m));

        reloadedSignal.Wait(TimeSpan.FromSeconds(5)).Should().BeTrue("watcher should fire within 5s");
    }

    private static string SimpleConfig(decimal input)
    {
        var sb = new StringBuilder();
        sb.AppendLine("{");
        sb.AppendLine("  \"schema_version\": 1,");
        sb.AppendLine("  \"models\": [{");
        sb.AppendLine("    \"id\": \"claude-opus-4-7\",");
        sb.AppendLine("    \"rates\": [{");
        sb.AppendLine("      \"effective_date\": \"2026-01-01T00:00:00Z\",");
        sb.AppendLine($"      \"input_per_mtok\": {input.ToString(System.Globalization.CultureInfo.InvariantCulture)},");
        sb.AppendLine("      \"output_per_mtok\": 25.00,");
        sb.AppendLine("      \"cache_read_per_mtok\": 0.50,");
        sb.AppendLine("      \"cache_write_5m_per_mtok\": 6.25,");
        sb.AppendLine("      \"cache_write_1h_per_mtok\": 10.00");
        sb.AppendLine("    }]");
        sb.AppendLine("  }]");
        sb.AppendLine("}");
        return sb.ToString();
    }

    private static string MultiRateConfig()
    {
        // Two effective dates for the same model: $3/MTok input starting 2026-01-01,
        // bumped to $5/MTok starting 2026-04-01.
        return """
            {
              "schema_version": 1,
              "models": [{
                "id": "claude-opus-4-7",
                "rates": [
                  {
                    "effective_date": "2026-01-01T00:00:00Z",
                    "input_per_mtok": 3.00,
                    "output_per_mtok": 15.00,
                    "cache_read_per_mtok": 0.30,
                    "cache_write_5m_per_mtok": 3.75,
                    "cache_write_1h_per_mtok": 6.00
                  },
                  {
                    "effective_date": "2026-04-01T00:00:00Z",
                    "input_per_mtok": 5.00,
                    "output_per_mtok": 25.00,
                    "cache_read_per_mtok": 0.50,
                    "cache_write_5m_per_mtok": 6.25,
                    "cache_write_1h_per_mtok": 10.00
                  }
                ]
              }]
            }
            """;
    }
}
