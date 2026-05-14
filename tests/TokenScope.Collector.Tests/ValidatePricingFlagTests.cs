using AwesomeAssertions;
using Xunit;

namespace TokenScope.Collector.Tests;

/// <summary>
/// Tests for the Phase 8 <c>--validate-pricing &lt;path&gt;</c> flag on the
/// collector binary. CI pipelines rely on the exit-code contract:
///   0 success, 4 validation failure, 2 file-not-found / usage error.
/// </summary>
public class ValidatePricingFlagTests : IDisposable
{
    private readonly string _tempDir;
    private readonly StringWriter _stdout = new();
    private readonly StringWriter _stderr = new();
    private readonly TextWriter _origOut = Console.Out;
    private readonly TextWriter _origErr = Console.Error;

    public ValidatePricingFlagTests()
    {
        _tempDir = Path.Combine(Path.GetTempPath(), "tokenscope-validate-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(_tempDir);
        Console.SetOut(_stdout);
        Console.SetError(_stderr);
    }

    public void Dispose()
    {
        Console.SetOut(_origOut);
        Console.SetError(_origErr);
        _stdout.Dispose();
        _stderr.Dispose();
        try { Directory.Delete(_tempDir, recursive: true); } catch { }
    }

    [Fact]
    public void ValidatePricing_HappyPath_Exits0()
    {
        var pricingPath = Path.Combine(_tempDir, "pricing.json");
        File.WriteAllText(pricingPath, ValidPricingJson());

        var exit = Program.RunValidatePricing(pricingPath);

        exit.Should().Be(0);
        _stdout.ToString().Should().Contain("OK").And.Contain("1 models");
    }

    [Fact]
    public void ValidatePricing_NegativeRate_Exits4_ErrorsOnStderr()
    {
        var pricingPath = Path.Combine(_tempDir, "pricing.json");
        File.WriteAllText(pricingPath, ValidPricingJson().Replace("\"input_per_mtok\": 5.00", "\"input_per_mtok\": -1"));

        var exit = Program.RunValidatePricing(pricingPath);

        exit.Should().Be(4);
        _stderr.ToString().Should().Contain("FAIL").And.Contain("input_per_mtok is negative");
        _stdout.ToString().Should().BeEmpty("errors go to stderr; stdout stays clean for downstream parsing");
    }

    [Fact]
    public void ValidatePricing_MissingFile_Exits2()
    {
        var nonexistent = Path.Combine(_tempDir, "no-such-file.json");

        var exit = Program.RunValidatePricing(nonexistent);

        exit.Should().Be(2);
        _stderr.ToString().Should().Contain("file not found");
    }

    [Fact]
    public void TryGetValidatePricingPath_FlagPresent_ReturnsPath()
    {
        var args = new[] { "--validate-pricing", "/some/path" };

        var found = Program.TryGetValidatePricingPath(args, out var path);

        found.Should().BeTrue();
        path.Should().Be("/some/path");
    }

    [Fact]
    public void TryGetValidatePricingPath_FlagAbsent_ReturnsFalse()
    {
        var args = new[] { "--config", "tokenscope.yaml" };

        var found = Program.TryGetValidatePricingPath(args, out var path);

        found.Should().BeFalse();
        path.Should().BeEmpty();
    }

    private static string ValidPricingJson() => """
        {
          "schema_version": 1,
          "currency": "USD",
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
}
