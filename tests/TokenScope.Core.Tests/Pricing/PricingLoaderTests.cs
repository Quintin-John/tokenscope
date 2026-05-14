using AwesomeAssertions;
using TokenScope.Core.Pricing;
using Xunit;

namespace TokenScope.Core.Tests.Pricing;

public class PricingLoaderTests
{
    private static readonly DateTimeOffset FixedNow = new(2026, 5, 13, 0, 0, 0, TimeSpan.Zero);

    private static TimeProvider FixedClock(DateTimeOffset at) => new FakeClock(at);

    [Fact]
    public void LoadFromJson_ValidConfig_ProducesPopulatedTable()
    {
        const string json = """
            {
              "schema_version": 1,
              "models": [
                {
                  "id": "claude-opus-4-7",
                  "rates": [
                    {
                      "effective_date": "2026-01-01T00:00:00Z",
                      "input_per_mtok": 5.00,
                      "output_per_mtok": 25.00,
                      "cache_read_per_mtok": 0.50,
                      "cache_write_5m_per_mtok": 6.25,
                      "cache_write_1h_per_mtok": 10.00
                    }
                  ]
                }
              ]
            }
            """;

        var table = PricingLoader.LoadFromJson(json, FixedClock(FixedNow));

        table.KnownModelIds.Should().Contain("claude-opus-4-7");
        var result = table.Lookup("claude-opus-4-7", FixedNow);
        result.Should().BeOfType<PricingLookupResult.Found>()
            .Which.Rate.InputPerMTok.Should().Be(5.00m);
    }

    [Fact]
    public void LoadFromFile_RealRepositoryConfig_LoadsAllTenModels()
    {
        // Use the real config/pricing.json shipped with the repo as an integration smoke.
        var repoRoot = FindRepoRoot();
        var pricingPath = Path.Combine(repoRoot, "config", "pricing.json");

        var table = PricingLoader.LoadFromFile(pricingPath, FixedClock(FixedNow));

        table.KnownModelIds.Should().HaveCount(10);
        table.KnownModelIds.Should().Contain(new[]
        {
            "claude-opus-4-7", "claude-opus-4-6", "claude-opus-4-5", "claude-opus-4-1", "claude-opus-4",
            "claude-sonnet-4-6", "claude-sonnet-4-5", "claude-sonnet-4",
            "claude-haiku-4-5", "claude-haiku-3-5",
        });
    }

    [Fact]
    public void LoadFromFile_UsingFixturePath_Works()
    {
        var fixturePath = FixturePath("pricing-valid.json");

        var table = PricingLoader.LoadFromFile(fixturePath, FixedClock(FixedNow));

        table.KnownModelIds.Should().ContainSingle().Which.Should().Be("claude-opus-4-7");
    }

    [Fact]
    public void LoadFromJson_WrongSchemaVersion_Throws()
    {
        const string json = """{ "schema_version": 999, "models": [{"id":"m","rates":[{"effective_date":"2026-01-01T00:00:00Z","input_per_mtok":1,"output_per_mtok":1,"cache_read_per_mtok":1,"cache_write_5m_per_mtok":1,"cache_write_1h_per_mtok":1}]}] }""";

        var act = () => PricingLoader.LoadFromJson(json, FixedClock(FixedNow));

        act.Should().Throw<PricingValidationException>()
            .Which.Errors.Should().Contain(e => e.Contains("schema_version 999"));
    }

    [Fact]
    public void LoadFromJson_EmptyModels_Throws()
    {
        const string json = """{ "schema_version": 1, "models": [] }""";

        var act = () => PricingLoader.LoadFromJson(json, FixedClock(FixedNow));

        act.Should().Throw<PricingValidationException>()
            .Which.Errors.Should().Contain(e => e.Contains("models array"));
    }

    [Fact]
    public void LoadFromJson_NegativeRate_Throws()
    {
        const string json = """
            {
              "schema_version": 1,
              "models": [{
                "id": "m",
                "rates": [{
                  "effective_date": "2026-01-01T00:00:00Z",
                  "input_per_mtok": -1,
                  "output_per_mtok": 1,
                  "cache_read_per_mtok": 1,
                  "cache_write_5m_per_mtok": 1,
                  "cache_write_1h_per_mtok": 1
                }]
              }]
            }
            """;

        var act = () => PricingLoader.LoadFromJson(json, FixedClock(FixedNow));

        act.Should().Throw<PricingValidationException>()
            .Which.Errors.Should().Contain(e => e.Contains("input_per_mtok is negative"));
    }

    [Fact]
    public void LoadFromJson_FutureEffectiveDate_Throws()
    {
        const string json = """
            {
              "schema_version": 1,
              "models": [{
                "id": "m",
                "rates": [{
                  "effective_date": "2099-01-01T00:00:00Z",
                  "input_per_mtok": 1,
                  "output_per_mtok": 1,
                  "cache_read_per_mtok": 1,
                  "cache_write_5m_per_mtok": 1,
                  "cache_write_1h_per_mtok": 1
                }]
              }]
            }
            """;

        var act = () => PricingLoader.LoadFromJson(json, FixedClock(FixedNow));

        act.Should().Throw<PricingValidationException>()
            .Which.Errors.Should().Contain(e => e.Contains("is in the future"));
    }

    [Fact]
    public void LoadFromJson_DuplicateModelId_Throws()
    {
        const string json = """
            {
              "schema_version": 1,
              "models": [
                {"id": "m", "rates": [{"effective_date":"2026-01-01T00:00:00Z","input_per_mtok":1,"output_per_mtok":1,"cache_read_per_mtok":1,"cache_write_5m_per_mtok":1,"cache_write_1h_per_mtok":1}]},
                {"id": "m", "rates": [{"effective_date":"2026-01-01T00:00:00Z","input_per_mtok":1,"output_per_mtok":1,"cache_read_per_mtok":1,"cache_write_5m_per_mtok":1,"cache_write_1h_per_mtok":1}]}
              ]
            }
            """;

        var act = () => PricingLoader.LoadFromJson(json, FixedClock(FixedNow));

        act.Should().Throw<PricingValidationException>()
            .Which.Errors.Should().Contain(e => e.Contains("duplicated"));
    }

    [Fact]
    public void LoadFromJson_DuplicateEffectiveDate_Throws()
    {
        const string json = """
            {
              "schema_version": 1,
              "models": [{
                "id": "m",
                "rates": [
                  {"effective_date":"2026-01-01T00:00:00Z","input_per_mtok":1,"output_per_mtok":1,"cache_read_per_mtok":1,"cache_write_5m_per_mtok":1,"cache_write_1h_per_mtok":1},
                  {"effective_date":"2026-01-01T00:00:00Z","input_per_mtok":2,"output_per_mtok":2,"cache_read_per_mtok":2,"cache_write_5m_per_mtok":2,"cache_write_1h_per_mtok":2}
                ]
              }]
            }
            """;

        var act = () => PricingLoader.LoadFromJson(json, FixedClock(FixedNow));

        act.Should().Throw<PricingValidationException>()
            .Which.Errors.Should().Contain(e => e.Contains("duplicates another entry"));
    }

    [Fact]
    public void LoadFromJson_MissingFields_Throws()
    {
        const string json = """
            {
              "schema_version": 1,
              "models": [{
                "id": "m",
                "rates": [{
                  "effective_date": "2026-01-01T00:00:00Z",
                  "input_per_mtok": 1
                }]
              }]
            }
            """;

        var act = () => PricingLoader.LoadFromJson(json, FixedClock(FixedNow));

        act.Should().Throw<PricingValidationException>()
            .Which.Errors.Should().Contain(e => e.Contains("output_per_mtok is missing"));
    }

    [Fact]
    public void LoadFromJson_MalformedJson_Throws()
    {
        var act = () => PricingLoader.LoadFromJson("{ not json", FixedClock(FixedNow));

        act.Should().Throw<PricingValidationException>()
            .Which.Errors.Should().Contain(e => e.Contains("not valid JSON"));
    }

    [Fact]
    public void LoadFromJson_EmptyRatesArray_Throws()
    {
        const string json = """{"schema_version":1,"models":[{"id":"m","rates":[]}]}""";

        var act = () => PricingLoader.LoadFromJson(json, FixedClock(FixedNow));

        act.Should().Throw<PricingValidationException>()
            .Which.Errors.Should().Contain(e => e.Contains("rates array"));
    }

    [Fact]
    public void LoadFromJson_MissingModelId_Throws()
    {
        const string json = """{"schema_version":1,"models":[{"rates":[{"effective_date":"2026-01-01T00:00:00Z","input_per_mtok":1,"output_per_mtok":1,"cache_read_per_mtok":1,"cache_write_5m_per_mtok":1,"cache_write_1h_per_mtok":1}]}]}""";

        var act = () => PricingLoader.LoadFromJson(json, FixedClock(FixedNow));

        act.Should().Throw<PricingValidationException>()
            .Which.Errors.Should().Contain(e => e.Contains("id is missing"));
    }

    [Fact]
    public void LoadFromJson_MissingEffectiveDate_Throws()
    {
        const string json = """{"schema_version":1,"models":[{"id":"m","rates":[{"input_per_mtok":1,"output_per_mtok":1,"cache_read_per_mtok":1,"cache_write_5m_per_mtok":1,"cache_write_1h_per_mtok":1}]}]}""";

        var act = () => PricingLoader.LoadFromJson(json, FixedClock(FixedNow));

        act.Should().Throw<PricingValidationException>()
            .Which.Errors.Should().Contain(e => e.Contains("effective_date is missing"));
    }

    [Fact]
    public void PricingValidationException_WithEmptyErrors_HasFallbackMessage()
    {
        var ex = new PricingValidationException(System.Collections.Immutable.ImmutableArray<string>.Empty);

        ex.Message.Should().Be("Pricing configuration is invalid.");
        ex.Errors.Should().BeEmpty();
    }

    // -- helpers --

    private static string FixturePath(string name)
    {
        var assemblyDir = Path.GetDirectoryName(typeof(PricingLoaderTests).Assembly.Location)!;
        return Path.Combine(assemblyDir, "Fixtures", name);
    }

    private static string FindRepoRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, "TokenScope.sln")))
        {
            dir = dir.Parent;
        }
        return dir?.FullName
            ?? throw new InvalidOperationException("Could not locate repo root (TokenScope.sln)");
    }

    private sealed class FakeClock : TimeProvider
    {
        private readonly DateTimeOffset _now;
        public FakeClock(DateTimeOffset now) => _now = now;
        public override DateTimeOffset GetUtcNow() => _now;
    }
}
