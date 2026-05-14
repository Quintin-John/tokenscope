using System.Collections.Immutable;
using AwesomeAssertions;
using TokenScope.Core.Costing;
using TokenScope.Core.Domain;
using TokenScope.Core.Pricing;
using Xunit;

namespace TokenScope.Core.Tests.Costing;

public class CostCalculatorTests
{
    private static readonly ModelRate Opus47 = new(
        InputPerMTok: 5.00m,
        OutputPerMTok: 25.00m,
        CacheReadPerMTok: 0.50m,
        CacheWrite5mPerMTok: 6.25m,
        CacheWrite1hPerMTok: 10.00m);

    // -- Compute: each component verified independently against hand math --

    [Fact]
    public void Compute_InputOnly_MatchesHandComputed()
    {
        // 1,000,000 input tokens at $5/MTok = $5.00
        var usage = new TokenUsage(1_000_000, 0, 0, 0, 0);

        var cost = CostCalculator.Compute(usage, Opus47);

        cost.Input.Should().Be(5.00m);
        cost.Output.Should().Be(0m);
        cost.CacheRead.Should().Be(0m);
        cost.CacheWrite5m.Should().Be(0m);
        cost.CacheWrite1h.Should().Be(0m);
        cost.Total.Should().Be(5.00m);
    }

    [Fact]
    public void Compute_OutputOnly_MatchesHandComputed()
    {
        // 250,000 output tokens at $25/MTok = $6.25
        var usage = new TokenUsage(0, 250_000, 0, 0, 0);

        var cost = CostCalculator.Compute(usage, Opus47);

        cost.Output.Should().Be(6.25m);
        cost.Total.Should().Be(6.25m);
    }

    [Fact]
    public void Compute_CacheReadOnly_MatchesHandComputed()
    {
        // 2,000,000 cache-read tokens at $0.50/MTok = $1.00
        var usage = new TokenUsage(0, 0, 2_000_000, 0, 0);

        var cost = CostCalculator.Compute(usage, Opus47);

        cost.CacheRead.Should().Be(1.00m);
        cost.Total.Should().Be(1.00m);
    }

    [Fact]
    public void Compute_CacheWrite5m_MatchesHandComputed()
    {
        // 400,000 cache-write-5m tokens at $6.25/MTok = $2.50
        var usage = new TokenUsage(0, 0, 0, 400_000, 0);

        var cost = CostCalculator.Compute(usage, Opus47);

        cost.CacheWrite5m.Should().Be(2.50m);
        cost.Total.Should().Be(2.50m);
    }

    [Fact]
    public void Compute_CacheWrite1h_MatchesHandComputed()
    {
        // 300,000 cache-write-1h tokens at $10.00/MTok = $3.00
        var usage = new TokenUsage(0, 0, 0, 0, 300_000);

        var cost = CostCalculator.Compute(usage, Opus47);

        cost.CacheWrite1h.Should().Be(3.00m);
        cost.Total.Should().Be(3.00m);
    }

    [Fact]
    public void Compute_MixedUsage_SumsAllComponents()
    {
        // 100k input ($0.50) + 50k output ($1.25) + 200k cache read ($0.10)
        // + 80k cache write 5m ($0.50) + 60k cache write 1h ($0.60) = $2.95
        var usage = new TokenUsage(
            Input: 100_000,
            Output: 50_000,
            CacheRead: 200_000,
            CacheWrite5m: 80_000,
            CacheWrite1h: 60_000);

        var cost = CostCalculator.Compute(usage, Opus47);

        cost.Input.Should().Be(0.50m);
        cost.Output.Should().Be(1.25m);
        cost.CacheRead.Should().Be(0.10m);
        cost.CacheWrite5m.Should().Be(0.50m);
        cost.CacheWrite1h.Should().Be(0.60m);
        cost.Total.Should().Be(2.95m);
    }

    // -- Edge cases --

    [Fact]
    public void Compute_ZeroTokens_ReturnsZeroCost()
    {
        var cost = CostCalculator.Compute(TokenUsage.Empty, Opus47);

        cost.Should().Be(Cost.Zero);
        cost.Total.Should().Be(0m);
    }

    [Fact]
    public void Compute_VeryLargeTokenCount_DoesNotOverflow()
    {
        // 10^15 (one quadrillion) input tokens at $5/MTok = $5,000,000,000
        // Verifies no integer overflow in the conversion path.
        var usage = new TokenUsage(1_000_000_000_000_000L, 0, 0, 0, 0);

        var cost = CostCalculator.Compute(usage, Opus47);

        cost.Input.Should().Be(5_000_000_000m);
        cost.Total.Should().Be(5_000_000_000m);
    }

    [Fact]
    public void Compute_LongMaxValue_StillProducesFiniteDecimal()
    {
        // Pathological: long.MaxValue tokens. Decimal range is ±7.9e28; result
        // (~6.9e14 at $75/MTok output rate) fits comfortably with full precision.
        var ratesOpus41 = new ModelRate(15m, 75m, 1.5m, 18.75m, 30m);
        var usage = new TokenUsage(0, long.MaxValue, 0, 0, 0);

        var cost = CostCalculator.Compute(usage, ratesOpus41);

        cost.Output.Should().BeGreaterThan(0m);
    }

    [Fact]
    public void Compute_NegativeRate_NotPossibleViaLoader_StillComputesArithmetically()
    {
        // Records do not validate; the loader rejects negative rates. This documents
        // that the calculator itself is a pure arithmetic function — if a negative
        // rate ever reached it, you'd get a negative cost. The loader is the guard.
        var weirdRate = new ModelRate(-1m, 0m, 0m, 0m, 0m);
        var usage = new TokenUsage(1_000_000, 0, 0, 0, 0);

        var cost = CostCalculator.Compute(usage, weirdRate);

        cost.Input.Should().Be(-1m);
    }

    // -- Calculate (with pricing lookup) --

    [Fact]
    public void Calculate_KnownModelAndEffectiveRate_ReturnsSuccess()
    {
        var table = TableWith(("claude-opus-4-7", new DateTimeOffset(2026, 1, 1, 0, 0, 0, TimeSpan.Zero), Opus47));
        var request = new Request(
            Id: "req-1",
            ModelId: "claude-opus-4-7",
            Timestamp: new DateTimeOffset(2026, 5, 13, 12, 0, 0, TimeSpan.Zero),
            Usage: new TokenUsage(1_000_000, 0, 0, 0, 0));

        var result = CostCalculator.Calculate(request, table);

        result.Should().BeOfType<CostResult.Success>();
        ((CostResult.Success)result).Cost.Input.Should().Be(5.00m);
    }

    [Fact]
    public void Calculate_UnknownModel_ReturnsModelNotFound()
    {
        var table = TableWith(("claude-opus-4-7", new DateTimeOffset(2026, 1, 1, 0, 0, 0, TimeSpan.Zero), Opus47));
        var request = new Request(
            Id: "req-1",
            ModelId: "gpt-7",
            Timestamp: new DateTimeOffset(2026, 5, 13, 12, 0, 0, TimeSpan.Zero),
            Usage: TokenUsage.Empty);

        var result = CostCalculator.Calculate(request, table);

        result.Should().BeOfType<CostResult.ModelNotFound>();
        ((CostResult.ModelNotFound)result).ModelId.Should().Be("gpt-7");
    }

    [Fact]
    public void Calculate_KnownModelButRequestBeforeAnyEffectiveDate_ReturnsNoRateEffective()
    {
        var effectiveDate = new DateTimeOffset(2026, 1, 1, 0, 0, 0, TimeSpan.Zero);
        var table = TableWith(("claude-opus-4-7", effectiveDate, Opus47));
        var request = new Request(
            Id: "req-1",
            ModelId: "claude-opus-4-7",
            Timestamp: new DateTimeOffset(2025, 12, 31, 23, 59, 59, TimeSpan.Zero),
            Usage: TokenUsage.Empty);

        var result = CostCalculator.Calculate(request, table);

        result.Should().BeOfType<CostResult.NoRateEffective>();
    }

    [Fact]
    public void Calculate_NullArguments_Throws()
    {
        var table = TableWith(("claude-opus-4-7", DateTimeOffset.UnixEpoch, Opus47));
        var request = new Request("r", "claude-opus-4-7", DateTimeOffset.UtcNow, TokenUsage.Empty);

        var calculateNullRequest = () => CostCalculator.Calculate(null!, table);
        var calculateNullTable = () => CostCalculator.Calculate(request, null!);
        var computeNullUsage = () => CostCalculator.Compute(null!, Opus47);
        var computeNullRate = () => CostCalculator.Compute(TokenUsage.Empty, null!);

        calculateNullRequest.Should().Throw<ArgumentNullException>();
        calculateNullTable.Should().Throw<ArgumentNullException>();
        computeNullUsage.Should().Throw<ArgumentNullException>();
        computeNullRate.Should().Throw<ArgumentNullException>();
    }

    // -- Test helper --

    private static PricingTable TableWith(params (string ModelId, DateTimeOffset At, ModelRate Rate)[] entries)
    {
        var builder = ImmutableDictionary.CreateBuilder<string, ImmutableArray<PricingEntry>>();
        foreach (var group in entries.GroupBy(e => e.ModelId))
        {
            builder[group.Key] = group
                .Select(e => new PricingEntry(e.At, e.Rate))
                .OrderBy(p => p.EffectiveDate)
                .ToImmutableArray();
        }
        return new PricingTable(builder.ToImmutable());
    }
}
