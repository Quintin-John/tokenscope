using AwesomeAssertions;
using OpenTelemetry;
using OpenTelemetry.Metrics;
using TokenScope.Core.Domain;
using TokenScope.Otel.Configuration;
using TokenScope.Otel.Metrics;
using TokenScope.Otel.Tracking;
using Xunit;

namespace TokenScope.Otel.Tests.Configuration;

public class TokenScopeMeterProviderBuilderExtensionsTests
{
    [Fact]
    public void AddTokenScopeMeter_Builds_Without_Exporter()
    {
        using var provider = Sdk.CreateMeterProviderBuilder()
            .AddTokenScopeMeter()
            .Build();

        provider.Should().NotBeNull();
    }

    [Fact]
    public void AddTokenScopeMetricsWithOtlp_DefaultEndpoint_Builds()
    {
        // Default endpoint is http://localhost:4317 — exporter creation should not
        // require the endpoint to be reachable.
        using var provider = Sdk.CreateMeterProviderBuilder()
            .AddTokenScopeMetricsWithOtlp()
            .Build();

        provider.Should().NotBeNull();
    }

    [Fact]
    public void AddTokenScopeMetricsWithOtlp_CustomOptions_Builds()
    {
        var opts = new TokenScopeOtelOptions
        {
            OtlpEndpoint = "http://collector.example.test:4318",
            Protocol = OpenTelemetry.Exporter.OtlpExportProtocol.HttpProtobuf,
            ActiveSessionWindow = TimeSpan.FromMinutes(5),
        };

        using var provider = Sdk.CreateMeterProviderBuilder()
            .AddTokenScopeMetricsWithOtlp(opts)
            .Build();

        provider.Should().NotBeNull();
    }

    [Fact]
    public void OtelSdk_Registered_Meter_CollectsMeasurements()
    {
        // End-to-end through the OTEL SDK using an inline exporter: emit a
        // measurement, force-flush, observe it.
        var captured = new List<long>();
        using var provider = Sdk.CreateMeterProviderBuilder()
            .AddTokenScopeMeter()
            .AddReader(new PeriodicExportingMetricReader(
                exporter: new CallbackExporter(metrics =>
                {
                    foreach (var m in metrics)
                    {
                        if (m.Name != "tokenscope.requests.total") continue;
                        foreach (ref readonly var point in m.GetMetricPoints())
                        {
                            captured.Add(point.GetSumLong());
                        }
                    }
                }),
                exportIntervalMilliseconds: int.MaxValue))
            .Build();

        using var metrics = new TokenScopeMetrics(new SessionActivityTracker(), new CacheRatioTracker(), TimeProvider.System);
        metrics.RecordRequest("m", "s", TokenUsage.Empty, Cost.Zero, DateTimeOffset.UtcNow);
        metrics.RecordRequest("m", "s", TokenUsage.Empty, Cost.Zero, DateTimeOffset.UtcNow);
        provider.ForceFlush();

        captured.Should().NotBeEmpty();
        captured.Sum().Should().Be(2L);
    }

    [Fact]
    public void Extensions_NullBuilder_Throws()
    {
        var addMeter = () => TokenScopeMeterProviderBuilderExtensions.AddTokenScopeMeter(null!);
        var addOtlp = () => TokenScopeMeterProviderBuilderExtensions.AddTokenScopeMetricsWithOtlp(null!);

        addMeter.Should().Throw<ArgumentNullException>();
        addOtlp.Should().Throw<ArgumentNullException>();
    }

    private sealed class CallbackExporter(Action<Batch<Metric>> onExport) : BaseExporter<Metric>
    {
        public override ExportResult Export(in Batch<Metric> batch)
        {
            onExport(batch);
            return ExportResult.Success;
        }
    }
}
